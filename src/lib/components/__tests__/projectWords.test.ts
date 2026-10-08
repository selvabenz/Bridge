import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { get } from "svelte/store";

const { languageQaWordsAdd, languageQaOccurrences } = vi.hoisted(() => ({
  languageQaWordsAdd: vi.fn(),
  languageQaOccurrences: vi.fn(),
}));
vi.mock("../../api/bridgeClient", () => ({ bridge: { languageQaWordsAdd, languageQaOccurrences } }));

import OccurrencesPopup from "../OccurrencesPopup.svelte";
import { addProjectWords, canAddProjectWord, isKnownMisspellingRule } from "../../projectWords";
import { languageQaFindingsByVerse, project } from "../../stores";
import { lqaFinding } from "./languageQaFixture";

describe("project words", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    project.set({ path: "/p", bookId: "act" } as never);
  });

  it("recognises a known-misspelling rule whatever its pack", () => {
    expect(isKnownMisspellingRule("hi-irv/hi.lex.known-misspelling")).toBe(true);
    expect(isKnownMisspellingRule("ta-irv/lexicon.known-misspelling")).toBe(true);
    expect(isKnownMisspellingRule("hi-irv/hi.shape.errors")).toBe(false);
  });

  it("drops the word's spelling findings at once, keeps a grammar lead, and nudges the engine", async () => {
    languageQaWordsAdd.mockResolvedValue({ entries: [], count: 1 });
    const typo = lqaFinding({ id: "t", category: "typo", originalText: "राज्यपाल" });
    const grammar = lqaFinding({ id: "g", category: "grammar", originalText: "राज्यपाल" });
    languageQaFindingsByVerse.set({ "1:1": [typo, grammar] });
    expect(await addProjectWords(["राज्यपाल"])).toBe("");
    expect(get(languageQaFindingsByVerse)["1:1"].map((f) => f.id)).toEqual(["g"]);
    expect(languageQaWordsAdd).toHaveBeenCalledWith("/p", ["राज्यपाल"], "book");
  });

  it("puts the findings back when the engine refuses", async () => {
    languageQaWordsAdd.mockRejectedValue(new Error("no"));
    const typo = lqaFinding({ id: "t", category: "typo", originalText: "x" });
    languageQaFindingsByVerse.set({ "1:1": [typo] });
    expect(await addProjectWords(["x"])).toBe("no");
    expect(get(languageQaFindingsByVerse)["1:1"].map((f) => f.id)).toEqual(["t"]);
  });
});

describe("OccurrencesPopup", () => {
  it("lists every place with the word marked, opens an IRV verse, and says when the list is cut", async () => {
    languageQaOccurrences.mockResolvedValue({
      word: "सताईस", source: "irv", ready: true, total: 3, truncated: true,
      hits: [{ book: "gen", chapter: "2", verse: "1", start: 3, end: 8, snippet: "और सताईस लोग", snippetStart: 3, snippetEnd: 8 }],
    });
    const onNavigate = vi.fn();
    const onClose = vi.fn();
    render(OccurrencesPopup, { props: { projectPath: "/p", word: "सताईस", onNavigate, onClose } });
    expect(await screen.findByText(/3 places, the first 1 shown/)).toBeTruthy();
    expect(document.querySelector(".snippet b")?.textContent).toBe("सताईस");
    await fireEvent.click(screen.getByRole("button", { name: "GEN 2:1" }));
    expect(onNavigate).toHaveBeenCalledWith("gen", "2", "1");
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });
});

describe("canAddProjectWord: where Add would silence the finding", () => {
  it.each([
    [{ category: "typo", ruleId: "hi-irv/hi.lex.unknown", originalText: "मनूष्य" }, true],
    [{ category: "consistency", ruleId: "ml-irv/ml.lex.consistency", originalText: "x" }, true],
    [{ category: "learned", ruleId: "project/learned.replacement", originalText: "x" }, false],
    [{ category: "unicode", ruleId: "ta-irv/indicqa.shape.malformed", originalText: "x" }, false],
    [{ category: "word-joining", ruleId: "ta-irv/indicqa.lex.compound", originalText: "x" }, false],
    [{ category: "typo", ruleId: "hi-irv/hi.lex.known-misspelling", originalText: "x" }, false],
    [{ category: "typo", ruleId: "hi-irv/hi.lex.unknown", originalText: "two words" }, false],
  ])("%o -> %s", (finding, expected) => {
    expect(canAddProjectWord(finding as Parameters<typeof canAddProjectWord>[0])).toBe(expected);
  });
});

describe("OccurrencesPopup, Search in this book", () => {
  it("searches any run of the text and says so", async () => {
    languageQaOccurrences.mockResolvedValue({
      word: "सताईस दिन", source: "irv", match: "text", ready: true, total: 1, truncated: false,
      hits: [{ book: "gen", chapter: "1", verse: "2", start: 3, end: 12, snippet: "वे सताईस दिन रहे।", snippetStart: 3, snippetEnd: 12 }],
    });
    render(OccurrencesPopup, { props: { projectPath: "/p", word: "सताईस दिन", match: "text",
      onNavigate: vi.fn(), onClose: vi.fn() } });
    expect(await screen.findByRole("heading", { name: /सताईस दिन anywhere in this book/ })).toBeTruthy();
    expect(languageQaOccurrences).toHaveBeenCalledWith("/p", "सताईस दिन", "irv", 200, "text");
  });
});
