import { get } from "svelte/store";
import { bridge } from "./api/bridgeClient";
import { nudgeLanguageQa } from "./languageQaInline";
import { languageQaFindingsByVerse, project } from "./stores";
import type { LanguageQaFinding } from "./types/languageQa";

/** The categories a project word silences (housestyle.PROJECT_WORD_CATEGORIES). */
const WORD_CATEGORIES = new Set(["typo", "name", "consistency", "learned"]);

/** A rule whose finding is a reviewed misspelling: its word cannot be "fine". */
export function isKnownMisspellingRule(ruleId: string): boolean {
  return /(^|[./])known-misspelling$/.test(ruleId);
}

/**
 * Whether "Add to the project word list" would silence this finding: one
 * word, in a category a project word hides, not a learned fix (that word is
 * the reviewer's own correction already, as the indic-qa editor says), and
 * not a reviewed misspelling. The verse menu and the review panel both ask
 * this, so they never disagree.
 */
export function canAddProjectWord(finding: Pick<LanguageQaFinding, "category" | "ruleId" | "originalText">): boolean {
  const word = finding.originalText.trim();
  return Boolean(word) && !/\s/.test(word) && finding.category !== "learned"
    && WORD_CATEGORIES.has(finding.category) && !isKnownMisspellingRule(finding.ruleId);
}

/**
 * Add words to the project word list: their spelling findings go from the
 * chapter on screen at once, then the engine records them (a pass follows).
 * Put back if the engine refuses. Returns an error message, or "".
 */
export async function addProjectWords(
  words: string[], scope: "book" | "project" = "book", projectPath?: string,
): Promise<string> {
  const path = projectPath ?? get(project)?.path;
  if (!path) return "No project is open.";
  const wanted = new Set(words.map((w) => w.normalize("NFC")));
  const before = get(languageQaFindingsByVerse);
  const silenced = (f: LanguageQaFinding) => WORD_CATEGORIES.has(f.category) && wanted.has(f.originalText.normalize("NFC"));
  languageQaFindingsByVerse.set(Object.fromEntries(
    Object.entries(before).map(([key, findings]) => [key, findings.filter((f) => !silenced(f))]),
  ));
  try {
    await bridge.languageQaWordsAdd(path, [...wanted], scope);
    nudgeLanguageQa();
    return "";
  } catch (cause) {
    languageQaFindingsByVerse.set(before);
    return cause instanceof Error ? cause.message : String(cause);
  }
}
