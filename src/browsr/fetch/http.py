"""Fast HTTP path used before opening a browser in auto mode."""

from __future__ import annotations

import asyncio
import re
from html import unescape
from html.parser import HTMLParser

import httpx

from ..errors import BrowsrError
from ..models import RawPage
from . import detect

_EMPTY_APP = re.compile(
    r'<div\b[^>]*\bid\s*=\s*["\'](?:root|app|__next|__nuxt)["\'][^>]*>\s*</div\s*>',
    re.IGNORECASE,
)
_NOSCRIPT = re.compile(r"<noscript\b[^>]*>(.*?)</noscript\s*>", re.IGNORECASE | re.DOTALL)


def needs_js(html: str) -> bool:
    if _EMPTY_APP.search(html):
        return True
    return any(
        re.search(r"enable javascript|JavaScript\s*を有効", m.group(1), re.IGNORECASE)
        for m in _NOSCRIPT.finditer(html)
    )


class _Title(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_title = False
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self.in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.parts.append(data)


def _title(html: str) -> str:
    parser = _Title()
    parser.feed(html)
    return unescape("".join(parser.parts).strip())


class HttpFetcher:
    def __init__(self, cfg, guard, user_agent: str):
        self.cfg = cfg
        self.guard = guard

        async def checked_request(request: httpx.Request) -> None:
            # httpx invokes this for every hop, including redirects.
            await guard.check_url(str(request.url))

        self._client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=cfg.browser.timeout_ms / 1000,
            headers={"User-Agent": user_agent, "Accept-Language": cfg.browser.locale},
            event_hooks={"request": [checked_request]},
        )

    async def try_fetch(self, url: str):
        await self.guard.check_url(url)
        try:
            async with self._client.stream("GET", url) as response:
                status = response.status_code
                ctype = (
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
                final_url = str(response.url)
                encoding = response.encoding or "utf-8"
        except BrowsrError:
            raise
        except httpx.TimeoutException as exc:
            raise BrowsrError("timeout") from exc
        except httpx.HTTPError:
            # A browser may still be able to load sites that reject plain HTTP clients.
            return None
        detect.raise_for_status(status)
        if ctype not in {"text/html", "application/xhtml+xml"}:
            detect.raise_for_challenge(status, "", 0)
            raw = RawPage(url, final_url, status, ctype, body=bytes(body))
            from ..extract import to_page

            return await asyncio.to_thread(to_page, raw, self.cfg)
        html = body.decode(encoding, errors="replace")
        title = _title(html)
        detect.raise_for_challenge(status, title, len(html))
        if needs_js(html):
            return None
        import trafilatura

        md = await asyncio.to_thread(
            trafilatura.extract,
            html,
            url=final_url,
            output_format="markdown",
            include_links=True,
            include_tables=True,
            include_images=False,
            include_comments=False,
        )
        if not md or len(md) < self.cfg.fetch.min_text_chars:
            return None
        raw = RawPage(url, final_url, status, ctype, title=title, text=md)
        from ..extract import to_page

        return await asyncio.to_thread(to_page, raw, self.cfg)

    async def close(self) -> None:
        await self._client.aclose()
