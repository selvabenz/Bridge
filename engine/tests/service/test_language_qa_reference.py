"""The reference Bible and related words through the real dispatcher. A
Tamil project gets the 1957 OV that ta-irv ships, with no folder set; any
pack can be pointed at a folder in Settings > Language QA."""
import time

import pytest

from bridge_service import BridgeEngine
from tc_ai_bridge.language_qa_jobs import LanguageQaManager
from tc_ai_bridge.secret_store import AppSettings
from tests.support.indic_qa import wait, write_project
from tests.support.projects import call

# RUT 1:1-2 as the IRV words them (two words differ from the OV in each verse).
VERSES = {"1": "நியாயாதிபதிகள் நியாயம் செய்த நாட்களில், தேசத்திலே பஞ்சம் உண்டாயிற்று;",
          "2": "அந்த மனிதனுடைய பெயர் எலிமெலேக்கு."}
TSV_HEADER = "ref\tbook\tbook_idx\tchapter\tverse\tseg\ttext\n"


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


def until_ready(bridge, method, deadline=60.0, **params):
    """Reference and related words load on a background thread; poll as the UI does."""
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        result = rpc(bridge, method, **params)
        if result.get("ready") or result.get("error"):
            return result
        time.sleep(0.05)
    pytest.fail(f"{method} never became ready")


def test_a_tamil_book_gets_the_bundled_ov_with_no_folder_set(engine):
    bridge, project = engine
    first = rpc(bridge, "languageQa.reference", projectPath=str(project), chapter="1")
    assert first["configured"] is True and first["source"]["label"] == "Old Version (bundled)"
    result = until_ready(bridge, "languageQa.reference", projectPath=str(project), chapter="1")
    assert result["ready"] and result["verses"][0]["verse"] == "1"
    assert result["verses"][0]["text"].startswith("நியாயாதிபதிகள் நியாயம் விசாரித்துவரும்")


def test_a_folder_set_in_settings_replaces_the_bundled_one(engine, tmp_path):
    bridge, project = engine
    folder = tmp_path / "my-ov"
    folder.mkdir()
    (folder / "verses.tsv").write_text(TSV_HEADER + "RUT 1:1\tRUT\t8\t1\t1\tv\tஎன் பழைய மொழிபெயர்ப்பு.\n",
                                       encoding="utf-8")
    settings = rpc(bridge, "settings.set", languageQaReferenceDirs={"ta-irv": str(folder)})
    assert settings["languageQaReferenceDirs"] == {"ta-irv": str(folder)}
    result = until_ready(bridge, "languageQa.reference", projectPath=str(project), chapter="1")
    assert result["source"]["label"] == "Old Version" and result["verses"] == [
        {"verse": "1", "text": "என் பழைய மொழிபெயர்ப்பு."}]
    assert (tmp_path / "settings" / "reference-cache").is_dir(), "the plain text is cached in the app's data folder"


def test_a_folder_without_scripture_is_refused_at_once(engine, tmp_path):
    bridge, _project = engine
    (tmp_path / "empty").mkdir()
    refused = call(bridge, "settings.set", {"languageQaReferenceDirs": {"ta-irv": str(tmp_path / "empty")}})
    assert not refused["success"] and "no verses.tsv" in refused["error"]["message"]
    cleared = rpc(bridge, "settings.set", languageQaReferenceDirs={"ta-irv": ""})
    assert cleared["languageQaReferenceDirs"] == {}


def test_ov_occurrences_come_from_the_reference_text(engine):
    bridge, project = engine
    until_ready(bridge, "languageQa.reference", projectPath=str(project), chapter="1")
    result = rpc(bridge, "languageQa.occurrences", projectPath=str(project), word="எலிமெலேக்கு", source="ov", limit=5)
    assert result["ready"] and result["total"] >= 1
    assert result["hits"][0]["book"] == "rut"
    hit = result["hits"][0]
    assert hit["snippet"][hit["snippetStart"]:hit["snippetEnd"]] == "எலிமெலேக்கு"


def test_related_words_build_once_in_the_background_and_can_be_turned_off(engine):
    bridge, project = engine
    until_ready(bridge, "languageQa.reference", projectPath=str(project), chapter="1")
    result = until_ready(bridge, "languageQa.related", projectPath=str(project), word="மனிதனுடைய")
    assert result["ready"] and result["versesAligned"] >= 2
    assert result["irv"] == 1, "the IRV project uses it once (verse 2)"
    assert result["ov"] > 0, "counted over the whole 1957 OV"
    again = rpc(bridge, "languageQa.related", projectPath=str(project), word="மனிதனுடைய")
    assert again["ready"], "built once, kept for the session"
    rpc(bridge, "settings.set", languageQaRelatedWords=False)
    off = rpc(bridge, "languageQa.related", projectPath=str(project), word="மனிதனுடைய")
    assert off["ready"] is False and off["off"] is True
