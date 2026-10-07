"""verse.history and chapter.verseData's editCounts through the real
dispatcher: the native verseEdits records every Scripture edit writes, read
back newest first for one verse, a chapter or the book."""
import time

from bridge_service import BridgeEngine
from tests.support.projects import call, fixture_project  # noqa: F401  (a fixture)


def rpc(engine, method, **params):
    response = call(engine, method, params)
    assert response["success"], response
    return response["result"]


def edit(engine, text):
    rpc(engine, "verse.edit", chapter="1", verse="1", newText=text)
    # Records are named by millisecond timestamp; keep two edits apart.
    time.sleep(0.01)


def test_history_lists_each_edit_newest_first_with_both_texts(fixture_project):
    engine = BridgeEngine()
    rpc(engine, "project.open", path=str(fixture_project))
    original = rpc(engine, "verse.get", chapter="1", verse="1")["text"]
    edit(engine, "முதல் மாற்றம்")
    edit(engine, "இரண்டாம் \\nd மாற்றம்\\nd*")

    history = rpc(engine, "verse.history", chapter="1", verse="1")
    assert history["total"] == 2 and not history["truncated"]
    newest, oldest = history["entries"]
    assert newest["verseBefore"] == "முதல் மாற்றம்"
    assert newest["verseAfter"] == "இரண்டாம் \\nd மாற்றம்\\nd*"
    assert newest["plainAfter"] == "இரண்டாம் மாற்றம்", "the diff gets visible text"
    assert oldest["verseBefore"] == original
    assert newest["timestamp"] > oldest["timestamp"]
    assert newest["groupId"] == "human-scripture-edit" and newest["batchId"] is None
    assert (newest["chapter"], newest["verse"]) == ("1", "1")


def test_verse_data_counts_edits_and_the_book_history_covers_every_verse(fixture_project):
    engine = BridgeEngine()
    rpc(engine, "project.open", path=str(fixture_project))
    assert rpc(engine, "chapter.verseData", chapter="1")["editCounts"] == {}
    edit(engine, "ஒன்று")
    edit(engine, "இரண்டு")
    assert rpc(engine, "chapter.verseData", chapter="1")["editCounts"] == {"1": 2}
    assert rpc(engine, "verse.history")["total"] == 2
    assert rpc(engine, "verse.history", chapter="1")["total"] == 2
    limited = rpc(engine, "verse.history", limit=1)
    assert len(limited["entries"]) == 1 and limited["truncated"] is True


def test_history_checks_its_parameters(fixture_project):
    engine = BridgeEngine()
    rpc(engine, "project.open", path=str(fixture_project))
    for params in ({"verse": "1"}, {"chapter": 1}, {"limit": 0}, {"limit": True}):
        assert not call(engine, "verse.history", params)["success"], params
