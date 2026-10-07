import json
import os
import re
import threading
import time
import unicodedata
from types import SimpleNamespace

import pytest

from tc_ai_bridge.language_packs import default_pack
from tc_ai_bridge.language_qa import (
    CATEGORIES, CONFIDENCES, DEFAULT_COVERAGE, INLINE_RULES, LAYERS, RULE_VERSION, RULES, WORDLIST_COMMON_MIN, inline_rule_names, WORDLIST_MIN_LENGTH, WORDLIST_RARE_MAX, WORDLIST_RATIO_MIN,
    detect_language, lift_inline_usfm, scan_text, stable_finding_id, text_hash, wordlist_findings,
)
from tc_ai_bridge.language_qa_jobs import (
    LanguageQaManager, MAX_CHAPTER_BYTES, MAX_BOOK_FINDINGS, apply_decisions, decision_effect,
)
from tests.support.projects import fixture_project, call
from tests.support.packs import switch_off_ta_lexicon, ta_pack
from tests.support.paths import REPO_ROOT
from bridge_service import BridgeEngine


@pytest.fixture(autouse=True)
def within_book_wordlist(monkeypatch):
    """These tests pin the within-book wordlist audit on synthetic words that
    the IRV corpus does not contain, so they run with the pack's corpus
    lexicon switched off -- the fallback path that audit now is. The lexicon
    rules are tested on the real lexicon in test_lexicon.py."""
    switch_off_ta_lexicon(monkeypatch)


def scan(text, tamil=True):
    return scan_text(text, book="php", chapter="2", verse="3-4", pack=ta_pack() if tamil else None)


def wordlist(book, counts, first_seen):
    """The ta-irv pack's within-book wordlist audit (its fallback without a lexicon)."""
    pack = ta_pack()
    return wordlist_findings(book, counts, first_seen, pack=pack, rule=pack.by_id("tamil.wordlist-variant"))


@pytest.mark.parametrize("text", [
    "தமிழ் மொழி", "கொ கோ கௌ ஔ", "க்ஷேத்திரம் ஸ்ரீ ஜீவன் ஹோசன்னா ஷ ஶ்ரீ",
    "அவர் இல்லை.", "ர ற ல ள ழ ந ன ண", "௧௨௩ ௐ ஃ",
    "க\u0bc6\u0bbe க\u0bc7\u0bbe க\u0bc6\u0bd7 ஒ\u0bd7",
])
def test_legal_tamil_signs_and_conjuncts(text):
    assert not [f for f in scan(text)["findings"] if f["rule"] == "tamil.dependent-sign"]


@pytest.mark.parametrize("text", ["ாக", "க்்", "குீ", "அா", " ் ", "கைா"])
def test_broken_dependent_signs_have_exact_raw_spans(text):
    findings = scan(text)["findings"]
    assert any(f["rule"] == "tamil.dependent-sign" for f in findings)
    for finding in findings:
        assert text[finding["start"]:finding["end"]] == finding["originalText"]
        assert finding["verse"] == "3-4"
        assert finding["textHash"] == text_hash(text)


def test_normalization_is_advisory_and_input_is_preserved():
    raw = unicodedata.normalize("NFD", "கொடுத்தார்.")
    assert scan(raw)["findings"][0]["rule"] == "unicode.nfc"
    assert all(f["severity"] == "low" for f in scan(raw)["findings"])
    assert raw != unicodedata.normalize("NFC", raw)


def test_identity_survives_unrelated_text_shift_but_hash_changes():
    one = next(f for f in scan("அவர் �")["findings"] if f["rule"] == "unicode.corruption")
    two = next(f for f in scan("😀 அவர் �")["findings"] if f["rule"] == "unicode.corruption")
    assert one["id"] == two["id"]
    assert one["textHash"] != two["textHash"]
    assert two["start"] == 7  # Python code points, not UTF-16 units.


def test_review_candidates_and_explicit_omissions():
    result = scan("மெல்ல மெல்ல தமிழ்a  ,,,\u200d\ue001")
    rules = {f["rule"] for f in result["findings"]}
    assert {"tamil.mixed-word", "punctuation.repeated",
            "spacing.extra", "unicode.invisible", "unicode.private-use"} <= rules
    # Disabled by default (2026-09-28 review, 0 of 20): மெல்ல மெல்ல is அடுக்குத்தொடர்.
    assert "tamil.repeated-word" not in rules
    assert all(f["status"] == "review-needed" for f in result["findings"])
    wj = scan("\\wj அவர்\\wj*")  # inline USFM is lifted and scanned, not omitted
    assert wj["checked"] and not wj["limitations"]
    assert scan("அ" * 20_001)["limitations"]
    assert not scan("அ" * 20_001)["checked"]
    assert len(scan("�" * 200)["findings"]) == 100
    assert scan("�" * 200)["limitations"]
    assert not scan("என்ன?! ... …", tamil=False)["findings"]


@pytest.mark.parametrize("text,flagged,initial", [
    ("அந்த காகம்", "அந்த", "க"), ("அந்த பெண்", "அந்த", "ப"),
    ("இந்த செய்தி", "இந்த", "ச"), ("இந்த தலைமுறை", "இந்த", "த"),
    ("எந்த பக்கம்", "எந்த", "ப"), ("எந்த காரணம்", "எந்த", "க"),
])
def test_vallinam_missing_link_is_flagged(text, flagged, initial):
    findings = [f for f in scan(text)["findings"] if f["rule"] == "tamil.vallinam-missing"]
    assert len(findings) == 1
    finding = findings[0]
    assert finding["severity"] == "medium" and finding["status"] == "review-needed"
    assert finding["originalText"] == text
    assert flagged in finding["message"] and f"{flagged}{initial}்" in finding["message"]
    assert finding["suggestedReplacement"] == text.replace(flagged, f"{flagged}{initial}்", 1)


@pytest.mark.parametrize("text", [
    "அந்தக் காகம்", "அந்தப் பெண்", "இந்தச் செய்தி",
    "இந்தத் தலைமுறை", "எந்தப் பக்கம்", "எந்தக் காரணம்",
])
def test_vallinam_correct_forms_are_not_flagged(text):
    assert not [f for f in scan(text)["findings"] if f["rule"] == "tamil.vallinam-missing"]


@pytest.mark.parametrize("text", ["அந்த வீடு", "இந்த மனிதன்", "எந்த ஊர்"])
def test_vallinam_ignores_non_trigger_initials(text):
    assert not [f for f in scan(text)["findings"] if f["rule"] == "tamil.vallinam-missing"]


@pytest.mark.parametrize("text", ["அந்த, காகம்", "அந்த. காகம்", "அந்த; காகம்"])
def test_vallinam_ignores_a_punctuation_boundary(text):
    assert not [f for f in scan(text)["findings"] if f["rule"] == "tamil.vallinam-missing"]


@pytest.mark.parametrize("text", ["சிந்த காகம்", "அந்தநாள் காகம்"])
def test_vallinam_requires_an_exact_token_match(text):
    assert not [f for f in scan(text)["findings"] if f["rule"] == "tamil.vallinam-missing"]


@pytest.mark.parametrize("text", ["அது காகம்", "இது சோலை", "எது தண்ணீர்"])
def test_vallinam_does_not_generalize_to_adhu_idhu_edhu(text):
    assert not [f for f in scan(text)["findings"] if f["rule"] == "tamil.vallinam-missing"]


# Part B2: same rule, same mechanism, a second closed-class trigger set
# (அப்படி/இப்படி/எப்படி, manner-adverbs, rather than B1's demonstratives).
# See VALLINAM_TRIGGERS's own comment in language_qa.py for why one shared
# mechanism covers both without any new logic.


@pytest.mark.parametrize("text,flagged,initial", [
    ("அப்படி கூறினான்", "அப்படி", "க"), ("அப்படி செய்தான்", "அப்படி", "ச"),
    ("அப்படி திரும்பினான்", "அப்படி", "த"), ("அப்படி பேசினான்", "அப்படி", "ப"),
    ("இப்படி காட்டினான்", "இப்படி", "க"), ("இப்படி சொன்னான்", "இப்படி", "ச"),
    ("இப்படி தெரியும்", "இப்படி", "த"), ("இப்படி பார்த்தான்", "இப்படி", "ப"),
    ("எப்படி கண்டாய்", "எப்படி", "க"), ("எப்படி செய்வாய்", "எப்படி", "ச"),
    ("எப்படி தெரியும்", "எப்படி", "த"), ("எப்படி பேசுவாய்", "எப்படி", "ப"),
])
def test_vallinam_b2_missing_link_is_flagged(text, flagged, initial):
    findings = [f for f in scan(text)["findings"] if f["rule"] == "tamil.vallinam-missing"]
    assert len(findings) == 1
    finding = findings[0]
    assert finding["severity"] == "medium" and finding["status"] == "review-needed"
    assert finding["originalText"] == text
    assert flagged in finding["message"] and f"{flagged}{initial}்" in finding["message"]
    assert text[finding["start"]:finding["end"]] == finding["originalText"]
    assert finding["suggestedReplacement"] == text.replace(flagged, f"{flagged}{initial}்", 1)


@pytest.mark.parametrize("text", [
    "அப்படிக் கூறினான்", "அப்படிச் செய்தான்", "அப்படித் திரும்பினான்", "அப்படிப் பேசினான்",
    "இப்படிக் காட்டினான்", "இப்படிச் சொன்னான்", "இப்படித் தெரியும்", "இப்படிப் பார்த்தான்",
    "எப்படிக் கண்டாய்", "எப்படிச் செய்வாய்", "எப்படித் தெரியும்", "எப்படிப் பேசுவாய்",
])
def test_vallinam_b2_correct_forms_are_not_flagged(text):
    assert not [f for f in scan(text)["findings"] if f["rule"] == "tamil.vallinam-missing"]


@pytest.mark.parametrize("text", ["அப்படி நடந்தது", "இப்படி வந்தான்", "எப்படி முடியும்", "அப்படி எழுதினான்"])
def test_vallinam_b2_ignores_non_trigger_initials(text):
    assert not [f for f in scan(text)["findings"] if f["rule"] == "tamil.vallinam-missing"]


@pytest.mark.parametrize("text", ["அப்படி, கூறினான்", "இப்படி; சொன்னான்", "எப்படி? தெரியும்"])
def test_vallinam_b2_ignores_a_punctuation_boundary(text):
    assert not [f for f in scan(text)["findings"] if f["rule"] == "tamil.vallinam-missing"]


@pytest.mark.parametrize("text", [
    "அப்படித்தான் கூறினான்", "இப்படியும் சொன்னான்", "எப்படியோ தெரியும்",
    "அப்படியான செயல்", "இப்படிப்பட்ட மனிதன்",
])
def test_vallinam_b2_does_not_generalize_to_lookalike_suffixed_forms(text):
    # அப்படித்தான்/இப்படியும்/எப்படியோ/அப்படியான/இப்படிப்பட்ட tokenize as one
    # word each (letters+marks with no whitespace inside), so none of them
    # equal a bare trigger -- the same exact-match mechanism that keeps this
    # rule from generalizing for B1's அது/இது/எது, with no separate
    # exclusion list needed.
    assert not [f for f in scan(text)["findings"] if f["rule"] == "tamil.vallinam-missing"]


@pytest.mark.parametrize("text", ["அப்படி", "இப்படி.", "எப்படி?"])
def test_vallinam_b2_trigger_at_end_of_verse_does_not_crash_or_flag(text):
    assert not [f for f in scan(text)["findings"] if f["rule"] == "tamil.vallinam-missing"]


def test_vallinam_b2_is_found_inside_inline_usfm():
    # Inline USFM used to make scan_text() abstain on the whole verse. The
    # markers are now lifted; the finding still carries raw offsets.
    text = "\\wj அப்படி கூறினான்\\wj*"
    [finding] = [f for f in scan(text)["findings"] if f["rule"] == "tamil.vallinam-missing"]
    assert finding["originalText"] == "அப்படி கூறினான்" == text[finding["start"]:finding["end"]]
    assert finding["start"] == len("\\wj ")
    assert finding["suggestedReplacement"] == "அப்படிக் கூறினான்"


def test_vallinam_b2_matches_nfd_decomposed_trigger_text():
    nfd_text = unicodedata.normalize("NFD", "அப்படி கூறினான்")
    findings = [f for f in scan(nfd_text)["findings"] if f["rule"] == "tamil.vallinam-missing"]
    assert len(findings) == 1
    assert findings[0]["originalText"] == nfd_text  # raw span, never rewritten to NFC


def test_vallinam_b2_finding_identity_is_stable_across_repeated_scans():
    first = next(f for f in scan("அப்படி கூறினான்")["findings"] if f["rule"] == "tamil.vallinam-missing")
    second = next(f for f in scan("அப்படி கூறினான்")["findings"] if f["rule"] == "tamil.vallinam-missing")
    assert first["id"] == second["id"]


def test_vallinam_b2_never_mutates_the_input_text():
    text = "அப்படி கூறினான்"
    scan(text)
    assert text == "அப்படி கூறினான்"


def test_vallinam_b2_coexists_with_b1_triggers_in_the_same_verse():
    result = scan("அந்த காகம் அப்படி கூறினான்.")
    findings = [f for f in result["findings"] if f["rule"] == "tamil.vallinam-missing"]
    assert {f["originalText"] for f in findings} == {"அந்த காகம்", "அப்படி கூறினான்"}


def test_vallinam_b1_regression_is_unaffected_by_b2():
    # B1's own tests above are untouched; this is a direct check that adding
    # the B2 trigger words did not change B1's existing behavior.
    findings = [f for f in scan("அந்த காகம்")["findings"] if f["rule"] == "tamil.vallinam-missing"]
    assert len(findings) == 1 and findings[0]["originalText"] == "அந்த காகம்"


# Part B3: same mechanism again, a third closed-class trigger set -- an
# explicit fourth-case/dative surface form (எனக்கு/உங்களுக்கு/தேவனுக்கு)
# rather than B1's demonstratives or B2's manner-adverbs. This is an
# allowlist of independently verified dative forms, not a suffix rule --
# see VALLINAM_TRIGGERS's own comment in language_qa.py.


@pytest.mark.parametrize("text,flagged,initial", [
    ("எனக்கு கொடு", "எனக்கு", "க"), ("எனக்கு சம்பவித்தவைகள்", "எனக்கு", "ச"),
    ("எனக்கு தெரியும்", "எனக்கு", "த"), ("எனக்கு பயன்", "எனக்கு", "ப"),
    ("உங்களுக்கு கொடுக்கப்பட்டிருக்கிறது", "உங்களுக்கு", "க"), ("உங்களுக்கு செய்தான்", "உங்களுக்கு", "ச"),
    ("உங்களுக்கு தெரியும்", "உங்களுக்கு", "த"), ("உங்களுக்கு பயன்", "உங்களுக்கு", "ப"),
    ("தேவனுக்கு கொடு", "தேவனுக்கு", "க"), ("தேவனுக்கு சுகந்த", "தேவனுக்கு", "ச"),
    ("தேவனுக்கு தெரியும்", "தேவனுக்கு", "த"), ("தேவனுக்கு பயன்", "தேவனுக்கு", "ப"),
])
def test_vallinam_b3_missing_link_is_flagged(text, flagged, initial):
    findings = [f for f in scan(text)["findings"] if f["rule"] == "tamil.vallinam-missing"]
    assert len(findings) == 1
    finding = findings[0]
    assert finding["severity"] == "medium" and finding["status"] == "review-needed"
    assert finding["originalText"] == text
    assert flagged in finding["message"] and f"{flagged}{initial}்" in finding["message"]
    assert text[finding["start"]:finding["end"]] == finding["originalText"]
    assert finding["suggestedReplacement"] == text.replace(flagged, f"{flagged}{initial}்", 1)


@pytest.mark.parametrize("text,flagged,initial,expected_span", [
    ("எனக்கு சம்பவித்தவைகள்", "எனக்கு", "ச", "எனக்கு சம்பவித்தவைகள்"),
    ("உங்களுக்கு கொடுக்கப்பட்டிருக்கிறது", "உங்களுக்கு", "க", "உங்களுக்கு கொடுக்கப்பட்டிருக்கிறது"),
    # The maintainer's literal fixture has a third word (வாசனையாக) that sits
    # outside the flagged span -- the rule only ever covers the trigger plus
    # its immediately following word, same as every other case here.
    ("தேவனுக்கு சுகந்த வாசனையாக", "தேவனுக்கு", "ச", "தேவனுக்கு சுகந்த"),
])
def test_vallinam_b3_matches_the_maintainers_philippians_fixtures(text, flagged, initial, expected_span):
    # The exact "incorrect" forms from the maintainer's B3 spec (2026-09-24),
    # each traceable to a real verse from the Round 2 QA pass (php 1:12,
    # 1:29, 4:15 respectively) rather than a synthetic example -- and each
    # previously carried a "Rejected"/no-finding disposition in that pass's
    # own review, which the maintainer explicitly said not to treat as
    # linguistic ground truth. This rule now flags all three.
    findings = [f for f in scan(text)["findings"] if f["rule"] == "tamil.vallinam-missing"]
    assert len(findings) == 1
    finding = findings[0]
    assert finding["originalText"] == expected_span
    assert finding["suggestedReplacement"] == expected_span.replace(flagged, f"{flagged}{initial}்", 1)


@pytest.mark.parametrize("text", [
    "எனக்குக் கொடு", "எனக்குச் சம்பவித்தவைகள்", "எனக்குத் தெரியும்", "எனக்குப் பயன்",
    "உங்களுக்குக் கொடுக்கப்பட்டிருக்கிறது", "உங்களுக்குச் செய்தான்", "உங்களுக்குத் தெரியும்", "உங்களுக்குப் பயன்",
    "தேவனுக்குக் கொடு", "தேவனுக்குச் சுகந்த வாசனையாக", "தேவனுக்குத் தெரியும்", "தேவனுக்குப் பயன்",
])
def test_vallinam_b3_correct_forms_are_not_flagged(text):
    assert not [f for f in scan(text)["findings"] if f["rule"] == "tamil.vallinam-missing"]


@pytest.mark.parametrize("text", ["எனக்கு வேண்டும்", "உங்களுக்கு நன்மை", "தேவனுக்கு மகிமை"])
def test_vallinam_b3_ignores_non_trigger_initials(text):
    assert not [f for f in scan(text)["findings"] if f["rule"] == "tamil.vallinam-missing"]


@pytest.mark.parametrize("text", ["எனக்கு, கொடு", "உங்களுக்கு; செய்தான்", "தேவனுக்கு? தெரியும்"])
def test_vallinam_b3_ignores_a_punctuation_boundary(text):
    assert not [f for f in scan(text)["findings"] if f["rule"] == "tamil.vallinam-missing"]


@pytest.mark.parametrize("text", ["எனக்குள் இருக்கிறது", "உங்களுக்குள் இருக்கிறது", "தேவனுக்குரிய கனம்"])
def test_vallinam_b3_does_not_generalize_to_lookalike_suffixed_forms(text):
    # எனக்குள்/உங்களுக்குள்/தேவனுக்குரிய each tokenize as one word (letters+
    # marks, no internal whitespace), so none equals a bare trigger -- the
    # same exact-match mechanism, not a new exclusion list. This is the
    # concrete demonstration of the spec's morphological guard: B3 never
    # infers "ends with க்கு" as dative, only exact surface-form membership.
    assert not [f for f in scan(text)["findings"] if f["rule"] == "tamil.vallinam-missing"]


@pytest.mark.parametrize("text", ["எனக்கு", "உங்களுக்கு.", "தேவனுக்கு?"])
def test_vallinam_b3_trigger_at_end_of_verse_does_not_crash_or_flag(text):
    assert not [f for f in scan(text)["findings"] if f["rule"] == "tamil.vallinam-missing"]


def test_vallinam_b3_is_found_inside_inline_usfm():
    text = "\\wj எனக்கு கொடு\\wj*"
    [finding] = [f for f in scan(text)["findings"] if f["rule"] == "tamil.vallinam-missing"]
    assert finding["originalText"] == "எனக்கு கொடு" == text[finding["start"]:finding["end"]]


def test_vallinam_b3_matches_nfd_decomposed_trigger_text():
    nfd_text = unicodedata.normalize("NFD", "எனக்கு கொடு")
    findings = [f for f in scan(nfd_text)["findings"] if f["rule"] == "tamil.vallinam-missing"]
    assert len(findings) == 1
    assert findings[0]["originalText"] == nfd_text  # raw span, never rewritten to NFC


def test_vallinam_b3_finding_identity_is_stable_across_repeated_scans():
    first = next(f for f in scan("எனக்கு கொடு")["findings"] if f["rule"] == "tamil.vallinam-missing")
    second = next(f for f in scan("எனக்கு கொடு")["findings"] if f["rule"] == "tamil.vallinam-missing")
    assert first["id"] == second["id"]


def test_vallinam_b3_never_mutates_the_input_text():
    text = "எனக்கு கொடு"
    scan(text)
    assert text == "எனக்கு கொடு"


def test_vallinam_b3_coexists_with_b1_and_b2_triggers_in_the_same_verse():
    result = scan("அந்த காகம் இப்படி செய்தான் எனக்கு கொடு.")
    findings = [f for f in result["findings"] if f["rule"] == "tamil.vallinam-missing"]
    assert {f["originalText"] for f in findings} == {"அந்த காகம்", "இப்படி செய்தான்", "எனக்கு கொடு"}


def test_vallinam_b1_b2_regression_is_unaffected_by_b3():
    # B1/B2's own tests above are untouched; this is a direct check that
    # adding B3's trigger words did not change either's existing behavior.
    b1 = [f for f in scan("அந்த காகம்")["findings"] if f["rule"] == "tamil.vallinam-missing"]
    assert len(b1) == 1 and b1[0]["originalText"] == "அந்த காகம்"
    b2 = [f for f in scan("அப்படி கூறினான்")["findings"] if f["rule"] == "tamil.vallinam-missing"]
    assert len(b2) == 1 and b2[0]["originalText"] == "அப்படி கூறினான்"


# Part B4: same mechanism again, a fourth closed-class trigger set -- an
# explicit second-case/accusative surface form (என்னை/உங்களை/அவனை/அதை/எதை)
# rather than B1's demonstratives, B2's manner-adverbs, or B3's dative
# forms. Allowlist of verified accusative pronoun forms, not a suffix rule --
# see VALLINAM_TRIGGERS's own comment in language_qa.py.


@pytest.mark.parametrize("text,flagged,initial", [
    ("என்னை கொடு", "என்னை", "க"), ("என்னை சம்பவித்தவைகள்", "என்னை", "ச"),
    ("என்னை தெரியும்", "என்னை", "த"), ("என்னை பயன்", "என்னை", "ப"),
    ("உங்களை கொடு", "உங்களை", "க"), ("உங்களை செய்தான்", "உங்களை", "ச"),
    ("உங்களை திரும்பினான்", "உங்களை", "த"), ("உங்களை பயன்", "உங்களை", "ப"),
    ("அவனை கொடு", "அவனை", "க"), ("அவனை சீக்கிரமாக", "அவனை", "ச"),
    ("அவனை தெரியும்", "அவனை", "த"), ("அவனை பேசினான்", "அவனை", "ப"),
    ("அதை கொடு", "அதை", "க"), ("அதை செய்தான்", "அதை", "ச"),
    ("அதை திரும்பினான்", "அதை", "த"), ("அதை பயன்", "அதை", "ப"),
    ("எதை கொடு", "எதை", "க"), ("எதை செய்தான்", "எதை", "ச"),
    ("எதை தெரியும்", "எதை", "த"), ("எதை பேசினான்", "எதை", "ப"),
])
def test_vallinam_b4_missing_link_is_flagged(text, flagged, initial):
    findings = [f for f in scan(text)["findings"] if f["rule"] == "tamil.vallinam-missing"]
    assert len(findings) == 1
    finding = findings[0]
    assert finding["severity"] == "medium" and finding["status"] == "review-needed"
    assert finding["originalText"] == text
    assert flagged in finding["message"] and f"{flagged}{initial}்" in finding["message"]
    assert text[finding["start"]:finding["end"]] == finding["originalText"]
    assert finding["suggestedReplacement"] == text.replace(flagged, f"{flagged}{initial}்", 1)


def test_vallinam_b4_matches_the_real_philippians_2_28_occurrence():
    # Exact real text from php 2:28 (verified directly against the staged
    # source file, not a synthetic example): "அவனை சீக்கிரமாக" is bare in
    # the live project text. Not a maintainer-supplied fixture like B1-B3's
    # -- an independently re-verified real occurrence in the actual review
    # target, the same evidentiary bar.
    text = "நீங்கள் அவனை மீண்டும் பார்த்து மகிழ்ச்சியடையவும், அவனை சீக்கிரமாக அனுப்பினேன்."
    findings = [f for f in scan(text)["findings"] if f["rule"] == "tamil.vallinam-missing"]
    assert len(findings) == 1
    finding = findings[0]
    assert finding["originalText"] == "அவனை சீக்கிரமாக"
    assert finding["suggestedReplacement"] == "அவனைச் சீக்கிரமாக"


@pytest.mark.parametrize("text", [
    "என்னைக் கொடு", "என்னைச் சம்பவித்தவைகள்", "என்னைத் தெரியும்", "என்னைப் பயன்",
    "உங்களைக் கொடு", "உங்களைச் செய்தான்", "உங்களைத் திரும்பினான்", "உங்களைப் பயன்",
    "அவனைக் கொடு", "அவனைச் சீக்கிரமாக", "அவனைத் தெரியும்", "அவனைப் பேசினான்",
    "அதைக் கொடு", "அதைச் செய்தான்", "அதைத் திரும்பினான்", "அதைப் பயன்",
    "எதைக் கொடு", "எதைச் செய்தான்", "எதைத் தெரியும்", "எதைப் பேசினான்",
])
def test_vallinam_b4_correct_forms_are_not_flagged(text):
    assert not [f for f in scan(text)["findings"] if f["rule"] == "tamil.vallinam-missing"]


@pytest.mark.parametrize("text", ["என்னை வேண்டும்", "உங்களை நான்", "அவனை மகிழ்ச்சி", "அதை நல்லது", "எதை மனிதன்"])
def test_vallinam_b4_ignores_non_trigger_initials(text):
    assert not [f for f in scan(text)["findings"] if f["rule"] == "tamil.vallinam-missing"]


@pytest.mark.parametrize("text", ["என்னை, கொடு", "உங்களை; செய்தான்", "அவனை? தெரியும்"])
def test_vallinam_b4_ignores_a_punctuation_boundary(text):
    assert not [f for f in scan(text)["findings"] if f["rule"] == "tamil.vallinam-missing"]


@pytest.mark.parametrize("text", ["என்னைவிட வந்தான்", "அதைவிட செய்தான்", "அவனைப்போல தெரியும்"])
def test_vallinam_b4_does_not_generalize_to_lookalike_suffixed_forms(text):
    # என்னைவிட/அதைவிட/அவனைப்போல each tokenize as one word (letters+marks, no
    # internal whitespace), so none equals a bare trigger -- the same
    # exact-match mechanism, not a new exclusion list.
    assert not [f for f in scan(text)["findings"] if f["rule"] == "tamil.vallinam-missing"]


@pytest.mark.parametrize("text", ["என்னை", "உங்களை.", "அவனை?", "அதை!", "எதை"])
def test_vallinam_b4_trigger_at_end_of_verse_does_not_crash_or_flag(text):
    assert not [f for f in scan(text)["findings"] if f["rule"] == "tamil.vallinam-missing"]


def test_vallinam_b4_is_found_inside_inline_usfm():
    text = "\\wj அவனை சீக்கிரமாக\\wj*"
    [finding] = [f for f in scan(text)["findings"] if f["rule"] == "tamil.vallinam-missing"]
    assert finding["originalText"] == "அவனை சீக்கிரமாக" == text[finding["start"]:finding["end"]]


def test_vallinam_b4_matches_nfd_decomposed_trigger_text():
    nfd_text = unicodedata.normalize("NFD", "அவனை சீக்கிரமாக")
    findings = [f for f in scan(nfd_text)["findings"] if f["rule"] == "tamil.vallinam-missing"]
    assert len(findings) == 1
    assert findings[0]["originalText"] == nfd_text  # raw span, never rewritten to NFC


def test_vallinam_b4_finding_identity_is_stable_across_repeated_scans():
    first = next(f for f in scan("அவனை சீக்கிரமாக")["findings"] if f["rule"] == "tamil.vallinam-missing")
    second = next(f for f in scan("அவனை சீக்கிரமாக")["findings"] if f["rule"] == "tamil.vallinam-missing")
    assert first["id"] == second["id"]


def test_vallinam_b4_never_mutates_the_input_text():
    text = "அவனை சீக்கிரமாக"
    scan(text)
    assert text == "அவனை சீக்கிரமாக"


def test_vallinam_b4_coexists_with_b1_b2_b3_triggers_in_the_same_verse():
    result = scan("அந்த காகம் இப்படி செய்தான் எனக்கு கொடு அவனை சீக்கிரமாக.")
    findings = [f for f in result["findings"] if f["rule"] == "tamil.vallinam-missing"]
    assert {f["originalText"] for f in findings} == {
        "அந்த காகம்", "இப்படி செய்தான்", "எனக்கு கொடு", "அவனை சீக்கிரமாக",
    }


def test_vallinam_b1_b2_b3_regression_is_unaffected_by_b4():
    # B1/B2/B3's own tests above are untouched; this is a direct check that
    # adding B4's trigger words did not change any earlier behavior.
    b1 = [f for f in scan("அந்த காகம்")["findings"] if f["rule"] == "tamil.vallinam-missing"]
    assert len(b1) == 1 and b1[0]["originalText"] == "அந்த காகம்"
    b2 = [f for f in scan("அப்படி கூறினான்")["findings"] if f["rule"] == "tamil.vallinam-missing"]
    assert len(b2) == 1 and b2[0]["originalText"] == "அப்படி கூறினான்"
    b3 = [f for f in scan("எனக்கு கொடு")["findings"] if f["rule"] == "tamil.vallinam-missing"]
    assert len(b3) == 1 and b3[0]["originalText"] == "எனக்கு கொடு"


def test_other_scan_text_rules_default_suggested_replacement_to_none():
    # add()'s new parameter defaults to None for every rule that doesn't
    # explicitly pass one -- only தமிழ்.vallinam-missing does today.
    result = scan("மெல்ல மெல்ல தமிழ்a  ,,,")
    assert result["findings"]
    for finding in result["findings"]:
        if finding["rule"] != "tamil.vallinam-missing":
            assert finding["suggestedReplacement"] is None, finding


def test_vallinam_ignored_decision_suppresses_the_finding(tmp_path):
    finding_id = stable_finding_id("php", "1", "1", "tamil.vallinam-missing", "அந்த காகம்", 1)
    decisions = [{"issueKey": finding_id, "decision": "ignored"}]
    project = project_at(tmp_path, verses={"1": "அந்த காகம் பறந்தது."}, decisions=decisions)
    manager = LanguageQaManager(debounce=0, yield_seconds=0)
    manager.bind(project)
    result = wait(manager)
    assert not [f for f in result["findings"] if f["rule"] == "tamil.vallinam-missing"]


def test_vallinam_decision_on_one_occurrence_does_not_suppress_another(tmp_path):
    first_occurrence_id = stable_finding_id("php", "1", "1", "tamil.vallinam-missing", "அந்த காகம்", 1)
    decisions = [{"issueKey": first_occurrence_id, "decision": "ignored"}]
    project = project_at(tmp_path, verses={
        "1": "அந்த காகம் பறந்தது.", "2": "அந்த காகம் பறந்தது.",
    }, decisions=decisions)
    manager = LanguageQaManager(debounce=0, yield_seconds=0)
    manager.bind(project)
    result = wait(manager)
    matches = [f for f in result["findings"] if f["rule"] == "tamil.vallinam-missing"]
    assert len(matches) == 1 and matches[0]["verse"] == "2"


def test_verse_decide_ignored_actually_suppresses_a_vallinam_finding_through_the_real_dispatcher(fixture_project):
    # Mirrors test_terminology.py's own real-dispatcher test for the same
    # class of bug (decide_verse not invalidating Language QA) -- proves
    # the fix generalizes through the actual RPC path, not just the manager.
    engine = BridgeEngine()
    engine._language_qa = LanguageQaManager(debounce=0, yield_seconds=0)
    try:
        _r = call(engine, "project.open", {"path": str(fixture_project)})
        assert _r["success"], _r
        edit = call(engine, "verse.edit", {"chapter": "1", "verse": "1", "newText": "அந்த காகம் பறந்தது."})
        assert edit["success"]
        first = wait(engine._language_qa)
        finding = next(f for f in first["findings"] if f["rule"] == "tamil.vallinam-missing")
        assert finding["originalText"] == "அந்த காகம்"
        response = call(engine, "verse.decide", {
            "chapter": "1", "verse": "1", "findingId": finding["id"], "status": "ignored",
        })
        assert response["success"]
        after = wait(engine._language_qa)
        assert not [f for f in after["findings"] if f["rule"] == "tamil.vallinam-missing"]
    finally:
        engine._language_qa.unbind()


def issue_for(finding):
    """What the frontend sends with a Language QA verse.decide."""
    return {key: finding[key] for key in ("source", "rule", "ruleVersion", "originalText",
                                          "suggestedReplacement", "message", "start", "end")}


def test_every_language_qa_finding_carries_its_source(tmp_path):
    assert all(f["source"] == "languageQa" for f in scan("அந்த காகம்  ,,")["findings"])
    terms = [{"conceptId": "god", "status": "approved", "approvedRenderings": ["இறைவன்"],
              "rejectedRenderings": ["கடவுள்"], "note": ""}]
    verses = {str(n): "தமிழ்" for n in range(1, 7)} | {"7": "தமிழ", "8": "கடவுள்"}
    manager = LanguageQaManager(debounce=0, yield_seconds=0)
    manager.bind(project_at(tmp_path, verses=verses, terminology=terms))
    findings = wait(manager)["findings"]
    assert {"terminology.deprecated-form", "tamil.wordlist-variant"} <= {f["rule"] for f in findings}
    assert all(f["source"] == "languageQa" for f in findings)


def test_language_qa_decision_is_not_counted_until_a_check_job_reported_the_finding(fixture_project):
    engine = BridgeEngine()
    engine._language_qa = LanguageQaManager(debounce=0, yield_seconds=0)
    try:
        _r = call(engine, "project.open", {"path": str(fixture_project)})
        assert _r["success"], _r
        _r = call(engine, "verse.edit", {"chapter": "1", "verse": "1", "newText": "அந்த காகம் பறந்தது."})
        assert _r["success"], _r
        finding = next(f for f in wait(engine._language_qa)["findings"] if f["rule"] == "tamil.vallinam-missing")
        before = engine.project.load_progress_rollup()

        # "accepted" is recorded but is not a standing verdict: the finding stays.
        assert call(engine, "verse.decide", {"chapter": "1", "verse": "1", "findingId": finding["id"],
                                             "status": "accepted", "issue": issue_for(finding)})["success"]
        assert any(f["id"] == finding["id"] for f in wait(engine._language_qa)["findings"])
        assert call(engine, "verse.decide", {"chapter": "1", "verse": "1", "findingId": finding["id"],
                                             "status": "ignored", "issue": issue_for(finding)})["success"]

        # No finding row, no totals change, no verse entry: the rollup is untouched.
        assert engine.project.load_progress_rollup() == before
        # The decision itself is recorded, with what the reviewer saw.
        payload = engine.project.qa_decisions_for_verse("1", "1")[finding["id"]]
        assert payload["decision"] == "ignored"
        assert payload["issue"]["source"] == "languageQa"
        assert payload["issue"]["rule"] == "tamil.vallinam-missing"
        assert payload["issue"]["originalText"] == "அந்த காகம்"
        # And the next scan still suppresses the ignored occurrence.
        assert not [f for f in wait(engine._language_qa)["findings"] if f["id"] == finding["id"]]
    finally:
        engine._language_qa.unbind()


@pytest.mark.parametrize("issue", [None, {}, {"source": "greekRoom", "rule": "spelling"}])
def test_other_decisions_still_update_review_progress(fixture_project, issue):
    engine = BridgeEngine()
    _r = call(engine, "project.open", {"path": str(fixture_project)})
    assert _r["success"], _r
    params = {"chapter": "1", "verse": "1", "findingId": "greek-room-finding", "status": "accepted"}
    if issue is not None:
        params["issue"] = issue
    _r = call(engine, "verse.decide", params)
    assert _r["success"], _r
    rollup = engine.project.load_progress_rollup()
    assert rollup["chapters"]["1"]["verses"]["1"]["findings"]["greek-room-finding"] == "accepted"
    assert rollup["totals"]["approvedFindingCount"] == 1


# ---- Phase 4.1: Language QA as a check-job stage ----

@pytest.fixture
def staged_engine(fixture_project):
    from tests.support.projects import wait_for_job

    engine = BridgeEngine()
    engine._language_qa = LanguageQaManager(debounce=0, yield_seconds=0)
    _r = call(engine, "project.open", {"path": str(fixture_project)})
    assert _r["success"], _r
    _r = call(engine, "verse.edit", {"chapter": "1", "verse": "1", "newText": "அந்த காகம் பறந்தது."})
    assert _r["success"], _r
    wait(engine._language_qa)

    def run_job(checks, scope="book"):
        started = call(engine, "checks.start", {"scope": scope, "checks": checks})
        assert started["success"], started
        return wait_for_job(engine, started["result"]["jobId"], timeout=30)

    yield engine, run_job
    engine._language_qa.unbind()


def test_the_language_qa_stage_runs_in_a_book_job_and_counts_in_the_rollup(staged_engine):
    engine, run_job = staged_engine
    snapshot = run_job(["languageQa"])
    assert snapshot["state"] == "succeeded", snapshot
    result = snapshot["results"]["1:1"]
    [lqa] = [f for f in result["languageQa"]["findings"] if f["rule"] == "tamil.vallinam-missing"]
    assert result["findings"] == []  # QaFinding readers see no Language QA dicts
    assert snapshot["languageQa"]["state"] == "completed" and snapshot["languageQa"]["findings"] >= 1
    rollup = engine.project.load_progress_rollup()
    assert rollup["chapters"]["1"]["verses"]["1"]["findings"][lqa["id"]] == "open"
    open_before = rollup["totals"]["findingCount"] - rollup["totals"]["approvedFindingCount"]
    # Ignoring it now moves the open count down by one.
    assert call(engine, "verse.decide", {"chapter": "1", "verse": "1", "findingId": lqa["id"],
                                         "status": "ignored", "issue": issue_for(lqa)})["success"]
    totals = engine.project.load_progress_rollup()["totals"]
    assert totals["findingCount"] - totals["approvedFindingCount"] == open_before - 1
    # A re-run keeps the decision: the hidden finding is counted as ignored.
    rerun = run_job(["languageQa"])
    assert lqa["id"] in rerun["results"]["1:1"]["languageQa"]["decided"]
    assert engine.project.load_progress_rollup()["chapters"]["1"]["verses"]["1"]["findings"][lqa["id"]] == "ignored"


def test_a_language_qa_decision_never_marks_a_verse_reviewed_while_other_findings_are_open(staged_engine):
    engine, run_job = staged_engine
    snapshot = run_job(["languageQa"])
    lqa_ids = [f["id"] for f in snapshot["results"]["1:1"]["languageQa"]["findings"]]
    engine.project.record_progress_decision("1", "1", "greek-room-open", "open", verse_count=1)
    for finding_id in lqa_ids:
        finding = next(f for f in snapshot["results"]["1:1"]["languageQa"]["findings"] if f["id"] == finding_id)
        assert call(engine, "verse.decide", {"chapter": "1", "verse": "1", "findingId": finding_id,
                                             "status": "ignored", "issue": issue_for(finding)})["success"]
    assert engine.project.load_progress_rollup()["totals"]["reviewedVerseCount"] == 0
    assert call(engine, "verse.decide", {"chapter": "1", "verse": "1", "findingId": "greek-room-open",
                                         "status": "accepted"})["success"]
    assert engine.project.load_progress_rollup()["totals"]["reviewedVerseCount"] >= 1


def test_job_path_and_live_path_produce_identical_findings(staged_engine):
    engine, run_job = staged_engine
    live = {(f["chapter"], f["verse"], f["id"]) for f in wait(engine._language_qa)["findings"]}
    snapshot = run_job(["languageQa"], scope="chapter")
    job = {(r["chapter"], r["verse"], f["id"]) for r in snapshot["results"].values()
           for f in r["languageQa"]["findings"]}
    assert job == {item for item in live if item[0] == "1"}
    # The job pass reused the cache the live pass wrote: nothing was rescanned.
    assert engine._language_qa.status()["scannedVerses"] == 0


def test_language_qa_verse_lists_one_verses_findings_open_and_decided(staged_engine):
    engine, _ = staged_engine
    project = str(engine.project.path)
    first = call(engine, "languageQa.verse", {"projectPath": project, "chapter": "1", "verse": "1"})["result"]
    [lqa] = [f for f in first["findings"] if f["rule"] == "tamil.vallinam-missing"]
    assert first["state"] == "completed" and first["hidden"] == []
    assert call(engine, "verse.decide", {"chapter": "1", "verse": "1", "findingId": lqa["id"],
                                         "status": "rejected", "issue": issue_for(lqa)})["success"]
    wait(engine._language_qa)
    after = call(engine, "languageQa.verse", {"projectPath": project, "chapter": "1", "verse": "1"})["result"]
    assert lqa["id"] not in {f["id"] for f in after["findings"]}
    assert [(f["id"], f["decision"]) for f in after["hidden"]] == [(lqa["id"], "rejected")]
    assert not call(engine, "languageQa.verse", {"projectPath": "elsewhere", "chapter": "1", "verse": "1"})["success"]
    assert not call(engine, "languageQa.verse", {"projectPath": project, "chapter": 1, "verse": "1"})["success"]


def test_a_job_without_the_stage_carries_no_language_qa(staged_engine):
    _, run_job = staged_engine
    snapshot = run_job(["local"])
    assert "languageQa" not in snapshot and all("languageQa" not in r for r in snapshot["results"].values())


def test_a_decision_rescans_nothing_yet_takes_effect(fixture_project):
    # Phase 4.1: decisions are applied when a pass assembles its results, not
    # cached, so no decision -- Greek Room or Language QA -- rescans a verse.
    (fixture_project / "rut" / "2.json").write_text(
        json.dumps({"1": "அந்த காகம் பறந்தது."}, ensure_ascii=False), encoding="utf-8")
    engine = BridgeEngine()
    engine._language_qa = LanguageQaManager(debounce=0, yield_seconds=0)
    try:
        _r = call(engine, "project.open", {"path": str(fixture_project)})
        assert _r["success"], _r
        first = wait(engine._language_qa)
        assert first["totalChapters"] == 2 and first["scannedVerses"] > 0
        finding = next(f for f in first["findings"] if f["chapter"] == "2" and f["rule"] == "tamil.vallinam-missing")

        assert call(engine, "verse.decide", {"chapter": "1", "verse": "1", "findingId": "greek-room-finding",
                                             "status": "accepted"})["success"]
        assert engine.project.qa_decisions_for_verse("1", "1")["greek-room-finding"]["issue"] == {
            "source": "unspecified"}
        after_other = wait(engine._language_qa)
        assert (after_other["reusedChapters"], after_other["scannedVerses"]) == (2, 0)

        assert call(engine, "verse.decide", {"chapter": "2", "verse": "1", "findingId": finding["id"],
                                             "status": "ignored", "issue": issue_for(finding)})["success"]
        after_ignore = wait(engine._language_qa)
        assert (after_ignore["reusedChapters"], after_ignore["scannedVerses"]) == (2, 0)
        assert finding["id"] not in {f["id"] for f in after_ignore["findings"]}
    finally:
        engine._language_qa.unbind()


def test_results_persist_across_reopen_and_a_live_edit_rescans_only_that_verse(fixture_project):
    (fixture_project / "rut" / "2.json").write_text(json.dumps(
        {"1": "அந்த காகம் பறந்தது.", "2": "இந்த பெண் வந்தாள்."}, ensure_ascii=False), encoding="utf-8")
    engine = BridgeEngine()
    engine._language_qa = LanguageQaManager(debounce=0, yield_seconds=0)
    try:
        _r = call(engine, "project.open", {"path": str(fixture_project)})
        assert _r["success"], _r
        first = wait(engine._language_qa)
        assert first["scannedVerses"] == first["checkedVerses"] + first["skippedVerses"]
        assert engine.project.load_language_qa_cache().keys() == {"1", "2"}
        # Reopen: a new manager, nothing in memory, every verse from the workbench.
        engine._language_qa.unbind()
        engine._language_qa = LanguageQaManager(debounce=0, yield_seconds=0)
        _r = call(engine, "project.open", {"path": str(fixture_project)})
        assert _r["success"], _r
        reopened = wait(engine._language_qa)
        assert reopened["scannedVerses"] == 0 and reopened["storage"] == "Persisted in the project workbench."
        assert [f["id"] for f in reopened["findings"]] == [f["id"] for f in first["findings"]]
        # A live edit rescans the edited verse only (chapter 1 has alignment
        # data, so it is editable; chapter 2 here is text only).
        _r = call(engine, "verse.edit", {"chapter": "1", "verse": "1", "newText": "அந்த காகம் பறந்தது."})
        assert _r["success"], _r
        edited = wait(engine._language_qa)
        assert edited["scannedVerses"] == 1 and edited["reusedChapters"] == 1
        assert [f["rule"] for f in edited["findings"] if (f["chapter"], f["verse"]) == ("1", "1")] == [
            "tamil.vallinam-missing"]
    finally:
        engine._language_qa.unbind()


# ---- Phase 1.3/1.5: decisions on any finding; false positives; ignore-expiry ----

def fake(finding_id="f", pack=RULE_VERSION, revision=1):
    return {"id": finding_id, "packVersion": pack, "ruleRevision": revision}


@pytest.mark.parametrize("decision,expected", [
    (None, None),
    ({"decision": "accepted", "issue": {"packVersion": RULE_VERSION, "ruleRevision": 1}}, None),
    ({"decision": "ignored", "issue": {"packVersion": RULE_VERSION, "ruleRevision": 1}}, "suppress"),
    ({"decision": "rejected", "issue": {"packVersion": RULE_VERSION, "ruleRevision": 1}}, "suppress"),
    ({"decision": "ignored", "issue": {"packVersion": "language-qa-1", "ruleRevision": 1}}, "recheck"),
    ({"decision": "ignored", "issue": {"packVersion": RULE_VERSION, "ruleRevision": 2}}, "recheck"),
    ({"decision": "rejected", "issue": {"packVersion": "language-qa-1"}}, "recheck"),
    # Decisions from before Phase 1 recorded the pack version as ruleVersion.
    ({"decision": "ignored", "issue": {"source": "languageQa", "ruleVersion": "language-qa-6"}}, "recheck"),
    ({"decision": "ignored", "issue": {"source": "languageQa", "ruleVersion": RULE_VERSION}}, "suppress"),
    # A pack rule's legacy ruleVersion is "<pack>@<version>#<revision>": both parts are compared.
    ({"decision": "ignored", "issue": {"ruleVersion": f"{RULE_VERSION}#1"}}, "suppress"),
    ({"decision": "ignored", "issue": {"ruleVersion": f"{RULE_VERSION}#2"}}, "recheck"),
    ({"decision": "ignored", "issue": {"ruleVersion": "ta-irv@0.9.0#1"}}, "recheck"),
    # No version at all (legacy, or a caller that sent no issue): cannot be compared, still suppresses.
    ({"decision": "ignored", "issue": {}}, "suppress"),
    ({"decision": "ignored", "issue": {"source": "unspecified"}}, "suppress"),
    ({"decision": "ignored"}, "suppress"),
])
def test_decision_effect(decision, expected):
    assert decision_effect(fake(), decision) == expected


def test_apply_decisions_keeps_false_positives_aside_and_flags_rechecks():
    findings = [fake("a"), fake("b"), fake("c"), fake("d")]
    decisions = {
        "a": {"decision": "ignored", "issue": {"packVersion": RULE_VERSION, "ruleRevision": 1}},
        "b": {"decision": "rejected", "issue": {"packVersion": RULE_VERSION, "ruleRevision": 1}},
        "c": {"decision": "ignored", "issue": {"packVersion": "language-qa-1"}},
    }
    false_positives = []
    shown = apply_decisions(findings, decisions, false_positives)
    assert [f["id"] for f in shown] == ["c", "d"]
    assert shown[0]["previouslyIgnored"] is True and "previouslyIgnored" not in shown[1]
    assert [f["id"] for f in false_positives] == ["b"]


def decide(engine, finding, status, **issue_overrides):
    response = call(engine, "verse.decide", {"chapter": finding["chapter"], "verse": finding["verse"],
                                             "findingId": finding["id"], "status": status,
                                             "issue": {**issue_for(finding), **issue_overrides}})
    assert response["success"], response
    return response


def phase1_issue(finding, chosen=None):
    """The issue the frontend now sends (findingActions.languageQaDecisionIssue)."""
    return {**issue_for(finding), "ruleId": finding["ruleId"], "packVersion": finding["packVersion"],
            "ruleRevision": finding["ruleRevision"], "layer": finding["layer"], "category": finding["category"],
            "chosenSuggestion": chosen["text"] if chosen else None, "chosenRank": chosen["rank"] if chosen else None}


@pytest.fixture
def vallinam_engine(fixture_project):
    engine = BridgeEngine()
    engine._language_qa = LanguageQaManager(debounce=0, yield_seconds=0)
    _r = call(engine, "project.open", {"path": str(fixture_project)})
    assert _r["success"], _r
    # The dative rule: inline on its human precision, so the inline list sees it.
    _r = call(engine, "verse.edit", {"chapter": "1", "verse": "1", "newText": "அவனுக்கு பதில் சொன்னான்,,."})
    assert _r["success"], _r
    finding = next(f for f in wait(engine._language_qa)["findings"] if f["rule"] == "tamil.vallinam-missing")
    yield engine, finding, fixture_project
    engine._language_qa.unbind()


def status_view(engine, project, view):
    response = call(engine, "languageQa.status", {"projectPath": str(project), "limit": 100, "view": view})
    assert response["success"], response
    return response["result"]


def test_a_false_positive_is_hidden_and_listed_on_its_own(vallinam_engine):
    engine, finding, project = vallinam_engine
    decide(engine, finding, "rejected", **phase1_issue(finding))
    after = wait(engine._language_qa)
    assert finding["id"] not in {f["id"] for f in after["findings"]}
    assert after["falsePositiveCount"] == 1 and "falsePositives" not in after
    listed = status_view(engine, project, "falsePositives")
    assert [f["id"] for f in listed["findings"]] == [finding["id"]] and listed["totalFindings"] == 1
    assert finding["id"] not in {f["id"] for f in engine._language_qa.inline()["findings"]}


def test_decisions_now_apply_to_every_rule_not_only_the_inline_two(vallinam_engine):
    engine, _, _ = vallinam_engine
    # A panel-only rule (the fixture's ",,"): decisions apply to every rule.
    spacing = next(f for f in wait(engine._language_qa)["findings"] if f["rule"] == "punctuation.repeated")
    decide(engine, spacing, "ignored", **phase1_issue(spacing))
    assert spacing["id"] not in {f["id"] for f in wait(engine._language_qa)["findings"]}


def test_an_ignore_under_an_older_pack_version_comes_back_for_rechecking(vallinam_engine):
    engine, finding, project = vallinam_engine
    decide(engine, finding, "ignored", **{**phase1_issue(finding), "packVersion": "language-qa-1"})
    after = wait(engine._language_qa)
    [back] = [f for f in after["findings"] if f["id"] == finding["id"]]
    assert back["previouslyIgnored"] is True and after["recheckCount"] == 1
    recheck = status_view(engine, project, "recheck")
    assert [f["id"] for f in recheck["findings"]] == [finding["id"]]
    assert [f["id"] for f in engine._language_qa.inline()["findings"]] == [finding["id"]]
    # Deciding it again under the current version suppresses it again.
    decide(engine, finding, "ignored", **phase1_issue(finding))
    again = wait(engine._language_qa)
    assert finding["id"] not in {f["id"] for f in again["findings"]} and again["recheckCount"] == 0


def test_an_ignore_under_an_older_rule_revision_comes_back_for_rechecking(vallinam_engine):
    engine, finding, _ = vallinam_engine
    decide(engine, finding, "ignored", **{**phase1_issue(finding), "ruleRevision": finding["ruleRevision"] + 1})
    [back] = [f for f in wait(engine._language_qa)["findings"] if f["id"] == finding["id"]]
    assert back["previouslyIgnored"] is True


def test_status_rejects_an_unknown_view(vallinam_engine):
    engine, _, project = vallinam_engine
    response = call(engine, "languageQa.status", {"projectPath": str(project), "view": "everything"})
    assert not response["success"]


def test_status_states_what_language_qa_does_and_does_not_check(vallinam_engine):
    """Phase 7: the coverage block is structured, bilingual, and covers every
    category, so the panel's in-scope list can never drift from the engine."""
    engine, _, project = vallinam_engine
    response = call(engine, "languageQa.status", {"projectPath": str(project)})
    assert response["success"]
    cover = response["result"]["coverage"]
    # A Tamil project's statement; a profile pack lists its own categories.
    assert [row["category"] for row in cover["inScope"]] == list(DEFAULT_COVERAGE)
    assert {row["category"] for row in cover["outOfScope"]} >= {
        "agreement", "meaning-shift", "omission-addition", "textual-basis"}
    for row in cover["inScope"] + cover["outOfScope"]:
        assert row["label"] and row["labelTa"]
    for row in cover["outOfScope"]:
        assert row["reason"] and row["reasonTa"]
    assert not {row["category"] for row in cover["outOfScope"]} & set(CATEGORIES)
    assert (REPO_ROOT / cover["handOff"]).is_file()


# ---- Phase 1.4: decision history ----

def test_history_lists_every_decision_on_a_finding_in_order(vallinam_engine):
    engine, finding, project = vallinam_engine
    chosen = finding["suggestions"][0]
    decide(engine, finding, "ignored", **phase1_issue(finding))
    decide(engine, finding, "rejected", **phase1_issue(finding))
    decide(engine, finding, "accepted", **phase1_issue(finding, chosen))
    # A Greek Room decision on the same verse is not Language QA history.
    _r = call(engine, "verse.decide", {"chapter": "1", "verse": "1", "findingId": "gr-1", "status": "accepted"})
    assert _r["success"], _r
    response = call(engine, "languageQa.history", {"projectPath": str(project), "chapter": "1", "verse": "1",
                                                   "findingId": finding["id"]})
    assert response["success"], response
    entries = response["result"]["entries"]
    assert [e["decision"] for e in entries] == ["ignored", "rejected", "accepted"]
    assert [e["seq"] for e in entries] == sorted(e["seq"] for e in entries)
    assert entries[-1]["chosenSuggestion"] == chosen["text"] and entries[-1]["chosenRank"] == 1
    assert [e["revision"] for e in entries] == [1, 2, 3]
    assert all(e["ruleId"] == "ta-irv/sandhi.vallinam.dative" and e["recordedAt"] for e in entries)
    verse = call(engine, "languageQa.history", {"projectPath": str(project), "chapter": "1", "verse": "1"})
    assert {e["findingId"] for e in verse["result"]["entries"]} == {finding["id"]}


@pytest.mark.parametrize("params", [
    {"chapter": 1, "verse": "1"}, {"chapter": "1"}, {"chapter": "1", "verse": "1", "findingId": 3},
])
def test_history_validates_its_parameters(vallinam_engine, params):
    engine, _, project = vallinam_engine
    assert not call(engine, "languageQa.history", {"projectPath": str(project), **params})["success"]


def test_history_is_project_guarded(vallinam_engine):
    engine, _, _ = vallinam_engine
    response = call(engine, "languageQa.history", {"projectPath": "C:/elsewhere", "chapter": "1", "verse": "1"})
    assert not response["success"]


# ---- Phase 1.6: the Settings pane never replaces a termbase rule silently ----

def test_terminology_record_refuses_to_replace_without_overwrite(fixture_project):
    engine = BridgeEngine()
    _r = call(engine, "project.open", {"path": str(fixture_project)})
    assert _r["success"], _r
    first = call(engine, "terminology.record", {"conceptId": "god", "approvedRenderings": ["இறைவன்"],
                                                "rejectedRenderings": ["கடவுள்"]})
    assert first["success"] and "conflict" not in first["result"]
    second = call(engine, "terminology.record", {"conceptId": "god", "approvedRenderings": ["தேவன்"],
                                                 "rejectedRenderings": []})
    assert second["success"]
    assert second["result"]["conflict"]["approvedRenderings"] == ["இறைவன்"]
    [rule] = [r for r in engine.project.terminology_rules() if r["conceptId"] == "god"]
    assert rule["approvedRenderings"] == ["இறைவன்"]  # nothing written
    third = call(engine, "terminology.record", {"conceptId": "god", "approvedRenderings": ["தேவன்"],
                                                "rejectedRenderings": [], "overwrite": True})
    assert third["success"] and "conflict" not in third["result"]
    [rule] = [r for r in engine.project.terminology_rules() if r["conceptId"] == "god"]
    assert rule["approvedRenderings"] == ["தேவன்"]
    assert not call(engine, "terminology.record", {"conceptId": "god", "approvedRenderings": ["x"],
                                                   "overwrite": "yes"})["success"]


def test_verse_decide_rejects_a_non_object_issue(fixture_project):
    engine = BridgeEngine()
    _r = call(engine, "project.open", {"path": str(fixture_project)})
    assert _r["success"], _r
    response = call(engine, "verse.decide", {"chapter": "1", "verse": "1", "findingId": "x",
                                             "status": "ignored", "issue": "languageQa"})
    assert not response["success"]
    assert engine.project.qa_decisions_for_verse("1", "1") == {}


def test_detection_metadata_conflicts_shared_scripts_and_mixed_input():
    tamil = "தமிழ் மொழியில் எழுதப்பட்ட உரை. " * 10
    assert detect_language(tamil)["pack"] == "ta-irv"
    assert detect_language(tamil)["basis"] == "script-suggestion"
    assert detect_language(tamil, "ta-IN")["language"] == "ta"
    assert detect_language(tamil, "tam")["pack"] == "ta-irv"
    assert detect_language(tamil, "hin")["basis"] == "metadata-conflict"
    assert detect_language(tamil, "hin")["pack"] == "common"
    assert detect_language("देवनागरी " * 20)["language"] == "und"
    assert detect_language("देवनागरी " * 20, "mar")["language"] == "mar"
    assert detect_language("hello world " * 20, "ta-Latn")["pack"] == "common"
    assert detect_language("தமிழ் abcde " * 20)["basis"] == "mixed-script"
    assert detect_language("123")["language"] == "und"


def test_a_conflict_runs_common_checks_and_names_both_languages():
    detection = detect_language("தமிழ் மொழியில் எழுதப்பட்ட உரை. " * 10, "hin")
    assert detection["pack"] == "common" and detection["basis"] == "metadata-conflict"
    assert "hin" in detection["message"] and "Devanagari" in detection["message"] and "Tamil" in detection["message"]


def test_a_shared_script_never_suggests_a_pack_without_metadata():
    # Devanagari is written by Hindi, Marathi, Nepali and Sanskrit: never guessed.
    assert detect_language("देवनागरी लिपि में लिखा पाठ " * 10)["pack"] == "common"
    # A language with no registered pack gets the common checks, however clear its script.
    # (Malayalam got the ml-irv pack on 2026-10-07; Bengali has none.)
    assert detect_language("বাংলা লিপিতে লেখা পাঠ্য " * 10, "bn")["pack"] == "common"


def test_wordlist_findings_flags_a_rare_pulli_variant_of_a_common_word():
    common, rare = "தமிழ்", "தமிழ"  # differ by exactly one pulli
    assert len(common) >= WORDLIST_MIN_LENGTH and len(rare) >= WORDLIST_MIN_LENGTH
    counts = {common: WORDLIST_COMMON_MIN, rare: 1}
    first_seen = {
        common: ("1", "1", 0, len(common), common, "hash-common"),
        rare: ("1", "2", 3, 3 + len(rare), rare, "hash-rare"),
    }
    findings = wordlist("php", counts, first_seen)
    assert len(findings) == 1
    finding = findings[0]
    assert finding["rule"] == "tamil.wordlist-variant"
    assert finding["severity"] == "low" and finding["status"] == "review-needed"
    assert finding["chapter"] == "1" and finding["verse"] == "2"
    assert finding["start"] == 3 and finding["end"] == 3 + len(rare)
    assert finding["originalText"] == rare  # the raw span, not the NFC counting key
    assert finding["textHash"] == "hash-rare"
    assert common in finding["message"] and str(WORDLIST_COMMON_MIN) in finding["message"]


def test_wordlist_findings_never_flags_rarity_alone():
    rare = "தமிழக"  # length >= WORDLIST_MIN_LENGTH, no similar word present at all
    counts = {rare: 1}
    first_seen = {rare: ("1", "1", 0, len(rare), rare, "hash")}
    assert wordlist("php", counts, first_seen) == []


def test_wordlist_findings_never_flags_similarity_alone():
    a, b = "தமிழ்", "தமிழ"
    counts = {a: WORDLIST_COMMON_MIN, b: WORDLIST_COMMON_MIN}  # both common, neither rare
    first_seen = {
        a: ("1", "1", 0, len(a), a, "hash-a"),
        b: ("1", "2", 0, len(b), b, "hash-b"),
    }
    assert wordlist("php", counts, first_seen) == []


def test_wordlist_findings_ignores_words_shorter_than_the_minimum():
    common, rare = "தமி", "தம"
    assert len(common) < WORDLIST_MIN_LENGTH and len(rare) < WORDLIST_MIN_LENGTH
    counts = {common: WORDLIST_COMMON_MIN, rare: 1}
    first_seen = {
        common: ("1", "1", 0, len(common), common, "hash-common"),
        rare: ("1", "2", 0, len(rare), rare, "hash-rare"),
    }
    assert wordlist("php", counts, first_seen) == []


def test_wordlist_findings_tie_break_is_deterministic():
    rare = "தமிழ"
    higher_count, lower_count = "தமிழ்", "தமிள"  # insertion vs. substitution, both edit-distance-1
    counts = {rare: 1, higher_count: WORDLIST_COMMON_MIN + 6, lower_count: WORDLIST_COMMON_MIN + 2}
    first_seen = {
        rare: ("1", "1", 0, len(rare), rare, "hash-rare"),
        higher_count: ("1", "2", 0, len(higher_count), higher_count, "hash-a"),
        lower_count: ("1", "3", 0, len(lower_count), lower_count, "hash-b"),
    }
    findings = wordlist("php", counts, first_seen)
    assert len(findings) == 1
    assert higher_count in findings[0]["message"]  # higher count wins over lexical order
    # Equal counts: lexicographically smaller string wins instead.
    counts[higher_count] = counts[lower_count]
    findings = wordlist("php", counts, first_seen)
    assert lower_count in findings[0]["message"]
    assert lower_count < higher_count


def project_at(root, text="தமிழ் தமிழ்  ", book="php", verses=None, terminology=None, decisions=None):
    folder = root / book
    folder.mkdir(parents=True)
    (folder / "1.json").write_text(json.dumps(verses or {"3a": text}, ensure_ascii=False), encoding="utf-8")
    namespace = SimpleNamespace(path=root, book_id=book, book_dir=folder,
                                manifest={"target_language": {"id": "tam"}})
    if terminology is not None:
        namespace.terminology_rules = terminology if callable(terminology) else (lambda: terminology)
    if decisions is not None:
        namespace.project_qa_decisions = decisions if callable(decisions) else (lambda: decisions)
    return namespace


def wait(manager, state="completed"):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        status = manager.status(limit=100)
        if status["state"] == state:
            return status
        assert status["state"] != "failed", status
        time.sleep(.005)
    pytest.fail(f"Language QA did not reach {state}: {manager.status()}")


def test_wordlist_audit_end_to_end_via_manager(tmp_path):
    project = project_at(tmp_path, verses={"1": "தமிழ்", "2": "தமிழ்", "3": "தமிழ்"})
    (project.book_dir / "2.json").write_text(json.dumps(
        {"1": "தமிழ்", "2": "தமிழ்", "3": "தமிழ்", "4": "தமிழ"}, ensure_ascii=False), encoding="utf-8")
    manager = LanguageQaManager(debounce=0, yield_seconds=0)
    manager.bind(project)
    result = wait(manager)
    variants = [f for f in result["findings"] if f["rule"] == "tamil.wordlist-variant"]
    assert len(variants) == 1
    assert variants[0]["chapter"] == "2" and variants[0]["verse"] == "4"
    assert variants[0]["originalText"] == "தமிழ"


def test_wordlist_audit_excludes_words_from_verses_with_limitations(tmp_path):
    # A verse that is not checked (here: an unbalanced \wj) must not
    # contribute its words to the book-wide wordlist audit.
    verses = {str(n): "தமிழ்" for n in range(1, 7)}  # 6 occurrences: satisfies WORDLIST_COMMON_MIN
    verses["7"] = "\\wj தமிழ"
    project = project_at(tmp_path, verses=verses)
    manager = LanguageQaManager(debounce=0, yield_seconds=0)
    manager.bind(project)
    result = wait(manager)
    assert not [f for f in result["findings"] if f["rule"] == "tamil.wordlist-variant"]
    assert any("7: Unbalanced \\wj: 1 open, 0 close; verse not checked." in m for m in result["limitations"])


def test_wordlist_audit_counts_words_inside_balanced_inline_usfm_at_raw_offsets(tmp_path):
    verses = {str(n): "தமிழ்" for n in range(1, 7)}
    verses["7"] = "\\wj தமிழ\\wj*"
    project = project_at(tmp_path, verses=verses)
    manager = LanguageQaManager(debounce=0, yield_seconds=0)
    manager.bind(project)
    result = wait(manager)
    [variant] = [f for f in result["findings"] if f["rule"] == "tamil.wordlist-variant"]
    assert variant["verse"] == "7"
    assert variant["originalText"] == "தமிழ" == verses["7"][variant["start"]:variant["end"]]


def test_wordlist_audit_skips_when_book_scan_is_truncated(tmp_path):
    project = project_at(tmp_path, verses={str(n): "�" * 100 for n in range(100)})
    manager = LanguageQaManager(debounce=0, yield_seconds=0)
    manager.bind(project)
    result = wait(manager)
    assert result["totalFindings"] == MAX_BOOK_FINDINGS
    assert any("Wordlist audit skipped: book scan was truncated." in m for m in result["limitations"])


def test_wordlist_finding_id_is_stable_when_its_anchor_occurrence_moves(tmp_path):
    project = project_at(tmp_path, verses={"1": "தமிழ"})
    (project.book_dir / "2.json").write_text(json.dumps(
        {"1": "தமிழ்", "2": "தமிழ்", "3": "தமிழ்", "4": "தமிழ்", "5": "தமிழ்", "6": "தமிழ்"},
        ensure_ascii=False), encoding="utf-8")
    manager = LanguageQaManager(debounce=0, yield_seconds=0)
    manager.bind(project)
    first = wait(manager)
    before = next(f for f in first["findings"] if f["rule"] == "tamil.wordlist-variant")
    assert before["chapter"] == "1" and before["verse"] == "1"
    # Move the rare word's only occurrence to a different verse in the same chapter.
    (project.book_dir / "1.json").write_text(
        json.dumps({"1": "புதிய வரி", "2": "தமிழ"}, ensure_ascii=False), encoding="utf-8")
    manager.invalidate("1")
    after = wait(manager)
    after_finding = next(f for f in after["findings"] if f["rule"] == "tamil.wordlist-variant")
    assert after_finding["verse"] == "2"
    assert after_finding["id"] == before["id"]


def test_background_external_edits_and_no_writes(tmp_path):
    project = project_at(tmp_path)
    manager = LanguageQaManager(debounce=0, yield_seconds=0)
    source = project.book_dir / "1.json"
    before = source.read_bytes()
    manager.bind(project)
    first = wait(manager)
    assert first["totalFindings"] == 1  # spacing.extra; the repeated word is deliberate reduplication
    assert first["findings"][0]["verse"] == "3a"
    assert source.read_bytes() == before
    assert list(tmp_path.rglob("*")) == [project.book_dir, source]
    source.write_text('{"3a":"clean"}', encoding="utf-8")
    manager._last_scan = 0
    assert manager.status()["state"] == "queued"
    assert wait(manager)["totalFindings"] == 0


def test_completed_scan_stays_completed_when_idle_refresh_finds_no_change(tmp_path):
    project = project_at(tmp_path)
    manager = LanguageQaManager(debounce=0, yield_seconds=0)
    manager.bind(project)
    completed = wait(manager)
    generation = completed["generation"]
    manager._last_scan = 0
    refreshed = manager.status(limit=100)
    assert refreshed["state"] == "completed"
    assert refreshed["generation"] == generation
    assert manager._thread is None


def test_idle_refresh_rechecks_an_ordinary_external_edit(tmp_path):
    project = project_at(tmp_path, text="a  b")
    manager = LanguageQaManager(debounce=0, yield_seconds=0)
    manager.bind(project)
    assert wait(manager)["totalFindings"] == 1
    path = project.book_dir / "1.json"
    path.write_text('{"3a":"clean"}', encoding="utf-8")
    manager._last_scan = 0
    assert manager.status()["state"] == "queued"
    assert wait(manager)["totalFindings"] == 0


def test_pause_keeps_results_visible_and_resume_with_no_changes_skips_rescan(tmp_path):
    project = project_at(tmp_path)
    manager = LanguageQaManager(debounce=0, yield_seconds=0)
    manager.bind(project)
    completed = wait(manager)
    manager.pause(True)
    paused = manager.status(limit=100)
    assert paused["state"] == "paused"
    assert paused["findings"] == completed["findings"]
    # A book where every chapter carries a per-verse limitation (e.g. inline
    # USFM) never populates _scan's chapter cache, so an unconditional
    # reschedule on resume would redo that same, unchanged pass and look
    # like the whole book restarting from zero.
    resumed = manager.pause(False)
    assert resumed["state"] == "completed"  # returned synchronously: no rescan thread ran
    assert manager.status(limit=100)["findings"] == completed["findings"]
    assert manager._thread is None


def test_resume_after_an_external_edit_made_while_paused_rescans(tmp_path):
    project = project_at(tmp_path)
    manager = LanguageQaManager(debounce=0, yield_seconds=0)
    manager.bind(project)
    wait(manager)
    manager.pause(True)
    (project.book_dir / "1.json").write_text('{"3a":"clean"}', encoding="utf-8")
    resumed = manager.pause(False)
    assert resumed["state"] == "queued"
    assert wait(manager)["totalFindings"] == 0


def test_edit_switch_and_pause_discard_inflight_results(tmp_path, monkeypatch):
    from tc_ai_bridge import language_qa_jobs as jobs
    original = jobs.scan_text
    entered, release = threading.Event(), threading.Event()
    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(jobs, "scan_text", blocked)
    first = project_at(tmp_path / "first")
    second = project_at(tmp_path / "second", text="சரியான உரை")
    manager = LanguageQaManager(debounce=0, yield_seconds=0)
    manager.bind(first)
    assert entered.wait(5)
    worker = manager._thread
    start = time.perf_counter()
    manager.pause(True)
    assert manager.status()["state"] == "paused"
    manager.bind(second)
    assert manager._thread is worker  # Never spawn a second worker.
    manager.invalidate("1")
    assert manager.status()["findings"] == []
    assert time.perf_counter() - start < .25
    release.set()
    final = wait(manager)
    assert final["projectPath"] == str(second.path)
    assert final["totalFindings"] == 0


def test_paused_edits_remain_pending_until_resume(tmp_path):
    manager = LanguageQaManager(debounce=.05, yield_seconds=0)
    project = project_at(tmp_path)
    manager.bind(project)
    manager.pause(True)
    manager.invalidate("1")
    time.sleep(.06)
    assert manager.status()["state"] == "paused"
    manager.pause(False)
    assert wait(manager)["totalFindings"] == 1


@pytest.mark.parametrize("payload", [b"{bad json", b"\xff", b"[]", b" " * (MAX_CHAPTER_BYTES + 1),
                                    b'{"1":"first", "1":"duplicate"}'],
                         ids=["bad-json", "bad-utf8", "wrong-shape", "oversize", "duplicate-verse"])
def test_unreadable_chapter_is_incomplete_not_clean(tmp_path, payload):
    project = project_at(tmp_path)
    (project.book_dir / "1.json").write_bytes(payload)
    manager = LanguageQaManager(debounce=0, yield_seconds=0)
    manager.bind(project)
    result = wait(manager)
    assert result["incomplete"] and result["limitations"]
    assert result["checkedVerses"] == 0


def test_book_limits_and_status_page_are_bounded(tmp_path):
    project = project_at(tmp_path, verses={str(n): "�" * 100 for n in range(100)})
    manager = LanguageQaManager(debounce=0, yield_seconds=0)
    manager.bind(project)
    result = wait(manager)
    assert result["totalFindings"] == 3000
    assert result["incomplete"]
    assert len(manager.status(limit=10000)["findings"]) == 100
    assert manager.status()["findings"] == []
    assert manager.status(offset=5000, limit=100)["findings"] == []


def _three_chapter_project(root, verses_per_chapter=60):
    # Every verse yields one inline finding (the dative வல்லினம் rule,
    # "அவனுக்கு பதில்", inline on its human precision) and one panel-only
    # finding (punctuation.repeated, the ",,"), so the book holds 180 inline
    # findings -- well past one 100-finding page.
    project = project_at(root, verses={str(n): "அவனுக்கு பதில்,," for n in range(1, verses_per_chapter + 1)})
    for chapter in ("2", "3"):
        (project.book_dir / f"{chapter}.json").write_text(json.dumps(
            {str(n): "அவனுக்கு பதில்,," for n in range(1, verses_per_chapter + 1)},
            ensure_ascii=False), encoding="utf-8")
    return project


def test_inline_returns_a_whole_chapter_past_the_status_page(tmp_path):
    manager = LanguageQaManager(debounce=0, yield_seconds=0)
    manager.bind(_three_chapter_project(tmp_path))
    status = wait(manager)
    assert status["totalFindings"] == 360
    # The panel's page is still a page: 100 rows, starting at chapter 1.
    page = manager.status(offset=0, limit=100)
    assert len(page["findings"]) == 100
    assert {f["chapter"] for f in page["findings"]} == {"1"}
    # Chapter 3 lies entirely past that page, and inline still returns all of it.
    inline = manager.inline(chapter="3")
    assert len(inline["findings"]) == 60
    assert {f["chapter"] for f in inline["findings"]} == {"3"}
    assert {f["rule"] for f in inline["findings"]} == {"tamil.vallinam-missing"}
    assert inline["chapter"] == "3" and inline["state"] == "completed"
    assert inline["generation"] == status["generation"]


def test_inline_without_a_chapter_returns_the_whole_book_and_only_inline_rules(tmp_path):
    manager = LanguageQaManager(debounce=0, yield_seconds=0)
    manager.bind(_three_chapter_project(tmp_path))
    wait(manager)
    inline = manager.inline()
    assert len(inline["findings"]) == 180
    assert all(f["inline"] for f in inline["findings"])
    drawn = inline_rule_names(ta_pack())
    assert {f["rule"] for f in inline["findings"]} <= set(drawn)
    assert inline["chapter"] is None
    # The drawn rules: the in-code INLINE_RULES plus the pack's inline rules
    # (inline on human-labelled precision, DECISIONS.md 2026-09-28).
    assert inline["inlineRules"] == drawn
    assert manager.status()["inlineRules"] == drawn
    # The migrated வல்லினம் rules keep their legacy name; the lexicon rules
    # moved into the pack (2026-09-30) keep theirs.
    assert set(drawn) == INLINE_RULES | {"tamil.vallinam-missing", "sandhi.vallinam.wrong-consonant",
                                         "sandhi.compound.direction", "typo.suffix.dropped-tha",
                                         "lexicon.known-misspelling", "lexicon.known-split"}
    # Common checks only: the in-code inline rules alone.
    assert inline_rule_names(None) == sorted(INLINE_RULES)


def test_category_marks_match_the_engine():
    # highlight.ts maps each category to a CSS class; the engine owns the
    # category list and decides which findings are inline.
    source = (REPO_ROOT / "src" / "lib" / "utils" / "highlight.ts").read_text(encoding="utf-8")
    block = re.search(r"LANGUAGE_QA_CATEGORY_MARKS[^=]*=\s*\{(.*?)\};", source, re.S)
    assert block, "LANGUAGE_QA_CATEGORY_MARKS not found in highlight.ts"
    keys = set(re.findall(r'^\s*"?([a-z-]+)"?\s*:', block.group(1), re.M))
    assert keys == set(CATEGORIES)


def test_frontend_types_list_the_engine_vocabulary():
    # languageQa.ts's unions must match the engine's layer/category/confidence vocabularies.
    source = (REPO_ROOT / "src" / "lib" / "types" / "languageQa.ts").read_text(encoding="utf-8")

    def union(name):
        match = re.search(rf"type {name} =\s*(.*?);", source, re.S)
        assert match, name
        return set(re.findall(r'"([^"]+)"', match.group(1)))

    assert union("LanguageQaLayer") == set(LAYERS)
    assert union("LanguageQaCategory") == set(CATEGORIES)
    assert union("LanguageQaConfidence") == set(CONFIDENCES)


def test_every_rule_has_valid_metadata_and_inline_flags_follow_the_engine_list():
    for rule, meta in RULES.items():
        assert meta.layer in LAYERS and meta.category in CATEGORIES and meta.confidence in CONFIDENCES, rule
        assert meta.pack in {"common", "project"} and meta.revision >= 1, rule
    assert INLINE_RULES <= set(RULES)
    for pack_rule in ta_pack().rules:
        assert pack_rule.layer in LAYERS and pack_rule.category in CATEGORIES, pack_rule.id
        assert pack_rule.confidence in CONFIDENCES, pack_rule.id
        # Inline is decided by the human-label gate (language_qa_benchmark.human_gate), never a sign-off.
        assert "inlineSignOff" not in pack_rule.source, pack_rule.id


FINDING_FIELDS = {"id", "book", "chapter", "verse", "rule", "severity", "start", "end", "originalText",
                  "message", "textHash", "ruleVersion", "status", "suggestedReplacement", "source",
                  "layer", "category", "confidence", "suggestions", "ruleId", "packVersion",
                  "ruleRevision", "inline"}


def assert_finding_shape(finding):
    assert set(finding) == FINDING_FIELDS, set(finding) ^ FINDING_FIELDS
    pack = ta_pack()
    pack_rule = pack.by_id(finding["ruleId"].split("/", 1)[1]) if finding["ruleId"].startswith("ta-irv/") else None
    if pack_rule is not None:
        # A pack rule: its metadata and versions come from the pack.
        assert finding["rule"] == pack_rule.name
        assert (finding["layer"], finding["category"], finding["confidence"], finding["severity"]) == (
            pack_rule.layer, pack_rule.category, pack_rule.confidence, pack_rule.severity)
        # A rule moved out of the engine's code keeps the stamp it had there (legacyVersion).
        pack_version = pack_rule.legacy_version or pack.pack_version
        assert finding["packVersion"] == pack_version and finding["ruleRevision"] == pack_rule.version
        assert finding["ruleVersion"] == (pack_rule.legacy_version or f"{pack.pack_version}#{pack_rule.version}")
        assert finding["inline"] is pack_rule.inline
    else:
        meta = RULES[finding["rule"]]
        assert (finding["layer"], finding["category"], finding["confidence"]) == (meta.layer, meta.category, meta.confidence)
        assert finding["ruleId"] == f'{meta.pack}/{finding["rule"]}'
        assert finding["packVersion"] == RULE_VERSION and finding["ruleRevision"] == meta.revision
        assert finding["inline"] is (finding["rule"] in INLINE_RULES)
    assert finding["source"] == "languageQa"
    ranks = [s["rank"] for s in finding["suggestions"]]
    assert ranks == list(range(1, len(ranks) + 1)) and len(ranks) <= 5
    for s in finding["suggestions"]:
        assert set(s) == {"text", "rank", "source", "rationale"} and s["text"] and s["rationale"]
    assert finding["suggestedReplacement"] == (finding["suggestions"][0]["text"] if finding["suggestions"] else None)


def test_every_scan_text_finding_has_the_layered_shape():
    text = "மெல்ல மெல்ல தமிழ்a  ,,,‍ அந்த காகம் க்்"
    findings = scan(text)["findings"]
    assert {"tamil.mixed-word", "punctuation.repeated", "spacing.extra",
            "unicode.invisible", "unicode.private-use", "tamil.vallinam-missing"} <= {f["rule"] for f in findings}
    for finding in findings:
        assert_finding_shape(finding)
    [vallinam] = [f for f in findings if f["rule"] == "tamil.vallinam-missing"]
    assert vallinam["ruleId"] == "ta-irv/sandhi.vallinam.demonstrative" and vallinam["category"] == "sandhi"
    assert vallinam["suggestions"] == [{"text": "அந்தக் காகம்", "rank": 1, "source": "rule",
                                        "rationale": '"அந்த" before a க-initial word takes the linking க்'}]


def test_terminology_and_wordlist_findings_have_the_layered_shape(tmp_path):
    terms = [{"conceptId": "god", "status": "approved", "approvedRenderings": ["இறைவன்"],
              "rejectedRenderings": ["கடவுள்"], "note": ""},
             {"conceptId": "nothing-preferred", "status": "approved", "approvedRenderings": [],
              "rejectedRenderings": ["தேவதை"], "note": ""}]
    verses = {str(n): "தமிழ்" for n in range(1, 7)} | {"7": "தமிழ", "8": "கடவுள் தேவதை"}
    manager = LanguageQaManager(debounce=0, yield_seconds=0)
    manager.bind(project_at(tmp_path, verses=verses, terminology=terms))
    findings = wait(manager)["findings"]
    by_text = {f["originalText"]: f for f in findings}
    for finding in findings:
        assert_finding_shape(finding)
    assert by_text["கடவுள்"]["suggestions"] == [
        {"text": "இறைவன்", "rank": 1, "source": "termbase", "rationale": "Preferred form for god"}]
    assert by_text["தேவதை"]["suggestions"] == [] and by_text["தேவதை"]["suggestedReplacement"] is None
    assert by_text["தமிழ"]["layer"] == "lexicon" and by_text["தமிழ"]["inline"] is False


def test_inline_rpc_filters_on_the_findings_own_flag(tmp_path):
    manager = LanguageQaManager(debounce=0, yield_seconds=0)
    manager.bind(project_at(tmp_path, verses={"1": "அவனுக்கு பதில் சொன்னான்,,"}))
    wait(manager)
    assert [f["rule"] for f in manager.inline()["findings"]] == ["tamil.vallinam-missing"]
    assert all(f["inline"] for f in manager.inline()["findings"])


def test_inline_rpc_is_project_guarded_and_validates_chapter(fixture_project):
    engine = BridgeEngine()
    engine._language_qa = LanguageQaManager(debounce=0, yield_seconds=0)
    try:
        _r = call(engine, "project.open", {"path": str(fixture_project)})
        assert _r["success"], _r
        _r = call(engine, "verse.edit", {"chapter": "1", "verse": "1", "newText": "அவனுக்கு பதில் சொன்னான்."})
        assert _r["success"], _r
        wait(engine._language_qa)
        path = str(fixture_project)
        assert not call(engine, "languageQa.inline", {"projectPath": "other", "chapter": "1"})["success"]
        assert not call(engine, "languageQa.inline", {"projectPath": path, "chapter": 1})["success"]
        response = call(engine, "languageQa.inline", {"projectPath": path, "chapter": "1"})
        assert response["success"]
        findings = response["result"]["findings"]
        assert [f["originalText"] for f in findings] == ["அவனுக்கு பதில்"]
    finally:
        engine._language_qa.unbind()


def test_real_dispatcher_auto_open_edit_and_project_guard(fixture_project):
    engine = BridgeEngine()
    engine._language_qa = LanguageQaManager(debounce=0, yield_seconds=0)
    try:
        _r = call(engine, "project.open", {"path": str(fixture_project)})
        assert _r["success"], _r
        assert wait(engine._language_qa)["language"]["pack"] == "ta-irv"
        wrong = call(engine, "languageQa.pause", {"projectPath": "other", "paused": True})
        assert not wrong["success"]
        _r = call(engine, "verse.edit", {"chapter": "1", "verse": "1", "newText": "தமிழ் �"})
        assert _r["success"], _r
        assert wait(engine._language_qa)["totalFindings"] == 1
        response = call(engine, "languageQa.status", {"projectPath": str(fixture_project), "limit": 10})
        assert response["result"]["findings"][0]["rule"] == "unicode.corruption"
        _r = call(engine, "ping")
        assert _r["success"], _r
    finally:
        engine._language_qa.unbind()


def test_recovery_block_cannot_be_resumed(tmp_path):
    manager = LanguageQaManager(debounce=0, yield_seconds=0)
    project = project_at(tmp_path)
    manager.bind(project, blocked_reason="Recovery required")
    assert manager.pause(False)["state"] == "failed"
    assert manager.status()["projectPath"] == str(project.path)
    assert manager._thread is None


def test_continuous_foreground_polling_does_not_starve_worker(tmp_path):
    project = project_at(tmp_path, verses={str(n): "சரியான உரை" for n in range(50)})
    manager = LanguageQaManager(debounce=0, yield_seconds=.001)
    manager.bind(project)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        manager.touch()
        result = manager.status()
        if result["state"] == "completed":
            assert result["checkedVerses"] == 50
            break
        time.sleep(.005)
    else:
        pytest.fail("Frequent foreground requests starved Language QA")


def test_invalidated_edit_with_preserved_timestamp_and_size_is_not_cached(tmp_path):
    project = project_at(tmp_path, text="a  b")
    manager = LanguageQaManager(debounce=0, yield_seconds=0)
    manager.bind(project)
    assert wait(manager)["totalFindings"] == 1
    path = project.book_dir / "1.json"
    previous = path.stat()
    path.write_text(path.read_text(encoding="utf-8").replace("a  b", "a. b"), encoding="utf-8")
    os.utime(path, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    assert path.stat().st_size == previous.st_size
    manager.invalidate("1")
    assert wait(manager)["totalFindings"] == 0


def test_escaped_json_surrogate_is_reported_without_breaking_utf8_protocol(tmp_path):
    project = project_at(tmp_path)
    (project.book_dir / "1.json").write_text('{"1":"\\ud800"}', encoding="utf-8")
    manager = LanguageQaManager(debounce=0, yield_seconds=0)
    manager.bind(project)
    status = wait(manager)
    assert status["incomplete"]
    assert "U+D800" in status["limitations"][0]
    assert json.dumps(status, ensure_ascii=False).encode("utf-8")


# ---- inline USFM: notes and character markers are lifted, spans stay raw ----

def vallinam_in(text):
    return [f for f in scan(text)["findings"] if f["rule"] == "tamil.vallinam-missing"]


def test_footnoted_verse_is_scanned_before_the_note():
    text = "அந்த பட்டணம்\\f + \\ft பட்டணம் என்பது ஊர்.\\f* அழகாக இருந்தது."
    result = scan(text)
    assert result["checked"] and not result["limitations"]
    [finding] = vallinam_in(text)
    assert (finding["start"], finding["end"]) == (0, len("அந்த பட்டணம்"))
    assert finding["originalText"] == text[finding["start"]:finding["end"]] == "அந்த பட்டணம்"
    assert finding["suggestedReplacement"] == "அந்தப் பட்டணம்"


def test_footnote_with_a_reference_marker_is_lifted_exactly_as_the_review_specified():
    # The literal example from the #169 review brief, \fr reference included.
    text = "அந்த பட்டணம்\\f + \\fr 1:1 \\ft note\\f* வந்தான்"
    result = scan(text)
    assert result["checked"] and not result["limitations"]
    [finding] = vallinam_in(text)
    assert (finding["start"], finding["end"]) == (0, len("அந்த பட்டணம்"))
    assert finding["originalText"] == text[finding["start"]:finding["end"]] == "அந்த பட்டணம்"


def test_footnoted_verse_is_scanned_after_the_note_at_raw_offsets():
    text = "அவன் சொன்னான்\\f + \\ft குறிப்பு\\f* அந்த காகம் பறந்தது."
    [finding] = vallinam_in(text)
    assert finding["start"] == text.index("அந்த காகம்")
    assert finding["originalText"] == text[finding["start"]:finding["end"]] == "அந்த காகம்"


def test_words_of_jesus_marker_is_lifted():
    text = "அவர்கள் \\wj அவனை கொன்றார்கள்\\wj* என்றார்."
    [finding] = vallinam_in(text)
    assert finding["originalText"] == text[finding["start"]:finding["end"]] == "அவனை கொன்றார்கள்"
    assert finding["suggestedReplacement"] == "அவனைக் கொன்றார்கள்"


def test_a_doubled_space_inside_a_footnote_is_not_a_finding():
    # The note body is not Scripture text and is lifted with its contents;
    # lifting it also swallows one adjacent space, so no doubled space is left.
    text = "அவன் வந்தான் \\f + \\ft இரண்டு  இடைவெளி\\f* பின்பு போனான்."
    result = scan(text)
    assert result["checked"]
    assert not [f for f in result["findings"] if f["rule"] == "spacing.extra"]


def test_a_doubled_space_in_the_verse_itself_is_still_found_at_its_raw_offset():
    text = "\\wj அவன்  வந்தான்\\wj*"
    [finding] = [f for f in scan(text)["findings"] if f["rule"] == "spacing.extra"]
    assert text[finding["start"]:finding["end"]] == "  "
    assert finding["start"] == text.index("  ")


def test_unbalanced_footnote_skips_the_verse_with_a_named_reason():
    result = scan("அந்த காகம்\\f + \\ft முடிவில்லாத குறிப்பு")
    assert not result["checked"] and not result["findings"]
    assert result["limitations"] == ["Unbalanced \\f: 1 open, 0 close; verse not checked."]


@pytest.mark.parametrize("text,reason", [
    ("அந்த \\ft காகம்", "Footnote or cross-reference markup outside a complete"),
    ("அந்த \\ காகம்", "Backslash that is not a USFM marker at code-point 5"),
    # Shape found in a real local project: a milestone closed by a bare `*`.
    ('\\zsem-s  |x-content="δέσμιος" x-note="gloss"*\\w அந்த|strong="G1"\\w* காகம்\\zsem-e*',
     "Word attributes not closed by a USFM marker at code-point 9"),
])
def test_markup_that_cannot_be_lifted_safely_skips_the_verse(text, reason):
    result = scan(text)
    assert not result["checked"] and not result["findings"]
    assert result["limitations"][0].startswith(reason)


def test_a_pair_across_lifted_markup_is_flagged_on_its_first_word():
    # Visible text reads "அந்த காகம்", but the raw span would contain \wj* --
    # no raw span can hold the pair without covering markup. The first word
    # can: it is flagged, with the fix confined to it (2026-09-28 review).
    text = "\\wj அந்த\\wj* காகம்"
    result = scan(text)
    assert result["checked"] and not result["limitations"]
    [finding] = vallinam_in(text)
    assert finding["originalText"] == text[finding["start"]:finding["end"]] == "அந்த"
    assert finding["suggestedReplacement"] == "அந்தக்"


def test_a_pair_that_cannot_be_confined_to_its_first_word_is_still_dropped_and_counted():
    # Here the first word itself is split by markup: nothing can be drawn safely.
    text = "\\wj அ\\wj*ந்த காகம்"
    result = scan(text)
    assert result["checked"]
    assert not vallinam_in(text)
    assert result["limitations"] == ["1 candidate(s) spanning inline USFM markup omitted."]


def test_word_attributes_are_not_visible_text():
    lifted, reason = lift_inline_usfm('\\w அந்த|lemma="x"\\w* காகம்')
    assert reason == "" and lifted.visible == "அந்த காகம்"
    lifted, reason = lift_inline_usfm('அவன் \\zaln-s |x-strong="G1"\\*\\w வந்தான்|x-occurrence="1"\\w*\\zaln-e\\*.')
    assert reason == "" and lifted.visible == "அவன் வந்தான்."


def test_lifted_text_mirrors_the_frontend_note_swallow_rule():
    # The engine is the only side now (#91 Phase 2b: usfmNotes.ts no longer parses);
    # usfmNotes.test.ts's copy of this table was deleted with it. The rule itself lives in usfm_verse.
    for raw, visible in [
        ("a \\f + \\ft n\\f* b", "a b"),
        ("a\\f + \\ft n\\f* b", "a b"),
        # parseVerseNotes' first branch takes any note preceded by a space, so
        # its end-of-verse branch never removes that space; mirrored as-is.
        ("a \\f + \\ft n\\f*", "a "),
        ("a\\f + \\ft n\\f*", "a"),
        ("\\f + \\ft n\\f* b", "b"),
        ("a \\x - \\xo 1.1 \\xt Gen 1.1\\x* b", "a b"),
    ]:
        lifted, _ = lift_inline_usfm(raw)
        assert lifted.visible == visible, raw


def test_poetry_line_structure_is_whitespace_not_a_control_character():
    # The shape IRV Psalm 23:1 has in chapter JSON. Before this was fixed,
    # every line feed was a unicode.invisible finding: 2,977 in Psalms,
    # filling the 3,000-finding book cap by chapter 87.
    text = "யெகோவா என் மேய்ப்பராக இருக்கிறார்;\n\\q நான் தாழ்ச்சி அடையமாட்டேன்.\n\\q"
    result = scan(text)
    assert result["checked"] and not result["limitations"]
    assert not [f for f in result["findings"] if f["rule"] in {"unicode.invisible", "spacing.unusual"}]
    assert scan("அவன்\r\nவந்தான்")["findings"] == []


def test_a_line_break_still_separates_words():
    # A bare line break is whitespace between words: the pair is still checked.
    text = "அவன் அந்த\nகாகம் பார்த்தான்."
    [finding] = vallinam_in(text)
    assert finding["originalText"] == text[finding["start"]:finding["end"]] == "அந்த\nகாகம்"
    # A \q marker between them: the pair crosses markup, so it is flagged on
    # its first word, never drawn over the marker (PSA 135:21, 143:11 confirmed).
    poetry = "அவன் அந்த\n\\q காகம் பார்த்தான்."
    result = scan(poetry)
    [finding] = vallinam_in(poetry)
    assert finding["originalText"] == poetry[finding["start"]:finding["end"]] == "அந்த"
    assert finding["suggestedReplacement"] == "அந்தக்"
    assert not result["limitations"]


def test_real_invisible_characters_are_still_reported():
    for char in ("​", "‌", "\u0007"):
        rules = {f["rule"] for f in scan(f"அவன்{char}வந்தான்")["findings"]}
        assert "unicode.invisible" in rules, hex(ord(char))


def test_verse_without_markup_is_unchanged_by_lifting():
    # Same findings, same ids, same offsets as before lifting existed.
    text = "அந்த காகம்  பறந்தது"
    lifted, _ = lift_inline_usfm(text)
    assert lifted.visible == text and lifted.raw_index == tuple(range(len(text)))
    ids = [f["id"] for f in scan(text)["findings"]]
    assert ids == [stable_finding_id("php", "2", "3-4", "spacing.extra", "  ", 1),
                   stable_finding_id("php", "2", "3-4", "tamil.vallinam-missing", "அந்த காகம்", 1)]


def test_footnoted_verse_through_the_manager_has_raw_offsets_and_terminology(tmp_path):
    raw = "அவர் சொன்னார்\\f + \\ft குறிப்பு\\f* அந்த காகம் கடவுள் ஆவார்."
    terms = [{"conceptId": "god", "status": "approved", "approvedRenderings": ["இறைவன்"],
              "rejectedRenderings": ["கடவுள்"], "note": ""}]
    project = project_at(tmp_path, verses={"1": raw}, terminology=terms)
    manager = LanguageQaManager(debounce=0, yield_seconds=0)
    manager.bind(project)
    result = wait(manager)
    assert result["checkedVerses"] == 1 and result["skippedVerses"] == 0
    by_rule = {f["rule"]: f for f in result["findings"]}
    for rule, flagged in (("tamil.vallinam-missing", "அந்த காகம்"), ("terminology.deprecated-form", "கடவுள்")):
        finding = by_rule[rule]
        assert finding["start"] == raw.index(flagged)
        assert finding["originalText"] == raw[finding["start"]:finding["end"]] == flagged


def test_the_project_guard_compares_paths_canonically_not_as_strings(fixture_project):
    """The guard used `p["projectPath"] != str(self.project.path)`.

    `project.open` resolves the path it is given, so a caller echoing back the
    path *it* used was rejected whenever the two spellings differ — an 8.3 short
    name on Windows (`REVANT~1` versus `Revant C Idikulay`, which is exactly what
    `tempfile` hands out), a symlinked temp root, or a difference in case.

    Every UI path worked, because the UI only echoes a path the engine itself
    produced. What failed was the Language QA foreground latency gate in CI,
    which builds its own project under `tempfile.TemporaryDirectory()`.
    """
    from pathlib import Path

    engine = BridgeEngine()
    opened = call(engine, "project.open", {"path": str(fixture_project)})
    assert opened["success"], opened
    resolved = str(Path(fixture_project).resolve())

    # The spelling the engine settled on is accepted.
    assert call(engine, "languageQa.status", {"projectPath": resolved, "limit": 5})["success"]

    # So is an equivalent spelling that is not string-equal: a trailing separator
    # and a redundant "." component name the same directory.
    aliased = str(Path(fixture_project)) + os.sep + "." + os.sep
    assert aliased != resolved
    result = call(engine, "languageQa.status", {"projectPath": aliased, "limit": 5})
    assert result["success"], result

    # A genuinely different project is still refused — the guard still guards.
    other = call(engine, "languageQa.status",
                 {"projectPath": str(Path(fixture_project).parent / "not-this-project"), "limit": 5})
    assert other["success"] is False
    assert "different project" in other["error"]["message"]
