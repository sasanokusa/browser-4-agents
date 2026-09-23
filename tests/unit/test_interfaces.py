from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

from starlette.testclient import TestClient

from browsr.interfaces.rest import create_http_app
from browsr.interfaces.schemas import export, tool_defs


@dataclass
class FakeBrowsr:
    cfg: object = field(
        default_factory=lambda: SimpleNamespace(
            server=SimpleNamespace(mode="standard"),
            tools=SimpleNamespace(descriptions={"search": "Custom search"}),
        )
    )
    pool: object = field(default_factory=lambda: SimpleNamespace(connected=True))
    breaker: object = field(
        default_factory=lambda: SimpleNamespace(state=lambda: {"ddg": "closed"})
    )
    search: object = field(
        default_factory=lambda: SimpleNamespace(backend_states={"ddg": "closed"})
    )
    calls: list = field(default_factory=list)
    started: int = 0
    closed: int = 0

    async def start(self):
        self.started += 1

    async def close(self):
        self.closed += 1

    async def call(self, sid, name, arguments):
        self.calls.append((sid, name, arguments))
        if name not in {"search", "open", "web"}:
            return {"error": "bad_input", "hint": "Pass a URL, a result number, or search words."}
        return {"session": sid, "tool": name, "arguments": arguments, "title": "日本語"}


def test_tool_schemas_and_exports():
    standard = tool_defs("standard", {"search": "Custom search"})
    assert [d["name"] for d in standard] == ["search", "open"]
    assert standard[0]["parameters"] == {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }
    assert standard[0]["description"] == "Custom search"
    standard[0]["parameters"]["properties"]["query"]["type"] = "number"
    assert tool_defs("standard")[0]["parameters"]["properties"]["query"]["type"] == "string"
    assert [d["function"]["name"] for d in export("openai", "standard")] == ["search", "open"]
    assert export("anthropic", "single")[0]["input_schema"]["required"] == ["input"]
    assert export("mcp", "single")[0]["inputSchema"]["required"] == ["input"]


def test_rest_contract_and_lifecycle():
    service = FakeBrowsr()
    with TestClient(create_http_app(service)) as client:
        assert service.started == 1
        result = client.get("/search", params={"q": "東京"}, headers={"X-Session": "alpha"})
        assert result.status_code == 200
        assert result.headers["content-type"] == "application/json; charset=utf-8"
        assert (
            result.text
            == '{"session":"alpha","tool":"search","arguments":{"q":"東京"},"title":"日本語"}'
        )
        assert client.get("/open", params={"url": "1"}).json()["session"] == "default"
        posted = client.post("/call", json={"name": "web", "arguments": '{"input":"2"}'})
        assert posted.json()["arguments"] == '{"input":"2"}'
        assert client.post("/call", content="{").json()["error"] == "bad_input"
        assert client.post("/call", json={"name": 3}).json()["error"] == "bad_input"
        assert client.post("/call", json={"name": "missing"}).json()["error"] == "bad_input"
        assert (
            client.get("/tools", params={"format": "mcp", "mode": "single"}).json()[0]["name"]
            == "web"
        )
        assert client.get("/tools", params={"format": "bogus"}).json()["error"] == "bad_input"
        assert client.get("/health").json() == {
            "ok": True,
            "browser": True,
            "backends": {"ddg": "closed"},
        }
        assert client.get("/not-a-route").status_code == 404
    assert service.closed == 1
