"""Shared builders for the passage-semantic stage tests.

`_runtime` had been copy-pasted into `test_meaning_analysis_stage7.py`,
`test_qa_audit_stage8.py` and `test_semantic_location_stage6b.py`. The three
copies had drifted into formatting differences and nothing else: identical
signatures, identical fixture content, differing only in the project-id prefix
that keeps each file's projects apart in the shared semantic database.

That duplication is why #74 phase 4 exists. Each stage file also carried one
protocol test that needed `BridgeEngine`, so every one of them imported the
5,300-line dispatcher, and affected-test selection from any engine module
reached them all. The protocol tests move to `tests/service/`; they need this
builder, and importing it from a sibling test module is the cross-test coupling
phase 1 removed.

Deliberately NOT unified here: `test_target_semantic_inventory_stage6a.py`'s own
`_runtime`. It writes different USFM (a section heading, `\\q1`, different verse
wording) and a project id without `tmp_path.name`. Folding it in would change
what those tests exercise, and this phase moves tests without changing them.
"""
from __future__ import annotations

import json
from pathlib import Path

from tc_ai_bridge.passage_semantic_runtime import PassageSemanticRuntime
from tc_ai_bridge.tc_project import TranslationCoreProject

# Philippians 1:3-6 in Tamil, the fixture passage these stages are written
# against. Was duplicated verbatim in all three files.
TAMIL = {
    "3": "நற்செய்தி உங்களுக்கு அறிவிக்கப்பட்ட நாள் முதல் இதுவரைக்கும் நீங்கள் எங்களோடு ஊழியத்தில் ஐக்கியப்பட்டிருப்பதால்,",
    "4": "நான் பண்ணுகிற ஒவ்வொரு விண்ணப்பத்திலும் உங்கள் அனைவருக்காகவும் எப்பொழுதும் மகிழ்ச்சியோடு ஜெபம் செய்து,",
    "5": "உங்களில் நல்ல செயலைத் தொடங்கினவர் அதை இயேசு கிறிஸ்துவின் நாள் வரை நடத்தி வருவார் என்று நம்பி,",
    "6": "நான் உங்களை நினைக்கும் போதெல்லாம் என் தேவனை ஸ்தோத்திரிக்கிறேன்.",
}


def semantic_runtime(
    tmp_path: Path,
    *,
    project_prefix: str,
    book: str = "PHP",
    language: str = "ta",
    chapters: dict[str, dict[str, str]] | None = None,
) -> PassageSemanticRuntime:
    """A minimal normalized project with a runtime bound to it.

    `project_prefix` is what the three copies actually differed in ("meaning",
    "qa8", "location"): it keeps one file's projects from colliding with
    another's in the shared semantic database, so it stays explicit rather than
    being derived from something that looks stable but is not.
    """
    root = tmp_path / f"{book}-{language}"
    lower = book.lower()
    (root / lower).mkdir(parents=True)
    alignment = root / ".apps" / "translationCore" / "alignmentData" / lower
    alignment.mkdir(parents=True)
    chapters = chapters or {"1": TAMIL}
    (root / "manifest.json").write_text(json.dumps({
        "project": {"id": lower, "name": book}, "target_language": {"id": language},
        "resource": {"id": "test"}, "tc_version": "8",
    }), encoding="utf-8")
    lines = [f"\\id {book}"]
    for chapter, verses in chapters.items():
        (root / lower / f"{chapter}.json").write_text(
            json.dumps(verses, ensure_ascii=False), encoding="utf-8",
        )
        (alignment / f"{chapter}.json").write_text(json.dumps({
            ref: {"alignments": [], "wordBank": []} for ref in verses
        }), encoding="utf-8")
        lines.extend([f"\\c {chapter}", "\\p"])
        lines.extend(f"\\v {ref} OLD IMPORTED" for ref in verses)
    (root / f"{lower}.usfm").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return PassageSemanticRuntime(
        TranslationCoreProject(root), f"{project_prefix}-{book}-{language}-{tmp_path.name}",
    )


def qa8_runtime(tmp_path: Path, **kwargs) -> PassageSemanticRuntime:
    """semantic_runtime with the Stage 8 tests' project prefix ("qa8").

    test_qa_audit_stage8.py and test_analysis_jobs_stage9a4.py both build their
    runtimes this way and import it as `_runtime`, so their call sites read as
    they always did."""
    return semantic_runtime(tmp_path, project_prefix="qa8", **kwargs)
