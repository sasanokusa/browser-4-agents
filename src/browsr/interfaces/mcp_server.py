"""MCP low-level adapter; application errors remain successful tool results."""

from __future__ import annotations

from typing import Any, Literal

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from browsr import render

from .schemas import tool_defs


def create_server(app: Any, *, transport: Literal["stdio", "http"] = "stdio") -> Server:
    """Build one MCP server. The transport owner manages ``app``'s lifespan."""

    # MCP 1.x exposes decorators while 2.x registers low-level callbacks in
    # Server's constructor. Both paths avoid schema validation of tool inputs.
    if hasattr(Server, "call_tool"):
        server = Server("browsr")

        @server.list_tools()
        async def list_tools_v1():
            return [
                types.Tool(
                    name=d["name"], description=d["description"], inputSchema=d["parameters"]
                )
                for d in tool_defs(app.cfg.server.mode, app.cfg.tools.descriptions)
            ]

        @server.call_tool(validate_input=False)
        async def call_tool_v1(name, arguments):
            sid = "stdio" if transport == "stdio" else str(id(server.request_context.session))
            result = await app.call(sid, name, arguments)
            return [types.TextContent(type="text", text=render.dumps(result))]

        return server

    async def list_tools(_ctx, _params) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=d["name"],
                    description=d["description"],
                    inputSchema=d["parameters"],
                )
                for d in tool_defs(app.cfg.server.mode, app.cfg.tools.descriptions)
            ]
        )

    async def call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        # Low-level handlers bypass tool-schema validation; Browsr normalizes the
        # argument names and values. The MCP envelope itself requires an object.
        sid = "stdio" if transport == "stdio" else str(id(ctx.session))
        result = await app.call(sid, params.name, params.arguments or {})
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=render.dumps(result))]
        )

    return Server("browsr", on_list_tools=list_tools, on_call_tool=call_tool)


async def run_stdio(app: Any) -> None:
    """Run a stdio MCP connection, starting and closing the service once."""
    server = create_server(app, transport="stdio")
    await app.start()
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
    finally:
        await app.close()
