"""The `confusion-set` kind: a weighted edit distance over grapheme clusters.

Inserting, deleting or substituting a cluster (`regex` `\\X`) costs `default`
(1.0), except for the confusions a pack's `confusion.json` lists, which cost
what the entry says. The entry types are script-neutral:

- `base`: two clusters whose base letters are in one set and whose marks are
  identical (Tamil ர/ற with the same vowel sign; Malayalam ര/റ);
- `independent`: two bare independent vowels in one set (எ/ஏ);
- `sign`: the same base letter with one vowel sign each, the two signs in one
  set (கெ/கே);
- `toggle`: the same base letter with and without one mark (க/க், க/கா);
- `split`: one cluster against two (ஐ/அய்), in either direction;
- `equivalent`: two spellings that are the same word (cost 0). Folded before
  a lookup, never proposed as a correction.

Where several entries match, the lowest cost wins. Comparison keys are NFC;
the text itself is never normalised or rewritten. A confusion set is not a
model of a language's spelling, and a distance below a threshold is a reason
to suggest, not a verdict.
"""
from __future__ import annotations

import unicodedata
from typing import Any

import regex

CLUSTER = regex.compile(r"\X")
ENTRY_TYPES = ("base", "independent", "sign", "toggle", "split", "equivalent")


def clusters(text: str) -> list[str]:
    return CLUSTER.findall(unicodedata.normalize("NFC", text))


class ConfusionSetError(ValueError):
    pass


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


class ConfusionSet:
    """A pack's confusion table, compiled. `distance(a, b)` is the weighted
    edit distance between two words."""

    def __init__(self, table: dict[str, Any], *, where: str = "confusion.json") -> None:
        if not isinstance(table, dict) or not isinstance(table.get("entries"), list):
            raise ConfusionSetError(f"{where}: expected {{\"entries\": [...]}}")
        self.default = float(table.get("default", 1.0))
        self.version = str(table.get("version", ""))
        self._base: list[tuple[frozenset, float]] = []
        self._independent: list[tuple[frozenset, float]] = []
        self._sign: list[tuple[frozenset, float]] = []
        self._toggle: dict[str, float] = {}
        self._splits: dict[str, tuple[tuple[str, str], float]] = {}
        self._equivalent: list[tuple[str, str]] = []
        for index, entry in enumerate(table["entries"]):
            spot = f"{where}: entries[{index}]"
            kind = entry.get("type") if isinstance(entry, dict) else None
            if kind not in ENTRY_TYPES:
                raise ConfusionSetError(f"{spot}: type must be one of {ENTRY_TYPES}")
            cost = float(entry.get("cost", 0.0 if kind == "equivalent" else self.default))
            if kind in {"base", "independent", "sign"}:
                members = entry.get("set")
                if not isinstance(members, list) or len(members) < 2 or not all(isinstance(m, str) and m for m in members):
                    raise ConfusionSetError(f"{spot}: set must list at least two characters")
                {"base": self._base, "independent": self._independent, "sign": self._sign}[kind].append(
                    (frozenset(_nfc(m) for m in members), cost))
            elif kind == "toggle":
                mark = entry.get("mark")
                if not isinstance(mark, str) or not mark:
                    raise ConfusionSetError(f"{spot}: toggle needs a mark")
                self._toggle[_nfc(mark)] = min(cost, self._toggle.get(_nfc(mark), cost))
            elif kind == "split":
                one, two = entry.get("one"), entry.get("two")
                if not isinstance(one, str) or not isinstance(two, list) or len(two) != 2:
                    raise ConfusionSetError(f"{spot}: split needs one and a two-cluster list")
                self._splits[_nfc(one)] = ((_nfc(two[0]), _nfc(two[1])), cost)
            else:
                pair = entry.get("pair")
                if not isinstance(pair, list) or len(pair) != 2 or not all(isinstance(p, str) and p for p in pair):
                    raise ConfusionSetError(f"{spot}: equivalent needs a pair of two strings")
                self._equivalent.append((_nfc(pair[0]), _nfc(pair[1])))
        self._costs: dict[tuple[str, str], float] = {}

    @staticmethod
    def _grouped(groups: list[tuple[frozenset, float]], a: str, b: str) -> float | None:
        found = [cost for members, cost in groups if a in members and b in members]
        return min(found) if found else None

    def substitution_cost(self, a: str, b: str) -> float:
        if a == b:
            return 0.0
        key = (a, b)
        cached = self._costs.get(key)
        if cached is not None:
            return cached
        base_a, marks_a = a[:1], a[1:]
        base_b, marks_b = b[:1], b[1:]
        costs = [self.default]
        if not marks_a and not marks_b:
            found = self._grouped(self._independent, base_a, base_b)
            if found is not None:
                costs.append(found)
        if marks_a == marks_b:
            found = self._grouped(self._base, base_a, base_b)
            if found is not None:
                costs.append(found)
        if base_a == base_b:
            toggled = {marks_a, marks_b} - {""}
            if "" in {marks_a, marks_b} and len(toggled) == 1:
                mark = next(iter(toggled))
                if mark in self._toggle:
                    costs.append(self._toggle[mark])
            if len(marks_a) == len(marks_b) == 1:
                found = self._grouped(self._sign, marks_a, marks_b)
                if found is not None:
                    costs.append(found)
        cost = min(costs)
        if len(self._costs) < 65536:
            self._costs[key] = cost
        return cost

    def fold(self, word: str) -> str:
        """`word` with every `equivalent` spelling rewritten to the first of
        its pair: the lookup key, never a suggestion."""
        folded = _nfc(word)
        for canonical, variant in self._equivalent:
            folded = folded.replace(variant, canonical)
        return folded

    def distance(self, a: str, b: str) -> float:
        """Weighted edit distance between two words (see the module docstring)."""
        x, y = clusters(a), clusters(b)
        n, m = len(x), len(y)
        previous = [float(j) * self.default for j in range(m + 1)]
        rows = [previous]
        for i in range(1, n + 1):
            current = [float(i) * self.default] + [0.0] * m
            for j in range(1, m + 1):
                best = min(previous[j] + self.default, current[j - 1] + self.default,
                           previous[j - 1] + self.substitution_cost(x[i - 1], y[j - 1]))
                split = self._splits.get(x[i - 1])
                if j >= 2 and split and split[0] == (y[j - 2], y[j - 1]):
                    best = min(best, previous[j - 2] + split[1])
                split = self._splits.get(y[j - 1])
                if i >= 2 and split and split[0] == (x[i - 2], x[i - 1]):
                    best = min(best, rows[i - 2][j - 1] + split[1])
                current[j] = best
            rows.append(current)
            previous = current
        return previous[m]
