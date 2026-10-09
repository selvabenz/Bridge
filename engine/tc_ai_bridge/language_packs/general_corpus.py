"""A pack's general-corpus lexicon: how often each word form occurs in general
written use of the language, outside the Bible (fork issue #246).

Built at development time by scripts/build_corpus_lexicon.py from sources
compatible with Bridge's licence (AI4Bharat IndicCorp v2, the Kaniyam Tamil
counts, the Hindi hunspell list), shipped as pack data beside the IRV
snapshot: `general_corpus.tsv.gz` (key, count, source letters) and its
manifest `general_corpus.json`. Loaded once per process on the first pass
that needs it, never at startup, and never written.

Language QA consults it after the vendored checker, in Bridge's own code
(indic_qa_adapter._items, indic_qa_tamil._token_item), on two token exits:

- `unknown`: not in the OV and rare in the IRV. If the corpus knows the word
  (count >= acceptMin) and no corpus neighbour is `ratio` times commoner, the
  word is accepted and counted in the pass's coverage note. A word the corpus
  knows but whose one-edit neighbour dwarfs it is a popular web typo, and
  stays a finding. Either way, corpus near-misses are offered as suggestions,
  after the reviewer's, learned and OV ones.
- house practice: the IRV uses the word `irvAcceptMin` times or more, so the
  checker accepted it without looking. If neither the OV nor the corpus knows
  it and a corpus word is one edit away, that is an IRV-wide consistent slip:
  `<code>.lex.irv-consistent-slip`, one finding per book, for the reviewer.

The checker's own near-miss and compound branches are untouched, so a word it
already explains keeps its rule and its suggestions.
"""
from __future__ import annotations

import gzip
import json
import threading
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import indic_qa_vendor
from .lexicon import deletion_keys

PACK_KEY = "generalCorpus"
FORMAT = 1
FILE_NAME, MANIFEST_NAME = "general_corpus.tsv.gz", "general_corpus.json"
RULE_TAIL = "lex.irv-consistent-slip"
LABEL = "Used throughout the IRV, but unknown to the OV and to the general corpus, and one edit from a corpus word"
# Thresholds a manifest may override (the build records what it used).
ACCEPT_MIN, SUGGEST_MIN, SUGGEST_TOP, RATIO = 10, 200, 15_000, 50
MAX_NEAR = 3
# Set by scripts/measure_indic_qa.py --without-corpus: the native-parity
# comparison must see the checker alone.
DISABLED = False


def rule_id(code: str) -> str:
    """The slip rule's id for a profile pack (`hi.lex.irv-consistent-slip`)
    or the Tamil layer (`indicqa.lex.irv-consistent-slip`)."""
    return f"{'indicqa' if code in indic_qa_vendor.LAYER_PROFILES else code}.{RULE_TAIL}"


# code -> catalogue entry in the profile's shape: (group, label, inline upstream, on by default).
RULES: dict[str, dict[str, tuple[str, str, bool, bool]]] = {
    code: {rule_id(code): ("Lexicon", LABEL, False, True)}
    for code in indic_qa_vendor.PROFILES + indic_qa_vendor.LAYER_PROFILES
}
DEFAULT_ENTRY = {"category": "typo", "layer": "lexicon", "severity": "low", "confidence": "medium",
                 "note": "IRV-wide form absent from the general corpus; one edit from a corpus word"}


def key_function(code: str) -> Callable[[str], str]:
    """How a word becomes a lookup key: NFC, then the profile's `canon` where
    the profile looks words up by one (hi, ml, or). The build script uses this
    same function, so the shipped keys and the runtime's agree."""
    canon = getattr(indic_qa_vendor.profile(code), "canon", None)
    if canon is None:
        return lambda word: unicodedata.normalize("NFC", word)
    return lambda word: canon(unicodedata.normalize("NFC", word))


@dataclass
class Verdict:
    kind: str                                  # "accept" | "augment" | "slip" | "none"
    suggestions: list[dict[str, Any]] = field(default_factory=list)
    note: str = ""


@dataclass
class GeneralCorpus:
    counts: dict[str, int]
    key: Callable[[str], str]
    manifest: dict[str, Any]
    accept_min: int = ACCEPT_MIN
    suggest_min: int = SUGGEST_MIN
    suggest_top: int = SUGGEST_TOP
    ratio: int = RATIO
    _index: Any = None
    _near: dict[str, list[tuple[str, int]]] = field(default_factory=dict)

    def count(self, word: str) -> int:
        return self.counts.get(self.key(word), 0)

    def known(self, word: str) -> bool:
        return self.count(word) >= self.accept_min

    def _suggest_index(self) -> dict[str, list[str]]:
        """A one-deletion index over the words common enough to suggest, built
        on first use: the top `suggest_top` by count, each >= `suggest_min`.
        Deletions are by grapheme cluster, as the ta-irv lexicon's are: a
        dropped final consonant takes its virama with it, and a vowel sign
        changes its cluster, so each is one edit, not two code points."""
        if self._index is None:
            ranked = sorted((w for w, n in self.counts.items() if n >= self.suggest_min),
                            key=lambda w: (-self.counts[w], w))[:self.suggest_top]
            index: dict[str, list[str]] = {}
            for word in ranked:
                for key in (word, *deletion_keys(word)):
                    index.setdefault(key, []).append(word)
            self._index = index
        return self._index

    def candidates(self, word: str) -> set[str]:
        """Suggestible words within one cluster edit of `word` (one
        substitution, insertion or deletion), itself excluded."""
        index = self._suggest_index()
        found: set[str] = set()
        for key in (word, *deletion_keys(word)):
            found.update(index.get(key, ()))
        found.discard(word)
        return found

    def near_miss(self, word: str) -> list[tuple[str, int]]:
        """Suggestible corpus words one edit from `word` that are at least
        `ratio` times commoner than it, commonest first, at most MAX_NEAR."""
        key = self.key(word)
        if key not in self._near:
            own = max(1, self.counts.get(key, 0))
            found = [(c, self.counts[c]) for c in self.candidates(key)
                     if self.counts.get(c, 0) >= max(self.suggest_min, self.ratio * own)]
            found.sort(key=lambda pair: (-pair[1], pair[0]))
            self._near[key] = found[:MAX_NEAR]
        return self._near[key]

    def fingerprint(self) -> str:
        return f"{str(self.manifest.get('outputSha256') or '')[:12]}/{self.accept_min}/{self.suggest_min}/{self.ratio}"


def read_rows(path: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("#"):
                continue
            word, n, _src = line.rstrip("\n").split("\t")
            counts[word] = int(n)
    return counts


def load(path: Path, manifest_path: Path, key: Callable[[str], str]) -> GeneralCorpus | None:
    if not path.is_file() or not manifest_path.is_file():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if int(manifest.get("format") or 0) != FORMAT:
        return None
    return GeneralCorpus(counts=read_rows(path), key=key, manifest=manifest,
                         accept_min=int(manifest.get("acceptMin") or ACCEPT_MIN),
                         suggest_min=int(manifest.get("suggestMin") or SUGGEST_MIN),
                         suggest_top=int(manifest.get("suggestTop") or SUGGEST_TOP),
                         ratio=int(manifest.get("ratio") or RATIO))


_LOADED: dict[Path, GeneralCorpus | None] = {}
_LOCK = threading.Lock()


def load_file(path: Path, manifest_path: Path, key: Callable[[str], str]) -> GeneralCorpus | None:
    """A pack's corpus, loaded once per process (serialised, as the pack is)."""
    path = Path(path)
    if path not in _LOADED:
        with _LOCK:
            if path not in _LOADED:
                _LOADED[path] = load(path, manifest_path, key)
    return _LOADED[path]


def for_pack(meta: dict[str, Any], directory: Path | None, code: str) -> GeneralCorpus | None:
    """The pack's corpus from pack.json `generalCorpus`, or None when the pack
    has none, has it switched off, or the measure script disabled it."""
    config = meta.get(PACK_KEY)
    if DISABLED or not config or directory is None or not config.get("enabled", True):
        return None
    return load_file(directory / str(config.get("file") or FILE_NAME),
                     directory / str(config.get("manifest") or MANIFEST_NAME), key_function(code))


def _suggestion(word: str, count: int) -> dict[str, Any]:
    return {"text": word, "rank": 1, "source": "corpus", "rationale": f"{count}× in the general corpus",
            "kind": "corpus", "freq": int(count)}


def verdict(corpus: GeneralCorpus, token: dict[str, Any], *, irv_accept_min: int) -> Verdict:
    """What the corpus says about one checker token (its `to_json` dict).

    `unknown` -> accept (known, no dominant neighbour) or augment (the finding
    stays; corpus suggestions follow the checker's). A word the IRV uses
    `irv_accept_min` times or more (status `irv_ok`, or `unknown` under the
    Tamil layer, which has no irv_ok) that the corpus does not know, with a
    corpus neighbour -> slip. Anything else -> none."""
    status, word = token.get("status"), str(token.get("t") or "")
    if not word or token.get("ignored_once"):
        return Verdict("none")
    house_practice = status == "irv_ok" or (status == "unknown" and int(token.get("irv") or 0) >= irv_accept_min)
    if house_practice:
        if corpus.known(word) or int(token.get("ov") or 0) > 0:
            return Verdict("none")
        near = corpus.near_miss(word)
        if not near:
            return Verdict("none")
        best, n = near[0]
        return Verdict("slip", [_suggestion(w, c) for w, c in near],
                       f"IRV {token.get('irv', 0)}×; not in the OV nor the general corpus; "
                       f"nearest corpus word {best} {n}×")
    if status != "unknown":
        return Verdict("none")
    near = corpus.near_miss(word)
    count = corpus.count(word)
    if corpus.known(word) and not near:
        return Verdict("accept", [], f"{count}× in the general corpus")
    if not near:
        return Verdict("none")
    kind = "augment"
    note = (f"{count}× in the general corpus, but {near[0][0]} {near[0][1]}×" if count
            else f"nearest corpus word {near[0][0]} {near[0][1]}×")
    return Verdict(kind, [_suggestion(w, c) for w, c in near], note)


def append_suggestions(existing: list[dict[str, Any]], extra: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The checker's suggestions first (reviewed, learned, OV), then the corpus
    ones it did not already offer."""
    seen = {s.get("text") for s in existing}
    return existing + [s for s in extra if s["text"] not in seen]
