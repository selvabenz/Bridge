#!/usr/bin/env python3
"""Build a Tamil QA dictionary from the BSI 1957 (Old Version) Sathiyavedam USFM corpus.

Reads the 66 USFM files in ``usfm/`` (markers used: \\id \\mt1 \\mt2 \\mt3 \\c \\p \\v only),
cleans the text, and writes a set of tab-separated tables plus a JSON concordance into
``dictionary/`` that Tamil proofreading / QA tooling can use for:

* spell checking (frequency word list, near-miss "suspect" leads, malformed-sequence leads)
* sandhi (ஒற்று) consistency checking ("doubled" vs "bare" spelling of a word, grouped by
  the initial consonant of the word that follows it)

The source files are never modified.  All outputs are deterministic: running the script twice
produces byte-identical files.  Standard library only.

Usage (from the repository root)::

    python scripts/build_dictionary.py --expect bsi1957

Other languages have their own build script and dictionary folder; ``--lang pa`` hands over to
``scripts/pa_build.py`` (Punjabi, into dictionary_pa/) and ``--lang ml`` to ``scripts/ml_build.py``
(Malayalam, into dictionary_ml/) before anything Tamil is read or written.

See dictionary/REPORT.md (generated) for a description of every output file.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

VERSION = "1.0.0"

# --------------------------------------------------------------------------------------
# Unicode classes and regexes
# --------------------------------------------------------------------------------------

# Tamil letters, signs, OM and the AU length mark.  Deliberately excludes Tamil digits,
# numerals and symbols (U+0BE6 .. U+0BFA) so that they surface as non-word segments.
TAM = "ஂ-்ௐௗ"
TOKEN_RE = re.compile(rf"[{TAM}]+(?:-[{TAM}]+)*")
SEG_RE = re.compile(rf"[{TAM}]+(?:-[{TAM}]+)*|[^{TAM}\s]+")

CONS = "க-ஹ"                    # க .. ஹ (consonants, incl. Grantha)
VSIGN = "ா-ைொ-ௌ"      # dependent vowel signs ா .. ௌ
PULLI = "்"                          # virama ்
IVOWEL = "அ-ஔ"                  # independent vowels அ .. ஔ
AYTHAM = "ஃ"                         # ஃ

ID_RE = re.compile(r"^\\id\s+([A-Z0-9]{3})\s*$")
C_RE = re.compile(r"^\\c\s+(\d+)\s*$")
V_RE = re.compile(r"^\\v\s+(\d+)\s+(\S.*)$")
MT_RE = re.compile(r"^\\mt([123])\s+(.*)$")
P_RE = re.compile(r"^\\p(?:\s+(.*))?$")

FORBIDDEN_CHARS = {
    "\t": "TAB", "\r": "CR", " ": "NBSP", "​": "ZWSP", "‌": "ZWNJ",
    "‍": "ZWJ", "﻿": "BOM",
}

# Two-part vowel signs and the independent AU vowel in decomposed form (NFC recomposes them).
DECOMPOSED_PAIRS = ("ொ", "ோ", "ௌ", "ஔ")

# Sandhi consonant classes: the ஒற்று letter -> class code, and the class of a following word.
KSTP = {"க": "K", "ச": "S", "த": "T", "ப": "P"}
KSTP_LETTER = {v: k for k, v in KSTP.items()}
SANDHI_FINAL_RE = re.compile(r"[கசதப]்$")
# A word can carry ஒற்று only if it ends in a vowel sign, an independent vowel, a bare
# consonant (inherent a) or ய்/ழ்.  Words ending in ம் ன் ண் ல் ள் ர் ற் ங் ஞ் ந் never do.
ELIGIBLE_RE = re.compile(rf"(?:[{VSIGN}]|[{IVOWEL}]|[{CONS}]|[யழ]{PULLI})$")

SUFFIX_GROUPS = [
    ("அந்த/இந்த/எந்த", re.compile(r"^(?:அந்த|இந்த|எந்த)$")),
    ("-என்று", re.compile(r"என்று$")),
    ("-ாய்", re.compile(r"ாய்$")),
    ("-ாக", re.compile(r"ாக$")),
    ("-ான", re.compile(r"ான$")),
    ("-ை", re.compile(r"ை$")),
    ("-க்கு", re.compile(r"க்கு$")),
    ("-ுடைய", re.compile(r"ுடைய$")),
    ("-ோடு/-ோடே", re.compile(r"ோட[ுே]$")),
    ("-ே/-ோ/-ா", re.compile(r"[ேோா]$")),
    ("-ு", re.compile(r"ு$")),
    ("-ி", re.compile(r"ி$")),
    ("-ய்/-ழ்", re.compile(r"[யழ]்$")),
]

# Malformed code-point sequences (severity error) and orthographic warnings.
MALFORMED_RULES = [
    ("sign_without_consonant", "error", re.compile(rf"(?:^|[^{CONS}])[{VSIGN}{PULLI}]")),
    ("double_sign", "error", re.compile(rf"[{VSIGN}{PULLI}][{VSIGN}{PULLI}]")),
    ("au_length_mark", "error", re.compile("ௗ")),
    ("aytham_position", "error", re.compile(rf"{AYTHAM}(?![{CONS}])|{PULLI}{AYTHAM}")),
    ("unexpected_codepoint", "error", re.compile(
        "[ஂ஄஋-஍஑஖-஘஛஝஠-஢஥-஧"
        "஫-஭஺-஽௃-௅௉௎-௏௑-௖௘-௿]")),
    ("visual_au", "warning", re.compile(rf"[{CONS}]ெள(?=[{CONS}]|$)")),  # ெ+ள for ௌ
    ("sha_letter", "warning", re.compile("ஶ")),                                # ஶ
]

VOWEL_LENGTH_PAIRS = {frozenset(p) for p in [
    ("ி", "ீ"), ("ு", "ூ"), ("ெ", "ே"), ("ொ", "ோ"),
    ("அ", "ஆ"), ("இ", "ஈ"), ("உ", "ஊ"), ("எ", "ஏ"), ("ஒ", "ஓ"),
]}
CONSONANT_CONFUSABLE_PAIRS = {frozenset(p) for p in [
    ("ல", "ள"), ("ல", "ழ"), ("ள", "ழ"), ("ன", "ண"), ("ன", "ந"), ("ண", "ந"), ("ர", "ற"), ("ா", "ர"),
]}
EDIT_CLASS_RANK = {
    "consonant_confusable": 0, "vowel_length": 1, "pulli": 2, "other": 3, "transpose": 3,
    "inflection": 4, "clitic": 5,
}
POSITION_RANK = {"medial": 0, "initial": 1, "final": 2}

# Chapter counts of the 66-book Protestant canon, keyed by USFM book code.
CANON = {
    "GEN": 50, "EXO": 40, "LEV": 27, "NUM": 36, "DEU": 34, "JOS": 24, "JDG": 21, "RUT": 4,
    "1SA": 31, "2SA": 24, "1KI": 22, "2KI": 25, "1CH": 29, "2CH": 36, "EZR": 10, "NEH": 13,
    "EST": 10, "JOB": 42, "PSA": 150, "PRO": 31, "ECC": 12, "SNG": 8, "ISA": 66, "JER": 52,
    "LAM": 5, "EZK": 48, "DAN": 12, "HOS": 14, "JOL": 3, "AMO": 9, "OBA": 1, "JON": 4,
    "MIC": 7, "NAM": 3, "HAB": 3, "ZEP": 3, "HAG": 2, "ZEC": 14, "MAL": 4,
    "MAT": 28, "MRK": 16, "LUK": 24, "JHN": 21, "ACT": 28, "ROM": 16, "1CO": 16, "2CO": 13,
    "GAL": 6, "EPH": 6, "PHP": 4, "COL": 4, "1TH": 5, "2TH": 3, "1TI": 6, "2TI": 4, "TIT": 3,
    "PHM": 1, "HEB": 13, "JAS": 5, "1PE": 5, "2PE": 3, "1JN": 5, "2JN": 1, "3JN": 1, "JUD": 1,
    "REV": 22,
}
assert sum(CANON.values()) == 1189 and len(CANON) == 66


# --------------------------------------------------------------------------------------
# Data structures
# --------------------------------------------------------------------------------------

@dataclass
class Unit:
    """One text-bearing line: a verse (seg='v'), a text-bearing paragraph marker ('p'),
    or a Tamil book title ('mt1' / 'mt2')."""
    book: str
    book_idx: int
    chapter: int
    verse: int
    seg: str
    text: str
    ref: str
    file: str
    line_no: int

    @property
    def is_scripture(self) -> bool:
        return self.seg in ("v", "p")


class Log:
    """Collects everything worth reporting about the source text."""

    def __init__(self) -> None:
        self.entities: list[tuple[str, str, str]] = []     # (ref, entity, replacement)
        self.nfc_changed: list[str] = []
        self.decomposed_pairs = 0
        self.trailing_space_lines = 0
        self.char_counts: Counter = Counter()             # non-Tamil, non-space chars
        self.char_refs: dict[str, list[str]] = defaultdict(list)
        self.unexpected_lines: list[tuple[str, int, str]] = []
        self.mt3_dropped = 0
        self.non_tamil_titles: list[str] = []            # mt1/mt2 lines without a Tamil word
        self.p_with_text: list[str] = []
        self.id_lines_with_trailing_space: list[str] = []
        self.forbidden: list[tuple[str, int, str]] = []
        self.digit_units: list[tuple[str, str]] = []      # (ref, digit run)
        self.input_files: list[dict] = []


# --------------------------------------------------------------------------------------
# Parsing and cleaning
# --------------------------------------------------------------------------------------

ENTITY_RE = re.compile(r"&(?:#\d+|#x[0-9a-fA-F]+|[A-Za-z]+);")


def clean(text: str, ref: str, log: Log) -> str:
    if "&" in text:
        for m in ENTITY_RE.finditer(text):
            log.entities.append((ref, m.group(), html.unescape(m.group())))
        text = html.unescape(text)
    for pair in DECOMPOSED_PAIRS:
        log.decomposed_pairs += text.count(pair)
    nfc = unicodedata.normalize("NFC", text)
    if nfc != text:
        log.nfc_changed.append(ref)
    text = re.sub(r"\s+", " ", nfc).strip()
    for ch in text:
        if ch == " " or TOKEN_RE.fullmatch(ch) or ch == "-":
            continue
        log.char_counts[ch] += 1
        if len(log.char_refs[ch]) < 5:
            log.char_refs[ch].append(ref)
    for m in re.finditer(r"\d+", text):
        log.digit_units.append((ref, m.group()))
    return text


def parse_corpus(usfm_dir: Path, log: Log) -> list[Unit]:
    files = sorted(usfm_dir.glob("*.usfm"))
    if not files:
        sys.exit(f"no .usfm files found in {usfm_dir}")
    units: list[Unit] = []
    for idx, path in enumerate(files, 1):
        raw = path.read_bytes()
        log.input_files.append({
            "file": path.name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
        })
        text = raw.decode("utf-8")
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        book = None
        chapter = 0
        verse = 0
        expect_code = path.name[3:6]
        for ln, line in enumerate(lines, 1):
            for ch, name in FORBIDDEN_CHARS.items():
                if ch in line:
                    log.forbidden.append((path.name, ln, name))
            m = ID_RE.match(line)
            if m:
                book = m.group(1)
                if book != expect_code:
                    raise ValueError(f"{path.name}: \\id {book} does not match filename code {expect_code}")
                if line != line.rstrip():
                    log.id_lines_with_trailing_space.append(path.name)
                continue
            if book is None:
                raise ValueError(f"{path.name}:{ln}: text before \\id")
            m = C_RE.match(line)
            if m:
                c = int(m.group(1))
                if c != chapter + 1:
                    raise ValueError(f"{path.name}:{ln}: chapter {c} after chapter {chapter}")
                chapter, verse = c, 0
                continue
            m = V_RE.match(line)
            if m:
                v = int(m.group(1))
                if v != verse + 1:
                    raise ValueError(f"{path.name}:{ln}: verse {v} after verse {verse} in chapter {chapter}")
                verse = v
                if line != line.rstrip():
                    log.trailing_space_lines += 1
                ref = f"{book} {chapter}:{verse}"
                units.append(Unit(book, idx, chapter, verse, "v", clean(m.group(2), ref, log), ref, path.name, ln))
                continue
            m = MT_RE.match(line)
            if m:
                level = m.group(1)
                if level == "3":
                    log.mt3_dropped += 1
                    continue
                ref = f"{book} mt{level}"
                if not TOKEN_RE.search(m.group(2)):
                    # e.g. HAG: "\mt2 The Book of Haggai" (English title on the wrong level)
                    log.non_tamil_titles.append(f"{path.name}:{ln} \\mt{level} {m.group(2).strip()}")
                    continue
                units.append(Unit(book, idx, 0, 0, f"mt{level}", clean(m.group(2), ref, log), ref, path.name, ln))
                continue
            m = P_RE.match(line)
            if m:
                if m.group(1) and m.group(1).strip():
                    ref = f"{book} {chapter}:{verse}"
                    log.p_with_text.append(f"{path.name}:{ln} -> {ref}")
                    units.append(Unit(book, idx, chapter, verse, "p", clean(m.group(1), ref, log), ref, path.name, ln))
                continue
            log.unexpected_lines.append((path.name, ln, line[:80]))
        if book in CANON and chapter != CANON[book]:
            raise ValueError(f"{path.name}: {chapter} chapters, canon has {CANON[book]}")
    return units


# --------------------------------------------------------------------------------------
# Tokenisation
# --------------------------------------------------------------------------------------

def segments(text: str):
    """Yield (segment, is_token) in order: Tamil tokens and runs of everything else."""
    for m in SEG_RE.finditer(text):
        s = m.group()
        yield s, bool(TOKEN_RE.fullmatch(s))


def bigrams(text: str):
    """Yield (w1, w2, punct) once per token w1.  w2 is the next token when only whitespace
    separates them, otherwise None; punct is the intervening non-word run ('' at line end)."""
    prev = None
    for s, is_tok in segments(text):
        if is_tok:
            if prev is not None:
                yield prev, s, ""
            prev = s
        else:
            if prev is not None:
                yield prev, None, s
            prev = None
    if prev is not None:
        yield prev, None, ""


def tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(text)


# --------------------------------------------------------------------------------------
# Near-miss helpers
# --------------------------------------------------------------------------------------

def edit1(a: str, b: str):
    """If optimal-string-alignment distance(a, b) == 1 return (op, frm, to, index) else None."""
    if a == b:
        return None
    la, lb = len(a), len(b)
    if la == lb:
        diffs = [i for i in range(la) if a[i] != b[i]]
        if len(diffs) == 1:
            i = diffs[0]
            return ("sub", a[i], b[i], i)
        if (len(diffs) == 2 and diffs[1] == diffs[0] + 1
                and a[diffs[0]] == b[diffs[1]] and a[diffs[1]] == b[diffs[0]]):
            i = diffs[0]
            return ("transpose", a[i:i + 2], b[i:i + 2], i)
        return None
    if la + 1 == lb:
        i = 0
        while i < la and a[i] == b[i]:
            i += 1
        if a[i:] == b[i + 1:]:
            return ("ins", "", b[i], i)
        return None
    if la == lb + 1:
        r = edit1(b, a)
        if r:
            return ("del", r[2], "", r[3])
        return None
    return None


def deletions(w: str) -> set[str]:
    return {w[:i] + w[i + 1:] for i in range(len(w))}


def edit_position(index: int, length: int) -> str:
    if index == 0:
        return "initial"
    if index >= length - 2:
        return "final"
    return "medial"


def edit_class(op: str, frm: str, to: str, position: str) -> str:
    if op == "sub":
        pair = frozenset((frm, to))
        if pair in CONSONANT_CONFUSABLE_PAIRS:
            return "consonant_confusable"
        if pair in VOWEL_LENGTH_PAIRS:
            return "vowel_length"
        if position == "final" and frm in "னளர" and to in "னளர":
            return "inflection"
        return "other"
    if op in ("ins", "del"):
        ch = frm or to
        if ch == PULLI:
            return "pulli"
        if position == "final" and ch in "ேோா":
            return "clitic"
        return "other"
    return "transpose"


# --------------------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------------------

class Build:
    def __init__(self, units: list[Unit], log: Log, args: argparse.Namespace) -> None:
        self.units = units
        self.log = log
        self.args = args
        self.scripture = [u for u in units if u.is_scripture]
        self.titles = [u for u in units if not u.is_scripture]

    # -- counting -------------------------------------------------------------------
    def count_words(self) -> None:
        self.count: Counter = Counter()
        self.title_count: Counter = Counter()
        self.books: dict[str, set] = defaultdict(set)
        self.vrefs: dict[str, list[str]] = defaultdict(list)
        self.trefs: dict[str, list[str]] = defaultdict(list)
        for u in self.scripture:
            for t in tokens(u.text):
                self.count[t] += 1
                self.books[t].add(u.book)
                self.vrefs[t].append(u.ref)
        for u in self.titles:
            for t in tokens(u.text):
                self.title_count[t] += 1
                self.trefs[t].append(u.ref)
        self.all_words = sorted(set(self.count) | set(self.title_count))
        self.total_tokens = sum(self.count.values())

    # -- sandhi ---------------------------------------------------------------------
    def classify_kstp(self) -> None:
        """Pass 1: decide whether each க்/ச்/த்/ப்-final surface form is bare+ஒற்று ('sandhi')
        or a word with a lexical final consonant such as a transliterated name ('lexical')."""
        self.n_match: Counter = Counter()
        self.n_other: Counter = Counter()
        for u in self.scripture:
            for w1, w2, _ in bigrams(u.text):
                if SANDHI_FINAL_RE.search(w1):
                    cls = KSTP[w1[-2]]
                    if w2 and KSTP.get(w2[0]) == cls:
                        self.n_match[w1] += 1
                    else:
                        self.n_other[w1] += 1
        self.kind: dict[str, str] = {}
        for w in self.all_words:
            if not SANDHI_FINAL_RE.search(w):
                continue
            bare = w[:-2]
            nm, no = self.n_match[w], self.n_other[w]
            if nm > no or (no <= 2 and self.count[bare] >= 5):
                self.kind[w] = "sandhi"
            else:
                self.kind[w] = "lexical"

    def lemma_of(self, w: str) -> str:
        return w[:-2] if self.kind.get(w) == "sandhi" else w

    def build_sandhi_tables(self) -> None:
        """Pass 2: with/without counts per (bare form, following consonant class)."""
        self.with_: Counter = Counter()
        self.without: Counter = Counter()
        self.with_refs: dict[tuple, list[str]] = defaultdict(list)
        self.without_refs: dict[tuple, list[str]] = defaultdict(list)
        self.anomalies: list[dict] = []
        for u in self.scripture:
            for w1, w2, punct in bigrams(u.text):
                if SANDHI_FINAL_RE.search(w1):
                    if self.kind[w1] == "lexical":
                        continue
                    cls = KSTP[w1[-2]]
                    bare = w1[:-2]
                    if w2 and KSTP.get(w2[0]) == cls:
                        key = (bare, cls)
                        self.with_[key] += 1
                        if len(self.with_refs[key]) < 5:
                            self.with_refs[key].append(u.ref)
                    else:
                        if w2 is None:
                            typ = "punct" if punct else "verse-end"
                        elif w2[0] in KSTP:
                            typ = "wrongclass"
                        else:
                            typ = "nonKSTP"
                        self.anomalies.append({
                            "ref": u.ref, "token": w1, "next": w2 if w2 is not None else punct,
                            "type": typ, "bare": bare, "bare_count": self.count[bare],
                            "form_match_n": self.n_match[w1], "form_other_n": self.n_other[w1],
                        })
                elif w2 and w2[0] in KSTP and ELIGIBLE_RE.search(w1):
                    key = (w1, KSTP[w2[0]])
                    self.without[key] += 1
                    if len(self.without_refs[key]) < 5:
                        self.without_refs[key].append(u.ref)
        self.sandhi_keys = sorted(set(self.with_) | set(self.without))

        # Suffix summary
        def group_of(bare: str) -> str:
            for name, rx in SUFFIX_GROUPS:
                if rx.search(bare):
                    return name
            return "-" + bare[-1]

        agg: dict[tuple, dict] = {}
        for key in self.sandhi_keys:
            bare, cls = key
            g = (group_of(bare), cls)
            d = agg.setdefault(g, {"with": 0, "without": 0, "forms": set(), "top_with": None, "top_without": None})
            d["with"] += self.with_[key]
            d["without"] += self.without[key]
            d["forms"].add(bare)
            if self.with_[key] and (d["top_with"] is None or self.with_[key] > self.with_[d["top_with"]]):
                d["top_with"] = key
            if self.without[key] and (d["top_without"] is None or self.without[key] > self.without[d["top_without"]]):
                d["top_without"] = key
        self.suffix_summary = agg

        # Lemma counts (surface forms folded onto their sandhi-stripped form)
        self.lemma_count: Counter = Counter()
        self.lemma_surfaces: dict[str, list[str]] = defaultdict(list)
        for w in self.all_words:
            lemma = self.lemma_of(w)
            self.lemma_count[lemma] += self.count[w]
            self.lemma_surfaces[lemma].append(w)

    # -- suspects -------------------------------------------------------------------
    def find_suspects(self) -> None:
        freq_min, rare_max = self.args.freq_min, self.args.rare_max
        frequent = [l for l, n in self.lemma_count.items() if n >= freq_min]
        rare = [l for l, n in self.lemma_count.items() if 1 <= n <= rare_max]
        index: dict[str, set] = defaultdict(set)
        for f in frequent:
            index[f].add(f)
            for d in deletions(f):
                index[d].add(f)
        rows = []
        for r in sorted(rare):
            cands = set(index.get(r, ()))
            for d in deletions(r):
                cands |= index.get(d, set())
            cands.discard(r)
            hits = []
            for c in cands:
                e = edit1(r, c)
                if e:
                    hits.append((c, e))
            hits.sort(key=lambda h: (-self.lemma_count[h[0]], h[0]))
            surfaces = self.lemma_surfaces[r]
            surface = r if r in self.count else surfaces[0]
            ref = (self.vrefs.get(surface) or self.trefs.get(surface) or [""])[0]
            for c, (op, frm, to, i) in hits[:3]:
                pos = edit_position(i, len(r))
                cls = edit_class(op, frm, to, pos)
                priority = EDIT_CLASS_RANK[cls] * 3 + POSITION_RANK[pos]
                rows.append({
                    "rare": r, "rare_count": self.lemma_count[r], "lemma_count": self.lemma_count[r],
                    "candidate": c, "candidate_count": self.lemma_count[c], "op": op, "from": frm,
                    "to": to, "position": pos, "edit_class": cls, "priority": priority, "rare_ref": ref,
                })
        rows.sort(key=lambda d: (d["priority"], -d["candidate_count"], d["rare"], d["candidate"]))
        self.suspects_total = len(rows)
        self.suspects = rows[: self.args.max_suspects]
        self.suspect_of: dict[str, str] = {}
        for d in self.suspects:
            self.suspect_of.setdefault(d["rare"], d["candidate"])
        self.n_frequent, self.n_rare = len(frequent), len(rare)

    # -- malformed ------------------------------------------------------------------
    def check_malformed(self) -> None:
        self.malformed: dict[str, tuple[str, str]] = {}
        for w in self.all_words:
            for name, severity, rx in MALFORMED_RULES:
                if rx.search(w):
                    self.malformed[w] = (name, severity)
                    break

    # -- extra words ----------------------------------------------------------------
    def load_extra_words(self, path: Path) -> None:
        self.extra_words: list[str] = []
        self.extra_rejected: list[str] = []
        if not path.exists():
            return
        for line in path.read_text(encoding="utf-8").splitlines():
            s = unicodedata.normalize("NFC", line.strip())
            if not s or s.startswith("#"):
                continue
            if TOKEN_RE.fullmatch(s):
                self.extra_words.append(s)
            else:
                self.extra_rejected.append(s)
        self.extra_words = sorted(set(self.extra_words))

    # -- expectations ---------------------------------------------------------------
    def word_count(self, w: str) -> int:
        return self.count[w]

    def sandhi_kind_counts(self) -> Counter:
        return Counter(self.kind.values())

    def anomaly_type_counts(self) -> Counter:
        return Counter(a["type"] for a in self.anomalies)


# --------------------------------------------------------------------------------------
# Writers
# --------------------------------------------------------------------------------------

def write_tsv(path: Path, header: list[str], rows) -> int:
    n = 0
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write("\t".join(header) + "\n")
        for row in rows:
            cells = ["" if c is None else str(c) for c in row]
            if len(cells) != len(header):
                raise ValueError(f"{path.name}: row has {len(cells)} cells, header has {len(header)}")
            for c in cells:
                if "\t" in c or "\n" in c or "\r" in c:
                    raise ValueError(f"{path.name}: control character in cell {c!r}")
            f.write("\t".join(cells) + "\n")
            n += 1
    return n


def pct(a: int, b: int) -> str:
    return f"{100.0 * a / b:.1f}" if b else ""


def write_outputs(b: Build, out: Path) -> dict[str, int]:
    rows_written: dict[str, int] = {}

    # verses.tsv
    order = {"v": 0, "p": 1}
    rows_written["verses.tsv"] = write_tsv(
        out / "verses.tsv", ["ref", "book", "book_idx", "chapter", "verse", "seg", "text"],
        ((u.ref, u.book, u.book_idx, u.chapter, u.verse, u.seg, u.text) for u in b.units))

    # words.tsv
    def word_row(w: str):
        kstp_final = KSTP[w[-2]] if SANDHI_FINAL_RE.search(w) else ""
        kind = b.kind.get(w, "")
        refs = b.vrefs.get(w) or b.trefs.get(w) or []
        uniq = []
        for r in refs:
            if r not in uniq:
                uniq.append(r)
            if len(uniq) == 5:
                break
        return (
            w, b.count[w], b.title_count[w], len(b.books[w]), refs[0] if refs else "", "; ".join(uniq),
            len(w), 1 if b.count[w] == 1 else 0, kstp_final, kind, w[:-2] if kind == "sandhi" else "",
            b.lemma_count[b.lemma_of(w)], b.malformed.get(w, ("", ""))[0], b.suspect_of.get(b.lemma_of(w), ""),
        )
    words_sorted = sorted(b.all_words, key=lambda w: (-b.count[w], w))
    rows_written["words.tsv"] = write_tsv(
        out / "words.tsv",
        ["word", "count", "title_count", "n_books", "first_ref", "sample_refs", "length", "hapax",
         "kstp_final", "kstp_kind", "bare", "lemma_count", "malformed_rule", "suspect_of"],
        (word_row(w) for w in words_sorted))

    # wordlist.txt / wordlist_bare.txt
    wordlist = sorted(set(b.all_words) | set(b.extra_words))
    (out / "wordlist.txt").write_text("\n".join(wordlist) + "\n", encoding="utf-8", newline="\n")
    rows_written["wordlist.txt"] = len(wordlist)
    bare_list = sorted({b.lemma_of(w) for w in b.all_words} | set(b.extra_words))
    (out / "wordlist_bare.txt").write_text("\n".join(bare_list) + "\n", encoding="utf-8", newline="\n")
    rows_written["wordlist_bare.txt"] = len(bare_list)

    # sandhi_pairs.tsv
    def verdict(wi: int, wo: int) -> str:
        if wi and wo:
            return "mixed"
        return "always_doubled" if wi else "never_doubled"
    pair_rows = []
    for key in b.sandhi_keys:
        wi, wo = b.with_[key], b.without[key]
        pair_rows.append((key[0], key[1], wi, wo, wi + wo, pct(wi, wi + wo), verdict(wi, wo),
                          (b.with_refs[key] or [""])[0], (b.without_refs[key] or [""])[0]))
    pair_rows.sort(key=lambda r: (-r[4], r[0], r[1]))
    rows_written["sandhi_pairs.tsv"] = write_tsv(
        out / "sandhi_pairs.tsv",
        ["bare", "next_initial", "with", "without", "total", "pct_with", "verdict", "with_ref", "without_ref"],
        pair_rows)

    # sandhi_inconsistent.tsv
    inc_rows = []
    for key in b.sandhi_keys:
        wi, wo = b.with_[key], b.without[key]
        if wi and wo:
            if wi > wo:
                maj, maj_n, min_n, min_refs, maj_ref = "doubled", wi, wo, b.without_refs[key], b.with_refs[key][0]
            elif wo > wi:
                maj, maj_n, min_n, min_refs, maj_ref = "bare", wo, wi, b.with_refs[key], b.without_refs[key][0]
            else:
                maj, maj_n, min_n, min_refs, maj_ref = "tie", wi, wo, b.with_refs[key], b.without_refs[key][0]
            strength = "strong" if min_n <= 2 and maj_n >= 10 else "weak"
            inc_rows.append((key[0], key[1], maj, maj_n, min_n, strength, "; ".join(min_refs[:5]), maj_ref))
    inc_rows.sort(key=lambda r: (r[4], -r[3], r[0], r[1]))
    rows_written["sandhi_inconsistent.tsv"] = write_tsv(
        out / "sandhi_inconsistent.tsv",
        ["bare", "next_initial", "majority", "majority_n", "minority_n", "strength", "minority_refs", "majority_ref"],
        inc_rows)

    # sandhi_anomalies.tsv (already in canonical order)
    rows_written["sandhi_anomalies.tsv"] = write_tsv(
        out / "sandhi_anomalies.tsv",
        ["ref", "token", "next", "type", "bare", "bare_count", "form_match_n", "form_other_n"],
        ((a["ref"], a["token"], a["next"], a["type"], a["bare"], a["bare_count"], a["form_match_n"], a["form_other_n"])
         for a in b.anomalies))

    # sandhi_suffix_summary.tsv
    def example(key) -> str:
        if key is None:
            return ""
        return f"{key[0]} ({(b.with_refs[key] or b.without_refs[key] or [''])[0]})"
    suf_rows = []
    for (group, cls), d in b.suffix_summary.items():
        suf_rows.append((group, cls, d["with"], d["without"], pct(d["with"], d["with"] + d["without"]),
                         len(d["forms"]),
                         f"{d['top_with'][0]} ({b.with_refs[d['top_with']][0]})" if d["top_with"] else "",
                         f"{d['top_without'][0]} ({b.without_refs[d['top_without']][0]})" if d["top_without"] else ""))
    suf_rows.sort(key=lambda r: (-(r[2] + r[3]), r[0], r[1]))
    rows_written["sandhi_suffix_summary.tsv"] = write_tsv(
        out / "sandhi_suffix_summary.tsv",
        ["suffix_group", "next_initial", "with", "without", "pct_with", "n_bare_forms", "example_with", "example_without"],
        suf_rows)

    # final_consonant_words.tsv
    lex_rows = [(w, b.count[w], b.n_match[w], b.n_other[w], (b.vrefs.get(w) or b.trefs.get(w) or [""])[0])
                for w, k in b.kind.items() if k == "lexical"]
    lex_rows.sort(key=lambda r: (-r[1], r[0]))
    rows_written["final_consonant_words.tsv"] = write_tsv(
        out / "final_consonant_words.tsv", ["word", "count", "n_match", "n_other", "sample_ref"], lex_rows)

    # suspect_words.tsv
    rows_written["suspect_words.tsv"] = write_tsv(
        out / "suspect_words.tsv",
        ["rare", "rare_count", "lemma_count", "candidate", "candidate_count", "op", "from", "to", "position",
         "edit_class", "priority", "rare_ref"],
        ((d["rare"], d["rare_count"], d["lemma_count"], d["candidate"], d["candidate_count"], d["op"], d["from"],
          d["to"], d["position"], d["edit_class"], d["priority"], d["rare_ref"]) for d in b.suspects))

    # malformed_words.tsv
    mal_rows = [(w, b.count[w] + b.title_count[w], rule, sev, (b.vrefs.get(w) or b.trefs.get(w) or [""])[0])
                for w, (rule, sev) in b.malformed.items()]
    mal_rows.sort(key=lambda r: (r[2], -r[1], r[0]))
    rows_written["malformed_words.tsv"] = write_tsv(
        out / "malformed_words.tsv", ["word", "count", "rule", "severity", "sample_ref"], mal_rows)

    # concordance.json
    conc = {w: {"n": b.count[w] + b.title_count[w], "refs": b.vrefs.get(w, []) + b.trefs.get(w, [])}
            for w in b.all_words}
    with (out / "concordance.json").open("w", encoding="utf-8", newline="\n") as f:
        json.dump(conc, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        f.write("\n")
    rows_written["concordance.json"] = len(conc)
    return rows_written


EXTRA_WORDS_SEED = """\
# extra_words.txt - supplement lexicon merged into wordlist.txt and wordlist_bare.txt on
# every rebuild (python scripts/build_dictionary.py).  One word per line, UTF-8; blank lines
# and lines starting with # are ignored; entries are NFC-normalised and must be Tamil words.
#
# Use it for forms that are correct in the text under review but absent from the BSI 1957
# Old Version.  The main case for the Tamil IRV is the divine name: the OV writes கர்த்தர்
# where the IRV writes யெகோவா, so every inflection of யெகோவா used in the IRV should be
# listed here (take them from the IRV's own frequency list once it is checked).  Examples,
# commented out until verified against the IRV text:
# யெகோவா
# யெகோவாவை
# யெகோவாவின்
# யெகோவாவுக்கு
# யெகோவாவுடைய
# யெகோவாவாகிய
"""


def write_build_info(b: Build, out: Path, rows_written: dict[str, int]) -> None:
    info = {
        "script": "scripts/build_dictionary.py",
        "script_version": VERSION,
        "corpus": "Tamil BSI Old Version 1957 (Sathiyavedam), TFBF digitisation, USFM",
        "parameters": {
            "freq_min": b.args.freq_min, "rare_max": b.args.rare_max, "max_suspects": b.args.max_suspects,
            "expect": b.args.expect,
        },
        "inputs": b.log.input_files,
        "outputs_rows": rows_written,
        "summary": {
            "books": len({u.book for u in b.units}),
            "chapters": sum(CANON[bk] for bk in {u.book for u in b.units}),
            "verses": sum(1 for u in b.units if u.seg == "v"),
            "tokens": b.total_tokens,
            "unique_forms": len(b.count),
            "hapax": sum(1 for n in b.count.values() if n == 1),
        },
    }
    with (out / "build_info.json").open("w", encoding="utf-8", newline="\n") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)
        f.write("\n")


def md_table(header: list[str], rows, limit: int | None = None) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for i, r in enumerate(rows):
        if limit is not None and i >= limit:
            break
        lines.append("| " + " | ".join(str(c).replace("|", "\\|") for c in r) + " |")
    return "\n".join(lines)


def write_report(b: Build, out: Path, rows_written: dict[str, int]) -> None:
    log = b.log
    count = b.count
    freq_bands = [(1, 1), (2, 4), (5, 9), (10, 19), (20, 99), (100, 999), (1000, 10 ** 9)]
    band_rows = []
    for lo, hi in freq_bands:
        n = sum(1 for c in count.values() if lo <= c <= hi)
        band_rows.append((f"{lo}" if lo == hi else (f"{lo}-{hi}" if hi < 10 ** 9 else f"{lo}+"), n))
    kinds = b.sandhi_kind_counts()
    atypes = b.anomaly_type_counts()
    with_tokens = sum(b.with_.values())
    without_tokens = sum(b.without.values())
    both = sum(1 for k in b.sandhi_keys if b.with_[k] and b.without[k])
    top_words = sorted(count.items(), key=lambda kv: (-kv[1], kv[0]))[:25]
    char_rows = sorted(log.char_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    entity_rows = Counter((e[1], e[2]) for e in log.entities)
    entity_refs = defaultdict(list)
    for ref, ent, _ in log.entities:
        entity_refs[ent].append(ref)
    visual_au = sorted(w for w, (rule, _) in b.malformed.items() if rule == "visual_au")
    errors = sorted((w, rule) for w, (rule, sev) in b.malformed.items() if sev == "error")
    inc_strong = [k for k in b.sandhi_keys if b.with_[k] and b.without[k]
                  and min(b.with_[k], b.without[k]) <= 2 and max(b.with_[k], b.without[k]) >= 10]

    def inc_row(key):
        wi, wo = b.with_[key], b.without[key]
        maj = "doubled" if wi > wo else ("bare" if wo > wi else "tie")
        minority_refs = b.without_refs[key] if wi > wo else b.with_refs[key]
        return (key[0], key[1], maj, max(wi, wo), min(wi, wo), "; ".join(minority_refs[:3]))
    inc_rows = sorted((inc_row(k) for k in inc_strong), key=lambda r: (r[4], -r[3], r[0], r[1]))

    suf_rows = sorted(((g, c, d["with"], d["without"], pct(d["with"], d["with"] + d["without"]), len(d["forms"]))
                       for (g, c), d in b.suffix_summary.items()),
                      key=lambda r: (-(r[2] + r[3]), r[0], r[1]))
    susp_class = Counter(d["edit_class"] for d in b.suspects)

    L: list[str] = []
    A = L.append
    A("# Tamil QA dictionary built from the BSI 1957 Old Version (Sathiyavedam)\n")
    A("Generated by `scripts/build_dictionary.py` (version " + VERSION + "). Input: the 66 USFM files in `usfm/`; "
      "the source files are never modified. Every output is deterministic (no timestamps), so a rebuild "
      "can be diffed. Input hashes and parameters are in `build_info.json`.\n")
    A("The text is the BSI Old Version of 1957, digitised by The Free Bible Foundation (public domain in "
      "India per the repository README; MIT licence on the digitisation). It is the base text that the "
      "Tamil IRV revises, which is what makes its vocabulary a useful reference lexicon for IRV QA.\n")

    A("## 1. Files\n")
    A("All `.tsv` files are UTF-8 without BOM, LF line endings, tab-separated with a header row and **no "
      "quoting** (no field contains a tab or newline; verse text does contain `\"`). Read them with "
      "`csv.reader(f, delimiter='\\t', quoting=csv.QUOTE_NONE)` or `pandas.read_csv(..., sep='\\t', "
      "quoting=3)`. Sort order inside the files is Python code-point order, which is not traditional "
      "Tamil collation; it is stable and fine for machine lookup.\n")
    A(md_table(["File", "Rows", "What it is / how to use it"], [
        ("verses.tsv", rows_written["verses.tsv"],
         "`ref, book, book_idx, chapter, verse, seg, text`. Cleaned NFC text of every verse (`seg=v`), the one "
         "text-bearing `\\p` line (`seg=p`, Hab 3:19 colophon) and the Tamil book titles (`seg=mt1/mt2`, "
         "chapter and verse 0). Use it to show context for any lead."),
        ("words.tsv", rows_written["words.tsv"],
         "One row per surface form. `count` is occurrences in verse text, `title_count` in book titles, "
         "`n_books` the number of books it occurs in, `first_ref`/`sample_refs` where to find it, `hapax` = 1 "
         "if it occurs once, `kstp_final` K/S/T/P when the form ends in க்/ச்/த்/ப், `kstp_kind` = `sandhi` "
         "(bare word + ஒற்று) or `lexical` (a name or loanword with a real final consonant), `bare` the "
         "sandhi-stripped form, `lemma_count` the count of the form with all its sandhi variants folded in, "
         "`malformed_rule` and `suspect_of` (the frequent form it is one edit away from) flag leads. "
         "For spell checking: a word absent from this list, or present with a tiny `lemma_count`, is a lead; "
         "a word with a high count is house practice."),
        ("wordlist.txt", rows_written["wordlist.txt"],
         "Plain lexicon, one surface form per line, plus everything in `extra_words.txt`. Load into a set "
         "and test membership."),
        ("wordlist_bare.txt", rows_written["wordlist_bare.txt"],
         "Same lexicon with the ஒற்று stripped from sandhi forms (lexical-final names kept). Use it when the "
         "text under review joins or splits sandhi differently."),
        ("sandhi_pairs.tsv", rows_written["sandhi_pairs.tsv"],
         "`bare, next_initial, with, without, total, pct_with, verdict, with_ref, without_ref`. For every "
         "bare word and following-consonant class (K/S/T/P = next word starts with க/ச/த/ப) how often the OV "
         "writes the doubled form (`with`, e.g. அந்தக் கூடாரம்) and the bare form (`without`, e.g. அந்த "
         "கூடாரம்). `verdict` is always_doubled / never_doubled / mixed. This is the lookup for the sandhi "
         "sweep: when the text under review has word W before a K/S/T/P word, look up (W, class) and compare."),
        ("sandhi_inconsistent.tsv", rows_written["sandhi_inconsistent.tsv"],
         "The `mixed` keys only, with the majority spelling, the counts and up to five references for the "
         "minority spelling. `strength=strong` (minority <= 2, majority >= 10) marks probable OV typos."),
        ("sandhi_anomalies.tsv", rows_written["sandhi_anomalies.tsv"],
         "Sandhi-kind tokens whose next word does not start with the matching consonant class "
         "(`wrongclass`, e.g. -த் before க-), starts with a non-K/S/T/P letter (`nonKSTP`), or is followed by "
         "punctuation (`punct`). Each row is a probable typo in the OV; `bare_count`, `form_match_n` and "
         "`form_other_n` let you dismiss residual names quickly."),
        ("sandhi_suffix_summary.tsv", rows_written["sandhi_suffix_summary.tsv"],
         "Doubling rate per word-ending group (e.g. `-ை`, `-ாய்`, `-க்கு`, `அந்த/இந்த/எந்த`) and following "
         "class. Gives the OV house norm for weighting deviations in another text."),
        ("final_consonant_words.tsv", rows_written["final_consonant_words.tsv"],
         "Forms classified `lexical`: names and loanwords that genuinely end in க்/ச்/த்/ப் (யோவாப், மோவாப், "
         "காத் ...). A QA sweep must not treat these as sandhi forms."),
        ("suspect_words.tsv", rows_written["suspect_words.tsv"],
         "Near-miss leads inside the OV itself: rare lemmas (lemma_count <= " + str(b.args.rare_max) +
         ") that are one edit (substitution, insertion, deletion or adjacent transposition of a code point) "
         "away from a frequent lemma (lemma_count >= " + str(b.args.freq_min) + "). `edit_class` and "
         "`priority` (lower = stronger) rank them: consonant confusions (ல/ள/ழ, ன/ண/ந, ர/ற) and vowel-length "
         "slips first, final-position inflection/clitic differences last. These are leads, not findings."),
        ("malformed_words.tsv", rows_written["malformed_words.tsv"],
         "Forms with impossible Tamil code-point sequences (severity `error`) or orthographic warnings "
         "(`visual_au` = ெ+ள typed instead of ௌ; `sha_letter` = ஶ). The rules are reusable on any text."),
        ("concordance.json", rows_written["concordance.json"],
         "`{word: {n, refs}}` for every form; `refs` lists one entry per occurrence (a verse where the word "
         "occurs twice is listed twice) in canonical order; title occurrences appear as `BOOK mt1`."),
        ("extra_words.txt", len(b.extra_words),
         "Supplement lexicon merged into the two wordlists on rebuild. Seeded with a commented explanation "
         "and IRV divine-name examples."),
        ("build_info.json", "-", "Script version, parameters, SHA-256 and size of every input file, row counts."),
    ]))
    A("")

    A("## 2. Corpus statistics\n")
    A(md_table(["Measure", "Value"], [
        ("Books", len({u.book for u in b.units})),
        ("Chapters", 1189),
        ("Verses (`\\v`)", sum(1 for u in b.units if u.seg == "v")),
        ("Text-bearing `\\p` lines", len(log.p_with_text)),
        ("Tamil title lines (`\\mt1`/`\\mt2`)", len(b.titles)),
        ("English title lines dropped (`\\mt3`)", log.mt3_dropped),
        ("Tokens in verse text (incl. the `\\p` line)", b.total_tokens),
        ("Unique surface forms in verse text", len(count)),
        ("Forms occurring only in titles", len(b.all_words) - len(count)),
        ("Hapax legomena (count = 1)", sum(1 for n in count.values() if n == 1)),
        ("Forms with count >= 20", sum(1 for n in count.values() if n >= 20)),
        ("Longest form (code points)", max(len(w) for w in count)),
        ("Lemmas (sandhi variants folded)", len(b.lemma_count)),
    ]))
    A("")
    A("Frequency bands (surface forms by count):\n")
    A(md_table(["count", "forms"], band_rows))
    A("")
    A("Most frequent forms:\n")
    A(md_table(["word", "count"], top_words))
    A("")

    A("## 3. Source anomalies handled in the pipeline\n")
    A("None of these were changed in `usfm/`; they are normalised or recorded in the outputs.\n")
    items = []
    for ent, rep in sorted(entity_rows):
        refs = entity_refs[ent]
        items.append(f"HTML entity `{ent}` decoded to `{rep}`: {entity_rows[(ent, rep)]} times "
                     f"({', '.join(refs[:6])}{', ...' if len(refs) > 6 else ''}).")
    items.append(f"Verse lines with trailing spaces: {log.trailing_space_lines} (stripped).")
    items.append(f"Lines changed by NFC normalisation: {len(log.nfc_changed)}; decomposed two-part vowel "
                 f"sequences before NFC: {log.decomposed_pairs}. NFC is applied defensively anyway.")
    items.append("Digits inside verse text: " + "; ".join(f"`{d}` at {r}" for r, d in log.digit_units) +
                 ". These are merged verses (the next verse's number kept inline); they are left as "
                 "punctuation runs, so they break word adjacency but never enter the word list.")
    items.append("Text-bearing `\\p` line: " + "; ".join(log.p_with_text) +
                 " (a closing colophon after Hab 3:19, kept as `seg=p` with that reference).")
    if log.id_lines_with_trailing_space:
        items.append("`\\id` line with trailing space: " + ", ".join(log.id_lines_with_trailing_space) +
                     " (that book also has its titles in the order mt2, mt1, mt2; kept as-is).")
    if log.non_tamil_titles:
        items.append("Title lines without a Tamil word, dropped like `\\mt3`: " +
                     "; ".join(f"`{t}`" for t in log.non_tamil_titles) +
                     " (an English title on the wrong marker level).")
    if log.forbidden:
        items.append("Forbidden characters found: " + "; ".join(f"{f}:{ln} {n}" for f, ln, n in log.forbidden[:20]))
    else:
        items.append("No CR, tab, BOM, NBSP, ZWSP, ZWNJ or ZWJ anywhere in the files.")
    if log.unexpected_lines:
        items.append("Unexpected lines (not one of the seven known markers): " +
                     "; ".join(f"{f}:{ln} `{t}`" for f, ln, t in log.unexpected_lines[:20]))
    else:
        items.append("Every line is one of `\\id \\mt1 \\mt2 \\mt3 \\c \\p \\v`; no other marker occurs.")
    if visual_au:
        items.append(f"Visual AU (ெ+ள typed for ௌ), {len(visual_au)} forms: " + ", ".join(visual_au) +
                     ". Listed in `malformed_words.tsv` as warnings.")
    if errors:
        items.append(f"Malformed code-point sequences: {len(errors)} forms: " +
                     ", ".join(f"{w} ({r})" for w, r in errors[:30]))
    else:
        items.append("No malformed Tamil code-point sequences (vowel sign without consonant, double signs, "
                     "stray ௗ, misplaced ஃ, unassigned code points).")
    for it in items:
        A(f"- {it}")
    A("")
    A("Non-Tamil characters in the cleaned verse text (after entity decoding):\n")
    A(md_table(["char", "code", "count", "sample refs"],
               ((repr(ch), f"U+{ord(ch):04X}", n, ", ".join(log.char_refs[ch][:3])) for ch, n in char_rows)))
    A("")

    A("## 4. Sandhi (ஒற்று) analysis\n")
    A("In this orthography the inserted consonant is written attached to the end of the preceding word "
      "(எவ்வளவாய்ப் பெருகி…). Native Tamil words do not end in க்/ச்/த்/ப், but transliterated names do "
      "(யோவாப், மோவாப், காத்), so every form ending in one of those letters is first classified: it is a "
      "sandhi form if it is more often followed by a word of the matching class than not, or if it is rare "
      "and its bare form is well attested; otherwise it is lexical. The tables then count, for each bare "
      "word and following class, how often the OV doubles and how often it does not. Only words that can "
      "take ஒற்று (ending in a vowel, a bare consonant, ய் or ழ்) are counted on the bare side. Adjacency "
      "means only whitespace between the two words; punctuation and verse ends break it.\n")
    A(md_table(["Measure", "Value"], [
        ("Forms ending in க்/ச்/த்/ப்", len(b.kind)),
        ("... classified sandhi", kinds.get("sandhi", 0)),
        ("... classified lexical (names, loanwords)", kinds.get("lexical", 0)),
        ("Sandhi tokens before a matching word (`with`)", with_tokens),
        ("Eligible bare tokens before a K/S/T/P word (`without`)", without_tokens),
        ("(bare, class) keys", len(b.sandhi_keys)),
        ("... always doubled", sum(1 for k in b.sandhi_keys if b.with_[k] and not b.without[k])),
        ("... never doubled", sum(1 for k in b.sandhi_keys if b.without[k] and not b.with_[k])),
        ("... mixed", both),
        ("... mixed and strong (minority <= 2, majority >= 10)", len(inc_strong)),
        ("Anomalies: wrongclass / nonKSTP / punct / verse-end",
         f"{atypes.get('wrongclass', 0)} / {atypes.get('nonKSTP', 0)} / {atypes.get('punct', 0)} / {atypes.get('verse-end', 0)}"),
    ]))
    A("")
    A("Doubling rate by word-ending group (top rows by volume; full table in `sandhi_suffix_summary.tsv`):\n")
    A(md_table(["ending", "next", "with", "without", "% doubled", "bare forms"], suf_rows, limit=30))
    A("")
    A("Strong inconsistencies (probable OV typos; full list in `sandhi_inconsistent.tsv`):\n")
    A(md_table(["bare", "next", "majority", "majority n", "minority n", "minority refs"], inc_rows, limit=30))
    A("")
    A("All sandhi anomalies (`sandhi_anomalies.tsv`):\n")
    A(md_table(["ref", "token", "next", "type", "bare count", "form match/other"],
               ((a["ref"], a["token"], a["next"], a["type"], a["bare_count"], f"{a['form_match_n']}/{a['form_other_n']}")
                for a in b.anomalies)))
    A("")

    A("## 5. Near-miss suspects\n")
    A(f"Frequent lemmas (lemma_count >= {b.args.freq_min}): {b.n_frequent}. Rare lemmas "
      f"(1 <= lemma_count <= {b.args.rare_max}): {b.n_rare}. Pairs at one edit: {b.suspects_total} "
      f"(written: {len(b.suspects)}, cap {b.args.max_suspects}; at most three candidates per rare lemma).\n")
    A(md_table(["edit class", "pairs"], sorted(susp_class.items(), key=lambda kv: EDIT_CLASS_RANK[kv[0]])))
    A("")
    A("Top of the list (lowest priority number = strongest lead):\n")
    A(md_table(["rare", "n", "candidate", "candidate n", "edit", "class", "ref"],
               ((d["rare"], d["rare_count"], d["candidate"], d["candidate_count"],
                 f"{d['op']} {d['from']}>{d['to']} ({d['position']})", d["edit_class"], d["rare_ref"])
                for d in b.suspects), limit=30))
    A("")
    A("Expect a large share of false leads: Tamil inflection means many genuine forms differ by one code "
      "point (அவன்/அவள்/அவர், அனைவரும்/அனைவருமே), and short words (four code points or fewer) are "
      "mostly distinct lexemes (நரகம்/நகரம்). A rare form is only a finding after the verse has been read.\n")

    A("## 6. Using this dictionary for Tamil IRV QA\n")
    for it in [
        "**Divine name.** The OV writes கர்த்தர் (" + str(count["கர்த்தர்"]) + " occurrences of the bare form) "
        "where the IRV writes யெகோவா. No யெகோவா form exists in this lexicon, so put the IRV inflections in "
        "`extra_words.txt` and rebuild, or the spell sweep will flag every one of them. Do the same for any "
        "other vocabulary the IRV modernised.",
        "**Known IRV defect shapes** (from earlier Round 2 QA passes) are worth grepping directly rather "
        "than waiting for the lexicon to catch them: `யெகோவவ` (dropped vowel), `யெகோவாக்க` (dative-stem "
        "defect), `வற்கு` (dropped த in -வதற்கு), digits inside verse text, U+200B/U+200C, double spaces, "
        "a space before `\\f*`.",
        "**Spell sweep.** Tokenise the IRV verse text the same way (Tamil-block runs, hyphen allowed inside "
        "a word), look each token up in `wordlist.txt`; for misses, look up the sandhi-stripped form in "
        "`wordlist_bare.txt`, then run the one-edit search against the frequent lemmas in `words.tsv` "
        "(`lemma_count` >= 20) to propose a correction. A miss that occurs many times in the IRV book is "
        "more likely a deliberate revision than a typo; report it once as a reviewer decision.",
        "**Sandhi sweep.** For each adjacent pair (W, X) in the IRV where X starts with க/ச/த/ப, strip "
        "any ஒற்று from W and look up (W-bare, class) in `sandhi_pairs.tsv`. If the OV verdict is "
        "always_doubled or never_doubled with a large total and the IRV disagrees, that is a lead; if the "
        "OV itself is mixed, weight by `pct_with` and by the ending-group norm in "
        "`sandhi_suffix_summary.tsv`. Skip W if it is in `final_consonant_words.tsv`.",
        "**Solid compounds** hide sandhi from the bigram tables: எடுத்துக்கொண்டு, அந்தப்பிரகாரமாய்ச் and "
        "the like are single tokens here, so the internal ஒற்று is never counted. A sweep that also splits "
        "compounds will see more variation than these tables show.",
        "**Orthography of 1957.** Spellings such as ஜலம், சிருஷ்டித்தார், இருக்கிறார்கள் and the pervasive "
        "Grantha letters (ஜ ஸ ஷ) are house practice in the OV. A modern text that avoids them is not wrong; "
        "a word missing from this lexicon is a lead, not a finding.",
        "**Rebuilding.** `python scripts/build_dictionary.py --expect bsi1957` from the repository root. "
        "`--freq-min`, `--rare-max` and `--max-suspects` tune the near-miss list; `--out-dir` and "
        "`--usfm-dir` relocate it. The parser is written for this corpus's seven markers; on a file with "
        "footnotes or headings the unknown lines are reported as unexpected rather than parsed.",
    ]:
        A(f"- {it}")
    A("")
    (out / "REPORT.md").write_text("\n".join(L), encoding="utf-8", newline="\n")


# --------------------------------------------------------------------------------------
# Expectations (corpus-specific self-test)
# --------------------------------------------------------------------------------------

def check_expectations(b: Build, rows_written: dict[str, int]) -> list[str]:
    log = b.log
    count = b.count
    kinds = b.sandhi_kind_counts()
    atypes = b.anomaly_type_counts()
    ent = Counter(e[1] for e in log.entities)
    checks = {
        "books": (len({u.book for u in b.units}), 66),
        "verses": (sum(1 for u in b.units if u.seg == "v"), 31102),
        "verses.tsv rows": (rows_written["verses.tsv"], 31102 + 1 + len(b.titles)),
        "text-bearing \\p lines": (len(log.p_with_text), 1),
        "\\p line location": (log.p_with_text[0] if log.p_with_text else "", "35_HABFBta.usfm:76 -> HAB 3:19"),
        "mt3 dropped": (log.mt3_dropped, 66),
        "non-Tamil titles dropped": (log.non_tamil_titles, ["37_HAGFBta.usfm:2 \\mt2 The Book of Haggai"]),
        "Tamil title lines": (len(b.titles), 130),
        "Latin letters in kept text": (sum(1 for u in b.units if re.search(r"[A-Za-z]", u.text)), 0),
        "unexpected lines": (len(log.unexpected_lines), 0),
        "forbidden characters": (len(log.forbidden), 0),
        "&quot; entities": (ent["&quot;"], 22),
        "&#39; entities": (ent["&#39;"], 1),
        "&#39; ref": ([e[0] for e in log.entities if e[1] == "&#39;"], ["2CH 30:15"]),
        "trailing-space verse lines": (log.trailing_space_lines, 823),
        "NFC-changed lines": (len(log.nfc_changed), 0),
        "decomposed pairs": (log.decomposed_pairs, 0),
        "digits in verse text": (log.digit_units, [("1SA 20:42", "43"), ("PSA 127:5", "6")]),
        "tokens": (b.total_tokens, 418560),
        "sum(count) == tokens": (sum(count.values()), b.total_tokens),
        "unique forms": (len(count), 94360),
        "hapax": (sum(1 for n in count.values() if n == 1), 61035),
        "forms >= 20": (sum(1 for n in count.values() if n >= 20), 2430),
        "U+0BCC count": (sum(u.text.count("ௌ") for u in b.scripture), 20),
        "count நான்": (count["நான்"], 4769),
        "count அவன்": (count["அவன்"], 4581),
        "count என்று": (count["என்று"], 4264),
        "count கர்த்தர்": (count["கர்த்தர்"], 3045),
        "count தேவன்": (count["தேவன்"], 841),
        "count ஆதியிலே": (count["ஆதியிலே"], 8),
        "count சிருஷ்டித்தார்": (count["சிருஷ்டித்தார்"], 5),
        "count ஹின்": (count["ஹின்"], 1),
        "ref ஹின்": (b.vrefs["ஹின்"], ["EZK 4:11"]),
        "count மகேர்-சாலால்-அஷ்-பாஸ்": (count["மகேர்-சாலால்-அஷ்-பாஸ்"], 2),
        "count சேலா": (count["சேலா"], 82),
        "count அந்த": (count["அந்த"], 1224),
        "count அந்தக்": (count["அந்தக்"], 183),
        "count அந்தச்": (count["அந்தச்"], 134),
        "count அந்தத்": (count["அந்தத்"], 151),
        "count அந்தப்": (count["அந்தப்"], 286),
        "count எனக்கு": (count["எனக்கு"], 879),
        "count எனக்குச்": (count["எனக்குச்"], 219),
        "kind யோவாப்": (b.kind.get("யோவாப்"), "lexical"),
        "kind அந்தக்": (b.kind.get("அந்தக்"), "sandhi"),
        "KSTP-final forms": (len(b.kind), 10256),
        "sandhi keys": (len(b.sandhi_keys), 22335),
        "mixed sandhi keys": (sum(1 for k in b.sandhi_keys if b.with_[k] and b.without[k]), 449),
        "sandhi kinds": ((kinds.get("sandhi", 0), kinds.get("lexical", 0)), (9931, 325)),
        "anomaly types": ((atypes.get("wrongclass", 0), atypes.get("nonKSTP", 0), atypes.get("punct", 0),
                           atypes.get("verse-end", 0)), (18, 5, 5, 0)),
        "sandhi identity": (sum(count[w] for w, k in b.kind.items() if k == "sandhi"),
                            sum(b.with_.values()) + len(b.anomalies)),
        "(-ை, K)": ((b.suffix_summary[("-ை", "K")]["with"], b.suffix_summary[("-ை", "K")]["without"]), (4944, 217)),
        "malformed errors": (sum(1 for _, (r, s) in b.malformed.items() if s == "error"), 0),
        "visual_au warnings": (sum(1 for _, (r, s) in b.malformed.items() if r == "visual_au"), 18),
    }
    failures = []
    for name, (actual, expected) in checks.items():
        if actual != expected:
            failures.append(f"{name}: expected {expected!r}, got {actual!r}")
    return failures


def verify_outputs(out: Path) -> list[str]:
    problems = []
    seen = set()
    for i, line in enumerate((out / "wordlist.txt").read_text(encoding="utf-8").split("\n")):
        if line == "":
            continue
        if line in seen:
            problems.append(f"wordlist.txt duplicate: {line}")
        seen.add(line)
        if unicodedata.normalize("NFC", line) != line:
            problems.append(f"wordlist.txt not NFC: {line}")
        if not TOKEN_RE.fullmatch(line):
            problems.append(f"wordlist.txt not a token: {line}")
    return problems


# --------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------

OTHER_LANGUAGE_BUILDS = {"pa": "pa_build", "ml": "ml_build", "hi": "hi_build", "or": "or_build"}      # --lang code -> build module in scripts/ (Tamil is this file)


def split_lang(argv: list[str]) -> tuple[str | None, list[str]]:
    """Remove --lang X / --lang=X from argv; returns (X or None, remaining args)."""
    out, lang, i = [], None, 0
    while i < len(argv):
        a = argv[i]
        if a == "--lang" and i + 1 < len(argv):
            lang, i = argv[i + 1], i + 2
            continue
        if a.startswith("--lang="):
            lang = a.split("=", 1)[1]
        else:
            out.append(a)
        i += 1
    return lang, out


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    except AttributeError:
        pass
    raw = sys.argv[1:] if argv is None else list(argv)
    if any(a == "--lang" or a.startswith("--lang=") for a in raw):
        lang, argv = split_lang(raw)
        if lang in OTHER_LANGUAGE_BUILDS:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import importlib
            return importlib.import_module(OTHER_LANGUAGE_BUILDS[lang]).main(argv)
        if lang != "ta":
            print(f"unknown --lang {lang!r}; known: ta, {', '.join(OTHER_LANGUAGE_BUILDS)}", file=sys.stderr)
            return 2
    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--usfm-dir", type=Path, default=root / "usfm")
    ap.add_argument("--out-dir", type=Path, default=root / "dictionary")
    ap.add_argument("--extra-words", type=Path, default=None,
                    help="supplement lexicon (default: <out-dir>/extra_words.txt; seeded if missing)")
    ap.add_argument("--freq-min", type=int, default=20, help="lemma count that makes a form 'frequent'")
    ap.add_argument("--rare-max", type=int, default=1, help="lemma count at or below which a form is 'rare'")
    ap.add_argument("--max-suspects", type=int, default=5000)
    ap.add_argument("--expect", choices=["bsi1957"], default=None,
                    help="run the corpus-specific self-test after building")
    args = ap.parse_args(argv)

    out: Path = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    extra_path = args.extra_words or (out / "extra_words.txt")
    if not extra_path.exists():
        extra_path.write_text(EXTRA_WORDS_SEED, encoding="utf-8", newline="\n")

    log = Log()
    units = parse_corpus(args.usfm_dir, log)
    b = Build(units, log, args)
    b.count_words()
    b.classify_kstp()
    b.build_sandhi_tables()
    b.find_suspects()
    b.check_malformed()
    b.load_extra_words(extra_path)
    rows_written = write_outputs(b, out)
    write_build_info(b, out, rows_written)
    write_report(b, out, rows_written)

    print(f"parsed {len(units)} units from {len(log.input_files)} files; "
          f"{b.total_tokens} tokens, {len(b.count)} unique forms, {len(b.kind)} KSTP-final forms "
          f"({b.sandhi_kind_counts().get('sandhi', 0)} sandhi / {b.sandhi_kind_counts().get('lexical', 0)} lexical), "
          f"{len(b.sandhi_keys)} sandhi keys, {len(b.anomalies)} anomalies, {len(b.suspects)} suspects, "
          f"{len(b.malformed)} malformed/warning forms")
    for name, n in rows_written.items():
        print(f"  {name}: {n}")
    if b.extra_rejected:
        print(f"  WARNING extra_words.txt: rejected {len(b.extra_rejected)} non-word lines: {b.extra_rejected[:5]}")
    if log.unexpected_lines:
        print(f"  WARNING {len(log.unexpected_lines)} unexpected lines, e.g. {log.unexpected_lines[:3]}")

    problems = verify_outputs(out)
    failures = check_expectations(b, rows_written) if args.expect else []
    for p in problems + failures:
        print("FAIL " + p)
    if problems or failures:
        return 1
    print("self-test: " + ("all expectations met" if args.expect else "outputs consistent (no --expect given)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
