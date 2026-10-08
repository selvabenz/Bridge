// Mirrors engine/greek_room_engine/models/finding.py and the response
// shapes bridge_service.py actually returns. Keep in sync manually.

export type FindingCategory =
  | "structure" | "unicode" | "spelling" | "names" | "repetition"
  | "alignment" | "consistency" | "omission_addition"
  | "translation_word" | "translation_note";

export type Severity = "high" | "medium" | "low" | "info";

export type FindingStatus =
  | "open" | "accepted" | "rejected" | "ignored" | "fixed" | "needs_discussion";

export interface EvidenceItem {
  label: string;
  value: string;
}

export type TriageVerdict = "true_positive" | "false_positive" | "uncertain";
/** A reviewer's thumbs up/down. Never "uncertain" — a human either agrees or doesn't. */
export type TriageOverrideVerdict = "true_positive" | "false_positive";

/**
 * One AI-triage verdict, as stored in
 * .apps/translationCoreAI/triage/<book>.json and returned by triage.results.
 * Mirrors tc_ai_bridge/triage.py's make_record. `confidence` is 0-100 and
 * expresses the model's certainty about its own verdict — it is a ranking
 * signal, not a calibrated probability.
 */
export interface TriageRecord {
  findingId: string;
  chapter: string;
  verse: string;
  checkType: string;
  family: string;
  verdict: TriageVerdict;
  confidence: number;
  reason: string;
  model: string;
  timestamp: string;
  /** Set by triage.override. Always wins over `verdict`. */
  userOverride: { verdict: TriageOverrideVerdict; timestamp: string } | null;
}

export interface QaFinding {
  id: string;
  project_id: string;
  book: string;
  chapter: number;
  verse: number;
  start_offset: number | null;
  end_offset: number | null;
  original_text: string;
  engine: string;
  check_type: string;
  category: FindingCategory;
  severity: Severity;
  confidence: number;
  suggested_replacement: string | null;
  explanation: string;
  evidence: EvidenceItem[];
  engine_version: string;
  resource_versions: Record<string, string>;
  status: FindingStatus;
  human_comment: string | null;
  created_at: string;
  resolved_at: string | null;
}

// Mirrors tc_ai_bridge/reporting.py's ReportService output. Only the
// fields the UI actually reads are typed here — the real payload carries
// more (scan, terminology, psalms, git, team, metrics, knowledgeBaseProvenance)
// that ProjectDashboard doesn't render yet.
export type CoverageState = "PASS" | "ISSUE" | "REVIEW_REQUIRED" | "NOT_CHECKED";

export interface VerseCoverage {
  counts: Record<CoverageState, number>;
  totalVerses: number;
  checkedPercent: number;
  chapters: Record<string, Record<string, CoverageState>>;
}

export interface PublicationGate {
  readyForHumanPublicationSignoff: boolean;
  criticalFindings: number;
  highFindings: number;
  staleAIReviews: number;
  pendingTransactions: number;
  openDiscussions: number;
  note: string;
}

export interface LocalFindingSummary {
  engine: string;
  severity: string;
  checkType: string;
  explanation: string;
}

/**
 * A tN/tW check that needs attention: invalidated, stale after a Scripture
 * edit, or never selected. Kept separate from LocalFindingSummary (Greek
 * Room) because the dashboard colour-codes the two sources differently.
 */
export interface HelpsFindingSummary {
  tool: "translationNotes" | "translationWords";
  category: "translation_note" | "translation_word";
  severity: string;
  checkType: string;
  groupId: string;
  explanation: string;
}

export interface ExceptionQueueRow {
  chapter: string;
  verse: string;
  critical: number;
  high: number;
  medium: number;
  cache: string;
  wordAlignment: string;
  invalidChecks: number;
  discussions: number;
  finalState: string;
  summary: string;
  localFindings: LocalFindingSummary[];
  helpsFindings: HelpsFindingSummary[];
}

export interface ProjectReport {
  project: string;
  bookId: string;
  exceptionQueue: ExceptionQueueRow[];
  qaSeverityCounts: Record<string, number>;
  needsDiscussion: Array<{ reference: string; [key: string]: unknown }>;
  publicationGate: PublicationGate;
  coverage: { verses: VerseCoverage; resources: Record<string, string> };
}

export interface CollectionReportBookSummary {
  bookId: string;
  project: string;
  coverage: VerseCoverage;
  qaSeverityCounts: Record<string, number>;
  publicationGate: PublicationGate;
}

export interface TokenRef {
  word: string;
  occurrence: number;
  occurrences: number;
  strong?: string;
  lemma?: string;
  morph?: string;
  type?: string;
}

export interface AlignmentGroup {
  topWords: TokenRef[];
  bottomWords: TokenRef[];
}

export interface VerseAlignment {
  alignments: AlignmentGroup[];
  wordBank: TokenRef[];
}

export type AlignmentWorkStatus = "complete" | "partial" | "untouched" | "invalid";

export interface AlignmentToken extends TokenRef {
  id: string;
}

export interface LexiconSegment {
  strong: string | null;
  morphLabel: string | null;
  partOfSpeech: string | null;
  lemma: string | null;
  translit: string | null;
  pron: string | null;
  meaning: string | null;
  usage: string | null;
  source: string | null;
}

export interface LexiconEntryResponse {
  languageId: string | null;
  segments: LexiconSegment[];
}

export interface AlignmentGroupView {
  id: string;
  topIds: string[];
  bottomIds: string[];
}

export interface AlignmentHistoryEntry {
  id: string;
  operation: string;
  timestamp: string;
}

export interface AlignmentCounts {
  complete: number;
  partial: number;
  untouched: number;
  invalid: number;
}

/** Per-verse gap counts (#116): source tokens with no target word, target
 *  words in no group. Computed by the engine; alignmentGroups.gapCounts is
 *  the client-side mirror. */
export interface AlignmentGaps {
  sourceUnmatched: number;
  targetUnmatched: number;
}

/** One end of a Bridge-private cross-verse link (#117): a tC token signature
 *  plus its verse. Positional ids are resolved per verse, never stored. */
export interface CrossVerseLinkEnd {
  chapter: string;
  verse: string;
  word: string;
  occurrence: number;
  occurrences: number;
  signature: string;
  strong?: string;
  lemma?: string;
  morph?: string;
}

export interface CrossVerseLink {
  id: string;
  bookId: string;
  /** #217: absent on a link written before groups, which is a group of one. */
  groupId?: string;
  relation?: CrossVerseRelation;
  source: CrossVerseLinkEnd;
  target: CrossVerseLinkEnd;
  state: "active" | "invalid";
  invalidReason?: string;
  createdAt: string;
  updatedAt: string;
  actorId: string;
  /** Set when the source is in the verse this context describes. */
  sourceTopId: string | null;
  /** Set when the target is in the verse this context describes. */
  targetBottomId: string | null;
}

export interface CrossVerseLinkResult {
  link: CrossVerseLink;
  source: AlignmentContext;
  target: AlignmentContext;
  /** #217: the composite group the call wrote. `extended` when a single pair
   *  was dropped onto a word already in a group between the same two verses. */
  group?: { groupId: string; relation: CrossVerseRelation; linkIds: string[]; extended: boolean };
}

export type CrossVerseRelation = "one-to-one" | "one-to-many" | "many-to-one" | "many-to-many";

/** #217: one cross-verse realization -- every pair row sharing a groupId,
 *  written and removed as a unit. Sources are all in one verse and targets all
 *  in one other verse. Ids are set only on the side this context describes. */
export interface CrossVerseGroup {
  groupId: string;
  state: "active" | "invalid";
  relation: CrossVerseRelation;
  sourceChapter: string;
  sourceVerse: string;
  targetChapter: string;
  targetVerse: string;
  sources: { word: string; topId: string | null }[];
  targets: { word: string; bottomId: string | null }[];
  sourceTopIds: string[];
  targetBottomIds: string[];
  linkIds: string[];
}

/** One scored component behind a cross-verse proposal (#138). `rawScore` and
 *  `weightedScore` are kept apart because every weight is an uncalibrated
 *  placeholder -- see cross_verse_proposals.py's docstring. */
export interface CrossVerseProposalEvidence {
  kind: "STRONGS_PRECEDENT" | "SURFACE_PRECEDENT" | "PHONETIC" | "PROXIMITY" | "MODEL_PICK";
  rawScore: number;
  weight: number;
  weightedScore: number;
  /** Corpus components only: how often this pairing was seen. */
  jointCount?: number;
  sourceCount?: number;
  strongKey?: string;
  sedCost?: number;
  /** MODEL_PICK only (#146): the model's own 0-100 number, and its one-line
   *  reason. Recorded as evidence about the model, not as a calibrated score --
   *  it is deliberately not what gates an automatic link. */
  modelConfidence?: number;
  reason?: string;
}

export interface CrossVerseProposal {
  /** PROPOSED is one-click acceptable; AMBIGUOUS points at the verse instead. */
  status: "PROPOSED" | "AMBIGUOUS";
  confidence: number;
  /** Lead over the runner-up on substantive evidence, excluding proximity. */
  margin: number;
  /** Another source token's best candidate is this same target word. */
  contested: boolean;
  /** #146, AI proposals only: the corpus scorer's top candidate is this same
   *  pair. `autoLinkable` is that agreement AND not contested -- the only thing
   *  that may be linked without a per-link click. */
  agreesWithCorpus?: boolean;
  autoLinkable?: boolean;
  source: {
    chapter: string; verse: string; topId: string;
    word: string; signature: string; strong: string; lemma: string;
  };
  target: { chapter: string; verse: string; bottomId: string; word: string; signature: string };
  evidence: CrossVerseProposalEvidence[];
  alternatives: Omit<CrossVerseProposal, "status" | "margin" | "contested" | "alternatives">[];
}

export interface CrossVerseProposalResult {
  chapter: string;
  verses: string[];
  proposals: CrossVerseProposal[];
  calibrationVersion: string;
  corpus?: { versesScanned: number; booksScanned: string[]; totalPairs: number };
  /** Set instead of proposals when the project has nothing to learn from. */
  unavailable?: { reason: string; message: string };
}

/** `alignment.crossVerse.aiPropose` (#146). Same proposal shape, plus what the
 *  corpus pass found on its own, so a model that returns nothing still leaves
 *  the statistical suggestions on screen. */
export interface CrossVerseAiProposalResult extends CrossVerseProposalResult {
  /** False when the gaps gave nothing worth a billed request. */
  modelConsulted?: boolean;
  /** The menu hit its cap; some gaps were not offered to the model. */
  truncated?: boolean;
  corpusProposals?: CrossVerseProposal[];
  corpusUnavailable?: { reason: string; message: string };
}

export interface AlignmentContext {
  chapter: string;
  verse: string;
  alignment: VerseAlignment;
  topTokens: AlignmentToken[];
  bottomTokens: AlignmentToken[];
  groups: AlignmentGroupView[];
  status: AlignmentWorkStatus;
  completionState: "pending" | "completed" | "invalid";
  sourceAvailable: boolean;
  sourceMessage: string;
  sourceDirection: "ltr" | "rtl";
  targetDirection: "ltr" | "rtl";
  issues: string[];
  canComplete: boolean;
  history: AlignmentHistoryEntry[];
  chapterStatus: AlignmentCounts;
  /** Gaps net of cross-verse links: a linked word is no longer a gap. */
  gaps: AlignmentGaps;
  crossVerseLinks: CrossVerseLink[];
  /** The same links folded into their composite groups (#217). */
  crossVerseGroups: CrossVerseGroup[];
  /** Bottom ids of this verse whose word is the target of an active link. */
  crossVerseAccountedIds: string[];
  /** Top ids of this verse whose token is realized in another verse. */
  crossVerseRealizedIds: string[];
  crossVerseAccounted: number;
  crossVerseRealized: number;
  /** Every remaining gap is covered by a link or a null decision. `status` and
   *  `completionState` still tell the translationCore truth: the verse is not
   *  complete. */
  fullyAccounted: boolean;
  /** #216: this verse's null decisions, active and invalid, per side. */
  nullDecisions: { source: NullDecisionEntry[]; target: NullDecisionEntry[] };
  /** How many tokens each kind of home accounts for (#216). */
  accountedBy: { tc: number; crossVerse: number; null: number };
  /** Every token has a home -- a tC group, a cross-verse link or a reasoned
   *  null -- and the verse has a source and no structural issue. Bridge's
   *  "nothing left to do"; `status` stays translationCore's own state. */
  accounted: boolean;
  /** #219: the last automatic pass on this verse, if any. */
  autoAlign?: AutoAlignSummary | null;
}

/** #216: why a word has no counterpart. Source words are implicit or
 *  grammatical; target words are grammatical or explicitation. No decision at
 *  all means unaligned -- that is never stored. */
export type NullSide = "source" | "target";
export type NullReason = "IMPLICIT" | "GRAMMATICAL" | "EXPLICITATION";
export type NullOrigin = "human" | "ai-auto" | "ai-proposed-accepted";

export interface NullDecision {
  id: string;
  bookId: string;
  chapter: string;
  verse: string;
  side: NullSide;
  token: { word: string; occurrence: number; occurrences: number; signature: string; strong?: string; lemma?: string; morph?: string };
  reason: NullReason;
  note: string;
  state: "active" | "invalid";
  origin: NullOrigin;
  previousReason?: NullReason;
  invalidReason?: string;
  createdAt: string;
  updatedAt: string;
  actorId: string;
}

/** A null decision as one verse's context reports it, with this load's id. */
export interface NullDecisionEntry {
  /** Positional id in this verse, or null when the token can't be resolved. */
  id: string | null;
  decisionId: string;
  word: string;
  reason: NullReason;
  note: string;
  origin: NullOrigin;
  state: "active" | "invalid";
  invalidReason?: string | null;
  /** Active, but this load cannot find the token: the source pack changed. */
  stale: boolean;
}

/** #219: what the automatic two-pass alignment concluded about one verse. */
export type AutoAlignVerdictKind = "ALIGNED_CLEAN" | "NEEDS_REVIEW" | "UNAVAILABLE" | "REVERTED";

/** One end of a suggestion or issue, resolved to this load's positional id. */
export interface AutoAlignTokenEnd {
  side: NullSide;
  chapter: string;
  verse: string;
  signature: string;
  word: string;
  /** Positional id in its verse now, or null if the word is gone. */
  id: string | null;
}

/** A word neither pass could place. Never aligned; reported for the reviewer. */
export interface AutoAlignIssue {
  kind: "POSSIBLE_OMISSION" | "POSSIBLE_ADDITION";
  side: NullSide;
  signature: string;
  word: string;
  id: string | null;
  /** The model's own words about what is missing or added. */
  note: string;
}

/** A claim only one pass made, or that agreement could not settle. Accepting
 *  one goes through the ordinary writers (realign / crossVerse.link / null.set). */
export interface AutoAlignSuggestion {
  kind: "link" | "null";
  status: "UNCERTAIN" | "CORPUS_DISAGREES" | "CONFLICTS_WITH_HUMAN";
  votes: Record<string, boolean>;
  source: AutoAlignTokenEnd | null;
  target: AutoAlignTokenEnd | null;
  /** For a null suggestion: the word it is about. */
  token: AutoAlignTokenEnd | null;
  reason: string;
  note: string;
  confidence: number;
}

/** The ledger of what one automatic pass wrote in a verse -- what a re-run
 *  supersedes and "Undo a verse" removes. */
export interface AutoAlignApplied {
  groups: { tops: string[]; bottoms: string[]; relation?: string }[];
  links: string[];
  nulls: string[];
}

export interface AutoAlignVerseResult {
  verse: string;
  verdict: AutoAlignVerdictKind;
  applied: AutoAlignApplied;
  issues: AutoAlignIssue[];
  suggestions: AutoAlignSuggestion[];
  context: AlignmentContext;
}

export interface AutoAlignWindowResult {
  chapter: string;
  verses: string[];
  runId: string;
  calibrationVersion: string;
  corpus?: { checked: boolean; reason: string };
  results: AutoAlignVerseResult[];
  usage?: { calls: number; totalTokens: number; estimatedCostUSD: number };
  notes?: string[];
  failures?: string[];
  unavailable?: { reason: string; message: string };
}

/** The stored verdict of the last automatic pass on a verse. */
export interface AutoAlignVerdict {
  chapter: string;
  verse: string;
  verdict: AutoAlignVerdictKind;
  runId: string;
  jobId?: string;
  applyRequested: boolean;
  issues: AutoAlignIssue[];
  suggestions: AutoAlignSuggestion[];
  applied: AutoAlignApplied;
  window: string[];
  createdAt: string;
  usage?: { calls: number; totalTokens: number; estimatedCostUSD: number };
}

/** #221: the chapter / book job over overlapping windows. */
export interface AutoAlignJobStatus {
  jobId: string;
  scope: "chapter" | "book";
  apply: boolean;
  state: "queued" | "running" | "cancelling" | "succeeded" | "failed" | "cancelled";
  stage: string;
  chapters: string[];
  windowsTotal: number;
  windowsDone: number;
  windowsFailed: number;
  percent: number;
  currentWindow: { chapter: string; verses: string[] } | null;
  /** Counts by verdict, e.g. { ALIGNED_CLEAN: 24, NEEDS_REVIEW: 6 }. */
  verdictCounts: Record<string, number>;
  verdicts: Record<string, AutoAlignVerdictKind>;
  usage: { calls: number; totalTokens: number; estimatedCostUSD: number };
  error: string | null;
  unavailable: { reason: string; message: string } | null;
  resumeOf: string;
  createdAt: string;
  finishedAt: string | null;
}

/** What a job would cost, worked out offline before the click. */
export interface AutoAlignEstimate {
  scope: "chapter" | "book";
  chapters: string[];
  windows: number;
  calls: number;
  estimatedInputTokens: number;
  estimatedOutputTokens: number;
  estimatedCostUSD: number;
  model: string;
  hasApiKey: boolean;
}

/** The verdict summary each alignment context carries. */
export interface AutoAlignSummary {
  verdict: AutoAlignVerdictKind;
  runId: string;
  createdAt: string;
  /** The verse or its alignment changed after the pass. */
  stale: boolean;
  issues: number;
  suggestions: number;
}

export interface NullDecisionResult {
  decision: NullDecision;
  context: AlignmentContext;
}

/** alignment.getRange: one context per requested verse, in the caller's
 *  order, with the chapter counts computed once. */
export interface AlignmentRange {
  chapter: string;
  verses: AlignmentContext[];
  chapterStatus: AlignmentCounts;
}

export interface AlignmentStatusResponse {
  chapter: string;
  counts: AlignmentCounts;
  verses: Record<string, AlignmentWorkStatus>;
}

/**
 * Field names deliberately match alignment_reliability.compile_link_proposal's
 * own schema verbatim (snake_case), not this file's usual camelCase convention —
 * this object round-trips unchanged from alignment.aiPropose back into
 * alignment.aiApplyProposal, which expects exactly these keys. See
 * bridge_service.py's propose_ai_alignment for the full rationale.
 */
export interface AlignmentAiProposalGroup {
  top_ids: string[];
  bottom_ids: string[];
  confidence: number;
  reason: string;
  origin: "existing" | "ai_compiled" | "extended_protected" | "implicit" | "unresolved";
  relation?: string;
}

export interface AlignmentAiProposal {
  groups: AlignmentAiProposalGroup[];
  links: Array<{ top_id: string; bottom_id: string; confidence: number; reason: string }>;
  uncertain_links: Array<{ top_id: string; bottom_id: string; confidence: number; reason: string }>;
  implicit_top_ids: string[];
  target_only_ids: string[];
  review_notes: string[];
  diagnostics: Array<Record<string, unknown>>;
  conflicts: Array<Record<string, unknown>>;
  requires_human_review: boolean;
  compiler_version: string;
  mode: string;
  lock_policy: string;
  thresholds: { auto: number; review: number };
}

export interface AlignmentAiProposeResponse {
  proposal: AlignmentAiProposal;
  usage: { totalTokens: number; estimatedCostUSD: number };
}

export type SemanticSelectionState =
  | ""
  | "found_this_verse"
  | "found_another_verse"
  | "split_across_verses"
  | "represented_implicitly"
  | "target_not_located"
  | "needs_passage_review"
  | "needs_extended_passage_review"
  | "source_anchor_unresolved"
  | "mapping_error";

export interface SemanticTargetSpan {
  reference: string;
  quote: string;
  start: number | null;
  end: number | null;
}

export interface SemanticMappingEvidence {
  source: string;
  target: string;
  explanation: string;
}

export interface SemanticMapping {
  source_unit_id: string;
  source_token_ids: string[];
  source_reference: string;
  target_spans: SemanticTargetSpan[];
  relationships: string[];
  meaning_status: "PRESERVED" | "PARTIALLY_PRESERVED" | "NOT_LOCATED" | "POSSIBLE_PROBLEM" | "UNCERTAIN";
  confidence: number;
  evidence: SemanticMappingEvidence;
}

/** Field names match AICheckReview.to_dict()/QAIssue.to_dict() verbatim (Python's own
 * dict output, snake_case) — this is display-only data, never sent back to the engine,
 * but declaring it with the wire shape it actually has avoids a silently-wrong type. */
export interface AiCheckReview {
  tool: string;
  group_id: string;
  check_id: string;
  source_quote: string;
  proposed_selection_ids: string[];
  proposed_selection_text: string[];
  proposed_selections: CheckTargetSelection[];
  nothing_to_select: boolean;
  verdict: "pass" | "review" | "problem" | "not_applicable";
  severity: "critical" | "high" | "medium" | "editorial" | "info";
  rationale: string;
  suggested_correction: string;
  confidence: number;
  evidence_used: Array<Record<string, unknown>>;
  selection_state: SemanticSelectionState;
  semantic_mapping: SemanticMapping | null;
}

export interface AiQaIssue {
  code: string;
  severity: "critical" | "high" | "medium" | "editorial" | "info";
  title: string;
  detail: string;
  source: string;
  check_id?: string;
  group_id?: string;
  confidence?: number;
}

export interface AiExplainResult {
  summary: string;
  checkReviews: AiCheckReview[];
  qaIssues: AiQaIssue[];
  alignmentProposal: AlignmentAiProposal | null;
  alignmentWasAIProposed: boolean;
  usage: { totalTokens: number; estimatedCostUSD: number };
}

export interface DesktopConnectorState {
  connected: boolean;
  detected?: boolean;
  reference?: string;
  project_id?: string;
  project_name?: string;
  user?: string;
  capabilities?: string[];
  [key: string]: unknown;
}

export interface NavigationTargetState extends DesktopConnectorState {
  enabled: boolean;
  checking: boolean;
  error: string;
  checkedAt: number;
}

export interface NavigationSyncState {
  enabled: boolean;
  ownsNavigation: boolean;
  ownerConflict: boolean;
  currentReference: string;
  currentOrigin: string;
  candidate: { reference: string; origin: "paratext" | "logos"; requestId: string } | null;
  paratext: NavigationTargetState;
  logos: NavigationTargetState;
}

export interface ProjectInfo {
  projectId?: string;
  collectionId?: string;
  managed?: boolean;
  path: string;
  bookId: string;
  bookName: string;
  targetLanguage: string;
  targetLanguageId?: string;
  targetLanguageDirection?: string;
  projectName?: string;
  bibleName?: string;
  tcVersion: string;
  chapters: string[];
  checkTypes: Record<string, number>;
  originalLanguageResource?: {
    available: boolean;
    languageId?: string;
    resourceId?: string;
    version?: string;
    owner?: string;
    commit?: string;
    release?: string;
    license?: string;
    attribution?: string;
    projectVersion?: string;
    versionMismatch?: boolean;
    message?: string;
  };
  importedProjects?: ImportedProject[];
  passageSemantic?: {
    state: "NO_PROJECT" | "READY" | "UNAVAILABLE" | "RECOVERY_REQUIRED";
    available: boolean;
    readOnly: boolean;
    error?: string;
    [key: string]: unknown;
  };
}

export interface ImportBook {
  bookId: string;
  bookName: string;
  sourceFile: string;
  verseCount: number | null;
  hasAlignments: boolean;
}

export interface ImportMetadata {
  languageId: string;
  languageName: string;
  languageDirection: "ltr" | "rtl" | "";
  projectName: string;
  bibleName: string;
  resourceId?: string;
}

export interface ImportPreview {
  sourcePath: string;
  kind: "usfm" | "usfmCollection" | "paratext" | "translationCore" | "translationCoreArchive";
  metadata: ImportMetadata;
  books: ImportBook[];
  missingFields: string[];
  warnings: string[];
  duplicates: DuplicateAssessment;
}

export type DuplicateClassification = "new" | "exactDuplicate" | "possibleDuplicate" | "partialOverlap";

export interface DuplicateMatch {
  match: "exact" | "possible";
  reason: "sourceFingerprint" | "bookLanguageBible";
  groupId: string;
  projectId: string;
  collectionId?: string;
  path: string;
  bookId: string;
  bookName: string;
  projectName?: string;
  bibleName?: string;
  lastOpenedAt?: string;
  missing: boolean;
}

export interface DuplicateAssessment {
  classification: DuplicateClassification;
  matches: DuplicateMatch[];
  inputBookCount: number;
  exactBookCount: number;
  missingExactBookCount: number;
  possibleBookCount: number;
  overlapBookCount: number;
  matchingGroupCount: number;
  exactMatchGroupId: string;
  sourceFingerprints: Record<string, string>;
  collectionFingerprint: string;
}

export interface RegisteredProject {
  projectId: string;
  collectionId?: string;
  path: string;
  managed: boolean;
  missing: boolean;
  bookId: string;
  bookName: string;
  targetLanguageId?: string;
  targetLanguage?: string;
  projectName?: string;
  bibleName?: string;
  lastOpenedAt?: string;
  bookCount?: number;
}

export interface ImportedProject {
  projectId?: string;
  collectionId?: string;
  directoryName?: string;
  path: string;
  bookId: string;
  bookName: string;
  chapters?: string[];
  checkIndexStatus?: string;
  lazy?: boolean;
}

/** Flattened `totals` block from a book's .bridge/progress.json rollup. */
export interface BookProgressSummary {
  chapterCount: number;
  checkedChapterCount: number;
  verseCount: number;
  checkedVerseCount: number;
  reviewedVerseCount: number;
  findingCount: number;
  approvedFindingCount: number;
  updatedAt: string | null;
}

export interface BookProgressEntry {
  path: string;
  bookId: string;
  bookName: string;
  lazy: boolean;
  missing: boolean;
  /** null = lazy sibling never opened, or a materialized book never checked yet. */
  progress: BookProgressSummary | null;
}

export type VerseNoteKind = "footnote" | "xref";

export interface VerseNotePart {
  /** USFM marker without its backslash, e.g. "fr", "fq", "ft", "xo", "xt". */
  marker: string;
  text: string;
}

/** A footnote or cross-reference lifted out of the verse by the engine. */
export interface VerseNote {
  kind: VerseNoteKind;
  /** The caller glyph USFM puts right after \f / \x — usually "+". */
  caller: string;
  /** Verse reference from \fr (footnote) or \xo (xref), when present. */
  reference: string;
  parts: VerseNotePart[];
  /** Everything the note says, flattened, for a plain one-line rendering. */
  text: string;
  /** Code-point offset into `VerseDisplay.plain` where this note sat. */
  position: number;
}

/** A character style (`\nd`, `\wj`, `\it` …) spanning `plain[start, end)`, code points. */
export interface StyleSpan {
  marker: string;
  start: number;
  end: number;
}

/**
 * What the reader shows for one verse, computed by the engine's fragment
 * reader (`usfm_verse.lift_verse`, #91) next to the text it describes. The
 * frontend renders this and never parses USFM. Every offset is a Unicode
 * code point, like every engine finding's; convert with codePointToUtf16
 * before slicing a JS string.
 */
export interface VerseDisplay {
  /** The raw verse with notes, markers and attributes removed — deletion only. */
  plain: string;
  notes: VerseNote[];
  /** Raw code-point ranges `[start, end)` that were removed, merged and sorted. */
  removed: [number, number][];
  styles: StyleSpan[];
  warnings: string[];
}

export interface VerseData {
  chapter: string;
  verse: string;
  /** The raw stored string: what the editor edits and fixes splice. */
  text: string;
  display: VerseDisplay;
  alignment: VerseAlignment;
  alignmentStatus: AlignmentWorkStatus;
}

/** A bookmarked verse or a recent chapter: `collection` is the collection id
 *  (or the project path) it belongs to, `book` a lower-case book id. */
export interface Place {
  collection: string;
  book: string;
  chapter: string;
  verse?: string;
  label?: string;
  ts?: string;
}

/** One recorded Scripture edit (verse.history): the native checkData/verseEdits
 *  record every edit writes, with the visible text of both sides for a diff. */
export interface VerseHistoryEntry {
  chapter: string;
  verse: string;
  timestamp: string;
  username: string;
  verseBefore: string;
  verseAfter: string;
  plainBefore: string;
  plainAfter: string;
  tags: string[];
  groupId: string;
  /** Set when the edit was one verse of a scoped Language QA change set. */
  batchId: string | null;
  /** Set on the edits that undid a change set. */
  undoes: string | null;
}

export interface VerseHistory {
  entries: VerseHistoryEntry[];
  total: number;
  truncated: boolean;
}

/** A section heading (`\s`, `\ms`, `\r`, ...) lifted out of the verse text it
 *  used to trail (#180). It is the team's text and belongs on screen, but it is
 *  not a translation of any source word, so it is never an alignable target. */
export interface VerseHeading {
  tag: string;
  text: string;
}

export type NativeCheckTool = "translationNotes" | "translationWords";
export type CheckSelectionStatus = "pending" | "selected" | "nothing_to_select" | "invalidated";
export type CheckEvaluationStatus = "not_run" | "running" | "passed" | "issue_open" | "needs_review" | "failed";
export type CheckSelectionProvenance = "none" | "existing_tc" | "human" | "bridge_ai";

export interface CheckTargetSelection {
  text: string;
  occurrence: number;
  occurrences: number;
}

/**
 * What the last AI review's automatic-selection pass did with this check, and
 * why. Persisted next to the review, so it is still answerable after the job
 * result is gone — reopening the verse, or restarting the app.
 */
export interface CheckAutomationResult {
  outcome: "applied" | "skipped" | "";
  reason: string;
}

export interface NativeCheckReview {
  chapter: string;
  verse: string;
  tool: NativeCheckTool;
  groupId: string;
  checkId: string;
  sourceQuote: string;
  sourceOccurrence: number | null;
  occurrenceNote: string;
  selections: CheckTargetSelection[];
  nothingToSelect: boolean;
  invalidated: boolean;
  stale: boolean;
  selectionStatus: CheckSelectionStatus;
  evaluationStatus: CheckEvaluationStatus;
  provenance: CheckSelectionProvenance;
  stateFingerprint: string;
  /** null when no AI review has run for this verse since the check appeared. */
  automaticSelection: CheckAutomationResult | null;
}

export interface NativeCheckListResponse {
  chapter: string;
  verse: string;
  checks: NativeCheckReview[];
  state: "ready" | "preparing";
  retryAfterMs: number;
  message: string;
  aiReviewState: "missing" | "current" | "stale";
  aiReviews: AiCheckReview[];
  aiQaIssues: AiQaIssue[];
  aiSummary: string;
}

export interface CheckSelectionValidation {
  valid: boolean;
  errors: string[];
  selections: CheckTargetSelection[];
  ranges: Array<{ start: number; end: number }>;
  stateFingerprint: string;
}

export interface CheckSelectionMutation {
  committed: true;
  review: NativeCheckReview;
  files: Record<string, string>;
}

export type ParatextHandoffStatus = "not_queued" | "queued" | "sent";

export interface IssueResolutionRecheck {
  status: "not_run" | "stale" | "running" | "resolved" | "reflagged" | "needs_review" | "failed" | "cancelled";
  attempt?: number;
  verdict?: "pass" | "problem" | "review" | "not_applicable";
  confidence?: number;
  rationale?: string;
  suggestedCorrection?: string;
  evidence?: Array<Record<string, unknown>>;
  model?: string;
  summary?: string;
  inputFingerprint?: string;
  reason?: string;
  error?: string;
  startedAt?: string;
  staleAt?: string;
  completedAt?: string;
}

export interface IssueResolutionRecord {
  schemaVersion: 1;
  resolutionId: string;
  bookId: string;
  chapter: string;
  verse: string;
  reference: string;
  check: {
    tool: NativeCheckTool;
    groupId: string;
    checkId: string;
    sourceQuote: string;
    sourceOccurrence: number | null;
    stateFingerprint: string;
  };
  selectedText: string;
  issueSummary: string;
  reviewerNote: string;
  proposedCorrection: string;
  evidence: Array<Record<string, unknown> | string>;
  status: "open" | "resolved" | "reflagged";
  recheck: IssueResolutionRecheck;
  paratext: {
    status: ParatextHandoffStatus;
    messageId: string;
    attempts: number;
    lastError: string;
    sentAt: string;
    remoteId: string;
    contentSignature: string;
    expectedProjectId?: string;
  };
  createdAt: string;
  updatedAt: string;
  history: Array<Record<string, unknown>>;
}

export interface IssueResolutionListResponse {
  chapter: string;
  verse: string;
  items: IssueResolutionRecord[];
  queued: number;
  sent: number;
  resolved: number;
  reflagged: number;
}

export interface IssueResolutionHandoffResult {
  record: IssueResolutionRecord;
  handoff: {
    messageId: string;
    resolutionId: string;
    status: "queued" | "sent";
    attempts: number;
    lastError: string;
    sentAt?: string;
    remoteId?: string;
    expectedProjectId?: string;
  };
}

export type CheckJobState =
  | "queued" | "running" | "cancelling" | "succeeded" | "failed" | "cancelled";

export interface CheckJobVerseResult {
  chapter: string;
  verse: string;
  status: "succeeded" | "failed";
  findings: QaFinding[];
  error: string | null;
  /** The Language QA stage's share of this verse (only when the job ran it).
   * Kept apart from `findings`: these are not QaFindings. `decided` maps the
   * id of each finding a decision hides to that decision. The marks are not
   * drawn from here; the job's pass publishes a new Language QA generation,
   * and the status channel redraws from it. */
  languageQa?: { findings: import("./languageQa").LanguageQaFinding[]; decided: Record<string, string> };
}

/** checks.status's view of the Language QA stage, for the main progress bar. */
export interface CheckJobLanguageQa {
  state: string | null;
  completedChapters: number;
  totalChapters: number;
  findings: number;
  limitations: string[];
}

export interface CheckJobSnapshot {
  jobId: string;
  scope: "chapter" | "book";
  projectPath: string;
  state: CheckJobState;
  checks: string[];
  chapters: string[];
  chapterVerses: Record<string, string[]>;
  totalVerses: number;
  completedVerses: number;
  failedVerses: number;
  percent: number;
  currentChapter: string | null;
  currentVerse: string | null;
  currentStage: string;
  results: Record<string, CheckJobVerseResult>;
  error: string | null;
  createdAt: string;
  finishedAt: string | null;
  languageQa?: CheckJobLanguageQa;
}

export interface AIReviewJobVerseResult {
  chapter: string;
  verse: string;
  status: "succeeded" | "failed";
  summary: string;
  checkReviews: AiCheckReview[];
  qaIssues: AiQaIssue[];
  appliedSelections: Array<Record<string, unknown>>;
  skippedSelections: Array<Record<string, unknown>>;
  alignmentProposal: AlignmentAiProposal | null;
  alignmentWasAIProposed: boolean;
  usage: { totalTokens: number; estimatedCostUSD: number };
  error: string | null;
}

export interface AIReviewJobVerseStatus {
  chapter: string;
  verse: string;
  status: "succeeded" | "failed";
  summary: string;
  appliedCount: number;
  skippedCount: number;
  usage: { totalTokens?: number; estimatedCostUSD?: number };
  error: string | null;
}

export interface AIReviewJobSnapshot {
  jobId: string;
  scope: "verse" | "chapter" | "book";
  mode: "basic" | "advanced";
  projectPath: string;
  state: CheckJobState;
  chapters: string[];
  chapterVerses: Record<string, string[]>;
  skippedCurrentVerses: number;
  resumeOf: string;
  totalVerses: number;
  completedVerses: number;
  failedVerses: number;
  percent: number;
  currentChapter: string | null;
  currentVerse: string | null;
  currentStage: string;
  results: Record<string, AIReviewJobVerseStatus>;
  latestResult: { key: string; result: AIReviewJobVerseResult } | null;
  error: string | null;
  createdAt: string;
  finishedAt: string | null;
}

export interface AIReviewChapterResponse {
  chapter: string;
  reviewsByVerse: Record<string, AiCheckReview[]>;
  states: Record<string, "missing" | "current" | "stale">;
  current: number;
  stale: number;
  missing: number;
}

export interface SettingsData {
  provider: string;
  apiBaseUrl: string;
  model: string;
  reviewerName: string;
  /** ISO-8601 UTC, or "" if never explicitly changed via Settings (the
   * OS-seeded default on a fresh profile does not count as a change). */
  reviewerNameUpdatedAt: string;
  /** The stable local `user_id` to stamp on a write (#78) — never the display
   *  name. Renaming yourself changes `reviewerName` and leaves this alone, so a
   *  rename re-labels the rows you already wrote instead of splitting your
   *  history between two apparent actors. */
  localUserId: string;
  reviewerMode: "basic" | "advanced";
  paratextUsername: string;
  paratextNavigation: boolean;
  logosNavigation: boolean;
  /** Report screen: hide findings AI triage rated this likely (50-100) to be
   * a false positive. 0 means off. Default 90. */
  triageHideThreshold: number;
  /** Settings > Language QA: an indic-qa finding is drawn in the text only
   * when its rule's measured reviewer precision is at least this (0-100; a
   * rule nobody labelled always passes). 0, the default, draws them all. */
  languageQaInlinePrecision: number;
  /** The lowest rule confidence drawn in the text. Default "low" (every rule). */
  languageQaInlineConfidence: InlineConfidence;
  /** Learn a word the reviewer replaced and offer the change where it recurs. Default on. */
  languageQaLearnedFixes?: boolean;
  /** The reference Bible per Language QA pack: {"hi-irv": "D:/OV Hindi"}. */
  languageQaReferenceDirs?: Record<string, string>;
  /** Offer related words from the reference text. Default on. */
  languageQaRelatedWords?: boolean;
  /** Kept verses and recently visited chapters, newest first (app-level). */
  bookmarks?: Place[];
  recentChapters?: Place[];
  hasApiKey: boolean;
  aiUsage: { tokens: number; estimatedCostUSD: number };
}

export type InlineConfidence = "low" | "medium" | "high";

export interface TerminologyRule {
  conceptId: string;
  approvedRenderings: string[];
  allowedAlternatives: string[];
  rejectedRenderings: string[];
  status: string;
  provenance: string;
  modifiedTimestamp: string;
  /** Termbase v3 (layered-rules 6.1): a rejected rendering -> its listed forms. */
  inflectedForms?: Record<string, string[]>;
  /** "prefix" also matches the rejected renderings with case/plural endings. */
  matchMode?: "exact" | "prefix";
}

export const STATUS_COLOR: Record<string, string> = {
  passed: "#22c55e",
  needs_review: "#f59e0b",
  problem: "#ef4444",
  checking: "#3b82f6",
  not_checked: "#9ca3af",
  ignored: "#a855f7",
};
