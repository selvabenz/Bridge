"""The timing instrumentation as the protocol exposes it (2026-10-08): a check
job's snapshot carries `timings` per stage and per engine, and a Language QA
pass reports its phases on languageQa.status. Measurement only: the findings
are the same as before it existed, which the rest of the suite asserts."""
from __future__ import annotations

import time

from bridge_service import BridgeEngine
from tests.support.projects import call, fixture_project, wait_for_job  # noqa: F401 - fixture


def test_check_job_snapshot_carries_stage_and_engine_timings(fixture_project, monkeypatch):
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})
    monkeypatch.setattr(engine, "_usfm_findings_for_book", lambda project=None, cancel_event=None: [])

    started = call(engine, "checks.start", {
        "scope": "chapter", "chapters": ["1"], "checks": ["local", "greekroom", "languageQa"],
    })["result"]
    finished = wait_for_job(engine, started["jobId"])

    assert finished["state"] == "succeeded"
    timings = finished["timings"]
    for name in ("preflight", "preflight.language_qa", "stage:tN · tW · Alignment", "stage:QA",
                 "stage:Language QA", "greekroom.wildebeest", "local.editorial", "decisions.reapply"):
        assert name in timings, name
        assert timings[name]["calls"] >= 1
        assert timings[name]["ms"] >= 0
    # Two QaFinding stages, one read of the verse's decisions (#231; it was
    # two before, the repeat this instrumentation first made visible).
    assert timings["decisions.reapply"]["calls"] == 1
    assert finished["elapsedSeconds"] >= 0


def test_language_qa_pass_reports_its_phases(fixture_project):
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        status = call(engine, "languageQa.status", {"projectPath": str(fixture_project), "limit": 0})["result"]
        if status["state"] == "completed":
            break
        time.sleep(0.05)
    assert status["state"] == "completed"
    timings = status["timings"]
    for name in ("lqa.sample_detect", "lqa.loaders", "lqa.pack_resolve", "lqa.chapter_read", "lqa.scan_verse",
                 "rule.unicode.charscan", "rule.spacing.extra", "lqa.flush"):
        assert name in timings, name
    assert timings["lqa.scan_verse"]["calls"] == 1
