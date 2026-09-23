"""REST and Streamable HTTP endpoints sharing one Browsr instance."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from browsr import render
from browsr.errors import hint

from .schemas import export


def _json(value: Any) -> Response:
    return Response(render.dumps(value), media_type="application/json; charset=utf-8")


def _bad_input() -> Response:
    return _json({"error": "bad_input", "hint": hint("bad_input")})


def _sid(request: Request) -> str:
    return request.headers.get("X-Session") or "default"


def create_http_app(app: Any) -> Starlette:
    """Create REST endpoints and MCP at /mcp; this ASGI app owns Browsr's lifespan."""
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

    from .mcp_server import create_server

    manager = StreamableHTTPSessionManager(app=create_server(app, transport="http"))

    class MCPASGI:
        async def __call__(self, scope, receive, send):
            await manager.handle_request(scope, receive, send)

    @asynccontextmanager
    async def lifespan(_asgi_app: Starlette):
        await app.start()
        try:
            async with manager.run():
                yield
        finally:
            await app.close()

    async def search(request: Request) -> Response:
        return _json(await app.call(_sid(request), "search", dict(request.query_params)))

    async def open_page(request: Request) -> Response:
        return _json(await app.call(_sid(request), "open", dict(request.query_params)))

    async def call(request: Request) -> Response:
        try:
            body = await request.json()
        except (ValueError, UnicodeDecodeError):
            return _bad_input()
        if not isinstance(body, dict) or not isinstance(body.get("name"), str):
            return _bad_input()
        arguments = body.get("arguments", {})
        if not isinstance(arguments, (dict, str)):
            return _bad_input()
        return _json(await app.call(_sid(request), body["name"], arguments))

    async def tools(request: Request) -> Response:
        fmt = request.query_params.get("format", "openai")
        mode = request.query_params.get("mode", app.cfg.server.mode)
        try:
            return _json(export(fmt, mode, app.cfg.tools.descriptions))
        except ValueError:
            return _bad_input()

    async def health(_request: Request) -> Response:
        search_backend = getattr(app, "search", None)
        states = (
            search_backend.backend_states
            if search_backend is not None and hasattr(search_backend, "backend_states")
            else app.breaker.state()
        )
        return _json({"ok": True, "browser": bool(app.pool.connected), "backends": states})

    return Starlette(
        routes=[
            Route("/search", search, methods=["GET"]),
            Route("/open", open_page, methods=["GET"]),
            Route("/call", call, methods=["POST"]),
            Route("/tools", tools, methods=["GET"]),
            Route("/health", health, methods=["GET"]),
            Route("/mcp", endpoint=MCPASGI()),
        ],
        lifespan=lifespan,
    )
