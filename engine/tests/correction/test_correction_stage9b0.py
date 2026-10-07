"""Stage 9B.0 — schema/API design and dependency repair.

9B.0 builds the foundation a correction workflow will stand on and deliberately
stops there: no wording generation, no correction UI, no Scripture application,
no post-correction rerun. These tests therefore prove two things in equal
measure -- that the new eligibility/schema/dependency machinery is correct, and
that **nothing in this stage can change Scripture**.

The rule coverage uses a stub repository on purpose. Eligibility is a decision
table, and driving 12 branches through the full Stage 6B/7/8 pipeline would
test the pipeline rather than the rules. Dependency propagation, which is
exactly about how real records are wired, uses the real repository instead.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from tc_ai_bridge import passage_semantic_repository as repository_module
from tc_ai_bridge.correction_eligibility import (
    CorrectionEligibilityCode,
    CorrectionEligibilityService,
)
from tc_ai_bridge.passage_semantic_models import (
    ActorType,
    AffectedTargetSpan,
    CorrectionApplicationActor,
    CorrectionApplicationIntent,
    CorrectionApplicationState,
    CorrectionCreationMode,
    CorrectionIntent,
    CorrectionProposal,
    CorrectionProposalV2,
    CoverageDimension,
    LifecycleStatus,
    PolicyBinding,
    QaDisposition,
    ReviewStatus,
    VerificationStatus,
    to_wire,
)
from tc_ai_bridge.passage_semantic_repository import (
    DATABASE_SCHEMA_VERSION,
    RECORD_DEPENDENCY_ANCHOR_TYPES,
    RECORD_DEPENDENCY_TABLES,
    FoundationRepository,
)
from tests.support.correction_9b0 import TEXT, VERSE, _confirmed_finding, _service


# --- Stub runtime -----------------------------------------------------------


def _codes(result) -> set[str]:
    return {reason.code.value for reason in result.reasons}


# --- Eligibility rules ------------------------------------------------------

def test_confirmed_active_finding_is_eligible() -> None:
    service = _service({VERSE: TEXT})
    _confirmed_finding(service)
    result = service.evaluate("qa-1")
    assert result.eligible is True
    assert _codes(result) == {CorrectionEligibilityCode.ELIGIBLE.value}


@pytest.mark.parametrize("disposition", [
    QaDisposition.UNRESOLVED.value,
    QaDisposition.ACCEPTABLE_TRANSLATION.value,
    QaDisposition.FALSE_POSITIVE.value,
    QaDisposition.NEEDS_DISCUSSION.value,
])
def test_non_confirmed_dispositions_are_blocked(disposition: str) -> None:
    """Only a human confirming a real translation error authorizes a correction.

    ACCEPTABLE_TRANSLATION is the important one: the reviewer agreed something
    differs and judged the difference legitimate. Correcting it would overturn
    their decision.
    """
    service = _service({VERSE: TEXT})
    _confirmed_finding(service, qaDisposition=disposition)
    result = service.evaluate("qa-1")
    assert result.eligible is False
    assert CorrectionEligibilityCode.DISPOSITION_NOT_CONFIRMED.value in _codes(result)


def test_stale_finding_is_blocked() -> None:
    service = _service({VERSE: TEXT})
    _confirmed_finding(service, lifecycleStatus=LifecycleStatus.STALE.value)
    result = service.evaluate("qa-1")
    assert result.eligible is False
    assert CorrectionEligibilityCode.FINDING_STALE.value in _codes(result)


@pytest.mark.parametrize("outcome", ["AMBIGUOUS", "SEARCH_INCOMPLETE", "UNSUPPORTED_ANALYSIS"])
def test_inconclusive_location_outcomes_are_blocked(outcome: str) -> None:
    """These three mean the search never reached a conclusion, so no span is
    defensible to edit."""
    service = _service({VERSE: TEXT})
    service.repository.relationships["loc-1"] = {
        "id": "loc-1", "locationOutcome": outcome,
        "reviewStatus": ReviewStatus.AI_PROPOSED.value,
        "lifecycleStatus": LifecycleStatus.ACTIVE.value,
    }
    service.repository.assessments["ma-1"] = {
        "id": "ma-1", "semanticLocationRelationshipId": "loc-1",
        "meaningStatus": "MEANING_SHIFT", "reviewStatus": ReviewStatus.AI_PROPOSED.value,
        "lifecycleStatus": LifecycleStatus.ACTIVE.value,
    }
    _confirmed_finding(service, meaningAssessmentIds=["ma-1"])
    result = service.evaluate("qa-1")
    assert result.eligible is False
    assert CorrectionEligibilityCode.LOCATION_EVIDENCE_UNUSABLE.value in _codes(result)


def test_confirmed_not_located_omission_stays_eligible() -> None:
    """NOT_LOCATED is the omission case, and omissions are exactly what a
    correction should be able to fix by inserting text."""
    service = _service({VERSE: TEXT})
    service.repository.relationships["loc-1"] = {
        "id": "loc-1", "locationOutcome": "NOT_LOCATED",
        "reviewStatus": ReviewStatus.HUMAN_APPROVED.value,
        "lifecycleStatus": LifecycleStatus.ACTIVE.value,
    }
    service.repository.accounts["acct-1"] = {
        "id": "acct-1", "coveredByRelationshipIds": ["loc-1"],
    }
    _confirmed_finding(service, coverageAccountIds=["acct-1"])
    result = service.evaluate("qa-1")
    assert result.eligible is True, result.to_dict()


def test_human_rejected_mapping_is_blocked() -> None:
    service = _service({VERSE: TEXT})
    service.repository.relationships["loc-1"] = {
        "id": "loc-1", "locationOutcome": "LOCATED",
        "reviewStatus": ReviewStatus.HUMAN_REJECTED.value,
        "lifecycleStatus": LifecycleStatus.ACTIVE.value,
    }
    service.repository.assessments["ma-1"] = {
        "id": "ma-1", "semanticLocationRelationshipId": "loc-1",
        "meaningStatus": "MEANING_SHIFT", "reviewStatus": ReviewStatus.AI_PROPOSED.value,
        "lifecycleStatus": LifecycleStatus.ACTIVE.value,
    }
    _confirmed_finding(service, meaningAssessmentIds=["ma-1"])
    result = service.evaluate("qa-1")
    assert result.eligible is False
    assert CorrectionEligibilityCode.MAPPING_HUMAN_REJECTED.value in _codes(result)


def test_meaning_overridden_to_preserved_is_blocked() -> None:
    """The reviewer already said the meaning survives; there is nothing to fix."""
    service = _service({VERSE: TEXT})
    service.repository.assessments["ma-1"] = {
        "id": "ma-1", "semanticLocationRelationshipId": "",
        "meaningStatus": "PRESERVED", "reviewStatus": ReviewStatus.HUMAN_MODIFIED.value,
        "lifecycleStatus": LifecycleStatus.ACTIVE.value,
    }
    _confirmed_finding(service, meaningAssessmentIds=["ma-1"])
    result = service.evaluate("qa-1")
    assert result.eligible is False
    assert CorrectionEligibilityCode.MEANING_OVERRIDDEN_PRESERVED.value in _codes(result)


def test_unresolved_resource_conflict_is_blocked() -> None:
    service = _service({VERSE: TEXT})
    _confirmed_finding(service, resourceConflictEvidenceIds=["ev-1"])
    result = service.evaluate("qa-1")
    assert result.eligible is False
    assert CorrectionEligibilityCode.RESOURCE_CONFLICT_REQUIRES_REVIEW.value in _codes(result)


def test_meaning_failure_evidence_alone_is_not_a_resource_conflict() -> None:
    """The two concepts that once shared `conflictingEvidenceIds`.

    Evidence that the target meaning differs is the finding itself. Reading it
    as "the resources disagree" refused a correction to every meaning failure
    Bridge could emit; see the Stage 9B meaning-failure eligibility tests.
    """
    service = _service({VERSE: TEXT})
    _confirmed_finding(
        service,
        conflictingEvidenceIds=["meaning-evidence-1"],
        resourceConflictEvidenceIds=[],
    )
    result = service.evaluate("qa-1")
    assert result.eligible is True
    assert _codes(result) == {CorrectionEligibilityCode.ELIGIBLE.value}


def test_a_pre_split_finding_fails_closed_on_its_ambiguous_conflicting_ids() -> None:
    """No `resourceConflictEvidenceIds` key at all: a 0.9.3 record.

    Nothing on it says whether those ids are meaning evidence or a real
    resource disagreement, so it blocks until re-analysis rewrites it.
    """
    service = _service({VERSE: TEXT})
    finding = _confirmed_finding(service, conflictingEvidenceIds=["ev-1"])
    assert "resourceConflictEvidenceIds" not in finding
    result = service.evaluate("qa-1")
    assert result.eligible is False
    assert CorrectionEligibilityCode.RESOURCE_CONFLICT_REQUIRES_REVIEW.value in _codes(result)


def test_conflicting_active_correction_is_blocked() -> None:
    service = _service({VERSE: TEXT})
    _confirmed_finding(service)
    service.repository.proposals["qa-1"] = [
        {"id": "prop-1", "lifecycleStatus": LifecycleStatus.ACTIVE.value},
    ]
    result = service.evaluate("qa-1")
    assert result.eligible is False
    assert CorrectionEligibilityCode.CONFLICTING_CORRECTION.value in _codes(result)
    assert result.existing_proposal_ids == ("prop-1",)


def test_current_chapter_hash_mismatch_blocks_eligibility() -> None:
    """Stage 9A only compared against the finding's own snapshot. Eligibility
    re-reads Scripture, so an edit made after the finding was produced is caught."""
    service = _service({VERSE: TEXT + " EDITED"})
    _confirmed_finding(service)  # hashes recorded against the pre-edit text
    result = service.evaluate("qa-1")
    assert result.eligible is False
    assert CorrectionEligibilityCode.TARGET_TEXT_CHANGED.value in _codes(result)


def test_missing_target_reference_blocks_eligibility() -> None:
    service = _service({})
    _confirmed_finding(service)
    result = service.evaluate("qa-1")
    assert result.eligible is False
    assert CorrectionEligibilityCode.TARGET_REFERENCE_MISSING.value in _codes(result)


def test_every_blocker_is_reported_at_once() -> None:
    """A reviewer clearing one blocker only to meet the next is a bad loop."""
    service = _service({VERSE: TEXT + " EDITED"})
    _confirmed_finding(
        service, qaDisposition=QaDisposition.NEEDS_DISCUSSION.value,
        reviewStatus=ReviewStatus.NEEDS_DISCUSSION.value,
    )
    codes = _codes(service.evaluate("qa-1"))
    assert {
        CorrectionEligibilityCode.DISPOSITION_NOT_CONFIRMED.value,
        CorrectionEligibilityCode.REVIEW_STATUS_NOT_HUMAN_APPROVED.value,
        CorrectionEligibilityCode.TARGET_TEXT_CHANGED.value,
    } <= codes


# --- Current-text / span validation -----------------------------------------

def test_exact_span_text_mismatch_blocks() -> None:
    service = _service({VERSE: TEXT})
    validation = service.validate_current_text(
        displayed_reference=VERSE, start_code_point=0, end_code_point=4,
        expected_span_text="ZZZZ",
    )
    assert validation.valid is False
    assert validation.reasons[0].code is CorrectionEligibilityCode.SPAN_TEXT_MISMATCH
    assert validation.observed_span_text == TEXT[:4]


def test_span_outside_the_verse_is_rejected() -> None:
    service = _service({VERSE: TEXT})
    validation = service.validate_current_text(
        displayed_reference=VERSE, start_code_point=0, end_code_point=len(TEXT) + 5,
    )
    assert validation.valid is False
    assert validation.reasons[0].code is CorrectionEligibilityCode.SPAN_TEXT_MISMATCH


def test_matching_span_and_hash_validate() -> None:
    service = _service({VERSE: TEXT})
    expected_hash = service.runtime.text_hash(TEXT)
    validation = service.validate_current_text(
        displayed_reference=VERSE, expected_target_content_hash=expected_hash,
        start_code_point=5, end_code_point=12, expected_span_text=TEXT[5:12],
    )
    assert validation.valid is True
    assert validation.current_target_content_hash == expected_hash


# --- Exact span contract (Unicode) ------------------------------------------

@pytest.mark.parametrize("sample,description", [
    ("தேவனை ஸ்தோத்திரிக்கிறேன்", "Tamil combining vowel signs"),
    ("בְּרֵאשִׁ֖ית בָּרָ֣א", "Hebrew points and cantillation marks"),
    ("ἐν ἀρχῇ ἦν ὁ λόγος", "Greek diacritics"),
    ("𐤀𐤁𐤂 ancient text 𝕭𝖔𝖑𝖉", "supplementary-plane characters"),
])
def test_code_point_spans_are_exact_across_scripts(sample: str, description: str) -> None:
    """Offsets are Python/code-point indices, never UTF-16 units or graphemes.

    The supplementary case is the one that silently breaks a JavaScript caller:
    those characters are one code point here and two UTF-16 units there.
    """
    reference = "GEN 1:1"
    service = _service({reference: sample})
    for start in range(len(sample)):
        for end in (start, start + 1, len(sample)):
            validation = service.validate_current_text(
                displayed_reference=reference, start_code_point=start,
                end_code_point=end, expected_span_text=sample[start:end],
            )
            assert validation.valid is True, f"{description}: [{start},{end})"
    # A UTF-16 length would over-run the string for supplementary characters.
    assert len(sample) == len(list(sample))


def test_zero_length_insertion_span_is_valid_for_omission() -> None:
    """A genuine omission is repaired by inserting at [n, n) -- nothing replaced."""
    span = AffectedTargetSpan(
        displayed_reference=VERSE, canonical_references=(VERSE,),
        start_code_point=7, end_code_point=7, original_text="",
        target_text_revision="rev-1", target_content_hash="hash-1",
    )
    assert span.is_insertion is True
    service = _service({VERSE: TEXT})
    validation = service.validate_current_text(
        displayed_reference=VERSE, start_code_point=7, end_code_point=7,
        expected_span_text="",
    )
    assert validation.valid is True
    assert validation.observed_span_text == ""


def test_span_rejects_incoherent_offsets() -> None:
    with pytest.raises(ValueError, match="precede its start"):
        AffectedTargetSpan(
            displayed_reference=VERSE, canonical_references=(VERSE,),
            start_code_point=9, end_code_point=4, original_text="",
            target_text_revision="r", target_content_hash="h",
        )
    with pytest.raises(ValueError, match="length"):
        AffectedTargetSpan(
            displayed_reference=VERSE, canonical_references=(VERSE,),
            start_code_point=0, end_code_point=5, original_text="ab",
            target_text_revision="r", target_content_hash="h",
        )


# --- Schema v12 / legacy proposals ------------------------------------------

def _v2_proposal(proposal_id: str = "prop-v2", finding_id: str = "qa-1") -> CorrectionProposalV2:
    span = AffectedTargetSpan(
        displayed_reference=VERSE, canonical_references=(VERSE,),
        start_code_point=0, end_code_point=4, original_text=TEXT[:4],
        target_text_revision="rev-1", target_content_hash="hash-1",
    )
    return CorrectionProposalV2(
        id=proposal_id, qa_finding_id=finding_id, project_id="project-1",
        intent=CorrectionIntent(
            failed_dimension=CoverageDimension.QUANTITY,
            observed_meaning="target says 'some'", required_meaning="source requires 'all'",
            affected_source_semantic_unit_ids=(), affected_target_span=span,
        ),
        affected_references=(VERSE,), current_text=TEXT[:4], proposed_text="",
        explanation="Quantity mismatch.", evidence_ids=(),
        semantic_relationship_ids=(), meaning_assessment_ids=(),
        created_by="human", created_at="2026-09-04T00:00:00Z",
        creation_mode=CorrectionCreationMode.HUMAN_AUTHORED,
        policy_binding=PolicyBinding.foundation_v1(),
        review_status=ReviewStatus.AI_PROPOSED, lifecycle_status=LifecycleStatus.INACTIVE,
    )


def test_fresh_database_is_at_current_schema(tmp_path: Path) -> None:
    repo = FoundationRepository(tmp_path / "semantic.sqlite3")
    assert repo.schema_version() == DATABASE_SCHEMA_VERSION == 16


def test_v10_to_current_migration_preserves_legacy_proposals(tmp_path: Path) -> None:
    """A real v10 database with a v1 proposal upgrades, keeps its history, and
    does not silently acquire a span it never had."""
    database = tmp_path / "semantic.sqlite3"
    conn = sqlite3.connect(database)
    for version in range(1, 11):
        conn.executescript(getattr(repository_module, f"_MIGRATION_V{version}"))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations("
            "version INTEGER PRIMARY KEY, schema_id TEXT NOT NULL, applied_at TEXT NOT NULL)")
        conn.execute("INSERT INTO schema_migrations VALUES(?,?,?)",
                     (version, repository_module.SCHEMA_ID, "2026-09-02T00:00:00Z"))
    conn.execute("INSERT INTO policy_bindings VALUES('pb','c-v1','cal-v1','audit-v1')")
    conn.execute(
        "INSERT INTO qa_findings(id,project_id,qa_disposition,policy_binding_id,review_status,"
        "lifecycle_status,revision,payload_json,book,kind,direction,severity,severity_rank,"
        "sort_chapter,sort_verse,displayed_reference) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("qa-legacy", "project-1", "CONFIRMED_TRANSLATION_ERROR", "pb", "HUMAN_APPROVED",
         "ACTIVE", 1, json.dumps({"id": "qa-legacy", "book": "PHP"}),
         "PHP", "QUANTITY_PROBLEM", "SOURCE_COVERAGE", "HIGH", 1, 1, 3, "PHP 1:3"),
    )
    legacy_payload = {
        "id": "prop-legacy", "qaFindingId": "qa-legacy", "currentText": "old",
        "proposedText": "new", "explanation": "legacy history worth keeping",
    }
    conn.execute(
        "INSERT INTO correction_proposals VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("prop-legacy", "project-1", "qa-legacy", "rev-1", None, "pb",
         "AI_PROPOSED", "ACTIVE", 1, json.dumps(legacy_payload)),
    )
    conn.commit()
    conn.close()

    upgraded = FoundationRepository(database)
    assert upgraded.schema_version() == 16
    assert (tmp_path / "backups").is_dir(), "migration must back up before upgrading"
    assert upgraded.recovery_check()["ok"] is True

    stored = upgraded.correction_proposal("prop-legacy")
    assert stored["explanation"] == "legacy history worth keeping"
    assert stored["proposalSchemaVersion"] == 1
    assert stored["verificationStatus"] == VerificationStatus.NOT_RUN.value

    listed = upgraded.correction_proposals_for_finding("qa-legacy")
    assert [item["id"] for item in listed] == ["prop-legacy"]
    # The whole point: a legacy row has no exact span or content hash, so it
    # must never become applicable by migration alone.
    assert listed[0]["applicable"] is False
    migration_history = upgraded.correction_proposal_history("prop-legacy")
    assert [event["eventType"] for event in migration_history] == ["CREATED"]
    assert migration_history[0]["actorType"] == "MIGRATION"


def test_legacy_v1_proposal_written_today_is_still_not_applicable(tmp_path: Path) -> None:
    repo = FoundationRepository(tmp_path / "semantic.sqlite3")
    repo.create_qa_finding("qa-1", "project-1")
    repo.save_correction_proposal(CorrectionProposal.example("prop-v1", "qa-1"))
    listed = repo.correction_proposals_for_finding("qa-1")
    assert listed[0]["proposalSchemaVersion"] == 1
    assert listed[0]["applicable"] is False


def test_v2_proposal_is_applicable_and_round_trips(tmp_path: Path) -> None:
    repo = FoundationRepository(tmp_path / "semantic.sqlite3")
    repo.create_qa_finding("qa-1", "project-1")
    proposal = _v2_proposal()
    assert proposal.is_applicable is True
    repo.save_correction_proposal_v2(proposal)
    listed = repo.correction_proposals_for_finding("qa-1")
    assert listed[0]["proposalSchemaVersion"] == 2
    assert listed[0]["applicable"] is True
    assert listed[0]["intent"]["affectedTargetSpan"]["startCodePoint"] == 0
    assert listed[0]["verificationStatus"] == VerificationStatus.NOT_RUN.value


# --- Verification model -----------------------------------------------------

def test_applying_a_proposal_is_not_a_corrected_finding(tmp_path: Path) -> None:
    """The central Stage 9B invariant: applied != corrected."""
    repo = FoundationRepository(tmp_path / "semantic.sqlite3")
    repo.create_qa_finding("qa-1", "project-1")
    repo.update_qa_disposition(
        "qa-1", QaDisposition.CONFIRMED_TRANSLATION_ERROR, 1, "Reviewer")
    repo.save_correction_proposal_v2(_v2_proposal())

    repo.record_correction_application_metadata(
        "prop-v2", actor_type="HUMAN", applied_target_revision="rev-2", expected_revision=1)

    proposal = repo.correction_proposal("prop-v2")
    finding = repo.qa_finding("qa-1")
    assert proposal["appliedTargetRevision"] == "rev-2"
    # Applied, and therefore awaiting verification -- not passed, not corrected.
    assert proposal["verificationStatus"] == VerificationStatus.PENDING.value
    assert finding["qaDisposition"] == QaDisposition.CONFIRMED_TRANSLATION_ERROR.value
    assert finding["qaDisposition"] != QaDisposition.CORRECTED.value
    # The finding is historical while re-analysis is pending.
    assert finding["lifecycleStatus"] == LifecycleStatus.STALE.value


def test_deprecated_record_correction_applied_cannot_mark_corrected(tmp_path: Path) -> None:
    """The Stage 9A name still works, but no longer mints a CORRECTED verdict."""
    repo = FoundationRepository(tmp_path / "semantic.sqlite3")
    repo.create_qa_finding("qa-1", "project-1")
    repo.update_qa_disposition(
        "qa-1", QaDisposition.CONFIRMED_TRANSLATION_ERROR, 1, "Reviewer")
    repo.save_correction_proposal(CorrectionProposal.example("prop-v1", "qa-1"))
    repo.record_correction_applied(
        "prop-v1", actor_type="HUMAN", applied_target_revision="rev-2", expected_revision=1)
    assert repo.qa_finding("qa-1")["qaDisposition"] != QaDisposition.CORRECTED.value


def test_verification_passed_alone_does_not_produce_corrected(tmp_path: Path) -> None:
    """PASSED is evidence, not acknowledgement. Nothing in 9B.0 may bridge the gap."""
    repo = FoundationRepository(tmp_path / "semantic.sqlite3")
    repo.create_qa_finding("qa-1", "project-1")
    repo.update_qa_disposition(
        "qa-1", QaDisposition.CONFIRMED_TRANSLATION_ERROR, 1, "Reviewer")
    proposal = _v2_proposal()
    repo.save_correction_proposal_v2(proposal)
    repo.record_correction_application_metadata(
        "prop-v2", actor_type="HUMAN", applied_target_revision="rev-2", expected_revision=1)

    with sqlite3.connect(tmp_path / "semantic.sqlite3") as conn:
        conn.execute(
            "UPDATE correction_proposals SET verification_status=? WHERE id=?",
            (VerificationStatus.PASSED.value, "prop-v2"))
        conn.commit()

    assert repo.qa_finding("qa-1")["qaDisposition"] != QaDisposition.CORRECTED.value


def test_verification_status_is_independent_of_qa_disposition() -> None:
    assert not (set(VerificationStatus) & set(QaDisposition))
    assert {item.value for item in VerificationStatus} == {
        "NOT_RUN", "PENDING", "PASSED", "FAILED", "UNCERTAIN"}


# --- Application transaction model (design only) ----------------------------

def test_application_intent_states_cover_the_recovery_window() -> None:
    """APPLIED_SCRIPTURE must stay distinct from COMPLETED: the gap between
    'Scripture written' and 'bookkeeping done' is exactly where a crash needs
    recovery, and collapsing them makes that unrepresentable."""
    assert {item.value for item in CorrectionApplicationState} == {
        "PREPARED", "APPLYING", "APPLIED_SCRIPTURE", "INVALIDATED",
        "COMPLETED", "FAILED", "RECOVERY_REQUIRED"}


def test_application_intent_is_separate_from_the_proposal(tmp_path: Path) -> None:
    before_hash = hashlib.sha256(b"before").hexdigest()
    after_hash = hashlib.sha256(b"after").hexdigest()
    intent = CorrectionApplicationIntent(
        application_id="app-1", proposal_id="prop-v2", project_id="project-1",
        finding_id="qa-1", expected_proposal_revision=1,
        expected_finding_revision=2, target_displayed_reference=VERSE,
        canonical_references=(VERSE,), source_provenance_references=(VERSE,),
        expected_target_revision="rev-1", expected_target_content_hash=before_hash,
        expected_start_code_point=0, expected_end_code_point=4,
        expected_original_text=TEXT[:4], replacement_text_snapshot="replacement",
        intended_final_verse_hash=after_hash, pending_invalidation_id="pending-1",
        translation_core_journal_transaction_id="",
        actor=CorrectionApplicationActor(actor_type=ActorType.HUMAN, actor_id="reviewer"),
        created_at="2026-09-04T00:00:00Z", updated_at="2026-09-04T00:00:00Z",
        application_state=CorrectionApplicationState.PREPARED, state_revision=1,
    )
    wire = to_wire(intent)
    assert wire["applicationState"] == "PREPARED"
    assert wire["expectedTargetContentHash"] == before_hash
    # Every precondition an apply must re-check lives on the intent, and none
    # of them is stored on the proposal -- that separation is the design.
    preconditions = {
        "expectedProposalRevision", "expectedFindingRevision", "expectedTargetRevision",
        "expectedTargetContentHash", "expectedStartCodePoint", "expectedEndCodePoint",
        "expectedOriginalText",
    }
    assert preconditions <= set(wire)
    assert not (preconditions & set(to_wire(_v2_proposal())))

    repo = FoundationRepository(tmp_path / "semantic.sqlite3")
    with sqlite3.connect(tmp_path / "semantic.sqlite3") as conn:
        columns = {row[1] for row in conn.execute(
            "PRAGMA table_info(correction_application_intents)")}
    assert {"application_id", "proposal_id", "expected_target_revision",
            "expected_original_text", "application_state", "state_revision"} <= columns
    assert repo.schema_version() == 16


# --- Dependency graph invariants --------------------------------------------

def test_every_writable_dependency_type_is_registered() -> None:
    """The QA_RUN drift bug, made impossible to repeat.

    Stage 8 wrote QA_RUN dependency edges but taught only the propagation map
    about them, so recovery reported every audited project as corrupt. Any type
    that can be written must be known to the propagation map, the recovery
    check, and the table mapping -- which are now one constant.
    """
    written = _dependency_types_written_by_source()
    known = set(RECORD_DEPENDENCY_TABLES) | set(RECORD_DEPENDENCY_ANCHOR_TYPES)
    assert written <= known, f"unregistered dependency types: {sorted(written - known)}"


def _dependency_types_written_by_source() -> set[str]:
    """Every literal used as a record/depends-on type across the writing modules.

    Every package module is considered, not just the repository:
    SOURCE_RESOURCE edges are written from the runtime, and a repository-only
    scan missed them -- which is the very drift this test exists to catch.
    """
    import re

    types: set[str] = set()
    package = Path(repository_module.__file__).parent
    scanned: list[Path] = []
    for path in package.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "record_dependencies" not in source and "add_record_dependency" not in source:
            continue
        scanned.append(path)
        pattern = r'\(\s*"([A-Z_]+)"\s*,\s*[^,\n]+,\s*\n?\s*"([A-Z_]+)"\s*,'
        for match in re.finditer(pattern, source):
            types.add(match.group(1))
            types.add(match.group(2))
    assert scanned, "no dependency-writing modules were scanned"
    assert types, "source scan found no dependency edges; the pattern has drifted"
    assert "SOURCE_RESOURCE" in types, (
        "the scan no longer sees runtime-written edges; widen it before trusting it"
    )
    return types


def test_dependency_tables_all_exist_and_are_stale_propagatable(tmp_path: Path) -> None:
    """A type mapped to a table that cannot carry STALE propagates nothing."""
    FoundationRepository(tmp_path / "semantic.sqlite3")
    with sqlite3.connect(tmp_path / "semantic.sqlite3") as conn:
        for record_type, table in RECORD_DEPENDENCY_TABLES.items():
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            assert columns, f"{record_type} maps to missing table {table}"
            assert {"id", "lifecycle_status", "payload_json"} <= columns, (
                f"{record_type} -> {table} cannot carry stale propagation"
            )


def test_correction_proposal_registers_its_dependency_edges(tmp_path: Path) -> None:
    repo = FoundationRepository(tmp_path / "semantic.sqlite3")
    repo.create_qa_finding("qa-1", "project-1")
    repo.save_correction_proposal_v2(_v2_proposal())
    with sqlite3.connect(tmp_path / "semantic.sqlite3") as conn:
        edges = {
            (row[2], row[3]) for row in conn.execute(
                "SELECT * FROM record_dependencies WHERE record_type='CORRECTION_PROPOSAL' "
                "AND record_id='prop-v2'")
        }
    assert ("QA_FINDING", "qa-1") in edges
    assert any(kind == "TARGET_REFERENCE" for kind, _ in edges)
    assert repo.recovery_check()["ok"] is True


def test_staling_the_finding_stales_its_correction_proposal(tmp_path: Path) -> None:
    """A correction must not outlive the finding that justifies it."""
    repo = FoundationRepository(tmp_path / "semantic.sqlite3")
    repo.create_qa_finding("qa-1", "project-1")
    repo.save_correction_proposal_v2(_v2_proposal())
    with sqlite3.connect(tmp_path / "semantic.sqlite3") as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        FoundationRepository._stale_generic_dependencies(conn, "QA_FINDING", "qa-1")
        conn.commit()
    assert repo.correction_proposal("prop-v2")["lifecycleStatus"] == LifecycleStatus.STALE.value


# --- Safety boundary: Stage 9B.0 must not alter Scripture -------------------

def _stage9a_fixtures():
    """Reuse the proven Stage 9A pipeline fixture builders from tests.review."""
    from tests.review import test_qa_review_service_stage9a as module

    return module


def _scripture_digest(root: Path) -> dict[str, str]:
    """Hash every artifact Stage 9B.0 is forbidden to touch."""
    digest: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        is_chapter_json = relative.startswith("php/") and path.suffix == ".json"
        is_usfm = path.suffix.lower() in {".usfm", ".sfm"}
        is_alignment = "alignmentData" in relative
        if is_chapter_json or is_usfm or is_alignment:
            digest[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest


@pytest.fixture()
def tamil_runtime(tmp_path: Path):
    """The reordered IRV Tamil PHP 1:3-6 passage -- the Section 13 cross-verse case."""
    fixtures = _stage9a_fixtures()
    runtime = fixtures._project(tmp_path, {"1": fixtures.TAMIL_PHP}, "ta")
    fixtures._run(
        runtime, fixtures._FixtureEmbeddings(fixtures._paired(fixtures.PHP_PAIRS)),
        "1", "3", "1", "6")
    return runtime


def test_stage_9b0_never_alters_scripture(tamil_runtime) -> None:
    """Chapter JSON, preserved imported USFM and alignment data must be
    byte-identical after everything this stage can do."""
    root = Path(tamil_runtime.project.path)
    before = _scripture_digest(root)
    assert before, "fixture produced no Scripture artifacts to guard"

    service = CorrectionEligibilityService(tamil_runtime)
    for summary in tamil_runtime.qa_review.get_queue(limit=200)["findings"]:
        result = service.evaluate(summary["id"])
        assert isinstance(result.eligible, bool)
        if summary.get("displayedReferences"):
            service.validate_current_text(
                displayed_reference=summary["displayedReferences"][0],
                start_code_point=0, end_code_point=1,
            )
    finding_id = tamil_runtime.qa_review.get_queue(limit=1)["findings"][0]["id"]
    tamil_runtime.repository.save_correction_proposal_v2(
        _v2_proposal("prop-guard", finding_id))
    tamil_runtime.correction_get_eligibility(finding_id)
    tamil_runtime.correction_list_for_finding(finding_id)

    assert _scripture_digest(root) == before


def test_correction_modules_contain_no_scripture_writer() -> None:
    """Section 10's contract, enforced rather than merely documented.

    Any future application must route through BridgeEngine.edit_verse() ->
    TranslationCoreProject.apply_scripture_edit(), which already owns the
    tC-compatible write, alignment reconciliation, tN/tW invalidation and
    transactional rollback. A second Scripture mutation path is exactly the
    duplication this stage exists to prevent.
    """
    from tc_ai_bridge import correction_eligibility

    source = Path(correction_eligibility.__file__).read_text(encoding="utf-8")
    for forbidden in ("write_text", "apply_scripture_edit", "json.dump"):
        assert forbidden not in source, f"correction_eligibility must not use {forbidden}"


def test_apply_scripture_edit_still_lacks_the_strict_preconditions() -> None:
    """Records the minimum strict-mode additions 9B.3 must add before applying.

    apply_scripture_edit() reads the current text and writes; it takes no
    expected revision, no expected content hash and no expected old span, so it
    cannot yet fail closed on a concurrent edit. This test pins that gap and
    will fail once the parameters land -- at which point 9B.3 updates it.
    """
    import inspect

    from tc_ai_bridge.tc_project import TranslationCoreProject

    parameters = set(inspect.signature(TranslationCoreProject.apply_scripture_edit).parameters)
    required_later = {
        "expected_target_revision", "expected_target_content_hash", "expected_old_text",
    }
    assert not (required_later & parameters), (
        "apply_scripture_edit gained strict-mode parameters; update Stage 9B.3 "
        "readiness expectations and the correction application design."
    )


# --- Dependency propagation over real records -------------------------------

def _located_finding(runtime) -> dict:
    for summary in runtime.qa_review.get_queue(limit=200)["findings"]:
        detail = runtime.qa_review.get_finding(summary["id"])
        if detail["meaning"] and detail["location"]:
            return detail
    pytest.skip("fixture produced no located, assessed finding")


def test_rejecting_a_mapping_blocks_the_dependent_finding(tamil_runtime) -> None:
    """A human rejecting a location mapping must reach the QA finding built on it."""
    detail = _located_finding(tamil_runtime)
    finding_id = detail["finding"]["id"]
    relationship = detail["location"][0]["location"]

    tamil_runtime.qa_review.decide_location(
        relationship["id"], "REJECT",
        expected_revision=int(relationship["revision"]), note="Wrong mapping.")

    service = CorrectionEligibilityService(tamil_runtime)
    result = service.evaluate(finding_id)
    codes = {reason.code.value for reason in result.reasons}
    assert result.eligible is False
    assert codes & {
        CorrectionEligibilityCode.MAPPING_HUMAN_REJECTED.value,
        CorrectionEligibilityCode.FINDING_STALE.value,
    }, codes


def test_overriding_meaning_to_preserved_blocks_correction(tamil_runtime) -> None:
    detail = _located_finding(tamil_runtime)
    finding_id = detail["finding"]["id"]
    assessment = detail["meaning"][0]["assessment"]

    tamil_runtime.qa_review.decide_meaning(
        assessment["id"], "PRESERVED",
        expected_revision=int(assessment["revision"]), note="Reads correctly.")

    service = CorrectionEligibilityService(tamil_runtime)
    result = service.evaluate(finding_id)
    codes = {reason.code.value for reason in result.reasons}
    assert result.eligible is False
    assert codes & {
        CorrectionEligibilityCode.MEANING_OVERRIDDEN_PRESERVED.value,
        CorrectionEligibilityCode.FINDING_STALE.value,
    }, codes


def test_php_cross_verse_case_reaches_a_structured_answer(tamil_runtime) -> None:
    """Section 13's PHP 1:3-6 reordered/cross-verse case must reach eligibility
    with a real structured answer rather than an error."""
    findings = tamil_runtime.qa_review.get_queue(limit=200)["findings"]
    assert findings
    service = CorrectionEligibilityService(tamil_runtime)
    for summary in findings:
        result = service.evaluate(summary["id"])
        assert result.reasons, "every answer must carry a structured reason"


# --- Regression fixture shapes ----------------------------------------------

@pytest.mark.parametrize("dimension,observed,required", [
    (CoverageDimension.QUANTITY, "some", "all"),
    (CoverageDimension.POLARITY, "positive", "negative"),
    (CoverageDimension.POLARITY, "negative", "positive"),
    (CoverageDimension.TEMPORAL_ASPECTUAL, "before", "after"),
    (CoverageDimension.QUANTITY, "three", "four"),
    (CoverageDimension.PARTICIPANT, "Paul greets Timothy", "Timothy greets Paul"),
    (CoverageDimension.LEXICAL_CONTENT, "an unsupported modifier is present", "no modifier"),
    (CoverageDimension.LEXICAL_CONTENT, "nothing rendered", "the source meaning rendered"),
    (CoverageDimension.REFERENT, "wrong referent", "the source referent"),
])
def test_intent_represents_every_regression_shape(
    dimension: CoverageDimension, observed: str, required: str,
) -> None:
    """The schema must express each Section 13 shape without wording generation.

    proposed_text stays empty throughout: 9B.0 records what a correction must
    achieve, and 9B.1 is what decides how to say it.
    """
    span = AffectedTargetSpan(
        displayed_reference=VERSE, canonical_references=(VERSE,),
        start_code_point=0, end_code_point=0, original_text="",
        target_text_revision="rev-1", target_content_hash="hash-1",
    )
    intent = CorrectionIntent(
        failed_dimension=dimension, observed_meaning=observed, required_meaning=required,
        affected_source_semantic_unit_ids=("unit-1",), affected_target_span=span,
    )
    wire = to_wire(intent)
    assert wire["failedDimension"] == dimension.value
    assert wire["observedMeaning"] == observed
    assert wire["requiredMeaning"] == required
    assert wire["affectedTargetSpan"]["startCodePoint"] == 0
