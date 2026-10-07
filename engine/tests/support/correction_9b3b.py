"""Builders for the Stage 9B correction tests: a project with a confirmed Stage
8 finding and an approved correction proposal (Stage 9B.3b's _fixture), its
authorized apply, and the text hash the ledger keys on.

Moved verbatim out of test_correction_stage9b3b.py, which three other modules
imported them from (CLAUDE.md rule 2, #74). The correction ledger is append-only; these
builders only set up projects for the tests that check that."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tc_ai_bridge.correction_application import CorrectionApplicationService
from tc_ai_bridge.qa_audit import QaAuditPolicy
from tc_ai_bridge.passage_semantic_models import (
    AffectedTargetSpan,
    AuditDirection,
    ConfidenceScore,
    CorrectionCreationMode,
    CorrectionIntent,
    CorrectionProposalV2,
    CoverageDimension,
    LifecycleStatus,
    PolicyBinding,
    QaDisposition,
    QaFinding,
    QaFindingKind,
    ReviewStatus,
)
from tc_ai_bridge.passage_semantic_runtime import PassageSemanticRuntime
from tc_ai_bridge.tc_project import TranslationCoreProject


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fixture(
    tmp_path: Path, before: str, original: str, replacement: str, *,
    project_root: Path | None = None, project_id: str = "project-1",
):
    root = project_root or tmp_path / "project"
    alignment = root / ".apps" / "translationCore" / "alignmentData" / "php"
    alignment.mkdir(parents=True)
    (root / "php").mkdir(parents=True)
    (root / "manifest.json").write_text(json.dumps({
        "project": {"id": "php", "name": "Philippians"},
        "target_language": {"id": "tam", "name": "Tamil"},
        "resource": {"id": "irv", "name": "IRVTam"}, "tc_version": "8",
    }), encoding="utf-8")
    (root / "php" / "1.json").write_text(
        json.dumps({"3": "unchanged source-corresponding target", "6": before}, ensure_ascii=False),
        encoding="utf-8",
    )
    (alignment / "1.json").write_text(json.dumps({
        "3": {"alignments": [], "wordBank": []},
        "6": {"alignments": [], "wordBank": []},
    }), encoding="utf-8")
    (root / "php.usfm").write_text("\\id PHP\n\\c 1\n\\v 3 IMPORTED THREE\n\\v 6 IMPORTED SIX\n", encoding="utf-8")
    project = TranslationCoreProject(root)
    runtime = PassageSemanticRuntime(project, project_id)
    project.attach_passage_semantic_runtime(runtime)
    repo = runtime.repository
    source_unit_id = "source-unit-php-1-3"
    repo.create_minimal_semantic_unit(source_unit_id, project_id=project_id)
    source_unit = repo.semantic_unit(source_unit_id)
    source_unit.update({
        "displayedReferences": ["PHP 1:3"],
        "canonicalReferences": ["PHP 1:3"],
        "rawSurface": "τῷ θεῷ μου",
        "normalizedSurface": "τῷ θεῷ μου",
    })
    with repo._connect() as conn:
        conn.execute(
            "UPDATE semantic_units SET payload_json=? WHERE id=?",
            (json.dumps(source_unit, ensure_ascii=False), source_unit_id),
        )
        conn.commit()
    # Persist the finding through the same canonical repository save path
    # Stage 8 uses.  Writing the human-decided columns with raw SQL instead
    # left the schema-v8 Stage 9A queue index (book, kind, direction,
    # severity/severity_rank, sort_chapter/sort_verse, displayed_reference and
    # the qa_finding_scope_references rows) at its empty create-time defaults,
    # so `qa_finding()` and the correction services could read the finding
    # while `query_qa_findings()` could never return it under a real UI scope.
    policy = PolicyBinding.foundation_v1()
    confidence = ConfidenceScore(
        raw_score=None, calibrated_value=0.0,
        confidence_policy_version=policy.confidence_policy_version,
        calibration_version=policy.calibration_version,
    )
    kind = QaFindingKind.NEEDS_PASSAGE_REVIEW
    repo.save_qa_finding(QaFinding(
        id="finding-1", project_id=project_id, book="PHP", passage_id="PHP",
        kind=kind, direction=AuditDirection.SOURCE_COVERAGE,
        source_semantic_unit_ids=(source_unit_id,), target_semantic_unit_ids=(),
        semantic_relationship_ids=(), evidence_ids=(), explanation="",
        confidence=confidence, current_target_revision="UNBOUND",
        qa_disposition=QaDisposition.CONFIRMED_TRANSLATION_ERROR,
        policy_binding=policy, review_status=ReviewStatus.HUMAN_APPROVED,
        lifecycle_status=LifecycleStatus.ACTIVE,
        severity=QaAuditPolicy.severity_for(kind, confidence.calibrated_value),
        meaning_assessment_ids=(), coverage_account_ids=(),
        location_outcome_snapshot="", meaning_status_snapshot="",
        supporting_evidence_ids=(), conflicting_evidence_ids=(),
        resource_evidence_ids=(), target_content_hashes=(_hash(before),),
        source_resource_hashes=(), qa_engine_version="stage9b-fixture",
        qa_policy_version=policy.audit_policy_version, fingerprint="stage9b-fixture",
        revision=1, displayed_references=("PHP 1:6",),
        resource_conflict_evidence_ids=(),
    ))
    finding = repo.qa_finding("finding-1")
    current = repo.current_target_revision(project_id, "PHP", "PHP 1:6")
    start = before.index(original)
    proposal = CorrectionProposalV2(
        id="proposal-1", qa_finding_id="finding-1", project_id=project_id,
        intent=CorrectionIntent(
            failed_dimension=CoverageDimension.LEXICAL_CONTENT,
            observed_meaning="missing", required_meaning="required",
            affected_source_semantic_unit_ids=(source_unit_id,),
            affected_target_span=AffectedTargetSpan(
                displayed_reference="PHP 1:6", canonical_references=("PHP 1:6",),
                start_code_point=start, end_code_point=start + len(original),
                original_text=original, target_text_revision=current["textRevision"],
                target_content_hash=_hash(before),
            ),
        ),
        # Reproduce the installed legacy proposal: this overloaded field kept
        # only the target reference. Application must recover source provenance
        # from the durable source semantic-unit identity.
        affected_references=("PHP 1:6",), current_text=original,
        proposed_text=replacement, explanation="reviewed correction", evidence_ids=(),
        semantic_relationship_ids=(), meaning_assessment_ids=(), created_by="Reviewer",
        created_at="2026-09-05T00:00:00Z",
        creation_mode=CorrectionCreationMode.HUMAN_AUTHORED,
        policy_binding=PolicyBinding.foundation_v1(),
        review_status=ReviewStatus.HUMAN_MODIFIED,
        lifecycle_status=LifecycleStatus.ACTIVE,
    )
    repo.save_correction_proposal_v2(proposal)
    service = CorrectionApplicationService(
        runtime,
        lambda chapter, verse, text, **options: project.apply_scripture_edit(
            chapter, verse, text, **options,
        ),
    )
    return root, project, runtime, service, finding, proposal


def _apply(service, finding_revision=1, proposal_revision=1, application_id="apply-1"):
    return service.apply(
        proposal_id="proposal-1", expected_proposal_revision=proposal_revision,
        finding_id="finding-1", expected_finding_revision=finding_revision,
        application_id=application_id,
        actor={"actorType": "HUMAN", "actorId": "Reviewer"},
    )
