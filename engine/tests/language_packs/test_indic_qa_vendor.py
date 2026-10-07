"""The vendored indic-qa checker imports and loads every shipped dictionary.

`packs` marker (tests/conftest.py _FILE_MARKERS): reads the real
dictionaries in engine/language_packs/<code>-irv/dictionary/."""
import hashlib
import json
import sys
from pathlib import Path

import pytest

from tc_ai_bridge.language_packs import indic_qa_vendor
from tc_ai_bridge.language_packs.registry import packs_dir
from tests.support.paths import REPO_ROOT

VENDORED = json.loads((indic_qa_vendor.vendor_root() / "VENDORED.json").read_text(encoding="utf-8"))


def test_every_vendored_file_matches_its_recorded_hash():
    for key, digest in VENDORED["files"].items():
        assert hashlib.sha256((REPO_ROOT / key).read_bytes()).hexdigest() == digest, key


def test_the_vendored_modules_import_from_the_vendor_tree():
    mods = indic_qa_vendor.modules()
    root = indic_qa_vendor.vendor_root().resolve()
    for module in (mods.checker, mods.usfm_doc, mods.kinds, mods.langs):
        assert Path(module.__file__).resolve().is_relative_to(root), module.__file__
    # usfm_doc's own sys.path insert must resolve to the vendored copy, never a
    # build_dictionary elsewhere on the path (Bridge's scripts/ is on it in tests).
    assert Path(sys.modules["build_dictionary"].__file__).resolve() == root / "scripts" / "build_dictionary.py"
    assert sorted(mods.langs.all_codes()) == ["hi", "ml", "or", "pa", "ta"]


@pytest.mark.parametrize("code", indic_qa_vendor.PROFILES)
def test_each_dictionary_loads_and_a_checker_builds(code):
    mods = indic_qa_vendor.modules()
    lang = mods.langs.get(code)
    folder = packs_dir() / f"{code}-irv" / "dictionary"
    info = json.loads((folder / "build_info.json").read_text(encoding="utf-8"))
    assert info["lang"] == code
    lex = mods.checker.Lexicon.load(folder, lang)
    assert len(lex.ov) == info["rows_written"]["wordlist.txt"]
    assert not any(w.endswith("\r") for w in list(lex.ov)[:1000])
    checker = mods.checker.Checker(lex, None, lang)
    checker.build_index()
    assert checker.index.words
    assert indic_qa_vendor.profile(code).PROFILE is lang


def test_ignored_and_learned_words_belong_to_one_checker_not_the_shared_lexicon():
    """Since ab53636 a project's ignored words and learned fixes live on its
    Checker. Bridge relies on that: two projects of one language share a
    Lexicon (and the Tamil layer caches one), so a word one project accepts
    must not change another's findings, and the dictionary is never touched
    (NOTICE contract 2)."""
    mods = indic_qa_vendor.modules()
    lang = mods.langs.get("hi")
    lex = mods.checker.Lexicon.load(packs_dir() / "hi-irv" / "dictionary", lang)
    one, other = mods.checker.Checker(lex, None, lang), mods.checker.Checker(lex, None, lang)
    unknown, known = "कखगघट", "परमेश्वर"
    assert (one.classify(unknown).status, one.classify(known).status) == ("unknown", "ok")

    one.set_ignored({unknown})
    one.set_learned({known: ["प्रभु"]})

    assert one.classify(unknown).status == "ignored"
    assert one.classify(known).status == "learned"
    assert [s["w"] for s in one.learned_suggestions(known)] == ["प्रभु"]
    assert (other.classify(unknown).status, other.classify(known).status) == ("unknown", "ok")
    assert lex.ignored == set() and not hasattr(lex, "learned")


def test_each_dictionary_manifest_matches_its_files():
    for code in indic_qa_vendor.PROFILES + indic_qa_vendor.LAYER_PROFILES:
        folder = packs_dir() / f"{code}-irv" / "dictionary"
        manifest = json.loads((folder / "MANIFEST.json").read_text(encoding="utf-8"))
        assert manifest["commit"] == VENDORED["commit"]
        for name, digest in manifest["files"].items():
            assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == digest, (code, name)


def test_tamil_runs_only_as_the_ta_irv_layer():
    """2026-10-07 (second decision): indic-qa's Tamil profile runs as a layer of
    ta-irv (pack.json indicQa), never as a pack of its own."""
    assert "ta" not in indic_qa_vendor.PROFILES and "ta" in indic_qa_vendor.LAYER_PROFILES
    assert indic_qa_vendor.profile("ta").PROFILE.code == "ta"
    with pytest.raises(KeyError):
        indic_qa_vendor.profile("xx")
