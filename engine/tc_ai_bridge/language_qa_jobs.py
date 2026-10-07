"""Disposable, bounded background Language QA. Never writes project files.

Workers exit after a pass; status polling discovers external edits. Generation
checks isolate edits, pause/resume and project switches from in-flight work.
"""
from __future__ import annotations

import collections
import copy
import hashlib
import itertools
import json
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from . import terminology
from .housestyle import bundled_seed, house_style, name_findings, preferences_from, with_seed
from .language_packs import PackError, default_pack, load_project_overrides, loaded_pack
from .language_packs import indic_qa_adapter
from .language_qa_drawn import InlinePolicy, drawn_rule_names, gated_rules, is_drawn, with_drawn
from .language_qa_learned import learned_findings
from .language_packs.indic import ConfusionSet
from .language_packs.lexicon import lexicon_findings, lexicon_fingerprint
from .language_packs.loader import apply_overrides
from .language_packs.registry import AUTO, OFF, available, language_name, language_of_pack, pack_setting, select_pack
from .language_packs.registry import problem as registry_problem
from .language_qa import RULES as COMMON_RULES
from .language_qa import (CROSSING_LIMITATION, FINDING_SOURCE, INLINE_RULES, MAX_VERSE_CHARS,
                          coverage, inline_rule_names, rule_fields, suggestion,
                          RULE_VERSION, detect_language, lift_inline_usfm, scan_text,
                          stable_finding_id, text_hash, word_occurrences, wordlist_findings)

MAX_CHAPTER_BYTES = 2 * 1024 * 1024
MAX_BOOK_FINDINGS = 3000
MAX_CHAPTERS = 1000
MAX_CHAPTER_VERSES = 2000
REFRESH_SECONDS = 15.0
# Rescanned chapters are written in one workbench transaction per this many
# (one fsync'd commit per chapter more than doubled a first Psalms pass).
FLUSH_CHAPTERS = 50


def _may_concern_language_qa(decision: dict[str, Any]) -> bool:
    """True for a decision recorded against a Language QA finding, or recorded
    before decide_verse stamped every decision's issue with a source (a row with
    no `source` key cannot be told apart, so it is kept, never guessed away)."""
    issue = decision.get("issue")
    if not isinstance(issue, dict) or "source" not in issue:
        return True
    return issue["source"] == FINDING_SOURCE


# The rule pack's listRef lists. Phase 6 fills these from the project's house
# style; until then they are empty, so a proper-noun abstain never fires.
HOUSE_STYLE_LISTS: dict[str, frozenset] = {"housestyle.properNouns": frozenset(),
                                           "housestyle.projectWords": frozenset()}
# A pack with no confusion.json: every one-cluster edit costs 1.
PLAIN_DISTANCE = ConfusionSet({"entries": []})


def _read_headings(path: Path, limitations: list[str]) -> dict[str, list[dict[str, Any]]]:
    """A chapter's section headings (`<chapter>.headings.json`, #180), as
    tc_project.chapter_headings reads them; {} when there are none. An
    unreadable file is a coverage note, never a failed pass."""
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        limitations.append(f"Headings not checked: {exc}")
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(verse): [h for h in items if isinstance(h, dict)]
            for verse, items in data.items() if isinstance(items, list)}


def _already_flagged(finding: dict[str, Any], flagged: dict[str, list[tuple[int, int, str]]]) -> bool:
    """Whether a pack layer's finding repeats one the pack's own or the common
    rules raised on this verse: the same category on an overlapping span. The
    verse's raw findings count, before decisions, so ignoring the original
    never lets its repeat through. A heading's offsets index the heading, not
    the verse, so it is never compared."""
    if finding.get("context") == "heading":
        return False
    start, end, category = int(finding["start"]), int(finding["end"]), str(finding.get("category") or "")
    return any(c == category and s < end and start < e
               for s, e, c in flagged.get(f"{finding['chapter']}:{finding['verse']}", ()))


def project_rule_pack(project_path: str | Path, name: str | None) -> tuple[Any, list[str]]:
    """The named bundled pack narrowed by the project's overrides, and any
    notes about overrides that were refused or unreadable. (None, notes) when
    no pack applies or it cannot be loaded: the common checks still run."""
    if not name:
        return None, []
    try:
        base = default_pack(name)
    except (OSError, PackError) as exc:
        return None, [f"Rule pack {name} unavailable; common checks only: {exc}"]
    overrides, problems = load_project_overrides(project_path, name)
    pack = apply_overrides(base, overrides)
    return pack, [f"Rule pack: {p}" for p in problems + pack.problems]


def project_pack_name(project: Any, manager: "LanguageQaManager | None" = None) -> str | None:
    """The pack a project uses, for requests that must not scan: the one the
    manager's last pass resolved (script evidence included), else what the
    setting and the declared language alone decide. None: common checks."""
    if manager is not None and manager.pack_name():
        return manager.pack_name()
    setting = pack_setting(project.manifest)
    if setting == OFF:
        return None
    if setting != AUTO:
        return setting
    target = project.manifest.get("target_language", {})
    return select_pack(str(target.get("id") or "")) if isinstance(target, dict) else None


def resolve_language(detection: dict[str, Any], setting: str) -> dict[str, Any]:
    """The detection, with the project's Language QA setting applied (manifest
    `language_qa.pack`): "auto" keeps what detect_language decided; "off"
    runs the common checks only; a pack name runs that pack, whatever the
    metadata says, because a person chose it. `pack` is the resolved pack
    name or "common"."""
    resolved = {**detection, "setting": setting}
    if setting == OFF:
        resolved.update(pack="common", basis="setting-off",
                        message="Language QA is set to common checks only (Settings > Language QA).")
    elif setting != AUTO:
        language = language_of_pack(setting) or ""
        resolved.update(pack=setting, basis="setting", language=language or resolved["language"],
                        message=f"{language_name(language) if language else setting} rules, chosen in Settings "
                                f"> Language QA.")
        if detection.get("basis") == "metadata-conflict" or (
                detection.get("pack") not in {"common", setting} and detection.get("basis") != "undetermined"):
            resolved["message"] += f" (The project metadata and text suggested otherwise: {detection['message']})"
    return resolved


MAX_FALSE_POSITIVES = 500
STATUS_VIEWS = frozenset({"findings", "recheck", "falsePositives"})
SUPPRESSING_DECISIONS = frozenset({"ignored", "rejected"})  # "rejected" = marked as a false positive


def decision_effect(finding: dict[str, Any], decision: dict[str, Any] | None) -> str | None:
    """What a recorded decision does to the finding it names.

    None: no effect, show the finding. "accepted" is an audit record of a
    past Use, not a standing verdict: if the identical text comes back at the
    same place (same id), that is a new problem, not a stale one. Suppressing
    it too was a real bug, caught in desktop acceptance.
    "suppress": "ignored" or "rejected", made under this finding's current
    pack version and rule revision.
    "recheck": the same decision made under an older pack version or rule
    revision. The rule changed underneath it, so it is not re-applied
    silently: the finding is shown again, flagged previouslyIgnored (Phase
    1.5). A legacy decision that recorded no version at all cannot be
    compared and still suppresses."""
    if not decision or str(decision.get("decision", "")) not in SUPPRESSING_DECISIONS:
        return None
    issue = decision.get("issue") if isinstance(decision.get("issue"), dict) else {}
    pack = issue.get("packVersion")
    revision = issue.get("ruleRevision")
    legacy = issue.get("ruleVersion")
    if pack is None and isinstance(legacy, str):
        # Only the legacy field: before Phase 1 it was the pack version alone
        # ("language-qa-6"); for a pack rule it is "<pack>@<version>#<revision>".
        pack, _, rev = legacy.rpartition("#") if "#" in legacy else (legacy, "", "")
        if revision is None and rev.isdigit():
            revision = int(rev)
    if pack is not None and pack != finding.get("packVersion"):
        return "recheck"
    if revision is not None and revision != finding.get("ruleRevision"):
        return "recheck"
    return "suppress"


def apply_decisions(findings: list[dict[str, Any]], decisions: dict[str, dict[str, Any]],
                    false_positives: list[dict[str, Any]],
                    decided: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """The findings still shown after decisions. Every finding is handled
    the same way, whatever its rule. A suppressed "rejected" finding is kept
    in `false_positives`, for the panel's own list. `decided`, when given,
    collects finding id -> decision for every suppressed finding (the
    progress rollup counts them as decided, not open)."""
    shown = []
    for finding in findings:
        decision = decisions.get(finding["id"])
        effect = decision_effect(finding, decision)
        if effect == "suppress":
            if decision and decision.get("decision") == "rejected":
                false_positives.append(finding)
            if decided is not None and decision:
                decided[finding["id"]] = str(decision.get("decision"))
            continue
        shown.append({**finding, "previouslyIgnored": True} if effect == "recheck" else finding)
    return shown


def reported_language_qa(project: Any, rollup: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Every Language QA finding the last check job with the stage reported,
    each with `status`: the progress rollup's current status for it (a
    decision made since the job updates the rollup, Phase 4.1), else the
    decision it was hidden by, else "open". The one reader the QA report, the
    exception queue and the publication gate share, so they cannot disagree.
    Persisted state only: it never scans."""
    snapshots = project.language_qa_snapshots()
    if not snapshots:
        return []
    rollup = rollup if rollup is not None else project.load_progress_rollup()
    chapters = rollup.get("chapters", {}) if isinstance(rollup.get("chapters"), dict) else {}
    out: list[dict[str, Any]] = []
    for (chapter, verse), findings in snapshots.items():
        chapter_entry = chapters.get(chapter) if isinstance(chapters.get(chapter), dict) else {}
        verse_entry = (chapter_entry.get("verses") or {}).get(verse) or {}
        statuses = verse_entry.get("findings") if isinstance(verse_entry, dict) else {}
        for finding in findings:
            current = (statuses or {}).get(str(finding.get("id", "")))
            out.append({**finding, "status": str(current or finding.get("decision") or "open")})
    return out


LANGUAGE_QA_BLOCKING = ("high", "high")  # (severity, confidence) of an open finding that blocks


def language_qa_open_counts(findings: list[dict[str, Any]]) -> dict[str, int]:
    """Open Language QA findings, counted the way the publication gate and the
    exception queue use them."""
    open_findings = [f for f in findings if f.get("status") in ("open", "needs_discussion")]
    return {
        "open": len(open_findings),
        "blocking": sum(1 for f in open_findings
                        if (str(f.get("severity")), str(f.get("confidence"))) == LANGUAGE_QA_BLOCKING),
        "high": sum(1 for f in open_findings if f.get("severity") == "high"),
        "medium": sum(1 for f in open_findings if f.get("severity") == "medium"),
        "low": sum(1 for f in open_findings if f.get("severity") == "low"),
    }


def _hidden(findings: list[dict[str, Any]], decided: dict[str, str]) -> list[dict[str, Any]]:
    """The findings a decision hides, each with that decision: the reports list
    them as resolved rows."""
    return [{**f, "decision": decided[f["id"]]} for f in findings if f["id"] in decided]


# Bump when the shape of a cached verse entry changes: persisted entries from
# an older build are then rescanned rather than misread.
SCAN_CACHE_VERSION = 3  # 2: `words` holds [count, start, end]; `firstSeen` is gone. 3: in-code rules gained `enabled`, and a pair across a poetry line moved to its first word (2026-09-28)


def verse_hash(text: Any) -> str:
    raw = text if isinstance(text, str) else json.dumps(text, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8", errors="surrogatepass")).hexdigest()


def chapter_cache_key(language_pack: str, pack_fingerprint: str, raw_terms: Any, lists_fingerprint: str = "",
                      lexicon: str = "") -> str:
    """What a cached verse result depends on besides its own text: the
    resolved pack name (or "common") and its fingerprint, the termbase, the
    house-style lists, and the pack lexicon's fingerprint (its known splits
    run in the verse scan)."""
    return hashlib.sha1(json.dumps(
        [SCAN_CACHE_VERSION, RULE_VERSION, language_pack, pack_fingerprint, raw_terms, lists_fingerprint, lexicon],
        sort_keys=True, ensure_ascii=False, default=str,
    ).encode("utf-8")).hexdigest()


def scan_verse(book: str, chapter: str, verse: str, text: Any, *, pack: Any,
               lists: dict[str, frozenset], term_index: Any) -> dict[str, Any]:
    """Everything Language QA finds in one verse, before decisions. Pure: the
    background worker (live editing) and the check-job stage both call this,
    and its result is what the persisted cache holds, keyed by `hash`.

    `words` feeds the book-level wordlist audit, compactly: word ->
    [count, start, end] of its first occurrence in raw code points (the text
    itself is sliced from the verse when a pass needs it; this map was 75% of
    a book's persisted size when it held the full location). `limitations`
    are per-verse coverage notes (a candidate crossing markup, the per-verse
    finding limit)."""
    entry: dict[str, Any] = {"hash": verse_hash(text)}
    if not verse[:1].isdigit() or not isinstance(text, str):
        entry["skipped"] = True
        return entry
    result = scan_text(text, book=book, chapter=chapter, verse=verse, pack=pack, lists=lists)
    limitations = list(result["limitations"])
    verse_findings: list[dict[str, Any]] = []
    words: dict[str, list[int]] = {}
    entry["textHash"] = result["textHash"]
    if not result["checked"]:
        entry.update(skipped=True, limitations=limitations)
        return entry
    if pack is not None:
        # Same visible text scan_text read; every span below is translated
        # back to raw code points, and one that would cross lifted markup is
        # dropped, as scan_text does.
        lifted, _ = lift_inline_usfm(text)
        assert lifted is not None  # scan_text checked this verse
        crossing = 0
        for word, w_start, w_end in word_occurrences(lifted.visible):
            span = lifted.raw_span(w_start, w_end)
            if span is None:
                continue  # a word split by markup is not one word
            if word in words:
                words[word][0] += 1
            else:
                words[word] = [1, span[0], span[1]]
        term_occurrences: dict[tuple[str, str], int] = {}
        for visible_match in terminology.find_deprecated_forms(lifted.visible, term_index):
            span = lifted.raw_span(visible_match["start"], visible_match["end"])
            if span is None:
                crossing += 1
                continue
            match = {**visible_match, "start": span[0], "end": span[1],
                     "matchedText": text[span[0]:span[1]]}
            key = ("terminology.deprecated-form", match["matchedText"])
            term_occurrences[key] = term_occurrences.get(key, 0) + 1
            # Always advance the counter above, even when this specific
            # occurrence ends up suppressed by a decision -- otherwise a later,
            # undecided occurrence of the same word in the same verse would
            # shift onto a different, unstable id once an earlier one is
            # ignored.
            finding_id = stable_finding_id(
                book, chapter, verse, "terminology.deprecated-form",
                match["matchedText"], term_occurrences[key])
            preferred = ", ".join(match["preferredRenderings"]) or "no preferred form recorded yet"
            note = f" {match['note']}" if match["note"] else ""
            ending = (f' (matched with the ending "{match["suffix"]}"; confirm it is this term)'
                      if match.get("suffix") else "")
            fields = rule_fields("terminology.deprecated-form", [
                suggestion(text, "termbase", f'{why} for {match["conceptId"]}')
                for text, why in match.get("suggestions") or []
            ])
            if match.get("confidence") == "medium":
                fields["confidence"] = "medium"  # a generated case ending: the reviewer confirms (termbase v3)
            verse_findings.append({
                "id": finding_id,
                "book": book, "chapter": chapter, "verse": verse,
                "rule": "terminology.deprecated-form", "severity": "high",
                "start": match["start"], "end": match["end"],
                "originalText": match["matchedText"],
                "message": (f'"{match["matchedText"]}" is marked deprecated for '
                           f'{match["conceptId"]}{ending}. Preferred form: {preferred}.{note} '
                           f'Verify this occurrence.'),
                "textHash": result["textHash"], "ruleVersion": RULE_VERSION,
                "status": "review-needed",
                **fields,
            })
        if crossing:
            limitations.append(f"{crossing} terminology {CROSSING_LIMITATION}")
    entry.update(findings=verse_findings + result["findings"], limitations=limitations, words=words)
    return entry


class LanguageQaManager:
    def __init__(self, *, debounce: float = .75, yield_seconds: float = .02) -> None:
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._generation = 0
        # (project path, book, declared language, book dir, Language QA setting)
        self._context: tuple[str, str, str, Path, str] | None = None
        # The pack the last pass resolved to, for requests before its result.
        self._pack_name: str | None = None
        self._paused = False
        self._blocked_reason = ""
        self._foreground = 0.0
        self._last_deferral = 0.0
        self._last_scan = 0.0
        self._debounce = debounce
        self._yield_seconds = yield_seconds
        # chapter -> {"key", "verses": {verse: scan_verse() entry}}: the
        # in-memory mirror of the project's persisted `language_qa_cache`,
        # read once per bind (`_store` is the project's load/save pair).
        self._cache: dict[str, dict[str, Any]] = {}
        self._cache_loaded = False
        self._store: tuple[Callable[[], Any], Callable[[str, str, dict[str, Any]], Any]] | None = None
        # One pass at a time: the background worker and the check-job stage
        # share the scan and the cache, never concurrently.
        self._pass_lock = threading.Lock()
        # "chapter:verse" -> {"findings", "decided"} from the last completed pass,
        # for the check-job stage and the progress rollup.
        self._by_verse: dict[str, dict[str, Any]] = {}
        self._source_signature: tuple[tuple[str, int, int], ...] | None = None
        self._paused_state: str | None = None
        self._terminology_loader: Callable[[], list[dict[str, Any]]] | None = None
        self._decisions_loader: Callable[[], list[dict[str, Any]]] | None = None
        self._housestyle_loader: Callable[[], list[dict[str, Any]]] | None = None
        self._summary: dict[str, Any] = {"state": "idle", "findings": [], "limitations": []}
        # The user's inline threshold, and the indic-qa rules of the last pass it
        # decides (language_qa_drawn). Applied per request: never in a pass.
        self._policy = InlinePolicy()
        self._gated: dict[str, dict[str, Any]] = {}
        # The reviewer's learned fixes (workbench v6), read on every pass so a
        # new one shows on the next pass without a rescan; off in Settings.
        self._learned_loader: Callable[[], list[dict[str, Any]]] | None = None
        self._learned_on = True
        # The last completed pass's word counts, for the panel's Book words list.
        self._book_words: dict[str, Any] = {}

    def configure(self, *, inline_precision: Any = 0, inline_confidence: Any = "low",
                  learned_fixes: bool = True) -> None:
        """Settings > Language QA. The inline threshold takes effect on the next
        request (no rescan); turning learned fixes on or off runs a pass."""
        with self._lock:
            self._policy = InlinePolicy.of(inline_precision, inline_confidence)
            changed = self._learned_on != bool(learned_fixes)
            self._learned_on = bool(learned_fixes)
            if changed and self._context is not None:
                self._schedule()

    def _drawn_rules(self) -> list[str]:
        return drawn_rule_names(self._inline_rules(), self._gated, self._policy)

    def touch(self) -> None:
        self._foreground = time.monotonic()

    def pack_name(self) -> str | None:
        """The pack the bound project's last pass resolved to (None: common
        checks, or no pass yet)."""
        with self._lock:
            return self._pack_name

    def _inline_rules(self) -> list[str]:
        """The rule names drawn inline for this project: from the current pass
        (its project-narrowed pack), else the bundled pack's once loaded. A
        request never loads the pack itself: before the first pass has it,
        no pack finding exists yet, so the non-pack rules are the answer."""
        if self._summary.get("inlineRules"):
            return self._summary["inlineRules"]
        pack = loaded_pack(self._pack_name) if self._pack_name else None
        return inline_rule_names(pack) if pack is not None else sorted(INLINE_RULES)

    def bind(self, project: Any, *, blocked_reason: str = "", autostart: bool = True) -> None:
        """`autostart=False` binds without starting the background worker: a
        caller that runs its own pass (`run_pass`, the collection runner)."""
        target = project.manifest.get("target_language", {})
        declared = str(target.get("id") or "") if isinstance(target, dict) else ""
        with self._lock:
            self._context = (str(project.path), project.book_id, declared, project.book_dir,
                             pack_setting(project.manifest))
            self._pack_name = None
            self._gated = {}
            self._book_words = {}
            self._cache = {}
            self._cache_loaded = False
            self._by_verse = {}
            loader = getattr(project, "load_language_qa_cache", None)
            saver = getattr(project, "save_language_qa_chapters", None)
            self._store = (loader, saver) if callable(loader) and callable(saver) else None
            self._source_signature = None
            self._paused_state = None
            self._terminology_loader = getattr(project, "terminology_rules", None)
            self._decisions_loader = getattr(project, "project_qa_decisions", None)
            self._housestyle_loader = getattr(project, "housestyle_entries", None)
            # Shared across the collection (DECISIONS 2026-10-07); a project
            # without siblings support offers its own book's.
            self._learned_loader = (getattr(project, "collection_learned_fixes", None)
                                    or getattr(project, "learned_fixes", None))
            self._paused = bool(blocked_reason)
            self._blocked_reason = blocked_reason
            if autostart:
                self._schedule()
            else:
                self._generation += 1
                self._summary = {"state": "idle", "findings": [], "limitations": [],
                                 "completedChapters": 0, "totalChapters": 0}
            if blocked_reason:
                self._summary.update(state="failed", error=blocked_reason, incomplete=True,
                                     limitations=["Project recovery must complete before checking."])

    def unbind(self) -> None:
        with self._lock:
            self._context = None
            self._pack_name = None
            self._gated = {}
            self._book_words = {}
            self._generation += 1
            self._wake.set()
            self._cache = {}
            self._cache_loaded = False
            self._by_verse = {}
            self._store = None
            self._source_signature = None
            self._paused_state = None
            self._terminology_loader = None
            self._decisions_loader = None
            self._housestyle_loader = None
            self._learned_loader = None
            self._summary = {"state": "idle", "findings": [], "limitations": []}

    def invalidate(self, chapter: str) -> None:
        """Something in `chapter` changed (an edit, a decision): run a pass.
        Nothing is discarded. The pass rescans only verses whose text hash
        changed (a live edit rescans just the edited verse), and it applies
        decisions afresh, so a decision rescans nothing."""
        with self._lock:
            self._schedule()

    def invalidate_all(self) -> None:
        """Something book-wide changed underneath Language QA -- a termbase
        rule added or edited through Settings (#171) -- so run a pass. The
        termbase is part of every chapter's cache key, so that pass rescans
        what the change can affect. Nothing else would trigger one: the
        idle-refresh check only watches chapter file mtimes/sizes, and without
        this a rule added via terminology.record would sit invisible until an
        unrelated edit happened to trigger a fresh pass."""
        with self._lock:
            self._schedule()

    def pause(self, paused: bool) -> dict[str, Any]:
        with self._lock:
            if self._blocked_reason:
                return self.status()
            was_paused = self._paused
            self._paused = paused
            if paused:
                # Stop scheduling work but keep the last results on screen —
                # pausing is not an invalidation, nothing found so far is wrong.
                self._generation += 1
                self._wake.set()
                if not was_paused:
                    self._paused_state = self._summary.get("state")
                self._summary["state"] = "paused"
                return self.status()
            resumable = (was_paused and self._context is not None
                        and self._paused_state == "completed")
            context = self._context
            generation = self._generation
        if resumable:
            # Nothing forced a real edit while paused. A content-limited
            # chapter (e.g. unbalanced inline USFM) never qualifies for _scan's cache,
            # so resuming unconditionally would redo that pass for the exact
            # same result — visible as the whole book restarting from zero.
            signature = self._chapter_signature(context[3])
            with self._lock:
                if (not self._paused and context == self._context
                        and generation == self._generation
                        and self._summary.get("state") == "paused"
                        and signature == self._source_signature):
                    self._summary["state"] = "completed"
                    self._last_scan = time.monotonic()
                    return self.status()
        with self._lock:
            self._schedule()
        return self.status()

    def _schedule(self) -> None:
        # Called under the small metadata lock; no filesystem work here.
        self._generation += 1
        self._wake.set()
        self._summary = {
            "state": "paused" if self._paused else "queued", "findings": [],
            "limitations": [], "completedChapters": 0, "totalChapters": 0,
        }
        if self._context and not self._paused and self._thread is None:
            self._thread = threading.Thread(target=self._run, name="language-qa", daemon=True)
            self._thread.start()

    def _refresh_if_due(self) -> None:
        """Idle-refresh probe shared by every poll: reschedule when chapter
        files changed on disk since the last completed pass (an external
        editor), at most once per REFRESH_SECONDS."""
        probe: tuple[int, tuple[str, str, str, Path, str]] | None = None
        with self._lock:
            if (self._context and not self._paused and self._thread is None
                    and time.monotonic() - self._last_scan >= REFRESH_SECONDS):
                # Reserve this refresh interval before touching the filesystem.
                # The stdio dispatcher is serial in production, but tests and
                # embedded clients may call status concurrently.
                self._last_scan = time.monotonic()
                probe = (self._generation, self._context)
        if probe is not None:
            generation, context = probe
            signature = self._chapter_signature(context[3])
            with self._lock:
                if (generation == self._generation and context == self._context
                        and not self._paused and self._thread is None
                        and signature != self._source_signature):
                    self._schedule()

    def inline(self, *, chapter: str | None = None) -> dict[str, Any]:
        """Every finding drawn in the text, for one chapter or the whole book --
        not paged. Drawn means reviewed-inline, or an indic-qa finding the
        user's threshold lets through (language_qa_drawn). The verse marks are
        drawn from this; status() is a page for the panel's list, and a page
        cannot back marks (a book with more findings than one page lost marks
        past it). Still bounded: the summary itself never holds more than
        MAX_BOOK_FINDINGS, and each verse contributes at most
        MAX_VERSE_FINDINGS.

        The findings are picked under the lock and copied after it is released.
        Since every indic-qa finding is drawn by default, a chapter's copy can
        be large, and verse.decide and the pass wait on the same lock. That is
        safe because a published finding is never changed in place: a pass
        replaces `_summary` whole, and `update()` touches only top-level keys."""
        self._refresh_if_due()
        with self._lock:
            wanted = None if chapter is None else str(chapter)
            gated, policy = self._gated, self._policy
            findings = [
                f for f in self._summary.get("findings", [])
                if (wanted is None or str(f.get("chapter")) == wanted)
                and is_drawn(f, gated, policy)
            ]
            result = {
                "projectPath": self._context[0] if self._context else "",
                "book": self._context[1] if self._context else "",
                "generation": self._generation,
                "state": self._summary.get("state", "idle"),
                "ruleVersion": RULE_VERSION, "chapter": wanted,
                "inlineRules": list(self._inline_rules()), "drawnRules": self._drawn_rules(),
            }
        result["findings"] = [{**copy.deepcopy(f), "drawn": True} for f in findings]
        return result

    def status(self, *, offset: int = 0, limit: int = 0, view: str = "findings",
               chapter: str | None = None, categories: Iterable[str] | None = None) -> dict[str, Any]:
        """One page of one list. `view`: "findings" (every open finding),
        "recheck" (only those shown again after an old decision expired) or
        "falsePositives" (findings marked as false positives, now hidden).
        `chapter` and `categories` narrow the list before it is paged (the
        panel's scope switch and kind legend). totalFindings counts the
        narrowed list; categoryCounts counts the list narrowed by chapter only,
        so the legend shows every kind. The other two lists' sizes are always
        reported as recheckCount and falsePositiveCount. Each finding on the
        page carries `drawn` (language_qa_drawn)."""
        if view not in STATUS_VIEWS:
            raise ValueError(f"view must be one of {sorted(STATUS_VIEWS)}")
        wanted_categories = None if categories is None else {str(c) for c in categories}
        self._refresh_if_due()
        with self._lock:
            offset = max(0, int(offset))
            limit = max(0, min(100, int(limit)))
            false_positives = self._summary.get("falsePositives", [])
            findings = (false_positives if view == "falsePositives" else
                        [f for f in self._summary.get("findings", []) if f.get("previouslyIgnored")]
                        if view == "recheck" else self._summary.get("findings", []))
            if chapter is not None:
                findings = [f for f in findings if str(f.get("chapter")) == str(chapter)]
            category_counts = collections.Counter(str(f.get("category") or "") for f in findings)
            if wanted_categories is not None:
                findings = [f for f in findings if str(f.get("category") or "") in wanted_categories]
            result = {k: v for k, v in self._summary.items() if k not in {"findings", "falsePositives"}}
            result.update({
                "view": view, "falsePositiveCount": len(false_positives),
                "recheckCount": self._summary.get("recheckCount", 0),
                "projectPath": self._context[0] if self._context else "",
                "book": self._context[1] if self._context else "",
                "generation": self._generation, "ruleVersion": RULE_VERSION,
                "inlineRules": self._inline_rules(), "drawnRules": self._drawn_rules(),
                "inlinePolicy": {"minPrecision": self._policy.min_precision,
                                 "minConfidence": self._policy.min_confidence},
                "categoryCounts": dict(sorted(category_counts.items())),
                "totalFindings": len(findings), "offset": offset,
                "findings": with_drawn(findings[offset:offset + limit], self._gated, self._policy),
                "coverage": coverage(self._summary.get("coverageCategories")),
                # Settings > Language QA: the project's setting and what it may name.
                "setting": self._context[4] if self._context else AUTO,
                "packs": available(),
                "storage": ("Persisted in the project workbench." if self._store is not None
                            else "Session results; regenerated on reopen."),
            })
            return copy.deepcopy(result)

    @staticmethod
    def _chapter_signature(directory: Path) -> tuple[tuple[str, int, int], ...]:
        """Cheap change detector for idle polling; content is hashed by workers.

        Normal editor saves change size or mtime. A scan triggered by Bridge's
        edit hook or another invalidation still hashes content, so equal metadata
        can never make changed content reusable once a scan has been requested.
        """
        entries: list[tuple[str, int, int]] = []
        candidates = (p for p in directory.glob("*.json") if p.stem.isdecimal())
        for path in itertools.islice(candidates, MAX_CHAPTERS + 1):
            try:
                stat = path.stat()
                entries.append((path.name, stat.st_mtime_ns, stat.st_size))
            except OSError:
                entries.append((path.name, -1, -1))
        return tuple(sorted(entries))

    def _cancelled(self, generation: int) -> bool:
        return generation != self._generation or self._paused or self._context is None

    def _yield(self, generation: int) -> bool:
        if self._wake.wait(self._yield_seconds):
            return False
        # Requests never wait for the worker. Cap foreground deferral so regular
        # connector polling cannot starve checking indefinitely.
        now = time.monotonic()
        if now - self._foreground < .1 and now - self._last_deferral >= .5:
            self._last_deferral = now
            self._wake.wait(.05)
        return not self._cancelled(generation)

    @staticmethod
    def _read(path: Path) -> tuple[dict[str, Any], tuple[int, int], str]:
        before = path.stat()
        with path.open("rb") as stream:
            raw = stream.read(MAX_CHAPTER_BYTES + 1)
        if len(raw) > MAX_CHAPTER_BYTES:
            raise ValueError("Chapter exceeds 2 MiB; not checked.")
        after = path.stat()
        signature = (after.st_mtime_ns, after.st_size)
        if signature != (before.st_mtime_ns, before.st_size):
            raise ValueError("Chapter changed while being read; waiting for the next pass.")
        def unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            data: dict[str, Any] = {}
            for key, value in pairs:
                if key in data:
                    raise ValueError(f"Duplicate chapter JSON key: {key!r}; not checked.")
                data[key] = value
            return data

        data = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique_keys)
        if not isinstance(data, dict):
            raise ValueError("Chapter must contain a verse-keyed JSON object.")
        return data, signature, hashlib.sha256(raw).hexdigest()

    def _scan(self, generation: int, context: tuple[str, str, str, Path, str], *,
              cancelled: Callable[[], bool] | None = None, yielding: bool = True) -> dict[str, Any] | None:
        """One book pass. `cancelled`/`yielding` let the check-job stage run the
        same pass on its own thread without the background worker's pauses."""
        with self._pass_lock:
            pending: dict[str, tuple[str, dict[str, Any]]] = {}
            try:
                return self._scan_locked(generation, context, pending,
                                         cancelled=cancelled, yielding=yielding)
            finally:
                # A cancelled pass still keeps what it computed: every entry is
                # keyed by its own text hash, so it is valid for the next pass.
                with self._lock:
                    store = self._store if self._context == context else None
                self._flush(store, pending, [])

    def _scan_locked(self, generation: int, context: tuple[str, str, str, Path, str],
                     pending: dict[str, tuple[str, dict[str, Any]]], *,
                     cancelled: Callable[[], bool] | None, yielding: bool) -> dict[str, Any] | None:
        _, book, declared, directory, setting = context

        def proceed() -> bool:
            if cancelled is not None:
                return not cancelled()
            return self._yield(generation) if yielding else not self._cancelled(generation)

        source_signature = self._chapter_signature(directory)
        candidates = (p for p in directory.glob("*.json") if p.stem.isdecimal())
        paths = sorted(itertools.islice(candidates, MAX_CHAPTERS + 1), key=lambda p: int(p.stem))
        limitations: list[str] = []
        truncated = len(paths) > MAX_CHAPTERS
        if truncated:
            limitations.append("Chapter limit reached; additional chapters omitted.")
            paths = paths[:MAX_CHAPTERS]
        if not paths:
            raise ValueError("No target chapter JSON files available for Language QA.")
        # Bounded sample across current target chapters, never original USFM.
        sample = ""
        for path in paths:
            if not proceed():
                return None
            try:
                data, _, _ = self._read(path)
                for verse, text in itertools.islice(data.items(), MAX_CHAPTER_VERSES):
                    if isinstance(text, str) and verse[:1].isdigit() and len(text) <= MAX_VERSE_CHARS:
                        lifted, _ = lift_inline_usfm(text)
                        if lifted is not None:
                            sample += lifted.visible[:20_000 - len(sample)]
                    if len(sample) >= 20_000:
                        break
            except (OSError, ValueError, UnicodeError):
                pass  # Main pass reports the exact chapter error.
            if len(sample) >= 20_000:
                break
        detection = resolve_language(detect_language(sample, declared), setting)
        if registry_problem():
            # Never a silent fallback: a build without its packs says so.
            limitations.append(registry_problem())
        pack_name = None if detection["pack"] == "common" else detection["pack"]
        with self._lock:
            terminology_loader = self._terminology_loader
            decisions_loader = self._decisions_loader
            learned_loader = self._learned_loader if self._learned_on else None
            store = self._store
        try:
            raw_terms = terminology_loader() if terminology_loader else []
        except Exception as exc:
            raw_terms = []
            limitations.append(f"Terminology unavailable: {exc}")
        term_index = terminology.TermIndex(raw_terms)
        try:
            raw_decisions = decisions_loader() if decisions_loader else []
        except Exception as exc:
            raw_decisions = []
            limitations.append(f"Decisions unavailable: {exc}")
        # The reviewer's learned fixes: old -> [new, ...], most used first, and
        # each (old, new) fix for its count and last place. Read every pass, never
        # part of the chapter cache key: like the profile checker, the learned
        # step runs over the book text after the chapter loop.
        try:
            learned_rows = [f for f in (learned_loader() if learned_loader else [])
                            if f.get("enabled") and int(f.get("count") or 0) > 0]
        except Exception as exc:
            learned_rows = []
            limitations.append(f"Learned fixes unavailable: {exc}")
        learned: dict[str, list[str]] = {}
        for fix in learned_rows:
            learned.setdefault(str(fix["old"]), []).append(str(fix["new"]))
        learned_fixes = {(str(f["old"]), str(f["new"])): f for f in learned_rows}
        # Every QA decision by finding id. decision_effect() decides what each
        # one does to the finding it names; ids are content hashes, so a Greek
        # Room decision can never name a Language QA finding. Decisions are
        # applied when a pass assembles its results, never cached, so a
        # decision never forces a rescan.
        decisions = {
            str(row.get("issueKey", "")): row
            for row in raw_decisions if isinstance(row, dict)
        }
        # House style (Phase 6): word and rule entries hide findings and
        # learned preferences rank suggestions, both at assembly like
        # decisions; the lists feed the pack's abstains inside the verse scan,
        # so they join the cache key.
        with self._lock:
            housestyle_loader = self._housestyle_loader
        try:
            # A pack may bundle a curated seed (ta-irv: the 2026-09-28 review's names).
            seed = bundled_seed(pack_name) if pack_name else []
            style = house_style(with_seed(housestyle_loader() if housestyle_loader else [], seed),
                                preferences_from(raw_decisions))
        except Exception as exc:
            style = house_style([])
            limitations.append(f"House style unavailable: {exc}")
        lists = {**HOUSE_STYLE_LISTS, **style.lists}
        suppressed_by_rule: dict[str, int] = {}

        def settle(raw: list[dict[str, Any]], decided: dict[str, str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
            """(shown, hidden) for one verse's raw findings: decisions first,
            then house style. A house-style hide counts as decided for the
            rollup, and is listed with houseStyleSuppressed for the reports."""
            shown = apply_decisions(raw, decisions, false_positives, decided)
            kept, by_style = [], []
            for finding in shown:
                if style.suppresses(finding):
                    by_style.append({**finding, "decision": "housestyle", "houseStyleSuppressed": True})
                    decided[finding["id"]] = "ignored"
                    rule_id = str(finding.get("ruleId") or "")
                    suppressed_by_rule[rule_id] = suppressed_by_rule.get(rule_id, 0) + 1
                else:
                    kept.append(style.rank(finding))
            return kept, _hidden(raw, {k: v for k, v in decided.items()
                                       if k not in {f["id"] for f in by_style}}) + by_style

        # The rule pack, narrowed by this project's overrides (only narrowing is
        # accepted; refusals become coverage notes).
        rule_pack, pack_problems = project_rule_pack(context[0], pack_name)
        limitations.extend(pack_problems)
        # A profile pack does carry a dictionary; say what it checks instead of
        # detect_language's "no dictionaries" line. A pack chosen in Settings
        # keeps resolve_language's own message.
        if (rule_pack is not None and indic_qa_adapter.is_profile_pack(rule_pack.meta)
                and detection.get("basis") != "setting"):
            detection = {**detection, "message": f"{language_name(rule_pack.language)} spelling, encoding, "
                         f"punctuation and consistency checks from the IRV dictionary; agreement leads are listed, "
                         f"never drawn. Not a grammar or publication review."}
        if pack_name and rule_pack is None:
            detection = {**detection, "pack": "common", "message": pack_problems[0] if pack_problems else
                         detection["message"]}
        # A profile pack (indic-qa, pa/ml/hi/or) checks the whole book after the
        # chapter loop; its rules never run in the per-verse scan. So does a
        # pack's indic-qa layer (ta-irv's OV dictionary checks), after the
        # pack's own per-verse rules.
        profile = rule_pack is not None and indic_qa_adapter.is_profile_pack(rule_pack.meta)
        book_checker = indic_qa_adapter.runs_checker(rule_pack)
        layer = book_checker and not profile
        indic_qa_adapter.release(keep=rule_pack.name if book_checker else None)
        coverage_categories = (sorted({meta.category for meta in COMMON_RULES.values() if meta.enabled}
                                      | {r.category for r in rule_pack.rules if r.enabled})
                               if profile else None)
        with self._lock:
            if self._cancelled(generation) and cancelled is None:
                return None
            self._pack_name = rule_pack.name if rule_pack is not None else None
            self._gated = gated_rules(rule_pack)
            self._summary["inlineRules"] = inline_rule_names(rule_pack)
            self._summary["coverageCategories"] = coverage_categories
        # The pack's lexicon, loaded on this first pass that needs it (never at
        # startup); its known splits run in the verse scan, so it keys the cache.
        lexicon = rule_pack.lexicon() if rule_pack is not None else None
        # A verse's cached result is valid while its text hash and this chapter
        # key are unchanged: the engine's rule version, the resolved pack
        # (fingerprint includes project overrides), the termbase, the lists.
        key = chapter_cache_key(detection["pack"], rule_pack.fingerprint() if rule_pack is not None else "",
                                raw_terms, style.list_fingerprint(),
                                lexicon_fingerprint(lexicon) if rule_pack is not None else "")
        cache = self._load_cache(store, limitations)
        findings: list[dict[str, Any]] = []
        false_positives: list[dict[str, Any]] = []
        by_verse: dict[str, dict[str, Any]] = {}
        checked = skipped = reused = scanned_verses = 0
        book_counts: dict[str, int] = {}
        book_first_seen: dict[str, tuple[str, str, int, int, str, str]] = {}
        book_text: dict[str, dict[str, Any]] = {}  # chapter -> verses, for a profile pack's book step
        # A layer also reads the section headings (#180), and its findings are
        # dropped where the pack or the common rules already flagged the same
        # place: chapter:verse -> [(start, end, category)] of every raw finding.
        book_headings: dict[str, dict[str, list[dict[str, Any]]]] = {}
        flagged: dict[str, list[tuple[int, int, str]]] = {}
        completed = 0
        for completed, path in enumerate(paths):
            if not proceed():
                return None
            with self._lock:
                if cancelled is None and self._cancelled(generation):
                    return None
                self._summary.update(state="running", language=detection,
                                     completedChapters=completed, totalChapters=len(paths))
            chapter = path.stem
            chapter_limitations: list[str] = []
            try:
                data, signature, _ = self._read(path)
                if book_checker or learned:
                    book_text[chapter] = data
                if layer:
                    headings = _read_headings(path.with_name(f"{chapter}.headings.json"), chapter_limitations)
                    if headings:
                        book_headings[chapter] = headings
                cached = cache.get(chapter)
                cached_verses = cached["verses"] if cached and cached.get("key") == key else {}
                verses: dict[str, Any] = {}
                fresh = 0
                if len(data) > MAX_CHAPTER_VERSES:
                    chapter_limitations.append("Chapter verse limit reached; remaining entries omitted.")
                for verse, text in itertools.islice(data.items(), MAX_CHAPTER_VERSES):
                    entry = cached_verses.get(verse)
                    if entry is None or entry.get("hash") != verse_hash(text):
                        if not proceed():
                            return None
                        entry = scan_verse(book, chapter, verse, text, pack=rule_pack,
                                           lists=lists, term_index=term_index)
                        fresh += 1
                    verses[verse] = entry
                final = path.stat()
                if signature != (final.st_mtime_ns, final.st_size):
                    raise ValueError("Chapter changed during checking; waiting for the next pass.")
                if fresh or set(verses) != set(cached_verses):
                    cache[chapter] = {"key": key, "verses": verses}
                    pending[chapter] = (key, verses)
                    if len(pending) >= FLUSH_CHAPTERS:
                        self._flush(store, pending, limitations)
                else:
                    reused += 1
                scanned_verses += fresh
            except (OSError, ValueError, UnicodeError) as exc:
                limitations.append(f"Chapter {chapter}: {exc}")
                continue
            chapter_findings: list[dict[str, Any]] = []
            chapter_skipped = 0
            omitted = False
            for verse, entry in verses.items():
                if entry.get("limitations") and len(chapter_limitations) < 20:
                    chapter_limitations.extend(f"{verse}: {message}" for message in entry["limitations"])
                if entry.get("skipped"):
                    chapter_skipped += 1
                    continue
                checked += 1
                if layer:
                    flagged[f"{chapter}:{verse}"] = [
                        (int(f.get("start", 0)), int(f.get("end", 0)), str(f.get("category") or ""))
                        for f in entry.get("findings", [])]
                text = data.get(verse)
                for word, (count, start, end) in entry.get("words", {}).items():
                    book_counts[word] = book_counts.get(word, 0) + count
                    if word not in book_first_seen and isinstance(text, str):
                        book_first_seen[word] = (chapter, verse, start, end, text[start:end], entry["textHash"])
                if omitted:
                    continue
                decided: dict[str, str] = {}
                # Decisions apply before the room slice, so a suppressed
                # finding never consumes budget it will never use.
                shown, hidden = settle(entry.get("findings", []), decided)
                room = MAX_BOOK_FINDINGS - len(findings) - len(chapter_findings)
                chapter_findings.extend(shown[:max(0, room)])
                by_verse[f"{chapter}:{verse}"] = {
                    "findings": shown[:max(0, room)], "decided": decided, "hidden": hidden}
                if len(shown) > room:
                    chapter_limitations.append("Book finding limit reached; remaining verses omitted.")
                    omitted = True
            del false_positives[MAX_FALSE_POSITIVES:]
            findings.extend(chapter_findings)
            skipped += chapter_skipped
            limitations.extend(f"Chapter {chapter}: {m}" for m in chapter_limitations)
            if chapter_skipped and not chapter_limitations:
                limitations.append(f"Chapter {chapter}: non-verse or non-text entries omitted.")
            if len(findings) >= MAX_BOOK_FINDINGS:
                limitations.append("Book finding limit reached; additional findings/chapters may be omitted.")
                truncated = True
                break
            if len(limitations) >= 200:
                limitations = limitations[:200] + ["Diagnostic limit reached; remaining chapters omitted."]
                truncated = True
                break
        # A truncated pass never opened every chapter, so a word that is genuinely
        # common in the unread tail would look artificially rare here -- exactly
        # the false-positive shape the rarity+similarity guardrail must prevent.
        # With the pack's corpus lexicon (Phase 5) the audit compares each rare
        # word against the whole corpus; without one it falls back to the pack's
        # within-book wordlist audit, which is bounded by its maxTerms.
        wordlist = rule_pack.book_rule("wordlist-variant") if rule_pack is not None and lexicon is None else None
        if truncated:
            limitations.append("Wordlist audit skipped: book scan was truncated.")
            if book_checker:
                limitations.append(f"{rule_pack.name} {'OV dictionary ' if layer else ''}checks skipped: "
                                   f"book scan was truncated.")
        elif wordlist is not None and len(book_counts) > wordlist.params["maxTerms"]:
            limitations.append("Wordlist audit skipped: too many distinct words to compare.")
        else:
            audit = (lexicon_findings(book, book_counts, book_first_seen, lexicon, pack=rule_pack,
                                      rule_fields=rule_fields, suggestion=suggestion)
                     if lexicon is not None else
                     wordlist_findings(book, book_counts, book_first_seen, pack=rule_pack, rule=wordlist)
                     if wordlist is not None else [])
            checked_book: tuple[list[dict[str, Any]], list[str]] | None = ([], [])
            if book_checker:
                try:
                    checked_book = indic_qa_adapter.profile_findings(
                        rule_pack, book, book_text, lift=lift_inline_usfm, max_verse_chars=MAX_VERSE_CHARS,
                        finding_id=stable_finding_id, rule_fields=rule_fields, text_hash=text_hash,
                        crossing_note=CROSSING_LIMITATION, proceed=proceed,
                        headings=book_headings if layer else None)
                except Exception as exc:  # the vendored checker must never fail the pass
                    checked_book = [], [f"{rule_pack.name} {'OV dictionary ' if layer else ''}checks failed; "
                                        f"{'the pack' if layer else 'common'} checks only: "
                                        f"{type(exc).__name__}: {exc}"]
                if checked_book is None:
                    return None
                limitations.extend(checked_book[1])
            if profile:
                audit += checked_book[0]
            if rule_pack is not None:
                audit += name_findings(book, book_counts, book_first_seen,
                                       style.lists.get("housestyle.properNouns", frozenset()),
                                       rule_fields=rule_fields, suggestion=suggestion, rule_version=RULE_VERSION,
                                       corpus_count=lexicon.count if lexicon is not None else None,
                                       distance=(rule_pack.confusion() or PLAIN_DISTANCE).distance)
            if layer:
                for finding in audit:
                    flagged.setdefault(f"{finding['chapter']}:{finding['verse']}", []).append(
                        (int(finding["start"]), int(finding["end"]), str(finding.get("category") or "")))
                audit += [f for f in checked_book[0] if not _already_flagged(f, flagged)]
            audit += learned_findings(book, book_text, learned, fixes=learned_fixes, max_verse_chars=MAX_VERSE_CHARS,
                                      finding_id=stable_finding_id, rule_fields=rule_fields, suggestion=suggestion,
                                      text_hash=text_hash, rule_version=RULE_VERSION)
            for finding in audit:
                decided = {}
                shown, hidden = settle([finding], decided)
                slot = by_verse.setdefault(f"{finding['chapter']}:{finding['verse']}",
                                           {"findings": [], "decided": {}, "hidden": []})
                slot["decided"].update(decided)
                slot["hidden"].extend(hidden)
                if not shown:
                    continue
                if len(findings) >= MAX_BOOK_FINDINGS:
                    limitations.append("Book finding limit reached; wordlist audit findings omitted.")
                    break
                findings.extend(shown)
                slot["findings"].extend(shown)
            del false_positives[MAX_FALSE_POSITIVES:]
        self._flush(store, pending, limitations)
        with self._lock:
            if cancelled is None and self._cancelled(generation):
                return None
            self._by_verse = by_verse
            self._book_words = {"counts": dict(book_counts), "first": dict(book_first_seen),
                                "pack": rule_pack.name if rule_pack is not None else None,
                                "checker": bool(book_checker), "lexicon": lexicon,
                                "projectWords": frozenset(style.lists.get("housestyle.projectWords", frozenset())),
                                "generation": generation}
        return {"state": "completed", "language": detection, "findings": findings,
                "falsePositives": false_positives, "inlineRules": inline_rule_names(rule_pack),
                "rulePack": rule_pack.pack_version if rule_pack is not None else "common",
                "recheckCount": sum(1 for f in findings if f.get("previouslyIgnored")),
                "houseStyleSuppressed": suppressed_by_rule, "coverageCategories": coverage_categories,
                "limitations": limitations, "incomplete": bool(limitations or skipped),
                "checkedVerses": checked, "skippedVerses": skipped,
                "reusedChapters": reused, "scannedVerses": scanned_verses,
                "completedChapters": completed + 1,
                "totalChapters": len(paths), "_sourceSignature": source_signature}

    def _load_cache(self, store: Any, limitations: list[str]) -> dict[str, dict[str, Any]]:
        """The in-memory mirror of the persisted cache, read from the project
        workbench once per bind."""
        with self._lock:
            if self._cache_loaded:
                return self._cache
        loaded: dict[str, dict[str, Any]] = {}
        if store is not None:
            try:
                loaded = store[0]() or {}
            except Exception as exc:
                limitations.append(f"Saved Language QA results unavailable; rescanning: {exc}")
        with self._lock:
            if not self._cache_loaded:
                self._cache = dict(loaded)
                self._cache_loaded = True
            return self._cache

    @staticmethod
    def _flush(store: Any, pending: dict[str, tuple[str, dict[str, Any]]], limitations: list[str]) -> None:
        """Write the rescanned chapters, all in one transaction (at most
        FLUSH_CHAPTERS). A failed write costs a rescan on the next reopen,
        never a wrong result, so it is a coverage note only."""
        if store is None or not pending:
            pending.clear()
            return
        try:
            store[1](dict(pending))
        except Exception as exc:
            if not any(m.startswith("Language QA results not saved") for m in limitations):
                limitations.append(f"Language QA results not saved (they will be recomputed): {exc}")
        pending.clear()

    def run_pass(self, cancel_event: threading.Event | None = None) -> dict[str, Any] | None:
        """The authoritative book pass, run by the check-job stage on the job's
        own thread: the same scan and the same cache as the background worker,
        serialised with it by the pass lock. Returns None when cancelled or when
        nothing is bound. Publishes its result to the panel like a worker pass."""
        with self._lock:
            context = self._context
            generation = self._generation
            if context is not None and self._blocked_reason:
                # Recovery must finish first; the stage then reports nothing.
                return copy.deepcopy(self._summary)
        if context is None:
            return None
        started = time.monotonic()
        result = self._scan(generation, context, yielding=False, cancelled=lambda: (
            (cancel_event is not None and cancel_event.is_set()) or self._context != context))
        if result is None:
            return None
        source_signature = result.pop("_sourceSignature", None)
        result["elapsedSeconds"] = round(time.monotonic() - started, 3)
        with self._lock:
            if self._context == context and generation == self._generation and not self._paused:
                # Nothing changed while the job ran: this is the current result.
                self._generation += 1
                self._summary = result
                if source_signature is not None:
                    self._source_signature = source_signature
                self._last_scan = time.monotonic()
        return result

    def verse(self, chapter: str, verse: str) -> dict[str, Any]:
        """languageQa.verse: one verse's findings from the last completed pass,
        inline or not, for the review panel (layered-rules 4.3)."""
        slot = self.verse_results(chapter, verse)
        with self._lock:
            return {"projectPath": self._context[0] if self._context else "",
                    "generation": self._generation, "state": self._summary.get("state", "idle"),
                    "chapter": chapter, "verse": verse,
                    "findings": with_drawn(slot["findings"], self._gated, self._policy),
                    "hidden": slot.get("hidden", [])}

    def book_words(self, *, min_count: int = 1, limit: int = 500) -> dict[str, Any]:
        """indic-qa's "Book words": every word of the book that is not in the
        dictionary, most frequent first, with its IRV and OV counts where the
        pack's checker knows them, and the project's own word list marked
        `added`. From the last completed pass; nothing is rescanned."""
        with self._lock:
            basis = dict(self._book_words)
        if not basis:
            return {"ready": False, "reason": "Language QA has not finished a pass yet.", "words": [], "total": 0}
        counts, first = basis["counts"], basis["first"]
        words = [w for w, n in counts.items() if n >= max(1, int(min_count))]
        if basis["checker"] and basis["pack"]:
            statuses = indic_qa_adapter.word_statuses(basis["pack"], words)
            if statuses is None:
                return {"ready": False, "reason": "The pack's checker is not loaded; run a pass first.",
                        "words": [], "total": 0}
        elif basis["lexicon"] is not None:
            statuses = {w: ("unknown", 0, 0) for w in words if not basis["lexicon"].count(w)}
        else:
            return {"ready": False, "reason": "This book's Language QA rules have no dictionary to compare with.",
                    "words": [], "total": 0}
        added = basis["projectWords"]
        rows = []
        for word, (status, irv, ov) in statuses.items():
            chapter, verse, *_rest = first.get(word, ("", "", 0, 0, "", ""))
            rows.append({"word": word, "status": "added" if word in added else status, "countBook": counts[word],
                         "countIrv": irv, "countOv": ov, "firstRef": {"chapter": chapter, "verse": verse}})
        rows.sort(key=lambda r: (-r["countBook"], -r["countIrv"], r["word"]))
        return {"ready": True, "generation": basis["generation"], "total": len(rows), "words": rows[:max(1, int(limit))]}

    def scope_findings(self, chapter: str, verse: str, finding_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """(origin finding, every shown finding of the book) from the last
        completed pass, for a scoped correction (language_qa_scope). Raises
        ValueError when the finding is not in it: the text or the pass moved on,
        and the reviewer should act on what is on screen now."""
        with self._lock:
            slot = self._by_verse.get(f"{chapter}:{verse}") or {}
            origin = next((f for f in slot.get("findings", []) if f.get("id") == finding_id), None)
            shown = [f for entry in self._by_verse.values() for f in entry.get("findings", [])]
        if origin is None:
            raise ValueError("That finding is not in the last Language QA pass. Wait for the check to finish, "
                             "then try again.")
        return copy.deepcopy(origin), copy.deepcopy(shown)

    def verse_results(self, chapter: str, verse: str) -> dict[str, Any]:
        """The last pass's findings for one verse: `findings` (open, after
        decisions) and `decided` (finding id -> the decision that hides it)."""
        with self._lock:
            slot = self._by_verse.get(f"{chapter}:{verse}") or {"findings": [], "decided": {}, "hidden": []}
            return copy.deepcopy(slot)

    def _run(self) -> None:
        while True:
            with self._lock:
                if self._paused or self._context is None:
                    self._thread = None
                    return
                generation, context = self._generation, self._context
                self._wake.clear()
            started = time.monotonic()
            try:
                result = None if self._wake.wait(self._debounce) else self._scan(generation, context)
            except Exception as exc:
                result = {"state": "failed", "error": str(exc), "findings": [],
                          "limitations": ["Language QA did not complete."], "incomplete": True}
            with self._lock:
                if generation != self._generation:
                    continue
                if result is not None:
                    source_signature = result.pop("_sourceSignature", None)
                    result["elapsedSeconds"] = round(time.monotonic() - started, 3)
                    self._summary = result
                    if source_signature is not None:
                        self._source_signature = source_signature
                self._last_scan = time.monotonic()
                self._thread = None
                return
