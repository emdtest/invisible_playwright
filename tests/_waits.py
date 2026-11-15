"""What the e2e tests wait for: a CONDITION with a deadline, and a record of what was seen while waiting.

A test that sleeps a fixed time and then reads the state judges the speed of the machine it runs on: on a loaded
Windows runner the event arrives after the sleep and the test is red, while the product did nothing wrong. A test
that waits until the condition holds (or a deadline that is far beyond any healthy run) is red only when the
condition never holds, and it says what it saw on the way, so a red run tells a slow machine from a lost event.

* :func:`wait_until` and :func:`awatch` poll a condition; ``pump`` is what lets the events of a sync client be
  read (``page.wait_for_timeout``) or a coroutine yield. The deadline is the only clock the verdict depends on, and
  it is the one the test names.
* :class:`Timeline` is a list of events with the time each was heard, relative to a start the test chooses.
* :func:`without_end` says which requests began and never ended.
* :func:`quiet_problem` judges a `networkidle` from the server's own stamps of each request.
* :func:`spare_processes` is the pool of preallocated content processes retail Firefox keeps (see its docstring).
"""
from __future__ import annotations

import time
from collections import Counter
from typing import Any, Callable, Iterable, List, Optional, Tuple


class Watch:
    """The outcome of a wait: ``ok`` (the condition held before the deadline) and ``history``, the sampled value each
    time it changed, as ``(seconds since the wait began, value)``."""

    def __init__(self, ok: bool, history: List[Tuple[float, Any]], took: float) -> None:
        self.ok = ok
        self.history = history
        self.took = took

    def render(self) -> str:
        return "; ".join("+%.2fs %r" % (at, value) for at, value in self.history) or "nothing sampled"


def wait_until(condition: Callable[[], Any], seconds: float, pump: Callable[[], Any],
               clock: Callable[[], float] = time.monotonic) -> bool:
    """Until ``condition()`` is true, calling ``pump()`` between two looks; False if ``seconds`` pass first."""
    return watch(lambda: bool(condition()), lambda value: value, seconds, pump, clock).ok


def watch(sample: Callable[[], Any], done: Callable[[Any], Any], seconds: float, pump: Callable[[], Any],
          clock: Callable[[], float] = time.monotonic) -> Watch:
    """Until ``done(sample())``, calling ``pump()`` between two looks. The deadline is ``seconds`` from the start; the
    last look is taken AT the deadline, so a condition that became true during the last pump is not missed."""
    began = clock()
    history: List[Tuple[float, Any]] = []
    while True:
        value = sample()
        now = clock()
        if not history or history[-1][1] != value:
            history.append((now - began, value))
        if done(value):
            return Watch(True, history, now - began)
        if now - began >= seconds:
            return Watch(False, history, now - began)
        pump()


async def awatch(sample: Callable[[], Any], done: Callable[[Any], Any], seconds: float,
                 pump: Callable[[], Any], clock: Callable[[], float] = time.monotonic) -> Watch:
    """:func:`watch` for an async client: ``pump`` is awaited."""
    began = clock()
    history: List[Tuple[float, Any]] = []
    while True:
        value = sample()
        now = clock()
        if not history or history[-1][1] != value:
            history.append((now - began, value))
        if done(value):
            return Watch(True, history, now - began)
        if now - began >= seconds:
            return Watch(False, history, now - began)
        await pump()


class Timeline:
    """Events with the time each was heard, relative to ``start`` (set by the test just before the action)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self.start = clock()
        self.events: List[Tuple[float, str, str]] = []

    def begin(self) -> None:
        self.start = self._clock()

    def add(self, kind: str, what: str) -> None:
        self.events.append((self._clock() - self.start, kind, what))

    def of(self, *kinds: str) -> List[str]:
        return [what for _at, kind, what in self.events if kind in kinds]

    def render(self) -> str:
        return "\n".join("  +%.3fs %-9s %s" % (at, kind, what) for at, kind, what in sorted(self.events)) or "  no event"


def without_end(started: Iterable[str], ended: Iterable[str]) -> List[str]:
    """The requests that began and never ended: each url as many times as it began more than it ended."""
    return sorted((Counter(started) - Counter(ended)).elements())


def spare_processes(total_memory_bytes: Optional[int], lowmem_mb: int = 4096, fission_number: int = 3) -> int:
    """How many preallocated content processes retail Firefox keeps beside the ones that host pages.

    With Fission on (the wrapper's strategy 1), `PreallocatedProcessManagerImpl::RereadPrefs`
    (dom/ipc/PreallocatedProcessManager.cpp) keeps a pool of `dom.ipc.processPrelaunch.fission.number` processes
    (StaticPrefList.yaml: 3 on desktop), and 1 on a machine whose memory is at or under
    `dom.ipc.processPrelaunch.lowmem_mb` (4096 MB). The pool is refilled one process at a time, each after
    `dom.ipc.processPrelaunch.delayMs` (1000 ms) and when the loop is idle, so how many of them exist at the moment a
    test looks depends on how long ago the last page took one."""
    if total_memory_bytes and total_memory_bytes <= lowmem_mb * 1024 * 1024:
        return 1
    return fission_number


def quiet_problem(stamps: Iterable[Tuple[str, float, Optional[float]]], returned: float, quiet: float) -> Optional[str]:
    """What is wrong with a `networkidle` that came at ``returned`` (None if nothing), from ``(path, arrival, end)``
    stamps of the requests of the page on the server's clock (``end`` None: not answered yet).

    `networkidle` promises that no request is in flight and that half a second has passed since the last one ended.
    Asked here for ``quiet`` (a little under the half second, because the client hears of an end a moment after the
    server writes it). A late request that has not even arrived is no reason to complain when the network really was
    quiet before ``returned``, and a request in flight, or an end too near, is."""
    stamps = list(stamps)
    in_flight = [path for path, _arrival, end in stamps if end is None or end > returned]
    if in_flight:
        return "networkidle came while %s was in flight" % in_flight
    if not stamps:
        return "no request of the page was seen at all"
    since = returned - max(end for _path, _arrival, end in stamps)
    if since < quiet:
        return "networkidle came after only %.3f s without network (it promises 0.5)" % since
    return None
