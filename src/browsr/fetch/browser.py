"""Firefox/Camoufox contexts with guarded navigation and bounded concurrency."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar
from urllib.parse import urljoin

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from ..errors import BrowsrError
from ..models import RawPage
from . import detect
from .guard import Guard
from .proxy import GuardProxy

T = TypeVar("T")
_HTML = {"text/html", "application/xhtml+xml"}


def _mime(header: str) -> str:
    return header.split(";", 1)[0].strip().lower() or "application/octet-stream"


@dataclass
class _Context:
    value: object
    active: int = 0


class BrowserPool:
    def __init__(self, cfg, guard=None):
        self.cfg = cfg
        self._owns_guard = guard is None
        self.guard = guard if guard is not None else Guard(cfg)
        self.user_agent = "browsr"
        self.connected = False
        self._sem = asyncio.Semaphore(cfg.browser.max_tabs)
        self._contexts: OrderedDict[str, _Context] = OrderedDict()
        self._context_condition = asyncio.Condition()
        self._start_lock = asyncio.Lock()
        self._restarts: deque[float] = deque()
        self._started_once = False
        self._pw = None
        self._cm = None
        self._browser = None
        self._proxy = GuardProxy(cfg, self.guard)

    async def start(self) -> None:
        if self.connected:
            return
        async with self._start_lock:
            if self.connected:
                return
            if self._started_once:
                now = time.monotonic()
                while self._restarts and self._restarts[0] < now - 300:
                    self._restarts.popleft()
                if len(self._restarts) >= 3:
                    raise BrowsrError("blocked", detail="browser restart limit reached")
                self._restarts.append(now)
            await self._shutdown()
            try:
                await self._proxy.start()
                if self.cfg.browser.engine == "camoufox":
                    from camoufox.async_api import AsyncCamoufox

                    self._cm = AsyncCamoufox(
                        headless=self.cfg.browser.headless,
                        locale=self.cfg.browser.locale,
                        block_images=True,
                        proxy={"server": self._proxy.server_url},
                    )
                    browser = await self._cm.__aenter__()
                else:
                    self._pw = await async_playwright().start()
                    browser = await self._pw.firefox.launch(
                        headless=self.cfg.browser.headless,
                        firefox_user_prefs={
                            "pdfjs.disabled": True,
                            "media.autoplay.default": 5,
                            "dom.webnotifications.enabled": False,
                            "geo.enabled": False,
                        },
                        proxy={"server": self._proxy.server_url},
                    )
                self._browser = browser
                browser.on("disconnected", lambda: setattr(self, "connected", False))
                probe = await browser.new_page()
                try:
                    self.user_agent = await probe.evaluate("navigator.userAgent")
                finally:
                    await probe.close()
                self.connected = True
                self._started_once = True
            except Exception:
                await self._shutdown()
                raise

    async def _shutdown(self) -> None:
        self.connected = False
        async with self._context_condition:
            entries = list(self._contexts.values())
            self._contexts.clear()
            self._context_condition.notify_all()
        for entry in entries:
            try:
                await entry.value.close()
            except PlaywrightError:
                pass
        if self._browser is not None:
            try:
                await self._browser.close()
            except PlaywrightError:
                pass
            self._browser = None
        if self._cm is not None:
            try:
                await self._cm.__aexit__(None, None, None)
            except PlaywrightError:
                pass
            self._cm = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None
        await self._proxy.close()

    async def close(self) -> None:
        async with self._start_lock:
            await self._shutdown()
        if self._owns_guard:
            await self.guard.close()

    async def _context(self, sid: str) -> _Context:
        await self.start()
        async with self._context_condition:
            while True:
                if sid in self._contexts:
                    entry = self._contexts[sid]
                    entry.active += 1
                    self._contexts.move_to_end(sid)
                    return entry
                if len(self._contexts) < self.cfg.browser.max_contexts:
                    break
                idle = next(
                    ((key, entry) for key, entry in self._contexts.items() if entry.active == 0),
                    None,
                )
                if idle is None:
                    await self._context_condition.wait()
                    continue
                key, entry = idle
                del self._contexts[key]
                await entry.value.close()
                break
            ctx = await self._browser.new_context(
                locale=self.cfg.browser.locale,
                timezone_id=self.cfg.browser.timezone,
                viewport={"width": 1280, "height": 900},
                accept_downloads=False,
            )
            await ctx.route("**/*", self._route)
            entry = _Context(ctx, 1)
            self._contexts[sid] = entry
            return entry

    async def _release(self, entry: _Context) -> None:
        async with self._context_condition:
            entry.active -= 1
            self._context_condition.notify_all()

    async def drop_context(self, sid: str) -> None:
        async with self._context_condition:
            while sid in self._contexts and self._contexts[sid].active:
                await self._context_condition.wait()
            entry = self._contexts.pop(sid, None)
            if entry is not None:
                await entry.value.close()

    async def _route(self, route, forbidden: set[str] | None = None) -> None:
        request = route.request
        if request.resource_type in self.cfg.browser.block:
            await route.abort()
        elif self.guard is not None and not await self.guard.host_allowed(request.url):
            if forbidden is not None:
                forbidden.add(request.url)
            await route.abort("blockedbyclient")
        else:
            await route.continue_()

    async def _page(self, ctx):
        page = await ctx.new_page()
        forbidden: set[str] = set()
        requested: set[str] = set()
        page.on("request", lambda request: requested.add(request.url))

        async def route_handler(route):
            await self._route(route, forbidden)

        await page.route("**/*", route_handler)
        return page, forbidden, requested

    async def _check_requested(self, requested: set[str]) -> None:
        for url in requested:
            if not await self.guard.host_allowed(url):
                raise BrowsrError("forbidden_target")

    async def _goto(self, page, url: str, forbidden: set[str], requested: set[str]):
        try:
            return await page.goto(
                url, wait_until="domcontentloaded", timeout=self.cfg.browser.timeout_ms
            )
        except PlaywrightTimeoutError as exc:
            raise BrowsrError("timeout") from exc
        except PlaywrightError as exc:
            message = str(exc)
            await self._check_requested(requested)
            if forbidden:
                raise BrowsrError("forbidden_target") from exc
            if "Download is starting" in message:
                raise
            if "NS_ERROR_UNKNOWN_HOST" in message or "NS_ERROR_CONNECTION_REFUSED" in message:
                raise BrowsrError("not_found") from exc
            if "NS_ERROR_NET_TIMEOUT" in message:
                raise BrowsrError("timeout") from exc
            raise BrowsrError("blocked", detail=message) from exc

    async def run(
        self, sid: str, url: str, fn: Callable[[object, object | None], Awaitable[T]]
    ) -> T:
        if self.guard is not None:
            await self.guard.check_url(url)
        async with self._sem:
            entry = await self._context(sid)
            page = None
            try:
                page, forbidden, requested = await self._page(entry.value)
                response = await self._goto(page, url, forbidden, requested)
                await self._check_requested(requested)
                if forbidden:
                    raise BrowsrError("forbidden_target")
                result = await fn(page, response)
                await self._check_requested(requested)
                if forbidden:
                    raise BrowsrError("forbidden_target")
                return result
            finally:
                if page is not None:
                    try:
                        await page.close()
                    except PlaywrightError:
                        pass
                await self._release(entry)

    async def fetch(self, sid: str, url: str) -> RawPage:
        if self.guard is not None:
            await self.guard.check_url(url)
        async with self._sem:
            entry = await self._context(sid)
            page = None
            try:
                page, forbidden, requested = await self._page(entry.value)
                try:
                    response = await self._goto(page, url, forbidden, requested)
                except PlaywrightError as exc:
                    if "Download is starting" not in str(exc):
                        raise
                    return await self._fetch_raw(entry.value, url)
                await self._check_requested(requested)
                if forbidden:
                    raise BrowsrError("forbidden_target")
                status = response.status if response else 0
                ctype = (
                    _mime(response.headers.get("content-type", "text/html"))
                    if response
                    else "text/html"
                )
                if response is not None:
                    self._check_declared_length(response.headers, status)
                    if len(await response.body()) > self.cfg.fetch.max_bytes:
                        raise BrowsrError("unsupported", status=status)
                if ctype not in _HTML:
                    return await self._fetch_raw(entry.value, url)
                detect.raise_for_status(status)
                await settle(page, self.cfg.browser.settle_ms)
                await self._check_requested(requested)
                from ..extract import CONSENT_JS, EXTRACT_FN

                if await page.evaluate(CONSENT_JS):
                    await page.wait_for_timeout(300)
                    await settle(page, 1000)
                result = await page.evaluate(
                    EXTRACT_FN, {"minChars": self.cfg.extract.min_readability_chars}
                )
                detect.raise_for_challenge(
                    status, result.get("title", ""), result.get("textLen", 0)
                )
                await self._check_requested(requested)
                if forbidden:
                    raise BrowsrError("forbidden_target")
                return RawPage(
                    url,
                    page.url,
                    status,
                    ctype,
                    html=result.get("html", ""),
                    title=result.get("title", ""),
                )
            finally:
                if page is not None:
                    try:
                        await page.close()
                    except PlaywrightError:
                        pass
                await self._release(entry)

    def _check_declared_length(self, headers, status: int) -> None:
        try:
            size = int(headers.get("content-length", "0"))
        except (TypeError, ValueError):
            return
        if size > self.cfg.fetch.max_bytes:
            raise BrowsrError("unsupported", status=status)

    async def _fetch_raw(self, ctx, url: str) -> RawPage:
        current = url
        for _ in range(11):
            if self.guard is not None:
                await self.guard.check_url(current)
            try:
                response = await ctx.request.get(
                    current, timeout=self.cfg.browser.timeout_ms, max_redirects=0
                )
            except PlaywrightTimeoutError as exc:
                raise BrowsrError("timeout") from exc
            except PlaywrightError as exc:
                raise BrowsrError("blocked", detail=str(exc)) from exc
            status = response.status
            if status in {301, 302, 303, 307, 308} and response.headers.get("location"):
                current = urljoin(current, response.headers["location"])
                await response.dispose()
                continue
            self._check_declared_length(response.headers, status)
            detect.raise_for_status(status)
            detect.raise_for_challenge(status, "", 0)
            body = await response.body()
            if len(body) > self.cfg.fetch.max_bytes:
                raise BrowsrError("unsupported", status=status)
            return RawPage(
                url,
                str(response.url),
                status,
                _mime(response.headers.get("content-type", "application/octet-stream")),
                body=body,
            )
        raise BrowsrError("blocked", detail="too many redirects")


async def settle(page, ms: int) -> None:
    deadline = time.monotonic() + ms / 1000
    previous = -1
    stable = 0
    while time.monotonic() < deadline:
        length = await page.evaluate("document.body ? document.body.innerText.length : 0")
        if length == previous and length > 0:
            stable += 1
            if stable >= 2:
                break
        else:
            stable = 0
        previous = length
        await asyncio.sleep(0.25)
