"""A navigation that changes the process (a -> b, sites isolated: strategy 1) ends the way one that does not does.

With sites isolated a navigation to another site starts in the process of the page being left and commits in a new
one. The id of the navigation is the load identifier the parent assigns; the process that commits does not know it, and
the old process's own stop of the load (it is cut when the navigation changes process) is not a failure of the
navigation. What the client must still see, whatever the process:

  * a goto stopped by the page it is leaving raises NS_BINDING_ABORTED (the abort of the old process is its own);
  * a goto replaced by another navigation, to another site, is "interrupted by another navigation" and the second
    lands: the abort of the replaced one is told under its own id, not taken for the end of the new one;
  * a goto whose response is half there when the next one starts is replaced the same way (the swap has happened);
  * an iframe whose load is refused ends its load (the events a waiter needs) and its replacement error page is a
    navigation of its own; an iframe removed while it loads is aborted, then detached;
  * an iframe that goes to another site (its src changes) ends with the new address in the frame list: the process
    it leaves cuts its load (NS_BINDING_ABORTED, its docShell being destroyed), which is not the navigation's abort;
  * a frame in another process is committed with the name of its <iframe> (the embedder reads it).

Prefs: strategy 1 (`fission.webContentIsolationStrategy = 1`, `fission.autostart`) for this file only, as the bench
(`test_fission_strategy1_bench.py`); hosts a.test, b.test and c.test resolve to the local server through
`network.dns.localDomains`: two hosts are two sites.
"""
from __future__ import annotations

import asyncio
import threading
import time
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlsplit

import pytest

from _servers import LocalServer
from _waits import awatch

pytestmark = pytest.mark.e2e

#: The longest any wait of this file lasts; no healthy run comes near it. It is the only clock the verdicts have.
DEADLINE = 8.0

FISSION_ON = {
    "fission.autostart": True,
    "fission.webContentIsolationStrategy": 1,
    "dom.ipc.processCount.webIsolated": 4,
    "network.dns.localDomains": "a.test,b.test,c.test",
}


@pytest.fixture
def sites():
    arrived = set()

    class Handler(BaseHTTPRequestHandler):
        timeout = 10

        def log_message(self, *args):
            pass

        def do_GET(self):
            path = self.path.split("?")[0]
            arrived.add(path)
            try:
                if path == "/partial":
                    # the headers and the start of the body arrive, the rest is held: the response is there, the
                    # navigation has changed process, the document is not complete
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.send_header("Content-Length", "200")
                    self.end_headers()
                    self.wfile.write(b"<!doctype html><title>partial</title><p>start")
                    self.wfile.flush()
                    time.sleep(5)
                    self.wfile.write(b" " * 156)
                    return
                if path == "/slow-frame":
                    time.sleep(3)
                body = ("<!doctype html><title>%s</title><p>%s" % (path, path)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except OSError:
                pass

    server = LocalServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    yield {"at": lambda host, path, p=None: "http://%s:%d%s" % (host, p or port, path), "arrived": arrived}
    server.shutdown()
    server.server_close()
    thread.join()


def _run(firefox_binary, scenario):
    from invisible_playwright.async_api import InvisiblePlaywright

    async def go():
        async with InvisiblePlaywright(seed=42, binary_path=firefox_binary, humanize=False, headless=True,
                                       extra_prefs=FISSION_ON) as browser:
            page = await browser.new_page()
            return await scenario(page)

    return asyncio.run(go())


def test_a_goto_to_another_site_stopped_by_the_page_it_leaves_raises_aborted(firefox_binary, sites):
    async def scenario(page):
        await page.goto(sites["at"]("a.test", "/plain"))
        await page.evaluate("setTimeout(() => window.stop(), 1000)")
        began = time.monotonic()
        try:
            await page.goto(sites["at"]("b.test", "/slow-frame"), wait_until="domcontentloaded", timeout=15000)
        except Exception as e:
            return str(e), time.monotonic() - began, page.url
        return None, time.monotonic() - began, page.url

    error, took, url = _run(firefox_binary, scenario)
    assert error is not None and "NS_BINDING_ABORTED" in error, error
    assert took < 8, took
    assert url.startswith("http://a.test:"), url


def test_a_second_goto_to_a_third_site_interrupts_the_first_and_lands(firefox_binary, sites):
    async def scenario(page):
        await page.goto(sites["at"]("a.test", "/plain"))

        async def go(url, after=None):
            while after is not None and after not in sites["arrived"]:
                await asyncio.sleep(0.02)
            if after is not None:
                # the first response has arrived and is held: the navigation has changed process
                await asyncio.sleep(1.0)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                return None
            except Exception as e:
                return str(e)

        began = time.monotonic()
        first, second = await asyncio.gather(
            go(sites["at"]("b.test", "/partial")),
            go(sites["at"]("c.test", "/second"), after="/partial"))
        return first, second, time.monotonic() - began, page.url

    first, second, took, url = _run(firefox_binary, scenario)
    assert first is not None and "is interrupted by another navigation" in first, first
    assert second is None, second
    assert took < 8, took
    assert url == "http://c.test:" + url.split(":")[2], url


def test_an_iframe_refused_its_load_ends_it_and_its_error_page_is_a_navigation(firefox_binary, sites):
    async def scenario(page):
        await page.goto(sites["at"]("a.test", "/plain"))
        events = []
        page.on("frameattached", lambda f: events.append("attached"))
        await page.evaluate(
            "(u) => { const f = document.createElement('iframe'); f.id = 'r'; f.src = u; document.body.appendChild(f); }",
            sites["at"]("b.test", "/x", 9))   # a port Firefox refuses

        def frames():
            return [(f.url, f.name) for f in page.frames]

        # until the error page's frame is there with its address (a condition, not 2.5 s: a loaded machine is slower)
        seen = await awatch(frames, lambda found: len(found) == 2 and urlsplit(found[1][0])[:2] == ("http", "b.test:9"),
                            DEADLINE, lambda: page.wait_for_timeout(100))
        return frames(), events, seen.render()

    frames, events, seen = _run(firefox_binary, scenario)
    assert len(frames) == 2, "%r; the frames over time: %s" % (frames, seen)
    assert frames[1][1] == "r", "%r; the frames over time: %s" % (frames, seen)   # named by its iframe, in whatever process
    assert urlsplit(frames[1][0])[:2] == ("http", "b.test:9"), "%r; the frames over time: %s" % (frames, seen)


def test_an_iframe_removed_while_it_loads_is_aborted_then_gone(firefox_binary, sites):
    async def scenario(page):
        await page.goto(sites["at"]("a.test", "/plain"))
        counts = {"attached": 0, "detached": 0}
        page.on("frameattached", lambda f: counts.__setitem__("attached", counts["attached"] + 1))
        page.on("framedetached", lambda f: counts.__setitem__("detached", counts["detached"] + 1))
        await page.evaluate(
            "(u) => { const f = document.createElement('iframe'); f.id = 's'; f.src = u; document.body.appendChild(f);"
            " setTimeout(() => f.remove(), 1000); }", sites["at"]("b.test", "/slow-frame"))
        # until the iframe has been attached AND detached and only the page is left (the removal comes 1 s after the
        # insertion, whatever the machine: a test that only reads the list could read it before the frame existed)
        seen = await awatch(lambda: (counts["attached"], counts["detached"], len(page.frames)),
                            lambda state: state[0] >= 1 and state[1] >= 1 and state[2] == 1,
                            DEADLINE + 2, lambda: page.wait_for_timeout(100))
        return [f.url for f in page.frames], seen.render()

    urls, seen = _run(firefox_binary, scenario)
    assert len(urls) == 1 and urls[0].startswith("http://a.test:"), (
        "%r; (frames attached, detached, listed) over time: %s" % (urls, seen))


def test_an_iframe_whose_src_goes_to_another_site_shows_the_new_address(firefox_binary, sites):
    async def scenario(page):
        await page.goto(sites["at"]("a.test", "/plain"))
        await page.evaluate(
            "(u) => { const f = document.createElement('iframe'); f.id = 'f'; f.src = u; document.body.appendChild(f); }",
            sites["at"]("b.test", "/first"))
        # until the iframe is at its first address (a condition, not a time: a loaded machine takes longer than 1.5 s)
        first = await awatch(lambda: [f.url for f in page.frames], lambda urls: any(u.endswith("/first") for u in urls),
                             DEADLINE, lambda: page.wait_for_timeout(100))
        before = [f.url for f in page.frames]
        await page.evaluate("(u) => { document.getElementById('f').src = u; }", sites["at"]("c.test", "/second"))
        second = await awatch(lambda: [f.url for f in page.frames], lambda urls: any(u.endswith("/second") for u in urls),
                              DEADLINE, lambda: page.wait_for_timeout(100))
        return before, [(f.url, f.name) for f in page.frames], first.render(), second.render()

    before, after, seen_first, seen_second = _run(firefox_binary, scenario)
    seen = "; the frame urls over time: first %s | second %s" % (seen_first, seen_second)
    assert len(before) == 2 and before[1].startswith("http://b.test:") and before[1].endswith("/first"), (
        "%r%s" % (before, seen))
    assert len(after) == 2, "%r%s" % (after, seen)
    assert after[1][0].startswith("http://c.test:") and after[1][0].endswith("/second"), "%r%s" % (after, seen)
    assert after[1][1] == "f", "%r%s" % (after, seen)            # still the frame of its <iframe>
