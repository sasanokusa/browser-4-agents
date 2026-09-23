"""Site-specific page adapters for APIs that expose cleaner page content."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterable, Mapping
from datetime import date
from typing import Protocol
from urllib.parse import quote, unquote, urlsplit

from ..errors import BrowsrError
from ..extract.links import rewrite
from ..extract.post import post
from ..models import Page

_PYPI_PROJECT_PATH = re.compile(r"/project/([^/]+)/(?:([^/]+)/)?$")


class Adapter(Protocol):
    name: str

    def match(self, url: str) -> bool: ...

    async def fetch(self, url: str) -> Page: ...


class PyPIAdapter:
    """Read project metadata from PyPI's JSON API and render it as Markdown."""

    name = "pypi"

    def __init__(self, http) -> None:
        self.http = http

    def match(self, url: str) -> bool:
        try:
            parsed = urlsplit(url)
            return (
                parsed.hostname is not None
                and parsed.hostname.lower() == "pypi.org"
                and _PYPI_PROJECT_PATH.fullmatch(parsed.path) is not None
            )
        except (TypeError, ValueError):
            return False

    async def fetch(self, url: str) -> Page:
        match = self._match(url)
        if match is None:
            raise BrowsrError("bad_input", detail="URL is not a PyPI project page")
        name, requested_version = (unquote(component) if component else None for component in match)

        # The page URL is checked by PageLoader as well, but this keeps direct
        # adapter calls subject to the same policy as its API request.
        await self.http.guard.check_url(url)
        api_path = f"/pypi/{quote(name, safe='')}"
        if requested_version is not None:
            api_path += f"/{quote(requested_version, safe='')}"
        api_url = f"https://pypi.org{api_path}/json"
        await self.http.guard.check_url(api_url)

        response = await self.http.request(api_url)
        if response.status == 404:
            raise BrowsrError("not_found", status=404, detail="PyPI project or release not found")
        if response.status != 200:
            raise BrowsrError(
                "fetch_failed",
                status=response.status,
                detail=f"PyPI returned HTTP {response.status}",
            )

        try:
            payload = json.loads(response.body)
            markdown, canonical_url, title = self._render(payload, requested_version)
        except BrowsrError:
            raise
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError, KeyError) as exc:
            raise BrowsrError("fetch_failed", detail=f"Invalid PyPI response: {exc}") from exc
        except Exception as exc:
            raise BrowsrError(
                "fetch_failed", detail=f"Could not extract PyPI metadata: {exc}"
            ) from exc

        markdown, links = rewrite(markdown, canonical_url, self._max_links())
        markdown = post(markdown)
        return Page(
            url=url,
            final_url=canonical_url,
            title=title,
            markdown=markdown,
            links=links,
            content_type="text/markdown",
            created=int(time.time()),
        )

    def _match(self, url: str) -> tuple[str, str | None] | None:
        if not self.match(url):
            return None
        parsed = urlsplit(url)
        found = _PYPI_PROJECT_PATH.fullmatch(parsed.path)
        if found is None:
            return None
        name, version = found.groups()
        return name, version

    def _max_links(self) -> int:
        cfg = getattr(self.http, "cfg", None)
        output = getattr(cfg, "output", None)
        value = getattr(output, "max_links", 2000)
        return max(0, int(value))

    @staticmethod
    def _render(payload: object, requested_version: str | None) -> tuple[str, str, str]:
        if not isinstance(payload, Mapping):
            raise ValueError("top-level JSON value must be an object")
        info = payload.get("info")
        if not isinstance(info, Mapping):
            raise ValueError("response is missing project info")
        name = info.get("name")
        version = info.get("version")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("project info is missing its name")
        if not isinstance(version, str) or not version.strip():
            raise ValueError("project info is missing its version")
        name = name.strip()
        version = version.strip()
        canonical_url = f"https://pypi.org/project/{quote(name, safe='')}/"
        if requested_version is not None:
            canonical_url += f"{quote(version, safe='')}/"
        title = f"{name} {version} · PyPI"

        lines = [f"# {name} {version}", "", _text(info.get("summary")), ""]
        released = _date_for_files(payload.get("urls"))
        license_name = (_text(info.get("license_expression")) or _text(info.get("license")))[:100]
        lines.extend(
            [
                f"- Released: {released or 'Unknown'}",
                f"- Requires Python: {_text(info.get('requires_python'))}",
                f"- License: {license_name}",
            ]
        )
        if info.get("yanked"):
            lines.append("- Yanked: yes")

        if requested_version is None:
            lines.extend(["", "## Recent releases", "| Version | Date |", "|---|---|"])
            for release_version, release_date in _recent_releases(payload.get("releases")):
                lines.append(f"| {release_version} | {release_date} |")

        lines.extend(["", "## Links"])
        project_urls = info.get("project_urls")
        if isinstance(project_urls, Mapping):
            for label, target in project_urls.items():
                if not isinstance(label, str) or not isinstance(target, str) or not target.strip():
                    continue
                lines.append(f"- [{label.strip()}]({target.strip()})")

        description = ""
        description_type = (
            _text(info.get("description_content_type")).split(";", 1)[0].strip().lower()
        )
        if description_type in {"text/markdown", "text/plain"}:
            description = _text(info.get("description"))[:8000]
        lines.extend(["", "## Description", description])
        return "\n".join(lines), canonical_url, title


class AdapterRegistry:
    """Registry for site adapters, using PyPI by default."""

    def __init__(self, http, adapters: Iterable[Adapter] | None = None) -> None:
        self.adapters: list[Adapter] = (
            list(adapters) if adapters is not None else [PyPIAdapter(http)]
        )

    def find(self, url: str) -> Adapter | None:
        return next((adapter for adapter in self.adapters if adapter.match(url)), None)


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _upload_date(value: object) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _date_for_files(files: object) -> str | None:
    if not isinstance(files, list):
        return None
    dates = [
        parsed
        for item in files
        if isinstance(item, Mapping)
        if (parsed := _upload_date(item.get("upload_time_iso_8601"))) is not None
    ]
    return min(dates).isoformat() if dates else None


def _recent_releases(releases: object) -> list[tuple[str, str]]:
    if not isinstance(releases, Mapping):
        return []
    dated: list[tuple[date, str]] = []
    for version, files in releases.items():
        if not isinstance(version, str) or not isinstance(files, list):
            continue
        file_dates: list[date] = []
        has_available_file = False
        for item in files:
            if not isinstance(item, Mapping):
                continue
            if not item.get("yanked"):
                has_available_file = True
            uploaded = _upload_date(item.get("upload_time_iso_8601"))
            if uploaded is not None:
                file_dates.append(uploaded)
        if has_available_file and file_dates:
            dated.append((min(file_dates), version))
    dated.sort(key=lambda pair: pair[0], reverse=True)
    return [(version, uploaded.isoformat()) for uploaded, version in dated[:10]]
