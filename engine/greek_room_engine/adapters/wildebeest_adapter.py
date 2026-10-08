"""
Wildebeest adapter.

Per doc §7, this is the first and cleanest Greek Room component to
integrate: direct Python API, read-only, no modification of source files.
It catches mixed scripts, bad Unicode, misplaced Indic marks, punctuation
anomalies, etc. — exactly what the "Unicode" badge category needs.

This adapter tries to use the real `wildebeest` package if installed. If
it's not available (e.g. during frontend/protocol development before the
pip dependency is wired up), it falls back to a small set of deterministic
mock checks so the rest of the pipeline (protocol, caching, UI) can be
built and tested independently.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import sys
import threading
from collections import OrderedDict
from typing import Any

from .base import CheckAdapter
from ..models.finding import QaFinding, FindingCategory, Severity, EvidenceItem

log = logging.getLogger(__name__)

try:
    import wildebeest  # type: ignore
    import wildebeest.wb_analysis as wb_ana  # type: ignore
    _WILDEBEEST_AVAILABLE = True
    # stdout is reserved strictly for the JSON-RPC protocol stream
    # (stdio_transport.py) — stderr is where the Rust side's sidecar.rs
    # prints everything, prefixed "[bridge-engine]", visible live in
    # whichever terminal `npm run tauri dev` is running in. Without this
    # line, whether the real engine loaded or silently fell back to the
    # mock was completely invisible short of manually calling engine.info.
    print(f"[wildebeest] real engine active, version {getattr(wildebeest, '__version__', 'unknown')}",
          file=sys.stderr, flush=True)
except Exception as exc:
    # Deliberately broad, not just ImportError: an installed-but-broken
    # wildebeest must degrade to the mock, never crash the whole sidecar at
    # startup. Confirmed necessary, not defensive-programming paranoia —
    # wildebeest-nlp 0.9.2 (the only release on PyPI, and the same on
    # upstream's GitHub HEAD as of 2026-08) fails with UnicodeEncodeError,
    # not ImportError, when compiled under Python 3.13: one of its
    # docstrings contains a literal lone-surrogate escape (\uDC80-\uDCFF,
    # used as prose describing surrogateescape handling), which Python 3.13
    # newly rejects in docstrings (see CPython issue #142411). See
    # docs/BUILD_LOG.md for the full investigation and options.
    wb_ana = None
    _WILDEBEEST_AVAILABLE = False
    print(f"[wildebeest] real engine unavailable ({exc!r}), using mock fallback",
          file=sys.stderr, flush=True)


# Very small illustrative script-mixing check used only when the real
# `wildebeest` package isn't installed. NOT a substitute for the real thing.
_LATIN_RE = re.compile(r"[A-Za-z]")
_TAMIL_RE = re.compile(r"[\u0B80-\u0BFF]")
_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")


# The parts of a Wildebeest report this adapter reads, per (language, text).
# wb_ana.process is a pure function of its input for the package version this
# process loaded, and it was 58-68% of a whole-book check job, run again on
# every verse of every job and every verse selection (#230). Held for the life
# of the sidecar, not persisted: the project's check_cache rows are written
# through the workbench journal, and a row per verse would flood change_log.
# Keyed by a digest, so the cache never holds verse text; bounded, oldest out.
_REPORT_CACHE: "OrderedDict[tuple[str, bytes], dict[str, Any]]" = OrderedDict()
_REPORT_CACHE_MAX = 50_000
_REPORT_CACHE_LOCK = threading.Lock()


def _report_key(lang_code: str, text: str) -> tuple[str, bytes]:
    return (lang_code, hashlib.sha1(text.encode("utf-8", errors="surrogatepass")).digest())


def _cached_report(lang_code: str, text: str) -> dict[str, Any]:
    """The used part of wb_ana.process's report for this text. Raises what
    process raises; a failure is not cached, so the next call tries again."""
    key = _report_key(lang_code, text)
    with _REPORT_CACHE_LOCK:
        report = _REPORT_CACHE.get(key)
        if report is not None:
            _REPORT_CACHE.move_to_end(key)
            return report
    buf = io.StringIO()
    wb_ana.process(string=text, lang_code=lang_code, json_output=buf)  # type: ignore[union-attr]
    data = json.loads(buf.getvalue())
    report = {
        "notable-token": data.get("notable-token") or {},
        "non-canonical": data.get("non-canonical") or {},
        "zero-width": ((data.get("block") or {}).get("ZERO_WIDTH")) or {},
    }
    with _REPORT_CACHE_LOCK:
        _REPORT_CACHE[key] = report
        _REPORT_CACHE.move_to_end(key)
        while len(_REPORT_CACHE) > _REPORT_CACHE_MAX:
            _REPORT_CACHE.popitem(last=False)
    return report


class WildebeestAdapter(CheckAdapter):
    engine_name = "wildebeest"

    def is_available(self) -> bool:
        # Always available: falls back to the mock implementation when the
        # real `wildebeest` package isn't installed, so protocol/UI work
        # isn't blocked on the upstream dependency being wired up yet.
        return True

    def using_real_engine(self) -> bool:
        return _WILDEBEEST_AVAILABLE

    def version(self) -> str:
        if _WILDEBEEST_AVAILABLE:
            try:
                import wildebeest  # type: ignore
                return getattr(wildebeest, "__version__", "unknown")
            except Exception:
                return "unknown"
        return "mock-0.0.0"

    def check_verse(self, *, project_id: str, lang_code: str, ref: str,
                     text: str, params: dict[str, Any]) -> list[QaFinding]:
        if _WILDEBEEST_AVAILABLE:
            return self._check_with_wildebeest(project_id, lang_code, ref, text)
        return self._check_mock(project_id, lang_code, ref, text)

    # -- real integration -------------------------------------------------

    def _check_with_wildebeest(self, project_id: str, lang_code: str,
                                ref: str, text: str) -> list[QaFinding]:
        """
        Adapts real Wildebeest analysis into QaFindings.

        wb_analysis has no `check()`/issue-list function — it's built as a
        bulk file-analysis tool. The actual programmatic entry point is
        `wb_analysis.process(string=..., lang_code=..., json_output=<IO>)`,
        which writes an aggregate report grouped by category, not a flat
        per-position issue list (verified directly against wildebeest-nlp
        0.9.2, since its own docs don't describe this — see
        docs/BUILD_LOG.md for the investigation).

        Only a subset of that report's top-level keys represent an actual
        problem for a single short verse; most of the rest (letter-script
        counts, the full per-character `block` inventory, digit/number
        breakdowns) are corpus-level descriptive tallies — e.g. `block`
        lists ordinary Tamil letters just because they occurred, not
        because anything is wrong with them. Treating every key as a
        finding would flood a verse with "findings" for its own ordinary
        text. Wired up so far, each verified against real flagged input:
          - `notable-token`: tokens containing characters from more than
            one script (mixed-script tokens — the case the old mock
            approximated with one regex).
          - `non-canonical`: characters not in Unicode Normalization Form C
            (e.g. NFD-decomposed instead of NFC-composed).
          - zero-width/invisible characters, read from `block.ZERO_WIDTH`
            (bidi/formatting characters that are invisible but real).
        Not yet wired: `char-conflict` and `pattern` — real categories in
        the schema, but no input was found in this session that populated
        them, so their exact shape is still unverified. Don't guess it;
        that's exactly the mistake that made this function wrong the first
        time. Verify against real triggering input before adding them.
        """
        findings: list[QaFinding] = []
        book = ref.split()[0] if " " in ref else ""
        try:
            data = _cached_report(lang_code, text)
        except Exception:
            # Wildebeest is explicitly Alpha (doc §31) — a bad verse must
            # degrade to "no wildebeest findings for this verse", never take
            # down the whole verse.runChecks call alongside local/tN/tW
            # findings that have nothing to do with it.
            log.exception("Real Wildebeest analysis failed for %s; skipping wildebeest findings for this verse.", ref)
            return findings

        for category_name, tokens in data["notable-token"].items():
            if not isinstance(tokens, dict):
                continue
            for token_text in tokens:
                findings.append(self._make_finding(
                    project_id=project_id, book=book, text=text, flagged=token_text,
                    check_type="wildebeest.notable_token",
                    severity=Severity.HIGH, confidence=0.85,
                    explanation=f"{category_name.capitalize()}.",
                    evidence=[EvidenceItem(label="Category", value=category_name)],
                ))

        for orig, details in data["non-canonical"].items():
            if not isinstance(details, dict):
                continue
            norm = str(details.get("norm") or "") or None
            orig_form = str(details.get("orig-form") or "").strip().rstrip(",").strip()
            norm_form = str(details.get("norm-form") or "").strip().rstrip(",").strip()
            findings.append(self._make_finding(
                project_id=project_id, book=book, text=text, flagged=orig,
                check_type="wildebeest.non_canonical",
                severity=Severity.MEDIUM, confidence=0.75,
                explanation=f"Not in Unicode Normalization Form C ({orig_form} instead of {norm_form}).",
                suggested_replacement=norm,
            ))

        for char, details in data["zero-width"].items():
            if not isinstance(details, dict):
                continue
            name = str(details.get("name") or "unknown character")
            findings.append(self._make_finding(
                project_id=project_id, book=book, text=text, flagged=char,
                check_type="wildebeest.zero_width",
                severity=Severity.MEDIUM, confidence=0.8,
                explanation=f"Contains a zero-width/invisible character: {name}.",
            ))

        return findings

    def _make_finding(self, *, project_id: str, book: str, text: str, flagged: str,
                       check_type: str, severity: Severity, confidence: float,
                       explanation: str, suggested_replacement: str | None = None,
                       evidence: list[EvidenceItem] | None = None) -> QaFinding:
        start = text.find(flagged) if flagged else -1
        return QaFinding(
            project_id=project_id,
            book=book,
            original_text=flagged,
            start_offset=start if start >= 0 else None,
            end_offset=(start + len(flagged)) if start >= 0 else None,
            engine=self.engine_name,
            check_type=check_type,
            category=FindingCategory.UNICODE,
            severity=severity,
            confidence=confidence,
            suggested_replacement=suggested_replacement,
            explanation=explanation,
            evidence=evidence or [],
            engine_version=self.version(),
        )

    # -- fallback mock ------------------------------------------------------

    def _check_mock(self, project_id: str, lang_code: str, ref: str,
                     text: str) -> list[QaFinding]:
        findings: list[QaFinding] = []

        has_latin = bool(_LATIN_RE.search(text))
        has_tamil = bool(_TAMIL_RE.search(text))
        has_deva = bool(_DEVANAGARI_RE.search(text))

        if has_latin and (has_tamil or has_deva):
            match = _LATIN_RE.search(text)
            start, end = match.start(), match.end()
            findings.append(QaFinding(
                project_id=project_id,
                book=ref.split()[0] if " " in ref else "",
                original_text=text[start:end],
                start_offset=start,
                end_offset=end,
                engine=self.engine_name,
                check_type="wildebeest.script.mixed",
                category=FindingCategory.UNICODE,
                severity=Severity.HIGH,
                confidence=0.9,
                explanation="Token contains a Latin character mixed into non-Latin script text.",
                evidence=[EvidenceItem(label="Mode", value="mock adapter (wildebeest not installed)")],
                engine_version=self.version(),
            ))

        return findings
