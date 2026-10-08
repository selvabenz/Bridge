"""Project and protocol builders shared across the engine test suite.

These five lived in `tests/service/test_bridge_service.py`, and seven other test
modules reached into that file to borrow them:

    from tests.service.test_bridge_service import fixture_project, call

That is a test module importing another test module, which #74 phase 1 set out to
remove, and it had a second cost: `test_bridge_service.py` imports the 5,300-line
dispatcher, so every borrower inherited that dependency transitively. Tracing
"what could this change affect" from any engine module therefore reached them all
(#74 phases 3 and 4).

Taken verbatim -- same fixtures, same shapes. Only their address changed.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from greek_room_engine.protocol import EngineRequest
# wait_for_job's timeout scaling; it came with the helper (#74 phase 4).
from tests.support.waits import job_timeout


def _write_minimal_book(root: Path, book_id: str, verse_text: str, lang_id: str = "tam", lang_name: str = "Tamil"):
    align_dir = root / ".apps" / "translationCore" / "alignmentData" / book_id
    align_dir.mkdir(parents=True)
    (root / book_id).mkdir(parents=True)
    (root / "manifest.json").write_text(json.dumps({
        "project": {"id": book_id, "name": book_id.upper()},
        "target_language": {"id": lang_id, "name": lang_name},
        "tc_version": "8", "tc_edit_version": "3.7.0",
    }), encoding="utf-8")
    (align_dir / "1.json").write_text(json.dumps({"1": {"alignments": [], "wordBank": []}}), encoding="utf-8")
    (root / book_id / "1.json").write_text(json.dumps({"1": verse_text}, ensure_ascii=False), encoding="utf-8")
    (root / f"{book_id}.usfm").write_text(
        f"\\id {book_id.upper()}\n\\c 1\n\\v 1 {verse_text}\n", encoding="utf-8",
    )


@pytest.fixture
def fixture_project(tmp_path):
    root = tmp_path / "rut"
    align_dir = root / ".apps" / "translationCore" / "alignmentData" / "rut"
    align_dir.mkdir(parents=True)
    (root / "rut").mkdir(parents=True)

    (root / "manifest.json").write_text(json.dumps({
        "project": {"id": "rut", "name": "Ruth"},
        "target_language": {"id": "tam", "name": "Tamil"},
        "tc_version": "8", "tc_edit_version": "3.7.0",
    }), encoding="utf-8")

    (align_dir / "1.json").write_text(json.dumps({
        "1": {
            "alignments": [{
                "topWords": [{"word": "אֱלֹהִ֑ים", "strong": "H430", "occurrence": 1, "occurrences": 1}],
                "bottomWords": [{"word": "தேவன்", "occurrence": 1, "occurrences": 1}],
            }],
            "wordBank": [],
        }
    }, ensure_ascii=False), encoding="utf-8")

    (root / "rut" / "1.json").write_text(json.dumps({
        "1": "ஆதியிலே தேவன் வானத்தையும் பூமியையும் படைத்தார்."
    }, ensure_ascii=False), encoding="utf-8")

    (root / "rut.usfm").write_text(
        "\\id RUT\n\\c 1\n\\v 1 ஆதியிலே தேவன் வானத்தையும் பூமியையும் படைத்தார்.\n",
        encoding="utf-8",
    )

    return root


@pytest.fixture
def two_book_collection(tmp_path):
    """A minimal two-book collection: rut and gen as sibling directories
    under tmp_path, linked via .bridge/collection.json the way a real
    multi-book import links siblings (see project_import.py's
    _collection_projects)."""
    rut = tmp_path / "rut"
    gen = tmp_path / "gen"
    _write_minimal_book(rut, "rut", "ஆதியிலே தேவன் வானத்தையும் பூமியையும் படைத்தார்.")
    _write_minimal_book(gen, "gen", "தொடக்கத்தில் தேவன் வானத்தையும் பூமியையும் படைத்தார்.")
    (rut / ".bridge").mkdir(parents=True)
    (rut / ".bridge" / "collection.json").write_text(json.dumps({
        "projects": [
            {"directoryName": "rut", "bookId": "rut", "bookName": "Ruth"},
            {"directoryName": "gen", "bookId": "gen", "bookName": "Genesis"},
        ],
    }), encoding="utf-8")
    return rut


def call(engine, method, params=None):
    return engine.handle_request(EngineRequest(id="t", method=method, params=params or {})).to_dict()


def wait_for_job(engine, job_id, timeout=5.0):
    deadline = time.monotonic() + job_timeout(timeout)
    while time.monotonic() < deadline:
        snapshot = call(engine, "checks.status", {"jobId": job_id})["result"]
        if snapshot["state"] in {"succeeded", "failed", "cancelled"}:
            return snapshot
        time.sleep(0.01)
    raise AssertionError(f"check job {job_id} did not finish")


def link_collection(books: dict[str, Path]) -> None:
    """Link book folders as one collection: `.bridge/collection.json` on every
    book, as a multi-book import writes it (schema 2: sibling directory names).
    `books` maps each book id to its folder; the folders share a parent."""
    entries = [{"directoryName": Path(root).name, "bookId": name, "bookName": name.upper()}
               for name, root in books.items()]
    for root in books.values():
        (Path(root) / ".bridge").mkdir(parents=True, exist_ok=True)
        (Path(root) / ".bridge" / "collection.json").write_text(json.dumps({"projects": entries}), encoding="utf-8")
