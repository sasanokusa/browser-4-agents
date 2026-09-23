from __future__ import annotations

import time
from dataclasses import dataclass, field

from .refs import RefTable


@dataclass(slots=True)
class Session:
    id: str
    refs: RefTable
    last_results: list[int] = field(default_factory=list)
    opened: set[str] = field(default_factory=set)
    last_access: float = field(default_factory=time.monotonic)


class SessionStore:
    def __init__(
        self, cfg=None, *, ttl_s: float | None = None, max_refs: int | None = None
    ) -> None:
        if cfg is not None:
            ttl_s = cfg.ttl_s if ttl_s is None else ttl_s
            max_refs = cfg.max_refs if max_refs is None else max_refs
        self.ttl_s = 3600 if ttl_s is None else ttl_s
        self.max_refs = 10000 if max_refs is None else max_refs
        self._sessions: dict[str, Session] = {}

    def get(self, sid: str) -> Session:
        now = time.monotonic()
        session = self._sessions.get(sid)
        if session is None:
            session = Session(sid, RefTable(self.max_refs), last_access=now)
            self._sessions[sid] = session
        else:
            session.last_access = now
        return session

    def sweep(self) -> list[str]:
        now = time.monotonic()
        expired = [
            sid
            for sid, session in self._sessions.items()
            if now - session.last_access >= self.ttl_s
        ]
        for sid in expired:
            del self._sessions[sid]
        return expired
