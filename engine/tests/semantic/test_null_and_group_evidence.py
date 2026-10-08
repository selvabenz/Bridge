"""#218: Stage 6B/8 read cross-verse link groups and null alignment decisions.

A cross-verse group (#217) is one precedent with all its members, the shape a
same-verse N:M tC group already has. A reasoned null decision (#216) explains an
absence in Stage 8: a source meaning realised grammatically or implicitly is
COVERED_BY_RESTRUCTURING rather than POSSIBLY_MISSING, and a target word that is
grammar or explicitation is supported. Stage 8 reads the decisions; it never
re-runs Stage 6B.
"""
from __future__ import annotations

from pathlib import Path

from tc_ai_bridge.meaning_analysis import MeaningAnalysisEngine
from tc_ai_bridge.models import TokenRef
from tc_ai_bridge.qa_audit import QaAuditEngine, QaAuditPolicy
from tc_ai_bridge.semantic_location import SemanticLocationEngine
from tc_ai_bridge.word_alignment_evidence import alignment_precedents_for_range, null_precedents_for_range
from tests.support.semantic import semantic_runtime

THEOS = TokenRef("Θεῷ", 1, 1, strong="G23160", lemma="θεός", morph="Gr,N,,,,,DMS,")
EUCHARISTO = TokenRef("εὐχαριστῶ", 1, 1, strong="G21680", lemma="εὐχαριστέω", morph="Gr,V,IPA1,,S,")
DEESEI = TokenRef("δεήσει", 1, 1, strong="G11620", lemma="δέησις", morph="Gr,N,,,,,DFS,")


def _run_qa(runtime, chapter="1", verse="3", end_chapter="", end_verse=""):
    location = SemanticLocationEngine(runtime).run_range(chapter, verse, end_chapter, end_verse)
    meaning = MeaningAnalysisEngine(runtime).run_range(
        chapter, verse, end_chapter, end_verse, location_run_id=location["id"],
    )
    return QaAuditEngine(runtime).run_range(chapter, verse, end_chapter, end_verse, meaning_run_id=meaning["id"])


def _source_unit(runtime, lemma, chapter="1", verse="3"):
    source = runtime.source_semantic.build_range(chapter, verse)
    return next(
        unit for unit in source["units"]
        if unit["kind"] == "LEXICAL" and unit.get("semanticFeatures", {}).get("lemma") == lemma
    )


def _target_token(runtime, word, chapter="1", verse="3", end_chapter="", end_verse=""):
    target = runtime.target_semantic.build_range(chapter, verse, end_chapter, end_verse)
    return next(token for token in target["tokens"] if token["rawForm"] == word)


# --- pure policy ---------------------------------------------------------------

def test_policy_a_null_explains_an_absence_and_nothing_else():
    owner = {"id": "u1", "accountingRole": "PRIMARY", "auditEligibility": "ELIGIBLE"}
    null = {"reason": "GRAMMATICAL", "origin": "ai-auto", "note": "article"}
    not_located = [{"id": "r1", "locationOutcome": "NOT_LOCATED"}]
    assert QaAuditPolicy.source_coverage_for(owner, not_located, {}, False)[0].value == "POSSIBLY_MISSING"
    status, reason = QaAuditPolicy.source_coverage_for(owner, not_located, {}, False, null_precedent=null)
    assert status.value == "COVERED_BY_RESTRUCTURING"
    assert "automatic alignment pass" in reason and "grammatically" in reason and "article" in reason
    # No relationship at all: also explained.
    assert QaAuditPolicy.source_coverage_for(owner, [], {}, False, null_precedent=null)[0].value == "COVERED_BY_RESTRUCTURING"
    # An ambiguous or incomplete search is still not a conclusion.
    ambiguous = [{"id": "r1", "locationOutcome": "AMBIGUOUS"}]
    assert QaAuditPolicy.source_coverage_for(owner, ambiguous, {}, False, null_precedent=null)[0].value == "UNCERTAIN"
    incomplete = [{"id": "r1", "locationOutcome": "SEARCH_INCOMPLETE"}]
    assert QaAuditPolicy.source_coverage_for(owner, incomplete, {}, False, null_precedent=null)[0].value == "UNCERTAIN"


def test_policy_a_target_null_supports_the_word_before_any_word_list():
    unit = {"id": "t1", "accountingRole": "PRIMARY", "auditEligibility": "ELIGIBLE", "normalizedSurface": "holy"}
    # "holy" is on the hard-coded unsupported-specificity list ...
    assert QaAuditPolicy.target_support_for(unit, [], {})[0].value == "POSSIBLY_UNSUPPORTED"
    # ... but a reasoned decision on this exact word comes first.
    grammar = QaAuditPolicy.target_support_for(unit, [], {}, null_precedent={"reason": "GRAMMATICAL"})
    assert grammar[0].value == "GRAMMATICALLY_REQUIRED"
    explicit = QaAuditPolicy.target_support_for(unit, [], {}, null_precedent={"reason": "EXPLICITATION"})
    assert explicit[0].value == "EXPLICITATION_SUPPORTED"


def test_a_unit_is_explained_only_when_every_token_carries_the_same_reason():
    by_token = {"a": {"reason": "GRAMMATICAL"}, "b": {"reason": "IMPLICIT"}}
    assert QaAuditEngine._unit_null({"tokenInstanceIds": ["a"]}, by_token) == {"reason": "GRAMMATICAL"}
    assert QaAuditEngine._unit_null({"tokenInstanceIds": ["a", "b"]}, by_token) is None
    assert QaAuditEngine._unit_null({"tokenInstanceIds": ["a", "c"]}, by_token) is None
    assert QaAuditEngine._unit_null({"tokenInstanceIds": []}, by_token) is None
    assert QaAuditEngine._unit_null(None, by_token) is None


# --- evidence ------------------------------------------------------------------

def test_null_decisions_resolve_to_the_stage_5_and_6a_instance_ids(tmp_path: Path):
    runtime = semantic_runtime(tmp_path, project_prefix="nulls", language="ta",
                               chapters={"1": {"3": "நான் தேவனை ஸ்தோத்திரிக்கிறேன்"}})
    assert null_precedents_for_range(runtime, "1", "3") == {"source": {}, "target": {}}
    runtime.project.null_decisions.set("1", "3", "source", THEOS, "IMPLICIT", note="carried by context")
    runtime.project.null_decisions.set("1", "3", "target", TokenRef("நான்", 1, 1), "EXPLICITATION")
    found = null_precedents_for_range(runtime, "1", "3")
    theos_id = _source_unit(runtime, "θεός")["tokenInstanceIds"][0]
    nan_id = _target_token(runtime, "நான்")["id"]
    assert found["source"] == {theos_id: {"reason": "IMPLICIT", "origin": "human", "note": "carried by context"}}
    assert set(found["target"]) == {nan_id}
    # A decision outside the range is not evidence for this range.
    assert null_precedents_for_range(runtime, "1", "4") == {"source": {}, "target": {}}


def test_a_cross_verse_group_is_one_precedent_with_every_member(tmp_path: Path):
    runtime = semantic_runtime(tmp_path, project_prefix="groups", language="ta",
                               chapters={"1": {"3": "நான் தேவனை", "4": "ஸ்தோத்திரிக்கிறேன் எப்பொழுதும்"}})
    # 1:3's εὐχαριστῶ is realised by two words... of 1:4 for this test's purposes: 1:N.
    runtime.project.cross_verse_links.link_group(
        "1", "3", [EUCHARISTO], "1", "4", [TokenRef("ஸ்தோத்திரிக்கிறேன்", 1, 1), TokenRef("எப்பொழுதும்", 1, 1)],
    )
    precedents = alignment_precedents_for_range(runtime, "1", "3", "1", "4")
    source_id = _source_unit(runtime, "εὐχαριστέω")["tokenInstanceIds"][0]
    targets = {
        _target_token(runtime, word, "1", "3", "1", "4")["id"]
        for word in ("ஸ்தோத்திரிக்கிறேன்", "எப்பொழுதும்")
    }
    assert len(precedents) == 1
    assert precedents[0]["sourceTokenInstanceIds"] == [source_id]
    assert set(precedents[0]["targetTokenInstanceIds"]) == targets


def test_a_group_with_an_unresolvable_member_contributes_nothing(tmp_path: Path):
    runtime = semantic_runtime(tmp_path, project_prefix="groups-bad", language="ta",
                               chapters={"1": {"3": "நான் தேவனை", "4": "ஸ்தோத்திரிக்கிறேன்"}})
    runtime.project.cross_verse_links.link_group(
        "1", "3", [EUCHARISTO], "1", "4", [TokenRef("ஸ்தோத்திரிக்கிறேன்", 1, 1), TokenRef("இல்லாதது", 1, 1)],
    )
    assert alignment_precedents_for_range(runtime, "1", "3", "1", "4") == []


# --- Stage 8 end to end ----------------------------------------------------------

def test_a_grammatical_source_word_is_covered_not_an_omission(tmp_path: Path):
    runtime = semantic_runtime(tmp_path, project_prefix="nullqa", language="ta",
                               chapters={"1": {"3": "நான் உங்களை நினைக்கும்"}})
    theos = _source_unit(runtime, "θεός")

    def omitted(run):
        return [f for f in run["findings"] if f["kind"] == "POSSIBLE_OMISSION" and theos["id"] in f["sourceSemanticUnitIds"]]

    before = _run_qa(runtime)
    assert omitted(before), "without a decision, an unlocated θεός is a possible omission"

    runtime.project.null_decisions.set("1", "3", "source", THEOS, "IMPLICIT", note="named in v.6")
    runtime.synchronize_alignment_state()
    after = _run_qa(runtime)
    assert after["fingerprint"] != before["fingerprint"]
    assert omitted(after) == []
    coverage = QaAuditEngine(runtime).get_source_coverage(after["id"])
    account = next(item for item in coverage if item["auditOwnerUnitId"] == theos["id"])
    assert account["coverageStatus"] == "COVERED_BY_RESTRUCTURING"
    # Every other unlocated word is still reported: the decision covers its own token only.
    assert any(f["kind"] == "POSSIBLE_OMISSION" for f in after["findings"])


def test_a_target_null_turns_an_uncertain_word_into_a_supported_one(tmp_path: Path):
    runtime = semantic_runtime(tmp_path, project_prefix="nullqa-t", language="ta",
                               chapters={"1": {"3": "நான் உங்களை நினைக்கும்"}})
    nan_unit_tokens = {_target_token(runtime, "நான்")["id"]}
    runtime.project.null_decisions.set("1", "3", "target", TokenRef("நான்", 1, 1), "GRAMMATICAL")
    runtime.synchronize_alignment_state()
    run = _run_qa(runtime)
    target = runtime.target_semantic.build_range("1", "3")
    units = {u["id"]: u for u in target["units"]}
    support = QaAuditEngine(runtime).get_target_support(run["id"])
    statuses = {
        item["coverageStatus"] for item in support
        if set(units.get(item["auditOwnerUnitId"], {}).get("tokenInstanceIds") or ()) == nan_unit_tokens
    }
    assert statuses == {"GRAMMATICALLY_REQUIRED"}
