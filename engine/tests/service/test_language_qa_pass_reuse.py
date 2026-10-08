"""A chapter check job reuses a current Language QA pass, and the background
worker does not repeat a pass a job just published (#232). Whatever changes an
input -- an edit, a decision, a file changed outside Bridge -- still gets a
fresh pass."""
from __future__ import annotations

import json
import os
import time

from bridge_service import BridgeEngine
from tests.support.projects import call, fixture_project, wait_for_job  # noqa: F401 - fixture


def _counting(engine):
    """Count passes that ran to completion (a cancelled one returns None)."""
    manager = engine._language_qa
    original = manager._scan_locked
    done: list[int] = []

    def scan_locked(*args, **kwargs):
        result = original(*args, **kwargs)
        if result is not None:
            done.append(1)
        return result

    manager._scan_locked = scan_locked
    return done


def _settle(engine, path, quiet=1.5, timeout=30.0):
    """Wait until Language QA is completed and its worker has stopped."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = call(engine, "languageQa.status", {"projectPath": str(path), "limit": 0})["result"]
        if status["state"] == "completed" and engine._language_qa._thread is None:
            time.sleep(quiet)  # past the worker's debounce: nothing else starts
            if engine._language_qa._thread is None:
                return status
        time.sleep(0.05)
    raise AssertionError("Language QA did not settle")


def _chapter_job(engine):
    started = call(engine, "checks.start", {
        "scope": "chapter", "chapters": ["1"], "checks": ["languageQa"],
    })["result"]
    finished = wait_for_job(engine, started["jobId"], timeout=30)
    assert finished["state"] == "succeeded"
    return finished


def test_a_chapter_job_reuses_a_current_pass_and_rescans_after_an_edit(fixture_project):
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})
    _settle(engine, fixture_project)
    done = _counting(engine)

    finished = _chapter_job(engine)
    _settle(engine, fixture_project)
    assert done == []  # nothing changed since the open's pass
    assert "1:1" in finished["results"]

    text = call(engine, "verse.get", {"chapter": "1", "verse": "1"})["result"]["text"]
    edited = call(engine, "verse.edit", {"chapter": "1", "verse": "1", "newText": text.replace(" ", "  ", 1)})
    assert not edited.get("error"), edited
    assert call(engine, "verse.get", {"chapter": "1", "verse": "1"})["result"]["text"] != text
    _chapter_job(engine)
    _settle(engine, fixture_project)
    assert len(done) == 1  # the edit's pass, once: the job's or the worker's, not both


def test_a_file_changed_outside_bridge_is_checked_again(fixture_project):
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})
    _settle(engine, fixture_project)
    done = _counting(engine)

    chapter = fixture_project / "rut" / "1.json"
    chapter.write_text(json.dumps({"1": "ஆதியிலே  தேவன் வானத்தையும் படைத்தார்."}, ensure_ascii=False),
                       encoding="utf-8")
    stat = chapter.stat()
    os.utime(chapter, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    _chapter_job(engine)
    assert len(done) >= 1


def test_reopen_and_an_immediate_chapter_job_run_one_pass(fixture_project):
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})
    _settle(engine, fixture_project)
    done = _counting(engine)

    call(engine, "project.open", {"path": str(fixture_project)})  # bind() schedules the worker
    _chapter_job(engine)                                           # and the job runs its own pass
    _settle(engine, fixture_project)
    assert len(done) == 1
