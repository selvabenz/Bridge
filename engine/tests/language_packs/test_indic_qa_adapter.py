"""The indic-qa adapter's own logic, without the shipped dictionaries:
the snapshot codec, run splitting, synthetic books and the rule catalogue."""
from collections import Counter
from types import SimpleNamespace

import pytest

from tc_ai_bridge.language_packs import PackError, indic_qa_adapter as adapter
from tc_ai_bridge.language_qa import MAX_VERSE_CHARS, lift_inline_usfm


def test_the_snapshot_codec_keeps_every_type_the_checker_relies_on():
    state = {"irv_canon": Counter({"क": 3, "ख": 1}),
             "join_split": Counter({("उस", "ने"): 2}),
             "book_state": {"GEN": {"irv_canon": Counter({"क": 2}), "abbr": Counter()}},
             "names": {"a", "b"}, "pair": ("x", 1), "plain": [1, "two"]}
    back = adapter.decode_state(adapter.encode_state(state))
    assert back == state
    assert type(back["irv_canon"]) is Counter and type(back["book_state"]["GEN"]["irv_canon"]) is Counter
    assert list(back["join_split"]) == [("उस", "ने")]
    assert isinstance(back["names"], set) and back["pair"] == ("x", 1)


def test_the_snapshot_encoding_is_deterministic():
    one = Counter({"b": 1, "a": 2, ("x", "y"): 3})
    two = Counter({("x", "y"): 3, "a": 2, "b": 1})
    assert adapter.encode_state(one) == adapter.encode_state(two)


def test_a_run_ends_where_lifted_markup_was():
    text = "पहला शब्द\\f + \\ft टिप्पणी\\f* दूसरा"
    lifted, _ = lift_inline_usfm(text)
    runs = adapter._runs(lifted, 0, len(lifted.visible))
    assert len(runs) == 2
    first, second = (lifted.visible[a:b] for a, b in runs)
    assert first == "पहला शब्द"
    for a, b in runs:  # each run is one contiguous piece of the raw verse
        assert adapter.LineRef("1", "1", text, lifted, 0).lifted.raw_span(a, b) is not None
    assert second.strip() == "दूसरा"


def _book(chapters):
    return adapter.build_book("GEN", chapters, lift=lift_inline_usfm, max_verse_chars=MAX_VERSE_CHARS)


def test_a_poetry_verse_becomes_one_line_per_physical_line():
    book, refs, _ = _book({"1": {"1": "पहली पंक्ति\n\\q दूसरी पंक्ति", "2": "तीसरा"}})
    verse_lines = [line for line in book.lines if line.style != "c"]
    assert [line.raw for line in verse_lines] == ["पहली पंक्ति", "दूसरी पंक्ति", "तीसरा"]
    assert [line.style for line in verse_lines] == ["v", "q", "v"]
    # The second line starts after the first line's text and its "\n".
    second = next(i for i, line in enumerate(book.lines) if line.raw == "दूसरी पंक्ति")
    assert refs[second].base == len("पहली पंक्ति") + 1
    assert "\n" not in "".join(line.raw for line in book.lines)


def test_chapters_get_a_chapter_line_and_their_own_span():
    book, refs, _ = _book({"1": {"1": "क"}, "3": {"1": "ग"}})
    assert book.lines[0].style == "c" and book.lines[0].chapter == 1
    assert len(book.chapters) == 4  # 0 (front matter), 1, 2 (missing: empty), 3
    lo, hi = book.chapters[2]
    assert hi < lo
    lo, hi = book.chapters[3]
    assert [line.raw for line in book.lines[lo:hi + 1]] == ["", "ग"]


def test_verse_bridges_and_segments_keep_their_key():
    book, refs, _ = _book({"1": {"3-4": "क", "5a": "ख"}})
    lines = {refs[i].verse: book.lines[i] for i in refs}
    assert (lines["3-4"].verse, lines["3-4"].verse_end) == (3, 4)
    assert (lines["5a"].verse, lines["5a"].verse_end) == (5, None)


def test_a_verse_that_cannot_be_lifted_is_left_out():
    book, refs, _ = _book({"1": {"1": "क \\f + \\ft खुला", "2": "ख"}})
    assert [refs[i].verse for i in sorted(refs)] == ["2"]


@pytest.mark.parametrize("group", ["Encoding", "Shape", "Punctuation", "Lexicon", "Consistency", "Grammar", "Style"])
def test_a_default_entry_is_never_inline_and_never_blocks_export(group):
    entry = adapter.default_entry("hi.lex.known-misspelling", group, True)
    assert entry["inline"] is False
    assert (entry["severity"], entry["confidence"]) != ("high", "high")
    assert entry["category"] in adapter.CATEGORIES and entry["layer"] in adapter.LAYERS


def test_unknown_words_are_off_by_default():
    assert adapter.default_entry("ml.lex.unknown", "Lexicon", True)["enabled"] is False


def _profile(rules):
    return SimpleNamespace(RULES=rules, __name__="qa_app.langs.hi")


def test_rule_versions_must_list_exactly_the_profile_rules():
    profile = _profile({"hi.a": ("Lexicon", "A", False, True), "hi.b": ("Shape", "B", True, True)})
    entry = adapter.default_entry("hi.a", "Lexicon", True)
    with pytest.raises(PackError, match="missing \\['hi.b'\\]"):
        adapter._rules(profile, {"hi.a": entry}, "hi-irv")
    with pytest.raises(PackError, match="unknown \\['hi.c'\\]"):
        adapter._rules(profile, {"hi.a": entry, "hi.b": entry, "hi.c": entry}, "hi-irv")


def test_a_high_high_entry_is_refused():
    profile = _profile({"hi.a": ("Lexicon", "A", False, True)})
    entry = {**adapter.default_entry("hi.a", "Lexicon", True), "severity": "high", "confidence": "high"}
    with pytest.raises(PackError, match="blocks export"):
        adapter._rules(profile, {"hi.a": entry}, "hi-irv")


def test_catalogue_rules_never_run_in_the_verse_scan():
    profile = _profile({"hi.a": ("Lexicon", "A", False, True)})
    [rule] = adapter._rules(profile, {"hi.a": adapter.default_entry("hi.a", "Lexicon", True)}, "hi-irv")
    assert (rule.match_type, rule.stage) == ("indic-qa", "book")
    assert rule.message == "A" and rule.version == 1
