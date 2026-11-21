"""A popup reads the window frame the core declares, not the one of the host.

What it fixes: the geometry of a window a page opens with `window.open(url, 'p', 'width=500,height=400')`, read from
inside the popup. A popup is its real inner size plus the declared frame (retail Windows Firefox, measured by the core at
scale 1.0: a border of 8 on the left, right and bottom, the popup UI of 72 above the content, and a default position of
4,4 when the page names no left/top):

  * `outerWidth  == innerWidth + 2 * border`
  * `outerHeight == innerHeight + popup_ui + border`
  * `screenX == screenY ==` the declared default position (the page named none)
  * `mozInnerScreenX == screenX + border`, `mozInnerScreenY == screenY + popup_ui`

Engine hook: `fingerprint/screen-window/geometry` of the engine series (`nsGlobalWindowOuter::GetOuterSize`,
`GetScreenXY`, `GetMozInnerScreenX/Y`, with the content origin shift of `StealthScreenCoords`) and
`fingerprint/screen-window/popup-position` (`nsWindowWatcher`, the default position of a popup).

Negative control, in the same test: the same page in a launch with the engine OFF (`zoom.stealth.fpp.hw_seed` 0, a bare
binary) reads the host's popup, which has no declared frame (a headless popup has none: outer == inner), so the sizes
that the declaration adds are not there. The test asserts the two readings DIFFER, so a hook that stops answering from
the declaration turns it red.

Why a page reads it: `outerWidth - innerWidth`, `screenX` and `mozInnerScreenX/Y` are one line each, and a popup whose
frame is not a Windows frame is a browser that does not run on Windows. Linux and Windows run the same assertions: the
declaration, not the host, is what is read.
"""
from __future__ import annotations

import pytest
from _popup_geometry import ENGINE_OFF, frame_of_the_declaration, read_popup

pytestmark = pytest.mark.e2e


def test_a_popup_is_its_inner_size_plus_the_declared_frame(firefox_binary):
    frame = frame_of_the_declaration()
    declared = (2 * frame.border, frame.popup_ui + frame.border)

    def has_the_frame(reading):
        p = reading["popup"]
        return (p["ow"] - p["iw"], p["oh"] - p["ih"]) == declared

    on = read_popup(firefox_binary, ready=has_the_frame)["popup"]
    assert (on["ow"] - on["iw"], on["oh"] - on["ih"]) == declared, (
        "the popup reads outer %dx%d for inner %dx%d: the frame is %r, the declaration is %r"
        % (on["ow"], on["oh"], on["iw"], on["ih"], (on["ow"] - on["iw"], on["oh"] - on["ih"]), declared))
    assert (on["sx"], on["sy"]) == (frame.popup_position, frame.popup_position), (
        "a popup that names no position reads screen %r, the declared default is %d" % ((on["sx"], on["sy"]),
                                                                                      frame.popup_position))
    assert on["mx"] == pytest.approx(on["sx"] + frame.border, abs=0.01), (
        "mozInnerScreenX %r is not screenX %r plus the border %d" % (on["mx"], on["sx"], frame.border))
    assert on["my"] == pytest.approx(on["sy"] + frame.popup_ui, abs=0.01), (
        "mozInnerScreenY %r is not screenY %r plus the popup UI %d" % (on["my"], on["sy"], frame.popup_ui))

    # The control: the engine off is the host's popup, and none of the declared relations holds.
    off = read_popup(firefox_binary, extra_prefs=ENGINE_OFF)["popup"]
    assert (off["ow"] - off["iw"], off["oh"] - off["ih"]) != declared, (
        "with the engine off the popup also reads the declared frame %r: the control reads the declaration, so the "
        "test cannot tell a hook that answers from one that does not" % (declared,))
    assert abs(off["my"] - off["sy"] - frame.popup_ui) > 0.01, (
        "with the engine off mozInnerScreenY is also screenY + %d: the control reads the declaration" % frame.popup_ui)
