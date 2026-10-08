"""#217: one token, one home; cross-verse N:M groups.

Every token has at most one home -- a tC group, a cross-verse link group or a
null decision -- and a 1:N / N:1 / N:M cross-verse realization is one composite
group, written and removed as a unit.
"""
import json

import pytest

from bridge_service import BridgeEngine
from tc_ai_bridge.cross_verse_links import TABLE
from tests.support.alignment_books import context_id, unaligned_verse, write_alignment_book
from tests.support.projects import call


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "php"
    write_alignment_book(root, "php", {"1": {
        # Greek v3 is realised in Tamil v6 (the IRV rotation of PHP 1:3-6).
        "3": unaligned_verse("நற்செய்தி நாள்", [("εὐχαριστῶ", "G21680"), ("ἐπὶ", "G19090"), ("πάσῃ", "G39560")]),
        "6": unaligned_verse("நான் போதெல்லாம் ஸ்தோத்திரிக்கிறேன்", [("ἄχρι", "G08910")]),
        "7": unaligned_verse("வேறு", [("καθώς", "G25310")]),
    }})
    return root


@pytest.fixture
def engine(project):
    engine = BridgeEngine()
    assert call(engine, "project.open", {"path": str(project)})["success"] is True
    return engine


def _get(engine, verse):
    return call(engine, "alignment.get", {"chapter": "1", "verse": verse})["result"]


def _src(engine, verse, word):
    return {"chapter": "1", "verse": verse, "topId": context_id(_get(engine, verse), top_word=word)}


def _tgt(engine, verse, word):
    return {"chapter": "1", "verse": verse, "bottomId": context_id(_get(engine, verse), bottom_word=word)}


def test_an_n_to_m_group_is_one_group_with_one_history_row(engine):
    response = call(engine, "alignment.crossVerse.link", {
        "sources": [_src(engine, "3", "ἐπὶ"), _src(engine, "3", "πάσῃ")],
        "targets": [_tgt(engine, "6", "போதெல்லாம்")],
    })
    assert response["success"] is True, response
    group = response["result"]["group"]
    assert group["relation"] == "many-to-one" and len(group["linkIds"]) == 2 and group["extended"] is False

    v3 = response["result"]["source"]
    [g] = v3["crossVerseGroups"]
    assert g["groupId"] == group["groupId"] and g["relation"] == "many-to-one"
    assert sorted(g["sourceTopIds"]) == sorted([context_id(v3, top_word="ἐπὶ"), context_id(v3, top_word="πάσῃ")])
    assert v3["gaps"]["sourceUnmatched"] == 1  # εὐχαριστῶ
    v6 = response["result"]["target"]
    assert v6["crossVerseAccountedIds"] == [context_id(v6, bottom_word="போதெல்லாம்")]

    workbench = engine.project.workbench
    project_id = engine.project.workbench_identity.project_id
    history = workbench.payloads("alignment_history", project_id=project_id, book_id="php",
                                 equals={"chapter": "1", "verse": "3"})
    assert [h["operation"] for h in history] == ["crossVerseLinkGroup"]
    assert sorted(history[0]["link"]["links"]) == sorted(group["linkIds"])
    events = [e["op"] for e in workbench.change_log_entries(project_id) if e["table_name"] == TABLE]
    assert events.count("crossVerseLink") == 2


def test_dragging_a_second_word_extends_the_group_instead_of_making_a_second(engine):
    first = call(engine, "alignment.crossVerse.link", {
        "source": _src(engine, "3", "εὐχαριστῶ"), "target": _tgt(engine, "6", "ஸ்தோத்திரிக்கிறேன்"),
    })["result"]["group"]
    second = call(engine, "alignment.crossVerse.link", {
        "source": _src(engine, "3", "εὐχαριστῶ"), "target": _tgt(engine, "6", "நான்"),
    })
    assert second["success"] is True, second
    group = second["result"]["group"]
    assert group["extended"] is True and group["relation"] == "one-to-many"
    assert group["groupId"] != first["groupId"]
    [g] = second["result"]["source"]["crossVerseGroups"]
    assert len(g["targets"]) == 2
    # The old group's rows are gone; only the new group remains.
    assert engine.project.cross_verse_links.group_members(first["groupId"]) == []
    assert len(engine.project.cross_verse_links.group_members(group["groupId"])) == 2


def test_a_word_already_linked_into_another_verse_is_refused(engine):
    assert call(engine, "alignment.crossVerse.link", {
        "source": _src(engine, "3", "εὐχαριστῶ"), "target": _tgt(engine, "6", "ஸ்தோத்திரிக்கிறேன்"),
    })["success"]
    third_verse = call(engine, "alignment.crossVerse.link", {
        "source": _src(engine, "3", "εὐχαριστῶ"), "target": _tgt(engine, "7", "வேறு"),
    })
    assert third_verse["success"] is False
    assert "already linked to ஸ்தோத்திரிக்கிறேன் (1:6)" in third_verse["error"]["message"]
    other_source = call(engine, "alignment.crossVerse.link", {
        "source": _src(engine, "7", "καθώς"), "target": _tgt(engine, "6", "ஸ்தோத்திரிக்கிறேன்"),
    })
    assert other_source["success"] is False and "already linked" in other_source["error"]["message"]


def test_a_group_spans_exactly_two_verses(engine):
    response = call(engine, "alignment.crossVerse.link", {
        "sources": [_src(engine, "3", "ἐπὶ"), _src(engine, "6", "ἄχρι")],
        "targets": [_tgt(engine, "7", "வேறு")],
    })
    assert response["success"] is False and "one source verse" in response["error"]["message"]


def test_unlinking_any_pair_removes_the_whole_group(engine):
    group = call(engine, "alignment.crossVerse.link", {
        "sources": [_src(engine, "3", "ἐπὶ"), _src(engine, "3", "πάσῃ")],
        "targets": [_tgt(engine, "6", "போதெல்லாம்"), _tgt(engine, "6", "நான்")],
    })["result"]["group"]
    assert group["relation"] == "many-to-many" and len(group["linkIds"]) == 4
    response = call(engine, "alignment.crossVerse.unlink", {"linkId": group["linkIds"][2]})
    assert response["success"] is True, response
    assert response["result"]["source"]["crossVerseGroups"] == []
    assert engine.project.cross_verse_links.group_members(group["groupId"]) == []
    history = engine.project.workbench.payloads(
        "alignment_history", project_id=engine.project.workbench_identity.project_id, book_id="php",
        equals={"chapter": "1", "verse": "3"})
    assert [h["operation"] for h in history].count("crossVerseUnlinkGroup") == 1

    again = call(engine, "alignment.crossVerse.link", {
        "sources": [_src(engine, "3", "ἐπὶ")], "targets": [_tgt(engine, "6", "போதெல்லாம்")],
    })["result"]["group"]
    by_group = call(engine, "alignment.crossVerse.unlink", {"groupId": again["groupId"]})
    assert by_group["success"] is True and by_group["result"]["target"]["crossVerseGroups"] == []


def test_removing_one_target_word_invalidates_the_whole_group(engine):
    group = call(engine, "alignment.crossVerse.link", {
        "sources": [_src(engine, "3", "εὐχαριστῶ")],
        "targets": [_tgt(engine, "6", "நான்"), _tgt(engine, "6", "ஸ்தோத்திரிக்கிறேன்")],
    })["result"]["group"]
    result = engine.project.apply_scripture_edit("1", "6", "போதெல்லாம் ஸ்தோத்திரிக்கிறேன்", username="tester")
    assert sorted(result["crossVerseLinksInvalidated"]) == sorted(group["linkIds"])
    members = engine.project.cross_verse_links.group_members(group["groupId"])
    assert {m["state"] for m in members} == {"invalid"}
    v3 = _get(engine, "3")
    assert v3["crossVerseRealized"] == 0


def test_realign_refuses_a_token_that_already_has_a_home(engine):
    assert call(engine, "alignment.crossVerse.link", {
        "source": _src(engine, "3", "εὐχαριστῶ"), "target": _tgt(engine, "6", "நான்"),
    })["success"]
    v3 = _get(engine, "3")
    refused = call(engine, "alignment.realign", {
        "chapter": "1", "verse": "3", "topIds": [context_id(v3, top_word="εὐχαριστῶ")],
        "bottomIds": [context_id(v3, bottom_word="நாள்")], "expectedOriginal": v3["alignment"],
    })
    assert refused["success"] is False and "remove that link before aligning it here" in refused["error"]["message"]
    v6 = _get(engine, "6")
    refused_target = call(engine, "alignment.realign", {
        "chapter": "1", "verse": "6", "topIds": [context_id(v6, top_word="ἄχρι")],
        "bottomIds": [context_id(v6, bottom_word="நான்")], "expectedOriginal": v6["alignment"],
    })
    assert refused_target["success"] is False and "is linked to" in refused_target["error"]["message"]

    # A null decision is a home too.
    assert call(engine, "alignment.null.set", {
        "chapter": "1", "verse": "3", "side": "source", "id": context_id(v3, top_word="ἐπὶ"), "reason": "GRAMMATICAL",
    })["success"]
    v3 = _get(engine, "3")
    null_refused = call(engine, "alignment.realign", {
        "chapter": "1", "verse": "3", "topIds": [context_id(v3, top_word="ἐπὶ")],
        "bottomIds": [context_id(v3, bottom_word="நாள்")], "expectedOriginal": v3["alignment"],
    })
    assert null_refused["success"] is False and "is marked grammatical" in null_refused["error"]["message"]

    # Ordinary realigns of homeless tokens still work.
    ok = call(engine, "alignment.realign", {
        "chapter": "1", "verse": "3", "topIds": [context_id(v3, top_word="πάσῃ")],
        "bottomIds": [context_id(v3, bottom_word="நாள்")], "expectedOriginal": v3["alignment"],
    })
    assert ok["success"] is True, ok


def test_ai_auto_origin_and_run_id_ride_on_the_events(engine):
    response = engine.link_cross_verse(
        None, None, "ai-auto", run_id="run-1",
        sources=[_src(engine, "3", "ἐπὶ"), _src(engine, "3", "πάσῃ")],
        targets=[_tgt(engine, "6", "போதெல்லாம்")],
    )
    project_id = engine.project.workbench_identity.project_id
    events = [json.loads(e["payload_json"]) for e in engine.project.workbench.change_log_entries(project_id)
              if e["table_name"] == TABLE and e["op"] == "crossVerseLink"]
    assert {(e["origin"], e["runId"], e["groupId"]) for e in events} == {("ai-auto", "run-1", response["group"]["groupId"])}
    # The row itself carries no origin: the fact is the same however it was reached (#146).
    row = engine.project.cross_verse_links.get(response["group"]["linkIds"][0])
    assert "origin" not in row and row["groupId"] == response["group"]["groupId"]
