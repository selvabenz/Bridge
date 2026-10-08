"""The bundled language packs, for tests that exercise one on purpose."""
from pathlib import Path

from tc_ai_bridge.language_packs import default_pack
from tc_ai_bridge.language_packs.registry import packs_dir


def ta_pack():
    """The bundled ta-irv pack (loaded once per process)."""
    return default_pack("ta-irv")


def ta_lexicon_path() -> Path:
    return packs_dir() / "ta-irv" / ta_pack().meta["lexicon"]


def with_enabled(pack, *rule_ids):
    """A copy of `pack` with these rules enabled. Tests only: a project
    override may never enable a rule (loader.apply_overrides)."""
    import copy
    from tc_ai_bridge.language_packs.loader import RulePack
    rules = [copy.copy(rule) for rule in pack.rules]
    for rule in rules:
        if rule.id in rule_ids:
            rule.enabled = True
    return RulePack(pack.name, pack.version, pack.language, rules, pack.description,
                    directory=pack.directory, meta=pack.meta)


def switch_off_ta_lexicon(monkeypatch) -> None:
    """Run as if ta-irv had no lexicon: the within-book wordlist fallback,
    and no known splits."""
    from tc_ai_bridge.language_packs import lexicon
    monkeypatch.setitem(lexicon._LOADED, ta_lexicon_path(), None)


def switch_off_ta_unknown_words(monkeypatch) -> None:
    """Run without the indic-qa layer's unknown-word findings
    (indicqa.lex.unknown, DECISIONS 2026-10-08): every out-of-OV word counts as
    house practice. For tests whose synthetic Tamil words are not in the OV and
    are about something else (the wordlist audit, persistence, benchmark
    counting); the rule itself is tested in tests/language_packs."""
    from tc_ai_bridge.language_packs import indic_qa_tamil
    monkeypatch.setattr(indic_qa_tamil, "IRV_ACCEPT_MIN", 0)
