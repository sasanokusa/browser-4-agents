from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from browsr.errors import BrowsrError
from browsr.fetch.adapters import AdapterRegistry, PyPIAdapter
from browsr.models import Page

FIXTURES = Path(__file__).parents[1] / "fixtures" / "pypi"


@dataclass
class HttpResponse:
    status: int
    final_url: str
    content_type: str
    body: bytes


class Guard:
    def __init__(self):
        self.checked: list[str] = []

    async def check_url(self, url: str) -> None:
        self.checked.append(url)


class FakeHttp:
    def __init__(self, response: HttpResponse):
        self.response = response
        self.guard = Guard()
        self.cfg = SimpleNamespace(output=SimpleNamespace(max_links=20))
        self.requested: list[str] = []

    async def request(self, url: str) -> HttpResponse:
        self.requested.append(url)
        return self.response


def fixture_body(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def adapter_for(body: bytes, status: int = 200) -> tuple[PyPIAdapter, FakeHttp]:
    http = FakeHttp(HttpResponse(status, "https://pypi.org/api/", "application/json", body))
    return PyPIAdapter(http), http


def test_registry_matches_only_exact_pypi_project_paths():
    registry = AdapterRegistry(object())
    assert isinstance(registry.find("https://pypi.org/project/requests/"), PyPIAdapter)
    assert registry.find("https://pypi.org/project/requests/2.31.0/") is not None
    for url in (
        "https://www.pypi.org/project/requests/",
        "https://notpypi.org/project/requests/",
        "https://pypi.org/project/requests",
        "https://pypi.org/project/requests/2.31.0/files/",
        "https://pypi.org/simple/requests/",
        "not a URL",
    ):
        assert registry.find(url) is None


@pytest.mark.asyncio
async def test_project_fetch_renders_recent_releases_and_rewrites_links():
    adapter, http = adapter_for(fixture_body("project.json"))
    url = "https://pypi.org/project/Demo_Pkg/"
    page = await adapter.fetch(url)

    assert isinstance(page, Page)
    assert page.url == url
    assert page.final_url == "https://pypi.org/project/Demo-Pkg/"
    assert page.title == "Demo-Pkg 2.0 · PyPI"
    assert page.content_type == "text/markdown"
    assert "- Released: 2025-02-01" in page.markdown
    assert "- Requires Python: >=3.10" in page.markdown
    assert "- License: MIT" in page.markdown
    assert "[Homepage](@L0)" in page.markdown
    assert "[Source](@L1)" in page.markdown
    assert "[documentation](@L2)" in page.markdown
    assert page.links == [
        "https://demo.example/",
        "https://code.example/demo",
        "https://docs.example/guide",
    ]
    recent = page.markdown.split("## Recent releases\n", 1)[1].split("\n\n## Links", 1)[0]
    versions = [line.split("|", 2)[1].strip() for line in recent.splitlines()[2:]]
    assert versions == ["12.0", "11.0", "10.0", "9.0", "8.0", "7.0", "6.0", "5.0", "3.0", "2.0"]
    assert "| 4.0 |" not in recent  # Yanked release.
    assert "| 1.0 |" not in recent  # Older than the ten most recent releases.
    assert http.requested == ["https://pypi.org/pypi/Demo_Pkg/json"]
    assert http.guard.checked == [url, "https://pypi.org/pypi/Demo_Pkg/json"]


@pytest.mark.asyncio
async def test_version_fetch_uses_version_api_and_marks_yanked_without_recent_section():
    adapter, http = adapter_for(fixture_body("version_yanked.json"))
    page = await adapter.fetch("https://pypi.org/project/Demo-Pkg/1.0.1/")

    assert page.final_url == "https://pypi.org/project/Demo-Pkg/1.0.1/"
    assert page.title == "Demo-Pkg 1.0.1 · PyPI"
    assert "- Released: 2024-06-01" in page.markdown
    assert "- License: BSD-3-Clause" in page.markdown
    assert "- Yanked: yes" in page.markdown
    assert "## Recent releases" not in page.markdown
    assert "Plain description." in page.markdown
    assert http.requested == ["https://pypi.org/pypi/Demo-Pkg/1.0.1/json"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "code"),
    [(404, "not_found"), (403, "fetch_failed"), (500, "fetch_failed")],
)
async def test_status_mapping(status: int, code: str):
    adapter, _ = adapter_for(fixture_body("project.json"), status)
    with pytest.raises(BrowsrError) as caught:
        await adapter.fetch("https://pypi.org/project/Demo-Pkg/")
    assert caught.value.code == code
    if status == 404:
        assert caught.value.status == 404
    else:
        assert f"HTTP {status}" in caught.value.detail


@pytest.mark.asyncio
async def test_invalid_json_and_malformed_metadata_become_fetch_failed_with_detail():
    adapter, _ = adapter_for(fixture_body("invalid.json"))
    with pytest.raises(BrowsrError) as caught:
        await adapter.fetch("https://pypi.org/project/Demo-Pkg/")
    assert caught.value.code == "fetch_failed"
    assert caught.value.detail

    malformed, _ = adapter_for(json.dumps({"info": {"name": "Demo"}}).encode())
    with pytest.raises(BrowsrError) as caught:
        await malformed.fetch("https://pypi.org/project/Demo-Pkg/")
    assert caught.value.code == "fetch_failed"
    assert "version" in caught.value.detail


@pytest.mark.asyncio
async def test_description_type_and_length_are_restricted():
    payload = json.loads(fixture_body("project.json"))
    payload["info"]["description_content_type"] = "application/rst"
    payload["info"]["description"] = "should not appear"
    payload["info"]["license_expression"] = "L" * 120
    adapter, _ = adapter_for(json.dumps(payload).encode())
    page = await adapter.fetch("https://pypi.org/project/Demo-Pkg/")
    assert "should not appear" not in page.markdown
    license_line = next(
        line for line in page.markdown.splitlines() if line.startswith("- License:")
    )
    assert len(license_line.removeprefix("- License: ")) == 100

    payload["info"]["description_content_type"] = "text/plain"
    payload["info"]["description"] = "x" * 8001
    adapter, _ = adapter_for(json.dumps(payload).encode())
    page = await adapter.fetch("https://pypi.org/project/Demo-Pkg/")
    description = page.markdown.split("## Description\n", 1)[1]
    assert len(description) == 8000


@pytest.mark.asyncio
async def test_invalid_adapter_path_is_rejected_before_request():
    adapter, http = adapter_for(fixture_body("project.json"))
    with pytest.raises(BrowsrError) as caught:
        await adapter.fetch("https://pypi.org/simple/demo/")
    assert caught.value.code == "bad_input"
    assert http.requested == []
