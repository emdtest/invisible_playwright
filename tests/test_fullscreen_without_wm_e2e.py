"""Fullscreen is decided from the declared screen, with no window manager to answer it.

What it fixes: on an X display with no window manager (Xvfb, the way the engine runs on Linux servers) GTK's fullscreen is a request nobody answers, so
Mozilla refuses it: the DOM goes fullscreen and the window does not (``display-mode: fullscreen`` false, ``innerHeight`` the work area). Retail Windows
decides it itself and gives the screen. With the engine active the widget decides it too, from the screen the core declared: after ``requestFullscreen()``
the page reads ``innerWidth x innerHeight`` equal to the declared screen and ``display-mode: fullscreen``.

The hook: ``nsWindow::MakeFullScreen`` in ``widget/gtk/nsWindow.cpp`` of the series, folder ``fingerprint/screen-window/occlusion`` (the marker names
``fingerprint/screen-window/geometry``, whose function ``StealthMakeFullScreenWithoutWM`` in ``widget/gtk/StealthFullscreenGtk.cpp`` it calls). On Windows
the same reading is held by ``nsIWidget::InfallibleMakeFullScreen`` and the Win32 rules of the same folder (``geometry``).

The screen is pinned to 1600x900, which no runner has (the Linux display is 1920x1080, the Windows runner's monitor 1024x768 or 1920x1080), so a window that
filled the host would read differently from the declared one.

The control, in the same test: the engine inactive (``zoom.stealth.fpp.hw_seed`` 0: the engine is on when it is above 0) keeps Mozilla's behaviour. On
Linux without a window manager that is the refusal (``display-mode: fullscreen`` false and the window not the declared screen); on Windows, which always has a
window manager and a widget that decides fullscreen, it is the host's monitor, not the declared screen. The same click, the same pinned screen; only the engine
differs.

The click is real input (``page.mouse``): ``requestFullscreen`` needs a user gesture. A page reads it from ``innerWidth``, ``innerHeight``,
``matchMedia('(display-mode: fullscreen)')`` and ``document.fullscreenElement``.
"""
from __future__ import annotations

import http.server
import sys
import threading

import pytest

from _servers import LocalServer
from _window_sessions import launch, wait_for_condition

pytestmark = pytest.mark.e2e

#: The device pixel ratio is pinned too: the core declares 1.5 for some personas (Windows runner), and a page reads the screen and the window in
#: CSS pixels, declared / dpr (1067x600), which is not what this test is about.
DECLARED = {"screen.width": 1600, "screen.height": 900, "screen.dpr": 1.0}

PAGE = (b"<!doctype html><html><head><meta charset=utf-8><title>fullscreen</title></head><body style=\"margin:0\">"
        b"<button id=go style=\"position:fixed;left:20px;top:20px;width:160px;height:40px;margin:0\">fullscreen</button>"
        b"<script>"
        b"window.__how = null; window.__error = null;"
        b"for (const t of ['fullscreenchange', 'fullscreenerror']) document.addEventListener(t, () => { window.__how = window.__how || t; });"
        b"document.getElementById('go').addEventListener('click', () => {"
        b"  document.documentElement.requestFullscreen().catch(e => { window.__error = String(e); });"
        b"});"
        b"</script></body></html>")

GEOMETRY = """() => ({
  innerWidth, innerHeight,
  screen: [screen.width, screen.height],
  displayModeFullscreen: matchMedia('(display-mode: fullscreen)').matches,
  fullscreenElement: document.fullscreenElement !== null,
})"""
HOW = "() => [window.__how, window.__error]"


@pytest.fixture
def page_url():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(PAGE)))
            self.end_headers()
            self.wfile.write(PAGE)

        def log_message(self, *args):
            pass

    with LocalServer(("127.0.0.1", 0), Handler) as server:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            yield "http://127.0.0.1:%d/" % server.server_address[1]
        finally:
            server.shutdown()


def fullscreen_geometry(firefox_binary, page_url, engine_on):
    """The page's geometry once the window has held still after a click on a button that asks for fullscreen."""
    extra = None if engine_on else {"zoom.stealth.fpp.hw_seed": 0}
    with launch(firefox_binary, extra_prefs=extra, pin=DECLARED, headless=True) as browser:
        page = browser.new_page()
        page.goto(page_url)
        page.mouse.move(90, 40)
        page.mouse.click(100, 40)
        wait_for_condition(page, lambda: page.evaluate(HOW), lambda how: how[0] is not None,
                           "the click on a button that asks for fullscreen was followed by neither fullscreenchange nor fullscreenerror")
        seen = []

        def held_still(geometry):
            seen.append(geometry)
            return len(seen) >= 4 and all(g == geometry for g in seen[-4:])

        # a transition resizes in steps: the geometry counts once it has been the same for four looks in a row
        wait_for_condition(page, lambda: page.evaluate(GEOMETRY), held_still, "the window never held still after the fullscreen request")
        return dict(seen[-1], how=page.evaluate(HOW))


def test_fullscreen_without_a_window_manager_is_the_declared_screen(firefox_binary, page_url):
    on = fullscreen_geometry(firefox_binary, page_url, engine_on=True)
    assert on["screen"] == [1600, 900], "the pinned screen is not what the page reads: %r" % on
    assert (on["innerWidth"], on["innerHeight"]) == (1600, 900), "with the engine on the fullscreen window is not the declared screen: %r" % on
    assert on["displayModeFullscreen"] is True and on["fullscreenElement"] is True, "with the engine on the page does not read fullscreen: %r" % on

    off = fullscreen_geometry(firefox_binary, page_url, engine_on=False)
    assert (off["innerWidth"], off["innerHeight"]) != (1600, 900), "control: the engine inactive also gives the declared screen: %r" % off
    if sys.platform != "win32":
        # no window manager: Mozilla's refusal, the DOM fullscreen without the window
        assert off["displayModeFullscreen"] is False, "control: without a window manager and without the engine the window went fullscreen: %r" % off
    assert (on["innerWidth"], on["innerHeight"], on["displayModeFullscreen"]) != (off["innerWidth"], off["innerHeight"], off["displayModeFullscreen"])
