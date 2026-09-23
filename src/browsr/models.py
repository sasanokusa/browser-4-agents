from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class SearchResult:
    title: str
    url: str
    snippet: str


@dataclass(slots=True)
class RawPage:
    url: str
    final_url: str
    status: int
    content_type: str
    html: str | None = None
    body: bytes | None = None
    title: str = ""
    text: str | None = None


@dataclass(slots=True)
class Page:
    url: str
    final_url: str
    title: str
    markdown: str
    links: list[str]
    content_type: str
    created: int


@dataclass(frozen=True, slots=True)
class RefEntry:
    url: str
    part: int = 1


@dataclass(frozen=True, slots=True)
class Call:
    action: Literal["search", "open"]
    query: str | None = None
    url: str | None = None
    ref: int | None = None
    corrected: bool = False
