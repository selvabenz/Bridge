"""Project words, Book words and IRV occurrences through the real dispatcher
(indic-qa's "Add to dictionary", unknown-word table and occurrence list).
Hindi: a reviewed misspelling in three verses across two chapters."""
import pytest

from bridge_service import BridgeEngine
from tc_ai_bridge.language_qa_jobs import LanguageQaManager
from tc_ai_bridge.secret_store import AppSettings
from tests.support.indic_qa import wait, write_project
from tests.support.projects import call

CHAPTERS = {"1": {"1": "उसकी आयु सताईस वर्ष की थी।", "2": "वे सताईस दिन रहे।"},
            "2": {"1": "और सताईस लोग थे।", "2": "राज्यपाल ने कहा।", "3": "तब राज्यपाल आया।"}}
WORD = "सताईस"


@pytest.fixture
def engine(tmp_path):
    project = write_project(tmp_path / "gen", "gen", CHAPTERS, lang_id="hin", lang_name="Hindi")
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


def misspellings(bridge, project):
    wait(bridge._language_qa)
    findings = rpc(bridge, "languageQa.status", projectPath=str(project), limit=100)["findings"]
    return [f for f in findings if f["rule"] == "hi.lex.known-misspelling"]


def test_a_project_word_silences_its_spelling_findings_without_a_rescan(engine):
    bridge, project = engine
    assert len(misspellings(bridge, project)) == 3
    result = rpc(bridge, "languageQa.words.add", projectPath=str(project), words=[WORD])
    assert result["count"] == 1 and result["entries"][0]["list"] == "projectWords"
    assert misspellings(bridge, project) == []
    status = rpc(bridge, "languageQa.status", projectPath=str(project), limit=0)
    assert status["scannedVerses"] == 0, "a project word is applied after the scan: nothing is rescanned"
    added = rpc(bridge, "languageQa.words.list", projectPath=str(project))["added"]
    assert [e["word"] for e in added] == [WORD]


def test_removing_a_project_word_brings_its_findings_back(engine):
    bridge, project = engine
    entry = rpc(bridge, "languageQa.words.add", projectPath=str(project), words=[WORD])["entries"][0]
    assert misspellings(bridge, project) == []
    rpc(bridge, "housestyle.setState", key=entry["key"], state="removed")
    assert len(misspellings(bridge, project)) == 3


def test_words_are_checked(engine):
    bridge, project = engine
    for bad in ([], ["two words"], [""], "सताईस", ["x"] * 501):
        assert not call(bridge, "languageQa.words.add", {"projectPath": str(project), "words": bad})["success"], bad
    assert not call(bridge, "languageQa.words.add", {"projectPath": str(project), "words": ["x"],
                                                      "scope": "bible"})["success"]


def test_book_words_lists_words_outside_the_dictionary_by_frequency_and_marks_added_ones(engine):
    bridge, project = engine
    result = rpc(bridge, "languageQa.bookWords", projectPath=str(project))
    assert result["ready"] and result["total"] >= 1
    # राज्यपाल is an IRV word the OV dictionary does not have (indic-qa: irv_ok).
    governor = next(w for w in result["words"] if w["word"] == "राज्यपाल")
    assert governor["countBook"] == 2 and governor["status"] == "irv_ok" and governor["countIrv"] > 0
    assert governor["firstRef"] == {"chapter": "2", "verse": "2"}
    counts = [w["countBook"] for w in result["words"]]
    assert counts == sorted(counts, reverse=True)
    assert all(w["status"] in {"unknown", "irv_ok", "inflected_ok", "compound", "rare_near_common"}
               for w in result["words"])
    some = result["words"][0]["word"]
    rpc(bridge, "languageQa.words.add", projectPath=str(project), words=[some])
    wait(bridge._language_qa)
    again = {w["word"]: w for w in rpc(bridge, "languageQa.bookWords", projectPath=str(project))["words"]}
    assert again[some]["status"] == "added"


def test_irv_occurrences_scan_the_open_book_with_raw_offsets(engine):
    bridge, project = engine
    result = rpc(bridge, "languageQa.occurrences", projectPath=str(project), word=WORD, source="irv")
    assert (result["total"], result["truncated"]) == (3, False)
    assert [(h["chapter"], h["verse"]) for h in result["hits"]] == [("1", "1"), ("1", "2"), ("2", "1")]
    for hit in result["hits"]:
        assert CHAPTERS[hit["chapter"]][hit["verse"]][hit["start"]:hit["end"]] == WORD
        assert hit["snippet"][hit["snippetStart"]:hit["snippetEnd"]] == WORD
    capped = rpc(bridge, "languageQa.occurrences", projectPath=str(project), word=WORD, limit=1)
    assert (capped["total"], len(capped["hits"]), capped["truncated"]) == (3, 1, True)
