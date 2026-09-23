from __future__ import annotations

from typing import Protocol

from browsr.models import SearchResult


class BackendBlocked(Exception):
    """Raised when a provider presents a challenge or rejects our request."""


class BackendTimeout(Exception):
    """Raised when a provider times out while searching."""


class Backend(Protocol):
    name: str
    interval_s: float
    timeout_s: float

    async def search(self, query: str, n: int) -> list[SearchResult]: ...
