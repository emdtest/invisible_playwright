"""A download reaches Playwright: downloadCreated, then downloadFinished, and the
file is where the context's download options say.

**Why this file exists.** The engine accepted Browser.setDownloadOptions and
registered Juggler's interceptor (nsExternalHelperAppService::SetDownloadInterceptor),
but nothing ever called it: no Browser.downloadCreated, no downloadFinished, and
a navigation that ends in a download got no event telling it so. Playwright
Firefox calls the interceptor from nsExternalAppHandler::OnStartRequest, and
reports completion from OnSaveComplete and Cancel. Reported 2026-10-30 on the
151 and 159 engines.

Known-bad input: the engine before the fix -> every test here goes red
(expect_download times out).

**The end of a download, and what is left to Firefox.** Two more tests hold the
other half of the hook (`StealthDownloadIntercept`, folder `automation/downloads`
of the engine series, hooks in `uriloader/exthandler/nsExternalHelperAppService.cpp`):

* a download the client cancels (`download.cancel()`) reports its end ONCE, as
  `canceled`: `Cancel` and `OnSaveComplete` both end in the interceptor's
  `onDownloadComplete`, and a second `Browser.downloadFinished` for the same
  uuid would be a second report of one download. Counted on the engine's own
  events (the connection's listener), not on the client's objects: the client
  ignores a repeated finish.
* a context that has NO download options is not intercepted: the engine sends
  no `Browser.downloadCreated` and no `Browser.downloadFinished`, and the file
  goes where Firefox puts it (`browser.download.dir`). The same context, with
  its options, is the control: its download is announced and saved in the
  wrapper's directory, so the silence of the second download is the absence of
  the hook and not a page that never downloaded.

Why a page cares: a download that ends twice, or that lands in the wrong place,
is a script that waits for a file that never comes, and a context that was given
no options must behave as retail Firefox does.
"""
from __future__ import annotations

import http.server
import os
import threading

import pytest

from _waits import wait_until

BODY = b"invisible download body\n" * 64


def _serve():
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.startswith("/file"):
                self.send_response(200)
                self.send_header("content-type", "application/octet-stream")
                self.send_header("content-disposition", 'attachment; filename="report.bin"')
                self.send_header("content-length", str(len(BODY)))
                self.end_headers()
                self.wfile.write(BODY)
                return
            self.send_response(200)
            self.send_header("content-type", "text/html")
            self.end_headers()
            self.wfile.write(b'<title>d</title><a id=dl href="/file">get</a>')

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d/" % srv.server_address[1]


@pytest.fixture
def served():
    srv, url = _serve()
    try:
        yield url
    finally:
        srv.shutdown()


@pytest.mark.e2e
def test_a_clicked_download_is_reported_and_saved(firefox_binary, served, tmp_path):
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=4242, binary_path=firefox_binary, headless=True) as browser:
        page = browser.new_context(accept_downloads=True).new_page()
        page.goto(served, timeout=30_000)
        with page.expect_download(timeout=30_000) as info:
            page.click("#dl")
        download = info.value
        assert download.suggested_filename == "report.bin"
        target = tmp_path / "report.bin"
        download.save_as(target)
        assert target.read_bytes() == BODY
        assert download.failure() is None


@pytest.mark.e2e
def test_a_navigation_that_ends_in_a_download_is_reported(firefox_binary, served):
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=4242, binary_path=firefox_binary, headless=True) as browser:
        page = browser.new_context(accept_downloads=True).new_page()
        with page.expect_download(timeout=30_000) as info:
            with pytest.raises(Exception) as nav:
                page.goto(served + "file", timeout=20_000)
        # Playwright reports such a goto as "Download is starting"; it must END,
        # not hang until the timeout.
        assert "download" in str(nav.value).lower(), str(nav.value)
        assert info.value.suggested_filename == "report.bin"


# --------------------------------------------------------------------------------------------------------------------
#  The end of a download, and the context that has no download options
# --------------------------------------------------------------------------------------------------------------------
#: What the held answer promises: far more than the first chunk, so the transfer cannot end by itself.
HELD_LENGTH = 64 * 1024 * 1024

#: The longest the held answer waits for the test to let it go (only so that a failed test leaves no thread behind).
HELD_GIVES_UP_AFTER = 120


def _serve_held():
    """A site with `/file` (a quick attachment) and `/slow` (an attachment whose answer stops after its first chunk
    and goes on only when `release` is set): the held transfer is what a `cancel()` interrupts, with no clock."""
    release = threading.Event()

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            try:
                if self.path.startswith("/file"):
                    self.send_response(200)
                    self.send_header("content-type", "application/octet-stream")
                    self.send_header("content-disposition", 'attachment; filename="report.bin"')
                    self.send_header("content-length", str(len(BODY)))
                    self.end_headers()
                    self.wfile.write(BODY)
                elif self.path.startswith("/slow"):
                    self.send_response(200)
                    self.send_header("content-type", "application/octet-stream")
                    self.send_header("content-disposition", 'attachment; filename="held.bin"')
                    self.send_header("content-length", str(HELD_LENGTH))
                    self.end_headers()
                    self.wfile.write(b"x" * 65536)
                    self.wfile.flush()
                    release.wait(HELD_GIVES_UP_AFTER)
                else:
                    self.send_response(200)
                    self.send_header("content-type", "text/html")
                    self.end_headers()
                    self.wfile.write(b'<title>d</title><a id=dl href="/file">get</a>'
                                     b'<a id=slow href="/slow">slow</a>')
            except OSError:
                pass   # the browser dropped the connection: that is the cancel

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d/" % srv.server_address[1], release


@pytest.fixture
def held_site():
    srv, url, release = _serve_held()
    try:
        yield url
    finally:
        release.set()
        srv.shutdown()


def _engine_side(context):
    """The browser dispatcher (with the engine connection, `.conn`) and the context dispatcher of the in-process server
    that answers this context.

    The server runs IN THIS PROCESS (as `test_injected_node_surface.py` uses it), so the test can listen to the
    engine's own events and send the one command the client API never sends: `Browser.setDownloadOptions` with no
    options, which is a context that was never given any."""
    impl = context._impl_obj
    server = impl._connection._transport._server
    dispatcher = server.object(impl._guid)
    return dispatcher.browser, dispatcher


def _record(conn):
    """Every `Browser.downloadCreated` and `Browser.downloadFinished` the engine sends, as `(method, params)`."""
    events = []

    def listener(method, params, session):
        if method in ("Browser.downloadCreated", "Browser.downloadFinished"):
            events.append((method, dict(params or {})))

    conn.add_listener(listener)
    return events


def _of(events, method, uuid=None):
    return [p for m, p in events if m == method and (uuid is None or p.get("uuid") == uuid)]


@pytest.mark.e2e
def test_a_canceled_download_reports_once_as_canceled(firefox_binary, held_site):
    from invisible_playwright import InvisiblePlaywright

    with InvisiblePlaywright(seed=4242, binary_path=firefox_binary, headless=True) as browser:
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        engine, _context = _engine_side(context)
        events = _record(engine.conn)
        page.goto(held_site, timeout=30_000)

        with page.expect_download(timeout=30_000) as info:
            page.click("#slow")
        held = info.value
        assert held.suggested_filename == "held.bin"
        held.cancel()
        assert held.failure() == "canceled"

        # The engine announced this download once, and it must say its end once. A later duplicate of the same end
        # would follow the first one on the same stream, so a second download, run to its end, is the barrier: the
        # events are read in order, and when the second download has finished anything the first had left to say has
        # been said.
        created = _of(events, "Browser.downloadCreated")
        assert len(created) == 1, created
        held_uuid = created[0]["uuid"]

        with page.expect_download(timeout=30_000) as info:
            page.click("#dl")
        quick = info.value
        assert quick.failure() is None
        quick_uuid = _of(events, "Browser.downloadCreated")[1]["uuid"]
        assert wait_until(lambda: _of(events, "Browser.downloadFinished", quick_uuid), 60,
                          pump=lambda: page.wait_for_timeout(100)), events

        ended = _of(events, "Browser.downloadFinished", held_uuid)
        assert len(ended) == 1, "the canceled download reported its end %d times: %r" % (len(ended), events)
        assert ended[0].get("canceled") is True, ended
        assert not ended[0].get("error"), ended
        # and the quick one, after it, ends clean: a cancel does not leave the interceptor in a state of its own
        assert len(_of(events, "Browser.downloadFinished", quick_uuid)) == 1, events
        assert not _of(events, "Browser.downloadFinished", quick_uuid)[0].get("canceled"), events


@pytest.mark.e2e
def test_a_context_without_download_options_is_left_to_firefox(firefox_binary, served, tmp_path):
    """The negative control of the interception: the same context, the same link, the same file.

    With its options the download is taken (announced, saved in the wrapper's directory). After
    `Browser.setDownloadOptions` with no options the engine does not take it: no `downloadCreated`, no
    `downloadFinished`, no `download` event of the client, and the file is where Firefox's own preference says."""
    from invisible_playwright import InvisiblePlaywright

    where = tmp_path / "firefox"
    where.mkdir()
    prefs = {"browser.download.folderList": 2,
             "browser.download.dir": str(where),
             "browser.download.useDownloadDir": True}
    with InvisiblePlaywright(seed=4242, binary_path=firefox_binary, headless=True,
                             extra_prefs=prefs) as browser:
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        engine, ctx = _engine_side(context)
        events = _record(engine.conn)
        announced = []
        page.on("download", lambda download: announced.append(download))
        page.goto(served, timeout=30_000)

        # the control: with its options the download is taken
        with page.expect_download(timeout=30_000) as info:
            page.click("#dl")
        info.value.path()
        assert wait_until(lambda: _of(events, "Browser.downloadFinished"), 60,
                          pump=lambda: page.wait_for_timeout(100)), events
        assert len(_of(events, "Browser.downloadCreated")) == 1, events
        assert len(_of(events, "Browser.downloadFinished")) == 1, events
        downloads_dir = engine._downloads_dir(ctx.context_id)
        saved_by_the_wrapper = sorted(os.listdir(downloads_dir))
        assert len(saved_by_the_wrapper) == 1, saved_by_the_wrapper
        assert os.listdir(str(where)) == [], "the taken download landed in Firefox's directory"

        # the context loses its download options: Firefox's own path takes the download
        engine.conn.send("Browser.setDownloadOptions",
                         {"browserContextId": ctx.context_id, "downloadOptions": None}, timeout=None)
        # The click on a download Firefox keeps plans a navigation that never completes (nothing commits: the response is a download),
        # so the click does not wait for it; the end of the test waits on the CONDITION, the file in Firefox's directory.
        page.click("#dl", no_wait_after=True, timeout=30_000)
        landed = where / "report.bin"
        assert wait_until(lambda: landed.exists() and landed.read_bytes() == BODY, 120,
                          pump=lambda: page.wait_for_timeout(100)), os.listdir(str(where))

        assert len(_of(events, "Browser.downloadCreated")) == 1, "the engine took a download of a context with no options"
        assert len(_of(events, "Browser.downloadFinished")) == 1, events
        assert len(announced) == 1, announced
        assert sorted(os.listdir(downloads_dir)) == saved_by_the_wrapper, "the wrapper's directory got a second file"
