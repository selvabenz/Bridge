import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/svelte";
import { get } from "svelte/store";
import CrossVerseAlignmentModal from "../CrossVerseAlignmentModal.svelte";
import {
  alignmentStatusByVerse, chapterVerseNums, checkStatusByVerse, currentChapter, findingsByVerse,
} from "../../stores";
import type {
  AlignmentContext, AlignmentRange, AlignmentToken, CrossVerseLink, CrossVerseLinkResult,
} from "../../types/finding";

const {
  getAlignmentRange, realignWords, unalignWords, runVerseChecks, getLexiconEntry, crossVerseLink, crossVerseUnlink,
  analysisJobGetScopeStatus, semanticLocationGetRange, targetSemanticGetRange, crossVersePropose,
  crossVerseAiPropose, getSettings,
} = vi.hoisted(() => ({
  crossVerseAiPropose: vi.fn(),
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
    crossVerseAiPropose, getSettings,
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
  it("opens on the selected verse ±1 and shows every verse in both columns plus the gap overview", async () => {
    await renderPage("2");
    expect(getAlignmentRange).toHaveBeenCalledWith("1", ["1", "2", "3-4"]);
    // Two columns, so each verse header appears twice; the bridge verse keeps its string.
    expect(screen.getAllByText("1:1")).toHaveLength(2);
    expect(screen.getAllByText("1:3-4")).toHaveLength(2);
    const strip = screen.getByLabelText("Gap overview");
    // v.2 and v.3-4 each have one unmatched source word; only v.3-4 has two unaligned targets.
    expect(within(strip).getAllByText("1 source word with no counterpart")).toHaveLength(2);
    expect(within(strip).getByText("2 target words with no counterpart")).toBeInTheDocument();
    expect(within(strip).getByText("0 source words with no counterpart")).toBeInTheDocument();
    expect(within(strip).getByText(/Range: 2 source · 3 target unmatched/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /verses 1–3-4/ })).toBeInTheDocument();
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
    // ... and the gap strip counts it as linked, not as a gap. tC status is untouched.
    const strip = screen.getByLabelText("Gap overview");
    expect(within(strip).getAllByText(/1 linked across verses/)).toHaveLength(2);
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
    const strip = screen.getByLabelText("Gap overview");
    expect(within(strip).getAllByText("1 source word with no counterpart")).toHaveLength(2);
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

  it("clicking a verse in the gap overview filters both columns to that verse's gaps", async () => {
    await renderPage("2");
    const strip = screen.getByLabelText("Gap overview");
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
    expect(await screen.findByText(/Range widened with verse 5/)).toBeInTheDocument();
    expect(screen.getAllByText("1:5")).toHaveLength(2);
    await fireEvent.click(screen.getByRole("button", { name: "Back to 2 ±1" }));
    await waitFor(() => expect(getAlignmentRange).toHaveBeenLastCalledWith("1", ["1", "2", "3-4"]));
    expect(screen.queryByText(/Range widened/)).not.toBeInTheDocument();
  });

  it("closes itself when the chapter changes underneath it", async () => {
    const { onClose } = await renderPage("2");
    currentChapter.set("2");
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  describe("cross-verse suggestions (#139)", () => {
    async function suggest() {
      await fireEvent.click(screen.getByRole("button", { name: /Suggest links/ }));
      await waitFor(() => expect(crossVersePropose).toHaveBeenCalled());
    }

    it("asks for nothing until the reviewer asks: a proposal is a claim, not a default", async () => {
      await renderPage("2");
      expect(crossVersePropose).not.toHaveBeenCalled();
      await suggest();
      expect(crossVersePropose).toHaveBeenCalledWith("1", ["1", "2", "3-4"]);
    });

    it("shows the claim and why it was made", async () => {
      crossVersePropose.mockResolvedValue({
        chapter: "1", verses: ["1", "2", "3-4"], proposals: [proposal()],
        calibrationVersion: "cross-verse-uncalibrated-v1",
      });
      await renderPage("2");
      await suggest();

      const strip = screen.getByLabelText("Cross-verse suggestions");
      expect(within(strip).getByText("φῶς")).toBeInTheDocument();
      expect(within(strip).getByText("was")).toBeInTheDocument();
      expect(within(strip).getByText(/rendered "was" 7× in completed verses/)).toBeInTheDocument();
      expect(within(strip).getByText("1 suggestion")).toBeInTheDocument();
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

      await fireEvent.click(screen.getByRole("button", { name: "Accept" }));

      await waitFor(() => expect(crossVerseLink).toHaveBeenCalledWith(
        { chapter: "1", verse: "3-4", topId: "H001" },
        { chapter: "1", verse: "2", bottomId: "T002" },
      ));
      await waitFor(() => expect(runVerseChecks).toHaveBeenCalledWith("1", "3-4", ["alignment", "greekroom"]));
    });

    it("an ambiguous proposal cannot be accepted in one click; it points at the gap instead", async () => {
      crossVersePropose.mockResolvedValue({
        chapter: "1", verses: ["1", "2", "3-4"],
        proposals: [proposal({ status: "AMBIGUOUS", contested: true, margin: 0 })],
        calibrationVersion: "cross-verse-uncalibrated-v1",
      });
      await renderPage("2");
      await suggest();

      const strip = screen.getByLabelText("Cross-verse suggestions");
      expect(within(strip).queryByRole("button", { name: "Accept" })).not.toBeInTheDocument();
      expect(within(strip).getByText("ambiguous")).toBeInTheDocument();
      await fireEvent.click(within(strip).getByRole("button", { name: "Show the gap" }));
      expect(crossVerseLink).not.toHaveBeenCalled();
    });

    it("a refused link leaves the suggestion on screen with the error", async () => {
      crossVersePropose.mockResolvedValue({
        chapter: "1", verses: ["1", "2", "3-4"], proposals: [proposal()],
        calibrationVersion: "cross-verse-uncalibrated-v1",
      });
      crossVerseLink.mockRejectedValue(new Error("was is already aligned in verse 2."));
      await renderPage("2");
      await suggest();

      await fireEvent.click(screen.getByRole("button", { name: "Accept" }));

      expect(await screen.findByText(/already aligned in verse 2/)).toBeInTheDocument();
      const strip = screen.getByLabelText("Cross-verse suggestions");
      expect(within(strip).getByText("1 suggestion")).toBeInTheDocument();
    });

    it("dismissing removes it without writing anything", async () => {
      crossVersePropose.mockResolvedValue({
        chapter: "1", verses: ["1", "2", "3-4"], proposals: [proposal()],
        calibrationVersion: "cross-verse-uncalibrated-v1",
      });
      await renderPage("2");
      await suggest();

      await fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));

      const strip = screen.getByLabelText("Cross-verse suggestions");
      expect(within(strip).getByText("0 suggestions")).toBeInTheDocument();
      expect(crossVerseLink).not.toHaveBeenCalled();
    });

    it("says why there is nothing to suggest rather than showing an empty list", async () => {
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

      expect(screen.getByText(/learned from this project's own completed alignments…/)).toBeInTheDocument();
    });

    it("reports suggestions as stale when the range moves under them", async () => {
      crossVersePropose.mockResolvedValue({
        chapter: "1", verses: ["1", "2", "3-4"], proposals: [proposal()],
        calibrationVersion: "cross-verse-uncalibrated-v1",
      });
      await renderPage("2");
      await suggest();
      expect(screen.queryByText(/range changed/i)).not.toBeInTheDocument();

      // Widen the range to verse 5 through the "To" picker.
      await fireEvent.change(screen.getByLabelText("To"), { target: { value: "5" } });
      await waitFor(() => expect(getAlignmentRange).toHaveBeenLastCalledWith("1", ["1", "2", "3-4", "5"]));

      expect(await screen.findByText(/The range changed since these were worked out/)).toBeInTheDocument();
    });
  });

  describe("AI cross-verse suggestions (#146)", () => {
    /** A proposal as `alignment.crossVerse.aiPropose` returns it. */
    function aiProposal(overrides: Record<string, unknown> = {}) {
      return {
        ...proposal(),
        confidence: 0.88,
        agreesWithCorpus: true,
        autoLinkable: true,
        evidence: [
          { kind: "MODEL_PICK", rawScore: 0.88, weight: 1, weightedScore: 0.88, modelConfidence: 88, reason: "v.2 renders it \"was\"" },
          { kind: "STRONGS_PRECEDENT", rawScore: 1, weight: 0.55, weightedScore: 0.55, jointCount: 7, sourceCount: 7 },
        ],
        ...overrides,
      };
    }

    async function clickAi() {
      await fireEvent.click(await screen.findByRole("button", { name: /Suggest with AI/ }));
    }

    it("sends nothing to a provider until the reviewer asks, exactly like the offline pass", async () => {
      await renderPage("2");
      // A billed request must never fire because a page opened or a range moved.
      expect(crossVerseAiPropose).not.toHaveBeenCalled();
      await clickAi();
      await waitFor(() => expect(crossVerseAiPropose).toHaveBeenCalledWith("1", ["1", "2", "3-4"]));
    });

    it("links a pair the AI and the corpus both chose, and records that the AI did it", async () => {
      crossVerseAiPropose.mockResolvedValue({
        chapter: "1", verses: ["1", "2", "3-4"], proposals: [aiProposal()], corpusProposals: [],
        calibrationVersion: "cross-verse-ai-uncalibrated-v1", modelConsulted: true,
      });
      crossVerseLink.mockResolvedValue(linked());
      await renderPage("2");
      await clickAi();

      await waitFor(() => expect(crossVerseLink).toHaveBeenCalledTimes(1));
      expect(crossVerseLink).toHaveBeenCalledWith(
        { chapter: "1", verse: "3-4", topId: "H001" },
        { chapter: "1", verse: "2", bottomId: "T002" },
        "ai-auto",
      );
      expect(await screen.findByText(/Linked 1 pair/)).toBeInTheDocument();
    });

    it("never links a proposal the corpus does not corroborate, however sure the AI is", async () => {
      crossVerseAiPropose.mockResolvedValue({
        chapter: "1", verses: ["1", "2", "3-4"],
        proposals: [aiProposal({ confidence: 0.99, agreesWithCorpus: false, autoLinkable: false, status: "AMBIGUOUS" })],
        corpusProposals: [], calibrationVersion: "cross-verse-ai-uncalibrated-v1", modelConsulted: true,
      });
      await renderPage("2");
      await clickAi();

      await waitFor(() => expect(screen.getByText("AI only")).toBeInTheDocument());
      expect(crossVerseLink).not.toHaveBeenCalled();
    });

    it("shows the AI's own reason, so a reviewer who reads no Greek can check it", async () => {
      crossVerseAiPropose.mockResolvedValue({
        chapter: "1", verses: ["1", "2", "3-4"],
        proposals: [aiProposal({ autoLinkable: false, agreesWithCorpus: false })],
        corpusProposals: [], calibrationVersion: "cross-verse-ai-uncalibrated-v1", modelConsulted: true,
      });
      await renderPage("2");
      await clickAi();

      expect(await screen.findByText(/the AI says: v\.2 renders it "was"/)).toBeInTheDocument();
    });

    it("keeps the corpus suggestions when the model returned none", async () => {
      crossVerseAiPropose.mockResolvedValue({
        chapter: "1", verses: ["1", "2", "3-4"], proposals: [], corpusProposals: [proposal()],
        calibrationVersion: "cross-verse-ai-uncalibrated-v1", modelConsulted: true,
      });
      await renderPage("2");
      await clickAi();

      // Otherwise asking the AI would be strictly worse than not asking.
      expect(await screen.findByText(/1 suggestion/)).toBeInTheDocument();
      expect(crossVerseLink).not.toHaveBeenCalled();
    });

    it("stops at the first refused link rather than half-applying the batch", async () => {
      crossVerseAiPropose.mockResolvedValue({
        chapter: "1", verses: ["1", "2", "3-4"],
        proposals: [
          aiProposal(),
          aiProposal({ source: { chapter: "1", verse: "3-4", topId: "H002", word: "x", signature: "x", strong: "", lemma: "" } }),
        ],
        corpusProposals: [], calibrationVersion: "cross-verse-ai-uncalibrated-v1", modelConsulted: true,
      });
      crossVerseLink.mockRejectedValue(new Error("was is already aligned within 1:2."));
      await renderPage("2");
      await clickAi();

      await waitFor(() => expect(screen.getByText(/already aligned within 1:2/)).toBeInTheDocument());
      expect(crossVerseLink).toHaveBeenCalledTimes(1);
    });

    it("offers no AI button at all when no API key is configured", async () => {
      getSettings.mockResolvedValue({ hasApiKey: false });
      await renderPage("2");

      await waitFor(() => expect(screen.getByText(/add an API key in Settings/)).toBeInTheDocument());
      expect(screen.queryByRole("button", { name: /Suggest with AI/ })).not.toBeInTheDocument();
      // The offline pass is unaffected: it needs no key and stays the default.
      expect(screen.getByRole("button", { name: /Suggest links/ })).toBeInTheDocument();
    });

    it("reports an unavailable provider instead of failing the page", async () => {
      crossVerseAiPropose.mockResolvedValue({
        chapter: "1", verses: ["1", "2", "3-4"], proposals: [],
        calibrationVersion: "cross-verse-ai-uncalibrated-v1",
        unavailable: { reason: "no-api-key", message: "No OpenAI-compatible API key is configured." },
      });
      await renderPage("2");
      await clickAi();

      expect(await screen.findByText(/No OpenAI-compatible API key is configured/)).toBeInTheDocument();
      expect(crossVerseLink).not.toHaveBeenCalled();
    });
  });
});
