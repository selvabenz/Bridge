/** Mirrors language_qa.RuleMeta (engine). */
export type LanguageQaLayer = "pattern" | "lexicon" | "housestyle" | "integrity";
export type LanguageQaCategory =
  "typo" | "sandhi" | "word-joining" | "punctuation" | "unicode" | "spacing" | "termbase" | "name" | "usfm"
  | "consistency" | "grammar" | "learned";
export type LanguageQaConfidence = "high" | "medium" | "low";

/** One ranked fix (language_qa.suggestion). */
export interface LanguageQaSuggestion {
  text: string;
  /** 1-based; suggestions arrive sorted by it. */
  rank: number;
  /** "learned": a replacement the reviewer made before (language_qa_learned.py). */
  source: "rule" | "lexicon" | "termbase" | "majority-form" | "housestyle" | "learned";
  /** Why this fix; shown as the menu item's tooltip. */
  rationale: string;
  /** The checker's edit class ("vowel_length", "split", ...) and how often the
   * form is used: shown beside the suggestion, as the indic-qa editor does. */
  kind?: string;
  freq?: number;
}

export interface LanguageQaFinding {
  id: string;
  book: string;
  chapter: string;
  verse: string;
  /** Legacy rule id, e.g. "tamil.vallinam-missing". Kept as an alias of
   * ruleId for one release; finding ids are still derived from it. */
  rule: string;
  severity: string;
  start: number;
  end: number;
  originalText: string;
  message: string;
  textHash: string;
  ruleVersion: string;
  status: "review-needed";
  /** Alias of suggestions[0].text (null when there are none), kept for one release. */
  suggestedReplacement?: string | null;
  /** Always "languageQa" from the engine (language_qa.FINDING_SOURCE). */
  source: "languageQa";
  layer: LanguageQaLayer;
  category: LanguageQaCategory;
  /** A categorical label, not a calibrated probability. */
  confidence: LanguageQaConfidence;
  /** Ranked, at most five; empty when the rule proposes no fix. */
  suggestions: LanguageQaSuggestion[];
  /** Pack-qualified, e.g. "ta-irv/tamil.vallinam-missing". */
  ruleId: string;
  packVersion: string;
  /** The rule's own version; with packVersion it decides when an old
   * "ignored" decision stops applying. */
  ruleRevision: number;
  /** The reviewed flag: the rule is inline by review (rule_versions.json,
   * INLINE_RULES), which the CI human gate checks. Never the user's setting. */
  inline: boolean;
  /** Whether the verse text draws it: reviewed-inline, or an indic-qa finding
   * the Settings > Language QA threshold lets through (language_qa_drawn.py).
   * Every finding languageQa.inline returns is drawn. */
  drawn?: boolean;
  /** Set when the finding was ignored under an older rule or pack version and
   * is shown again for re-checking. */
  previouslyIgnored?: boolean;
  /** Where the finding is when it is not in the verse text (ta-irv's OV
   * dictionary layer): "footnote" -- start/end still index the stored verse,
   * so a suggestion applies as usual; "heading" -- a section heading before
   * this verse, start/end index `contextText`, and no suggestion is offered
   * because a fix would be written into the verse. Absent: the verse text. */
  context?: "footnote" | "heading";
  /** The heading's visible text, for context "heading". */
  contextText?: string;
  /** The same verse in a reference text (ta-irv: the 1957 Old Version). */
  reference?: LanguageQaReference;
}

export interface LanguageQaReference {
  label: string;
  /** e.g. "GEN 1:27": a verse bridge looks up its first number. */
  ref: string;
  text: string;
}

/** The `issue` a Language QA verse.decide carries: what the reviewer saw and
 * chose, recorded in the decision payload for the audit trail. */
export interface LanguageQaDecisionIssue {
  source: "languageQa";
  rule: string;
  ruleId: string;
  ruleVersion: string;
  packVersion: string;
  ruleRevision: number;
  layer: LanguageQaLayer;
  category: LanguageQaCategory;
  originalText: string;
  /** Alias: the chosen suggestion's text, or the first suggestion's when none was chosen. */
  suggestedReplacement: string | null;
  /** The suggestion the reviewer applied (Use), when there was one. */
  chosenSuggestion: string | null;
  chosenRank: number | null;
  message: string;
  start: number;
  end: number;
}

/** Which list languageQa.status pages. */
export type LanguageQaView = "findings" | "recheck" | "falsePositives";

/** A learned fix (workbench v6): the reviewer replaced `old` with `new`
 * `count` times; offered again where `old` recurs while enabled. */
/** One learned fix, merged over the collection's books (shared, DECISIONS
 * 2026-10-07): `count` is the net uses in every book, `own` this book's share
 * (negative when a use learned elsewhere was taken back here), `books` the
 * books with uses. Forgotten in any book means forgotten everywhere. */
export interface LearnedFix {
  old: string;
  new: string;
  count: number;
  own?: number;
  books?: string[];
  firstRef: string;
  lastRef: string;
  reviewer: string;
  source: "edit" | "scope" | "flag" | "retraction" | "collection";
  enabled: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface LearnedFixesResponse {
  fixes: LearnedFix[];
  /** Settings > Language QA: whether learned fixes are on. */
  enabled: boolean;
}

export type LanguageQaScope = "verse" | "chapter" | "book";

export interface ScopeOccurrence {
  chapter: string;
  verse: string;
  findingId: string;
  /** Raw code points into the verse text. */
  start: number;
  end: number;
  old: string;
  /** null: nothing to write here (no replacement). */
  new: string | null;
}

/** languageQa.scopeFind: where the same finding is, before anything is written. */
export interface ScopeFindResult {
  chapter: string;
  verse: string;
  findingId: string;
  scope: LanguageQaScope;
  key: { ruleId: string; detailRule: string; originalText: string; kind: "word" | "warning" };
  count: number;
  verses: number;
  occurrences: ScopeOccurrence[];
}

export interface ScopeChange {
  chapter: string;
  verse: string;
  newText: string;
  oldText?: string;
  display?: import("./finding").VerseDisplay;
}

/** languageQa.scopeApply with action "accept". */
export interface ScopeAcceptResult {
  batchId: string | null;
  action: "accept";
  scope: LanguageQaScope;
  count: number;
  changed: ScopeChange[];
  skipped: { chapter: string; verse: string; reason: string }[];
  learned?: { old: string; new: string; count: number; n: number };
}

/** languageQa.scopeApply with action "ignore". */
export interface ScopeIgnoreResult {
  action: "ignore";
  scope: LanguageQaScope;
  count: number;
  decided: string[];
  houseStyle?: unknown;
}

export interface BatchUndoResult {
  batchId: string;
  undoBatchId: string;
  reverted: ScopeChange[];
  conflicts: { chapter: string; verse: string; reason: string }[];
}

/** Mirrors BridgeEngine.FLAG_TYPES (engine). */
export const FLAG_TYPES = ["spelling", "grammar", "meaning", "style", "encoding", "font", "other"] as const;
export type FlagType = (typeof FLAG_TYPES)[number];
export type FlagStatus = "open" | "resolved" | "deleted";

/** A reviewer's question on a passage (workbench v6). Never a Scripture write. */
export interface LanguageQaFlag {
  flagId: string;
  chapter: string;
  verse: string;
  /** A flag that runs on through later verses names the last one. */
  verseEnd: string | null;
  /** Raw code points into the verse text when the flag was made. */
  start: number;
  end: number;
  text: string;
  textHash: string;
  type: FlagType;
  note: string;
  suggested: string | null;
  findingId: string | null;
  reviewer: string;
  status: FlagStatus;
  createdAt: string;
  updatedAt: string;
}

export interface FlagInput {
  chapter: string;
  verse: string;
  verseEnd?: string;
  start: number;
  end: number;
  text: string;
  type: FlagType;
  note: string;
  suggested?: string;
  findingId?: string;
}

/** languageQa.bookWords: a word of the book outside the dictionary. */
export interface BookWordRow {
  word: string;
  /** The checker's view (irv_ok: the IRV uses it; unknown: neither), or "added" to the project word list. */
  status: "unknown" | "irv_ok" | "inflected_ok" | "compound" | "rare_near_common" | "added";
  countBook: number;
  countIrv: number;
  countOv: number;
  firstRef: { chapter: string; verse: string };
}

export interface BookWordsResult {
  ready: boolean;
  reason?: string;
  generation?: number;
  total: number;
  words: BookWordRow[];
}

export interface OccurrenceHit {
  book: string;
  chapter: string;
  verse: string;
  /** Raw code points into the verse. */
  start: number;
  end: number;
  /** What a reader sees around it; the word is snippet[snippetStart:snippetEnd]. */
  snippet: string;
  snippetStart: number;
  snippetEnd: number;
}

export interface OccurrencesResult {
  word: string;
  source: "irv" | "ov";
  ready: boolean;
  total: number;
  truncated: boolean;
  hits: OccurrenceHit[];
}

/** languageQa.reference: one chapter of the reference Bible (an Old Version). */
export interface ReferenceChapter {
  /** false until the folder has been read (a background load), or when none is set. */
  ready: boolean;
  configured: boolean;
  source: { path: string; label: string; kind?: "tsv" | "usfm" } | null;
  verses: { verse: string; text: string }[];
  error?: string;
  /** The Language QA pack a folder would be set for (when none is configured). */
  pack?: string | null;
}

/** languageQa.related: OV/IRV equivalents and same-stem forms of a word. */
export interface RelatedWordsResult {
  word: string;
  ready: boolean;
  off?: boolean;
  configured?: boolean;
  error?: string;
  irv?: number;
  ov?: number;
  equivalents: { w: string; n: number; source: "OV" | "IRV"; ref: string; irv: number; ov: number }[];
  family: { w: string; irv: number; ov: number }[];
  versesAligned?: number;
}

/** What verse.edit says it learned from a single-word edit. */
export interface VerseEditLearned {
  old: string;
  new: string;
  action: "learned" | "retracted" | "failed";
  count?: number;
  enabled?: boolean;
  error?: string;
}

/** One recorded decision (tc_project.language_qa_decision_history). */
export interface LanguageQaHistoryEntry {
  seq: number;
  findingId: string;
  decision: string;
  note: string;
  rule: string | null;
  ruleId: string | null;
  originalText: string | null;
  chosenSuggestion: string | null;
  chosenRank: number | null;
  packVersion: string | null;
  recordedAt: string;
  revision: number | null;
  actorId: string;
}

export interface LanguageQaHistory {
  chapter: string;
  verse: string;
  findingId: string | null;
  entries: LanguageQaHistoryEntry[];
}

/** What Language QA checks and what it never checks (language_qa.coverage(),
 * layered-rules Phase 7). The panel shows it permanently. */
export interface LanguageQaCoverage {
  inScope: { category: string; label: string; labelTa: string }[];
  outOfScope: { category: string; label: string; labelTa: string; reason: string; reasonTa: string }[];
  /** Repo path of the doc naming who checks each out-of-scope item. */
  handOff: string;
  summary: string;
}

export interface LanguageQaStatus {
  projectPath: string;
  /** The list this page came from; totalFindings counts that list. */
  view?: LanguageQaView;
  recheckCount?: number;
  falsePositiveCount?: number;
  book: string;
  generation: number;
  state: "idle" | "queued" | "running" | "completed" | "paused" | "failed";
  ruleVersion: string;
  language?: {
    declared: string;
    language: string;
    /** Display name from the pack registry ("Tamil"); "" when undetermined. */
    name?: string;
    script: string;
    /** "metadata" | "script-suggestion" | "metadata-conflict" | "mixed-script" | "undetermined"
     *  | "setting" | "setting-off". */
    basis: string;
    /** The resolved pack name (e.g. "ta-irv"), or "common" for the common checks only. */
    pack: string;
    /** The project setting: "auto", "off" or a pack name. */
    setting?: string;
    message: string;
  };
  findings: LanguageQaFinding[];
  totalFindings: number;
  offset: number;
  completedChapters?: number;
  totalChapters?: number;
  checkedVerses?: number;
  skippedVerses?: number;
  incomplete?: boolean;
  limitations: string[];
  error?: string;
  coverage: LanguageQaCoverage;
  storage: string;
  /** The pack this pass ran, e.g. "hi-irv@1.0.0", or "common". */
  rulePack?: string;
  /** The project's setting (Settings > Language QA): "auto", "off" or a pack. */
  setting?: string;
  /** Every registered pack, for that setting's choices. */
  packs?: { language: string; name: string; pack: string }[];
  /** The engine's list of rules that carry an inline span (INLINE_RULES in
   * language_qa.py) -- the one authority for which findings are drawn in the
   * verse text. highlight.ts only maps these names to CSS classes. */
  inlineRules?: string[];
  /** The rule names drawn under the user's threshold: inlineRules plus the
   * indic-qa rules it lets through. */
  drawnRules?: string[];
  inlinePolicy?: { minPrecision: number; minConfidence: "low" | "medium" | "high" };
  /** Open findings per category in the list (narrowed by chapter only), for
   * the panel's kind legend. */
  categoryCounts?: Record<string, number>;
}

/** languageQa.inline: every inline-rule finding for one chapter (or the whole
 * book), unpaged. Separate from LanguageQaStatus's page so the verse marks
 * never depend on which page the panel happens to be showing. */
export interface LanguageQaInline {
  projectPath: string;
  book: string;
  generation: number;
  state: LanguageQaStatus["state"];
  ruleVersion: string;
  chapter: string | null;
  inlineRules: string[];
  drawnRules?: string[];
  findings: LanguageQaFinding[];
}

/** languageQa.verse: every Language QA finding of one verse from the last
 * pass, inline or not -- what the review panel lists (layered-rules 4.3).
 * `hidden` are the ones a decision hides, each with that `decision`. */
export interface LanguageQaVerse {
  projectPath: string;
  generation: number;
  state: LanguageQaStatus["state"];
  chapter: string;
  verse: string;
  findings: LanguageQaFinding[];
  hidden: Array<LanguageQaFinding & { decision: string }>;
}

/** languageQa.checkerSettings.get: the indic-qa checker's own settings (the
 * web app's Settings dialog), per collection. `available: false` for a
 * project whose language has no indic-qa checker. */
export interface CheckerSettingsUnavailable {
  pack: null;
  available: false;
  reason: string;
}

export interface CheckerSettings {
  pack: string;
  language: string;
  /** The Tamil layer (sandhi leads, three contexts) rather than a profile pack. */
  layer: boolean;
  /** False until a pass has loaded the checker: the style choices need it. */
  ready: boolean;
  /** The books a save is written to (every materialized book of the collection). */
  books: string[];
  legend: string;
  contexts: { checkable: string[]; checked: string[]; labels: Record<string, string> };
  warnings: { values: Record<string, boolean>; labels: Record<string, string> };
  suggest: { max: number; limit: number };
  compound: { enabled: boolean };
  rules: { id: string; label: string; default: boolean; enabled: boolean; count: number }[];
  sandhi?: { values: Record<string, number | boolean>; labels: Record<string, string> };
  numbers?: { group: string; key: string; label: string; min: number; default: number; value: number }[];
  style?: { id: string; label: string; close: boolean; options: { value: string; label: string }[]; value: string }[];
}

/** languageQa.checkerSettings.set: only the sections that changed. */
export interface CheckerSettingsPatch {
  checker?: Record<string, unknown>;
  rules?: Record<string, { enabled: boolean }>;
}
