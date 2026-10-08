"""Background chapter/book checking jobs for the Bridge sidecar.

The stdio dispatcher remains responsive while a worker evaluates verses.  Job
snapshots are deliberately JSON-native so every transport can expose the same
protocol without knowing about threads or QaFinding objects.
"""
from __future__ import annotations

import copy
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from tc_ai_bridge import check_timing


TERMINAL_STATES = {"succeeded", "failed", "cancelled"}
# Every verse is checked and the completion hook is writing the rollup and the
# findings snapshots. Not terminal: a poll keeps polling, and never sees the
# outcome before those writes land (#235).
FINALIZING = "finalizing"
LANGUAGE_QA_CHECK = "languageQa"


class CheckJobError(RuntimeError):
    pass


class CheckJobConflict(CheckJobError):
    pass


class CheckJobNotFound(CheckJobError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class CheckJobSpec:
    scope: str
    project_path: str
    chapters: tuple[str, ...]
    chapter_verses: dict[str, list[str]]
    checks: tuple[str, ...]


class _CheckJob:
    def __init__(self, spec: CheckJobSpec) -> None:
        self.id = str(uuid.uuid4())
        self.spec = spec
        self.state = "queued"
        self.current_chapter: Optional[str] = None
        self.current_verse: Optional[str] = None
        self.current_stage = "Queued"
        self.completed_steps = 0
        self.total_steps = 0
        self.results: dict[str, dict[str, Any]] = {}
        # Keys of `results` in the order they were written: the cursor a
        # status poll passes back (`since`) indexes this list, so a poll copies
        # only the verses finished since the last one (#229).
        self.result_order: list[str] = []
        self.error: Optional[str] = None
        # "succeeded" or "failed", set when every verse is done; `state` only
        # becomes it after the completion hook has run (#235).
        self.outcome: Optional[str] = None
        self.created_at = _now()
        self.finished_at: Optional[str] = None
        self.cancel_event = threading.Event()
        self.lock = threading.RLock()
        # Wall-clock per stage and per engine (check_timing): "stage:<label>"
        # from the loop below, "preflight.*"/"local.*"/"greekroom.*" from the
        # code the stages call. Reported as `timings` on the snapshot.
        self.timings = check_timing.Timings()

    def snapshot(self, since: Optional[int] = None) -> dict[str, Any]:
        """The job as JSON. `results` holds every verse finished so far, or
        with `since`, only those finished after the first `since` (the
        `resultsCursor` of an earlier snapshot). Copying every verse's findings
        on every poll held this lock -- which the job writes results under --
        for seconds on a whole book (#229)."""
        with self.lock:
            if since is None:
                results = copy.deepcopy(self.results)
            else:
                start = max(0, min(int(since), len(self.result_order)))
                results = {key: copy.deepcopy(self.results[key]) for key in self.result_order[start:]}
            total_verses = sum(len(v) for v in self.spec.chapter_verses.values())
            completed_verses = sum(
                1 for item in self.results.values()
                if item.get("status") in {"succeeded", "failed"}
            )
            percent = 100 if self.state == "succeeded" else (
                round((self.completed_steps / self.total_steps) * 100)
                if self.total_steps else 0
            )
            return {
                "jobId": self.id,
                "scope": self.spec.scope,
                "projectPath": self.spec.project_path,
                "state": self.state,
                "checks": list(self.spec.checks),
                "chapters": list(self.spec.chapters),
                "chapterVerses": copy.deepcopy(self.spec.chapter_verses),
                "totalVerses": total_verses,
                "completedVerses": completed_verses,
                "failedVerses": sum(
                    1 for item in self.results.values() if item.get("status") == "failed"
                ),
                "percent": max(0, min(100, percent)),
                "currentChapter": self.current_chapter,
                "currentVerse": self.current_verse,
                "currentStage": self.current_stage,
                "results": results,
                "resultsSince": 0 if since is None else start,
                "resultsCursor": len(self.result_order),
                "error": self.error,
                "createdAt": self.created_at,
                "finishedAt": self.finished_at,
                "timings": self.timings.as_dict(),
                "elapsedSeconds": round(self.timings.elapsed(), 3),
            }


RunStage = Callable[[str, str, list[str]], list[dict[str, Any]]]
Preflight = Callable[[threading.Event], None]


class CheckJobManager:
    """Owns one active checking pass and retains completed snapshots for retry."""

    def __init__(self) -> None:
        self._jobs: dict[str, _CheckJob] = {}
        self._active_job_id: Optional[str] = None
        self._lock = threading.RLock()

    def start(
        self,
        spec: CheckJobSpec,
        *,
        run_stage: RunStage,
        preflight: Optional[Preflight] = None,
        on_complete: Optional[Callable[["_CheckJob"], None]] = None,
    ) -> dict[str, Any]:
        with self._lock:
            active = self._jobs.get(self._active_job_id or "")
            if active is not None and active.state not in TERMINAL_STATES:
                raise CheckJobConflict(f"Check job {active.id} is already {active.state}.")
            job = _CheckJob(spec)
            self._jobs[job.id] = job
            self._active_job_id = job.id

        thread = threading.Thread(
            target=self._run,
            args=(job, run_stage, preflight, on_complete),
            name=f"bridge-check-{job.id[:8]}",
            daemon=True,
        )
        thread.start()
        return job.snapshot()

    def active(self) -> bool:
        """True while a job is queued, running or cancelling."""
        with self._lock:
            job = self._jobs.get(self._active_job_id or "")
            return job is not None and job.state not in TERMINAL_STATES

    def status(self, job_id: str = "", since: Optional[int] = None) -> dict[str, Any]:
        with self._lock:
            resolved_id = job_id or self._active_job_id or ""
            job = self._jobs.get(resolved_id)
        if job is None:
            raise CheckJobNotFound(f"Unknown check job '{resolved_id}'.")
        return job.snapshot(since)

    def cancel(self, job_id: str = "") -> dict[str, Any]:
        with self._lock:
            resolved_id = job_id or self._active_job_id or ""
            job = self._jobs.get(resolved_id)
        if job is None:
            raise CheckJobNotFound(f"Unknown check job '{resolved_id}'.")
        with job.lock:
            # Finalizing: every verse is done and its results are being saved;
            # there is nothing left to cancel.
            if job.state not in TERMINAL_STATES and job.state != FINALIZING:
                job.cancel_event.set()
                job.state = "cancelling"
                job.current_stage = "Cancelling after current check"
        return job.snapshot()

    def spec_for_retry(self, job_id: str) -> CheckJobSpec:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise CheckJobNotFound(f"Unknown check job '{job_id}'.")
        with job.lock:
            if job.state not in {"failed", "cancelled"}:
                raise CheckJobConflict("Only failed or cancelled jobs can be retried.")
            return job.spec

    @staticmethod
    def _stages(checks: tuple[str, ...]) -> list[tuple[str, list[str]]]:
        stages: list[tuple[str, list[str]]] = []
        if any(c in checks for c in ("local", "tN", "tW", "alignment", "usfm")):
            local = [c for c in checks if c in {"local", "tN", "tW", "alignment", "usfm"}]
            stages.append(("tN · tW · Alignment", local or ["local"]))
        if any(c in checks for c in ("greekroom", "wildebeest")):
            stages.append(("QA", ["greekroom"]))
        # Language QA findings are not QaFindings: the stage returns
        # {"findings": [...], "decided": {id: decision}} and the verse result
        # carries it under "languageQa", so every reader of "findings" keeps
        # getting QaFinding dicts only.
        if LANGUAGE_QA_CHECK in checks:
            stages.append(("Language QA", [LANGUAGE_QA_CHECK]))
        return stages

    def _finish(
        self, job: _CheckJob, outcome: str, error: Optional[str], stage: str,
        on_complete: Optional[Callable[["_CheckJob"], None]],
    ) -> None:
        """End the job as `outcome` ("succeeded" or "failed"). The completion
        hook runs first, behind the non-terminal FINALIZING state and WITHOUT
        job.lock: a poll can never observe the outcome before the hook has run,
        and no longer waits on the lock while it writes. That wait was 4.5-5 s
        on a whole book, with the stdio dispatcher waiting behind it (#235)."""
        with job.lock:
            job.outcome = outcome
            job.state = FINALIZING
            job.current_stage = "Saving results"
            job.current_chapter = None
            job.current_verse = None
        self._fire_on_complete(job, on_complete)
        with job.lock:
            job.state = outcome
            job.error = error
            job.current_stage = stage
            job.finished_at = _now()

    @staticmethod
    def _fire_on_complete(job: _CheckJob, on_complete: Optional[Callable[["_CheckJob"], None]]) -> None:
        # Called by _finish for the succeeded/failed outcomes, without
        # job.lock, while job.state is FINALIZING; job.outcome says which.
        # Every verse is done, so job.results is no longer written. (Not called
        # on cancellation: on_complete's own logic only ever acts on a
        # succeeded outcome.) Never let a rollup-write failure surface as a
        # check-job failure.
        if on_complete is not None:
            try:
                with job.timings.step("on_complete"):
                    on_complete(job)
            except Exception:
                pass

    def _run(
        self, job: _CheckJob, run_stage: RunStage, preflight: Optional[Preflight],
        on_complete: Optional[Callable[["_CheckJob"], None]] = None,
    ) -> None:
        with check_timing.activate(job.timings):
            self._run_impl(job, run_stage, preflight, on_complete)
        check_timing.trace(f"check job {job.spec.scope} {job.state} {len(job.results)} verse(s)", job.timings)

    def _run_impl(
        self, job: _CheckJob, run_stage: RunStage, preflight: Optional[Preflight],
        on_complete: Optional[Callable[["_CheckJob"], None]] = None,
    ) -> None:
        stages = self._stages(job.spec.checks)
        verse_count = sum(len(v) for v in job.spec.chapter_verses.values())
        with job.lock:
            job.state = "running"
            job.total_steps = verse_count * len(stages) + (1 if preflight else 0)

        try:
            if preflight is not None:
                if self._cancelled(job):
                    return
                with job.lock:
                    job.current_stage = "Preparing checks"
                with job.timings.step("preflight"):
                    preflight(job.cancel_event)
                with job.lock:
                    job.completed_steps += 1

            for chapter in job.spec.chapters:
                for verse in job.spec.chapter_verses.get(chapter, []):
                    if self._cancelled(job):
                        return
                    key = f"{chapter}:{verse}"
                    findings: list[dict[str, Any]] = []
                    language_qa: Optional[dict[str, Any]] = None
                    verse_error: Optional[str] = None
                    for label, stage_checks in stages:
                        if self._cancelled(job):
                            return
                        with job.lock:
                            job.current_chapter = chapter
                            job.current_verse = verse
                            job.current_stage = label
                        try:
                            with job.timings.step(f"stage:{label}"):
                                produced = run_stage(chapter, verse, stage_checks)
                            if stage_checks == [LANGUAGE_QA_CHECK]:
                                language_qa = produced  # type: ignore[assignment]
                            else:
                                findings.extend(produced)
                        except Exception as exc:  # one verse must not abort the whole book
                            verse_error = str(exc)
                        finally:
                            with job.lock:
                                job.completed_steps += 1
                        if self._cancelled(job):
                            return
                        if verse_error:
                            break
                    with job.lock:
                        if key not in job.results:
                            job.result_order.append(key)
                        job.results[key] = {
                            "chapter": chapter,
                            "verse": verse,
                            "status": "failed" if verse_error else "succeeded",
                            "findings": findings,
                            "error": verse_error,
                        }
                        if language_qa is not None:
                            job.results[key]["languageQa"] = language_qa

            with job.lock:
                failed = [r for r in job.results.values() if r.get("status") == "failed"]
            self._finish(
                job, "failed" if failed else "succeeded",
                f"{len(failed)} verse(s) failed checking." if failed else None,
                "Checking failed" if failed else "Complete", on_complete,
            )
        except Exception as exc:
            if self._cancelled(job):
                return
            self._finish(job, "failed", str(exc), "Checking failed", on_complete)

    @staticmethod
    def _cancelled(job: _CheckJob) -> bool:
        if not job.cancel_event.is_set():
            return False
        with job.lock:
            job.state = "cancelled"
            job.current_stage = "Cancelled"
            job.current_chapter = None
            job.current_verse = None
            job.finished_at = _now()
        return True
