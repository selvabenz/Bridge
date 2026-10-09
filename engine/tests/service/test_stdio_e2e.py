"""End-to-end coverage for the newline-delimited sidecar process boundary."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path


from tests.support.paths import ENGINE_ROOT
from tests.support.waits import job_timeout


class Sidecar:
    def __init__(self) -> None:
        self.process = subprocess.Popen(
            [sys.executable, str(ENGINE_ROOT / "main.py")],
            cwd=ENGINE_ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        ready = self._read()
        assert ready["id"] == "__ready__"
        self.sequence = 0

    def _read(self) -> dict:
        assert self.process.stdout is not None
        line = self.process.stdout.readline()
        assert line, "sidecar exited before returning a response"
        return json.loads(line)

    def request(self, method: str, params: dict) -> dict:
        self.sequence += 1
        request_id = f"e2e-{self.sequence}"
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps({
            "id": request_id,
            "method": method,
            "params": params,
        }, ensure_ascii=False) + "\n")
        self.process.stdin.flush()
        response = self._read()
        assert response["id"] == request_id
        assert response["success"] is True, response
        return response

    def close(self) -> None:
        self.process.terminate()
        try:
            self.process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.communicate(timeout=5)


def _wait_for_job(sidecar: Sidecar, job_id: str) -> dict:
    deadline = time.monotonic() + job_timeout(20)
    while time.monotonic() < deadline:
        snapshot = sidecar.request("checks.status", {"jobId": job_id})["result"]
        if snapshot["state"] in {"succeeded", "failed", "cancelled"}:
            return snapshot
        time.sleep(0.02)
    raise AssertionError("background sidecar job did not finish")


def test_import_check_decide_restart_edit_and_export_over_stdio(tmp_path):
    source = tmp_path / "TIT.usfm"
    source.write_text(
        "\\id TIT\n\\h தீத்து\n\\c 1\n\\p\n\\v 1 தேவன்aஆதி\n",
        encoding="utf-8",
    )
    projects = tmp_path / "projects"
    aligned = tmp_path / "aligned.json"
    non_aligned = tmp_path / "non-aligned.usfm"

    sidecar = Sidecar()
    try:
        imported = sidecar.request("project.import", {
            "path": str(source),
            "destinationRoot": str(projects),
            "metadata": {
                "languageId": "tam",
                "languageName": "Tamil",
                "projectName": "Tamil Titus",
                "bibleName": "Tamil Bible",
            },
        })["result"]
        project_path = imported["path"]

        started = sidecar.request("checks.start", {
            "scope": "chapter",
            "chapters": ["1"],
            "checks": ["greekroom"],
        })["result"]
        snapshot = _wait_for_job(sidecar, started["jobId"])
        assert snapshot["state"] == "succeeded"
        findings = snapshot["results"]["1:1"]["findings"]
        finding = next(item for item in findings if item["engine"] == "wildebeest")

        sidecar.request("verse.decide", {
            "chapter": "1",
            "verse": "1",
            "findingId": finding["id"],
            "status": "accepted",
            "comment": "stdio review",
        })
    finally:
        sidecar.close()

    # A fresh process proves that review state is on disk, not only cached
    # inside the original BridgeEngine instance.
    restarted = Sidecar()
    try:
        restarted.request("project.open", {"path": project_path})
        checked = restarted.request("verse.runChecks", {
            "chapter": "1", "verse": "1", "checks": ["greekroom"],
        })["findings"]
        persisted = next(item for item in checked if item["id"] == finding["id"])
        assert persisted["status"] == "accepted"
        assert persisted["human_comment"] == "stdio review"

        restarted.request("verse.edit", {
            "chapter": "1", "verse": "1", "newText": "தேவன் ஆதி",
        })
        verse = restarted.request("verse.get", {"chapter": "1", "verse": "1"})["result"]
        assert verse["text"] == "தேவன் ஆதி"

        restarted.request("export.aligned", {"outputPath": str(aligned)})
        restarted.request("export.nonAligned", {"outputPath": str(non_aligned)})
    finally:
        restarted.close()

    aligned_data = json.loads(aligned.read_text(encoding="utf-8"))
    assert aligned_data["chapters"]["1"]["1"]["text"] == "தேவன் ஆதி"
    exported_usfm = non_aligned.read_text(encoding="utf-8")
    assert "\\v 1 தேவன் ஆதி" in exported_usfm
    assert (Path(project_path) / ".apps" / "translationCoreAI" / "transactions").is_dir()



def test_uroman_loads_after_ready_and_engine_info_does_not_wait_for_it():
    """main.py starts the uroman preload after the ready line (#242):
    engine.info answers at once with names available, and names reports
    loaded once the background load finishes."""
    sidecar = Sidecar()
    try:
        started = time.monotonic()
        info = sidecar.request("engine.info", {})
        elapsed = time.monotonic() - started
        names = info["result"]["greekRoom"]["adapters"]["names"]
        assert names["available"] is True
        assert elapsed < 2.0, f"engine.info took {elapsed:.2f}s"
        deadline = time.monotonic() + job_timeout(30)
        while names.get("state") != "loaded" and time.monotonic() < deadline:
            time.sleep(0.25)
            names = sidecar.request("engine.info", {})["result"]["greekRoom"]["adapters"]["names"]
        assert names["state"] == "loaded", names
    finally:
        sidecar.close()
