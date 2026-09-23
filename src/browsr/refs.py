from __future__ import annotations

from collections import OrderedDict

from .models import RefEntry
from .urlnorm import normalize


class RefTable:
    def __init__(self, max_entries: int) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self.max_entries = max_entries
        self._next = 1
        self._by_id: OrderedDict[int, RefEntry] = OrderedDict()
        self._url: dict[str, int] = {}
        self._part: dict[tuple[str, int], int] = {}

    def for_url(self, url: str) -> int:
        key = normalize(url)
        found = self._url.get(key)
        if found is not None:
            return found
        return self._allocate(RefEntry(url=url), key=key)

    def alias(self, url: str, ref: int) -> None:
        entry = self._by_id.get(ref)
        if entry is None:
            raise KeyError(ref)
        key = normalize(url)
        self._url[key] = ref

    def for_part(self, url: str, part: int) -> int:
        if part < 2:
            raise ValueError("part must be >= 2")
        key = normalize(url)
        existing = self._part.get((key, part))
        if existing is not None:
            return existing
        ref = self._allocate(RefEntry(url=url, part=part))
        self._part[(key, part)] = ref
        return ref

    def get(self, ref: int) -> RefEntry | None:
        return self._by_id.get(ref)

    def _allocate(self, entry: RefEntry, *, key: str | None = None) -> int:
        ref = self._next
        self._next += 1
        self._by_id[ref] = entry
        if key is not None:
            self._url[key] = ref
        self._evict_if_needed()
        return ref

    def _evict_if_needed(self) -> None:
        if len(self._by_id) <= self.max_entries:
            return
        count = max(1, (self.max_entries + 9) // 10)
        evicted = set(list(self._by_id)[:count])
        for ref in evicted:
            self._by_id.pop(ref, None)
        self._url = {key: ref for key, ref in self._url.items() if ref not in evicted}
        self._part = {key: ref for key, ref in self._part.items() if ref not in evicted}
