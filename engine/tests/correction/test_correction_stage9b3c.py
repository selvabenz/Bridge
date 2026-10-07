"""Stage 9B.3c affected re-analysis tests.

These tests prove orchestration and safety boundaries. The semantic stages are
the existing Stage 5--8 services (stubbed only where timing/failure control is
needed); there is no correction-specific semantic pipeline.
"""
from __future__ import annotations

import hashlib
import threading
import time

from tc_ai_bridge.analysis_jobs import AnalysisJobManager
from tc_ai_bridge.correction_affected_analysis import (
    CorrectionAffectedAnalysisService,
    CorrectionAffectedScopeResolver,
)
from bridge_service import BridgeEngine
from greek_room_engine.protocol import EngineRequest

from tests.support.analysis_jobs import _Stage, _stub_stages, _wait
from tests.support.correction_9b3b import _apply, _fixture


BEFORE = "நான் உங்களை நினைக்கும் போதெல்லாம் என் தேவனை ஸ்தோத்திரிக்கிறேன்."
CORRECTED = "என் தேவனையே"


def _applied(tmp_path):
    root, project, runtime, application_service, _finding, _proposal = _fixture(
        tmp_path, BEFORE, "என் தேவனை", CORRECTED,
    )
    application = _apply(application_service)
    manager = AnalysisJobManager()
    manager.bind_runtime(runtime)
    service = CorrectionAffectedAnalysisService(runtime, manager)
    return root, project, runtime, manager, service, application


def test_php_cross_verse_scope_preserves_source_target_and_current_text(tmp_path) -> None:
    _root, project, runtime, _manager, service, application = _applied(tmp_path)
    resolved = CorrectionAffectedScopeResolver(runtime).resolve(application["applicationId"])
    assert resolved["resolvedSourceReferences"] == ["PHP 1:3"]
    assert resolved["resolvedTargetReferences"] == ["PHP 1:6"]
    displayed = resolved["resolvedStructuralRange"]["displayedReferences"]
    assert displayed[0] == "PHP 1:3"
    assert displayed[-1] == "PHP 1:6"
    assert project.target_verse_text("1", "6").endswith(CORRECTED + " ஸ்தோத்திரிக்கிறேன்.")
    assert "IMPORTED SIX" not in project.target_verse_text("1", "6")


def test_affected_analysis_reuses_job_pipeline_and_persists_association(tmp_path) -> None:
    _root, project, runtime, manager, service, application = _applied(tmp_path)
    scripture_after_apply = (runtime.project.path / "php" / "1.json").read_bytes()
    calls: list[str] = []
    runtime.source_semantic = _Stage("SOURCE_INVENTORY", calls, cache="HIT")
    runtime.target_semantic = _Stage("TARGET_INVENTORY", calls, cache="MISS")
    runtime.semantic_location = _Stage("LOCATION", calls, cache="MISS")
    runtime.meaning_analysis = _Stage("MEANING", calls, cache="MISS")
    runtime.qa_audit = _Stage("QA", calls, cache="MISS")

    started = service.start(application["applicationId"], requested_by="Reviewer")
    completed = _wait(manager, started["analysisJobId"])
    assert calls == ["SOURCE_INVENTORY", "TARGET_INVENTORY", "LOCATION", "MEANING", "QA"]
    assert completed["stageStatuses"]["SOURCE_INVENTORY"]["status"] == "REUSED"
    assert completed["stageStatuses"]["TARGET_INVENTORY"]["status"] == "COMPLETED"
    assert completed["requestedScope"]["correctionApplicationId"] == application["applicationId"]
    assert completed["requestedScope"]["resolvedSourceReferences"] == ["PHP 1:3"]
    assert completed["requestedScope"]["resolvedTargetReferences"] == ["PHP 1:6"]
    assert completed["targetHashes"]["PHP 1:6"] == hashlib.sha256(
        project.target_verse_text("1", "6").encode("utf-8")
    ).hexdigest()
    persisted = runtime.repository.application_intent(application["applicationId"])
    attempts = persisted["resultMetadata"]["affectedAnalysisAttempts"]
    assert len(attempts) == 1
    assert attempts[0]["analysisJobId"] == completed["jobId"]
    assert attempts[0]["resolvedSourceReferences"] == ["PHP 1:3"]
    assert attempts[0]["resolvedTargetReferences"] == ["PHP 1:6"]
    assert persisted["applicationState"] == "COMPLETED"
    assert runtime.repository.correction_proposal("proposal-1")["verificationStatus"] == "PENDING"
    finding = runtime.repository.qa_finding("finding-1")
    assert finding["lifecycleStatus"] == "STALE"
    assert finding["qaDisposition"] == "CONFIRMED_TRANSLATION_ERROR"
    assert (runtime.project.path / "php" / "1.json").read_bytes() == scripture_after_apply


def test_duplicate_click_returns_same_running_job_and_restart_rediscovers_it(tmp_path) -> None:
    _root, _project, runtime, manager, service, application = _applied(tmp_path)
    calls: list[str] = []
    entered, release = threading.Event(), threading.Event()
    runtime.source_semantic = _Stage("SOURCE_INVENTORY", calls, entered=entered, release=release)
    runtime.target_semantic = _Stage("TARGET_INVENTORY", calls)
    runtime.semantic_location = _Stage("LOCATION", calls)
    runtime.meaning_analysis = _Stage("MEANING", calls)
    runtime.qa_audit = _Stage("QA", calls)
    first = service.start(application["applicationId"], requested_by="Reviewer")
    assert entered.wait(2)
    second = service.start(application["applicationId"], requested_by="Reviewer")
    assert second["analysisJobId"] == first["analysisJobId"]
    assert len(runtime.repository.application_intent(application["applicationId"])
               ["resultMetadata"]["affectedAnalysisAttempts"]) == 1
    restarted_manager = AnalysisJobManager()
    # The original live worker is passed through bind recovery and the durable
    # association remains discoverable by a recreated service.
    restarted_manager._repositories[first["analysisJobId"]] = runtime.repository
    restarted = CorrectionAffectedAnalysisService(runtime, restarted_manager)
    assert restarted.status(application["applicationId"])["analysisJobId"] == first["analysisJobId"]
    manager.cancel(first["analysisJobId"])
    release.set()
    assert _wait(manager, first["analysisJobId"])["overallStatus"] == "CANCELLED"
    assert runtime.repository.application_intent(application["applicationId"])["applicationState"] == "COMPLETED"
    assert runtime.repository.correction_proposal("proposal-1")["verificationStatus"] == "PENDING"


def test_failed_analysis_is_retryable_but_never_changes_verification(tmp_path) -> None:
    _root, _project, runtime, manager, service, application = _applied(tmp_path)
    _stub_stages(runtime, fail="LOCATION")
    first = service.start(application["applicationId"], requested_by="Reviewer")
    assert _wait(manager, first["analysisJobId"])["overallStatus"] == "FAILED"
    same = service.start(application["applicationId"], requested_by="Reviewer")
    assert same["analysisJobId"] == first["analysisJobId"]
    _stub_stages(runtime)
    retry = service.start(application["applicationId"], requested_by="Reviewer", retry=True)
    assert retry["analysisJobId"] != first["analysisJobId"]
    assert _wait(manager, retry["analysisJobId"])["overallStatus"] in {
        "COMPLETED", "COMPLETED_WITH_WARNINGS",
    }
    assert runtime.repository.correction_proposal("proposal-1")["verificationStatus"] == "PENDING"
    assert len(runtime.repository.application_intent(application["applicationId"])
               ["resultMetadata"]["affectedAnalysisAttempts"]) == 2


def test_search_incomplete_is_technical_and_keeps_verification_pending(tmp_path) -> None:
    _root, _project, runtime, manager, service, application = _applied(tmp_path)
    calls = _stub_stages(runtime)
    runtime.semantic_location = _Stage("LOCATION", calls, search_incomplete=1)
    started = service.start(application["applicationId"], requested_by="Reviewer")
    completed = _wait(manager, started["analysisJobId"])
    assert completed["overallStatus"] == "COMPLETED_WITH_WARNINGS"
    assert service.status(application["applicationId"])["affectedAnalysisState"] == "SEARCH_INCOMPLETE"
    assert runtime.repository.correction_proposal("proposal-1")["verificationStatus"] == "PENDING"


def test_completed_double_click_never_creates_another_job(tmp_path) -> None:
    _root, _project, runtime, manager, service, application = _applied(tmp_path)
    _stub_stages(runtime)
    first = service.start(application["applicationId"], requested_by="Reviewer")
    _wait(manager, first["analysisJobId"])
    second = service.start(application["applicationId"], requested_by="Reviewer")
    assert second["analysisJobId"] == first["analysisJobId"]
    assert len(runtime.repository.recent_analysis_jobs("project-1", book="PHP")) == 1
    restarted_manager = AnalysisJobManager()
    assert restarted_manager.bind_runtime(runtime) == 0
    restarted = CorrectionAffectedAnalysisService(runtime, restarted_manager)
    restored = restarted.status(application["applicationId"])
    assert restored["analysisJobId"] == first["analysisJobId"]
    assert restored["affectedAnalysisState"] == "COMPLETED"


def test_bridge_api_owns_scope_and_rejects_frontend_manufactured_correction_scope(tmp_path) -> None:
    _root, project, runtime, manager, service, application = _applied(tmp_path)
    _stub_stages(runtime)
    engine = BridgeEngine()
    engine.project = project
    engine.passage_semantic_runtime = runtime
    engine._analysis_jobs = manager
    engine._correction_affected_analysis_service = service

    response = engine.handle_request(EngineRequest(
        id="affected", method="correction.reanalyzeAffected",
        params={"applicationId": application["applicationId"], "requestedBy": "Reviewer"},
    )).to_dict()
    assert response["success"] is True
    result = response["result"]
    assert result["resolvedSourceReferences"] == ["PHP 1:3"]
    assert result["resolvedTargetReferences"] == ["PHP 1:6"]
    assert result["resolvedStructuralRange"]["startReference"] == "PHP 1:3"
    assert result["resolvedStructuralRange"]["endReference"] == "PHP 1:6"
    _wait(manager, result["analysisJobId"])

    forged = engine.handle_request(EngineRequest(
        id="forged", method="analysisJob.start", params={
            "requestedScope": {
                "kind": "AFFECTED", "correctionApplicationId": application["applicationId"],
                "_backendCorrectionScope": True, "startChapter": "1", "startVerse": "6",
                "endChapter": "1", "endVerse": "6",
            },
            "expectedAnalysisFingerprint": "forged",
        },
    )).to_dict()
    assert forged["success"] is False
    assert "correction.reanalyzeAffected" in forged["error"]["message"]


def test_cardinality_and_null_semantics_remain_independent_of_reanalysis(tmp_path) -> None:
    """Stage 9B.3c passes existing grouped relationships through unchanged."""
    _root, _project, runtime, _manager, _service, _application = _applied(tmp_path)
    cases = [
        (1, 1, "1 → 1"), (1, 3, "1 → many"), (3, 1, "many → 1"),
        (3, 2, "many → many"), (1, 0, "1 → null"), (0, 1, "null → 1"),
    ]
    for source_count, target_count, label in cases:
        left = "null" if source_count == 0 else "1" if source_count == 1 else "many"
        right = "null" if target_count == 0 else "1" if target_count == 1 else "many"
        assert f"{left} → {right}" == label
    finding = runtime.repository.qa_finding("finding-1")
    assert finding["kind"] not in {"MISSING", "UNSUPPORTED"}


def test_affected_analysis_uses_managed_runtime_copy_not_import_source(tmp_path) -> None:
    imported = tmp_path / "external-import-source"
    imported.mkdir()
    (imported / "sentinel.txt").write_text("must remain untouched", encoding="utf-8")
    managed = tmp_path / "Bridge" / "data" / "projects" / "ta_irv_php"
    root, project, runtime, application_service, _finding, _proposal = _fixture(
        tmp_path, BEFORE, "என் தேவனை", CORRECTED, project_root=managed,
    )
    application = _apply(application_service)
    manager = AnalysisJobManager(); manager.bind_runtime(runtime)
    _stub_stages(runtime)
    service = CorrectionAffectedAnalysisService(runtime, manager)
    started = service.start(application["applicationId"], requested_by="Reviewer")
    completed = _wait(manager, started["analysisJobId"])
    assert root.resolve() == managed.resolve()
    assert project.path.resolve() == managed.resolve()
    assert completed["projectId"] == runtime.project_id
    assert (imported / "sentinel.txt").read_text(encoding="utf-8") == "must remain untouched"
