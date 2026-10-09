"""
Whole-collection QA report (tc_ai_bridge/qa_report.py + report_jobs.py,
exposed as report.generate/status/get/cancel/export).

The report reads only what earlier checks and decisions left on disk, so
every test here plants that state the way the real writers do -- a check
job's finding snapshot, checkCache.json, the tN/tW index plus a
checkData/selections record, a wordAlignment/invalid mark, an aiReview
record -- and asserts on the rows and coverage the report derives from it.
"""
import json
import time
from pathlib import Path

import pytest

from bridge_service import BridgeEngine, _stable_finding_id
from greek_room_engine.models.finding import QaFinding, FindingCategory, Severity
from tc_ai_bridge.qa_report import (
    CATEGORY_AI_REVIEW, CATEGORY_ALIGNMENT, CATEGORY_GREEK_ROOM, CATEGORY_LANGUAGE_QA, CATEGORY_TN, CATEGORY_TW,
    stable_finding_id, summarize_rows, write_report_rows,
)

from tests.support.projects import _write_minimal_book, call, fixture_project, two_book_collection, wait_for_job  # noqa: F401
from tests.support.waits import job_timeout


def wait_for_report(engine, job_id, timeout=5.0):
    deadline = time.monotonic() + job_timeout(timeout)
    while time.monotonic() < deadline:
        snapshot = call(engine, "report.status", {"jobId": job_id})["result"]
        if snapshot["state"] in {"succeeded", "failed", "cancelled"}:
            return snapshot
        time.sleep(0.01)
    raise AssertionError(f"report job {job_id} did not finish")


def generate(engine):
    started = call(engine, "report.generate")
    assert started["success"] is True, started
    finished = wait_for_report(engine, started["result"]["jobId"])
    fetched = call(engine, "report.get", {"jobId": started["result"]["jobId"]})
    assert fetched["success"] is True, fetched
    return finished, fetched["result"]["report"]


def _write_helps_index(root: Path, tool: str, group: str, entries: list[dict]) -> None:
    path = root / ".apps" / "translationCore" / "index" / tool / "rut" / f"{group}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")


def _check_entry(tool, group, check_id, *, selections=False, nothing=False, invalidated=False, note=""):
    return {
        "contextId": {
            "reference": {"bookId": "rut", "chapter": "1", "verse": "1"},
            "tool": tool, "groupId": group, "checkId": check_id,
            "quoteString": "θεός", "occurrence": 1, "occurrenceNote": note,
        },
        "selections": selections, "nothingToSelect": nothing, "invalidated": invalidated,
    }


def _write_selection_state(root: Path, tool: str, group: str, check_id: str, username: str, ts: str) -> None:
    path = root / ".apps" / "translationCore" / "checkData" / "selections" / "rut" / "1" / "1" / f"{ts.replace(':', '_')}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "contextId": {"reference": {"bookId": "rut", "chapter": "1", "verse": "1"},
                      "tool": tool, "groupId": group, "checkId": check_id},
        "selections": [{"text": "தேவன்", "occurrence": 1, "occurrences": 1}],
        "username": username, "modifiedTimestamp": ts,
    }, ensure_ascii=False), encoding="utf-8")


def test_stable_ids_match_bridge_service():
    """qa_report re-derives finding ids to look up reviewer decisions; the
    two hash functions must never drift apart."""
    kwargs = dict(chapter="3", verse="4-5", engine="translationCore", check_type="tn-1", disambiguator="figs-metaphor")
    assert stable_finding_id(**kwargs) == _stable_finding_id(**kwargs)


def test_check_job_leaves_a_finding_snapshot_the_report_reads(fixture_project, monkeypatch):
    """Before this snapshot only usfm/names findings survived a job on disk;
    the report needs the Wildebeest/local ones too."""
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})

    def fake_checks(project, chapter, verse, checks, **_kwargs):
        return [QaFinding(
            id=_stable_finding_id(chapter=chapter, verse=verse, engine="wildebeest",
                                  check_type="wildebeest.script.mixed", disambiguator="0:3:ஆதி"),
            project_id=str(project.summary.path), book="rut", chapter=int(chapter), verse=int(verse),
            start_offset=0, end_offset=3, original_text="ஆதி", engine="wildebeest",
            check_type="wildebeest.script.mixed", category=FindingCategory.UNICODE, severity=Severity.HIGH,
            confidence=0.9, suggested_replacement="ஆதியிலே", explanation="Latin character mixed into Tamil text.",
        )]

    monkeypatch.setattr(engine, "_run_verse_checks_for_project", fake_checks)
    started = call(engine, "checks.start", {"scope": "chapter", "chapters": ["1"], "checks": ["greekroom"]})["result"]
    assert wait_for_job(engine, started["jobId"])["state"] == "succeeded"

    snapshot = engine.project.load_check_findings_snapshot("1")
    assert list(snapshot) == ["1"]
    assert snapshot["1"][0]["check_type"] == "wildebeest.script.mixed"

    _, report = generate(engine)
    assert report["bookCount"] == 1
    book = report["books"][0]
    assert book["checks"]["greekRoom"] == {
        "state": "complete", "checked": 1, "total": 1, "percent": 100.0,
        "checkedChapters": 1, "chapterCount": 1,
        "engines": {"wildebeest": True, "usfm": False, "names": False},
        "run": 1, "passed": 0, "failed": 1,
    }
    [row] = report["rows"]
    assert row["category"] == CATEGORY_GREEK_ROOM
    assert row["engine"] == "wildebeest"
    assert row["reference"] == "RUT 1:1"
    assert row["issue"] == "wildebeest.script.mixed"
    assert row["explanation"] == "Latin character mixed into Tamil text."
    assert row["aiProposal"] == "ஆதியிலே"
    assert (row["status"], row["resolution"], row["result"], row["fixedBy"]) == ("open", "unresolved", "fail", "")

    # A reviewer's decision (recorded through verse.decide, the same path
    # the editor uses) closes the row and shows up as human-fixed.
    finding_id = row["id"].split(":", 1)[1]
    call(engine, "verse.decide", {"chapter": "1", "verse": "1", "findingId": finding_id,
                                  "status": "accepted", "comment": "Real problem, fixed in text."})
    _, report = generate(engine)
    [row] = report["rows"]
    assert (row["status"], row["resolution"], row["result"], row["fixedBy"]) == ("accepted", "resolved", "pass", "human")
    assert row["note"] == "Real problem, fixed in text."
    assert row["decidedAt"]
    assert report["books"][0]["checks"]["greekRoom"]["passed"] == 1
    assert report["issues"] == {
        "total": 1, "resolved": 1, "unresolved": 0,
        "byCategory": {
            CATEGORY_GREEK_ROOM: {"total": 1, "resolved": 1, "unresolved": 0},
            CATEGORY_TN: {"total": 0, "resolved": 0, "unresolved": 0},
            CATEGORY_TW: {"total": 0, "resolved": 0, "unresolved": 0},
            CATEGORY_ALIGNMENT: {"total": 0, "resolved": 0, "unresolved": 0},
            CATEGORY_AI_REVIEW: {"total": 0, "resolved": 0, "unresolved": 0},
            CATEGORY_LANGUAGE_QA: {"total": 0, "resolved": 0, "unresolved": 0},
        },
        "openBySeverity": {}, "byFixedBy": {"human": 1, "machine": 0, "unresolved": 0},
    }


# ---- Phase 4.2: Language QA in the report, the exception queue and the gate ----

def _language_qa_job(engine):
    """A book job with the Language QA stage over a verse with a vallinam finding."""
    _r = call(engine, "verse.edit", {"chapter": "1", "verse": "1", "newText": "அந்த காகம் பறந்தது."})
    assert _r["success"], _r
    started = call(engine, "checks.start", {"scope": "book", "checks": ["languageQa"]})["result"]
    assert wait_for_job(engine, started["jobId"], timeout=30)["state"] == "succeeded"


@pytest.fixture
def lqa_engine(fixture_project):
    from tc_ai_bridge.language_qa_jobs import LanguageQaManager
    engine = BridgeEngine()
    engine._language_qa = LanguageQaManager(debounce=0, yield_seconds=0)
    _r = call(engine, "project.open", {"path": str(fixture_project)})
    assert _r["success"], _r
    yield engine
    engine._language_qa.unbind()


def test_language_qa_rows_carry_their_rule_columns_and_follow_decisions(lqa_engine, tmp_path):
    engine = lqa_engine
    _language_qa_job(engine)
    _, report = generate(engine)
    rows = [r for r in report["rows"] if r["category"] == CATEGORY_LANGUAGE_QA]
    [row] = [r for r in rows if r["ruleId"] == "ta-irv/sandhi.vallinam.demonstrative"]
    assert (row["languageQaCategory"], row["layer"], row["confidence"]) == ("sandhi", "pattern", "medium")
    assert row["packVersion"] == "ta-irv@1.1.0" and row["suggestions"] == "அந்தக் காகம்"
    assert row["issue"] == "அந்த காகம்" and row["resolution"] == "unresolved"
    book = report["books"][0]["checks"]["languageQa"]
    assert book["state"] == "partial" and book["open"] == len(rows) and book["byCategory"]["sandhi"]["open"] == 1
    # The CSV carries the Language QA columns.
    out = tmp_path / "report.csv"
    write_report_rows(out, "csv", rows)
    header = out.read_text(encoding="utf-8-sig").splitlines()[0]
    for column in ("ruleId", "packVersion", "layer", "confidence", "suggestions", "houseStyleSuppressed"):
        assert column in header
    # An ignore through verse.decide resolves the row without a new job.
    finding_id = row["id"].split(":", 2)[2]
    issue = {"source": "languageQa", "rule": "tamil.vallinam-missing"}
    assert call(engine, "verse.decide", {"chapter": "1", "verse": "1", "findingId": finding_id,
                                         "status": "ignored", "issue": issue})["success"]
    _, report = generate(engine)
    [row] = [r for r in report["rows"] if r["id"].endswith(finding_id)]
    assert (row["status"], row["resolution"]) == ("ignored", "resolved")


def test_a_high_high_language_qa_finding_blocks_the_gate_and_joins_the_exception_queue(lqa_engine):
    from tc_ai_bridge.analytics import exception_first_queue
    from tc_ai_bridge.reporting import ReportService
    engine = lqa_engine
    engine.project.record_terminology_rule("god", ["இறைவன்"], rejected_renderings=["தேவன்"])
    _r = call(engine, "verse.edit", {"chapter": "1", "verse": "1", "newText": "தேவன் பேசினார்."})
    assert _r["success"], _r
    started = call(engine, "checks.start", {"scope": "book", "checks": ["languageQa"]})["result"]
    assert wait_for_job(engine, started["jobId"], timeout=30)["state"] == "succeeded"
    # terminology.deprecated-form is severity high, confidence high.
    report = ReportService(engine.project).build_book_report()
    gate = report["publicationGate"]
    assert gate["languageQaBlocking"] == 1 and gate["readyForHumanPublicationSignoff"] is False
    assert report["languageQa"]["blockingFindings"][0]["originalText"] == "தேவன்"
    [row] = [r for r in exception_first_queue(engine.project) if (r["chapter"], r["verse"]) == ("1", "1")]
    assert row["languageQa"] == 1 and row["languageQaFindings"][0]["ruleId"].endswith("terminology.deprecated-form")


def test_many_open_medium_findings_add_an_advisory_line_that_never_blocks():
    from tc_ai_bridge.reporting import ReportService
    gate = ReportService._publication_gate({}, [], [], [], {"blocking": 0, "medium": 51}, 50)
    assert gate["readyForHumanPublicationSignoff"] is True
    assert gate["advisories"] == ["51 open medium Language QA findings (more than 50)."]
    assert ReportService._publication_gate({}, [], [], [], {"blocking": 0, "medium": 50}, 50)["advisories"] == []
    assert ReportService._publication_gate({}, [], [], [], {"blocking": 1, "medium": 0}, 50)[
        "readyForHumanPublicationSignoff"] is False


# export.aligned writes .json here: the fixture's alignment does not match its
# edited verse text, which the USFM renderer refuses; the gate runs first either way.
@pytest.mark.parametrize("method,suffix", [("export.aligned", ".json"), ("export.nonAligned", ".usfm")])
def test_export_is_blocked_by_the_publication_gate_until_overridden_and_the_override_is_recorded(
        lqa_engine, tmp_path, method, suffix):
    engine = lqa_engine
    clean = tmp_path / f"clean{suffix}"
    first = call(engine, method, {"outputPath": str(clean)})
    assert first["success"], first
    assert first["result"]["written"] is True and not first["result"].get("overridden")  # nothing blocks yet
    engine.project.record_terminology_rule("god", ["இறைவன்"], rejected_renderings=["தேவன்"])
    _r = call(engine, "verse.edit", {"chapter": "1", "verse": "1", "newText": "தேவன் பேசினார்."})
    assert _r["success"], _r
    started = call(engine, "checks.start", {"scope": "book", "checks": ["languageQa"]})["result"]
    assert wait_for_job(engine, started["jobId"], timeout=30)["state"] == "succeeded"
    out = tmp_path / f"out{suffix}"
    refused = call(engine, method, {"outputPath": str(out)})["result"]
    assert refused["written"] is False and refused["blocked"] is True and not out.exists()
    assert refused["gate"]["counts"]["languageQa"] == 1
    assert refused["gate"]["items"][0]["source"] == "languageQa"
    written = call(engine, method, {"outputPath": str(out), "override": True})["result"]
    assert written["written"] is True and written["overridden"] is True and out.exists()
    [override] = [d for d in engine.project.project_qa_decisions() if d.get("issueKey") == "export.override"]
    assert override["issue"]["source"] == "export" and override["issue"]["counts"]["languageQa"] == 1
    assert override["issue"]["openItems"][0]["summary"].endswith("தேவன்")
    # The export ledger (6.5) records the override beside the export.
    ledger = (tmp_path / "rut.language-qa-changes.csv").read_text(encoding="utf-8-sig")
    assert "export-override" in ledger and "1 open blocking item(s)" in ledger
    # The report's gate names the same items: one definition.
    from tc_ai_bridge.reporting import ReportService
    assert ReportService(engine.project).build_book_report()["publicationGate"]["exportBlocking"]["items"] == \
        refused["gate"]["items"]


def test_a_job_without_the_stage_keeps_the_previous_language_qa_snapshot(lqa_engine):
    engine = lqa_engine
    _language_qa_job(engine)
    before = engine.project.language_qa_snapshots()
    assert before
    started = call(engine, "checks.start", {"scope": "book", "checks": ["local"]})["result"]
    assert wait_for_job(engine, started["jobId"], timeout=30)["state"] == "succeeded"
    assert engine.project.language_qa_snapshots() == before


def test_report_lists_lazy_and_missing_siblings_without_materializing_them(tmp_path, fixture_project):
    lazy_dir = tmp_path / "gen"
    (lazy_dir / ".bridge").mkdir(parents=True)
    (lazy_dir / ".bridge" / "lazy-import.json").write_text(json.dumps({"bookId": "GEN", "bookName": "Genesis"}), encoding="utf-8")
    (fixture_project / ".bridge").mkdir(parents=True, exist_ok=True)
    (fixture_project / ".bridge" / "collection.json").write_text(json.dumps({
        "projects": [
            {"directoryName": "rut", "bookId": "rut", "bookName": "Ruth"},
            {"directoryName": "gen", "bookId": "gen", "bookName": "Genesis", "lazy": True},
            {"directoryName": "exo", "bookId": "exo", "bookName": "Exodus"},
        ],
    }), encoding="utf-8")

    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})
    finished, report = generate(engine)

    assert finished["state"] == "succeeded"
    assert finished["totalBooks"] == 3 and finished["completedBooks"] == 3
    by_id = {b["bookId"]: b for b in report["books"]}
    assert by_id["gen"]["lazy"] is True and by_id["gen"]["checks"]["greekRoom"]["state"] == "not_run"
    assert by_id["exo"]["missing"] is True
    assert by_id["rut"]["lazy"] is False and by_id["rut"]["verseCount"] == 1
    # Nothing normalized the lazy sibling just to say it was never checked.
    assert not (lazy_dir / "manifest.json").exists()
    assert (lazy_dir / ".bridge" / "lazy-import.json").exists()


def test_tn_tw_rows_come_from_the_live_index_with_selection_provenance(fixture_project):
    """Pending, invalidated, human-selected, Bridge-AI-selected and
    pre-existing translationCore selections each land as their own row
    state; the AI review's proposal rides along on the matching check."""
    root = fixture_project
    _write_helps_index(root, "translationNotes", "figs-metaphor", [
        _check_entry("translationNotes", "figs-metaphor", "tn-pending", note="Review the metaphor."),
    ])
    _write_helps_index(root, "translationWords", "god", [
        _check_entry("translationWords", "god", "tw-ai", selections=[{"text": "தேவன்", "occurrence": 1, "occurrences": 1}]),
        _check_entry("translationWords", "god", "tw-human", selections=[{"text": "தேவன்", "occurrence": 1, "occurrences": 1}]),
        _check_entry("translationWords", "god", "tw-tc", selections=[{"text": "தேவன்", "occurrence": 1, "occurrences": 1}]),
        _check_entry("translationWords", "god", "tw-invalid", selections=[{"text": "x", "occurrence": 1, "occurrences": 1}], invalidated=True),
        _check_entry("translationWords", "god", "tw-nts", nothing=True),
    ])
    _write_selection_state(root, "translationWords", "god", "tw-ai", "Bridge AI", "2026-09-01T10:00:00.000Z")
    _write_selection_state(root, "translationWords", "god", "tw-human", "Revant", "2026-09-01T10:00:01.000Z")

    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(root)})
    engine.project.record_ai_review_result("1", "1", {
        "summary": "ok", "qaIssues": [
            {"code": "MEANING_SHIFT", "severity": "high", "title": "Possible meaning shift",
             "detail": "The verb tense differs from the source.", "source": "ai"},
        ],
        "checkReviews": [{
            "tool": "translationNotes", "group_id": "figs-metaphor", "check_id": "tn-pending",
            "verdict": "problem", "severity": "high", "rationale": "Metaphor dropped.",
            "suggested_correction": "Render the metaphor explicitly.", "proposed_selection_text": ["தேவன்"],
        }],
    })

    _, report = generate(engine)
    rows = {r["id"].split(":", 1)[1]: r for r in report["rows"]}

    tn = rows["translationNotes:1:1:tn-pending:figs-metaphor"]
    assert tn["category"] == CATEGORY_TN
    assert (tn["status"], tn["result"], tn["fixedBy"], tn["severity"]) == ("pending", "fail", "", "medium")
    assert tn["explanation"] == "Review the metaphor."
    assert tn["aiProposal"] == "Render the metaphor explicitly."
    assert tn["aiVerdict"] == "problem"

    ai = rows["translationWords:1:1:tw-ai:god"]
    assert (ai["status"], ai["result"], ai["fixedBy"], ai["fixedByDetail"]) == ("selected", "pass", "machine", "Bridge AI")
    assert ai["selection"] == "தேவன்"
    human = rows["translationWords:1:1:tw-human:god"]
    assert (human["fixedBy"], human["fixedByDetail"]) == ("human", "Revant")
    tc = rows["translationWords:1:1:tw-tc:god"]
    assert (tc["fixedBy"], tc["fixedByDetail"]) == ("human", "translationCore")
    invalid = rows["translationWords:1:1:tw-invalid:god"]
    assert (invalid["status"], invalid["result"], invalid["severity"]) == ("invalidated", "fail", "high")
    nts = rows["translationWords:1:1:tw-nts:god"]
    assert (nts["status"], nts["result"], nts["selection"]) == ("nothing_to_select", "pass", "nothing to select")

    ai_issue = rows["ai:1:1:MEANING_SHIFT:0"]
    assert ai_issue["category"] == CATEGORY_AI_REVIEW
    assert (ai_issue["issue"], ai_issue["severity"], ai_issue["result"]) == ("Possible meaning shift", "high", "fail")

    book = report["books"][0]
    assert book["checks"]["translationNotes"] == {
        "state": "partial", "available": True, "total": 1, "passed": 0, "failed": 1,
        "pending": 1, "invalidated": 0, "percent": 0.0, "run": 1,
    }
    assert book["checks"]["translationWords"] == {
        "state": "partial", "available": True, "total": 5, "passed": 4, "failed": 1,
        "pending": 0, "invalidated": 1, "percent": 80.0, "run": 5,
    }
    assert book["checks"]["aiReview"]["current"] == 1
    assert report["issues"]["byFixedBy"] == {"human": 3, "machine": 1, "unresolved": 3}
    # Rows come unresolved-first so the table opens on what still needs work.
    assert [r["resolution"] for r in report["rows"]][:3] == ["unresolved"] * 3


def test_tn_tw_marked_unavailable_until_a_resource_index_exists(fixture_project):
    (fixture_project / ".bridge").mkdir(parents=True, exist_ok=True)
    (fixture_project / ".bridge" / "import.json").write_text(json.dumps({
        "capabilities": {"translationNotes": "requires-resource-index", "translationWords": "requires-resource-index"},
    }), encoding="utf-8")
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})
    _, report = generate(engine)
    checks = report["books"][0]["checks"]
    assert checks["translationNotes"]["state"] == "unavailable"
    assert checks["translationWords"]["available"] is False


def test_pending_tn_check_ignored_by_reviewer_counts_as_resolved(fixture_project):
    """verse.decide on the TC_PENDING finding (Ignore in the editor) is a
    human decision on that check, so the report must not keep calling it
    an open failure."""
    _write_helps_index(fixture_project, "translationNotes", "figs-metaphor", [
        _check_entry("translationNotes", "figs-metaphor", "tn-1", note="Review."),
    ])
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})
    finding_id = _stable_finding_id(chapter="1", verse="1", engine="translationCore",
                                    check_type="tn-1", disambiguator="figs-metaphor")
    call(engine, "verse.decide", {"chapter": "1", "verse": "1", "findingId": finding_id,
                                  "status": "ignored", "comment": "Not applicable in Tamil."})
    _, report = generate(engine)
    [row] = [r for r in report["rows"] if r["category"] == CATEGORY_TN]
    assert (row["status"], row["resolution"], row["fixedBy"], row["note"]) == ("ignored", "resolved", "human", "Not applicable in Tamil.")


def test_alignment_invalid_mark_becomes_an_unresolved_row(fixture_project):
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})
    engine.project.mark_word_alignment_invalid("1", "1")
    _, report = generate(engine)
    [row] = [r for r in report["rows"] if r["category"] == CATEGORY_ALIGNMENT]
    assert (row["checkType"], row["severity"], row["result"]) == ("WA_INVALID", "high", "fail")
    alignment = report["books"][0]["checks"]["alignment"]
    assert (alignment["invalid"], alignment["complete"], alignment["failed"]) == (1, 0, 1)


def test_cached_usfm_findings_and_collection_totals(two_book_collection):
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(two_book_collection)})
    engine.project.save_check_cache_section("usfm", "hash", [{
        "id": "usfm-1", "chapter": 1, "verse": 1, "engine": "usfm", "check_type": "usfm.marker.unknown",
        "category": "structure", "severity": "medium", "explanation": "Unknown marker \\zz.", "status": "open",
    }])
    _, report = generate(engine)
    assert report["bookCount"] == 2
    assert [b["bookId"] for b in report["books"]] == ["rut", "gen"]
    [row] = report["rows"]
    assert (row["book"], row["engine"], row["issue"], row["explanation"]) == ("rut", "usfm", "usfm.marker.unknown", "Unknown marker \\zz.")
    assert report["books"][0]["checks"]["greekRoom"]["engines"]["usfm"] is True
    assert report["checks"]["alignment"]["total"] == 2
    assert report["checkResults"] == {"run": 0, "passed": 0, "failed": 0}
    assert report["issues"]["unresolved"] == 1


def test_report_get_before_generate_and_export_errors(fixture_project, tmp_path):
    engine = BridgeEngine()
    assert call(engine, "report.status")["error"]["code"] == "report_not_found"
    call(engine, "project.open", {"path": str(fixture_project)})
    bad = call(engine, "report.export", {"outputPath": str(tmp_path / "x.pdf"), "format": "pdf", "rows": []})
    assert bad["success"] is False and "csv or tsv" in bad["error"]["message"]
    missing_path = call(engine, "report.export", {"outputPath": "", "format": "csv", "rows": []})
    assert missing_path["success"] is False


def test_export_writes_filtered_rows_as_csv_and_tsv_with_bom(fixture_project, tmp_path):
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})
    rows = [{
        "category": "translationWords", "book": "rut", "chapter": "1", "verse": "1",
        "issue": "god: θεός", "explanation": "Key term, contains \"quotes\", and a comma",
        "aiProposal": "தேவன்", "fixedBy": "machine", "result": "pass",
    }]
    columns = [{"key": "category", "label": "Error category"}, {"key": "reference", "label": "Reference"},
               {"key": "explanation", "label": "Issue and explanation"}, {"key": "aiProposal", "label": "AI proposal"}]

    out = call(engine, "report.export", {"outputPath": str(tmp_path / "issues.csv"), "format": "csv", "rows": rows, "columns": columns})
    assert out["success"] is True and out["result"]["rows"] == 1
    raw = (tmp_path / "issues.csv").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    assert text.splitlines()[0] == "Error category,Reference,Issue and explanation,AI proposal"
    assert text.splitlines()[1] == 'translationWords,,"Key term, contains ""quotes"", and a comma",தேவன்'

    out = call(engine, "report.export", {"outputPath": str(tmp_path / "issues.tsv"), "format": "tsv", "rows": rows, "columns": columns})
    assert out["success"] is True
    lines = (tmp_path / "issues.tsv").read_text(encoding="utf-8-sig").splitlines()
    assert lines[0].split("\t") == ["Error category", "Reference", "Issue and explanation", "AI proposal"]
    assert lines[1].split("\t")[3] == "தேவன்"

    # Default columns when the caller sends none.
    result = write_report_rows(tmp_path / "default.csv", "csv", rows)
    assert result["rows"] == 1
    assert (tmp_path / "default.csv").read_text(encoding="utf-8-sig").splitlines()[0].startswith("category,book,chapter,verse,")


def test_summarize_rows_counts_fixed_by_and_open_severity():
    summary = summarize_rows([
        {"category": CATEGORY_TN, "resolution": "resolved", "fixedBy": "machine", "severity": "medium"},
        {"category": CATEGORY_TN, "resolution": "resolved", "fixedBy": "human", "severity": "medium"},
        {"category": CATEGORY_GREEK_ROOM, "resolution": "unresolved", "fixedBy": "", "severity": "high"},
    ])
    assert summary["total"] == 3 and summary["resolved"] == 2
    assert summary["byFixedBy"] == {"human": 1, "machine": 1, "unresolved": 1}
    assert summary["openBySeverity"] == {"high": 1}
    assert summary["byCategory"][CATEGORY_TN] == {"total": 2, "resolved": 2, "unresolved": 0}
