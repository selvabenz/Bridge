"""#77: metrics and the backups index move into the workbench.

Metrics: one append-only event row per event, one counters row per book,
the summary shape unchanged. Backups: the files stay on disk (crash
recovery must not depend on a database opening); `file_backups` only indexes
them so a reader does not walk `backups/`.
"""
from __future__ import annotations

import json

import pytest

from tc_ai_bridge.metrics import MetricsStore
from tc_ai_bridge.tc_project import ProjectError, TranslationCoreProject
from tests.support.workbench import _build_minimal_project


@pytest.fixture
def project(tmp_path):
    root = _build_minimal_project(tmp_path / "rut")
    (root / "rut" / "1.json").write_text(json.dumps({"1": "verse one"}), encoding="utf-8")
    return TranslationCoreProject(root)


def test_metrics_events_accumulate_counters_and_tokens(project, monkeypatch):
    monkeypatch.delenv("TC_AI_BRIDGE_TEST_MODE", raising=False)
    store = MetricsStore(project)
    assert store.load()["events"] == []

    store.event("human_accept")
    store.event("human_reject", input_tokens=10, output_tokens=5, total_tokens=15, estimated_cost_usd=0.5)
    store.event("qa_decision", decision="accepted")

    data = MetricsStore(project).load()
    assert data["counters"] == {"human_accept": 1, "human_reject": 1, "qa_decision": 1}
    assert data["tokens"] == {"input": 10, "output": 5, "total": 15}
    assert data["estimatedCostUSD"] == 0.5
    assert [e["name"] for e in data["events"]] == ["human_accept", "human_reject", "qa_decision"]

    summary = MetricsStore(project).summary()
    assert summary["humanDecisionCount"] == 2
    assert summary["acceptanceRate"] == 0.5
    assert summary["confirmedQAFindingRate"] == 1.0


def test_metrics_event_is_one_commit_and_events_are_never_capped(project, monkeypatch):
    monkeypatch.delenv("TC_AI_BRIDGE_TEST_MODE", raising=False)
    store = MetricsStore(project)
    before = len(project.workbench.change_log_entries(project.workbench_identity.project_id))
    store.event("cache_skip", skipped=1)
    entries = project.workbench.change_log_entries(project.workbench_identity.project_id)[before:]
    assert [e["table_name"] for e in entries] == ["metrics_events", "metrics_counters"]
    assert entries[0]["created_at"] == entries[1]["created_at"], "one transaction, one timestamp"


def test_metrics_respect_the_test_mode_gate(project, monkeypatch):
    monkeypatch.setenv("TC_AI_BRIDGE_TEST_MODE", "1")
    MetricsStore(project).event("human_accept")
    assert MetricsStore(project).load()["events"] == []
    MetricsStore(project).event("human_accept", _force_test_write=True)
    assert len(MetricsStore(project).load()["events"]) == 1


def test_report_service_reads_metrics_through_the_project(project, monkeypatch):
    monkeypatch.delenv("TC_AI_BRIDGE_TEST_MODE", raising=False)
    MetricsStore(project).event("human_edit")
    from tc_ai_bridge.reporting import ReportService
    assert ReportService(project)._metrics()["counters"] == {"human_edit": 1}


def test_chapter_backups_are_indexed_and_listed_newest_first(project):
    first = project.backup_chapter("1")
    second = project.backup_chapter("1")
    assert first.is_file() and second.is_file()

    listed = project.list_alignment_backups("1")
    assert listed == sorted([first, second], key=lambda p: p.parts[-4], reverse=True)
    assert project.list_alignment_backups("2") == []

    rows = project.workbench.payloads(
        "file_backups", project_id=project.workbench_identity.project_id, book_id="rut",
    )
    assert {r["label"] for r in rows} == {"alignmentData"}
    assert {r["path"] for r in rows} == {str(first.resolve()), str(second.resolve())}


def test_an_indexed_backup_that_is_gone_is_not_listed(project):
    kept = project.backup_chapter("1")
    gone = project.backup_chapter("1")
    gone.unlink()
    assert project.list_alignment_backups("1") == [kept]


def test_transaction_backups_are_indexed_under_their_label(project):
    chapter = project.chapter_path("1")
    root = project._backup_paths([chapter, project.path / "does-not-exist.json"], "scriptureEdit")
    rows = project.workbench.payloads(
        "file_backups", project_id=project.workbench_identity.project_id, book_id="rut",
    )
    assert len(rows) == 1, "only files that existed were copied, so only those are indexed"
    assert rows[0]["label"] == "scriptureEdit"
    assert rows[0]["path"].startswith(str(root.resolve()))


def test_a_project_carrying_a_metrics_file_refuses_to_open(tmp_path):
    root = _build_minimal_project(tmp_path / "rut")
    metrics = root / ".apps" / "translationCoreAI" / "metrics"
    metrics.mkdir(parents=True)
    (metrics / "rut.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ProjectError) as raised:
        TranslationCoreProject(root)
    assert "metrics" in str(raised.value)
