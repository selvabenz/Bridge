import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";

// A failed ai.review.status poll used to stop polling for good, so the panel
// sat on "Preparing AI review · 0%" beside a timeout error while the engine
// carried on with the job.
const { startAIReview, aiReviewStatus, known } = vi.hoisted(() => ({
  startAIReview: vi.fn(),
  aiReviewStatus: vi.fn(),
  known: {} as Record<string, unknown>,
}));

vi.mock("../../api/bridgeClient", () => ({
  // Everything else the panel calls on mount resolves to an empty answer.
  bridge: new Proxy(known, {
    get: (target, name: string) => {
      if (name === "startAIReview") return startAIReview;
      if (name === "aiReviewStatus") return aiReviewStatus;
      if (name === "runVerseChecks") return vi.fn().mockResolvedValue([]);
      if (!(name in target)) target[name] = vi.fn().mockResolvedValue({ items: [] });
      return target[name];
    },
  }),
}));

import ReviewPanel from "../ReviewPanel.svelte";
import { currentChapter, project, selectedVerse } from "../../stores";
import { aiReviewRequest } from "../../aiReviewUi";
import type { AIReviewJobSnapshot } from "../../types/finding";

function snapshot(overrides: Partial<AIReviewJobSnapshot>): AIReviewJobSnapshot {
  return {
    jobId: "job-1", scope: "verse", mode: "basic", projectPath: "/p", state: "running",
    chapters: ["1"], chapterVerses: { "1": ["1"] }, skippedCurrentVerses: 0, resumeOf: "",
    totalVerses: 1, completedVerses: 0, failedVerses: 0, percent: 0,
    currentChapter: "1", currentVerse: "1", currentStage: "Preparing AI review",
    results: {}, latestResult: null, error: null, createdAt: "", finishedAt: null,
    ...overrides,
  };
}

describe("ReviewPanel AI review polling", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    project.set({ path: "/p", bookId: "gen" } as never);
    currentChapter.set("1");
    selectedVerse.set("1");
  });

  afterEach(() => {
    selectedVerse.set(null);
  });

  it("keeps polling through a timed-out status and clears the error once the engine answers", async () => {
    startAIReview.mockResolvedValue(snapshot({}));
    aiReviewStatus
      .mockRejectedValueOnce(new Error("sidecar request 'ai.review.status' timed out"))
      .mockResolvedValue(snapshot({
        state: "succeeded", percent: 100, completedVerses: 1, currentStage: "Complete",
        currentChapter: null, currentVerse: null,
      }));
    render(ReviewPanel);
    await fireEvent.click(screen.getByRole("tab", { name: /AI review/ }));
    aiReviewRequest.set({ chapter: "1", verse: "1", scope: "verse" });

    expect(await screen.findAllByText(/'ai\.review\.status' timed out/)).not.toHaveLength(0);
    await waitFor(() => expect(aiReviewStatus).toHaveBeenCalledTimes(2), { timeout: 3000 });
    await waitFor(() => expect(screen.getByText("1/1 verses")).toBeInTheDocument());
    expect(screen.queryByText(/'ai\.review\.status' timed out/)).toBeNull();
  });

  it("stops after five failed polls in a row", async () => {
    startAIReview.mockResolvedValue(snapshot({}));
    aiReviewStatus.mockRejectedValue(new Error("Unknown AI review job 'job-1'."));
    render(ReviewPanel);
    aiReviewRequest.set({ chapter: "1", verse: "1", scope: "verse" });

    await waitFor(() => expect(aiReviewStatus).toHaveBeenCalledTimes(5), { timeout: 6000 });
    await new Promise((resolve) => setTimeout(resolve, 1000));
    expect(aiReviewStatus).toHaveBeenCalledTimes(5);
  });
});
