"""Bridge-private cross-verse alignment links (#117).

translationCore alignment groups are verse-local: each verse's groups may only
contain that verse's own target tokens, and ``_validate_alignment_identity``
rejects anything else on every save. Bridge never fakes a cross-verse link
inside them (``semantic_alignment_guard.py``, INVARIANTS ss39). When a reviewer
sees that a source token of verse 3 is realized in verse 6's target text, that
judgement is recorded *here* -- table ``alignment_cross_verse_links`` in
``bridge-workbench.sqlite3`` -- and nowhere in ``alignmentData/``. The tC group
for the source token stays empty, the target word stays in its own verse's
word bank, and ``completionState`` never turns complete for either verse,
because aligned USFM cannot express the link.

Identity is the pair of tC token signatures (``word U+241F occurrence U+241F
occurrences``) plus chapter and verse on both sides. The positional
``H001``/``T001`` ids that ``make_inventory`` regenerates on every load are
resolved to signatures on the way in and back to ids on the way out; they are
never stored.

Every change is three writes: the row itself (``_write`` / ``_delete``, whose
change_log row image sync replays), a domain event on ``change_log``
(``crossVerseLink`` / ``crossVerseUnlink`` / ``crossVerseInvalidate``), and an
``alignment_history`` row *without* a ``backupPath`` -- so it is in the
per-verse history but invisible to ``alignment.restore``, which only restores
files. A link is marked ``invalid`` rather than deleted when a target text edit
removes the word it pointed at: the reviewer's judgement is kept for the
record and shown as no longer applicable.

**Groups (#217).** Spec section 5: a 1:N, N:1 or N:M realization is *one*
composite group, not several overlapping pairs. A row is still one
(source, target) pair -- the unique pair index and every reader stay as they
were -- and ``groupId`` in the payload says which rows form one group. It is
derived from the members, so the same group lands on the same id everywhere.
A group is written and removed as a unit: N x M rows and events, one history
row. Membership never changes in place; extending a group is unlink-then-link,
the same model tC uses when it rebuilds a group. A row written before groups
existed has no ``groupId`` and is a group of one, keyed by its own id. All of a
group's sources are in one verse and all its targets in one other verse.
"""
from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any, Iterable

from .alignment_engine import AlignmentError
from .models import TokenRef
from .workbench_repository import natural_row_id

if TYPE_CHECKING:  # pragma: no cover
    from .tc_project import TranslationCoreProject

TABLE = "alignment_cross_verse_links"
STATE_ACTIVE = "active"
STATE_INVALID = "invalid"


def group_id_for(sources: Iterable[dict[str, Any]], targets: Iterable[dict[str, Any]]) -> str:
    """Deterministic id of a link group, from its members' (chapter, verse, signature)."""
    def keys(ends: Iterable[dict[str, Any]]) -> list[str]:
        return sorted(f"{e['chapter']}:{e['verse']}:{e['signature']}" for e in ends)
    raw = "␟".join(keys(sources)) + "|" + "␟".join(keys(targets))
    return "xvg_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def group_of(payload: dict[str, Any]) -> str:
    """A row's group: its `groupId`, or its own id for a row written before groups."""
    return str(payload.get("groupId") or payload.get("id") or "")


def relation(source_count: int, target_count: int) -> str:
    """The same labels `alignment_reliability.compile_link_proposal` gives tC groups."""
    if source_count > 1 and target_count == 1:
        return "many-to-one"
    if source_count == 1 and target_count > 1:
        return "one-to-many"
    if source_count > 1 and target_count > 1:
        return "many-to-many"
    return "one-to-one"


def _end(chapter: str | int, verse: str | int, token: TokenRef, *, source: bool) -> dict[str, Any]:
    end: dict[str, Any] = {
        "chapter": str(chapter),
        "verse": str(verse),
        "word": token.word,
        "occurrence": int(token.occurrence),
        "occurrences": int(token.occurrences),
        "signature": token.signature,
    }
    if source:
        for key in ("strong", "lemma", "morph"):
            value = getattr(token, key, "")
            if value:
                end[key] = value
    return end


class CrossVerseLinkStore:
    """Reader/writer for one project's cross-verse links."""

    def __init__(self, project: "TranslationCoreProject") -> None:
        self.project = project

    # ---- identity -----------------------------------------------------------

    @property
    def _identity(self):
        return self.project.workbench_identity

    def _row_id(
        self, s_chapter: str, s_verse: str, s_signature: str,
        t_chapter: str, t_verse: str, t_signature: str,
    ) -> str:
        identity = self._identity
        return natural_row_id(
            identity.project_id, self.project.book_id, TABLE,
            s_chapter, s_verse, s_signature, t_chapter, t_verse, t_signature,
        )

    # ---- reads ----------------------------------------------------------------

    def links_for_verse(self, chapter: str | int, verse: str | int) -> list[dict[str, Any]]:
        """Every link touching a verse, as source or as target, oldest first."""
        identity = self._identity
        chapter, verse = str(chapter), str(verse)
        found: dict[str, dict[str, Any]] = {}
        for equals in (
            {"chapter": chapter, "verse": verse},
            {"target_chapter": chapter, "target_verse": verse},
        ):
            for payload in self.project.workbench.payloads(
                TABLE, project_id=identity.project_id, book_id=self.project.book_id, equals=equals,
            ):
                if isinstance(payload, dict) and payload.get("id"):
                    found[str(payload["id"])] = payload
        return sorted(found.values(), key=lambda item: (str(item.get("createdAt", "")), str(item["id"])))

    def active_links(self) -> list[dict[str, Any]]:
        """Every active link of this book, oldest first (#119: Stage 6B evidence)."""
        identity = self._identity
        payloads = self.project.workbench.payloads(
            TABLE, project_id=identity.project_id, book_id=self.project.book_id,
            equals={"state": STATE_ACTIVE},
        )
        return sorted(
            (p for p in payloads if isinstance(p, dict) and p.get("id")),
            key=lambda item: (str(item.get("createdAt", "")), str(item["id"])),
        )

    def digest(self) -> str:
        """Content digest over every link row of this book, active or invalid.

        Folded into `alignment_state_digest` (#119) so that Stage 6B's run
        fingerprint and `synchronize_alignment_state`'s staling memo both move
        when a link is added, removed or invalidated -- the same rule that
        makes a completion-marker change stale a cached location run.
        """
        import hashlib
        identity = self._identity
        rows = self.project.workbench.rows(
            TABLE, project_id=identity.project_id, book_id=self.project.book_id, order_by="id",
        )
        builder = hashlib.sha256()
        for row in rows:
            builder.update(
                f"{row['id']}␟{row.get('state')}␟{row.get('revision')}␟{row.get('updated_at')}".encode("utf-8")
            )
        return builder.hexdigest()

    def get(self, link_id: str) -> dict[str, Any] | None:
        row = self.project.workbench.get(TABLE, link_id)
        if row is None:
            return None
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    # ---- writes ---------------------------------------------------------------

    def link(
        self,
        source_chapter: str | int, source_verse: str | int, source_token: TokenRef,
        target_chapter: str | int, target_verse: str | int, target_token: TokenRef,
        *, origin: str = "", run_id: str = "",
    ) -> dict[str, Any]:
        """Record that ``source_token`` (of verse A) is realized by ``target_token`` (of verse B).

        A group of one (#217): see `link_group`. Re-linking an ``invalid`` pair
        reactivates it; re-linking an ``active`` pair is refused, so a double
        drop cannot write two events.

        ``origin`` records what produced the link -- ``"ai-auto"`` for one an
        agreed model proposal applied without a per-link click (#146). It rides
        on the ``change_log`` event only, never on the row: the *fact* recorded
        is identical however it was reached, and a row column would be a schema
        bump for something only the audit trail needs. The event is append-only
        and can never be edited, which is exactly the property "who decided
        this?" wants.
        """
        return self.link_group(
            source_chapter, source_verse, [source_token],
            target_chapter, target_verse, [target_token],
            origin=origin, run_id=run_id,
        )[0]

    def link_group(
        self,
        source_chapter: str | int, source_verse: str | int, source_tokens: list[TokenRef],
        target_chapter: str | int, target_verse: str | int, target_tokens: list[TokenRef],
        *, origin: str = "", run_id: str = "",
    ) -> list[dict[str, Any]]:
        """Write one composite group (#217): every (source, target) pair of it,
        under one ``groupId``, with one history row. Returns the pair payloads.

        Refused when any pair is already active, so nothing is half-written.
        The caller (``link_cross_verse``) has already checked that no member
        has a home elsewhere; this store only guards its own rows.
        """
        if not source_tokens or not target_tokens:
            raise AlignmentError("A cross-verse group needs at least one source and one target word.")
        sources = [_end(source_chapter, source_verse, t, source=True) for t in source_tokens]
        targets = [_end(target_chapter, target_verse, t, source=False) for t in target_tokens]
        if len({s["signature"] for s in sources}) != len(sources) or len({t["signature"] for t in targets}) != len(targets):
            raise AlignmentError("A cross-verse group lists the same word twice.")
        group_id = group_id_for(sources, targets)
        rel = relation(len(sources), len(targets))
        pairs: list[tuple[dict[str, Any], dict[str, Any], str, dict[str, Any] | None]] = []
        for source in sources:
            for target in targets:
                row_id = self._row_id(
                    source["chapter"], source["verse"], source["signature"],
                    target["chapter"], target["verse"], target["signature"],
                )
                existing = self.get(row_id)
                if existing is not None and existing.get("state") == STATE_ACTIVE:
                    raise AlignmentError(
                        f"{source['word']} ({source['chapter']}:{source['verse']}) is already linked to "
                        f"{target['word']} ({target['chapter']}:{target['verse']})."
                    )
                pairs.append((source, target, row_id, existing))
        now = self.project.workbench._now()
        written: list[dict[str, Any]] = []
        for source, target, row_id, existing in pairs:
            payload = {
                "id": row_id,
                "bookId": self.project.book_id,
                "groupId": group_id,
                "relation": rel,
                "source": source,
                "target": target,
                "state": STATE_ACTIVE,
                "createdAt": existing.get("createdAt", now) if existing else now,
                "updatedAt": now,
                "actorId": self._identity.actor_id,
            }
            self._write_row(payload)
            event = dict(payload)
            if origin:
                event["origin"] = origin
            if run_id:
                event["runId"] = run_id
            self._event(row_id, "crossVerseLink", event)
            written.append(payload)
        self._history(
            sources[0]["chapter"], sources[0]["verse"],
            "crossVerseLink" if len(written) == 1 else "crossVerseLinkGroup",
            written[0] if len(written) == 1 else self._group_record(written, origin),
        )
        return written

    def group_members(self, link_or_group_id: str) -> list[dict[str, Any]]:
        """Every row of the group a link belongs to (or of a group id), any state."""
        payload = self.get(link_or_group_id)
        if payload is not None:
            group_id = group_of(payload)
            chapter, verse = payload["source"]["chapter"], payload["source"]["verse"]
        else:
            group_id = link_or_group_id
            found = [p for p in self._all_payloads() if group_of(p) == group_id]
            if not found:
                return []
            chapter, verse = found[0]["source"]["chapter"], found[0]["source"]["verse"]
        return [
            p for p in self.links_for_verse(chapter, verse)
            if group_of(p) == group_id and p["source"]["chapter"] == chapter and p["source"]["verse"] == verse
        ]

    def unlink(self, link_id: str, *, origin: str = "", extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """Remove the link's whole group (#217): a composite group is one
        realization, so taking one pair out of it would leave a different
        claim nobody made. Returns the pair that was asked for."""
        members = self.group_members(link_id)
        payload = next((m for m in members if m["id"] == link_id), None)
        if payload is None:
            raise AlignmentError("That cross-verse link no longer exists. Reload before removing it.")
        self._unlink_members(members, origin=origin, extra=extra)
        return payload

    def unlink_group(self, group_id: str, *, origin: str = "", extra: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        members = self.group_members(group_id)
        if not members:
            raise AlignmentError("That cross-verse group no longer exists. Reload before removing it.")
        self._unlink_members(members, origin=origin, extra=extra)
        return members

    def _unlink_members(self, members: list[dict[str, Any]], *, origin: str, extra: dict[str, Any] | None) -> None:
        identity = self._identity
        for member in members:
            self.project.workbench._delete(
                TABLE, member["id"],
                project_id=identity.project_id, book_id=self.project.book_id,
                actor_id=identity.actor_id, device_id=identity.device_id,
            )
            event = {**member, **(extra or {})}
            if origin:
                event["origin"] = origin
            self._event(member["id"], "crossVerseUnlink", event)
        first = members[0]
        self._history(
            first["source"]["chapter"], first["source"]["verse"],
            "crossVerseUnlink" if len(members) == 1 else "crossVerseUnlinkGroup",
            first if len(members) == 1 else self._group_record(members, origin),
        )

    def invalidate_missing_targets(
        self, chapter: str | int, verse: str | int, missing_signatures: set[str],
    ) -> list[dict[str, Any]]:
        """Mark every active link whose *target* word in this verse was removed
        by a text edit -- and, since #217, every other row of its group: a
        composite group with a member gone is not the group anyone decided.
        Called by ``apply_scripture_edit`` with the signatures the reconcile
        step dropped; a word that survives the edit under the same signature
        keeps its link."""
        if not missing_signatures:
            return []
        chapter, verse = str(chapter), str(verse)
        hit_groups: dict[str, str] = {}
        for payload in self.links_for_verse(chapter, verse):
            target = payload.get("target", {})
            if payload.get("state") != STATE_ACTIVE:
                continue
            if target.get("chapter") != chapter or target.get("verse") != verse:
                continue
            if target.get("signature") not in missing_signatures:
                continue
            hit_groups.setdefault(group_of(payload), str(target.get("word", "")))
        invalidated: list[dict[str, Any]] = []
        for group_id, word in hit_groups.items():
            members = [m for m in self.group_members(group_id) if m.get("state") == STATE_ACTIVE]
            for member in members:
                updated = dict(member)
                updated["state"] = STATE_INVALID
                updated["invalidReason"] = f"{word} is no longer in the text of {chapter}:{verse}."
                updated["updatedAt"] = self.project.workbench._now()
                self._write_row(updated)
                self._event(updated["id"], "crossVerseInvalidate", updated)
                invalidated.append(updated)
            if members:
                self._history(
                    members[0]["source"]["chapter"], members[0]["source"]["verse"], "crossVerseInvalidate",
                    invalidated[-1] if len(members) == 1 else self._group_record(invalidated[-len(members):], ""),
                )
        return invalidated

    def _all_payloads(self) -> list[dict[str, Any]]:
        identity = self._identity
        return [
            p for p in self.project.workbench.payloads(
                TABLE, project_id=identity.project_id, book_id=self.project.book_id,
            ) if isinstance(p, dict) and p.get("id")
        ]

    @staticmethod
    def _group_record(members: list[dict[str, Any]], origin: str) -> dict[str, Any]:
        """The history row's view of a group: who, what, and the row ids."""
        sources = {m["source"]["signature"]: m["source"] for m in members}
        targets = {m["target"]["signature"]: m["target"] for m in members}
        record = {
            "id": group_of(members[0]),
            "groupId": group_of(members[0]),
            "relation": relation(len(sources), len(targets)),
            "sources": list(sources.values()),
            "targets": list(targets.values()),
            "links": [m["id"] for m in members],
            "state": members[0].get("state"),
        }
        if origin:
            record["origin"] = origin
        return record

    # ---- the three writes -----------------------------------------------------

    def _write_row(self, payload: dict[str, Any]) -> None:
        identity = self._identity
        source, target = payload["source"], payload["target"]
        self.project.workbench._write(
            TABLE, payload["id"],
            project_id=identity.project_id, book_id=self.project.book_id, payload=payload,
            actor_id=identity.actor_id, device_id=identity.device_id,
            expected_revision=None,
            extra_columns={
                "chapter": source["chapter"], "verse": source["verse"],
                "source_signature": source["signature"],
                "target_chapter": target["chapter"], "target_verse": target["verse"],
                "target_signature": target["signature"],
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

    def _history(self, chapter: str, verse: str, operation: str, link: dict[str, Any]) -> None:
        # Same table and id shape as `_record_alignment_history`, deliberately
        # without `backupPath`: nothing on disk changed, so there is nothing to
        # restore, and `_alignment_history_entries` skips it for that reason.
        # The record's id is part of the history id (#217): `_timestamp` has
        # millisecond resolution, and the automatic pass writes many groups of
        # one verse in a burst, which would otherwise overwrite each other.
        identity = self._identity
        iso, safe = self.project._timestamp()
        history_id = f"{safe}_{operation}_{str(link.get('id', ''))[:12]}.json"
        payload = {
            "id": history_id,
            "bookId": self.project.book_id,
            "chapter": str(chapter),
            "verse": str(verse),
            "operation": operation,
            "timestamp": iso,
            "link": link,
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
