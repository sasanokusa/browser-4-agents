from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from browsr.config import Config
from browsr.errors import BrowsrError
from browsr.models import SearchResult
from browsr.search.base import BackendBlocked
from browsr.search.ddg import parse as parse_ddg
from browsr.search.mojeek import parse as parse_mojeek
from browsr.search.router import SearchRouter

FIXTURES = Path(__file__).parents[1] / "fixtures" / "search"


class FakeResponse:
    def __init__(self, status=200):
        self.status = status


class FixturePage:
    url = "https://html.duckduckgo.com/html/"

    def __init__(self, fixture, rows):
        self.html = (FIXTURES / fixture).read_text()
        self.rows = rows

    async def evaluate(self, script, selectors):
        if "block" in selectors and "anomaly-modal" in self.html:
            return {"blocked": True, "rows": []}
        if "result" in selectors and selectors["result"].startswith("ul.results-standard"):
            return self.rows
        return {"blocked": False, "rows": self.rows}


def test_ddg_parser_unwraps_redirect_and_detects_challenge():
    page = FixturePage(
        "ddg.html",
        [
            {
                "title": "Example guide",
                "href": "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fguide",
                "snippet": "Useful information about the guide.",
            }
        ],
    )
    assert asyncio.run(parse_ddg(page, FakeResponse())) == [
        SearchResult(
            "Example guide",
            "https://example.com/guide",
            "Useful information about the guide.",
        )
    ]

    with pytest.raises(BackendBlocked):
        asyncio.run(parse_ddg(FixturePage("ddg_blocked.html", []), FakeResponse()))


def test_mojeek_parser_extracts_fixture_result():
    page = FixturePage(
        "mojeek.html",
        [
            {
                "title": "Mojeek result",
                "href": "https://example.org/page",
                "snippet": "A result from Mojeek.",
            }
        ],
    )
    assert asyncio.run(parse_mojeek(page, FakeResponse())) == [
        SearchResult("Mojeek result", "https://example.org/page", "A result from Mojeek.")
    ]


class FakeBreaker:
    def __init__(self):
        self.events = []

    def available(self, key):
        return True

    def success(self, key):
        self.events.append(("success", key))

    def fail(self, key):
        self.events.append(("fail", key))

    def trip(self, key):
        self.events.append(("trip", key))

    def state(self):
        return {}


class FakeLimiter:
    async def acquire(self, *args):
        return True


class FakeBackend:
    def __init__(self, name, response):
        self.name = name
        self.response = response
        self.interval_s = 0
        self.timeout_s = 1

    async def search(self, query, n):
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def make_router(backends):
    router = SearchRouter(Config(), None, FakeLimiter(), FakeBreaker())
    # Close the default client immediately; only mocked providers are used here.
    asyncio.run(router.close())
    router.backends = backends
    router._backend_names = tuple(backend.name for backend in backends)
    return router


def test_router_filters_deduplicates_and_falls_back():
    router = make_router(
        [
            FakeBackend("first", [SearchResult("bad", "javascript:alert(1)", "")]),
            FakeBackend(
                "second",
                [
                    SearchResult("  title  ", "https://example.com/a?utm_source=x", "one\n two"),
                    SearchResult("duplicate", "https://example.com/a", "duplicate"),
                    SearchResult("bad", "file:///tmp/a", ""),
                ],
            ),
        ]
    )

    async def run_search():
        results = await router.search("query", 5)
        return results, router.last_backend

    results, last_backend = asyncio.run(run_search())
    assert results == [SearchResult("title", "https://example.com/a?utm_source=x", "one two")]
    assert last_backend == "second"


def test_router_returns_empty_when_a_backend_answers_with_no_results():
    router = make_router([FakeBackend("first", [])])

    async def run_search():
        results = await router.search("no match", 5)
        return results, router.last_backend

    results, last_backend = asyncio.run(run_search())
    assert results == []
    assert last_backend == "first"


def test_router_raises_unavailable_when_every_backend_fails():
    router = make_router([FakeBackend("first", BackendBlocked())])
    with pytest.raises(BrowsrError) as raised:
        asyncio.run(router.search("query", 5))
    assert raised.value.code == "search_unavailable"
