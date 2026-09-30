"""
Tests for main.py's _apply_cli_overrides — parses the --resources-dir flag
Tauri's sidecar spawner (src-tauri/src/sidecar.rs) passes, so the frozen
bridge-engine.exe can find the bundled tN/tW/tA/UHB/UGNT snapshot at the
location Tauri's bundle.resources installed it to, instead of the
PyInstaller onefile archive it used to ship inside (see bridge-engine.spec's
own comment for why that changed).
"""
import os

import main


def test_apply_cli_overrides_sets_env_var_from_flag(monkeypatch):
    monkeypatch.delenv("BRIDGE_BUNDLED_RESOURCES_DIR", raising=False)
    monkeypatch.setattr("sys.argv", ["bridge-engine.exe", "--resources-dir", r"C:\Program Files\Bridge\resources"])

    try:
        main._apply_cli_overrides()
        assert os.environ["BRIDGE_BUNDLED_RESOURCES_DIR"] == r"C:\Program Files\Bridge\resources"
    finally:
        # _apply_cli_overrides mutates os.environ directly (that's the
        # whole point — it must survive past this call so later resource
        # resolution sees it), so monkeypatch's own auto-revert can't
        # intercept it. Left uncleaned, this leaked into every other test
        # module run afterward in the same pytest process, pointing every
        # bundled-resource lookup at a nonexistent path.
        os.environ.pop("BRIDGE_BUNDLED_RESOURCES_DIR", None)


def test_apply_cli_overrides_sets_the_language_packs_dir_from_its_flag(monkeypatch):
    monkeypatch.delenv("BRIDGE_BUNDLED_RESOURCES_DIR", raising=False)
    monkeypatch.delenv("BRIDGE_LANGUAGE_PACKS_DIR", raising=False)
    monkeypatch.setattr("sys.argv", ["bridge-engine.exe", "--resources-dir", r"C:\B\resources",
                                     "--language-packs-dir", r"C:\B\language_packs"])
    try:
        main._apply_cli_overrides()
        assert os.environ["BRIDGE_BUNDLED_RESOURCES_DIR"] == r"C:\B\resources"
        assert os.environ["BRIDGE_LANGUAGE_PACKS_DIR"] == r"C:\B\language_packs"
    finally:
        os.environ.pop("BRIDGE_BUNDLED_RESOURCES_DIR", None)
        os.environ.pop("BRIDGE_LANGUAGE_PACKS_DIR", None)


def test_apply_cli_overrides_leaves_env_var_unset_without_flag(monkeypatch):
    monkeypatch.delenv("BRIDGE_BUNDLED_RESOURCES_DIR", raising=False)
    monkeypatch.setattr("sys.argv", ["bridge-engine.exe"])

    main._apply_cli_overrides()

    assert "BRIDGE_BUNDLED_RESOURCES_DIR" not in os.environ


def test_apply_cli_overrides_ignores_flag_with_no_value(monkeypatch):
    """A malformed invocation (flag present but nothing after it) must not
    crash the sidecar at startup — degrade to unset, same as not passing
    the flag at all."""
    monkeypatch.delenv("BRIDGE_BUNDLED_RESOURCES_DIR", raising=False)
    monkeypatch.setattr("sys.argv", ["bridge-engine.exe", "--resources-dir"])

    main._apply_cli_overrides()

    assert "BRIDGE_BUNDLED_RESOURCES_DIR" not in os.environ
