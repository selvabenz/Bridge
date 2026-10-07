/** Mirrors language_qa.RuleMeta (engine). */
export type LanguageQaLayer = "pattern" | "lexicon" | "housestyle" | "integrity";
export type LanguageQaCategory =
  "typo" | "sandhi" | "word-joining" | "punctuation" | "unicode" | "spacing" | "termbase" | "name" | "usfm"
  | "consistency" | "grammar";
export type LanguageQaConfidence = "high" | "medium" | "low";

/** One ranked fix (language_qa.suggestion). */
export interface LanguageQaSuggestion {
  text: string;
  /** 1-based; suggestions arrive sorted by it. */
  rank: number;
  source: "rule" | "lexicon" | "termbase" | "majority-form" | "housestyle";
  /** Why this fix; shown as the menu item's tooltip. */
  rationale: string;
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
