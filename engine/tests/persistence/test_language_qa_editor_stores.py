"""Workbench v6: the three Bridge-private stores behind the indic-qa editor
features (correction batches, learned fixes, flags), and the verse-history
readers over checkData/verseEdits. Rows are rewritten, never deleted, so
change_log keeps every image."""
import json
import time

import pytest

from tc_ai_bridge.tc_project import TranslationCoreProject
from tests.support.projects import fixture_project  # noqa: F401  (a fixture)

VERSE = "ஆதியிலே தேவன் வானத்தையும் பூமியையும் படைத்தார்."


@pytest.fixture
def project(fixture_project):
    return TranslationCoreProject(fixture_project)


def images(project, table, row_id):
    """Every change_log image of one row, oldest first, decoded."""
    rows = project.workbench.events_for_row(table, row_id, project_id=project.workbench_identity.project_id)
    return [{**row, "payload": json.loads(row["payload_json"])} for row in rows]


def test_the_three_v6_tables_are_on_a_fresh_project(project):
    assert project.workbench.schema_version() == 7
    for table in ("language_qa_batches", "language_qa_learned_fixes", "language_qa_flags"):
        assert project.workbench.rows(table, project_id=project.workbench_identity.project_id) == []


def test_a_batch_moves_to_undone_and_keeps_both_images(project):
    record = project.record_language_qa_batch({
        "batchId": "b1", "kind": "accept", "state": "applied", "chapter": "1", "scope": "chapter",
        "items": [{"chapter": "1", "verse": "1", "oldText": "a", "newText": "b"}], "createdAt": "2026-10-07T10:00:00Z"})
    assert record["schemaVersion"] == 1 and project.language_qa_batch("b1")["state"] == "applied"

    updated = project.set_language_qa_batch_state("b1", "undone", undoneBy="b2")
    assert (updated["state"], updated["undoneBy"], updated["items"]) == ("undone", "b2", record["items"])
    assert [b["batchId"] for b in project.language_qa_batches()] == ["b1"]
    states = [e["payload"]["state"] for e in images(project, "language_qa_batches",
                                                     project._language_qa_batch_row_id("b1"))]
    assert states == ["applied", "undone"]
    with pytest.raises(KeyError):
        project.set_language_qa_batch_state("missing", "undone")


def test_batches_are_listed_newest_first(project):
    for n, at in (("old", "2026-10-07T09:00:00Z"), ("new", "2026-10-07T11:00:00Z")):
        project.record_language_qa_batch({"batchId": n, "kind": "accept", "state": "applied", "createdAt": at})
    assert [b["batchId"] for b in project.language_qa_batches()] == ["new", "old"]


def test_a_learned_fix_counts_uses_and_forget_restore_retract_never_delete(project):
    first = project.record_learned_fix("தேவன்", "கடவுள்", ref="RUT 1:1", reviewer="Benz", source="edit")
    assert (first["count"], first["enabled"], first["firstRef"]) == (1, True, "RUT 1:1")
    again = project.record_learned_fix("தேவன்", "கடவுள்", ref="RUT 1:2", reviewer="Benz", source="scope", n=3)
    assert (again["count"], again["firstRef"], again["lastRef"]) == (4, "RUT 1:1", "RUT 1:2")
    assert project.learned_map() == {"தேவன்": ["கடவுள்"]}

    project.set_learned_fix_enabled("தேவன்", "கடவுள்", False)
    assert project.learned_map() == {}
    assert project.record_learned_fix("தேவன்", "கடவுள்", ref="RUT 1:3", reviewer="Benz",
                                      source="edit")["enabled"] is False, "forgotten stays forgotten"
    project.set_learned_fix_enabled("தேவன்", "கடவுள்", True)
    assert project.learned_map() == {"தேவன்": ["கடவுள்"]}

    assert project.retract_learned_fix("தேவன்", "கடவுள்", n=10)["count"] == 0
    assert project.learned_map() == {}, "a fix at zero is no longer offered"
    assert len(project.learned_fixes()) == 1, "and its row is kept"
    assert len(images(project, "language_qa_learned_fixes", project._learned_fix_row_id("தேவன்", "கடவுள்"))) == 6


def test_learned_fixes_are_keyed_in_nfc_and_most_used_first(project):
    # The same letter composed (U+0B94 TAMIL LETTER AU) and decomposed
    # (U+0B92 + U+0BD7): one row, keyed in NFC.
    composed, decomposed = "ஔ", "ஔ"
    project.record_learned_fix(decomposed, "x", ref="RUT 1:1", reviewer="r", source="edit")
    project.record_learned_fix(composed, "x", ref="RUT 1:1", reviewer="r", source="edit")
    project.record_learned_fix("a", "b", ref="RUT 1:1", reviewer="r", source="edit")
    project.record_learned_fix("a", "c", ref="RUT 1:1", reviewer="r", source="edit", n=5)
    assert [(f["old"], f["new"], f["count"]) for f in project.learned_fixes()] == [
        ("a", "c", 5), ("ஔ", "x", 2), ("a", "b", 1)]
    assert project.learned_map()["a"] == ["c", "b"]


def test_a_flag_is_created_open_updated_and_deleted_as_a_status(project):
    flag = project.record_language_qa_flag({"chapter": "1", "verse": "1", "start": 0, "end": 6, "text": "ஆதியிலே",
                                            "type": "style", "note": "check"}, reviewer="Benz")
    assert (flag["status"], flag["reviewer"]) == ("open", "Benz") and len(flag["flagId"]) == 16
    resolved = project.update_language_qa_flag(flag["flagId"], {"status": "resolved", "flagId": "spoofed"})
    assert resolved["flagId"] == flag["flagId"] and resolved["status"] == "resolved"
    project.update_language_qa_flag(flag["flagId"], {"status": "deleted"})
    assert project.language_qa_flags() == []
    assert [f["status"] for f in project.language_qa_flags(include_deleted=True)] == ["deleted"]
    statuses = [e["payload"]["status"] for e in images(project, "language_qa_flags",
                                                       project._language_qa_flag_row_id(flag["flagId"]))]
    assert statuses == ["open", "resolved", "deleted"]
    with pytest.raises(KeyError):
        project.update_language_qa_flag("missing", {"status": "resolved"})


def test_flags_list_in_reading_order_and_filter_by_chapter(project):
    for chapter, verse, start in (("2", "1", 0), ("1", "10", 0), ("1", "2", 5), ("1", "2", 1)):
        project.record_language_qa_flag({"chapter": chapter, "verse": verse, "start": start, "end": start + 1,
                                         "text": "x", "type": "other", "note": ""}, reviewer="r")
    assert [(f["chapter"], f["verse"], f["start"]) for f in project.language_qa_flags()] == [
        ("1", "2", 1), ("1", "2", 5), ("1", "10", 0), ("2", "1", 0)]
    assert {f["chapter"] for f in project.language_qa_flags(chapter="2")} == {"2"}


def test_verse_history_reads_the_native_verse_edit_records_newest_first(project):
    assert project.verse_edit_history("1", "1") == [] and project.verse_edit_counts("1") == {}
    project.apply_scripture_edit("1", "1", VERSE + " ஒன்று", username="Benz")
    time.sleep(0.01)
    project.apply_scripture_edit("1", "1", VERSE + " இரண்டு", username="Benz")
    history = project.verse_edit_history("1", "1")
    assert [e["verseAfter"] for e in history] == [VERSE + " இரண்டு", VERSE + " ஒன்று"]
    assert history[-1]["verseBefore"] == VERSE
    assert project.verse_edit_counts("1") == {"1": 2}
