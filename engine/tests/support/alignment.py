"""Builders for the word-alignment tests: a book project on disk from
{chapter: {verse: {text, alignment, complete}}}.

Shared here so no test module imports another (CLAUDE.md rule 2, #74)."""
from __future__ import annotations

import json
from pathlib import Path


def _write_book(root: Path, book_id: str, chapters: dict, lang_id: str = "tam") -> None:
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
        align_chapter = {}
        text_chapter = {}
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
        (align_dir / f"{chapter}.json").write_text(
            json.dumps(align_chapter, ensure_ascii=False), encoding="utf-8",
        )
        (root / book_id / f"{chapter}.json").write_text(
            json.dumps(text_chapter, ensure_ascii=False), encoding="utf-8",
        )
    (root / f"{book_id}.usfm").write_text(f"\\id {book_id.upper()}\n", encoding="utf-8")
