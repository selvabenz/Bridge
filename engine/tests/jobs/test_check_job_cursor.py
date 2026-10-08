"""checks.status with a cursor (#229): a poll copies only the verses finished
since the previous one. The manager alone, with a stub stage; no dispatcher."""
from __future__ import annotations

import threading
import time

from check_jobs import CheckJobManager, CheckJobSpec


def _spec(verses: int) -> CheckJobSpec:
    numbers = [str(n) for n in range(1, verses + 1)]
    return CheckJobSpec(scope="chapter", project_path="p", chapters=("1",),
                        chapter_verses={"1": numbers}, checks=("local",))


def test_incremental_polls_add_up_to_the_full_results():
    gate = threading.Event()

    def run_stage(chapter, verse, checks):
        if verse == "4":
            gate.wait(5)  # hold the job mid-chapter so a poll lands while it runs
        return [{"id": f"{chapter}:{verse}", "check_type": "stub"}]

    manager = CheckJobManager()
    snapshot = manager.start(_spec(8), run_stage=run_stage)
    # The job thread may already have finished some verses: the start snapshot
    # is a full one, and its cursor counts exactly what it holds (App.svelte
    # seeds its tally and cursor from it the same way).
    assert snapshot["resultsCursor"] == len(snapshot["results"])

    collected: dict[str, dict] = dict(snapshot["results"])
    cursor = snapshot["resultsCursor"]
    polls = 0
    while True:
        snapshot = manager.status(snapshot["jobId"], since=cursor)
        polls += 1
        assert snapshot["resultsSince"] == cursor
        # Never a verse twice, never one out of order.
        assert not set(snapshot["results"]) & set(collected)
        collected.update(snapshot["results"])
        cursor = snapshot["resultsCursor"]
        assert cursor == len(collected)
        if cursor >= 3:
            gate.set()
        if snapshot["state"] in {"succeeded", "failed", "cancelled"}:
            break
        time.sleep(0.005)

    assert snapshot["state"] == "succeeded"
    assert polls > 1
    full = manager.status(snapshot["jobId"])
    assert full["resultsSince"] == 0 and full["resultsCursor"] == 8
    assert collected == full["results"]
    assert list(collected) == [f"1:{n}" for n in range(1, 9)]


def test_a_cursor_past_the_end_copies_nothing_and_a_negative_one_copies_all():
    manager = CheckJobManager()
    snapshot = manager.start(_spec(3), run_stage=lambda c, v, k: [])
    deadline = time.monotonic() + 5
    while snapshot["state"] not in {"succeeded", "failed", "cancelled"} and time.monotonic() < deadline:
        time.sleep(0.01)
        snapshot = manager.status(snapshot["jobId"])
    assert manager.status(snapshot["jobId"], since=10**9)["results"] == {}
    assert manager.status(snapshot["jobId"], since=10**9)["resultsCursor"] == 3
    assert len(manager.status(snapshot["jobId"], since=-1)["results"]) == 3
    # Progress fields do not depend on the cursor.
    assert manager.status(snapshot["jobId"], since=10**9)["completedVerses"] == 3
