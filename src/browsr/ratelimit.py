"""Async interval limiter with reservations made before sleeping."""

from __future__ import annotations

import asyncio
import time


class RateLimiter:
    def __init__(self):
        self._next: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, key: str, interval_s: float, max_wait_s: float) -> bool:
        async with self._lock:
            now = time.monotonic()
            nxt = self._next.get(key, now)
            wait = max(0.0, nxt - now)
            if wait > max_wait_s:
                return False
            self._next[key] = max(now, nxt) + interval_s
        if wait:
            await asyncio.sleep(wait)
        return True
