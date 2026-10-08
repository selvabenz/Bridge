import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";
import AutoAlignChapterButton from "../AutoAlignChapterButton.svelte";

const { getSettings, autoAlignEstimate, autoAlignStart, autoAlignStatus, autoAlignCancel } = vi.hoisted(() => ({
  getSettings: vi.fn(),
  autoAlignEstimate: vi.fn(),
  autoAlignStart: vi.fn(),
  autoAlignStatus: vi.fn(),
  autoAlignCancel: vi.fn(),
}));

vi.mock("../../api/bridgeClient", () => ({
  bridge: { getSettings, autoAlignEstimate, autoAlignStart, autoAlignStatus, autoAlignCancel },
}));

const ESTIMATE = {
  scope: "chapter", chapters: ["1"], windows: 15, calls: 30, estimatedInputTokens: 612000,
  estimatedOutputTokens: 45000, estimatedCostUSD: 2.31, model: "gpt-5.6", hasApiKey: true,
};

function snapshot(overrides: Record<string, unknown> = {}) {
  return {
    jobId: "aaj-1", scope: "chapter", apply: true, state: "running", stage: "", chapters: ["1"],
    windowsTotal: 15, windowsDone: 4, windowsFailed: 0, percent: 27,
    currentWindow: { chapter: "1", verses: ["7", "8", "9"] }, verdictCounts: {}, verdicts: {},
    usage: { calls: 8, totalTokens: 1000, estimatedCostUSD: 0.5 }, error: null, unavailable: null,
    resumeOf: "", createdAt: "", finishedAt: null, ...overrides,
  };
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  getSettings.mockResolvedValue({ hasApiKey: true });
  autoAlignEstimate.mockResolvedValue(ESTIMATE);
});
afterEach(() => { vi.useRealTimers(); vi.clearAllMocks(); });

describe("AutoAlignChapterButton", () => {
  it("shows the offline estimate before anything is sent, and sends nothing on Cancel", async () => {
    render(AutoAlignChapterButton, { props: { chapter: "1", onOpenReview: vi.fn() } });
    const button = await screen.findByRole("button", { name: "Align chapter with AI" });
    await waitFor(() => expect(button).toBeEnabled());
    await fireEvent.click(button);
    expect(await screen.findByText(/15 windows · 30 requests · ~612k tokens · about \$2\.31/)).toBeInTheDocument();
    expect(autoAlignEstimate).toHaveBeenCalledWith("chapter", ["1"]);
    await fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(autoAlignStart).not.toHaveBeenCalled();
  });

  it("starts, shows progress, and reports the verdicts with a way into review", async () => {
    const onOpenReview = vi.fn();
    const onFinished = vi.fn();
    autoAlignStart.mockResolvedValue(snapshot({ windowsDone: 0, currentWindow: null }));
    autoAlignStatus
      .mockResolvedValueOnce(snapshot())
      .mockResolvedValue(snapshot({
        state: "succeeded", windowsDone: 15, currentWindow: null,
        verdictCounts: { ALIGNED_CLEAN: 24, NEEDS_REVIEW: 6 },
        verdicts: { "1:5": "NEEDS_REVIEW", "1:1": "ALIGNED_CLEAN", "1:9": "NEEDS_REVIEW" },
      }));
    render(AutoAlignChapterButton, { props: { chapter: "1", onOpenReview, onFinished } });
    const button = await screen.findByRole("button", { name: "Align chapter with AI" });
    await waitFor(() => expect(button).toBeEnabled());
    await fireEvent.click(button);
    await fireEvent.click(await screen.findByRole("button", { name: "Start" }));
    expect(autoAlignStart).toHaveBeenCalledWith("chapter", ["1"]);
    await vi.advanceTimersByTimeAsync(1100);
    expect(await screen.findByText(/v\.7–9 · 4\/15 windows/)).toBeInTheDocument();
    await vi.advanceTimersByTimeAsync(1100);
    expect(await screen.findByText(/Chapter 1 aligned: 24 verses clean, 6 need review/)).toBeInTheDocument();
    expect(onFinished).toHaveBeenCalledTimes(1);
    await fireEvent.click(screen.getByRole("button", { name: "Open review" }));
    expect(onOpenReview).toHaveBeenCalledWith("5", ["5", "9"]);
  });

  it("is disabled with a reason when no API key is configured", async () => {
    getSettings.mockResolvedValue({ hasApiKey: false });
    render(AutoAlignChapterButton, { props: { chapter: "1", onOpenReview: vi.fn() } });
    const button = await screen.findByRole("button", { name: "Align chapter with AI" });
    await waitFor(() => expect(button).toBeDisabled());
    expect(button).toHaveAttribute("title", "Add an API key in Settings to align automatically");
  });

  it("reports an unavailable provider from start instead of failing", async () => {
    autoAlignStart.mockResolvedValue({ state: "failed", unavailable: { reason: "no-api-key", message: "No key configured." } });
    render(AutoAlignChapterButton, { props: { chapter: "1", onOpenReview: vi.fn() } });
    const button = await screen.findByRole("button", { name: "Align chapter with AI" });
    await waitFor(() => expect(button).toBeEnabled());
    await fireEvent.click(button);
    await fireEvent.click(await screen.findByRole("button", { name: "Start" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("No key configured.");
  });
});
