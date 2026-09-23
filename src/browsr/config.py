from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ServerConfig:
    mode: str = "standard"
    transport: str = "stdio"
    host: str = "127.0.0.1"
    port: int = 8765


@dataclass(frozen=True, slots=True)
class ToolsConfig:
    tip: bool = True
    link_style: str = "id"
    descriptions: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OutputConfig:
    max_tokens: int = 3000
    envelope_reserve: int = 150
    results: int = 8
    snippet_chars: int = 200
    max_markdown_chars: int = 300000
    max_links: int = 2000


@dataclass(frozen=True, slots=True)
class BrowserConfig:
    engine: str = "firefox"
    headless: bool = True
    max_tabs: int = 4
    max_contexts: int = 8
    timeout_ms: int = 15000
    settle_ms: int = 2000
    locale: str = "ja-JP"
    timezone: str = "Asia/Tokyo"
    block: list[str] = field(default_factory=lambda: ["image", "font", "media"])


@dataclass(frozen=True, slots=True)
class FetchConfig:
    mode: str = "browser"
    min_text_chars: int = 500
    max_bytes: int = 20000000


@dataclass(frozen=True, slots=True)
class ExtractConfig:
    min_readability_chars: int = 200
    max_pdf_pages: int = 300


@dataclass(frozen=True, slots=True)
class SearxngConfig:
    url: str = "http://127.0.0.1:8888"
    interval_s: float = 0.5
    timeout_s: float = 8.0


@dataclass(frozen=True, slots=True)
class DdgConfig:
    region: str = "jp-jp"
    interval_s: float = 3.0
    timeout_s: float = 12.0


@dataclass(frozen=True, slots=True)
class MojeekConfig:
    interval_s: float = 3.0
    timeout_s: float = 12.0


@dataclass(frozen=True, slots=True)
class SearchConfig:
    backends: list[str] = field(default_factory=lambda: ["searxng", "ddg", "mojeek"])
    language: str = "auto"
    cooldown_s: int = 600
    fail_threshold: int = 2
    max_wait_s: float = 5.0
    searxng: SearxngConfig = field(default_factory=SearxngConfig)
    ddg: DdgConfig = field(default_factory=DdgConfig)
    mojeek: MojeekConfig = field(default_factory=MojeekConfig)


@dataclass(frozen=True, slots=True)
class CacheConfig:
    path: str = "~/.cache/browsr/cache.sqlite"
    search_ttl_s: int = 86400
    page_ttl_s: int = 3600
    max_pages: int = 5000


@dataclass(frozen=True, slots=True)
class SecurityConfig:
    allow_private: bool = False
    domain_interval_s: float = 1.0
    respect_robots: bool = False


@dataclass(frozen=True, slots=True)
class SessionConfig:
    ttl_s: int = 3600
    max_refs: int = 10000


@dataclass(frozen=True, slots=True)
class LogConfig:
    calls_path: str = "~/.local/state/browsr/calls.jsonl"
    level: str = "INFO"


@dataclass(frozen=True, slots=True)
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    fetch: FetchConfig = field(default_factory=FetchConfig)
    extract: ExtractConfig = field(default_factory=ExtractConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    log: LogConfig = field(default_factory=LogConfig)


def _merge(dst: dict[str, Any], src: dict[str, Any]) -> None:
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            _merge(dst[key], value)
        else:
            dst[key] = value


def _coerce(value: Any, exemplar: Any, path: str) -> Any:
    if isinstance(exemplar, bool):
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"true", "1", "yes", "on"}:
            return True
        if text in {"false", "0", "no", "off"}:
            return False
        raise ValueError(f"{path} must be a boolean")
    if isinstance(exemplar, int) and not isinstance(exemplar, bool):
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{path} must be an integer") from exc
    if isinstance(exemplar, float):
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{path} must be a number") from exc
    if isinstance(exemplar, list):
        if isinstance(value, str):
            return [v.strip() for v in value.split(",") if v.strip()]
        if isinstance(value, (tuple, list)):
            return list(value)
        raise ValueError(f"{path} must be a list")
    if isinstance(exemplar, str):
        return str(value)
    return value


def _build(cls: type, data: dict[str, Any], defaults: Any, path: str = "") -> Any:
    known = {f.name: f for f in fields(cls)}
    kwargs: dict[str, Any] = {}
    for key, value in data.items():
        if key not in known:
            raise ValueError(f"Unknown configuration key: {path + key}")
    for key, f in known.items():
        if key not in data:
            continue
        value = data[key]
        default = getattr(defaults, key)
        # postponed annotations are strings, so detect nested defaults structurally.
        if is_dataclass(default):
            if not isinstance(value, dict):
                raise ValueError(f"{path + key} must be a table")
            kwargs[key] = _build(type(default), value, default, path + key + ".")
        else:
            kwargs[key] = _coerce(value, default, path + key)
    return cls(**kwargs)


def load_config(
    path: str | os.PathLike[str] | None = None, environ: dict[str, str] | None = None
) -> Config:
    """Load defaults, an optional TOML file, then BROWSR_* environment overrides."""
    candidates: list[Path] = []
    if path is not None:
        candidates.append(Path(path).expanduser())
    else:
        candidates.extend([Path.cwd() / "browsr.toml", Path.home() / ".config/browsr/browsr.toml"])
    raw: dict[str, Any] = {}
    for candidate in candidates:
        if candidate.is_file():
            with candidate.open("rb") as stream:
                _merge(raw, tomllib.load(stream))
            break
        if path is not None:
            raise FileNotFoundError(candidate)
    env = os.environ if environ is None else environ
    defaults = Config()
    skeleton = _as_dict(defaults)
    # type each environment value against defaults before layering it over TOML.
    env_overrides: dict[str, Any] = {}
    _env_nested(env_overrides, env, skeleton)
    _merge(raw, env_overrides)
    cfg = _build(Config, raw, defaults)
    _validate(cfg)
    return cfg


def _validate(cfg: Config) -> None:
    if cfg.server.mode not in {"standard", "single"}:
        raise ValueError("server.mode must be 'standard' or 'single'")
    if cfg.server.transport not in {"stdio", "http"}:
        raise ValueError("server.transport must be 'stdio' or 'http'")
    if cfg.browser.engine not in {"firefox", "camoufox"}:
        raise ValueError("browser.engine must be 'firefox' or 'camoufox'")
    if cfg.fetch.mode not in {"browser", "auto"}:
        raise ValueError("fetch.mode must be 'browser' or 'auto'")
    if cfg.tools.link_style not in {"id", "url"}:
        raise ValueError("tools.link_style must be 'id' or 'url'")
    if not 1000 <= cfg.output.max_tokens <= 8000:
        raise ValueError("output.max_tokens must be between 1000 and 8000")
    if not 0 <= cfg.output.envelope_reserve < cfg.output.max_tokens:
        raise ValueError("output.envelope_reserve must be non-negative and less than max_tokens")
    if (
        min(
            cfg.browser.max_tabs,
            cfg.browser.max_contexts,
            cfg.session.max_refs,
            cfg.output.results,
            cfg.output.max_links,
            cfg.cache.max_pages,
        )
        < 1
    ):
        raise ValueError(
            "browser limits, output counts, cache.max_pages, and session.max_refs must be positive"
        )


def _as_dict(obj: Any) -> dict[str, Any]:
    return {
        f.name: _as_dict(getattr(obj, f.name))
        if is_dataclass(getattr(obj, f.name))
        else getattr(obj, f.name)
        for f in fields(obj)
    }


def _env_nested(output: dict[str, Any], env: dict[str, str], defaults: dict[str, Any]) -> None:
    for name, value in env.items():
        if not name.startswith("BROWSR_"):
            continue
        path = name[7:].lower().split("__")
        node = output
        ref: Any = defaults
        for part in path[:-1]:
            if part not in ref or not isinstance(ref[part], dict):
                break
            node = node.setdefault(part, {})
            ref = ref[part]
        else:
            key = path[-1]
            if key in ref:
                node[key] = _coerce(value, ref[key], ".".join(path))
