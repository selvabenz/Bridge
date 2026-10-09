import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { get } from "svelte/store";

const { collectionQaStatus, collectionRunChecks, collectionPauseChecks, collectionCancelChecks } = vi.hoisted(() => ({
  collectionQaStatus: vi.fn(),
  collectionRunChecks: vi.fn(),
  collectionPauseChecks: vi.fn(),
  collectionCancelChecks: vi.fn(),
}));

vi.mock("../../api/bridgeClient", () => ({
  bridge: { collectionQaStatus, collectionRunChecks, collectionPauseChecks, collectionCancelChecks },
}));

import CollectionQaPanel from "../CollectionQaPanel.svelte";
import { collectionQaRunning, stopCollectionQa } from "../../collectionQa";
import { collectionQaCollapsed } from "../../editorPrefs";
import type { CollectionQaBook, CollectionQaSnapshot } from "../../types/collectionQa";

function book(bookId: string, overrides: Partial<CollectionQaBook> = {}): CollectionQaBook {
  return { bookId, bookName: bookId.toUpperCase(), path: `/p/${bookId}`, state: "pending", elapsedSeconds: null,
    jobId: null, findingsByCategory: {}, checkedVerses: 0, error: null, completedAt: null, ...overrides };
}

function snapshot(overrides: Partial<CollectionQaSnapshot> = {}): CollectionQaSnapshot {
  return { jobId: "", state: "idle", paused: false, collectionPath: "/p/rut", checks: [], totalBooks: 2,
    completedBooks: 0, percent: 0, currentBook: null, books: [book("rut"), book("gen")], finalStage: null,
    elapsedSeconds: 0, estimatedRemainingSeconds: null, error: null, createdAt: null, finishedAt: null,
    ...overrides };
}

describe("CollectionQaPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    collectionQaStatus.mockResolvedValue(snapshot({
      books: [book("rut", { state: "done", completedAt: "2026-09-24T10:00:00Z", checkedVerses: 85,
        findingsByCategory: { languageQa: 3 } }), book("gen")],
    }));
  });
  afterEach(() => {
    stopCollectionQa();
    collectionQaCollapsed.set(false);
  });

  it("folds to its header, remembers it, and still offers a run", async () => {
    render(CollectionQaPanel, { props: { bookCount: 2 } });
    await screen.findByText("languageQa 3");
    await fireEvent.click(screen.getByRole("button", { name: "Collapse Collection QA" }));
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.getByText(/2 books · 1 done · last run/)).toBeInTheDocument();
    expect(localStorage.getItem("bridge.dashboard.collectionQaCollapsed.v1")).toBe("true");
    expect(screen.getByRole("button", { name: "Run QA on all 2 books" })).toBeEnabled();
    await fireEvent.click(screen.getByRole("button", { name: "Expand Collection QA" }));
    expect(screen.getByRole("table")).toBeInTheDocument();
  });

  it("opens itself while a run is in progress, whatever was chosen", async () => {
    collectionQaCollapsed.set(true);
    collectionQaStatus.mockResolvedValue(snapshot({ jobId: "j1", state: "running", currentBook: "rut",
      books: [book("rut", { state: "running" }), book("gen")] }));
    render(CollectionQaPanel, { props: { bookCount: 2 } });
    expect(await screen.findByRole("table")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Collapse Collection QA" })).toBeDisabled();
  });

  it("shows each book's last recorded run and offers a run, never starting one itself", async () => {
    render(CollectionQaPanel, { props: { bookCount: 2 } });
    expect(await screen.findByText("languageQa 3")).toBeInTheDocument();
    expect(screen.getByText("85")).toBeInTheDocument();
    expect(collectionRunChecks).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Run QA on all 2 books" })).toBeEnabled();
  });

  it("starts a run, holds the app read-only while it runs, and releases it when it ends", async () => {
    collectionRunChecks.mockResolvedValue(snapshot({ jobId: "j1", state: "running", currentBook: "rut",
      books: [book("rut", { state: "running" }), book("gen")] }));
    render(CollectionQaPanel, { props: { bookCount: 2 } });
    await fireEvent.click(await screen.findByRole("button", { name: "Run QA on all 2 books" }));
    expect(collectionRunChecks).toHaveBeenCalledWith(["local", "greekroom", "languageQa"], false);
    await waitFor(() => expect(get(collectionQaRunning)).toBe(true));
    expect(screen.getByRole("button", { name: "Pause after this book" })).toBeInTheDocument();
    for (const button of screen.getAllByRole("button", { name: "Open book" })) expect(button).toBeDisabled();
    collectionQaStatus.mockResolvedValue(snapshot({ jobId: "j1", state: "succeeded",
      books: [book("rut", { state: "done" }), book("gen", { state: "skipped" })] }));
    await waitFor(() => expect(get(collectionQaRunning)).toBe(false), { timeout: 3000 });
    expect(screen.getByText("Unchanged")).toBeInTheDocument();
  });
});
