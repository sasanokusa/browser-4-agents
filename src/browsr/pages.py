"""Cached, coalesced page loading shared by every interface."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar

from . import extract, urlnorm
from .models import Page


class PageLoader:
    def __init__(self, cfg, cache, pool, guard, http):
        self.cfg = cfg
        self.cache = cache
        self.pool = pool
        self.guard = guard
        self.http = http
        self._inflight: dict[str, asyncio.Task[Page]] = {}
        self._cache_hit = ContextVar("page_cache_hit", default=False)

    @property
    def cache_hit(self) -> bool:
        return self._cache_hit.get()

    async def get(self, sid: str, url: str) -> Page:
        self._cache_hit.set(False)
        key = urlnorm.normalize(url)
        page = await self.cache.get_page(key)
        if page is not None:
            # Cached content must not bypass a changed target policy.
            await self.guard.check_url(page.final_url)
            self._cache_hit.set(True)
            return page
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.create_task(self._load(sid, url), name="browsr:page")
            self._inflight[key] = task
            task.add_done_callback(lambda done: self._finished(key, done))
        # A disconnected client must not cancel other clients' shared fetch.
        return await asyncio.shield(task)

    def _finished(self, key: str, task: asyncio.Task[Page]) -> None:
        if self._inflight.get(key) is task:
            del self._inflight[key]
        if not task.cancelled():
            task.exception()  # Retrieve failures even if every waiter left.

    async def _load(self, sid: str, url: str) -> Page:
        await self.guard.before_fetch(url)
        page = None
        if self.cfg.fetch.mode == "auto":
            page = await self.http.try_fetch(url)
        if page is None:
            raw = await self.pool.fetch(sid, url)
            page = await asyncio.to_thread(extract.to_page, raw, self.cfg)
        await self.guard.check_url(page.final_url)
        await self.cache.put_page(page, keys=[urlnorm.normalize(url)])
        return page

    async def close(self) -> None:
        tasks = list(self._inflight.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._inflight.clear()
