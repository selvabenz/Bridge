"""Stage 9B correction eligibility for *meaning-failure* findings.

A meaning failure and a resource conflict are different things, and Stage 7 and
Stage 9B once shared one field for both.  Stage 7 filed every ALTERED /
CONTRADICTED / PARTIALLY_PRESERVED / TARGET_ADDS_SPECIFICITY /
TARGET_WEAKENS_SPECIFICITY component under ``conflictingEvidenceIds`` -- the
evidence *that the translation is wrong* -- and
``CorrectionEligibilityService._check_resource_conflicts`` read that same field
as "the resources disagree, a human must choose first".  So every meaning
failure the pipeline could emit was refused a correction with
RESOURCE_CONFLICT_REQUIRES_REVIEW, which is the one blocker a confirmed
translation error must never raise.

These tests run the real Stage 5 -> 6A -> 6B -> 7 -> 8 pipeline and the real
review, eligibility and correction services.  Nothing here patches a finding
into shape first, except where a test says so in as many words -- the legacy
and resource-conflict sections, which have to manufacture states the current
writer no longer produces.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from tc_ai_bridge.correction_affected_analysis import CorrectionAffectedScopeResolver
from tc_ai_bridge.correction_application import CorrectionApplicationService
from tc_ai_bridge.correction_eligibility import CorrectionEligibilityCode
from tc_ai_bridge.meaning_analysis import (
    MeaningAnalysisEngine,
    MeaningPolicy,
    resource_conflict_evidence_ids,
)
from tc_ai_bridge.passage_semantic_models import (
    AffectedTargetSpan,
    CorrectionIntent,
    CoverageDimension,
    MeaningAssessmentReason,
)
from tc_ai_bridge.passage_semantic_runtime import PassageSemanticRuntime
from tc_ai_bridge.qa_audit import QaAuditEngine
from tc_ai_bridge.semantic_location import SemanticLocationEngine

from tests.support.correction_9b0 import TEXT as TAMIL_TEXT, VERSE, _confirmed_finding, _service
from tests.support.qa_target_hash import (
    CROSS_VERSE_PAIRS,
    ENGLISH_PHP,
    SOURCE_REFERENCE,
    TARGET_REFERENCE,
    _FixtureEmbeddings,
    _analyze,
    _codes,
    _confirm,
    _of_kind,
    _paired,
    _project,
)


# The five component statuses that mean "the target meaning differs".  None of
# them is a resource disagreement, and none may block a correction by itself.
MEANING_FAILURE_STATUSES = [
    "ALTERED",
    "CONTRADICTED",
    "PARTIALLY_PRESERVED",
    "TARGET_ADDS_SPECIFICITY",
    "TARGET_WEAKENS_SPECIFICITY",
]

# The dimensions a meaning failure can be reported in.
MEANING_DIMENSIONS = [
    "LEXICAL_CONTENT", "POLARITY", "QUANTITY",
    "PARTICIPANT", "REFERENT", "TEMPORAL_ASPECTUAL",
]

_RESOURCE_CONFLICT = CorrectionEligibilityCode.RESOURCE_CONFLICT_REQUIRES_REVIEW.value


# --- Fixtures the real pipeline emits meaning failures from -----------------

@pytest.fixture()
def cross_verse(tmp_path: Path) -> tuple[PassageSemanticRuntime, dict]:
    """Source semantics at PHP 1:3, target realization at PHP 1:6."""
    runtime = _project(tmp_path, ENGLISH_PHP)
    audit, _ = _analyze(
        runtime, _FixtureEmbeddings(_paired(CROSS_VERSE_PAIRS)), "1", "3", "1", "6")
    return runtime, audit


def _meaning_findings(audit: dict) -> list[dict]:
    """Everything the audit emitted that is a meaning failure, not coverage."""
    return [
        finding for finding in audit["findings"]
        if finding["kind"] not in {"POSSIBLE_OMISSION", "POSSIBLE_ADDITION"}
    ]


def _components(runtime: PassageSemanticRuntime, finding: dict) -> list[dict]:
    return [
        component
        for assessment_id in finding["meaningAssessmentIds"]
        for component in runtime.repository.meaning_assessment(
            assessment_id)["componentAssessments"]
    ]


def _component_statuses(runtime: PassageSemanticRuntime, finding: dict) -> set[str]:
    return {component["status"] for component in _components(runtime, finding)}


def _component_dimensions(runtime: PassageSemanticRuntime, finding: dict) -> set[str]:
    return {component["coverageDimension"] for component in _components(runtime, finding)}


# --- The production gate ----------------------------------------------------

def test_naturally_emitted_meaning_failure_reaches_correction_eligibility(
    cross_verse: tuple[PassageSemanticRuntime, dict],
) -> None:
    """The gate this change exists for.

    A POSSIBLE_OVERTRANSLATION the real Stage 5-8 pipeline emitted, confirmed
    through the ordinary review service, with no resource disagreement anywhere
    near it.  Before the fix its only blocker was the one that should never
    apply to a confirmed translation error.
    """
    runtime, audit = cross_verse
    finding = _of_kind(audit, "POSSIBLE_OVERTRANSLATION")

    # The meaning-failure evidence is present and is *not* filed as a conflict.
    assert finding["conflictingEvidenceIds"], "Stage 7 recorded no meaning evidence"
    assert finding["resourceConflictEvidenceIds"] == []
    assert _component_statuses(runtime, finding) == {"TARGET_ADDS_SPECIFICITY"}

    _confirm(runtime, finding)
    eligibility = runtime.correction_eligibility.evaluate(finding["id"])

    assert _RESOURCE_CONFLICT not in _codes(eligibility)
    assert _codes(eligibility) == {CorrectionEligibilityCode.ELIGIBLE.value}
    assert eligibility.eligible is True


def test_confirmed_meaning_failure_can_have_a_correction_proposed(
    cross_verse: tuple[PassageSemanticRuntime, dict],
) -> None:
    """Reachable, not merely unblocked: creation re-evaluates eligibility."""
    runtime, audit = cross_verse
    finding = _of_kind(audit, "POSSIBLE_OVERTRANSLATION")
    _confirm(runtime, finding)

    proposal = _propose(runtime, finding)

    assert proposal["qaFindingId"] == finding["id"]
    # The UI's mayApply is (proposal ACTIVE and unblocked) and (human-reviewed)
    # and (verification NOT_RUN).  The backend owns all three inputs.  "Unblocked"
    # discounts the CONFLICTING_CORRECTION the proposal itself raises -- the
    # panel filters exactly that reason out for a proposal it already has.
    eligibility = runtime.correction_get_eligibility(finding["id"])
    assert [item["code"] for item in eligibility["reasons"]] == ["CONFLICTING_CORRECTION"]
    assert eligibility["reasons"][0]["entityId"] == proposal["id"]
    assert runtime.correction_eligibility.evaluate(
        finding["id"], ignore_proposal_ids=(proposal["id"],)).eligible is True
    assert proposal["lifecycleStatus"] == "ACTIVE"
    assert proposal["verificationStatus"] == "NOT_RUN"

    reviewed = runtime.correction_edit_proposal(
        proposal["id"], proposed_text="constant",
        expected_revision=proposal["revision"], actor_id="Reviewer",
        explanation="Reviewer wording.",
    )
    assert reviewed["reviewStatus"] == "HUMAN_MODIFIED"
    assert reviewed["lifecycleStatus"] == "ACTIVE"
    assert reviewed["verificationStatus"] == "NOT_RUN"
    # Nothing was applied: proposal review never touches Scripture.
    assert runtime.correction_eligibility.current_text_snapshot()[TARGET_REFERENCE] \
        == ENGLISH_PHP["6"]


def test_cross_verse_meaning_failure_keeps_its_real_reference_relationship(
    cross_verse: tuple[PassageSemanticRuntime, dict],
) -> None:
    """PHP 1:3 -> PHP 1:6, with no manufactured same-verse source unit."""
    runtime, audit = cross_verse
    finding = _of_kind(audit, "POSSIBLE_OVERTRANSLATION")

    source_references = {
        reference
        for unit_id in finding["sourceSemanticUnitIds"]
        for reference in runtime.repository.semantic_unit(unit_id)["displayedReferences"]
    }
    target_references = {
        reference
        for unit_id in finding["targetSemanticUnitIds"]
        for reference in runtime.repository.semantic_unit(unit_id)["displayedReferences"]
    }
    assert source_references == {SOURCE_REFERENCE}
    assert target_references == {TARGET_REFERENCE}
    assert finding["displayedReferences"] == [SOURCE_REFERENCE, TARGET_REFERENCE]

    _confirm(runtime, finding)
    eligibility = runtime.correction_eligibility.evaluate(finding["id"])
    assert eligibility.eligible is True
    # Only the target side is content-addressed; the source verse is untouched.
    assert runtime.correction_eligibility.target_references(
        runtime.repository.qa_finding(finding["id"])) == (TARGET_REFERENCE,)


def test_every_meaning_failure_in_the_run_is_free_of_resource_conflict_blockers(
    cross_verse: tuple[PassageSemanticRuntime, dict],
) -> None:
    """Not just the one finding under test: the whole emitted population."""
    runtime, audit = cross_verse
    findings = _meaning_findings(audit)
    assert findings, "the real pipeline emitted no meaning-failure finding"

    for finding in findings:
        assert finding["resourceConflictEvidenceIds"] == []
        _confirm(runtime, finding)
        codes = _codes(runtime.correction_eligibility.evaluate(finding["id"]))
        assert _RESOURCE_CONFLICT not in codes, f"{finding['kind']} still blocked: {codes}"


# --- Component-status and dimension matrix, through the real pipeline -------

QUANTITY_VERSES = {**ENGLISH_PHP, "3": "i thank my god upon some remembrance of you,"}
COMPLETION_VERSES = {
    **ENGLISH_PHP,
    "6": "he who began a good work in you will carry on and finish it,",
}
POLARITY_VERSES = {
    "22": "but if i live in the flesh this is fruit of labor "
          "and what i will choose i make known,",
}


@pytest.mark.parametrize("verses,pairs,scope,kind,status,dimension", [
    # ALL -> SOME on an explicit Greek quantifier.
    (QUANTITY_VERSES, [("πᾶς", "some")], ("1", "3", "1", "6"),
     "QUANTITY_PROBLEM", "CONTRADICTED", "QUANTITY"),
    # An explicit Greek negation with no negative counterpart in the target.
    # Greek/English on purpose: the Tamil polarity tokenizer defect is a
    # separate, still-open problem and must not decide this result.
    (POLARITY_VERSES, [("οὐ", "known")], ("1", "22", "", ""),
     "NEGATION_PROBLEM", "CONTRADICTED", "POLARITY"),
    # ἐπιτελέω (COMPLETE) realized as bare continuation.
    (COMPLETION_VERSES,
     [("ἐπιτελέω", "carry on")],
     ("1", "3", "1", "6"),
     "POSSIBLE_UNDERTRANSLATION", "TARGET_WEAKENS_SPECIFICITY", "LEXICAL_CONTENT"),
    # ἐνάρχομαι (BEGIN) realized as completion.
    (COMPLETION_VERSES,
     [("ἐνάρχομαι", "finish")],
     ("1", "3", "1", "6"),
     "POSSIBLE_UNDERTRANSLATION", "PARTIALLY_PRESERVED", "LEXICAL_CONTENT"),
    # Unlicensed specificity added by the target.
    (ENGLISH_PHP, CROSS_VERSE_PAIRS, ("1", "3", "1", "6"),
     "POSSIBLE_OVERTRANSLATION", "TARGET_ADDS_SPECIFICITY", "LEXICAL_CONTENT"),
])
def test_pipeline_meaning_failures_are_correctable_across_statuses_and_dimensions(
    tmp_path: Path, verses: dict, pairs: list, scope: tuple,
    kind: str, status: str, dimension: str,
) -> None:
    """Each row is a real finding from a real run, not a hand-built fixture."""
    runtime = _project(tmp_path, verses)
    audit, _ = _analyze(runtime, _FixtureEmbeddings(_paired(pairs)), *scope)

    matching = [
        finding for finding in _meaning_findings(audit)
        if finding["kind"] == kind
        and status in _component_statuses(runtime, finding)
        and dimension in _component_dimensions(runtime, finding)
    ]
    assert matching, (
        f"the real pipeline emitted no {kind}/{status}/{dimension} finding; "
        f"it emitted {[item['kind'] for item in _meaning_findings(audit)]}"
    )

    finding = matching[0]
    assert finding["resourceConflictEvidenceIds"] == []
    _confirm(runtime, finding)
    eligibility = runtime.correction_eligibility.evaluate(finding["id"])
    assert _RESOURCE_CONFLICT not in _codes(eligibility)
    assert eligibility.eligible is True


# --- Component-status and dimension matrix, deterministically ---------------
#
# Two gaps the pipeline cannot fill on its own, so they are driven through the
# real Stage 7 aggregation and the real eligibility rule instead:
#
#   * ALTERED is not a status the deterministic comparator can produce at all.
#   * REFERENT, PARTICIPANT and TEMPORAL_ASPECTUAL source units are created as
#     COMPONENT-role, CONDITIONAL-eligibility units, so they are never coverage
#     owners and Stage 6B never locates them.  That is a pre-existing property
#     of the source inventory, unrelated to this repair.

def _stage7_assessment(
    status: str, dimension: str, *, resource_status: str = "SUPPORTING",
    resource_ids: tuple[str, ...] = ("resource-evidence-1",),
) -> dict:
    """One assessment built by the *real* Stage 7 writer."""
    engine = MeaningAnalysisEngine(SimpleNamespace(
        repository=None, project_id="project-1", book="PHP"))
    component = {
        "id": "meaning-component-1", "coverageDimension": dimension,
        "sourceSemanticUnitIds": ["source-unit-1"],
        "targetSemanticUnitIds": ["target-unit-1"], "targetSpanIds": ["span-1"],
        "status": status,
        "confidence": {"rawScore": 0.9, "calibratedValue": 0.9},
        "evidence": {
            "id": "meaning-evidence-1", "kind": "LEXICAL_CONCEPT",
            "resourceStatus": resource_status,
            "resourceEvidenceIds": list(resource_ids),
        },
        "explanation": f"{dimension} is {status}.",
    }
    relationship = {
        "id": "loc-1", "sourceSemanticUnitIds": ["source-unit-1"],
        "targetSemanticUnitIds": ["target-unit-1"],
        "locationOutcome": "LOCATED",
        "locationConfidence": {"rawScore": 0.9, "calibratedValue": 0.9},
    }
    context = {
        "source": {"fingerprint": "src", "sourceResource": {"resourceHash": "hash"}},
        "target": {"fingerprint": "tgt", "targetRevision": "rev-1",
                   "targetContentHash": "content-hash"},
    }
    return engine._assessment(
        relationship, [component], MeaningPolicy.aggregate([status], False),
        MeaningAssessmentReason.ASSESSED, context, "run-fingerprint",
    )


@pytest.mark.parametrize("status", MEANING_FAILURE_STATUSES)
@pytest.mark.parametrize("dimension", MEANING_DIMENSIONS)
def test_no_meaning_failure_status_is_recorded_as_a_resource_conflict(
    status: str, dimension: str,
) -> None:
    assessment = _stage7_assessment(status, dimension)

    assert assessment["conflictingEvidenceIds"], "meaning evidence must still be recorded"
    assert assessment["resourceConflictEvidenceIds"] == []
    assert resource_conflict_evidence_ids(assessment) == ()


@pytest.mark.parametrize("status", MEANING_FAILURE_STATUSES)
@pytest.mark.parametrize("dimension", MEANING_DIMENSIONS)
def test_a_meaning_failure_finding_is_eligible_in_every_status_and_dimension(
    status: str, dimension: str,
) -> None:
    """The Stage 7 output above, carried into the real eligibility rule."""
    assessment = _stage7_assessment(status, dimension)
    service = _service({VERSE: TAMIL_TEXT})
    _confirmed_finding(
        service,
        conflictingEvidenceIds=list(assessment["conflictingEvidenceIds"]),
        resourceConflictEvidenceIds=list(assessment["resourceConflictEvidenceIds"]),
    )

    result = service.evaluate("qa-1")

    assert _RESOURCE_CONFLICT not in _codes(result)
    assert result.eligible is True


# --- Genuine resource conflicts still block ---------------------------------

def _set_evidence_status(
    runtime: PassageSemanticRuntime, evidence_id: str, status: str,
) -> None:
    """Mark one stored resource record CONFLICTING.

    The controlled part of the resource-conflict tests.  Bridge's own resource
    validation only attaches evidence it managed to match, so it cannot
    currently produce an attached CONFLICTING record; this writes the state a
    resource disagreement would leave, through the same store the reader uses.
    """
    with sqlite3.connect(runtime.repository.path) as conn:
        row = conn.execute(
            "SELECT payload_json FROM evidence_records WHERE id=?", (evidence_id,),
        ).fetchone()
        assert row is not None, f"no such evidence record: {evidence_id}"
        payload = json.loads(row[0])
        payload["validationStatus"] = status
        conn.execute(
            "UPDATE evidence_records SET validation_status=?,payload_json=? WHERE id=?",
            (status, json.dumps(payload, ensure_ascii=False), evidence_id),
        )
        conn.commit()


def test_a_real_resource_disagreement_still_blocks_the_correction(tmp_path: Path) -> None:
    """Two applicable resources, one of them contradicting, unresolved.

    Stage 6B, 7 and 8 all run for real over that state: Stage 7 sees the
    CONFLICTING record on the located source unit, Stage 8 emits a
    RESOURCE_CONFLICT finding, and eligibility refuses it.
    """
    runtime = _project(tmp_path, ENGLISH_PHP)
    location = SemanticLocationEngine(
        runtime, _FixtureEmbeddings(_paired(CROSS_VERSE_PAIRS)),
    ).run_range("1", "3", "1", "6")
    source = runtime.repository.source_inventory(location["sourceInventoryId"])
    units = {unit["id"]: unit for unit in source["units"]}

    located = [
        units[unit_id]
        for relationship in location["relationships"]
        if relationship["locationOutcome"] == "LOCATED"
        for unit_id in relationship["sourceSemanticUnitIds"]
        if len(units.get(unit_id, {}).get("evidenceIds") or ()) >= 2
    ]
    assert located, "no located unit carries two resource records to disagree"
    unit = located[0]
    agreeing, disagreeing = unit["evidenceIds"][0], unit["evidenceIds"][1]
    _set_evidence_status(runtime, disagreeing, "CONFLICTING")
    assert runtime.repository.evidence_record(agreeing)["validationStatus"] == "SUPPORTING"

    meaning = MeaningAnalysisEngine(runtime).run_range(
        "1", "3", "1", "6", location_run_id=location["id"])
    audit = QaAuditEngine(runtime).run_range(
        "1", "3", "1", "6", meaning_run_id=meaning["id"])

    finding = _of_kind(audit, "RESOURCE_CONFLICT")
    assert disagreeing in finding["resourceConflictEvidenceIds"]
    _confirm(runtime, finding)

    eligibility = runtime.correction_eligibility.evaluate(finding["id"])
    assert eligibility.eligible is False
    assert _RESOURCE_CONFLICT in _codes(eligibility)


def test_a_resource_record_that_turns_conflicting_later_blocks_a_confirmed_finding(
    tmp_path: Path,
) -> None:
    """The rule that never fired.

    ``_check_resource_conflicts`` looked for ``resourceValidationStatus`` on the
    evidence record; ``EvidenceRecord`` serializes ``validationStatus``, so no
    record ever matched and the only thing enforcing resource protection was the
    overloaded meaning field.  With that overload gone, this branch *is* the
    protection, so it has to work.
    """
    runtime = _project(tmp_path, ENGLISH_PHP)
    audit, _ = _analyze(
        runtime, _FixtureEmbeddings(_paired(CROSS_VERSE_PAIRS)), "1", "3", "1", "6")
    with_resources = [
        finding for finding in audit["findings"] if finding["resourceEvidenceIds"]
    ]
    assert with_resources, "no finding carries resource evidence"
    finding = with_resources[0]
    _confirm(runtime, finding)
    assert runtime.correction_eligibility.evaluate(finding["id"]).eligible is True

    _set_evidence_status(runtime, finding["resourceEvidenceIds"][0], "CONFLICTING")

    eligibility = runtime.correction_eligibility.evaluate(finding["id"])
    assert eligibility.eligible is False
    assert _RESOURCE_CONFLICT in _codes(eligibility)


def test_stage7_records_a_conflicting_component_as_a_resource_conflict() -> None:
    """The typed field is populated from the component's own resource status."""
    assessment = _stage7_assessment(
        "CONTRADICTED", "LEXICAL_CONTENT",
        resource_status="CONFLICTING", resource_ids=("resource-evidence-9",))

    assert assessment["resourceConflictEvidenceIds"] == ["resource-evidence-9"]
    assert resource_conflict_evidence_ids(assessment) == ("resource-evidence-9",)


# --- Findings written before the split --------------------------------------

def test_a_legacy_finding_with_conflicting_evidence_fails_closed() -> None:
    """No heuristic reinterpretation of an ambiguous 0.9.3 record.

    ``_confirmed_finding`` builds the pre-split shape: ``conflictingEvidenceIds``
    present, ``resourceConflictEvidenceIds`` absent.  Nothing on that record
    says which of the two kinds those ids are, so it blocks.
    """
    service = _service({VERSE: TAMIL_TEXT})
    finding = _confirmed_finding(service, conflictingEvidenceIds=["ev-1"])
    assert "resourceConflictEvidenceIds" not in finding

    result = service.evaluate("qa-1")

    assert result.eligible is False
    assert _RESOURCE_CONFLICT in _codes(result)


def test_a_legacy_finding_without_conflicting_evidence_is_unaffected() -> None:
    service = _service({VERSE: TAMIL_TEXT})
    assert "resourceConflictEvidenceIds" not in _confirmed_finding(service)

    assert service.evaluate("qa-1").eligible is True


def test_re_analysis_repairs_a_legacy_finding_and_keeps_the_human_decision(
    cross_verse: tuple[PassageSemanticRuntime, dict],
) -> None:
    """Re-analysis, not reinterpretation, is what clears a legacy record.

    The finding is rewritten to its 0.9.3 shape, then written again by the
    production Stage 8 writer under its own stable id.
    """
    runtime, audit = cross_verse
    finding = _of_kind(audit, "POSSIBLE_OVERTRANSLATION")
    _confirm(runtime, finding)

    stored = runtime.repository.qa_finding(finding["id"])
    legacy = {k: v for k, v in stored.items() if k != "resourceConflictEvidenceIds"}
    with sqlite3.connect(runtime.repository.path) as conn:
        conn.execute(
            "UPDATE qa_findings SET payload_json=? WHERE id=?",
            (json.dumps(legacy, ensure_ascii=False), finding["id"]),
        )
        conn.commit()

    blocked = runtime.correction_eligibility.evaluate(finding["id"])
    assert blocked.eligible is False
    assert _RESOURCE_CONFLICT in _codes(blocked)

    engine = QaAuditEngine(runtime)
    runtime.repository.save_qa_finding(engine._finding_to_dataclass(finding))

    repaired = runtime.repository.qa_finding(finding["id"])
    assert repaired["id"] == finding["id"], "the stable finding id survived"
    assert repaired["qaDisposition"] == "CONFIRMED_TRANSLATION_ERROR"
    assert repaired["reviewStatus"] == "HUMAN_APPROVED"
    assert repaired["resourceConflictEvidenceIds"] == []
    assert runtime.correction_eligibility.evaluate(finding["id"]).eligible is True


# --- Stage 9B.4 interaction -------------------------------------------------

def _propose(runtime: PassageSemanticRuntime, finding: dict, word: str = "only") -> dict:
    text = ENGLISH_PHP["6"]
    start = text.index(word)
    content_hash = runtime.text_hash(text)
    return runtime.correction_create_proposal(
        finding_id=finding["id"],
        intent=CorrectionIntent(
            failed_dimension=CoverageDimension.LEXICAL_CONTENT,
            observed_meaning="the target adds unlicensed specificity",
            required_meaning="drop the unsupported restriction",
            affected_source_semantic_unit_ids=tuple(finding["sourceSemanticUnitIds"]),
            affected_target_span=AffectedTargetSpan(
                displayed_reference=TARGET_REFERENCE,
                canonical_references=(TARGET_REFERENCE,),
                start_code_point=start, end_code_point=start + len(word),
                original_text=word,
                target_text_revision=runtime.text_revision(TARGET_REFERENCE, content_hash),
                target_content_hash=content_hash,
            ),
        ),
        human_proposed_text="constant",
        explanation="Reviewer wording.", actor_id="Reviewer",
    )


def test_a_meaning_failure_can_be_applied_and_traced_into_stage_9b4(
    cross_verse: tuple[PassageSemanticRuntime, dict],
) -> None:
    """meaning failure -> confirm -> propose -> apply -> re-analysis obligation.

    9B.4's verdict rules are not touched here; what is checked is that the same
    meaning-failure obligation survives the whole path and arrives at
    verification with its cross-verse provenance intact.
    """
    runtime, audit = cross_verse
    finding = _of_kind(audit, "POSSIBLE_OVERTRANSLATION")
    _confirm(runtime, finding)
    proposal = _propose(runtime, finding)
    reviewed = runtime.correction_edit_proposal(
        proposal["id"], proposed_text="constant",
        expected_revision=proposal["revision"], actor_id="Reviewer",
        explanation="Reviewer wording.",
    )

    stored_finding = runtime.repository.qa_finding(finding["id"])
    service = CorrectionApplicationService(
        runtime,
        lambda chapter, verse, text, **options: runtime.project.apply_scripture_edit(
            chapter, verse, text, **options,
        ),
    )
    applied = service.apply(
        proposal_id=proposal["id"], expected_proposal_revision=reviewed["revision"],
        finding_id=finding["id"], expected_finding_revision=stored_finding["revision"],
        application_id="meaning-failure-apply-1",
        actor={"actorType": "HUMAN", "actorId": "Reviewer"},
    )

    assert applied["applicationState"] == "COMPLETED"
    assert applied["sourceProvenanceReferences"] == [SOURCE_REFERENCE]
    assert applied["targetDisplayedReference"] == TARGET_REFERENCE
    assert runtime.project.target_verse_text("1", "6") == \
        ENGLISH_PHP["6"].replace("only", "constant", 1)
    assert runtime.project.target_verse_text("1", "3") == ENGLISH_PHP["3"]

    persisted = runtime.repository.correction_proposal(proposal["id"])
    assert persisted["appliedTargetRevision"]
    assert persisted["verificationStatus"] == "PENDING"
    # The finding the reviewer confirmed is now awaiting re-analysis, with the
    # human decision still on it.
    after = runtime.repository.qa_finding(finding["id"])
    assert after["lifecycleStatus"] == "STALE"
    assert after["qaDisposition"] == "CONFIRMED_TRANSLATION_ERROR"

    # Stage 9B.3c/9B.4 can still see both ends of the obligation: the source
    # semantics at PHP 1:3 and the verse actually edited at PHP 1:6. Nothing
    # about the verdict rules is touched here -- only that they are reachable.
    scope = CorrectionAffectedScopeResolver(runtime).resolve("meaning-failure-apply-1")
    assert scope["resolvedSourceReferences"] == [SOURCE_REFERENCE]
    assert scope["resolvedTargetReferences"] == [TARGET_REFERENCE]
    displayed = scope["resolvedStructuralRange"]["displayedReferences"]
    assert SOURCE_REFERENCE in displayed and TARGET_REFERENCE in displayed
