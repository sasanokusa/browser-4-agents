from __future__ import annotations

import json
from types import SimpleNamespace

from starlette.testclient import TestClient

from browsr.interfaces.rest import create_http_app


class FakeBrowsr:
    def __init__(self):
        self.cfg = SimpleNamespace(
            server=SimpleNamespace(mode="standard"),
            tools=SimpleNamespace(descriptions={}),
        )
        self.pool = SimpleNamespace(connected=True)
        self.breaker = SimpleNamespace(state=lambda: {})
        self.calls = []

    async def start(self):
        pass

    async def close(self):
        pass

    async def call(self, sid, name, arguments):
        self.calls.append((sid, name, arguments))
        return {"session": sid, "name": name, "arguments": arguments}


def _message(response):
    # Streamable HTTP commonly returns SSE; the MCP JSON is one data event.
    data = next(line[6:] for line in response.text.splitlines() if line.startswith("data: "))
    return json.loads(data)


def test_streamable_http_lists_and_calls_tools_without_schema_rejection():
    app = FakeBrowsr()
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    with TestClient(create_http_app(app)) as client:
        initialized = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
            headers=headers,
        )
        assert initialized.status_code == 200
        assert _message(initialized)["result"]["serverInfo"]["name"] == "browsr"
        headers["mcp-session-id"] = initialized.headers["mcp-session-id"]
        client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            headers=headers,
        )

        listed = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers=headers,
        )
        assert [tool["name"] for tool in _message(listed)["result"]["tools"]] == ["search", "open"]

        # `q` is intentionally outside the advertised `query` schema. Browsr
        # accepts it and performs the final normalization.
        called = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "search",
                    "arguments": {"q": "京都"},
                },
            },
            headers=headers,
        )
        result = _message(called)["result"]
        assert result["isError"] is False
        content = json.loads(result["content"][0]["text"])
        assert content["arguments"] == {"q": "京都"}
        assert content["session"] == app.calls[0][0]
        assert content["session"] != "default"
