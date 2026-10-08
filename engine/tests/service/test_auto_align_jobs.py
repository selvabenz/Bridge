"""#221: chapter-wide automatic alignment as a background job.

Windows of three verses overlapping by one; each window is one
alignment.window.autoAlign. Fake transport only.
"""
import json
import time

import pytest

from alignment_auto_align_jobs import AutoAlignJobError, windows_for
from bridge_service import BridgeEngine
from tests.support.alignment_books import unaligned_verse, write_alignment_book
from tests.support.projects import call
from tests.support.waits import job_timeout


def test_windows_overlap_by_one_and_cover_every_verse():
    assert windows_for(["1", "2", "3", "4", "5", "6"]) == [["1", "2", "3"], ["3", "4", "5"], ["5", "6"]]
    assert windows_for(["1", "2", "3", "4", "5"]) == [["1", "2", "3"], ["3", "4", "5"]]
    assert windows_for(["1", "2"]) == [["1", "2"]]
    assert windows_for([]) == []
    assert windows_for(["1", "2-3", "4", "5"], window=2, overlap=0) == [["1", "2-3"], ["4", "5"]]
    with pytest.raises(AutoAlignJobError):
        windows_for(["1", "2", "3"], window=3, overlap=3)


def _empty_reply():
    return {"links": [], "nulls": [], "unplaced": [], "review_notes": []}


def transport(captured, delay=0.0):
    def send(url, headers, request_body, timeout):
        captured.append(json.loads(request_body))
        if delay:
            time.sleep(delay)
        return 200, json.dumps({
            "output_text": json.dumps(_empty_reply()),
            "usage": {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110},
        }).encode("utf-8")
    return send


@pytest.fixture
def root(tmp_path):
    root = tmp_path / "php"
    verses = {str(v): unaligned_verse(f"w{v}", [(f"λ{v}", f"G{1000 + v}0")]) for v in range(1, 7)}
    write_alignment_book(root, "php", {"1": verses})
    return root


def _wait(engine, job_id, timeout=10.0):
    deadline = time.monotonic() + job_timeout(timeout)
    while time.monotonic() < deadline:
        snapshot = call(engine, "alignment.autoAlign.status", {"jobId": job_id})["result"]
        if snapshot["state"] in {"succeeded", "failed", "cancelled"}:
            return snapshot
        time.sleep(0.02)
    raise AssertionError("auto-align job did not finish")


def _engine(root, send, key="test-key"):
    engine = BridgeEngine(ai_transport=send)
    engine.settings.set_api_key(key, persist=False)
    assert call(engine, "project.open", {"path": str(root)})["success"] is True
    return engine


def test_estimate_is_offline_and_counts_windows_and_calls(root):
    captured = []
    engine = _engine(root, transport(captured))
    estimate = call(engine, "alignment.autoAlign.estimate", {"scope": "chapter", "chapters": ["1"]})
    assert estimate["success"] is True, estimate
    result = estimate["result"]
    assert result["windows"] == 3 and result["calls"] == 6
    assert result["estimatedInputTokens"] > 0 and result["hasApiKey"] is True
    assert captured == []


def test_a_chapter_job_runs_every_window_twice_and_records_verdicts(root):
    captured = []
    engine = _engine(root, transport(captured))
    started = call(engine, "alignment.autoAlign.start", {"scope": "chapter", "chapters": ["1"]})
    assert started["success"] is True, started
    snapshot = _wait(engine, started["result"]["jobId"])
    assert snapshot["state"] == "succeeded", snapshot
    assert snapshot["windowsTotal"] == 3 and snapshot["windowsDone"] == 3
    assert len(captured) == 6 and snapshot["usage"]["calls"] == 6
    # Every verse has a verdict; nothing was placed, so every word is reported.
    assert set(snapshot["verdicts"]) == {f"1:{v}" for v in range(1, 7)}
    assert set(snapshot["verdicts"].values()) == {"NEEDS_REVIEW"}
    stored = call(engine, "alignment.autoAlign.verdict", {"chapter": "1", "verse": "3"})["result"]["verdict"]
    assert stored["jobId"] == snapshot["jobId"] and stored["window"] == ["3", "4", "5"]


def test_without_a_key_the_job_does_not_start(root):
    captured = []
    engine = _engine(root, transport(captured), key="")
    started = call(engine, "alignment.autoAlign.start", {"scope": "chapter", "chapters": ["1"]})
    assert started["success"] is True
    assert started["result"]["unavailable"]["reason"] == "no-api-key"
    assert captured == []


def test_cancel_stops_between_requests_and_retry_runs_what_is_left(root):
    captured = []
    engine = _engine(root, transport(captured, delay=0.15))
    job_id = call(engine, "alignment.autoAlign.start", {"scope": "chapter", "chapters": ["1"]})["result"]["jobId"]
    deadline = time.monotonic() + job_timeout(5.0)
    while not captured and time.monotonic() < deadline:
        time.sleep(0.01)
    call(engine, "alignment.autoAlign.cancel", {"jobId": job_id})
    snapshot = _wait(engine, job_id)
    assert snapshot["state"] == "cancelled"
    assert snapshot["windowsDone"] < snapshot["windowsTotal"]
    retried = call(engine, "alignment.autoAlign.retry", {"jobId": job_id})
    assert retried["success"] is True, retried
    second = _wait(engine, retried["result"]["jobId"])
    assert second["state"] == "succeeded" and second["resumeOf"] == job_id
    assert second["windowsTotal"] == 3 - snapshot["windowsDone"]


def test_a_second_job_while_one_runs_is_refused(root):
    captured = []
    engine = _engine(root, transport(captured, delay=0.2))
    first = call(engine, "alignment.autoAlign.start", {"scope": "chapter", "chapters": ["1"]})["result"]
    second = call(engine, "alignment.autoAlign.start", {"scope": "chapter", "chapters": ["1"]})
    assert second["success"] is False and "already" in second["error"]["message"]
    call(engine, "alignment.autoAlign.cancel", {"jobId": first["jobId"]})
    _wait(engine, first["jobId"])
