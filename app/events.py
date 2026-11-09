"""In-process event bus for the SSE stream."""

from __future__ import annotations

import asyncio
from typing import Any

from .util import now_iso


class EventBus:
    def __init__(self) -> None:
        self.version = 0
        self._queues: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=32)
        self._queues.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._queues.discard(queue)

    def publish(self, kind: str = "state", **extra: Any) -> dict:
        self.version += 1
        payload = {"version": self.version, "kind": kind, "ts": now_iso(), **extra}
        for queue in list(self._queues):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass
        return payload
