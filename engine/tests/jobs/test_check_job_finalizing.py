"""#235: the completion hook runs without the job lock, behind a non-terminal
"finalizing" state. A poll during it answers at once and never sees the
outcome before the hook has finished; cancel cannot undo a finished job."""
from __future__ import annotations

import threading
import time

from check_jobs import TERMINAL_STATES, CheckJobManager, CheckJobSpec


def _spec() -> CheckJobSpec:
    return CheckJobSpec(scope="chapter", project_path="p", chapters=("1",),
                        chapter_verses={"1": ["1", "2"]}, checks=("local",))


def _wait(manager, job_id, predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = manager.status(job_id, since=10**9)
        if predicate(snapshot):
            return snapshot
        time.sleep(0.005)
    raise AssertionError("condition not reached")


def test_polls_answer_at_once_while_the_hook_writes_and_never_see_the_outcome_early():
    entered, release = threading.Event(), threading.Event()
    seen: list[tuple[str, str | None]] = []

    def on_complete(job):
        seen.append((job.state, job.outcome))
        entered.set()
        release.wait(5)

    manager = CheckJobManager()
    started = manager.start(_spec(), run_stage=lambda c, v, k: [], on_complete=on_complete)
    assert entered.wait(5)

    began = time.perf_counter()
    snapshot = manager.status(started["jobId"])
    assert time.perf_counter() - began < 0.5  # not waiting on the hook
    assert snapshot["state"] == "finalizing" and snapshot["state"] not in TERMINAL_STATES
    assert snapshot["currentStage"] == "Saving results"
    assert snapshot["finishedAt"] is None
    assert len(snapshot["results"]) == 2  # every verse is already there

    cancelled = manager.cancel(started["jobId"])
    assert cancelled["state"] == "finalizing"

    release.set()
    done = _wait(manager, started["jobId"], lambda s: s["state"] in TERMINAL_STATES)
    assert done["state"] == "succeeded" and done["finishedAt"] and done["currentStage"] == "Complete"
    assert seen == [("finalizing", "succeeded")]
    assert "on_complete" in done["timings"]


def test_a_failed_verse_finishes_failed_and_the_hook_sees_it():
    seen: list[str | None] = []

    def run_stage(chapter, verse, checks):
        if verse == "2":
            raise RuntimeError("boom")
        return []

    manager = CheckJobManager()
    started = manager.start(_spec(), run_stage=run_stage, on_complete=lambda job: seen.append(job.outcome))
    done = _wait(manager, started["jobId"], lambda s: s["state"] in TERMINAL_STATES)
    assert done["state"] == "failed" and done["error"] == "1 verse(s) failed checking."
    assert seen == ["failed"]


def test_a_hook_that_raises_still_ends_the_job():
    def on_complete(job):
        raise RuntimeError("rollup write failed")

    manager = CheckJobManager()
    started = manager.start(_spec(), run_stage=lambda c, v, k: [], on_complete=on_complete)
    done = _wait(manager, started["jobId"], lambda s: s["state"] in TERMINAL_STATES)
    assert done["state"] == "succeeded"
