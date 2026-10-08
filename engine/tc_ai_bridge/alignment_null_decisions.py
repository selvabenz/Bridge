"""Null alignment decisions: a token with no counterpart, for a named reason (#216).

translationCore alignment can say only "this source word has these target
words" or nothing at all; an empty group and a word left in the word bank both
mean *not aligned yet*. Bridge needs a third statement, the one spec section 7
calls NULL_ALIGNED: "this word has no counterpart, and that is correct" -- a
Greek article Tamil does not render, a Tamil resumptive pronoun no source word
licenses. Without it every such word stays a gap forever and Stage 8 can only
read it as a possible omission or addition.

A row here is that positive assertion and nothing else:

* **No row means unaligned.** UNRESOLVED is never stored (spec section 7:
  UNALIGNED != NULL_ALIGNED). The `unresolved` origin that
  `alignment_reliability.compile_link_proposal` gives an empty group is a tC
  presentation detail and never reaches this table.
* **The reason is closed and depends on the side.** A source word is
  ``IMPLICIT`` (context carries the meaning) or ``GRAMMATICAL`` (morphology or
  word order with no separate target word). A target word is ``GRAMMATICAL``
  (the target language requires it) or ``EXPLICITATION`` (it states what the
  source implies). The two source reasons have different Stage 8 consequences,
  which is why the two-pass agreement (#219) treats a reason mismatch as a
  disagreement.
* **Nothing is written to `alignmentData/`.** Like a cross-verse link (#117),
  a null decision is Bridge-private: aligned USFM has no way to express it, so
  a verse that has one keeps its honest tC status.

Same discipline as `cross_verse_links.py`: identity is the tC token signature
plus chapter, verse and side; positional ``H001``/``T001`` ids are resolved per
request and never stored. Every change is three writes -- the row, a domain
event on ``change_log`` (``nullDecide`` / ``nullClear`` / ``nullInvalidate``)
and an ``alignment_history`` row without ``backupPath``. Changing a reason is an
explicit update that carries ``previousReason`` (spec section 7.6: "a null
decision may be replaced, but it must be an explicit update with history
preserved"). A target edit that removes the word marks the row ``invalid``
inside the same journal transaction, rather than deleting the reviewer's
judgement. Source tokens come from the pinned original-language pack and no
edit path removes one, so there is no source-side invalidation hook; a reader
that cannot resolve a source signature reports the row as stale.

Unlike a link, ``origin`` is kept on the row as well as on the event: the
automatic pass (#219) must be able to tell the nulls it wrote from the ones a
reviewer wrote, so that a re-run supersedes its own decisions and never a
human's.
"""
from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from .alignment_engine import AlignmentError
from .models import TokenRef
from .workbench_repository import natural_row_id

if TYPE_CHECKING:  # pragma: no cover
    from .tc_project import TranslationCoreProject

TABLE = "alignment_null_decisions"
STATE_ACTIVE = "active"
STATE_INVALID = "invalid"

SIDE_SOURCE = "source"
SIDE_TARGET = "target"
REASONS_BY_SIDE: dict[str, frozenset[str]] = {
    SIDE_SOURCE: frozenset({"IMPLICIT", "GRAMMATICAL"}),
    SIDE_TARGET: frozenset({"GRAMMATICAL", "EXPLICITATION"}),
}
ORIGINS = frozenset({"human", "ai-auto", "ai-proposed-accepted"})


def validate(side: str, reason: str, origin: str = "human") -> tuple[str, str]:
    """The canonical ``(side, reason)``, or an `AlignmentError` naming what is allowed."""
    side = str(side or "").strip().lower()
    if side not in REASONS_BY_SIDE:
        raise AlignmentError(f"A null decision is for a 'source' or 'target' word, not {side!r}.")
    reason = str(reason or "").strip().upper()
    allowed = REASONS_BY_SIDE[side]
    if reason not in allowed:
        raise AlignmentError(
            f"A {side} word can be marked {' or '.join(sorted(allowed))}, not {reason or 'nothing'}."
        )
    if origin not in ORIGINS:
        raise AlignmentError(f"Unknown null-decision origin {origin!r}.")
    return side, reason


def _token(token: TokenRef, *, source: bool) -> dict[str, Any]:
    data: dict[str, Any] = {
        "word": token.word,
        "occurrence": int(token.occurrence),
        "occurrences": int(token.occurrences),
        "signature": token.signature,
    }
    if source:
        for key in ("strong", "lemma", "morph"):
            value = getattr(token, key, "")
            if value:
                data[key] = value
    return data


class NullDecisionStore:
    """Reader/writer for one project's null alignment decisions."""

    def __init__(self, project: "TranslationCoreProject") -> None:
        self.project = project

    @property
    def _identity(self):
        return self.project.workbench_identity

    def _row_id(self, chapter: str, verse: str, side: str, signature: str) -> str:
        return natural_row_id(
            self._identity.project_id, self.project.book_id, TABLE, chapter, verse, side, signature,
        )

    # ---- reads ----------------------------------------------------------------

    def decisions_for_verse(self, chapter: str | int, verse: str | int) -> list[dict[str, Any]]:
        """Every decision of one verse, active or invalid, oldest first."""
        identity = self._identity
        payloads = self.project.workbench.payloads(
            TABLE, project_id=identity.project_id, book_id=self.project.book_id,
            equals={"chapter": str(chapter), "verse": str(verse)},
        )
        return _ordered(payloads)

    def active_decisions(self) -> list[dict[str, Any]]:
        """Every active decision of this book, oldest first -- one read for a
        chapter scan or for Stage 6B/8 evidence."""
        identity = self._identity
        payloads = self.project.workbench.payloads(
            TABLE, project_id=identity.project_id, book_id=self.project.book_id,
            equals={"state": STATE_ACTIVE},
        )
        return _ordered(payloads)

    def digest(self) -> str:
        """Content digest over every row of this book, folded into
        `alignment_state_digest` so Stage 6B/8 go stale when a decision
        changes -- the same rule `CrossVerseLinkStore.digest` follows."""
        rows = self.project.workbench.rows(
            TABLE, project_id=self._identity.project_id, book_id=self.project.book_id, order_by="id",
        )
        if not rows:
            # Empty, not the hash of nothing: lets `alignment_state_digest`
            # leave a book with no decisions on the digest it always had.
            return ""
        builder = hashlib.sha256()
        for row in rows:
            builder.update(
                f"{row['id']}␟{row.get('state')}␟{row.get('reason')}␟{row.get('revision')}␟{row.get('updated_at')}".encode("utf-8")
            )
        return builder.hexdigest()

    def get(self, decision_id: str) -> dict[str, Any] | None:
        row = self.project.workbench.get(TABLE, decision_id)
        if row is None:
            return None
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    # ---- writes ---------------------------------------------------------------

    def set(
        self, chapter: str | int, verse: str | int, side: str, token: TokenRef, reason: str,
        *, note: str = "", origin: str = "human", run_id: str = "",
    ) -> dict[str, Any]:
        """Record that ``token`` has no counterpart, for ``reason``.

        The same reason twice is refused (a double click must not write two
        events). A different reason is an explicit update that keeps the old
        one as ``previousReason``. Setting an ``invalid`` row reactivates it.
        """
        side, reason = validate(side, reason, origin)
        chapter, verse = str(chapter), str(verse)
        row_id = self._row_id(chapter, verse, side, token.signature)
        existing = self.get(row_id)
        if existing is not None and existing.get("state") == STATE_ACTIVE and existing.get("reason") == reason:
            raise AlignmentError(
                f"{token.word} in {chapter}:{verse} is already marked {reason.lower()}."
            )
        now = self.project.workbench._now()
        payload: dict[str, Any] = {
            "id": row_id,
            "bookId": self.project.book_id,
            "chapter": chapter,
            "verse": verse,
            "side": side,
            "token": _token(token, source=side == SIDE_SOURCE),
            "reason": reason,
            "note": str(note or "")[:300],
            "state": STATE_ACTIVE,
            "origin": origin,
            "createdAt": existing.get("createdAt", now) if existing else now,
            "updatedAt": now,
            "actorId": self._identity.actor_id,
        }
        if run_id:
            payload["runId"] = run_id
        if existing is not None and existing.get("state") == STATE_ACTIVE:
            payload["previousReason"] = existing.get("reason")
        self._write_row(payload)
        self._event(row_id, "nullDecide", payload)
        self._history(chapter, verse, "nullDecide", payload)
        return payload

    def clear(self, decision_id: str, *, origin: str = "human", extra: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = self.get(decision_id)
        if payload is None:
            raise AlignmentError("That decision no longer exists. Reload before clearing it.")
        identity = self._identity
        self.project.workbench._delete(
            TABLE, decision_id,
            project_id=identity.project_id, book_id=self.project.book_id,
            actor_id=identity.actor_id, device_id=identity.device_id,
        )
        event = {**payload, "clearedBy": origin, **(extra or {})}
        self._event(decision_id, "nullClear", event)
        self._history(payload["chapter"], payload["verse"], "nullClear", event)
        return payload

    def invalidate_missing(
        self, chapter: str | int, verse: str | int, side: str, missing_signatures: set[str],
    ) -> list[dict[str, Any]]:
        """Mark every active decision on a token a text edit removed. Called by
        `apply_scripture_edit` with the signatures the reconcile step dropped,
        inside the edit's journal transaction."""
        if not missing_signatures:
            return []
        invalidated: list[dict[str, Any]] = []
        for payload in self.decisions_for_verse(chapter, verse):
            if payload.get("state") != STATE_ACTIVE or payload.get("side") != side:
                continue
            token = payload.get("token", {})
            if token.get("signature") not in missing_signatures:
                continue
            updated = dict(payload)
            updated["state"] = STATE_INVALID
            updated["invalidReason"] = f"{token.get('word', '')} is no longer in the text of {chapter}:{verse}."
            updated["updatedAt"] = self.project.workbench._now()
            self._write_row(updated)
            self._event(updated["id"], "nullInvalidate", updated)
            self._history(str(chapter), str(verse), "nullInvalidate", updated)
            invalidated.append(updated)
        return invalidated

    # ---- the three writes -----------------------------------------------------

    def _write_row(self, payload: dict[str, Any]) -> None:
        identity = self._identity
        self.project.workbench._write(
            TABLE, payload["id"],
            project_id=identity.project_id, book_id=self.project.book_id, payload=payload,
            actor_id=identity.actor_id, device_id=identity.device_id,
            expected_revision=None,
            extra_columns={
                "chapter": payload["chapter"], "verse": payload["verse"], "side": payload["side"],
                "signature": payload["token"]["signature"], "reason": payload["reason"],
                "state": payload["state"],
            },
        )

    def _event(self, row_id: str, op: str, payload: dict[str, Any]) -> None:
        identity = self._identity
        self.project.workbench.append_event(
            TABLE, row_id,
            project_id=identity.project_id, book_id=self.project.book_id, op=op, payload=payload,
            actor_id=identity.actor_id, device_id=identity.device_id,
        )

    def _history(self, chapter: str, verse: str, operation: str, decision: dict[str, Any]) -> None:
        # Same shape as CrossVerseLinkStore._history: in the verse's history,
        # never offered for restore, because no file changed.
        # The row id is part of the history id: `_timestamp` has millisecond
        # resolution and the automatic pass (#219) writes many decisions of one
        # verse in a burst, so a timestamp alone would let the second write
        # overwrite the first's history row.
        identity = self._identity
        iso, safe = self.project._timestamp()
        history_id = f"{safe}_{operation}_{str(decision.get('id', ''))[:12]}.json"
        payload = {
            "id": history_id,
            "bookId": self.project.book_id,
            "chapter": str(chapter),
            "verse": str(verse),
            "operation": operation,
            "timestamp": iso,
            "nullDecision": decision,
        }
        row_id = natural_row_id(
            identity.project_id, self.project.book_id, "alignment_history",
            str(chapter), str(verse), history_id,
        )
        self.project.workbench._write(
            "alignment_history", row_id,
            project_id=identity.project_id, book_id=self.project.book_id, payload=payload,
            actor_id=identity.actor_id, device_id=identity.device_id,
            expected_revision=None,
            extra_columns={"chapter": str(chapter), "verse": str(verse)},
        )


def _ordered(payloads: Any) -> list[dict[str, Any]]:
    return sorted(
        (p for p in payloads if isinstance(p, dict) and p.get("id")),
        key=lambda item: (str(item.get("createdAt", "")), str(item["id"])),
    )
