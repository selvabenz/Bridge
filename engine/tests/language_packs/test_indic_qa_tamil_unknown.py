"""The Tamil layer's unknown words (DECISIONS 2026-10-08), on the checker's
own token shape: no dictionary is loaded. A word not in the OV is a near miss
when it is rare and one typing slip from a known word, an unknown word when the
IRV uses it fewer than IRV_ACCEPT_MIN times, and house practice (no finding)
when the IRV uses it that often; a compound the IRV uses often is accepted."""
from tc_ai_bridge.language_packs.indic_qa_tamil import IRV_ACCEPT_MIN, RARE_MAX, _token_item


def token(status, irv, sugg=(), parts=None):
    out = {"status": status, "irv": irv, "ov": 0, "ov_lemma": 0, "sugg": list(sugg)}
    if parts:
        out["parts"] = parts
    return out


def test_tamil_unknown_is_reported_only_when_rare_in_the_irv():
    assert _token_item(token("unknown", 0))[0] == "indicqa.lex.unknown"
    assert _token_item(token("unknown", IRV_ACCEPT_MIN - 1))[0] == "indicqa.lex.unknown"
    assert _token_item(token("unknown", IRV_ACCEPT_MIN)) is None, "house practice"


def test_a_rare_typing_slip_is_still_a_near_miss_with_its_kind_and_frequency():
    rule, suggestions, _why = _token_item(token("unknown", RARE_MAX, [{"w": "அவன்", "cls": "consonant_confusable",
                                                                      "freq": 412}]))
    assert rule == "indicqa.lex.near-miss"
    assert (suggestions[0]["text"], suggestions[0]["kind"], suggestions[0]["freq"]) == (
        "அவன்", "consonant_confusable", 412)


def test_a_compound_is_split_when_rare_and_accepted_when_common():
    rare = _token_item(token("compound", 1, parts=["அவன்", "வீடு", "plain"]))
    assert rare[0] == "indicqa.lex.compound" and rare[1][0]["kind"] == "split"
    assert _token_item(token("compound", RARE_MAX + 1, parts=["அவன்", "வீடு", "plain"])) is None
