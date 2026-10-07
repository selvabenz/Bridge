import { writable, derived, get } from "svelte/store";
import type { AiCheckReview, AlignmentWorkStatus, NativeCheckReview, NavigationSyncState, ProjectInfo, QaFinding, VerseDisplay, VerseHeading } from "./types/finding";
import type { LanguageQaFinding, LanguageQaFlag } from "./types/languageQa";
import type { EngineLogEntry } from "./api/bridgeClient";

export function verseKey(chapter: string, verse: string): string {
  return `${chapter}:${verse}`;
}

export const project = writable<ProjectInfo | null>(null);
export const currentChapter = writable<string>("1");

// Keyed by chapter number -> list of verse numbers in that chapter.
export const chapterVerseNums = writable<Record<string, string[]>>({});

// Keyed by "chapter:verse" (verseKey) so multiple chapters can coexist
// without collisions — needed for chapter switching and "Run whole book"
// (verse "1" in chapter 1 and verse "1" in chapter 2 are different keys).
export const verseTexts = writable<Record<string, string>>({});
/** What the reader shows for each verse, from the engine (#91 Phase 2b): plain
 *  text, notes, style spans and the removed raw ranges. A verse with no entry
 *  (an optimistic save awaiting the engine's answer) renders its raw text. */
export const verseDisplay = writable<Record<string, VerseDisplay>>({});
/** Section headings keyed by the verse they INTRODUCE, so a heading renders
 *  above that verse rather than glued to the end of the previous one (#180). */
export const headingsByVerse = writable<Record<string, VerseHeading[]>>({});
export const findingsByVerse = writable<Record<string, QaFinding[]>>({});
export type CheckStatus = "pending" | "succeeded" | "failed" | "cancelled";
export const checkStatusByVerse = writable<Record<string, CheckStatus>>({});
export const alignmentStatusByVerse = writable<Record<string, AlignmentWorkStatus>>({});
export const nativeChecksByVerse = writable<Record<string, NativeCheckReview[]>>({});
export const aiCheckReviewsByVerse = writable<Record<string, AiCheckReview[]>>({});
// Populated only by languageQaInline.ts (started from App.svelte) from the
// unpaged languageQa.inline RPC, for the chapter on screen -- disposable,
// recomputed on every scan pass, never the source of truth the way
// findingsByVerse is for QaFinding. Holds only the engine's inline rules
// (INLINE_RULES in language_qa.py); VerseList reads this for inline marks.
// Offsets are raw verse code-point offsets, like QaFinding's.
export const languageQaFindingsByVerse = writable<Record<string, LanguageQaFinding[]>>({});
// Reviewer flags (workbench v6) of the chapters loaded, keyed by the verseKey
// of each flag's first verse; deleted ones are not held.
export const flagsByVerse = writable<Record<string, LanguageQaFlag[]>>({});
// Recorded edits per verse (chapter.verseData's editCounts), for the ↺ mark;
// bumped by every save the editor makes.
export const historyCountByVerse = writable<Record<string, number>>({});
export type ReviewerMode = "basic" | "advanced";
export const reviewerMode = writable<ReviewerMode>("basic");

// One capability, not two experiences: may the reviewer hand-edit a
// translationCore selection? Safe, evidence-grounded AI selections are applied
// either way (BridgeEngine._apply_safe_ai_selections runs for every review),
// so turning this on adds the override editor rather than taking the automatic
// selection away.
//
// The stored/protocol values stay "basic"/"advanced": AppSettings, the engine's
// reviewerMode field and every persisted review-provenance record already use
// them, so renaming the wire value would need a settings migration for no
// user-visible gain. "advanced" == override allowed.
export const allowManualOverride = derived(
  reviewerMode, ($mode) => $mode === "advanced",
);

export function manualOverrideMode(allowed: boolean): ReviewerMode {
  return allowed ? "advanced" : "basic";
}

// Which chapters have had their verse text + checks loaded already, so
// switching back to a chapter you've already visited doesn't re-fetch.
export const loadedChapters = writable<Record<string, boolean>>({});

export const selectedVerse = writable<string | null>(null);
/** Ctrl/Shift-click multi-selection in the verse list (#118), feeding the
 *  Cross-verse alignment range picker. `selectedVerse` stays the single
 *  active verse; this is a separate, chapter-scoped set of verse strings. */
export const selectedVerseSet = writable<string[]>([]);

// Clears everything keyed by the previously open book's chapter/verse
// numbers. Chapter "1" in one book is unrelated to chapter "1" in another,
// so switching books must not let stale entries from the old book show
// through under the new book's chapter/verse selectors.
export function resetBookState(): void {
  chapterVerseNums.set({});
  verseTexts.set({});
  verseDisplay.set({});
  headingsByVerse.set({});
  findingsByVerse.set({});
  checkStatusByVerse.set({});
  alignmentStatusByVerse.set({});
  nativeChecksByVerse.set({});
  aiCheckReviewsByVerse.set({});
  languageQaFindingsByVerse.set({});
  flagsByVerse.set({});
  historyCountByVerse.set({});
  loadedChapters.set({});
  selectedVerse.set(null);
  selectedVerseSet.set([]);
  checkingProgress.set({ running: false, percent: 0, label: "", jobId: "", state: "idle", error: "", scope: "chapter" });
}

export interface CheckingProgress {
  running: boolean;
  percent: number;
  label: string;
  jobId: string;
  state: "idle" | "queued" | "running" | "cancelling" | "succeeded" | "failed" | "cancelled";
  error: string;
  scope: "chapter" | "book";
}

export const checkingProgress = writable<CheckingProgress>({
  running: false, percent: 0, label: "", jobId: "", state: "idle", error: "", scope: "chapter",
});

export const settingsOpen = writable(false);
export const exportOpen = writable(false);
export const diagnosticsOpen = writable(false);

const disconnectedNavigationTarget = {
  enabled: false, checking: false, connected: false, reference: "", error: "", checkedAt: 0,
};
export const navigationStatus = writable<NavigationSyncState>({
  enabled: false,
  ownsNavigation: false,
  ownerConflict: false,
  currentReference: "",
  currentOrigin: "bridge",
  candidate: null,
  paratext: { ...disconnectedNavigationTarget },
  logos: { ...disconnectedNavigationTarget },
});

const ENGINE_LOG_CAPACITY = 400;
export const engineLog = writable<EngineLogEntry[]>([]);

export function appendEngineLog(entry: EngineLogEntry): void {
  engineLog.update((entries) => {
    const next = [...entries, entry];
    return next.length > ENGINE_LOG_CAPACITY ? next.slice(next.length - ENGINE_LOG_CAPACITY) : next;
  });
}

export const verseNums = derived(
  [chapterVerseNums, currentChapter],
  ([$chapterVerseNums, $currentChapter]) => $chapterVerseNums[$currentChapter] ?? []
);

export const selectedFindings = derived(
  [findingsByVerse, currentChapter, selectedVerse],
  ([$findingsByVerse, $currentChapter, $selectedVerse]) =>
    $selectedVerse ? $findingsByVerse[verseKey($currentChapter, $selectedVerse)] ?? [] : []
);

export const approvedCount = derived(
  [findingsByVerse, checkStatusByVerse, currentChapter, verseNums],
  ([$findingsByVerse, $checkStatusByVerse, $currentChapter, $verseNums]) =>
    $verseNums.filter((v) =>
      $checkStatusByVerse[verseKey($currentChapter, v)] === "succeeded" &&
      ($findingsByVerse[verseKey($currentChapter, v)] ?? []).every((f) => f.status !== "open")
    ).length
);

// Book-wide: how many chapters are fully approved, for "Run whole book"
// progress and the Export-enabled check (all chapters, not just current).
export function bookApprovedSummary(): { approvedChapters: number; totalChapters: number } {
  const proj = get(project);
  const loaded = get(loadedChapters);
  const cvn = get(chapterVerseNums);
  const fbv = get(findingsByVerse);
  const statuses = get(checkStatusByVerse);
  if (!proj) return { approvedChapters: 0, totalChapters: 0 };
  const chapters = proj.chapters;
  let approvedChapters = 0;
  for (const ch of chapters) {
    if (!loaded[ch]) continue;
    const verses = cvn[ch] ?? [];
    if (verses.length === 0) continue;
    const allApproved = verses.every((v) =>
      statuses[verseKey(ch, v)] === "succeeded" &&
      (fbv[verseKey(ch, v)] ?? []).every((f) => f.status !== "open")
    );
    if (allApproved) approvedChapters++;
  }
  return { approvedChapters, totalChapters: chapters.length };
}
