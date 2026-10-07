"""The inline threshold on its own (language_qa_drawn): which findings are
drawn in the verse text. No dispatcher, no pass."""
from types import SimpleNamespace

from tc_ai_bridge.language_qa_drawn import InlinePolicy, drawn_rule_names, gated_rules, is_drawn, with_drawn

GATED = {"hi-irv/a": {"rule": "a", "confidence": "high", "precision": 0.5, "labelled": 30},
         "hi-irv/b": {"rule": "b", "confidence": "medium", "precision": None, "labelled": 0},
         "hi-irv/c": {"rule": "c", "confidence": "low", "precision": 0.95, "labelled": 25}}


def finding(rule_id, inline=False):
    return {"ruleId": rule_id, "inline": inline}


def test_the_default_policy_draws_every_indic_qa_rule_and_nothing_else():
    policy = InlinePolicy()
    assert all(is_drawn(finding(r), GATED, policy) for r in GATED)
    assert not is_drawn(finding("ta-irv/sandhi.x"), GATED, policy), "a non-indic-qa rule keeps its reviewed flag"
    assert is_drawn(finding("ta-irv/sandhi.x", inline=True), GATED, policy)


def test_precision_hides_measured_rules_below_it_and_never_an_unmeasured_one():
    policy = InlinePolicy.of(60, "low")
    assert [is_drawn(finding(r), GATED, policy) for r in ("hi-irv/a", "hi-irv/b", "hi-irv/c")] == [False, True, True]
    assert is_drawn(finding("hi-irv/b"), GATED, InlinePolicy.of(100, "low"))


def test_the_confidence_floor_and_the_slider_must_both_pass():
    assert [is_drawn(finding(r), GATED, InlinePolicy.of(0, "medium"))
            for r in ("hi-irv/a", "hi-irv/b", "hi-irv/c")] == [True, True, False]
    assert [is_drawn(finding(r), GATED, InlinePolicy.of(40, "high"))
            for r in ("hi-irv/a", "hi-irv/b", "hi-irv/c")] == [True, False, False]


def test_a_reviewed_inline_finding_is_drawn_whatever_the_threshold():
    assert is_drawn(finding("hi-irv/a", inline=True), GATED, InlinePolicy.of(100, "high"))


def test_out_of_range_input_falls_back_to_drawing_everything():
    assert InlinePolicy.of("x", None) == InlinePolicy(0, "low")
    assert InlinePolicy.of(-5, "HIGH") == InlinePolicy(0, "high")
    assert InlinePolicy.of(500, "certain") == InlinePolicy(100, "low")


def test_drawn_rule_names_and_with_drawn_agree_with_is_drawn():
    policy = InlinePolicy.of(60, "low")
    assert drawn_rule_names(["spacing.extra"], GATED, policy) == ["b", "c", "spacing.extra"]
    copies = with_drawn([finding("hi-irv/a"), finding("hi-irv/b")], GATED, policy)
    assert [f["drawn"] for f in copies] == [False, True]


def test_gated_rules_lists_only_the_enabled_indic_qa_rules_of_the_pack():
    rule = lambda id_, match_type="indic-qa", enabled=True, confidence="medium": SimpleNamespace(  # noqa: E731
        id=id_, name=id_, match_type=match_type, enabled=enabled, confidence=confidence)
    pack = SimpleNamespace(name="ta-irv", rules=[rule("indicqa.x"), rule("indicqa.off", enabled=False),
                                                   rule("sandhi.y", match_type="token-context")],
                           precision=lambda: {"ta-irv/indicqa.x": {"precision": 0.8, "labelled": 22}})
    assert gated_rules(pack) == {"ta-irv/indicqa.x": {"rule": "indicqa.x", "confidence": "medium",
                                                      "precision": 0.8, "labelled": 22}}
    assert gated_rules(None) == {}
