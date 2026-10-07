"""Lexicon layers, token classification, near-miss suggestions, sandhi leads and run warnings.

Everything here is independent of FastAPI.  `Checker.check_line` turns a parsed line into the
JSON block the UI renders; `Checker.book_report` aggregates a whole book.
"""
from __future__ import annotations

import csv
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterable

from . import langs
from . import tamil_grammar as tg
from . import usfm_doc as ud
from .langs import ta as _ta

bd = ud.bd
DEFAULT_SETTINGS = _ta.DEFAULT_SETTINGS          # Tamil defaults; each language has lang.default_settings
ZERO_WIDTH_RE = re.compile("[​‌‍﻿]")
DIGIT_RE = re.compile(r"\d+")


def merge_settings(stored: dict | None, lang: "langs.Language | None" = None) -> dict:
    defaults = (lang or langs.default()).default_settings
    out = {k: (dict(v) if isinstance(v, dict) else (list(v) if isinstance(v, list) else v))
           for k, v in defaults.items()}
    for k, v in (stored or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    out["checked_contexts"] = [c for c in out["checked_contexts"] if c in ud.CHECKABLE]
    return out


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def single_token_change(old_text: str, new_text: str, lang) -> tuple[str, str] | None:
    """(A, B) when the edit of a text run replaced exactly one word A by exactly one word B and
    nothing else; None for every other edit (several words, spacing, punctuation, A == B).  The
    editor saves whole runs, so this is how a word replacement is recognised and learned."""
    if old_text == new_text:
        return None
    a = lang.tokens_with_offsets(old_text)
    b = lang.tokens_with_offsets(new_text)
    if not a or not b:
        return None
    i = 0
    while i < len(a) and i < len(b) and a[i][0] == b[i][0]:
        i += 1
    j = 0
    while j < len(a) - i and j < len(b) - i and a[-1 - j][0] == b[-1 - j][0]:
        j += 1
    if len(a) - i - j != 1 or len(b) - i - j != 1:
        return None
    (wa, sa, ea), (wb, sb, eb) = a[i], b[i]
    if wa == wb or not lang.token_re.fullmatch(wa) or not lang.token_re.fullmatch(wb):
        return None
    # everything around the two words must be the same text, so the change is the word alone
    if old_text[:sa] != new_text[:sb] or old_text[ea:] != new_text[eb:]:
        return None
    return nfc(wa), nfc(wb)


# --------------------------------------------------------------------------------------
# Lexicon
# --------------------------------------------------------------------------------------

def _read_tsv(path: Path) -> Iterable[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        yield from csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE)


def read_extra_words(path: Path, token_re: re.Pattern | None = None) -> list[str]:
    if not path.exists():
        return []
    token_re = token_re or ud.TOKEN_RE
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = nfc(line.strip())
        if s and not s.startswith("#") and token_re.fullmatch(s):
            out.append(s)
    return out


class Lexicon:
    def __init__(self, lang: "langs.Language | None" = None) -> None:
        self.lang = lang or langs.default()
        self.ov: set[str] = set()
        self.bare: set[str] = set()
        self.extra: set[str] = set()
        self.lemma_count: dict[str, int] = {}
        self.kstp_kind: dict[str, str] = {}
        self.sandhi: dict[tuple[str, str], tuple[int, int]] = {}
        self.sandhi_suffix: dict[tuple[str, str], list[int]] = {}
        self.name_final: set[str] = set()
        self.ignored: set[str] = set()
        self.corrections: dict[str, str] = {}     # reviewed misspelling -> its correction (corrections.tsv)
        self.generation = 0
        self.dict_dir: Path | None = None
        self.rules: list[tg.Rule] = list(tg.DEFAULT_RULES) if self.lang.sandhi else []
        self.rule_errors: list[str] = []
        self.rules_path: Path | None = None
        self.data: dict = {}                     # language-specific tables (lang.load_lexicon_extra)

    @classmethod
    def load(cls, dict_dir: Path, lang: "langs.Language | None" = None) -> "Lexicon":
        lex = cls(lang)
        dict_dir = Path(dict_dir)
        lex.dict_dir = dict_dir
        lex.ov = {w for w in (dict_dir / "wordlist.txt").read_text(encoding="utf-8").split("\n") if w}
        if lex.lang.sandhi:
            lex.bare = {w for w in (dict_dir / "wordlist_bare.txt").read_text(encoding="utf-8").split("\n") if w}
        for row in _read_tsv(dict_dir / "words.tsv"):
            lex.lemma_count[row["word"]] = int(row["lemma_count"])
            if row.get("kstp_kind"):
                lex.kstp_kind[row["word"]] = row["kstp_kind"]
        if lex.lang.sandhi:
            for row in _read_tsv(dict_dir / "sandhi_pairs.tsv"):
                lex.sandhi[(row["bare"], row["next_initial"])] = (int(row["with"]), int(row["without"]))
            lex.build_sandhi_suffix()
            lex.name_final = {row["word"] for row in _read_tsv(dict_dir / "final_consonant_words.tsv")}
        lex.extra = set(read_extra_words(dict_dir / "extra_words.txt", lex.lang.token_re))
        if (dict_dir / "corrections.tsv").exists():
            lex.corrections = {nfc(r["wrong"]): nfc(r["right"]) for r in _read_tsv(dict_dir / "corrections.tsv")
                               if r.get("wrong") and r.get("right")}
        lex.load_rules()
        return lex

    def load_rules(self) -> None:
        if self.dict_dir is None:
            return
        if self.lang.sandhi:
            self.rules, self.rule_errors, self.rules_path = tg.load_rules(self.dict_dir)
        if self.lang.load_lexicon_extra:
            self.lang.load_lexicon_extra(self, self.dict_dir)
        self.generation += 1

    @classmethod
    def synthetic(cls, ov: Iterable[str] = (), bare: Iterable[str] = (), lemma_count: dict | None = None,
                  sandhi: dict | None = None, name_final: Iterable[str] = (), kstp_kind: dict | None = None,
                  rules: list | None = None, lang: "langs.Language | None" = None) -> "Lexicon":
        lex = cls(lang)
        lex.ov = set(ov)
        lex.bare = set(bare) or set(ov)
        lex.lemma_count = dict(lemma_count or {})
        lex.sandhi = dict(sandhi or {})
        lex.name_final = set(name_final)
        lex.kstp_kind = dict(kstp_kind or {})
        if rules is not None:
            lex.rules = list(rules)
        lex.build_sandhi_suffix()
        return lex

    SUFFIX_MAX = 6

    def build_sandhi_suffix(self) -> None:
        """Aggregate the OV sandhi counts by word ending: (suffix, class) -> [with, without, distinct forms].
        Lets a rare word inherit the habit of its ending (e.g. every -க்கு doubles before ப)."""
        agg: dict[tuple[str, str], list[int]] = {}
        for (bare, cls), (wi, wo) in self.sandhi.items():
            if not wi and not wo:
                continue
            for n in range(1, min(self.SUFFIX_MAX, len(bare) - 1) + 1):
                a = agg.setdefault((bare[-n:], cls), [0, 0, 0])
                a[0] += wi
                a[1] += wo
                a[2] += 1
        self.sandhi_suffix = agg

    def known(self, w: str) -> bool:
        return w in self.ov or w in self.extra

    def known_bare(self, w: str) -> bool:
        return w in self.bare or w in self.extra or w in self.ov

    def add_words(self, words: Iterable[str]) -> list[str]:
        added = [w for w in words if w not in self.extra]
        self.extra.update(added)
        if added:
            self.generation += 1
        return added

    def remove_word(self, word: str) -> None:
        self.extra.discard(word)
        self.generation += 1

    def set_ignored(self, words: Iterable[str]) -> None:
        self.ignored = set(words)
        self.generation += 1

    def remove_from_extra_words_file(self, words: list[str]) -> None:
        """Remove the last non-comment line equal to each word (undo of an add)."""
        if not self.dict_dir or not words:
            return
        path = self.dict_dir / "extra_words.txt"
        if not path.exists():
            return
        lines = path.read_text(encoding="utf-8").split("\n")
        for w in words:
            for i in range(len(lines) - 1, -1, -1):
                if nfc(lines[i].strip()) == w:
                    del lines[i]
                    break
        path.write_text("\n".join(lines), encoding="utf-8", newline="\n")

    def append_extra_words_file(self, words: list[str]) -> None:
        if not self.dict_dir or not words:
            return
        path = self.dict_dir / "extra_words.txt"
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        parts = []
        if existing and not existing.endswith("\n"):
            parts.append("\n")
        if "# added by qa_app" not in existing:
            parts.append("# added by qa_app (one word per line)\n")
        parts.extend(w + "\n" for w in words)
        with path.open("a", encoding="utf-8", newline="\n") as f:
            f.write("".join(parts))


# --------------------------------------------------------------------------------------
# Suggestion index (SymSpell-style 1-deletion index, compact)
# --------------------------------------------------------------------------------------

class SuggestIndex:
    def __init__(self, sources: Iterable[str] = ()) -> None:
        self.words: list[str] = []
        self.id: dict[str, int] = {}
        self.index: dict[str, int | tuple[int, ...]] = {}
        for w in sources:
            self.add(w)

    def add(self, w: str) -> None:
        if w in self.id:
            return
        i = len(self.words)
        self.words.append(w)
        self.id[w] = i
        for key in (w, *bd.deletions(w)):
            v = self.index.get(key)
            self.index[key] = i if v is None else ((v, i) if isinstance(v, int) else v + (i,))

    def candidates(self, w: str) -> set[str]:
        out: set[str] = set()
        for key in (w, *bd.deletions(w)):
            v = self.index.get(key)
            if v is None:
                continue
            if isinstance(v, int):
                out.add(self.words[v])
            else:
                out.update(self.words[i] for i in v)
        out.discard(w)
        return out


# --------------------------------------------------------------------------------------
# Checker
# --------------------------------------------------------------------------------------

@dataclass
class WordInfo:
    status: str
    rule: str = ""
    severity: str = ""
    bare: str = ""
    parts: tuple = ()
    sugg: list = field(default_factory=list)
    irv: int = 0
    ov: int = 0
    ov_lemma: int = 0
    extra: dict = field(default_factory=dict)     # language-specific fields (none for Tamil)

    def to_json(self, word: str, s: int, e: int, ignored_once: bool = False) -> dict:
        d = {"t": word, "s": s, "e": e, "status": self.status, "irv": self.irv, "ov": self.ov, "ov_lemma": self.ov_lemma,
             "ignored_once": ignored_once, "sugg": self.sugg}
        if self.rule:
            d["rule"] = self.rule
            d["severity"] = self.severity
        if self.parts:
            d["parts"] = list(self.parts)
        if self.bare:
            d["bare"] = self.bare
        if self.extra:
            d.update(self.extra)
        return d


def mark_ignored_once(block: dict, ignored_once: set[tuple[int, str]]) -> None:
    """Mark the leads and warnings the reviewer ignored at this one spot (words are marked as they
    are classified).  The keys are those of scope.pair_key / scope.warn_key; a block with nothing
    ignored is left exactly as it was."""
    for seg in block["segs"]:
        if seg.get("k") != "t":
            continue
        for lead in seg.get("sandhi", ()):
            if (seg["s"] + lead["s"], f"pair:{lead.get('w1') or ''} {lead.get('w2') or ''}") in ignored_once:
                lead["ignored"] = lead["ignored_once"] = True
        for w in seg.get("warnings", ()):
            if (seg["s"] + w["s"], f"warn:{w['kind']}") in ignored_once:
                w["ignored"] = w["ignored_once"] = True


class Checker:
    def __init__(self, lex: Lexicon, settings: dict | None = None, lang: "langs.Language | None" = None) -> None:
        self.lang = lang or lex.lang
        self.lex = lex
        self.settings = merge_settings(settings, self.lang)
        self.data: dict = {}                     # language-specific corpus state (lang.on_index_book)
        self.settings_gen = 0
        self.irv_count: Counter = Counter()
        self.book_count: dict[str, Counter] = {}
        self.irv_pairs: dict[tuple[str, str], list[int]] = {}     # (bare, class) -> [with, without] across the IRV
        self.book_pairs: dict[str, Counter] = {}
        self.irv_form_use: dict[str, list[int]] = {}               # ஒற்று form -> [before a matching letter, elsewhere]
        self.book_form_use: dict[str, Counter] = {}
        self._rule_cache: dict[tuple[str, str], tg.RuleHit | None] = {}
        self._rule_stats: tuple = ((), {})
        self.ignored_pairs: set[tuple[str, str]] = set()
        self.ignored: set[str] = set()           # this project's "ignore everywhere" words (the lexicon is shared)
        self.learned: dict[str, list[str]] = {}  # this project's word replacements (store.learned_fixes), old -> [new, …]
        self.index = SuggestIndex()
        self._cache: dict[str, WordInfo] = {}
        self._cache_key: tuple = ()
        self._reports: dict[str, tuple[tuple, dict]] = {}
        self.ignore_gen = 0           # bumped whenever an ignore list changes (it is part of a report's key)

    # -- configuration --------------------------------------------------------------
    def update_settings(self, patch: dict) -> None:
        self.settings = merge_settings({**self.settings, **patch}, self.lang)
        self.settings_gen += 1
        self.build_index()

    def set_ignored_pairs(self, pairs: Iterable[tuple[str, str]]) -> None:
        self.ignored_pairs = set(pairs)
        self.settings_gen += 1

    def set_ignored(self, words: Iterable[str]) -> None:
        self.ignored = set(words)
        self.settings_gen += 1

    def set_learned(self, fixes: dict[str, list[str]]) -> None:
        """The reviewer's past word replacements in this project (old -> [new, …]).  Empty for every
        regression contract, so nothing changes until a reviewer has made a change."""
        self.learned = {k: list(v) for k, v in fixes.items() if v}
        self.settings_gen += 1

    def is_ignored(self, w: str) -> bool:
        """Ignored in this project, or (single-project app) on the lexicon itself."""
        return w in self.ignored or w in self.lex.ignored

    def freq(self, w: str) -> int:
        return max(self.lex.lemma_count.get(w, 0), self.irv_count.get(w, 0))

    # -- corpus counts ----------------------------------------------------------------
    def index_book(self, code: str, book: ud.Book) -> None:
        cnt: Counter = Counter()
        checked = set(self.settings["checked_contexts"])
        for line in book.lines:
            for seg in line.segs:
                if seg.kind == "t" and seg.context in checked:
                    cnt.update(self.lang.tokens(seg.text))
        old = self.book_count.get(code)
        if old:
            self.irv_count.subtract(old)
            self.irv_count += Counter()          # drop zero/negative entries
        self.book_count[code] = cnt
        self.irv_count.update(cnt)
        if self.lang.sandhi:
            self._index_pairs(code, book, checked)
        if self.lang.on_index_book:
            self.lang.on_index_book(self, code, book, checked)

    def _index_pairs(self, code: str, book: ud.Book, checked: set) -> None:
        """Count how the IRV itself treats each (bare word, next class) pair, and where each ஒற்று form
        is used.  Refreshed per book load, not per keystroke: it only needs to tell a consistent IRV
        habit (or a name like நோத்) from a sporadic slip."""
        sd = self.lang.sandhi
        KSTP, SANDHI_FINAL_RE, ELIGIBLE_RE = sd.kstp, sd.final_re, sd.eligible_re
        pairs: Counter = Counter()
        forms: Counter = Counter()
        for line in book.lines:
            streams: dict[int, str] = {}
            for seg in line.segs:
                if seg.kind == "t" and seg.context in checked:
                    streams[seg.stream] = streams.get(seg.stream, "") + seg.text
            for text in streams.values():
                toks = self.lang.tokens_with_offsets(text)
                for (w1, _, b), nxt in zip(toks, toks[1:] + [None]):
                    adj = nxt is not None and text[b:nxt[1]].isspace()
                    cls = KSTP.get(nxt[0][0]) if adj else None
                    if SANDHI_FINAL_RE.search(w1):
                        match = cls == KSTP[w1[-2]]
                        forms[(w1, 0 if match else 1)] += 1
                        if match:
                            pairs[(w1[:-2], cls, 1)] += 1
                    elif cls and ELIGIBLE_RE.search(w1):
                        pairs[(w1, cls, 0)] += 1
        for key, n in self.book_pairs.get(code, Counter()).items():
            self.irv_pairs[key[:2]][1 - key[2]] -= n
        for (bare, cls, has), n in pairs.items():
            self.irv_pairs.setdefault((bare, cls), [0, 0])[1 - has] += n
        self.book_pairs[code] = pairs
        for (w, i), n in self.book_form_use.get(code, Counter()).items():
            self.irv_form_use[w][i] -= n
        for (w, i), n in forms.items():
            self.irv_form_use.setdefault(w, [0, 0])[i] += n
        self.book_form_use[code] = forms

    def reindex_run(self, code: str, context: str, old_text: str, new_text: str) -> None:
        """Incremental count update after one run changed."""
        if context not in self.settings["checked_contexts"]:
            return
        cnt = self.book_count.setdefault(code, Counter())
        old_t, new_t = Counter(self.lang.tokens(old_text)), Counter(self.lang.tokens(new_text))
        for w, n in old_t.items():
            cnt[w] -= n
            self.irv_count[w] -= n
            if cnt[w] <= 0:
                del cnt[w]
            if self.irv_count[w] <= 0:
                del self.irv_count[w]
        cnt.update(new_t)
        self.irv_count.update(new_t)
        self._cache.clear()

    def build_index(self) -> None:
        s = self.settings["suggest"]
        sources = {w for w, n in self.lex.lemma_count.items()
                   if n >= s["lemma_min"] and self.lex.kstp_kind.get(w) != "sandhi"}
        sources |= self.lex.extra
        sources |= {w for w, n in self.irv_count.items() if n >= s["irv_min"] and self.lex.known(w)}
        self.index = SuggestIndex(sorted(sources))
        if self.lang.on_build_index:
            self.lang.on_build_index(self)
        self._cache.clear()
        self._rule_cache.clear()

    def add_words(self, words: Iterable[str]) -> list[str]:
        added = self.lex.add_words(words)
        for w in added:
            self.index.add(w)
        self._cache.clear()
        return added

    # -- classification ---------------------------------------------------------------
    def _check_cache(self) -> None:
        key = (self.lex.generation, self.settings_gen)
        if key != self._cache_key:
            self._cache.clear()
            self._rule_cache.clear()
            self._cache_key = key

    def classify(self, w: str) -> WordInfo:
        self._check_cache()
        info = self._cache.get(w)
        if info is None:
            info = self._classify(w)
            if self.learned and w in self.learned:
                info = self._with_learned(w, info)
            self._cache[w] = info
        return info

    def learned_suggestions(self, w: str) -> list[dict]:
        return [{"w": b, "op": "learned", "cls": "learned", "freq": self.freq(b)} for b in self.learned.get(w, ())]

    def _with_learned(self, w: str, info: WordInfo) -> WordInfo:
        """A word the reviewer replaced before: a known word is marked 'learned' so the editor can
        offer the same change; a word with another status keeps it, the learned forms come first."""
        learned = self.learned_suggestions(w)
        if info.status == "ok":
            return WordInfo("learned", sugg=learned, irv=info.irv, ov=info.ov, ov_lemma=info.ov_lemma)
        seen = {d["w"] for d in learned}
        return replace(info, sugg=learned + [d for d in info.sugg if d["w"] not in seen])

    def _classify(self, w: str) -> WordInfo:
        lex = self.lex
        irv, ov = self.irv_count.get(w, 0), lex.lemma_count.get(w, 0)
        if self.lang.classify_first:
            info = self.lang.classify_first(self, w, irv, ov)
            if info is not None:
                return info
        bad = self.lang.malformed(w)
        if bad:
            name, severity = bad
            return WordInfo("malformed", rule=name, severity=severity, sugg=self.suggest(w), irv=irv, ov=ov, ov_lemma=ov)
        if self.is_ignored(w):
            return WordInfo("ignored", irv=irv, ov=ov, ov_lemma=ov)
        if w in lex.name_final or lex.known(w):
            return WordInfo("ok", irv=irv, ov=ov, ov_lemma=ov)
        if "-" in w and all(lex.known(p) for p in w.split("-") if p):
            return WordInfo("ok", irv=irv, ov=ov, ov_lemma=ov)
        sd = self.lang.sandhi
        if sd and sd.final_re.search(w) and lex.known_bare(w[:-2]):
            bare = w[:-2]
            return WordInfo("sandhi_ok", bare=bare, irv=irv, ov=ov, ov_lemma=lex.lemma_count.get(bare, 0))
        if self.lang.classify_extra:
            info = self.lang.classify_extra(self, w, irv, ov)
            if info is not None:
                return info
        parts = self.compound_split(w)
        if parts:
            return WordInfo("compound", parts=parts, sugg=self.suggest(w), irv=irv, ov=ov, ov_lemma=ov)
        return WordInfo("unknown", sugg=self.suggest(w), irv=irv, ov=ov, ov_lemma=ov)

    def compound_split(self, w: str) -> tuple:
        s = self.settings["compound"]
        if not s.get("enabled", True):
            return ()
        n = len(w)
        if n < s["min_first"] + s["min_second"]:
            return ()
        stop = set(s.get("suffix_stop", ()))
        lex = self.lex
        sd = self.lang.sandhi
        for i in range(s["min_first"], n - s["min_second"] + 1):
            a, b = w[:i], w[i:]
            if not self.lang.grapheme_start.match(b) or b in stop:
                continue
            if not (lex.known(b) and self.freq(b) >= s["min_freq"]):
                continue
            if lex.known(a) and self.freq(a) >= s["min_freq"]:
                return (a, b, "plain")
            if sd and sd.final_re.search(a) and sd.kstp[a[-2]] == sd.kstp.get(b[0]) and lex.known_bare(a[:-2]):
                return (a, b, "sandhi")
        return ()

    def suggest(self, w: str) -> list[dict]:
        """Near-miss suggestions.  The profile ranks each candidate (lang.rank returns a key whose first
        four items sort it) and may add its own (Tamil: the bare form of a sandhi word)."""
        k = self.settings["suggest"]["max"]
        cands = self.index.candidates(w)
        if self.lang.more_candidates:
            cands |= self.lang.more_candidates(self, w)
            cands.discard(w)
        hits = []
        for c in cands:
            h = self.lang.rank(self, w, c)
            if h is not None:
                hits.append(h)
        hits.extend(self.lang.extra_suggestions(self, w))
        hits.sort(key=lambda h: h[:4])
        out = [h[4] for h in hits[:k]]
        right = self.lex.corrections.get(w)
        if right:
            # a reviewer already corrected this misspelling: their form comes first
            first = {"w": right, "op": "reviewed", "cls": "reviewed", "freq": self.freq(right)}
            out = [first] + [d for d in out if d["w"] != right][:k - 1]
        if self.learned and w in self.learned:
            # the reviewer replaced this word before: what it became comes before everything else
            learned = self.learned_suggestions(w)
            seen = {d["w"] for d in learned}
            out = learned + [d for d in out if d["w"] not in seen]
        return out

    # -- sandhi ---------------------------------------------------------------------------
    def suffix_verdict(self, bare: str, cls: str) -> tuple | None:
        """The OV habit of the longest ending of `bare` seen on enough distinct words:
        (ov_doubled, with, without, suffix, forms), or None when that ending is not decisive."""
        s = self.settings["sandhi"]
        table = self.lex.sandhi_suffix
        for n in range(min(Lexicon.SUFFIX_MAX, len(bare) - 1), 0, -1):
            a = table.get((bare[-n:], cls))
            if not a or a[2] < s.get("suffix_min_forms", 20):
                continue
            wi, wo, forms = a
            if max(wi, wo) / (wi + wo) < s.get("suffix_pct", 0.95):
                return None
            return (wi > wo, wi, wo, bare[-n:], forms)
        return None

    def grammar(self, bare: str, cls: str) -> tg.RuleHit | None:
        self._check_cache()
        key = (bare, cls)
        if key not in self._rule_cache:
            self._rule_cache[key] = tg.sandhi_rule(bare, cls, self.lex, self.lex.rules)
        return self._rule_cache[key]

    def next_grammar(self, w2: str, cls: str) -> tg.RuleHit | None:
        """A `next:` rule (house style about the following word, e.g. no ஒற்று before தேவன்)."""
        self._check_cache()
        key = ("next", w2, cls)                 # three items, so never equal to a (bare, cls) key
        if key not in self._rule_cache:
            self._rule_cache[key] = tg.next_rule(w2, cls, self.lex.rules)
        return self._rule_cache[key]

    def rule_stats(self) -> dict[str, dict]:
        if not self.lang.sandhi:
            return {}
        key = (self.lex.generation, id(self.lex.rules))
        if self._rule_stats[0] != key:
            self._rule_stats = (key, tg.measure(self.lex, self.lex.rules))
        return self._rule_stats[1]

    def reload_rules(self) -> None:
        self.lex.load_rules()
        self.settings_gen += 1

    @staticmethod
    def _pooled(table: dict, bare: str) -> tuple[int, int]:
        w = o = 0
        for k in "KSTP":
            a = table.get((bare, k))
            if a:
                w += a[0]
                o += a[1]
        return w, o

    def sandhi_leads(self, toks: list[tuple]) -> list[dict]:
        """toks: [(word, run_index, s, e, adjacent_to_previous, text_after)] for one adjacency stream.

        Up to three sources may object to the text: the OV's habit for this very word (strong/weak), the
        grammar rule for its ending (tamil_grammar), and, only when neither has a view, the OV habit of its
        ending (suffix statistics, missing ஒற்று only).  A lead is raised when any of them objects; when
        another source (including the IRV's own consistent habit) agrees with the text, the lead is marked
        strength "conflict" so the reviewer can see that the sources disagree."""
        sd = self.lang.sandhi
        if not sd:
            return []
        KSTP, KSTP_LETTER, SANDHI_FINAL_RE, ELIGIBLE_RE, PULLI = sd.kstp, sd.kstp_letter, sd.final_re, sd.eligible_re, sd.pulli
        s = self.settings["sandhi"]
        lex = self.lex
        leads = []
        for (w1, r1, s1, e1, _, _), (w2, _, _, _, adj, _) in zip(toks, toks[1:]):
            if not adj or not w2 or w2[0] not in KSTP:
                continue
            cls = KSTP[w2[0]]
            if w1 in lex.name_final:
                continue
            if SANDHI_FINAL_RE.search(w1):
                has, bare = True, w1[:-2]
                if KSTP[w1[-2]] != cls:
                    if s.get("report_wrong_class", True) and lex.known_bare(bare):
                        wi, wo = lex.sandhi.get((bare, cls), (0, 0))
                        leads.append({"kind": "wrong_class", "w1": w1, "w2": w2, "run": r1, "s": s1, "e": e1,
                                      "proposed": bare + KSTP_LETTER[cls] + PULLI, "with": wi, "without": wo,
                                      "strength": "strong", "basis": "ov", "ignored": (w1, w2) in self.ignored_pairs})
                    continue
            elif ELIGIBLE_RE.search(w1):
                has, bare = False, w1
            else:
                continue
            wi, wo = lex.sandhi.get((bare, cls), (0, 0))
            lo, hi = min(wi, wo), max(wi, wo)
            strong = hi > 0 and ((lo == 0 and hi >= s["min_total"]) or (lo <= s["minority_max"] and hi >= s["majority_min"]))
            weak = (hi > 0 and not strong and s.get("include_weak") and hi + lo >= s["min_total"]
                    and hi / (hi + lo) >= s.get("weak_pct", 0.8))
            ow, oo = self._pooled(lex.sandhi, bare)             # the OV's habit for this word before any க/ச/த/ப
            iw, io = self._pooled(self.irv_pairs, bare)         # the IRV's own habit for it
            nh = self.next_grammar(w2, cls) if s.get("use_grammar", True) else None
            if nh is not None and nh.verdict == "skip":
                continue
            if nh is not None and nh.verdict != "ambiguous":
                # a house rule about the following word decides alone: the OV and IRV habits do not override it
                if (nh.verdict == "double") != has:
                    lead = {"kind": "extra" if has else "missing", "w1": w1, "w2": w2, "run": r1, "s": s1, "e": e1,
                            "proposed": bare if has else bare + KSTP_LETTER[cls] + PULLI,
                            "with": wi, "without": wo, "pooled_with": ow, "pooled_without": oo, "irv_with": iw,
                            "irv_without": io, "strength": "grammar", "basis": "grammar", "objecting": ["grammar"],
                            "agreeing": [], "ignored": (w1, w2) in self.ignored_pairs}
                    lead.update(nh.to_json(), rule_with=0, rule_without=0, rule_forms=0)
                    leads.append(lead)
                continue
            irv_n = iw + io
            irv_habit = None
            if irv_n >= s.get("irv_veto_min", 5):
                if iw / irv_n < s.get("irv_veto_pct", 0.35):
                    irv_habit = False
                elif io / irv_n < s.get("irv_veto_pct", 0.35):
                    irv_habit = True
            ops: list[tuple[str, bool]] = []                    # (source, says "doubled")
            if strong or weak:
                ops.append(("ov", wi > wo))
            elif ow + oo >= 2 and max(ow, oo) / (ow + oo) >= 0.75:
                ops.append(("ov_pooled", ow > oo))              # may only agree with the text, never raise a lead
            hit = self.grammar(bare, cls) if s.get("use_grammar", True) else None
            if hit is not None and hit.verdict == "skip":
                continue                                        # the reviewer decided: never flag these words
            if hit and hit.verdict != "ambiguous":
                ops.append(("grammar", hit.verdict == "double"))
            if irv_habit is not None:
                ops.append(("irv", irv_habit))                  # agree-only, like ov_pooled
            sv = None
            if (not has and s.get("use_suffix", True) and not (strong or weak)
                    and (hit is None or hit.verdict == "ambiguous")):
                # Too few OV examples and no grammar verdict: fall back on the OV habit of the ending, but only
                # when nothing disagrees (the weakest evidence; a bare-looking ending also covers infinitives).
                sv = self.suffix_verdict(bare, cls)
                if sv is not None and sv[0] and not oo > ow and irv_habit is not False:
                    ops.append(("suffix", True))
                else:
                    sv = None
            objecting = [src for src, d in ops if d != has and src in ("ov", "grammar", "suffix")]
            if not objecting:
                continue
            agreeing = [src for src, d in ops if d == has]
            basis = objecting[0]
            strength = ("strong" if strong else "weak") if basis == "ov" else basis
            if agreeing:
                if not s.get("show_conflicts", True):
                    continue
                strength = "conflict"
            lead = {"kind": "extra" if has else "missing", "w1": w1, "w2": w2, "run": r1, "s": s1, "e": e1,
                    "proposed": bare if has else bare + KSTP_LETTER[cls] + PULLI,
                    "with": wi, "without": wo, "pooled_with": ow, "pooled_without": oo, "irv_with": iw, "irv_without": io,
                    "strength": strength, "basis": basis, "objecting": objecting, "agreeing": agreeing,
                    "ignored": (w1, w2) in self.ignored_pairs}
            if hit:
                st = self.rule_stats().get(hit.rule.id, {})
                lead.update(hit.to_json(), rule_with=st.get("with", 0), rule_without=st.get("without", 0),
                            rule_forms=st.get("forms", 0))
            if sv:
                lead.update(suffix=sv[3], suffix_with=sv[1], suffix_without=sv[2], forms=sv[4])
            leads.append(lead)
        return leads

    def dangling_leads(self, toks: list[tuple], next_first: str | None = None, body: bool = True) -> list[dict]:
        """An ஒற்று with nothing to join: the next word starts with a vowel or soft consonant, or a digit,
        punctuation or the end of the text follows.  `next_first` is the first word of the next line when it
        continues the same text (poetry lines), so `அவனைக்` at a line end before `கொன்றான்` is fine."""
        sd = self.lang.sandhi
        if not sd:
            return []
        KSTP, SANDHI_FINAL_RE = sd.kstp, sd.final_re
        s = self.settings["sandhi"]
        if not s.get("dangling", True):
            return []
        lex = self.lex
        out = []
        for i, (w1, r1, s1, e1, _, after) in enumerate(toks):
            if not SANDHI_FINAL_RE.search(w1) or w1 in lex.name_final:
                continue
            kind = lex.kstp_kind.get(w1)
            bare = w1[:-2]
            if kind == "lexical" or not (kind == "sandhi" or lex.known_bare(bare)):
                continue
            m, o = self.irv_form_use.get(w1, (0, 0))
            if o > m and o >= s.get("dangling_irv_min", 2):
                continue                                        # the IRV uses this form mostly unjoined: a name (நோத்)
            hit = self.grammar(bare, KSTP[w1[-2]])
            if hit and hit.verdict in ("bare", "skip"):
                continue                                        # an ending that never takes ஒற்று: a name (மாராத், நோத்)
            nxt = toks[i + 1] if i + 1 < len(toks) else None
            rest = after.lstrip()
            w2, reading = "", ""
            if nxt is not None and nxt[4]:                      # the next word follows after a space
                if nxt[0][0] in KSTP:
                    continue                                    # same class is right; another class is wrong_class
                reason, w2 = "vowel", nxt[0]
            elif rest:
                if rest[0].isdigit():
                    if not s.get("dangling_digits", True):
                        continue
                    w2 = re.match(r"\d[\d,]*\d|\d", rest).group()
                    reading = tg.number_reading(w2) or ""
                    if reading and KSTP.get(reading[0]) == KSTP[w1[-2]]:
                        continue                                # அந்தப் 10 is read அந்தப் பத்து
                    reason = "digit"
                else:
                    reason, w2 = "punct", rest[0]
            elif nxt is None and body and next_first:
                if next_first[0] in KSTP:
                    continue
                reason, w2 = "vowel", next_first
            else:
                reason = "end"
            ow, oo = self._pooled(lex.sandhi, bare)
            out.append({"kind": "dangling", "reason": reason, "w1": w1, "w2": w2, "run": r1, "s": s1, "e": e1,
                        "proposed": bare, "with": ow, "without": oo, "irv_match": m, "irv_other": o, "reading": reading,
                        "strength": "rule", "basis": "dangling", "ignored": (w1, w2) in self.ignored_pairs})
        return out

    # -- warnings ---------------------------------------------------------------------------
    def run_warnings(self, seg: ud.Seg, next_seg: ud.Seg | None) -> list[dict]:
        s = self.settings["warnings"]
        out = []
        text = seg.text
        if s.get("double_space", True):
            for m in re.finditer("  +", text):
                out.append({"kind": "double_space", "s": m.start(), "e": m.end()})
        for m in (self.lang.zero_width_re or ZERO_WIDTH_RE).finditer(text):
            out.append({"kind": "zero_width", "s": m.start(), "e": m.end() + 1 if m.end() < len(text) else m.end()})
        if s.get("digits", True) and seg.context == "verse":
            for m in DIGIT_RE.finditer(text):
                out.append({"kind": "digits_in_verse", "s": m.start(), "e": m.end()})
        lang = self.lang
        if s.get("defects", True):
            for name, rx in lang.defect_patterns:
                for m in rx.finditer(text):
                    out.append({"kind": name, "s": m.start(), "e": m.end()})
        if (s.get("space_before_note_close") and text.endswith(" ") and next_seg is not None
                and next_seg.kind == "m" and next_seg.marker in ("f*", "x*")):
            out.append({"kind": "space_before_note_close", "s": len(text) - 1, "e": len(text)})
        if s.get("double_punct", True):
            for m in lang.punct_run_re.finditer(text):
                if set(m.group()) != {"."} and m.group() not in lang.ok_punct_runs:
                    out.append({"kind": "double_punct", "s": m.start(), "e": m.end()})
        if s.get("no_space_after_punct", True):
            for m in lang.no_space_after_re.finditer(text):
                out.append({"kind": "no_space_after_punct", "s": m.start(), "e": m.end()})
        if s.get("space_before_punct", True):
            for m in lang.space_before_re.finditer(text):
                out.append({"kind": "space_before_punct", "s": m.start(1), "e": m.end(1)})
        prev = None
        for w, a, b in lang.tokens_with_offsets(text):
            tw = lang.token_warning(self, text, w, a, b, s)
            if tw:
                out.append(tw)
            if s.get("repeated_word") and prev and prev[0] == w and text[prev[2]:a].isspace():
                out.append({"kind": "repeated_word", "s": a, "e": b})
            prev = (w, a, b)
        if lang.extra_warnings:
            out.extend(lang.extra_warnings(self, seg, next_seg))
        return out

    # -- line / chapter / book -------------------------------------------------------------
    def next_first(self, book: ud.Book, idx: int) -> str | None:
        """First word of the text that continues after book.lines[idx] (0-based), skipping empty paragraph
        lines; None at a chapter break or when the next text is not verse text (a heading)."""
        checked = set(self.settings["checked_contexts"])
        for line in book.lines[idx + 1:idx + 4]:
            if line.style == "c":
                return None
            for seg in line.segs:
                if seg.kind == "t" and seg.stream == 0 and seg.context in checked:
                    toks = self.lang.tokens(seg.text)
                    if toks:
                        return toks[0] if seg.context == "verse" else None
        return None

    def check_line(self, line: ud.Line, ignored_once: set[tuple[int, str]] | None = None,
                   next_first: str | None = None) -> dict:
        ignored_once = ignored_once or set()
        block = line.to_json()
        segs_json = block["segs"]
        checked = set(self.settings["checked_contexts"])
        runs = [(i, seg) for i, seg in enumerate(line.segs) if seg.kind == "t"]
        for i, seg in runs:
            if seg.context in checked:
                segs_json[i]["tokens"] = []
                segs_json[i]["sandhi"] = []
                segs_json[i]["warnings"] = self.run_warnings(seg, line.segs[i + 1] if i + 1 < len(line.segs) else None)
        streams: dict[int, list[tuple[int, ud.Seg]]] = {}
        for i, seg in runs:
            streams.setdefault(seg.stream, []).append((i, seg))
        for stream, sruns in streams.items():
            toks: list[tuple] = []
            text_so_far = ""
            prev_end: int | None = None
            for i, seg in sruns:
                for w, a, b in self.lang.tokens_with_offsets(seg.text):
                    abs_a = len(text_so_far) + a
                    adj = False
                    if prev_end is not None:
                        gap = (text_so_far + seg.text)[prev_end:abs_a]
                        adj = gap != "" and gap.isspace()
                        toks[-1] = toks[-1][:5] + (gap,)
                    toks.append((w, i, a, b, adj, ""))
                    prev_end = len(text_so_far) + b
                text_so_far += seg.text
            if toks:
                toks[-1] = toks[-1][:5] + (text_so_far[prev_end:],)
            for w, i, a, b, _, _ in toks:
                seg = line.segs[i]
                if seg.context not in checked:
                    continue
                info = self.classify(w)
                if info.status == "ok":
                    continue
                segs_json[i]["tokens"].append(info.to_json(w, a, b, ignored_once=(seg.start + a, w) in ignored_once))
            if sruns and sruns[0][1].context in checked:
                leads = self.sandhi_leads(toks) + self.dangling_leads(toks, next_first, body=stream == 0)
                if self.lang.line_leads:
                    leads += self.lang.line_leads(self, toks, line)
                for lead in leads:
                    r = lead.pop("run")
                    segs_json[r]["sandhi"].append(lead)
        if ignored_once:
            mark_ignored_once(block, ignored_once)
        return block

    def check_chapter(self, book: ud.Book, n: int, ignored_once_by_line: dict[int, set] | None = None) -> list[dict]:
        lo, hi = book.chapters[n]
        ign = ignored_once_by_line or {}
        blocks = [self.check_line(line, ign.get(line.line_no), self.next_first(book, lo + k))
                  for k, line in enumerate(book.lines[lo:hi + 1])]
        if self.lang.chapter_leads:
            self.lang.chapter_leads(self, book, n, blocks)
            for line, block in zip(book.lines[lo:hi + 1], blocks):
                if ign.get(line.line_no):
                    mark_ignored_once(block, ign[line.line_no])
        return blocks

    def book_report(self, code: str, version: str, book: ud.Book, ignored_once_by_line: dict[int, set] | None = None,
                    ignore_gen: int | None = None) -> dict:
        """Per-chapter counts and the unknown-word table for a whole book (cached per version/generation).
        `ignore_gen` is the ignore generation read *before* ignored_once_by_line was, so that a report
        computed from an ignore list that changed meanwhile is never cached as current."""
        key = (version, self.lex.generation, self.settings_gen, self.ignore_gen if ignore_gen is None else ignore_gen)
        cached = self._reports.get(code)
        if cached and cached[0] == key:
            return cached[1]
        ign = ignored_once_by_line or {}
        extra: dict = {}
        chapters: dict[int, Counter] = {}
        words: dict[str, dict] = {}
        total: Counter = Counter()
        for n, (lo, hi) in enumerate(book.chapters):
            c: Counter = Counter()
            for line, block in zip(book.lines[lo:hi + 1], self.check_chapter(book, n, ign)):
                if self.lang.report_block:
                    self.lang.report_block(self, extra, block)
                for seg in block["segs"]:
                    if seg.get("k") != "t" or not seg.get("checked"):
                        continue
                    for t in seg.get("tokens", []):
                        st = t["status"]
                        if t.get("ignored_once") or st == "ignored":
                            continue
                        c[st] += 1
                        w = t["t"]
                        d = words.get(w)
                        if d is None:
                            d = words[w] = {"word": w, "status": st, "count_book": 0, "count_total": self.irv_count.get(w, 0),
                                            "suggestion": (t["sugg"][0]["w"] if t["sugg"] else ""),
                                            "sugg_class": (t["sugg"][0]["cls"] if t["sugg"] else ""),
                                            "first_ref": line.ref, "first_line": line.line_no,
                                            "first_start": seg["s"] + t["s"], "first_chapter": n, "contexts": set(),
                                            "ov_lemma": t.get("ov_lemma", 0)}
                        d["count_book"] += 1
                        d["contexts"].add(seg["ctx"])
                    for lead in seg.get("sandhi", []):
                        if not lead.get("ignored"):
                            c["sandhi_leads"] += 1
                            if lead["kind"] == "dangling":
                                c["dangling"] += 1
                    c["warnings"] += sum(1 for w in seg.get("warnings", []) if not w.get("ignored"))
            chapters[n] = c
            total.update(c)
        for d in words.values():
            d["contexts"] = sorted(d["contexts"])
        report = {"chapters": {n: dict(c) for n, c in chapters.items()}, "total": dict(total),
                  "unknown_types": sum(1 for d in words.values() if d["status"] == "unknown"), "words": words}
        if extra:
            report["extra"] = extra
        self._reports[code] = (key, report)
        return report

    def cached_report(self, code: str, version: str) -> dict | None:
        cached = self._reports.get(code)
        if cached and cached[0] == (version, self.lex.generation, self.settings_gen, self.ignore_gen):
            return cached[1]
        return None

    def invalidate_report(self, code: str) -> None:
        self._reports.pop(code, None)
