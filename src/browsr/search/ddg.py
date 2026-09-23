from __future__ import annotations

from urllib.parse import parse_qs, quote_plus, urljoin, urlsplit

from browsr.errors import BrowsrError
from browsr.models import SearchResult

from . import selectors
from .base import BackendBlocked, BackendTimeout


def _status(response) -> int:
    return int(getattr(response, "status", 0) or 0)


def _unwrap_ddg_url(href: str) -> str:
    parts = urlsplit(href)
    host = (parts.hostname or "").lower()
    if host == "duckduckgo.com" and parts.path == "/l/":
        target = parse_qs(parts.query).get("uddg", [None])[0]
        if target:
            return target
    return href


async def parse(page, response) -> list[SearchResult]:
    if _status(response) in (202, 403, 429):
        raise BackendBlocked(f"DuckDuckGo returned HTTP {_status(response)}")
    rows = await page.evaluate(
        """({result, title, snippet, block}) => {
          if (document.querySelector(block)) return {blocked: true, rows: []};
          return {blocked: false, rows: [...document.querySelectorAll(result)].map((el) => {
            const a = el.querySelector(title);
            const s = el.querySelector(snippet);
            return {title: a?.innerText || a?.textContent || '', href: a?.href || '',
                    snippet: s?.innerText || s?.textContent || ''};
          })};
        }""",
        {
            "result": selectors.DDG_RESULT,
            "title": selectors.DDG_TITLE,
            "snippet": selectors.DDG_SNIPPET,
            "block": selectors.DDG_BLOCK,
        },
    )
    if isinstance(rows, dict) and rows.get("blocked"):
        raise BackendBlocked("DuckDuckGo challenge page detected")
    if isinstance(rows, dict):
        rows = rows.get("rows", [])
    results = []
    for row in rows or []:
        href = _unwrap_ddg_url(str(row.get("href") or ""))
        if href:
            results.append(
                SearchResult(
                    title=str(row.get("title") or ""),
                    url=urljoin(getattr(page, "url", "https://html.duckduckgo.com/"), href),
                    snippet=str(row.get("snippet") or ""),
                )
            )
    return results


class DdgBackend:
    name = "ddg"

    def __init__(self, cfg, pool) -> None:
        self.region = cfg.search.ddg.region
        self.interval_s = cfg.search.ddg.interval_s
        self.timeout_s = cfg.search.ddg.timeout_s
        self.pool = pool

    async def search(self, query: str, n: int) -> list[SearchResult]:
        url = (
            f"https://html.duckduckgo.com/html/?q={quote_plus(query)}&kl={quote_plus(self.region)}"
        )
        try:
            return (await self.pool.run("__search__", url, parse))[:n]
        except BrowsrError as exc:
            if exc.code == "blocked":
                raise BackendBlocked from exc
            if exc.code == "timeout":
                raise BackendTimeout from exc
            raise
