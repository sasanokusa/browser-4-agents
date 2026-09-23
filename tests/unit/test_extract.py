import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pymupdf
import pytest

from browsr.config import Config, ExtractConfig, OutputConfig
from browsr.errors import BrowsrError
from browsr.extract import CONSENT_JS, EXTRACT_FN, to_page
from browsr.extract import pdf as pdf_extract
from browsr.extract.links import rewrite
from browsr.extract.post import post
from browsr.models import RawPage

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "extract"


def raw(content_type="text/html", **kwargs):
    return RawPage(
        url="https://example.org/original",
        final_url="https://example.org/articles/story",
        status=200,
        content_type=content_type,
        **kwargs,
    )


def test_html_conversion_and_link_ids():
    page = to_page(
        raw(
            title="  Story  ",
            html=(
                '<article><h1>Heading</h1><p>Body <a href="../next">Next</a> '
                '<a href="https://example.org/next?utm_source=x">Again</a>'
                '<img alt="diagram" src="/image.png"></p><button>Buy</button></article>'
            ),
        ),
        Config(),
    )
    assert page.title == "Story"
    assert page.markdown.startswith("# Heading")
    assert "[Next](@L0)" in page.markdown
    assert "[Again](@L0)" in page.markdown
    assert "diagram" in page.markdown
    assert "<button>" not in page.markdown
    assert page.links == ["https://example.org/next"]


def test_markdown_link_parser_handles_parentheses_code_images_and_limits():
    md = (
        '[One](../a(b)c "ignored") [Two](../a(b)c) [Bad](javascript:alert(1)) '
        "![image](https://example.org/img.png) `[%](../literal)`\n"
        "```md\n[code](../literal)\n```\n"
        "[Extra](../extra)"
    )
    output, links = rewrite(md, "https://example.org/docs/page", 1)
    assert links == ["https://example.org/a(b)c"]
    assert output.count("@L0") == 2
    assert "Bad" in output and "javascript:" not in output
    assert "![image]" not in output
    assert "`[%](../literal)`" in output
    assert "[code](../literal)" in output
    assert output.endswith("Extra")


def test_post_removes_invisible_chars_and_blank_lines():
    assert post(" \u200bA\u00a0B  \n\n\n\u200cC \ufeff ") == "A B\n\nC"


def test_plain_charset_json_and_empty_page():
    cfg = Config()
    plain = to_page(raw("text/plain; charset=iso-8859-1", body=b"caf\xe9"), cfg)
    assert plain.markdown == "café"
    assert plain.title == "story"
    json_page = to_page(raw("application/problem+json", body='{"x":1,"text":"日本"}'.encode()), cfg)
    assert json_page.markdown == '```json\n{\n "x": 1,\n "text": "日本"\n}\n```'
    empty = to_page(raw("text/plain", body=b""), cfg)
    assert empty.markdown == "(empty page)"


def test_pdf_pages_and_metadata_title():
    document = pymupdf.open()
    document.set_metadata({"title": "PDF title"})
    for number in range(3):
        page = document.new_page()
        page.insert_text((72, 72), f"This is the substantial content of page {number + 1}. " * 3)
    body = document.tobytes()
    document.close()
    cfg = replace(Config(), extract=ExtractConfig(max_pdf_pages=2))
    page = to_page(raw("application/pdf", body=body), cfg)
    assert page.title == "PDF title"
    assert "### Page 1" in page.markdown
    assert "### Page 2" in page.markdown
    assert "### Page 3" not in page.markdown
    assert page.links == []


@pytest.mark.parametrize("name", ["news", "blog", "wiki", "list", "spa"])
def test_saved_html_fixture(name):
    html = (FIXTURES / f"{name}.html").read_text(encoding="utf-8")
    page = to_page(raw(html=html), Config())
    assert page.markdown
    assert page.markdown != "(empty page)"


def test_saved_pdf_fixture():
    page = to_page(raw("application/pdf", body=(FIXTURES / "report.pdf").read_bytes()), Config())
    assert page.title == "Annual field report"
    assert "### Page 1" in page.markdown


def test_concurrent_pdf_requests_use_one_extraction_thread(monkeypatch):
    body = (FIXTURES / "report.pdf").read_bytes()
    original = pdf_extract._extract_on_worker
    state_lock = threading.Lock()
    callers_ready = threading.Barrier(8)
    active = 0
    peak = 0
    worker_threads: set[int] = set()

    def tracked(body: bytes, max_pages: int):
        nonlocal active, peak
        with state_lock:
            active += 1
            peak = max(peak, active)
            worker_threads.add(threading.get_ident())
        try:
            # Keep an extraction active while the other callers dispatch.
            time.sleep(0.02)
            return original(body, max_pages)
        finally:
            with state_lock:
                active -= 1

    def request():
        callers_ready.wait(timeout=5)
        return to_page(raw("application/pdf", body=body), Config())

    monkeypatch.setattr(pdf_extract, "_extract_on_worker", tracked)
    with ThreadPoolExecutor(max_workers=8) as callers:
        pages = list(callers.map(lambda _: request(), range(8)))
    assert all(page.title == "Annual field report" for page in pages)
    assert peak == 1
    assert len(worker_threads) == 1
    assert threading.get_ident() not in worker_threads


def test_unsupported_and_pdf_without_text():
    with pytest.raises(BrowsrError) as error:
        to_page(raw("image/png", body=b"abc"), Config())
    assert error.value.code == "unsupported"
    document = pymupdf.open()
    document.new_page()
    body = document.tobytes()
    document.close()
    with pytest.raises(BrowsrError) as error:
        to_page(raw("application/pdf", body=body), Config())
    assert error.value.code == "unsupported"
    with pytest.raises(BrowsrError) as error:
        to_page(raw("application/pdf", body=b"this is not a PDF"), Config())
    assert error.value.code == "unsupported"


def test_markdown_limit_and_browser_assets():
    cfg = replace(Config(), output=OutputConfig(max_markdown_chars=40))
    page = to_page(raw("text/plain", body=b"x" * 100), cfg)
    assert len(page.markdown) <= 40
    assert page.markdown.endswith("(truncated)")
    assert "function Readability" in EXTRACT_FN
    assert "new Readability" in EXTRACT_FN
    assert "document.querySelectorAll" in CONSENT_JS
