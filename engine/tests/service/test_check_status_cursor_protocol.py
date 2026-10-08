"""checks.status `since` over the protocol (#229): additive and optional. No
cursor returns every verse, as before; a cursor returns only what is new; a
bad one is refused rather than silently read as "everything"."""
from __future__ import annotations

import json

from bridge_service import BridgeEngine
from tests.support.projects import call, fixture_project, wait_for_job  # noqa: F401 - fixture


def _two_chapters(root):
    (root / "rut" / "2.json").write_text(json.dumps({"1": "இரண்டாம் அதிகாரம்.", "2": "மூன்றாம் வசனம்."},
                                                    ensure_ascii=False), encoding="utf-8")
    alignment_dir = root / ".apps" / "translationCore" / "alignmentData" / "rut"
    (alignment_dir / "2.json").write_text(json.dumps({
        "1": {"alignments": [], "wordBank": []}, "2": {"alignments": [], "wordBank": []},
    }), encoding="utf-8")


def test_status_without_since_is_unchanged_and_since_returns_only_new_verses(fixture_project):
    _two_chapters(fixture_project)
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})
    started = call(engine, "checks.start", {"scope": "book", "checks": ["greekroom"]})["result"]
    finished = wait_for_job(engine, started["jobId"])
    assert finished["state"] == "succeeded"

    full = call(engine, "checks.status", {"jobId": started["jobId"]})["result"]
    assert set(full["results"]) == {"1:1", "2:1", "2:2"}
    assert full["resultsSince"] == 0 and full["resultsCursor"] == 3

    tail = call(engine, "checks.status", {"jobId": started["jobId"], "since": 1})["result"]
    assert set(tail["results"]) == {"2:1", "2:2"}
    assert tail["results"] == {k: full["results"][k] for k in ("2:1", "2:2")}
    assert tail["completedVerses"] == 3 and tail["percent"] == 100

    nothing = call(engine, "checks.status", {"jobId": started["jobId"], "since": 3})["result"]
    assert nothing["results"] == {} and nothing["resultsCursor"] == 3


def test_status_refuses_a_since_that_is_not_a_non_negative_integer(fixture_project):
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})
    started = call(engine, "checks.start", {"scope": "chapter", "chapters": ["1"], "checks": ["greekroom"]})["result"]
    wait_for_job(engine, started["jobId"])
    for bad in (-1, "2", 1.5, True):
        response = call(engine, "checks.status", {"jobId": started["jobId"], "since": bad})
        assert response.get("error"), bad
