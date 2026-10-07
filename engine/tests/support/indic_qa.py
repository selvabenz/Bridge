"""Builders for the indic-qa profile-pack tests (pa, ml, hi, or).

Shared here so no test module imports another (CLAUDE.md, #74)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tc_ai_bridge.language_packs import indic_qa_adapter
from tc_ai_bridge.language_qa import (CROSSING_LIMITATION, MAX_VERSE_CHARS, lift_inline_usfm, rule_fields,
                                      stable_finding_id, text_hash)


def write_project(root: Path, book: str, chapters: dict[str, dict[str, str]], *,
                  lang_id: str, lang_name: str, language_qa: dict[str, Any] | None = None) -> Path:
    """A minimal translationCore book project with these chapters."""
    (root / book).mkdir(parents=True)
    align = root / ".apps" / "translationCore" / "alignmentData" / book
    align.mkdir(parents=True)
    manifest = {"project": {"id": book, "name": book.upper()},
                "target_language": {"id": lang_id, "name": lang_name},
                "tc_version": "8", "tc_edit_version": "3.7.0"}
    if language_qa is not None:
        manifest["language_qa"] = language_qa
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    usfm = [f"\\id {book.upper()}"]
    for chapter, verses in chapters.items():
        (root / book / f"{chapter}.json").write_text(json.dumps(verses, ensure_ascii=False), encoding="utf-8")
        (align / f"{chapter}.json").write_text(
            json.dumps({v: {"alignments": [], "wordBank": []} for v in verses}), encoding="utf-8")
        usfm.append(f"\\c {chapter}")
        usfm.extend(f"\\v {v} {t}" for v, t in verses.items())
    (root / f"{book}.usfm").write_text("\n".join(usfm) + "\n", encoding="utf-8")
    return root


def manager_project(root: Path, book: str, chapters: dict[str, dict[str, str]], declared: str) -> SimpleNamespace:
    """What LanguageQaManager.bind reads from a project, without the dispatcher."""
    folder = root / book
    folder.mkdir(parents=True)
    for chapter, verses in chapters.items():
        (folder / f"{chapter}.json").write_text(json.dumps(verses, ensure_ascii=False), encoding="utf-8")
    return SimpleNamespace(path=root, book_id=book, book_dir=folder,
                           manifest={"target_language": {"id": declared}},
                           terminology_rules=lambda: [], housestyle_entries=lambda: [])


def wait(manager, timeout: float = 90.0) -> dict[str, Any]:
    """The first completed status. A profile pack's first pass loads its
    dictionary and IRV snapshot, so this waits longer than the Tamil tests."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = manager.status(limit=100)
        if status["state"] == "completed":
            return status
        assert status["state"] != "failed", status
        time.sleep(.01)
    pytest.fail(f"Language QA did not complete: {manager.status()}")


def check(pack, book: str, chapters: dict[str, dict[str, str]]) -> tuple[list[dict[str, Any]], list[str]]:
    """profile_findings with the real language_qa helpers."""
    result = indic_qa_adapter.profile_findings(
        pack, book, chapters, lift=lift_inline_usfm, max_verse_chars=MAX_VERSE_CHARS,
        finding_id=stable_finding_id, rule_fields=rule_fields, text_hash=text_hash,
        crossing_note=CROSSING_LIMITATION)
    assert result is not None
    return result
