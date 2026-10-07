"""A Hindi project through the real dispatcher: Language QA resolves the
hi-irv profile pack, reports its findings with every Bridge field, and a
decision hides one on the next pass, as for any other pack."""
import pytest

from bridge_service import BridgeEngine
from tc_ai_bridge.language_qa_jobs import LanguageQaManager
from tests.support.indic_qa import wait, write_project
from tests.support.projects import call

VERSES = {"1": "उसकी आयु सताईस वर्ष की थी।", "2": "वे बडे़ लोग थे।", "3": "यह वचन ठीक है।"}


@pytest.fixture
def hindi_engine(tmp_path):
    project = write_project(tmp_path / "gen", "gen", {"1": VERSES}, lang_id="hin", lang_name="Hindi")
    engine = BridgeEngine()
    engine._language_qa = LanguageQaManager(debounce=0, yield_seconds=0)
    response = call(engine, "project.open", {"path": str(project)})
    assert response["success"], response
    yield engine, project
    engine._language_qa.unbind()


def status(engine, project):
    response = call(engine, "languageQa.status", {"projectPath": str(project), "limit": 100})
    assert response["success"], response
    return response["result"]


def test_a_hindi_project_runs_the_hindi_pack(hindi_engine):
    engine, project = hindi_engine
    wait(engine._language_qa)
    result = status(engine, project)
    assert (result["language"]["pack"], result["language"]["name"]) == ("hi-irv", "Hindi")
    assert result["rulePack"] == "hi-irv@1.0.0"
    rules = {f["ruleId"] for f in result["findings"]}
    assert {"hi-irv/hi.lex.known-misspelling", "hi-irv/hi.shape.errors"} <= rules
    for finding in result["findings"]:
        assert VERSES[finding["verse"]][finding["start"]:finding["end"]] == finding["originalText"]
        assert finding["source"] == "languageQa" and finding["status"] == "review-needed"
    assert not {f["ruleId"] for f in result["findings"] if f["inline"]} - {
        "common/spacing.extra"}, "no indic-qa rule is drawn inline before the human gate"
    in_scope = [row["category"] for row in result["coverage"]["inScope"]]
    assert "consistency" in in_scope and "grammar" in in_scope and "sandhi" not in in_scope
    assert "agreement" not in {row["category"] for row in result["coverage"]["outOfScope"]}


def test_an_ignored_indic_qa_finding_stays_hidden_on_the_next_pass(hindi_engine):
    engine, project = hindi_engine
    wait(engine._language_qa)
    finding = next(f for f in status(engine, project)["findings"] if f["rule"] == "hi.lex.known-misspelling")
    response = call(engine, "verse.decide", {
        "chapter": finding["chapter"], "verse": finding["verse"], "findingId": finding["id"], "status": "ignored",
        "issue": {"source": "languageQa", "rule": finding["rule"], "ruleId": finding["ruleId"],
                  "ruleVersion": finding["ruleVersion"], "packVersion": finding["packVersion"],
                  "ruleRevision": finding["ruleRevision"], "originalText": finding["originalText"]}})
    assert response["success"], response
    wait(engine._language_qa)
    assert finding["id"] not in {f["id"] for f in status(engine, project)["findings"]}


def test_the_off_setting_runs_the_common_checks_only(tmp_path):
    project = write_project(tmp_path / "gen", "gen", {"1": VERSES}, lang_id="hin", lang_name="Hindi",
                            language_qa={"pack": "off"})
    engine = BridgeEngine()
    engine._language_qa = LanguageQaManager(debounce=0, yield_seconds=0)
    try:
        assert call(engine, "project.open", {"path": str(project)})["success"]
        wait(engine._language_qa)
        result = status(engine, project)
        assert result["language"]["pack"] == "common"
        assert not [f for f in result["findings"] if f["ruleId"].startswith("hi-irv/")]
    finally:
        engine._language_qa.unbind()
