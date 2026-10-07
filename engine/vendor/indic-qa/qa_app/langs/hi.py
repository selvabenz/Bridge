"""Hindi (Devanagari) profile for the IRV editor.  Rules: docs/HINDI_RULES.md (`hi.*` ids), measured in
docs/HINDI_CORPUS_FACTS.md.

Tables live in hi_tables.py, the algorithms in qa_app/kinds.py; this module wires them into the checker's
hooks.  Nothing here is used when the editor runs another language.

The algorithm, in the order a word goes through it (rules §4.1, revised by the measurements):
  1. the lookup key: NFC with ZWJ/ZWNJ removed (the OV's BSI परमेश्‍वर and the IRV's परमेश्वर are one word);
     the raw text is kept, so underlines and edits land on what is written
  2. a known misspelling or a divine-name variant → malformed (red)
  3. a Devanagari sign-sequence error (§3: बडे़, ॠतु, नहीँ, मुँण्ड़ा, a stray joiner …) → malformed, with the fix
  4. an OV word (by key), an extra word, a joined OV pronoun + postposition, or a hyphenated compound of
     known words → ok (a rare OV word next to a much commoner form → suspect, listed)
  5. a house-style variant of an OV word (हजार / OV हज़ार, नुक़्ता, ँ/ं, pancham, -ये/-ए, final ्) → ok;
     the consistency rules decide whether the spelling is the IRV's majority
  6. a known word + a listed ending → inflected_ok (grey); used ≥ 5× in the IRV → irv_ok (grey)
  7. two known words run together → compound; otherwise unknown (red), with ranked suggestions
Then, over adjacent words: spelling consistency per word, pronoun + postposition joined/split per pair,
genitive gender, oblique before a postposition, and the bigram rules (के लिये, कहा कि, चारों ओर, मैं हूँ).
"""
from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from pathlib import Path

from .. import paths
from .. import kinds
from . import APP_DIR, REPO_ROOT, Language
from . import hi_tables as T

DEV = T.DEV
canon = T.canon

# ---------------------------------------------------------------------------------------------
# rule catalogue: id -> (group, plain-language label, inline?, on by default?)
# Inline = mechanical, or measured at ≥ 0.90 precision; everything else is listed until a review round.
# ---------------------------------------------------------------------------------------------
RULES: dict[str, tuple[str, str, bool, bool]] = {
    "hi.norm.encoding": ("Encoding", "Encoding: not NFC, precomposed nuqta letter, invisible or control character "
                                     "(ZWSP, BOM, NBSP, U+009D), ASCII | for the danda, Devanagari digit (the IRV writes 0–9)", True, True),
    "hi.norm.colon-for-visarga": ("Encoding", "ASCII colon typed for the visarga ः (दु:ख → दुःख, नि:सन्देह → निःसन्देह)", True, True),
    "hi.norm.zero-width": ("Encoding", "ZWJ / ZWNJ after a halant (्‍य): strip them (Q18); one fix per text run", True, True),
    "hi.norm.latin-in-text": ("Encoding", "Latin letter inside Devanagari text", True, True),
    "hi.norm.quotes": ("Encoding", "Straight quote \" or ' (the IRV uses curly quotes)", True, True),
    "hi.shape.errors": ("Shape", "Impossible sign sequence (§3): nuqta after the vowel sign (बडे़), ॠ for ऋ, a joiner "
                                 "that joins nothing, ँ over ि ी े ै ो ौ (नहीँ), flap after ण्/न् (मुँण्ड़ा), a halant, vowel "
                                 "sign, nasal or nuqta where Devanagari does not allow it", True, True),
    "hi.shape.warnings": ("Shape", "Unusual sign sequence (candra ॉ ॅ, four consonants in one conjunct) in a word not "
                                   "in the dictionary", False, True),
    "hi.shape.single-letter": ("Shape", "A one-letter word other than न आ व ए ओ औ (ई / स only in ई. पू., ई.स.)", False, True),
    "hi.shape.long-token": ("Shape", "A word of 20+ characters (a missing space?) not in the dictionary", False, True),
    "hi.shape.mixed-script": ("Shape", "Gurmukhi, Bengali or other Indic letter in Hindi text", True, True),
    "hi.shape.hyphen": ("Shape", "Loose hyphen: a hyphen used as a dash (है- देखें → है — देखें), or a space inside a "
                                 "compound (तितर- बितर → तितर-बितर)", True, True),
    "hi.punct.en-dash-in-word": ("Punctuation", "En dash – between words: - in a compound (बारी–सा → बारी-सा), "
                                                "a spaced — between clauses", False, True),
    "hi.punct.ellipsis": ("Punctuation", "Ellipsis written .... or …: the IRV writes three dots ... (Q19)", True, True),
    "hi.punct.space-before-danda": ("Punctuation", "Space before । or other punctuation (the IRV writes है।)", True, True),
    "hi.punct.double": ("Punctuation", "Doubled punctuation (।। !! ?, ।,); not ?: after a footnote lemma", True, True),
    "hi.norm.spacing": ("Punctuation", "Double space, or no space after , ; ! ? ।", True, True),
    "hi.punct.punct-before-danda": ("Punctuation", "! ? , ; : directly before । (Q10: one mark only)", False, True),
    "hi.punct.double-danda": ("Punctuation", "॥ in the text", False, True),
    "hi.punct.quotes-unbalanced": ("Punctuation", "A closing ” without an opening “ in the chapter (an open quotation "
                                                  "may run on into the next chapter: the IRV convention)", False, True),
    "hi.punct.fullstop-for-danda": ("Punctuation", "Full stop . after a word that is not an abbreviation (ई. पू., यूह. 1:4 "
                                                   "are fine): use the danda ।", False, True),
    "hi.lex.unknown": ("Lexicon", "Word not in the dictionary (OV Hindi + extra words) and rare in the IRV: not "
                                  "verifiable, shown grey (the review found most of them correct)", True, True),
    "hi.lex.rare-near-common": ("Lexicon", "Rare word (≤ 2× in OV + IRV) next to a much commoner near form "
                                           "(वें → वे, दिवार → दीवार); not for house-style differences", False, True),
    "hi.lex.known-misspelling": ("Lexicon", "Listed in known_misspellings.tsv (आर्शीवाद → आशीर्वाद, नें → ने, जबकी → जबकि)",
                                 True, True),
    "hi.style.divine-names": ("Lexicon", "Divine name spelled another way than the IRV's (परमेश्वर, यहोवा, यीशु, मसीह, प्रभु)",
                              True, True),
    "hi.lex.archaic-form": ("Lexicon", "OV (older) word the IRV replaced (इसलिये → इसलिए, सभों → सभी)", False, False),
    "hi.style.nuqta-consistency": ("Consistency", "Minority spelling with/without nuqta on क ख ग ज फ (Q1)", False, True),
    "hi.style.chandrabindu-consistency": ("Consistency", "ं where the standard writes ँ (Q2: बंधुआई → बँधुआई, "
                                                         "तुरहियां → तुरहियाँ)", False, True),
    "hi.style.pancham-consistency": ("Consistency", "Minority spelling pancham vs anusvara (मन्दिर / मंदिर, Q3)", False, True),
    "hi.style.ye-ending-consistency": ("Consistency", "-ये / -यी where the standard writes -ए / -ई (Q4: गये → गए, "
                                                      "चाहिये → चाहिए)", True, True),
    "hi.style.halant-final-consistency": ("Consistency", "Minority spelling with/without a final halant (अर्थात् / अर्थात, Q5)",
                                          False, True),
    "hi.style.postposition-joining": ("Consistency", "Pronoun + postposition written as one word or two against the IRV's "
                                                     "habit for that pair (जिस से → जिससे; the IRV writes उसने but उस पर)", False, True),
    "hi.gram.genitive-gender": ("Grammar", "का / की, मेरा / मेरी … disagrees with the noun it qualifies (उसका सामर्थ्य → "
                                           "उसकी), or is not oblique before a postposition (मेरी उद्धार का → मेरे); not when a "
                                           "later noun is the head (परमेश्वर की … शिकायत)", False, True),
    "hi.gram.oblique": ("Grammar", "Direct -ा form before a postposition where the corpus uses -े (बच्चा के → बच्चे के)",
                        False, True),
    "hi.gram.compound-postposition": ("Grammar", "का / की before a compound postposition (की समान → के समान, का ओर → की ओर)",
                                      False, True),
    "hi.gram.ki-ki": ("Grammar", "की after a verb of saying or knowing (जानते की → जानते कि)", False, True),
    "hi.gram.aur-or": ("Grammar", "और after चारों / दाहिनी / बाईं (चारों और → चारों ओर); not दाहिनी और बाईं", False, True),
    "hi.gram.na-negative": ("Grammar", "ना before an imperative or infinitive (चिन्ता ना करो → न करो)", False, True),
    "hi.gram.pronoun-copula": ("Grammar", "Pronoun and copula side by side disagree (मैं है → मैं हूँ, वे है → वे हैं)", False, True),
    "hi.style.reduplication-hyphen": ("Style", "Reduplication written “X X”: hyphenate (बार-बार, बूँद-बूँद); not an emphatic "
                                               "नहीं नहीं or a verb ले ले", False, True),
    "hi.style.repeated-word": ("Style", "The same word twice", False, False),
}
SHAPE_RULE_SWITCH = {r.id: ("hi.shape.errors" if r.severity == "error" else "hi.shape.warnings") for r in T.SHAPE_RULES}
SHAPE_RULE_SWITCH.update({"hi.shape.single-letter": "hi.shape.single-letter", "hi.shape.long-token": "hi.shape.long-token"})
GENERIC_WARNING_RULE = {"double_space": "hi.norm.spacing", "no_space_after_punct": "hi.norm.spacing",
                        "space_before_punct": "hi.punct.space-before-danda", "double_punct": "hi.punct.double",
                        "digits_in_verse": "hi.norm.encoding", "space_before_note_close": "hi.norm.spacing",
                        "repeated_word": "hi.style.repeated-word"}
CONSISTENCY_RULE = {"nuqta": "hi.style.nuqta-consistency", "chandrabindu": "hi.style.chandrabindu-consistency",
                    "pancham": "hi.style.pancham-consistency", "ye": "hi.style.ye-ending-consistency",
                    "halant": "hi.style.halant-final-consistency"}

SUFFIX_STOP = ["ने", "को", "से", "में", "पर", "का", "की", "के", "ही", "भी", "तो", "वाला", "वाले", "वाली"]
# §4.1 endings stripped for inflected_ok, longest first; the base may be the stem, or the stem + ा / ी / ना
ENDINGS = sorted(set("""ों ाओं ियों ें ाएँ ाएं ियाँ ियां े ता ती ते ना नी ने कर के गा गी गे ेगा ेगी ेंगे ोगे ूँगा ूंगा ा ी ए ीं इए िए ओ
ो ाओ ाओगे ाएगा ाएगी ाएँगे ाएंगे ाया ाई ाए ाती ाते ाता ाना ाने ाकर ोगी ूँ ूं""".split()), key=lambda s: (-len(s), s))

DEFAULT_SETTINGS: dict = {
    "reviewer": "",
    "checked_contexts": ["verse", "heading", "psalm_title", "footnote_text", "intro", "title"],
    "suggest": {"max": 5, "lemma_min": 3, "irv_min": 3},
    "compound": {"enabled": True, "min_first": 3, "min_second": 3, "min_freq": 3, "suffix_stop": SUFFIX_STOP},
    "warnings": {"double_space": True, "digits": False, "defects": False, "space_before_note_close": False,
                 "double_punct": False, "no_space_after_punct": True, "space_before_punct": True, "repeated_word": False},
    "lex": {"irv_accept_min": 5, "inflect_min_aksharas": 2, "rare_max": 2, "rare_ratio": 50, "rare_cost": 0.5},
    "consistency": {"minority_max": 0.25, "close": 0.8, "style_min": 20, "ov_veto": 3, "join_min": 3},
    "agreement": {"gender_min": 20, "gender_pct": 0.95, "oblique_min": 10, "oblique_direct_max": 0.10},
    "rules": {rid: v[3] for rid, v in RULES.items()},
    # decided in the reviewed workbook: unknown words grey (most were correct); असम्भव matches सम्भव / सम्भवतः (Q3)
    "style": {"unknown_color": "grey", "pancham:असंभव": "असम्भव"},
}


def on(checker, rid: str) -> bool:
    return bool(checker.settings.get("rules", {}).get(rid, RULES.get(rid, ("", "", False, True))[3]))


# ---------------------------------------------------------------------------------------------
# lexicon tables
# ---------------------------------------------------------------------------------------------

def _tsv(path: Path):
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE))


def load_lexicon_extra(lex, dict_dir: Path) -> None:
    d = lex.data
    d["misspell"], d["misspell_prefix"], d["misspell_forced"], d["misspell_keep"] = {}, [], set(), set()
    for r in _tsv(dict_dir / "known_misspellings.tsv"):
        wrong, right = canon(r.get("wrong", "").strip()), T.normalize_text(r.get("right", "").strip())
        if not wrong or not right or wrong.startswith("#"):
            continue
        rule = (r.get("rule") or "hi.lex.known-misspelling").strip()
        if wrong.endswith("*") and right.endswith("*"):
            d["misspell_prefix"].append((wrong[:-1], right[:-1], rule))
            continue
        if canon(right) == wrong:
            d["misspell_keep"].add(wrong)              # reviewed: keep as written (रुपये, परिक्षेपण)
            continue
        d["misspell"][wrong] = (right, rule)
        if "reviewed" in (r.get("note") or ""):
            d["misspell_forced"].add(wrong)            # decided by the reviewer: flagged even as the IRV majority
    d["misspell_prefix"].sort(key=lambda x: -len(x[0]))
    d["names"] = {canon(r["name"]) for r in _tsv(dict_dir / "names.tsv") if r.get("name") and r.get("use", "yes") != "no"}
    d["gender"] = {r["word"]: (int(r["m"]), int(r["f"])) for r in _tsv(dict_dir / "gender.tsv")}
    for w, g in T.GENDER_OVERRIDE.items():                  # reviewed (अन्नबलि is feminine): no learned count decides
        n = sum(d["gender"].get(w, (0, 0))) or 20
        d["gender"][w] = (n, 0) if g == "M" else (0, n)
    d["oblique"] = {r["stem"]: (int(r["direct"]), int(r["oblique"])) for r in _tsv(dict_dir / "oblique.tsv")}
    d["archaic"] = {r["ov_word"]: (r["modern"], int(r["aligned"]), int(r["slots"])) for r in _tsv(dict_dir / "archaic.tsv")}
    d["misspell_path"] = str(dict_dir / "known_misspellings.tsv")
    d.pop("house_map", None)


def _extra_canon(lex) -> set:
    d = lex.data
    if d.get("extra_canon_gen") != lex.generation:
        d["extra_canon"] = {canon(w) for w in lex.extra}
        d["extra_canon_gen"] = lex.generation
    return d["extra_canon"]


def known_key(lex, key: str) -> bool:
    return key in lex.ov or key in _extra_canon(lex) or key in lex.data.get("names", ())


def house_map(lex) -> dict:
    """House-style skeleton → the OV word(s) with it (rebuilt when the lexicon changes)."""
    d = lex.data
    if d.get("house_gen") != lex.generation:
        m = defaultdict(list)
        for w in sorted(lex.ov):
            m[T.sk_house(w)].append(w)
        d["house_map"], d["house_gen"] = m, lex.generation
    return d["house_map"]


def house_variant(lex, key: str) -> str | None:
    for w in house_map(lex).get(T.sk_house(key), ()):
        if w != key:
            return w
    return None


# ---------------------------------------------------------------------------------------------
# corpus state (per book, refreshed like the Tamil sandhi pair counts)
# ---------------------------------------------------------------------------------------------
QUOTE_CONTEXTS = {"verse", "heading", "psalm_title"}
ABBREV_RE = re.compile(f"([{DEV}]+)\\.(?=\\s*\\d)")
JOIN_SET = set(T.JOIN_PAIRS)
SPLIT_INDEX: dict[str, tuple[str, str]] = {}
for _a, _b in T.JOIN_PAIRS:
    SPLIT_INDEX.setdefault(_a + _b, (_a, _b))


def on_index_book(checker, code: str, book, checked: set) -> None:
    raw, hyph, space, split, abbr, colon = Counter(), Counter(), Counter(), Counter(), Counter(), Counter()
    for line in book.lines:
        for seg in line.segs:
            if seg.kind != "t":
                continue
            for m in ABBREV_RE.finditer(seg.text):           # any context: the \bdit references teach the abbreviations
                abbr[canon(m.group(1))] += 1
            if seg.context not in checked:
                continue
            raw.update(T.TOKEN_RE.findall(seg.text))
            for m in COLON_FINAL_RE.finditer(seg.text):
                colon[canon(m.group(1))] += 1
            for w1, w2, _, _ in kinds.adjacent_pairs(seg.text, T.TOKEN_RE):
                k1, k2 = canon(w1), canon(w2)
                if k1 == k2:
                    space[k1] += 1
                if (k1, k2) in JOIN_SET:
                    split[(k1, k2)] += 1
    can, hyph_tok = Counter(), Counter()
    for w, n in raw.items():
        k = canon(w)
        can[k] += n
        if "-" in k:
            hyph_tok[k] += n
        parts = k.split("-")
        if len(parts) == 2 and parts[0] == parts[1]:
            hyph[parts[0]] += n
    d = checker.data
    books = d.setdefault("book_state", {})
    old = books.get(code)
    for total_key, new in (("irv_canon", can), ("redup_hyph", hyph), ("redup_space", space), ("join_split", split),
                           ("abbr", abbr), ("colon_final", colon), ("hyph_tok", hyph_tok)):
        total = d.setdefault(total_key, Counter())
        if old:
            total.subtract(old[total_key])
            for k in [k for k, v in total.items() if v <= 0]:
                del total[k]
        total.update(new)
    books[code] = {"irv_canon": can, "redup_hyph": hyph, "redup_space": space, "join_split": split, "abbr": abbr,
                   "colon_final": colon, "hyph_tok": hyph_tok}


def on_build_index(checker) -> None:
    d = checker.data
    accept = checker.settings["lex"].get("irv_accept_min", 5)
    irv = {k: n for k, n in d.get("irv_canon", Counter()).items() if n > 0}
    # words the IRV itself uses often (modern forms the OV lacks: बल्कि, हजार) are suggestion targets too
    for k, n in sorted(irv.items()):
        if n >= accept and not any(f.severity == "error" for f in T.shape_findings(k)):
            checker.index.add(k)
    loose = defaultdict(list)
    for w in checker.index.words:
        loose[T.loose_skeleton(w)].append(w)
    d["loose"] = loose
    d["clusters"] = {kind: kinds.consistency_clusters(irv, fn) for kind, fn in T.SKELETONS.items()}
    d["join"] = kinds.join_split_clusters(T.JOIN_PAIRS, irv, d.get("join_split", Counter()))
    d["join_n"] = {p: (irv.get(p[0] + p[1], 0), d.get("join_split", Counter()).get(p, 0)) for p in T.JOIN_PAIRS}
    d["abbreviations"] = set(T.ABBREVIATION_SEED) | set(d.get("abbr", Counter()))
    d.pop("cons_memo", None)


# ---------------------------------------------------------------------------------------------
# classification
# ---------------------------------------------------------------------------------------------

def _info(status, **kw):
    from ..checker import WordInfo
    return WordInfo(status, **kw)


def _panel(extra: dict | None = None) -> dict:
    return {"panel": True, **(extra or {})}


def irv_c(checker, key: str, irv_raw: int = 0) -> int:
    return max(irv_raw, checker.data.get("irv_canon", {}).get(key, 0))


def freq_c(checker, key: str) -> int:
    return max(checker.lex.lemma_count.get(key, 0), checker.data.get("irv_canon", {}).get(key, 0))


def _sugg(w: str, to: str, cls: str, freq: int) -> dict:
    return {"w": to, "op": "sub", "from": w, "to": to, "at": 0, "cls": cls, "pos": "medial", "freq": freq}


def misspelling_of(checker, key: str):
    d = checker.lex.data
    if key in d.get("misspell_keep", ()):
        return None
    ms = d.get("misspell", {}).get(key)
    if not ms:
        for wrong, right, rule in d.get("misspell_prefix", ()):
            if key.startswith(wrong):
                return right + key[len(wrong):], rule
        return None
    right, rule = ms
    # never against the IRV's own majority (rules §4.3), unless the reviewer decided it (उज्जवल, सदृश्य)
    if (irv_c(checker, key) > irv_c(checker, canon(right)) and rule != "hi.style.divine-names"
            and key not in d.get("misspell_forced", ())):
        return None
    return ms


def classify_first(checker, w: str, irv: int, ov: int):
    lex = checker.lex
    if w in lex.ignored:
        return None
    key = canon(w)
    irv_n, ov_n = irv_c(checker, key, irv), lex.lemma_count.get(key, 0)
    ms = misspelling_of(checker, key)
    if ms and on(checker, ms[1] if ms[1] in RULES else "hi.lex.known-misspelling"):
        sugg = [_sugg(w, ms[0], "house", freq_c(checker, canon(ms[0])))]
        sugg += [x for x in checker.suggest(w) if x["w"] != ms[0]]
        return _info("malformed", rule=ms[1], severity="error", sugg=sugg[:5], irv=irv_n, ov=ov_n, ov_lemma=ov_n)
    found = [f for f in T.shape_findings(w) if not f.gated and on(checker, SHAPE_RULE_SWITCH.get(f.rule, f.rule))]
    if found:
        f = next((x for x in found if x.severity == "error"), found[0])
        return _info("malformed", rule=f.rule, severity=f.severity, sugg=checker.suggest(w), irv=irv_n, ov=ov_n, ov_lemma=ov_n,
                     extra={} if f.severity == "error" else _panel())
    known = known_key(lex, key) or ("-" in key and all(known_key(lex, p) for p in key.split("-") if p))
    if not known and key in checker.data.get("abbreviations", ()):
        return _info("ok", irv=irv_n, ov=ov_n, ov_lemma=ov_n)    # a book abbreviation of a reference (भज. 18:10)
    if not known:
        return None
    if key in lex.data.get("names", ()) or key in _extra_canon(lex):
        return _info("ok", irv=irv_n, ov=ov_n, ov_lemma=ov_n)    # a confirmed name (शाप, मास) or reviewer word: no lead
    if on(checker, "hi.lex.archaic-form"):
        a = lex.data.get("archaic", {}).get(key)
        if a:
            return _info("archaic", rule="hi.lex.archaic-form", severity="warning",
                         sugg=[_sugg(w, a[0], "archaic", freq_c(checker, a[0]))], irv=irv_n, ov=ov_n, ov_lemma=ov_n,
                         extra=_panel({"why": [f"the IRV has {a[0]} in {a[1]} of {a[2]} aligned verses"]}))
    if on(checker, "hi.lex.rare-near-common"):
        s = checker.settings["lex"]
        if ov_n + irv_n <= s.get("rare_max", 2) and ov_n < 3:
            best = _near_common(checker, key, ov_n + irv_n, s)
            if best:
                return _info("suspect", rule="hi.lex.rare-near-common", severity="warning", sugg=[best], irv=irv_n, ov=ov_n,
                             ov_lemma=ov_n, extra=_panel())
    return _info("ok", irv=irv_n, ov=ov_n, ov_lemma=ov_n)


def _near_common(checker, key, freq, s):
    """The frequent near form of a rare word: same loose skeleton or one deletion apart, cost ≤ rare_cost, and not
    only a house-style difference (nuqta, pancham, -ये/-ए, final ्: those are consistency questions)."""
    best = None
    cands = set(checker.data.get("loose", {}).get(T.loose_skeleton(key), ())) | checker.index.candidates(key)
    for c in sorted(cands):
        if c == key or freq_c(checker, c) < s.get("rare_ratio", 50) * max(1, freq):
            continue
        cost, ops = kinds.confusion_distance(key, c, T.CONFUSION, limit=s.get("rare_cost", 0.5))
        if cost > s.get("rare_cost", 0.5) or all(o[1] in T.HOUSE_CLASSES for o in ops):
            continue
        n_units = len(kinds.units(key, T.CONFUSION))
        if all(o[1] == "nasal" and o[2] in ("ins", "del") and o[5] >= n_units - 1 for o in ops):
            continue                    # only a word-final nasal: बदलें / बचें / दिखाईं are verb forms (reviewed)
        h = rank(checker, key, c, limit=s.get("rare_cost", 0.5))
        if h and (best is None or h[:4] < best[:4]):
            best = h
    return best[4] if best else None


def inflected(checker, key: str):
    memo = checker.data.setdefault("inflect_memo", {})
    gen = (checker.lex.generation, checker.settings_gen)
    if memo.get("gen") != gen or len(memo) > 60000:
        memo.clear()
        memo["gen"] = gen
    if key not in memo:
        min_ak = checker.settings["lex"].get("inflect_min_aksharas", 2)
        hit = None                      # the longest known base (दर्शाती → दर्शा, not दर्शी; वादों → वादा)
        for end in ENDINGS:
            if not key.endswith(end) or len(key) <= len(end):
                continue
            stem = key[:-len(end)]
            if aksharas(stem) < min_ak:
                continue
            for base in (stem + "ा", stem, stem + "ना", stem + "ी"):
                if known_key(checker.lex, base) and (hit is None or len(base) > len(hit[0])):
                    hit = (base, end)
        memo[key] = hit
    return memo[key]


def aksharas(w: str) -> int:
    n, prev = 0, ""
    for ch in w:
        c = T.cls(ch)
        if c in "CV" and prev != "_":
            n += 1
        if c != "Z":
            prev = c
    return n


def classify_extra(checker, w: str, irv: int, ov: int):
    """After classify_first: gated shape warnings, house-style variant, known word + ending, IRV-frequent."""
    lex = checker.lex
    key = canon(w)
    irv_n, ov_n = irv_c(checker, key, irv), lex.lemma_count.get(key, 0)
    gated = [f for f in T.shape_findings(w) if f.gated and on(checker, SHAPE_RULE_SWITCH.get(f.rule, f.rule))]
    if gated:
        f = gated[0]
        return _info("malformed", rule=f.rule, severity=f.severity, sugg=checker.suggest(w), irv=irv_n, ov=ov_n, ov_lemma=ov_n,
                     extra=_panel())
    variant = house_variant(lex, key)
    if variant:
        return _info("ok", irv=irv_n, ov=ov_n, ov_lemma=lex.lemma_count.get(variant, 0))
    hit = inflected(checker, key)
    if hit:
        return _info("inflected_ok", bare=hit[0], irv=irv_n, ov=ov_n, ov_lemma=lex.lemma_count.get(hit[0], 0),
                     extra={"suffix": hit[1]})
    if irv_n >= checker.settings["lex"].get("irv_accept_min", 5):
        return _info("irv_ok", irv=irv_n, ov=ov_n, ov_lemma=ov_n)
    return None


def malformed(w: str):
    return None                        # Hindi shape rules need settings: see classify_first


# ---------------------------------------------------------------------------------------------
# suggestions
# ---------------------------------------------------------------------------------------------
POS_RANK = {"medial": 0, "initial": 1, "final": 2}
GRAPHEME_START = re.compile("[\u0904-\u0914\u0915-\u0939\u0958-\u0961]")


def _extra_targets(checker, w: str) -> set:
    memo = checker.data.setdefault("extra_targets", {})
    gen = (checker.lex.generation, checker.settings_gen)
    if memo.get("gen") != gen or len(memo) > 20000:
        memo.clear()
        memo["gen"] = gen
    if w not in memo:
        memo[w] = {canon(h[3]) for h in extra_suggestions(checker, w)}
    return memo[w]


def rank(checker, w: str, c: str, limit: float = 2.0):
    key = canon(w)
    if c == key or c in _extra_targets(checker, w):
        return None                      # the word itself, or already offered as the shape fix / house spelling
    if c in checker.lex.data.get("misspell", {}):
        return None                      # never suggest a known misspelling (प्रतिक्षा)
    cost, ops = kinds.confusion_distance(key, c, T.CONFUSION, limit=limit)
    if cost == float("inf"):
        return None
    _, cls, op, frm, to, at = ops[0] if ops else (0, "other", "sub", "", "", 0)
    n = len(kinds.units(key, T.CONFUSION))
    pos = "initial" if at == 0 else "final" if at >= n - 2 else "medial"
    f = freq_c(checker, c)
    return (round(cost, 3), POS_RANK[pos], -f, c,
            {"w": c, "op": op, "from": frm, "to": to, "at": at, "cls": cls, "pos": pos, "freq": f, "cost": round(cost, 2)})


def more_candidates(checker, w: str) -> set:
    key = canon(w)
    out = set(checker.index.candidates(key)) if key != w else set()
    out |= set(checker.data.get("loose", {}).get(T.loose_skeleton(key), ()))
    return out


def extra_suggestions(checker, w: str) -> list:
    out = []
    key = canon(w)
    ms = checker.lex.data.get("misspell", {}).get(key)
    if ms:
        out.append((-2, 0, -freq_c(checker, canon(ms[0])), ms[0], _sugg(w, ms[0], "house", freq_c(checker, canon(ms[0])))))
    fixed = T.fixed_word(w)
    if fixed and fixed != w:
        f = freq_c(checker, canon(fixed))
        out.append((-1, 0, -f, fixed, {"w": fixed, "op": "fix", "from": w, "to": fixed, "at": 0, "cls": "shape",
                                       "pos": "medial", "freq": f}))
    return out


# ---------------------------------------------------------------------------------------------
# warnings (text runs)
# ---------------------------------------------------------------------------------------------
PRECOMPOSED_RE = re.compile("[\u0958-\u095F]")
INVISIBLE_RE = T.INVISIBLE_RE
DANDA_TYPED_RE = re.compile(rf"(?<=[{DEV}”’!?)]) *(?:\||[lI](?=\s|$|[”’\"')]))")
LATIN_RE = re.compile("[A-Za-z]+")
OTHER_INDIC_RE = re.compile("[\u0980-\u0DFF]+")
STRAIGHT_QUOTE_RE = re.compile("[\"']")
COLON_IN_WORD_RE = T.COLON_IN_WORD_RE
COLON_FINAL_RE = re.compile(f"([{DEV}]+):(?=\\s|$|[,;।”’])")
COLON_SPACE_RE = re.compile(f"([{DEV}]+): ([{DEV}]+)")
ZW_AFTER_HALANT_RE = re.compile("\u094D[\u200C\u200D]")
PUNCT_BEFORE_DANDA_RE = re.compile("[!?,;:]।")
DOUBLE_DANDA_RE = re.compile("॥")
FULLSTOP_RE = re.compile(f"([{DEV}]+)\\.(?!\\s*\\d)(?!\\.)")
HYPHEN_RE = re.compile(rf"([{DEV}]+)(- +| +-)([{DEV}]+)|--+")
EN_DASH_IN_WORD_RE = re.compile(f"([{DEV}]+)\u2013([{DEV}]+)")
ELLIPSIS_RE = re.compile("\\.{4,}|\u2026")
DOUBLE_PUNCT_RE = re.compile("[,;:!?\u0964]{2,}")
# words a speaker repeats for emphasis, or verb forms (ले ले "take it"): not a reduplication to hyphenate
REPEAT_OK = frozenset("नहीं हाँ हाय ले दे आ जा हो चल न".split())
NEVER_RE = re.compile("(?!)")


def normalize_run(text: str) -> tuple[str, list[str]]:
    """The L0 pipeline for one text run: (normalized text, what changed)."""
    import unicodedata
    what = []
    t = unicodedata.normalize("NFC", text)
    if t != text:
        what.append("precomposed nuqta letter" if PRECOMPOSED_RE.search(text) else "not NFC")
    if INVISIBLE_RE.search(t):
        what.append("invisible character")
        t = INVISIBLE_RE.sub(lambda m: " " if m.group() == "\u00A0" else "", t)
    if DANDA_TYPED_RE.search(t):
        what.append("| or Latin l typed as the danda")
        t = DANDA_TYPED_RE.sub("।", t)
    d = t.translate(T.DEV_DIGITS)
    if d != t:
        what.append("Devanagari digit")
        t = d
    return t, what


def _span(a: str, b: str) -> tuple[int, int, int]:
    i = 0
    while i < min(len(a), len(b)) and a[i] == b[i]:
        i += 1
    j = 0
    while j < min(len(a), len(b)) - i and a[len(a) - 1 - j] == b[len(b) - 1 - j]:
        j += 1
    return i, len(a) - j, len(b) - j


def _dash_fix(checker, a: str, b: str) -> tuple[str, str]:
    """A loose hyphen / en dash between two words: a compound when the IRV writes a-b elsewhere or a == b
    (तितर-बितर, राज्य-राज्य); otherwise a dash between clauses, written as a spaced em dash (reviewed 1 Oct 2026)."""
    if canon(a) == canon(b) or checker.data.get("hyph_tok", Counter()).get(canon(f"{a}-{b}"), 0):
        return f"{a}-{b}", f"Join: {a}-{b}"
    if canon(a) in NO_DASH_AFTER:
        return f"{a} {b}", "A space (no dash after जैसे / कि)"   # reviewed: जैसे- ग्रहण → जैसे ग्रहण
    return f"{a} — {b}", "Write a spaced dash —"


# a finite verb or copula ends a clause: an unspaced – after it is a dash between clauses (थी–पाप, किया–उसके)
CLAUSE_END = frozenset("है हैं था थी थे हो किया किए कहा गया गई गए दिया लिया थीं".split())
# a participle before its noun (बच्चा-छीनी-हुई रीछनी), and जैसे / कि / अर्थात् before what they introduce: a space
PARTICIPLE = frozenset("हुआ हुई हुए".split())
NO_DASH_AFTER = frozenset("जैसे कि अर्थात् यथा".split())


def _en_dash_fix(a: str, b: str) -> tuple[str, str]:
    """An unspaced en dash between words (reviewed): a compound (बारी–सा, संकट–भरा → -), a clause dash after a
    finite verb (→ spaced —), or a plain space after अर्थात्."""
    if canon(a) in CLAUSE_END:
        return f"{a} — {b}", "Write a spaced dash —"
    if canon(a) in NO_DASH_AFTER or canon(a) in PARTICIPLE:
        return f"{a} {b}", "A space"
    return f"{a}-{b}", "Change – to -"


def _double_fix(run: str) -> str | None:
    """hi.punct.double: the mark to keep (reviewed): ?: and !: after a footnote lemma are the convention (None);
    ।: keeps the colon of the lemma; otherwise the strongest mark (? ! । ; , in that order)."""
    if len(run) == 2 and run[0] in "?!" and run[1] == ":":
        return None
    if run == "।:":
        return ":"
    for mark in "?!।;:,":
        if mark in run:
            return mark
    return run[0]


def extra_warnings(checker, seg, next_seg) -> list:
    text = seg.text
    out = []

    def add(kind, s, e, **kw):
        out.append({"kind": kind, "s": s, "e": max(e, s + 1) if s < len(text) else e, "rule": kw.pop("rule", kind), **kw})

    if on(checker, "hi.norm.encoding"):
        norm, what = normalize_run(text)
        if norm != text:
            s, e, e2 = _span(text, norm)
            if e == s:
                e, e2 = s + 1, e2 + 1
            add("hi.norm.encoding", s, e, fix=norm[s:e2], fix_label="Fix the encoding (" + ", ".join(what) + ")",
                label="encoding: " + ", ".join(what))
    if on(checker, "hi.norm.colon-for-visarga"):
        for m in COLON_IN_WORD_RE.finditer(text):
            a = re.search(f"[{DEV}]+$", text[:m.start()])
            b = re.match(f"[{DEV}]+", text[m.end():])
            left, right = (a.group() if a else ""), (b.group() if b else "")
            if (left and right and known_key(checker.lex, canon(left)) and known_key(checker.lex, canon(right))
                    and not known_key(checker.lex, canon(left + "ः" + right)) and aksharas(left) >= 2):
                # a label colon with the space missing (आकर्षण:पनिन्ना, 1SA 1:2, reviewed), not a visarga
                add("hi.norm.colon-for-visarga", m.start(), m.end(), fix=": ", fix_label="Add the space after :",
                    label="no space after the colon")
                continue
            add("hi.norm.colon-for-visarga", m.start(), m.end(), fix="ः", fix_label="Change : to the visarga ः",
                label="colon typed for the visarga")
        for m in COLON_SPACE_RE.finditer(text):
            joined = canon(m.group(1)) + "ः" + canon(m.group(2))
            if known_key(checker.lex, joined) or freq_c(checker, joined):
                add("hi.norm.colon-for-visarga", m.start(1) + len(m.group(1)), m.end(1) + 2, fix="ः",
                    fix_label=f"Change to {joined}", label=f"colon and space typed for the visarga ({joined})")
        for m in COLON_FINAL_RE.finditer(text):
            k = canon(m.group(1)) + "ः"
            n = freq_c(checker, k)
            if n >= 3 and n > checker.data.get("colon_final", Counter()).get(canon(m.group(1)), 0):
                add("hi.norm.colon-for-visarga", m.end() - 1, m.end(), fix="ः", fix_label=f"Change to {m.group(1)}ः",
                    label=f"colon typed for the visarga ({k} is the word)")
    if on(checker, "hi.norm.zero-width"):
        zw = list(ZW_AFTER_HALANT_RE.finditer(text))
        if zw:                                           # Q18: strip; one fix for the whole run
            s0, e0 = zw[0].start() + 1, zw[-1].end()
            add("hi.norm.zero-width", s0, e0, panel=True, fix=text[s0:e0].replace("\u200d", "").replace("\u200c", ""),
                fix_label=f"Remove the joiner{'s' if len(zw) > 1 else ''} ({len(zw)})",
                label="ZWJ/ZWNJ after a halant: the IRV writes its conjuncts without it (Q18)")
    danda = [m.span() for m in DANDA_TYPED_RE.finditer(text)]
    if on(checker, "hi.norm.latin-in-text"):
        for m in LATIN_RE.finditer(text):
            if not any(a <= m.start() < b for a, b in danda):
                add("hi.norm.latin-in-text", m.start(), m.end(), label="Latin letters in Devanagari text")
    if on(checker, "hi.shape.mixed-script"):
        for m in OTHER_INDIC_RE.finditer(text):
            add("hi.shape.mixed-script", m.start(), m.end(), label="letter of another Indic script")
    if on(checker, "hi.norm.quotes"):
        for m in STRAIGHT_QUOTE_RE.finditer(text):
            add("hi.norm.quotes", m.start(), m.end(), label="straight quote (the IRV uses “ ” ‘ ’)")
    if on(checker, "hi.shape.hyphen"):
        for m in HYPHEN_RE.finditer(text):
            if m.group(1):
                fix, label = _dash_fix(checker, m.group(1), m.group(3))
                add("hi.shape.hyphen", m.start(), m.end(), panel=True, fix=fix, fix_label=label, label="loose hyphen")
            else:
                add("hi.shape.hyphen", m.start(), m.end(), panel=True, fix="-", fix_label="One hyphen", label="doubled hyphen")
    if on(checker, "hi.punct.en-dash-in-word"):
        for m in EN_DASH_IN_WORD_RE.finditer(text):
            fix, label = _en_dash_fix(m.group(1), m.group(2))
            add("hi.punct.en-dash-in-word", m.start(), m.end(), panel=True, fix=fix, fix_label=label,
                label="en dash between words")
    if on(checker, "hi.punct.ellipsis"):
        for m in ELLIPSIS_RE.finditer(text):
            add("hi.punct.ellipsis", m.start(), m.end(), fix="...", fix_label="Three dots ...", label="ellipsis (Q19)")
    if on(checker, "hi.punct.double"):
        for m in DOUBLE_PUNCT_RE.finditer(text):
            if PUNCT_BEFORE_DANDA_RE.fullmatch(m.group()):
                continue                                   # !। ,। :। : hi.punct.punct-before-danda
            keep = _double_fix(m.group())
            if keep is None or keep == m.group():
                continue
            add("hi.punct.double", m.start(), m.end(), fix=keep, fix_label=f"Keep {keep}", label="doubled punctuation")
    if on(checker, "hi.punct.punct-before-danda"):
        for m in PUNCT_BEFORE_DANDA_RE.finditer(text):
            keep = m.group()[0] if m.group()[0] in "!?" else "।"     # Q10: one mark only (पृथक:। → पृथक।)
            add("hi.punct.punct-before-danda", m.start(), m.end(), panel=True, fix=keep,
                fix_label=f"Keep only {keep}", label=f"{m.group()} (punctuation before the danda)")
    if on(checker, "hi.punct.double-danda"):
        for m in DOUBLE_DANDA_RE.finditer(text):
            add("hi.punct.double-danda", m.start(), m.end(), panel=True, label="॥ in the text")
    if on(checker, "hi.punct.fullstop-for-danda"):
        abbr = checker.data.get("abbreviations", T.ABBREVIATION_SEED)
        for m in FULLSTOP_RE.finditer(text):
            if canon(m.group(1)) in abbr:
                continue
            mid = re.match(r"\s*[(,;]", text[m.end():])          # mid-sentence (गेर्शोम. (निर्ग. 2:22),): no mark at all
            add("hi.punct.fullstop-for-danda", m.end() - 1, m.end(), panel=True, fix="" if mid else "।",
                fix_label="Remove the full stop" if mid else "Change . to ।",
                label=f"full stop after {m.group(1)} (not an abbreviation the IRV uses)")
    hyph, space = checker.data.get("redup_hyph", Counter()), checker.data.get("redup_space", Counter())
    red_on, rep_on = on(checker, "hi.style.reduplication-hyphen"), on(checker, "hi.style.repeated-word")
    if red_on or rep_on:
        for w1, w2, a, b in kinds.adjacent_pairs(text, T.TOKEN_RE):
            k = canon(w1)
            if k != canon(w2):
                continue
            if red_on and k not in REPEAT_OK:              # reviewed: distributive / intensive reduplication is hyphenated
                add("hi.style.reduplication-hyphen", a, b, panel=True, fix=f"{w1}-{w2}", fix_label=f"Change to {w1}-{w2}",
                    label=f"reduplication: {k}-{k} (the IRV writes it {hyph[k]}× hyphenated, {space[k]}× spaced)")
            elif rep_on and k not in REPEAT_OK:
                add("hi.style.repeated-word", b - len(w2), b, panel=True, label="word repeated")
    return out


def token_warning(checker, text, w, a, b, s):
    return None


# ---------------------------------------------------------------------------------------------
# chapter-level: quotes that do not balance (a quotation re-opened at a new paragraph continues)
# ---------------------------------------------------------------------------------------------
PARAGRAPH_STYLES = {"p", "m", "pi", "pi1", "pi2", "q", "q1", "q2", "q3", "q4", "nb", "pc", "li", "li1", "li2", "d", "s", "s1"}


def chapter_leads(checker, book, n, blocks) -> None:
    # a one-letter fragment of a word split by a colon (दु:ख → ख) is not a separate finding: colon-for-visarga is
    for block in blocks:
        for seg in block["segs"]:
            if seg.get("k") == "t" and seg.get("tokens"):
                t = seg["t"]
                seg["tokens"] = [x for x in seg["tokens"] if not (x.get("rule") == "hi.shape.single-letter"
                                                                  and x["s"] > 0 and t[x["s"] - 1] == ":")]
    if not on(checker, "hi.punct.quotes-unbalanced"):
        return
    # only a closing ” with nothing open is flagged: an open “ may run on into the next chapter (reviewed: the
    # IRV re-opens “ at each paragraph of a speech that continues past the chapter end)
    opens = 0
    for block in blocks:
        for seg in block["segs"]:
            if seg.get("k") != "t" or not seg.get("checked") or seg.get("stream", 0) != 0 or "warnings" not in seg:
                continue
            if seg.get("ctx") not in QUOTE_CONTEXTS:
                continue
            for i, ch in enumerate(seg["t"]):
                if ch == "“":
                    opens += 1
                elif ch == "”":
                    if opens:
                        opens -= 1
                    else:
                        seg["warnings"].append({"kind": "hi.punct.quotes-unbalanced", "s": i, "e": i + 1, "panel": True,
                                                "rule": "hi.punct.quotes-unbalanced",
                                                "label": "” without an opening “ in this chapter"})


# ---------------------------------------------------------------------------------------------
# leads: spelling consistency, joining, agreement and the bigram rules
# ---------------------------------------------------------------------------------------------
M_TO_F = {"का": "की", "की": "का"}


def _flip_marker(w: str) -> str:
    if w in M_TO_F:
        return M_TO_F[w]
    if w.endswith("ा"):
        return w[:-1] + "ी"
    if w.endswith("ी"):
        return w[:-1] + "ा"
    return w


def style_choice(checker, key: str) -> str:
    return checker.settings.get("style", {}).get(key, "auto")


def consistency_lead(checker, w: str):
    key = (checker.lex.generation, checker.settings_gen, id(checker.data.get("clusters")))
    memo = checker.data.get("cons_memo")
    if memo is None or memo[0] != key:
        memo = checker.data["cons_memo"] = (key, {})
    if w not in memo[1]:
        memo[1][w] = _consistency_lead(checker, w)
    return memo[1][w]


def _ye_target(checker, k: str) -> str | None:
    """Q4: the standard -ए / -ई form of a -ये / -यी word, when that form is attested (IRV or OV)."""
    if k in T.YE_STEM_WORDS:
        return None
    for a, bs in (("यें", ("एँ", "एं")), ("यीं", ("ईं",)), ("ये", ("ए",)), ("यी", ("ई",)), ("येँ", ("एँ",))):
        if k.endswith(a) and len(k) > len(a):
            stem = k[:-len(a)]
            if known_key(checker.lex, stem + "य") and len(stem) > 1:
                return None                      # the य belongs to the stem (गाय + ें)
            for b in bs:
                if freq_c(checker, stem + b):
                    return stem + b
            return None
    return None


CANDRA_FINAL_RE = re.compile("(?<=[\u093E\u0941\u0942\u0905\u0906\u0909\u090A])\u0902$")


def _chandrabindu_target(checker, k: str, forms: dict | None) -> str | None:
    """Q2: ँ wherever no vowel sign rises above the headline.  Within a cluster, the ँ form; and a word-final
    ं after ा ु ू अ आ उ ऊ (तुरहियां → तुरहियाँ).  Never when both forms are OV words (हंस / हँस)."""
    lex = checker.lex
    if forms:
        best = max((f for f in forms if f.count("\u0901") > k.count("\u0901")), key=lambda f: (forms[f], f), default=None)
        if best:
            if lex.lemma_count.get(k, 0) >= 3 and lex.lemma_count.get(best, 0) >= 3:
                return None
            return best
    if CANDRA_FINAL_RE.search(k):
        return k[:-1] + "\u0901"
    return None


def _consistency_lead(checker, w: str):
    s = checker.settings["consistency"]
    lex = checker.lex
    k = canon(w)
    if k in lex.data.get("misspell_keep", ()):
        return None                              # reviewed: keep as written (रुपये, पहिये, पाये …)
    clusters = checker.data.get("clusters", {})
    for kind, fn in T.SKELETONS.items():
        rid = CONSISTENCY_RULE[kind]
        if not on(checker, rid):
            continue
        sk = fn(k)
        forms = clusters.get(kind, {}).get(sk)
        choice = style_choice(checker, f"{kind}:{sk}")
        if choice == "off":
            continue
        policy = None
        if choice == "auto" and kind == "ye":
            policy = _ye_target(checker, k)
        elif choice == "auto" and kind == "chandrabindu":
            policy = _chandrabindu_target(checker, k, forms if forms and k in forms else None)
        if policy:
            forms = dict(forms or {k: irv_c(checker, k)})
            target = policy
            ranked = sorted(forms.items(), key=lambda kv: (-kv[1], kv[0]))
            why = ["IRV forms: " + ", ".join(f"{f} {c}×" for f, c in ranked[:5]),
                   "house style decided in the review: " + ("-ए / -ई (Q4)" if kind == "ye" else "ँ (Q2)")]
            return _cons(kind, rid, w, target, why, forms.get(k, 0), forms.get(target, freq_c(checker, target)))
        if kind in ("ye", "chandrabindu") and choice == "auto":
            continue                              # the policy decides these; a majority alone does not
        if not forms or k not in forms:
            continue
        ranked = sorted(forms.items(), key=lambda kv: (-kv[1], kv[0]))
        total = sum(forms.values())
        if choice == "auto":
            top, n_top = ranked[0]
            close = len(ranked) > 1 and ranked[1][1] >= s.get("close", 0.8) * n_top
            if k == top or close or forms[k] / total >= s.get("minority_max", 0.25):
                continue
            if lex.lemma_count.get(k, 0) >= s.get("ov_veto", 3):      # the minority form is an OV word in its own right
                continue
            target = top
        else:
            if k == choice or choice not in forms:
                continue
            target = choice
        why = ["IRV forms: " + ", ".join(f"{f} {c}×" for f, c in ranked[:5])]
        if choice != "auto":
            why.append(f"house style chosen: {choice}")
        return _cons(kind, rid, w, target, why, forms[k], forms[target])
    return None


def _cons(kind, rid, w, target, why, n_w, n_t):
    return {"kind": "consistency", "rule": rid, "proposed": target, "basis": kind, "strength": "panel",
            "panel": True, "w2": "≈" + kind, "pair": w, "why": why,
            "tag": f"{kind} {n_w}:{n_t}", "title": f"spelling consistency ({kind})",
            "issue_type": "Spelling consistency", "ignore_label": f"Stop flagging {w} for {kind} consistency"}


# after the noun, a word that shows a later noun is the head (परमेश्वर की … शिकायत, दिन भर की रोटी, उद्धार करने की
# शक्ति, बड़ाई मारना): the marker agrees with that head, not with the next word (reviewed: 18 of 25)
HEAD_LATER = frozenset("का की के भर रूपी सम्बन्धी संबंधी".split())
COPULA = frozenset("हो है हैं था थी थे हुआ हुई हुए होगा होगी".split())
OBLIQUE_BEFORE = frozenset("का की के पर में".split())
OBLIQUE_MARK = {"मेरा": "मेरे", "मेरी": "मेरे", "तेरा": "तेरे", "तेरी": "तेरे", "हमारा": "हमारे", "हमारी": "हमारे",
                "तुम्हारा": "तुम्हारे", "तुम्हारी": "तुम्हारे", "अपना": "अपने", "अपनी": "अपने", "का": "के", "की": "के"}


def _oblique_marker(w1: str) -> str:
    if w1 in OBLIQUE_MARK:
        return OBLIQUE_MARK[w1]
    return w1[:-1] + "े" if w1.endswith(("ा", "ी")) else w1


def agreement_leads(checker, w1: str, w2: str, w3: str = ""):
    a = checker.settings["agreement"]
    out = []
    g = T.GENDER_MARKERS.get(w1)
    if (g and on(checker, "hi.gram.genitive-gender") and w2 not in T.GENDER_STOP and not w2.endswith("ों")
            and w3 not in HEAD_LATER and w3 not in COPULA and not w3.endswith(("ना", "ने", "वाला", "वाली", "वाले"))):
        m, f = checker.lex.data.get("gender", {}).get(w2, (0, 0))
        tot = m + f
        if tot >= a.get("gender_min", 20):
            noun_g = "M" if m / tot >= a.get("gender_pct", 0.95) else "F" if f / tot >= a.get("gender_pct", 0.95) else None
            proposed = None
            if w3 in T.POSTPOSITIONS:
                # before a postposition the noun is usually inside a phrase whose head comes later (मूसा की परमेश्वर से
                # शिकायत, स्त्री का कलीसिया में बातें करना); only a feminine marker before a masculine noun is a fault, and
                # it wants the oblique (मेरी उद्धार का → मेरे, तुम्हारी सिर पर → तुम्हारे, अपनी प्रेमी की → अपने)
                if noun_g == "M" and g == "F" and w3 in OBLIQUE_BEFORE:
                    proposed = _oblique_marker(w1)
            elif noun_g and noun_g != g:
                proposed = _flip_marker(w1)
            if proposed and proposed != w1:
                out.append({"kind": "agreement", "rule": "hi.gram.genitive-gender", "proposed": proposed,
                            "basis": "gender", "strength": "panel", "panel": True, "w2": w2, "on": 1,
                            "why": [f"{w2} is {'masculine' if noun_g == 'M' else 'feminine'} in {max(m, f)} of {tot} "
                                    f"corpus contexts (M {m} · F {f})"
                                    + (f"; before {w3} the marker is oblique" if noun_g == "M" and w3 in T.POSTPOSITIONS else "")],
                            "tag": f"gender {m}:{f}", "title": f"gender agreement with {w2}", "issue_type": "Grammar"})
    if w2 in T.POSTPOSITIONS and w1.endswith("ा") and on(checker, "hi.gram.oblique") and w1 not in T.GENDER_MARKERS:
        stem = w1[:-1]
        d, o = checker.lex.data.get("oblique", {}).get(stem, (0, 0))
        if o >= a.get("oblique_min", 10) and d / (d + o) <= a.get("oblique_direct_max", 0.10):
            out.append({"kind": "agreement", "rule": "hi.gram.oblique", "proposed": stem + "े", "basis": "oblique",
                        "strength": "panel", "panel": True, "w2": w2, "on": 1,
                        "why": [f"before a postposition the corpus has {stem}े {o}× and {w1} {d}×"],
                        "tag": f"oblique {d}:{o}", "title": f"oblique case before {w2}", "issue_type": "Grammar"})
    return out


def join_lead(checker, k1: str, k2: str, joined_token: bool):
    """hi.style.postposition-joining: the IRV writes this pronoun + postposition pair mostly the other way."""
    if not on(checker, "hi.style.postposition-joining"):
        return None
    s = checker.settings["consistency"]
    j, sp = checker.data.get("join_n", {}).get((k1, k2), (0, 0))
    mine, other = (j, sp) if joined_token else (sp, j)
    if other < s.get("join_min", 3) or mine / (j + sp) >= s.get("minority_max", 0.25):
        return None
    proposed = f"{k1} {k2}" if joined_token else k1 + k2
    return {"kind": "consistency", "rule": "hi.style.postposition-joining", "proposed": proposed, "basis": "joining",
            "strength": "panel", "panel": True, "w2": "≈joining", "pair": k1 + k2 if joined_token else f"{k1} {k2}",
            "why": [f"the IRV writes {k1}{k2} {j}× and {k1} {k2} {sp}×"], "tag": f"joining {j}:{sp}",
            "title": "pronoun + postposition", "issue_type": "Spelling consistency",
            "ignore_label": f"Stop flagging {k1}{k2} / {k1} {k2}"}


def _bigram_lead(rule: kinds.BigramRule, proposed: str, w1: str, w2: str) -> dict:
    return {"kind": "agreement", "rule": rule.id, "proposed": proposed, "basis": "bigram", "strength": "panel",
            "panel": True, "w2": w2 if rule.fix == "left" else w1, "why": [rule.why],
            "pair": f"{w1} {w2}", "tag": f"{w1} {w2}", "title": rule.why, "issue_type": "Grammar"}


def line_leads(checker, toks: list, line) -> list:
    leads = []
    split_index = SPLIT_INDEX
    for i, (w, r, s, e, adj, _) in enumerate(toks):
        k = canon(w)
        c = consistency_lead(checker, w)
        if c:
            leads.append({**c, "w1": w, "run": r, "s": s, "e": e, "ignored": (w, c["w2"]) in checker.ignored_pairs})
        if k in split_index:
            jl = join_lead(checker, *split_index[k], joined_token=True)
            if jl:
                leads.append({**jl, "w1": w, "run": r, "s": s, "e": e, "ignored": (w, jl["w2"]) in checker.ignored_pairs})
        if i + 1 >= len(toks) or not toks[i + 1][4]:
            continue
        w2, r2, s2, e2 = toks[i + 1][:4]
        k2 = canon(w2)
        if (k, k2) in JOIN_SET and r2 == r:
            jl = join_lead(checker, k, k2, joined_token=False)
            if jl:
                leads.append({**jl, "w1": w, "run": r, "s": s, "e": e2, "ignored": (w, jl["w2"]) in checker.ignored_pairs})
        k3 = canon(toks[i + 2][0]) if i + 2 < len(toks) and toks[i + 2][4] else ""
        for g in agreement_leads(checker, k, k2, k3):
            g.pop("on")
            leads.append({**g, "w1": w, "run": r, "s": s, "e": e, "ignored": (w, w2) in checker.ignored_pairs})
        w3 = canon(toks[i + 2][0]) if i + 2 < len(toks) and toks[i + 2][4] else ""
        for rule, proposed in kinds.bigram_rules(k, k2, T.BIGRAM_RULES, w3):
            if not on(checker, rule.id):
                continue
            lead = _bigram_lead(rule, proposed, w, w2)
            if rule.fix == "left":
                leads.append({**lead, "w1": w, "run": r, "s": s, "e": e, "ignored": (w, w2) in checker.ignored_pairs})
            else:
                leads.append({**lead, "w1": w2, "run": r2, "s": s2, "e": e2, "ignored": (w2, w) in checker.ignored_pairs})
    return leads


# ---------------------------------------------------------------------------------------------
# reports, rules page, settings spec
# ---------------------------------------------------------------------------------------------
STATUS_RULE = {"unknown": "hi.lex.unknown"}


def report_block(checker, extra: dict, block: dict) -> None:
    c = extra.setdefault("rules", Counter())
    for seg in block["segs"]:
        if seg.get("k") != "t" or not seg.get("checked"):
            continue
        for t in seg.get("tokens", []):
            if t.get("ignored_once") or t["status"] == "ignored":
                continue
            rid = t.get("rule") or STATUS_RULE.get(t["status"])
            if rid:
                c[rid] += 1
            elif t["status"] in ("irv_ok", "inflected_ok", "compound"):
                c["status:" + t["status"]] += 1
        for lead in seg.get("sandhi", []):
            if not lead.get("ignored"):
                c[lead.get("rule", "lead")] += 1
        for w in seg.get("warnings", []):
            c[w.get("rule") or GENERIC_WARNING_RULE.get(w["kind"], w["kind"])] += 1


def rule_hits(checker) -> tuple[Counter, int]:
    total, n = Counter(), 0
    for _, (_, rep) in list(checker._reports.items()):
        n += 1
        total.update(rep.get("extra", {}).get("rules", {}))
    return total, n


def _rule_of_hit(rid: str) -> str:
    return SHAPE_RULE_SWITCH.get(rid, rid)


def rules_payload(checker) -> dict:
    hits, books = rule_hits(checker)
    by_switch, detail = Counter(), defaultdict(Counter)
    for rid, n in hits.items():
        sw = _rule_of_hit(rid)
        by_switch[sw] += n
        if sw != rid:
            detail[sw][rid] += n
    rows = [{"id": rid, "group": g, "label": label, "inline": inline, "default": dflt, "enabled": on(checker, rid),
             "hits": by_switch.get(rid, 0), "detail": dict(detail.get(rid, {}))}
            for rid, (g, label, inline, dflt) in RULES.items()]
    statuses = {k[7:]: v for k, v in hits.items() if k.startswith("status:")}
    path = checker.lex.data.get("misspell_path")
    joins = sorted(((f"{a}{b}", j, s) for (a, b), (j, s) in checker.data.get("join", {}).items()), key=lambda x: -min(x[1], x[2]))
    return {"lang": "hi", "rules": rows, "statuses": statuses, "books_counted": books, "errors": [], "file": path,
            "misspellings": len(checker.lex.data.get("misspell", {})), "lexicon_gen": checker.lex.generation,
            "joining_both_ways": [{"pair": p, "joined": j, "split": s} for p, j, s in joins[:20]],
            "abbreviations": sorted(checker.data.get("abbreviations", ())),
            "message": f"{len(checker.lex.data.get('misspell', {}))} known misspellings loaded from {path}"}


def style_toggles(checker) -> list[dict]:
    s = checker.settings["consistency"]
    out = [{"id": "unknown_color", "label": "Unknown (not verifiable) words are shown",
            "options": [{"value": "grey", "label": "light grey (the review found most unknown words correct)"},
                        {"value": "red", "label": "red"}], "default": "grey", "close": False, "total": 10 ** 9, "fixed": True}]
    for kind, groups in checker.data.get("clusters", {}).items():
        for sk, forms in groups.items():
            ranked = sorted(forms.items(), key=lambda kv: (-kv[1], kv[0]))
            if len(ranked) < 2 or ranked[1][1] < s.get("style_min", 20):
                continue
            (a, na), (b, nb) = ranked[0], ranked[1]
            close = nb >= s.get("close", 0.8) * na
            auto = "undecided, forms within 20 % — please choose" if close else f"majority: {a}"
            opts = [{"value": "auto", "label": f"auto ({auto})"}] + \
                   [{"value": f, "label": f"{f}  ({c}×)"} for f, c in ranked[:4]] + [{"value": "off", "label": "no lead"}]
            out.append({"id": f"{kind}:{sk}", "label": f"{kind}: " + " / ".join(f"{f} {c}" for f, c in ranked[:3]),
                        "options": opts, "default": "auto", "close": close, "total": na + nb})
    out.sort(key=lambda t: (not t["close"], -t["total"], t["id"]))
    return out[:60]


def info_extra(checker) -> dict:
    hits, _ = rule_hits(checker)
    by_switch = Counter()
    for rid, n in hits.items():
        by_switch[_rule_of_hit(rid)] += n
    return {"unknown_color": style_choice(checker, "unknown_color") if style_choice(checker, "unknown_color") != "auto" else "grey",
            "settings_extra": {
        "legend": "Hindi checks (docs/HINDI_RULES.md)",
        "rules": [{"id": rid, "label": f"{label}{'' if inline else ' — listed, not underlined'}", "default": dflt,
                   "count": by_switch.get(rid, 0)} for rid, (g, label, inline, dflt) in RULES.items()],
        "style": style_toggles(checker),
        "numbers": [{"group": "lex", "key": "irv_accept_min", "label": "Accept a word used at least this often in the IRV",
                     "default": 5, "min": 1},
                    {"group": "consistency", "key": "minority_max", "label": "Consistency: flag a form below this share (0–1)",
                     "default": 0.25, "min": 0}],
        "reload": "Reload known_misspellings.tsv",
    }}


# ---------------------------------------------------------------------------------------------
# UI labels
# ---------------------------------------------------------------------------------------------
LABELS = {
    "status": {"malformed": "Devanagari spelling fault", "irv_ok": "not in the OV, but common in the IRV (accepted)",
               "unknown": "not verifiable (not in the OV, no known word + ending)",
               "inflected_ok": "known word + ending", "suspect": "rare form of a much commoner word",
               "archaic": "older (OV) spelling"},
    "cls": {"nuqta": "़ nuqta", "flap": "ड़ / ढ़", "nasal": "ं / ँ", "pancham": "pancham / anusvara",
            "vowel_length": "vowel length", "ye_ending": "-ए / -ये", "halant": "final ्", "reph": "र् position",
            "ri": "ृ / ्र", "sibilant": "श / ष / स", "na_na": "न / ण", "ba_va": "ब / व", "conjunct": "conjunct",
            "aspiration": "aspiration", "retroflex": "त/ट द/ड", "transpose": "swap", "other": "", "shape": "fix",
            "house": "house spelling", "archaic": "IRV form"},
    "warn": {rid: v[1] for rid, v in RULES.items()} | {
        "double_space": "double space", "no_space_after_punct": "no space after the punctuation mark",
        "space_before_punct": "space before punctuation", "double_punct": "double punctuation",
        "zero_width": "invisible character", "digits_in_verse": "digits in verse text"},
    "legend": [["unknown", "not verifiable"], ["malformed", "spelling fault"], ["malformed panel", "listed fault"],
               ["irv_ok", "IRV word"], ["inflected_ok", "word + ending"], ["compound", "compound?"],
               ["lead-panel", "consistency / grammar lead"], ["warn", "warning"], ["learned", "changed before"], ["ignored", "ignored"]],
    "counts_leads": "leads",
    "leads_heading": "Consistency and grammar leads",
    "ignored_pairs_heading": "Ignored leads",
    "book_word_statuses": "unknown,compound,malformed,irv_ok,inflected_ok,suspect,archaic",
    "quiet_statuses": ["irv_ok", "inflected_ok", "compound"],
    "issue_sections": [["suspect", "Rare forms of common words"], ["archaic", "Older (OV) spellings"]],
    "rebuild": "Rebuild dictionary_hi from the OV Hindi + extra_words.txt",
    "rules_link": "Hindi rules",
    "issue_types": ["Possible typo", "Confirmed typo", "Spelling consistency", "Nuqta / chandrabindu / pancham", "Grammar",
                    "Punctuation", "Encoding", "Font / rendering", "Name consistency", "Reviewer decision", "Other"],
    "settings_warn": {"double_space": "double space", "digits": "digits in verse text", "defects": "(not used for Hindi)",
                      "space_before_note_close": "space before a footnote end", "double_punct": "double punctuation (,, ।।)",
                      "no_space_after_punct": "no space after , ; ! ? ।", "space_before_punct": "space before । or punctuation",
                      "repeated_word": "(see Style below)"},
    "word_re": f"[{T.TOKEN_CH}]+(?:-[{T.TOKEN_CH}]+)*",
    "zw_strip": "[\u200B\uFEFF\u2060]",                       # the editor keeps ZWJ / ZWNJ: they are in the text
}


def related_norm(lex):
    return canon


def build_args(config) -> list[str]:
    args = ["--lang", "hi", "--out-dir", str(config.get("dict_dir") or REPO_ROOT / "dictionary_hi")]
    if config.get("irv_dir"):
        args += ["--irv-dir", str(config["irv_dir"])]
    return args


FORBIDDEN_IN_TEXT = {"\\": "backslash", "\r": "CR", "\n": "LF", "\t": "TAB", "\u200B": "U+200B", "\uFEFF": "U+FEFF"}

PROFILE = Language(
    code="hi", name="Hindi", script=T.SCRIPT, file_re=T.FILE_RE,
    dict_dir_default=REPO_ROOT / "dictionary_hi", data_dir_default=APP_DIR / "data_hi", port_default=8768,
    html_lang="hi", app_title="Hindi IRV spell-check editor",
    token_re=T.TOKEN_RE, grapheme_start=GRAPHEME_START, default_settings=DEFAULT_SETTINGS,
    malformed=malformed, rank=rank, extra_suggestions=extra_suggestions,
    defect_patterns=[], punct_run_re=re.compile("[,;:!?।]{2,}"), ok_punct_runs={"?!", "!?"},
    no_space_after_re=re.compile(rf"[,;!?।](?=[{DEV}])"),
    space_before_re=re.compile(rf"(?<=[{T.TOKEN_CH}\d)”’])( +)(?=[,;:!?।])"),
    token_warning=token_warning, related_norm=related_norm, build_args=build_args,
    irv_dir_default=str(paths.irv_dir("hi")),
    more_candidates=more_candidates, classify_first=classify_first, classify_extra=classify_extra,
    line_leads=line_leads, chapter_leads=chapter_leads, extra_warnings=extra_warnings,
    on_index_book=on_index_book, on_build_index=on_build_index, load_lexicon_extra=load_lexicon_extra,
    info_extra=info_extra, rules_payload=rules_payload, report_block=report_block,
    rules_page="rules_hi.html", labels=LABELS,
    font_family="Noto Sans Devanagari", font_fallback=("Nirmala UI",),
    features={"sandhi": False, "rule_pack": True, "consistency": True, "encoding": True},
    zero_width_re=NEVER_RE, forbidden_in_text=FORBIDDEN_IN_TEXT,
    detect_file_re=T.DETECT_FILE_RE, detect_letters_re=T.DETECT_LETTERS_RE,
    line_ending="\n",                                   # the IRV Hindi files have LF line ends (facts M1)
)
