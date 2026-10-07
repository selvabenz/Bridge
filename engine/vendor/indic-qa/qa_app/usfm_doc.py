"""USFM line segmenter and document model for the IRV editor.

Pure functions, no file IO.  A file is a list of lines (CRLF stripped).  Every line is split
into *segments* that alternate between immutable MARKER segments (``\\v 3 ``, ``\\f + ``,
``\\ft ``, ``\\f*``, ``\\wj``...) and editable TEXT RUNS (everything between markers).  Joining
the segment texts reproduces the line exactly; this round-trip invariant is asserted on every
line and is what makes in-place editing safe: the editor only ever replaces the characters of
one text run.

Each text run carries a *context* (verse, heading, footnote_text, xref_ref, ...) assigned by a
small state machine driven by the paragraph marker at the start of the line and the inline
note / character markers, and a *stream* number so that adjacency for sandhi can be computed
across a footnote (body runs of a line are stream 0, each note is its own stream).
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
import build_dictionary as bd  # noqa: E402  (side-effect free on import)

TOKEN_RE = bd.TOKEN_RE
CANON = bd.CANON
CANON_ORDER = {code: i for i, code in enumerate(CANON)}

# --------------------------------------------------------------------------------------
# Marker grammar
# --------------------------------------------------------------------------------------

MARKER_RE = re.compile(r"\\\+?[a-z]+[0-9]*\*?")
V_NUM_RE = re.compile(r"[ \t]+\d+[a-z]?(?:-\d+[a-z]?)?")     # absorbed into \v: " 3", " 68-79", " 1a"
C_NUM_RE = re.compile(r"[ \t]+\d+")                          # absorbed into \c
CALLER_RE = re.compile(r"[ \t]+[^\s\\]")                     # note caller: " +", " -", " *", " a"
ID_RE = re.compile(r"^\\id[ \t]+([A-Z0-9]{3})\b")            # "\id HAB id HAB" -> HAB
VREF_RE = re.compile(r"^\\v[ \t]+(\d+)[a-z]?(?:-(\d+))?")
CREF_RE = re.compile(r"^\\c[ \t]+(\d+)")
FILE_RE = re.compile(r"^(\d\d)([A-Z0-9]{3})IRVTam\.SFM$", re.IGNORECASE)

NOTE_OPEN = {"f", "fe", "ef", "x", "ex"}
NOTE_DEFAULT = {"f": "footnote_text", "fe": "footnote_text", "ef": "footnote_text",
                "x": "xref_text", "ex": "xref_text"}
# note-internal markers: they set a sub-context that lasts until the next marker
NOTE_SUB = {
    "fr": "footnote_ref", "fv": "footnote_ref", "fm": "footnote_ref",
    "ft": "footnote_text", "fq": "footnote_text", "fqa": "footnote_text", "fp": "footnote_text",
    "fk": "footnote_text", "fl": "footnote_text", "fw": "footnote_text", "fdc": "footnote_text",
    "xo": "xref_ref", "xop": "xref_ref", "xk": "xref_text", "xq": "xref_text",
    "xt": "xref_text", "xta": "xref_text", "xot": "xref_text", "xnt": "xref_text", "xdc": "xref_text",
}
# character styles that carry their own context when used outside a note (\xt ... \xt* in \ip)
# dit … dit*: the cross-reference parenthesis of the IRV Hindi "(इब्रा. 1:10)"; no other corpus has it
CHAR_CONTEXT = {"xt": "xref_ref", "ior": "xref_ref", "rq": "parallel_ref", "ndx": "meta", "w": "verse", "bdit": "xref_ref"}

PARA_CONTEXT = {
    "v": "verse", "p": "verse", "m": "verse", "pi": "verse", "pi1": "verse", "pi2": "verse", "pm": "verse",
    "pmo": "verse", "pmc": "verse", "pmr": "verse", "pc": "verse", "pr": "verse", "cls": "verse", "nb": "verse",
    "mi": "verse", "q": "verse", "q1": "verse", "q2": "verse", "q3": "verse", "q4": "verse", "qr": "verse",
    "qc": "verse", "qm": "verse", "qm1": "verse", "qm2": "verse", "li": "verse", "li1": "verse", "li2": "verse",
    "lit": "verse", "tr": "verse",
    "s": "heading", "s1": "heading", "s2": "heading", "s3": "heading", "sp": "heading", "sd": "heading",
    "sd1": "heading", "qa": "heading",
    "r": "parallel_ref", "mr": "parallel_ref", "sr": "parallel_ref",
    "ms": "chapter_label", "ms1": "chapter_label", "ms2": "chapter_label", "cl": "chapter_label",
    "d": "psalm_title",
    "ip": "intro", "is": "intro", "is1": "intro", "is2": "intro", "iot": "intro", "io": "intro", "io1": "intro",
    "io2": "intro", "im": "intro", "ipi": "intro", "imi": "intro", "ipq": "intro", "ipr": "intro", "iq": "intro",
    "ib": "intro", "ili": "intro", "ili1": "intro", "iex": "intro", "imt": "intro", "imt1": "intro", "imte": "intro",
    "ie": "meta",
    "mt": "title", "mt1": "title", "mt2": "title", "mt3": "title", "mte": "title", "h": "title",
    "toc1": "title", "toc2": "title",
    "toc3": "meta", "id": "meta", "ide": "meta", "c": "meta", "ca": "meta", "cp": "meta", "b": "meta",
    "rem": "meta", "sts": "meta", "usfm": "meta", "restore": "meta", "periph": "meta",
}
PARA_STYLE = {"p", "m", "pi", "pi1", "pi2", "pm", "pmo", "pmc", "pmr", "pc", "pr", "cls", "nb", "mi",
              "q", "q1", "q2", "q3", "q4", "qr", "qc", "qm", "qm1", "qm2", "li", "li1", "li2"}
CHECKABLE = {"verse", "heading", "psalm_title", "footnote_text", "intro", "title"}
NEVER_CHECKED = {"chapter_label", "parallel_ref", "footnote_ref", "xref_ref", "xref_text", "meta", "other"}

FORBIDDEN_IN_TEXT = {"\\": "backslash", "\r": "CR", "\n": "LF", "\t": "TAB", "​": "U+200B",
                     "‌": "U+200C", "‍": "U+200D", "﻿": "U+FEFF"}


class EditError(Exception):
    """A rejected edit.  `code` is a short machine-readable reason."""

    def __init__(self, code: str, **detail):
        super().__init__(code)
        self.code = code
        self.detail = detail


# --------------------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------------------

@dataclass(slots=True)
class Seg:
    kind: str                 # "m" marker | "t" text run
    text: str                 # exact slice of the line
    start: int                # code-point offset within the line
    marker: str = ""          # marker name without backslash, "*" kept on closers, "+" kept on nested
    context: str = ""         # text runs only
    stream: int = 0           # 0 = line body, k > 0 = k-th note on the line

    @property
    def end(self) -> int:
        return self.start + len(self.text)

    def to_json(self) -> dict:
        if self.kind == "m":
            return {"k": "m", "t": self.text, "m": self.marker}
        return {"k": "t", "t": self.text, "s": self.start, "ctx": self.context, "stream": self.stream,
                "checked": self.context in CHECKABLE}


@dataclass(slots=True)
class Line:
    line_no: int              # 1-based
    raw: str                  # without CRLF
    style: str                # first marker of the line ("v", "q", "s", ...) or "text"
    para: str                 # paragraph style in force for rendering ("p", "q", "q2", ...)
    chapter: int
    verse: int
    verse_end: int | None
    ref: str
    segs: list[Seg]
    warnings: list[str]
    state_before: tuple       # (chapter, verse, verse_end, para) before this line was parsed

    @property
    def runs(self) -> list[Seg]:
        return [s for s in self.segs if s.kind == "t"]

    def run_containing(self, start: int, length: int) -> Seg | None:
        for s in self.segs:
            if s.kind == "t" and s.start <= start and start + length <= s.end:
                return s
        return None

    def to_json(self) -> dict:
        return {"line_no": self.line_no, "style": self.style, "para": self.para, "chapter": self.chapter,
                "verse": self.verse, "verse_end": self.verse_end, "ref": self.ref, "warnings": list(self.warnings),
                "segs": [s.to_json() for s in self.segs]}


@dataclass
class ParseState:
    chapter: int = 0
    verse: int = 0
    verse_end: int | None = None
    para: str = "p"

    def as_tuple(self) -> tuple:
        return (self.chapter, self.verse, self.verse_end, self.para)


@dataclass
class Book:
    code: str
    lines: list[Line]
    chapters: list[tuple[int, int]]      # chapters[n] = (first index, last index) into lines; 0 = front matter
    warnings: list[str] = field(default_factory=list)

    def chapter_of_line_index(self, idx: int) -> int:
        for n, (lo, hi) in enumerate(self.chapters):
            if lo <= idx <= hi:
                return n
        return 0

    def line(self, line_no: int) -> Line:
        if not 1 <= line_no <= len(self.lines):
            raise EditError("line_out_of_range", line_no=line_no)
        return self.lines[line_no - 1]

    def replace_line(self, line_no: int, new_raw: str) -> Line:
        """Re-parse one line in place using the parse state that preceded it.  Valid only when the
        edit cannot change chapter/verse state (run edits and validated raw-line edits)."""
        old = self.line(line_no)
        st = ParseState(*old.state_before)
        new = parse_line(self.code, new_raw, line_no, st)
        self.lines[line_no - 1] = new
        return new

    def verse_count(self) -> int:
        return sum(1 for ln in self.lines if ln.style == "v")


# --------------------------------------------------------------------------------------
# Segmenter
# --------------------------------------------------------------------------------------

def segment(line: str) -> list[Seg]:
    """Split one line into marker and text segments.  Round-trips exactly."""
    segs: list[Seg] = []
    pos = 0
    for m in MARKER_RE.finditer(line):
        if m.start() < pos:
            continue
        if m.start() > pos:
            segs.append(Seg("t", line[pos:m.start()], pos))
        name, end = m.group()[1:], m.end()
        ext = V_NUM_RE if name == "v" else C_NUM_RE if name == "c" else CALLER_RE if name in NOTE_OPEN else None
        if ext is not None:
            e = ext.match(line, end)
            if e:
                end = e.end()
        if not name.endswith("*") and end < len(line) and line[end] == " ":
            end += 1                                    # exactly one space belongs to the marker
        segs.append(Seg("m", line[m.start():end], m.start(), marker=name))
        pos = end
    if pos < len(line):
        segs.append(Seg("t", line[pos:], pos))
    joined = "".join(s.text for s in segs)
    if joined != line:                                   # cannot happen; guards the editing model
        raise AssertionError("segment round-trip failed")
    return segs


@dataclass(slots=True)
class _Frame:
    marker: str
    context: str
    is_note: bool
    stream: int


def assign_contexts(segs: list[Seg], base: str, warnings: list[str]) -> None:
    stack: list[_Frame] = []
    sub: str | None = None
    notes = 0
    for seg in segs:
        if seg.kind == "t":
            seg.context = sub or (stack[-1].context if stack else base)
            seg.stream = stack[-1].stream if stack else 0
            continue
        name = seg.marker
        in_note = any(f.is_note for f in stack)
        if name.endswith("*"):
            opener = name[:-1]
            idx = next((i for i in range(len(stack) - 1, -1, -1) if stack[i].marker == opener), None)
            if idx is not None:
                del stack[idx:]
            elif not (in_note and opener in NOTE_SUB):   # \xt ... \xt* inside a footnote is fine
                warnings.append(f"unbalanced_close:{name}")
            sub = None
        elif name in NOTE_OPEN:
            notes += 1
            sub = None
            stack.append(_Frame(name, NOTE_DEFAULT[name], True, notes))
        elif in_note and name in NOTE_SUB:
            sub = NOTE_SUB[name]
        else:
            parent = sub or (stack[-1].context if stack else base)
            stack.append(_Frame(name, CHAR_CONTEXT.get(name, parent), False, stack[-1].stream if stack else 0))
            sub = None
    for f in stack:
        if f.is_note:
            warnings.append(f"unclosed_note:{f.marker}")


def parse_line(code: str, raw: str, line_no: int, st: ParseState) -> Line:
    before = st.as_tuple()
    segs = segment(raw)
    warnings: list[str] = []
    first = segs[0].marker if segs and segs[0].kind == "m" and segs[0].start == 0 else ""
    if first == "c":
        m = CREF_RE.match(raw)
        if m:
            st.chapter, st.verse, st.verse_end = int(m.group(1)), 0, None
        else:
            warnings.append("c_without_number")
    elif first == "v":
        m = VREF_RE.match(raw)
        if m:
            st.verse, st.verse_end = int(m.group(1)), (int(m.group(2)) if m.group(2) else None)
        else:
            warnings.append("v_without_number")
    elif first in PARA_STYLE:
        st.para = first
    base = PARA_CONTEXT.get(first, "other") if first else "verse"     # a bare continuation line is verse text
    assign_contexts(segs, base, warnings)
    ref = f"{code} {st.chapter}:{st.verse}" + (f"-{st.verse_end}" if st.verse_end else "")
    return Line(line_no, raw, first or "text", st.para, st.chapter, st.verse, st.verse_end, ref, segs, warnings, before)


def parse_book(code: str, raw_lines: list[str]) -> Book:
    st = ParseState()
    lines: list[Line] = []
    chapters: list[list[int]] = [[0, -1]]
    for i, raw in enumerate(raw_lines, 1):
        if "\r" in raw or "\n" in raw:
            raise EditError("newline_in_line", line_no=i)
        line = parse_line(code, raw, i, st)
        if line.style == "c":
            chapters[-1][1] = len(lines) - 1
            chapters.append([len(lines), -1])
        lines.append(line)
    chapters[-1][1] = len(lines) - 1
    book = Book(code, lines, [tuple(c) for c in chapters])
    if code in CANON and len(chapters) - 1 != CANON[code]:
        book.warnings.append(f"chapter_count:{len(chapters) - 1}!={CANON[code]}")
    if not lines or not ID_RE.match(lines[0].raw) or ID_RE.match(lines[0].raw).group(1) != code:
        book.warnings.append("id_mismatch")
    return book


def book_code_from_filename(name: str, lang=None) -> str | None:
    """Book code from an IRV file name; `lang` (a langs.Language) supplies its own pattern, else Tamil's."""
    if lang is not None:
        return lang.book_code_from_filename(name)
    m = FILE_RE.match(name)
    return m.group(2).upper() if m else None


def tokens_with_offsets(text: str, lang=None) -> list[tuple[str, int, int]]:
    if lang is not None:
        return lang.tokens_with_offsets(text)
    return [(m.group(), m.start(), m.end()) for m in TOKEN_RE.finditer(text)]


# --------------------------------------------------------------------------------------
# Edit validation (string level)
# --------------------------------------------------------------------------------------

def check_text_allowed(new: str, forbidden: dict | None = None) -> None:
    """`forbidden` (char -> name) defaults to FORBIDDEN_IN_TEXT; a language whose words carry ZWJ /
    ZWNJ (Malayalam) passes its own table (langs.Language.forbidden_in_text)."""
    for ch, name in (forbidden or FORBIDDEN_IN_TEXT).items():
        if ch in new:
            raise EditError("markup_in_text" if ch == "\\" else "forbidden_char", char=name)


def apply_run_edit(line: Line, start: int, old: str, new: str, forbidden: dict | None = None) -> str:
    """Return the new raw line after replacing `old` at `start` with `new`.  The span must lie
    inside one text run and `new` may not contain markup or control characters."""
    check_text_allowed(new, forbidden)
    seg = line.run_containing(start, len(old))
    if seg is None:
        raise EditError("span_not_in_text_run", start=start, length=len(old))
    actual = line.raw[start:start + len(old)]
    if actual != old:
        raise EditError("text_mismatch", actual=actual)
    return line.raw[:start] + new + line.raw[start + len(old):]


def _cv_markers(raw: str) -> list[str]:
    return [s.text.rstrip() for s in segment(raw) if s.kind == "m" and s.marker in ("c", "v")]


def _leading_marker(raw: str) -> str:
    segs = segment(raw)
    return segs[0].marker if segs and segs[0].kind == "m" and segs[0].start == 0 else ""


def validate_line_edit(old_raw: str, new_raw: str, allow_style_change: bool = False,
                       forbidden: dict | None = None) -> list[str]:
    """Validate a raw single-line replacement.  Returns warnings (unknown markers etc.).  `forbidden`
    (char -> name) lists the characters an edit may not introduce; None = the Tamil set."""
    if "\r" in new_raw or "\n" in new_raw:
        raise EditError("newline_in_line")
    invisible = ("\u200b", "\u200c", "\u200d", "\ufeff", "\t") if forbidden is None \
        else tuple(ch for ch in forbidden if ch not in "\\\r\n")
    for ch in invisible:
        if ch in new_raw and ch not in old_raw:
            raise EditError("forbidden_char", char=f"U+{ord(ch):04X}")
    if _cv_markers(old_raw) != _cv_markers(new_raw):
        raise EditError("cv_markers_changed", old=_cv_markers(old_raw), new=_cv_markers(new_raw))
    if not allow_style_change and _leading_marker(old_raw) != _leading_marker(new_raw):
        raise EditError("paragraph_marker_changed", old=_leading_marker(old_raw), new=_leading_marker(new_raw))
    warnings: list[str] = []
    assign_contexts(segment(new_raw), "verse", warnings)
    return warnings


def validate_chapter_edit(old_lines: list[str], new_lines: list[str], force: bool = False) -> list[str]:
    """Validate a raw chapter replacement (may change the line count).  Returns warnings."""
    for i, raw in enumerate(new_lines, 1):
        if "\r" in raw or "\n" in raw:
            raise EditError("newline_in_line", line_no=i)
    if not new_lines:
        raise EditError("empty_chapter")
    old_first, new_first = old_lines[0] if old_lines else "", new_lines[0]
    if old_first.startswith("\\c") and _cv_markers(old_first)[:1] != _cv_markers(new_first)[:1]:
        raise EditError("chapter_marker_changed")
    extra_c = [i for i, raw in enumerate(new_lines[1:], 2) if _leading_marker(raw) == "c"]
    if extra_c:
        raise EditError("extra_chapter_marker", line=extra_c[0])
    old_v = sorted(m for raw in old_lines for m in _cv_markers(raw) if m.startswith("\\v"))
    new_v = sorted(m for raw in new_lines for m in _cv_markers(raw) if m.startswith("\\v"))
    warnings: list[str] = []
    if old_v != new_v:
        if not force:
            raise EditError("verse_markers_changed", old=len(old_v), new=len(new_v))
        warnings.append("verse_markers_changed")
    for raw in new_lines:
        assign_contexts(segment(raw), "verse", warnings)
    return warnings
