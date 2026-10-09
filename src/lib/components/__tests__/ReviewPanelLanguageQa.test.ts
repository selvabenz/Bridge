import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";

// Layered-rules Phase 4.3: the review panel lists the selected verse's
// Language QA findings (inline or not) with the same actions as the verse
// menu, so the Language QA panel is not the only place to act on one.
const { languageQaVerse, decideVerse, known } = vi.hoisted(() => ({
  languageQaVerse: vi.fn(),
  decideVerse: vi.fn(),
  known: {} as Record<string, unknown>,
}));

vi.mock("../../api/bridgeClient", () => ({
  // Everything else the panel calls on mount resolves to an empty answer.
  bridge: new Proxy(known, {
    get: (target, name: string) => {
      if (name === "languageQaVerse") return languageQaVerse;
      if (name === "decideVerse") return decideVerse;
      if (name === "runVerseChecks") return vi.fn().mockResolvedValue([]);
      if (!(name in target)) target[name] = vi.fn().mockResolvedValue({ items: [] });
      return target[name];
    },
  }),
}));

import ReviewPanel from "../ReviewPanel.svelte";
import { currentChapter, project, selectedVerse, verseTexts, verseKey } from "../../stores";
import { languageQaChannel } from "../../languageQaInline";
import { reviewPanelCollapsed } from "../../editorPrefs";
import { get } from "svelte/store";
import { lqaFinding } from "./languageQaFixture";
import { scopeDialog } from "../../scopedApply";
import type { LanguageQaStatus } from "../../types/languageQa";

function channel(generation: number, state: LanguageQaStatus["state"] = "completed"): void {
  languageQaChannel.set({
    projectPath: "/p", error: "",
    status: { projectPath: "/p", state, generation } as unknown as LanguageQaStatus,
  });
}

describe("ReviewPanel Language QA tab", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    project.set({ path: "/p", bookId: "php" } as never);
    currentChapter.set("1");
    verseTexts.set({ [verseKey("1", "6")]: "அந்த காகம் பறந்தது." });
    decideVerse.mockResolvedValue({});
    languageQaVerse.mockResolvedValue({
      projectPath: "/p", generation: 3, state: "completed", chapter: "1", verse: "6",
      findings: [lqaFinding({ id: "lqa-p", rule: "tamil.wordlist-variant", inline: false })],
      hidden: [{ ...lqaFinding({ id: "lqa-h" }), decision: "ignored" }],
    });
    channel(3);
    selectedVerse.set("6");
  });

  afterEach(() => {
    selectedVerse.set(null);
    languageQaChannel.set({ projectPath: "", status: null, error: "" });
  });

  it("lists the verse's findings, panel-only ones included, and ignores one before the engine answers", async () => {
    render(ReviewPanel);
    await waitFor(() => expect(languageQaVerse).toHaveBeenCalledWith("/p", "1", "6"));
    await fireEvent.click(screen.getByRole("tab", { name: /Language QA/ }));
    expect(await screen.findByText("ta-irv/tamil.wordlist-variant")).toBeInTheDocument();
    expect(screen.getByText(/Decided \(1\)/)).toBeInTheDocument();
    let release: (value: unknown) => void = () => {};
    decideVerse.mockReturnValue(new Promise((resolve) => { release = resolve; }));
    await fireEvent.click(screen.getByRole("button", { name: "⊘ Ignore" }));
    // Gone from the list at once; the decision is sent with the Language QA issue.
    expect(document.querySelector('[data-lqa-id="lqa-p"]')).toBeNull();
    expect(decideVerse).toHaveBeenCalledWith("1", "6", "lqa-p", "ignored", undefined,
      expect.objectContaining({ source: "languageQa" }));
    release({});
  });

  it("labels a learned fix and Forget drops every recurrence without recording a decision", async () => {
    const learned = (id: string, start: number) => lqaFinding({
      id, rule: "learned.replacement", category: "learned", layer: "housestyle", originalText: "தேவன்",
      start, end: start + 5, suggestions: [{ text: "கர்த்தர்", rank: 1, source: "learned", rationale: "Replaced with this 2× before." }],
    });
    languageQaVerse.mockResolvedValue({
      projectPath: "/p", generation: 3, state: "completed", chapter: "1", verse: "6",
      findings: [learned("l1", 0), learned("l2", 10)], hidden: [],
    });
    render(ReviewPanel);
    await fireEvent.click(screen.getByRole("tab", { name: /Language QA/ }));
    expect((await screen.findAllByText("learned fix", { selector: ".engine-badge" })).length).toBe(2);
    await fireEvent.click(screen.getAllByRole("button", { name: "Forget this fix" })[0]);
    expect(document.querySelector('[data-lqa-id="l1"]')).toBeNull();
    expect(document.querySelector('[data-lqa-id="l2"]')).toBeNull();
    // The Proxy creates the stub on the panel's first call, so read it afterwards.
    await waitFor(() => expect(known.languageQaLearnedForget).toHaveBeenCalledWith("/p", "தேவன்", "கர்த்தர்"));
    expect(decideVerse).not.toHaveBeenCalled();
  });

  it("Apply to: Chapter asks for every place first instead of writing this verse", async () => {
    languageQaVerse.mockResolvedValue({
      projectPath: "/p", generation: 3, state: "completed", chapter: "1", verse: "6",
      findings: [lqaFinding({ id: "lqa-s" })], hidden: [],
    });
    render(ReviewPanel);
    await fireEvent.click(screen.getByRole("tab", { name: /Language QA/ }));
    await fireEvent.click(await screen.findByRole("radio", { name: "Chapter" }));
    expect(screen.getByRole("radio", { name: "Chapter" })).toHaveAttribute("aria-checked", "true");
    await fireEvent.click(screen.getByRole("button", { name: /^Use / }));
    await waitFor(() => expect(known.languageQaScopeFind).toHaveBeenCalledWith(
      "/p", "1", "6", "lqa-s", "chapter", "அந்தக் காகம்"));
    expect(decideVerse).not.toHaveBeenCalled();
    // Nothing is removed or written until the reviewer confirms.
    expect(document.querySelector('[data-lqa-id="lqa-s"]')).not.toBeNull();
    scopeDialog.set(null);
  });

  it("refetches when a new completed pass lands, not on every status tick", async () => {
    render(ReviewPanel);
    await waitFor(() => expect(languageQaVerse).toHaveBeenCalledTimes(1));
    channel(3);
    channel(4, "running");
    await Promise.resolve();
    expect(languageQaVerse).toHaveBeenCalledTimes(1);
    channel(4);
    await waitFor(() => expect(languageQaVerse).toHaveBeenCalledTimes(2));
  });

  it("puts a finding back when recording its decision fails", async () => {
    decideVerse.mockRejectedValue(new Error("disk full"));
    render(ReviewPanel);
    await fireEvent.click(screen.getByRole("tab", { name: /Language QA/ }));
    await screen.findByText("ta-irv/tamil.wordlist-variant");
    await fireEvent.click(screen.getByRole("button", { name: "False positive" }));
    expect(await screen.findByText("disk full")).toBeInTheDocument();
    expect(document.querySelector('[data-lqa-id="lqa-p"]')).not.toBeNull();
  });
});

describe("ReviewPanel folding (#236)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    project.set({ path: "/p", bookId: "php" } as never);
    currentChapter.set("1");
    verseTexts.set({ [verseKey("1", "6")]: "அந்த காகம் பறந்தது." });
    languageQaVerse.mockResolvedValue({
      projectPath: "/p", generation: 3, state: "completed", chapter: "1", verse: "6",
      findings: [lqaFinding({ id: "lqa-p", rule: "tamil.wordlist-variant", inline: false })], hidden: [],
    });
    channel(3);
    selectedVerse.set("6");
  });

  afterEach(() => {
    reviewPanelCollapsed.set(false);
    selectedVerse.set(null);
    languageQaChannel.set({ projectPath: "", status: null, error: "" });
  });

  it("folds to a strip that keeps its contents mounted, and remembers it", async () => {
    render(ReviewPanel);
    await fireEvent.click(screen.getByRole("tab", { name: /Language QA/ }));
    await screen.findByText("ta-irv/tamil.wordlist-variant");

    await fireEvent.click(screen.getByRole("button", { name: "Hide the review panel" }));
    expect(get(reviewPanelCollapsed)).toBe(true);
    expect(localStorage.getItem("bridge.editor.reviewPanelCollapsed.v1")).toBe("true");
    expect(document.querySelector(".panel-body")?.classList.contains("folded")).toBe(true);
    // Folded, not unmounted: the finding and the open tab are still there.
    expect(document.querySelector('[data-lqa-id="lqa-p"]')).not.toBeNull();

    await fireEvent.click(screen.getByRole("button", { name: "Show the review panel" }));
    expect(document.querySelector(".panel-body")?.classList.contains("folded")).toBe(false);
    expect(screen.getByRole("tab", { name: /Language QA/ }).getAttribute("aria-selected")).toBe("true");
  });
});
