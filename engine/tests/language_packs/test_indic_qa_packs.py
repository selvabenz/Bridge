"""The shipped indic-qa profile packs, end to end through the adapter.

`packs` marker (tests/conftest.py _FILE_MARKERS): loads the real
dictionaries and IRV snapshots. Examples are the reviewed misspellings and
sign-order faults from the profiles' own tables."""
import json

import pytest

from tc_ai_bridge.language_packs import default_pack, indic_qa_adapter, indic_qa_vendor
from tc_ai_bridge.language_packs.registry import packs_dir
from tests.support.indic_qa import check

PACKS = [f"{code}-irv" for code in indic_qa_vendor.PROFILES]


@pytest.mark.parametrize("name", PACKS)
def test_each_pack_lists_every_profile_rule_and_ships_its_data(name):
    pack = default_pack(name)
    profile = indic_qa_vendor.profile(pack.meta["profile"])
    assert [r.id for r in pack.rules] == list(profile.RULES)
    assert not [r.id for r in pack.rules if r.inline], "inline needs the human gate"
    assert not [r.id for r in pack.rules if (r.severity, r.confidence) == ("high", "high")]
    provenance = json.loads(json.dumps(indic_qa_adapter.state_digest(pack)))
    assert provenance != "none"


def _finding(findings, rule):
    matches = [f for f in findings if f["rule"] == rule]
    assert matches, [f["rule"] for f in findings]
    return matches[0]


def test_a_reviewed_hindi_misspelling_is_a_listed_typo_with_its_correction():
    pack = default_pack("hi-irv")
    text = "उसकी आयु सताईस वर्ष की थी।"
    findings, notes = check(pack, "GEN", {"1": {"1": text}})
    finding = _finding(findings, "hi.lex.known-misspelling")
    assert finding["originalText"] == "सताईस" == text[finding["start"]:finding["end"]]
    assert finding["suggestions"][0]["text"] == "सत्ताईस"
    assert (finding["ruleId"], finding["category"], finding["inline"]) == ("hi-irv/hi.lex.known-misspelling",
                                                                        "typo", False)
    assert (finding["severity"], finding["confidence"]) == ("medium", "high")
    assert finding["source"] == "languageQa" and finding["packVersion"] == "hi-irv@1.0.0"
    assert finding["ruleVersion"] == "hi-irv@1.0.0#1"
    assert not notes


def test_a_sub_rule_reports_under_its_catalogue_rule_and_keeps_the_detail():
    findings, _ = check(default_pack("hi-irv"), "GEN", {"1": {"1": "वे बडे़ लोग थे।"}})
    finding = _finding(findings, "hi.shape.errors")
    assert finding["detailRule"] == "hi.norm.nuqta-order"
    assert finding["suggestions"][0]["text"] == "बड़े"


def test_a_finding_after_a_footnote_has_its_raw_offsets():
    text = "पहले वचन\\f + \\ft एक टिप्पणी\\f* फिर सताईस दिन।"
    findings, _ = check(default_pack("hi-irv"), "GEN", {"1": {"1": text}})
    finding = _finding(findings, "hi.lex.known-misspelling")
    assert text[finding["start"]:finding["end"]] == "सताईस"
    assert finding["start"] > text.index("\\f*")


def test_a_repeated_word_in_one_verse_gets_two_stable_ids():
    chapters = {"1": {"1": "सताईस और सताईस", "2": "सताईस\n\\q फिर सताईस"}}
    pack = default_pack("hi-irv")
    first, _ = check(pack, "GEN", chapters)
    again, _ = check(pack, "GEN", chapters)
    ids = [f["id"] for f in first if f["rule"] == "hi.lex.known-misspelling"]
    assert len(ids) == len(set(ids)) == 4
    assert ids == [f["id"] for f in again if f["rule"] == "hi.lex.known-misspelling"]
    for finding in first:
        text = chapters[finding["chapter"]][finding["verse"]]
        assert text[finding["start"]:finding["end"]] == finding["originalText"]


def test_malayalam_encoding_is_reported_per_run_not_across_a_footnote():
    """The grouped encoding fix works per text run. A run ends at lifted
    markup, as in indic-qa; one run across the footnote would make a span
    that crosses markup, which is dropped (found measuring GEN 6:3)."""
    text = "എന്‍റെ ആത്മാവ് ജഡം\\f + \\ft കുറിപ്പ്\\f* തന്നെ; അവന്‍റെ വാക്ക്."
    findings, notes = check(default_pack("ml-irv"), "GEN", {"6": {"3": text}})
    encoding = [f for f in findings if f["rule"] == "ml.norm.encoding"]
    assert len(encoding) == 2, (encoding, notes)
    for finding in encoding:
        assert text[finding["start"]:finding["end"]] == finding["originalText"]
    assert not any("spanning inline USFM" in note for note in notes)


def test_a_disabled_rule_reports_nothing():
    pack = default_pack("hi-irv")
    assert not pack.by_id("hi.lex.unknown").enabled
    findings, _ = check(pack, "GEN", {"1": {"1": "ज़ीज़ीक्वाक्स्त्र नामक शब्द"}})
    assert not [f for f in findings if f["rule"] == "hi.lex.unknown"]


def test_one_checker_is_resident_at_a_time():
    check(default_pack("hi-irv"), "GEN", {"1": {"1": "क"}})
    check(default_pack("pa-irv"), "GEN", {"1": {"1": "ਕ"}})
    assert indic_qa_adapter._RESIDENT.key[0] == "pa-irv"
    indic_qa_adapter.release(keep="hi-irv")
    assert indic_qa_adapter._RESIDENT is None


def test_the_snapshot_seeds_irv_wide_counts():
    """A word the open book never uses still counts from the rest of the IRV."""
    check(default_pack("hi-irv"), "RUT", {"1": {"1": "क"}})
    checker = indic_qa_adapter._RESIDENT.checker
    assert len(checker.book_count) == 66  # RUT replaced by the live text, the other books from the snapshot
    assert checker.irv_count["परमेश्वर"] > 1000
    assert sum(checker.book_count["rut"].values()) == 1  # keyed by Bridge's book id, any case in


@pytest.mark.parametrize("name", PACKS)
def test_each_snapshot_is_for_the_vendored_dictionary(name):
    import gzip
    pack = default_pack(name)
    raw = json.loads(gzip.decompress((packs_dir() / name / "irv_state.json.gz").read_bytes()))
    manifest = json.loads((packs_dir() / name / "dictionary" / "MANIFEST.json").read_text(encoding="utf-8"))
    assert raw["provenance"]["dictionary"] == manifest["files"], "rebuild: build_indic_qa_packs.py --irv-state"
    assert raw["provenance"]["books"] == 66 and pack.meta["profile"] == name[:2]


def test_a_books_findings_do_not_depend_on_the_books_checked_before_it():
    """Each book is the IRV snapshot plus its own live text: a book checked
    earlier must not leave its live counts behind (found importing labels)."""
    pack = default_pack("hi-irv")
    gen = {"1": {"1": "उसकी आयु सताईस वर्ष की थी। वे बडे़ लोग थे।", "2": "लिये गये"}}
    indic_qa_adapter.release()
    alone, _ = check(pack, "GEN", gen)
    check(pack, "RUT", {"1": {"1": "लिये लिये लिये लिये लिये लिये लिये लिये"}})
    after, _ = check(pack, "GEN", gen)
    assert [f["id"] for f in after] == [f["id"] for f in alone]
    assert len(indic_qa_adapter._RESIDENT.checker.book_count) == 66
