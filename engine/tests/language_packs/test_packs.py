"""Every shipped pack, one parametrized test each (`packs` marker).

A pack is data: it is validated by its own embedded examples at load, and
here. Tests do not grow per language; a new pack is a new parameter
(docs/LANGUAGE_QA_PACKS.md). Inner loop: `-m "not packs"`."""
import json

import pytest

from tc_ai_bridge.language_packs import load_pack
from tc_ai_bridge.language_packs.indic import KINDS
from tc_ai_bridge.language_packs.registry import available, packs_dir

pytestmark = pytest.mark.packs

SHIPPED = sorted(p.name for p in packs_dir().iterdir() if (p / "pack.json").is_file())


def test_every_registered_pack_is_shipped_and_every_shipped_pack_is_registered():
    registered = {entry["pack"] for entry in available()}
    assert registered == set(SHIPPED), (registered, SHIPPED)


@pytest.mark.parametrize("name", SHIPPED)
def test_pack(name):
    pack = load_pack(name)  # compiles every rule and runs every embedded example
    meta = json.loads((packs_dir() / name / "pack.json").read_text(encoding="utf-8"))
    assert pack.name == meta["pack"] == name
    [entry] = [e for e in available() if e["pack"] == name]
    assert pack.language == entry["language"]
    assert len({r.id for r in pack.rules}) == len(pack.rules)
    if pack.meta.get("engine") == "indic-qa":
        # A profile pack: indic-qa's rule catalogue, checked in
        # test_indic_qa_adapter.py; its data files are below.
        assert {r.match_type for r in pack.rules} == {"indic-qa"}
        assert (packs_dir() / name / "dictionary" / "MANIFEST.json").is_file()
        assert (packs_dir() / name / "irv_state.json.gz").is_file()
        return
    if meta.get("indicQa"):
        # A pack's indic-qa layer (ta-irv): catalogue rules checked in
        # test_indic_qa_tamil_layer.py, with the layer's data files.
        layer = meta["indicQa"]
        assert (packs_dir() / name / layer["dictionary"] / "MANIFEST.json").is_file()
        assert (packs_dir() / name / layer["rules"]).is_file()
        assert (packs_dir() / name / "irv_state.json.gz").is_file()
        assert any(r.match_type == "indic-qa" for r in pack.rules), pack.problems
    for rule in pack.rules:
        if rule.match_type == "indic-qa":
            continue
        assert rule.match_type in KINDS, rule.id
        assert rule.source.get("provenance"), rule.id
        if not rule.enabled:
            assert "DISABLED" in rule.source["provenance"], rule.id
    # The data files the pack names load (the lexicon lazily, never at startup).
    if meta.get("lexicon"):
        assert pack.lexicon() is not None
    if meta.get("confusion"):
        assert pack.confusion() is not None
    if any(r.stage == "book" and r.match_type == "lexicon-lookup" for r in pack.rules):
        assert meta.get("lexicon"), "a lexicon-lookup rule needs the pack's lexicon"
