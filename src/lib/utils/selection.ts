/**
 * The reader's text selection inside one rendered verse, as code points into
 * the verse's `display.plain`. Only the verse's own text counts: the note,
 * flag and history buttons threaded into it are skipped, because their labels
 * are not part of the text. Null when the selection is empty or not wholly
 * inside `root`.
 */
export function selectionInVerse(root: Element): { start: number; end: number } | null {
  const selection = typeof window !== "undefined" ? window.getSelection() : null;
  if (!selection || selection.rangeCount === 0 || selection.isCollapsed) return null;
  const range = selection.getRangeAt(0);
  if (!root.contains(range.startContainer) || !root.contains(range.endContainer)) return null;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: (node) => (node.parentElement?.closest("button") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
  });
  let text = "";
  let start = -1;
  let end = -1;
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const value = node.nodeValue ?? "";
    if (node === range.startContainer) start = text.length + range.startOffset;
    if (node === range.endContainer) end = text.length + range.endOffset;
    text += value;
  }
  if (start < 0 || end < 0 || end <= start) return null;
  // UTF-16 indexes into the rendered text, as code points.
  return { start: Array.from(text.slice(0, start)).length, end: Array.from(text.slice(0, end)).length };
}
