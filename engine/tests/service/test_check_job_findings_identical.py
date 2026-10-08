"""The local stage's speed work (#239-#241) must not change one finding: a check
job's findings equal a direct verse.runChecks for every verse, byte for byte,
including after an edit made while the job runs. USFM and names are stubbed;
none of this work touches them."""
from __future__ import annotations

import json

import pytest

from bridge_service import BridgeEngine
from tests.support.alignment_books import bottom, top, unaligned_verse, write_alignment_book
from tests.support.projects import call, wait_for_job


def _aligned(text: str, pairs: list[tuple[str, str, str]]) -> dict:
    """Every pair aligned 1:1; the remaining target words in the bank."""
    words = text.split()
    aligned = {target for _source, _strong, target in pairs}
    return {
        "text": text,
        "alignment": {
            "alignments": [{"topWords": [top(source, strong)], "bottomWords": [bottom(target)]}
                           for source, strong, target in pairs],
            "wordBank": [bottom(word) for word in words if word not in aligned],
        },
    }


@pytest.fixture
def book(tmp_path):
    root = tmp_path / "php"
    write_alignment_book(root, "php", {
        "1": {
            "1": {**_aligned("அவன் வந்தான்", [("ἦλθεν", "G20640", "வந்தான்")]), "complete": True},
            "2": unaligned_verse("அவள் போனாள் வேகமாக", [("ἀπῆλθεν", "G05650"), ("ταχύ", "G50350")]),
            "3": _aligned("அவர்கள் அங்கே இருந்தார்கள்", [("ἦσαν", "G15100", "இருந்தார்கள்")]),
        },
        "2": {
            "1": _aligned("இரண்டாம் அதிகாரம் தொடங்குகிறது", [("δεύτερον", "G12080", "இரண்டாம்")]),
            "2": unaligned_verse("முடிவு", [("τέλος", "G50560")]),
        },
    })
    invalid = root / ".apps" / "translationCore" / "tools" / "wordAlignment" / "invalid" / "1"
    invalid.mkdir(parents=True)
    (invalid / "3.json").write_text(json.dumps({"username": "t", "modifiedTimestamp": "2026-01-01T00:00:00.000Z"}),
                                    encoding="utf-8")
    return root


def _engine(root, monkeypatch) -> BridgeEngine:
    engine = BridgeEngine()
    assert call(engine, "project.open", {"path": str(root)})["success"]
    monkeypatch.setattr(engine, "_usfm_findings_for_book", lambda project=None, cancel_event=None: [])
    monkeypatch.setattr(engine, "_names_findings_for_book", lambda project=None: [])
    return engine


def _book_job(engine) -> dict:
    started = call(engine, "checks.start", {"scope": "book", "checks": ["local"]})["result"]
    finished = wait_for_job(engine, started["jobId"], timeout=60)
    assert finished["state"] == "succeeded", finished
    return finished


def _direct(engine, chapter: str, verse: str) -> list[dict]:
    response = call(engine, "verse.runChecks", {"chapter": chapter, "verse": verse, "checks": ["local"]})
    assert response["success"], response
    return response["findings"]


def _same(a: list[dict], b: list[dict]) -> bool:
    """Byte-identical but for `created_at`, the wall clock each run stamps."""
    a = [{k: v for k, v in f.items() if k != "created_at"} for f in a]
    b = [{k: v for k, v in f.items() if k != "created_at"} for f in b]
    if json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(b, sort_keys=True, ensure_ascii=False):
        return True
    diffs = [(i, k, x.get(k), y.get(k)) for i, (x, y) in enumerate(zip(a, b))
             for k in sorted(set(x) | set(y)) if x.get(k) != y.get(k)]
    raise AssertionError(f"{len(a)} vs {len(b)} findings; differing fields: {diffs[:8]}")


def test_job_findings_equal_direct_verse_checks_byte_for_byte(book, monkeypatch):
    engine = _engine(book, monkeypatch)
    finished = _book_job(engine)
    codes = set()
    for key, result in finished["results"].items():
        chapter, verse = key.split(":")
        direct = _direct(engine, chapter, verse)
        assert _same(result["findings"], direct), key
        codes |= {f["check_type"] for f in direct}
    # Not vacuous: the fixture reaches the alignment and tC branches.
    assert {"ALIGN_UNALIGNED_BOTTOM", "ALIGN_UNALIGNED_TOP", "WA_INVALID"} <= codes, codes


def test_an_edit_while_the_job_runs_is_seen_by_the_later_verse(book, monkeypatch):
    engine = _engine(book, monkeypatch)
    project = engine.project
    original = engine._run_verse_checks_for_project
    edited: list[bool] = []

    def edit_once(proj, chapter, verse, checks, **kwargs):
        if (chapter, verse) == ("1", "2") and not edited:
            edited.append(True)
            # Chapter 2 is cached first, so only the cache's own check can see the edit.
            project.target_verse_text("2", "1")
            project.load_verse_alignment("2", "1")
            project.verses("2")
            project.apply_scripture_edit("2", "1", "அதிகாரம் தொடங்குகிறது")
        return original(proj, chapter, verse, checks, **kwargs)

    monkeypatch.setattr(engine, "_run_verse_checks_for_project", edit_once)
    finished = _book_job(engine)
    monkeypatch.setattr(engine, "_run_verse_checks_for_project", original)
    assert edited
    after = _direct(engine, "2", "1")
    assert _same(finished["results"]["2:1"]["findings"], after)
    assert project.target_verse_text("2", "1") == "அதிகாரம் தொடங்குகிறது"


def test_a_job_parses_each_chapter_file_once(book, monkeypatch):
    engine = _engine(book, monkeypatch)
    finished = _book_job(engine)
    # Two chapters, an alignment file and a target file each.
    assert finished["timings"]["project.chapter_parse"]["calls"] <= 4
