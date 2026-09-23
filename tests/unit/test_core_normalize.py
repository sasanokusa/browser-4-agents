import json

import pytest

from browsr.errors import BrowsrError, hint
from browsr.normalize import normalize


@pytest.mark.parametrize(
    ("tool", "raw", "action", "value"),
    [
        ("search", {"q": "  Firefox   webdriver  "}, "search", "Firefox webdriver"),
        ("open", {"url": "example.com/a."}, "open", "https://example.com/a"),
        ("search", {"query": "#3"}, "open", 3),
        ("web", {"input": "[3]"}, "open", 3),
        ("open", {"href": "https://example.com"}, "open", "https://example.com"),
        ("open", {"url": "weather tomorrow"}, "search", "weather tomorrow"),
        ("search", json.dumps({"keyword": '"日本語"'}), "search", "日本語"),
    ],
)
def test_normalize_cases(tool, raw, action, value):
    call = normalize(tool, raw)
    assert call.action == action
    assert (
        call.ref == value if isinstance(value, int) else (call.query == value or call.url == value)
    )


def test_normalize_handles_bad_input_and_hint_alternates():
    with pytest.raises(BrowsrError) as error:
        normalize("search", {"a": "x", "b": "y"})
    assert error.value.code == "bad_input"
    assert (
        hint("not_found", 404, 7) == "Page not found (HTTP 404). Try another result, e.g. open(7)."
    )


def test_normalize_corrected_tracks_coercion_and_cleaning():
    assert normalize("search", {"query": "plain words"}).corrected is False
    assert normalize("search", '{"query":"abc"}').corrected is False
    assert normalize("search", '{"q":"abc"}').corrected is True
    assert normalize("search", {"query": "  plain   words "}).corrected is True
    assert normalize("open", {"url": "https://example.com/a."}).corrected is True
    assert normalize("open", {"url": 4}).corrected is True
    assert normalize("search", json.dumps({"query": "words"})).corrected is False
    assert normalize("open", [["#4"]]).ref == 4


@pytest.mark.parametrize("raw", [float("nan"), float("inf"), float("-inf")])
def test_normalize_rejects_non_finite_numbers(raw):
    with pytest.raises(BrowsrError) as error:
        normalize("search", {"query": raw})
    assert error.value.code == "bad_input"


def test_normalize_rejects_cyclic_and_excessively_nested_values():
    cyclic = {}
    cyclic["query"] = cyclic
    with pytest.raises(BrowsrError):
        normalize("search", cyclic)

    deeply_nested = "leaf"
    for _ in range(40):
        deeply_nested = [deeply_nested]
    with pytest.raises(BrowsrError):
        normalize("search", deeply_nested)
