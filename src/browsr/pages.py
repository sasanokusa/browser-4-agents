"""Cached, coalesced page loading with ordered acquisition fallbacks."""

from __future__ import annotations

import asyncio
import importlib.util
import time
from contextvars import ContextVar
from urllib.parse import urlsplit

from . import extract, urlnorm
from .errors import BrowsrError
from .models import Page


class PageLoader:
    def __init__(self, cfg, cache, pool, guard, http):
        from .fetch.adapters import AdapterRegistry

        self.cfg = cfg
        self.cache = cache
        self.pool = pool
        self.guard = guard
        self.http = http
        self.adapters = AdapterRegistry(http)
        self.alt_pool = None
        self._alt_lock = asyncio.Lock()
        self._blocked: dict[str, float] = {}
        self._inflight: dict[str, asyncio.Task[Page]] = {}
        self._cache_hit = ContextVar("page_cache_hit", default=False)
        self._via: ContextVar[str | None] = ContextVar("page_via", default=None)

    @property
    def cache_hit(self) -> bool:
        return self._cache_hit.get()

    @property
    def via(self) -> str | None:
        return self._via.get()

    async def get(self, sid: str, url: str) -> Page:
        self._cache_hit.set(False)
        self._via.set(None)
        key = urlnorm.normalize(url)
        page = await self.cache.get_page(key)
        if page is not None:
            # Cached content must not bypass a changed target policy.
            self._via.set("cache")
            await self.guard.check_url(page.final_url)
            self._cache_hit.set(True)
            return page
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.create_task(self._shared_load(sid, url), name="browsr:page")
            self._inflight[key] = task
            task.add_done_callback(lambda done: self._finished(key, done))
        try:
            # A disconnected client must not cancel other clients' shared fetch.
            return await asyncio.shield(task)
        finally:
            # ContextVar values set inside a Task do not flow back to its waiters.
            self._via.set(getattr(task, "_browsr_via", None))

    async def _shared_load(self, sid: str, url: str) -> Page:
        try:
            return await self._load(sid, url)
        finally:
            task = asyncio.current_task()
            if task is not None:
                task._browsr_via = self.via

    def _finished(self, key: str, task: asyncio.Task[Page]) -> None:
        if self._inflight.get(key) is task:
            del self._inflight[key]
        if not task.cancelled():
            task.exception()  # Retrieve failures even if every waiter left.

    @staticmethod
    def _host(url: str) -> str:
        host = (urlsplit(url).hostname or "").lower()
        return host[4:] if host.startswith("www.") else host

    def _available(self, method: str) -> bool:
        if method == "http":
            return True
        if method == "camoufox" and self.cfg.browser.engine != "camoufox":
            try:
                return importlib.util.find_spec("camoufox") is not None
            except (ImportError, ValueError):
                return False
        return False

    async def _by(self, method: str, sid: str, url: str) -> Page | None:
        if method == "adapter":
            adapter = self.adapters.find(url)
            if adapter is None:
                raise BrowsrError("fetch_failed", detail="adapter no longer available")
            return await adapter.fetch(url)
        if method == "auto":
            return await self.http.try_fetch(url)
        if method == "http":
            return await self.http.fetch(url)
        if method == "browser":
            raw = await self.pool.fetch(sid, url)
        elif method == "camoufox":
            if self.alt_pool is None:
                async with self._alt_lock:
                    if self.alt_pool is None:
                        from .fetch.browser import BrowserPool

                        self.alt_pool = BrowserPool(self.cfg, self.guard, engine="camoufox")
            raw = await self.alt_pool.fetch(sid, url)
        else:
            raise BrowsrError("fetch_failed", detail=f"unknown fetch method: {method}")
        return await asyncio.to_thread(extract.to_page, raw, self.cfg)

    async def _load(self, sid: str, url: str) -> Page:
        await self.guard.before_fetch(url)
        host = self._host(url)
        expiry = self._blocked.get(host, 0)
        if expiry and expiry <= time.monotonic():
            self._blocked.pop(host, None)
        adapter = self.adapters.find(url)
        methods = []
        if adapter is not None:
            methods.append("adapter")
        if self.cfg.fetch.mode == "auto":
            methods.append("auto")
        if host not in self._blocked:
            methods.append("browser")
        methods.extend(method for method in self.cfg.fetch.fallbacks if self._available(method))

        last = BrowsrError("fetch_failed", detail="no fetch method")
        for method in methods:
            self._via.set(method)
            try:
                page = await self._by(method, sid, url)
            except BrowsrError as exc:
                if exc.code not in {"blocked", "fetch_failed"}:
                    raise
                if method == "browser" and exc.code == "blocked":
                    self._blocked[host] = time.monotonic() + self.cfg.fetch.blocked_memory_s
                last = exc
                continue
            except TimeoutError as exc:
                raise BrowsrError("timeout", detail=str(exc)) from exc
            except Exception as exc:
                # An extractor or transport failure is a failed method, so an
                # available fallback still gets a chance to fetch the page.
                last = BrowsrError("fetch_failed", detail=f"{type(exc).__name__}: {exc}")
                continue
            if page is None:
                continue
            await self.guard.check_url(page.final_url)
            await self.cache.put_page(page, keys=[urlnorm.normalize(url)])
            return page
        raise last

    async def drop_context(self, sid: str) -> None:
        if self.alt_pool is not None:
            await self.alt_pool.drop_context(sid)

    async def close(self) -> None:
        tasks = list(self._inflight.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._inflight.clear()
        if self.alt_pool is not None:
            await self.alt_pool.close()
            self.alt_pool = None
