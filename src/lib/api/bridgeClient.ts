import type {
  LanguageQaDecisionIssue, LanguageQaHistory, LanguageQaInline, LanguageQaStatus, LanguageQaVerse, LanguageQaView,
} from "../types/languageQa";
import type { CollectionQaSnapshot } from "../types/collectionQa";
import type {
  HouseStyleEntry, HouseStyleEntryInput, HouseStyleListResponse,
} from "../types/houseStyle";

/** One thing that blocks an export (reporting.publication_gate, layered-rules 4.5). */
export interface ExportGateItem {
  source: "aiReview" | "languageQa" | "translationHelps" | string;
  reference: string;
  summary: string;
}

export interface ExportResult {
  written: boolean;
  path: string;
  chapters?: number;
  /** True when blocking items are open and no override was given: nothing was written. */
  blocked?: boolean;
  /** True when it was written over blocking items, with the override recorded. */
  overridden?: boolean;
  gate?: { blocking: boolean; items: ExportGateItem[]; counts: Record<string, number> };
}
import type {
  AIReviewChapterResponse,
  AIReviewJobSnapshot,
  AlignmentAiProposal,
  AlignmentAiProposeResponse,
  AlignmentContext,
  AlignmentRange,
  AlignmentStatusResponse,
  CrossVerseLinkResult,
  CrossVerseAiProposalResult,
  CrossVerseProposalResult,
  BookProgressEntry,
  CheckJobSnapshot,
  DesktopConnectorState,
  ImportMetadata,
  ImportPreview,
  CheckSelectionMutation,
  CheckSelectionValidation,
  CheckTargetSelection,
  LexiconEntryResponse,
  NativeCheckListResponse,
  NativeCheckTool,
  NavigationSyncState,
  ProjectInfo,
  RegisteredProject,
  VerseAlignment,
  VerseData,
  VerseHeading,
  QaFinding,
  SettingsData,
  TerminologyRule,
  IssueResolutionHandoffResult,
  IssueResolutionListResponse,
  IssueResolutionRecord,
  ProjectReport,
} from "../types/finding";
import type {
  DecideFindingResult,
  QaFindingDetail,
  ReviewEntityType,
  ReviewQueueFilters,
  ReviewQueuePage,
  ReviewRecord,
  ReviewerDecision,
} from "../types/qaReview";
import type {
  TargetSemanticInventory,
  SemanticLocationRun,
} from "../types/passageSemanticV1";
import type {
  AnalysisJobSnapshot,
  AnalysisScope,
  AnalysisScopeStatus,
} from "../types/analysisJob";
import type {
  CorrectionEligibility,
  CorrectionAffectedAnalysisResult,
  CorrectionVerificationState,
  CorrectionApplicationIntent,
  CorrectionIntent,
  CorrectionProposal,
  CorrectionProposalEvent,
  CorrectionReviewContext,
} from "../types/correctionReview";
import type {
  ReportExportColumn,
  ReportExportResult,
  ReportGetResponse,
  ReportJobSnapshot,
  TriageJobSnapshot,
  TriageOverrideResponse,
  TriageResultsResponse,
  TriageRunResponse,
} from "../types/report";
import type {
  TriageOverrideVerdict,
} from "../types/finding";
import type {
  ExportRow,
} from "../utils/reportStats";

/**
 * Thin wrapper around Tauri's invoke(). Every BridgeEngine protocol method
 * (engine/bridge_service.py) goes through the one generic `engine_call`
 * command in src-tauri/src/commands.rs, which forwards the method name and
 * params to the sidecar unchanged (#115); the handful of named commands left
 * are native OS dialogs and the sidecar's own log. This file is the ONLY
 * place that imports @tauri-apps/api — swapping to an HTTP transport for a
 * future web build means rewriting this file only, not any component.
 */

async function invoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  const { invoke: tauriInvoke } = await import("@tauri-apps/api/core");
  return tauriInvoke<T>(cmd, args);
}

export type FileDropPhase = "over" | "drop" | "leave";

/**
 * Native OS drag-and-drop (dropping a file/folder from Explorer onto the window),
 * not the HTML5 drag/drop DOM API — Tauri's webview intercepts that itself. `onDrop`
 * fires with the dropped path(s) once the drop completes; `onPhaseChange` is optional
 * and only useful for "is something being dragged over the window right now" styling.
 * Returns an unlisten function the caller must invoke on unmount.
 */
async function onFileDrop(
  onDrop: (paths: string[]) => void,
  onPhaseChange?: (phase: FileDropPhase) => void,
): Promise<() => void> {
  const { getCurrentWebview } = await import("@tauri-apps/api/webview");
  return getCurrentWebview().onDragDropEvent((event) => {
    const payload = event.payload;
    if (payload.type === "drop") {
      onPhaseChange?.("drop");
      onDrop(payload.paths);
    } else if (payload.type === "over") {
      onPhaseChange?.("over");
    } else if (payload.type === "leave") {
      onPhaseChange?.("leave");
    }
  });
}

export interface EngineLogEntry {
  ts_ms: number;
  level: "info" | "warn" | "error";
  message: string;
}

/** V11-011: what's actually running in this process right now, read from
 * the same constants the rest of the sidecar uses (BRIDGE_VERSION,
 * DATABASE_SCHEMA_VERSION) -- not a separately maintained build stamp. */
export interface EngineInfo {
  bridgeVersion: string;
  companionSchemaVersion: number;
  projectOpen: boolean;
  greekRoom: Record<string, unknown>;
}

/** Live diagnostics entries as sidecar.rs records them (spawn/restart/
 * terminate, request timeouts, relayed stderr). Fires after the initial
 * `engineLogRecent()` fetch, so the panel never needs to poll. */
async function onEngineLog(onEntry: (entry: EngineLogEntry) => void): Promise<() => void> {
  const { listen } = await import("@tauri-apps/api/event");
  return listen<EngineLogEntry>("engine-log", (event) => onEntry(event.payload));
}

/** Fires when sidecar.rs silently respawns bridge-engine.exe mid-session
 * (it crashed, or something else killed it) — the new process has no
 * project open, so anything project-scoped will fail until it's reopened. */
async function onEngineRespawned(onRespawn: () => void): Promise<() => void> {
  const { listen } = await import("@tauri-apps/api/event");
  return listen("engine-respawned", () => onRespawn());
}

interface EngineEnvelope<T> {
  id: string;
  success: boolean;
  result?: T;
  findings?: QaFinding[];
  error?: { code: string; message: string };
}

/** Every engine protocol method the client can name. Adding a method here
 * and a `bridge.*` wrapper below is the whole client-side cost of a new RPC;
 * the engine's dispatcher and sidecar.rs's timeout table key on the same
 * string. A typo fails to compile instead of failing at runtime. */
export type EngineMethod =
  | "languageQa.status"
  | "languageQa.pause"
  | "languageQa.inline"
  | "languageQa.history"
  | "languageQa.verse"
  | "languageQa.setPack"
  | "housestyle.list"
  | "housestyle.nameSuggestions"
  | "housestyle.record"
  | "housestyle.setState"
  | "housestyle.export"
  | "housestyle.import"
  | "collection.runChecks"
  | "collection.qaStatus"
  | "collection.pauseChecks"
  | "collection.cancelChecks"
  | "ai.review.cancel"
  | "ai.review.listForChapter"
  | "ai.review.retry"
  | "ai.review.start"
  | "ai.review.status"
  | "alignment.aiApplyProposal"
  | "alignment.aiPropose"
  | "alignment.crossVerse.aiPropose"
  | "alignment.crossVerse.link"
  | "alignment.crossVerse.propose"
  | "alignment.crossVerse.unlink"
  | "alignment.gapScan"
  | "alignment.get"
  | "alignment.getRange"
  | "alignment.realign"
  | "alignment.restore"
  | "alignment.status"
  | "alignment.unalign"
  | "alignment.undo"
  | "analysisJob.cancel"
  | "analysisJob.getScopeStatus"
  | "analysisJob.start"
  | "analysisJob.status"
  | "chapter.verseData"
  | "chapter.verses"
  | "check.clearSelection"
  | "check.listForVerse"
  | "check.saveSelection"
  | "check.validateSelection"
  | "checks.cancel"
  | "checks.retry"
  | "checks.start"
  | "checks.status"
  | "correction.acknowledgeCorrected"
  | "correction.applyProposal"
  | "correction.createProposal"
  | "correction.editProposal"
  | "correction.getApplicationStatus"
  | "correction.getEligibility"
  | "correction.getProposal"
  | "correction.getProposalHistory"
  | "correction.getReviewContext"
  | "correction.getVerification"
  | "correction.listForFinding"
  | "correction.reanalyzeAffected"
  | "correction.regenerateProposal"
  | "correction.rejectProposal"
  | "correction.verifyApplication"
  | "engine.info"
  | "export.aligned"
  | "export.nonAligned"
  | "issueResolution.list"
  | "issueResolution.queueParatext"
  | "issueResolution.retryParatext"
  | "issueResolution.save"
  | "lexicon.getEntry"
  | "navigation.bridgeChanged"
  | "navigation.poll"
  | "navigation.resolve"
  | "navigation.status"
  | "paratext.getState"
  | "ping"
  | "project.delete"
  | "project.forget"
  | "project.import"
  | "project.inspectImport"
  | "project.list"
  | "project.listBookProgress"
  | "project.open"
  | "project.report"
  | "qaReview.addNote"
  | "qaReview.decideFinding"
  | "qaReview.getFinding"
  | "qaReview.getQueue"
  | "report.cancel"
  | "report.export"
  | "report.generate"
  | "report.get"
  | "report.status"
  | "semanticLocation.getRange"
  | "settings.get"
  | "settings.set"
  | "targetSemantic.getRange"
  | "terminology.list"
  | "terminology.record"
  | "triage.cancel"
  | "triage.override"
  | "triage.results"
  | "triage.run"
  | "triage.status"
  | "verse.decide"
  | "verse.edit"
  | "verse.get"
  | "verse.runChecks";

/** Send one engine request through `engine_call` and unwrap the envelope.
 * Keys with `undefined` values are dropped by JSON serialization, and the
 * engine's dispatcher fills the same defaults the old per-method Rust
 * forwarders did, so callers pass their argument objects straight through. */
async function call<T>(method: EngineMethod, params?: Record<string, unknown>): Promise<T> {
  const envelope = await invoke<EngineEnvelope<T>>("engine_call", { method, params: params ?? {} });
  if (!envelope.success) {
    throw new Error(envelope.error?.message ?? `${method} failed`);
  }
  return (envelope.result ?? (envelope as unknown)) as T;
}

export const bridge = {
  /** One page of one list: every open finding, only those shown again for
   * re-checking, or those marked as false positives. */
  languageQaStatus(
    projectPath: string, offset = 0, limit = 0, view: LanguageQaView = "findings",
    filters: { chapter?: string; categories?: string[] } = {},
  ): Promise<LanguageQaStatus> {
    // Filters are sent only when set: the panel's scope switch and kind legend.
    const params: Record<string, unknown> = { projectPath, offset, limit, view };
    if (filters.chapter !== undefined) params.chapter = filters.chapter;
    if (filters.categories !== undefined) params.categories = filters.categories;
    return call("languageQa.status", params);
  },

  /** Every decision recorded on this verse's Language QA findings (or one of
   * them), oldest first. Read-only. */
  languageQaHistory(
    projectPath: string, chapter: string, verse: string, findingId?: string,
  ): Promise<LanguageQaHistory> {
    return call("languageQa.history", { projectPath, chapter, verse, findingId });
  },

  languageQaPause(projectPath: string, paused: boolean): Promise<LanguageQaStatus> {
    return call("languageQa.pause", { projectPath, paused });
  },

  /** The project's Language QA setting (manifest language_qa.pack): "auto",
   * "off" or a registered pack name. The engine rebinds and starts a pass. */
  languageQaSetPack(projectPath: string, pack: string): Promise<LanguageQaStatus> {
    return call("languageQa.setPack", { projectPath, pack });
  },

  /** Every inline-rule finding for `chapter` (the whole book when omitted),
   * unpaged -- what the verse marks are drawn from. */
  languageQaInline(projectPath: string, chapter?: string): Promise<LanguageQaInline> {
    return call("languageQa.inline", { projectPath, chapter });
  },

  /** Check every book of the open collection in turn (layered-rules 4.4). */
  collectionRunChecks(checks: string[], force = false): Promise<CollectionQaSnapshot> {
    return call("collection.runChecks", { checks, force });
  },

  /** The run's snapshot; with jobId "" and no run, each book's last recorded run. */
  collectionQaStatus(jobId: string): Promise<CollectionQaSnapshot> {
    return call("collection.qaStatus", { jobId });
  },

  collectionPauseChecks(paused: boolean): Promise<CollectionQaSnapshot> {
    return call("collection.pauseChecks", { paused });
  },

  collectionCancelChecks(): Promise<CollectionQaSnapshot> {
    return call("collection.cancelChecks", {});
  },

  /** Every Language QA finding of one verse, inline or not, for the review panel. */
  languageQaVerse(projectPath: string, chapter: string, verse: string): Promise<LanguageQaVerse> {
    return call("languageQa.verse", { projectPath, chapter, verse });
  },

  ping(): Promise<{ pong: boolean }> {
    return call("ping");
  },

  onFileDrop,
  onEngineLog,
  onEngineRespawned,

  engineInfo(): Promise<EngineInfo> {
    return call("engine.info");
  },

  engineLogRecent(limit?: number): Promise<EngineLogEntry[]> {
    return invoke<EngineLogEntry[]>("engine_log_recent", { limit });
  },

  async pickProjectFolder(): Promise<string | null> {
    return invoke<string | null>("pick_project_folder");
  },

  async pickImportFile(): Promise<string | null> {
    return invoke<string | null>("pick_import_file");
  },

  openProject(path: string, projectId?: string): Promise<ProjectInfo> {
    return call("project.open", { path, projectId });
  },

  listProjects(): Promise<{ projects: RegisteredProject[] }> {
    return call("project.list");
  },

  listBookProgress(): Promise<{ books: BookProgressEntry[] }> {
    return call("project.listBookProgress");
  },

  forgetProject(projectId: string): Promise<{ forgotten: boolean }> {
    return call("project.forget", { projectId });
  },

  deleteProject(projectId: string): Promise<{ deleted: boolean; managed: boolean }> {
    return call("project.delete", { projectId });
  },

  projectReport(): Promise<ProjectReport> {
    return call("project.report");
  },

  // --- Whole-collection QA report (engine/tc_ai_bridge/qa_report.py) -------
  // A background build: generate, poll status, fetch once with get. Export
  // writes the rows the report screen has filtered down to.

  reportGenerate(): Promise<ReportJobSnapshot> {
    return call("report.generate");
  },

  reportStatus(jobId: string): Promise<ReportJobSnapshot> {
    return call("report.status", { jobId });
  },

  reportGet(jobId: string): Promise<ReportGetResponse> {
    return call("report.get", { jobId });
  },

  reportCancel(jobId: string): Promise<ReportJobSnapshot> {
    return call("report.cancel", { jobId });
  },

  /** `rows` are the export-shaped rows from reportStats.exportRows, not raw
   * ReportRows: the category is a display label and any triage verdict is
   * flattened on. The engine writes exactly the columns it is given. */
  reportExport(
    outputPath: string, format: "csv" | "tsv", rows: ExportRow[],
    columns: ReportExportColumn[],
  ): Promise<ReportExportResult> {
    return call("report.export", { outputPath, format, rows, columns });
  },

  // --- AI triage (engine/tc_ai_bridge/triage.py) --------------------------
  // Optional and online-only. Verdicts are persisted per book as the run
  // produces them, so triageResults reads disk rather than a job — results
  // survive a restart, and a cancelled run keeps whatever it already paid for.

  triageRun(book = "", force = false): Promise<TriageRunResponse> {
    return call("triage.run", { book, force });
  },

  triageStatus(jobId: string): Promise<TriageJobSnapshot> {
    return call("triage.status", { jobId });
  },

  triageCancel(jobId: string): Promise<TriageJobSnapshot> {
    return call("triage.cancel", { jobId });
  },

  /** An empty verdict clears the override and returns the finding to the model. */
  triageOverride(book: string, hash: string, verdict: TriageOverrideVerdict | ""): Promise<TriageOverrideResponse> {
    return call("triage.override", { book, hash, verdict });
  },

  triageResults(book = ""): Promise<TriageResultsResponse> {
    return call("triage.results", { book });
  },

  inspectImport(path: string, metadata?: ImportMetadata): Promise<ImportPreview> {
    return call("project.inspectImport", { path, metadata });
  },

  importProject(path: string, metadata: ImportMetadata, allowDuplicate = false): Promise<ProjectInfo> {
    return call("project.import", { path, metadata, allowDuplicate });
  },

  chapterVerses(chapter: string): Promise<{ verses: string[] }> {
    return call("chapter.verses", { chapter });
  },

  /** `headings` is keyed by the verse each heading INTRODUCES, and rides on this
   *  call rather than an RPC of its own: the editor needs them exactly when it
   *  needs the verses, and this call exists to avoid a second round trip (#180).
   *  Empty for a project imported before #180, or a book with no headings. */
  chapterVerseData(chapter: string): Promise<{
    chapter: string;
    verses: Record<string, VerseData>;
    headings: Record<string, VerseHeading[]>;
  }> {
    return call("chapter.verseData", { chapter });
  },

  getVerse(chapter: string, verse: string): Promise<VerseData> {
    return call("verse.get", { chapter, verse });
  },

  async runVerseChecks(chapter: string, verse: string, checks: string[]): Promise<QaFinding[]> {
    const envelope = await invoke<EngineEnvelope<unknown>>("engine_call", {
      method: "verse.runChecks", params: { chapter, verse, checks },
    });
    if (!envelope.success) throw new Error(envelope.error?.message ?? "verse.runChecks failed");
    return envelope.findings ?? [];
  },

  startChecks(scope: "chapter" | "book", chapters: string[], checks: string[]): Promise<CheckJobSnapshot> {
    return call("checks.start", { scope, chapters, checks });
  },

  checkStatus(jobId: string): Promise<CheckJobSnapshot> {
    return call("checks.status", { jobId });
  },

  cancelChecks(jobId: string): Promise<CheckJobSnapshot> {
    return call("checks.cancel", { jobId });
  },

  retryChecks(jobId: string): Promise<CheckJobSnapshot> {
    return call("checks.retry", { jobId });
  },

  decideVerse(
    chapter: string, verse: string, findingId: string,
    status: string, comment?: string, issue?: LanguageQaDecisionIssue,
  ): Promise<Record<string, unknown>> {
    return call("verse.decide", { chapter, verse, findingId, status, comment, issue });
  },

  editVerse(chapter: string, verse: string, newText: string): Promise<{
    committed: boolean;
    issueResolutionsNeedingRecheck: number;
    /** What the reader shows for the saved text (#91); absent from an older engine. */
    display?: import("../types/finding").VerseDisplay;
  }> {
    return call("verse.edit", { chapter, verse, newText });
  },

  listChecksForVerse(chapter: string, verse: string): Promise<NativeCheckListResponse> {
    return call("check.listForVerse", { chapter, verse });
  },

  validateCheckSelection(
    chapter: string, verse: string, tool: NativeCheckTool, groupId: string, checkId: string,
    selections: CheckTargetSelection[], nothingToSelect: boolean,
  ): Promise<CheckSelectionValidation> {
    return call("check.validateSelection", {
      chapter, verse, tool, groupId, checkId, selections, nothingToSelect,
    });
  },

  saveCheckSelection(
    chapter: string, verse: string, tool: NativeCheckTool, groupId: string, checkId: string,
    selections: CheckTargetSelection[], nothingToSelect: boolean,
    provenance: "human" | "bridge_ai", expectedFingerprint: string,
    metadata: Record<string, unknown> = {},
  ): Promise<CheckSelectionMutation> {
    return call("check.saveSelection", {
      chapter, verse, tool, groupId, checkId, selections, nothingToSelect,
      provenance, expectedFingerprint, metadata,
    });
  },

  clearCheckSelection(
    chapter: string, verse: string, tool: NativeCheckTool, groupId: string, checkId: string,
    provenance: "human" | "bridge_ai", expectedFingerprint: string,
    metadata: Record<string, unknown> = {},
  ): Promise<CheckSelectionMutation> {
    return call("check.clearSelection", {
      chapter, verse, tool, groupId, checkId, provenance, expectedFingerprint, metadata,
    });
  },

  listIssueResolutions(chapter: string, verse: string): Promise<IssueResolutionListResponse> {
    return call("issueResolution.list", { chapter, verse });
  },

  saveIssueResolution(
    chapter: string, verse: string,
    check: { tool: NativeCheckTool; groupId: string; checkId: string; expectedFingerprint: string },
    values: {
      selectedText: string;
      issueSummary: string;
      reviewerNote: string;
      proposedCorrection: string;
      evidence: Array<Record<string, unknown> | string>;
    },
  ): Promise<IssueResolutionRecord> {
    return call("issueResolution.save", {
      chapter, verse, tool: check.tool, groupId: check.groupId, checkId: check.checkId,
      expectedFingerprint: check.expectedFingerprint, ...values,
    });
  },

  queueIssueResolutionForParatext(
    chapter: string, verse: string, resolutionId: string, expectedProjectId = "",
  ): Promise<IssueResolutionHandoffResult> {
    return call("issueResolution.queueParatext", {
      chapter, verse, resolutionId, expectedProjectId,
    });
  },

  retryIssueResolutionParatext(
    chapter: string, verse: string, resolutionId: string,
  ): Promise<IssueResolutionHandoffResult> {
    return call("issueResolution.retryParatext", { chapter, verse, resolutionId });
  },

  getAlignment(chapter: string, verse: string): Promise<AlignmentContext> {
    return call("alignment.get", { chapter, verse });
  },

  /** One context per verse for the Cross-verse alignment page (#116);
   *  `verses` are the opaque strings from verseNums, in the order wanted back. */
  getAlignmentRange(chapter: string, verses: string[]): Promise<AlignmentRange> {
    return call("alignment.getRange", { chapter, verses });
  },

  /** Where each unmatched source token in the range was probably realized
   *  (#138/#139). Read-only: accepting one is a separate crossVerseLink call. */
  crossVersePropose(chapter: string, verses: string[]): Promise<CrossVerseProposalResult> {
    return call("alignment.crossVerse.propose", { chapter, verses });
  },

  /** The same question asked of a model as well (#146). Still read-only: a
   *  proposal marked `autoLinkable` is applied by the caller through the same
   *  crossVerseLink below, so there is only ever one writer. */
  crossVerseAiPropose(chapter: string, verses: string[]): Promise<CrossVerseAiProposalResult> {
    return call("alignment.crossVerse.aiPropose", { chapter, verses });
  },

  /** Bridge-private cross-verse link (#117): nothing in alignmentData changes.
   *  Ids are this load's positional ids; the engine stores signatures.
   *  `origin` records what produced the link on the append-only change_log
   *  event ("ai-auto" for an agreed proposal applied without a click, #146). */
  crossVerseLink(
    source: { chapter: string; verse: string; topId: string },
    target: { chapter: string; verse: string; bottomId: string },
    origin = "",
  ): Promise<CrossVerseLinkResult> {
    return call("alignment.crossVerse.link", { source, target, origin });
  },

  crossVerseUnlink(linkId: string): Promise<CrossVerseLinkResult> {
    return call("alignment.crossVerse.unlink", { linkId });
  },

  getLexiconEntry(strong: string, morph: string): Promise<LexiconEntryResponse> {
    return call("lexicon.getEntry", { strong, morph });
  },

  alignmentStatus(chapter?: string): Promise<AlignmentStatusResponse> {
    return call("alignment.status", chapter ? { chapter } : {});
  },

  realignWords(
    chapter: string, verse: string, topIds: string[], bottomIds: string[],
    expectedOriginal: VerseAlignment,
  ): Promise<AlignmentContext> {
    return call("alignment.realign", { chapter, verse, topIds, bottomIds, expectedOriginal });
  },

  unalignWords(
    chapter: string, verse: string, bottomIds: string[], expectedOriginal: VerseAlignment,
  ): Promise<AlignmentContext> {
    return call("alignment.unalign", { chapter, verse, bottomIds, expectedOriginal });
  },

  undoAlignment(
    chapter: string, verse: string, expectedOriginal: VerseAlignment,
  ): Promise<AlignmentContext> {
    return call("alignment.undo", { chapter, verse, expectedOriginal });
  },

  restoreAlignment(
    chapter: string, verse: string, historyId: string, expectedOriginal: VerseAlignment,
  ): Promise<AlignmentContext> {
    return call("alignment.restore", { chapter, verse, historyId, expectedOriginal });
  },

  /** Read-only: nothing is written to project files. See aiApplyAlignmentProposal. */
  aiProposeAlignment(chapter: string, verse: string, mode: "gap_fill" | "audit" = "gap_fill"): Promise<AlignmentAiProposeResponse> {
    return call("alignment.aiPropose", { chapter, verse, mode });
  },

  aiApplyAlignmentProposal(
    chapter: string, verse: string, proposal: AlignmentAiProposal, expectedOriginal: VerseAlignment,
  ): Promise<AlignmentContext> {
    return call("alignment.aiApplyProposal", { chapter, verse, proposal, expectedOriginal });
  },

  startAIReview(
    scope: "verse" | "chapter" | "book", chapter: string, verse: string,
    mode: "basic" | "advanced",
  ): Promise<AIReviewJobSnapshot> {
    return call("ai.review.start", { scope, chapter, verse, mode });
  },

  aiReviewStatus(jobId: string): Promise<AIReviewJobSnapshot> {
    return call("ai.review.status", { jobId });
  },

  cancelAIReview(jobId: string): Promise<AIReviewJobSnapshot> {
    return call("ai.review.cancel", { jobId });
  },

  retryAIReview(jobId: string): Promise<AIReviewJobSnapshot> {
    return call("ai.review.retry", { jobId });
  },

  listAIReviewsForChapter(chapter: string): Promise<AIReviewChapterResponse> {
    return call("ai.review.listForChapter", { chapter });
  },

  targetSemanticGetRange(inventoryId: string): Promise<TargetSemanticInventory> {
    return call("targetSemantic.getRange", { inventoryId });
  },

  semanticLocationGetRange(runId: string): Promise<SemanticLocationRun> {
    return call("semanticLocation.getRange", { runId });
  },

  paratextGetState(): Promise<DesktopConnectorState> {
    return call("paratext.getState");
  },

  navigationStatus(context?: string): Promise<NavigationSyncState> {
    return call("navigation.status", { context });
  },

  navigationPoll(context?: string): Promise<NavigationSyncState> {
    return call("navigation.poll", { context });
  },

  navigationBridgeChanged(reference: string): Promise<NavigationSyncState> {
    return call("navigation.bridgeChanged", { reference });
  },

  navigationResolve(
    requestId: string, accepted: boolean, bridgeReference?: string, context?: string,
  ): Promise<NavigationSyncState> {
    return call("navigation.resolve", { requestId, accepted, bridgeReference, context });
  },

  getSettings(): Promise<SettingsData> {
    return call("settings.get");
  },

  setSettings(params: Record<string, unknown>): Promise<SettingsData> {
    return call("settings.set", params);
  },

  terminologyList(): Promise<{ rules: TerminologyRule[] }> {
    return call("terminology.list");
  },

  /** Without `overwrite`, an existing rule for the concept is not replaced:
   * nothing is written and it comes back as `conflict`. */
  terminologyRecord(
    conceptId: string, approvedRenderings: string[], rejectedRenderings: string[], overwrite = false,
    extra: { allowedAlternatives?: string[]; inflectedForms?: Record<string, string[]>; matchMode?: "exact" | "prefix" } = {},
  ): Promise<{ rules: TerminologyRule[]; conflict?: TerminologyRule }> {
    return call("terminology.record", { conceptId, approvedRenderings, rejectedRenderings, overwrite, ...extra });
  },

  async pickJsonFile(): Promise<string | null> {
    return invoke<string | null>("pick_json_file");
  },

  // -- house style (layered-rules 6.3/6.4) --
  housestyleList(): Promise<HouseStyleListResponse> {
    return call("housestyle.list", {});
  },
  housestyleNameSuggestions(): Promise<{ suggestions: Array<{ word: string; count: number; variants: string[] }>; checked: boolean }> {
    return call("housestyle.nameSuggestions", {});
  },
  housestyleRecord(entry: HouseStyleEntryInput): Promise<HouseStyleListResponse & { entry: HouseStyleEntry }> {
    return call("housestyle.record", { entry });
  },
  housestyleSetState(key: string, state: "active" | "removed" | "undone"): Promise<HouseStyleListResponse & { entry: HouseStyleEntry }> {
    return call("housestyle.setState", { key, state });
  },
  housestyleExport(outputPath: string): Promise<{ written: boolean; path: string; count: number }> {
    return call("housestyle.export", { outputPath });
  },
  housestyleImport(inputPath: string): Promise<HouseStyleListResponse & { imported: number }> {
    return call("housestyle.import", { inputPath });
  },

  pickSavePath(defaultName: string): Promise<string | null> {
    return invoke<string | null>("pick_save_path", { defaultName });
  },

  /** With blocking publication-gate items open and no override, nothing is
   * written and the answer is `{written: false, blocked: true, gate}`. */
  exportAligned(outputPath: string, override = false): Promise<ExportResult> {
    return call("export.aligned", { outputPath, override });
  },

  exportNonAligned(outputPath: string, override = false): Promise<ExportResult> {
    return call("export.nonAligned", { outputPath, override });
  },

  // --- Stage 8 QA audit (analysis; read-only) -------------------------------

  // --- Stage 9A human review (decisions only; never edits Scripture) --------

  qaReviewGetQueue(filters: ReviewQueueFilters = {}): Promise<ReviewQueuePage> {
    return call("qaReview.getQueue", {
      book: filters.book,
      chapter: filters.chapter,
      canonicalReferences: filters.canonicalReferences,
      kinds: filters.kinds,
      severities: filters.severities,
      coverageDimensions: filters.coverageDimensions,
      dispositions: filters.dispositions,
      reviewStatuses: filters.reviewStatuses,
      lifecycleStatuses: filters.lifecycleStatuses,
      order: filters.order,
      limit: filters.limit,
      cursor: filters.cursor,
    });
  },

  qaReviewGetFinding(findingId: string): Promise<QaFindingDetail> {
    return call("qaReview.getFinding", { findingId });
  },

  /**
   * Record a reviewer's conclusion. `expectedEntityRevision` and
   * `expectedTargetContentHashes` are what make this safe: the engine rejects
   * the write with a `revision_conflict` error rather than clobbering a
   * decision made elsewhere, or accepting one made against text that has since
   * changed. `promote` is the only route from POSSIBLY_MISSING to MISSING.
   */
  qaReviewDecideFinding(
    findingId: string,
    disposition: ReviewerDecision,
    expectedEntityRevision: number,
    options: {
      note?: string;
      promote?: boolean;
      expectedTargetContentHashes?: string[];
    } = {},
  ): Promise<DecideFindingResult> {
    return call("qaReview.decideFinding", {
      findingId,
      disposition,
      expectedEntityRevision,
      expectedTargetContentHashes: options.expectedTargetContentHashes,
      note: options.note,
      promote: options.promote,
    });
  },

  qaReviewAddNote(
    entityType: ReviewEntityType, entityId: string, note: string,
  ): Promise<{ history: ReviewRecord[] }> {
    return call("qaReview.addNote", { entityType, entityId, note });
  },

  // --- Stage 9B correction proposal review / explicit-human application ----

  correctionGetEligibility(findingId: string): Promise<CorrectionEligibility> {
    return call("correction.getEligibility", { findingId });
  },

  correctionGetReviewContext(findingId: string): Promise<CorrectionReviewContext> {
    return call("correction.getReviewContext", { findingId });
  },

  correctionGetProposal(proposalId: string): Promise<CorrectionProposal> {
    return call("correction.getProposal", { proposalId });
  },

  correctionListForFinding(
    findingId: string,
  ): Promise<{
    findingId: string;
    proposals: CorrectionProposal[];
    applications: CorrectionApplicationIntent[];
    correctionWritesBlocked: boolean;
  }> {
    return call("correction.listForFinding", { findingId });
  },

  correctionCreateProposal(options: {
    findingId: string;
    intent: CorrectionIntent;
    humanProposedText?: string;
    explanation?: string;
    requestSuggestion?: boolean;
    actorId?: string;
  }): Promise<CorrectionProposal> {
    return call("correction.createProposal", options);
  },

  correctionEditProposal(proposalId: string, options: {
    proposedText: string;
    explanation?: string;
    expectedProposalRevision: number;
    actorId?: string;
  }): Promise<CorrectionProposal> {
    return call("correction.editProposal", { proposalId, ...options });
  },

  correctionRejectProposal(proposalId: string, options: {
    expectedProposalRevision: number;
    actorId?: string;
    note?: string;
  }): Promise<CorrectionProposal> {
    return call("correction.rejectProposal", { proposalId, ...options });
  },

  correctionRegenerateProposal(proposalId: string, options: {
    expectedProposalRevision: number;
    actorId?: string;
  }): Promise<CorrectionProposal> {
    return call("correction.regenerateProposal", { proposalId, ...options });
  },

  correctionGetProposalHistory(
    proposalId: string,
  ): Promise<{ proposalId: string; events: CorrectionProposalEvent[] }> {
    return call("correction.getProposalHistory", { proposalId });
  },

  correctionApplyProposal(options: {
    proposalId: string;
    expectedProposalRevision: number;
    findingId: string;
    expectedFindingRevision: number;
    applicationId: string;
    actor: { actorType: "HUMAN"; actorId: string };
  }): Promise<CorrectionApplicationIntent> {
    return call("correction.applyProposal", options);
  },

  correctionGetApplicationStatus(applicationId: string): Promise<CorrectionApplicationIntent> {
    return call("correction.getApplicationStatus", { applicationId });
  },

  correctionReanalyzeAffected(options: {
    applicationId: string;
    requestedBy: string;
    retry?: boolean;
  }): Promise<CorrectionAffectedAnalysisResult> {
    return call("correction.reanalyzeAffected", options);
  },

  // Stage 9B.4: verification is decided by the backend. The UI renders the
  // verdict and evidence; it never computes one.
  correctionVerifyApplication(options: {
    applicationId: string;
    requestedBy: string;
  }): Promise<CorrectionVerificationState> {
    return call("correction.verifyApplication", options);
  },

  correctionGetVerification(applicationId: string): Promise<CorrectionVerificationState> {
    return call("correction.getVerification", { applicationId });
  },

  correctionAcknowledgeCorrected(options: {
    applicationId: string;
    verificationId: string;
    expectedVerificationRevision: number;
    expectedFindingRevision: number;
    actor: { actorType: "HUMAN"; actorId: string };
    note?: string;
  }): Promise<CorrectionVerificationState> {
    return call("correction.acknowledgeCorrected", options);
  },

  // Stage 9A.4 orchestrates the frozen Stage 5--8 engines. Starting a job is
  // explicit; project open only reports persisted state and never starts one.
  analysisJobStart(
    requestedScope: AnalysisScope,
    expectedAnalysisFingerprint: string,
  ): Promise<AnalysisJobSnapshot> {
    return call("analysisJob.start", { requestedScope, expectedAnalysisFingerprint });
  },

  analysisJobStatus(jobId: string): Promise<AnalysisJobSnapshot> {
    return call("analysisJob.status", { jobId });
  },

  analysisJobCancel(jobId: string): Promise<AnalysisJobSnapshot> {
    return call("analysisJob.cancel", { jobId });
  },

  analysisJobGetScopeStatus(requestedScope: AnalysisScope): Promise<AnalysisScopeStatus> {
    return call("analysisJob.getScopeStatus", { requestedScope });
  },
};
