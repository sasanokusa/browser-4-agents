from dataclasses import FrozenInstanceError

import pytest

from browsr.config import Config, load_config


def test_config_defaults_and_frozen_sections():
    cfg = load_config(environ={})
    assert cfg == Config()
    assert cfg.search.backends == ["searxng", "ddg", "mojeek"]
    assert cfg.fetch.fallbacks == ["http", "camoufox"]
    assert cfg.fetch.blocked_memory_s == 3600
    with pytest.raises(FrozenInstanceError):
        cfg.session.ttl_s = 20


def test_config_toml_and_typed_environment_precedence(tmp_path):
    path = tmp_path / "browsr.toml"
    path.write_text('[output]\nresults = 3\n[search]\nlanguage = "en"\n', encoding="utf-8")
    cfg = load_config(
        path,
        {
            "BROWSR_OUTPUT__RESULTS": "5",
            "BROWSR_BROWSER__BLOCK": "image, media",
            "BROWSR_FETCH__FALLBACKS": "camoufox,http",
            "BROWSR_FETCH__BLOCKED_MEMORY_S": "60",
        },
    )
    assert cfg.output.results == 5
    assert cfg.search.language == "en"
    assert cfg.browser.block == ["image", "media"]
    assert cfg.fetch.fallbacks == ["camoufox", "http"]
    assert cfg.fetch.blocked_memory_s == 60


def test_config_rejects_invalid_typed_override():
    with pytest.raises(ValueError):
        load_config(environ={"BROWSR_SERVER__PORT": "not-a-port"})


def test_config_rejects_invalid_semantic_values():
    with pytest.raises(ValueError, match="between 1000 and 8000"):
        load_config(environ={"BROWSR_OUTPUT__MAX_TOKENS": "900"})
    with pytest.raises(FileNotFoundError):
        load_config("/definitely/missing/browsr.toml", environ={})
    with pytest.raises(ValueError, match="fetch.blocked_memory_s"):
        load_config(environ={"BROWSR_FETCH__BLOCKED_MEMORY_S": "-1"})


def test_config_warns_for_unknown_fallback_and_keeps_known_order():
    with pytest.warns(UserWarning, match="Unknown fetch fallback method: custom"):
        cfg = load_config(environ={"BROWSR_FETCH__FALLBACKS": "custom,http"})
    assert cfg.fetch.fallbacks == ["custom", "http"]
