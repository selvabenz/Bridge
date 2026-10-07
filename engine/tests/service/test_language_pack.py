"""The bundled ta-irv rule pack and its loader (layered-rules brief, Phase 3)."""
import copy
import json
import shutil

import pytest

from tc_ai_bridge.language_packs import PackError, load_pack
from tc_ai_bridge.language_packs.loader import apply_overrides
from tc_ai_bridge.language_packs.registry import packs_dir
from tc_ai_bridge.language_qa import scan_text, stable_finding_id
from tc_ai_bridge.language_qa_jobs import LanguageQaManager
from tests.service.test_language_qa import project_at, wait
from tests.support.packs import ta_pack as default_pack


def scan(text, **kwargs):
    kwargs.setdefault("pack", default_pack())
    return scan_text(text, book="php", chapter="1", verse="1", **kwargs)["findings"]


def by_rule(text, rule_id, **kwargs):
    return [f for f in scan(text, **kwargs) if f["ruleId"] == f"ta-irv/{rule_id}"]


# ---- the bundled pack ------------------------------------------------------

def test_a_status_request_never_loads_the_pack_and_concurrent_first_loads_share_one(monkeypatch):
    """The Phase 3 latency regression: a status poll during the first pass ran
    its own full pack load, and languageQa.status p95 rose from 0.3 to 80 ms."""
    import threading
    from tc_ai_bridge.language_packs import loader, loaded_pack

    monkeypatch.setattr(loader, "_LOADED", {})
    status = LanguageQaManager(debounce=0, yield_seconds=0).status()
    assert loaded_pack("ta-irv") is None
    # Before any pass no pack finding exists: the in-code inline rules are the answer.
    assert status["inlineRules"] == ["spacing.extra", "terminology.deprecated-form"]
    calls = []
    real = loader.load_pack
    monkeypatch.setattr(loader, "load_pack", lambda name: calls.append(name) or real(name))
    threads = [threading.Thread(target=loader.default_pack, args=("ta-irv",)) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert calls == ["ta-irv"] and loaded_pack("ta-irv") is loader.default_pack("ta-irv")


def test_the_bundled_pack_loads_and_every_example_passes():
    pack = load_pack("ta-irv")  # runs every example; raises on the first failure
    assert pack.pack_version == "ta-irv@1.1.0"
    assert len({r.id for r in pack.rules}) == len(pack.rules)


def test_every_enabled_rule_has_ten_real_examples_each_way_with_their_origin():
    for rule in default_pack().rules:
        if not rule.enabled:
            assert "DISABLED" in rule.source["provenance"], rule.id
            continue
        if rule.match_type == "indic-qa":
            # The indic-qa layer's catalogue rules (indic_qa_tamil.py) run the
            # vendored checker, not a JSON match, so carry no embedded examples;
            # tests/language_packs/test_indic_qa_tamil_layer.py covers them.
            continue
        if rule.legacy_version:
            # Moved from the engine's code (2026-09-30) with its matching
            # unchanged; its tests stayed where they were (test_language_qa.py,
            # test_lexicon.py). The ten-example rule was never applied to it.
            continue
        incorrect, correct = rule.examples["incorrect"], rule.examples["correct"]
        assert len(incorrect) >= 10 and len(correct) >= 10, rule.id
        assert all(e["origin"] for e in incorrect + correct), rule.id
        assert rule.source.get("provenance"), rule.id


def test_the_migrated_rules_keep_their_legacy_name_and_so_their_finding_ids():
    pack = default_pack()
    migrated = {r.id for r in pack.rules if r.legacy_id == "tamil.vallinam-missing"}
    assert migrated == {"sandhi.vallinam.demonstrative", "sandhi.vallinam.manner-adverb",
                        "sandhi.vallinam.accusative", "sandhi.vallinam.dative"}
    [finding] = scan("அந்த காகம் பறந்தது")
    assert finding["rule"] == "tamil.vallinam-missing"
    assert finding["id"] == stable_finding_id("php", "1", "1", "tamil.vallinam-missing", "அந்த காகம்", 1)


def test_inline_rules_are_exactly_the_human_justified_ones():
    # Inline needs >= 0.90 human-labelled precision on >= 20 findings
    # (DECISIONS.md 2026-09-28); the benchmark's human gate enforces it in CI.
    inline = {r.id for r in default_pack().rules if r.inline}
    # (a) >= 20 labelled at >= 0.90 over both review rounds; (b) the whole
    # collection's findings labelled, none wrong (wrong-consonant 1/1, dropped-tha 2/2).
    # lexicon.known-misspelling 43/43 (2026-09-28) and lexicon.known-split 11/11
    # (2026-09-29) were inline in the engine's code; they are pack rules since 2026-09-30.
    assert inline == {"sandhi.vallinam.dative", "sandhi.vallinam.accusative", "sandhi.vallinam.demonstrative",
                      "sandhi.vallinam.manner-adverb", "sandhi.compound.direction",
                      "sandhi.vallinam.wrong-consonant", "typo.suffix.dropped-tha",
                      "lexicon.known-misspelling", "lexicon.known-split"}
    assert not any("inlineSignOff" in r.source for r in default_pack().rules)


# ---- rule behaviour --------------------------------------------------------

@pytest.mark.parametrize("text,span,fix", [
    ("அவன் அதை செய்தான்", "அதை செய்தான்", "அதைச் செய்தான்"),                 # a pronoun accusative (was B4)
    ("அவன் வார்த்தையை கேட்டான்", "வார்த்தையை கேட்டான்", "வார்த்தையைக் கேட்டான்"),  # any -ஐ object form
    ("அவருக்கு பதில் சொன்னான்", "அவருக்கு பதில்", "அவருக்குப் பதில்"),              # any -க்கு dative
])
def test_the_generalised_case_rules_flag_real_case_forms(text, span, fix):
    [finding] = [f for f in scan(text) if f["category"] == "sandhi"]
    assert finding["originalText"] == span and finding["suggestedReplacement"] == fix


@pytest.mark.parametrize("text", [
    "அவர்கள் படை பெத்லெகேமில் இருந்தது",   # படை "army": a root noun (IRV has படையை), not an accusative
    "அவன் மலை தேசத்தில் இருந்தான்",       # மலை: root (மலையை)
    "அவன் கை தட்டினான்",                 # கை: root (கையை)
    "கிழக்கு பக்கத்தில் இருந்தது",         # கிழக்கு "east": a root in -க்கு (கிழக்கில்), not a dative
    "ஈசாக்கு பதில் சொன்னான்",            # a name in -க்கு: a nominative (IRV_Pass3_Handoff §5)
    "அவனோடு கூட போனான்",                  # a spaced clitic, house style apart without doubling
])
def test_roots_names_house_forms_and_clitics_are_not_missing_links(text):
    assert not [f for f in scan(text) if f["rule"] == "tamil.vallinam-missing"]


def test_wrong_linking_consonant_is_replaced_not_added():
    [finding] = by_rule("அவன் அதைக் பார்த்தான்", "sandhi.vallinam.wrong-consonant")
    assert finding["originalText"] == "அதைக் பார்த்தான்"
    assert finding["suggestedReplacement"] == "அதைப் பார்த்தான்"
    assert finding["inline"] is True  # its whole population (1) labelled, none wrong (DECISIONS.md 2026-09-29)


@pytest.mark.parametrize("text", [
    "காத் கோத்திரத்தில் இருந்தான்",     # the name Gad ends in த்: not a linking consonant
    "மோவாப் தேசத்திலுள்ள மலை",          # Moab
    "அவன் அதைப் பார்த்தான்",            # correctly linked
])
def test_names_ending_in_a_consonant_are_not_wrong_links(text):
    assert not by_rule(text, "sandhi.vallinam.wrong-consonant")


def test_bare_tan_offers_the_pronoun_first_and_the_clitic_second():
    """2026-09-29: after a case form a bare "X தான்" is always a defect; the
    reflexive pronoun is written apart and doubled (அதைத் தான்), the clitic
    fused (அதைத்தான்). The engine cannot tell which, so it offers both, the
    pronoun first (6 of 6 sampled), at medium confidence."""
    assert default_pack().by_id("sandhi.clitic.fused").enabled is False
    [finding] = by_rule("அவன் அதை தான் செய்தான்", "sandhi.vallinam.accusative")
    assert [s["text"] for s in finding["suggestions"]] == ["அதைத் தான்", "அதைத்தான்"]
    assert [s["rationale"] for s in finding["suggestions"]] == ["pronoun: separate, doubled", "clitic: fused"]
    assert finding["confidence"] == "medium" and "பிரதிப்பெயர்" in finding["message"]
    [dative] = by_rule("தேவனுக்கு தான் கொடுத்தான்", "sandhi.vallinam.dative")
    assert dative["suggestedReplacement"] == "தேவனுக்குத் தான்"
    # Either correct form is left alone.
    for text in ("அவன் அதைத் தான் செய்தான்", "அதைத்தான் செய்தான்"):
        assert not [f for f in scan(text) if f["category"] in {"sandhi", "word-joining"}], text


@pytest.mark.parametrize("text,rule_id,span,fix", [
    ("அவர்கள் யெகோவவை ஆராதித்தார்கள்", "typo.divine-name.vowel-drop", "யெகோவவ", "யெகோவாவ"),
    ("யெகோவாக்குப் பலி", "typo.divine-name.dative-stem", "யெகோவாக்க", "யெகோவாவுக்க"),
    ("அதைச் செய்வற்கு வந்தான்", "typo.suffix.dropped-tha", "செய்வற்கு", "செய்வதற்கு"),
])
def test_known_irv_defect_shapes(text, rule_id, span, fix):
    [finding] = by_rule(text, rule_id)
    assert finding["originalText"] == span == text[finding["start"]:finding["end"]]
    assert finding["suggestedReplacement"] == fix
    assert finding["category"] == "typo"


def test_space_before_a_note_end_is_found_on_the_raw_text_at_raw_offsets():
    text = "அவன் சொன்னான்\\f + \\ft குறிப்பு. \\f* பின்பு."
    [finding] = by_rule(text, "integrity.space-before-note-end")
    assert text[finding["start"]:finding["end"]] == " " and text[finding["end"]:].startswith("\\f*")
    assert finding["suggestedReplacement"] is None or finding["suggestedReplacement"] == ""
    assert not by_rule("அவன் சொன்னான்\\f + \\ft குறிப்பு.\\f* பின்பு.", "integrity.space-before-note-end")


def test_the_digits_rule_ships_disabled():
    rule = default_pack().by_id("integrity.digits-in-text")
    assert rule.enabled is False
    assert not by_rule("அவன் 12 பேரை அழைத்தான்", "integrity.digits-in-text")


def test_a_proper_noun_no_longer_blocks_doubling_but_a_list_can_still_feed_an_abstain():
    # 2026-09-28 review: after a case form, a name needed doubling in 17 of 25
    # cases (அவனுக்குச் சேத் என்று பெயரிட்டான்), so the pack has no name abstain.
    text = "அவன் அவனை தாவீது கண்டான்"
    names = {"housestyle.properNouns": frozenset({"தாவீது"})}
    assert [f["originalText"] for f in scan(text, lists=names) if f["rule"] == "tamil.vallinam-missing"] \
        == ["அவனை தாவீது"]
    # The listRef mechanism still works for a project that narrows a rule with it.
    narrowed = apply_overrides(default_pack(), {"rules": {"sandhi.vallinam.accusative": {
        "abstain": [{"next": {"listRef": "housestyle.properNouns"}}]}}})
    assert not [f for f in scan(text, lists=names, pack=narrowed) if f["rule"] == "tamil.vallinam-missing"]


@pytest.mark.parametrize("text,span,fix", [
    ("இந்த தேசத்தில் இருந்தான்", "இந்த தேசத்தில்", "இந்தத் தேசத்தில்"),     # house-form stems gone: 9 of 9 were errors
    ("அதற்கு சித்னா என்று பெயரிட்டான்", "அதற்கு சித்னா", "அதற்குச் சித்னா"),  # -ற்கு datives: 17 of 17
    ("கிழக்கு காற்றினால்", "கிழக்கு காற்றினால்", "கிழக்குக் காற்றினால்"),       # direction compound
])
def test_the_reviewed_misses_are_now_flagged(text, span, fix):
    [finding] = [f for f in scan(text) if f["category"] == "sandhi"]
    assert (finding["originalText"], finding["suggestedReplacement"]) == (span, fix)


@pytest.mark.parametrize("text", [
    "அவனுக்கு தேவன் தோன்றினார்",   # house style: no doubling before தேவ- (11 of 13)
    "வெண்மை காணப்பட்டது",          # the reviewer's root noun, excluded exactly
    "ஒருமுறை கண்டான்",              # a compound-final noun (notSuffixLexical)
])
def test_the_reviewed_false_alarms_are_not_flagged(text):
    assert not [f for f in scan(text) if f["category"] == "sandhi"]


def test_tevai_need_is_still_checked():
    # notPrefix: தேவை "need" is not a தேவ- form, so the pair is still checked.
    assert [f["originalText"] for f in scan("அவனுக்கு தேவை இருந்தது") if f["category"] == "sandhi"] \
        == ["அவனுக்கு தேவை"]


# ---- the loader refuses a broken pack -----------------------------------------

def pack_copy(tmp_path, edit):
    target = tmp_path / "ta-irv"
    shutil.copytree(packs_dir() / "ta-irv", target)
    path = target / "rules" / "sandhi.vallinam.demonstrative.json"
    rule = json.loads(path.read_text(encoding="utf-8"))
    edit(rule)
    path.write_text(json.dumps(rule, ensure_ascii=False), encoding="utf-8")
    return target


@pytest.mark.parametrize("edit,message", [
    (lambda r: r.update(colour="red"), "unknown rule keys ['colour']"),
    # "grammar" became a category with the indic-qa profile packs (2026-10-07).
    (lambda r: r.update(category="semantics"), "unknown category 'semantics'"),
    (lambda r: r.update(severity="urgent"), "severity and confidence must be"),
    (lambda r: r.pop("message"), "missing 'message'"),
    (lambda r: r["match"].update(link="sometimes"), "match.link must be one of"),
    (lambda r: r["match"].update(gap="any"), "gap must be 'whitespace'"),
    (lambda r: r["match"]["prev"].update(suffix="(["), "bad regex"),
    (lambda r: r["match"]["prev"].update(spelling=["x"]), "unknown condition keys ['spelling']"),
    (lambda r: r.update(fix={"type": "rewrite"}), "fix.type must be one of"),
    # The AI-agreement sign-off was superseded by the human gate (2026-09-28).
    (lambda r: r.update(inlineSignOff={"by": "m", "date": "d"}), "unknown rule keys ['inlineSignOff']"),
    (lambda r: r.update(match={"type": "regex", "on": "raw", "pattern": "x"}), "only integrity rules may match raw"),
])
def test_a_malformed_rule_stops_the_pack_loading(tmp_path, edit, message):
    directory = pack_copy(tmp_path, edit)
    with pytest.raises(PackError, match="sandhi.vallinam.demonstrative|ta-irv") as raised:
        load_pack(directory=directory)
    assert message in str(raised.value)


def test_a_failing_example_stops_the_pack_loading_and_names_it(tmp_path):
    def break_example(rule):
        rule["examples"]["correct"].append({"text": "அந்த காகம் பறந்தது", "origin": "a bad example"})
    with pytest.raises(PackError) as raised:
        load_pack(directory=pack_copy(tmp_path, break_example))
    assert "sandhi.vallinam.demonstrative" in str(raised.value) and "a bad example" in str(raised.value)


def test_a_wrong_fix_in_an_example_stops_the_pack_loading(tmp_path):
    def wrong_fix(rule):
        rule["examples"]["incorrect"][0]["fix"] = "something else"
    with pytest.raises(PackError, match="expected fix"):
        load_pack(directory=pack_copy(tmp_path, wrong_fix))


# ---- project overrides may only narrow -----------------------------------------

def test_overrides_narrow_and_refuse_to_widen():
    base = default_pack()
    narrowed = apply_overrides(base, {"rules": {
        "sandhi.vallinam.accusative": {"enabled": False},
        "sandhi.vallinam.dative": {"inline": False, "abstain": [{"prev": {"lexical": ["அவருக்கு"]}}]},
        "integrity.digits-in-text": {"enabled": True},             # refused: cannot enable
        "sandhi.clitic.fused": {"inline": True, "match": {}},      # refused twice
        "no.such.rule": {"enabled": False},
    }})
    assert not by_rule("அவன் அதை செய்தான்", "sandhi.vallinam.accusative", pack=narrowed)
    assert by_rule("அவன் அதை செய்தான்", "sandhi.vallinam.accusative", pack=base)
    assert not by_rule("அவருக்கு பதில் சொன்னான்", "sandhi.vallinam.dative", pack=narrowed)
    [still] = by_rule("அவனுக்கு பதில் சொன்னான்", "sandhi.vallinam.dative", pack=narrowed)
    assert still["inline"] is False
    assert not by_rule("அவன் 12 பேர்", "integrity.digits-in-text", pack=narrowed)
    assert narrowed.by_id("sandhi.clitic.fused").inline is False
    problems = " | ".join(narrowed.problems)
    assert "integrity.digits-in-text.enabled refused" in problems
    assert "sandhi.clitic.fused.inline refused" in problems and "sandhi.clitic.fused.match refused" in problems
    assert "unknown rule 'no.such.rule'" in problems
    assert narrowed.fingerprint() != base.fingerprint()
    # The bundled pack itself is untouched.
    assert base.by_id("sandhi.vallinam.accusative").enabled and base.by_id("sandhi.vallinam.dative").inline


def test_a_project_override_file_applies_on_the_next_pass(tmp_path):
    project = project_at(tmp_path, verses={"1": "அவருக்கு பதில் சொன்னான். அந்த காகம் பறந்தது."})
    manager = LanguageQaManager(debounce=0, yield_seconds=0)
    manager.bind(project)
    rules = {f["ruleId"] for f in wait(manager)["findings"]}
    assert {"ta-irv/sandhi.vallinam.dative", "ta-irv/sandhi.vallinam.demonstrative"} <= rules
    overrides = project.path / ".apps" / "translationCoreAI" / "language-packs" / "ta-irv"
    overrides.mkdir(parents=True)
    (overrides / "overrides.json").write_text(json.dumps({"rules": {
        "sandhi.vallinam.dative": {"enabled": False},
        "sandhi.clitic.fused": {"inline": True},  # widening: refused and reported
    }}), encoding="utf-8")
    manager.invalidate_all()
    after = wait(manager)
    rules = {f["ruleId"] for f in after["findings"]}
    assert "ta-irv/sandhi.vallinam.dative" not in rules and "ta-irv/sandhi.vallinam.demonstrative" in rules
    assert any("sandhi.clitic.fused.inline refused" in m for m in after["limitations"])
    manager.unbind()
