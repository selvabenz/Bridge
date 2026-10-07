import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { get } from "svelte/store";

const { verseHistory, editVerse, runVerseChecks } = vi.hoisted(() => ({
  verseHistory: vi.fn(),
  editVerse: vi.fn(),
  runVerseChecks: vi.fn(),
}));
vi.mock("../../api/bridgeClient", () => ({ bridge: { verseHistory, editVerse, runVerseChecks } }));

import VerseHistoryPopover from "../VerseHistoryPopover.svelte";
import { checkingProgress, historyCountByVerse, verseKey, verseTexts } from "../../stores";
import { cancelVerseEdit } from "../../verseEditor";
import type { VerseHistoryEntry } from "../../types/finding";

function entry(overrides: Partial<VerseHistoryEntry> = {}): VerseHistoryEntry {
  return {
    chapter: "1", verse: "2", timestamp: "2026-10-07T10:00:00.000Z", username: "Benz",
    verseBefore: "अन्त \\nd प्रभु\\nd*", verseAfter: "अंत \\nd प्रभु\\nd*",
    plainBefore: "अन्त प्रभु", plainAfter: "अंत प्रभु",
    tags: ["meaning"], groupId: "human-scripture-edit", batchId: null, undoes: null,
    ...overrides,
  };
}

describe("VerseHistoryPopover", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    cancelVerseEdit();
    checkingProgress.set({ running: false, percent: 0, label: "", jobId: "", state: "idle", error: "", scope: "chapter" });
    verseTexts.set({ [verseKey("1", "2")]: "अंत \\nd प्रभु\\nd*" });
    historyCountByVerse.set({ [verseKey("1", "2")]: 1 });
  });

  it("lists each change with what was removed and inserted, and marks a change set", async () => {
    verseHistory.mockResolvedValue({ total: 2, truncated: false, entries: [
      entry({ batchId: "b1", tags: ["languageQa", "scope"] }), entry({ timestamp: "2026-10-06T10:00:00.000Z" })] });
    render(VerseHistoryPopover, { props: { chapter: "1", verse: "2", reference: "GEN 1:2", x: 10, y: 10, onClose: vi.fn() } });
    const dialog = await screen.findByRole("dialog", { name: "Change history of GEN 1:2" });
    await waitFor(() => expect(dialog).toHaveTextContent("2 changes"));
    expect(verseHistory).toHaveBeenCalledWith({ chapter: "1", verse: "2" });
    expect(dialog.querySelector("del")?.textContent).toContain("न्");
    expect(dialog.querySelector("ins")?.textContent).toContain("ं");
    expect(dialog).toHaveTextContent("change set");
  });

  it("restores an earlier wording only on a second click, through the ordinary edit", async () => {
    verseHistory.mockResolvedValue({ total: 1, truncated: false, entries: [entry()] });
    editVerse.mockResolvedValue({ committed: true, issueResolutionsNeedingRecheck: 0 });
    runVerseChecks.mockResolvedValue([]);
    const onClose = vi.fn();
    render(VerseHistoryPopover, { props: { chapter: "1", verse: "2", reference: "GEN 1:2", x: 10, y: 10, onClose } });
    await fireEvent.click(await screen.findByRole("button", { name: "Restore the text before this change" }));
    expect(editVerse).not.toHaveBeenCalled();
    await fireEvent.click(screen.getByRole("button", { name: /Restore it: the verse becomes/ }));
    await waitFor(() => expect(editVerse).toHaveBeenCalledWith("1", "2", "अन्त \\nd प्रभु\\nd*"));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(get(verseTexts)[verseKey("1", "2")]).toBe("अन्त \\nd प्रभु\\nd*");
    expect(get(historyCountByVerse)[verseKey("1", "2")]).toBe(2);
  });
});
