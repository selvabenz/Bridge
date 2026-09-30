"""House style as project data: scoped ignores, the learner, proposals,
preferences, export/import (layered-rules Phase 6.3/6.4)."""
import json

import pytest

from bridge_service import BridgeEngine
from tc_ai_bridge.housestyle import (
    LEARN_IGNORES, PREFER_USES, PROPOSE_RULE_DECISIONS, HouseStyleLearner, house_style, preferences_from,
    project_proposals, validate_entry,
)
from tc_ai_bridge.language_qa_jobs import LanguageQaManager
from tests.support.projects import _write_minimal_book, call, fixture_project  # noqa: F401
from tests.service.test_language_qa import issue_for, wait

VALLINAM = "ta-irv/sandhi.vallinam.demonstrative"


def lqa_row(finding_id, decision, word="அந்த காகம்", rule=VALLINAM, ts="2026-09-24T00:00:0{}", n=0, chosen=None):
    issue = {"source": "languageQa", "ruleId": rule, "originalText": word}
    if chosen:
        issue["chosenSuggestion"] = chosen
    return {"issueKey": finding_id, "decision": decision, "chapter": "1", "verse": str(n + 1),
            "issue": issue, "modifiedTimestamp": ts.format(n)}


# ---- the model --------------------------------------------------------------------

def test_entries_are_validated_and_only_narrow():
    entry = validate_entry({"scope": "word-in-book", "ruleId": VALLINAM, "word": "அந்த காகம்"})
    assert entry["provenance"] == "explicit" and entry["state"] == "active" and entry["key"]
    for bad in ({"scope": "everywhere"}, {"scope": "word-in-book", "word": "x"},
                {"scope": "rule-in-book"}, {"scope": "word-in-book", "ruleId": "r", "word": "w", "state": "gone"},
                {"scope": "word-in-book", "list": "verbs", "word": "w"}):
        with pytest.raises(ValueError):
            validate_entry(bad)


def test_house_style_hides_words_and_rules_fills_lists_and_ranks_without_adding():
    style = house_style([
        {"scope": "word-in-book", "ruleId": VALLINAM, "word": "அந்த காகம்", "state": "active"},
        {"scope": "rule-in-project", "ruleId": "ta-irv/sandhi.clitic.fused", "word": "", "state": "active"},
        {"scope": "word-in-book", "list": "properNouns", "word": "மோவாப்", "state": "active"},
        {"scope": "word-in-book", "ruleId": VALLINAM, "word": "இந்த பெண்", "state": "removed"},
    ], {(VALLINAM, "அந்த பெண்"): "அந்தப் பெண்"})
    assert style.suppresses({"ruleId": VALLINAM, "originalText": "அந்த காகம்"})
    assert not style.suppresses({"ruleId": VALLINAM, "originalText": "இந்த பெண்"})  # removed is inert
    assert style.suppresses({"ruleId": "ta-irv/sandhi.clitic.fused", "originalText": "anything"})
    assert style.lists["housestyle.properNouns"] == {"மோவாப்"}
    finding = {"ruleId": VALLINAM, "originalText": "அந்த பெண்", "suggestions": [
        {"text": "அந்தப் பென்", "rank": 1, "source": "rule"}, {"text": "அந்தப் பெண்", "rank": 2, "source": "rule"}]}
    ranked = style.rank(finding)
    assert [s["text"] for s in ranked["suggestions"]] == ["அந்தப் பெண்", "அந்தப் பென்"]
    assert ranked["suggestions"][0]["source"] == "housestyle"
    other = {"ruleId": VALLINAM, "originalText": "அந்த பெண்", "suggestions": [{"text": "x", "rank": 1}]}
    assert style.rank(other) == other  # a preference never adds a suggestion


# ---- the learner's thresholds, at their boundaries ---------------------------------

def test_three_ignores_learn_a_word_but_two_do_not_and_a_use_resets_the_streak():
    rows = [lqa_row(f"f{i}", "ignored", n=i) for i in range(LEARN_IGNORES - 1)]
    learner = HouseStyleLearner()
    assert learner.observe("book", lambda: rows, rows[-1], []) is None  # 2 ignores
    third = lqa_row("f9", "ignored", n=5)
    learned = learner.observe("book", lambda: rows, third, [])
    assert learned["scope"] == "word-in-book" and learned["provenance"] == "learned"
    assert [e["decisionId"] for e in learned["evidence"]] == ["f0", "f1", "f9"]
    # A Use after two ignores resets the streak.
    learner = HouseStyleLearner()
    history = [lqa_row("a", "ignored", n=1), lqa_row("b", "ignored", n=2), lqa_row("c", "accepted", n=3)]
    assert learner.observe("book2", lambda: history, lqa_row("d", "ignored", n=4), []) is None
    # A false positive counts as an ignore.
    learner = HouseStyleLearner()
    fps = [lqa_row("x", "rejected", n=1), lqa_row("y", "rejected", n=2)]
    assert learner.observe("book3", lambda: fps, lqa_row("z", "ignored", n=3), [])["word"] == "அந்த காகம்"


def test_an_undone_or_removed_pair_is_never_relearned():
    rows = [lqa_row(f"f{i}", "ignored", n=i) for i in range(LEARN_IGNORES)]
    entries = [{"scope": "word-in-book", "ruleId": VALLINAM, "word": "அந்த காகம்", "state": "undone"}]
    assert HouseStyleLearner().observe("book", lambda: rows[:-1], rows[-1], entries) is None


def test_project_proposals_at_their_boundaries():
    learned = {"scope": "word-in-book", "provenance": "learned", "state": "active", "ruleId": VALLINAM, "word": "அந்த காகம்"}
    one = project_proposals([("rut", [learned], [])])
    two = project_proposals([("rut", [learned], []), ("gen", [learned], [])])
    assert one == [] and [p["scope"] for p in two] == ["word-in-project"]

    def decisions(ignored, other):
        return ([lqa_row(f"i{n}", "ignored", n=n) for n in range(ignored)]
                + [lqa_row(f"o{n}", "accepted", n=n) for n in range(other)])
    assert project_proposals([("rut", [], decisions(15, 4))]) == []            # 19 decisions
    assert [p["scope"] for p in project_proposals([("rut", [], decisions(16, 4))])] == ["rule-in-project"]  # 20, 0.80
    assert project_proposals([("rut", [], decisions(15, 5))]) == []            # 20, 0.75
    assert PROPOSE_RULE_DECISIONS == 20


def test_preferences_need_three_uses_of_the_same_suggestion():
    uses = [lqa_row(f"u{i}", "accepted", word="அந்த பெண்", chosen="அந்தப் பெண்", n=i) for i in range(PREFER_USES)]
    assert preferences_from(uses) == {(VALLINAM, "அந்த பெண்"): "அந்தப் பெண்"}
    assert preferences_from(uses[:-1]) == {}


# ---- through the engine -----------------------------------------------------------

@pytest.fixture
def hs_engine(fixture_project):
    engine = BridgeEngine()
    engine._language_qa = LanguageQaManager(debounce=0, yield_seconds=0)
    assert call(engine, "project.open", {"path": str(fixture_project)})["success"]
    yield engine
    engine._language_qa.unbind()


def test_a_short_approved_name_does_not_claim_ordinary_words():
    """The seed name சேத்து is one cluster from காத்து, பூத்து, தைத்து, சேராது and
    common words like பத்து and சேர்த்து. None of them is a variant of it: a
    common corpus word is never a misspelt name, and a short name admits only a
    single typist confusion (2026-09-28, found on the Bible-wide re-measure)."""
    from tc_ai_bridge.housestyle import name_findings
    from tc_ai_bridge.language_qa import RULE_VERSION, rule_fields, suggestion
    from tests.support.packs import ta_pack
    words = ["காத்து", "பூத்து", "தைத்து", "சேராது", "கொத்து", "பத்து", "சேர்த்து", "செத்து"]
    counts = {w: 1 for w in words}
    first_seen = {w: ("1", str(i + 1), 0, len(w), w, "h") for i, w in enumerate(words)}
    lexicon, distance = ta_pack().lexicon(), ta_pack().confusion().distance
    found = name_findings("gen", counts, first_seen, frozenset({"சேத்து"}), rule_fields=rule_fields,
                          suggestion=suggestion, rule_version=RULE_VERSION, corpus_count=lexicon.count,
                          distance=distance)
    assert found == []
    # A longer approved name still admits a one-cluster variant.
    found = name_findings("gen", {"பார்வொன்": 1}, {"பார்வொன்": ("1", "1", 0, 8, "பார்வொன்", "h")},
                          frozenset({"பார்வோன்"}), rule_fields=rule_fields, suggestion=suggestion,
                          rule_version=RULE_VERSION, corpus_count=lexicon.count, distance=distance)
    assert [f["suggestions"][0]["text"] for f in found] == ["பார்வோன்"]


def test_the_pack_seed_is_read_only_and_a_project_entry_replaces_it(hs_engine, fixture_project):
    """ta-irv bundles the 2026-09-28 review's curated names (one, சேத்து). It is
    merged into house style when read, never written into the workbench, and
    a project's own entry with its key -- here a removal -- replaces it."""
    from tc_ai_bridge.housestyle import bundled_seed, house_style, with_seed
    [seed] = bundled_seed("ta-irv")
    assert (seed["list"], seed["word"], seed["provenance"], seed["seed"]) == ("properNouns", "சேத்து", "curated", True)
    assert house_style(with_seed([], [seed])).lists["housestyle.properNouns"] == frozenset({"சேத்து"})
    engine = hs_engine
    listed = call(engine, "housestyle.list", {})["result"]
    assert [e["word"] for e in listed["seed"]] == ["சேத்து"] and not listed["entries"]
    removed = call(engine, "housestyle.record", {"entry": {"scope": "word-in-project", "list": "properNouns",
                                                           "word": "சேத்து", "provenance": "curated",
                                                           "state": "removed"}})
    assert removed["success"], removed
    after = call(engine, "housestyle.list", {})["result"]
    assert after["seed"] == [] and [e["state"] for e in after["entries"]] == ["removed"]
    own = engine.project.housestyle_entries()
    assert house_style(with_seed(own, bundled_seed("ta-irv"))).lists["housestyle.properNouns"] == frozenset()


def test_a_scoped_ignore_hides_the_word_and_counts_as_suppressed_by_house_style(hs_engine):
    engine = hs_engine
    assert call(engine, "verse.edit", {"chapter": "1", "verse": "1", "newText": "அந்த காகம் பறந்தது."})["success"]
    [finding] = [f for f in wait(engine._language_qa)["findings"] if f["rule"] == "tamil.vallinam-missing"]
    recorded = call(engine, "housestyle.record", {"entry": {
        "scope": "word-in-book", "ruleId": finding["ruleId"], "word": finding["originalText"],
        "provenance": "explicit", "evidence": [{"chapter": "1", "verse": "1", "decisionId": finding["id"]}]}})
    assert recorded["success"], recorded
    after = wait(engine._language_qa)
    assert finding["id"] not in {f["id"] for f in after["findings"]}
    assert after["houseStyleSuppressed"] == {finding["ruleId"]: 1}
    hidden = engine._language_qa.verse("1", "1")["hidden"]
    assert hidden[0]["houseStyleSuppressed"] is True
    # Remove writes a new state; the finding comes back.
    key = recorded["result"]["entry"]["key"]
    assert call(engine, "housestyle.setState", {"key": key, "state": "removed"})["success"]
    assert finding["id"] in {f["id"] for f in wait(engine._language_qa)["findings"]}
    assert [e["state"] for e in engine.project.housestyle_entries()] == ["removed"]


def test_three_ignores_through_verse_decide_learn_and_report_it_for_undo(hs_engine, fixture_project):
    engine = hs_engine
    text = "அந்த காகம் வந்தது; அந்த காகம் போனது; அந்த காகம் நின்றது."
    assert call(engine, "verse.edit", {"chapter": "1", "verse": "1", "newText": text})["success"]
    findings = [f for f in wait(engine._language_qa)["findings"] if f["originalText"] == "அந்த காகம்"]
    assert len(findings) == 3
    responses = [call(engine, "verse.decide", {"chapter": "1", "verse": "1", "findingId": f["id"],
                                               "status": "ignored", "issue": {**issue_for(f), "ruleId": f["ruleId"]}})
                 for f in findings]
    assert all(r["success"] for r in responses)
    assert "houseStyle" not in responses[1]["result"]
    learned = responses[2]["result"]["houseStyle"]["learned"]
    assert (learned["scope"], learned["provenance"], len(learned["evidence"])) == ("word-in-book", "learned", 3)
    # Undo supersedes it, and the pair is not learned again.
    assert call(engine, "housestyle.setState", {"key": learned["key"], "state": "undone"})["success"]
    again = call(engine, "verse.decide", {"chapter": "1", "verse": "1", "findingId": findings[0]["id"],
                                          "status": "ignored", "issue": {**issue_for(findings[0]), "ruleId": findings[0]["ruleId"]}})
    assert "houseStyle" not in again["result"]


def test_export_and_import_carry_learned_style_marked_imported(hs_engine, tmp_path):
    engine = hs_engine
    call(engine, "housestyle.record", {"entry": {"scope": "word-in-book", "ruleId": VALLINAM,
                                                 "word": "அந்த காகம்", "provenance": "learned",
                                                 "evidence": [{"chapter": "1", "verse": "1", "decisionId": "f"}]}})
    out = tmp_path / "style.json"
    assert call(engine, "housestyle.export", {"outputPath": str(out)})["result"]["count"] == 1
    other = tmp_path / "gen"
    _write_minimal_book(other, "gen", "அந்த காகம்.")
    assert call(engine, "project.open", {"path": str(other)})["success"]
    imported = call(engine, "housestyle.import", {"inputPath": str(out)})["result"]
    assert imported["imported"] == 1
    [entry] = imported["entries"]
    assert (entry["provenance"], entry["imported"], len(entry["evidence"])) == ("learned", True, 1)
    confirmed = call(engine, "housestyle.setState", {"key": entry["key"], "state": "active"})["result"]
    assert confirmed["entry"]["imported"] is False
    assert json.loads(out.read_text(encoding="utf-8"))["entries"][0]["word"] == "அந்த காகம்"


# ---- 6.5 the export ledger ------------------------------------------------------------

def test_export_writes_a_ledger_of_language_qa_uses_and_gate_overrides(hs_engine, tmp_path):
    import csv
    engine = hs_engine
    assert call(engine, "verse.edit", {"chapter": "1", "verse": "1", "newText": "அந்தக் காகம் பறந்தது."})["success"]
    assert call(engine, "verse.decide", {"chapter": "1", "verse": "1", "findingId": "lqa-use", "status": "accepted",
                                         "issue": {"source": "languageQa", "ruleId": VALLINAM, "originalText": "அந்த காகம்",
                                                   "chosenSuggestion": "அந்தக் காகம்"}})["success"]
    assert call(engine, "verse.decide", {"chapter": "1", "verse": "1", "findingId": "lqa-ign", "status": "ignored",
                                         "issue": {"source": "languageQa", "ruleId": VALLINAM, "originalText": "x"}})["success"]
    out = tmp_path / "rut.usfm"
    result = call(engine, "export.nonAligned", {"outputPath": str(out)})["result"]
    ledger = tmp_path / "rut.language-qa-changes.csv"
    assert result["ledgerPath"] == str(ledger) and ledger.exists()
    [row] = list(csv.DictReader(ledger.open(encoding="utf-8-sig")))
    assert (row["kind"], row["chapter"], row["verse"], row["ruleId"], row["original"], row["replacement"]) == (
        "use", "1", "1", VALLINAM, "அந்த காகம்", "அந்தக் காகம்")
    assert row["timestamp"] and row["user"]


# ---- 6.2 the name pack --------------------------------------------------------------

def test_an_approved_name_no_longer_abstains_vallinam_and_its_variant_is_a_minority_spelling(hs_engine):
    engine = hs_engine
    text = "அந்த பார்வோன் பேசினான்; பார்வொன் போனான்."
    assert call(engine, "verse.edit", {"chapter": "1", "verse": "1", "newText": text})["success"]
    before = wait(engine._language_qa)["findings"]
    assert any(f["originalText"] == "அந்த பார்வோன்" for f in before)
    call(engine, "housestyle.record", {"entry": {"scope": "word-in-book", "list": "properNouns",
                                                 "word": "பார்வோன்", "provenance": "curated"}})
    after = wait(engine._language_qa)
    # A name does not block doubling (2026-09-28 review: 17 of 25 needed it), so
    # the pack has no proper-noun abstain: the verse was rescanned (the list is
    # in the cache key) and the vallinam finding stays.
    assert any(f["originalText"] == "அந்த பார்வோன்" for f in after["findings"])
    [minority] = [f for f in after["findings"] if f["rule"] == "name.minority-spelling"]
    assert minority["originalText"] == "பார்வொன்" and minority["suggestions"][0]["text"] == "பார்வோன்"
    assert (minority["category"], minority["layer"]) == ("name", "housestyle")


def test_name_suggestions_come_from_the_names_check_and_skip_approved_names():
    from tc_ai_bridge.housestyle import name_suggestions
    cached = [
        {"original_text": "பார்வொன்", "suggested_replacement": "பார்வோன்",
         "evidence": [{"label": "More common spelling", "value": "“பார்வோன்” — 41x, e.g. 1:1"}]},
        {"original_text": "மோவாபு", "suggested_replacement": "மோவாப்",
         "evidence": [{"label": "More common spelling", "value": "“மோவாப்” — 12x, e.g. 2:3"}]},
    ]
    suggestions = name_suggestions(cached, frozenset({"மோவாப்"}))
    assert suggestions == [{"word": "பார்வோன்", "count": 41, "variants": ["பார்வொன்"]}]
