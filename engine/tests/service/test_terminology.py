import json
import unicodedata

import pytest

from bridge_service import BridgeEngine
from tc_ai_bridge.language_qa import stable_finding_id
from tc_ai_bridge.language_qa_jobs import LanguageQaManager
from tc_ai_bridge.tc_project import ProjectError, TranslationCoreProject
from tc_ai_bridge.terminology import MAX_PHRASE_WORDS, TermIndex, find_deprecated_forms, phrase_tokens
from tests.support.workbench import _build_minimal_project
from tests.support.projects import call, fixture_project
from tests.support.language_qa import project_at, wait


def approved_term(concept_id, rejected, preferred=None, note="", status="approved"):
    return {
        "conceptId": concept_id, "status": status,
        "approvedRenderings": preferred or [], "rejectedRenderings": rejected, "note": note,
    }


def test_phrase_tokens_splits_and_normalizes():
    assert phrase_tokens("இயேசு கிறிஸ்து") == (
        unicodedata.normalize("NFC", "இயேசு"), unicodedata.normalize("NFC", "கிறிஸ்து"))


def test_term_index_ignores_non_approved_status():
    index = TermIndex([approved_term("god", ["தேவன்"], status="provisional")])
    assert not index  # nothing authoritative registered -- frequency/discovery is never enough
    assert find_deprecated_forms("தேவன் இருக்கிறார்.", index) == []


def test_term_index_ignores_malformed_entries():
    terms = [None, "not a dict", {}, {"conceptId": "", "rejectedRenderings": ["x"], "status": "approved"},
             approved_term("god", ["தேவன்"])]
    index = TermIndex(terms)
    assert bool(index)  # the one well-formed entry still registers
    assert find_deprecated_forms("தேவன் இருக்கிறார்.", index)


def test_term_index_skips_overlong_phrases():
    long_phrase = " ".join(["ஒரு"] * (MAX_PHRASE_WORDS + 1))
    index = TermIndex([approved_term("x", [long_phrase])])
    assert not index


def test_find_deprecated_forms_flags_single_word_match():
    index = TermIndex([approved_term("god", ["தேவன்"], preferred=["இறைவன்"], note="Use இறைவன் in Psalms.")])
    matches = find_deprecated_forms("அவர் தேவன் ஆவார்.", index)
    assert len(matches) == 1
    m = matches[0]
    assert m["matchedText"] == "தேவன்"
    assert m["conceptId"] == "god"
    assert m["preferredRenderings"] == ["இறைவன்"]
    assert m["note"] == "Use இறைவன் in Psalms."
    assert m["suggestedReplacement"] == "இறைவன்"


def test_find_deprecated_forms_suggested_replacement_is_first_preferred_form():
    index = TermIndex([approved_term("god", ["தேவன்"], preferred=["இறைவன்", "தெய்வம்"])])
    assert find_deprecated_forms("தேவன்", index)[0]["suggestedReplacement"] == "இறைவன்"


def test_find_deprecated_forms_suggested_replacement_is_none_without_a_preferred_form():
    # A term can exist with only rejected forms recorded -- never invent a
    # preferred one; None is the honest, expected value here.
    index = TermIndex([approved_term("god", ["தேவன்"])])
    assert find_deprecated_forms("தேவன்", index)[0]["suggestedReplacement"] is None


def test_find_deprecated_forms_flags_multi_word_phrase():
    index = TermIndex([approved_term("jesus-christ", ["இயேசு கிறிஸ்து"], preferred=["இயேசு"])])
    matches = find_deprecated_forms("இயேசு கிறிஸ்து வந்தார்.", index)
    assert len(matches) == 1
    assert matches[0]["matchedText"] == "இயேசு கிறிஸ்து"


def test_find_deprecated_forms_requires_a_whitespace_only_boundary():
    index = TermIndex([approved_term("jesus-christ", ["இயேசு கிறிஸ்து"])])
    assert find_deprecated_forms("இயேசு, கிறிஸ்து வந்தார்.", index) == []


def test_find_deprecated_forms_ignores_unregistered_words():
    index = TermIndex([approved_term("god", ["தேவன்"])])
    assert find_deprecated_forms("இறைவன் இருக்கிறார்.", index) == []


def test_find_deprecated_forms_raw_span_and_normalization():
    # The termbase is written in NFC; a target-text occurrence in NFD must
    # still match (comparison is normalized) but the captured span/matchedText
    # must be the ORIGINAL, unnormalized bytes (raw spans never refer to a
    # normalized copy -- the same invariant language_qa.py states for itself).
    raw = unicodedata.normalize("NFD", "தேவன்")
    text = f"அவர் {raw} ஆவார்."
    index = TermIndex([approved_term("god", ["தேவன்"])])
    matches = find_deprecated_forms(text, index)
    assert len(matches) == 1
    start, end = matches[0]["start"], matches[0]["end"]
    assert text[start:end] == raw
    assert matches[0]["matchedText"] == raw


def test_term_index_ignores_a_pre_existing_payload_with_no_status_field():
    # schemaVersion 1 rows (written before this session, if any ever existed)
    # have no 'status' key at all. Missing status must never be treated as
    # implicitly authoritative -- the safe default is "not approved", not
    # "assume yes". This is what keeps a schema-shape change backward
    # compatible without a real migration: old-shaped data degrades to
    # inert rather than crashing or silently gaining new authority.
    old_shape = {
        "bookId": "php", "conceptId": "god", "sourceLemma": "", "strong": "",
        "approvedRenderings": ["இறைவன்"], "allowedAlternatives": [], "rejectedRenderings": ["கடவுள்"],
        "note": "", "scope": "book", "username": "x", "modifiedTimestamp": "x",
        "app": "translationCore AI Bridge", "schemaVersion": 1,
    }
    index = TermIndex([old_shape])
    assert not index
    assert find_deprecated_forms("கடவுள்", index) == []


def test_term_index_conflicting_entries_resolve_deterministically():
    # Two different concepts registering the same rejected phrase is a data-
    # quality issue the curator should fix, but the system must never behave
    # ambiguously about it -- last-by-conceptId wins, not iteration-order luck.
    terms = [approved_term("god-a", ["X"], preferred=["A"]), approved_term("god-b", ["X"], preferred=["B"])]
    matches = find_deprecated_forms("X", TermIndex(terms))
    assert len(matches) == 1
    assert matches[0]["conceptId"] == "god-b"


# ---- storage: TranslationCoreProject.record_terminology_rule / terminology_rules ----

def test_record_and_read_terminology_rule_round_trips(tmp_path):
    root = _build_minimal_project(tmp_path)
    project = TranslationCoreProject(root)
    project.record_terminology_rule(
        "god", ["இறைவன்"], allowed_alternatives=["தேவன்"], rejected_renderings=["கடவுள்"],
        category="key_term", source_lemma="אלהים", strong="H430",
        note="Prefer இறைவன் in poetic books.", provenance="imported", status="approved",
    )
    rules = project.terminology_rules()
    assert len(rules) == 1
    rule = rules[0]
    assert rule["conceptId"] == "god"
    assert rule["approvedRenderings"] == ["இறைவன்"]
    assert rule["allowedAlternatives"] == ["தேவன்"]
    assert rule["rejectedRenderings"] == ["கடவுள்"]
    assert rule["category"] == "key_term"
    assert rule["provenance"] == "imported"  # provenance preserved distinctly from status
    assert rule["status"] == "approved"
    assert rule["schemaVersion"] == 3  # termbase v3 (layered-rules 6.1)
    assert rule["matchMode"] == "exact" and rule["inflectedForms"] == {}


def test_terminology_rule_survives_project_restart(tmp_path):
    root = _build_minimal_project(tmp_path)
    TranslationCoreProject(root).record_terminology_rule("god", ["இறைவன்"], rejected_renderings=["கடவுள்"])
    reopened = TranslationCoreProject(root)  # a fresh instance, same on-disk project -- simulates restart
    rules = reopened.terminology_rules()
    assert len(rules) == 1
    assert rules[0]["conceptId"] == "god"
    assert rules[0]["rejectedRenderings"] == ["கடவுள்"]


def test_record_terminology_rule_upserts_by_concept_id(tmp_path):
    root = _build_minimal_project(tmp_path)
    project = TranslationCoreProject(root)
    project.record_terminology_rule("god", ["இறைவன்"])
    project.record_terminology_rule("god", ["தேவன்"])  # same concept, revised decision
    rules = project.terminology_rules()
    assert len(rules) == 1  # updated in place, not a duplicate row
    assert rules[0]["approvedRenderings"] == ["தேவன்"]


def test_record_terminology_rule_rejects_an_empty_entry(tmp_path):
    project = TranslationCoreProject(_build_minimal_project(tmp_path))
    with pytest.raises(ProjectError):
        project.record_terminology_rule("god")  # no approved/allowed/rejected forms at all


@pytest.mark.parametrize("kwargs", [
    {"status": "definitely-approved"}, {"category": "deity"}, {"provenance": "guessed"},
])
def test_record_terminology_rule_rejects_unknown_enum_values(tmp_path, kwargs):
    project = TranslationCoreProject(_build_minimal_project(tmp_path))
    with pytest.raises(ProjectError):
        project.record_terminology_rule("god", ["இறைவன்"], **kwargs)


# ---- end-to-end: real project -> Language QA pipeline ----

def test_terminology_deprecated_form_flagged_via_real_project_and_manager(tmp_path):
    root = _build_minimal_project(tmp_path)
    project = TranslationCoreProject(root)
    project.record_terminology_rule("god", ["இறைவன்"], rejected_renderings=["கடவுள்"],
                                    note="Prefer இறைவன்.")
    (root / "rut" / "1.json").write_text(
        json.dumps({"1": "அவர் கடவுள் ஆவார்."}, ensure_ascii=False), encoding="utf-8")
    manager = LanguageQaManager(debounce=0, yield_seconds=0)
    manager.bind(project)
    result = wait(manager)
    matches = [f for f in result["findings"] if f["rule"] == "terminology.deprecated-form"]
    assert len(matches) == 1
    assert matches[0]["originalText"] == "கடவுள்"
    assert matches[0]["severity"] == "high"
    assert "இறைவன்" in matches[0]["message"]
    assert matches[0]["status"] == "review-needed"


# ---- integration via LanguageQaManager (SimpleNamespace fixture, mirrors the
# language_qa.py test conventions already established for B1 / item 49) ----

def test_terminology_preferred_and_allowed_forms_are_clean(tmp_path):
    terms = [approved_term("god", ["கடவுள்"], preferred=["இறைவன்", "தேவன்"])]
    project = project_at(tmp_path, verses={"1": "இறைவன்", "2": "தேவன்"}, terminology=terms)
    manager = LanguageQaManager(debounce=0, yield_seconds=0)
    manager.bind(project)
    result = wait(manager)
    assert not [f for f in result["findings"] if f["rule"] == "terminology.deprecated-form"]


def test_terminology_finding_id_is_stable_across_an_unrelated_rescan(tmp_path):
    terms = [approved_term("god", ["கடவுள்"], preferred=["இறைவன்"])]
    project = project_at(tmp_path, verses={"1": "கடவுள்", "2": "வேறு வரி"}, terminology=terms)
    manager = LanguageQaManager(debounce=0, yield_seconds=0)
    manager.bind(project)
    first = wait(manager)
    before = next(f for f in first["findings"] if f["rule"] == "terminology.deprecated-form")
    (project.book_dir / "1.json").write_text(
        json.dumps({"1": "கடவுள்", "2": "புதிய வரி"}, ensure_ascii=False), encoding="utf-8")
    manager.invalidate("1")
    after_status = wait(manager)
    after = next(f for f in after_status["findings"] if f["rule"] == "terminology.deprecated-form")
    assert after["id"] == before["id"]


def test_terminology_finding_disappears_when_occurrence_is_edited_away(tmp_path):
    terms = [approved_term("god", ["கடவுள்"], preferred=["இறைவன்"])]
    project = project_at(tmp_path, verses={"1": "கடவுள் இருக்கிறார்."}, terminology=terms)
    manager = LanguageQaManager(debounce=0, yield_seconds=0)
    manager.bind(project)
    first = wait(manager)
    assert any(f["rule"] == "terminology.deprecated-form" for f in first["findings"])
    (project.book_dir / "1.json").write_text(
        json.dumps({"1": "இறைவன் இருக்கிறார்."}, ensure_ascii=False), encoding="utf-8")
    manager.invalidate("1")
    after = wait(manager)
    assert not [f for f in after["findings"] if f["rule"] == "terminology.deprecated-form"]


def test_terminology_finding_disappears_when_the_verse_is_removed(tmp_path):
    terms = [approved_term("god", ["கடவுள்"], preferred=["இறைவன்"])]
    project = project_at(tmp_path, verses={"1": "கடவுள் இருக்கிறார்.", "2": "வேறு வரி"}, terminology=terms)
    manager = LanguageQaManager(debounce=0, yield_seconds=0)
    manager.bind(project)
    first = wait(manager)
    assert any(f["rule"] == "terminology.deprecated-form" for f in first["findings"])
    (project.book_dir / "1.json").write_text(
        json.dumps({"2": "வேறு வரி"}, ensure_ascii=False), encoding="utf-8")
    manager.invalidate("1")
    after = wait(manager)
    assert not [f for f in after["findings"] if f["rule"] == "terminology.deprecated-form"]


def test_terminology_no_termbase_installed_produces_no_findings_or_errors(tmp_path):
    project = project_at(tmp_path, verses={"1": "கடவுள் இருக்கிறார்."})  # no terminology= at all
    manager = LanguageQaManager(debounce=0, yield_seconds=0)
    manager.bind(project)
    result = wait(manager)
    assert result["state"] == "completed"
    assert not [f for f in result["findings"] if f["rule"] == "terminology.deprecated-form"]


def test_terminology_malformed_loader_degrades_gracefully(tmp_path):
    def broken_loader():
        raise RuntimeError("workbench unavailable")
    project = project_at(tmp_path, verses={"1": "கடவுள் இருக்கிறார்."}, terminology=broken_loader)
    manager = LanguageQaManager(debounce=0, yield_seconds=0)
    manager.bind(project)
    result = wait(manager)
    assert result["state"] == "completed"
    assert not [f for f in result["findings"] if f["rule"] == "terminology.deprecated-form"]
    assert any("Terminology unavailable" in m for m in result["limitations"])


def test_terminology_change_invalidates_an_unrelated_cached_chapter(tmp_path):
    # A curated term changing must invalidate every cached chapter's findings,
    # not just the chapter that happened to trigger this pass -- otherwise a
    # chapter whose own text never changed would keep serving pre-change
    # results indefinitely from its content-hash cache.
    terms: list[dict] = []
    project = project_at(tmp_path, verses={"1": "கடவுள் இருக்கிறார்."}, terminology=lambda: terms)
    (project.book_dir / "2.json").write_text(
        json.dumps({"1": "வேறு வரி"}, ensure_ascii=False), encoding="utf-8")
    manager = LanguageQaManager(debounce=0, yield_seconds=0)
    manager.bind(project)
    first = wait(manager)
    assert not [f for f in first["findings"] if f["rule"] == "terminology.deprecated-form"]
    terms.append(approved_term("god", ["கடவுள்"], preferred=["இறைவன்"]))
    # Force a new pass via chapter 2 only -- chapter 1's own file is untouched.
    (project.book_dir / "2.json").write_text(
        json.dumps({"1": "புதிய வரி"}, ensure_ascii=False), encoding="utf-8")
    manager.invalidate("2")
    after = wait(manager)
    findings = [f for f in after["findings"] if f["rule"] == "terminology.deprecated-form"]
    assert len(findings) == 1
    assert findings[0]["chapter"] == "1"


# ---- decision suppression (termbase v2: Ignore/Use must actually stick) ----

def test_terminology_finding_includes_a_suggested_replacement(tmp_path):
    terms = [approved_term("god", ["கடவுள்"], preferred=["இறைவன்"])]
    project = project_at(tmp_path, verses={"1": "கடவுள் இருக்கிறார்."}, terminology=terms)
    manager = LanguageQaManager(debounce=0, yield_seconds=0)
    manager.bind(project)
    result = wait(manager)
    match = next(f for f in result["findings"] if f["rule"] == "terminology.deprecated-form")
    assert match["suggestedReplacement"] == "இறைவன்"


def test_terminology_ignored_decision_suppresses_the_finding(tmp_path):
    terms = [approved_term("god", ["கடவுள்"], preferred=["இறைவன்"])]
    finding_id = stable_finding_id("php", "1", "1", "terminology.deprecated-form", "கடவுள்", 1)
    decisions = [{"issueKey": finding_id, "decision": "ignored"}]
    project = project_at(tmp_path, verses={"1": "கடவுள் இருக்கிறார்."},
                         terminology=terms, decisions=decisions)
    manager = LanguageQaManager(debounce=0, yield_seconds=0)
    manager.bind(project)
    result = wait(manager)
    assert not [f for f in result["findings"] if f["rule"] == "terminology.deprecated-form"]


def test_terminology_accepted_decision_does_not_suppress_a_reintroduced_occurrence(tmp_path):
    # Caught in desktop acceptance, not hypothetical: "accepted" is recorded
    # as an audit trail when Use fixes a verse, but the finding id is
    # where-based (book/chapter/verse/text/occurrence-index), not tied to a
    # point in time. If the identical deprecated text is later reintroduced
    # at the same position -- paste, undo, retyping the same mistake -- it
    # produces the exact same id, and a stale "accepted" must not silently
    # suppress what is, in the text, a brand new violation. Only "ignored"
    # is a sticky, deliberate reviewer decision.
    terms = [approved_term("god", ["கடவுள்"], preferred=["இறைவன்"])]
    finding_id = stable_finding_id("php", "1", "1", "terminology.deprecated-form", "கடவுள்", 1)
    decisions = [{"issueKey": finding_id, "decision": "accepted"}]
    project = project_at(tmp_path, verses={"1": "கடவுள் இருக்கிறார்."},
                         terminology=terms, decisions=decisions)
    manager = LanguageQaManager(debounce=0, yield_seconds=0)
    manager.bind(project)
    result = wait(manager)
    assert any(f["rule"] == "terminology.deprecated-form" for f in result["findings"])


def test_terminology_decision_on_one_occurrence_does_not_suppress_another(tmp_path):
    terms = [approved_term("god", ["கடவுள்"], preferred=["இறைவன்"])]
    first_occurrence_id = stable_finding_id("php", "1", "1", "terminology.deprecated-form", "கடவுள்", 1)
    decisions = [{"issueKey": first_occurrence_id, "decision": "ignored"}]
    project = project_at(tmp_path, verses={"1": "கடவுள் அங்கு. பின்னர் கடவுள் மீண்டும்."},
                         terminology=terms, decisions=decisions)
    manager = LanguageQaManager(debounce=0, yield_seconds=0)
    manager.bind(project)
    result = wait(manager)
    matches = [f for f in result["findings"] if f["rule"] == "terminology.deprecated-form"]
    assert len(matches) == 1  # only the second, undecided occurrence remains


def test_terminology_no_decisions_loader_shows_every_finding(tmp_path):
    terms = [approved_term("god", ["கடவுள்"], preferred=["இறைவன்"])]
    project = project_at(tmp_path, verses={"1": "கடவுள் இருக்கிறார்."}, terminology=terms)  # no decisions=
    manager = LanguageQaManager(debounce=0, yield_seconds=0)
    manager.bind(project)
    result = wait(manager)
    assert any(f["rule"] == "terminology.deprecated-form" for f in result["findings"])


def test_terminology_malformed_decisions_loader_degrades_gracefully(tmp_path):
    def broken_decisions():
        raise RuntimeError("workbench unavailable")
    terms = [approved_term("god", ["கடவுள்"], preferred=["இறைவன்"])]
    project = project_at(tmp_path, verses={"1": "கடவுள் இருக்கிறார்."},
                         terminology=terms, decisions=broken_decisions)
    manager = LanguageQaManager(debounce=0, yield_seconds=0)
    manager.bind(project)
    result = wait(manager)
    assert result["state"] == "completed"
    assert any(f["rule"] == "terminology.deprecated-form" for f in result["findings"])
    assert any("Decisions unavailable" in m for m in result["limitations"])


def test_terminology_decision_change_invalidates_the_chapter_cache(tmp_path):
    terms = [approved_term("god", ["கடவுள்"], preferred=["இறைவன்"])]
    decisions: list[dict] = []
    project = project_at(tmp_path, verses={"1": "கடவுள் இருக்கிறார்."},
                         terminology=terms, decisions=lambda: decisions)
    (project.book_dir / "2.json").write_text(
        json.dumps({"1": "வேறு வரி"}, ensure_ascii=False), encoding="utf-8")
    manager = LanguageQaManager(debounce=0, yield_seconds=0)
    manager.bind(project)
    first = wait(manager)
    assert any(f["rule"] == "terminology.deprecated-form" for f in first["findings"])
    finding_id = stable_finding_id("php", "1", "1", "terminology.deprecated-form", "கடவுள்", 1)
    decisions.append({"issueKey": finding_id, "decision": "ignored"})
    # Force a new pass via chapter 2 only -- chapter 1's own file is untouched,
    # so without decisions folded into the cache key it would keep serving
    # the pre-decision finding from its content-hash cache.
    (project.book_dir / "2.json").write_text(
        json.dumps({"1": "புதிய வரி"}, ensure_ascii=False), encoding="utf-8")
    manager.invalidate("2")
    after = wait(manager)
    assert not [f for f in after["findings"] if f["rule"] == "terminology.deprecated-form"]


def test_verse_decide_ignored_actually_takes_effect_through_the_real_dispatcher(fixture_project):
    # Bug found in desktop acceptance testing, not hypothetical: recording an
    # "ignored" decision has no text change for Language QA to notice on its
    # own, unlike an edit -- without bridge_service.py's decide_verse also
    # invalidating the chapter, the finding would stay visible forever,
    # because nothing else would ever trigger a rescan.
    engine = BridgeEngine()
    engine._language_qa = LanguageQaManager(debounce=0, yield_seconds=0)
    try:
        assert call(engine, "project.open", {"path": str(fixture_project)})["success"]
        engine.project.record_terminology_rule("god", ["இறைவன்"], rejected_renderings=["தேவன்"])
        engine._language_qa.invalidate("1")  # pick up the newly-recorded term
        first = wait(engine._language_qa)
        finding = next(f for f in first["findings"] if f["rule"] == "terminology.deprecated-form")
        assert finding["originalText"] == "தேவன்"
        response = call(engine, "verse.decide", {
            "chapter": "1", "verse": "1", "findingId": finding["id"], "status": "ignored",
        })
        assert response["success"]
        after = wait(engine._language_qa)
        assert not [f for f in after["findings"] if f["rule"] == "terminology.deprecated-form"]
    finally:
        engine._language_qa.unbind()


def test_verse_edit_after_an_ignore_still_detects_a_new_occurrence_elsewhere(tmp_path):
    # Reproduces the maintainer's exact desktop sequence: ignore one
    # occurrence, then edit a DIFFERENT verse in the SAME chapter to
    # introduce a fresh occurrence of the same deprecated word, through the
    # real dispatcher end to end (project.open -> verse.decide -> verse.edit).
    root = tmp_path / "rut"
    align_dir = root / ".apps" / "translationCore" / "alignmentData" / "rut"
    align_dir.mkdir(parents=True)
    (root / "rut").mkdir(parents=True)
    (root / "manifest.json").write_text(json.dumps({
        "project": {"id": "rut", "name": "Ruth"},
        "target_language": {"id": "tam", "name": "Tamil"},
        "tc_version": "8", "tc_edit_version": "3.7.0",
    }), encoding="utf-8")
    (align_dir / "1.json").write_text(json.dumps({
        "1": {"alignments": [], "wordBank": []},
        "2": {"alignments": [], "wordBank": []},
    }), encoding="utf-8")
    (root / "rut" / "1.json").write_text(json.dumps({
        "1": "தேவன் இருக்கிறார்.", "2": "வேறு வரி.",
    }, ensure_ascii=False), encoding="utf-8")

    engine = BridgeEngine()
    engine._language_qa = LanguageQaManager(debounce=0, yield_seconds=0)
    try:
        assert call(engine, "project.open", {"path": str(root)})["success"]
        engine.project.record_terminology_rule("god", ["இறைவன்"], rejected_renderings=["தேவன்"])
        engine._language_qa.invalidate("1")
        first = wait(engine._language_qa)
        matches = [f for f in first["findings"] if f["rule"] == "terminology.deprecated-form"]
        assert len(matches) == 1 and matches[0]["verse"] == "1"

        decide = call(engine, "verse.decide", {
            "chapter": "1", "verse": "1", "findingId": matches[0]["id"], "status": "ignored",
        })
        assert decide["success"]
        ignored = wait(engine._language_qa)
        assert not [f for f in ignored["findings"] if f["rule"] == "terminology.deprecated-form"]

        edit = call(engine, "verse.edit", {"chapter": "1", "verse": "2", "newText": "தேவன் இருக்கிறார்."})
        assert edit["success"]
        after_edit = wait(engine._language_qa)
        matches2 = [f for f in after_edit["findings"] if f["rule"] == "terminology.deprecated-form"]
        assert len(matches2) == 1, after_edit
        assert matches2[0]["verse"] == "2"
    finally:
        engine._language_qa.unbind()


def test_terminology_list_and_record_fail_without_an_open_project():
    # #171: there was no RPC surface for this at all before -- guard the
    # same project.open gate every other book-scoped method already has.
    engine = BridgeEngine()
    listing = call(engine, "terminology.list")
    assert not listing["success"]
    assert listing["error"]["code"] == "project_error"
    recorded = call(engine, "terminology.record", {
        "conceptId": "god", "approvedRenderings": ["இறைவன்"], "rejectedRenderings": [],
    })
    assert not recorded["success"]
    assert recorded["error"]["code"] == "project_error"


def test_terminology_list_returns_empty_for_a_project_with_no_rules(fixture_project):
    engine = BridgeEngine()
    try:
        assert call(engine, "project.open", {"path": str(fixture_project)})["success"]
        listing = call(engine, "terminology.list")
        assert listing["success"]
        assert listing["result"]["rules"] == []
    finally:
        engine._language_qa.unbind()


def test_terminology_record_through_the_dispatcher_persists_and_list_reflects_it(fixture_project):
    # #171's actual fix: a rule can now be added without a backend script.
    engine = BridgeEngine()
    try:
        assert call(engine, "project.open", {"path": str(fixture_project)})["success"]
        recorded = call(engine, "terminology.record", {
            "conceptId": "god", "approvedRenderings": ["இறைவன்"], "rejectedRenderings": ["கடவுள்"],
        })
        assert recorded["success"]
        assert [r["conceptId"] for r in recorded["result"]["rules"]] == ["god"]
        rule = recorded["result"]["rules"][0]
        assert rule["approvedRenderings"] == ["இறைவன்"]
        assert rule["rejectedRenderings"] == ["கடவுள்"]

        listing = call(engine, "terminology.list")
        assert listing["success"]
        assert listing["result"]["rules"] == recorded["result"]["rules"]
    finally:
        engine._language_qa.unbind()


def test_terminology_record_rejects_a_missing_concept_id(fixture_project):
    engine = BridgeEngine()
    try:
        assert call(engine, "project.open", {"path": str(fixture_project)})["success"]
        recorded = call(engine, "terminology.record", {
            "conceptId": "", "approvedRenderings": ["இறைவன்"], "rejectedRenderings": [],
        })
        assert not recorded["success"]
        assert recorded["error"]["code"] == "project_error"
    finally:
        engine._language_qa.unbind()


def test_terminology_record_rejects_no_renderings_at_all(fixture_project):
    engine = BridgeEngine()
    try:
        assert call(engine, "project.open", {"path": str(fixture_project)})["success"]
        recorded = call(engine, "terminology.record", {
            "conceptId": "god", "approvedRenderings": [], "rejectedRenderings": [],
        })
        assert not recorded["success"]
        assert recorded["error"]["code"] == "project_error"
    finally:
        engine._language_qa.unbind()


def test_terminology_record_through_the_dispatcher_is_picked_up_by_the_next_language_qa_scan(fixture_project):
    # Ties directly back to the actual 2026-09-23 incident: a rule added
    # without a script must still be visible to Language QA's next pass.
    engine = BridgeEngine()
    engine._language_qa = LanguageQaManager(debounce=0, yield_seconds=0)
    try:
        assert call(engine, "project.open", {"path": str(fixture_project)})["success"]
        # Let the initial post-open scan fully finish BEFORE recording a rule
        # -- otherwise a fast test process can race the background thread and
        # pass by luck (the open's own first pass happening to run after the
        # record call), the same way the real desktop sequence never would:
        # a translator opens a project, its scan settles to "completed", and
        # only then adds a rule through Settings.
        initial = wait(engine._language_qa)
        assert not [f for f in initial["findings"] if f["rule"] == "terminology.deprecated-form"]

        # No manual invalidate() call here on purpose -- terminology.record
        # itself must trigger the rescan, the same way verse.decide/verse.edit
        # already do for their own writes. A manual invalidate() here would
        # mask exactly the bug desktop testing found: the RPC recorded the
        # rule but never told LanguageQaManager, so the double underline
        # never appeared until something unrelated happened to rescan.
        recorded = call(engine, "terminology.record", {
            "conceptId": "god", "approvedRenderings": ["இறைவன்"], "rejectedRenderings": ["தேவன்"],
        })
        assert recorded["success"]
        result = wait(engine._language_qa)
        matches = [f for f in result["findings"] if f["rule"] == "terminology.deprecated-form"]
        assert matches and matches[0]["originalText"] == "தேவன்"
    finally:
        engine._language_qa.unbind()


# ---- termbase v3 (layered-rules 6.1) -------------------------------------------

from tc_ai_bridge.terminology import CASE_SUFFIXES, join_suffix  # noqa: E402


@pytest.mark.parametrize("word,suffix,joined", [
    ("தேவன்", "ஐ", "தேவனை"), ("தேவன்", "இல்", "தேவனில்"), ("தேவன்", "உக்கு", "தேவனுக்கு"),
    ("கர்த்தர்", "கள்", "கர்த்தர்கள்"), ("யெகோவா", "க்கு", "யெகோவாக்கு"),
])
def test_a_case_ending_joins_as_written_tamil_does(word, suffix, joined):
    assert join_suffix(word, suffix) == unicodedata.normalize("NFC", joined)


def v3_term(**extra):
    return {**approved_term("god", ["தேவன்"], preferred=["இறைவன்", "கடவுள்"]), **extra}


def test_prefix_mode_matches_case_forms_at_medium_confidence_with_the_same_ending(tmp_path):
    manager = LanguageQaManager(debounce=0, yield_seconds=0)
    manager.bind(project_at(tmp_path, verses={"1": "அவன் தேவனை நோக்கினான்."},
                            terminology=[v3_term(matchMode="prefix", allowedAlternatives=["ஆண்டவன்"])]))
    [finding] = [f for f in wait(manager)["findings"] if f["rule"] == "terminology.deprecated-form"]
    manager.unbind()
    assert finding["originalText"] == "தேவனை" and finding["confidence"] == "medium"
    assert [s["text"] for s in finding["suggestions"]] == ["இறைவனை", "கடவுளை", "ஆண்டவனை"]
    assert "ending" in finding["message"]


def test_exact_mode_leaves_case_forms_alone(tmp_path):
    manager = LanguageQaManager(debounce=0, yield_seconds=0)
    manager.bind(project_at(tmp_path, verses={"1": "அவன் தேவனை நோக்கினான். தேவன் பேசினார்."},
                            terminology=[v3_term()]))
    matched = [f["originalText"] for f in wait(manager)["findings"] if f["rule"] == "terminology.deprecated-form"]
    manager.unbind()
    assert matched == ["தேவன்"]


def test_a_listed_inflected_form_is_as_authoritative_as_the_rendering(tmp_path):
    manager = LanguageQaManager(debounce=0, yield_seconds=0)
    manager.bind(project_at(tmp_path, verses={"1": "அவன் தேவனே என்றான்."},
                            terminology=[v3_term(inflectedForms={"தேவன்": ["தேவனே"]})]))
    [finding] = [f for f in wait(manager)["findings"] if f["rule"] == "terminology.deprecated-form"]
    manager.unbind()
    assert finding["originalText"] == "தேவனே" and finding["confidence"] == "high"
    assert [s["text"] for s in finding["suggestions"]] == ["இறைவன்", "கடவுள்"]


def test_terminology_record_takes_the_v3_fields_and_refuses_an_unknown_mode(fixture_project):
    engine = BridgeEngine()
    assert call(engine, "project.open", {"path": str(fixture_project)})["success"]
    recorded = call(engine, "terminology.record", {
        "conceptId": "god", "approvedRenderings": ["இறைவன்"], "rejectedRenderings": ["தேவன்"],
        "allowedAlternatives": ["கடவுள்"], "inflectedForms": {"தேவன்": ["தேவனே"]}, "matchMode": "prefix"})
    assert recorded["success"], recorded
    [rule] = recorded["result"]["rules"]
    assert (rule["matchMode"], rule["inflectedForms"], rule["allowedAlternatives"]) == (
        "prefix", {"தேவன்": ["தேவனே"]}, ["கடவுள்"])
    bad = call(engine, "terminology.record", {"conceptId": "x", "rejectedRenderings": ["a"],
                                               "matchMode": "fuzzy", "overwrite": True})
    assert not bad["success"]
    assert len(CASE_SUFFIXES) == len(set(CASE_SUFFIXES))
