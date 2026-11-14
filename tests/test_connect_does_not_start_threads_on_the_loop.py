"""`InProcessTransport.connect` returns without waiting for any thread to start.

`connect` is a coroutine of the user's event loop, and `Thread.start` does not return until the new thread is running. Measured with
four browsers in one process under CPU load on Windows, the four worker starts held the loop ~200 ms per launch (4.4% of the main
thread's wall time). The workers are now started by a bootstrapper that is not the loop (and is itself begun without a
`Thread.start`), and a message sent right after `connect` waits in the queue until a worker runs.

NO BROWSER HERE: a scripted server behind the real transport, with `Thread.start` slowed to 300 ms, the way a loaded machine slows it.

Known-bad input: workers started inside `connect` -> `connect` takes 4 x 300 ms and fails the 50 ms bound.
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from invisible_playwright._juggler.transport import InProcessTransport

pytestmark = pytest.mark.unit

START_DELAY_S = 0.3


class _Server:
    def attach(self, transport):
        pass

    def handle(self, message):
        return {"echo": message["id"]}

    def shutdown(self):
        pass


def test_connect_returns_before_any_worker_has_started_and_a_message_is_still_answered(monkeypatch):
    original = threading.Thread.start

    def slow_start(self):
        time.sleep(START_DELAY_S)
        return original(self)
    monkeypatch.setattr(threading.Thread, "start", slow_start)

    async def scenario():
        loop = asyncio.get_running_loop()
        transport = InProcessTransport(loop, _Server())
        replies: "asyncio.Queue" = asyncio.Queue()
        transport.on_message = replies.put_nowait

        began = time.perf_counter()
        await transport.connect()
        took = time.perf_counter() - began

        transport.send({"id": 7, "guid": "g", "method": "m", "params": {}})
        reply = await asyncio.wait_for(replies.get(), 10)
        transport.request_stop()
        await asyncio.wait_for(transport.wait_until_stopped(), 10)
        return took, reply

    took, reply = asyncio.run(scenario())
    assert took < 0.05, "connect() waited %.0f ms: a thread was started on the loop" % (took * 1000)
    assert reply == {"id": 7, "result": {"echo": 7}}


def test_a_transport_stopped_before_its_workers_started_ends_without_error(monkeypatch):
    original = threading.Thread.start

    def slow_start(self):
        time.sleep(START_DELAY_S)
        return original(self)
    monkeypatch.setattr(threading.Thread, "start", slow_start)

    async def scenario():
        transport = InProcessTransport(asyncio.get_running_loop(), _Server())
        await transport.connect()
        transport.request_stop()
        await asyncio.wait_for(transport.wait_until_stopped(), 10)

    asyncio.run(scenario())
