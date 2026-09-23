import asyncio
import json
from types import SimpleNamespace

import pytest

from browsr.breaker import Breaker
from browsr.cache import Cache
from browsr.calllog import CallLog
from browsr.models import Page, SearchResult
from browsr.ratelimit import RateLimiter


def page(url: str, created: int) -> Page:
    return Page(
        url=url,
        final_url=url,
        title=url,
        markdown=f"content for {url}",
        links=["https://example.test/link"],
        content_type="text/html",
        created=created,
    )


@pytest.mark.asyncio
async def test_cache_search_and_page_ttl_normalization_and_sweep(tmp_path, monkeypatch):
    now = 1_700_000_000
    monkeypatch.setattr("browsr.cache.time.time", lambda: now)
    cache = Cache(
        SimpleNamespace(
            path=str(tmp_path / "cache.sqlite"), search_ttl_s=10, page_ttl_s=10, max_pages=10
        )
    )
    await cache.open()
    try:
        results = [SearchResult("Example", "https://example.test/", "snippet")]
        await cache.put_search("en\x1fquery", results, backend="ddg")
        assert await cache.get_search("en\x1fquery") == results

        p = Page(
            "https://EXAMPLE.test:443/a?utm_source=x#top",
            "https://example.test/a?utm_source=x",
            "A",
            "body",
            [],
            "text/html",
            now,
        )
        await cache.put_page(p)
        # Fragments, host case, default ports and tracking parameters share a key.
        assert await cache.get_page("https://example.test/a#section") == p

        now += 11
        assert await cache.get_search("en\x1fquery") is None
        assert await cache.get_page("https://example.test/a") is None
        await cache.sweep()
        assert await cache.get_page("https://example.test/a") is None
    finally:
        await cache.close()


@pytest.mark.asyncio
async def test_cache_page_aliases_and_oldest_page_eviction(tmp_path, monkeypatch):
    monkeypatch.setattr("browsr.cache.time.time", lambda: 30)
    cache = Cache(
        SimpleNamespace(
            path=str(tmp_path / "cache.sqlite"), search_ttl_s=100, page_ttl_s=100, max_pages=10
        )
    )
    await cache.open()
    try:
        await cache.put_page(
            Page(
                "https://example.test/old",
                "https://example.test/old",
                "old",
                "old body",
                [],
                "text/html",
                10,
            )
        )
        await cache.put_page(
            Page(
                "https://example.test/middle",
                "https://example.test/middle",
                "middle",
                "middle body",
                [],
                "text/html",
                20,
            )
        )
        redirected = Page(
            "https://example.test/redirect",
            "https://example.test/final",
            "new",
            "new body",
            [],
            "text/html",
            30,
        )
        await cache.put_page(redirected, keys=["https://example.test/alias#frag"])
        assert await cache.get_page("https://example.test/final") == redirected
        assert await cache.get_page("https://example.test/alias") == redirected
    finally:
        await cache.close()

    small = Cache(
        SimpleNamespace(
            path=str(tmp_path / "eviction.sqlite"), search_ttl_s=100, page_ttl_s=100, max_pages=2
        )
    )
    await small.open()
    try:
        await small.put_page(page("https://example.test/old", 10))
        await small.put_page(page("https://example.test/middle", 20))
        await small.put_page(page("https://example.test/new", 30))
        await small.sweep()
        assert await small.get_page("https://example.test/old") is None
        assert await small.get_page("https://example.test/middle") is not None
        assert await small.get_page("https://example.test/new") is not None
    finally:
        await small.close()


@pytest.mark.asyncio
async def test_cache_serializes_concurrent_operations(tmp_path):
    cache = Cache(
        SimpleNamespace(
            path=str(tmp_path / "cache.sqlite"), search_ttl_s=100, page_ttl_s=100, max_pages=100
        )
    )
    await cache.open()
    try:
        await asyncio.gather(
            *(
                cache.put_search(f"key-{i}", [SearchResult(str(i), f"https://e.test/{i}", "s")])
                for i in range(30)
            )
        )
        found = await asyncio.gather(*(cache.get_search(f"key-{i}") for i in range(30)))
        assert [items[0].title for items in found if items] == [str(i) for i in range(30)]
    finally:
        await cache.close()


def test_breaker_threshold_cooldown_reset_and_independent_keys(monkeypatch):
    now = 50.0
    monkeypatch.setattr("browsr.breaker.time.monotonic", lambda: now)
    breaker = Breaker(cooldown_s=5, fail_threshold=2)
    breaker.fail("ddg")
    assert breaker.available("ddg")
    breaker.fail("ddg")
    assert not breaker.available("ddg")
    assert breaker.available("mojeek")
    assert breaker.state()["ddg"] == "cooldown"
    now += 5
    assert breaker.available("ddg")
    breaker.fail("ddg")
    breaker.success("ddg")
    breaker.fail("ddg")
    assert breaker.available("ddg")


@pytest.mark.asyncio
async def test_rate_limiter_reserves_slots_for_concurrent_callers(monkeypatch):
    now = 100.0
    sleeps = []

    def monotonic():
        return now

    async def fake_sleep(delay):
        nonlocal now
        sleeps.append(delay)
        now += delay

    monkeypatch.setattr("browsr.ratelimit.time.monotonic", monotonic)
    monkeypatch.setattr("browsr.ratelimit.asyncio.sleep", fake_sleep)
    limiter = RateLimiter()
    outcomes = await asyncio.gather(
        limiter.acquire("same", 2, 0),
        limiter.acquire("same", 2, 1),
        limiter.acquire("same", 2, 5),
        limiter.acquire("other", 2, 0),
    )
    assert outcomes == [True, False, True, True]
    assert sleeps == [2]


@pytest.mark.asyncio
async def test_calllog_appends_json_lines_and_bounds_raw_arguments(tmp_path, capsys):
    log = CallLog(SimpleNamespace(calls_path=str(tmp_path / "nested" / "calls.jsonl")))
    await asyncio.gather(
        *(
            log.write(session=f"s{i}", tool="search", args_raw={"query": "x" * 600}, outcome="ok")
            for i in range(8)
        )
    )
    lines = (tmp_path / "nested" / "calls.jsonl").read_text().splitlines()
    entries = [json.loads(line) for line in lines]
    assert len(entries) == 8
    assert all(len(item["args_raw"]) == 500 for item in entries)
    assert all(item["ts"] and item["tool"] == "search" for item in entries)
    assert capsys.readouterr().out == ""
    await log.close()
