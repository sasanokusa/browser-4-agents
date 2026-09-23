"""Local Firefox checks; no external network access is needed."""

import asyncio
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from browsr.config import Config
from browsr.errors import BrowsrError
from browsr.fetch.browser import BrowserPool
from browsr.fetch.guard import Guard

pytestmark = [pytest.mark.browser, pytest.mark.asyncio]
DETECT_FIXTURES = Path(__file__).parents[1] / "fixtures" / "detect"


@pytest.fixture(scope="module")
def site():
    class Handler(BaseHTTPRequestHandler):
        hits = []

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            self.hits.append((path, self.headers.get("Host")))
            if path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/html")
                self.end_headers()
                return
            if path == "/redirect-private":
                self.send_response(302)
                self.send_header("Location", f"http://localhost:{self.server.server_port}/secret")
                self.end_headers()
                return
            if path == "/script-redirect":
                self.send_response(302)
                self.send_header("Location", f"http://localhost:{self.server.server_port}/secret")
                self.end_headers()
                return
            if path == "/slow":
                time.sleep(0.8)
            status = 200
            if path in {"/fastly", "/cloudflare", "/normal"}:
                body = (DETECT_FIXTURES / f"{path[1:]}.html").read_bytes()
                ctype = "text/html"
            elif path == "/raw-404":
                body = b"missing PDF"
                ctype = "application/pdf"
                status = 404
            elif path == "/raw-503":
                body = b"temporary block"
                ctype = "application/pdf"
                status = 503
            elif path == "/pdf":
                body = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF"
                ctype = "application/pdf"
            elif path == "/consent":
                body = (
                    b"<html><head><title>Consent fixture</title></head><body>"
                    b"<button id='accept'>Accept all cookies</button>"
                    b"<article><h1>Article</h1><p>Consent article content remains visible. "
                    b"This text should be extracted.</p></article>"
                    b"<script>document.getElementById('accept').onclick = () => "
                    b"document.getElementById('accept').remove()</script>"
                    b"</body></html>"
                )
                ctype = "text/html"
            elif path == "/subresource":
                body = (
                    b"<html><head><title>Subresource fixture</title></head><body>"
                    b"<script src='/forbidden'></script><article>Body content</article>"
                    b"</body></html>"
                )
                ctype = "text/html"
            elif path == "/subresource-redirect":
                body = (
                    b"<html><head><title>Subresource redirect</title></head><body>"
                    b"<script src='/script-redirect'></script><article>Body content</article>"
                    b"</body></html>"
                )
                ctype = "text/html"
            elif path == "/forbidden":
                body = b"console.log('should not load')"
                ctype = "application/javascript"
            else:
                body = (
                    b"<html><head><title>Fixture title</title></head><body>"
                    b"<article><h1>Fixture title</h1><p>Browser fixture article text "
                    b"for extraction and redirect tests.</p></article>"
                    b"</body></html>"
                )
                ctype = "text/html"
            try:
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except BrokenPipeError:
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    class Site(str):
        pass

    origin = Site(f"http://127.0.0.1:{server.server_port}")
    origin.hits = Handler.hits
    try:
        yield origin
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def config(**browser_changes):
    base = Config()
    return replace(
        base,
        browser=replace(base.browser, settle_ms=300, **browser_changes),
        security=replace(base.security, allow_private=True),
    )


async def test_html_redirect_and_context_reuse(site):
    cfg = config()
    guard = Guard(cfg)
    pool = BrowserPool(cfg, guard)
    try:
        await pool.start()
        assert pool.connected and "Firefox" in pool.user_agent
        raw = await pool.fetch("s1", site + "/redirect")
        assert raw.final_url == site + "/html"
        assert raw.status == 200
        assert raw.content_type == "text/html"
        assert raw.title == "Fixture title"
        assert "Browser fixture article" in raw.html

        async def relative(page, response):
            return await page.evaluate("new URL('relative', document.baseURI).href")

        assert await pool.run("s1", site + "/redirect", relative) == site + "/relative"
        assert len(pool._contexts) == 1
        await pool.drop_context("s1")
        assert not pool._contexts
    finally:
        await pool.close()
        await guard.close()


async def test_pdf_download_fallback(site):
    cfg = config()
    pool = BrowserPool(cfg, Guard(cfg))
    try:
        raw = await pool.fetch("pdf", site + "/pdf")
        assert raw.content_type == "application/pdf"
        assert raw.body.startswith(b"%PDF")
    finally:
        await pool.close()


@pytest.mark.parametrize("path", ["/fastly", "/cloudflare"])
async def test_challenge_page_is_blocked(site, path):
    cfg = config()
    pool = BrowserPool(cfg, Guard(cfg))
    try:
        with pytest.raises(BrowsrError) as error:
            await pool.fetch("challenge", site + path)
        assert error.value.code == "blocked"
        assert error.value.detail.startswith("challenge ")
    finally:
        await pool.close()


async def test_short_normal_page_is_not_blocked(site):
    cfg = config()
    pool = BrowserPool(cfg, Guard(cfg))
    try:
        raw = await pool.fetch("normal", site + "/normal")
        assert raw.title == "Garden notes"
        assert "Autumn planting" in raw.html
    finally:
        await pool.close()


@pytest.mark.parametrize(("path", "expected"), [("/raw-404", "not_found"), ("/raw-503", "blocked")])
async def test_raw_status_errors(site, path, expected):
    cfg = config()
    pool = BrowserPool(cfg, Guard(cfg))
    try:
        entry = await pool._context("raw")
        try:
            with pytest.raises(BrowsrError) as error:
                await pool._fetch_raw(entry.value, site + path)
            assert error.value.code == expected
        finally:
            await pool._release(entry)
    finally:
        await pool.close()


async def test_timeout(site):
    cfg = config(timeout_ms=150)
    pool = BrowserPool(cfg, Guard(cfg))
    try:
        with pytest.raises(BrowsrError) as error:
            await pool.fetch("slow", site + "/slow")
        assert error.value.code == "timeout"
    finally:
        await pool.close()


async def test_consent_banner(site):
    cfg = config()
    pool = BrowserPool(cfg, Guard(cfg))
    try:
        raw = await pool.fetch("consent", site + "/consent")
        assert raw.title == "Consent fixture"
        assert "Consent article content" in raw.html
    finally:
        await pool.close()


async def test_forbidden_subresource_is_request_local(site):
    cfg = config()

    class SelectiveGuard:
        async def check_url(self, url):
            if not await self.host_allowed(url):
                raise BrowsrError("forbidden_target")

        async def host_allowed(self, url):
            return not url.endswith("/forbidden")

    pool = BrowserPool(cfg, SelectiveGuard())
    try:
        with pytest.raises(BrowsrError) as error:
            await pool.fetch("same", site + "/subresource")
        assert error.value.code == "forbidden_target"
        raw = await pool.fetch("same", site + "/html")
        assert raw.status == 200
    finally:
        await pool.close()


async def test_forbidden_redirect_is_blocked(site):
    cfg = config()

    class RedirectGuard:
        async def check_url(self, url):
            if not await self.host_allowed(url):
                raise BrowsrError("forbidden_target")

        async def host_allowed(self, url):
            return "localhost" not in url

    pool = BrowserPool(cfg, RedirectGuard())
    try:
        with pytest.raises(BrowsrError) as error:
            await pool.fetch("redirect", site + "/redirect-private")
        assert error.value.code == "forbidden_target"
        assert not any(path == "/secret" for path, _ in site.hits)
    finally:
        await pool.close()


async def test_forbidden_subresource_redirect_never_connects(site):
    cfg = config()

    class RedirectGuard:
        async def check_url(self, url):
            if not await self.host_allowed(url):
                raise BrowsrError("forbidden_target")

        async def host_allowed(self, url):
            return "localhost" not in url

    pool = BrowserPool(cfg, RedirectGuard())
    try:
        with pytest.raises(BrowsrError) as error:
            await pool.fetch("subresource", site + "/subresource-redirect")
        assert error.value.code == "forbidden_target"
        assert not any(path == "/secret" for path, _ in site.hits)
    finally:
        await pool.close()


async def test_busy_context_waits_before_eviction(site):
    cfg = config(max_contexts=1, max_tabs=2)
    pool = BrowserPool(cfg, Guard(cfg))
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold(page, response):
        entered.set()
        await release.wait()
        return response.status

    try:
        first = asyncio.create_task(pool.run("first", site + "/html", hold))
        await asyncio.wait_for(entered.wait(), timeout=5)
        second = asyncio.create_task(pool.fetch("second", site + "/html"))
        await asyncio.sleep(0.2)
        assert "first" in pool._contexts
        assert pool._contexts["first"].active == 1
        assert not second.done()
        release.set()
        assert await first == 200
        assert (await second).status == 200
        assert list(pool._contexts) == ["second"]
    finally:
        release.set()
        await pool.close()
