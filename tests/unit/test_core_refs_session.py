from browsr.config import SessionConfig
from browsr.refs import RefTable
from browsr.session import SessionStore


def test_reference_alias_parts_and_eviction():
    refs = RefTable(10)
    first = refs.for_url("https://example.com/a#one")
    assert refs.for_url("https://example.com/a#two") == first
    refs.alias("https://redirect.example/", first)
    assert refs.for_url("https://redirect.example/") == first
    part = refs.for_part("https://example.com/a", 2)
    assert refs.for_part("https://example.com/a#fragment", 2) == part
    for i in range(12):
        refs.for_url(f"https://site{i}.example/")
    assert refs.get(first) is None
    assert refs.for_url("https://site1.example/") > 10


def test_session_lifecycle_and_config_constructor(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr("browsr.session.time.monotonic", lambda: clock[0])
    store = SessionStore(SessionConfig(ttl_s=5, max_refs=20))
    assert store.get("one").refs.max_entries == 20
    assert store.get("two").id == "two"
    clock[0] += 4
    store.get("one")
    clock[0] += 2
    assert store.sweep() == ["two"]
    assert store.get("one").id == "one"
