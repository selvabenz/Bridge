"""A reference Bible beside the text being checked: indic-qa's Old Version
panel and "OV occurrences", read from a folder the reviewer chooses.

Nothing OV-derived is bundled for Punjabi, Malayalam, Hindi or Odia (their
licence is unresolved: engine/vendor/indic-qa/NOTICE.md). The reviewer points
Settings > Language QA at a folder per pack. Two shapes are accepted:

- an indic-qa dictionary folder holding `verses.tsv` (ref, book, book_idx,
  chapter, verse, seg, text; indic-qa's `ov_text.py` reads the same file);
- a folder of USFM/SFM books, read through Bridge's own importer
  (`language_qa_benchmark.book_verses`, the path `build_indic_qa_packs.py`
  uses). USFM is never parsed here (CLAUDE.md gotcha 14).

ta-irv ships Tamil's `dictionary/verses.tsv` (the 1957 OV, public domain in
India), so a Tamil project has a reference with no folder set.

Loading a folder parses every book, which is slow for USFM, so it happens on a
background thread. The plain verse text is cached as `<fingerprint>.json.gz`
in the app's data folder; the fingerprint covers each file's name, size and
mtime, so editing the folder rebuilds it. Requests never wait: until it is
ready they say so.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import threading
import unicodedata
from pathlib import Path
from typing import Any

CACHE_VERSION = 1
SCRIPTURE_SUFFIXES = (".sfm", ".usfm")


def detect(folder: str | Path) -> str | None:
    """"tsv", "usfm", or None when the folder holds neither."""
    path = Path(folder)
    if not path.is_dir():
        return None
    if (path / "verses.tsv").is_file():
        return "tsv"
    if any(p.suffix.lower() in SCRIPTURE_SUFFIXES for p in path.iterdir() if p.is_file()):
        return "usfm"
    return None


def _source_files(folder: Path, kind: str) -> list[Path]:
    if kind == "tsv":
        return [folder / "verses.tsv"]
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in SCRIPTURE_SUFFIXES)


def fingerprint(folder: str | Path, kind: str) -> str:
    path = Path(folder).resolve()
    state = [str(path), kind, CACHE_VERSION]
    for file in _source_files(path, kind):
        stat = file.stat()
        state.append((file.name, stat.st_size, stat.st_mtime_ns))
    return hashlib.sha1(json.dumps(state).encode("utf-8")).hexdigest()[:20]


def _load_tsv(folder: Path) -> dict[str, dict[str, dict[str, str]]]:
    verses: dict[str, dict[str, dict[str, str]]] = {}
    with (folder / "verses.tsv").open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream, delimiter="\t", quoting=csv.QUOTE_NONE):
            if row.get("seg") != "v" or not row.get("book"):
                continue
            book = row["book"].strip().upper()
            verses.setdefault(book, {}).setdefault(str(row["chapter"]).strip(), {})[str(row["verse"]).strip()] = (
                unicodedata.normalize("NFC", row.get("text") or ""))
    return verses


def _load_usfm(folder: Path) -> dict[str, dict[str, dict[str, str]]]:
    from ..language_qa import lift_inline_usfm
    from ..language_qa_benchmark import book_verses
    verses: dict[str, dict[str, dict[str, str]]] = {}
    for file in _source_files(folder, "usfm"):
        try:
            book, chapters = book_verses(file)
        except Exception:
            continue  # not a Scripture book (a front-matter file, a broken one): skipped, never fatal
        out = verses.setdefault(str(book).upper(), {})
        for chapter, by_verse in chapters.items():
            for verse, raw in by_verse.items():
                lifted, _ = lift_inline_usfm(raw)
                text = lifted.visible if lifted is not None else raw
                out.setdefault(str(chapter), {})[str(verse)] = unicodedata.normalize("NFC", text)
    return verses


class ReferenceText:
    """Plain verse text of a reference Bible: book (upper case) -> chapter -> verse -> text."""

    def __init__(self, label: str, kind: str, path: str, verses: dict[str, dict[str, dict[str, str]]]):
        self.label, self.kind, self.path, self.verses = label, kind, path, verses

    def chapter(self, book: str, chapter: str) -> list[dict[str, str]]:
        rows = self.verses.get(book.upper(), {}).get(str(chapter), {})

        def order(verse: str) -> tuple[int, str]:
            digits = "".join(ch for ch in verse.split("-")[0] if ch.isdigit())
            return (int(digits) if digits else 0, verse)
        return [{"verse": v, "text": rows[v]} for v in sorted(rows, key=order)]

    def references(self):
        """(book, chapter, verse, text) in book order as stored."""
        for book, chapters in self.verses.items():
            for chapter, by_verse in chapters.items():
                for verse, text in by_verse.items():
                    yield book, chapter, verse, text

    def find(self, word: str, limit: int) -> tuple[list[dict[str, Any]], int]:
        from ..language_qa import word_occurrences
        wanted = unicodedata.normalize("NFC", word.strip())
        hits: list[dict[str, Any]] = []
        total = 0
        for book, chapter, verse, text in self.references():
            if wanted not in text:
                continue
            for token, start, end in word_occurrences(text):
                if token != wanted:
                    continue
                total += 1
                if len(hits) < limit:
                    before = text[max(0, start - 40):start]
                    hits.append({"book": book.lower(), "chapter": chapter, "verse": verse, "start": start, "end": end,
                                 "snippet": before + text[start:end + 40], "snippetStart": len(before),
                                 "snippetEnd": len(before) + end - start})
        return hits, total


class ReferenceHolder:
    """One reference folder: loaded once on a background thread, cached on disk."""

    def __init__(self, folder: str | Path, label: str, cache_dir: Path):
        self.folder = Path(folder)
        self.label = label
        self.cache_dir = cache_dir
        self._lock = threading.Lock()
        self._text: ReferenceText | None = None
        self._error = ""
        self._thread: threading.Thread | None = None

    def state(self) -> tuple[ReferenceText | None, str]:
        """(text, error); starts loading on first use. Never blocks."""
        with self._lock:
            if self._text is not None or self._error:
                return self._text, self._error
            if self._thread is None:
                self._thread = threading.Thread(target=self._load, name="reference-text", daemon=True)
                self._thread.start()
            return None, ""

    def wait(self, timeout: float = 120.0) -> tuple[ReferenceText | None, str]:
        """For tests and scripts: load, waiting for it."""
        self.state()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        with self._lock:
            return self._text, self._error

    def _load(self) -> None:
        try:
            kind = detect(self.folder)
            if kind is None:
                raise ValueError(f"{self.folder} holds no verses.tsv and no USFM books.")
            cache = self.cache_dir / f"{fingerprint(self.folder, kind)}.json.gz"
            if cache.is_file():
                verses = json.loads(gzip.decompress(cache.read_bytes()).decode("utf-8"))
            else:
                verses = _load_tsv(self.folder) if kind == "tsv" else _load_usfm(self.folder)
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                tmp = cache.with_suffix(".tmp")
                tmp.write_bytes(gzip.compress(json.dumps(verses, ensure_ascii=False).encode("utf-8")))
                tmp.replace(cache)
            text = ReferenceText(self.label, kind, str(self.folder), verses)
            with self._lock:
                self._text = text
        except Exception as exc:  # reported to the panel, never a failed request
            with self._lock:
                self._error = f"{type(exc).__name__}: {exc}"
