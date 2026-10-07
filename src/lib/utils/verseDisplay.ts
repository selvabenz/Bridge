import type { VerseDisplay } from "../types/finding";
import { codePointToUtf16 } from "./codePoints";

/**
 * Helpers over the engine's `VerseDisplay` payload (#91 Phase 2b).
 *
 * The engine computes what a verse shows; the frontend only maps offsets onto
 * it. QaFinding and Language QA offsets index the RAW verse string in code
 * points. The display removed some raw ranges, so a raw offset has to move
 * left by everything removed before it, or each underline slides off its word.
 * `plainOffset` does that in code points; `utf16Offset` then converts to the
 * UTF-16 index a JS string slice needs. Both steps matter: Tamil is in the BMP
 * so the second is a no-op there, but it is not for every script.
 */

/** The display of a verse that has no engine display yet: the raw string as-is. */
export function identityDisplay(raw: string): VerseDisplay {
  return { plain: raw, notes: [], removed: [], styles: [], warnings: [] };
}

/**
 * Where a raw code-point offset lands in `display.plain`, in code points. An
 * offset inside a removed range collapses to the position the removal left
 * behind, so a span that lived entirely inside a note maps to a zero-length
 * range — which covers no segment rather than drawing an empty highlight.
 */
export function plainOffset(display: VerseDisplay, rawOffset: number): number {
  if (rawOffset <= 0) return 0;
  let removedBefore = 0;
  for (const [start, end] of display.removed) {
    if (rawOffset <= start) break;
    if (rawOffset < end) return start - removedBefore;
    removedBefore += end - start;
  }
  const plainLength = Array.from(display.plain).length;
  return Math.min(rawOffset - removedBefore, plainLength);
}

/** A raw code-point offset as a UTF-16 index into `display.plain`. */
export function utf16Offset(display: VerseDisplay, rawOffset: number): number {
  return codePointToUtf16(display.plain, plainOffset(display, rawOffset));
}

/** A code-point offset already in `plain` (a note's position, a style's bound) as a UTF-16 index. */
export function plainToUtf16(display: VerseDisplay, plainCodePoint: number): number {
  return codePointToUtf16(display.plain, plainCodePoint);
}

/**
 * The inverse of `plainOffset`: a code-point offset in `display.plain` back to
 * the raw verse string, by adding every removed range before it. At a removal
 * boundary the two sides of a span differ: a span's start belongs after a
 * lifted note (side "start"), its end before it (side "end"), so a selection
 * that ends just before a footnote never swallows the footnote.
 */
export function rawOffsetFromPlain(display: VerseDisplay, plainCodePoint: number, side: "start" | "end"): number {
  let raw = Math.max(0, plainCodePoint);
  for (const [start, end] of display.removed) {
    if (side === "start" ? start <= raw : start < raw) raw += end - start;
    else break;
  }
  return raw;
}
