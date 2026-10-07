import { describe, it, expect } from "vitest";
import { withNoteMarkers } from "../../utils/usfmNotes";
import { displayFor } from "./verseDisplayFixtures";
import type { TextSegment } from "../../utils/highlight";

const seg = (text: string): TextSegment => ({
  text,
  findingIds: [],
  className: null,
  title: "",
  numbers: [],
});

describe("withNoteMarkers", () => {
  it("splits a segment so the marker lands where the note was", () => {
    const parsed = displayFor("alpha\\f + \\ft n\\f* bravo");
    const pieces = withNoteMarkers([seg(parsed.plain)], parsed.notes);
    expect(pieces.map((p) => (p.kind === "note" ? "[f]" : p.kind === "text" ? p.seg.text : "⚑"))).toEqual([
      "alpha",
      "[f]",
      " bravo",
    ]);
  });

  it("puts a leading note before all the text", () => {
    const parsed = displayFor("\\f + \\ft n\\f*alpha");
    const pieces = withNoteMarkers([seg(parsed.plain)], parsed.notes);
    expect(pieces[0].kind).toBe("note");
  });

  it("puts a trailing note after all the text", () => {
    const parsed = displayFor("alpha\\f + \\ft n\\f*");
    const pieces = withNoteMarkers([seg(parsed.plain)], parsed.notes);
    expect(pieces[pieces.length - 1].kind).toBe("note");
  });

  it("keeps highlight segments intact when a note sits on their boundary", () => {
    const parsed = displayFor("alpha\\f + \\ft n\\f* bravo");
    const marked: TextSegment[] = [
      { ...seg("alpha"), className: "m-gr" },
      seg(" bravo"),
    ];
    const pieces = withNoteMarkers(marked, parsed.notes);
    const texts = pieces.filter((p) => p.kind === "text");
    expect(texts.map((p) => (p as { seg: TextSegment }).seg.text)).toEqual(["alpha", " bravo"]);
    expect((texts[0] as { seg: TextSegment }).seg.className).toBe("m-gr");
  });

  it("returns segments unchanged when there are no notes", () => {
    const pieces = withNoteMarkers([seg("alpha bravo")], []);
    expect(pieces).toHaveLength(1);
    expect(pieces[0].kind).toBe("text");
  });

  it("orders several markers by position", () => {
    const parsed = displayFor("a\\f + \\ft one\\f*b\\x + \\xt G 1:1\\x*c");
    const pieces = withNoteMarkers([seg(parsed.plain)], parsed.notes);
    expect(pieces.map((p) => (p.kind === "note" ? p.note.kind : p.kind === "text" ? p.seg.text : "⚑"))).toEqual([
      "a",
      "footnote",
      "b",
      "xref",
      "c",
    ]);
  });
});

describe("withNoteMarkers with reviewer flags", () => {
  const flag = { flagId: "f1", status: "open" } as never;

  it("puts a flag's ⚑ after the flagged text, before a footnote callout at the same spot", () => {
    const parsed = displayFor("alpha\\f + \\ft n\\f* bravo");
    const pieces = withNoteMarkers([seg(parsed.plain)], parsed.notes, [{ position: 5, flag }]);
    expect(pieces.map((p) => (p.kind === "note" ? "[f]" : p.kind === "flag" ? "⚑" : p.seg.text)))
      .toEqual(["alpha", "⚑", "[f]", " bravo"]);
  });

  it("splits a segment for a flag in the middle of it, and keeps one at the end", () => {
    const pieces = withNoteMarkers([seg("alpha bravo")], [], [{ position: 5, flag }, { position: 11, flag }]);
    expect(pieces.map((p) => (p.kind === "flag" ? "⚑" : p.kind === "text" ? p.seg.text : "?")))
      .toEqual(["alpha", "⚑", " bravo", "⚑"]);
  });
});
