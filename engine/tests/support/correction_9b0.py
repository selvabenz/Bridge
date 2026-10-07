"""Builders for the Stage 9B.0 correction tests: the Tamil verse, stub runtime
and repository, a correction service over them and a confirmed Stage 8
finding.

Moved verbatim out of test_correction_stage9b0.py, which another module
imported them from (CLAUDE.md rule 2, #74)."""
from __future__ import annotations

import hashlib

from tc_ai_bridge import passage_semantic_repository as repository_module
from tc_ai_bridge.correction_eligibility import CorrectionEligibilityService
from tc_ai_bridge.passage_semantic_models import LifecycleStatus, QaDisposition, ReviewStatus


class _StubRepository:
    def __init__(self) -> None:
        self.findings: dict[str, dict] = {}
        self.assessments: dict[str, dict] = {}
        self.relationships: dict[str, dict] = {}
        self.accounts: dict[str, dict] = {}
        self.evidence: dict[str, dict] = {}
        self.proposals: dict[str, list[dict]] = {}

    def qa_finding(self, finding_id):
        try:
            return self.findings[finding_id]
        except KeyError:
            raise repository_module.FoundationValidationError(
                f"Unknown QA finding: {finding_id}") from None

    def meaning_assessment(self, assessment_id):
        try:
            return self.assessments[assessment_id]
        except KeyError:
            raise repository_module.FoundationValidationError("unknown") from None

    def semantic_location_relationship(self, relationship_id):
        try:
            return self.relationships[relationship_id]
        except KeyError:
            raise repository_module.FoundationValidationError("unknown") from None

    def coverage_account(self, account_id):
        try:
            return self.accounts[account_id]
        except KeyError:
            raise repository_module.FoundationValidationError("unknown") from None

    def evidence_record(self, evidence_id):
        try:
            return self.evidence[evidence_id]
        except KeyError:
            raise repository_module.FoundationValidationError("unknown") from None

    def correction_proposals_for_finding(self, finding_id):
        return self.proposals.get(finding_id, [])


class _StubRuntime:
    """Just enough runtime for eligibility: hashes, revisions, current text."""

    def __init__(self, texts: dict[str, str]) -> None:
        self.repository = _StubRepository()
        self.project_id = "project-1"
        self.book = "PHP"
        self.project = object()
        self._texts = dict(texts)

    @staticmethod
    def text_hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def text_revision(self, reference: str, text_hash: str) -> str:
        return hashlib.sha256(f"{reference}:{text_hash}".encode("utf-8")).hexdigest()


def _service(texts: dict[str, str]) -> CorrectionEligibilityService:
    runtime = _StubRuntime(texts)
    service = CorrectionEligibilityService(runtime)
    service.current_text_snapshot = lambda: dict(runtime._texts)  # type: ignore[method-assign]
    return service


VERSE = "PHP 1:3"


TEXT = "நான் உங்களை நினைக்கும் போதெல்லாம் என் தேவனை ஸ்தோத்திரிக்கிறேன்."


def _confirmed_finding(service, **overrides) -> dict:
    finding = {
        "id": "qa-1",
        "qaDisposition": QaDisposition.CONFIRMED_TRANSLATION_ERROR.value,
        "reviewStatus": ReviewStatus.HUMAN_APPROVED.value,
        "lifecycleStatus": LifecycleStatus.ACTIVE.value,
        "revision": 3,
        "displayedReferences": [VERSE],
        "targetContentHashes": [service.runtime.text_hash(TEXT)],
        "meaningAssessmentIds": [],
        "coverageAccountIds": [],
        "conflictingEvidenceIds": [],
        "resourceEvidenceIds": [],
    }
    finding.update(overrides)
    service.repository.findings["qa-1"] = finding
    return finding
