"""Stage 8 -> Stage 9B target-hash contract, exercised through the real writer.

Every Stage 9B test that existed before this file hand-built its QA finding with
``runtime.text_hash(TEXT)`` already in ``targetContentHashes``.  Those tests are
valid statements about the *reader* contract, and they still pass -- but they
could not fail when the *writer* stopped honouring it.  Stage 8 was persisting
the Stage 6A target-inventory ``targetContentHash``, a SHA-256 of the JSON
object holding the whole analyzed range, into a field Stage 9B reads as an
exact per-verse hash.  The two can never be equal, so no finding the production
pipeline actually emitted could reach correction eligibility, however untouched
Scripture was.

These tests run the real Stage 5 -> 6A -> 6B -> 7 -> 8 pipeline and assert on
what it writes.  Nothing here patches a finding into shape first.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tc_ai_bridge.correction_eligibility import CorrectionEligibilityCode
from tc_ai_bridge.passage_semantic_models import (
    AffectedTargetSpan, CorrectionIntent, CoverageDimension,
)
from tc_ai_bridge.passage_semantic_runtime import PassageSemanticRuntime
from tc_ai_bridge.qa_target_hash import canonical_text_hash
from tests.support.qa_target_hash import CROSS_VERSE_PAIRS, ENGLISH_PHP, SOURCE_REFERENCE, TARGET_REFERENCE, _FixtureEmbeddings, _analyze, _codes, _confirm, _of_kind, _paired, _project


@pytest.fixture()
def cross_verse(tmp_path: Path) -> tuple[PassageSemanticRuntime, dict, dict]:
    runtime = _project(tmp_path, ENGLISH_PHP)
    audit, inventory = _analyze(
        runtime, _FixtureEmbeddings(_paired(CROSS_VERSE_PAIRS)), "1", "3", "1", "6")
    return runtime, audit, inventory


# --- Single-verse scope -----------------------------------------------------

def test_single_verse_stage8_writes_the_verse_hash_not_the_range_json_hash(
    tmp_path: Path,
) -> None:
    """The whole defect, at the smallest scope that can show it.

    On a one-verse range the Stage 6A fingerprint is a SHA-256 of a JSON object
    that happens to contain exactly that verse -- close enough to look right,
    and still never equal to the verse's own hash.

    The project holds only PHP 1:3: a passage window opened at a verse with no
    end reference runs to the end of the chapter, so a one-verse *scope* needs
    a one-verse book, not just a one-verse request.
    """
    runtime = _project(tmp_path, {"3": ENGLISH_PHP["3"]})
    audit, inventory = _analyze(runtime, None, "1", "3")
    assert inventory["rangeKey"] == f"{SOURCE_REFERENCE}..{SOURCE_REFERENCE}"

    verse_hash = canonical_text_hash(ENGLISH_PHP["3"])
    range_json_hash = hashlib.sha256(json.dumps(
        {SOURCE_REFERENCE: ENGLISH_PHP["3"]},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()

    assert inventory["targetContentHash"] == range_json_hash, (
        "Stage 6A keeps its range fingerprint; only the Stage 8 field changed")
    assert audit["findings"], "the real pipeline emitted no finding to check"
    for finding in audit["findings"]:
        assert finding["targetContentHashes"] == [verse_hash]
    assert verse_hash != range_json_hash


# --- Multi-verse scope ------------------------------------------------------

def test_multi_verse_findings_hash_only_their_own_target_references(
    cross_verse: tuple[PassageSemanticRuntime, dict, dict],
) -> None:
    """Stage 6A may fingerprint the whole range. Stage 8 may not."""
    runtime, audit, inventory = cross_verse
    range_hash = runtime.repository.target_content_hash(
        {f"PHP 1:{verse}": text for verse, text in ENGLISH_PHP.items()})
    per_verse = {
        f"PHP 1:{verse}": canonical_text_hash(text)
        for verse, text in ENGLISH_PHP.items()
    }

    assert inventory["targetContentHash"] == range_hash, (
        "the Stage 6A inventory still covers the whole analyzed range")
    assert len(audit["findings"]) > 1

    for finding in audit["findings"]:
        references = runtime.correction_eligibility.target_references(finding)
        assert finding["targetContentHashes"] == [per_verse[ref] for ref in references]
        assert range_hash not in finding["targetContentHashes"]


# --- Cross-verse: the canonical PHP case ------------------------------------

def test_cross_verse_finding_hashes_the_target_verse_not_the_source_verse(
    cross_verse: tuple[PassageSemanticRuntime, dict, dict],
) -> None:
    """Source semantics at PHP 1:3, target realization at PHP 1:6.

    `displayedReferences` legitimately carries both sides; only the target one
    may be content-addressed.
    """
    runtime, audit, _ = cross_verse
    finding = _of_kind(audit, "POSSIBLE_OVERTRANSLATION")

    source_references = {
        reference
        for unit_id in finding["sourceSemanticUnitIds"]
        for reference in runtime.repository.semantic_unit(unit_id)["displayedReferences"]
    }
    target_references = {
        reference
        for unit_id in finding["targetSemanticUnitIds"]
        for reference in runtime.repository.semantic_unit(unit_id)["displayedReferences"]
    }
    assert source_references == {SOURCE_REFERENCE}
    assert target_references == {TARGET_REFERENCE}
    # No manufactured same-verse source provenance: nothing invented a source
    # unit at PHP 1:6 to make the two references line up.
    assert finding["displayedReferences"] == [SOURCE_REFERENCE, TARGET_REFERENCE]

    assert finding["targetContentHashes"] == [canonical_text_hash(ENGLISH_PHP["6"])]
    assert canonical_text_hash(ENGLISH_PHP["3"]) not in finding["targetContentHashes"]
    assert runtime.correction_eligibility.target_references(finding) == (TARGET_REFERENCE,)


def test_cross_verse_finding_is_not_reported_as_edited_after_human_confirmation(
    cross_verse: tuple[PassageSemanticRuntime, dict, dict],
) -> None:
    runtime, audit, _ = cross_verse
    finding = _of_kind(audit, "POSSIBLE_OVERTRANSLATION")
    _confirm(runtime, finding)

    eligibility = runtime.correction_eligibility.evaluate(finding["id"])

    assert CorrectionEligibilityCode.TARGET_TEXT_CHANGED.value not in _codes(eligibility)
    assert CorrectionEligibilityCode.TARGET_REFERENCE_MISSING.value not in _codes(eligibility)
    assert eligibility.current_target_content_hash == canonical_text_hash(ENGLISH_PHP["6"])


# --- The production Stage 8 -> Stage 9B gate --------------------------------

def test_naturally_emitted_finding_reaches_correction_without_a_scripture_edit(
    cross_verse: tuple[PassageSemanticRuntime, dict, dict],
) -> None:
    """The gate this change exists for.

    A finding produced by the real Stage 5-8 pipeline, confirmed through the
    ordinary review service, must be correctable when Scripture has not been
    touched.  Before the fix this was impossible for every finding Bridge could
    actually emit.
    """
    runtime, audit, _ = cross_verse
    finding = _of_kind(audit, "POSSIBLE_ADDITION")
    stored = _confirm(runtime, finding)

    current = runtime.correction_eligibility.current_text_snapshot()
    references = runtime.correction_eligibility.target_references(stored)
    assert references == (TARGET_REFERENCE,)
    # The Stage 8 writer and the Stage 9B reader agree bit for bit.
    assert tuple(stored["targetContentHashes"]) == tuple(
        runtime.text_hash(current[reference]) for reference in references
    ) == (canonical_text_hash(ENGLISH_PHP["6"]),)

    eligibility = runtime.correction_eligibility.evaluate(finding["id"])
    assert _codes(eligibility) == {CorrectionEligibilityCode.ELIGIBLE.value}
    assert eligibility.eligible is True

    # ...and the correction proposal path is genuinely reachable, not merely
    # unblocked: creating one re-evaluates eligibility twice internally.
    text = ENGLISH_PHP["6"]
    start = text.index("only")
    content_hash = runtime.text_hash(text)
    proposal = runtime.correction_create_proposal(
        finding_id=finding["id"],
        intent=CorrectionIntent(
            failed_dimension=CoverageDimension.LEXICAL_CONTENT,
            observed_meaning="the target adds unlicensed specificity",
            required_meaning="drop the unsupported restriction",
            affected_source_semantic_unit_ids=(),
            affected_target_span=AffectedTargetSpan(
                displayed_reference=TARGET_REFERENCE,
                canonical_references=(TARGET_REFERENCE,),
                start_code_point=start, end_code_point=start + len("only"),
                original_text="only",
                target_text_revision=runtime.text_revision(TARGET_REFERENCE, content_hash),
                target_content_hash=content_hash,
            ),
        ),
        human_proposed_text="constant",
        explanation="Reviewer wording.", actor_id="Reviewer",
    )
    assert proposal["qaFindingId"] == finding["id"]
    # Nothing was applied: proposal creation never touches Scripture.
    assert runtime.correction_eligibility.current_text_snapshot()[TARGET_REFERENCE] == text


# --- Edit regressions -------------------------------------------------------

def test_editing_the_exact_target_verse_invalidates_the_snapshot(
    cross_verse: tuple[PassageSemanticRuntime, dict, dict],
) -> None:
    runtime, audit, _ = cross_verse
    finding = _of_kind(audit, "POSSIBLE_ADDITION")
    stored = _confirm(runtime, finding)
    assert runtime.correction_eligibility.evaluate(finding["id"]).eligible is True

    edited = ENGLISH_PHP["6"].replace("only ", "")
    runtime.project.apply_scripture_edit("1", "6", edited)

    eligibility = runtime.correction_eligibility.evaluate(finding["id"])
    assert eligibility.eligible is False
    assert CorrectionEligibilityCode.TARGET_TEXT_CHANGED.value in _codes(eligibility)
    assert stored["targetContentHashes"][0] != canonical_text_hash(edited)
    # The currentness machinery reacts as well.  The two are independent, and
    # both stand between a correction and the reviewer's stale wording.
    assert CorrectionEligibilityCode.FINDING_STALE.value in _codes(eligibility)


def test_editing_a_neighbouring_verse_leaves_the_correction_target_hash_alone(
    cross_verse: tuple[PassageSemanticRuntime, dict, dict],
) -> None:
    """The separation, stated as a test.

    The finding corrects PHP 1:6 and draws its source semantics from PHP 1:3.
    Editing PHP 1:3 is a semantic-freshness event, not a content-addressed
    conflict on the correction target: whether the finding survives is the
    invalidation machinery's call, and it answers here by staling it.  What
    must not happen is the exact-target CAS firing over a verse the correction
    never touches.
    """
    runtime, audit, _ = cross_verse
    finding = _of_kind(audit, "POSSIBLE_OVERTRANSLATION")
    stored = _confirm(runtime, finding)
    assert runtime.correction_eligibility.target_references(stored) == (TARGET_REFERENCE,)

    runtime.project.apply_scripture_edit("1", "3", ENGLISH_PHP["3"] + " indeed")

    eligibility = runtime.correction_eligibility.evaluate(finding["id"])
    current = runtime.correction_eligibility.current_text_snapshot()
    assert current[SOURCE_REFERENCE] != ENGLISH_PHP["3"], "the neighbour really was edited"
    assert current[TARGET_REFERENCE] == ENGLISH_PHP["6"], "the target verse was not"

    assert stored["targetContentHashes"] == [runtime.text_hash(current[TARGET_REFERENCE])]
    assert CorrectionEligibilityCode.TARGET_TEXT_CHANGED.value not in _codes(eligibility)
    assert CorrectionEligibilityCode.FINDING_STALE.value in _codes(eligibility)


# --- One hash implementation ------------------------------------------------

def test_one_hash_implementation_across_stage8_stage9b_and_application(
    cross_verse: tuple[PassageSemanticRuntime, dict, dict],
) -> None:
    runtime, audit, _ = cross_verse
    finding = _of_kind(audit, "POSSIBLE_ADDITION")
    text = ENGLISH_PHP["6"]

    written = finding["targetContentHashes"][0]
    assert written == canonical_text_hash(text) == runtime.text_hash(text)
    assert written == PassageSemanticRuntime.text_hash(text)
    # And the value correction application validates against, byte for byte.
    assert written == hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- Findings persisted by the broken writer --------------------------------

def test_a_range_hash_left_by_the_old_writer_is_blocked_not_reinterpreted(
    cross_verse: tuple[PassageSemanticRuntime, dict, dict],
) -> None:
    """No heuristic hash-type detection, and no silent reinterpretation.

    A finding still carrying the Stage 6A range fingerprint is treated as what
    it literally is -- a hash that does not match the verse -- and blocks.  Such
    a finding needs re-analysis before it can be corrected; see BUILD_LOG.
    """
    runtime, audit, inventory = cross_verse
    finding = _of_kind(audit, "POSSIBLE_ADDITION")
    _confirm(runtime, finding)
    assert runtime.correction_eligibility.evaluate(finding["id"]).eligible is True

    legacy = runtime.repository.qa_finding(finding["id"])
    legacy["targetContentHashes"] = [inventory["targetContentHash"]]
    with runtime.repository._connect() as conn:
        conn.execute(
            "UPDATE qa_findings SET payload_json=? WHERE id=?",
            (json.dumps(legacy, ensure_ascii=False), finding["id"]),
        )
        conn.commit()

    eligibility = runtime.correction_eligibility.evaluate(finding["id"])
    assert eligibility.eligible is False
    assert CorrectionEligibilityCode.TARGET_TEXT_CHANGED.value in _codes(eligibility)
