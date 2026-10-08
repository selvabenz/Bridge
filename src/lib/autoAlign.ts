// Pure helpers for the automatic alignment surface of the Cross-Verse
// Alignment page (#222). The engine decides (alignment.window.autoAlign,
// #219); these only answer "what does this verse / this cell show".
//
// Suggestions and issues from a pass name words by tC signature, not by
// positional id, because a write made after the pass can renumber the ids.
// Everything here resolves a signature against the verse's *current* context.
import type {
  AlignmentContext, AlignmentToken, AutoAlignIssue, AutoAlignSuggestion, AutoAlignVerdict,
} from "./types/finding";
import { nullDecidedIds, unaccountedTargets, unrealizedSources } from "./alignmentGroups";

export const MAX_WINDOW = 5;

export function signatureOf(token: Pick<AlignmentToken, "word" | "occurrence" | "occurrences">): string {
  return `${token.word}␟${token.occurrence}␟${token.occurrences}`;
}

export type VerdictTone = "clean" | "review" | "idle" | "stale";

export interface VerdictView {
  tone: VerdictTone;
  glyph: string;
  label: string;
  lines: string[];
}

function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** The card for one verse in the verdict strip. */
export function verdictView(context: AlignmentContext, verdict: AutoAlignVerdict | null | undefined): VerdictView {
  const summary = context.autoAlign;
  if (!verdict || verdict.verdict === "REVERTED" || !summary || summary.verdict === "REVERTED") {
    return {
      tone: "idle", glyph: "·", label: "Not aligned yet",
      lines: [`${context.topTokens.length} source · ${context.bottomTokens.length} target words`],
    };
  }
  const issues = liveIssues(context, verdict);
  const suggestions = liveSuggestions(context, verdict);
  const omissions = issues.filter((i) => i.kind === "POSSIBLE_OMISSION").length;
  const additions = issues.filter((i) => i.kind === "POSSIBLE_ADDITION").length;
  const gaps = unrealizedSources(context).length + unaccountedTargets(context).length;
  if (summary.stale && (omissions || additions || suggestions.length || gaps)) {
    return { tone: "stale", glyph: "↻", label: "Edited since the pass", lines: ["Run it again to refresh", ""] };
  }
  if (!omissions && !additions && !suggestions.length && context.accounted) {
    const nulls = [...context.nullDecisions.source, ...context.nullDecisions.target].filter((n) => n.state === "active");
    const grammatical = nulls.filter((n) => n.reason === "GRAMMATICAL").length;
    const implicit = nulls.filter((n) => n.reason === "IMPLICIT").length;
    const explicit = nulls.filter((n) => n.reason === "EXPLICITATION").length;
    const elsewhere = context.crossVerseGroups.filter((g) => g.state === "active");
    const into = [...new Set(elsewhere.filter((g) => g.sourceVerse === context.verse).map((g) => g.targetVerse))];
    const from = [...new Set(elsewhere.filter((g) => g.targetVerse === context.verse).map((g) => g.sourceVerse))];
    const where = into.length || from.length
      ? [into.length ? `source → v.${into.join(", ")}` : "", from.length ? `target ← v.${from.join(", ")}` : ""].filter(Boolean).join(" · ")
      : "All words in this verse";
    const reasons = [
      grammatical ? `${grammatical} grammatical` : "", implicit ? `${implicit} implicit` : "",
      explicit ? `${explicit} explicitation` : "",
    ].filter(Boolean).join(" · ");
    return { tone: "clean", glyph: "✓", label: "Aligned", lines: [where, reasons || "Nothing left"] };
  }
  const first = [
    omissions ? plural(omissions, "possible omission") : "",
    additions ? plural(additions, "possible addition") : "",
  ].filter(Boolean).join(" · ");
  const second = [
    suggestions.length ? plural(suggestions.length, "suggestion") : "",
    !omissions && !additions && !suggestions.length && gaps ? plural(gaps, "word without a counterpart", "words without a counterpart") : "",
  ].filter(Boolean).join(" · ");
  return { tone: "review", glyph: "?", label: "Needs review", lines: [first || second, first ? second : ""] };
}

/** Is this token still without a home in the current context? */
export function isLiveGap(context: AlignmentContext, side: "source" | "target", signature: string): boolean {
  const pool = side === "source" ? unrealizedSources(context) : unaccountedTargets(context);
  return pool.some((token) => signatureOf(token) === signature);
}

/** Issues from the pass whose word is still a gap now. */
export function liveIssues(context: AlignmentContext, verdict: AutoAlignVerdict | null | undefined): AutoAlignIssue[] {
  return (verdict?.issues ?? []).filter((issue) => isLiveGap(context, issue.side, issue.signature));
}

/** Suggestions still worth showing in this verse: every word they would place
 *  is still homeless here. A suggestion the reviewer has already acted on in
 *  some other way drops away by itself. */
export function liveSuggestions(context: AlignmentContext, verdict: AutoAlignVerdict | null | undefined): AutoAlignSuggestion[] {
  return (verdict?.suggestions ?? []).filter((s) => {
    const ends = [s.source, s.target, s.token].filter((e): e is NonNullable<typeof e> => Boolean(e));
    return ends.every((end) => end.verse !== context.verse || isLiveGap(context, end.side, end.signature));
  });
}

/** The current positional id of a word named by signature, or null. */
export function idFor(context: AlignmentContext, side: "source" | "target", signature: string): string | null {
  const pool = side === "source" ? context.topTokens : context.bottomTokens;
  return pool.find((token) => signatureOf(token) === signature)?.id ?? null;
}

export function suggestionKey(s: AutoAlignSuggestion): string {
  const end = (e: AutoAlignSuggestion["source"]) => (e ? `${e.side}:${e.verse}:${e.signature}` : "-");
  return `${s.kind}|${end(s.source)}|${end(s.target)}|${end(s.token)}|${s.reason}`;
}

/** Suggestions a source cell shows: link suggestions from this word, and null
 *  suggestions about it. */
export function suggestionsForSource(list: AutoAlignSuggestion[], verse: string, signature: string): AutoAlignSuggestion[] {
  return list.filter((s) =>
    (s.kind === "link" && s.source?.verse === verse && s.source.signature === signature)
    || (s.kind === "null" && s.token?.side === "source" && s.token.verse === verse && s.token.signature === signature));
}

/** Suggestions a target word in the bank shows. */
export function suggestionsForTarget(list: AutoAlignSuggestion[], verse: string, signature: string): AutoAlignSuggestion[] {
  return list.filter((s) =>
    (s.kind === "link" && s.target?.verse === verse && s.target.signature === signature)
    || (s.kind === "null" && s.token?.side === "target" && s.token.verse === verse && s.token.signature === signature));
}

/** "2:1" for a group that is not 1:1; "" otherwise. */
export function relationTag(sources: number, targets: number): string {
  return sources === 1 && targets === 1 ? "" : `${sources}:${targets}`;
}

/** Did the last automatic pass write the tC group this source word is in? */
export function placedByPass(
  verdict: AutoAlignVerdict | null | undefined, topSignature: string, bottomSignatures: string[],
): boolean {
  const groups = verdict?.applied?.groups ?? [];
  return groups.some((g) => g.tops.includes(topSignature) && bottomSignatures.every((b) => g.bottoms.includes(b)));
}

/** A window the engine will accept: 1..MAX_WINDOW consecutive verses. */
export function windowProblem(selection: string[], chapterVerses: string[]): string {
  if (selection.length < 1) return "Choose at least one verse.";
  if (selection.length > MAX_WINDOW) return `Choose at most ${MAX_WINDOW} verses, or align the whole chapter.`;
  const positions = selection.map((v) => chapterVerses.indexOf(v)).sort((a, b) => a - b);
  if (positions.some((p) => p < 0)) return "A verse in the range is not in this chapter.";
  if (positions[positions.length - 1] - positions[0] + 1 !== positions.length) return "Choose consecutive verses.";
  return "";
}

export { nullDecidedIds };
