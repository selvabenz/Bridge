"""
Tests for BridgeEngine — the unified dispatcher composing GreekRoomEngine
with real tc_ai_bridge business logic.

Uses a minimal but spec-accurate fixture translationCore project (built
directly from reading TranslationCoreProject's actual parsing code, not
guessed) rather than mocking tc_ai_bridge — the whole point of Phase 1 is
proving the real modules work behind the new protocol.
"""
import json
import shutil
import threading
import time
from pathlib import Path

import pytest

from bridge_service import BridgeEngine
from greek_room_engine.protocol import EngineRequest
from tests.support.waits import job_timeout
# Moved to tests/support/projects.py (#74 phase 4); re-exported so this file's
# own call sites are untouched.
from tests.support.projects import (  # noqa: F401
    _write_minimal_book, call, fixture_project, two_book_collection, wait_for_job,
)












def test_ping_and_info_work_without_a_project():
    engine = BridgeEngine()
    assert call(engine, "ping")["result"] == {"pong": True}
    info = call(engine, "engine.info")["result"]
    assert info["projectOpen"] is False
    assert "greekRoom" in info


def test_lexicon_get_entry_works_without_an_open_project():
    engine = BridgeEngine()
    result = call(engine, "lexicon.getEntry", {"strong": "H776", "morph": "He,Ncbsa"})["result"]

    assert result["languageId"] == "hbo"
    assert len(result["segments"]) == 1
    assert result["segments"][0]["lemma"] == "אֶרֶץ"
    assert result["segments"][0]["morphLabel"] == "Noun, Common, Both genders, Singular, Absolute"


def test_lexicon_get_entry_splits_compound_prefix_and_lexeme():
    engine = BridgeEngine()
    result = call(engine, "lexicon.getEntry", {"strong": "d:H0776", "morph": "He,Td:Ncbsa"})["result"]

    assert [s["strong"] for s in result["segments"]] == ["d", "H0776"]
    assert result["segments"][0]["meaning"] == "Definite article (the)"
    assert result["segments"][1]["lemma"] == "אֶרֶץ"


def test_open_real_fixture_project(fixture_project):
    engine = BridgeEngine()
    result = call(engine, "project.open", {"path": str(fixture_project)})
    assert result["success"] is True
    assert result["result"]["bookId"] == "rut"
    assert result["result"]["targetLanguage"] == "Tamil"
    assert result["result"]["chapters"] == ["1"]


def test_project_report_exposes_existing_report_service(fixture_project):
    """tc_ai_bridge.reporting.ReportService already existed but was never
    wired into the protocol (see BUILD_LOG.md) — project.report exposes it
    as-is rather than reimplementing report aggregation."""
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})
    result = call(engine, "project.report")
    assert result["success"] is True
    report = result["result"]
    assert report["bookId"] == "rut"
    assert "publicationGate" in report
    assert "knowledgeBaseProvenance" in report
    assert report["qaSeverityCounts"] == {}


def test_project_report_requires_open_project():
    engine = BridgeEngine()
    result = call(engine, "project.report")
    assert result["success"] is False


def test_project_report_coverage_reflects_progress_rollup(fixture_project, monkeypatch):
    """coverage.verses is derived from the existing progress rollup
    (written by _on_check_job_complete after checks.start) — a verse
    that's never had checks run is NOT_CHECKED, one that comes back clean
    is PASS."""
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})
    monkeypatch.setattr(engine, "_usfm_findings_for_book", lambda project=None, cancel_event=None: [])

    before = call(engine, "project.report")["result"]
    assert before["coverage"]["verses"]["counts"] == {
        "PASS": 0, "ISSUE": 0, "REVIEW_REQUIRED": 0, "NOT_CHECKED": 1,
    }

    started = call(engine, "checks.start", {
        "scope": "chapter", "chapters": ["1"], "checks": ["greekroom"],
    })["result"]
    wait_for_job(engine, started["jobId"])

    after = call(engine, "project.report")["result"]
    assert after["coverage"]["verses"]["counts"]["PASS"] == 1
    assert after["coverage"]["verses"]["counts"]["NOT_CHECKED"] == 0
    assert after["coverage"]["verses"]["checkedPercent"] == 100.0
    assert after["coverage"]["verses"]["chapters"]["1"]["1"] == "PASS"


def test_project_collection_report_aggregates_every_sibling_book(two_book_collection, monkeypatch):
    """project.collectionReport builds each sibling's own build_book_report()
    (via ReportService.build_collection_report) rather than only reporting
    on whichever book happens to be open."""
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(two_book_collection)})

    result = call(engine, "project.collectionReport")
    assert result["success"] is True
    report = result["result"]
    assert report["bookCount"] == 2
    assert {b["bookId"] for b in report["books"]} == {"rut", "gen"}
    assert "verseCoverage" in report
    assert "publicationGate" in report


def _write_alignment_book_with_completed_groups(root: Path, book_id: str, groups: dict):
    """groups: {chapter: {verse: (source_word, target_word)}}. Every verse
    is marked complete under tools/wordAlignment/completed — the same
    signal alignment_statistics.build_corpus_stats reads (see
    test_alignment_statistics.py's equivalent helper)."""
    align_dir = root / ".apps" / "translationCore" / "alignmentData" / book_id
    align_dir.mkdir(parents=True)
    (root / book_id).mkdir(parents=True)
    (root / "manifest.json").write_text(json.dumps({
        "project": {"id": book_id, "name": book_id.upper()},
        "target_language": {"id": "tam", "name": "Tamil"},
        "tc_version": "8", "tc_edit_version": "3.7.0",
    }), encoding="utf-8")
    for chapter, verses in groups.items():
        align_chapter = {}
        text_chapter = {}
        for verse, (source_word, target_word) in verses.items():
            align_chapter[verse] = {
                "alignments": [{
                    "topWords": [{"word": source_word, "strong": "H430", "occurrence": 1, "occurrences": 1}],
                    "bottomWords": [{"word": target_word, "occurrence": 1, "occurrences": 1}],
                }],
                "wordBank": [],
            }
            text_chapter[verse] = target_word
            completed_dir = (
                root / ".apps" / "translationCore" / "tools" / "wordAlignment" / "completed" / str(chapter)
            )
            completed_dir.mkdir(parents=True, exist_ok=True)
            (completed_dir / f"{verse}.json").write_text(
                json.dumps({"username": "tester", "modifiedTimestamp": "2026-01-01T00:00:00.000Z"}),
                encoding="utf-8",
            )
        (align_dir / f"{chapter}.json").write_text(json.dumps(align_chapter, ensure_ascii=False), encoding="utf-8")
        (root / book_id / f"{chapter}.json").write_text(json.dumps(text_chapter, ensure_ascii=False), encoding="utf-8")
    (root / f"{book_id}.usfm").write_text(f"\\id {book_id.upper()}\n", encoding="utf-8")


def test_consistency_check_flags_fragmented_renderings(tmp_path):
    """A source word aligned to 3+ different target renderings across
    completed alignments, with no single dominant rendering, is flagged —
    Layer-2 corpus-consistency, built on alignment_statistics.py's
    existing co-occurrence table."""
    root = tmp_path / "rut"
    _write_alignment_book_with_completed_groups(root, "rut", {
        "1": {
            "1": ("אֱלֹהִ֑ים", "தேவன்"),
            "2": ("אֱלֹהִ֑ים", "கடவுள்"),
            "3": ("אֱלֹהִ֑ים", "இறைவன்"),
        },
    })
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(root)})
    result = call(engine, "verse.runChecks", {"chapter": "1", "verse": "1", "checks": ["consistency"]})
    findings = result["findings"]

    assert len(findings) == 1
    finding = findings[0]
    assert finding["check_type"] == "alignment.inconsistent_rendering"
    assert finding["original_text"] == "אֱלֹהִ֑ים"
    assert {e["label"] for e in finding["evidence"]} == {'"தேவன்"', '"கடவுள்"', '"இறைவன்"'}

    # Never anchored to a verse other than the book's own first one.
    not_first = call(engine, "verse.runChecks", {"chapter": "1", "verse": "2", "checks": ["consistency"]})
    assert not_first["findings"] == []


def test_consistency_check_is_opt_in_not_bundled_into_local(tmp_path):
    """Deliberately not included under checks=['local'] — see the comment
    at its call site in _run_verse_checks_for_project."""
    root = tmp_path / "rut"
    _write_alignment_book_with_completed_groups(root, "rut", {
        "1": {"1": ("אֱלֹהִ֑ים", "தேவன்"), "2": ("אֱלֹהִ֑ים", "கடவுள்"), "3": ("אֱלֹהִ֑ים", "இறைவன்")},
    })
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(root)})
    result = call(engine, "verse.runChecks", {"chapter": "1", "verse": "1", "checks": ["local"]})
    assert not any(f["check_type"] == "alignment.inconsistent_rendering" for f in result["findings"])


def test_verse_get_returns_real_alignment_data(fixture_project):
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})
    result = call(engine, "verse.get", {"chapter": "1", "verse": "1"})["result"]
    assert "தேவன்" in result["text"]
    assert result["alignment"]["alignments"][0]["bottomWords"][0]["word"] == "தேவன்"


def test_chapter_verse_data_returns_all_verses_in_one_call(fixture_project):
    """This is the fix for the 'app not responding for minutes' bug — a
    real project chapter with N verses used to mean N sequential Tauri
    round trips before the editor ever appeared. This proves the bulk
    endpoint returns the same data in one call."""
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})
    result = call(engine, "chapter.verseData", {"chapter": "1"})["result"]
    assert result["chapter"] == "1"
    assert "1" in result["verses"]
    assert "தேவன்" in result["verses"]["1"]["text"]
    assert result["verses"]["1"]["alignment"]["alignments"][0]["bottomWords"][0]["word"] == "தேவன்"


def _write_alignment_work_fixture(fixture_project):
    path = fixture_project / ".apps" / "translationCore" / "alignmentData" / "rut" / "1.json"
    path.write_text(json.dumps({
        "1": {
            "alignments": [
                {
                    "topWords": [{
                        "word": "אֱלֹהִ֑ים", "strong": "H430", "lemma": "אֱלֹהִים",
                        "morph": "He,Ncmsa", "occurrence": 1, "occurrences": 1,
                    }],
                    "bottomWords": [{
                        "word": "தேவன்", "occurrence": 1, "occurrences": 1,
                        "type": "bottomWord",
                    }],
                },
                {
                    "topWords": [{
                        "word": "בָּרָא", "strong": "H1254", "lemma": "בָּרָא",
                        "morph": "He,Vqp3ms", "occurrence": 1, "occurrences": 1,
                    }],
                    "bottomWords": [],
                },
            ],
            "wordBank": [
                {"word": "ஆதியிலே", "occurrence": 1, "occurrences": 1, "type": "bottomWord"},
                {"word": "வானத்தையும்", "occurrence": 1, "occurrences": 1, "type": "bottomWord"},
                {"word": "பூமியையும்", "occurrence": 1, "occurrences": 1, "type": "bottomWord"},
                {"word": "படைத்தார்", "occurrence": 1, "occurrences": 1, "type": "bottomWord"},
            ],
        },
    }, ensure_ascii=False), encoding="utf-8")


def test_alignment_protocol_realigns_many_to_many_and_undo_survives_restart(fixture_project):
    _write_alignment_work_fixture(fixture_project)
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})
    original = call(engine, "alignment.get", {"chapter": "1", "verse": "1"})["result"]

    assert original["sourceAvailable"] is True
    assert original["sourceDirection"] == "rtl"
    assert original["status"] == "partial"
    top_ids = [token["id"] for token in original["topTokens"]]
    bottom_ids = [token["id"] for token in original["bottomTokens"][:2]]
    changed = call(engine, "alignment.realign", {
        "chapter": "1", "verse": "1", "topIds": top_ids, "bottomIds": bottom_ids,
        "expectedOriginal": original["alignment"],
    })

    assert changed["success"] is True
    changed_context = changed["result"]
    assert any(
        len(group["topIds"]) == 2 and len(group["bottomIds"]) == 2
        for group in changed_context["groups"]
    )
    assert changed_context["history"][0]["operation"] == "realign"

    restarted = BridgeEngine()
    call(restarted, "project.open", {"path": str(fixture_project)})
    persisted = call(restarted, "alignment.get", {"chapter": "1", "verse": "1"})["result"]
    undone = call(restarted, "alignment.undo", {
        "chapter": "1", "verse": "1", "expectedOriginal": persisted["alignment"],
    })
    assert undone["success"] is True
    assert undone["result"]["alignment"] == original["alignment"]


def test_alignment_save_rejects_stale_editor_and_completion_requires_full_coverage(fixture_project):
    _write_alignment_work_fixture(fixture_project)
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})
    original = call(engine, "alignment.get", {"chapter": "1", "verse": "1"})["result"]
    all_top = [token["id"] for token in original["topTokens"]]
    all_bottom = [token["id"] for token in original["bottomTokens"]]
    changed = call(engine, "alignment.realign", {
        "chapter": "1", "verse": "1", "topIds": all_top, "bottomIds": all_bottom,
        "expectedOriginal": original["alignment"],
    })["result"]

    stale = call(engine, "alignment.save", {
        "chapter": "1", "verse": "1", "alignment": original["alignment"],
        "expectedOriginal": original["alignment"],
    })
    assert stale["success"] is False
    assert stale["error"]["code"] == "project_error"

    completed = call(engine, "alignment.complete", {"chapter": "1", "verse": "1"})
    assert completed["success"] is True
    assert completed["result"]["status"] == "complete"
    assert completed["result"]["completionState"] == "completed"
    assert changed["canComplete"] is True
    restarted = BridgeEngine()
    call(restarted, "project.open", {"path": str(fixture_project)})
    persisted = call(restarted, "alignment.get", {"chapter": "1", "verse": "1"})["result"]
    assert persisted["completionState"] == "completed"
    status = call(restarted, "alignment.status", {"chapter": "1"})["result"]
    assert status["counts"]["complete"] == 1


def test_alignment_unalign_and_restore_selected_history_entry(fixture_project):
    _write_alignment_work_fixture(fixture_project)
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})
    original = call(engine, "alignment.get", {"chapter": "1", "verse": "1"})["result"]
    aligned_target = next(
        token["id"] for token in original["bottomTokens"] if token["word"] == "தேவன்"
    )

    changed = call(engine, "alignment.unalign", {
        "chapter": "1", "verse": "1", "bottomIds": [aligned_target],
        "expectedOriginal": original["alignment"],
    })["result"]

    assert any(token["word"] == "தேவன்" for token in changed["alignment"]["wordBank"])
    history_id = changed["history"][0]["id"]
    restored = call(engine, "alignment.restore", {
        "chapter": "1", "verse": "1", "historyId": history_id,
        "expectedOriginal": changed["alignment"],
    })
    assert restored["success"] is True
    assert restored["result"]["alignment"] == original["alignment"]


def test_alignment_history_failure_rolls_back_chapter_write(fixture_project, monkeypatch):
    _write_alignment_work_fixture(fixture_project)
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})
    original = call(engine, "alignment.get", {"chapter": "1", "verse": "1"})["result"]

    def fail_history(*_args, **_kwargs):
        raise OSError("simulated history write failure")

    monkeypatch.setattr(engine.project, "_record_alignment_history", fail_history)
    failed = call(engine, "alignment.realign", {
        "chapter": "1", "verse": "1",
        "topIds": [token["id"] for token in original["topTokens"]],
        "bottomIds": [token["id"] for token in original["bottomTokens"]],
        "expectedOriginal": original["alignment"],
    })

    assert failed["success"] is False
    assert engine.project.load_verse_alignment("1", "1").to_dict() == original["alignment"]
    assert engine.project.pending_transactions() == []


def test_every_redirected_store_populates_its_lifted_columns(fixture_project):
    """#76: a row whose lifted columns are NULL is invisible to every query.

    This bit twice. `alignment_history` rows were written without chapter/verse,
    so `alignment_history()` returned nothing and restore had no candidates --
    caught only because two tests read it back. `alignment_diagnostics` is
    **write-only**: nothing reads it, so the same mistake there would have sat
    undetected until someone came to build a reader months later and found an
    unqueryable table. Hence a direct assertion on the columns.
    """
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})
    project = engine.project

    project.record_alignment_diagnostic("1", "1", {"reason": "token mismatch"})
    project.record_human_decision("1", "1", "check-1", "accepted")
    project.record_qa_decision("1", "1", "finding-1", "ignored")
    project.record_review_state("1", "1", "reviewed")
    project.record_ai_review_result("1", "1", {"summary": "fine"})

    expected = {
        "alignment_diagnostics": ("chapter", "verse"),
        "human_decisions": ("chapter", "verse", "key", "kind"),
        "ai_review_results": ("chapter", "verse"),
    }
    with project.workbench._connect() as conn:
        for table, columns in expected.items():
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            assert rows, f"{table} got no rows"
            for row in rows:
                for column in columns:
                    assert row[column] is not None, f"{table}.{column} is NULL"
                assert row["project_id"] and row["actor_id"] and row["device_id"]


def test_alignment_reports_missing_original_language_source(fixture_project):
    path = fixture_project / ".apps" / "translationCore" / "alignmentData" / "rut" / "1.json"
    path.write_text(json.dumps({
        "1": {
            "alignments": [],
            "wordBank": [
                {"word": word, "occurrence": 1, "occurrences": 1, "type": "bottomWord"}
                for word in ["ஆதியிலே", "தேவன்", "வானத்தையும்", "பூமியையும்", "படைத்தார்"]
            ],
        },
    }, ensure_ascii=False), encoding="utf-8")
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})

    context = call(engine, "alignment.get", {"chapter": "1", "verse": "1"})["result"]
    assert context["sourceAvailable"] is False
    assert "original-language source tokens" in context["sourceMessage"]
    completed = call(engine, "alignment.complete", {"chapter": "1", "verse": "1"})
    assert completed["success"] is False
    assert completed["error"]["code"] == "alignment_error"


def test_alignment_context_respects_rtl_target_metadata(fixture_project):
    _write_alignment_work_fixture(fixture_project)
    manifest_path = fixture_project / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["target_language"]["direction"] = "rtl"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})

    context = call(engine, "alignment.get", {"chapter": "1", "verse": "1"})["result"]

    assert context["sourceDirection"] == "rtl"
    assert context["targetDirection"] == "rtl"


def test_alignment_flags_non_adjacent_target_group_before_completion(fixture_project):
    _write_alignment_work_fixture(fixture_project)
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})
    original = call(engine, "alignment.get", {"chapter": "1", "verse": "1"})["result"]

    changed = call(engine, "alignment.realign", {
        "chapter": "1", "verse": "1",
        "topIds": [original["topTokens"][0]["id"]],
        "bottomIds": [original["bottomTokens"][0]["id"], original["bottomTokens"][2]["id"]],
        "expectedOriginal": original["alignment"],
    })["result"]

    assert changed["canComplete"] is False
    assert any("non-adjacent target words" in issue for issue in changed["issues"])


def test_clean_verse_has_no_findings(fixture_project):
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})
    result = call(engine, "verse.runChecks", {"chapter": "1", "verse": "1", "checks": ["local", "greekroom"]})
    assert result["findings"] == []


def test_chapter_check_job_reports_real_progress_and_results(fixture_project, monkeypatch):
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})
    monkeypatch.setattr(
        engine, "_usfm_findings_for_book",
        lambda project=None, cancel_event=None: [],
    )

    started = call(engine, "checks.start", {
        "scope": "chapter", "chapters": ["1"], "checks": ["local", "greekroom"],
    })["result"]
    finished = wait_for_job(engine, started["jobId"])

    assert finished["state"] == "succeeded"
    assert finished["percent"] == 100
    assert finished["chapterVerses"] == {"1": ["1"]}
    assert finished["results"]["1:1"]["status"] == "succeeded"
    assert isinstance(finished["results"]["1:1"]["findings"], list)


def test_whole_book_job_checks_every_chapter_once(fixture_project):
    (fixture_project / "rut" / "2.json").write_text(json.dumps({
        "1": "இரண்டாம் அதிகாரம்.",
    }, ensure_ascii=False), encoding="utf-8")
    alignment_dir = fixture_project / ".apps" / "translationCore" / "alignmentData" / "rut"
    (alignment_dir / "2.json").write_text(json.dumps({
        "1": {"alignments": [], "wordBank": [{
            "word": "இரண்டாம்", "occurrence": 1, "occurrences": 1,
        }]},
    }, ensure_ascii=False), encoding="utf-8")
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})

    started = call(engine, "checks.start", {
        "scope": "book", "checks": ["greekroom"],
    })["result"]
    finished = wait_for_job(engine, started["jobId"])

    assert finished["state"] == "succeeded"
    assert finished["chapterVerses"] == {"1": ["1"], "2": ["1"]}
    assert set(finished["results"]) == {"1:1", "2:1"}
    assert all(result["status"] == "succeeded" for result in finished["results"].values())


def test_check_job_can_cancel_and_retry(fixture_project, monkeypatch):
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})
    entered = threading.Event()
    release = threading.Event()

    def slow_check(project, chapter, verse, checks, reads=None):
        entered.set()
        release.wait(timeout=2)
        return []

    monkeypatch.setattr(engine, "_run_verse_checks_for_project", slow_check)
    started = call(engine, "checks.start", {
        "scope": "chapter", "chapters": ["1"], "checks": ["greekroom"],
    })["result"]
    assert entered.wait(timeout=1)

    cancelling = call(engine, "checks.cancel", {"jobId": started["jobId"]})["result"]
    assert cancelling["state"] == "cancelling"
    release.set()
    cancelled = wait_for_job(engine, started["jobId"])
    assert cancelled["state"] == "cancelled"

    retried = call(engine, "checks.retry", {"jobId": started["jobId"]})["result"]
    finished = wait_for_job(engine, retried["jobId"])
    assert finished["state"] == "succeeded"
    assert finished["jobId"] != started["jobId"]


def test_check_job_cancels_during_usfm_preflight(fixture_project, monkeypatch):
    from greek_room_engine.adapters.usfm_adapter import UsfmCheckerCancelled

    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})
    entered = threading.Event()

    def slow_preflight(project=None, cancel_event=None):
        entered.set()
        assert cancel_event is not None
        cancel_event.wait(timeout=2)
        raise UsfmCheckerCancelled("cancelled by test")

    monkeypatch.setattr(engine, "_usfm_findings_for_book", slow_preflight)
    started = call(engine, "checks.start", {
        "scope": "chapter", "chapters": ["1"], "checks": ["local"],
    })["result"]
    assert entered.wait(timeout=1)
    call(engine, "checks.cancel", {"jobId": started["jobId"]})

    cancelled = wait_for_job(engine, started["jobId"])
    assert cancelled["state"] == "cancelled"
    assert cancelled["finishedAt"] is not None


def test_live_greek_room_and_status_stay_responsive_during_check_preparation(
    fixture_project, monkeypatch,
):
    """A first-time raw-book preflight holds the tN/tW checker lock.

    ReviewPanel immediately asks for a Greek-Room-only live check.  That request
    must not queue behind resource/USFM/names preparation on the synchronous
    stdio dispatcher, otherwise checks.status and check.listForVerse behind it
    both hit their 30-second desktop timeout.
    """
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})
    monkeypatch.setattr(engine.greek_room, "check_verse", lambda **kwargs: [])

    lock_entered = threading.Event()
    release_lock = threading.Event()

    def hold_checker_lock():
        with engine._checker_lock:
            lock_entered.set()
            release_lock.wait(timeout=2)

    holder = threading.Thread(target=hold_checker_lock, daemon=True)
    holder.start()
    assert lock_entered.wait(timeout=1)

    started = call(engine, "checks.start", {
        "scope": "chapter", "chapters": ["1"], "checks": ["greekroom"],
    })["result"]
    started_at = time.monotonic()
    live = call(engine, "verse.runChecks", {
        "chapter": "1", "verse": "1", "checks": ["greekroom"],
    })
    live_elapsed = time.monotonic() - started_at

    assert live["success"] is True
    # The claim is that the live check did not queue behind the checker lock,
    # which `hold_checker_lock` holds for 2s -- so anything that queued lands at
    # ~2s, not at a few hundred milliseconds. The old 0.25 budget was a proxy
    # for that tuned to one machine, and on CI (windows-latest, serial) it
    # measured 0.250-0.375 across four runs while the property itself held every
    # time. Windows' ~15.6ms timer tick is a real fraction of a 250ms budget, so
    # that threshold sat inside the noise floor. 1.0 keeps the assertion
    # unambiguous against a 2s queue without failing on a loaded runner. See #90.
    assert live_elapsed < 1.0
    status = call(engine, "checks.status", {"jobId": started["jobId"]})["result"]
    assert status["state"] in {"queued", "running"}
    listed = call(engine, "check.listForVerse", {"chapter": "1", "verse": "1"})["result"]
    assert listed["state"] == "preparing"

    release_lock.set()
    holder.join(timeout=1)
    assert wait_for_job(engine, started["jobId"])["state"] == "succeeded"


def test_check_job_rejects_a_second_active_job(fixture_project, monkeypatch):
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})
    entered = threading.Event()
    release = threading.Event()

    def slow_check(project, chapter, verse, checks, reads=None):
        entered.set()
        release.wait(timeout=2)
        return []

    monkeypatch.setattr(engine, "_run_verse_checks_for_project", slow_check)
    first = call(engine, "checks.start", {
        "scope": "chapter", "chapters": ["1"], "checks": ["greekroom"],
    })["result"]
    assert entered.wait(timeout=1)
    second = call(engine, "checks.start", {
        "scope": "chapter", "chapters": ["1"], "checks": ["greekroom"],
    })
    assert second["success"] is False
    assert second["error"]["code"] == "job_conflict"
    call(engine, "checks.cancel", {"jobId": first["jobId"]})
    release.set()
    wait_for_job(engine, first["jobId"])


def test_mixed_script_verse_is_flagged_by_greek_room(monkeypatch):
    # Force the mock adapter path — see test_wildebeest_real.py for
    # coverage of the real engine, skipped when it isn't installed.
    from greek_room_engine.adapters import wildebeest_adapter
    monkeypatch.setattr(wildebeest_adapter, "_WILDEBEEST_AVAILABLE", False)

    engine = BridgeEngine()
    findings = engine.greek_room.check_verse(
        project_id="p", lang_code="tam", ref="RUT 1:1", text="தேவன்aஆதி", checks=["wildebeest"],
    )
    assert any(f.check_type == "wildebeest.script.mixed" for f in findings)


def test_decide_verse_really_persists_the_qa_decision(fixture_project):
    """Was `..._writes_real_qa_decision_file` and read the path back off disk.

    #76 moved QA decisions into the workbench database, so there is no file to
    open. The intent was never the file though -- it was that a decision the
    reviewer makes is actually durable and readable afterwards -- so this now
    asserts that through the public reader.
    """
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})
    result = call(engine, "verse.decide", {
        "chapter": "1", "verse": "1", "findingId": "test-finding",
        "status": "accepted", "comment": "looks fine",
    })["result"]

    assert result["recordedAt"], "the response still identifies the stored record"
    stored = engine.project.qa_decisions_for_verse("1", "1")["test-finding"]
    assert stored["decision"] == "accepted"
    assert stored["issueKey"] == "test-finding"
    assert stored["note"] == "looks fine"


def test_a_qa_decision_survives_reopening_the_project(fixture_project):
    """The durability half, which the file-based test got for free."""
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})
    call(engine, "verse.decide", {
        "chapter": "1", "verse": "1", "findingId": "test-finding",
        "status": "accepted", "comment": "looks fine",
    })

    reopened = BridgeEngine()
    call(reopened, "project.open", {"path": str(fixture_project)})

    stored = reopened.project.qa_decisions_for_verse("1", "1")["test-finding"]
    assert stored["decision"] == "accepted"
    assert stored["note"] == "looks fine"


def test_usfm_checks_run_once_per_book_not_once_per_verse(fixture_project):
    """The real checker spawns a subprocess loading a full tag database —
    calling it once per verse.runChecks call during a whole-book pass would
    be far too slow. Confirms the book-level cache actually caches, using a
    stub instead of the real vendored checker so this stays fast and
    independent of whether it's installed correctly in this environment."""
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})

    call_count = 0

    def fake_check_book_usfm(*, project_id, book_id, usfm_text, cancel_event=None):
        nonlocal call_count
        call_count += 1
        from greek_room_engine.models.finding import QaFinding, FindingCategory, Severity
        return [QaFinding(
            project_id=project_id, book=book_id, chapter=1, verse=1,
            engine="usfm", check_type="usfm.fake_issue",
            category=FindingCategory.STRUCTURE, severity=Severity.HIGH,
            explanation="fake finding for the cache test",
        )]

    engine.greek_room.check_book_usfm = fake_check_book_usfm

    result1 = call(engine, "verse.runChecks", {"chapter": "1", "verse": "1", "checks": ["local"]})
    result2 = call(engine, "verse.runChecks", {"chapter": "1", "verse": "1", "checks": ["local"]})

    assert call_count == 1, "check_book_usfm should be cached, not re-run per verse.runChecks call"
    assert any(f["check_type"] == "usfm.fake_issue" for f in result1["findings"])
    assert any(f["check_type"] == "usfm.fake_issue" for f in result2["findings"])


def test_usfm_checker_failure_is_protocol_error_and_cached(fixture_project):
    """A checker crash must never be indistinguishable from a clean book."""
    from greek_room_engine.adapters.usfm_adapter import UsfmCheckerError

    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})
    call_count = 0

    def failed_check(**kwargs):
        nonlocal call_count
        call_count += 1
        raise UsfmCheckerError("helper failed")

    engine.greek_room.check_book_usfm = failed_check
    first = call(engine, "verse.runChecks", {"chapter": "1", "verse": "1", "checks": ["local"]})
    second = call(engine, "verse.runChecks", {"chapter": "1", "verse": "1", "checks": ["local"]})

    assert first["success"] is False
    assert first["error"]["code"] == "checker_error"
    assert second["error"]["code"] == "checker_error"
    assert call_count == 1


def test_usfm_findings_are_cached_on_disk_across_project_reopen(fixture_project):
    """Reopening a project (e.g. after an app restart) must not re-run the
    USFM subprocess when the underlying source file hasn't changed — the
    in-memory-only cache this replaced was cleared on every open_project()
    call, which is exactly what made Genesis slow on every reopen."""
    from greek_room_engine.models.finding import QaFinding, FindingCategory, Severity

    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})

    call_count = 0

    def fake_check_book_usfm(*, project_id, book_id, usfm_text, cancel_event=None):
        nonlocal call_count
        call_count += 1
        return [QaFinding(
            project_id=project_id, book=book_id, chapter=1, verse=1,
            engine="usfm", check_type="usfm.fake_issue",
            category=FindingCategory.STRUCTURE, severity=Severity.HIGH,
            explanation="fake finding for the disk-cache test",
        )]

    engine.greek_room.check_book_usfm = fake_check_book_usfm

    first = call(engine, "verse.runChecks", {"chapter": "1", "verse": "1", "checks": ["local"]})
    assert call_count == 1

    call(engine, "project.open", {"path": str(fixture_project)})
    second = call(engine, "verse.runChecks", {"chapter": "1", "verse": "1", "checks": ["local"]})

    assert call_count == 1, "USFM check should be reused from the on-disk cache after reopen"
    assert any(f["check_type"] == "usfm.fake_issue" for f in first["findings"])
    assert any(f["check_type"] == "usfm.fake_issue" for f in second["findings"])


def test_usfm_disk_cache_invalidated_when_source_changes(fixture_project):
    """A real content change (e.g. a re-import over the same directory) must
    still force a fresh run, not silently reuse stale findings forever."""
    from greek_room_engine.models.finding import QaFinding, FindingCategory, Severity

    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})

    call_count = 0

    def fake_check_book_usfm(*, project_id, book_id, usfm_text, cancel_event=None):
        nonlocal call_count
        call_count += 1
        return [QaFinding(
            project_id=project_id, book=book_id, chapter=1, verse=1,
            engine="usfm", check_type="usfm.fake_issue",
            category=FindingCategory.STRUCTURE, severity=Severity.HIGH,
            explanation="fake finding for the disk-cache test",
        )]

    engine.greek_room.check_book_usfm = fake_check_book_usfm

    call(engine, "verse.runChecks", {"chapter": "1", "verse": "1", "checks": ["local"]})
    assert call_count == 1

    usfm_path = fixture_project / "rut.usfm"
    usfm_path.write_text(usfm_path.read_text(encoding="utf-8") + "\\v 2 Extra verse.\n", encoding="utf-8")

    call(engine, "project.open", {"path": str(fixture_project)})
    call(engine, "verse.runChecks", {"chapter": "1", "verse": "1", "checks": ["local"]})

    assert call_count == 2, "a changed source file must invalidate the disk cache"


def test_names_findings_are_cached_on_disk_across_project_reopen(fixture_project):
    """Same disk-cache behavior as USFM, for the names/spelling scan."""
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})

    call_count = 0

    def fake_check_book_names(*, project_id, book_id, lang_code, token_occurrences):
        nonlocal call_count
        call_count += 1
        return []

    engine.greek_room.check_book_names = fake_check_book_names

    call(engine, "verse.runChecks", {"chapter": "1", "verse": "1", "checks": ["local"]})
    assert call_count == 1

    call(engine, "project.open", {"path": str(fixture_project)})
    call(engine, "verse.runChecks", {"chapter": "1", "verse": "1", "checks": ["local"]})

    assert call_count == 1, "names check should be reused from the on-disk cache after reopen"


def test_names_disk_cache_invalidated_by_verse_edit(fixture_project):
    """Unlike USFM (keyed to the untouched original source file), the names
    cache is keyed to current verse text, so an edit must invalidate it —
    this is a real behavior improvement over the old always-recompute
    approach, not just a cache: today's blind recompute-on-every-reopen
    never distinguished 'content changed' from 'nothing changed'."""
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})

    call_count = 0

    def fake_check_book_names(*, project_id, book_id, lang_code, token_occurrences):
        nonlocal call_count
        call_count += 1
        return []

    engine.greek_room.check_book_names = fake_check_book_names

    call(engine, "verse.runChecks", {"chapter": "1", "verse": "1", "checks": ["local"]})
    assert call_count == 1

    call(engine, "verse.edit", {"chapter": "1", "verse": "1", "newText": "changed text"})
    call(engine, "project.open", {"path": str(fixture_project)})
    call(engine, "verse.runChecks", {"chapter": "1", "verse": "1", "checks": ["local"]})

    assert call_count == 2, "an edited verse must invalidate the names disk cache"

    call(engine, "project.open", {"path": str(fixture_project)})
    call(engine, "verse.runChecks", {"chapter": "1", "verse": "1", "checks": ["local"]})

    assert call_count == 2, "a no-op reopen after the edit should stay cached"


def test_usfm_finding_for_missing_verse_surfaces_on_first_existing_verse(fixture_project):
    """The UI cannot request a missing verse, so chapter-level structural
    findings must be attached to an existing display slot."""
    from greek_room_engine.models.finding import QaFinding, FindingCategory, Severity

    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})
    engine.greek_room.check_book_usfm = lambda **kwargs: [QaFinding(
        project_id="p", book="rut", chapter=1, verse=2,
        engine="usfm", check_type="usfm.chapters_with_missing_verses",
        category=FindingCategory.STRUCTURE, severity=Severity.HIGH,
        explanation="Missing verse: 2",
    )]

    result = call(engine, "verse.runChecks", {
        "chapter": "1", "verse": "1", "checks": ["usfm"],
    })
    assert result["success"] is True
    assert any("missing_verses" in f["check_type"] for f in result["findings"])


def test_edit_verse_creates_transaction_backup(fixture_project):
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})
    result = call(engine, "verse.edit", {"chapter": "1", "verse": "1", "newText": "test"})
    assert result["result"]["committed"] is True
    tx_dir = fixture_project / ".apps" / "translationCoreAI" / "transactions"
    assert any(tx_dir.glob("*.json"))


def test_edit_verse_actually_writes_the_new_text_and_invalidates_alignment(fixture_project):
    """Guards against edit_verse regressing to its old stub, which committed
    a transaction but never wrote anything — verse.get would still show the
    pre-edit text. Also verifies the real tc_project.apply_scripture_edit()
    behavior this now calls: word alignment is marked invalid (its bottomWord
    tokens no longer match the new text) rather than silently kept stale."""
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})
    call(engine, "verse.edit", {"chapter": "1", "verse": "1", "newText": "புதிய வார்த்தைகள்"})

    verse = call(engine, "verse.get", {"chapter": "1", "verse": "1"})["result"]
    assert verse["text"] == "புதிய வார்த்தைகள்"
    assert verse["alignment"]["alignments"][0]["bottomWords"] == []
    assert [t["word"] for t in verse["alignment"]["wordBank"]] == ["புதிய", "வார்த்தைகள்"]

    findings = call(engine, "verse.runChecks", {"chapter": "1", "verse": "1", "checks": ["local"]})["findings"]
    assert any(f["check_type"] == "WA_INVALID" for f in findings)


def test_edit_verse_with_unchanged_text_fails_gracefully(fixture_project):
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})
    current = call(engine, "verse.get", {"chapter": "1", "verse": "1"})["result"]["text"]
    result = call(engine, "verse.edit", {"chapter": "1", "verse": "1", "newText": current})
    assert result["success"] is False
    assert result["error"]["code"] == "project_error"


def test_edit_verse_journals_the_configured_reviewer_not_ai_bridge_reviewer(
    fixture_project, tmp_path, monkeypatch,
):
    """V11-010: edit_verse() used to call apply_scripture_edit() without
    username=, so its 'AI Bridge Reviewer' signature default silently fired
    on every real edit. It must now record the actual configured reviewer."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from tc_ai_bridge.secret_store import AppSettings

    isolated = AppSettings(path=tmp_path / "settings.json")
    isolated.reviewer_name = "Alice"
    engine = BridgeEngine(settings=isolated)
    call(engine, "project.open", {"path": str(fixture_project)})

    result = call(engine, "verse.edit", {"chapter": "1", "verse": "1", "newText": "edited by Alice"})
    edit_record = json.loads(Path(result["result"]["verseEdit"]).read_text(encoding="utf-8"))

    assert edit_record["username"] == "Alice"
    assert edit_record["username"] != "AI Bridge Reviewer"


def test_a_later_edit_by_a_different_reviewer_does_not_rewrite_the_earlier_journal_entry(
    fixture_project, tmp_path, monkeypatch,
):
    """V11-010: renaming yourself in Settings must never retroactively alter
    who an already-written journal entry says made an earlier edit."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from tc_ai_bridge.secret_store import AppSettings

    isolated = AppSettings(path=tmp_path / "settings.json")
    isolated.reviewer_name = "Alice"
    engine = BridgeEngine(settings=isolated)
    call(engine, "project.open", {"path": str(fixture_project)})

    first = call(engine, "verse.edit", {"chapter": "1", "verse": "1", "newText": "Alice's edit"})
    first_edit_path = Path(first["result"]["verseEdit"])
    first_record_before = json.loads(first_edit_path.read_text(encoding="utf-8"))
    assert first_record_before["username"] == "Alice"

    isolated.reviewer_name = "Bob"
    second = call(engine, "verse.edit", {"chapter": "1", "verse": "1", "newText": "Bob's later edit"})
    second_edit_path = Path(second["result"]["verseEdit"])

    first_record_after = json.loads(first_edit_path.read_text(encoding="utf-8"))
    second_record = json.loads(second_edit_path.read_text(encoding="utf-8"))
    assert first_record_after["username"] == "Alice"  # unchanged by Bob's later edit
    assert second_record["username"] == "Bob"


def test_engine_info_reports_the_real_app_version_and_schema_version(monkeypatch):
    """V11-011: the in-app version display must read the same constants the
    rest of the process uses, not a separately maintained copy. Prove it by
    changing the real source and confirming engine.info() moves with it,
    rather than asserting a fixed literal that a hardcoded copy would also
    satisfy."""
    import bridge_service

    monkeypatch.setattr(bridge_service, "BRIDGE_VERSION", "9.9.9-test")
    monkeypatch.setattr(bridge_service, "DATABASE_SCHEMA_VERSION", 999)
    engine = BridgeEngine()

    result = call(engine, "engine.info")["result"]

    assert result["bridgeVersion"] == "9.9.9-test"
    assert result["companionSchemaVersion"] == 999


def test_open_missing_project_fails_gracefully():
    engine = BridgeEngine()
    result = call(engine, "project.open", {"path": "/no/such/path"})
    assert result["success"] is False
    assert result["error"]["code"] == "project_error"


def test_verse_op_before_project_open_fails_gracefully():
    engine = BridgeEngine()
    result = call(engine, "chapter.verses", {"chapter": "1"})
    assert result["success"] is False
    assert result["error"]["code"] == "project_error"


def test_settings_get_has_safe_defaults(tmp_path, monkeypatch):
    # AppSettings() with no explicit path reads a real, persistent location
    # on the machine (and get_api_key() also checks $OPENAI_API_KEY) — that
    # is correct production behavior, but tests must not depend on or leak
    # into the real machine's settings. Isolate explicitly.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from tc_ai_bridge.secret_store import AppSettings
    isolated = AppSettings(path=tmp_path / "settings.json")
    engine = BridgeEngine(settings=isolated)
    result = call(engine, "settings.get")["result"]
    assert result["hasApiKey"] is False
    assert "model" in result
    assert result["reviewerMode"] == "basic"


def test_settings_persists_valid_reviewer_mode_and_rejects_invalid_mode(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from tc_ai_bridge.secret_store import AppSettings

    settings_path = tmp_path / "settings.json"
    engine = BridgeEngine(settings=AppSettings(path=settings_path))
    changed = call(engine, "settings.set", {"reviewerMode": "advanced"})
    assert changed["success"] is True
    assert changed["result"]["reviewerMode"] == "advanced"

    restarted = BridgeEngine(settings=AppSettings(path=settings_path))
    assert call(restarted, "settings.get")["result"]["reviewerMode"] == "advanced"
    invalid = call(restarted, "settings.set", {"reviewerMode": "expert"})
    assert invalid["success"] is False


def test_settings_get_reflects_a_saved_api_key(tmp_path, monkeypatch):
    """Documents the real (correct) behavior: once a key is saved, hasApiKey
    flips to True. This is what you're seeing on a machine that already has
    a saved key or OPENAI_API_KEY set — not a bug."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from tc_ai_bridge.secret_store import AppSettings
    isolated = AppSettings(path=tmp_path / "settings.json")
    engine = BridgeEngine(settings=isolated)
    call(engine, "settings.set", {"apiKey": "sk-test-123"})
    result = call(engine, "settings.get")["result"]
    assert result["hasApiKey"] is True
    persisted = (tmp_path / "settings.json").read_text(encoding="utf-8")
    assert "sk-test-123" not in persisted
    assert "_session_api_key" not in persisted


def test_settings_load_removes_legacy_plaintext_session_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from tc_ai_bridge.secret_store import AppSettings

    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps({"model": "gpt-5.6", "_session_api_key": "sk-legacy-plaintext"}),
        encoding="utf-8",
    )

    settings = AppSettings(path=settings_path)

    assert settings.get_api_key() == "sk-legacy-plaintext"
    persisted = settings_path.read_text(encoding="utf-8")
    assert "sk-legacy-plaintext" not in persisted
    assert "_session_api_key" not in persisted


def test_qaissue_categorization_matches_real_local_checks_codes():
    """Locks in the code/title -> FindingCategory mapping against the
    ACTUAL codes local_checks.py produces (checked against source, not
    assumed) so the UI's tN/tW/Alignment color-coding stays correct."""
    from bridge_service import _categorize_qaissue
    from tc_ai_bridge.models import QAIssue
    from greek_room_engine.models.finding import FindingCategory

    cases = [
        (QAIssue("ALIGN_DUP_TOP", "critical", "x", "y"), FindingCategory.ALIGNMENT),
        (QAIssue("WA_INVALID", "high", "x", "y", "translationCore"), FindingCategory.ALIGNMENT),
        (QAIssue("USFM_BALANCE", "high", "x", "y", "local"), FindingCategory.STRUCTURE),
        (QAIssue("USFM_REDUNDANT_MARKER", "medium", "x", "y", "local"), FindingCategory.STRUCTURE),
        (QAIssue("SRC_REPEAT_WORD", "medium", "x", "y"), FindingCategory.REPETITION),
        (QAIssue("TGT_HIDDEN_CHAR", "editorial", "x", "y"), FindingCategory.UNICODE),
        (QAIssue("TC_PENDING", "high", "translationWords: unchecked item", "y", "translationCore"), FindingCategory.TRANSLATION_WORD),
        (QAIssue("TC_INVALIDATED", "high", "translationNotes: recheck required", "y", "translationCore"), FindingCategory.TRANSLATION_NOTE),
        (QAIssue("TC_COMMENTS", "info", "Reviewer comments present", "y", "translationCore"), FindingCategory.CONSISTENCY),
    ]
    for issue, expected in cases:
        assert _categorize_qaissue(issue) == expected, f"{issue.code} -> expected {expected}"


def test_qaissue_to_finding_carries_resource_versions_when_given():
    """A finding stamped with the tN/tW/original-language versions it was
    produced against can later be told apart from a stale one after those
    bundled resources update — see BridgeEngine._pinned_resource_versions."""
    from bridge_service import _qaissue_to_finding
    from tc_ai_bridge.models import QAIssue

    issue = QAIssue("TC_PENDING", "high", "translationWords: unchecked item", "y", "translationCore")
    finding = _qaissue_to_finding(
        issue, project_id="p", book="rut", chapter="1", verse="1",
        resource_versions={"translationWords": "v90_unfoldingWord"},
    )
    assert finding.resource_versions == {"translationWords": "v90_unfoldingWord"}

    finding_without = _qaissue_to_finding(issue, project_id="p", book="rut", chapter="1", verse="1")
    assert finding_without.resource_versions == {}


def test_pinned_resource_versions_reads_manifest_and_skips_untracked(fixture_project):
    """Existing (non-Bridge-materialized) projects never had these manifest
    keys stamped — that must read back as 'not tracked', not a crash or a
    fabricated version string."""
    from bridge_service import BridgeEngine

    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})
    assert engine._pinned_resource_versions(engine.project) == {}

    engine.project.manifest["tc_en_check_version_translationNotes"] = "v90_unfoldingWord"
    engine.project.manifest["tc_orig_lang_check_version_wordAlignment"] = "v3.0.0_unfoldingWord"
    assert engine._pinned_resource_versions(engine.project) == {
        "translationNotes": "v90_unfoldingWord",
        "originalLanguage": "v3.0.0_unfoldingWord",
    }


def test_finding_ids_are_stable_across_repeated_check_runs():
    """The bug this fixes: findings used to get a random uuid4 id every
    time verse.runChecks ran, so a saved decision could never be matched
    back to 'the same' finding later. This proves ids are now stable."""
    from bridge_service import _stable_finding_id
    id1 = _stable_finding_id(chapter="1", verse="3", engine="wildebeest", check_type="wildebeest.script.mixed", disambiguator="7:8:a")
    id2 = _stable_finding_id(chapter="1", verse="3", engine="wildebeest", check_type="wildebeest.script.mixed", disambiguator="7:8:a")
    assert id1 == id2
    id3 = _stable_finding_id(chapter="1", verse="4", engine="wildebeest", check_type="wildebeest.script.mixed", disambiguator="7:8:a")
    assert id1 != id3  # different verse -> different id


def test_greek_room_finding_id_is_stable_across_repeated_check_runs(fixture_project):
    # overwrite verse 1 text with a mixed-script token so Greek Room flags it
    (fixture_project / "rut" / "1.json").write_text(
        '{"1": "\\u0ba4\\u0bc7\\u0bb5\\u0ba9a"}', encoding="utf-8"
    )
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})
    findings1 = call(engine, "verse.runChecks", {"chapter": "1", "verse": "1", "checks": ["greekroom"]})["findings"]
    findings2 = call(engine, "verse.runChecks", {"chapter": "1", "verse": "1", "checks": ["greekroom"]})["findings"]
    assert len(findings1) >= 1 and len(findings2) >= 1
    assert findings1[0]["id"] == findings2[0]["id"]


def test_decision_persists_across_repeated_check_runs(fixture_project):
    """The actual feature: accept a finding, re-run checks (simulating
    reopening the project), and confirm the finding comes back already
    marked accepted instead of resetting to open."""
    (fixture_project / "rut" / "1.json").write_text(
        '{"1": "\\u0ba4\\u0bc7\\u0bb5\\u0ba9a"}', encoding="utf-8"
    )
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})

    first_run = call(engine, "verse.runChecks", {"chapter": "1", "verse": "1", "checks": ["greekroom"]})["findings"]
    assert first_run[0]["status"] == "open"
    finding_id = first_run[0]["id"]

    call(engine, "verse.decide", {
        "chapter": "1", "verse": "1", "findingId": finding_id,
        "status": "accepted", "comment": "reviewed",
    })

    second_run = call(engine, "verse.runChecks", {"chapter": "1", "verse": "1", "checks": ["greekroom"]})["findings"]
    assert second_run[0]["id"] == finding_id
    assert second_run[0]["status"] == "accepted"
    assert second_run[0]["human_comment"] == "reviewed"

    restarted = BridgeEngine()
    call(restarted, "project.open", {"path": str(fixture_project)})
    after_restart = call(restarted, "verse.runChecks", {
        "chapter": "1", "verse": "1", "checks": ["greekroom"],
    })["findings"]
    assert after_restart[0]["id"] == finding_id
    assert after_restart[0]["status"] == "accepted"
    assert after_restart[0]["human_comment"] == "reviewed"


def test_decide_verse_updates_progress_rollup_incrementally(fixture_project):
    """verse.decide should update the book's .bridge/progress.json rollup
    in place — not by rescanning qaDecisions — and flipping a decision's
    status must not double-count the same finding."""
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})

    call(engine, "verse.decide", {
        "chapter": "1", "verse": "1", "findingId": "fake-finding",
        "status": "accepted", "comment": "",
    })

    rollup = engine.project.load_progress_rollup()
    totals = rollup["totals"]
    assert totals["findingCount"] == 1
    assert totals["approvedFindingCount"] == 1
    assert totals["reviewedVerseCount"] == 1
    assert rollup["chapters"]["1"]["verses"]["1"]["findings"]["fake-finding"] == "accepted"

    call(engine, "verse.decide", {
        "chapter": "1", "verse": "1", "findingId": "fake-finding",
        "status": "rejected", "comment": "",
    })

    rollup = engine.project.load_progress_rollup()
    totals = rollup["totals"]
    assert totals["findingCount"] == 1, "flipping a decision must not double-count the finding"
    assert totals["approvedFindingCount"] == 1
    assert rollup["chapters"]["1"]["verses"]["1"]["findings"]["fake-finding"] == "rejected"


def test_check_job_populates_only_its_own_chapters_in_rollup(fixture_project):
    """A chapter-scope job must leave other chapters' rollup state alone —
    the rollup shouldn't be able to falsely claim an untouched chapter is
    AI-checked."""
    (fixture_project / "rut" / "2.json").write_text(json.dumps({
        "1": "இரண்டாம் அதிகாரம்.",
    }, ensure_ascii=False), encoding="utf-8")
    alignment_dir = fixture_project / ".apps" / "translationCore" / "alignmentData" / "rut"
    (alignment_dir / "2.json").write_text(json.dumps({
        "1": {"alignments": [], "wordBank": [{
            "word": "இரண்டாம்", "occurrence": 1, "occurrences": 1,
        }]},
    }, ensure_ascii=False), encoding="utf-8")
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})

    started = call(engine, "checks.start", {
        "scope": "chapter", "chapters": ["1"], "checks": ["greekroom"],
    })["result"]
    wait_for_job(engine, started["jobId"])

    rollup = engine.project.load_progress_rollup()
    assert rollup["chapters"]["1"]["aiChecked"] is True
    assert "2" not in rollup["chapters"]
    assert rollup["totals"]["checkedChapterCount"] == 1
    assert rollup["totals"]["chapterCount"] == 2


def test_failed_check_job_does_not_mark_chapter_ai_checked(fixture_project, monkeypatch):
    """A job with a failed verse must not claim the chapter is AI-checked —
    the dashboard would otherwise show false coverage."""
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})

    def failing_verse_checks(project, chapter, verse, checks, reads=None):
        raise RuntimeError("simulated verse-check failure")

    monkeypatch.setattr(engine, "_run_verse_checks_for_project", failing_verse_checks)

    started = call(engine, "checks.start", {
        "scope": "chapter", "chapters": ["1"], "checks": ["greekroom"],
    })["result"]
    finished = wait_for_job(engine, started["jobId"])
    assert finished["state"] == "failed"

    rollup = engine.project.load_progress_rollup()
    assert "1" not in rollup.get("chapters", {})


def test_list_book_progress_does_not_materialize_lazy_siblings(tmp_path, fixture_project):
    """The dashboard must be able to summarize a collection without forcing
    every lazy sibling to normalize just to compute stats."""
    primary_dir = fixture_project
    lazy_dir = tmp_path / "gen"
    (lazy_dir / ".bridge").mkdir(parents=True)
    (lazy_dir / ".bridge" / "lazy-import.json").write_text(json.dumps({
        "bookId": "GEN", "bookName": "Genesis",
    }), encoding="utf-8")

    (primary_dir / ".bridge").mkdir(parents=True, exist_ok=True)
    (primary_dir / ".bridge" / "collection.json").write_text(json.dumps({
        "projects": [
            {"directoryName": "rut", "bookId": "RUT", "bookName": "Ruth"},
            {"directoryName": "gen", "bookId": "GEN", "bookName": "Genesis"},
        ],
    }), encoding="utf-8")

    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(primary_dir)})
    call(engine, "verse.decide", {
        "chapter": "1", "verse": "1", "findingId": "fake-finding",
        "status": "accepted", "comment": "",
    })

    result = call(engine, "project.listBookProgress")["result"]
    by_book = {b["bookId"]: b for b in result["books"]}

    assert by_book["GEN"]["lazy"] is True
    assert by_book["GEN"]["progress"] is None
    assert not (lazy_dir / "manifest.json").exists(), "lazy sibling must not be materialized just to list progress"

    assert by_book["RUT"]["lazy"] is False
    assert by_book["RUT"]["progress"]["findingCount"] == 1


def test_settings_supports_any_provider_not_just_openai(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from tc_ai_bridge.secret_store import AppSettings
    isolated = AppSettings(path=tmp_path / "settings.json")
    engine = BridgeEngine(settings=isolated)

    result = call(engine, "settings.set", {
        "provider": "anthropic",
        "apiBaseUrl": "https://api.anthropic.com/v1",
        "model": "claude-sonnet-5",
        "apiKey": "sk-ant-test",
    })["result"]
    assert result["provider"] == "anthropic"
    assert result["apiBaseUrl"] == "https://api.anthropic.com/v1"
    assert result["model"] == "claude-sonnet-5"
    assert result["hasApiKey"] is True


def test_export_non_aligned_writes_real_usfm_file(fixture_project, tmp_path):
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})
    out_path = tmp_path / "export.usfm"
    result = call(engine, "export.nonAligned", {"outputPath": str(out_path)})["result"]
    assert result["written"] is True
    assert result["fidelity"] == "source-preserving"
    content = out_path.read_text(encoding="utf-8")
    assert "\\id RUT" in content
    assert "\\c 1" in content
    assert "\\v 1" in content
    assert "தேவன்" in content
    from tc_ai_bridge.project_import import inspect_import
    preview = inspect_import(out_path)
    assert preview["books"][0]["bookId"] == "rut"
    assert preview["books"][0]["verseCount"] == 1


@pytest.mark.parametrize("source_encoding", ["utf-8", "utf-16"])
def test_non_aligned_export_preserves_usfm_esfm_structure(tmp_path, source_encoding):
    source = tmp_path / "TIT.usfm"
    source.write_text(
        "\\id TIT\n"
        "\\usfm 3.0\n"
        "\\h Titus\n"
        "\\toc1 The Letter to Titus\n"
        "\\c 1\n"
        "\\s1 Greeting\n"
        "\\p\n"
        "\\v 1 Paul spoke. \\f + \\ft A footnote.\\f*\n"
        "\\q1 A poetry continuation.\n"
        "\\v 3-4 Bridged text. \\zbridge custom\\zbridge*\n",
        encoding=source_encoding,
    )
    engine = BridgeEngine()
    imported = call(engine, "project.import", {
        "path": str(source),
        "destinationRoot": str(tmp_path / "projects"),
        "metadata": {
            "languageId": "eng",
            "languageName": "English",
            "languageDirection": "ltr",
            "projectName": "Titus",
            "bibleName": "Test Bible",
        },
    })
    assert imported["success"] is True

    out_path = tmp_path / "exported.usfm"
    exported = call(engine, "export.nonAligned", {"outputPath": str(out_path)})["result"]
    content = out_path.read_text(encoding="utf-8")

    assert exported["fidelity"] == "source-preserving"
    for marker in ("\\usfm 3.0", "\\h Titus", "\\toc1", "\\s1 Greeting", "\\p",
                   "\\f +", "\\ft A footnote.", "\\q1", "\\v 3-4", "\\zbridge"):
        assert marker in content
    from tc_ai_bridge.project_import import inspect_import
    preview = inspect_import(out_path)
    assert preview["books"][0]["verseCount"] == 2


def test_export_keeps_a_heading_between_verses_and_writes_back_a_mid_line_verse(tmp_path):
    """#190: the export used to replace everything from a `\\v` to the next `\\v`, so a
    heading that #180 moved out of the verse text was overwritten and lost, and a
    verse whose `\\v` did not start its line was never matched, so its edit was
    silently dropped. Both are decided by the parser's verse spans now."""
    source = tmp_path / "PSA.usfm"
    source.write_text(
        "\\id PSA\n"
        "\\c 1\n"
        "\\s Book One\n"
        "\\p\n"
        "\\v 1 Blessed is the man.\n"
        "\\s The wicked\n"
        "\\q1 \\v 2 The wicked are not so,\n"
        "\\q2 but are like chaff.\n"
        "\\v 3 Therefore they will not stand.\n",
        encoding="utf-8",
    )
    engine = BridgeEngine()
    imported = call(engine, "project.import", {
        "path": str(source),
        "destinationRoot": str(tmp_path / "projects"),
        "metadata": {
            "languageId": "eng", "languageName": "English", "languageDirection": "ltr",
            "projectName": "Psalms", "bibleName": "Test Bible",
        },
    })
    assert imported["success"] is True
    verses = call(engine, "chapter.verseData", {"chapter": "1"})["result"]
    assert verses["verses"]["1"]["text"] == "Blessed is the man."
    assert verses["verses"]["2"]["text"] == "The wicked are not so,\n\\q2 but are like chaff."
    assert verses["headings"]["2"][0]["text"] == "The wicked"

    edited = call(engine, "verse.edit", {
        "chapter": "1", "verse": "2", "newText": "The wicked are not so,\n\\q2 but are like chaff blown away.",
    })
    assert edited["success"] is True, edited
    edited_one = call(engine, "verse.edit", {"chapter": "1", "verse": "1", "newText": "Blessed is the one."})
    assert edited_one["success"] is True

    out_path = tmp_path / "exported.usfm"
    exported = call(engine, "export.nonAligned", {"outputPath": str(out_path)})["result"]
    assert exported["fidelity"] == "source-preserving"
    content = out_path.read_text(encoding="utf-8")

    # The heading between verses 1 and 2 survives, once, in its own place.
    assert content.count("\\s The wicked") == 1
    assert "\\v 1 Blessed is the one.\n\\s The wicked\n\\q1 \\v 2 The wicked are not so,\n" in content
    # The mid-line verse's edit is written back; the old text is gone.
    assert "chaff blown away." in content
    assert "like chaff.\n" not in content
    assert content.count("\\v 2 ") == 1

    # And the export re-imports to the same project.
    from tc_ai_bridge.project_import import import_source
    from tc_ai_bridge.tc_project import TranslationCoreProject
    reimported = import_source(out_path, tmp_path / "reimported", {
        "languageId": "eng", "languageName": "English", "languageDirection": "ltr",
        "projectName": "Round trip", "bibleName": "Round trip Bible",
    })
    project = TranslationCoreProject(reimported["primaryProjectPath"])
    assert project.target_verse_text("1", "1") == "Blessed is the one."
    assert project.target_verse_text("1", "2") == "The wicked are not so,\n\\q2 but are like chaff blown away."
    assert project.target_verse_text("1", "3") == "Therefore they will not stand."
    assert [h["text"] for h in project.chapter_headings("1")["2"]] == ["The wicked"]


def test_non_aligned_export_has_explicit_fallback_without_source(fixture_project, tmp_path):
    (fixture_project / "rut.usfm").unlink()
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})
    out_path = tmp_path / "fallback.usfm"

    result = call(engine, "export.nonAligned", {"outputPath": str(out_path)})["result"]

    assert result["fidelity"] == "simplified"
    assert "No source USFM was available" in result["note"]
    assert "\\v 1 " in out_path.read_text(encoding="utf-8")


def test_export_aligned_writes_real_json_with_alignment_and_decisions(fixture_project, tmp_path):
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})
    call(engine, "verse.decide", {
        "chapter": "1", "verse": "1", "findingId": "some-finding",
        "status": "accepted", "comment": "ok",
    })
    out_path = tmp_path / "export.json"
    result = call(engine, "export.aligned", {"outputPath": str(out_path)})["result"]
    assert result["written"] is True
    import json as _json
    data = _json.loads(out_path.read_text(encoding="utf-8"))
    assert data["bookId"] == "rut"
    verse1 = data["chapters"]["1"]["1"]
    assert verse1["alignment"]["alignments"][0]["bottomWords"][0]["word"] == "தேவன்"
    assert "some-finding" in verse1["decisions"]


def test_export_aligned_usfm_round_trips_nested_many_to_many_alignment(fixture_project, tmp_path):
    _write_alignment_work_fixture(fixture_project)
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(fixture_project)})
    context = call(engine, "alignment.get", {"chapter": "1", "verse": "1"})["result"]
    context = call(engine, "alignment.realign", {
        "chapter": "1", "verse": "1",
        "topIds": [token["id"] for token in context["topTokens"]],
        "bottomIds": [token["id"] for token in context["bottomTokens"]],
        "expectedOriginal": context["alignment"],
    })["result"]
    assert call(engine, "alignment.complete", {"chapter": "1", "verse": "1"})["success"] is True

    output = tmp_path / "rut-aligned.usfm"
    exported = call(engine, "export.aligned", {"outputPath": str(output)})

    assert exported["success"] is True
    assert exported["result"]["format"] == "usfm3-aligned"
    content = output.read_text(encoding="utf-8")
    assert "\\usfm 3.0" in content
    assert content.count("\\zaln-s") == 2
    assert content.count("\\zaln-e\\*") == 2
    assert "\\w தேவன்|x-occurrence=\"1\" x-occurrences=\"1\"\\w*" in content

    from tc_ai_bridge.project_import import import_source
    from tc_ai_bridge.tc_project import TranslationCoreProject
    reimported = import_source(output, tmp_path / "reimported", {
        "languageId": "tam", "languageName": "Tamil", "languageDirection": "ltr",
        "projectName": "Round trip", "bibleName": "Round trip Bible",
    })
    project = TranslationCoreProject(reimported["primaryProjectPath"])
    round_trip = project.load_verse_alignment("1", "1")
    assert len(round_trip.alignments) == 1
    assert len(round_trip.alignments[0].top_words) == 2
    assert len(round_trip.alignments[0].bottom_words) == 5
    assert round_trip.word_bank == []


def _make_managed_book(managed_root: Path, book_id: str) -> Path:
    path = managed_root / book_id
    (path / book_id).mkdir(parents=True)
    (path / "manifest.json").write_text(json.dumps({
        "project": {"id": book_id, "name": book_id.upper()},
        "target_language": {"id": "tam", "name": "Tamil"},
    }), encoding="utf-8")
    (path / book_id / "1.json").write_text('{"1": "text"}', encoding="utf-8")
    return path


def test_delete_project_removes_a_managed_collection_from_disk_and_registry(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from tc_ai_bridge.secret_store import AppSettings
    engine = BridgeEngine(settings=AppSettings(path=tmp_path / "settings.json"))

    tit_path = _make_managed_book(engine.project_root, "tit")
    phm_path = _make_managed_book(engine.project_root, "phm")
    tit_entry = engine.project_registry.register(tit_path, collection_id="collection-x")
    engine.project_registry.register(phm_path, collection_id="collection-x")

    result = call(engine, "project.delete", {"projectId": tit_entry["projectId"]})["result"]

    assert result == {"deleted": True, "managed": True}
    assert not tit_path.exists()
    assert not phm_path.exists()
    assert call(engine, "project.list")["result"]["projects"] == []


def test_delete_project_never_removes_files_for_an_unmanaged_project(tmp_path, monkeypatch, fixture_project):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from tc_ai_bridge.secret_store import AppSettings
    engine = BridgeEngine(settings=AppSettings(path=tmp_path / "settings.json"))

    registered = engine.project_registry.register(fixture_project)
    assert registered["managed"] is False

    result = call(engine, "project.delete", {"projectId": registered["projectId"]})["result"]

    assert result == {"deleted": True, "managed": False}
    assert fixture_project.exists()
    assert call(engine, "project.list")["result"]["projects"] == []
