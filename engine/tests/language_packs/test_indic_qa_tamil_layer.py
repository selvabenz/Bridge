"""ta-irv's indic-qa layer: indic-qa's Tamil checker over the OV dictionary,
beside the pack's own rules (indic_qa_tamil.py, 2026-10-07).

`packs` marker (tests/conftest.py _FILE_MARKERS): loads the real Tamil
dictionary and IRV snapshot. Texts are real IRV verses or the reviewer's
house-style cases from indic-qa's docs/TAMIL_REVIEW.md."""
import json
import shutil
from collections import Counter

import pytest

from tc_ai_bridge.language_packs import default_pack, indic_qa_adapter, indic_qa_tamil, indic_qa_vendor
from tc_ai_bridge.language_packs.loader import load_pack
from tc_ai_bridge.language_packs.registry import packs_dir
from tc_ai_bridge.language_qa import (CROSSING_LIMITATION, MAX_VERSE_CHARS, lift_inline_usfm, rule_fields,
                                      stable_finding_id, text_hash)
from tc_ai_bridge.language_qa_jobs import LanguageQaManager
from tests.support.indic_qa import manager_project, wait


def run(chapters, headings=None, book="GEN"):
    result = indic_qa_adapter.profile_findings(
        default_pack("ta-irv"), book, chapters, lift=lift_inline_usfm, max_verse_chars=MAX_VERSE_CHARS,
        finding_id=stable_finding_id, rule_fields=rule_fields, text_hash=text_hash,
        crossing_note=CROSSING_LIMITATION, headings=headings)
    assert result is not None
    return result


def layer(findings, rule=None):
    return [f for f in findings if f["ruleId"].startswith("ta-irv/indicqa.")
            and (rule is None or f["rule"] == rule)]


def test_the_layer_joins_ta_irv_without_changing_its_own_rules():
    pack = default_pack("ta-irv")
    own = json.loads((packs_dir() / "ta-irv" / "pack.json").read_text(encoding="utf-8"))["rules"]
    ids = [r.id for r in pack.rules]
    assert len(ids) == len(own) + len(indic_qa_tamil.RULES)
    assert ids[len(own):] == list(indic_qa_tamil.RULES)
    extra = [r for r in pack.rules if r.match_type == "indic-qa"]
    assert {(r.stage, r.inline) for r in extra} == {("book", False)}, "inline needs the human gate"
    assert not [r.id for r in extra if (r.severity, r.confidence) == ("high", "high")]
    assert not pack.by_id("indicqa.lex.unknown").enabled
    assert pack.version == "1.1.0", "the layer must not expire the reviewers' ta-irv ignores"
    assert indic_qa_adapter.runs_checker(pack) and not indic_qa_adapter.is_profile_pack(pack.meta)


def test_a_missing_dictionary_drops_the_layer_with_a_note_not_the_pack(tmp_path):
    source = packs_dir() / "ta-irv"
    shutil.copytree(source, tmp_path / "ta-irv", ignore=shutil.ignore_patterns("dictionary"))
    pack = load_pack(directory=tmp_path / "ta-irv")
    assert not [r for r in pack.rules if r.match_type == "indic-qa"]
    assert not indic_qa_adapter.runs_checker(pack)
    assert any("OV dictionary checks skipped" in p for p in pack.problems)


def test_the_reviewers_theva_house_style_asks_to_remove_an_existing_otru():
    """GEN 1:27, as the IRV has it: no ஒற்று before தேவ- (TAMIL_REVIEW.md
    `no_theva`). ta-irv's own rules only abstain there; the layer reports it."""
    text = "அவனைத் தேவசாயலாகவே சிருஷ்டித்தார்"
    findings, notes = run({"1": {"27": text}})
    [finding] = layer(findings, "indicqa.sandhi.extra")
    assert finding["originalText"] == "அவனைத்" == text[finding["start"]:finding["end"]]
    assert finding["suggestions"][0]["text"] == "அவனை"
    assert "no_theva" in finding["message"] and "OV " in finding["message"]
    assert finding["reference"]["ref"] == "GEN 1:27" and finding["reference"]["label"] == "OV 1957"
    assert "context" not in finding
    assert not notes


def test_a_rare_word_one_slip_from_a_known_one_is_a_near_miss():
    """GEN 6:14 (IRV): கீல் for கீழ் -- not in the OV, used once in the IRV."""
    text = "அந்தக் கப்பலில் அறைகளை உண்டாக்கி, அதை உள்ளேயும் வெளியேயும் கீல் பூசு."
    findings, _ = run({"6": {"14": text}})
    [finding] = layer(findings, "indicqa.lex.near-miss")
    assert finding["originalText"] == "கீல்" == text[finding["start"]:finding["end"]]
    assert finding["suggestions"][0]["text"] == "கீழ்"
    assert (finding["category"], finding["severity"], finding["confidence"]) == ("typo", "medium", "medium")


def test_footnote_text_is_checked_with_raw_offsets_into_the_stored_verse():
    """GEN 4:8 (IRV): the footnote's கூட்டிசென்றான் runs two words together."""
    text = "காயீன் பேசினான். \\f + \\fr 4:8 \\ft பொய்யாக பேசி வயல் வெளிக்கு கூட்டிசென்றான்\\f*அவர்கள் வயல்வெளியில்"
    findings, _ = run({"4": {"8": text}})
    notes = [f for f in layer(findings) if f.get("context") == "footnote"]
    assert notes
    for finding in notes:
        assert text[finding["start"]:finding["end"]] == finding["originalText"]
        assert text.index("\\ft") < finding["start"] < text.index("\\f*")
    [joined] = [f for f in notes if f["originalText"] == "கூட்டிசென்றான்"]
    assert joined["suggestions"][0]["text"] == "கூட்டி சென்றான்"


def test_a_heading_is_checked_but_offers_no_fix_to_apply():
    """MAT 18:15's heading (IRV). A heading is not verse text: its offsets
    index the heading, so a fix applied by offset would land in the verse."""
    heading = "உனக்கு விரோதமாக குற்றம் செய்யும் சகோதரன்"
    findings, _ = run({"18": {"15": "உன் சகோதரன் உனக்கு விரோதமாகக் குற்றஞ்செய்தால்"}},
                      headings={"18": {"15": [{"tag": "s", "text": heading}, {"tag": "r", "text": "லூக் 17:3"}]}},
                      book="MAT")
    [finding] = [f for f in layer(findings) if f.get("context") == "heading"]
    assert finding["originalText"] == "விரோதமாக" == heading[finding["start"]:finding["end"]]
    assert finding["contextText"] == heading and finding["textHash"] == text_hash(heading)
    assert finding["suggestions"] == [] and finding["suggestedReplacement"] is None
    assert "suggested: விரோதமாகக்" in finding["message"]


def test_checks_bridge_already_runs_in_verse_text_are_not_repeated():
    text = "அவன்  சொன்னான்,,  யெகோவவை துதி"
    findings, _ = run({"1": {"1": text}})
    rules = {f["rule"] for f in layer(findings)}
    assert not rules & {"indicqa.spacing.double-space", "indicqa.punct.repeated", "indicqa.typo.known-defect"}


def test_finding_ids_are_stable_and_offsets_exact_across_runs():
    chapters = {"1": {"1": "இந்த தேசம் \\f + \\ft அந்த தேசம்\\f* நல்லது", "2": "அவனைத் தேவனுக்கு"}}
    first, _ = run(chapters)
    again, _ = run(chapters)
    assert [f["id"] for f in first] == [f["id"] for f in again]
    assert len({f["id"] for f in first}) == len(first)
    for finding in first:
        assert chapters[finding["chapter"]][finding["verse"]][finding["start"]:finding["end"]] == finding["originalText"]


def test_the_snapshot_restores_the_irv_otru_habits_exactly():
    pack = default_pack("ta-irv")
    loaded = indic_qa_adapter._load(pack)
    checker = loaded.checker
    assert len(checker.book_count) == 66 and checker.irv_pairs and checker.irv_form_use
    for book, pairs in list(checker.book_pairs.items())[:3]:
        assert sum(pairs.values()) > 0, book
    again = indic_qa_adapter._load(pack).checker
    assert again.irv_pairs == checker.irv_pairs and again.irv_form_use == checker.irv_form_use
    again.irv_count["இல்லாதசொல்"] += 1
    assert "இல்லாதசொல்" not in indic_qa_adapter._load(pack).checker.irv_count


def test_a_pass_drops_layer_findings_that_repeat_the_packs_own(tmp_path):
    """இந்த தேசம்: ta-irv's sandhi.vallinam.demonstrative flags it; the layer's
    indicqa.sandhi.missing on the same words is the same problem, so it is
    dropped, and a decision on the first never lets the second through."""
    project = manager_project(tmp_path, "gen", {"1": {"1": "இந்த தேசம் நல்லது. அவனைத் தேவனுக்கு"}}, "tam")
    manager = LanguageQaManager(debounce=0, yield_seconds=0)
    manager.bind(project)
    try:
        status = wait(manager)
    finally:
        manager.unbind()
    by_rule = Counter(f["ruleId"] for f in status["findings"])
    assert by_rule["ta-irv/sandhi.vallinam.demonstrative"] == 1
    assert by_rule["ta-irv/indicqa.sandhi.missing"] == 0
    assert by_rule["ta-irv/indicqa.sandhi.extra"] == 1  # அவனைத் தேவனுக்கு: the layer's own


def test_profile_packs_build_their_books_exactly_as_before():
    """Headings and footnotes are a layer's: a profile pack's book is unchanged."""
    text = "पहले वचन\\f + \\ft एक टिप्पणी\\f* फिर"
    book, refs, notes = indic_qa_adapter.build_book("GEN", {"1": {"1": text}}, lift=lift_inline_usfm,
                                                    max_verse_chars=MAX_VERSE_CHARS)
    assert [s.context for line in book.lines for s in line.segs] == ["verse", "verse"]
    assert all(r.notes == () and r.context == "verse" for r in refs.values()) and not notes
    assert "ta" not in indic_qa_vendor.PROFILES


@pytest.mark.parametrize("rule_id", list(indic_qa_tamil.RULES))
def test_every_layer_rule_has_its_bridge_entry(rule_id):
    entries = json.loads((packs_dir() / "ta-irv" / indic_qa_tamil.RULES_FILE).read_text(encoding="utf-8"))["rules"]
    assert entries[rule_id]["revision"] >= 1 and entries[rule_id]["inline"] is False


def test_the_snapshot_is_for_the_vendored_tamil_dictionary():
    import gzip
    raw = json.loads(gzip.decompress((packs_dir() / "ta-irv" / "irv_state.json.gz").read_bytes()))
    manifest = json.loads((packs_dir() / "ta-irv" / "dictionary" / "MANIFEST.json").read_text(encoding="utf-8"))
    assert raw["provenance"]["dictionary"] == manifest["files"], "rebuild: build_indic_qa_packs.py --irv-state ta"
    assert raw["provenance"]["books"] == 66 and "bookPairs" in raw and "bookFormUse" in raw


def test_a_books_layer_findings_do_not_depend_on_the_books_checked_before_it():
    gen = {"1": {"27": "அவனைத் தேவசாயலாகவே சிருஷ்டித்தார்", "28": "இந்த தேசம் கீல் பூசு"}}
    indic_qa_adapter.release()
    alone, _ = run(gen)
    run({"5": {"1": "இந்த தேசம் இந்த தேசம் இந்த தேசம் அவனைத் தேவன்"}}, book="MAT")
    after, _ = run(gen)
    assert [(f["id"], f["message"]) for f in alone] == [(f["id"], f["message"]) for f in after]
