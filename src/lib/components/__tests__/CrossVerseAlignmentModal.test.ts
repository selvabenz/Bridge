import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/svelte";
import { get } from "svelte/store";
import CrossVerseAlignmentModal from "../CrossVerseAlignmentModal.svelte";
import {
  alignmentStatusByVerse, chapterVerseNums, checkStatusByVerse, currentChapter, findingsByVerse,
} from "../../stores";
import type {
  AlignmentContext, AlignmentRange, AlignmentToken, AutoAlignWindowResult, CrossVerseLink, CrossVerseLinkResult,
} from "../../types/finding";

const {
  getAlignmentRange, realignWords, unalignWords, runVerseChecks, getLexiconEntry, crossVerseLink, crossVerseUnlink,
  analysisJobGetScopeStatus, semanticLocationGetRange, targetSemanticGetRange, crossVersePropose,
  crossVerseAiPropose, getSettings, autoAlignWindow, autoAlignRevert, autoAlignVerdict, nullSet, nullClear,
} = vi.hoisted(() => ({
  crossVerseAiPropose: vi.fn(),
  autoAlignWindow: vi.fn(),
  autoAlignRevert: vi.fn(),
  autoAlignVerdict: vi.fn(),
  nullSet: vi.fn(),
  nullClear: vi.fn(),
  getSettings: vi.fn(),
  getAlignmentRange: vi.fn(),
  realignWords: vi.fn(),
  unalignWords: vi.fn(),
  runVerseChecks: vi.fn(),
  getLexiconEntry: vi.fn(),
  crossVerseLink: vi.fn(),
  crossVerseUnlink: vi.fn(),
  analysisJobGetScopeStatus: vi.fn(),
  semanticLocationGetRange: vi.fn(),
  targetSemanticGetRange: vi.fn(),
  crossVersePropose: vi.fn(),
}));

vi.mock("../../api/bridgeClient", () => ({
  bridge: {
    getAlignmentRange, realignWords, unalignWords, runVerseChecks, getLexiconEntry, crossVerseLink, crossVerseUnlink,
    analysisJobGetScopeStatus, semanticLocationGetRange, targetSemanticGetRange, crossVersePropose,
    crossVerseAiPropose, getSettings, autoAlignWindow, autoAlignRevert, autoAlignVerdict, nullSet, nullClear,
  },
}));

function token(id: string, word: string, extra: Partial<AlignmentToken> = {}): AlignmentToken {
  return { id, word, occurrence: 1, occurrences: 1, ...extra };
}

/** A verse with source tokens H001.. and target words T001.., where the
 *  first `aligned` pairs are grouped 1:1 and the rest sit in the word bank. */
function context(verse: string, sources: string[], targets: string[], aligned: number): AlignmentContext {
  const topTokens = sources.map((w, i) => token(`H${String(i + 1).padStart(3, "0")}`, w, { strong: "G2316", lemma: `${w}·lemma` }));
  const bottomTokens = targets.map((w, i) => token(`T${String(i + 1).padStart(3, "0")}`, w));
  const groups = topTokens.map((top, i) => ({
    id: `G${String(i + 1).padStart(3, "0")}`,
    topIds: [top.id],
    bottomIds: i < aligned && bottomTokens[i] ? [bottomTokens[i].id] : [],
  }));
  return {
    chapter: "1", verse,
    alignment: {
      alignments: groups.map((g) => ({
        topWords: g.topIds.map((id) => topTokens.find((t) => t.id === id)!),
        bottomWords: g.bottomIds.map((id) => bottomTokens.find((t) => t.id === id)!),
      })),
      wordBank: bottomTokens.slice(aligned),
    },
    topTokens, bottomTokens, groups,
    status: aligned === sources.length && aligned === targets.length ? "complete" : aligned ? "partial" : "untouched",
    completionState: "pending",
    sourceAvailable: true, sourceMessage: "",
    sourceDirection: "ltr", targetDirection: "ltr",
    issues: [], canComplete: false, history: [],
    chapterStatus: { complete: 1, partial: 1, untouched: 2, invalid: 0 },
    gaps: {
      sourceUnmatched: groups.filter((g) => g.bottomIds.length === 0).length,
      targetUnmatched: targets.length - aligned,
    },
    crossVerseLinks: [],
    crossVerseGroups: [],
    crossVerseAccountedIds: [],
    crossVerseRealizedIds: [],
    crossVerseAccounted: 0,
    crossVerseRealized: 0,
    fullyAccounted: false,
    nullDecisions: { source: [], target: [] },
    accountedBy: { tc: 0, crossVerse: 0, null: 0 },
    accounted: false,
  };
}

const V1 = context("1", ["θεός"], ["God"], 1);
const V2 = context("2", ["λόγος", "ἦν"], ["word", "was"], 1);
const V34 = context("3-4", ["φῶς"], ["light", "shone"], 0);

/** The link the engine would record for "was" (1:2) dropped on φῶς (1:3-4). */
const LINK: CrossVerseLink = {
  id: "link-1", bookId: "php",
  source: { chapter: "1", verse: "3-4", word: "φῶς", occurrence: 1, occurrences: 1, signature: "φῶς␟1␟1", strong: "G2316" },
  target: { chapter: "1", verse: "2", word: "was", occurrence: 1, occurrences: 1, signature: "was␟1␟1" },
  state: "active", createdAt: "t", updatedAt: "t", actorId: "human",
  sourceTopId: null, targetBottomId: null,
};

function linked(): CrossVerseLinkResult {
  return {
    link: LINK,
    source: {
      ...V34,
      crossVerseLinks: [{ ...LINK, sourceTopId: "H001" }],
      crossVerseRealizedIds: ["H001"], crossVerseRealized: 1,
      gaps: { sourceUnmatched: 0, targetUnmatched: 2 },
    },
    target: {
      ...V2,
      crossVerseLinks: [{ ...LINK, targetBottomId: "T002" }],
      crossVerseAccountedIds: ["T002"], crossVerseAccounted: 1,
      gaps: { sourceUnmatched: 1, targetUnmatched: 0 },
    },
  };
}

function rangeFor(verses: string[], all: Record<string, AlignmentContext> = { "1": V1, "2": V2, "3-4": V34 }): AlignmentRange {
  return { chapter: "1", verses: verses.map((v) => all[v]), chapterStatus: V1.chapterStatus };
}

function seed() {
  chapterVerseNums.set({ "1": ["1", "2", "3-4", "5"] });
  currentChapter.set("1");
  alignmentStatusByVerse.set({});
  checkStatusByVerse.set({});
  findingsByVerse.set({});
}

const V5 = context("5", ["ζωή"], ["life"], 0);

beforeEach(() => {
  seed();
  getAlignmentRange.mockImplementation(async (_chapter: string, verses: string[]) =>
    rangeFor(verses, { "1": V1, "2": V2, "3-4": V34, "5": V5 }));
  getLexiconEntry.mockResolvedValue({
    languageId: "el-x-koine",
    segments: [{ meaning: "a deity, especially the supreme Divinity", lemma: "θεός", usage: "God, god" }],
  });
  runVerseChecks.mockResolvedValue([{ id: "f1" }]);
  // No completed analysis by default: the suggestion path stays quiet.
  analysisJobGetScopeStatus.mockResolvedValue({ state: "NOT_ANALYZED", latestJob: null });
  crossVersePropose.mockResolvedValue({
    chapter: "1", verses: ["1", "2", "3-4"], proposals: [], calibrationVersion: "cross-verse-uncalibrated-v1",
  });
  // An API key is configured by default so the AI button renders; the
  // no-key case is its own test.
  getSettings.mockResolvedValue({ hasApiKey: true });
  crossVerseAiPropose.mockResolvedValue({
    chapter: "1", verses: ["1", "2", "3-4"], proposals: [], corpusProposals: [],
    calibrationVersion: "cross-verse-ai-uncalibrated-v1", modelConsulted: true,
  });
});

/** A proposal shaped as `alignment.crossVerse.propose` returns it (#139). */
function proposal(overrides: Record<string, unknown> = {}) {
  return {
    status: "PROPOSED", confidence: 0.65, margin: 0.55, contested: false,
    source: {
      chapter: "1", verse: "3-4", topId: "H001", word: "φῶς",
      signature: "φῶς␟1␟1", strong: "G54570", lemma: "φῶς",
    },
    target: { chapter: "1", verse: "2", bottomId: "T002", word: "was", signature: "was␟1␟1" },
    evidence: [
      { kind: "STRONGS_PRECEDENT", rawScore: 1, weight: 0.55, weightedScore: 0.55, jointCount: 7, sourceCount: 7 },
      { kind: "SURFACE_PRECEDENT", rawScore: 0, weight: 0.45, weightedScore: 0, jointCount: 0, sourceCount: 0 },
      { kind: "PHONETIC", rawScore: 0, weight: 0.25, weightedScore: 0 },
      { kind: "PROXIMITY", rawScore: 1, weight: 0.1, weightedScore: 0.1 },
    ],
    alternatives: [],
    ...overrides,
  };
}

async function renderPage(verse = "2", initialVerses: string[] = []) {
  const onClose = vi.fn();
  const utils = render(CrossVerseAlignmentModal, { props: { chapter: "1", verse, onClose, initialVerses } });
  await waitFor(() => expect(getAlignmentRange).toHaveBeenCalled());
  await waitFor(() => expect(screen.getAllByText("1:2").length).toBeGreaterThan(0));
  return { ...utils, onClose };
}

async function pickUp(word: string, verse: string) {
  const bank = screen.getByLabelText(`Word bank of verse ${verse}`);
  await fireEvent.click(within(bank).getByRole("button", { name: word }));
}

describe("CrossVerseAlignmentModal", () => {
  it("opens on the selected verse ±1 and shows every verse in both columns plus the verdict strip", async () => {
    await renderPage("2");
    expect(getAlignmentRange).toHaveBeenCalledWith("1", ["1", "2", "3-4"]);
    // Two columns, so each verse header appears twice; the bridge verse keeps its string.
    expect(screen.getAllByText("1:1")).toHaveLength(2);
    expect(screen.getAllByText("1:3-4")).toHaveLength(2);
    const strip = screen.getByLabelText("Verse verdicts");
    // No automatic pass has run: every verse is "Not aligned yet".
    expect(within(strip).getAllByText(/Not aligned yet/)).toHaveLength(3);
    expect(within(strip).getByText("2 source · 2 target words")).toBeInTheDocument();
    expect(within(strip).getByText(/Range: 2 source · 3 target unmatched/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /verses 1–3-4/ })).toBeInTheDocument();
    // Nothing is asked of a provider on open.
    expect(autoAlignWindow).not.toHaveBeenCalled();
  });

  it("labels a source word with its renderings, not its lemma, and keeps lemma and definition on hover", async () => {
    const { container } = await renderPage("2");
    const source = [...container.querySelectorAll<HTMLElement>("button.token.source")]
      .find((element) => element.textContent?.includes("λόγος"))!;
    // The lemma is another Greek string, so it is no longer the visible label.
    await waitFor(() => expect(within(source).getByText("God, god")).toBeInTheDocument());
    expect(within(source).queryByText("λόγος·lemma")).not.toBeInTheDocument();
    const title = source.getAttribute("title") ?? "";
    expect(title).toContain("λόγος·lemma");
    expect(title).toContain("a deity, especially the supreme Divinity");
  });

  it("a same-verse drop realigns through alignment.realign, resending the column's existing words, then reruns local checks", async () => {
    realignWords.mockResolvedValue({ ...V2, status: "complete", gaps: { sourceUnmatched: 0, targetUnmatched: 0 } });
    await renderPage("2");
    // Click-to-pick-up is the same one-step drop the pointer drag delivers.
    await pickUp("was", "2");
    await fireEvent.click(screen.getByLabelText(/Align picked-up word to λόγος in verse 2/));
    await waitFor(() => expect(realignWords).toHaveBeenCalledTimes(1));
    expect(realignWords).toHaveBeenCalledWith("1", "2", ["H001"], ["T001", "T002"], V2.alignment);
    await waitFor(() => expect(runVerseChecks).toHaveBeenCalledWith("1", "2", ["alignment", "greekroom"]));
    expect(get(alignmentStatusByVerse)["1:2"]).toBe("complete");
    expect(get(checkStatusByVerse)["1:2"]).toBe("succeeded");
    expect(get(findingsByVerse)["1:2"]).toEqual([{ id: "f1" }]);
    expect(unalignWords).not.toHaveBeenCalled();
    expect(crossVerseLink).not.toHaveBeenCalled();
  });

  it("a cross-verse drop records a Bridge-private link (#117), patches both verses and rechecks each", async () => {
    crossVerseLink.mockResolvedValue(linked());
    await renderPage("2");
    await pickUp("was", "2");
    await fireEvent.click(screen.getByLabelText(/Align picked-up word to φῶς in verse 3-4/));
    await waitFor(() => expect(crossVerseLink).toHaveBeenCalledTimes(1));
    // Source is the column (verse 3-4's φῶς), target is the dragged word (verse 2's "was").
    expect(crossVerseLink).toHaveBeenCalledWith(
      { chapter: "1", verse: "3-4", topId: "H001" },
      { chapter: "1", verse: "2", bottomId: "T002" },
    );
    expect(realignWords).not.toHaveBeenCalled();
    await waitFor(() => expect(runVerseChecks).toHaveBeenCalledWith("1", "3-4", ["alignment", "greekroom"]));
    await waitFor(() => expect(runVerseChecks).toHaveBeenCalledWith("1", "2", ["alignment", "greekroom"]));
    expect(await screen.findByText(/Cross-verse link saved/)).toBeInTheDocument();

    // The source row shows where the token is realized ...
    const cell = screen.getByLabelText(/Target words aligned to φῶς in verse 3-4/);
    expect(within(cell).getByText("was")).toBeInTheDocument();
    expect(within(cell).getByText(/realized in v\.2/)).toBeInTheDocument();
    // ... the word stays in its own verse's bank, marked and no longer draggable ...
    const bank2 = screen.getByLabelText("Word bank of verse 2");
    expect(within(bank2).queryByRole("button", { name: "was" })).not.toBeInTheDocument();
    expect(within(bank2).getByText("↔ v.3-4")).toBeInTheDocument();
    expect(within(bank2).getByTitle(/Realizes φῶς from verse 3-4/)).toBeInTheDocument();
    // ... and the range total counts it as linked, not as a gap. tC status is untouched.
    const strip = screen.getByLabelText("Verse verdicts");
    expect(within(strip).getByText(/Range: 1 source · 2 target unmatched/)).toBeInTheDocument();
    expect(get(alignmentStatusByVerse)["1:2"]).toBe("partial");
  });

  it("the × on a linked chip or an accounted word removes the link through alignment.crossVerse.unlink", async () => {
    getAlignmentRange.mockImplementation(async (_c: string, verses: string[]) =>
      rangeFor(verses, { "1": V1, "2": linked().target, "3-4": linked().source }));
    crossVerseUnlink.mockResolvedValue({ link: LINK, source: V34, target: V2 });
    await renderPage("2");
    await fireEvent.click(screen.getByRole("button", { name: /Remove cross-verse link from φῶς to was in verse 2/ }));
    await waitFor(() => expect(crossVerseUnlink).toHaveBeenCalledWith("link-1"));
    expect(await screen.findByText(/Cross-verse link removed/)).toBeInTheDocument();
    const bank2 = screen.getByLabelText("Word bank of verse 2");
    expect(within(bank2).getByRole("button", { name: "was" })).toBeInTheDocument();
  });

  it("shows an invalidated link with its reason and lets it be removed", async () => {
    const invalid: CrossVerseLink = { ...LINK, state: "invalid", invalidReason: "was is no longer in the text of 1:2.", sourceTopId: "H001" };
    getAlignmentRange.mockImplementation(async (_c: string, verses: string[]) =>
      rangeFor(verses, { "1": V1, "2": V2, "3-4": { ...V34, crossVerseLinks: [invalid] } }));
    await renderPage("2");
    const cell = screen.getByLabelText(/Target words aligned to φῶς in verse 3-4/);
    expect(within(cell).getByText(/link invalid v\.2/)).toBeInTheDocument();
    expect(within(cell).getByTitle("was is no longer in the text of 1:2.")).toBeInTheDocument();
    // An invalid link accounts for nothing: φῶς is still a gap.
    const strip = screen.getByLabelText("Verse verdicts");
    expect(within(strip).getByText(/Range: 2 source · 3 target unmatched/)).toBeInTheDocument();
  });

  it("dropping a word into another verse's word bank is refused with guidance", async () => {
    await renderPage("2");
    await pickUp("was", "2");
    await fireEvent.click(screen.getByLabelText(/word bank of verse 1$/i));
    expect(await screen.findByText(/not into its word bank/)).toBeInTheDocument();
    expect(unalignWords).not.toHaveBeenCalled();
    expect(crossVerseLink).not.toHaveBeenCalled();
  });

  it("the × on an aligned card unaligns within its own verse", async () => {
    unalignWords.mockResolvedValue(V1);
    await renderPage("2");
    await fireEvent.click(screen.getByRole("button", { name: "Unalign God from θεός" }));
    await waitFor(() => expect(unalignWords).toHaveBeenCalledWith("1", "1", ["T001"], V1.alignment));
    await waitFor(() => expect(runVerseChecks).toHaveBeenCalledWith("1", "1", ["alignment", "greekroom"]));
  });

  it("clicking a verse in the verdict strip filters both columns to that verse's gaps", async () => {
    await renderPage("2");
    const strip = screen.getByLabelText("Verse verdicts");
    await fireEvent.click(within(strip).getByRole("button", { name: /v\.2/ }));
    // Only verse 2 remains, only its unmatched source (ἦν) and unaligned target (was).
    expect(screen.queryAllByText("1:1")).toHaveLength(0);
    expect(screen.queryByText("λόγος")).not.toBeInTheDocument();
    expect(screen.getByText("ἦν")).toBeInTheDocument();
    expect(screen.queryByText("word")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "was" })).toBeInTheDocument();
    await fireEvent.click(screen.getByRole("button", { name: "Show all" }));
    expect(screen.getAllByText("1:1")).toHaveLength(2);
  });

  it("changing the range refetches with the new verse strings and toggling a chip drops a verse", async () => {
    await renderPage("2");
    const [fromSelect] = screen.getAllByRole("combobox");
    await fireEvent.change(fromSelect, { target: { value: "2" } });
    await waitFor(() => expect(getAlignmentRange).toHaveBeenLastCalledWith("1", ["2", "3-4"]));
    const chips = screen.getByRole("group", { name: "Verses in range" });
    await fireEvent.click(within(chips).getByRole("button", { name: "3-4" }));
    await waitFor(() => expect(getAlignmentRange).toHaveBeenLastCalledWith("1", ["2"]));
  });

  it("starts from the given verses instead of anchor ±1 when opened from a multi-selection or a finding (#118)", async () => {
    await renderPage("2", ["5", "2"]);
    expect(getAlignmentRange).toHaveBeenCalledWith("1", ["2", "5"]);
    expect(screen.getByRole("heading", { name: /verses 2–5/ })).toBeInTheDocument();
    // The picker's chips cover the span; only the given verses are on.
    const chips = screen.getByRole("group", { name: "Verses in range" });
    expect(within(chips).getByRole("button", { name: "3-4" })).toHaveAttribute("aria-pressed", "false");
    expect(within(chips).getByRole("button", { name: "5" })).toHaveAttribute("aria-pressed", "true");
    // No suggestion is fetched for an explicit range.
    expect(analysisJobGetScopeStatus).not.toHaveBeenCalled();
  });

  it("widens the default range with the last Stage 6B run's cross-verse verses and can go back (#118)", async () => {
    analysisJobGetScopeStatus.mockResolvedValue({
      state: "ANALYZED", latestJob: { stageStatuses: { LOCATION: { runId: "run-1" } } },
    });
    semanticLocationGetRange.mockResolvedValue({
      targetInventoryId: "inv-1",
      relationships: [{ properties: ["CROSS_VERSE"], targetTokenInstanceIds: ["t5"] }],
    });
    targetSemanticGetRange.mockResolvedValue({ tokens: [{ id: "t5", displayedReference: "PHP 1:5" }] });
    await renderPage("2");
    await waitFor(() => expect(getAlignmentRange).toHaveBeenLastCalledWith("1", ["1", "2", "3-4", "5"]));
    expect(await screen.findByText(/widened with v\.5/)).toBeInTheDocument();
    expect(screen.getAllByText("1:5")).toHaveLength(2);
    await fireEvent.click(screen.getByRole("button", { name: "Back to 2 ±1" }));
    await waitFor(() => expect(getAlignmentRange).toHaveBeenLastCalledWith("1", ["1", "2", "3-4"]));
    expect(screen.queryByText(/widened with/)).not.toBeInTheDocument();
  });

  it("closes itself when the chapter changes underneath it", async () => {
    const { onClose } = await renderPage("2");
    currentChapter.set("2");
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  describe("corpus suggestions, inline (#139, #222)", () => {
    async function suggest() {
      await fireEvent.click(screen.getByRole("button", { name: /Suggest links/ }));
      await waitFor(() => expect(crossVersePropose).toHaveBeenCalled());
    }

    function cellOf(word: string, verse: string) {
      return screen.getByLabelText(new RegExp(`Target words aligned to ${word} in verse ${verse}`));
    }

    it("asks for nothing until the reviewer asks: a proposal is a claim, not a default", async () => {
      await renderPage("2");
      expect(crossVersePropose).not.toHaveBeenCalled();
      await suggest();
      expect(crossVersePropose).toHaveBeenCalledWith("1", ["1", "2", "3-4"]);
    });

    it("shows the claim in the cell it would fill, with why on hover", async () => {
      crossVersePropose.mockResolvedValue({
        chapter: "1", verses: ["1", "2", "3-4"], proposals: [proposal()],
        calibrationVersion: "cross-verse-uncalibrated-v1",
      });
      await renderPage("2");
      await suggest();
      const cell = cellOf("φῶς", "3-4");
      expect(within(cell).getByText("was ?")).toBeInTheDocument();
      expect(within(cell).getByTitle(/rendered "was" 7× in completed verses/)).toBeInTheDocument();
    });

    it("accepting is an ordinary cross-verse link, and nothing was written before it", async () => {
      crossVersePropose.mockResolvedValue({
        chapter: "1", verses: ["1", "2", "3-4"], proposals: [proposal()],
        calibrationVersion: "cross-verse-uncalibrated-v1",
      });
      crossVerseLink.mockResolvedValue(linked());
      await renderPage("2");
      await suggest();
      expect(crossVerseLink).not.toHaveBeenCalled();
      await fireEvent.click(screen.getByRole("button", { name: "Accept was for φῶς" }));
      await waitFor(() => expect(crossVerseLink).toHaveBeenCalledWith(
        { chapter: "1", verse: "3-4", topId: "H001" },
        { chapter: "1", verse: "2", bottomId: "T002" },
      ));
      await waitFor(() => expect(runVerseChecks).toHaveBeenCalledWith("1", "3-4", ["alignment", "greekroom"]));
    });

    it("an ambiguous proposal has no one-click accept", async () => {
      crossVersePropose.mockResolvedValue({
        chapter: "1", verses: ["1", "2", "3-4"],
        proposals: [proposal({ status: "AMBIGUOUS", contested: true, margin: 0 })],
        calibrationVersion: "cross-verse-uncalibrated-v1",
      });
      await renderPage("2");
      await suggest();
      const cell = cellOf("φῶς", "3-4");
      expect(within(cell).getByText(/ambiguous/)).toBeInTheDocument();
      expect(within(cell).queryByRole("button", { name: /Accept/ })).not.toBeInTheDocument();
    });

    it("a refused link leaves the suggestion on screen with the error", async () => {
      crossVersePropose.mockResolvedValue({
        chapter: "1", verses: ["1", "2", "3-4"], proposals: [proposal()],
        calibrationVersion: "cross-verse-uncalibrated-v1",
      });
      crossVerseLink.mockRejectedValue(new Error("was is already aligned in verse 2."));
      await renderPage("2");
      await suggest();
      await fireEvent.click(screen.getByRole("button", { name: "Accept was for φῶς" }));
      expect(await screen.findByText(/already aligned in verse 2/)).toBeInTheDocument();
      expect(within(cellOf("φῶς", "3-4")).getByText("was ?")).toBeInTheDocument();
    });

    it("dismissing removes it without writing anything", async () => {
      crossVersePropose.mockResolvedValue({
        chapter: "1", verses: ["1", "2", "3-4"], proposals: [proposal()],
        calibrationVersion: "cross-verse-uncalibrated-v1",
      });
      await renderPage("2");
      await suggest();
      await fireEvent.click(within(cellOf("φῶς", "3-4")).getByRole("button", { name: "Dismiss this suggestion" }));
      expect(within(cellOf("φῶς", "3-4")).queryByText("was ?")).not.toBeInTheDocument();
      expect(crossVerseLink).not.toHaveBeenCalled();
    });

    it("says why there is nothing to suggest rather than showing nothing", async () => {
      crossVersePropose.mockResolvedValue({
        chapter: "1", verses: ["1", "2", "3-4"], proposals: [],
        calibrationVersion: "cross-verse-uncalibrated-v1",
        unavailable: {
          reason: "no-completed-alignments",
          message: "Cross-verse suggestions are learned from this project's own completed alignments…",
        },
      });
      await renderPage("2");
      await suggest();
      expect(screen.getByText(/Cross-verse suggestions are learned from this project's own completed alignments…/)).toBeInTheDocument();
    });

    it("reports suggestions as stale when the range moves under them", async () => {
      crossVersePropose.mockResolvedValue({
        chapter: "1", verses: ["1", "2", "3-4"], proposals: [proposal()],
        calibrationVersion: "cross-verse-uncalibrated-v1",
      });
      await renderPage("2");
      await suggest();
      expect(screen.queryByText(/range changed/i)).not.toBeInTheDocument();
      await fireEvent.change(screen.getByLabelText("To"), { target: { value: "5" } });
      await waitFor(() => expect(getAlignmentRange).toHaveBeenLastCalledWith("1", ["1", "2", "3-4", "5"]));
      expect(await screen.findByText(/The range changed since the suggestions were worked out/)).toBeInTheDocument();
    });
  });

  describe("automatic alignment (#222)", () => {
    const sig = (w: string) => `${w}␟1␟1`;
    /** v.2 after a pass: λόγος → word placed (ai), ἦν left unplaced, "was" disputed. */
    function after(): AutoAlignWindowResult {
      const v2 = { ...V2, autoAlign: { verdict: "NEEDS_REVIEW" as const, runId: "aa-1", createdAt: "", stale: false, issues: 1, suggestions: 1 } };
      return {
        chapter: "1", verses: ["1", "2", "3-4"], runId: "aa-1", calibrationVersion: "two-pass-agreement-v1",
        corpus: { checked: false, reason: "no-completed-alignments" },
        usage: { calls: 2, totalTokens: 41280, estimatedCostUSD: 0.13 },
        results: [
          { verse: "1", verdict: "ALIGNED_CLEAN", applied: { groups: [{ tops: [sig("θεός")], bottoms: [sig("God")] }], links: [], nulls: [] },
            issues: [], suggestions: [], context: { ...V1, accounted: true, autoAlign: { verdict: "ALIGNED_CLEAN", runId: "aa-1", createdAt: "", stale: false, issues: 0, suggestions: 0 } } },
          { verse: "2", verdict: "NEEDS_REVIEW", applied: { groups: [{ tops: [sig("λόγος")], bottoms: [sig("word")] }], links: [], nulls: [] },
            issues: [{ kind: "POSSIBLE_OMISSION", side: "source", signature: sig("ἦν"), word: "ἦν", id: "H002", note: "no word for 'was' (to be)" }],
            suggestions: [{
              kind: "link", status: "UNCERTAIN", votes: { "source-first": true, "target-first": false },
              source: { side: "source", chapter: "1", verse: "3-4", signature: sig("φῶς"), word: "φῶς", id: "H001" },
              target: { side: "target", chapter: "1", verse: "2", signature: sig("was"), word: "was", id: "T002" },
              token: null, reason: "light shone", note: "", confidence: 70,
            }],
            context: v2 },
          { verse: "3-4", verdict: "NEEDS_REVIEW", applied: { groups: [], links: [], nulls: [] }, issues: [],
            suggestions: [], context: { ...V34, autoAlign: { verdict: "NEEDS_REVIEW", runId: "aa-1", createdAt: "", stale: false, issues: 0, suggestions: 1 } } },
        ],
      };
    }

    it("runs only on a click, then shows the verdicts, the omission and the suggestion in place", async () => {
      autoAlignWindow.mockResolvedValue(after());
      await renderPage("2");
      await fireEvent.click(await screen.findByRole("button", { name: /Align automatically/ }));
      await waitFor(() => expect(autoAlignWindow).toHaveBeenCalledWith("1", ["1", "2", "3-4"]));
      const strip = screen.getByLabelText("Verse verdicts");
      expect(await within(strip).findByText(/✓ Aligned/)).toBeInTheDocument();
      expect(within(strip).getAllByText(/Needs review/).length).toBeGreaterThan(0);
      // The omission sits on ἦν's cell with the model's note and a way to say why it is fine.
      const cell = screen.getByLabelText(/Target words aligned to ἦν in verse 2/);
      expect(within(cell).getByText("possible omission")).toBeInTheDocument();
      expect(within(cell).getByTitle("no word for 'was' (to be)")).toBeInTheDocument();
      // The disputed link shows in φῶς's cell, with both votes on hover.
      const phos = screen.getByLabelText(/Target words aligned to φῶς in verse 3-4/);
      expect(within(phos).getByText("was ?")).toBeInTheDocument();
      expect(within(phos).getByTitle(/source-first: yes · target-first: no/)).toBeInTheDocument();
      // The placed group is tagged as the pass's own.
      expect(screen.getAllByText("ai").length).toBeGreaterThan(0);
      expect(screen.getByText(/Last run: 2 requests · 41,280 tokens/)).toBeInTheDocument();
      // Every verse was rechecked so the editor's findings are current.
      await waitFor(() => expect(runVerseChecks).toHaveBeenCalledWith("1", "2", ["alignment", "greekroom"]));
    });

    it("is disabled with the reason when no key is configured, and for a range it cannot take", async () => {
      getSettings.mockResolvedValue({ hasApiKey: false });
      await renderPage("2");
      const button = await screen.findByRole("button", { name: /Align automatically/ });
      await waitFor(() => expect(button).toBeDisabled());
      expect(button).toHaveAttribute("title", "Add an API key in Settings to align automatically");
      expect(screen.getByRole("button", { name: /Suggest links/ })).toBeEnabled();
    });

    it("Not missing ▸ Implicit records a null decision through alignment.null.set", async () => {
      autoAlignWindow.mockResolvedValue(after());
      nullSet.mockResolvedValue({ decision: {}, context: V2 });
      await renderPage("2");
      await fireEvent.click(await screen.findByRole("button", { name: /Align automatically/ }));
      // The page rechecks every verse after a run; controls wait for that.
      const notMissing = await screen.findByRole("button", { name: "Not missing ▾" });
      await waitFor(() => expect(notMissing).toBeEnabled());
      await fireEvent.click(notMissing);
      await fireEvent.click(screen.getByRole("menuitem", { name: /Implicit/ }));
      await waitFor(() => expect(nullSet).toHaveBeenCalledWith("1", "2", "source", "H002", "IMPLICIT", ""));
    });

    it("accepting a disputed cross-verse suggestion is an ordinary link", async () => {
      autoAlignWindow.mockResolvedValue(after());
      crossVerseLink.mockResolvedValue(linked());
      await renderPage("2");
      await fireEvent.click(await screen.findByRole("button", { name: /Align automatically/ }));
      const accept = await screen.findByRole("button", { name: "Accept was for φῶς" });
      await waitFor(() => expect(accept).toBeEnabled());
      await fireEvent.click(accept);
      await waitFor(() => expect(crossVerseLink).toHaveBeenCalledWith(
        { chapter: "1", verse: "3-4", topId: "H001" },
        { chapter: "1", verse: "2", bottomId: "T002" },
      ));
    });

    it("Undo a verse calls alignment.autoAlign.revert for that verse", async () => {
      autoAlignWindow.mockResolvedValue(after());
      autoAlignRevert.mockResolvedValue({ chapter: "1", verse: "1", skipped: [], context: V1 });
      await renderPage("2");
      await fireEvent.click(await screen.findByRole("button", { name: /Align automatically/ }));
      const undo = await screen.findByLabelText("Undo a verse");
      await waitFor(() => expect(undo).toBeEnabled());
      await fireEvent.change(undo, { target: { value: "1" } });
      await waitFor(() => expect(autoAlignRevert).toHaveBeenCalledWith("1", "1"));
      expect(await screen.findByText(/Restored v\.1/)).toBeInTheDocument();
    });

    it("an unavailable provider is reported, not thrown", async () => {
      autoAlignWindow.mockResolvedValue({
        chapter: "1", verses: ["1", "2", "3-4"], runId: "aa-2", calibrationVersion: "two-pass-agreement-v1", results: [],
        unavailable: { reason: "no-api-key", message: "No OpenAI-compatible API key is configured." },
      });
      await renderPage("2");
      await fireEvent.click(await screen.findByRole("button", { name: /Align automatically/ }));
      expect(await screen.findByText(/No OpenAI-compatible API key is configured/)).toBeInTheDocument();
    });

    it("loads the stored verdict of a verse an earlier pass ran on", async () => {
      const v1 = { ...V1, accounted: true, autoAlign: { verdict: "ALIGNED_CLEAN" as const, runId: "aa-0", createdAt: "", stale: false, issues: 0, suggestions: 0 } };
      getAlignmentRange.mockImplementation(async (_c: string, verses: string[]) =>
        rangeFor(verses, { "1": v1, "2": V2, "3-4": V34 }));
      autoAlignVerdict.mockResolvedValue({ chapter: "1", verse: "1", verdict: {
        chapter: "1", verse: "1", verdict: "ALIGNED_CLEAN", runId: "aa-0", applyRequested: true, issues: [], suggestions: [],
        applied: { groups: [], links: [], nulls: [] }, window: ["1", "2"], createdAt: "",
      } });
      await renderPage("2");
      await waitFor(() => expect(autoAlignVerdict).toHaveBeenCalledWith("1", "1"));
      expect(autoAlignVerdict).toHaveBeenCalledTimes(1);
      expect(await within(screen.getByLabelText("Verse verdicts")).findByText(/✓ Aligned/)).toBeInTheDocument();
    });
  });
});
