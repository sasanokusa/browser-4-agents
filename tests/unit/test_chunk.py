from dataclasses import replace

from browsr.chunk import Chunker
from browsr.config import OutputConfig
from browsr.models import Page


def chunker(budget):
    return Chunker(OutputConfig(max_tokens=budget, envelope_reserve=0))


def test_paragraphs_obey_budget_and_preserve_sentences():
    md = "# Heading\n\n" + ("This is one sentence. This is another sentence. " * 12).strip()
    splitter = chunker(35)
    chunks = splitter.split(md)
    assert len(chunks) > 1
    assert all(splitter.cost_fn(chunk) <= 35 for chunk in chunks)
    assert chunks[0].startswith("# Heading")
    assert all(not chunk.rstrip().endswith("# Heading") for chunk in chunks)
    assert "".join(chunks).count("This is one sentence.") == 12
    assert chunks == splitter.split(md)


def test_code_fences_and_tables_reopen_with_headers():
    code = "```python\n" + "\n".join(f"value_{i} = {i}" for i in range(16)) + "\n```"
    table = "| Name | Value |\n| --- | --- |\n" + "\n".join(
        f"| Item {i} | {i} |" for i in range(20)
    )
    splitter = chunker(35)
    chunks = splitter.split(code + "\n\n" + table)
    assert len(chunks) > 3
    assert all(splitter.cost_fn(chunk) <= 35 for chunk in chunks)
    for part in chunks:
        if part.startswith("```"):
            assert part.endswith("```")
        if part.startswith("| Name"):
            assert part.splitlines()[1] == "| --- | --- |"
    assert sum(part.count("value_") for part in chunks) == 16
    assert sum(part.count("| Item ") for part in chunks) == 20


def test_custom_cost_and_lru_copy():
    output = OutputConfig(max_tokens=45, envelope_reserve=0)
    splitter = Chunker(output, cost_fn=len)
    page = Page("u", "u", "title", "first paragraph " * 10, [], "text/plain", 100)
    chunks = splitter.split_cached(page)
    assert all(len(part) <= 45 for part in chunks)
    chunks.clear()
    assert splitter.split_cached(page)
    for created in range(101, 170):
        splitter.split_cached(replace(page, created=created))
    assert len(splitter._cache) == 64


def test_hard_split_keeps_link_placeholders_atomic():
    splitter = Chunker(OutputConfig(max_tokens=24, envelope_reserve=0), cost_fn=len)
    markdown = "A long lead in the article [linked label](@L0) and more words after it."
    chunks = splitter.split(markdown)
    assert all(len(chunk) <= 24 for chunk in chunks)
    assert sum(chunk.count("[linked label](@L0)") for chunk in chunks) == 1
    assert sum(chunk.count("@L0") for chunk in chunks) == 1
    assert "".join(chunks) == markdown


def test_oversize_link_degrades_to_label_and_empty_input_has_one_part():
    splitter = Chunker(OutputConfig(max_tokens=10, envelope_reserve=0), cost_fn=len)
    chunks = splitter.split("[very long label](@L12)")
    assert "@L" not in "".join(chunks)
    assert "".join(chunks) == "very long label"
    assert splitter.split("") == [""]
