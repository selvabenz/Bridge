"""Chapter-wide automatic alignment, as a cancellable background job (#221).

A window (#219) covers at most five verses; a chapter is a run of windows. This
job walks a chapter in windows of ``window`` verses that overlap by
``overlap`` verse, so every verse boundary sits *inside* some window and a word
moved across it can be seen from both sides. Each window is one call to the
service's ``auto_align_window`` -- two model requests, then agreement and
writes -- so every rule of the window applies here unchanged: agreement is the
gate, a reviewer's work is never written over, a word neither pass placed is
reported and not forced.

**Overlap.** Windows run in reading order. A verse in two windows is decided
again by the later one, which supersedes only what an earlier automatic pass
wrote and only where both ends of a link lie inside the later window; a link
reaching back to a verse the later window cannot see is left as it is. That is
simpler than the cross-window vote the plan sketched, and it never loses a link
the later window had no means to judge.

Lifecycle copied from `ai_review_jobs.AIReviewJobManager`: one active job per
engine, cancellation between model requests, a failed window recorded and
skipped rather than sinking the chapter, retry of what did not succeed.
Offline, the first window returns ``unavailable`` and the job stops with that
reason; nothing is ever sent unless a reviewer started the job.
"""
from __future__ import annotations

import copy
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

TERMINAL_STATES = {"succeeded", "failed", "cancelled"}

RunWindow = Callable[[str, list[str], str], dict[str, Any]]


class AutoAlignJobError(RuntimeError):
    pass


class AutoAlignJobConflict(AutoAlignJobError):
    pass


class AutoAlignJobNotFound(AutoAlignJobError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def windows_for(verses: list[str], *, window: int = 3, overlap: int = 1) -> list[list[str]]:
    """Consecutive windows over a chapter's verse list, overlapping by
    `overlap`. A verse list shorter than one window is one window."""
    if window < 2 or overlap < 0 or overlap >= window:
        raise AutoAlignJobError("A window needs at least two verses and an overlap smaller than itself.")
    if not verses:
        return []
    if len(verses) <= window:
        return [list(verses)]
    stride = window - overlap
    found: list[list[str]] = []
    start = 0
    while True:
        chunk = verses[start:start + window]
        found.append(list(chunk))
        if start + window >= len(verses):
            break
        start += stride
    return found


@dataclass(frozen=True)
class AutoAlignJobSpec:
    scope: str                                  # "chapter" | "book"
    chapters: tuple[str, ...]
    windows: dict[str, list[list[str]]]         # chapter -> windows still to run
    apply: bool = True
    resume_of: str = ""


class _AutoAlignJob:
    def __init__(self, spec: AutoAlignJobSpec) -> None:
        self.id = f"aaj-{uuid.uuid4().hex[:12]}"
        self.spec = spec
        self.state = "queued"
        self.current: Optional[dict[str, Any]] = None
        self.stage = "Queued"
        self.results: dict[str, dict[str, Any]] = {}     # "chapter:v1-v3" -> window result summary
        self.verdicts: dict[str, str] = {}               # "chapter:verse" -> latest verdict
        self.usage = {"calls": 0, "totalTokens": 0, "estimatedCostUSD": 0.0}
        self.error: Optional[str] = None
        self.unavailable: Optional[dict[str, Any]] = None
        self.created_at = _now()
        self.finished_at: Optional[str] = None
        self.cancel_event = threading.Event()
        self.lock = threading.RLock()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            total = sum(len(w) for w in self.spec.windows.values())
            done = sum(1 for r in self.results.values() if r.get("status") in {"succeeded", "failed"})
            counts: dict[str, int] = {}
            for verdict in self.verdicts.values():
                counts[verdict] = counts.get(verdict, 0) + 1
            return {
                "jobId": self.id, "scope": self.spec.scope, "apply": self.spec.apply,
                "state": self.state, "stage": self.stage, "chapters": list(self.spec.chapters),
                "windowsTotal": total, "windowsDone": done,
                "windowsFailed": sum(1 for r in self.results.values() if r.get("status") == "failed"),
                "percent": 100 if self.state == "succeeded" else (round(done / total * 100) if total else 0),
                "currentWindow": copy.deepcopy(self.current),
                "verdictCounts": counts,
                "verdicts": dict(self.verdicts),
                "results": copy.deepcopy(self.results),
                "usage": dict(self.usage),
                "error": self.error, "unavailable": copy.deepcopy(self.unavailable),
                "resumeOf": self.spec.resume_of,
                "createdAt": self.created_at, "finishedAt": self.finished_at,
            }


class AutoAlignJobManager:
    """Own one active automatic-alignment job and keep terminal ones for retry."""

    def __init__(self) -> None:
        self._jobs: dict[str, _AutoAlignJob] = {}
        self._active: Optional[str] = None
        self._lock = threading.RLock()

    def start(self, spec: AutoAlignJobSpec, *, run_window: RunWindow) -> dict[str, Any]:
        if spec.scope not in {"chapter", "book"}:
            raise AutoAlignJobError("Automatic alignment runs on a chapter or a book.")
        with self._lock:
            active = self._jobs.get(self._active or "")
            if active is not None and active.state not in TERMINAL_STATES:
                raise AutoAlignJobConflict(f"Automatic alignment job {active.id} is already {active.state}.")
            job = _AutoAlignJob(spec)
            self._jobs[job.id] = job
            self._active = job.id
        threading.Thread(
            target=self._run, args=(job, run_window), name=f"bridge-auto-align-{job.id[-8:]}", daemon=True,
        ).start()
        return job.snapshot()

    def status(self, job_id: str = "") -> dict[str, Any]:
        return self._get(job_id).snapshot()

    def cancel(self, job_id: str = "") -> dict[str, Any]:
        job = self._get(job_id)
        with job.lock:
            if job.state not in TERMINAL_STATES:
                job.cancel_event.set()
                job.state = "cancelling"
                job.stage = "Cancelling after the current request"
        return job.snapshot()

    def spec_for_retry(self, job_id: str) -> AutoAlignJobSpec:
        job = self._get(job_id)
        with job.lock:
            if job.state not in {"failed", "cancelled"}:
                raise AutoAlignJobConflict("Only a failed or cancelled job can be retried.")
            remaining = {
                chapter: [w for w in windows if job.results.get(_key(chapter, w), {}).get("status") != "succeeded"]
                for chapter, windows in job.spec.windows.items()
            }
            return AutoAlignJobSpec(
                scope=job.spec.scope, chapters=job.spec.chapters, windows=remaining,
                apply=job.spec.apply, resume_of=job.id,
            )

    def _get(self, job_id: str) -> _AutoAlignJob:
        with self._lock:
            resolved = job_id or self._active or ""
            job = self._jobs.get(resolved)
        if job is None:
            raise AutoAlignJobNotFound(f"Unknown automatic alignment job '{resolved}'.")
        return job

    def _run(self, job: _AutoAlignJob, run_window: RunWindow) -> None:
        with job.lock:
            job.state = "running"
        try:
            for chapter in job.spec.chapters:
                for window in job.spec.windows.get(chapter, []):
                    if self._cancelled(job):
                        return
                    key = _key(chapter, window)
                    with job.lock:
                        job.current = {"chapter": chapter, "verses": list(window)}
                        job.stage = f"Asking about {chapter}:{window[0]}-{window[-1]}"
                    try:
                        result = run_window(chapter, list(window), job.id)
                    except Exception as exc:  # one bad window must not sink the chapter
                        if self._cancelled(job):
                            return
                        with job.lock:
                            job.results[key] = {"chapter": chapter, "verses": list(window), "status": "failed", "error": str(exc)}
                        continue
                    # A request already in flight when cancel arrived has written;
                    # its verdicts are kept (they are real), nothing after it runs.
                    with job.lock:
                        if result.get("unavailable"):
                            job.unavailable = result["unavailable"]
                            job.state = "failed"
                            job.error = str(result["unavailable"].get("message") or "No AI provider is configured.")
                            job.stage = "No AI provider"
                            job.finished_at = _now()
                            job.current = None
                            return
                        for item in result.get("results", ()):
                            job.verdicts[f"{chapter}:{item['verse']}"] = item.get("verdict", "")
                        usage = result.get("usage") or {}
                        job.usage["calls"] += int(usage.get("calls", 0) or 0)
                        job.usage["totalTokens"] += int(usage.get("totalTokens", 0) or 0)
                        job.usage["estimatedCostUSD"] = round(
                            job.usage["estimatedCostUSD"] + float(usage.get("estimatedCostUSD", 0.0) or 0.0), 6,
                        )
                        job.results[key] = {
                            "chapter": chapter, "verses": list(window), "status": "succeeded", "error": None,
                            "verdicts": {i["verse"]: i.get("verdict") for i in result.get("results", ())},
                            "failures": list(result.get("failures", ())),
                        }
            with job.lock:
                failed = [r for r in job.results.values() if r.get("status") == "failed"]
                job.state = "failed" if failed else "succeeded"
                job.error = f"{len(failed)} window(s) failed." if failed else None
                job.stage = "Finished with failures" if failed else "Complete"
                job.current = None
                job.finished_at = _now()
        except Exception as exc:
            if self._cancelled(job):
                return
            with job.lock:
                job.state = "failed"
                job.error = str(exc)
                job.stage = "Failed"
                job.finished_at = _now()

    @staticmethod
    def _cancelled(job: _AutoAlignJob) -> bool:
        if not job.cancel_event.is_set():
            return False
        with job.lock:
            job.state = "cancelled"
            job.stage = "Cancelled"
            job.current = None
            job.finished_at = _now()
        return True


def _key(chapter: str, window: list[str]) -> str:
    return f"{chapter}:{window[0]}-{window[-1]}"
