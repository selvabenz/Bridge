"""#220: possible omission / addition from alignment as verse check findings.

They fire only where an automatic pass has run (or tC says completed), only
for a live gap, with a stable id, and a reviewer's decision silences them on
the next check with no model call.
"""
import json

import pytest

from bridge_service import BridgeEngine
from tests.support.alignment_books import context_id, unaligned_verse, write_alignment_book
from tests.support.projects import call

#   S1 εὐχαριστῶ  S2 τῷ  S3 Θεῷ (v3)   S4 πάντοτε (v4)
#   T1 தேவனை (v3)   T2 நான்  T3 எப்பொழுதும்  T4 ஸ்தோத்திரிக்கிறேன் (v4)
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
    }})
    engine = BridgeEngine(ai_transport=_transport(PARTIAL))
    engine.settings.set_api_key("test-key", persist=False)
    assert call(engine, "project.open", {"path": str(root)})["success"] is True
    return engine


def _alignment_findings(engine, verse):
    response = call(engine, "verse.runChecks", {"chapter": "1", "verse": verse, "checks": ["alignment"]})
    assert response["success"] is True, response
    return [f for f in response["findings"]
            if f["check_type"] in {"alignment.possible_omission", "alignment.possible_addition"}]


def test_nothing_fires_before_any_automatic_pass(engine):
    assert _alignment_findings(engine, "4") == []


def test_unplaced_words_become_stable_findings_with_the_models_note(engine):
    assert call(engine, "alignment.window.autoAlign", {"chapter": "1", "verses": ["3", "4"]})["success"]
    first = _alignment_findings(engine, "4")
    kinds = {(f["check_type"], f["category"]) for f in first}
    assert kinds == {("alignment.possible_omission", "alignment"), ("alignment.possible_addition", "alignment")}
    omission = next(f for f in first if f["check_type"] == "alignment.possible_omission")
    assert "πάντοτε" in omission["explanation"] and "no word for 'always'" in omission["explanation"]
    addition = next(f for f in first if f["check_type"] == "alignment.possible_addition")
    text = call(engine, "verse.get", {"chapter": "1", "verse": "4"})["result"]["text"]
    assert text[addition["start_offset"]:addition["end_offset"]] == "எப்பொழுதும்"
    # Same ids on a second check: decisions keyed by them survive.
    assert {f["id"] for f in _alignment_findings(engine, "4")} == {f["id"] for f in first}
    # v3 is fully accounted, so it raises nothing.
    assert _alignment_findings(engine, "3") == []


def test_a_reviewers_null_decision_silences_the_finding(engine):
    assert call(engine, "alignment.window.autoAlign", {"chapter": "1", "verses": ["3", "4"]})["success"]
    v4 = call(engine, "alignment.get", {"chapter": "1", "verse": "4"})["result"]
    assert call(engine, "alignment.null.set", {
        "chapter": "1", "verse": "4", "side": "target", "id": context_id(v4, bottom_word="எப்பொழுதும்"),
        "reason": "EXPLICITATION",
    })["success"]
    remaining = _alignment_findings(engine, "4")
    assert [f["check_type"] for f in remaining] == ["alignment.possible_omission"]


def test_a_decision_on_the_finding_is_reapplied(engine):
    assert call(engine, "alignment.window.autoAlign", {"chapter": "1", "verses": ["3", "4"]})["success"]
    finding = next(f for f in _alignment_findings(engine, "4") if f["check_type"] == "alignment.possible_omission")
    decided = call(engine, "verse.decide", {"chapter": "1", "verse": "4", "findingId": finding["id"], "status": "accepted"})
    assert decided["success"] is True, decided
    again = next(f for f in _alignment_findings(engine, "4") if f["id"] == finding["id"])
    assert again["status"] == "accepted"


def test_an_edit_after_the_pass_replaces_the_note(engine):
    assert call(engine, "alignment.window.autoAlign", {"chapter": "1", "verses": ["3", "4"]})["success"]
    engine.project.apply_scripture_edit("1", "4", "நான் எப்பொழுதும் ஸ்தோத்திரிக்கிறேன் இன்று", username="tester")
    findings = _alignment_findings(engine, "4")
    assert findings and all("verse changed after the last automatic pass" in f["explanation"] for f in findings)


def test_a_reverted_verse_goes_quiet(engine):
    assert call(engine, "alignment.window.autoAlign", {"chapter": "1", "verses": ["3", "4"]})["success"]
    assert call(engine, "alignment.autoAlign.revert", {"chapter": "1", "verse": "4"})["success"]
    assert _alignment_findings(engine, "4") == []
