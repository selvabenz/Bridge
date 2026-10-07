"""
Reproduction for a real session report: right after an automatic AI review
finished, the verse immediately showed "Verse changed -- the previous AI
review is stale."

ai_review_cache_status() compares the fingerprint stored with the review
against review_input_fingerprint() recomputed now, and that fingerprint
covers the verse alignment as well as check state. The review records
itself (ai_client.prepare_verse_review -> record_ai_review_result) BEFORE
_run_ai_review_for_verse writes the AI-filled alignment gaps, so a review
that fills any alignment gap invalidates its own stored fingerprint.

Only _apply_basic_ai_selections rebased, and only when it actually applied
something -- so Manual (advanced) mode, and Auto mode with nothing safe to
apply, both reported the review stale the instant it finished.
"""
import pytest

from bridge_service import BridgeEngine
from greek_room_engine.protocol import EngineRequest
from tc_ai_bridge.models import VerseAlignment
from tc_ai_bridge.tc_project import TranslationCoreProject

from tests.support.ai_review import (  # noqa: F401  (fixture import)
    _grounded_fake_transport,
    _wait_for_ai_job,
    imported_titus_project,
)


def call(engine, method, params=None):
    return engine.handle_request(
        EngineRequest(id="t", method=method, params=params or {}),
    ).to_dict()


def _fill_one_alignment_gap(engine):
    """Make prepare_verse_review return a genuinely changed alignment.

    The bundled fixture has no original-language source tokens, so the real
    gap-fill call can never produce a link here. Wrapping the real client
    keeps every other part of the flow real while reproducing the one
    condition that matters: the review hands back an alignment that differs
    from the one it was given, so _run_ai_review_for_verse saves it.
    """
    client = engine._ai_client()
    real_prepare = client.prepare_verse_review

    def prepare(project, chapter, verse, alignment, **kwargs):
        proposal, review_alignment, reviews, issues, summary, meta = real_prepare(
            project, chapter, verse, alignment, **kwargs,
        )
        moved = VerseAlignment.from_dict(review_alignment.to_dict())
        if moved.word_bank and moved.alignments:
            # Exactly what a real gap fill does: take one still-unaligned
            # target token out of the word bank and attach it to a source
            # group. Every target token stays accounted for, so this is a
            # change _save_alignment will actually persist.
            token = moved.word_bank.pop()
            moved.alignments[0].bottom_words.append(token)
        return object(), moved, reviews, issues, summary, meta

    client.prepare_verse_review = prepare
    engine._ai_client = lambda: client
    return client


@pytest.mark.parametrize("mode", ("basic", "advanced"))
def test_review_is_current_immediately_after_it_writes_its_own_alignment(
    imported_titus_project, monkeypatch, mode,
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings, project_path = imported_titus_project
    settings.set_api_key("sk-test-123")
    engine = BridgeEngine(settings=settings, ai_transport=_grounded_fake_transport())
    call(engine, "project.open", {"path": project_path})
    _fill_one_alignment_gap(engine)

    before = TranslationCoreProject(project_path).load_verse_alignment("1", "1").to_dict()

    started = call(engine, "ai.review.start", {
        "scope": "verse", "chapter": "1", "verse": "1", "mode": mode,
    })
    assert started["success"] is True, started
    snapshot = _wait_for_ai_job(engine, started["result"]["jobId"])
    assert snapshot["state"] == "succeeded", snapshot

    fresh = TranslationCoreProject(project_path)
    after = fresh.load_verse_alignment("1", "1").to_dict()
    assert after != before, "the review was supposed to write an alignment change"

    listed = call(engine, "check.listForVerse", {"chapter": "1", "verse": "1"})["result"]
    assert listed["aiReviewState"] == "current", (
        f"[{mode}] the review reported itself stale the moment it finished"
    )


@pytest.mark.parametrize("mode", ("basic", "advanced"))
def test_safe_selections_are_applied_whatever_the_override_setting(
    imported_titus_project, monkeypatch, mode,
):
    """Manual override adds an editor; it does not switch the AI off.

    "advanced" used to skip _apply_safe_ai_selections entirely, so turning
    manual override on silently stopped tN/tW words being selected at all --
    reported from a real session as "the tN words are not getting selected
    automatically like earlier".
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings, project_path = imported_titus_project
    settings.set_api_key("sk-test-123")
    engine = BridgeEngine(settings=settings, ai_transport=_grounded_fake_transport())
    call(engine, "project.open", {"path": project_path})

    started = call(engine, "ai.review.start", {
        "scope": "verse", "chapter": "1", "verse": "1", "mode": mode,
    })
    snapshot = _wait_for_ai_job(engine, started["result"]["jobId"])
    assert snapshot["state"] == "succeeded", snapshot

    applied = snapshot["latestResult"]["result"]["appliedSelections"]
    assert applied, f"[{mode}] no safe selection was applied"

    after = call(engine, "check.listForVerse", {"chapter": "1", "verse": "1"})["result"]
    selected = [c for c in after["checks"] if c["selectionStatus"] == "selected"]
    assert selected, f"[{mode}] no check ended up selected"
