from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

from .errors import hint as error_hint
from .models import Page, SearchResult


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def search(
    query: str,
    items: Iterable[tuple[int, SearchResult] | SearchResult],
    tip: bool = True,
    snippet_chars: int = 200,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for fallback_id, item in enumerate(items, 1):
        if isinstance(item, tuple):
            ref, result = item
        else:
            ref, result = fallback_id, item
        snippet = re.sub(r"\s+", " ", result.snippet).strip()
        if len(snippet) > snippet_chars:
            snippet = snippet[: max(0, snippet_chars)] + "…"
        results.append(
            {
                "id": int(ref),
                "title": result.title.strip()[:150],
                "url": result.url,
                "snippet": snippet,
            }
        )
    out: dict[str, Any] = {"query": query, "results": results}
    if tip:
        out["tip"] = (
            f"open({results[0]['id']}) to read a result"
            if results
            else "No results. Try different words."
        )
    return out


def _links(markdown: str, urls: list[str], refs: RefTableLike, style: str) -> str:
    def replace(match: re.Match[str]) -> str:
        index = int(match.group(1))
        if index < 0 or index >= len(urls):
            return match.group(0)
        url = urls[index]
        destination = url if style == "url" else str(refs.for_url(url))
        return f"]({destination})"

    return re.sub(r"\]\(@L(\d+)\)", replace, markdown)


class RefTableLike:
    def for_url(self, url: str) -> int: ...


def links(markdown: str, urls: list[str] | Page, refs: RefTableLike, style: str = "id") -> str:
    if isinstance(urls, Page):
        urls = urls.links
    return _links(markdown, urls, refs, style)


def open(
    ref: int,
    page: Page,
    text: str,
    part: int = 1,
    total: int = 1,
    next: int | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {"id": ref, "title": page.title, "url": page.final_url, "text": text}
    if total > 1:
        out["part"] = f"{part}/{total}"
    if next is not None:
        out["next"] = next
    return out


def error(
    code: str,
    message: str | None = None,
    *,
    status: int | None = None,
    alt_id: int | None = None,
) -> dict[str, Any]:
    return {
        "error": code,
        "hint": message if message is not None else error_hint(code, status, alt_id),
    }
