"""The reference Bible read from a reviewer's folder (reference_text): an
indic-qa dictionary folder (verses.tsv) or a folder of USFM books, loaded on a
background thread and cached by the folder's fingerprint."""
import gzip
import json

from tc_ai_bridge.language_packs.reference_text import ReferenceHolder, detect, fingerprint

TSV_HEADER = "ref\tbook\tbook_idx\tchapter\tverse\tseg\ttext\n"
USFM = ("\\id RUT\n\\c 1\n\\p\n\\v 1 அந்நாட்களில் \\w பஞ்சம்\\w* உண்டாயிற்று.\\f + \\ft குறிப்பு\\f*\n"
        "\\v 2 அவன் பேர் எலிமெலேக்கு.\n\\c 2\n\\v 1 நகோமிக்கு ஒரு உறவின்முறையான் இருந்தான்.\n")


def tsv_folder(tmp_path):
    folder = tmp_path / "dict"
    folder.mkdir()
    rows = ["RUT mt1\tRUT\t8\t0\t0\tmt1\tரூத்\n", "RUT 1:2\tRUT\t8\t1\t2\tv\tஅவன் பேர் எலிமெலேக்கு.\n",
            "RUT 1:1\tRUT\t8\t1\t1\tv\tநியாயாதிபதிகள் நியாயம் விசாரித்த நாட்களிலே பஞ்சம் உண்டாயிற்று.\n",
            "RUT 1:10\tRUT\t8\t1\t10\tv\tபஞ்சம் பஞ்சம்.\n"]
    (folder / "verses.tsv").write_text(TSV_HEADER + "".join(rows), encoding="utf-8")
    return folder


def test_a_folder_is_tsv_usfm_or_neither(tmp_path):
    assert detect(tsv_folder(tmp_path)) == "tsv"
    usfm = tmp_path / "usfm"
    usfm.mkdir()
    (usfm / "08RUTOV.SFM").write_text(USFM, encoding="utf-8")
    assert detect(usfm) == "usfm"
    (tmp_path / "empty").mkdir()
    assert detect(tmp_path / "empty") is None and detect(tmp_path / "missing") is None


def test_a_tsv_folder_serves_chapters_in_verse_order_and_finds_words(tmp_path):
    holder = ReferenceHolder(tsv_folder(tmp_path), "OV", tmp_path / "cache")
    text, error = holder.wait()
    assert error == "" and text.kind == "tsv"
    assert [v["verse"] for v in text.chapter("rut", "1")] == ["1", "2", "10"], "titles left out, verse 10 after 2"
    hits, total = text.find("பஞ்சம்", limit=2)
    assert total == 3 and len(hits) == 2
    assert hits[0]["snippet"][hits[0]["snippetStart"]:hits[0]["snippetEnd"]] == "பஞ்சம்"


def test_a_usfm_folder_is_read_through_the_importer_with_notes_and_markers_lifted(tmp_path):
    folder = tmp_path / "usfm"
    folder.mkdir()
    (folder / "08RUTOV.SFM").write_text(USFM, encoding="utf-8-sig")  # a BOM must not matter
    (folder / "notes.SFM").write_text("not scripture at all", encoding="utf-8")
    text, error = ReferenceHolder(folder, "OV", tmp_path / "cache").wait()
    assert error == ""
    assert text.chapter("RUT", "1")[0]["text"] == "அந்நாட்களில் பஞ்சம் உண்டாயிற்று."
    assert [v["verse"] for v in text.chapter("RUT", "2")] == ["1"]


def test_the_plain_text_is_cached_by_fingerprint_and_a_changed_folder_rebuilds(tmp_path):
    folder = tsv_folder(tmp_path)
    cache = tmp_path / "cache"
    ReferenceHolder(folder, "OV", cache).wait()
    first = fingerprint(folder, "tsv")
    cached = cache / f"{first}.json.gz"
    assert cached.is_file()
    assert json.loads(gzip.decompress(cached.read_bytes()).decode("utf-8"))["RUT"]["1"]["2"] == "அவன் பேர் எலிமெலேக்கு."
    with (folder / "verses.tsv").open("a", encoding="utf-8") as stream:
        stream.write("RUT 1:3\tRUT\t8\t1\t3\tv\tபுதிது.\n")
    assert fingerprint(folder, "tsv") != first
    text, _ = ReferenceHolder(folder, "OV", cache).wait()
    assert [v["verse"] for v in text.chapter("RUT", "1")] == ["1", "2", "3", "10"]


def test_a_folder_that_cannot_be_read_reports_an_error_never_raises(tmp_path):
    holder = ReferenceHolder(tmp_path / "missing", "OV", tmp_path / "cache")
    text, error = holder.wait()
    assert text is None and "no verses.tsv" in error
    assert holder.state() == (None, error)
