"""The general corpus inside a real profile pack and the Tamil layer (#246):
a synthetic corpus over a copy of the shipped pack, through profile_findings."""
from __future__ import annotations

import json

import pytest

from tc_ai_bridge.language_packs import general_corpus, indic_qa_tamil
from tc_ai_bridge.language_packs.loader import apply_overrides
from tc_ai_bridge.language_packs.registry import packs_dir
from tests.support.corpus import pack_with_corpus
from tests.support.indic_qa import check

# कखगघट: nonsense the OV cannot know (test_indic_qa_vendor.py uses it the same way).
MODERN, SLIP, NEIGHBOUR = "कखगघट", "कखगघठ", "कखगघठ"


def _rules(findings, word):
    return [f["rule"] for f in findings if f["originalText"] == word]


def test_a_word_the_ov_lacks_is_accepted_when_the_general_corpus_knows_it(tmp_path):
    pack = pack_with_corpus(tmp_path, "hi-irv", {MODERN: 500, "मनुष्य": 50_000})
    assert pack.meta[general_corpus.PACK_KEY]["enabled"]
    findings, notes = check(pack, "GEN", {"1": {"1": f"वह {MODERN} गया।"}})
    assert _rules(findings, MODERN) == []
    assert any("accepted from the general corpus" in note for note in notes), notes
    # The same text with no corpus: the word is unknown, as before.
    findings, notes = check(pack_with_corpus(tmp_path / "bare", "hi-irv", {}), "GEN",
                            {"1": {"1": f"वह {MODERN} गया।"}})
    assert _rules(findings, MODERN) == ["hi.lex.unknown"]
    assert not any("general corpus" in note for note in notes)


def test_a_web_typo_stays_unknown_with_the_ov_suggestion_first(tmp_path):
    # मनूष्य is in the corpus (a popular slip), but मनुष्य is 50x commoner.
    pack = pack_with_corpus(tmp_path, "hi-irv", {"मनूष्य": 12, "मनुष्य": 50_000})
    findings, _notes = check(pack, "GEN", {"1": {"1": "वह मनूष्य गया।"}})
    [finding] = [f for f in findings if f["originalText"] == "मनूष्य"]
    assert finding["rule"] == "hi.lex.unknown"
    texts = [s["text"] for s in finding["suggestions"]]
    assert texts[0] == "मनुष्य" and texts.count("मनुष्य") == 1, finding["suggestions"]
    assert finding["suggestions"][0]["source"] == "lexicon", "the OV's suggestion comes first"
    assert "but मनुष्य 50000×" in finding["message"]


def test_an_irv_wide_slip_is_one_finding_per_book_with_its_neighbour(tmp_path):
    # The open book's text counts as IRV usage: six uses make house practice.
    pack = pack_with_corpus(tmp_path, "hi-irv", {NEIGHBOUR: 50_000})
    verses = {str(v): f"वह {SLIP.replace('ठ', 'ट')} गया।" for v in range(1, 7)}
    # Shipped off (its hits on GEN were mostly names): nothing until a project
    # switches it on, which the Checker settings dialog does through overrides.
    rules = json.loads((tmp_path / "hi-irv" / "rule_versions.json").read_text(encoding="utf-8"))
    entry = rules["rules"]["hi.lex.irv-consistent-slip"]
    assert (entry["enabled"], entry["inline"]) == (False, False)
    findings, _notes = check(pack, "GEN", {"1": verses})
    assert not [f for f in findings if f["rule"] == "hi.lex.irv-consistent-slip"]
    pack = apply_overrides(pack, {"rules": {"hi.lex.irv-consistent-slip": {"enabled": True}}})
    findings, _notes = check(pack, "GEN", {"1": verses})
    slips = [f for f in findings if f["rule"] == "hi.lex.irv-consistent-slip"]
    assert len(slips) == 1 and slips[0]["verse"] == "1", [(f["verse"], f["rule"]) for f in findings]
    assert [s["text"] for s in slips[0]["suggestions"]] == [NEIGHBOUR]
    assert "IRV 6×" in slips[0]["message"] and "nearest corpus word" in slips[0]["message"]
    assert not [f for f in findings if f["rule"] == "hi.lex.unknown" and f["originalText"] == MODERN]


def test_the_tamil_layer_consults_the_corpus_on_its_two_unexplained_exits(tmp_path):
    pack = pack_with_corpus(tmp_path, "ta-irv", {"மனிதன்": 50_000, "கணினி": 800})
    assert not pack.by_id("indicqa.lex.irv-consistent-slip").enabled, "shipped off, pending the spot check"
    pack = apply_overrides(pack, {"rules": {"indicqa.lex.irv-consistent-slip": {"enabled": True}}})
    text = {"1": "அந்த கணினி வந்தது.", "2": "அந்த மனிதம் வந்தது.", "3": "அந்த மனிதம் வந்தது.",
            "4": "அந்த மனிதம் வந்தது.", "5": "அந்த மனிதம் வந்தது.", "6": "அந்த மனிதம் வந்தது."}
    findings, notes = check(pack, "GEN", {"1": text})
    assert _rules(findings, "கணினி") == [], "a modern word the 1957 OV lacks"
    assert any("accepted from the general corpus" in note for note in notes)
    slips = [f for f in findings if f["rule"] == "indicqa.lex.irv-consistent-slip"]
    assert [(f["verse"], f["originalText"]) for f in slips] == [("2", "மனிதம்")]
    assert slips[0]["suggestions"][0]["text"] == "மனிதன்"


def test_shipped_packs_declare_the_corpus_only_when_they_ship_it():
    for folder in sorted(packs_dir().glob("*-irv")):
        meta = json.loads((folder / "pack.json").read_text(encoding="utf-8"))
        declared = meta.get(general_corpus.PACK_KEY)
        shipped = (folder / general_corpus.FILE_NAME).is_file()
        assert bool(declared) == shipped, f"{folder.name}: declared={bool(declared)} shipped={shipped}"
        if not shipped:
            continue
        manifest = json.loads((folder / general_corpus.MANIFEST_NAME).read_text(encoding="utf-8"))
        assert manifest["format"] == general_corpus.FORMAT
        assert (folder / general_corpus.FILE_NAME).stat().st_size <= 5 * 1024 * 1024, "the 5 MB budget"
        import build_corpus_lexicon as build
        assert build.verify(meta.get("profile") or (meta.get("indicQa") or {}).get("profile")) == 0


@pytest.mark.parametrize("word,expected", [("கணினி", None), ("மனிதம்", "indicqa.lex.irv-consistent-slip")])
def test_tamil_token_exits_with_a_corpus(word, expected):
    lex = general_corpus.GeneralCorpus(counts={"மனிதன்": 50_000, "கணினி": 800}, key=lambda w: w, manifest={})
    irv = indic_qa_tamil.IRV_ACCEPT_MIN if expected else 1
    token = {"t": word, "status": "unknown", "irv": irv, "ov": 0, "ov_lemma": 0, "sugg": []}
    hit = indic_qa_tamil._token_item(token, lex)
    assert (hit[0] if hit else None) == expected
