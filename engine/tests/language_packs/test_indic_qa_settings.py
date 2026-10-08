"""The checker settings a project may keep (indic_qa_settings), on the real
packs' language defaults: what is kept, what is refused, and what the checker
runs with. No dispatcher; the dictionaries are not read."""
import pytest

from tc_ai_bridge.language_packs import default_pack, indic_qa_adapter
from tc_ai_bridge.language_packs import indic_qa_settings as settings
from tc_ai_bridge.language_packs.loader import apply_overrides


def test_values_equal_to_the_defaults_are_not_kept():
    pack = default_pack("hi-irv")
    clean, problems = settings.validate({"version": 1, "suggest": {"max": 5}, "compound": {"enabled": True},
                                         "warnings": {"double_space": False}}, pack)
    assert problems == []
    assert clean == {"warnings": {"double_space": False}}


@pytest.mark.parametrize(("raw", "fragment"), [
    ({"sandhi": {"min_total": 3}}, "checker.sandhi refused"),                       # Tamil only
    ({"checked_contexts": ["heading"]}, "not text Bridge checks"),                  # profiles read verse text
    ({"suggest": {"max": 12}}, "1 to 9"),
    ({"suggest": {"lemma_min": 1}}, "not a setting"),                               # an internal key
    ({"warnings": {"double_space": "no"}}, "expected bool"),
    ({"consistency": {"minority_max": 2}}, "a share from 0 to 1"),
    ({"style": {"nuqta:x": 3}}, "a style choice"),
    ({"layout": {}}, "checker.layout refused"),
])
def test_what_the_checker_would_not_understand_is_refused(raw, fragment):
    clean, problems = settings.validate(raw, default_pack("hi-irv"))
    assert clean == {} and len(problems) == 1 and fragment in problems[0], problems


def test_the_tamil_layer_offers_its_sandhi_and_contexts_but_not_its_forced_warnings():
    pack = default_pack("ta-irv")
    assert settings.contexts(pack) == ("verse", "heading", "footnote_text")
    clean, problems = settings.validate({"sandhi": {"include_weak": True, "suffix_pct": 0.9},
                                         "checked_contexts": ["verse"], "warnings": {"digits": True}}, pack)
    assert clean == {"sandhi": {"include_weak": True, "suffix_pct": 0.9}, "checked_contexts": ["verse"]}
    assert problems == ["checker.warnings.digits refused: not a setting of this checker"]
    assert settings.merged(pack, clean)["warnings"]["digits"] is False, "the 2026-09-28 review's switch stands"


def test_a_project_s_settings_reach_the_checker_but_its_rules_follow_the_pack():
    pack = apply_overrides(default_pack("hi-irv"), {
        "rules": {"hi.lex.unknown": {"enabled": False}},
        "checker": {"suggest": {"max": 2}, "warnings": {"double_space": False}}})
    assert pack.checker_settings == {"suggest": {"max": 2}, "warnings": {"double_space": False}}
    given = indic_qa_adapter._settings(pack)
    assert given["suggest"] == {"max": 2} and given["warnings"] == {"double_space": False}
    assert given["rules"]["hi.lex.unknown"] is False and given["rules"]["hi.lex.known-misspelling"] is True


def test_the_layer_s_forced_warnings_win_over_project_settings():
    pack = apply_overrides(default_pack("ta-irv"), {"checker": {"warnings": {"double_space": False}}})
    given = indic_qa_adapter._settings(pack)
    assert given["warnings"] == {"double_space": False, "digits": False, "repeated_word": False,
                                 "space_before_note_close": False}


def test_refused_checker_settings_are_coverage_notes_not_errors():
    pack = apply_overrides(default_pack("hi-irv"), {"checker": {"suggest": {"max": 0}}})
    assert pack.checker_settings == {}
    assert any("checker.suggest.max refused" in p for p in pack.problems)
    common = apply_overrides(default_pack("ta-irv"), {"checker": {"suggest": {"max": 3}}})
    assert common.checker_settings == {"suggest": {"max": 3}}
