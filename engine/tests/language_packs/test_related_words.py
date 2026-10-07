"""Related words (related_words, ported from indic-qa's related.py): verse-
aligned OV/IRV equivalents and same-stem forms."""
from tc_ai_bridge.language_packs.related_words import MIN_PAIR, RelatedWords


def build(pairs):
    ov = [(ref, o.split()) for ref, o, _i in pairs]
    irv = [(ref, i.split()) for ref, _o, i in pairs]
    return RelatedWords.build(ov, irv)


def test_a_replacement_seen_in_two_verses_is_an_equivalent_once_is_not():
    related = build([
        ("GEN 1:1", "அவன் கண்டான் அதை", "அவன் பார்த்தான் அதை"),
        ("GEN 1:2", "அவள் கண்டான் இதை", "அவள் பார்த்தான் இதை"),
        ("GEN 1:3", "நான் சொன்னேன்", "நான் கூறினேன்"),
    ])
    assert MIN_PAIR == 2
    seen = related.lookup("பார்த்தான்")
    assert [(e["w"], e["n"], e["source"]) for e in seen["equivalents"]] == [("கண்டான்", 2, "OV")]
    assert seen["equivalents"][0]["ref"] == "GEN 1:1"
    assert related.lookup("கண்டான்")["equivalents"][0]["w"] == "பார்த்தான்"
    assert related.lookup("கூறினேன்")["equivalents"] == [], "a single occurrence is not evidence"


def test_only_same_length_replacements_of_at_most_three_words_are_paired():
    related = build([
        ("GEN 1:1", "a b c d e", "a x y z w e"),   # 3 replaced by 4: not paired
        ("GEN 1:2", "a b c d e", "a x y z w e"),
    ])
    assert related.lookup("x")["equivalents"] == []


def test_verses_only_in_one_text_are_counted_but_not_aligned():
    related = RelatedWords.build([("GEN 1:1", ["a", "b"])], [("GEN 1:1", ["a", "c"]), ("GEN 9:9", ["c"])])
    assert related.irv_count["c"] == 2 and related.verses_aligned == 1


def test_the_family_is_words_sharing_the_stem_by_frequency():
    related = build([
        ("GEN 1:1", "உபதேசம் உபதேசி உபதேசம்", "உபதேசம் உபதேசித்தான் உபதேசம்"),
    ])
    # Each occurs once (one OV, one IRV): a tie on frequency, then the word.
    assert related.lookup("உபதேசம்")["family"] == [
        {"w": "உபதேசி", "irv": 0, "ov": 1}, {"w": "உபதேசித்தான்", "irv": 1, "ov": 0}]
    assert related.lookup("அ")["family"] == [], "under three characters there is no stem"
