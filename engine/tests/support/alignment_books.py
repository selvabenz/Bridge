"""Builders for small hand-written alignment projects (#216-#221).

`tests/alignment/test_alignment_statistics.py` has a private `_write_book` that
three other test modules borrow by importing it -- the pattern CLAUDE.md rule 2
forbids. The automatic cross-verse alignment tests need the same shape, so the
builder lives here instead of being borrowed a fourth time. It writes exactly
what that helper writes.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_alignment_book(root: Path, book_id: str, chapters: dict, lang_id: str = "tam") -> None:
    """chapters: {chapter: {verse: {"text": str, "alignment": dict, "complete": bool}}}"""
    align_dir = root / ".apps" / "translationCore" / "alignmentData" / book_id
    align_dir.mkdir(parents=True, exist_ok=True)
    (root / book_id).mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(json.dumps({
        "project": {"id": book_id, "name": book_id.upper()},
        "target_language": {"id": lang_id, "name": "Tamil"},
        "tc_version": "8", "tc_edit_version": "3.7.0",
    }), encoding="utf-8")
    for chapter, verses in chapters.items():
        align_chapter: dict[str, Any] = {}
        text_chapter: dict[str, str] = {}
        for verse, data in verses.items():
            align_chapter[verse] = data["alignment"]
            text_chapter[verse] = data["text"]
            if data.get("complete"):
                completed_dir = (
                    root / ".apps" / "translationCore" / "tools" / "wordAlignment" / "completed" / str(chapter)
                )
                completed_dir.mkdir(parents=True, exist_ok=True)
                (completed_dir / f"{verse}.json").write_text(
                    json.dumps({"username": "tester", "modifiedTimestamp": "2026-01-01T00:00:00.000Z"}),
                    encoding="utf-8",
                )
        (align_dir / f"{chapter}.json").write_text(json.dumps(align_chapter, ensure_ascii=False), encoding="utf-8")
        (root / book_id / f"{chapter}.json").write_text(json.dumps(text_chapter, ensure_ascii=False), encoding="utf-8")
    (root / f"{book_id}.usfm").write_text(f"\\id {book_id.upper()}\n", encoding="utf-8")


def top(word: str, strong: str, occurrence: int = 1, occurrences: int = 1) -> dict:
    return {"word": word, "strong": strong, "lemma": word, "occurrence": occurrence, "occurrences": occurrences}


def bottom(word: str, occurrence: int = 1, occurrences: int = 1) -> dict:
    return {"word": word, "occurrence": occurrence, "occurrences": occurrences, "type": "bottomWord"}


def unaligned_verse(text: str, sources: list[tuple[str, str]]) -> dict:
    """A verse with every source token in its own empty group and every target
    word in the bank -- what a fresh raw import looks like."""
    words = text.split()
    counts: dict[str, int] = {}
    bank = []
    for word in words:
        counts[word] = counts.get(word, 0) + 1
    seen: dict[str, int] = {}
    for word in words:
        seen[word] = seen.get(word, 0) + 1
        bank.append(bottom(word, seen[word], counts[word]))
    return {
        "text": text,
        "alignment": {
            "alignments": [{"topWords": [top(w, s)], "bottomWords": []} for w, s in sources],
            "wordBank": bank,
        },
    }


def context_id(context: dict, *, top_word: str | None = None, bottom_word: str | None = None) -> str:
    """The positional id of a word in an `alignment.get` context."""
    if top_word is not None:
        return next(t["id"] for t in context["topTokens"] if t["word"] == top_word)
    return next(t["id"] for t in context["bottomTokens"] if t["word"] == bottom_word)
