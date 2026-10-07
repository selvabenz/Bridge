import type { AiCheckReview, QaFinding, FindingCategory, NativeCheckReview } from "../types/finding";
import type { LanguageQaCategory, LanguageQaFinding } from "../types/languageQa";

// Category -> CSS class matching the four-colour finding-source legend in
// index.css (tN red, tW blue, Alignment amber, everything else = Greek Room
// green). The same classes/tokens colour the review panel, so an underline in
// the verse and its entry in the panel always agree on what found it.
export function categoryClass(category: FindingCategory): string {
  switch (category) {
    case "translation_note": return "m-tn";
    case "translation_word": return "m-tw";
    case "alignment": return "m-align";
    default: return "m-gr"; // unicode, spelling, names, repetition, consistency, structure, omission_addition
  }
}

// Language QA category -> CSS class. WHICH findings are drawn inline is the
// engine's decision (each finding's `inline` flag, from INLINE_RULES in
// language_qa.py); this map only chooses how a category looks. Its keys must
// equal the engine's CATEGORIES -- test_category_marks_match_the_engine
// (engine suite) fails if they drift. Every style is distinguishable without
// colour: wavy, dotted, dashed, thin solid, hatched background, double.
export const LANGUAGE_QA_CATEGORY_MARKS: Record<LanguageQaCategory, string> = {
  typo: "m-lqa-typo",              // amber dotted; high-confidence typo -> m-lqa-typo-high, red wavy
  sandhi: "m-lqa-sandhi",          // green dashed
  "word-joining": "m-lqa-sandhi",
  punctuation: "m-lqa-spacing",    // grey thin solid
  spacing: "m-lqa-spacing",
  unicode: "m-lqa-unicode",        // grey hatched background
  termbase: "m-term",              // purple double
  name: "m-term",
  usfm: "m-lqa-spacing",           // markup hygiene, not a text change
  // indic-qa profile packs (pa, ml, hi, or): panel-only today, so these
  // classes are drawn only once a rule passes the human gate.
  consistency: "m-lqa-typo",       // a minority spelling: the same family as a possible typo
  grammar: "m-lqa-sandhi",         // a lead between two words, like a sandhi lead
};

/** The class for one Language QA finding. */
export function languageQaMarkClass(
  finding: Pick<LanguageQaFinding, "category" | "confidence" | "layer">,
): string {
  if (finding.layer === "lexicon") return "m-lqa-typo";
  if (finding.category === "typo" && finding.confidence === "high") return "m-lqa-typo-high";
  return LANGUAGE_QA_CATEGORY_MARKS[finding.category] ?? "m-lqa-typo";
}

// When Language QA marks overlap, one class supplies the underline:
// highest severity first, then this class order (the brief's table order).
const LQA_CLASS_ORDER = ["m-lqa-typo-high", "m-lqa-typo", "m-lqa-sandhi", "m-lqa-spacing", "m-lqa-unicode", "m-term"];
const SEVERITY_ORDER: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3 };

/** Lower wins. Exported for its test. */
export function languageQaMarkRank(finding: Pick<LanguageQaFinding, "severity" | "category" | "confidence" | "layer">): number {
  const severity = SEVERITY_ORDER[finding.severity] ?? SEVERITY_ORDER.low;
  const order = LQA_CLASS_ORDER.indexOf(languageQaMarkClass(finding));
  return severity * LQA_CLASS_ORDER.length + (order === -1 ? LQA_CLASS_ORDER.length - 1 : order);
}

export interface TextSegment {
  text: string;
  findingIds: string[];
  className: string | null;
  title: string;
  numbers: number[];
}

/**
 * Deterministic 1-based numbering, in verse reading order, for findings
 * anchored to an exact word/phrase span — the same cross-reference-style
 * marker shown both inline in the verse (buildSegments) and next to each
 * finding in ReviewPanel, so a reader can spot which highlighted word a
 * list entry refers to without hunting for it. Findings with no span
 * (most tN/tW/alignment QAIssues — verse-level, not word-level) aren't
 * numbered, matching buildSegments' own existing offset filter. Callers
 * must pass the same verse's finding list to both sides for the numbers
 * to line up — true today since ReviewPanel's $selectedFindings and
 * VerseList's findingsByVerse[key] both read the exact same store entry.
 */
export function findingNumbers(findings: QaFinding[]): Map<string, number> {
  const spanned = findings
    .filter((f) => f.start_offset !== null && f.end_offset !== null)
    .sort((a, b) => (a.start_offset! - b.start_offset!) || a.id.localeCompare(b.id));
  const numbers = new Map<string, number>();
  spanned.forEach((f, index) => numbers.set(f.id, index + 1));
  return numbers;
}

export interface ExactTextRange {
  start: number;
  end: number;
}

/** Exact, non-overlapping occurrences matching the native tC occurrence model. */
export function exactTextRanges(text: string, selectedText: string): ExactTextRange[] {
  if (!selectedText) return [];
  const ranges: ExactTextRange[] = [];
  let from = 0;
  while (from <= text.length - selectedText.length) {
    const start = text.indexOf(selectedText, from);
    if (start < 0) break;
    const end = start + selectedText.length;
    ranges.push({ start, end });
    from = end;
  }
  return ranges;
}

interface ReviewSpan extends ExactTextRange {
  id: string;
  className: string;
  title: string;
  number?: number;
  /** Language QA spans only: languageQaMarkRank, lower wins. */
  lqaRank?: number;
}

/**
 * Splits verse text into segments based on findings' start/end offsets.
 * Findings without offsets (most tc_ai_bridge QAIssues — they're
 * verse-level, not span-level) don't produce an inline highlight; they
 * still show up in the review panel, just not underlined in the text.
 * Where spans overlap, a segment carries every covering finding id and the
 * union of the non-Language-QA classes, but only ONE Language QA class: the
 * highest-ranked covering finding's (languageQaMarkRank). Language QA ids are
 * listed after the others, in rank order, so the first one is the mark's
 * primary finding.
 */
export function buildSegments(
  text: string,
  findings: QaFinding[],
  nativeChecks: NativeCheckReview[] = [],
  aiReviews: AiCheckReview[] = [],
  languageQaFindings: LanguageQaFinding[] = [],
): TextSegment[] {
  const numbers = findingNumbers(findings);
  const spans: ReviewSpan[] = findings
    .filter((f) => f.start_offset !== null && f.end_offset !== null && f.end_offset! <= text.length)
    .map((finding) => ({
      start: finding.start_offset!, end: finding.end_offset!, id: finding.id,
      className: categoryClass(finding.category), title: finding.explanation,
      number: numbers.get(finding.id),
    }));

  for (const check of nativeChecks) {
    const className = check.tool === "translationNotes" ? "m-tn" : "m-tw";
    const label = check.tool === "translationNotes" ? "Translation Note" : "Translation Word";
    const detail = [
      `${label}: ${check.groupId || check.checkId}`,
      check.sourceQuote ? `Source: ${check.sourceQuote}` : "",
      check.occurrenceNote,
      `Selection: ${check.selectionStatus.replaceAll("_", " ")}`,
    ].filter(Boolean).join("\n");
    for (const selection of check.selections) {
      const ranges = exactTextRanges(text, selection.text);
      const range = ranges[selection.occurrence - 1];
      if (range && ranges.length === selection.occurrences) {
        spans.push({ ...range, id: check.checkId, className, title: detail });
      }
    }
  }

  // Every AI review with a proposed span is underlined, including verdict
  // "pass". The mark is not a severity signal -- it is where the tN/tW check
  // landed in this verse. A pass carries the exact selection the AI resolved,
  // and in Manual (advanced) reviewer mode that proposal is the ONLY thing
  // marking the words until a human applies it, because auto-application runs
  // in Auto (basic) mode alone -- see BridgeEngine._apply_basic_ai_selections.
  // Filtering passes out here left the verse blank until the reviewer selected
  // text by hand. Reviews with nothing_to_select carry no span and so mark
  // nothing anyway.
  //
  // New AI reviews carry exact translationCore occurrence metadata. Older cached
  // reviews fall back to unique-text matching rather than guessing a repeated span.
  for (const review of aiReviews) {
    const className = review.tool === "translationNotes" ? "m-tn" : "m-tw";
    const evidence = review.evidence_used
      .map((item) => String(item.title ?? item.identifier ?? item.kind ?? ""))
      .filter(Boolean)
      .join(", ");
    const title = [`AI proposal: ${review.rationale}`, evidence ? `Evidence: ${evidence}` : ""]
      .filter(Boolean).join("\n");
    if ((review.proposed_selections ?? []).length > 0) {
      for (const selection of review.proposed_selections) {
        const ranges = exactTextRanges(text, selection.text);
        const range = ranges[selection.occurrence - 1];
        if (range && ranges.length === selection.occurrences) {
          spans.push({ ...range, id: review.check_id, className, title });
        }
      }
    } else {
      for (const proposedText of review.proposed_selection_text) {
        const ranges = exactTextRanges(text, proposedText);
        if (ranges.length === 1) {
          spans.push({ ...ranges[0], id: review.check_id, className, title });
        }
      }
    }
  }

  // Language QA findings are offset-addressed the same way QaFinding is
  // (start/end rather than start_offset/end_offset -- the only real
  // difference), but they're a deliberately separate, disposable data model
  // (see language_qa_jobs.py's own docstring) and never get cast into a fake
  // QaFinding here, matching how nativeChecks/aiReviews above are mapped in
  // their own native shape rather than forced into QaFinding's either.
  // Only findings the engine marked `inline` carry a span; the rest are
  // listed in the panel only.
  for (const finding of languageQaFindings) {
    if (!finding.inline) continue;
    spans.push({
      start: finding.start, end: finding.end, id: finding.id,
      className: languageQaMarkClass(finding), title: finding.message,
      lqaRank: languageQaMarkRank(finding),
    });
  }

  if (spans.length === 0) {
    return [{ text, findingIds: [], className: null, title: "", numbers: [] }];
  }

  const boundaries = new Set<number>([0, text.length]);
  spans.forEach((span) => {
    boundaries.add(span.start);
    boundaries.add(span.end);
  });
  const points = Array.from(boundaries).sort((a, b) => a - b);

  const segments: TextSegment[] = [];
  for (let i = 0; i < points.length - 1; i++) {
    const start = points[i];
    const end = points[i + 1];
    const covering = spans.filter((span) => span.start <= start && span.end >= end);
    const other = covering.filter((span) => span.lqaRank === undefined);
    const languageQa = covering
      .filter((span) => span.lqaRank !== undefined)
      .sort((a, b) => (a.lqaRank! - b.lqaRank!) || a.id.localeCompare(b.id));
    covering.splice(0, covering.length, ...other, ...languageQa);
    const classes = Array.from(new Set([
      ...other.map((span) => span.className),
      ...(languageQa.length ? [languageQa[0].className] : []),
    ]));
    segments.push({
      text: text.slice(start, end),
      findingIds: Array.from(new Set(covering.map((span) => span.id))),
      className: classes.length > 0 ? classes.join(" ") : null,
      title: Array.from(new Set(covering.map((span) => span.title).filter(Boolean))).join("\n\n"),
      numbers: Array.from(new Set(
        covering.map((span) => span.number).filter((n): n is number => n !== undefined),
      )).sort((a, b) => a - b),
    });
  }
  return segments;
}
