import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { get } from "svelte/store";

const { languageQaScopeFind, languageQaScopeAccept, languageQaBatchUndo } = vi.hoisted(() => ({
  languageQaScopeFind: vi.fn(),
  languageQaScopeAccept: vi.fn(),
  languageQaBatchUndo: vi.fn(),
}));
vi.mock("../../api/bridgeClient", () => ({
  bridge: { languageQaScopeFind, languageQaScopeAccept, languageQaBatchUndo },
}));

import ScopeConfirmDialog from "../ScopeConfirmDialog.svelte";
import ScopeNotice from "../ScopeNotice.svelte";
import { lastBatch, openScopeDialog, resetScopedApply, scopeDialog, scopeNotice } from "../../scopedApply";
import { languageQaFindingsByVerse, project, verseKey, verseTexts } from "../../stores";
import { lqaFinding } from "./languageQaFixture";

const OLD = "सताईस", NEW = "सत्ताईस";
const V1 = "उसकी आयु सताईस वर्ष की थी।", V2 = "वे सताईस दिन रहे।";
const finding = lqaFinding({ id: "f1", book: "gen", chapter: "1", verse: "1", originalText: OLD, start: 9, end: 14,
  suggestions: [{ text: NEW, rank: 1, source: "rule", rationale: "" }] });
const found = {
  chapter: "1", verse: "1", findingId: "f1", scope: "chapter" as const, count: 2, verses: 2,
  key: { ruleId: "hi-irv/hi.lex.known-misspelling", detailRule: "", originalText: OLD, kind: "word" as const },
  occurrences: [
    { chapter: "1", verse: "1", findingId: "f1", start: 9, end: 14, old: OLD, new: NEW },
    { chapter: "1", verse: "2", findingId: "f2", start: 3, end: 8, old: OLD, new: NEW },
  ],
};

describe("scoped Language QA corrections", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetScopedApply();
    project.set({ path: "/p", bookId: "gen" } as never);
    verseTexts.set({ [verseKey("1", "1")]: V1, [verseKey("1", "2")]: V2 });
    languageQaFindingsByVerse.set({ [verseKey("1", "1")]: [finding] });
    languageQaScopeFind.mockResolvedValue(found);
  });

  afterEach(() => resetScopedApply());

  it("lists every place with its before and after, and Cancel writes nothing", async () => {
    await openScopeDialog(finding, NEW, "chapter");
    render(ScopeConfirmDialog);
    expect(languageQaScopeFind).toHaveBeenCalledWith("/p", "1", "1", "f1", "chapter", NEW);
    expect(screen.getByRole("dialog", { name: /Change “सताईस” to “सत्ताईस” in this chapter/ })).toBeTruthy();
    expect(screen.getByText("GEN 1:1")).toBeTruthy();
    expect(screen.getByText("GEN 1:2")).toBeTruthy();
    expect(document.querySelectorAll("del").length).toBe(2);
    expect(screen.getByRole("button", { name: "Change 2 verses" })).toBeTruthy();
    await waitFor(() => expect(document.activeElement?.textContent).toBe("Cancel"));
    await fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(get(scopeDialog)).toBeNull();
    expect(languageQaScopeAccept).not.toHaveBeenCalled();
  });

  it("Escape cancels too", async () => {
    await openScopeDialog(finding, NEW, "book");
    render(ScopeConfirmDialog);
    await fireEvent.keyDown(window, { key: "Escape" });
    expect(get(scopeDialog)).toBeNull();
  });

  it("confirming writes once, shows the new text at once and offers Undo all, which restores it", async () => {
    languageQaScopeAccept.mockResolvedValue({
      batchId: "b1", action: "accept", scope: "chapter", count: 2, skipped: [],
      changed: [{ chapter: "1", verse: "1", newText: V1.replace(OLD, NEW) }, { chapter: "1", verse: "2", newText: V2.replace(OLD, NEW) }],
    });
    languageQaBatchUndo.mockResolvedValue({
      batchId: "b1", undoBatchId: "u1", conflicts: [],
      reverted: [{ chapter: "1", verse: "1", newText: V1 }, { chapter: "1", verse: "2", newText: V2 }],
    });
    await openScopeDialog(finding, NEW, "chapter");
    render(ScopeConfirmDialog);
    render(ScopeNotice);
    await fireEvent.click(screen.getByRole("button", { name: "Change 2 verses" }));
    await waitFor(() => expect(languageQaScopeAccept).toHaveBeenCalledTimes(1));
    expect(languageQaScopeAccept).toHaveBeenCalledWith("/p", "1", "1", "f1", "chapter", NEW);
    expect(get(verseTexts)[verseKey("1", "2")]).toBe(V2.replace(OLD, NEW));
    expect(get(languageQaFindingsByVerse)[verseKey("1", "1")]).toBeUndefined();
    expect(get(scopeDialog)).toBeNull();
    expect(get(lastBatch)?.batchId).toBe("b1");
    expect(await screen.findByText(/Changed “सताईस” → “सत्ताईस” in 2 verses/)).toBeTruthy();

    await fireEvent.click(screen.getByRole("button", { name: "Undo all" }));
    await waitFor(() => expect(languageQaBatchUndo).toHaveBeenCalledWith("/p", "b1"));
    expect(get(verseTexts)[verseKey("1", "2")]).toBe(V2);
    expect(get(lastBatch)).toBeNull();
    expect(get(scopeNotice)?.text).toMatch(/2 verses restored/);
  });

  it("says which verses were left because they changed after the check", async () => {
    languageQaScopeAccept.mockResolvedValue({
      batchId: "b2", action: "accept", scope: "chapter", count: 1,
      changed: [{ chapter: "1", verse: "1", newText: V1.replace(OLD, NEW) }],
      skipped: [{ chapter: "1", verse: "2", reason: "changed" }],
    });
    await openScopeDialog(finding, NEW, "chapter");
    render(ScopeConfirmDialog);
    await fireEvent.click(screen.getByRole("button", { name: "Change 2 verses" }));
    await waitFor(() => expect(get(scopeNotice)?.text).toMatch(/1 verse was changed after the check and left as it is/));
  });

  it("keeps the dialog open with the error when the engine refuses", async () => {
    languageQaScopeAccept.mockRejectedValue(new Error("That finding is not in the last Language QA pass."));
    await openScopeDialog(finding, NEW, "chapter");
    render(ScopeConfirmDialog);
    await fireEvent.click(screen.getByRole("button", { name: "Change 2 verses" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/not in the last Language QA pass/);
    expect(get(scopeDialog)).not.toBeNull();
    expect(get(lastBatch)).toBeNull();
  });
});
