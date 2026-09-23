from __future__ import annotations

import asyncio
import json

from browsr import __main__ as cli
from browsr import service
from browsr.config import Config


def test_tools_cli_prints_only_json_without_starting_browser(capsys, monkeypatch):
    monkeypatch.setattr(cli, "_config", lambda path: Config())
    assert cli.main(["tools", "--format", "anthropic", "--mode", "single"]) == 0
    out = capsys.readouterr()
    assert out.err == ""
    assert json.loads(out.out)[0]["name"] == "web"


def test_cli_call_dispatches_arguments(capsys, monkeypatch):
    monkeypatch.setattr(cli, "_config", lambda path: Config())
    captured = []

    async def fake_call(cfg, name, arguments):
        captured.append((name, arguments))
        return {"query": "日本語"}

    monkeypatch.setattr(cli, "_call", fake_call)
    assert cli.main(["search", "日本語"]) == 0
    assert captured == [("search", {"query": "日本語"})]
    assert capsys.readouterr().out == '{"query":"日本語"}\n'


def test_cli_call_owns_service_and_uses_cli_session(monkeypatch):
    events = []

    class FakeService:
        def __init__(self, cfg):
            events.append(("init", cfg))

        async def start(self):
            events.append(("start",))

        async def call(self, sid, name, arguments):
            events.append(("call", sid, name, arguments))
            return {"ok": True}

        async def close(self):
            events.append(("close",))

    monkeypatch.setattr(service, "Browsr", FakeService)
    cfg = Config()
    assert asyncio.run(cli._call(cfg, "open", {"url": "1"})) == {"ok": True}
    assert events == [("init", cfg), ("start",), ("call", "cli", "open", {"url": "1"}), ("close",)]


def test_doctor_reports_each_check_and_fails_when_one_fails(capsys, monkeypatch):
    monkeypatch.setattr(cli, "_config", lambda path: Config())

    async def good(_cfg):
        return {"ok": True}

    async def bad(_cfg):
        return {"ok": False, "detail": "unavailable"}

    monkeypatch.setattr(cli, "_check_browser", good)
    monkeypatch.setattr(cli, "_check_searxng", bad)
    monkeypatch.setattr(cli, "_check_cache", lambda _cfg: {"ok": True})
    assert cli.main(["doctor"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False
    assert list(result["checks"]) == ["browser", "searxng", "cache"]
