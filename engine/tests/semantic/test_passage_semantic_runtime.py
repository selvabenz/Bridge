from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import bridge_service
from bridge_service import BridgeEngine
from greek_room_engine.protocol import EngineRequest
from tc_ai_bridge.passage_semantic_models import (
    LifecycleStatus,
    QaDisposition,
    ReviewStatus,
)
from tc_ai_bridge import passage_semantic_runtime as psr
from tc_ai_bridge.passage_semantic_repository import (
    FoundationConflict,
    FoundationRepository,
    FoundationValidationError,
)
from tc_ai_bridge.passage_semantic_runtime import (
    _canonical_reference,
    build_current_text_overlay,
    tokenize_target_text,
)
from tc_ai_bridge.secret_store import AppSettings
from tc_ai_bridge.tc_project import TranslationCoreProject


def _call(engine: BridgeEngine, method: str, params: dict | None = None) -> dict:
    return engine.handle_request(
        EngineRequest(id="stage4", method=method, params=params or {})
    ).to_dict()


def _write_project(root: Path, *, usfm: bool = True) -> Path:
    align_dir = root / ".apps" / "translationCore" / "alignmentData" / "rut"
    align_dir.mkdir(parents=True)
    (root / "rut").mkdir(parents=True)
    (root / "manifest.json").write_text(json.dumps({
        "project": {"id": "rut", "name": "Ruth"},
        "target_language": {"id": "tam", "name": "Tamil"},
        "resource": {"id": "irv", "name": "IRVTam"},
        "tc_version": "8",
    }), encoding="utf-8")
    (root / "rut" / "1.json").write_text(json.dumps({
        "1": "புதிய தற்போதைய வசனம்.",
        "2-3": "பாலமாக உள்ள தற்போதைய வசனம்.",
    }, ensure_ascii=False), encoding="utf-8")
    (root / "rut" / "2.json").write_text(json.dumps({
        "1": "அடுத்த அதிகாரத்தின் தற்போதைய வசனம்.",
    }, ensure_ascii=False), encoding="utf-8")
    alignment = {
        "1": {
            "alignments": [{
                "topWords": [{"word": "דָּבָר", "occurrence": 1, "occurrences": 1}],
                "bottomWords": [{"word": "புதிய", "occurrence": 1, "occurrences": 1}],
            }],
            "wordBank": [],
        },
        "2-3": {"alignments": [], "wordBank": []},
    }
    (align_dir / "1.json").write_text(
        json.dumps(alignment, ensure_ascii=False), encoding="utf-8"
    )
    (align_dir / "2.json").write_text(json.dumps({
        "1": {"alignments": [], "wordBank": []},
    }), encoding="utf-8")
    if usfm:
        (root / "rut.usfm").write_text(
            "\\id RUT\n"
            "\\h Old heading wording\n"
            "\\c 1\n"
            "\\s Old section heading\n"
            "\\p\n"
            "\\v 1 OLD IMPORTED SCRIPTURE \\f + \\ft old footnote words\\f* "
            "\\x + \\xt old cross reference words\\x* \\add wrapper\\add*\n"
            "\\q1\n"
            "\\v 2-3 OLD BRIDGE WORDING\n"
            "\\c 2\n"
            "\\v 1 OLD CHAPTER TWO WORDING\n",
            encoding="utf-8",
        )
    return root


@pytest.fixture
def stage4_project(tmp_path: Path) -> Path:
    return _write_project(tmp_path / "project")


def _engine(tmp_path: Path) -> BridgeEngine:
    return BridgeEngine(settings=AppSettings(path=tmp_path / "settings.json"))


_SCAN_SCHEMA = "translationCore.alignmentData.compatibility-scan.v1"


def _scan_report(report: dict) -> dict:
    """The compatibility scan's own report, chosen by schema rather than position.

    `migration_report()["runs"]` holds every run for the project ordered by
    `started_at, id`, and `alignment_compatibility_scan` saves up to two of them:
    an `...invalidation-state.v1` run whose report is `{"staled": N}`, and the
    compatibility scan. `runs[-1]` assumes the scan is last.

    It usually is, and once was not: CI failed with `KeyError: 'quarantined'`
    while the identical assertion passed locally and on the next run. The state
    run has no `quarantined` key, so reading the wrong one fails in a way that
    says nothing about why.
    """
    scans = [run for run in report["runs"] if run["sourceSchema"] == _SCAN_SCHEMA]
    assert scans, (
        "no compatibility-scan run in this report; schemas present: "
        f"{[run['sourceSchema'] for run in report['runs']]}"
    )
    return scans[-1]["report"]


def test_first_open_initializes_companion_and_second_open_is_idempotent(
    tmp_path: Path, stage4_project: Path,
) -> None:
    engine = _engine(tmp_path)
    first = _call(engine, "project.open", {"path": str(stage4_project)})
    assert first["success"] is True
    assert first["result"]["passageSemantic"]["state"] == "READY"
    database = stage4_project / ".apps" / "translationCoreAI" / "passageSemantic" / "bridge-semantic.sqlite3"
    assert database.is_file()
    project_id = first["result"]["projectId"]

    second = _call(engine, "project.open", {"path": str(stage4_project)})
    assert second["success"] is True
    assert second["result"]["projectId"] == project_id
    assert second["result"]["passageSemantic"]["recovery"]["ok"] is True
    status = _call(engine, "passageSemantic.status")
    assert status["result"]["state"] == "READY"
    passage = _call(engine, "passageSemantic.getCurrentPassage", {
        "chapter": "1", "verse": "1",
    })
    assert passage["success"] is True
    assert passage["result"]["targetTextByDisplayedReference"]["RUT 1:1"] == "புதிய தற்போதைய வசனம்."


def test_moved_project_retains_identity_but_live_copy_is_rejected(
    tmp_path: Path, stage4_project: Path,
) -> None:
    engine = _engine(tmp_path)
    opened = _call(engine, "project.open", {"path": str(stage4_project)})
    project_id = opened["result"]["projectId"]

    copy = tmp_path / "live-copy"
    shutil.copytree(stage4_project, copy)
    ambiguous = _call(engine, "project.open", {"path": str(copy)})
    assert ambiguous["success"] is False
    assert "two accessible paths" in ambiguous["error"]["message"]

    shutil.rmtree(copy)
    moved = tmp_path / "moved-project"
    shutil.move(str(stage4_project), moved)
    reopened = _call(engine, "project.open", {"path": str(moved)})
    assert reopened["success"] is True
    assert reopened["result"]["projectId"] == project_id


def test_companion_failure_does_not_block_scripture_access(
    tmp_path: Path, stage4_project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenRuntime:
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("damaged companion database")

    monkeypatch.setattr(bridge_service, "PassageSemanticRuntime", BrokenRuntime)
    engine = _engine(tmp_path)
    opened = _call(engine, "project.open", {"path": str(stage4_project)})
    assert opened["success"] is True
    assert opened["result"]["passageSemantic"]["state"] == "RECOVERY_REQUIRED"
    verse = _call(engine, "verse.get", {"chapter": "1", "verse": "1"})
    assert verse["success"] is True
    assert verse["result"]["text"] == "புதிய தற்போதைய வசனம்."


def test_real_bridge_edit_is_authoritative_and_old_usfm_words_never_return(
    tmp_path: Path, stage4_project: Path,
) -> None:
    engine = _engine(tmp_path)
    _call(engine, "project.open", {"path": str(stage4_project)})
    edited = "திருத்தப்பட்ட தற்போதைய வசனம்."
    result = _call(engine, "verse.edit", {
        "chapter": "1", "verse": "1", "newText": edited,
    })
    assert result["success"] is True
    passage = engine.passage_semantic_runtime.get_current_passage("1", "1", "2", "1")
    all_text = " ".join(passage["targetTextByDisplayedReference"].values())
    assert edited in all_text
    assert "OLD IMPORTED SCRIPTURE" not in all_text
    assert "old footnote words" not in all_text
    assert "old cross reference words" not in all_text
    assert "Old section heading" not in all_text


def test_structure_overlay_preserves_markers_not_old_scripture_and_crosses_chapters(
    stage4_project: Path,
) -> None:
    project = TranslationCoreProject(stage4_project)
    overlay = build_current_text_overlay(project)
    assert [segment.reference for segment in overlay.index.segments] == [
        "RUT 1:1", "RUT 1:2-3", "RUT 2:1",
    ]
    assert [segment.text for segment in overlay.index.segments] == [
        "புதிய தற்போதைய வசனம்.",
        "பாலமாக உள்ள தற்போதைய வசனம்.",
        "அடுத்த அதிகாரத்தின் தற்போதைய வசனம்.",
    ]
    kinds = {marker.kind.value for marker in overlay.structure_markers}
    assert {"PARAGRAPH", "POETRY", "VERSE_BRIDGE", "HEADING", "NOTE", "CROSS_REFERENCE", "INLINE_MARKUP"} <= kinds


def test_project_without_preserved_usfm_uses_current_text_only(tmp_path: Path) -> None:
    root = _write_project(tmp_path / "without-usfm", usfm=False)
    overlay = build_current_text_overlay(TranslationCoreProject(root))
    assert overlay.structure_resource_id == "current-chapter-json-only"
    assert "புதிய தற்போதைய வசனம்." in overlay.index.segments[0].text


@pytest.mark.parametrize("text", [
    "தமிழ் கொ",                     # Tamil combining character sequence
    "בְּרֵאשִׁ֖ית",                  # Hebrew niqqud/cantillation
    "Ἰησοῦς Ι\u0307ησους",           # precomposed and combining Greek
    "word \U0001F642 word",           # supplementary Unicode scalar
])
def test_companion_tokenizer_emits_valid_codepoint_and_grapheme_spans(text: str) -> None:
    tokens = tokenize_target_text(text)
    assert tokens
    for token in tokens:
        assert text[token["start"]:token["end"]] == token["raw"]
        assert token["startGrapheme"] <= token["endGrapheme"]


def test_human_review_is_preserved_when_dependent_record_becomes_stale(
    tmp_path: Path, stage4_project: Path,
) -> None:
    engine = _engine(tmp_path)
    _call(engine, "project.open", {"path": str(stage4_project)})
    runtime = engine.passage_semantic_runtime
    runtime.repository.create_qa_finding("qa-direct", runtime.project_id)
    runtime.repository.update_qa_disposition(
        "qa-direct", QaDisposition.ACCEPTABLE_TRANSLATION, 1, "Reviewer",
    )
    dependency = runtime.repository.target_dependency_id(runtime.project_id, "RUT", "RUT 1:1")
    runtime.repository.add_record_dependency("QA_FINDING", "qa-direct", "TARGET_REFERENCE", dependency)

    _call(engine, "verse.edit", {
        "chapter": "1", "verse": "1", "newText": "மறுபடியும் திருத்தப்பட்ட வசனம்.",
    })
    finding = runtime.repository.qa_finding("qa-direct")
    assert finding["reviewStatus"] == ReviewStatus.HUMAN_APPROVED.value
    assert finding["lifecycleStatus"] == LifecycleStatus.STALE.value


def test_invalidation_is_dependency_bounded(
    tmp_path: Path, stage4_project: Path,
) -> None:
    engine = _engine(tmp_path)
    _call(engine, "project.open", {"path": str(stage4_project)})
    runtime = engine.passage_semantic_runtime
    for finding_id, reference in (("qa-searched", "RUT 1:1"), ("qa-unrelated", "RUT 2:1")):
        runtime.repository.create_qa_finding(finding_id, runtime.project_id)
        runtime.repository.add_record_dependency(
            "QA_FINDING", finding_id, "TARGET_REFERENCE",
            runtime.repository.target_dependency_id(runtime.project_id, "RUT", reference),
        )

    _call(engine, "verse.edit", {
        "chapter": "1", "verse": "1", "newText": "சார்பு மாற்றப்பட்ட வசனம்.",
    })
    assert runtime.repository.qa_finding("qa-searched")["lifecycleStatus"] == "STALE"
    assert runtime.repository.qa_finding("qa-unrelated")["lifecycleStatus"] == "ACTIVE"


def test_invalidation_propagates_through_passage_dependency(
    tmp_path: Path, stage4_project: Path,
) -> None:
    engine = _engine(tmp_path)
    _call(engine, "project.open", {"path": str(stage4_project)})
    runtime = engine.passage_semantic_runtime
    passage = runtime.rebuild_current_passage("1", "1")
    runtime.repository.create_qa_finding("qa-via-passage", runtime.project_id)
    runtime.repository.add_record_dependency(
        "QA_FINDING", "qa-via-passage", "PASSAGE_RECORD", passage["id"],
    )
    _call(engine, "verse.edit", {
        "chapter": "1", "verse": "1", "newText": "தொடர் சார்பு மாற்றம்.",
    })
    assert runtime.repository.passage_record(passage["id"])["lifecycleStatus"] == "STALE"
    assert runtime.repository.qa_finding("qa-via-passage")["lifecycleStatus"] == "STALE"


def test_interrupted_invalidation_replays_on_restart(
    tmp_path: Path, stage4_project: Path,
) -> None:
    engine = _engine(tmp_path)
    opened = _call(engine, "project.open", {"path": str(stage4_project)})
    runtime = engine.passage_semantic_runtime
    runtime.repository.create_qa_finding("qa-replay", runtime.project_id)
    runtime.repository.add_record_dependency(
        "QA_FINDING", "qa-replay", "TARGET_REFERENCE",
        runtime.repository.target_dependency_id(runtime.project_id, "RUT", "RUT 1:1"),
    )
    new_text = "விபத்துக்குப் பிறகு மீட்டெடுக்கப்பட்ட வசனம்."
    runtime.prepare_target_edit("1", "1", "புதிய தற்போதைய வசனம்.", new_text)
    chapter_path = stage4_project / "rut" / "1.json"
    chapter = json.loads(chapter_path.read_text(encoding="utf-8"))
    chapter["1"] = new_text
    chapter_path.write_text(json.dumps(chapter, ensure_ascii=False), encoding="utf-8")

    restarted = _engine(tmp_path)
    result = _call(restarted, "project.open", {
        "path": str(stage4_project), "projectId": opened["result"]["projectId"],
    })
    assert result["success"] is True
    assert restarted.passage_semantic_runtime.replayed_invalidations == 1
    assert restarted.passage_semantic_runtime.repository.qa_finding("qa-replay")["lifecycleStatus"] == "STALE"


def test_open_edit_rebuild_reopen_smoke_preserves_stale_review_state(
    tmp_path: Path, stage4_project: Path,
) -> None:
    engine = _engine(tmp_path)
    opened = _call(engine, "project.open", {"path": str(stage4_project)})
    runtime = engine.passage_semantic_runtime
    before = runtime.rebuild_current_passage("1", "1")
    runtime.repository.create_qa_finding("qa-smoke", runtime.project_id)
    runtime.repository.update_qa_disposition(
        "qa-smoke", QaDisposition.ACCEPTABLE_TRANSLATION, 1, "Reviewer",
    )
    runtime.repository.add_record_dependency(
        "QA_FINDING", "qa-smoke", "PASSAGE_RECORD", before["id"],
    )
    edited_text = "முழு சுற்றுச் சோதனை வசனம்."
    assert _call(engine, "verse.edit", {
        "chapter": "1", "verse": "1", "newText": edited_text,
    })["success"] is True
    after = runtime.rebuild_current_passage("1", "1")
    assert before["targetContentHash"] != after["targetContentHash"]
    assert set(before["targetTokenInstanceIds"]).isdisjoint(after["targetTokenInstanceIds"])

    restarted = _engine(tmp_path)
    reopened = _call(restarted, "project.open", {
        "path": str(stage4_project), "projectId": opened["result"]["projectId"],
    })
    assert reopened["success"] is True
    finding = restarted.passage_semantic_runtime.repository.qa_finding("qa-smoke")
    assert finding["reviewStatus"] == "HUMAN_APPROVED"
    assert finding["lifecycleStatus"] == "STALE"
    current = restarted.passage_semantic_runtime.get_current_passage("1", "1")
    assert current["targetTextByDisplayedReference"]["RUT 1:1"] == edited_text


def test_target_edits_create_new_instances_and_only_lineage_candidates(
    tmp_path: Path, stage4_project: Path,
) -> None:
    engine = _engine(tmp_path)
    _call(engine, "project.open", {"path": str(stage4_project)})
    runtime = engine.passage_semantic_runtime
    before = runtime.rebuild_current_passage("1", "1")
    before_ids = set(before["targetTokenInstanceIds"])
    _call(engine, "verse.edit", {
        "chapter": "1", "verse": "1", "newText": "புதிய சொல் தற்போதைய வசனம்.",
    })
    after = runtime.rebuild_current_passage("1", "1")
    after_ids = set(after["targetTokenInstanceIds"])
    assert before_ids.isdisjoint(after_ids)
    candidates = runtime.repository.token_lineage_candidates(runtime.project_id)
    assert candidates
    assert {item["relation"] for item in candidates} == {"POSSIBLE_SUCCESSOR"}


def test_source_lock_change_stales_only_declared_dependents(tmp_path: Path) -> None:
    repo = FoundationRepository(tmp_path / "semantic.sqlite3")
    repo.bind_project_metadata(
        project_id="p", identity_fingerprint="identity", book="RUT",
        target_language_id="tam", resource_id="irv", path=str(tmp_path),
    )
    repo.synchronize_source_lock(
        project_id="p", book="RUT", resource_id="UHB", resource_version="1", resource_hash="old",
    )
    repo.create_qa_finding("qa-source", "p")
    repo.create_qa_finding("qa-other", "p")
    repo.add_record_dependency("QA_FINDING", "qa-source", "SOURCE_RESOURCE", "p\u241fRUT\u241fold")
    changed = repo.synchronize_source_lock(
        project_id="p", book="RUT", resource_id="UHB", resource_version="2", resource_hash="new",
    )
    assert changed == {"changed": True, "staled": 1, "resourceId": "UHB", "resourceVersion": "2", "resourceHash": "new"}
    assert repo.qa_finding("qa-source")["lifecycleStatus"] == "STALE"
    assert repo.qa_finding("qa-other")["lifecycleStatus"] == "ACTIVE"


def test_pending_invalidation_uses_current_revision_cas(tmp_path: Path) -> None:
    repo = FoundationRepository(tmp_path / "semantic.sqlite3")
    repo.bind_project_metadata(
        project_id="p", identity_fingerprint="identity", book="RUT",
        target_language_id="tam", resource_id="irv", path=str(tmp_path),
    )
    repo.establish_target_revision(
        project_id="p", book="RUT", displayed_reference="RUT 1:1",
        text_hash="old", text_revision="revision-old",
    )
    first = repo.prepare_target_invalidation(
        project_id="p", book="RUT", displayed_reference="RUT 1:1",
        previous_text_hash="old", expected_text_hash="first",
    )
    competing = repo.prepare_target_invalidation(
        project_id="p", book="RUT", displayed_reference="RUT 1:1",
        previous_text_hash="old", expected_text_hash="second",
    )
    repo.apply_target_invalidation(
        first, actual_text_hash="first", text_revision="revision-first",
    )
    with pytest.raises(FoundationConflict, match="revision changed"):
        repo.apply_target_invalidation(
            competing, actual_text_hash="second", text_revision="revision-second",
        )


def test_open_and_passage_rebuild_do_not_mutate_scripture_or_native_alignment(
    tmp_path: Path, stage4_project: Path,
) -> None:
    scripture = (stage4_project / "rut" / "1.json").read_bytes()
    alignment = (
        stage4_project / ".apps" / "translationCore" / "alignmentData" / "rut" / "1.json"
    ).read_bytes()
    engine = _engine(tmp_path)
    _call(engine, "project.open", {"path": str(stage4_project)})
    engine.passage_semantic_runtime.rebuild_current_passage("1", "1")
    assert (stage4_project / "rut" / "1.json").read_bytes() == scripture
    assert (
        stage4_project / ".apps" / "translationCore" / "alignmentData" / "rut" / "1.json"
    ).read_bytes() == alignment


def test_native_alignment_ambiguities_are_quarantined_without_rewrite(
    tmp_path: Path, stage4_project: Path,
) -> None:
    path = stage4_project / ".apps" / "translationCore" / "alignmentData" / "rut" / "1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    duplicate = {"word": "புதிய", "occurrence": 1, "occurrences": 1}
    payload["1"]["alignments"] = [
        {"topWords": [{"word": "א", "occurrence": 1, "occurrences": 1}], "bottomWords": [duplicate]},
        {"topWords": [{"word": "ב", "occurrence": 1, "occurrences": 1}], "bottomWords": [duplicate]},
        # Two source words sharing one lost target side: a real translationCore
        # group we cannot interpret, so it is still quarantined. (A single
        # topWord with an empty bottom is the raw-import stub and is skipped --
        # see test_raw_import_stubs_are_skipped_but_real_empty_bottoms_are_not.)
        {"topWords": [
            {"word": "ג", "occurrence": 1, "occurrences": 1},
            {"word": "ד", "occurrence": 1, "occurrences": 1},
        ], "bottomWords": []},
    ]
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    original = path.read_bytes()
    engine = _engine(tmp_path)
    _call(engine, "project.open", {"path": str(stage4_project)})
    report = engine.passage_semantic_runtime.migration_report()
    assert report["quarantineByReason"]["DUPLICATE_ACTIVE_TOKEN_MEMBERSHIP"] == 1
    assert report["quarantineByReason"]["LEGACY_EMPTY_BOTTOM_WORDS_AMBIGUOUS"] == 1
    assert path.read_bytes() == original


def test_raw_import_stubs_are_skipped_but_real_empty_bottoms_are_not(
    tmp_path: Path, stage4_project: Path,
) -> None:
    """#99: the raw importer writes one empty group per unaligned source word.

    Quarantining those produced one row per word of the book (20,612 for
    Genesis) recording nothing anyone reads. The skip is shape-exact, so a
    genuine translationCore group that merely lost its target side still gets
    quarantined.
    """
    path = stage4_project / ".apps" / "translationCore" / "alignmentData" / "rut" / "1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["1"]["alignments"] = [
        # Exactly what blank_source_alignments writes: skipped.
        {"topWords": [{"word": "א", "occurrence": 1, "occurrences": 1}], "bottomWords": []},
        {"topWords": [{"word": "ב", "occurrence": 1, "occurrences": 1}], "bottomWords": []},
        # Same empty bottom, but carrying a key the stub never has: quarantined.
        {"topWords": [{"word": "ג", "occurrence": 1, "occurrences": 1}],
         "bottomWords": [], "alignmentIndex": 7},
    ]
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    original = path.read_bytes()

    engine = _engine(tmp_path)
    _call(engine, "project.open", {"path": str(stage4_project)})
    report = engine.passage_semantic_runtime.migration_report()

    assert report["quarantineByReason"].get("LEGACY_EMPTY_BOTTOM_WORDS_AMBIGUOUS", 0) == 1
    latest = _scan_report(report)
    assert latest["rawImportStubsSkipped"] == 2
    assert latest["legacyEmptyBottomWords"] == 1
    # Skipping is not rewriting: the file on disk is untouched either way.
    assert path.read_bytes() == original


def test_first_open_establishes_every_verse_revision_in_one_batch(
    tmp_path: Path, stage4_project: Path, monkeypatch,
) -> None:
    """A first open used one read connection and one commit per verse:
    1,533 commits and 20.8 s for Genesis. It must read the book's revisions
    once and establish everything new in one transaction; a second open
    establishes nothing."""
    from tc_ai_bridge.passage_semantic_repository import FoundationRepository

    batches: list[int] = []
    real_bulk = FoundationRepository.establish_target_revisions_bulk

    def recording_bulk(self, **kwargs):
        batches.append(len(kwargs["revisions"]))
        return real_bulk(self, **kwargs)

    monkeypatch.setattr(FoundationRepository, "establish_target_revisions_bulk", recording_bulk)
    monkeypatch.setattr(
        FoundationRepository, "current_target_revision",
        lambda self, *a, **k: pytest.fail("synchronize_current_text must not read one verse at a time"),
    )
    engine = _engine(tmp_path)
    _call(engine, "project.open", {"path": str(stage4_project)})
    project = TranslationCoreProject(str(stage4_project))
    verses = len(psr.current_target_text(project))
    assert verses > 0
    assert [n for n in batches if n] == [verses]
    stored = engine.passage_semantic_runtime.repository.current_target_revisions(
        engine.passage_semantic_runtime.project_id, engine.passage_semantic_runtime.book,
    )
    assert len(stored) == verses
    batches.clear()
    _call(engine, "project.open", {"path": str(stage4_project)})
    assert [n for n in batches if n] == []


def test_alignment_compatibility_scan_quarantines_in_one_batch(
    tmp_path: Path, stage4_project: Path, monkeypatch,
) -> None:
    """A raw import writes every unaligned source word as its own empty group,
    so the scan quarantines one record per word of the book. Written one
    commit at a time that took 5m11s for Genesis (20,612 words) and timed out
    project.import; the scan must hand the repository one batch."""
    path = stage4_project / ".apps" / "translationCore" / "alignmentData" / "rut" / "1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    # Two topWords, so these are genuinely-ambiguous records rather than
    # raw-import stubs, which #99 now skips. What this test is about is that the
    # scan hands the repository ONE batch however many there are.
    payload["1"]["alignments"] = [
        {"topWords": [
            {"word": f"w{i}", "occurrence": 1, "occurrences": 1},
            {"word": f"x{i}", "occurrence": 1, "occurrences": 1},
        ], "bottomWords": []}
        for i in range(120)
    ]
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    from tc_ai_bridge.passage_semantic_repository import FoundationRepository

    batches: list[int] = []
    real_bulk = FoundationRepository.quarantine_migration_records_bulk

    def recording_bulk(self, records):
        batches.append(len(records))
        return real_bulk(self, records)

    monkeypatch.setattr(FoundationRepository, "quarantine_migration_records_bulk", recording_bulk)
    monkeypatch.setattr(
        FoundationRepository, "quarantine_migration_record",
        lambda self, **kwargs: pytest.fail("scan must not quarantine one record at a time"),
    )
    engine = _engine(tmp_path)
    _call(engine, "project.open", {"path": str(stage4_project)})
    report = engine.passage_semantic_runtime.migration_report()
    assert report["quarantineByReason"]["LEGACY_EMPTY_BOTTOM_WORDS_AMBIGUOUS"] == 120
    assert batches == [120]
    assert _scan_report(report)["quarantined"] == 120
    # Memoized against the alignment folder's content digest: a second open
    # does not scan again, so it writes nothing.
    _call(engine, "project.open", {"path": str(stage4_project)})
    assert batches == [120]


def test_an_alignment_change_rescans_only_its_chapter_and_records_nothing_twice(
    tmp_path: Path, stage4_project: Path,
) -> None:
    """Every Scripture edit rewrites one chapter's alignment file. The scan
    used to key only on the whole folder's digest, so each edit re-parsed every
    chapter of the book (154 ms per edit on Malayalam Romans, more for a long
    book) and re-inserted every legacy issue of the unchanged chapters into the
    append-only quarantine. Now each file is scanned once per content."""
    align_dir = stage4_project / ".apps" / "translationCore" / "alignmentData" / "rut"
    legacy = json.loads((align_dir / "1.json").read_text(encoding="utf-8"))
    legacy["1"]["alignments"].append({"topWords": [
        {"word": "ג", "occurrence": 1, "occurrences": 1},
        {"word": "ד", "occurrence": 1, "occurrences": 1},
    ], "bottomWords": []})
    (align_dir / "1.json").write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    engine = _engine(tmp_path)
    _call(engine, "project.open", {"path": str(stage4_project)})
    runtime = engine.passage_semantic_runtime
    first = _scan_report(runtime.migration_report())
    assert (first["filesScanned"], first["filesUnchanged"]) == (2, 0)
    assert runtime.migration_report()["quarantineByReason"]["LEGACY_EMPTY_BOTTOM_WORDS_AMBIGUOUS"] == 1

    chapter_two = json.loads((align_dir / "2.json").read_text(encoding="utf-8"))
    chapter_two["1"]["wordBank"] = [{"word": "அடுத்த", "occurrence": 1, "occurrences": 1}]
    (align_dir / "2.json").write_text(json.dumps(chapter_two, ensure_ascii=False), encoding="utf-8")
    sync = runtime.synchronize_alignment_state()
    assert sync["changed"] is True
    assert (sync["report"]["filesScanned"], sync["report"]["filesUnchanged"]) == (1, 1)
    assert runtime.migration_report()["quarantineByReason"]["LEGACY_EMPTY_BOTTOM_WORDS_AMBIGUOUS"] == 1,         "chapter 1 did not change, so its legacy issue is not recorded again"
    assert runtime.synchronize_alignment_state() == {"changed": False, "staled": 0}


def test_a_changed_chapter_with_a_legacy_issue_is_scanned_again(tmp_path: Path, stage4_project: Path) -> None:
    """Scanning per file must not skip a chapter whose content did change."""
    align_dir = stage4_project / ".apps" / "translationCore" / "alignmentData" / "rut"
    engine = _engine(tmp_path)
    _call(engine, "project.open", {"path": str(stage4_project)})
    runtime = engine.passage_semantic_runtime
    assert "LEGACY_EMPTY_BOTTOM_WORDS_AMBIGUOUS" not in runtime.migration_report()["quarantineByReason"]
    (align_dir / "2.json").write_text(json.dumps({"1": {"alignments": [{"topWords": [
        {"word": "ג", "occurrence": 1, "occurrences": 1},
        {"word": "ד", "occurrence": 1, "occurrences": 1},
    ], "bottomWords": []}], "wordBank": []}}, ensure_ascii=False), encoding="utf-8")
    (align_dir / "3.json").write_text("{not json", encoding="utf-8")
    report = runtime.synchronize_alignment_state()["report"]
    assert (report["filesScanned"], report["filesUnchanged"]) == (2, 1)
    reasons = runtime.migration_report()["quarantineByReason"]
    assert reasons["LEGACY_EMPTY_BOTTOM_WORDS_AMBIGUOUS"] == 1
    assert reasons["MALFORMED_LEGACY_ALIGNMENT_FILE"] == 1
    malformed = [r for r in runtime.repository.migration_quarantine_records()
                 if r["reasonCode"] == "MALFORMED_LEGACY_ALIGNMENT_FILE"]
    assert malformed[0]["payload"]["originalText"] == "{not json"


def test_the_digests_are_the_same_from_shared_bytes(stage4_project: Path) -> None:
    """The sync reads each file once and hands the bytes to both digests; the
    values must be exactly those Stage 6B's run fingerprint computes alone."""
    project = TranslationCoreProject(str(stage4_project))
    files = psr.alignment_files(project)
    assert [path.name for path, _ in files] == ["1.json", "2.json"]
    content = psr.alignment_directory_digest(project)
    assert psr.alignment_directory_digest(project, files) == content
    assert psr.alignment_state_digest(project, content_digest=content) == psr.alignment_state_digest(project)


@pytest.mark.parametrize(("book", "chapter", "verse", "mapped", "kind"), [
    ("RUT", "1", "1", {"mapping": "same", "orgRef": "RUT 1:1"}, "SAME"),
    ("RUT", "1", "1", {"mapping": "mapped", "orgRef": "RUT 1:2"}, "MAPPED"),
    ("RUT", "1", "1", {"mapping": "merge", "orgRef": "RUT 1:1"}, "MERGE"),
    ("RUT", "1", "1", {"mapping": "split", "splitInto": ["RUT 1:1", "RUT 1:2"]}, "SPLIT"),
    ("RUT", "1", "1", {"mapping": "mapped", "orgRef": "RUT 2:1"}, "CHAPTER_SHIFT"),
    ("PSA", "3", "1", {"mapping": "mapped", "orgRef": "PSA 3:0"}, "PSALM_TITLE"),
])
def test_versification_mapping_kinds_are_persistable(
    monkeypatch: pytest.MonkeyPatch,
    book: str,
    chapter: str,
    verse: str,
    mapped: dict,
    kind: str,
) -> None:
    monkeypatch.setattr(
        "tc_ai_bridge.passage_semantic_runtime.versification.to_org_ref",
        lambda *_args: mapped,
    )
    assert _canonical_reference(book, chapter, verse, "test")["mappingKind"] == kind


def test_verse_bridge_and_lettered_segment_are_deterministic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tc_ai_bridge.passage_semantic_runtime.versification.to_org_ref",
        lambda book, chapter, verse, _schema: {
            "mapping": "same", "orgRef": f"{book} {chapter}:{verse}",
        },
    )
    assert _canonical_reference("RUT", "1", "2-3", "test")["mappingKind"] == "VERSE_BRIDGE"
    assert _canonical_reference("RUT", "1", "4a", "test")["mappingKind"] == "AMBIGUOUS_SEGMENT"


# --- Verse text carrying its own structural markers -------------------------

def _write_marker_bearing_project(root: Path) -> Path:
    r"""A project shaped like real imported Scripture.

    translationCore chapter JSON stores trailing paragraph/section markers
    inside the verse string, e.g. ``...text\n\p`` or
    ``...text\n\s heading\n\p``. Every real imported Hindi/English project in
    this workspace looks like this; only synthetic fixtures have clean verse
    text, which is why this went unnoticed until Stage 9A.4 made scope
    resolution run automatically when Alignment Review opens.
    """
    align_dir = root / ".apps" / "translationCore" / "alignmentData" / "php"
    align_dir.mkdir(parents=True)
    (root / "php").mkdir(parents=True)
    (root / "manifest.json").write_text(json.dumps({
        "project": {"id": "php", "name": "Philippians"},
        "target_language": {"id": "hin", "name": "Hindi"},
        "resource": {"id": "irv", "name": "IRV"},
        "tc_version": "8",
    }), encoding="utf-8")
    (root / "php" / "1.json").write_text(json.dumps({
        "1": "पहला वचन,\n\\p",
        "2": "दूसरा वचन।\n\\s अनुभाग शीर्षक\n\\p",
        "3": "तीसरा वचन।",
    }, ensure_ascii=False), encoding="utf-8")
    (align_dir / "1.json").write_text(json.dumps({
        "1": {"alignments": [], "wordBank": []},
        "2": {"alignments": [], "wordBank": []},
        "3": {"alignments": [], "wordBank": []},
    }, ensure_ascii=False), encoding="utf-8")
    (root / "php.usfm").write_text(
        "\\id PHP\n\\c 1\n\\p\n\\v 1 पुराना आयातित पाठ।\n"
        "\\v 2 पुराना आयातित पाठ।\n\\v 3 पुराना आयातित पाठ।\n",
        encoding="utf-8",
    )
    return root


def test_verse_text_carrying_structural_markers_is_authoritative(tmp_path: Path) -> None:
    """Trailing \\p and \\s markers in verse JSON must not read as corruption.

    The parser correctly hoists them out of verse text. Comparing a parsed
    segment against the raw stored string therefore fails on good data - it
    blocked 16 of 26 real projects, and with them Stage 9A.4 analysis.
    """
    root = _write_marker_bearing_project(tmp_path / "marker-bearing")
    overlay = build_current_text_overlay(TranslationCoreProject(root))

    by_reference = {seg.reference: seg.text for seg in overlay.index.segments}
    assert by_reference["PHP 1:1"] == "पहला वचन,"
    assert by_reference["PHP 1:2"] == "दूसरा वचन।"
    assert by_reference["PHP 1:3"] == "तीसरा वचन।"
    # The markers become structure, and the old imported Scripture never returns.
    assert "पुराना आयातित पाठ" not in " ".join(by_reference.values())
    assert "अनुभाग शीर्षक" not in " ".join(by_reference.values())


def test_overlay_still_rejects_text_that_is_not_the_current_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard must keep its teeth after being taught about markers.

    Simulates the regression it exists for - the assembled overlay parsing to
    something other than the current authoritative text - and asserts it still
    refuses rather than silently publishing non-authoritative Scripture.
    """
    root = _write_marker_bearing_project(tmp_path / "guard-still-bites")
    real = psr._authoritative_current_segments

    def diverged(book: str, by_chapter: dict) -> dict:
        segments = dict(real(book, by_chapter))
        segments["PHP 1:2"] = "कुछ और ही पाठ"   # not what the overlay will produce
        return segments

    monkeypatch.setattr(psr, "_authoritative_current_segments", diverged)
    with pytest.raises(FoundationValidationError, match="non-authoritative"):
        build_current_text_overlay(TranslationCoreProject(root))


def test_the_scan_report_is_found_by_schema_even_when_it_is_not_the_last_run() -> None:
    """Pins the helper against the case that actually broke CI.

    `runs` is ordered by `started_at, id`, and both runs are written inside one
    scan call, so the ordering between them is not something a test should rely
    on. Here the invalidation-state run is deliberately last — reading `runs[-1]`
    would pick `{"staled": 3}` and raise `KeyError: 'quarantined'`, which is
    exactly the CI failure on f817033.
    """
    report = {
        "runs": [
            {"sourceSchema": _SCAN_SCHEMA, "report": {"quarantined": 120}},
            {"sourceSchema": "translationCore.alignmentData.invalidation-state.v1",
             "report": {"staled": 3}},
        ],
    }
    assert _scan_report(report) == {"quarantined": 120}
    assert report["runs"][-1]["report"] != _scan_report(report), (
        "if these were equal the test would pass with the old runs[-1] too"
    )


def test_a_report_with_no_scan_run_says_so_rather_than_raising_a_key_error() -> None:
    """The old failure mode was `KeyError: 'quarantined'`, which names neither the
    run that was read nor the ones available."""
    report = {"runs": [
        {"sourceSchema": "translationCore.alignmentData.invalidation-state.v1",
         "report": {"staled": 0}},
    ]}
    with pytest.raises(AssertionError, match="no compatibility-scan run"):
        _scan_report(report)
