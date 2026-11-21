"""A window that another one covers stays visible, unless occlusion tracking is enabled.

What it fixes: with ``widget.windows.window_occlusion_tracking.enabled`` and ``zoom.stealth.widget.occlusion_tracking.enabled`` false, as the core declares
them from one value, a page whose window is fully covered by a second window keeps ``document.visibilityState`` ``visible`` and gets no ``visibilitychange``.
Retail Windows with the tracker on says ``hidden`` there (and then throttles ``requestAnimationFrame`` to 1 Hz and the timers to 1000 ms); a session nobody
looks at must not read as backgrounded.

The hook: ``nsWindow::OnVisibilityNotifyEvent`` in ``widget/gtk/nsWindow.cpp`` of the series, folder ``fingerprint/screen-window/occlusion``: the condition of the
ternary that decides ``OCCLUDED`` or ``UNKNOWN`` reads the pref (``StealthOcclusionTracked``). On Windows the same declaration is Mozilla's own pref, so the same
page reading is the proof that the two builds do not disagree.

The control, in the same test: the two prefs true. The same two sessions, the same covering, and the covered page turns ``hidden``. Without it the first half
proves nothing (a window that was never covered is visible too); it runs first, so a runner on which two windows do not cover each other is red here and not a
green that means nothing.

Two sessions are launched on one display and both are headed: the Linux e2e runs under ``xvfb-run`` (no window manager, so the second window is mapped over the
first), Windows uses the interactive desktop of the runner (the second maximized window is created on top of the first and the tracker, when on, calculates
the cover). A page reads it from ``document.visibilityState`` and the ``visibilitychange`` event.
"""
from __future__ import annotations

import pytest

from _window_sessions import launch, wait_for_condition

pytestmark = pytest.mark.e2e

#: The one decision, under both engine names (core: ``OCCLUSION_TRACKING``).
TRACKING_PREFS = ("widget.windows.window_occlusion_tracking.enabled", "zoom.stealth.widget.occlusion_tracking.enabled")

#: Records every change of visibility of the document, and answers the current state.
WATCH = """() => {
  window.__visibility = [document.visibilityState];
  document.addEventListener('visibilitychange', () => window.__visibility.push(document.visibilityState));
  return document.visibilityState;
}"""
STATE = "() => document.visibilityState"
CHANGES = "() => window.__visibility"
#: Two painted frames: the page's own process has handled what the window system told it before this returns.
TWO_FRAMES = "() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)))"


def second_window_over_the_first(firefox_binary, tracking):
    """(state, changes) of the first session's page after a second session's window is up over it.

    ``tracking`` is None for the product as it launches, or the value both tracking prefs are given."""
    extra = None if tracking is None else {name: tracking for name in TRACKING_PREFS}
    with launch(firefox_binary, extra_prefs=extra) as first, launch(firefox_binary) as second:
        covered = first.new_page()
        covered.goto("about:blank")
        assert covered.evaluate(WATCH) == "visible", "the page was not visible before anything covered it"
        cover = second.new_page()
        cover.goto("about:blank")
        cover.evaluate(TWO_FRAMES)
        if tracking:
            wait_for_condition(covered, lambda: covered.evaluate(STATE), lambda state: state == "hidden",
                               "a window that another one covers never turned hidden with occlusion tracking enabled")
        else:
            covered.evaluate(TWO_FRAMES)
        return covered.evaluate(STATE), covered.evaluate(CHANGES)


def test_a_covered_window_stays_visible_unless_occlusion_tracking_is_enabled(firefox_binary):
    tracked_state, tracked_changes = second_window_over_the_first(firefox_binary, True)
    assert tracked_state == "hidden" and tracked_changes == ["visible", "hidden"], (
        "control: with tracking enabled the covered page reads %r, changes %r" % (tracked_state, tracked_changes))

    state, changes = second_window_over_the_first(firefox_binary, None)
    assert state == "visible" and changes == ["visible"], (
        "the product's covered page reads %r, changes %r: it must stay visible, with no visibilitychange" % (state, changes))
    assert state != tracked_state, "enabled and disabled read the same: the declaration decides nothing"
