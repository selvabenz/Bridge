"""#241: WorkbenchRepository.write_generation counts committed row writes per
database file, so a reader that kept rows (a check job's gap prefetch) knows
when its copy may be stale. Reads and a rolled-back batch do not count."""
from __future__ import annotations

import pytest

from tc_ai_bridge.workbench_repository import WorkbenchRepository

TABLE = "alignment_verdicts"


def _write(repo: WorkbenchRepository, key: str, verse: str = "1") -> None:
    repo._write(TABLE, key, project_id="p", book_id="php", payload={"verdict": "ALIGNED_CLEAN"},
                actor_id="a", device_id="d", expected_revision=None, extra_columns={"chapter": "1", "verse": verse})


def test_each_committed_write_counts_and_reads_do_not(tmp_path):
    repo = WorkbenchRepository(tmp_path / "w.sqlite3")
    start = repo.write_generation
    _write(repo, "row-1")
    assert repo.write_generation == start + 1
    repo.rows(TABLE, project_id="p", book_id="php")
    assert repo.get(TABLE, "row-1") is not None
    assert repo.write_generation == start + 1
    repo._delete(TABLE, "row-1", project_id="p", book_id="php", actor_id="a", device_id="d")
    assert repo.write_generation == start + 2


def test_a_committed_batch_counts_and_a_rolled_back_one_does_not(tmp_path):
    repo = WorkbenchRepository(tmp_path / "w.sqlite3")
    start = repo.write_generation
    with repo.batch() as batch:
        batch.write(TABLE, "b-1", project_id="p", book_id="php", payload={}, actor_id="a", device_id="d",
                    extra_columns={"chapter": "1", "verse": "1"})
    assert repo.write_generation == start + 1
    with pytest.raises(RuntimeError):
        with repo.batch() as batch:
            batch.write(TABLE, "b-2", project_id="p", book_id="php", payload={}, actor_id="a", device_id="d",
                        extra_columns={"chapter": "1", "verse": "2"})
            raise RuntimeError("abandon")
    assert repo.write_generation == start + 1


def test_two_repositories_on_one_file_share_the_count(tmp_path):
    first = WorkbenchRepository(tmp_path / "w.sqlite3")
    second = WorkbenchRepository(tmp_path / "w.sqlite3")
    before = second.write_generation
    _write(first, "row-1")
    assert second.write_generation == before + 1
    other = WorkbenchRepository(tmp_path / "other.sqlite3")
    assert other.write_generation == 0
