import asyncio
import socket
import time
from dataclasses import replace

import httpx
import pytest

from browsr.config import Config
from browsr.errors import BrowsrError
from browsr.fetch import detect
from browsr.fetch.browser import BrowserPool
from browsr.fetch.guard import Guard as URLGuard
from browsr.fetch.http import HttpFetcher, needs_js
from browsr.fetch.proxy import GuardProxy


class Guard:
    def __init__(self):
        self.checked = []

    async def check_url(self, url):
        self.checked.append(url)
        if "localhost" in url:
            raise BrowsrError("forbidden_target")

    async def host_allowed(self, url):
        return "localhost" not in url


@pytest.mark.asyncio
async def test_http_redirect_is_checked_before_request():
    guard = Guard()
    fetcher = HttpFetcher(Config(), guard, "test-agent")
    requested = []

    def transport(request):
        requested.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://localhost/private"})

    await fetcher._client.aclose()
    fetcher._client = httpx.AsyncClient(
        transport=httpx.MockTransport(transport),
        follow_redirects=True,
        event_hooks={"request": [lambda request: guard.check_url(str(request.url))]},
    )
    try:
        with pytest.raises(BrowsrError) as error:
            await fetcher.try_fetch("https://example.org/")
        assert error.value.code == "forbidden_target"
        assert requested == ["https://example.org/"]
        assert "http://localhost/private" in guard.checked
    finally:
        await fetcher.close()


@pytest.mark.asyncio
async def test_http_actual_body_limit():
    cfg = replace(Config(), fetch=replace(Config().fetch, max_bytes=4))
    guard = Guard()
    fetcher = HttpFetcher(cfg, guard, "test-agent")
    await fetcher._client.aclose()
    fetcher._client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, content=b"12345", headers={"content-type": "text/plain"}
            )
        ),
        event_hooks={"request": [lambda request: guard.check_url(str(request.url))]},
    )
    try:
        with pytest.raises(BrowsrError) as error:
            await fetcher.try_fetch("https://example.org/")
        assert error.value.code == "unsupported"
    finally:
        await fetcher.close()


def test_js_detection_and_challenge():
    assert needs_js('<div id="__next"></div>')
    assert needs_js("<noscript>Please enable JavaScript</noscript>")
    assert not needs_js('<div id="root">Content</div>')
    with pytest.raises(BrowsrError) as error:
        detect.raise_for_challenge(403, "", 10)
    assert error.value.code == "blocked"


@pytest.mark.asyncio
async def test_raw_redirect_checks_every_hop():
    guard = Guard()
    pool = BrowserPool(Config(), guard)
    requests = []

    class Response:
        status = 302
        headers = {"location": "http://localhost/secret"}

        async def dispose(self):
            pass

    class Request:
        async def get(self, url, **kwargs):
            requests.append(url)
            return Response()

    class Context:
        request = Request()

    with pytest.raises(BrowsrError) as error:
        await pool._fetch_raw(Context(), "https://example.org/doc.pdf")
    assert error.value.code == "forbidden_target"
    assert requests == ["https://example.org/doc.pdf"]


@pytest.mark.asyncio
async def test_proxy_pins_only_public_dns(monkeypatch):
    async def rebinding_dns(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))]

    async def unexpected_connect(*args, **kwargs):
        raise AssertionError("private socket must not be opened")

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", rebinding_dns)
    monkeypatch.setattr(asyncio, "open_connection", unexpected_connect)
    proxy = GuardProxy(Config(), Guard())
    with pytest.raises(BrowsrError) as error:
        await proxy._connect("example.org", 80)
    assert error.value.code == "forbidden_target"


@pytest.mark.asyncio
async def test_proxy_rejects_multicast_after_cached_public_dns(monkeypatch):
    async def rebinding_dns(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("224.0.0.1", 80))]

    async def unexpected_connect(*args, **kwargs):
        raise AssertionError("multicast socket must not be opened")

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", rebinding_dns)
    monkeypatch.setattr(asyncio, "open_connection", unexpected_connect)
    guard = URLGuard(Config())
    guard._dns["example.org"] = (time.monotonic() + 300, True)
    assert await guard.host_allowed("http://example.org/")
    proxy = GuardProxy(Config(), guard)
    with pytest.raises(BrowsrError) as error:
        await proxy._connect("example.org", 80)
    assert error.value.code == "forbidden_target"


@pytest.mark.asyncio
async def test_browser_restart_is_throttled():
    pool = BrowserPool(Config(), Guard())
    pool._started_once = True
    pool._restarts.extend([time.monotonic()] * 3)
    with pytest.raises(BrowsrError) as error:
        await pool.start()
    assert error.value.code == "blocked"
