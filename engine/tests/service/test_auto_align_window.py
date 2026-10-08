"""#219: alignment.window.autoAlign -- two passes, agreement writes, the rest
is suggested or reported. Fake transport throughout: no network, no real key.

The fixture is the IRV shape in miniature: Greek v.3's "I thank" is rendered in
Tamil v.4, so a verse can only end clean if the cross-verse link is made.

Handles, in the order the window assigns them:
    S1 εὐχαριστῶ (v3)  S2 τῷ (v3)  S3 Θεῷ (v3)  S4 πάντοτε (v4)
    T1 தேவனை (v3)  T2 நான் (v4)  T3 எப்பொழுதும் (v4)  T4 ஸ்தோத்திரிக்கிறேன் (v4)
"""
import json

import pytest

from bridge_service import BridgeEngine
from tc_ai_bridge.cross_verse_links import TABLE as LINKS
from tests.support.alignment_books import context_id, unaligned_verse, write_alignment_book
from tests.support.projects import call

CLEAN = {
    "links": [
        {"source_id": "S3", "target_id": "T1", "confidence": 95, "reason": "God"},
        {"source_id": "S1", "target_id": "T4", "confidence": 90, "reason": "give thanks"},
        {"source_id": "S1", "target_id": "T2", "confidence": 80, "reason": "first person of the verb"},
        {"source_id": "S4", "target_id": "T3", "confidence": 95, "reason": "always"},
    ],
    "nulls": [{"id": "S2", "reason": "GRAMMATICAL", "note": "article"}],
    "unplaced": [], "review_notes": [],
}


def _without(body, *, links=(), nulls=(), unplaced=()):
    out = json.loads(json.dumps(body))
    out["links"] = [l for l in out["links"] if (l["source_id"], l["target_id"]) not in set(links)]
    out["nulls"] = [n for n in out["nulls"] if n["id"] not in set(nulls)]
    out["unplaced"] = out["unplaced"] + [{"id": i, "note": f"{i} has no counterpart"} for i in unplaced]
    return out


def transport_for(source_first, target_first, captured=None):
    def transport(url, headers, request_body, timeout):
        request = json.loads(request_body)
        if captured is not None:
            captured.append(request)
        text = json.dumps(request, ensure_ascii=False)
        body = source_first if "SOURCE-FIRST" in text else target_first
        return 200, json.dumps({
            "output_text": json.dumps(body, ensure_ascii=False),
            "usage": {"input_tokens": 1000, "output_tokens": 200, "total_tokens": 1200},
        }).encode("utf-8")
    return transport


@pytest.fixture
def root(tmp_path):
    root = tmp_path / "php"
    write_alignment_book(root, "php", {"1": {
        "3": unaligned_verse("தேவனை", [("εὐχαριστῶ", "G21680"), ("τῷ", "G35880"), ("Θεῷ", "G23160")]),
        "4": unaligned_verse("நான் எப்பொழுதும் ஸ்தோத்திரிக்கிறேன்", [("πάντοτε", "G38420")]),
    }})
    return root


def open_engine(root, transport, api_key="test-key"):
    engine = BridgeEngine(ai_transport=transport)
    engine.settings.set_api_key(api_key, persist=False)
    assert call(engine, "project.open", {"path": str(root)})["success"] is True
    return engine


def _run(engine, apply=True):
    response = call(engine, "alignment.window.autoAlign", {"chapter": "1", "verses": ["3", "4"], "apply": apply})
    assert response["success"] is True, response
    return response["result"]


def _by_verse(result):
    return {r["verse"]: r for r in result["results"]}


def test_a_clean_window_is_aligned_with_no_click(root):
    captured = []
    engine = open_engine(root, transport_for(CLEAN, CLEAN, captured))
    result = _run(engine)
    verses = _by_verse(result)
    assert {v: r["verdict"] for v, r in verses.items()} == {"3": "ALIGNED_CLEAN", "4": "ALIGNED_CLEAN"}
    assert result["usage"]["calls"] == 2 and result["corpus"]["checked"] is False
    # Both passes went out, each in its own direction, with opaque handles only.
    assert len(captured) == 2
    sent = json.dumps(captured, ensure_ascii=False)
    assert "H001" not in sent and "␟" not in sent

    v3, v4 = verses["3"]["context"], verses["4"]["context"]
    # Same-verse: Θεῷ → தேவனை as a real tC group.
    [group] = [g for g in v3["groups"] if g["bottomIds"]]
    assert group["topIds"] == [context_id(v3, top_word="Θεῷ")]
    # Cross-verse 1:2: εὐχαριστῶ (v3) → நான் + ஸ்தோத்திரிக்கிறேன் (v4), one group.
    [xv] = v3["crossVerseGroups"]
    assert xv["relation"] == "one-to-many" and {t["word"] for t in xv["targets"]} == {"நான்", "ஸ்தோத்திரிக்கிறேன்"}
    # Null: τῷ grammatical, written by the automatic pass.
    [null] = v3["nullDecisions"]["source"]
    assert (null["word"], null["reason"], null["origin"]) == ("τῷ", "GRAMMATICAL", "ai-auto")
    assert v3["accounted"] is True and v4["accounted"] is True
    # translationCore stays honest: v3 has an empty group and a cross-verse word.
    assert v3["completionState"] == "pending"
    # The ledger records exactly what was written.
    applied = verses["3"]["applied"]
    assert len(applied["groups"]) == 1 and applied["links"] == [xv["groupId"]] and len(applied["nulls"]) == 1
    # Audit trail: ai-auto on the link events, ai_auto_align in the tC history.
    project_id = engine.project.workbench_identity.project_id
    link_events = [json.loads(e["payload_json"]) for e in engine.project.workbench.change_log_entries(project_id)
                   if e["table_name"] == LINKS and e["op"] == "crossVerseLink"]
    assert {(e["origin"], e["runId"]) for e in link_events} == {("ai-auto", result["runId"])}
    history = [h["operation"] for h in engine.project.workbench.payloads(
        "alignment_history", project_id=project_id, book_id="php", equals={"chapter": "1", "verse": "4"})]
    assert "ai_auto_align" in history


def test_an_edge_only_one_pass_gave_is_a_suggestion_not_a_write(root):
    target_first = _without(CLEAN, links=[("S1", "T2")])
    engine = open_engine(root, transport_for(CLEAN, target_first))
    verses = _by_verse(_run(engine))
    v4 = verses["4"]
    assert v4["verdict"] == "NEEDS_REVIEW"
    [suggestion] = [s for s in v4["suggestions"] if s["kind"] == "link"]
    assert suggestion["target"]["word"] == "நான்" and suggestion["status"] == "UNCERTAIN"
    assert suggestion["votes"] == {"source-first": True, "target-first": False}
    # The agreed half of the realization was still written; நான் was not.
    [xv] = v4["context"]["crossVerseGroups"]
    assert [t["word"] for t in xv["targets"]] == ["ஸ்தோத்திரிக்கிறேன்"]
    assert v4["context"]["gaps"]["targetUnmatched"] == 1


def test_a_word_neither_pass_placed_is_reported_and_never_forced(root):
    body = _without(CLEAN, links=[("S4", "T3")], unplaced=["S4", "T3"])
    engine = open_engine(root, transport_for(body, body))
    verses = _by_verse(_run(engine))
    v4 = verses["4"]
    assert v4["verdict"] == "NEEDS_REVIEW"
    kinds = {(i["kind"], i["word"]) for i in v4["issues"]}
    assert kinds == {("POSSIBLE_OMISSION", "πάντοτε"), ("POSSIBLE_ADDITION", "எப்பொழுதும்")}
    assert all(i["note"] for i in v4["issues"])
    # Nothing was written for either word.
    context = v4["context"]
    assert not any(g["bottomIds"] for g in context["groups"])
    assert context["gaps"] == {"sourceUnmatched": 1, "targetUnmatched": 1}
    # v3 is untouched by v4's problem.
    assert verses["3"]["verdict"] == "ALIGNED_CLEAN"


def test_without_a_key_nothing_is_sent_and_the_result_says_why(root):
    captured = []
    engine = open_engine(root, transport_for(CLEAN, CLEAN, captured), api_key="")
    result = _run(engine)
    assert result["unavailable"]["reason"] == "no-api-key" and result["results"] == []
    assert captured == []


def test_apply_false_writes_only_the_verdict(root):
    engine = open_engine(root, transport_for(CLEAN, CLEAN))
    verses = _by_verse(_run(engine, apply=False))
    assert {r["verdict"] for r in verses.values()} == {"ALIGNED_CLEAN"}
    assert verses["3"]["applied"] == {"groups": [], "links": [], "nulls": []}
    v3 = verses["3"]["context"]
    assert not any(g["bottomIds"] for g in v3["groups"])
    assert v3["crossVerseGroups"] == [] and v3["nullDecisions"]["source"] == []
    stored = call(engine, "alignment.autoAlign.verdict", {"chapter": "1", "verse": "3"})["result"]["verdict"]
    assert stored["verdict"] == "ALIGNED_CLEAN" and stored["applyRequested"] is False


def test_a_reviewers_decision_is_never_overwritten(root):
    engine = open_engine(root, transport_for(CLEAN, CLEAN))
    v3 = call(engine, "alignment.get", {"chapter": "1", "verse": "3"})["result"]
    assert call(engine, "alignment.null.set", {
        "chapter": "1", "verse": "3", "side": "source", "id": context_id(v3, top_word="τῷ"), "reason": "IMPLICIT",
    })["success"]
    verses = _by_verse(_run(engine))
    [null] = verses["3"]["context"]["nullDecisions"]["source"]
    assert (null["reason"], null["origin"]) == ("IMPLICIT", "human")
    assert verses["3"]["verdict"] == "ALIGNED_CLEAN"  # τῷ already has a home


def test_a_rerun_supersedes_only_its_own_writes(root):
    engine = open_engine(root, transport_for(CLEAN, CLEAN))
    _run(engine)
    # A reviewer adds a decision the pass did not make... (none to add here: all homed)
    # ...and the second run disagrees about Θεῷ: neither pass places it now.
    second = _without(CLEAN, links=[("S3", "T1")], unplaced=["S3", "T1"])
    engine._ai_transport = transport_for(second, second)
    verses = _by_verse(_run(engine))
    v3 = verses["3"]
    # The first run's Θεῷ group was removed, because the pass that wrote it is superseded.
    assert not any(g["bottomIds"] for g in v3["context"]["groups"])
    assert {(i["kind"], i["word"]) for i in v3["issues"]} == {("POSSIBLE_OMISSION", "Θεῷ"), ("POSSIBLE_ADDITION", "தேவனை")}
    # The rest is re-written, not duplicated.
    assert len(v3["context"]["crossVerseGroups"]) == 1 and len(v3["context"]["nullDecisions"]["source"]) == 1


def test_revert_undoes_one_verse(root):
    engine = open_engine(root, transport_for(CLEAN, CLEAN))
    _run(engine)
    reverted = call(engine, "alignment.autoAlign.revert", {"chapter": "1", "verse": "3"})
    assert reverted["success"] is True, reverted
    v3 = reverted["result"]["context"]
    assert not any(g["bottomIds"] for g in v3["groups"])
    assert v3["crossVerseGroups"] == [] and v3["nullDecisions"]["source"] == []
    assert reverted["result"]["skipped"] == []
    stored = call(engine, "alignment.autoAlign.verdict", {"chapter": "1", "verse": "3"})["result"]["verdict"]
    assert stored["verdict"] == "REVERTED" and stored["applied"] == {"groups": [], "links": [], "nulls": []}
    # v4's own same-verse group is not v3's to revert.
    v4 = call(engine, "alignment.get", {"chapter": "1", "verse": "4"})["result"]
    assert any(g["bottomIds"] for g in v4["groups"])


def test_revert_leaves_a_group_the_reviewer_changed_alone(root):
    engine = open_engine(root, transport_for(CLEAN, CLEAN))
    _run(engine)
    v4 = call(engine, "alignment.get", {"chapter": "1", "verse": "4"})["result"]
    # The reviewer takes எப்பொழுதும் out of the automatic group.
    assert call(engine, "alignment.unalign", {
        "chapter": "1", "verse": "4", "bottomIds": [context_id(v4, bottom_word="எப்பொழுதும்")],
        "expectedOriginal": v4["alignment"],
    })["success"]
    reverted = call(engine, "alignment.autoAlign.revert", {"chapter": "1", "verse": "4"})["result"]
    assert reverted["skipped"] and reverted["skipped"][0].startswith("group ")
    # The link group in v4's ledger is still undone; the reviewer's state stays.
    assert reverted["context"]["crossVerseGroups"] == []
    assert reverted["context"]["gaps"]["targetUnmatched"] == 3


def test_an_invented_id_fails_the_request_and_writes_nothing(root):
    bad = _without(CLEAN)
    bad["links"].append({"source_id": "S99", "target_id": "T1", "confidence": 99, "reason": "made up"})
    engine = open_engine(root, transport_for(bad, CLEAN))
    response = call(engine, "alignment.window.autoAlign", {"chapter": "1", "verses": ["3", "4"]})
    assert response["success"] is False and "'S99'" in response["error"]["message"]
    v3 = call(engine, "alignment.get", {"chapter": "1", "verse": "3"})["result"]
    assert not any(g["bottomIds"] for g in v3["groups"]) and v3["crossVerseGroups"] == []


def test_window_validation(root):
    engine = open_engine(root, transport_for(CLEAN, CLEAN))
    for params in ({"chapter": "1", "verses": []}, {"chapter": "1", "verses": ["3", "9"]},
                   {"chapter": "9", "verses": ["1"]}):
        response = call(engine, "alignment.window.autoAlign", params)
        assert response["success"] is False and response["error"]["code"] == "project_error", params


def test_a_shared_ending_does_not_fuse_two_pairs_into_one_group(tmp_path):
    """1 Cor 7:2, as found in the desktop app: both passes linked καὶ to both
    Tamil words (each carries "-உம்"), and the compiler wrote ONE 3:2 group.
    Now the two pairs are written on their own and καὶ is offered, with
    "grammatical" as Bridge's reading. Handles: S1 γυναῖκα S2 καὶ S3 ἄνδρα;
    T1 மனைவியையும் T2 கணவனையும்."""
    root = tmp_path / "1co"
    write_alignment_book(root, "1co", {"7": {
        "2": unaligned_verse("மனைவியையும் கணவனையும்", [("γυναῖκα", "G11350"), ("καὶ", "G25320"), ("ἄνδρα", "G04350")]),
    }})
    body = {
        "links": [
            {"source_id": "S1", "target_id": "T1", "confidence": 95, "reason": "wife"},
            {"source_id": "S3", "target_id": "T2", "confidence": 95, "reason": "husband"},
            {"source_id": "S2", "target_id": "T1", "confidence": 70, "reason": "-உம் and"},
            {"source_id": "S2", "target_id": "T2", "confidence": 70, "reason": "-உம் and"},
        ],
        "nulls": [], "unplaced": [], "review_notes": [],
    }
    engine = open_engine(root, transport_for(body, body))
    response = call(engine, "alignment.window.autoAlign", {"chapter": "7", "verses": ["2"]})
    assert response["success"] is True, response
    [result] = response["result"]["results"]
    context = result["context"]
    written = [g for g in context["groups"] if g["bottomIds"]]
    # Two 1:1 groups, never a 3:2 block.
    assert sorted((len(g["topIds"]), len(g["bottomIds"])) for g in written) == [(1, 1), (1, 1)]
    words = {t["id"]: t["word"] for t in context["topTokens"] + context["bottomTokens"]}
    assert {(words[g["topIds"][0]], words[g["bottomIds"][0]]) for g in written} == {
        ("γυναῖκα", "மனைவியையும்"), ("ἄνδρα", "கணவனையும்"),
    }
    # καὶ is left for the reviewer: two link options and a grammatical one.
    assert result["verdict"] == "NEEDS_REVIEW"
    kinds = sorted((s["kind"], (s["target"] or {}).get("word", s["reason"])) for s in result["suggestions"])
    assert kinds == [("link", "கணவனையும்"), ("link", "மனைவியையும்"), ("null", "GRAMMATICAL")]
    # Accepting "grammatical" settles the verse.
    kai = context_id(context, top_word="καὶ")
    assert call(engine, "alignment.null.set", {
        "chapter": "7", "verse": "2", "side": "source", "id": kai, "reason": "GRAMMATICAL",
    })["success"]
    after = call(engine, "alignment.get", {"chapter": "7", "verse": "2"})["result"]
    assert after["accounted"] is True
