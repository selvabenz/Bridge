import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";

import AlignmentModal from "../AlignmentModal.svelte";
// @ts-expect-error Vite's test-only raw loader is not part of the app tsconfig.
import alignmentModalSource from "../AlignmentModal.svelte?raw";
import { project } from "../../stores";
import type { AlignmentContext, AlignmentToken, ProjectInfo } from "../../types/finding";

const {
  getAlignment, realignWords, unalignWords, undoAlignment, restoreAlignment, runVerseChecks, getLexiconEntry,
} = vi.hoisted(() => ({
  getAlignment: vi.fn(),
  realignWords: vi.fn(),
  unalignWords: vi.fn(),
  undoAlignment: vi.fn(),
  restoreAlignment: vi.fn(),
  runVerseChecks: vi.fn(),
  getLexiconEntry: vi.fn(),
}));

vi.mock("../../api/bridgeClient", () => ({
  bridge: {
    getAlignment, realignWords, unalignWords, undoAlignment, restoreAlignment, runVerseChecks, getLexiconEntry,
  },
}));

function token(id: string, word: string, extra: Partial<AlignmentToken> = {}): AlignmentToken {
  return { id, word, occurrence: 1, occurrences: 1, ...extra };
}

/** Genesis 1:1-shaped: two source words, three target words, one pair aligned. */
function context(extra: Partial<AlignmentContext> = {}): AlignmentContext {
  const topTokens = [token("H001", "בְּרֵאשִׁית", { lemma: "רֵאשִׁית" }), token("H002", "בָּרָא")];
  const bottomTokens = [token("T001", "आदि"), token("T002", "में"), token("T003", "सृष्टि")];
  return {
    chapter: "1", verse: "1",
    alignment: {
      alignments: [
        { topWords: [topTokens[0]], bottomWords: [bottomTokens[0]] },
        { topWords: [topTokens[1]], bottomWords: [] },
      ],
      wordBank: [bottomTokens[1], bottomTokens[2]],
    },
    topTokens,
    bottomTokens,
    groups: [
      { id: "G001", topIds: ["H001"], bottomIds: ["T001"] },
      { id: "G002", topIds: ["H002"], bottomIds: [] },
    ],
    status: "partial",
    completionState: "pending",
    sourceAvailable: true, sourceMessage: "",
    sourceDirection: "rtl", targetDirection: "ltr",
    issues: [], canComplete: false, history: [],
    // 0 + 3 + 28 = 31, Genesis 1's verse count -- the tally is per verse.
    chapterStatus: { complete: 0, partial: 3, untouched: 28, invalid: 0 },
    gaps: { sourceUnmatched: 1, targetUnmatched: 2 },
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
    ...extra,
  };
}

function projectInfo(extra: Partial<ProjectInfo> = {}): ProjectInfo {
  return {
    path: "C:/projects/gen", bookId: "gen", bookName: "Genesis", chapters: ["1"], ...extra,
  } as ProjectInfo;
}

/** Renders and waits out the async load in onMount. */
async function open(ctx: AlignmentContext = context()) {
  getAlignment.mockResolvedValue(ctx);
  const result = render(AlignmentModal, { props: { chapter: "1", verse: "1", onClose: vi.fn() } });
  await screen.findByText(/align source and target words/);
  return result;
}

describe("AlignmentModal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    project.set(projectInfo());
    getLexiconEntry.mockResolvedValue({ segments: [] });
    runVerseChecks.mockResolvedValue([]);
  });

  it("names the book and its code in the title", async () => {
    await open();
    expect(screen.getByRole("heading", { name: /^Genesis \(GEN\) 1:1 — align source and target words$/ }))
      .toBeInTheDocument();
  });

  it("does not repeat the book id as its own name", async () => {
    // bookName falls back to the raw book id when the tC manifest has no
    // project.name, and "gen (GEN)" would be nonsense.
    project.set(projectInfo({ bookName: "gen" }));
    await open();
    expect(screen.getByRole("heading", { name: /^GEN 1:1 — align source and target words$/ })).toBeInTheDocument();
  });

  it("says the tally counts this chapter's verses", async () => {
    await open();
    const tally = screen.getByText(/31 verses/);
    expect(tally).toHaveTextContent("Chapter 1 — 31 verses:");
    expect(tally).toHaveTextContent("0 complete");
    expect(tally).toHaveTextContent("3 partial");
    expect(tally).toHaveTextContent("28 untouched");
  });

  it("shows the unaligned count as a chip, not a banner", async () => {
    await open();
    expect(screen.getByText("2 remaining")).toBeInTheDocument();
    expect(screen.queryByText(/Not fully aligned/)).not.toBeInTheDocument();
  });

  it("shows cross-verse linked words as their own chip", async () => {
    await open(context({ crossVerseAccounted: 2, crossVerseRealized: 0, crossVerseAccountedIds: ["T002", "T003"] }));
    const chip = screen.getByText(/2 linked across verses/);
    expect(chip).toHaveAttribute("title", expect.stringContaining("completion stays with translationCore"));
  });

  it("headings name both word banks", async () => {
    await open();
    expect(screen.getByText("Source words")).toBeInTheDocument();
    expect(screen.getByText("Target words")).toBeInTheDocument();
  });

  it("keeps structural issues behind a button until asked for", async () => {
    const issue = "2 alignment group(s) contain non-adjacent target words; aligned USFM requires each target span to be contiguous.";
    await open(context({ issues: [issue] }));

    const button = screen.getByRole("button", { name: "⚠ 1 issue" });
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText(`⚠ ${issue}`)).not.toBeInTheDocument();

    await fireEvent.click(button);
    expect(screen.getByText(`⚠ ${issue}`)).toBeInTheDocument();
    expect(button).toHaveAttribute("aria-expanded", "true");

    await fireEvent.click(button);
    expect(screen.queryByText(`⚠ ${issue}`)).not.toBeInTheDocument();
  });

  it("reveals the issues on hover too", async () => {
    const issue = "1 target token(s) occur more than once in alignment data.";
    await open(context({ issues: [issue] }));
    const wrap = screen.getByRole("button", { name: "⚠ 1 issue" }).parentElement!;

    await fireEvent.mouseEnter(wrap);
    expect(screen.getByText(`⚠ ${issue}`)).toBeInTheDocument();
    await fireEvent.mouseLeave(wrap);
    expect(screen.queryByText(`⚠ ${issue}`)).not.toBeInTheDocument();
  });

  it("calls the invalid flag what it is", async () => {
    // #149: this is the translationCore marker, not the issues list.
    await open(context({ status: "invalid" }));
    expect(screen.getByText("⚑ Marked invalid in translationCore.")).toBeInTheDocument();
  });

  it("still aligns a word bank item into a column by click", async () => {
    const ctx = context();
    realignWords.mockResolvedValue(ctx);
    await open(ctx);

    await fireEvent.click(screen.getByRole("button", { name: "में" }));
    await fireEvent.click(screen.getByLabelText("Align picked-up word to בָּרָא"));

    await waitFor(() => expect(realignWords).toHaveBeenCalledWith("1", "1", ["H002"], ["T002"], ctx.alignment));
  });
});

/** The text of one CSS rule block, by selector, from a component's source. */
function rule(source: string, selector: string): string {
  const match = source.match(new RegExp(`\\${selector}\\s*{([^}]*)}`));
  return match?.[1] ?? "";
}

describe("AlignmentModal layout", () => {
  // jsdom neither lays out nor paints, so this asserts on the rules themselves
  // (the pattern QaFindingDetail.test.ts uses). It guards #72: the interlinear
  // wraps instead of scrolling sideways, and that was not bought by letting the
  // columns squash.
  it("wraps the interlinear instead of scrolling it sideways", () => {
    const interlinear = rule(alignmentModalSource, ".interlinear");
    expect(interlinear).toMatch(/flex-wrap:\s*wrap/);
    expect(interlinear).toMatch(/overflow-x:\s*hidden/);
    expect(interlinear).not.toMatch(/overflow-x:\s*(auto|scroll)/);
  });

  it("caps the wrapped block so the word bank stays on screen", () => {
    const interlinear = rule(alignmentModalSource, ".interlinear");
    expect(interlinear).toMatch(/max-height:/);
    expect(interlinear).toMatch(/overflow-y:\s*auto/);
  });

  it("keeps the columns unsqueezable", () => {
    expect(rule(alignmentModalSource, ".column")).toMatch(/flex-shrink:\s*0/);
  });
});
