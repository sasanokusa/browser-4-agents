import json

from browsr.models import Page, SearchResult
from browsr.refs import RefTable
from browsr.render import dumps, error, links, open, search
from browsr.urlnorm import normalize


def test_url_normalization_removes_tracking_and_canonicalizes_host():
    assert (
        normalize("HTTPS://ExAmPle.com:443/a?utm_source=x&b=2&a=1#frag")
        == "https://example.com/a?b=2&a=1"
    )
    assert normalize("https://bücher.example") == "https://xn--bcher-kva.example/"


def test_render_search_open_links_and_json():
    result = SearchResult(
        "  " + "見" * 155 + "  ",
        "https://example.com/",
        "  line one\n\tline   two and more  ",
    )
    out = search("q", [(7, result)], snippet_chars=15)
    assert out["results"] == [
        {
            "id": 7,
            "title": "見" * 150,
            "url": "https://example.com/",
            "snippet": "line one line t…",
        }
    ]
    assert out["tip"] == "open(7) to read a result"
    refs = RefTable(20)
    markdown = "See [first](@L0) and [second](@L1)."
    urls = [result.url, "https://other.example/"]
    assert links(markdown, urls, refs) == (
        f"See [first]({refs.for_url(urls[0])}) and [second]({refs.for_url(urls[1])})."
    )
    assert links(markdown, urls, refs, "url") == (
        "See [first](https://example.com/) and [second](https://other.example/)."
    )
    page = Page(result.url, result.url, result.title, markdown, [result.url], "text/html", 0)
    assert open(7, page, "Body", 2, 3, 9)["part"] == "2/3"
    assert open(7, page, "Body", 2, 3, 9)["next"] == 9
    assert json.loads(dumps(out))["query"] == "q"
    assert error("timeout")["error"] == "timeout"


def test_search_snippet_no_truncation_and_empty_tip():
    item = SearchResult(" title ", "https://example.com", "already short")
    assert search("q", [(12, item)]) == {
        "query": "q",
        "results": [
            {"id": 12, "title": "title", "url": "https://example.com", "snippet": "already short"}
        ],
        "tip": "open(12) to read a result",
    }
    assert search("q", [], tip=True)["tip"] == "No results. Try different words."
