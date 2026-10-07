"""Related words from the two Bible texts themselves: indic-qa's related-word
suggestions, ported from `qa_app/related.py` (selvabenz/indic-qa at ab53636,
MIT) into Bridge's own code rather than vendored, because upstream walks
indic-qa's `usfm_doc.Book` objects and its checker's private counts, which
Bridge only builds for the checker itself.

What it does is unchanged from upstream:

- **Equivalents.** The IRV is a revision of the OV, so aligning the words of
  every verse the two share (difflib.SequenceMatcher, no autojunk) shows which
  word the other text used in the same slot. Only `replace` runs of equal
  length on both sides, at most MAX_SPAN words, are paired word by word, and a
  pair must recur MIN_PAIR times. Archaic vs modern forms, near-synonyms and
  inflections come out (பார்த்தான் / கண்டான்).
- **Same stem.** Words of either text sharing the word's first
  max(3, len - 3) characters, by frequency.

Tokens are Language QA's own (`language_qa.word_occurrences`), so a "word"
here is a word everywhere else in Bridge. This is corpus evidence, not a
thesaurus: every candidate carries its counts and a sample reference.
"""
from __future__ import annotations

import bisect
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from typing import Any, Callable, Iterable

MIN_PAIR = 2
MAX_SPAN = 3


class RelatedWords:
    def __init__(self) -> None:
        self.ov_to_irv: dict[str, Counter] = defaultdict(Counter)
        self.irv_to_ov: dict[str, Counter] = defaultdict(Counter)
        self.sample: dict[tuple[str, str], str] = {}
        self.irv_count: Counter = Counter()
        self.ov_count: Counter = Counter()
        self.vocab: list[str] = []
        self.verses_aligned = 0

    @classmethod
    def build(cls, ov: Iterable[tuple[str, list[str]]], irv: Iterable[tuple[str, list[str]]],
              *, proceed: Callable[[], bool] = lambda: True) -> "RelatedWords":
        """`ov`, `irv`: (reference, tokens) per verse; a reference is "GEN 1:1"."""
        self = cls()
        ov_verses: dict[str, list[str]] = {}
        for ref, tokens in ov:
            ov_verses[ref] = tokens
            self.ov_count.update(tokens)
        pairs: dict[tuple[str, str], int] = Counter()
        for n, (ref, irv_tokens) in enumerate(irv):
            self.irv_count.update(irv_tokens)
            ov_tokens = ov_verses.get(ref)
            if not ov_tokens or ov_tokens == irv_tokens:
                if ov_tokens:
                    self.verses_aligned += 1
                continue
            if n % 500 == 0 and not proceed():
                break
            self.verses_aligned += 1
            matcher = SequenceMatcher(None, ov_tokens, irv_tokens, autojunk=False)
            for op, a0, a1, b0, b1 in matcher.get_opcodes():
                if op != "replace" or a1 - a0 != b1 - b0 or a1 - a0 > MAX_SPAN:
                    continue
                for x, y in zip(ov_tokens[a0:a1], irv_tokens[b0:b1]):
                    if x != y:
                        pairs[(x, y)] += 1
                        self.sample.setdefault((x, y), ref)
        for (x, y), n in pairs.items():
            if n >= MIN_PAIR:
                self.ov_to_irv[x][y] = n
                self.irv_to_ov[y][x] = n
        self.vocab = sorted(set(self.irv_count) | set(self.ov_count))
        return self

    def _family(self, word: str, exclude: set[str], limit: int) -> list[dict[str, Any]]:
        if len(word) < 3:
            return []
        stem = word[:max(3, len(word) - 3)]
        start = bisect.bisect_left(self.vocab, stem)
        found = []
        for candidate in self.vocab[start:start + 4000]:
            if not candidate.startswith(stem):
                break
            if candidate != word and candidate not in exclude:
                found.append(candidate)
        found.sort(key=lambda w: (-(self.irv_count[w] + self.ov_count[w]), w))
        return [{"w": w, "irv": self.irv_count[w], "ov": self.ov_count[w]} for w in found[:limit]]

    def lookup(self, word: str, limit: int = 8) -> dict[str, Any]:
        merged: dict[str, dict[str, Any]] = {}
        for other, n in self.irv_to_ov.get(word, Counter()).items():
            merged[other] = {"w": other, "n": n, "source": "OV", "ref": self.sample.get((other, word), ""),
                             "irv": self.irv_count[other], "ov": self.ov_count[other]}
        for other, n in self.ov_to_irv.get(word, Counter()).items():
            if other not in merged or n > merged[other]["n"]:
                merged[other] = {"w": other, "n": n, "source": "IRV", "ref": self.sample.get((word, other), ""),
                                 "irv": self.irv_count[other], "ov": self.ov_count[other]}
        equivalents = sorted(merged.values(), key=lambda e: (-e["n"], e["w"]))[:limit]
        return {"word": word, "ready": True, "irv": self.irv_count[word], "ov": self.ov_count[word],
                "equivalents": equivalents,
                "family": self._family(word, {e["w"] for e in equivalents}, limit),
                "versesAligned": self.verses_aligned}
