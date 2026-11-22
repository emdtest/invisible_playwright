"""Which of the engine's processes host a site, told by the name of the site: what the e2e tests that look at processes read.

Counting processes between two reads does not tell a site's process from the browser's own. The browser starts
content processes for itself: it refills the pool of preallocated ones (`dom.ipc.processPrelaunch.fission.number`)
whenever it gets to it, and the New Tab page starts a process for a Mozilla site of its own (remote type
`webIsolated=https://mozgcp.net`, born about a second after the process of the New Tab page, with any page, even
about:blank). Under load that process arrives after the first read of a test, and "the set of processes changed"
read it as the effect of the frame under test: the same-site control was red in 14 of 30 runs of the whole e2e suite
(`[P] -> [P, P2]`), and "at least one new process" for a cross-site frame was satisfied by that process alone.

So a test asks for the processes of ONE site, by name. With the pref `fission.processSiteNames` on (`NAMED_BY_SITE`,
test-only: the product never sets it) a content process that hosts a site is named by that site
(`ContentChild::SetProcessName`, dom/ipc/ContentChild.cpp: a leading `https://` is dropped), and on Linux
`prctl(PR_SET_NAME)` keeps 15 characters (ipc/glue/ProcessUtils_linux.cpp). Measured on firefox-62 (Linux):

- the process of `http://a.test` in a Playwright context is named `http://a.test^u`: the site, then the start of
  `^userContextId=N`, cut at 15 characters;
- the process the New Tab page starts is named `mozgcp.net`;
- a process with no site keeps its type: `Web Content`, `Privileged Cont`, `WebExtensions`.

The process gets its name before any document loads in it, so when a page of the site has run, its process already
answers to the site. Windows names no process (ProcessUtils_none.cpp): a test that looks at sites skips there and
says so.

Windows names no process, but the engine's own log names the type of every process it creates (`Setting remoteType of
process <pid> to <type>` when a process is born with its type, `Changing remoteType of process <pid> from prealloc to
<type>` when a spare one is given its site: `dom/ipc/ContentChild.cpp`, `RecvSetRemoteType`, log module `Process`). The
type of a process that hosts a site is `webIsolated=<site>^<attributes>` (`dom/ipc/RemoteType.cpp`, `Stringify`), the
same site the Linux name carries, and the process gets it before any document loads in it. So on Windows a test starts the
engine with `LOG_ENV` and a file base (`MOZ_LOG_FILE`), and `remote_types(base)` reads which type each pid was given;
liveness and the engine's own directory are still told by the process tree, so a process that has ended is not counted.
"""
from __future__ import annotations

import os
import re
import sys

import pytest

#: The pref overlay that names a content process by the site it hosts. Test-only.
NAMED_BY_SITE = {"fission.processSiteNames": True}

#: What PR_SET_NAME keeps of a process name on Linux.
NAME_LENGTH = 15


def name_of_site(site):
    """What the process name of `site` starts with: the site without a leading `https://`, cut at 15 characters."""
    if site.startswith("https://"):
        site = site[len("https://"):]
    return site[:NAME_LENGTH]


def hosts_site(name, site):
    """True when a process called `name` is a process of `site`.

    The name is the prefix itself, or the prefix followed by `^` (the start of `^userContextId=N`). When the prefix is
    shorter than 15 characters the next character has to be `^`, so `http://a.test` does not match a process of
    `http://a.testing.example`."""
    prefix = name_of_site(site)
    if name == prefix:
        return True
    if not name.startswith(prefix):
        return False
    return len(prefix) >= NAME_LENGTH or name[len(prefix)] == "^"


#: The environment that makes the engine write the type of each process it creates (`MOZ_LOG_FILE` is added by the
#: test: a file base in its own temporary folder). Used where the system names no process (Windows).
LOG_ENV = {"MOZ_LOG": "Process:5,timestamp,sync"}

_TYPE_GIVEN = re.compile(r"(?:Setting remoteType of process (\d+) to|Changing remoteType of process (\d+) from \S+ to) (\S+)")


def remote_types(base):
    """`{pid: remote type}` of the processes the engine has logged to the files that start with the path `base`
    (`MOZ_LOG_FILE`: the parent writes `base`, each child its own `base.child-N`). A process that logs twice (a spare one,
    then the site it is given) keeps its last type: the lines of one process are in one file, in order."""
    types = {}
    for path in sorted(base.parent.glob(base.name + "*")):
        for match in _TYPE_GIVEN.finditer(path.read_text(encoding="utf-8", errors="replace")):
            types[int(match.group(1) or match.group(2))] = match.group(3)
    return types


def hosts_site_by_type(remote_type, site):
    """True when a process of the remote type `remote_type` hosts `site` (`http://a.test`): `<kind>=<site>` or
    `<kind>=<site>^<attributes>`. A process with no site (`web`, `prealloc`, `privilegedabout`, `extension`) has no `=`."""
    meta = remote_type.partition("=")[2]
    return meta == site or meta.startswith(site + "^")


def site_pids_by_type(processes, site, types, engine_dir=None):
    """Like `site_pids`, told by the remote types the engine logged (`remote_types`) instead of by the process name."""
    return _content_pids(processes, engine_dir, lambda proc, _name: hosts_site_by_type(types.get(proc.pid, ""), site))


def site_pids(processes, site, engine_dir=None):
    """The pids, among `processes` (psutil-like), of the content processes (`-contentproc ... tab`) that host `site`;
    with `engine_dir`, only those of the engine in that directory."""
    return _content_pids(processes, engine_dir, lambda _proc, name: hosts_site(name, site))


def _content_pids(processes, engine_dir, hosts):
    import psutil

    pids = set()
    for proc in processes:
        try:
            if engine_dir is not None and not os.path.normcase(proc.exe()).startswith(os.path.normcase(engine_dir)):
                continue
            name, cmd = proc.name(), proc.cmdline()
        except psutil.Error:
            continue
        if "-contentproc" in cmd and cmd and cmd[-1] == "tab" and hosts(proc, name):
            pids.add(proc.pid)
    return pids


def site_processes_below(root_pid, site, engine_dir=None, types=None):
    """The pids of the processes below `root_pid` that host `site`. On Linux the name of the process says it; on Windows
    `types` (a function that returns `remote_types(base)` of the engine's log, see `LOG_ENV`) does. With neither, the test
    skips: no name says it."""
    import psutil

    if not sys.platform.startswith("linux") and types is None:
        pytest.skip("only Linux names a content process by the site it hosts; %s names none (pass `types`)" % sys.platform)
    try:
        children = psutil.Process(root_pid).children(recursive=True)
    except psutil.Error:
        return set()
    if sys.platform.startswith("linux"):
        return site_pids(children, site, engine_dir)
    return site_pids_by_type(children, site, types(), engine_dir)
