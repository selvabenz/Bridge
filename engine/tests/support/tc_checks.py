"""Builders for translationCore check data in a test project: tN/tW index entries
and checkData records (selections, verseEdits, comments, invalidated), in the
shapes translationCore writes and tc_project reads. Book, chapter and verse are
parameters, unlike the private helpers in test_qa_report.py (which stay there;
CLAUDE.md rule 2 forbids importing them)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def check_entry(book: str, chapter: str, verse: str, tool: str, group: str, check_id: str, *,
                selections: Any = False, nothing: bool = False, invalidated: bool = False,
                note: str = "") -> dict[str, Any]:
    """One tN/tW index entry."""
    return {
        "contextId": {
            "reference": {"bookId": book, "chapter": chapter, "verse": verse},
            "tool": tool, "groupId": group, "checkId": check_id,
            "quoteString": "θεός", "occurrence": 1, "occurrenceNote": note,
        },
        "selections": selections, "nothingToSelect": nothing, "invalidated": invalidated,
    }


def write_index(root: Path, book: str, tool: str, group: str, entries: list[dict[str, Any]]) -> None:
    path = root / ".apps" / "translationCore" / "index" / tool / book / f"{group}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")


def write_state(root: Path, book: str, kind: str, chapter: str, verse: str, record: Any, name: str) -> Path:
    """A checkData record: kind is selections, verseEdits, comments or invalidated;
    `name` is the file name without .json (translationCore uses the timestamp)."""
    path = root / ".apps" / "translationCore" / "checkData" / kind / book / chapter / verse / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return path


def state_record(book: str, chapter: str, verse: str, tool: str, group: str, check_id: str, *,
                 modified: str | None = None, timestamp: str | None = None, **extra: Any) -> dict[str, Any]:
    """A selections/comments/invalidated record for one check."""
    record: dict[str, Any] = {
        "contextId": {"reference": {"bookId": book, "chapter": chapter, "verse": verse},
                      "tool": tool, "groupId": group, "checkId": check_id},
        "username": "tester", **extra,
    }
    if modified is not None:
        record["modifiedTimestamp"] = modified
    if timestamp is not None:
        record["timestamp"] = timestamp
    return record
