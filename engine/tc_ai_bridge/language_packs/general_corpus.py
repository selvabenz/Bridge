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
from .indic.confusion import clusters
from .lexicon import deletion_keys

PACK_KEY = "generalCorpus"
FORMAT = 2
READABLE_FORMATS = (1, 2)
FILE_NAME, MANIFEST_NAME = "general_corpus.tsv.gz", "general_corpus.json"
RULE_TAIL = "lex.irv-consistent-slip"
LABEL = "Used throughout the IRV, but unknown to the OV and to the general corpus, and one edit from a corpus word"
# Thresholds a manifest may override (the build records what it used).
ACCEPT_MIN, SUGGEST_MIN, SUGGEST_TOP, RATIO = 10, 200, 15_000, 1
MAX_NEAR = 3
# Set by scripts/measure_indic_qa.py --without-corpus: the native-parity
# comparison must see the checker alone.
DISABLED = False


def rule_id(code: str) -> str:
    """The slip rule's id for a profile pack (`hi.lex.irv-consistent-slip`)
    or the Tamil layer (`indicqa.lex.irv-consistent-slip`)."""
    return f"{'indicqa' if code in indic_qa_vendor.LAYER_PROFILES else code}.{RULE_TAIL}"


# code -> catalogue entry in the profile's shape: (group, label, inline upstream, on by default).
# Off by default: on GEN the rule's hits were mostly Bible names (hi: गमोरा;
# pa: 9 of 13 names) with one reviewer-confirmed misspelling among ml's 8, so
# it waits for the owner's spot check (BUILD_LOG 2026-10-09). The Checker
# settings dialog switches it on per project like any indic-qa rule.
RULES: dict[str, dict[str, tuple[str, str, bool, bool]]] = {
    code: {rule_id(code): ("Lexicon", LABEL, False, False)}
    for code in indic_qa_vendor.PROFILES + indic_qa_vendor.LAYER_PROFILES
}
DEFAULT_ENTRY = {"category": "typo", "layer": "lexicon", "severity": "low", "confidence": "medium",
                 "enabled": False,
                 "note": "IRV-wide form absent from the general corpus; one edit from a corpus word. Off until "
                         "the owner's spot check: on GEN its hits were mostly Bible names"}


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
    # key -> the commonest surface spelling, where it differs from the key
    # (Malayalam's canon folds a final ു to ്: a key is not a spelling to offer).
    forms: dict[str, str] = field(default_factory=dict)
    _index: Any = None
    _alphabet: str | None = None
    _near: dict[str, list[tuple[str, int]]] = field(default_factory=dict)

    def count(self, word: str) -> int:
        return self.counts.get(self.key(word), 0)

    def known(self, word: str) -> bool:
        return self.count(word) >= self.accept_min

    def form(self, key: str) -> str:
        """The spelling to offer for a key."""
        return self.forms.get(key, key)

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

    def _edits(self, key: str) -> set[str]:
        """Every string one code point away from `key`: a deletion, an
        adjacent swap, a substitution or an insertion from the corpus's own
        letters and signs. Looked up in the whole corpus, not an index, so a
        rare correct form still counts: कवियत्री (36x) is one swap from
        कवयित्री (137x), सन्यासी one inserted anusvara from संन्यासी."""
        if self._alphabet is None:
            self._alphabet = "".join(sorted({ch for word in self.counts for ch in word}))
        out: set[str] = set()
        for i in range(len(key)):
            out.add(key[:i] + key[i + 1:])
            if i + 1 < len(key):
                out.add(key[:i] + key[i + 1] + key[i] + key[i + 2:])
            for ch in self._alphabet:
                out.add(key[:i] + ch + key[i + 1:])
        for i in range(len(key) + 1):
            for ch in self._alphabet:
                out.add(key[:i] + ch + key[i:])
        out.discard(key)
        return out

    def neighbours(self, word: str) -> list[tuple[str, int, int]]:
        """Corpus keys one edit from `word` as (key, count, tier), closest
        edit first, then commonest: tier 0 a one-code-point edit (a sign
        swapped, dropped or added); tier 1 a whole grapheme cluster added or
        dropped (தண்ணீ / தண்ணீர்); tier 2 one cluster replaced by another,
        which can swap a conjunct for an unrelated syllable (सन्यासी / एससी)
        and so only ever suggests, never vetoes."""
        key = self.key(word)
        if key not in self._near:
            close = {v for v in self._edits(key) if v in self.counts}
            size = len(clusters(key))
            found = [(c, self.counts[c], 0) for c in close]
            for c in self.candidates(key) - close - {key}:
                found.append((c, self.counts[c], 1 if len(clusters(c)) != size else 2))
            found.sort(key=lambda item: (item[2], -item[1], item[0]))
            self._near[key] = found
        return self._near[key]

    def dominant(self, word: str) -> list[tuple[str, int]]:
        """Neighbours that make `word` look like their misspelling: one sign or
        one cluster away (tiers 0-1), known, at least `ratio` times as common,
        and commoner. A web corpus carries popular misspellings, so with ratio
        1 a word is accepted only when nothing that close to it is commoner
        (#246 review: कवियत्री 36x beside कवयित्री 137x)."""
        own = self.count(word)
        return [(c, n) for c, n, tier in self.neighbours(word)
                if tier < 2 and n >= self.accept_min and n >= self.ratio * max(1, own) and n > own][:MAX_NEAR]

    def near_miss(self, word: str) -> list[tuple[str, int]]:
        """Suggestions for a word the corpus does not know: neighbours common
        enough to suggest (`suggest_min`), closest edit first, at most MAX_NEAR."""
        return [(c, n) for c, n, _tier in self.neighbours(word) if n >= self.suggest_min][:MAX_NEAR]

    def fingerprint(self) -> str:
        return f"{str(self.manifest.get('outputSha256') or '')[:12]}/{self.accept_min}/{self.suggest_min}/{self.ratio}"


def read_rows(path: Path) -> tuple[dict[str, int], dict[str, str]]:
    """(key -> count, key -> surface form where it differs). One decompress
    and one split, not a line iterator: the 300k-row Hindi file loads in
    about a third of the time that way. Format 1 rows have three columns;
    format 2 adds the commonest surface spelling."""
    counts: dict[str, int] = {}
    forms: dict[str, str] = {}
    text = gzip.decompress(path.read_bytes()).decode("utf-8")
    for line in text.split("\n"):
        if not line or line[0] == "#":
            continue
        parts = line.split("\t")
        counts[parts[0]] = int(parts[1])
        if len(parts) > 3 and parts[3] and parts[3] != parts[0]:
            forms[parts[0]] = parts[3]
    return counts, forms


def load(path: Path, manifest_path: Path, key: Callable[[str], str]) -> GeneralCorpus | None:
    if not path.is_file() or not manifest_path.is_file():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if int(manifest.get("format") or 0) not in READABLE_FORMATS:
        return None
    counts, forms = read_rows(path)
    return GeneralCorpus(counts=counts, key=key, manifest=manifest, forms=forms,
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
        return Verdict("slip", [_suggestion(corpus.form(w), c) for w, c in near],
                       f"IRV {token.get('irv', 0)}×; not in the OV nor the general corpus; "
                       f"nearest corpus word {corpus.form(best)} {n}×")
    if status != "unknown":
        return Verdict("none")
    count = corpus.count(word)
    if corpus.known(word):
        dominant = corpus.dominant(word)
        if not dominant:
            return Verdict("accept", [], f"{count}× in the general corpus")
        best, n = dominant[0]
        return Verdict("augment", [_suggestion(corpus.form(w), c) for w, c in dominant],
                       f"{count}× in the general corpus, but {corpus.form(best)} {n}×")
    near = corpus.near_miss(word)
    if not near:
        return Verdict("none")
    best, n = near[0]
    note = (f"{count}× in the general corpus, but {corpus.form(best)} {n}×" if count
            else f"nearest corpus word {corpus.form(best)} {n}×")
    return Verdict("augment", [_suggestion(corpus.form(w), c) for w, c in near], note)


def append_suggestions(existing: list[dict[str, Any]], extra: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The checker's suggestions first (reviewed, learned, OV), then the corpus
    ones it did not already offer."""
    seen = {s.get("text") for s in existing}
    return existing + [s for s in extra if s["text"] not in seen]
