"""Bookmarks and recent chapters (indic-qa's) through settings.get/set: app-level
rows in workspace.sqlite3, validated, deduplicated and capped."""
from bridge_service import BridgeEngine
from tc_ai_bridge.secret_store import AppSettings
from tests.support.projects import call


def place(chapter, verse=None, collection="c1", book="GEN", **extra):
    out = {"collection": collection, "book": book, "chapter": chapter, "label": "", "ts": "2026-10-07T10:00:00Z", **extra}
    if verse is not None:
        out["verse"] = verse
    return out


def engine(tmp_path):
    return BridgeEngine(settings=AppSettings(path=tmp_path / "settings" / "settings.json"))


def test_bookmarks_are_kept_across_restarts_without_repeats(tmp_path):
    bridge = engine(tmp_path)
    assert bridge.get_settings()["bookmarks"] == []
    saved = call(bridge, "settings.set", {"bookmarks": [
        place("3", "16", label="  the gospel  "), place("3", "16", label="again"), place("1", "1", collection="c2")]})
    assert saved["success"], saved
    marks = saved["result"]["bookmarks"]
    assert [(m["collection"], m["book"], m["chapter"], m["verse"]) for m in marks] == [
        ("c1", "gen", "3", "16"), ("c2", "gen", "1", "1")], "a repeat keeps the newest; the book id is lower case"
    assert marks[0]["label"] == "  the gospel  "
    reopened = engine(tmp_path)
    assert reopened.get_settings()["bookmarks"] == marks
    assert not (tmp_path / "settings" / "settings.json").exists() or "bookmarks" not in (
        tmp_path / "settings" / "settings.json").read_text(encoding="utf-8"), "not a secret: kept in the database"


def test_recent_chapters_are_capped_at_twenty_newest_first(tmp_path):
    bridge = engine(tmp_path)
    recent = [place(str(n)) for n in range(1, 31)]
    result = call(bridge, "settings.set", {"recentChapters": recent})["result"]["recentChapters"]
    assert len(result) == 20 and result[0]["chapter"] == "1" and result[-1]["chapter"] == "20"
    assert "verse" not in result[0]


def test_a_place_without_its_reference_is_refused(tmp_path):
    bridge = engine(tmp_path)
    for bad in ({"bookmarks": {}}, {"bookmarks": ["GEN 1:1"]}, {"bookmarks": [place("1")]},
                {"recentChapters": [place("", collection="c1")]}, {"recentChapters": [place("1", collection=None)]}):
        assert not call(bridge, "settings.set", bad)["success"], bad
    assert bridge.get_settings()["bookmarks"] == []
