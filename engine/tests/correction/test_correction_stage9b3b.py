"""Stage 9B.3b strict explicit-human correction application tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bridge_service import BridgeEngine
from greek_room_engine.protocol import EngineRequest
from tc_ai_bridge.passage_semantic_models import (
    StrictScriptureEditContext,
)
from tc_ai_bridge.passage_semantic_repository import FoundationConflict, FoundationValidationError
from tc_ai_bridge.secret_store import AppSettings
from tc_ai_bridge.tc_project import ProjectError
from tests.support.correction_9b3b import _apply, _fixture, _hash


def _call(engine: BridgeEngine, method: str, params: dict) -> dict:
    return engine.handle_request(
        EngineRequest(id="stage9b3b-integration", method=method, params=params)
    ).to_dict()


def test_cross_verse_prepare_preserves_source_and_target_references_without_editing(
    tmp_path: Path,
) -> None:
    before = "நான் உங்களை நினைக்கும் போதெல்லாம் என் தேவனை ஸ்தோத்திரிக்கிறேன்."
    _root, project, _runtime, service, _finding, proposal = _fixture(
        tmp_path, before, "என் தேவனை", "என் தேவனையே",
    )

    prepared = service._prepare(
        proposal_id="proposal-1", expected_proposal_revision=1,
        finding_id="finding-1", expected_finding_revision=1,
        application_id="prepare-cross-verse", actor_id="Reviewer",
    )

    assert prepared["sourceProvenanceReferences"] == ["PHP 1:3"]
    assert prepared["targetDisplayedReference"] == "PHP 1:6"
    assert prepared["canonicalReferences"] == ["PHP 1:6"]
    assert prepared["expectedStartCodePoint"] == proposal.intent.affected_target_span.start_code_point
    assert prepared["expectedEndCodePoint"] == proposal.intent.affected_target_span.end_code_point
    assert project.target_verse_text("1", "6") == before
    assert project.target_verse_text("1", "3") == "unchanged source-corresponding target"


@pytest.mark.parametrize("before,original,replacement", [
    ("முன் என் தேவனை பின்", "என் தேவனை", "என்னுடைய தேவனை"),
    ("אָבִ֑י שָׁלוֹם סוף", "שָׁלוֹם", "שָׁלוֹם־לְךָ"),
    ("ἀρχή λόγος τέλος", "λόγος", "λόγος"),
    ("left 😀 right", "😀", "𐐷"),
    ("prefix suffix", "", "insert "),
    ("prefix delete suffix", "delete ", ""),
])
def test_exact_unicode_replacement_insertion_and_deletion(
    tmp_path: Path, before: str, original: str, replacement: str,
) -> None:
    root, project, runtime, service, finding, proposal = _fixture(
        tmp_path, before, original, replacement,
    )
    result = _apply(service)
    span = proposal.intent.affected_target_span
    expected = before[:span.start_code_point] + replacement + before[span.end_code_point:]
    assert result["applicationState"] == "COMPLETED"
    assert project.target_verse_text("1", "6") == expected
    assert project.target_verse_text("1", "3") == "unchanged source-corresponding target"
    assert (root / "php.usfm").read_text(encoding="utf-8").endswith("\\v 6 IMPORTED SIX\n")
    assert (root / ".apps" / "translationCore" / "alignmentData" / "php" / "1.json.invalid").exists() is False
    assert (root / ".apps" / "translationCore" / "tools" / "wordAlignment" / "invalid" / "1" / "6.json").exists()
    stored_finding = runtime.repository.qa_finding("finding-1")
    assert stored_finding["qaDisposition"] == "CONFIRMED_TRANSLATION_ERROR"
    assert stored_finding["lifecycleStatus"] == "STALE"
    assert runtime.repository.correction_proposal("proposal-1")["verificationStatus"] == "PENDING"
    assert result["resultMetadata"]["affectedAnalysisStarted"] is False


def test_duplicate_apply_is_idempotent_and_never_inserts_twice(tmp_path: Path) -> None:
    _root, project, _runtime, service, _finding, _proposal = _fixture(
        tmp_path, "before after", "", "NEW ",
    )
    first = _apply(service)
    second = _apply(service, application_id="different-retry-id")
    assert second["applicationId"] == first["applicationId"]
    assert project.target_verse_text("1", "6") == "NEW before after"


def test_external_edit_causes_revision_conflict_without_relocation(tmp_path: Path) -> None:
    _root, project, _runtime, service, _finding, _proposal = _fixture(
        tmp_path, "same token suffix", "same", "reviewed",
    )
    project.apply_scripture_edit("1", "6", "external token suffix")
    with pytest.raises((FoundationConflict, FoundationValidationError), match="changed|REVISION_CONFLICT"):
        _apply(service)
    assert project.target_verse_text("1", "6") == "external token suffix"


def test_unreviewed_proposal_is_not_applicable(tmp_path: Path) -> None:
    _root, project, runtime, service, _finding, _proposal = _fixture(
        tmp_path, "before", "before", "after",
    )
    with runtime.repository._connect() as conn:
        payload = runtime.repository.correction_proposal("proposal-1")
        payload["reviewStatus"] = "AI_PROPOSED"
        conn.execute(
            "UPDATE correction_proposals SET review_status='AI_PROPOSED',payload_json=? WHERE id='proposal-1'",
            (json.dumps(payload, ensure_ascii=False),),
        )
        conn.commit()
    with pytest.raises(FoundationValidationError, match="reviewed by a human"):
        _apply(service)
    assert project.target_verse_text("1", "6") == "before"


def test_installed_style_apply_persists_completed_ledger_after_disk_reopen(
    tmp_path: Path,
) -> None:
    """The desktop-managed project path is the authoritative acceptance path.

    Exercise the real protocol, close every in-memory object, and reopen the
    same on-disk project before asserting the application, proposal, and
    finding state. This prevents an acceptance check from accidentally reading
    the source folder that Bridge copied during import.
    """
    before = "நான் உங்களை நினைக்கும் போதெல்லாம் என் தேவனை ஸ்தோத்திரிக்கிறேன்."
    replacement = "என் தேவனையே"
    app_data = tmp_path / "Bridge" / "data"
    managed = app_data / "projects" / "ta_irv_php"
    root, _project, _runtime, _service, finding, proposal = _fixture(
        tmp_path, before, "என் தேவனை", replacement, project_root=managed,
    )
    identity = root / ".bridge" / "project.json"
    identity.parent.mkdir(parents=True, exist_ok=True)
    identity.write_text(json.dumps({
        "schemaVersion": 1, "projectId": "project-1", "collectionId": "",
        "sourceFingerprint": "installed-style-fixture",
        "createdAt": "2026-09-06T00:00:00Z",
    }), encoding="utf-8")

    settings_path = app_data / "settings.json"
    engine = BridgeEngine(settings=AppSettings(path=settings_path))
    opened = _call(engine, "project.open", {"path": str(root)})
    assert opened["success"] is True, opened
    assert Path(opened["result"]["path"]).resolve() == root.resolve()

    applied = _call(engine, "correction.applyProposal", {
        "proposalId": proposal.id,
        "expectedProposalRevision": proposal.revision,
        "findingId": finding["id"],
        "expectedFindingRevision": finding["revision"],
        "applicationId": "installed-style-apply-1",
        "actor": {"actorType": "HUMAN", "actorId": "Reviewer"},
    })
    assert applied["success"] is True, applied
    assert applied["result"]["applicationState"] == "COMPLETED"

    # Recreate the service/runtime from disk. No assertion below relies on the
    # objects that performed the write.
    restarted = BridgeEngine(settings=AppSettings(path=settings_path))
    reopened = _call(restarted, "project.open", {
        "path": str(root), "projectId": "project-1",
    })
    assert reopened["success"] is True, reopened
    status = _call(restarted, "correction.getApplicationStatus", {
        "applicationId": "installed-style-apply-1",
    })
    assert status["success"] is True, status
    application = status["result"]
    assert application["applicationState"] == "COMPLETED"
    assert application["sourceProvenanceReferences"] == ["PHP 1:3"]
    assert application["targetDisplayedReference"] == "PHP 1:6"

    repository = restarted.passage_semantic_runtime.repository
    with repository._connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM correction_application_intents "
            "WHERE proposal_id=? AND expected_proposal_revision=?",
            (proposal.id, proposal.revision),
        ).fetchone()[0]
    assert count == 1
    persisted_proposal = repository.correction_proposal(proposal.id)
    assert persisted_proposal["appliedTargetRevision"]
    assert persisted_proposal["appliedBy"] == "Reviewer"
    assert persisted_proposal["appliedAt"]
    assert persisted_proposal["verificationStatus"] == "PENDING"
    persisted_finding = repository.qa_finding(finding["id"])
    assert persisted_finding["lifecycleStatus"] == "STALE"
    assert persisted_finding["qaDisposition"] == "CONFIRMED_TRANSLATION_ERROR"
    start = before.index("என் தேவனை")
    expected = before[:start] + replacement + before[start + len("என் தேவனை"):]
    assert restarted.project.target_verse_text("1", "6") == expected


def test_application_persistence_failure_leaves_scripture_and_alignment_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _project, runtime, service, _finding, _proposal = _fixture(
        tmp_path, "before original after", "original", "reviewed",
    )
    chapter = root / "php" / "1.json"
    alignment = root / ".apps" / "translationCore" / "alignmentData" / "php" / "1.json"
    before = (chapter.read_bytes(), alignment.read_bytes())

    def fail_persistence(*_args, **_kwargs):
        raise OSError("simulated durable application persistence failure")

    monkeypatch.setattr(runtime.repository, "prepare_application_intent", fail_persistence)
    with pytest.raises(OSError, match="durable application persistence failure"):
        _apply(service)
    assert (chapter.read_bytes(), alignment.read_bytes()) == before
    assert not (root / ".apps" / "translationCore" / "tools" / "wordAlignment" / "invalid" / "1" / "6.json").exists()


def test_strict_writer_rejects_prepared_invalidation_without_application_ledger(
    tmp_path: Path,
) -> None:
    root, project, runtime, _service, _finding, _proposal = _fixture(
        tmp_path, "before original after", "original", "reviewed",
    )
    before = project.target_verse_text("1", "6")
    final = "before reviewed after"
    current = runtime.repository.current_target_revision("project-1", "PHP", "PHP 1:6")
    invalidation_id = runtime.repository.prepare_target_invalidation(
        project_id="project-1", book="PHP", displayed_reference="PHP 1:6",
        previous_text_hash=_hash(before), expected_text_hash=_hash(final),
    )
    strict = StrictScriptureEditContext(
        expected_target_revision=current["textRevision"],
        expected_target_content_hash=_hash(before),
        expected_original_verse_text=before,
        expected_start_code_point=7, expected_end_code_point=15,
        expected_original_span_text="original", intended_final_verse_text=final,
        pending_invalidation_id=invalidation_id,
        application_id="missing-application-ledger",
    )
    chapter_before = (root / "php" / "1.json").read_bytes()
    with pytest.raises(ProjectError, match="CORRECTION_APPLICATION_NOT_DURABLE"):
        project.apply_scripture_edit(
            "1", "6", final, strict_context=strict,
            journal_prepared_callback=lambda _transaction_id: None,
        )
    assert (root / "php" / "1.json").read_bytes() == chapter_before
