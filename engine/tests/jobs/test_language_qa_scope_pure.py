"""Scoped corrections on their own (language_qa_scope): which findings count as
the same one, what a scope covers, and the right-to-left splice. No I/O."""
import pytest

from tc_ai_bridge.language_qa_scope import Occurrence, match_key, occurrences, splice


def finding(fid, chapter, verse, start, text, *, rule="hi-irv/hi.lex.known-misspelling", category="typo",
            suggestion="new", context=None):
    return {"id": fid, "chapter": chapter, "verse": verse, "start": start, "end": start + len(text),
            "originalText": text, "ruleId": rule, "category": category, "textHash": f"h{chapter}{verse}",
            "suggestions": [{"text": suggestion}] if suggestion else [], **({"context": context} if context else {})}


BOOK = [finding("a", "1", "1", 0, "x"), finding("b", "1", "2", 4, "x"), finding("c", "2", "1", 0, "x"),
        finding("d", "1", "1", 9, "y"), finding("e", "1", "10", 0, "x"), finding("h", "1", "1", 0, "x", context="heading")]


def ids(found):
    return [o.finding_id for o in found]


def test_scope_covers_the_verse_the_chapter_or_the_book_in_reading_order():
    origin = BOOK[0]
    assert ids(occurrences(BOOK, origin, "verse")) == ["a"]
    assert ids(occurrences(BOOK, origin, "chapter")) == ["a", "b", "e"]
    assert ids(occurrences(BOOK, origin, "book")) == ["a", "b", "e", "c"]


def test_a_word_matches_on_rule_and_text_a_warning_on_rule_alone():
    word = finding("w", "1", "1", 0, "x")
    assert match_key(word) != match_key(finding("w2", "1", "1", 0, "y"))
    assert match_key(word) != match_key(finding("w3", "1", "1", 0, "x", rule="hi-irv/hi.shape.errors"))
    spaces = [finding("s1", "1", "1", 3, "  ", rule="common/spacing.extra", category="spacing", suggestion=" "),
              finding("s2", "1", "2", 7, "   ", rule="common/spacing.extra", category="spacing", suggestion=" ")]
    assert ids(occurrences(spaces, spaces[0], "chapter")) == ["s1", "s2"]


def test_a_chosen_suggestion_applies_to_words_and_a_warning_keeps_its_own_fix():
    assert [o.new for o in occurrences(BOOK, BOOK[0], "chapter", "z")] == ["z", "z", "z"]
    space = finding("s", "1", "1", 3, "  ", rule="common/spacing.extra", category="spacing", suggestion=" ")
    assert occurrences([space], space, "verse", "ignored")[0].new == " "


def test_a_heading_finding_cannot_be_corrected_from_here():
    with pytest.raises(ValueError):
        occurrences(BOOK, BOOK[-1], "book")
    with pytest.raises(ValueError):
        occurrences(BOOK, BOOK[0], "testament")


def occ(start, old, new):
    return Occurrence("1", "1", f"f{start}", start, start + len(old), old, new, "h", {})


def test_splice_applies_right_to_left_so_every_offset_stays_valid():
    assert splice("ab xx cd xx", [occ(3, "xx", "yyy"), occ(9, "xx", "z")]) == "ab yyy cd z"


def test_splice_refuses_overlaps_and_text_that_is_not_what_the_pass_saw():
    with pytest.raises(ValueError):
        splice("abcdef", [occ(0, "abc", "x"), occ(2, "cde", "y")])
    with pytest.raises(ValueError):
        splice("abcdef", [occ(0, "abd", "x")])
