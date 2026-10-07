import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { get } from "svelte/store";

const { setSettings } = vi.hoisted(() => ({ setSettings: vi.fn() }));
vi.mock("../../api/bridgeClient", () => ({ bridge: { setSettings } }));

import {
  bookmarks, buildPlacesMenu, collectionOf, recentChapters, recordRecentChapter, resetPlacesTimer, setPlaces, toggleBookmark,
} from "../../bookmarks";
import { TEXT_SCALES, bumpTextScale, textScale } from "../../editorPrefs";
import type { Place } from "../../types/finding";

const here = (chapter: string, verse?: string, collection = "c1"): Place => ({ collection, book: "gen", chapter, verse });

describe("bookmarks and recent chapters", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setPlaces({ bookmarks: [], recentChapters: [] });
  });
  afterEach(() => {
    resetPlacesTimer();
    vi.useRealTimers();
  });

  it("files a place under the collection id, or the project path without one", () => {
    expect(collectionOf({ collectionId: "c1", path: "/p" })).toBe("c1");
    expect(collectionOf({ path: "/p" })).toBe("/p");
    expect(collectionOf(null)).toBe("");
  });

  it("toggles the current verse, saving the list, and puts it back on a refusal", async () => {
    setSettings.mockImplementation(async (params: { bookmarks: Place[] }) => ({ bookmarks: params.bookmarks }));
    expect(await toggleBookmark(here("3", "16"))).toBe("");
    expect(get(bookmarks).map((p) => p.verse)).toEqual(["16"]);
    expect(setSettings).toHaveBeenCalledWith({ bookmarks: [expect.objectContaining({ chapter: "3", verse: "16" })] });
    expect(await toggleBookmark(here("3", "16"))).toBe("");
    expect(get(bookmarks)).toEqual([]);
    setSettings.mockRejectedValueOnce(new Error("disk full"));
    expect(await toggleBookmark(here("1", "1"))).toBe("disk full");
    expect(get(bookmarks)).toEqual([]);
  });

  it("records a chapter after two seconds on it, newest first, without repeats", async () => {
    vi.useFakeTimers();
    setSettings.mockResolvedValue({});
    recordRecentChapter(here("1"));
    await vi.advanceTimersByTimeAsync(1000);
    recordRecentChapter(here("2"));  // moved on before two seconds: chapter 1 is not recorded
    await vi.advanceTimersByTimeAsync(2000);
    recordRecentChapter(here("5"));
    await vi.advanceTimersByTimeAsync(2000);
    recordRecentChapter(here("2"));
    await vi.advanceTimersByTimeAsync(2000);
    expect(get(recentChapters).map((p) => p.chapter)).toEqual(["2", "5"]);
    expect(setSettings).toHaveBeenLastCalledWith({ recentChapters: get(recentChapters) });
  });

  it("lists only the open collection's places in the menu, with the toggle first", () => {
    const marks = [here("3", "16"), here("1", "1", "other")];
    const recent = [here("4"), here("9", undefined, "other")];
    const menu = buildPlacesMenu(marks, recent, "c1", here("3", "16"));
    expect(menu.map((a) => [a.id, a.label])).toEqual([
      ["toggle", "Remove bookmark GEN 3:16"], ["bm:0", "★ GEN 3:16"], ["recent:0", "GEN 4 (recent)"]]);
    expect(buildPlacesMenu([], [], "c1", null)).toEqual([
      expect.objectContaining({ id: "toggle", disabled: true }),
      expect.objectContaining({ id: "none", disabled: true })]);
  });
});

describe("text size", () => {
  afterEach(() => textScale.set(1));

  it("steps along the ladder and stops at either end", () => {
    textScale.set(1);
    bumpTextScale(1);
    expect(get(textScale)).toBe(1.15);
    for (let i = 0; i < 10; i += 1) bumpTextScale(1);
    expect(get(textScale)).toBe(TEXT_SCALES[TEXT_SCALES.length - 1]);
    for (let i = 0; i < 10; i += 1) bumpTextScale(-1);
    expect(get(textScale)).toBe(TEXT_SCALES[0]);
  });
});
