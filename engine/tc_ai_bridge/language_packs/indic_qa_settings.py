"""The indic-qa checker's own settings for one project (the web app's Settings
dialog), kept per collection in the pack's overrides file (DECISIONS
2026-10-08, "checker settings are per collection").

`.apps/translationCoreAI/language-packs/<pack>/overrides.json` holds, beside
the narrowing `rules`, a sparse `checker` object: only the values that differ
from the language profile's `default_settings`. `validate` keeps what this
pack's checker understands and lists the rest as problems; `merged` is what the
checker runs with. Nothing here writes, and nothing here reads a dictionary.

What can be set is what the web app offers, less what Bridge cannot honour:

- `checked_contexts`: only the text Bridge gives the checker. A profile pack
  (pa, ml, hi, or) reads verse text; the Tamil layer also reads headings and
  footnote text (indic_qa_adapter.build_book).
- `warnings`, `suggest.max` (1 to MAX_SUGGESTIONS), `compound.enabled`.
- `lex` and `consistency` numbers, and `style` choices (profile packs).
- `sandhi` (the Tamil layer).
The layer's forced switches (indic_qa_tamil.SETTINGS) are not settable: the
2026-09-28 review rejected those three warnings.
"""
from __future__ import annotations

import copy
from typing import Any

from . import indic_qa_tamil
from .indic_qa_adapter import indic_config
from .loader import RulePack

SECTIONS = ("checked_contexts", "warnings", "suggest", "compound", "lex", "consistency", "style", "sandhi")
PROFILE_CONTEXTS = ("verse",)
LAYER_CONTEXTS = ("verse", "heading", "footnote_text")
CONTEXT_LABELS = {"verse": "verse text", "heading": "section headings (\\s)",
                  "footnote_text": "footnote text (\\ft)"}
# The keys of these sections the web app's dialog offers (index.html,
# settingsExtra numbers); the rest are the checker's internals. `warnings`
# offers every key of the profile's defaults except the layer's forced ones.
SANDHI_LABELS = {
    "min_total": "Minimum total occurrences",
    "minority_max": "Minority at most",
    "majority_min": "Majority at least",
    "include_weak": "Include weak leads (80% rule)",
    "report_wrong_class": "Report a wrong-class joining letter",
    "use_suffix": "For rare words, use the OV habit of the word's ending",
    "suffix_pct": "Ending must agree at least (share, 0.5 to 1)",
    "use_grammar": "Use the grammar rules",
    "show_conflicts": "Show leads where the OV, the grammar or the IRV disagree",
    "dangling": "Report a joining letter with nothing to join (before a vowel, punctuation or the end)",
    "dangling_digits": "… also before a number in digits that is not read with the same letter",
}
NUMBERS = (
    ("lex", "irv_accept_min", "Accept a word used at least this often in the IRV", 1),
    ("consistency", "minority_max", "Consistency: flag a form below this share (0–1)", 0),
)
OFFERED_KEYS = {"suggest": ("max",), "compound": ("enabled",), "sandhi": tuple(SANDHI_LABELS),
                "lex": ("irv_accept_min",), "consistency": ("minority_max",)}
MAX_SUGGEST = 9
MAX_STYLE_KEY = 200


def is_layer(pack: RulePack) -> bool:
    config = indic_config(pack.meta)
    return bool(config and config["layer"])


def contexts(pack: RulePack) -> tuple[str, ...]:
    return LAYER_CONTEXTS if is_layer(pack) else PROFILE_CONTEXTS


def language(pack: RulePack) -> Any:
    """The vendored language profile (qa_app.langs) the pack's checker runs."""
    from . import indic_qa_vendor
    config = indic_config(pack.meta) or {}
    return indic_qa_vendor.modules().langs.get(str(config.get("profile") or ""))


def defaults(pack: RulePack) -> dict[str, Any]:
    """The language profile's default settings, as the checker merges them."""
    return copy.deepcopy(language(pack).default_settings)


def forced(pack: RulePack) -> dict[str, Any]:
    return copy.deepcopy(indic_qa_tamil.SETTINGS) if is_layer(pack) else {}


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _same_type(value: Any, default: Any) -> bool:
    if isinstance(default, bool):
        return isinstance(value, bool)
    if _number(default):
        return _number(value)
    return isinstance(value, type(default))


def validate(raw: Any, pack: RulePack) -> tuple[dict[str, Any], list[str]]:
    """(sparse settings, problems): what of `raw` this pack's checker
    understands, minus every value equal to the default."""
    if raw is None:
        return {}, []
    if not isinstance(raw, dict):
        return {}, ["checker settings ignored: expected an object"]
    base = defaults(pack)
    locked = forced(pack)
    clean: dict[str, Any] = {}
    problems: list[str] = []
    for section, value in raw.items():
        if section == "version":
            continue
        where = f"checker.{section}"
        if section not in SECTIONS or section not in base:
            problems.append(f"{where} refused: not a setting of this checker")
            continue
        if section == "checked_contexts":
            allowed = contexts(pack)
            if not isinstance(value, list) or not all(isinstance(c, str) for c in value):
                problems.append(f"{where} refused: expected a list of contexts")
            elif set(value) - set(allowed):
                problems.append(f"{where} refused: {sorted(set(value) - set(allowed))} is not text Bridge checks "
                                f"for this pack ({', '.join(allowed)})")
            elif sorted(value) != sorted(c for c in base[section] if c in allowed):
                clean[section] = [c for c in allowed if c in value]
            continue
        if not isinstance(value, dict):
            problems.append(f"{where} refused: expected an object")
            continue
        kept: dict[str, Any] = {}
        for key, item in value.items():
            here = f"{where}.{key}"
            if section == "style":
                if not isinstance(key, str) or not key or len(key) > MAX_STYLE_KEY or not isinstance(item, str):
                    problems.append(f"{here} refused: a style choice is a form, \"auto\" or \"off\"")
                elif item != base[section].get(key, "auto"):
                    kept[key] = item
                continue
            if key in (OFFERED_KEYS.get(section) or base[section]) and key not in locked.get(section, {}):
                default = base[section].get(key)
                if default is None or not _same_type(item, default):
                    problems.append(f"{here} refused: expected {type(default).__name__}")
                elif _number(item) and item < 0:
                    problems.append(f"{here} refused: must not be negative")
                elif section == "suggest" and key == "max" and not 1 <= item <= MAX_SUGGEST:
                    problems.append(f"{here} refused: 1 to {MAX_SUGGEST}")
                elif (section, key) == ("sandhi", "suffix_pct") and not 0.5 <= item <= 1:
                    problems.append(f"{here} refused: a share from 0.5 to 1")
                elif (section, key) == ("consistency", "minority_max") and not 0 <= item <= 1:
                    problems.append(f"{here} refused: a share from 0 to 1")
                elif item != default:
                    kept[key] = item
            else:
                problems.append(f"{here} refused: not a setting of this checker")
        if kept:
            clean[section] = kept
    return clean, problems


def merged(pack: RulePack, settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """The defaults with the project's settings (`settings`, else the pack's
    own) and the layer's forced switches over them: what the checker runs with,
    for the sections the dialog shows."""
    out = defaults(pack)
    for section, value in (pack.checker_settings if settings is None else settings).items():
        if isinstance(value, dict) and isinstance(out.get(section), dict):
            out[section] = {**out[section], **value}
        else:
            out[section] = copy.deepcopy(value)
    for section, value in forced(pack).items():
        out[section] = {**out.get(section, {}), **value}
    if "checked_contexts" in out:
        out["checked_contexts"] = [c for c in contexts(pack) if c in out["checked_contexts"]]
    return out
