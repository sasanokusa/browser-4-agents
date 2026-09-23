"""Command-line entry point for Browsr."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sqlite3
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="browsr")
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="Run an MCP server and, for HTTP, REST endpoints")
    serve.add_argument("--transport", choices=("stdio", "http"))
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--mode", choices=("standard", "single"))
    serve.add_argument("--config", type=Path)

    tools = commands.add_parser("tools", help="Print tool definitions as JSON")
    tools.add_argument("--format", choices=("openai", "anthropic", "mcp"), default="openai")
    tools.add_argument("--mode", choices=("standard", "single"))
    tools.add_argument("--config", type=Path)

    search = commands.add_parser("search", help="Search the web")
    search.add_argument("query")
    search.add_argument("--config", type=Path)

    open_page = commands.add_parser("open", help="Open a URL or result number")
    open_page.add_argument("url_or_id")
    open_page.add_argument("--config", type=Path)

    doctor = commands.add_parser("doctor", help="Check browser, SearXNG, and cache")
    doctor.add_argument("--config", type=Path)
    return parser


def _config(path: Path | None):
    from browsr.config import load_config

    return load_config(path)


async def _call(cfg: Any, name: str, arguments: dict[str, str]) -> dict[str, Any]:
    from browsr.service import Browsr

    app = Browsr(cfg)
    await app.start()
    try:
        return await app.call("cli", name, arguments)
    finally:
        await app.close()


async def _check_browser(cfg: Any) -> dict[str, Any]:
    try:
        if cfg.browser.engine == "camoufox":
            from camoufox.async_api import AsyncCamoufox

            async with AsyncCamoufox(headless=cfg.browser.headless) as browser:
                page = await browser.new_page()
                await page.close()
        else:
            from playwright.async_api import async_playwright

            async with async_playwright() as playwright:
                browser = await playwright.firefox.launch(headless=cfg.browser.headless)
                try:
                    page = await browser.new_page()
                    await page.close()
                finally:
                    await browser.close()
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


async def _check_searxng(cfg: Any) -> dict[str, Any]:
    import httpx

    url = cfg.search.searxng.url.rstrip("/") + "/search"
    try:
        async with httpx.AsyncClient(timeout=cfg.search.searxng.timeout_s) as client:
            response = await client.get(url, params={"q": "browsr doctor", "format": "json"})
            response.raise_for_status()
            data = response.json()
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise ValueError("SearXNG returned JSON without a results list")
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


def _check_cache(cfg: Any) -> dict[str, Any]:
    cache_path = Path(cfg.cache.path).expanduser()
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="browsr-doctor-", dir=cache_path.parent) as dirname:
            probe = Path(dirname) / "probe.sqlite"
            with sqlite3.connect(probe) as connection:
                connection.execute("CREATE TABLE probe (value INTEGER NOT NULL)")
                connection.execute("INSERT INTO probe VALUES (1)")
                if connection.execute("SELECT value FROM probe").fetchone() != (1,):
                    raise OSError("SQLite read-back failed")
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


async def _doctor(cfg: Any) -> dict[str, Any]:
    browser, searxng = await asyncio.gather(_check_browser(cfg), _check_searxng(cfg))
    cache = await asyncio.to_thread(_check_cache, cfg)
    checks = {"browser": browser, "searxng": searxng, "cache": cache}
    return {"ok": all(check["ok"] for check in checks.values()), "checks": checks}


async def _serve(cfg: Any, args: argparse.Namespace) -> None:
    from dataclasses import replace

    from browsr.service import Browsr

    overrides = {
        key: getattr(args, key)
        for key in ("transport", "host", "port", "mode")
        if getattr(args, key) is not None
    }
    if overrides:
        cfg = replace(cfg, server=replace(cfg.server, **overrides))
    app = Browsr(cfg)
    if cfg.server.transport == "stdio":
        from browsr.interfaces.mcp_server import run_stdio

        await run_stdio(app)
    else:
        import uvicorn

        from browsr.interfaces.rest import create_http_app

        server = uvicorn.Server(
            uvicorn.Config(
                create_http_app(app), host=cfg.server.host, port=cfg.server.port, log_config=None
            )
        )
        await server.serve()


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    cfg = _config(args.config)
    logging.basicConfig(level=cfg.log.level.upper(), stream=sys.stderr)
    if args.command == "serve":
        asyncio.run(_serve(cfg, args))
        return 0

    from browsr import render

    if args.command == "tools":
        from browsr.interfaces.schemas import export

        result = export(args.format, args.mode or cfg.server.mode, cfg.tools.descriptions)
    elif args.command == "search":
        result = asyncio.run(_call(cfg, "search", {"query": args.query}))
    elif args.command == "open":
        result = asyncio.run(_call(cfg, "open", {"url": args.url_or_id}))
    else:
        result = asyncio.run(_doctor(cfg))
    sys.stdout.write(render.dumps(result) + "\n")
    return 1 if args.command == "doctor" and not result["ok"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
