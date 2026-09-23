"""Real Firefox + real service over REST and a child-process MCP transport."""

import json
import sys
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import httpx
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from browsr.config import Config
from browsr.interfaces.rest import create_http_app
from browsr.service import Browsr
from browsr.tokens import estimate

pytestmark = pytest.mark.browser


@pytest.fixture
def website():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path).path
            origin = f"http://127.0.0.1:{self.server.server_port}"
            if path == "/search":
                content = json.dumps(
                    {
                        "results": [
                            {
                                "title": "Integration article",
                                "url": origin + "/redirect",
                                "content": "Local deterministic search fixture",
                            }
                        ]
                    }
                ).encode()
                content_type = "application/json"
            elif path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/article")
                self.end_headers()
                return
            else:
                paragraphs = "".join(
                    f"<h2>Section {i}</h2><p>" + (f"段落{i}の内容を読むテストです。" * 70) + "</p>"
                    for i in range(12)
                )
                content = (
                    "<html><head><title>Integration article</title></head>"
                    "<body><nav>Excluded navigation</nav><article><h1>Article</h1>"
                    + paragraphs
                    + '<a href="/other">Further reading</a>'
                    + "</article></body></html>"
                ).encode()
                content_type = "text/html; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


async def test_rest_search_open_continuation(website, tmp_path):
    cfg = Config()
    cfg = replace(
        cfg,
        security=replace(cfg.security, allow_private=True, domain_interval_s=0),
        browser=replace(cfg.browser, settle_ms=50),
        output=replace(cfg.output, max_tokens=1000),
        search=replace(
            cfg.search, backends=["searxng"], searxng=replace(cfg.search.searxng, url=website)
        ),
        cache=replace(cfg.cache, path=str(tmp_path / "cache.sqlite")),
        log=replace(cfg.log, calls_path=str(tmp_path / "calls.jsonl")),
    )
    service = Browsr(cfg)
    asgi = create_http_app(service)
    async with asgi.router.lifespan_context(asgi):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=asgi),
            base_url="http://test",
            headers={"X-Session": "e2e"},
        ) as client:
            response = await client.get("/search", params={"q": "integration"})
            assert response.status_code == 200
            assert response.json()["results"][0]["id"] == 1
            first = (await client.get("/open", params={"url": "1"})).json()
            assert "error" not in first, first
            assert first["url"] == website + "/article"
            assert "Excluded navigation" not in first["text"]
            assert first["part"].startswith("1/")
            assert estimate(json.dumps(first, ensure_ascii=False, separators=(",", ":"))) <= 1000
            second = (
                await client.post(
                    "/call", json={"name": "open", "arguments": json.dumps({"ref": first["next"]})}
                )
            ).json()
            assert second["part"].startswith("2/")
            assert second["id"] == first["id"]
            health = (await client.get("/health")).json()
            assert health["ok"] and health["browser"]
    records = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert records[-1]["cache"] is True


async def test_stdio_search_open_continuation(website, tmp_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "browsr", "serve"],
        env={
            "BROWSR_SECURITY__ALLOW_PRIVATE": "true",
            "BROWSR_SECURITY__DOMAIN_INTERVAL_S": "0",
            "BROWSR_BROWSER__SETTLE_MS": "50",
            "BROWSR_OUTPUT__MAX_TOKENS": "1000",
            "BROWSR_SEARCH__BACKENDS": "searxng",
            "BROWSR_SEARCH__SEARXNG__URL": website,
            "BROWSR_CACHE__PATH": str(tmp_path / "mcp.sqlite"),
            "BROWSR_LOG__CALLS_PATH": str(tmp_path / "mcp.jsonl"),
        },
    )
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as client:
            await client.initialize()
            assert [tool.name for tool in (await client.list_tools()).tools] == ["search", "open"]
            result = await client.call_tool("search", {"q": "integration"})
            assert not result.model_dump(by_alias=True).get("isError")
            assert json.loads(result.content[0].text)["results"][0]["id"] == 1
            result = await client.call_tool("open", {"url": "1"})
            first = json.loads(result.content[0].text)
            assert "error" not in first, first
            result = await client.call_tool("open", {"id": first["next"]})
            second = json.loads(result.content[0].text)
            assert second["part"].startswith("2/")
            bad = await client.call_tool("open", {"url": "99999"})
            assert not bad.model_dump(by_alias=True).get("isError")
            assert json.loads(bad.content[0].text)["error"] == "unknown_id"
