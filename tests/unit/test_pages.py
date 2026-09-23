"""Ordered page acquisition, shared tasks, and caller-local load metrics."""

import asyncio
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from browsr.config import Config
from browsr.errors import BrowsrError
from browsr.models import Page, RawPage
from browsr.pages import PageLoader

URL = "https://example.org/article"


def make_page(url=URL):
    return Page(url, url, "Title", "Body", [], "text/html", int(time.time()))


def make_loader(*, mode="browser", fallbacks=None, engine="firefox", cached=None):
    cfg = Config()
    cfg = replace(
        cfg,
        fetch=replace(cfg.fetch, mode=mode, fallbacks=fallbacks if fallbacks is not None else []),
        browser=replace(cfg.browser, engine=engine),
    )
    cache = SimpleNamespace(get_page=AsyncMock(return_value=cached), put_page=AsyncMock())
    guard = SimpleNamespace(before_fetch=AsyncMock(), check_url=AsyncMock())
    pool = SimpleNamespace(fetch=AsyncMock(), drop_context=AsyncMock())
    http = SimpleNamespace(try_fetch=AsyncMock(), fetch=AsyncMock())
    loader = PageLoader(cfg, cache, pool, guard, http)
    return loader, cache, guard, pool, http


async def test_order_adapter_auto_browser_then_configured_fallbacks(monkeypatch):
    loader, cache, guard, _, _ = make_loader(mode="auto", fallbacks=["http", "camoufox"])
    loader.adapters = SimpleNamespace(find=lambda url: object())
    monkeypatch.setattr(loader, "_available", lambda method: True)
    methods = []

    async def by(method, sid, url):
        methods.append(method)
        if method == "auto":
            return None
        if method == "camoufox":
            return make_page()
        raise BrowsrError("blocked")

    monkeypatch.setattr(loader, "_by", by)
    assert await loader.get("one", URL) == make_page()
    assert methods == ["adapter", "auto", "browser", "http", "camoufox"]
    assert loader.via == "camoufox"
    assert not loader.cache_hit
    guard.before_fetch.assert_awaited_once_with(URL)
    cache.put_page.assert_awaited_once()


@pytest.mark.parametrize("code", ["not_found", "timeout", "forbidden_target"])
async def test_non_retryable_error_stops_at_failed_method(monkeypatch, code):
    loader, cache, _, _, _ = make_loader(fallbacks=["http"])
    methods = []

    async def by(method, sid, url):
        methods.append(method)
        raise BrowsrError(code)

    monkeypatch.setattr(loader, "_by", by)
    with pytest.raises(BrowsrError) as caught:
        await loader.get("one", URL)
    assert caught.value.code == code
    assert methods == ["browser"]
    assert loader.via == "browser"
    cache.put_page.assert_not_awaited()


async def test_last_failure_and_method_via_are_returned(monkeypatch):
    loader, _, _, _, _ = make_loader(fallbacks=["http"])
    methods = []

    async def by(method, sid, url):
        methods.append(method)
        raise BrowsrError("blocked" if method == "browser" else "fetch_failed", detail=method)

    monkeypatch.setattr(loader, "_by", by)
    with pytest.raises(BrowsrError) as caught:
        await loader.get("one", URL)
    assert (caught.value.code, caught.value.detail) == ("fetch_failed", "http")
    assert methods == ["browser", "http"]
    assert loader.via == "http"


async def test_configured_fallback_order_is_respected(monkeypatch):
    loader, _, _, _, _ = make_loader(fallbacks=["camoufox", "http"])
    monkeypatch.setattr(loader, "_available", lambda method: True)
    methods = []

    async def by(method, sid, url):
        methods.append(method)
        if method == "http":
            return make_page()
        raise BrowsrError("fetch_failed")

    monkeypatch.setattr(loader, "_by", by)
    assert await loader.get("one", URL) == make_page()
    assert methods == ["browser", "camoufox", "http"]
    assert loader.via == "http"


async def test_no_available_method_returns_fetch_failed():
    loader, _, _, _, _ = make_loader(fallbacks=["camoufox"], engine="camoufox")
    loader._blocked["example.org"] = float("inf")
    with pytest.raises(BrowsrError) as caught:
        await loader.get("one", URL)
    assert (caught.value.code, caught.value.detail) == ("fetch_failed", "no fetch method")
    assert loader.via is None


async def test_browser_block_memory_normalizes_www_and_expires(monkeypatch):
    loader, _, _, _, _ = make_loader(fallbacks=["http"])
    clock = [100.0]
    monkeypatch.setattr("browsr.pages.time.monotonic", lambda: clock[0])
    methods = []

    async def by(method, sid, url):
        methods.append(method)
        if method == "browser":
            raise BrowsrError("blocked")
        return make_page(url)

    monkeypatch.setattr(loader, "_by", by)
    await loader.get("one", URL)
    assert methods == ["browser", "http"]
    methods.clear()
    await loader.get("two", "https://www.example.org/other")
    assert methods == ["http"]
    methods.clear()
    clock[0] += loader.cfg.fetch.blocked_memory_s + 1
    await loader.get("three", "https://example.org/new")
    assert methods == ["browser", "http"]


async def test_browser_fetch_failure_does_not_enter_block_memory(monkeypatch):
    loader, _, _, _, _ = make_loader(fallbacks=["http"])
    methods = []

    async def by(method, sid, url):
        methods.append(method)
        if method == "browser":
            raise BrowsrError("fetch_failed")
        return make_page(url)

    monkeypatch.setattr(loader, "_by", by)
    await loader.get("one", URL)
    await loader.get("two", "https://www.example.org/other")
    assert methods == ["browser", "http", "browser", "http"]
    assert loader._blocked == {}


async def test_via_cache_hit_and_concurrent_waiters(monkeypatch):
    loader, cache, _, _, _ = make_loader(mode="auto")
    started = asyncio.Event()
    release = asyncio.Event()

    async def by(method, sid, url):
        started.set()
        await release.wait()
        return make_page()

    monkeypatch.setattr(loader, "_by", by)

    async def caller(sid):
        page = await loader.get(sid, URL)
        return page, loader.via, loader.cache_hit

    first = asyncio.create_task(caller("first"))
    await started.wait()
    second = asyncio.create_task(caller("second"))
    await asyncio.sleep(0)
    release.set()
    assert await first == (make_page(), "auto", False)
    assert await second == (make_page(), "auto", False)
    cache.get_page.return_value = make_page()
    assert await caller("third") == (make_page(), "cache", True)


async def test_failure_via_reaches_each_concurrent_waiter(monkeypatch):
    loader, _, _, _, _ = make_loader(fallbacks=["http"])
    started = asyncio.Event()
    release = asyncio.Event()

    async def by(method, sid, url):
        if method == "browser":
            started.set()
            await release.wait()
        raise BrowsrError("fetch_failed", detail=method)

    monkeypatch.setattr(loader, "_by", by)

    async def caller(sid):
        with pytest.raises(BrowsrError) as caught:
            await loader.get(sid, URL)
        return caught.value.detail, loader.via

    first = asyncio.create_task(caller("first"))
    await started.wait()
    second = asyncio.create_task(caller("second"))
    await asyncio.sleep(0)
    release.set()
    assert await first == ("http", "http")
    assert await second == ("http", "http")


async def test_via_is_isolated_between_concurrent_urls(monkeypatch):
    loader, _, _, _, _ = make_loader(fallbacks=["http"])
    entered = set()
    release = asyncio.Event()

    async def by(method, sid, url):
        entered.add(url)
        if len(entered) == 2:
            release.set()
        await release.wait()
        if url == URL:
            if method == "browser":
                raise BrowsrError("blocked")
            return make_page(url)
        raise BrowsrError("timeout")

    monkeypatch.setattr(loader, "_by", by)

    async def caller(url):
        try:
            await loader.get(url, url)
        except BrowsrError as exc:
            return exc.code, loader.via
        return "ok", loader.via

    assert await asyncio.gather(caller(URL), caller("https://other.example.org/elsewhere")) == [
        ("ok", "http"),
        ("timeout", "browser"),
    ]


async def test_internal_error_retries_but_builtin_timeout_stops(monkeypatch):
    loader, _, _, _, _ = make_loader(fallbacks=["http"])
    methods = []

    async def by(method, sid, url):
        methods.append(method)
        if method == "browser":
            raise RuntimeError("extractor failed")
        return make_page()

    monkeypatch.setattr(loader, "_by", by)
    assert await loader.get("one", URL) == make_page()
    assert methods == ["browser", "http"]
    assert loader.via == "http"

    async def timed_out(method, sid, url):
        methods.append(method)
        raise TimeoutError("late")

    monkeypatch.setattr(loader, "_by", timed_out)
    methods.clear()
    with pytest.raises(BrowsrError, match="late") as caught:
        await loader.get("two", "https://example.org/second")
    assert caught.value.code == "timeout"
    assert methods == ["browser"]


async def test_internal_error_keeps_type_in_last_failure(monkeypatch):
    loader, _, _, _, _ = make_loader()

    async def broken(method, sid, url):
        raise RuntimeError("extractor failed")

    monkeypatch.setattr(loader, "_by", broken)
    with pytest.raises(BrowsrError) as caught:
        await loader.get("one", URL)
    assert caught.value.code == "fetch_failed"
    assert caught.value.detail == "RuntimeError: extractor failed"
    assert loader.via == "browser"


async def test_alt_pool_is_lazy_and_closed_with_contexts(monkeypatch):
    loader, _, _, _, _ = make_loader(fallbacks=["camoufox"])
    alternate = SimpleNamespace(
        fetch=AsyncMock(return_value=RawPage(URL, URL, 200, "text/html")),
        drop_context=AsyncMock(),
        close=AsyncMock(),
    )
    constructed = []

    def factory(cfg, guard, *, engine):
        constructed.append(engine)
        return alternate

    monkeypatch.setattr("browsr.fetch.browser.BrowserPool", factory)
    monkeypatch.setattr("browsr.pages.extract.to_page", lambda raw, cfg: make_page())
    assert loader.alt_pool is None
    await loader._by("camoufox", "one", URL)
    assert constructed == ["camoufox"]
    await loader.drop_context("one")
    alternate.drop_context.assert_awaited_once_with("one")
    await loader.close()
    alternate.close.assert_awaited_once()
    assert loader.alt_pool is None


async def test_camoufox_unavailable_or_primary_engine_skips_alternate(monkeypatch):
    loader, _, _, _, _ = make_loader(fallbacks=["camoufox"])
    monkeypatch.setattr("browsr.pages.importlib.util.find_spec", lambda name: None)
    assert not loader._available("camoufox")
    loader.cfg = replace(loader.cfg, browser=replace(loader.cfg.browser, engine="camoufox"))
    monkeypatch.setattr("browsr.pages.importlib.util.find_spec", lambda name: object())
    assert not loader._available("camoufox")
