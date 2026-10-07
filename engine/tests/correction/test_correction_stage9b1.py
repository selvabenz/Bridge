"""Stage 9B.1 correction wording generation and proposal lifecycle.

These tests intentionally stop before application: no API in this stage may
write Scripture, mark a finding CORRECTED, or trigger analysis.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tc_ai_bridge.correction_eligibility import (
    CorrectionEligibilityService,
)
from tc_ai_bridge.correction_wording import (
    ConfiguredCorrectionSuggestionProvider,
    CorrectionSuggestionResult,
    CorrectionWordingService,
    NoCorrectionSuggestionProvider,
)
from tc_ai_bridge.passage_semantic_models import (
    ActorType,
    AffectedTargetSpan,
    CorrectionCreationMode,
    CorrectionIntent,
    CoverageDimension,
    LifecycleStatus,
    QaDisposition,
    ReviewStatus,
    to_wire,
)
from tc_ai_bridge.passage_semantic_repository import (
    DATABASE_SCHEMA_VERSION,
    FoundationConflict,
    FoundationRepository,
    FoundationValidationError,
)
from tests.support.correction_9b1 import REFERENCE, TEXT, _FixtureProvider, _Review, _Runtime, _intent


def test_human_authored_proposal_works_offline_and_defaults_to_approved(tmp_path: Path) -> None:
    """V11-003 (#57): a human-authored proposal needs no Edit->Save round-trip
    to become reviewed -- it is already self-review by its own author (see
    docs/archive/V11-003_ISSUE57_PROMPT.md Part A)."""
    runtime = _Runtime(tmp_path / "semantic.sqlite3")
    service = CorrectionWordingService(runtime, NoCorrectionSuggestionProvider())
    before = TEXT

    proposal = service.create_proposal(
        finding_id="qa-1", intent=_intent(runtime),
        human_proposed_text="என் தேவனுக்கு", explanation="Human wording.",
        actor_id="Reviewer",
    )

    assert proposal["proposedText"] == "என் தேவனுக்கு"
    assert proposal["creationMode"] == CorrectionCreationMode.HUMAN_AUTHORED.value
    assert proposal["reviewStatus"] == ReviewStatus.HUMAN_APPROVED.value
    assert proposal["lifecycleStatus"] == LifecycleStatus.ACTIVE.value
    assert runtime.correction_eligibility.calls == [("qa-1", ()), ("qa-1", ())]
    assert runtime.repository.qa_finding("qa-1")["qaDisposition"] == QaDisposition.UNRESOLVED.value
    assert TEXT == before
    # The CREATED event already snapshots reviewStatus == HUMAN_APPROVED --
    # no second event is appended just for the default (W1 requirement).
    history = runtime.repository.correction_proposal_history(proposal["id"])
    assert [event["eventType"] for event in history] == ["CREATED"]
    assert history[0]["proposalSnapshot"]["reviewStatus"] == ReviewStatus.HUMAN_APPROVED.value


def test_human_authored_proposal_with_only_whitespace_wording_stays_unreviewed(
    tmp_path: Path,
) -> None:
    """Nobody has written anything to review yet -- whitespace is not text.
    (Also the only way to reach _build_proposal's `else` branch: a fully
    empty proposed_text is rejected before review_status is even computed.)"""
    runtime = _Runtime(tmp_path / "semantic.sqlite3")
    service = CorrectionWordingService(runtime, NoCorrectionSuggestionProvider())

    proposal = service.create_proposal(
        finding_id="qa-1", intent=_intent(runtime),
        human_proposed_text="   ", explanation="",
        actor_id="Reviewer",
    )

    assert proposal["creationMode"] == CorrectionCreationMode.HUMAN_AUTHORED.value
    assert proposal["reviewStatus"] == ReviewStatus.UNREVIEWED.value


def test_new_cross_verse_proposal_keeps_source_and_target_references_distinct(
    tmp_path: Path,
) -> None:
    runtime = _Runtime(tmp_path / "semantic.sqlite3")
    base = _intent(runtime)
    base_span = base.affected_target_span
    intent = CorrectionIntent(
        failed_dimension=base.failed_dimension,
        observed_meaning=base.observed_meaning,
        required_meaning=base.required_meaning,
        affected_source_semantic_unit_ids=("source-unit-php-1-3",),
        affected_target_span=AffectedTargetSpan(
            displayed_reference="PHP 1:6",
            canonical_references=("PHP 1:6",),
            start_code_point=base_span.start_code_point,
            end_code_point=base_span.end_code_point,
            original_text=base_span.original_text,
            target_text_revision=base_span.target_text_revision,
            target_content_hash=base_span.target_content_hash,
        ),
    )
    context = runtime.qa_review.get_finding("qa-1")
    context["finding"]["sourceSemanticUnitIds"] = ["source-unit-php-1-3"]
    context["source"] = [{
        "id": "source-unit-php-1-3",
        "displayedReferences": ["PHP 1:3"],
        "canonicalReferences": ["PHP 1:3"],
        "rawSurface": "τῷ θεῷ μου",
    }]

    proposal = CorrectionWordingService(runtime)._build_proposal(
        finding_id="qa-1", intent=intent, context=context,
        human_proposed_text="என் தேவனையே", explanation="Reviewed wording.",
        request_suggestion=False, actor_id="Reviewer",
    )

    assert to_wire(proposal)["affectedReferences"] == ["PHP 1:3", "PHP 1:6"]
    assert proposal.intent.affected_source_semantic_unit_ids == ("source-unit-php-1-3",)
    assert proposal.intent.affected_target_span.displayed_reference == "PHP 1:6"


def test_provider_suggestion_persists_metadata_evidence_and_alternatives(tmp_path: Path) -> None:
    runtime = _Runtime(tmp_path / "semantic.sqlite3")
    provider = _FixtureProvider()
    proposal = CorrectionWordingService(runtime, provider).create_proposal(
        finding_id="qa-1", intent=_intent(runtime), request_suggestion=True,
        actor_id="Reviewer",
    )

    assert provider.calls == 1
    assert proposal["proposedText"] == "என் தேவனை"
    assert proposal["creationMode"] == CorrectionCreationMode.MACHINE_SUGGESTED.value
    assert proposal["reviewStatus"] == ReviewStatus.AI_PROPOSED.value
    assert proposal["providerMetadata"] == {
        "providerName": "fixture",
        "model": "fixture-multilingual-v1",
        "modelVersionId": "",
        "promptPolicyVersion": "correction-wording-v1",
        "responseFingerprint": "response-hash",
    }
    assert proposal["evidenceIds"] == ["evidence-1"]
    assert proposal["alternatives"][0]["proposedText"] == "என்னுடைய தேவனை"


def test_inline_meaning_evidence_is_cited_without_dangling_repository_edge(tmp_path: Path) -> None:
    runtime = _Runtime(tmp_path / "semantic.sqlite3")

    class InlineReview(_Review):
        def get_finding(self, finding_id):
            detail = super().get_finding(finding_id)
            detail["finding"]["evidenceIds"] = []
            detail["supportingEvidence"] = [{
                "id": "meaning-inline-1", "evidenceSource": "MEANING_ASSESSMENT",
            }]
            return detail

    class InlineProvider(_FixtureProvider):
        def suggest(self, context):
            base = super().suggest(context)
            return CorrectionSuggestionResult(
                proposed_text=base.proposed_text, explanation=base.explanation,
                evidence_ids=("meaning-inline-1",), provider_name=base.provider_name,
                model=base.model, response_fingerprint=base.response_fingerprint,
            )

    runtime.qa_review = InlineReview()
    proposal = CorrectionWordingService(runtime, InlineProvider()).create_proposal(
        finding_id="qa-1", intent=_intent(runtime), request_suggestion=True,
        actor_id="Reviewer",
    )
    assert proposal["evidenceIds"] == ["meaning-inline-1"]
    reopened = FoundationRepository(tmp_path / "semantic.sqlite3")
    assert reopened.recovery_check()["ok"] is True


def test_configured_provider_receives_constrained_context_and_never_persists_secret() -> None:
    class Client:
        model = "gpt-multilingual"
        api_key = "never-persist-this-secret"

        def _post_structured(self, instructions, input_text, schema_name, schema):
            assert "smallest defensible change" in instructions
            assert schema_name == "bridge_correction_wording_v1"
            assert self.api_key not in input_text
            assert "project files" not in input_text
            return {
                "proposedText": "wording", "explanation": "grounded",
                "evidenceIds": [], "alternatives": [], "warnings": [],
            }

    client = Client()
    result = ConfiguredCorrectionSuggestionProvider(client).suggest({
        "intent": {"failedDimension": "LEXICAL_CONTENT"},
        "currentTarget": {"displayedReference": "PHP 1:3", "verseText": "text"},
        "sourceSemanticUnits": [{"id": "source-1"}],
        "resourceEvidence": [{"id": "tn-1"}],
    })
    wire = json.dumps(to_wire(result), ensure_ascii=False)
    assert result.model == "gpt-multilingual"
    assert Client.api_key not in wire
    assert client.last_privacy_manifest == {
        "reference": "PHP 1:3",
        "sentFields": ["currentTarget", "intent", "resourceEvidence", "sourceSemanticUnits"],
        "containsScripture": True,
        "containsOriginalLanguage": True,
        "containsTranslationHelps": True,
        "unrelatedProjectFilesSent": False,
    }


def test_provider_unavailable_falls_back_only_when_human_wording_exists(tmp_path: Path) -> None:
    runtime = _Runtime(tmp_path / "semantic.sqlite3")
    service = CorrectionWordingService(runtime, NoCorrectionSuggestionProvider())
    proposal = service.create_proposal(
        finding_id="qa-1", intent=_intent(runtime), request_suggestion=True,
        human_proposed_text="மனிதர் எழுதிய உரை", actor_id="Reviewer",
    )
    assert proposal["proposedText"] == "மனிதர் எழுதிய உரை"
    assert "PROVIDER_UNAVAILABLE" in proposal["warnings"]

    runtime2 = _Runtime(tmp_path / "other.sqlite3")
    with pytest.raises(FoundationValidationError, match="provider is unavailable"):
        CorrectionWordingService(runtime2, NoCorrectionSuggestionProvider()).create_proposal(
            finding_id="qa-1", intent=_intent(runtime2), request_suggestion=True,
            actor_id="Reviewer",
        )


def test_eligibility_is_rechecked_at_creation_and_blocks_persistence(tmp_path: Path) -> None:
    runtime = _Runtime(tmp_path / "semantic.sqlite3")
    runtime.correction_eligibility.eligible = False
    with pytest.raises(FoundationValidationError, match="not eligible"):
        CorrectionWordingService(runtime).create_proposal(
            finding_id="qa-1", intent=_intent(runtime),
            human_proposed_text="text", actor_id="Reviewer",
        )
    assert runtime.repository.correction_proposals_for_finding("qa-1") == []


def test_provider_delay_cannot_bypass_immediate_pre_persistence_recheck(tmp_path: Path) -> None:
    runtime = _Runtime(tmp_path / "semantic.sqlite3")

    class ChangingProvider(_FixtureProvider):
        def suggest(self, context):
            result = super().suggest(context)
            runtime.correction_eligibility.eligible = False
            return result

    with pytest.raises(FoundationValidationError, match="not eligible"):
        CorrectionWordingService(runtime, ChangingProvider()).create_proposal(
            finding_id="qa-1", intent=_intent(runtime), request_suggestion=True,
            actor_id="Reviewer",
        )
    assert runtime.repository.correction_proposals_for_finding("qa-1") == []


def test_changed_exact_target_span_is_a_revision_conflict(tmp_path: Path) -> None:
    runtime = _Runtime(tmp_path / "semantic.sqlite3")
    base = _intent(runtime)
    span = base.affected_target_span
    changed_intent = CorrectionIntent(
        failed_dimension=base.failed_dimension,
        observed_meaning=base.observed_meaning,
        required_meaning=base.required_meaning,
        affected_source_semantic_unit_ids=(),
        affected_target_span=AffectedTargetSpan(
            displayed_reference=span.displayed_reference,
            canonical_references=span.canonical_references,
            start_code_point=span.start_code_point,
            end_code_point=span.end_code_point,
            original_text="x" * len(span.original_text),
            target_text_revision=span.target_text_revision,
            target_content_hash=span.target_content_hash,
        ),
    )
    with pytest.raises(FoundationConflict, match="revision conflict"):
        CorrectionWordingService(runtime).create_proposal(
            finding_id="qa-1", intent=changed_intent,
            human_proposed_text="wording", actor_id="Reviewer",
        )


def test_real_backend_eligibility_authorizes_only_confirmed_current_finding(tmp_path: Path) -> None:
    runtime = _Runtime(tmp_path / "semantic.sqlite3")
    runtime.repository.update_qa_disposition(
        "qa-1", QaDisposition.CONFIRMED_TRANSLATION_ERROR, 1, "Reviewer",
        review_status=ReviewStatus.HUMAN_APPROVED,
    )
    finding = runtime.repository.qa_finding("qa-1")
    finding.update({
        "displayedReferences": [REFERENCE],
        "targetContentHashes": [runtime.text_hash(TEXT)],
    })
    with runtime.repository._connect() as conn:
        conn.execute(
            "UPDATE qa_findings SET payload_json=? WHERE id=?",
            (json.dumps(finding, ensure_ascii=False), "qa-1"),
        )
        conn.commit()
    actual = CorrectionEligibilityService(runtime)
    actual.current_text_snapshot = lambda: {REFERENCE: TEXT}  # type: ignore[method-assign]
    runtime.correction_eligibility = actual
    runtime.correction_wording = CorrectionWordingService(runtime)

    proposal = runtime.correction_wording.create_proposal(
        finding_id="qa-1", intent=_intent(runtime),
        human_proposed_text="current wording", actor_id="Reviewer",
    )
    assert proposal["qaFindingId"] == "qa-1"


def test_edit_is_cas_protected_and_preserves_revision_history(tmp_path: Path) -> None:
    runtime = _Runtime(tmp_path / "semantic.sqlite3")
    service = CorrectionWordingService(runtime)
    created = service.create_proposal(
        finding_id="qa-1", intent=_intent(runtime),
        human_proposed_text="first", actor_id="Reviewer",
    )
    edited = service.edit_proposal(
        created["id"], proposed_text="second", explanation="edited",
        expected_revision=1, actor_id="Reviewer",
    )
    assert edited["revision"] == 2
    assert edited["proposedText"] == "second"
    history = runtime.repository.correction_proposal_history(created["id"])
    assert [event["eventType"] for event in history] == ["CREATED", "EDITED"]
    assert history[0]["proposalSnapshot"]["proposedText"] == "first"
    with pytest.raises(FoundationConflict):
        service.edit_proposal(
            created["id"], proposed_text="third", expected_revision=1,
            actor_id="Reviewer",
        )


def test_human_edit_preserves_original_machine_suggestion(tmp_path: Path) -> None:
    runtime = _Runtime(tmp_path / "semantic.sqlite3")
    service = CorrectionWordingService(runtime, _FixtureProvider())
    created = service.create_proposal(
        finding_id="qa-1", intent=_intent(runtime), request_suggestion=True,
        actor_id="Reviewer",
    )
    edited = service.edit_proposal(
        created["id"], proposed_text="மனிதர் திருத்திய உரை",
        expected_revision=1, actor_id="Reviewer",
    )
    assert edited["creationMode"] == CorrectionCreationMode.MACHINE_SUGGESTED_HUMAN_EDITED.value
    assert edited["originalSuggestedText"] == "என் தேவனை"
    assert edited["proposedText"] == "மனிதர் திருத்திய உரை"


def test_stale_proposal_cannot_be_edited_as_current(tmp_path: Path) -> None:
    runtime = _Runtime(tmp_path / "semantic.sqlite3")
    service = CorrectionWordingService(runtime)
    created = service.create_proposal(
        finding_id="qa-1", intent=_intent(runtime),
        human_proposed_text="wording", actor_id="Reviewer",
    )
    with runtime.repository._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        runtime.repository._stale_generic_dependencies(conn, "QA_FINDING", "qa-1")
        conn.commit()
    with pytest.raises(FoundationValidationError, match="active current proposal"):
        service.edit_proposal(
            created["id"], proposed_text="new", expected_revision=2,
            actor_id="Reviewer",
        )
    assert runtime.repository.correction_proposal_history(created["id"])[-1]["eventType"] == "STALE"


def test_reject_preserves_proposal_and_does_not_change_finding_disposition(tmp_path: Path) -> None:
    runtime = _Runtime(tmp_path / "semantic.sqlite3")
    service = CorrectionWordingService(runtime)
    created = service.create_proposal(
        finding_id="qa-1", intent=_intent(runtime),
        human_proposed_text="wording", actor_id="Reviewer",
    )
    rejected = service.reject_proposal(
        created["id"], expected_revision=1, actor_id="Reviewer",
        reason="Not natural Tamil.",
    )
    assert rejected["reviewStatus"] == ReviewStatus.HUMAN_REJECTED.value
    assert rejected["lifecycleStatus"] == LifecycleStatus.INACTIVE.value
    assert runtime.repository.qa_finding("qa-1")["qaDisposition"] == QaDisposition.UNRESOLVED.value
    assert runtime.repository.correction_proposal(created["id"])["proposedText"] == "wording"


def test_regenerate_creates_new_record_and_supersedes_old_atomically(tmp_path: Path) -> None:
    runtime = _Runtime(tmp_path / "semantic.sqlite3")
    service = CorrectionWordingService(runtime, _FixtureProvider())
    original = service.create_proposal(
        finding_id="qa-1", intent=_intent(runtime),
        human_proposed_text="old wording", actor_id="Reviewer",
    )
    replacement = service.regenerate_proposal(
        original["id"], expected_revision=1, actor_id="Reviewer",
    )
    assert replacement["id"] != original["id"]
    assert replacement["supersedesProposalId"] == original["id"]
    assert runtime.repository.correction_proposal(original["id"])["lifecycleStatus"] == LifecycleStatus.SUPERSEDED.value
    proposals = runtime.repository.correction_proposals_for_finding("qa-1")
    assert {item["id"] for item in proposals} == {original["id"], replacement["id"]}
    assert runtime.correction_eligibility.calls[-1] == ("qa-1", (original["id"],))


def test_confirmed_omission_uses_zero_length_insertion_span(tmp_path: Path) -> None:
    runtime = _Runtime(tmp_path / "semantic.sqlite3")
    content_hash = runtime.text_hash(TEXT)
    offset = TEXT.index("என்")
    intent = CorrectionIntent(
        failed_dimension=CoverageDimension.LEXICAL_CONTENT,
        observed_meaning="not present", required_meaning="restore omitted meaning",
        affected_source_semantic_unit_ids=(),
        affected_target_span=AffectedTargetSpan(
            displayed_reference=REFERENCE, canonical_references=(REFERENCE,),
            start_code_point=offset, end_code_point=offset, original_text="",
            target_text_revision=runtime.text_revision(REFERENCE, content_hash),
            target_content_hash=content_hash,
        ),
    )
    proposal = CorrectionWordingService(runtime).create_proposal(
        finding_id="qa-1", intent=intent,
        human_proposed_text="தேவையான சொல் ", actor_id="Reviewer",
    )
    span = proposal["intent"]["affectedTargetSpan"]
    assert span["startCodePoint"] == span["endCodePoint"] == offset
    assert span["originalText"] == ""


def test_protocol_create_edit_reject_and_history_round_trip(tmp_path: Path) -> None:
    from bridge_service import BridgeEngine
    from greek_room_engine.protocol import EngineRequest

    runtime = _Runtime(tmp_path / "semantic.sqlite3")
    bridge = BridgeEngine()
    bridge.project = object()
    bridge.passage_semantic_runtime = runtime

    def call(method: str, params: dict) -> dict:
        return bridge.handle_request(
            EngineRequest(id="request", method=method, params=params)
        ).to_dict()

    created = call("correction.createProposal", {
        "findingId": "qa-1", "intent": to_wire(_intent(runtime)),
        "humanProposedText": "offline wording", "actorId": "Reviewer",
    })
    assert created["success"] is True, created
    proposal_id = created["result"]["id"]
    edited = call("correction.editProposal", {
        "proposalId": proposal_id, "proposedText": "edited wording",
        "expectedProposalRevision": 1, "actorId": "Reviewer",
    })
    assert edited["success"] is True, edited
    rejected = call("correction.rejectProposal", {
        "proposalId": proposal_id, "expectedProposalRevision": 2,
        "actorId": "Reviewer", "note": "reject",
    })
    assert rejected["success"] is True, rejected
    history = call("correction.getProposalHistory", {"proposalId": proposal_id})
    assert [event["eventType"] for event in history["result"]["events"]] == [
        "CREATED", "EDITED", "REJECTED",
    ]


@pytest.mark.parametrize("displayed", [
    "தேவனை ஸ்தோத்திரிக்கிறேன்",
    "בְּרֵאשִׁ֖ית בָּרָ֣א",
    "ἐν ἀρχῇ ἦν ὁ λόγος",
    "𐤀𐤁𐤂 𝕭𝖔𝖑𝖉",
])
def test_code_point_span_contract_is_unchanged_for_multilingual_text(displayed: str) -> None:
    start = 1
    end = len(displayed) - 1
    span = AffectedTargetSpan(
        displayed_reference="TST 1:1", canonical_references=("TST 1:1",),
        start_code_point=start, end_code_point=end,
        original_text=displayed[start:end], target_text_revision="r",
        target_content_hash="h",
    )
    assert len(span.original_text) == end - start


@pytest.mark.parametrize("dimension,observed,required", [
    (CoverageDimension.QUANTITY, "some", "all"),
    (CoverageDimension.QUANTITY, "all", "some"),
    (CoverageDimension.POLARITY, "positive", "negative"),
    (CoverageDimension.POLARITY, "negative", "positive"),
    (CoverageDimension.TEMPORAL_ASPECTUAL, "before", "after"),
    (CoverageDimension.QUANTITY, "three", "four"),
    (CoverageDimension.PARTICIPANT, "Paul greets Timothy", "Timothy greets Paul"),
    (CoverageDimension.LEXICAL_CONTENT, "unsupported modifier", "remove modifier"),
    (CoverageDimension.LEXICAL_CONTENT, "omitted", "restore meaning"),
])
def test_controlled_semantic_shapes_reach_wording_provider(
    tmp_path: Path, dimension: CoverageDimension, observed: str, required: str,
) -> None:
    class EchoProvider:
        available = True

        def suggest(self, context):
            wire_intent = context["intent"]
            assert wire_intent["failedDimension"] == dimension.value
            assert wire_intent["observedMeaning"] == observed
            assert wire_intent["requiredMeaning"] == required
            return CorrectionSuggestionResult(
                proposed_text=f"repair: {required}", explanation="fixture",
                provider_name="fixture", model="deterministic",
            )

    runtime = _Runtime(tmp_path / "semantic.sqlite3")
    base = _intent(runtime)
    intent = CorrectionIntent(
        failed_dimension=dimension, observed_meaning=observed,
        required_meaning=required,
        affected_source_semantic_unit_ids=(),
        affected_target_span=base.affected_target_span,
    )
    proposal = CorrectionWordingService(runtime, EchoProvider()).create_proposal(
        finding_id="qa-1", intent=intent, request_suggestion=True,
        actor_id="Reviewer",
    )
    assert proposal["proposedText"] == f"repair: {required}"


def test_all_stage9b1_operations_leave_scripture_and_alignment_byte_identical(tmp_path: Path) -> None:
    project = tmp_path / "project"
    guarded = {
        project / "php" / "1.json": json.dumps({"3": TEXT}, ensure_ascii=False),
        project / "imported.usfm": "\\id PHP\n\\c 1\n\\v 3 " + TEXT,
        project / "alignmentData" / "php" / "1.json": "{\"alignments\":[]}",
    }
    for path, content in guarded.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in guarded}

    runtime = _Runtime(project / ".bridge" / "semantic.sqlite3")
    human = CorrectionWordingService(runtime).create_proposal(
        finding_id="qa-1", intent=_intent(runtime),
        human_proposed_text="human", actor_id="Reviewer",
    )
    CorrectionWordingService(runtime).edit_proposal(
        human["id"], proposed_text="human edited", expected_revision=1,
        actor_id="Reviewer",
    )
    CorrectionWordingService(runtime).reject_proposal(
        human["id"], expected_revision=2, actor_id="Reviewer", reason="reject",
    )
    machine_service = CorrectionWordingService(runtime, _FixtureProvider())
    machine = machine_service.regenerate_proposal(
        human["id"], expected_revision=3, actor_id="Reviewer",
    )
    replacement = machine_service.regenerate_proposal(
        machine["id"], expected_revision=1, actor_id="Reviewer",
    )

    reopened = FoundationRepository(project / ".bridge" / "semantic.sqlite3")
    assert reopened.recovery_check()["ok"] is True
    assert reopened.correction_proposal(replacement["id"])["proposedText"] == "என் தேவனை"
    assert reopened.correction_proposal_history(machine["id"])[-1]["eventType"] == "SUPERSEDED"
    assert {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in guarded} == before


def test_correction_wording_module_contains_no_scripture_writer() -> None:
    import tc_ai_bridge.correction_wording as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    for forbidden in ("write_text(", "write_bytes(", "apply_scripture_edit(", "open("):
        assert forbidden not in source


def test_fresh_database_has_stage9b1_history_migration(tmp_path: Path) -> None:
    repo = FoundationRepository(tmp_path / "semantic.sqlite3")
    assert repo.schema_version() == DATABASE_SCHEMA_VERSION == 16
    assert repo.recovery_check()["ok"] is True


def test_stage9b1_wording_service_still_exposes_no_application_operation() -> None:
    from bridge_service import Methods

    public = set(dir(CorrectionWordingService))
    assert "apply_proposal" not in public
    assert "apply_scripture_edit" not in public
    assert hasattr(Methods, "CORRECTION_APPLY_PROPOSAL")


def test_review_context_exposes_authoritative_text_revision_and_exact_location_span(
    tmp_path: Path,
) -> None:
    runtime = _Runtime(tmp_path / "semantic.sqlite3")
    start = TEXT.index("என் தேவனை")
    end = start + len("என் தேவனை")
    location = {
        "id": "location-1", "runId": "location-run-1",
        "locationOutcome": "LOCATED", "targetSpanIds": ["target-span-1"],
        "targetDisplayedReferences": [REFERENCE],
        "targetCanonicalReferences": [REFERENCE],
    }

    class ReviewWithLocation(_Review):
        def get_finding(self, finding_id: str) -> dict:
            detail = super().get_finding(finding_id)
            detail["finding"].update({
                "sourceSemanticUnitIds": ["source-unit-1"],
                "displayedReferences": [REFERENCE],
            })
            detail["source"] = [{
                "id": "source-unit-1", "rawSurface": "τῷ θεῷ μου",
                "coverageDimension": "LEXICAL_CONTENT",
                "displayedReferences": [REFERENCE],
                "canonicalReferences": [REFERENCE],
            }]
            detail["location"] = [{"location": location, "alternatives": []}]
            detail["meaning"] = [{"components": [{
                "coverageDimension": "QUANTITY", "status": "CONTRADICTED",
                "explanation": "The target says one where the source requires all.",
            }]}]
            return detail

    runtime.qa_review = ReviewWithLocation()
    runtime.repository.semantic_location_run = lambda run_id: {  # type: ignore[method-assign]
        "id": run_id, "targetInventoryId": "target-inventory-1",
    }
    runtime.repository.target_inventory = lambda inventory_id: {  # type: ignore[method-assign]
        "id": inventory_id,
        "searchSpans": [{
            "id": "target-span-1", "displayedReference": REFERENCE,
            "startCodePoint": start, "endCodePoint": end,
            "quote": TEXT[start:end],
        }],
    }

    context = CorrectionWordingService(runtime).review_context("qa-1")
    current = context["currentTargets"][0]
    assert current["text"] == TEXT
    assert current["targetContentHash"] == runtime.text_hash(TEXT)
    assert current["targetTextRevision"] == runtime.text_revision(
        REFERENCE, runtime.text_hash(TEXT),
    )
    assert context["candidateSpans"] == [{
        "displayedReference": REFERENCE,
        "canonicalReferences": [REFERENCE],
        "startCodePoint": start,
        "endCodePoint": end,
        "originalText": "என் தேவனை",
        "targetTextRevision": current["targetTextRevision"],
        "targetContentHash": current["targetContentHash"],
    }]
    assert context["suggestedIntent"]["failedDimension"] == "QUANTITY"
    assert context["suggestedIntent"]["affectedSourceSemanticUnitIds"] == ["source-unit-1"]
    assert context["findingDisplayedReferences"] == [REFERENCE]
    assert context["sourceSemanticReferences"] == [REFERENCE]
    assert context["sourceCanonicalReferences"] == [REFERENCE]


def test_review_context_protocol_is_read_only_and_exposes_no_apply_method(tmp_path: Path) -> None:
    from bridge_service import BridgeEngine
    from greek_room_engine.protocol import EngineRequest

    runtime = _Runtime(tmp_path / "semantic.sqlite3")
    bridge = BridgeEngine()
    bridge.project = object()
    bridge.passage_semantic_runtime = runtime
    before = runtime.repository.correction_proposals_for_finding("qa-1")
    response = bridge.handle_request(EngineRequest(
        id="review-context", method="correction.getReviewContext",
        params={"findingId": "qa-1"},
    )).to_dict()
    assert response["success"] is True, response
    assert response["result"]["currentTargets"][0]["text"] == TEXT
    assert runtime.repository.correction_proposals_for_finding("qa-1") == before


def test_proposal_history_is_insertion_ordered_when_timestamps_tie(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ledger read order must be write order even inside one clock tick.

    On Python 3.12 for Windows `datetime.now()` advances about every 15 ms, and
    event ids are uuid4, so `ORDER BY created_at,id` returned events written in
    the same tick in random order. The fsync behind every commit used to hide
    this; without it (tests/conftest.py's SQLite opt-out) the history came back
    scrambled in roughly half the runs. Freezing the clock makes every event tie.
    """
    frozen = "2026-09-11T12:00:00+00:00"
    monkeypatch.setattr(FoundationRepository, "_now", staticmethod(lambda: frozen))
    runtime = _Runtime(tmp_path / "semantic.sqlite3")
    service = CorrectionWordingService(runtime)
    created = service.create_proposal(
        finding_id="qa-1", intent=_intent(runtime),
        human_proposed_text="wording 1", actor_id="Reviewer",
    )
    revision = 1
    for step in range(2, 10):
        service.edit_proposal(
            created["id"], proposed_text=f"wording {step}", explanation="edited",
            expected_revision=revision, actor_id="Reviewer",
        )
        revision += 1
    service.reject_proposal(created["id"], expected_revision=revision, actor_id="Reviewer")

    history = runtime.repository.correction_proposal_history(created["id"])
    assert {event["createdAt"] for event in history} == {frozen}
    assert [event["eventType"] for event in history] == (
        ["CREATED"] + ["EDITED"] * 8 + ["REJECTED"]
    )
    assert [event["newRevision"] for event in history] == sorted(
        event["newRevision"] for event in history
    )
