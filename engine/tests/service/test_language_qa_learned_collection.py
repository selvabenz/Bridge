"""Learned fixes shared across a collection's books (DECISIONS 2026-10-07):
a fix learned in one book is offered in its siblings, Forget and Restore act
on every book, and typing the word back counts against the collection's
total, which never goes below zero. Through the real dispatcher."""
import json

import pytest

from bridge_service import BridgeEngine
from tc_ai_bridge.language_qa_jobs import LanguageQaManager
from tc_ai_bridge.secret_store import AppSettings
from tc_ai_bridge.tc_project import TranslationCoreProject
from tests.support.indic_qa import wait, write_project
from tests.support.projects import call

VERSES = {"1": "அவர் தேவன் என்றார்.", "2": "தேவன் நல்லவர்.", "3": "நாம் தேவன் பேரில் நம்புவோம்."}
OLD, NEW = "தேவன்", "கர்த்தர்"


def _link(books: dict[str, object]) -> None:
    """.bridge/collection.json on every book, as a multi-book import writes it."""
    entries = [{"directoryName": name, "bookId": name, "bookName": name.upper()} for name in books]
    for root in books.values():
        (root / ".bridge").mkdir(parents=True, exist_ok=True)
        (root / ".bridge" / "collection.json").write_text(json.dumps({"projects": entries}), encoding="utf-8")


@pytest.fixture
def collection(tmp_path):
    books = {name: write_project(tmp_path / name, name, {"1": VERSES}, lang_id="tam", lang_name="Tamil")
             for name in ("rut", "gen")}
    _link(books)
    bridge = BridgeEngine(settings=AppSettings(path=tmp_path / "settings" / "settings.json"))
    bridge._language_qa = LanguageQaManager(debounce=0, yield_seconds=0)
    bridge._configure_language_qa()
    yield bridge, books
    bridge._language_qa.unbind()


def rpc(bridge, method, **params):
    response = call(bridge, method, params)
    assert response["success"], response
    return response["result"]


def open_book(bridge, path):
    rpc(bridge, "project.open", path=str(path))
    wait(bridge._language_qa)


def learned_marks(bridge, path):
    wait(bridge._language_qa)
    return [f for f in rpc(bridge, "languageQa.inline", projectPath=str(path), chapter="1")["findings"]
            if f["rule"] == "learned.replacement"]


def test_a_fix_learned_in_one_book_is_offered_in_its_siblings(collection):
    bridge, books = collection
    open_book(bridge, books["rut"])
    rpc(bridge, "verse.edit", chapter="1", verse="1", newText=f"அவர் {NEW} என்றார்.")
    open_book(bridge, books["gen"])
    marks = learned_marks(bridge, books["gen"])
    assert sorted(m["verse"] for m in marks) == ["1", "2", "3"], "every recurrence in the sibling"
    fixes = rpc(bridge, "languageQa.learned.list", projectPath=str(books["gen"]))["fixes"]
    assert [(f["old"], f["new"], f["count"], f["own"], f["books"], f["firstRef"]) for f in fixes] == [
        (OLD, NEW, 1, 0, ["rut"], "RUT 1:1")]


def test_forget_and_restore_act_on_every_book(collection):
    bridge, books = collection
    open_book(bridge, books["rut"])
    rpc(bridge, "verse.edit", chapter="1", verse="1", newText=f"அவர் {NEW} என்றார்.")
    open_book(bridge, books["gen"])
    assert learned_marks(bridge, books["gen"])
    forgot = rpc(bridge, "languageQa.learned.forget", projectPath=str(books["gen"]), old=OLD, new=NEW)
    assert forgot["fix"]["enabled"] is False and forgot["failedBooks"] == []
    assert learned_marks(bridge, books["gen"]) == []
    rut = TranslationCoreProject(books["rut"])
    assert rut.learned_fix(OLD, NEW)["enabled"] is False, "the book it was learned in agrees"
    assert rut.learned_fix(OLD, NEW)["count"] == 1, "a forget keeps the uses"
    rpc(bridge, "languageQa.learned.restore", projectPath=str(books["gen"]), old=OLD, new=NEW)
    assert learned_marks(bridge, books["gen"])
    assert TranslationCoreProject(books["rut"]).learned_fix(OLD, NEW)["enabled"] is True
    refused = call(bridge, "languageQa.learned.forget", {"projectPath": str(books["gen"]), "old": "இல்லை", "new": "இல்லவே"})
    assert not refused["success"]


def test_typing_the_word_back_in_a_sibling_counts_against_the_collection(collection):
    bridge, books = collection
    open_book(bridge, books["rut"])
    rpc(bridge, "verse.edit", chapter="1", verse="1", newText=f"அவர் {NEW} என்றார்.")
    open_book(bridge, books["gen"])
    rpc(bridge, "verse.edit", chapter="1", verse="2", newText=f"{NEW} நல்லவர்.")
    # The fix is now used twice (once in each book). Typing it back in GEN
    # retracts one use, not learn the reverse.
    back = rpc(bridge, "verse.edit", chapter="1", verse="2", newText="தேவன் நல்லவர்.")
    assert back["learned"]["action"] == "retracted"
    fix = rpc(bridge, "languageQa.learned.list", projectPath=str(books["gen"]))["fixes"]
    assert [(f["old"], f["new"], f["count"]) for f in fix] == [(OLD, NEW, 1)]
    # Only RUT's use is left. The new word reaches GEN 1:3 in an edit that
    # changes two words (nothing is learned), and is typed back: that takes
    # RUT's use back, so GEN's own row goes to -1, the collection reaches zero
    # and the offer stops in every book.
    rpc(bridge, "verse.edit", chapter="1", verse="3", newText=f"நாம் {NEW} மேல் நம்புவோம்.")
    assert [(f["count"], f["own"]) for f in rpc(bridge, "languageQa.learned.list",
                                                  projectPath=str(books["gen"]))["fixes"]] == [(1, 0)]
    back = rpc(bridge, "verse.edit", chapter="1", verse="3", newText="நாம் தேவன் மேல் நம்புவோம்.")
    assert back["learned"]["action"] == "retracted"
    [fix] = rpc(bridge, "languageQa.learned.list", projectPath=str(books["gen"]))["fixes"]
    assert (fix["count"], fix["own"]) == (0, -1), fix
    assert learned_marks(bridge, books["gen"]) == []
    # Typed back again with nothing left to take back: that is an ordinary new
    # single-word edit, learned as the reverse fix, and the old one stays at zero.
    rpc(bridge, "verse.edit", chapter="1", verse="3", newText=f"நாம் {NEW} மேல் நம்பினோம்.")
    again = rpc(bridge, "verse.edit", chapter="1", verse="3", newText="நாம் தேவன் மேல் நம்பினோம்.")
    assert again["learned"]["action"] == "learned"
    fixes = {(f["old"], f["new"]): (f["count"], f["own"])
             for f in rpc(bridge, "languageQa.learned.list", projectPath=str(books["gen"]))["fixes"]}
    assert fixes == {(OLD, NEW): (0, -1), (NEW, OLD): (1, 1)}, "never below zero for the collection"


def test_a_lazy_book_is_not_read_until_it_is_materialized(collection, tmp_path):
    bridge, books = collection
    exo = write_project(tmp_path / "exo", "exo", {"1": VERSES}, lang_id="tam", lang_name="Tamil")
    TranslationCoreProject(exo).record_learned_fix(OLD, NEW, ref="EXO 1:1", reviewer="r", source="edit")
    (exo / ".bridge").mkdir(parents=True, exist_ok=True)
    (exo / ".bridge" / "lazy-import.json").write_text("{}", encoding="utf-8")
    _link({**books, "exo": exo})
    open_book(bridge, books["gen"])
    assert rpc(bridge, "languageQa.learned.list", projectPath=str(books["gen"]))["fixes"] == []
    (exo / ".bridge" / "lazy-import.json").unlink()
    open_book(bridge, books["gen"])
    assert [f["books"] for f in rpc(bridge, "languageQa.learned.list", projectPath=str(books["gen"]))["fixes"]] == [["exo"]]
