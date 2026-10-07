// Verse-edit lifecycle, shared between VerseList.svelte (renders the inline
// textarea in the left editor panel) and ReviewPanel.svelte (the "Edit
// verse" triggers, plus the recheck/AI-review follow-up once a save
// completes). Moved out of ReviewPanel.svelte so the edit textbox could
// move into the left panel — there's much more width there than the
// 400px-wide review panel — without either component needing to reach
// into the other's internals.
import { get, writable } from "svelte/store";
import { bridge } from "./api/bridgeClient";
import { decideLanguageQaFinding } from "./findingActions";
import { nudgeLanguageQa } from "./languageQaInline";
import type { QaFinding, VerseDisplay } from "./types/finding";
import type { LanguageQaFinding, LanguageQaSuggestion } from "./types/languageQa";
import type { CorrectionApplicationIntent } from "./types/correctionReview";
import {
  alignmentStatusByVerse, checkStatusByVerse, checkingProgress, findingsByVerse,
  nativeChecksByVerse, aiCheckReviewsByVerse, languageQaFindingsByVerse, verseDisplay, verseKey, verseTexts,
} from "./stores";

// The "BOOK C:V" shape the engine's displayed references use. Verse bridges
// ("3-4") and lettered segments ("3a") are treated the way the rest of the UI
// keys verses — the group stops before a bridge's trailing "-4", while letters
// stay attached to the verse group. (The semantic-validation screen that once
// shared this regex was removed in #100.)
const DISPLAYED_REFERENCE = /^[A-Z0-9]+\s+([^:]+):([^\s-]+)/i;

/**
 * A correction application writes the new target verse text on the backend
 * before the frontend ever hears about it (CorrectionReviewPanel just
 * receives the outcome) — so once applicationState is COMPLETED, reflect
 * the backend's own record of what it wrote (resultMetadata.canonicalEdit
 * .newText) into verseTexts, rather than computing the text client-side.
 * Returns the verseKey touched, or "" if the response didn't carry a
 * usable target reference/text (nothing is changed in that case).
 */
export function refreshVerseTextFromApplication(
  application: Pick<CorrectionApplicationIntent, "targetDisplayedReference" | "resultMetadata">,
): string {
  const match = application.targetDisplayedReference.match(DISPLAYED_REFERENCE);
  if (!match) return "";
  const canonicalEdit = application.resultMetadata?.canonicalEdit;
  const edit = canonicalEdit && typeof canonicalEdit === "object"
    ? (canonicalEdit as Record<string, unknown>)
    : {};
  const newText = edit.newText;
  if (typeof newText !== "string") return "";
  const key = verseKey(match[1], match[2]);
  verseTexts.update((t) => ({ ...t, [key]: newText }));
  // The engine's record also says what the new text shows (#91); without it
  // the reader falls back to the raw string until the chapter reloads.
  setDisplay(key, isVerseDisplay(edit.display) ? edit.display : undefined);
  alignmentStatusByVerse.update((values) => ({ ...values, [key]: "invalid" }));
  return key;
}

/**
 * Text the engine wrote on the reviewer's behalf (a scoped Language QA
 * correction, or its undo): each loaded verse shows its new text through the
 * same path a saved edit uses (its Language QA, AI and native marks dropped,
 * alignment marked invalid). A verse of a chapter not loaded yet simply loads
 * fresh later. One nudge, so Language QA runs once for all of them.
 */
export function applyEngineChanges(changed: { chapter: string; verse: string; newText: string; display?: VerseDisplay }[]): void {
  const loaded = get(verseTexts);
  for (const change of changed) {
    const key = verseKey(change.chapter, change.verse);
    if (key in loaded) showVerseText(key, change.newText, change.display);
  }
  if (changed.length) nudgeLanguageQa();
}

export const editingChapter = writable("");
export const editingVerse = writable("");
export const editText = writable("");
export const editSaving = writable(false);
export const editError = writable("");
export const editErrorKey = writable("");
export const recheckingKey = writable("");
export const recheckedKey = writable("");

let onSaved:
  | ((info: {
      chapter: string; verse: string; issueResolutionsNeedingRecheck: number; acceptFindingId: string;
    }) => void)
  | null = null;

/** ReviewPanel registers its own follow-up (refresh translation helps,
 * maybe restart AI review) here rather than saveVerseEdit calling back
 * into a specific component instance. */
export function setVerseEditSavedHook(hook: typeof onSaved): void {
  onSaved = hook;
}

// Set by ReviewPanel's "Accept and edit" (as opposed to a plain "Edit
// verse") right after starting an edit session, so a successful save can
// report which specific finding to mark "accepted" — cleared by
// startVerseEdit (a fresh edit session starts clean) and by cancelVerseEdit
// (a true cancel drops the accept intent too), but saveVerseEdit captures
// its value into a local const before its own internal cancelVerseEdit()
// call clears it, so a successful save still has it for the onSaved payload.
let pendingAcceptFindingId = "";

export function setPendingAcceptFinding(findingId: string): void {
  pendingAcceptFindingId = findingId;
}

export function startVerseEdit(chapter: string, verse: string): boolean {
  if (!verse || get(checkingProgress).running || get(editSaving) || get(recheckingKey)) return false;
  editingChapter.set(chapter);
  editingVerse.set(verse);
  editText.set(get(verseTexts)[verseKey(chapter, verse)] ?? "");
  editError.set("");
  editErrorKey.set("");
  pendingAcceptFindingId = "";
  return true;
}

export function cancelVerseEdit(): void {
  editingChapter.set("");
  editingVerse.set("");
  pendingAcceptFindingId = "";
}

function withoutKey<T>(values: Record<string, T>, key: string): Record<string, T> {
  const next = { ...values };
  delete next[key];
  return next;
}

/**
 * Show a saved verse text and drop everything derived from the old text.
 * Returns an undo that restores the text and the derived stores exactly.
 * Every Language QA offset in the verse becomes stale, whoever made the edit
 * (typed, a Use, a Greek Room fix); languageQaInline.ts repopulates it from
 * the next pass, and until then no mark sits on the wrong word.
 */
function isVerseDisplay(value: unknown): value is VerseDisplay {
  return !!value && typeof value === "object" && typeof (value as VerseDisplay).plain === "string"
    && Array.isArray((value as VerseDisplay).removed);
}

/** Set, or clear, what the reader shows for a verse. Cleared means "render the
 *  raw text until the engine says otherwise" (an optimistic save in flight). */
function setDisplay(key: string, display: VerseDisplay | undefined): void {
  verseDisplay.update((values) => (display ? { ...values, [key]: display } : withoutKey(values, key)));
}

function showVerseText(key: string, text: string, display?: VerseDisplay): () => void {
  const before = {
    text: get(verseTexts)[key],
    display: get(verseDisplay)[key],
    ai: get(aiCheckReviewsByVerse)[key],
    native: get(nativeChecksByVerse)[key],
    languageQa: get(languageQaFindingsByVerse)[key],
    alignment: get(alignmentStatusByVerse)[key],
  };
  verseTexts.update((t) => ({ ...t, [key]: text }));
  // The old display describes the old text: drop it (or replace it) in the
  // same tick, or the reader shows the old clean text over the new raw one.
  setDisplay(key, display);
  aiCheckReviewsByVerse.update((values) => withoutKey(values, key));
  nativeChecksByVerse.update((values) => withoutKey(values, key));
  languageQaFindingsByVerse.update((values) => withoutKey(values, key));
  alignmentStatusByVerse.update((values) => ({ ...values, [key]: "invalid" }));
  return () => {
    verseTexts.update((t) => (before.text === undefined ? withoutKey(t, key) : { ...t, [key]: before.text }));
    setDisplay(key, before.display);
    if (before.ai !== undefined) aiCheckReviewsByVerse.update((v) => ({ ...v, [key]: before.ai! }));
    if (before.native !== undefined) nativeChecksByVerse.update((v) => ({ ...v, [key]: before.native! }));
    if (before.languageQa !== undefined) languageQaFindingsByVerse.update((v) => ({ ...v, [key]: before.languageQa! }));
    alignmentStatusByVerse.update((v) =>
      before.alignment === undefined ? withoutKey(v, key) : { ...v, [key]: before.alignment! });
  };
}

/**
 * Save the open edit, then re-check the verse.
 *
 * `optimistic` (a Language QA Use): the new text is shown, and the editor
 * closed, before the engine answers, so the click never waits on it. If the
 * save fails, the old text and everything derived from it are put back and
 * editError explains why. A typed save keeps the editor open until the
 * engine has accepted the text, so a refused save can be corrected in place.
 */
export async function saveVerseEdit({ optimistic = false }: { optimistic?: boolean } = {}): Promise<boolean> {
  const chapter = get(editingChapter);
  const verse = get(editingVerse);
  if (!chapter || !verse) return false;
  const key = verseKey(chapter, verse);
  const text = get(editText);
  if (text.trim() === (get(verseTexts)[key] ?? "").trim()) {
    // No real change — apply_scripture_edit rejects this as a no-op
    // rather than journaling a spurious edit, so don't call it.
    cancelVerseEdit();
    return false;
  }
  const acceptFindingId = pendingAcceptFindingId;
  editError.set("");
  editSaving.set(true);
  let undo: (() => void) | null = null;
  if (optimistic) {
    undo = showVerseText(key, text);
    cancelVerseEdit();
  }
  try {
    const editResult = await bridge.editVerse(chapter, verse, text);
    undo = null;
    nudgeLanguageQa();  // the edit started a new Language QA pass
    if (!optimistic) {
      showVerseText(key, text, editResult.display);
      cancelVerseEdit();
    } else {
      // The optimistic text has been showing raw; now the engine has said
      // what it shows.
      setDisplay(key, editResult.display);
    }
    recheckingKey.set(key);
    recheckedKey.set("");
    checkStatusByVerse.update((map) => ({ ...map, [key]: "pending" }));
    const findings = await bridge.runVerseChecks(chapter, verse, ["local", "greekroom"]);
    findingsByVerse.update((map) => ({ ...map, [key]: findings }));
    checkStatusByVerse.update((map) => ({ ...map, [key]: "succeeded" }));
    recheckingKey.set("");
    recheckedKey.set(key);
    onSaved?.({
      chapter, verse, issueResolutionsNeedingRecheck: editResult.issueResolutionsNeedingRecheck,
      acceptFindingId,
    });
    window.setTimeout(() => {
      if (get(recheckedKey) === key) recheckedKey.set("");
    }, 3500);
    return true;
  } catch (e) {
    // Only an unconfirmed optimistic text is rolled back. Once the engine has
    // saved it, a failed re-check leaves the saved text showing.
    undo?.();
    recheckingKey.set("");
    checkStatusByVerse.update((map) => ({ ...map, [key]: "failed" }));
    editError.set(e instanceof Error ? e.message : String(e));
    editErrorKey.set(key);
    return false;
  } finally {
    editSaving.set(false);
  }
}

export interface FindingFixOutcome {
  ok: boolean;
  message: string;
}

/** Apply a finding's exact replacement through the normal editor/save/re-check path. */
export async function applySuggestedFindingFix(finding: QaFinding): Promise<FindingFixOutcome> {
  if (finding.suggested_replacement === null
      || finding.start_offset === null
      || finding.end_offset === null) {
    return { ok: false, message: "No proposed fix is available for this finding." };
  }
  const chapter = String(finding.chapter);
  const verse = String(finding.verse);
  const key = verseKey(chapter, verse);
  const current = get(verseTexts)[key];
  if (current === undefined) {
    return { ok: false, message: "The verse text is no longer loaded." };
  }

  // Engine offsets are Unicode code-point offsets. Array.from avoids moving
  // the replacement when the verse contains astral characters.
  const points = Array.from(current);
  const start = finding.start_offset;
  const end = finding.end_offset;
  if (start < 0 || end < start || end > points.length) {
    return { ok: false, message: "The proposed fix no longer matches the current verse." };
  }
  const original = points.slice(start, end).join("");
  if (finding.original_text && original !== finding.original_text) {
    return { ok: false, message: "The proposed fix is stale because the verse text changed." };
  }
  if (!startVerseEdit(chapter, verse)) {
    return { ok: false, message: "Finish the current check or edit before applying this fix." };
  }
  editText.set(
    points.slice(0, start).join("")
      + finding.suggested_replacement
      + points.slice(end).join(""),
  );
  setPendingAcceptFinding(finding.id);
  const saved = await saveVerseEdit();
  return saved
    ? { ok: true, message: "Fix applied and verse re-checked." }
    : { ok: false, message: get(editError) || "The fix could not be applied." };
}

/**
 * Termbase v2's "Use <preferred form>" action. Same splice/save/re-check
 * path as applySuggestedFindingFix above, adapted for Language QA's own
 * finding shape (start/end, not start_offset/end_offset -- see
 * language_qa.py's module docstring on why the two models are kept
 * separate). Language QA has no onSaved hook of its own (that mechanism is
 * ReviewPanel/QaFinding-specific), so the accepted decision is recorded
 * directly here via the same generic verse.decide endpoint every other
 * finding type already uses -- language_qa_jobs.py's _scan() reads it back
 * on the next pass to keep the finding from reappearing.
 */
export async function applyLanguageQaSuggestedFix(
  finding: LanguageQaFinding,
  chosen: LanguageQaSuggestion | null = null,
): Promise<FindingFixOutcome> {
  // The reviewer's pick, or the top-ranked suggestion. suggestedReplacement
  // is the one-release alias for findings from an older engine.
  const pick: LanguageQaSuggestion | null = chosen ?? finding.suggestions?.[0]
    ?? (finding.suggestedReplacement
      ? { text: finding.suggestedReplacement, rank: 1, source: "rule", rationale: "" }
      : null);
  if (!pick) {
    return { ok: false, message: "No suggested form is recorded for this term." };
  }
  const chapter = finding.chapter;
  const verse = finding.verse;
  const key = verseKey(chapter, verse);
  const current = get(verseTexts)[key];
  if (current === undefined) {
    return { ok: false, message: "The verse text is no longer loaded." };
  }
  const points = Array.from(current);
  const { start, end } = finding;
  if (start < 0 || end < start || end > points.length) {
    return { ok: false, message: "The suggested fix no longer matches the current verse." };
  }
  const original = points.slice(start, end).join("");
  if (finding.originalText && original !== finding.originalText) {
    return { ok: false, message: "The suggested fix is stale because the verse text changed." };
  }
  if (!startVerseEdit(chapter, verse)) {
    return { ok: false, message: "Finish the current check or edit before applying this fix." };
  }
  editText.set(points.slice(0, start).join("") + pick.text + points.slice(end).join(""));
  const saved = await saveVerseEdit({ optimistic: true });
  if (!saved) {
    return { ok: false, message: get(editError) || "The fix could not be applied." };
  }
  try {
    await decideLanguageQaFinding(finding, "accepted", pick);
  } catch {
    // The text fix already landed and the verse was re-checked -- a
    // decision-recording failure here must not be reported as the fix
    // itself having failed.
  }
  return { ok: true, message: "Fix applied and verse re-checked." };
}
