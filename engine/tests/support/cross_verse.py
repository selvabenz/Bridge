"""Builders for the cross-verse alignment proposal tests (#139, #146): Greek
and Tamil word tokens, alignment groups and a two-verse book on disk.

Shared here so no test module imports another (CLAUDE.md rule 2, #74)."""
from __future__ import annotations

import json
from pathlib import Path


def _top(word: str, strong: str) -> dict:
    return {"word": word, "strong": strong, "occurrence": 1, "occurrences": 1}


def _bottom(word: str) -> dict:
    return {"word": word, "occurrence": 1, "occurrences": 1}


def _group(tops: list[dict], bottoms: list[dict]) -> dict:
    return {"topWords": tops, "bottomWords": bottoms}


KADAVUL = "கடவுள்"


VARTHAI = "வார்த்தை"


def _write_book(root: Path, chapters: dict) -> None:
    book_id = "php"
    align_dir = root / ".apps" / "translationCore" / "alignmentData" / book_id
    align_dir.mkdir(parents=True, exist_ok=True)
    (root / book_id).mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(json.dumps({
        "project": {"id": book_id, "name": "PHP"},
        "target_language": {"id": "tam", "name": "Tamil"},
        "tc_version": "8", "tc_edit_version": "3.7.0",
    }), encoding="utf-8")
    for chapter, verses in chapters.items():
        align_chapter, text_chapter = {}, {}
        for verse, data in verses.items():
            align_chapter[verse] = data["alignment"]
            text_chapter[verse] = data["text"]
            if data.get("complete"):
                completed = (
                    root / ".apps" / "translationCore" / "tools"
                    / "wordAlignment" / "completed" / str(chapter)
                )
                completed.mkdir(parents=True, exist_ok=True)
                (completed / f"{verse}.json").write_text(
                    json.dumps({"username": "tester", "modifiedTimestamp": "2026-01-01T00:00:00.000Z"}),
                    encoding="utf-8",
                )
        (align_dir / f"{chapter}.json").write_text(
            json.dumps(align_chapter, ensure_ascii=False), encoding="utf-8",
        )
        (root / book_id / f"{chapter}.json").write_text(
            json.dumps(text_chapter, ensure_ascii=False), encoding="utf-8",
        )
    (root / f"{book_id}.usfm").write_text("\\id PHP\n", encoding="utf-8")
