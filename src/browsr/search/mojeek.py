from __future__ import annotations

from urllib.parse import quote_plus

from browsr.errors import BrowsrError
from browsr.models import SearchResult

from . import selectors
from .base import BackendBlocked, BackendTimeout


def _status(response) -> int:
    return int(getattr(response, "status", 0) or 0)


async def parse(page, response) -> list[SearchResult]:
    status = _status(response)
    if status in (403, 429):
        raise BackendBlocked(f"Mojeek returned HTTP {status}")
    rows = await page.evaluate(
        """({result, title, snippet}) => [...document.querySelectorAll(result)].map((el) => {
          const a = el.querySelector(title);
          const s = el.querySelector(snippet);
          return {title: a?.innerText || a?.textContent || '', href: a?.href || '',
                  snippet: s?.innerText || s?.textContent || ''};
        })""",
        {
            "result": selectors.MOJEEK_RESULT,
            "title": selectors.MOJEEK_TITLE,
            "snippet": selectors.MOJEEK_SNIPPET,
        },
    )
    return [
        SearchResult(
            title=str(row.get("title") or ""),
            url=str(row.get("href") or ""),
            snippet=str(row.get("snippet") or ""),
        )
        for row in rows or []
        if row.get("href")
    ]


class MojeekBackend:
    name = "mojeek"

    def __init__(self, cfg, pool) -> None:
        self.interval_s = cfg.search.mojeek.interval_s
        self.timeout_s = cfg.search.mojeek.timeout_s
        self.pool = pool

    async def search(self, query: str, n: int) -> list[SearchResult]:
        url = f"https://www.mojeek.com/search?q={quote_plus(query)}"
        try:
            return (await self.pool.run("__search__", url, parse))[:n]
        except BrowsrError as exc:
            if exc.code == "blocked":
                raise BackendBlocked from exc
            if exc.code == "timeout":
                raise BackendTimeout from exc
            raise
