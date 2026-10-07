import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { get } from "svelte/store";
import type { LanguageQaFinding, LanguageQaStatus } from "../../types/languageQa";
import { languageQaFindingsByVerse } from "../../stores";
import { lqaFinding } from "./languageQaFixture";

const statusCall = vi.fn();
const pauseCall = vi.fn();
const historyCall = vi.fn();
vi.mock("../../api/bridgeClient", () => ({ bridge: {
  languageQaStatus: (...args: unknown[]) => statusCall(...args),
  languageQaPause: (...args: unknown[]) => pauseCall(...args),
  languageQaHistory: (...args: unknown[]) => historyCall(...args),
} }));
import LanguageQaPanel from "../LanguageQaPanel.svelte";
import { languageQaChannel } from "../../languageQaInline";

function snapshot(overrides: Partial<LanguageQaStatus> = {}): LanguageQaStatus {
  return {
    projectPath: "C:/project", book: "php", generation: 1, state: "completed",
    ruleVersion: "language-qa-1", totalFindings: 1, offset: 0,
    completedChapters: 1, totalChapters: 1, limitations: [],
    coverage: {
      inScope: [{ category: "sandhi", label: "Sandhi", labelTa: "சந்திப் பிழைகள்" }],
      outOfScope: [{ category: "agreement", label: "Agreement", labelTa: "திணை, பால், எண் இயைபு",
        reason: "Checked in the Round 2 review.", reasonTa: "இரண்டாம் சுற்றில்." }],
      handOff: "docs/LANGUAGE_QA_REVIEW_HANDOFF.md",
      summary: "Technical checks; no grammar certification.",
    },
    storage: "Session results.",
    language: { declared: "tam", language: "tam", script: "TAMIL", basis: "metadata",
      pack: "ta-irv", message: "Tamil character rules available." },
    findings: [lqaFinding({ id: "f1", chapter: "2", verse: "3-4", rule: "unicode.corruption",
      severity: "high", start: 0, end: 1, originalText: "�", message: "Check source encoding.",
      suggestedReplacement: null })],
    ...overrides,
  };
}

/** What languageQaInline.ts's status channel publishes: count-only status. */
function publish(overrides: Partial<LanguageQaStatus> = {}, error = ""): void {
  languageQaChannel.set({ projectPath: "C:/project", status: snapshot({ findings: [], ...overrides }), error });
}

beforeEach(() => {
  // statusCall now serves only the panel's own page requests (limit 50).
  statusCall.mockReset().mockImplementation(async (_path, _offset, limit) =>
    snapshot({ findings: limit ? snapshot().findings : [] }));
  pauseCall.mockReset().mockResolvedValue(snapshot({ state: "paused", findings: [] }));
  publish();
});

afterEach(() => languageQaChannel.set({ projectPath: "", status: null, error: "" }));

describe("Language QA", () => {
  it("stays collapsed and makes no request of its own while closed", async () => {
    // The status channel (languageQaInline.ts) supplies state and totals; the
    // panel no longer polls.
    render(LanguageQaPanel, { projectPath: "C:/project", onNavigate: vi.fn() });
    expect(await screen.findByRole("button", { name: /Language QA · completed · 1/ })).toBeTruthy();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(statusCall).not.toHaveBeenCalled();
    expect(screen.queryByRole("region", { name: "Language QA results" })).toBeNull();
    expect(screen.queryByText("Check source encoding.")).toBeNull();
  });

  it("refetches the open page when a new pass lands on the channel, and not otherwise", async () => {
    render(LanguageQaPanel, { projectPath: "C:/project", onNavigate: vi.fn() });
    await fireEvent.click(await screen.findByRole("button", { name: /Language QA · completed/ }));
    await screen.findByText("Check source encoding.");
    expect(statusCall).toHaveBeenCalledTimes(1);
    publish();  // same generation and state: nothing new to show
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(statusCall).toHaveBeenCalledTimes(1);
    publish({ generation: 2 });
    await waitFor(() => expect(statusCall).toHaveBeenCalledTimes(2));
  });

  it("loads findings on demand and navigates exact verse bridges", async () => {
    const navigate = vi.fn();
    render(LanguageQaPanel, { projectPath: "C:/project", onNavigate: navigate });
    await screen.findByRole("button", { name: /Language QA · completed/ });
    await fireEvent.click(screen.getByRole("button", { name: /Language QA · completed/ }));
    await screen.findByText("Check source encoding.");
    await fireEvent.click(screen.getByRole("button", { name: "PHP 2:3-4" }));
    expect(navigate).toHaveBeenCalledWith("php", "2", "3-4");
    expect(statusCall).toHaveBeenLastCalledWith("C:/project", 0, 50, "findings");
  });

  it("shows incomplete coverage instead of claiming a clean publication", async () => {
    const incomplete = { totalFindings: 0, findings: [], incomplete: true,
      limitations: ["Chapter 1: 4: Unbalanced \\f: 1 open, 0 close; verse not checked."] };
    publish(incomplete);
    statusCall.mockResolvedValue(snapshot(incomplete));
    render(LanguageQaPanel, { projectPath: "C:/project", onNavigate: vi.fn() });
    await fireEvent.click(await screen.findByRole("button", { name: /Language QA · completed/ }));
    expect(screen.getByText(/Coverage incomplete/)).toBeTruthy();
    expect(screen.getByText(/not publication approval/)).toBeTruthy();
  });

  it("names a profile pack's language and pack, with no empty Tamil label", async () => {
    const hindi = {
      totalFindings: 0, findings: [],
      language: { declared: "hin", language: "hi", name: "Hindi", script: "DEVANAGARI", basis: "metadata",
        pack: "hi-irv", message: "Hindi spelling, encoding, punctuation and consistency checks." },
      coverage: {
        inScope: [{ category: "consistency", label: "Spelling consistency", labelTa: "" }],
        outOfScope: [{ category: "clause-agreement", label: "Agreement across a whole clause", labelTa: "",
          reason: "Only corpus-attested pairs are flagged.", reasonTa: "" }],
        handOff: "docs/LANGUAGE_QA_REVIEW_HANDOFF.md", summary: "Technical checks.",
      },
    };
    publish(hindi);
    statusCall.mockResolvedValue(snapshot(hindi));
    render(LanguageQaPanel, { projectPath: "C:/project", onNavigate: vi.fn() });
    await fireEvent.click(await screen.findByRole("button", { name: /Language QA · completed/ }));
    const heading = screen.getByText(/hi-irv rules/, { selector: "p" });
    expect(heading.textContent).toMatch(/^\s*Hindi/);
    const scope = screen.getByLabelText("What Language QA checks");
    expect(scope.textContent).toContain("Spelling consistency");
    expect(scope.querySelector('span[lang="ta"]')).toBeNull();
    expect(scope.textContent).not.toContain("Tamil");
  });

  it("always states what it checks and what it does not, in Tamil and English", async () => {
    publish({ totalFindings: 0, findings: [] });
    statusCall.mockResolvedValue(snapshot({ totalFindings: 0, findings: [] }));
    render(LanguageQaPanel, { projectPath: "C:/project", onNavigate: vi.fn() });
    await fireEvent.click(await screen.findByRole("button", { name: /Language QA · completed/ }));
    const scope = screen.getByLabelText("What Language QA checks");
    expect(scope.textContent).toContain("Sandhi");
    expect(scope.textContent).toContain("சந்திப் பிழைகள்");
    expect(scope.textContent).toContain("Does not check");
    expect(scope.textContent).toContain("திணை, பால், எண் இயைபு");
    expect(scope.textContent).toContain("Checked in the Round 2 review.");
    expect(scope.textContent).toContain("docs/LANGUAGE_QA_REVIEW_HANDOFF.md");
    // Not behind a disclosure: visible whether or not there are findings.
    expect(scope.closest("details")).toBeNull();
  });

  it("pauses through the project-guarded endpoint", async () => {
    render(LanguageQaPanel, { projectPath: "C:/project", onNavigate: vi.fn() });
    await fireEvent.click(await screen.findByRole("button", { name: /Language QA · completed/ }));
    await screen.findByText("Check source encoding.");
    statusCall.mockResolvedValue(snapshot({ state: "paused", findings: [] }));
    await fireEvent.click(screen.getByRole("button", { name: "Pause checks" }));
    expect(pauseCall).toHaveBeenCalledWith("C:/project", true);
    await screen.findByRole("button", { name: "Resume checks" });
  });

  it("does not display a response belonging to a different project", async () => {
    statusCall.mockResolvedValue(snapshot({ projectPath: "C:/old-project" }));
    render(LanguageQaPanel, { projectPath: "C:/project", onNavigate: vi.fn() });
    await fireEvent.click(await screen.findByRole("button", { name: /Language QA/ }));
    await waitFor(() => expect(statusCall).toHaveBeenCalled());
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(screen.queryByText("Check source encoding.")).toBeNull();
    // Nor a channel status published for another project.
    languageQaChannel.set({ projectPath: "C:/old-project", status: snapshot(), error: "" });
    expect(await screen.findByRole("button", { name: /Language QA · starting/ })).toBeTruthy();
  });

  it("never writes the inline-marks store, whatever page it shows", async () => {
    // The panel used to fill languageQaFindingsByVerse from its own page, so
    // any inline finding past the first 100 in the book never got a mark.
    // The store now belongs to languageQaInline.ts; paging here must not touch it.
    const seeded: Record<string, LanguageQaFinding[]> = { "1:1": [lqaFinding({ id: "keep", chapter: "1",
      verse: "1", rule: "terminology.deprecated-form", start: 0, end: 3,
      originalText: "bad", message: "Deprecated.", suggestedReplacement: "good" })] };
    languageQaFindingsByVerse.set(seeded);
    statusCall.mockImplementation(async (_path, _offset, limit) => snapshot({
      totalFindings: 120,
      findings: limit ? [lqaFinding({ id: "f2", chapter: "3", verse: "9", rule: "terminology.deprecated-form",
        start: 0, end: 3, originalText: "bad", message: "Deprecated.", suggestedReplacement: "good" })] : [],
    }));
    render(LanguageQaPanel, { projectPath: "C:/project", onNavigate: vi.fn() });
    await fireEvent.click(await screen.findByRole("button", { name: /Language QA · completed/ }));
    await screen.findByText("Deprecated.");
    expect(get(languageQaFindingsByVerse)).toEqual(seeded);
  });

  it("lists re-check and false-positive findings separately, each paged by the engine", async () => {
    statusCall.mockImplementation(async (_path, _offset, limit, view) => snapshot({
      view, recheckCount: 1, falsePositiveCount: 2, totalFindings: view === "findings" ? 1 : view === "recheck" ? 1 : 2,
      findings: !limit ? [] : view === "recheck"
        ? [lqaFinding({ id: "r1", message: "Ignored before.", previouslyIgnored: true })]
        : view === "falsePositives"
          ? [lqaFinding({ id: "fp1", message: "Not a problem." }), lqaFinding({ id: "fp2", message: "Also fine." })]
          : snapshot().findings,
    }));
    render(LanguageQaPanel, { projectPath: "C:/project", onNavigate: vi.fn() });
    await fireEvent.click(await screen.findByRole("button", { name: /Language QA · completed/ }));
    await fireEvent.click(await screen.findByRole("tab", { name: "Re-check (1)" }));
    await screen.findByText("Ignored before.");
    expect(statusCall).toHaveBeenLastCalledWith("C:/project", 0, 50, "recheck");
    expect(screen.getByText("Re-check", { selector: ".recheck" })).toBeTruthy();
    await fireEvent.click(screen.getByRole("tab", { name: "False positives (2)" }));
    await screen.findByText("Also fine.");
    expect(statusCall).toHaveBeenLastCalledWith("C:/project", 0, 50, "falsePositives");
    expect(screen.queryByText("Ignored before.")).toBeNull();
  });

  it("loads a finding's decision history only when asked, behind a placeholder", async () => {
    let answer: (value: unknown) => void = () => {};
    historyCall.mockReset().mockReturnValue(new Promise((resolve) => { answer = resolve; }));
    render(LanguageQaPanel, { projectPath: "C:/project", onNavigate: vi.fn() });
    await fireEvent.click(await screen.findByRole("button", { name: /Language QA · completed/ }));
    await screen.findByText("Check source encoding.");
    expect(historyCall).not.toHaveBeenCalled();
    await fireEvent.click(screen.getByRole("button", { name: "History" }));
    expect(screen.getByText("Loading history…")).toBeTruthy();
    expect(historyCall).toHaveBeenCalledWith("C:/project", "2", "3-4", "f1");
    answer({ chapter: "2", verse: "3-4", findingId: "f1", entries: [
      { seq: 4, findingId: "f1", decision: "ignored", note: "", rule: "unicode.corruption", ruleId: "common/unicode.corruption",
        originalText: "�", chosenSuggestion: null, chosenRank: null, packVersion: "language-qa-7",
        recordedAt: "2026-09-24T10:00:00Z", revision: 1, actorId: "a" },
      { seq: 9, findingId: "f1", decision: "rejected", note: "", rule: "unicode.corruption", ruleId: "common/unicode.corruption",
        originalText: "�", chosenSuggestion: null, chosenRank: null, packVersion: "language-qa-7",
        recordedAt: "2026-09-24T11:00:00Z", revision: 2, actorId: "a" },
    ] });
    await screen.findByText("Marked as false positive");
    const items = screen.getByRole("list", { name: "Language QA decision history" }).querySelectorAll("li");
    expect(Array.from(items).map((li) => li.querySelector(".decision")?.textContent))
      .toEqual(["Ignored", "Marked as false positive"]);
  });

  it("surfaces the channel's failure", async () => {
    publish({}, "Engine unavailable");
    render(LanguageQaPanel, { projectPath: "C:/project", onNavigate: vi.fn() });
    await fireEvent.click(await screen.findByRole("button", { name: /Language QA · unavailable/ }));
    expect(screen.getByRole("alert").textContent).toContain("Engine unavailable");
  });
});
