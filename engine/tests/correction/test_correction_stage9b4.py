"""Stage 9B.4 positive semantic verification and explicit CORRECTED acknowledgement.

The fixtures here build a *real* applied correction (through the Stage 9B.3b
service and the canonical Scripture writer) and then publish controlled but
real-shaped Stage 6A/6B/7/8 records for the affected analysis. The semantic
content is controlled so PASSED/FAILED/UNCERTAIN are deterministic; everything
the verifier touches -- repository readers, run lifecycle rules, the Stage 7
comparator, the analysis-job ledger -- is the production code path.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from bridge_service import BridgeEngine
from greek_room_engine.protocol import EngineRequest
from tc_ai_bridge.analysis_jobs import AnalysisJobManager
from tc_ai_bridge.correction_affected_analysis import CorrectionAffectedAnalysisService
from tc_ai_bridge.correction_verification import (
    CorrectionVerificationService,
    ReasonCode,
    VERIFICATION_ENGINE_VERSION,
)
from tc_ai_bridge.passage_semantic_models import (
    AuditDirection,
    CorrectionApplicationState,
    CoverageDimension,
    LifecycleStatus,
    PolicyBinding,
    ReviewStatus,
    SemanticCoverageAccount,
)
from tc_ai_bridge import passage_semantic_repository as repository_module
from tc_ai_bridge.passage_semantic_repository import (
    DATABASE_SCHEMA_VERSION,
    RECORD_DEPENDENCY_TABLES,
    FoundationConflict,
    FoundationRepository,
    FoundationValidationError,
)
from tc_ai_bridge.secret_store import AppSettings

from tests.support.correction_9b3b import _apply, _fixture, _hash


BEFORE = "நான் உங்களை நினைக்கும் போதெல்லாம் என் தேவனை ஸ்தோத்திரிக்கிறேன்."
ORIGINAL_SPAN = "என் தேவனை"
SOURCE_UNIT = "source-unit-php-1-3"

# The source obligation lives in PHP 1:3; its realization is in PHP 1:6. That
# separation is the load-bearing property of every cross-verse assertion here.
SOURCE_REFERENCE = "PHP 1:3"
TARGET_REFERENCE = "PHP 1:6"


# --- Controlled current-evidence fixtures -----------------------------------

def _source_inventory(runtime: Any, *, source_text: str, kind: str = "LEXICAL",
                      dimension: str = "LEXICAL_CONTENT") -> str:
    """Publish a source inventory holding the one obligation under test."""
    lock = runtime.repository.source_lock(runtime.project_id, runtime.book) or {}
    inventory_id = "source-inventory-verification-fixture"
    unit = runtime.repository.semantic_unit(SOURCE_UNIT)
    unit = {
        **unit, "kind": kind, "coverageDimension": dimension,
        "rawSurface": source_text, "normalizedSurface": source_text,
        "displayedReferences": [SOURCE_REFERENCE],
        "canonicalReferences": [SOURCE_REFERENCE],
    }
    runtime.repository.save_source_inventory(
        inventory_id=inventory_id, project_id=runtime.project_id, book=runtime.book,
        range_key="PHP 1:3-1:6", fingerprint="source-fingerprint-verification",
        source_resource_id=str(lock.get("resource_id") or ""),
        source_resource_version=str(lock.get("resource_version") or ""),
        source_resource_hash=str(lock.get("resource_hash") or ""),
        audit_policy_version="audit-policy-v1", diagnostics={},
        payload={
            "id": inventory_id, "fingerprint": "source-fingerprint-verification",
            "units": [unit], "coverageAccounts": [],
            "sourceResource": {"resourceHash": str(lock.get("resource_hash") or "")},
        },
        token_rows=[], unit_ids=[], evidence_ids=[],
    )
    return inventory_id


def _target_inventory(
    runtime: Any, *, target_text: str, target_units: list[dict[str, Any]] | None = None,
) -> str:
    inventory_id = "target-inventory-verification-fixture"
    reference = TARGET_REFERENCE
    runtime.repository.save_target_inventory(
        inventory_id=inventory_id, project_id=runtime.project_id, book=runtime.book,
        range_key="PHP 1:3-1:6", fingerprint="target-fingerprint-verification",
        target_revision="target-revision-verification",
        target_content_hash=_hash(target_text), language_id="tam",
        tokenizer_version="tc-whitespace-v1", analyzer_registry_version="v1",
        structure_hash="structure", diagnostics={},
        payload={
            "id": inventory_id, "fingerprint": "target-fingerprint-verification",
            "targetRevision": "target-revision-verification",
            "targetContentHash": _hash(target_text),
            "units": list(target_units or ()),
            "capabilities": {"morphology": "AVAILABLE"},
            "targetTextByDisplayedReference": {reference: target_text},
        },
        token_ids=[], unit_ids=[], spans=[], neighborhoods=[], references=[reference],
    )
    return inventory_id


def _target_semantic_unit(runtime: Any, unit_id: str, reference: str,
                         start: int, end: int) -> dict[str, Any]:
    """Persist a real TARGET-side semantic unit the support account can own."""
    payload = {
        "id": unit_id, "side": "TARGET", "projectId": runtime.project_id,
        "book": runtime.book, "kind": "LEXICAL",
        "displayedReferences": [reference], "canonicalReferences": [reference],
        "tokenInstanceIds": [], "tokenLineageIds": [], "rawSurface": "",
        "normalizedSurface": "",
        "semanticFeatures": {"startCodePoint": str(start), "endCodePoint": str(end)},
        "unitConfidence": {
            "rawScore": None, "calibratedValue": 0.0,
            "confidencePolicyVersion": "confidence-v1", "calibrationVersion": "calibration-v1",
        },
        "provenance": "DETERMINISTIC_RULE", "evidenceIds": [], "resourceValidationIds": [],
        "auditEligibility": "ELIGIBLE", "semanticObligation": "CONTEXT_DEPENDENT",
        "accountingRole": "PRIMARY", "auditOwnerUnitId": unit_id,
        "coverageDimension": "LEXICAL_CONTENT", "semanticFingerprint": unit_id,
        "policyBinding": {
            "confidencePolicyVersion": "confidence-v1", "calibrationVersion": "calibration-v1",
            "auditPolicyVersion": "audit-policy-v1",
        },
        "reviewStatus": "UNREVIEWED", "lifecycleStatus": "ACTIVE", "revision": 1,
    }
    with runtime.repository._connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO semantic_units VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (unit_id, runtime.project_id, "TARGET", "LEXICAL", unit_id, "ELIGIBLE",
             "CONTEXT_DEPENDENT", "PRIMARY", "LEXICAL_CONTENT", unit_id,
             "UNREVIEWED", "ACTIVE", 1, json.dumps(payload, ensure_ascii=False)),
        )
        conn.commit()
    return payload


def _publish_evidence(
    runtime: Any, *, source_text: str, target_text: str,
    location_outcome: str = "LOCATED", realization: str = "LEXICALLY_REALIZED",
    component_status: str = "PRESERVED", component_dimension: str = "LEXICAL_CONTENT",
    component_evidence_kind: str = "LEXICAL_CONCEPT", resource_status: str = "NOT_CHECKED",
    coverage_status: str = "COVERED", meaning_status: str = "PRESERVED",
    properties: tuple[str, ...] = (), target_semantic_unit_ids: tuple[str, ...] = (),
    source_kind: str = "LEXICAL", source_dimension: str = "LEXICAL_CONTENT",
    findings: list[dict[str, Any]] | None = None,
    target_units: list[dict[str, Any]] | None = None,
    target_support: list[tuple[str, str]] = (),
    extra_accounts: list[tuple[str, str, str]] = (),
    verse_text: str | None = None,
) -> dict[str, str]:
    """Publish one controlled Stage 6A/6B/7/8 chain over the corrected verse."""
    repository = runtime.repository
    source_inventory_id = _source_inventory(
        runtime, source_text=source_text, kind=source_kind, dimension=source_dimension,
    )
    target_inventory_id = _target_inventory(
        runtime, target_text=verse_text if verse_text is not None else target_text,
        target_units=target_units,
    )

    candidate = {
        "id": "candidate-verification-1",
        "quotes": [{"quote": target_text, "displayedReference": TARGET_REFERENCE}],
    }
    relationship = {
        "id": "relationship-verification-1",
        "sourceSemanticUnitIds": [SOURCE_UNIT],
        "targetSemanticUnitIds": list(target_semantic_unit_ids),
        "targetSpanIds": ["target-span-verification-1"],
        "locationOutcome": location_outcome, "realization": realization,
        "locationConfidence": {"rawScore": 0.9, "calibratedValue": 0.9},
        "selectedCandidateId": candidate["id"],
        "properties": list(properties),
        # Cross-verse by construction: the source obligation is PHP 1:3 and the
        # realization is PHP 1:6. Verification must never collapse these.
        "displayedReferences": [TARGET_REFERENCE],
        "sourceDisplayedReferences": [SOURCE_REFERENCE],
        "reviewStatus": "AI_PROPOSED", "lifecycleStatus": "ACTIVE", "revision": 1,
    }
    location_run_id = "location-run-verification"
    repository.save_semantic_location_run(
        run_id=location_run_id, project_id=runtime.project_id, book=runtime.book,
        range_key="PHP 1:3-1:6", fingerprint="location-fingerprint-verification",
        source_inventory_id=source_inventory_id, target_inventory_id=target_inventory_id,
        run_status="COMPLETE",
        payload={
            "id": location_run_id, "rangeKey": "PHP 1:3-1:6",
            "fingerprint": "location-fingerprint-verification",
            "sourceInventoryId": source_inventory_id,
            "targetInventoryId": target_inventory_id,
            "relationships": [relationship], "candidates": [candidate], "diagnostics": {},
        },
        candidates=[], relationships=[],
        alignment_dependency_id=repository.alignment_dependency_id(runtime.project_id, runtime.book),
    )

    component = {
        "id": "meaning-component-verification-1",
        "coverageDimension": component_dimension,
        "sourceSemanticUnitIds": [SOURCE_UNIT],
        "targetSemanticUnitIds": list(target_semantic_unit_ids),
        "targetSpanIds": ["target-span-verification-1"],
        "status": component_status,
        "confidence": {"rawScore": 0.95, "calibratedValue": 0.95},
        "evidence": {
            "id": "meaning-evidence-verification-1", "kind": component_evidence_kind,
            "resourceStatus": resource_status, "resourceEvidenceIds": [],
        },
        "explanation": "controlled verification fixture",
    }
    assessment = {
        "id": "meaning-assessment-verification-1",
        "semanticLocationRelationshipId": relationship["id"],
        "sourceSemanticUnitIds": [SOURCE_UNIT],
        "targetSemanticUnitIds": list(target_semantic_unit_ids),
        "meaningStatus": meaning_status,
        "componentAssessments": [] if component_status == "" else [component],
        "locationOutcomeSnapshot": location_outcome,
        "reviewStatus": "AI_PROPOSED", "lifecycleStatus": "ACTIVE", "revision": 1,
    }
    meaning_run_id = "meaning-run-verification"
    repository.save_meaning_analysis_run(
        run_id=meaning_run_id, project_id=runtime.project_id, book=runtime.book,
        range_key="PHP 1:3-1:6", fingerprint="meaning-fingerprint-verification",
        location_run_id=location_run_id, run_status="COMPLETE",
        payload={
            "id": meaning_run_id, "locationRunId": location_run_id,
            "fingerprint": "meaning-fingerprint-verification",
            "assessments": [assessment], "diagnostics": {},
        },
        assessments=[],
    )

    account_id = "coverage-account-verification-1"
    repository.save_coverage_account(SemanticCoverageAccount(
        id=account_id, project_id=runtime.project_id, passage_id="",
        direction=AuditDirection.SOURCE_COVERAGE, audit_owner_unit_id=SOURCE_UNIT,
        member_unit_ids=(SOURCE_UNIT,),
        coverage_dimension=CoverageDimension(component_dimension),
        semantic_fingerprint=account_id,
        covered_by_relationship_ids=(relationship["id"],),
        excluded_duplicate_unit_ids=(), finding_id=None,
        policy_binding=PolicyBinding.foundation_v1(),
        review_status=ReviewStatus.UNREVIEWED, lifecycle_status=LifecycleStatus.ACTIVE,
        coverage_status=coverage_status,
    ))
    extra_ids: list[str] = []
    for account_key, owner_unit_id, extra_dimension in extra_accounts:
        repository.save_coverage_account(SemanticCoverageAccount(
            id=account_key, project_id=runtime.project_id, passage_id="",
            direction=AuditDirection.SOURCE_COVERAGE, audit_owner_unit_id=owner_unit_id,
            member_unit_ids=(owner_unit_id,),
            coverage_dimension=CoverageDimension(extra_dimension),
            semantic_fingerprint=account_key, covered_by_relationship_ids=(),
            excluded_duplicate_unit_ids=(), finding_id=None,
            policy_binding=PolicyBinding.foundation_v1(),
            review_status=ReviewStatus.UNREVIEWED, lifecycle_status=LifecycleStatus.ACTIVE,
            coverage_status="COVERED",
        ))
        extra_ids.append(account_key)

    support_ids: list[str] = []
    for index, (unit_id, status) in enumerate(target_support):
        support_id = f"coverage-account-target-support-{index}"
        repository.save_coverage_account(SemanticCoverageAccount(
            id=support_id, project_id=runtime.project_id, passage_id="",
            direction=AuditDirection.TARGET_SUPPORT, audit_owner_unit_id=unit_id,
            member_unit_ids=(unit_id,), coverage_dimension=CoverageDimension.LEXICAL_CONTENT,
            semantic_fingerprint=support_id, covered_by_relationship_ids=(),
            excluded_duplicate_unit_ids=(), finding_id=None,
            policy_binding=PolicyBinding.foundation_v1(),
            review_status=ReviewStatus.UNREVIEWED, lifecycle_status=LifecycleStatus.ACTIVE,
            coverage_status=status,
        ))
        support_ids.append(support_id)

    qa_run_id = "qa-run-verification"
    repository.save_qa_audit_run(
        run_id=qa_run_id, project_id=runtime.project_id, book=runtime.book,
        range_key="PHP 1:3-1:6", fingerprint="qa-fingerprint-verification",
        meaning_run_id=meaning_run_id, run_status="COMPLETE",
        payload={
            "id": qa_run_id, "meaningRunId": meaning_run_id,
            "fingerprint": "qa-fingerprint-verification",
            "sourceCoverageAccountIds": [account_id, *extra_ids],
            "targetSupportAccountIds": support_ids,
            "findings": list(findings or ()), "diagnostics": {},
        },
    )
    return {
        "location": location_run_id, "meaning": meaning_run_id, "qa": qa_run_id,
        "coverageAccountId": account_id,
    }


def _analysis_job(
    runtime: Any, application: dict[str, Any], runs: dict[str, str], *,
    overall_status: str = "COMPLETED", search_incomplete: bool = False,
    provider_retrieval: str = "LIMITED", job_id: str = "affected-job-verification",
    warnings: list[dict[str, str]] | None = None,
    target_hash: str | None = None,
) -> dict[str, Any]:
    """Create and associate a durable affected-analysis job for the application."""
    reference = str(application["targetDisplayedReference"])
    chapter, verse = reference.rpartition(" ")[2].split(":", 1)
    current = str(runtime.project.target_verse_text(chapter, verse))
    default_warnings = [] if provider_retrieval == "FULL" else [{
        "code": "MULTILINGUAL_EMBEDDING_PROVIDER_NOT_CONFIGURED",
        "message": "Semantic retrieval is limited because no production "
                   "multilingual embedding provider is configured.",
    }]
    payload = {
        "jobId": job_id, "projectId": runtime.project_id, "book": runtime.book,
        "requestedScope": {
            "kind": "AFFECTED", "correctionApplicationId": application["applicationId"],
            "resolvedSourceReferences": [SOURCE_REFERENCE],
            "resolvedTargetReferences": [reference],
            "resolvedStartChapter": "1", "resolvedStartVerse": "3",
            "resolvedEndChapter": "1", "resolvedEndVerse": "6",
        },
        "rangeKey": "PHP 1:3-1:6",
        "displayedReferences": [SOURCE_REFERENCE, reference],
        "canonicalReferences": [SOURCE_REFERENCE, reference],
        "targetRevision": "target-revision-verification",
        "targetContentHash": _hash(current),
        "targetHashes": {reference: target_hash or _hash(current)},
        "sourceResourceHash": "source-resource-hash",
        "analysisFingerprint": _hash("analysis-fingerprint-verification"),
        "policyVersions": {}, "createdAt": "2026-09-07T00:00:00Z",
        "startedAt": "2026-09-07T00:00:01Z", "completedAt": "2026-09-07T00:00:02Z",
        "currentStage": "", "overallStatus": overall_status,
        "stageStatuses": {
            "SOURCE_INVENTORY": {"status": "COMPLETED", "runId": "source-inventory-verification-fixture", "cacheStatus": "MISS", "elapsedSeconds": 0.0},
            "TARGET_INVENTORY": {"status": "COMPLETED", "runId": "target-inventory-verification-fixture", "cacheStatus": "MISS", "elapsedSeconds": 0.0},
            "LOCATION": {"status": "COMPLETED", "runId": runs["location"], "cacheStatus": "MISS", "elapsedSeconds": 0.0},
            "MEANING": {"status": "COMPLETED", "runId": runs["meaning"], "cacheStatus": "MISS", "elapsedSeconds": 0.0},
            "QA": {"status": "COMPLETED", "runId": runs["qa"], "cacheStatus": "MISS", "elapsedSeconds": 0.0},
        },
        "stageProgress": {"completedStages": 5, "totalStages": 5},
        "reusedRunIds": [], "createdRunIds": [],
        "warnings": default_warnings if warnings is None else warnings,
        "failures": [], "cancellationRequested": False,
        "providerCapability": {"semanticRetrieval": provider_retrieval, "fixtureProvider": False},
        "timings": {}, "qaFindingCount": 0, "searchIncomplete": search_incomplete,
    }
    runtime.repository.create_analysis_job(payload)
    runtime.repository.record_affected_analysis_association(
        application["applicationId"],
        expected_state_revision=int(
            runtime.repository.application_intent(application["applicationId"])["stateRevision"]
        ),
        association={
            "applicationId": application["applicationId"], "analysisJobId": job_id,
            "resolvedSourceReferences": [SOURCE_REFERENCE],
            "resolvedTargetReferences": [reference],
        },
    )
    return payload


class _Jobs:
    """Minimal job reader. The service falls back to the repository anyway."""

    def __init__(self, repository: Any) -> None:
        self.repository = repository

    def status(self, job_id: str) -> dict[str, Any]:
        return self.repository.analysis_job(job_id)


def _verified(
    tmp_path: Path, *, replacement: str = "என் தேவனையே", **evidence: Any,
) -> tuple[Any, dict[str, Any], CorrectionVerificationService, dict[str, Any]]:
    """Apply a correction, publish controlled current evidence, build the service."""
    _root, _project, runtime, application_service, _finding, _proposal = _fixture(
        tmp_path, BEFORE, ORIGINAL_SPAN, replacement,
    )
    application = _apply(application_service)
    assert application["applicationState"] == CorrectionApplicationState.COMPLETED.value
    corrected_verse = str(runtime.project.target_verse_text("1", "6"))
    evidence.setdefault("verse_text", corrected_verse)
    runs = _publish_evidence(runtime, **evidence)
    job = _analysis_job(runtime, application, runs)
    service = CorrectionVerificationService(runtime, _Jobs(runtime.repository))
    return runtime, application, service, job


def _set_dimension(runtime: Any, dimension: str) -> None:
    """Point the persisted proposal intent at a specific failed dimension."""
    repository = runtime.repository
    with repository._connect() as conn:
        row = conn.execute(
            "SELECT payload_json FROM correction_proposals WHERE id='proposal-1'"
        ).fetchone()
        payload = json.loads(row[0])
        payload["intent"]["failedDimension"] = dimension
        conn.execute(
            "UPDATE correction_proposals SET payload_json=? WHERE id='proposal-1'",
            (json.dumps(payload, ensure_ascii=False),),
        )
        conn.commit()


# --- Preconditions ----------------------------------------------------------

def test_verification_requires_a_completed_application(tmp_path: Path) -> None:
    _root, _project, runtime, application_service, _finding, _proposal = _fixture(
        tmp_path, BEFORE, ORIGINAL_SPAN, "என் தேவனையே",
    )
    prepared = application_service._prepare(
        proposal_id="proposal-1", expected_proposal_revision=1,
        finding_id="finding-1", expected_finding_revision=1,
        application_id="prepared-only", actor_id="Reviewer",
    )
    service = CorrectionVerificationService(runtime, _Jobs(runtime.repository))
    result = service.verify(prepared["applicationId"], requested_by="Reviewer")
    assert result["verificationStatus"] == "PENDING"
    assert ReasonCode.APPLICATION_NOT_COMPLETED in result["verificationReasonCodes"]
    assert result["mayVerify"] is False
    assert result["mayAcknowledgeCorrected"] is False


def test_verification_without_affected_analysis_stays_pending(tmp_path: Path) -> None:
    _root, _project, runtime, application_service, _finding, _proposal = _fixture(
        tmp_path, BEFORE, ORIGINAL_SPAN, "என் தேவனையே",
    )
    application = _apply(application_service)
    service = CorrectionVerificationService(runtime, _Jobs(runtime.repository))
    result = service.verify(application["applicationId"], requested_by="Reviewer")
    assert result["verificationStatus"] == "PENDING"
    assert result["verificationReasonCodes"] == [ReasonCode.ANALYSIS_NOT_RUN]
    assert runtime.repository.current_correction_verification(
        application["applicationId"]) is None


@pytest.mark.parametrize("status,code", [
    ("FAILED", ReasonCode.ANALYSIS_FAILED),
    ("CANCELLED", ReasonCode.ANALYSIS_CANCELLED),
    ("RUNNING", ReasonCode.ANALYSIS_RUNNING),
])
def test_technical_analysis_outcome_is_never_a_semantic_failure(
    tmp_path: Path, status: str, code: str,
) -> None:
    _root, _project, runtime, application_service, _finding, _proposal = _fixture(
        tmp_path, BEFORE, ORIGINAL_SPAN, "என் தேவனையே",
    )
    application = _apply(application_service)
    runs = _publish_evidence(
        runtime, source_text="τῷ θεῷ μου", target_text="என் தேவனையே",
        verse_text=str(runtime.project.target_verse_text("1", "6")),
    )
    _analysis_job(runtime, application, runs, overall_status=status)
    service = CorrectionVerificationService(runtime, _Jobs(runtime.repository))
    result = service.verify(application["applicationId"], requested_by="Reviewer")
    assert result["verificationStatus"] == "PENDING"
    assert code in result["verificationReasonCodes"]
    assert result["verificationStatus"] != "FAILED"


def test_analysis_for_older_text_is_rejected_as_not_current(tmp_path: Path) -> None:
    _root, _project, runtime, application_service, _finding, _proposal = _fixture(
        tmp_path, BEFORE, ORIGINAL_SPAN, "என் தேவனையே",
    )
    application = _apply(application_service)
    runs = _publish_evidence(
        runtime, source_text="τῷ θεῷ μου", target_text="என் தேவனையே",
        verse_text=str(runtime.project.target_verse_text("1", "6")),
    )
    _analysis_job(runtime, application, runs, target_hash=_hash(BEFORE))
    service = CorrectionVerificationService(runtime, _Jobs(runtime.repository))
    result = service.verify(application["applicationId"], requested_by="Reviewer")
    assert result["verificationStatus"] == "PENDING"
    assert ReasonCode.ANALYSIS_NOT_CURRENT in result["verificationReasonCodes"]


# --- Positive verification --------------------------------------------------

def test_passed_uses_current_positive_evidence_and_leaves_disposition_alone(
    tmp_path: Path,
) -> None:
    runtime, application, service, _job = _verified(
        tmp_path, source_text="τῷ θεῷ μου", target_text="என் தேவனையே",
        component_status="PRESERVED", coverage_status="COVERED",
    )
    result = service.verify(application["applicationId"], requested_by="Reviewer")

    assert result["verificationStatus"] == "PASSED"
    assert result["verificationCurrent"] is True
    assert ReasonCode.DIMENSION_PRESERVED in result["verificationReasonCodes"]
    assert ReasonCode.COVERAGE_COVERED in result["verificationReasonCodes"]
    # PASSED must never promote the human disposition on its own.
    assert result["qaDisposition"] == "CONFIRMED_TRANSLATION_ERROR"
    assert runtime.repository.qa_finding("finding-1")["qaDisposition"] == (
        "CONFIRMED_TRANSLATION_ERROR"
    )
    assert result["mayAcknowledgeCorrected"] is True
    # The proposal's independent verification field mirrors the verdict.
    assert runtime.repository.correction_proposal("proposal-1")["verificationStatus"] == "PASSED"


def test_failed_reports_the_original_obligation_still_unsatisfied(tmp_path: Path) -> None:
    runtime, application, service, _job = _verified(
        tmp_path, source_text="all", target_text="some",
        component_status="CONTRADICTED", component_dimension="QUANTITY",
        source_dimension="QUANTITY", component_evidence_kind="DETERMINISTIC_CONTRADICTION",
        meaning_status="CONTRADICTED", coverage_status="UNCERTAIN",
    )
    _set_dimension(runtime, "QUANTITY")
    result = service.verify(application["applicationId"], requested_by="Reviewer")

    assert result["verificationStatus"] == "FAILED"
    assert ReasonCode.DIMENSION_CONTRADICTED in result["verificationReasonCodes"]
    assert result["mayAcknowledgeCorrected"] is False
    assert result["qaDisposition"] == "CONFIRMED_TRANSLATION_ERROR"


def test_uncertain_when_current_evidence_cannot_decide(tmp_path: Path) -> None:
    runtime, application, service, _job = _verified(
        tmp_path, source_text="τῷ θεῷ μου", target_text="என் தேவனையே",
        location_outcome="AMBIGUOUS", component_status="NOT_DETERMINABLE",
        meaning_status="UNVERIFIABLE", coverage_status="UNCERTAIN",
    )
    result = service.verify(application["applicationId"], requested_by="Reviewer")

    assert result["verificationStatus"] == "UNCERTAIN"
    assert ReasonCode.LOCATION_AMBIGUOUS in result["verificationReasonCodes"]
    assert result["mayAcknowledgeCorrected"] is False


def test_unresolved_resource_conflict_is_uncertain_not_failed(tmp_path: Path) -> None:
    _runtime, application, service, _job = _verified(
        tmp_path, source_text="τῷ θεῷ μου", target_text="என் தேவனையே",
        component_status="PRESERVED", resource_status="CONFLICTING",
        coverage_status="COVERED",
    )
    result = service.verify(application["applicationId"], requested_by="Reviewer")
    assert result["verificationStatus"] == "UNCERTAIN"
    assert ReasonCode.RESOURCE_CONFLICT_UNRESOLVED in result["verificationReasonCodes"]


def test_search_incomplete_never_becomes_a_pass_or_a_false_absence(tmp_path: Path) -> None:
    _root, _project, runtime, application_service, _finding, _proposal = _fixture(
        tmp_path, BEFORE, ORIGINAL_SPAN, "என் தேவனையே",
    )
    application = _apply(application_service)
    runs = _publish_evidence(
        runtime, source_text="τῷ θεῷ μου", target_text="என் தேவனையே",
        component_status="PRESERVED", coverage_status="COVERED",
        verse_text=str(runtime.project.target_verse_text("1", "6")),
    )
    _analysis_job(runtime, application, runs,
                  overall_status="COMPLETED_WITH_WARNINGS", search_incomplete=True)
    service = CorrectionVerificationService(runtime, _Jobs(runtime.repository))
    result = service.verify(application["applicationId"], requested_by="Reviewer")
    assert result["verificationStatus"] == "UNCERTAIN"
    assert ReasonCode.SEARCH_INCOMPLETE in result["verificationReasonCodes"]


def test_completed_with_warnings_can_still_pass_on_deterministic_evidence(
    tmp_path: Path,
) -> None:
    """The only recorded warning is the missing production embedding provider.

    Section 9's rule: inspect the warning rather than mapping it to UNCERTAIN.
    Deterministic quantity evidence is unaffected by retrieval limitations.
    """
    _root, _project, runtime, application_service, _finding, _proposal = _fixture(
        tmp_path, BEFORE, ORIGINAL_SPAN, "எல்லாரும்",
    )
    application = _apply(application_service)
    runs = _publish_evidence(
        runtime, source_text="all", target_text="எல்லாரும்",
        component_status="PRESERVED", component_dimension="QUANTITY",
        component_evidence_kind="QUANTITY", source_dimension="QUANTITY",
        coverage_status="COVERED",
        verse_text=str(runtime.project.target_verse_text("1", "6")),
    )
    _set_dimension(runtime, "QUANTITY")
    _analysis_job(runtime, application, runs, overall_status="COMPLETED_WITH_WARNINGS")
    service = CorrectionVerificationService(runtime, _Jobs(runtime.repository))
    result = service.verify(application["applicationId"], requested_by="Reviewer")
    assert result["verificationStatus"] == "PASSED"
    assert ReasonCode.PROVIDER_LIMITED in result["verificationReasonCodes"]
    assert result["verification"]["payload"]["providerLimited"] is True


def test_provider_limited_downgrades_a_non_deterministic_pass(tmp_path: Path) -> None:
    _runtime, application, service, _job = _verified(
        tmp_path, source_text="τῷ θεῷ μου", target_text="என் தேவனையே",
        component_status="NOT_EXPLICIT_BUT_RECOVERABLE", component_evidence_kind="CONTEXTUAL",
        coverage_status="COVERED_BY_RESTRUCTURING", realization="IMPLICIT",
    )
    result = service.verify(application["applicationId"], requested_by="Reviewer")
    assert result["verificationStatus"] == "UNCERTAIN"
    assert ReasonCode.PROVIDER_LIMITED in result["verificationReasonCodes"]


# --- Finding disappearance / recurrence -------------------------------------

def test_finding_disappearance_alone_does_not_produce_passed(tmp_path: Path) -> None:
    """The current QA run emits nothing, yet the meaning evidence still fails.

    This is the whole point of Stage 9B.4: absence of the old finding is not
    evidence that the correction worked.
    """
    runtime, application, service, _job = _verified(
        tmp_path, source_text="all", target_text="some",
        component_status="CONTRADICTED", component_dimension="QUANTITY",
        source_dimension="QUANTITY", component_evidence_kind="DETERMINISTIC_CONTRADICTION",
        meaning_status="CONTRADICTED", coverage_status="UNCERTAIN", findings=[],
    )
    _set_dimension(runtime, "QUANTITY")
    result = service.verify(application["applicationId"], requested_by="Reviewer")
    assert result["verification"]["payload"]["recurringFindings"] == []
    assert result["verificationStatus"] == "FAILED"


def test_recurring_stable_finding_identity_is_not_automatically_failed(
    tmp_path: Path,
) -> None:
    """The same stable finding id recurs, on a different semantic dimension.

    Recurrence is matched on semantic identity, not on the id, so a finding
    about another dimension must not decide this correction's verdict.
    """
    runtime, application, service, _job = _verified(
        tmp_path, source_text="τῷ θεῷ μου", target_text="என் தேவனையே",
        component_status="PRESERVED", coverage_status="COVERED",
        component_evidence_kind="LEXICAL_CONCEPT",
        extra_accounts=[("coverage-account-polarity", SOURCE_UNIT, "POLARITY")],
        findings=[{
            "id": "finding-1", "kind": "NEGATION_PROBLEM",
            "sourceSemanticUnitIds": [SOURCE_UNIT],
            "coverageAccountIds": ["coverage-account-polarity"],
        }],
    )
    result = service.verify(application["applicationId"], requested_by="Reviewer")
    # The recurring finding is about POLARITY; this correction targeted
    # LEXICAL_CONTENT, so it names a different semantic obligation and must not
    # decide this verdict.
    assert result["verificationStatus"] == "PASSED"
    assert result["verification"]["payload"]["recurringFindings"] == []


def test_recurring_finding_on_the_same_dimension_forces_uncertain_not_passed(
    tmp_path: Path,
) -> None:
    runtime, application, service, _job = _verified(
        tmp_path, source_text="τῷ θεῷ μου", target_text="என் தேவனையே",
        component_status="PRESERVED", coverage_status="COVERED",
        findings=[{
            "id": "finding-recurred", "kind": "POSSIBLE_UNDERTRANSLATION",
            "sourceSemanticUnitIds": [SOURCE_UNIT],
            "coverageAccountIds": ["coverage-account-verification-1"],
        }],
    )
    result = service.verify(application["applicationId"], requested_by="Reviewer")
    assert result["verificationStatus"] == "UNCERTAIN"
    assert ReasonCode.RECURRING_FINDING_SAME_DIMENSION in result["verificationReasonCodes"]
    assert ReasonCode.CONFLICTING_CURRENT_ASSESSMENT in result["verificationReasonCodes"]
    recurrence = result["verification"]["payload"]["recurringFindings"]
    assert recurrence[0]["sameStableIdentityAsOriginal"] is False


def test_finding_presence_alone_does_not_produce_failed(tmp_path: Path) -> None:
    runtime, application, service, _job = _verified(
        tmp_path, source_text="τῷ θεῷ μου", target_text="என் தேவனையே",
        component_status="PRESERVED", coverage_status="COVERED",
        findings=[{
            "id": "finding-1", "kind": "POSSIBLE_OMISSION",
            "sourceSemanticUnitIds": [SOURCE_UNIT],
            "coverageAccountIds": ["coverage-account-verification-1"],
        }],
    )
    result = service.verify(application["applicationId"], requested_by="Reviewer")
    assert result["verificationStatus"] == "UNCERTAIN"
    assert result["verificationStatus"] != "FAILED"
    assert result["verification"]["payload"]["recurringFindings"][0][
        "sameStableIdentityAsOriginal"] is True


# --- Dimension regression matrix --------------------------------------------

@pytest.mark.parametrize("dimension,source,target,status,kind,expected", [
    ("LEXICAL_CONTENT", "τῷ θεῷ μου", "என் தேவனையே", "PRESERVED", "LEXICAL_CONCEPT", "PASSED"),
    ("POLARITY", "οὐ", "μή", "PRESERVED", "POLARITY", "PASSED"),
    ("POLARITY", "not", "உண்டு", "CONTRADICTED", "DETERMINISTIC_CONTRADICTION", "FAILED"),
    ("QUANTITY", "all", "எல்லாரும்", "PRESERVED", "QUANTITY", "PASSED"),
    ("QUANTITY", "all", "சிலர்", "CONTRADICTED", "DETERMINISTIC_CONTRADICTION", "FAILED"),
    ("PARTICIPANT", "god", "தேவன்", "PRESERVED", "PARTICIPANT", "PASSED"),
    ("PARTICIPANT", "god", "மனிதன்", "CONTRADICTED", "DETERMINISTIC_CONTRADICTION", "FAILED"),
    ("REFERENT", "he", "the man", "NOT_EXPLICIT_BUT_RECOVERABLE", "CONTEXTUAL", "PASSED"),
    ("TEMPORAL_ASPECTUAL", "complete", "முடிப்பார்", "PRESERVED", "COMPLETION", "PASSED"),
    ("TEMPORAL_ASPECTUAL", "complete", "நடத்தி வருவார்", "TARGET_WEAKENS_SPECIFICITY",
     "COMPLETION", "FAILED"),
    ("PREDICATION", "τῷ θεῷ μου", "என் தேவனையே", "PRESERVED", "LEXICAL_CONCEPT", "PASSED"),
])
def test_dimension_specific_verification(
    tmp_path: Path, dimension: str, source: str, target: str,
    status: str, kind: str, expected: str,
) -> None:
    coverage = "COVERED" if expected == "PASSED" else "UNCERTAIN"
    meaning = "PRESERVED" if expected == "PASSED" else "CONTRADICTED"
    _root, _project, runtime, application_service, _finding, _proposal = _fixture(
        tmp_path, BEFORE, ORIGINAL_SPAN, "என் தேவனையே",
    )
    application = _apply(application_service)
    runs = _publish_evidence(
        runtime, source_text=source, target_text=target,
        component_status=status, component_dimension=dimension,
        source_dimension=dimension, component_evidence_kind=kind,
        meaning_status=meaning, coverage_status=coverage,
        verse_text=str(runtime.project.target_verse_text("1", "6")),
    )
    # A configured production provider isolates the dimension logic from the
    # separate provider-limitation rule, which has its own tests.
    _analysis_job(runtime, application, runs, provider_retrieval="FULL")
    _set_dimension(runtime, dimension)
    service = CorrectionVerificationService(runtime, _Jobs(runtime.repository))
    result = service.verify(application["applicationId"], requested_by="Reviewer")
    assert result["verificationStatus"] == expected, result["verificationReasonCodes"]


def test_a_dimension_stage_seven_never_scored_is_still_verified(tmp_path: Path) -> None:
    """The reviewer corrected QUANTITY on a LEXICAL_CONTENT obligation.

    Stage 7 scored only LEXICAL_CONTENT, so the persisted components hold no
    quantity evidence at all. The dimension-targeted recheck supplies it.
    """
    runtime, application, service, _job = _verified(
        tmp_path, source_text="all", target_text="சிலர்",
        component_status="PRESERVED", component_dimension="LEXICAL_CONTENT",
        source_dimension="LEXICAL_CONTENT", coverage_status="COVERED",
    )
    _set_dimension(runtime, "QUANTITY")
    result = service.verify(application["applicationId"], requested_by="Reviewer")
    assert result["verificationStatus"] == "FAILED"
    obligation = result["verification"]["payload"]["obligations"][0]
    assert obligation["directRecheck"]["status"] == "CONTRADICTED"
    assert obligation["componentStatuses"] == []


# --- Null alignment semantics ----------------------------------------------

def test_one_to_null_grammatical_realization_is_not_a_failure(tmp_path: Path) -> None:
    """1 -> null: no target lexical unit, realized grammatically. Legitimate."""
    runtime, application, service, _job = _verified(
        tmp_path, source_text="τῷ θεῷ μου", target_text="என் தேவனையே",
        realization="GRAMMATICALLY_REALIZED", properties=("CLAUSE_RESTRUCTURED",),
        component_status="NOT_EXPLICIT_BUT_RECOVERABLE", component_evidence_kind="GRAMMATICAL",
        meaning_status="PRESERVED_WITH_RESTRUCTURING",
        coverage_status="COVERED_BY_RESTRUCTURING", target_semantic_unit_ids=(),
    )
    result = service.verify(application["applicationId"], requested_by="Reviewer")
    assert result["verificationStatus"] == "PASSED"
    assert ReasonCode.DIMENSION_PRESERVED_BY_RESTRUCTURING in result["verificationReasonCodes"]
    assert ReasonCode.COVERAGE_COVERED_BY_RESTRUCTURING in result["verificationReasonCodes"]
    obligation = result["verification"]["payload"]["obligations"][0]
    assert obligation["cardinalities"] == ["1 → null"]


def test_one_to_null_possibly_missing_abstains_instead_of_failing(tmp_path: Path) -> None:
    """POSSIBLY_MISSING alone is a candidate omission, never an established one.

    Stage 8 left coverage unresolved, so there is no positive current evidence
    either way. Calling this FAILED would assert an absence Stage 8 declined to
    conclude -- the auto-promotion docs/INVARIANTS.md section 39 forbids.
    """
    runtime, application, service, _job = _verified(
        tmp_path, source_text="τῷ θεῷ μου", target_text="",
        location_outcome="NOT_LOCATED", component_status="",
        meaning_status="UNVERIFIABLE", coverage_status="POSSIBLY_MISSING",
        target_semantic_unit_ids=(),
    )
    result = service.verify(application["applicationId"], requested_by="Reviewer")
    assert result["verificationStatus"] == "UNCERTAIN"
    assert ReasonCode.COVERAGE_POSSIBLY_MISSING in result["verificationReasonCodes"]
    assert ReasonCode.COVERAGE_STILL_MISSING not in result["verificationReasonCodes"]
    assert result["mayAcknowledgeCorrected"] is False


def test_one_to_null_resolved_missing_still_fails(tmp_path: Path) -> None:
    """MISSING is Stage 8's positive conclusion, and it does support FAILED."""
    runtime, application, service, _job = _verified(
        tmp_path, source_text="τῷ θεῷ μου", target_text="",
        location_outcome="NOT_LOCATED", component_status="",
        meaning_status="UNVERIFIABLE", coverage_status="MISSING",
        target_semantic_unit_ids=(),
    )
    result = service.verify(application["applicationId"], requested_by="Reviewer")
    assert result["verificationStatus"] == "FAILED"
    assert ReasonCode.COVERAGE_STILL_MISSING in result["verificationReasonCodes"]


def test_possibly_missing_is_never_auto_promoted_to_missing(tmp_path: Path) -> None:
    """The recorded coverage stays POSSIBLY_MISSING; verification promotes nothing."""
    runtime, application, service, _job = _verified(
        tmp_path, source_text="τῷ θεῷ μου", target_text="",
        location_outcome="NOT_LOCATED", component_status="",
        meaning_status="UNVERIFIABLE", coverage_status="POSSIBLY_MISSING",
        target_semantic_unit_ids=(),
    )
    result = service.verify(application["applicationId"], requested_by="Reviewer")
    obligation = result["verification"]["payload"]["obligations"][0]
    assert obligation["coverageStatus"] == "POSSIBLY_MISSING"
    assert obligation["result"] == "UNCERTAIN"
    assert ReasonCode.COVERAGE_STILL_MISSING not in obligation["reasonCodes"]


def test_unresolved_coverage_abstains(tmp_path: Path) -> None:
    """Stage 8's own UNCERTAIN coverage cannot license a pass either."""
    runtime, application, service, _job = _verified(
        tmp_path, source_text="τῷ θεῷ μου", target_text="என் தேவனையே",
        component_status="PRESERVED", coverage_status="UNCERTAIN",
    )
    result = service.verify(application["applicationId"], requested_by="Reviewer")
    assert result["verificationStatus"] == "UNCERTAIN"
    assert ReasonCode.COVERAGE_UNRESOLVED in result["verificationReasonCodes"]


def test_covered_by_restructuring_passes_on_implicit_realization(tmp_path: Path) -> None:
    """COVERED_BY_RESTRUCTURING is preservation, not absence, on 1 -> null."""
    runtime, application, service, _job = _verified(
        tmp_path, source_text="τῷ θεῷ μου", target_text="என் தேவனையே",
        realization="IMPLICIT", properties=("CLAUSE_RESTRUCTURED",),
        component_status="NOT_EXPLICIT_BUT_RECOVERABLE",
        component_evidence_kind="GRAMMATICAL",
        meaning_status="PRESERVED_WITH_RESTRUCTURING",
        coverage_status="COVERED_BY_RESTRUCTURING", target_semantic_unit_ids=(),
    )
    result = service.verify(application["applicationId"], requested_by="Reviewer")
    assert result["verificationStatus"] == "PASSED"
    assert ReasonCode.COVERAGE_COVERED_BY_RESTRUCTURING in result["verificationReasonCodes"]


def test_search_incomplete_prevents_an_absence_based_failure(tmp_path: Path) -> None:
    """A run-level incomplete search cannot license a negative absence claim.

    Coverage is resolved MISSING, which would otherwise be FAILED, but the
    realization may lie in the passage the search never reached.
    """
    _root, _project, runtime, application_service, _finding, _proposal = _fixture(
        tmp_path, BEFORE, ORIGINAL_SPAN, "என் தேவனையே",
    )
    application = _apply(application_service)
    runs = _publish_evidence(
        runtime, source_text="τῷ θεῷ μου", target_text="",
        location_outcome="NOT_LOCATED", component_status="",
        meaning_status="UNVERIFIABLE", coverage_status="MISSING",
        target_semantic_unit_ids=(),
        verse_text=str(runtime.project.target_verse_text("1", "6")),
    )
    _analysis_job(runtime, application, runs,
                  overall_status="COMPLETED_WITH_WARNINGS", search_incomplete=True)
    service = CorrectionVerificationService(runtime, _Jobs(runtime.repository))
    result = service.verify(application["applicationId"], requested_by="Reviewer")
    assert result["verificationStatus"] == "UNCERTAIN"
    assert ReasonCode.SEARCH_INCOMPLETE in result["verificationReasonCodes"]


def test_search_incomplete_does_not_rescue_a_located_contradiction(tmp_path: Path) -> None:
    """Search completeness is irrelevant to a contradiction that WAS located."""
    _root, _project, runtime, application_service, _finding, _proposal = _fixture(
        tmp_path, BEFORE, ORIGINAL_SPAN, "என் தேவனையே",
    )
    application = _apply(application_service)
    runs = _publish_evidence(
        runtime, source_text="all", target_text="சிலர்",
        component_status="CONTRADICTED", component_dimension="QUANTITY",
        source_dimension="QUANTITY", component_evidence_kind="DETERMINISTIC_CONTRADICTION",
        meaning_status="CONTRADICTED", coverage_status="COVERED",
        verse_text=str(runtime.project.target_verse_text("1", "6")),
    )
    _analysis_job(runtime, application, runs,
                  overall_status="COMPLETED_WITH_WARNINGS", search_incomplete=True,
                  provider_retrieval="FULL")
    _set_dimension(runtime, "QUANTITY")
    service = CorrectionVerificationService(runtime, _Jobs(runtime.repository))
    result = service.verify(application["applicationId"], requested_by="Reviewer")
    assert result["verificationStatus"] == "FAILED"
    assert ReasonCode.DIMENSION_CONTRADICTED in result["verificationReasonCodes"]


def test_null_to_one_legitimate_target_support_does_not_block_passed(
    tmp_path: Path,
) -> None:
    """null -> 1: wording the correction introduced is licensed, not an addition."""
    replacement = "என் தேவனையே"
    _root, _project, runtime, application_service, _finding, _proposal = _fixture(
        tmp_path, BEFORE, ORIGINAL_SPAN, replacement,
    )
    application = _apply(application_service)
    start = int(application["expectedStartCodePoint"])
    added = _target_semantic_unit(
        runtime, "target-unit-added", TARGET_REFERENCE, start, start + len(replacement),
    )
    runs = _publish_evidence(
        runtime, source_text="τῷ θεῷ μου", target_text=replacement,
        component_status="PRESERVED", coverage_status="COVERED",
        target_units=[added], target_support=[("target-unit-added", "GRAMMATICALLY_REQUIRED")],
        verse_text=str(runtime.project.target_verse_text("1", "6")),
    )
    _analysis_job(runtime, application, runs)
    service = CorrectionVerificationService(runtime, _Jobs(runtime.repository))
    result = service.verify(application["applicationId"], requested_by="Reviewer")
    assert result["verificationStatus"] == "PASSED"
    assert result["verification"]["payload"]["unresolvedTargetSupportUnitIds"] == []


def test_null_to_one_unresolved_support_inside_the_span_is_uncertain(
    tmp_path: Path,
) -> None:
    replacement = "என் தேவனையே"
    _root, _project, runtime, application_service, _finding, _proposal = _fixture(
        tmp_path, BEFORE, ORIGINAL_SPAN, replacement,
    )
    application = _apply(application_service)
    start = int(application["expectedStartCodePoint"])
    added = _target_semantic_unit(
        runtime, "target-unit-added", TARGET_REFERENCE, start, start + len(replacement),
    )
    runs = _publish_evidence(
        runtime, source_text="τῷ θεῷ μου", target_text=replacement,
        component_status="PRESERVED", coverage_status="COVERED",
        target_units=[added], target_support=[("target-unit-added", "POSSIBLY_UNSUPPORTED")],
        verse_text=str(runtime.project.target_verse_text("1", "6")),
    )
    _analysis_job(runtime, application, runs)
    service = CorrectionVerificationService(runtime, _Jobs(runtime.repository))
    result = service.verify(application["applicationId"], requested_by="Reviewer")
    assert result["verificationStatus"] == "UNCERTAIN"
    assert ReasonCode.NEW_TARGET_SUPPORT_UNRESOLVED in result["verificationReasonCodes"]


# --- Cross-verse PHP 1:3 -> PHP 1:6 regression ------------------------------

def test_php_cross_verse_provenance_survives_verification(tmp_path: Path) -> None:
    runtime, application, service, _job = _verified(
        tmp_path, source_text="τῷ θεῷ μου", target_text="என் தேவனையே",
        component_status="PRESERVED", coverage_status="COVERED",
        properties=("CROSS_VERSE", "REORDERED"),
    )
    scripture_before = (runtime.project.path / "php" / "1.json").read_bytes()
    result = service.verify(application["applicationId"], requested_by="Reviewer")
    payload = result["verification"]["payload"]

    assert result["verificationStatus"] == "PASSED"
    # Source provenance stays PHP 1:3 and is never collapsed onto the target.
    assert payload["sourceReferences"] == [SOURCE_REFERENCE]
    assert payload["targetReferences"] == [TARGET_REFERENCE]
    assert SOURCE_REFERENCE not in payload["targetReferences"]
    obligation = payload["obligations"][0]
    assert obligation["targetReferences"] == [TARGET_REFERENCE]
    # No same-verse source relationship is manufactured.
    relationship = runtime.repository.semantic_location_run(
        payload["locationRunId"])["relationships"][0]
    assert relationship["displayedReferences"] == [TARGET_REFERENCE]
    assert relationship["sourceDisplayedReferences"] == [SOURCE_REFERENCE]
    # Verification is read-only with respect to Scripture.
    assert (runtime.project.path / "php" / "1.json").read_bytes() == scripture_before


def test_verification_never_writes_scripture_usfm_or_alignment(tmp_path: Path) -> None:
    runtime, application, service, _job = _verified(
        tmp_path, source_text="τῷ θεῷ μου", target_text="என் தேவனையே",
        component_status="PRESERVED", coverage_status="COVERED",
    )
    root = runtime.project.path
    watched = {
        path: path.read_bytes() for path in (
            root / "php" / "1.json", root / "php.usfm",
            root / ".apps" / "translationCore" / "alignmentData" / "php" / "1.json",
        )
    }
    verification = service.verify(application["applicationId"], requested_by="Reviewer")
    service.acknowledge_corrected(
        application["applicationId"], verification_id=verification["verificationId"],
        expected_verification_revision=verification["verificationRevision"],
        expected_finding_revision=verification["findingRevision"],
        actor={"actorType": "HUMAN", "actorId": "Reviewer"},
    )
    for path, content in watched.items():
        assert path.read_bytes() == content, f"{path} changed"


def test_word_alignment_state_is_independent_of_a_passed_verification(
    tmp_path: Path,
) -> None:
    runtime, application, service, _job = _verified(
        tmp_path, source_text="τῷ θεῷ μου", target_text="என் தேவனையே",
        component_status="PRESERVED", coverage_status="COVERED",
    )
    alignment_path = (runtime.project.path / ".apps" / "translationCore"
                      / "alignmentData" / "php" / "1.json")
    before = json.loads(alignment_path.read_text(encoding="utf-8"))
    verification = service.verify(application["applicationId"], requested_by="Reviewer")
    service.acknowledge_corrected(
        application["applicationId"], verification_id=verification["verificationId"],
        expected_verification_revision=verification["verificationRevision"],
        expected_finding_revision=verification["findingRevision"],
        actor={"actorType": "HUMAN", "actorId": "Reviewer"},
    )
    after = json.loads(alignment_path.read_text(encoding="utf-8"))
    assert after == before
    # The correction application left PHP 1:6 alignment invalid/reviewable and
    # verification does not approve it.
    assert after["6"].get("alignments") == before["6"].get("alignments")


# --- Idempotency, fingerprint, currentness ----------------------------------

def test_repeated_verification_returns_the_same_record(tmp_path: Path) -> None:
    runtime, application, service, _job = _verified(
        tmp_path, source_text="τῷ θεῷ μου", target_text="என் தேவனையே",
        component_status="PRESERVED", coverage_status="COVERED",
    )
    first = service.verify(application["applicationId"], requested_by="Reviewer")
    second = service.verify(application["applicationId"], requested_by="Reviewer")
    third = service.verify(application["applicationId"], requested_by="Someone Else")
    assert first["verificationId"] == second["verificationId"] == third["verificationId"]
    assert len(runtime.repository.correction_verification_history(
        application["applicationId"])) == 1


def test_a_changed_verifier_fingerprint_makes_a_verification_non_current(
    tmp_path: Path,
) -> None:
    runtime, application, service, _job = _verified(
        tmp_path, source_text="τῷ θεῷ μου", target_text="என் தேவனையே",
        component_status="PRESERVED", coverage_status="COVERED",
    )
    first = service.verify(application["applicationId"], requested_by="Reviewer")
    assert first["verificationStatus"] == "PASSED"

    class _NewPolicy(type(service.policy)):
        version = "correction-verification-policy-v2-test"

    upgraded = CorrectionVerificationService(
        runtime, _Jobs(runtime.repository), policy=_NewPolicy(),
    )
    assert upgraded.fingerprint() != service.fingerprint()
    stale = upgraded.get(application["applicationId"])
    assert stale["verificationCurrent"] is False
    assert stale["verificationStatus"] == "PENDING"
    assert stale["mayAcknowledgeCorrected"] is False
    # Re-evaluating under new logic creates a new record and retains the old.
    refreshed = upgraded.verify(application["applicationId"], requested_by="Reviewer")
    assert refreshed["verificationId"] != first["verificationId"]
    history = runtime.repository.correction_verification_history(application["applicationId"])
    assert len(history) == 2
    assert history[0]["lifecycleStatus"] == "SUPERSEDED"


def test_a_later_target_edit_makes_a_passed_verification_non_current(
    tmp_path: Path,
) -> None:
    runtime, application, service, _job = _verified(
        tmp_path, source_text="τῷ θεῷ μου", target_text="என் தேவனையே",
        component_status="PRESERVED", coverage_status="COVERED",
    )
    verification = service.verify(application["applicationId"], requested_by="Reviewer")
    assert verification["verificationStatus"] == "PASSED"

    runtime.project.apply_scripture_edit("1", "6", "a later unrelated human edit")
    after = service.get(application["applicationId"])
    assert after["verificationCurrent"] is False
    assert after["verificationStatus"] == "PENDING"
    assert ReasonCode.ANALYSIS_NOT_CURRENT in after["verificationReasonCodes"]
    assert after["mayAcknowledgeCorrected"] is False
    # The record itself is retained as history, not deleted.
    assert len(runtime.repository.correction_verification_history(
        application["applicationId"])) == 1


# --- Explicit human acknowledgement -----------------------------------------

def test_passed_requires_explicit_human_acknowledgement_to_reach_corrected(
    tmp_path: Path,
) -> None:
    runtime, application, service, _job = _verified(
        tmp_path, source_text="τῷ θεῷ μου", target_text="என் தேவனையே",
        component_status="PRESERVED", coverage_status="COVERED",
    )
    verification = service.verify(application["applicationId"], requested_by="Reviewer")
    assert verification["verificationStatus"] == "PASSED"
    assert runtime.repository.qa_finding("finding-1")["qaDisposition"] == (
        "CONFIRMED_TRANSLATION_ERROR"
    )

    acknowledged = service.acknowledge_corrected(
        application["applicationId"], verification_id=verification["verificationId"],
        expected_verification_revision=verification["verificationRevision"],
        expected_finding_revision=verification["findingRevision"],
        actor={"actorType": "HUMAN", "actorId": "Reviewer"}, note="Checked in context.",
    )
    assert acknowledged["qaDisposition"] == "CORRECTED"
    assert acknowledged["correctedAcknowledgement"]["acknowledgedBy"] == "Reviewer"
    assert acknowledged["correctedAcknowledgement"]["verificationId"] == (
        verification["verificationId"]
    )
    assert acknowledged["mayAcknowledgeCorrected"] is False
    finding = runtime.repository.qa_finding("finding-1")
    assert finding["qaDisposition"] == "CORRECTED"
    # The original human decision is preserved in review history, not erased.
    history = runtime.repository.review_records("QA_FINDING", "finding-1")
    dispositions = [item.get("newQaDisposition") for item in history]
    assert "CORRECTED" in dispositions


@pytest.mark.parametrize("evidence,expected", [
    (dict(component_status="CONTRADICTED", component_evidence_kind="DETERMINISTIC_CONTRADICTION",
          meaning_status="CONTRADICTED", coverage_status="UNCERTAIN"), "FAILED"),
    (dict(location_outcome="AMBIGUOUS", component_status="NOT_DETERMINABLE",
          meaning_status="UNVERIFIABLE", coverage_status="UNCERTAIN"), "UNCERTAIN"),
])
def test_only_passed_may_be_acknowledged(
    tmp_path: Path, evidence: dict[str, Any], expected: str,
) -> None:
    runtime, application, service, _job = _verified(
        tmp_path, source_text="all", target_text="some", **evidence,
    )
    verification = service.verify(application["applicationId"], requested_by="Reviewer")
    assert verification["verificationStatus"] == expected
    assert verification["mayAcknowledgeCorrected"] is False
    with pytest.raises(FoundationConflict, match="PASSED"):
        service.acknowledge_corrected(
            application["applicationId"], verification_id=verification["verificationId"],
            expected_verification_revision=verification["verificationRevision"],
            expected_finding_revision=verification["findingRevision"],
            actor={"actorType": "HUMAN", "actorId": "Reviewer"},
        )
    assert runtime.repository.qa_finding("finding-1")["qaDisposition"] == (
        "CONFIRMED_TRANSLATION_ERROR"
    )


def test_acknowledgement_requires_a_human_actor(tmp_path: Path) -> None:
    _runtime, application, service, _job = _verified(
        tmp_path, source_text="τῷ θεῷ μου", target_text="என் தேவனையே",
        component_status="PRESERVED", coverage_status="COVERED",
    )
    verification = service.verify(application["applicationId"], requested_by="Reviewer")
    for actor in ({"actorType": "AI", "actorId": "model"}, {"actorType": "HUMAN", "actorId": ""}):
        with pytest.raises(FoundationValidationError):
            service.acknowledge_corrected(
                application["applicationId"], verification_id=verification["verificationId"],
                expected_verification_revision=verification["verificationRevision"],
                expected_finding_revision=verification["findingRevision"], actor=actor,
            )


def test_acknowledgement_fails_closed_on_a_stale_verification_revision(
    tmp_path: Path,
) -> None:
    _runtime, application, service, _job = _verified(
        tmp_path, source_text="τῷ θεῷ μου", target_text="என் தேவனையே",
        component_status="PRESERVED", coverage_status="COVERED",
    )
    verification = service.verify(application["applicationId"], requested_by="Reviewer")
    with pytest.raises(FoundationConflict, match="revision conflict"):
        service.acknowledge_corrected(
            application["applicationId"], verification_id=verification["verificationId"],
            expected_verification_revision=verification["verificationRevision"] + 5,
            expected_finding_revision=verification["findingRevision"],
            actor={"actorType": "HUMAN", "actorId": "Reviewer"},
        )


def test_acknowledgement_fails_closed_on_a_stale_finding_revision(tmp_path: Path) -> None:
    _runtime, application, service, _job = _verified(
        tmp_path, source_text="τῷ θεῷ μου", target_text="என் தேவனையே",
        component_status="PRESERVED", coverage_status="COVERED",
    )
    verification = service.verify(application["applicationId"], requested_by="Reviewer")
    with pytest.raises(FoundationConflict, match="revision conflict"):
        service.acknowledge_corrected(
            application["applicationId"], verification_id=verification["verificationId"],
            expected_verification_revision=verification["verificationRevision"],
            expected_finding_revision=verification["findingRevision"] + 5,
            actor={"actorType": "HUMAN", "actorId": "Reviewer"},
        )


def test_acknowledgement_after_a_later_edit_is_refused(tmp_path: Path) -> None:
    runtime, application, service, _job = _verified(
        tmp_path, source_text="τῷ θεῷ μου", target_text="என் தேவனையே",
        component_status="PRESERVED", coverage_status="COVERED",
    )
    verification = service.verify(application["applicationId"], requested_by="Reviewer")
    runtime.project.apply_scripture_edit("1", "6", "another later human edit")
    with pytest.raises(FoundationConflict):
        service.acknowledge_corrected(
            application["applicationId"], verification_id=verification["verificationId"],
            expected_verification_revision=verification["verificationRevision"],
            expected_finding_revision=verification["findingRevision"],
            actor={"actorType": "HUMAN", "actorId": "Reviewer"},
        )
    assert runtime.repository.qa_finding("finding-1")["qaDisposition"] == (
        "CONFIRMED_TRANSLATION_ERROR"
    )


def test_acknowledging_twice_is_idempotent(tmp_path: Path) -> None:
    runtime, application, service, _job = _verified(
        tmp_path, source_text="τῷ θεῷ மு", target_text="என் தேவனையே",
        component_status="PRESERVED", coverage_status="COVERED",
    )
    verification = service.verify(application["applicationId"], requested_by="Reviewer")
    first = service.acknowledge_corrected(
        application["applicationId"], verification_id=verification["verificationId"],
        expected_verification_revision=verification["verificationRevision"],
        expected_finding_revision=verification["findingRevision"],
        actor={"actorType": "HUMAN", "actorId": "Reviewer"},
    )
    second = service.acknowledge_corrected(
        application["applicationId"], verification_id=verification["verificationId"],
        expected_verification_revision=first["verificationRevision"],
        expected_finding_revision=first["findingRevision"],
        actor={"actorType": "HUMAN", "actorId": "Reviewer"},
    )
    assert second["qaDisposition"] == "CORRECTED"
    assert second["correctedAcknowledgement"]["acknowledgedAt"] == (
        first["correctedAcknowledgement"]["acknowledgedAt"]
    )


def test_corrected_does_not_immunize_the_finding_from_a_later_edit(
    tmp_path: Path,
) -> None:
    """A later Scripture change stales the verification without erasing history."""
    runtime, application, service, _job = _verified(
        tmp_path, source_text="τῷ θεῷ மு", target_text="என் தேவனையே",
        component_status="PRESERVED", coverage_status="COVERED",
    )
    verification = service.verify(application["applicationId"], requested_by="Reviewer")
    acknowledged = service.acknowledge_corrected(
        application["applicationId"], verification_id=verification["verificationId"],
        expected_verification_revision=verification["verificationRevision"],
        expected_finding_revision=verification["findingRevision"],
        actor={"actorType": "HUMAN", "actorId": "Reviewer"},
    )
    assert acknowledged["qaDisposition"] == "CORRECTED"

    runtime.project.apply_scripture_edit("1", "6", "a later revision of this verse")
    after = service.get(application["applicationId"])
    assert after["verificationCurrent"] is False
    assert after["correctedAcknowledgement"]["current"] is False
    # The historical CORRECTED event is preserved, not erased.
    assert after["qaDisposition"] == "CORRECTED"
    history = runtime.repository.correction_verification_history(application["applicationId"])
    assert history[0]["acknowledgedBy"] == "Reviewer"


# --- Restart persistence ----------------------------------------------------

def test_verification_and_corrected_persist_across_a_runtime_restart(
    tmp_path: Path,
) -> None:
    runtime, application, service, _job = _verified(
        tmp_path, source_text="τῷ θεῷ மு", target_text="என் தேவனையே",
        component_status="PRESERVED", coverage_status="COVERED",
    )
    verification = service.verify(application["applicationId"], requested_by="Reviewer")
    service.acknowledge_corrected(
        application["applicationId"], verification_id=verification["verificationId"],
        expected_verification_revision=verification["verificationRevision"],
        expected_finding_revision=verification["findingRevision"],
        actor={"actorType": "HUMAN", "actorId": "Reviewer"},
    )
    scripture = (runtime.project.path / "php" / "1.json").read_bytes()

    from tc_ai_bridge.passage_semantic_runtime import PassageSemanticRuntime
    from tc_ai_bridge.tc_project import TranslationCoreProject

    reopened_project = TranslationCoreProject(runtime.project.path)
    reopened = PassageSemanticRuntime(reopened_project, runtime.project_id)
    reopened_project.attach_passage_semantic_runtime(reopened)
    restarted = CorrectionVerificationService(reopened, _Jobs(reopened.repository))

    state = restarted.get(application["applicationId"])
    assert state["verificationStatus"] == "PASSED"
    assert state["verificationCurrent"] is True
    assert state["qaDisposition"] == "CORRECTED"
    assert state["correctedAcknowledgement"]["acknowledgedBy"] == "Reviewer"
    assert state["applicationState"] == "COMPLETED"
    # No verification or application executed a second time on restart.
    assert len(reopened.repository.correction_verification_history(
        application["applicationId"])) == 1
    assert (reopened_project.path / "php" / "1.json").read_bytes() == scripture


# --- Protocol wiring --------------------------------------------------------

def _call(engine: BridgeEngine, method: str, params: dict) -> dict:
    return engine.handle_request(
        EngineRequest(id="stage9b4", method=method, params=params)
    ).to_dict()


def test_protocol_verify_acknowledge_and_restart_on_the_managed_project(
    tmp_path: Path,
) -> None:
    """Exercise the real protocol against a Bridge-managed runtime project.

    This mirrors installed acceptance: the managed ``data/projects`` copy is
    the authoritative path, every in-memory object is discarded, and the same
    on-disk project is reopened before the persistence assertions.
    """
    app_data = tmp_path / "Bridge" / "data"
    managed = app_data / "projects" / "ta_irv_php"
    root, _project, runtime, application_service, _finding, _proposal = _fixture(
        tmp_path, BEFORE, ORIGINAL_SPAN, "என் தேவனையே", project_root=managed,
    )
    identity = root / ".bridge" / "project.json"
    identity.parent.mkdir(parents=True, exist_ok=True)
    identity.write_text(json.dumps({
        "schemaVersion": 1, "projectId": "project-1", "collectionId": "",
        "sourceFingerprint": "stage9b4-fixture",
        "createdAt": "2026-09-07T00:00:00Z",
    }), encoding="utf-8")
    application = _apply(application_service)
    # Applying a correction edits the verse (tc_project.py's apply_scripture_edit),
    # which -- independently of V11-000a -- also writes the word-alignment
    # "invalid" marker for it (same mutually-exclusive state observed in the
    # real tC backend). Since the V11-000a review fix F6,
    # apply_scripture_edit refreshes the alignment invalidation memo itself
    # as part of that same call, so no manual synchronize_alignment_state()
    # is needed here -- see docs/archive/V11-000a_REVIEW_FIX_PROMPT.md F6 for why an
    # earlier version of this test that called it by hand was masking a real
    # over-invalidation bug rather than reflecting realistic sequencing.
    runs = _publish_evidence(
        runtime, source_text="τῷ θεῷ μου", target_text="என் தேவனையே",
        component_status="PRESERVED", coverage_status="COVERED",
        verse_text=str(runtime.project.target_verse_text("1", "6")),
    )
    _analysis_job(runtime, application, runs)

    settings_path = app_data / "settings.json"
    engine = BridgeEngine(settings=AppSettings(path=settings_path))
    opened = _call(engine, "project.open", {"path": str(root)})
    assert opened["success"] is True, opened
    assert Path(opened["result"]["path"]).resolve() == managed.resolve()

    verified = _call(engine, "correction.verifyApplication", {
        "applicationId": application["applicationId"], "requestedBy": "Reviewer",
    })
    assert verified["success"] is True, verified
    assert verified["result"]["verificationStatus"] == "PASSED"
    assert verified["result"]["qaDisposition"] == "CONFIRMED_TRANSLATION_ERROR"
    assert verified["result"]["mayAcknowledgeCorrected"] is True

    read = _call(engine, "correction.getVerification", {
        "applicationId": application["applicationId"],
    })
    assert read["success"] is True, read
    assert read["result"]["verificationId"] == verified["result"]["verificationId"]

    acknowledged = _call(engine, "correction.acknowledgeCorrected", {
        "applicationId": application["applicationId"],
        "verificationId": verified["result"]["verificationId"],
        "expectedVerificationRevision": verified["result"]["verificationRevision"],
        "expectedFindingRevision": read["result"]["findingRevision"],
        "actorId": "Reviewer", "note": "Verified in context.",
    })
    assert acknowledged["success"] is True, acknowledged
    assert acknowledged["result"]["qaDisposition"] == "CORRECTED"
    assert acknowledged["result"]["verifierEngineVersion"] == VERIFICATION_ENGINE_VERSION

    scripture = (managed / "php" / "1.json").read_bytes()

    # Restart: nothing below touches the objects that performed the writes.
    restarted = BridgeEngine(settings=AppSettings(path=settings_path))
    reopened = _call(restarted, "project.open", {
        "path": str(root), "projectId": "project-1",
    })
    assert reopened["success"] is True, reopened
    after = _call(restarted, "correction.getVerification", {
        "applicationId": application["applicationId"],
    })
    assert after["success"] is True, after
    assert after["result"]["verificationStatus"] == "PASSED"
    assert after["result"]["verificationCurrent"] is True
    assert after["result"]["qaDisposition"] == "CORRECTED"
    assert after["result"]["applicationState"] == "COMPLETED"
    assert after["result"]["correctedAcknowledgement"]["acknowledgedBy"] == "Reviewer"
    assert len(after["result"]["history"]) == 1
    # Neither verification nor the correction application ran a second time.
    assert (managed / "php" / "1.json").read_bytes() == scripture


def test_tamil_negation_polarity_is_grapheme_safe_through_verification(
    tmp_path: Path,
) -> None:
    """The V1.1 comparison key keeps Tamil vowel signs and pulli attached."""
    from tc_ai_bridge.meaning_analysis import DeterministicMeaningComparator

    status, _confidence, _kind, _explanation = DeterministicMeaningComparator.compare(
        "οὐ", "இல்லை", "POLARITY", "NEGATION", "LEXICALLY_REALIZED", {},
    )
    assert status.value == "PRESERVED"

    _root, _project, runtime, application_service, _finding, _proposal = _fixture(
        tmp_path, BEFORE, ORIGINAL_SPAN, "இல்லை",
    )
    application = _apply(application_service)
    runs = _publish_evidence(
        runtime, source_text="οὐ", target_text="இல்லை",
        component_status="PRESERVED", component_dimension="POLARITY",
        component_evidence_kind="POLARITY", source_dimension="POLARITY",
        coverage_status="COVERED",
        verse_text=str(runtime.project.target_verse_text("1", "6")),
    )
    _set_dimension(runtime, "POLARITY")
    _analysis_job(runtime, application, runs, provider_retrieval="FULL")
    service = CorrectionVerificationService(runtime, _Jobs(runtime.repository))
    result = service.verify(application["applicationId"], requested_by="Reviewer")
    assert result["verificationStatus"] == "PASSED"
    assert ReasonCode.DIMENSION_PRESERVED in result["verificationReasonCodes"]
    assert result["mayAcknowledgeCorrected"] is True


def test_affected_analysis_still_never_sets_a_verification_verdict(
    tmp_path: Path,
) -> None:
    """Stage 9B.3c's boundary is unchanged: analysis alone leaves PENDING."""
    _root, _project, runtime, application_service, _finding, _proposal = _fixture(
        tmp_path, BEFORE, ORIGINAL_SPAN, "என் தேவனையே",
    )
    application = _apply(application_service)
    manager = AnalysisJobManager()
    manager.bind_runtime(runtime)
    affected = CorrectionAffectedAnalysisService(runtime, manager)
    status = affected.status(application["applicationId"])
    assert status["verificationStatus"] == "PENDING"
    assert runtime.repository.correction_proposal("proposal-1")["verificationStatus"] == "PENDING"
    assert runtime.repository.qa_finding("finding-1")["qaDisposition"] == (
        "CONFIRMED_TRANSLATION_ERROR"
    )


# --- Schema v13 -> v14 -------------------------------------------------------

def test_v13_to_v14_migration_is_additive_and_keeps_v13_data_readable(
    tmp_path: Path,
) -> None:
    """A real v13 database upgrades without losing or rewriting anything.

    v14 adds exactly one table. The invariant that required it is the UNIQUE
    verification identity asserted below: a JSON blob on the application ledger
    cannot enforce it under a double-clicked Verify button.
    """
    database = tmp_path / "semantic.sqlite3"
    conn = sqlite3.connect(database)
    for version in range(1, 14):
        conn.executescript(getattr(repository_module, f"_MIGRATION_V{version}"))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations("
            "version INTEGER PRIMARY KEY,schema_id TEXT NOT NULL,applied_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO schema_migrations VALUES(?,?,?)",
            (version, repository_module.SCHEMA_ID, "2026-09-06T00:00:00Z"),
        )
    policy_id = "legacy-policy"
    conn.execute(
        "INSERT INTO policy_bindings VALUES(?,?,?,?)",
        (policy_id, "confidence-v1", "calibration-v1", "audit-v1"),
    )
    finding_payload = {
        "id": "legacy-finding", "projectId": "project-1",
        "qaDisposition": "CONFIRMED_TRANSLATION_ERROR",
        "reviewStatus": "HUMAN_APPROVED", "lifecycleStatus": "STALE", "revision": 3,
    }
    conn.execute(
        "INSERT INTO qa_findings"
        "(id,project_id,qa_disposition,policy_binding_id,review_status,lifecycle_status,"
        "revision,payload_json) VALUES(?,?,?,?,?,?,?,?)",
        ("legacy-finding", "project-1", "CONFIRMED_TRANSLATION_ERROR", policy_id,
         "HUMAN_APPROVED", "STALE", 3, json.dumps(finding_payload)),
    )
    proposal_payload = {
        "id": "legacy-proposal", "qaFindingId": "legacy-finding", "projectId": "project-1",
        "proposedText": "replacement", "affectedReferences": ["PHP 1:6"],
        "intent": {"affectedTargetSpan": {
            "displayedReference": "PHP 1:6", "canonicalReferences": ["PHP 1:6"],
            "startCodePoint": 0, "endCodePoint": 1, "targetContentHash": "before-hash",
        }},
        "reviewStatus": "HUMAN_APPROVED", "lifecycleStatus": "ACTIVE", "revision": 2,
    }
    conn.execute(
        "INSERT INTO correction_proposals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("legacy-proposal", "project-1", "legacy-finding", "target-r1", "target-r2",
         policy_id, "HUMAN_APPROVED", "ACTIVE", 2, json.dumps(proposal_payload),
         2, "PENDING", 1),
    )
    conn.execute(
        "INSERT INTO correction_proposal_events(id,proposal_id,event_type,actor_type,"
        "actor_id,base_revision,new_revision,reason,provider_metadata_json,"
        "proposal_snapshot_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("legacy-event", "legacy-proposal", "EDITED", "HUMAN", "Reviewer", 1, 2,
         "reviewed", "{}", json.dumps(proposal_payload), "2026-09-06T00:00:00Z"),
    )
    conn.commit()
    conn.close()
    before_rows = {
        "proposals": 1, "findings": 1, "events": 1,
    }

    repo = FoundationRepository(database)

    assert repo.schema_version() == DATABASE_SCHEMA_VERSION == 16
    # Every v13 record survives, unmodified.
    finding = repo.qa_finding("legacy-finding")
    assert finding["qaDisposition"] == "CONFIRMED_TRANSLATION_ERROR"
    assert finding["lifecycleStatus"] == "STALE"
    assert finding["revision"] == 3
    proposal = repo.correction_proposal("legacy-proposal")
    assert proposal["reviewStatus"] == "HUMAN_APPROVED"
    assert proposal["verificationStatus"] == "PENDING"
    assert proposal["revision"] == 2
    history = repo.correction_proposal_history("legacy-proposal")
    assert [item["eventType"] for item in history] == ["EDITED"]

    with sqlite3.connect(database) as migrated:
        migrated.row_factory = sqlite3.Row
        assert {
            "id", "project_id", "application_id", "proposal_id", "finding_id",
            "analysis_job_id", "target_content_hash", "verifier_fingerprint",
            "result", "lifecycle_status", "acknowledged_at", "acknowledged_by",
            "revision", "payload_json",
        } <= {row[1] for row in migrated.execute(
            "PRAGMA table_info(correction_verifications)")}
        # Additive only: no v13 row was rewritten or dropped.
        assert migrated.execute(
            "SELECT COUNT(*) FROM correction_proposals").fetchone()[0] == before_rows["proposals"]
        assert migrated.execute(
            "SELECT COUNT(*) FROM qa_findings").fetchone()[0] == before_rows["findings"]
        assert migrated.execute(
            "SELECT COUNT(*) FROM correction_proposal_events").fetchone()[0] == before_rows["events"]
        assert migrated.execute(
            "SELECT COUNT(*) FROM correction_verifications").fetchone()[0] == 0
    assert (tmp_path / "backups").is_dir()
    # A v13 database with no verification history reads as PENDING, not as an
    # error and never as a verdict.
    assert repo.current_correction_verification("legacy-application") is None
    assert repo.correction_verification_history("legacy-application") == []


def test_every_ledger_insert_names_its_columns_and_assigns_seq() -> None:
    """Static guard: #84's `seq` has to be set by *every* writer, not most.

    Adding the column broke four positional `INSERT ... VALUES(?,?,...)`
    statements at once, and the fourth -- the STALE event written by dependency
    invalidation -- was missed on the first pass and only surfaced as 93 test
    failures. A positional insert into an append-only ledger is exactly the
    shape that breaks silently the next time a column is added, so forbid it
    outright rather than trusting the next person to grep.
    """
    source = Path(repository_module.__file__).read_text(encoding="utf-8")
    # The _MIGRATION_V* scripts legitimately rebuild these tables with their
    # own column lists; only runtime writes are in scope here.
    runtime = re.sub(r'_MIGRATION_V\d+ = r?""".*?"""', "", source, flags=re.S)

    for table in ("correction_proposal_events", "correction_verifications", "review_records"):
        # `INSERT OR IGNORE` counts: import_review_record used that form, and a
        # pattern matching only bare `INSERT INTO` missed it -- which showed up
        # not as a crash but as a legacy import being silently quarantined by
        # the `except Exception` around it.
        statements = [
            runtime[m.start():m.start() + 800]
            for m in re.finditer(
                rf'"INSERT( OR (?:IGNORE|REPLACE|ABORT))? INTO {table}[\s("]', runtime
            )
        ]
        assert statements, f"expected at least one runtime INSERT into {table}"
        for statement in statements:
            assert f"IFNULL(MAX(seq),0)+1 FROM {table}" in statement, (
                f"a runtime INSERT INTO {table} does not assign seq:\n"
                f"{statement[:300]}"
            )


def test_v15_to_v16_migration_gives_every_ledger_row_an_explicit_sequence(
    tmp_path: Path,
) -> None:
    """#84: v16 retires the `ORDER BY created_at, rowid` tie-break.

    Builds a real v15 database and writes ten proposal events that all share
    one `created_at` -- the tick collision that produced randomly-ordered
    history in the first place -- then migrates forward and asserts the rows
    still come back in the order they were written. The backfill is from
    rowid, which is exactly what the tie-break was already reading, so this
    pins that the replacement agrees with the thing it replaces.

    The new insert SQL is not exercised here on purpose: every test in the
    suite that creates a proposal or a review runs through
    `_append_correction_event` / `_append_review`, so a mistake in the named
    column lists fails loudly and everywhere rather than in one migration test.
    """
    database = tmp_path / "semantic.sqlite3"
    conn = sqlite3.connect(database)
    for version in range(1, 16):
        conn.executescript(getattr(repository_module, f"_MIGRATION_V{version}"))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations("
            "version INTEGER PRIMARY KEY,schema_id TEXT NOT NULL,applied_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO schema_migrations VALUES(?,?,?)",
            (version, repository_module.SCHEMA_ID, "2026-09-14T00:00:00Z"),
        )
    policy_id = "legacy-policy"
    conn.execute(
        "INSERT INTO policy_bindings VALUES(?,?,?,?)",
        (policy_id, "confidence-v1", "calibration-v1", "audit-v1"),
    )
    finding_payload = {
        "id": "f1", "projectId": "p1", "qaDisposition": "UNRESOLVED",
        "reviewStatus": "UNREVIEWED", "lifecycleStatus": "ACTIVE", "revision": 1,
    }
    conn.execute(
        "INSERT INTO qa_findings"
        "(id,project_id,qa_disposition,policy_binding_id,review_status,lifecycle_status,"
        "revision,payload_json) VALUES(?,?,?,?,?,?,?,?)",
        ("f1", "p1", "UNRESOLVED", policy_id, "UNREVIEWED", "ACTIVE", 1,
         json.dumps(finding_payload)),
    )
    proposal_payload = {"id": "prop-1", "qaFindingId": "f1", "projectId": "p1", "revision": 1}
    conn.execute(
        "INSERT INTO correction_proposals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("prop-1", "p1", "f1", "target-r1", "target-r2", policy_id, "UNREVIEWED",
         "ACTIVE", 1, json.dumps(proposal_payload), 1, "PENDING", 1),
    )
    frozen = "2026-09-14T00:00:00+00:00"
    written = [f"EVENT-{index}" for index in range(10)]
    for index, reason in enumerate(written):
        conn.execute(
            "INSERT INTO correction_proposal_events VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (f"evt-{index}", "prop-1", "EDITED", "HUMAN", "human", index, index + 1,
             reason, "{}", json.dumps(proposal_payload), frozen),
        )
    conn.commit()
    conn.close()

    repo = FoundationRepository(database)

    assert repo.schema_version() == DATABASE_SCHEMA_VERSION == 16
    events = repo.correction_proposal_history("prop-1")
    assert [event["reason"] for event in events] == written, (
        "v16 must preserve the order the rowid tie-break was already reading"
    )

    with sqlite3.connect(database) as check:
        seqs = [row[0] for row in check.execute(
            "SELECT seq FROM correction_proposal_events ORDER BY seq"
        ).fetchall()]
    assert all(value is not None for value in seqs), "every backfilled row needs a seq"
    assert len(set(seqs)) == 10 and seqs == sorted(seqs), "seq must be unique and ordered"


def test_v14_to_v15_migration_is_additive_and_keeps_v14_data_readable(
    tmp_path: Path,
) -> None:
    """V11-003 (#57): v15 widens correction_proposal_events.event_type's CHECK
    constraint to admit REVIEW_STATUS_BACKFILLED, by rebuilding the table --
    SQLite cannot ALTER a CHECK constraint in place. A real v14 database
    upgrades without losing or rewriting anything, and the widened
    constraint actually accepts the new literal afterward (not just assumed
    from reading the migration script).
    """
    database = tmp_path / "semantic.sqlite3"
    conn = sqlite3.connect(database)
    for version in range(1, 15):
        conn.executescript(getattr(repository_module, f"_MIGRATION_V{version}"))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations("
            "version INTEGER PRIMARY KEY,schema_id TEXT NOT NULL,applied_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO schema_migrations VALUES(?,?,?)",
            (version, repository_module.SCHEMA_ID, "2026-09-12T00:00:00Z"),
        )
    policy_id = "legacy-policy"
    conn.execute(
        "INSERT INTO policy_bindings VALUES(?,?,?,?)",
        (policy_id, "confidence-v1", "calibration-v1", "audit-v1"),
    )
    finding_payload = {
        "id": "legacy-finding", "projectId": "project-1",
        "qaDisposition": "CONFIRMED_TRANSLATION_ERROR",
        "reviewStatus": "HUMAN_APPROVED", "lifecycleStatus": "ACTIVE", "revision": 1,
    }
    conn.execute(
        "INSERT INTO qa_findings"
        "(id,project_id,qa_disposition,policy_binding_id,review_status,lifecycle_status,"
        "revision,payload_json) VALUES(?,?,?,?,?,?,?,?)",
        ("legacy-finding", "project-1", "CONFIRMED_TRANSLATION_ERROR", policy_id,
         "HUMAN_APPROVED", "ACTIVE", 1, json.dumps(finding_payload)),
    )
    proposal_payload = {
        "id": "legacy-proposal", "qaFindingId": "legacy-finding", "projectId": "project-1",
        "proposedText": "unreviewed wording", "creationMode": "HUMAN_AUTHORED",
        "affectedReferences": ["PHP 1:6"],
        "intent": {"affectedTargetSpan": {
            "displayedReference": "PHP 1:6", "canonicalReferences": ["PHP 1:6"],
            "startCodePoint": 0, "endCodePoint": 1, "targetContentHash": "before-hash",
        }},
        "reviewStatus": "UNREVIEWED", "lifecycleStatus": "ACTIVE", "revision": 1,
    }
    conn.execute(
        "INSERT INTO correction_proposals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("legacy-proposal", "project-1", "legacy-finding", "target-r1", "target-r2",
         policy_id, "UNREVIEWED", "ACTIVE", 1, json.dumps(proposal_payload),
         2, "PENDING", 1),
    )
    conn.execute(
        "INSERT INTO correction_proposal_events(id,proposal_id,event_type,actor_type,"
        "actor_id,base_revision,new_revision,reason,provider_metadata_json,"
        "proposal_snapshot_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("legacy-event", "legacy-proposal", "CREATED", "HUMAN", "Reviewer", 0, 1,
         "created before V11-003", "{}", json.dumps(proposal_payload), "2026-09-12T00:00:00Z"),
    )
    conn.commit()
    conn.close()

    repo = FoundationRepository(database)

    assert repo.schema_version() == DATABASE_SCHEMA_VERSION == 16
    # The pre-existing row and its event survive, unmodified by the rebuild.
    with sqlite3.connect(database) as migrated:
        migrated.row_factory = sqlite3.Row
        assert migrated.execute(
            "SELECT COUNT(*) FROM correction_proposal_events").fetchone()[0] == 1
        legacy_event = migrated.execute(
            "SELECT * FROM correction_proposal_events WHERE id='legacy-event'").fetchone()
        assert legacy_event["event_type"] == "CREATED"
        assert legacy_event["actor_type"] == "HUMAN"
    # The widened constraint actually accepts the new literal now -- proven
    # by reading this UNREVIEWED + HUMAN_AUTHORED row (never touched above),
    # which the running repository reseeds on read.
    read = repo.correction_proposal("legacy-proposal")
    assert read["reviewStatus"] == "HUMAN_APPROVED"
    assert read["revision"] == 1
    history = repo.correction_proposal_history("legacy-proposal")
    assert [item["eventType"] for item in history] == ["CREATED", "REVIEW_STATUS_BACKFILLED"]
    assert history[-1]["actorType"] == "MIGRATION"
    assert (tmp_path / "backups").is_dir()


def test_the_verification_identity_is_unique_in_the_database(tmp_path: Path) -> None:
    """The invariant that justified v14 rather than v13 metadata.

    Two verifications for the same application/job/target-hash/fingerprint are
    the same verification. The second request must return the first record, not
    insert a duplicate -- a double-clicked Verify button is the realistic case.
    """
    runtime, application, service, job = _verified(
        tmp_path, source_text="τῷ θεῷ μου", target_text="என் தேவனையே",
        component_status="PRESERVED", coverage_status="COVERED",
    )
    first = service.verify(application["applicationId"], requested_by="Reviewer")
    duplicate = runtime.repository.save_correction_verification(
        verification_id="a-second-identity-for-the-same-inputs",
        project_id=runtime.project_id, application_id=application["applicationId"],
        proposal_id="proposal-1", proposal_revision=2, finding_id="finding-1",
        analysis_job_id=str(job["jobId"]),
        target_revision="target-revision-verification",
        target_content_hash=first["verification"]["targetContentHash"],
        verifier_fingerprint=service.fingerprint(), result="FAILED", confidence=0.1,
        reason_codes=["DIMENSION_CONTRADICTED"], payload={}, created_at="now",
    )
    # The existing record is returned; no second row and no changed verdict.
    assert duplicate["verificationId"] == first["verificationId"]
    assert duplicate["result"] == "PASSED"
    assert len(runtime.repository.correction_verification_history(
        application["applicationId"])) == 1


def test_verification_is_registered_in_the_shared_dependency_map(
    tmp_path: Path,
) -> None:
    """A record type that can be written must be known to stale propagation.

    Stage 9A.4 found the failure mode this guards: an unregistered dependency
    type made recovery report every audited project as corrupt.
    """
    assert RECORD_DEPENDENCY_TABLES["CORRECTION_VERIFICATION"] == "correction_verifications"
    runtime, application, service, _job = _verified(
        tmp_path, source_text="τῷ θεῷ μου", target_text="என் தேவனையே",
        component_status="PRESERVED", coverage_status="COVERED",
    )
    verification = service.verify(application["applicationId"], requested_by="Reviewer")
    with runtime.repository._connect() as conn:
        edges = {
            (row["depends_on_type"], ) for row in conn.execute(
                "SELECT depends_on_type FROM record_dependencies "
                "WHERE record_type='CORRECTION_VERIFICATION' AND record_id=?",
                (verification["verificationId"],),
            )
        }
    assert ("CORRECTION_PROPOSAL",) in edges
    assert ("TARGET_REFERENCE",) in edges
    assert runtime.repository.recovery_check()["readOnly"] is False
