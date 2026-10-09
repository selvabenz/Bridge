"""#240: translationcore_check_issues reads a verse's tC check state once and
answers every check's staleness from it. check_staleness(state=...) must give
the same answer as the per-check glob it replaces, in every case that rule
distinguishes; and the issues it produces are unchanged."""
from __future__ import annotations

import pytest

from tc_ai_bridge.local_checks import translationcore_check_issues
from tc_ai_bridge.tc_project import TranslationCoreProject
from tests.support.alignment_books import unaligned_verse, write_alignment_book
from tests.support.tc_checks import check_entry, state_record, write_index, write_state

TN, TW = "translationNotes", "translationWords"


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "php"
    write_alignment_book(root, "php", {"1": {
        "1": unaligned_verse("அவன் வந்தான்", [("ἦλθεν", "G20640")]),
        "2": unaligned_verse("அவள் போனாள்", [("ἀπῆλθεν", "G05650")]),
    }})
    write_index(root, "php", TN, "figs-metaphor", [
        check_entry("php", "1", "1", TN, "figs-metaphor", "current-1"),
        check_entry("php", "1", "1", TN, "figs-metaphor", "stale-1", selections=[{"text": "அவன்"}]),
        check_entry("php", "1", "1", TN, "figs-metaphor", "pending-1"),
        check_entry("php", "1", "1", TN, "figs-metaphor", "ts-only"),
        check_entry("php", "1", "2", TN, "figs-metaphor", "no-edits"),
    ])
    write_index(root, "php", TW, "kt", [check_entry("php", "1", "1", TW, "kt", "tie")])
    # current-1: selected after the last verse edit.
    write_state(root, "php", "selections", "1", "1",
                state_record("php", "1", "1", TN, "figs-metaphor", "current-1", modified="2026-03-01T00:00:00Z"),
                "2026-03-01T00_00_00Z")
    # stale-1: two selections, both before the edit; the later one counts.
    for ts in ("2026-01-01T00:00:00Z", "2026-01-15T00:00:00Z"):
        write_state(root, "php", "selections", "1", "1",
                    state_record("php", "1", "1", TN, "figs-metaphor", "stale-1", modified=ts), ts.replace(":", "_"))
    # ts-only: a record with `timestamp` but no `modifiedTimestamp`.
    write_state(root, "php", "selections", "1", "1",
                state_record("php", "1", "1", TN, "figs-metaphor", "ts-only", timestamp="2026-04-01T00:00:00Z"),
                "zz-ts-only")
    # tie: equal timestamps in two files, and a record whose tool and group are blank.
    for name in ("a-tie", "b-tie"):
        write_state(root, "php", "selections", "1", "1",
                    state_record("php", "1", "1", TW, "kt", "tie", modified="2026-02-01T00:00:00Z", note=name), name)
    write_state(root, "php", "selections", "1", "1",
                state_record("php", "1", "1", "", "", "tie", modified="2026-05-01T00:00:00Z"), "blank-tool")
    # One verse edit on 1:1, between the stale and the current selections.
    write_state(root, "php", "verseEdits", "1", "1",
                {"modifiedTimestamp": "2026-02-15T00:00:00Z", "verseBefore": "a", "verseAfter": "b"},
                "2026-02-15T00_00_00Z")
    write_state(root, "php", "comments", "1", "1",
                state_record("php", "1", "1", TN, "figs-metaphor", "current-1", text="note"), "c1")
    # 1:2: a selection and no verse edits at all.
    write_state(root, "php", "selections", "1", "2",
                state_record("php", "1", "2", TN, "figs-metaphor", "no-edits", modified="2026-01-01T00:00:00Z"), "s")
    return TranslationCoreProject(root)


CASES = [
    ("1", "1", "current-1", TN, "figs-metaphor"),
    ("1", "1", "stale-1", TN, "figs-metaphor"),
    ("1", "1", "pending-1", TN, "figs-metaphor"),
    ("1", "1", "ts-only", TN, "figs-metaphor"),
    ("1", "1", "tie", TW, "kt"),
    ("1", "1", "tie", "", ""),
    ("1", "1", "tie", TW, ""),
    ("1", "2", "no-edits", TN, "figs-metaphor"),
    ("1", "2", "missing", TN, "figs-metaphor"),
]


@pytest.mark.parametrize("chapter,verse,check_id,tool,group", CASES)
def test_staleness_from_the_read_state_matches_the_per_check_glob(project, chapter, verse, check_id, tool, group):
    state = project.check_state_for_verse(chapter, verse)
    assert (project.check_staleness(chapter, verse, check_id, tool, group, state=state)
            == project.check_staleness(chapter, verse, check_id, tool, group))


def test_the_cases_cover_every_answer(project):
    answers = {project.check_staleness(c, v, k, t, g) for c, v, k, t, g in CASES}
    assert answers == {"current", "stale", "pending"}


def test_each_state_directory_is_listed_once_per_verse(project, monkeypatch):
    listed: list[str] = []
    real = project._state_files_for_verse

    def counting(kind, chapter, verse):
        listed.append(kind)
        return real(kind, chapter, verse)

    monkeypatch.setattr(project, "_state_files_for_verse", counting)
    translationcore_check_issues(project, "1", "1")
    assert sorted(listed) == ["comments", "invalidated", "selections", "verseEdits"]


def test_the_issues_are_unchanged(project):
    issues = [(i.code, i.severity, i.title, i.detail, i.source, i.check_id, i.group_id)
              for i in translationcore_check_issues(project, "1", "1")]
    # Recorded from the code before #240, unchanged by it.
    assert issues == [
        ("TC_PENDING", "medium", "translationNotes: unchecked item",
         "figs-metaphor / current-1 has no selection yet.", "translationCore", "current-1", "figs-metaphor"),
        ("TC_STALE_AFTER_EDIT", "high", "translationNotes: recheck required",
         "figs-metaphor / stale-1 is stale after a later Scripture edit.", "translationCore", "stale-1",
         "figs-metaphor"),
        ("TC_PENDING", "medium", "translationNotes: unchecked item",
         "figs-metaphor / pending-1 has no selection yet.", "translationCore", "pending-1", "figs-metaphor"),
        ("TC_STALE_AFTER_EDIT", "high", "translationNotes: recheck required",
         "figs-metaphor / ts-only is stale after a later Scripture edit.", "translationCore", "ts-only",
         "figs-metaphor"),
        ("TC_STALE_AFTER_EDIT", "high", "translationWords: recheck required",
         "kt / tie is stale after a later Scripture edit.", "translationCore", "tie", "kt"),
        ("TC_COMMENTS", "info", "Reviewer comments present",
         "1 translationCore comment(s) exist for this verse.", "translationCore", "", ""),
        ("TC_VERSE_EDITS", "info", "Verse edit history present",
         "1 translationCore verse edit record(s) exist.", "translationCore", "", ""),
    ]
