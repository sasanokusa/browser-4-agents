"""The compact tool definitions shared by all transports."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

DEFS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "standard": (
        ("search", "Search the web. Returns numbered results.", "query"),
        ("open", "Open a URL or a number from results/links. Returns page text.", "url"),
    ),
    "single": (("web", "Search words, or open a URL/number.", "input"),),
}


def tool_defs(mode: str, overrides: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """Return independent definitions so callers cannot mutate the shared defaults."""
    if mode not in DEFS:
        raise ValueError(f"Unknown tool mode: {mode}")
    overrides = overrides or {}
    out = []
    for name, description, parameter in DEFS[mode]:
        out.append(
            {
                "name": name,
                "description": overrides.get(name, description),
                "parameters": {
                    "type": "object",
                    "properties": {parameter: {"type": "string"}},
                    "required": [parameter],
                },
            }
        )
    return out


def export(fmt: str, mode: str, overrides: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """Export definitions to OpenAI, Anthropic, or MCP shape."""
    if fmt not in ("openai", "anthropic", "mcp"):
        raise ValueError(f"Unknown tool format: {fmt}")
    definitions = tool_defs(mode, overrides)
    if fmt == "openai":
        return [{"type": "function", "function": d} for d in definitions]
    if fmt == "anthropic":
        return [
            {"name": d["name"], "description": d["description"], "input_schema": d["parameters"]}
            for d in definitions
        ]
    return [
        {"name": d["name"], "description": d["description"], "inputSchema": d["parameters"]}
        for d in definitions
    ]
