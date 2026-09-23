"""Append-only JSON Lines call log."""

from __future__ import annotations

import asyncio
import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class CallLog:
    def __init__(self, cfg: Any):
        path = getattr(cfg, "calls_path", None) or getattr(cfg, "path", None)
        if path is None:
            raise ValueError("log configuration must provide calls_path")
        self.path = Path(path).expanduser()
        self._lock = threading.Lock()

    async def write(self, **record: Any) -> None:
        payload = dict(record)
        if not payload.get("ts"):
            payload["ts"] = datetime.now(UTC).isoformat()
        if "args_raw" in payload:
            raw = payload["args_raw"]
            if not isinstance(raw, str):
                raw = json.dumps(raw, ensure_ascii=False, default=str)
            payload["args_raw"] = raw[:500]
        await asyncio.to_thread(self._append, payload)

    def _append(self, payload: dict[str, Any]) -> None:
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
                try:
                    os.write(fd, (line + "\n").encode("utf-8"))
                finally:
                    os.close(fd)
        except OSError:
            # Logging must never corrupt a tool response or write diagnostics to stdout.
            return

    async def close(self) -> None:
        return None
