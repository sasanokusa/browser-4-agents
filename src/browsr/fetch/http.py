"""Guarded direct HTTP retrieval for auto mode, fallbacks, and adapters."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser

import httpx

from ..errors import BrowsrError
from ..models import RawPage
from . import detect

_HTML = {"text/html", "application/xhtml+xml"}
_EMPTY_APP = re.compile(
    r'<div\b[^>]*\bid\s*=\s*["\'](?:root|app|__next|__nuxt)["\'][^>]*>\s*</div\s*>',
    re.IGNORECASE,
)
_NOSCRIPT = re.compile(r"<noscript\b[^>]*>(.*?)</noscript\s*>", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True, slots=True)
class HttpResult:
    status: int
    final_url: str
    content_type: str
    body: bytes
    encoding: str


def needs_js(html: str) -> bool:
    if _EMPTY_APP.search(html):
        return True
    return any(
        re.search(r"enable javascript|JavaScript\s*を有効", m.group(1), re.IGNORECASE)
        for m in _NOSCRIPT.finditer(html)
    )


class _VisibleHTML(HTMLParser):
    """Collect page title and visible body text, not markup or script source."""

    _HIDDEN = {"head", "script", "style", "template", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._hidden: list[str] = []
        self._in_title = False
        self._in_body = False
        self._saw_body = False
        self.title_parts: list[str] = []
        self.body_parts: list[str] = []
        self.fallback_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "title":
            self._in_title = True
        if tag == "body":
            self._in_body = True
            self._saw_body = True
        if tag in self._HIDDEN:
            self._hidden.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag == "body":
            self._in_body = False
        if tag in self._HIDDEN and tag in self._hidden:
            self._hidden.remove(tag)

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        if not self._hidden and data.strip():
            self.fallback_parts.append(data)
            if self._in_body:
                self.body_parts.append(data)

    @property
    def title(self) -> str:
        return unescape("".join(self.title_parts).strip())

    @property
    def body_text(self) -> str:
        parts = self.body_parts if self._saw_body else self.fallback_parts
        return " ".join(" ".join(parts).split())


def _visible(html: str) -> _VisibleHTML:
    parser = _VisibleHTML()
    parser.feed(html)
    return parser


class HttpFetcher:
    def __init__(self, cfg, guard, user_agent: str):
        self.cfg = cfg
        self.guard = guard

        async def checked_request(request: httpx.Request) -> None:
            # httpx invokes this before every hop, including redirects.
            await guard.check_url(str(request.url))

        self._client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=cfg.browser.timeout_ms / 1000,
            headers={"User-Agent": user_agent, "Accept-Language": cfg.browser.locale},
            event_hooks={"request": [checked_request]},
        )

    async def request(self, url: str) -> HttpResult:
        """Return bytes and metadata without mapping the HTTP status.

        Adapters use this method so they can apply their own status semantics.
        """
        await self.guard.check_url(url)
        try:
            async with self._client.stream("GET", url) as response:
                status = response.status_code
                content_type = (
                    response.headers.get("content-type", "text/html")
                    .split(";", 1)[0]
                    .strip()
                    .lower()
                )
                try:
                    declared = int(response.headers.get("content-length", "0"))
                except ValueError:
                    declared = 0
                if declared > self.cfg.fetch.max_bytes:
                    raise BrowsrError("unsupported", status=status)
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > self.cfg.fetch.max_bytes:
                        raise BrowsrError("unsupported", status=status)
                return HttpResult(
                    status=status,
                    final_url=str(response.url),
                    content_type=content_type,
                    body=bytes(body),
                    encoding=response.encoding or "utf-8",
                )
        except BrowsrError:
            raise
        except httpx.TimeoutException as exc:
            raise BrowsrError("timeout", detail=str(exc)) from exc
        except httpx.HTTPError as exc:
            raise BrowsrError("fetch_failed", detail=str(exc)) from exc

    async def try_fetch(self, url: str):
        return await self._fetch(url, strict=False)

    async def fetch(self, url: str):
        return await self._fetch(url, strict=True)

    async def _fetch(self, url: str, *, strict: bool):
        response = await self.request(url)
        detect.raise_for_status(response.status)
        body = response.body
        if response.content_type not in _HTML:
            detect.raise_for_challenge(response.status, "", 0, "")
            raw = RawPage(
                url, response.final_url, response.status, response.content_type, body=body
            )
            from ..extract import to_page

            return await asyncio.to_thread(to_page, raw, self.cfg)

        html = self._decode(response)
        visible = _visible(html)
        detect.raise_for_challenge(
            response.status, visible.title, len(visible.body_text), visible.body_text[:1500]
        )
        if not strict and needs_js(html):
            return None
        import trafilatura

        md = await asyncio.to_thread(
            trafilatura.extract,
            html,
            url=response.final_url,
            output_format="markdown",
            include_links=True,
            include_tables=True,
            include_images=False,
            include_comments=False,
        )
        if not md:
            if strict:
                raise BrowsrError("fetch_failed", detail="empty HTTP extraction")
            return None
        if not strict and len(md) < self.cfg.fetch.min_text_chars:
            return None
        raw = RawPage(
            url,
            response.final_url,
            response.status,
            response.content_type,
            title=visible.title,
            text=md,
        )
        from ..extract import to_page

        return await asyncio.to_thread(to_page, raw, self.cfg)

    @staticmethod
    def _decode(response: HttpResult) -> str:
        try:
            return response.body.decode(response.encoding, errors="replace")
        except LookupError:
            return response.body.decode("utf-8", errors="replace")

    async def close(self) -> None:
        await self._client.aclose()
