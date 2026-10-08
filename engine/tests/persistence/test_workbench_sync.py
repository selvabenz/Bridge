"""#77: sync readiness without a server.

An unsynced cursor, acknowledgement, and a JSON-lines export/import that
carries an event batch between two workbench databases (a USB stick). Import
goes through the same revision check every local write uses; a divergent
row is a conflict handed back, never an overwrite.

Also the workbench v1 -> v2 migration: change_log gains `columns_json` and
its immutability trigger is rebuilt, which a reader should not take on trust.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from tc_ai_bridge.workbench_repository import (
    MUTABLE_TABLES,
    WORKBENCH_SCHEMA_VERSION,
    WorkbenchRepository,
    _MIGRATION_V1,
    _MIGRATION_V2,
    _MIGRATION_V3,
)
from tests.support.workbench import _REQUIRED_EXTRA_COLUMNS, _write


_SECOND_ROW = {
    "ai_review_results": {"extra_columns": {"chapter": "2"}},
    "semantic_mappings": {"extra_columns": {"fingerprint": "fp2"}},
    "project_state": {"extra_columns": {"key": "k2"}},
    "progress_chapters": {"extra_columns": {"chapter": "2"}},
    "check_findings": {"extra_columns": {"chapter": "2"}},
    "progress_totals": {"book_id": "gen"},
    "triage_verdicts": {"book_id": "gen"},
}


def _events(repo, project_id="proj-1"):
    return repo.change_log_entries(project_id)


def test_unsynced_is_a_cursor_and_mark_synced_advances_it(tmp_path):
    repo = WorkbenchRepository(tmp_path / "a.sqlite3")
    first = _write(repo, "check_cache", "c1")
    second = _write(repo, "check_cache", "c2")
    third = _write(repo, "check_cache", "c1")

    pending = repo.unsynced(project_id="proj-1")
    assert [e["seq"] for e in pending] == [first["seq"], second["seq"], third["seq"]]
    assert [e["seq"] for e in repo.unsynced(project_id="proj-1", after_seq=first["seq"])] == [second["seq"], third["seq"]]
    assert len(repo.unsynced(project_id="proj-1", limit=1)) == 1

    assert repo.mark_synced([first["eventId"], second["eventId"]], synced_at="2026-09-15T00:00:00Z") == 2
    assert repo.mark_synced([first["eventId"]]) == 0, "acknowledging twice is a no-op"
    assert [e["seq"] for e in repo.unsynced(project_id="proj-1")] == [third["seq"]]
    assert _events(repo)[0]["synced_at"] == "2026-09-15T00:00:00Z"


def test_every_row_event_carries_its_lifted_columns(tmp_path):
    repo = WorkbenchRepository(tmp_path / "a.sqlite3")
    _write(repo, "human_decisions", "h1", extra_columns={"chapter": "1", "verse": "2", "decision": "accepted"})
    event = _events(repo)[0]
    assert json.loads(event["columns_json"]) == {
        "kind": "qa", "key": "k1", "chapter": "1", "verse": "2", "decision": "accepted",
    }
    _write(repo, "check_cache", "c1")
    assert _events(repo)[1]["columns_json"] is None


@pytest.mark.parametrize("table", MUTABLE_TABLES)
def test_export_then_import_into_an_empty_database_reproduces_the_rows(tmp_path, table):
    """The issue's test, per table: rows, revisions, lifted columns and the
    event log itself all arrive intact, and importing twice changes nothing."""
    source = WorkbenchRepository(tmp_path / "source.sqlite3")
    _write(source, table, "r1", payload={"v": 1})
    _write(source, table, "r1", payload={"v": 2})
    # Several tables are UNIQUE on their lifted columns within a book, so the
    # second row has to differ where the table says it must.
    second = _SECOND_ROW.get(table, {})
    _write(source, table, "r2", payload={"v": 3}, **second)
    source.append_event(table, "r1", project_id="proj-1", book_id="rut", op="noted",
                        payload={"note": "domain event"}, actor_id="human", device_id="dev-1")
    source._delete(table, "r2", project_id="proj-1", book_id="rut", actor_id="human", device_id="dev-1")

    exported = source.export_events(tmp_path / "batch.jsonl", project_id="proj-1")
    assert exported["count"] == 5
    lines = (tmp_path / "batch.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 5 and all(json.loads(line)["event_id"] for line in lines)

    target = WorkbenchRepository(tmp_path / "target.sqlite3")
    result = target.import_events(tmp_path / "batch.jsonl")
    assert (result["applied"], result["duplicates"], result["conflicts"], result["malformed"]) == (5, 0, [], 0)

    row = target.get(table, "r1")
    assert row is not None and row["revision"] == 2 and json.loads(row["payload_json"]) == {"v": 2}
    for column, value in _REQUIRED_EXTRA_COLUMNS.get(table, {}).items():
        assert row[column] == value
    assert target.get(table, "r2") is None

    source_log = [(e["event_id"], e["op"], e["row_key"], e["base_revision"], e["new_revision"], e["payload_json"], e["columns_json"], e["actor_id"], e["created_at"]) for e in _events(source)]
    target_log = [(e["event_id"], e["op"], e["row_key"], e["base_revision"], e["new_revision"], e["payload_json"], e["columns_json"], e["actor_id"], e["created_at"]) for e in _events(target)]
    assert target_log == source_log
    assert all(e["synced_at"] for e in _events(target)), "imported events have nowhere to travel back to"

    again = target.import_events(tmp_path / "batch.jsonl")
    assert (again["applied"], again["duplicates"]) == (0, 5)
    assert len(_events(target)) == 5


def test_a_divergent_local_row_is_a_conflict_not_an_overwrite(tmp_path):
    source = WorkbenchRepository(tmp_path / "source.sqlite3")
    target = WorkbenchRepository(tmp_path / "target.sqlite3")
    # Both sides start from the same first version, then each edits it.
    _write(source, "check_cache", "shared", payload={"v": "origin"})
    source.export_events(tmp_path / "base.jsonl", project_id="proj-1")
    target.import_events(tmp_path / "base.jsonl")
    _write(target, "check_cache", "shared", payload={"v": "mine"})
    _write(source, "check_cache", "shared", payload={"v": "theirs"})

    source.export_events(tmp_path / "delta.jsonl", project_id="proj-1", after_seq=1)
    result = target.import_events(tmp_path / "delta.jsonl")

    assert result["applied"] == 0
    [conflict] = result["conflicts"]
    assert conflict["reason"] == "revision_mismatch"
    assert (conflict["table"], conflict["rowKey"], conflict["baseRevision"], conflict["localRevision"]) == ("check_cache", "shared", 1, 2)
    assert json.loads(target.get("check_cache", "shared")["payload_json"]) == {"v": "mine"}
    assert len(_events(target)) == 2, "a conflicting event is not logged as applied"


def test_an_event_whose_base_is_missing_locally_is_a_conflict(tmp_path):
    source = WorkbenchRepository(tmp_path / "source.sqlite3")
    _write(source, "check_cache", "c1")
    _write(source, "check_cache", "c1")
    source.export_events(tmp_path / "tail.jsonl", project_id="proj-1", after_seq=1)

    target = WorkbenchRepository(tmp_path / "target.sqlite3")
    result = target.import_events(tmp_path / "tail.jsonl")
    assert [c["reason"] for c in result["conflicts"]] == ["missing_base"]
    assert target.get("check_cache", "c1") is None


def test_only_unsynced_export_and_malformed_lines(tmp_path):
    repo = WorkbenchRepository(tmp_path / "a.sqlite3")
    first = _write(repo, "check_cache", "c1")
    _write(repo, "check_cache", "c2")
    repo.mark_synced([first["eventId"]])
    exported = repo.export_events(tmp_path / "u.jsonl", project_id="proj-1", only_unsynced=True)
    assert exported["count"] == 1

    with (tmp_path / "u.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("not json\n")
        handle.write(json.dumps({"event_id": "x", "table_name": "not_a_table"}) + "\n")
    target = WorkbenchRepository(tmp_path / "b.sqlite3")
    result = target.import_events(tmp_path / "u.jsonl")
    # c2's only event is its creation, which needs no base; the two bad
    # lines are counted and skipped rather than aborting the batch.
    assert (result["applied"], result["malformed"], result["conflicts"]) == (1, 2, [])
    assert target.get("check_cache", "c2") is not None and target.get("check_cache", "c1") is None


def test_workbench_v1_to_v2_keeps_v1_rows_readable_and_the_log_immutable(tmp_path):
    """v2 adds a column and rebuilds the trigger. A v1 database must come up
    at v2 with its rows and events intact, the backup taken, and the trigger
    still rejecting everything but synced_at."""
    path = tmp_path / "bridge-workbench.sqlite3"
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, schema_id TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        conn.executescript(
            "BEGIN;\n" + _MIGRATION_V1
            + "\nINSERT INTO schema_migrations(version,schema_id,applied_at) VALUES(1,'bridge-workbench-v1','t');\nCOMMIT;"
        )
        conn.execute(
            "INSERT INTO check_cache(id,project_id,book_id,revision,actor_id,device_id,created_at,updated_at,payload_json) "
            "VALUES('c1','proj-1','rut',1,'human','dev-1','t','t','{\"v\":1}')"
        )
        conn.execute(
            "INSERT INTO change_log(event_id,project_id,book_id,table_name,row_key,op,base_revision,new_revision,"
            "actor_id,device_id,created_at,payload_json) VALUES('e1','proj-1','rut','check_cache','c1','upsert',NULL,1,"
            "'human','dev-1','t','{\"v\":1}')"
        )
        conn.commit()
    finally:
        conn.close()

    repo = WorkbenchRepository(path)
    assert repo.schema_version() == WORKBENCH_SCHEMA_VERSION == 7
    assert json.loads(repo.get("check_cache", "c1")["payload_json"]) == {"v": 1}
    [event] = _events(repo)
    assert event["columns_json"] is None and event["event_id"] == "e1"
    assert list((tmp_path / "backups").glob("pre-workbench-v2-*"))

    conn = sqlite3.connect(str(path))
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE change_log SET columns_json='{}' WHERE event_id='e1'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM change_log WHERE event_id='e1'")
        conn.execute("UPDATE change_log SET synced_at='now' WHERE event_id='e1'")
    finally:
        conn.close()

    # And a later write on the upgraded database records its lifted columns.
    _write(repo, "human_decisions", "h1")
    assert json.loads(_events(repo)[-1]["columns_json"]) == {"kind": "qa", "key": "k1"}


def test_workbench_v2_to_v3_adds_the_cross_verse_link_table_and_keeps_v2_data(tmp_path):
    """v3 (#117) adds `alignment_cross_verse_links`. A v2 database must come up
    at v3 with its rows and events intact, the backup taken, the new table
    writable through the ordinary `_write` path (so it is a mutable table with
    the nine common columns), and its UNIQUE pair index enforced."""
    path = tmp_path / "bridge-workbench.sqlite3"
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, schema_id TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        conn.executescript(
            "BEGIN;\n" + _MIGRATION_V1
            + "\nINSERT INTO schema_migrations(version,schema_id,applied_at) VALUES(1,'bridge-workbench-v1','t');\n"
            + _MIGRATION_V2
            + "\nINSERT INTO schema_migrations(version,schema_id,applied_at) VALUES(2,'bridge-workbench-v1','t');\nCOMMIT;"
        )
        conn.execute(
            "INSERT INTO alignment_history(id,project_id,book_id,revision,actor_id,device_id,created_at,updated_at,"
            "payload_json,chapter,verse,backup_path) VALUES('a1','proj-1','rut',1,'human','dev-1','t','t',"
            "'{\"operation\":\"realign\"}','1','2','backups/x.json')"
        )
        conn.execute(
            "INSERT INTO change_log(event_id,project_id,book_id,table_name,row_key,op,base_revision,new_revision,"
            "actor_id,device_id,created_at,payload_json,columns_json) VALUES('e1','proj-1','rut','alignment_history',"
            "'a1','upsert',NULL,1,'human','dev-1','t','{}','{\"chapter\":\"1\"}')"
        )
        conn.commit()
        assert "alignment_cross_verse_links" not in {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()

    repo = WorkbenchRepository(path)
    assert repo.schema_version() == WORKBENCH_SCHEMA_VERSION == 7
    assert json.loads(repo.get("alignment_history", "a1")["payload_json"]) == {"operation": "realign"}
    [event] = _events(repo)
    assert event["event_id"] == "e1" and json.loads(event["columns_json"]) == {"chapter": "1"}
    assert list((tmp_path / "backups").glob("pre-workbench-v3-*"))

    lifted = {
        "chapter": "1", "verse": "3", "source_signature": "a\u241f1\u241f1",
        "target_chapter": "1", "target_verse": "6", "target_signature": "b\u241f1\u241f1", "state": "active",
    }
    _write(repo, "alignment_cross_verse_links", "l1", payload={"state": "active"}, extra_columns=lifted)
    row = repo.get("alignment_cross_verse_links", "l1")
    assert row is not None and row["target_verse"] == "6" and row["state"] == "active"
    assert json.loads(_events(repo)[-1]["columns_json"]) == lifted
    # The pair is unique per project/book: a second row for the same pair is refused.
    with pytest.raises(sqlite3.IntegrityError):
        _write(repo, "alignment_cross_verse_links", "l2", payload={"state": "active"}, extra_columns=lifted)


def test_workbench_v3_to_v4_adds_the_language_qa_cache_one_row_per_chapter(tmp_path):
    """v4 (#169 Phase 4.1) adds `language_qa_cache`: a v3 database comes up at
    v4 with a backup, and the table is an ordinary mutable table whose
    (project, book, chapter) is unique."""
    path = tmp_path / "bridge-workbench.sqlite3"
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, schema_id TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        conn.executescript(
            "BEGIN;\n" + _MIGRATION_V1
            + "\nINSERT INTO schema_migrations(version,schema_id,applied_at) VALUES(1,'bridge-workbench-v1','t');\n"
            + _MIGRATION_V2
            + "\nINSERT INTO schema_migrations(version,schema_id,applied_at) VALUES(2,'bridge-workbench-v1','t');\n"
            + _MIGRATION_V3
            + "\nINSERT INTO schema_migrations(version,schema_id,applied_at) VALUES(3,'bridge-workbench-v1','t');\nCOMMIT;"
        )
    finally:
        conn.close()
    repo = WorkbenchRepository(path)
    assert repo.schema_version() == WORKBENCH_SCHEMA_VERSION == 7
    assert list((tmp_path / "backups").glob("pre-workbench-v4-*"))
    _write(repo, "language_qa_cache", "c1", payload={"verses": {}}, extra_columns={"chapter": "1"})
    assert repo.get("language_qa_cache", "c1")["chapter"] == "1"
    with pytest.raises(sqlite3.IntegrityError):
        _write(repo, "language_qa_cache", "c2", payload={"verses": {}}, extra_columns={"chapter": "1"})


def test_workbench_v4_to_v5_rebuilds_human_decisions_keeping_every_row_and_allows_housestyle(tmp_path):
    """v5 (#169 Phase 6.3) rebuilds `human_decisions` to widen its kind CHECK.
    A rebuild is exactly what a reader should not take on trust: every row,
    column and revision must survive, the unique key must still hold, and the
    new kind must be writable while an unknown one is still refused."""
    from tc_ai_bridge.workbench_repository import _MIGRATION_V4
    path = tmp_path / "bridge-workbench.sqlite3"
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, schema_id TEXT NOT NULL, applied_at TEXT NOT NULL)")
        script = "BEGIN;\n"
        for version, block in enumerate((_MIGRATION_V1, _MIGRATION_V2, _MIGRATION_V3, _MIGRATION_V4), start=1):
            script += block + f"\nINSERT INTO schema_migrations(version,schema_id,applied_at) VALUES({version},'bridge-workbench-v1','t');\n"
        conn.executescript(script + "COMMIT;")
        conn.execute(
            "INSERT INTO human_decisions(id,project_id,book_id,revision,actor_id,device_id,created_at,updated_at,"
            "payload_json,kind,chapter,verse,key,decision) VALUES('d1','proj-1','rut',3,'human','dev-1','c','u',"
            "'{\"decision\":\"ignored\"}','qa','1','2','finding-1','ignored')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO human_decisions(id,project_id,book_id,actor_id,device_id,created_at,updated_at,"
                "payload_json,kind,key) VALUES('d2','proj-1','rut','h','d','c','u','{}','housestyle','k')")
        conn.commit()
    finally:
        conn.close()
    repo = WorkbenchRepository(path)
    assert repo.schema_version() == WORKBENCH_SCHEMA_VERSION == 7
    assert list((tmp_path / "backups").glob("pre-workbench-v5-*"))
    row = repo.get("human_decisions", "d1")
    assert (row["revision"], row["kind"], row["chapter"], row["verse"], row["key"], row["decision"],
            row["created_at"], row["updated_at"]) == (3, "qa", "1", "2", "finding-1", "ignored", "c", "u")
    assert json.loads(row["payload_json"]) == {"decision": "ignored"}
    _write(repo, "human_decisions", "h5", extra_columns={"kind": "housestyle", "key": "word-in-book|r|w"})
    assert repo.get("human_decisions", "h5")["kind"] == "housestyle"
    with pytest.raises(sqlite3.IntegrityError):
        _write(repo, "human_decisions", "h6", extra_columns={"kind": "nonsense", "key": "k"})
    with pytest.raises(sqlite3.IntegrityError):  # the unique natural key survived the rebuild
        _write(repo, "human_decisions", "h7", extra_columns={"kind": "qa", "chapter": "1", "verse": "2", "key": "finding-1"})


def test_workbench_v5_to_v6_adds_the_three_editor_stores_and_keeps_v5_data(tmp_path):
    """v6 (indic-qa editor features) only adds tables: a v5 database comes up at
    v6 with a backup, its rows are untouched, and the three new tables are
    ordinary mutable tables (a learned fix's pair is unique per book)."""
    from tc_ai_bridge.workbench_repository import _MIGRATION_V4, _MIGRATION_V5
    path = tmp_path / "bridge-workbench.sqlite3"
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, schema_id TEXT NOT NULL, applied_at TEXT NOT NULL)")
        script = "BEGIN;\n"
        blocks = (_MIGRATION_V1, _MIGRATION_V2, _MIGRATION_V3, _MIGRATION_V4, _MIGRATION_V5)
        for version, block in enumerate(blocks, start=1):
            script += block + f"\nINSERT INTO schema_migrations(version,schema_id,applied_at) VALUES({version},'bridge-workbench-v1','t');\n"
        conn.executescript(script + "COMMIT;")
        conn.execute(
            "INSERT INTO human_decisions(id,project_id,book_id,revision,actor_id,device_id,created_at,updated_at,"
            "payload_json,kind,chapter,verse,key,decision) VALUES('d1','proj-1','rut',2,'human','dev-1','c','u',"
            "'{\"decision\":\"ignored\"}','housestyle','','','word-in-book|r|w','active')")
        conn.execute(
            "INSERT INTO language_qa_cache(id,project_id,book_id,actor_id,device_id,created_at,updated_at,"
            "payload_json,chapter) VALUES('c1','proj-1','rut','h','d','c','u','{\"verses\":{}}','1')")
        conn.commit()
    finally:
        conn.close()
    repo = WorkbenchRepository(path)
    assert repo.schema_version() == WORKBENCH_SCHEMA_VERSION == 7
    assert list((tmp_path / "backups").glob("pre-workbench-v6-*"))
    row = repo.get("human_decisions", "d1")
    assert (row["revision"], row["kind"], row["key"], row["decision"]) == (2, "housestyle", "word-in-book|r|w", "active")
    assert repo.get("language_qa_cache", "c1")["chapter"] == "1"
    _write(repo, "language_qa_batches", "b1", payload={"state": "applied"},
           extra_columns={"chapter": "1", "kind": "accept", "state": "applied"})
    _write(repo, "language_qa_flags", "f1", payload={"status": "open"},
           extra_columns={"chapter": "1", "verse": "2", "status": "open"})
    pair = {"old_word": "a", "new_word": "b", "enabled": 1}
    _write(repo, "language_qa_learned_fixes", "l1", payload={"count": 1}, extra_columns=pair)
    assert repo.get("language_qa_batches", "b1")["state"] == "applied"
    assert repo.get("language_qa_flags", "f1")["status"] == "open"
    with pytest.raises(sqlite3.IntegrityError):
        _write(repo, "language_qa_learned_fixes", "l2", payload={"count": 1}, extra_columns=pair)
