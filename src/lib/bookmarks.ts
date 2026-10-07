import { get, writable } from "svelte/store";
import { bridge } from "./api/bridgeClient";
import type { Place, SettingsData } from "./types/finding";

/**
 * indic-qa's bookmarks and recent chapters. Both are app-level settings
 * (settings_kv in workspace.sqlite3), so they follow the reviewer across
 * projects; each place names its collection, and the menu shows only the
 * open collection's.
 */
export const bookmarks = writable<Place[]>([]);
export const recentChapters = writable<Place[]>([]);

export const RECENT_MAX = 20;
const RECORD_AFTER_MS = 2000;

let recordTimer: ReturnType<typeof setTimeout> | null = null;

/** The key a place is filed under: the collection id, or the project path. */
export function collectionOf(project: { collectionId?: string; path: string } | null): string {
  return project ? project.collectionId || project.path : "";
}

/** From settings.get/set. */
export function setPlaces(settings: Pick<SettingsData, "bookmarks" | "recentChapters">): void {
  bookmarks.set(settings.bookmarks ?? []);
  recentChapters.set(settings.recentChapters ?? []);
}

function samePlace(a: Place, b: Place): boolean {
  return a.collection === b.collection && a.book === b.book && a.chapter === b.chapter
    && (a.verse ?? "") === (b.verse ?? "");
}

export function isBookmarked(list: Place[], place: Place): boolean {
  return list.some((p) => samePlace(p, place));
}

/** Add the verse, or remove it when it is already kept. Returns an error message, or "". */
export async function toggleBookmark(place: Place): Promise<string> {
  const before = get(bookmarks);
  const next = isBookmarked(before, place) ? before.filter((p) => !samePlace(p, place))
    : [{ ...place, ts: new Date().toISOString() }, ...before];
  bookmarks.set(next);
  try {
    bookmarks.set((await bridge.setSettings({ bookmarks: next })).bookmarks ?? next);
    return "";
  } catch (cause) {
    bookmarks.set(before);
    return cause instanceof Error ? cause.message : String(cause);
  }
}

/** A chapter the reviewer stayed on for two seconds joins the recent list. */
export function recordRecentChapter(place: Place): void {
  if (recordTimer) clearTimeout(recordTimer);
  recordTimer = setTimeout(() => {
    recordTimer = null;
    const next = [{ ...place, verse: undefined, ts: new Date().toISOString() },
      ...get(recentChapters).filter((p) => !samePlace(p, { ...place, verse: undefined }))].slice(0, RECENT_MAX);
    recentChapters.set(next);
    bridge.setSettings({ recentChapters: next }).catch(() => {
      // Not saved: the list still holds for this session.
    });
  }, RECORD_AFTER_MS);
}

export interface PlaceMenuAction {
  id: string;
  label: string;
  disabled?: boolean;
  title?: string;
  separatorBefore?: boolean;
}

/** The ★ menu: toggle the current verse, then the collection's bookmarks and
 * recent chapters. Ids: "toggle", "bm:<index>", "recent:<index>" into the
 * full lists, so the caller can look the place up. */
export function buildPlacesMenu(marks: Place[], recent: Place[], collection: string, current: Place | null): PlaceMenuAction[] {
  const reference = (p: Place) => `${p.book.toUpperCase()} ${p.chapter}${p.verse ? `:${p.verse}` : ""}`;
  const actions: PlaceMenuAction[] = [{
    id: "toggle",
    label: current && isBookmarked(marks, current) ? `Remove bookmark ${reference(current)}` : current
      ? `Bookmark ${reference(current)}` : "Bookmark this verse",
    disabled: !current,
    title: current ? undefined : "Select a verse first",
  }];
  const mine = marks.map((p, index) => ({ p, index })).filter(({ p }) => p.collection === collection);
  mine.forEach(({ p, index }, n) => actions.push({
    id: `bm:${index}`, label: `★ ${reference(p)}${p.label ? ` — ${p.label}` : ""}`, separatorBefore: n === 0 }));
  const recentHere = recent.map((p, index) => ({ p, index })).filter(({ p }) => p.collection === collection);
  recentHere.forEach(({ p, index }, n) => actions.push({
    id: `recent:${index}`, label: `${reference(p)} (recent)`, separatorBefore: n === 0 }));
  if (!mine.length && !recentHere.length) {
    actions.push({ id: "none", label: "No bookmarks or recent chapters yet", disabled: true, separatorBefore: true });
  }
  return actions;
}

/** For a test: forget any pending recent-chapter write. */
export function resetPlacesTimer(): void {
  if (recordTimer) clearTimeout(recordTimer);
  recordTimer = null;
}
