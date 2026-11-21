"""A page keeps the focus when another session takes the display, and loses it only to its own session.

What it fixes: with ``zoom.stealth.focus.ignore_os_deactivation`` (the core declares it true, one value for every session) the window does not give up its
activation when the SYSTEM takes it: another application, or another session's window on the same display. A page reads ``document.hasFocus()`` and the
``blur`` event; without the pref a second visible session made the first one read ``hasFocus()`` false (4 times in 4 on one display) while nothing in its own
session had moved. When the session opens a window of its own the window it replaces is lowered with the blur any Firefox gives.

The hooks: ``AppWindow::WindowDeactivated`` (the early return) and ``AppWindow::WindowActivated`` (``StealthLowerActiveWindow``, which lowers the active window
with its own action id before Mozilla's raise) in ``xpfe/appshell/AppWindow.cpp`` of the series, folder ``fingerprint/screen-window/focus``.

The steps: session A opens a page that records ``focus`` and ``blur``; session B, headed on the same display, opens a window and takes the focus. A's page
still reads ``hasFocus()`` true and has recorded no ``blur``. Then A opens a second page of its own: the first page records its ``blur`` and reads
``hasFocus()`` false, the new one reads it true.

The control, in the same test: ``extra_prefs={"zoom.stealth.focus.ignore_os_deactivation": False}``, the same steps up to B taking the display, and A's page
reads ``hasFocus()`` false. It runs first, so a runner where the second session does not take the focus is red there and not a green that means nothing.
Both sessions are headed on one display (the Linux e2e runs under ``xvfb-run``; Windows has the interactive desktop of the runner).
"""
from __future__ import annotations

import pytest

from _window_sessions import launch, wait_for_condition

pytestmark = pytest.mark.e2e

PREF = "zoom.stealth.focus.ignore_os_deactivation"

RECORD = """() => {
  window.__events = [];
  for (const type of ['focus', 'blur']) window.addEventListener(type, () => window.__events.push(type));
  return document.hasFocus();
}"""
HAS_FOCUS = "() => document.hasFocus()"
EVENTS = "() => window.__events"
TWO_FRAMES = "() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)))"


def another_session_takes_the_display(firefox_binary, keeps, with_own_page=False):
    """What A's page reads once B's window has the focus, and (``with_own_page``) once A has opened a page of its own."""
    extra = None if keeps else {PREF: False}
    with launch(firefox_binary, extra_prefs=extra) as a:
        first = a.new_page()
        first.goto("about:blank")
        first.evaluate(RECORD)
        wait_for_condition(first, lambda: first.evaluate(HAS_FOCUS), lambda focused: focused is True, "the page never had the focus of its own window")
        with launch(firefox_binary) as b:
            taker = b.new_page()
            taker.goto("about:blank")
            wait_for_condition(taker, lambda: taker.evaluate(HAS_FOCUS), lambda focused: focused is True, "the second session's page never took the focus")
            if keeps:
                first.evaluate(TWO_FRAMES)
            else:
                wait_for_condition(first, lambda: first.evaluate(HAS_FOCUS), lambda focused: focused is False,
                                   "the first page still has the focus although another session's window took it")
            seen = {"has_focus": first.evaluate(HAS_FOCUS), "events": first.evaluate(EVENTS)}
            if with_own_page:
                own = a.new_page()
                own.goto("about:blank")
                wait_for_condition(own, lambda: (own.evaluate(HAS_FOCUS), first.evaluate(EVENTS), first.evaluate(HAS_FOCUS)),
                                   lambda state: state[0] is True and state[1].count("blur") == 1 and state[2] is False,
                                   "a page of the session's own did not take the focus from the first with one blur")
                seen["after_own_page"] = (own.evaluate(HAS_FOCUS), first.evaluate(EVENTS), first.evaluate(HAS_FOCUS))
        return seen


def test_a_page_keeps_focus_when_another_session_takes_the_display(firefox_binary):
    control = another_session_takes_the_display(firefox_binary, keeps=False)
    assert control["has_focus"] is False, "control: without the pref the first page still has the focus: %r" % control

    kept = another_session_takes_the_display(firefox_binary, keeps=True, with_own_page=True)
    assert kept["has_focus"] is True, "with the pref the first page lost the focus to another session: %r" % kept
    assert "blur" not in kept["events"], "with the pref the first page recorded a blur when another session took the display: %r" % kept
    assert kept["has_focus"] != control["has_focus"]
    own_focused, events, first_focused = kept["after_own_page"]
    assert own_focused is True and events.count("blur") == 1 and first_focused is False, "a page of the session's own did not replace the first one with one blur: %r" % kept
