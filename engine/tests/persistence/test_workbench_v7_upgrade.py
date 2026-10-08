"""Workbench v6 -> v7 (2026-10-08): the regression that showed up in the desktop app.

indic-qa-editor took workbench v6 for its Language QA batch tables, and real
project databases were migrated to it. This branch first called its own
alignment tables "v6" too, so a project already at the other v6 skipped them and
the alignment page failed with "no such table: alignment_null_decisions". The
fix carries indic-qa-editor's v6 block verbatim and makes the alignment tables
v7. These tests pin the scenario that broke and the one that would break next.
"""
import sqlite3

import pytest

from tc_ai_bridge import workbench_repository as wr


def _build_at(path, version, monkeypatch):
    """A database built by a ladder that stops at `version`, the way an older
    build would have left it."""
    with monkeypatch.context() as m:
        m.setattr(wr, "WORKBENCH_SCHEMA_VERSION", version)
        m.setattr(wr, "_MIGRATIONS", tuple(item for item in wr._MIGRATIONS if item[0] <= version))
        wr.WorkbenchRepository(path)


def _tables(path):
    conn = sqlite3.connect(path)
    try:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


def test_a_database_at_indic_qa_v6_gains_the_alignment_tables(tmp_path, monkeypatch):
    path = tmp_path / "bridge-workbench.sqlite3"
    _build_at(path, 6, monkeypatch)
    before = _tables(path)
    assert {"language_qa_batches", "language_qa_learned_fixes", "language_qa_flags"} <= before
    assert "alignment_null_decisions" not in before

    repo = wr.WorkbenchRepository(path)
    assert repo.schema_version() == 7
    after = _tables(path)
    assert {"alignment_null_decisions", "alignment_verdicts"} <= after
    # The other branch's tables are untouched.
    assert {"language_qa_batches", "language_qa_learned_fixes", "language_qa_flags"} <= after


def test_a_v5_database_climbs_both_rungs(tmp_path, monkeypatch):
    path = tmp_path / "bridge-workbench.sqlite3"
    _build_at(path, 5, monkeypatch)
    repo = wr.WorkbenchRepository(path)
    assert repo.schema_version() == 7
    assert {"language_qa_batches", "alignment_null_decisions", "alignment_verdicts"} <= _tables(path)


def test_v6_is_the_language_qa_block_and_v7_the_alignment_block():
    by_version = dict(wr._MIGRATIONS)
    assert "CREATE TABLE language_qa_batches" in by_version[6]
    assert "alignment_null_decisions" not in by_version[6]
    assert "CREATE TABLE alignment_null_decisions" in by_version[7]
    assert "CREATE TABLE alignment_verdicts" in by_version[7]


@pytest.mark.parametrize("table", ["alignment_null_decisions", "alignment_verdicts", "language_qa_batches"])
def test_every_new_table_is_writable_through_the_generic_path(tmp_path, table):
    repo = wr.WorkbenchRepository(tmp_path / "w.sqlite3")
    repo._write(
        table, "r1", project_id="p", book_id="gen", payload={"v": 1},
        actor_id="human", device_id="d", expected_revision=None,
    )
    assert repo.get(table, "r1") is not None
