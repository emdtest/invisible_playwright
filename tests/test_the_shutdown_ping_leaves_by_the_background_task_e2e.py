"""The telemetry ping sent at shutdown is sent by the engine's own background task, `firefox --backgroundtask pingsender` (firefox-68, Linux and Windows).

What it fixes, in the published engine (firefox-68), for the hooks of `stillborn/telemetry` (`toolkit/components/telemetry/app/TelemetrySend.sys.mjs`,
`pings/BackgroundTask_pingsender.sys.mjs`, the own files `StealthPingsender.sys.mjs` and `StealthPingsenderRoute.sys.mjs`): the shutdown ping
never goes through `pingsender[.exe]` (a separate program that takes its road from the host: WinINet's proxy settings on Windows, libcurl's
environment on Linux) but through the engine itself, with necko, by the road of the session. A server that receives a ping sees who sent it: the
background task is the one whose request says `User-Agent: pingsender/2.0`. The program of the host says another one, and a ping that
left from the host's own address says where the browser is. The server of telemetry is a pref (`toolkit.telemetry.server`), so the test points it at a
server of its own on 127.0.0.1: nothing leaves the machine.

What the test does. A session (seed 42, no proxy; the road of the session is decided by the launch) with the telemetry server pref at the fake endpoint
is opened and closed. The session ENDS (`_session_end.EndingPlaywright`: the reaper has finished, so the sender is finished or ended), then the test
reads what the endpoint received.

What it asserts:
* at least one request reached the endpoint with `User-Agent: pingsender/2.0`, a POST to a path of the telemetry protocol (`/submit/telemetry/...`),
  with a body;
* a second endpoint that the session was NOT pointed at received nothing (the pref is the address, there is no other).

Negative control: the same session with `datareporting.healthreport.uploadEnabled` false (the switch retail Firefox has for it) sends no request with
that user agent. The test therefore fails when the ping leaves by another program (its user agent differs) and when the session sends none.
"""
from __future__ import annotations

import http.server
import threading

import pytest

from _session_end import EndingPlaywright
from _waits import wait_until

pytestmark = pytest.mark.e2e

SERVER = "toolkit.telemetry.server"
UPLOAD = "datareporting.healthreport.uploadEnabled"
BACKGROUND_TASK_AGENT = "pingsender/2.0"


class Endpoint:
    """A telemetry endpoint on 127.0.0.1 (a port the OS gives) that records every request and answers 200."""

    def __init__(self):
        self.requests = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            timeout = 10

            def _record(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                outer.requests.append({"method": self.command, "path": self.path, "agent": self.headers.get("User-Agent"),
                                       "bytes": len(body)})
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_POST = do_PUT = do_GET = _record

            def log_message(self, *args):
                pass

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:%d" % self._server.server_address[1]

    def by_agent(self, agent):
        return [r for r in self.requests if r["agent"] == agent]

    def close(self):
        self._server.shutdown()
        self._server.server_close()


def session(binary, endpoint, extra_prefs=None):
    """One session pointed at ``endpoint``, opened and closed; it has ended (the sender included) when this returns."""
    prefs = {SERVER: endpoint.url}
    prefs.update(extra_prefs or {})
    with EndingPlaywright(seed=42, binary_path=binary, headless=True, humanize=False, locale="en-US",
                          timezone="America/New_York", extra_prefs=prefs) as browser:
        page = browser.new_page()
        page.goto("about:blank", timeout=60_000)


def test_the_shutdown_ping_is_sent_by_the_background_task_and_not_by_the_host_program(firefox_binary):
    endpoint, elsewhere = Endpoint(), Endpoint()
    stop = threading.Event()
    try:
        session(firefox_binary, endpoint)
        # The session has ended; the request of the sender is on the endpoint, or it comes within the time a healthy run allows.
        arrived = wait_until(lambda: endpoint.by_agent(BACKGROUND_TASK_AGENT), 60, lambda: stop.wait(0.25))
        seen = [(r["method"], r["path"][:60], r["agent"], r["bytes"]) for r in endpoint.requests]
        assert arrived, ("no request with `User-Agent: %s` reached the endpoint after the session ended; what it received: %s"
                         % (BACKGROUND_TASK_AGENT, seen))
        pings = endpoint.by_agent(BACKGROUND_TASK_AGENT)
        assert all(r["method"] == "POST" and r["path"].startswith("/submit/telemetry/") and r["bytes"] > 0 for r in pings), pings
        assert elsewhere.requests == [], "an endpoint the session was not pointed at received %s" % (elsewhere.requests,)

        # Control: with the upload switched off no ping leaves, so the request above is the product's and not the endpoint's own noise.
        control = Endpoint()
        try:
            session(firefox_binary, control, extra_prefs={UPLOAD: False})
            assert control.by_agent(BACKGROUND_TASK_AGENT) == [], (
                "a session with the upload switched off still sent a ping: %s" % (control.requests,))
        finally:
            control.close()
    finally:
        endpoint.close()
        elsewhere.close()
