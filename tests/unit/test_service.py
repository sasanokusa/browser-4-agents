import asyncio
import json
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from browsr.config import Config
from browsr.errors import BrowsrError
from browsr.models import Page, SearchResult
from browsr.pages import PageLoader
from browsr.render import dumps
from browsr.service import Browsr
from browsr.tokens import estimate


def config(tmp_path, **kwargs):
    cfg = Config()
    return replace(
        cfg,
        cache=replace(cfg.cache, path=str(tmp_path / "cache.sqlite")),
        log=replace(cfg.log, calls_path=str(tmp_path / "calls.jsonl")),
        **kwargs,
    )


@pytest.fixture
async def app(tmp_path):
    service = Browsr(config(tmp_path))
    await service.cache.open()
    service.guard.check_url = AsyncMock()
    service.search = SimpleNamespace(
        search=AsyncMock(
            return_value=[
                SearchResult("First", "https://example.org/a", "A page"),
                SearchResult("Second", "https://example.org/b", "Another page"),
            ]
        ),
        last_backend="fake",
        close=AsyncMock(),
    )
    service.pages = SimpleNamespace(
        get=AsyncMock(), cache_hit=False, via="browser", close=AsyncMock(), drop_context=AsyncMock()
    )
    yield service
    await service.close()


def make_page(markdown="Some text", links=None):
    return Page(
        "https://example.org/a",
        "https://example.org/a",
        "A title",
        markdown,
        links or [],
        "text/html",
        int(time.time()),
    )


async def test_search_open_next_and_session_isolation(app, tmp_path):
    app.pages.get.return_value = make_page(
        "\n\n".join(f"Section {i}. " + "日本語の文章。" * 80 for i in range(30))
    )
    results = await app.call("one", "search", {"q": "test"})
    assert results["results"][0]["id"] == 1
    first = await app.call("one", "open", {"url": "1"})
    assert first["part"].startswith("1/") and first["next"] > 2
    second = await app.call("one", "open", {"url": first["next"]})
    assert second["part"].startswith("2/")
    assert first["text"] != second["text"]
    assert (await app.call("other", "open", {"url": "1"}))["error"] == "unknown_id"
    await app.call("other", "search", {"query": "test"})
    assert app.search.search.await_count == 1
    records = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert len(records) == 5
    assert records[0]["corrected"] and records[-1]["cache"]
    assert all("ms" in record and "out_tokens" in record for record in records)


async def test_error_alternative_excludes_failed_and_opened(app):
    await app.call("one", "search", {"query": "test"})
    app.pages.get.side_effect = BrowsrError("timeout")
    out = await app.call("one", "open", {"url": "1"})
    assert "open(2)" in out["hint"]
    assert (await app.call("one", "unknown", {}))["error"] == "bad_input"


@pytest.mark.parametrize(
    ("failure", "code", "detail"),
    [
        (
            RuntimeError("Set changed size during iteration"),
            "fetch_failed",
            "RuntimeError: Set changed size during iteration",
        ),
        (TimeoutError("timed out"), "timeout", "TimeoutError: timed out"),
        (
            BrowsrError("blocked", status=403, detail="challenge title: Client Challenge"),
            "blocked",
            "challenge title: Client Challenge",
        ),
        (
            BrowsrError("fetch_failed", detail="socket connection reset"),
            "fetch_failed",
            "socket connection reset",
        ),
    ],
)
async def test_failure_classification_detail_and_via(app, tmp_path, failure, code, detail):
    await app.call("one", "search", {"query": "test"})
    app.pages.get.side_effect = failure
    app.pages.via = "http"
    out = await app.call("one", "open", {"url": "1"})
    assert out["error"] == code
    assert "open(2)" in out["hint"]
    assert set(out) == {"error", "hint"}
    assert detail not in dumps(out)
    record = json.loads((tmp_path / "calls.jsonl").read_text().splitlines()[-1])
    assert record["outcome"] == code
    assert record["detail"] == detail
    assert record["via"] == "http"
    assert record["cache"] is False


async def test_log_detail_is_bounded_and_metrics_reset_between_calls(app, tmp_path):
    app.pages.get.side_effect = BrowsrError("fetch_failed", detail="詳細" * 200)
    await app.call("one", "open", {"url": "https://example.org/a"})
    await app.call("one", "search", {"query": "test"})
    records = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert len(records[0]["detail"]) == 300
    assert records[0]["via"] == "browser"
    assert records[1]["detail"] == ""
    assert records[1]["via"] is None


async def test_forbidden_target_does_not_reuse_previous_fetch_metrics(app, tmp_path):
    app.pages.get.return_value = make_page()
    await app.call("one", "open", {"url": "https://example.org/a"})
    app.guard.check_url.side_effect = BrowsrError("forbidden_target")
    out = await app.call("one", "open", {"url": "http://localhost/secret"})
    assert out["error"] == "forbidden_target"
    assert app.pages.get.await_count == 1
    record = json.loads((tmp_path / "calls.jsonl").read_text().splitlines()[-1])
    assert record["via"] is None
    assert record["cache"] is False


@pytest.mark.parametrize("style", ["id", "url"])
async def test_serialized_response_budget(app, style):
    app.cfg = replace(
        app.cfg,
        tools=replace(app.cfg.tools, link_style=style),
        output=replace(app.cfg.output, max_tokens=1000),
    )
    app.pages.get.return_value = make_page(
        "\n\n".join('日本語と引用符 " \\ ' * 25 + " [link](@L0)" for _ in range(40)),
        ["https://example.org/" + "long/" * 30],
    )
    args = {"url": "https://example.org/a"}
    parts = []
    for _ in range(100):
        out = await app.call("one", "open", args)
        assert "error" not in out
        assert estimate(dumps(out)) <= 1000
        parts.append(out["text"])
        if "next" not in out:
            break
        args = {"url": out["next"]}
    else:
        pytest.fail("Pagination failed to terminate")
    assert len(parts) > 1
    assert "@L0" not in "".join(parts)


async def test_page_singleflight_survives_one_waiter_cancellation(tmp_path):
    started = asyncio.Event()
    release = asyncio.Event()
    page = make_page()

    async def fetch(url):
        started.set()
        await release.wait()
        return page

    cfg = config(tmp_path, fetch=replace(Config().fetch, mode="auto"))
    cache = SimpleNamespace(get_page=AsyncMock(return_value=None), put_page=AsyncMock())
    guard = SimpleNamespace(before_fetch=AsyncMock(), check_url=AsyncMock())
    http = SimpleNamespace(try_fetch=AsyncMock(side_effect=fetch))
    loader = PageLoader(cfg, cache, None, guard, http)
    first = asyncio.create_task(loader.get("a", page.url))
    await started.wait()
    second = asyncio.create_task(loader.get("b", page.url))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    assert await second is page
    assert http.try_fetch.await_count == 1
    assert cache.put_page.await_count == 1
    await loader.close()


async def test_page_load_failure_retries_and_is_not_cached(tmp_path):
    cfg = config(tmp_path, fetch=replace(Config().fetch, mode="auto"))
    cache = SimpleNamespace(get_page=AsyncMock(return_value=None), put_page=AsyncMock())
    guard = SimpleNamespace(before_fetch=AsyncMock(), check_url=AsyncMock())
    http = SimpleNamespace(try_fetch=AsyncMock(side_effect=[BrowsrError("timeout"), make_page()]))
    loader = PageLoader(cfg, cache, None, guard, http)
    with pytest.raises(BrowsrError):
        await loader.get("a", "https://example.org/a")
    assert await loader.get("a", "https://example.org/a")
    assert cache.put_page.await_count == 1


async def test_active_session_is_preserved_during_maintenance(app):
    app.sessions.ttl_s = 0.01
    session = app.sessions.get("busy")
    session.refs.for_url("https://example.org/a")
    lock = app._session_locks.setdefault("busy", asyncio.Lock())
    app.pool.drop_context = AsyncMock()
    async with lock:
        session.last_access -= 10
        await app._sweep_sessions()
        assert app.sessions.get("busy") is session
        app.pool.drop_context.assert_not_awaited()
    session.last_access -= 10
    await app._sweep_sessions()
    app.pool.drop_context.assert_awaited_once_with("busy")
    app.pages.drop_context.assert_awaited_once_with("busy")
    assert app.sessions.get("busy") is not session


async def test_budget_with_large_reference_ids_and_many_links(app):
    app.cfg = replace(app.cfg, output=replace(app.cfg.output, max_tokens=1000))
    session = app.sessions.get("one")
    session.refs._next = 10000
    app.pages.get.return_value = make_page(
        " ".join(["[x](@L0)"] * 360), ["https://example.org/link"]
    )
    out = await app.call("one", "open", {"url": "https://example.org/a"})
    assert "error" not in out
    assert estimate(dumps(out)) <= 1000
    assert "@L" not in out["text"]
    cached = app._chunks_cache.copy()
    await app.call("one", "open", {"url": "https://example.org/a"})
    assert app._chunks_cache == cached


async def test_cancelled_call_is_logged_and_reraises(app):
    entered = asyncio.Event()

    async def wait(*args):
        entered.set()
        await asyncio.Event().wait()

    app._search = wait
    app.calllog.write = AsyncMock()
    task = asyncio.create_task(app.call("one", "search", {"query": "example"}))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    app.calllog.write.assert_awaited_once()
    assert app.calllog.write.call_args.kwargs["outcome"] == "timeout"
