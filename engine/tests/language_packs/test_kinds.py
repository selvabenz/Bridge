"""The `indic` tier's rule kinds, on synthetic packs in two scripts (Malayalam
and Devanagari), never on a shipped pack: a kind is written once and must work
for every language that names it (docs/LANGUAGE_QA_PACKS.md)."""
import json

import pytest

from tc_ai_bridge.language_packs import PackError, load_pack
from tc_ai_bridge.language_packs.indic import ConfusionSet, ConfusionSetError
from tc_ai_bridge.language_packs.lexicon import write_lexicon_json
from tc_ai_bridge.language_qa import rule_fields, scan_text, suggestion, wordlist_findings
from tc_ai_bridge.language_packs.lexicon import lexicon_findings


def rule(rule_id, match, **extra):
    return {"id": rule_id, "version": 1, "category": extra.pop("category", "typo"), "severity": "medium",
            "confidence": "medium", "message": {"en": extra.pop("message", "{span} flagged")}, "match": match,
            "provenance": "synthetic", **extra}


def make_pack(tmp_path, name, rules, *, lexicon=None, confusion=None):
    folder = tmp_path / name
    (folder / "rules").mkdir(parents=True)
    meta = {"pack": name, "version": "0.0.1", "language": name.split("-")[0], "rules": []}
    for raw in rules:
        (folder / "rules" / f"{raw['id']}.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        meta["rules"].append(f"rules/{raw['id']}.json")
    if lexicon is not None:
        write_lexicon_json(folder / "lexicon.json.gz", lexicon)
        meta["lexicon"] = "lexicon.json.gz"
    if confusion is not None:
        (folder / "confusion.json").write_text(json.dumps(confusion, ensure_ascii=False), encoding="utf-8")
        meta["confusion"] = "confusion.json"
    (folder / "pack.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return load_pack(directory=folder)


def found(pack, text, rule_id):
    return [f["originalText"] for f in scan_text(text, book="x", chapter="1", verse="1", pack=pack)["findings"]
            if f["ruleId"] == f"{pack.name}/{rule_id}"]


# ---- sign-sequence --------------------------------------------------------------

@pytest.mark.parametrize("name,bases,signs,bad,good", [
    ("ml-test", "കഗചജടതദനപമയരലവസഹ", "ാിീുൂെേൈ്", "ാക", "കാ"),
    ("hi-test", "कगचजटतदनपमयरलवसह", "ािीुूेै्", "ाक", "का"),
])
def test_sign_sequence_flags_a_dependent_sign_without_its_base(tmp_path, name, bases, signs, bad, good):
    pack = make_pack(tmp_path, name, [rule("sign", {"type": "sign-sequence", "bases": bases, "signs": signs},
                                           category="unicode")])
    assert found(pack, f"{bad} {good}", "sign") == [bad[0]]
    assert found(pack, good, "sign") == []


# ---- mixed-script ---------------------------------------------------------------

@pytest.mark.parametrize("name,script,word,clean", [
    ("ml-test", "Malayalam", "മലയാളംabc", "മലയാളം abc"),
    ("hi-test", "Devanagari", "हिंदीabc", "हिंदी abc"),
])
def test_mixed_script_flags_two_scripts_inside_one_word(tmp_path, name, script, word, clean):
    pack = make_pack(tmp_path, name, [rule("mixed", {"type": "mixed-script", "scripts": [script, "Latin"]})])
    assert found(pack, word, "mixed") == [word]
    assert found(pack, clean, "mixed") == []


def test_mixed_script_refuses_an_unknown_script(tmp_path):
    with pytest.raises(PackError, match="scripts"):
        make_pack(tmp_path, "xx-test", [rule("mixed", {"type": "mixed-script", "scripts": ["Klingon", "Latin"]})])


# ---- reduplication-allowlist ------------------------------------------------------

@pytest.mark.parametrize("name,allowed,flagged", [
    ("ml-test", "പതുക്കെ പതുക്കെ", "അവൻ അവൻ"),
    ("hi-test", "धीरे धीरे", "वह वह"),
])
def test_reduplication_flags_a_repeat_unless_the_pack_allows_it(tmp_path, name, allowed, flagged):
    pack = make_pack(tmp_path, name, [rule("repeat", {"type": "reduplication-allowlist",
                                                      "allow": [allowed.split()[0]]})])
    assert found(pack, allowed, "repeat") == []
    assert found(pack, flagged, "repeat") == [flagged.split()[1]]  # the second word


# ---- lexicon-lookup ----------------------------------------------------------------

def lexicon(**extra):
    base = {"version": "test@1", "forms": {}, "common": [], "buckets": {}, "deprecated": {}, "corpus": {},
            "protected": [], "provenance": {}, "splits": {}}
    return {**base, **extra}


KNOWN_MISSPELLING = {"type": "lexicon-lookup", "check": "known-misspelling",
                     "unconfirmed": {"message": "{word} may be {fix}", "rationale": "unconfirmed"}}


@pytest.mark.parametrize("name,split,joined", [("ml-test", "ക ടൽ", "കടൽ"), ("hi-test", "सम य", "समय")])
def test_a_known_split_is_found_in_the_verse_with_its_join(tmp_path, name, split, joined):
    pack = make_pack(tmp_path, name, [rule("split", {"type": "lexicon-lookup", "check": "known-split"},
                                           category="word-joining", message="{prev} {next} -> {fix}")],
                     lexicon=lexicon(splits={split: joined}))
    [finding] = scan_text(split, book="x", chapter="1", verse="1", pack=pack)["findings"]
    assert finding["originalText"] == split and finding["suggestedReplacement"] == joined
    assert finding["suggestions"][0]["source"] == "lexicon"


def book(words):
    counts, first_seen = {}, {}
    for index, word in enumerate(words):
        counts[word] = counts.get(word, 0) + 1
        first_seen.setdefault(word, ("1", str(index + 1), 0, len(word), word, "h"))
    return counts, first_seen


@pytest.mark.parametrize("name,wrong,right", [("ml-test", "ഹ്രിദയം", "ഹൃദയം"), ("hi-test", "परमेशवर", "परमेश्वर")])
def test_a_known_misspelling_is_medium_and_never_inline_until_a_human_confirmed_it(tmp_path, name, wrong, right):
    pack = make_pack(tmp_path, name, [rule("known", KNOWN_MISSPELLING, inline=True, message="{word} is {fix}")],
                     lexicon=lexicon(deprecated={wrong: right}, provenance={wrong: "ai-review"}))
    counts, first_seen = book([wrong])
    [finding] = lexicon_findings("x", counts, first_seen, pack.lexicon(), pack=pack, rule_fields=rule_fields,
                                 suggestion=suggestion)
    assert (finding["confidence"], finding["inline"], finding["message"]) == ("medium", False, f"{wrong} may be {right}")
    assert finding["suggestedReplacement"] == right and finding["packVersion"] == f"{name}@0.0.1"


def test_rare_near_common_ranks_by_the_packs_confusion_set(tmp_path):
    rare, common = "കരം", "കറം"
    confusion = {"entries": [{"type": "base", "set": ["ര", "റ"], "cost": 0.2}]}
    near = {"type": "lexicon-lookup", "check": "rare-near-common", "suggestionRationale": "{corpusCount} ({distance})",
            "maxDistance": 0.5, "commonMin": 6, "ratioMin": 5, "minClusters": 2}
    pack = make_pack(tmp_path, "ml-test", [rule("near", near, message="{word} near {best}")],
                     lexicon=lexicon(forms={common: [60, 5]}, common=[common], buckets={"ക": [0]}),
                     confusion=confusion)
    counts, first_seen = book([rare])
    [finding] = lexicon_findings("x", counts, first_seen, pack.lexicon(), pack=pack, rule_fields=rule_fields,
                                 suggestion=suggestion)
    assert finding["suggestions"][0] == {"text": common, "rank": 1, "source": "lexicon", "rationale": "60 (0.2)"}


def test_a_known_misspelling_needs_its_unconfirmed_wording(tmp_path):
    with pytest.raises(PackError, match="unconfirmed"):
        make_pack(tmp_path, "xx-test", [rule("known", {"type": "lexicon-lookup", "check": "known-misspelling"})])


# ---- wordlist-variant ----------------------------------------------------------------

def test_wordlist_variant_uses_the_rules_thresholds(tmp_path):
    pack = make_pack(tmp_path, "hi-test", [rule("variant", {"type": "wordlist-variant", "commonMin": 3, "ratioMin": 3, "minLength": 3},
                                                message="{word} {count} {common} {commonCount}")])
    counts, first_seen = book(["नमक"] * 3 + ["नमख"])
    [finding] = wordlist_findings("x", counts, first_seen, pack=pack, rule=pack.by_id("variant"))
    assert finding["message"] == "नमख 1 नमक 3" and finding["rule"] == "variant"


# ---- confusion-set -----------------------------------------------------------------------

def test_confusion_costs_are_per_entry_and_the_lowest_match_wins():
    ml = ConfusionSet({"entries": [
        {"type": "base", "set": ["ര", "റ"], "cost": 1.0},
        {"type": "base", "set": ["ര", "റ"], "cost": 0.5},
        {"type": "toggle", "mark": "്", "cost": 0.2},
        {"type": "equivalent", "pair": ["ന്റ", "ൻ്റ"]},
    ]})
    assert ml.distance("കര", "കറ") == 0.5
    assert ml.distance("ക", "ക്") == 0.2 and ml.distance("കല", "കള") == 1.0
    # An equivalent pair is folded before a lookup, never proposed.
    assert ml.fold("എൻ്റെ") == ml.fold("എന്റെ") == "എന്റെ"
    hi = ConfusionSet({"entries": [{"type": "sign", "set": ["ि", "ी"], "cost": 0.5},
                                   {"type": "independent", "set": ["इ", "ई"], "cost": 0.4}]})
    assert hi.distance("किताब", "कीताब") == 0.5 and hi.distance("इस", "ईस") == 0.4
    assert hi.distance("किताब", "किताब") == 0.0


def test_a_malformed_confusion_table_is_refused():
    with pytest.raises(ConfusionSetError, match="type must be"):
        ConfusionSet({"entries": [{"type": "rhyme"}]})
    with pytest.raises(ConfusionSetError, match="at least two"):
        ConfusionSet({"entries": [{"type": "base", "set": ["ര"]}]})


# ---- the loader, for every kind ------------------------------------------------------------

@pytest.mark.parametrize("match,message", [
    ({"type": "sign-sequence", "bases": "ക"}, "match.signs"),
    ({"type": "sign-sequence", "bases": "ക", "signs": "ാ", "colour": "red"}, "unknown match keys ['colour']"),
    ({"type": "lexicon-lookup", "check": "guess"}, "match.check must be one of"),
    ({"type": "wordlist-variant", "rareMax": -1}, "non-negative"),
    ({"type": "grammar"}, "match.type must be one of"),
])
def test_a_malformed_kind_stops_the_pack_loading(tmp_path, match, message):
    with pytest.raises(PackError, match=message.replace("[", r"\[").replace("]", r"\]")):
        make_pack(tmp_path, "xx-test", [rule("broken", match)])


def test_a_kind_takes_no_fix_and_a_legacy_version_stamps_its_findings(tmp_path):
    with pytest.raises(PackError, match="fix applies to"):
        make_pack(tmp_path, "xx-test", [rule("a", {"type": "mixed-script", "scripts": ["Malayalam", "Latin"]},
                                             fix={"type": "replace", "text": "x"})])
    pack = make_pack(tmp_path / "b", "ml-test", [rule("mixed", {"type": "mixed-script", "scripts": ["Malayalam", "Latin"]},
                                                      legacyVersion="engine-1")])
    [finding] = scan_text("മലയാളംabc", book="x", chapter="1", verse="1", pack=pack)["findings"]
    assert (finding["packVersion"], finding["ruleVersion"]) == ("engine-1", "engine-1")
