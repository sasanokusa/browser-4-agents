from __future__ import annotations

import httpx

from browsr.models import SearchResult

from .base import BackendBlocked, BackendTimeout


class SearxngBackend:
    name = "searxng"

    def __init__(self, cfg, client: httpx.AsyncClient) -> None:
        self.url = cfg.search.searxng.url.rstrip("/")
        self.language = cfg.search.language
        self.interval_s = cfg.search.searxng.interval_s
        self.timeout_s = cfg.search.searxng.timeout_s
        self.client = client

    async def search(self, query: str, n: int) -> list[SearchResult]:
        try:
            response = await self.client.get(
                f"{self.url}/search",
                params={
                    "q": query,
                    "format": "json",
                    "language": self.language,
                    "safesearch": 0,
                    "pageno": 1,
                },
                timeout=self.timeout_s,
            )
        except httpx.TimeoutException as exc:
            raise BackendTimeout from exc
        if response.status_code in (403, 429):
            raise BackendBlocked(f"SearXNG returned HTTP {response.status_code}")
        if response.status_code != 200:
            raise RuntimeError(f"SearXNG returned HTTP {response.status_code}")
        payload = response.json()
        return [
            SearchResult(
                title=str(item.get("title") or ""),
                url=str(item.get("url") or ""),
                snippet=str(item.get("content") or ""),
            )
            for item in payload.get("results", [])[:n]
            if isinstance(item, dict)
        ]
