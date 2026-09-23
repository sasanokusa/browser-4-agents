"""M6 fallback, cache, and call-log behavior through REST with real Firefox."""

import json
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import httpx
import pytest

from browsr.config import Config
from browsr.interfaces.rest import create_http_app
from browsr.service import Browsr

pytestmark = pytest.mark.browser


@pytest.fixture
def fallback_site():
    hits = []
    state = {"challenge": True}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path).path
            via = "http" if self.headers.get("X-Test-Fetch") == "http" else "browser"
            hits.append((path, via))
            blocked = path.startswith("/recover") and via == "browser"
            blocked |= path == "/change" and state["challenge"]
            if blocked:
                body = (
                    b"<html><title>Client Challenge</title><body>"
                    b"Please verify you are human.</body></html>"
                )
            else:
                body = (
                    b"<html><title>Recovered article</title><body><article>"
                    b"<h1>Recovered article</h1><p>This is the actual article text, "
                    b"served successfully after the alternative fetch method was selected. "
                    b"It is deliberately shorter than the auto mode threshold.</p>"
                    b"</article></body></html>"
                )
            self.send_response(404 if path == "/missing" else 200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", hits, state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


async def test_rest_fallback_domain_memory_and_call_log(fallback_site, tmp_path):
    origin, hits, state = fallback_site
    cfg = Config()
    cfg = replace(
        cfg,
        browser=replace(cfg.browser, settle_ms=50),
        fetch=replace(cfg.fetch, fallbacks=["http"]),
        security=replace(cfg.security, allow_private=True, domain_interval_s=0),
        cache=replace(cfg.cache, path=str(tmp_path / "cache.sqlite")),
        log=replace(cfg.log, calls_path=str(tmp_path / "calls.jsonl")),
    )
    service = Browsr(cfg)
    asgi = create_http_app(service)
    async with asgi.router.lifespan_context(asgi):
        # Identify the direct client to this fixture without replacing either fetcher.
        service.http._client.headers["X-Test-Fetch"] = "http"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=asgi), base_url="http://test"
        ) as client:
            missing = (await client.get("/open", params={"url": origin + "/missing"})).json()
            assert missing["error"] == "not_found"
            assert [hit for hit in hits if hit[0] == "/missing"] == [("/missing", "browser")]

            for path in ("/recover/one", "/recover/two", "/recover/one"):
                out = (await client.get("/open", params={"url": origin + path})).json()
                assert "error" not in out, out
                assert "actual article text" in out["text"]
                assert "Client Challenge" not in out["text"]
            assert [hit for hit in hits if hit[0].startswith("/recover")] == [
                ("/recover/one", "browser"),
                ("/recover/one", "http"),
                ("/recover/two", "http"),
            ]

            blocked = (await client.get("/open", params={"url": origin + "/change"})).json()
            assert blocked["error"] == "blocked"
            assert "detail" not in blocked
            state["challenge"] = False
            recovered = (await client.get("/open", params={"url": origin + "/change"})).json()
            assert "actual article text" in recovered["text"]
            assert [hit for hit in hits if hit[0] == "/change"] == [
                ("/change", "http"),
                ("/change", "http"),
            ]

    records = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert [record["via"] for record in records] == [
        "browser",
        "http",
        "http",
        "cache",
        "http",
        "http",
    ]
    assert [record["outcome"] for record in records] == [
        "not_found",
        "ok",
        "ok",
        "ok",
        "blocked",
        "ok",
    ]
    assert records[3]["cache"] is True
    assert records[4]["detail"]
    assert records[5]["cache"] is False
