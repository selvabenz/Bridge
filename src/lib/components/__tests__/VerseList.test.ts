import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { get } from "svelte/store";

const { decideVerse, editVerse, runVerseChecks, languageQaHistory, housestyleRecord, housestyleSetState, languageQaLearnedForget, languageQaScopeFind, verseHistory, languageQaWordsAdd, languageQaOccurrences } = vi.hoisted(() => ({
  languageQaWordsAdd: vi.fn(),
  languageQaOccurrences: vi.fn(),
  verseHistory: vi.fn(),
  languageQaLearnedForget: vi.fn(),
  languageQaScopeFind: vi.fn(),
  housestyleRecord: vi.fn(),
  housestyleSetState: vi.fn(),
  decideVerse: vi.fn(),
  editVerse: vi.fn(),
  runVerseChecks: vi.fn(),
  languageQaHistory: vi.fn(),
}));

vi.mock("../../api/bridgeClient", () => ({
  bridge: { decideVerse, editVerse, runVerseChecks, languageQaHistory, housestyleRecord, housestyleSetState, languageQaLearnedForget, languageQaScopeFind, verseHistory, languageQaWordsAdd, languageQaOccurrences },
}));
import { lqaFinding } from "./languageQaFixture";
import { scopeDialog } from "../../scopedApply";
import { displayFor } from "./verseDisplayFixtures";
import type { LanguageQaSuggestion } from "../../types/languageQa";

import VerseList from "../VerseList.svelte";
import {
  chapterVerseNums,
  currentChapter,
  verseTexts,
  verseDisplay,
  findingsByVerse,
  checkStatusByVerse,
  checkingProgress,
  alignmentStatusByVerse,
  nativeChecksByVerse,
  aiCheckReviewsByVerse,
  headingsByVerse,
  selectedVerse,
  verseKey,
  languageQaFindingsByVerse,
  project,
  historyCountByVerse,
  activeLanguageQaFindingId,
} from "../../stores";
import { rawView } from "../../editorPrefs";
import { alignmentOpen, alignmentKey } from "../../alignmentUi";
import { cancelVerseEdit, editingChapter, editingVerse, editSaving, recheckingKey } from "../../verseEditor";
import { aiReviewRequest, aiJobActive } from "../../aiReviewUi";
import type { QaFinding } from "../../types/finding";

/** The store holds full QaFindings; fixtures.ts only builds summaries/details. */
function finding(overrides: Partial<QaFinding> = {}): QaFinding {
  return {
    id: "f1",
    project_id: "p1",
    book: "php",
    chapter: 1,
    verse: 6,
    start_offset: null,
    end_offset: null,
    original_text: "",
    engine: "greek_room",
    check_type: "spelling",
    category: "spelling",
    severity: "low",
    confidence: 0.5,
    suggested_replacement: null,
    explanation: "Possible spelling issue",
    evidence: [],
    engine_version: "1",
    resource_versions: {},
    status: "open",
    human_comment: null,
    created_at: "2026-09-06T00:00:00Z",
    resolved_at: null,
    ...overrides,
  };
}

const PHP_1_6 =
  "\\it मुझे इस बात का भरोसा है\\it*\\f + \\fr 1.6 \\fq मुझे इस बात का भरोसा है: " +
  "\\ft इसका मतलब यह है पौलुस ने जो कुछ कहा उसका सच पूरी तरह से आश्वस्त था।\\f* " +
  "कि जिसने तुम में अच्छा काम आरम्भ किया है।";

function seed(text: string, findings: QaFinding[] = []): void {
  currentChapter.set("1");
  chapterVerseNums.set({ "1": ["6"] });
  verseTexts.set({ [verseKey("1", "6")]: text });
  // What the engine sends alongside the raw text (#91 Phase 2b): the display
  // fixtures are the engine's own output for these strings.
  verseDisplay.set({ [verseKey("1", "6")]: displayFor(text) });
  headingsByVerse.set({});
  findingsByVerse.set({ [verseKey("1", "6")]: findings });
  checkStatusByVerse.set({});
  alignmentStatusByVerse.set({});
  nativeChecksByVerse.set({});
  aiCheckReviewsByVerse.set({});
  selectedVerse.set(null);
  checkingProgress.set({
    running: false, percent: 0, label: "", jobId: "", state: "idle", error: "", scope: "chapter",
  });
  alignmentOpen.set(false);
  alignmentKey.set("");
  editingChapter.set("");
  editingVerse.set("");
  editSaving.set(false);
  recheckingKey.set("");
  aiReviewRequest.set(null);
  aiJobActive.set(false);
  vi.clearAllMocks();
  decideVerse.mockResolvedValue(undefined);
  editVerse.mockResolvedValue({ issueResolutionsNeedingRecheck: 0 });
  runVerseChecks.mockResolvedValue([]);
}

/** The one verse row seed() renders, addressed the way the component keys it. */
function verseRow(): HTMLElement {
  return document.querySelector('[data-verse-key="1:6"]') as HTMLElement;
}

const FOOTNOTE_MARKER = /Show footnote at this point in verse 1:6/;
const XREF_MARKER = /Show cross reference at this point in verse 1:6/;

describe("VerseList footnote handling", () => {
  beforeEach(() => seed(PHP_1_6));

  it("keeps footnote markup out of the verse area", () => {
    render(VerseList, { props: { onSelect: vi.fn() } });
    expect(document.body.textContent).not.toContain("\\f");
    expect(document.body.textContent).not.toContain("\\fr");
  });

  it("does not show styling markers, only their text", () => {
    // Flipped deliberately in #91 Phase 2b: the engine lifts character styles
    // out of the display and reports them as spans, so `\it` is never shown.
    render(VerseList, { props: { onSelect: vi.fn() } });
    expect(document.body.textContent).not.toContain("\\it");
    expect(document.body.textContent).toContain("मुझे इस बात का भरोसा है");
  });

  it("does not show the footnote text inline with the verse", () => {
    render(VerseList, { props: { onSelect: vi.fn() } });
    expect(screen.queryByText(/इसका मतलब यह है/)).toBeNull();
  });

  it("still shows the Scripture itself", () => {
    render(VerseList, { props: { onSelect: vi.fn() } });
    expect(document.body.textContent).toContain("कि जिसने तुम में अच्छा काम आरम्भ किया है।");
  });

  it("offers an f marker for a verse that has a footnote", () => {
    render(VerseList, { props: { onSelect: vi.fn() } });
    expect(screen.getByLabelText(FOOTNOTE_MARKER)).toHaveTextContent("f");
  });

  it("places the marker where the footnote was, not at the end of the verse", () => {
    render(VerseList, { props: { onSelect: vi.fn() } });
    const vtext = document.querySelector(".vtext .vbody") as HTMLElement;
    const nodes = Array.from(vtext.childNodes);
    const markerIndex = nodes.findIndex(
      (node) => node instanceof HTMLElement && node.classList.contains("note-btn"),
    );
    expect(markerIndex).toBeGreaterThan(-1);
    // Scripture still follows the marker, so it is not trailing the verse.
    const after = nodes.slice(markerIndex + 1).map((n) => n.textContent ?? "").join("");
    expect(after).toContain("कि जिसने");
  });

  it("shows no marker for a verse with no notes", () => {
    seed("मसीह यीशु के दास पौलुस");
    render(VerseList, { props: { onSelect: vi.fn() } });
    expect(screen.queryByLabelText(/Show footnote/)).toBeNull();
    expect(screen.queryByLabelText(/Show cross reference/)).toBeNull();
  });

  it("opens a titled popup carrying the footnote text", async () => {
    render(VerseList, { props: { onSelect: vi.fn() } });
    await fireEvent.click(screen.getByLabelText(FOOTNOTE_MARKER));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText("Footnotes")).toBeInTheDocument();
    expect(screen.getByText(/इसका मतलब यह है/)).toBeInTheDocument();
  });

  it("closes the popup again", async () => {
    render(VerseList, { props: { onSelect: vi.fn() } });
    await fireEvent.click(screen.getByLabelText(FOOTNOTE_MARKER));
    await fireEvent.click(screen.getByLabelText("Close footnotes"));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("does not select the verse when the marker is clicked", async () => {
    const onSelect = vi.fn();
    render(VerseList, { props: { onSelect } });
    await fireEvent.click(screen.getByLabelText(FOOTNOTE_MARKER));
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("uses the cross reference title for an x marker", async () => {
    seed("पवित्र लोगों\\x + \\xo 1.1 \\xt रोम. 1:7\\x* के नाम");
    render(VerseList, { props: { onSelect: vi.fn() } });
    await fireEvent.click(screen.getByLabelText(XREF_MARKER));
    expect(screen.getByText("Cross reference")).toBeInTheDocument();
    expect(screen.getByText("रोम. 1:7")).toBeInTheDocument();
  });

  it("renders one marker per note on a verse with several", () => {
    seed("a\\f + \\ft one\\f* b\\f + \\ft two\\f*");
    render(VerseList, { props: { onSelect: vi.fn() } });
    expect(screen.getAllByLabelText(/Show footnote at this point/)).toHaveLength(2);
  });

  it("opens only the note whose marker was clicked", async () => {
    seed("a\\f + \\ft first note\\f* b\\f + \\ft second note\\f*");
    render(VerseList, { props: { onSelect: vi.fn() } });
    await fireEvent.click(screen.getAllByLabelText(/Show footnote at this point/)[1]);
    expect(screen.getByText("second note")).toBeInTheDocument();
    expect(screen.queryByText("first note")).toBeNull();
  });

  it("keeps a finding underline on its word after the note is lifted out", () => {
    // "आरम्भ" sits after the footnote, so its raw offset is far beyond where
    // the word lands once the note is removed. Without offset remapping the
    // mark would be dropped entirely.
    const word = "आरम्भ";
    const start = PHP_1_6.indexOf(word);
    seed(PHP_1_6, [finding({ start_offset: start, end_offset: start + word.length })]);
    render(VerseList, { props: { onSelect: vi.fn() } });
    const marks = Array.from(document.querySelectorAll("mark")).map((m) => m.textContent);
    expect(marks).toContain(word);
  });

  it("applies a Language QA fix after a footnote at the raw offset, not the display one", async () => {
    // Regression: the menu used to receive the display-shifted copy of the
    // finding, so the fix spliced the raw verse at the wrong place and its
    // own staleness guard refused it. Use must equal a manual raw splice.
    const raw = "அவன் சொன்னான்\\f + \\ft குறிப்பு\\f* அந்த காகம் பறந்தது.";
    const flagged = "அந்த காகம்";
    const points = Array.from(raw);
    const start = Array.from(raw.slice(0, raw.indexOf(flagged))).length;
    const end = start + Array.from(flagged).length;
    seed(raw);
    languageQaFindingsByVerse.set({ "1:6": [lqaFinding({ start, end, originalText: flagged })] });
    try {
      render(VerseList, { props: { onSelect: vi.fn() } });
      const mark = document.querySelector("mark.m-lqa-sandhi") as HTMLElement;
      expect(mark.textContent).toBe(flagged);
      await fireEvent.contextMenu(mark);
      await fireEvent.click(screen.getByRole("menuitem", { name: 'Use "அந்தக் காகம்"' }));
      const manual = points.slice(0, start).join("") + "அந்தக் காகம்" + points.slice(end).join("");
      expect(editVerse).toHaveBeenCalledWith("1", "6", manual);
      expect(manual).toContain("\\f + \\ft குறிப்பு\\f* அந்தக் காகம் பறந்தது.");
    } finally {
      languageQaFindingsByVerse.set({});
    }
  });

  it("ignores a Language QA mark with an issue that keeps it out of review progress", async () => {
    seed("அந்த காகம் பறந்தது.");
    languageQaFindingsByVerse.set({ "1:6": [lqaFinding({ id: "lqa-2" })] });
    try {
      render(VerseList, { props: { onSelect: vi.fn() } });
      await fireEvent.contextMenu(document.querySelector("mark.m-lqa-sandhi") as HTMLElement);
      await fireEvent.click(screen.getByRole("menuitem", { name: "Ignore this occurrence" }));
      expect(decideVerse).toHaveBeenCalledWith("1", "6", "lqa-2", "ignored", undefined, {
        source: "languageQa", rule: "tamil.vallinam-missing", ruleId: "ta-irv/tamil.vallinam-missing",
        ruleVersion: "language-qa-7", packVersion: "language-qa-7", ruleRevision: 1,
        layer: "pattern", category: "sandhi",
        originalText: "அந்த காகம்", suggestedReplacement: "அந்தக் காகம்",
        chosenSuggestion: null, chosenRank: null,
        message: "Possible missing வல்லினம்.", start: 0, end: 10,
      });
      expect(document.querySelector("mark.m-lqa-sandhi")).toBeNull();
    } finally {
      languageQaFindingsByVerse.set({});
    }
  });

  describe("Language QA menu", () => {
    const five: LanguageQaSuggestion[] = ["அந்தக் காகம்", "அந்தக்காகம்", "அக்காகம்", "அந்த க் காகம்", "அக் காகம்"]
      .map((text, i) => ({ text, rank: i + 1, source: "rule", rationale: `reason ${i + 1}` }));

    async function openMenu(overrides: Parameters<typeof lqaFinding>[0] = {}, text = "அந்த காகம் பறந்தது.") {
      seed(text);
      languageQaFindingsByVerse.set({ "1:6": [lqaFinding(overrides)] });
      render(VerseList, { props: { onSelect: vi.fn() } });
      await fireEvent.contextMenu(document.querySelector("mark.m-lqa-sandhi") as HTMLElement);
      return screen.getAllByRole("menuitem").map((item) => [item.textContent, item.getAttribute("title")]);
    }

    afterEach(() => languageQaFindingsByVerse.set({}));

    it("offers Forget on a learned fix's mark, which drops it at once without recording a decision", async () => {
      seed("அவர் தேவன் என்றார்.");
      languageQaLearnedForget.mockReset().mockResolvedValue({ fix: {} });
      project.set({ path: "/p", bookId: "php" } as never);
      languageQaFindingsByVerse.set({ "1:6": [lqaFinding({
        id: "l1", rule: "learned.replacement", category: "learned", layer: "housestyle", inline: true,
        start: 4, end: 9, originalText: "தேவன்",
        suggestions: [{ text: "கர்த்தர்", rank: 1, source: "learned", rationale: "Replaced with this 1× before." }],
      })] });
      render(VerseList, { props: { onSelect: vi.fn() } });
      await fireEvent.contextMenu(document.querySelector("mark.m-lqa-learned") as HTMLElement);
      await fireEvent.click(screen.getByRole("menuitem", { name: "Forget this learned fix" }));
      expect(document.querySelector("mark.m-lqa-learned")).toBeNull();
      await waitFor(() => expect(languageQaLearnedForget).toHaveBeenCalledWith("/p", "தேவன்", "கர்த்தர்"));
      expect(decideVerse).not.toHaveBeenCalled();
    });

    it("offers no Use item when the finding has no suggestion", async () => {
      expect((await openMenu({ suggestions: [] })).map(([label]) => label)).toEqual(["Ignore this occurrence", "Book", "Project", "Ignore this rule▸", "Mark as false positive",
          "Flag for review…", "Copy word", "Search in this book", "Show IRV occurrences…", "Show OV occurrences…", "Edit…"]);
    });

    it("offers one Use item, with Chapter and Book on its row and the rationale as its tooltip", async () => {
      const items = await openMenu();
      expect(items[0]).toEqual(['Use "அந்தக் காகம்"', "test rationale"]);
      expect(items.map(([label]) => label)).toEqual(['Use "அந்தக் காகம்"', "Chapter", "Book", "Ignore this occurrence", "Book", "Project", "Ignore this rule▸", "Mark as false positive",
          "Flag for review…", "Copy word", "Search in this book", "Show IRV occurrences…", "Show OV occurrences…", "Edit…"]);
      // The word and what was found head the menu, as in the indic-qa editor: text, not an item.
      expect(screen.getByRole("menu").textContent).toContain("“அந்த காகம்”");
    });

    it("shows a suggestion's kind and frequency, learned fixes first, and nine at most", async () => {
      const suggestions: LanguageQaSuggestion[] = [
        ...Array.from({ length: 9 }, (_, i) => ({ text: `form${i + 1}`, rank: i + 1, source: "lexicon" as const,
          rationale: "r", kind: "vowel_length", freq: 100 + i })),
        { text: "earlier", rank: 10, source: "learned", rationale: "Replaced with this 3× before." },
      ];
      await openMenu({ suggestions });
      const uses = screen.getAllByRole("menuitem").filter((item) => /^(Use|Change to) /.test(item.textContent ?? ""));
      expect(uses).toHaveLength(9);
      expect(uses[0].getAttribute("aria-label")).toBe('Change to "earlier", learned fix');
      expect(uses[1].getAttribute("aria-label")).toBe('Use "form1", vowel length · 100');
      expect(uses[1].querySelector(".tag")?.textContent).toBe("vowel length · 100");
    });

    it("offers the same change in the chapter or the book, and asks first", async () => {
      languageQaScopeFind.mockReset().mockResolvedValue({
        chapter: "1", verse: "6", findingId: "lqa-1", scope: "book", count: 1, verses: 1,
        key: { ruleId: "ta-irv/tamil.vallinam-missing", detailRule: "", originalText: "அந்த காகம்", kind: "word" },
        occurrences: [{ chapter: "1", verse: "6", findingId: "lqa-1", start: 0, end: 10, old: "அந்த காகம்", new: "அந்தக் காகம்" }],
      });
      project.set({ path: "/p", bookId: "php" } as never);
      await openMenu();
      expect(screen.getByRole("menuitem", { name: 'Use "அந்தக் காகம்": Chapter' })).toBeInTheDocument();
      await fireEvent.click(screen.getByRole("menuitem", { name: 'Use "அந்தக் காகம்": Book' }));
      await waitFor(() => expect(languageQaScopeFind).toHaveBeenCalledWith("/p", "1", "6", "lqa-1", "book", "அந்தக் காகம்"));
      expect(get(scopeDialog)?.scope).toBe("book");
      expect(editVerse).not.toHaveBeenCalled();
      scopeDialog.set(null);
    });

    it("offers the ranked Use items in order, and Use applies the one chosen and records its rank", async () => {
      const items = await openMenu({ suggestions: five });
      expect(items.filter(([label]) => label?.startsWith("Use "))).toEqual(five.map((s) => [`Use "${s.text}"`, s.rationale]));
      await fireEvent.click(screen.getByRole("menuitem", { name: 'Use "அந்தக்காகம்"' }));
      expect(editVerse).toHaveBeenCalledWith("1", "6", "அந்தக்காகம் பறந்தது.");
      await waitFor(() => expect(decideVerse).toHaveBeenCalledWith("1", "6", "lqa-1", "accepted", undefined,
        expect.objectContaining({ chosenSuggestion: "அந்தக்காகம்", chosenRank: 2, suggestedReplacement: "அந்தக்காகம்" })));
    });

    it("Use changes the verse before the engine answers, and closes the menu at once", async () => {
      let finish: (value: unknown) => void = () => {};
      await openMenu();
      editVerse.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
      await fireEvent.click(screen.getByRole("menuitem", { name: 'Use "அந்தக் காகம்"' }));
      expect(screen.queryByRole("menu")).toBeNull();
      expect(get(verseTexts)["1:6"]).toBe("அந்தக் காகம் பறந்தது.");
      expect(document.querySelector('[data-verse-key="1:6"] .vtext')?.textContent).toContain("அந்தக் காகம்");
      finish({ issueResolutionsNeedingRecheck: 0 });
      await waitFor(() => expect(runVerseChecks).toHaveBeenCalled());
    });

    it("Use puts the old text and mark back if the save fails", async () => {
      await openMenu();
      editVerse.mockRejectedValue(new Error("disk full"));
      await fireEvent.click(screen.getByRole("menuitem", { name: 'Use "அந்தக் காகம்"' }));
      await waitFor(() => expect(screen.getByRole("status").textContent).toContain("disk full"));
      expect(get(verseTexts)["1:6"]).toBe("அந்த காகம் பறந்தது.");
      expect(get(languageQaFindingsByVerse)["1:6"]).toHaveLength(1);
    });

    it("marks a false positive: the mark goes at once, and comes back if recording fails", async () => {
      let fail: (reason: unknown) => void = () => {};
      await openMenu();
      decideVerse.mockReturnValue(new Promise((_, reject) => { fail = reject; }));
      await fireEvent.click(screen.getByRole("menuitem", { name: "Mark as false positive" }));
      expect(document.querySelector("mark.m-lqa-sandhi")).toBeNull();
      expect(decideVerse).toHaveBeenCalledWith("1", "6", "lqa-1", "rejected", undefined,
        expect.objectContaining({ source: "languageQa", ruleId: "ta-irv/tamil.vallinam-missing" }));
      fail(new Error("workbench locked"));
      await waitFor(() => expect(document.querySelector("mark.m-lqa-sandhi")).not.toBeNull());
      expect(screen.getByRole("status").textContent).toContain("workbench locked");
    });

    it("Edit… opens the verse with the flagged text selected, in UTF-16 units past an astral character", async () => {
      // "𝔸" is one code point but two UTF-16 units: the engine's start (3) is
      // a code-point offset, the textarea's selection is not.
      const text = "𝔸 அந்த காகம் பறந்தது.";
      const start = 2;
      const end = start + Array.from("அந்த காகம்").length;
      await openMenu({ start, end }, text);
      await fireEvent.click(screen.getByRole("menuitem", { name: "Edit…" }));
      const area = await waitFor(() => {
        const found = document.querySelector('[data-verse-key="1:6"] textarea') as HTMLTextAreaElement;
        expect(found).not.toBeNull();
        return found;
      });
      await waitFor(() => expect(area.selectionStart).toBe(3));
      expect(area.value.slice(area.selectionStart, area.selectionEnd)).toBe("அந்த காகம்");
    });

    it("meets the click budget: every action changes the screen in under 100 ms with the engine silent", async () => {
      // The engine never answers, so any action that waited on it would never
      // change the screen at all. jsdom does not paint; this times the DOM change.
      const never = () => new Promise(() => {});
      for (const [label, changed] of [
        ["Ignore this occurrence", () => document.querySelector("mark.m-lqa-sandhi") === null],
        ["Mark as false positive", () => document.querySelector("mark.m-lqa-sandhi") === null],
        ['Use "அந்தக் காகம்"', () => Boolean(document.querySelector('[data-verse-key="1:6"] .vtext')
          ?.textContent?.includes("அந்தக் காகம்"))],
      ] as const) {
        // The fastest of three clicks: one wall-clock sample under a parallel
        // run measures the machine (a GC pause, another worker), while an
        // action that waited on the engine would be slow -- in fact never
        // change the screen -- every time. Every click must change the screen.
        let fastest = Infinity;
        for (let attempt = 0; attempt < 3; attempt += 1) {
          // Destroy the previous round's component, not just its DOM: a live
          // one stays subscribed to the stores, so each click would re-render
          // every earlier copy too, and the budget would measure leftovers.
          cleanup();
          await openMenu();
          decideVerse.mockImplementation(never);
          editVerse.mockImplementation(never);
          const started = performance.now();
          await fireEvent.click(screen.getByRole("menuitem", { name: label }));
          expect(changed(), label).toBe(true);
          fastest = Math.min(fastest, performance.now() - started);
          cancelVerseEdit();
          editSaving.set(false);
        }
        expect(fastest, label).toBeLessThan(100);
      }
    });

    it("the verse menu opens this verse's Language QA history", async () => {
      languageQaHistory.mockResolvedValue({ chapter: "1", verse: "6", findingId: null, entries: [] });
      project.set({ path: "C:/project" } as never);
      seed("அந்த காகம் பறந்தது.");
      render(VerseList, { props: { onSelect: vi.fn() } });
      await fireEvent.contextMenu(verseRow());
      await fireEvent.click(screen.getByRole("menuitem", { name: "Language QA history…" }));
      expect(await screen.findByRole("dialog", { name: "Language QA history for verse 1:6" })).toBeTruthy();
      expect(languageQaHistory).toHaveBeenCalledWith("C:/project", "1", "6", undefined);
      await screen.findByText("No decisions recorded yet.");
      project.set(null);
    });
  });

  it("offers the review panel's two actions, then the word actions", async () => {
    seed("alpha beta", [finding({
      start_offset: 0, end_offset: 5, original_text: "alpha", suggested_replacement: null,
    })]);
    render(VerseList, { props: { onSelect: vi.fn() } });
    await fireEvent.contextMenu(document.querySelector("mark") as HTMLElement, {
      clientX: 25, clientY: 35,
    });
    expect(screen.getByRole("menu", { name: /Actions for Possible spelling issue/i }))
      .toBeInTheDocument();
    expect(screen.getAllByRole("menuitem").map((item) => item.textContent))
      .toEqual(["Accept finding", "Ignore", "Flag for review…", "Copy word", "Search in this book"]);
    // Both stay usable whether or not the check proposed a correction — the
    // menu never changes height between findings.
    for (const name of ["Accept finding", "Ignore"]) expect(screen.getByRole("menuitem", { name })).toBeEnabled();
  });

  it("accepts without touching the verse when the check proposed no correction", async () => {
    seed("alpha beta", [finding({
      start_offset: 0, end_offset: 5, original_text: "alpha", suggested_replacement: null,
    })]);
    render(VerseList, { props: { onSelect: vi.fn() } });
    await fireEvent.contextMenu(document.querySelector("mark") as HTMLElement);
    expect(screen.getByRole("menuitem", { name: "Accept finding" }))
      .toHaveAttribute("title", expect.stringContaining("left alone"));
    await fireEvent.click(screen.getByRole("menuitem", { name: "Accept finding" }));
    expect(decideVerse).toHaveBeenCalledWith("1", "6", "f1", "accepted");
    expect(editVerse).not.toHaveBeenCalled();
  });

  it("applies the proposed correction as part of accepting", async () => {
    seed("alpha beta", [finding({
      start_offset: 0, end_offset: 5, original_text: "alpha", suggested_replacement: "omega",
    })]);
    render(VerseList, { props: { onSelect: vi.fn() } });
    await fireEvent.contextMenu(document.querySelector("mark") as HTMLElement);
    expect(screen.getByRole("menuitem", { name: "Accept finding" }))
      .toHaveAttribute("title", expect.stringContaining("proposed correction"));
    await fireEvent.click(screen.getByRole("menuitem", { name: "Accept finding" }));
    expect(editVerse).toHaveBeenCalledWith("1", "6", "omega beta");
    expect(runVerseChecks).toHaveBeenCalledWith("1", "6", ["local", "greekroom"]);
  });

  it("does not accept a correction that could not be applied", async () => {
    // original_text no longer matches the verse, so the fix is stale.
    seed("alpha beta", [finding({
      start_offset: 0, end_offset: 5, original_text: "moved", suggested_replacement: "omega",
    })]);
    render(VerseList, { props: { onSelect: vi.fn() } });
    await fireEvent.contextMenu(document.querySelector("mark") as HTMLElement);
    await fireEvent.click(screen.getByRole("menuitem", { name: "Accept finding" }));
    expect(editVerse).not.toHaveBeenCalled();
    expect(decideVerse).not.toHaveBeenCalled();
    // Menu stays open with the reason, rather than silently filing a decision.
    expect(screen.getByRole("menu")).toBeInTheDocument();
    expect(screen.getByRole("status").textContent).toMatch(/stale/i);
  });

  it("ignores a finding and drops its underline", async () => {
    seed("alpha beta", [finding({ start_offset: 0, end_offset: 5, original_text: "alpha" })]);
    render(VerseList, { props: { onSelect: vi.fn() } });
    await fireEvent.contextMenu(document.querySelector("mark") as HTMLElement);
    await fireEvent.click(screen.getByRole("menuitem", { name: "Ignore" }));
    expect(decideVerse).toHaveBeenCalledWith("1", "6", "f1", "ignored");
    expect(editVerse).not.toHaveBeenCalled();
    expect(document.querySelector("mark")).toBeNull();
  });

  it("opens the same finding menu from the keyboard, with no pointer involved", async () => {
    seed("alpha beta", [finding({
      start_offset: 0, end_offset: 5, original_text: "alpha", suggested_replacement: "omega",
    })]);
    render(VerseList, { props: { onSelect: vi.fn() } });
    const row = verseRow();
    expect(row).toHaveAttribute("aria-keyshortcuts", "Shift+F10");
    await fireEvent.keyDown(row, { key: "F10", shiftKey: true });
    expect(screen.getByRole("menu")).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Accept finding" })).toBeEnabled();
  });

  it("also opens it with the dedicated Menu key", async () => {
    seed("alpha beta", [finding({ start_offset: 0, end_offset: 5, original_text: "alpha" })]);
    render(VerseList, { props: { onSelect: vi.fn() } });
    await fireEvent.keyDown(verseRow(), { key: "ContextMenu" });
    expect(screen.getByRole("menu")).toBeInTheDocument();
  });

  it("walks between a verse's findings with the arrow keys before opening the menu", async () => {
    seed("alpha beta", [
      finding({ id: "f1", start_offset: 0, end_offset: 5, original_text: "alpha",
        explanation: "First finding" }),
      finding({ id: "f2", start_offset: 6, end_offset: 10, original_text: "beta",
        explanation: "Second finding" }),
    ]);
    selectedVerse.set("6");
    render(VerseList, { props: { onSelect: vi.fn() } });
    const row = verseRow();

    // Starts on the first underline in reading order.
    expect(document.querySelector("mark.active-finding")?.textContent).toBe("alpha");
    await fireEvent.keyDown(row, { key: "ArrowRight" });
    expect(document.querySelector("mark.active-finding")?.textContent).toBe("beta");

    await fireEvent.keyDown(row, { key: "F10", shiftKey: true });
    expect(screen.getByRole("menu", { name: /Actions for Second finding/i })).toBeInTheDocument();
  });

  it("opens the general verse menu, not the finding menu, when the shortcut fires with no underlined finding", async () => {
    seed("alpha beta", [finding({ start_offset: null, end_offset: null })]);
    render(VerseList, { props: { onSelect: vi.fn() } });
    const row = verseRow();
    await fireEvent.keyDown(row, { key: "F10", shiftKey: true });
    expect(screen.getByRole("menu", { name: /Actions for verse 6/i })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Accept finding" })).toBeNull();
  });

  it("advertises the popup on the focusable row rather than the plain mark", () => {
    seed("alpha beta", [finding({ start_offset: 0, end_offset: 5, original_text: "alpha" })]);
    render(VerseList, { props: { onSelect: vi.fn() } });
    expect(verseRow()).toHaveAttribute("aria-haspopup", "menu");
    expect(document.querySelector("mark")).not.toHaveAttribute("aria-haspopup");
  });
});

describe("VerseList alignment glyph (issue #70)", () => {
  beforeEach(() => seed(PHP_1_6));

  function glyph(): HTMLElement {
    return screen.getByLabelText(/Open Align Words/) as HTMLElement;
  }

  it("always renders the ⇄ arrow, colored by completion status", () => {
    alignmentStatusByVerse.set({ [verseKey("1", "6")]: "complete" });
    render(VerseList, { props: { onSelect: vi.fn() } });
    expect(glyph()).toHaveTextContent("⇄");
    expect(glyph()).toHaveClass("complete");
  });

  it("colors the arrow for partial, invalid and untouched alignment too", () => {
    alignmentStatusByVerse.set({ [verseKey("1", "6")]: "partial" });
    const { unmount } = render(VerseList, { props: { onSelect: vi.fn() } });
    expect(glyph()).toHaveTextContent("⇄");
    expect(glyph()).toHaveClass("partial");
    unmount();

    alignmentStatusByVerse.set({ [verseKey("1", "6")]: "invalid" });
    const rendered2 = render(VerseList, { props: { onSelect: vi.fn() } });
    expect(glyph()).toHaveTextContent("⇄");
    expect(glyph()).toHaveClass("invalid");
    rendered2.unmount();

    alignmentStatusByVerse.set({});
    render(VerseList, { props: { onSelect: vi.fn() } });
    expect(glyph()).toHaveTextContent("⇄");
    expect(glyph()).toHaveClass("untouched");
  });

  it("selects the verse and opens the Align Words modal when clicked", async () => {
    const onSelect = vi.fn();
    render(VerseList, { props: { onSelect } });
    await fireEvent.click(glyph());
    expect(onSelect).toHaveBeenCalledWith("6");
    expect(get(alignmentOpen)).toBe(true);
    expect(get(alignmentKey)).toBe(verseKey("1", "6"));
  });

  it("does not also trigger the row's own select/edit handlers", async () => {
    const onSelect = vi.fn();
    render(VerseList, { props: { onSelect } });
    await fireEvent.click(glyph());
    // selectFromList runs exactly once (from the glyph handler itself, not
    // once more via the row's on:click) -- stopPropagation is load-bearing.
    expect(onSelect).toHaveBeenCalledTimes(1);
  });

  it("disables the glyph and refuses to open while background checking runs", async () => {
    checkingProgress.set({
      running: true, percent: 40, label: "Checking…", jobId: "j1", state: "running", error: "", scope: "chapter",
    });
    const onSelect = vi.fn();
    render(VerseList, { props: { onSelect } });
    expect(glyph()).toBeDisabled();
    await fireEvent.click(glyph());
    expect(onSelect).not.toHaveBeenCalled();
    expect(get(alignmentOpen)).toBe(false);
  });
});

describe("VerseList edit pencil (issue #73)", () => {
  beforeEach(() => seed(PHP_1_6));

  function pencil(): HTMLElement {
    return screen.getByLabelText("Edit verse 6") as HTMLElement;
  }

  it("renders a pencil above the alignment arrow, in the same action column", () => {
    render(VerseList, { props: { onSelect: vi.fn() } });
    const arrow = screen.getByLabelText(/Open Align Words/);
    const column = pencil().parentElement!;

    expect(pencil()).toHaveTextContent("✎");
    expect(column).toHaveClass("row-actions");
    expect(arrow.parentElement).toBe(column);
    // "Above" is the ask: the pencil precedes the arrow in a column layout.
    expect(Array.from(column.children).indexOf(pencil())).toBeLessThan(
      Array.from(column.children).indexOf(arrow),
    );
  });

  it("selects the verse and opens the editor when clicked", async () => {
    const onSelect = vi.fn();
    render(VerseList, { props: { onSelect } });
    await fireEvent.click(pencil());

    expect(onSelect).toHaveBeenCalledWith("6");
    expect(get(editingChapter)).toBe("1");
    expect(get(editingVerse)).toBe("6");
  });

  it("does not also trigger the row's own select handler", async () => {
    const onSelect = vi.fn();
    render(VerseList, { props: { onSelect } });
    await fireEvent.click(pencil());
    expect(onSelect).toHaveBeenCalledTimes(1);
  });

  it("disables the pencil and refuses to open while background checking runs", async () => {
    checkingProgress.set({
      running: true, percent: 40, label: "Checking…", jobId: "j1",
      state: "running", error: "", scope: "chapter",
    });
    const onSelect = vi.fn();
    render(VerseList, { props: { onSelect } });

    expect(pencil()).toBeDisabled();
    await fireEvent.click(pencil());
    expect(onSelect).not.toHaveBeenCalled();
    expect(get(editingVerse)).toBe("");
  });

  it("refuses to open while an edit save or recheck is in flight", async () => {
    editSaving.set(true);
    const { unmount } = render(VerseList, { props: { onSelect: vi.fn() } });
    expect(pencil()).toBeDisabled();
    unmount();
    editSaving.set(false);

    recheckingKey.set(verseKey("1", "6"));
    render(VerseList, { props: { onSelect: vi.fn() } });
    expect(pencil()).toBeDisabled();
    recheckingKey.set("");
  });

  it("leaves the selection alone when a double-click's edit is refused", async () => {
    // Pre-existing, found while adding the pencil: beginEditFromList selected
    // the verse before asking startVerseEdit whether it could open, so a
    // double-click during background checking moved the reader's selection for
    // an edit that never appeared. Double-click has no disabled attribute to
    // hide it behind, unlike the pencil.
    checkingProgress.set({
      running: true, percent: 40, label: "Checking…", jobId: "j1",
      state: "running", error: "", scope: "chapter",
    });
    const onSelect = vi.fn();
    const { container } = render(VerseList, { props: { onSelect } });
    const row = container.querySelector(`[data-verse-key="${verseKey("1", "6")}"]`)!;

    await fireEvent.dblClick(row);
    expect(get(editingVerse)).toBe("");
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("gives way to the edit form once that verse is being edited", () => {
    editingChapter.set("1");
    editingVerse.set("6");
    render(VerseList, { props: { onSelect: vi.fn() } });

    expect(screen.queryByLabelText("Edit verse 6")).toBeNull();
    expect(screen.queryByLabelText(/Open Align Words/)).toBeNull();
  });
});

describe("VerseList verse context menu (issue #69)", () => {
  beforeEach(() => seed("alpha beta"));

  it("opens on a plain right-click with AI review and Edit verse", async () => {
    const onSelect = vi.fn();
    render(VerseList, { props: { onSelect } });
    await fireEvent.contextMenu(verseRow());
    expect(screen.getByRole("menu", { name: /Actions for verse 6/i })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "AI review" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Edit verse" })).toBeInTheDocument();
    expect(onSelect).toHaveBeenCalledWith("6");
  });

  it("does not also open the finding menu when a finding mark is right-clicked", async () => {
    seed("alpha beta", [finding({
      start_offset: 0, end_offset: 5, original_text: "alpha", suggested_replacement: null,
    })]);
    render(VerseList, { props: { onSelect: vi.fn() } });
    await fireEvent.contextMenu(document.querySelector("mark") as HTMLElement);
    expect(screen.getAllByRole("menu")).toHaveLength(1);
    expect(screen.getByRole("menuitem", { name: "Accept finding" })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "AI review" })).toBeNull();
  });

  it("opens the AI review submenu and requests a scope, then closes", async () => {
    render(VerseList, { props: { onSelect: vi.fn() } });
    await fireEvent.contextMenu(verseRow());
    await fireEvent.click(screen.getByRole("menuitem", { name: "AI review" }));
    await fireEvent.click(screen.getByRole("menuitem", { name: "Chapter" }));
    expect(get(aiReviewRequest)).toEqual({ chapter: "1", verse: "6", scope: "chapter" });
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("starts editing the verse from the menu's Edit verse item", async () => {
    render(VerseList, { props: { onSelect: vi.fn() } });
    await fireEvent.contextMenu(verseRow());
    await fireEvent.click(screen.getByRole("menuitem", { name: "Edit verse" }));
    expect(get(editingChapter)).toBe("1");
    expect(get(editingVerse)).toBe("6");
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("disables every action while background checking runs", async () => {
    checkingProgress.set({
      running: true, percent: 40, label: "Checking…", jobId: "j1", state: "running", error: "", scope: "chapter",
    });
    render(VerseList, { props: { onSelect: vi.fn() } });
    await fireEvent.contextMenu(verseRow());
    expect(screen.getByRole("menuitem", { name: "Edit verse" })).toBeDisabled();
    await fireEvent.click(screen.getByRole("menuitem", { name: "AI review" }));
    expect(screen.getByRole("menuitem", { name: "Verse" })).toBeDisabled();
    expect(screen.getByRole("menuitem", { name: "Chapter" })).toBeDisabled();
    expect(screen.getByRole("menuitem", { name: "Book" })).toBeDisabled();
  });

  it("disables only the verse scope while an AI review job is already active", async () => {
    aiJobActive.set(true);
    render(VerseList, { props: { onSelect: vi.fn() } });
    await fireEvent.contextMenu(verseRow());
    await fireEvent.click(screen.getByRole("menuitem", { name: "AI review" }));
    expect(screen.getByRole("menuitem", { name: "Verse" })).toBeDisabled();
    expect(screen.getByRole("menuitem", { name: "Chapter" })).toBeDisabled();
    expect(screen.getByRole("menuitem", { name: "Book" })).toBeDisabled();
  });

  it("opens the same verse menu via Shift+F10 and closes it on Escape", async () => {
    render(VerseList, { props: { onSelect: vi.fn() } });
    const row = verseRow();
    await fireEvent.keyDown(row, { key: "F10", shiftKey: true });
    const menu = screen.getByRole("menu", { name: /Actions for verse 6/i });
    await fireEvent.keyDown(menu, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
  });
});

describe("VerseList section headings (#180)", () => {
  beforeEach(() => seed("कि जिसने तुम में अच्छा काम आरम्भ किया है।"));

  it("renders a heading above the verse it introduces", () => {
    headingsByVerse.set({ [verseKey("1", "6")]: [{ tag: "s", text: "पहला दिन—उजियाला" }] });
    render(VerseList, { props: { onSelect: vi.fn() } });

    const heading = screen.getByRole("heading", { name: "पहला दिन—उजियाला" });
    expect(heading).toBeInTheDocument();
    // Above, not inside: a heading used to be stored in the previous verse's
    // text, which is what put its words into the alignable target list.
    expect(heading.compareDocumentPosition(verseRow()))
      .toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    expect(verseRow().textContent).not.toContain("पहला दिन—उजियाला");
  });

  it("is not part of the selectable verse row", async () => {
    const onSelect = vi.fn();
    headingsByVerse.set({ [verseKey("1", "6")]: [{ tag: "s", text: "The work in Crete" }] });
    render(VerseList, { props: { onSelect } });

    await fireEvent.click(screen.getByRole("heading", { name: "The work in Crete" }));
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("renders every heading when a verse carries more than one", () => {
    headingsByVerse.set({
      [verseKey("1", "6")]: [
        { tag: "ms", text: "Book Two" },
        { tag: "s", text: "A psalm of David" },
      ],
    });
    render(VerseList, { props: { onSelect: vi.fn() } });
    expect(screen.getByRole("heading", { name: "Book Two" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "A psalm of David" })).toBeInTheDocument();
  });

  it("renders nothing extra for a project imported before headings were split out", () => {
    headingsByVerse.set({});
    render(VerseList, { props: { onSelect: vi.fn() } });
    expect(document.querySelectorAll(".section-heading")).toHaveLength(0);
  });
});

describe("VerseList one review surface (layered-rules 4.3)", () => {
  const text = "அந்த காகம் பறந்தது.";
  const flaggedEnd = Array.from("அந்த காகம்").length;

  afterEach(() => languageQaFindingsByVerse.set({}));

  it("offers every finding on a span carried by two sources, each with its own actions", async () => {
    seed(text, [finding({ id: "gr-1", start_offset: 0, end_offset: flaggedEnd, engine: "wildebeest",
      explanation: "Mixed script" })]);
    languageQaFindingsByVerse.set({ "1:6": [lqaFinding({ id: "lqa-9" })] });
    render(VerseList, { props: { onSelect: vi.fn() } });
    await fireEvent.contextMenu(document.querySelector('[data-finding-ids~="lqa-9"]') as HTMLElement);
    const menu = screen.getByRole("menu", { name: "Findings on this text" });
    // One flat menu: a header line per finding, then its own actions.
    expect(menu.textContent).toContain("wildebeest: Mixed script");
    expect(menu.textContent).toContain("Language QA: Possible missing வல்லினம்.");
    expect(screen.getByRole("menuitem", { name: "Accept finding" })).toBeInTheDocument();
    await fireEvent.click(screen.getByRole("menuitem", { name: "Ignore this occurrence" }));
    expect(decideVerse).toHaveBeenCalledWith("1", "6", "lqa-9", "ignored", undefined,
      expect.objectContaining({ source: "languageQa" }));
  });

  it("reaches a Language QA scope from the mixed menu, which a flyout used to hide", async () => {
    seed(text, [finding({ id: "gr-2", start_offset: 0, end_offset: flaggedEnd, engine: "wildebeest",
      explanation: "Mixed script" })]);
    housestyleRecord.mockResolvedValue({ entries: [], proposals: [], thresholds: {}, entry: {} });
    languageQaFindingsByVerse.set({ "1:6": [lqaFinding({ id: "lqa-m" })] });
    render(VerseList, { props: { onSelect: vi.fn() } });
    await fireEvent.contextMenu(document.querySelector('[data-finding-ids~="lqa-m"]') as HTMLElement);
    await fireEvent.click(screen.getByRole("menuitem", { name: "Ignore this occurrence: Book" }));
    await waitFor(() => expect(housestyleRecord).toHaveBeenCalledWith(expect.objectContaining({
      scope: "word-in-book", word: "அந்த காகம்" })));
  });

  it("walks Language QA marks with the keyboard and opens their menu", async () => {
    seed(text);
    languageQaFindingsByVerse.set({ "1:6": [lqaFinding({ id: "lqa-k" })] });
    render(VerseList, { props: { onSelect: vi.fn() } });
    await fireEvent.keyDown(verseRow(), { key: "F10", shiftKey: true });
    expect(screen.getByRole("menuitem", { name: 'Use "அந்தக் காகம்"' })).toBeInTheDocument();
  });

  it("does not show a verse as clean while a Language QA mark is drawn on it", () => {
    seed(text);
    checkStatusByVerse.set({ "1:6": "succeeded" });
    languageQaFindingsByVerse.set({ "1:6": [lqaFinding()] });
    render(VerseList, { props: { onSelect: vi.fn() } });
    expect(verseRow().classList.contains("approved")).toBe(false);
    expect(verseRow().querySelector(".vnum")?.textContent).not.toContain("✓");
  });
});


describe("VerseList house style (layered-rules 6.3/6.4)", () => {
  afterEach(() => languageQaFindingsByVerse.set({}));

  it("records a scoped Ignore as house style after ignoring the occurrence", async () => {
    seed("அந்த காகம் பறந்தது.");
    housestyleRecord.mockResolvedValue({ entries: [], proposals: [], thresholds: {}, entry: {} });
    languageQaFindingsByVerse.set({ "1:6": [lqaFinding({ id: "lqa-s" })] });
    render(VerseList, { props: { onSelect: vi.fn() } });
    await fireEvent.contextMenu(document.querySelector("mark.m-lqa-sandhi") as HTMLElement);
    await fireEvent.click(screen.getByRole("menuitem", { name: "Ignore this occurrence: Book" }));
    await waitFor(() => expect(housestyleRecord).toHaveBeenCalledWith({
      scope: "word-in-book", ruleId: "ta-irv/tamil.vallinam-missing", word: "அந்த காகம்", provenance: "explicit",
      evidence: [{ chapter: "1", verse: "6", decisionId: "lqa-s" }] }));
    expect(decideVerse).toHaveBeenCalledWith("1", "6", "lqa-s", "ignored", undefined, expect.anything());
  });

  it("shows what the learner learned from an Ignore, with Undo", async () => {
    seed("அந்த காகம் பறந்தது.");
    const learned = { key: "k-learned", word: "அந்த காகம்", ruleId: "ta-irv/tamil.vallinam-missing",
                      evidence: [{}, {}, {}] };
    decideVerse.mockResolvedValue({ houseStyle: { learned } });
    housestyleSetState.mockResolvedValue({ entries: [], proposals: [], thresholds: {}, entry: learned });
    languageQaFindingsByVerse.set({ "1:6": [lqaFinding({ id: "lqa-l" })] });
    render(VerseList, { props: { onSelect: vi.fn() } });
    await fireEvent.contextMenu(document.querySelector("mark.m-lqa-sandhi") as HTMLElement);
    await fireEvent.click(screen.getByRole("menuitem", { name: "Ignore this occurrence" }));
    expect(await screen.findByText(/Learned: “அந்த காகம்” is house style/)).toBeInTheDocument();
    await fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(housestyleSetState).toHaveBeenCalledWith("k-learned", "undone");
    expect(screen.queryByText(/Learned:/)).toBeNull();
  });
});

describe("VerseList change history and Raw view", () => {
  beforeEach(() => {
    seed(PHP_1_6);
    historyCountByVerse.set({});
  });
  afterEach(() => {
    rawView.set(false);
    project.set(null);
  });

  it("shows ↺ with the edit count only on an edited verse, and opens its history", async () => {
    verseHistory.mockResolvedValue({ total: 0, truncated: false, entries: [] });
    render(VerseList, { props: { onSelect: vi.fn() } });
    expect(screen.queryByRole("button", { name: /Change history of verse/ })).toBeNull();
    historyCountByVerse.set({ [verseKey("1", "6")]: 2 });
    const button = await screen.findByRole("button", { name: "Change history of verse 6: 2 edits" });
    expect(button).toHaveTextContent("↺2");
    await fireEvent.click(button);
    expect(await screen.findByRole("dialog", { name: /Change history of/ })).toBeTruthy();
    expect(verseHistory).toHaveBeenCalledWith({ chapter: "1", verse: "6" });
  });

  it("offers Change history in the verse menu", async () => {
    verseHistory.mockResolvedValue({ total: 0, truncated: false, entries: [] });
    project.set({ path: "/p", bookId: "php" } as never);
    render(VerseList, { props: { onSelect: vi.fn() } });
    await fireEvent.contextMenu(document.querySelector('[data-verse-key="1:6"]') as HTMLElement);
    await fireEvent.click(screen.getByRole("menuitem", { name: "Change history…" }));
    expect(await screen.findByRole("dialog", { name: /Change history of/ })).toBeTruthy();
  });

  it("Raw view shows the stored string, markers and footnote included, and keeps marks on the same words", async () => {
    const raw = Array.from(PHP_1_6);
    const start = raw.join("").indexOf("कि जिसने");
    const at = Array.from(PHP_1_6.slice(0, start)).length;
    seed(PHP_1_6, [finding({ start_offset: at, end_offset: at + 2, original_text: "कि" })]);
    rawView.set(true);
    render(VerseList, { props: { onSelect: vi.fn() } });
    const text = document.querySelector(".vtext") as HTMLElement;
    expect(text.classList.contains("raw")).toBe(true);
    expect(text.textContent).toContain("\\f + \\fr 1.6");
    expect(screen.queryByLabelText(FOOTNOTE_MARKER)).toBeNull();
    expect(text.querySelector("mark")?.textContent).toBe("कि");
  });
});

describe("VerseList ring from the panel's F8", () => {
  beforeEach(() => seed("मसीह यीशु के दास पौलुस"));
  afterEach(() => {
    activeLanguageQaFindingId.set(null);
    languageQaFindingsByVerse.set({});
  });

  it("rings the mark F8 moved to, and a row click clears it", async () => {
    languageQaFindingsByVerse.set({ [verseKey("1", "6")]: [
      lqaFinding({ id: "q1", chapter: "1", verse: "6", start: 0, end: 4, originalText: "मसीह" }),
      lqaFinding({ id: "q2", chapter: "1", verse: "6", start: 5, end: 9, originalText: "यीशु" })] });
    activeLanguageQaFindingId.set("q2");
    render(VerseList, { props: { onSelect: vi.fn() } });
    const ringed = await waitFor(() => {
      const mark = document.querySelector("mark.active-finding");
      expect(mark).not.toBeNull();
      return mark as HTMLElement;
    });
    expect(ringed.textContent).toBe("यीशु");
    await fireEvent.click(document.querySelector('[data-verse-key="1:6"]') as HTMLElement);
    expect(get(activeLanguageQaFindingId)).toBeNull();
  });
});


describe("VerseList word actions, as the indic-qa editor offers them", () => {
  const text = "मसीह यीशु के दास पौलुस";

  /** The browser's answer to "which character is under the pointer". */
  function pointAt(word: string, within = 1): void {
    const root = document.querySelector('[data-verse-key="1:6"] .vtext') as HTMLElement;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const at = (node.nodeValue ?? "").indexOf(word);
      if (at >= 0) {
        (document as unknown as { caretRangeFromPoint: () => Partial<Range> }).caretRangeFromPoint =
          () => ({ startContainer: node as Node, startOffset: at + within });
        return;
      }
    }
    throw new Error(`${word} is not in the verse`);
  }

  beforeEach(() => {
    seed(text);
    project.set({ path: "/p", bookId: "php" } as never);
    languageQaWordsAdd.mockReset().mockResolvedValue({ entries: [], count: 1 });
    languageQaOccurrences.mockReset().mockResolvedValue({ word: "यीशु", source: "irv", match: "text", ready: true,
      total: 0, truncated: false, hits: [] });
  });
  afterEach(() => {
    delete (document as unknown as { caretRangeFromPoint?: unknown }).caretRangeFromPoint;
    languageQaFindingsByVerse.set({});
    project.set(null);
  });

  it("offers Add, Copy and Search on an unmarked word under the pointer", async () => {
    render(VerseList, { props: { onSelect: vi.fn() } });
    pointAt("यीशु");
    await fireEvent.contextMenu(verseRow(), { clientX: 5, clientY: 5 });
    const menu = screen.getByRole("menu");
    expect(menu.textContent).toContain("“यीशु”");
    expect(screen.getByRole("menuitem", { name: "Copy word" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Flag “यीशु” for review…" })).toBeEnabled();
    await fireEvent.click(screen.getByRole("menuitem", { name: "Add “यीशु” to the project word list" }));
    await waitFor(() => expect(languageQaWordsAdd).toHaveBeenCalledWith("/p", ["यीशु"], "book"));
  });

  it("Search in this book looks for any run of the text", async () => {
    render(VerseList, { props: { onSelect: vi.fn() } });
    pointAt("यीशु");
    await fireEvent.contextMenu(verseRow(), { clientX: 5, clientY: 5 });
    await fireEvent.click(screen.getByRole("menuitem", { name: "Search in this book" }));
    await waitFor(() => expect(languageQaOccurrences).toHaveBeenCalledWith("/p", "यीशु", "irv", 200, "text"));
  });

  it("offers Add on a misspelling's mark and drops the mark at once; not where Add cannot silence it", async () => {
    const typo = lqaFinding({ id: "u1", rule: "hi.lex.unknown", ruleId: "hi-irv/hi.lex.unknown", category: "typo",
      layer: "lexicon", start: 5, end: 9, originalText: "यीशु", suggestions: [] });
    languageQaFindingsByVerse.set({ "1:6": [typo] });
    render(VerseList, { props: { onSelect: vi.fn() } });
    await fireEvent.contextMenu(document.querySelector('[data-finding-ids~="u1"]') as HTMLElement);
    const started = performance.now();
    await fireEvent.click(screen.getByRole("menuitem", { name: "Add “यीशु” to the project word list" }));
    expect(document.querySelector('[data-finding-ids~="u1"]')).toBeNull();
    expect(performance.now() - started).toBeLessThan(250);
    await waitFor(() => expect(languageQaWordsAdd).toHaveBeenCalledWith("/p", ["यीशु"], "book"));

    cleanup();
    languageQaFindingsByVerse.set({ "1:6": [{ ...typo, id: "m1", category: "unicode", ruleId: "ta-irv/indicqa.shape.malformed" }] });
    render(VerseList, { props: { onSelect: vi.fn() } });
    await fireEvent.contextMenu(document.querySelector('[data-finding-ids~="m1"]') as HTMLElement);
    expect(screen.queryByRole("menuitem", { name: /to the project word list/ })).toBeNull();
  });
});
