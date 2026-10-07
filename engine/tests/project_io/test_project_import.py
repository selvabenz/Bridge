import json
import shutil
import zipfile
from pathlib import Path

import pytest

import tc_ai_bridge.project_import as project_import
from bridge_service import BridgeEngine
from greek_room_engine.protocol import EngineRequest
from tc_ai_bridge.project_import import (
    ensure_bridge_original_language,
    import_source,
    inspect_import,
    materialize_lazy_project,
)
from tc_ai_bridge.secret_store import AppSettings
from tc_ai_bridge.tc_project import ProjectError, TranslationCoreProject


SIMPLE_USFM = """\\id TIT
\\h Titus
\\toc1 The Letter to Titus
\\c 1
\\p
\\v 1 Paul, a servant of God.
\\v 2 in hope of eternal life.
\\c 2
\\v 1 But speak the things that fit sound doctrine.
"""


def _metadata(**overrides):
    value = {
        "languageId": "eng",
        "languageName": "English",
        "languageDirection": "ltr",
        "projectName": "Titus review",
        "bibleName": "Test Bible",
    }
    value.update(overrides)
    return value


def _call(engine, method, params=None):
    request = EngineRequest(id="import-test", method=method, params=params or {})
    return engine.handle_request(request).to_dict()


def test_inspect_usfm_reports_books_and_missing_language(tmp_path):
    source = tmp_path / "57-TIT.usfm"
    source.write_text(SIMPLE_USFM, encoding="utf-8")

    preview = inspect_import(source)

    assert preview["kind"] == "usfm"
    assert preview["books"] == [{
        "bookId": "tit",
        "bookName": "Titus",
        "sourceFile": str(source.resolve()),
        "verseCount": 3,
        "hasAlignments": False,
    }]
    assert "languageId" in preview["missingFields"]
    assert "languageName" in preview["missingFields"]
    assert preview["metadata"]["bibleName"] == "The Letter to Titus"


def test_import_usfm_creates_tc_compatible_normalized_project(tmp_path):
    source = tmp_path / "57-TIT.sfm"
    source.write_text(SIMPLE_USFM, encoding="utf-8")

    result = import_source(source, tmp_path / "projects", _metadata())
    project_path = Path(result["primaryProjectPath"])
    project = TranslationCoreProject(project_path)

    assert project.summary.target_language == "English"
    assert project.chapters() == ["1", "2"]
    assert project.target_verse_text("1", "1") == "Paul, a servant of God."
    verse = project.load_verse_alignment("1", "1")
    assert len(verse.alignments) == 17
    assert verse.alignments[0].top_words[0].word == "Παῦλος"
    assert all(group.bottom_words == [] for group in verse.alignments)
    assert [token.word for token in verse.word_bank] == ["Paul", "a", "servant", "of", "God"]
    assert (project_path / "tit.usfm").read_bytes() == source.read_bytes()

    manifest = json.loads((project_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["resource"]["name"] == "Test Bible"
    assert manifest["bridge_import"]["resourceIndexStatus"] == "required"
    assert manifest["tc_orig_lang_check_version_wordAlignment"] == "0.34"
    assert manifest["toolsSelectedOwners"]["wordAlignment"] == "unfoldingWord"
    assert manifest["bridge_original_language"]["commit"] == "fc95b2b8aad08bb65ab54628ab685413a1139e97"
    provenance = json.loads((project_path / ".bridge" / "import.json").read_text(encoding="utf-8"))
    assert provenance["capabilities"]["translationNotes"] == "requires-resource-index"
    assert provenance["capabilities"]["wordAlignment"] == "ready-for-alignment"
    assert provenance["alignment"]["generatedSourceTokens"] > 0
    assert provenance["alignment"]["originalLanguageResource"]["license"] == "CC BY-SA 4.0"


def test_backfill_only_changes_empty_bridge_raw_alignment_verses(tmp_path):
    source = tmp_path / "57-TIT.usfm"
    source.write_text(SIMPLE_USFM, encoding="utf-8")
    result = import_source(source, tmp_path / "projects", _metadata())
    project_path = Path(result["primaryProjectPath"])
    chapter_path = project_path / ".apps" / "translationCore" / "alignmentData" / "tit" / "1.json"
    chapter = json.loads(chapter_path.read_text(encoding="utf-8"))
    preserved = chapter["1"]
    chapter["2"]["alignments"] = []
    chapter_path.write_text(json.dumps(chapter, ensure_ascii=False), encoding="utf-8")

    migration = ensure_bridge_original_language(project_path)
    after = json.loads(chapter_path.read_text(encoding="utf-8"))

    assert migration["changedVerses"] == 1
    assert after["1"] == preserved
    assert after["2"]["alignments"]
    assert ensure_bridge_original_language(project_path)["changedVerses"] == 0


def test_backfill_stops_on_pinned_resource_version_mismatch(tmp_path):
    source = tmp_path / "57-TIT.usfm"
    source.write_text(SIMPLE_USFM, encoding="utf-8")
    result = import_source(source, tmp_path / "projects", _metadata())
    project_path = Path(result["primaryProjectPath"])
    chapter_path = project_path / ".apps" / "translationCore" / "alignmentData" / "tit" / "1.json"
    chapter = json.loads(chapter_path.read_text(encoding="utf-8"))
    chapter["2"]["alignments"] = []
    chapter_path.write_text(json.dumps(chapter, ensure_ascii=False), encoding="utf-8")
    manifest_path = project_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["bridge_original_language"]["version"] = "older-version"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    migration = ensure_bridge_original_language(project_path)
    after = json.loads(chapter_path.read_text(encoding="utf-8"))
    after_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert migration["versionMismatch"] is True
    assert migration["changedVerses"] == 0
    assert after["2"]["alignments"] == []
    assert after_manifest["bridge_original_language"]["version"] == "older-version"


def test_backfill_skips_project_when_optional_import_metadata_is_damaged(tmp_path):
    source = tmp_path / "57-TIT.usfm"
    source.write_text(SIMPLE_USFM, encoding="utf-8")
    result = import_source(source, tmp_path / "projects", _metadata())
    project_path = Path(result["primaryProjectPath"])
    import_path = project_path / ".bridge" / "import.json"
    import_path.write_text("{not valid json", encoding="utf-8")

    migration = ensure_bridge_original_language(project_path)

    assert migration["eligible"] is False
    assert migration["changedVerses"] == 0
    assert "Could not read Bridge import metadata" in migration["error"]


def test_import_collection_creates_one_book_project_per_usfm(tmp_path):
    source = tmp_path / "ParatextProject"
    source.mkdir()
    (source / "57TIT.SFM").write_text(SIMPLE_USFM, encoding="utf-8")
    (source / "58PHM.SFM").write_text(
        "\\id PHM\n\\h Philemon\n\\c 1\n\\v 1 Paul, a prisoner of Christ Jesus.\n",
        encoding="utf-8",
    )

    preview = inspect_import(source)
    assert preview["kind"] == "usfmCollection"
    assert {book["bookId"] for book in preview["books"]} == {"tit", "phm"}

    result = import_source(source, tmp_path / "projects", _metadata(projectName="Whole NT"))
    assert {project["bookId"] for project in result["projects"]} == {"tit", "phm"}
    primary = result["projects"][0]
    deferred = result["projects"][1]
    assert primary["lazy"] is False
    assert TranslationCoreProject(primary["path"]).chapters() == ["1", "2"]
    assert deferred["lazy"] is True
    assert (Path(deferred["path"]) / ".bridge" / "lazy-import.json").is_file()

    # A collection must remain usable after the original Paratext/USFM
    # folder is removed; every deferred source was copied into app storage.
    for path in source.iterdir():
        path.unlink()
    source.rmdir()
    assert materialize_lazy_project(deferred["path"]) is True
    assert TranslationCoreProject(deferred["path"]).chapters() == ["1"]
    assert materialize_lazy_project(deferred["path"]) is False

    collection_data = json.loads(
        (Path(primary["path"]) / ".bridge" / "collection.json").read_text(encoding="utf-8")
    )
    assert collection_data["schemaVersion"] == 2
    assert collection_data["collectionId"]
    assert all("path" not in entry and entry["directoryName"] for entry in collection_data["projects"])

    imported_root = tmp_path / "projects"
    moved_root = tmp_path / "moved-projects"
    imported_root.rename(moved_root)
    moved_primary = moved_root / Path(primary["path"]).name
    reopened_collection = project_import.collection_projects(moved_primary)
    assert {Path(item["path"]).parent for item in reopened_collection} == {moved_root.resolve()}


def test_open_deferred_book_restores_collection_after_restart(tmp_path):
    source = tmp_path / "Bible"
    source.mkdir()
    (source / "57TIT.SFM").write_text(SIMPLE_USFM, encoding="utf-8")
    (source / "58PHM.SFM").write_text(
        "\\id PHM\n\\h Philemon\n\\c 1\n\\v 1 Grace to you.\n",
        encoding="utf-8",
    )
    settings = AppSettings(path=tmp_path / "settings.json")
    first_engine = BridgeEngine(settings=settings)
    imported = first_engine.import_project(str(source), _metadata(projectName="Whole NT"))
    deferred = imported["importedProjects"][1]

    restarted = BridgeEngine(settings=AppSettings(path=tmp_path / "settings.json"))
    opened = restarted.open_project(deferred["path"])

    assert opened["bookId"] == "phm"
    assert opened["chapters"] == ["1"]
    assert len(opened["importedProjects"]) == 2
    assert opened["importedProjects"][1]["lazy"] is False


def test_full_bible_import_only_normalizes_primary_book_eagerly(tmp_path, monkeypatch):
    source = tmp_path / "Bible"
    source.mkdir()
    book_ids = list(project_import.BOOK_NAMES)
    for index, book_id in enumerate(book_ids):
        (source / f"{index + 1:02d}-{book_id.upper()}.usfm").write_text(
            f"\\id {book_id.upper()}\n\\c 1\n\\v 1 Text for {book_id}.\n",
            encoding="utf-8",
        )

    real_write = project_import._write_imported_book
    normalized: list[str] = []

    def record_write(project_root, book, metadata):
        normalized.append(book.book_id)
        return real_write(project_root, book, metadata)

    monkeypatch.setattr(project_import, "_write_imported_book", record_write)
    result = project_import.import_source(source, tmp_path / "projects", _metadata(projectName="Bible"))

    assert len(result["projects"]) == 66
    assert normalized == [book_ids[0]]
    assert sum(bool(project["lazy"]) for project in result["projects"]) == 65


def test_basic_usfm3_alignment_is_preserved(tmp_path):
    source = tmp_path / "57-TIT.usfm"
    source.write_text(
        "\\id TIT\n\\h Titus\n\\c 1\n\\v 1 "
        "\\zaln-s |x-strong=\"G39720\" x-lemma=\"Παῦλος\" x-morph=\"Gr,N,,,,,NMS,\" "
        "x-occurrence=\"1\" x-occurrences=\"1\" x-content=\"Παῦλος\"\\*"
        "\\w Paul|x-occurrence=\"1\" x-occurrences=\"1\"\\w*\\zaln-e\\* came.\n",
        encoding="utf-8",
    )

    result = import_source(source, tmp_path / "projects", _metadata())
    project = TranslationCoreProject(result["primaryProjectPath"])
    alignment = project.load_verse_alignment("1", "1")

    assert project.target_verse_text("1", "1") == "Paul came."
    assert len(alignment.alignments) == 1
    assert alignment.alignments[0].top_words[0].word == "Παῦλος"
    assert alignment.alignments[0].bottom_words[0].word == "Paul"
    assert [word.word for word in alignment.word_bank] == ["came"]
    manifest = json.loads((Path(result["primaryProjectPath"]) / "manifest.json").read_text(encoding="utf-8"))
    assert "bridge_original_language" not in manifest
    assert "tc_orig_lang_check_version_wordAlignment" not in manifest


@pytest.mark.parametrize("extension", [".tcore", ".tstudio", ".zip"])
def test_tc_archive_import_preserves_check_indexes(tmp_path, extension):
    source_project = tmp_path / "source-project"
    alignment = source_project / ".apps" / "translationCore" / "alignmentData" / "tit"
    index = source_project / ".apps" / "translationCore" / "index" / "translationNotes" / "tit"
    target = source_project / "tit"
    alignment.mkdir(parents=True)
    index.mkdir(parents=True)
    target.mkdir()
    (source_project / "manifest.json").write_text(json.dumps({
        "project": {"id": "tit", "name": "Titus"},
        "target_language": {"id": "eng", "name": "English", "direction": "ltr"},
        "resource": {"id": "ult", "name": "Unlocked Literal Text"},
    }), encoding="utf-8")
    (alignment / "1.json").write_text('{"1":{"alignments":[],"wordBank":[]}}', encoding="utf-8")
    (target / "1.json").write_text('{"1":"Text"}', encoding="utf-8")
    (index / "grammar.json").write_text('[{"contextId":{"tool":"translationNotes"}}]', encoding="utf-8")

    archive_path = tmp_path / f"project{extension}"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for path in source_project.rglob("*"):
            if path.is_file():
                archive.write(path, Path("inside") / path.relative_to(source_project))

    result = import_source(archive_path, tmp_path / "projects", _metadata())
    imported = Path(result["primaryProjectPath"])
    assert (imported / ".apps" / "translationCore" / "index" / "translationNotes" / "tit" / "grammar.json").exists()


def test_archive_path_traversal_is_rejected(tmp_path):
    archive_path = tmp_path / "unsafe.tcore"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"project": {"id": "tit", "name": "Titus"}}))
        archive.writestr("../escape.txt", "no")

    with pytest.raises(ProjectError, match="unsafe path"):
        import_source(archive_path, tmp_path / "projects", _metadata())


def test_bridge_import_protocol_opens_primary_project(tmp_path):
    source = tmp_path / "TIT.usfm"
    source.write_text(SIMPLE_USFM, encoding="utf-8")
    engine = BridgeEngine(settings=AppSettings(path=tmp_path / "settings.json"))

    preview = _call(engine, "project.inspectImport", {"path": str(source)})
    assert preview["success"] is True
    result = _call(engine, "project.import", {"path": str(source), "metadata": _metadata()})

    assert result["success"] is True
    assert result["result"]["bookId"] == "tit"
    assert result["result"]["targetLanguageId"] == "eng"
    assert result["result"]["projectName"] == "Titus review"
    assert result["result"]["bibleName"] == "Test Bible"
    assert result["result"]["chapters"] == ["1", "2"]
    assert Path(result["result"]["path"]).is_relative_to(tmp_path / "projects")


def test_duplicate_import_is_blocked_until_separate_copy_is_explicit(tmp_path):
    source = tmp_path / "TIT.usfm"
    source.write_text(SIMPLE_USFM, encoding="utf-8")
    engine = BridgeEngine(settings=AppSettings(path=tmp_path / "settings.json"))

    before = _call(engine, "project.inspectImport", {"path": str(source)})
    assert before["result"]["duplicates"]["classification"] == "new"

    first = _call(engine, "project.import", {"path": str(source), "metadata": _metadata()})
    assert first["success"] is True
    after = _call(engine, "project.inspectImport", {"path": str(source)})
    assert after["result"]["duplicates"]["classification"] == "exactDuplicate"
    assert after["result"]["duplicates"]["matches"][0]["projectId"] == first["result"]["projectId"]

    blocked = _call(engine, "project.import", {"path": str(source), "metadata": _metadata()})
    assert blocked["success"] is False
    assert "already been imported" in blocked["error"]["message"]

    separate = _call(engine, "project.import", {
        "path": str(source), "metadata": _metadata(), "allowDuplicate": True,
    })
    assert separate["success"] is True
    assert separate["result"]["path"] != first["result"]["path"]

    listed = _call(engine, "project.list")["result"]["projects"]
    assert len(listed) == 2
    assert {item["projectId"] for item in listed} == {
        first["result"]["projectId"], separate["result"]["projectId"],
    }


def test_changed_source_is_reported_as_possible_overlap_after_metadata_review(tmp_path):
    source = tmp_path / "TIT.usfm"
    source.write_text(SIMPLE_USFM, encoding="utf-8")
    engine = BridgeEngine(settings=AppSettings(path=tmp_path / "settings.json"))
    imported = _call(engine, "project.import", {"path": str(source), "metadata": _metadata()})
    assert imported["success"] is True
    source.write_text(SIMPLE_USFM.replace("eternal life", "everlasting life"), encoding="utf-8")

    initial = _call(engine, "project.inspectImport", {"path": str(source)})["result"]
    reviewed = _call(engine, "project.inspectImport", {
        "path": str(source), "metadata": _metadata(),
    })["result"]

    assert initial["duplicates"]["classification"] == "new"
    assert reviewed["duplicates"]["classification"] == "possibleDuplicate"
    assert reviewed["duplicates"]["matches"][0]["match"] == "possible"


def test_reimporting_one_complete_collection_is_blocked(tmp_path):
    source = tmp_path / "Bible"
    source.mkdir()
    (source / "57TIT.SFM").write_text(SIMPLE_USFM, encoding="utf-8")
    (source / "58PHM.SFM").write_text(
        "\\id PHM\n\\h Philemon\n\\c 1\n\\v 1 Grace to you.\n",
        encoding="utf-8",
    )
    engine = BridgeEngine(settings=AppSettings(path=tmp_path / "settings.json"))

    first = _call(engine, "project.import", {"path": str(source), "metadata": _metadata()})
    assert first["success"] is True
    preview = _call(engine, "project.inspectImport", {"path": str(source), "metadata": _metadata()})

    assert preview["result"]["duplicates"]["classification"] == "exactDuplicate"
    assert preview["result"]["duplicates"]["exactBookCount"] == 2
    assert preview["result"]["duplicates"]["exactMatchGroupId"].startswith("collection:")
    blocked = _call(engine, "project.import", {"path": str(source), "metadata": _metadata()})
    assert blocked["success"] is False
    assert "already been imported" in blocked["error"]["message"]


def test_exact_books_from_separate_projects_do_not_block_collection_import(tmp_path):
    sources = tmp_path / "single-sources"
    sources.mkdir()
    titus = sources / "TIT.usfm"
    philemon = sources / "PHM.usfm"
    titus.write_text(SIMPLE_USFM, encoding="utf-8")
    philemon.write_text(
        "\\id PHM\n\\h Philemon\n\\c 1\n\\v 1 Grace to you.\n",
        encoding="utf-8",
    )
    engine = BridgeEngine(settings=AppSettings(path=tmp_path / "settings.json"))
    assert _call(engine, "project.import", {"path": str(titus), "metadata": _metadata()})["success"] is True
    assert _call(engine, "project.import", {"path": str(philemon), "metadata": _metadata()})["success"] is True

    collection = tmp_path / "collection"
    collection.mkdir()
    shutil.copy2(titus, collection / titus.name)
    shutil.copy2(philemon, collection / philemon.name)
    preview = _call(engine, "project.inspectImport", {
        "path": str(collection), "metadata": _metadata(),
    })["result"]

    assert preview["duplicates"]["classification"] == "partialOverlap"
    assert preview["duplicates"]["exactBookCount"] == 2
    assert preview["duplicates"]["matchingGroupCount"] == 2
    imported = _call(engine, "project.import", {"path": str(collection), "metadata": _metadata()})
    assert imported["success"] is True
    assert len(imported["result"]["importedProjects"]) == 2


def test_forget_removes_registry_entry_without_deleting_project(tmp_path):
    source = tmp_path / "TIT.usfm"
    source.write_text(SIMPLE_USFM, encoding="utf-8")
    engine = BridgeEngine(settings=AppSettings(path=tmp_path / "settings.json"))
    imported = _call(engine, "project.import", {"path": str(source), "metadata": _metadata()})["result"]
    project_path = Path(imported["path"])

    forgotten = _call(engine, "project.forget", {"projectId": imported["projectId"]})

    assert forgotten["result"] == {"forgotten": True}
    assert project_path.is_dir()
    # Managed-project discovery intentionally restores the entry on the next
    # list. Forget is not delete, and app-owned projects remain recoverable.
    listed = _call(engine, "project.list")["result"]["projects"]
    assert [item["projectId"] for item in listed] == [imported["projectId"]]


def test_locate_rejects_a_different_book_for_missing_project(tmp_path):
    source = tmp_path / "TIT.usfm"
    source.write_text(SIMPLE_USFM, encoding="utf-8")
    engine = BridgeEngine(settings=AppSettings(path=tmp_path / "settings.json"))
    imported = _call(engine, "project.import", {"path": str(source), "metadata": _metadata()})["result"]
    moved = tmp_path / "moved-titus"
    Path(imported["path"]).rename(moved)

    wrong = tmp_path / "wrong" / "phm"
    (wrong / "phm").mkdir(parents=True)
    (wrong / "manifest.json").write_text(json.dumps({
        "project": {"id": "phm", "name": "Philemon"},
        "target_language": {"id": "eng", "name": "English"},
    }), encoding="utf-8")
    (wrong / "phm" / "1.json").write_text('{"1": "Grace."}', encoding="utf-8")
    alignment = wrong / ".apps" / "translationCore" / "alignmentData" / "phm"
    alignment.mkdir(parents=True)
    (alignment / "1.json").write_text('{"1": {"alignments": [], "wordBank": []}}', encoding="utf-8")

    result = _call(engine, "project.open", {
        "path": str(wrong), "projectId": imported["projectId"],
    })

    assert result["success"] is False
    assert "missing project is TIT" in result["error"]["message"]


def test_verse_bridge_import_and_check_does_not_crash(tmp_path):
    source = tmp_path / "TIT.usfm"
    source.write_text("\\id TIT\n\\c 1\n\\v 3-4 A bridged verse.\n", encoding="utf-8")
    engine = BridgeEngine(settings=AppSettings(path=tmp_path / "settings.json"))
    imported = _call(engine, "project.import", {"path": str(source), "metadata": _metadata()})
    assert imported["success"] is True

    checked = _call(engine, "verse.runChecks", {"chapter": "1", "verse": "3-4", "checks": ["local"]})
    assert checked["success"] is True


def test_paratext_settings_are_detected_without_modifying_source(tmp_path):
    source = tmp_path / "ParatextProject"
    source.mkdir()
    scripture = source / "57TIT.SFM"
    scripture.write_text(SIMPLE_USFM, encoding="utf-8")
    settings = source / "Settings.xml"
    settings.write_text(
        "<ScriptureText><Name>Community NT</Name><FullName>Community Bible</FullName>"
        "<LanguageIsoCode>ory</LanguageIsoCode><LeftToRight>true</LeftToRight></ScriptureText>",
        encoding="utf-8",
    )
    before = {path.name: path.read_bytes() for path in source.iterdir()}

    preview = inspect_import(source)

    assert preview["kind"] == "paratext"
    assert preview["metadata"]["languageId"] == "ory"
    assert preview["metadata"]["projectName"] == "Community NT"
    assert preview["metadata"]["bibleName"] == "Community Bible"
    assert "languageName" in preview["missingFields"]
    assert {path.name: path.read_bytes() for path in source.iterdir()} == before


def test_utf8_bom_non_latin_txt_import_preserves_text(tmp_path):
    source = tmp_path / "REV.txt"
    source.write_text("\\id REV\n\\h ପ୍ରକାଶିତ\n\\c 1\n\\v 1 ପ୍ରଥମ ପଦ।\n", encoding="utf-8-sig")

    result = import_source(source, tmp_path / "projects", _metadata(
        languageId="ory", languageName="Odia", projectName="Odia Revelation",
    ))
    project = TranslationCoreProject(result["primaryProjectPath"])

    assert project.target_verse_text("1", "1") == "ପ୍ରଥମ ପଦ।"
    assert (Path(result["primaryProjectPath"]) / "rev.usfm").read_bytes() == source.read_bytes()


def test_import_rejects_incomplete_metadata(tmp_path):
    source = tmp_path / "TIT.usfm"
    source.write_text(SIMPLE_USFM, encoding="utf-8")

    with pytest.raises(ProjectError, match="languageId.*languageName"):
        import_source(source, tmp_path / "projects", {"projectName": "Titus", "bibleName": "Bible"})


def test_a_malformed_later_book_is_reported_when_it_is_first_opened(tmp_path):
    """The preview fully parses only a collection's first book (#91: parsing all
    66 through usfmtc is ~11 s). A later book's error therefore surfaces on first
    open -- as a clear ProjectError, and its source is left untouched."""
    source = tmp_path / "Bible"
    source.mkdir()
    (source / "57TIT.SFM").write_text(SIMPLE_USFM, encoding="utf-8")
    broken = "\\id PHM\n\\h Philemon\n\\c 1\n\\v 1 Grace.\n\\v 1 Again.\n"
    (source / "58PHM.SFM").write_text(broken, encoding="utf-8")

    preview = inspect_import(source)
    assert [book["bookId"] for book in preview["books"]] == ["tit", "phm"]
    imported = import_source(source, tmp_path / "out", _metadata(projectName="Two books"))
    deferred = Path(imported["projects"][1]["path"])

    with pytest.raises(ProjectError, match="Duplicate verse 1:1"):
        project_import.materialize_lazy_project(deferred)
    copied = next((deferred / ".bridge").glob("source.*"))
    assert copied.read_text(encoding="utf-8") == broken


def test_import_rejects_duplicate_verse_numbers(tmp_path):
    source = tmp_path / "TIT.usfm"
    source.write_text("\\id TIT\n\\c 1\n\\v 1 First.\n\\v 1 Duplicate.\n", encoding="utf-8")

    with pytest.raises(ProjectError, match="Duplicate verse 1:1"):
        inspect_import(source)


def test_translationcore_folder_import_preserves_project_files(tmp_path):
    source = tmp_path / "existing-tc"
    (source / "tit").mkdir(parents=True)
    (source / "manifest.json").write_text(json.dumps({
        "project": {"id": "tit", "name": "Titus"},
        "target_language": {"id": "eng", "name": "English", "direction": "ltr"},
        "resource": {"id": "ult", "name": "Existing Bible"},
    }), encoding="utf-8")
    (source / "tit" / "1.json").write_text('{"1":"Existing text"}', encoding="utf-8")
    (source / "reviewer-notes.txt").write_text("preserve me", encoding="utf-8")

    result = import_source(source, tmp_path / "projects", _metadata())
    imported = Path(result["primaryProjectPath"])
    project = TranslationCoreProject(imported)

    assert project.target_verse_text("1", "1") == "Existing text"
    assert project.load_verse_alignment("1", "1").word_bank
    assert (imported / "reviewer-notes.txt").read_text(encoding="utf-8") == "preserve me"


HEADING_USFM = """\\id TIT
\\h Titus
\\c 1
\\s Opening greeting
\\p
\\v 1 Paul, a servant of God.
\\v 2 in hope of eternal life.
\\s The work in Crete
\\p
\\v 3 For this cause I left you in Crete.
\\v 4 to Titus, my true child.
\\c 2
\\v 1 But speak the things that fit sound doctrine.
"""


def _import_heading_book(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "TIT.usfm").write_text(HEADING_USFM, encoding="utf-8")
    destination = tmp_path / "projects"
    destination.mkdir()
    import_source(source, destination, _metadata())
    return next(p for p in destination.iterdir() if p.is_dir())


def test_section_headings_are_split_out_of_the_verse_they_trailed(tmp_path):
    """#180: verse content ran from one \\v to the next, so a heading between two
    verses was stored inside the earlier one -- shown as part of it, and its
    words offered as alignable targets an aligner could never close."""
    project = _import_heading_book(tmp_path)
    chapter = json.loads((project / "tit" / "1.json").read_text(encoding="utf-8"))

    assert chapter["2"] == "in hope of eternal life."
    for verse_text in chapter.values():
        assert "\\s" not in verse_text
        assert "The work in Crete" not in verse_text
        assert "Opening greeting" not in verse_text


def test_headings_are_kept_beside_the_chapter_keyed_by_the_verse_they_introduce(tmp_path):
    project = _import_heading_book(tmp_path)
    headings = json.loads((project / "tit" / "1.headings.json").read_text(encoding="utf-8"))

    # The heading after verse 2 introduces verse 3, not verse 2.
    assert headings["3"] == [{"tag": "s", "text": "The work in Crete"}]
    # One before the first \v belongs to verse 1, which the verse loop never sees.
    assert headings["1"] == [{"tag": "s", "text": "Opening greeting"}]


def test_heading_words_never_become_alignable_target_words(tmp_path):
    """The point of the split. A heading is not a translation of any source
    word, so an aligner can never close a gap it creates."""
    project = _import_heading_book(tmp_path)
    alignment = json.loads(
        (project / ".apps" / "translationCore" / "alignmentData" / "tit" / "1.json")
        .read_text(encoding="utf-8")
    )
    words = [
        word.get("word")
        for verse in alignment.values()
        for group in (verse.get("alignments") or [])
        for word in (group.get("bottomWords") or [])
    ]
    assert not any("Crete" in str(w) and "work" in str(w) for w in words)
    for fragment in ("Opening", "greeting"):
        assert fragment not in words


def test_chapter_headings_reader_and_verse_data_expose_them(tmp_path):
    project = _import_heading_book(tmp_path)
    opened = TranslationCoreProject(str(project))
    assert opened.chapter_headings("1")["3"][0]["text"] == "The work in Crete"
    # A chapter with no headings is an empty dict, not an error.
    assert opened.chapter_headings("2") == {}

    engine = BridgeEngine()
    _call(engine, "project.open", {"path": str(project)})
    data = _call(engine, "chapter.verseData", {"chapter": "1"})["result"]
    assert data["headings"]["3"][0]["text"] == "The work in Crete"
    assert _call(engine, "chapter.verseData", {"chapter": "2"})["result"]["headings"] == {}


def test_a_project_without_headings_writes_no_sibling_file(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "TIT.usfm").write_text(SIMPLE_USFM, encoding="utf-8")
    destination = tmp_path / "projects"
    destination.mkdir()
    import_source(source, destination, _metadata())
    project = next(p for p in destination.iterdir() if p.is_dir())
    assert not list((project / "tit").glob("*.headings.json"))
    # And the chapter files are still found: the sibling name must never be
    # mistaken for a chapter (stem "1.headings" is not a digit).
    assert TranslationCoreProject(str(project)).chapters() == ["1", "2"]


def test_an_opened_external_project_is_an_exact_duplicate_of_itself(tmp_path):
    """#130: `project.open` builds PassageSemanticRuntime, which writes under
    `.apps/translationCoreAI/` *after* `ProjectRegistry.register()` snapshotted
    the tree. The project then no longer matched its own fingerprint, so
    `inspectImport` called it a `possibleDuplicate` of itself on
    `bookLanguageBible` rather than an `exactDuplicate` on `sourceFingerprint`.

    That is what made `scripts/smoke_sidecars.py` fail, and why the frozen-sidecar
    smoke ran `continue-on-error` in `release.yml`. The project here is external —
    no `.bridge/import.json` — which is the case that falls through to the tree
    fingerprint.
    """
    from tests.support.workbench import _build_minimal_project

    root = _build_minimal_project(tmp_path / "rut")
    (root / "rut" / "1.json").write_text(json.dumps({"1": "text"}), encoding="utf-8")
    assert not (root / ".bridge" / "import.json").exists(), "fixture must be an external project"

    engine = BridgeEngine(settings=AppSettings(path=tmp_path / "settings.json"))
    assert _call(engine, "project.open", {"path": str(root)})["success"]
    # Opening really does write Bridge state into the project; without that this
    # test would pass even with the bug present.
    assert (root / ".apps" / "translationCoreAI").is_dir()

    duplicates = _call(engine, "project.inspectImport", {"path": str(root)})["result"]["duplicates"]
    assert duplicates["classification"] == "exactDuplicate", duplicates
    assert duplicates["matches"][0]["reason"] == "sourceFingerprint", duplicates["matches"][0]


def test_bridge_private_state_never_changes_a_project_fingerprint(tmp_path):
    """The whole `.apps/translationCoreAI/` tree is excluded, not just
    `passageSemantic/` as #130 proposed: the workbench database changes on every
    decision and `transactions/` on every edit, so either would break the same
    comparison as soon as the project was used."""
    from tc_ai_bridge.project_registry import _tree_fingerprint
    from tests.support.workbench import _build_minimal_project

    root = _build_minimal_project(tmp_path / "rut")
    before = _tree_fingerprint(root)

    state = root / ".apps" / "translationCoreAI"
    for relative in ("passageSemantic/run.json", "bridge-workbench.sqlite3",
                     "transactions/tx-1.json", "backups/backup-1.sqlite3"):
        target = state / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("state that is not the project", encoding="utf-8")

    assert _tree_fingerprint(root) == before

    # A real project file still moves it, or the fingerprint would be useless.
    (root / "rut" / "1.json").write_text(json.dumps({"1": "edited"}), encoding="utf-8")
    assert _tree_fingerprint(root) != before
