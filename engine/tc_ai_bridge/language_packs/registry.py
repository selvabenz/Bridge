"""Where the language packs live, and which one a language gets.

Packs are data outside the engine executable (docs/LANGUAGE_QA_PACKS.md):
`index.json` plus one folder per pack. A frozen build is told where Tauri
installed them (`--language-packs-dir`, which main.py puts in
BRIDGE_LANGUAGE_PACKS_DIR); a source checkout reads engine/language_packs.
Resolved on every call, never at import: main.py sets the variable after
bridge_service has been imported.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ENV = "BRIDGE_LANGUAGE_PACKS_DIR"
SOURCE_DIR = Path(__file__).resolve().parents[2] / "language_packs"
COMMON = "common"
# The project setting (manifest `language_qa.pack`): "auto", "off", or a pack name.
AUTO, OFF = "auto", "off"


def packs_dir() -> Path:
    override = os.environ.get(ENV, "").strip()
    if override:
        return Path(override)
    return SOURCE_DIR


_INDEX: dict[Path, dict[str, Any]] = {}


def index() -> dict[str, Any]:
    """index.json, read once per packs directory. A missing or unreadable
    index is an empty registry: every language runs the common checks."""
    root = packs_dir()
    if root not in _INDEX:
        problem = ""
        try:
            data = json.loads((root / "index.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            data, problem = {}, f"Language packs unavailable ({root / 'index.json'}: {exc}); common checks only."
        languages = data.get("languages") if isinstance(data.get("languages"), dict) else {}
        aliases = data.get("aliases") if isinstance(data.get("aliases"), dict) else {}
        _INDEX[root] = {"languages": languages, "aliases": aliases, "problem": problem}
    return _INDEX[root]


def problem() -> str:
    """Why no pack can be chosen at all (a missing or unreadable index), or ""."""
    return str(index().get("problem") or "")


index.cache_clear = _INDEX.clear  # type: ignore[attr-defined]


def language_code(code: str) -> str:
    """A declared language (BCP-47-ish, any case) reduced to the registry's
    key: the primary subtag, with ISO 639-2/3 aliases folded (tam -> ta)."""
    primary = code.strip().lower().replace("_", "-").split("-")[0]
    return str(index()["aliases"].get(primary, primary))


def select_pack(code: str) -> str | None:
    """The pack registered for a language, or None (common checks only)."""
    entry = index()["languages"].get(language_code(code)) if code else None
    return str(entry["pack"]) if isinstance(entry, dict) and entry.get("pack") else None


def language_of_pack(pack: str) -> str | None:
    """The registry language whose pack this is."""
    for code, entry in index()["languages"].items():
        if isinstance(entry, dict) and entry.get("pack") == pack:
            return str(code)
    return None


def language_name(code: str) -> str:
    entry = index()["languages"].get(language_code(code))
    return str(entry.get("name") or code) if isinstance(entry, dict) else code


def available() -> list[dict[str, str]]:
    """Every registered pack, for the settings choice: [{language, name, pack}]."""
    return [{"language": str(code), "name": str(entry.get("name") or code), "pack": str(entry["pack"])}
            for code, entry in sorted(index()["languages"].items())
            if isinstance(entry, dict) and entry.get("pack")]


def pack_setting(manifest: Any) -> str:
    """The project's Language QA setting: "auto" (the default), "off", or a
    registered pack name. Anything else reads as "auto"; it is never guessed
    into a pack."""
    block = manifest.get("language_qa") if isinstance(manifest, dict) else None
    value = str(block.get("pack") or AUTO).strip() if isinstance(block, dict) else AUTO
    if value in (AUTO, OFF) or value in {p["pack"] for p in available()}:
        return value
    return AUTO
