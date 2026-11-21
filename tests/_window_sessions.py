"""What the window e2e tests share: a headed session of the product, and a wait on a condition of a page.

The sessions are HEADED on purpose. ``headless=True`` gives every session a display of its own (a fresh Xvfb on Linux, a fresh Win32 desktop on
Windows), so two of them never see each other; the focus and occlusion tests need two windows on ONE display, which is what two headed sessions of the
same run have (the Linux e2e runs under ``xvfb-run``). Both sessions use the same seed, so both windows are the same declared window and the second one
covers the first completely.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from _waits import Watch, watch

#: The seed only has to give two sessions the same window.
SEED = 21

#: The deadline of a wait for something that must happen, in seconds.
DEADLINE_S = 30


def launch(firefox_binary: str, *, extra_prefs: Optional[Dict[str, Any]] = None, pin: Optional[Dict[str, Any]] = None, headless: bool = False):
    """A session of the product (RFP off, the engine declared as the core launches it); ``extra_prefs`` and ``pin`` only where a test needs them."""
    from invisible_playwright import InvisiblePlaywright

    return InvisiblePlaywright(seed=SEED, binary_path=firefox_binary, headless=headless, locale="en-US", timezone="America/New_York",
                               extra_prefs=extra_prefs, pin=pin)


def wait_for_condition(page, sample: Callable[[], Any], done: Callable[[Any], Any], what: str, seconds: float = DEADLINE_S) -> Watch:
    """Until ``done(sample())``, reading the page between two looks. Red after ``seconds`` with what was seen on the way."""
    result = watch(sample, done, seconds, lambda: page.wait_for_timeout(100))
    assert result.ok, "%s; seen: %s" % (what, result.render())
    return result
