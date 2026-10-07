import { beforeEach, describe, expect, it, vi } from "vitest";
import { get } from "svelte/store";

const { editVerse, runVerseChecks, decideVerse } = vi.hoisted(() => ({
  editVerse: vi.fn(),
  runVerseChecks: vi.fn(),
  decideVerse: vi.fn(),
}));

vi.mock("../../api/bridgeClient", () => ({
  bridge: { editVerse, runVerseChecks, decideVerse },
}));

import {
  applyLanguageQaSuggestedFix, applySuggestedFindingFix, cancelVerseEdit, editText, editWarnings, saveVerseEdit,
  startVerseEdit,
} from "../../verseEditor";
import {
  checkingProgress,
  historyCountByVerse,
  findingsByVerse,
  languageQaFindingsByVerse,
  verseKey,
  verseDisplay,
  verseTexts,
} from "../../stores";
import type { QaFinding } from "../../types/finding";
import type { LanguageQaFinding } from "../../types/languageQa";
import { lqaFinding } from "./languageQaFixture";

function finding(overrides: Partial<QaFinding> = {}): QaFinding {
  return {
    id: "finding-1", project_id: "project-1", book: "php", chapter: 1, verse: 6,
    start_offset: 0, end_offset: 5, original_text: "alpha", engine: "wildebeest",
    check_type: "normalization", category: "unicode", severity: "low", confidence: 1,
    suggested_replacement: "omega", explanation: "Normalize text", evidence: [],
    engine_version: "1", resource_versions: {}, status: "open", human_comment: null,
    created_at: "2026-09-07T00:00:00Z", resolved_at: null,
    ...overrides,
  };
}

describe("applySuggestedFindingFix", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    cancelVerseEdit();
    checkingProgress.set({
      running: false, percent: 0, label: "", jobId: "", state: "idle", error: "", scope: "chapter",
    });
    verseTexts.set({ [verseKey("1", "6")]: "alpha beta" });
    findingsByVerse.set({ [verseKey("1", "6")]: [finding()] });
    editVerse.mockResolvedValue({ issueResolutionsNeedingRecheck: 0 });
    runVerseChecks.mockResolvedValue([]);
  });

  it("uses the normal scripture edit and re-check path", async () => {
    const result = await applySuggestedFindingFix(finding());
    expect(result.ok).toBe(true);
    expect(editVerse).toHaveBeenCalledWith("1", "6", "omega beta");
    expect(runVerseChecks).toHaveBeenCalledWith("1", "6", ["local", "greekroom"]);
    expect(get(verseTexts)[verseKey("1", "6")]).toBe("omega beta");
  });

  it("does not write when the finding no longer matches the verse", async () => {
    const result = await applySuggestedFindingFix(finding({ original_text: "moved" }));
    expect(result.ok).toBe(false);
    expect(result.message).toMatch(/stale/i);
    expect(editVerse).not.toHaveBeenCalled();
  });

  it("clears the edited verse's Language QA marks on success, and only that verse's", async () => {
    // Any saved edit makes every Language QA offset in that verse stale, not
    // only an edit made through a Language QA Use.
    const mark = languageQaFinding();
    languageQaFindingsByVerse.set({ "1:6": [mark], "1:7": [{ ...mark, id: "other", verse: "7" }] });
    const result = await applySuggestedFindingFix(finding());
    expect(result.ok).toBe(true);
    expect(get(languageQaFindingsByVerse)["1:6"]).toBeUndefined();
    expect(get(languageQaFindingsByVerse)["1:7"]).toEqual([expect.objectContaining({ id: "other" })]);
  });

  it("clears the verse's Language QA marks after a hand-typed edit is saved, until the next poll", async () => {
    // No Use involved: the translator opens the editor, types, and saves.
    languageQaFindingsByVerse.set({ "1:6": [languageQaFinding()] });
    expect(startVerseEdit("1", "6")).toBe(true);
    editText.set("alpha gamma");
    expect(await saveVerseEdit()).toBe(true);
    expect(editVerse).toHaveBeenCalledWith("1", "6", "alpha gamma");
    expect(get(languageQaFindingsByVerse)["1:6"]).toBeUndefined();
  });

  it("keeps the Language QA marks when the save fails", async () => {
    languageQaFindingsByVerse.set({ "1:6": [languageQaFinding()] });
    editVerse.mockRejectedValue(new Error("disk full"));
    const result = await applySuggestedFindingFix(finding());
    expect(result.ok).toBe(false);
    expect(get(languageQaFindingsByVerse)["1:6"]).toHaveLength(1);
  });
});

function languageQaFinding(overrides: Partial<LanguageQaFinding> = {}): LanguageQaFinding {
  return lqaFinding({
    id: "term-1", rule: "terminology.deprecated-form", start: 0, end: 5, originalText: "alpha",
    message: "Deprecated form.", suggestedReplacement: "omega", ...overrides,
  });
}

describe("applyLanguageQaSuggestedFix", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    cancelVerseEdit();
    checkingProgress.set({
      running: false, percent: 0, label: "", jobId: "", state: "idle", error: "", scope: "chapter",
    });
    verseTexts.set({ [verseKey("1", "6")]: "alpha beta" });
    editVerse.mockResolvedValue({ issueResolutionsNeedingRecheck: 0 });
    runVerseChecks.mockResolvedValue([]);
    decideVerse.mockResolvedValue({});
  });

  it("uses the normal scripture edit/re-check path, then records the decision via verse.decide", async () => {
    const result = await applyLanguageQaSuggestedFix(languageQaFinding());
    expect(result.ok).toBe(true);
    expect(editVerse).toHaveBeenCalledWith("1", "6", "omega beta");
    expect(runVerseChecks).toHaveBeenCalledWith("1", "6", ["local", "greekroom"]);
    // The issue marks it as Language QA, so the engine keeps it out of the
    // review-progress rollup, and records what the reviewer saw.
    expect(decideVerse).toHaveBeenCalledWith("1", "6", "term-1", "accepted", undefined, {
      source: "languageQa", rule: "terminology.deprecated-form", ruleId: "project/terminology.deprecated-form",
      ruleVersion: "language-qa-7", packVersion: "language-qa-7", ruleRevision: 1,
      layer: "housestyle", category: "termbase", originalText: "alpha",
      suggestedReplacement: "omega", chosenSuggestion: "omega", chosenRank: 1,
      message: "Deprecated form.", start: 0, end: 5,
    });
    expect(get(verseTexts)[verseKey("1", "6")]).toBe("omega beta");
  });

  it("applies the suggestion the reviewer chose, not always the first", async () => {
    const finding = languageQaFinding({ suggestions: [
      { text: "omega", rank: 1, source: "termbase", rationale: "first" },
      { text: "psi", rank: 2, source: "termbase", rationale: "second" },
    ] });
    const result = await applyLanguageQaSuggestedFix(finding, finding.suggestions[1]);
    expect(result.ok).toBe(true);
    expect(editVerse).toHaveBeenCalledWith("1", "6", "psi beta");
    expect(decideVerse).toHaveBeenCalledWith("1", "6", "term-1", "accepted", undefined,
      expect.objectContaining({ chosenSuggestion: "psi", chosenRank: 2 }));
  });

  it("does not write when the finding no longer matches the verse", async () => {
    const result = await applyLanguageQaSuggestedFix(languageQaFinding({ originalText: "moved" }));
    expect(result.ok).toBe(false);
    expect(result.message).toMatch(/stale/i);
    expect(editVerse).not.toHaveBeenCalled();
    expect(decideVerse).not.toHaveBeenCalled();
  });

  it("refuses with no suggested form rather than inventing one", async () => {
    const result = await applyLanguageQaSuggestedFix(languageQaFinding({ suggestedReplacement: null }));
    expect(result.ok).toBe(false);
    expect(editVerse).not.toHaveBeenCalled();
  });

  it("still reports success if recording the decision fails -- the text fix already landed", async () => {
    decideVerse.mockRejectedValue(new Error("workbench unavailable"));
    const result = await applyLanguageQaSuggestedFix(languageQaFinding());
    expect(result.ok).toBe(true);
    expect(editVerse).toHaveBeenCalled();
  });

  it("works identically for a tamil.vallinam-missing finding -- the logic is rule-agnostic", async () => {
    const original = "அப்படி கூறினான்";
    verseTexts.set({ [verseKey("1", "6")]: `${original} பின்னர்` });
    const result = await applyLanguageQaSuggestedFix(languageQaFinding({
      id: "vallinam-1", rule: "tamil.vallinam-missing",
      start: 0, end: Array.from(original).length, originalText: original,
      suggestedReplacement: "அப்படிக் கூறினான்",
    }));
    expect(result.ok).toBe(true);
    expect(editVerse).toHaveBeenCalledWith("1", "6", "அப்படிக் கூறினான் பின்னர்");
    expect(decideVerse).toHaveBeenCalledWith("1", "6", "vallinam-1", "accepted", undefined,
      expect.objectContaining({ source: "languageQa", rule: "tamil.vallinam-missing" }));
  });

  it("splices a footnoted verse at the engine's raw offsets and keeps the note byte-identical", async () => {
    // The engine scans the visible text but reports raw code-point offsets;
    // the fix uses them as-is, with no remapping in either direction.
    const raw = "அவன் சொன்னான்\\f + \\ft குறிப்பு\\f* அந்த காகம் பறந்தது.";
    const flagged = "அந்த காகம்";
    const start = Array.from(raw.slice(0, raw.indexOf(flagged))).length;
    verseTexts.set({ [verseKey("1", "6")]: raw });
    const result = await applyLanguageQaSuggestedFix(languageQaFinding({
      id: "vallinam-2", rule: "tamil.vallinam-missing", start, end: start + Array.from(flagged).length,
      originalText: flagged, suggestedReplacement: "அந்தக் காகம்",
    }));
    expect(result.ok).toBe(true);
    expect(editVerse).toHaveBeenCalledWith(
      "1", "6", "அவன் சொன்னான்\\f + \\ft குறிப்பு\\f* அந்தக் காகம் பறந்தது.");
  });
});

describe("the display payload after a save (#91 Phase 2b)", () => {
  const key = verseKey("1", "6");
  const display = (plain: string) => ({ plain, notes: [], removed: [], styles: [], warnings: [] });

  beforeEach(() => {
    vi.clearAllMocks();
    cancelVerseEdit();
    checkingProgress.set({
      running: false, percent: 0, label: "", jobId: "", state: "idle", error: "", scope: "chapter",
    });
    verseTexts.set({ [key]: "alpha beta" });
    verseDisplay.set({ [key]: display("alpha beta") });
    findingsByVerse.set({ [key]: [finding()] });
    runVerseChecks.mockResolvedValue([]);
  });

  it("stores what the engine says the saved text shows", async () => {
    editVerse.mockResolvedValue({ issueResolutionsNeedingRecheck: 0, display: display("omega beta") });
    const result = await applySuggestedFindingFix(finding());
    expect(result.ok).toBe(true);
    expect(get(verseDisplay)[key]).toEqual(display("omega beta"));
  });

  it("drops the old display while an optimistic save is in flight, then takes the engine's", async () => {
    let seen: unknown = "unset";
    editVerse.mockImplementation(async () => {
      seen = get(verseDisplay)[key];  // during the round trip
      return { issueResolutionsNeedingRecheck: 0, display: display("alpha gamma") };
    });
    const mark = languageQaFinding({ start: 6, end: 10, originalText: "beta", suggestedReplacement: "gamma" });
    const result = await applyLanguageQaSuggestedFix(mark);
    expect(result.ok).toBe(true);
    expect(seen).toBeUndefined();  // the old "alpha beta" display never described the new text
    expect(get(verseDisplay)[key]).toEqual(display("alpha gamma"));
  });

  it("keeps rendering raw text when an older engine sends no display", async () => {
    editVerse.mockResolvedValue({ issueResolutionsNeedingRecheck: 0 });
    const result = await applySuggestedFindingFix(finding());
    expect(result.ok).toBe(true);
    expect(get(verseDisplay)[key]).toBeUndefined();
  });
});

describe("saveVerseEdit and the change history", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    cancelVerseEdit();
    checkingProgress.set({ running: false, percent: 0, label: "", jobId: "", state: "idle", error: "", scope: "chapter" });
    verseTexts.set({ [verseKey("1", "6")]: "alpha beta" });
    historyCountByVerse.set({ [verseKey("1", "6")]: 1 });
    runVerseChecks.mockResolvedValue([]);
  });

  it("counts a saved edit and keeps the engine's marker warnings for that verse", async () => {
    editVerse.mockResolvedValue({ issueResolutionsNeedingRecheck: 0, warnings: ["unclosed \\nd"] });
    startVerseEdit("1", "6");
    editText.set("alpha \\nd beta");
    expect(await saveVerseEdit()).toBe(true);
    expect(get(historyCountByVerse)[verseKey("1", "6")]).toBe(2);
    expect(get(editWarnings)).toEqual({ key: verseKey("1", "6"), warnings: ["unclosed \\nd"] });
    startVerseEdit("1", "6");
    expect(get(editWarnings)).toBeNull();
  });

  it("does not count a refused save", async () => {
    editVerse.mockRejectedValue(new Error("refused"));
    startVerseEdit("1", "6");
    editText.set("gamma");
    expect(await saveVerseEdit()).toBe(false);
    expect(get(historyCountByVerse)[verseKey("1", "6")]).toBe(1);
  });
});
