"""#239: TranslationCoreProject's read-only accessors serve chapter files from a
cache checked against the file on every read. A file is parsed once while it is
unchanged; any write -- Bridge's own, a rollback, or another program's -- is
read next time; and the loaders writers use still return a fresh parse."""
from __future__ import annotations

import json
import os

import pytest

from tc_ai_bridge import tc_project
from tc_ai_bridge.models import VerseAlignment
from tc_ai_bridge.tc_project import TranslationCoreProject
from tests.support.alignment_books import unaligned_verse, write_alignment_book


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "php"
    write_alignment_book(root, "php", {
        "1": {
            "1": unaligned_verse("அவன் வந்தான்", [("ἦλθεν", "G20640")]),
            "2": unaligned_verse("அவள் போனாள்", [("ἀπῆλθεν", "G05650")]),
            "3": unaligned_verse("அவர்கள் இருந்தார்கள்", [("ἦσαν", "G15100")]),
        },
        "2": {"1": unaligned_verse("இரண்டாம் அதிகாரம்", [("δεύτερον", "G12080")])},
    })
    return TranslationCoreProject(root)


@pytest.fixture
def parses(monkeypatch):
    """Paths parsed through tc_project._read_json, in order."""
    seen: list[str] = []
    real = tc_project._read_json

    def counting(path):
        seen.append(path.name if path.parent.name != "php" else f"{path.parent.parent.name}:{path.name}")
        return real(path)

    monkeypatch.setattr(tc_project, "_read_json", counting)
    return seen


def _rewrite(path, data):
    """Another program's write: a new file through os.replace, as tC does."""
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)


def test_each_chapter_file_is_parsed_once_while_it_is_unchanged(project, parses):
    for verse in ("1", "2", "3"):
        project.load_verse_alignment("1", verse)
        project.target_verse_text("1", verse)
        project.verses("1")
    assert len(parses) == 2  # the alignment chapter and the target chapter, once each
    assert project.target_verse_text("1", "2") == "அவள் போனாள்"
    assert project.verses("1") == ["1", "2", "3"]


def test_a_file_rewritten_by_another_program_is_read_on_the_next_access(project):
    assert project.target_verse_text("1", "1") == "அவன் வந்தான்"
    target = project.book_dir / "1.json"
    # Same length, so only the file id and the mtime tell it apart.
    _rewrite(target, {"1": "அவன் போனான்", "2": "அவள் போனாள்", "3": "அவர்கள் இருந்தார்கள்"})
    assert project.target_verse_text("1", "1") == "அவன் போனான்"

    alignment = project.chapter_path("1")
    data = json.loads(alignment.read_text(encoding="utf-8"))
    del data["3"]
    _rewrite(alignment, data)
    assert project.verses("1") == ["1", "2"]


def test_a_write_in_place_with_a_new_mtime_is_read_too(project):
    assert project.target_verse_text("1", "1") == "அவன் வந்தான்"
    target = project.book_dir / "1.json"
    stat = target.stat()
    target.write_text(json.dumps({"1": "அவன் போனான்"}, ensure_ascii=False), encoding="utf-8")
    os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    assert project.target_verse_text("1", "1") == "அவன் போனான்"


def test_bridge_writes_are_read_back(project):
    project.verses("1")
    assert project.target_verse_text("1", "1") == "அவன் வந்தான்"
    project.apply_scripture_edit("1", "1", "அவன் உடனே வந்தான்")
    assert project.target_verse_text("1", "1") == "அவன் உடனே வந்தான்"

    alignment = project.load_verse_alignment("1", "2").to_dict()
    alignment["wordBank"] = alignment["wordBank"][:1]
    project.save_verse_alignment("1", "2", VerseAlignment.from_dict(alignment))
    assert project.load_verse_alignment("1", "2").to_dict()["wordBank"] == alignment["wordBank"]


def test_a_rolled_back_write_leaves_the_accessors_equal_to_disk(project, monkeypatch):
    before = project.load_verse_alignment("1", "2").to_dict()
    changed = json.loads(json.dumps(before))
    changed["wordBank"] = []

    def fail(*_args, **_kwargs):
        raise OSError("simulated history write failure")

    monkeypatch.setattr(project, "_record_alignment_history", fail)
    with pytest.raises(OSError):
        project.save_verse_alignment("1", "2", VerseAlignment.from_dict(changed))
    on_disk = json.loads(project.chapter_path("1").read_text(encoding="utf-8"))["2"]
    assert project.load_verse_alignment("1", "2").to_dict() == before
    assert on_disk["wordBank"] == before["wordBank"]


def test_the_writer_loaders_return_a_fresh_mutable_parse(project):
    project.load_verse_alignment("1", "1")
    project.target_verse_text("1", "1")
    chapter = project.load_alignment_chapter("1")
    chapter["1"]["wordBank"] = []
    target = project.target_chapter("1")
    target["1"] = "changed in memory only"
    assert project.load_verse_alignment("1", "1").to_dict()["wordBank"] != []
    assert project.target_verse_text("1", "1") == "அவன் வந்தான்"


def test_a_new_or_removed_chapter_shows_in_chapters(project):
    assert project.chapters() == ["1", "2"]
    (project.chapter_path("3")).write_text(json.dumps({"1": {"alignments": [], "wordBank": []}}), encoding="utf-8")
    assert project.chapters() == ["1", "2", "3"]
    project.chapter_path("2").unlink()
    assert project.chapters() == ["1", "3"]


def test_errors_are_unchanged(project):
    with pytest.raises(tc_project.ProjectError, match="Missing alignment chapter"):
        project.verses("9")
    with pytest.raises(tc_project.ProjectError, match="No alignment data for"):
        project.load_verse_alignment("1", "99")
    project.chapter_path("2").write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(tc_project.ProjectError, match="Invalid alignment chapter JSON"):
        project.verses("2")
    assert project.target_verse_text("7", "1") == ""
