"""Learned fixes: a word the reviewer replaced once is offered again wherever
it recurs (indic-qa's editor feature, brought into Bridge).

Recording and offering use the one tokenizer Language QA already has
(language_qa.word_occurrences over the lifted, visible text), never a second
one. Upstream's `checker.single_token_change` does the same job with each
language profile's own `token_re`, which a common-checks or Tamil project does
not have, and two tokenizers would disagree about what "one word" is.

Nothing here writes. The fixes themselves are rows of the workbench's
`language_qa_learned_fixes` (tc_project.record_learned_fix); a pass reads
`tc_project.learned_map()` and reports each recurrence as an ordinary Language
QA finding, so decisions, house style, the inline threshold and the finding
cap all apply to it unchanged.
"""
from __future__ import annotations

from typing import Any, Callable

from .language_qa import lift_inline_usfm, word_occurrences

RULE = "learned.replacement"


def learned_pair(old_raw: str, new_raw: str) -> tuple[str, str] | None:
    """(old word, new word) when the edit replaced exactly one word and changed
    nothing else that is visible: same number of words, one position differs,
    and the text before and after it is identical (no spacing or punctuation
    change). Markup is lifted first, so moving a footnote is not a word change.
    None for every other edit, including one whose markup cannot be lifted."""
    before, _ = lift_inline_usfm(old_raw)
    after, _ = lift_inline_usfm(new_raw)
    if before is None or after is None:
        return None
    old_words, new_words = word_occurrences(before.visible), word_occurrences(after.visible)
    if len(old_words) != len(new_words) or not old_words:
        return None
    changed = [i for i, (a, b) in enumerate(zip(old_words, new_words)) if a[0] != b[0]]
    if len(changed) != 1:
        return None
    (old, old_start, old_end), (new, new_start, new_end) = old_words[changed[0]], new_words[changed[0]]
    if before.visible[:old_start] != after.visible[:new_start] or before.visible[old_end:] != after.visible[new_end:]:
        return None
    return old, new


def learned_findings(book: str, book_text: dict[str, dict[str, Any]], learned: dict[str, list[str]], *,
                     fixes: dict[tuple[str, str], dict[str, Any]], max_verse_chars: int,
                     finding_id: Callable[..., str], rule_fields: Callable[..., dict[str, Any]],
                     suggestion: Callable[..., dict[str, Any]], text_hash: Callable[[str], str],
                     rule_version: str) -> list[dict[str, Any]]:
    """One finding per recurrence of a learned `old` word in the book's verse
    text. `book_text`: chapter -> {verse: raw text}. `learned`: old -> [new,
    ...], most used first (tc_project.learned_map). `fixes`: (old, new) -> the
    stored fix, for the count and last place shown as the rationale."""
    if not learned:
        return []
    out: list[dict[str, Any]] = []
    for chapter in sorted(book_text, key=lambda c: (int(c) if c.isdigit() else 0, c)):
        verses = book_text[chapter]
        for verse, raw in verses.items():
            if not isinstance(raw, str) or not raw or len(raw) > max_verse_chars or verse == "front":
                continue
            lifted, _ = lift_inline_usfm(raw)
            if lifted is None:
                continue
            occurrence: dict[str, int] = {}
            for word, start, end in word_occurrences(lifted.visible):
                forms = learned.get(word)
                if not forms:
                    continue
                span = lifted.raw_span(start, end)
                if span is None:
                    continue
                original = raw[span[0]:span[1]]
                n = occurrence.get(word, 0)
                occurrence[word] = n + 1
                suggestions = []
                for form in forms:
                    fix = fixes.get((word, form), {})
                    count, last = int(fix.get("count") or 0), str(fix.get("lastRef") or "")
                    rationale = f"Replaced with this {count}× before" + (f", last at {last}" if last else "") + "."
                    suggestions.append(suggestion(form, "learned", rationale))
                out.append({
                    "id": finding_id(book, chapter, str(verse), RULE, word, n),
                    "book": book, "chapter": chapter, "verse": str(verse), "rule": RULE,
                    "severity": "low", "start": span[0], "end": span[1], "originalText": original,
                    "message": f'"{word}" was replaced with "{forms[0]}" before.',
                    "textHash": text_hash(raw), "ruleVersion": rule_version, "status": "review-needed",
                    **rule_fields(RULE, suggestions),
                })
    return out


__all__ = ["RULE", "learned_findings", "learned_pair"]
