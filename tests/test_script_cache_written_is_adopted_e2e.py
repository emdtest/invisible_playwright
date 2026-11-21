"""A fresh profile keeps the startup script cache it wrote as a file mapping, not as a second copy on the heap (firefox-68, Linux and Windows).

What it fixes, in the published engine (firefox-68), for the hooks of `platform/script-cache` (`js/xpconnect/loader/ScriptPreloader.cpp` and `.h`, the own
file `StealthScriptCache-inl.h`): the script preloader writes `<profile>/startupCache/scriptCache.bin` once the startup is over. On a WARM profile the
cache is a file mapping (`explicit/script-preloader/non-heap/memmapped-cache`) and costs no heap. On a FRESH profile the same bytes were built in memory
for the write and stayed on the heap for the whole session (16.5 MB measured in the parent, with the 13.3 MB file already on disk): the engine now maps the
file it wrote and points every entry into the mapping (`AdoptWrittenCache`), and reports the mapping as
`explicit/script-preloader/non-heap/memmapped-written-cache`, so the megabytes that left the heap appear somewhere. It is a memory property, not a
fingerprint one: a page cannot read it. The test reads the memory report of the parent (the engine's own reporters, `nsIMemoryReporterManager.getReports`,
in chrome scope through Marionette: the route of `tests/webfont_probe.py`; `MOZ_REMOTE_ALLOW_SYSTEM_ACCESS` is set with `monkeypatch`).

What the test does. A NEW profile directory, a session on it. It waits on the CONDITION "`startupCache/scriptCache.bin` exists" (not on a timer), then on the
condition "the parent reports a non-zero `memmapped-written-cache`" (the file appears when the write is moved into place and is adopted a moment after).
It asserts, for that session: `memmapped-written-cache` equals the size of the file (non-zero), and `heap/saved-scripts` is below the size of the file (the
heap copies are gone: without the hook it is about the size of the file). Then the SAME profile is opened again (warm): `memmapped-cache` equals the size of
the file the session started with, and `memmapped-written-cache` is 0 unless the session wrote the cache again, in which case it equals the new size.

Negative control: the warm session of the same profile. The fresh and the warm session differ in the two reporters in the way described (written is the size
of the file in one and 0 in the other, read is 0 in one and the size in the other), so the reading cannot give the same number whatever the engine does.
"""
from __future__ import annotations

import json

import pytest

from _waits import wait_until

pytestmark = pytest.mark.e2e

MARIONETTE_ARGS = ["-marionette", "-remote-allow-system-access"]
WRITTEN = "explicit/script-preloader/non-heap/memmapped-written-cache"
READ_CACHE = "explicit/script-preloader/non-heap/memmapped-cache"
SAVED = "explicit/script-preloader/heap/saved-scripts"
LAUNCH = dict(seed=42, headless=True, humanize=False, locale="en-US", timezone="America/New_York")

#: The reports of the parent process (the process name of a reporter of this process is the empty string) under the script preloader.
REPORT_JS = """
const done = arguments[arguments.length - 1];
const out = {};
const manager = Cc["@mozilla.org/memory-reporter-manager;1"].getService(Ci.nsIMemoryReporterManager);
manager.getReports(
  {callback(process, path, kind, units, amount, description) {
    if (process === "" && path.startsWith("explicit/script-preloader/")) { out[path] = amount; }
  }},
  null,
  {callback() { done(JSON.stringify(out)); }},
  null,
  false);
"""


def _reports(marionette):
    answer = marionette.call("WebDriver:ExecuteAsyncScript", {"script": REPORT_JS, "args": []})
    return json.loads(answer["value"])


def _session(binary, profile, port):
    from invisible_playwright import InvisiblePlaywright

    return InvisiblePlaywright(binary_path=binary, profile_dir=profile, extra_args=MARIONETTE_ARGS,
                               extra_prefs={"marionette.port": port}, **LAUNCH)


def _open_marionette(port):
    import webfont_probe

    marionette = webfont_probe._Marionette(port)
    marionette.call("WebDriver:NewSession", {"capabilities": {"alwaysMatch": {}}})
    marionette.call("WebDriver:SetTimeouts", {"script": 240000})
    marionette.call("Marionette:SetContext", {"value": "chrome"})
    return marionette


def test_a_fresh_profile_reports_its_written_cache_as_mapped(firefox_binary, monkeypatch, tmp_path):
    import webfont_probe

    monkeypatch.setenv("MOZ_REMOTE_ALLOW_SYSTEM_ACCESS", "1")   # the chrome-scope read of the memory report
    profile = tmp_path / "profile"
    cache = profile / "startupCache" / "scriptCache.bin"

    # The fresh session: the cache is written during it and adopted.
    port = webfont_probe.marionette_port()
    marionette = None
    try:
        with _session(firefox_binary, profile, port) as context:
            page = context.new_page()
            page.goto("about:blank", timeout=60_000)
            written_file = wait_until(lambda: cache.exists() and cache.stat().st_size > 0, 180, lambda: page.wait_for_timeout(500))
            assert written_file, "the profile never got %s (the startup script cache is written once the startup is over)" % cache
            marionette = _open_marionette(port)
            seen = {}

            def reported():
                seen.clear()
                seen.update(_reports(marionette))
                return seen.get(WRITTEN, 0) > 0

            adopted = wait_until(reported, 60, lambda: page.wait_for_timeout(500))
            size = cache.stat().st_size
            fresh = dict(seen)
    finally:
        if marionette is not None:
            marionette.close()
    assert adopted, "the parent never reported a written cache as mapped (%s); the reports of the preloader: %r" % (WRITTEN, fresh)
    assert fresh[WRITTEN] == size, "the mapping reported (%r) is not the file written (%d bytes): %r" % (fresh[WRITTEN], size, fresh)
    assert fresh.get(SAVED, 0) < size, "the heap still holds the scripts of the cache that is on disk: %r (file %d bytes)" % (fresh, size)
    assert fresh.get(READ_CACHE, 0) == 0, "a fresh profile has no cache to read: %r" % (fresh,)

    # Control: the same profile, warm. The cache of the file is read as a mapping and nothing written this session is reported unless it was rewritten.
    started_with = size
    port = webfont_probe.marionette_port()
    marionette = None
    try:
        with _session(firefox_binary, profile, port) as context:
            page = context.new_page()
            page.goto("about:blank", timeout=60_000)
            marionette = _open_marionette(port)
            warm = _reports(marionette)
            now = cache.stat().st_size
    finally:
        if marionette is not None:
            marionette.close()
    assert warm.get(READ_CACHE, 0) == started_with, "the warm session did not map the cache it found (%d bytes): %r" % (started_with, warm)
    assert warm.get(WRITTEN, 0) in (0, now), "the warm session reports a written cache that is not its file: %r (file %d bytes)" % (warm, now)
    assert warm.get(READ_CACHE, 0) != fresh.get(READ_CACHE, 0)
