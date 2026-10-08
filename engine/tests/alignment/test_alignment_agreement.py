"""#219: two-pass agreement and the window the model is shown. Pure: no
project, no transport.

The gate is agreement between a source-first and a target-first reading of the
same window -- never a confidence number. These tests pin what is written, what
is only suggested, and what is reported as unplaced.
"""
import json

import pytest

from tc_ai_bridge.alignment_agreement import CorpusCheck, PassResult, TokenKey, agree
from tc_ai_bridge.alignment_window import (
    SCHEMA, SOURCE_FIRST, TARGET_FIRST, WindowToken, WindowVerse, ask, build_window, decode,
)

S = lambda verse, word: TokenKey("source", verse, f"{word}␟1␟1")  # noqa: E731
T = lambda verse, word: TokenKey("target", verse, f"{word}␟1␟1")  # noqa: E731

THEOS, EUCH, ARTICLE = S("3", "Θεῷ"), S("3", "εὐχαριστῶ"), S("3", "τῷ")
DEVANAI, STHOTH, NAN = T("6", "தேவனை"), T("6", "ஸ்தோத்திரிக்கிறேன்"), T("6", "நான்")
ALL = [THEOS, EUCH, ARTICLE, DEVANAI, STHOTH, NAN]


def _pass(direction, edges=(), nulls=None, unplaced=None, confidence=90):
    result = PassResult(direction=direction)
    for source, target in edges:
        result.edges[(source, target)] = {"confidence": confidence, "reason": f"{direction} says so"}
    for token, reason in (nulls or {}).items():
        result.nulls[token] = {"reason": reason, "note": "article"}
    result.unplaced.update(unplaced or {})
    return result


def test_what_both_passes_say_is_agreed_and_nothing_else():
    a = _pass(SOURCE_FIRST, [(THEOS, DEVANAI), (EUCH, STHOTH), (EUCH, NAN)], {ARTICLE: "GRAMMATICAL"})
    b = _pass(TARGET_FIRST, [(THEOS, DEVANAI), (EUCH, STHOTH)], {ARTICLE: "GRAMMATICAL", NAN: "GRAMMATICAL"})
    result = agree(a, b, tokens=ALL)
    assert set(result.agreed_edges) == {(THEOS, DEVANAI), (EUCH, STHOTH)}
    assert result.agreed_nulls == {ARTICLE: {"reason": "GRAMMATICAL", "note": "article"}}
    # நான்: linked by one pass and nulled by the other -- two suggestions, no write.
    kinds = {(s.kind, s.status, s.target or s.token) for s in result.suggestions}
    assert kinds == {("link", "UNCERTAIN", NAN), ("null", "UNCERTAIN", NAN)}
    link = next(s for s in result.suggestions if s.kind == "link")
    assert link.votes == {SOURCE_FIRST: True, TARGET_FIRST: False}
    assert result.unplaced == {}


def test_a_null_reason_mismatch_is_a_disagreement():
    a = _pass(SOURCE_FIRST, nulls={ARTICLE: "GRAMMATICAL"})
    b = _pass(TARGET_FIRST, nulls={ARTICLE: "IMPLICIT"})
    result = agree(a, b, tokens=[ARTICLE])
    assert result.agreed_nulls == {}
    assert sorted((s.reason, s.votes[SOURCE_FIRST], s.votes[TARGET_FIRST]) for s in result.suggestions) == [
        ("GRAMMATICAL", True, False), ("IMPLICIT", False, True),
    ]


def test_an_agreed_edge_on_a_token_either_pass_nulled_is_not_written():
    a = _pass(SOURCE_FIRST, [(EUCH, STHOTH)], {EUCH: "IMPLICIT"})
    b = _pass(TARGET_FIRST, [(EUCH, STHOTH)])
    result = agree(a, b, tokens=[EUCH, STHOTH])
    assert result.agreed_edges == {} and result.agreed_nulls == {}
    assert {s.kind for s in result.suggestions} == {"link", "null"}


def test_a_token_neither_pass_placed_is_unplaced_with_both_notes():
    a = _pass(SOURCE_FIRST, [(EUCH, STHOTH)], unplaced={THEOS: "no word for God"})
    b = _pass(TARGET_FIRST, [(EUCH, STHOTH)], unplaced={THEOS: "God is not translated", NAN: "adds 'I'"})
    result = agree(a, b, tokens=[THEOS, EUCH, STHOTH, NAN])
    assert result.unplaced == {THEOS: ["no word for God", "God is not translated"], NAN: ["adds 'I'"]}
    # Placed by only one pass is a suggestion, not an omission.
    a2 = _pass(SOURCE_FIRST, [(THEOS, DEVANAI)])
    b2 = _pass(TARGET_FIRST, unplaced={THEOS: "?"})
    result2 = agree(a2, b2, tokens=[THEOS, DEVANAI])
    assert result2.unplaced == {} and [s.status for s in result2.suggestions] == ["UNCERTAIN"]


def test_the_corpus_blocks_a_cross_verse_edge_only_when_it_disagrees():
    a = _pass(SOURCE_FIRST, [(THEOS, DEVANAI)])
    b = _pass(TARGET_FIRST, [(THEOS, DEVANAI)])
    silent = CorpusCheck(checked=True, best={})
    assert set(agree(a, b, tokens=ALL, corpus=silent).agreed_edges) == {(THEOS, DEVANAI)}
    same = CorpusCheck(checked=True, best={THEOS: DEVANAI})
    assert set(agree(a, b, tokens=ALL, corpus=same).agreed_edges) == {(THEOS, DEVANAI)}
    other = CorpusCheck(checked=True, best={THEOS: NAN})
    blocked = agree(a, b, tokens=ALL, corpus=other)
    assert blocked.agreed_edges == {} and blocked.suggestions[0].status == "CORPUS_DISAGREES"
    contested = CorpusCheck(checked=True, best={THEOS: DEVANAI}, contested={DEVANAI})
    assert agree(a, b, tokens=ALL, corpus=contested).suggestions[0].status == "CORPUS_DISAGREES"
    cold = agree(a, b, tokens=ALL, corpus=CorpusCheck(checked=False, reason="no-completed-alignments"))
    assert set(cold.agreed_edges) == {(THEOS, DEVANAI)} and cold.corpus == {"checked": False, "reason": "no-completed-alignments"}


def test_the_corpus_is_never_asked_about_a_same_verse_edge():
    same_verse = (S("6", "x"), T("6", "y"))
    a, b = _pass(SOURCE_FIRST, [same_verse]), _pass(TARGET_FIRST, [same_verse])
    assert set(agree(a, b, tokens=list(same_verse), corpus=CorpusCheck(checked=True, best={same_verse[0]: NAN})).agreed_edges) == {same_verse}


def test_human_homes_are_never_written_over_and_never_unplaced():
    a = _pass(SOURCE_FIRST, [(THEOS, DEVANAI), (EUCH, STHOTH)])
    b = _pass(TARGET_FIRST, [(THEOS, DEVANAI)])
    result = agree(a, b, tokens=ALL, homed={THEOS, EUCH, NAN})
    assert result.agreed_edges == {}
    # Both passes contradicting a reviewer is surfaced once; one pass alone is noise.
    assert [(s.status, s.source) for s in result.suggestions] == [("CONFLICTS_WITH_HUMAN", THEOS)]
    assert NAN not in result.unplaced and ARTICLE in result.unplaced


def test_confidence_is_recorded_and_decides_nothing():
    a = _pass(SOURCE_FIRST, [(THEOS, DEVANAI)], confidence=3)
    b = _pass(TARGET_FIRST, [(THEOS, DEVANAI)], confidence=99)
    result = agree(a, b, tokens=[THEOS, DEVANAI])
    assert result.agreed_edges[(THEOS, DEVANAI)]["confidence"] == 3


# --- the window ----------------------------------------------------------------

def _window():
    verses = [
        WindowVerse("3", "first verse", sources=[
            WindowToken(EUCH, "εὐχαριστῶ", "H001", strong="G21680", lemma="εὐχαριστέω"),
            WindowToken(ARTICLE, "τῷ", "H002", strong="G35880"),
            WindowToken(THEOS, "Θεῷ", "H003", strong="G23160", homed=True),
        ], targets=[]),
        WindowVerse("6", "நான் தேவனை ஸ்தோத்திரிக்கிறேன்", sources=[], targets=[
            WindowToken(NAN, "நான்", "T001"), WindowToken(DEVANAI, "தேவனை", "T002"),
            WindowToken(STHOTH, "ஸ்தோத்திரிக்கிறேன்", "T003"),
        ]),
    ]
    return build_window("1", verses, reference="PHP 1:3-6", glosses={"G21680": "give thanks"})


def test_the_payload_uses_opaque_handles_in_reading_order():
    window = _window()
    payload = window.payload
    assert [v["verse"] for v in payload["verses"]] == ["3", "6"]
    assert [r["id"] for r in payload["verses"][0]["source"]] == ["S1", "S2", "S3"]
    assert payload["verses"][0]["source"][0]["meaning"] == "give thanks"
    assert payload["verses"][0]["source"][2]["alreadyAligned"] is True
    text = json.dumps(payload, ensure_ascii=False)
    assert "H001" not in text and "T001" not in text and "␟" not in text
    assert window.homed() == {THEOS}


def test_decode_resolves_handles_and_refuses_invented_ones():
    window = _window()
    raw = {
        "links": [
            {"source_id": "S1", "target_id": "T3", "confidence": 130, "reason": "thanks"},
            {"source_id": "S1", "target_id": "T1", "confidence": 80, "reason": "I"},
            {"source_id": "S3", "target_id": "T2", "confidence": 90, "reason": "already aligned"},
            {"source_id": "T1", "target_id": "T2", "confidence": 90, "reason": "both target"},
        ],
        "nulls": [{"id": "S2", "reason": "GRAMMATICAL", "note": "article"},
                  {"id": "T2", "reason": "IMPLICIT", "note": "wrong side"}],
        "unplaced": [], "review_notes": ["ok"],
    }
    result = decode(raw, window, SOURCE_FIRST)
    assert result.edges[(EUCH, STHOTH)]["confidence"] == 100
    assert set(result.edges) == {(EUCH, STHOTH), (EUCH, NAN)}
    assert result.nulls == {ARTICLE: {"reason": "GRAMMATICAL", "note": "article"}}
    assert any("IMPLICIT on the target word" in n for n in result.notes)
    assert "ok" in result.notes
    with pytest.raises(RuntimeError, match="'S9'"):
        decode({"links": [{"source_id": "S9", "target_id": "T1", "confidence": 1, "reason": ""}]}, window, SOURCE_FIRST)
    with pytest.raises(RuntimeError):
        decode({"links": "nope"}, window, SOURCE_FIRST)
    with pytest.raises(RuntimeError):
        decode(["not", "an", "object"], window, SOURCE_FIRST)


def test_ask_sends_the_direction_and_guidance():
    window = _window()
    seen = {}

    def call_model(instructions, input_text, direction):
        seen.update(instructions=instructions, payload=json.loads(input_text), direction=direction)
        return {"links": [], "nulls": [], "unplaced": [], "review_notes": []}

    ask(window, call_model, TARGET_FIRST, guidance="Tamil is agglutinative.")
    assert seen["direction"] == TARGET_FIRST
    assert "TARGET-FIRST" in seen["instructions"] and "Tamil is agglutinative." in seen["instructions"]
    assert "on both sides" in seen["instructions"]
    assert seen["payload"]["passage"] == "PHP 1:3-6"
    assert set(SCHEMA["required"]) == {"links", "nulls", "unplaced", "review_notes"}
