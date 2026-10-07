"""Learned fixes: a word the reviewer replaced once is offered again wherever
it recurs (indic-qa's editor feature, brought into Bridge).

Recording and offering use the one tokenizer Language QA already has
(language_qa.word_occurrences over the lifted, visible text), never a second
one. Upstream's `checker.single_token_change` does the same job with each
language profile's own `token_re`, which a common-checks or Tamil project does
not have, and two tokenizers would disagree about what "one word" is.

Nothing here writes. The fixes themselves are rows of each book's workbench
(`language_qa_learned_fixes`, tc_project.record_learned_fix). A pass reads the
whole collection's rows merged (`merge_learned_fixes`, DECISIONS 2026-10-07
"shared across the collection") and reports each recurrence as an ordinary
Language QA finding, so decisions, house style, the inline threshold and the
finding cap all apply to it unchanged.
"""
from __future__ import annotations

from typing import Any, Callable

from .language_qa import lift_inline_usfm, word_occurrences

RULE = "learned.replacement"


def merge_learned_fixes(book: str, own: list[dict[str, Any]],
                        siblings: list[tuple[str, list[dict[str, Any]]]]) -> list[dict[str, Any]]:
    """One row per (old, new) over the open book and its siblings' rows.

    - `count` is the net evidence: every use learned in any book, less every
      retraction (typing the word back). A book's own row may be negative.
    - `enabled` is False when any book has the fix forgotten. Forget and
      Restore write every book that holds the fix, so they agree.
    - `firstRef` is the earliest book's, `lastRef`, reviewer and source the
      latest; `books` lists the books with uses, `own` this book's own count.
    Most used first, as tc_project.learned_fixes orders a book's own rows."""
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for owner, rows in [(book, own), *siblings]:
        for row in rows:
            if not isinstance(row, dict) or not row.get("old") or not row.get("new"):
                continue
            key = (str(row["old"]), str(row["new"]))
            count = int(row.get("count") or 0)
            entry = merged.get(key)
            if entry is None:
                entry = merged[key] = {**row, "count": 0, "enabled": True, "books": [], "own": 0,
                                       "_first": "", "_last": ""}
            entry["count"] += count
            entry["enabled"] = entry["enabled"] and bool(row.get("enabled"))
            if owner == book:
                entry["own"] += count
            if count > 0 and owner not in entry["books"]:
                entry["books"].append(owner)
            created, updated = str(row.get("createdAt") or ""), str(row.get("updatedAt") or "")
            if not entry["_first"] or (created and created < entry["_first"]):
                entry["_first"] = created
                entry["firstRef"] = row.get("firstRef", entry.get("firstRef"))
            if updated >= entry["_last"]:
                entry["_last"] = updated
                for field in ("lastRef", "reviewer", "source", "updatedAt"):
                    if field in row:
                        entry[field] = row[field]
    out = []
    for entry in merged.values():
        entry.pop("_first")
        entry.pop("_last")
        entry["books"] = sorted(entry["books"])
        out.append(entry)
    return sorted(out, key=lambda f: (-int(f["count"]), str(f["old"]), str(f["new"])))


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
