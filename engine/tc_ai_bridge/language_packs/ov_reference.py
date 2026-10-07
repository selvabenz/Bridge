"""The Old Version verse beside a finding: a pack dictionary's verses.tsv.

indic-qa's Tamil dictionary is built from the BSI 1957 Old Version, the text
the IRV revises, and ships that text cleaned as `verses.tsv` (ref, book,
book_idx, chapter, verse, seg, text; tab-separated, no quoting). The ta-irv
indic-qa layer attaches the OV verse with the same reference to each of its
findings, so a reviewer sees how the OV wrote the passage while deciding.

Read on first need and kept for the process. Read only: the file is pack
data, never written. A verse the OV numbers differently is simply absent.
"""
from __future__ import annotations

import csv
import threading
from pathlib import Path

FILE = "verses.tsv"
LABEL = "OV 1957"

_lock = threading.Lock()
_loaded: dict[Path, dict[tuple[str, str, str], str]] = {}


def _load(path: Path) -> dict[tuple[str, str, str], str]:
    out: dict[tuple[str, str, str], str] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t", quoting=csv.QUOTE_NONE):
            if row.get("seg") == "v" and row.get("text"):
                out[(row["book"].upper(), row["chapter"], row["verse"])] = row["text"]
    return out


def verses(dictionary: Path) -> dict[tuple[str, str, str], str]:
    """(BOOK, chapter, verse) -> OV text for a dictionary folder; empty when it has no verses.tsv."""
    path = Path(dictionary) / FILE
    table = _loaded.get(path)
    if table is None:
        with _lock:
            table = _loaded.get(path)
            if table is None:
                table = _loaded[path] = _load(path) if path.is_file() else {}
    return table


def reference(dictionary: Path, book: str, chapter: str, verse: str) -> dict[str, str] | None:
    """{label, ref, text} for one verse, or None. A bridge ("3-4") or a
    lettered segment ("3a") looks up its first number."""
    first = "".join(ch for ch in verse.split("-", 1)[0] if ch.isdigit())
    text = verses(dictionary).get((book.upper(), str(chapter), first))
    if text is None:
        return None
    return {"label": LABEL, "ref": f"{book.upper()} {chapter}:{first}", "text": text}
