"""Stage 4 runtime integration for the passage-semantic companion store.

This module builds structural passages exclusively from current editable
chapter JSON. Preserved imported USFM supplies markers and ordering hints only;
its Scripture wording is never copied into a semantic passage or token stream.
It does not perform semantic matching, QA audits, correction generation, or
translationCore projection.
"""
from __future__ import annotations

from bisect import bisect_left
from collections import Counter, OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import threading
import time
import unicodedata
from typing import Any, Iterable
import uuid

import regex

from .original_language_resources import resource_for_book
from .passage_semantic_models import (
    CharacterSpan,
    LifecycleStatus,
    PassageRecord,
    PassageStructureKind,
    PassageStructureMarker,
    PolicyBinding,
    SemanticUnitProvenance,
    TokenInstance,
    TokenKind,
    TokenLayer,
    TokenLineage,
    TokenSide,
)
from .passage_semantic_repository import (
    DATABASE_SCHEMA_VERSION,
    FoundationConflict,
    FoundationRepository,
    FoundationValidationError,
)
from .qa_target_hash import canonical_text_hash
from .unicode_coordinates import grapheme_boundaries
from .usfm_parser import UsfmParseError, decode_usfm_text, parse_usfm
from .usfm_passages import PassageWindow, TargetSegment, UsfmPassageIndex
from .usfm_verse import marker_names
from . import versification
from .source_semantic_inventory import SourceSemanticInventory
from .target_semantic_inventory import TargetSemanticInventory
from .semantic_location import SemanticLocationEngine
from .meaning_analysis import MeaningAnalysisEngine
from .qa_audit import QaAuditEngine
from .correction_eligibility import CorrectionEligibilityService
from .correction_wording import CorrectionWordingService
from .correction_application_recovery import CorrectionApplicationRecoveryCoordinator
from .qa_review import QaReviewService


RUNTIME_VERSION = "stage4-runtime-v1"
DEFAULT_TOKENIZER = "bridge-unicode-word-v1"
TC_COMPATIBILITY_TOKENIZER = "tc-whitespace-v1"
NORMALIZATION_PROFILE = "NFC-v1"

# Reference shapes, not USFM: USFM itself is read through usfm_parser (#91).
_BRIDGE = re.compile(r"^(\d+)[-–](\d+)$")
_LETTERED = re.compile(r"^\d+[A-Za-z]+$")

_PARAGRAPH = {"p", "m", "b", "pi", "pi1", "pi2", "mi", "nb", "pc", "pr"}
_POETRY = {"q", "q1", "q2", "q3", "q4", "qr", "qc", "qm", "qm1", "qm2"}
_HEADING = {
    "h", "h1", "h2", "h3", "toc1", "toc2", "toc3",
    "s", "s1", "s2", "s3", "s4", "ms", "ms1", "ms2", "mr", "r", "sr", "qa",
}
_NOTE = {"f", "fe", "ef"}
_XREF = {"x", "ex"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_raw_import_stub(group: dict[str, Any]) -> bool:
    """Is this the placeholder a raw import writes for an unaligned source word?

    `original_language_resources.blank_source_alignments` writes exactly
    ``{"topWords": [<one token>], "bottomWords": []}`` per source word, and
    nothing else. That is a book nobody has aligned yet -- an ordinary state, not
    a legacy record whose meaning cannot be recovered (#99).

    Deliberately exact rather than "any empty bottomWords": a real
    translationCore group that lost its target side, or one carrying extra keys
    we do not understand, is still something a human should see, so it keeps
    being quarantined. Matching loosely here would silently swallow those.
    """
    if set(group) != {"topWords", "bottomWords"}:
        return False
    if group.get("bottomWords") != []:
        return False
    top = group.get("topWords")
    return isinstance(top, list) and len(top) == 1 and isinstance(top[0], dict)


def _sha256_text(value: str) -> str:
    # One implementation, shared with Stage 8 persistence and Stage 9B
    # eligibility, so the same target verse string cannot hash differently
    # depending on which side of the contract looked at it.
    return canonical_text_hash(value)


def _json_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _sha256_text(payload)


def _decode_usfm(raw: bytes, path: Path) -> str:
    """The preserved source as text, through the one USFM decoder (#91), with
    this module's own requirement kept: a file with no `\\c` or no `\\v` is
    not a structural skeleton, and the caller falls back to current text only."""
    try:
        value = decode_usfm_text(raw, path.name)
    except UsfmParseError as exc:
        raise UnicodeError(f"Cannot decode structural USFM: {path}: {exc}") from exc
    if "\\c" not in value or "\\v" not in value:
        raise UnicodeError(f"Cannot decode structural USFM: {path}")
    return value


# --- the overlay's two caches (#91 Phase 3a) ---------------------------------
#
# rebuild_current_passage runs in loops -- affected re-analysis, word-alignment
# evidence, Stage 6B once per range -- and every call rebuilt the overlay from
# scratch: a pass over the preserved source, then two whole-book parses of
# synthetic USFM. Nothing in those inputs changes between calls except when a
# verse is edited (the chapter JSON) or, rarely, the preserved file itself.
#
# Level 1 keys the source *skeleton* -- what the preserved file contributes:
# chapter, verse and marker events in document order, no Scripture -- on the
# file's SHA-256, which the overlay already computed for `structure_hash`.
# Level 2 keys the finished overlay on (path, source hash, hash of the current
# chapter JSON). A verse edit misses level 2 and hits level 1. A guard failure
# raises before anything is stored, so a bad build is never served again.

_METADATA_MARKERS = frozenset({"id", "ide", "h", "h1", "h2", "h3", "toc1", "toc2", "toc3"})
_CACHE_LIMIT = 8
_CACHE_LOCK = threading.Lock()
_SKELETON_CACHE: "OrderedDict[str, tuple[_SkeletonEvent, ...]]" = OrderedDict()
_OVERLAY_CACHE: "OrderedDict[tuple[str, str, str], CurrentTextOverlay]" = OrderedDict()


@dataclass(frozen=True)
class _SkeletonEvent:
    """One line of the preserved source that carries structure.

    ``kind`` is "c" (chapter; value = number), "v" (verse; value = the
    structural verse as written) or "m" (any other marker line; value = the
    marker, lower-cased). ``inline`` are the character markers found on the
    rest of that line, in order. ``keep`` says whether a marker line survives
    into the synthetic text (metadata markers do not).
    """
    kind: str
    value: str
    inline: tuple[str, ...] = ()
    keep: bool = True


def _source_skeleton(source: str) -> tuple[_SkeletonEvent, ...]:
    """The preserved file's structure, with every body discarded. A pure
    function of the source text, which is why it can be cached by its hash.

    Since #91 Phase 3c the structure is the parser's: the pre-chapter header
    lines (`\\id`, `\\usfm`, `\\h`, `\\toc1`, `\\mt` …) come from
    `ParsedUsfm.headers`, and every chapter, verse and paragraph marker of the
    body from `ParsedUsfm.structure`, in document order. The inline markers
    recorded for a verse or paragraph line are those on that line after its
    own marker, up to the next structural element -- so a mid-line
    `\\q1 \\v 2 …` is a paragraph event followed by a verse event, where the
    line regex this replaced saw a paragraph whose body happened to contain a
    `\\v`. A `\\c` that does not start its line is a chapter now, too.
    """
    parsed = parse_usfm(source)
    events: list[_SkeletonEvent] = []
    for header in parsed.headers:
        tag = header.tag.lower()
        events.append(_SkeletonEvent(
            "m", tag, marker_names(header.content), keep=tag not in _METADATA_MARKERS,
        ))
    items = parsed.structure
    for index, item in enumerate(items):
        line_end = source.find("\n", item.offset)
        line_end = len(source) if line_end < 0 else line_end
        if index + 1 < len(items):
            line_end = min(line_end, items[index + 1].offset)
        inline = marker_names(source[item.offset:line_end])[1:]  # [0] is this marker itself
        if item.kind == "chapter":
            events.append(_SkeletonEvent("c", item.chapter))
        elif item.kind == "verse":
            events.append(_SkeletonEvent("v", item.verse, inline))
        else:
            events.append(_SkeletonEvent("m", item.marker, inline, keep=item.marker not in _METADATA_MARKERS))
    return tuple(events)


def _lru_get(cache: "OrderedDict", key: Any) -> Any:
    with _CACHE_LOCK:
        if key in cache:
            cache.move_to_end(key)
            return cache[key]
    return None


def _lru_put(cache: "OrderedDict", key: Any, value: Any) -> None:
    with _CACHE_LOCK:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > _CACHE_LIMIT:
            cache.popitem(last=False)


def _skeleton_for(source_hash: str, source: str) -> tuple[_SkeletonEvent, ...]:
    cached = _lru_get(_SKELETON_CACHE, source_hash)
    if cached is None:
        cached = _source_skeleton(source)
        _lru_put(_SKELETON_CACHE, source_hash, cached)
    return cached


def clear_overlay_caches() -> None:
    """Drop both caches. Tests use it; production never needs to, because every
    key already changes when its inputs do."""
    with _CACHE_LOCK:
        _SKELETON_CACHE.clear()
        _OVERLAY_CACHE.clear()


def _verse_key(value: str) -> tuple[int, int, str]:
    raw = str(value).replace("–", "-")
    if raw.isdigit():
        return (int(raw), int(raw), "")
    match = _BRIDGE.fullmatch(raw)
    if match:
        return (int(match.group(1)), int(match.group(2)), "")
    match = re.fullmatch(r"(\d+)([A-Za-z]+)", raw)
    if match:
        return (int(match.group(1)), int(match.group(1)), match.group(2).lower())
    return (10**9, 10**9, raw)


def _intersects(left: str, right: str) -> bool:
    l1, l2, _ = _verse_key(left)
    r1, r2, _ = _verse_key(right)
    return l1 != 10**9 and r1 != 10**9 and max(l1, r1) <= min(l2, r2)


def _structure_kind(marker: str) -> PassageStructureKind:
    value = marker.lower()
    if value == "c":
        return PassageStructureKind.CHAPTER
    if value == "v":
        return PassageStructureKind.VERSE
    if value in _PARAGRAPH:
        return PassageStructureKind.PARAGRAPH
    if value in _POETRY:
        return PassageStructureKind.POETRY
    if value in _HEADING:
        return PassageStructureKind.HEADING
    if value in _NOTE:
        return PassageStructureKind.NOTE
    if value in _XREF:
        return PassageStructureKind.CROSS_REFERENCE
    return PassageStructureKind.INLINE_MARKUP


def current_target_text(project: Any) -> dict[str, str]:
    """Return current editable Scripture only, keyed by full displayed ref."""
    book = str(project.book_id).upper()
    result: dict[str, str] = {}
    book_dir = getattr(project, "book_dir", None)
    if book_dir is not None and Path(book_dir).is_dir():
        chapter_paths = sorted(
            (path for path in Path(book_dir).glob("*.json") if path.stem.isdigit()),
            key=lambda path: int(path.stem),
        )
        for path in chapter_paths:
            try:
                chapter_data = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError) as exc:
                raise FoundationValidationError(
                    f"Cannot read authoritative current target chapter: {path}: {exc}"
                ) from exc
            if not isinstance(chapter_data, dict):
                raise FoundationValidationError(
                    f"Authoritative current target chapter is not an object: {path}"
                )
            for verse, text in chapter_data.items():
                if str(verse) != "front":
                    result[f"{book} {path.stem}:{verse}"] = str(text)
        return result
    # Compatibility for project-like test/read adapters without a book_dir.
    for chapter in project.chapters():
        for verse in project.verses(chapter):
            if str(verse) != "front":
                result[f"{book} {chapter}:{verse}"] = str(
                    project.target_verse_text(chapter, verse)
                )
    return result


@dataclass(frozen=True)
class CurrentTextOverlay:
    index: UsfmPassageIndex
    structure_markers: tuple[PassageStructureMarker, ...]
    mismatches: tuple[dict[str, Any], ...]
    structure_hash: str
    structure_resource_id: str


def _authoritative_current_segments(
    book: str, by_chapter: dict[str, dict[str, str]],
) -> dict[str, str]:
    r"""Parsed form of the current chapter JSON, via the same USFM parser.

    Chapter JSON legitimately stores trailing paragraph/section markers inside
    the verse string (``...text\n\p``, ``...text\n\s heading\n\p``) -- real
    imported projects are full of them. The parser correctly hoists those out
    of verse text, so comparing a parsed segment against the raw stored string
    reports perfectly good data as non-authoritative: 39 of 104 segments in a
    real Hindi Philippians failed that way, which blocked scope resolution and
    therefore Stage 9A.4 analysis for the whole book.

    Both sides are parsed identically instead. This is the same construction
    the no-preserved-USFM fallback above already treats as authoritative, so
    the guard still catches what it exists for: any source-USFM Scripture body
    leaking into a verse still parses differently from the current text.
    """
    lines = [f"\\id {book}"]
    for chapter in sorted(by_chapter, key=lambda item: _verse_key(item)[0]):
        lines.append(f"\\c {chapter}")
        for verse in sorted(by_chapter[chapter], key=_verse_key):
            lines.append(f"\\v {verse} {by_chapter[chapter][verse]}")
    index = UsfmPassageIndex.from_text("\n".join(lines) + "\n", book_hint=book)
    return {segment.reference: segment.text for segment in index.segments}


def build_current_text_overlay(project: Any) -> CurrentTextOverlay:
    """Overlay current chapter JSON onto a marker-only imported-USFM skeleton.

    No source-USFM Scripture body is ever appended to ``synthetic``. A mismatch
    records STRUCTURE_TEXT_MISMATCH and current verses are appended safely.
    """
    book = str(project.book_id).upper()
    by_chapter: dict[str, dict[str, str]] = {}
    for reference, text in current_target_text(project).items():
        cv = reference.split(" ", 1)[1]
        chapter, verse = cv.split(":", 1)
        by_chapter.setdefault(chapter, {})[verse] = text

    path = project.usfm_path()
    if path is None or not Path(path).is_file():
        lines = [f"\\id {book}"]
        markers: list[PassageStructureMarker] = []
        order = 0
        for chapter in sorted(by_chapter, key=lambda item: _verse_key(item)[0]):
            lines.append(f"\\c {chapter}")
            markers.append(PassageStructureMarker(
                PassageStructureKind.CHAPTER, "c", f"{book} {chapter}:front",
                None, None, order,
            )); order += 1
            for verse in sorted(by_chapter[chapter], key=_verse_key):
                lines.append(f"\\v {verse} {by_chapter[chapter][verse]}")
                markers.append(PassageStructureMarker(
                    PassageStructureKind.VERSE_BRIDGE if _BRIDGE.fullmatch(verse) else PassageStructureKind.VERSE,
                    "v", f"{book} {chapter}:{verse}", None, None, order,
                )); order += 1
        synthetic = "\n".join(lines) + "\n"
        return CurrentTextOverlay(
            UsfmPassageIndex.from_text(synthetic, book_hint=book), tuple(markers), (),
            _sha256_text("NO_PRESERVED_USFM"), "current-chapter-json-only",
        )

    source_path = Path(path)
    try:
        source_bytes = source_path.read_bytes()
        source = _decode_usfm(source_bytes, source_path)
    except (OSError, UnicodeError) as exc:
        # Safe fallback still uses only current text.
        class NoUsfmProject:
            book_id = project.book_id

            @staticmethod
            def chapters() -> list[str]:
                return project.chapters()

            @staticmethod
            def verses(chapter_value: str) -> list[str]:
                return project.verses(chapter_value)

            @staticmethod
            def target_verse_text(chapter_value: str, verse_value: str) -> str:
                return project.target_verse_text(chapter_value, verse_value)

            @staticmethod
            def usfm_path() -> None:
                return None

        fallback = NoUsfmProject()
        overlay = build_current_text_overlay(fallback)
        mismatch = {"code": "STRUCTURE_TEXT_MISMATCH", "detail": str(exc)}
        return CurrentTextOverlay(
            overlay.index, overlay.structure_markers, (mismatch,),
            _sha256_text("UNREADABLE_PRESERVED_USFM"), str(source_path),
        )

    source_hash = hashlib.sha256(source_bytes).hexdigest()
    overlay_key = (str(source_path), source_hash, _json_hash(by_chapter))
    cached_overlay = _lru_get(_OVERLAY_CACHE, overlay_key)
    if cached_overlay is not None:
        return cached_overlay
    events = _skeleton_for(source_hash, source)

    synthetic: list[str] = [f"\\id {book}"]
    markers: list[PassageStructureMarker] = []
    mismatches: list[dict[str, Any]] = []
    seen: dict[str, set[str]] = {chapter: set() for chapter in by_chapter}
    pending_marker_indexes: list[int] = []
    chapter = "0"
    order = 0

    def assign_pending(reference: str) -> None:
        nonlocal markers
        for index in pending_marker_indexes:
            marker = markers[index]
            markers[index] = PassageStructureMarker(
                marker.kind, marker.marker, reference,
                marker.start_code_point, marker.end_code_point, marker.source_order,
            )
        pending_marker_indexes.clear()

    def append_current(verse: str) -> None:
        nonlocal order
        if verse in seen.setdefault(chapter, set()):
            return
        text = by_chapter.get(chapter, {}).get(verse)
        if text is None:
            return
        reference = f"{book} {chapter}:{verse}"
        assign_pending(reference)
        synthetic.append(f"\\v {verse} {text}")
        markers.append(PassageStructureMarker(
            PassageStructureKind.VERSE_BRIDGE if _BRIDGE.fullmatch(verse) else PassageStructureKind.VERSE,
            "v", reference, None, None, order,
        )); order += 1
        seen[chapter].add(verse)

    def flush_unseen(active_chapter: str) -> None:
        for verse in sorted(by_chapter.get(active_chapter, {}), key=_verse_key):
            if verse not in seen.setdefault(active_chapter, set()):
                mismatches.append({
                    "code": "STRUCTURE_TEXT_MISMATCH",
                    "reference": f"{book} {active_chapter}:{verse}",
                    "detail": "Current target verse has no safely matching imported structural anchor.",
                })
                append_current(verse)

    for event in events:
        if event.kind == "c":
            if chapter != "0":
                flush_unseen(chapter)
            chapter = event.value
            synthetic.append(f"\\c {chapter}")
            markers.append(PassageStructureMarker(
                PassageStructureKind.CHAPTER, "c", f"{book} {chapter}:front",
                None, None, order,
            )); order += 1
            continue
        if event.kind == "v":
            structural_verse = event.value
            current_keys = list(by_chapter.get(chapter, {}))
            matches = [key for key in current_keys if key == structural_verse]
            if not matches:
                matches = [
                    key for key in current_keys
                    if key not in seen.setdefault(chapter, set()) and _intersects(key, structural_verse)
                ]
            if not matches:
                mismatches.append({
                    "code": "STRUCTURE_TEXT_MISMATCH",
                    "reference": f"{book} {chapter}:{structural_verse}",
                    "detail": "Imported structural verse has no current editable target text.",
                })
            for current_verse in sorted(matches, key=_verse_key):
                append_current(current_verse)
            if (
                _BRIDGE.fullmatch(structural_verse.replace("–", "-"))
                and matches and structural_verse not in matches
            ):
                markers.append(PassageStructureMarker(
                    PassageStructureKind.VERSE_BRIDGE, "v",
                    f"{book} {chapter}:{matches[0]}", None, None, order,
                )); order += 1
            for inline in event.inline:
                markers.append(PassageStructureMarker(
                    _structure_kind(inline), inline.lower(),
                    f"{book} {chapter}:{matches[0]}" if matches else None,
                    None, None, order,
                )); order += 1
            continue
        if event.kind == "m":
            marker = event.value
            # Metadata/id text and every marker body are discarded. Only the
            # marker itself survives as structure.
            if event.keep:
                synthetic.append(f"\\{marker}")
            markers.append(PassageStructureMarker(
                _structure_kind(marker), marker, None, None, None, order,
            )); pending_marker_indexes.append(len(markers) - 1); order += 1
            for inline in event.inline:
                markers.append(PassageStructureMarker(
                    _structure_kind(inline), inline.lower(), None, None, None, order,
                )); pending_marker_indexes.append(len(markers) - 1); order += 1

    if chapter != "0":
        flush_unseen(chapter)
    for remaining in sorted(set(by_chapter) - set(seen), key=lambda item: _verse_key(item)[0]):
        chapter = remaining
        synthetic.append(f"\\c {chapter}")
        markers.append(PassageStructureMarker(
            PassageStructureKind.CHAPTER, "c", f"{book} {chapter}:front",
            None, None, order,
        )); order += 1
        flush_unseen(chapter)

    text = "\n".join(synthetic) + "\n"
    index = UsfmPassageIndex.from_text(text, book_hint=book)
    authoritative = _authoritative_current_segments(book, by_chapter)
    for segment in index.segments:
        if authoritative.get(segment.reference) != segment.text:
            raise FoundationValidationError(
                f"Current-text overlay produced non-authoritative text at {segment.reference}"
            )
    overlay = CurrentTextOverlay(
        index, tuple(markers), tuple(mismatches), source_hash, str(source_path),
    )
    _lru_put(_OVERLAY_CACHE, overlay_key, overlay)
    return overlay


def project_current_passage_index(project: Any) -> UsfmPassageIndex:
    return build_current_text_overlay(project).index


def _canonical_reference(
    book: str, chapter: str, verse: str, project_schema: str,
) -> dict[str, Any]:
    displayed = f"{book} {chapter}:{verse}"
    if _LETTERED.fullmatch(verse):
        return {
            "displayedReference": displayed, "projectVersification": project_schema,
            "canonicalReferences": [displayed], "mappingKind": "AMBIGUOUS_SEGMENT",
        }
    bridge = _BRIDGE.fullmatch(verse.replace("–", "-"))
    if bridge:
        refs: list[str] = []
        for number in range(int(bridge.group(1)), int(bridge.group(2)) + 1):
            mapped = _canonical_reference(book, chapter, str(number), project_schema)
            refs.extend(mapped["canonicalReferences"])
        return {
            "displayedReference": displayed, "projectVersification": project_schema,
            "canonicalReferences": list(dict.fromkeys(refs)), "mappingKind": "VERSE_BRIDGE",
        }
    try:
        mapped = versification.to_org_ref(book, chapter, verse, project_schema)
    except Exception:
        return {
            "displayedReference": displayed, "projectVersification": project_schema,
            "canonicalReferences": [displayed], "mappingKind": "SAME",
        }
    kind = str(mapped.get("mapping") or "same").upper()
    raw_refs: list[str]
    if kind == "SPLIT":
        raw_refs = [str(item) for item in mapped.get("splitInto") or []]
    else:
        raw_refs = [str(mapped.get("orgRef") or displayed)]
    mapping_kind = kind if kind in {"SAME", "MAPPED", "MERGE", "SPLIT"} else "MAPPED"
    if mapping_kind == "MAPPED" and any(
        ref.split(" ", 1)[-1].split(":", 1)[0] != str(chapter) for ref in raw_refs if ":" in ref
    ):
        mapping_kind = "CHAPTER_SHIFT"
    if book == "PSA" and str(verse) in {"0", "1"} and mapping_kind != "SAME":
        mapping_kind = "PSALM_TITLE"
    return {
        "displayedReference": displayed, "projectVersification": project_schema,
        "canonicalReferences": raw_refs, "mappingKind": mapping_kind,
    }


# The compatibility scan's per-file memo rows in migration_runs (one per
# chapter file and content). The folder-level row keeps its own schema,
# "translationCore.alignmentData.compatibility-scan.v1", and its report.
ALIGNMENT_FILE_SCAN_SCHEMA = "translationCore.alignmentData.compatibility-scan.file.v1"


def alignment_files(project: Any) -> list[tuple[Path, bytes]]:
    """Every tC alignmentData chapter file of the book, in name order, with its
    bytes: read once and shared by the digests and the compatibility scan."""
    return [(path, path.read_bytes()) for path in sorted(project.alignment_dir.glob("*.json"))]


def alignment_file_digest(path: Path, data: bytes) -> str:
    """One chapter file's digest (its name and bytes): the compatibility scan's
    per-file memo key, so an edit re-scans only the chapter it changed."""
    return hashlib.sha256(path.name.encode("utf-8") + data).hexdigest()


def alignment_directory_digest(project: Any, files: list[tuple[Path, bytes]] | None = None) -> str:
    """Content digest over every tC alignmentData chapter file for this book.

    Pulled out of `_scan_native_alignment_compatibility` so Stage 6B's word
    alignment evidence (word_alignment_evidence.py) can put the exact same
    digest into its run fingerprint without a second directory walk -- any
    alignment change anywhere in the book changes this, which is deliberately
    coarser than per-verse but matches the compatibility scan's own
    memoization key exactly. `files` (from `alignment_files`) saves a second
    read when the caller has them already; the digest is the same either way.
    """
    digest_builder = hashlib.sha256()
    for path, data in (alignment_files(project) if files is None else files):
        digest_builder.update(path.name.encode("utf-8"))
        digest_builder.update(data)
    return digest_builder.hexdigest()


def alignment_state_digest(project: Any, *, content_digest: str | None = None) -> str:
    """Content digest over alignment content AND completion/invalid markers.

    Distinct from `alignment_directory_digest` (content only): completing or
    invalidating an alignment (tools/wordAlignment/completed|invalid/<ch>/
    <v>.json) changes what Stage 6B word-alignment evidence should find
    without necessarily changing a single alignmentData byte -- Bridge
    auto-completes the moment every word is grouped, often in the same
    mutation that changed the content, but `complete_alignment()` can also
    flip completion state on its own. Whatever decides to stale downstream
    Stage 6B/7/8 records must be sensitive to completion state too.
    """
    digest_builder = hashlib.sha256()
    digest_builder.update((content_digest or alignment_directory_digest(project)).encode("utf-8"))
    tools_dir = project.tc_dir / "tools" / "wordAlignment"
    for path in sorted(tools_dir.glob("*/*/*.json")):
        digest_builder.update(str(path.relative_to(tools_dir)).encode("utf-8"))
        digest_builder.update(path.read_bytes())
    # #119: the Bridge-private cross-verse links are alignment state too. A
    # link change must move this digest for exactly the reason a completion
    # marker does: it changes what Stage 6B's WORD_ALIGNMENT evidence finds.
    cross_verse = getattr(project, "cross_verse_links", None)
    if cross_verse is not None:
        digest_builder.update("␟crossVerse␟".encode("utf-8"))
        digest_builder.update(cross_verse.digest().encode("utf-8"))
    return digest_builder.hexdigest()


def target_token_identity(
    project_id: str, book: str, displayed_reference: str, text_revision: str,
    profile: str, token: dict[str, Any],
) -> tuple[str, str, str]:
    """Mint the same (lineage_id, instance_id, identity) `_ensure_target_tokens` would.

    Pulled out so a resolver matching a translationCore `bottomWord` back onto
    a freshly retokenized current verse (word_alignment_evidence.py) computes
    the identity that token is already stored under, rather than re-deriving
    the hash formula in a second place.
    """
    identity = "␟".join((
        project_id, book, displayed_reference, text_revision,
        profile, str(token["index"]), token["raw"],
    ))
    lineage_id = "target-lineage-" + _sha256_text("lineage␟" + identity)[:32]
    instance_id = "target-token-" + _sha256_text("instance␟" + identity)[:32]
    return lineage_id, instance_id, identity


def tokenize_target_text(text: str, profile: str = DEFAULT_TOKENIZER) -> list[dict[str, Any]]:
    if profile == TC_COMPATIBILITY_TOKENIZER:
        matches = list(regex.finditer(r"\S+", text))
    elif profile == DEFAULT_TOKENIZER:
        matches = list(regex.finditer(
            r"\p{L}[\p{L}\p{M}\p{N}\p{Pc}\p{Pd}'’]*|\p{N}+|[^\p{Z}\p{C}\p{L}\p{M}\p{N}]",
            text,
        ))
    else:
        raise FoundationValidationError(f"Unknown companion tokenizer profile: {profile}")
    normalized = [unicodedata.normalize("NFC", match.group(0)) for match in matches]
    totals = Counter(normalized)
    seen: Counter[str] = Counter()
    boundaries = grapheme_boundaries(text)
    result: list[dict[str, Any]] = []
    for index, (match, norm) in enumerate(zip(matches, normalized)):
        seen[norm] += 1
        start, end = match.span()
        start_grapheme = bisect_left(boundaries, start)
        end_grapheme = bisect_left(boundaries, end)
        kind = TokenKind.WORD
        if regex.fullmatch(r"[^\p{L}\p{M}\p{N}]+", match.group(0)):
            kind = TokenKind.PUNCTUATION
        result.append({
            "index": index, "raw": match.group(0), "normalized": norm,
            "occurrence": seen[norm], "occurrences": totals[norm], "kind": kind,
            "start": start, "end": end,
            "startGrapheme": start_grapheme, "endGrapheme": end_grapheme,
        })
    return result


class PassageSemanticRuntime:
    def __init__(self, project: Any, project_id: str):
        self.project = project
        self.project_id = project_id
        self.book = str(project.book_id).upper()
        self.path = project.companion_dir() / "passageSemantic" / "bridge-semantic.sqlite3"
        # Wall-clock seconds per constructor phase, in order, for the
        # `[trace] project.open` line bridge_service writes to stderr. Opening
        # a freshly imported book has taken minutes with nothing to say where.
        self.init_timings: list[tuple[str, float]] = []
        last = time.perf_counter()

        def mark(phase: str) -> None:
            nonlocal last
            now = time.perf_counter()
            self.init_timings.append((phase, now - last))
            last = now

        self.repository = FoundationRepository(self.path)
        mark("repository")
        self.last_error = ""
        self.replayed_invalidations = 0
        self._versification_schema = ""
        self._bind_project()
        mark("bind")
        self.replayed_invalidations = self.replay_pending_invalidations()
        mark("replay_invalidations")
        self.synchronize_current_text()
        mark("current_text")
        self._synchronize_source_lock()
        mark("source_lock")
        self.source_semantic = SourceSemanticInventory(self)
        self.target_semantic = TargetSemanticInventory(self)
        self.semantic_location = SemanticLocationEngine(self)
        self.meaning_analysis = MeaningAnalysisEngine(self)
        self.qa_audit = QaAuditEngine(self)
        self.qa_review = QaReviewService(self)
        self.correction_eligibility = CorrectionEligibilityService(self)
        self.correction_wording = CorrectionWordingService(self)
        self.correction_application_recovery = CorrectionApplicationRecoveryCoordinator(self)
        mark("engines")
        self.application_recovery = self.correction_application_recovery.reconcile_incomplete()
        mark("reconcile_applications")
        # `_migrate_legacy_companions()` used to run here. It imported
        # `semanticMappings/` and `semanticValidation/` companion files as
        # AI_RATIONALE evidence, and #76 moved both stores into the workbench
        # database. Deleted rather than repointed: it de-duplicated on file
        # content, so every human decision changed the digest and imported a
        # *new* evidence record, for a kind nothing reads. See the commit that
        # removed it. What remains is the alignment scan, which is a real
        # content-addressed compatibility check and always was.
        self.synchronize_alignment_state()
        mark("alignment_scan")

    def init_timing_summary(self) -> str:
        return " ".join(f"{phase}={seconds:.2f}s" for phase, seconds in self.init_timings)

    def _identity_fingerprint(self) -> str:
        manifest = self.project.manifest
        target = manifest.get("target_language") if isinstance(manifest.get("target_language"), dict) else {}
        resource = manifest.get("resource") if isinstance(manifest.get("resource"), dict) else {}
        payload = {
            "projectId": self.project_id, "book": self.book,
            "targetLanguageId": str(target.get("id") or ""),
            "resourceId": str(resource.get("id") or ""),
        }
        return _json_hash(payload)

    def _bind_project(self) -> None:
        manifest = self.project.manifest
        target = manifest.get("target_language") if isinstance(manifest.get("target_language"), dict) else {}
        resource = manifest.get("resource") if isinstance(manifest.get("resource"), dict) else {}
        self.repository.bind_project_metadata(
            project_id=self.project_id, identity_fingerprint=self._identity_fingerprint(),
            book=self.book, target_language_id=str(target.get("id") or ""),
            resource_id=str(resource.get("id") or ""), path=str(self.project.path),
        )

    @staticmethod
    def reference(chapter: str | int, verse: str | int, book: str) -> str:
        return f"{book.upper()} {chapter}:{verse}"

    @staticmethod
    def text_hash(text: str) -> str:
        return _sha256_text(text)

    def text_revision(self, reference: str, text_hash: str) -> str:
        return _sha256_text("\u241f".join((self.project_id, self.book, reference, text_hash, RUNTIME_VERSION)))

    def synchronize_current_text(self) -> dict[str, int]:
        changed = 0
        established = 0
        current_text = current_target_text(self.project)
        # One read of the book's revisions and one write for everything new,
        # instead of a connection per verse to read and a commit per verse to
        # establish: on a first open that was 1,533 commits for Genesis and
        # 20.8 s of a 22 s project.open (the 30 s timeout is the ceiling).
        # A verse whose text changed still goes through the per-reference
        # prepare/apply intent below -- that pair is the crash-safe path and
        # is rare on open (an edit made outside Bridge).
        existing_rows = self.repository.current_target_revisions(self.project_id, self.book)
        existing_by_reference = {str(row["displayedReference"]): row for row in existing_rows}
        to_establish: list[tuple[str, str, str]] = []
        for reference, text in current_text.items():
            actual_hash = self.text_hash(text)
            existing = existing_by_reference.get(reference)
            revision = self.text_revision(reference, actual_hash)
            if existing is None:
                to_establish.append((reference, actual_hash, revision))
                established += 1
            elif existing["textHash"] != actual_hash:
                intent = self.repository.prepare_target_invalidation(
                    project_id=self.project_id, book=self.book, displayed_reference=reference,
                    previous_text_hash=existing["textHash"], expected_text_hash=actual_hash,
                )
                self.repository.apply_target_invalidation(
                    intent, actual_text_hash=actual_hash, text_revision=revision,
                )
                changed += 1
        self.repository.establish_target_revisions_bulk(
            project_id=self.project_id, book=self.book, revisions=to_establish,
        )
        # External project changes can remove a reference without passing
        # through apply_scripture_edit(). Represent deletion as a tombstone
        # revision so its dependents cannot remain current or be served.
        # `existing_rows` is the pre-loop snapshot: every row the loop above
        # touched has a reference in `current_text` and is skipped here, so
        # re-reading would see the same candidates.
        empty_hash = self.text_hash("")
        for existing in existing_rows:
            reference = str(existing["displayedReference"])
            if reference in current_text or existing["textHash"] == empty_hash:
                continue
            intent = self.repository.prepare_target_invalidation(
                project_id=self.project_id, book=self.book, displayed_reference=reference,
                previous_text_hash=existing["textHash"], expected_text_hash=empty_hash,
            )
            self.repository.apply_target_invalidation(
                intent, actual_text_hash=empty_hash,
                text_revision=self.text_revision(reference, empty_hash),
            )
            changed += 1
        return {"established": established, "changed": changed}

    def prepare_target_edit(self, chapter: str, verse: str, old_text: str, new_text: str) -> str:
        reference = self.reference(chapter, verse, self.book)
        existing = self.repository.current_target_revision(self.project_id, self.book, reference)
        return self.repository.prepare_target_invalidation(
            project_id=self.project_id, book=self.book, displayed_reference=reference,
            previous_text_hash=str(existing.get("textHash") if existing else self.text_hash(old_text)),
            expected_text_hash=self.text_hash(new_text),
        )

    def complete_target_edit(self, intent_id: str, chapter: str, verse: str) -> dict[str, Any]:
        reference = self.reference(chapter, verse, self.book)
        current = str(self.project.target_verse_text(chapter, verse))
        actual_hash = self.text_hash(current)
        return self.repository.apply_target_invalidation(
            intent_id, actual_text_hash=actual_hash,
            text_revision=self.text_revision(reference, actual_hash),
        )

    def cancel_target_edit(self, intent_id: str, reason: str) -> None:
        self.repository.cancel_target_invalidation(intent_id, reason)

    def replay_pending_invalidations(self) -> int:
        replayed = 0
        current = current_target_text(self.project)
        for intent in self.repository.pending_invalidations(self.project_id):
            reference = str(intent["displayed_reference"])
            text = current.get(reference)
            if text is None:
                empty_hash = self.text_hash("")
                if intent["expected_text_hash"] == empty_hash:
                    self.repository.apply_target_invalidation(
                        intent["id"], actual_text_hash=empty_hash,
                        text_revision=self.text_revision(reference, empty_hash),
                    )
                    replayed += 1
                else:
                    self.repository.cancel_target_invalidation(
                        intent["id"], "Target reference no longer exists",
                    )
                continue
            actual_hash = self.text_hash(text)
            if actual_hash == intent["expected_text_hash"]:
                self.repository.apply_target_invalidation(
                    intent["id"], actual_text_hash=actual_hash,
                    text_revision=self.text_revision(reference, actual_hash),
                )
                replayed += 1
            elif actual_hash == intent["previous_text_hash"]:
                self.repository.cancel_target_invalidation(intent["id"], "Scripture edit was not committed")
            else:
                self.repository.cancel_target_invalidation(intent["id"], "Superseded by a different target edit")
                fresh = self.repository.current_target_revision(self.project_id, self.book, reference)
                new_intent = self.repository.prepare_target_invalidation(
                    project_id=self.project_id, book=self.book, displayed_reference=reference,
                    previous_text_hash=str(fresh.get("textHash") if fresh else intent["previous_text_hash"]),
                    expected_text_hash=actual_hash,
                )
                self.repository.apply_target_invalidation(
                    new_intent, actual_text_hash=actual_hash,
                    text_revision=self.text_revision(reference, actual_hash),
                )
                replayed += 1
        return replayed

    def _project_versification(self) -> str:
        if self._versification_schema:
            return self._versification_schema
        try:
            verses = {
                reference.split(" ", 1)[1]: text
                for reference, text in current_target_text(self.project).items()
            }
            self._versification_schema = str(
                versification.detect_schema(self.book, verses).get("bestSchema") or "org"
            )
        except Exception:
            self._versification_schema = "org"
        return self._versification_schema

    def _synchronize_source_lock(self) -> dict[str, Any]:
        resource = resource_for_book(self.book)
        if resource is not None:
            return self.repository.synchronize_source_lock(
                project_id=self.project_id, book=self.book,
                resource_id=resource.resource_id, resource_version=resource.version,
                resource_hash=resource.provenance_sha256,
            )
        manifest_resource = self.project.manifest.get("bridge_original_language")
        value = manifest_resource if isinstance(manifest_resource, dict) else {}
        if not value:
            return {"available": False, "changed": False, "staled": 0}
        return self.repository.synchronize_source_lock(
            project_id=self.project_id, book=self.book,
            resource_id=str(value.get("resourceId") or "unknown"),
            resource_version=str(value.get("version") or "unknown"),
            resource_hash=str(value.get("provenanceSha256") or value.get("commit") or "unknown"),
        )

    def _segments_for_range(
        self, index: UsfmPassageIndex, chapter: str, verse: str,
        end_chapter: str = "", end_verse: str = "",
    ) -> list[TargetSegment]:
        start = index.segment_for_source_reference(chapter, verse)
        if start is None:
            raise FoundationValidationError(f"Current target reference is unavailable: {self.book} {chapter}:{verse}")
        if end_chapter and end_verse:
            end = index.segment_for_source_reference(end_chapter, end_verse)
            if end is None or end.ordinal < start.ordinal:
                raise FoundationValidationError("Invalid current passage range")
            return index.segments[start.ordinal:end.ordinal + 1]
        window = index.window_for_source_reference(chapter, verse)
        return list(window.segments if window is not None else (start,))

    def rebuild_current_passage(
        self, chapter: str, verse: str, end_chapter: str = "", end_verse: str = "",
        tokenizer_profile: str = DEFAULT_TOKENIZER,
    ) -> dict[str, Any]:
        self.synchronize_current_text()
        overlay = build_current_text_overlay(self.project)
        segments = self._segments_for_range(
            overlay.index, str(chapter), str(verse), str(end_chapter), str(end_verse),
        )
        project_schema = self._project_versification()
        references = [
            _canonical_reference(self.book, segment.chapter, segment.verse, project_schema)
            for segment in segments
        ]
        target_text = {segment.reference: segment.text for segment in segments}
        target_hash = self.repository.target_content_hash(target_text)
        passage_id = "passage-" + _sha256_text("\u241f".join((
            self.project_id, self.book, *target_text.keys(), target_hash, project_schema,
        )))[:32]
        canonical = tuple(dict.fromkeys(
            item for reference in references for item in reference["canonicalReferences"]
        ))
        resource = resource_for_book(self.book)
        source_id = resource.resource_id if resource else "unavailable"
        source_version = resource.version if resource else None
        source_hash = resource.provenance_sha256 if resource else "unavailable"
        selected_refs = set(target_text)
        selected_chapters = {
            reference.split(" ", 1)[1].split(":", 1)[0] for reference in selected_refs
        }
        selected_markers = tuple(marker for marker in overlay.structure_markers if (
            marker.displayed_reference in selected_refs
            or (
                marker.displayed_reference is not None
                and marker.displayed_reference.endswith(":front")
                and marker.displayed_reference.split(" ", 1)[1].split(":", 1)[0]
                in selected_chapters
            )
        ))
        passage = PassageRecord(
            id=passage_id, project_id=self.project_id, book=self.book,
            displayed_source_references=tuple(item["displayedReference"] for item in references),
            displayed_target_references=tuple(target_text), canonical_references=canonical,
            source_resource_id=source_id, source_resource_version=source_version,
            source_resource_hash=source_hash,
            target_revision=_sha256_text("\u241f".join(
                self.repository.current_target_revision(self.project_id, self.book, ref)["textRevision"]
                for ref in target_text
            )),
            target_content_hash=target_hash,
            structure_resource_id=overlay.structure_resource_id,
            structure_resource_version=None, structure_resource_hash=overlay.structure_hash,
            target_text_by_displayed_reference=target_text,
            structure_markers=selected_markers, policy_binding=PolicyBinding.foundation_v1(),
            lifecycle_status=LifecycleStatus.ACTIVE,
        )
        try:
            existing = self.repository.passage_record(passage_id)
        except FoundationValidationError:
            self.repository.save_passage_record(passage)
        else:
            if existing.get("targetContentHash") != target_hash:
                raise FoundationConflict(
                    "Content-addressed passage identity conflicts with stored target content"
                )
        self.repository.save_passage_references(passage_id, references)
        for reference in target_text:
            dependency_id = self.repository.target_dependency_id(self.project_id, self.book, reference)
            self.repository.add_record_dependency(
                "PASSAGE_RECORD", passage_id, "TARGET_REFERENCE", dependency_id,
            )
        self.repository.add_record_dependency(
            "PASSAGE_RECORD", passage_id, "SOURCE_RESOURCE",
            self.repository.source_dependency_id(self.project_id, self.book, source_hash),
        )
        token_ids = self._ensure_target_tokens(passage, references, tokenizer_profile)
        for mismatch in overlay.mismatches:
            self.repository.record_runtime_diagnostic(
                project_id=self.project_id, code="STRUCTURE_TEXT_MISMATCH",
                severity="WARNING", payload=mismatch,
            )
        return {
            **self.repository.passage_record(passage_id),
            "referenceMappings": self.repository.passage_references(passage_id),
            "targetTokenInstanceIds": token_ids,
            "structureStatus": "STRUCTURE_TEXT_MISMATCH" if overlay.mismatches else "CURRENT",
            "structureDiagnostics": list(overlay.mismatches),
            "tokenizerProfile": tokenizer_profile,
        }

    def get_current_passage(
        self, chapter: str, verse: str, end_chapter: str = "", end_verse: str = "",
    ) -> dict[str, Any]:
        # Rebuild is intentionally content-addressed. It validates current target
        # hashes on every read, so an interrupted invalidation cannot serve stale
        # passage content as current.
        return self.rebuild_current_passage(chapter, verse, end_chapter, end_verse)

    def _ensure_target_tokens(
        self, passage: PassageRecord, references: list[dict[str, Any]], profile: str,
    ) -> list[str]:
        reference_map = {item["displayedReference"]: item for item in references}
        result: list[str] = []
        for displayed_reference, text in passage.target_text_by_displayed_reference.items():
            current = self.repository.current_target_revision(
                self.project_id, self.book, displayed_reference,
            )
            if current is None:
                raise FoundationValidationError(f"Missing current revision for {displayed_reference}")
            text_revision = current["textRevision"]
            existing = self.repository.token_instances_for_reference(
                project_id=self.project_id, book=self.book,
                displayed_reference=displayed_reference, text_revision=text_revision,
            )
            if existing:
                result.extend(str(item["id"]) for item in existing)
                continue
            previous = self.repository.token_instances_for_reference(
                project_id=self.project_id, book=self.book,
                displayed_reference=displayed_reference,
            )
            previous = [item for item in previous if item.get("textRevision") != text_revision]
            lineages: list[TokenLineage] = []
            instances: list[TokenInstance] = []
            for token in tokenize_target_text(text, profile):
                lineage_id, instance_id, identity = target_token_identity(
                    self.project_id, self.book, displayed_reference, text_revision,
                    profile, token,
                )
                span = CharacterSpan(
                    start_code_point=token["start"], end_code_point=token["end"],
                    start_grapheme=token["startGrapheme"], end_grapheme=token["endGrapheme"],
                    quote=token["raw"], quote_sha256=_sha256_text(token["raw"]),
                )
                lineages.append(TokenLineage(
                    id=lineage_id, side=TokenSide.TARGET, project_id=self.project_id,
                    logical_resource_id=self.project_id, book=self.book,
                    canonical_reference_scope=tuple(reference_map[displayed_reference]["canonicalReferences"]),
                    token_layer=TokenLayer.ORTHOGRAPHIC, upstream_identity=None,
                    created_at=_now(), provenance=SemanticUnitProvenance.DETERMINISTIC_RULE,
                ))
                instances.append(TokenInstance(
                    id=instance_id, lineage_id=lineage_id, side=TokenSide.TARGET,
                    project_id=self.project_id, resource_id=self.project_id,
                    resource_version=None, resource_hash=current["textHash"],
                    text_revision=text_revision, book=self.book,
                    displayed_reference=displayed_reference,
                    canonical_references=tuple(reference_map[displayed_reference]["canonicalReferences"]),
                    index=token["index"], occurrence=token["occurrence"],
                    occurrences=token["occurrences"], span=span, raw_form=token["raw"],
                    normalized_form=token["normalized"], normalization_profile=NORMALIZATION_PROFILE,
                    tokenization_version=profile, token_layer=TokenLayer.ORTHOGRAPHIC,
                    token_kind=token["kind"], parent_instance_id=None,
                    instance_fingerprint=_sha256_text(identity),
                ))
                result.append(instance_id)
            created = self.repository.save_target_token_batch(lineages, instances)
            self._suggest_lineage_candidates(previous, created)
        return result

    def _suggest_lineage_candidates(
        self, previous: list[dict[str, Any]], current: list[dict[str, Any]],
    ) -> None:
        old_by_signature: dict[tuple[str, int, int], list[dict[str, Any]]] = {}
        for item in previous:
            key = (
                str(item.get("normalizedForm") or ""), int(item.get("occurrence") or 1),
                int(item.get("occurrences") or 1),
            )
            old_by_signature.setdefault(key, []).append(item)
        for item in current:
            key = (
                str(item.get("normalizedForm") or ""), int(item.get("occurrence") or 1),
                int(item.get("occurrences") or 1),
            )
            matches = old_by_signature.get(key, [])
            if len(matches) != 1:
                continue
            old = matches[0]
            candidate_id = "lineage-candidate-" + _sha256_text(
                f"{old['id']}\u241f{item['id']}\u241fPOSSIBLE_SUCCESSOR"
            )[:32]
            self.repository.save_token_lineage_candidate(
                candidate_id=candidate_id, project_id=self.project_id,
                old_instance_id=str(old["id"]), new_instance_id=str(item["id"]),
                relation="POSSIBLE_SUCCESSOR", confidence=1.0,
                reason_code="EXACT_NORMALIZED_OCCURRENCE_SIGNATURE",
            )

    @staticmethod
    def _legacy_token_signature(token: Any) -> tuple[str, int, int] | None:
        if not isinstance(token, dict):
            return None
        word = str(token.get("word") or "")
        try:
            occurrence = int(token.get("occurrence"))
            occurrences = int(token.get("occurrences"))
        except (TypeError, ValueError):
            return None
        if not word or occurrence < 1 or occurrences < occurrence:
            return None
        return word, occurrence, occurrences

    def synchronize_alignment_state(self) -> dict[str, Any]:
        """Read-only compatibility scan, plus V11-000a's alignment invalidation.

        Two independent content-addressed checks share this one call, each
        against the digest it actually needs to be sensitive to, tracked as
        separate `migration_run` rows so a redundant call is always cheap
        and neither degrades the other's own memoization:

        - Staling (below) keys on `alignment_state_digest`, which covers the
          tools/wordAlignment/completed|invalid markers as well as content --
          `complete_alignment()` can flip completion state with no content
          byte changing, and that alone changes what evidence Stage 6B
          should find.
        - The legacy-compatibility scan keys on `alignment_directory_digest`
          (content only, unchanged from before this existed) exactly as it
          always has, so calling this twice in one session (both at project
          open and again right after `bridge_service` finishes an alignment
          mutation) never re-quarantines the same already-recorded issue.

        Crash-safety follows from both being plain content-addressed
        idempotency checks: a crash between staling and its own save just
        leaves that digest reading as unprocessed next time, which redoes it
        safely (staling something already STALE is a no-op in effect).
        """
        # Every chapter file is read once here, for both digests and the scan.
        files = alignment_files(self.project)
        digest = alignment_directory_digest(self.project, files)
        state_digest = alignment_state_digest(self.project, content_digest=digest)
        state_key = f"{self.project.alignment_dir}::state"
        staled = 0
        if self.repository.migration_run_for(self.project_id, state_key, state_digest) is None:
            staled = self.repository.apply_alignment_invalidation(self.project_id, self.book)
            self.repository.save_migration_run(
                run_id=str(uuid.uuid4()), project_id=self.project_id,
                source_path=state_key, source_hash=state_digest,
                source_schema="translationCore.alignmentData.invalidation-state.v1",
                status="IMPORTED", started_at=_now(), report={"staled": staled},
            )

        source_path = str(self.project.alignment_dir)
        if self.repository.migration_run_for(self.project_id, source_path, digest) is not None:
            return {"changed": staled > 0, "staled": staled}
        started = _now()
        # Each chapter file is scanned once per content: a file whose digest
        # already has a run is not scanned again. One verse edit changes one
        # chapter file, so it scans that chapter, not the book, and records
        # nothing twice for the chapters it did not touch (the quarantine is
        # append-only and not deduplicated, so a whole-book rescan per edit
        # re-recorded every legacy issue of every unchanged chapter).
        scanned = self.repository.migration_run_hashes(self.project_id, ALIGNMENT_FILE_SCAN_SCHEMA)
        file_digests = {path: alignment_file_digest(path, data) for path, data in files}
        pending = [(path, data) for path, data in files
                   if file_digests[path] not in scanned.get(str(path), ())]
        report = {
            "filesScanned": len(pending), "filesUnchanged": len(files) - len(pending),
            "groupsScanned": 0, "quarantined": 0,
            "legacyEmptyBottomWords": 0, "duplicateMembership": 0,
            "malformedTokenIdentity": 0, "rawImportStubsSkipped": 0, "mutated": False,
        }
        # Collected and written in one transaction below. See
        # quarantine_migration_records_bulk: batching this was what brought a
        # Genesis-sized scan back under the import timeout.
        found: list[dict[str, Any]] = []

        def quarantine(*, source_kind: str, source_identity: str, reason_code: str, payload: dict[str, Any]) -> None:
            found.append({
                "sourceKind": source_kind, "sourceIdentity": source_identity,
                "reasonCode": reason_code, "payload": payload,
            })

        for path, data in pending:
            try:
                chapter = json.loads(data.decode("utf-8-sig"))
            except Exception as exc:
                quarantine(
                    source_kind="translationCore.alignmentData", source_identity=str(path),
                    reason_code="MALFORMED_LEGACY_ALIGNMENT_FILE",
                    payload={"originalText": data.decode("utf-8-sig", errors="replace"), "error": str(exc)},
                )
                report["quarantined"] += 1
                continue
            if not isinstance(chapter, dict):
                continue
            for verse, verse_data in chapter.items():
                groups = verse_data.get("alignments", []) if isinstance(verse_data, dict) else []
                memberships: dict[tuple[str, str, int, int], list[int]] = {}
                for group_index, group in enumerate(groups if isinstance(groups, list) else []):
                    if not isinstance(group, dict):
                        continue
                    report["groupsScanned"] += 1
                    bottom = group.get("bottomWords")
                    if bottom == []:
                        # A raw import writes exactly this for every not-yet-aligned
                        # source word (`blank_source_alignments`): one topWord, an
                        # empty bottom, nothing else. That is the normal state of a
                        # freshly imported book, not a legacy record whose meaning is
                        # ambiguous -- so quarantining it produced one row per word of
                        # the book (20,612 for Genesis) of a kind nothing ever reads.
                        # Skip that exact shape; anything else with an empty bottom is
                        # still a real translationCore record we cannot interpret.
                        if _is_raw_import_stub(group):
                            report["rawImportStubsSkipped"] += 1
                            continue
                        report["legacyEmptyBottomWords"] += 1
                        report["quarantined"] += 1
                        quarantine(
                            source_kind="translationCore.alignmentData",
                            source_identity=f"{path}#{verse}/alignment/{group_index}",
                            reason_code="LEGACY_EMPTY_BOTTOM_WORDS_AMBIGUOUS",
                            payload={"originalRecord": group},
                        )
                        continue
                    for side, tokens in (
                        ("SOURCE", group.get("topWords")), ("TARGET", bottom),
                    ):
                        if not isinstance(tokens, list):
                            report["malformedTokenIdentity"] += 1
                            report["quarantined"] += 1
                            quarantine(
                                source_kind="translationCore.alignmentData",
                                source_identity=f"{path}#{verse}/alignment/{group_index}/{side}",
                                reason_code="MALFORMED_LEGACY_TOKEN_IDENTITY",
                                payload={"originalRecord": group},
                            )
                            continue
                        for token in tokens:
                            signature = self._legacy_token_signature(token)
                            if signature is None:
                                report["malformedTokenIdentity"] += 1
                                report["quarantined"] += 1
                                quarantine(
                                    source_kind="translationCore.alignmentData",
                                    source_identity=f"{path}#{verse}/alignment/{group_index}/{side}",
                                    reason_code="MALFORMED_LEGACY_TOKEN_IDENTITY",
                                    payload={"originalRecord": group},
                                )
                                continue
                            memberships.setdefault((side, *signature), []).append(group_index)
                for signature, group_indexes in memberships.items():
                    if len(set(group_indexes)) <= 1:
                        continue
                    report["duplicateMembership"] += 1
                    report["quarantined"] += 1
                    quarantine(
                        source_kind="translationCore.alignmentData",
                        source_identity=f"{path}#{verse}/{signature}",
                        reason_code="DUPLICATE_ACTIVE_TOKEN_MEMBERSHIP",
                        payload={
                            "tokenSignature": list(signature), "groupIndexes": group_indexes,
                            "originalVerseRecord": verse_data,
                        },
                    )
        self.repository.quarantine_migration_records_bulk(found)
        # One transaction for the per-file runs and the folder's run. A crash
        # before it re-scans only the files of this sync next time.
        runs = [{
            "run_id": str(uuid.uuid4()), "source_path": str(path), "source_hash": file_digests[path],
            "source_schema": ALIGNMENT_FILE_SCAN_SCHEMA, "status": "IMPORTED", "started_at": started,
            "report": {"file": path.name},
        } for path, _data in pending]
        runs.append({
            "run_id": str(uuid.uuid4()), "source_path": source_path, "source_hash": digest,
            "source_schema": "translationCore.alignmentData.compatibility-scan.v1",
            "status": "IMPORTED", "started_at": started, "report": report,
        })
        self.repository.save_migration_runs(self.project_id, runs)
        return {"changed": True, "staled": staled, "report": report}

    def status(self) -> dict[str, Any]:
        recovery = self.repository.recovery_check()
        return {
            "available": True, "readOnly": recovery["readOnly"],
            "databaseSchemaVersion": DATABASE_SCHEMA_VERSION,
            "databasePath": str(self.path), "projectId": self.project_id,
            "book": self.book, "replayedInvalidations": self.replayed_invalidations,
            "recovery": recovery,
            "correctionWritesBlocked": bool(
                self.application_recovery.get("correctionWritesBlocked")
            ),
            "applicationRecovery": dict(self.application_recovery),
        }

    def project_metadata(self) -> dict[str, Any]:
        result = self.repository.project_metadata(self.project_id)
        lock = self.repository.source_lock(self.project_id, self.book)
        result["sourceLock"] = None if lock is None else {
            "projectId": lock["project_id"], "book": lock["book"],
            "resourceId": lock["resource_id"], "resourceVersion": lock["resource_version"],
            "resourceHash": lock["resource_hash"],
            "lifecycleStatus": lock["lifecycle_status"], "revision": lock["revision"],
            "updatedAt": lock["updated_at"],
        }
        return result

    def stale_summary(self) -> dict[str, Any]:
        return self.repository.stale_summary(self.project_id)

    def migration_report(self) -> dict[str, Any]:
        return self.repository.migration_report(self.project_id)

    def build_source_semantic_range(
        self, chapter: str, verse: str, end_chapter: str = "", end_verse: str = "",
    ) -> dict[str, Any]:
        return self.source_semantic.build_range(chapter, verse, end_chapter, end_verse)

    def source_semantic_range(self, inventory_id: str) -> dict[str, Any]:
        return self.source_semantic.get_range(inventory_id)

    def source_semantic_unit(self, unit_id: str) -> dict[str, Any]:
        return self.source_semantic.get_unit(unit_id)

    def source_semantic_coverage_accounts(self, inventory_id: str) -> list[dict[str, Any]]:
        return self.source_semantic.get_coverage_accounts(inventory_id)

    def source_semantic_diagnostics(self, inventory_id: str) -> dict[str, Any]:
        return self.source_semantic.get_diagnostics(inventory_id)

    def build_target_semantic_range(
        self, chapter: str, verse: str, end_chapter: str = "", end_verse: str = "",
    ) -> dict[str, Any]:
        return self.target_semantic.build_range(chapter, verse, end_chapter, end_verse)

    def target_semantic_range(self, inventory_id: str) -> dict[str, Any]:
        return self.target_semantic.get_range(inventory_id)

    def target_semantic_unit(self, unit_id: str) -> dict[str, Any]:
        return self.target_semantic.get_unit(unit_id)

    def target_semantic_diagnostics(self, inventory_id: str) -> dict[str, Any]:
        return self.target_semantic.get_diagnostics(inventory_id)

    def target_semantic_search_spans(self, inventory_id: str) -> list[dict[str, Any]]:
        return self.target_semantic.get_search_spans(inventory_id)

    def target_semantic_capabilities(self, inventory_id: str = "") -> dict[str, Any]:
        return self.target_semantic.get_capabilities(inventory_id)

    def run_semantic_location_range(
        self, chapter: str, verse: str, end_chapter: str = "", end_verse: str = "",
        max_candidate_evaluations: int | None = None,
    ) -> dict[str, Any]:
        return self.semantic_location.run_range(
            chapter, verse, end_chapter, end_verse,
            max_candidate_evaluations=max_candidate_evaluations,
        )

    def semantic_location_status(self, run_id: str) -> dict[str, Any]:
        return self.semantic_location.status(run_id)

    def semantic_location_range(self, run_id: str) -> dict[str, Any]:
        return self.semantic_location.get_range(run_id)

    def semantic_location_relationship(self, relationship_id: str) -> dict[str, Any]:
        return self.semantic_location.get_relationship(relationship_id)

    def semantic_location_candidates(
        self, run_id: str, source_owner_unit_id: str = "",
    ) -> list[dict[str, Any]]:
        return self.semantic_location.get_candidates(run_id, source_owner_unit_id)

    def semantic_location_diagnostics(self, run_id: str) -> dict[str, Any]:
        return self.semantic_location.get_diagnostics(run_id)

    def run_meaning_analysis_range(
        self, chapter: str, verse: str, end_chapter: str = "", end_verse: str = "",
        location_run_id: str = "",
    ) -> dict[str, Any]:
        return self.meaning_analysis.run_range(
            chapter, verse, end_chapter, end_verse, location_run_id=location_run_id,
        )

    def meaning_analysis_status(self, run_id: str) -> dict[str, Any]:
        return self.meaning_analysis.status(run_id)

    def meaning_analysis_range(self, run_id: str) -> dict[str, Any]:
        return self.meaning_analysis.get_range(run_id)

    def meaning_assessment(self, assessment_id: str) -> dict[str, Any]:
        return self.meaning_analysis.get_assessment(assessment_id)

    def meaning_components(self, assessment_id: str) -> list[dict[str, Any]]:
        return self.meaning_analysis.get_components(assessment_id)

    def meaning_analysis_diagnostics(self, run_id: str) -> dict[str, Any]:
        return self.meaning_analysis.get_diagnostics(run_id)

    def run_qa_audit_range(
        self, chapter: str, verse: str, end_chapter: str = "", end_verse: str = "",
        meaning_run_id: str = "",
    ) -> dict[str, Any]:
        return self.qa_audit.run_range(
            chapter, verse, end_chapter, end_verse, meaning_run_id=meaning_run_id,
        )

    def qa_audit_status(self, run_id: str) -> dict[str, Any]:
        return self.qa_audit.status(run_id)

    def qa_audit_range(self, run_id: str) -> dict[str, Any]:
        return self.qa_audit.get_range(run_id)

    def qa_audit_source_coverage(self, run_id: str) -> list[dict[str, Any]]:
        return self.qa_audit.get_source_coverage(run_id)

    def qa_audit_target_support(self, run_id: str) -> list[dict[str, Any]]:
        return self.qa_audit.get_target_support(run_id)

    def qa_audit_finding(self, finding_id: str) -> dict[str, Any]:
        return self.qa_audit.get_finding(finding_id)

    def qa_audit_diagnostics(self, run_id: str) -> dict[str, Any]:
        return self.qa_audit.get_diagnostics(run_id)

    # -- Stage 9A human review (never re-runs analysis) --------------------

    def qa_review_queue(self, **filters: Any) -> dict[str, Any]:
        return self.qa_review.get_queue(**filters)

    def qa_review_finding(self, finding_id: str) -> dict[str, Any]:
        return self.qa_review.get_finding(finding_id)

    def qa_review_decide(self, finding_id: str, disposition: str, **options: Any) -> dict[str, Any]:
        return self.qa_review.decide_finding(finding_id, disposition, **options)

    def qa_review_add_note(self, entity_type: str, entity_id: str, note: str) -> dict[str, Any]:
        return self.qa_review.add_note(entity_type, entity_id, note)

    def correction_get_eligibility(self, finding_id: str) -> dict[str, Any]:
        return self.correction_eligibility.evaluate(finding_id).to_dict()

    def correction_get_review_context(self, finding_id: str) -> dict[str, Any]:
        return self.correction_wording.review_context(finding_id)

    def correction_get_proposal(self, proposal_id: str) -> dict[str, Any]:
        return self.repository.correction_proposal(proposal_id)

    def correction_list_for_finding(self, finding_id: str) -> dict[str, Any]:
        return {
            "findingId": finding_id,
            "proposals": self.repository.correction_proposals_for_finding(finding_id),
            "applications": self.repository.applications_for_finding(finding_id),
            "correctionWritesBlocked": bool(
                self.application_recovery.get("correctionWritesBlocked")
            ),
        }

    def correction_create_proposal(
        self, *, provider: Any = None, **options: Any,
    ) -> dict[str, Any]:
        service = CorrectionWordingService(self, provider) if provider is not None else self.correction_wording
        return service.create_proposal(**options)

    def correction_edit_proposal(self, proposal_id: str, **options: Any) -> dict[str, Any]:
        return self.correction_wording.edit_proposal(proposal_id, **options)

    def correction_reject_proposal(self, proposal_id: str, **options: Any) -> dict[str, Any]:
        return self.correction_wording.reject_proposal(proposal_id, **options)

    def correction_regenerate_proposal(
        self, proposal_id: str, *, provider: Any, **options: Any,
    ) -> dict[str, Any]:
        return CorrectionWordingService(self, provider).regenerate_proposal(
            proposal_id, **options,
        )

    def semantic_review_decide_location(
        self, relationship_id: str, decision: str, **options: Any,
    ) -> dict[str, Any]:
        return self.qa_review.decide_location(relationship_id, decision, **options)

    def semantic_review_decide_meaning(
        self, assessment_id: str, meaning_status: str, **options: Any,
    ) -> dict[str, Any]:
        return self.qa_review.decide_meaning(assessment_id, meaning_status, **options)

    def review_history(self, entity_type: str, entity_id: str) -> dict[str, Any]:
        return self.qa_review.get_entity_history(entity_type, entity_id)
