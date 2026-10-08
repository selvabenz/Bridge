"""
Protocol-level tests for the names/transliteration spelling-consistency
check (Phase 5) — proving NamesAdapter's whole-book comparison surfaces
real findings through BridgeEngine's actual verse.runChecks dispatch, with
stable ids that survive a fresh process, the same way USFM and
versification findings were proven end to end.
"""
import json

import pytest

from bridge_service import BridgeEngine
from greek_room_engine.protocol import EngineRequest


def call(engine, method, params=None):
    return engine.handle_request(EngineRequest(id="t", method=method, params=params or {})).to_dict()


@pytest.fixture
def spelling_fixture_project(tmp_path):
    """A minimal English TIT project with a deliberate one-off typo
    ("Tituss" for "Titus") planted in verse 2:7, so the check has a real,
    unambiguous inconsistency to find — not a synthetic finding shape."""
    root = tmp_path / "tit"
    align_dir = root / ".apps" / "translationCore" / "alignmentData" / "tit"
    align_dir.mkdir(parents=True)
    (root / "tit").mkdir(parents=True)

    (root / "manifest.json").write_text(json.dumps({
        "project": {"id": "tit", "name": "Titus"},
        "target_language": {"id": "eng", "name": "English"},
        "tc_version": "8", "tc_edit_version": "3.7.0",
    }), encoding="utf-8")

    (align_dir / "1.json").write_text(json.dumps({
        "1": {"alignments": [], "wordBank": []},
        "4": {"alignments": [], "wordBank": []},
    }), encoding="utf-8")
    (align_dir / "2.json").write_text(json.dumps({
        "7": {"alignments": [], "wordBank": []},
    }), encoding="utf-8")

    (root / "tit" / "1.json").write_text(json.dumps({
        "1": "Paul, a servant of God, to Titus my true son.",
        "4": "Titus is a beloved child in the common faith.",
    }), encoding="utf-8")
    (root / "tit" / "2.json").write_text(json.dumps({
        "7": "In everything show yourself an example, Tituss my son.",
    }), encoding="utf-8")

    return root


def test_names_check_surfaces_a_real_spelling_inconsistency(spelling_fixture_project):
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(spelling_fixture_project)})

    result = call(engine, "verse.runChecks", {"chapter": "2", "verse": "7", "checks": ["names"]})
    assert result["success"] is True
    findings = result["findings"]
    assert len(findings) == 1
    finding = findings[0]
    assert finding["engine"] == "names"
    assert finding["original_text"] == "Tituss"
    assert finding["suggested_replacement"] == "Titus"
    # NamesAdapter itself never sees verse text (only token_occurrences), so
    # _names_findings_for_book must compute the span — real offsets into the
    # exact verse text, not just an anchor at (chapter, verse), so the
    # frontend can both highlight the flagged word inline and number it
    # (see highlight.ts's findingNumbers/buildSegments).
    verse_text = "In everything show yourself an example, Tituss my son."
    assert finding["start_offset"] is not None
    assert finding["end_offset"] is not None
    assert verse_text[finding["start_offset"]:finding["end_offset"]] == "Tituss"

    # Doesn't leak onto an unrelated verse that has no spelling issue.
    clean = call(engine, "verse.runChecks", {"chapter": "1", "verse": "1", "checks": ["names"]})
    assert clean["findings"] == []


def test_names_check_is_gated_behind_its_own_or_local_check_name(spelling_fixture_project):
    """Same convention as USFM findings: requesting an unrelated check list
    (e.g. just "wildebeest") should not incidentally run the whole-book
    names comparison."""
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(spelling_fixture_project)})

    result = call(engine, "verse.runChecks", {"chapter": "2", "verse": "7", "checks": ["wildebeest"]})
    assert all(f["engine"] != "names" for f in result["findings"])


def test_names_findings_have_stable_ids_across_a_fresh_process(spelling_fixture_project):
    engine1 = BridgeEngine()
    call(engine1, "project.open", {"path": str(spelling_fixture_project)})
    first = call(engine1, "verse.runChecks", {"chapter": "2", "verse": "7", "checks": ["names"]})
    first_id = first["findings"][0]["id"]

    engine2 = BridgeEngine()
    call(engine2, "project.open", {"path": str(spelling_fixture_project)})
    second = call(engine2, "verse.runChecks", {"chapter": "2", "verse": "7", "checks": ["names"]})
    second_id = second["findings"][0]["id"]

    assert first_id == second_id


def test_names_adapter_is_listed_as_a_real_engine():
    engine = BridgeEngine()
    info = call(engine, "engine.info")["result"]
    names_info = info["greekRoom"]["adapters"]["names"]
    assert names_info["available"] is True
    assert names_info["usingRealEngine"] is True
    assert names_info["state"] in {"not-loaded", "loading", "loaded"}  # #242


@pytest.fixture
def uroman_constructions(monkeypatch):
    """Reset the names adapter to a new process's state and count every
    Uroman() built from here on (#242)."""
    import uroman

    from greek_room_engine.adapters import names_adapter

    monkeypatch.setattr(names_adapter, "_uroman", None)
    monkeypatch.setattr(names_adapter, "_uroman_unavailable_reason", None)
    monkeypatch.setattr(names_adapter, "_uroman_thread", None)
    real = uroman.Uroman
    built: list[int] = []

    class Counting(real):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            built.append(1)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(uroman, "Uroman", Counting)
    return built


def test_engine_info_builds_no_uroman_and_names_still_finds_the_typo(spelling_fixture_project, uroman_constructions):
    engine = BridgeEngine()
    names_info = call(engine, "engine.info")["result"]["greekRoom"]["adapters"]["names"]
    assert names_info["available"] is True and names_info["state"] == "not-loaded"
    assert uroman_constructions == []
    call(engine, "project.open", {"path": str(spelling_fixture_project)})
    result = call(engine, "verse.runChecks", {"chapter": "2", "verse": "7", "checks": ["names"]})
    assert [f["original_text"] for f in result["findings"]] == ["Tituss"]
    assert uroman_constructions == [1]
    assert call(engine, "engine.info")["result"]["greekRoom"]["adapters"]["names"]["state"] == "loaded"


def test_cached_names_findings_never_load_uroman(spelling_fixture_project, monkeypatch):
    first = BridgeEngine()
    call(first, "project.open", {"path": str(spelling_fixture_project)})
    found = call(first, "verse.runChecks", {"chapter": "2", "verse": "7", "checks": ["names"]})["findings"]
    assert [f["original_text"] for f in found] == ["Tituss"]

    import uroman

    from greek_room_engine.adapters import names_adapter

    monkeypatch.setattr(names_adapter, "_uroman", None)
    monkeypatch.setattr(names_adapter, "_uroman_unavailable_reason", None)

    def refuse(*_args, **_kwargs):
        raise AssertionError("a book whose names cache matches must not load uroman")

    monkeypatch.setattr(uroman, "Uroman", refuse)
    second = BridgeEngine()
    call(second, "project.open", {"path": str(spelling_fixture_project)})
    again = call(second, "verse.runChecks", {"chapter": "2", "verse": "7", "checks": ["names"]})
    assert again["success"] is True, again
    assert [f["id"] for f in again["findings"]] == [f["id"] for f in found]


@pytest.fixture
def tamil_spelling_fixture_project(tmp_path):
    """The real reason this check exists: Bridge's actual target languages
    are mostly non-Latin (Tamil, Odia, Hebrew, ...), and every test above
    this one only exercised English, where Uroman's romanization step is
    close to a no-op. This plants a real, common Indic-script consistency
    issue — an inconsistently included/omitted long-a vowel sign (matra)
    on the same name, "யோவான்" vs "யோவன்" — inside full Tamil verse
    sentences (not isolated words), so whitespace_tokens' real
    punctuation/combining-mark handling is exercised too, not bypassed."""
    root = tmp_path / "tit_tam"
    align_dir = root / ".apps" / "translationCore" / "alignmentData" / "tit"
    align_dir.mkdir(parents=True)
    (root / "tit").mkdir(parents=True)

    (root / "manifest.json").write_text(json.dumps({
        "project": {"id": "tit", "name": "Titus"},
        "target_language": {"id": "tam", "name": "Tamil"},
        "tc_version": "8", "tc_edit_version": "3.7.0",
    }), encoding="utf-8")

    (align_dir / "1.json").write_text(json.dumps({
        "1": {"alignments": [], "wordBank": []},
        "5": {"alignments": [], "wordBank": []},
    }), encoding="utf-8")
    (align_dir / "2.json").write_text(json.dumps({
        "3": {"alignments": [], "wordBank": []},
    }), encoding="utf-8")

    (root / "tit" / "1.json").write_text(json.dumps({
        "1": "பவுல், தேவனுடைய ஊழியக்காரனும் யோவான் என்பவனுக்கு எழுதுகிறது.",
        "5": "யோவான் ஒரு உண்மையான மகன்.",
    }, ensure_ascii=False), encoding="utf-8")
    (root / "tit" / "2.json").write_text(json.dumps({
        "3": "யோவன் என்பவனுக்கு எழுதுகிறேன்.",
    }, ensure_ascii=False), encoding="utf-8")

    return root


def test_names_check_flags_a_real_tamil_vowel_sign_inconsistency(tamil_spelling_fixture_project):
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(tamil_spelling_fixture_project)})

    result = call(engine, "verse.runChecks", {"chapter": "2", "verse": "3", "checks": ["names"]})
    assert result["success"] is True
    findings = result["findings"]
    assert len(findings) == 1
    finding = findings[0]
    assert finding["engine"] == "names"
    assert finding["original_text"] == "யோவன்"
    assert finding["suggested_replacement"] == "யோவான்"

    # The correctly-spelled, repeated occurrences don't flag against
    # themselves or against the unrelated word "தேவனுடைய".
    clean = call(engine, "verse.runChecks", {"chapter": "1", "verse": "1", "checks": ["names"]})
    assert clean["findings"] == []


def test_switching_projects_clears_the_cached_names_findings(spelling_fixture_project, tmp_path):
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(spelling_fixture_project)})
    result = call(engine, "verse.runChecks", {"chapter": "2", "verse": "7", "checks": ["names"]})
    assert len(result["findings"]) == 1

    # A second, unrelated clean project must not see the first project's
    # cached whole-book findings bleed through (same class of bug gotcha
    # already fixed for chapter/book state elsewhere in this app).
    other = tmp_path / "other_tit"
    other.mkdir()
    (other / "tit").mkdir()
    (other / ".apps" / "translationCore" / "alignmentData" / "tit").mkdir(parents=True)
    (other / "manifest.json").write_text(json.dumps({
        "project": {"id": "tit", "name": "Titus"},
        "target_language": {"id": "eng", "name": "English"},
        "tc_version": "8", "tc_edit_version": "3.7.0",
    }), encoding="utf-8")
    (other / "tit" / "1.json").write_text(json.dumps({
        "1": "Paul, a servant of God, to Titus my true son.",
    }), encoding="utf-8")
    (other / ".apps" / "translationCore" / "alignmentData" / "tit" / "1.json").write_text(
        json.dumps({"1": {"alignments": [], "wordBank": []}}), encoding="utf-8",
    )

    call(engine, "project.open", {"path": str(other)})
    clean = call(engine, "verse.runChecks", {"chapter": "1", "verse": "1", "checks": ["names"]})
    assert clean["findings"] == []
