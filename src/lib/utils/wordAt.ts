/**
 * The word under the pointer in one rendered verse, as code points into the
 * verse's `display.plain`, for the word actions a right-click on an unmarked
 * word offers (the indic-qa editor's menu works on any word). The note, flag
 * and history buttons threaded into the text are skipped, as in
 * selectionInVerse. Null where the browser cannot say which character is under
 * the point, or when it is not inside a word.
 */
const WORD = /[\p{L}\p{M}\p{N}]+(?:[-‐][\p{L}\p{M}\p{N}]+)*/gu;

type CaretAt = { node: Node; offset: number } | null;

function caretAt(x: number, y: number): CaretAt {
  const doc = document as Document & {
    caretPositionFromPoint?: (x: number, y: number) => { offsetNode: Node; offset: number } | null;
    caretRangeFromPoint?: (x: number, y: number) => Range | null;
  };
  if (typeof doc.caretPositionFromPoint === "function") {
    const position = doc.caretPositionFromPoint(x, y);
    return position ? { node: position.offsetNode, offset: position.offset } : null;
  }
  if (typeof doc.caretRangeFromPoint === "function") {
    const range = doc.caretRangeFromPoint(x, y);
    return range ? { node: range.startContainer, offset: range.startOffset } : null;
  }
  return null;
}

/** The word at a UTF-16 index of `text`, as code-point offsets. */
export function wordAtIndex(text: string, index: number): { word: string; start: number; end: number } | null {
  for (const match of text.matchAll(WORD)) {
    const from = match.index ?? 0;
    const to = from + match[0].length;
    // A caret just after the last letter is still on the word.
    if (index >= from && index <= to) {
      return { word: match[0], start: Array.from(text.slice(0, from)).length, end: Array.from(text.slice(0, to)).length };
    }
    if (from > index) break;
  }
  return null;
}

export function wordAtPoint(root: Element, x: number, y: number): { word: string; start: number; end: number } | null {
  const caret = caretAt(x, y);
  if (!caret || !root.contains(caret.node)) return null;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: (node) => (node.parentElement?.closest("button") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
  });
  let text = "";
  let at = -1;
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (node === caret.node) at = text.length + caret.offset;
    text += node.nodeValue ?? "";
  }
  return at < 0 ? null : wordAtIndex(text, at);
}
