"""Builders for the AI-review protocol tests: a fake LLM transport whose answers
are grounded in the verse, a wait for an AI job, and an imported Titus project.

Shared here so no test module imports another (CLAUDE.md rule 2, #74)."""
from __future__ import annotations

import json
import time

import pytest
from bridge_service import BridgeEngine
from tc_ai_bridge.secret_store import AppSettings
from tests.support.projects import call
from tests.support.waits import job_timeout


def _metadata(**overrides):
    value = {
        "languageId": "eng", "languageName": "English", "languageDirection": "ltr",
        "projectName": "Titus review", "bibleName": "Test Bible",
    }
    value.update(overrides)
    return value


def _grounded_fake_transport():
    """Return exact IDs/evidence from Bridge's own structured input.

    This exercises the important half of the contract: the model never gets to
    invent a target word or citation.  The production parser must resolve the
    supplied opaque ID back to text + repeated-word occurrence metadata.
    """
    alignment_payload = {"links": [], "implicit_top_ids": [], "target_only_ids": [], "review_notes": []}

    def transport(url, headers, body, timeout):
        request = json.loads(body.decode("utf-8"))
        schema_format = request.get("text", {}).get("format", {})
        assert schema_format.get("type") == "json_schema"
        assert schema_format.get("strict") is True
        if schema_format.get("name") == "tc_alignment_proposal":
            payload = alignment_payload
        else:
            review_input = json.loads(request["input"])
            check_count = len(review_input["translationCore_checks"])
            check_array_schema = schema_format["schema"]["properties"]["check_reviews"]
            assert check_array_schema["minItems"] == check_count
            assert check_array_schema["maxItems"] == check_count
            target_id = review_input["target_bottomWords"][0]["id"]
            payload = {
                "summary": "Grounded automatic review.",
                "check_reviews": [
                    {
                        "tool": check["tool"], "group_id": check["groupId"],
                        "check_id": check["checkId"], "source_quote": check.get("source_quote") or "",
                        "selection_ids": [target_id], "nothing_to_select": False,
                        "verdict": "pass", "severity": "info", "rationale": "Supported by bundled evidence.",
                        "suggested_correction": "", "confidence": 0.91,
                        "evidence_ids": check["evidence_ids"][:1],
                    }
                    for check in review_input["translationCore_checks"]
                ],
                "qa_issues": [],
            }
        return 200, json.dumps({
            "output_text": json.dumps(payload),
            "usage": {"input_tokens": 200, "output_tokens": 80, "total_tokens": 280,
                      "input_tokens_details": {"cached_tokens": 0}},
        }).encode("utf-8")

    return transport


def _wait_for_ai_job(engine, job_id, timeout=10):
    deadline = time.monotonic() + job_timeout(timeout)
    while time.monotonic() < deadline:
        status = call(engine, "ai.review.status", {"jobId": job_id})
        assert status["success"] is True, status
        snapshot = status["result"]
        if snapshot["state"] in {"succeeded", "failed", "cancelled"}:
            return snapshot
        time.sleep(0.02)
    raise AssertionError("AI review job did not finish")


@pytest.fixture
def imported_titus_project(tmp_path):
    isolated = AppSettings(path=tmp_path / "settings.json")
    engine = BridgeEngine(settings=isolated)
    source = tmp_path / "57-TIT.usfm"
    source.write_text(
        "\\id TIT\n\\h Titus\n\\c 1\n\\p\n\\v 1 Paul, a servant of God.\n", encoding="utf-8",
    )
    result = engine.import_project(str(source), _metadata())
    # Materializes real translationNotes/translationWords (and, since this phase,
    # translationAcademy is bundled too) so prepare_verse_review has real evidence.
    call(engine, "verse.runChecks", {"chapter": "1", "verse": "1", "checks": ["local"]})
    return isolated, result["path"]
