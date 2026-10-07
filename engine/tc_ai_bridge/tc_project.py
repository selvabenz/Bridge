from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import tempfile
import time
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from collections import Counter

from .usfm import strip_usfm, whitespace_tokens

from .models import VerseAlignment
from .transaction_journal import TransactionJournal
from .workbench_repository import WorkbenchIdentity, WorkbenchRepository, natural_row_id
from .workspace_repository import WorkspaceRepository
import sqlite3
from urllib.parse import quote as _url_quote
from .paratext_notes import append_paratext_note, validate_notes_11, convert_comment_list_to_notes_11, convert_legacy_notes_11, EXTERNAL_NOTE_SOURCE
from .alignment_reliability import structural_issues, alignment_fingerprint


class ProjectError(RuntimeError):
    pass


def _read_json(path: Path) -> Any:
    with path.open('r', encoding='utf-8-sig') as f:
        return json.load(f)


def _replace_retrying(temp_name: str, path: Path, *, seconds: float = 2.0) -> None:
    """`os.replace`, retried briefly while Windows says the target is in use.

    On Windows a replace fails with `PermissionError` (WinError 5 / 32) if ANY
    handle holds the destination -- Python's `open()` does not request
    FILE_SHARE_DELETE. A reader that only holds the file for a few milliseconds
    is still enough: Language QA's background pass reads every chapter of the
    book, so a Scripture edit landing inside that window was refused outright
    (#184, seen as `[WinError 5] Access is denied` on `rut/1.json` in CI).

    Retrying rather than coordinating with the scanner, because the scanner is
    not the only thing that opens these files: on Windows an antivirus or the
    search indexer will do the same, and no amount of internal quiescing helps
    there.

    Deliberately NOT silent: after `seconds` the original error is raised. A
    permission problem that is actually permanent must still fail loudly rather
    than be absorbed by a retry loop.

    This does not weaken the write. `os.replace` remains atomic; retrying only
    chooses a later moment to perform it, and the temp file has already been
    written, fsynced and validated by the caller.
    """
    deadline = time.monotonic() + seconds
    while True:
        try:
            os.replace(temp_name, path)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            # A flat 1 ms poll, not exponential backoff. The reader holds the
            # file for single-digit milliseconds, so what matters is noticing
            # quickly that it has let go, not being gentle about asking. Backoff
            # doubling to 100 ms meant a miss needing five attempts idled ~155 ms
            # after the file was already free, and on two contended CI cores that
            # took this write's p95 from 112 ms to 410 ms.
            time.sleep(0.001)


def _write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        # Validate the exact bytes before replacement.
        _read_json(Path(temp_name))
        _replace_retrying(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


@dataclass
class ProjectSummary:
    path: Path
    book_id: str
    book_name: str
    target_language: str
    tc_version: str
    edit_version: str

    @property
    def display_name(self) -> str:
        return f'{self.book_name} ({self.book_id}) — {self.target_language}'


_PARATEXT_SYNC_STATE_KEY = 'paratext_live_sync_state'

# Directories that held human-owned records before #76 moved those stores into
# `bridge-workbench.sqlite3`. A project containing any of them was written by a
# pre-cutover build and is not upgraded -- see TEAM_ARCHITECTURE.md ss3.5 for why
# there is no migration. `audit/` is deliberately absent, and stays absent now
# that Bridge no longer writes it: it was always a derived shadow of records
# held elsewhere, never a store in its own right. Projects on disk still carry
# those tails, and refusing to open on them would reject projects whose actual
# stores are fine.
_PRE_CUTOVER_STORE_DIRS = (
    'decisions', 'qaDecisions', 'review', 'terminology', 'aiReview',
    'issueResolutions', 'alignmentHistory', 'alignmentDiagnostics',
    'semanticMappings', 'semanticValidation',
    # #77: the derived stores. Rebuildable in principle, but a project
    # carrying them was reviewed on a build whose check results, triage
    # verdicts and progress this build cannot see, and showing "not checked"
    # for a checked book is the same silent-empty failure.
    'checkFindings', 'triage', 'metrics',
)
# Single files, relative to the project root, that #77 moved the same way.
# The progress rollup lived under `.bridge/`, not the companion dir, and
# the dashboard read it for siblings that were never opened -- so a project
# carrying one was written by a build whose review progress this build
# cannot see.
_PRE_CUTOVER_STORE_FILES = (
    '.bridge/progress.json',
    '.apps/translationCoreAI/checkCache.json',
)


class TranslationCoreProject:
    def __init__(
        self, project_path: str | Path, *,
        identity: WorkbenchIdentity | None = None,
        workspace: WorkspaceRepository | None = None,
        reviewer_name: str = '',
    ):
        self.path = Path(project_path).resolve()
        manifest_path = self.path / 'manifest.json'
        if not manifest_path.exists():
            raise ProjectError(f'Not a translationCore project: {self.path}')
        self.manifest = _read_json(manifest_path)
        self.book_id = str(self.manifest.get('project', {}).get('id', '')).lower()
        if not self.book_id:
            raise ProjectError('manifest.json has no project.id')
        self.tc_dir = self.path / '.apps' / 'translationCore'
        self.alignment_dir = self.tc_dir / 'alignmentData' / self.book_id
        self.index_dir = self.tc_dir / 'index'
        self.check_dir = self.tc_dir / 'checkData'
        self.book_dir = self.path / self.book_id
        if not self.alignment_dir.exists():
            raise ProjectError(f'Missing alignmentData for {self.book_id}')
        self._index_cache: dict[str, list[dict[str, Any]]] = {}
        self._checks_by_verse_cache: dict[tuple[str, str], list[dict[str, Any]]] | None = None
        self.journal = TransactionJournal(self.path, self.companion_dir())
        self._refuse_pre_cutover_project()
        self.workbench = WorkbenchRepository(self.companion_dir() / 'bridge-workbench.sqlite3')
        self._cross_verse_links = None  # CrossVerseLinkStore, built on first use (#117)
        # Identity for every workbench write. Injectable so a test can stamp a
        # known actor without touching app-level state; resolved lazily
        # otherwise, because working it out opens the workspace database and
        # most project operations never write anything.
        # The name the person chose in Settings, passed down by BridgeEngine
        # (#78). Empty means "nobody told us", and `identity` then falls back to
        # the OS account exactly as before.
        self._reviewer_name = str(reviewer_name or '').strip()
        self._identity: WorkbenchIdentity | None = identity
        # The app-level workspace database (users, devices, the progress
        # cache). BridgeEngine owns one and injects it so every project it
        # opens shares it; a project constructed directly resolves the
        # installation default lazily, and only when something needs it.
        self._workspace: WorkspaceRepository | None = workspace
        # Stage 4 attaches an advisory companion runtime after project identity
        # is resolved. Direct TranslationCoreProject users remain unchanged.
        self.passage_semantic_runtime: Any | None = None

    def _refuse_pre_cutover_project(self) -> None:
        """Refuse to open a project whose records predate the #76 cutover.

        Opening it would succeed and show an empty review queue, empty decision
        history and no saved issue resolutions, because the new readers query a
        database those records were never written to. That looks exactly like
        data loss. A refusal naming the reason is worse for nobody and much
        easier to act on.

        Nothing is deleted: the directories stay untouched and the project can
        be re-imported alongside them.
        """
        companion = self.companion_dir()
        stale: list[str] = []
        if companion.is_dir():
            stale.extend(sorted(
                name for name in _PRE_CUTOVER_STORE_DIRS
                if any((companion / name).glob('**/*.json'))
            ))
        stale.extend(rel for rel in _PRE_CUTOVER_STORE_FILES if (self.path / rel).is_file())
        if not stale:
            return
        raise ProjectError(
            'This project was created by an earlier version of Bridge and stores '
            'its review data in files (' + ', '.join(stale) + '). Bridge now keeps '
            'them in the project database and does not convert the old format. '
            'Re-import the project to open it. Nothing has been deleted -- the '
            'original files are still in '
            + str(self.path) + '.'
        )

    def attach_passage_semantic_runtime(self, runtime: Any | None) -> None:
        self.passage_semantic_runtime = runtime

    @property
    def workspace(self) -> WorkspaceRepository:
        if self._workspace is None:
            from .secret_store import _default_app_root
            self._workspace = WorkspaceRepository(_default_app_root() / 'workspace.sqlite3')
        return self._workspace

    @property
    def workbench_identity(self) -> WorkbenchIdentity:
        """Who is writing, and to which project, for every workbench row.

        Resolved once, on first write. `.bridge/project.json` carries the
        stable `projectId` the registry also knows the project by, so workbench
        rows key to the same identity the rest of the app uses. A project
        written before that file existed falls back to a deterministic id
        derived from its path -- stable across opens on one machine, which is
        all a pre-release project needs, and never a fresh uuid4 (that would
        detach a project's rows from themselves on every open).

        The actor is the local user from the workspace database, not
        `settings.reviewer_name`: `change_log` rows can never be edited, so
        attribution has to survive the reviewer renaming themselves.
        """
        if self._identity is None:
            from .secret_store import AppSettings

            project_id = ''
            try:
                marker = _read_json(self.path / '.bridge' / 'project.json')
                project_id = str(marker.get('projectId') or '') if isinstance(marker, dict) else ''
            except Exception:
                project_id = ''
            if not project_id:
                project_id = 'path:' + hashlib.sha256(
                    str(self.path).casefold().encode('utf-8')
                ).hexdigest()[:32]

            workspace = self.workspace
            # The name the person chose in Settings wins; the OS account is only
            # the seed for someone who has never chosen one (V11-005).
            #
            # #78: this used to pass `_seed_reviewer_name()` unconditionally, so
            # the users row was always the OS account and a Settings rename
            # never reached it — `reviewer_name` and `users.display_name` were
            # two competing answers to "who is this". The user_id was stable
            # throughout, so no history detached; only the name shown was wrong.
            user = workspace.get_or_create_local_user(
                self._reviewer_name or AppSettings._seed_reviewer_name()
            )
            self._identity = WorkbenchIdentity(
                project_id=project_id, book_id=self.book_id,
                actor_id=user['userId'], device_id=workspace.get_or_create_device_id(),
            )
        return self._identity

    @property
    def summary(self) -> ProjectSummary:
        target = self.manifest.get('target_language', {})
        return ProjectSummary(
            self.path,
            self.book_id,
            str(self.manifest.get('project', {}).get('name', self.book_id)),
            str(target.get('name') or target.get('id') or ''),
            str(self.manifest.get('tc_version', '')),
            str(self.manifest.get('tc_edit_version', '')),
        )

    def chapters(self) -> list[str]:
        chapters = [x.stem for x in self.alignment_dir.glob('*.json') if x.stem.isdigit()]
        return sorted(chapters, key=int)

    def chapter_path(self, chapter: str | int) -> Path:
        return self.alignment_dir / f'{chapter}.json'

    def load_alignment_chapter(self, chapter: str | int) -> dict[str, Any]:
        p = self.chapter_path(chapter)
        if not p.exists():
            raise ProjectError(f'Missing alignment chapter: {p}')
        data = _read_json(p)
        if not isinstance(data, dict):
            raise ProjectError(f'Invalid alignment chapter JSON: {p}')
        return data

    def verses(self, chapter: str | int) -> list[str]:
        data = self.load_alignment_chapter(chapter)
        def key(v: str):
            if str(v).isdigit(): return (0, int(v))
            if str(v) == 'front': return (-1, 0)
            return (1, str(v))
        return sorted((str(x) for x in data.keys()), key=key)

    def load_verse_alignment(self, chapter: str | int, verse: str | int) -> VerseAlignment:
        chapter_data = self.load_alignment_chapter(chapter)
        raw = chapter_data.get(str(verse))
        if raw is None:
            raise ProjectError(f'No alignment data for {self.book_id} {chapter}:{verse}')
        return VerseAlignment.from_dict(raw)

    def target_chapter(self, chapter: str | int) -> dict[str, Any]:
        p = self.book_dir / f'{chapter}.json'
        if not p.exists():
            return {}
        data = _read_json(p)
        return data if isinstance(data, dict) else {}

    def target_verse_text(self, chapter: str | int, verse: str | int) -> str:
        return str(self.target_chapter(chapter).get(str(verse), ''))

    def chapter_headings(self, chapter: str | int) -> dict[str, list[dict[str, str]]]:
        """Section headings of `chapter`, keyed by the verse each introduces.

        Kept beside the verse map rather than inside it (#180): a heading is not
        a translation of any source word, so it must never reach the alignable
        target tokens, but it is still the team's text and belongs on screen.
        Absent for projects imported before #180 and for books with no headings
        -- an empty dict, not an error.
        """
        p = self.book_dir / f'{chapter}.headings.json'
        if not p.exists():
            return {}
        data = _read_json(p)
        if not isinstance(data, dict):
            return {}
        return {
            str(verse): [h for h in items if isinstance(h, dict)]
            for verse, items in data.items()
            if isinstance(items, list)
        }

    def usfm_path(self) -> Path | None:
        candidates = list(self.path.glob('*.usfm')) + list(self.path.glob('*.USFM')) + list(self.path.glob('*.sfm')) + list(self.path.glob('*.SFM'))
        return candidates[0] if candidates else None

    def check_types(self) -> dict[str, int]:
        out: dict[str, int] = {}
        if self.check_dir.exists():
            for p in sorted(self.check_dir.iterdir()):
                if p.is_dir():
                    out[p.name] = sum(1 for _ in p.rglob('*.json'))
        return out

    def index_tools(self) -> dict[str, int]:
        out: dict[str, int] = {}
        if self.index_dir.exists():
            for p in sorted(self.index_dir.iterdir()):
                if p.is_dir():
                    out[p.name] = sum(1 for _ in p.rglob('*.json'))
        return out

    def _load_index_tool(self, tool: str) -> list[dict[str, Any]]:
        if tool in self._index_cache:
            return self._index_cache[tool]
        book_dir = self.index_dir / tool / self.book_id
        entries: list[dict[str, Any]] = []
        if book_dir.exists():
            for p in book_dir.glob('*.json'):
                if p.name == 'contextId.json':
                    continue
                try:
                    data = _read_json(p)
                except Exception:
                    continue
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and 'contextId' in item:
                            item = copy.deepcopy(item)
                            item['_index_file'] = p.name
                            entries.append(item)
        self._index_cache[tool] = entries
        return entries

    def _checks_by_verse(self) -> dict[tuple[str, str], list[dict[str, Any]]]:
        """Build a persistent in-memory verse index once per project session.

        The original translationCore indexes are grouped by check category rather than verse.
        Re-scanning thousands of entries for every verse is acceptable for one book but scales
        poorly to Bible-size projects, so production mode materializes this read-only lookup.
        """
        if self._checks_by_verse_cache is not None:
            return self._checks_by_verse_cache
        by: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for tool in ('translationNotes', 'translationWords'):
            for entry in self._load_index_tool(tool):
                ref = entry.get('contextId', {}).get('reference', {}) if isinstance(entry, dict) else {}
                key = (str(ref.get('chapter')), str(ref.get('verse')))
                by.setdefault(key, []).append(entry)
        self._checks_by_verse_cache = by
        return by

    def checks_for_verse(self, chapter: str | int, verse: str | int) -> list[dict[str, Any]]:
        return list(self._checks_by_verse().get((str(chapter), str(verse)), ()))

    def invalidate_index_cache(self) -> None:
        """Invalidate materialized check indexes after a translationCore index write."""
        self._index_cache.clear()
        self._checks_by_verse_cache = None

    def _state_files_for_verse(self, state_type: str, chapter: str | int, verse: str | int) -> list[Path]:
        p = self.check_dir / state_type / self.book_id / str(chapter) / str(verse)
        return sorted(p.glob('*.json')) if p.exists() else []

    def check_state_for_verse(self, chapter: str | int, verse: str | int) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {}
        for typ in ('selections', 'invalidated', 'comments', 'verseEdits'):
            vals = []
            for p in self._state_files_for_verse(typ, chapter, verse):
                try:
                    d = _read_json(p)
                    if isinstance(d, dict):
                        d['_file'] = str(p)
                        vals.append(d)
                except Exception:
                    pass
            out[typ] = vals
        return out

    def _serializable_check_state(self, chapter: str | int, verse: str | int) -> dict[str, list[dict[str, Any]]]:
        """Return tC check-state content without local filesystem paths, for deterministic fingerprints."""
        raw = self.check_state_for_verse(chapter, verse)
        out: dict[str, list[dict[str, Any]]] = {}
        for key, values in raw.items():
            clean = []
            for item in values:
                if not isinstance(item, dict):
                    continue
                d = copy.deepcopy(item)
                d.pop('_file', None)
                clean.append(d)
            out[key] = clean
        return out

    def decisions_for_verse(self, chapter: str | int, verse: str | int) -> list[dict[str, Any]]:
        return self._human_decision_payloads(kind='check', chapter=chapter, verse=verse)

    def review_input_fingerprint(self, chapter: str | int, verse: str | int) -> str:
        """Fingerprint all project inputs that can materially change an AI verse review.

        It intentionally excludes model output and timestamps. A Scripture/alignment/check-state/
        project-version/human-decision change makes a cached review stale.
        """
        alignment_raw = self.load_alignment_chapter(chapter).get(str(verse), {})
        checks = []
        for entry in self.checks_for_verse(chapter, verse):
            if not isinstance(entry, dict):
                continue
            checks.append({
                'contextId': copy.deepcopy(entry.get('contextId', {})),
                'selections': copy.deepcopy(entry.get('selections', False)),
                'nothingToSelect': bool(entry.get('nothingToSelect', False)),
                'invalidated': copy.deepcopy(entry.get('invalidated', False)),
                'verseEdits': copy.deepcopy(entry.get('verseEdits', False)),
                'comments': copy.deepcopy(entry.get('comments', False)),
            })
        version_manifest = {
            k: copy.deepcopy(v) for k, v in self.manifest.items()
            if k.startswith('tc_') or k in ('source_translations', 'target_language', 'resource', 'project')
        }
        resource_dirs = {}
        helps = self.path.parent.parent / 'resources' / 'en' / 'translationHelps'
        if helps.exists():
            for name in ('translationAcademy','translationNotes','translationWords','translationWordsLinks'):
                rd=helps/name
                if rd.exists():
                    vals=[]
                    for q in sorted((x for x in rd.iterdir() if x.is_dir()), key=lambda x:x.name):
                        try: vals.append((q.name,q.stat().st_mtime_ns))
                        except Exception: vals.append((q.name,0))
                    resource_dirs[name]=vals
        payload = {
            'bookId': self.book_id,
            'chapter': str(chapter),
            'verse': str(verse),
            'targetText': self.target_verse_text(chapter, verse),
            'alignment': alignment_raw,
            'checks': checks,
            'checkState': self._serializable_check_state(chapter, verse),
            'manifestVersions': version_manifest,
            'installedTranslationHelps': resource_dirs,
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
        return hashlib.sha256(encoded).hexdigest()

    def ai_review_cache_status(self, chapter: str | int, verse: str | int) -> str:
        """Return missing | current | stale for the locally cached AI review."""
        saved = self.load_ai_review_result(chapter, verse)
        if not saved:
            return 'missing'
        if str(saved.get('batchState') or 'complete') != 'complete':
            return 'stale'
        saved_fp = str(saved.get('inputFingerprint') or '')
        if not saved_fp:
            return 'stale'
        try:
            return 'current' if saved_fp == self.review_input_fingerprint(chapter, verse) else 'stale'
        except Exception:
            return 'stale'

    def mark_ai_review_incomplete(
        self, chapter: str | int, verse: str | int, state: str = 'cancelled'
    ) -> None:
        """Prevent a cancelled in-flight result from being treated as resumably complete."""
        d = self.load_ai_review_result(chapter, verse)
        if d is None:
            return
        d['batchState'] = str(state or 'incomplete')
        d['batchStateTimestamp'] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        self._write_ai_review_payload(chapter, verse, d)

    def _write_ai_review_payload(
        self, chapter: str | int, verse: str | int, payload: dict[str, Any],
    ) -> str:
        """Upsert one verse's AI review result and return its row id.

        One result per verse, so `(book, chapter, verse)` is the natural key --
        the same thing the one-file-per-verse layout encoded in its path. The
        read-modify-write callers around this (cancellation, fingerprint
        rebasing, selection outcomes) rewrite the whole payload rather than
        appending to what they read, which is what keeps a re-run idempotent.
        """
        identity = self.workbench_identity
        row_id = natural_row_id(
            identity.project_id, self.book_id, 'ai_review', str(chapter), str(verse),
        )
        self.workbench._write(
            'ai_review_results', row_id,
            project_id=identity.project_id, book_id=self.book_id, payload=payload,
            actor_id=identity.actor_id, device_id=identity.device_id,
            expected_revision=None,
            extra_columns={
                'chapter': str(chapter), 'verse': str(verse),
                'input_fingerprint': str(payload.get('inputFingerprint') or ''),
                'generated_at': str(payload.get('generatedTimestamp') or ''),
            },
        )
        return row_id

    @staticmethod
    def _alignment_work_state(alignment: VerseAlignment) -> str:
        aligned_bottom = alignment.aligned_bottom()
        if not aligned_bottom:
            return 'untouched'
        incomplete_top = any(g.top_words and not g.bottom_words for g in alignment.alignments)
        if alignment.word_bank or incomplete_top:
            return 'partial'
        return 'complete'

    def alignment_work_state(self, chapter: str | int, verse: str | int) -> str:
        return self._alignment_work_state(self.load_verse_alignment(chapter, verse))

    def verse_work_state(self, chapter: str | int, verse: str | int) -> str:
        states = self.check_state_for_verse(chapter, verse)
        indexed = self.checks_for_verse(chapter, verse)
        if self.word_alignment_state(chapter, verse) == 'invalid':
            return 'STALE_AFTER_VERSE_EDIT'
        if any(
            self.check_staleness(
                chapter, verse,
                str(e.get('contextId', {}).get('checkId', '')),
                str(e.get('contextId', {}).get('tool', '')),
                str(e.get('contextId', {}).get('groupId', '')),
            ) == 'stale'
            for e in indexed
        ):
            return 'STALE_AFTER_VERSE_EDIT'
        if states.get('invalidated') or any(bool(e.get('invalidated')) for e in indexed):
            return 'STALE_RECHECK_REQUIRED'
        review_state = self.load_review_state(chapter, verse)
        if review_state and str(review_state.get('status', '')).lower() in ('approved', 'human_approved'):
            return 'HUMAN_APPROVED'
        cache = self.ai_review_cache_status(chapter, verse)
        if cache == 'current':
            return 'AI_PREPARED'
        alignment_state = self._alignment_work_state(self.load_verse_alignment(chapter, verse))
        checked = any(isinstance(e.get('selections'), list) or bool(e.get('nothingToSelect')) for e in indexed)
        if alignment_state == 'untouched' and not checked:
            return 'UNTOUCHED'
        return 'PARTIALLY_WORKED'

    def project_scan(self) -> dict[str, Any]:
        """Scan existing translationCore + companion state for reviewer planning.

        This is intentionally deterministic and makes no API calls.
        """
        result: dict[str, Any] = {
            'bookId': self.book_id,
            'verses': 0,
            'alignment': {'complete': 0, 'partial': 0, 'untouched': 0},
            'translationNotes': {'total': 0, 'checked': 0, 'pending': 0, 'invalidated': 0},
            'translationWords': {'total': 0, 'checked': 0, 'pending': 0, 'invalidated': 0},
            'aiReview': {'current': 0, 'stale': 0, 'missing': 0},
            'workState': {},
            'humanDecisions': {'accepted': 0, 'needs_discussion': 0, 'rejected': 0, 'other': 0},
            'verseEdits': 0,
            'comments': 0,
            'pendingTransactions': len(self.pending_transactions()),
        }
        for ch in self.chapters():
            # One parse of this chapter's alignment file for the whole verse
            # loop (#143). `load_verse_alignment` is `load_alignment_chapter`
            # plus a dict lookup and caches nothing, so calling it per verse
            # re-parsed the same file once per verse -- 1533 parses for Genesis
            # instead of 50, and 78 of project.report's 130 seconds. Every key
            # of this dict is exactly what `verses()` returns (it sorts these
            # same keys), so the lookup below cannot miss.
            chapter_alignments = self.load_alignment_chapter(ch)
            for vs in self.verses(ch):
                if str(vs) == 'front':
                    continue
                result['verses'] += 1
                alignment_state = self._alignment_work_state(
                    VerseAlignment.from_dict(chapter_alignments[str(vs)])
                )
                result['alignment'][alignment_state] += 1
                for entry in self.checks_for_verse(ch, vs):
                    tool = str(entry.get('contextId', {}).get('tool') or '')
                    bucket = result.get(tool)
                    if not isinstance(bucket, dict):
                        continue
                    bucket['total'] += 1
                    invalid = bool(entry.get('invalidated', False))
                    cid = str(entry.get('contextId',{}).get('checkId',''))
                    stale = self.check_staleness(
                        ch, vs, cid, tool,
                        str(entry.get('contextId', {}).get('groupId', '')),
                    ) == 'stale'
                    checked = isinstance(entry.get('selections'), list) or bool(entry.get('nothingToSelect', False))
                    if invalid or stale:
                        bucket['invalidated'] += 1
                    if checked and not invalid and not stale:
                        bucket['checked'] += 1
                    else:
                        bucket['pending'] += 1
                cache = self.ai_review_cache_status(ch, vs)
                result['aiReview'][cache] += 1
                work = self.verse_work_state(ch, vs)
                result['workState'][work] = int(result['workState'].get(work, 0)) + 1
                states = self.check_state_for_verse(ch, vs)
                result['verseEdits'] += len(states.get('verseEdits', []))
                result['comments'] += len(states.get('comments', []))
        for d in self.project_decisions():
            decision = str(d.get('decision') or '').lower()
            key = decision if decision in ('accepted', 'needs_discussion', 'rejected') else 'other'
            result['humanDecisions'][key] += 1
        return result

    def alignment_lock_state(self, chapter: str | int, verse: str | int) -> str:
        """Classify existing work for v0.7.4 without rewriting it."""
        alignment = self.load_verse_alignment(chapter, verse)
        review = self.load_review_state(chapter, verse) or {}
        if str(review.get('status', '')).lower() in ('approved', 'human_approved'):
            return 'HARD_LOCK'
        state = self._alignment_work_state(alignment)
        if state == 'complete' or self.word_alignment_state(chapter, verse) == 'completed':
            return 'PROTECTED_LEGACY'
        if state == 'partial':
            return 'PARTIAL_PROTECTED'
        return 'OPEN'

    def alignment_compatibility_scan(self) -> dict[str, Any]:
        """Read-only v0.7.4 compatibility scan for existing alignment work."""
        result: dict[str, Any] = {
            'bookId': self.book_id,
            'verses': 0,
            'hardLocked': 0,
            'protectedLegacy': 0,
            'partialProtected': 0,
            'open': 0,
            'stale': 0,
            'structuralReview': 0,
            'malformed': 0,
            'exceptions': [],
            'filesModified': 0,
        }
        for ch in self.chapters():
            for vs in self.verses(ch):
                if str(vs) == 'front':
                    continue
                result['verses'] += 1
                try:
                    alignment = self.load_verse_alignment(ch, vs)
                    issues = structural_issues(alignment)
                    lock = self.alignment_lock_state(ch, vs)
                    if lock == 'HARD_LOCK': result['hardLocked'] += 1
                    elif lock == 'PROTECTED_LEGACY': result['protectedLegacy'] += 1
                    elif lock == 'PARTIAL_PROTECTED': result['partialProtected'] += 1
                    else: result['open'] += 1
                    stale = self.word_alignment_state(ch, vs) == 'invalid'
                    if stale:
                        result['stale'] += 1
                    if issues:
                        result['structuralReview'] += 1
                    if stale or issues:
                        result['exceptions'].append({
                            'chapter': str(ch), 'verse': str(vs), 'lock': lock,
                            'stale': stale, 'issues': issues,
                        })
                except Exception as exc:
                    result['malformed'] += 1
                    result['exceptions'].append({
                        'chapter': str(ch), 'verse': str(vs), 'lock': 'UNKNOWN',
                        'stale': False, 'issues': [str(exc)],
                    })
        return result

    def ensure_v073_migration_snapshot(self) -> Path:
        """Create a hash-only compatibility snapshot; never rewrites alignment/project data."""
        root = self.companion_dir() / 'migration' / 'pre-v073'
        path = root / f'{self.book_id}.json'
        if path.exists():
            return path
        chapters: dict[str, Any] = {}
        for ch in self.chapters():
            cp = self.chapter_path(ch)
            try:
                raw = cp.read_bytes()
                chapters[str(ch)] = {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
            except Exception as exc:
                chapters[str(ch)] = {'error': str(exc)}
        data = {
            'bookId': self.book_id,
            'projectPath': str(self.path),
            'createdTimestamp': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            'purpose': 'v0.7.4 existing-work protection snapshot; hashes only; no project data rewritten',
            'chapters': chapters,
        }
        _write_json_atomic(path, data)
        return path

    def record_alignment_diagnostic(self, chapter: str | int, verse: str | int, payload: dict[str, Any]) -> str:
        """Persist non-secret compiler diagnostics for field reliability analysis."""
        iso = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        safe = iso.replace(':', '_').replace('.', '_')
        data = {
            'bookId': self.book_id, 'chapter': str(chapter), 'verse': str(verse),
            'timestamp': iso, 'app': 'translationCore AI Bridge', 'schemaVersion': 1,
            **copy.deepcopy(payload),
        }
        # Append-only: the timestamp is part of the key, so every diagnostic is
        # its own row rather than overwriting the previous one for that verse.
        identity = self.workbench_identity
        row_id = natural_row_id(
            identity.project_id, self.book_id, 'alignment_diagnostic',
            str(chapter), str(verse), safe,
        )
        self.workbench._write(
            'alignment_diagnostics', row_id,
            project_id=identity.project_id, book_id=self.book_id, payload=data,
            actor_id=identity.actor_id, device_id=identity.device_id,
            expected_revision=None,
            extra_columns={'chapter': str(chapter), 'verse': str(verse)},
        )
        return row_id

    def recover_incomplete_transactions(self) -> list[dict[str, Any]]:
        """Roll back any transaction left unfinished by a prior crash/power loss."""
        out = self.journal.recover_all()
        if out:
            self._index_cache.clear(); self._checks_by_verse_cache = None
        return out

    def pending_transactions(self) -> list[dict[str, Any]]:
        return self.journal.pending()

    def sync_comment(self, chapter: str | int, verse: str | int, context_id: dict[str, Any], text: str, username: str = 'AI Bridge Reviewer', gateway_language_code: str = 'en', gateway_language_quote: str = '') -> Path:
        """Append a native translationCore comment using the observed checkData/comments shape.

        This never completes a check. It is appropriate for reviewer discussion notes and
        rejection rationale attached to a concrete TN/TW contextId.
        """
        text=str(text).strip()
        if not text:
            raise ProjectError('Comment text may not be empty.')
        ctx=copy.deepcopy(context_id or {})
        ref=ctx.get('reference',{}) if isinstance(ctx,dict) else {}
        if str(ref.get('bookId',self.book_id)).lower()!=self.book_id or str(ref.get('chapter'))!=str(chapter) or str(ref.get('verse'))!=str(verse):
            raise ProjectError('Comment contextId does not match the current verse.')
        if str(ctx.get('tool','')) not in ('translationNotes','translationWords'):
            raise ProjectError('Native translationCore comments require a Translation Notes/Words contextId.')
        iso,safe=self._timestamp()
        path=self.check_dir/'comments'/self.book_id/str(chapter)/str(verse)/f'{safe}.json'
        rec={
            'text':text,'username':username,'activeBook':self.book_id,
            'activeChapter':int(chapter) if str(chapter).isdigit() else str(chapter),
            'activeVerse':int(verse) if str(verse).isdigit() else str(verse),
            'modifiedTimestamp':iso,'gatewayLanguageCode':gateway_language_code,
            'gatewayLanguageQuote':gateway_language_quote,'contextId':ctx,
        }
        tx=self.journal.begin('tcCommentSync',[path]); self.journal.mark_writing(tx)
        try:
            _write_json_atomic(path,rec)
            reread=_read_json(path)
            if reread.get('contextId',{}).get('checkId')!=ctx.get('checkId'):
                raise ProjectError('Post-write translationCore comment verification failed.')
            self.journal.commit(tx,{'operation':'tcCommentSync','checkId':ctx.get('checkId','')})
        except Exception as e:
            self.journal.rollback(tx,str(e)); raise
        return path


    def _target_language_code(self) -> str:
        target = self.manifest.get('target_language') if isinstance(self.manifest, dict) else None
        if isinstance(target, dict):
            value = target.get('id') or target.get('language_id') or target.get('identifier')
            if value:
                return str(value).strip()
        for key in ('target_language_id', 'language_id', 'languageId'):
            value = self.manifest.get(key) if isinstance(self.manifest, dict) else None
            if value:
                return str(value).strip()
        return 'und'

    def _legacy_paratext_notes_path(self) -> Path:
        return self.companion_dir() / 'paratextNotes' / f'{self.book_id}.notes.xml'

    def _legacy_paratext_commentlist_path(self) -> Path:
        return self.companion_dir() / 'paratextNotes' / f'{self.book_id}.comments.xml'

    def record_paratext_note(self, chapter: str | int, verse: str | int, text: str, username: str = 'AI Bridge Reviewer', selected_text: str = '', note_type: str = '', metadata: dict[str, Any] | None = None, assigned_user: str = '', reply_to_user: str = '', ext_user: str = EXTERNAL_NOTE_SOURCE, thread_id: str = '') -> Path:
        """Record an API-ready Paratext Notes 1.1 project note.

        ``username`` is stored as the best-known Paratext author hint in the companion XML. Live
        Plugin API synchronization does not impersonate that value: Paratext itself records the
        current logged-in Paratext user as the real Project Note author. API-ready XML export also
        normalizes ``comment@user`` to a real detected/configured Paratext member. ``ext_user``
        identifies the external AI origin. The target Scripture text is never modified by note
        creation.
        """
        path = self.paratext_notes_path()
        old_commentlist = self._legacy_paratext_commentlist_path()
        old_notes = self._legacy_paratext_notes_path()

        # Upgrade existing companion data once. v0.7.1 wrote CommentList exports; v0.7.0 wrote
        # Notes 1.1. Preserve the old files and create the corrected primary Notes file.
        if not path.exists():
            if old_commentlist.exists():
                convert_comment_list_to_notes_11(old_commentlist, path, ext_user=ext_user)
            elif old_notes.exists():
                convert_legacy_notes_11(old_notes, path, language=self._target_language_code())

        tx = self.journal.begin('paratextNote', [path]); self.journal.mark_writing(tx)
        try:
            out, thread_id = append_paratext_note(
                path, book_id=self.book_id, chapter=chapter, verse=verse,
                verse_text=self.target_verse_text(chapter, verse), comment_text=text, reviewer=username,
                selected_text=selected_text, language=self._target_language_code(), note_type=str((metadata or {}).get('paratextThreadType') or ''),
                assigned_user=assigned_user, reply_to_user=reply_to_user, metadata=metadata, ext_user=ext_user,
                thread_id=thread_id or None,
            )
            check = validate_notes_11(out)
            self.journal.commit(tx, {'operation': 'paratextNotes11', 'threadId': thread_id, 'threads': check['threads'], 'comments': check['comments']})
            return out
        except Exception as e:
            self.journal.rollback(tx, str(e)); raise

    def paratext_notes_path(self) -> Path:
        # This is a Bridge companion/export filename only. Direct Paratext synchronization uses
        # the project GUID + Notes 1.1 API and does not depend on a guessed Paratext project file.
        return self.companion_dir() / 'paratextNotes' / 'Notes_AI_Suggestion.xml'

    def paratext_note_sync_state_path(self) -> Path:
        return self.companion_dir() / 'paratextNotes' / 'live_sync_state.json'

    def load_paratext_note_sync_state(self) -> dict[str, Any]:
        # `paratext_note_sync_state_path()` is kept: the *notes XML* beside it
        # stays on disk in Paratext's own format (TEAM_ARCHITECTURE ss3.4), and
        # callers still use that directory. Only this small state record moved.
        found = self.workbench.payloads(
            'project_state', project_id=self.workbench_identity.project_id,
            book_id=self.book_id, equals={'key': _PARATEXT_SYNC_STATE_KEY},
        )
        data = found[0] if found else None
        if not isinstance(data, dict):
            return {'version': 1, 'items': {}}
        items = data.get('items')
        if not isinstance(items, dict):
            items = {}
        return {'version': 1, 'items': dict(items)}

    def save_paratext_note_sync_state(self, data: dict[str, Any]) -> str:
        payload = {'version': 1, 'items': dict((data or {}).get('items') or {})}
        identity = self.workbench_identity
        row_id = natural_row_id(
            identity.project_id, self.book_id, _PARATEXT_SYNC_STATE_KEY,
        )
        self.workbench._write(
            'project_state', row_id,
            project_id=identity.project_id, book_id=self.book_id, payload=payload,
            actor_id=identity.actor_id, device_id=identity.device_id,
            expected_revision=None,
            extra_columns={'key': _PARATEXT_SYNC_STATE_KEY},
        )
        return row_id

    @staticmethod
    def _validate_issue_resolution_id(resolution_id: str) -> str:
        """The id came from a filename, so it was validated to stop a caller
        escaping the directory. It is a row key now and cannot traverse
        anything, but the check stays: it is also what stops a malformed id
        being silently stored as a new record rather than rejected."""
        rid = str(resolution_id or '').strip().lower()
        if len(rid) != 32 or any(ch not in '0123456789abcdef' for ch in rid):
            raise ProjectError('Invalid issue resolution ID.')
        return rid

    def _issue_resolution_id(
        self, chapter: str | int, verse: str | int, tool: str, group_id: str, check_id: str,
    ) -> str:
        identity = '\u241f'.join((self.book_id, str(chapter), str(verse), str(tool), str(group_id), str(check_id)))
        return hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]

    def load_issue_resolution(
        self, chapter: str | int, verse: str | int, resolution_id: str,
    ) -> dict[str, Any]:
        self._validate_issue_resolution_id(resolution_id)
        for data in self.list_issue_resolutions(chapter, verse):
            if str(data.get('resolutionId') or '') == str(resolution_id):
                return data
        raise ProjectError('Issue resolution was not found for this verse.')

    def list_issue_resolutions(self, chapter: str | int, verse: str | int) -> list[dict[str, Any]]:
        # `issue_resolutions` deliberately lifts only status columns
        # (TEAM_ARCHITECTURE ss3.1), not chapter/verse, so the verse filter is
        # applied here rather than in SQL. These are human-created and there are
        # a handful per book, so scanning the book costs nothing worth a schema
        # change; if that ever stops being true, lift the columns rather than
        # adding an index to a query that cannot use one.
        out = [
            payload for payload in self.workbench.payloads(
                'issue_resolutions', project_id=self.workbench_identity.project_id,
                book_id=self.book_id,
            )
            if str(payload.get('chapter') or '') == str(chapter)
            and str(payload.get('verse') or '') == str(verse)
        ]
        return sorted(out, key=lambda item: str(item.get('updatedAt') or ''), reverse=True)

    def save_issue_resolution(
        self,
        chapter: str | int,
        verse: str | int,
        tool: str,
        group_id: str,
        check_id: str,
        expected_fingerprint: str,
        *,
        selected_text: str = '',
        issue_summary: str = '',
        reviewer_note: str = '',
        proposed_correction: str = '',
        evidence: list[Any] | None = None,
        username: str = 'Bridge Reviewer',
    ) -> dict[str, Any]:
        """Create/update one human-owned resolution record for a native tN/tW check."""
        current = self.check_review(chapter, verse, tool, group_id, check_id)
        if not expected_fingerprint or expected_fingerprint != current['stateFingerprint']:
            raise ProjectError('The translation check changed after it was opened. Reload before recording its resolution.')
        summary = str(issue_summary or '').strip()
        note = str(reviewer_note or '').strip()
        selected = str(selected_text or '').strip()
        correction = str(proposed_correction or '').strip()
        if not summary:
            raise ProjectError('Describe the translation issue before creating a resolution.')
        if not note:
            raise ProjectError('Add a reviewer note before handing the issue to Paratext.')
        if selected and not self._selection_ranges(self.target_verse_text(chapter, verse), selected):
            raise ProjectError('The selected target text is not present in the current verse.')
        clean_evidence: list[Any] = []
        for item in list(evidence or [])[:25]:
            if isinstance(item, dict):
                clean_evidence.append(copy.deepcopy(item))
            elif str(item or '').strip():
                clean_evidence.append(str(item).strip())
        resolution_id = self._issue_resolution_id(chapter, verse, tool, group_id, check_id)
        try:
            existing = self.load_issue_resolution(chapter, verse, resolution_id)
        except ProjectError:
            existing = {}
        now, _ = self._timestamp()
        history = list(existing.get('history') or [])
        history.append({
            'event': 'updated' if existing else 'created',
            'at': now,
            'by': str(username or 'Bridge Reviewer'),
            'inputFingerprint': self.review_input_fingerprint(chapter, verse),
        })
        old_handoff = existing.get('paratext') if isinstance(existing.get('paratext'), dict) else {}
        content_signature = hashlib.sha256(json.dumps({
            'selectedText': selected, 'issueSummary': summary, 'reviewerNote': note,
            'proposedCorrection': correction, 'evidence': clean_evidence,
        }, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
        paratext = copy.deepcopy(old_handoff)
        if str(paratext.get('contentSignature') or '') != content_signature:
            paratext = {
                'status': 'not_queued', 'messageId': '', 'attempts': 0,
                'lastError': '', 'sentAt': '', 'remoteId': '',
                'contentSignature': content_signature,
            }
        record = {
            'schemaVersion': 1,
            'resolutionId': resolution_id,
            'bookId': self.book_id,
            'chapter': str(chapter),
            'verse': str(verse),
            'reference': f'{self.book_id.upper()} {chapter}:{verse}',
            'check': {
                'tool': str(tool), 'groupId': str(group_id), 'checkId': str(check_id),
                'sourceQuote': current.get('sourceQuote', ''),
                'sourceOccurrence': current.get('sourceOccurrence'),
                'stateFingerprint': current['stateFingerprint'],
            },
            'selectedText': selected,
            'issueSummary': summary,
            'reviewerNote': note,
            'proposedCorrection': correction,
            'evidence': clean_evidence,
            'status': str(existing.get('status') or 'open'),
            'recheck': copy.deepcopy(existing.get('recheck') or {'status': 'not_run'}),
            'paratext': paratext,
            'createdAt': str(existing.get('createdAt') or now),
            'updatedAt': now,
            'history': history[-100:],
        }
        self._save_issue_resolution_row(record, op='updated' if existing else 'created')
        return record

    def _save_issue_resolution_row(self, record: dict[str, Any], *, op: str) -> str:
        identity = self.workbench_identity
        resolution_id = self._validate_issue_resolution_id(str(record.get('resolutionId') or ''))
        row_id = natural_row_id(
            identity.project_id, self.book_id, 'issue_resolution', resolution_id,
        )
        paratext = record.get('paratext') if isinstance(record.get('paratext'), dict) else {}
        recheck = record.get('recheck') if isinstance(record.get('recheck'), dict) else {}
        self.workbench._write(
            'issue_resolutions', row_id,
            project_id=identity.project_id, book_id=self.book_id, payload=record,
            actor_id=identity.actor_id, device_id=identity.device_id,
            expected_revision=None, op=op,
            extra_columns={
                'status': str(record.get('status') or ''),
                'recheck_status': str(recheck.get('status') or ''),
                'paratext_status': str(paratext.get('status') or ''),
            },
        )
        return row_id

    def update_issue_resolution_paratext(
        self, chapter: str | int, verse: str | int, resolution_id: str,
        handoff: dict[str, Any], event: str,
    ) -> dict[str, Any]:
        record = self.load_issue_resolution(chapter, verse, resolution_id)
        now, _ = self._timestamp()
        record['paratext'] = copy.deepcopy(handoff)
        record['updatedAt'] = now
        lifecycle = {'event': str(event), 'at': now, 'messageId': str(handoff.get('messageId') or '')}
        history = list(record.get('history') or [])
        history.append(copy.deepcopy(lifecycle))
        record['history'] = history[-100:]
        self._save_issue_resolution_row(record, op=str(event))
        self._record_issue_resolution_lifecycle_audit(record, lifecycle)
        return record

    def _record_issue_resolution_lifecycle_audit(
        self, record: dict[str, Any], event: dict[str, Any],
    ) -> None:
        """Keep lifecycle events append-only even when the record history is compacted.

        `record['history']` is capped at the last hundred entries, so the events
        behind a long-lived resolution would otherwise fall off the end. This
        used to be a second, append-only file per event beside the record; it is
        a change_log row now, which is the same guarantee with the immutability
        actually enforced by triggers rather than by convention.
        """
        identity = self.workbench_identity
        event_name = str(event.get('event') or 'recheck')
        self.workbench.append_event(
            'issue_resolutions', str(record.get('resolutionId') or ''),
            project_id=identity.project_id, book_id=self.book_id,
            op='lifecycle:' + event_name,
            payload={
                'schemaVersion': 1,
                'resolutionId': record.get('resolutionId'),
                'reference': record.get('reference'),
                **copy.deepcopy(event),
            },
            actor_id=identity.actor_id, device_id=identity.device_id,
        )

    def mark_issue_resolutions_recheck(
        self, chapter: str | int, verse: str | int, state: str, *,
        reason: str = '', error: str = '',
    ) -> list[dict[str, Any]]:
        """Move every saved issue on a verse through a durable recheck state."""
        allowed = {'stale', 'running', 'failed', 'cancelled'}
        normalized = str(state or '').strip().lower()
        if normalized not in allowed:
            raise ProjectError(f'Unsupported issue-resolution recheck state: {state}')
        records = self.list_issue_resolutions(chapter, verse)
        updated: list[dict[str, Any]] = []
        for record in records:
            now, _ = self._timestamp()
            previous_status = str(record.get('status') or 'open')
            previous_recheck = record.get('recheck') if isinstance(record.get('recheck'), dict) else {}
            attempt = int(previous_recheck.get('attempt') or 0)
            if normalized == 'running':
                attempt += 1
            recheck = copy.deepcopy(previous_recheck)
            recheck.update({
                'status': normalized,
                'attempt': attempt,
                'inputFingerprint': self.review_input_fingerprint(chapter, verse),
                'reason': str(reason or recheck.get('reason') or ''),
                'error': str(error or ''),
            })
            if normalized == 'running':
                recheck['startedAt'] = now
                recheck['completedAt'] = ''
            elif normalized in {'failed', 'cancelled'}:
                recheck['completedAt'] = now
            if normalized == 'stale':
                # A prior pass cannot remain authoritative after Scripture changes.
                record['status'] = 'open'
                recheck['staleAt'] = now
            elif normalized in {'failed', 'cancelled'} and previous_status == 'resolved':
                record['status'] = 'open'
            record['recheck'] = recheck
            record['updatedAt'] = now
            event = {
                'event': f'recheck_{normalized}', 'at': now,
                'previousStatus': previous_status,
                'inputFingerprint': recheck['inputFingerprint'],
                'reason': recheck.get('reason', ''), 'error': recheck.get('error', ''),
            }
            history = list(record.get('history') or [])
            history.append(copy.deepcopy(event))
            record['history'] = history[-100:]
            self._save_issue_resolution_row(record, op='recheck_' + normalized)
            self._record_issue_resolution_lifecycle_audit(record, event)
            updated.append(record)
        return updated

    def reconcile_issue_resolutions_after_ai_review(
        self, chapter: str | int, verse: str | int, reviews: list[dict[str, Any]], *,
        model: str = '', summary: str = '', allow_automatic_resolution: bool = True,
    ) -> list[dict[str, Any]]:
        """Resolve or reflag saved issues from a completed, current AI review."""
        by_identity = {
            (
                str(item.get('tool') or ''), str(item.get('group_id') or ''),
                str(item.get('check_id') or ''),
            ): item
            for item in list(reviews or []) if isinstance(item, dict)
        }
        updated: list[dict[str, Any]] = []
        for record in self.list_issue_resolutions(chapter, verse):
            check = record.get('check') if isinstance(record.get('check'), dict) else {}
            review = by_identity.get((
                str(check.get('tool') or ''), str(check.get('groupId') or ''),
                str(check.get('checkId') or ''),
            ))
            verdict = str((review or {}).get('verdict') or 'review').lower()
            confidence = float((review or {}).get('confidence') or 0.0)
            evidence = copy.deepcopy(list((review or {}).get('evidence_used') or []))
            safely_grounded_pass = (
                allow_automatic_resolution and verdict in {'pass', 'not_applicable'}
                and confidence >= 0.82 and bool(evidence)
            )
            safely_grounded_problem = verdict == 'problem' and confidence >= 0.70 and bool(evidence)
            if safely_grounded_pass:
                status, recheck_status, event_name = 'resolved', 'resolved', 'recheck_resolved'
            elif safely_grounded_problem:
                status, recheck_status, event_name = 'reflagged', 'reflagged', 'recheck_reflagged'
            else:
                status, recheck_status, event_name = 'open', 'needs_review', 'recheck_needs_review'
            now, _ = self._timestamp()
            previous_status = str(record.get('status') or 'open')
            previous_recheck = record.get('recheck') if isinstance(record.get('recheck'), dict) else {}
            recheck = {
                'status': recheck_status,
                'attempt': max(1, int(previous_recheck.get('attempt') or 0)),
                'verdict': verdict,
                'confidence': confidence,
                'rationale': str((review or {}).get('rationale') or (
                    'The completed AI review did not return this check; human review is required.'
                )),
                'suggestedCorrection': str((review or {}).get('suggested_correction') or ''),
                'evidence': evidence,
                'model': str(model or ''),
                'summary': str(summary or ''),
                'inputFingerprint': self.review_input_fingerprint(chapter, verse),
                'startedAt': str(previous_recheck.get('startedAt') or ''),
                'completedAt': now,
                'error': '',
            }
            record['status'] = status
            record['recheck'] = recheck
            record['updatedAt'] = now
            event = {
                'event': event_name, 'at': now, 'previousStatus': previous_status,
                'status': status, 'verdict': verdict, 'confidence': recheck['confidence'],
                'inputFingerprint': recheck['inputFingerprint'], 'model': recheck['model'],
            }
            history = list(record.get('history') or [])
            history.append(copy.deepcopy(event))
            record['history'] = history[-100:]
            self._save_issue_resolution_row(record, op=event_name)
            self._record_issue_resolution_lifecycle_audit(record, event)
            updated.append(record)
        return updated

    def comments_for_check(self, chapter: str | int, verse: str | int, check_id: str) -> list[dict[str, Any]]:
        out=[]
        for item in self.check_state_for_verse(chapter,verse).get('comments',[]):
            if str(item.get('contextId',{}).get('checkId',''))==str(check_id): out.append(item)
        return out

    def companion_dir(self) -> Path:
        return self.path / '.apps' / 'translationCoreAI'

    def backup_chapter(self, chapter: str | int) -> Path:
        src = self.chapter_path(chapter)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
        dst = self.companion_dir() / 'backups' / stamp / 'alignmentData' / self.book_id / src.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        self._index_backups([dst], label='alignmentData', stamp=stamp)
        return dst

    # -- backups index --------------------------------------------------------
    #
    # The backup files themselves stay on disk (TEAM_ARCHITECTURE.md ss3.4:
    # crash recovery must not depend on a database opening). `file_backups`
    # is only an index over them, so a reader can ask "which backups exist
    # for this chapter" with one query instead of walking `backups/`.

    def _index_backups(self, files: list[Path], *, label: str, stamp: str) -> None:
        if not files:
            return
        identity = self.workbench_identity
        root = (self.companion_dir() / 'backups').resolve()
        with self.workbench.batch() as batch:
            for file in files:
                resolved = file.resolve()
                try:
                    relative = resolved.relative_to(root).as_posix()
                except ValueError:
                    relative = resolved.as_posix()
                batch.write(
                    'file_backups', natural_row_id(identity.project_id, self.book_id, 'file_backup', relative),
                    project_id=identity.project_id, book_id=self.book_id,
                    payload={
                        'path': str(resolved), 'relativePath': relative, 'label': label,
                        'stamp': stamp, 'name': resolved.name,
                    },
                    actor_id=identity.actor_id, device_id=identity.device_id,
                )

    def save_verse_alignment(
        self,
        chapter: str | int,
        verse: str | int,
        value: VerseAlignment,
        expected_original: dict[str, Any] | None = None,
        operation: str = 'save',
    ) -> Path:
        chapter_path = self.chapter_path(chapter)
        chapter_data = self.load_alignment_chapter(chapter)
        current = copy.deepcopy(chapter_data.get(str(verse)))
        if expected_original is not None:
            if current != expected_original:
                raise ProjectError('The alignment file changed on disk after it was loaded. Reload before saving to avoid overwriting another edit.')
        # Validate model serialization before touching disk.
        raw = value.to_dict()
        self._validate_verse_raw(raw)
        backup = self.backup_chapter(chapter)
        iso, safe = self._timestamp()
        # The journal protects the tC chapter file; history used to be a file
        # enrolled alongside it, so a rollback removed both. A database row
        # cannot join a file transaction, but the guarantee that mattered is
        # preserved by keeping the row write *inside* the try: if history cannot
        # be recorded the whole operation fails and the chapter write rolls
        # back, so an alignment never changes without a history entry
        # (test_alignment_history_failure_rolls_back_chapter_write).
        #
        # What is genuinely no longer atomic is the reverse direction: if
        # `journal.commit` itself fails after the row is written, the chapter
        # rolls back and the row is orphaned. That window is much narrower than
        # the one it replaces -- commit only writes a small journal file once
        # the real work is done -- and `journal_tx_id` on the row is what makes
        # such a row identifiable as belonging to a transaction that did not
        # complete.
        tx = self.journal.begin('saveApprovedAlignment', [chapter_path])
        self.journal.mark_writing(tx)
        try:
            chapter_data[str(verse)] = raw
            _write_json_atomic(chapter_path, chapter_data)
            # Re-read and validate after write.
            written = self.load_alignment_chapter(chapter).get(str(verse))
            self._validate_verse_raw(written)
            self._record_alignment_history(
                chapter, verse, operation, backup, current, raw,
                history_id=f'{safe}_{operation}.json', timestamp=iso,
                journal_tx_id=tx.transaction_id,
            )
            self.journal.commit(tx, {'operation':'saveApprovedAlignment','chapter':str(chapter),'verse':str(verse)})
        except Exception as e:
            self.journal.rollback(tx, str(e)); raise
        return backup

    def set_language_qa_pack(self, pack: str) -> str:
        """Write the project's Language QA setting, manifest `language_qa.pack`:
        "auto", "off" or a registered pack name (the caller validates it). The
        manifest is re-read inside the transaction so no other key is touched;
        translationCore ignores keys it does not know."""
        manifest_path = self.path / 'manifest.json'
        tx = self.journal.begin('languageQaSetPack', [manifest_path])
        self.journal.mark_writing(tx)
        try:
            manifest = _read_json(manifest_path)
            block = manifest.get('language_qa') if isinstance(manifest.get('language_qa'), dict) else {}
            manifest['language_qa'] = {**block, 'pack': pack}
            _write_json_atomic(manifest_path, manifest)
            self.manifest = manifest
            self.journal.commit(tx, {'operation': 'languageQaSetPack', 'pack': pack})
        except Exception as e:
            self.journal.rollback(tx, str(e)); raise
        return pack

    @property
    def cross_verse_links(self):
        """The Bridge-private cross-verse link store (#117), over this project's
        workbench database. Lazy: most project operations never touch it."""
        if self._cross_verse_links is None:
            from .cross_verse_links import CrossVerseLinkStore
            self._cross_verse_links = CrossVerseLinkStore(self)
        return self._cross_verse_links

    @staticmethod
    def _bottom_signatures(raw: dict[str, Any]) -> set[str]:
        """Every target-token signature a raw tC verse holds, grouped or banked."""
        out: set[str] = set()
        for group in raw.get('alignments', []) or []:
            for token in group.get('bottomWords', []) or []:
                out.add(f"{token.get('word','')}\u241f{token.get('occurrence',1)}\u241f{token.get('occurrences',1)}")
        for token in raw.get('wordBank', []) or []:
            out.add(f"{token.get('word','')}\u241f{token.get('occurrence',1)}\u241f{token.get('occurrences',1)}")
        return out

    def _record_alignment_history(
        self,
        chapter: str | int,
        verse: str | int,
        operation: str,
        backup: Path,
        before: Any,
        after: Any,
        *,
        history_id: str = '',
        timestamp: str = '',
        journal_tx_id: str | None = None,
    ) -> str:
        iso = timestamp
        if not iso:
            iso, safe = self._timestamp()
            history_id = f'{safe}_{operation}.json'
        if not history_id:
            raise ProjectError('Alignment history destination was not created.')
        # The id keeps the old filename shape on purpose: it is what
        # `restore_verse_alignment_history` is given by the UI, so changing it
        # would invalidate every history id a running client already holds
        # (TEAM_ARCHITECTURE ss3.1). `backupPath` still points at a real file --
        # `backups/` deliberately stays on disk (ss3.4), because crash recovery
        # must not depend on the database opening.
        payload = {
            'id': history_id,
            'bookId': self.book_id,
            'chapter': str(chapter),
            'verse': str(verse),
            'operation': operation,
            'timestamp': iso,
            'backupPath': str(backup),
            'beforeFingerprint': hashlib.sha256(
                json.dumps(before, sort_keys=True, ensure_ascii=False).encode('utf-8')
            ).hexdigest(),
            'afterFingerprint': hashlib.sha256(
                json.dumps(after, sort_keys=True, ensure_ascii=False).encode('utf-8')
            ).hexdigest(),
        }
        identity = self.workbench_identity
        row_id = natural_row_id(
            identity.project_id, self.book_id, 'alignment_history',
            str(chapter), str(verse), history_id,
        )
        self.workbench._write(
            'alignment_history', row_id,
            project_id=identity.project_id, book_id=self.book_id, payload=payload,
            actor_id=identity.actor_id, device_id=identity.device_id,
            expected_revision=None, journal_tx_id=journal_tx_id,
            extra_columns={
                'chapter': str(chapter), 'verse': str(verse), 'backup_path': str(backup),
            },
        )
        return history_id

    def _alignment_history_entries(
        self, chapter: str | int, verse: str | int,
    ) -> list[dict[str, Any]]:
        """Newest first, matching the reverse-sorted directory listing.

        The id embeds a sortable timestamp, so ordering by it reproduces what
        `sorted(root.glob('*.json'), reverse=True)` produced.
        """
        entries = [
            value for value in self.workbench.payloads(
                'alignment_history', project_id=self.workbench_identity.project_id,
                book_id=self.book_id,
                equals={'chapter': str(chapter), 'verse': str(verse)},
            )
            if value.get('backupPath')
        ]
        return sorted(entries, key=lambda value: str(value.get('id') or ''), reverse=True)

    def alignment_history(self, chapter: str | int, verse: str | int) -> list[dict[str, Any]]:
        return [
            {
                'id': str(value.get('id') or ''),
                'operation': str(value.get('operation') or 'save'),
                'timestamp': str(value.get('timestamp') or ''),
            }
            for value in self._alignment_history_entries(chapter, verse)
        ]

    def restore_verse_alignment_history(
        self,
        chapter: str | int,
        verse: str | int,
        history_id: str = '',
        expected_original: dict[str, Any] | None = None,
    ) -> Path:
        candidates = self._alignment_history_entries(chapter, verse)
        if history_id:
            candidates = [value for value in candidates if str(value.get('id') or '') == history_id]
        if not candidates:
            raise ProjectError('No saved alignment change is available to restore for this verse.')
        entry = candidates[0]
        if not isinstance(entry, dict) or not entry.get('backupPath'):
            raise ProjectError('Alignment history entry is invalid.')
        backup = Path(str(entry['backupPath'])).resolve()
        allowed_root = (self.companion_dir() / 'backups').resolve()
        try:
            backup.relative_to(allowed_root)
        except ValueError as exc:
            raise ProjectError('Alignment history points outside the project backup directory.') from exc
        if not backup.is_file() or backup.name != f'{chapter}.json':
            raise ProjectError('The saved alignment backup is missing or belongs to another chapter.')
        backup_chapter = _read_json(backup)
        restored = backup_chapter.get(str(verse)) if isinstance(backup_chapter, dict) else None
        self._validate_verse_raw(restored)
        return self.save_verse_alignment(
            chapter,
            verse,
            VerseAlignment.from_dict(restored),
            expected_original=expected_original,
            operation='restore',
        )

    @staticmethod
    def _validate_verse_raw(raw: Any) -> None:
        if not isinstance(raw, dict):
            raise ProjectError('Verse alignment must be an object')
        if not isinstance(raw.get('alignments'), list) or not isinstance(raw.get('wordBank'), list):
            raise ProjectError('Verse alignment must contain alignments[] and wordBank[]')
        for i, group in enumerate(raw['alignments']):
            if not isinstance(group, dict) or not isinstance(group.get('topWords'), list) or not isinstance(group.get('bottomWords'), list):
                raise ProjectError(f'Invalid alignment group {i}')
            for side in ('topWords', 'bottomWords'):
                for token in group[side]:
                    if not isinstance(token, dict) or not token.get('word'):
                        raise ProjectError(f'Invalid token in group {i}/{side}')
                    if int(token.get('occurrence', 0)) < 1 or int(token.get('occurrences', 0)) < 1:
                        raise ProjectError(f'Invalid occurrence metadata in group {i}/{side}')
        for token in raw['wordBank']:
            if not isinstance(token, dict) or not token.get('word'):
                raise ProjectError('Invalid wordBank token')


    def list_alignment_backups(self, chapter: str | int) -> list[Path]:
        """Newest first. Read from the `file_backups` index rather than a
        directory walk; an indexed file that has since gone is skipped."""
        suffix = f'/alignmentData/{self.book_id}/{chapter}.json'
        found: list[Path] = []
        for payload in self.workbench.payloads(
            'file_backups', project_id=self.workbench_identity.project_id, book_id=self.book_id,
        ):
            relative = str(payload.get('relativePath') or '')
            if payload.get('label') != 'alignmentData' or not relative.endswith(suffix):
                continue
            path = Path(str(payload.get('path') or ''))
            if path.is_file():
                found.append(path)
        return sorted(found, key=lambda x: x.parts[-4], reverse=True)

    def restore_alignment_backup(self, chapter: str | int, backup_path: str | Path) -> Path:
        backup = Path(backup_path).resolve()
        allowed_root = (self.companion_dir() / 'backups').resolve()
        try:
            backup.relative_to(allowed_root)
        except ValueError as e:
            raise ProjectError('Refusing to restore a file outside translationCoreAI/backups.') from e
        if not backup.exists() or backup.name != f'{chapter}.json':
            raise ProjectError('Selected backup does not match the current chapter.')
        data = _read_json(backup)
        if not isinstance(data, dict):
            raise ProjectError('Backup is not a valid alignment chapter object.')
        for raw in data.values():
            self._validate_verse_raw(raw)
        # Preserve the current chapter before rolling back so restore itself is reversible.
        safety_backup = self.backup_chapter(chapter)
        _write_json_atomic(self.chapter_path(chapter), data)
        reread = self.load_alignment_chapter(chapter)
        if reread != data:
            raise ProjectError('Post-restore verification failed.')
        return safety_backup

    def load_review_state(self, chapter: str | int, verse: str | int) -> dict[str, Any] | None:
        found = self._human_decision_payloads(
            kind='verse_status', chapter=chapter, verse=verse,
        )
        return found[0] if found else None

    def record_review_state(self, chapter: str | int, verse: str | int, status: str, note: str = '') -> str:
        data = {
            'bookId': self.book_id,
            'chapter': str(chapter),
            'verse': str(verse),
            'status': status,
            'note': note,
            'modifiedTimestamp': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            'app': 'translationCore AI Bridge',
            'schemaVersion': 1,
        }
        # One status per verse, so the verse itself is the natural key.
        return self._record_human_decision_row(
            kind='verse_status', chapter=chapter, verse=verse, key=str(verse),
            decision=str(status), payload=data,
        )

    def record_ai_review_result(self, chapter: str | int, verse: str | int, payload: dict[str, Any]) -> str:
        data = {
            'bookId': self.book_id,
            'chapter': str(chapter),
            'verse': str(verse),
            'generatedTimestamp': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            'inputFingerprint': self.review_input_fingerprint(chapter, verse),
            'app': 'translationCore AI Bridge',
            'schemaVersion': 3,
            **copy.deepcopy(payload),
        }
        return self._write_ai_review_payload(chapter, verse, data)

    def load_ai_review_result(self, chapter: str | int, verse: str | int) -> dict[str, Any] | None:
        found = self.workbench.payloads(
            'ai_review_results', project_id=self.workbench_identity.project_id,
            book_id=self.book_id, equals={'chapter': str(chapter), 'verse': str(verse)},
        )
        return found[0] if found else None

    def record_human_decision(self, chapter: str | int, verse: str | int, check_id: str, decision: str, note: str = '', selection_text: list[str] | None = None, selection_ids: list[str] | None = None, tool: str = '', group_id: str = '', model: str = '', evidence: list[dict[str, Any]] | None = None) -> str:
        stamp = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        compact_evidence = []
        for ev in list(evidence or []):
            if not isinstance(ev, dict):
                continue
            compact_evidence.append({k: copy.deepcopy(ev.get(k)) for k in ('kind','title','path','version','provider','identifier','authoritative') if ev.get(k) not in (None,'')})
        data = {
            'bookId': self.book_id,
            'chapter': str(chapter),
            'verse': str(verse),
            'checkId': str(check_id),
            'tool': str(tool),
            'groupId': str(group_id),
            'decision': str(decision),
            'note': str(note),
            'selectionText': list(selection_text or []),
            'selectionIds': list(selection_ids or []),
            'model': str(model),
            'evidenceProvenance': compact_evidence,
            'modifiedTimestamp': stamp,
            'app': 'translationCore AI Bridge',
            'schemaVersion': 2,
        }
        # The `audit/` file this used to write alongside the record is gone: a
        # change_log row carries the full payload at time of change, which is
        # exactly what that file was (TEAM_ARCHITECTURE.md ss3.3). The
        # append-only property is unchanged -- human_decisions holds the current
        # decision, change_log holds every version it has ever had.
        return self._record_human_decision_row(
            kind='check', chapter=chapter, verse=verse, key=str(check_id),
            decision=str(decision), payload=data,
        )

    def _record_human_decision_row(
        self, *, kind: str, chapter: str | int, verse: str | int, key: str,
        decision: str, payload: dict[str, Any],
    ) -> str:
        """Upsert one human decision and return its workbench row id.

        The four decision kinds (check, qa, verse_status, terminology) shared a
        directory-per-kind on disk and share one table here, discriminated by
        `kind`. The row id is derived from the natural key so re-deciding the
        same thing updates in place rather than accumulating rows -- the same
        property the one-file-per-decision layout had.
        """
        identity = self.workbench_identity
        row_id = natural_row_id(
            identity.project_id, self.book_id, kind, str(chapter), str(verse), key,
        )
        self.workbench._write(
            'human_decisions', row_id,
            project_id=identity.project_id, book_id=self.book_id, payload=payload,
            actor_id=identity.actor_id, device_id=identity.device_id,
            expected_revision=None,
            extra_columns={
                'kind': kind, 'chapter': str(chapter), 'verse': str(verse),
                'key': key, 'decision': decision,
            },
        )
        return row_id

    def _record_check_selection_provenance(
        self, chapter: str | int, verse: str | int, tool: str, group_id: str,
        check_id: str, record: dict[str, Any], *, journal_tx_id: str | None = None,
    ) -> str:
        """Append one check selection's Bridge-side provenance to `change_log`.

        This was a file under `audit/`, a shadow of the native selection record
        with two fields the native one has no place for. It is an event, not a
        row image: the native `checkData/selections/` file is still the record,
        and this says who chose it and what they were looking at. `change_log`
        is where an event with no row of its own belongs, and its append-only
        triggers enforce what the file layout only implied.

        Keyed to the `human_decisions` row for the same check, so a reader that
        wants the decision and its provenance asks for one row key, not two.
        """
        identity = self.workbench_identity
        row_key = natural_row_id(
            identity.project_id, self.book_id, 'check_selection',
            str(chapter), str(verse), str(check_id),
        )
        return self.workbench.append_event(
            'human_decisions', row_key,
            project_id=identity.project_id, book_id=self.book_id,
            op=str(record.get('operation') or 'tcCheckSelectionSave'),
            payload={
                'bookId': self.book_id, 'chapter': str(chapter), 'verse': str(verse),
                'tool': tool, 'groupId': group_id, 'checkId': str(check_id),
                **copy.deepcopy(record),
            },
            actor_id=identity.actor_id, device_id=identity.device_id,
            journal_tx_id=journal_tx_id,
        )

    def _human_decision_payloads(
        self, *, kind: str, chapter: str | int | None = None, verse: str | int | None = None,
    ) -> list[dict[str, Any]]:
        equals: dict[str, Any] = {'kind': kind}
        if chapter is not None:
            equals['chapter'] = str(chapter)
        if verse is not None:
            equals['verse'] = str(verse)
        return self.workbench.payloads(
            'human_decisions', project_id=self.workbench_identity.project_id,
            book_id=self.book_id, equals=equals,
        )

    def record_ai_selection_outcomes(
        self, chapter: str | int, verse: str | int,
        applied: list[dict[str, Any]], skipped: list[dict[str, Any]],
    ) -> None:
        """Persist why each check was or was not selected automatically.

        The applied/skipped lists are otherwise only returned in the AI job
        result, which carries one verse's full payload at a time — so as soon as
        the reviewer navigated away the reason a check stayed Pending was gone.
        Storing it beside the review lets nativeChecks.list answer 'why is this
        still pending?' on any later visit, including after a restart.
        """
        d = self.load_ai_review_result(chapter, verse)
        if d is None:
            return
        outcomes: dict[str, Any] = {}
        for item in list(applied or []):
            key = f"{str(item.get('tool') or '')}:{str(item.get('checkId') or '')}"
            outcomes[key] = {'outcome': 'applied', 'reason': ''}
        for item in list(skipped or []):
            key = f"{str(item.get('tool') or '')}:{str(item.get('checkId') or '')}"
            outcomes[key] = {'outcome': 'skipped', 'reason': str(item.get('reason') or '')}
        d['automaticSelection'] = outcomes
        d['automaticSelectionTimestamp'] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        self._write_ai_review_payload(chapter, verse, d)

    def rebase_ai_review_fingerprint(self, chapter: str | int, verse: str | int) -> None:
        """Keep an already-reviewed verse current after human-only TN/TW state synchronization."""
        d = self.load_ai_review_result(chapter, verse)
        if d is not None:
            d['inputFingerprint'] = self.review_input_fingerprint(chapter, verse)
            d['humanStateRebasedTimestamp'] = datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
            self._write_ai_review_payload(chapter, verse, d)

    def list_ai_review_results(self, chapter: str | int | None = None) -> list[dict[str, Any]]:
        equals = {} if chapter is None else {'chapter': str(chapter)}
        return self.workbench.payloads(
            'ai_review_results', project_id=self.workbench_identity.project_id,
            book_id=self.book_id, equals=equals,
        )

    _TERMINOLOGY_CATEGORIES = ('person_name', 'place_name', 'key_term', 'other')
    _TERMINOLOGY_PROVENANCE = ('human', 'imported')
    # 'approved' is the only status the terminology QA check ever treats as
    # authoritative (see terminology.py's TermIndex). 'provisional'/'imported'
    # exist so a future auto-discovery path has somewhere to land without ever
    # being silently treated as a human decision -- frequency is evidence, not
    # authority. This method represents explicit human/API-driven curation, so
    # it defaults to 'approved'; nothing in this codebase yet writes anything
    # else, and that absence is deliberate, not an oversight.
    _TERMINOLOGY_STATUSES = ('approved', 'provisional', 'imported')

    def terminology_rules(self) -> list[dict[str, Any]]:
        return self._human_decision_payloads(kind='terminology')

    def record_terminology_rule(
        self, concept_id: str, approved_renderings: list[str] | None = None,
        allowed_alternatives: list[str] | None = None, rejected_renderings: list[str] | None = None,
        *, category: str = 'other', source_lemma: str = '', strong: str = '', note: str = '',
        provenance: str = 'human', status: str = 'approved',
        username: str = 'AI Bridge Reviewer', scope: str = 'book',
        inflected_forms: dict[str, list[str]] | None = None, match_mode: str = 'exact',
    ) -> str:
        """Termbase v3 (layered-rules 6.1) adds `inflected_forms` (rendering ->
        its forms, as authoritative as the rendering) and `match_mode`
        ("prefix" also matches the rejected renderings with a closed list of
        case/plural endings, at medium confidence; terminology.CASE_SUFFIXES)."""
        concept_id = str(concept_id).strip()
        if not concept_id:
            raise ProjectError('Terminology concept/key-term ID is required.')
        if match_mode not in ('exact', 'prefix'):
            raise ProjectError(f"Unknown terminology match mode: {match_mode!r}.")
        inflected: dict[str, list[str]] = {}
        for rendering, forms in (inflected_forms or {}).items():
            if not isinstance(forms, list):
                raise ProjectError('inflectedForms must map a rendering to a list of forms.')
            cleaned = [str(f).strip() for f in forms if str(f).strip()]
            if str(rendering).strip() and cleaned:
                inflected[str(rendering).strip()] = cleaned
        approved = [str(x).strip() for x in (approved_renderings or []) if str(x).strip()]
        allowed = [str(x).strip() for x in (allowed_alternatives or []) if str(x).strip()]
        rejected = [str(x).strip() for x in (rejected_renderings or []) if str(x).strip()]
        if not (approved or allowed or rejected):
            raise ProjectError('At least one approved, allowed, or rejected rendering is required.')
        if category not in self._TERMINOLOGY_CATEGORIES:
            raise ProjectError(f"Unknown terminology category: {category!r}.")
        if provenance not in self._TERMINOLOGY_PROVENANCE:
            raise ProjectError(f"Unknown terminology provenance: {provenance!r}.")
        if status not in self._TERMINOLOGY_STATUSES:
            raise ProjectError(f"Unknown terminology status: {status!r}.")
        if scope != 'book':
            raise ProjectError("Only book-scoped terminology is supported; whole-project scope is a separate, unbuilt design.")
        iso, _ = self._timestamp()
        data = {
            'bookId': self.book_id, 'conceptId': concept_id, 'category': category,
            'sourceLemma': source_lemma, 'strong': strong,
            'approvedRenderings': approved, 'allowedAlternatives': allowed,
            'rejectedRenderings': rejected,
            'inflectedForms': inflected, 'matchMode': match_mode,
            'note': note, 'scope': scope, 'provenance': provenance, 'status': status,
            'username': username, 'modifiedTimestamp': iso,
            'app': 'translationCore AI Bridge', 'schemaVersion': 3,
        }
        # Book-scoped rather than verse-scoped: a terminology rule applies to the
        # whole book, so chapter/verse stay empty and the concept is the key.
        return self._record_human_decision_row(
            kind='terminology', chapter='', verse='', key=concept_id,
            decision='human_approved', payload=data,
        )

    # -- house style (layered-rules 6.2-6.4; human_decisions kind 'housestyle', v5)

    def housestyle_entries(self) -> list[dict[str, Any]]:
        return self._human_decision_payloads(kind='housestyle')

    def record_housestyle_entry(self, entry: dict[str, Any], *, username: str = 'Bridge Reviewer') -> dict[str, Any]:
        """Upsert one house-style entry (housestyle.validate_entry). The same
        key is the same row: Remove and Undo write a new state onto it, and
        change_log keeps every earlier one -- nothing is deleted."""
        from .housestyle import validate_entry
        try:
            data = validate_entry(entry)
        except ValueError as exc:
            raise ProjectError(str(exc)) from exc
        iso, _ = self._timestamp()
        data.update({'bookId': self.book_id, 'username': username, 'modifiedTimestamp': iso,
                     'app': 'translationCore AI Bridge', 'schemaVersion': 1})
        self._record_human_decision_row(kind='housestyle', chapter='', verse='', key=data['key'],
                                        decision=data['state'], payload=data)
        return data

    def project_decisions(self) -> list[dict[str, Any]]:
        return self._human_decision_payloads(kind='check')

    def project_qa_decisions(self) -> list[dict[str, Any]]:
        """Every QA decision in the book, for whole-book readers.

        `qa_report` built this by walking the `qaDecisions` tree itself rather
        than going through this class, which is why the cutover broke it. One
        query beats a directory walk, and it keeps the store boundary in one
        place.
        """
        return self._human_decision_payloads(kind='qa')

    def _timestamp(self) -> tuple[str, str]:
        iso = datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
        return iso, iso.replace(':', '_')

    def _backup_paths(self, paths: list[Path], label: str) -> Path:
        """Back up existing project files before a multi-file tC-compatible transaction."""
        iso, safe = self._timestamp()
        root = self.companion_dir() / 'backups' / safe / label
        copied: list[Path] = []
        for path in paths:
            if not path.exists():
                continue
            try:
                rel = path.resolve().relative_to(self.path)
            except Exception:
                rel = Path(path.name)
            dst = root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dst)
            copied.append(dst)
        self._index_backups(copied, label=label, stamp=safe)
        return root

    def _rollback_paths(self, backup_root: Path, paths: list[Path], existed: dict[str, bool]) -> None:
        errors=[]
        for path in paths:
            try:
                key=str(path.resolve())
                rel=path.resolve().relative_to(self.path)
                src=backup_root/rel
                if existed.get(key,False):
                    if not src.exists(): raise ProjectError(f'Missing rollback copy for {path}')
                    path.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(src,path)
                elif path.exists():
                    path.unlink()
            except Exception as e:
                errors.append(f'{path}: {e}')
        if errors:
            raise ProjectError('Rollback was incomplete; restore from backup '+str(backup_root)+'\n'+'\n'.join(errors))

    def _find_index_entry_path(self, tool: str, group_id: str, check_id: str) -> tuple[Path, list[dict[str, Any]], int]:
        if tool not in ('translationNotes', 'translationWords'):
            raise ProjectError('Only Translation Notes/Words checks are supported here.')
        book_dir = self.index_dir / tool / self.book_id
        candidates: list[Path] = []
        if book_dir.exists():
            # Most native tC indexes use groupId.json. Only form that direct path
            # when every character is filename-safe; otherwise use the bounded scan.
            if group_id and all(char.isalnum() or char in ('.', '_', '-') for char in group_id):
                direct = book_dir / f'{group_id}.json'
                if direct.exists():
                    candidates.append(direct)
            candidates.extend(p for p in book_dir.glob('*.json') if p not in candidates)
        for candidate in candidates:
            if candidate.name == 'contextId.json':
                continue
            try:
                data = _read_json(candidate)
            except Exception:
                continue
            if not isinstance(data, list):
                continue
            for i, entry in enumerate(data):
                ctx = entry.get('contextId', {}) if isinstance(entry, dict) else {}
                if (str(ctx.get('checkId', '')) == str(check_id)
                        and str(ctx.get('groupId', '')) == str(group_id)):
                    return candidate, data, i
        raise ProjectError(f'Could not find check {check_id} in {tool}/{group_id}')

    @staticmethod
    def _selection_ranges(target_text: str, selected_text: str) -> list[tuple[int, int]]:
        """Return tC-style, non-overlapping occurrences in the displayed verse text."""
        ranges: list[tuple[int, int]] = []
        start = 0
        while True:
            index = target_text.find(selected_text, start)
            if index < 0:
                return ranges
            end = index + len(selected_text)
            ranges.append((index, end))
            start = end

    def _normalize_check_selections(
        self,
        chapter: str | int,
        verse: str | int,
        selections: Any,
        nothing_to_select: bool,
    ) -> tuple[list[dict[str, Any]], list[dict[str, int]]]:
        if not isinstance(nothing_to_select, bool):
            raise ProjectError('nothingToSelect must be a boolean.')
        if not isinstance(selections, list):
            raise ProjectError('selections must be a list.')
        if bool(selections) == bool(nothing_to_select):
            raise ProjectError('A completed check must have selections or nothingToSelect=true, but not both.')

        target_text = strip_usfm(self.target_verse_text(chapter, verse))
        normalized: list[dict[str, Any]] = []
        selected_ranges: list[dict[str, int]] = []
        identities: set[tuple[str, int, int]] = set()
        occupied: list[tuple[int, int]] = []
        for raw in selections:
            if not isinstance(raw, dict):
                raise ProjectError('Every target selection must be an object.')
            text = str(raw.get('text', ''))
            if not text or text != text.strip():
                raise ProjectError('Target selection text must be non-empty and may not have outer whitespace.')
            occurrence = raw.get('occurrence')
            occurrences = raw.get('occurrences')
            if (isinstance(occurrence, bool) or not isinstance(occurrence, int)
                    or isinstance(occurrences, bool) or not isinstance(occurrences, int)):
                raise ProjectError('Target selection occurrence metadata must use integers.')
            ranges = self._selection_ranges(target_text, text)
            actual = len(ranges)
            if actual == 0:
                raise ProjectError(f'Target selection {text!r} is not present in the current verse.')
            if occurrences != actual:
                raise ProjectError(
                    f'Target selection {text!r} reports {occurrences} occurrences, but the current verse has {actual}.'
                )
            if occurrence < 1 or occurrence > actual:
                raise ProjectError(
                    f'Target selection {text!r} occurrence {occurrence} is outside 1..{actual}.'
                )
            identity = (text, occurrence, occurrences)
            if identity in identities:
                raise ProjectError(f'Duplicate target selection: {text!r} occurrence {occurrence}.')
            start, end = ranges[occurrence - 1]
            if any(start < other_end and other_start < end for other_start, other_end in occupied):
                raise ProjectError('Target selections may not overlap; combine overlapping words into one selection.')
            identities.add(identity)
            occupied.append((start, end))
            normalized.append({'text': text, 'occurrence': occurrence, 'occurrences': occurrences})
            selected_ranges.append({'start': start, 'end': end})
        return normalized, selected_ranges

    def _check_provenance(
        self, chapter: str | int, verse: str | int, check_id: str, entry: dict[str, Any]
    ) -> str:
        has_selection = bool(entry.get('selections')) or bool(entry.get('nothingToSelect'))
        if not has_selection:
            return 'none'
        ctx = entry.get('contextId', {}) if isinstance(entry, dict) else {}
        latest = self._latest_state_for_check(
            'selections', chapter, verse, check_id,
            str(ctx.get('tool', '')), str(ctx.get('groupId', '')),
        )
        if not latest:
            return 'existing_tc'
        return 'bridge_ai' if str(latest.get('username', '')).strip().lower() == 'bridge ai' else 'human'

    def _check_state_fingerprint(
        self, chapter: str | int, verse: str | int, check_id: str, entry: dict[str, Any]
    ) -> str:
        ctx = entry.get('contextId', {}) if isinstance(entry, dict) else {}
        latest = self._latest_state_for_check(
            'selections', chapter, verse, check_id,
            str(ctx.get('tool', '')), str(ctx.get('groupId', '')),
        ) or {}
        value = {
            'selections': entry.get('selections', False),
            'nothingToSelect': bool(entry.get('nothingToSelect')),
            'invalidated': bool(entry.get('invalidated')),
            'verseEdits': bool(entry.get('verseEdits')),
            'latestSelectionTimestamp': latest.get('modifiedTimestamp', ''),
            'targetText': self.target_verse_text(chapter, verse),
        }
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()

    def _check_review_from_entry(
        self, chapter: str | int, verse: str | int, tool: str, group_id: str,
        check_id: str, entry: dict[str, Any],
    ) -> dict[str, Any]:
        for label, value in (('chapter', chapter), ('verse', verse)):
            part = str(value)
            if not part or part in ('.', '..') or '/' in part or '\\' in part:
                raise ProjectError(f'Invalid {label} reference.')
        ctx = copy.deepcopy(entry.get('contextId', {}))
        ref = ctx.get('reference', {})
        if str(ref.get('chapter')) != str(chapter) or str(ref.get('verse')) != str(verse):
            raise ProjectError('Check reference does not match the current verse.')
        stale = self.check_staleness(chapter, verse, check_id, tool, group_id) == 'stale'
        invalidated = bool(entry.get('invalidated')) or bool(entry.get('verseEdits')) or stale
        selections = copy.deepcopy(entry.get('selections')) if isinstance(entry.get('selections'), list) else []
        nothing_to_select = bool(entry.get('nothingToSelect'))
        if invalidated:
            selection_status = 'invalidated'
        elif selections:
            selection_status = 'selected'
        elif nothing_to_select:
            selection_status = 'nothing_to_select'
        else:
            selection_status = 'pending'
        return {
            'chapter': str(chapter),
            'verse': str(verse),
            'tool': tool,
            'groupId': str(group_id),
            'checkId': str(check_id),
            'sourceQuote': str(ctx.get('quoteString', '')),
            'sourceOccurrence': ctx.get('occurrence'),
            'occurrenceNote': str(ctx.get('occurrenceNote', '')),
            'selections': selections,
            'nothingToSelect': nothing_to_select,
            'invalidated': invalidated,
            'stale': stale,
            'selectionStatus': selection_status,
            # 3B.1 does not run AI evaluation. Do not mislabel a native selection as a pass.
            'evaluationStatus': 'needs_review' if invalidated else 'not_run',
            'provenance': self._check_provenance(chapter, verse, check_id, entry),
            'stateFingerprint': self._check_state_fingerprint(chapter, verse, check_id, entry),
            # Filled in by BridgeEngine.list_checks_for_verse, which is the only
            # caller that reads the AI review record. Declared here so every
            # check row has one shape -- a row returned by save_check_selection
            # would otherwise be missing the field the UI reads.
            'automaticSelection': None,
        }

    def check_review(self, chapter: str | int, verse: str | int, tool: str, group_id: str, check_id: str) -> dict[str, Any]:
        _, data, idx = self._find_index_entry_path(tool, group_id, check_id)
        return self._check_review_from_entry(
            chapter, verse, tool, group_id, check_id, data[idx],
        )

    def check_reviews_for_verse(self, chapter: str | int, verse: str | int) -> list[dict[str, Any]]:
        reviews = []
        for entry in self.checks_for_verse(chapter, verse):
            ctx = entry.get('contextId', {}) if isinstance(entry, dict) else {}
            tool = str(ctx.get('tool', ''))
            if tool not in ('translationNotes', 'translationWords'):
                continue
            reviews.append(self._check_review_from_entry(
                chapter, verse, tool, str(ctx.get('groupId', '')),
                str(ctx.get('checkId', '')), entry,
            ))
        return reviews

    def validate_check_selection(
        self,
        chapter: str | int,
        verse: str | int,
        tool: str,
        group_id: str,
        check_id: str,
        selections: Any,
        nothing_to_select: bool,
    ) -> dict[str, Any]:
        # Resolve the exact native entry first; an invalid identity is a request error, not a validation result.
        current = self.check_review(chapter, verse, tool, group_id, check_id)
        try:
            normalized, ranges = self._normalize_check_selections(
                chapter, verse, selections, bool(nothing_to_select),
            )
        except ProjectError as exc:
            return {
                'valid': False, 'errors': [str(exc)], 'selections': [], 'ranges': [],
                'stateFingerprint': current['stateFingerprint'],
            }
        return {
            'valid': True, 'errors': [], 'selections': normalized, 'ranges': ranges,
            'stateFingerprint': current['stateFingerprint'],
        }

    def _latest_state_for_check(
        self, state_type: str, chapter: str | int, verse: str | int, check_id: str,
        tool: str = '', group_id: str = '',
    ) -> dict[str, Any] | None:
        latest = None
        latest_ts = ''
        for path in self._state_files_for_verse(state_type, chapter, verse):
            try:
                d = _read_json(path)
            except Exception:
                continue
            ctx = d.get('contextId', {}) if isinstance(d, dict) else {}
            if str(ctx.get('checkId', '')) != str(check_id):
                continue
            if tool and str(ctx.get('tool', '')) != str(tool):
                continue
            if group_id and str(ctx.get('groupId', '')) != str(group_id):
                continue
            ts = str(d.get('modifiedTimestamp') or d.get('timestamp') or path.name)
            if ts >= latest_ts:
                latest, latest_ts = d, ts
        return latest

    def check_staleness(
        self, chapter: str | int, verse: str | int, check_id: str,
        tool: str = '', group_id: str = '',
    ) -> str:
        """current | stale | pending using actual tC selection vs verse-edit timestamps."""
        sel = self._latest_state_for_check('selections', chapter, verse, check_id, tool, group_id)
        if not sel:
            return 'pending'
        sel_ts = str(sel.get('modifiedTimestamp', ''))
        edit_ts = self._latest_verse_edit_timestamp(chapter, verse)
        return 'stale' if edit_ts and sel_ts <= edit_ts else 'current'

    def _latest_verse_edit_timestamp(self, chapter: str | int, verse: str | int) -> str:
        edit_ts = ''
        for path in self._state_files_for_verse('verseEdits', chapter, verse):
            try:
                d = _read_json(path)
            except Exception:
                continue
            edit_ts = max(edit_ts, str(d.get('modifiedTimestamp', '')))
        return edit_ts

    def _persist_check_selection(
        self,
        chapter: str | int,
        verse: str | int,
        tool: str,
        group_id: str,
        check_id: str,
        selections: list[dict[str, Any]],
        nothing_to_select: bool,
        provenance: str,
        expected_fingerprint: str,
        username: str,
        gateway_language_code: str = 'en',
        gateway_language_quote: str = '',
        audit_metadata: dict[str, Any] | None = None,
        clear: bool = False,
    ) -> dict[str, Any]:
        if tool not in ('translationNotes', 'translationWords'):
            raise ProjectError('Only Translation Notes/Words checks can be synchronized here.')
        if not isinstance(nothing_to_select, bool):
            raise ProjectError('nothingToSelect must be a boolean.')
        if not isinstance(audit_metadata, (dict, type(None))):
            raise ProjectError('metadata must be an object.')
        if provenance not in ('human', 'bridge_ai'):
            raise ProjectError('provenance must be human or bridge_ai.')
        path, data, idx = self._find_index_entry_path(tool, group_id, check_id)
        entry = data[idx]
        ctx = copy.deepcopy(entry.get('contextId', {}))
        ref = ctx.get('reference', {})
        if str(ref.get('chapter')) != str(chapter) or str(ref.get('verse')) != str(verse):
            raise ProjectError('Check reference does not match the current verse.')

        current_fingerprint = self._check_state_fingerprint(chapter, verse, check_id, entry)
        if not expected_fingerprint:
            raise ProjectError('expectedFingerprint is required; reload the check before saving.')
        if expected_fingerprint != current_fingerprint:
            raise ProjectError('The check changed on disk after it was loaded. Reload before saving.')
        current_provenance = self._check_provenance(chapter, verse, check_id, entry)
        has_current_selection = bool(entry.get('selections')) or bool(entry.get('nothingToSelect'))
        if provenance == 'bridge_ai' and has_current_selection and current_provenance != 'bridge_ai':
            raise ProjectError('Bridge AI may not replace an existing translationCore or human selection.')

        if clear:
            normalized: list[dict[str, Any]] = []
            nothing_to_select = False
        else:
            normalized, _ = self._normalize_check_selections(
                chapter, verse, selections, bool(nothing_to_select),
            )

        iso, safe = self._timestamp()
        edit_timestamp = self._latest_verse_edit_timestamp(chapter, verse)
        if edit_timestamp and iso <= edit_timestamp:
            try:
                after_edit = datetime.fromisoformat(edit_timestamp.replace('Z', '+00:00')) + timedelta(milliseconds=1)
                iso = after_edit.isoformat(timespec='milliseconds').replace('+00:00', 'Z')
                safe = iso.replace(':', '_')
            except ValueError:
                # A malformed external timestamp remains safely stale rather than
                # being guessed current; native write validation still proceeds.
                pass
        state_root = self.check_dir
        suffix = ''
        counter = 0
        while True:
            stem = f'{safe}{suffix}'
            sel_path = state_root / 'selections' / self.book_id / str(chapter) / str(verse) / f'{stem}.json'
            inv_path = state_root / 'invalidated' / self.book_id / str(chapter) / str(verse) / f'{stem}.json'
            if not sel_path.exists() and not inv_path.exists():
                break
            counter += 1
            suffix = f'-{counter}'

        tx_paths = [path, sel_path, inv_path]
        existed = {str(x.resolve()): x.exists() for x in tx_paths}
        backup = self._backup_paths(tx_paths, 'checkDataSync')
        journal_tx = self.journal.begin('checkDataSync', tx_paths)
        self.journal.mark_writing(journal_tx)
        native_username = 'Bridge AI' if provenance == 'bridge_ai' else (username.strip() or 'Bridge Reviewer')
        common = {
            'contextId': ctx,
            'modifiedTimestamp': iso,
            'gatewayLanguageCode': gateway_language_code,
            'gatewayLanguageQuote': gateway_language_quote,
            'username': native_username,
        }
        selection_record = {
            **common,
            'selections': copy.deepcopy(normalized),
            'nothingToSelect': bool(nothing_to_select),
        }
        invalid_record = {
            'contextId': ctx,
            'username': native_username,
            'invalidated': False,
            'gatewayLanguageCode': gateway_language_code,
            'gatewayLanguageQuote': gateway_language_quote,
            'modifiedTimestamp': iso,
        }
        # Not a file any more, but not droppable either: `provenance`
        # (human vs bridge_ai) and `metadata` (which interface the human used,
        # and whether AI evidence was on screen when they chose) exist in no
        # other record. The native selection record only has `username`, a
        # display string. Given Bridge's three-way split -- Greek Room suspects,
        # AI interprets, the human decides -- that is not reconstructable once
        # it stops being written.
        selection_provenance = {
            'operation': 'tcCheckSelectionClear' if clear else 'tcCheckSelectionSave',
            'tool': tool,
            'groupId': group_id,
            'checkId': check_id,
            'selections': copy.deepcopy(normalized),
            'nothingToSelect': bool(nothing_to_select),
            'provenance': provenance,
            'username': native_username,
            'metadata': copy.deepcopy(audit_metadata or {}),
            'modifiedTimestamp': iso,
            'app': 'translationCore AI Bridge',
            'schemaVersion': 1,
        }
        try:
            _write_json_atomic(sel_path, selection_record)
            _write_json_atomic(inv_path, invalid_record)
            entry['selections'] = copy.deepcopy(normalized) if normalized else False
            entry['nothingToSelect'] = bool(nothing_to_select)
            entry['invalidated'] = False
            entry['verseEdits'] = False
            data[idx] = entry
            _write_json_atomic(path, data)
            self._record_check_selection_provenance(
                chapter, verse, tool, group_id, check_id, selection_provenance,
                journal_tx_id=journal_tx.transaction_id,
            )
            self.journal.commit(journal_tx, {
                'operation': selection_provenance['operation'], 'tool': tool, 'checkId': check_id,
            })
        except Exception as exc:
            try:
                self._rollback_paths(backup, tx_paths, existed)
            finally:
                try:
                    self.journal.rollback(journal_tx, str(exc))
                except Exception:
                    pass
            self.invalidate_index_cache()
            raise
        self.invalidate_index_cache()
        return {
            'committed': True,
            'review': self.check_review(chapter, verse, tool, group_id, check_id),
            'files': {
                'selection': str(sel_path), 'invalidated': str(inv_path),
                'index': str(path), 'backup': str(backup),
            },
        }

    def save_check_selection(
        self,
        chapter: str | int,
        verse: str | int,
        tool: str,
        group_id: str,
        check_id: str,
        selections: list[dict[str, Any]],
        nothing_to_select: bool,
        provenance: str,
        expected_fingerprint: str,
        username: str = 'Bridge Reviewer',
        audit_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._persist_check_selection(
            chapter, verse, tool, group_id, check_id, selections, nothing_to_select,
            provenance, expected_fingerprint, username, audit_metadata=audit_metadata,
        )

    def clear_check_selection(
        self,
        chapter: str | int,
        verse: str | int,
        tool: str,
        group_id: str,
        check_id: str,
        provenance: str,
        expected_fingerprint: str,
        username: str = 'Bridge Reviewer',
        audit_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._persist_check_selection(
            chapter, verse, tool, group_id, check_id, [], False, provenance,
            expected_fingerprint, username, audit_metadata=audit_metadata, clear=True,
        )

    def sync_check_approval(self, chapter: str | int, verse: str | int, tool: str, group_id: str, check_id: str, selections: list[dict[str, Any]], nothing_to_select: bool, username: str = 'Bridge Reviewer', gateway_language_code: str = 'en', gateway_language_quote: str = '') -> dict[str, Any]:
        """Backward-compatible human writer; new callers should use save_check_selection."""
        _, data, idx = self._find_index_entry_path(tool, group_id, check_id)
        expected = self._check_state_fingerprint(chapter, verse, check_id, data[idx])
        return self._persist_check_selection(
            chapter, verse, tool, group_id, check_id, selections, nothing_to_select,
            'human', expected, username, gateway_language_code, gateway_language_quote,
        )

    def word_alignment_state(self, chapter: str | int, verse: str | int) -> str:
        completed = self.tc_dir / 'tools' / 'wordAlignment' / 'completed' / str(chapter) / f'{verse}.json'
        invalid = self.tc_dir / 'tools' / 'wordAlignment' / 'invalid' / str(chapter) / f'{verse}.json'
        if invalid.exists(): return 'invalid'
        if completed.exists(): return 'completed'
        return 'pending'

    def mark_word_alignment_completed(self, chapter: str | int, verse: str | int, username: str = 'AI Bridge Reviewer') -> Path:
        completed = self.tc_dir / 'tools' / 'wordAlignment' / 'completed' / str(chapter) / f'{verse}.json'
        invalid = self.tc_dir / 'tools' / 'wordAlignment' / 'invalid' / str(chapter) / f'{verse}.json'
        self._backup_paths([completed, invalid], 'wordAlignmentState')
        tx=self.journal.begin('wordAlignmentComplete',[completed,invalid]); self.journal.mark_writing(tx)
        iso, _ = self._timestamp()
        try:
            _write_json_atomic(completed, {'username': username, 'modifiedTimestamp': iso})
            if invalid.exists(): invalid.unlink()
            self.journal.commit(tx,{'operation':'wordAlignmentComplete','chapter':str(chapter),'verse':str(verse)})
        except Exception as e:
            self.journal.rollback(tx,str(e)); raise
        return completed

    def mark_word_alignment_invalid(self, chapter: str | int, verse: str | int) -> Path:
        completed = self.tc_dir / 'tools' / 'wordAlignment' / 'completed' / str(chapter) / f'{verse}.json'
        invalid = self.tc_dir / 'tools' / 'wordAlignment' / 'invalid' / str(chapter) / f'{verse}.json'
        self._backup_paths([completed, invalid], 'wordAlignmentState')
        tx=self.journal.begin('wordAlignmentInvalid',[completed,invalid]); self.journal.mark_writing(tx)
        iso, _ = self._timestamp()
        try:
            _write_json_atomic(invalid, {'timestamp': iso})
            if completed.exists(): completed.unlink()
            self.journal.commit(tx,{'operation':'wordAlignmentInvalid','chapter':str(chapter),'verse':str(verse)})
        except Exception as e:
            self.journal.rollback(tx,str(e)); raise
        return invalid

    def mark_word_alignment_pending(self, chapter: str | int, verse: str | int) -> None:
        """Clear completed/invalid markers after an intentional alignment edit."""
        completed = self.tc_dir / 'tools' / 'wordAlignment' / 'completed' / str(chapter) / f'{verse}.json'
        invalid = self.tc_dir / 'tools' / 'wordAlignment' / 'invalid' / str(chapter) / f'{verse}.json'
        self._backup_paths([completed, invalid], 'wordAlignmentState')
        tx = self.journal.begin('wordAlignmentPending', [completed, invalid])
        self.journal.mark_writing(tx)
        try:
            if completed.exists(): completed.unlink()
            if invalid.exists(): invalid.unlink()
            self.journal.commit(tx, {
                'operation': 'wordAlignmentPending', 'chapter': str(chapter), 'verse': str(verse),
            })
        except Exception as exc:
            self.journal.rollback(tx, str(exc)); raise

    @staticmethod
    def _target_tokens(text: str) -> list[dict[str, Any]]:
        words = whitespace_tokens(text)
        totals = Counter(words)
        seen: Counter[str] = Counter()
        out = []
        for word in words:
            seen[word] += 1
            out.append({'word': word, 'occurrence': seen[word], 'occurrences': totals[word], 'type': 'bottomWord'})
        return out

    def _reconcile_alignment_after_target_edit(self, chapter: str | int, verse: str | int, new_text: str) -> dict[str, Any]:
        raw = copy.deepcopy(self.load_alignment_chapter(chapter).get(str(verse), {}))
        self._validate_verse_raw(raw)
        new_tokens = self._target_tokens(new_text)
        new_by_sig = {f"{x['word']}\u241f{x['occurrence']}\u241f{x['occurrences']}": x for x in new_tokens}
        used = set()
        for group in raw['alignments']:
            kept = []
            for token in group.get('bottomWords', []):
                sig = f"{token.get('word','')}\u241f{token.get('occurrence',1)}\u241f{token.get('occurrences',1)}"
                if sig in new_by_sig:
                    kept.append(copy.deepcopy(new_by_sig[sig])); used.add(sig)
            group['bottomWords'] = kept
        bank = []
        for token in new_tokens:
            sig = f"{token['word']}\u241f{token['occurrence']}\u241f{token['occurrences']}"
            if sig not in used:
                bank.append(copy.deepcopy(token)); used.add(sig)
        raw['wordBank'] = bank
        self._validate_verse_raw(raw)
        return raw

    def _require_durable_correction_application(
        self, strict_context: Any, *, reference: str, new_text: str,
        required_state: str, transaction_id: str = '',
    ) -> dict[str, Any]:
        """Fail closed unless strict correction state is durably journaled.

        The application service normally owns this ordering. Enforcing the
        same contract at the canonical Scripture writer prevents a future or
        accidental direct strict-mode call from changing Scripture with only a
        prepared semantic invalidation and no recoverable application ledger.
        """
        import hashlib

        runtime = self.passage_semantic_runtime
        if runtime is None:
            raise ProjectError(
                'CORRECTION_APPLICATION_NOT_DURABLE: semantic runtime is unavailable.'
            )
        if not strict_context.application_id:
            raise ProjectError(
                'CORRECTION_APPLICATION_NOT_DURABLE: application identity is missing.'
            )
        try:
            application = runtime.repository.application_intent(
                strict_context.application_id
            )
        except Exception as exc:
            raise ProjectError(
                'CORRECTION_APPLICATION_NOT_DURABLE: application record was not persisted.'
            ) from exc

        final_hash = hashlib.sha256(new_text.encode('utf-8')).hexdigest()
        mismatches: list[str] = []
        expected = {
            'projectId': runtime.project_id,
            'targetDisplayedReference': reference,
            'expectedTargetRevision': strict_context.expected_target_revision,
            'expectedTargetContentHash': strict_context.expected_target_content_hash,
            'expectedStartCodePoint': strict_context.expected_start_code_point,
            'expectedEndCodePoint': strict_context.expected_end_code_point,
            'expectedOriginalText': strict_context.expected_original_span_text,
            'intendedFinalVerseHash': final_hash,
            'pendingInvalidationId': strict_context.pending_invalidation_id,
            'applicationState': required_state,
        }
        for field, value in expected.items():
            if application.get(field) != value:
                mismatches.append(field)
        if transaction_id and application.get('translationCoreJournalTransactionId') != transaction_id:
            mismatches.append('translationCoreJournalTransactionId')
        if mismatches:
            raise ProjectError(
                'CORRECTION_APPLICATION_NOT_DURABLE: application snapshot mismatch: '
                + ', '.join(sorted(set(mismatches)))
            )
        return application

    def apply_scripture_edit(self, chapter: str | int, verse: str | int, new_text: str, username: str = 'AI Bridge Reviewer', tags: list[str] | None = None, context_id: dict[str, Any] | None = None, *, strict_context: Any | None = None, journal_prepared_callback: Any | None = None, journal_metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        """Human-only target verse edit with tC-compatible stale propagation and rollback."""
        if str(verse) == 'front':
            raise ProjectError('Front-matter editing is not enabled in v0.6; edit a numbered verse.')
        # Ordinary manual edits retain their historical trimming behaviour.
        # A reviewed correction is already an exact code-point replacement;
        # trimming it here would silently mutate text outside the approved span.
        new_text = str(new_text) if strict_context is not None else str(new_text).strip()
        if not new_text:
            raise ProjectError('Scripture verse may not be empty.')
        chapter_path = self.book_dir / f'{chapter}.json'
        chapter_data = self.target_chapter(chapter)
        old_text = str(chapter_data.get(str(verse), ''))
        if strict_context is not None:
            import hashlib
            if journal_prepared_callback is None:
                raise ProjectError(
                    'CORRECTION_APPLICATION_NOT_DURABLE: journal transition callback is required.'
                )
            actual_hash = hashlib.sha256(old_text.encode('utf-8')).hexdigest()
            if old_text != strict_context.expected_original_verse_text:
                raise ProjectError('REVISION_CONFLICT: target verse text changed since review.')
            if actual_hash != strict_context.expected_target_content_hash:
                raise ProjectError('REVISION_CONFLICT: target verse hash changed since review.')
            start = strict_context.expected_start_code_point
            end = strict_context.expected_end_code_point
            if not 0 <= start <= end <= len(old_text):
                raise ProjectError('REVISION_CONFLICT: reviewed correction span is no longer valid.')
            if old_text[start:end] != strict_context.expected_original_span_text:
                raise ProjectError('REVISION_CONFLICT: reviewed correction span text changed.')
            if new_text != strict_context.intended_final_verse_text:
                raise ProjectError('REVISION_CONFLICT: proposed final verse does not match the reviewed correction.')
            if self.passage_semantic_runtime is not None:
                reference = self.passage_semantic_runtime.reference(str(chapter), str(verse), self.book_id.upper())
                current = self.passage_semantic_runtime.repository.current_target_revision(
                    self.passage_semantic_runtime.project_id, self.book_id.upper(), reference,
                )
                if current is None or current.get('textRevision') != strict_context.expected_target_revision:
                    raise ProjectError('REVISION_CONFLICT: target revision changed since review.')
                pending = self.passage_semantic_runtime.repository.target_invalidation(
                    strict_context.pending_invalidation_id,
                )
                if pending is None or pending.get('state') != 'PREPARED':
                    raise ProjectError('Correction semantic invalidation is not prepared.')
                self._require_durable_correction_application(
                    strict_context, reference=reference, new_text=new_text,
                    required_state='PREPARED',
                )
            else:
                raise ProjectError(
                    'CORRECTION_APPLICATION_NOT_DURABLE: semantic runtime is unavailable.'
                )
        if old_text == new_text:
            raise ProjectError('No Scripture text change detected.')
        semantic_intent = ''
        if strict_context is None and self.passage_semantic_runtime is not None:
            try:
                semantic_intent = self.passage_semantic_runtime.prepare_target_edit(
                    str(chapter), str(verse), old_text, new_text,
                )
            except Exception:
                # Semantic companion failure must never block the authorized
                # Scripture edit. Startup hash reconciliation remains a second
                # independent stale-safety boundary.
                semantic_intent = ''
        alignment_path = self.chapter_path(chapter)
        checks = self.checks_for_verse(chapter, verse)
        index_paths: list[Path] = []
        for e in checks:
            tool = str(e.get('contextId', {}).get('tool', '')); gid = str(e.get('contextId', {}).get('groupId', ''))
            ip = self.index_dir / tool / self.book_id / f'{gid}.json'
            if ip.exists() and ip not in index_paths: index_paths.append(ip)
        completed = self.tc_dir / 'tools' / 'wordAlignment' / 'completed' / str(chapter) / f'{verse}.json'
        invalid = self.tc_dir / 'tools' / 'wordAlignment' / 'invalid' / str(chapter) / f'{verse}.json'
        iso, safe = self._timestamp()
        edit_path = self.check_dir / 'verseEdits' / self.book_id / str(chapter) / str(verse) / f'{safe}.json'
        tx_paths=[chapter_path,alignment_path,completed,invalid,edit_path,*index_paths]
        existed={str(x.resolve()):x.exists() for x in tx_paths}
        backup = self._backup_paths(tx_paths, 'scriptureEdit')
        journal_tx=self.journal.begin('scriptureEdit',tx_paths)
        if journal_prepared_callback is not None:
            try:
                journal_prepared_callback(journal_tx.transaction_id)
                if strict_context is not None:
                    reference = self.passage_semantic_runtime.reference(
                        str(chapter), str(verse), self.book_id.upper()
                    )
                    self._require_durable_correction_application(
                        strict_context, reference=reference, new_text=new_text,
                        required_state='APPLYING',
                        transaction_id=journal_tx.transaction_id,
                    )
            except Exception as exc:
                self.journal.rollback(journal_tx, str(exc))
                raise
        self.journal.mark_writing(journal_tx)
        new_alignment = self._reconcile_alignment_after_target_edit(chapter, verse, new_text)
        # #117: a target word that the edit removed may be the target of a
        # cross-verse link; those links are marked invalid inside the same
        # journal transaction (below), so a failed edit leaves them untouched.
        removed_signatures = (
            self._bottom_signatures(self.load_alignment_chapter(chapter).get(str(verse), {}))
            - self._bottom_signatures(new_alignment)
        )
        invalidated_links: list[dict[str, Any]] = []
        if context_id is None:
            context_id = {'reference': {'bookId': self.book_id, 'chapter': int(chapter) if str(chapter).isdigit() else str(chapter), 'verse': int(verse) if str(verse).isdigit() else str(verse)}, 'tool': 'translationCoreAI', 'groupId': 'human-scripture-edit'}
        edit_record = {
            'verseBefore': old_text, 'verseAfter': new_text, 'tags': list(tags or ['meaning']),
            'username': username, 'activeBook': self.book_id,
            'activeChapter': int(chapter) if str(chapter).isdigit() else str(chapter),
            'activeVerse': int(verse) if str(verse).isdigit() else str(verse),
            'modifiedTimestamp': iso, 'gatewayLanguageCode': 'en', 'gatewayLanguageQuote': '',
            'contextId': copy.deepcopy(context_id),
        }
        touched=[]
        try:
            chapter_data[str(verse)] = new_text
            alignment_chapter = self.load_alignment_chapter(chapter); alignment_chapter[str(verse)] = new_alignment
            _write_json_atomic(chapter_path, chapter_data)
            _write_json_atomic(alignment_path, alignment_chapter)
            # Same mutually-exclusive state observed in the real tC backend.
            _write_json_atomic(invalid, {'timestamp': iso})
            if completed.exists(): completed.unlink()
            _write_json_atomic(edit_path, edit_record)
            # Materialized tC indexes retain selections but flag that a verse edit occurred.
            for ip in index_paths:
                arr = _read_json(ip); changed=False
                if isinstance(arr, list):
                    for e in arr:
                        ref = e.get('contextId', {}).get('reference', {}) if isinstance(e, dict) else {}
                        if str(ref.get('chapter')) == str(chapter) and str(ref.get('verse')) == str(verse):
                            e['verseEdits'] = True; changed=True
                    if changed:
                        _write_json_atomic(ip, arr); touched.append(str(ip))
            if removed_signatures:
                invalidated_links = self.cross_verse_links.invalidate_missing_targets(
                    chapter, verse, removed_signatures,
                )
        except Exception as e:
            if semantic_intent and self.passage_semantic_runtime is not None:
                try:
                    self.passage_semantic_runtime.cancel_target_edit(semantic_intent, str(e))
                except Exception:
                    pass
            try: self._rollback_paths(backup,tx_paths,existed)
            finally:
                try: self.journal.rollback(journal_tx,str(e))
                except Exception: pass
            self._index_cache.clear(); self._checks_by_verse_cache = None; raise
        commit_metadata = {'operation':'scriptureEdit','chapter':str(chapter),'verse':str(verse)}
        commit_metadata.update(journal_metadata or {})
        self.journal.commit(journal_tx, commit_metadata)
        # V11-000a review fix (F6): the invalid marker written above (and,
        # for the editor-edit route below, whatever complete_target_edit
        # does to Stage 6B/7/8 records) changes what Stage 6B's
        # WORD_ALIGNMENT evidence should find, so the alignment
        # invalidation memo must be refreshed in the same call that wrote
        # it -- not left for the next project reopen to notice. Placed
        # here, after the commit, because it is the one point both the
        # editor-edit route (strict_context is None) and the
        # correction-application route (strict_context is not None)
        # unconditionally reach. Without this, a location run published
        # later in the same session already reflects the post-edit
        # alignment state, but a subsequent reopen's fresh
        # PassageSemanticRuntime -- comparing current disk state against a
        # memo that never advanced -- incorrectly stales it (see
        # docs/archive/V11-000a_REVIEW_FIX_PROMPT.md F6).
        if self.passage_semantic_runtime is not None:
            self.passage_semantic_runtime.synchronize_alignment_state()
        semantic_invalidation: dict[str, Any] = {}
        if strict_context is None and semantic_intent and self.passage_semantic_runtime is not None:
            try:
                semantic_invalidation = self.passage_semantic_runtime.complete_target_edit(
                    semantic_intent, str(chapter), str(verse),
                )
            except Exception as exc:
                # Leave PREPARED intent on disk for startup replay when possible.
                semantic_invalidation = {"state": "PENDING_REPLAY", "error": str(exc)}
        self._index_cache.clear(); self._checks_by_verse_cache = None
        # Companion state is advisory/audit; project transaction is already durable here.
        try:
            review = self.load_review_state(chapter, verse)
            if review and str(review.get('status', '')).lower() in ('approved','human_approved'):
                self.record_review_state(chapter, verse, 'stale_after_verse_edit', note='Scripture text changed; final approval requires recheck.')
            self.mark_issue_resolutions_recheck(
                chapter, verse, 'stale',
                reason='Scripture text changed; the saved issue resolution requires a new AI review.',
            )
        except Exception:
            pass
        return {'oldText': old_text, 'newText': new_text, 'backup': str(backup), 'verseEdit': str(edit_path), 'alignmentInvalid': str(invalid), 'indexesTouched': touched, 'semanticInvalidation': semantic_invalidation, 'journalTransactionId': journal_tx.transaction_id, 'crossVerseLinksInvalidated': [link['id'] for link in invalidated_links]}

    def record_qa_decision(self, chapter: str | int, verse: str | int, issue_key: str, decision: str, note: str = '', issue: dict[str, Any] | None = None) -> str:
        iso, _ = self._timestamp()
        data = {'bookId':self.book_id,'chapter':str(chapter),'verse':str(verse),'issueKey':issue_key,'decision':decision,'note':note,'issue':copy.deepcopy(issue or {}),'modifiedTimestamp':iso,'app':'translationCore AI Bridge','schemaVersion':1}
        # The key is stored raw. On disk it was sanitised and truncated to 120
        # characters to make a filename, so two finding ids differing only past
        # that point shared one file and silently overwrote each other; a column
        # has no such limit.
        return self._record_human_decision_row(
            kind='qa', chapter=chapter, verse=verse, key=str(issue_key),
            decision=str(decision), payload=data,
        )

    def qa_decisions_for_verse(self, chapter: str | int, verse: str | int) -> dict[str, dict[str, Any]]:
        return {
            str(payload.get('issueKey', '')): payload
            for payload in self._human_decision_payloads(
                kind='qa', chapter=chapter, verse=verse,
            )
        }

    def language_qa_decision_history(
        self, chapter: str | int, verse: str | int, finding_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Every decision ever recorded on this verse's Language QA findings
        (or on one finding), oldest first. Read-only.

        A decision row is updated in place when a finding is decided again,
        so its current value alone loses the history. The history is the row's
        change_log images, which are append-only. Only rows whose issue says
        "languageQa" are included: a decision recorded before issues carried a
        source cannot be told apart from a Greek Room one."""
        identity = self.workbench_identity
        entries: list[dict[str, Any]] = []
        for row in self.workbench.rows(
            'human_decisions', project_id=identity.project_id, book_id=self.book_id,
            equals={'kind': 'qa', 'chapter': str(chapter), 'verse': str(verse)},
        ):
            if finding_id is not None and row.get('key') != finding_id:
                continue
            current = json.loads(row.get('payload_json') or '{}')
            if (current.get('issue') or {}).get('source') != 'languageQa':
                continue
            for event in self.workbench.events_for_row('human_decisions', row['id'], project_id=identity.project_id):
                payload = json.loads(event.get('payload_json') or '{}')
                if not payload:
                    continue  # a pure domain event carries no decision image
                issue = payload.get('issue') or {}
                entries.append({
                    'seq': event['seq'], 'findingId': row.get('key'),
                    'decision': payload.get('decision'), 'note': payload.get('note', ''),
                    'rule': issue.get('rule'), 'ruleId': issue.get('ruleId'),
                    'originalText': issue.get('originalText'),
                    'chosenSuggestion': issue.get('chosenSuggestion'), 'chosenRank': issue.get('chosenRank'),
                    'packVersion': issue.get('packVersion', issue.get('ruleVersion')),
                    'recordedAt': event.get('created_at'), 'revision': event.get('new_revision'),
                    'actorId': event.get('actor_id'),
                })
        entries.sort(key=lambda entry: entry['seq'])
        return entries

    LEDGER_COLUMNS = ('chapter', 'verse', 'ruleId', 'original', 'replacement', 'timestamp', 'user', 'kind')

    def language_qa_change_ledger(self) -> list[dict[str, str]]:
        """Every Scripture change applied through a Language QA Use, and every
        export made over the publication gate, from change_log (layered-rules
        6.5). A Use is recorded as an 'accepted' Language QA decision right
        after the verse edit it applied; each accepted image in a decision
        row's history is one change, even when the same finding id was later
        decided again."""
        identity = self.workbench_identity
        rows: list[dict[str, str]] = []
        for row in self.workbench.rows('human_decisions', project_id=identity.project_id, book_id=self.book_id,
                                       equals={'kind': 'qa'}):
            current = json.loads(row.get('payload_json') or '{}')
            source = (current.get('issue') or {}).get('source')
            if source not in ('languageQa', 'export'):
                continue
            for event in self.workbench.events_for_row('human_decisions', row['id'], project_id=identity.project_id):
                payload = json.loads(event.get('payload_json') or '{}')
                issue = payload.get('issue') or {}
                if source == 'languageQa' and payload.get('decision') == 'accepted':
                    rows.append({'chapter': str(payload.get('chapter', '')), 'verse': str(payload.get('verse', '')),
                                 'ruleId': str(issue.get('ruleId') or issue.get('rule') or ''),
                                 'original': str(issue.get('originalText') or ''),
                                 'replacement': str(issue.get('chosenSuggestion') or issue.get('suggestedReplacement') or ''),
                                 'timestamp': str(event.get('created_at') or ''), 'user': str(event.get('actor_id') or ''),
                                 'kind': 'use'})
                elif source == 'export' and payload.get('issueKey') == 'export.override':
                    rows.append({'chapter': '', 'verse': '', 'ruleId': 'export.override',
                                 'original': f"{len(issue.get('openItems') or [])} open blocking item(s)",
                                 'replacement': str(issue.get('outputPath') or ''),
                                 'timestamp': str(event.get('created_at') or ''), 'user': str(event.get('actor_id') or ''),
                                 'kind': 'export-override'})
        rows.sort(key=lambda r: r['timestamp'])
        return rows

    def timestamp_iso(self) -> str:
        """Public wrapper so bridge_service can stamp a rollup entry with the
        same timestamp format used everywhere else in this file."""
        return self._timestamp()[0]

    # -- whole-book QA-check content-hash cache ------------------------------
    #
    # _usfm_findings_for_book/_names_findings_for_book (bridge_service.py) are
    # whole-book passes, not per-verse, so they don't fit the qaDecisions
    # shape above. Cached here, one `check_cache` row per section, keyed by
    # a content hash of exactly what each check consumes, so an unchanged
    # reopen can skip the subprocess/scan while a real content change still
    # invalidates it. The cache starts empty after #77 (TEAM_ARCHITECTURE.md
    # ss3.2): a miss recomputes.

    def _check_cache_row_id(self, section: str) -> str:
        return natural_row_id(self.workbench_identity.project_id, self.book_id, 'check_cache', section)

    def load_check_cache(self) -> dict[str, Any]:
        data: dict[str, Any] = {'schemaVersion': 1}
        for payload in self.workbench.payloads(
            'check_cache', project_id=self.workbench_identity.project_id, book_id=self.book_id,
        ):
            section = str(payload.get('section') or '')
            if not section:
                continue
            data[section] = {
                'contentHash': payload.get('contentHash'),
                'computedAt': payload.get('computedAt'),
                'findings': list(payload.get('findings') or []),
            }
        return data

    def save_check_cache_section(self, section: str, content_hash: str, findings: list[dict[str, Any]]) -> str:
        identity = self.workbench_identity
        row_id = self._check_cache_row_id(section)
        self.workbench._write(
            'check_cache', row_id,
            project_id=identity.project_id, book_id=self.book_id,
            payload={
                'section': section, 'contentHash': content_hash,
                'computedAt': self._timestamp()[0], 'findings': list(findings),
            },
            actor_id=identity.actor_id, device_id=identity.device_id,
            expected_revision=None,
        )
        return row_id

    # -- persisted Language QA scan (workbench v4) ---------------------------
    #
    # One `language_qa_cache` row per chapter: every verse's raw Language QA
    # result, before decisions, keyed by the verse's text hash under a chapter
    # key (rule pack, termbase, detected language). Written by
    # language_qa_jobs from its worker or from the check-job stage, one
    # transaction per chapter. Derived and regenerable: a miss rescans.

    def _language_qa_cache_row_id(self, chapter: str) -> str:
        return natural_row_id(self.workbench_identity.project_id, self.book_id, 'language_qa_cache', chapter)

    def load_language_qa_cache(self) -> dict[str, dict[str, Any]]:
        """chapter -> {"key", "verses"}, for the whole book in one query."""
        out: dict[str, dict[str, Any]] = {}
        for payload in self.workbench.payloads(
            'language_qa_cache', project_id=self.workbench_identity.project_id, book_id=self.book_id,
        ):
            chapter = str(payload.get('chapter') or '')
            verses = payload.get('verses')
            if chapter and isinstance(verses, dict):
                out[chapter] = {'key': str(payload.get('key') or ''), 'verses': verses}
        return out

    def save_language_qa_chapters(self, chapters: dict[str, tuple[str, dict[str, Any]]]) -> None:
        """chapter -> (key, verses), all in one transaction: a full first pass
        over Psalms is 150 chapters, and one fsync'd commit each more than
        doubled the pass (5.3 s against 2.2 s)."""
        identity = self.workbench_identity
        computed_at = self._timestamp()[0]
        with self.workbench.batch() as batch:
            for chapter, (key, verses) in chapters.items():
                chapter_key = str(chapter)
                batch.write(
                    'language_qa_cache', self._language_qa_cache_row_id(chapter_key),
                    project_id=identity.project_id, book_id=self.book_id,
                    payload={'schemaVersion': 1, 'chapter': chapter_key, 'key': key,
                             'computedAt': computed_at, 'verses': verses},
                    actor_id=identity.actor_id, device_id=identity.device_id,
                    extra_columns={'chapter': chapter_key},
                )

    # -- indic-qa editor stores (workbench v6) --------------------------------
    #
    # Three Bridge-private stores for the reviewer features brought over from
    # indic-qa's editor. None of them writes Scripture: a scoped correction is
    # N ordinary apply_scripture_edit calls, and its batch row only groups
    # them. Every row is rewritten (revision++), never deleted, so change_log
    # keeps every image.

    def _v6_write(self, table: str, row_id: str, payload: dict[str, Any], extra_columns: dict[str, Any]) -> None:
        identity = self.workbench_identity
        self.workbench._write(
            table, row_id, project_id=identity.project_id, book_id=self.book_id, payload=payload,
            actor_id=identity.actor_id, device_id=identity.device_id, expected_revision=None,
            extra_columns=extra_columns,
        )

    def _v6_payload(self, table: str, row_id: str) -> dict[str, Any] | None:
        row = self.workbench.get(table, row_id)
        if row is None:
            return None
        try:
            payload = json.loads(row['payload_json'])
        except (TypeError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    # Batches: one per scoped correction and one per undo of it.

    def _language_qa_batch_row_id(self, batch_id: str) -> str:
        return natural_row_id(self.workbench_identity.project_id, self.book_id, 'language_qa_batch', batch_id)

    def language_qa_batches(self) -> list[dict[str, Any]]:
        """This book's correction batches, newest first."""
        rows = self.workbench.payloads('language_qa_batches', project_id=self.workbench_identity.project_id,
                                       book_id=self.book_id)
        return sorted(rows, key=lambda b: str(b.get('createdAt') or ''), reverse=True)

    def language_qa_batch(self, batch_id: str) -> dict[str, Any] | None:
        return self._v6_payload('language_qa_batches', self._language_qa_batch_row_id(batch_id))

    def record_language_qa_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Write a new batch. `payload` carries `batchId`, `kind` ("accept" |
        "undo"), `state` and `chapter` (where the action was taken)."""
        batch_id = str(payload['batchId'])
        record = {'schemaVersion': 1, **payload}
        self._v6_write('language_qa_batches', self._language_qa_batch_row_id(batch_id), record, {
            'chapter': str(record.get('chapter') or ''), 'kind': str(record.get('kind') or ''),
            'state': str(record.get('state') or ''),
        })
        return record

    def set_language_qa_batch_state(self, batch_id: str, state: str, **fields: Any) -> dict[str, Any]:
        """Move a batch to `state` ("applied" | "undone"), keeping every other
        field. The earlier image stays in change_log."""
        current = self.language_qa_batch(batch_id)
        if current is None:
            raise KeyError(f"no Language QA batch {batch_id}")
        updated = {**current, **fields, 'state': state, 'updatedAt': self._timestamp()[0]}
        self._v6_write('language_qa_batches', self._language_qa_batch_row_id(batch_id), updated, {
            'chapter': str(updated.get('chapter') or ''), 'kind': str(updated.get('kind') or ''), 'state': state,
        })
        return updated

    # Learned fixes: a reviewer's single-word replacement, offered again.

    @staticmethod
    def _nfc(word: str) -> str:
        return unicodedata.normalize('NFC', str(word))

    def _learned_fix_row_id(self, old: str, new: str) -> str:
        return natural_row_id(self.workbench_identity.project_id, self.book_id, 'learned_fix',
                              self._nfc(old), self._nfc(new))

    def learned_fix(self, old: str, new: str) -> dict[str, Any] | None:
        return self._v6_payload('language_qa_learned_fixes', self._learned_fix_row_id(old, new))

    def learned_fixes(self) -> list[dict[str, Any]]:
        """Every learned fix of this book, enabled or not: most used first."""
        rows = self.workbench.payloads('language_qa_learned_fixes', project_id=self.workbench_identity.project_id,
                                       book_id=self.book_id)
        return sorted(rows, key=lambda f: (-int(f.get('count') or 0), str(f.get('old') or ''),
                                           str(f.get('new') or '')))

    def _write_learned_fix(self, fix: dict[str, Any]) -> dict[str, Any]:
        self._v6_write('language_qa_learned_fixes', self._learned_fix_row_id(fix['old'], fix['new']), fix, {
            'old_word': fix['old'], 'new_word': fix['new'], 'enabled': 1 if fix.get('enabled') else 0,
        })
        return fix

    def record_learned_fix(self, old: str, new: str, *, ref: str, reviewer: str, source: str,
                           n: int = 1) -> dict[str, Any]:
        """The reviewer replaced `old` with `new` `n` times (an edit, a scoped
        correction, a flag's suggested form). Adds to the count; a forgotten
        fix stays forgotten until it is restored."""
        old, new = self._nfc(old), self._nfc(new)
        now = self._timestamp()[0]
        current = self.learned_fix(old, new)
        if current is None:
            fix = {'schemaVersion': 1, 'old': old, 'new': new, 'count': max(1, int(n)), 'firstRef': ref,
                   'lastRef': ref, 'reviewer': reviewer, 'source': source, 'enabled': True,
                   'createdAt': now, 'updatedAt': now}
        else:
            fix = {**current, 'count': int(current.get('count') or 0) + max(1, int(n)), 'lastRef': ref,
                   'reviewer': reviewer, 'source': source, 'updatedAt': now}
        return self._write_learned_fix(fix)

    def retract_learned_fix(self, old: str, new: str, *, n: int = 1) -> dict[str, Any] | None:
        """Take back `n` uses (the reviewer typed the word back, or undid the
        change). At zero the fix stops being offered; the row is kept."""
        current = self.learned_fix(old, new)
        if current is None:
            return None
        count = max(0, int(current.get('count') or 0) - max(1, int(n)))
        return self._write_learned_fix({**current, 'count': count, 'updatedAt': self._timestamp()[0]})

    def set_learned_fix_enabled(self, old: str, new: str, enabled: bool) -> dict[str, Any]:
        """Forget (False) or restore (True) a learned fix."""
        current = self.learned_fix(old, new)
        if current is None:
            raise KeyError(f"no learned fix {old} -> {new}")
        return self._write_learned_fix({**current, 'enabled': bool(enabled), 'updatedAt': self._timestamp()[0]})

    def learned_map(self) -> dict[str, list[str]]:
        """old -> [new, ...] for every fix that is enabled and still counted,
        the most used replacement first. What a Language QA pass offers."""
        out: dict[str, list[str]] = {}
        for fix in self.learned_fixes():
            if fix.get('enabled') and int(fix.get('count') or 0) > 0:
                out.setdefault(str(fix['old']), []).append(str(fix['new']))
        return out

    # Flags: a reviewer's question on a passage. Deleting is a status.

    def _language_qa_flag_row_id(self, flag_id: str) -> str:
        return natural_row_id(self.workbench_identity.project_id, self.book_id, 'language_qa_flag', flag_id)

    def language_qa_flag(self, flag_id: str) -> dict[str, Any] | None:
        return self._v6_payload('language_qa_flags', self._language_qa_flag_row_id(flag_id))

    def language_qa_flags(self, *, chapter: str | None = None, status: str | None = None,
                          include_deleted: bool = False) -> list[dict[str, Any]]:
        """This book's flags in reading order (chapter, verse, start)."""
        equals = {'chapter': str(chapter)} if chapter is not None else None
        rows = self.workbench.payloads('language_qa_flags', project_id=self.workbench_identity.project_id,
                                       book_id=self.book_id, equals=equals)
        if status is not None:
            rows = [f for f in rows if f.get('status') == status]
        elif not include_deleted:
            rows = [f for f in rows if f.get('status') != 'deleted']

        def order(f: dict[str, Any]) -> tuple:
            def num(value: Any) -> tuple[int, str]:
                text = str(value or '')
                digits = ''.join(ch for ch in text.split('-')[0] if ch.isdigit())
                return (int(digits) if digits else 0, text)
            return (num(f.get('chapter')), num(f.get('verse')), int(f.get('start') or 0), str(f.get('createdAt')))
        return sorted(rows, key=order)

    def _write_language_qa_flag(self, flag: dict[str, Any]) -> dict[str, Any]:
        self._v6_write('language_qa_flags', self._language_qa_flag_row_id(flag['flagId']), flag, {
            'chapter': str(flag.get('chapter') or ''), 'verse': str(flag.get('verse') or ''),
            'status': str(flag.get('status') or ''),
        })
        return flag

    def record_language_qa_flag(self, flag: dict[str, Any], *, reviewer: str) -> dict[str, Any]:
        """A new flag, status "open". The caller has validated the fields."""
        now = self._timestamp()[0]
        record = {'schemaVersion': 1, **flag, 'flagId': uuid.uuid4().hex[:16], 'reviewer': reviewer,
                  'status': 'open', 'createdAt': now, 'updatedAt': now}
        return self._write_language_qa_flag(record)

    def update_language_qa_flag(self, flag_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        """Change a flag's fields (status "deleted" is a delete)."""
        current = self.language_qa_flag(flag_id)
        if current is None:
            raise KeyError(f"no Language QA flag {flag_id}")
        protected = {'flagId', 'createdAt', 'schemaVersion'}
        updated = {**current, **{k: v for k, v in patch.items() if k not in protected},
                   'updatedAt': self._timestamp()[0]}
        return self._write_language_qa_flag(updated)

    # Verse change history: the native checkData/verseEdits records every
    # Scripture edit already writes (apply_scripture_edit).

    def verse_edit_history(self, chapter: str | int, verse: str | int) -> list[dict[str, Any]]:
        """Every recorded edit of one verse, newest first."""
        entries: list[dict[str, Any]] = []
        for path in self._state_files_for_verse('verseEdits', chapter, verse):
            try:
                record = _read_json(path)
            except Exception:
                continue
            if isinstance(record, dict):
                entries.append(record)
        return sorted(entries, key=lambda e: str(e.get('modifiedTimestamp') or ''), reverse=True)

    def verse_edit_counts(self, chapter: str | int) -> dict[str, int]:
        """verse -> number of recorded edits, for one chapter, without reading
        any record: one directory listing per edited verse."""
        folder = self.check_dir / 'verseEdits' / self.book_id / str(chapter)
        counts: dict[str, int] = {}
        if not folder.is_dir():
            return counts
        for verse_dir in folder.iterdir():
            if verse_dir.is_dir():
                n = sum(1 for p in verse_dir.glob('*.json'))
                if n:
                    counts[verse_dir.name] = n
        return counts

    def progress_finding_status(self, chapter: str | int, verse: str | int, finding_id: str) -> str | None:
        """The rollup's status for one finding, or None when the rollup has no
        row for it (no check job has reported it)."""
        row = self.workbench.get(
            'progress_findings', self._progress_finding_row_id(str(chapter), str(verse), str(finding_id)))
        if row is None:
            return None
        try:
            return str(json.loads(row['payload_json']).get('status') or '')
        except (TypeError, ValueError, KeyError):
            return None

    # -- per-chapter check-finding snapshots ---------------------------------
    #
    # The progress rollup below records only finding id -> status; the
    # findings themselves (Wildebeest spans, local alignment/editorial
    # issues, explanations, suggested replacements) used to live only in the
    # check job's in-memory result and were gone once it finished. The
    # project QA report (qa_report.py) needs them, so a succeeded check job
    # leaves the chapter's findings here as well
    # (BridgeEngine._on_check_job_complete): one `check_findings` row per
    # chapter, the whole chapter as one payload because qa_report reads whole
    # chapters. Read-only for everyone else.

    def _check_findings_row_id(self, chapter: str) -> str:
        return natural_row_id(self.workbench_identity.project_id, self.book_id, 'check_findings', chapter)

    def save_check_findings_snapshot(self, chapter: str | int, verses: dict[str, list[dict[str, Any]]],
                                     language_qa: dict[str, list[dict[str, Any]]] | None = None) -> str:
        """`language_qa` is the Language QA stage's findings per verse, open
        and decided (each decided one carries `decision`). Kept apart from
        `verses`, which every reader takes as QaFinding dicts. None (a job
        without the stage) keeps the chapter's previous Language QA share."""
        identity = self.workbench_identity
        chapter_key = str(chapter)
        row_id = self._check_findings_row_id(chapter_key)
        if language_qa is None:
            previous = self.workbench.get('check_findings', row_id)
            try:
                language_qa = json.loads(previous['payload_json']).get('languageQa') if previous else None
            except (TypeError, ValueError, KeyError):
                language_qa = None
        payload: dict[str, Any] = {
            'schemaVersion': 1, 'bookId': self.book_id, 'chapter': chapter_key,
            'updatedAt': self._timestamp()[0],
            'verses': {str(v): list(findings) for v, findings in verses.items()},
        }
        if isinstance(language_qa, dict):
            payload['languageQa'] = {str(v): list(f) for v, f in language_qa.items()}
        self.workbench._write(
            'check_findings', row_id,
            project_id=identity.project_id, book_id=self.book_id,
            payload=payload,
            actor_id=identity.actor_id, device_id=identity.device_id,
            expected_revision=None,
            extra_columns={'chapter': chapter_key},
        )
        return row_id

    def language_qa_snapshots(self) -> dict[tuple[str, str], list[dict[str, Any]]]:
        """(chapter, verse) -> the Language QA findings the last check job with
        the Language QA stage reported, for the whole book in one query. Open
        ones carry no `decision`; decided ones carry the decision that hid
        them at the time (the progress rollup has the current status)."""
        out: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for payload in self.workbench.payloads(
            'check_findings', project_id=self.workbench_identity.project_id, book_id=self.book_id,
        ):
            chapter = str(payload.get('chapter') or '')
            by_verse = payload.get('languageQa')
            if not chapter or not isinstance(by_verse, dict):
                continue
            for verse, findings in by_verse.items():
                if isinstance(findings, list):
                    out[(chapter, str(verse))] = [f for f in findings if isinstance(f, dict)]
        return out

    def load_check_findings_snapshot(self, chapter: str | int) -> dict[str, list[dict[str, Any]]]:
        found = self.workbench.payloads(
            'check_findings', project_id=self.workbench_identity.project_id,
            book_id=self.book_id, equals={'chapter': str(chapter)},
        )
        if not found:
            return {}
        verses = found[0].get('verses')
        if not isinstance(verses, dict):
            return {}
        return {
            str(v): [f for f in findings if isinstance(f, dict)]
            for v, findings in verses.items() if isinstance(findings, list)
        }

    # -- AI triage verdicts ---------------------------------------------------
    #
    # One `triage_verdicts` row per book, not per chapter: the store is read
    # whole (the report screen merges every verdict in the collection at
    # once), so one row holding the whole map is the shape the file had.
    # Records are keyed by tc_ai_bridge.triage.triage_hash, which hashes the
    # finding's evidence -- a finding whose evidence changed loses its cached
    # verdict rather than carrying a stale one forward. Purely additive:
    # nothing in the offline check or report flow reads this.

    def _triage_row_id(self) -> str:
        return natural_row_id(self.workbench_identity.project_id, self.book_id, 'triage_verdicts')

    def load_triage_records(self) -> dict[str, Any]:
        found = self.workbench.payloads(
            'triage_verdicts', project_id=self.workbench_identity.project_id, book_id=self.book_id,
        )
        entries = found[0].get('entries') if found else None
        if not isinstance(entries, dict):
            return {}
        return {str(k): v for k, v in entries.items() if isinstance(v, dict)}

    def save_triage_records(self, entries: dict[str, Any]) -> str:
        identity = self.workbench_identity
        row_id = self._triage_row_id()
        self.workbench._write(
            'triage_verdicts', row_id,
            project_id=identity.project_id, book_id=self.book_id,
            payload={
                'schemaVersion': 1, 'bookId': self.book_id,
                'updatedAt': self._timestamp()[0],
                'entries': {str(k): v for k, v in entries.items() if isinstance(v, dict)},
            },
            actor_id=identity.actor_id, device_id=identity.device_id,
            expected_revision=None,
        )
        return row_id

    def clear_triage_records(self) -> bool:
        identity = self.workbench_identity
        receipt = self.workbench._delete(
            'triage_verdicts', self._triage_row_id(),
            project_id=identity.project_id, book_id=self.book_id,
            actor_id=identity.actor_id, device_id=identity.device_id,
        )
        return receipt is not None

    # -- per-book progress rollup --------------------------------------------
    #
    # Incrementally-updated summary of human-review and AI-check progress,
    # read by the project dashboard, the QA report and the exception queue.
    # Never rebuilt by a full rescan of decisions on every read -- callers
    # update just the chapter/verse that changed (bridge_service.py's
    # decide_verse and check-job completion hook).
    #
    # #77 normalised the old `.bridge/progress.json` into three workbench
    # tables: one `progress_chapters` row per chapter (verseCount, aiChecked,
    # aiCheckedAt), one `progress_findings` row per finding id -> status, and
    # one `progress_totals` row per book. `load_progress_rollup()` reassembles
    # exactly the dict the file held, so no reader reshapes. Writing the totals
    # also refreshes the app-level `project_progress_cache`, which is what the
    # multi-book dashboard reads instead of opening every sibling's database.

    def _progress_chapter_row_id(self, chapter: str) -> str:
        return natural_row_id(self.workbench_identity.project_id, self.book_id, 'progress_chapter', chapter)

    def _progress_finding_row_id(self, chapter: str, verse: str, finding_id: str) -> str:
        return natural_row_id(
            self.workbench_identity.project_id, self.book_id, 'progress_finding', chapter, verse, finding_id,
        )

    def _progress_totals_row_id(self) -> str:
        return natural_row_id(self.workbench_identity.project_id, self.book_id, 'progress_totals')

    def load_progress_rollup(self) -> dict[str, Any]:
        project_id = self.workbench_identity.project_id
        chapters: dict[str, Any] = {}
        for payload in self.workbench.payloads(
            'progress_chapters', project_id=project_id, book_id=self.book_id,
        ):
            chapter = str(payload.get('chapter') or '')
            if not chapter:
                continue
            listed = payload.get('checkedVerses')
            chapters[chapter] = {
                'verseCount': int(payload.get('verseCount') or 0),
                'aiChecked': bool(payload.get('aiChecked')),
                'aiCheckedAt': payload.get('aiCheckedAt'),
                # A verse the job checked and found nothing in has no finding
                # row, but it was checked: the file kept an empty entry for it
                # and the QA report's coverage counts it as PASS, so the
                # chapter row lists it.
                'verses': {
                    str(v): {'findings': {}} for v in (listed if isinstance(listed, list) else [])
                },
            }
        for payload in self.workbench.payloads(
            'progress_findings', project_id=project_id, book_id=self.book_id,
        ):
            chapter = str(payload.get('chapter') or '')
            verse = str(payload.get('verse') or '')
            finding_id = str(payload.get('findingId') or '')
            if not (chapter and verse and finding_id):
                continue
            entry = chapters.setdefault(chapter, {
                'verseCount': 0, 'aiChecked': False, 'aiCheckedAt': None, 'verses': {},
            })
            entry['verses'].setdefault(verse, {'findings': {}})['findings'][finding_id] = str(
                payload.get('status') or ''
            )
        totals_rows = self.workbench.payloads(
            'progress_totals', project_id=project_id, book_id=self.book_id,
        )
        totals = dict(totals_rows[0]) if totals_rows else {}
        updated_at = totals.pop('updatedAt', None)
        return {
            'schemaVersion': 1, 'bookId': self.book_id, 'updatedAt': updated_at,
            'chapters': chapters, 'totals': totals,
        }

    def record_progress_decision(
        self, chapter: str | int, verse: str | int, finding_id: str, status: str, *,
        verse_count: int,
    ) -> None:
        """One human decision: the finding's row, plus the chapter row if this
        is the first thing recorded for that chapter. One transaction."""
        identity = self.workbench_identity
        chapter_key, verse_key = str(chapter), str(verse)
        chapter_row_id = self._progress_chapter_row_id(chapter_key)
        chapter_exists = self.workbench.get('progress_chapters', chapter_row_id) is not None
        with self.workbench.batch() as batch:
            if not chapter_exists:
                batch.write(
                    'progress_chapters', chapter_row_id,
                    project_id=identity.project_id, book_id=self.book_id,
                    payload={
                        'chapter': chapter_key, 'verseCount': int(verse_count),
                        'aiChecked': False, 'aiCheckedAt': None, 'checkedVerses': [],
                    },
                    actor_id=identity.actor_id, device_id=identity.device_id,
                    extra_columns={'chapter': chapter_key},
                )
            batch.write(
                'progress_findings', self._progress_finding_row_id(chapter_key, verse_key, str(finding_id)),
                project_id=identity.project_id, book_id=self.book_id,
                payload={
                    'chapter': chapter_key, 'verse': verse_key,
                    'findingId': str(finding_id), 'status': str(status),
                },
                actor_id=identity.actor_id, device_id=identity.device_id,
                extra_columns={'chapter': chapter_key},
            )

    def replace_progress_chapters(self, chapters: dict[str, dict[str, Any]]) -> None:
        """A succeeded check job's result for exactly the chapters it covered.

        Each chapter's row is rewritten and its finding rows replaced -- a
        finding the new run no longer reports loses its row, as the whole
        chapter entry was replaced in the file. Chapters not named are left
        alone. One transaction for the whole job, so a book-wide run is one
        commit rather than one per finding.
        """
        identity = self.workbench_identity
        plan: list[tuple[str, dict[str, Any], dict[str, tuple[str, str, str]], list[str]]] = []
        for chapter, entry in chapters.items():
            chapter_key = str(chapter)
            wanted: dict[str, tuple[str, str, str]] = {}
            verses = entry.get('verses') if isinstance(entry.get('verses'), dict) else {}
            for verse, verse_entry in verses.items():
                findings = verse_entry.get('findings') if isinstance(verse_entry, dict) else None
                for finding_id, status in (findings or {}).items():
                    row_id = self._progress_finding_row_id(chapter_key, str(verse), str(finding_id))
                    wanted[row_id] = (str(verse), str(finding_id), str(status))
            # Read what exists before the write transaction opens, so the batch
            # holds the write lock for as short a time as possible.
            stale = [
                str(row['id']) for row in self.workbench.rows(
                    'progress_findings', project_id=identity.project_id, book_id=self.book_id,
                    equals={'chapter': chapter_key},
                ) if str(row['id']) not in wanted
            ]
            plan.append((chapter_key, entry, wanted, stale))
        with self.workbench.batch() as batch:
            for chapter_key, entry, wanted, stale in plan:
                batch.write(
                    'progress_chapters', self._progress_chapter_row_id(chapter_key),
                    project_id=identity.project_id, book_id=self.book_id,
                    payload={
                        'chapter': chapter_key,
                        'verseCount': int(entry.get('verseCount') or 0),
                        'aiChecked': bool(entry.get('aiChecked')),
                        'aiCheckedAt': entry.get('aiCheckedAt'),
                        'checkedVerses': [str(v) for v in (
                            entry.get('verses') if isinstance(entry.get('verses'), dict) else {}
                        )],
                    },
                    actor_id=identity.actor_id, device_id=identity.device_id,
                    extra_columns={'chapter': chapter_key},
                )
                for row_id in stale:
                    batch.delete(
                        'progress_findings', row_id,
                        project_id=identity.project_id, book_id=self.book_id,
                        actor_id=identity.actor_id, device_id=identity.device_id,
                    )
                for row_id, (verse, finding_id, status) in wanted.items():
                    batch.write(
                        'progress_findings', row_id,
                        project_id=identity.project_id, book_id=self.book_id,
                        payload={
                            'chapter': chapter_key, 'verse': verse,
                            'findingId': finding_id, 'status': status,
                        },
                        actor_id=identity.actor_id, device_id=identity.device_id,
                        extra_columns={'chapter': chapter_key},
                    )

    def save_progress_totals(self, totals: dict[str, Any]) -> dict[str, Any]:
        """Write the book's totals row and refresh the workspace cache from it.

        The cache write happens after the workbench commit, from that commit's
        change_log `seq`. A crash between the two leaves the workbench -- the
        record -- correct, and the cache one step behind, which the next open
        repairs (`sync_progress_cache`).
        """
        identity = self.workbench_identity
        iso = self._timestamp()[0]
        payload = {**dict(totals), 'updatedAt': iso}
        receipt = self.workbench._write(
            'progress_totals', self._progress_totals_row_id(),
            project_id=identity.project_id, book_id=self.book_id, payload=payload,
            actor_id=identity.actor_id, device_id=identity.device_id,
            expected_revision=None,
        )
        self.workspace.upsert_progress_cache(
            self.path, project_id=identity.project_id, book_id=self.book_id,
            totals=dict(totals), updated_at=iso, source_seq=int(receipt['seq']),
        )
        return receipt

    def sync_progress_cache(self) -> str:
        """Repair this project's entry in the workspace progress cache.

        Called on project open. The entry is trusted only if its `source_seq`
        is the change_log seq of the latest progress_totals write *and* it
        names this project id and book -- a re-import at the same folder
        starts a fresh workbench whose seq restarts, and the old entry would
        otherwise look current by accident. Returns 'fresh', 'repaired' or
        'cleared' so the caller can report what happened.
        """
        identity = self.workbench_identity
        current_seq = self.workbench.max_seq(project_id=identity.project_id, table='progress_totals')
        cached = self.workspace.progress_cache_entry(self.path)
        if (
            cached is not None
            and cached['sourceSeq'] == current_seq
            and cached['projectId'] == identity.project_id
            and cached['bookId'] == self.book_id
        ):
            return 'fresh'
        rows = self.workbench.payloads(
            'progress_totals', project_id=identity.project_id, book_id=self.book_id,
        )
        if not rows:
            if cached is not None:
                self.workspace.forget_progress_cache([self.path])
                return 'cleared'
            return 'fresh'
        totals = dict(rows[0])
        updated_at = totals.pop('updatedAt', None)
        self.workspace.upsert_progress_cache(
            self.path, project_id=identity.project_id, book_id=self.book_id,
            totals=totals, updated_at=updated_at, source_seq=current_seq,
        )
        return 'repaired'


def _workbench_db_path(project_root: str | Path) -> Path:
    return Path(project_root).resolve() / '.apps' / 'translationCoreAI' / 'bridge-workbench.sqlite3'


def _peek_workbench(project_root: str | Path) -> sqlite3.Connection | None:
    """A read-only connection to a sibling's workbench database, or None.

    For readers that must look at a book without constructing a
    TranslationCoreProject: the dashboard and the triage report walk every
    sibling in a collection, and a lazy sibling has no manifest, no
    alignmentData and no workbench database. `mode=ro` guarantees this never
    creates the file, and no migration runs -- a database newer than this
    build understands is simply read as far as its tables allow.
    """
    db = _workbench_db_path(project_root)
    if not db.is_file():
        return None
    try:
        conn = sqlite3.connect(
            'file:///' + _url_quote(db.as_posix(), safe='/:') + '?mode=ro', uri=True, timeout=5.0,
        )
    except sqlite3.Error:
        return None
    conn.row_factory = sqlite3.Row
    return conn


def peek_progress_totals(project_root: str | Path) -> dict[str, Any] | None:
    """A sibling book's progress totals straight from its workbench database.

    The dashboard's fallback for a materialized sibling with no entry in the
    workspace cache (the workspace database was reset, or the folder arrived
    from another machine); the caller writes what this returns into the cache
    so the next dashboard load is one query again. None when there is no
    database or no totals row.
    """
    conn = _peek_workbench(project_root)
    if conn is None:
        return None
    try:
        row = conn.execute(
            'SELECT project_id, book_id, payload_json FROM progress_totals ORDER BY updated_at DESC LIMIT 1'
        ).fetchone()
        if row is None:
            return None
        seq = conn.execute(
            "SELECT COALESCE(MAX(seq),0) FROM change_log WHERE project_id=? AND table_name='progress_totals'",
            (row['project_id'],),
        ).fetchone()[0]
        payload = json.loads(row['payload_json'])
    except (sqlite3.Error, TypeError, ValueError):
        return None
    finally:
        conn.close()
    if not isinstance(payload, dict):
        return None
    totals = dict(payload)
    updated_at = totals.pop('updatedAt', None)
    return {
        'projectId': str(row['project_id']), 'bookId': str(row['book_id']),
        'totals': totals, 'updatedAt': updated_at, 'sourceSeq': int(seq),
    }


def read_triage_records(project_root: str | Path, book_id: str) -> dict[str, Any] | None:
    """Peek at a sibling book's triage verdicts without constructing a full
    TranslationCoreProject -- same reason as peek_progress_totals above: a
    lazy sibling has no usable manifest/alignmentData yet, and triage.results
    must still be able to report that it simply has no verdicts. Read-only;
    never creates the sibling's database."""
    conn = _peek_workbench(project_root)
    if conn is None:
        return None
    try:
        row = conn.execute(
            'SELECT payload_json FROM triage_verdicts WHERE book_id=? ORDER BY updated_at DESC LIMIT 1',
            (str(book_id).lower(),),
        ).fetchone()
        if row is None:
            return None
        payload = json.loads(row['payload_json'])
    except (sqlite3.Error, TypeError, ValueError):
        return None
    finally:
        conn.close()
    entries = payload.get('entries') if isinstance(payload, dict) else None
    if not isinstance(entries, dict):
        return None
    return {str(k): v for k, v in entries.items() if isinstance(v, dict)}


class TranslationCoreRoot:
    def __init__(self, root: str | Path):
        p = Path(root).resolve()
        # Accept either translationCore root or its parent.
        if (p / 'translationCore' / 'projects').is_dir():
            p = p / 'translationCore'
        if not (p / 'projects').is_dir():
            raise ProjectError(f'Could not find translationCore/projects under {root}')
        self.path = p

    def projects(self) -> list[TranslationCoreProject]:
        out = []
        for p in sorted((self.path / 'projects').iterdir()):
            if p.is_dir() and (p / 'manifest.json').exists():
                try:
                    out.append(TranslationCoreProject(p))
                except ProjectError:
                    pass
        return out
