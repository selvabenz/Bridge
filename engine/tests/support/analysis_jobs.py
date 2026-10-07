"""Builders for the analysis-job tests (Stage 9A.4): a wait for a job, and stub
stages that record the order Stages 5-8 ran in, with a cache result or a
failure on demand.

Shared here so no test module imports another (CLAUDE.md rule 2, #74)."""
from __future__ import annotations

import threading
import time
from typing import Any

from tc_ai_bridge.analysis_jobs import AnalysisJobManager
from tests.support.waits import job_timeout


def _wait(manager: AnalysisJobManager, job_id: str, timeout: float = 5.0) -> dict[str, Any]:
    deadline = time.monotonic() + job_timeout(timeout)
    while time.monotonic() < deadline:
        result = manager.status(job_id)
        if result["overallStatus"] in {
            "COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED", "CANCELLED",
        }:
            return result
        time.sleep(0.01)
    raise AssertionError(f"analysis job {job_id} did not finish")


class _Provider:
    provider_id = "unavailable"
    provider_version = "v1"
    model_hash = "unavailable"
    available = False
    fixture_only = False

    def descriptor(self) -> dict[str, Any]:
        return {
            "providerId": self.provider_id,
            "providerVersion": self.provider_version,
            "modelHash": self.model_hash,
            "available": self.available,
            "fixtureOnly": self.fixture_only,
        }


class _Stage:
    def __init__(
        self, name: str, calls: list[str], *, cache: str = "MISS",
        fail: bool = False, entered: threading.Event | None = None,
        release: threading.Event | None = None, search_incomplete: int = 0,
    ) -> None:
        self.name = name
        self.calls = calls
        self.cache = cache
        self.fail = fail
        self.entered = entered
        self.release = release
        self.search_incomplete = search_incomplete
        self.embedding_provider = _Provider()

    def _result(self) -> dict[str, Any]:
        if self.fail:
            raise RuntimeError(f"{self.name} failed")
        if self.entered:
            self.entered.set()
        if self.release:
            assert self.release.wait(3)
        result: dict[str, Any] = {
            "id": f"{self.name.lower()}-run",
            "cacheStatus": self.cache,
            "diagnostics": {},
            "elapsedSeconds": 0.01,
        }
        if self.name == "TARGET_INVENTORY":
            result["targetContentHash"] = "target-hash"
        if self.name == "LOCATION":
            result.update({
                "diagnostics": {"searchIncomplete": self.search_incomplete},
                "relationships": [],
            })
        if self.name == "QA":
            result.update({
                "findings": [],
                "phaseProfile": {
                    "sourceCoverageAudit": 0.01,
                    "targetSupportAudit": 0.02,
                    "findingSynthesis": 0.0,
                    "persistence": 0.03,
                },
            })
        return result

    def build_range(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        self.calls.append(self.name)
        return self._result()

    def run_range(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        self.calls.append(self.name)
        return self._result()


def _stub_stages(runtime: Any, *, cache: str = "MISS", fail: str = "") -> list[str]:
    calls: list[str] = []
    runtime.source_semantic = _Stage("SOURCE_INVENTORY", calls, cache=cache, fail=fail == "SOURCE_INVENTORY")
    runtime.target_semantic = _Stage("TARGET_INVENTORY", calls, cache=cache, fail=fail == "TARGET_INVENTORY")
    runtime.semantic_location = _Stage("LOCATION", calls, cache=cache, fail=fail == "LOCATION")
    runtime.meaning_analysis = _Stage("MEANING", calls, cache=cache, fail=fail == "MEANING")
    runtime.qa_audit = _Stage("QA", calls, cache=cache, fail=fail == "QA")
    return calls
