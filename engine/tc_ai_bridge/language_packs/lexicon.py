"""A pack's corpus lexicon and the `lexicon-lookup` book checks (layered-rules Phase 5).

A pack names its lexicon in pack.json (`"lexicon": "lexicon.json.gz"`; ta-irv's
is built by scripts/build_tamil_lexicon.py). It holds how often each word
occurs in the pack's corpus, the words common enough to suggest, their
precomputed one-cluster deletion neighbours, and a curated map of known
misspellings. It is loaded once per process, on the first Language QA pass
that needs it, never at startup. Two book-level checks use it (the
`known-split` check is a word-pair check, indic/kinds.py):

- `rare-near-common`: a word rare in this book (at most `rareBookMax`) and
  rare in the corpus (absent from, or at most `rareCorpusMax` in, the
  lexicon) within `maxDistance` of words common in the corpus (at least
  `commonMin`, and `ratioMin` times the rare word's own corpus count), by the
  pack's confusion set. Up to five ranked suggestions, by (distance, corpus
  count, same-book count), each with its evidence. For ta-irv it is DISABLED
  since the 2026-09-28 review, 0 of 21: every suggestion was a different real
  word -- a feminine past -ஆள் against a conditional -ஆல், வாள்/வாழ்,
  காலை/காளை, a name. It may be re-enabled only when a candidate passes a
  morphology filter and a new human sample shows at least 0.90
  (DECISIONS.md, 2026-09-28).
- `known-misspelling`: a word in the curated `deprecated` map, with its
  correction. For ta-irv: confidence high, 43 of 43 human-confirmed on
  2026-09-28; inline. A pair no human confirmed is medium and never inline.

`protected` holds words a reviewer confirmed are correct; neither check ever
flags one. The lexicon is evidence for ranking and never learns by itself:
translator decisions are reported for a human to fold into the curated list
(scripts/lexicon_feedback_report.py), never written back (DECISIONS.md).
"""
from __future__ import annotations

import gzip
import hashlib
import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .indic.confusion import clusters

LEXICON_VERSION = "ta-irv-lexicon@4"  # 2: 2026-09-28. 3: pair provenance. 4: known splits (2026-09-29)
# Build-time bounds (scripts/build_tamil_lexicon.py).
MIN_LISTED_COUNT = 3   # a word seen fewer times is not listed: absent means rare
COMMON_MIN = 6         # corpus count a suggestion needs
# The rule's default thresholds (a rule's `match` may set its own:
# indic/kinds.py RARE_NEAR_COMMON); docs/LANGUAGE_QA_BENCHMARK.md cites them.
RARE_BOOK_MAX = 2
RARE_CORPUS_MAX = 2
RATIO_MIN = 5
# 0.5: the typist confusions only (the pack's confusion.json). At 1.0 -- any one
# cluster edit -- Tamil inflection floods it: 2,931 findings at 0.8% strict
# precision on the review set, against 76 at 2.6% here (BUILD_LOG, Phase 5).
MAX_DISTANCE = 0.5
MIN_CLUSTERS = 3
MAX_SUGGESTIONS = 5
MAX_LEXICON_FINDINGS = 200


def deletion_keys(word: str) -> set[str]:
    """The word with one grapheme cluster removed, every way."""
    parts = clusters(word)
    return {"".join(parts[:i] + parts[i + 1:]) for i in range(len(parts))}


@dataclass
class Lexicon:
    version: str
    forms: dict[str, list[int]]
    common: list[str]
    buckets: dict[str, list[int]]
    deprecated: dict[str, str]
    corpus: dict[str, Any]
    protected: frozenset = frozenset()
    # wrong -> "human" | "ai-review"; a pair with none is ai-review.
    provenance: dict[str, str] = field(default_factory=dict)
    # "word word" -> the one word it is (known-split), human-confirmed only.
    splits: dict[str, str] = field(default_factory=dict)

    def count(self, word: str) -> int:
        entry = self.forms.get(word)
        return int(entry[0]) if entry else 0

    def candidates(self, word: str) -> set[str]:
        """Common words sharing a one-cluster deletion neighbour with `word`
        (covers one substitution, insertion or deletion)."""
        found: set[str] = set()
        for key in {word, *deletion_keys(word)}:
            for index in self.buckets.get(key, ()):
                found.add(self.common[index])
        found.discard(word)
        return found


def read_lexicon_json(path: Path) -> dict[str, Any]:
    """The lexicon's JSON, from `.json` or gzip-compressed `.json.gz`."""
    raw = path.read_bytes()
    if path.suffix == ".gz":
        raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8"))


def write_lexicon_json(path: Path, data: dict[str, Any]) -> None:
    """Write a lexicon deterministically: identical data, identical bytes
    (gzip with no file name and mtime 0), so a rebuild is diffable by hash."""
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if path.suffix != ".gz":
        path.write_bytes(raw)
        return
    with path.open("wb") as handle, gzip.GzipFile(filename="", mode="wb", fileobj=handle, mtime=0,
                                                   compresslevel=9) as stream:
        stream.write(raw)


def load_lexicon(path: Path) -> Lexicon | None:
    if not path.is_file():
        return None
    data = read_lexicon_json(path)
    return Lexicon(version=str(data.get("version") or LEXICON_VERSION), forms=data.get("forms") or {},
                   common=list(data.get("common") or []), buckets=data.get("buckets") or {},
                   deprecated=data.get("deprecated") or {}, corpus=data.get("corpus") or {},
                   protected=frozenset(data.get("protected") or ()),
                   provenance=dict(data.get("provenance") or {}),
                   splits=dict(data.get("splits") or {}))


_LOADED: dict[Path, Lexicon | None] = {}
_LOCK = threading.Lock()


def load_file(path: Path) -> Lexicon | None:
    """A lexicon file, loaded once per process (serialised, as the pack is)."""
    path = Path(path)
    if path not in _LOADED:
        with _LOCK:
            if path not in _LOADED:
                _LOADED[path] = load_lexicon(path)
    return _LOADED[path]


def load(pack_name: str) -> Lexicon | None:
    """The named pack's lexicon, or None when the pack has none."""
    from .loader import default_pack
    return default_pack(pack_name).lexicon()


load.cache_clear = _LOADED.clear  # type: ignore[attr-defined]


def lexicon_findings(book: str, counts: dict[str, int],
                     first_seen: dict[str, tuple[str, str, int, int, str, str]],
                     lexicon: Lexicon, *, pack: Any, rule_fields: Any, suggestion: Any) -> list[dict[str, Any]]:
    """Pure function over one book's word counts: the pack's enabled
    `lexicon-lookup` book checks. `first_seen` maps a word to (chapter,
    verse, start, end, originalText, textHash) of its first occurrence; the
    finding sits there. `rule_fields`/`suggestion` are language_qa's own
    builders (passed in to keep the import one-way)."""
    known = pack.book_rule("lexicon-lookup", "known-misspelling")
    near_rule = pack.book_rule("lexicon-lookup", "rare-near-common")
    confusion = pack.confusion() if near_rule is not None else None
    findings: list[dict[str, Any]] = []
    near = 0  # the cap is per rule: the noisier rule must not crowd out the reviewed one
    for word in sorted(counts):
        if word not in first_seen or word in lexicon.protected:
            continue
        chapter, verse, start, end, original, text_hash = first_seen[word]
        right = lexicon.deprecated.get(word) if known is not None else None
        if right:
            human = lexicon.provenance.get(word) == "human"
            values = {"word": word, "fix": right}
            text = known.params["unconfirmed"]
            finding = _finding(
                book, pack, known, word, chapter, verse, start, end, original, text_hash,
                (known.message if human else text["message"]).format(**values),
                [suggestion(right, "lexicon", (known.rationale if human else text["rationale"]).format(**values))],
                rule_fields)
            if not human:
                # Only a human-confirmed pair is high confidence and drawn inline (2026-09-29).
                finding.update(confidence="medium", inline=False)
            finding["provenance"] = "human" if human else "ai-review"
            findings.append(finding)
            continue
        if near_rule is None or confusion is None:
            continue
        p = near_rule.params
        book_count = counts[word]
        corpus_count = lexicon.count(word)
        if (near >= p["maxFindings"] or book_count > p["rareBookMax"] or corpus_count > p["rareCorpusMax"]
                or len(clusters(word)) < p["minClusters"]):
            continue
        ranked = []
        for candidate in lexicon.candidates(word):
            candidate_count = lexicon.count(candidate)
            if candidate_count < p["commonMin"] or candidate_count < max(1, corpus_count) * p["ratioMin"]:
                continue
            distance = confusion.distance(word, candidate)
            if distance > p["maxDistance"]:
                continue
            ranked.append((distance, -candidate_count, -counts.get(candidate, 0), candidate))
        if not ranked:
            continue
        ranked.sort()
        suggestions = [
            suggestion(candidate, "lexicon", p["suggestionRationale"].format(
                corpusCount=-corpus, bookCount=-same, distance=f"{distance:g}"))
            for distance, corpus, same, candidate in ranked[:p["maxSuggestions"]]
        ]
        best = ranked[0][3]
        findings.append(_finding(
            book, pack, near_rule, word, chapter, verse, start, end, original, text_hash,
            near_rule.message.format(word=word, bookCount=book_count, corpusCount=corpus_count, best=best,
                                     bestCount=-ranked[0][1]),
            suggestions, rule_fields))
        near += 1
    return findings


def _finding(book, pack, rule, word, chapter, verse, start, end, original, text_hash, message, suggestions,
             rule_fields) -> dict[str, Any]:
    identity = f"{book}:{rule.name}:{word}"
    return {
        "id": hashlib.sha1(identity.encode("utf-8", errors="surrogatepass")).hexdigest()[:20],
        "book": book, "chapter": chapter, "verse": verse, "rule": rule.name,
        "severity": rule.severity,
        "start": start, "end": end, "originalText": original, "message": message,
        "textHash": text_hash, "ruleVersion": pack.stamp(rule)[1], "status": "review-needed",
        **rule_fields(rule.name, suggestions, pack=pack, pack_rule=rule),
    }


def lexicon_fingerprint(lexicon: Lexicon | None) -> str:
    """What of a lexicon a cached verse result depends on (its known splits
    run in the verse scan)."""
    if not lexicon:
        return "none"
    splits = hashlib.sha1(json.dumps(lexicon.splits, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    return f"{lexicon.version}:{len(lexicon.forms)}:{len(lexicon.deprecated)}:{splits}"
