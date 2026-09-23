"""SQLite-backed search and page cache."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
from collections.abc import Iterable
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from browsr.models import Page, SearchResult


def _url_key(url: str) -> str:
    """Return the cache's stable URL key, matching the project's URL rules."""
    # Import lazily: cache is also useful during startup, before every module is
    # necessarily imported. The common fallback keeps the cache independent.
    try:
        from browsr.urlnorm import normalize

        return normalize(url)
    except (ImportError, AttributeError):
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        host = (parts.hostname or "").encode("idna").decode("ascii").lower()
        port = parts.port
        netloc = host
        default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
        if port is not None and not default_port:
            netloc += f":{port}"
        if parts.username is not None:
            userinfo = parts.username
            if parts.password is not None:
                userinfo += f":{parts.password}"
            netloc = f"{userinfo}@{netloc}"
        return urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))


class Cache:
    """A single-connection cache; all SQLite work runs in worker threads."""

    def __init__(self, cfg: Any):
        self.path = Path(cfg.path).expanduser()
        self.search_ttl_s = int(cfg.search_ttl_s)
        self.page_ttl_s = int(cfg.page_ttl_s)
        self.max_pages = int(cfg.max_pages)
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    async def open(self) -> None:
        await asyncio.to_thread(self._open)

    def _open(self) -> None:
        with self._lock:
            if self._conn is not None:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS search ("
                "key TEXT PRIMARY KEY, backend TEXT, results TEXT NOT NULL, "
                "created INTEGER NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS page ("
                "key TEXT PRIMARY KEY, url TEXT, final_url TEXT, title TEXT, markdown TEXT, "
                "links TEXT, content_type TEXT, created INTEGER NOT NULL)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS search_created ON search(created)")
            conn.execute("CREATE INDEX IF NOT EXISTS page_created ON page(created)")
            conn.commit()
            self._conn = conn

    async def close(self) -> None:
        await asyncio.to_thread(self._close)

    def _close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Cache.open() must be called before using the cache")
        return self._conn

    async def get_search(self, key: str) -> list[SearchResult] | None:
        return await asyncio.to_thread(self._get_search, key)

    def _get_search(self, key: str) -> list[SearchResult] | None:
        with self._lock:
            row = (
                self._connection()
                .execute("SELECT results, created FROM search WHERE key = ?", (key,))
                .fetchone()
            )
            if row is None:
                return None
            results_json, created = row
            if int(created) + self.search_ttl_s < int(time.time()):
                return None
            return [SearchResult(**item) for item in json.loads(results_json)]

    async def put_search(
        self, key: str, results: Iterable[SearchResult], backend: str = ""
    ) -> None:
        payload = [
            asdict(result) if hasattr(result, "__dataclass_fields__") else dict(result)
            for result in results
        ]
        await asyncio.to_thread(self._put_search, key, payload, backend)

    def _put_search(self, key: str, results: list[dict[str, Any]], backend: str) -> None:
        with self._lock:
            self._connection().execute(
                "INSERT OR REPLACE INTO search(key, backend, results, created) VALUES (?, ?, ?, ?)",
                (key, backend, json.dumps(results, ensure_ascii=False), int(time.time())),
            )
            self._connection().commit()

    async def get_page(self, key: str) -> Page | None:
        normalized = _url_key(key)
        return await asyncio.to_thread(self._get_page, normalized)

    def _get_page(self, key: str) -> Page | None:
        with self._lock:
            row = (
                self._connection()
                .execute(
                    "SELECT url, final_url, title, markdown, links, content_type, created "
                    "FROM page WHERE key = ?",
                    (key,),
                )
                .fetchone()
            )
            if row is None:
                return None
            url, final_url, title, markdown, links, content_type, created = row
            if int(created) + self.page_ttl_s < int(time.time()):
                return None
            return Page(
                url=url,
                final_url=final_url,
                title=title,
                markdown=markdown,
                links=json.loads(links),
                content_type=content_type,
                created=int(created),
            )

    async def put_page(self, page: Page, keys: Iterable[str] | None = None) -> None:
        aliases = {_url_key(page.url), _url_key(page.final_url)}
        aliases.update(_url_key(key) for key in (keys or ()))
        await asyncio.to_thread(self._put_page, page, aliases)

    def _put_page(self, page: Page, keys: set[str]) -> None:
        created = int(page.created)
        values = [
            (
                key,
                page.url,
                page.final_url,
                page.title,
                page.markdown,
                json.dumps(page.links, ensure_ascii=False),
                page.content_type,
                created,
            )
            for key in keys
        ]
        with self._lock:
            self._connection().executemany(
                "INSERT OR REPLACE INTO page"
                "(key, url, final_url, title, markdown, links, content_type, created) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                values,
            )
            self._connection().commit()

    async def sweep(self) -> None:
        await asyncio.to_thread(self._sweep)

    def _sweep(self) -> None:
        now = int(time.time())
        with self._lock:
            conn = self._connection()
            conn.execute("DELETE FROM search WHERE created + ? < ?", (self.search_ttl_s, now))
            conn.execute("DELETE FROM page WHERE created + ? < ?", (self.page_ttl_s, now))
            count = conn.execute("SELECT COUNT(*) FROM page").fetchone()[0]
            excess = count - self.max_pages
            if excess > 0:
                conn.execute(
                    "DELETE FROM page WHERE key IN ("
                    "SELECT key FROM page ORDER BY created ASC, key ASC LIMIT ?)",
                    (excess,),
                )
            conn.commit()
