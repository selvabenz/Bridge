"""V11-002/V11-005: single source of truth for reviewer identity.

Covers: OS-account seeding instead of the literal 'AI Bridge Reviewer',
rename timestamp tracking, and history immutability (a rename must never
retroactively alter who an already-persisted event says did something).
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

import pytest

from tc_ai_bridge import secret_store
from tc_ai_bridge.secret_store import AppSettings
from tc_ai_bridge.qa_audit import QaAuditPolicy
from tc_ai_bridge.passage_semantic_models import (
    ActorType,
    AffectedTargetSpan,
    AuditDirection,
    ConfidenceScore,
    CorrectionCreationMode,
    CorrectionIntent,
    CorrectionProposalV2,
    CorrectionProviderMetadata,
    CoverageDimension,
    LifecycleStatus,
    PolicyBinding,
    QaDisposition,
    QaFinding,
    QaFindingKind,
    ReviewStatus,
)

from tests.support.correction_9b3b import _fixture


def _second_finding(runtime, *, finding_id: str) -> dict:
    """A finding cannot have two ACTIVE proposals at once (real ownership
    rule); give the AI-proposal test its own finding rather than fight
    _fixture()'s already-owned finding-1."""
    repo = runtime.repository
    policy = PolicyBinding.foundation_v1()
    confidence = ConfidenceScore(
        raw_score=None, calibrated_value=0.0,
        confidence_policy_version=policy.confidence_policy_version,
        calibration_version=policy.calibration_version,
    )
    kind = QaFindingKind.NEEDS_PASSAGE_REVIEW
    repo.save_qa_finding(QaFinding(
        id=finding_id, project_id=runtime.project_id, book="PHP", passage_id="PHP",
        kind=kind, direction=AuditDirection.SOURCE_COVERAGE,
        source_semantic_unit_ids=("source-unit-php-1-3",), target_semantic_unit_ids=(),
        semantic_relationship_ids=(), evidence_ids=(), explanation="",
        confidence=confidence, current_target_revision="UNBOUND",
        qa_disposition=QaDisposition.CONFIRMED_TRANSLATION_ERROR,
        policy_binding=policy, review_status=ReviewStatus.HUMAN_APPROVED,
        lifecycle_status=LifecycleStatus.ACTIVE,
        severity=QaAuditPolicy.severity_for(kind, confidence.calibrated_value),
        meaning_assessment_ids=(), coverage_account_ids=(),
        location_outcome_snapshot="", meaning_status_snapshot="",
        supporting_evidence_ids=(), conflicting_evidence_ids=(),
        resource_evidence_ids=(), target_content_hashes=(),
        source_resource_hashes=(), qa_engine_version="v11-fixture",
        qa_policy_version=policy.audit_policy_version, fingerprint="v11-fixture",
        revision=1, displayed_references=("PHP 1:6",),
        resource_conflict_evidence_ids=(),
    ))
    return repo.qa_finding(finding_id)


def test_fresh_profile_seeds_reviewer_name_from_os_account(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(secret_store.getpass, "getuser", lambda: "benz")
    settings = AppSettings(tmp_path / "settings.json")

    assert settings.reviewer_name == "benz"
    assert settings.reviewer_name != "AI Bridge Reviewer"
    # Seeding persists -- it is not recomputed (and not re-savable) every read.
    # Non-secret settings are workspace rows since #77, so reload rather than
    # reading settings.json (which now holds only DPAPI-wrapped secrets).
    monkeypatch.setattr(secret_store.getpass, "getuser", lambda: "someone-else")
    assert AppSettings(tmp_path / "settings.json").reviewer_name == "benz"
    assert "reviewer_name" not in (tmp_path / "settings.json").read_text(encoding="utf-8")


def test_fresh_profile_falls_back_to_neutral_name_when_os_account_unreadable(
    tmp_path: Path, monkeypatch,
) -> None:
    def _raise() -> str:
        raise OSError("no such user")
    monkeypatch.setattr(secret_store.getpass, "getuser", _raise)
    settings = AppSettings(tmp_path / "settings.json")

    assert settings.reviewer_name == "Unnamed Reviewer"
    assert "AI" not in settings.reviewer_name


def test_stale_pre_v11_005_profile_reseeds_from_os_account(tmp_path: Path, monkeypatch) -> None:
    """A settings.json written before V11-005 landed has the old literal
    'AI Bridge Reviewer' persisted as data, not merely defaulted -- so the
    plain "reseed if empty" check never fires for it. It must still heal
    on next read, same as a brand-new profile."""
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"reviewer_name": "AI Bridge Reviewer"}), encoding="utf-8")
    monkeypatch.setattr(secret_store.getpass, "getuser", lambda: "benz")

    settings = AppSettings(path)

    assert settings.reviewer_name == "benz"
    # Persisted (as a workspace row since #77): a reload with a different OS
    # account still reads the healed name rather than reseeding.
    monkeypatch.setattr(secret_store.getpass, "getuser", lambda: "someone-else")
    assert AppSettings(path).reviewer_name == "benz"


def test_explicit_rename_to_ai_bridge_reviewer_literal_is_respected(tmp_path: Path, monkeypatch) -> None:
    """Unlike the stale-profile case above, a real Settings-initiated rename
    always stamps reviewer_name_updated_at -- that's what distinguishes a
    deliberate (if unusual) choice from the old un-stamped default."""
    monkeypatch.setattr(secret_store.getpass, "getuser", lambda: "benz")
    settings = AppSettings(tmp_path / "settings.json")

    settings.reviewer_name = "AI Bridge Reviewer"

    assert settings.reviewer_name == "AI Bridge Reviewer"


def test_reviewer_name_setter_records_a_parseable_utc_timestamp(tmp_path: Path) -> None:
    settings = AppSettings(tmp_path / "settings.json")
    assert settings.reviewer_name_updated_at == ""

    settings.reviewer_name = "Alice"

    stamp = settings.reviewer_name_updated_at
    assert stamp
    datetime.fromisoformat(stamp)  # raises if not real ISO-8601
    assert settings.reviewer_name == "Alice"


def test_clearing_reviewer_name_reseeds_rather_than_reverting_to_ai_bridge_reviewer(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(secret_store.getpass, "getuser", lambda: "benz")
    settings = AppSettings(tmp_path / "settings.json")
    settings.reviewer_name = "Alice"

    settings.reviewer_name = "   "  # blank/whitespace-only, as Settings might send on clear

    assert settings.reviewer_name == "benz"
    assert settings.reviewer_name != "AI Bridge Reviewer"


def test_ai_generated_proposal_attributes_the_human_requester_and_the_ai_author_separately(
    tmp_path: Path,
) -> None:
    _root, _project, runtime, _service, _finding, _human_proposal = _fixture(
        tmp_path, "before text", "before", "after",
    )
    repo = runtime.repository
    finding = _second_finding(runtime, finding_id="finding-ai-1")
    span = AffectedTargetSpan(
        displayed_reference="PHP 1:6", canonical_references=("PHP 1:6",),
        start_code_point=0, end_code_point=6, original_text="before",
        target_text_revision="rev", target_content_hash="hash",
    )
    ai_proposal = CorrectionProposalV2(
        id="proposal-ai-1", qa_finding_id=finding["id"], project_id=runtime.project_id,
        intent=CorrectionIntent(
            failed_dimension=CoverageDimension.LEXICAL_CONTENT,
            observed_meaning="missing", required_meaning="required",
            affected_source_semantic_unit_ids=("source-unit-php-1-3",),
            affected_target_span=span,
        ),
        affected_references=("PHP 1:6",), current_text="before", proposed_text="after",
        explanation="AI-suggested wording", evidence_ids=(), semantic_relationship_ids=(),
        meaning_assessment_ids=(), created_by="Reviewer", created_at="2026-09-11T00:00:00Z",
        creation_mode=CorrectionCreationMode.MACHINE_SUGGESTED,
        policy_binding=PolicyBinding.foundation_v1(),
        review_status=ReviewStatus.AI_PROPOSED, lifecycle_status=LifecycleStatus.ACTIVE,
        provider_metadata=CorrectionProviderMetadata(provider_name="openai", model="gpt-5.6"),
    )
    repo.save_correction_proposal_v2(ai_proposal)

    history = repo.correction_proposal_history("proposal-ai-1")
    created = next(e for e in history if e["eventType"] == "CREATED")
    suggested = next(e for e in history if e["eventType"] == "SUGGESTED")

    # The person who clicked "Suggest wording" is a HUMAN action...
    assert created["actorType"] == ActorType.HUMAN.value
    assert created["actorId"] == "Reviewer"
    # ...requesting AI-authored content, attributed to the provider, not the person.
    assert suggested["actorType"] == ActorType.AI.value
    assert suggested["actorId"] == "gpt-5.6"
    assert suggested["actorId"] != "Reviewer"


def test_renaming_the_reviewer_never_rewrites_already_persisted_history(tmp_path: Path) -> None:
    _root, _project, runtime, _service, _finding, proposal = _fixture(
        tmp_path, "before text", "before", "after",
    )
    settings = AppSettings(tmp_path / "settings.json")
    settings.reviewer_name = "Reviewer"

    history_before = runtime.repository.correction_proposal_history(proposal.id)
    created_before = next(e for e in history_before if e["eventType"] == "CREATED")
    assert created_before["actorId"] == "Reviewer"

    settings.reviewer_name = "Someone Else"  # the rename V11-002/005 is about

    history_after = runtime.repository.correction_proposal_history(proposal.id)
    created_after = next(e for e in history_after if e["eventType"] == "CREATED")
    assert created_after["actorId"] == "Reviewer"  # unchanged -- an audit trail, not a live label
    assert settings.reviewer_name == "Someone Else"
