import { writable, type Writable } from "svelte/store";

/**
 * Per-browser editor preferences (indic-qa keeps the same ones in the
 * browser): whether the reference panel is open, the raw USFM view, and the
 * verse text size. Saved to localStorage, best effort: a private window or a
 * blocked store just starts from the defaults.
 */
function persisted<T>(key: string, fallback: T, valid: (value: unknown) => value is T): Writable<T> {
  let initial = fallback;
  try {
    const raw = localStorage.getItem(key);
    if (raw !== null) {
      const value: unknown = JSON.parse(raw);
      if (valid(value)) initial = value;
    }
  } catch {
    // unreadable: keep the default
  }
  const store = writable<T>(initial);
  store.subscribe((value) => {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      // not saved: the preference lasts for this session only
    }
  });
  return store;
}

const isBoolean = (value: unknown): value is boolean => typeof value === "boolean";

/** The reference Bible beside the text. Off by default: it narrows the text column. */
export const referencePanelOpen = persisted("bridge.editor.referencePanel.v1", false, isBoolean);

/** The reference panel's width in px, set by dragging its left edge. */
export const REFERENCE_WIDTH = { min: 200, max: 640, initial: 300 } as const;
export function clampReferenceWidth(px: number): number {
  return Math.round(Math.min(REFERENCE_WIDTH.max, Math.max(REFERENCE_WIDTH.min, px)));
}
const isReferenceWidth = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value) && value === clampReferenceWidth(value);
export const referencePanelWidth = persisted<number>(
  "bridge.editor.referencePanelWidth.v1", REFERENCE_WIDTH.initial, isReferenceWidth);

/** The review panel folded to a narrow strip at the right edge, giving the
 * text the room. Its contents stay mounted, so nothing in it is lost. */
export const reviewPanelCollapsed = persisted("bridge.editor.reviewPanelCollapsed.v1", false, isBoolean);

/** The verse text exactly as stored, USFM markers and all, instead of the
 * reader's clean text. Marks stay on the same characters. */
export const rawView = persisted("bridge.editor.rawView.v1", false, isBoolean);

/** The verse text size, a multiple of the normal size (indic-qa's A− / A+). */
export const TEXT_SCALES = [0.85, 1, 1.15, 1.3, 1.5, 1.7] as const;
const isScale = (value: unknown): value is number => typeof value === "number" && (TEXT_SCALES as readonly number[]).includes(value);
export const textScale = persisted<number>("bridge.editor.textScale.v1", 1, isScale);

/** One step smaller (-1) or larger (+1), stopping at either end. */
export function bumpTextScale(step: -1 | 1): void {
  textScale.update((scale) => {
    const at = Math.max(0, (TEXT_SCALES as readonly number[]).indexOf(scale));
    return TEXT_SCALES[Math.min(TEXT_SCALES.length - 1, Math.max(0, at + step))];
  });
}

const isStringArray = (value: unknown): value is string[] =>
  Array.isArray(value) && value.every((item) => typeof item === "string");

/** Which sections of the Checker settings dialog are open, by section id. As
 * in the indic-qa web app, every section starts closed and stays as left. */
export const checkerSettingsOpen = persisted<string[]>("bridge.checkerSettings.open.v1", [], isStringArray);
