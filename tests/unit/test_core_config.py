from dataclasses import FrozenInstanceError

import pytest

from browsr.config import Config, load_config


def test_config_defaults_and_frozen_sections():
    cfg = load_config(environ={})
    assert cfg == Config()
    assert cfg.search.backends == ["searxng", "ddg", "mojeek"]
    with pytest.raises(FrozenInstanceError):
        cfg.session.ttl_s = 20


def test_config_toml_and_typed_environment_precedence(tmp_path):
    path = tmp_path / "browsr.toml"
    path.write_text('[output]\nresults = 3\n[search]\nlanguage = "en"\n', encoding="utf-8")
    cfg = load_config(
        path, {"BROWSR_OUTPUT__RESULTS": "5", "BROWSR_BROWSER__BLOCK": "image, media"}
    )
    assert cfg.output.results == 5
    assert cfg.search.language == "en"
    assert cfg.browser.block == ["image", "media"]


def test_config_rejects_invalid_typed_override():
    with pytest.raises(ValueError):
        load_config(environ={"BROWSR_SERVER__PORT": "not-a-port"})


def test_config_rejects_invalid_semantic_values():
    with pytest.raises(ValueError, match="between 1000 and 8000"):
        load_config(environ={"BROWSR_OUTPUT__MAX_TOKENS": "900"})
    with pytest.raises(FileNotFoundError):
        load_config("/definitely/missing/browsr.toml", environ={})
