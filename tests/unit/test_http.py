"""M6 direct HTTP fallback and shared guarded transport."""

from dataclasses import replace

import httpx
import pytest

from browsr.config import Config
from browsr.errors import BrowsrError
from browsr.fetch.http import HttpFetcher


class Guard:
    def __init__(self):
        self.checked: list[str] = []

    async def check_url(self, url: str) -> None:
        self.checked.append(url)
        if "localhost" in url:
            raise BrowsrError("forbidden_target")


async def fetcher_with(transport, cfg=None):
    guard = Guard()
    fetcher = HttpFetcher(cfg or Config(), guard, "test-agent")
    await fetcher._client.aclose()

    async def checked(request):
        await guard.check_url(str(request.url))

    fetcher._client = httpx.AsyncClient(
        transport=httpx.MockTransport(transport),
        follow_redirects=True,
        event_hooks={"request": [checked]},
    )
    return fetcher, guard


@pytest.mark.asyncio
async def test_strict_accepts_short_javascript_shell(monkeypatch):
    html = '<html><head><title>Short page</title></head><body><div id="root"></div></body></html>'
    fetcher, _ = await fetcher_with(
        lambda request: httpx.Response(200, text=html, headers={"content-type": "text/html"})
    )
    monkeypatch.setattr("trafilatura.extract", lambda *args, **kwargs: "Short result")
    try:
        assert await fetcher.try_fetch("https://example.org/") is None
        page = await fetcher.fetch("https://example.org/")
        assert page.title == "Short page"
        assert "Short result" in page.markdown
    finally:
        await fetcher.close()


@pytest.mark.asyncio
async def test_auto_short_text_returns_none_but_strict_accepts(monkeypatch):
    html = "<html><head><title>Article</title></head><body><p>Article content</p></body></html>"
    fetcher, _ = await fetcher_with(
        lambda request: httpx.Response(200, text=html, headers={"content-type": "text/html"})
    )
    monkeypatch.setattr("trafilatura.extract", lambda *args, **kwargs: "Useful but short")
    try:
        assert await fetcher.try_fetch("https://example.org/") is None
        assert "Useful but short" in (await fetcher.fetch("https://example.org/")).markdown
    finally:
        await fetcher.close()


@pytest.mark.asyncio
async def test_strict_empty_extraction_is_fetch_failed(monkeypatch):
    fetcher, _ = await fetcher_with(
        lambda request: httpx.Response(
            200,
            text="<html><body>Content</body></html>",
            headers={"content-type": "text/html"},
        )
    )
    monkeypatch.setattr("trafilatura.extract", lambda *args, **kwargs: None)
    try:
        assert await fetcher.try_fetch("https://example.org/") is None
        with pytest.raises(BrowsrError) as error:
            await fetcher.fetch("https://example.org/")
        assert error.value.code == "fetch_failed"
    finally:
        await fetcher.close()


@pytest.mark.asyncio
async def test_challenge_uses_visible_body_in_both_modes():
    html = (
        "<html><head><title>Ordinary</title><script>" + "x" * 4000 + "</script></head>"
        "<body><p>Enter the characters to continue</p></body></html>"
    )
    fetcher, _ = await fetcher_with(
        lambda request: httpx.Response(200, text=html, headers={"content-type": "text/html"})
    )
    try:
        for method in (fetcher.try_fetch, fetcher.fetch):
            with pytest.raises(BrowsrError) as error:
                await method("https://example.org/")
            assert error.value.code == "blocked"
    finally:
        await fetcher.close()


@pytest.mark.asyncio
async def test_non_html_does_not_scan_content_for_challenge_words():
    def transport(request):
        if request.url.path == "/json":
            return httpx.Response(
                200,
                json={"captcha": False, "description": "No verification required"},
                headers={"content-type": "application/json"},
            )
        if request.url.path == "/plain":
            return httpx.Response(
                200,
                text="This article discusses CAPTCHA systems.",
                headers={"content-type": "text/plain"},
            )
        if request.url.path == "/blocked":
            return httpx.Response(403, text="denied", headers={"content-type": "text/plain"})
        return httpx.Response(404, text="missing", headers={"content-type": "text/plain"})

    fetcher, _ = await fetcher_with(transport)
    try:
        assert '"captcha": false' in (await fetcher.fetch("https://example.org/json")).markdown
        assert "CAPTCHA systems" in (await fetcher.try_fetch("https://example.org/plain")).markdown
        with pytest.raises(BrowsrError) as challenge:
            await fetcher.fetch("https://example.org/blocked")
        assert challenge.value.code == "blocked"
        raw = await fetcher.request("https://example.org/missing")
        assert raw.status == 404 and raw.body == b"missing"
        with pytest.raises(BrowsrError) as missing:
            await fetcher.fetch("https://example.org/missing")
        assert missing.value.code == "not_found"
    finally:
        await fetcher.close()


@pytest.mark.asyncio
async def test_request_checks_redirect_before_network():
    requests = []

    def transport(request):
        requests.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://localhost/secret"})

    fetcher, guard = await fetcher_with(transport)
    try:
        with pytest.raises(BrowsrError) as error:
            await fetcher.request("https://example.org/start")
        assert error.value.code == "forbidden_target"
        assert requests == ["https://example.org/start"]
        assert "http://localhost/secret" in guard.checked
    finally:
        await fetcher.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("declared", [True, False])
async def test_request_checks_declared_and_actual_size(declared):
    cfg = replace(Config(), fetch=replace(Config().fetch, max_bytes=4))
    headers = {"content-type": "application/json"}
    if declared:
        headers["content-length"] = "100"
    else:
        headers["content-length"] = "4"
    fetcher, _ = await fetcher_with(
        lambda request: httpx.Response(200, content=b"12345", headers=headers), cfg
    )
    try:
        with pytest.raises(BrowsrError) as error:
            await fetcher.request("https://example.org/")
        assert error.value.code == "unsupported"
    finally:
        await fetcher.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exception", "expected"),
    [
        (httpx.ConnectError("network down"), "fetch_failed"),
        (httpx.ReadTimeout("too slow"), "timeout"),
    ],
)
async def test_transport_errors_are_typed(exception, expected):
    def failing(request):
        raise exception

    fetcher, _ = await fetcher_with(failing)
    try:
        with pytest.raises(BrowsrError) as error:
            await fetcher.fetch("https://example.org/")
        assert error.value.code == expected
        assert error.value.detail
    finally:
        await fetcher.close()
