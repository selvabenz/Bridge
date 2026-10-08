import { afterEach, describe, expect, it } from "vitest";

import { wordAtIndex, wordAtPoint } from "../../utils/wordAt";

describe("wordAt: the word under the pointer, for the menu on an unmarked word", () => {
  afterEach(() => {
    delete (document as unknown as { caretRangeFromPoint?: unknown }).caretRangeFromPoint;
    document.body.innerHTML = "";
  });

  it("finds the word around an index, in code points, with its combining marks", () => {
    const text = "𝔸 அந்த காகம் பறந்தது.";
    expect(wordAtIndex(text, text.indexOf("காகம்") + 2)).toEqual({ word: "காகம்", start: 7, end: 12 });
    expect(wordAtIndex(text, text.indexOf("காகம்") + "காகம்".length)).toEqual({ word: "காகம்", start: 7, end: 12 });
    // A caret just after a word is still on it; one in the middle of "..." is on none.
    expect(wordAtIndex("அவன் ... வந்தான்", 6)).toBeNull();
  });

  it("reads the caret the browser reports, skipping the buttons threaded into the text", () => {
    document.body.innerHTML = '<div class="vtext">அவன் <button>⚑</button>வந்தான்</div>';
    const root = document.querySelector(".vtext") as HTMLElement;
    const last = root.lastChild as Text;
    (document as unknown as { caretRangeFromPoint: () => Partial<Range> }).caretRangeFromPoint =
      () => ({ startContainer: last, startOffset: 2 });
    expect(wordAtPoint(root, 10, 10)).toEqual({ word: "வந்தான்", start: 5, end: 12 });
  });

  it("is null where the browser cannot say", () => {
    document.body.innerHTML = '<div class="vtext">அவன்</div>';
    expect(wordAtPoint(document.querySelector(".vtext") as HTMLElement, 1, 1)).toBeNull();
  });
});
