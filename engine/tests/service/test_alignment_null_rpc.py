"""#216: alignment.null.set / alignment.null.clear through the dispatcher.

A null decision closes a gap the way a cross-verse link does, refuses a token
that already has a counterpart, and is refused by a link in turn: one token,
one home.
"""
import pytest

from bridge_service import BridgeEngine
from tests.support.alignment_books import bottom, context_id, top, unaligned_verse, write_alignment_book
from tests.support.projects import call


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "php"
    write_alignment_book(root, "php", {"1": {
        # v3: an article and a noun; two Tamil words, one of them pure grammar.
        "3": unaligned_verse("தேவனை அது", [("τῷ", "G35880"), ("Θεῷ", "G23160")]),
        # v4: πάντοτε aligned to its word, a second word in the bank.
        "4": {
            "text": "எப்பொழுதும் அதை",
            "alignment": {
                "alignments": [{"topWords": [top("πάντοτε", "G38420")], "bottomWords": [bottom("எப்பொழுதும்")]}],
                "wordBank": [bottom("அதை")],
            },
        },
    }})
    return root


@pytest.fixture
def engine(project):
    engine = BridgeEngine()
    assert call(engine, "project.open", {"path": str(project)})["success"] is True
    return engine


def _get(engine, verse):
    return call(engine, "alignment.get", {"chapter": "1", "verse": verse})["result"]


def _set(engine, verse, side, word, reason, note=""):
    context = _get(engine, verse)
    token_id = context_id(context, top_word=word) if side == "source" else context_id(context, bottom_word=word)
    return call(engine, "alignment.null.set", {
        "chapter": "1", "verse": verse, "side": side, "id": token_id, "reason": reason, "note": note,
    })


def test_a_null_decision_closes_the_gap_and_reports_itself(engine):
    before = _get(engine, "3")
    assert before["gaps"] == {"sourceUnmatched": 2, "targetUnmatched": 2}
    assert before["nullDecisions"] == {"source": [], "target": []}
    assert before["accounted"] is False

    response = _set(engine, "3", "source", "τῷ", "grammatical", note="article")
    assert response["success"] is True, response
    context = response["result"]["context"]
    assert context["gaps"] == {"sourceUnmatched": 1, "targetUnmatched": 2}
    [entry] = context["nullDecisions"]["source"]
    assert entry["id"] == context_id(context, top_word="τῷ")
    assert (entry["reason"], entry["note"], entry["origin"], entry["stale"]) == ("GRAMMATICAL", "article", "human", False)
    assert context["accountedBy"]["null"] == 1
    # translationCore truth is unchanged: the empty group is still there.
    assert context["status"] == "untouched" and context["canComplete"] is False
    assert all(not g["bottomWords"] for g in context["alignment"]["alignments"])


def test_a_verse_whose_every_token_has_a_home_is_accounted(engine):
    assert _set(engine, "3", "source", "τῷ", "GRAMMATICAL")["success"]
    assert _set(engine, "3", "target", "அது", "GRAMMATICAL")["success"]
    context = _get(engine, "3")
    assert context["accounted"] is False  # Θεῷ and தேவனை are still gaps
    v3 = context
    realign = call(engine, "alignment.realign", {
        "chapter": "1", "verse": "3",
        "topIds": [context_id(v3, top_word="Θεῷ")], "bottomIds": [context_id(v3, bottom_word="தேவனை")],
        "expectedOriginal": v3["alignment"],
    })
    assert realign["success"] is True, realign
    context = _get(engine, "3")
    assert context["gaps"] == {"sourceUnmatched": 0, "targetUnmatched": 0}
    assert context["accounted"] is True and context["fullyAccounted"] is True
    assert context["accountedBy"] == {"tc": 2, "crossVerse": 0, "null": 2}


def test_refusals(engine):
    # A source word already aligned in its own verse.
    aligned = _set(engine, "4", "source", "πάντοτε", "IMPLICIT")
    assert aligned["success"] is False and "already aligned within 1:4" in aligned["error"]["message"]
    # A target word already aligned in its own verse.
    aligned_t = _set(engine, "4", "target", "எப்பொழுதும்", "GRAMMATICAL")
    assert aligned_t["success"] is False and "already aligned within 1:4" in aligned_t["error"]["message"]
    # A reason that does not belong to the side.
    wrong = _set(engine, "3", "source", "τῷ", "EXPLICITATION")
    assert wrong["success"] is False and wrong["error"]["code"] == "alignment_error"
    # Missing fields and unknown verses are project errors.
    for params in ({}, {"chapter": "1", "verse": "99", "side": "source", "id": "H001", "reason": "IMPLICIT"},
                   {"chapter": "1", "verse": "3", "side": "source", "reason": "IMPLICIT"}):
        response = call(engine, "alignment.null.set", params)
        assert response["success"] is False and response["error"]["code"] == "project_error", params
    # A stale id.
    stale = call(engine, "alignment.null.set", {"chapter": "1", "verse": "3", "side": "source", "id": "H099", "reason": "IMPLICIT"})
    assert stale["success"] is False and stale["error"]["code"] == "alignment_error"


def test_one_token_one_home_between_nulls_and_links(engine):
    # Link v3's Θεῷ to v4's அதை, then try to mark either end as null.
    v3, v4 = _get(engine, "3"), _get(engine, "4")
    linked = call(engine, "alignment.crossVerse.link", {
        "source": {"chapter": "1", "verse": "3", "topId": context_id(v3, top_word="Θεῷ")},
        "target": {"chapter": "1", "verse": "4", "bottomId": context_id(v4, bottom_word="அதை")},
    })
    assert linked["success"] is True, linked
    src = _set(engine, "3", "source", "Θεῷ", "IMPLICIT")
    assert src["success"] is False and "linked to another verse" in src["error"]["message"]
    tgt = _set(engine, "4", "target", "அதை", "GRAMMATICAL")
    assert tgt["success"] is False and "linked to another verse" in tgt["error"]["message"]

    # The other way round: a null-decided word cannot be linked.
    assert _set(engine, "3", "source", "τῷ", "GRAMMATICAL")["success"]
    v3 = _get(engine, "3")
    refused = call(engine, "alignment.crossVerse.link", {
        "source": {"chapter": "1", "verse": "3", "topId": context_id(v3, top_word="τῷ")},
        "target": {"chapter": "1", "verse": "4", "bottomId": context_id(v4, bottom_word="அதை")},
    })
    assert refused["success"] is False and "marked grammatical" in refused["error"]["message"]


def test_clear_restores_the_gap_and_gap_scan_counts_nulls(engine):
    decision = _set(engine, "3", "source", "τῷ", "GRAMMATICAL")["result"]["decision"]
    scan = call(engine, "alignment.gapScan", {"chapter": "1"})["result"]
    v3 = next(v for v in scan["verses"] if v["verse"] == "3")
    assert v3["nullDecided"] == {"source": 1, "target": 0}
    assert [t["word"] for t in v3["sourceGaps"]] == ["Θεῷ"]
    assert scan["totals"]["nullDecided"] == 1

    cleared = call(engine, "alignment.null.clear", {"decisionId": decision["id"]})
    assert cleared["success"] is True, cleared
    assert cleared["result"]["context"]["gaps"]["sourceUnmatched"] == 2
    assert cleared["result"]["context"]["nullDecisions"]["source"] == []
    again = call(engine, "alignment.null.clear", {"decisionId": decision["id"]})
    assert again["success"] is False and again["error"]["code"] == "alignment_error"
    empty = call(engine, "alignment.null.clear", {})
    assert empty["success"] is False and empty["error"]["code"] == "project_error"


def test_decisions_survive_a_restart(engine, project):
    decision = _set(engine, "3", "target", "அது", "GRAMMATICAL")["result"]["decision"]
    second = BridgeEngine()
    assert call(second, "project.open", {"path": str(project)})["success"] is True
    [entry] = _get(second, "3")["nullDecisions"]["target"]
    assert entry["decisionId"] == decision["id"] and entry["reason"] == "GRAMMATICAL"
