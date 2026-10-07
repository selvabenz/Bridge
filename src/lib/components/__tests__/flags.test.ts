import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { get } from "svelte/store";

const { languageQaFlagAdd, languageQaFlagUpdate, languageQaFlagDelete, languageQaFlagsList } = vi.hoisted(() => ({
  languageQaFlagAdd: vi.fn(),
  languageQaFlagUpdate: vi.fn(),
  languageQaFlagDelete: vi.fn(),
  languageQaFlagsList: vi.fn(),
}));
vi.mock("../../api/bridgeClient", () => ({
  bridge: { languageQaFlagAdd, languageQaFlagUpdate, languageQaFlagDelete, languageQaFlagsList,
    decideVerse: vi.fn(), editVerse: vi.fn(), runVerseChecks: vi.fn(), languageQaHistory: vi.fn() },
}));

import FlagDialog from "../FlagDialog.svelte";
import VerseList from "../VerseList.svelte";
import { addFlag, deleteFlag, setFlagStatus, startFlagLoader } from "../../flags";
import { chapterVerseNums, currentChapter, flagsByVerse, project, verseDisplay, verseKey, verseTexts } from "../../stores";
import { plainOffset, rawOffsetFromPlain } from "../../utils/verseDisplay";
import { DISPLAY_FIXTURES } from "./verseDisplayFixtures";
import type { LanguageQaFlag } from "../../types/languageQa";

function flag(overrides: Partial<LanguageQaFlag> = {}): LanguageQaFlag {
  return { flagId: "f1", chapter: "1", verse: "1", verseEnd: null, start: 0, end: 4, text: "அவன்", textHash: "h",
    type: "style", note: "check this", suggested: null, findingId: null, reviewer: "Benz", status: "open",
    createdAt: "2026-10-07T10:00:00Z", updatedAt: "2026-10-07T10:00:00Z", ...overrides };
}

describe("rawOffsetFromPlain", () => {
  it("inverts plainOffset at every raw offset outside a removed range, over the engine's own fixtures", () => {
    for (const [raw, display] of Object.entries(DISPLAY_FIXTURES)) {
      const length = Array.from(raw).length;
      for (let r = 0; r <= length; r += 1) {
        if (display.removed.some(([from, to]) => from < r && r < to)) continue;
        const p = plainOffset(display, r);
        expect([rawOffsetFromPlain(display, p, "start"), rawOffsetFromPlain(display, p, "end")], `${raw} @ ${r}`)
          .toContain(r);
      }
    }
  });

  it("puts a span's start after a footnote and its end before it", () => {
    const display = { plain: "a b", notes: [], removed: [[2, 16]] as [number, number][], styles: [], warnings: [] };
    expect(rawOffsetFromPlain(display, 2, "start")).toBe(16);
    expect(rawOffsetFromPlain(display, 2, "end")).toBe(2);
  });
});

describe("FlagDialog", () => {
  const draft = { chapter: "1", verse: "2", start: 0, end: 4, text: "அவன்" };

  it("needs a type before it saves, and offers only the chapter's later verses", async () => {
    const onSave = vi.fn().mockResolvedValue("");
    render(FlagDialog, { props: { draft, verseNums: ["1", "2", "3", "4"], reference: "RUT 1:2", onSave, onCancel: vi.fn() } });
    expect(screen.getByRole("button", { name: "Save flag" })).toBeDisabled();
    const through = screen.getByRole("combobox");
    expect([...(through as HTMLSelectElement).options].map((o) => o.value)).toEqual(["", "3", "4"]);
    await fireEvent.click(screen.getByRole("radio", { name: "Meaning" }));
    await fireEvent.input(screen.getByLabelText("Note"), { target: { value: "Is this the sense?" } });
    await fireEvent.change(through, { target: { value: "4" } });
    await fireEvent.click(screen.getByRole("button", { name: "Save flag" }));
    expect(onSave).toHaveBeenCalledWith({ chapter: "1", verse: "2", start: 0, end: 4, text: "அவன்", type: "meaning",
      note: "Is this the sense?", verseEnd: "4" });
  });

  it("keeps the dialog open with the engine's refusal", async () => {
    const onSave = vi.fn().mockResolvedValue("flag.text is not the verse text at that span");
    render(FlagDialog, { props: { draft: { ...draft, type: "spelling" }, onSave, onCancel: vi.fn() } });
    await fireEvent.click(screen.getByRole("button", { name: "Save flag" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/not the verse text/);
  });
});

describe("flags in the verse text", () => {
  const raw = "அவன் சொன்னான்\\f + \\ft குறிப்பு\\f* அந்த காகம் பறந்தது.";

  beforeEach(() => {
    vi.clearAllMocks();
    project.set({ path: "/p", bookId: "rut" } as never);
    currentChapter.set("1");
    chapterVerseNums.set({ "1": ["1"] });
    verseTexts.set({ [verseKey("1", "1")]: raw });
    verseDisplay.set({ [verseKey("1", "1")]: DISPLAY_FIXTURES[raw] });
  });

  afterEach(() => flagsByVerse.set({}));

  it("draws ⚑ after the flagged text, past a footnote before it", async () => {
    // "அந்த" sits after the lifted footnote: raw 34-38, plain 14-18.
    flagsByVerse.set({ [verseKey("1", "1")]: [flag({ start: 34, end: 38, text: "அந்த" })] });
    render(VerseList, { props: { onSelect: vi.fn() } });
    const glyph = screen.getByRole("button", { name: /Flag on “அந்த”, open/ });
    const before = (glyph.parentElement?.textContent ?? "").split("⚑")[0];
    expect(before.endsWith("அந்த")).toBe(true);
  });

  it("opens the flag from its ⚑ and resolves it at once", async () => {
    languageQaFlagUpdate.mockResolvedValue({ flag: flag({ status: "resolved" }) });
    flagsByVerse.set({ [verseKey("1", "1")]: [flag()] });
    render(VerseList, { props: { onSelect: vi.fn() } });
    await fireEvent.click(screen.getByRole("button", { name: /Flag on “அவன்”/ }));
    expect(screen.getByRole("dialog", { name: "Flag" })).toHaveTextContent("check this");
    await fireEvent.click(screen.getByRole("button", { name: "Resolve" }));
    expect(get(flagsByVerse)[verseKey("1", "1")][0].status).toBe("resolved");
    await waitFor(() => expect(languageQaFlagUpdate).toHaveBeenCalledWith("/p", "f1", { status: "resolved" }));
  });

  it("flags the whole verse from the verse menu", async () => {
    languageQaFlagAdd.mockResolvedValue({ flag: flag({ start: 0, end: Array.from(raw).length, text: raw }) });
    render(VerseList, { props: { onSelect: vi.fn() } });
    await fireEvent.contextMenu(document.querySelector('[data-verse-key="1:1"]') as HTMLElement);
    expect(screen.getByRole("menuitem", { name: "Flag selected text…" })).toBeDisabled();
    await fireEvent.click(screen.getByRole("menuitem", { name: "Flag this verse…" }));
    await fireEvent.click(screen.getByRole("radio", { name: "Other" }));
    await fireEvent.click(screen.getByRole("button", { name: "Save flag" }));
    await waitFor(() => expect(languageQaFlagAdd).toHaveBeenCalledWith("/p", expect.objectContaining({
      chapter: "1", verse: "1", start: 0, end: Array.from(raw).length, text: raw, type: "other" })));
  });
});

describe("flags.ts", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    flagsByVerse.set({});
  });

  it("adds a saved flag to its verse, and leaves the store alone on a refusal", async () => {
    languageQaFlagAdd.mockResolvedValueOnce({ flag: flag() }).mockRejectedValueOnce(new Error("no"));
    expect((await addFlag("/p", { chapter: "1", verse: "1", start: 0, end: 4, text: "அவன்", type: "style", note: "" })).error).toBe("");
    expect(get(flagsByVerse)["1:1"].map((f) => f.flagId)).toEqual(["f1"]);
    expect((await addFlag("/p", { chapter: "1", verse: "1", start: 0, end: 4, text: "x", type: "style", note: "" })).error).toBe("no");
    expect(get(flagsByVerse)["1:1"].length).toBe(1);
  });

  it("puts a flag back when resolving or deleting it fails", async () => {
    flagsByVerse.set({ "1:1": [flag()] });
    languageQaFlagUpdate.mockRejectedValue(new Error("disk full"));
    expect(await setFlagStatus("/p", flag(), "resolved")).toBe("disk full");
    expect(get(flagsByVerse)["1:1"][0].status).toBe("open");
    languageQaFlagDelete.mockRejectedValue(new Error("locked"));
    expect(await deleteFlag("/p", flag())).toBe("locked");
    expect(get(flagsByVerse)["1:1"].map((f) => f.flagId)).toEqual(["f1"]);
  });

  it("loads each chapter's flags as it is shown, once", async () => {
    languageQaFlagsList.mockImplementation(async (_path: string, chapter: string) => ({
      flags: [flag({ flagId: `c${chapter}`, chapter })] }));
    currentChapter.set("1");
    const stop = startFlagLoader("/p");
    await waitFor(() => expect(get(flagsByVerse)["1:1"]?.[0].flagId).toBe("c1"));
    currentChapter.set("2");
    await waitFor(() => expect(get(flagsByVerse)["2:1"]?.[0].flagId).toBe("c2"));
    currentChapter.set("1");
    expect(languageQaFlagsList).toHaveBeenCalledTimes(2);
    expect(get(flagsByVerse)["1:1"][0].flagId).toBe("c1");
    stop();
  });
});
