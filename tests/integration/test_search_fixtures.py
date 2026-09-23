"""Real Firefox parsing checks for saved search result pages; no network access."""

from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from browsr.models import SearchResult
from browsr.search.base import BackendBlocked
from browsr.search.ddg import parse as parse_ddg
from browsr.search.mojeek import parse as parse_mojeek

pytestmark = pytest.mark.browser
FIXTURES = Path(__file__).parents[1] / "fixtures" / "search"


class FakeResponse:
    status = 200


async def test_saved_search_pages_parse_in_firefox():
    async with async_playwright() as playwright:
        browser = await playwright.firefox.launch()
        try:
            page = await browser.new_page()

            await page.set_content((FIXTURES / "ddg.html").read_text())
            ddg = await parse_ddg(page, FakeResponse())
            assert ddg == [
                SearchResult(
                    title="Example guide",
                    url="https://example.com/guide",
                    snippet="Useful information about the guide.",
                )
            ]

            await page.set_content((FIXTURES / "mojeek.html").read_text())
            mojeek = await parse_mojeek(page, FakeResponse())
            assert mojeek == [
                SearchResult(
                    title="Mojeek result",
                    url="https://example.org/page",
                    snippet="A result from Mojeek.",
                )
            ]

            await page.set_content((FIXTURES / "ddg_blocked.html").read_text())
            with pytest.raises(BackendBlocked):
                await parse_ddg(page, FakeResponse())
        finally:
            await browser.close()
