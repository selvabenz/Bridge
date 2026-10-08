"""A check job reads each verse's decisions once for all its stages (#231), and
a decision still reaches the findings of every stage: a local one (first
stage) and a Greek Room one (second stage, served from the memo)."""
from __future__ import annotations

import json

from bridge_service import BridgeEngine
from greek_room_engine.models.finding import QaFinding
from tests.support.projects import call, fixture_project, wait_for_job  # noqa: F401 - fixture


def _greek_room_finding(**_kwargs):
    return [QaFinding(engine="wildebeest", check_type="wildebeest.zero_width", original_text="x",
                      start_offset=0, end_offset=1, explanation="stub")]


def test_decisions_are_read_once_per_verse_and_reach_both_stages(fixture_project, monkeypatch):
    # A target word left in the word bank: ALIGN_UNALIGNED_BOTTOM, a first-stage (local) finding.
    alignment = fixture_project / ".apps" / "translationCore" / "alignmentData" / "rut" / "1.json"
    data = json.loads(alignment.read_text(encoding="utf-8"))
    data["1"]["wordBank"] = [{"word": "வானத்தையும்", "occurrence": 1, "occurrences": 1}]
    alignment.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})
    monkeypatch.setattr(engine, "_usfm_findings_for_book", lambda project=None, cancel_event=None: [])
    monkeypatch.setattr(engine.greek_room, "check_verse", _greek_room_finding)

    first = call(engine, "verse.runChecks", {"chapter": "1", "verse": "1", "checks": ["local", "greekroom"]})
    findings = first["findings"]
    assert {f["engine"] for f in findings} >= {"wildebeest"}, [(f["engine"], f["check_type"]) for f in findings]
    local = next(f for f in findings if f["engine"] != "wildebeest")
    greek = next(f for f in findings if f["engine"] == "wildebeest")
    for finding, status in ((local, "accepted"), (greek, "ignored")):
        decided = call(engine, "verse.decide", {"chapter": "1", "verse": "1", "findingId": finding["id"],
                                                "status": status})
        assert not decided.get("error"), decided

    started = call(engine, "checks.start", {
        "scope": "chapter", "chapters": ["1"], "checks": ["local", "greekroom"],
    })["result"]
    finished = wait_for_job(engine, started["jobId"])

    assert finished["state"] == "succeeded"
    by_id = {f["id"]: f for f in finished["results"]["1:1"]["findings"]}
    assert by_id[local["id"]]["status"] == "accepted"
    assert by_id[greek["id"]]["status"] == "ignored"
    # Two stages, one verse, one read.
    assert finished["timings"]["decisions.reapply"]["calls"] == 1
    assert finished["timings"]["verse.read_text"]["calls"] == 1
