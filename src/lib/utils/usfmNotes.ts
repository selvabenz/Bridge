import type { TextSegment } from "./highlight";
import type { VerseNote } from "../types/finding";
import type { LanguageQaFlag } from "../types/languageQa";

export type { VerseNote, VerseNoteKind, VerseNotePart } from "../types/finding";

/**
 * Layout only. The parsing that used to live here — lifting `\f`/`\x` out of
 * the raw verse and remapping offsets — is the engine's now (#91 Phase 2b):
 * every verse arrives with a `VerseDisplay` from `usfm_verse.lift_verse`, and
 * the frontend never parses USFM. What remains is threading the note markers
 * back into the highlighted segments at the spot each note was lifted from.
 *
 * `note.position` must already be a UTF-16 index into the rendered text
 * (see utils/verseDisplay.ts plainToUtf16); the engine sends code points.
 */

export type VersePiece =
  | { kind: "text"; seg: TextSegment }
  | { kind: "note"; note: VerseNote }
  | { kind: "flag"; flag: LanguageQaFlag };

/** A reviewer flag's ⚑, at a UTF-16 index into the rendered text (after the flagged text). */
export interface FlagMarker { position: number; flag: LanguageQaFlag }

/**
 * Threads note markers (and reviewer flags' ⚑) back into the highlighted
 * segments at the exact spot each belongs, splitting a segment when one lands
 * mid-span. At the same spot a flag comes first: it closes the flagged text,
 * and a footnote callout follows the text it annotates.
 */
export function withNoteMarkers(segments: TextSegment[], notes: VerseNote[], flags: FlagMarker[] = []): VersePiece[] {
  type Marker = { position: number; order: number; piece: VersePiece };
  const ordered: Marker[] = [
    ...flags.map((f) => ({ position: f.position, order: 0, piece: { kind: "flag", flag: f.flag } as VersePiece })),
    ...notes.map((note) => ({ position: note.position, order: 1, piece: { kind: "note", note } as VersePiece })),
  ].sort((a, b) => a.position - b.position || a.order - b.order);
  const pieces: VersePiece[] = [];
  let noteIndex = 0;
  let offset = 0;

  for (const seg of segments) {
    const segStart = offset;
    const segEnd = offset + seg.text.length;
    let cursor = segStart;

    while (noteIndex < ordered.length && ordered[noteIndex].position <= segEnd) {
      const at = Math.max(ordered[noteIndex].position, segStart);
      if (at > cursor) {
        pieces.push({ kind: "text", seg: { ...seg, text: seg.text.slice(cursor - segStart, at - segStart) } });
        cursor = at;
      }
      pieces.push(ordered[noteIndex].piece);
      noteIndex += 1;
    }

    if (cursor < segEnd) {
      pieces.push({ kind: "text", seg: { ...seg, text: seg.text.slice(cursor - segStart) } });
    }
    offset = segEnd;
  }

  while (noteIndex < ordered.length) {
    pieces.push(ordered[noteIndex].piece);
    noteIndex += 1;
  }
  return pieces;
}
