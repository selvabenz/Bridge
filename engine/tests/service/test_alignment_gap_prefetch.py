"""#241: gap_issues reading a job's prefetch (GapReads) gives exactly what it
gives reading the workbench per verse, across the states an automatic pass
leaves: fresh verdicts with notes, links, a reviewer's null decision, a stale
verdict after an edit, and a reverted verse. prefetch() reads again after any
committed write."""
from __future__ import annotations

import json

import pytest

from bridge_service import BridgeEngine
from tc_ai_bridge import alignment_gap_checks
from tests.support.alignment_books import context_id, unaligned_verse, write_alignment_book
from tests.support.projects import call

PARTIAL = {
    "links": [
        {"source_id": "S3", "target_id": "T1", "confidence": 95, "reason": "God"},
        {"source_id": "S1", "target_id": "T4", "confidence": 90, "reason": "give thanks"},
        {"source_id": "S1", "target_id": "T2", "confidence": 80, "reason": "first person"},
    ],
    "nulls": [{"id": "S2", "reason": "GRAMMATICAL", "note": "article"}],
    "unplaced": [{"id": "S4", "note": "no word for 'always'"}, {"id": "T3", "note": "'always' has no source"}],
    "review_notes": [],
}


def _transport(body):
    def transport(url, headers, request_body, timeout):
        return 200, json.dumps({
            "output_text": json.dumps(body, ensure_ascii=False),
            "usage": {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20},
        }).encode("utf-8")
    return transport


@pytest.fixture
def engine(tmp_path):
    root = tmp_path / "php"
    write_alignment_book(root, "php", {"1": {
        "3": unaligned_verse("தேவனை", [("εὐχαριστῶ", "G21680"), ("τῷ", "G35880"), ("Θεῷ", "G23160")]),
        "4": unaligned_verse("நான் எப்பொழுதும் ஸ்தோத்திரிக்கிறேன்", [("πάντοτε", "G38420")]),
        "5": {**unaligned_verse("வேறு வசனம்", [("ἄλλος", "G02430")]), "complete": True},
    }})
    engine = BridgeEngine(ai_transport=_transport(PARTIAL))
    engine.settings.set_api_key("test-key", persist=False)
    assert call(engine, "project.open", {"path": str(root)})["success"] is True
    assert call(engine, "alignment.window.autoAlign", {"chapter": "1", "verses": ["3", "4"]})["success"]
    return engine


def _both(engine, verse):
    """(per-verse reads, prefetched reads) for one verse, as comparable dicts."""
    project = engine.project
    alignment = project.load_verse_alignment("1", verse)
    text = project.target_verse_text("1", verse)
    direct = alignment_gap_checks.gap_issues(project, "1", verse, alignment, text)
    cached = alignment_gap_checks.gap_issues(project, "1", verse, alignment, text,
                                             reads=alignment_gap_checks.GapReads(project))
    return [vars(i) for i in direct], [vars(i) for i in cached]


def _assert_same(engine, expect_any: bool):
    seen = 0
    for verse in ("3", "4", "5"):
        direct, cached = _both(engine, verse)
        assert cached == direct, verse
        seen += len(direct)
    assert (seen > 0) == expect_any


def test_after_an_automatic_pass(engine):
    _assert_same(engine, expect_any=True)


def test_after_a_null_decision(engine):
    v4 = call(engine, "alignment.get", {"chapter": "1", "verse": "4"})["result"]
    assert call(engine, "alignment.null.set", {
        "chapter": "1", "verse": "4", "side": "target", "id": context_id(v4, bottom_word="எப்பொழுதும்"),
        "reason": "EXPLICITATION",
    })["success"]
    _assert_same(engine, expect_any=True)


def test_after_an_edit_makes_the_verdict_stale(engine):
    engine.project.apply_scripture_edit("1", "4", "நான் எப்பொழுதும் ஸ்தோத்திரிக்கிறேன் இன்று", username="tester")
    direct, _ = _both(engine, "4")
    assert direct and all("verse changed after the last automatic pass" in i["detail"] for i in direct)
    _assert_same(engine, expect_any=True)


def test_after_a_revert(engine):
    assert call(engine, "alignment.autoAlign.revert", {"chapter": "1", "verse": "4"})["success"]
    _assert_same(engine, expect_any=True)


def test_prefetch_reads_again_after_a_committed_write(engine):
    project = engine.project
    reads: dict = {}
    first = alignment_gap_checks.prefetch(project, reads)
    assert alignment_gap_checks.prefetch(project, reads) is first
    v4 = call(engine, "alignment.get", {"chapter": "1", "verse": "4"})["result"]
    assert call(engine, "alignment.null.set", {
        "chapter": "1", "verse": "4", "side": "target", "id": context_id(v4, bottom_word="எப்பொழுதும்"),
        "reason": "EXPLICITATION",
    })["success"]
    second = alignment_gap_checks.prefetch(project, reads)
    assert second is not first
    assert second.decisions_for_verse("1", "4") == project.null_decisions.decisions_for_verse("1", "4")
    assert alignment_gap_checks.prefetch(project, None) is None
