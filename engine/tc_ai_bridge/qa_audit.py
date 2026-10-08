"""Stage 8 bidirectional source-coverage / target-support QA synthesis.

This module never re-runs Stage 6B location search and never re-judges
Stage 7 meaning preservation. It synthesizes their frozen, already-persisted
outputs into gated coverage/support determinations and, only when every
required gate passes, into AI_PROPOSED QaFinding records. It never
auto-promotes POSSIBLY_MISSING/POSSIBLY_UNSUPPORTED to a confirmed MISSING/
UNSUPPORTED state, and it never generates or applies correction wording.
"""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
import json
import time
from typing import Any, Iterator

from .meaning_analysis import resource_conflict_evidence_ids
from .passage_semantic_models import (
    AuditDirection,
    ConfidenceScore,
    CoverageDimension,
    LifecycleStatus,
    PolicyBinding,
    QaDisposition,
    QaFinding,
    QaFindingKind,
    QaFindingSeverity,
    QaRunStatus,
    Realization,
    RelationshipProperty,
    ReviewStatus,
    SemanticCoverageAccount,
    SemanticRelationship,
    SourceCoverage,
    TargetSupport,
)
from .qa_target_hash import (
    canonical_text_hash,
    finding_target_references,
    target_content_hashes,
)


# Deliberately NOT bumped when targetContentHashes was corrected to per-verse
# hashes (see qa_target_hash).  Bumping it would change the QA run fingerprint
# and force a Stage 8 re-run for every existing project -- but the engine
# version is *not* part of `_build_target_support_account`'s account
# fingerprint (the policy version is), so on an unchanged target inventory that
# re-run re-INSERTs a byte-identical coverage_accounts row and dies with
# FoundationConflict before it can repair anything.  Verified, not assumed.
# Trading a wrong-hash bug for a crash is not an upgrade; findings persisted by
# the broken writer are instead repaired the next time the run fingerprint
# legitimately misses cache.  See docs/BUILD_LOG.md.
QA_ENGINE_VERSION = "bridge-qa-audit-v1"
# v2 (#218): a reasoned null alignment decision explains an absence -- a source
# meaning realised grammatically or implicitly is covered, and a target word
# that is grammar or explicitation is supported -- before the NOT_LOCATED and
# function-word heuristics are consulted.
QA_POLICY_VERSION = "qa-policy-v2"
QA_CONFIDENCE_POLICY_VERSION = "qa-confidence-v1"
QA_CALIBRATION_VERSION = "qa-uncalibrated-v1"
QA_MODEL_VERSION = "deterministic-coverage-support-synthesizer-v1"

_LOCATED = "LOCATED"
_AMBIGUOUS = "AMBIGUOUS"
_NOT_LOCATED = "NOT_LOCATED"
_SEARCH_INCOMPLETE = "SEARCH_INCOMPLETE"
_UNSUPPORTED_ANALYSIS = "UNSUPPORTED_ANALYSIS"
_BLOCKING_OUTCOMES = {_AMBIGUOUS, _SEARCH_INCOMPLETE, _UNSUPPORTED_ANALYSIS}
_PRESERVED_STATUSES = {"PRESERVED", "PRESERVED_WITH_RESTRUCTURING"}
_NON_AUDIT_ROLES = {"AGGREGATE", "EVIDENCE_ONLY"}
_NON_AUDIT_ELIGIBILITY = {"AGGREGATE_ONLY", "EXCLUDED", "REVIEW_ONLY"}


def _sha(value: str) -> str:
    return canonical_text_hash(value)


def _json_hash(value: Any) -> str:
    return _sha(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _confidence(raw: float) -> ConfidenceScore:
    return ConfidenceScore(
        raw_score=raw, calibrated_value=raw,
        confidence_policy_version=QA_CONFIDENCE_POLICY_VERSION,
        calibration_version=QA_CALIBRATION_VERSION,
    )


class PhaseProfiler:
    """Exclusive wall-clock accounting for the Stage 8 phases.

    Time spent inside a nested phase is subtracted from its parent, so the
    reported phases sum to the measured total instead of double-counting the
    persistence and synthesis work that happens inside each audit pass.
    Purely observational: it reads no QA state and changes no determination.
    """

    def __init__(self) -> None:
        self.totals: dict[str, float] = {}
        self._stack: list[float] = []

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        self._stack.append(0.0)
        started = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - started
            child = self._stack.pop()
            self.totals[name] = self.totals.get(name, 0.0) + elapsed - child
            if self._stack:
                self._stack[-1] += elapsed

    def report(self) -> dict[str, float]:
        return {name: round(value, 6) for name, value in sorted(self.totals.items())}


class _ProfiledRepository:
    """Forwards every repository call unchanged, timing only the writes.

    Used so persistence cost is attributed without editing any save call site;
    because PhaseProfiler is exclusive, this time is subtracted from whichever
    audit pass is currently running.
    """

    def __init__(self, repository: Any, profiler: PhaseProfiler):
        self._repository = repository
        self._profiler = profiler

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._repository, name)
        if not callable(attribute) or not name.startswith(("save_", "update_", "create_")):
            return attribute

        def profiled(*args: Any, **kwargs: Any) -> Any:
            with self._profiler.phase("persistence"):
                return attribute(*args, **kwargs)

        return profiled


class QaAuditPolicy:
    """One centralized, versioned deterministic gate/precedence/severity policy."""

    version = QA_POLICY_VERSION

    DIMENSION_FINDING_KIND = {
        "POLARITY": QaFindingKind.NEGATION_PROBLEM,
        "QUANTITY": QaFindingKind.QUANTITY_PROBLEM,
        "TEMPORAL_ASPECTUAL": QaFindingKind.TEMPORAL_PROBLEM,
        "PARTICIPANT": QaFindingKind.PARTICIPANT_PROBLEM,
        "REFERENT": QaFindingKind.REFERENT_PROBLEM,
    }
    HIGH_SEVERITY_KINDS = {
        QaFindingKind.NEGATION_PROBLEM, QaFindingKind.QUANTITY_PROBLEM,
        QaFindingKind.TEMPORAL_PROBLEM, QaFindingKind.PARTICIPANT_PROBLEM,
        QaFindingKind.REFERENT_PROBLEM, QaFindingKind.CONTRADICTION,
    }
    FUNCTION_WORD_FORMS = {
        "the", "a", "an", "is", "are", "was", "were", "to", "of", "and", "in", "on",
        "it", "he", "she", "they", "him", "her", "them",
        "அது", "ஒரு", "இது", "அவர்", "அவர்கள்", "ஐ", "ஓடு", "இல்", "க்கு",
    }
    SPECIFICITY_MARKERS = {"holy", "old", "quickly", "only"}
    LICENSED_EXPLICITATIONS_TARGETS = {"the man", "the people"}

    @classmethod
    def source_coverage_for(
        cls, owner_unit: dict[str, Any] | None, relationships: list[dict[str, Any]],
        assessments_by_relationship: dict[str, dict[str, Any]], has_variant_evidence: bool,
        null_precedent: dict[str, Any] | None = None,
    ) -> tuple[SourceCoverage, str]:
        """`null_precedent` (#218) is an active null alignment decision covering
        every token of the owner unit, from `null_precedents_for_range`. It
        explains an *absence* only: a NOT_LOCATED (or relationship-less)
        obligation becomes COVERED_BY_RESTRUCTURING, but an AMBIGUOUS or
        incomplete search stays UNCERTAIN, and a located realization is still
        judged on its own meaning. Stage 6B is never re-run here."""
        if owner_unit is None:
            return SourceCoverage.UNCERTAIN, "The audit owner unit could not be resolved."
        if (owner_unit.get("accountingRole") in _NON_AUDIT_ROLES
                or owner_unit.get("auditEligibility") in _NON_AUDIT_ELIGIBILITY):
            return (
                SourceCoverage.NOT_CHECKED,
                "This obligation is derived/aggregate-only and is not independently audit-eligible.",
            )
        if not relationships:
            if null_precedent:
                return SourceCoverage.COVERED_BY_RESTRUCTURING, cls._null_reason_text(null_precedent)
            return SourceCoverage.UNCERTAIN, "No source-to-target relationship references this obligation."
        outcomes = {relationship.get("locationOutcome") for relationship in relationships}
        if outcomes & _BLOCKING_OUTCOMES:
            if _SEARCH_INCOMPLETE in outcomes:
                return SourceCoverage.UNCERTAIN, "Stage 6B search did not complete for this passage scope."
            if _AMBIGUOUS in outcomes:
                return SourceCoverage.UNCERTAIN, "Stage 6B returned competing candidate locations; no confident omission can be inferred."
            return SourceCoverage.UNCERTAIN, "Location analysis capability was unavailable for this obligation."
        for relationship in relationships:
            if relationship.get("locationOutcome") != _LOCATED:
                continue
            assessment = assessments_by_relationship.get(relationship["id"])
            if assessment is None or assessment.get("meaningStatus") not in _PRESERVED_STATUSES:
                continue
            restructured = bool(relationship.get("properties")) or (
                relationship.get("realization") != Realization.LEXICALLY_REALIZED.value
            )
            return (
                (SourceCoverage.COVERED_BY_RESTRUCTURING if restructured else SourceCoverage.COVERED),
                "A located target realization preserves the required source meaning.",
            )
        if outcomes == {_NOT_LOCATED}:
            if null_precedent:
                return SourceCoverage.COVERED_BY_RESTRUCTURING, cls._null_reason_text(null_precedent)
            if has_variant_evidence:
                return (
                    SourceCoverage.UNCERTAIN,
                    "Documented source-variant evidence may explain the absence; requires human review.",
                )
            return (
                SourceCoverage.POSSIBLY_MISSING,
                "No lexical, grammatical, pronominalized, implicit, split, merged, or cross-verse "
                "realization was found after a completed search of the requested passage scope.",
            )
        return (
            SourceCoverage.UNCERTAIN,
            "A target location exists but its meaning was not determined to preserve this obligation; "
            "see the related meaning-preservation finding.",
        )

    @staticmethod
    def _null_reason_text(null_precedent: dict[str, Any]) -> str:
        reason = str(null_precedent.get("reason") or "").lower()
        origin = "the automatic alignment pass" if null_precedent.get("origin") == "ai-auto" else "a reviewer"
        note = str(null_precedent.get("note") or "").strip()
        return (
            f"An alignment decision by {origin} records this meaning as realised {reason}ly, "
            f"with no separate target word{': ' + note if note else ''}."
        ) if reason in {"grammatical", "implicit"} else (
            f"An alignment decision by {origin} marks this as {reason}{': ' + note if note else ''}."
        )

    @classmethod
    def _is_function_word(cls, unit: dict[str, Any]) -> bool:
        text = str(unit.get("normalizedSurface") or unit.get("rawSurface") or "").strip().lower()
        return text in cls.FUNCTION_WORD_FORMS

    @classmethod
    def _is_licensed_explicitation(cls, unit: dict[str, Any]) -> bool:
        text = str(unit.get("normalizedSurface") or unit.get("rawSurface") or "").strip().lower()
        return text in cls.LICENSED_EXPLICITATIONS_TARGETS

    @classmethod
    def _has_unsupported_specificity(cls, unit: dict[str, Any]) -> bool:
        text = str(unit.get("normalizedSurface") or unit.get("rawSurface") or "").strip().lower()
        return text in cls.SPECIFICITY_MARKERS

    @classmethod
    def target_support_for(
        cls, unit: dict[str, Any], relationships: list[dict[str, Any]],
        assessments_by_relationship: dict[str, dict[str, Any]],
        null_precedent: dict[str, Any] | None = None,
    ) -> tuple[TargetSupport, str]:
        if (unit.get("accountingRole") in _NON_AUDIT_ROLES
                or unit.get("auditEligibility") in _NON_AUDIT_ELIGIBILITY):
            return (
                TargetSupport.NOT_CHECKED,
                "This target contribution is derived/aggregate-only and is not independently audit-eligible.",
            )
        if not relationships:
            # #218: a reasoned decision on this exact word comes before the
            # hard-coded word lists, which only know English and a few Tamil
            # forms.
            if null_precedent and null_precedent.get("reason") == "GRAMMATICAL":
                return (
                    TargetSupport.GRAMMATICALLY_REQUIRED,
                    "An alignment decision records this word as required by target-language grammar.",
                )
            if null_precedent and null_precedent.get("reason") == "EXPLICITATION":
                return (
                    TargetSupport.EXPLICITATION_SUPPORTED,
                    "An alignment decision records this word as an explicitation of implied source meaning.",
                )
            if cls._is_function_word(unit):
                return (
                    TargetSupport.GRAMMATICALLY_REQUIRED,
                    "This target-language function-word class requires no direct source lexical counterpart.",
                )
            if cls._is_licensed_explicitation(unit):
                return (
                    TargetSupport.EXPLICITATION_SUPPORTED,
                    "A licensed explicitation is supported by controlled context evidence.",
                )
            if cls._has_unsupported_specificity(unit):
                return (
                    TargetSupport.POSSIBLY_UNSUPPORTED,
                    "The target adds explicit specificity with no source relationship, grammatical "
                    "requirement, or licensed explicitation to support it.",
                )
            return TargetSupport.UNCERTAIN, "No source relationship references this target contribution."
        outcomes = {relationship.get("locationOutcome") for relationship in relationships}
        if outcomes & _BLOCKING_OUTCOMES:
            return (
                TargetSupport.UNCERTAIN,
                "Upstream location analysis has not conclusively resolved this target contribution.",
            )
        for relationship in relationships:
            assessment = assessments_by_relationship.get(relationship["id"])
            if assessment is None:
                continue
            status = assessment.get("meaningStatus")
            if status in _PRESERVED_STATUSES:
                return TargetSupport.SOURCE_SUPPORTED, "A source semantic unit is located here with preserved meaning."
            if status == "OVERTRANSLATED":
                for component in assessment.get("componentAssessments", []):
                    if component.get("status") == "TARGET_ADDS_SPECIFICITY":
                        return (
                            TargetSupport.POSSIBLY_UNSUPPORTED,
                            "The target adds specificity not licensed by the located source expression.",
                        )
        return (
            TargetSupport.CONTEXT_SUPPORTED,
            "A source relationship is located here and no unsupported-specificity evidence was found.",
        )

    @classmethod
    def finding_kind_for(cls, assessment: dict[str, Any]) -> QaFindingKind | None:
        components = assessment.get("componentAssessments", [])
        if any(component.get("evidence", {}).get("resourceStatus") == "CONFLICTING" for component in components):
            return QaFindingKind.RESOURCE_CONFLICT
        status = assessment.get("meaningStatus")
        if status in _PRESERVED_STATUSES or status == "UNVERIFIABLE":
            return None
        problem_components = [
            component for component in components
            if component.get("status") in {"CONTRADICTED", "ALTERED"}
        ]
        for component in problem_components:
            kind = cls.DIMENSION_FINDING_KIND.get(component.get("coverageDimension"))
            if kind:
                return kind
        if status == "CONTRADICTED":
            return QaFindingKind.CONTRADICTION
        if status == "MEANING_SHIFT":
            return QaFindingKind.MEANING_SHIFT
        if status in {"UNDERTRANSLATED", "PARTIAL"}:
            return QaFindingKind.POSSIBLE_UNDERTRANSLATION
        if status == "OVERTRANSLATED":
            return QaFindingKind.POSSIBLE_OVERTRANSLATION
        return None

    @classmethod
    def severity_for(cls, kind: QaFindingKind, confidence: float) -> QaFindingSeverity:
        if kind in cls.HIGH_SEVERITY_KINDS:
            return QaFindingSeverity.CRITICAL if confidence >= 0.9 else QaFindingSeverity.HIGH
        if kind == QaFindingKind.MEANING_SHIFT:
            return QaFindingSeverity.HIGH if confidence >= 0.85 else QaFindingSeverity.MEDIUM
        if kind in {QaFindingKind.POSSIBLE_OMISSION, QaFindingKind.POSSIBLE_ADDITION}:
            return QaFindingSeverity.MEDIUM
        if kind == QaFindingKind.RESOURCE_CONFLICT:
            return QaFindingSeverity.MEDIUM
        return QaFindingSeverity.LOW


class QaAuditEngine:
    def __init__(self, runtime: Any, policy: QaAuditPolicy | None = None):
        self.runtime = runtime
        self.repository = runtime.repository
        self.project_id = runtime.project_id
        self.book = runtime.book
        self.policy = policy or QaAuditPolicy()

    def run_range(
        self, chapter: str, verse: str, end_chapter: str = "", end_verse: str = "",
        *, meaning_run_id: str = "",
    ) -> dict[str, Any]:
        meaning = (
            self.repository.meaning_analysis_run(meaning_run_id) if meaning_run_id
            else self.runtime.meaning_analysis.run_range(chapter, verse, end_chapter, end_verse)
        )
        location = self.repository.semantic_location_run(meaning["locationRunId"])
        source = self.repository.source_inventory(location["sourceInventoryId"])
        target = self.repository.target_inventory(location["targetInventoryId"])

        fingerprint = _json_hash({
            "meaningRun": meaning["id"], "meaningFingerprint": meaning["fingerprint"],
            "source": source["fingerprint"], "target": target["fingerprint"],
            "engine": QA_ENGINE_VERSION, "policy": self.policy.version,
            "model": QA_MODEL_VERSION, "calibration": QA_CALIBRATION_VERSION,
        })
        profiler = PhaseProfiler()
        with profiler.phase("cachedRetrieval"):
            cached = self.repository.qa_audit_for_fingerprint(
                self.project_id, self.book, location["rangeKey"], fingerprint,
            )
        if cached is not None:
            cached["cacheStatus"] = "HIT"
            cached["phaseProfile"] = profiler.report()
            return cached
        started = time.perf_counter()

        source_units = {unit["id"]: unit for unit in source["units"]}
        target_units = {unit["id"]: unit for unit in target["units"]}
        # #218: reasoned null alignment decisions, by token instance id. Read
        # here, never re-derived from Stage 6B. Their digest is already part of
        # the location run's fingerprint (alignment_state_digest), so a change
        # to them reaches this run's fingerprint through the meaning run.
        null_precedents = self._null_precedents(chapter, verse, end_chapter, end_verse)
        current_text = self._current_target_text()
        relationships_by_id = {item["id"]: item for item in location["relationships"]}
        assessments_by_relationship = {
            item["semanticLocationRelationshipId"]: item for item in meaning["assessments"]
        }
        relationships_by_source_owner: dict[str, list[dict[str, Any]]] = {}
        relationships_by_target_unit: dict[str, list[dict[str, Any]]] = {}
        for relationship in location["relationships"]:
            for unit_id in relationship.get("sourceSemanticUnitIds", []):
                relationships_by_source_owner.setdefault(unit_id, []).append(relationship)
            for unit_id in relationship.get("targetSemanticUnitIds", []):
                relationships_by_target_unit.setdefault(unit_id, []).append(relationship)

        policy_binding = PolicyBinding(
            confidence_policy_version=QA_CONFIDENCE_POLICY_VERSION,
            calibration_version=QA_CALIBRATION_VERSION, audit_policy_version=self.policy.version,
        )
        findings: list[dict[str, Any]] = []
        source_coverage_ids: list[str] = []
        target_support_ids: list[str] = []
        seen_owner_dimension: set[tuple[str, str]] = set()
        seen_relationship_finding: set[str] = set()

        # Persistence is attributed via a transparent proxy so no save call
        # site changes; the proxy is always restored, profiling or not.
        real_repository = self.repository
        self.repository = _ProfiledRepository(real_repository, profiler)
        try:
            with profiler.phase("sourceCoverageAudit"):
                # --- Source coverage pass (item 5-9) -------------------------------
                for account in source.get("coverageAccounts", []):
                    owner_id = account["auditOwnerUnitId"]
                    dimension = account["coverageDimension"]
                    key = (owner_id, dimension)
                    if key in seen_owner_dimension:
                        continue
                    seen_owner_dimension.add(key)
                    owner_unit = source_units.get(owner_id)
                    relationships = relationships_by_source_owner.get(owner_id, [])
                    has_variant_evidence = any(
                        self.repository.evidence_record(evidence_id).get("kind") == "SOURCE_VARIANT"
                        for evidence_id in (owner_unit or {}).get("evidenceIds", [])
                    )
                    status, reason = self.policy.source_coverage_for(
                        owner_unit, relationships, assessments_by_relationship, has_variant_evidence,
                        null_precedent=self._unit_null(owner_unit, null_precedents["source"]),
                    )
                    covered_by = tuple(
                        relationship["id"] for relationship in relationships
                        if relationship.get("locationOutcome") == _LOCATED
                    )
                    finding_id = None
                    if status == SourceCoverage.POSSIBLY_MISSING:
                        kind = (
                            QaFindingKind.SOURCE_VARIANT_REVIEW if has_variant_evidence
                            else QaFindingKind.POSSIBLE_OMISSION
                        )
                        finding = self._build_finding(
                            kind=kind, direction=AuditDirection.SOURCE_COVERAGE,
                            source_unit_ids=(owner_id,), target_unit_ids=(),
                            relationship_ids=(), account_ids=(account["id"],),
                            dimension=str(dimension),
                            meaning_assessment_ids=(), location_outcome="NOT_LOCATED",
                            meaning_status="", explanation=reason, confidence=0.75,
                            resource_evidence_ids=tuple((owner_unit or {}).get("evidenceIds", [])),
                            supporting_evidence_ids=(), conflicting_evidence_ids=(),
                            source=source, target=target, fingerprint=fingerprint, policy_binding=policy_binding,
                            target_units=target_units, current_text=current_text,
                        )
                        self.repository.save_qa_finding(self._finding_to_dataclass(finding))
                        findings.append(finding)
                        finding_id = finding["id"]
                    self.repository.update_coverage_account_status(
                        account["id"], coverage_status=status.value,
                        covered_by_relationship_ids=covered_by, finding_id=finding_id,
                        expected_revision=self.repository.coverage_account(account["id"])["revision"],
                    )
                    source_coverage_ids.append(account["id"])

            with profiler.phase("meaningFailureAudit"):
                # --- Meaning-failure pass (item 10-11, 24, 27) ----------------------
                for relationship in location["relationships"]:
                    if relationship.get("locationOutcome") != _LOCATED:
                        continue
                    if relationship["id"] in seen_relationship_finding:
                        continue
                    assessment = assessments_by_relationship.get(relationship["id"])
                    if assessment is None:
                        continue
                    kind = self.policy.finding_kind_for(assessment)
                    if kind is None:
                        continue
                    seen_relationship_finding.add(relationship["id"])
                    semantic_relationship_id = self._save_semantic_relationship(
                        relationship, assessment, source, target, fingerprint,
                    )
                    confidence = float((assessment.get("meaningConfidence") or {}).get("calibratedValue") or 0.5)
                    finding = self._build_finding(
                        kind=kind, direction=AuditDirection.SOURCE_COVERAGE,
                        source_unit_ids=tuple(relationship.get("sourceSemanticUnitIds", [])),
                        target_unit_ids=tuple(relationship.get("targetSemanticUnitIds", [])),
                        relationship_ids=(semantic_relationship_id,), account_ids=(),
                        dimension=str(assessment.get("coverageDimension") or ""),
                        meaning_assessment_ids=(assessment["id"],),
                        location_outcome=relationship["locationOutcome"],
                        meaning_status=assessment.get("meaningStatus", ""),
                        explanation=assessment.get("explanation", ""), confidence=confidence,
                        resource_evidence_ids=(), supporting_evidence_ids=tuple(assessment.get("supportingEvidenceIds", [])),
                        conflicting_evidence_ids=tuple(assessment.get("conflictingEvidenceIds", [])),
                        # Only a real tN/tW/TWL disagreement, read from the
                        # assessment's own typed field (or proved from its
                        # component records when it predates that field).
                        resource_conflict_evidence_ids=resource_conflict_evidence_ids(assessment),
                        source=source, target=target, fingerprint=fingerprint, policy_binding=policy_binding,
                        target_units=target_units, current_text=current_text,
                    )
                    self.repository.save_qa_finding(self._finding_to_dataclass(finding))
                    findings.append(finding)

            with profiler.phase("targetSupportAudit"):
                # --- Target support pass (item 12-17) -------------------------------
                for unit in target["units"]:
                    if unit.get("accountingRole") != "PRIMARY" or unit.get("auditEligibility") != "ELIGIBLE":
                        continue
                    relationships = relationships_by_target_unit.get(unit["id"], [])
                    status, reason = self.policy.target_support_for(
                        unit, relationships, assessments_by_relationship,
                        null_precedent=self._unit_null(unit, null_precedents["target"]),
                    )
                    account = self._build_target_support_account(
                        unit, status, relationships, source, policy_binding,
                    )
                    self.repository.save_coverage_account(account)
                    target_support_ids.append(account.id)
                    finding_id = None
                    if status == TargetSupport.POSSIBLY_UNSUPPORTED:
                        finding = self._build_finding(
                            kind=QaFindingKind.POSSIBLE_ADDITION, direction=AuditDirection.TARGET_SUPPORT,
                            source_unit_ids=(), target_unit_ids=(unit["id"],),
                            relationship_ids=(), account_ids=(account.id,),
                            dimension=account.coverage_dimension.value,
                            meaning_assessment_ids=(), location_outcome="", meaning_status="",
                            explanation=reason, confidence=0.7,
                            resource_evidence_ids=(), supporting_evidence_ids=(), conflicting_evidence_ids=(),
                            source=source, target=target, fingerprint=fingerprint, policy_binding=policy_binding,
                            target_units=target_units, current_text=current_text,
                        )
                        self.repository.save_qa_finding(self._finding_to_dataclass(finding))
                        findings.append(finding)
                        finding_id = finding["id"]
                    # A comparison-policy bump deliberately re-runs Stage 8
                    # against the same stable target-unit identity.  The seed
                    # is idempotent so human review metadata survives, but the
                    # freshly derived status and relationship coverage must
                    # replace the old algorithm's result rather than letting
                    # it masquerade as current.
                    self.repository.update_coverage_account_status(
                        account.id,
                        coverage_status=status.value,
                        covered_by_relationship_ids=tuple(
                            relationship["id"] for relationship in relationships
                            if relationship.get("locationOutcome") == _LOCATED
                        ),
                        finding_id=finding_id,
                        expected_revision=self.repository.coverage_account(account.id)["revision"],
                    )
        finally:
            self.repository = real_repository

        diagnostics = self._diagnostics(source, target, findings)
        run_id = "qa-run-" + fingerprint[:32]
        payload = {
            "id": run_id, "book": self.book, "rangeKey": location["rangeKey"],
            "fingerprint": fingerprint, "meaningRunId": meaning["id"],
            "meaningRunFingerprint": meaning["fingerprint"],
            "locationRunId": location["id"], "sourceInventoryFingerprint": source["fingerprint"],
            "targetInventoryFingerprint": target["fingerprint"],
            "qaEngineVersion": QA_ENGINE_VERSION, "qaPolicyVersion": self.policy.version,
            "modelVersion": QA_MODEL_VERSION, "calibrationVersion": QA_CALIBRATION_VERSION,
            "runStatus": QaRunStatus.COMPLETE.value,
            "sourceCoverageAccountIds": source_coverage_ids,
            "targetSupportAccountIds": target_support_ids,
            "findings": findings, "diagnostics": diagnostics,
            "elapsedSeconds": time.perf_counter() - started, "cacheStatus": "MISS",
            "phaseProfile": profiler.report(),
        }
        self.repository.save_qa_audit_run(
            run_id=run_id, project_id=self.project_id, book=self.book,
            range_key=location["rangeKey"], fingerprint=fingerprint,
            meaning_run_id=meaning["id"], run_status=QaRunStatus.COMPLETE.value, payload=payload,
        )
        return payload

    def _null_precedents(
        self, chapter: str, verse: str, end_chapter: str, end_verse: str,
    ) -> dict[str, dict[str, dict[str, Any]]]:
        try:
            from .word_alignment_evidence import null_precedents_for_range
            return null_precedents_for_range(self.runtime, chapter, verse, end_chapter, end_verse)
        except Exception:
            return {"source": {}, "target": {}}

    @staticmethod
    def _unit_null(
        unit: dict[str, Any] | None, by_token: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        """The null decision covering a unit: every one of its tokens must carry
        one, with the same reason. A unit with an undecided token is not
        explained by a decision on its neighbour."""
        if not unit or not by_token:
            return None
        tokens = [str(token) for token in unit.get("tokenInstanceIds") or ()]
        if not tokens or any(token not in by_token for token in tokens):
            return None
        reasons = {by_token[token]["reason"] for token in tokens}
        if len(reasons) != 1:
            return None
        return by_token[tokens[0]]

    def _save_semantic_relationship(
        self, relationship: dict[str, Any], assessment: dict[str, Any],
        source: dict[str, Any], target: dict[str, Any], run_fingerprint: str,
    ) -> str:
        location_confidence = relationship.get("locationConfidence") or {
            "rawScore": None, "calibratedValue": 0.0,
            "confidencePolicyVersion": QA_CONFIDENCE_POLICY_VERSION,
            "calibrationVersion": QA_CALIBRATION_VERSION,
        }
        meaning_confidence = assessment.get("meaningConfidence") or {
            "rawScore": None, "calibratedValue": 0.0,
            "confidencePolicyVersion": QA_CONFIDENCE_POLICY_VERSION,
            "calibrationVersion": QA_CALIBRATION_VERSION,
        }
        relationship_id = "qa-relationship-" + _json_hash({
            "relationship": relationship["id"], "assessment": assessment["id"], "run": run_fingerprint,
        })[:32]
        instance = SemanticRelationship(
            id=relationship_id, project_id=self.project_id, book=self.book,
            source_semantic_unit_ids=tuple(relationship.get("sourceSemanticUnitIds", [])),
            target_semantic_unit_ids=tuple(relationship.get("targetSemanticUnitIds", [])),
            lexical_group_ids=(),
            realization=Realization(relationship.get("realization") or Realization.UNCERTAIN.value),
            properties=tuple(RelationshipProperty(item) for item in relationship.get("properties", [])),
            location_confidence=ConfidenceScore(
                raw_score=location_confidence.get("rawScore"),
                calibrated_value=location_confidence.get("calibratedValue") or 0.0,
                confidence_policy_version=str(location_confidence.get("confidencePolicyVersion") or QA_CONFIDENCE_POLICY_VERSION),
                calibration_version=str(location_confidence.get("calibrationVersion") or QA_CALIBRATION_VERSION),
            ),
            meaning_status=assessment.get("meaningStatus", "UNVERIFIABLE"),
            meaning_confidence=ConfidenceScore(
                raw_score=meaning_confidence.get("rawScore"),
                calibrated_value=meaning_confidence.get("calibratedValue") or 0.0,
                confidence_policy_version=str(meaning_confidence.get("confidencePolicyVersion") or QA_CONFIDENCE_POLICY_VERSION),
                calibration_version=str(meaning_confidence.get("calibrationVersion") or QA_CALIBRATION_VERSION),
            ),
            source_coverage=SourceCoverage.NOT_CHECKED, target_support=TargetSupport.NOT_CHECKED,
            evidence_ids=(), policy_binding=PolicyBinding.foundation_v1(),
            review_status=ReviewStatus.AI_PROPOSED, lifecycle_status=LifecycleStatus.ACTIVE,
        )
        self.repository.save_semantic_relationship(instance)
        return relationship_id

    def _build_target_support_account(
        self, unit: dict[str, Any], status: TargetSupport, relationships: list[dict[str, Any]],
        source: dict[str, Any], policy_binding: PolicyBinding,
    ) -> SemanticCoverageAccount:
        dimension = unit.get("coverageDimension") or "OTHER"
        account_fingerprint = _json_hash({
            "owner": unit["id"], "dimension": dimension, "policy": self.policy.version,
        })
        return SemanticCoverageAccount(
            id="target-coverage-" + _sha(source["id"] + account_fingerprint)[:32],
            project_id=self.project_id, passage_id=source["id"], direction=AuditDirection.TARGET_SUPPORT,
            audit_owner_unit_id=unit["id"], member_unit_ids=(unit["id"],),
            coverage_dimension=CoverageDimension(dimension), semantic_fingerprint=account_fingerprint,
            covered_by_relationship_ids=tuple(item["id"] for item in relationships),
            excluded_duplicate_unit_ids=(), finding_id=None, policy_binding=policy_binding,
            review_status=ReviewStatus.UNREVIEWED, lifecycle_status=LifecycleStatus.ACTIVE,
            coverage_status=status.value,
        )

    def _current_target_text(self) -> dict[str, str]:
        """Authoritative current Scripture: <project>/<book>/<chapter>.json.

        The same snapshot Stage 9B eligibility re-reads, and deliberately not
        the preserved imported USFM.  Imported here rather than at module
        scope: passage_semantic_runtime constructs this engine, so a top-level
        import would be a cycle.
        """
        from .passage_semantic_runtime import current_target_text

        return current_target_text(self.runtime.project)

    @staticmethod
    def _finding_anchors(
        source_unit_ids: tuple[str, ...], target_unit_ids: tuple[str, ...],
        source: dict[str, Any], target: dict[str, Any],
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Displayed references for the finding, and stable target anchors.

        A target unit's id embeds the per-verse targetRevision, so it changes
        whenever its verse is edited.  Anchoring instead on
        reference + normalized surface + occurrence keeps a target-support
        finding's identity stable when an unrelated word in the same verse
        changes, and only breaks it when the word the finding is actually
        about changes -- at which point it genuinely is a different finding.
        """
        references: list[str] = []
        anchors: list[str] = []
        by_id = {unit["id"]: unit for unit in source.get("units", ())}
        by_id.update({unit["id"]: unit for unit in target.get("units", ())})
        occurrences: Counter[str] = Counter()
        for unit_id in (*source_unit_ids, *target_unit_ids):
            unit = by_id.get(unit_id)
            if unit is None:
                continue
            references.extend(unit.get("displayedReferences") or ())
            if unit_id in target_unit_ids:
                reference = next(iter(unit.get("displayedReferences") or ()), "")
                surface = str(unit.get("normalizedSurface") or unit.get("rawSurface") or "")
                key = f"{reference}␟{surface}␟{unit.get('kind', '')}"
                occurrences[key] += 1
                anchors.append(f"{key}␟{occurrences[key]}")
        return tuple(dict.fromkeys(references)), tuple(anchors)

    @staticmethod
    def _stable_finding_id(
        *, kind: QaFindingKind, direction: AuditDirection,
        source_unit_ids: tuple[str, ...], target_anchors: tuple[str, ...],
        dimension: str = "",
    ) -> str:
        """Identity that survives a re-run, so human review survives with it.

        Deliberately excludes the run fingerprint and the engine/policy
        versions.  Keying on the run made every re-run mint new ids, orphaning
        every prior human decision; keying on policy would do the same on any
        policy bump.  Both are recorded as fields on the finding instead.
        Source unit ids are content-derived from the locked source resource,
        so they are stable across target edits by construction.
        """
        return "qa-finding-" + _json_hash({
            "kind": kind.value, "direction": direction.value, "dimension": dimension,
            "source": sorted(source_unit_ids), "target": list(target_anchors),
        })[:32]

    def _build_finding(
        self, *, kind: QaFindingKind, direction: AuditDirection, source_unit_ids: tuple[str, ...],
        target_unit_ids: tuple[str, ...], relationship_ids: tuple[str, ...], account_ids: tuple[str, ...],
        meaning_assessment_ids: tuple[str, ...], location_outcome: str, meaning_status: str,
        explanation: str, confidence: float, resource_evidence_ids: tuple[str, ...],
        dimension: str = "",
        supporting_evidence_ids: tuple[str, ...], conflicting_evidence_ids: tuple[str, ...],
        resource_conflict_evidence_ids: tuple[str, ...] = (),
        source: dict[str, Any], target: dict[str, Any], fingerprint: str, policy_binding: PolicyBinding,
        target_units: dict[str, Any], current_text: dict[str, str],
    ) -> dict[str, Any]:
        references, target_anchors = self._finding_anchors(
            source_unit_ids, target_unit_ids, source, target,
        )
        # Per-target-verse content hashes, never the Stage 6A range fingerprint
        # carried on `target["targetContentHash"]`.  See qa_target_hash.
        target_references = finding_target_references(
            target_unit_ids, references, target_units.get,
        )
        finding_id = self._stable_finding_id(
            kind=kind, direction=direction, source_unit_ids=source_unit_ids,
            target_anchors=target_anchors, dimension=dimension,
        )
        severity = self.policy.severity_for(kind, confidence)
        return {
            "id": finding_id, "projectId": self.project_id, "book": self.book,
            "kind": kind.value, "direction": direction.value,
            "sourceSemanticUnitIds": list(source_unit_ids), "targetSemanticUnitIds": list(target_unit_ids),
            "semanticRelationshipIds": list(relationship_ids), "coverageAccountIds": list(account_ids),
            "meaningAssessmentIds": list(meaning_assessment_ids),
            "locationOutcomeSnapshot": location_outcome, "meaningStatusSnapshot": meaning_status,
            "explanation": explanation, "severity": severity.value,
            "confidence": {
                "rawScore": confidence, "calibratedValue": confidence,
                "confidencePolicyVersion": QA_CONFIDENCE_POLICY_VERSION,
                "calibrationVersion": QA_CALIBRATION_VERSION,
            },
            "currentTargetRevision": target.get("targetRevision", ""),
            "displayedReferences": list(references),
            "resourceEvidenceIds": list(resource_evidence_ids),
            "supportingEvidenceIds": list(supporting_evidence_ids),
            # Meaning-failure evidence.  Kept distinct from the field below:
            # "the target says something else" is the error a correction fixes,
            # while a resource conflict is a decision a human owes first.
            "conflictingEvidenceIds": list(conflicting_evidence_ids),
            "resourceConflictEvidenceIds": list(resource_conflict_evidence_ids),
            "targetContentHashes": list(target_content_hashes(target_references, current_text)),
            "sourceResourceHashes": [str(
                ((source.get("sourceResource") or {}).get("resourceHash"))
                or ((source.get("sourceResource") or {}).get("hash")) or ""
            )],
            "qaEngineVersion": QA_ENGINE_VERSION, "qaPolicyVersion": self.policy.version,
            "fingerprint": fingerprint, "qaDisposition": QaDisposition.UNRESOLVED.value,
            "policyBinding": {
                "confidencePolicyVersion": QA_CONFIDENCE_POLICY_VERSION,
                "calibrationVersion": QA_CALIBRATION_VERSION, "auditPolicyVersion": self.policy.version,
            },
            "reviewStatus": ReviewStatus.AI_PROPOSED.value, "lifecycleStatus": LifecycleStatus.ACTIVE.value,
            "revision": 1,
        }

    def _finding_to_dataclass(self, finding: dict[str, Any]) -> QaFinding:
        confidence = finding["confidence"]
        return QaFinding(
            id=finding["id"], project_id=finding["projectId"], book=finding["book"],
            passage_id=finding["book"], kind=QaFindingKind(finding["kind"]),
            direction=AuditDirection(finding["direction"]),
            source_semantic_unit_ids=tuple(finding["sourceSemanticUnitIds"]),
            target_semantic_unit_ids=tuple(finding["targetSemanticUnitIds"]),
            semantic_relationship_ids=tuple(finding["semanticRelationshipIds"]),
            evidence_ids=tuple(finding["resourceEvidenceIds"]), explanation=finding["explanation"],
            confidence=ConfidenceScore(
                raw_score=confidence["rawScore"], calibrated_value=confidence["calibratedValue"],
                confidence_policy_version=confidence["confidencePolicyVersion"],
                calibration_version=confidence["calibrationVersion"],
            ),
            current_target_revision=finding["currentTargetRevision"],
            qa_disposition=QaDisposition(finding["qaDisposition"]),
            policy_binding=PolicyBinding(
                confidence_policy_version=finding["policyBinding"]["confidencePolicyVersion"],
                calibration_version=finding["policyBinding"]["calibrationVersion"],
                audit_policy_version=finding["policyBinding"]["auditPolicyVersion"],
            ),
            review_status=ReviewStatus(finding["reviewStatus"]),
            lifecycle_status=LifecycleStatus(finding["lifecycleStatus"]),
            severity=QaFindingSeverity(finding["severity"]),
            meaning_assessment_ids=tuple(finding["meaningAssessmentIds"]),
            coverage_account_ids=tuple(finding["coverageAccountIds"]),
            location_outcome_snapshot=finding["locationOutcomeSnapshot"],
            meaning_status_snapshot=finding["meaningStatusSnapshot"],
            supporting_evidence_ids=tuple(finding["supportingEvidenceIds"]),
            conflicting_evidence_ids=tuple(finding["conflictingEvidenceIds"]),
            resource_conflict_evidence_ids=tuple(finding.get("resourceConflictEvidenceIds") or ()),
            resource_evidence_ids=tuple(finding["resourceEvidenceIds"]),
            target_content_hashes=tuple(finding["targetContentHashes"]),
            source_resource_hashes=tuple(finding["sourceResourceHashes"]),
            qa_engine_version=finding["qaEngineVersion"], qa_policy_version=finding["qaPolicyVersion"],
            fingerprint=finding["fingerprint"], revision=finding["revision"],
            displayed_references=tuple(finding.get("displayedReferences") or ()),
        )

    @staticmethod
    def _diagnostics(source: dict[str, Any], target: dict[str, Any], findings: list[dict[str, Any]]) -> dict[str, Any]:
        kind_counts = Counter(item["kind"] for item in findings)
        severity_counts = Counter(item["severity"] for item in findings)
        return {
            "sourcePrimaryObligationsAudited": sum(
                1 for account in source.get("coverageAccounts", [])
            ),
            "targetSupportUnitsAudited": sum(
                1 for unit in target["units"]
                if unit.get("accountingRole") == "PRIMARY" and unit.get("auditEligibility") == "ELIGIBLE"
            ),
            "findingsByType": dict(kind_counts), "findingsBySeverity": dict(severity_counts),
            "totalFindings": len(findings),
            "resourceConflicts": kind_counts.get(QaFindingKind.RESOURCE_CONFLICT.value, 0),
            "variantReviews": kind_counts.get(QaFindingKind.SOURCE_VARIANT_REVIEW.value, 0),
        }

    def status(self, run_id: str) -> dict[str, Any]:
        run = self.repository.qa_audit_run(run_id)
        return {key: run[key] for key in ("id", "runStatus", "diagnostics", "cacheStatus")}

    def get_range(self, run_id: str) -> dict[str, Any]:
        return self.repository.qa_audit_run(run_id)

    def get_source_coverage(self, run_id: str) -> list[dict[str, Any]]:
        run = self.repository.qa_audit_run(run_id)
        return [self.repository.coverage_account(item) for item in run["sourceCoverageAccountIds"]]

    def get_target_support(self, run_id: str) -> list[dict[str, Any]]:
        run = self.repository.qa_audit_run(run_id)
        return [self.repository.coverage_account(item) for item in run["targetSupportAccountIds"]]

    def get_finding(self, finding_id: str) -> dict[str, Any]:
        return self.repository.qa_finding(finding_id)

    def get_diagnostics(self, run_id: str) -> dict[str, Any]:
        return self.get_range(run_id)["diagnostics"]
