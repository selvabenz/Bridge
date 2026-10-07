"""
Tests for `alignment.crossVerse.propose` (#139): the protocol surface over
`cross_verse_proposals.propose`, against a real fixture project.

`test_cross_verse_proposals.py` covers the scoring as a pure function. What is
under test here is the wiring: that the gaps handed to the scorer are the real
ones, that the corpus table is this project's own, that accepting a proposal
goes through the ordinary link path, and above all that proposing writes
nothing.
"""

import pytest

from bridge_service import BridgeEngine
from greek_room_engine.protocol import EngineRequest
from tests.support.cross_verse import KADAVUL, VARTHAI, _bottom, _group, _top, _write_book


def call(engine, method, params=None):
    return engine.handle_request(EngineRequest(id="t", method=method, params=params or {})).to_dict()


THEOS = _top("θεός", "G23160")
THEOU = _top("θεοῦ", "G23160")
LOGOS = _top("λόγος", "G30560")


@pytest.fixture
def fixture_project(tmp_path):
    """Verses 1 and 2 are completed and teach the corpus that G2316 is rendered
    "கடவுள்". Verse 3 then has an unaligned θεοῦ and no target word for it, and
    verse 4 holds an unaccounted "கடவுள்" -- the paraphrase-across-the-boundary
    shape this feature exists for."""
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
        },
    })
    return root


@pytest.fixture
def engine(fixture_project):
    engine = BridgeEngine()
    opened = call(engine, "project.open", {"path": str(fixture_project)})
    assert opened["success"] is True, opened
    return engine


def _propose(engine, verses):
    response = call(engine, "alignment.crossVerse.propose", {"chapter": "1", "verses": verses})
    assert response["success"] is True, response
    return response["result"]


def test_the_projects_own_completed_alignments_propose_the_realization(engine):
    result = _propose(engine, ["3", "4"])

    assert result.get("unavailable") is None, result.get("unavailable")
    assert result["corpus"]["versesScanned"] == 2
    proposals = result["proposals"]
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal["source"]["verse"] == "3"
    assert proposal["source"]["word"] == "θεοῦ"
    assert proposal["target"]["verse"] == "4"
    assert proposal["target"]["word"] == KADAVUL
    assert proposal["status"] == "PROPOSED"
    # The inflected genitive never appeared in a completed verse; the Strong's
    # index is what connects it to the rendering (#138).
    evidence = {item["kind"]: item for item in proposal["evidence"]}
    assert evidence["STRONGS_PRECEDENT"]["rawScore"] > 0
    assert evidence["SURFACE_PRECEDENT"]["rawScore"] == 0


def test_proposing_writes_nothing(engine, fixture_project):
    """The whole design rests on this: a proposal is not a link."""
    chapter_path = (
        fixture_project / ".apps" / "translationCore" / "alignmentData" / "php" / "1.json"
    )
    before = chapter_path.read_bytes()

    result = _propose(engine, ["3", "4"])
    assert result["proposals"], "expected a proposal to exist for this test to mean anything"

    assert chapter_path.read_bytes() == before
    context = call(engine, "alignment.get", {"chapter": "1", "verse": "3"})["result"]
    assert context["crossVerseLinks"] == []
    assert context["gaps"]["sourceUnmatched"] == 1


def test_accepting_a_proposal_goes_through_the_ordinary_link_call(engine):
    """There is no separate apply path: the reviewer's Accept is an
    `alignment.crossVerse.link` with the ids the proposal carries."""
    proposal = _propose(engine, ["3", "4"])["proposals"][0]

    linked = call(engine, "alignment.crossVerse.link", {
        "source": {
            "chapter": proposal["source"]["chapter"], "verse": proposal["source"]["verse"],
            "topId": proposal["source"]["topId"],
        },
        "target": {
            "chapter": proposal["target"]["chapter"], "verse": proposal["target"]["verse"],
            "bottomId": proposal["target"]["bottomId"],
        },
    })
    assert linked["success"] is True, linked

    # The gap it was proposed for is now closed, and the proposal is gone.
    assert _propose(engine, ["3", "4"])["proposals"] == []


def test_an_unknown_verse_is_refused(engine):
    response = call(engine, "alignment.crossVerse.propose", {"chapter": "1", "verses": ["99"]})
    assert response["success"] is False
    assert "99" in response["error"]["message"]


def test_an_empty_verse_list_is_refused(engine):
    response = call(engine, "alignment.crossVerse.propose", {"chapter": "1", "verses": []})
    assert response["success"] is False


def test_a_book_with_no_completed_alignments_says_so(tmp_path):
    root = tmp_path / "php"
    _write_book(root, {
        "1": {
            "3": {
                "text": VARTHAI,
                "alignment": {"alignments": [_group([THEOU], [])], "wordBank": []},
            },
            "4": {
                "text": KADAVUL,
                "alignment": {"alignments": [], "wordBank": [_bottom(KADAVUL)]},
            },
        },
    })
    engine = BridgeEngine()
    assert call(engine, "project.open", {"path": str(root)})["success"] is True

    result = _propose(engine, ["3", "4"])

    assert result["proposals"] == []
    assert result["unavailable"]["reason"] == "no-completed-alignments"
