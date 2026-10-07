"""#77: the progress rollup lives in the workbench, the dashboard reads a cache.

`.bridge/progress.json` became three workbench tables plus one row per
project in the app-level `project_progress_cache`. The correctness question
is cache coherency, not placement: the dashboard must show the right totals
for a sibling that was opened in some earlier session and never this one,
and a project open must notice a cache entry that lags or that describes a
project that used to live at the same path.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bridge_service import BridgeEngine
from greek_room_engine.protocol import EngineRequest
from tc_ai_bridge.secret_store import AppSettings
from tc_ai_bridge.tc_project import ProjectError, TranslationCoreProject, peek_progress_totals
from tests.support.workbench import _build_minimal_project


def call(engine, method, params=None):
    response = engine.handle_request(EngineRequest(id="1", method=method, params=params or {}))
    payload = response.to_dict()
    assert payload["success"], payload.get("error")
    return payload["result"]


def _book(root: Path, book_id: str, name: str) -> Path:
    project = _build_minimal_project(root)
    manifest = json.loads((project / "manifest.json").read_text(encoding="utf-8"))
    manifest["project"] = {"id": book_id, "name": name}
    (project / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    if book_id != "rut":
        (project / ".apps" / "translationCore" / "alignmentData" / "rut").rename(
            project / ".apps" / "translationCore" / "alignmentData" / book_id,
        )
        (project / "rut").rename(project / book_id)
    (project / book_id / "1.json").write_text(json.dumps({"1": "verse one"}), encoding="utf-8")
    return project


@pytest.fixture
def engine(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    return BridgeEngine(settings=AppSettings(path=app / "settings.json"))


def _decide(engine, finding_id="f-1", status="accepted"):
    call(engine, "verse.decide", {
        "chapter": "1", "verse": "1", "findingId": finding_id, "status": status, "comment": "",
    })


def test_a_decision_writes_the_totals_row_and_refreshes_the_workspace_cache(tmp_path, engine):
    rut = _book(tmp_path / "rut", "rut", "Ruth")
    call(engine, "project.open", {"path": str(rut)})
    _decide(engine)

    rollup = engine.project.load_progress_rollup()
    assert rollup["totals"]["findingCount"] == 1
    assert rollup["chapters"]["1"]["verses"]["1"]["findings"]["f-1"] == "accepted"

    entry = engine.workspace.progress_cache_entry(rut)
    assert entry is not None
    assert entry["totals"] == rollup["totals"]
    assert entry["updatedAt"] == rollup["updatedAt"]
    assert entry["bookId"] == "rut"
    assert entry["projectId"] == engine.project.workbench_identity.project_id
    assert entry["sourceSeq"] == engine.project.workbench.max_seq(
        project_id=entry["projectId"], table="progress_totals",
    )


def test_a_decision_is_one_workbench_commit_per_table_not_one_per_row(tmp_path, engine):
    """A first decision in a chapter creates the chapter row and the finding
    row together; the totals follow as their own write. Three change_log rows,
    and the chapter and finding rows share a commit."""
    rut = _book(tmp_path / "rut", "rut", "Ruth")
    call(engine, "project.open", {"path": str(rut)})
    before = len(engine.project.workbench.change_log_entries(engine.project.workbench_identity.project_id))
    _decide(engine)
    entries = engine.project.workbench.change_log_entries(engine.project.workbench_identity.project_id)
    new = [e["table_name"] for e in entries[before:]]
    assert new == ["human_decisions", "progress_chapters", "progress_findings", "progress_totals"]


def _collection(tmp_path: Path, primary: Path, members: list[tuple[str, str, str]]) -> None:
    (primary / ".bridge").mkdir(parents=True, exist_ok=True)
    (primary / ".bridge" / "collection.json").write_text(json.dumps({
        "schemaVersion": 2,
        "projects": [
            {"directoryName": directory, "bookId": book_id.upper(), "bookName": name}
            for directory, book_id, name in members
        ],
    }), encoding="utf-8")


def test_dashboard_mixes_lazy_opened_and_missing_siblings_without_opening_any(tmp_path, engine, monkeypatch):
    """The 66-book case in miniature: the current book, a sibling opened in an
    earlier session (cache hit), a lazy stub, and a folder that is gone.
    None of the siblings may be constructed or have its workbench opened."""
    rut = _book(tmp_path / "rut", "rut", "Ruth")
    exo = _book(tmp_path / "exo", "exo", "Exodus")
    gen = tmp_path / "gen"
    (gen / ".bridge").mkdir(parents=True)
    (gen / ".bridge" / "lazy-import.json").write_text(json.dumps({"bookId": "GEN", "bookName": "Genesis"}), encoding="utf-8")
    _collection(tmp_path, rut, [("rut", "rut", "Ruth"), ("exo", "exo", "Exodus"), ("gen", "gen", "Genesis"), ("lev", "lev", "Leviticus")])

    # An earlier session: Exodus was opened and reviewed.
    call(engine, "project.open", {"path": str(exo)})
    _decide(engine, "exo-1")
    _decide(engine, "exo-2", "rejected")
    exo_totals = engine.project.load_progress_rollup()["totals"]

    # This session: Ruth is open, nothing else has been touched.
    call(engine, "project.open", {"path": str(rut)})
    _decide(engine)

    import bridge_service
    import tc_ai_bridge.tc_project as tc_project_module

    def refuse_sibling(self, path, *args, **kwargs):
        if Path(path).resolve() != rut.resolve():
            raise AssertionError(f"the dashboard must not open a sibling's workbench: {path}")
        return original_workbench_init(self, path, *args, **kwargs)

    original_workbench_init = tc_project_module.WorkbenchRepository.__init__
    monkeypatch.setattr(tc_project_module.WorkbenchRepository, "__init__", refuse_sibling)
    monkeypatch.setattr(bridge_service, "TranslationCoreProject", None)  # any construction is a NameError-shaped failure

    books = {b["bookId"]: b for b in call(engine, "project.listBookProgress")["books"]}

    assert books["RUT"]["progress"]["findingCount"] == 1
    assert books["EXO"]["progress"] == {**exo_totals, "updatedAt": books["EXO"]["progress"]["updatedAt"]}
    assert books["EXO"]["progress"]["findingCount"] == 2
    assert books["GEN"] == {**books["GEN"], "lazy": True, "missing": False, "progress": None}
    assert books["LEV"]["missing"] is True and books["LEV"]["progress"] is None
    assert not (gen / "manifest.json").exists()


def test_a_missing_cache_entry_is_rebuilt_from_a_read_only_peek(tmp_path, engine):
    """The workspace database was reset, or the folder came from another
    machine: the dashboard peeks the sibling's workbench read-only once,
    writes the entry back, and the next call is one query again."""
    rut = _book(tmp_path / "rut", "rut", "Ruth")
    exo = _book(tmp_path / "exo", "exo", "Exodus")
    _collection(tmp_path, rut, [("rut", "rut", "Ruth"), ("exo", "exo", "Exodus")])
    call(engine, "project.open", {"path": str(exo)})
    _decide(engine, "exo-1")
    call(engine, "project.open", {"path": str(rut)})

    assert engine.workspace.forget_progress_cache([exo]) == 1
    assert engine.workspace.progress_cache_entry(exo) is None
    workbench = exo / ".apps" / "translationCoreAI" / "bridge-workbench.sqlite3"
    size_before = workbench.stat().st_size

    books = {b["bookId"]: b for b in call(engine, "project.listBookProgress")["books"]}

    assert books["EXO"]["progress"]["findingCount"] == 1
    restored = engine.workspace.progress_cache_entry(exo)
    assert restored is not None and restored["totals"]["findingCount"] == 1
    assert workbench.stat().st_size == size_before, "a peek must not write to the sibling's database"


def test_peek_returns_none_for_a_folder_with_no_workbench_and_never_creates_one(tmp_path):
    lazy = tmp_path / "gen"
    (lazy / ".bridge").mkdir(parents=True)
    assert peek_progress_totals(lazy) is None
    assert not (lazy / ".apps").exists()


def test_open_repairs_a_cache_entry_that_lags_the_workbench(tmp_path, engine):
    """A crash between the workbench commit and the cache write leaves the
    cache one step behind. `source_seq` is how the next open notices."""
    rut = _book(tmp_path / "rut", "rut", "Ruth")
    opened = call(engine, "project.open", {"path": str(rut)})
    assert opened["progressCache"] == {"state": "fresh"}
    _decide(engine)
    good = engine.workspace.progress_cache_entry(rut)

    engine.workspace.upsert_progress_cache(
        rut, project_id=good["projectId"], book_id="rut",
        totals={"findingCount": 999}, updated_at=None, source_seq=good["sourceSeq"] - 1,
    )

    reopened = call(engine, "project.open", {"path": str(rut)})
    assert reopened["progressCache"] == {"state": "repaired"}
    assert engine.workspace.progress_cache_entry(rut)["totals"] == good["totals"]
    assert call(engine, "project.open", {"path": str(rut)})["progressCache"] == {"state": "fresh"}


def test_open_clears_a_cache_entry_left_by_whatever_used_to_live_at_that_path(tmp_path, engine):
    """A re-import at the same folder starts a fresh workbench whose seq
    restarts at 1. Matching seq alone would trust the old entry; the entry
    must also name this project id, and with no totals row it goes away."""
    rut = _book(tmp_path / "rut", "rut", "Ruth")
    engine.workspace.upsert_progress_cache(
        rut, project_id="some-earlier-import", book_id="rut",
        totals={"findingCount": 5}, updated_at=None, source_seq=0,
    )
    opened = call(engine, "project.open", {"path": str(rut)})
    assert opened["progressCache"] == {"state": "cleared"}
    assert engine.workspace.progress_cache_entry(rut) is None


def test_deleting_a_project_drops_its_cache_entries(tmp_path, engine):
    rut = _book(tmp_path / "rut", "rut", "Ruth")
    opened = call(engine, "project.open", {"path": str(rut)})
    _decide(engine)
    assert engine.workspace.progress_cache_entry(rut) is not None
    call(engine, "project.forget", {"projectId": opened["projectId"]})
    assert engine.workspace.progress_cache_entry(rut) is None


def test_a_check_job_replaces_a_chapters_findings_and_keeps_clean_verses(tmp_path, engine):
    """Replacing a chapter drops finding rows the new run did not report
    (as a `delete` change_log event, not a silent DELETE) and keeps a verse
    that was checked and came back clean as a PASS entry."""
    rut = _book(tmp_path / "rut", "rut", "Ruth")
    call(engine, "project.open", {"path": str(rut)})
    project: TranslationCoreProject = engine.project
    project.replace_progress_chapters({"1": {
        "verseCount": 2, "aiChecked": True, "aiCheckedAt": "t1",
        "verses": {"1": {"findings": {"a": "open", "b": "open"}}, "2": {"findings": {}}},
    }})
    project.replace_progress_chapters({"1": {
        "verseCount": 2, "aiChecked": True, "aiCheckedAt": "t2",
        "verses": {"1": {"findings": {"a": "accepted"}}, "2": {"findings": {}}},
    }})
    rollup = project.load_progress_rollup()
    assert rollup["chapters"]["1"]["verses"] == {"1": {"findings": {"a": "accepted"}}, "2": {"findings": {}}}
    assert rollup["chapters"]["1"]["aiCheckedAt"] == "t2"
    deletes = [
        e for e in project.workbench.change_log_entries(project.workbench_identity.project_id)
        if e["op"] == "delete"
    ]
    assert len(deletes) == 1 and deletes[0]["table_name"] == "progress_findings"
    assert json.loads(deletes[0]["payload_json"])["findingId"] == "b"


def test_a_project_carrying_a_progress_json_refuses_to_open(tmp_path):
    """The rollup moved with #77. A project that still has the file was
    written by a build whose review progress this one would silently show as
    zero -- the same silent-empty failure the #76 guard exists for."""
    root = _build_minimal_project(tmp_path / "rut")
    (root / ".bridge").mkdir()
    (root / ".bridge" / "progress.json").write_text(json.dumps({"totals": {"findingCount": 3}}), encoding="utf-8")
    with pytest.raises(ProjectError) as raised:
        TranslationCoreProject(root)
    assert ".bridge/progress.json" in str(raised.value)
    assert (root / ".bridge" / "progress.json").is_file()
