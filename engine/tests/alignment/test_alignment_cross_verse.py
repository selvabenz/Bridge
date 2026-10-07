"""
#117: Bridge-private cross-verse alignment links.

translationCore alignment groups are verse-local, so a link from a source token
in one verse to a target word in another lives in `bridge-workbench.sqlite3`
(`alignment_cross_verse_links`), never in `alignmentData/`. These tests drive
the protocol (`alignment.crossVerse.link` / `.unlink`, `alignment.get`,
`alignment.getRange`) against a real fixture project and then look at the
files and the database, not at mocks.
"""
import json

import pytest

from bridge_service import BridgeEngine
from tc_ai_bridge.cross_verse_links import TABLE
from tests.support.alignment import _write_book
from tests.support.projects import call


def _top(word: str, strong: str) -> dict:
    return {"word": word, "strong": strong, "lemma": word, "occurrence": 1, "occurrences": 1}


def _bottom(word: str, occurrence: int = 1, occurrences: int = 1) -> dict:
    return {"word": word, "occurrence": occurrence, "occurrences": occurrences, "type": "bottomWord"}


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "rut"
    _write_book(root, "rut", {
        "1": {
            # v1: one source token, nothing aligned, two words in the bank.
            "1": {
                "text": "தேவன் படைத்தார்",
                "alignment": {
                    "alignments": [{"topWords": [_top("אֱלֹהִים", "H430")], "bottomWords": []}],
                    "wordBank": [_bottom("தேவன்"), _bottom("படைத்தார்")],
                },
            },
            # v2: B aligned to y1, C unaligned, y2 in the bank.
            "2": {
                "text": "y1 y2",
                "alignment": {
                    "alignments": [
                        {"topWords": [_top("בָּרָא", "H1254")], "bottomWords": [_bottom("y1")]},
                        {"topWords": [_top("אֵת", "H853")], "bottomWords": []},
                    ],
                    "wordBank": [_bottom("y2")],
                },
            },
            # v3: untouched.
            "3": {
                "text": "z1 z2",
                "alignment": {
                    "alignments": [{"topWords": [_top("אוֹר", "H216")], "bottomWords": []}],
                    "wordBank": [_bottom("z1"), _bottom("z2")],
                },
            },
        },
    })
    return root


@pytest.fixture
def engine(project):
    engine = BridgeEngine()
    assert call(engine, "project.open", {"path": str(project)})["success"] is True
    return engine


def _ids(context, *, top=None, bottom=None):
    if top is not None:
        return next(t["id"] for t in context["topTokens"] if t["word"] == top)
    return next(t["id"] for t in context["bottomTokens"] if t["word"] == bottom)


def _link(engine, s_chapter, s_verse, top_word, t_chapter, t_verse, bottom_word):
    source = call(engine, "alignment.get", {"chapter": s_chapter, "verse": s_verse})["result"]
    target = call(engine, "alignment.get", {"chapter": t_chapter, "verse": t_verse})["result"]
    return call(engine, "alignment.crossVerse.link", {
        "source": {"chapter": s_chapter, "verse": s_verse, "topId": _ids(source, top=top_word)},
        "target": {"chapter": t_chapter, "verse": t_verse, "bottomId": _ids(target, bottom=bottom_word)},
    })


def test_link_annotates_both_verses_and_leaves_alignment_data_untouched(engine, project):
    before = (project / ".apps" / "translationCore" / "alignmentData" / "rut" / "1.json").read_text(encoding="utf-8")
    response = _link(engine, "1", "1", "אֱלֹהִים", "1", "2", "y2")
    assert response["success"] is True, response
    link = response["result"]["link"]
    assert link["state"] == "active"
    assert link["source"] == {
        "chapter": "1", "verse": "1", "word": "אֱלֹהִים", "occurrence": 1, "occurrences": 1,
        "signature": "אֱלֹהִים␟1␟1", "strong": "H430", "lemma": "אֱלֹהִים",
    }
    assert link["target"]["signature"] == "y2␟1␟1" and link["target"]["verse"] == "2"
    # Ids are resolved per verse, never stored.
    assert "topId" not in link["source"] and "bottomId" not in link["target"]

    source = response["result"]["source"]
    assert source["verse"] == "1"
    assert source["crossVerseRealizedIds"] == [_ids(source, top="אֱלֹהִים")]
    assert source["crossVerseRealized"] == 1 and source["crossVerseAccounted"] == 0
    assert source["gaps"] == {"sourceUnmatched": 0, "targetUnmatched": 2}
    assert source["fullyAccounted"] is False  # its own two bank words are still unaligned
    assert source["status"] == "untouched" and source["completionState"] == "pending"

    target = response["result"]["target"]
    assert target["crossVerseAccountedIds"] == [_ids(target, bottom="y2")]
    assert target["crossVerseAccounted"] == 1
    assert target["gaps"] == {"sourceUnmatched": 1, "targetUnmatched": 0}  # אֵת still has no counterpart
    assert target["status"] == "partial" and target["completionState"] == "pending"
    [entry] = target["crossVerseLinks"]
    assert entry["targetBottomId"] == _ids(target, bottom="y2") and entry["sourceTopId"] is None

    # The tC file is byte-for-byte what it was: the link is Bridge-private.
    after = (project / ".apps" / "translationCore" / "alignmentData" / "rut" / "1.json").read_text(encoding="utf-8")
    assert after == before
    # And the range view carries the same annotations.
    rng = call(engine, "alignment.getRange", {"chapter": "1", "verses": ["1", "2"]})["result"]
    assert [c["crossVerseRealized"] for c in rng["verses"]] == [1, 0]
    assert [c["crossVerseAccounted"] for c in rng["verses"]] == [0, 1]


def test_a_verse_whose_only_gaps_are_linked_is_fully_accounted_but_never_complete(engine):
    # v3's אוֹר realized in v1's "படைத்தார்" ... and v3's own two words realized from v1's אֱלֹהִים? No:
    # make v3 fully accounted from the target side: link v1.אֱלֹהִים -> v3.z1 and v2.אֵת -> v3.z2,
    # and its one source token אוֹר -> v1.தேவன்.
    assert _link(engine, "1", "1", "אֱלֹהִים", "1", "3", "z1")["success"]
    assert _link(engine, "1", "2", "אֵת", "1", "3", "z2")["success"]
    response = _link(engine, "1", "3", "אוֹר", "1", "1", "தேவன்")
    assert response["success"] is True, response
    v3 = call(engine, "alignment.get", {"chapter": "1", "verse": "3"})["result"]
    assert v3["gaps"] == {"sourceUnmatched": 0, "targetUnmatched": 0}
    assert v3["crossVerseAccounted"] == 2 and v3["crossVerseRealized"] == 1
    assert v3["fullyAccounted"] is True
    # tC truth is unchanged: nothing is grouped, so the verse is still untouched
    # and can never be marked complete through this route.
    assert v3["status"] == "untouched"
    assert v3["completionState"] == "pending"
    assert v3["canComplete"] is False
    assert v3["alignment"]["wordBank"] and all(not g["bottomWords"] for g in v3["alignment"]["alignments"])


def test_refusals(engine):
    # Same verse: use realign.
    same = _link(engine, "1", "2", "אֵת", "1", "2", "y2")
    assert same["success"] is False and same["error"]["code"] == "alignment_error"
    assert "same verse" in same["error"]["message"]
    # Source already aligned within its own verse.
    src_aligned = _link(engine, "1", "2", "בָּרָא", "1", "1", "தேவன்")
    assert src_aligned["success"] is False and src_aligned["error"]["code"] == "alignment_error"
    assert "already aligned within 1:2" in src_aligned["error"]["message"]
    # Target already aligned within its own verse.
    tgt_aligned = _link(engine, "1", "1", "אֱלֹהִים", "1", "2", "y1")
    assert tgt_aligned["success"] is False and tgt_aligned["error"]["code"] == "alignment_error"
    assert "already aligned within 1:2" in tgt_aligned["error"]["message"]
    # Unknown token id.
    bad_id = call(engine, "alignment.crossVerse.link", {
        "source": {"chapter": "1", "verse": "1", "topId": "H999"},
        "target": {"chapter": "1", "verse": "2", "bottomId": "T001"},
    })
    assert bad_id["success"] is False and bad_id["error"]["code"] == "alignment_error"
    # Unknown verse / chapter / missing fields are project errors.
    for params in (
        {"source": {"chapter": "1", "verse": "99", "topId": "H001"}, "target": {"chapter": "1", "verse": "2", "bottomId": "T001"}},
        {"source": {"chapter": "7", "verse": "1", "topId": "H001"}, "target": {"chapter": "1", "verse": "2", "bottomId": "T001"}},
        {"source": {"chapter": "1", "verse": "1"}, "target": {"chapter": "1", "verse": "2", "bottomId": "T001"}},
        {"source": None, "target": None},
    ):
        response = call(engine, "alignment.crossVerse.link", params)
        assert response["success"] is False and response["error"]["code"] == "project_error", params
    # A duplicate active link is refused rather than logged twice.
    assert _link(engine, "1", "1", "אֱלֹהִים", "1", "2", "y2")["success"] is True
    duplicate = _link(engine, "1", "1", "אֱלֹהִים", "1", "2", "y2")
    assert duplicate["success"] is False and "already linked" in duplicate["error"]["message"]
    # Nothing above touched the files.
    v2 = call(engine, "alignment.get", {"chapter": "1", "verse": "2"})["result"]
    assert [g["bottomIds"] for g in v2["groups"]] == [[_ids(v2, bottom="y1")], []]


def test_unlink_removes_the_annotation_and_the_row(engine):
    link = _link(engine, "1", "1", "אֱלֹהִים", "1", "2", "y2")["result"]["link"]
    response = call(engine, "alignment.crossVerse.unlink", {"linkId": link["id"]})
    assert response["success"] is True, response
    assert response["result"]["link"]["id"] == link["id"]
    assert response["result"]["source"]["crossVerseRealized"] == 0
    assert response["result"]["target"]["crossVerseAccounted"] == 0
    assert response["result"]["target"]["crossVerseLinks"] == []
    assert engine.project.cross_verse_links.get(link["id"]) is None
    again = call(engine, "alignment.crossVerse.unlink", {"linkId": link["id"]})
    assert again["success"] is False and again["error"]["code"] == "alignment_error"
    empty = call(engine, "alignment.crossVerse.unlink", {})
    assert empty["success"] is False and empty["error"]["code"] == "project_error"


def test_every_link_change_writes_a_row_image_a_domain_event_and_a_history_row(engine):
    link = _link(engine, "1", "1", "אֱלֹהִים", "1", "2", "y2")["result"]["link"]
    workbench = engine.project.workbench
    project_id = engine.project.workbench_identity.project_id
    events = [e for e in workbench.change_log_entries(project_id) if e["table_name"] == TABLE]
    assert [(e["op"], e["new_revision"]) for e in events] == [("upsert", 1), ("crossVerseLink", None)]
    assert json.loads(events[1]["payload_json"])["id"] == link["id"]

    history = workbench.payloads("alignment_history", project_id=project_id, book_id="rut",
                                 equals={"chapter": "1", "verse": "1"})
    assert [h["operation"] for h in history] == ["crossVerseLink"]
    assert "backupPath" not in history[0] and history[0]["link"]["id"] == link["id"]
    # No backup means nothing to restore: the verse's restore list stays empty.
    assert call(engine, "alignment.backups", {"chapter": "1", "verse": "1"})["result"]["history"] == []

    call(engine, "alignment.crossVerse.unlink", {"linkId": link["id"]})
    events = [e for e in workbench.change_log_entries(project_id) if e["table_name"] == TABLE]
    assert [e["op"] for e in events] == ["upsert", "crossVerseLink", "delete", "crossVerseUnlink"]
    history = workbench.payloads("alignment_history", project_id=project_id, book_id="rut",
                                 equals={"chapter": "1", "verse": "1"})
    assert [h["operation"] for h in history] == ["crossVerseLink", "crossVerseUnlink"]


def test_a_text_edit_that_removes_the_target_word_marks_the_link_invalid(engine):
    link = _link(engine, "1", "1", "אֱלֹהִים", "1", "2", "y2")["result"]["link"]
    # Keep y1, drop y2: the link's target signature disappears.
    result = engine.project.apply_scripture_edit("1", "2", "y1 y3", username="tester")
    assert result["crossVerseLinksInvalidated"] == [link["id"]]
    stored = engine.project.cross_verse_links.get(link["id"])
    assert stored["state"] == "invalid" and "no longer in the text of 1:2" in stored["invalidReason"]

    v2 = call(engine, "alignment.get", {"chapter": "1", "verse": "2"})["result"]
    assert v2["crossVerseAccounted"] == 0 and v2["crossVerseAccountedIds"] == []
    [entry] = v2["crossVerseLinks"]
    assert entry["state"] == "invalid" and entry["targetBottomId"] is None
    v1 = call(engine, "alignment.get", {"chapter": "1", "verse": "1"})["result"]
    assert v1["crossVerseRealized"] == 0  # an invalid link accounts for nothing
    assert v1["crossVerseLinks"][0]["state"] == "invalid"

    # An edit that keeps the word under the same signature keeps the link.
    link2 = _link(engine, "1", "3", "אוֹר", "1", "1", "தேவன்")["result"]["link"]
    result = engine.project.apply_scripture_edit("1", "1", "தேவன் உண்டாக்கினார்", username="tester")
    assert result["crossVerseLinksInvalidated"] == []
    assert engine.project.cross_verse_links.get(link2["id"])["state"] == "active"

    events = [e["op"] for e in engine.project.workbench.change_log_entries(
        engine.project.workbench_identity.project_id) if e["table_name"] == TABLE]
    assert events.count("crossVerseInvalidate") == 1


def test_relinking_an_invalid_pair_reactivates_it(engine):
    link = _link(engine, "1", "1", "אֱלֹהִים", "1", "2", "y2")["result"]["link"]
    engine.project.apply_scripture_edit("1", "2", "y1", username="tester")
    assert engine.project.cross_verse_links.get(link["id"])["state"] == "invalid"
    engine.project.apply_scripture_edit("1", "2", "y1 y2", username="tester")
    relinked = _link(engine, "1", "1", "אֱלֹהִים", "1", "2", "y2")
    assert relinked["success"] is True, relinked
    assert relinked["result"]["link"]["id"] == link["id"]
    assert relinked["result"]["link"]["state"] == "active"
    assert "invalidReason" not in relinked["result"]["link"]


def test_links_survive_a_restart(engine, project):
    link = _link(engine, "1", "1", "אֱלֹהִים", "1", "2", "y2")["result"]["link"]
    second = BridgeEngine()
    assert call(second, "project.open", {"path": str(project)})["success"] is True
    v2 = call(second, "alignment.get", {"chapter": "1", "verse": "2"})["result"]
    assert [entry["id"] for entry in v2["crossVerseLinks"]] == [link["id"]]
    assert v2["crossVerseAccounted"] == 1
