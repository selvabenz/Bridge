"""Learned fixes through the real dispatcher: a single-word edit is learned,
the next pass offers it wherever the old word recurs, and forget/restore,
typing it back and the Settings switch behave as indic-qa's editor does."""
import pytest

from bridge_service import BridgeEngine
from tc_ai_bridge.language_qa_jobs import LanguageQaManager
from tc_ai_bridge.secret_store import AppSettings
from tests.support.indic_qa import wait, write_project
from tests.support.projects import call

VERSES = {"1": "அவர் தேவன் என்றார்.", "2": "தேவன் நல்லவர்.", "3": "நாம் தேவன் பேரில் நம்புவோம்."}
OLD, NEW = "தேவன்", "கர்த்தர்"


@pytest.fixture
def engine(tmp_path):
    project = write_project(tmp_path / "rut", "rut", {"1": VERSES}, lang_id="tam", lang_name="Tamil")
    bridge = BridgeEngine(settings=AppSettings(path=tmp_path / "settings" / "settings.json"))
    bridge._language_qa = LanguageQaManager(debounce=0, yield_seconds=0)
    bridge._configure_language_qa()
    assert call(bridge, "project.open", {"path": str(project)})["success"]
    wait(bridge._language_qa)
    yield bridge, project
    bridge._language_qa.unbind()


def rpc(bridge, method, **params):
    response = call(bridge, method, params)
    assert response["success"], response
    return response["result"]


def learned(bridge, project):
    """Learned-fix findings drawn in chapter 1, after the next pass."""
    wait(bridge._language_qa)
    return [f for f in rpc(bridge, "languageQa.inline", projectPath=str(project), chapter="1")["findings"]
            if f["rule"] == "learned.replacement"]


def edit(bridge, verse, text):
    return rpc(bridge, "verse.edit", chapter="1", verse=verse, newText=text)


def test_a_single_word_edit_is_learned_and_offered_wherever_the_word_recurs(engine):
    bridge, project = engine
    assert learned(bridge, project) == []
    result = edit(bridge, "1", VERSES["1"].replace(OLD, NEW))
    assert result["learned"] == {"old": OLD, "new": NEW, "action": "learned", "count": 1, "enabled": True}
    found = learned(bridge, project)
    assert sorted(f["verse"] for f in found) == ["2", "3"]
    for finding in found:
        assert VERSES[finding["verse"]][finding["start"]:finding["end"]] == OLD
        assert [s["text"] for s in finding["suggestions"]] == [NEW] and finding["drawn"] is True
    fixes = rpc(bridge, "languageQa.learned.list", projectPath=str(project))["fixes"]
    assert [(f["old"], f["new"], f["count"], f["firstRef"]) for f in fixes] == [(OLD, NEW, 1, "RUT 1:1")]


def test_an_edit_that_is_not_one_word_learns_nothing(engine):
    bridge, project = engine
    assert "learned" not in edit(bridge, "1", "அவர் கர்த்தர் சொன்னார்.")
    assert "learned" not in edit(bridge, "2", VERSES["2"] + " ஆமென்.")
    assert rpc(bridge, "languageQa.learned.list", projectPath=str(project))["fixes"] == []


def test_typing_the_word_back_retracts_the_fix(engine):
    bridge, project = engine
    edit(bridge, "1", VERSES["1"].replace(OLD, NEW))
    result = edit(bridge, "1", VERSES["1"])
    assert result["learned"]["action"] == "retracted"
    assert learned(bridge, project) == []
    [fix] = rpc(bridge, "languageQa.learned.list", projectPath=str(project))["fixes"]
    assert fix["count"] == 0, "kept, no longer offered"


def test_forget_and_restore_rewrite_the_fix_and_run_a_pass(engine):
    bridge, project = engine
    edit(bridge, "1", VERSES["1"].replace(OLD, NEW))
    assert len(learned(bridge, project)) == 2
    assert rpc(bridge, "languageQa.learned.forget", projectPath=str(project), old=OLD, new=NEW)["fix"]["enabled"] is False
    assert learned(bridge, project) == []
    rpc(bridge, "languageQa.learned.restore", projectPath=str(project), old=OLD, new=NEW)
    assert len(learned(bridge, project)) == 2
    missing = call(bridge, "languageQa.learned.forget", {"projectPath": str(project), "old": "x", "new": "y"})
    assert not missing["success"]


def test_an_ignored_learned_finding_stays_hidden(engine):
    bridge, project = engine
    edit(bridge, "1", VERSES["1"].replace(OLD, NEW))
    finding = learned(bridge, project)[0]
    rpc(bridge, "verse.decide", chapter="1", verse=finding["verse"], findingId=finding["id"], status="ignored",
        issue={"source": "languageQa", "rule": finding["rule"], "ruleId": finding["ruleId"],
               "ruleVersion": finding["ruleVersion"], "packVersion": finding["packVersion"],
               "ruleRevision": finding["ruleRevision"], "originalText": finding["originalText"]})
    assert finding["id"] not in {f["id"] for f in learned(bridge, project)}


def test_the_settings_switch_stops_learning_and_offering(engine):
    bridge, project = engine
    edit(bridge, "1", VERSES["1"].replace(OLD, NEW))
    assert len(learned(bridge, project)) == 2
    assert rpc(bridge, "settings.set", languageQaLearnedFixes=False)["languageQaLearnedFixes"] is False
    assert learned(bridge, project) == []
    assert "learned" not in edit(bridge, "2", VERSES["2"].replace(OLD, "இறைவன்"))


def test_a_raw_edit_with_unbalanced_markup_is_saved_with_a_warning(engine):
    bridge, _project = engine
    result = edit(bridge, "3", "நாம் \\it தேவன் பேரில் நம்புவோம்.")
    assert result["committed"] and result["warnings"]
