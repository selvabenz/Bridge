"""V11-003 (#57): a manually written correction proposal must be usable
without an Edit->Save round-trip.

W1 (the default for new proposals) is covered in test_correction_stage9b1.py
alongside the rest of proposal creation. This file covers W2: the lazy,
idempotent reseed of existing UNREVIEWED + HUMAN_AUTHORED proposals that
predate the W1 fix. See docs/archive/V11-003_ISSUE57_PROMPT.md.
"""
from __future__ import annotations

import json
from pathlib import Path

from tc_ai_bridge.correction_wording import CorrectionWordingService
from tc_ai_bridge.passage_semantic_models import LifecycleStatus, ReviewStatus

from tests.support.correction_9b1 import _FixtureProvider, _Runtime, _intent


def _seed_stuck_proposal(runtime: _Runtime, *, human_text: str = "என் தேவனுக்கு") -> str:
    """A proposal that predates the V11-003 fix: created normally (so it gets
    real dependency edges through the actual service), then rewound to
    UNREVIEWED by raw SQL to simulate on-disk state from before W1 landed --
    exactly the shape the lazy reseed (W2) exists to repair."""
    proposal = runtime.correction_create_proposal(
        finding_id="qa-1", intent=_intent(runtime),
        human_proposed_text=human_text, explanation="Human wording.",
        actor_id="Reviewer",
    )
    _force_payload_field(runtime, proposal["id"], "reviewStatus", ReviewStatus.UNREVIEWED.value)
    return proposal["id"]


def _force_payload_field(runtime: _Runtime, proposal_id: str, key: str, value) -> None:
    """Test-only fixture surgery: bypasses the service/event log entirely,
    the way a real pre-fix database predates any of this code existing."""
    with runtime.repository._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        payload = json.loads(conn.execute(
            "SELECT payload_json FROM correction_proposals WHERE id=?", (proposal_id,),
        ).fetchone()[0])
        payload[key] = value
        column = {"reviewStatus": "review_status", "lifecycleStatus": "lifecycle_status"}.get(key)
        if column:
            conn.execute(
                f"UPDATE correction_proposals SET {column}=?,payload_json=? WHERE id=?",
                (value, json.dumps(payload, ensure_ascii=False), proposal_id),
            )
        else:
            conn.execute(
                "UPDATE correction_proposals SET payload_json=? WHERE id=?",
                (json.dumps(payload, ensure_ascii=False), proposal_id),
            )
        conn.commit()


def _raw_revision(runtime: _Runtime, proposal_id: str) -> int:
    with runtime.repository._connect() as conn:
        return int(conn.execute(
            "SELECT revision FROM correction_proposals WHERE id=?", (proposal_id,),
        ).fetchone()[0])


def test_reading_a_stuck_proposal_reseeds_it_to_approved_with_one_backfill_event(
    tmp_path: Path,
) -> None:
    runtime = _Runtime(tmp_path / "semantic.sqlite3")
    proposal_id = _seed_stuck_proposal(runtime)

    read = runtime.repository.correction_proposal(proposal_id)

    assert read["reviewStatus"] == ReviewStatus.HUMAN_APPROVED.value
    assert read["revision"] == 1
    history = runtime.repository.correction_proposal_history(proposal_id)
    backfill = [event for event in history if event["eventType"] == "REVIEW_STATUS_BACKFILLED"]
    assert len(backfill) == 1
    assert backfill[0]["actorType"] == "MIGRATION"
    assert backfill[0]["baseRevision"] == 1
    assert backfill[0]["newRevision"] == 1
    assert _raw_revision(runtime, proposal_id) == 1


def test_reseed_is_idempotent_on_a_second_read(tmp_path: Path) -> None:
    runtime = _Runtime(tmp_path / "semantic.sqlite3")
    proposal_id = _seed_stuck_proposal(runtime)

    runtime.repository.correction_proposal(proposal_id)
    second = runtime.repository.correction_proposal(proposal_id)

    assert second["reviewStatus"] == ReviewStatus.HUMAN_APPROVED.value
    assert second["revision"] == 1
    history = runtime.repository.correction_proposal_history(proposal_id)
    event_types = [event["eventType"] for event in history]
    assert event_types.count("REVIEW_STATUS_BACKFILLED") == 1


def test_reseed_leaves_ai_proposed_alone(tmp_path: Path) -> None:
    runtime = _Runtime(tmp_path / "semantic.sqlite3")
    provider = _FixtureProvider()
    proposal = CorrectionWordingService(runtime, provider).create_proposal(
        finding_id="qa-1", intent=_intent(runtime), request_suggestion=True,
        actor_id="Reviewer",
    )

    read = runtime.repository.correction_proposal(proposal["id"])

    assert read["reviewStatus"] == ReviewStatus.AI_PROPOSED.value
    # AI-suggested proposals already carry both CREATED and SUGGESTED from
    # creation (save_correction_proposal_v2) -- the reseed adds nothing.
    history = runtime.repository.correction_proposal_history(proposal["id"])
    assert [event["eventType"] for event in history] == ["CREATED", "SUGGESTED"]


def test_reseed_leaves_human_rejected_alone(tmp_path: Path) -> None:
    runtime = _Runtime(tmp_path / "semantic.sqlite3")
    proposal = runtime.correction_create_proposal(
        finding_id="qa-1", intent=_intent(runtime),
        human_proposed_text="என் தேவனுக்கு", explanation="Human wording.",
        actor_id="Reviewer",
    )
    runtime.correction_reject_proposal(
        proposal["id"], expected_revision=1, actor_id="Reviewer", reason="not needed",
    )

    read = runtime.repository.correction_proposal(proposal["id"])

    assert read["reviewStatus"] == ReviewStatus.HUMAN_REJECTED.value
    history = runtime.repository.correction_proposal_history(proposal["id"])
    assert [event["eventType"] for event in history] == ["CREATED", "REJECTED"]


def test_reseed_leaves_a_non_active_unreviewed_proposal_alone(tmp_path: Path) -> None:
    """Lifecycle non-ACTIVE isolated from reviewStatus/creationMode: forced
    back to UNREVIEWED + INACTIVE without going through reject (which would
    also change reviewStatus), so this proves the lifecycle condition on its
    own rather than piggy-backing on the HUMAN_REJECTED case above."""
    runtime = _Runtime(tmp_path / "semantic.sqlite3")
    proposal_id = _seed_stuck_proposal(runtime)
    _force_payload_field(runtime, proposal_id, "lifecycleStatus", LifecycleStatus.INACTIVE.value)

    read = runtime.repository.correction_proposal(proposal_id)

    assert read["reviewStatus"] == ReviewStatus.UNREVIEWED.value
    assert read["lifecycleStatus"] == LifecycleStatus.INACTIVE.value
    history = runtime.repository.correction_proposal_history(proposal_id)
    assert [event["eventType"] for event in history] == ["CREATED"]


def test_reseed_leaves_an_unreviewed_proposal_with_provider_metadata_alone(
    tmp_path: Path,
) -> None:
    """Belt-and-suspenders: creationMode HUMAN_AUTHORED and non-empty
    providerMetadata never co-occur through the real service, but the
    reseed must not trust that and must check AI provenance directly."""
    runtime = _Runtime(tmp_path / "semantic.sqlite3")
    proposal_id = _seed_stuck_proposal(runtime)
    _force_payload_field(
        runtime, proposal_id, "providerMetadata",
        {"providerName": "legacy", "model": "legacy-model"},
    )

    read = runtime.repository.correction_proposal(proposal_id)

    assert read["reviewStatus"] == ReviewStatus.UNREVIEWED.value
    history = runtime.repository.correction_proposal_history(proposal_id)
    assert [event["eventType"] for event in history] == ["CREATED"]


def test_both_read_paths_reseed_identically(tmp_path: Path) -> None:
    runtime = _Runtime(tmp_path / "semantic.sqlite3")
    proposal_id = _seed_stuck_proposal(runtime)

    via_finding = runtime.repository.correction_proposals_for_finding("qa-1")
    assert len(via_finding) == 1
    assert via_finding[0]["reviewStatus"] == ReviewStatus.HUMAN_APPROVED.value

    via_id = runtime.repository.correction_proposal(proposal_id)
    assert via_id["reviewStatus"] == ReviewStatus.HUMAN_APPROVED.value

    history = runtime.repository.correction_proposal_history(proposal_id)
    event_types = [event["eventType"] for event in history]
    assert event_types.count("REVIEW_STATUS_BACKFILLED") == 1


def test_a_reseed_triggering_read_does_not_invalidate_the_revision_the_caller_already_cached(
    tmp_path: Path,
) -> None:
    """The concurrency regression the revision rule exists for (Part A's
    "concurrency hazard" section): a mere read is what silently triggers
    this reseed, so it must never change what revision is persisted. Model
    a caller (the review panel) that already has a proposal's revision
    cached from before this read -- captured here directly from the
    database column, not from the reseed's own return value, so this test
    still fails even if a future "fix" bumps the stored revision but takes
    care to also reflect that in the value it hands back. Read the
    proposal (triggering the reseed), then edit using the old cached
    revision: it must succeed.
    """
    runtime = _Runtime(tmp_path / "semantic.sqlite3")
    proposal_id = _seed_stuck_proposal(runtime)
    cached_revision = _raw_revision(runtime, proposal_id)

    runtime.repository.correction_proposal(proposal_id)  # triggers the reseed

    edited = runtime.correction_edit_proposal(
        proposal_id, proposed_text="திருத்திய உரை",
        expected_revision=cached_revision, actor_id="Reviewer",
    )

    assert edited["proposedText"] == "திருத்திய உரை"
    assert edited["revision"] == cached_revision + 1


def test_reseed_never_writes_while_the_database_is_read_only(tmp_path: Path) -> None:
    """W2 requirement: never write on a read when the database is
    read-only/in recovery -- a reseed that raised here would break project
    open (recovery_check() is what sets this flag in production)."""
    runtime = _Runtime(tmp_path / "semantic.sqlite3")
    proposal_id = _seed_stuck_proposal(runtime)

    runtime.repository.read_only = True
    read = runtime.repository.correction_proposal(proposal_id)

    assert read["reviewStatus"] == ReviewStatus.UNREVIEWED.value
    history = runtime.repository.correction_proposal_history(proposal_id)
    assert [event["eventType"] for event in history] == ["CREATED"]

    runtime.repository.read_only = False
    reread = runtime.repository.correction_proposal(proposal_id)
    assert reread["reviewStatus"] == ReviewStatus.HUMAN_APPROVED.value
