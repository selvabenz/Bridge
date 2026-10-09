"""scripts/build_corpus_lexicon.py, without the network (#246)."""
from __future__ import annotations

import gzip
import io
import tarfile
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import build_corpus_lexicon as build
from tc_ai_bridge.language_packs import indic_qa_vendor


@pytest.fixture(scope="module")
def tamil():
    return build.tokenizer("ta")


def test_count_tokens_drops_the_line_cut_by_each_window_edge(tmp_path, tamil):
    findall, _key, _entry = tamil
    path = tmp_path / "w.part"
    path.write_bytes("ன் வந்தான்\nஅவன் வந்தான்\nஅவன் போ".encode("utf-8"))  # bytes: no newline translation
    counts, used, tokens = build.count_tokens(path, findall, first_line_partial=True, last_line_partial=True)
    assert counts == Counter({"அவன்": 1, "வந்தான்": 1}) and tokens == 2
    assert used == len("அவன் வந்தான்\n".encode("utf-8"))
    # A window that starts the file keeps its first line; one that ends it keeps its last.
    counts, _used, _tokens = build.count_tokens(path, findall, first_line_partial=False, last_line_partial=False)
    assert counts == Counter({"ன்": 1, "வந்தான்": 2, "அவன்": 2, "போ": 1})


def test_count_tokens_normalises_to_nfc_and_ignores_other_scripts(tmp_path, tamil):
    findall, _key, _entry = tamil
    path = tmp_path / "w.part"
    path.write_bytes("கோ hello கொ 123\n".encode("utf-8"))  # கோ as base + two signs (NFD-ish)
    counts, _used, _tokens = build.count_tokens(path, findall, first_line_partial=False, last_line_partial=False)
    assert set(counts) == {"கோ", "கொ"}


def test_keys_use_the_profiles_canon_for_hindi_and_nfc_only_for_tamil():
    _findall, key, entry = build.tokenizer("hi")
    assert key("परमेश्‍वर") == "परमेश्वर" and entry["canon"] == "hi.canon"
    _findall, key, entry = build.tokenizer("ta")
    assert key("கோ") == "கோ" and entry["canon"] == "nfc"
    assert entry["tokenRe"] == indic_qa_vendor.modules().langs.get("ta").token_re.pattern
    assert build.fold_keys(Counter({"a": 2, "b": 3}), lambda w: "x") == Counter({"x": 5})


def test_list_words_are_known_at_accept_min_but_never_suggestible():
    rows = build.merge(Counter({"common": 500, "rare": 3, "listed": 4}),
                       {"h": {"listed", "only"}}, floor=10, accept_min=10, cap=10, forms={"common": "Common"})
    assert rows == [("common", 500, "c", "Common"), ("listed", 10, "h", ""), ("only", 10, "h", "")]
    assert build.merge(Counter({"a": 20, "b": 20, "c": 20}), {}, floor=10, accept_min=10, cap=2) == [
        ("a", 20, "c", ""), ("b", 20, "c", "")]


def test_each_key_keeps_its_commonest_spelling_when_the_key_is_not_one():
    # Malayalam canon writes a final ு as ്: the key ഒര് is not a spelling.
    _findall, key, _entry = build.tokenizer("ml")
    counts = Counter({"ഒരു": 900, "ഒര്": 40, "പറഞ്ഞു": 300})
    forms = build.surface_forms(counts, key)
    assert forms[key("ഒരു")] == "ഒരു"
    assert key("ഒരു") != "ഒരു" and key("പറഞ്ഞു") in forms
    # A key that is its own commonest spelling carries none.
    _findall, ta_key, _entry = build.tokenizer("ta")
    assert build.surface_forms(Counter({"ஒரு": 5}), ta_key) == {}


def test_output_is_byte_deterministic_and_round_trips(tmp_path):
    rows = [("ஒரு", 100, "ck", ""), ("അവര്", 10, "c", "അവരു")]
    build.write_tsv_gz(tmp_path / "a.tsv.gz", rows)
    build.write_tsv_gz(tmp_path / "b.tsv.gz", rows)
    assert (tmp_path / "a.tsv.gz").read_bytes() == (tmp_path / "b.tsv.gz").read_bytes()
    assert build.read_tsv_gz(tmp_path / "a.tsv.gz") == rows
    assert gzip.decompress((tmp_path / "a.tsv.gz").read_bytes()).startswith(b"#key\tcount\tsrc\tform\n")


def _tar(path, members):
    with tarfile.open(path, "w:bz2") as archive:
        for name, text in members.items():
            data = text.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))


def test_the_tar_is_read_without_extracting_a_junk_member_is_skipped_and_the_cap_holds(tmp_path, tamil):
    findall, _key, _entry = tamil
    tar = tmp_path / "words.tar.bz2"
    _tar(tar, {
        "w/good.csv": "ஒரு,30\nஅவன்,25\nவந்தான்,19\nhello,99\nஅவன்,1\n",
        "w/pairs.html": "கள,35693\nடர,14290\nஅழ,13424\nஎன,11929\nதம,11478\nதன,9712\nணன,9615\n",
    })
    counts, report = build.kaniyam_counts(tar, findall, floor=20)
    assert counts == Counter({"ஒரு": 30, "அவன்": 26})
    assert report["skipped"] == ["w/pairs.html"] and report["members"][0]["kept"] == 4
    assert not list(tmp_path.glob("w/*"))
    with pytest.raises(ValueError):
        build.kaniyam_counts(tar, findall, floor=1, cap_bytes=10)


def test_hunspell_words_strip_flags_and_keep_whole_tokens(tmp_path):
    findall, _key, _entry = build.tokenizer("hi")
    path = tmp_path / "hi_IN.txt"
    path.write_text("मनुष्य/A\nपरमेश्वर\nhello\n\nदो शब्द\n", encoding="utf-8")
    assert build.hunspell_words(path, findall) == {"मनुष्य", "परमेश्वर"}


def test_window_ranges_are_evenly_spaced_and_clamped():
    assert build.window_ranges(100, 1000, 8) == [(0, 100)]
    assert build.window_ranges(1000, 100, 1) == [(0, 100)]
    ranges = build.window_ranges(1000, 400, 4)
    assert ranges == [(0, 100), (300, 400), (600, 700), (900, 1000)]
    assert sum(end - start for start, end in ranges) == 400


class _RangeServer(BaseHTTPRequestHandler):
    body = b"0123456789" * 50
    seen: list[str] = []

    def do_GET(self):  # noqa: N802
        header = self.headers.get("Range", "")
        self.seen.append(header)
        start, end = header.removeprefix("bytes=").split("-")
        start, end = int(start), int(end)
        chunk = self.body[start:end + 1]
        self.send_response(206)
        self.send_header("Content-Range", f"bytes {start}-{end}/{len(self.body)}")
        self.send_header("Content-Length", str(len(chunk)))
        self.end_headers()
        self.wfile.write(chunk)

    def log_message(self, *_args):
        pass


def test_fetch_range_resumes_from_a_partial_file(tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RangeServer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/file.txt"
        dest = tmp_path / "file.100-300.part"
        dest.write_bytes(_RangeServer.body[100:140])
        digest = build.fetch_range(url, 100, 300, dest)
        assert dest.read_bytes() == _RangeServer.body[100:300]
        assert _RangeServer.seen[-1] == "bytes=140-299"
        assert digest == build.sha256_of(dest)
        # Complete already: no request at all, even offline.
        assert build.fetch_range(url, 100, 300, dest, offline=True) == digest
    finally:
        server.shutdown()
