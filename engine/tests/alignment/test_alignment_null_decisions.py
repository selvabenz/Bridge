"""#216: the null-decision store on its own, against a real project's workbench.

A row says "this token has no counterpart, for this reason". These tests pin the
store's own rules -- the closed reason set per side, the three writes per
change, the explicit update, invalidation inside a text edit -- without the
dispatcher; the protocol is covered in tests/service/test_alignment_null_rpc.py.
"""
import json

import pytest

from tc_ai_bridge.alignment_engine import AlignmentError, make_inventory
from tc_ai_bridge.alignment_null_decisions import TABLE, validate
from tc_ai_bridge.passage_semantic_runtime import alignment_state_digest
from tc_ai_bridge.tc_project import TranslationCoreProject
from tests.support.alignment_books import unaligned_verse, write_alignment_book


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "php"
    write_alignment_book(root, "php", {"1": {
        "3": unaligned_verse("y1 y2", [("τῷ", "G35880"), ("Θεῷ", "G23160")]),
    }})
    return TranslationCoreProject(root)


def _tokens(project):
    inventory = make_inventory(project.load_verse_alignment("1", "3"))
    article = next(t for t in inventory.top_ids.values() if t.word == "τῷ")
    y2 = next(t for t in inventory.bottom_ids.values() if t.word == "y2")
    return article, y2


def test_reasons_are_closed_per_side():
    assert validate("source", "implicit") == ("source", "IMPLICIT")
    assert validate("Target", "explicitation") == ("target", "EXPLICITATION")
    for side, reason in (("source", "EXPLICITATION"), ("target", "IMPLICIT"), ("source", ""), ("middle", "GRAMMATICAL")):
        with pytest.raises(AlignmentError):
            validate(side, reason)
    with pytest.raises(AlignmentError):
        validate("source", "GRAMMATICAL", "robot")


def test_set_writes_a_row_an_event_and_a_history_row_without_a_backup(project):
    article, _ = _tokens(project)
    decision = project.null_decisions.set("1", "3", "source", article, "grammatical", note="article")
    assert decision["reason"] == "GRAMMATICAL" and decision["state"] == "active"
    assert decision["token"]["signature"] == article.signature and decision["token"]["strong"] == "G35880"
    assert decision["origin"] == "human" and "previousReason" not in decision

    project_id = project.workbench_identity.project_id
    events = [e for e in project.workbench.change_log_entries(project_id) if e["table_name"] == TABLE]
    assert [e["op"] for e in events] == ["upsert", "nullDecide"]
    history = project.workbench.payloads("alignment_history", project_id=project_id, book_id="php",
                                         equals={"chapter": "1", "verse": "3"})
    assert [h["operation"] for h in history] == ["nullDecide"]
    assert "backupPath" not in history[0] and history[0]["nullDecision"]["id"] == decision["id"]


def test_same_reason_twice_is_refused_and_a_new_reason_is_an_explicit_update(project):
    article, _ = _tokens(project)
    first = project.null_decisions.set("1", "3", "source", article, "GRAMMATICAL")
    with pytest.raises(AlignmentError, match="already marked grammatical"):
        project.null_decisions.set("1", "3", "source", article, "GRAMMATICAL")
    second = project.null_decisions.set("1", "3", "source", article, "IMPLICIT")
    assert second["id"] == first["id"]
    assert second["reason"] == "IMPLICIT" and second["previousReason"] == "GRAMMATICAL"
    # Both versions survive in the append-only log.
    project_id = project.workbench_identity.project_id
    decided = [json.loads(e["payload_json"])["reason"] for e in project.workbench.events_for_row(
        TABLE, first["id"], project_id=project_id) if e["op"] == "nullDecide"]
    assert decided == ["GRAMMATICAL", "IMPLICIT"]


def test_a_burst_of_decisions_in_one_verse_keeps_every_history_row(project):
    article, y2 = _tokens(project)
    project.null_decisions.set("1", "3", "source", article, "GRAMMATICAL")
    project.null_decisions.set("1", "3", "target", y2, "GRAMMATICAL")
    history = project.workbench.payloads("alignment_history", project_id=project.workbench_identity.project_id,
                                         book_id="php", equals={"chapter": "1", "verse": "3"})
    assert len(history) == 2


def test_clear_deletes_the_row_and_logs_who_cleared_it(project):
    article, _ = _tokens(project)
    decision = project.null_decisions.set("1", "3", "source", article, "GRAMMATICAL", origin="ai-auto")
    project.null_decisions.clear(decision["id"])
    assert project.null_decisions.get(decision["id"]) is None
    project_id = project.workbench_identity.project_id
    ops = [e["op"] for e in project.workbench.events_for_row(TABLE, decision["id"], project_id=project_id)]
    assert ops == ["upsert", "nullDecide", "delete", "nullClear"]
    with pytest.raises(AlignmentError):
        project.null_decisions.clear(decision["id"])


def test_a_text_edit_that_removes_the_word_invalidates_its_decision(project):
    _, y2 = _tokens(project)
    decision = project.null_decisions.set("1", "3", "target", y2, "GRAMMATICAL")
    result = project.apply_scripture_edit("1", "3", "y1 y3", username="tester")
    assert result["nullDecisionsInvalidated"] == [decision["id"]]
    stored = project.null_decisions.get(decision["id"])
    assert stored["state"] == "invalid" and "no longer in the text of 1:3" in stored["invalidReason"]
    # Re-deciding an invalid row reactivates it under the same id.
    project.apply_scripture_edit("1", "3", "y1 y2", username="tester")
    _, y2_again = _tokens(project)
    revived = project.null_decisions.set("1", "3", "target", y2_again, "GRAMMATICAL")
    assert revived["id"] == decision["id"] and revived["state"] == "active"


def test_the_alignment_digest_moves_only_once_a_decision_exists(project):
    before = alignment_state_digest(project)
    assert project.null_decisions.digest() == ""
    assert alignment_state_digest(project) == before
    article, _ = _tokens(project)
    decision = project.null_decisions.set("1", "3", "source", article, "GRAMMATICAL")
    after_set = alignment_state_digest(project)
    assert after_set != before
    project.null_decisions.set("1", "3", "source", article, "IMPLICIT")
    assert alignment_state_digest(project) != after_set
    project.null_decisions.clear(decision["id"])
    assert alignment_state_digest(project) == before
