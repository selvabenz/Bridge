"""
Tests for `alignment.crossVerse.aiPropose` (#146): the model-backed cross-verse
proposer and the agreement gate that decides what may be linked without a click.

Everything here runs through `BridgeEngine(ai_transport=...)` -- a fake HTTP
transport, never a network call and never a key. That seam is the project's
established way of testing an AI path (`tests/ai/test_ai_review_protocol.py`),
and it is what lets these assert the thing that actually matters: what Bridge
does with a reply, including a dishonest one.

The three properties under test, in order of how much they would cost to get
wrong: an id the model invented is refused rather than silently dropped; a link
is auto-applied only where the model and the corpus scorer independently agree;
and proposing still writes nothing.
"""
import json
from pathlib import Path

import pytest

from bridge_service import BridgeEngine
from greek_room_engine.protocol import EngineRequest

from tests.support.cross_verse import (  # the #139 fixture shape
    KADAVUL, VARTHAI, _bottom, _group, _top, _write_book,
)

THEOS = _top("θεός", "G23160")
THEOU = _top("θεοῦ", "G23160")
LOGOS = _top("λόγος", "G30560")
AGAPE = _top("ἀγάπη", "G00260")

ANBU = "அன்பு"
KIRUBAI = "கிருபை"


def call(engine, method, params=None):
    return engine.handle_request(EngineRequest(id="t", method=method, params=params or {})).to_dict()


def _response(body: dict) -> bytes:
    """One OpenAI Responses envelope carrying `body` as the structured output."""
    return json.dumps({
        "output_text": json.dumps(body, ensure_ascii=False),
        "usage": {"input_tokens": 120, "output_tokens": 30, "total_tokens": 150},
    }).encode("utf-8")


def transport_returning(body: dict, captured: list | None = None):
    """A fake transport that answers every request with `body`.

    When `captured` is given it collects the outgoing payloads, so a test can
    assert what Bridge actually sent -- which is the only way to check that the
    model was handed opaque handles and never Bridge's own token ids.
    """
    def transport(url, headers, request_body, timeout):
        if captured is not None:
            captured.append(json.loads(request_body))
        return 200, _response(body)
    return transport


@pytest.fixture
def fixture_root(tmp_path) -> Path:
    """Verses 1 and 2 are completed and teach the corpus that G2316 is rendered
    "கடவுள்". Verse 3 has an unaligned θεοῦ; verse 4 an unaccounted "கடவுள்".

    Same shape as the #139 protocol fixture on purpose: the corpus scorer
    proposes 3→4 here on its own, so a model that agrees can be distinguished
    from one that does not.

    Verse 5 then adds what the #139 fixture had no need of: a SECOND source gap
    (ἀγάπη) and two target gaps the corpus has never seen, one of them in ἀγάπη's
    own verse. That is what makes the same-verse, uncorroborated and
    two-links-onto-one-word cases testable instead of skipped.
    """
    root = tmp_path / "php"
    _write_book(root, {
        "1": {
            "1": {
                "text": KADAVUL,
                "alignment": {"alignments": [_group([THEOS], [_bottom(KADAVUL)])], "wordBank": []},
                "complete": True,
            },
            "2": {
                "text": KADAVUL,
                "alignment": {"alignments": [_group([THEOS], [_bottom(KADAVUL)])], "wordBank": []},
                "complete": True,
            },
            "3": {
                "text": VARTHAI,
                "alignment": {
                    "alignments": [_group([THEOU], []), _group([LOGOS], [_bottom(VARTHAI)])],
                    "wordBank": [],
                },
            },
            "4": {
                "text": KADAVUL,
                "alignment": {"alignments": [], "wordBank": [_bottom(KADAVUL)]},
            },
            "5": {
                "text": f"{ANBU} {KIRUBAI}",
                "alignment": {
                    "alignments": [_group([AGAPE], [])],
                    "wordBank": [_bottom(ANBU), _bottom(KIRUBAI)],
                },
            },
        },
    })
    return root


def open_engine(root: Path, transport, api_key: str = "test-key") -> BridgeEngine:
    engine = BridgeEngine(ai_transport=transport)
    engine.settings.set_api_key(api_key, persist=False)
    opened = call(engine, "project.open", {"path": str(root)})
    assert opened["success"] is True, opened
    return engine


def ai_propose(engine, verses):
    response = call(engine, "alignment.crossVerse.aiPropose", {"chapter": "1", "verses": verses})
    assert response["success"] is True, response
    return response["result"]


# ---- the closed menu ---------------------------------------------------------

def test_the_model_is_handed_opaque_handles_not_bridges_own_token_ids(fixture_root):
    captured: list = []
    engine = open_engine(fixture_root, transport_returning({"links": []}, captured))
    ai_propose(engine, ["3", "4"])

    assert len(captured) == 1, captured
    payload = json.loads(captured[0]["input"])
    source_ids = [row["id"] for row in payload["sourceGaps"]]
    target_ids = [row["id"] for row in payload["targetGaps"]]
    assert source_ids and target_ids
    assert all(value.startswith("S") for value in source_ids), source_ids
    assert all(value.startswith("T") for value in target_ids), target_ids
    # The positional ids the link store would use must never reach the provider:
    # they are meaningless outside this load and leaking them invites a reply
    # that looks authoritative while pointing at the wrong token.
    assert "H001" not in captured[0]["input"]
    assert "T001" not in captured[0]["input"]
    # The gloss is what lets the model (and the reviewer reading its reason)
    # work in a language they can actually read.
    assert any(row.get("meaning") for row in payload["sourceGaps"]), payload["sourceGaps"]


def test_an_invented_id_is_refused_rather_than_quietly_dropped(fixture_root):
    engine = open_engine(fixture_root, transport_returning(
        {"links": [{"source_id": "S99", "target_id": "T1", "confidence": 90, "reason": "invented"}]},
    ))
    response = call(engine, "alignment.crossVerse.aiPropose", {"chapter": "1", "verses": ["3", "4"]})

    assert response["success"] is False, response
    assert "S99" in response["error"]["message"], response["error"]


def test_a_same_verse_pair_is_dropped_because_the_link_store_would_refuse_it(fixture_root):
    """Legal JSON, meaningless as a cross-verse link -- and it would only fail on
    accept, so it is dropped here rather than offered."""
    captured: list = []
    engine = open_engine(fixture_root, transport_returning({"links": []}, captured))
    ai_propose(engine, ["3", "4", "5"])
    payload = json.loads(captured[0]["input"])
    source = next(row for row in payload["sourceGaps"] if row["verse"] == "5")
    same_verse_target = next(row for row in payload["targetGaps"] if row["verse"] == "5")

    engine2 = open_engine(fixture_root, transport_returning({"links": [{
        "source_id": source["id"], "target_id": same_verse_target["id"],
        "confidence": 95, "reason": "same verse",
    }]}))
    assert ai_propose(engine2, ["3", "4", "5"])["proposals"] == []


# ---- the agreement gate ------------------------------------------------------

def _model_picks_theou_to_kadavul(engine) -> dict:
    """The pairing the corpus scorer also finds: 1:3 θεοῦ realized by 1:4 கடவுள்."""
    return {"links": [{
        "source_id": "S1", "target_id": "T1", "confidence": 88,
        "reason": 'the Tamil "கடவுள்" in v.4 carries the sense of God from v.3',
    }]}


def test_agreement_with_the_corpus_is_what_makes_a_proposal_auto_linkable(fixture_root):
    engine = open_engine(fixture_root, transport_returning(_model_picks_theou_to_kadavul(None)))
    result = ai_propose(engine, ["3", "4"])

    assert len(result["proposals"]) == 1, result["proposals"]
    proposal = result["proposals"][0]
    assert proposal["agreesWithCorpus"] is True
    assert proposal["autoLinkable"] is True
    assert proposal["status"] == "PROPOSED"
    # Both reasons are carried, not just the model's: the reviewer is being told
    # two independent methods agreed, and must be able to see both.
    kinds = {item["kind"] for item in proposal["evidence"]}
    assert "MODEL_PICK" in kinds
    assert kinds & {"STRONGS_PRECEDENT", "SURFACE_PRECEDENT"}, kinds


def test_a_confident_model_the_corpus_does_not_corroborate_is_never_auto_linkable(fixture_root):
    """The whole point of the gate. A model at 99 on its own is still a suggestion.

    ἀγάπη (1:5) has never been seen in a completed verse, so the corpus scorer
    proposes nothing for it however sure the model is.
    """
    captured: list = []
    engine = open_engine(fixture_root, transport_returning({"links": []}, captured))
    ai_propose(engine, ["3", "4", "5"])
    payload = json.loads(captured[0]["input"])
    source = next(row for row in payload["sourceGaps"] if row["verse"] == "5")
    target = next(row for row in payload["targetGaps"] if row["verse"] == "4")

    engine2 = open_engine(fixture_root, transport_returning({"links": [{
        "source_id": source["id"], "target_id": target["id"],
        "confidence": 99, "reason": "very confident and uncorroborated",
    }]}))
    proposal = next(
        p for p in ai_propose(engine2, ["3", "4", "5"])["proposals"]
        if p["source"]["verse"] == "5"
    )
    assert proposal["agreesWithCorpus"] is False
    assert proposal["autoLinkable"] is False
    assert proposal["status"] == "AMBIGUOUS"
    assert proposal["confidence"] == pytest.approx(0.99)


def test_two_links_onto_one_target_word_keep_only_the_first(fixture_root):
    """One target word realizes one source word. A model that claims otherwise
    has contradicted itself, so the second claim is dropped rather than surfaced
    as a rival nobody could accept."""
    captured: list = []
    engine = open_engine(fixture_root, transport_returning({"links": []}, captured))
    ai_propose(engine, ["3", "4", "5"])
    payload = json.loads(captured[0]["input"])
    first = next(row for row in payload["sourceGaps"] if row["verse"] == "3")
    second = next(row for row in payload["sourceGaps"] if row["verse"] == "5")
    shared = next(row for row in payload["targetGaps"] if row["verse"] == "4")

    engine2 = open_engine(fixture_root, transport_returning({"links": [
        {"source_id": first["id"], "target_id": shared["id"], "confidence": 90, "reason": "a"},
        {"source_id": second["id"], "target_id": shared["id"], "confidence": 90, "reason": "b"},
    ]}))
    proposals = ai_propose(engine2, ["3", "4", "5"])["proposals"]
    assert len(proposals) == 1
    assert proposals[0]["source"]["verse"] == "3"


# ---- the invariants the offline path already holds ---------------------------

def test_ai_proposing_writes_nothing(fixture_root):
    """The same guarantee `test_proposing_writes_nothing` makes for #139.

    A proposal is not a link -- and now that some proposals *become* links, the
    boundary matters more, not less: the write happens on the caller's explicit
    `alignment.crossVerse.link`, never inside the proposer.
    """
    engine = open_engine(fixture_root, transport_returning(_model_picks_theou_to_kadavul(None)))
    alignment_file = (
        fixture_root / ".apps" / "translationCore" / "alignmentData" / "php" / "1.json"
    )
    before = alignment_file.read_bytes()

    result = ai_propose(engine, ["3", "4"])
    assert result["proposals"], "the fixture should produce a proposal to make this meaningful"

    assert alignment_file.read_bytes() == before
    context = call(engine, "alignment.get", {"chapter": "1", "verse": "3"})["result"]
    assert context["crossVerseLinks"] == []


def test_no_api_key_is_a_supported_state_not_an_error(fixture_root):
    engine = BridgeEngine(ai_transport=transport_returning({"links": []}))
    engine.settings.set_api_key("", persist=False)
    opened = call(engine, "project.open", {"path": str(fixture_root)})
    assert opened["success"] is True, opened

    response = call(engine, "alignment.crossVerse.aiPropose", {"chapter": "1", "verses": ["3", "4"]})
    # Structured unavailability, the shape `start_triage` established: a project
    # with no key is a state the UI renders, not an error to dismiss.
    assert response["success"] is True, response
    assert response["result"]["unavailable"]["reason"] == "no-api-key"
    assert response["result"]["proposals"] == []


def test_the_corpus_proposals_survive_a_model_that_returned_nothing(fixture_root):
    engine = open_engine(fixture_root, transport_returning({"links": []}))
    result = ai_propose(engine, ["3", "4"])

    assert result["proposals"] == []
    # Otherwise asking the AI would be strictly worse than not asking.
    assert result["corpusProposals"], result


def test_an_empty_range_is_refused(fixture_root):
    engine = open_engine(fixture_root, transport_returning({"links": []}))
    response = call(engine, "alignment.crossVerse.aiPropose", {"chapter": "1", "verses": []})
    assert response["success"] is False, response


def test_nothing_to_ask_about_spends_no_request(fixture_root):
    """Verses 1 and 2 are fully aligned, so there is no menu -- and a billed
    round trip to be told so would be pure waste."""
    captured: list = []
    engine = open_engine(fixture_root, transport_returning({"links": []}, captured))
    result = ai_propose(engine, ["1", "2"])

    assert captured == []
    assert result["modelConsulted"] is False
    assert result["proposals"] == []


def test_an_auto_applied_link_records_its_origin_on_the_append_only_event(fixture_root):
    """Accepting is byte-identical to a manual drag, so the audit trail is the
    only place that can say a model chose this one."""
    engine = open_engine(fixture_root, transport_returning(_model_picks_theou_to_kadavul(None)))
    proposal = ai_propose(engine, ["3", "4"])["proposals"][0]
    assert proposal["autoLinkable"] is True

    linked = call(engine, "alignment.crossVerse.link", {
        "source": {"chapter": "1", "verse": proposal["source"]["verse"], "topId": proposal["source"]["topId"]},
        "target": {"chapter": "1", "verse": proposal["target"]["verse"], "bottomId": proposal["target"]["bottomId"]},
        "origin": "ai-auto",
    })
    assert linked["success"] is True, linked

    entries = engine.project.workbench.change_log_entries(
        engine.project.workbench_identity.project_id,
    )
    events = [
        json.loads(row["payload_json"]) for row in entries
        if row["op"] == "crossVerseLink"
    ]
    assert events, entries
    assert any(event.get("origin") == "ai-auto" for event in events), events
