"""`close()` returns when the BROWSER has exited, and what is left of the launch is the reaper's.

⛔ THE KNOWN-BAD INPUT: invisible-core's `Connection.close` stopped waiting for the sender of the shutdown pings (it leaves by
itself within about a second) and gave it to the launch's reaper, but the wrapper's server still ran `SessionFiles.remove`
right after the shutdown, and that waits for the sender and the tree: the close of the wrapper took as long as before. The
server hands the removal to the reaper now (`_juggler/_reaped.py`).

Measured on the real engine: the time from the exit of the engine's parent process (read with psutil on its pid) to the
return of the session's exit. And once the reaper has finished (`wait_reaped`) nothing of the session is left: no process, and
no profile directory.

A core with no reaper fails this test, with the reason: it is the test of the core that has one, and a skip would let the pin
move to a core that waits again without anyone seeing it.
"""
from __future__ import annotations

import os
import threading
import time

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.wall_clock]

#: From the exit of the browser to the return of the close. The pings' sender needs 0.6 s (Linux) to 1.0 s (Windows) to leave,
#: and the close must not have waited for it.
RETURN_WITHIN_S = 0.5


def _the_core_has_a_reaper() -> None:
    import invisible_core

    try:
        from invisible_core.process import Reaper  # noqa: F401
    except ImportError:
        pytest.fail(
            "the pinned invisible-core %s has no `process.Reaper`: its close still waits for the sender of the shutdown "
            "pings, so there is nothing for the wrapper to hand over. This test needs the core that returns when the "
            "browser has exited (oss-invisible/core#284); move the pin to it." % invisible_core.__version__,
            pytrace=False)


def _the_engine_parent(members):
    """The process of the engine itself among the session's: not a child process of it (`-contentproc`), not the sender."""
    import psutil

    pids = {p.pid for p in members}
    parents = []
    for proc in members:
        try:
            cmd = proc.cmdline()
            if "-contentproc" in cmd or proc.ppid() in pids:
                continue
            parents.append(proc)
        except psutil.Error:
            continue
    assert parents, "no engine process among the session's: %r" % [p.pid for p in members]
    return min(parents, key=lambda p: p.create_time())


def _profile_of(proc):
    cmd = proc.cmdline()
    for flag in ("-profile", "--profile"):
        if flag in cmd and cmd.index(flag) + 1 < len(cmd):
            return cmd[cmd.index(flag) + 1]
    return None


def test_the_close_returns_when_the_browser_has_exited_and_the_reaper_leaves_nothing(firefox_binary):
    _the_core_has_a_reaper()
    import psutil

    from invisible_core.process import SessionToken, find_processes, wait_until_gone
    from invisible_playwright import InvisiblePlaywright
    from invisible_playwright._juggler._reaped import wait_reaped

    exited_at = []
    session = InvisiblePlaywright(seed=7104, binary_path=firefox_binary, humanize=False, headless=True)
    with session as browser:
        page = browser.new_page()
        page.set_content("<p>a page, so a content process exists</p>")
        token = SessionToken(session._session_token.value)
        engine = _the_engine_parent(find_processes(token))
        profile = _profile_of(engine)
        assert profile and os.path.isdir(profile), "the engine's profile was not found: %r" % (profile,)

        def watch() -> None:
            while True:
                try:
                    if not engine.is_running() or engine.status() == psutil.STATUS_ZOMBIE:
                        break
                except psutil.Error:
                    break
                time.sleep(0.005)
            exited_at.append(time.monotonic())

        watcher = threading.Thread(target=watch, daemon=True, name="engine-exit-watch")
        watcher.start()
    returned_at = time.monotonic()
    watcher.join(timeout=30)
    assert exited_at, "the engine's parent process was still running 30 s after the close returned"
    took = returned_at - exited_at[0]
    assert took <= RETURN_WITHIN_S, (
        "close() returned %.3f s after the browser's exit (limit %.1f s): it waited for the sender of the pings or the tree, "
        "which are the reaper's" % (took, RETURN_WITHIN_S))

    assert wait_reaped(token, timeout=30), "the reaper did not finish within 30 s of the close"
    assert wait_until_gone(token, timeout=30), "processes of the session after the reaper: %s" % [
        (p.pid, p.name()) for p in find_processes(token)]
    assert not os.path.exists(profile), "the profile is still there once the reaper has finished: %s" % profile
