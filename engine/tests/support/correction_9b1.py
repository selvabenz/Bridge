"""Builders for the Stage 9B.1 correction-wording tests: the reference and
Tamil verse, a fixture wording provider, stub eligibility and review
services, a small runtime and a correction intent.

Moved verbatim out of test_correction_stage9b1.py, which another module
imported them from (CLAUDE.md rule 2, #74)."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path

from tc_ai_bridge.correction_eligibility import (
    CorrectionEligibility,
    CorrectionEligibilityCode,
    CurrentTextValidation,
    EligibilityReason,
)
from tc_ai_bridge.correction_wording import (
    CorrectionSuggestionProvider,
    CorrectionSuggestionResult,
    CorrectionWordingService,
)
from tc_ai_bridge.passage_semantic_models import (
    AffectedTargetSpan,
    CorrectionIntent,
    CorrectionWordingAlternative,
    CoverageDimension,
    EvidenceKind,
    EvidenceRecord,
    LifecycleStatus,
    PolicyBinding,
    QaDisposition,
    ReviewStatus,
    ResourceValidationStatus,
)
from tc_ai_bridge.passage_semantic_repository import FoundationRepository


REFERENCE = "PHP 1:3"


TEXT = "நான் உங்களை நினைக்கும்போதெல்லாம் என் தேவனை நினைக்கிறேன்."


class _Eligibility:
    def __init__(self, runtime: "_Runtime", *, eligible: bool = True) -> None:
        self.runtime = runtime
        self.eligible = eligible
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def evaluate(self, finding_id: str, *, ignore_proposal_ids=()) -> CorrectionEligibility:
        ignored = tuple(ignore_proposal_ids)
        self.calls.append((finding_id, ignored))
        reasons = (
            (EligibilityReason(CorrectionEligibilityCode.ELIGIBLE, "eligible"),)
            if self.eligible else
            (EligibilityReason(
                CorrectionEligibilityCode.DISPOSITION_NOT_CONFIRMED,
                "finding is not confirmed",
            ),)
        )
        return CorrectionEligibility(
            finding_id=finding_id,
            eligible=self.eligible,
            reasons=reasons,
            finding_revision=2,
            current_target_content_hash=self.runtime.text_hash(TEXT),
            displayed_references=(REFERENCE,),
        )

    def validate_current_text(self, **kwargs) -> CurrentTextValidation:
        start = int(kwargs["start_code_point"])
        end = int(kwargs["end_code_point"])
        observed = TEXT[start:end]
        expected = kwargs.get("expected_span_text")
        valid = expected == observed
        return CurrentTextValidation(
            valid=valid,
            reasons=() if valid else (EligibilityReason(
                CorrectionEligibilityCode.SPAN_TEXT_MISMATCH, "span changed"),),
            current_target_revision=self.runtime.text_revision(
                REFERENCE, self.runtime.text_hash(TEXT)),
            current_target_content_hash=self.runtime.text_hash(TEXT),
            observed_span_text=observed,
        )

    def current_text_snapshot(self) -> dict[str, str]:
        return {REFERENCE: TEXT}


class _Review:
    def get_finding(self, finding_id: str) -> dict:
        return {
            "finding": {
                "id": finding_id,
                "qaDisposition": QaDisposition.CONFIRMED_TRANSLATION_ERROR.value,
                "reviewStatus": ReviewStatus.HUMAN_APPROVED.value,
                "lifecycleStatus": LifecycleStatus.ACTIVE.value,
                "sourceSemanticUnitIds": [],
                "targetSemanticUnitIds": [],
                "evidenceIds": ["evidence-1"],
                "explanation": "The required meaning is not preserved.",
            },
            "sourceSemanticUnits": [],
            "targetSemanticUnits": [],
            "location": [],
            "meaning": [],
            "coverage": [],
            "resources": [],
            "supportingEvidence": [{"id": "evidence-1", "content": "source evidence"}],
            "conflictingEvidence": [],
        }


class _Runtime:
    def __init__(self, path: Path) -> None:
        self.repository = FoundationRepository(path)
        self.repository.create_qa_finding("qa-1", "project-1")
        evidence_text = "source evidence"
        self.repository.save_evidence_record(EvidenceRecord(
            id="evidence-1", project_id="project-1", book="PHP",
            kind=EvidenceKind.SOURCE_TEXT, resource_id="fixture/source",
            resource_version="1", resource_hash="fixture-resource-hash",
            occurrence_id="PHP 1:3#1", displayed_references=(REFERENCE,),
            canonical_references=(REFERENCE,), content=evidence_text,
            content_hash=hashlib.sha256(evidence_text.encode("utf-8")).hexdigest(),
            validation_status=ResourceValidationStatus.SUPPORTING,
            source_semantic_unit_ids=(), target_semantic_unit_ids=(),
            policy_binding=PolicyBinding.foundation_v1(),
            review_status=ReviewStatus.UNREVIEWED,
            lifecycle_status=LifecycleStatus.ACTIVE,
        ))
        self.project_id = "project-1"
        self.book = "PHP"
        self.project = object()
        self.qa_review = _Review()
        self.correction_eligibility = _Eligibility(self)
        self.correction_wording = CorrectionWordingService(self)

    @staticmethod
    def text_hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def text_revision(reference: str, text_hash: str) -> str:
        return hashlib.sha256(f"{reference}:{text_hash}".encode("utf-8")).hexdigest()

    def correction_create_proposal(self, *, provider=None, **options):
        service = CorrectionWordingService(self, provider) if provider else self.correction_wording
        return service.create_proposal(**options)

    def correction_get_review_context(self, finding_id: str):
        return self.correction_wording.review_context(finding_id)

    def correction_edit_proposal(self, proposal_id: str, **options):
        return self.correction_wording.edit_proposal(proposal_id, **options)

    def correction_reject_proposal(self, proposal_id: str, **options):
        return self.correction_wording.reject_proposal(proposal_id, **options)


@dataclass
class _FixtureProvider(CorrectionSuggestionProvider):
    calls: int = 0

    @property
    def available(self) -> bool:
        return True

    def suggest(self, context: dict) -> CorrectionSuggestionResult:
        self.calls += 1
        assert context["intent"]["requiredMeaning"] == "restore the missing meaning"
        return CorrectionSuggestionResult(
            proposed_text="என் தேவனை",
            explanation="Restores the source meaning without changing verse order.",
            evidence_ids=("evidence-1",),
            alternatives=(CorrectionWordingAlternative(
                proposed_text="என்னுடைய தேவனை",
                explanation="A more explicit possessive alternative.",
                evidence_ids=("evidence-1",),
            ),),
            provider_name="fixture",
            model="fixture-multilingual-v1",
            prompt_policy_version="correction-wording-v1",
            response_fingerprint="response-hash",
        )


def _intent(runtime: _Runtime) -> CorrectionIntent:
    start = TEXT.index("என் தேவனை")
    end = start + len("என் தேவனை")
    content_hash = runtime.text_hash(TEXT)
    return CorrectionIntent(
        failed_dimension=CoverageDimension.LEXICAL_CONTENT,
        observed_meaning="the phrase is absent or incorrect",
        required_meaning="restore the missing meaning",
        affected_source_semantic_unit_ids=(),
        affected_target_span=AffectedTargetSpan(
            displayed_reference=REFERENCE,
            canonical_references=(REFERENCE,),
            start_code_point=start,
            end_code_point=end,
            original_text=TEXT[start:end],
            target_text_revision=runtime.text_revision(REFERENCE, content_hash),
            target_content_hash=content_hash,
        ),
    )
