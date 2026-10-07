"""Scoped corrections: accept or ignore the same Language QA finding in this
verse, this chapter or this book (indic-qa's editor "Here / Chapter / Book",
DECISIONS 2026-10-07).

Pure functions over the findings of the last pass, no I/O. What counts as "the
same finding" is decided here, so the confirmation the reviewer sees and the
change that is written can never disagree:

- a word-like finding (typo, consistency, grammar, sandhi, name, termbase,
  learned ...) matches another of the same rule (and indic-qa sub-rule) on the
  same NFC text;
- a warning (spacing, punctuation, unicode, usfm) matches another of the same
  rule and sub-rule, whatever its text: a double space is a double space.

Only findings the last pass reported are ever touched -- never a free text
search over Scripture -- and never a heading or footnote finding (they have no
verse text to correct here). Writing is the caller's job: one
apply_scripture_edit per verse, never more than one writer.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable

SCOPES = ("verse", "chapter", "book")
WARNING_CATEGORIES = frozenset({"spacing", "punctuation", "unicode", "usfm"})


@dataclass(frozen=True)
class Occurrence:
    chapter: str
    verse: str
    finding_id: str
    start: int
    end: int
    old: str
    new: str | None          # None: nothing to write here (no suggestion)
    text_hash: str           # the verse text the pass saw; a mismatch means it changed since
    finding: dict[str, Any]  # the finding itself, for its decision

    def to_json(self) -> dict[str, Any]:
        return {"chapter": self.chapter, "verse": self.verse, "findingId": self.finding_id,
                "start": self.start, "end": self.end, "old": self.old, "new": self.new}


def is_warning(finding: dict[str, Any]) -> bool:
    return str(finding.get("category") or "") in WARNING_CATEGORIES


def match_key(finding: dict[str, Any]) -> tuple[str, ...]:
    """What makes two findings "the same finding" for a scoped action."""
    rule = (str(finding.get("ruleId") or ""), str(finding.get("detailRule") or ""))
    if is_warning(finding):
        return ("warning", *rule)
    return ("word", *rule, unicodedata.normalize("NFC", str(finding.get("originalText") or "")))


def correctable(finding: dict[str, Any]) -> bool:
    """A finding in the verse text itself (not a heading or a footnote)."""
    return not finding.get("context")


def _in_scope(finding: dict[str, Any], origin: dict[str, Any], scope: str) -> bool:
    if scope == "book":
        return True
    if str(finding.get("chapter")) != str(origin.get("chapter")):
        return False
    return scope == "chapter" or str(finding.get("verse")) == str(origin.get("verse"))


def occurrences(findings: Iterable[dict[str, Any]], origin: dict[str, Any], scope: str,
                suggestion: str | None = None) -> list[Occurrence]:
    """Every finding of the last pass in `scope` that matches `origin`, in
    reading order. `suggestion`: the replacement the reviewer chose for a
    word-like finding; a warning (or no choice) uses each occurrence's own
    first suggestion, since a warning's fix depends on its own text."""
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {list(SCOPES)}")
    if not correctable(origin):
        raise ValueError("A heading or footnote finding cannot be corrected from here.")
    key = match_key(origin)
    out: list[Occurrence] = []
    for finding in findings:
        if not correctable(finding) or not _in_scope(finding, origin, scope) or match_key(finding) != key:
            continue
        own = (finding.get("suggestions") or [{}])[0].get("text") if finding.get("suggestions") else None
        new = suggestion if (suggestion is not None and not is_warning(finding)) else own
        out.append(Occurrence(
            chapter=str(finding["chapter"]), verse=str(finding["verse"]), finding_id=str(finding["id"]),
            start=int(finding["start"]), end=int(finding["end"]), old=str(finding["originalText"]),
            new=None if new is None or new == finding["originalText"] else str(new),
            text_hash=str(finding.get("textHash") or ""), finding=finding,
        ))

    def order(o: Occurrence) -> tuple:
        def num(value: str) -> tuple[int, str]:
            digits = "".join(ch for ch in value.split("-")[0] if ch.isdigit())
            return (int(digits) if digits else 0, value)
        return (num(o.chapter), num(o.verse), o.start)
    return sorted(out, key=order)


def by_verse(found: Iterable[Occurrence]) -> dict[tuple[str, str], list[Occurrence]]:
    grouped: dict[tuple[str, str], list[Occurrence]] = {}
    for occurrence in found:
        grouped.setdefault((occurrence.chapter, occurrence.verse), []).append(occurrence)
    return grouped


def splice(text: str, found: Iterable[Occurrence]) -> str:
    """Apply one verse's occurrences right to left, so each start offset is
    still valid when it is used. Refuses overlapping spans and any span whose
    text is not what the pass saw (the caller has already checked the hash)."""
    spans = sorted((o for o in found if o.new is not None), key=lambda o: o.start, reverse=True)
    out = text
    previous_start = len(text) + 1
    for occurrence in spans:
        if occurrence.end > previous_start:
            raise ValueError(f"Overlapping corrections at {occurrence.start}-{occurrence.end}.")
        if out[occurrence.start:occurrence.end] != occurrence.old:
            raise ValueError(f"The text at {occurrence.start}-{occurrence.end} is not {occurrence.old!r}.")
        out = out[:occurrence.start] + str(occurrence.new) + out[occurrence.end:]
        previous_start = occurrence.start
    return out
