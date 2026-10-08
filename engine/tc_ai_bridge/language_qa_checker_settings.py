"""languageQa.checkerSettings.get / .set: the indic-qa checker's own settings
(the web app's Settings dialog), per collection (DECISIONS 2026-10-08).

They live in each book's per-pack overrides file
(`.apps/translationCoreAI/language-packs/<pack>/overrides.json`, loader.
overrides_path): a sparse `checker` object (language_packs/indic_qa_settings.py)
and the dialog's rule switches as `rules.<id>.enabled`. A save writes the same
values into every materialized book of the collection, keeping each file's
other keys (a rule's abstains), the way a project-scope house-style entry is
written. A lazy book picks them up once it is opened and saved again; until
then it runs the defaults, as it does for house style.

Only rules of the pack's indic-qa checker are switched here: the profile's
RULES (pa, ml, hi, or) or the Tamil layer's (indic_qa_tamil.RULES). A switch
may turn such a rule back on (loader.apply_overrides); it never draws it
inline, which stays the reviewed flag.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable

from .language_packs import indic_qa_settings as checker_settings
from .language_packs import indic_qa_tamil, indic_qa_vendor
from .language_packs.indic_qa_adapter import indic_config, with_resident
from .language_packs.loader import RulePack, default_pack, load_project_overrides, overrides_path

SCHEMA_VERSION = 1


class CheckerSettingsError(ValueError):
    """A request the dialog should not have sent: the message names the key."""


def catalogue(pack: RulePack) -> dict[str, tuple[str, bool]]:
    """rule id -> (label, on by default) for the pack's indic-qa checker."""
    if checker_settings.is_layer(pack):
        return {rid: (label, on) for rid, (_group, label, _inline, on) in indic_qa_tamil.RULES.items()}
    profile = indic_qa_vendor.profile(str((indic_config(pack.meta) or {}).get("profile") or ""))
    return {rid: (label, on) for rid, (_group, label, _inline, on) in profile.RULES.items()}


def _legend(pack: RulePack) -> str:
    lang = checker_settings.language(pack)
    return f"{getattr(lang, 'name', pack.language)} checks"


def describe(pack: RulePack, *, counts: dict[str, int], books: list[str]) -> dict[str, Any]:
    """The dialog's model: every section the pack's checker offers, its current
    value, the labels to show, and the books a save is written to."""
    lang = checker_settings.language(pack)
    labels = getattr(lang, "labels", {}) or {}
    current = checker_settings.merged(pack)
    locked = checker_settings.forced(pack).get("warnings", {})
    warn_labels = labels.get("settings_warn") or {}
    warnings = {k: bool(v) for k, v in current.get("warnings", {}).items() if k not in locked}
    available = checker_settings.contexts(pack)
    out: dict[str, Any] = {
        "pack": pack.name, "language": getattr(lang, "name", pack.language),
        "layer": checker_settings.is_layer(pack), "ready": True, "books": books,
        "contexts": {"checkable": list(available),
                     "checked": [c for c in available if c in current.get("checked_contexts", available)],
                     "labels": {c: checker_settings.CONTEXT_LABELS[c] for c in available}},
        "warnings": {"values": warnings,
                     "labels": {k: str(warn_labels.get(k) or k.replace("_", " ")) for k in warnings}},
        "suggest": {"max": int(current["suggest"]["max"]), "limit": checker_settings.MAX_SUGGEST},
        "compound": {"enabled": bool(current["compound"]["enabled"])},
        "rules": [{"id": rid, "label": label, "default": on,
                   "enabled": bool(rule.enabled) if (rule := pack.by_id(rid)) is not None else on,
                   "count": int(counts.get(rid, 0))}
                  for rid, (label, on) in catalogue(pack).items()],
        "legend": _legend(pack),
    }
    if checker_settings.is_layer(pack):
        sandhi = current.get("sandhi", {})
        out["sandhi"] = {"values": {k: sandhi.get(k) for k in checker_settings.SANDHI_LABELS},
                         "labels": dict(checker_settings.SANDHI_LABELS)}
        return out
    out["numbers"] = [{"group": group, "key": key, "label": label, "min": low,
                       "default": checker_settings.defaults(pack)[group][key], "value": current[group][key]}
                      for group, key, label, low in checker_settings.NUMBERS]
    style = with_resident(pack.name, lambda checker, profile: profile.style_toggles(checker))
    if style is None:
        # The style choices are the checker's own spelling clusters, which exist
        # once a pass has loaded the pack: say so rather than show none.
        out["ready"] = False
        out["style"] = []
    else:
        chosen = current.get("style", {})
        out["style"] = [{"id": t["id"], "label": t["label"], "close": bool(t.get("close")),
                         "options": [{"value": o["value"], "label": o["label"]} for o in t["options"]],
                         "value": str(chosen.get(t["id"], t.get("default", "auto")))} for t in style]
    return out


def updated(pack: RulePack, base: RulePack, patch: dict[str, Any]) -> tuple[dict[str, Any], dict[str, bool]]:
    """(sparse checker settings, rule id -> enabled) after `patch`, over the
    project's current values. Raises CheckerSettingsError for anything the
    checker would refuse, so nothing half-valid is written."""
    if not isinstance(patch, dict):
        raise CheckerSettingsError("expected {checker?, rules?}")
    unknown = sorted(set(patch) - {"checker", "rules"})
    if unknown:
        raise CheckerSettingsError(f"unknown parameter(s) {unknown}")
    raw = dict(pack.checker_settings)
    sections = patch.get("checker") or {}
    if not isinstance(sections, dict):
        raise CheckerSettingsError("checker must be an object of sections")
    for section, value in sections.items():
        if isinstance(value, dict) and isinstance(raw.get(section), dict):
            raw[section] = {**raw[section], **value}
        else:
            raw[section] = value
    clean, problems = checker_settings.validate(raw, pack)
    if problems:
        raise CheckerSettingsError("; ".join(problems))
    known = catalogue(pack)
    enabled = {rid: (rule.enabled if (rule := pack.by_id(rid)) is not None else on)
               for rid, (_label, on) in known.items()}
    switches = patch.get("rules") or {}
    if not isinstance(switches, dict):
        raise CheckerSettingsError("rules must be an object of rule id -> {enabled}")
    for rid, change in switches.items():
        if rid not in known:
            raise CheckerSettingsError(f"{rid} is not a rule of this checker")
        if not isinstance(change, dict) or not isinstance(change.get("enabled"), bool) or set(change) != {"enabled"}:
            raise CheckerSettingsError(f"rules.{rid} must be {{\"enabled\": true|false}}")
        enabled[rid] = change["enabled"]
    return clean, enabled


def _written(document: dict[str, Any], base: RulePack, clean: dict[str, Any],
             enabled: dict[str, bool]) -> dict[str, Any]:
    """One book's overrides with the collection's checker values: the checker
    object replaced, each switched rule's `enabled` set only where it differs
    from the bundled pack, and every other key of the file kept."""
    out = {k: v for k, v in document.items() if k not in {"checker", "rules"}}
    if clean:
        out["checker"] = {"version": SCHEMA_VERSION, **clean}
    rules = {rid: dict(entry) for rid, entry in (document.get("rules") or {}).items() if isinstance(entry, dict)}
    for rid, on in enabled.items():
        entry = rules.get(rid, {})
        rule = base.by_id(rid)
        if rule is not None and on == rule.enabled:
            entry.pop("enabled", None)
        else:
            entry["enabled"] = on
        if entry:
            rules[rid] = entry
        else:
            rules.pop(rid, None)
    if rules:
        out["rules"] = rules
    return out


def _write_atomic(path: Path, document: dict[str, Any]) -> None:
    if not document:
        if path.exists():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=path.parent, prefix=".overrides-", suffix=".json")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=False, indent=1, sort_keys=True)
            stream.write("\n")
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def save(pack: RulePack, patch: dict[str, Any], book_paths: Iterable[Path]) -> list[str]:
    """Validate `patch` against the open book's pack and write it into every
    book's overrides file. Returns the folders written, by name."""
    base = default_pack(pack.name)
    clean, enabled = updated(pack, base, patch)
    written: list[str] = []
    for root in book_paths:
        document, problems = load_project_overrides(root, pack.name)
        if problems:
            raise CheckerSettingsError(f"{Path(root).name}: {problems[0]}")
        _write_atomic(overrides_path(root, pack.name), _written(document or {}, base, clean, enabled))
        written.append(Path(root).name)
    return written


def rule_counts(findings: Iterable[dict[str, Any]], rule_ids: Callable[[], Iterable[str]]) -> dict[str, int]:
    """How many findings of the last pass each of the dialog's rules raised."""
    wanted = set(rule_ids())
    counts: dict[str, int] = {}
    for finding in findings:
        rule = str(finding.get("rule") or "")
        if rule in wanted:
            counts[rule] = counts.get(rule, 0) + 1
    return counts
