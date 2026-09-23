"""Per-backend circuit breaker."""

from __future__ import annotations

import time
from typing import Any


class Breaker:
    def __init__(self, cooldown_s: float, fail_threshold: int):
        if fail_threshold < 1:
            raise ValueError("fail_threshold must be at least 1")
        self.cooldown_s = float(cooldown_s)
        self.fail_threshold = int(fail_threshold)
        self.fails: dict[Any, int] = {}
        self.open_until: dict[Any, float] = {}

    def available(self, key: Any) -> bool:
        return time.monotonic() >= self.open_until.get(key, 0.0)

    def success(self, key: Any) -> None:
        self.fails[key] = 0

    def fail(self, key: Any) -> None:
        count = self.fails.get(key, 0) + 1
        if count >= self.fail_threshold:
            self.trip(key)
        else:
            self.fails[key] = count

    def trip(self, key: Any) -> None:
        self.open_until[key] = time.monotonic() + self.cooldown_s
        self.fails[key] = 0

    def state(self) -> dict[str, str]:
        keys = self.fails.keys() | self.open_until.keys()
        return {str(key): ("ok" if self.available(key) else "cooldown") for key in keys}
