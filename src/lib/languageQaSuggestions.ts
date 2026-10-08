import type { LanguageQaFinding, LanguageQaSuggestion } from "./types/languageQa";

/** The indic-qa editor's menu offers nine numbered suggestions. */
export const MAX_SUGGESTIONS = 9;

/**
 * A finding's suggestions as the indic-qa editor lists them: the reviewer's
 * learned fixes first, then the rest in rank order, at most nine. Falls back
 * to the one-release `suggestedReplacement` alias for a finding from an older
 * engine.
 */
export function orderedSuggestions(finding: LanguageQaFinding): LanguageQaSuggestion[] {
  const ranked = finding.suggestions?.length
    ? finding.suggestions
    : finding.suggestedReplacement
      ? [{ text: finding.suggestedReplacement, rank: 1, source: "rule" as const, rationale: "" }]
      : [];
  // Learned first, then the cut: a learned fix is never the one dropped.
  return [...ranked.filter((s) => s.source === "learned"), ...ranked.filter((s) => s.source !== "learned")]
    .slice(0, MAX_SUGGESTIONS);
}

/** What is shown beside a suggestion, as the indic-qa editor shows it: the
 * checker's edit class and how often the form is used ("vowel length · 412"),
 * "split", or "learned fix". Empty for a rule's fix, which has neither. */
export function suggestionTag(s: LanguageQaSuggestion): string {
  if (s.source === "learned") return "learned fix";
  if (s.kind === "split") return "split";
  return [s.kind ? s.kind.replace(/_/g, " ") : "", s.freq !== undefined ? String(s.freq) : ""]
    .filter(Boolean).join(" · ");
}
