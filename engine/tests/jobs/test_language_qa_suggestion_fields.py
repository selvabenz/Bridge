"""A suggestion carries the checker's edit class and frequency beside its
rationale, and a finding keeps nine of them, as the indic-qa editor's menu
offers nine."""
from tc_ai_bridge.language_qa import MAX_SUGGESTIONS, rule_fields, suggestion


def test_a_suggestion_carries_kind_and_frequency_only_when_known():
    assert suggestion("x", "lexicon", "why", kind="vowel_length", freq=12) == {
        "text": "x", "rank": 1, "source": "lexicon", "rationale": "why", "kind": "vowel_length", "freq": 12}
    assert set(suggestion("x", "rule", "")) == {"text", "rank", "source", "rationale"}


def test_a_finding_keeps_nine_suggestions_ranked():
    fields = rule_fields("spacing.extra", [suggestion(str(n), "lexicon", "") for n in range(12)])
    assert MAX_SUGGESTIONS == 9
    assert [s["rank"] for s in fields["suggestions"]] == list(range(1, 10))
