"""Builders for the workbench-repository tests: one generic row write with each
table's required extra columns, and a minimal Ruth project on disk.

Taken verbatim from tests/persistence/test_workbench_repository.py, which eight
modules imported them from (CLAUDE.md rule 2, #74)."""
from __future__ import annotations

import json
from pathlib import Path


# Extra columns each table requires beyond the nine common ones, per
# TEAM_ARCHITECTURE.md ss3.1/3.2 -- only the NOT NULL ones need a value here.
_REQUIRED_EXTRA_COLUMNS = {
    "human_decisions": {"kind": "qa", "key": "k1"},
    "ai_review_results": {"chapter": "1", "verse": "1", "input_fingerprint": "fp", "generated_at": "now"},
    "semantic_mappings": {"fingerprint": "fp"},
    "semantic_validation_runs": {"suite_id": "s1"},
    "project_state": {"key": "k1"},
    "progress_chapters": {"chapter": "1"},
    "check_findings": {"chapter": "1"},
}


def _write(repo, table, row_id, *, project_id="proj-1", book_id="rut",
           payload=None, actor_id="human", device_id="dev-1",
           expected_revision=None, **kwargs):
    extra = dict(_REQUIRED_EXTRA_COLUMNS.get(table, {}))
    extra.update(kwargs.pop("extra_columns", {}) or {})
    return repo._write(
        table, row_id, project_id=project_id, book_id=book_id,
        payload=payload or {"note": "x"}, actor_id=actor_id, device_id=device_id,
        expected_revision=expected_revision, extra_columns=extra, **kwargs,
    )


def _build_minimal_project(root: Path) -> Path:
    align_dir = root / ".apps" / "translationCore" / "alignmentData" / "rut"
    align_dir.mkdir(parents=True)
    (root / "rut").mkdir(parents=True)
    (root / "manifest.json").write_text(json.dumps({
        "project": {"id": "rut", "name": "Ruth"},
        "target_language": {"id": "tam", "name": "Tamil"},
        "tc_version": "8", "tc_edit_version": "3.7.0",
    }), encoding="utf-8")
    (align_dir / "1.json").write_text(
        json.dumps({"1": {"alignments": [], "wordBank": []}}), encoding="utf-8",
    )
    return root
