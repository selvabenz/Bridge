"""
BridgeEngine: the single sidecar dispatcher for Bridge.

Composes two things behind ONE protocol:
  1. GreekRoomEngine       — offline QA adapters (Wildebeest, OWL, ...)
  2. tc_ai_bridge          — the existing, working business logic
                             (project reading, alignment, local QA,
                             settings/secrets, transaction journal,
                             Paratext/Logos connectors)

Per the architecture doc: this replaces `ui.py` as the thing that calls
tc_ai_bridge's modules. Nothing in tc_ai_bridge itself was rewritten —
only wrapped. See docs/ARCHITECTURE.md for the reasoning.
"""
from __future__ import annotations

import atexit
import copy
import hashlib
import json
import uuid
import os
import re
import shutil
import sys
import threading
import time
import unicodedata
import uuid
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from greek_room_engine.engine import GreekRoomEngine
from greek_room_engine.adapters.usfm_adapter import UsfmCheckerCancelled, UsfmCheckerError
from greek_room_engine.adapters.names_adapter import NamesCheckError
from greek_room_engine.models.finding import QaFinding, FindingCategory, Severity, FindingStatus, EvidenceItem
from greek_room_engine.protocol import EngineRequest, EngineResponse

from tc_ai_bridge.version import BRIDGE_VERSION as _BRIDGE_VERSION
from tc_ai_bridge.tc_project import (
    TranslationCoreProject, ProjectError, peek_learned_fixes, peek_progress_totals, read_triage_records,
)
from tc_ai_bridge.project_import import (
    apply_resource_materialization,
    collection_projects,
    collection_qa_runs,
    ensure_bridge_original_language,
    import_source,
    inspect_import,
    materialize_lazy_project,
    record_collection_qa_run,
)
from collection_jobs import CollectionJobConflict, CollectionJobError, CollectionJobManager, CollectionJobSpec
from tc_ai_bridge.housestyle import (
    LEARN_IGNORES, PREFER_USES, PROPOSE_PROJECT_BOOKS, PROPOSE_RULE_DECISIONS, PROPOSE_RULE_IGNORE_RATE,
    HouseStyleLearner, bundled_seed, name_suggestions, project_proposals,
)
from tc_ai_bridge.original_language_resources import resource_inventory
from tc_ai_bridge.lexicon_resources import lexicon_entry_for_strong, HEBREW_PREFIX_LABELS
from tc_ai_bridge.morphology_codes import decode_morph
from tc_ai_bridge.project_registry import (
    ProjectIdentityError, ProjectRegistry, canonical_path_key, source_fingerprints,
)
from tc_ai_bridge.passage_semantic_repository import (
    DATABASE_SCHEMA_VERSION,
    FoundationConflict,
    FoundationValidationError,
)
from tc_ai_bridge.passage_semantic_runtime import PassageSemanticRuntime
from tc_ai_bridge.analysis_jobs import (
    AnalysisJobConflict,
    AnalysisJobError,
    AnalysisJobManager,
    AnalysisJobNotFound,
)
from tc_ai_bridge.local_checks import run_local_qa
from tc_ai_bridge.language_qa import FINDING_SOURCE as LANGUAGE_QA_SOURCE, UNSPECIFIED_DECISION_SOURCE
from tc_ai_bridge.language_qa_jobs import LanguageQaManager, project_pack_name, project_rule_pack
from tc_ai_bridge import check_timing
from tc_ai_bridge import language_qa_checker_settings as checker_settings_rpc
from tc_ai_bridge.language_packs import indic_qa_adapter
from tc_ai_bridge.language_qa_learned import learned_pair
from tc_ai_bridge import language_qa_scope
from tc_ai_bridge.language_qa import text_hash as language_qa_text_hash
from tc_ai_bridge.language_qa import word_occurrences as language_qa_word_occurrences
from tc_ai_bridge.language_qa import lift_inline_usfm as language_qa_lift
from tc_ai_bridge.language_packs.reference_text import ReferenceHolder, detect as detect_reference
from tc_ai_bridge.language_packs.related_words import RelatedWords
from tc_ai_bridge.language_packs.registry import packs_dir
from tc_ai_bridge.language_packs.registry import available as available_language_packs
from tc_ai_bridge.workbench_repository import WorkbenchConflict, WorkbenchValidationError, natural_row_id
from tc_ai_bridge.alignment_engine import (
    AlignmentError, apply_proposal, make_inventory, realign, unalign_bottom,
    validate_preparation_proposal,
)
from tc_ai_bridge.aligned_usfm import AlignedUsfmError, render_aligned_verse
from tc_ai_bridge.alignment_reliability import (
    alignment_fingerprint, compile_link_proposal, structural_issues, target_token_fingerprint,
)
from tc_ai_bridge.ai_client import AIError, OpenAIResponsesClient, Transport
from tc_ai_bridge.correction_wording import (
    ConfiguredCorrectionSuggestionProvider,
    CorrectionWordingService,
)
from tc_ai_bridge.correction_application import CorrectionApplicationService
from tc_ai_bridge.correction_affected_analysis import CorrectionAffectedAnalysisService
from tc_ai_bridge.correction_verification import CorrectionVerificationService
from tc_ai_bridge.knowledge_base import KnowledgeBaseError
from tc_ai_bridge.paratext_connector import ParatextConnectorClient, ParatextConnectorError
from tc_ai_bridge.logos_connector import LogosConnectorClient, LogosConnectorError
from tc_ai_bridge.navigation import NavigationSyncCoordinator
from tc_ai_bridge.models import QAIssue, TokenRef, VerseAlignment
from tc_ai_bridge.secret_store import AppSettings
from tc_ai_bridge.workspace_repository import WorkspaceRepository, project_path_key
from tc_ai_bridge.resource_materializer import materialize_book_checks
from tc_ai_bridge.usfm import strip_usfm, whitespace_tokens
from tc_ai_bridge.usfm_verse import WHITESPACE_TOKEN_TRIM_CHARS, lift_verse
from tc_ai_bridge.usfm_parser import UsfmParseError, parse_usfm, read_usfm_text
from tc_ai_bridge import versification as versification_tool
from tc_ai_bridge import alignment_gaps
from tc_ai_bridge import alignment_null_decisions as null_decisions_tool
from tc_ai_bridge import cross_verse_links as cross_verse_tool
from tc_ai_bridge import alignment_agreement, alignment_gap_checks, alignment_window
from tc_ai_bridge.plugins import PluginRegistry
from tc_ai_bridge import alignment_statistics as corpus_stats_tool
from tc_ai_bridge import cross_verse_proposals, cross_verse_ai_proposals
from tc_ai_bridge.reporting import LANGUAGE_QA_MEDIUM_ADVISORY, ReportService, publication_gate
from tc_ai_bridge.qa_report import (
    aggregate_qa_report,
    build_book_qa_report,
    unopened_book_report,
    write_report_rows,
)
from tc_ai_bridge.triage import (
    OVERRIDE_VERDICTS,
    run_book_triage,
)
from tc_ai_bridge.semantic_review_policy import native_tc_apply_allowed
from check_jobs import (
    CheckJobConflict,
    CheckJobError,
    CheckJobManager,
    CheckJobNotFound,
    CheckJobSpec,
    LANGUAGE_QA_CHECK,
)
import alignment_auto_align_jobs as auto_align_jobs
from tc_ai_bridge.model_router import estimate_cost
from ai_review_jobs import (
    AIReviewJobConflict,
    AIReviewJobError,
    AIReviewJobManager,
    AIReviewJobNotFound,
    AIReviewJobSpec,
)
from report_jobs import (
    ReportBook,
    ReportJobConflict,
    ReportJobError,
    ReportJobManager,
    ReportJobNotFound,
    ReportNotReady,
)
from triage_jobs import (
    TriageBook,
    TriageJobConflict,
    TriageJobError,
    TriageJobManager,
    TriageJobNotFound,
)

# Re-exported, not redefined: project_import stamps the same number into each
# imported project's manifest (#176), and a second literal here is exactly how
# 0.12.0 shipped a sidecar still reporting 0.11.0 (#170). Kept in step with
# package.json by tests/service/test_version_consistency.py.
BRIDGE_VERSION = _BRIDGE_VERSION

# tc_ai_bridge's QAIssue.severity strings -> our shared Severity enum
_SEVERITY_MAP = {
    "critical": Severity.HIGH,
    "high": Severity.HIGH,
    "medium": Severity.MEDIUM,
    "editorial": Severity.LOW,
    "info": Severity.INFO,
}


def _trace(message: str) -> None:
    """One diagnostic line on stderr.

    stdout is the JSON-lines protocol and must stay clean; stderr is relayed by
    the Rust shell (sidecar.rs) into the diagnostics panel and
    ``engine-events.log`` under the app log directory, which is where a
    tester looking for "why did that open take a minute" can find it.
    """
    print(f"[trace] {message}", file=sys.stderr, flush=True)


class _PhaseTimer:
    """Wall-clock seconds per named phase of one request, for `_trace` lines."""

    def __init__(self) -> None:
        self._start = time.perf_counter()
        self._last = self._start
        self.phases: list[tuple[str, float]] = []

    def mark(self, phase: str) -> None:
        now = time.perf_counter()
        self.phases.append((phase, now - self._last))
        self._last = now

    def summary(self) -> str:
        total = time.perf_counter() - self._start
        return f"total={total:.2f}s " + " ".join(f"{p}={s:.2f}s" for p, s in self.phases)


def _termbase_coverage(book_id: str, rules: list[dict[str, Any]], text_map: dict[str, str]) -> dict[str, Any]:
    """One book's termbase coverage for the collection report (Phase 4.4):
    approved renderings the book never uses, and rejected ones still present.
    A substring count over the verse text: Tamil attaches case endings to the
    rendering, so a whole-token match would miss inflected uses."""
    text = "\n".join(text_map.values())
    issues = []
    for rule in rules:
        approved = [r for r in rule.get("approvedRenderings") or [] if isinstance(r, str) and r]
        rejected = [r for r in rule.get("rejectedRenderings") or [] if isinstance(r, str) and r]
        never_seen = [r for r in approved if r not in text]
        still_present = [{"rendering": r, "count": text.count(r)} for r in rejected if r in text]
        if never_seen or still_present:
            issues.append({"conceptId": str(rule.get("conceptId") or ""),
                           "approvedNeverSeen": never_seen, "rejectedStillPresent": still_present})
    return {"bookId": book_id, "concepts": len(rules), "issues": issues}


def _stable_finding_id(*, chapter: str, verse: str, engine: str,
                        check_type: str, disambiguator: str = "") -> str:
    """Deterministic finding id, NOT a random uuid4.

    QaFinding previously defaulted to uuid4() ids, meaning the SAME finding
    got a DIFFERENT id every time verse.runChecks was called — so a saved
    decision (keyed by finding id) could never be matched back to the
    finding it was made on next time checks ran. This makes ids stable
    across runs as long as the underlying finding is the same (same
    chapter/verse/engine/check_type/disambiguator).
    """
    key = f"{chapter}:{verse}:{engine}:{check_type}:{disambiguator}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]


def verse_display(text: str) -> dict[str, Any]:
    """The display payload for one stored verse string (#91 Phase 2):
    `{plain, notes, removed, styles, warnings}` from the fragment reader, all
    offsets in code points. Attached wherever the protocol hands the frontend
    a verse's text, so the frontend never parses USFM."""
    return lift_verse(text).to_dict()


def _first_token_span(text: str, token: str) -> Optional[tuple[int, int]]:
    """Locate `token`'s first whole-word occurrence in the raw verse string,
    as code-point offsets into that string -- the one a QaFinding's
    start_offset/end_offset is highlighted against on the frontend.

    The token came from `whitespace_tokens`, i.e. from the visible text, so
    it is looked for there with the same boundary rule and the fragment
    reader maps the span back to raw offsets (#91 Phase 1b). A token that
    sits against markup (`\\nd Lord\\nd*,`) is found this way where a scan of
    the raw string could not see it; one whose span would cross removed
    markup has no single raw span and gets None, as before."""
    lifted = lift_verse(text)
    for match in re.finditer(r"\S+", lifted.plain):
        chunk = match.group()
        stripped = chunk.strip(WHITESPACE_TOKEN_TRIM_CHARS)
        if stripped != token:
            continue
        start = match.start() + chunk.index(stripped)
        return lifted.raw_span(start, start + len(stripped))
    return None


def _categorize_qaissue(issue: QAIssue) -> FindingCategory:
    """Maps tc_ai_bridge's QAIssue codes to our shared FindingCategory,
    based on the real code/title patterns in local_checks.py — NOT a
    guess. 'translationWords'/'translationNotes' only appear baked into
    the title string (QAIssue has no separate `tool` field), so we check
    both code prefix and title text."""
    code = issue.code
    title = issue.title.lower()

    if code.startswith("ALIGN_") or code == "WA_INVALID":
        return FindingCategory.ALIGNMENT
    if code.startswith("USFM_"):
        return FindingCategory.STRUCTURE
    if code.endswith("_REPEAT_WORD"):
        return FindingCategory.REPETITION
    if code.endswith("_DOUBLE_SPACE") or code.endswith("_HIDDEN_CHAR"):
        return FindingCategory.UNICODE
    if code in ("TC_INVALIDATED", "TC_STALE_AFTER_EDIT", "TC_PENDING"):
        if "translationwords" in title:
            return FindingCategory.TRANSLATION_WORD
        if "translationnotes" in title:
            return FindingCategory.TRANSLATION_NOTE
        return FindingCategory.CONSISTENCY
    return FindingCategory.CONSISTENCY


def _qaissue_to_finding(issue: QAIssue, *, project_id: str, book: str,
                         chapter: str, verse: str,
                         resource_versions: dict[str, str] | None = None) -> QaFinding:
    """Adapts tc_ai_bridge's QAIssue (tN/tW/local QA) into the same
    QaFinding shape Greek Room findings use, so the UI never has to know
    which engine produced a given finding (architecture doc §6)."""
    engine_name = issue.source or "local"
    stable_id = _stable_finding_id(
        chapter=chapter, verse=verse, engine=engine_name,
        check_type=issue.check_id or issue.code,
        # disambiguates multiple issues of the same check_type in one verse
        disambiguator=issue.group_id or issue.detail,
    )
    return QaFinding(
        id=stable_id,
        project_id=project_id,
        # USFM permits verse bridges (for example 3-4) and segments (3a).
        # QaFinding currently stores numeric anchors, while the project/UI
        # retain the exact string reference. Use the first numeric component
        # so valid Scripture does not crash the checking pass.
        book=book,
        chapter=int(next(iter(re.findall(r"\d+", str(chapter))), "0")),
        verse=int(next(iter(re.findall(r"\d+", str(verse))), "0")),
        engine=engine_name,
        check_type=issue.check_id or issue.code,
        category=_categorize_qaissue(issue),
        severity=_SEVERITY_MAP.get(issue.severity, Severity.MEDIUM),
        confidence=issue.confidence if issue.confidence is not None else 0.5,
        explanation=f"{issue.title} — {issue.detail}".strip(" —"),
        engine_version=BRIDGE_VERSION,
        resource_versions=dict(resource_versions) if resource_versions else {},
        # A local check's one-click fix (#203): raw code-point span plus the
        # replacement, the same contract names/Language QA fixes use.
        start_offset=issue.start_offset,
        end_offset=issue.end_offset,
        original_text=issue.original_text,
        suggested_replacement=issue.suggested_replacement,
    )


class Methods:
    PING = "ping"
    ENGINE_INFO = "engine.info"

    PROJECT_OPEN = "project.open"
    PROJECT_LIST = "project.list"
    PROJECT_LIST_BOOK_PROGRESS = "project.listBookProgress"
    PROJECT_FORGET = "project.forget"
    PROJECT_DELETE = "project.delete"
    PROJECT_SCAN = "project.scan"
    PROJECT_REPORT = "project.report"
    PROJECT_COLLECTION_REPORT = "project.collectionReport"
    # Whole-collection QA report (qa_report.py + report_jobs.py): a
    # background build polled by status, fetched once with get, and an
    # export of whatever rows the report screen has filtered down to.
    REPORT_GENERATE = "report.generate"
    REPORT_STATUS = "report.status"
    REPORT_GET = "report.get"
    REPORT_CANCEL = "report.cancel"
    REPORT_EXPORT = "report.export"
    # Optional, online-only AI triage of Greek Room findings (triage.py +
    # triage_jobs.py). An overlay on the report: it scores how likely each
    # finding is to be a false positive so the report screen can hide the
    # noisiest ones. Nothing in the offline check/report flow depends on it.
    TRIAGE_RUN = "triage.run"
    TRIAGE_STATUS = "triage.status"
    TRIAGE_CANCEL = "triage.cancel"
    TRIAGE_OVERRIDE = "triage.override"
    TRIAGE_CLEAR = "triage.clear"
    TRIAGE_RESULTS = "triage.results"
    PROJECT_INSPECT_IMPORT = "project.inspectImport"
    PROJECT_IMPORT = "project.import"
    CHAPTER_VERSES = "chapter.verses"
    CHAPTER_VERSE_DATA = "chapter.verseData"

    CHECKS_START = "checks.start"
    HOUSESTYLE_LIST = "housestyle.list"
    HOUSESTYLE_NAME_SUGGESTIONS = "housestyle.nameSuggestions"
    HOUSESTYLE_RECORD = "housestyle.record"
    HOUSESTYLE_SET_STATE = "housestyle.setState"
    HOUSESTYLE_EXPORT = "housestyle.export"
    HOUSESTYLE_IMPORT = "housestyle.import"
    COLLECTION_RUN_CHECKS = "collection.runChecks"
    COLLECTION_QA_STATUS = "collection.qaStatus"
    COLLECTION_PAUSE_CHECKS = "collection.pauseChecks"
    COLLECTION_CANCEL_CHECKS = "collection.cancelChecks"
    LANGUAGE_QA_STATUS = "languageQa.status"
    LANGUAGE_QA_PAUSE = "languageQa.pause"
    LANGUAGE_QA_INLINE = "languageQa.inline"
    LANGUAGE_QA_HISTORY = "languageQa.history"
    LANGUAGE_QA_VERSE = "languageQa.verse"
    LANGUAGE_QA_SET_PACK = "languageQa.setPack"
    LANGUAGE_QA_CHECKER_SETTINGS_GET = "languageQa.checkerSettings.get"
    LANGUAGE_QA_CHECKER_SETTINGS_SET = "languageQa.checkerSettings.set"
    LANGUAGE_QA_LEARNED_LIST = "languageQa.learned.list"
    LANGUAGE_QA_LEARNED_FORGET = "languageQa.learned.forget"
    LANGUAGE_QA_LEARNED_RESTORE = "languageQa.learned.restore"
    LANGUAGE_QA_SCOPE_FIND = "languageQa.scopeFind"
    LANGUAGE_QA_SCOPE_APPLY = "languageQa.scopeApply"
    LANGUAGE_QA_BATCH_UNDO = "languageQa.batchUndo"
    LANGUAGE_QA_BATCHES = "languageQa.batches"
    LANGUAGE_QA_FLAGS_LIST = "languageQa.flags.list"
    LANGUAGE_QA_FLAGS_ADD = "languageQa.flags.add"
    LANGUAGE_QA_FLAGS_UPDATE = "languageQa.flags.update"
    LANGUAGE_QA_FLAGS_DELETE = "languageQa.flags.delete"
    LANGUAGE_QA_WORDS_ADD = "languageQa.words.add"
    LANGUAGE_QA_WORDS_LIST = "languageQa.words.list"
    LANGUAGE_QA_BOOK_WORDS = "languageQa.bookWords"
    LANGUAGE_QA_OCCURRENCES = "languageQa.occurrences"
    LANGUAGE_QA_REFERENCE = "languageQa.reference"
    LANGUAGE_QA_RELATED = "languageQa.related"
    CHECKS_STATUS = "checks.status"
    CHECKS_CANCEL = "checks.cancel"
    CHECKS_RETRY = "checks.retry"

    VERSE_GET = "verse.get"
    VERSE_RUN_CHECKS = "verse.runChecks"
    VERSE_DECIDE = "verse.decide"
    VERSE_EDIT = "verse.edit"
    VERSE_HISTORY = "verse.history"

    CHECK_LIST_FOR_VERSE = "check.listForVerse"
    CHECK_VALIDATE_SELECTION = "check.validateSelection"
    CHECK_SAVE_SELECTION = "check.saveSelection"
    CHECK_CLEAR_SELECTION = "check.clearSelection"

    ALIGNMENT_GET = "alignment.get"
    ALIGNMENT_GET_RANGE = "alignment.getRange"
    ALIGNMENT_GAP_SCAN = "alignment.gapScan"
    ALIGNMENT_CROSS_VERSE_PROPOSE = "alignment.crossVerse.propose"
    ALIGNMENT_CROSS_VERSE_AI_PROPOSE = "alignment.crossVerse.aiPropose"
    ALIGNMENT_CROSS_VERSE_LINK = "alignment.crossVerse.link"
    ALIGNMENT_CROSS_VERSE_UNLINK = "alignment.crossVerse.unlink"
    ALIGNMENT_NULL_SET = "alignment.null.set"
    ALIGNMENT_NULL_CLEAR = "alignment.null.clear"
    ALIGNMENT_WINDOW_AUTO_ALIGN = "alignment.window.autoAlign"
    ALIGNMENT_AUTO_ALIGN_REVERT = "alignment.autoAlign.revert"
    ALIGNMENT_AUTO_ALIGN_VERDICT = "alignment.autoAlign.verdict"
    ALIGNMENT_AUTO_ALIGN_START = "alignment.autoAlign.start"
    ALIGNMENT_AUTO_ALIGN_STATUS = "alignment.autoAlign.status"
    ALIGNMENT_AUTO_ALIGN_CANCEL = "alignment.autoAlign.cancel"
    ALIGNMENT_AUTO_ALIGN_RETRY = "alignment.autoAlign.retry"
    ALIGNMENT_AUTO_ALIGN_ESTIMATE = "alignment.autoAlign.estimate"
    ALIGNMENT_STATUS = "alignment.status"
    ALIGNMENT_REALIGN = "alignment.realign"
    ALIGNMENT_UNALIGN = "alignment.unalign"
    ALIGNMENT_SAVE = "alignment.save"
    ALIGNMENT_COMPLETE = "alignment.complete"
    ALIGNMENT_UNDO = "alignment.undo"
    ALIGNMENT_BACKUPS = "alignment.backups"
    ALIGNMENT_RESTORE = "alignment.restore"

    LEXICON_GET_ENTRY = "lexicon.getEntry"

    SETTINGS_GET = "settings.get"
    SETTINGS_SET = "settings.set"

    TERMINOLOGY_LIST = "terminology.list"
    TERMINOLOGY_RECORD = "terminology.record"

    EXPORT_ALIGNED = "export.aligned"
    EXPORT_NON_ALIGNED = "export.nonAligned"

    ALIGNMENT_AI_PROPOSE = "alignment.aiPropose"
    ALIGNMENT_AI_APPLY_PROPOSAL = "alignment.aiApplyProposal"

    AI_REVIEW_START = "ai.review.start"
    AI_REVIEW_STATUS = "ai.review.status"
    AI_REVIEW_CANCEL = "ai.review.cancel"
    AI_REVIEW_RETRY = "ai.review.retry"
    AI_REVIEW_LIST_CHAPTER = "ai.review.listForChapter"

    PASSAGE_SEMANTIC_STATUS = "passageSemantic.status"
    PASSAGE_SEMANTIC_PROJECT_METADATA = "passageSemantic.getProjectMetadata"
    PASSAGE_SEMANTIC_CURRENT_PASSAGE = "passageSemantic.getCurrentPassage"
    PASSAGE_SEMANTIC_STALE_SUMMARY = "passageSemantic.getStaleSummary"
    PASSAGE_SEMANTIC_MIGRATION_REPORT = "passageSemantic.getMigrationReport"
    PASSAGE_SEMANTIC_REBUILD_PASSAGE = "passageSemantic.rebuildCurrentPassage"
    SOURCE_SEMANTIC_BUILD_RANGE = "sourceSemantic.buildRange"
    SOURCE_SEMANTIC_GET_RANGE = "sourceSemantic.getRange"
    SOURCE_SEMANTIC_GET_UNIT = "sourceSemantic.getUnit"
    SOURCE_SEMANTIC_GET_COVERAGE_ACCOUNTS = "sourceSemantic.getCoverageAccounts"
    SOURCE_SEMANTIC_GET_DIAGNOSTICS = "sourceSemantic.getDiagnostics"
    TARGET_SEMANTIC_BUILD_RANGE = "targetSemantic.buildRange"
    TARGET_SEMANTIC_GET_RANGE = "targetSemantic.getRange"
    TARGET_SEMANTIC_GET_UNIT = "targetSemantic.getUnit"
    TARGET_SEMANTIC_GET_DIAGNOSTICS = "targetSemantic.getDiagnostics"
    TARGET_SEMANTIC_GET_SEARCH_SPANS = "targetSemantic.getSearchSpans"
    TARGET_SEMANTIC_GET_CAPABILITIES = "targetSemantic.getCapabilities"
    SEMANTIC_LOCATION_RUN_RANGE = "semanticLocation.runRange"
    SEMANTIC_LOCATION_STATUS = "semanticLocation.status"
    SEMANTIC_LOCATION_GET_RANGE = "semanticLocation.getRange"
    SEMANTIC_LOCATION_GET_RELATIONSHIP = "semanticLocation.getRelationship"
    SEMANTIC_LOCATION_GET_CANDIDATES = "semanticLocation.getCandidates"
    SEMANTIC_LOCATION_GET_DIAGNOSTICS = "semanticLocation.getDiagnostics"
    MEANING_ANALYSIS_RUN_RANGE = "meaningAnalysis.runRange"
    MEANING_ANALYSIS_STATUS = "meaningAnalysis.status"
    MEANING_ANALYSIS_GET_RANGE = "meaningAnalysis.getRange"
    MEANING_ANALYSIS_GET_ASSESSMENT = "meaningAnalysis.getAssessment"
    MEANING_ANALYSIS_GET_COMPONENTS = "meaningAnalysis.getComponents"
    MEANING_ANALYSIS_GET_DIAGNOSTICS = "meaningAnalysis.getDiagnostics"
    QA_AUDIT_RUN_RANGE = "qaAudit.runRange"
    QA_AUDIT_STATUS = "qaAudit.status"
    QA_AUDIT_GET_RANGE = "qaAudit.getRange"
    QA_AUDIT_GET_SOURCE_COVERAGE = "qaAudit.getSourceCoverage"
    QA_AUDIT_GET_TARGET_SUPPORT = "qaAudit.getTargetSupport"
    QA_AUDIT_GET_FINDING = "qaAudit.getFinding"
    QA_AUDIT_GET_DIAGNOSTICS = "qaAudit.getDiagnostics"
    QA_REVIEW_GET_QUEUE = "qaReview.getQueue"
    QA_REVIEW_GET_FINDING = "qaReview.getFinding"
    QA_REVIEW_DECIDE_FINDING = "qaReview.decideFinding"
    QA_REVIEW_ADD_NOTE = "qaReview.addNote"
    # Stage 9B correction data surface. Apply is the explicit-human-only
    # Stage 9B.3b operation; it never starts affected analysis.
    CORRECTION_GET_ELIGIBILITY = "correction.getEligibility"
    CORRECTION_GET_REVIEW_CONTEXT = "correction.getReviewContext"
    CORRECTION_GET_PROPOSAL = "correction.getProposal"
    CORRECTION_LIST_FOR_FINDING = "correction.listForFinding"
    CORRECTION_CREATE_PROPOSAL = "correction.createProposal"
    CORRECTION_EDIT_PROPOSAL = "correction.editProposal"
    CORRECTION_REJECT_PROPOSAL = "correction.rejectProposal"
    CORRECTION_REGENERATE_PROPOSAL = "correction.regenerateProposal"
    CORRECTION_GET_PROPOSAL_HISTORY = "correction.getProposalHistory"
    CORRECTION_APPLY_PROPOSAL = "correction.applyProposal"
    CORRECTION_GET_APPLICATION_STATUS = "correction.getApplicationStatus"
    CORRECTION_REANALYZE_AFFECTED = "correction.reanalyzeAffected"
    CORRECTION_VERIFY_APPLICATION = "correction.verifyApplication"
    CORRECTION_GET_VERIFICATION = "correction.getVerification"
    CORRECTION_ACKNOWLEDGE_CORRECTED = "correction.acknowledgeCorrected"
    SEMANTIC_REVIEW_DECIDE_LOCATION = "semanticReview.decideLocation"
    SEMANTIC_REVIEW_DECIDE_MEANING = "semanticReview.decideMeaning"
    REVIEW_HISTORY_GET_ENTITY_HISTORY = "reviewHistory.getEntityHistory"
    ANALYSIS_JOB_START = "analysisJob.start"
    ANALYSIS_JOB_STATUS = "analysisJob.status"
    ANALYSIS_JOB_CANCEL = "analysisJob.cancel"
    ANALYSIS_JOB_GET_RECENT = "analysisJob.getRecent"
    ANALYSIS_JOB_GET_SCOPE_STATUS = "analysisJob.getScopeStatus"

    PARATEXT_GET_STATE = "paratext.getState"
    PARATEXT_SET_REFERENCE = "paratext.setReference"

    ISSUE_RESOLUTION_LIST = "issueResolution.list"
    ISSUE_RESOLUTION_SAVE = "issueResolution.save"
    ISSUE_RESOLUTION_QUEUE_PARATEXT = "issueResolution.queueParatext"
    ISSUE_RESOLUTION_RETRY_PARATEXT = "issueResolution.retryParatext"

    LOGOS_GET_STATE = "logos.getState"
    LOGOS_SET_REFERENCE = "logos.setReference"

    NAVIGATION_STATUS = "navigation.status"
    NAVIGATION_POLL = "navigation.poll"
    NAVIGATION_BRIDGE_CHANGED = "navigation.bridgeChanged"
    NAVIGATION_RESOLVE = "navigation.resolve"


class BridgeEngine:
    def __init__(self, settings: Optional[AppSettings] = None, ai_transport: Optional[Transport] = None) -> None:
        # ai_transport lets tests inject a fake OpenAI-Responses-shaped
        # transport (the same Callable[[url, headers, body, timeout],
        # (status, bytes)] shape ai_client.OpenAIResponsesClient already
        # accepts) instead of a real network call — mirrors that class's
        # own existing dependency-injection pattern, extended one level up
        # so BridgeEngine's AI protocol methods are unit-testable without a
        # real API key. None in production means "use the real network".
        self._ai_transport = ai_transport
        # LogosConnectorClient owns one persistent -STA PowerShell subprocess (COM
        # automation needs a single-threaded apartment) — unlike ParatextConnectorClient's
        # stateless per-call named-pipe open, spawning a fresh PowerShell process on every
        # poll would be far too slow (real measured startup well over a second). Created
        # lazily on first use, reused for the rest of this process's life.
        self._logos_client: Optional[LogosConnectorClient] = None
        self.greek_room = GreekRoomEngine()
        self.project: Optional[TranslationCoreProject] = None
        self.passage_semantic_runtime: PassageSemanticRuntime | None = None
        self._passage_semantic_status: dict[str, Any] = {
            "available": False, "readOnly": True, "state": "NO_PROJECT",
        }
        # USFM structural checks run once per whole book (not once per
        # verse — each run spawns a subprocess loading a real tag/Unicode
        # database, far too slow to repeat per verse.runChecks call). Keyed
        # by project path so switching books/projects naturally invalidates.
        # Not invalidated by verse.edit — see _usfm_findings_for_book.
        self._usfm_findings_by_book: dict[str, list[QaFinding]] = {}
        self._usfm_errors_by_book: dict[str, str] = {}
        # Names/transliteration spelling-consistency is also inherently
        # whole-book (there's nothing to compare a single verse's spelling
        # against), so it's cached the same way as USFM findings — not
        # invalidated by verse.edit either, see _names_findings_for_book.
        self._names_findings_by_book: dict[str, list[QaFinding]] = {}
        self._names_errors_by_book: dict[str, str] = {}
        # Wildebeest findings have no whole-book cache: they're computed
        # live, per-verse, only when a user opens that verse in the editor
        # (ReviewPanel.svelte's runVerseChecks(["greekroom"]) on selection).
        # A whole-book Wildebeest cache was tried for the project report's
        # exception queue (issue #24) and reverted the same day — see
        # build_project_report's docstring for why computing it inline
        # there is unsafe (blocks the single-threaded stdio dispatcher).
        # Layer-2 corpus-consistency findings (see _consistency_findings_for_book)
        # — whole-book like USFM/names above, and for the same reason: this
        # scans every completed verse's alignment, too slow to redo per verse.
        self._consistency_findings_by_book: dict[str, list[QaFinding]] = {}
        # UAlign-style corpus statistics (Phase 6) scan every COMPLETED verse
        # across the open book's whole collection — see
        # alignment_statistics.py's own docstring for why this is a fresh
        # implementation against Bridge's own data rather than a vendored
        # ualign.py. Cached per primary project path like the caches above,
        # but ALSO invalidated by any alignment mutation that changes which
        # verses are complete for the current book (_save_alignment,
        # complete_alignment, undo_alignment) — unlike USFM/names findings,
        # this cache is cheap enough (a linear scan over already-completed
        # verses, not a subprocess or whole-book vocabulary comparison) that
        # keeping it fresh on every mutation is worth it rather than waiting
        # for the next project.open.
        self._corpus_stats_by_book: dict[str, corpus_stats_tool.CorpusStatsTable] = {}
        self._checker_lock = threading.RLock()
        self._import_lock = threading.Lock()
        self._check_jobs = CheckJobManager()
        self._collection_jobs = CollectionJobManager()
        self._housestyle_learner = HouseStyleLearner()
        self._language_qa = LanguageQaManager()
        self._ai_review_jobs = AIReviewJobManager()
        self._auto_align_jobs = auto_align_jobs.AutoAlignJobManager()  # #221
        self._analysis_jobs = AnalysisJobManager()
        self._correction_application_service: CorrectionApplicationService | None = None
        self._correction_affected_analysis_service: CorrectionAffectedAnalysisService | None = None
        self._correction_verification_service: CorrectionVerificationService | None = None
        self._report_jobs = ReportJobManager()
        self._triage_jobs = TriageJobManager()
        # Guards each book's triage store against a triage.override arriving
        # on the dispatcher thread while the triage worker is mid-merge. One
        # process-wide lock is enough: a triage write is a small whole-file
        # rewrite, and only one triage run exists at a time.
        self._triage_lock = threading.RLock()
        # AppSettings() with no path defaults to a real, persistent location
        # (%LOCALAPPDATA%/Bridge/data/settings.json on Windows — a subfolder
        # of the NSIS install dir, not the dir itself, so an uninstall can't
        # wipe user data as a side effect; see _default_app_root()'s
        # docstring in secret_store.py for the legacy-path migration), and
        # get_api_key() also checks OPENAI_API_KEY. That's correct for
        # production use — settings should survive restarts — but tests must
        # inject an isolated instance rather than touch the real machine's
        # settings. See tests/test_bridge_service.py.
        self.settings = settings if settings is not None else AppSettings()
        self._configure_language_qa()
        # Reference Bibles (one per folder) and related-word indexes, built on
        # background threads on first use and kept for the session.
        self._reference_holders: dict[tuple[str, str], Any] = {}
        self._related: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
        self._related_lock = threading.Lock()
        # Resolved lazily by current_actor_id(): working it out opens the
        # workspace database, and plenty of requests never write anything (#78).
        self._local_user: dict[str, str] | None = None

        # Connector I/O is deliberately performed by NavigationSyncCoordinator's
        # daemon probe rather than this service's single-threaded stdio request loop.
        self._navigation = NavigationSyncCoordinator(
            paratext_client=lambda: ParatextConnectorClient(),
            logos_client=self._logos_client_instance,
        )
        self._navigation.configure(
            paratext=self.settings.paratext_navigation,
            logos=self.settings.logos_navigation,
        )

        # Keep imported projects in the same application-owned folder as settings.
        settings_root = self.settings.path.parent
        self.project_root = settings_root / "projects"
        # The app-level workspace database (TEAM_ARCHITECTURE.md section 4):
        # settings already opened it beside settings.json. One instance,
        # shared with the registry and injected into every project this engine
        # opens, so the per-project workbench writes and the cross-project
        # progress cache agree on which installation they belong to.
        self.workspace: WorkspaceRepository = self.settings.workspace
        self.project_registry = ProjectRegistry(
            settings_root / "project-registry.json", self.project_root, workspace=self.workspace,
        )

    # -- identity -------------------------------------------------------

    def local_user(self) -> dict[str, str]:
        """The one local user: a stable id plus the name they chose (#78).

        Identity here is a chosen name plus a device, never a password
        (`docs/DECISIONS.md`, 2026-09-11). The name seeds from the OS account
        for someone who has never set one, and is editable in Settings.
        """
        if self._local_user is None:
            self._local_user = self.workspace.get_or_create_local_user(
                self.settings.reviewer_name
            )
        return self._local_user

    def current_actor_id(self) -> str:
        """The stable `user_id` to stamp on a write.

        Not the display name. Writes used to record `settings.reviewer_name`,
        so renaming yourself in Settings left your old rows under the old name
        and new rows under the new one -- the same person appearing as two
        actors, with no way to tell they were one. The id never changes, so a
        rename re-labels everything that person has ever written.
        """
        return self.local_user()["userId"]

    # -- lifecycle ------------------------------------------------------

    def info(self) -> dict[str, Any]:
        return {
            "bridgeVersion": BRIDGE_VERSION,
            # V11-011: the only prior way to confirm what's actually running
            # was Windows' "Installed apps" list, which reports what was
            # installed, not what this process loaded. Both values come from
            # the same constants everything else in this process already
            # uses (BRIDGE_VERSION, DATABASE_SCHEMA_VERSION) -- no separate
            # build-stamped source of truth exists to read a commit id from.
            "companionSchemaVersion": DATABASE_SCHEMA_VERSION,
            "projectOpen": self.project is not None,
            "greekRoom": self.greek_room.info(),
        }

    def open_project(self, path: str, project_id: str = "") -> dict[str, Any]:
        self._usfm_findings_by_book.clear()
        self._usfm_errors_by_book.clear()
        self._names_findings_by_book.clear()
        self._names_errors_by_book.clear()
        self._consistency_findings_by_book.clear()
        self._corpus_stats_by_book.clear()
        timer = _PhaseTimer()
        materialize_lazy_project(path)
        timer.mark("materialize_lazy")
        ensure_bridge_original_language(path)
        timer.mark("original_language")
        candidate = TranslationCoreProject(path, workspace=self.workspace, reviewer_name=self.settings.reviewer_name)
        timer.mark("load_project")
        if project_id:
            existing = self.project_registry.get(project_id)
            if existing:
                existing_book = str(existing.get("bookId") or "").lower()
                existing_language = str(existing.get("targetLanguageId") or "").lower()
                candidate_target = candidate.manifest.get("target_language", {})
                candidate_language = str(
                    candidate_target.get("id") or "" if isinstance(candidate_target, dict) else ""
                ).lower()
                if existing_book and existing_book != candidate.book_id.lower():
                    raise ProjectError(
                        f"The selected folder is {candidate.book_id.upper()}, but the missing "
                        f"project is {existing_book.upper()}. Choose the original project folder."
                    )
                if existing_language and candidate_language and existing_language != candidate_language:
                    raise ProjectError(
                        "The selected folder has a different target language from the missing project."
                    )
        try:
            registered = self.project_registry.register(path, touch=True, project_id=project_id)
        except ProjectIdentityError as exc:
            raise ProjectError(str(exc)) from exc
        timer.mark("register")
        self._language_qa.unbind()
        self.project = candidate
        self.passage_semantic_runtime = None
        self._correction_application_service = None
        self._correction_affected_analysis_service = None
        self._correction_verification_service = None
        # Filesystem recovery must precede semantic initialization. Otherwise
        # the semantic runtime could fingerprint a partially written chapter
        # that the translationCore journal then rolls back.
        tc_recovery = candidate.recover_incomplete_transactions()
        timer.mark("tc_recovery")
        recovery_failed = any(
            str(item.get("status") or "") == "recovery_required"
            for item in tc_recovery
        )
        # The workspace progress cache is repaired on every open, after
        # recovery (which may roll back what it would otherwise have cached).
        # A cache problem must not make the project unopenable -- the record
        # is the workbench, the cache is a copy -- but it must not vanish
        # either, so it comes back on the open result.
        try:
            progress_cache = {"state": candidate.sync_progress_cache()}
        except Exception as exc:
            progress_cache = {"state": "error", "error": str(exc)}
        timer.mark("progress_cache")
        if recovery_failed:
            self._passage_semantic_status = {
                "available": False, "readOnly": True,
                "state": "RECOVERY_REQUIRED",
                "correctionWritesBlocked": True,
                "translationCoreRecovery": tc_recovery,
                "error": "An incomplete translationCore filesystem transaction could not be rolled back safely.",
            }
            info = self._project_info()
            siblings = collection_projects(path)
            if siblings:
                info["importedProjects"] = siblings
            info.update({
                "projectId": registered["projectId"],
                "collectionId": registered.get("collectionId", ""),
                "managed": registered.get("managed", False),
                "passageSemantic": dict(self._passage_semantic_status),
                "progressCache": progress_cache,
            })
            timer.mark("project_info")
            _trace(f"project.open {candidate.book_id} RECOVERY_REQUIRED {timer.summary()}")
            self._language_qa.bind(candidate, blocked_reason="Project recovery requires attention.")
            return info
        self._passage_semantic_status = {
            "available": False, "readOnly": True, "state": "UNAVAILABLE",
        }
        try:
            runtime = PassageSemanticRuntime(candidate, str(registered["projectId"]))
            candidate.attach_passage_semantic_runtime(runtime)
            self.passage_semantic_runtime = runtime
            self._correction_application_service = CorrectionApplicationService(runtime, self.edit_verse)
            self._analysis_jobs.bind_runtime(runtime)
            self._correction_affected_analysis_service = CorrectionAffectedAnalysisService(
                runtime, self._analysis_jobs,
            )
            self._correction_verification_service = CorrectionVerificationService(
                runtime, self._analysis_jobs,
            )
            self._passage_semantic_status = {
                "state": "READY", **runtime.status(),
                "translationCoreRecovery": tc_recovery,
            }
        except Exception as exc:
            # Scripture/tC access remains fully usable. Semantic APIs expose the
            # recovery diagnostic rather than making project.open fail.
            candidate.attach_passage_semantic_runtime(None)
            self._passage_semantic_status = {
                "available": False, "readOnly": True,
                "state": "RECOVERY_REQUIRED", "error": str(exc),
            }
        timer.mark("semantic_runtime")
        info = self._project_info()
        siblings = collection_projects(path)
        if siblings:
            info["importedProjects"] = siblings
        info.update({
            "projectId": registered["projectId"],
            "collectionId": registered.get("collectionId", ""),
            "managed": registered.get("managed", False),
            "passageSemantic": dict(self._passage_semantic_status),
            "progressCache": progress_cache,
        })
        timer.mark("project_info")
        runtime_phases = (
            f" semantic_runtime[{self.passage_semantic_runtime.init_timing_summary()}]"
            if self.passage_semantic_runtime is not None else ""
        )
        _trace(f"project.open {candidate.book_id} {timer.summary()}{runtime_phases}")
        self._language_qa.bind(candidate)
        # Rebuilt from the book's decisions on first use: anything recorded
        # while the book was closed (an import, another device) is picked up.
        self._housestyle_learner.forget(str(candidate.path))
        return info

    def list_projects(self) -> dict[str, Any]:
        return {"projects": self.project_registry.list_projects(collapse_collections=True)}

    def list_book_progress(self) -> dict[str, Any]:
        """Progress rollups for every book in the currently open collection,
        for the project dashboard. Lazy siblings are never materialized just
        to compute stats — their progress comes back null and the frontend
        renders a distinct 'not yet opened' state.

        Reads the workspace `project_progress_cache` in one query rather than
        each sibling's own workbench database (#77): a 66-book Bible would
        otherwise mean opening 66 SQLite files to draw one dashboard. A
        materialized sibling with no cache entry (the workspace database was
        reset, or the folder came from another machine) is peeked read-only
        once and its entry written, so the next call is one query again.
        """
        self._require_project()
        siblings = collection_projects(str(self.project.path))
        if not siblings:
            siblings = [{
                "path": str(self.project.path), "bookId": self.project.book_id,
                "bookName": self.project.summary.book_name, "lazy": False,
            }]
        rows: list[tuple[dict[str, Any], Path, bool, bool, str]] = []
        for entry in siblings:
            path = Path(str(entry.get("path") or ""))
            lazy = bool(entry.get("lazy"))
            missing = not path.is_dir()
            key = project_path_key(path) if not lazy and not missing else ""
            rows.append((entry, path, lazy, missing, key))
        cached = self.workspace.progress_cache_by_keys([key for *_, key in rows if key])
        refill: list[dict[str, Any]] = []
        books: list[dict[str, Any]] = []
        for entry, path, lazy, missing, key in rows:
            progress = None
            if key:
                hit = cached.get(key)
                if hit is None:
                    peeked = peek_progress_totals(path)
                    if peeked is not None:
                        refill.append({**peeked, "projectPath": path})
                        hit = peeked
                if hit is not None:
                    progress = {**hit["totals"], "updatedAt": hit["updatedAt"]}
            books.append({
                "path": str(path), "bookId": str(entry.get("bookId") or ""),
                "bookName": str(entry.get("bookName") or ""), "lazy": lazy,
                "missing": missing, "progress": progress,
            })
        # Written after the walk, as one commit: a cold cache on a fully
        # opened Bible is 66 peeks, and one fsync rather than 66.
        self.workspace.upsert_progress_cache_many(refill)
        return {"books": books}

    def forget_project(self, project_id: str) -> dict[str, Any]:
        if not project_id:
            raise ProjectError("projectId is required")
        paths = [str(entry.get("path") or "") for entry in self.project_registry.group_entries(project_id)]
        forgotten = self.project_registry.forget(project_id)
        if forgotten:
            self.workspace.forget_progress_cache([path for path in paths if path])
        return {"forgotten": forgotten}

    def delete_project(self, project_id: str) -> dict[str, Any]:
        if not project_id:
            raise ProjectError("projectId is required")
        entry = self.project_registry.get(project_id)
        if entry is None:
            raise ProjectError("Project not found")
        managed = bool(entry.get("managed"))
        self.workspace.forget_progress_cache([
            str(sibling.get("path") or "") for sibling in self.project_registry.group_entries(project_id)
            if sibling.get("path")
        ])
        if managed:
            managed_root = self.project_registry.managed_root
            for sibling in self.project_registry.group_entries(project_id):
                sibling_path = Path(str(sibling.get("path") or ""))
                if not sibling_path.exists():
                    continue
                resolved = sibling_path.resolve(strict=False)
                if resolved != managed_root and managed_root not in resolved.parents:
                    continue
                shutil.rmtree(resolved, ignore_errors=True)
        forgotten = self.project_registry.forget(project_id)
        return {"deleted": forgotten, "managed": managed}

    def _project_info(self) -> dict[str, Any]:
        self._require_project()
        summary = self.project.summary  # property, not a method
        target = self.project.manifest.get("target_language", {})
        resource = self.project.manifest.get("resource", {})
        bridge_project = self.project.manifest.get("bridge_project", {})
        original_language = resource_inventory(self.project.book_id)
        project_original_language = self.project.manifest.get("bridge_original_language", {})
        project_original_version = str(
            self.project.manifest.get("tc_orig_lang_check_version_wordAlignment") or ""
        )
        original_language.update({
            "projectVersion": project_original_version,
            "projectResource": project_original_language,
            "versionMismatch": bool(
                isinstance(project_original_language, dict)
                and project_original_language
                and (
                    str(project_original_language.get("version") or "") != str(original_language.get("version") or "")
                    or str(project_original_language.get("commit") or "") != str(original_language.get("commit") or "")
                )
            ),
        })
        return {
            "path": str(summary.path),
            "bookId": summary.book_id,
            "bookName": summary.book_name,
            "targetLanguage": summary.target_language,
            "targetLanguageId": str(target.get("id") or "") if isinstance(target, dict) else "",
            "targetLanguageDirection": str(target.get("direction") or "") if isinstance(target, dict) else "",
            "projectName": str(bridge_project.get("name") or summary.book_name) if isinstance(bridge_project, dict) else summary.book_name,
            "bibleName": str(resource.get("name") or resource.get("id") or "") if isinstance(resource, dict) else "",
            "tcVersion": summary.tc_version,
            "chapters": self.project.chapters(),
            "checkTypes": self.project.check_types(),
            "originalLanguageResource": original_language,
        }

    def inspect_project_import(self, path: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        """Read-only detection/validation used before the metadata form."""
        preview = inspect_import(path)
        preview["duplicates"] = self.project_registry.classify(preview, metadata)
        return preview

    def import_project(self, path: str, metadata: dict[str, Any],
                       destination_root: str = "", allow_duplicate: bool = False) -> dict[str, Any]:
        """Normalize USFM/SFM, Paratext folders, or tC archives, then open it.

        Existing translationCore state is copied intact. Raw Scripture becomes
        a tC-compatible primary book immediately; other books in a collection
        are copied into application storage and normalized when first opened.
        TranslationNotes/translationWords indexes are prepared by the checking
        job instead of blocking navigation into the editor.
        """
        if not self._import_lock.acquire(blocking=False):
            raise ProjectError("Another project import is already running.")
        try:
            timer = _PhaseTimer()
            preview = inspect_import(path)
            timer.mark("inspect")
            duplicate = self.project_registry.classify(preview, metadata)
            timer.mark("classify")
            if duplicate["classification"] == "exactDuplicate" and not allow_duplicate:
                raise ProjectError(
                    "This source has already been imported. Open the existing project, "
                    "or explicitly choose Import as separate copy."
                )
            root = Path(destination_root).resolve() if destination_root else self.project_root
            result = import_source(path, root, metadata)
            timer.mark("import_source")
            # A folder that is being (re)populated is a new project even at an
            # old path; whatever the cache said about that path is void.
            self.workspace.forget_progress_cache([
                str(imported.get("path") or "") for imported in result["projects"] if imported.get("path")
            ])
            fingerprints = source_fingerprints(preview)
            for imported in result["projects"]:
                book_id = str(imported.get("bookId") or "").lower()
                registered = self.project_registry.register(
                    imported["path"],
                    source_fingerprint=fingerprints.get(book_id, ""),
                    project_id=str(imported.get("projectId") or ""),
                    collection_id=str(imported.get("collectionId") or ""),
                )
                imported.update({
                    "projectId": registered["projectId"],
                    "collectionId": registered.get("collectionId", ""),
                })
            timer.mark("register")

            info = self.open_project(result["primaryProjectPath"])
            timer.mark("open_primary")
            info["import"] = result
            info["importedProjects"] = result["projects"]
            _trace(f"project.import {len(result['projects'])} book(s) {timer.summary()}")
            return info
        finally:
            self._import_lock.release()

    def _ensure_resource_indexes(self, project: TranslationCoreProject) -> None:
        """Prepare raw-import tN/tW indexes on demand, once per book."""
        import_path = project.path / ".bridge" / "import.json"
        if not import_path.is_file():
            return
        try:
            import_data = json.loads(import_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return
        capabilities = import_data.get("capabilities")
        if not isinstance(capabilities, dict) or not any(
            capabilities.get(tool) == "requires-resource-index"
            for tool in ("translationNotes", "translationWords")
        ):
            return
        resources_root = project.path.parent.parent / "resources"
        materialization = materialize_book_checks(project.path, project.book_id, resources_root)
        apply_resource_materialization(project.path, materialization)

    @staticmethod
    def _resource_indexes_pending(project: TranslationCoreProject) -> bool:
        """Return quickly when a raw import still needs its tN/tW indexes.

        Materialization belongs to the background-check preflight.  Interactive
        review requests must never perform that potentially expensive work on
        the single stdio dispatcher thread, otherwise every later request
        (including checks.status/cancel) queues behind it.
        """
        import_path = project.path / ".bridge" / "import.json"
        if not import_path.is_file():
            return False
        try:
            import_data = json.loads(import_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return False
        capabilities = import_data.get("capabilities")
        return isinstance(capabilities, dict) and any(
            capabilities.get(tool) == "requires-resource-index"
            for tool in ("translationNotes", "translationWords")
        )

    def scan_project(self) -> dict[str, Any]:
        if not self.project:
            raise ProjectError("No project open — call project.open first")
        return {
            "chapters": self.project.chapters(),
            "checkTypes": self.project.check_types(),
            "indexTools": self.project.index_tools(),
        }

    def chapter_verses(self, chapter: str) -> list[str]:
        self._require_project()
        return self.project.verses(chapter)

    @staticmethod
    def _pinned_resource_versions(project: TranslationCoreProject) -> dict[str, str]:
        """Bundled-resource versions this project's tN/tW indexes and
        wordAlignment resources were built against, as already stamped on
        manifest.json by apply_resource_materialization/ensure_bridge_
        original_language. Absent for existing translationCore/
        translationStudio imports Bridge never materialized itself — that's
        an honest 'version not tracked', not an error."""
        manifest = project.manifest
        versions = {
            "translationNotes": manifest.get("tc_en_check_version_translationNotes"),
            "translationWords": manifest.get("tc_en_check_version_translationWords"),
            "originalLanguage": manifest.get("tc_orig_lang_check_version_wordAlignment"),
        }
        return {k: str(v) for k, v in versions.items() if v}

    def build_project_report(self) -> dict[str, Any]:
        """Deterministic QA/publication report for the current book, via the
        existing tc_ai_bridge.reporting.ReportService — never previously
        exposed over the protocol (see docs/BUILD_LOG.md). Book-scoped for
        now, same as ReportService itself; a whole-collection rollup is a
        separate, larger piece of work (multi-book aggregation).

        Deliberately does NOT warm the local-finding caches here (tried in
        issue #24, reverted the same day): the stdio dispatcher in
        stdio_transport.py is a single-threaded `for line in sys.stdin`
        loop — nothing else the sidecar does is concurrent with a request
        handler. project.report is called on every book/dashboard view, so
        synchronously computing whole-book USFM/Names/Wildebeest here (fast
        enough on a 1-2 verse test fixture to look fine) blocked the entire
        sidecar long enough on a real ~800-verse book (1 Samuel) that even
        an unrelated project.open for a different book queued behind it and
        timed out client-side. exception_first_queue still merges whatever
        local findings already happen to be cached (from a prior
        verse.runChecks/checks.start pass) — a book nobody has opened at
        all in this session just won't have any yet, same as before #24.
        Warming these safely needs a background job (CheckJobManager/
        AIReviewJobManager's own pattern), not inline in this handler.
        """
        self._require_project()
        return ReportService(self.project, **self._report_options()).build_book_report()

    def _report_options(self) -> dict[str, Any]:
        """The publication gate's Language QA advisory threshold: open medium
        findings above it add an advisory line (default 50)."""
        try:
            value = int(self.settings.data.get("language_qa_medium_advisory", LANGUAGE_QA_MEDIUM_ADVISORY))
        except (AttributeError, TypeError, ValueError):
            value = LANGUAGE_QA_MEDIUM_ADVISORY
        return {"language_qa_medium_advisory": value}

    def _materialized_collection_books(self) -> list[ReportBook]:
        """Every book in the currently open collection whose directory exists,
        or just the current book when it isn't part of a multi-book collection
        -- same sibling resolution list_book_progress already uses. Unlike
        _report_books, a registered but missing sibling is skipped rather than
        listed."""
        self._require_project()
        siblings = collection_projects(str(self.project.path))
        if not siblings:
            siblings = [{
                "path": str(self.project.path), "bookId": self.project.book_id,
                "bookName": self.project.summary.book_name, "lazy": False,
            }]
        books: list[ReportBook] = []
        for entry in siblings:
            path = str(entry.get("path") or "")
            if not Path(path).is_dir():
                continue
            books.append(ReportBook(
                path=path, book_id=str(entry.get("bookId") or ""),
                book_name=str(entry.get("bookName") or ""),
            ))
        return books

    def build_collection_report(self) -> dict[str, Any]:
        """Project-level rollup across every book in the current collection
        -- builds each sibling's own build_book_report() against a freshly
        constructed TranslationCoreProject (never self.project) and
        aggregates them with ReportService.build_collection_report.
        Synchronous: fine for a handful of books, but this does not solve
        the whole-Bible performance question (see issue #17) -- a 66-book
        collection sequentially building 66 full reports in one request
        could run long."""
        books = self._materialized_collection_books()
        reports: list[dict[str, Any]] = []
        for book in books:
            materialize_lazy_project(book.path)
            reports.append(ReportService(
                TranslationCoreProject(
                    book.path, workspace=self.workspace,
                    reviewer_name=self.settings.reviewer_name,
                ), **self._report_options(),
            ).build_book_report())
        return ReportService.build_collection_report(reports)

    # -- whole-collection QA report ---------------------------------------

    def _report_books(self) -> list[ReportBook]:
        """Every sibling in the open collection, lazy and missing ones
        included -- unlike _materialized_collection_books, the report must list a book
        that was never opened (as 'not checked') rather than skip it."""
        self._require_project()
        siblings = collection_projects(str(self.project.path))
        if not siblings:
            siblings = [{
                "path": str(self.project.path), "bookId": self.project.book_id,
                "bookName": self.project.summary.book_name, "lazy": False,
            }]
        books: list[ReportBook] = []
        for entry in siblings:
            path = str(entry.get("path") or "")
            books.append(ReportBook(
                path=path, book_id=str(entry.get("bookId") or ""),
                book_name=str(entry.get("bookName") or ""),
                lazy=bool(entry.get("lazy")), missing=not Path(path).is_dir(),
            ))
        return books

    def _report_project_name(self) -> str:
        self._require_project()
        bridge_project = self.project.manifest.get("bridge_project", {})
        if isinstance(bridge_project, dict) and bridge_project.get("name"):
            return str(bridge_project["name"])
        resource = self.project.manifest.get("resource", {})
        if isinstance(resource, dict) and resource.get("name"):
            return str(resource["name"])
        return self.project.summary.book_name

    def _build_report_book(self, book: ReportBook) -> dict[str, Any]:
        """Runs on the report job's worker thread. Never materializes a lazy
        sibling: a book nobody has opened has had no checks, and reading it
        would turn 'generate a report' into 'normalize the whole Bible'."""
        if book.missing or book.lazy:
            return unopened_book_report(
                book_id=book.book_id, book_name=book.book_name, path=book.path,
                lazy=book.lazy, missing=book.missing,
            )
        try:
            project = TranslationCoreProject(book.path, workspace=self.workspace, reviewer_name=self.settings.reviewer_name)
        except ProjectError as exc:
            return unopened_book_report(
                book_id=book.book_id, book_name=book.book_name, path=book.path,
                lazy=False, missing=False, error=str(exc),
            )
        return build_book_qa_report(project, book_name=book.book_name)

    def start_qa_report(self) -> dict[str, Any]:
        books = self._report_books()
        project_name = self._report_project_name()
        return self._report_jobs.start(
            books, build_book=self._build_report_book,
            assemble=lambda reports: aggregate_qa_report(project_name, reports),
        )

    def qa_report_status(self, job_id: str = "") -> dict[str, Any]:
        return self._report_jobs.status(job_id)

    def qa_report_get(self, job_id: str = "") -> dict[str, Any]:
        return self._report_jobs.get(job_id)

    def cancel_qa_report(self, job_id: str = "") -> dict[str, Any]:
        return self._report_jobs.cancel(job_id)

    def export_qa_report(self, output_path: str, fmt: str, rows: list[dict[str, Any]],
                         columns: list[dict[str, str]] | None = None) -> dict[str, Any]:
        if not str(output_path or "").strip():
            raise ProjectError("outputPath is required")
        try:
            return write_report_rows(output_path, fmt, rows, columns)
        except ValueError as exc:
            raise ProjectError(str(exc)) from exc

    # -- AI triage (optional, online-only) --------------------------------
    #
    # Scores how likely each persisted Greek Room finding is to be a false
    # positive, so the report screen can hide the noisiest ones behind a
    # slider. Strictly an overlay: no check, report, decision or export path
    # reads a triage verdict, and triage never rewrites a finding.

    def _triage_books(self, book: str = "") -> list[TriageBook]:
        """Books to triage. Unlike the report's book list this skips lazy and
        missing siblings: a book nobody has opened has had no checks, so it
        has no findings to triage, and materializing it to discover that
        would turn 'triage this collection' into 'normalize the whole Bible'."""
        books: list[TriageBook] = []
        wanted = str(book or "").strip().lower()
        for entry in self._report_books():
            if entry.lazy or entry.missing:
                continue
            if wanted and entry.book_id.lower() != wanted:
                continue
            books.append(TriageBook(
                path=entry.path, book_id=entry.book_id, book_name=entry.book_name,
            ))
        return books

    def _triage_client(self) -> tuple[Any, str]:
        """(client, "") when triage can run, (None, reason) when it cannot.

        Goes through _ai_client rather than testing settings.get_api_key()
        directly so that triage.results' "available" flag and triage.run's
        "unavailable" state can never disagree about whether the button
        should be enabled — they ask the same question the same way.
        """
        try:
            return self._ai_client(), ""
        except AIError as exc:
            return None, str(exc)

    def start_triage(self, book: str = "", force: bool = False) -> dict[str, Any]:
        """Start a background triage run.

        Returns an "unavailable" status rather than raising when no API key
        is configured: triage is optional and online-only, and a project with
        no key is a supported state, not an error the reviewer must dismiss.
        """
        self._require_project()
        client, reason = self._triage_client()
        if client is None:
            return {
                "state": "unavailable",
                "message": reason,
                "jobId": "",
                "totalBooks": 0,
            }

        books = self._triage_books(book)
        if not books:
            # Not "no findings" — a book that has been opened but never
            # checked is still a valid target; the run simply finds nothing
            # and succeeds. This is the narrower case of nothing to look at.
            return {
                "state": "unavailable",
                "message": (
                    f"No opened book '{book}' in this collection." if book else
                    "No books in this collection have been opened yet, so there is "
                    "nothing to triage. Open a book and run checks first."
                ),
                "jobId": "",
                "totalBooks": 0,
            }

        model = client.model
        settings = self.settings
        lock = self._triage_lock

        def run_book(entry: TriageBook, progress: Any, cancel: threading.Event) -> dict[str, Any]:
            project = TranslationCoreProject(entry.path, workspace=self.workspace, reviewer_name=self.settings.reviewer_name)

            def record_usage() -> None:
                settings.record_ai_usage(client.last_usage.total_tokens, client.last_cost_usd)

            return run_book_triage(
                project,
                call_model=client.triage_batch,
                model=model,
                lock=lock,
                force=force,
                cancel=cancel,
                progress=progress,
                on_usage=record_usage,
            )

        return self._triage_jobs.start(books, run_book=run_book, force=force)

    def triage_status(self, job_id: str = "") -> dict[str, Any]:
        return self._triage_jobs.status(job_id)

    def cancel_triage(self, job_id: str = "") -> dict[str, Any]:
        return self._triage_jobs.cancel(job_id)

    def _triage_project_for_book(self, book: str) -> TranslationCoreProject:
        wanted = str(book or "").strip().lower()
        if not wanted or wanted == self.project.book_id:
            return self.project
        for entry in self._report_books():
            if entry.book_id.lower() == wanted and not entry.missing and not entry.lazy:
                return TranslationCoreProject(entry.path, workspace=self.workspace, reviewer_name=self.settings.reviewer_name)
        raise ProjectError(f"No opened book '{book}' in this collection.")

    def override_triage(self, book: str, finding_hash: str, verdict: str = "") -> dict[str, Any]:
        """Record (or clear) a reviewer's thumbs up/down on one triage verdict.

        An override always wins over the model's verdict and is never
        re-sent to the model, so this is also how a reviewer stops paying for
        a finding they have already judged.
        """
        self._require_project()
        key = str(finding_hash or "").strip()
        if not key:
            raise ProjectError("A triage hash is required.")
        value = str(verdict or "").strip().lower()
        if value and value not in OVERRIDE_VERDICTS:
            raise ProjectError(
                f"verdict must be one of {', '.join(OVERRIDE_VERDICTS)}, or empty to clear."
            )
        project = self._triage_project_for_book(book)
        with self._triage_lock:
            records = project.load_triage_records()
            record = records.get(key)
            if not isinstance(record, dict):
                raise ProjectError(f"No triage result for '{key}'.")
            if value:
                record["userOverride"] = {
                    "verdict": value, "timestamp": project.timestamp_iso(),
                }
            else:
                record["userOverride"] = None
            records[key] = record
            project.save_triage_records(records)
        return {"bookId": project.book_id, "hash": key, "record": record}

    def clear_triage(self, book: str = "") -> dict[str, Any]:
        """Drop cached verdicts so the next run re-buys them — the escape
        hatch for a prompt-tuning session."""
        self._require_project()
        active = self._triage_jobs.active()
        if active is not None:
            raise TriageJobConflict(
                f"Triage run {active['jobId']} is {active['state']}; cancel it before clearing."
            )
        cleared: list[str] = []
        with self._triage_lock:
            for entry in self._triage_books(book):
                try:
                    project = TranslationCoreProject(entry.path, workspace=self.workspace, reviewer_name=self.settings.reviewer_name)
                except ProjectError:
                    continue
                if project.clear_triage_records():
                    cleared.append(project.book_id)
        return {"cleared": cleared}

    def triage_results(self, book: str = "") -> dict[str, Any]:
        """Every stored verdict for the collection, as {hash: record}.

        Read straight off disk per book rather than from a job, so verdicts
        survive a restart and the report screen can merge them without a
        triage run having happened this session. Hashes embed the book id, so
        one flat map cannot collide across books.
        """
        self._require_project()
        entries: dict[str, Any] = {}
        books: list[dict[str, Any]] = []
        wanted = str(book or "").strip().lower()
        for entry in self._report_books():
            if entry.missing or (wanted and entry.book_id.lower() != wanted):
                continue
            records = read_triage_records(entry.path, entry.book_id) or {}
            entries.update(records)
            books.append({
                "bookId": entry.book_id, "bookName": entry.book_name,
                "count": len(records),
            })
        active = self._triage_jobs.active()
        client, reason = self._triage_client()
        return {
            "entries": entries,
            "books": books,
            "total": len(entries),
            "running": active is not None,
            "jobId": active["jobId"] if active else "",
            "available": client is not None,
            "unavailableReason": reason,
        }

    # -- verse-level operations ------------------------------------------

    def get_verse(self, chapter: str, verse: str) -> dict[str, Any]:
        self._require_project()
        text = self.project.target_verse_text(chapter, verse)
        alignment = self.project.load_verse_alignment(chapter, verse)
        return {
            "chapter": chapter, "verse": verse,
            "text": text,
            "display": verse_display(text),
            "alignment": alignment.to_dict(),
            "alignmentStatus": self._alignment_verse_status(self.project, chapter, verse),
        }

    def get_chapter_verse_data(self, chapter: str) -> dict[str, Any]:
        """Bulk fetch: text + alignment for every verse in a chapter, in
        ONE call. Used on project open instead of looping verse.get per
        verse — a chapter with N verses used to mean N sequential round
        trips before the editor ever appeared, which for a real project
        (dozens of verses) looked exactly like the app hanging. This
        collapses that to a single request."""
        self._require_project()
        verses = self.project.verses(chapter)
        out: dict[str, Any] = {}
        for v in verses:
            text = self.project.target_verse_text(chapter, v)
            out[v] = {
                "text": text,
                # What the reader shows (#91 Phase 2): plain text, notes, style
                # spans and the raw ranges removed, all code-point offsets. The
                # frontend renders this and never parses USFM itself. `text`
                # stays: the editor edits the raw string and fixes splice it.
                "display": verse_display(text),
                "alignment": self.project.load_verse_alignment(chapter, v).to_dict(),
                "alignmentStatus": self._alignment_verse_status(self.project, chapter, v),
            }
        # Headings ride along on the same call rather than earning an RPC of
        # their own: the editor needs them exactly when it needs the verses, and
        # this call exists precisely to avoid a second round trip (#180). Keyed
        # by the verse each heading introduces; `{}` for a project imported
        # before #180 or a book with no headings.
        return {
            "chapter": chapter, "verses": out,
            "headings": self.project.chapter_headings(chapter),
            # verse -> recorded edits, for the change-history mark. A directory
            # listing per edited verse; no record is read here.
            "editCounts": self.project.verse_edit_counts(chapter),
        }

    VERSE_HISTORY_LIMIT = 500

    def verse_history(self, params: dict[str, Any]) -> dict[str, Any]:
        """verse.history: the native checkData/verseEdits records that every
        Scripture edit writes (apply_scripture_edit), newest first. One verse
        (chapter + verse), a chapter (chapter only) or the open book (neither).
        Reading only: undoing an entry is an ordinary verse.edit."""
        self._require_project()
        project = self.project
        chapter, verse = params.get("chapter"), params.get("verse")
        for name, value in (("chapter", chapter), ("verse", verse)):
            if value is not None and (not isinstance(value, str) or not value):
                raise ProjectError(f"{name} must be a non-empty string when given")
        if verse is not None and chapter is None:
            raise ProjectError("verse needs its chapter")
        limit = params.get("limit", self.VERSE_HISTORY_LIMIT)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 5000:
            raise ProjectError("limit must be an integer from 1 to 5000")
        chapters = [chapter] if chapter is not None else [str(c) for c in project.chapters()]
        entries: list[dict[str, Any]] = []
        for ch in chapters:
            verses = [verse] if verse is not None else list(project.verse_edit_counts(ch))
            for v in verses:
                for record in project.verse_edit_history(ch, v):
                    entries.append(self._verse_history_entry(ch, v, record))
        entries.sort(key=lambda e: e["timestamp"], reverse=True)
        return {"entries": entries[:limit], "total": len(entries), "truncated": len(entries) > limit}

    @staticmethod
    def _verse_history_entry(chapter: str, verse: str, record: dict[str, Any]) -> dict[str, Any]:
        before = record.get("verseBefore") if isinstance(record.get("verseBefore"), str) else ""
        after = record.get("verseAfter") if isinstance(record.get("verseAfter"), str) else ""
        context = record.get("contextId") if isinstance(record.get("contextId"), dict) else {}
        return {
            "chapter": chapter, "verse": verse,
            "timestamp": str(record.get("modifiedTimestamp") or ""),
            "username": str(record.get("username") or ""),
            "verseBefore": before, "verseAfter": after,
            # Visible text of both, so the history diff never parses USFM.
            "plainBefore": verse_display(before)["plain"], "plainAfter": verse_display(after)["plain"],
            "tags": [str(t) for t in record.get("tags") or [] if isinstance(t, str)],
            "groupId": str(context.get("groupId") or ""),
            "batchId": context.get("batchId") if isinstance(context.get("batchId"), str) else None,
            "undoes": context.get("undoes") if isinstance(context.get("undoes"), str) else None,
        }

    def list_checks_for_verse(self, chapter: str, verse: str) -> dict[str, Any]:
        self._require_project()
        project = self.project
        # Never wait behind whole-book USFM/names preparation on the stdio
        # dispatcher.  A blocking request here prevents even checks.status
        # and checks.cancel from being read until Rust's 30-second timeout.
        if self._resource_indexes_pending(project):
            return {
                "chapter": str(chapter), "verse": str(verse), "checks": [],
                "state": "preparing", "retryAfterMs": 750,
                "message": "Preparing translationNotes and translationWords resources…",
            }
        if not self._checker_lock.acquire(blocking=False):
            return {
                "chapter": str(chapter), "verse": str(verse), "checks": [],
                "state": "preparing", "retryAfterMs": 750,
                "message": "Translation checks are running; review data will appear shortly.",
            }
        try:
            self.project.invalidate_index_cache()
            checks = self.project.check_reviews_for_verse(chapter, verse)
            ai_review_state = self.project.ai_review_cache_status(chapter, verse)
            cached_ai = self.project.load_ai_review_result(chapter, verse) if ai_review_state == "current" else None
            ai_reviews = list((cached_ai or {}).get("checkReviews") or [])
            ai_by_identity = {
                (str(item.get("tool") or ""), str(item.get("check_id") or "")): item
                for item in ai_reviews if isinstance(item, dict)
            }
            evaluation = {
                "pass": "passed", "not_applicable": "passed",
                "problem": "issue_open", "review": "needs_review",
            }
            outcomes = dict((cached_ai or {}).get("automaticSelection") or {})
            for check in checks:
                identity = (str(check.get("tool") or ""), str(check.get("checkId") or ""))
                ai_item = ai_by_identity.get(identity)
                if ai_item:
                    check["evaluationStatus"] = evaluation.get(str(ai_item.get("verdict") or ""), "needs_review")
                elif ai_review_state == "stale":
                    check["evaluationStatus"] = "needs_review"
                outcome = outcomes.get(f"{identity[0]}:{identity[1]}")
                check["automaticSelection"] = (
                    {
                        "outcome": str(outcome.get("outcome") or ""),
                        "reason": str(outcome.get("reason") or ""),
                    }
                    if isinstance(outcome, dict) else None
                )
        finally:
            self._checker_lock.release()
        return {
            "chapter": str(chapter), "verse": str(verse), "checks": checks,
            "state": "ready", "retryAfterMs": 0, "message": "",
            "aiReviewState": ai_review_state,
            "aiReviews": ai_reviews,
            "aiQaIssues": list((cached_ai or {}).get("qaIssues") or []),
            "aiSummary": str((cached_ai or {}).get("summary") or ""),
        }

    def validate_check_selection(
        self, chapter: str, verse: str, tool: str, group_id: str, check_id: str,
        selections: Any, nothing_to_select: bool,
    ) -> dict[str, Any]:
        self._require_project()
        with self._checker_lock:
            return self.project.validate_check_selection(
                chapter, verse, tool, group_id, check_id, selections, nothing_to_select,
            )

    def save_check_selection(
        self, chapter: str, verse: str, tool: str, group_id: str, check_id: str,
        selections: list[dict[str, Any]], nothing_to_select: bool, provenance: str,
        expected_fingerprint: str, metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._require_project()
        accepted_advanced_proposal = (
            provenance == "human"
            and str((metadata or {}).get("interface") or "") == "advanced-ai-proposal"
            and bool((metadata or {}).get("acceptedAIProposal"))
        )
        if (
            accepted_advanced_proposal
            and nothing_to_select
            and str((metadata or {}).get("verdict") or "") == "problem"
        ):
            raise ProjectError(
                "An AI-reported translation problem cannot be saved as Nothing to Select. "
                "Leave it pending, select the affected target text, or resolve the issue."
            )
        with self._checker_lock:
            result = self.project.save_check_selection(
                chapter, verse, tool, group_id, check_id, selections, nothing_to_select,
                provenance, expected_fingerprint,
                username=self.settings.reviewer_name or "Bridge Reviewer",
                audit_metadata=metadata,
            )
            if provenance == "bridge_ai" or accepted_advanced_proposal:
                self.project.rebase_ai_review_fingerprint(chapter, verse)
                # In Advanced mode the model result remains advisory until the
                # reviewer explicitly applies it. That explicit mutation is the
                # human confirmation which may close a safely grounded pass.
                saved_ai = self.project.load_ai_review_result(chapter, verse) or {}
                if self.project.ai_review_cache_status(chapter, verse) == "current":
                    result["resolutionLifecycle"] = self.project.reconcile_issue_resolutions_after_ai_review(
                        chapter, verse, list(saved_ai.get("checkReviews") or []),
                        model=str(saved_ai.get("model") or ""),
                        summary=str(saved_ai.get("summary") or ""),
                        allow_automatic_resolution=True,
                    )
            return result

    def clear_check_selection(
        self, chapter: str, verse: str, tool: str, group_id: str, check_id: str,
        provenance: str, expected_fingerprint: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._require_project()
        with self._checker_lock:
            return self.project.clear_check_selection(
                chapter, verse, tool, group_id, check_id, provenance, expected_fingerprint,
                username=self.settings.reviewer_name or "Bridge Reviewer",
                audit_metadata=metadata,
            )

    # -- word alignment ---------------------------------------------------

    @staticmethod
    def _target_token_inventory(text: str) -> list[TokenRef]:
        words = whitespace_tokens(text)
        totals = Counter(words)
        seen: Counter[str] = Counter()
        result: list[TokenRef] = []
        for word in words:
            seen[word] += 1
            result.append(TokenRef(word, seen[word], totals[word], type="bottomWord"))
        return result

    @staticmethod
    def _alignment_verse_status(
        project: TranslationCoreProject, chapter: str, verse: str,
    ) -> str:
        if project.word_alignment_state(chapter, verse) == "invalid":
            return "invalid"
        return project.alignment_work_state(chapter, verse)

    def alignment_status(self, chapter: str = "") -> dict[str, Any]:
        self._require_project()
        project_chapters = self.project.chapters()
        chapters = [str(chapter)] if chapter else project_chapters
        counts = {"complete": 0, "partial": 0, "untouched": 0, "invalid": 0}
        verses: dict[str, str] = {}
        for ch in chapters:
            if ch not in project_chapters:
                raise ProjectError(f"Chapter {ch} does not exist in this project.")
            for verse in self.project.verses(ch):
                if verse == "front":
                    continue
                status = self._alignment_verse_status(self.project, ch, verse)
                counts[status] += 1
                verses[f"{ch}:{verse}"] = status
        return {"chapter": str(chapter), "counts": counts, "verses": verses}

    def _alignment_context(
        self, chapter: str, verse: str, *, chapter_counts: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        """Build the full alignment view for one verse.

        `chapter_counts` lets a caller that already holds
        `alignment_status(chapter)["counts"]` pass it in instead of having
        every verse rescan the whole chapter (alignment.getRange does this
        once for N verses). Left as None, the chapter is scanned here exactly
        as before.
        """
        self._require_project()
        alignment = self.project.load_verse_alignment(chapter, verse)
        inventory = make_inventory(alignment)
        expected_bottom = self._target_token_inventory(
            self.project.target_verse_text(chapter, verse)
        )
        expected_signatures = [token.signature for token in expected_bottom]
        actual_bottom = alignment.all_bottom()
        actual_signatures = [token.signature for token in actual_bottom]
        issues = list(structural_issues(alignment))
        missing = [signature for signature in expected_signatures if signature not in actual_signatures]
        extra = [signature for signature in actual_signatures if signature not in expected_signatures]
        duplicate_top = [
            key for key, count in Counter(t.signature for t in alignment.all_top()).items()
            if count > 1
        ]
        duplicate_bottom = [key for key, count in Counter(actual_signatures).items() if count > 1]
        if missing:
            issues.append(f"{len(missing)} target token(s) are missing from alignment data.")
        if extra:
            issues.append(f"{len(extra)} alignment token(s) are absent from current target text.")
        if duplicate_top:
            issues.append(f"{len(duplicate_top)} source token(s) occur in more than one alignment group.")
        if duplicate_bottom:
            issues.append(f"{len(duplicate_bottom)} target token(s) occur more than once in alignment data.")
        target_positions = {
            token.signature: position for position, token in enumerate(expected_bottom)
        }
        discontinuous_groups = 0
        for group in alignment.alignments:
            positions = sorted(
                target_positions[token.signature]
                for token in group.bottom_words
                if token.signature in target_positions
            )
            if len(positions) > 1 and positions[-1] - positions[0] + 1 != len(positions):
                discontinuous_groups += 1
        if discontinuous_groups:
            issues.append(
                f"{discontinuous_groups} alignment group(s) contain non-adjacent target words; "
                "aligned USFM requires each target span to be contiguous."
            )

        top_tokens = [
            {"id": token_id, **token.to_dict()}
            for token_id, token in inventory.top_ids.items()
        ]
        # Present target words in verse order, independent of group order.
        bottom_tokens: list[dict[str, Any]] = []
        seen_bottom_ids: set[str] = set()
        for token in expected_bottom:
            token_id = inventory.bottom_sig_to_id.get(token.signature)
            if token_id:
                bottom_tokens.append({
                    "id": token_id,
                    **inventory.bottom_ids[token_id].to_dict(bottom=True),
                })
                seen_bottom_ids.add(token_id)
        for token_id, token in inventory.bottom_ids.items():
            if token_id not in seen_bottom_ids:
                bottom_tokens.append({"id": token_id, **token.to_dict(bottom=True)})

        groups = alignment_gaps.group_views(alignment, inventory)
        # The gap sets are computed once, below, after the cross-verse links are
        # known -- #137. A link-blind pair used to be built here as well and
        # then overwritten a few lines down by the link-aware one, so it was
        # dead work on every alignment.get and every verse of every getRange.
        source_direction = "rtl" if any(
            token.strong.upper().startswith("H") or token.morph.startswith("He,")
            for token in alignment.all_top()
        ) else "ltr"
        target = self.project.manifest.get("target_language", {})
        target_direction = str(target.get("direction") or "ltr") if isinstance(target, dict) else "ltr"
        source_available = bool(top_tokens)
        work_state = self._alignment_verse_status(self.project, chapter, verse)
        can_complete = (
            source_available
            and not issues
            and not alignment.word_bank
            and bool(alignment.alignments)
            and all(group.top_words and group.bottom_words for group in alignment.alignments)
        )
        # #117: Bridge-private cross-verse links touching this verse. They never
        # enter `groups` (tC alignment stays verse-local); they only annotate.
        # A target word accounted for by a link, or a source token realized in
        # another verse, stops counting as a gap here, and `fullyAccounted`
        # is what lets the editors drop the "not fully aligned" flag while
        # `status` / `completionState` keep telling the tC truth.
        cross = self._cross_verse_annotations(str(chapter), str(verse), inventory)
        # #216: null decisions close a gap the same way a link does -- the
        # token is accounted for, by a named reason, with no tC group.
        nulls = self._null_annotations(str(chapter), str(verse), inventory)
        remaining_sources, remaining_targets = alignment_gaps.gap_ids(
            inventory, groups,
            realized_ids=cross["realizedIds"], accounted_ids=cross["accountedIds"],
            null_source_ids=nulls["sourceIds"], null_target_ids=nulls["targetIds"],
        )
        gaps = {"sourceUnmatched": len(remaining_sources), "targetUnmatched": len(remaining_targets)}
        fully_accounted = (
            not remaining_sources and not remaining_targets
            and bool(cross["accountedIds"] or cross["realizedIds"] or nulls["sourceIds"] or nulls["targetIds"])
        )
        tc_matched = (
            sum(1 for group in groups if group["bottomIds"] for _ in group["topIds"])
            + sum(len(group["bottomIds"]) for group in groups)
        )
        accounted_by = {
            "tc": tc_matched,
            "crossVerse": len(cross["realizedIds"]) + len(cross["accountedIds"]),
            "null": len(nulls["sourceIds"]) + len(nulls["targetIds"]),
        }
        # The Bridge-level "nothing left to do" (#216): every token has a home
        # -- a tC group, a cross-verse link or a reasoned null -- and the verse
        # has a source and no structural problem. `status` and
        # `completionState` keep telling the translationCore truth beside it.
        accounted = (
            source_available and not issues
            and not remaining_sources and not remaining_targets
        )
        return {
            "chapter": str(chapter), "verse": str(verse),
            "alignment": alignment.to_dict(),
            "topTokens": top_tokens, "bottomTokens": bottom_tokens, "groups": groups,
            "status": work_state,
            "crossVerseLinks": cross["links"],
            "crossVerseGroups": cross["groups"],
            "crossVerseAccountedIds": cross["accountedIds"],
            "crossVerseRealizedIds": cross["realizedIds"],
            "crossVerseAccounted": len(cross["accountedIds"]),
            "crossVerseRealized": len(cross["realizedIds"]),
            "fullyAccounted": fully_accounted,
            "nullDecisions": {"source": nulls["source"], "target": nulls["target"]},
            "accountedBy": accounted_by,
            "accounted": accounted,
            "autoAlign": self._auto_align_summary(str(chapter), str(verse), alignment),
            "completionState": self.project.word_alignment_state(chapter, verse),
            "sourceAvailable": source_available,
            "sourceMessage": "" if source_available else (
                "This verse has no original-language source tokens. Import a translationCore project "
                "or aligned USFM 3 containing source alignment milestones before creating alignments."
            ),
            "sourceDirection": source_direction, "targetDirection": target_direction,
            "issues": issues, "canComplete": can_complete,
            "gaps": gaps,
            "history": self.project.alignment_history(chapter, verse)[:20],
            "chapterStatus": (
                chapter_counts if chapter_counts is not None
                else self.alignment_status(chapter)["counts"]
            ),
        }

    def get_alignment(self, chapter: str, verse: str) -> dict[str, Any]:
        return self._alignment_context(chapter, verse)

    def get_alignment_range(self, chapter: str, verses: list[str]) -> dict[str, Any]:
        """alignment.getRange: the alignment.get context for several verses of
        one chapter in a single round-trip (#116, cross-verse alignment view).

        Verse strings are opaque -- bridges ("3-4") and lettered segments
        ("3a") are real input (CLAUDE.md gotcha 12) and are passed through
        unchanged, never parsed as integers. Caller order is preserved;
        duplicates keep their first occurrence. The chapter status is
        computed once and shared by every context instead of being rescanned
        per verse.
        """
        self._require_project()
        chapter = str(chapter)
        if not isinstance(verses, list) or not verses:
            raise ProjectError("alignment.getRange requires a non-empty list of verses.")
        if chapter not in self.project.chapters():
            raise ProjectError(f"Chapter {chapter} does not exist in this project.")
        known = set(self.project.verses(chapter))
        ordered: list[str] = []
        for raw in verses:
            verse = str(raw)
            if verse not in known:
                raise ProjectError(f"Verse {chapter}:{verse} does not exist in this project.")
            if verse not in ordered:
                ordered.append(verse)
        counts = self.alignment_status(chapter)["counts"]
        contexts = [
            self._alignment_context(chapter, verse, chapter_counts=counts) for verse in ordered
        ]
        return {"chapter": chapter, "verses": contexts, "chapterStatus": counts}

    def gap_scan(self, chapter: str) -> dict[str, Any]:
        """alignment.gapScan: every verse of one chapter with its alignment gaps
        *named*, not merely counted (#137).

        Deliberately not `[self._alignment_context(c, v) for v in verses]`:
        `load_alignment_chapter` re-reads and re-parses the whole chapter JSON on
        every call and caches nothing, so that shape costs one full chapter parse
        per verse, plus each verse's history, chapter-status and target-text
        reads -- none of which a gap needs. This reads the chapter once and the
        link table once.

        Verse strings stay opaque throughout (bridges "3-4", segments "3a" --
        CLAUDE.md gotcha 12). A verse whose alignment entry is missing or
        unparseable contributes an entry with `readable: false` rather than
        failing the scan: a chapter-wide view that dies on one bad verse is
        useless exactly when it is most needed.
        """
        self._require_project()
        chapter = str(chapter)
        if chapter not in self.project.chapters():
            raise ProjectError(f"Chapter {chapter} does not exist in this project.")
        chapter_data = self.project.load_alignment_chapter(chapter)

        # One read of the link table for the whole chapter. `links_for_verse`
        # would be two queries per verse; only *active* links close a gap.
        realized_by_verse: dict[tuple[str, str], list[str]] = {}
        accounted_by_verse: dict[tuple[str, str], list[str]] = {}
        try:
            active_links = self.project.cross_verse_links.active_links()
        except Exception:
            active_links = []
        for link in active_links:
            source, target = link.get("source") or {}, link.get("target") or {}
            realized_by_verse.setdefault(
                (str(source.get("chapter") or ""), str(source.get("verse") or "")), [],
            ).append(str(source.get("signature") or ""))
            accounted_by_verse.setdefault(
                (str(target.get("chapter") or ""), str(target.get("verse") or "")), [],
            ).append(str(target.get("signature") or ""))
        # #216: and one read of the null decisions, for the same reason.
        null_by_verse: dict[tuple[str, str, str], list[str]] = {}
        try:
            active_nulls = self.project.null_decisions.active_decisions()
        except Exception:
            active_nulls = []
        for decision in active_nulls:
            null_by_verse.setdefault(
                (str(decision.get("chapter") or ""), str(decision.get("verse") or ""), str(decision.get("side") or "")), [],
            ).append(str((decision.get("token") or {}).get("signature") or ""))

        verses: list[dict[str, Any]] = []
        total_null = 0
        total_source = total_target = verses_with_gaps = 0
        for verse in self.project.verses(chapter):
            raw = chapter_data.get(verse)
            try:
                if raw is None:
                    raise ProjectError(f"No alignment data for {chapter}:{verse}")
                alignment = VerseAlignment.from_dict(raw)
                inventory = make_inventory(alignment)
            except Exception:
                verses.append({
                    "chapter": chapter, "verse": verse, "readable": False,
                    "sourceGaps": [], "targetGaps": [],
                    "gaps": {"sourceUnmatched": 0, "targetUnmatched": 0},
                })
                continue
            # Signatures are what the link store persists; resolve them to this
            # load's positional ids here, the same way _cross_verse_annotations
            # does per verse.
            realized = [
                token_id for signature in realized_by_verse.get((chapter, verse), ())
                if (token_id := inventory.top_sig_to_id.get(signature))
            ]
            accounted = [
                token_id for signature in accounted_by_verse.get((chapter, verse), ())
                if (token_id := inventory.bottom_sig_to_id.get(signature))
            ]
            null_source = [
                token_id for signature in null_by_verse.get((chapter, verse, "source"), ())
                if (token_id := inventory.top_sig_to_id.get(signature))
            ]
            null_target = [
                token_id for signature in null_by_verse.get((chapter, verse, "target"), ())
                if (token_id := inventory.bottom_sig_to_id.get(signature))
            ]
            source_ids, target_ids = alignment_gaps.gap_ids(
                inventory, alignment_gaps.group_views(alignment, inventory),
                realized_ids=realized, accounted_ids=accounted,
                null_source_ids=null_source, null_target_ids=null_target,
            )
            total_null += len(null_source) + len(null_target)
            total_source += len(source_ids)
            total_target += len(target_ids)
            if source_ids or target_ids:
                verses_with_gaps += 1
            verses.append({
                "chapter": chapter, "verse": verse, "readable": True,
                "sourceGaps": alignment_gaps.describe_tokens(inventory, source_ids, bottom=False),
                "targetGaps": alignment_gaps.describe_tokens(inventory, target_ids, bottom=True),
                "gaps": {"sourceUnmatched": len(source_ids), "targetUnmatched": len(target_ids)},
                "crossVerseRealized": len(realized), "crossVerseAccounted": len(accounted),
                "nullDecided": {"source": len(null_source), "target": len(null_target)},
                "sourceAvailable": bool(inventory.top_ids),
            })
        return {
            "chapter": chapter, "verses": verses,
            "totals": {
                "sourceUnmatched": total_source, "targetUnmatched": total_target,
                "versesWithGaps": verses_with_gaps, "verses": len(verses),
                "nullDecided": total_null,
            },
        }

    def _corpus_stats(
        self, project: Optional[TranslationCoreProject] = None,
    ) -> corpus_stats_tool.CorpusStatsTable:
        """The book's corpus statistics, built once and cached until an
        alignment mutation invalidates it (see `_corpus_stats_by_book`)."""
        project = project or self.project
        book_key = str(project.path)
        table = self._corpus_stats_by_book.get(book_key)
        if table is None:
            table = corpus_stats_tool.build_corpus_stats(project)
            self._corpus_stats_by_book[book_key] = table
        return table

    def propose_cross_verse(self, chapter: str, verses: list[str]) -> dict[str, Any]:
        """alignment.crossVerse.propose: for each unmatched source token in the
        range, where it was probably realized in another verse (#138/#139).

        Read-only. Accepting a proposal is a separate, human-initiated
        `alignment.crossVerse.link` call -- nothing here writes, and there is no
        confidence at which a link is created automatically.
        """
        self._require_project()
        chapter = str(chapter)
        if not isinstance(verses, list) or not verses:
            raise ProjectError(
                "alignment.crossVerse.propose requires a non-empty list of verses."
            )
        scan = self.gap_scan(chapter)
        by_verse = {entry["verse"]: entry for entry in scan["verses"]}
        wanted: list[str] = []
        for raw in verses:
            verse = str(raw)
            if verse not in by_verse:
                raise ProjectError(f"Verse {chapter}:{verse} does not exist in this project.")
            if verse not in wanted:
                wanted.append(verse)
        # An unreadable verse contributes no gaps rather than failing the call,
        # the same way it does in the scan itself.
        gaps = {
            verse: by_verse[verse] for verse in wanted if by_verse[verse].get("readable")
        }
        result = cross_verse_proposals.propose(
            gaps, self._corpus_stats(),
            chapter=chapter, verse_order=self.project.verses(chapter),
        )
        result["verses"] = wanted
        return result

    def _cross_verse_gloss_table(self, gaps_by_verse: dict[str, Any]) -> dict[str, str]:
        """One short English gloss per distinct Strong's number in the source gaps.

        A model asked to judge whether a Tamil word realizes `θεοῦ` does far better
        told that it means "God"; the reviewer reading the reason back needs the
        same. Keyed on Strong's and resolved once per number, because a range
        routinely repeats one.
        """
        glosses: dict[str, str] = {}
        for entry in gaps_by_verse.values():
            for token in entry.get("sourceGaps", ()):
                strong = str(token.get("strong") or "")
                if not strong or strong in glosses:
                    continue
                try:
                    glosses[strong] = self._gloss_for_strong(strong, str(token.get("morph") or ""))
                except Exception:
                    # A missing gloss weakens the prompt; it must never fail the
                    # request. The lexicon is a bundled convenience, not a source
                    # of truth about the alignment.
                    glosses[strong] = ""
        return glosses

    def _gloss_for_strong(self, strong: str, morph: str) -> str:
        """A short English gloss for one (possibly compound) Strong's value.

        Language normally comes from the morph code, but a token can reach here
        without one -- older alignment data, and any producer that writes `strong`
        without `x-morph`. The H/G prefix still says which lexicon to read, so
        fall back to it rather than silently returning nothing: a prompt that
        says `θεοῦ` and a prompt that says `θεοῦ (God)` are not the same prompt.
        """
        language_id, _ = decode_morph(morph)
        parts = [part for part in strong.split(":") if part]
        glosses: list[str] = []
        for part in parts:
            language = language_id or ("hbo" if part[:1].upper() == "H" else "el-x-koine")
            entry = lexicon_entry_for_strong(part, language)
            if not entry:
                prefix = HEBREW_PREFIX_LABELS.get(part) if language == "hbo" else None
                if prefix:
                    glosses.append(prefix)
                continue
            # `usage` (what the KJV rendered it as) over `meaning` (the dictionary
            # definition), for the same reason the alignment labels prefer it
            # (#145): a translator wants the renderings, not the definition.
            text = str(entry.get("usage") or entry.get("meaning") or "").strip()
            if text:
                glosses.append(text)
        return "; ".join(glosses)[:160]

    def ai_propose_cross_verse(self, chapter: str, verses: list[str]) -> dict[str, Any]:
        """alignment.crossVerse.aiPropose: the same question as
        `alignment.crossVerse.propose`, asked of a model as well (#146).

        Read-only, exactly like its offline sibling: this returns proposals and
        writes nothing. A link is still written only by
        `alignment.crossVerse.link`, whether the reviewer clicked Accept or the
        caller is applying a proposal this marked `autoLinkable`.

        `autoLinkable` is set only where the model's pick and the offline
        scorer's top candidate are the same uncontested pair, so the automatic
        write rests on two independent methods agreeing -- never on the model's
        own confidence, which is as uncalibrated as everything else in this
        pipeline.

        Returns a structured `unavailable` rather than raising when no API key is
        configured, the shape `start_triage` established: an offline project is a
        supported state, not an error the reviewer has to dismiss.
        """
        self._require_project()
        chapter = str(chapter)
        if not isinstance(verses, list) or not verses:
            raise ProjectError("A cross-verse AI proposal needs at least one verse.")
        try:
            client = self._ai_client()
        except AIError as exc:
            return {
                "chapter": chapter, "verses": [str(v) for v in verses], "proposals": [],
                "calibrationVersion": cross_verse_ai_proposals.AI_PROPOSAL_CALIBRATION_VERSION,
                "unavailable": {"reason": "no-api-key", "message": str(exc)},
            }

        # The offline pass first: it is the other half of the agreement gate, and
        # it is also what makes a disagreement visible rather than invisible.
        offline = self.propose_cross_verse(chapter, verses)
        wanted = [str(verse) for verse in offline.get("verses", verses)]
        scan = self.gap_scan(chapter)
        gaps_by_verse = {
            str(entry["verse"]): entry for entry in scan.get("verses", ())
            if str(entry["verse"]) in set(wanted) and entry.get("readable")
        }
        verse_texts: dict[str, str] = {}
        for verse in gaps_by_verse:
            try:
                verse_texts[verse] = strip_usfm(self.project.target_verse_text(chapter, verse))
            except Exception:
                verse_texts[verse] = ""

        def call_model(instructions: str, input_text: str) -> dict[str, Any]:
            return client.propose_cross_verse_links(
                instructions, input_text, cross_verse_ai_proposals.LINK_SCHEMA,
            )

        result = cross_verse_ai_proposals.propose_with_model(
            gaps_by_verse, offline, call_model,
            chapter=chapter,
            verse_order=[str(v) for v in self.project.verses(chapter)],
            verse_texts=verse_texts,
            glosses=self._cross_verse_gloss_table(gaps_by_verse),
            error=AIError,
        )
        if result.get("modelConsulted"):
            usage = getattr(client, "last_usage", None)
            self.settings.record_ai_usage(
                getattr(usage, "total_tokens", 0) or 0, getattr(client, "last_cost_usd", 0.0) or 0.0,
            )
        result["chapter"] = chapter
        result["verses"] = wanted
        # The offline pass's own proposals are returned alongside, so the strip can
        # show what statistics alone found even when the model returned nothing.
        result["corpusProposals"] = offline.get("proposals", [])
        if offline.get("unavailable"):
            result["corpusUnavailable"] = offline["unavailable"]
        return result

    def _cross_verse_annotations(self, chapter: str, verse: str, inventory) -> dict[str, Any]:
        """Links touching one verse, with this verse's positional ids resolved
        from the stored signatures (ids are never persisted, #117)."""
        links = []
        accounted_ids: list[str] = []
        realized_ids: list[str] = []
        for link in self.project.cross_verse_links.links_for_verse(chapter, verse):
            source, target = link.get("source", {}), link.get("target", {})
            entry = dict(link)
            entry["sourceTopId"] = None
            entry["targetBottomId"] = None
            if source.get("chapter") == chapter and source.get("verse") == verse:
                entry["sourceTopId"] = inventory.top_sig_to_id.get(source.get("signature", ""))
            if target.get("chapter") == chapter and target.get("verse") == verse:
                entry["targetBottomId"] = inventory.bottom_sig_to_id.get(target.get("signature", ""))
            if link.get("state") == "active":
                if entry["sourceTopId"]:
                    realized_ids.append(entry["sourceTopId"])
                if entry["targetBottomId"]:
                    accounted_ids.append(entry["targetBottomId"])
            links.append(entry)
        # #217: the same rows, folded into their composite groups. A pair is a
        # row; a group is what the reviewer decided.
        groups: dict[str, dict[str, Any]] = {}
        for entry in links:
            group_id = cross_verse_tool.group_of(entry)
            group = groups.setdefault(group_id, {
                "groupId": group_id, "state": entry.get("state"),
                "sourceChapter": entry["source"]["chapter"], "sourceVerse": entry["source"]["verse"],
                "targetChapter": entry["target"]["chapter"], "targetVerse": entry["target"]["verse"],
                "sources": {}, "targets": {}, "linkIds": [],
            })
            group["sources"].setdefault(entry["source"]["signature"], {
                "word": entry["source"]["word"], "topId": entry["sourceTopId"],
            })
            group["targets"].setdefault(entry["target"]["signature"], {
                "word": entry["target"]["word"], "bottomId": entry["targetBottomId"],
            })
            group["linkIds"].append(entry["id"])
        group_list = []
        for group in groups.values():
            sources, targets = list(group.pop("sources").values()), list(group.pop("targets").values())
            group.update({
                "sources": sources, "targets": targets,
                "relation": cross_verse_tool.relation(len(sources), len(targets)),
                "sourceTopIds": [s["topId"] for s in sources if s["topId"]],
                "targetBottomIds": [t["bottomId"] for t in targets if t["bottomId"]],
            })
            group_list.append(group)
        return {
            "links": links, "groups": group_list,
            "accountedIds": list(dict.fromkeys(accounted_ids)),
            "realizedIds": list(dict.fromkeys(realized_ids)),
        }

    def _null_annotations(self, chapter: str, verse: str, inventory) -> dict[str, Any]:
        """One verse's null decisions (#216), with positional ids resolved from
        the stored signatures. Only an *active* decision closes a gap. A source
        decision whose signature this load cannot resolve is reported `stale`
        rather than dropped: no edit path removes a source token, so it means
        the source pack itself changed, and the reviewer should see that."""
        found: dict[str, list[dict[str, Any]]] = {"source": [], "target": []}
        ids: dict[str, list[str]] = {"source": [], "target": []}
        for decision in self.project.null_decisions.decisions_for_verse(chapter, verse):
            side = decision.get("side")
            if side not in found:
                continue
            signature = (decision.get("token") or {}).get("signature", "")
            lookup = inventory.top_sig_to_id if side == "source" else inventory.bottom_sig_to_id
            token_id = lookup.get(signature)
            active = decision.get("state") == "active"
            found[side].append({
                "id": token_id, "decisionId": decision.get("id"),
                "word": (decision.get("token") or {}).get("word", ""),
                "reason": decision.get("reason"), "note": decision.get("note", ""),
                "origin": decision.get("origin", "human"), "state": decision.get("state"),
                "invalidReason": decision.get("invalidReason"),
                "stale": active and token_id is None,
            })
            if active and token_id:
                ids[side].append(token_id)
        return {
            "source": found["source"], "target": found["target"],
            "sourceIds": ids["source"], "targetIds": ids["target"],
        }

    def _null_ref(self, ref: Any) -> tuple[str, str, str, str]:
        if not isinstance(ref, dict):
            raise ProjectError("A null decision needs {chapter, verse, side, id}.")
        chapter, verse = str(ref.get("chapter") or ""), str(ref.get("verse") or "")
        side, token_id = str(ref.get("side") or ""), str(ref.get("id") or "")
        if not chapter or not verse or not side or not token_id:
            raise ProjectError("A null decision needs chapter, verse, side and the token id.")
        if chapter not in self.project.chapters():
            raise ProjectError(f"Chapter {chapter} does not exist in this project.")
        if verse not in set(self.project.verses(chapter)):
            raise ProjectError(f"Verse {chapter}:{verse} does not exist in this project.")
        return chapter, verse, side, token_id

    def set_null_decision(self, params: Any, *, origin: str = "human") -> dict[str, Any]:
        """alignment.null.set (#216): record that one token has no counterpart,
        for a reason. Refused when the token already has a counterpart -- a
        source word in a group with target words, a target word in any group, or
        either end of an active cross-verse link: a null is a home of its own,
        and a token has one home."""
        self._require_project()
        chapter, verse, side, token_id = self._null_ref(params)
        side, reason = null_decisions_tool.validate(side, str(params.get("reason") or ""), origin)
        alignment = self.project.load_verse_alignment(chapter, verse)
        inventory = make_inventory(alignment)
        token = (inventory.top_ids if side == "source" else inventory.bottom_ids).get(token_id)
        if token is None:
            raise AlignmentError("That word is not in this verse any more. Reload before marking it.")
        if side == "source":
            if any(group.bottom_words and any(t.signature == token.signature for t in group.top_words)
                   for group in alignment.alignments):
                raise AlignmentError(
                    f"{token.word} is already aligned within {chapter}:{verse}; unalign it before marking it {reason.lower()}."
                )
        else:
            if any(t.signature == token.signature for t in alignment.aligned_bottom()):
                raise AlignmentError(
                    f"{token.word} is already aligned within {chapter}:{verse}; unalign it before marking it {reason.lower()}."
                )
            current = {t.signature for t in self._target_token_inventory(self.project.target_verse_text(chapter, verse))}
            if token.signature not in current:
                raise AlignmentError(f"{token.word} is no longer in the text of {chapter}:{verse}.")
        cross = self._cross_verse_annotations(chapter, verse, inventory)
        if token_id in (cross["realizedIds"] if side == "source" else cross["accountedIds"]):
            raise AlignmentError(
                f"{token.word} is linked to another verse; remove that link before marking it {reason.lower()}."
            )
        decision = self.project.null_decisions.set(
            chapter, verse, side, token, reason, note=str(params.get("note") or ""), origin=origin,
        )
        return self._null_result(decision)

    def clear_null_decision(self, decision_id: str) -> dict[str, Any]:
        self._require_project()
        if not decision_id:
            raise ProjectError("Clearing a null decision needs its decisionId.")
        decision = self.project.null_decisions.clear(decision_id)
        return self._null_result(decision)

    def _null_result(self, decision: dict[str, Any]) -> dict[str, Any]:
        # A null decision is alignment state Stage 6B/8 read (#218), so it
        # stales downstream records exactly as a link change does.
        if self.passage_semantic_runtime is not None:
            self.passage_semantic_runtime.synchronize_alignment_state()
        return {
            "decision": decision,
            "context": self._alignment_context(decision["chapter"], decision["verse"]),
        }

    def _cross_verse_ref(self, ref: Any, id_key: str) -> tuple[str, str, str]:
        if not isinstance(ref, dict):
            raise ProjectError("A cross-verse link needs source and target as {chapter, verse, id} objects.")
        chapter, verse, token_id = str(ref.get("chapter") or ""), str(ref.get("verse") or ""), str(ref.get(id_key) or "")
        if not chapter or not verse or not token_id:
            raise ProjectError(f"A cross-verse link end needs chapter, verse and {id_key}.")
        if chapter not in self.project.chapters():
            raise ProjectError(f"Chapter {chapter} does not exist in this project.")
        if verse not in set(self.project.verses(chapter)):
            raise ProjectError(f"Verse {chapter}:{verse} does not exist in this project.")
        return chapter, verse, token_id

    def _cross_verse_result(self, link: dict[str, Any]) -> dict[str, Any]:
        source, target = link["source"], link["target"]
        # #119: a link is alignment state Stage 6B reads as evidence, so the
        # same digest-keyed staling that follows a tC alignment save follows
        # a link change -- in this session, not only after a reopen.
        if self.passage_semantic_runtime is not None:
            self.passage_semantic_runtime.synchronize_alignment_state()
        return {
            "link": link,
            "source": self._alignment_context(source["chapter"], source["verse"]),
            "target": self._alignment_context(target["chapter"], target["verse"]),
        }

    def link_cross_verse(
        self, source: Any, target: Any, origin: str = "", *,
        sources: Any = None, targets: Any = None, run_id: str = "",
    ) -> dict[str, Any]:
        """Record that source token(s) of one verse are realized by target
        word(s) of another verse (#117), as one composite group (#217).

        Two shapes: the original single pair `{source, target}`, or
        `{sources: [...], targets: [...]}` for a 1:N, N:1 or N:M group. Either
        way every end must have no other home -- not aligned within its own
        verse, not marked null, not at either end of a different active link.

        A single pair onto a source or target that is *already* in a group
        between the same two verses extends that group (unlink, then link the
        union): dragging a second word onto a cross-verse realization means
        "this one too", and refusing it would make the drag useless. A group
        reaching a third verse is refused instead -- one group spans exactly
        two verses.

        Refused when both ends are the same verse (that is what
        alignment.realign is for), or when a target word is not in the current
        text. Nothing in alignmentData/ changes.
        """
        self._require_project()
        source_refs = list(sources) if isinstance(sources, list) and sources else [source]
        target_refs = list(targets) if isinstance(targets, list) and targets else [target]
        parsed_sources = [self._cross_verse_ref(ref, "topId") for ref in source_refs]
        parsed_targets = [self._cross_verse_ref(ref, "bottomId") for ref in target_refs]
        if len({(c, v) for c, v, _ in parsed_sources}) != 1 or len({(c, v) for c, v, _ in parsed_targets}) != 1:
            raise AlignmentError(
                "A cross-verse group links words of one source verse to words of one other verse."
            )
        s_chapter, s_verse, _ = parsed_sources[0]
        t_chapter, t_verse, _ = parsed_targets[0]
        if (s_chapter, s_verse) == (t_chapter, t_verse):
            raise AlignmentError(
                "Both words are in the same verse; align them in that verse instead of linking across verses."
            )
        s_alignment = self.project.load_verse_alignment(s_chapter, s_verse)
        s_inventory = make_inventory(s_alignment)
        t_alignment = self.project.load_verse_alignment(t_chapter, t_verse)
        t_inventory = make_inventory(t_alignment)
        s_tokens = []
        for _, _, top_id in parsed_sources:
            token = s_inventory.top_ids.get(top_id)
            if token is None:
                raise AlignmentError("The source token id is not in that verse any more. Reload before linking.")
            s_tokens.append(token)
        t_tokens = []
        for _, _, bottom_id in parsed_targets:
            token = t_inventory.bottom_ids.get(bottom_id)
            if token is None:
                raise AlignmentError("The target word id is not in that verse any more. Reload before linking.")
            t_tokens.append(token)
        current = {t.signature for t in self._target_token_inventory(self.project.target_verse_text(t_chapter, t_verse))}
        for s_token in s_tokens:
            self._refuse_if_null_decided(s_chapter, s_verse, "source", s_token)
            for group in s_alignment.alignments:
                if group.bottom_words and any(t.signature == s_token.signature for t in group.top_words):
                    raise AlignmentError(
                        f"{s_token.word} is already aligned within {s_chapter}:{s_verse}; unalign it there first."
                    )
        for t_token in t_tokens:
            self._refuse_if_null_decided(t_chapter, t_verse, "target", t_token)
            if any(t.signature == t_token.signature for t in t_alignment.aligned_bottom()):
                raise AlignmentError(
                    f"{t_token.word} is already aligned within {t_chapter}:{t_verse}; unalign it there first."
                )
            if t_token.signature not in current:
                raise AlignmentError(
                    f"{t_token.word} is no longer in the text of {t_chapter}:{t_verse}."
                )

        # One home (#217): an end already in an active group either extends
        # that group (same two verses, single-pair call) or is refused.
        store = self.project.cross_verse_links
        existing_groups: dict[str, list[dict[str, Any]]] = {}
        for s_token in s_tokens:
            for link in self._active_links_for_end(s_chapter, s_verse, "source", s_token.signature):
                existing_groups.setdefault(cross_verse_tool.group_of(link), []).append(link)
        for t_token in t_tokens:
            for link in self._active_links_for_end(t_chapter, t_verse, "target", t_token.signature):
                existing_groups.setdefault(cross_verse_tool.group_of(link), []).append(link)
        extend: list[dict[str, Any]] = []
        for group_id in existing_groups:
            members = [m for m in store.group_members(group_id) if m.get("state") == "active"]
            if not members:
                continue
            first = members[0]
            same_verses = (
                (first["source"]["chapter"], first["source"]["verse"]) == (s_chapter, s_verse)
                and (first["target"]["chapter"], first["target"]["verse"]) == (t_chapter, t_verse)
            )
            if not same_verses or len(s_tokens) > 1 or len(t_tokens) > 1:
                held = existing_groups[group_id][0]
                src, tgt = held["source"], held["target"]
                raise AlignmentError(
                    f"{src['word']} ({src['chapter']}:{src['verse']}) is already linked to "
                    f"{tgt['word']} ({tgt['chapter']}:{tgt['verse']}); remove that link first."
                )
            extend.extend(members)
        if extend:
            held_sources = {m["source"]["signature"] for m in extend}
            held_targets = {m["target"]["signature"] for m in extend}
            source_sigs = held_sources | {t.signature for t in s_tokens}
            target_sigs = held_targets | {t.signature for t in t_tokens}
            if source_sigs == held_sources and target_sigs == held_targets:
                held = extend[0]
                raise AlignmentError(
                    f"{held['source']['word']} ({s_chapter}:{s_verse}) is already linked to "
                    f"{held['target']['word']} ({t_chapter}:{t_verse})."
                )
            s_tokens = [t for t in s_inventory.top_ids.values() if t.signature in source_sigs]
            t_tokens = [t for t in t_inventory.bottom_ids.values() if t.signature in target_sigs]
            if len(s_tokens) != len(source_sigs) or len(t_tokens) != len(target_sigs):
                raise AlignmentError("A word of that cross-verse group is no longer in its verse. Reload first.")
            group_ids = {cross_verse_tool.group_of(m) for m in extend}
            for group_id in group_ids:
                store.unlink_group(group_id, origin=str(origin or ""), extra={"extendedInto": True})
        written = store.link_group(
            s_chapter, s_verse, s_tokens, t_chapter, t_verse, t_tokens,
            origin=str(origin or ""), run_id=run_id,
        )
        result = self._cross_verse_result(written[0])
        result["group"] = {
            "groupId": written[0]["groupId"], "relation": written[0]["relation"],
            "linkIds": [w["id"] for w in written], "extended": bool(extend),
        }
        return result

    def _active_links_for_end(self, chapter: str, verse: str, side: str, signature: str) -> list[dict[str, Any]]:
        found = []
        for link in self.project.cross_verse_links.links_for_verse(chapter, verse):
            end = link.get(side, {})
            if (link.get("state") == "active" and end.get("chapter") == chapter
                    and end.get("verse") == verse and end.get("signature") == signature):
                found.append(link)
        return found

    def _refuse_if_homed_elsewhere(
        self, chapter: str, verse: str, current: VerseAlignment, proposed: VerseAlignment,
    ) -> None:
        """One token, one home (#217). A token that *gains* a counterpart in
        this save -- a source word entering a group with target words, a target
        word entering any group -- must not already be at an end of an active
        cross-verse link or carry an active null decision. Tokens leaving a
        group never conflict, and a token that was already grouped before this
        save is left alone (a pre-existing double home is reported by the
        verse context, not made worse or silently repaired here)."""
        def grouped_tops(alignment: VerseAlignment) -> set[str]:
            return {t.signature for g in alignment.alignments if g.bottom_words for t in g.top_words}

        def grouped_bottoms(alignment: VerseAlignment) -> set[str]:
            return {t.signature for t in alignment.aligned_bottom()}

        gained_tops = grouped_tops(proposed) - grouped_tops(current)
        gained_bottoms = grouped_bottoms(proposed) - grouped_bottoms(current)
        if not gained_tops and not gained_bottoms:
            return
        words = {t.signature: t.word for t in proposed.all_top()}
        words.update({t.signature: t.word for t in proposed.all_bottom()})
        for link in self.project.cross_verse_links.links_for_verse(chapter, verse):
            if link.get("state") != "active":
                continue
            for side, gained in (("source", gained_tops), ("target", gained_bottoms)):
                end = link.get(side, {})
                if end.get("chapter") == chapter and end.get("verse") == verse and end.get("signature") in gained:
                    other = link["target"] if side == "source" else link["source"]
                    raise AlignmentError(
                        f"{end.get('word')} in {chapter}:{verse} is linked to {other.get('word')} in "
                        f"{other.get('chapter')}:{other.get('verse')}; remove that link before aligning it here."
                    )
        for decision in self.project.null_decisions.decisions_for_verse(chapter, verse):
            if decision.get("state") != "active":
                continue
            gained = gained_tops if decision.get("side") == "source" else gained_bottoms
            signature = (decision.get("token") or {}).get("signature")
            if signature in gained:
                raise AlignmentError(
                    f"{words.get(signature, (decision.get('token') or {}).get('word', ''))} in {chapter}:{verse} is marked "
                    f"{str(decision.get('reason', '')).lower()}; clear that before aligning it."
                )

    def _refuse_if_null_decided(self, chapter: str, verse: str, side: str, token) -> None:
        """A token marked as having no counterpart cannot also be linked
        (#216): one token, one home. The reviewer clears the decision first."""
        for decision in self.project.null_decisions.decisions_for_verse(chapter, verse):
            if (decision.get("state") == "active" and decision.get("side") == side
                    and (decision.get("token") or {}).get("signature") == token.signature):
                raise AlignmentError(
                    f"{token.word} in {chapter}:{verse} is marked {str(decision.get('reason', '')).lower()}; "
                    "clear that first."
                )

    def unlink_cross_verse(self, link_id: str, group_id: str = "") -> dict[str, Any]:
        """Remove a link's whole group (#217), by any of its link ids or by
        its `groupId`. A composite group is one realization; removing one pair
        of it would leave a claim nobody made."""
        self._require_project()
        if not link_id and not group_id:
            raise ProjectError("A cross-verse unlink needs the link id or the group id.")
        if link_id:
            link = self.project.cross_verse_links.unlink(link_id)
        else:
            link = self.project.cross_verse_links.unlink_group(group_id)[0]
        return self._cross_verse_result(link)

    # ---- automatic two-pass alignment (#219) -----------------------------------

    def _auto_window_verses(self, chapter: str, verses: Any) -> list[str]:
        """Validate a window: 1..5 known verses of one chapter, contiguous in
        the chapter's own order (verse strings stay opaque, gotcha 12)."""
        if not isinstance(verses, list) or not verses:
            raise ProjectError("Automatic alignment needs a list of verses.")
        if chapter not in self.project.chapters():
            raise ProjectError(f"Chapter {chapter} does not exist in this project.")
        order = [str(v) for v in self.project.verses(chapter) if str(v) != "front"]
        wanted = list(dict.fromkeys(str(v) for v in verses))
        for verse in wanted:
            if verse not in order:
                raise ProjectError(f"Verse {chapter}:{verse} does not exist in this project.")
        if len(wanted) > alignment_window.MAX_WINDOW_VERSES:
            raise ProjectError(
                f"Automatic alignment takes at most {alignment_window.MAX_WINDOW_VERSES} verses at a time; "
                "use the chapter job for more."
            )
        positions = sorted(order.index(v) for v in wanted)
        if positions[-1] - positions[0] + 1 != len(positions):
            raise ProjectError("Automatic alignment needs consecutive verses.")
        return [order[i] for i in positions]

    def _auto_align_summary(self, chapter: str, verse: str, alignment) -> dict[str, Any] | None:
        """The last automatic pass on this verse (#219), as every alignment
        context reports it. `stale` when the verse or its alignment changed
        after the pass, so its verdict may no longer describe it."""
        try:
            verdict = self._auto_verdict(chapter, verse)
        except Exception:
            return None
        if not verdict:
            return None
        try:
            stale = (
                verdict.get("alignmentFingerprint") != alignment_fingerprint(alignment)
                or verdict.get("targetFingerprint") != target_token_fingerprint(
                    alignment, self.project.target_verse_text(chapter, verse))
            )
        except Exception:
            stale = True
        return {
            "verdict": verdict.get("verdict"), "runId": verdict.get("runId", ""),
            "createdAt": verdict.get("createdAt", ""), "stale": bool(stale) and verdict.get("verdict") != "REVERTED",
            "issues": len(verdict.get("issues") or ()), "suggestions": len(verdict.get("suggestions") or ()),
        }

    def _auto_verdict(self, chapter: str, verse: str) -> dict[str, Any] | None:
        identity = self.project.workbench_identity
        row_id = natural_row_id(identity.project_id, self.project.book_id, "alignment_verdicts", str(chapter), str(verse))
        row = self.project.workbench.get("alignment_verdicts", row_id)
        if row is None:
            return None
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    def _write_auto_verdict(self, chapter: str, verse: str, payload: dict[str, Any]) -> None:
        identity = self.project.workbench_identity
        row_id = natural_row_id(identity.project_id, self.project.book_id, "alignment_verdicts", str(chapter), str(verse))
        self.project.workbench._write(
            "alignment_verdicts", row_id,
            project_id=identity.project_id, book_id=self.project.book_id, payload=payload,
            actor_id=identity.actor_id, device_id=identity.device_id, expected_revision=None,
            extra_columns={"chapter": str(chapter), "verse": str(verse)},
        )

    def _auto_owned(self, chapter: str, verses: list[str]) -> dict[str, set]:
        """What earlier automatic passes wrote in these verses and is still
        exactly as written, from their ledgers: tC groups (by their signature
        sets), link groups and null decisions. Everything else a token is in is
        a reviewer's home."""
        owned: dict[str, set] = {"groups": set(), "links": set(), "nulls": set()}
        for verse in verses:
            ledger = (self._auto_verdict(chapter, verse) or {}).get("applied") or {}
            for group in ledger.get("groups", ()):
                owned["groups"].add((verse, frozenset(group.get("tops", ())), frozenset(group.get("bottoms", ()))))
            owned["links"].update(str(g) for g in ledger.get("links", ()))
            owned["nulls"].update(str(n) for n in ledger.get("nulls", ()))
        return owned

    def _build_auto_window(self, chapter: str, verses: list[str], owned: dict[str, set]):
        """The window both passes see, with every token marked homed when a
        reviewer (not an earlier automatic pass) gave it a home."""
        window_verses = []
        first_alignment = None
        glosses_input: dict[str, Any] = {}
        texts: list[str] = []
        for verse in verses:
            alignment = self.project.load_verse_alignment(chapter, verse)
            first_alignment = first_alignment or alignment
            inventory = make_inventory(alignment)
            text = self.project.target_verse_text(chapter, verse)
            texts.append(text)
            human_tops: set[str] = set()
            human_bottoms: set[str] = set()
            for group in alignment.alignments:
                tops = frozenset(t.signature for t in group.top_words)
                bottoms = frozenset(t.signature for t in group.bottom_words)
                if not bottoms:
                    continue
                if (verse, tops, bottoms) in owned["groups"]:
                    continue
                human_tops |= tops
                human_bottoms |= bottoms
            for link in self.project.cross_verse_links.links_for_verse(chapter, verse):
                if link.get("state") != "active":
                    continue
                # An earlier pass's link group is this pass's to re-decide only
                # when both its ends are inside this window; one reaching a verse
                # this window cannot see stays put, as a fixed home.
                inside = all(
                    end.get("chapter") == chapter and end.get("verse") in verses
                    for end in (link["source"], link["target"])
                )
                if cross_verse_tool.group_of(link) in owned["links"] and inside:
                    continue
                if (link["source"]["chapter"], link["source"]["verse"]) == (chapter, verse):
                    human_tops.add(link["source"]["signature"])
                if (link["target"]["chapter"], link["target"]["verse"]) == (chapter, verse):
                    human_bottoms.add(link["target"]["signature"])
            for decision in self.project.null_decisions.decisions_for_verse(chapter, verse):
                if decision.get("state") != "active" or decision.get("id") in owned["nulls"]:
                    continue
                signature = (decision.get("token") or {}).get("signature")
                (human_tops if decision.get("side") == "source" else human_bottoms).add(signature)
            sources = [
                alignment_window.WindowToken(
                    alignment_agreement.TokenKey("source", verse, token.signature), token.word, token_id,
                    strong=token.strong, lemma=token.lemma, morph=token.morph,
                    homed=token.signature in human_tops,
                )
                for token_id, token in inventory.top_ids.items()
            ]
            targets = []
            for token in self._target_token_inventory(text):
                token_id = inventory.bottom_sig_to_id.get(token.signature)
                if not token_id:
                    continue
                targets.append(alignment_window.WindowToken(
                    alignment_agreement.TokenKey("target", verse, token.signature), token.word, token_id,
                    homed=token.signature in human_bottoms,
                ))
            glosses_input[verse] = {"sourceGaps": [{"strong": t.strong, "morph": t.morph} for t in inventory.top_ids.values()]}
            window_verses.append(alignment_window.WindowVerse(verse, strip_usfm(text), sources, targets))
        language = PluginRegistry().detect_project(self.project, first_alignment, " ".join(texts))
        reference = f"{self.project.book_id.upper()} {chapter}:{verses[0]}" + (f"-{verses[-1]}" if len(verses) > 1 else "")
        window = alignment_window.build_window(
            chapter, window_verses, reference=reference,
            glosses=self._cross_verse_gloss_table(glosses_input),
            source_language=getattr(language, "source_name", ""), target_language=getattr(language, "target_name", ""),
        )
        return window, str(getattr(language, "prompt_guidance", "") or "")

    def _auto_corpus(self, chapter: str, verses: list[str]) -> "alignment_agreement.CorpusCheck":
        try:
            offline = self.propose_cross_verse(chapter, verses)
        except Exception as exc:  # optional evidence: a failure costs the check, not the run
            return alignment_agreement.CorpusCheck(checked=False, reason=f"error: {exc}")
        if offline.get("unavailable"):
            return alignment_agreement.CorpusCheck(
                checked=False, reason=str((offline["unavailable"] or {}).get("reason") or "unavailable"),
            )
        check = alignment_agreement.CorpusCheck(checked=True)
        for proposal in offline.get("proposals", ()) or ():
            source, target = proposal.get("source") or {}, proposal.get("target") or {}
            s_key = alignment_agreement.TokenKey("source", str(source.get("verse", "")), str(source.get("signature", "")))
            t_key = alignment_agreement.TokenKey("target", str(target.get("verse", "")), str(target.get("signature", "")))
            check.best.setdefault(s_key, t_key)
            if proposal.get("contested"):
                check.contested.add(t_key)
        return check

    def _token_id(self, chapter: str, verse: str, side: str, signature: str) -> str | None:
        inventory = make_inventory(self.project.load_verse_alignment(chapter, verse))
        lookup = inventory.top_sig_to_id if side == "source" else inventory.bottom_sig_to_id
        return lookup.get(signature)

    def _revert_auto_writes(
        self, chapter: str, verse: str, ledger: dict[str, Any], *, run_id: str,
        window: list[str] | None = None,
    ) -> dict[str, Any]:
        """Undo what one automatic pass wrote in one verse -- only what is still
        exactly as it was written. A group, link or decision a reviewer has
        since changed is left alone and reported in `skipped`."""
        skipped: list[str] = []
        kept_links: list[str] = []
        extra = {"revertOf": ledger.get("runId", ""), "supersededBy": run_id} if run_id else {"revertOf": ledger.get("runId", "")}
        store = self.project.cross_verse_links
        for group_id in ledger.get("links", ()):
            members = [m for m in store.group_members(str(group_id)) if m.get("state") == "active"]
            if not members:
                continue
            if window is not None and not all(
                m[end]["chapter"] == chapter and m[end]["verse"] in window
                for m in members for end in ("source", "target")
            ):
                # Reaches outside the window that is re-deciding: not ours to undo.
                kept_links.append(str(group_id))
                continue
            store.unlink_group(str(group_id), origin="ai-auto", extra=extra)
        for decision_id in ledger.get("nulls", ()):
            decision = self.project.null_decisions.get(str(decision_id))
            if decision is None:
                continue
            if decision.get("origin") != "ai-auto":
                skipped.append(f"null {decision_id}")
                continue
            self.project.null_decisions.clear(str(decision_id), origin="ai-auto", extra=extra)
        groups = ledger.get("groups", ())
        if groups:
            current = self.project.load_verse_alignment(chapter, verse)
            intact = {
                (frozenset(t.signature for t in g.top_words), frozenset(t.signature for t in g.bottom_words))
                for g in current.alignments if g.bottom_words
            }
            bottoms = set()
            for group in groups:
                key = (frozenset(group.get("tops", ())), frozenset(group.get("bottoms", ())))
                if key in intact:
                    bottoms |= set(key[1])
                else:
                    skipped.append("group " + "+".join(sorted(group.get("tops", ()))))
            if bottoms:
                proposed = unalign_bottom(current, [t for t in current.aligned_bottom() if t.signature in bottoms])
                self._save_alignment(chapter, verse, proposed, current.to_dict(), "ai_auto_revert")
        return {"skipped": skipped, "keptLinks": kept_links}

    def auto_align_window(
        self, chapter: str, verses: Any, *, apply: bool = True, run_id: str = "", job_id: str = "",
        passes: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """alignment.window.autoAlign (#219): ask the window twice, write what
        both passes agree on, suggest the rest, report what neither placed.

        `passes` lets the chapter job (#221) hand in passes it already asked
        for, so reconciling across windows needs no second request.
        Returns a structured `unavailable` -- never raises -- when no provider
        is configured: offline is a supported state. Nothing is ever sent on
        open, import or check; this runs only on an explicit request.
        """
        self._require_project()
        chapter = str(chapter)
        ordered = self._auto_window_verses(chapter, verses)
        run_id = run_id or f"aa-{uuid.uuid4().hex[:12]}"
        base = {
            "chapter": chapter, "verses": ordered, "runId": run_id,
            "calibrationVersion": alignment_agreement.AGREEMENT_CALIBRATION_VERSION,
        }
        owned = self._auto_owned(chapter, ordered)
        window, guidance = self._build_auto_window(chapter, ordered, owned)
        usage = {"calls": 0, "totalTokens": 0, "estimatedCostUSD": 0.0}
        if passes is None:
            try:
                client = self._ai_client()
            except AIError as exc:
                return {**base, "results": [], "unavailable": {"reason": "no-api-key", "message": str(exc)}}

            def call_model(instructions: str, input_text: str, direction: str) -> dict[str, Any]:
                raw = client.propose_window_alignment(instructions, input_text, alignment_window.SCHEMA, direction)
                tokens = int(getattr(getattr(client, "last_usage", None), "total_tokens", 0) or 0)
                cost = float(getattr(client, "last_cost_usd", 0.0) or 0.0)
                self.settings.record_ai_usage(tokens, cost)
                usage["calls"] += 1
                usage["totalTokens"] += tokens
                usage["estimatedCostUSD"] = round(usage["estimatedCostUSD"] + cost, 6)
                return raw

            pass_a = alignment_window.ask(window, call_model, alignment_window.SOURCE_FIRST, guidance=guidance, error=AIError)
            pass_b = alignment_window.ask(window, call_model, alignment_window.TARGET_FIRST, guidance=guidance, error=AIError)
        else:
            pass_a = alignment_window.decode(passes.get(alignment_window.SOURCE_FIRST), window, alignment_window.SOURCE_FIRST, error=AIError)
            pass_b = alignment_window.decode(passes.get(alignment_window.TARGET_FIRST), window, alignment_window.TARGET_FIRST, error=AIError)
        corpus = self._auto_corpus(chapter, ordered)
        agreement = alignment_agreement.agree(
            pass_a, pass_b, tokens=window.tokens(), corpus=corpus, homed=window.homed(),
        )
        return self._apply_agreement(
            chapter, ordered, window, agreement, base=base, apply=apply, run_id=run_id, job_id=job_id,
            usage=usage, notes=[*pass_a.notes, *pass_b.notes],
        )

    def _apply_agreement(
        self, chapter: str, ordered: list[str], window, agreement, *, base: dict[str, Any], apply: bool,
        run_id: str, job_id: str, usage: dict[str, Any], notes: list[str],
    ) -> dict[str, Any]:
        TokenKey = alignment_agreement.TokenKey
        suggestions = list(agreement.suggestions)

        # Partition agreed edges: same-verse ones become tC groups per verse;
        # cross-verse ones become link groups, one per connected component. A
        # component reaching more than one source or target verse cannot be one
        # group (#217) and is offered instead.
        same_verse: dict[str, list[tuple]] = {}
        parent: dict[TokenKey, TokenKey] = {}

        def find(x):
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        cross_edges = []
        for (source, target), info in agreement.agreed_edges.items():
            if source.verse == target.verse:
                same_verse.setdefault(source.verse, []).append((source, target, info))
            else:
                cross_edges.append((source, target, info))
                parent[find(source)] = find(target)
        components: dict[TokenKey, list[tuple]] = {}
        for edge in cross_edges:
            components.setdefault(find(edge[0]), []).append(edge)
        cross_groups = []
        for edges in components.values():
            source_verses = {e[0].verse for e in edges}
            target_verses = {e[1].verse for e in edges}
            if len(source_verses) != 1 or len(target_verses) != 1:
                for source, target, info in edges:
                    suggestions.append(alignment_agreement.Suggestion(
                        kind="link", status="UNCERTAIN",
                        votes={alignment_window.SOURCE_FIRST: True, alignment_window.TARGET_FIRST: True},
                        source=source, target=target, confidence=int(info.get("confidence", 0)),
                        reason=alignment_agreement._join(info.get("reason"), "this realization spans more than two verses"),
                    ))
                continue
            cross_groups.append(edges)

        applied = {verse: {"groups": [], "links": [], "nulls": []} for verse in ordered}
        reverted: dict[str, Any] = {}
        failures: list[str] = []
        if apply:
            with self._checker_lock:
                # 1. Supersede the previous automatic pass's own writes.
                for verse in ordered:
                    ledger = (self._auto_verdict(chapter, verse) or {}).get("applied") or {}
                    if any(ledger.get(k) for k in ("groups", "links", "nulls")):
                        ledger = {**ledger, "runId": (self._auto_verdict(chapter, verse) or {}).get("runId", "")}
                        reverted[verse] = self._revert_auto_writes(chapter, verse, ledger, run_id=run_id, window=ordered)
                for verse, outcome in reverted.items():
                    applied[verse]["links"].extend(outcome.get("keptLinks", ()))
                # 2. Same-verse groups: compile agreed edges into legal tC groups.
                #    Thresholds are 0.0 on purpose: agreement is the gate, and
                #    AUTO_LINK_THRESHOLD (the AI review's own gate) is untouched.
                for verse, edges in same_verse.items():
                    current = self.project.load_verse_alignment(chapter, verse)
                    inventory = make_inventory(current)
                    links = []
                    for source, target, info in edges:
                        top_id = inventory.top_sig_to_id.get(source.signature)
                        bottom_id = inventory.bottom_sig_to_id.get(target.signature)
                        if top_id and bottom_id:
                            links.append({"top_id": top_id, "bottom_id": bottom_id,
                                          "confidence": int(info.get("confidence", 0)) / 100.0,
                                          "reason": info.get("reason", "")})
                    if not links:
                        continue
                    try:
                        proposal = compile_link_proposal(
                            current, {"links": links, "implicit_top_ids": [], "target_only_ids": []},
                            mode="gap_fill", auto_threshold=0.0, review_threshold=0.0,
                        )
                        if proposal.get("conflicts"):
                            raise AlignmentError("a compiled group would touch an existing reviewer group")
                        validate_preparation_proposal(current, proposal)
                        proposed = apply_proposal(current, proposal)
                        if proposed.to_dict() != current.to_dict():
                            self._save_alignment(chapter, verse, proposed, current.to_dict(), "ai_auto_align")
                    except AlignmentError as exc:
                        failures.append(f"{chapter}:{verse}: {exc}")
                        for source, target, info in edges:
                            suggestions.append(alignment_agreement.Suggestion(
                                kind="link", status="UNCERTAIN",
                                votes={alignment_window.SOURCE_FIRST: True, alignment_window.TARGET_FIRST: True},
                                source=source, target=target, confidence=int(info.get("confidence", 0)),
                                reason=alignment_agreement._join(info.get("reason"), f"not applied: {exc}"),
                            ))
                        continue
                    for group in proposal.get("groups", ()):
                        if group.get("origin") == "ai_compiled" and group.get("bottom_ids"):
                            applied[verse]["groups"].append({
                                "tops": sorted(inventory.top_ids[t].signature for t in group["top_ids"]),
                                "bottoms": sorted(inventory.bottom_ids[b].signature for b in group["bottom_ids"]),
                                "relation": group.get("relation", ""),
                            })
                # 3. Cross-verse groups, through the one link writer.
                for edges in cross_groups:
                    sources = sorted({e[0] for e in edges})
                    targets = sorted({e[1] for e in edges})
                    s_verse, t_verse = sources[0].verse, targets[0].verse
                    try:
                        s_refs = [{"chapter": chapter, "verse": s_verse,
                                   "topId": self._token_id(chapter, s_verse, "source", k.signature)} for k in sources]
                        t_refs = [{"chapter": chapter, "verse": t_verse,
                                   "bottomId": self._token_id(chapter, t_verse, "target", k.signature)} for k in targets]
                        if any(not r["topId"] for r in s_refs) or any(not r["bottomId"] for r in t_refs):
                            raise AlignmentError("a word is no longer in its verse")
                        linked = self.link_cross_verse(
                            None, None, "ai-auto", sources=s_refs, targets=t_refs, run_id=run_id,
                        )
                    except (AlignmentError, ProjectError) as exc:
                        failures.append(f"{chapter}:{s_verse}->{t_verse}: {exc}")
                        for source, target, info in edges:
                            suggestions.append(alignment_agreement.Suggestion(
                                kind="link", status="UNCERTAIN",
                                votes={alignment_window.SOURCE_FIRST: True, alignment_window.TARGET_FIRST: True},
                                source=source, target=target, confidence=int(info.get("confidence", 0)),
                                reason=alignment_agreement._join(info.get("reason"), f"not applied: {exc}"),
                            ))
                        continue
                    group_id = linked["group"]["groupId"]
                    applied[s_verse]["links"].append(group_id)
                    if group_id not in applied[t_verse]["links"]:
                        applied[t_verse]["links"].append(group_id)
                # 4. Agreed nulls.
                by_key = window.by_key()
                for key, info in agreement.agreed_nulls.items():
                    inventory = make_inventory(self.project.load_verse_alignment(chapter, key.verse))
                    lookup = inventory.top_sig_to_id if key.side == "source" else inventory.bottom_sig_to_id
                    pool = inventory.top_ids if key.side == "source" else inventory.bottom_ids
                    token_id = lookup.get(key.signature)
                    if not token_id or key not in by_key:
                        continue
                    try:
                        decision = self.project.null_decisions.set(
                            chapter, key.verse, key.side, pool[token_id], info["reason"],
                            note=info.get("note", ""), origin="ai-auto", run_id=run_id,
                        )
                    except AlignmentError as exc:
                        failures.append(f"{chapter}:{key.verse}: {exc}")
                        continue
                    applied[key.verse]["nulls"].append(decision["id"])
            if self.passage_semantic_runtime is not None:
                self.passage_semantic_runtime.synchronize_alignment_state()

        # Verdicts, one per verse.
        results = []
        for verse in ordered:
            context = self._alignment_context(chapter, verse)
            verse_tokens = {k for k in window.tokens() if k.verse == verse}
            issues = []
            remaining = self._remaining_gap_signatures(context)
            for key, notes_for in agreement.unplaced.items():
                if key.verse != verse:
                    continue
                if apply and key.signature not in remaining[key.side]:
                    continue
                token = window.by_key().get(key)
                issues.append({
                    "kind": "POSSIBLE_OMISSION" if key.side == "source" else "POSSIBLE_ADDITION",
                    "side": key.side, "signature": key.signature,
                    "word": token.word if token else "", "id": self._token_id(chapter, verse, key.side, key.signature),
                    "note": " · ".join(notes_for) or (
                        "Neither pass found this meaning anywhere in the passage." if key.side == "source"
                        else "Neither pass found a source word for this."
                    ),
                })
            verse_suggestions = [
                self._suggestion_view(chapter, s, window) for s in suggestions
                if any(t.verse == verse for t in s.tokens())
            ]
            if not apply:
                settled = (
                    {k for k in verse_tokens if k in window.homed()}
                    | {k for pair in agreement.agreed_edges for k in pair}
                    | set(agreement.agreed_nulls)
                )
                clean = not issues and not verse_suggestions and verse_tokens <= settled
            else:
                clean = not issues and not verse_suggestions and bool(context.get("accounted"))
            verdict = "ALIGNED_CLEAN" if clean else "NEEDS_REVIEW"
            alignment = self.project.load_verse_alignment(chapter, verse)
            payload = {
                "chapter": chapter, "verse": verse, "verdict": verdict, "runId": run_id,
                "jobId": job_id, "applyRequested": apply,
                "calibrationVersion": alignment_agreement.AGREEMENT_CALIBRATION_VERSION,
                "corpus": agreement.corpus, "issues": issues, "suggestions": verse_suggestions,
                "applied": applied[verse] if apply else {"groups": [], "links": [], "nulls": []},
                "reverted": reverted.get(verse, {}),
                "alignmentFingerprint": alignment_fingerprint(alignment),
                "targetFingerprint": target_token_fingerprint(alignment, self.project.target_verse_text(chapter, verse)),
                "window": ordered, "usage": usage, "createdAt": self.project.workbench._now(),
            }
            self._write_auto_verdict(chapter, verse, payload)
            results.append({
                "verse": verse, "verdict": verdict, "applied": payload["applied"], "issues": issues,
                "suggestions": verse_suggestions, "context": self._alignment_context(chapter, verse),
            })
        return {
            **base, "corpus": agreement.corpus, "results": results, "usage": usage,
            "notes": notes, "failures": failures,
        }

    @staticmethod
    def _remaining_gap_signatures(context: dict[str, Any]) -> dict[str, set[str]]:
        """The signatures of a context's live gaps, per side -- net of tC
        groups, links and nulls, exactly as `gap_ids` counts them."""
        top_sig = {t["id"]: f"{t['word']}␟{t['occurrence']}␟{t['occurrences']}" for t in context["topTokens"]}
        bottom_sig = {t["id"]: f"{t['word']}␟{t['occurrence']}␟{t['occurrences']}" for t in context["bottomTokens"]}
        matched_top = {i for g in context["groups"] if g["bottomIds"] for i in g["topIds"]}
        grouped_bottom = {i for g in context["groups"] for i in g["bottomIds"]}
        nulls = context.get("nullDecisions") or {"source": [], "target": []}
        realized = set(context.get("crossVerseRealizedIds") or ()) | {
            n["id"] for n in nulls["source"] if n.get("state") == "active" and n.get("id")}
        accounted = set(context.get("crossVerseAccountedIds") or ()) | {
            n["id"] for n in nulls["target"] if n.get("state") == "active" and n.get("id")}
        return {
            "source": {s for i, s in top_sig.items() if i not in matched_top and i not in realized},
            "target": {s for i, s in bottom_sig.items() if i not in grouped_bottom and i not in accounted},
        }

    def _suggestion_view(self, chapter: str, suggestion, window) -> dict[str, Any]:
        by_key = window.by_key()

        def end(key):
            if key is None:
                return None
            token = by_key.get(key)
            return {
                "side": key.side, "chapter": chapter, "verse": key.verse, "signature": key.signature,
                "word": token.word if token else "", "id": self._token_id(chapter, key.verse, key.side, key.signature),
            }

        return {
            "kind": suggestion.kind, "status": suggestion.status, "votes": suggestion.votes,
            "source": end(suggestion.source), "target": end(suggestion.target), "token": end(suggestion.token),
            "reason": suggestion.reason, "note": suggestion.note, "confidence": suggestion.confidence,
        }

    def auto_align_revert(self, chapter: str, verse: str) -> dict[str, Any]:
        """alignment.autoAlign.revert: undo what the last automatic pass wrote
        in one verse, where it is still exactly as written."""
        self._require_project()
        chapter, verse = str(chapter), str(verse)
        verdict = self._auto_verdict(chapter, verse)
        if verdict is None:
            raise AlignmentError(f"No automatic alignment has run on {chapter}:{verse}.")
        ledger = {**(verdict.get("applied") or {}), "runId": verdict.get("runId", "")}
        with self._checker_lock:
            outcome = self._revert_auto_writes(chapter, verse, ledger, run_id="")
        if self.passage_semantic_runtime is not None:
            self.passage_semantic_runtime.synchronize_alignment_state()
        verdict.update({"verdict": "REVERTED", "applied": {"groups": [], "links": [], "nulls": []},
                        "revertedAt": self.project.workbench._now(), "reverted": outcome})
        self._write_auto_verdict(chapter, verse, verdict)
        return {"chapter": chapter, "verse": verse, "skipped": outcome["skipped"],
                "context": self._alignment_context(chapter, verse)}

    def auto_align_verdict(self, chapter: str, verse: str) -> dict[str, Any]:
        self._require_project()
        return {"chapter": str(chapter), "verse": str(verse), "verdict": self._auto_verdict(str(chapter), str(verse))}

    # ---- chapter / book job (#221) ------------------------------------------------

    def _auto_job_windows(self, scope: str, chapters: Any) -> dict[str, list[list[str]]]:
        self._require_project()
        if scope not in {"chapter", "book"}:
            raise ProjectError("Automatic alignment runs on a chapter or a book.")
        if scope == "book":
            wanted = [str(c) for c in self.project.chapters()]
        else:
            if not isinstance(chapters, list) or not chapters:
                raise ProjectError("A chapter job needs the chapter to align.")
            wanted = [str(c) for c in chapters]
        known = set(str(c) for c in self.project.chapters())
        windows: dict[str, list[list[str]]] = {}
        for chapter in wanted:
            if chapter not in known:
                raise ProjectError(f"Chapter {chapter} does not exist in this project.")
            verses = [str(v) for v in self.project.verses(chapter) if str(v) != "front"]
            windows[chapter] = auto_align_jobs.windows_for(verses, window=3, overlap=1)
        return windows

    def auto_align_estimate(self, scope: str = "chapter", chapters: Any = None) -> dict[str, Any]:
        """alignment.autoAlign.estimate: what a job would cost, worked out
        offline from the real window payloads. About three characters per token
        for the mixed Greek/Hebrew/Tamil text is a rough rule, so this is an
        estimate to show before the click, not a quote."""
        windows = self._auto_job_windows(scope, chapters)
        count = sum(len(w) for w in windows.values())
        input_tokens = 0
        for chapter, chapter_windows in windows.items():
            for window in chapter_windows:
                built, guidance = self._build_auto_window(chapter, window, {"groups": set(), "links": set(), "nulls": set()})
                chars = len(json.dumps(built.payload, ensure_ascii=False)) + len(alignment_window.INSTRUCTIONS[alignment_window.SOURCE_FIRST]) + len(guidance)
                input_tokens += 2 * (chars // 3 + 1)
        output_tokens = count * 2 * 1500
        model = self.settings.model
        try:
            cost = estimate_cost(model, input_tokens, output_tokens)
        except Exception:
            cost = 0.0
        return {
            "scope": scope, "chapters": list(windows), "windows": count, "calls": count * 2,
            "estimatedInputTokens": input_tokens, "estimatedOutputTokens": output_tokens,
            "estimatedCostUSD": round(cost, 4), "model": model,
            "hasApiKey": bool(self.settings.get_api_key()),
        }

    def start_auto_align_job(self, scope: str = "chapter", chapters: Any = None, apply: bool = True) -> dict[str, Any]:
        windows = self._auto_job_windows(scope, chapters)
        try:
            self._ai_client()
        except AIError as exc:
            return {"state": "failed", "unavailable": {"reason": "no-api-key", "message": str(exc)}}
        spec = auto_align_jobs.AutoAlignJobSpec(
            scope=scope, chapters=tuple(windows), windows=windows, apply=bool(apply),
        )
        return self._start_auto_align(spec)

    def _start_auto_align(self, spec) -> dict[str, Any]:
        def run_window(chapter: str, verses: list[str], job_id: str) -> dict[str, Any]:
            return self.auto_align_window(chapter, verses, apply=spec.apply, job_id=job_id)
        try:
            return self._auto_align_jobs.start(spec, run_window=run_window)
        except auto_align_jobs.AutoAlignJobConflict as exc:
            raise ProjectError(str(exc)) from exc

    def auto_align_job_status(self, job_id: str = "") -> dict[str, Any]:
        try:
            return self._auto_align_jobs.status(job_id)
        except auto_align_jobs.AutoAlignJobNotFound as exc:
            raise ProjectError(str(exc)) from exc

    def cancel_auto_align_job(self, job_id: str = "") -> dict[str, Any]:
        try:
            return self._auto_align_jobs.cancel(job_id)
        except auto_align_jobs.AutoAlignJobNotFound as exc:
            raise ProjectError(str(exc)) from exc

    def retry_auto_align_job(self, job_id: str) -> dict[str, Any]:
        try:
            spec = self._auto_align_jobs.spec_for_retry(job_id)
        except (auto_align_jobs.AutoAlignJobNotFound, auto_align_jobs.AutoAlignJobConflict) as exc:
            raise ProjectError(str(exc)) from exc
        return self._start_auto_align(spec)

    def get_lexicon_entry(self, strong: str, morph: str) -> dict[str, Any]:
        """Look up lexicon glosses + decoded morphology for one source token.

        `strong` and `morph` come straight off an AlignmentToken; both may be
        compound (colon-joined) when the surface word is a lexeme plus a
        Hebrew proclitic prefix (e.g. strong "b:H7225", morph "He,R:Ncfsa") —
        this decodes and looks up each morpheme segment independently.
        """
        language_id, morph_segments = decode_morph(morph)
        strong_parts = [part for part in str(strong or "").split(":") if part]

        segment_count = max(len(strong_parts), len(morph_segments), 1)
        segments: list[dict[str, Any]] = []
        for index in range(segment_count):
            strong_part = strong_parts[index] if index < len(strong_parts) else ""
            morph_segment = morph_segments[index] if index < len(morph_segments) else None
            entry = (
                lexicon_entry_for_strong(strong_part, language_id)
                if strong_part and language_id else None
            )
            prefix_label = (
                HEBREW_PREFIX_LABELS.get(strong_part)
                if not entry and language_id == "hbo" else None
            )
            segments.append({
                "strong": strong_part or None,
                "morphLabel": morph_segment.label if morph_segment else None,
                "partOfSpeech": morph_segment.part_of_speech if morph_segment else None,
                "lemma": entry.get("lemma") if entry else None,
                "translit": entry.get("translit") if entry else None,
                "pron": entry.get("pron") if entry else None,
                "meaning": (entry.get("meaning") if entry else None) or prefix_label,
                "usage": entry.get("usage") if entry else None,
                "source": entry.get("derivation") if entry else None,
            })
        return {"languageId": language_id, "segments": segments}

    @staticmethod
    def _tokens_for_ids(
        inventory, top_ids: list[str], bottom_ids: list[str],
    ) -> tuple[list[TokenRef], list[TokenRef]]:
        unknown_top = [token_id for token_id in top_ids if token_id not in inventory.top_ids]
        unknown_bottom = [token_id for token_id in bottom_ids if token_id not in inventory.bottom_ids]
        if unknown_top or unknown_bottom:
            raise AlignmentError(
                "The alignment changed or contains unknown token IDs. Reload before saving."
            )
        return (
            [inventory.top_ids[token_id] for token_id in dict.fromkeys(top_ids)],
            [inventory.bottom_ids[token_id] for token_id in dict.fromkeys(bottom_ids)],
        )

    @staticmethod
    def _validate_alignment_identity(current: VerseAlignment, proposed: VerseAlignment) -> None:
        if Counter(t.signature for t in current.all_top()) != Counter(t.signature for t in proposed.all_top()):
            raise AlignmentError("A save may regroup source tokens but may not add, remove, or duplicate them.")
        if Counter(t.signature for t in current.all_bottom()) != Counter(t.signature for t in proposed.all_bottom()):
            raise AlignmentError("A save may regroup target tokens but may not add, remove, or duplicate them.")

    def _finish_alignment_mutation(self, chapter: str, verse: str) -> dict[str, Any]:
        """Shared tail for every alignment-mutating path (manual realign/unalign/save,
        AI auto-align, undo/restore): clear pending/invalid caches, then automatically
        mark the verse complete the moment every word is grouped — no separate human
        "Mark alignment complete" click. `mark_word_alignment_pending` runs first even
        when the verse turns out to still be complete, so an edit that keeps a
        previously human-completed verse fully aligned re-earns "completed" through
        the same check rather than leaving a stale marker in place.
        """
        self.project.mark_word_alignment_pending(chapter, verse)
        self._corpus_stats_by_book.pop(str(self.project.path), None)
        self._consistency_findings_by_book.pop(str(self.project.path), None)
        context = self._alignment_context(chapter, verse)
        if context["canComplete"] and context["completionState"] != "completed":
            self.project.mark_word_alignment_completed(
                chapter, verse, username=self.settings.reviewer_name or "Bridge Reviewer",
            )
            context = self._alignment_context(chapter, verse)
        # V11-000a: the alignment content is already written by this point
        # (save_verse_alignment / restore_verse_alignment_history ran before
        # this shared tail), so this picks up the fresh book-wide digest and
        # stales any Stage 6B/7/8 record that depends on it -- in this same
        # session, not only after a restart.
        if self.passage_semantic_runtime is not None:
            self.passage_semantic_runtime.synchronize_alignment_state()
        return context

    def _save_alignment(
        self,
        chapter: str,
        verse: str,
        proposed: VerseAlignment,
        expected_original: dict[str, Any],
        operation: str,
    ) -> dict[str, Any]:
        current = self.project.load_verse_alignment(chapter, verse)
        self._validate_alignment_identity(current, proposed)
        self._refuse_if_homed_elsewhere(str(chapter), str(verse), current, proposed)
        self.project.save_verse_alignment(
            chapter, verse, proposed,
            expected_original=expected_original,
            operation=operation,
        )
        return self._finish_alignment_mutation(chapter, verse)

    def realign_words(
        self,
        chapter: str,
        verse: str,
        top_ids: list[str],
        bottom_ids: list[str],
        expected_original: dict[str, Any],
    ) -> dict[str, Any]:
        current = self.project.load_verse_alignment(chapter, verse)
        inventory = make_inventory(current)
        selected_top, selected_bottom = self._tokens_for_ids(inventory, top_ids, bottom_ids)
        proposed = realign(current, selected_top, selected_bottom)
        return self._save_alignment(chapter, verse, proposed, expected_original, "realign")

    def unalign_words(
        self,
        chapter: str,
        verse: str,
        bottom_ids: list[str],
        expected_original: dict[str, Any],
    ) -> dict[str, Any]:
        current = self.project.load_verse_alignment(chapter, verse)
        inventory = make_inventory(current)
        _, selected_bottom = self._tokens_for_ids(inventory, [], bottom_ids)
        proposed = unalign_bottom(current, selected_bottom)
        return self._save_alignment(chapter, verse, proposed, expected_original, "unalign")

    def save_alignment(
        self,
        chapter: str,
        verse: str,
        value: dict[str, Any],
        expected_original: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise AlignmentError("Alignment must be an object.")
        return self._save_alignment(
            chapter, verse, VerseAlignment.from_dict(value), expected_original, "save",
        )

    def complete_alignment(self, chapter: str, verse: str) -> dict[str, Any]:
        context = self._alignment_context(chapter, verse)
        if not context["sourceAvailable"]:
            raise AlignmentError(context["sourceMessage"])
        if not context["canComplete"]:
            detail = "; ".join(context["issues"]) or "unaligned source or target words remain"
            raise AlignmentError(f"Alignment cannot be completed: {detail}.")
        self.project.mark_word_alignment_completed(
            chapter, verse, username=self.settings.reviewer_name or "Bridge Reviewer",
        )
        # V11-000a review fix (F6): unlike realign/unalign/save/undo, this RPC
        # does not go through _finish_alignment_mutation, so it must refresh
        # the alignment invalidation memo itself -- otherwise a location run
        # published after this call is over-invalidated the next time the
        # project is reopened (docs/archive/V11-000a_REVIEW_FIX_PROMPT.md F6).
        if self.passage_semantic_runtime is not None:
            self.passage_semantic_runtime.synchronize_alignment_state()
        self._corpus_stats_by_book.pop(str(self.project.path), None)
        self._consistency_findings_by_book.pop(str(self.project.path), None)
        return self._alignment_context(chapter, verse)

    def undo_alignment(
        self,
        chapter: str,
        verse: str,
        expected_original: dict[str, Any],
        history_id: str = "",
    ) -> dict[str, Any]:
        self.project.restore_verse_alignment_history(
            chapter, verse, history_id=history_id, expected_original=expected_original,
        )
        return self._finish_alignment_mutation(chapter, verse)

    def _ai_client(self) -> OpenAIResponsesClient:
        api_key = self.settings.get_api_key()
        if not api_key:
            raise AIError(
                "No OpenAI-compatible API key is configured. Add one in Settings before "
                "requesting an AI alignment proposal."
            )
        kwargs: dict[str, Any] = {}
        if self._ai_transport is not None:
            kwargs["transport"] = self._ai_transport
        return OpenAIResponsesClient(
            api_key, model=self.settings.model, base_url=self.settings.api_base_url, **kwargs
        )

    def propose_ai_alignment(self, chapter: str, verse: str, mode: str = "gap_fill") -> dict[str, Any]:
        """Ask AI for individual token links, then compile them deterministically into
        legal tC groups via alignment_reliability.compile_link_proposal. Read-only: the
        proposal is returned for human review and is not written to project files —
        alignment.aiApplyProposal is a separate, explicit step. gap_fill (the default)
        protects every existing non-empty group; audit is a read-only whole-verse
        comparison not meant to be applied directly (see compile_link_proposal's own
        docstring — an audit proposal is rejected by alignment.aiApplyProposal's
        validate_preparation_proposal safety check if it would detach an established
        group)."""
        self._require_project()
        client = self._ai_client()
        alignment = self.project.load_verse_alignment(chapter, verse)
        proposal = client.propose_alignment(self.project, chapter, verse, alignment, mode=mode)
        self.settings.record_ai_usage(client.last_usage.total_tokens, client.last_cost_usd)
        return {
            # The proposal's internal field names (top_ids/bottom_ids/requires_human_review/...)
            # match alignment_reliability.compile_link_proposal's own schema verbatim, unlike this
            # file's usual camelCase protocol convention — it must round-trip byte-for-byte back
            # into alignment.aiApplyProposal's apply_proposal() call, which reads those exact
            # snake_case keys. Re-keying it here would risk a lossy/asymmetric conversion for no
            # benefit, since the frontend only needs to read top_ids/bottom_ids generically to
            # resolve token labels, the same way it already does for existing alignment groups.
            "proposal": proposal,
            "usage": {
                "totalTokens": client.last_usage.total_tokens,
                "estimatedCostUSD": round(client.last_cost_usd, 6),
            },
        }

    def apply_ai_alignment_proposal(
        self, chapter: str, verse: str, proposal: dict[str, Any], expected_original: dict[str, Any],
    ) -> dict[str, Any]:
        """Human-triggered, explicit step that actually writes a previously returned AI
        proposal to the project — never called automatically by propose_ai_alignment.
        Goes through the exact same identity-preserving save pipeline as a manual
        realign/save (_save_alignment), so an AI proposal can add no protection an
        ordinary manual edit wouldn't also get."""
        self._require_project()
        current = self.project.load_verse_alignment(chapter, verse)
        validate_preparation_proposal(current, proposal)
        proposed = apply_proposal(current, proposal)
        return self._save_alignment(chapter, verse, proposed, expected_original, "ai_propose_apply")

    @staticmethod
    def _safe_ai_selection_reason(review: Any) -> str:
        """Return an empty string only when a model proposal is safe to auto-apply.

        Structured-output validation proves shape and token identity.  This second,
        deterministic policy gate proves that the conclusion is decisive, grounded,
        and complete enough to write without asking.  The native persistence layer
        remains the final authority and independently blocks overwriting
        imported/human choices -- which is what makes running this unconditionally
        safe, including when the reviewer has manual override enabled and may edit
        the result afterwards.
        """
        if review.verdict not in {"pass", "problem", "not_applicable"}:
            return "AI verdict requires human review"
        if float(review.confidence or 0.0) < 0.82:
            return "AI confidence is below the 82% automatic-selection threshold"
        if not review.evidence_used:
            return "No bundled evidence was cited"
        selection_state = str(getattr(review, "selection_state", "") or "")
        if selection_state and not native_tc_apply_allowed(review):
            return "Stage 3 mapping is not safe for a verse-local automatic selection"
        if review.verdict == "not_applicable" and not review.nothing_to_select:
            return "Not-applicable verdict must explicitly select nothing"
        if review.nothing_to_select:
            if review.verdict == "problem":
                return "An unresolved translation problem cannot be completed as nothing-to-select"
            return "" if not review.proposed_selections else "Proposal contradicts nothing-to-select"
        if not review.proposed_selections:
            return "No exact target selection was proposed"
        return ""

    def _apply_safe_ai_selections(
        self,
        project: TranslationCoreProject,
        chapter: str,
        verse: str,
        reviews: list[Any],
        *,
        model: str,
        qa_issues: list[Any] | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        applied: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        qa_issues = list(qa_issues or [])
        for review in reviews:
            reason = self._safe_ai_selection_reason(review)
            identity = {
                "tool": review.tool, "groupId": review.group_id, "checkId": review.check_id,
            }
            contradictory = [
                issue for issue in qa_issues
                if (
                    str(getattr(issue, "check_id", "") or "") == str(review.check_id)
                    or (
                        not str(getattr(issue, "check_id", "") or "")
                        and str(getattr(issue, "group_id", "") or "") == str(review.group_id)
                    )
                )
                and str(getattr(issue, "severity", "") or "") in {"critical", "high", "medium"}
                and float(getattr(issue, "confidence", 0.0) or 0.0) >= 0.75
            ]
            if contradictory:
                reason = "Contradictory QA evidence requires human review"
            if reason:
                skipped.append({**identity, "reason": reason})
                continue
            try:
                with self._checker_lock:
                    validation = project.validate_check_selection(
                        chapter, verse, review.tool, review.group_id, review.check_id,
                        review.proposed_selections, review.nothing_to_select,
                    )
                    if not validation.get("valid"):
                        skipped.append({
                            **identity,
                            "reason": " ".join(validation.get("errors") or ["Selection validation failed"]),
                        })
                        continue
                    mutation = project.save_check_selection(
                        chapter, verse, review.tool, review.group_id, review.check_id,
                        validation.get("selections") or [], review.nothing_to_select,
                        "bridge_ai", str(validation.get("stateFingerprint") or ""),
                        username="Bridge AI",
                        audit_metadata={
                            "interface": "automatic", "model": model,
                            "confidence": review.confidence, "verdict": review.verdict,
                            "evidenceGrounded": True,
                        },
                    )
                applied.append({**identity, "review": mutation.get("review")})
            except ProjectError as exc:
                # Protected imported/human choices and concurrent changes are a safe
                # per-check skip, not a reason to lose every other verse result.
                skipped.append({**identity, "reason": str(exc)})
        if applied:
            with self._checker_lock:
                project.rebase_ai_review_fingerprint(chapter, verse)
        # Written last so the reason a check stayed pending outlives this job
        # result and can be shown whenever the reviewer reopens the verse. Under
        # the same lock as the rebase above: both rewrite the one review record
        # that a concurrent list_checks_for_verse reads.
        with self._checker_lock:
            project.record_ai_selection_outcomes(chapter, verse, applied, skipped)
        return applied, skipped

    def _run_ai_review_for_project(
        self,
        project: TranslationCoreProject,
        chapter: str,
        verse: str,
        mode: str,
        progress_callback: Callable[[int, str], None],
        cancel_event: threading.Event,
    ) -> dict[str, Any]:
        if cancel_event.is_set():
            project.mark_issue_resolutions_recheck(
                chapter, verse, "cancelled", reason="AI review was cancelled before this verse started.",
            )
            raise AIError("AI review cancelled before the verse started.")
        project.mark_issue_resolutions_recheck(
            chapter, verse, "running", reason="Automatic AI recheck started.",
        )
        try:
            with self._checker_lock:
                self._ensure_resource_indexes(project)
            client = self._ai_client()
            alignment = project.load_verse_alignment(chapter, verse)
            proposal, review_alignment, reviews, issues, summary, meta = client.prepare_verse_review(
                project, chapter, verse, alignment, progress_callback=progress_callback,
            )
            total_tokens = int(meta.get("total_tokens_for_prepare", 0) or 0)
            cost = float(meta.get("estimated_cost_usd", 0.0) or 0.0)
            self.settings.record_ai_usage(total_tokens, cost)
            if cancel_event.is_set():
                project.mark_ai_review_incomplete(chapter, verse, "cancelled")
                project.mark_issue_resolutions_recheck(
                    chapter, verse, "cancelled",
                    reason="AI review was cancelled; no lifecycle conclusion was accepted.",
                )
                raise AIError("AI review cancelled; the completed model result was not automatically applied.")
            if proposal is not None and review_alignment.to_dict() != alignment.to_dict():
                progress_callback(90, "Saving AI-filled alignment gaps")
                try:
                    with self._checker_lock:
                        self._save_alignment(
                            chapter, verse, review_alignment, alignment.to_dict(), "ai_review_auto_align",
                        )
                        # The verse alignment is part of review_input_fingerprint,
                        # and prepare_verse_review already stamped the stored review
                        # with the fingerprint from BEFORE this write. Without the
                        # rebase the review invalidates itself: the UI showed
                        # "Verse changed - the previous AI review is stale" the
                        # instant the review finished. Only _apply_safe_ai_selections
                        # rebased, and only when it applied something, so Manual mode
                        # (which applies nothing) always went stale.
                        project.rebase_ai_review_fingerprint(chapter, verse)
                except Exception:
                    # A concurrent edit or a validation edge case here must not sink the
                    # tN/tW review this verse otherwise completed; the verse simply stays
                    # unaligned and is picked up by the alignment-popup/verse-list flag.
                    pass
            # Safe, evidence-grounded selections are applied for every review.
            # `mode` (allow-manual-override) decides whether the reviewer may
            # then hand-edit the result, not whether the AI is allowed to fill
            # it in -- gating the write on the mode meant turning override on
            # silently stopped tN/tW words being selected at all.
            progress_callback(94, "Applying safe evidence-grounded selections")
            applied, skipped = self._apply_safe_ai_selections(
                project, chapter, verse, reviews, model=str(meta.get("model") or client.model),
                qa_issues=issues,
            )
            review_dicts = [item.to_dict() for item in reviews]
            lifecycle = project.reconcile_issue_resolutions_after_ai_review(
                chapter, verse, review_dicts,
                model=str(meta.get("model") or client.model), summary=summary,
            )
            progress_callback(100, "Verse AI review complete")
            return {
                "summary": summary,
                "checkReviews": review_dicts,
                "qaIssues": [item.to_dict() for item in issues],
                "alignmentProposal": proposal,
                "alignmentWasAIProposed": bool(proposal is not None),
                "appliedSelections": applied,
                "skippedSelections": skipped,
                "resolutionLifecycle": lifecycle,
                "usage": {"totalTokens": total_tokens, "estimatedCostUSD": round(cost, 6)},
            }
        except Exception as exc:
            if not cancel_event.is_set():
                project.mark_issue_resolutions_recheck(
                    chapter, verse, "failed",
                    reason="Automatic AI recheck failed.", error=str(exc),
                )
            raise

    def _start_ai_review_spec(
        self, project: TranslationCoreProject, spec: AIReviewJobSpec,
    ) -> dict[str, Any]:
        # Fail immediately with the familiar settings error instead of starting a
        # background job whose first verse can only fail for a missing API key.
        self._ai_client()
        return self._ai_review_jobs.start(
            spec,
            run_verse=lambda ch, vs, reviewer_mode, progress, cancel: self._run_ai_review_for_project(
                project, ch, vs, reviewer_mode, progress, cancel,
            ),
        )

    def start_ai_review_job(
        self, scope: str, chapter: str = "", verse: str = "", mode: str = "",
    ) -> dict[str, Any]:
        self._require_project()
        project = self.project
        resolved_mode = str(mode or self.settings.reviewer_mode or "basic").lower()
        available = project.chapters()
        if scope == "book":
            chapters = available
        else:
            if not chapter or str(chapter) not in available:
                raise ProjectError(f"Chapter {chapter or '?'} does not exist in this project.")
            chapters = [str(chapter)]
        requested = {
            item: [value for value in project.verses(item) if value != "front"]
            for item in chapters
        }
        if scope == "verse":
            if not verse or str(verse) not in requested[str(chapter)]:
                raise ProjectError(f"Verse {chapter}:{verse or '?'} does not exist in this project.")
            chapter_verses = {str(chapter): [str(verse)]}
            skipped_current = 0
        else:
            # Chapter/book buttons are resumable by default. Already-current cached
            # reviews are real completed work and should not consume API calls again.
            chapter_verses = {
                ch: [vs for vs in values if project.ai_review_cache_status(ch, vs) != "current"]
                for ch, values in requested.items()
            }
            skipped_current = sum(len(values) for values in requested.values()) - sum(
                len(values) for values in chapter_verses.values()
            )
        spec = AIReviewJobSpec(
            scope=scope, mode=resolved_mode, project_path=str(project.path),
            chapters=tuple(chapters), chapter_verses=chapter_verses,
            skipped_current=skipped_current,
        )
        return self._start_ai_review_spec(project, spec)

    def ai_review_job_status(self, job_id: str = "") -> dict[str, Any]:
        return self._ai_review_jobs.status(job_id)

    def cancel_ai_review_job(self, job_id: str = "") -> dict[str, Any]:
        return self._ai_review_jobs.cancel(job_id)

    def retry_ai_review_job(self, job_id: str) -> dict[str, Any]:
        self._require_project()
        spec = self._ai_review_jobs.spec_for_retry(job_id)
        if str(self.project.path) != spec.project_path:
            raise ProjectError("Reopen the original project before retrying this AI review job.")
        return self._start_ai_review_spec(self.project, spec)

    @staticmethod
    def _compact_ai_review(item: dict[str, Any]) -> dict[str, Any]:
        compact = copy.deepcopy(item)
        compact["evidence_used"] = [
            {
                key: copy.deepcopy(evidence.get(key))
                for key in ("kind", "title", "identifier", "version", "provider", "authoritative")
                if evidence.get(key) not in (None, "")
            }
            for evidence in list(item.get("evidence_used") or []) if isinstance(evidence, dict)
        ]
        return compact

    def list_ai_reviews_for_chapter(self, chapter: str) -> dict[str, Any]:
        """Restore compact current reviews for chapter-wide inline highlighting."""
        self._require_project()
        if str(chapter) not in self.project.chapters():
            raise ProjectError(f"Chapter {chapter} does not exist in this project.")
        reviews_by_verse: dict[str, list[dict[str, Any]]] = {}
        states: dict[str, str] = {}
        for verse in self.project.verses(chapter):
            if verse == "front":
                continue
            state = self.project.ai_review_cache_status(chapter, verse)
            states[str(verse)] = state
            if state != "current":
                continue
            saved = self.project.load_ai_review_result(chapter, verse) or {}
            reviews_by_verse[str(verse)] = [
                self._compact_ai_review(item)
                for item in list(saved.get("checkReviews") or []) if isinstance(item, dict)
            ]
        return {
            "chapter": str(chapter),
            "reviewsByVerse": reviews_by_verse,
            "states": states,
            "current": sum(1 for state in states.values() if state == "current"),
            "stale": sum(1 for state in states.values() if state == "stale"),
            "missing": sum(1 for state in states.values() if state == "missing"),
        }

    # -- passage-semantic runtime foundation -----------------------------

    def passage_semantic_status(self) -> dict[str, Any]:
        if self.passage_semantic_runtime is None:
            return dict(self._passage_semantic_status)
        self._passage_semantic_status = {
            "state": "READY", **self.passage_semantic_runtime.status(),
        }
        return dict(self._passage_semantic_status)

    def _require_passage_semantic_runtime(self) -> PassageSemanticRuntime:
        self._require_project()
        if self.passage_semantic_runtime is None:
            error = str(self._passage_semantic_status.get("error") or "")
            raise ProjectError(
                "Passage-semantic companion storage is unavailable."
                + (f" Recovery detail: {error}" if error else "")
            )
        return self.passage_semantic_runtime

    def passage_semantic_project_metadata(self) -> dict[str, Any]:
        return self._require_passage_semantic_runtime().project_metadata()

    def passage_semantic_current_passage(
        self, chapter: str, verse: str, end_chapter: str = "", end_verse: str = "",
    ) -> dict[str, Any]:
        return self._require_passage_semantic_runtime().get_current_passage(
            chapter, verse, end_chapter, end_verse,
        )

    def passage_semantic_stale_summary(self) -> dict[str, Any]:
        return self._require_passage_semantic_runtime().stale_summary()

    def passage_semantic_migration_report(self) -> dict[str, Any]:
        return self._require_passage_semantic_runtime().migration_report()

    def passage_semantic_rebuild_passage(
        self, chapter: str, verse: str, end_chapter: str = "", end_verse: str = "",
        tokenizer_profile: str = "bridge-unicode-word-v1",
    ) -> dict[str, Any]:
        return self._require_passage_semantic_runtime().rebuild_current_passage(
            chapter, verse, end_chapter, end_verse, tokenizer_profile,
        )

    def source_semantic_build_range(
        self, chapter: str, verse: str, end_chapter: str = "", end_verse: str = "",
    ) -> dict[str, Any]:
        return self._require_passage_semantic_runtime().build_source_semantic_range(
            chapter, verse, end_chapter, end_verse,
        )

    def source_semantic_get_range(self, inventory_id: str) -> dict[str, Any]:
        return self._require_passage_semantic_runtime().source_semantic_range(inventory_id)

    def source_semantic_get_unit(self, unit_id: str) -> dict[str, Any]:
        return self._require_passage_semantic_runtime().source_semantic_unit(unit_id)

    def source_semantic_get_coverage_accounts(self, inventory_id: str) -> list[dict[str, Any]]:
        return self._require_passage_semantic_runtime().source_semantic_coverage_accounts(inventory_id)

    def source_semantic_get_diagnostics(self, inventory_id: str) -> dict[str, Any]:
        return self._require_passage_semantic_runtime().source_semantic_diagnostics(inventory_id)

    def target_semantic_build_range(
        self, chapter: str, verse: str, end_chapter: str = "", end_verse: str = "",
    ) -> dict[str, Any]:
        return self._require_passage_semantic_runtime().build_target_semantic_range(
            chapter, verse, end_chapter, end_verse,
        )

    def target_semantic_get_range(self, inventory_id: str) -> dict[str, Any]:
        return self._require_passage_semantic_runtime().target_semantic_range(inventory_id)

    def target_semantic_get_unit(self, unit_id: str) -> dict[str, Any]:
        return self._require_passage_semantic_runtime().target_semantic_unit(unit_id)

    def target_semantic_get_diagnostics(self, inventory_id: str) -> dict[str, Any]:
        return self._require_passage_semantic_runtime().target_semantic_diagnostics(inventory_id)

    def target_semantic_get_search_spans(self, inventory_id: str) -> list[dict[str, Any]]:
        return self._require_passage_semantic_runtime().target_semantic_search_spans(inventory_id)

    def target_semantic_get_capabilities(self, inventory_id: str = "") -> dict[str, Any]:
        return self._require_passage_semantic_runtime().target_semantic_capabilities(inventory_id)

    def semantic_location_run_range(
        self, chapter: str, verse: str, end_chapter: str = "", end_verse: str = "",
        max_candidate_evaluations: int | None = None,
    ) -> dict[str, Any]:
        return self._require_passage_semantic_runtime().run_semantic_location_range(
            chapter, verse, end_chapter, end_verse, max_candidate_evaluations,
        )

    def semantic_location_status(self, run_id: str) -> dict[str, Any]:
        return self._require_passage_semantic_runtime().semantic_location_status(run_id)

    def semantic_location_get_range(self, run_id: str) -> dict[str, Any]:
        return self._require_passage_semantic_runtime().semantic_location_range(run_id)

    def semantic_location_get_relationship(self, relationship_id: str) -> dict[str, Any]:
        return self._require_passage_semantic_runtime().semantic_location_relationship(relationship_id)

    def semantic_location_get_candidates(
        self, run_id: str, source_owner_unit_id: str = "",
    ) -> list[dict[str, Any]]:
        return self._require_passage_semantic_runtime().semantic_location_candidates(
            run_id, source_owner_unit_id,
        )

    def semantic_location_get_diagnostics(self, run_id: str) -> dict[str, Any]:
        return self._require_passage_semantic_runtime().semantic_location_diagnostics(run_id)

    def meaning_analysis_run_range(
        self, chapter: str, verse: str, end_chapter: str = "", end_verse: str = "",
        location_run_id: str = "",
    ) -> dict[str, Any]:
        return self._require_passage_semantic_runtime().run_meaning_analysis_range(
            chapter, verse, end_chapter, end_verse, location_run_id,
        )

    def meaning_analysis_status(self, run_id: str) -> dict[str, Any]:
        return self._require_passage_semantic_runtime().meaning_analysis_status(run_id)

    def meaning_analysis_get_range(self, run_id: str) -> dict[str, Any]:
        return self._require_passage_semantic_runtime().meaning_analysis_range(run_id)

    def meaning_analysis_get_assessment(self, assessment_id: str) -> dict[str, Any]:
        return self._require_passage_semantic_runtime().meaning_assessment(assessment_id)

    def meaning_analysis_get_components(self, assessment_id: str) -> list[dict[str, Any]]:
        return self._require_passage_semantic_runtime().meaning_components(assessment_id)

    def meaning_analysis_get_diagnostics(self, run_id: str) -> dict[str, Any]:
        return self._require_passage_semantic_runtime().meaning_analysis_diagnostics(run_id)

    def qa_audit_run_range(
        self, chapter: str, verse: str, end_chapter: str = "", end_verse: str = "",
        meaning_run_id: str = "",
    ) -> dict[str, Any]:
        return self._require_passage_semantic_runtime().run_qa_audit_range(
            chapter, verse, end_chapter, end_verse, meaning_run_id,
        )

    def qa_audit_status(self, run_id: str) -> dict[str, Any]:
        return self._require_passage_semantic_runtime().qa_audit_status(run_id)

    def qa_audit_get_range(self, run_id: str) -> dict[str, Any]:
        return self._require_passage_semantic_runtime().qa_audit_range(run_id)

    def qa_audit_get_source_coverage(self, run_id: str) -> list[dict[str, Any]]:
        return self._require_passage_semantic_runtime().qa_audit_source_coverage(run_id)

    def qa_audit_get_target_support(self, run_id: str) -> list[dict[str, Any]]:
        return self._require_passage_semantic_runtime().qa_audit_target_support(run_id)

    def qa_audit_get_finding(self, finding_id: str) -> dict[str, Any]:
        return self._require_passage_semantic_runtime().qa_audit_finding(finding_id)

    def qa_audit_get_diagnostics(self, run_id: str) -> dict[str, Any]:
        return self._require_passage_semantic_runtime().qa_audit_diagnostics(run_id)

    # -- Stage 9A human review --------------------------------------------
    # Deliberately separate from the qaAudit.* analysis methods above: these
    # write human decisions, those only read machine analysis.

    def qa_review_get_queue(self, **filters: Any) -> dict[str, Any]:
        return self._require_passage_semantic_runtime().qa_review_queue(**filters)

    def qa_review_get_finding(self, finding_id: str) -> dict[str, Any]:
        return self._require_passage_semantic_runtime().qa_review_finding(finding_id)

    def qa_review_decide_finding(
        self, finding_id: str, disposition: str, **options: Any,
    ) -> dict[str, Any]:
        return self._require_passage_semantic_runtime().qa_review_decide(
            finding_id, disposition, **options)

    def correction_get_eligibility(self, finding_id: str) -> dict[str, Any]:
        """The one authoritative eligibility answer. The UI must call this,
        never re-derive it from a finding's fields."""
        return self._require_passage_semantic_runtime().correction_get_eligibility(finding_id)

    def correction_get_review_context(self, finding_id: str) -> dict[str, Any]:
        """Read-only current text and existing semantic grounding for 9B.2."""
        return self._require_passage_semantic_runtime().correction_get_review_context(finding_id)

    def correction_get_proposal(self, proposal_id: str) -> dict[str, Any]:
        return self._require_passage_semantic_runtime().correction_get_proposal(proposal_id)

    def correction_list_for_finding(self, finding_id: str) -> dict[str, Any]:
        return self._require_passage_semantic_runtime().correction_list_for_finding(finding_id)

    def correction_create_proposal(self, **options: Any) -> dict[str, Any]:
        runtime = self._require_passage_semantic_runtime()
        request_suggestion = bool(options.get("request_suggestion"))
        client: OpenAIResponsesClient | None = None
        provider = None
        if request_suggestion and self.settings.get_api_key():
            client = self._ai_client()
            provider = ConfiguredCorrectionSuggestionProvider(
                client, provider_name=self.settings.provider or "openai-compatible",
            )
        result = runtime.correction_create_proposal(provider=provider, **options)
        if client is not None:
            self.settings.record_ai_usage(client.last_usage.total_tokens, client.last_cost_usd)
        return result

    def correction_edit_proposal(self, proposal_id: str, **options: Any) -> dict[str, Any]:
        return self._require_passage_semantic_runtime().correction_edit_proposal(
            proposal_id, **options,
        )

    def correction_reject_proposal(self, proposal_id: str, **options: Any) -> dict[str, Any]:
        return self._require_passage_semantic_runtime().correction_reject_proposal(
            proposal_id, **options,
        )

    def correction_regenerate_proposal(self, proposal_id: str, **options: Any) -> dict[str, Any]:
        client = self._ai_client()
        provider = ConfiguredCorrectionSuggestionProvider(
            client, provider_name=self.settings.provider or "openai-compatible",
        )
        result = self._require_passage_semantic_runtime().correction_regenerate_proposal(
            proposal_id, provider=provider, **options,
        )
        self.settings.record_ai_usage(client.last_usage.total_tokens, client.last_cost_usd)
        return result

    def correction_get_proposal_history(self, proposal_id: str) -> dict[str, Any]:
        runtime = self._require_passage_semantic_runtime()
        return {
            "proposalId": proposal_id,
            "events": runtime.repository.correction_proposal_history(proposal_id),
        }

    def correction_apply_proposal(self, **options: Any) -> dict[str, Any]:
        self._require_passage_semantic_runtime()
        if self._correction_application_service is None:
            raise ProjectError("Correction application service is unavailable")
        return self._correction_application_service.apply(**options)

    def correction_get_application_status(self, application_id: str) -> dict[str, Any]:
        self._require_passage_semantic_runtime()
        if self._correction_application_service is None:
            raise ProjectError("Correction application service is unavailable")
        return self._correction_application_service.get_status(application_id)

    def correction_reanalyze_affected(
        self, application_id: str, requested_by: str, retry: bool = False,
    ) -> dict[str, Any]:
        self._require_passage_semantic_runtime()
        if self._correction_affected_analysis_service is None:
            raise ProjectError("Correction affected analysis service is unavailable")
        return self._correction_affected_analysis_service.start(
            application_id, requested_by=requested_by, retry=retry,
        )

    def _verification_service(self) -> CorrectionVerificationService:
        self._require_passage_semantic_runtime()
        if self._correction_verification_service is None:
            raise ProjectError("Correction verification service is unavailable")
        return self._correction_verification_service

    def correction_verify_application(
        self, application_id: str, requested_by: str,
    ) -> dict[str, Any]:
        """Positively verify an applied correction against current evidence.

        Read-only with respect to Scripture. It never sets CORRECTED and never
        concludes anything from a finding having disappeared.
        """
        return self._verification_service().verify(
            application_id, requested_by=requested_by,
        )

    def correction_get_verification(self, application_id: str) -> dict[str, Any]:
        return self._verification_service().get(application_id)

    def correction_acknowledge_corrected(self, **options: Any) -> dict[str, Any]:
        """The one path to a CORRECTED disposition. Requires explicit human action."""
        return self._verification_service().acknowledge_corrected(**options)

    def qa_review_add_note(
        self, entity_type: str, entity_id: str, note: str,
    ) -> dict[str, Any]:
        return self._require_passage_semantic_runtime().qa_review_add_note(
            entity_type, entity_id, note)

    def semantic_review_decide_location(
        self, relationship_id: str, decision: str, **options: Any,
    ) -> dict[str, Any]:
        return self._require_passage_semantic_runtime().semantic_review_decide_location(
            relationship_id, decision, **options)

    def semantic_review_decide_meaning(
        self, assessment_id: str, meaning_status: str, **options: Any,
    ) -> dict[str, Any]:
        return self._require_passage_semantic_runtime().semantic_review_decide_meaning(
            assessment_id, meaning_status, **options)

    def review_history_get_entity_history(
        self, entity_type: str, entity_id: str,
    ) -> dict[str, Any]:
        return self._require_passage_semantic_runtime().review_history(entity_type, entity_id)

    # -- Stage 9A.4 analysis orchestration --------------------------------

    def analysis_job_start(
        self, requested_scope: dict[str, Any], expected_analysis_fingerprint: str = "",
    ) -> dict[str, Any]:
        if requested_scope.get("_backendCorrectionScope") or requested_scope.get("correctionApplicationId"):
            raise AnalysisJobConflict(
                "Correction affected scopes must be resolved by correction.reanalyzeAffected"
            )
        if not expected_analysis_fingerprint:
            raise AnalysisJobConflict(
                "Resolve the selected scope status before starting analysis"
            )
        return self._analysis_jobs.start(
            self._require_passage_semantic_runtime(), requested_scope=requested_scope,
            expected_analysis_fingerprint=expected_analysis_fingerprint,
        )

    def analysis_job_status(self, job_id: str) -> dict[str, Any]:
        return self._analysis_jobs.status(job_id)

    def analysis_job_cancel(self, job_id: str) -> dict[str, Any]:
        return self._analysis_jobs.cancel(job_id)

    def analysis_job_get_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        return self._analysis_jobs.get_recent(
            self._require_passage_semantic_runtime(), limit=limit,
        )

    def analysis_job_get_scope_status(
        self, requested_scope: dict[str, Any],
    ) -> dict[str, Any]:
        return self._analysis_jobs.get_scope_status(
            self._require_passage_semantic_runtime(), requested_scope,
        )

    # -- live desktop connectors (Paratext/Logos) --------------------------
    #
    # Explicit connector calls remain available for diagnostics and Paratext note
    # handoff. Continuous navigation uses the coordinator below so slow/unavailable
    # desktop applications never block ordinary Bridge requests.

    def paratext_get_state(self) -> dict[str, Any]:
        state = ParatextConnectorClient().get_state()
        return asdict(state)

    def paratext_set_reference(self, reference: str, origin_id: str = "") -> dict[str, Any]:
        return ParatextConnectorClient().set_reference(reference, origin_id)

    # -- issue resolution + explicit Paratext handoff --------------------

    def list_issue_resolutions(self, chapter: str, verse: str) -> dict[str, Any]:
        self._require_project()
        items = self.project.list_issue_resolutions(chapter, verse)
        return {
            "chapter": str(chapter), "verse": str(verse), "items": items,
            "queued": sum(1 for item in items if (item.get("paratext") or {}).get("status") == "queued"),
            "sent": sum(1 for item in items if (item.get("paratext") or {}).get("status") == "sent"),
            "resolved": sum(1 for item in items if item.get("status") == "resolved"),
            "reflagged": sum(1 for item in items if item.get("status") == "reflagged"),
        }

    def save_issue_resolution(
        self, chapter: str, verse: str, tool: str, group_id: str, check_id: str,
        expected_fingerprint: str, selected_text: str = "", issue_summary: str = "",
        reviewer_note: str = "", proposed_correction: str = "",
        evidence: Optional[list[Any]] = None,
    ) -> dict[str, Any]:
        self._require_project()
        with self._checker_lock:
            return self.project.save_issue_resolution(
                chapter, verse, tool, group_id, check_id, expected_fingerprint,
                selected_text=selected_text, issue_summary=issue_summary,
                reviewer_note=reviewer_note, proposed_correction=proposed_correction,
                evidence=evidence or [],
                username=self.settings.reviewer_name or "Bridge Reviewer",
            )

    @staticmethod
    def _issue_handoff_comment(record: dict[str, Any]) -> str:
        check = record.get("check") if isinstance(record.get("check"), dict) else {}
        tool = "Translation Note" if check.get("tool") == "translationNotes" else "Translation Word"
        parts = [
            f"Bridge {tool} review for {record.get('reference', '')}",
            f"Issue: {record.get('issueSummary', '')}",
        ]
        correction = str(record.get("proposedCorrection") or "").strip()
        if correction:
            parts.append(f"Proposed correction: {correction}")
        evidence = list(record.get("evidence") or [])
        if evidence:
            labels = []
            for item in evidence[:10]:
                if isinstance(item, dict):
                    labels.append(str(item.get("title") or item.get("identifier") or item.get("kind") or "Evidence"))
                else:
                    labels.append(str(item))
            parts.append("Evidence: " + "; ".join(x for x in labels if x))
        parts.append(f"Reviewer note: {record.get('reviewerNote', '')}")
        return "\n\n".join(parts)

    def _save_handoff_item(
        self, chapter: str, verse: str, resolution_id: str,
        item: dict[str, Any], event: str,
    ) -> dict[str, Any]:
        state = self.project.load_paratext_note_sync_state()
        items = dict(state.get("items") or {})
        items[str(item["messageId"])] = item
        state["items"] = items
        self.project.save_paratext_note_sync_state(state)
        return self.project.update_issue_resolution_paratext(
            chapter, verse, resolution_id,
            {
                "status": item["status"], "messageId": item["messageId"],
                "attempts": item["attempts"], "lastError": item.get("lastError", ""),
                "sentAt": item.get("sentAt", ""), "remoteId": item.get("remoteId", ""),
                "contentSignature": item["contentSignature"],
                "expectedProjectId": item.get("expectedProjectId", ""),
            },
            event,
        )

    def _attempt_issue_handoff(
        self, chapter: str, verse: str, resolution_id: str, item: dict[str, Any],
    ) -> dict[str, Any]:
        item = dict(item)
        item["attempts"] = int(item.get("attempts") or 0) + 1
        expected_project_id = str(item.get("expectedProjectId") or "").strip()
        try:
            state = ParatextConnectorClient().get_state()
            capabilities = {str(capability).strip().casefold() for capability in state.capabilities}
            if not capabilities.intersection({"create_note", "project_notes"}):
                raise ParatextConnectorError(
                    "The connected Paratext companion does not support live note creation yet; the Notes 1.1 handoff remains safely queued."
                )
            if not expected_project_id:
                raise ParatextConnectorError(
                    "Confirm the destination Paratext project before sending this note."
                )
            if state.project_id.casefold() != expected_project_id.casefold():
                raise ParatextConnectorError(
                    "The active Paratext project does not match the project confirmed for this handoff."
                )
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            response = ParatextConnectorClient().create_note(
                str(payload.get("reference") or ""), str(payload.get("selectedText") or ""),
                str(payload.get("comment") or ""), project_id=expected_project_id,
                message_id=str(item.get("messageId") or ""),
            )
            item["status"] = "sent"
            item["lastError"] = ""
            item["sentAt"] = datetime.now(timezone.utc).isoformat()
            item["remoteId"] = str(response.get("note_id") or response.get("thread_id") or "")
            record = self._save_handoff_item(chapter, verse, resolution_id, item, "paratext_sent")
        except ParatextConnectorError as exc:
            item["status"] = "queued"
            item["lastError"] = str(exc)
            record = self._save_handoff_item(chapter, verse, resolution_id, item, "paratext_queued")
        return {"record": record, "handoff": item}

    def queue_issue_resolution_for_paratext(
        self, chapter: str, verse: str, resolution_id: str, expected_project_id: str = "",
    ) -> dict[str, Any]:
        self._require_project()
        record = self.project.load_issue_resolution(chapter, verse, resolution_id)
        comment = self._issue_handoff_comment(record)
        signature = str((record.get("paratext") or {}).get("contentSignature") or "")
        message_id = f"bridge-{resolution_id}-{signature[:12]}"
        sync = self.project.load_paratext_note_sync_state()
        existing = (sync.get("items") or {}).get(message_id)
        if isinstance(existing, dict) and existing.get("status") == "sent":
            return {"record": record, "handoff": existing}
        note_path = self.project.record_paratext_note(
            chapter, verse, comment,
            username=self.settings.reviewer_name or "Bridge Reviewer",
            selected_text=str(record.get("selectedText") or ""),
            metadata={"resolutionId": resolution_id, "paratextThreadType": "BridgeTranslationIssue"},
            thread_id=message_id,
        )
        item = dict(existing) if isinstance(existing, dict) else {
            "messageId": message_id, "resolutionId": resolution_id,
            "createdAt": datetime.now(timezone.utc).isoformat(), "attempts": 0,
        }
        item.update({
            "status": "queued", "contentSignature": signature,
            "expectedProjectId": str(expected_project_id or item.get("expectedProjectId") or ""),
            "notePath": str(note_path),
            "payload": {
                "reference": record["reference"], "selectedText": record.get("selectedText", ""),
                "comment": comment,
            },
        })
        self._save_handoff_item(chapter, verse, resolution_id, item, "paratext_queued")
        return self._attempt_issue_handoff(chapter, verse, resolution_id, item)

    def retry_issue_resolution_paratext(
        self, chapter: str, verse: str, resolution_id: str,
    ) -> dict[str, Any]:
        self._require_project()
        record = self.project.load_issue_resolution(chapter, verse, resolution_id)
        message_id = str((record.get("paratext") or {}).get("messageId") or "")
        item = (self.project.load_paratext_note_sync_state().get("items") or {}).get(message_id)
        if not message_id or not isinstance(item, dict):
            raise ProjectError('This issue has not been queued for Paratext yet.')
        if item.get("status") == "sent":
            return {"record": record, "handoff": item}
        return self._attempt_issue_handoff(chapter, verse, resolution_id, item)

    def _logos_client_instance(self) -> LogosConnectorClient:
        if self._logos_client is None:
            self._logos_client = LogosConnectorClient()
            # Best-effort clean shutdown of the persistent PowerShell helper on a
            # normal interpreter exit. atexit does not run on a hard kill (e.g. Tauri
            # force-terminating the sidecar) — a known, documented limitation, not
            # silently unaddressed; see logos_connector/README.md.
            atexit.register(self._logos_client.close)
        return self._logos_client

    def logos_get_state(self) -> dict[str, Any]:
        state = self._logos_client_instance().get_state()
        return asdict(state)

    def logos_set_reference(self, reference: str, origin_id: str = "") -> dict[str, Any]:
        state = self._logos_client_instance().set_reference(reference, origin_id=origin_id)
        return asdict(state)

    def navigation_status(self, context: str = "") -> dict[str, Any]:
        return self._navigation.snapshot(context=context)

    def navigation_bridge_changed(self, reference: str) -> dict[str, Any]:
        return self._navigation.bridge_changed(reference)

    def navigation_resolve(
        self, request_id: str, accepted: bool, bridge_reference: str = "", context: str = "",
    ) -> dict[str, Any]:
        return self._navigation.resolve(
            request_id,
            accepted=accepted,
            bridge_reference=bridge_reference,
            context=context,
        )

    def _usfm_findings_for_book(
        self, project: Optional[TranslationCoreProject] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> list[QaFinding]:
        """Lazily compute + cache whole-book USFM structural findings.

        Not invalidated by verse.edit: re-running the real checker (a
        subprocess loading a full tag database) after every keystroke-level
        edit would be far too slow, and a single verse edit essentially
        never changes book-wide structure (duplicate/missing verse numbers,
        unclosed markers). Accepted as a known limitation, not an oversight —
        re-opening the project re-runs it fresh.
        """
        project = project or self.project
        if project is None:
            raise ProjectError("No project open — call project.open first")
        book_key = str(project.path)
        cached = self._usfm_findings_by_book.get(book_key)
        if cached is not None:
            return cached
        cached_error = self._usfm_errors_by_book.get(book_key)
        if cached_error is not None:
            raise UsfmCheckerError(cached_error)

        usfm_path = project.usfm_path()
        findings: list[QaFinding] = []
        if usfm_path is not None:
            try:
                usfm_text = usfm_path.read_text(encoding="utf-8-sig")
            except OSError:
                usfm_text = ""
            if usfm_text:
                content_hash = hashlib.sha256(usfm_text.encode("utf-8")).hexdigest()
                cached_section = project.load_check_cache().get("usfm") or {}
                if cached_section.get("contentHash") == content_hash:
                    findings = [QaFinding.from_dict(d) for d in cached_section.get("findings", [])]
                    self._usfm_findings_by_book[book_key] = findings
                    return findings
                try:
                    findings = self.greek_room.check_book_usfm(
                        project_id=str(project.summary.path),
                        book_id=project.book_id,
                        usfm_text=usfm_text,
                        cancel_event=cancel_event,
                    )
                except UsfmCheckerCancelled:
                    raise
                except UsfmCheckerError as exc:
                    self._usfm_errors_by_book[book_key] = str(exc)
                    raise
                for f in findings:
                    # Same stabilization as Greek Room findings (see
                    # run_verse_checks below) — otherwise a fresh subprocess
                    # run after an app restart would hand out new random
                    # uuid4 ids for the same underlying issues, and any
                    # decision recorded on them in a prior session could
                    # never be matched back.
                    f.id = _stable_finding_id(
                        chapter=str(f.chapter), verse=str(f.verse), engine=f.engine,
                        check_type=f.check_type, disambiguator=f.explanation,
                    )
                # Persist after id stabilization so a cache hit hands back
                # the same stable ids a fresh run would — required for prior
                # decisions (keyed by finding id) to keep matching.
                project.save_check_cache_section(
                    "usfm", content_hash, [f.to_dict() for f in findings],
                )
        self._usfm_findings_by_book[book_key] = findings
        return findings

    def _names_findings_for_book(
        self, project: Optional[TranslationCoreProject] = None,
    ) -> list[QaFinding]:
        """Lazily compute + cache whole-book names/transliteration spelling
        findings (Uroman romanization + vendored Smart Edit Distance — see
        NamesAdapter's own docstring). Same reason and shape as
        _usfm_findings_for_book: consistency is inherently a corpus-level
        question, so this is computed once per book and not invalidated by
        verse.edit — a single verse edit changing one word's spelling could
        in principle change the answer, but re-running a full whole-book
        vocabulary scan after every keystroke-level edit would be far too
        slow; re-opening the project re-runs it fresh, same tradeoff as USFM.
        """
        project = project or self.project
        if project is None:
            raise ProjectError("No project open — call project.open first")
        book_key = str(project.path)
        cached = self._names_findings_by_book.get(book_key)
        if cached is not None:
            return cached
        cached_error = self._names_errors_by_book.get(book_key)
        if cached_error is not None:
            raise NamesCheckError(cached_error)

        text_map = self._book_verse_text_map(project)
        # "|spans-v1" forces a one-time cache invalidation for on-disk
        # checkCache.json sections written before findings carried
        # start_offset/end_offset — otherwise a content-hash match on
        # unchanged verse text would keep silently returning the older,
        # offset-less cached findings.
        content_hash = hashlib.sha256(
            ("\n".join(f"{k}={v}" for k, v in sorted(text_map.items())) + "|spans-v1").encode("utf-8")
        ).hexdigest()
        cached_section = project.load_check_cache().get("names") or {}
        if cached_section.get("contentHash") == content_hash:
            findings = [QaFinding.from_dict(d) for d in cached_section.get("findings", [])]
            self._names_findings_by_book[book_key] = findings
            return findings

        token_occurrences: dict[str, list[tuple[str, str]]] = {}
        for ref, text in text_map.items():
            chapter, _, verse = ref.partition(":")
            for token in whitespace_tokens(text):
                token_occurrences.setdefault(token, []).append((chapter, verse))

        target = project.manifest.get("target_language", {})
        lang_code = str(target.get("id") or "") if isinstance(target, dict) else ""

        try:
            findings = self.greek_room.check_book_names(
                project_id=str(project.summary.path),
                book_id=project.book_id,
                lang_code=lang_code,
                token_occurrences=token_occurrences,
            )
        except NamesCheckError as exc:
            self._names_errors_by_book[book_key] = str(exc)
            raise
        for f in findings:
            # Same stabilization reason as USFM findings above: a stable id
            # keyed on the two spellings being compared (sorted, so it
            # doesn't matter which one this run happened to treat as
            # "majority") so a repeat run's random pairing order can't
            # orphan a prior human decision.
            disambiguator = "::".join(sorted([f.original_text, f.suggested_replacement or ""]))
            f.id = _stable_finding_id(
                chapter=str(f.chapter), verse=str(f.verse), engine=f.engine,
                check_type=f.check_type, disambiguator=disambiguator,
            )
            # NamesAdapter deliberately never sees verse text (only
            # token_occurrences), so it can't compute a span itself — do it
            # here, against the exact same raw text_map string the frontend
            # highlights into, not whitespace_tokens' strip_usfm'd copy
            # (which can shift character positions via whitespace collapse).
            verse_text = text_map.get(f"{f.chapter}:{f.verse}")
            if verse_text:
                span = _first_token_span(verse_text, f.original_text)
                if span:
                    f.start_offset, f.end_offset = span
        # Persist after id stabilization (see USFM above — same reasoning).
        # The hash is over current verse text, so an edit_verse call between
        # opens correctly invalidates this on the next reopen, unlike the
        # in-memory cache this replaces which never distinguished the two.
        project.save_check_cache_section(
            "names", content_hash, [f.to_dict() for f in findings],
        )
        self._names_findings_by_book[book_key] = findings
        return findings

    # Heuristic thresholds for _consistency_findings_for_book — unlike the
    # PMI/translation-probability formulas in alignment_statistics.py
    # (which mirror ualign.py's own AlignmentModel.support_probability()),
    # these three numbers have no textbook source; they're a starting,
    # tunable bar for "recurrent enough and fragmented enough to be worth a
    # human's attention", deliberately conservative (low severity, low
    # confidence — see the finding below) so this never claims more
    # certainty than a corpus-frequency heuristic actually has.
    _CONSISTENCY_MIN_OCCURRENCES = 3
    _CONSISTENCY_MIN_RENDERINGS = 3
    _CONSISTENCY_DOMINANCE_THRESHOLD = 0.7

    def _consistency_findings_for_book(
        self, project: Optional[TranslationCoreProject] = None,
    ) -> list[QaFinding]:
        """Whole-book Layer-2 check: a source (original-language) word that
        recurs often across this book's own human-completed alignments but
        maps to many different target renderings with no dominant one is
        flagged as a possible inconsistent-translation signal — using
        alignment_statistics.py's existing corpus co-occurrence table
        exactly as that module's own docstring anticipated ("a future
        phase can layer findings on top of this data without recomputing
        it"). This is corpus-wide, not tied to any one occurrence's
        chapter:verse — CorpusStatsTable aggregates counts only, it does
        not retain which verse produced which pairing (see its own
        docstring) — so, like a whole-book USFM finding with no matching
        verse slot, this surfaces on the book's first chapter at verse 0
        rather than a fabricated precise location. An honest book-level
        flag, not a claim about any specific occurrence.

        A blunt statistical signal, not a semantic judgment: some of what
        this flags will be legitimate contextual variation, not a real
        error — see the roadmap's own caution about distinguishing the
        two. That distinction needs real semantic evaluation (issue #16),
        not corpus frequency alone, hence the low severity/confidence.
        """
        project = project or self.project
        if project is None:
            raise ProjectError("No project open — call project.open first")
        book_key = str(project.path)
        cached = self._consistency_findings_by_book.get(book_key)
        if cached is not None:
            return cached

        table = self._corpus_stats(project)

        renderings: dict[str, Counter] = {}
        for (source_word, target_word), count in table.pair_counts.items():
            renderings.setdefault(source_word, Counter())[target_word] += count

        chapters = project.chapters()
        first_chapter = chapters[0] if chapters else "1"
        findings: list[QaFinding] = []
        for source_word, counts in renderings.items():
            total = sum(counts.values())
            if total < self._CONSISTENCY_MIN_OCCURRENCES or len(counts) < self._CONSISTENCY_MIN_RENDERINGS:
                continue
            top_word, top_count = counts.most_common(1)[0]
            if top_count / total >= self._CONSISTENCY_DOMINANCE_THRESHOLD:
                continue  # one rendering clearly dominates — not flagged
            evidence = [
                EvidenceItem(label=f'"{word}"', value=f"{n} of {total} occurrence(s)")
                for word, n in counts.most_common()
            ]
            findings.append(QaFinding(
                id=_stable_finding_id(
                    chapter=str(first_chapter), verse="0", engine="alignment-corpus",
                    check_type="alignment.inconsistent_rendering", disambiguator=source_word,
                ),
                project_id=str(project.summary.path), book=project.book_id,
                chapter=int(next(iter(re.findall(r"\d+", str(first_chapter))), "0")), verse=0,
                engine="alignment-corpus", check_type="alignment.inconsistent_rendering",
                category=FindingCategory.CONSISTENCY, severity=Severity.LOW, confidence=0.4,
                original_text=source_word,
                explanation=(
                    f'"{source_word}" occurs {total} times in completed alignments across this '
                    f"book with {len(counts)} different target renderings and no single dominant "
                    f'one (most common: "{top_word}", {top_count}/{total}).'
                ),
                evidence=evidence,
                engine_version=BRIDGE_VERSION,
            ))

        self._consistency_findings_by_book[book_key] = findings
        return findings

    def run_verse_checks(self, chapter: str, verse: str,
                          checks: list[str]) -> list[QaFinding]:
        """The unified check entrypoint: local QA (tN/tW/alignment) +
        Greek Room, merged into one QaFinding list — this IS the
        background chapter/book-wise automation the UI's status bar
        reflects, called once per verse during that pass.

        Findings get STABLE ids (see _stable_finding_id) and any prior
        human decision recorded via decide_verse is re-applied here, so
        reopening a project or re-running checks doesn't reset a verse
        you already reviewed back to "open"."""
        self._require_project()
        project = self.project
        # ReviewPanel asks for a Greek-Room-only live check as soon as a verse
        # is selected.  First-time tN/tW/USFM/names preparation can hold the
        # checker lock for much longer than the desktop's interactive timeout.
        # Waiting here would block the synchronous stdio dispatcher itself, so
        # checks.status and check.listForVerse queued behind this request would
        # time out too.  Greek Room reads only the captured project/verse and
        # its own adapter; it does not touch tC selection/index state and does
        # not need the checker lock.
        needs_checker_lock = any(
            name in {"local", "tN", "tW", "alignment", "usfm", "names", "consistency"}
            for name in checks
        )
        timings = check_timing.Timings()
        with check_timing.activate(timings):
            if not needs_checker_lock:
                findings = self._run_verse_checks_for_project(project, chapter, verse, checks)
            else:
                with self._checker_lock:
                    if any(name in checks for name in ("local", "tN", "tW")):
                        with timings.step("preflight.resource_indexes"):
                            self._ensure_resource_indexes(project)
                    findings = self._run_verse_checks_for_project(project, chapter, verse, checks)
        check_timing.trace(f"verse.runChecks {chapter}:{verse} {list(checks)}", timings)
        return findings

    def _run_verse_checks_for_project(
        self,
        project: TranslationCoreProject,
        chapter: str,
        verse: str,
        checks: list[str],
        reads: Optional[dict[str, Any]] = None,
    ) -> list[QaFinding]:
        """`reads`: a check job's memo for this one verse, shared by its
        stages so the verse's text and decisions are read once, not once per
        stage (#231). Each read costs a workbench connection."""
        findings: list[QaFinding] = []
        project_id = str(project.summary.path)
        book = project.summary.book_id
        timings = check_timing.current()
        reads = {} if reads is None else reads

        if "text" not in reads:
            with timings.step("verse.read_text"):
                reads["text"] = project.target_verse_text(chapter, verse)
        target_text = reads["text"]

        if "local" in checks or "tN" in checks or "tW" in checks or "alignment" in checks:
            with timings.step("local.alignment_load"):
                alignment = project.load_verse_alignment(chapter, verse)
            issues = run_local_qa(project, chapter, verse, alignment)
            if "local" in checks or "alignment" in checks:
                # #220: what the automatic pass could not place, as findings the
                # editor shows. Quiet on a verse no pass has run on; a live gap
                # only, so a reviewer's link or decision silences it at once.
                try:
                    with timings.step("local.alignment_gap"):
                        issues = [*issues, *alignment_gap_checks.gap_issues(
                            project, chapter, verse, alignment, target_text,
                        )]
                except Exception:
                    # Optional evidence: a problem here must never sink the
                    # verse's ordinary checks.
                    pass
            resource_versions = self._pinned_resource_versions(project)
            for issue in issues:
                findings.append(_qaissue_to_finding(
                    issue, project_id=project_id, book=book,
                    chapter=chapter, verse=verse,
                    resource_versions=resource_versions,
                ))

        if "local" in checks or "usfm" in checks:
            # Whole-book findings that fall on this chapter; a finding with
            # no existing verse slot (e.g. "chapter is missing verse 4")
            # surfaces on the chapter's first verse rather than nowhere,
            # since the UI can only request verses that actually exist.
            book_verses = project.verses(chapter) if chapter in project.chapters() else []
            existing_verses = {str(value) for value in book_verses}
            first_verse = book_verses[0] if book_verses else None
            with timings.step("usfm.book"):
                usfm_findings = self._usfm_findings_for_book(project)
            for f in usfm_findings:
                if str(f.chapter) != str(chapter):
                    continue
                if str(f.verse) == str(verse):
                    findings.append(f)
                elif (
                    first_verse is not None
                    and str(verse) == str(first_verse)
                    and (f.verse == 0 or str(f.verse) not in existing_verses)
                ):
                    findings.append(f)

        if "local" in checks or "names" in checks:
            # Unlike USFM findings, every names/spelling finding is anchored
            # at a real occurrence's own verse (the minority spelling's
            # first location — see NamesAdapter._build_finding), never a
            # placeholder chapter-level slot, so no first-verse fallback is
            # needed here.
            with timings.step("names.book"):
                names_findings = self._names_findings_for_book(project)
            for f in names_findings:
                if str(f.chapter) == str(chapter) and str(f.verse) == str(verse):
                    findings.append(f)

        if "consistency" in checks:
            # Opt-in, not bundled into "local" — a corpus-frequency
            # heuristic (see _consistency_findings_for_book) that would
            # otherwise silently change the finding volume for every
            # existing "local" caller. Same book-level chapter-0/verse-0
            # placeholder fallback as the USFM block above, since these
            # findings aren't anchored to one specific occurrence either.
            book_verses = project.verses(chapter) if chapter in project.chapters() else []
            existing_verses = {str(value) for value in book_verses}
            first_verse = book_verses[0] if book_verses else None
            with timings.step("consistency.book"):
                consistency_findings = self._consistency_findings_for_book(project)
            for f in consistency_findings:
                if str(f.chapter) != str(chapter):
                    continue
                if str(f.verse) == str(verse):
                    findings.append(f)
                elif (
                    first_verse is not None
                    and str(verse) == str(first_verse)
                    and (f.verse == 0 or str(f.verse) not in existing_verses)
                ):
                    findings.append(f)

        if "greekroom" in checks or "wildebeest" in checks:
            target_language = project.manifest.get("target_language", {})
            language_id = str(target_language.get("id") or "") if isinstance(target_language, dict) else ""
            with timings.step("greekroom.wildebeest"):
                gr_findings = self.greek_room.check_verse(
                    project_id=project_id,
                    lang_code=language_id,
                    ref=f"{book} {chapter}:{verse}",
                    text=target_text,
                    checks=["wildebeest"],
                )
            for f in gr_findings:
                # Greek Room findings default to a random uuid4 id from
                # QaFinding's dataclass default — override with a stable
                # one keyed on the span, so the same flagged character
                # range gets the same id across repeated check runs.
                f.id = _stable_finding_id(
                    chapter=chapter, verse=verse, engine=f.engine,
                    check_type=f.check_type,
                    disambiguator=f"{f.start_offset}:{f.end_offset}:{f.original_text}",
                )
            findings.extend(gr_findings)

        # Re-apply any prior human decision so re-running checks (or
        # reopening the project) doesn't silently forget review state.
        if "decisions" not in reads:
            with timings.step("decisions.reapply"):
                reads["decisions"] = project.qa_decisions_for_verse(chapter, verse)
        prior_decisions = reads["decisions"]
        for finding in findings:
            record = prior_decisions.get(finding.id)
            if record:
                try:
                    finding.status = FindingStatus(record.get("decision", "open"))
                except ValueError:
                    pass
                finding.human_comment = record.get("note") or None

        return findings

    # -- background checking jobs -----------------------------------------

    def start_check_job(
        self,
        *,
        scope: str = "chapter",
        chapters: Optional[list[str]] = None,
        checks: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        self._require_project()
        project = self.project
        available = project.chapters()
        requested = [str(ch) for ch in (chapters or ([] if scope == "book" else available[:1]))]
        if scope == "book" and not requested:
            requested = list(available)
        if scope not in {"chapter", "book"}:
            raise CheckJobError("Check scope must be 'chapter' or 'book'.")
        if not requested:
            raise CheckJobError("At least one chapter is required.")
        unknown = [ch for ch in requested if ch not in available]
        if unknown:
            raise CheckJobError(f"Unknown chapter(s): {', '.join(unknown)}")

        selected_checks = tuple(dict.fromkeys(checks or ["local", "greekroom"]))
        supported = {"local", "tN", "tW", "alignment", "usfm", "greekroom", "wildebeest", LANGUAGE_QA_CHECK}
        invalid = [name for name in selected_checks if name not in supported]
        if invalid:
            raise CheckJobError(f"Unknown check type(s): {', '.join(invalid)}")

        spec = CheckJobSpec(
            scope=scope,
            project_path=str(project.path),
            chapters=tuple(requested),
            chapter_verses={ch: list(project.verses(ch)) for ch in requested},
            checks=selected_checks,
        )
        return self._start_check_job_from_spec(spec, project)

    def _start_check_job_from_spec(
        self, spec: CheckJobSpec, project: TranslationCoreProject, *,
        language_qa: Optional[LanguageQaManager] = None,
        check_jobs: Optional[CheckJobManager] = None,
    ) -> dict[str, Any]:
        """`language_qa`/`check_jobs` default to the open project's own. The
        collection runner (Phase 4.4) passes its own for a book that is not the
        open one, so the editor's Language QA and check job are never touched."""
        language_qa = language_qa or self._language_qa
        check_jobs = check_jobs or self._check_jobs

        # One verse's text and decisions, read by its first stage and reused by
        # the next (#231). Only ever the current verse: a decision recorded on a
        # later verse while the job runs is read when the job gets there.
        verse_reads: dict[str, Any] = {}

        def run_stage(chapter: str, verse: str, stage_checks: list[str]) -> Any:
            if stage_checks == [LANGUAGE_QA_CHECK]:
                # The book pass ran in the preflight; this verse's share of it.
                return language_qa.verse_results(chapter, verse)
            if verse_reads.get("key") != (chapter, verse):
                verse_reads.clear()
                verse_reads["key"] = (chapter, verse)
            with self._checker_lock:
                return [
                    finding.to_dict()
                    for finding in self._run_verse_checks_for_project(
                        project, chapter, verse, stage_checks, reads=verse_reads,
                    )
                ]

        preflight = None
        if any(name in spec.checks for name in ("local", "tN", "tW", "usfm", "names", LANGUAGE_QA_CHECK)):
            def run_preflight(cancel_event: threading.Event) -> None:
                if LANGUAGE_QA_CHECK in spec.checks:
                    # The authoritative Language QA pass: the same scan and cache
                    # as live editing, on this job's thread. The wordlist audit
                    # needs the whole book, so even a chapter job scans the book;
                    # unchanged verses come from the persisted cache. Serialised
                    # with the background worker by Language QA's own pass lock,
                    # not _checker_lock: the dispatcher takes _checker_lock for
                    # verse.runChecks, and must not wait on a book pass.
                    with check_timing.current().step("preflight.language_qa"):
                        passed = language_qa.run_pass(cancel_event)
                    if passed is None and not cancel_event.is_set():
                        raise CheckJobError("Language QA could not check this book.")
                    if cancel_event.is_set():
                        return
                with self._checker_lock:
                    if any(name in spec.checks for name in ("local", "tN", "tW")):
                        with check_timing.current().step("preflight.resource_indexes"):
                            self._ensure_resource_indexes(project)
                    if cancel_event.is_set():
                        return
                    if any(name in spec.checks for name in ("local", "usfm")):
                        with check_timing.current().step("preflight.usfm"):
                            self._usfm_findings_for_book(project, cancel_event=cancel_event)
                    if cancel_event.is_set():
                        return
                    if any(name in spec.checks for name in ("local", "names")):
                        # Unlike USFM's subprocess, this is in-process pure
                        # Python with no mid-flight cancellation support yet
                        # — it either hasn't started (skipped by the check
                        # above) or runs to completion. Acceptable for now;
                        # revisit if real book-sized timing (see
                        # docs/BUILD_LOG.md's Phase 5 section) shows
                        # this needs the same cooperative-cancel treatment
                        # USFM's subprocess has.
                        with check_timing.current().step("preflight.names"):
                            self._names_findings_for_book(project)
            preflight = run_preflight

        return check_jobs.start(
            spec, run_stage=run_stage, preflight=preflight,
            on_complete=lambda job: self._on_check_job_complete(project, job),
        )

    # -- collection-level QA runs (layered-rules Phase 4.4) -------------------

    def start_collection_checks(self, *, checks: Optional[list[str]] = None, force: bool = False) -> dict[str, Any]:
        """collection.runChecks: every book of the open collection, in
        `.bridge/collection.json` order, each through the same check job a
        "Run whole book" starts. Books whose recorded run still matches their
        chapter files are skipped (resumable), unless `force`."""
        self._require_project()
        if self._check_jobs.active():
            raise CheckJobConflict("Wait for the running check to finish.")
        selected = tuple(dict.fromkeys(checks or ["local", "greekroom", LANGUAGE_QA_CHECK]))
        supported = {"local", "tN", "tW", "alignment", "usfm", "greekroom", "wildebeest", LANGUAGE_QA_CHECK}
        invalid = [name for name in selected if name not in supported]
        if invalid:
            raise CheckJobError(f"Unknown check type(s): {', '.join(invalid)}")
        collection_path = str(self.project.path)
        siblings = collection_projects(collection_path) or [{
            "path": collection_path, "bookId": self.project.book_id,
            "bookName": self.project.summary.book_name, "lazy": False,
        }]
        books = tuple(
            {"path": str(entry.get("path") or ""), "bookId": str(entry.get("bookId") or ""),
             "bookName": str(entry.get("bookName") or "")}
            for entry in siblings if Path(str(entry.get("path") or "")).is_dir()
        )
        spec = CollectionJobSpec(
            collection_path=collection_path, books=books, checks=selected,
            previous_runs=collection_qa_runs(collection_path), force=bool(force),
        )
        return self._collection_jobs.start(
            spec,
            run_book=lambda book, cancel: self._run_collection_book(book, selected, cancel),
            record_run=lambda entry: record_collection_qa_run(collection_path, entry),
            final_stage=lambda done, cancel: self._collection_final_stage(collection_path, done, cancel),
        )

    def _run_collection_book(self, book: dict[str, Any], checks: tuple[str, ...],
                             cancel_event: threading.Event) -> dict[str, Any]:
        """One book, end to end, on the collection runner's thread: materialize
        a lazy sibling, run the whole-book check job, persist (the job's own
        completion hook writes the rollup and snapshots), then let go of it."""
        path = str(book["path"])
        materialize_lazy_project(path)
        project = TranslationCoreProject(Path(path), workspace=self.workspace)
        chapters = project.chapters()
        spec = CheckJobSpec(scope="book", project_path=path, chapters=tuple(chapters),
                            chapter_verses={ch: list(project.verses(ch)) for ch in chapters}, checks=checks)
        language_qa = LanguageQaManager(debounce=0, yield_seconds=0) if LANGUAGE_QA_CHECK in checks else None
        manager = CheckJobManager()
        try:
            if language_qa is not None:
                language_qa.bind(project, autostart=False)
            snapshot = self._start_check_job_from_spec(spec, project, language_qa=language_qa, check_jobs=manager)
            # Progress polls ask for no results at all (a cursor past every
            # verse); the full set is read once, after the job ends (#229).
            unbounded = sum(len(v) for v in spec.chapter_verses.values())
            while snapshot["state"] not in {"succeeded", "failed", "cancelled"}:
                if cancel_event.is_set():
                    manager.cancel(snapshot["jobId"])
                time.sleep(0.2)
                snapshot = manager.status(snapshot["jobId"], since=unbounded)
            snapshot = manager.status(snapshot["jobId"])
        finally:
            if language_qa is not None:
                language_qa.unbind()
        by_category: dict[str, int] = {}
        for result in snapshot["results"].values():
            for finding in result.get("findings") or []:
                if finding.get("status") == "open":
                    category = str(finding.get("category") or "other")
                    by_category[category] = by_category.get(category, 0) + 1
            open_lqa = len((result.get("languageQa") or {}).get("findings") or [])
            if open_lqa:
                by_category["languageQa"] = by_category.get("languageQa", 0) + open_lqa
        return {"state": snapshot["state"], "jobId": snapshot["jobId"], "error": snapshot.get("error"),
                "findingsByCategory": by_category, "checkedVerses": snapshot.get("completedVerses", 0)}

    def _collection_final_stage(self, collection_path: str, books: list[dict[str, Any]],
                                cancel_event: threading.Event) -> dict[str, Any]:
        """The passes that only make sense across the whole collection, run
        once after every book: a termbase coverage report and cross-book name
        consistency. Reports only; nothing here writes Scripture."""
        stage: dict[str, Any] = {"completedAt": None, "termbaseCoverage": [], "crossBookNames": None,
                                 "houseStylePropagation": None}
        texts: dict[str, dict[str, str]] = {}
        styles: list[tuple[str, list[dict[str, Any]], list[dict[str, Any]]]] = []
        for book in books:
            if cancel_event.is_set():
                return {**stage, "cancelled": True}
            project = TranslationCoreProject(Path(book["path"]), workspace=self.workspace)
            texts[book["bookId"]] = self._book_verse_text_map(project)
            stage["termbaseCoverage"].append(_termbase_coverage(book["bookId"], project.terminology_rules(),
                                                                texts[book["bookId"]]))
            styles.append((book["bookId"], project.housestyle_entries(), project.project_qa_decisions()))
        # House-style propagation (6.4): proposals only, never applied here.
        proposals = project_proposals(styles)
        stage["houseStylePropagation"] = {
            "available": True, "proposals": proposals,
            "reason": f"{len(proposals)} proposal(s); accept or dismiss them in Settings → Terminology → House style."}
        if not cancel_event.is_set() and len(texts) > 1:
            stage["crossBookNames"] = self._cross_book_names(texts)
        stage["completedAt"] = datetime.now(timezone.utc).isoformat()
        try:
            record_collection_qa_run(collection_path, {}, final_stage={
                "completedAt": stage["completedAt"],
                "termbaseCoverage": stage["termbaseCoverage"],
                "crossBookNames": (stage["crossBookNames"] or {}).get("summary"),
            })
        except Exception as exc:
            stage["recordError"] = str(exc)
        return stage

    def _cross_book_names(self, texts: dict[str, dict[str, str]]) -> dict[str, Any]:
        """The names adapter over the union of every book's tokens: a spelling
        that is the minority form across books, which no single book shows."""
        occurrences: dict[str, list[tuple[str, str]]] = {}
        for book_id, text_map in texts.items():
            for ref, text in text_map.items():
                chapter, _, verse = ref.partition(":")
                for token in whitespace_tokens(text):
                    occurrences.setdefault(token, []).append((f"{book_id.upper()} {chapter}", verse))
        target = self.project.manifest.get("target_language", {}) if self.project else {}
        lang_code = str(target.get("id") or "") if isinstance(target, dict) else ""
        try:
            findings = self.greek_room.check_book_names(
                project_id="collection", book_id="collection", lang_code=lang_code,
                token_occurrences=occurrences)
        except Exception as exc:
            return {"available": False, "error": str(exc), "summary": {"available": False}}
        rows = [{"explanation": f.explanation, "originalText": f.original_text,
                 "suggestedReplacement": f.suggested_replacement,
                 "evidence": [{"label": e.label, "value": e.value} for e in f.evidence][:6]}
                for f in findings[:500]]
        return {"available": True, "findings": rows,
                "summary": {"available": True, "count": len(findings)}}

    def collection_check_status(self, job_id: str = "") -> dict[str, Any]:
        """The run's snapshot; with no run in this session, an idle one built
        from the recorded `qaRuns[]`, so the Collection QA screen shows each
        book's last run after a restart."""
        try:
            return self._collection_jobs.status(job_id)
        except CollectionJobError:
            if job_id:
                raise
        self._require_project()
        collection_path = str(self.project.path)
        runs = collection_qa_runs(collection_path)
        siblings = collection_projects(collection_path) or [{
            "path": collection_path, "bookId": self.project.book_id,
            "bookName": self.project.summary.book_name}]
        books = []
        for entry in siblings:
            run = runs.get(str(entry.get("bookId") or "")) or {}
            books.append({
                "bookId": str(entry.get("bookId") or ""), "bookName": str(entry.get("bookName") or ""),
                "path": str(entry.get("path") or ""),
                "state": run.get("state") or "pending", "elapsedSeconds": run.get("elapsedSeconds"),
                "jobId": run.get("jobId"), "findingsByCategory": run.get("findingsByCategory") or {},
                "checkedVerses": int(run.get("checkedVerses") or 0), "error": None,
                "completedAt": run.get("completedAt"),
            })
        return {"jobId": "", "state": "idle", "paused": False, "collectionPath": collection_path,
                "checks": [], "totalBooks": len(books),
                "completedBooks": sum(1 for b in books if b["state"] == "done"),
                "percent": 0, "currentBook": None, "books": books, "finalStage": None,
                "elapsedSeconds": 0, "estimatedRemainingSeconds": None, "error": None,
                "createdAt": None, "finishedAt": None}

    def _on_check_job_complete(self, project: TranslationCoreProject, job: Any) -> None:
        """Rebuilds the progress rollup's entries for exactly the chapters
        this job covered — chapters it didn't touch are left alone. Only a
        succeeded job (no failed verses) updates anything; a failed/cancelled
        job must not claim a chapter is AI-checked when it isn't. Best-effort,
        same reasoning as _apply_decision_to_progress: never let this surface
        as a check-job failure to the UI. Runs while the job is "finalizing";
        `job.outcome` is how it ended (#235)."""
        if (getattr(job, "outcome", None) or job.state) != "succeeded":
            return
        try:
            by_chapter: dict[str, dict[str, dict[str, str]]] = {}
            # The same findings twice over: id -> status for the rollup the
            # dashboard reads, and the findings themselves for the project QA
            # report (qa_report.py), which has nothing else to read them from.
            snapshot_by_chapter: dict[str, dict[str, list[dict[str, Any]]]] = {}
            # The Language QA stage's findings, open and decided, for the
            # reports (qa_report, reporting, the exception queue).
            language_qa_by_chapter: dict[str, dict[str, list[dict[str, Any]]]] = {}
            for result in job.results.values():
                chapter = result.get("chapter")
                verse = result.get("verse")
                if chapter is None or verse is None or chapter not in job.spec.chapters:
                    continue
                findings = [
                    f for f in (result.get("findings") or []) if isinstance(f, dict) and f.get("id")
                ]
                statuses = {
                    str(f["id"]): str(f.get("status", FindingStatus.OPEN.value)) for f in findings
                }
                # Language QA (Phase 4.1): an open finding counts as open; one a
                # decision hides counts with that decision, so ignoring it moves
                # the verse's open count down like any other decision.
                language_qa = result.get("languageQa") or {}
                for finding in language_qa.get("findings") or []:
                    if isinstance(finding, dict) and finding.get("id"):
                        statuses[str(finding["id"])] = FindingStatus.OPEN.value
                for finding_id, decision in (language_qa.get("decided") or {}).items():
                    statuses[str(finding_id)] = str(decision)
                by_chapter.setdefault(str(chapter), {})[str(verse)] = statuses
                snapshot_by_chapter.setdefault(str(chapter), {})[str(verse)] = findings
                if LANGUAGE_QA_CHECK in job.spec.checks:
                    language_qa_by_chapter.setdefault(str(chapter), {})[str(verse)] = [
                        *(language_qa.get("findings") or []), *(language_qa.get("hidden") or [])]

            now = project.timestamp_iso()
            project.replace_progress_chapters({
                chapter: {
                    "verseCount": len(project.verses(chapter)),
                    "aiChecked": True,
                    "aiCheckedAt": now,
                    "verses": {v: {"findings": f} for v, f in verses_map.items()},
                }
                for chapter, verses_map in by_chapter.items()
            })
            rollup = project.load_progress_rollup()
            self._recompute_progress_totals(project, rollup)
            project.save_progress_totals(rollup["totals"])
            # Written after the rollup so a crash between the two leaves the
            # rollup -- what the dashboard reads -- intact.
            for chapter, verses_map in snapshot_by_chapter.items():
                project.save_check_findings_snapshot(
                    chapter, verses_map,
                    language_qa=language_qa_by_chapter.get(chapter, {}) if LANGUAGE_QA_CHECK in job.spec.checks else None)
        except Exception:
            pass

    def check_job_status(self, job_id: str = "", since: Optional[int] = None) -> dict[str, Any]:
        """checks.status. `since`: only the verses finished after that many
        (the previous snapshot's `resultsCursor`); omitted, every verse (#229)."""
        snapshot = self._check_jobs.status(job_id, since)
        if LANGUAGE_QA_CHECK in snapshot.get("checks", []):
            # The main progress bar's view of the Language QA stage; the panel
            # keeps languageQa.status for its book-level lists.
            summary = self._language_qa.status(limit=0)
            snapshot["languageQa"] = {
                "state": summary.get("state"),
                "completedChapters": summary.get("completedChapters", 0),
                "totalChapters": summary.get("totalChapters", 0),
                "findings": summary.get("totalFindings", 0),
                "limitations": list(summary.get("limitations") or [])[:20],
            }
        return snapshot

    def cancel_check_job(self, job_id: str = "") -> dict[str, Any]:
        return self._check_jobs.cancel(job_id)

    def retry_check_job(self, job_id: str) -> dict[str, Any]:
        self._require_project()
        spec = self._check_jobs.spec_for_retry(job_id)
        if str(self.project.path) != spec.project_path:
            raise CheckJobConflict("The project changed; start a new check job instead.")
        return self._start_check_job_from_spec(spec, self.project)

    def decide_verse(self, chapter: str, verse: str, finding_id: str,
                      status: str, comment: str = "",
                      issue: dict[str, Any] | None = None) -> dict[str, Any]:
        """Records a human decision (accept/reject/ignore/needs_discussion)
        on a specific finding. Uses tc_ai_bridge's existing QA-decision
        store (companion_dir()/qaDecisions/...) rather than reinventing
        persistence — this already exists, is atomic, and is audited.

        `issue` is what the caller knows about the finding, stored as the
        decision's payload. A Language QA finding (`issue.source ==
        "languageQa"`) counts in the review-progress rollup once a check job
        has put it there (the Language QA stage, Phase 4.1), exactly like a
        Greek Room finding. Until then its decision is recorded and audited
        but not counted: a decision alone must not add a finding row, or a
        verse whose Language QA findings were never checked could look
        reviewed. The origin comes only from `issue`, never from the finding
        id."""
        self._require_project()
        # A caller that names no source gets "unspecified": that records that
        # nobody said, and it is what lets Language QA tell a new non-Language-QA
        # decision from a legacy row with no source key at all
        # (language_qa_jobs._may_concern_language_qa).
        issue = {"source": UNSPECIFIED_DECISION_SOURCE, **(issue or {})}
        path = self.project.record_qa_decision(
            chapter, verse, issue_key=finding_id, decision=status, note=comment, issue=issue,
        )
        if issue["source"] != LANGUAGE_QA_SOURCE or self._rollup_has_finding(chapter, verse, finding_id):
            self._apply_decision_to_progress(self.project, chapter, verse, finding_id, status)
        # Language QA reads this same decision store back inside its own scan
        # loop (language_qa_jobs.py) to suppress a decided terminology
        # finding, but nothing else about recording a decision touches its
        # in-memory summary -- unlike an edit, there is no text change for it
        # to notice on its own. Without this, an "ignored" decision on a
        # Language QA finding would never actually take visible effect until
        # some unrelated trigger (an edit elsewhere, a reopen) happened to
        # force a rescan. Cheap and safe to call unconditionally, same as
        # edit_verse already does below -- invalidate() is a no-op if
        # Language QA isn't bound to a project, and debounces if several
        # decisions land in a burst.
        learned = self._learn_house_style(chapter, verse, finding_id, status, issue)
        self._language_qa.invalidate(chapter)
        result = {"chapter": chapter, "verse": verse, "findingId": finding_id,
                  "status": status, "recordedAt": str(path)}
        if learned is not None:
            result["houseStyle"] = {"learned": learned}
        return result

    # -- house style (layered-rules 6.2-6.4) ---------------------------------

    def _learn_house_style(self, chapter: str, verse: str, finding_id: str, status: str,
                           issue: dict[str, Any]) -> Optional[dict[str, Any]]:
        """The learner's one incremental step (HouseStyleLearner.observe):
        only the (rule, word) pair this decision touched is recomputed. A
        pair that reaches the threshold becomes a learned word-in-book entry
        at once, reported back so the UI can offer Undo. Best-effort: a
        learning failure never fails the decision itself."""
        if issue.get("source") != LANGUAGE_QA_SOURCE:
            return None
        try:
            row = {"chapter": str(chapter), "verse": str(verse), "issueKey": finding_id, "decision": status,
                   "issue": issue, "modifiedTimestamp": datetime.now(timezone.utc).isoformat()}
            entry = self._housestyle_learner.observe(
                str(self.project.path), self.project.project_qa_decisions, row, self.project.housestyle_entries())
            if entry is None:
                return None
            return self.project.record_housestyle_entry(
                entry, username=self.settings.reviewer_name or "Bridge Reviewer")
        except Exception:
            return None

    def language_qa_checker_settings(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        """languageQa.checkerSettings.get | .set: the indic-qa checker's own
        settings (the web app's Settings dialog), per collection
        (language_qa_checker_settings.py, DECISIONS 2026-10-08). A save is
        written into every materialized book's overrides file, then the open
        book is rebound so a pass runs with them."""
        self._require_project()
        name = project_pack_name(self.project, self._language_qa)
        pack, _notes = project_rule_pack(self.project.path, name) if name else (None, [])
        if pack is None or indic_qa_adapter.indic_config(pack.meta) is None:
            return {"pack": None, "available": False, "reason": "This project's language has no indic-qa checker."}
        books = sorted({self.project.book_id, *(book for book, _path in self.project.collection_sibling_paths())})
        if action == "set":
            patch = {key: params[key] for key in params if key != "projectPath"}
            try:
                checker_settings_rpc.save(pack, patch, [Path(self.project.path), *(
                    path for _book, path in self.project.collection_sibling_paths())])
            except checker_settings_rpc.CheckerSettingsError as exc:
                raise ProjectError(str(exc)) from exc
            self._language_qa.bind(self.project)
            pack, _notes = project_rule_pack(self.project.path, name)
        counts = self._language_qa.rule_counts()
        return checker_settings_rpc.describe(pack, counts=counts, books=books)

    def _collection_book_projects(self) -> list[TranslationCoreProject]:
        """The open book and every materialized sibling (a lazy one has no
        workbench yet). A project-scope house-style entry is written to each."""
        self._require_project()
        projects = [self.project]
        for entry in collection_projects(str(self.project.path)):
            path = Path(str(entry.get("path") or ""))
            if entry.get("lazy") or not path.is_dir() or path.resolve() == Path(self.project.path).resolve():
                continue
            try:
                projects.append(TranslationCoreProject(path, workspace=self.workspace))
            except Exception:
                continue
        return projects

    def housestyle_list(self) -> dict[str, Any]:
        self._require_project()
        books = []
        for project in self._collection_book_projects():
            try:
                books.append((project.book_id, project.housestyle_entries(), project.project_qa_decisions()))
            except Exception:
                continue
        own = self.project.housestyle_entries()
        own_keys = {entry.get("key") for entry in own}
        pack = project_pack_name(self.project, self._language_qa)
        return {"entries": own, "proposals": project_proposals(books),
                # The project pack's bundled seed, read-only; an own entry with its key replaces it.
                "seed": [entry for entry in (bundled_seed(pack) if pack else []) if entry["key"] not in own_keys],
                "thresholds": {"learnIgnores": LEARN_IGNORES, "proposeProjectBooks": PROPOSE_PROJECT_BOOKS,
                               "proposeRuleDecisions": PROPOSE_RULE_DECISIONS,
                               "proposeRuleIgnoreRate": PROPOSE_RULE_IGNORE_RATE, "preferUses": PREFER_USES}}

    def housestyle_name_suggestions(self) -> dict[str, Any]:
        """Candidates for the approved proper-noun list: the names check's
        cached majority spellings (it is not run here), minus those approved."""
        self._require_project()
        approved = frozenset(e.get("word") for e in self.project.housestyle_entries()
                             if e.get("list") == "properNouns" and e.get("state") == "active")
        cached = (self.project.load_check_cache().get("names") or {}).get("findings") or []
        return {"suggestions": name_suggestions(cached, approved), "checked": bool(cached)}

    def housestyle_record(self, entry: dict[str, Any]) -> dict[str, Any]:
        """Record an entry: explicit (a scoped Ignore), curated (Settings), or
        an accepted/dismissed proposal. A project scope is written to every
        materialized book of the collection."""
        self._require_project()
        username = self.settings.reviewer_name or "Bridge Reviewer"
        targets = (self._collection_book_projects() if str(entry.get("scope", "")).endswith("project")
                   else [self.project])
        recorded = [project.record_housestyle_entry(entry, username=username) for project in targets]
        self._language_qa.invalidate_all()
        return {"entry": recorded[0], "books": [p.book_id for p in targets], **self.housestyle_list()}

    def housestyle_set_state(self, key: str, state: str) -> dict[str, Any]:
        """Remove, Undo, or confirm (an imported entry): a new state on the
        same row, never a delete."""
        self._require_project()
        current = next((e for e in self.project.housestyle_entries() if e.get("key") == key), None)
        if current is None:
            raise ProjectError(f"No house-style entry {key!r}.")
        entry = {**current, "state": state}
        if state == "active":
            entry["imported"] = False  # a local decision confirms an imported entry
        return self.housestyle_record(entry)

    def housestyle_export(self, output_path: str) -> dict[str, Any]:
        self._require_project()
        entries = [e for e in self.project.housestyle_entries() if e.get("state") == "active"]
        Path(output_path).write_text(json.dumps({"schemaVersion": 1, "bookId": self.project.book_id,
                                                 "entries": entries}, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
        return {"written": True, "path": output_path, "count": len(entries)}

    def housestyle_import(self, input_path: str) -> dict[str, Any]:
        """Carry a book's learned style into this one. Imported entries keep
        their provenance and evidence, and show as imported until confirmed."""
        self._require_project()
        try:
            data = json.loads(Path(input_path).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise ProjectError(f"Cannot read house style from {input_path}: {exc}") from exc
        count = 0
        username = self.settings.reviewer_name or "Bridge Reviewer"
        existing = {e.get("key") for e in self.project.housestyle_entries()}
        for entry in data.get("entries") or []:
            if not isinstance(entry, dict) or entry.get("key") in existing:
                continue
            self.project.record_housestyle_entry({**entry, "imported": True, "state": "active"}, username=username)
            count += 1
        self._language_qa.invalidate_all()
        return {"imported": count, **self.housestyle_list()}

    def _rollup_has_finding(self, chapter: str, verse: str, finding_id: str) -> bool:
        try:
            return self.project.progress_finding_status(chapter, verse, finding_id) is not None
        except Exception:
            return False

    def _apply_decision_to_progress(
        self, project: TranslationCoreProject, chapter: str, verse: str,
        finding_id: str, status: str,
    ) -> None:
        """Incrementally updates the book's progress rollup for one decision
        — one finding row and the totals row, never a rescan of the decision
        store. Best-effort: a rollup bookkeeping failure must never surface as
        a decide_verse failure, since the actual decision is already safely
        recorded via record_qa_decision above."""
        try:
            chapter_key = str(chapter)
            project.record_progress_decision(
                chapter_key, verse, finding_id, status,
                verse_count=len(project.verses(chapter_key)) if chapter_key in project.chapters() else 0,
            )
            rollup = project.load_progress_rollup()
            self._recompute_progress_totals(project, rollup)
            project.save_progress_totals(rollup["totals"])
        except Exception:
            pass

    @staticmethod
    def _recompute_progress_totals(project: TranslationCoreProject, rollup: dict[str, Any]) -> None:
        chapters_dict = rollup.get("chapters", {})
        all_chapters = project.chapters()
        checked_chapter_count = 0
        checked_verse_count = 0
        reviewed_verse_count = 0
        finding_count = 0
        approved_finding_count = 0
        for chapter_entry in chapters_dict.values():
            if chapter_entry.get("aiChecked"):
                checked_chapter_count += 1
                checked_verse_count += int(chapter_entry.get("verseCount") or 0)
            for verse_entry in chapter_entry.get("verses", {}).values():
                findings = verse_entry.get("findings", {})
                finding_count += len(findings)
                verse_reviewed = bool(findings)
                for status in findings.values():
                    if status == FindingStatus.OPEN.value:
                        verse_reviewed = False
                    else:
                        approved_finding_count += 1
                if verse_reviewed:
                    reviewed_verse_count += 1
        rollup["totals"] = {
            "chapterCount": len(all_chapters),
            "checkedChapterCount": checked_chapter_count,
            "verseCount": sum(len(project.verses(ch)) for ch in all_chapters),
            "checkedVerseCount": checked_verse_count,
            "reviewedVerseCount": reviewed_verse_count,
            "findingCount": finding_count,
            "approvedFindingCount": approved_finding_count,
        }

    def edit_verse(self, chapter: str, verse: str, new_text: str, **strict_options: Any) -> dict[str, Any]:
        """Human-authorized scripture edit.

        tc_project.TranslationCoreProject.apply_scripture_edit() already
        implements the real, tC-compatible write: updates the target chapter
        JSON, reconciles alignment (keeps bottomWords tokens that still
        exist in the new text by word/occurrence signature, moves the rest
        to wordBank), marks word alignment invalid (surfaced by local_checks'
        existing WA_INVALID check), flags touched tN/tW index entries
        verseEdits=True, and runs it all through its own TransactionJournal
        transaction with rollback on failure — undoable and crash-safe.
        Nothing here reinvents that; it only calls it.

        Also invalidates the in-memory whole-book consistency cache (see
        _consistency_findings_for_book's own docstring) for THIS book only
        — cheap, in-memory corpus-stats arithmetic, same cost as the
        existing invalidation every alignment mutation already does in
        _finish_alignment_mutation.

        Deliberately does NOT touch the USFM/names caches, even though a
        finding whose text this edit just fixed (e.g. a
        names.spelling_similarity correction) will keep reappearing until
        the project is reopened. Tried clearing them here too and reverted
        it the same session: saveVerseEdit's post-save runVerseChecks(
        ["local", "greekroom"]) calls straight into
        _usfm_findings_for_book/_names_findings_for_book on every edit
        (see run_verse_checks), and those functions' own docstrings say
        why they're never invalidated by verse.edit — a real rescan means
        the isolated USFM checker subprocess (a 120-second hard timeout on
        its own) and a full whole-book names/vocabulary scan. Popping the
        cache here made that recheck pay for both, synchronously, inside
        the single-threaded stdio dispatcher and while holding
        _checker_lock — so a save could block the sidecar long enough that
        an unrelated project.report queued behind it timed out client-side
        (same failure mode as issue #24, just via a different call path).
        Accepted as a known limitation, same tradeoff as the two functions
        it depends on."""
        self._require_project()
        result = self.project.apply_scripture_edit(
            chapter, verse, new_text,
            username=self.settings.reviewer_name or "Bridge Reviewer",
            **strict_options,
        )
        saved = str(result.get("newText", new_text))
        learned = self._learn_from_edit(chapter, verse, str(result.get("oldText") or ""), saved)
        self._language_qa.invalidate(chapter)
        self._consistency_findings_by_book.pop(str(self.project.path), None)
        resolutions = self.project.list_issue_resolutions(chapter, verse)
        display = verse_display(saved)
        return {
            "committed": True, "chapter": chapter, "verse": verse,
            "issueResolutionsNeedingRecheck": sum(
                1 for item in resolutions
                if str((item.get("recheck") or {}).get("status") or "") == "stale"
            ),
            # The saved text's display payload, so the reader refreshes from
            # the response instead of parsing the new string itself (#91).
            "display": display,
            # Marker problems in the saved text (a raw USFM edit can unbalance
            # them). Never blocking: the save already happened, and Language QA
            # will not scan such a verse until it is fixed.
            "warnings": list(display.get("warnings") or []),
            **({"learned": learned} if learned else {}),
            **result,
        }

    def _learn_from_edit(self, chapter: str, verse: str, old_text: str, new_text: str) -> dict[str, Any] | None:
        """A learned fix from a saved edit that replaced exactly one word
        (language_qa_learned.learned_pair). Typing a learned replacement back to
        the original word retracts that fix instead of learning the reverse.
        Shared across the collection (DECISIONS 2026-10-07): the reverse is
        looked for in every book's fixes, and a retraction counts against the
        collection's total. Never fails the edit, which is already saved."""
        if not self.settings.language_qa_learned_fixes or not old_text:
            return None
        pair = learned_pair(old_text, new_text)
        if pair is None:
            return None
        old, new = pair
        ref = f"{self.project.book_id.upper()} {chapter}:{verse}"
        try:
            reverse = self._collection_learned_fix(new, old)
            if reverse and reverse.get("enabled") and int(reverse.get("count") or 0) > 0:
                self.project.retract_learned_fix(new, old, ref=ref, floor=reverse["own"] - reverse["count"],
                                                 reviewer=self.settings.reviewer_name or "Bridge Reviewer")
                return {"old": new, "new": old, "action": "retracted"}
            fix = self.project.record_learned_fix(old, new, ref=ref, source="edit",
                                                  reviewer=self.settings.reviewer_name or "Bridge Reviewer")
        except Exception as exc:  # the edit is saved; a learning failure is only reported
            return {"old": old, "new": new, "action": "failed", "error": str(exc)}
        return {"old": old, "new": new, "action": "learned", "count": fix["count"], "enabled": fix["enabled"]}

    def _collection_learned_fix(self, old: str, new: str) -> dict[str, Any] | None:
        key = (unicodedata.normalize("NFC", old), unicodedata.normalize("NFC", new))
        return next((f for f in self.project.collection_learned_fixes() if (f["old"], f["new"]) == key), None)

    def language_qa_learned(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        """languageQa.learned.list | .forget | .restore, over the collection's
        fixes (shared, DECISIONS 2026-10-07). Forget and restore re-write the
        row (never delete it) in this book and in every materialized sibling
        that has the fix, so every book agrees; then a pass runs."""
        self._require_project()
        if action == "list":
            return {"fixes": self.project.collection_learned_fixes(),
                    "enabled": self.settings.language_qa_learned_fixes}
        old, new = params.get("old"), params.get("new")
        if not isinstance(old, str) or not isinstance(new, str) or not old or not new:
            raise ProjectError("old and new must be non-empty strings")
        if self._collection_learned_fix(old, new) is None:
            raise ProjectError(f"no learned fix {old} -> {new}")
        enabled = action == "restore"
        key = (unicodedata.normalize("NFC", old), unicodedata.normalize("NFC", new))
        self.project.set_learned_fix_enabled(old, new, enabled, create=True)
        failed: list[str] = []
        for book, path in self.project.collection_sibling_paths():
            if not any((row.get("old"), row.get("new")) == key for row in peek_learned_fixes(path)):
                continue
            try:
                TranslationCoreProject(path, workspace=self.workspace).set_learned_fix_enabled(old, new, enabled)
            except Exception as exc:  # reported: the other books are already written
                failed.append(f"{book.upper()}: {exc}")
        self._language_qa.invalidate_all()
        return {"fix": self._collection_learned_fix(old, new), "failedBooks": failed}

    # -- project words and book words (indic-qa's "Add to dictionary") --------
    #
    # A project word is a house-style list entry (list "projectWords"): it
    # silences spelling-type findings on that word and is never written into a
    # dictionary file (NOTICE contract 2). Book words come from the last pass.

    WORDS_MAX = 500

    def language_qa_words(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        """languageQa.words.add | .list."""
        self._require_project()
        if action == "list":
            entries = [e for e in self.project.housestyle_entries()
                       if e.get("list") == "projectWords" and e.get("state", "active") == "active"]
            return {"added": entries}
        words, scope = params.get("words"), params.get("scope", "book")
        if (not isinstance(words, list) or not words or len(words) > self.WORDS_MAX
                or not all(isinstance(w, str) and w.strip() and not any(c.isspace() for c in w.strip()) for w in words)):
            raise ProjectError(f"words must be a list of 1 to {self.WORDS_MAX} single words")
        if scope not in {"book", "project"}:
            raise ProjectError("scope must be book or project")
        entries = []
        for word in dict.fromkeys(unicodedata.normalize("NFC", w.strip()) for w in words):
            result = self.housestyle_record({"scope": f"word-in-{scope}", "list": "projectWords", "word": word,
                                             "provenance": "explicit"})
            entries.append(result.get("entry"))
        return {"entries": entries, "count": len(entries)}

    def language_qa_book_words(self, params: dict[str, Any]) -> dict[str, Any]:
        min_count, limit = params.get("minCount", 1), params.get("limit", 500)
        if not isinstance(min_count, int) or min_count < 1 or not isinstance(limit, int) or not 1 <= limit <= 5000:
            raise ProjectError("minCount must be a positive integer and limit 1-5000")
        return self._language_qa.book_words(min_count=min_count, limit=limit)

    OCCURRENCE_VERSE_CAP = 5000

    def language_qa_occurrences(self, params: dict[str, Any]) -> dict[str, Any]:
        """Every place a word occurs in this book's verse text (source "irv"),
        with raw offsets and a snippet of the text a reader sees. Same tokenizer
        as Language QA, so "a word" means what it means everywhere else.
        `match: "text"` is the word menu's "Search in this book": any run of
        the visible text equal to `word` (a phrase, part of a word), not only a
        whole word."""
        self._require_project()
        word, source, limit = params.get("word"), params.get("source", "irv"), params.get("limit", 50)
        match = params.get("match", "word")
        if not isinstance(word, str) or not word.strip():
            raise ProjectError("word must be a non-empty string")
        if not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ProjectError("limit must be an integer from 1 to 1000")
        if match not in {"word", "text"}:
            raise ProjectError("match must be word or text")
        if source == "ov" and match == "word":
            return self._reference_occurrences(word, limit)
        if source != "irv":
            raise ProjectError("source must be irv (or ov, for a word)")
        wanted = unicodedata.normalize("NFC", word.strip())
        hits, total, scanned = [], 0, 0
        for chapter in self.project.chapters():
            for verse, raw in (self.project.target_chapter(chapter) or {}).items():
                if not isinstance(raw, str) or verse == "front":
                    continue
                scanned += 1
                if scanned > self.OCCURRENCE_VERSE_CAP:
                    break
                lifted, _ = language_qa_lift(raw)
                if lifted is None:
                    continue
                if match == "text":
                    found, at = [], lifted.visible.find(wanted)
                    while at >= 0:
                        found.append((wanted, at, at + len(wanted)))
                        at = lifted.visible.find(wanted, at + len(wanted))
                else:
                    found = language_qa_word_occurrences(lifted.visible)
                for token, start, end in found:
                    if token != wanted:
                        continue
                    total += 1
                    span = lifted.raw_span(start, end)
                    if len(hits) < limit and span is not None:
                        before = lifted.visible[max(0, start - 40):start]
                        hits.append({"book": self.project.book_id, "chapter": str(chapter), "verse": str(verse),
                                     "start": span[0], "end": span[1],
                                     "snippet": before + lifted.visible[start:end + 40],
                                     "snippetStart": len(before), "snippetEnd": len(before) + end - start})
        return {"word": wanted, "source": "irv", "match": match, "ready": True, "total": total,
                "truncated": total > len(hits), "hits": hits}

    def _reference_occurrences(self, word: str, limit: int) -> dict[str, Any]:
        """The reference text's occurrences of a word (OV occurrences)."""
        holder = self._reference_holder()
        if holder is None:
            raise ProjectError("No reference text is configured for this book's language (Settings > Language QA).")
        text, error = holder.state()
        if error:
            raise ProjectError(f"The reference text could not be read: {error}")
        if text is None:
            return {"word": word, "source": "ov", "ready": False, "total": 0, "truncated": False, "hits": []}
        hits, total = text.find(word, limit)
        return {"word": unicodedata.normalize("NFC", word.strip()), "source": "ov", "ready": True, "total": total,
                "truncated": total > len(hits), "hits": hits}

    # -- reference text and related words (indic-qa's OV panel) --------------

    def _reference_holder(self) -> Any:
        """The reference Bible for the open book's Language QA pack: the folder
        set in Settings, else the pack's own dictionary/verses.tsv when it ships
        one (ta-irv: the 1957 OV). None when there is neither."""
        pack = self._language_qa.pack_name()
        if not pack:
            return None
        folder = self.settings.language_qa_reference_dirs.get(pack)
        label = "Old Version"
        if not folder:
            bundled = packs_dir() / pack / "dictionary"
            if not (bundled / "verses.tsv").is_file():
                return None
            folder, label = str(bundled), "Old Version (bundled)"
        key = (pack, str(Path(folder).resolve()))
        holder = self._reference_holders.get(key)
        if holder is None:
            holder = ReferenceHolder(folder, label, self.settings.path.parent / "reference-cache")
            self._reference_holders[key] = holder
        return holder

    def language_qa_reference(self, params: dict[str, Any]) -> dict[str, Any]:
        """The reference text of one chapter of the open book, for the panel
        beside the text. Never waits: until the folder is read, `ready` is false."""
        self._require_project()
        chapter = params.get("chapter")
        if not isinstance(chapter, str) or not chapter:
            raise ProjectError("chapter must be a non-empty string")
        holder = self._reference_holder()
        if holder is None:
            return {"ready": False, "configured": False, "source": None, "verses": [],
                    "pack": self._language_qa.pack_name()}
        text, error = holder.state()
        source = {"path": str(holder.folder), "label": holder.label}
        if error:
            return {"ready": False, "configured": True, "source": source, "verses": [], "error": error}
        if text is None:
            return {"ready": False, "configured": True, "source": source, "verses": []}
        return {"ready": True, "configured": True, "source": {**source, "kind": text.kind},
                "verses": text.chapter(self.project.book_id, chapter)}

    def language_qa_related(self, params: dict[str, Any]) -> dict[str, Any]:
        """Related words for one word (OV/IRV equivalents and same-stem forms),
        from the reference text and the collection's opened books. Built once on a
        background thread; until then `ready` is false."""
        self._require_project()
        word = params.get("word")
        if not isinstance(word, str) or not word.strip():
            raise ProjectError("word must be a non-empty string")
        word = unicodedata.normalize("NFC", word.strip())
        if not self.settings.language_qa_related_words:
            return {"word": word, "ready": False, "off": True, "equivalents": [], "family": []}
        holder = self._reference_holder()
        if holder is None:
            return {"word": word, "ready": False, "configured": False, "equivalents": [], "family": []}
        text, error = holder.state()
        if error or text is None:
            return {"word": word, "ready": False, "error": error, "equivalents": [], "family": []}
        projects = self._collection_book_projects()
        key = (str(holder.folder), tuple(sorted(str(p.path) for p in projects)))
        with self._related_lock:
            entry = self._related.get(key)
            if entry is None:
                entry = {"index": None, "error": ""}
                self._related[key] = entry
                # The projects are opened here, on the request thread; the
                # builder only reads their chapter files.
                threading.Thread(target=self._build_related, args=(entry, text, projects), name="related-words",
                                 daemon=True).start()
        if entry["error"]:
            return {"word": word, "ready": False, "error": entry["error"], "equivalents": [], "family": []}
        if entry["index"] is None:
            return {"word": word, "ready": False, "equivalents": [], "family": []}
        return entry["index"].lookup(word)

    def _build_related(self, entry: dict[str, Any], text: Any, projects: list[Any]) -> None:
        try:
            ov = [(f"{book} {chapter}:{verse}", [t for t, _, _ in language_qa_word_occurrences(t_)])
                  for book, chapter, verse, t_ in text.references()]
            irv = []
            for project in projects:
                book = str(project.book_id).upper()
                for chapter in project.chapters():
                    for verse, raw in (project.target_chapter(chapter) or {}).items():
                        if not isinstance(raw, str) or verse == "front":
                            continue
                        lifted, _ = language_qa_lift(raw)
                        if lifted is not None:
                            irv.append((f"{book} {chapter}:{verse}",
                                        [t for t, _, _ in language_qa_word_occurrences(lifted.visible)]))
            index = RelatedWords.build(ov, irv)
            with self._related_lock:
                entry["index"] = index
        except Exception as exc:  # a failed build is reported, never a failed request
            with self._related_lock:
                entry["error"] = f"{type(exc).__name__}: {exc}"

    # -- flags (indic-qa's reviewer flags) -----------------------------------
    #
    # A reviewer's question on a passage: never a Scripture write, never a
    # finding. Stored in the workbench (v6, language_qa_flags); delete is a
    # status so change_log keeps every image.

    FLAG_TYPES = ("spelling", "grammar", "meaning", "style", "encoding", "font", "other")
    FLAG_STATUSES = ("open", "resolved", "deleted")
    FLAG_NOTE_MAX = 4000

    def language_qa_flags(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        """languageQa.flags.list | .add | .update | .delete."""
        self._require_project()
        if action == "list":
            chapter, status = params.get("chapter"), params.get("status")
            if chapter is not None and not isinstance(chapter, str):
                raise ProjectError("chapter must be a string when given")
            if status is not None and status not in self.FLAG_STATUSES:
                raise ProjectError(f"status must be one of {list(self.FLAG_STATUSES)} when given")
            return {"flags": self.project.language_qa_flags(chapter=chapter, status=status)}
        if action == "add":
            flag = self._valid_flag(params.get("flag"))
            reviewer = self.settings.reviewer_name or "Bridge Reviewer"
            record = self.project.record_language_qa_flag(flag, reviewer=reviewer)
            learned = None
            suggested = flag.get("suggested")
            if suggested and self.settings.language_qa_learned_fixes:
                one = lambda text: len(language_qa_word_occurrences(text)) == 1  # noqa: E731
                if one(flag["text"]) and one(suggested):
                    old = unicodedata.normalize("NFC", flag["text"].strip())
                    fix = self.project.record_learned_fix(
                        old, suggested.strip(), ref=f"{self.project.book_id.upper()} {flag['chapter']}:{flag['verse']}",
                        reviewer=reviewer, source="flag")
                    learned = {"old": old, "new": suggested.strip(), "count": fix["count"]}
                    self._language_qa.invalidate(flag["chapter"])
            return {"flag": record, **({"learned": learned} if learned else {})}
        flag_id = params.get("flagId")
        if not isinstance(flag_id, str) or not flag_id:
            raise ProjectError("flagId must be a non-empty string")
        if action == "delete":
            patch: dict[str, Any] = {"status": "deleted"}
        else:
            raw_patch = params.get("patch")
            if not isinstance(raw_patch, dict) or not raw_patch:
                raise ProjectError("patch must be a non-empty object")
            allowed = {"status", "note", "type", "suggested"}
            if set(raw_patch) - allowed:
                raise ProjectError(f"patch may change only {sorted(allowed)}")
            patch = dict(raw_patch)
            if "status" in patch and patch["status"] not in self.FLAG_STATUSES:
                raise ProjectError(f"status must be one of {list(self.FLAG_STATUSES)}")
            if "type" in patch and patch["type"] not in self.FLAG_TYPES:
                raise ProjectError(f"type must be one of {list(self.FLAG_TYPES)}")
            if "note" in patch and (not isinstance(patch["note"], str) or len(patch["note"]) > self.FLAG_NOTE_MAX):
                raise ProjectError(f"note must be a string of at most {self.FLAG_NOTE_MAX} characters")
            if "suggested" in patch and patch["suggested"] is not None and not isinstance(patch["suggested"], str):
                raise ProjectError("suggested must be a string or null")
        try:
            record = self.project.update_language_qa_flag(flag_id, patch)
        except KeyError as exc:
            raise ProjectError(f"No flag {flag_id} in this book.") from exc
        return {"flag": record}

    def _valid_flag(self, flag: Any) -> dict[str, Any]:
        """A new flag's fields, checked against the verse as it is now: the
        span must be inside the verse and `text` must be exactly what is there
        (raw code points), so a flag always points at real text."""
        if not isinstance(flag, dict):
            raise ProjectError("flag must be an object")
        chapter, verse = flag.get("chapter"), flag.get("verse")
        if not isinstance(chapter, str) or not isinstance(verse, str) or not chapter or not verse:
            raise ProjectError("flag.chapter and flag.verse must be non-empty strings")
        current = self.project.target_verse_text(chapter, verse)
        if current is None or current == "":
            raise ProjectError(f"There is no verse {chapter}:{verse} in this book.")
        start, end = flag.get("start"), flag.get("end")
        if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(current):
            raise ProjectError("flag.start and flag.end must mark a non-empty span inside the verse")
        text = flag.get("text")
        if text != current[start:end]:
            raise ProjectError("flag.text is not the verse text at that span; reload the verse and try again")
        flag_type = flag.get("type")
        if flag_type not in self.FLAG_TYPES:
            raise ProjectError(f"flag.type must be one of {list(self.FLAG_TYPES)}")
        note = flag.get("note", "")
        if not isinstance(note, str) or len(note) > self.FLAG_NOTE_MAX:
            raise ProjectError(f"flag.note must be a string of at most {self.FLAG_NOTE_MAX} characters")
        suggested = flag.get("suggested")
        if suggested is not None and (not isinstance(suggested, str) or not suggested.strip()):
            raise ProjectError("flag.suggested must be a non-empty string when given")
        verse_end = flag.get("verseEnd")
        if verse_end is not None and (not isinstance(verse_end, str) or not verse_end):
            raise ProjectError("flag.verseEnd must be a non-empty string when given")
        return {"chapter": chapter, "verse": verse, "verseEnd": verse_end, "start": start, "end": end,
                "text": text, "textHash": language_qa_text_hash(current), "type": flag_type, "note": note,
                "suggested": suggested.strip() if suggested else None,
                "findingId": flag.get("findingId") if isinstance(flag.get("findingId"), str) else None}

    # -- scoped corrections (indic-qa's "Here / Chapter / Book") -------------
    #
    # Scripture is still written only by apply_scripture_edit, once per verse,
    # each an ordinary journalled edit with its own verseEdits record. A batch
    # row (workbench v6) only groups them, so they can be listed and undone
    # together. Never across books: a collection's siblings are other projects.

    def _scoped(self, params: dict[str, Any]) -> tuple[dict[str, Any], list[Any], str, str | None]:
        chapter, verse, finding_id = params.get("chapter"), params.get("verse"), params.get("findingId")
        if not all(isinstance(v, str) and v for v in (chapter, verse, finding_id)):
            raise ProjectError("chapter, verse and findingId must be non-empty strings")
        scope = params.get("scope")
        if scope not in language_qa_scope.SCOPES:
            raise ProjectError(f"scope must be one of {list(language_qa_scope.SCOPES)}")
        suggestion = params.get("suggestion")
        if suggestion is not None and (not isinstance(suggestion, str) or not suggestion):
            raise ProjectError("suggestion must be a non-empty string when given")
        try:
            origin, shown = self._language_qa.scope_findings(chapter, verse, finding_id)
            found = language_qa_scope.occurrences(shown, origin, scope, suggestion)
        except ValueError as exc:
            raise ProjectError(str(exc)) from exc
        wanted = params.get("occurrences")
        if wanted is not None:
            if not isinstance(wanted, list) or not all(isinstance(i, str) for i in wanted):
                raise ProjectError("occurrences must be a list of finding ids when given")
            found = [o for o in found if o.finding_id in set(wanted)]
        return origin, found, scope, suggestion

    def language_qa_scope_find(self, params: dict[str, Any]) -> dict[str, Any]:
        """Where the same finding is, in the verse, chapter or book: what the
        confirmation lists before anything is written."""
        self._require_project()
        origin, found, scope, _suggestion = self._scoped(params)
        return {"chapter": origin["chapter"], "verse": origin["verse"], "findingId": origin["id"], "scope": scope,
                "key": {"ruleId": origin.get("ruleId"), "detailRule": origin.get("detailRule") or "",
                        "originalText": origin.get("originalText"),
                        "kind": "warning" if language_qa_scope.is_warning(origin) else "word"},
                "count": len(found), "verses": len(language_qa_scope.by_verse(found)),
                "occurrences": [o.to_json() for o in found]}

    def _language_qa_issue(self, finding: dict[str, Any], chosen: str | None) -> dict[str, Any]:
        """The decision issue the frontend's languageQaDecisionIssue builds."""
        rank = next((s.get("rank") for s in finding.get("suggestions") or [] if s.get("text") == chosen), None)
        return {"source": LANGUAGE_QA_SOURCE, "rule": finding.get("rule"), "ruleId": finding.get("ruleId"),
                "ruleVersion": finding.get("ruleVersion"), "packVersion": finding.get("packVersion"),
                "ruleRevision": finding.get("ruleRevision"), "layer": finding.get("layer"),
                "category": finding.get("category"), "originalText": finding.get("originalText"),
                "suggestedReplacement": chosen, "chosenSuggestion": chosen, "chosenRank": rank,
                "message": finding.get("message"), "start": finding.get("start"), "end": finding.get("end")}

    def language_qa_scope_apply(self, params: dict[str, Any]) -> dict[str, Any]:
        """Accept (write) or ignore the same finding across the scope.

        accept: per verse, the verse must still be the text the pass saw (its
        hash), else it is skipped and reported; the occurrences are spliced
        right to left and written with one apply_scripture_edit; each finding
        is decided `accepted`. A word replaced by a single word is learned.
        One batch row lists every verse for Undo.
        ignore: each finding is decided `ignored`; at book scope a word is
        recorded as house style (word-in-book), which also covers later text."""
        self._require_project()
        action = params.get("action")
        if action not in {"accept", "ignore"}:
            raise ProjectError("action must be accept or ignore")
        origin, found, scope, suggestion = self._scoped(params)
        if action == "ignore":
            return self._scope_ignore(origin, found, scope)
        writable = [o for o in found if o.new is not None]
        if not writable:
            raise ProjectError("Nothing to change: these findings carry no replacement.")
        batch_id = uuid.uuid4().hex[:16]
        username = self.settings.reviewer_name or "Bridge Reviewer"
        items, changed, skipped, decided_ids = [], [], [], []
        for (chapter, verse), group in language_qa_scope.by_verse(writable).items():
            current = self.project.target_verse_text(chapter, verse) or ""
            if any(o.text_hash != language_qa_text_hash(current) for o in group):
                skipped.append({"chapter": chapter, "verse": verse,
                                "reason": "The verse changed after the check; nothing was written. Run the check again."})
                continue
            try:
                new_text = language_qa_scope.splice(current, group)
            except ValueError as exc:
                skipped.append({"chapter": chapter, "verse": verse, "reason": str(exc)})
                continue
            context = {"reference": {"bookId": self.project.book_id, "chapter": chapter, "verse": verse},
                       "tool": "translationCoreAI", "groupId": "language-qa-scope", "batchId": batch_id}
            result = self.project.apply_scripture_edit(chapter, verse, new_text, username=username,
                                                       tags=["languageQa", "scope"], context_id=context)
            for occurrence in group:
                self.decide_verse(chapter, verse, occurrence.finding_id, "accepted",
                                  issue={**self._language_qa_issue(occurrence.finding, occurrence.new),
                                         "batchId": batch_id})
                decided_ids.append(occurrence.finding_id)
            items.append({"chapter": chapter, "verse": verse, "findingIds": [o.finding_id for o in group],
                          "oldText": current, "newText": new_text,
                          "journalTransactionId": result.get("journalTransactionId")})
            changed.append({"chapter": chapter, "verse": verse, "oldText": current, "newText": new_text,
                            "display": verse_display(new_text), "findingIds": [o.finding_id for o in group]})
        learned = None
        if items and suggestion and not language_qa_scope.is_warning(origin) and self.settings.language_qa_learned_fixes:
            single = lambda text: len(language_qa_word_occurrences(text)) == 1  # noqa: E731
            if single(origin["originalText"]) and single(suggestion):
                old = unicodedata.normalize("NFC", origin["originalText"])
                fix = self.project.record_learned_fix(
                    old, suggestion, ref=f"{self.project.book_id.upper()} {origin['chapter']}:{origin['verse']}",
                    reviewer=username, source="scope", n=len(decided_ids))
                learned = {"old": old, "new": suggestion, "count": fix["count"], "n": len(decided_ids)}
        if items:
            self.project.record_language_qa_batch({
                "batchId": batch_id, "kind": "accept", "state": "applied", "action": "accept", "scope": scope,
                "chapter": origin["chapter"], "verse": origin["verse"], "ruleId": origin.get("ruleId"),
                "detailRule": origin.get("detailRule") or "", "originalText": origin.get("originalText"),
                "suggestion": suggestion, "count": len(decided_ids), "items": items, "learned": learned,
                "username": username, "createdAt": datetime.now(timezone.utc).isoformat(timespec="milliseconds")})
            for chapter in sorted({item["chapter"] for item in items}):
                self._language_qa.invalidate(chapter)
            self._consistency_findings_by_book.pop(str(self.project.path), None)
        return {"batchId": batch_id if items else None, "action": "accept", "scope": scope,
                "count": len(decided_ids), "changed": changed, "skipped": skipped,
                **({"learned": learned} if learned else {})}

    def _scope_ignore(self, origin: dict[str, Any], found: list[Any], scope: str) -> dict[str, Any]:
        decided = []
        for occurrence in found:
            self.decide_verse(occurrence.chapter, occurrence.verse, occurrence.finding_id, "ignored",
                              issue={**self._language_qa_issue(occurrence.finding, None), "scope": scope})
            decided.append(occurrence.finding_id)
        entry = None
        if scope == "book" and not language_qa_scope.is_warning(origin):
            # The book-wide ignore is house style, so later text is covered too.
            entry = self.housestyle_record({
                "scope": "word-in-book", "ruleId": origin.get("ruleId"), "word": origin.get("originalText"),
                "provenance": "explicit",
                "evidence": [{"chapter": origin["chapter"], "verse": origin["verse"], "decisionId": origin["id"]}],
            }).get("entry")
        return {"action": "ignore", "scope": scope, "count": len(decided), "decided": decided,
                **({"houseStyle": entry} if entry else {})}

    def language_qa_batch_undo(self, params: dict[str, Any]) -> dict[str, Any]:
        """Undo a scoped correction: each verse goes back to its text before the
        batch, through apply_scripture_edit, if nobody changed it since (else it
        is reported and left alone). A new `undo` batch is written and the
        original moves to `undone`; neither is deleted. The accepted decisions
        stay; a finding whose text is back simply reappears on the next pass."""
        self._require_project()
        batch_id = params.get("batchId")
        if not isinstance(batch_id, str) or not batch_id:
            raise ProjectError("batchId must be a non-empty string")
        batch = self.project.language_qa_batch(batch_id)
        if batch is None or batch.get("kind") != "accept":
            raise ProjectError(f"No correction batch {batch_id} in this book.")
        if batch.get("state") != "applied":
            raise ProjectError("That batch was already undone.")
        undo_id = uuid.uuid4().hex[:16]
        username = self.settings.reviewer_name or "Bridge Reviewer"
        reverted, conflicts, items = [], [], []
        for item in reversed(batch.get("items") or []):
            chapter, verse = str(item["chapter"]), str(item["verse"])
            current = self.project.target_verse_text(chapter, verse) or ""
            if current != item["newText"]:
                conflicts.append({"chapter": chapter, "verse": verse,
                                  "reason": "Changed after the batch; left as it is."})
                continue
            context = {"reference": {"bookId": self.project.book_id, "chapter": chapter, "verse": verse},
                       "tool": "translationCoreAI", "groupId": "language-qa-scope-undo",
                       "batchId": undo_id, "undoes": batch_id}
            result = self.project.apply_scripture_edit(chapter, verse, item["oldText"], username=username,
                                                       tags=["languageQa", "scope", "undo"], context_id=context)
            items.append({"chapter": chapter, "verse": verse, "oldText": current, "newText": item["oldText"],
                          "journalTransactionId": result.get("journalTransactionId")})
            reverted.append({"chapter": chapter, "verse": verse, "newText": item["oldText"],
                             "display": verse_display(item["oldText"])})
        learned = batch.get("learned")
        if learned and reverted:
            shared = self._collection_learned_fix(learned["old"], learned["new"])
            self.project.retract_learned_fix(learned["old"], learned["new"], n=int(learned.get("n") or 1),
                                             floor=(shared["own"] - shared["count"]) if shared else 0)
        now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        self.project.record_language_qa_batch({
            "batchId": undo_id, "kind": "undo", "state": "applied", "action": "undo", "undoes": batch_id,
            "chapter": batch.get("chapter"), "count": len(reverted), "items": items, "conflicts": conflicts,
            "username": username, "createdAt": now})
        self.project.set_language_qa_batch_state(batch_id, "undone", undoneBy=undo_id)
        for chapter in sorted({r["chapter"] for r in reverted}):
            self._language_qa.invalidate(chapter)
        self._consistency_findings_by_book.pop(str(self.project.path), None)
        return {"batchId": batch_id, "undoBatchId": undo_id, "reverted": reverted, "conflicts": conflicts}

    # -- export -------------------------------------------------------------
    #
    # Raw imports preserve their original USFM alongside the normalized tC
    # project. Use that file as a structural template so headings, poetry,
    # footnotes, custom/ESFM markers, and verse bridges survive export. The
    # normalized target chapter JSON supplies each current verse payload,
    # which also removes imported USFM 3 alignment milestones. Older tC
    # projects without a source USFM still receive an explicit simplified
    # reconstruction rather than failing export altogether.

    def _source_preserving_usfm(
        self, verse_renderer: Optional[Callable[[str, str], str]] = None,
    ) -> str | None:
        # The parser that imported the book decides where each verse's text
        # sits in the preserved source (#91), and only that span is replaced
        # (#190). Everything between verses -- the section headings #180 moved
        # into headings.json, paragraph and poetry markers, blank lines -- is
        # left exactly as written, and a verse whose `\v` does not start its
        # line is written back like any other. The regex this replaced took
        # everything up to the next `\v`, which overwrote those headings, and
        # never matched a mid-line `\v`, which silently dropped its edit.
        source_path = self.project.usfm_path()
        if source_path is None:
            return None
        try:
            source = read_usfm_text(source_path)
            parsed = parse_usfm(source)
        except (OSError, UsfmParseError):
            return None
        if not parsed.chapters or not parsed.verses:
            return None

        replacements: list[tuple[int, int, str]] = []
        available_chapters = set(self.project.chapters())
        verse_cache: dict[str, set[str]] = {}
        for entry in parsed.verses:
            if entry.chapter not in available_chapters:
                continue
            chapter_verses = verse_cache.setdefault(
                entry.chapter, set(self.project.verses(entry.chapter)),
            )
            if entry.verse not in chapter_verses:
                continue
            current_text = (
                verse_renderer(entry.chapter, entry.verse)
                if verse_renderer is not None
                else self.project.target_verse_text(entry.chapter, entry.verse)
            ).strip()
            if current_text and entry.start > 0 and not source[entry.start - 1].isspace():
                current_text = " " + current_text
            replacements.append((entry.start, entry.head_end, current_text))
            # Scripture that followed a heading inside this verse was folded
            # into its stored text, which is now all written at the head, so
            # the old copies go (with their line break).
            for tail_start, tail_end in entry.tail_spans:
                drop_end = tail_end + 1 if source[tail_end:tail_end + 1] == "\n" else tail_end
                replacements.append((tail_start, drop_end, ""))

        if not replacements:
            return None
        replacements.sort()
        pieces: list[str] = []
        cursor = 0
        for start, end, replacement in replacements:
            pieces.extend((source[cursor:start], replacement))
            cursor = end
        pieces.append(source[cursor:])
        rendered = "".join(pieces)
        return rendered if rendered.endswith("\n") else rendered + "\n"

    def _export_gate(self, kind: str, output_path: str, override: bool) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
        """(refusal, gate). The one publication gate (reporting.publication_gate,
        layered-rules 4.5) is consulted before any export. With blocking items
        and no override the export is refused with those items, and nothing is
        written. With the override it proceeds, and the override is recorded
        as a decision (kind 'qa', key 'export.override') listing the items
        open at that moment, so it is in change_log for the export ledger."""
        gate = publication_gate(self.project)
        if not gate["blocking"]:
            return None, gate
        if not override:
            return {"written": False, "blocked": True, "path": output_path, "gate": gate}, gate
        self.project.record_qa_decision(
            "", "", issue_key="export.override", decision="accepted",
            note=f"Exported {kind} with {len(gate['items'])} blocking item(s) open.",
            issue={"source": "export", "format": kind, "outputPath": output_path,
                   "openItems": gate["items"][:200], "counts": gate["counts"]},
        )
        return None, gate

    def _write_export_ledger(self, output_path: str) -> str:
        """`<book>.language-qa-changes.csv` beside the export (layered-rules
        6.5): every Scripture change a Language QA Use applied, and every
        export made over the publication gate. UTF-8 with a BOM, like the
        other CSVs, so Excel opens Tamil as text."""
        import csv
        ledger = Path(output_path).with_name(f"{self.project.book_id}.language-qa-changes.csv")
        with ledger.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(TranslationCoreProject.LEDGER_COLUMNS))
            writer.writeheader()
            writer.writerows(self.project.language_qa_change_ledger())
        return str(ledger)

    def export_non_aligned(self, output_path: str, override: bool = False) -> dict[str, Any]:
        """Write current verse text as non-aligned, re-importable USFM."""
        self._require_project()
        refusal, gate = self._export_gate("nonAligned", output_path, override)
        if refusal is not None:
            return refusal
        summary = self.project.summary
        content = self._source_preserving_usfm()
        fidelity = "source-preserving"
        if content is None:
            fidelity = "simplified"
            lines = [f"\\id {summary.book_id.upper()}"]
            for chapter in self.project.chapters():
                lines.append(f"\\c {chapter}")
                for verse in self.project.verses(chapter):
                    if verse == "front":
                        continue
                    text = self.project.target_verse_text(chapter, verse)
                    lines.append(f"\\v {verse} {text}")
            content = "\n".join(lines) + "\n"
        Path(output_path).write_text(content, encoding="utf-8")
        ledger = self._write_export_ledger(output_path)
        return {
            "written": True, "path": output_path,
            "bookId": summary.book_id, "chapters": len(self.project.chapters()),
            "fidelity": fidelity,
            "note": (
                "Original USFM structure preserved with current verse text."
                if fidelity == "source-preserving"
                else "No source USFM was available; generated id/chapter/verse markers only."
            ),
            "gate": gate, "overridden": bool(gate and gate["blocking"]), "ledgerPath": ledger,
        }

    def export_aligned(self, output_path: str, override: bool = False) -> dict[str, Any]:
        """Write interoperable aligned USFM 3.

        A `.json` destination remains supported for backward compatibility
        with the earlier diagnostic export, but the desktop now defaults to
        `.usfm` and emits unfoldingWord-compatible `zaln`/`w` markers.
        """
        self._require_project()
        refusal, gate = self._export_gate("aligned", output_path, override)
        if refusal is not None:
            return refusal
        if Path(output_path).suffix.lower() == ".json":
            result = self._export_alignment_json(output_path)
            return {**result, "gate": gate, "overridden": bool(gate and gate["blocking"]),
                    "ledgerPath": self._write_export_ledger(output_path)}
        summary = self.project.summary
        book = summary.book_id

        def render(chapter: str, verse: str) -> str:
            try:
                return render_aligned_verse(
                    self.project.target_verse_text(chapter, verse),
                    self.project.load_verse_alignment(chapter, verse),
                )
            except AlignedUsfmError as exc:
                raise ProjectError(
                    f"Cannot export aligned USFM at {book.upper()} {chapter}:{verse}: {exc}"
                ) from exc

        content = self._source_preserving_usfm(render)
        fidelity = "source-preserving"
        if content is None:
            fidelity = "simplified"
            lines = [f"\\id {book.upper()}", "\\usfm 3.0"]
            for chapter in self.project.chapters():
                lines.append(f"\\c {chapter}")
                for verse in self.project.verses(chapter):
                    if verse == "front":
                        continue
                    lines.append(f"\\v {verse} {render(chapter, verse)}")
            content = "\n".join(lines) + "\n"
        # Aligned USFM declares its version. The parser says where the `\usfm`
        # header is, or where the `\id` line ends so one can be inserted (#91).
        headers = {h.tag.lower(): h for h in parse_usfm(content).headers}
        version = headers.get("usfm")
        if version is not None and version.offset >= 0:
            line_end = content.find("\n", version.offset)
            line_end = len(content) if line_end < 0 else line_end
            content = content[:version.offset] + "\\usfm 3.0" + content[line_end:]
        else:
            book_id = headers.get("id")
            insert_at = 0
            if book_id is not None and book_id.offset >= 0:
                line_end = content.find("\n", book_id.offset)
                insert_at = len(content) if line_end < 0 else line_end + 1
            content = content[:insert_at] + "\\usfm 3.0\n" + content[insert_at:]
        Path(output_path).write_text(content, encoding="utf-8")
        ledger = self._write_export_ledger(output_path)
        status = self.alignment_status()
        return {
            "written": True, "path": output_path, "bookId": book,
            "chapters": len(self.project.chapters()), "format": "usfm3-aligned",
            "fidelity": fidelity, "alignmentStatus": status["counts"],
            "gate": gate, "overridden": bool(gate and gate["blocking"]), "ledgerPath": ledger,
        }

    def _export_alignment_json(self, output_path: str) -> dict[str, Any]:
        summary = self.project.summary
        book = summary.book_id
        out: dict[str, Any] = {
            "bookId": book, "bookName": summary.book_name,
            "targetLanguage": summary.target_language,
            "chapters": {},
        }
        for chapter in self.project.chapters():
            chapter_out: dict[str, Any] = {}
            for verse in self.project.verses(chapter):
                alignment = self.project.load_verse_alignment(chapter, verse)
                chapter_out[verse] = {
                    "text": self.project.target_verse_text(chapter, verse),
                    "alignment": alignment.to_dict(),
                    "decisions": self.project.qa_decisions_for_verse(chapter, verse),
                }
            out["chapters"][chapter] = chapter_out
        Path(output_path).write_text(
            json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {"written": True, "path": output_path, "bookId": book,
                "chapters": len(self.project.chapters()), "format": "alignment-json"}

    # -- versification ------------------------------------------------------

    def _book_verse_text_map(self, project: TranslationCoreProject) -> dict[str, str]:
        result: dict[str, str] = {}
        for chapter in project.chapters():
            for verse in project.verses(chapter):
                text = project.target_verse_text(chapter, verse)
                if text:
                    result[f"{chapter}:{verse}"] = text
        return result

    # -- settings ---------------------------------------------------------

    def get_settings(self) -> dict[str, Any]:
        return {
            "provider": self.settings.provider,
            "apiBaseUrl": self.settings.api_base_url,
            "model": self.settings.model,
            "reviewerName": self.settings.reviewer_name,
            "reviewerNameUpdatedAt": self.settings.reviewer_name_updated_at,
            # The stable id to stamp on a write (#78). The UI used to send the
            # display name as `actorId`, so renaming yourself split your history
            # between two apparent actors. It sends this instead, and the engine
            # falls back to the same value when it is absent.
            "localUserId": self.current_actor_id(),
            "reviewerMode": self.settings.reviewer_mode,
            "paratextUsername": self.settings.paratext_username,
            "paratextNavigation": self.settings.paratext_navigation,
            "logosNavigation": self.settings.logos_navigation,
            "triageHideThreshold": self.settings.triage_hide_threshold,
            "languageQaInlinePrecision": self.settings.language_qa_inline_precision,
            "languageQaInlineConfidence": self.settings.language_qa_inline_confidence,
            "languageQaLearnedFixes": self.settings.language_qa_learned_fixes,
            "languageQaReferenceDirs": self.settings.language_qa_reference_dirs,
            "languageQaRelatedWords": self.settings.language_qa_related_words,
            "bookmarks": self.settings.bookmarks,
            "recentChapters": self.settings.recent_chapters,
            "hasApiKey": bool(self.settings.get_api_key()),
            "aiUsage": self.settings.get_ai_usage_totals(),
        }

    def set_settings(self, **kwargs) -> dict[str, Any]:
        if "apiKey" in kwargs:
            self.settings.set_api_key(kwargs["apiKey"])
        if "provider" in kwargs:
            self.settings.provider = kwargs["provider"]
        if "apiBaseUrl" in kwargs:
            self.settings.api_base_url = kwargs["apiBaseUrl"]
        if "model" in kwargs:
            self.settings.model = kwargs["model"]
        if "reviewerName" in kwargs:
            self.settings.reviewer_name = kwargs["reviewerName"]
            # Keep the workspace users row in step immediately rather than at
            # the next project open (#78). The user_id never changes, so the
            # rename reaches every row this person has already written instead
            # of detaching them from their own history.
            self._local_user = None
            self.current_actor_id()
        if "reviewerMode" in kwargs:
            self.settings.reviewer_mode = kwargs["reviewerMode"]
        if "paratextNavigation" in kwargs:
            self.settings.paratext_navigation = bool(kwargs["paratextNavigation"])
        if "logosNavigation" in kwargs:
            self.settings.logos_navigation = bool(kwargs["logosNavigation"])
        if "triageHideThreshold" in kwargs:
            self.settings.triage_hide_threshold = kwargs["triageHideThreshold"]
        if "languageQaInlinePrecision" in kwargs:
            self.settings.language_qa_inline_precision = kwargs["languageQaInlinePrecision"]
        if "languageQaInlineConfidence" in kwargs:
            self.settings.language_qa_inline_confidence = kwargs["languageQaInlineConfidence"]
        if "languageQaLearnedFixes" in kwargs:
            self.settings.language_qa_learned_fixes = bool(kwargs["languageQaLearnedFixes"])
        if "languageQaRelatedWords" in kwargs:
            self.settings.language_qa_related_words = bool(kwargs["languageQaRelatedWords"])
        if "languageQaReferenceDirs" in kwargs:
            self.settings.language_qa_reference_dirs = self._valid_reference_dirs(kwargs["languageQaReferenceDirs"])
        if "bookmarks" in kwargs:
            self.settings.bookmarks = self._valid_places(kwargs["bookmarks"], "bookmarks", self.BOOKMARKS_MAX, True)
        if "recentChapters" in kwargs:
            self.settings.recent_chapters = self._valid_places(
                kwargs["recentChapters"], "recentChapters", self.RECENT_CHAPTERS_MAX, False)
        self._configure_language_qa()
        self._navigation.configure(
            paratext=self.settings.paratext_navigation,
            logos=self.settings.logos_navigation,
        )
        return self.get_settings()

    BOOKMARKS_MAX = 200
    RECENT_CHAPTERS_MAX = 20
    PLACE_LABEL_MAX = 200

    @classmethod
    def _valid_places(cls, value: Any, name: str, cap: int, with_verse: bool) -> list[dict[str, Any]]:
        """Bookmarks (a verse) or recent chapters, newest first. A place is
        `{collection, book, chapter[, verse], label, ts}`; `collection` is the
        open collection's id (or the project path), so a list can span projects
        and each shows only its own. A repeat keeps its first (newest) entry,
        and the list is cut to `cap`."""
        if not isinstance(value, list):
            raise ProjectError(f"{name} must be a list")
        out: list[dict[str, Any]] = []
        seen: set[tuple[str, ...]] = set()
        for place in value:
            if not isinstance(place, dict):
                raise ProjectError(f"each of {name} must be an object")
            fields = {k: place.get(k) for k in ("collection", "book", "chapter", "verse", "label", "ts")}
            for key in ("collection", "book", "chapter") + (("verse",) if with_verse else ()):
                if not isinstance(fields[key], str) or not fields[key].strip():
                    raise ProjectError(f"each of {name} needs a non-empty {key}")
            label = fields["label"] if isinstance(fields["label"], str) else ""
            clean = {"collection": fields["collection"], "book": fields["book"].lower(),
                     "chapter": fields["chapter"], "label": label[:cls.PLACE_LABEL_MAX],
                     "ts": fields["ts"] if isinstance(fields["ts"], str) else ""}
            if with_verse:
                clean["verse"] = fields["verse"]
            identity = (clean["collection"], clean["book"], clean["chapter"], clean.get("verse", ""))
            if identity in seen:
                continue
            seen.add(identity)
            out.append(clean)
        return out[:cap]

    @staticmethod
    def _valid_reference_dirs(value: Any) -> dict[str, str]:
        """{pack: folder}; an empty folder removes the pack's entry. A folder
        must exist and hold verses.tsv or USFM books, so a typo is refused at
        once rather than shown as a reference that never loads."""
        if not isinstance(value, dict):
            raise ProjectError("languageQaReferenceDirs must be an object of pack -> folder")
        out: dict[str, str] = {}
        for pack, folder in value.items():
            if not isinstance(pack, str) or not isinstance(folder, str):
                raise ProjectError("languageQaReferenceDirs must map a pack name to a folder path")
            if not folder.strip():
                continue
            if detect_reference(folder.strip()) is None:
                raise ProjectError(f"{folder}: no verses.tsv and no USFM books in that folder")
            out[pack] = str(Path(folder.strip()))
        return out

    def _configure_language_qa(self) -> None:
        """Hand the reviewer's Language QA preferences to the manager. The inline
        threshold is read per request, so nothing is rescanned."""
        self._language_qa.configure(inline_precision=self.settings.language_qa_inline_precision,
                                    inline_confidence=self.settings.language_qa_inline_confidence,
                                    learned_fixes=self.settings.language_qa_learned_fixes)

    def _require_project(self) -> None:
        if not self.project:
            raise ProjectError("No project open — call project.open first")

    # -- terminology ----------------------------------------------------

    def terminology_list(self) -> dict[str, Any]:
        self._require_project()
        return {"rules": self.project.terminology_rules()}

    def terminology_record(
        self, concept_id: str, approved_renderings: list[str] | None = None,
        rejected_renderings: list[str] | None = None, overwrite: bool = False, *,
        allowed_alternatives: list[str] | None = None,
        inflected_forms: dict[str, list[str]] | None = None, match_mode: str = "exact",
    ) -> dict[str, Any]:
        """Add a termbase rule from the Settings pane. A rule that already
        exists for this concept is never replaced silently: without
        `overwrite`, nothing is written and the existing rule comes back as
        `conflict`, so the pane can ask first."""
        self._require_project()
        existing = next((rule for rule in self.project.terminology_rules()
                         if str(rule.get("conceptId", "")) == concept_id), None)
        if existing is not None and not overwrite:
            return {"rules": self.project.terminology_rules(), "conflict": existing}
        self.project.record_terminology_rule(
            concept_id, approved_renderings=approved_renderings,
            allowed_alternatives=allowed_alternatives,
            rejected_renderings=rejected_renderings,
            username=self.settings.reviewer_name or "Bridge Reviewer",
            inflected_forms=inflected_forms, match_mode=match_mode,
        )
        # #171 desktop testing: a rule added here has no chapter-file change
        # for Language QA's idle-refresh to notice on its own, unlike an edit
        # or a decision -- without this it would sit invisible until
        # something unrelated triggered a rescan (same bug class as
        # decide_verse not invalidating, fixed 2026-09-23 in 3a095c0).
        self._language_qa.invalidate_all()
        return {"rules": self.project.terminology_rules()}

    # -- protocol dispatch --------------------------------------------------

    def handle_request(self, request: EngineRequest) -> EngineResponse:
        started = time.perf_counter()
        try:
            return self._handle_request(request)
        finally:
            # One line per RPC that held the dispatcher for 100ms or more, so the
            # engine log says which request made the UI wait (check_timing).
            elapsed = time.perf_counter() - started
            if elapsed >= 0.1:
                _trace(f"rpc {request.method} took {elapsed * 1000:.0f}ms")

    def _handle_request(self, request: EngineRequest) -> EngineResponse:
        self._language_qa.touch()
        try:
            m, p = request.method, request.params

            if m in {Methods.LANGUAGE_QA_STATUS, Methods.LANGUAGE_QA_PAUSE, Methods.LANGUAGE_QA_INLINE,
                     Methods.LANGUAGE_QA_HISTORY, Methods.LANGUAGE_QA_VERSE, Methods.LANGUAGE_QA_SET_PACK,
                     Methods.LANGUAGE_QA_LEARNED_LIST, Methods.LANGUAGE_QA_LEARNED_FORGET,
                     Methods.LANGUAGE_QA_LEARNED_RESTORE, Methods.LANGUAGE_QA_SCOPE_FIND,
                     Methods.LANGUAGE_QA_SCOPE_APPLY, Methods.LANGUAGE_QA_BATCH_UNDO, Methods.LANGUAGE_QA_BATCHES,
                     Methods.LANGUAGE_QA_FLAGS_LIST, Methods.LANGUAGE_QA_FLAGS_ADD, Methods.LANGUAGE_QA_FLAGS_UPDATE,
                     Methods.LANGUAGE_QA_FLAGS_DELETE, Methods.LANGUAGE_QA_WORDS_ADD, Methods.LANGUAGE_QA_WORDS_LIST,
                     Methods.LANGUAGE_QA_BOOK_WORDS, Methods.LANGUAGE_QA_OCCURRENCES, Methods.LANGUAGE_QA_REFERENCE,
                     Methods.LANGUAGE_QA_RELATED, Methods.LANGUAGE_QA_CHECKER_SETTINGS_GET,
                     Methods.LANGUAGE_QA_CHECKER_SETTINGS_SET}:
                self._require_project()
                # Compared canonically, not as strings. `project.open` resolves
                # the path it is given, so a caller echoing back the path *it*
                # used is rejected whenever the two spellings differ: an 8.3
                # short name on Windows (`REVANT~1` vs `Revant C Idikulay`, which
                # is what `tempfile` hands out), a symlinked temp root, or a
                # difference in case. That is what failed the Language QA
                # latency gate in CI while every UI path worked, because the UI
                # only ever echoes a path the engine itself produced.
                if canonical_path_key(str(p.get("projectPath") or "")) != canonical_path_key(self.project.path):
                    raise ProjectError("Language QA request belongs to a different project.")
                if m in {Methods.LANGUAGE_QA_LEARNED_LIST, Methods.LANGUAGE_QA_LEARNED_FORGET,
                         Methods.LANGUAGE_QA_LEARNED_RESTORE}:
                    result = self.language_qa_learned(m.rsplit(".", 1)[1], p)
                elif m in {Methods.LANGUAGE_QA_WORDS_ADD, Methods.LANGUAGE_QA_WORDS_LIST}:
                    result = self.language_qa_words(m.rsplit(".", 1)[1], p)
                elif m == Methods.LANGUAGE_QA_BOOK_WORDS:
                    result = self.language_qa_book_words(p)
                elif m == Methods.LANGUAGE_QA_OCCURRENCES:
                    result = self.language_qa_occurrences(p)
                elif m == Methods.LANGUAGE_QA_REFERENCE:
                    result = self.language_qa_reference(p)
                elif m == Methods.LANGUAGE_QA_RELATED:
                    result = self.language_qa_related(p)
                elif m in {Methods.LANGUAGE_QA_FLAGS_LIST, Methods.LANGUAGE_QA_FLAGS_ADD,
                           Methods.LANGUAGE_QA_FLAGS_UPDATE, Methods.LANGUAGE_QA_FLAGS_DELETE}:
                    result = self.language_qa_flags(m.rsplit(".", 1)[1], p)
                elif m == Methods.LANGUAGE_QA_SCOPE_FIND:
                    result = self.language_qa_scope_find(p)
                elif m == Methods.LANGUAGE_QA_SCOPE_APPLY:
                    result = self.language_qa_scope_apply(p)
                elif m == Methods.LANGUAGE_QA_BATCH_UNDO:
                    result = self.language_qa_batch_undo(p)
                elif m == Methods.LANGUAGE_QA_BATCHES:
                    limit = p.get("limit", 50)
                    if not isinstance(limit, int) or limit < 1:
                        raise ProjectError("limit must be a positive integer when given")
                    result = {"batches": self.project.language_qa_batches()[:min(limit, 200)]}
                elif m in {Methods.LANGUAGE_QA_CHECKER_SETTINGS_GET, Methods.LANGUAGE_QA_CHECKER_SETTINGS_SET}:
                    result = self.language_qa_checker_settings(m.rsplit(".", 1)[1], p)
                elif m == Methods.LANGUAGE_QA_SET_PACK:
                    # The project's Language QA setting (Settings > Language QA):
                    # "auto", "off", or a registered pack. Rebinding starts a pass
                    # with the new pack; nothing else about the project changes.
                    pack = p.get("pack")
                    allowed = {"auto", "off"} | {entry["pack"] for entry in available_language_packs()}
                    if not isinstance(pack, str) or pack not in allowed:
                        raise ProjectError(f"pack must be one of {sorted(allowed)}")
                    self.project.set_language_qa_pack(pack)
                    self._language_qa.bind(self.project)
                    result = self._language_qa.status(limit=0)
                elif m == Methods.LANGUAGE_QA_PAUSE:
                    if not isinstance(p.get("paused"), bool):
                        raise ProjectError("paused must be a boolean")
                    result = self._language_qa.pause(p["paused"])
                elif m == Methods.LANGUAGE_QA_INLINE:
                    chapter = p.get("chapter")
                    if chapter is not None and not isinstance(chapter, str):
                        raise ProjectError("chapter must be a string when given")
                    result = self._language_qa.inline(chapter=chapter)
                elif m == Methods.LANGUAGE_QA_VERSE:
                    chapter, verse = p.get("chapter"), p.get("verse")
                    if not isinstance(chapter, str) or not isinstance(verse, str):
                        raise ProjectError("chapter and verse must be strings")
                    result = self._language_qa.verse(chapter, verse)
                elif m == Methods.LANGUAGE_QA_HISTORY:
                    chapter, verse, finding_id = p.get("chapter"), p.get("verse"), p.get("findingId")
                    if not isinstance(chapter, str) or not isinstance(verse, str):
                        raise ProjectError("chapter and verse must be strings")
                    if finding_id is not None and not isinstance(finding_id, str):
                        raise ProjectError("findingId must be a string when given")
                    result = {"chapter": chapter, "verse": verse, "findingId": finding_id,
                              "entries": self.project.language_qa_decision_history(chapter, verse, finding_id)}
                else:
                    view = p.get("view", "findings")
                    if view not in {"findings", "recheck", "falsePositives"}:
                        raise ProjectError("view must be findings, recheck or falsePositives")
                    chapter, categories = p.get("chapter"), p.get("categories")
                    if chapter is not None and not isinstance(chapter, str):
                        raise ProjectError("chapter must be a string when given")
                    if categories is not None and (not isinstance(categories, list)
                                                   or not all(isinstance(c, str) for c in categories)):
                        raise ProjectError("categories must be a list of strings when given")
                    result = self._language_qa.status(offset=p.get("offset", 0), limit=p.get("limit", 0), view=view,
                                                      chapter=chapter, categories=categories)
                return EngineResponse.ok(request.id, result=result)

            if m == Methods.PING:
                return EngineResponse.ok(request.id, result={"pong": True})
            if m == Methods.ENGINE_INFO:
                return EngineResponse.ok(request.id, result=self.info())
            if m == Methods.PROJECT_OPEN:
                return EngineResponse.ok(request.id, result=self.open_project(
                    p["path"], p.get("projectId", ""),
                ))
            if m == Methods.PROJECT_LIST:
                return EngineResponse.ok(request.id, result=self.list_projects())
            if m == Methods.PROJECT_LIST_BOOK_PROGRESS:
                return EngineResponse.ok(request.id, result=self.list_book_progress())
            if m == Methods.PROJECT_FORGET:
                return EngineResponse.ok(request.id, result=self.forget_project(p.get("projectId", "")))
            if m == Methods.PROJECT_DELETE:
                return EngineResponse.ok(request.id, result=self.delete_project(p.get("projectId", "")))
            if m == Methods.PROJECT_SCAN:
                return EngineResponse.ok(request.id, result=self.scan_project())
            if m == Methods.PROJECT_REPORT:
                return EngineResponse.ok(request.id, result=self.build_project_report())
            if m == Methods.PROJECT_COLLECTION_REPORT:
                return EngineResponse.ok(request.id, result=self.build_collection_report())
            if m == Methods.REPORT_GENERATE:
                return EngineResponse.ok(request.id, result=self.start_qa_report())
            if m == Methods.REPORT_STATUS:
                return EngineResponse.ok(request.id, result=self.qa_report_status(p.get("jobId", "")))
            if m == Methods.REPORT_GET:
                return EngineResponse.ok(request.id, result=self.qa_report_get(p.get("jobId", "")))
            if m == Methods.REPORT_CANCEL:
                return EngineResponse.ok(request.id, result=self.cancel_qa_report(p.get("jobId", "")))
            if m == Methods.REPORT_EXPORT:
                return EngineResponse.ok(request.id, result=self.export_qa_report(
                    p.get("outputPath", ""), p.get("format", "csv"),
                    p.get("rows", []), p.get("columns"),
                ))
            if m == Methods.TRIAGE_RUN:
                return EngineResponse.ok(request.id, result=self.start_triage(
                    p.get("book", ""), bool(p.get("force", False)),
                ))
            if m == Methods.TRIAGE_STATUS:
                return EngineResponse.ok(request.id, result=self.triage_status(p.get("jobId", "")))
            if m == Methods.TRIAGE_CANCEL:
                return EngineResponse.ok(request.id, result=self.cancel_triage(p.get("jobId", "")))
            if m == Methods.TRIAGE_OVERRIDE:
                return EngineResponse.ok(request.id, result=self.override_triage(
                    p.get("book", ""), p.get("hash", ""), p.get("verdict", ""),
                ))
            if m == Methods.TRIAGE_CLEAR:
                return EngineResponse.ok(request.id, result=self.clear_triage(p.get("book", "")))
            if m == Methods.TRIAGE_RESULTS:
                return EngineResponse.ok(request.id, result=self.triage_results(p.get("book", "")))
            if m == Methods.PROJECT_INSPECT_IMPORT:
                return EngineResponse.ok(request.id, result=self.inspect_project_import(
                    p["path"], p.get("metadata"),
                ))
            if m == Methods.PROJECT_IMPORT:
                return EngineResponse.ok(request.id, result=self.import_project(
                    p["path"], p.get("metadata", {}), p.get("destinationRoot", ""),
                    bool(p.get("allowDuplicate", False)),
                ))
            if m == Methods.CHAPTER_VERSES:
                return EngineResponse.ok(request.id, result={"verses": self.chapter_verses(p["chapter"])})
            if m == Methods.CHAPTER_VERSE_DATA:
                return EngineResponse.ok(request.id, result=self.get_chapter_verse_data(p["chapter"]))
            if m == Methods.HOUSESTYLE_LIST:
                return EngineResponse.ok(request.id, result=self.housestyle_list())
            if m == Methods.HOUSESTYLE_NAME_SUGGESTIONS:
                return EngineResponse.ok(request.id, result=self.housestyle_name_suggestions())
            if m == Methods.HOUSESTYLE_RECORD:
                if not isinstance(p.get("entry"), dict):
                    raise ProjectError("entry must be an object")
                return EngineResponse.ok(request.id, result=self.housestyle_record(p["entry"]))
            if m == Methods.HOUSESTYLE_SET_STATE:
                return EngineResponse.ok(request.id, result=self.housestyle_set_state(
                    str(p.get("key") or ""), str(p.get("state") or "")))
            if m == Methods.HOUSESTYLE_EXPORT:
                return EngineResponse.ok(request.id, result=self.housestyle_export(str(p["outputPath"])))
            if m == Methods.HOUSESTYLE_IMPORT:
                return EngineResponse.ok(request.id, result=self.housestyle_import(str(p["inputPath"])))
            if m == Methods.COLLECTION_RUN_CHECKS:
                checks = p.get("checks")
                if checks is not None and not (isinstance(checks, list) and all(isinstance(c, str) for c in checks)):
                    raise ProjectError("checks must be a list of check names")
                return EngineResponse.ok(request.id, result=self.start_collection_checks(
                    checks=checks, force=bool(p.get("force", False))))
            if m == Methods.COLLECTION_QA_STATUS:
                return EngineResponse.ok(request.id, result=self.collection_check_status(str(p.get("jobId", ""))))
            if m == Methods.COLLECTION_PAUSE_CHECKS:
                if not isinstance(p.get("paused"), bool):
                    raise ProjectError("paused must be a boolean")
                return EngineResponse.ok(request.id, result=self._collection_jobs.pause(
                    p["paused"], str(p.get("jobId", ""))))
            if m == Methods.COLLECTION_CANCEL_CHECKS:
                return EngineResponse.ok(request.id, result=self._collection_jobs.cancel(str(p.get("jobId", ""))))
            if m == Methods.CHECKS_START:
                if self._collection_jobs.active():
                    raise CheckJobConflict("A collection QA run is in progress; wait for it or cancel it.")
                return EngineResponse.ok(request.id, result=self.start_check_job(
                    scope=p.get("scope", "chapter"),
                    chapters=p.get("chapters"),
                    checks=p.get("checks"),
                ))
            if m == Methods.CHECKS_STATUS:
                since = p.get("since")
                if since is not None and (isinstance(since, bool) or not isinstance(since, int) or since < 0):
                    raise ValueError("since must be a non-negative integer")
                return EngineResponse.ok(
                    request.id, result=self.check_job_status(p.get("jobId", ""), since),
                )
            if m == Methods.CHECKS_CANCEL:
                return EngineResponse.ok(
                    request.id, result=self.cancel_check_job(p.get("jobId", "")),
                )
            if m == Methods.CHECKS_RETRY:
                return EngineResponse.ok(
                    request.id, result=self.retry_check_job(p["jobId"]),
                )
            if m == Methods.VERSE_GET:
                return EngineResponse.ok(request.id, result=self.get_verse(p["chapter"], p["verse"]))
            if m == Methods.VERSE_RUN_CHECKS:
                findings = self.run_verse_checks(p["chapter"], p["verse"], p.get("checks", ["local", "greekroom"]))
                return EngineResponse.ok(request.id, findings=findings)
            if m == Methods.VERSE_DECIDE:
                issue = p.get("issue")
                if issue is not None and not isinstance(issue, dict):
                    raise ProjectError("issue must be an object when given")
                result = self.decide_verse(p["chapter"], p["verse"], p["findingId"], p["status"],
                                           p.get("comment", ""), issue)
                return EngineResponse.ok(request.id, result=result)
            if m == Methods.VERSE_EDIT:
                result = self.edit_verse(p["chapter"], p["verse"], p["newText"])
                return EngineResponse.ok(request.id, result=result)
            if m == Methods.VERSE_HISTORY:
                return EngineResponse.ok(request.id, result=self.verse_history(p))
            if m == Methods.CHECK_LIST_FOR_VERSE:
                return EngineResponse.ok(request.id, result=self.list_checks_for_verse(
                    p["chapter"], p["verse"],
                ))
            if m == Methods.CHECK_VALIDATE_SELECTION:
                return EngineResponse.ok(request.id, result=self.validate_check_selection(
                    p["chapter"], p["verse"], p["tool"], p["groupId"], p["checkId"],
                    p.get("selections", []), p.get("nothingToSelect", False),
                ))
            if m == Methods.CHECK_SAVE_SELECTION:
                return EngineResponse.ok(request.id, result=self.save_check_selection(
                    p["chapter"], p["verse"], p["tool"], p["groupId"], p["checkId"],
                    p.get("selections", []), p.get("nothingToSelect", False),
                    p.get("provenance", "human"), p.get("expectedFingerprint", ""),
                    p.get("metadata"),
                ))
            if m == Methods.CHECK_CLEAR_SELECTION:
                return EngineResponse.ok(request.id, result=self.clear_check_selection(
                    p["chapter"], p["verse"], p["tool"], p["groupId"], p["checkId"],
                    p.get("provenance", "human"), p.get("expectedFingerprint", ""),
                    p.get("metadata"),
                ))
            if m == Methods.ALIGNMENT_GET:
                return EngineResponse.ok(
                    request.id, result=self.get_alignment(p["chapter"], p["verse"]),
                )
            if m == Methods.ALIGNMENT_GET_RANGE:
                return EngineResponse.ok(
                    request.id, result=self.get_alignment_range(p["chapter"], p.get("verses", [])),
                )
            if m == Methods.ALIGNMENT_GAP_SCAN:
                return EngineResponse.ok(request.id, result=self.gap_scan(p["chapter"]))
            if m == Methods.ALIGNMENT_CROSS_VERSE_PROPOSE:
                return EngineResponse.ok(request.id, result=self.propose_cross_verse(
                    p["chapter"], p.get("verses", []),
                ))
            if m == Methods.ALIGNMENT_CROSS_VERSE_AI_PROPOSE:
                return EngineResponse.ok(request.id, result=self.ai_propose_cross_verse(
                    p["chapter"], p.get("verses", []),
                ))
            if m == Methods.ALIGNMENT_CROSS_VERSE_LINK:
                return EngineResponse.ok(request.id, result=self.link_cross_verse(
                    p.get("source"), p.get("target"), p.get("origin", ""),
                    sources=p.get("sources"), targets=p.get("targets"),
                ))
            if m == Methods.ALIGNMENT_CROSS_VERSE_UNLINK:
                return EngineResponse.ok(request.id, result=self.unlink_cross_verse(
                    str(p.get("linkId") or ""), str(p.get("groupId") or ""),
                ))
            if m == Methods.ALIGNMENT_NULL_SET:
                return EngineResponse.ok(request.id, result=self.set_null_decision(p))
            if m == Methods.ALIGNMENT_NULL_CLEAR:
                return EngineResponse.ok(request.id, result=self.clear_null_decision(
                    str(p.get("decisionId") or ""),
                ))
            if m == Methods.ALIGNMENT_WINDOW_AUTO_ALIGN:
                return EngineResponse.ok(request.id, result=self.auto_align_window(
                    str(p.get("chapter") or ""), p.get("verses"), apply=bool(p.get("apply", True)),
                ))
            if m == Methods.ALIGNMENT_AUTO_ALIGN_REVERT:
                return EngineResponse.ok(request.id, result=self.auto_align_revert(
                    str(p.get("chapter") or ""), str(p.get("verse") or ""),
                ))
            if m == Methods.ALIGNMENT_AUTO_ALIGN_VERDICT:
                return EngineResponse.ok(request.id, result=self.auto_align_verdict(
                    str(p.get("chapter") or ""), str(p.get("verse") or ""),
                ))
            if m == Methods.ALIGNMENT_AUTO_ALIGN_START:
                return EngineResponse.ok(request.id, result=self.start_auto_align_job(
                    str(p.get("scope") or "chapter"), p.get("chapters"), bool(p.get("apply", True)),
                ))
            if m == Methods.ALIGNMENT_AUTO_ALIGN_STATUS:
                return EngineResponse.ok(request.id, result=self.auto_align_job_status(str(p.get("jobId") or "")))
            if m == Methods.ALIGNMENT_AUTO_ALIGN_CANCEL:
                return EngineResponse.ok(request.id, result=self.cancel_auto_align_job(str(p.get("jobId") or "")))
            if m == Methods.ALIGNMENT_AUTO_ALIGN_RETRY:
                return EngineResponse.ok(request.id, result=self.retry_auto_align_job(str(p.get("jobId") or "")))
            if m == Methods.ALIGNMENT_AUTO_ALIGN_ESTIMATE:
                return EngineResponse.ok(request.id, result=self.auto_align_estimate(
                    str(p.get("scope") or "chapter"), p.get("chapters"),
                ))
            if m == Methods.LEXICON_GET_ENTRY:
                return EngineResponse.ok(
                    request.id, result=self.get_lexicon_entry(p.get("strong", ""), p.get("morph", "")),
                )
            if m == Methods.ALIGNMENT_STATUS:
                return EngineResponse.ok(
                    request.id, result=self.alignment_status(p.get("chapter", "")),
                )
            if m == Methods.ALIGNMENT_REALIGN:
                return EngineResponse.ok(request.id, result=self.realign_words(
                    p["chapter"], p["verse"], p.get("topIds", []), p.get("bottomIds", []),
                    p["expectedOriginal"],
                ))
            if m == Methods.ALIGNMENT_UNALIGN:
                return EngineResponse.ok(request.id, result=self.unalign_words(
                    p["chapter"], p["verse"], p.get("bottomIds", []), p["expectedOriginal"],
                ))
            if m == Methods.ALIGNMENT_SAVE:
                return EngineResponse.ok(request.id, result=self.save_alignment(
                    p["chapter"], p["verse"], p["alignment"], p["expectedOriginal"],
                ))
            if m == Methods.ALIGNMENT_COMPLETE:
                return EngineResponse.ok(
                    request.id, result=self.complete_alignment(p["chapter"], p["verse"]),
                )
            if m == Methods.ALIGNMENT_UNDO:
                return EngineResponse.ok(request.id, result=self.undo_alignment(
                    p["chapter"], p["verse"], p["expectedOriginal"],
                ))
            if m == Methods.ALIGNMENT_BACKUPS:
                context = self.get_alignment(p["chapter"], p["verse"])
                return EngineResponse.ok(request.id, result={"history": context["history"]})
            if m == Methods.ALIGNMENT_RESTORE:
                return EngineResponse.ok(request.id, result=self.undo_alignment(
                    p["chapter"], p["verse"], p["expectedOriginal"], p["historyId"],
                ))
            if m == Methods.SETTINGS_GET:
                return EngineResponse.ok(request.id, result=self.get_settings())
            if m == Methods.SETTINGS_SET:
                return EngineResponse.ok(request.id, result=self.set_settings(**p))
            if m == Methods.TERMINOLOGY_LIST:
                return EngineResponse.ok(request.id, result=self.terminology_list())
            if m == Methods.TERMINOLOGY_RECORD:
                overwrite = p.get("overwrite", False)
                if not isinstance(overwrite, bool):
                    raise ProjectError("overwrite must be a boolean")
                inflected = p.get("inflectedForms")
                if inflected is not None and not isinstance(inflected, dict):
                    raise ProjectError("inflectedForms must be an object")
                return EngineResponse.ok(request.id, result=self.terminology_record(
                    p["conceptId"], p.get("approvedRenderings"), p.get("rejectedRenderings"), overwrite,
                    allowed_alternatives=p.get("allowedAlternatives"), inflected_forms=inflected,
                    match_mode=str(p.get("matchMode") or "exact"),
                ))
            if m == Methods.EXPORT_ALIGNED:
                return EngineResponse.ok(request.id, result=self.export_aligned(
                    p["outputPath"], override=p.get("override") is True))
            if m == Methods.EXPORT_NON_ALIGNED:
                return EngineResponse.ok(request.id, result=self.export_non_aligned(
                    p["outputPath"], override=p.get("override") is True))
            if m == Methods.ALIGNMENT_AI_PROPOSE:
                return EngineResponse.ok(request.id, result=self.propose_ai_alignment(
                    p["chapter"], p["verse"], p.get("mode", "gap_fill"),
                ))
            if m == Methods.ALIGNMENT_AI_APPLY_PROPOSAL:
                return EngineResponse.ok(request.id, result=self.apply_ai_alignment_proposal(
                    p["chapter"], p["verse"], p["proposal"], p["expectedOriginal"],
                ))
            if m == Methods.AI_REVIEW_START:
                return EngineResponse.ok(request.id, result=self.start_ai_review_job(
                    p.get("scope", "verse"), p.get("chapter", ""), p.get("verse", ""),
                    p.get("mode", ""),
                ))
            if m == Methods.AI_REVIEW_STATUS:
                return EngineResponse.ok(
                    request.id, result=self.ai_review_job_status(p.get("jobId", "")),
                )
            if m == Methods.AI_REVIEW_CANCEL:
                return EngineResponse.ok(
                    request.id, result=self.cancel_ai_review_job(p.get("jobId", "")),
                )
            if m == Methods.AI_REVIEW_RETRY:
                return EngineResponse.ok(
                    request.id, result=self.retry_ai_review_job(p["jobId"]),
                )
            if m == Methods.AI_REVIEW_LIST_CHAPTER:
                return EngineResponse.ok(
                    request.id, result=self.list_ai_reviews_for_chapter(p["chapter"]),
                )
            if m == Methods.PASSAGE_SEMANTIC_STATUS:
                return EngineResponse.ok(request.id, result=self.passage_semantic_status())
            if m == Methods.PASSAGE_SEMANTIC_PROJECT_METADATA:
                return EngineResponse.ok(
                    request.id, result=self.passage_semantic_project_metadata(),
                )
            if m == Methods.PASSAGE_SEMANTIC_CURRENT_PASSAGE:
                return EngineResponse.ok(request.id, result=self.passage_semantic_current_passage(
                    str(p.get("chapter") or ""), str(p.get("verse") or ""),
                    str(p.get("endChapter") or ""), str(p.get("endVerse") or ""),
                ))
            if m == Methods.PASSAGE_SEMANTIC_STALE_SUMMARY:
                return EngineResponse.ok(
                    request.id, result=self.passage_semantic_stale_summary(),
                )
            if m == Methods.PASSAGE_SEMANTIC_MIGRATION_REPORT:
                return EngineResponse.ok(
                    request.id, result=self.passage_semantic_migration_report(),
                )
            if m == Methods.PASSAGE_SEMANTIC_REBUILD_PASSAGE:
                return EngineResponse.ok(request.id, result=self.passage_semantic_rebuild_passage(
                    str(p.get("chapter") or ""), str(p.get("verse") or ""),
                    str(p.get("endChapter") or ""), str(p.get("endVerse") or ""),
                    str(p.get("tokenizerProfile") or "bridge-unicode-word-v1"),
                ))
            if m == Methods.SOURCE_SEMANTIC_BUILD_RANGE:
                return EngineResponse.ok(request.id, result=self.source_semantic_build_range(
                    str(p.get("chapter") or ""), str(p.get("verse") or ""),
                    str(p.get("endChapter") or ""), str(p.get("endVerse") or ""),
                ))
            if m == Methods.SOURCE_SEMANTIC_GET_RANGE:
                return EngineResponse.ok(
                    request.id, result=self.source_semantic_get_range(str(p.get("inventoryId") or "")),
                )
            if m == Methods.SOURCE_SEMANTIC_GET_UNIT:
                return EngineResponse.ok(
                    request.id, result=self.source_semantic_get_unit(str(p.get("unitId") or "")),
                )
            if m == Methods.SOURCE_SEMANTIC_GET_COVERAGE_ACCOUNTS:
                return EngineResponse.ok(request.id, result=self.source_semantic_get_coverage_accounts(
                    str(p.get("inventoryId") or ""),
                ))
            if m == Methods.SOURCE_SEMANTIC_GET_DIAGNOSTICS:
                return EngineResponse.ok(request.id, result=self.source_semantic_get_diagnostics(
                    str(p.get("inventoryId") or ""),
                ))
            if m == Methods.TARGET_SEMANTIC_BUILD_RANGE:
                return EngineResponse.ok(request.id, result=self.target_semantic_build_range(
                    str(p.get("chapter") or ""), str(p.get("verse") or ""),
                    str(p.get("endChapter") or ""), str(p.get("endVerse") or ""),
                ))
            if m == Methods.TARGET_SEMANTIC_GET_RANGE:
                return EngineResponse.ok(request.id, result=self.target_semantic_get_range(
                    str(p.get("inventoryId") or ""),
                ))
            if m == Methods.TARGET_SEMANTIC_GET_UNIT:
                return EngineResponse.ok(request.id, result=self.target_semantic_get_unit(
                    str(p.get("unitId") or ""),
                ))
            if m == Methods.TARGET_SEMANTIC_GET_DIAGNOSTICS:
                return EngineResponse.ok(request.id, result=self.target_semantic_get_diagnostics(
                    str(p.get("inventoryId") or ""),
                ))
            if m == Methods.TARGET_SEMANTIC_GET_SEARCH_SPANS:
                return EngineResponse.ok(request.id, result=self.target_semantic_get_search_spans(
                    str(p.get("inventoryId") or ""),
                ))
            if m == Methods.TARGET_SEMANTIC_GET_CAPABILITIES:
                return EngineResponse.ok(request.id, result=self.target_semantic_get_capabilities(
                    str(p.get("inventoryId") or ""),
                ))
            if m == Methods.SEMANTIC_LOCATION_RUN_RANGE:
                budget = p.get("maxCandidateEvaluations")
                return EngineResponse.ok(request.id, result=self.semantic_location_run_range(
                    str(p.get("chapter") or ""), str(p.get("verse") or ""),
                    str(p.get("endChapter") or ""), str(p.get("endVerse") or ""),
                    int(budget) if budget is not None else None,
                ))
            if m == Methods.SEMANTIC_LOCATION_STATUS:
                return EngineResponse.ok(request.id, result=self.semantic_location_status(
                    str(p.get("runId") or ""),
                ))
            if m == Methods.SEMANTIC_LOCATION_GET_RANGE:
                return EngineResponse.ok(request.id, result=self.semantic_location_get_range(
                    str(p.get("runId") or ""),
                ))
            if m == Methods.SEMANTIC_LOCATION_GET_RELATIONSHIP:
                return EngineResponse.ok(request.id, result=self.semantic_location_get_relationship(
                    str(p.get("relationshipId") or ""),
                ))
            if m == Methods.SEMANTIC_LOCATION_GET_CANDIDATES:
                return EngineResponse.ok(request.id, result=self.semantic_location_get_candidates(
                    str(p.get("runId") or ""), str(p.get("sourceOwnerUnitId") or ""),
                ))
            if m == Methods.SEMANTIC_LOCATION_GET_DIAGNOSTICS:
                return EngineResponse.ok(request.id, result=self.semantic_location_get_diagnostics(
                    str(p.get("runId") or ""),
                ))
            if m == Methods.MEANING_ANALYSIS_RUN_RANGE:
                return EngineResponse.ok(request.id, result=self.meaning_analysis_run_range(
                    str(p.get("chapter") or ""), str(p.get("verse") or ""),
                    str(p.get("endChapter") or ""), str(p.get("endVerse") or ""),
                    str(p.get("locationRunId") or ""),
                ))
            if m == Methods.MEANING_ANALYSIS_STATUS:
                return EngineResponse.ok(request.id, result=self.meaning_analysis_status(
                    str(p.get("runId") or ""),
                ))
            if m == Methods.MEANING_ANALYSIS_GET_RANGE:
                return EngineResponse.ok(request.id, result=self.meaning_analysis_get_range(
                    str(p.get("runId") or ""),
                ))
            if m == Methods.MEANING_ANALYSIS_GET_ASSESSMENT:
                return EngineResponse.ok(request.id, result=self.meaning_analysis_get_assessment(
                    str(p.get("assessmentId") or ""),
                ))
            if m == Methods.MEANING_ANALYSIS_GET_COMPONENTS:
                return EngineResponse.ok(request.id, result=self.meaning_analysis_get_components(
                    str(p.get("assessmentId") or ""),
                ))
            if m == Methods.MEANING_ANALYSIS_GET_DIAGNOSTICS:
                return EngineResponse.ok(request.id, result=self.meaning_analysis_get_diagnostics(
                    str(p.get("runId") or ""),
                ))
            if m == Methods.QA_AUDIT_RUN_RANGE:
                return EngineResponse.ok(request.id, result=self.qa_audit_run_range(
                    str(p.get("chapter") or ""), str(p.get("verse") or ""),
                    str(p.get("endChapter") or ""), str(p.get("endVerse") or ""),
                    str(p.get("meaningRunId") or ""),
                ))
            if m == Methods.QA_AUDIT_STATUS:
                return EngineResponse.ok(request.id, result=self.qa_audit_status(
                    str(p.get("runId") or ""),
                ))
            if m == Methods.QA_AUDIT_GET_RANGE:
                return EngineResponse.ok(request.id, result=self.qa_audit_get_range(
                    str(p.get("runId") or ""),
                ))
            if m == Methods.QA_AUDIT_GET_SOURCE_COVERAGE:
                return EngineResponse.ok(request.id, result=self.qa_audit_get_source_coverage(
                    str(p.get("runId") or ""),
                ))
            if m == Methods.QA_AUDIT_GET_TARGET_SUPPORT:
                return EngineResponse.ok(request.id, result=self.qa_audit_get_target_support(
                    str(p.get("runId") or ""),
                ))
            if m == Methods.QA_AUDIT_GET_FINDING:
                return EngineResponse.ok(request.id, result=self.qa_audit_get_finding(
                    str(p.get("findingId") or ""),
                ))
            if m == Methods.QA_AUDIT_GET_DIAGNOSTICS:
                return EngineResponse.ok(request.id, result=self.qa_audit_get_diagnostics(
                    str(p.get("runId") or ""),
                ))
            if m == Methods.QA_REVIEW_GET_QUEUE:
                return EngineResponse.ok(request.id, result=self.qa_review_get_queue(
                    book=str(p.get("book") or ""),
                    chapter=(int(p["chapter"]) if p.get("chapter") not in (None, "") else None),
                    canonical_references=tuple(p.get("canonicalReferences") or ()),
                    kinds=tuple(p.get("kinds") or ()),
                    severities=tuple(p.get("severities") or ()),
                    coverage_dimensions=tuple(p.get("coverageDimensions") or ()),
                    dispositions=tuple(p.get("dispositions") or ()),
                    review_statuses=tuple(p.get("reviewStatuses") or ()),
                    lifecycle_statuses=tuple(p.get("lifecycleStatuses") or ()),
                    order=str(p.get("order") or "CANONICAL"),
                    limit=int(p.get("limit") or 50), cursor=str(p.get("cursor") or ""),
                ))
            if m == Methods.QA_REVIEW_GET_FINDING:
                return EngineResponse.ok(request.id, result=self.qa_review_get_finding(
                    str(p.get("findingId") or ""),
                ))
            if m == Methods.QA_REVIEW_DECIDE_FINDING:
                return EngineResponse.ok(request.id, result=self.qa_review_decide_finding(
                    str(p.get("findingId") or ""), str(p.get("disposition") or ""),
                    expected_revision=int(p.get("expectedEntityRevision") or 0),
                    expected_target_content_hashes=tuple(p.get("expectedTargetContentHashes") or ()),
                    note=str(p.get("note") or ""), promote=bool(p.get("promote") or False),
                ))
            if m == Methods.QA_REVIEW_ADD_NOTE:
                return EngineResponse.ok(request.id, result=self.qa_review_add_note(
                    str(p.get("entityType") or ""), str(p.get("entityId") or ""),
                    str(p.get("note") or ""),
                ))
            if m == Methods.CORRECTION_GET_ELIGIBILITY:
                return EngineResponse.ok(request.id, result=self.correction_get_eligibility(
                    str(p.get("findingId") or ""),
                ))
            if m == Methods.CORRECTION_GET_PROPOSAL:
                return EngineResponse.ok(request.id, result=self.correction_get_proposal(
                    str(p.get("proposalId") or ""),
                ))
            if m == Methods.CORRECTION_LIST_FOR_FINDING:
                return EngineResponse.ok(request.id, result=self.correction_list_for_finding(
                    str(p.get("findingId") or ""),
                ))
            if m == Methods.CORRECTION_GET_REVIEW_CONTEXT:
                return EngineResponse.ok(request.id, result=self.correction_get_review_context(
                    str(p.get("findingId") or ""),
                ))
            if m == Methods.CORRECTION_CREATE_PROPOSAL:
                return EngineResponse.ok(request.id, result=self.correction_create_proposal(
                    finding_id=str(p.get("findingId") or ""),
                    intent=CorrectionWordingService._intent_from_wire(p.get("intent") or {}),
                    human_proposed_text=str(p.get("humanProposedText") or ""),
                    explanation=str(p.get("explanation") or ""),
                    request_suggestion=bool(p.get("requestSuggestion") or False),
                    actor_id=str(p.get("actorId") or self.current_actor_id()),
                ))
            if m == Methods.CORRECTION_EDIT_PROPOSAL:
                return EngineResponse.ok(request.id, result=self.correction_edit_proposal(
                    str(p.get("proposalId") or ""),
                    proposed_text=str(p.get("proposedText") or ""),
                    explanation=str(p.get("explanation") or ""),
                    expected_revision=int(p.get("expectedProposalRevision") or 0),
                    actor_id=str(p.get("actorId") or self.current_actor_id()),
                ))
            if m == Methods.CORRECTION_REJECT_PROPOSAL:
                return EngineResponse.ok(request.id, result=self.correction_reject_proposal(
                    str(p.get("proposalId") or ""),
                    expected_revision=int(p.get("expectedProposalRevision") or 0),
                    actor_id=str(p.get("actorId") or self.current_actor_id()),
                    reason=str(p.get("reason") or p.get("note") or ""),
                ))
            if m == Methods.CORRECTION_REGENERATE_PROPOSAL:
                return EngineResponse.ok(request.id, result=self.correction_regenerate_proposal(
                    str(p.get("proposalId") or ""),
                    expected_revision=int(p.get("expectedProposalRevision") or 0),
                    actor_id=str(p.get("actorId") or self.current_actor_id()),
                ))
            if m == Methods.CORRECTION_GET_PROPOSAL_HISTORY:
                return EngineResponse.ok(request.id, result=self.correction_get_proposal_history(
                    str(p.get("proposalId") or ""),
                ))
            if m == Methods.CORRECTION_APPLY_PROPOSAL:
                actor = p.get("actor") or {
                    "actorType": "HUMAN",
                    "actorId": str(p.get("actorId") or self.current_actor_id()),
                }
                return EngineResponse.ok(request.id, result=self.correction_apply_proposal(
                    proposal_id=str(p.get("proposalId") or ""),
                    expected_proposal_revision=int(p.get("expectedProposalRevision") or 0),
                    finding_id=str(p.get("findingId") or ""),
                    expected_finding_revision=int(p.get("expectedFindingRevision") or 0),
                    application_id=str(p.get("applicationId") or ""),
                    actor=dict(actor),
                ))
            if m == Methods.CORRECTION_GET_APPLICATION_STATUS:
                return EngineResponse.ok(request.id, result=self.correction_get_application_status(
                    str(p.get("applicationId") or ""),
                ))
            if m == Methods.CORRECTION_REANALYZE_AFFECTED:
                return EngineResponse.ok(request.id, result=self.correction_reanalyze_affected(
                    str(p.get("applicationId") or ""),
                    str(p.get("requestedBy") or self.current_actor_id()),
                    bool(p.get("retry") or False),
                ))
            if m == Methods.CORRECTION_VERIFY_APPLICATION:
                return EngineResponse.ok(request.id, result=self.correction_verify_application(
                    str(p.get("applicationId") or ""),
                    str(p.get("requestedBy") or self.current_actor_id()),
                ))
            if m == Methods.CORRECTION_GET_VERIFICATION:
                return EngineResponse.ok(request.id, result=self.correction_get_verification(
                    str(p.get("applicationId") or ""),
                ))
            if m == Methods.CORRECTION_ACKNOWLEDGE_CORRECTED:
                actor = p.get("actor") or {
                    "actorType": "HUMAN",
                    "actorId": str(p.get("actorId") or self.current_actor_id()),
                }
                return EngineResponse.ok(request.id, result=self.correction_acknowledge_corrected(
                    application_id=str(p.get("applicationId") or ""),
                    verification_id=str(p.get("verificationId") or ""),
                    expected_verification_revision=int(p.get("expectedVerificationRevision") or 0),
                    expected_finding_revision=int(p.get("expectedFindingRevision") or 0),
                    actor=dict(actor), note=str(p.get("note") or ""),
                ))
            if m == Methods.SEMANTIC_REVIEW_DECIDE_LOCATION:
                return EngineResponse.ok(request.id, result=self.semantic_review_decide_location(
                    str(p.get("relationshipId") or ""), str(p.get("decision") or ""),
                    expected_revision=int(p.get("expectedEntityRevision") or 0),
                    note=str(p.get("note") or ""),
                    selected_candidate_id=str(p.get("selectedCandidateId") or ""),
                ))
            if m == Methods.SEMANTIC_REVIEW_DECIDE_MEANING:
                return EngineResponse.ok(request.id, result=self.semantic_review_decide_meaning(
                    str(p.get("assessmentId") or ""), str(p.get("meaningStatus") or ""),
                    expected_revision=int(p.get("expectedEntityRevision") or 0),
                    note=str(p.get("note") or ""),
                ))
            if m == Methods.REVIEW_HISTORY_GET_ENTITY_HISTORY:
                return EngineResponse.ok(request.id, result=self.review_history_get_entity_history(
                    str(p.get("entityType") or ""), str(p.get("entityId") or ""),
                ))
            if m == Methods.ANALYSIS_JOB_START:
                return EngineResponse.ok(request.id, result=self.analysis_job_start(
                    dict(p.get("requestedScope") or {}),
                    str(p.get("expectedAnalysisFingerprint") or ""),
                ))
            if m == Methods.ANALYSIS_JOB_STATUS:
                return EngineResponse.ok(request.id, result=self.analysis_job_status(
                    str(p.get("jobId") or ""),
                ))
            if m == Methods.ANALYSIS_JOB_CANCEL:
                return EngineResponse.ok(request.id, result=self.analysis_job_cancel(
                    str(p.get("jobId") or ""),
                ))
            if m == Methods.ANALYSIS_JOB_GET_RECENT:
                return EngineResponse.ok(request.id, result=self.analysis_job_get_recent(
                    int(p.get("limit") or 20),
                ))
            if m == Methods.ANALYSIS_JOB_GET_SCOPE_STATUS:
                return EngineResponse.ok(request.id, result=self.analysis_job_get_scope_status(
                    dict(p.get("requestedScope") or {}),
                ))
            if m == Methods.ISSUE_RESOLUTION_LIST:
                return EngineResponse.ok(
                    request.id, result=self.list_issue_resolutions(p["chapter"], p["verse"]),
                )
            if m == Methods.ISSUE_RESOLUTION_SAVE:
                return EngineResponse.ok(request.id, result=self.save_issue_resolution(
                    p["chapter"], p["verse"], p["tool"], p["groupId"], p["checkId"],
                    p.get("expectedFingerprint", ""), p.get("selectedText", ""),
                    p.get("issueSummary", ""), p.get("reviewerNote", ""),
                    p.get("proposedCorrection", ""), p.get("evidence", []),
                ))
            if m == Methods.ISSUE_RESOLUTION_QUEUE_PARATEXT:
                return EngineResponse.ok(request.id, result=self.queue_issue_resolution_for_paratext(
                    p["chapter"], p["verse"], p["resolutionId"], p.get("expectedProjectId", ""),
                ))
            if m == Methods.ISSUE_RESOLUTION_RETRY_PARATEXT:
                return EngineResponse.ok(request.id, result=self.retry_issue_resolution_paratext(
                    p["chapter"], p["verse"], p["resolutionId"],
                ))
            if m == Methods.PARATEXT_GET_STATE:
                return EngineResponse.ok(request.id, result=self.paratext_get_state())
            if m == Methods.PARATEXT_SET_REFERENCE:
                return EngineResponse.ok(request.id, result=self.paratext_set_reference(
                    p["reference"], p.get("originId", ""),
                ))
            if m == Methods.LOGOS_GET_STATE:
                return EngineResponse.ok(request.id, result=self.logos_get_state())
            if m == Methods.LOGOS_SET_REFERENCE:
                return EngineResponse.ok(request.id, result=self.logos_set_reference(
                    p["reference"], p.get("originId", ""),
                ))
            if m in (Methods.NAVIGATION_STATUS, Methods.NAVIGATION_POLL):
                return EngineResponse.ok(request.id, result=self.navigation_status(
                    str(p.get("context") or ""),
                ))
            if m == Methods.NAVIGATION_BRIDGE_CHANGED:
                return EngineResponse.ok(request.id, result=self.navigation_bridge_changed(
                    p.get("reference", ""),
                ))
            if m == Methods.NAVIGATION_RESOLVE:
                return EngineResponse.ok(request.id, result=self.navigation_resolve(
                    str(p.get("requestId") or ""), bool(p.get("accepted", False)),
                    str(p.get("bridgeReference") or ""), str(p.get("context") or ""),
                ))

            return EngineResponse.fail(request.id, "unknown_method", f"No handler for '{m}'")
        except ProjectError as exc:
            return EngineResponse.fail(request.id, "project_error", str(exc))
        except AlignmentError as exc:
            return EngineResponse.fail(request.id, "alignment_error", str(exc))
        except AIError as exc:
            return EngineResponse.fail(request.id, "ai_error", str(exc))
        except KnowledgeBaseError as exc:
            return EngineResponse.fail(request.id, "knowledge_base_error", str(exc))
        except ParatextConnectorError as exc:
            return EngineResponse.fail(request.id, "paratext_connector_error", str(exc))
        except LogosConnectorError as exc:
            return EngineResponse.fail(request.id, "logos_connector_error", str(exc))
        except UsfmCheckerError as exc:
            return EngineResponse.fail(request.id, "checker_error", str(exc))
        except versification_tool.VersificationUnavailable as exc:
            return EngineResponse.fail(request.id, "versification_unavailable", str(exc))
        except CollectionJobConflict as exc:
            return EngineResponse.fail(request.id, "job_conflict", str(exc))
        except CollectionJobError as exc:
            return EngineResponse.fail(request.id, "job_error", str(exc))
        except CheckJobNotFound as exc:
            return EngineResponse.fail(request.id, "job_not_found", str(exc))
        except CheckJobConflict as exc:
            return EngineResponse.fail(request.id, "job_conflict", str(exc))
        except CheckJobError as exc:
            return EngineResponse.fail(request.id, "job_error", str(exc))
        except AIReviewJobNotFound as exc:
            return EngineResponse.fail(request.id, "ai_job_not_found", str(exc))
        except AIReviewJobConflict as exc:
            return EngineResponse.fail(request.id, "ai_job_conflict", str(exc))
        except AIReviewJobError as exc:
            return EngineResponse.fail(request.id, "ai_job_error", str(exc))
        except ReportJobNotFound as exc:
            return EngineResponse.fail(request.id, "report_not_found", str(exc))
        except ReportJobConflict as exc:
            return EngineResponse.fail(request.id, "report_conflict", str(exc))
        except ReportNotReady as exc:
            return EngineResponse.fail(request.id, "report_not_ready", str(exc))
        except ReportJobError as exc:
            return EngineResponse.fail(request.id, "report_error", str(exc))
        except TriageJobNotFound as exc:
            return EngineResponse.fail(request.id, "triage_not_found", str(exc))
        except TriageJobConflict as exc:
            return EngineResponse.fail(request.id, "triage_conflict", str(exc))
        except TriageJobError as exc:
            return EngineResponse.fail(request.id, "triage_error", str(exc))
        except AnalysisJobNotFound as exc:
            return EngineResponse.fail(request.id, "analysis_job_not_found", str(exc))
        except AnalysisJobConflict as exc:
            return EngineResponse.fail(request.id, "analysis_job_conflict", str(exc))
        except AnalysisJobError as exc:
            return EngineResponse.fail(request.id, "analysis_job_error", str(exc))
        except WorkbenchConflict as exc:
            return EngineResponse.fail(request.id, "revision_conflict", str(exc))
        except WorkbenchValidationError as exc:
            return EngineResponse.fail(request.id, "workbench_validation_error", str(exc))
        except FoundationConflict as exc:
            # Optimistic concurrency: a human review decision written against a
            # revision that has since moved is rejected, never merged blindly.
            return EngineResponse.fail(request.id, "revision_conflict", str(exc))
        except FoundationValidationError as exc:
            return EngineResponse.fail(request.id, "semantic_validation_error", str(exc))
        except Exception as exc:  # noqa: BLE001 - protocol boundary must never crash the sidecar
            return EngineResponse.fail(request.id, "internal_error", str(exc))
