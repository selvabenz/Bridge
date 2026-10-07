import { get } from "svelte/store";
import { bridge } from "./api/bridgeClient";
import { nudgeLanguageQa } from "./languageQaInline";
import { languageQaFindingsByVerse, project } from "./stores";
import type { LanguageQaFinding } from "./types/languageQa";

/** The rule a learned fix's recurrences are reported under (language_qa_learned.py). */
export const LEARNED_RULE = "learned.replacement";

export function isLearnedFinding(finding: Pick<LanguageQaFinding, "rule" | "category">): boolean {
  return finding.rule === LEARNED_RULE || finding.category === "learned";
}

/** The (old, new) pair a learned finding offers: its text and first suggestion. */
export function learnedPairOf(finding: LanguageQaFinding): { old: string; newWord: string } | null {
  const newWord = finding.suggestions?.[0]?.text;
  return isLearnedFinding(finding) && newWord ? { old: finding.originalText, newWord } : null;
}

/**
 * Stop offering a learned fix everywhere. Drops every mark it drew in the
 * chapter on screen at once, then asks the engine (which re-runs a pass); puts
 * the marks back if the engine refuses. Records no decision: forgetting a fix
 * is not ignoring a finding. Returns an error message, or "" on success.
 */
export async function forgetLearnedFix(old: string, newWord: string): Promise<string> {
  const path = get(project)?.path;
  if (!path) return "No project is open.";
  const before = get(languageQaFindingsByVerse);
  const matches = (f: LanguageQaFinding) =>
    isLearnedFinding(f) && f.originalText === old && f.suggestions.some((s) => s.text === newWord);
  languageQaFindingsByVerse.set(Object.fromEntries(
    Object.entries(before).map(([key, findings]) => [key, findings.filter((f) => !matches(f))]),
  ));
  try {
    await bridge.languageQaLearnedForget(path, old, newWord);
    nudgeLanguageQa();
    return "";
  } catch (cause) {
    languageQaFindingsByVerse.set(before);
    return cause instanceof Error ? cause.message : String(cause);
  }
}
