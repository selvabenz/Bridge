"""
Entrypoint for the Bridge sidecar process.

Bundled via PyInstaller into a standalone executable (no Python runtime
required on the translator's machine). Tauri spawns this once at app
startup and keeps it alive for the whole session (doc §4).

    pyinstaller --onefile --name bridge-engine main.py
"""
import os
import sys

from bridge_service import BridgeEngine
from greek_room_engine.transport.stdio_transport import run_stdio_loop


def _apply_cli_overrides() -> None:
    """Tauri's sidecar spawner (src-tauri/src/sidecar.rs) passes
    --resources-dir pointing at the bundled tN/tW/tA/UHB/UGNT resources
    Tauri installs via bundle.resources. tc_ai_bridge's bundled_resources_
    source()/bundled_resources_root() functions check this env var before
    falling back to sys._MEIPASS or the source-tree layout — see their own
    docstrings for why this ~45MB payload moved out of bridge-engine.spec's
    onefile archive. Reading argv directly rather than pulling in argparse
    for one optional flag."""
    args = sys.argv[1:]
    # --language-packs-dir: the Language QA packs (engine/language_packs),
    # shipped the same way and for the same reason (docs/LANGUAGE_QA_PACKS.md).
    for flag, variable in (("--resources-dir", "BRIDGE_BUNDLED_RESOURCES_DIR"),
                           ("--language-packs-dir", "BRIDGE_LANGUAGE_PACKS_DIR")):
        if flag in args:
            index = args.index(flag)
            if index + 1 < len(args):
                os.environ[variable] = args[index + 1]


if __name__ == "__main__":
    _apply_cli_overrides()
    engine = BridgeEngine()
    run_stdio_loop(engine, on_ready=engine.start_background_warmup)
