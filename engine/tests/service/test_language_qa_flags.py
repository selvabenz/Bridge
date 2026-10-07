"""Reviewer flags through the real dispatcher: a question on a passage, kept
in the workbench (v6), never a Scripture write and never a finding."""
import json

import pytest

from bridge_service import BridgeEngine
from tc_ai_bridge.language_qa_jobs import LanguageQaManager
from tc_ai_bridge.secret_store import AppSettings
from tests.support.indic_qa import wait, write_project
from tests.support.projects import call

VERSES = {"1": "அவர் தேவன் என்றார்.", "2": "தேவன் நல்லவர்."}


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


def new_flag(**overrides):
    flag = {"chapter": "1", "verse": "1", "start": 5, "end": 10, "text": "தேவன்", "type": "style", "note": "check"}
    return {**flag, **overrides}


def test_add_list_update_and_delete_keep_every_image(engine):
    bridge, project = engine
    flag = rpc(bridge, "languageQa.flags.add", projectPath=str(project), flag=new_flag())["flag"]
    assert (flag["status"], flag["text"], flag["type"]) == ("open", "தேவன்", "style")
    assert [f["flagId"] for f in rpc(bridge, "languageQa.flags.list", projectPath=str(project), chapter="1")["flags"]] == [
        flag["flagId"]]
    rpc(bridge, "languageQa.flags.update", projectPath=str(project), flagId=flag["flagId"],
        patch={"status": "resolved", "note": "done"})
    rpc(bridge, "languageQa.flags.delete", projectPath=str(project), flagId=flag["flagId"])
    assert rpc(bridge, "languageQa.flags.list", projectPath=str(project))["flags"] == []
    rows = bridge.project.workbench.events_for_row("language_qa_flags", bridge.project._language_qa_flag_row_id(flag["flagId"]),
                                                   project_id=bridge.project.workbench_identity.project_id)
    assert [json.loads(r["payload_json"])["status"] for r in rows] == ["open", "resolved", "deleted"]
    assert rpc(bridge, "verse.get", chapter="1", verse="1")["text"] == VERSES["1"], "a flag never writes Scripture"


def test_a_flag_must_point_at_the_text_that_is_there(engine):
    bridge, project = engine
    for bad in (new_flag(text="வேறு"), new_flag(start=8, end=4), new_flag(end=999), new_flag(type="opinion"),
                new_flag(verse="9"), new_flag(note="x" * 5000), new_flag(suggested="  ")):
        assert not call(bridge, "languageQa.flags.add", {"projectPath": str(project), "flag": bad})["success"], bad
    assert not call(bridge, "languageQa.flags.update", {"projectPath": str(project), "flagId": "nope",
                                                         "patch": {"status": "resolved"}})["success"]


def test_an_update_may_not_move_or_rewrite_the_flagged_text(engine):
    bridge, project = engine
    flag = rpc(bridge, "languageQa.flags.add", projectPath=str(project), flag=new_flag())["flag"]
    moved = call(bridge, "languageQa.flags.update", {"projectPath": str(project), "flagId": flag["flagId"],
                                                      "patch": {"text": "x", "start": 0}})
    assert not moved["success"]


def test_a_one_word_suggested_form_is_learned_and_a_passage_is_not(engine):
    bridge, project = engine
    result = rpc(bridge, "languageQa.flags.add", projectPath=str(project), flag=new_flag(suggested="கர்த்தர்"))
    assert result["learned"]["old"] == "தேவன்" and result["learned"]["new"] == "கர்த்தர்"
    passage = new_flag(start=0, end=10, text="அவர் தேவன்", suggested="அவர் கர்த்தர்")
    assert "learned" not in rpc(bridge, "languageQa.flags.add", projectPath=str(project), flag=passage)
    fixes = rpc(bridge, "languageQa.learned.list", projectPath=str(project))["fixes"]
    assert [(f["old"], f["new"], f["source"]) for f in fixes] == [("தேவன்", "கர்த்தர்", "flag")]
