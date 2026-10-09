"""The one USFM document parser (#91).

Every whole-book USFM read that decides *what a verse is* -- where it starts,
where it ends, which lines are section headings and which are Scripture -- goes
through :func:`parse_usfm`. It wraps usfmtc, the USFM Technical Committee's
reference implementation, and **no other Bridge module may import usfmtc**: the
walker below is ours so the parser behind it stays swappable.

The parser decides structure; the text is cut from the source. usfmtc gives each
paragraph, chapter, verse and note element a line/column position, so a verse's
stored string is a verbatim slice of the input between parser-chosen
boundaries, not a re-serialisation. That keeps what an import writes to chapter
JSON byte-identical to the original markup -- inline notes, ``\\w`` attributes
and ``\\zaln`` milestones included -- which translationCore, the aligned-USFM
export and every finding offset depend on.

Things measured, not read from docs (usfmtc 0.4.8, 2026-09-29):

- ``USX.fromUsfm`` treats a *string* argument as a filename whenever
  ``os.path.exists`` says so, and raises FileNotFoundError for any short
  single-line string that is not one. Input is always passed as a StringIO.
- Only the ``usfmtc`` CLI, ``etCmp`` and a debug path print; the parse path does
  not. Parsing must not run under ``contextlib.redirect_stdout``: that swaps the
  process-global ``sys.stdout``, and a parse on a background job thread then
  swallowed the dispatcher's response to whatever request it was answering
  (``ai.review.status`` timing out, 2026-10-09). The stdio transport writes to
  the stream it captured at start-up and points ``sys.stdout`` at stderr, so a
  stray print cannot reach the protocol anyway.
- ``\\zaln-s``/``\\zaln-e`` are reported as "Unknown tag" warnings but parse, so
  aligned USFM is read in the default lenient mode.
- ``import usfmtc`` costs ~100 ms, so it is imported on first parse, not at
  sidecar start.
- Its lexer is quadratic in input length (it slices the rest of the input for
  every tag and attribute), which is why a book is handed over a chapter at a
  time -- see :func:`_chapter_chunks`. Even so it runs at roughly 2 us a
  character, ~20x the line regex it replaced: the whole Tamil IRV Bible is
  ~11 s, an aligned Psalms ~5-8 s. Import previews therefore read identity
  from the preamble only (:func:`identify_usfm`).
- Parity with the line regex it replaced, over 443 distinct local USFM files
  (209,715 verses): identical tokens on every verse, except where the regex
  was wrong -- 263 verses it lost because their ``\\v`` did not start the line,
  and 6 "verses" it invented from a ``\\v`` with no number.
"""
from __future__ import annotations

import bisect
import io
import re
from pathlib import Path
from dataclasses import dataclass
from typing import Any

# Section headings and their kin. A heading between two verses is an editorial
# navigation aid, not a translation of any source word, so it is never part of
# a verse's text (#92, #180). It is filed against the verse it INTRODUCES.
#
# \d (descriptive title, e.g. a Psalm superscription) is deliberately NOT here:
# it is translated content in its own right, and pulling it out of the verse
# would remove real text from alignment.
HEADING_MARKERS = frozenset({
    "s", "s1", "s2", "s3", "s4", "s5",
    "ms", "ms1", "ms2", "ms3",
    "mr", "r", "sr", "sp",
})

# Elements that start a new block of the document. A heading runs from its own
# marker to the next of these.
_BLOCK_TAGS = frozenset({"book", "chapter", "verse", "para", "table", "row", "sidebar", "periph"})

_MARKER_AT = re.compile(r"\\(?P<tag>[A-Za-z0-9-]+)\*?")
_VERSE_MARKER_AT = re.compile(r"\\v\s+\S+[ \t]*")
_MARKER_ONLY_LINE = re.compile(r"^[ \t]*\\[A-Za-z0-9]+\*?[ \t]*$")
_CHAPTER_LINE = re.compile(r"^[ \t]*\\c[ \t]", re.MULTILINE)
_VERSE_MARKER_ANYWHERE = re.compile(r"\\v\s+\S")
_NUMBERLESS_VERSE_AT = re.compile(r"\\v[ \t]*")
_USFM_VERSION = re.compile(r"\s*\\usfm[ \t]+(?P<version>\S+)")


class UsfmParseError(ValueError):
    """The input could not be read as USFM at all."""


@dataclass(frozen=True)
class UsfmHeader:
    tag: str
    content: str
    offset: int = -1  # code-point offset of the marker in the source; -1 when unknown


@dataclass(frozen=True)
class UsfmHeading:
    chapter: str
    verse: str  # the verse this heading introduces
    tag: str
    text: str


@dataclass(frozen=True)
class UsfmVerse:
    chapter: str
    verse: str  # opaque: bridges ("3-4") and segments ("3a") are kept as written
    text: str   # the verse's own USFM, cut from the source, headings removed
    # Where `text` came from, as code-point offsets into the parsed source, so
    # an export can put edited text back in the same place and leave everything
    # else -- headings, paragraph markers, the next verse -- exactly as written
    # (#190). `start` is just after the `\v N ` marker; `head_end` is the end of
    # the verse's text before any heading inside it (trailing newlines
    # excluded), so [start, head_end) is the region current text is written to.
    # `tail_spans` are the Scripture lines that followed a heading *inside* the
    # verse and were folded into `text`; an export deletes them, because the
    # whole current text is written at the head.
    start: int = 0
    head_end: int = 0
    tail_spans: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True)
class UsfmStructure:
    """One structural element of the book body, in document order (#91 Phase 3b):
    a chapter, a verse, or a paragraph-level marker (`\\p`, `\\q1`, `\\s`, `\\b` …).
    `verse` is the verse this element belongs to or introduces: for a verse,
    itself; for a chapter or a paragraph marker, the next verse that follows it
    in the same chapter, or "" when none does. `offset` is the code-point
    offset of the marker in the source."""
    kind: str      # "chapter" | "verse" | "para"
    marker: str    # "c", "v", or the paragraph style lower-cased
    chapter: str
    verse: str
    offset: int


@dataclass(frozen=True)
class ParsedUsfm:
    book_code: str  # as written after \id, upper-cased; "" when there is no \id
    id_line: str    # everything after "\id " on that line
    headers: tuple[UsfmHeader, ...]
    chapters: tuple[str, ...]
    verses: tuple[UsfmVerse, ...]
    headings: tuple[UsfmHeading, ...]
    warnings: tuple[str, ...]
    structure: tuple[UsfmStructure, ...] = ()

    def header(self, tag: str) -> str:
        for item in self.headers:
            if item.tag.lower() == tag.lower():
                return item.content
        return ""


def _line_starts(text: str) -> list[int]:
    starts = [0]
    for index, char in enumerate(text):
        if char == "\n":
            starts.append(index + 1)
    return starts


def _chapter_chunks(text: str) -> list[str]:
    """The book cut before each line-initial ``\\c``, each chunk padded with the
    newlines that preceded it so usfmtc's line numbers stay absolute.

    usfmtc 0.4.8's lexer matches every tag and attribute against
    ``self.txt[m.end():]`` -- a copy of the rest of the input -- so its cost is
    quadratic in the length of what it is given. On ordinary USFM that is
    invisible (Luke, 480 KB: 0.3 s), but aligned USFM carries a ``\\w`` and a
    ``\\zaln`` with attributes on every word: a 1.9 MB aligned Psalms took 277 s
    whole, over the sidecar's 300 s import timeout. Per chapter it is linear in
    the number of chapters. A chapter boundary resets all verse and paragraph
    state anyway, so where the cut falls decides nothing about structure; a
    ``\\c`` that does not start its line simply stays inside its chunk.
    """
    cuts = [match.start() for match in _CHAPTER_LINE.finditer(text)][1:]
    chunks: list[str] = []
    previous = 0
    newlines = 0
    for cut in [*cuts, len(text)]:
        chunks.append("\n" * newlines + text[previous:cut])
        newlines += text.count("\n", previous, cut)
        previous = cut
    return chunks


def _usfmtc_documents(text: str) -> tuple[list[Any], list[Any]]:
    try:
        import usfmtc
    except ImportError as exc:  # pragma: no cover - a broken install or freeze
        raise UsfmParseError(f"The USFM parser is not available: {exc}") from exc
    roots: list[Any] = []
    errors: list[Any] = []
    try:
        for chunk in _chapter_chunks(text):
            document = usfmtc.USX.fromUsfm(io.StringIO(chunk))
            roots.append(document.xml)
            errors.extend(document.errors or [])
    except Exception as exc:  # usfmtc raises a wide variety on hostile input
        raise UsfmParseError(f"{type(exc).__name__}: {exc}") from exc
    return roots, errors


def _warning_text(error: Any) -> str:
    if isinstance(error, tuple):
        return " ".join(str(part) for part in error if part is not None)
    return str(error)


_USFM_ENCODINGS = ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be")


def read_usfm_text(path: Any) -> str:
    """Decode a USFM file to text with ``\\n`` newlines.

    The one decoder for the preserved source (#190): import, export and the
    semantic runtime used to carry their own copies of this loop. UTF-8 with or
    without a BOM, then the UTF-16 variants Paratext and Windows editors
    produce; a decode that yields no backslash at all is the wrong encoding, not
    Scripture. Raises :class:`UsfmParseError` when nothing fits; the caller
    decides whether that is an import refusal or an export fallback.
    """
    return decode_usfm_text(Path(path).read_bytes(), Path(path).name)


def decode_usfm_text(raw: bytes, name: str = "") -> str:
    """`read_usfm_text` for bytes already in hand (a caller that also hashes
    them reads the file once). Same encodings, same newline normalisation,
    same refusal."""
    for encoding in _USFM_ENCODINGS:
        try:
            text = raw.decode(encoding)
        except UnicodeError:
            continue
        if "\\" in text:
            return text.replace("\r\n", "\n").replace("\r", "\n")
    raise UsfmParseError(f"{name or 'USFM input'} is not UTF-8 or UTF-16 USFM text.")


@dataclass(frozen=True)
class UsfmIdentity:
    """What an import *preview* needs, without parsing the book body."""
    book_code: str
    id_line: str
    headers: tuple[UsfmHeader, ...]
    has_chapters: bool
    # A count of \v markers, not a parse: shown in the preview as the book's
    # size. The import's parse_usfm decides the real verses.
    verse_markers: int


def identify_usfm(text: str) -> UsfmIdentity:
    """Book code, \\id line and headers from the preamble alone.

    A 66-book preview used to parse every book in full; through usfmtc that is
    ~11 s for the Tamil IRV Bible (its lexer runs at ~2 us a character), against
    0.5 s for the line regex it replaced. Only the preamble decides identity, so
    only the preamble is parsed; the body is parsed when the book is normalized
    -- the first book at import, the rest lazily on first open.
    """
    text = text.lstrip("\ufeff")
    first_chapter = _CHAPTER_LINE.search(text)
    preamble = parse_usfm(text[:first_chapter.start()] if first_chapter else text)
    return UsfmIdentity(
        book_code=preamble.book_code,
        id_line=preamble.id_line,
        headers=preamble.headers,
        has_chapters=first_chapter is not None or bool(preamble.chapters),
        verse_markers=len(_VERSE_MARKER_ANYWHERE.findall(text)),
    )


def parse_usfm(text: str) -> ParsedUsfm:
    """Parse one book of USFM. ``text`` must already be decoded, with ``\\n`` newlines."""
    text = text.lstrip("\ufeff")
    roots, errors = _usfmtc_documents(text)
    starts = _line_starts(text)

    def offset(element: Any) -> int | None:
        pos = getattr(element, "pos", None)
        if pos is None or pos.l >= len(starts):
            return None
        value = starts[pos.l] + pos.c - 1
        if not (0 <= value < len(text)) or text[value] != "\\":
            raise UsfmParseError(
                f"Parser position {pos.l}:{pos.c} does not point at a marker; "
                "refusing to cut verse text from a drifted offset."
            )
        return value

    book_code = ""
    id_line = ""
    headers: list[UsfmHeader] = []
    blocks: list[int] = []
    # (kind, offset, value): "c" chapter number, "v" verse number, "h" heading tag
    events: list[tuple[str, int, str]] = []
    seen_chapter = False

    for element in (item for root in roots for item in root.iter()):
        tag = element.tag
        if tag not in _BLOCK_TAGS:
            continue
        if tag == "chapter" and element.get("eid") is not None:
            continue
        at = offset(element)
        if at is None:
            continue
        blocks.append(at)
        marker = _MARKER_AT.match(text, at)
        marker_end = marker.end() if marker else at + 1
        if tag == "book":
            book_code = str(element.get("code") or "").upper()
            line_end = text.find("\n", at)
            id_line = text[marker_end:line_end if line_end >= 0 else len(text)].strip()
            headers.append(UsfmHeader("id", id_line, at))
            # usfmtc folds \usfm into the document's version attribute rather
            # than giving it an element, so it is read back from the source.
            usfm_version = _USFM_VERSION.match(text, line_end if line_end >= 0 else len(text))
            if usfm_version:
                headers.append(UsfmHeader(
                    "usfm", usfm_version.group("version"),
                    text.find(chr(92) + "usfm", line_end if line_end >= 0 else len(text)),
                ))
        elif tag == "chapter":
            seen_chapter = True
            events.append(("c", at, str(element.get("number") or "")))
        elif tag == "verse":
            if seen_chapter:
                events.append(("v", at, str(element.get("number") or "")))
        elif tag == "para":
            style = str(element.get("style") or "")
            source_tag = marker.group("tag").lower() if marker else ""
            if source_tag != style.lower():
                # usfmtc opens an implicit paragraph when a verse follows a
                # chapter with no `\p` between them, and reports it at the
                # chapter marker's position. It is not in the source; it is
                # not structure (#91 Phase 3c).
                continue
            if not seen_chapter:
                headers.append(UsfmHeader(marker.group("tag") if marker else style, "", at))
                events.append(("header", at, str(len(headers) - 1)))
            else:
                # Every paragraph-level marker in the body is structure; the
                # heading ones are also cut out of verse text (the "h" event).
                events.append(("p", at, style.lower()))
                if style.lower() in HEADING_MARKERS:
                    events.append(("h", at, style.lower()))

    blocks.sort()
    events.sort(key=lambda item: item[1])

    def block_end(at: int) -> int:
        index = bisect.bisect_right(blocks, at)
        return blocks[index] if index < len(blocks) else len(text)

    def marker_content(at: int) -> str:
        marker = _MARKER_AT.match(text, at)
        body = text[marker.end() if marker else at:block_end(at)]
        return re.sub(r"\s+", " ", body).strip()

    for kind, at, value in events:
        if kind == "header":
            index = int(value)
            headers[index] = UsfmHeader(headers[index].tag, marker_content(at), at)

    chapters: list[str] = []
    verses: list[UsfmVerse] = []
    headings: list[UsfmHeading] = []
    # Document structure: chapters, verses and paragraph markers in order. A
    # chapter's or paragraph marker's `verse` is the next verse in its chapter,
    # filled in when that verse opens (`open_structure` holds the indexes).
    structure: list[UsfmStructure] = []
    open_structure: list[int] = []
    chapter = ""
    # Headings seen since the last verse opened, waiting to learn which verse
    # they introduce.
    pending: list[tuple[str, str]] = []
    current: dict[str, Any] | None = None
    # Bridge's own reports about what it did with input usfmtc only warned on.
    bridge_warnings: list[str] = []

    def close_verse(end: int) -> None:
        """Cut the verse out of the source: from its \\v to `end`, minus every
        heading inside it and minus any numberless \\v marker token."""
        nonlocal current
        if current is None:
            return
        cuts = sorted(
            [(h_at, block_end(h_at), True) for h_at in current["headings"] if h_at < end]
            + [(d_at, d_end, False) for d_at, d_end in current["drops"] if d_at < end]
        )
        start = current["start"]
        cursor = start
        after_heading = False
        # Text before the first heading is kept exactly; text after one keeps
        # only its Scripture lines: bare structure markers (`\p`, `\q1`) that
        # introduce the NEXT verse are dropped, Scripture that somehow follows a
        # heading inside the same verse is kept -- silently dropping verse text
        # would be the worst way to be wrong. Everything is
        # tracked as source ranges so the verse also knows where it came from.
        pieces: list[tuple[int, int, bool]] = []
        for c_at, c_end, is_heading in cuts:
            piece_end = c_at
            if is_heading and not after_heading:
                while piece_end > cursor and text[piece_end - 1] == "\n":
                    piece_end -= 1
            pieces.append((cursor, max(cursor, piece_end), after_heading))
            after_heading = after_heading or is_heading
            cursor = max(cursor, c_end)
        pieces.append((cursor, max(cursor, end), after_heading))
        head_pieces = [(a, b) for a, b, later in pieces if not later]
        head_text = "".join(text[a:b] for a, b in head_pieces)
        head_end = head_pieces[-1][1] if head_pieces else start
        # The span an export writes into ends where the text does, not at the
        # whitespace that separates it from the next marker.
        while head_end > start and text[head_end - 1].isspace():
            head_end -= 1
        tail: list[str] = []
        tail_spans: list[tuple[int, int]] = []
        for a, b, later in pieces:
            if not later:
                continue
            line_start = a
            for line in text[a:b].split("\n"):
                line_end = line_start + len(line)
                if line.strip() and not _MARKER_ONLY_LINE.match(line):
                    tail.append(line)
                    tail_spans.append((line_start, line_end))
                line_start = line_end + 1
        verse_text = "\n".join(head_text.split("\n") + tail).strip()
        verses.append(UsfmVerse(
            current["chapter"], current["verse"], verse_text,
            start=start, head_end=head_end, tail_spans=tuple(tail_spans),
        ))
        current = None

    def file_pending(verse: str) -> None:
        for tag, heading_text in pending:
            headings.append(UsfmHeading(chapter, verse, tag, heading_text))
        pending.clear()

    def resolve_open_structure(verse: str) -> None:
        for index in open_structure:
            item = structure[index]
            structure[index] = UsfmStructure(item.kind, item.marker, item.chapter, verse, item.offset)
        open_structure.clear()

    for kind, at, value in events:
        if kind == "c":
            close_verse(at)
            if pending and verses and verses[-1].chapter == chapter:
                # A heading after a chapter's last verse has no next verse to
                # introduce; filing it a verse early beats losing it.
                file_pending(verses[-1].verse)
            pending.clear()
            open_structure.clear()  # nothing after a chapter's last verse introduces one
            chapter = value
            if value not in chapters:
                chapters.append(value)
            structure.append(UsfmStructure("chapter", "c", chapter, "", at))
            open_structure.append(len(structure) - 1)
        elif kind == "p":
            structure.append(UsfmStructure("para", value, chapter, "", at))
            open_structure.append(len(structure) - 1)
        elif kind == "v" and not value.strip():
            # `\v` with no number (`\v \x - \xo 61:2 ...` in a real IRV Isaiah).
            # The line regex this replaced stored the text under the key "\x".
            # Its text is Scripture, so it stays with the verse it follows;
            # only the numberless marker token itself is removed.
            line = text.count("\n", 0, at) + 1
            marker = _NUMBERLESS_VERSE_AT.match(text, at)
            if current is not None:
                current["drops"].append((at, marker.end() if marker else at + 2))
                bridge_warnings.append(
                    f"{chapter}:{current['verse']}: \\v with no verse number at line {line}; "
                    "its text was kept with this verse."
                )
            else:
                bridge_warnings.append(
                    f"{chapter}: \\v with no verse number at line {line}, before the chapter's "
                    "first verse; text there belongs to no verse."
                )
        elif kind == "v":
            close_verse(at)
            file_pending(value)
            resolve_open_structure(value)
            structure.append(UsfmStructure("verse", "v", chapter, value, at))
            marker = _VERSE_MARKER_AT.match(text, at)
            current = {
                "chapter": chapter, "verse": value,
                "start": marker.end() if marker else at, "headings": [], "drops": [],
            }
        elif kind == "h":
            heading_text = marker_content(at)
            if heading_text:
                pending.append((value, heading_text))
            if current is not None:
                current["headings"].append(at)
    close_verse(len(text))
    if pending and verses and verses[-1].chapter == chapter:
        file_pending(verses[-1].verse)

    return ParsedUsfm(
        book_code=book_code,
        id_line=id_line,
        headers=tuple(headers),
        chapters=tuple(chapters),
        verses=tuple(verses),
        headings=tuple(headings),
        warnings=tuple(bridge_warnings) + tuple(_warning_text(error) for error in errors),
        structure=tuple(structure),
    )
