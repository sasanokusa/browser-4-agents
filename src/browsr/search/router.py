from __future__ import annotations

import asyncio
import contextvars
import logging
from collections.abc import Sequence

import httpx

from browsr.errors import BrowsrError
from browsr.models import SearchResult
from browsr.urlnorm import normalize

from .base import Backend, BackendBlocked, BackendTimeout
from .ddg import DdgBackend
from .mojeek import MojeekBackend
from .searxng import SearxngBackend

log = logging.getLogger(__name__)


class SearchRouter:
    """Try configured search providers in order and return the first useful set."""

    def __init__(self, cfg, pool, limiter, breaker) -> None:
        self.cfg = cfg
        self.pool = pool
        self.limiter = limiter
        self.breaker = breaker
        self._last_backend: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            f"browsr_search_backend_{id(self)}", default=None
        )
        self._client = httpx.AsyncClient()
        self._owns_client = True
        self.backends: list[Backend] = []
        for name in cfg.search.backends:
            if name == "searxng":
                self.backends.append(SearxngBackend(cfg, self._client))
            elif name == "ddg":
                self.backends.append(DdgBackend(cfg, pool))
            elif name == "mojeek":
                self.backends.append(MojeekBackend(cfg, pool))
            else:
                log.warning("Ignoring unknown search backend %r", name)
        self._backend_names = tuple(b.name for b in self.backends)

    @property
    def last_backend(self) -> str | None:
        """Name of the provider that answered the current async context, if any."""
        return self._last_backend.get()

    @property
    def backend_states(self) -> dict[str, str]:
        """Breaker health for configured providers, suitable for health output."""
        states = self.breaker.state()
        return {name: states.get(name, "ok") for name in self._backend_names}

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
            self._owns_client = False

    async def search(self, query: str, n: int) -> list[SearchResult]:
        self._last_backend.set(None)
        answered = False
        max_wait_s = self.cfg.search.max_wait_s
        for backend in self.backends:
            name = backend.name
            if not self.breaker.available(name):
                continue
            if not await self.limiter.acquire("search:" + name, backend.interval_s, max_wait_s):
                continue
            try:
                results = await asyncio.wait_for(
                    backend.search(query, n), timeout=backend.timeout_s
                )
            except BackendBlocked:
                self.breaker.trip(name)
                continue
            except (TimeoutError, BackendTimeout):
                self.breaker.fail(name)
                continue
            except Exception:
                log.exception("Search backend %s failed", name)
                self.breaker.fail(name)
                continue
            answered = True
            self.breaker.success(name)
            normalized = self._normalize_results(results)
            self._last_backend.set(name)
            if normalized:
                return normalized[: max(0, n)]
        if answered:
            return []
        raise BrowsrError("search_unavailable")

    @staticmethod
    def _normalize_results(results: Sequence[SearchResult]) -> list[SearchResult]:
        out: list[SearchResult] = []
        seen: set[str] = set()
        for result in results:
            url = str(result.url or "").strip()
            try:
                parts = httpx.URL(url)
            except (TypeError, ValueError):
                continue
            if parts.scheme not in ("http", "https") or not parts.host:
                continue
            try:
                key = normalize(url)
            except (UnicodeError, ValueError):
                continue
            if key in seen:
                continue
            seen.add(key)
            out.append(
                SearchResult(
                    title=str(result.title or "").strip()[:150],
                    url=url,
                    snippet=" ".join(str(result.snippet or "").split()),
                )
            )
        return out
