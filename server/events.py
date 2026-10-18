"""In-process publish / subscribe, bridged to the browser with server-sent events.

Background threads (the job runner, the live service) publish; each browser tab
holds an SSE connection per channel and receives JSON events.
"""
from __future__ import annotations

import asyncio
import json
import threading


class Broker:
    def __init__(self):
        self.loop: asyncio.AbstractEventLoop | None = None
        self.subs: dict[str, set[asyncio.Queue]] = {}
        self.lock = threading.Lock()

    def bind(self, loop):
        self.loop = loop

    def publish(self, channel: str, event: dict):
        if self.loop is None:
            return
        data = json.dumps(event, default=str)
        with self.lock:
            queues = list(self.subs.get(channel, ()))
        for q in queues:
            self.loop.call_soon_threadsafe(self._put, q, data)

    @staticmethod
    def _put(q: asyncio.Queue, data: str):
        if q.qsize() > 5000:  # a stalled tab must not grow memory without bound
            return
        q.put_nowait(data)

    async def stream(self, channel: str):
        q: asyncio.Queue = asyncio.Queue()
        with self.lock:
            self.subs.setdefault(channel, set()).add(q)
        try:
            yield "retry: 2000\n\n"
            while True:
                try:
                    data = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {data}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            with self.lock:
                self.subs.get(channel, set()).discard(q)


broker = Broker()
