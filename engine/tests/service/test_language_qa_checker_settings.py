"""languageQa.checkerSettings.get / .set through the real dispatcher: the
indic-qa checker's own settings (the web app's Settings dialog), per collection
(DECISIONS 2026-10-08)."""
import json

import pytest

from bridge_service import BridgeEngine
from tc_ai_bridge.language_packs import indic_qa_adapter
from tc_ai_bridge.language_packs.loader import overrides_path
from tc_ai_bridge.language_qa_jobs import LanguageQaManager
from tc_ai_bridge.secret_store import AppSettings
from tests.support.indic_qa import wait, write_project
from tests.support.projects import call, link_collection

# मनूष्य: a vowel-length slip of मनुष्य, so hi.lex.unknown with suggestions.
HINDI = {"1": "वह मनूष्य गया।", "2": "यह वचन ठीक है।"}
TAMIL = {"1": "அவன் வீட்டுக்குப் போனான்.", "2": "அவள் வந்தாள்."}


def engine_for(tmp_path, books, lang_id, lang_name, verses):
    roots = {name: write_project(tmp_path / name, name, {"1": verses}, lang_id=lang_id, lang_name=lang_name)
             for name in books}
    if len(roots) > 1:
        link_collection(roots)
    engine = BridgeEngine(settings=AppSettings(path=tmp_path / "settings" / "settings.json"))
    engine._language_qa = LanguageQaManager(debounce=0, yield_seconds=0)
    engine._configure_language_qa()
    first = roots[books[0]]
    assert call(engine, "project.open", {"path": str(first)})["success"]
    wait(engine._language_qa)
    return engine, roots


@pytest.fixture
def hindi(tmp_path):
    engine, roots = engine_for(tmp_path, ["gen", "exo"], "hin", "Hindi", HINDI)
    yield engine, roots
    engine._language_qa.unbind()


def rpc(engine, method, **params):
    response = call(engine, method, params)
    assert response["success"], response
    return response["result"]


def get(engine, root):
    return rpc(engine, "languageQa.checkerSettings.get", projectPath=str(root))


def unknown_findings(engine, root):
    wait(engine._language_qa)
    status = rpc(engine, "languageQa.status", projectPath=str(root), limit=100)
    return [f for f in status["findings"] if f["rule"] == "hi.lex.unknown"]


def test_get_describes_the_hindi_checker_after_a_pass(hindi):
    engine, roots = hindi
    model = get(engine, roots["gen"])
    assert (model["pack"], model["layer"], model["ready"]) == ("hi-irv", False, True)
    assert model["contexts"] == {"checkable": ["verse"], "checked": ["verse"], "labels": {"verse": "verse text"}}
    assert model["warnings"]["values"]["double_space"] is True and model["warnings"]["labels"]["double_space"]
    assert model["suggest"] == {"max": 5, "limit": 9} and model["compound"] == {"enabled": True}
    unknown = next(r for r in model["rules"] if r["id"] == "hi.lex.unknown")
    assert (unknown["enabled"], unknown["default"], unknown["count"]) == (True, True, 1)
    assert [n["key"] for n in model["numbers"]] == ["irv_accept_min", "minority_max"]
    assert isinstance(model["style"], list) and "sandhi" not in model
    assert model["books"] == ["exo", "gen"]


def test_without_a_resident_checker_the_style_choices_wait_for_a_pass(hindi):
    engine, roots = hindi
    indic_qa_adapter.release()
    model = get(engine, roots["gen"])
    assert model["ready"] is False and model["style"] == []
    assert model["rules"] and model["numbers"], "everything else needs no checker"


def test_set_changes_what_the_checker_reports_and_writes_every_book(hindi):
    engine, roots = hindi
    assert len(unknown_findings(engine, roots["gen"])[0]["suggestions"]) >= 1
    saved = rpc(engine, "languageQa.checkerSettings.set", projectPath=str(roots["gen"]),
                checker={"suggest": {"max": 1}}, rules={"hi.lex.archaic-form": {"enabled": True}})
    assert saved["suggest"]["max"] == 1
    assert next(r for r in saved["rules"] if r["id"] == "hi.lex.archaic-form")["enabled"] is True
    for root in roots.values():
        document = json.loads(overrides_path(root, "hi-irv").read_text(encoding="utf-8"))
        assert document == {"checker": {"version": 1, "suggest": {"max": 1}},
                            "rules": {"hi.lex.archaic-form": {"enabled": True}}}
    rpc(engine, "languageQa.checkerSettings.set", projectPath=str(roots["gen"]),
        rules={"hi.lex.unknown": {"enabled": False}})
    assert unknown_findings(engine, roots["gen"]) == []
    restored = rpc(engine, "languageQa.checkerSettings.set", projectPath=str(roots["gen"]),
                   checker={"suggest": {"max": 5}}, rules={"hi.lex.unknown": {"enabled": True},
                                                          "hi.lex.archaic-form": {"enabled": False}})
    assert restored["suggest"]["max"] == 5
    assert not overrides_path(roots["gen"], "hi-irv").exists(), "back to the defaults: nothing left to keep"
    assert unknown_findings(engine, roots["gen"])


def test_set_keeps_a_book_s_other_overrides(hindi):
    engine, roots = hindi
    path = overrides_path(roots["exo"], "hi-irv")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"rules": {"hi.lex.unknown": {"abstain": [{"word": "मनूष्य"}]}}}), encoding="utf-8")
    rpc(engine, "languageQa.checkerSettings.set", projectPath=str(roots["gen"]),
        rules={"hi.lex.unknown": {"enabled": False}})
    assert json.loads(path.read_text(encoding="utf-8"))["rules"]["hi.lex.unknown"] == {
        "abstain": [{"word": "मनूष्य"}], "enabled": False}


@pytest.mark.parametrize("params", [
    {"checker": {"sandhi": {"min_total": 3}}},
    {"checker": {"checked_contexts": ["heading"]}},
    {"checker": {"suggest": {"max": "5"}}},
    {"rules": {"hi.not-a-rule": {"enabled": False}}},
    {"rules": {"hi.lex.unknown": {"inline": True}}},
    {"colour": "red"},
])
def test_set_refuses_what_the_checker_would_not_understand(hindi, params):
    engine, roots = hindi
    response = call(engine, "languageQa.checkerSettings.set", {"projectPath": str(roots["gen"]), **params})
    assert not response["success"]
    assert not overrides_path(roots["gen"], "hi-irv").exists(), "nothing half-valid is written"


def test_checker_settings_are_project_guarded(hindi, tmp_path):
    engine, _roots = hindi
    for method in ("languageQa.checkerSettings.get", "languageQa.checkerSettings.set"):
        response = call(engine, method, {"projectPath": str(tmp_path / "elsewhere")})
        assert not response["success"] and "different project" in response["error"]["message"]


def test_the_tamil_layer_offers_sandhi_and_three_contexts(tmp_path):
    engine, roots = engine_for(tmp_path, ["rut"], "tam", "Tamil", TAMIL)
    try:
        model = get(engine, roots["rut"])
        assert model["layer"] is True and model["pack"] == "ta-irv"
        assert model["contexts"]["checkable"] == ["verse", "heading", "footnote_text"]
        assert set(model["sandhi"]["values"]) == set(model["sandhi"]["labels"]) and len(model["sandhi"]["values"]) == 11
        assert not {"digits", "repeated_word", "space_before_note_close"} & set(model["warnings"]["values"])
        assert "numbers" not in model and "style" not in model
        assert any(r["id"] == "indicqa.lex.unknown" for r in model["rules"])
    finally:
        engine._language_qa.unbind()


def test_a_project_with_no_indic_qa_checker_has_no_checker_settings(tmp_path):
    root = write_project(tmp_path / "php", "php", {"1": {"1": "plain English text"}}, lang_id="en", lang_name="English")
    engine = BridgeEngine(settings=AppSettings(path=tmp_path / "settings" / "settings.json"))
    engine._language_qa = LanguageQaManager(debounce=0, yield_seconds=0)
    try:
        assert call(engine, "project.open", {"path": str(root)})["success"]
        model = get(engine, root)
        assert model == {"pack": None, "available": False,
                         "reason": "This project's language has no indic-qa checker."}
    finally:
        engine._language_qa.unbind()
