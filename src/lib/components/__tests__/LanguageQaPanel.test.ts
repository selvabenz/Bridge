import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { get } from "svelte/store";
import type { LanguageQaFinding, LanguageQaStatus } from "../../types/languageQa";
import {
  activeLanguageQaFindingId, chapterVerseNums, currentChapter, languageQaFindingsByVerse, project, selectedVerse, verseKey,
} from "../../stores";
import { lqaFinding } from "./languageQaFixture";

const statusCall = vi.fn();
const pauseCall = vi.fn();
const historyCall = vi.fn();
const learnedListCall = vi.fn();
const learnedForgetCall = vi.fn();
const learnedRestoreCall = vi.fn();
const flagsListCall = vi.fn();
const flagUpdateCall = vi.fn();
const bookWordsCall = vi.fn();
const wordsAddCall = vi.fn();
const verseHistoryCall = vi.fn();
const verseCall = vi.fn();
vi.mock("../../api/bridgeClient", () => ({ bridge: {
  languageQaStatus: (...args: unknown[]) => statusCall(...args),
  languageQaPause: (...args: unknown[]) => pauseCall(...args),
  languageQaHistory: (...args: unknown[]) => historyCall(...args),
  languageQaLearnedList: (...args: unknown[]) => learnedListCall(...args),
  languageQaLearnedForget: (...args: unknown[]) => learnedForgetCall(...args),
  languageQaLearnedRestore: (...args: unknown[]) => learnedRestoreCall(...args),
  languageQaFlagsList: (...args: unknown[]) => flagsListCall(...args),
  languageQaFlagUpdate: (...args: unknown[]) => flagUpdateCall(...args),
  languageQaBookWords: (...args: unknown[]) => bookWordsCall(...args),
  languageQaWordsAdd: (...args: unknown[]) => wordsAddCall(...args),
  verseHistory: (...args: unknown[]) => verseHistoryCall(...args),
  languageQaVerse: (...args: unknown[]) => verseCall(...args),
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

  it("says when a finding is in a heading or a footnote, and shows the OV verse", async () => {
    const reference = { label: "OV 1957", ref: "MAT 18:15", text: "உன் சகோதரன் உனக்கு விரோதமாய்க் குற்றஞ்செய்தால்" };
    const findings = [
      lqaFinding({ id: "h1", chapter: "18", verse: "15", originalText: "விரோதமாக", message: "Heading lead.",
        context: "heading", contextText: "உனக்கு விரோதமாக குற்றம் செய்யும் சகோதரன்", suggestions: [],
        suggestedReplacement: null, reference }),
      lqaFinding({ id: "n1", chapter: "4", verse: "8", originalText: "கூட்டிசென்றான்", message: "Footnote lead.",
        context: "footnote" }),
    ];
    publish({ totalFindings: 2, findings });
    statusCall.mockResolvedValue(snapshot({ totalFindings: 2, findings }));
    render(LanguageQaPanel, { projectPath: "C:/project", onNavigate: vi.fn() });
    await fireEvent.click(await screen.findByRole("button", { name: /Language QA · completed/ }));
    await screen.findByText("Heading lead.");
    expect(screen.getByText("in heading")).toBeTruthy();
    expect(screen.getByText("in footnote")).toBeTruthy();
    expect(screen.getByText(/Heading: உனக்கு விரோதமாக/)).toBeTruthy();
    expect(screen.getByText(/OV 1957 MAT 18:15:/)).toBeTruthy();
    expect(screen.getByText(reference.text)).toBeTruthy();
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

  it("lists learned fixes only when the Dictionary tab opens, and forgets and restores one", async () => {
    const fix = { old: "தேவன்", new: "கர்த்தர்", count: 3, firstRef: "RUT 1:1", lastRef: "RUT 2:4", reviewer: "Benz",
      source: "edit" as const, enabled: true, createdAt: "t", updatedAt: "t" };
    learnedListCall.mockReset().mockResolvedValue({ fixes: [fix], enabled: true });
    learnedForgetCall.mockReset().mockResolvedValue({ fix: { ...fix, enabled: false } });
    learnedRestoreCall.mockReset().mockResolvedValue({ fix });
    render(LanguageQaPanel, { projectPath: "C:/project", onNavigate: vi.fn() });
    await fireEvent.click(await screen.findByRole("button", { name: /Language QA · completed/ }));
    await screen.findByText("Check source encoding.");
    expect(learnedListCall).not.toHaveBeenCalled();

    await fireEvent.click(screen.getByRole("tab", { name: "Dictionary" }));
    expect(await screen.findByText("கர்த்தர்")).toBeTruthy();
    expect(learnedListCall).toHaveBeenCalledWith("C:/project");
    expect(screen.getByText("RUT 2:4")).toBeTruthy();

    await fireEvent.click(screen.getByRole("button", { name: "Forget" }));
    await waitFor(() => expect(learnedForgetCall).toHaveBeenCalledWith("C:/project", "தேவன்", "கர்த்தர்"));
    await fireEvent.click(await screen.findByRole("button", { name: "Restore" }));
    await waitFor(() => expect(learnedRestoreCall).toHaveBeenCalledWith("C:/project", "தேவன்", "கர்த்தர்"));
    expect(await screen.findByRole("button", { name: "Forget" })).toBeTruthy();
  });

  it("lists the book's open flags in the Flags tab, navigates to one and resolves it", async () => {
    const flag = { flagId: "f1", chapter: "3", verse: "4", verseEnd: null, start: 0, end: 4, text: "அவன்", textHash: "h",
      type: "meaning" as const, note: "Is this the sense?", suggested: null, findingId: null, reviewer: "Benz",
      status: "open" as const, createdAt: "2026-10-07T10:00:00Z", updatedAt: "2026-10-07T10:00:00Z" };
    flagsListCall.mockReset().mockResolvedValue({ flags: [flag] });
    flagUpdateCall.mockReset().mockResolvedValue({ flag: { ...flag, status: "resolved" } });
    const onNavigate = vi.fn();
    render(LanguageQaPanel, { projectPath: "C:/project", onNavigate });
    await fireEvent.click(await screen.findByRole("button", { name: /Language QA · completed/ }));
    await fireEvent.click(screen.getByRole("tab", { name: "Flags" }));
    expect(await screen.findByText("Is this the sense?")).toBeTruthy();
    expect(flagsListCall).toHaveBeenCalledWith("C:/project");
    await fireEvent.click(screen.getByRole("button", { name: "PHP 3:4" }));
    expect(onNavigate).toHaveBeenCalledWith("php", "3", "4");
    await fireEvent.click(screen.getByRole("button", { name: "Resolve" }));
    await waitFor(() => expect(flagUpdateCall).toHaveBeenCalledWith("C:/project", "f1", { status: "resolved" }));
    await waitFor(() => expect(screen.queryByText("Is this the sense?")).toBeNull());
  });

  it("lists the book's edits by day in the Edits tab, with what changed, and navigates to one", async () => {
    const edit = (verse: string, timestamp: string, batchId: string | null = null) => ({
      chapter: "2", verse, timestamp, username: "Benz", verseBefore: "a", verseAfter: "b",
      plainBefore: "पुराना", plainAfter: "नया", tags: [], groupId: "", batchId, undoes: null });
    verseHistoryCall.mockReset().mockResolvedValue({ total: 2, truncated: false, entries: [
      edit("5", "2026-10-07T10:00:00.000Z", "b1"), edit("4", "2026-10-01T10:00:00.000Z")] });
    const onNavigate = vi.fn();
    render(LanguageQaPanel, { projectPath: "C:/project", onNavigate });
    await fireEvent.click(await screen.findByRole("button", { name: /Language QA · completed/ }));
    await fireEvent.click(screen.getByRole("tab", { name: "Edits" }));
    const section = await screen.findByRole("region", { name: "Edits" });
    await waitFor(() => expect(section.querySelectorAll("h3")).toHaveLength(2));
    expect(verseHistoryCall).toHaveBeenCalledWith();
    expect(section).toHaveTextContent("change set");
    expect(section.querySelector("ins")?.textContent).toBeTruthy();
    await fireEvent.click(screen.getByRole("button", { name: "PHP 2:4" }));
    expect(onNavigate).toHaveBeenCalledWith("php", "2", "4");
  });

  it("Book words ticks the words the IRV uses often and adds them to the project word list", async () => {
    const row = (word: string, countIrv: number) => ({ word, status: "irv_ok" as const, countBook: 2, countIrv, countOv: 0,
      firstRef: { chapter: "1", verse: "1" } });
    bookWordsCall.mockReset().mockResolvedValue({ ready: true, total: 2, words: [row("राज्यपाल", 25), row("सोची", 2)] });
    wordsAddCall.mockReset().mockResolvedValue({ entries: [], count: 1 });
    render(LanguageQaPanel, { projectPath: "C:/project", onNavigate: vi.fn() });
    await fireEvent.click(await screen.findByRole("button", { name: /Language QA · completed/ }));
    await fireEvent.click(screen.getByRole("tab", { name: "Book words" }));
    expect(await screen.findByText("राज्यपाल")).toBeTruthy();
    await fireEvent.click(screen.getByRole("button", { name: "Tick" }));
    expect((screen.getByRole("checkbox", { name: "Tick राज्यपाल" }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole("checkbox", { name: "Tick सोची" }) as HTMLInputElement).checked).toBe(false);
    await fireEvent.click(screen.getByRole("button", { name: "Add to the project word list" }));
    await waitFor(() => expect(wordsAddCall).toHaveBeenCalledWith("C:/project", ["राज्यपाल"], "book"));
    expect(await screen.findByText(/1 word added to the project word list/)).toBeTruthy();
    expect(screen.getByText("in word list")).toBeTruthy();
  });
});

describe("Language QA panel: kinds, scope and F8", () => {
  afterEach(() => {
    project.set(null);
    activeLanguageQaFindingId.set(null);
    languageQaFindingsByVerse.set({});
    chapterVerseNums.set({});
    selectedVerse.set(null);
  });

  async function openPanel(onNavigate = vi.fn()) {
    render(LanguageQaPanel, { projectPath: "C:/project", onNavigate });
    await fireEvent.click(await screen.findByRole("button", { name: /Language QA · completed/ }));
    await screen.findByText("Check source encoding.");
    return onNavigate;
  }

  it("shows each kind with its count and narrows the list to the kinds ticked", async () => {
    statusCall.mockImplementation(async (_path, _offset, limit) =>
      snapshot({ findings: limit ? snapshot().findings : [], categoryCounts: { typo: 4, unicode: 1 } }));
    await openPanel();
    const legend = screen.getByRole("group", { name: "Kinds of finding" });
    expect(legend).toHaveTextContent(/Possible typo\s*4/);
    const typo = screen.getByRole("button", { name: /Possible typo/ });
    expect(typo.getAttribute("aria-pressed")).toBe("false");
    expect(typo.querySelector("mark")?.className).toContain("m-lqa-typo");
    await fireEvent.click(typo);
    await waitFor(() => expect(statusCall).toHaveBeenLastCalledWith("C:/project", 0, 50, "findings", { categories: ["typo"] }));
    expect(typo.getAttribute("aria-pressed")).toBe("true");
  });

  it("lists one chapter, or the selected verse's findings, as the scope says", async () => {
    currentChapter.set("2");
    selectedVerse.set("3-4");
    verseCall.mockResolvedValue({ projectPath: "C:/project", generation: 1, state: "completed", chapter: "2", verse: "3-4",
      findings: [lqaFinding({ id: "v1", chapter: "2", verse: "3-4", originalText: "அவண்", message: "Verse finding.", category: "typo" })],
      hidden: [] });
    await openPanel();
    await fireEvent.click(screen.getByRole("radio", { name: "Chapter" }));
    await waitFor(() => expect(statusCall).toHaveBeenLastCalledWith("C:/project", 0, 50, "findings", { chapter: "2" }));
    await fireEvent.click(screen.getByRole("radio", { name: "Verse" }));
    expect(await screen.findByText("Verse finding.")).toBeTruthy();
    expect(verseCall).toHaveBeenCalledWith("C:/project", "2", "3-4");
    expect(screen.getByRole("group", { name: "Kinds of finding" })).toHaveTextContent(/Possible typo\s*1/);
  });

  it("F8 and Shift+F8 walk the marks in reading order, and typing in a box is left alone", async () => {
    project.set({ path: "C:/project", bookId: "php", chapters: ["1", "2"] } as never);
    currentChapter.set("1");
    chapterVerseNums.set({ "1": ["1", "2"] });
    const late = lqaFinding({ id: "b", chapter: "1", verse: "1", start: 9, end: 12, originalText: "late" });
    const early = lqaFinding({ id: "a", chapter: "1", verse: "1", start: 0, end: 4, originalText: "early" });
    const second = lqaFinding({ id: "c", chapter: "1", verse: "2", start: 2, end: 5, originalText: "next" });
    languageQaFindingsByVerse.set({ [verseKey("1", "1")]: [late, early], [verseKey("1", "2")]: [second] });
    const onNavigate = await openPanel();
    await fireEvent.keyDown(window, { key: "F8" });
    expect(get(activeLanguageQaFindingId)).toBe("a");
    expect(get(selectedVerse)).toBe("1");
    expect(screen.getByRole("status")).toHaveTextContent("Finding 1 of 3 in chapter 1: early");
    await fireEvent.keyDown(window, { key: "F8" });
    await fireEvent.keyDown(window, { key: "F8" });
    expect(get(activeLanguageQaFindingId)).toBe("c");
    await fireEvent.keyDown(window, { key: "F8", shiftKey: true });
    expect(get(activeLanguageQaFindingId)).toBe("b");
    const input = document.createElement("input");
    document.body.appendChild(input);
    await fireEvent.keyDown(input, { key: "F8" });
    expect(get(activeLanguageQaFindingId)).toBe("b");
    input.remove();
    await fireEvent.keyDown(window, { key: "F8" });
    await fireEvent.keyDown(window, { key: "F8" });
    expect(onNavigate).toHaveBeenCalledWith("php", "2", "");
  });
});
