from __future__ import annotations

import json
import math
import re
from typing import Any

from .errors import BrowsrError
from .models import Call

TOOL_ALIASES = {
    "web_search": "search",
    "search_web": "search",
    "google": "search",
    "fetch": "open",
    "browse": "open",
    "open_url": "open",
    "visit": "open",
    "read": "open",
    "get": "open",
}
PRIMARY = {"search": "query", "open": "url", "web": "input"}
VALUE_KEYS = [
    "query",
    "q",
    "keyword",
    "keywords",
    "text",
    "input",
    "search",
    "url",
    "link",
    "href",
    "uri",
    "target",
    "id",
    "ref",
    "page",
]
QUOTE_PAIRS = [
    ('"', '"'),
    ("'", "'"),
    ("`", "`"),
    ("「", "」"),
    ("『", "』"),
    ("“", "”"),
    ("‘", "’"),
    ("<", ">"),
]
ID_RE = re.compile(r"^[#\[(]?\s*(\d{1,7})\s*[\])]?$")
URL_RE = re.compile(r"^https?://\S+$", re.I)
BARE_RE = re.compile(r"^(?:[a-z0-9-]+\.)+[a-z]{2,}(?::\d+)?(?:[/?#]\S*)?$", re.I)


def _coerce_args(tool: str, raw: Any) -> tuple[dict[str, Any], bool]:
    primary = PRIMARY[tool]
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {primary: raw}, True
        if isinstance(decoded, dict):
            return decoded, False
        return {primary: decoded}, True
    if isinstance(raw, dict):
        return raw, False
    return {primary: raw}, True


def _pick(args: dict[str, Any], tool: str) -> tuple[Any, bool]:
    primary = PRIMARY[tool]
    if primary in args:
        return args[primary], False
    for key in VALUE_KEYS:
        if key in args:
            return args[key], True
    if len(args) == 1:
        return next(iter(args.values())), True
    raise BrowsrError("bad_input")


def _value(value: Any, tool: str) -> tuple[str, bool]:
    corrected = False
    seen: set[int] = set()
    for _ in range(32):
        if isinstance(value, list):
            if not value:
                raise BrowsrError("bad_input")
            identity = id(value)
            if identity in seen:
                raise BrowsrError("bad_input")
            seen.add(identity)
            value = value[0]
            corrected = True
        if isinstance(value, dict):
            identity = id(value)
            if identity in seen:
                raise BrowsrError("bad_input")
            seen.add(identity)
            value, changed = _pick(value, tool)
            corrected |= changed
            continue
        if not isinstance(value, list):
            break
    else:
        raise BrowsrError("bad_input")
    if isinstance(value, bool) or value is None:
        raise BrowsrError("bad_input")
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise BrowsrError("bad_input")
        value = str(int(value))
        corrected = True
    elif not isinstance(value, str):
        raise BrowsrError("bad_input")
    stripped = value.strip()
    corrected |= stripped != value
    value = stripped
    while len(value) >= 2 and any(
        value.startswith(left) and value.endswith(right) for left, right in QUOTE_PAIRS
    ):
        left, right = next(
            pair for pair in QUOTE_PAIRS if value.startswith(pair[0]) and value.endswith(pair[1])
        )
        value = value[len(left) : len(value) - len(right)].strip()
        corrected = True
    if not value:
        raise BrowsrError("bad_input")
    return value, corrected


def clean_url(value: str) -> str:
    value = value.strip()
    while value and value[-1] in ".,;:。、":
        value = value[:-1]
    if value.endswith(")") and "(" not in value:
        value = value[:-1]
    return value


def normalize(tool: str, raw: Any) -> Call:
    tool = str(tool).strip().lower()
    corrected = False
    if tool in TOOL_ALIASES:
        tool = TOOL_ALIASES[tool]
        corrected = True
    if tool not in PRIMARY:
        raise BrowsrError("bad_input")
    args, coercion = _coerce_args(tool, raw)
    value, picked = _pick(args, tool)
    value, value_correction = _value(value, tool)
    corrected |= coercion or picked or value_correction
    match = ID_RE.fullmatch(value)
    if match:
        kind, payload = "id", int(match.group(1))
        corrected |= value != str(payload)
    elif URL_RE.fullmatch(cleaned_url := clean_url(value)):
        kind, payload = "url", cleaned_url
        corrected |= cleaned_url != value
    elif not any(c.isspace() for c in value) and BARE_RE.fullmatch(cleaned_url := clean_url(value)):
        kind, payload = "url", "https://" + cleaned_url
        corrected = True
    else:
        normalized_text = re.sub(r"\s+", " ", value)[:400]
        kind, payload = "text", normalized_text
        corrected |= normalized_text != value
    if tool == "search":
        if kind == "id":
            return Call("open", ref=payload, corrected=True)
        if kind == "url":
            return Call("open", url=payload, corrected=True)
        return Call("search", query=payload, corrected=corrected)
    if tool == "open":
        if kind == "text":
            return Call("search", query=payload, corrected=True)
        if kind == "id":
            return Call("open", ref=payload, corrected=corrected)
        return Call("open", url=payload, corrected=corrected)
    if kind == "id":
        return Call("open", ref=payload, corrected=corrected)
    if kind == "url":
        return Call("open", url=payload, corrected=corrected)
    return Call("search", query=payload, corrected=corrected)
