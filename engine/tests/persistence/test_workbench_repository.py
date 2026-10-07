"""#75: the workbench SQLite repository skeleton.

Covers exactly the issue's test list: schema creation, the change_log
triggers rejecting delete/update, a revision conflict on the generic write
path, one change_log row per write across every mutable table, and a
static guard that nothing in the module updates change_log except
`synced_at`.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sqlite3

import pytest

from tc_ai_bridge.workbench_repository import (
    MUTABLE_TABLES,
    WorkbenchConflict,
    WorkbenchRepository,
    WorkbenchValidationError,
    natural_row_id,
)
from tc_ai_bridge.workspace_repository import WorkspaceRepository
from tests.support.workbench import _build_minimal_project, _write


def test_schema_creates_every_table_from_team_architecture(tmp_path):
    repo = WorkbenchRepository(tmp_path / "bridge-workbench.sqlite3")
    with repo._connect() as conn:
        names = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    for table in MUTABLE_TABLES:
        assert table in names, table
    assert "change_log" in names
    assert repo.schema_version() == 6


def test_write_inserts_row_and_appends_one_change_log_entry(tmp_path):
    repo = WorkbenchRepository(tmp_path / "w.sqlite3")
    result = _write(repo, "check_cache", "c1", payload={"hit": False})
    assert result["revision"] == 1

    row = repo.get("check_cache", "c1")
    assert row is not None
    assert json.loads(row["payload_json"]) == {"hit": False}

    entries = repo.change_log_entries("proj-1")
    assert len(entries) == 1
    assert entries[0]["table_name"] == "check_cache"
    assert entries[0]["row_key"] == "c1"
    assert entries[0]["op"] == "upsert"
    assert entries[0]["base_revision"] is None
    assert entries[0]["new_revision"] == 1
    assert entries[0]["synced_at"] is None


def test_write_updates_row_and_appends_a_second_change_log_entry(tmp_path):
    repo = WorkbenchRepository(tmp_path / "w.sqlite3")
    _write(repo, "check_cache", "c1", payload={"hit": False})
    result = _write(repo, "check_cache", "c1", payload={"hit": True}, expected_revision=1)
    assert result["revision"] == 2

    row = repo.get("check_cache", "c1")
    assert json.loads(row["payload_json"]) == {"hit": True}
    assert row["revision"] == 2

    entries = repo.change_log_entries("proj-1")
    assert len(entries) == 2
    assert entries[1]["base_revision"] == 1
    assert entries[1]["new_revision"] == 2


def test_write_rejects_a_stale_expected_revision(tmp_path):
    repo = WorkbenchRepository(tmp_path / "w.sqlite3")
    _write(repo, "check_cache", "c1")
    _write(repo, "check_cache", "c1", expected_revision=1)  # now at revision 2
    with pytest.raises(WorkbenchConflict):
        _write(repo, "check_cache", "c1", expected_revision=1)  # stale: real revision is 2


def test_write_last_writer_wins_when_no_revision_expected(tmp_path):
    repo = WorkbenchRepository(tmp_path / "w.sqlite3")
    _write(repo, "check_cache", "c1")
    # expected_revision=None: no conflict even though the real revision is 1.
    result = _write(repo, "check_cache", "c1", payload={"hit": True})
    assert result["revision"] == 2


def test_write_rejects_unknown_table(tmp_path):
    repo = WorkbenchRepository(tmp_path / "w.sqlite3")
    with pytest.raises(WorkbenchValidationError):
        _write(repo, "not_a_real_table", "row-1")


@pytest.mark.parametrize("table", MUTABLE_TABLES)
def test_every_mutable_table_writes_exactly_one_change_log_row(tmp_path, table):
    repo = WorkbenchRepository(tmp_path / "w.sqlite3")
    _write(repo, table, "row-1")
    entries = repo.change_log_entries("proj-1")
    assert len(entries) == 1
    assert entries[0]["table_name"] == table
    assert entries[0]["row_key"] == "row-1"


def test_change_log_rejects_delete(tmp_path):
    repo = WorkbenchRepository(tmp_path / "w.sqlite3")
    _write(repo, "check_cache", "c1")
    with pytest.raises(sqlite3.IntegrityError):
        with repo._connect() as conn:
            conn.execute("DELETE FROM change_log")
            conn.commit()


def test_change_log_rejects_update_of_anything_but_synced_at(tmp_path):
    repo = WorkbenchRepository(tmp_path / "w.sqlite3")
    _write(repo, "check_cache", "c1")
    with pytest.raises(sqlite3.IntegrityError):
        with repo._connect() as conn:
            conn.execute("UPDATE change_log SET payload_json='{}'")
            conn.commit()


def test_change_log_allows_setting_synced_at(tmp_path):
    repo = WorkbenchRepository(tmp_path / "w.sqlite3")
    _write(repo, "check_cache", "c1")
    with repo._connect() as conn:
        conn.execute("UPDATE change_log SET synced_at=?", ("2026-09-11T00:00:00Z",))
        conn.commit()
    entries = repo.change_log_entries("proj-1")
    assert entries[0]["synced_at"] == "2026-09-11T00:00:00Z"


def test_no_source_site_updates_change_log_except_synced_at():
    """Static guard: `_write()` only ever INSERTs into change_log. If future
    code (#76+) adds a raw UPDATE against it, this fails unless the SET
    clause is synced_at-only, matching the trigger's own contract."""
    package_dir = Path(__file__).resolve().parents[2] / "tc_ai_bridge"
    pattern = re.compile(r"UPDATE\s+change_log\s+SET\s+([^\"']+)", re.IGNORECASE)
    offenders = []
    for path in package_dir.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            clause = match.group(1).strip()
            if not clause.lower().startswith("synced_at"):
                offenders.append(f"{path.name}: {match.group(0)!r}")
    assert offenders == []


def test_workspace_repository_device_id_is_stable_across_reopen(tmp_path):
    path = tmp_path / "workspace.sqlite3"
    first = WorkspaceRepository(path).get_or_create_device_id()
    second = WorkspaceRepository(path).get_or_create_device_id()
    assert first == second
    assert first  # non-empty


def test_a_pre_cutover_project_refuses_to_open_rather_than_looking_empty(tmp_path):
    """#76: the readers now query a database those records were never in.

    Opening such a project would succeed and show an empty review queue and no
    decision history, which looks exactly like data loss. A refusal naming the
    directories is worse for nobody and much easier to act on.
    """
    from tc_ai_bridge.tc_project import ProjectError, TranslationCoreProject

    root = _build_minimal_project(tmp_path / "rut")
    legacy = root / ".apps" / "translationCoreAI" / "qaDecisions" / "rut" / "1" / "1"
    legacy.mkdir(parents=True)
    (legacy / "finding-1.json").write_text(
        json.dumps({"issueKey": "finding-1", "decision": "accepted"}), encoding="utf-8",
    )

    with pytest.raises(ProjectError) as raised:
        TranslationCoreProject(root)

    message = str(raised.value)
    assert "qaDecisions" in message, "the message must name what it found"
    assert "Re-import" in message
    assert "Nothing has been deleted" in message
    assert legacy.is_dir(), "the guard must not touch the files it refuses over"


def test_an_empty_legacy_directory_does_not_block_opening(tmp_path):
    """Directories get created by things other than records being written.

    The guard keys on actual JSON, not on a directory existing, so a stray
    empty folder cannot make a healthy project unopenable.
    """
    from tc_ai_bridge.tc_project import TranslationCoreProject

    root = _build_minimal_project(tmp_path / "rut")
    (root / ".apps" / "translationCoreAI" / "decisions" / "rut").mkdir(parents=True)

    assert TranslationCoreProject(root).book_id == "rut"


def test_an_audit_directory_alone_does_not_block_opening(tmp_path):
    """`audit/` is not written any more, but its presence still says nothing
    about which build created the project: it was a derived shadow of records
    held elsewhere, never a store of its own, and projects written before it
    was removed still carry the tails."""
    from tc_ai_bridge.tc_project import TranslationCoreProject

    root = _build_minimal_project(tmp_path / "rut")
    audit = root / ".apps" / "translationCoreAI" / "audit" / "rut" / "1" / "1"
    audit.mkdir(parents=True)
    (audit / "x_scripture-edit.json").write_text("{}", encoding="utf-8")

    assert TranslationCoreProject(root).book_id == "rut"


def test_translation_core_project_creates_an_empty_workbench_db(tmp_path):
    from tc_ai_bridge.tc_project import TranslationCoreProject

    root = _build_minimal_project(tmp_path / "rut")
    project = TranslationCoreProject(root)

    workbench_path = root / ".apps" / "translationCoreAI" / "bridge-workbench.sqlite3"
    assert workbench_path.is_file()
    assert project.workbench.schema_version() == 6
    # Nothing has written into it yet -- an empty DB is the only change (#75).
    assert repo_is_empty(project.workbench)


def repo_is_empty(repo) -> bool:
    with repo._connect() as conn:
        for table in MUTABLE_TABLES:
            if conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] != 0:
                return False
        return conn.execute("SELECT COUNT(*) FROM change_log").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Stable row ids and local identity. Both were written alongside the lazy
# migration runner and outlived it: the runner went when the decision was taken
# to reset all development data rather than migrate it, but deriving a row id
# from a natural key and stamping a stable `user_id` are properties of every
# workbench write, not of migration.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# The query API (#76). The file stores were directory trees, so every read was
# "give me what is under this prefix" -- per verse, per chapter, per book. These
# cover exactly that and nothing wider.
# ---------------------------------------------------------------------------

def _decision(repo, row_id, *, chapter, verse, key, payload, book_id="rut"):
    return _write(
        repo, "human_decisions", row_id, book_id=book_id, payload=payload,
        extra_columns={"kind": "check", "chapter": chapter, "verse": verse,
                       "key": key, "decision": "accepted"},
    )


def test_rows_filters_to_one_verse(tmp_path):
    repo = WorkbenchRepository(tmp_path / "bridge-workbench.sqlite3")
    _decision(repo, "a", chapter="1", verse="1", key="k1", payload={"n": 1})
    _decision(repo, "b", chapter="1", verse="2", key="k2", payload={"n": 2})

    found = repo.rows("human_decisions", project_id="proj-1", book_id="rut",
                      equals={"chapter": "1", "verse": "1"})

    assert [row["id"] for row in found] == ["a"]


def test_rows_scopes_by_book(tmp_path):
    """Bridge imports one project per book; a Ruth read must not see Titus."""
    repo = WorkbenchRepository(tmp_path / "bridge-workbench.sqlite3")
    _decision(repo, "a", chapter="1", verse="1", key="k", payload={}, book_id="rut")
    _decision(repo, "b", chapter="1", verse="1", key="k", payload={}, book_id="tit")

    found = repo.rows("human_decisions", project_id="proj-1", book_id="rut")

    assert [row["id"] for row in found] == ["a"]


def test_rows_without_a_book_spans_books_rather_than_matching_null(tmp_path):
    repo = WorkbenchRepository(tmp_path / "bridge-workbench.sqlite3")
    _decision(repo, "a", chapter="1", verse="1", key="k", payload={}, book_id="rut")
    _decision(repo, "b", chapter="1", verse="1", key="k", payload={}, book_id="tit")

    found = repo.rows("human_decisions", project_id="proj-1")

    assert {row["id"] for row in found} == {"a", "b"}


def test_rows_never_leak_across_projects(tmp_path):
    repo = WorkbenchRepository(tmp_path / "bridge-workbench.sqlite3")
    _decision(repo, "a", chapter="1", verse="1", key="k", payload={})
    _write(repo, "human_decisions", "b", project_id="other-project",
           payload={}, extra_columns={"kind": "check", "chapter": "1",
                                      "verse": "1", "key": "k", "decision": "x"})

    found = repo.rows("human_decisions", project_id="proj-1")

    assert [row["id"] for row in found] == ["a"]


def test_payloads_returns_what_the_file_reader_used_to_parse(tmp_path):
    repo = WorkbenchRepository(tmp_path / "bridge-workbench.sqlite3")
    original = {"bookId": "rut", "chapter": "1", "verse": "1", "decision": "accepted",
                "selectionText": ["a", "b"], "schemaVersion": 1}
    _decision(repo, "a", chapter="1", verse="1", key="k", payload=original)

    assert repo.payloads("human_decisions", project_id="proj-1", book_id="rut") == [original]


def test_payloads_skips_an_undecodable_row_rather_than_raising(tmp_path):
    """A corrupt file used to read as absent; one bad row must not take out a verse."""
    repo = WorkbenchRepository(tmp_path / "bridge-workbench.sqlite3")
    _decision(repo, "good", chapter="1", verse="1", key="k1", payload={"n": 1})
    _decision(repo, "bad", chapter="1", verse="1", key="k2", payload={"n": 2})
    with repo._connect() as conn:
        conn.execute("UPDATE human_decisions SET payload_json='{not json' WHERE id='bad'")
        conn.commit()

    assert repo.payloads("human_decisions", project_id="proj-1", book_id="rut") == [{"n": 1}]


def test_a_query_column_must_be_a_plain_identifier(tmp_path):
    repo = WorkbenchRepository(tmp_path / "bridge-workbench.sqlite3")
    with pytest.raises(WorkbenchValidationError):
        repo.rows("human_decisions", project_id="p", equals={"chapter=1 OR 1": "x"})
    with pytest.raises(WorkbenchValidationError):
        repo.rows("human_decisions", project_id="p", order_by="created_at; DROP TABLE x")


def test_a_query_table_must_be_workbench_mutable(tmp_path):
    repo = WorkbenchRepository(tmp_path / "bridge-workbench.sqlite3")
    with pytest.raises(WorkbenchValidationError):
        repo.rows("change_log", project_id="p")


def test_natural_row_id_is_stable_for_the_same_key():
    assert natural_row_id("proj", "php", "1", "4") == natural_row_id("proj", "php", "1", "4")


def test_natural_row_id_does_not_collide_across_field_boundaries():
    """Length-prefixing is what stops ("a","bc") and ("ab","c") colliding."""
    assert natural_row_id("a", "bc") != natural_row_id("ab", "c")


def test_natural_row_id_separates_none_from_the_string_none():
    assert natural_row_id("a", None) != natural_row_id("a", "None")
    assert natural_row_id("a", "") != natural_row_id("a", None)


def test_the_local_user_id_is_stable_across_calls(tmp_path):
    workspace = tmp_path / "workspace.sqlite3"
    first = WorkspaceRepository(workspace).get_or_create_local_user("Revant")
    second = WorkspaceRepository(workspace).get_or_create_local_user("Revant")

    assert first["userId"] == second["userId"]


def test_renaming_yourself_keeps_the_same_user_id(tmp_path):
    """Why the id exists at all: change_log rows can never be edited.

    If `actor_id` were the display name, renaming in Settings would split one
    person's history into two sets of immutable rows that can never be
    re-linked.
    """
    workspace = tmp_path / "workspace.sqlite3"
    before = WorkspaceRepository(workspace).get_or_create_local_user("Revant")
    after = WorkspaceRepository(workspace).get_or_create_local_user("R. Idikulay")

    assert after["userId"] == before["userId"]
    assert after["displayName"] == "R. Idikulay"


def test_a_blank_display_name_falls_back_rather_than_writing_an_empty_actor(tmp_path):
    user = WorkspaceRepository(tmp_path / "workspace.sqlite3").get_or_create_local_user("   ")
    assert user["displayName"] == "Unnamed Reviewer"
    assert user["userId"]


def test_device_id_and_user_id_are_independent(tmp_path):
    repo = WorkspaceRepository(tmp_path / "workspace.sqlite3")
    assert repo.get_or_create_device_id() != repo.get_or_create_local_user("Revant")["userId"]


# -- #77: batched writes, deletes, seq receipts ------------------------------


def test_a_batch_commits_every_write_or_none_of_them(tmp_path):
    repo = WorkbenchRepository(tmp_path / "w.sqlite3")
    with pytest.raises(WorkbenchValidationError):
        with repo.batch() as batch:
            batch.write("check_cache", "c1", project_id="proj-1", book_id="rut",
                        payload={"n": 1}, actor_id="u", device_id="d")
            batch.write("not_a_table", "c2", project_id="proj-1", book_id="rut",
                        payload={"n": 2}, actor_id="u", device_id="d")
    assert repo.get("check_cache", "c1") is None
    assert repo.change_log_entries("proj-1") == []

    with repo.batch() as batch:
        first = batch.write("check_cache", "c1", project_id="proj-1", book_id="rut",
                            payload={"n": 1}, actor_id="u", device_id="d")
        second = batch.write("check_cache", "c2", project_id="proj-1", book_id="rut",
                             payload={"n": 2}, actor_id="u", device_id="d")
        assert batch.get("check_cache", "c1") is not None, "a batch sees its own writes"
    assert [e["row_key"] for e in repo.change_log_entries("proj-1")] == ["c1", "c2"]
    assert second["seq"] == first["seq"] + 1


def test_delete_removes_the_row_and_logs_its_last_image(tmp_path):
    repo = WorkbenchRepository(tmp_path / "w.sqlite3")
    _write(repo, "check_cache", "c1", payload={"n": 1})
    receipt = repo._delete("check_cache", "c1", project_id="proj-1", book_id="rut",
                           actor_id="u", device_id="d")
    assert receipt["revision"] is None and receipt["seq"] == 2
    assert repo.get("check_cache", "c1") is None
    event = repo.change_log_entries("proj-1")[-1]
    assert (event["op"], event["base_revision"], event["new_revision"]) == ("delete", 1, None)
    assert json.loads(event["payload_json"]) == {"n": 1}
    assert repo._delete("check_cache", "c1", project_id="proj-1", book_id="rut",
                        actor_id="u", device_id="d") is None


def test_delete_honours_an_expected_revision(tmp_path):
    repo = WorkbenchRepository(tmp_path / "w.sqlite3")
    _write(repo, "check_cache", "c1")
    _write(repo, "check_cache", "c1")
    with pytest.raises(WorkbenchConflict):
        repo._delete("check_cache", "c1", project_id="proj-1", book_id="rut",
                     actor_id="u", device_id="d", expected_revision=1)


def test_write_receipts_carry_the_change_log_seq_and_max_seq_reads_it_back(tmp_path):
    repo = WorkbenchRepository(tmp_path / "w.sqlite3")
    first = _write(repo, "check_cache", "c1")
    second = _write(repo, "progress_totals", "t1")
    assert (first["seq"], second["seq"]) == (1, 2)
    assert repo.max_seq(project_id="proj-1") == 2
    assert repo.max_seq(project_id="proj-1", table="check_cache") == 1
    assert repo.max_seq(project_id="proj-1", table="progress_totals") == 2
    assert repo.max_seq(project_id="nobody") == 0


@pytest.mark.parametrize("relative", [
    ".apps/translationCoreAI/checkFindings/rut/1.json",
    ".apps/translationCoreAI/triage/rut.json",
    ".apps/translationCoreAI/checkCache.json",
])
def test_a_project_carrying_a_77_derived_store_file_refuses_to_open(tmp_path, relative):
    """#77 moved the derived stores too. They are rebuildable, but a project
    that still has them was checked on a build whose results this one would
    show as 'not checked' -- the guard exists for exactly that silence."""
    from tc_ai_bridge.tc_project import ProjectError, TranslationCoreProject

    root = _build_minimal_project(tmp_path / "rut")
    target = root / Path(relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{}", encoding="utf-8")
    with pytest.raises(ProjectError) as raised:
        TranslationCoreProject(root)
    assert Path(relative).parts[2] in str(raised.value)
    assert target.is_file()
