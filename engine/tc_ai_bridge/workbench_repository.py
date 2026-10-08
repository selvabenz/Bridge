"""SQLite persistence for Bridge's per-project workbench store.

Beside the v14 semantic-analysis companion database
(``passage_semantic_repository.py``, untouched by this module) each project
gets a second database, ``bridge-workbench.sqlite3``, for Bridge-private
state that is not an analysis record with a lifecycle: human decisions, QA
dispositions, progress rollups, and the rest of ``docs/TEAM_ARCHITECTURE.md``
section 3. See that document and ``docs/DECISIONS.md`` (2026-09-11, "second
per-project SQLite, not v15") for why this is a second database rather than
a v15 migration of the existing one.

Schema (a versioned ladder, ``WORKBENCH_SCHEMA_VERSION``), the append-only
``change_log``, one generic revision-checked write path with a batched
variant, and the local half of sync: an unsynced cursor, acknowledgement,
and JSON-lines export/import of event batches (#75 skeleton; #76 human-owned
stores; #77 derived stores and sync readiness).
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterator
import uuid

from . import check_timing


WORKBENCH_SCHEMA_VERSION = 7
WORKBENCH_SCHEMA_ID = "bridge-workbench-v1"

_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# The tables human decisions and derived caches move into, per
# TEAM_ARCHITECTURE.md ss3.1/3.2. `change_log` is deliberately not one of
# these -- it is never a `_write()` target, only ever appended to inside
# `_write()` itself.
MUTABLE_TABLES: tuple[str, ...] = (
    "human_decisions",
    "issue_resolutions",
    "ai_review_results",
    "alignment_history",
    "alignment_diagnostics",
    "team_members",
    "team_assignments",
    "semantic_mappings",
    "semantic_validation_runs",
    "project_state",
    "progress_chapters",
    "progress_findings",
    "progress_totals",
    "check_findings",
    "check_cache",
    "triage_verdicts",
    "metrics_events",
    "metrics_counters",
    "file_backups",
    "alignment_cross_verse_links",
    "language_qa_cache",
    "language_qa_batches",
    "language_qa_learned_fixes",
    "language_qa_flags",
    "alignment_null_decisions",
    "alignment_verdicts",
)


@dataclass(frozen=True)
class WorkbenchIdentity:
    """Who is writing, and to which project.

    Carried explicitly rather than read from global settings, so a background
    job and a user action are attributable to the same identity without either
    reaching for app state. Every field lands verbatim on ``change_log`` rows
    that can never be edited afterwards, so this is deliberately a value object
    with no defaults -- a caller has to say who it is.
    """

    project_id: str
    book_id: str | None
    actor_id: str
    device_id: str


def natural_row_id(*parts: Any) -> str:
    """A deterministic row id derived from a store's natural key.

    Migration has to be idempotent -- it can be interrupted and re-run, and
    re-running must not duplicate a record (TEAM_ARCHITECTURE.md ss3.5,
    "INSERT OR IGNORE on natural keys"). Deriving the primary key from the
    natural key is what makes a plain upsert idempotent without a separate
    existence query, and it makes the same record land on the same id on
    every machine, which is what the hub will later need to match rows
    across devices. A random uuid4 would satisfy neither.

    ``None`` and ``''`` are distinct from the string ``'None'``: parts are
    length-prefixed so ("a", "bc") and ("ab", "c") cannot collide.
    """
    encoded = "|".join(
        "~" if part is None else f"{len(str(part))}:{part}" for part in parts
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]


class WorkbenchError(RuntimeError):
    pass


class WorkbenchValidationError(WorkbenchError):
    pass


class WorkbenchConflict(WorkbenchError):
    pass


# Every mutable table carries the same nine columns first (id, project_id,
# book_id, revision, actor_id, device_id, created_at, updated_at,
# payload_json) so `_write()` can INSERT/UPDATE them generically; table-
# specific "lifted" columns come after, exactly the ones TEAM_ARCHITECTURE.md
# ss3.1/3.2 names for that table and no others -- `payload_json` already
# carries everything else unchanged.
_MIGRATION_V1 = r"""
CREATE TABLE human_decisions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    book_id TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    actor_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('check','qa','verse_status','terminology')),
    chapter TEXT,
    verse TEXT,
    key TEXT NOT NULL,
    decision TEXT,
    UNIQUE(project_id, book_id, kind, chapter, verse, key)
);
CREATE INDEX ix_human_decisions_scope ON human_decisions(project_id, book_id, kind);

CREATE TABLE issue_resolutions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    book_id TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    actor_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT,
    recheck_status TEXT,
    paratext_status TEXT
);
CREATE INDEX ix_issue_resolutions_scope ON issue_resolutions(project_id, book_id);

CREATE TABLE ai_review_results (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    book_id TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    actor_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    chapter TEXT NOT NULL,
    verse TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    UNIQUE(project_id, book_id, chapter, verse)
);

CREATE TABLE alignment_history (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    book_id TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    actor_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    chapter TEXT,
    verse TEXT,
    backup_path TEXT
);
CREATE INDEX ix_alignment_history_scope ON alignment_history(project_id, book_id, chapter, verse);

CREATE TABLE alignment_diagnostics (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    book_id TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    actor_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    chapter TEXT,
    verse TEXT
);
CREATE INDEX ix_alignment_diagnostics_scope ON alignment_diagnostics(project_id, book_id, chapter, verse);

CREATE TABLE team_members (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    book_id TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    actor_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE team_assignments (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    book_id TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    actor_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE semantic_mappings (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    book_id TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    actor_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    UNIQUE(project_id, book_id, fingerprint)
);

CREATE TABLE semantic_validation_runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    book_id TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    actor_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    suite_id TEXT NOT NULL
);
CREATE INDEX ix_semantic_validation_runs_suite ON semantic_validation_runs(project_id, suite_id);

CREATE TABLE project_state (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    book_id TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    actor_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    key TEXT NOT NULL,
    UNIQUE(project_id, book_id, key)
);

CREATE TABLE progress_chapters (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    book_id TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    actor_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    chapter TEXT NOT NULL,
    UNIQUE(project_id, book_id, chapter)
);

CREATE TABLE progress_findings (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    book_id TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    actor_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    chapter TEXT
);
CREATE INDEX ix_progress_findings_scope ON progress_findings(project_id, book_id, chapter);

CREATE TABLE progress_totals (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    book_id TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    actor_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    UNIQUE(project_id, book_id)
);

CREATE TABLE check_findings (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    book_id TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    actor_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    chapter TEXT NOT NULL,
    UNIQUE(project_id, book_id, chapter)
);

CREATE TABLE check_cache (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    book_id TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    actor_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE triage_verdicts (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    book_id TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    actor_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    UNIQUE(project_id, book_id)
);

CREATE TABLE metrics_events (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    book_id TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    actor_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX ix_metrics_events_scope ON metrics_events(project_id, book_id, created_at);

CREATE TABLE metrics_counters (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    book_id TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    actor_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE file_backups (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    book_id TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    actor_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE change_log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL,
    book_id TEXT,
    table_name TEXT NOT NULL,
    row_key TEXT NOT NULL,
    op TEXT NOT NULL,
    base_revision INTEGER,
    new_revision INTEGER,
    actor_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    journal_tx_id TEXT,
    synced_at TEXT
);
CREATE INDEX ix_change_log_project_seq ON change_log(project_id, seq);
CREATE INDEX ix_change_log_unsynced ON change_log(project_id, synced_at);

CREATE TRIGGER trg_change_log_no_delete
BEFORE DELETE ON change_log
BEGIN
    SELECT RAISE(ABORT, 'change_log is append-only: delete is not permitted');
END;

CREATE TRIGGER trg_change_log_no_update
BEFORE UPDATE ON change_log
WHEN NEW.seq IS NOT OLD.seq OR NEW.event_id IS NOT OLD.event_id
    OR NEW.project_id IS NOT OLD.project_id OR NEW.book_id IS NOT OLD.book_id
    OR NEW.table_name IS NOT OLD.table_name OR NEW.row_key IS NOT OLD.row_key
    OR NEW.op IS NOT OLD.op OR NEW.base_revision IS NOT OLD.base_revision
    OR NEW.new_revision IS NOT OLD.new_revision OR NEW.actor_id IS NOT OLD.actor_id
    OR NEW.device_id IS NOT OLD.device_id OR NEW.created_at IS NOT OLD.created_at
    OR NEW.payload_json IS NOT OLD.payload_json OR NEW.journal_tx_id IS NOT OLD.journal_tx_id
BEGIN
    SELECT RAISE(ABORT, 'change_log rows are immutable except synced_at');
END;
"""

# v2 (#77, sync readiness): a change_log row must be enough to rebuild the
# row it describes on another machine. `payload_json` is the payload the
# store wrote; the lifted columns (`kind`, `chapter`, `key`, ...) that the
# table also needs were only ever on the row itself. `columns_json` carries
# them on the event. The immutability trigger is rebuilt to cover the new
# column -- a trigger cannot be altered in place.
_MIGRATION_V2 = r"""
ALTER TABLE change_log ADD COLUMN columns_json TEXT;

DROP TRIGGER trg_change_log_no_update;
CREATE TRIGGER trg_change_log_no_update
BEFORE UPDATE ON change_log
WHEN NEW.seq IS NOT OLD.seq OR NEW.event_id IS NOT OLD.event_id
    OR NEW.project_id IS NOT OLD.project_id OR NEW.book_id IS NOT OLD.book_id
    OR NEW.table_name IS NOT OLD.table_name OR NEW.row_key IS NOT OLD.row_key
    OR NEW.op IS NOT OLD.op OR NEW.base_revision IS NOT OLD.base_revision
    OR NEW.new_revision IS NOT OLD.new_revision OR NEW.actor_id IS NOT OLD.actor_id
    OR NEW.device_id IS NOT OLD.device_id OR NEW.created_at IS NOT OLD.created_at
    OR NEW.payload_json IS NOT OLD.payload_json OR NEW.journal_tx_id IS NOT OLD.journal_tx_id
    OR NEW.columns_json IS NOT OLD.columns_json
BEGIN
    SELECT RAISE(ABORT, 'change_log rows are immutable except synced_at');
END;
"""

# v3 (#117, cross-verse alignment): Bridge-private cross-verse links.
# translationCore alignment groups are verse-local, and Bridge never fakes a
# cross-verse link inside them (semantic_alignment_guard.py, INVARIANTS ss39), so
# a reviewer's judgement that a source token of one verse is realized in
# another verse's target text has nowhere to live in `alignmentData/`. It
# lives here. A link is keyed by tC token signatures
# (`word U+241F occurrence U+241F occurrences`) plus chapter and verse on both
# sides -- never by the positional H001/T001 ids `make_inventory` regenerates
# on every load. `state` is 'active', or 'invalid' once a target edit removed
# the word the link pointed at. The UNIQUE index makes the pair idempotent;
# NULLs are distinct to SQLite, so the generic per-table tests that write rows
# without lifted columns still pass. See cross_verse_links.py.
_MIGRATION_V3 = r"""
CREATE TABLE alignment_cross_verse_links (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    book_id TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    actor_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    chapter TEXT,
    verse TEXT,
    source_signature TEXT,
    target_chapter TEXT,
    target_verse TEXT,
    target_signature TEXT,
    state TEXT
);
CREATE INDEX ix_alignment_cross_verse_links_source
    ON alignment_cross_verse_links(project_id, book_id, chapter, verse);
CREATE INDEX ix_alignment_cross_verse_links_target
    ON alignment_cross_verse_links(project_id, book_id, target_chapter, target_verse);
CREATE UNIQUE INDEX ux_alignment_cross_verse_links_pair
    ON alignment_cross_verse_links(project_id, book_id, chapter, verse, source_signature,
                                   target_chapter, target_verse, target_signature);
"""

# v4 (#169, layered-rules Phase 4.1): the persisted Language QA scan. One row
# per chapter holding every verse's raw findings (before decisions, which are
# applied on every pass), keyed by the verse's text hash and a chapter key
# over the rule pack, termbase and detected language. It survives reopen, and
# the check-job stage and live editing share it. Not in `check_cache`, because
# every reader of that table (`load_check_cache`: USFM, names, the QA report,
# triage, analytics) loads all of a book's rows, and 150 chapter payloads would
# ride along on each of those reads. `chapter` is nullable, as in v3, so the
# generic per-table tests that write rows without lifted columns still pass.
_MIGRATION_V4 = r"""
CREATE TABLE language_qa_cache (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    book_id TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    actor_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    chapter TEXT
);
CREATE UNIQUE INDEX ux_language_qa_cache_chapter
    ON language_qa_cache(project_id, book_id, chapter);
"""

# v5 (#169, layered-rules Phase 6.3): house style is a human decision kind.
# `kind` carries a CHECK constraint, which SQLite cannot alter, so the table is
# rebuilt with the widened CHECK and every row copied with its columns named
# (a later rebuild must name any column added after this one). Nothing else
# about the table changes; its one index is recreated.
_MIGRATION_V5 = r"""
CREATE TABLE human_decisions_v5 (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    book_id TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    actor_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('check','qa','verse_status','terminology','housestyle')),
    chapter TEXT,
    verse TEXT,
    key TEXT NOT NULL,
    decision TEXT,
    UNIQUE(project_id, book_id, kind, chapter, verse, key)
);
INSERT INTO human_decisions_v5(id, project_id, book_id, revision, actor_id, device_id, created_at,
                               updated_at, payload_json, kind, chapter, verse, key, decision)
    SELECT id, project_id, book_id, revision, actor_id, device_id, created_at,
           updated_at, payload_json, kind, chapter, verse, key, decision FROM human_decisions;
DROP TABLE human_decisions;
ALTER TABLE human_decisions_v5 RENAME TO human_decisions;
CREATE INDEX ix_human_decisions_scope ON human_decisions(project_id, book_id, kind);
"""

# v6 is indic-qa-editor's block, carried here verbatim (2026-10-08): that branch
# took v6 first and real project databases are already at it, so this branch's
# own tables are v7 and both branches build the same v1..v6 ladder. When the two
# merge, v6 is byte-identical on both sides.
# v6 (indic-qa editor features, 2026-10-07): three Bridge-private stores,
# designed together so the schema moves once.
#
# - language_qa_batches: one row per scoped correction ("this chapter", "this
#   book") and one per undo of it. Scripture is still written only by
#   apply_scripture_edit, once per verse; a batch row only groups those journalled
#   edits so they can be listed and undone together. Undo writes a new row and
#   moves the original's state; nothing is deleted.
# - language_qa_learned_fixes: a reviewer's single-word replacement (old -> new),
#   offered again wherever the old word recurs. A row per (book, old, new);
#   count, forget and restore re-write it (revision++), never delete it.
# - language_qa_flags: a reviewer's question on a passage. Delete is a status,
#   so change_log keeps every image.
#
# Lifted columns are nullable, as in v3/v4, so the generic per-table tests that
# write rows without them still pass.
_MIGRATION_V6 = r"""
CREATE TABLE language_qa_batches (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    book_id TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    actor_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    chapter TEXT,
    kind TEXT,
    state TEXT
);
CREATE INDEX ix_language_qa_batches_scope
    ON language_qa_batches(project_id, book_id, created_at);

CREATE TABLE language_qa_learned_fixes (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    book_id TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    actor_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    old_word TEXT,
    new_word TEXT,
    enabled INTEGER
);
CREATE UNIQUE INDEX ux_language_qa_learned_fixes_pair
    ON language_qa_learned_fixes(project_id, book_id, old_word, new_word);

CREATE TABLE language_qa_flags (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    book_id TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    actor_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    chapter TEXT,
    verse TEXT,
    status TEXT
);
CREATE INDEX ix_language_qa_flags_scope
    ON language_qa_flags(project_id, book_id, chapter, status);
"""

# v7 (#216, automatic cross-verse alignment part 1): two siblings of v3's
# links table. `alignment_null_decisions` records that a token has no
# counterpart *for a named reason* (a Greek article, a Tamil resumptive
# pronoun) -- a positive assertion, never "unaligned", which is the absence of
# a row. `alignment_verdicts` is the per-verse cache of the last automatic
# pass (#219-#221): verdict, issues, suggestions and the ledger of what it
# wrote, so a re-run can supersede its own writes and nothing else. Neither
# `side` nor `reason` carries a CHECK: v5 shows that widening one costs a table
# rebuild, and both sets are validated in Python. Lifted columns are nullable,
# as in v3/v4, so the generic per-table tests can write rows without them.
_MIGRATION_V7 = r"""
CREATE TABLE alignment_null_decisions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    book_id TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    actor_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    chapter TEXT,
    verse TEXT,
    side TEXT,
    signature TEXT,
    reason TEXT,
    state TEXT
);
CREATE INDEX ix_alignment_null_decisions_verse
    ON alignment_null_decisions(project_id, book_id, chapter, verse);
CREATE UNIQUE INDEX ux_alignment_null_decisions_token
    ON alignment_null_decisions(project_id, book_id, chapter, verse, side, signature);
CREATE TABLE alignment_verdicts (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    book_id TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    actor_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    chapter TEXT,
    verse TEXT
);
CREATE UNIQUE INDEX ux_alignment_verdicts_verse
    ON alignment_verdicts(project_id, book_id, chapter, verse);
"""

_MIGRATIONS: tuple[tuple[int, str], ...] = (
    (1, _MIGRATION_V1),
    (2, _MIGRATION_V2),
    (3, _MIGRATION_V3),
    (4, _MIGRATION_V4),
    (5, _MIGRATION_V5),
    (6, _MIGRATION_V6),
    (7, _MIGRATION_V7),
)


class WorkbenchRepository:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.read_only = False
        self._migrate()

    # -- connection discipline, copied from FoundationRepository
    # (passage_semantic_repository.py:1070-1086) --------------------------
    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # Timed (check_timing): `calls` on a check job is how many connections
        # its verses opened, the cost a reused connection would remove.
        with check_timing.current().step("workbench.connect"):
            conn = sqlite3.connect(str(self.path), timeout=5.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA synchronous = FULL")
            conn.execute("PRAGMA journal_mode = WAL")
            if self.read_only:
                conn.execute("PRAGMA query_only = ON")
            if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                conn.close()
                raise WorkbenchError("SQLite foreign-key enforcement could not be enabled")
        try:
            yield conn
        finally:
            conn.close()

    def _migrate(self) -> None:
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations("
                "version INTEGER PRIMARY KEY, schema_id TEXT NOT NULL, applied_at TEXT NOT NULL)"
            )
            current = conn.execute(
                "SELECT COALESCE(MAX(version),0) FROM schema_migrations"
            ).fetchone()[0]
            if current > WORKBENCH_SCHEMA_VERSION:
                raise WorkbenchError(
                    f"Workbench schema {current} is newer than supported v{WORKBENCH_SCHEMA_VERSION}"
                )
            for version, script in _MIGRATIONS:
                if version <= current:
                    continue
                self._backup_before_migration(conn, current, version)
                self._apply_migration(conn, version, script)
                current = version

    def _backup_before_migration(self, conn: sqlite3.Connection, current: int, target: int) -> None:
        """Create a consistent, inspectable backup before upgrading an existing DB.

        A no-op for the very first migration into a not-yet-existing file --
        there is nothing to back up yet.
        """
        if current <= 0 or not self.path.is_file():
            return
        root = self.path.parent / "backups"
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        directory = root / f"pre-workbench-v{target}-{stamp}"
        directory.mkdir(parents=True, exist_ok=False)
        backup_db = directory / "bridge-workbench.sqlite3"
        destination = sqlite3.connect(str(backup_db))
        try:
            conn.backup(destination)
        finally:
            destination.close()
        digest = hashlib.sha256(backup_db.read_bytes()).hexdigest()
        manifest = {
            "reason": f"automatic backup before workbench schema migration v{current} to v{target}",
            "schemaId": WORKBENCH_SCHEMA_ID,
            "schemaVersion": current,
            "source": str(self.path),
            "targetSchemaVersion": target,
            "sha256": digest,
            "createdAt": self._now(),
        }
        (directory / "backup-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )

    def _apply_migration(self, conn: sqlite3.Connection, version: int, script: str) -> None:
        schema_id = WORKBENCH_SCHEMA_ID.replace("'", "''")
        applied_at = self._now().replace("'", "''")
        conn.executescript(
            "BEGIN IMMEDIATE;\n"
            + script
            + f"\nINSERT INTO schema_migrations(version,schema_id,applied_at) "
              f"VALUES({version},'{schema_id}','{applied_at}');\n"
            + f"PRAGMA user_version = {version};\nCOMMIT;"
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def schema_version(self) -> int:
        with self._connect() as conn:
            return int(
                conn.execute("SELECT COALESCE(MAX(version),0) FROM schema_migrations").fetchone()[0]
            )

    def get(self, table: str, row_id: str) -> dict[str, Any] | None:
        self._require_mutable_table(table)
        with self._connect() as conn:
            row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone()
            return dict(row) if row is not None else None

    def change_log_entries(self, project_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM change_log WHERE project_id=? ORDER BY seq", (project_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def append_event(
        self,
        table: str,
        row_key: str,
        *,
        project_id: str,
        book_id: str | None,
        op: str,
        payload: dict[str, Any],
        actor_id: str,
        device_id: str,
        journal_tx_id: str | None = None,
    ) -> str:
        """Append a domain event to ``change_log`` without changing a row.

        Some records compact their own in-record history -- an issue resolution
        keeps only its last hundred entries -- while the lifecycle events behind
        them must survive forever. On disk that was a second, append-only file
        per event beside the record. Here it is a ``change_log`` row, which is
        the same thing with the immutability actually enforced rather than
        merely intended: CLAUDE.md's rule that compacting a record must not
        compact its lifecycle events.

        This is the one write that does not go through ``_write``, because there
        is no row change to make -- the event describes something that happened
        to a row, not a new version of it. ``base_revision``/``new_revision``
        are therefore left NULL, which is what distinguishes an event row from a
        row-image row when reading the log back.
        """
        self._require_mutable_table(table)
        event_id = str(uuid.uuid4())
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO change_log(event_id,project_id,book_id,table_name,row_key,op,"
                "base_revision,new_revision,actor_id,device_id,created_at,payload_json,journal_tx_id) "
                "VALUES(?,?,?,?,?,?,NULL,NULL,?,?,?,?,?)",
                (
                    event_id, project_id, book_id, table, row_key, op,
                    actor_id, device_id, self._now(),
                    json.dumps(payload, ensure_ascii=False), journal_tx_id,
                ),
            )
            conn.commit()
        return event_id

    def events_for_row(
        self, table: str, row_key: str, *, project_id: str,
    ) -> list[dict[str, Any]]:
        """Every change_log entry for one row, oldest first -- row images and
        domain events alike. This is what makes a compacted record's full
        lifecycle recoverable."""
        self._require_mutable_table(table)
        with self._connect() as conn:
            found = conn.execute(
                "SELECT * FROM change_log WHERE project_id=? AND table_name=? AND row_key=? "
                "ORDER BY seq",
                (project_id, table, row_key),
            ).fetchall()
            return [dict(row) for row in found]

    def rows(
        self,
        table: str,
        *,
        project_id: str,
        book_id: str | None = None,
        equals: dict[str, Any] | None = None,
        order_by: str = "created_at",
    ) -> list[dict[str, Any]]:
        """Every row in one table matching an exact-match filter.

        The stores this replaces were directory trees, so every read they did
        was "give me the files under this prefix" -- per verse, per chapter, per
        book. That is an equality filter on the lifted columns and nothing more,
        so this deliberately offers no ranges, no LIKE and no joins: a reader
        that needs those wants a purpose-built method on the repository, not a
        general query language leaking into `tc_project`.

        ``book_id=None`` means "any book", not "rows whose book_id is NULL" --
        every caller here is book-scoped, and a NULL-matching filter has no use
        that is not better served by passing the column in ``equals``.

        Column names are validated rather than escaped: they reach SQL by string
        interpolation because SQLite cannot parameterise an identifier, so
        anything not matching a plain lowercase identifier is refused outright.
        """
        self._require_mutable_table(table)
        equals = dict(equals or {})
        for column in (*equals.keys(), order_by):
            if not _IDENTIFIER_RE.match(column):
                raise WorkbenchValidationError(f"Unsafe column name: {column!r}")

        clauses = ["project_id=?"]
        values: list[Any] = [project_id]
        if book_id is not None:
            clauses.append("book_id=?")
            values.append(book_id)
        for column, value in equals.items():
            clauses.append(f"{column}=?")
            values.append(value)

        with self._connect() as conn:
            found = conn.execute(
                f"SELECT * FROM {table} WHERE {' AND '.join(clauses)} ORDER BY {order_by}, id",
                values,
            ).fetchall()
            return [dict(row) for row in found]

    def payloads(
        self,
        table: str,
        *,
        project_id: str,
        book_id: str | None = None,
        equals: dict[str, Any] | None = None,
        order_by: str = "created_at",
    ) -> list[dict[str, Any]]:
        """:meth:`rows`, decoded to the payloads the file stores used to hold.

        `payload_json` keeps each record's original JSON shape verbatim, so a
        reader that used to parse a file gets back exactly what it parsed
        before. A row whose payload will not decode is skipped rather than
        raising: the file readers behaved that way (a corrupt file was treated
        as absent), and one bad row must not take out a whole verse's history.
        """
        decoded: list[dict[str, Any]] = []
        for row in self.rows(
            table, project_id=project_id, book_id=book_id, equals=equals, order_by=order_by,
        ):
            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, ValueError):
                continue
            if isinstance(payload, dict):
                decoded.append(payload)
        return decoded

    @staticmethod
    def _require_mutable_table(table: str) -> None:
        if table not in MUTABLE_TABLES:
            raise WorkbenchValidationError(f"Not a workbench-mutable table: {table}")

    def _write(
        self,
        table: str,
        row_id: str,
        *,
        project_id: str,
        book_id: str | None,
        payload: dict[str, Any],
        actor_id: str,
        device_id: str,
        expected_revision: int | None,
        op: str = "upsert",
        extra_columns: dict[str, Any] | None = None,
        journal_tx_id: str | None = None,
    ) -> dict[str, Any]:
        """Insert-or-update one row with an optimistic-concurrency check.

        ``expected_revision=None`` means last-writer-wins (what every file
        writer does today); a caller can start passing real revisions later
        without a schema change (TEAM_ARCHITECTURE.md ss3.3). The revision
        check and the change_log append happen in the same BEGIN IMMEDIATE
        transaction, following the pattern in
        passage_semantic_repository.py:3717-3733 -- callers never touch
        change_log directly.

        One row, one transaction. A store that has to change many rows for
        one human action (a check job re-populating a chapter's progress)
        uses :meth:`batch`, which runs the same code against one connection
        so the whole update is a single commit and a single fsync.
        """
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            result = self._write_in(
                conn, table, row_id, project_id=project_id, book_id=book_id,
                payload=payload, actor_id=actor_id, device_id=device_id,
                expected_revision=expected_revision, op=op,
                extra_columns=extra_columns, journal_tx_id=journal_tx_id,
            )
            conn.commit()
        return result

    def _delete(
        self,
        table: str,
        row_id: str,
        *,
        project_id: str,
        book_id: str | None,
        actor_id: str,
        device_id: str,
        expected_revision: int | None = None,
    ) -> dict[str, Any] | None:
        """Delete one row, logging the deletion as a ``change_log`` event.

        Returns the write receipt, or ``None`` if there was no such row. The
        row's last image travels on the event, so the log alone still says
        what was removed -- and sync (TEAM_ARCHITECTURE.md ss7) can replay a
        deletion the same way it replays an upsert.
        """
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            result = self._delete_in(
                conn, table, row_id, project_id=project_id, book_id=book_id,
                actor_id=actor_id, device_id=device_id, expected_revision=expected_revision,
            )
            conn.commit()
        return result

    @contextmanager
    def batch(self) -> Iterator["WorkbenchBatch"]:
        """Several row writes and deletes in one transaction.

        Everything the batch does commits together or not at all, with one
        ``change_log`` row per operation exactly as the single-row paths
        write. Nothing here may touch a second database: the batch holds
        ``BEGIN IMMEDIATE`` on this one for its whole life, and a caller
        that needs to update the workspace cache afterwards does so after
        the ``with`` block, from the receipts.
        """
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield WorkbenchBatch(self, conn)
            except BaseException:
                conn.rollback()
                raise
            conn.commit()

    def max_seq(self, *, project_id: str, table: str | None = None) -> int:
        """Highest ``change_log.seq`` for a project, optionally for one table.

        ``0`` when nothing has been written. This is the ``source_seq`` the
        workspace progress cache records, so a cache entry can be compared
        against the log it was built from.
        """
        with self._connect() as conn:
            if table is None:
                row = conn.execute(
                    "SELECT COALESCE(MAX(seq),0) FROM change_log WHERE project_id=?", (project_id,),
                ).fetchone()
            else:
                self._require_mutable_table(table)
                row = conn.execute(
                    "SELECT COALESCE(MAX(seq),0) FROM change_log WHERE project_id=? AND table_name=?",
                    (project_id, table),
                ).fetchone()
            return int(row[0])

    # -- sync readiness (TEAM_ARCHITECTURE.md ss2, ss7; no server) -------------
    #
    # Sync is an exchange of change_log events. These are the local halves:
    # a cursor over unsynced events, an acknowledgement, and a JSON-lines
    # export/import for carrying a batch between machines without a hub
    # (a USB stick). Import applies events through the same revision check
    # every local write uses; a divergent row is surfaced as a conflict and
    # never overwritten -- a person resolves it, not the importer.

    def unsynced(self, *, project_id: str, after_seq: int = 0, limit: int | None = None) -> list[dict[str, Any]]:
        """change_log events not yet acknowledged, oldest first, from `after_seq`."""
        sql = (
            "SELECT * FROM change_log WHERE project_id=? AND synced_at IS NULL AND seq>? ORDER BY seq"
        )
        values: list[Any] = [project_id, int(after_seq)]
        if limit is not None:
            sql += " LIMIT ?"
            values.append(int(limit))
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(sql, values).fetchall()]

    def mark_synced(self, event_ids: list[str], *, synced_at: str | None = None) -> int:
        """Acknowledge events. The only change_log column the triggers allow."""
        ids = [str(e) for e in event_ids if e]
        if not ids:
            return 0
        stamp = synced_at or self._now()
        changed = 0
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for start in range(0, len(ids), 500):
                chunk = ids[start:start + 500]
                changed += conn.execute(
                    "UPDATE change_log SET synced_at=? WHERE synced_at IS NULL AND event_id IN ("
                    + ",".join("?" for _ in chunk) + ")",
                    [stamp, *chunk],
                ).rowcount
            conn.commit()
        return changed

    def export_events(
        self, path: str | Path, *, project_id: str, after_seq: int = 0, only_unsynced: bool = False,
    ) -> dict[str, Any]:
        """Write change_log events after `after_seq` as JSON lines, oldest first.

        One event per line, every column verbatim. `seq` travels too but is
        meaningful only to the exporting database; an importer keys on
        `event_id`.
        """
        sql = "SELECT * FROM change_log WHERE project_id=? AND seq>?"
        if only_unsynced:
            sql += " AND synced_at IS NULL"
        sql += " ORDER BY seq"
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        last_seq = int(after_seq)
        event_ids: list[str] = []
        with self._connect() as conn, destination.open("w", encoding="utf-8", newline="\n") as handle:
            for row in conn.execute(sql, (project_id, int(after_seq))):
                event = dict(row)
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
                count += 1
                last_seq = int(event["seq"])
                event_ids.append(str(event["event_id"]))
        return {"path": str(destination), "count": count, "lastSeq": last_seq, "eventIds": event_ids}

    def import_events(self, path: str | Path) -> dict[str, Any]:
        """Apply a JSON-lines event batch to this database.

        Idempotent on `event_id`: an event already in this change_log is
        skipped. Row events (`upsert`, `delete`) apply only when the local row
        is exactly at the event's `base_revision`; anything else is a
        conflict, returned rather than resolved, and the local row is left
        alone. Domain events (no revisions) are appended verbatim. Applied
        events keep their original id, actor, device and timestamp, and are
        marked synced on arrival: they came from elsewhere and have nothing
        to travel back to.
        """
        source = Path(path)
        applied = 0
        duplicates = 0
        conflicts: list[dict[str, Any]] = []
        malformed = 0
        arrived = self._now()
        with source.open("r", encoding="utf-8-sig") as handle:
            lines = [line for line in handle if line.strip()]
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for line in lines:
                try:
                    event = json.loads(line)
                except ValueError:
                    malformed += 1
                    continue
                if not isinstance(event, dict) or not event.get("event_id") or not event.get("table_name"):
                    malformed += 1
                    continue
                table = str(event["table_name"])
                if table not in MUTABLE_TABLES:
                    malformed += 1
                    continue
                event_id = str(event["event_id"])
                if conn.execute("SELECT 1 FROM change_log WHERE event_id=?", (event_id,)).fetchone():
                    duplicates += 1
                    continue
                op = str(event.get("op") or "")
                row_key = str(event.get("row_key") or "")
                base_revision = event.get("base_revision")
                new_revision = event.get("new_revision")
                common = dict(
                    project_id=str(event.get("project_id") or ""), book_id=event.get("book_id"),
                    actor_id=str(event.get("actor_id") or ""), device_id=str(event.get("device_id") or ""),
                    event_id=event_id, created_at=str(event.get("created_at") or arrived), synced_at=arrived,
                )
                is_row_event = new_revision is not None or op == "delete"
                if not is_row_event:
                    conn.execute(
                        "INSERT INTO change_log(event_id,project_id,book_id,table_name,row_key,op,"
                        "base_revision,new_revision,actor_id,device_id,created_at,payload_json,"
                        "journal_tx_id,columns_json,synced_at) VALUES(?,?,?,?,?,?,NULL,NULL,?,?,?,?,?,?,?)",
                        (
                            event_id, common["project_id"], common["book_id"], table, row_key, op,
                            common["actor_id"], common["device_id"], common["created_at"],
                            str(event.get("payload_json") or "{}"), event.get("journal_tx_id"),
                            event.get("columns_json"), arrived,
                        ),
                    )
                    applied += 1
                    continue
                local = conn.execute(f"SELECT revision FROM {table} WHERE id=?", (row_key,)).fetchone()
                local_revision = int(local["revision"]) if local is not None else None
                expected = int(base_revision) if base_revision is not None else None
                if local_revision != expected:
                    conflicts.append({
                        "eventId": event_id, "table": table, "rowKey": row_key, "op": op,
                        "baseRevision": expected, "localRevision": local_revision,
                        "reason": "missing_base" if local_revision is None else "revision_mismatch",
                    })
                    continue
                if op == "delete":
                    self._delete_in(
                        conn, table, row_key, expected_revision=expected, **common,
                    )
                else:
                    try:
                        payload = json.loads(str(event.get("payload_json") or "{}"))
                        columns = json.loads(event["columns_json"]) if event.get("columns_json") else {}
                    except ValueError:
                        malformed += 1
                        continue
                    self._write_in(
                        conn, table, row_key, payload=payload if isinstance(payload, dict) else {},
                        expected_revision=expected, op=op or "upsert",
                        extra_columns=columns if isinstance(columns, dict) else {},
                        journal_tx_id=event.get("journal_tx_id"), **common,
                    )
                applied += 1
            conn.commit()
        return {
            "path": str(source), "applied": applied, "duplicates": duplicates,
            "conflicts": conflicts, "malformed": malformed,
        }

    def _delete_in(
        self,
        conn: sqlite3.Connection,
        table: str,
        row_id: str,
        *,
        project_id: str,
        book_id: str | None,
        actor_id: str,
        device_id: str,
        expected_revision: int | None,
        event_id: str | None = None,
        created_at: str | None = None,
        synced_at: str | None = None,
    ) -> dict[str, Any] | None:
        self._require_mutable_table(table)
        row = conn.execute(f"SELECT revision, payload_json FROM {table} WHERE id=?", (row_id,)).fetchone()
        if row is None:
            return None
        base_revision = int(row["revision"])
        if expected_revision is not None and expected_revision != base_revision:
            raise WorkbenchConflict(f"{table}:{row_id} revision conflict")
        changed = conn.execute(
            f"DELETE FROM {table} WHERE id=? AND revision=?", (row_id, base_revision),
        ).rowcount
        if changed != 1:
            raise WorkbenchConflict(f"{table}:{row_id} revision conflict")
        event_id = event_id or str(uuid.uuid4())
        cursor = conn.execute(
            "INSERT INTO change_log(event_id,project_id,book_id,table_name,row_key,op,"
            "base_revision,new_revision,actor_id,device_id,created_at,payload_json,journal_tx_id,"
            "columns_json,synced_at) VALUES(?,?,?,?,?,?,?,NULL,?,?,?,?,NULL,NULL,?)",
            (
                event_id, project_id, book_id, table, row_id, "delete",
                base_revision, actor_id, device_id, created_at or self._now(), row["payload_json"],
                synced_at,
            ),
        )
        return {"id": row_id, "revision": None, "eventId": event_id, "seq": int(cursor.lastrowid)}

    def _write_in(
        self,
        conn: sqlite3.Connection,
        table: str,
        row_id: str,
        *,
        project_id: str,
        book_id: str | None,
        payload: dict[str, Any],
        actor_id: str,
        device_id: str,
        expected_revision: int | None,
        op: str = "upsert",
        extra_columns: dict[str, Any] | None = None,
        journal_tx_id: str | None = None,
        event_id: str | None = None,
        created_at: str | None = None,
        synced_at: str | None = None,
    ) -> dict[str, Any]:
        self._require_mutable_table(table)
        extra_columns = extra_columns or {}
        for column in extra_columns:
            if not _IDENTIFIER_RE.match(column):
                raise WorkbenchValidationError(f"Unsafe column name: {column!r}")
        now = created_at or self._now()
        payload_text = json.dumps(payload, ensure_ascii=False)
        if True:  # kept one level deep so the body below reads as it did under `with`
            row = conn.execute(f"SELECT revision FROM {table} WHERE id=?", (row_id,)).fetchone()
            if row is None:
                if expected_revision not in (None, 0):
                    raise WorkbenchConflict(f"{table}:{row_id} does not exist yet")
                base_revision = None
                new_revision = 1
                columns = [
                    "id", "project_id", "book_id", "revision", "actor_id",
                    "device_id", "created_at", "updated_at", "payload_json",
                    *extra_columns.keys(),
                ]
                values = [
                    row_id, project_id, book_id, new_revision, actor_id,
                    device_id, now, now, payload_text, *extra_columns.values(),
                ]
                placeholders = ",".join("?" for _ in values)
                conn.execute(
                    f"INSERT INTO {table}({','.join(columns)}) VALUES({placeholders})", values,
                )
            else:
                base_revision = int(row["revision"])
                if expected_revision is not None and expected_revision != base_revision:
                    raise WorkbenchConflict(f"{table}:{row_id} revision conflict")
                new_revision = base_revision + 1
                assignments = ["revision=?", "actor_id=?", "device_id=?", "updated_at=?", "payload_json=?"]
                values: list[Any] = [new_revision, actor_id, device_id, now, payload_text]
                for column, value in extra_columns.items():
                    assignments.append(f"{column}=?")
                    values.append(value)
                values.extend([row_id, base_revision])
                changed = conn.execute(
                    f"UPDATE {table} SET {','.join(assignments)} WHERE id=? AND revision=?", values,
                ).rowcount
                if changed != 1:
                    raise WorkbenchConflict(f"{table}:{row_id} revision conflict")
            event_id = event_id or str(uuid.uuid4())
            cursor = conn.execute(
                "INSERT INTO change_log(event_id,project_id,book_id,table_name,row_key,op,"
                "base_revision,new_revision,actor_id,device_id,created_at,payload_json,journal_tx_id,"
                "columns_json,synced_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    event_id, project_id, book_id, table, row_id, op,
                    base_revision, new_revision, actor_id, device_id, now, payload_text, journal_tx_id,
                    json.dumps(extra_columns, ensure_ascii=False) if extra_columns else None,
                    synced_at,
                ),
            )
        return {"id": row_id, "revision": new_revision, "eventId": event_id, "seq": int(cursor.lastrowid)}


class WorkbenchBatch:
    """The handle :meth:`WorkbenchRepository.batch` yields: the single-row
    write and delete with the transaction already open. Receipts carry the
    same fields as the single-row paths, including ``seq``."""

    def __init__(self, repository: WorkbenchRepository, conn: sqlite3.Connection):
        self._repository = repository
        self._conn = conn
        # One transaction, one timestamp: every row and event the batch
        # writes carries the same created_at unless the caller supplies its
        # own (sync import does, to keep the originating machine's). Without
        # this, two rows written microseconds apart in the same commit sort
        # apart by created_at, which is not what "one change" means.
        self._created_at = repository._now()

    def get(self, table: str, row_id: str) -> dict[str, Any] | None:
        """One row as this transaction sees it -- including rows the batch
        itself has already written."""
        self._repository._require_mutable_table(table)
        row = self._conn.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone()
        return dict(row) if row is not None else None

    def write(
        self,
        table: str,
        row_id: str,
        *,
        project_id: str,
        book_id: str | None,
        payload: dict[str, Any],
        actor_id: str,
        device_id: str,
        expected_revision: int | None = None,
        op: str = "upsert",
        extra_columns: dict[str, Any] | None = None,
        journal_tx_id: str | None = None,
    ) -> dict[str, Any]:
        return self._repository._write_in(
            self._conn, table, row_id, project_id=project_id, book_id=book_id,
            payload=payload, actor_id=actor_id, device_id=device_id,
            expected_revision=expected_revision, op=op,
            extra_columns=extra_columns, journal_tx_id=journal_tx_id,
            created_at=self._created_at,
        )

    def delete(
        self,
        table: str,
        row_id: str,
        *,
        project_id: str,
        book_id: str | None,
        actor_id: str,
        device_id: str,
        expected_revision: int | None = None,
    ) -> dict[str, Any] | None:
        return self._repository._delete_in(
            self._conn, table, row_id, project_id=project_id, book_id=book_id,
            actor_id=actor_id, device_id=device_id, expected_revision=expected_revision,
            created_at=self._created_at,
        )
