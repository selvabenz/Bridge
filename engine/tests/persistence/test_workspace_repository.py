"""#77: the app-level workspace database gets a versioned schema ladder.

The first cut of ``workspace_repository.py`` created ``devices`` and ``users``
with ``CREATE TABLE IF NOT EXISTS`` and no version at all. Those two tables
hold the ids stamped on immutable workbench rows, so the ladder's v1 has to
*adopt* such a database rather than treat it as empty -- that is the one
migration property here a reader should not take on trust.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from tc_ai_bridge.workspace_repository import (
    WORKSPACE_SCHEMA_VERSION,
    WorkspaceError,
    WorkspaceRepository,
)


def _table_names(path) -> set[str]:
    conn = sqlite3.connect(str(path))
    try:
        return {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()


def test_a_fresh_workspace_is_built_by_running_the_whole_ladder(tmp_path):
    repo = WorkspaceRepository(tmp_path / "workspace.sqlite3")
    assert repo.schema_version() == WORKSPACE_SCHEMA_VERSION == 2
    names = _table_names(repo.path)
    for table in ("devices", "users", "projects", "settings_kv", "project_progress_cache", "schema_migrations"):
        assert table in names, table
    conn = sqlite3.connect(str(repo.path))
    try:
        versions = [row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
        assert versions == [1, 2], "every block is recorded, in order, even on a fresh database"
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    finally:
        conn.close()


def _build_unversioned_workspace(path) -> tuple[str, str]:
    """Exactly what the pre-#77 module wrote: the two tables, no ladder."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS devices("
            "device_id TEXT PRIMARY KEY, machine_id TEXT, os_user TEXT, created_at TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS users("
            "user_id TEXT PRIMARY KEY, display_name TEXT NOT NULL, created_at TEXT NOT NULL, "
            "updated_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1)"
        )
        conn.execute(
            "INSERT INTO devices(device_id, machine_id, os_user, created_at) VALUES(?,?,?,?)",
            ("device-legacy", None, None, "2026-09-14T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO users(user_id, display_name, created_at, updated_at, active) VALUES(?,?,?,?,1)",
            ("user-legacy", "Revant", "2026-09-14T00:00:00+00:00", "2026-09-14T00:00:00+00:00"),
        )
        conn.commit()
    finally:
        conn.close()
    return "device-legacy", "user-legacy"


def test_an_unversioned_pre_77_workspace_is_adopted_and_keeps_its_ids(tmp_path):
    """The device and user ids in that database are already stamped on
    append-only workbench rows. Re-minting them would detach every one of
    those rows from the installation that wrote them."""
    path = tmp_path / "workspace.sqlite3"
    device_id, user_id = _build_unversioned_workspace(path)

    repo = WorkspaceRepository(path)

    assert repo.schema_version() == WORKSPACE_SCHEMA_VERSION
    assert repo.get_or_create_device_id() == device_id
    assert repo.get_or_create_local_user("Revant")["userId"] == user_id
    assert "project_progress_cache" in _table_names(path)


def test_upgrading_an_existing_workspace_leaves_an_inspectable_backup(tmp_path):
    path = tmp_path / "workspace.sqlite3"
    _build_unversioned_workspace(path)

    WorkspaceRepository(path)

    backups = sorted((tmp_path / "backups").glob("pre-workspace-v*"))
    # v1 adopts, v2 adds tables: one backup per block applied to a database
    # that already held something.
    assert [b.name.split("-")[2] for b in backups] == ["v1", "v2"]
    manifest = json.loads((backups[-1] / "backup-manifest.json").read_text(encoding="utf-8"))
    assert manifest["targetSchemaVersion"] == 2
    assert manifest["schemaVersion"] == 1
    copied = backups[-1] / "workspace.sqlite3"
    conn = sqlite3.connect(str(copied))
    try:
        assert conn.execute("SELECT device_id FROM devices").fetchone()[0] == "device-legacy"
    finally:
        conn.close()


def test_a_fresh_workspace_takes_no_backups(tmp_path):
    WorkspaceRepository(tmp_path / "workspace.sqlite3")
    assert not (tmp_path / "backups").exists()


def test_the_repository_refuses_a_newer_schema_rather_than_downgrading(tmp_path):
    path = tmp_path / "workspace.sqlite3"
    WorkspaceRepository(path)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "INSERT INTO schema_migrations(version, schema_id, applied_at) VALUES(?,?,?)",
            (WORKSPACE_SCHEMA_VERSION + 1, "bridge-workspace", "2099-01-01T00:00:00+00:00"),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(WorkspaceError):
        WorkspaceRepository(path)


def test_reopening_is_idempotent(tmp_path):
    path = tmp_path / "workspace.sqlite3"
    first = WorkspaceRepository(path).get_or_create_device_id()
    second = WorkspaceRepository(path).get_or_create_device_id()
    assert first == second
    assert WorkspaceRepository(path).schema_version() == WORKSPACE_SCHEMA_VERSION


def test_the_engine_owns_one_workspace_beside_its_settings_and_projects_write_through_it(tmp_path):
    """Before #77 each project reached for the installation default itself.
    The progress cache needs the engine and its projects to agree on one
    database, so the engine constructs it and injects it."""
    from bridge_service import BridgeEngine
    from tc_ai_bridge.secret_store import AppSettings
    from tests.support.workbench import _build_minimal_project

    settings_root = tmp_path / "app"
    settings_root.mkdir()
    engine = BridgeEngine(settings=AppSettings(path=settings_root / "settings.json"))
    assert engine.workspace.path == settings_root / "workspace.sqlite3"

    root = _build_minimal_project(tmp_path / "rut")
    (root / "rut" / "1.json").write_text(json.dumps({"1": "text"}), encoding="utf-8")
    engine.open_project(str(root))
    engine.project.record_qa_decision("1", "1", issue_key="f1", decision="accepted")

    stamped = engine.project.workbench_identity
    assert stamped.device_id == engine.workspace.get_or_create_device_id()
    assert stamped.actor_id == engine.workspace.get_or_create_local_user("whoever")["userId"]


def test_the_users_row_follows_the_name_chosen_in_settings_not_the_os_account(tmp_path):
    """#78: `identity` passed `_seed_reviewer_name()` (the OS account)
    unconditionally, so `users.display_name` was always the OS account and a
    Settings rename never reached it. `reviewer_name` and `users.display_name`
    were two competing answers to "who is this person".
    """
    from bridge_service import BridgeEngine
    from tc_ai_bridge.secret_store import AppSettings
    from tests.support.workbench import _build_minimal_project

    settings_root = tmp_path / "app"
    settings_root.mkdir()
    settings = AppSettings(path=settings_root / "settings.json")
    settings.reviewer_name = "Benz"
    engine = BridgeEngine(settings=settings)

    root = _build_minimal_project(tmp_path / "rut")
    (root / "rut" / "1.json").write_text(json.dumps({"1": "text"}), encoding="utf-8")
    engine.open_project(str(root))
    engine.project.record_qa_decision("1", "1", issue_key="f1", decision="accepted")

    user = engine.local_user()
    assert user["displayName"] == "Benz"
    assert engine.project.workbench_identity.actor_id == user["userId"]


def test_renaming_yourself_keeps_the_id_so_past_rows_are_relabelled_not_orphaned(tmp_path):
    """The whole point of an id separate from a name. Writes used to record
    `settings.reviewer_name`, so a rename left old rows under the old name and
    new rows under the new one -- one person showing up as two actors."""
    from bridge_service import BridgeEngine
    from tc_ai_bridge.secret_store import AppSettings

    settings_root = tmp_path / "app"
    settings_root.mkdir()
    settings = AppSettings(path=settings_root / "settings.json")
    settings.reviewer_name = "Benz"
    engine = BridgeEngine(settings=settings)

    before = engine.local_user()
    assert before["displayName"] == "Benz"

    engine.set_settings(reviewerName="R. Idikulay")
    after = engine.local_user()

    assert after["userId"] == before["userId"], "a rename must not mint a new identity"
    assert after["displayName"] == "R. Idikulay"
    # And it is visible to the UI, which stamps this rather than the name.
    assert engine.get_settings()["localUserId"] == before["userId"]


def test_a_write_records_the_stable_id_rather_than_whatever_name_is_current(tmp_path):
    from bridge_service import BridgeEngine
    from tc_ai_bridge.secret_store import AppSettings

    settings_root = tmp_path / "app"
    settings_root.mkdir()
    settings = AppSettings(path=settings_root / "settings.json")
    settings.reviewer_name = "Benz"
    engine = BridgeEngine(settings=settings)

    actor = engine.current_actor_id()
    assert actor and actor != "Benz", "the display name must never be the actor id"
    assert actor != "human", "a real user id, not the legacy placeholder"
