"""Language-neutral rule kinds, driven by tables in a language profile.

* `sign_sequence`   shape rules over a word: each rule is a regex over the word's class string
                    (one class letter per code point) or over the code points themselves.
* `confusion_distance`  weighted optimal-string-alignment distance with per-pair costs, over
                    "units" (a profile may glue two code points into one unit, e.g. Gurmukhi ੍ਹ).
* `consistency_clusters`  group word forms by a skeleton function and report minority forms.
* `strip_suffix` / `split_compound`  a known word plus an ending (undoing the language's junction
                    rules), or two known words run together (see langs/ml.py for their use).
* `pair_agreement`  statistics of a word given its neighbour (see langs/pa.py for its use).

Nothing here knows a script; Tamil does not use this module.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Iterable, Optional


# --------------------------------------------------------------------------------------
# sign_sequence
# --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ShapeRule:
    id: str
    severity: str                  # "error" | "warning"
    on: str                        # "cls": pattern runs over the class string; "cp": over the word
    pattern: re.Pattern
    fix: Optional[Callable] = None  # (word, start, end, classes) -> replacement for word[start:end], or None
    gated: bool = False            # only a fault when the word is not in the lexicon


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str
    start: int
    end: int
    fix: Optional[str]             # replacement for word[start:end], None when there is no safe fix
    gated: bool

    def fixed(self, word: str) -> Optional[str]:
        return None if self.fix is None else word[:self.start] + self.fix + word[self.end:]


def class_string(word: str, cls: Callable[[str], str]) -> str:
    return "".join(cls(ch) for ch in word)


def sign_sequence(word: str, cls: Callable[[str], str], rules: Iterable[ShapeRule]) -> list[Finding]:
    """All findings, in rule order; a finding that overlaps an earlier (higher-priority) one is dropped."""
    classes = class_string(word, cls)
    out: list[Finding] = []
    taken: list[tuple[int, int]] = []
    for r in rules:
        subject = classes if r.on == "cls" else word
        for m in r.pattern.finditer(subject):
            s, e = m.start(), max(m.end(), m.start() + 1)
            if any(s < b and a < e for a, b in taken):
                continue
            fix = r.fix(word, s, e, classes) if r.fix else None
            out.append(Finding(r.id, r.severity, s, e, fix, r.gated))
            taken.append((s, e))
    return out


# --------------------------------------------------------------------------------------
# confusion_distance
# --------------------------------------------------------------------------------------

@dataclass
class ConfusionCosts:
    units: re.Pattern                          # splits a word into units (findall)
    sub: dict                                  # frozenset({u1, u2}) -> (cost, class)
    indel: dict                                # unit -> (cost, class)
    sub_final: dict                            # like sub, used instead of it when both units are the last unit
    transpose: tuple = (1.0, "transpose")
    indel_final: dict = None                   # like indel, used instead of it for the last unit of the longer word
    default_sub: tuple = (1.0, "other")
    default_indel: tuple = (1.0, "other")


def units(word: str, costs: ConfusionCosts) -> list[str]:
    return costs.units.findall(word)


def confusion_distance(a: str, b: str, costs: ConfusionCosts, limit: float = 9.0) -> tuple[float, list[tuple]]:
    """(cost, ops) of the cheapest alignment of a to b.  ops: (cost, class, op, from, to, unit_index_in_a),
    most expensive first.  Stops early (returns (inf, [])) when every path exceeds `limit`."""
    x, y = units(a, costs), units(b, costs)
    n, m = len(x), len(y)
    INF = float("inf")
    d = [[INF] * (m + 1) for _ in range(n + 1)]
    bt: list[list[Optional[tuple]]] = [[None] * (m + 1) for _ in range(n + 1)]
    d[0][0] = 0.0

    final_indel = costs.indel_final or {}

    def indel(u, last=False):
        if last and u in final_indel:
            return final_indel[u]
        return costs.indel.get(u, costs.default_indel)

    for i in range(n + 1):
        row_min = INF
        for j in range(m + 1):
            if i == 0 and j == 0:
                row_min = 0.0
                continue
            best, how = INF, None
            if i > 0:
                c, k = indel(x[i - 1], i == n and j == m)
                if d[i - 1][j] + c < best:
                    best, how = d[i - 1][j] + c, (c, k, "del", x[i - 1], "", i - 1, i - 1, j)
            if j > 0:
                c, k = indel(y[j - 1], i == n and j == m)
                if d[i][j - 1] + c < best:
                    best, how = d[i][j - 1] + c, (c, k, "ins", "", y[j - 1], i, i, j - 1)
            if i > 0 and j > 0:
                if x[i - 1] == y[j - 1]:
                    if d[i - 1][j - 1] < best:
                        best, how = d[i - 1][j - 1], ("=", i - 1, j - 1)
                else:
                    pair = frozenset((x[i - 1], y[j - 1]))
                    c, k = costs.sub.get(pair, costs.default_sub)
                    if i == n and j == m and pair in costs.sub_final:
                        c, k = costs.sub_final[pair]
                    if d[i - 1][j - 1] + c < best:
                        best, how = d[i - 1][j - 1] + c, (c, k, "sub", x[i - 1], y[j - 1], i - 1, i - 1, j - 1)
                if i > 1 and j > 1 and x[i - 1] == y[j - 2] and x[i - 2] == y[j - 1] and x[i - 1] != x[i - 2]:
                    c, k = costs.transpose
                    if d[i - 2][j - 2] + c < best:
                        best, how = d[i - 2][j - 2] + c, (c, k, "transpose", x[i - 2] + x[i - 1], y[j - 2] + y[j - 1], i - 2, i - 2, j - 2)
            d[i][j] = best
            bt[i][j] = how
            row_min = min(row_min, best)
        if row_min > limit:
            return INF, []
    if d[n][m] > limit:
        return INF, []
    ops = []
    i, j = n, m
    while i > 0 or j > 0:
        how = bt[i][j]
        if how is None:
            break
        if how[0] == "=":
            i, j = how[1], how[2]
            continue
        c, k, op, frm, to, at, pi, pj = how
        ops.append((c, k, op, frm, to, at))
        i, j = pi, pj
    ops.sort(key=lambda o: (-o[0], o[5]))
    return d[n][m], ops


# --------------------------------------------------------------------------------------
# consistency_clusters
# --------------------------------------------------------------------------------------

def consistency_clusters(counts: dict[str, int], skeleton: Callable[[str], str]) -> dict[str, dict[str, int]]:
    """skeleton -> {form: count} for every skeleton that has at least two forms."""
    groups: dict[str, dict[str, int]] = defaultdict(dict)
    for w, n in counts.items():
        groups[skeleton(w)][w] = n
    return {k: v for k, v in groups.items() if len(v) > 1}


# --------------------------------------------------------------------------------------
# strip_suffix / split_compound: a known word plus an ending, two known words run together
# --------------------------------------------------------------------------------------

def strip_suffix(word: str, suffixes: Iterable[str], stem_bases: Callable[[str, str], Iterable[str]],
                 known: Callable[[str], bool], min_stem: int = 3) -> Optional[tuple[str, str]]:
    """(base, suffix) for the first suffix (in the given order, normally longest first) that `word` ends
    with and whose stem, undone with the language's junction rules by `stem_bases(stem, suffix)`, is a
    known base form; None when there is none.  The stem must keep at least `min_stem` code points."""
    for suf in suffixes:
        if not suf or not word.endswith(suf) or len(word) - len(suf) < min_stem:
            continue
        stem = word[:-len(suf)]
        for base in stem_bases(stem, suf):
            if known(base):
                return base, suf
    return None


def split_compound(word: str, head_ok: Callable[[str], bool], tail_ok: Callable[[str], bool],
                   start_ok: Callable[[str], bool], min_first: int = 3, min_second: int = 3,
                   stop: Iterable[str] = ()) -> Optional[tuple[str, str]]:
    """(head, tail) at the first split (shortest head) where the head and the tail are both acceptable,
    the tail starts where the script allows a word to start, and the tail is not a bare ending."""
    stop = set(stop)
    n = len(word)
    for i in range(min_first, n - min_second + 1):
        a, b = word[:i], word[i:]
        if b in stop or not start_ok(b):
            continue
        if head_ok(a) and tail_ok(b):
            return a, b
    return None


# --------------------------------------------------------------------------------------
# pair_agreement
# --------------------------------------------------------------------------------------

def adjacent_pairs(text: str, token_re: re.Pattern) -> Iterable[tuple[str, str, int, int]]:
    """(w1, w2, start1, end2) for consecutive tokens separated only by whitespace."""
    prev = None
    for m in token_re.finditer(text):
        if prev is not None and text[prev.end():m.start()].isspace():
            yield prev.group(), m.group(), prev.start(), m.end()
        prev = m


# --------------------------------------------------------------------------------------
# join_split_clusters / bigram_rules (first used by langs/hi.py)
# --------------------------------------------------------------------------------------

def join_split_clusters(pairs: Iterable[tuple[str, str]], joined: dict[str, int],
                        split: dict[tuple[str, str], int]) -> dict[tuple[str, str], tuple[int, int]]:
    """(a, b) -> (one-token count of a+b, two-token count of "a b") for every pair written both ways at least
    once (Hindi उसने / उस ने).  The caller decides which form is the minority."""
    out = {}
    for a, b in pairs:
        j, s = joined.get(a + b, 0), split.get((a, b), 0)
        if j and s:
            out[(a, b)] = (j, s)
    return out


@dataclass(frozen=True)
class BigramRule:
    """Two adjacent words: when the left word is in `left` and the right word in `right`, the word at `fix`
    ("left" | "right") should be `to` (a string, or a dict keyed by that word)."""
    id: str
    left: frozenset
    right: frozenset
    fix: str
    to: object
    why: str = ""
    stop_after: frozenset = frozenset()     # no lead when the word after the pair is one of these

    def proposed(self, w1: str, w2: str) -> Optional[str]:
        w = w1 if self.fix == "left" else w2
        p = self.to.get(w) if isinstance(self.to, dict) else self.to
        return None if p is None or p == w else p


def bigram_rules(w1: str, w2: str, table: Iterable[BigramRule], w3: str = "") -> list[tuple[BigramRule, str]]:
    """(rule, proposed word) for every rule of `table` that the adjacent pair w1 w2 breaks."""
    out = []
    for r in table:
        if w1 in r.left and w2 in r.right and w3 not in r.stop_after:
            p = r.proposed(w1, w2)
            if p:
                out.append((r, p))
    return out
