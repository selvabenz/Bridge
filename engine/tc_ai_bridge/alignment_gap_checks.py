"""Possible omission / addition from alignment, as verse check findings (#220).

The automatic pass (#219) reports a word neither of its readings could place.
A reviewer working in the verse editor should see that without opening the
Cross-Verse Alignment page, so the ordinary `alignment` check engine raises it
as two finding types:

* ``ALIGN_POSSIBLE_OMISSION`` -- a source word with no home: not in a tC group
  with target words, not at either end of an active cross-verse link, and not
  carrying a null decision;
* ``ALIGN_POSSIBLE_ADDITION`` -- the same for a target word.

**When they fire.** Only on a verse an automatic pass has run on (a verdict
row exists and was not reverted) or that translationCore marks completed. On a
verse nobody has aligned, every word is a gap, and `ALIGN_UNALIGNED_TOP` /
`_BOTTOM` already say so -- repeating that per word would bury the verse.
A gap is always *live*: a reviewer's link or null decision silences it on the
next check, with no model call.

**Identity.** ``group_id`` is the token's tC signature, so
`_stable_finding_id` gives the same id across re-runs and a reviewer's decision
is re-applied inline (CLAUDE.md gotcha 4). A target finding carries the word's
raw code-point span so the editor can underline it; a source word has no span in
the target text and carries none.

The detail carries the model's own note from the verdict. If the verse or its
alignment changed after the pass, it says so instead of repeating a note that
may no longer apply. These are *possible* findings: nothing here was aligned or
decided, and a reviewer's "Not missing" is a null decision, not a dismissal.
"""
from __future__ import annotations

import json
import re
from typing import Any

from . import alignment_gaps, check_timing
from .alignment_engine import make_inventory
from .alignment_null_decisions import TABLE as NULL_DECISIONS_TABLE
from .cross_verse_links import TABLE as CROSS_VERSE_LINKS_TABLE
from .alignment_reliability import alignment_fingerprint, target_token_fingerprint
from .models import QAIssue
from .usfm_verse import WHITESPACE_TOKEN_TRIM_CHARS, lift_verse
from .workbench_repository import natural_row_id

OMISSION = "ALIGN_POSSIBLE_OMISSION"
ADDITION = "ALIGN_POSSIBLE_ADDITION"
CHECK_OMISSION = "alignment.possible_omission"
CHECK_ADDITION = "alignment.possible_addition"


def read_verdict(project: Any, chapter: str, verse: str) -> dict[str, Any] | None:
    identity = project.workbench_identity
    row_id = natural_row_id(identity.project_id, project.book_id, "alignment_verdicts", str(chapter), str(verse))
    row = project.workbench.get("alignment_verdicts", row_id)
    if row is None:
        return None
    try:
        payload = json.loads(row["payload_json"])
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _decoded(row: dict[str, Any]) -> dict[str, Any] | None:
    """A row's payload as WorkbenchRepository.payloads decodes it."""
    try:
        payload = json.loads(row["payload_json"])
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _oldest_first(payloads: Any) -> list[dict[str, Any]]:
    return sorted(payloads, key=lambda item: (str(item.get("createdAt", "")), str(item["id"])))


class GapReads:
    """A check job's copy of what gap_issues reads per verse (#241): every
    cross-verse link, null decision and verdict row of the book, read in three
    queries instead of up to four connections per verse. Valid while the
    workbench's write generation is the one it was read at (`prefetch`).

    Each per-verse view selects and orders exactly as the reader it replaces:
    links by the lifted (chapter, verse) and (target_chapter, target_verse)
    columns, as `CrossVerseLinkStore.links_for_verse`; decisions by (chapter,
    verse), as `NullDecisionStore.decisions_for_verse`; a verdict by its row id,
    as `read_verdict`. Every row of the book is read, active or not, so the
    filters in gap_issues see what they always saw."""

    def __init__(self, project: Any) -> None:
        workbench = project.workbench
        identity = project.workbench_identity
        self.generation = workbench.write_generation
        scope = {"project_id": identity.project_id, "book_id": project.book_id}
        with check_timing.current().step("gap.prefetch"):
            link_rows = workbench.rows(CROSS_VERSE_LINKS_TABLE, **scope)
            null_rows = workbench.rows(NULL_DECISIONS_TABLE, **scope)
            verdict_rows = workbench.rows("alignment_verdicts", **scope)
        links: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
        for row in link_rows:
            payload = _decoded(row)
            if payload is None or not payload.get("id"):
                continue
            for key in ((str(row.get("chapter")), str(row.get("verse"))),
                        (str(row.get("target_chapter")), str(row.get("target_verse")))):
                links.setdefault(key, {})[str(payload["id"])] = payload
        self._links = {key: _oldest_first(found.values()) for key, found in links.items()}
        nulls: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in null_rows:
            payload = _decoded(row)
            if payload is not None and payload.get("id"):
                nulls.setdefault((str(row.get("chapter")), str(row.get("verse"))), []).append(payload)
        self._nulls = {key: _oldest_first(found) for key, found in nulls.items()}
        self._verdict_rows = {str(row["id"]): row for row in verdict_rows}
        self._identity = identity
        self._book_id = project.book_id

    def verdict(self, chapter: str, verse: str) -> dict[str, Any] | None:
        row_id = natural_row_id(self._identity.project_id, self._book_id, "alignment_verdicts",
                                str(chapter), str(verse))
        row = self._verdict_rows.get(row_id)
        return None if row is None else _decoded(row)

    def links_for_verse(self, chapter: str, verse: str) -> list[dict[str, Any]]:
        return list(self._links.get((str(chapter), str(verse)), ()))

    def decisions_for_verse(self, chapter: str, verse: str) -> list[dict[str, Any]]:
        return list(self._nulls.get((str(chapter), str(verse)), ()))


def prefetch(project: Any, reads: dict[str, Any] | None) -> GapReads | None:
    """The job's GapReads in `reads` (a dict the job keeps for its whole run),
    read again when the workbench has had a committed write since. None outside
    a job (verse.runChecks reads per verse, as before) or when the prefetch
    itself fails, so gap_issues falls back to its own reads."""
    if reads is None:
        return None
    memo = reads.get("gap")
    try:
        if memo is None or memo.generation != project.workbench.write_generation:
            memo = reads["gap"] = GapReads(project)
    except Exception:
        reads.pop("gap", None)
        return None
    return memo


def verdict_is_stale(verdict: dict[str, Any], alignment: Any, target_text: str) -> bool:
    return (
        verdict.get("alignmentFingerprint") != alignment_fingerprint(alignment)
        or verdict.get("targetFingerprint") != target_token_fingerprint(alignment, target_text)
    )


def _nth_word_span(text: str, word: str, occurrence: int) -> tuple[int, int] | None:
    """Raw code-point span of the n-th whole-word occurrence of `word` in the
    visible text -- `_first_token_span` generalised to an occurrence, with the
    same boundary rule (whitespace tokens, edge punctuation trimmed)."""
    lifted = lift_verse(text)
    seen = 0
    for match in re.finditer(r"\S+", lifted.plain):
        chunk = match.group()
        stripped = chunk.strip(WHITESPACE_TOKEN_TRIM_CHARS)
        if stripped != word:
            continue
        seen += 1
        if seen == occurrence:
            start = match.start() + chunk.index(stripped)
            return lifted.raw_span(start, start + len(stripped))
    return None


def gap_issues(project: Any, chapter: str, verse: str, alignment: Any, target_text: str, *,
               reads: GapReads | None = None) -> list[QAIssue]:
    """`reads`: a check job's prefetch (`prefetch`); without it each read goes
    to the workbench, as verse.runChecks does."""
    chapter, verse = str(chapter), str(verse)
    verdict = reads.verdict(chapter, verse) if reads is not None else read_verdict(project, chapter, verse)
    if verdict is not None and verdict.get("verdict") == "REVERTED":
        verdict = None
    completed = False
    try:
        completed = project.word_alignment_state(chapter, verse) == "completed"
    except Exception:
        completed = False
    if verdict is None and not completed:
        return []
    inventory = make_inventory(alignment)
    if not inventory.top_ids:
        return []

    realized: list[str] = []
    accounted: list[str] = []
    try:
        links = (reads.links_for_verse(chapter, verse) if reads is not None
                 else project.cross_verse_links.links_for_verse(chapter, verse))
    except Exception:
        links = []
    for link in links:
        if link.get("state") != "active":
            continue
        source, target = link.get("source") or {}, link.get("target") or {}
        if source.get("chapter") == chapter and source.get("verse") == verse:
            token_id = inventory.top_sig_to_id.get(source.get("signature", ""))
            if token_id:
                realized.append(token_id)
        if target.get("chapter") == chapter and target.get("verse") == verse:
            token_id = inventory.bottom_sig_to_id.get(target.get("signature", ""))
            if token_id:
                accounted.append(token_id)
    null_source: list[str] = []
    null_target: list[str] = []
    try:
        decisions = (reads.decisions_for_verse(chapter, verse) if reads is not None
                     else project.null_decisions.decisions_for_verse(chapter, verse))
    except Exception:
        decisions = []
    for decision in decisions:
        if decision.get("state") != "active":
            continue
        signature = (decision.get("token") or {}).get("signature", "")
        if decision.get("side") == "source" and (token_id := inventory.top_sig_to_id.get(signature)):
            null_source.append(token_id)
        elif decision.get("side") == "target" and (token_id := inventory.bottom_sig_to_id.get(signature)):
            null_target.append(token_id)
    source_gaps, target_gaps = alignment_gaps.gap_ids(
        inventory, alignment_gaps.group_views(alignment, inventory),
        realized_ids=realized, accounted_ids=accounted,
        null_source_ids=null_source, null_target_ids=null_target,
    )

    notes: dict[tuple[str, str], str] = {}
    stale = False
    if verdict is not None:
        for issue in verdict.get("issues", ()) or ():
            notes[(str(issue.get("side")), str(issue.get("signature")))] = str(issue.get("note") or "")
        stale = verdict_is_stale(verdict, alignment, target_text)

    def detail(side: str, word: str, signature: str) -> str:
        if stale:
            note = "The verse changed after the last automatic pass; run it again to refresh this."
        else:
            note = notes.get((side, signature), "")
        base = (
            f"{word} has no counterpart in this verse or its neighbours, and no reason is recorded."
            if side == "source" else
            f"{word} renders no source word that could be found, and no reason is recorded."
        )
        return f"{base} {note}".strip()

    issues: list[QAIssue] = []
    for token_id in source_gaps:
        token = inventory.top_ids[token_id]
        issues.append(QAIssue(
            OMISSION, "high", "Possible omission",
            detail("source", token.word, token.signature),
            source="local", check_id=CHECK_OMISSION, group_id=token.signature,
        ))
    for token_id in target_gaps:
        token = inventory.bottom_ids[token_id]
        span = _nth_word_span(target_text, token.word, int(token.occurrence or 1))
        issue = QAIssue(
            ADDITION, "medium", "Possible addition",
            detail("target", token.word, token.signature),
            source="local", check_id=CHECK_ADDITION, group_id=token.signature,
        )
        if span is not None:
            # A span without a replacement: highlight only, no one-click fix.
            issue.start_offset, issue.end_offset = span
            issue.original_text = token.word
        issues.append(issue)
    return issues
