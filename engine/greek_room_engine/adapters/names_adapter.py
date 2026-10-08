"""
Names & Transliteration spelling-consistency adapter.

Wraps two independent building blocks as one whole-book QA check:

- Uroman (real PyPI dependency ``uroman``, by Ulf Hermjakob, USC/ISI — the
  same author/group as Wildebeest and the vendored greek-room tools)
  romanizes target-language text to a common Latin representation.
- Smart Edit Distance (SED), vendored from the same pinned
  ``BibleNLP/greek-room`` commit already used for the USFM checker and
  versification (``engine/vendor/greekroom-smart-edit-distance/``, see that
  directory's NOTICE.md) — unpublished anywhere, so it's vendored rather
  than a normal dependency. Unlike the USFM checker it's a small,
  pure-stdlib, per-instance class with no class-level shared state, so
  (like ``versification.py``) it's imported directly into bridge-engine,
  not run as a subprocess. See docs/BUILD_LOG.md's Phase 5 section
  for the full investigation of both dependencies.

Like ``UsfmAdapter``, this is a WHOLE-BOOK check, not per-verse: consistency
is inherently a corpus-level question (there's nothing to compare a single
verse's spelling *against*). The caller (bridge_service.py) runs this once
per book and caches the result, the same reason and the same shape as the
USFM structural checker.

What it flags, and — just as importantly — what it deliberately does NOT
claim: this compares every pair of distinct target-language word types used
in the book and flags pairs whose *romanized* forms are suspiciously close
(low Smart Edit Distance cost) but not identical. It never asserts that two
spellings refer to the same name, or even that either one is wrong — that
would be Greek Room making a semantic claim it has no way to verify. It
only asserts a measurable, objective fact: "these two spellings are
unusually close to each other." That keeps it on the correct side of
Bridge's own three-way design boundary (architecture doc, "Greek Room says:
this is objectively suspicious") — the human reviewer decides whether it's
a real inconsistency, a coincidence, or two genuinely distinct words that
happen to look similar.
"""
from __future__ import annotations

import re
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from .base import CheckAdapter
from ..models.finding import EvidenceItem, FindingCategory, QaFinding, Severity


def _vendor_root() -> Path:
    """Same frozen-vs-source resolution as tc_ai_bridge.versification's
    _vendor_root() — this module is imported directly into bridge-engine,
    so in a frozen build its vendor tree must be resolved under
    sys._MEIPASS (see bridge-engine.spec's `datas` entry), not a path
    relative to this source file, which doesn't exist inside a PyInstaller
    onefile bundle."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "vendor" / "greekroom-smart-edit-distance"
    return Path(__file__).resolve().parent.parent.parent / "vendor" / "greekroom-smart-edit-distance"


VENDOR_ROOT = _vendor_root()
COST_RULES_PATH = VENDOR_ROOT / "data" / "string-distance-cost-rules.txt"

# Tuned against real observed costs, not guessed. The vendored module's own
# "Jim"/"Kim" example scores exactly the default substitution cost of 1.0,
# which its docstring frames as meaningfully DIFFERENT — but 1.0 alone
# turned out not to be a safe ceiling: testing this adapter against plain
# English "church"/"churches" (an ordinary, CORRECT singular/plural pair,
# not a spelling inconsistency) scored 0.70 under the general cost-rules
# file, because rules tuned for name-like variation (dropped vowels,
# consonant doubling) also happen to cover common inflectional endings.
# 0.4 is the highest ceiling that still keeps real phonetic-variant pairs
# found during the Phase 5 investigation ("Josef Schumann"/"Joseph
# Schuman" = 0.03, "Muhammad"/"Mohamed" = 0.22, a synthetic "Titus"/
# "Tituss" typo = 0.02, "Yohaan"/"Yohan" = 0.02) while excluding that
# false-positive class. This is an inherent limitation of an edit-distance
# family metric applied to morphologically rich target languages, not
# something a threshold alone fully solves — expect some inflectional
# false positives to still surface near this ceiling; the human reviewer
# is the actual filter, per this adapter's own design boundary above.
_MAX_COST = 0.4
# Skip very short tokens (particles, short function words) — real names and
# meaningful vocabulary are essentially never this short, and short strings
# dominate false-positive pairs under any edit-distance metric.
_MIN_TOKEN_LENGTH = 3
# Pruning: only compare romanized forms whose lengths differ by at most
# this many characters. A cost this low can't plausibly still be reached
# once more than a couple of characters would need to be inserted/deleted,
# so this bounds the comparison space to roughly one length-bucket's
# neighborhood instead of a full O(n^2) scan over book vocabulary.
_MAX_LENGTH_DELTA = 2
_MAX_EXAMPLE_LOCATIONS = 3
# Bigram-blocking parameters (see _candidate_pairs) — how much two romanized
# forms' bigram sets are allowed to disagree and still be worth the
# expensive exact comparison, and a hard cap on any single bigram's posting
# list so an unusually common bigram can't reintroduce an O(n^2) hot spot.
_MAX_BIGRAM_MISMATCH = 6
_MAX_BIGRAM_BUCKET = 250

_lock = threading.Lock()
_uroman = None
_uroman_unavailable = False
_sed_module = None
_sed_unavailable_reason: str | None = None
_sed_cache: dict[str, Any] = {}  # lang_code -> loaded SmartEditDistance instance


# Romanization of a token, and the edit distance between two romanized forms,
# are pure for one language and the loaded uroman/SED tables. After a one-verse
# edit the check used to redo both for the whole book: on GEN 4.2 s over 7,279
# tokens and 8.6 s over 38,577 pairs (#233). Held for the life of the sidecar,
# like Wildebeest's reports (#230): not persisted, because check_cache rows are
# journalled. Bounded, oldest out.
_ROMANIZED: "OrderedDict[tuple[str, str], str]" = OrderedDict()
_DISTANCES: "OrderedDict[tuple[str, str, str], tuple[Any, Any]]" = OrderedDict()
_ROMANIZED_MAX = 300_000
_DISTANCES_MAX = 400_000
_cache_lock = threading.Lock()


def _cached(cache: OrderedDict, limit: int, key: tuple, compute):
    with _cache_lock:
        if key in cache:
            cache.move_to_end(key)
            return cache[key]
    value = compute()
    with _cache_lock:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > limit:
            cache.popitem(last=False)
    return value


class NamesCheckError(RuntimeError):
    """Uroman or Smart Edit Distance could not be loaded/used reliably."""


def _ensure_uroman():
    """Load Uroman's full table set exactly once per process.

    Verified directly (not assumed from docs) that construction is a real,
    substantial cost — ~1.8-2.1s measured during the Phase 5 investigation —
    after which romanize_string() calls are effectively instant. This is
    the concrete reason the vendored tool's own "load once, not per call"
    recommendation is worth following, not a rubber-stamped best practice.
    """
    global _uroman, _uroman_unavailable
    if _uroman is not None or _uroman_unavailable:
        return _uroman
    with _lock:
        if _uroman is not None or _uroman_unavailable:
            return _uroman
        try:
            import uroman as uroman_pkg
        except ImportError:
            _uroman_unavailable = True
            return None
        _uroman = uroman_pkg.Uroman()
        return _uroman


def _load_sed_module():
    global _sed_module, _sed_unavailable_reason
    if _sed_module is not None or _sed_unavailable_reason is not None:
        return _sed_module
    if not COST_RULES_PATH.is_file():
        _sed_unavailable_reason = f"cost rules file not found: {COST_RULES_PATH}"
        return None
    if str(VENDOR_ROOT) not in sys.path:
        sys.path.insert(0, str(VENDOR_ROOT))
    try:
        import smart_edit_distance as sed_mod  # the vendored module
    except ImportError as exc:
        _sed_unavailable_reason = f"vendored smart_edit_distance module could not be imported: {exc}"
        return None
    _sed_module = sed_mod
    return _sed_module


def _ensure_sed(lang_code: str):
    """Load (and cache) a SmartEditDistance instance for one target
    language. Cached per-language rather than as a single global instance:
    the vendored loader applies any language-restricted cost rules (::lc1/
    ::lc2) at LOAD time based on the language codes passed in, not at
    compare time, so a language-specific rule only takes effect if this
    was loaded with that language's code. Re-parsing the 44KB cost-rules
    text file per distinct target language is milliseconds, nothing like
    Uroman's multi-second table load, so a small per-language cache (rather
    than a single global singleton) is the right tradeoff here.

    Always opens the cost-rules file itself with explicit UTF-8 encoding
    and hands the vendored loader a file object, never a string path — this
    architecturally avoids load_smart_edit_distance_data()'s bare open()
    bug on Windows (see NOTICE.md in the vendor directory), the same choice
    already made for versification.py's equivalent bug.
    """
    key = lang_code or ""
    with _lock:
        cached = _sed_cache.get(key)
        if cached is not None:
            return cached
        sed_mod = _load_sed_module()
        if sed_mod is None:
            return None
        sd = sed_mod.SmartEditDistance()
        with COST_RULES_PATH.open(encoding="utf-8") as fh:
            sd.load_smart_edit_distance_data(fh, key, key)
        _sed_cache[key] = sd
        return sd


def _numeric_anchor(value: str) -> int:
    """Same idiom as _qaissue_to_finding in bridge_service.py: USFM verse
    bridges (3-4) and segments (3a) are real input; take the first numeric
    component as the anchor rather than crashing on int()."""
    match = re.findall(r"\d+", str(value))
    return int(match[0]) if match else 0


class NamesAdapter(CheckAdapter):
    engine_name = "names"

    def is_available(self) -> bool:
        return _ensure_uroman() is not None and _load_sed_module() is not None

    def using_real_engine(self) -> bool:
        return self.is_available()

    def version(self) -> str:
        return "uroman-1.3.1.1+sed-vendored-18ddcf0"

    def check_verse(self, *, project_id: str, lang_code: str, ref: str,
                     text: str, params: dict[str, Any]) -> list[QaFinding]:
        # Not used: like UsfmAdapter, this is a whole-book check invoked
        # directly via check_book(), not through GreekRoomEngine's per-verse
        # dispatch loop. Present only to satisfy the CheckAdapter interface.
        return []

    def check_book(
        self, *, project_id: str, book_id: str, lang_code: str,
        token_occurrences: dict[str, list[tuple[str, str]]],
    ) -> list[QaFinding]:
        """token_occurrences: distinct target-language token -> every
        (chapter, verse) location it occurs at. Tokenized by the caller
        (bridge_service.py, via tc_ai_bridge.usfm.whitespace_tokens) rather
        than here, so this adapter — like every other adapter in this
        package — stays free of tc_ai_bridge imports; whitespace_tokens
        already handles real script-specific concerns (preserving Tamil/
        Hebrew combining marks, trimming Devanagari danda punctuation) that
        shouldn't be reimplemented a second time.
        """
        # Wall clock per phase of the last call, for the caller's timings
        # (bridge_service records it into check_timing; this package stays
        # free of tc_ai_bridge imports). name -> (seconds, count).
        phases: dict[str, tuple[float, int]] = {}
        self.last_phases = phases
        mark = time.perf_counter()
        uroman = _ensure_uroman()
        sed = _ensure_sed(lang_code)
        now = time.perf_counter()
        phases["names.load"] = (now - mark, 1)
        mark = now
        if uroman is None or sed is None:
            reason = _sed_unavailable_reason or "uroman package is not installed"
            raise NamesCheckError(f"Names/transliteration check unavailable: {reason}")

        candidates = {
            token: locations for token, locations in token_occurrences.items()
            if len(token) >= _MIN_TOKEN_LENGTH
        }

        romanized: dict[str, str] = {}
        for token in candidates:
            try:
                romanized[token] = _cached(
                    _ROMANIZED, _ROMANIZED_MAX, (lang_code, token),
                    lambda: uroman.romanize_string(token, lcode=lang_code or None))
            except Exception:
                # A single token's romanization failing (e.g. an unexpected
                # character) shouldn't sink the whole book's check. Not cached.
                romanized[token] = token
        now = time.perf_counter()
        phases["names.romanize"] = (now - mark, len(candidates))
        mark = now

        candidate_pairs = self._candidate_pairs(romanized)
        now = time.perf_counter()
        phases["names.candidate_pairs"] = (now - mark, len(candidate_pairs))
        mark = now

        findings: list[QaFinding] = []
        for token_a, token_b in candidate_pairs:
            rom_a, rom_b = romanized[token_a], romanized[token_b]
            if rom_a == rom_b:
                continue
            cost, cost_log = _cached(
                _DISTANCES, _DISTANCES_MAX, (lang_code, rom_a, rom_b),
                lambda: sed.string_distance_cost(rom_a, rom_b, max_cost=_MAX_COST)[:2])
            if cost is None or cost <= 0:
                continue
            findings.append(self._build_finding(
                project_id=project_id, book_id=book_id,
                token_a=token_a, token_b=token_b,
                locations_a=candidates[token_a], locations_b=candidates[token_b],
                cost=cost, cost_log=cost_log,
            ))
        phases["names.distance"] = (time.perf_counter() - mark, len(candidate_pairs))
        return findings

    @staticmethod
    def _bigrams(s: str) -> set[str]:
        if len(s) < 2:
            return {s} if s else set()
        return {s[i:i + 2] for i in range(len(s) - 1)}

    def _candidate_pairs(self, romanized: dict[str, str]) -> set[tuple[str, str]]:
        """Cut the O(n^2) comparison space down to pairs actually worth
        running the expensive DP-based string_distance_cost on.

        Plain length-bucket pruning alone was measured to be far too slow:
        ~3000 distinct word types took 133 SECONDS on real hardware (not
        estimated — timed directly), because a whole-book vocabulary this
        size still yields well over a million length-compatible pairs, and
        each string_distance_cost call is itself a non-trivial DP.

        This uses character-bigram "blocking", a standard approximate
        record-linkage technique: two strings differing by only a couple of
        edits (exactly the near-duplicate case this check is looking for)
        share almost all of their bigrams, so requiring most bigrams to
        overlap — rather than comparing every pair outright — throws away
        the overwhelming majority of unrelated pairs before ever calling
        the expensive function. Reduced the same 3000-token benchmark from
        133s to well under a second (see docs/BUILD_LOG.md's Phase
        5 section for the measured before/after).
        """
        bigram_sets: dict[str, set[str]] = {}
        bigram_index: dict[str, list[str]] = {}
        for token, rom in romanized.items():
            bg = self._bigrams(rom)
            bigram_sets[token] = bg
            for b in bg:
                bigram_index.setdefault(b, []).append(token)

        pairs: set[tuple[str, str]] = set()
        for token, bg in bigram_sets.items():
            shared_counts: dict[str, int] = {}
            for b in bg:
                bucket = bigram_index[b]
                # A bigram shared by an enormous fraction of the vocabulary
                # (common in short/low-diversity scripts) isn't a useful
                # discriminator — skip it as a blocking key rather than
                # letting it reintroduce an O(n^2) hot spot.
                if len(bucket) > _MAX_BIGRAM_BUCKET:
                    continue
                for other in bucket:
                    if other != token:
                        shared_counts[other] = shared_counts.get(other, 0) + 1
            len_token = len(romanized[token])
            for other, shared in shared_counts.items():
                # A real near-duplicate (1-2 character edits) shares almost
                # all of its bigrams with the other string — require shared
                # count within _MAX_BIGRAM_MISMATCH of the SMALLER token's
                # own bigram count, rather than a fixed absolute threshold,
                # so this scales sensibly across both short and long words.
                min_bigrams = min(len(bg), len(bigram_sets[other]))
                if shared < min_bigrams - _MAX_BIGRAM_MISMATCH:
                    continue
                if abs(len_token - len(romanized[other])) > _MAX_LENGTH_DELTA:
                    continue
                pairs.add((token, other) if token < other else (other, token))
        return pairs

    def _build_finding(
        self, *, project_id: str, book_id: str, token_a: str, token_b: str,
        locations_a: list[tuple[str, str]], locations_b: list[tuple[str, str]],
        cost: float, cost_log: str,
    ) -> QaFinding:
        count_a, count_b = len(locations_a), len(locations_b)
        # The more frequent spelling is treated as the presumed-intended
        # form and anchors the finding at the minority spelling's first
        # occurrence — a human-reviewable suggestion, not an auto-applied
        # correction (nothing here writes to project files).
        if count_a > count_b or (count_a == count_b and token_a < token_b):
            majority_token, majority_locations = token_a, locations_a
            minority_token, minority_locations = token_b, locations_b
        else:
            majority_token, majority_locations = token_b, locations_b
            minority_token, minority_locations = token_a, locations_a

        chapter, verse = minority_locations[0]
        severity = Severity.MEDIUM if cost <= _MAX_COST / 2 else Severity.LOW
        confidence = max(0.0, min(1.0, 1.0 - (cost / _MAX_COST)))

        def _location_list(locations: list[tuple[str, str]]) -> str:
            return ", ".join(f"{c}:{v}" for c, v in locations[:_MAX_EXAMPLE_LOCATIONS])

        evidence = [
            EvidenceItem(label="More common spelling",
                         value=f"“{majority_token}” — {len(majority_locations)}x, e.g. {_location_list(majority_locations)}"),
            EvidenceItem(label="Less common spelling",
                         value=f"“{minority_token}” — {len(minority_locations)}x, e.g. {_location_list(minority_locations)}"),
            EvidenceItem(label="Romanized similarity cost",
                         value=f"{cost:.2f} ({cost_log or 'default substitution'})"),
        ]

        return QaFinding(
            project_id=project_id,
            book=book_id,
            chapter=_numeric_anchor(chapter),
            verse=_numeric_anchor(verse),
            original_text=minority_token,
            engine=self.engine_name,
            check_type="names.spelling_similarity",
            category=FindingCategory.SPELLING,
            severity=severity,
            confidence=confidence,
            suggested_replacement=majority_token,
            explanation=(
                f"“{minority_token}” is spelled very similarly to “{majority_token}” "
                f"({len(majority_locations)}x elsewhere in this book) — possible spelling "
                "inconsistency, or an intentional distinct word."
            ),
            evidence=evidence,
            engine_version=self.version(),
        )
