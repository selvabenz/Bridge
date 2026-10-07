"""Learned fixes on their own (language_qa_learned): what counts as replacing
one word, and the findings a learned map produces. No dispatcher, no pass."""
from tc_ai_bridge.language_qa import rule_fields, stable_finding_id, suggestion, text_hash
from tc_ai_bridge.language_qa_learned import RULE, learned_findings, learned_pair


def test_replacing_one_word_is_learned():
    assert learned_pair("அவர் தேவன் என்றார்.", "அவர் கர்த்தர் என்றார்.") == ("தேவன்", "கர்த்தர்")
    assert learned_pair("वे बडे़ लोग थे।", "वे बड़े लोग थे।") == ("बडे़", "बड़े")


def test_anything_but_one_word_replaced_learns_nothing():
    assert learned_pair("அவர் தேவன் என்றார்.", "அவர் தேவன் என்றார்.") is None           # nothing changed
    assert learned_pair("அவர் தேவன் என்றார்.", "அவர் கர்த்தர் சொன்னார்.") is None       # two words
    assert learned_pair("அவர் தேவன் என்றார்.", "அவர் தேவன் நன்று என்றார்.") is None     # a word added
    assert learned_pair("அவர் தேவன் என்றார்.", "அவர் கர்த்தர்  என்றார்.") is None       # spacing changed too
    assert learned_pair("அவர் தேவன் என்றார்.", "அவர் கர்த்தர் என்றார்!") is None        # punctuation changed too
    assert learned_pair("", "x") is None


def test_markup_is_lifted_before_comparing_words():
    before = "அவர் \\it தேவன்\\it* என்றார்.\\f + \\ft குறிப்பு\\f*"
    after = "அவர் \\it கர்த்தர்\\it* என்றார்.\\f + \\ft குறிப்பு\\f*"
    assert learned_pair(before, after) == ("தேவன்", "கர்த்தர்")
    assert learned_pair("அவர் \\it தேவன் என்றார்.", "அவர் \\it கர்த்தர் என்றார்.") is None  # unbalanced markup


def make(book_text, learned, fixes=None):
    return learned_findings("rut", book_text, learned, fixes=fixes or {}, max_verse_chars=20_000,
                            finding_id=stable_finding_id, rule_fields=rule_fields, suggestion=suggestion,
                            text_hash=text_hash, rule_version="language-qa-7")


def test_every_recurrence_is_a_finding_with_raw_offsets_and_the_learned_forms():
    text = {"1": {"1": "தேவன் \\w அன்பு\\w* தேவன்.", "2": "எதுவும் இல்லை."}, "2": {"1": "தேவன்"}}
    fixes = {("தேவன்", "கர்த்தர்"): {"count": 3, "lastRef": "RUT 1:9"}}
    found = make(text, {"தேவன்": ["கர்த்தர்", "இறைவன்"]}, fixes)
    assert [(f["chapter"], f["verse"], f["start"], f["end"]) for f in found] == [
        ("1", "1", 0, 5), ("1", "1", 18, 23), ("2", "1", 0, 5)]
    for f in found:
        raw = text[f["chapter"]][f["verse"]]
        assert raw[f["start"]:f["end"]] == f["originalText"] == "தேவன்"
        assert f["rule"] == RULE and f["category"] == "learned" and f["inline"] is True
        assert f["ruleId"] == "project/learned.replacement"
    assert [s["text"] for s in found[0]["suggestions"]] == ["கர்த்தர்", "இறைவன்"]
    assert found[0]["suggestions"][0]["source"] == "learned"
    assert "3×" in found[0]["suggestions"][0]["rationale"] and "RUT 1:9" in found[0]["suggestions"][0]["rationale"]
    assert len({f["id"] for f in found}) == 3, "repeats in one verse get distinct, stable ids"
    assert [f["id"] for f in make(text, {"தேவன்": ["கர்த்தர்"]})] == [f["id"] for f in found]


def test_no_learned_fixes_means_no_work():
    assert make({"1": {"1": "தேவன்"}}, {}) == []
