"""Which pack a project gets: the registry, the setting, and where packs live."""
import json

import pytest

from tc_ai_bridge.language_packs import registry
from tc_ai_bridge.language_qa_jobs import resolve_language


@pytest.fixture
def packs(tmp_path, monkeypatch):
    """A packs directory of our own, registering two languages."""
    (tmp_path / "index.json").write_text(json.dumps({
        "languages": {"ta": {"pack": "ta-test", "name": "Tamil"}, "ml": {"pack": "ml-test", "name": "Malayalam"}},
        "aliases": {"tam": "ta", "mal": "ml"}}), encoding="utf-8")
    monkeypatch.setenv(registry.ENV, str(tmp_path))
    registry.index.cache_clear()
    yield tmp_path
    registry.index.cache_clear()


def test_the_packs_directory_comes_from_the_environment_else_the_source_tree(monkeypatch, tmp_path):
    monkeypatch.setenv(registry.ENV, str(tmp_path))
    assert registry.packs_dir() == tmp_path
    monkeypatch.delenv(registry.ENV)
    assert registry.packs_dir() == registry.SOURCE_DIR and (registry.SOURCE_DIR / "index.json").is_file()


def test_select_pack_folds_codes_and_subtags(packs):
    assert registry.select_pack("ta") == registry.select_pack("TAM") == registry.select_pack("ta-IN") == "ta-test"
    assert registry.select_pack("mal") == "ml-test"
    assert registry.select_pack("hi") is None and registry.select_pack("") is None
    assert registry.language_of_pack("ml-test") == "ml" and registry.language_name("mal") == "Malayalam"


def test_a_missing_index_registers_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv(registry.ENV, str(tmp_path / "nowhere"))
    registry.index.cache_clear()
    try:
        assert registry.select_pack("ta") is None and registry.available() == []
        assert "Language packs unavailable" in registry.problem() and "common checks only" in registry.problem()
    finally:
        registry.index.cache_clear()


@pytest.mark.parametrize("manifest,setting", [
    ({}, "auto"),
    ({"language_qa": {"pack": "off"}}, "off"),
    ({"language_qa": {"pack": "ml-test"}}, "ml-test"),
    ({"language_qa": {"pack": "no-such-pack"}}, "auto"),   # never guessed into a pack
    ({"language_qa": "ml-test"}, "auto"),
])
def test_the_project_setting_reads_auto_off_or_a_registered_pack(packs, manifest, setting):
    assert registry.pack_setting(manifest) == setting


def test_the_setting_overrides_detection_and_off_means_common_checks(packs):
    detection = {"pack": "ta-test", "language": "ta", "basis": "metadata", "message": "Tamil rules"}
    assert resolve_language(detection, "auto") == {**detection, "setting": "auto"}
    off = resolve_language(detection, "off")
    assert off["pack"] == "common" and off["basis"] == "setting-off"
    chosen = resolve_language(detection, "ml-test")
    assert (chosen["pack"], chosen["language"], chosen["basis"]) == ("ml-test", "ml", "setting")
    assert "suggested otherwise" in chosen["message"]  # the metadata said Tamil: shown, not hidden
