"""Application orchestration, session state, and the public call boundary."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import OrderedDict
from contextvars import ContextVar
from dataclasses import replace
from typing import Any

from . import render, urlnorm
from .breaker import Breaker
from .cache import Cache
from .calllog import CallLog
from .chunk import Chunker
from .config import Config
from .errors import ALT_CODES, BrowsrError, hint
from .fetch.browser import BrowserPool
from .fetch.guard import Guard
from .fetch.http import HttpFetcher
from .models import Call, Page
from .normalize import normalize
from .pages import PageLoader
from .ratelimit import RateLimiter
from .search.router import SearchRouter
from .session import Session, SessionStore
from .tokens import estimate

logger = logging.getLogger(__name__)


class Browsr:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.cache = Cache(cfg.cache)
        self.limiter = RateLimiter()
        self.breaker = Breaker(cfg.search.cooldown_s, cfg.search.fail_threshold)
        self.guard = Guard(cfg, self.limiter)
        self.pool = BrowserPool(cfg, self.guard)
        self.sessions = SessionStore(cfg.session)
        self.calllog = CallLog(cfg.log)
        self.http = None
        self.search = None
        self.pages = None
        self._chunks_cache: OrderedDict[tuple, list[str]] = OrderedDict()
        self._maintenance = None
        self._started = False
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._metrics: ContextVar[dict | None] = ContextVar("browsr_call", default=None)

    async def start(self) -> None:
        if self._started:
            return
        try:
            await self.cache.open()
            await self.pool.start()
            self.http = HttpFetcher(self.cfg, self.guard, self.pool.user_agent)
            self.search = SearchRouter(self.cfg, self.pool, self.limiter, self.breaker)
            self.pages = PageLoader(self.cfg, self.cache, self.pool, self.guard, self.http)
            self._started = True
            self._maintenance = asyncio.create_task(self._maintain(), name="browsr:maintenance")
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        self._started = False
        if self._maintenance is not None:
            self._maintenance.cancel()
            await asyncio.gather(self._maintenance, return_exceptions=True)
            self._maintenance = None
        for resource in (
            self.pages,
            self.search,
            self.http,
            self.pool,
            self.guard,
            self.cache,
            self.calllog,
        ):
            if resource is not None:
                try:
                    await resource.close()
                except Exception:
                    logger.exception("Error closing %s", type(resource).__name__)

    async def _maintain(self) -> None:
        count = 0
        while True:
            await asyncio.sleep(60)
            try:
                await self._sweep_sessions()
                count += 1
                if count % 10 == 0:
                    await self.cache.sweep()
            except Exception:
                logger.exception("Maintenance failed")

    async def _sweep_sessions(self) -> None:
        # Refresh active sessions before sweep removes entries, including calls
        # that legitimately take longer than the configured session TTL.
        for sid, lock in self._session_locks.items():
            if lock.locked():
                self.sessions.get(sid)
        for sid in self.sessions.sweep():
            self._session_locks.pop(sid, None)
            await self.pool.drop_context(sid)
            if self.pages is not None:
                await self.pages.drop_context(sid)

    async def call(self, sid: str, tool: str, raw_args: Any) -> dict:
        started = time.monotonic()
        call = None
        session = None
        cancelled = None
        detail = ""
        metrics = {
            "backend": None,
            "cache": False,
            "part": None,
            "via": None,
            "failed_url": None,
        }
        token = self._metrics.set(metrics)
        try:
            call = normalize(tool, raw_args)
            session = self.sessions.get(sid)
            lock = self._session_locks.setdefault(sid, asyncio.Lock())
            async with lock:
                if call.action == "search":
                    out = await self._search(session, call.query)
                else:
                    out = await self._open(session, call)
            outcome = "ok"
        except BrowsrError as exc:
            outcome = exc.code
            detail = exc.detail
            alternative = self._alt(session, exc, metrics["failed_url"])
            out = render.error(exc.code, hint(exc.code, exc.status, alternative))
        except asyncio.CancelledError as exc:
            # A disconnected caller is still a call for evaluation/logging.
            cancelled = exc
            detail = "CancelledError: caller cancelled"
            outcome = "timeout"
            out = render.error(outcome, hint(outcome))
        except Exception as exc:
            logger.exception("Call failed")
            detail = f"{type(exc).__name__}: {exc}"
            outcome = "timeout" if isinstance(exc, TimeoutError) else "fetch_failed"
            alternative = self._alt(session, BrowsrError(outcome), metrics["failed_url"])
            out = render.error(outcome, hint(outcome, None, alternative))
        finally:
            self._metrics.reset(token)
        if session is not None:
            # Keep long calls alive until they finish, not only until they begin.
            session.last_access = time.monotonic()
        try:
            try:
                args_preview = render.dumps(raw_args)[:500]
            except (TypeError, ValueError, RecursionError):
                args_preview = repr(raw_args)[:500]
            await self.calllog.write(
                session=sid,
                tool=tool,
                args_raw=args_preview,
                action=call.action if call else None,
                corrected=call.corrected if call else False,
                outcome=outcome,
                backend=metrics["backend"],
                cache=metrics["cache"],
                ms=round((time.monotonic() - started) * 1000, 3),
                out_tokens=estimate(render.dumps(out)),
                part=metrics["part"],
                via=metrics["via"],
                detail=detail[:300],
            )
        except Exception:
            logger.exception("Could not write call log")
        if cancelled is not None:
            raise cancelled
        return out

    def _alt(
        self, session: Session | None, error: BrowsrError, failed_url: str | None = None
    ) -> int | None:
        if session is None or error.code not in ALT_CODES:
            return None
        failed_key = urlnorm.normalize(failed_url) if failed_url else None
        for ref in session.last_results:
            entry = session.refs.get(ref)
            if entry is not None:
                key = urlnorm.normalize(entry.url)
                if key not in session.opened and key != failed_key:
                    return ref
        return None

    async def _search(self, session: Session, query: str) -> dict:
        metrics = self._metrics.get()
        key = f"{self.cfg.search.language}\x1f{query.lower()}"
        results = await self.cache.get_search(key)
        metrics["cache"] = results is not None
        if results is None:
            results = await self.search.search(query, self.cfg.output.results)
            metrics["backend"] = self.search.last_backend
            if results:
                await self.cache.put_search(key, results, backend=self.search.last_backend or "")
        results = results[: self.cfg.output.results]
        ids = [session.refs.for_url(result.url) for result in results]
        out = render.search(
            query,
            zip(ids, results),
            tip=self.cfg.tools.tip,
            snippet_chars=self.cfg.output.snippet_chars,
        )
        # Search output also observes the envelope budget, including long URLs.
        while out["results"] and estimate(render.dumps(out)) > self.cfg.output.max_tokens:
            out["results"].pop()
        if not out["results"] and self.cfg.tools.tip:
            out["tip"] = "No results. Try different words."
        session.last_results = [result["id"] for result in out["results"]]
        return out

    def _chunks(self, page: Page, session: Session, pid: int) -> list[str]:
        # Use the serialized text cost so escaped newlines/backslashes count.
        # URL style is expanded before splitting because URLs can be very long.
        key = (
            page.final_url,
            page.created,
            self.cfg.output.max_tokens,
            self.cfg.output.envelope_reserve,
            self.cfg.tools.link_style,
            page.title,
            hash(page.markdown),
        )
        if key in self._chunks_cache:
            self._chunks_cache.move_to_end(key)
            return self._chunks_cache[key]
        prepared = page
        if self.cfg.tools.link_style == "url":
            prepared = replace(
                page, markdown=render.links(page.markdown, page.links, session.refs, "url")
            )
        envelope = render.open(9999999, page, "", 9999999, 9999999, 9999999)
        reserve = max(self.cfg.output.envelope_reserve, estimate(render.dumps(envelope)) + 32)
        if reserve >= self.cfg.output.max_tokens - 32:
            raise BrowsrError("unsupported", detail="Page metadata exceeds response budget")
        options = replace(self.cfg.output, envelope_reserve=reserve)

        def cost(text: str) -> int:
            if self.cfg.tools.link_style == "id":
                # Reserve the largest accepted reference width without allocating
                # numbers for links that have not yet been shown to the client.
                text = re.sub(r"\]\(@L\d+\)", "](9999999)", text)
            return estimate(render.dumps(text))

        chunks = Chunker(options, cost_fn=cost).split_cached(prepared)
        self._chunks_cache[key] = chunks
        if len(self._chunks_cache) > 64:
            self._chunks_cache.popitem(last=False)
        return chunks

    async def _open(self, session: Session, call: Call) -> dict:
        metrics = self._metrics.get()
        if call.ref is not None:
            entry = session.refs.get(call.ref)
            if entry is None:
                raise BrowsrError("unknown_id")
            url, part = entry.url, entry.part
        else:
            url, part = call.url, 1
        metrics["failed_url"] = url
        await self.guard.check_url(url)
        try:
            page = await self.pages.get(session.id, url)
        finally:
            metrics["cache"] = self.pages.cache_hit
            metrics["via"] = self.pages.via
        pid = session.refs.for_url(page.final_url)
        session.refs.alias(url, pid)
        chunks = self._chunks(page, session, pid)
        part = min(part, len(chunks))
        text = chunks[part - 1]
        if self.cfg.tools.link_style == "id":
            text = render.links(text, page.links, session.refs, "id")
        nxt = session.refs.for_part(page.final_url, part + 1) if part < len(chunks) else None
        session.opened.update((urlnorm.normalize(url), urlnorm.normalize(page.final_url)))
        metrics["part"] = part
        return render.open(pid, page, text, part, len(chunks), nxt)
