import asyncio
import socket
from dataclasses import replace

import httpx
import pytest

from browsr.config import Config
from browsr.errors import BrowsrError
from browsr.fetch.guard import Guard


class Limiter:
    def __init__(self, answer=True):
        self.answer = answer
        self.calls = []

    async def acquire(self, key, interval, wait):
        self.calls.append((key, interval, wait))
        return self.answer


def _dns(address):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    return [(family, socket.SOCK_STREAM, 6, "", (address, 0))]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "192.168.1.1",
        "169.254.4.5",
        "::1",
        "fe80::1",
        "::ffff:127.0.0.1",
        "ff02::1",
        "0.0.0.0",
        "100.64.1.1",
    ],
)
async def test_denies_private_dns(monkeypatch, address):
    async def resolve(*args, **kwargs):
        return _dns(address)

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
    guard = Guard(Config(), Limiter())
    assert not await guard.host_allowed("https://example.org/path")
    with pytest.raises(BrowsrError, match="forbidden_target"):
        await guard.check_url("https://example.org/path")


@pytest.mark.asyncio
async def test_all_addresses_must_be_public_and_cache_expires(monkeypatch):
    calls = 0

    async def resolve(*args, **kwargs):
        nonlocal calls
        calls += 1
        return _dns("8.8.8.8") + _dns("127.0.0.1")

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
    guard = Guard(Config(), Limiter())
    assert not await guard.host_allowed("https://example.org")
    assert not await guard.host_allowed("https://example.org/a")
    assert calls == 1


@pytest.mark.asyncio
async def test_localhost_and_bad_schemes():
    guard = Guard(Config(), Limiter())
    for url in ("http://localhost", "http://foo.localhost", "http://[::1]/"):
        with pytest.raises(BrowsrError) as error:
            await guard.check_url(url)
        assert error.value.code == "forbidden_target"
    for url in ("file:///etc/passwd", "javascript:alert(1)", "http:///bad"):
        with pytest.raises(BrowsrError) as error:
            await guard.check_url(url)
        assert error.value.code == "bad_input"


@pytest.mark.asyncio
async def test_allow_private_and_domain_pacing():
    cfg = replace(Config(), security=replace(Config().security, allow_private=True))
    limiter = Limiter()
    guard = Guard(cfg, limiter)
    await guard.before_fetch("http://www.localhost:8080/path")
    assert limiter.calls == [("domain:localhost", cfg.security.domain_interval_s, 10)]
    limiter.answer = False
    with pytest.raises(BrowsrError) as error:
        await guard.before_fetch("http://localhost:8080/path")
    assert error.value.code == "blocked"


@pytest.mark.asyncio
async def test_robots_disallow_is_cached():
    cfg = replace(
        Config(),
        security=replace(Config().security, allow_private=True, respect_robots=True),
    )
    guard = Guard(cfg, Limiter())
    calls = []

    def robots(request):
        calls.append(str(request.url))
        return httpx.Response(200, text="User-agent: browsr\nDisallow: /private")

    guard._client = httpx.AsyncClient(transport=httpx.MockTransport(robots))
    try:
        with pytest.raises(BrowsrError) as error:
            await guard.before_fetch("https://example.org/private")
        assert error.value.code == "disallowed"
        await guard.before_fetch("https://example.org/public")
        assert calls == ["https://example.org/robots.txt"]
    finally:
        await guard.close()
