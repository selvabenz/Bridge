"""The one Bridge release version the engine reports and stamps.

Before #176 this number lived as separate literals in `bridge_service.py` (the
constant `engine.info` reports) and in `project_import.py` (the `generator.build`
string written into every imported project's `manifest.json`). The 0.12.0 release
bumped neither, so the sidecar reported 0.11.0 (#170) and every project imported
by a 0.12.0 build claimed on disk to have been made by 0.11.0.

It lives here rather than in `bridge_service.py` because `tc_ai_bridge` modules
cannot import that without a cycle: `bridge_service` imports them.

`package.json` remains the release source of truth. This constant is checked
against it by `engine/tests/service/test_version_consistency.py`, which cannot be
replaced by reading `package.json` at runtime -- a frozen PyInstaller sidecar
does not ship it.
"""
from __future__ import annotations

BRIDGE_VERSION = "0.14.0"

__all__ = ["BRIDGE_VERSION"]
