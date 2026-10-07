"""Odia profile for the IRV editor (language code "or"; the module is odia.py because `or` is a Python keyword).
Rules: docs/ODIA_RULES.md (`or.*` ids), measured in docs/ODIA_CORPUS_FACTS.md.

Tables live in odia_tables.py, the algorithms in qa_app/kinds.py; this module wires them into the checker's
hooks.  Nothing here is used when the editor runs another language.

The algorithm, in the order a word goes through it (rules §4.1):
  1. the lookup key: NFC, ZWJ/ZWNJ removed, ଵ read as ୱ, the apostrophe as ’ (the OV's ପରମେଶ୍ଵର and the IRV's
     ପରମେଶ୍ୱର are one word); the raw text is kept, so underlines and edits land on what is written
  2. a known misspelling or a divine-name variant → malformed (red)
  3. an Odia sign-sequence error (§3: ଅାମ୍ଭେ, ମୃତ୍ୟୁୁ, ପଗଡି଼, ଡ଼ାକିଲେ, ଖଡ଼୍ଗ, a stray joiner …) → malformed, with the fix
  4. an OV word (by key), an extra word or a name, or a hyphenated compound of known words → ok (a rare OV word
     next to a much commoner form → suspect, listed)
  5. a house-style variant of an OV word (ଙ୍କ / ଂକ, final ୍) → ok; the consistency rules decide
  6. a known word + up to two listed endings (ଲୋକ+ମାନଙ୍କ+ଠାରୁ, କର+ିଲେ) → inflected_ok (grey); used ≥ 5× in the IRV →
     irv_ok (grey)
  7. two known words run together → compound; otherwise unknown (grey), with ranked suggestions
Then, over adjacent words: spelling consistency per word (vowel length, ଡ/ଡ଼, ୍ବ/୍ୱ, ଯ/ୟ, ଲ/ଳ, pancham, final ୍,
the joiner), a bound ending written apart, the honorific case of a person (ଯୀଶୁକୁ → ଯୀଶୁଙ୍କୁ), and pronoun + copula.
"""
from __future__ import annotations

import csv
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from .. import paths
from .. import kinds
from . import APP_DIR, REPO_ROOT, Language
from . import odia_tables as T

ORI = T.ORI
canon = T.canon

# ---------------------------------------------------------------------------------------------
# rule catalogue: id -> (group, plain-language label, inline?, on by default?)
# Inline = mechanical (an encoding or a sign sequence Odia does not allow); everything else is listed until a
# reviewer has labelled a sample (as for Hindi and Malayalam).
# ---------------------------------------------------------------------------------------------
RULES: dict[str, tuple[str, str, bool, bool]] = {
    "or.norm.encoding": ("Encoding", "Encoding: not NFC, invisible or control character (ZWSP, BOM, NBSP), ASCII | "
                                     "typed for the danda ।", True, True),
    "or.norm.va-letter": ("Encoding", "ଵ (U+0B35) where the IRV writes ୱ (U+0B71): ପରମେଶ୍ଵର → ପରମେଶ୍ୱର (they look alike)",
                          True, True),
    "or.norm.colon-for-visarga": ("Encoding", "ASCII colon typed for the visarga ଃ (ଦୁ:ଖ → ଦୁଃଖ)", True, True),
    "or.norm.apostrophe": ("Encoding", "Straight ' inside a word where the IRV writes ’ (ତା'ପରେ → ତା’ପରେ)", True, True),
    "or.norm.zwnj-halant": ("Encoding", "ZWNJ after a virama where the IRV writes ZWJ (ଦ୍\u200Cବ → ଦ୍\u200Dବ)", False, True),
    "or.norm.latin-in-text": ("Encoding", "Latin letter inside Odia text", False, True),
    "or.norm.quotes": ("Encoding", "Straight quote \" (the IRV uses curly quotes “ ”)", True, True),
    "or.norm.mixed-digits": ("Encoding", "One number written with Odia and ASCII digits (୧0 → ୧୦)", True, True),
    "or.shape.errors": ("Shape", "Impossible sign sequence (§3): ଅା for ଆ, ଏୗ for ଐ, a bare length mark ୖ ୗ, a doubled "
                                 "vowel sign (ମୃତ୍ୟୁୁ), the nukta after the vowel sign (ପଗଡି଼), ଡ଼ at the start of a word "
                                 "or inside a conjunct (ଖଡ଼୍ଗ), ୟ at the start, ୍ଯ for ୍ୟ, ଣ୍ତ for ଣ୍ଡ, ଁଁ, a joiner that "
                                 "joins nothing, a virama, sign or nasal where Odia does not allow it", True, True),
    "or.shape.warnings": ("Shape", "Unusual word start (ଙ ଞ ଣ ଳ) or four consonants in one conjunct, in a word not in "
                                   "the dictionary", False, True),
    "or.shape.single-letter": ("Shape", "A one-letter word other than ଓ ନ ଏ ତ ଆ (ସ୍ଥାନ ର → ସ୍ଥାନର)", False, True),
    "or.shape.long-token": ("Shape", "A word of 24+ characters (a missing space?) not in the dictionary", False, True),
    "or.shape.mixed-script": ("Shape", "Devanagari, Bengali or other Indic letter in Odia text (the danda । is shared "
                                       "and fine)", True, True),
    "or.shape.hyphen": ("Shape", "Loose hyphen: a space inside a compound (ପାପ- କର୍ମକାରୀ → ପାପ-କର୍ମକାରୀ)", False, True),
    "or.punct.space-before-danda": ("Punctuation", "Space before । , ; : (the IRV writes କଲେ।)", True, True),
    "or.punct.question-space": ("Punctuation", "? or ! straight after a word: the IRV writes a space before them "
                                               "(କି କଲ ? — 3,235 against 12)", False, True),
    "or.punct.double": ("Punctuation", "Doubled punctuation (,, ।। ।,)", True, True),
    "or.norm.spacing": ("Punctuation", "Double space, or no space after , ; ! ? ।", True, True),
    "or.punct.fullstop-for-danda": ("Punctuation", "Full stop . after a word that is not an abbreviation (ଆଦି. 3:15 is "
                                                   "fine): use the danda ।", False, True),
    "or.punct.quotes-unbalanced": ("Punctuation", "A closing ” without an opening “ in the chapter", False, True),
    "or.lex.unknown": ("Lexicon", "Word not in the dictionary (OV Odia + extra words), not a known word + ending and "
                                  "rare in the IRV: not verifiable, shown grey", True, True),
    "or.lex.rare-near-common": ("Lexicon", "Rare word (≤ 2× in OV + IRV) next to a much commoner near form "
                                           "(ସେହୀ → ସେହି, ଆଡେ → ଆଡ଼େ, ସ୍ବରୂପ → ସ୍ୱରୂପ)", False, True),
    "or.lex.known-misspelling": ("Lexicon", "Listed in known_misspellings.tsv (ସେହୀ → ସେହି, କୋଣସି → କୌଣସି, "
                                            "ପରିକ୍ଷା → ପରୀକ୍ଷା)", True, True),
    "or.style.divine-names": ("Lexicon", "Divine name spelled another way than the IRV's (ସଦାପ୍ରଭୁ, ପରମେଶ୍ୱର, ଯୀଶୁ, "
                                         "ଖ୍ରୀଷ୍ଟ, ପ୍ରଭୁ, ଈଶ୍ୱର)", True, True),
    "or.lex.archaic-form": ("Lexicon", "OV (older) word the IRV replaced", False, False),
    "or.style.length-consistency": ("Consistency", "Minority spelling with ି/ୀ or ୁ/ୂ (ଯିହୂଦା / ଯିହୁଦା)", False, True),
    "or.style.flap-consistency": ("Consistency", "ଡ / ଢ where Odia writes the flap ଡ଼ / ଢ଼ (inside a word: ଗୁଡିକ → ଗୁଡ଼ିକ, "
                                                 "ଦୃଢ → ଦୃଢ଼), or the flap at a word start or in a conjunct, when the "
                                                 "corpus has the other form", False, True),
    "or.style.phala-consistency": ("Consistency", "୍ବ where the OV writes ୍ୱ, or the minority of the two (ପରମେଶ୍ବର → "
                                                  "ପରମେଶ୍ୱର, ସ୍ବର୍ଗ → ସ୍ୱର୍ଗ)", False, True),
    "or.style.ya-consistency": ("Consistency", "Minority spelling with ଯ / ୟ", False, True),
    # off: ଲ / ଳ make real pairs (ଖୋଲି open / ଖୋଳି dig, ପାଲ sail / ପାଳ straw, କାଲ / କାଳ); the majority chose wrong
    # in the samples (facts M6)
    "or.style.lla-consistency": ("Consistency", "Minority spelling with ଲ / ଳ (off: many ଲ / ଳ pairs are two words)",
                                 False, False),
    "or.style.pancham-consistency": ("Consistency", "Minority spelling nasal conjunct vs anusvara (ଶଙ୍ଖ / ଶଂଖ)", False, True),
    "or.style.halant-final-consistency": ("Consistency", "Minority spelling with/without a final virama (ପୃଥକ୍ / ପୃଥକ)",
                                          False, True),
    "or.style.joiner-consistency": ("Consistency", "The same word written with and without the ZWJ that shows the "
                                                   "virama (ଭବିଷ୍ୟଦ୍\u200Dବକ୍ତା / ଭବିଷ୍ୟଦ୍ବକ୍ତା); the minority form", False, True),
    "or.gram.detached-suffix": ("Grammar", "A case or plural ending written as a word of its own (ଲେଖକ ଙ୍କୁ → ଲେଖକଙ୍କୁ, "
                                           "ଈଶ୍ୱରଙ୍କ ଠାରୁ → ଈଶ୍ୱରଙ୍କଠାରୁ, ବେପାରୀ ମାନଙ୍କୁ → ବେପାରୀମାନଙ୍କୁ)", False, True),
    "or.gram.honorific-case": ("Grammar", "A person the IRV speaks of with honour (-ଙ୍କ) in a plain case form "
                                          "(ଯୀଶୁକୁ → ଯୀଶୁଙ୍କୁ, ସଦାପ୍ରଭୁର → ସଦାପ୍ରଭୁଙ୍କର)", False, True),
    "or.gram.pronoun-copula": ("Grammar", "Pronoun and copula side by side disagree (ଆମ୍ଭେ ଅଛି → ଆମ୍ଭେ ଅଛୁ, "
                                          "ତୁମ୍ଭେ ଅଟେ → ତୁମ୍ଭେ ଅଟ)", False, True),
    "or.style.repeated-word": ("Style", "The same word twice (Odia repeats words on purpose: ଆପଣା ଆପଣା, ଧୀରେ ଧୀରେ)",
                               False, False),
}
SHAPE_RULE_SWITCH = {r.id: ("or.shape.errors" if r.severity == "error" else "or.shape.warnings") for r in T.SHAPE_RULES}
SHAPE_RULE_SWITCH.update({"or.shape.single-letter": "or.shape.single-letter", "or.shape.long-token": "or.shape.long-token"})
GENERIC_WARNING_RULE = {"double_space": "or.norm.spacing", "no_space_after_punct": "or.norm.spacing",
                        "space_before_punct": "or.punct.space-before-danda", "double_punct": "or.punct.double",
                        "digits_in_verse": "or.norm.encoding", "space_before_note_close": "or.norm.spacing",
                        "repeated_word": "or.style.repeated-word"}
CONSISTENCY_RULE = {"pancham": "or.style.pancham-consistency", "halant": "or.style.halant-final-consistency",
                    "length": "or.style.length-consistency", "flap": "or.style.flap-consistency",
                    "phala": "or.style.phala-consistency", "ya": "or.style.ya-consistency",
                    "lla": "or.style.lla-consistency", "joiner": "or.style.joiner-consistency"}

SUFFIX_STOP = ["ର", "ରେ", "କୁ", "ରୁ", "ଙ୍କ", "ଙ୍କୁ", "ଙ୍କର", "ମାନେ", "ମାନଙ୍କ", "ଠାରୁ", "ଠାରେ", "ହିଁ", "ଟି", "ଗଣ"]
# §4.1 endings stripped for inflected_ok, longest first (nominal: case, plural, honorific, classifier, emphatic;
# verbal: tense, person, participles).  Up to two are stripped (ଲୋକ+ମାନଙ୍କ+ଠାରୁ).
ENDINGS = sorted(set("""ମାନଙ୍କଠାରୁ ମାନଙ୍କଠାରେ ମାନଙ୍କଦ୍ୱାରା ମାନଙ୍କର ମାନଙ୍କୁ ମାନଙ୍କ ମାନେ ମାନ ଙ୍କଠାରୁ ଙ୍କଠାରେ ଙ୍କଦ୍ୱାରା ଙ୍କର
ଙ୍କୁ ଙ୍କ ଠାରୁ ଠାରେ ଦ୍ୱାରା ଗଣଙ୍କ ଗଣର ଗଣ ଗୁଡ଼ିକର ଗୁଡ଼ିକୁ ଗୁଡ଼ିକ ଗୁଡ଼ାକ ସବୁ ସମୂହ ସ୍ଥ ସ୍ଥିତ ରେ ର କୁ ରୁ ଏ ଟି ଟିଏ ଟା ହିଁ ହି
ପରି ମୟ ୀୟ ତା ତ୍ୱ କାରୀ କାରୀମାନେ ଶାଳୀ ଯୁକ୍ତ ହୀନ ବାନ ବତୀ
ିଲେ ିଲା ିଲି ିଲୁ ିଲ ିଲୁଁ ିବେ ିବା ିବାକୁ ିବାର ିବାରେ ିବି ିବୁ ିବ ିବାରୁ ିବାବେଳେ ିଅଛି ିଅଛନ୍ତି ିଅଛ ିଅଛୁ ିଛି ିଛନ୍ତି ିଥିଲେ
ିଥିଲା ିଥିଲି ିଥିଲୁ ିଥିବା ିଥିବେ ିଥାନ୍ତେ ିଥାଏ ିଥାନ୍ତି ୁଅଛି ୁଅଛନ୍ତି ୁଅଛ ୁଅଛୁ ୁଛି ୁଛନ୍ତି ୁଥିଲେ ୁଥିଲା ୁଥିଲି ୁଥିବା ୁଥିବେ
ନ୍ତି ନ୍ତୁ ନ୍ତେ ଅନ୍ତି ଅନ୍ତୁ ଅନ୍ତେ ଲେ ଲା ଲି ଲୁ ଲ ବେ ବା ବାକୁ ବାର ବି ବୁ ବ ଇଲେ ଇଲା ଇଲି ଇବା ଇବେ ଇବାକୁ ଇବ ଇଅଛି ଇଅଛନ୍ତି
ଉଅଛି ଉଅଛନ୍ତି ଉଛି ଉଛନ୍ତି ଉଥିଲେ ଉଥିଲା ଅଛି ଅଛନ୍ତି ଅଛ ଅଛୁ ଥିଲେ ଥିଲା ଥିବା ି ୁ େ ଅ ଇ ଉ""".split()),
                 key=lambda s: (-len(s), s))

DEFAULT_SETTINGS: dict = {
    "reviewer": "",
    "checked_contexts": ["verse", "heading", "psalm_title", "footnote_text", "intro", "title"],
    "suggest": {"max": 5, "lemma_min": 3, "irv_min": 3},
    "compound": {"enabled": True, "min_first": 3, "min_second": 3, "min_freq": 3, "suffix_stop": SUFFIX_STOP},
    "warnings": {"double_space": True, "digits": False, "defects": False, "space_before_note_close": False,
                 "double_punct": False, "no_space_after_punct": True, "space_before_punct": True, "repeated_word": False},
    "lex": {"irv_accept_min": 5, "inflect_min_aksharas": 2, "rare_max": 2, "rare_ratio": 50, "rare_cost": 0.5,
            "unknown_ratio": 10, "unknown_cost": 0.4},
    "consistency": {"minority_max": 0.25, "close": 0.8, "style_min": 20, "ov_veto": 3},
    "agreement": {"honorific_min": 20, "honorific_plain_max": 0.05},
    "rules": {rid: v[3] for rid, v in RULES.items()},
    # unknown words grey, as the Hindi and Malayalam reviews decided (most unknown words were correct)
    "style": {"unknown_color": "grey"},
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
        rule = (r.get("rule") or "or.lex.known-misspelling").strip()
        if wrong.endswith("*") and right.endswith("*"):
            d["misspell_prefix"].append((wrong[:-1], right[:-1], rule))
            continue
        if canon(right) == wrong:
            d["misspell_keep"].add(wrong)              # reviewed: keep as written
            continue
        d["misspell"][wrong] = (right, rule)
        if "reviewed" in (r.get("note") or ""):
            d["misspell_forced"].add(wrong)            # decided by the reviewer: flagged even as the IRV majority
    d["misspell_prefix"].sort(key=lambda x: -len(x[0]))
    d["names"] = {canon(r["name"]) for r in _tsv(dict_dir / "names.tsv") if r.get("name") and r.get("use", "yes") != "no"}
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
ABBREV_RE = re.compile(f"([{ORI}]+)\\.(?=\\s*[\\d୦-୯])")
_RAW = str.maketrans({"'": "’", "\u02BC": "’"})


def raw_form(w: str) -> str:
    """The written form with its joiners: NFC, ଵ as the IRV writes it, the apostrophe as ’, ZWNJ after a virama as
    ZWJ (or.norm.zwnj-halant reports that one) — the joiner-consistency unit."""
    w = T.va_letter(unicodedata.normalize("NFC", w)).translate(_RAW)
    return w.replace(T.VIRAMA + T.ZWNJ, T.VIRAMA + T.ZWJ)


def on_index_book(checker, code: str, book, checked: set) -> None:
    raw, abbr, colon, hyph_tok = Counter(), Counter(), Counter(), Counter()
    for line in book.lines:
        for seg in line.segs:
            if seg.kind != "t":
                continue
            for m in ABBREV_RE.finditer(seg.text):           # any context: references teach the abbreviations
                abbr[canon(m.group(1))] += 1
            if seg.context not in checked:
                continue
            raw.update(T.TOKEN_RE.findall(seg.text))
            for m in COLON_FINAL_RE.finditer(seg.text):
                colon[canon(m.group(1))] += 1
    can, forms = Counter(), Counter()
    for w, n in raw.items():
        k = canon(w)
        can[k] += n
        if "-" in k:
            hyph_tok[k] += n
        # a joiner only ever follows a virama: both forms of such a word, unless the form has a shape error
        if T.VIRAMA in w and not T.lexicon_fault(w, T.shape_findings(w)):
            forms[raw_form(w)] += n
    d = checker.data
    books = d.setdefault("book_state", {})
    old = books.get(code)
    for total_key, new in (("irv_canon", can), ("abbr", abbr), ("colon_final", colon), ("hyph_tok", hyph_tok),
                           ("raw_forms", forms)):
        total = d.setdefault(total_key, Counter())
        if old:
            total.subtract(old[total_key])
            for k in [k for k, v in total.items() if v <= 0]:
                del total[k]
        total.update(new)
    books[code] = {"irv_canon": can, "abbr": abbr, "colon_final": colon, "hyph_tok": hyph_tok, "raw_forms": forms}


HONORIFIC = ("ଙ୍କ", "ଙ୍କର", "ଙ୍କୁ", "ଙ୍କଠାରେ", "ଙ୍କଠାରୁ")


def honorific_table(irv: dict) -> dict:
    """base → (honorific uses, plain uses) for every base the IRV declines with -ଙ୍କ."""
    h, p = Counter(), Counter()
    for k, n in irv.items():
        for e in HONORIFIC:
            if k.endswith(e) and len(k) > len(e) + 1:
                b = k[:-len(e)]
                if b not in T.HONORIFIC_STOP and not b.endswith("ମାନ"):
                    h[b] += n
    for k, n in irv.items():
        for e in T.HONORIFIC_ENDINGS:
            if k.endswith(e) and k[:-len(e)] in h:
                p[k[:-len(e)]] += n
    return {b: (h[b], p[b]) for b in h}


def on_build_index(checker) -> None:
    d = checker.data
    accept = checker.settings["lex"].get("irv_accept_min", 5)
    irv = {k: n for k, n in d.get("irv_canon", Counter()).items() if n > 0}
    # words the IRV itself uses often (modern forms the OV lacks) are suggestion targets too
    for k, n in sorted(irv.items()):
        if n >= accept and not any(f.severity == "error" for f in T.shape_findings(k)):
            checker.index.add(k)
    loose = defaultdict(list)
    for w in checker.index.words:
        loose[T.loose_skeleton(w)].append(w)
    d["loose"] = loose
    d["clusters"] = {kind: kinds.consistency_clusters(irv, fn) for kind, fn in T.SKELETONS.items()}
    d["clusters"]["joiner"] = kinds.consistency_clusters(dict(d.get("raw_forms", Counter())), canon)
    d["honorific"] = honorific_table(irv)
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
    # never against the IRV's own majority (rules §4.3), unless the reviewer decided it
    if (irv_c(checker, key) > irv_c(checker, canon(right)) and rule != "or.style.divine-names"
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
    if ms and on(checker, ms[1] if ms[1] in RULES else "or.lex.known-misspelling"):
        sugg = [_sugg(w, ms[0], "house", freq_c(checker, canon(ms[0])))]
        sugg += [x for x in checker.suggest(w) if x["w"] != ms[0]]
        return _info("malformed", rule=ms[1], severity="error", sugg=sugg[:5], irv=irv_n, ov=ov_n, ov_lemma=ov_n)
    found = [f for f in T.shape_findings(w) if not f.gated and on(checker, SHAPE_RULE_SWITCH.get(f.rule, f.rule))]
    if found:
        f = next((x for x in found if x.severity == "error"), found[0])
        return _info("malformed", rule=f.rule, severity=f.severity, sugg=checker.suggest(w), irv=irv_n, ov=ov_n, ov_lemma=ov_n,
                     extra={} if f.severity == "error" else _panel())
    known = known_key(lex, key) or ("-" in key and all(known_key(lex, p) or inflected(checker, p)
                                                       for p in key.split("-") if p))
    if not known and key in checker.data.get("abbreviations", ()):
        return _info("ok", irv=irv_n, ov=ov_n, ov_lemma=ov_n)    # a book abbreviation of a reference (ଆଦି. 3:15)
    if not known:
        return None
    if key in lex.data.get("names", ()) or key in _extra_canon(lex):
        return _info("ok", irv=irv_n, ov=ov_n, ov_lemma=ov_n)
    if on(checker, "or.lex.archaic-form"):
        a = lex.data.get("archaic", {}).get(key)
        if a:
            return _info("archaic", rule="or.lex.archaic-form", severity="warning",
                         sugg=[_sugg(w, a[0], "archaic", freq_c(checker, a[0]))], irv=irv_n, ov=ov_n, ov_lemma=ov_n,
                         extra=_panel({"why": [f"the IRV has {a[0]} in {a[1]} of {a[2]} aligned verses"]}))
    if on(checker, "or.lex.rare-near-common"):
        s = checker.settings["lex"]
        if ov_n + irv_n <= s.get("rare_max", 2) and ov_n < 3:
            best = _near_common(checker, key, ov_n + irv_n, s)
            if best:
                return _info("suspect", rule="or.lex.rare-near-common", severity="warning", sugg=[best], irv=irv_n,
                             ov=ov_n, ov_lemma=ov_n, extra=_panel())
    return _info("ok", irv=irv_n, ov=ov_n, ov_lemma=ov_n)


def _near_common(checker, key, freq, s):
    """The frequent near form of a rare word: same loose skeleton or one deletion apart, cost ≤ rare_cost, and not
    only a house-style difference (pancham, final ୍: those are consistency questions)."""
    best = None
    cands = set(checker.data.get("loose", {}).get(T.loose_skeleton(key), ())) | checker.index.candidates(key)
    for c in sorted(cands):
        if c == key or freq_c(checker, c) < s.get("rare_ratio", 50) * max(1, freq):
            continue
        cost, ops = kinds.confusion_distance(key, c, T.CONFUSION, limit=s.get("rare_cost", 0.5))
        if cost > s.get("rare_cost", 0.5) or all(o[1] in T.HOUSE_CLASSES for o in ops):
            continue
        h = rank(checker, key, c, limit=s.get("rare_cost", 0.5))
        if h and (best is None or h[:4] < best[:4]):
            best = h
    return best[4] if best else None


def aksharas(w: str) -> int:
    n, prev = 0, ""
    for ch in w:
        c = T.cls(ch)
        if c in "CV" and prev != "_":
            n += 1
        if c != "Z":
            prev = c
    return n


def inflected(checker, key: str):
    """A known base + one or two endings (longest base first): (base, ending) or None."""
    memo = checker.data.setdefault("inflect_memo", {})
    gen = (checker.lex.generation, checker.settings_gen)
    if memo.get("gen") != gen or len(memo) > 80000:
        memo.clear()
        memo["gen"] = gen
    if key not in memo:
        memo[key] = _inflected(checker, key, checker.settings["lex"].get("inflect_min_aksharas", 2), 2)
    return memo[key]


def _inflected(checker, key: str, min_ak: int, depth: int):
    hit = None
    for end in ENDINGS:
        if not key.endswith(end) or len(key) <= len(end):
            continue
        stem = key[:-len(end)]
        if aksharas(stem) < min_ak:
            continue
        for base in (stem, stem + "ା", stem + "ିବା"):
            if known_key(checker.lex, base) and (hit is None or len(base) > len(hit[0])):
                hit = (base, end)
        if hit is None and depth > 1:
            inner = _inflected(checker, stem, min_ak, depth - 1)
            if inner:
                hit = (inner[0], inner[1] + end)
        if hit:
            break
    return hit


def classify_extra(checker, w: str, irv: int, ov: int):
    """After classify_first: gated shape warnings, house-style variant, known word + ending, IRV-frequent."""
    lex = checker.lex
    key = canon(w)
    irv_n, ov_n = irv_c(checker, key, irv), lex.lemma_count.get(key, 0)
    gated = [f for f in T.shape_findings(w) if f.gated and on(checker, SHAPE_RULE_SWITCH.get(f.rule, f.rule))]
    if gated and key not in T.DETACHED_SUFFIXES:       # a lone ଙ୍କୁ is or.gram.detached-suffix's
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
    if on(checker, "or.lex.rare-near-common") and key not in T.DETACHED_SUFFIXES:
        # an unknown word one cheap slip from a much commoner word (ପୁର୍ବରୁ → ପୂର୍ବରୁ, ଅଧିନ → ଅଧୀନ): listed, not grey
        lx = checker.settings["lex"]
        best = _near_common(checker, key, max(1, ov_n + irv_n),
                            {**lx, "rare_ratio": lx.get("unknown_ratio", 10), "rare_cost": lx.get("unknown_cost", 0.4)})
        if best:
            return _info("suspect", rule="or.lex.rare-near-common", severity="warning", sugg=[best] + [
                x for x in checker.suggest(w) if x["w"] != best["w"]][:4], irv=irv_n, ov=ov_n, ov_lemma=ov_n,
                extra=_panel())
    return None


def malformed(w: str):
    return None                        # Odia shape rules need settings: see classify_first


# ---------------------------------------------------------------------------------------------
# suggestions
# ---------------------------------------------------------------------------------------------
POS_RANK = {"medial": 0, "initial": 1, "final": 2}
GRAPHEME_START = re.compile("[\u0B05-\u0B14\u0B15-\u0B39\u0B5C-\u0B61\u0B71]")


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
        return None                      # never suggest a known misspelling
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
INVISIBLE_RE = re.compile("[\u200B\uFEFF\u00A0\u2060\u0080-\u009F]")
DANDA_TYPED_RE = re.compile(rf"(?<=[{ORI}”’!?)]) *\|")
LATIN_RE = re.compile("[A-Za-z]+")
OTHER_INDIC_RE = re.compile("[\u0900-\u0963\u0966-\u0AFF\u0B80-\u0DFF]+")
STRAIGHT_QUOTE_RE = re.compile('"')
APOS_RE = re.compile(f"(?<=[{ORI}])['\u02BC](?=[{ORI}])")
VA_RE = re.compile("\u0B35")
ZWNJ_HALANT_RE = re.compile("\u0B4D\u200C(?=[\u0B15-\u0B39\u0B5F\u0B71])")
COLON_IN_WORD_RE = T.COLON_IN_WORD_RE
COLON_FINAL_RE = re.compile(f"([{ORI}]+):(?=\\s|$|[,;।”’])")
QUESTION_TIGHT_RE = re.compile(f"(?<=[{ORI}])[?!]")
FULLSTOP_RE = re.compile(f"([{ORI}]+)\\.(?!\\s*[\\d୦-୯])(?!\\.)")
HYPHEN_RE = re.compile(rf"([{ORI}]+)(- +| +-)([{ORI}]+)|--+")
DOUBLE_PUNCT_RE = re.compile("[,;:\u0964]{2,}|[!?]{2,}")
MIXED_DIGITS_RE = re.compile("[0-9୦-୯]*(?:[0-9][୦-୯]|[୦-୯][0-9])[0-9୦-୯]*")
ODIA_DIGITS = str.maketrans({str(i): chr(0x0B66 + i) for i in range(10)})
NEVER_RE = re.compile("(?!)")


def normalize_run(text: str) -> tuple[str, list[str]]:
    """The L0 pipeline for one text run: (normalized text, what changed)."""
    what = []
    t = unicodedata.normalize("NFC", text)
    if t != text:
        what.append("not NFC")
    if INVISIBLE_RE.search(t):
        what.append("invisible character")
        t = INVISIBLE_RE.sub(lambda m: " " if m.group() == "\u00A0" else "", t)
    if DANDA_TYPED_RE.search(t):
        what.append("| typed as the danda")
        t = DANDA_TYPED_RE.sub("।", t)
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
    """A loose hyphen between two words: a compound (ପାପ- କର୍ମକାରୀ, ନଗର- ପ୍ରାଚୀର); a dash between clauses when the
    left word ends a clause is rare in the IRV (facts M9)."""
    return f"{a}-{b}", f"Join: {a}-{b}"


def _double_fix(run: str) -> str | None:
    """or.punct.double: the mark to keep: ?: / !: after a footnote lemma are the convention (None); otherwise the
    strongest mark (? ! । ; : , in that order)."""
    if len(run) == 2 and run[0] in "?!" and run[1] == ":":
        return None
    for mark in "?!।;:,":
        if mark in run:
            return mark
    return run[0]


def extra_warnings(checker, seg, next_seg) -> list:
    text = seg.text
    out = []

    def add(kind, s, e, **kw):
        out.append({"kind": kind, "s": s, "e": max(e, s + 1) if s < len(text) else e, "rule": kw.pop("rule", kind), **kw})

    if on(checker, "or.norm.encoding"):                 # one finding per spot, each with its own fix
        for m in T.TOKEN_RE.finditer(text):
            nfc = unicodedata.normalize("NFC", m.group())
            if nfc != m.group():
                add("or.norm.encoding", m.start(), m.end(), fix=nfc, fix_label="Fix the encoding (NFC)",
                    label="encoding: not NFC")
        for m in INVISIBLE_RE.finditer(text):
            add("or.norm.encoding", m.start(), m.end(), fix=" " if m.group() == " " else "",
                fix_label="Remove the invisible character", label=f"encoding: invisible U+{ord(m.group()):04X}")
        for m in DANDA_TYPED_RE.finditer(text):
            add("or.norm.encoding", m.start(), m.end(), fix="।", fix_label="Change | to ।", label="| typed as the danda")
    if on(checker, "or.norm.va-letter"):
        for m in T.TOKEN_RE.finditer(text):
            if T.VA in m.group():                       # ବ after ମ୍, ୱ elsewhere (facts M3)
                add("or.norm.va-letter", m.start(), m.end(), fix=T.va_letter(m.group()),
                    fix_label="Change ଵ to ୱ (ବ after ମ୍)", label="ଵ U+0B35: the IRV writes ୱ U+0B71 (ବ after ମ୍)")
    if on(checker, "or.norm.colon-for-visarga"):
        for m in COLON_IN_WORD_RE.finditer(text):
            a = re.search(f"[{ORI}]+$", text[:m.start()])
            b = re.match(f"[{ORI}]+", text[m.end():])
            joined = canon((a.group() if a else "") + "ଃ" + (b.group() if b else ""))
            # the visarga only where the joined word is a word (ଦୁ:ଖ → ଦୁଃଖ); otherwise a label colon with the space
            # missing (ଅଛି:ଥେବେସ୍‍ର, NAM intro)
            if known_key(checker.lex, joined) or inflected(checker, joined) or freq_c(checker, joined) >= 3:
                add("or.norm.colon-for-visarga", m.start(), m.end(), fix="ଃ", fix_label="Change : to the visarga ଃ",
                    label=f"colon typed for the visarga ({joined})")
            else:
                add("or.norm.colon-for-visarga", m.start(), m.end(), fix=": ", fix_label="Add the space after :",
                    label="no space after the colon")
    if on(checker, "or.norm.apostrophe"):
        for m in APOS_RE.finditer(text):
            add("or.norm.apostrophe", m.start(), m.end(), fix="’", fix_label="Change ' to ’", label="straight apostrophe")
    if on(checker, "or.norm.zwnj-halant"):
        for m in ZWNJ_HALANT_RE.finditer(text):
            add("or.norm.zwnj-halant", m.start() + 1, m.end(), panel=True, fix="\u200d", fix_label="Change ZWNJ to ZWJ",
                label="ZWNJ after the virama (the IRV shows the virama with ZWJ)")
    if on(checker, "or.norm.mixed-digits"):
        for m in MIXED_DIGITS_RE.finditer(text):
            add("or.norm.mixed-digits", m.start(), m.end(), fix=m.group().translate(ODIA_DIGITS),
                fix_label="Odia digits throughout", label="Odia and ASCII digits in one number")
    if on(checker, "or.norm.latin-in-text"):
        for m in LATIN_RE.finditer(text):
            add("or.norm.latin-in-text", m.start(), m.end(), panel=True, label="Latin letters in Odia text")
    if on(checker, "or.shape.mixed-script"):
        for m in OTHER_INDIC_RE.finditer(text):
            add("or.shape.mixed-script", m.start(), m.end(), label="letter of another Indic script")
    if on(checker, "or.norm.quotes"):
        for m in STRAIGHT_QUOTE_RE.finditer(text):
            add("or.norm.quotes", m.start(), m.end(), label="straight quote (the IRV uses “ ”)")
    if on(checker, "or.shape.hyphen"):
        for m in HYPHEN_RE.finditer(text):
            if m.group(1):
                fix, label = _dash_fix(checker, m.group(1), m.group(3))
                add("or.shape.hyphen", m.start(), m.end(), panel=True, fix=fix, fix_label=label, label="loose hyphen")
            else:
                add("or.shape.hyphen", m.start(), m.end(), panel=True, fix="-", fix_label="One hyphen", label="doubled hyphen")
    if on(checker, "or.punct.question-space"):
        for m in QUESTION_TIGHT_RE.finditer(text):
            add("or.punct.question-space", m.start(), m.end(), panel=True, fix=" " + m.group(),
                fix_label=f"Write a space before {m.group()}", label=f"{m.group()} straight after the word")
    if on(checker, "or.punct.double"):
        for m in DOUBLE_PUNCT_RE.finditer(text):
            keep = _double_fix(m.group())
            if keep is None or keep == m.group() or m.group() in ("?!", "!?"):
                continue
            add("or.punct.double", m.start(), m.end(), fix=keep, fix_label=f"Keep {keep}", label="doubled punctuation")
    if on(checker, "or.punct.fullstop-for-danda"):
        abbr = checker.data.get("abbreviations", T.ABBREVIATION_SEED)
        for m in FULLSTOP_RE.finditer(text):
            if canon(m.group(1)) in abbr:
                continue
            mid = re.match(r"\s*[(,;]", text[m.end():])
            # ପ୍ର.ବଂ. and ଦ୍ଵି. ରା. 22:8: an abbreviation in parts
            if re.match(f"[{ORI}]|\\s*[{ORI}]+\\.\\s*[\\d୦-୯]", text[m.end():]):
                continue
            add("or.punct.fullstop-for-danda", m.end() - 1, m.end(), panel=True, fix="" if mid else "।",
                fix_label="Remove the full stop" if mid else "Change . to ।",
                label=f"full stop after {m.group(1)} (not an abbreviation the IRV uses)")
    return out


def token_warning(checker, text, w, a, b, s):
    return None


# ---------------------------------------------------------------------------------------------
# chapter-level: quotes that do not balance (a quotation re-opened at a new paragraph continues)
# ---------------------------------------------------------------------------------------------

def chapter_leads(checker, book, n, blocks) -> None:
    if not on(checker, "or.punct.quotes-unbalanced"):
        return
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
                        seg["warnings"].append({"kind": "or.punct.quotes-unbalanced", "s": i, "e": i + 1, "panel": True,
                                                "rule": "or.punct.quotes-unbalanced",
                                                "label": "” without an opening “ in this chapter"})


# ---------------------------------------------------------------------------------------------
# leads: spelling consistency, detached endings, honorific case, pronoun + copula
# ---------------------------------------------------------------------------------------------

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


def _phala_target(checker, k: str) -> str | None:
    """୍ବ / ୍ୱ: the OV distinguishes them (ପରମେଶ୍ଵର, ସ୍ଵର୍ଗ but ପର୍ବତ, ଅମ୍ବ).  A form the OV never writes, whose other
    phala is an OV word or an OV word + ending, takes the OV's phala."""
    lex = checker.lex
    for a, b in ((T.VIRAMA + T.BA, T.VIRAMA + T.WA), (T.VIRAMA + T.WA, T.VIRAMA + T.BA)):
        if a not in k or known_key(lex, k):
            continue
        alt = k.replace(a, b)
        if known_key(lex, alt) or (inflected(checker, alt) and not inflected(checker, k)):
            return alt
    return None


def _consistency_lead(checker, w: str):
    s = checker.settings["consistency"]
    lex = checker.lex
    k = canon(w)
    if k in lex.data.get("misspell_keep", ()) or T.fixed_word(w):
        return None                            # kept as written, or a shape error that its own rule reports
    clusters = checker.data.get("clusters", {})
    for kind in list(T.SKELETONS) + ["joiner"]:
        rid = CONSISTENCY_RULE[kind]
        if not on(checker, rid):
            continue
        me = raw_form(w) if kind == "joiner" else k
        sk = canon(w) if kind == "joiner" else T.SKELETONS[kind](k)
        forms = clusters.get(kind, {}).get(sk)
        choice = style_choice(checker, f"{kind}:{sk}")
        if choice == "off":
            continue
        # a variant the IRV writes ≥ style_min times is a house-style question for Settings, not a slip: the whole NT
        # writes ପରମେଶ୍ବର (1,427×), the OT ପରମେଶ୍ୱର (facts, hand-written part, item 2)
        uses = (forms or {}).get(me, 0) if kind == "joiner" else irv_c(checker, k)
        frequent = uses >= s.get("style_min", 20) and kind != "flap"
        if kind == "phala" and choice == "auto" and not frequent:
            target = _phala_target(checker, k)
            if target:
                fs = dict(forms or {})
                ranked = sorted(fs.items(), key=lambda kv: (-kv[1], kv[0]))
                why = ["IRV forms: " + ", ".join(f"{f} {c}×" for f, c in ranked[:5]),
                       f"the OV writes {target} (୍ୱ and ୍ବ are different letters)"]
                return _cons(kind, rid, w, target, why, fs.get(k, irv_c(checker, k)), fs.get(target, freq_c(checker, target)))
        if kind == "flap" and choice == "auto":
            # Odia's own rule, not the majority: the IRV writes ଗୁଡିକ more often than ଗୁଡ଼ିକ (facts M6)
            target = T.flap_expected(k)
            if (target != k and lex.lemma_count.get(k, 0) < s.get("ov_veto", 3)
                    and (freq_c(checker, target) or known_key(lex, target) or inflected(checker, target))):
                fs = dict(forms or {})
                ranked = sorted(fs.items(), key=lambda kv: (-kv[1], kv[0]))
                why = ["IRV forms: " + ", ".join(f"{f} {c}×" for f, c in ranked[:5]),
                       "Odia writes the flap ଡ଼ ଢ଼ inside a word, ଡ ଢ at its start and in a conjunct"]
                return _cons(kind, rid, w, target, why, fs.get(k, irv_c(checker, k)), fs.get(target, freq_c(checker, target)))
            continue
        if not forms or me not in forms:
            continue
        ranked = sorted(forms.items(), key=lambda kv: (-kv[1], kv[0]))
        total = sum(forms.values())
        if choice == "auto":
            top, n_top = ranked[0]
            close = len(ranked) > 1 and ranked[1][1] >= s.get("close", 0.8) * n_top
            if me == top or close or forms[me] / total >= s.get("minority_max", 0.25) or frequent:
                continue
            if kind != "joiner" and lex.lemma_count.get(k, 0) >= s.get("ov_veto", 3):
                continue                       # the minority form is an OV word in its own right (ଦିନ / ଦୀନ)
            target = top
        else:
            if me == choice or choice not in forms:
                continue
            target = choice
        why = ["IRV forms: " + ", ".join(f"{f} {c}×" for f, c in ranked[:5])]
        if choice != "auto":
            why.append(f"house style chosen: {choice}")
        return _cons(kind, rid, w, target, why, forms[me], forms[target])
    return None


def _cons(kind, rid, w, target, why, n_w, n_t):
    return {"kind": "consistency", "rule": rid, "proposed": target, "basis": kind, "strength": "panel",
            "panel": True, "w2": "≈" + kind, "pair": w, "why": why,
            "tag": f"{kind} {n_w}:{n_t}", "title": f"spelling consistency ({kind})",
            "issue_type": "Spelling consistency", "ignore_label": f"Stop flagging {w} for {kind} consistency"}


HONORIFIC_PLAIN = sorted(T.HONORIFIC_ENDINGS.items(), key=lambda kv: -len(kv[0]))


def honorific_lead(checker, w: str):
    """or.gram.honorific-case: B + a plain ending where the IRV declines B with -ଙ୍କ (ଯୀଶୁକୁ → ଯୀଶୁଙ୍କୁ)."""
    if not on(checker, "or.gram.honorific-case"):
        return None
    key = (checker.lex.generation, checker.settings_gen, id(checker.data.get("honorific")))
    memo = checker.data.get("hon_memo")
    if memo is None or memo[0] != key:
        memo = checker.data["hon_memo"] = (key, {})
    if w not in memo[1]:
        memo[1][w] = _honorific_lead(checker, w)
    return memo[1][w]


def _honorific_lead(checker, w: str):
    a = checker.settings["agreement"]
    k = canon(w)
    if checker.lex.lemma_count.get(k, 0) >= 3 or k in checker.lex.data.get("names", ()):
        return None
    table = checker.data.get("honorific", {})
    for plain, hon in HONORIFIC_PLAIN:
        if not k.endswith(plain):
            continue
        base = k[:-len(plain)]
        h, p = table.get(base, (0, 0))
        if h >= a.get("honorific_min", 20) and p / (h + p) <= a.get("honorific_plain_max", 0.05) and aksharas(base) >= 2:
            proposed = base + hon
            return {"kind": "agreement", "rule": "or.gram.honorific-case", "proposed": proposed, "basis": "honorific",
                    "strength": "panel", "panel": True, "w2": "≈honorific", "pair": w,
                    "why": [f"the IRV declines {base} with -ଙ୍କ {h}× and with a plain ending {p}×"],
                    "tag": f"honorific {h}:{p}", "title": f"honorific case of {base}", "issue_type": "Grammar",
                    "ignore_label": f"Stop flagging {w} (honorific)"}
    return None


def _joined(w1: str, w2: str) -> str:
    return w1 + ("\u200d" if w1.endswith(T.VIRAMA) else "") + w2


def _bigram_lead(rule: kinds.BigramRule, proposed: str, w1: str, w2: str) -> dict:
    return {"kind": "agreement", "rule": rule.id, "proposed": proposed, "basis": "bigram", "strength": "panel",
            "panel": True, "w2": w2 if rule.fix == "left" else w1, "why": [rule.why],
            "pair": f"{w1} {w2}", "tag": f"{w1} {w2}", "title": rule.why, "issue_type": "Grammar"}


def line_leads(checker, toks: list, line) -> list:
    leads = []
    for i, (w, r, s, e, adj, _) in enumerate(toks):
        c = consistency_lead(checker, w)
        if c:
            leads.append({**c, "w1": w, "run": r, "s": s, "e": e, "ignored": (w, c["w2"]) in checker.ignored_pairs})
        hl = honorific_lead(checker, w)
        if hl:
            leads.append({**hl, "w1": w, "run": r, "s": s, "e": e, "ignored": (w, hl["w2"]) in checker.ignored_pairs})
        if i + 1 >= len(toks) or not toks[i + 1][4]:
            continue
        w2, r2, s2, e2 = toks[i + 1][:4]
        k, k2 = canon(w), canon(w2)
        if (k2 in T.DETACHED_SUFFIXES and r2 == r and k != k2 and on(checker, "or.gram.detached-suffix")
                and aksharas(k) >= 1):
            proposed = _joined(w, w2)
            leads.append({"kind": "agreement", "rule": "or.gram.detached-suffix", "proposed": proposed,
                          "basis": "suffix", "strength": "panel", "panel": True, "w2": "≈suffix", "pair": f"{w} {w2}",
                          "why": [f"{w2} is an ending: it is written joined to the word before it"],
                          "tag": f"{w} {w2}", "title": "ending written apart", "issue_type": "Grammar",
                          "ignore_label": f"Stop flagging {w} {w2}",
                          "w1": w, "run": r, "s": s, "e": e2, "ignored": (w, "≈suffix") in checker.ignored_pairs})
        for rule, proposed in kinds.bigram_rules(k, k2, T.BIGRAM_RULES):
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
STATUS_RULE = {"unknown": "or.lex.unknown"}


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
    hon = sorted(((b, h, p) for b, (h, p) in checker.data.get("honorific", {}).items() if h >= 20 and p),
                 key=lambda x: -x[1])
    return {"lang": "or", "rules": rows, "statuses": statuses, "books_counted": books, "errors": [], "file": path,
            "misspellings": len(checker.lex.data.get("misspell", {})), "lexicon_gen": checker.lex.generation,
            "honorific_mixed": [{"base": b, "honorific": h, "plain": p} for b, h, p in hon[:20]],
            "abbreviations": sorted(checker.data.get("abbreviations", ())),
            "message": f"{len(checker.lex.data.get('misspell', {}))} known misspellings loaded from {path}"}


def style_toggles(checker) -> list[dict]:
    s = checker.settings["consistency"]
    out = [{"id": "unknown_color", "label": "Unknown (not verifiable) words are shown",
            "options": [{"value": "grey", "label": "light grey"}, {"value": "red", "label": "red"}],
            "default": "grey", "close": False, "total": 10 ** 9, "fixed": True}]
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
        "legend": "Odia checks (docs/ODIA_RULES.md)",
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
    "status": {"malformed": "Odia spelling fault", "irv_ok": "not in the OV, but common in the IRV (accepted)",
               "unknown": "not verifiable (not in the OV, no known word + ending)",
               "inflected_ok": "known word + ending", "suspect": "rare form of a much commoner word",
               "archaic": "older (OV) spelling"},
    "cls": {"vowel_length": "ି/ୀ ୁ/ୂ", "flap": "ଡ / ଡ଼", "phala": "୍ୱ / ୍ବ", "ba_wa": "ବ / ୱ", "ya": "ଯ / ୟ",
            "ja_ya": "ଜ / ଯ", "la_lla": "ଲ / ଳ", "na_nna": "ନ / ଣ", "sibilant": "ଶ / ଷ / ସ", "nasal": "ଂ / ଁ",
            "pancham": "nasal conjunct / ଂ", "diphthong": "େ/ୈ ୋ/ୌ", "ri": "ୃ / ୍ର", "halant": "final ୍",
            "reph": "ର୍ position", "aspiration": "aspiration", "retroflex": "ତ/ଟ ଦ/ଡ", "transpose": "swap",
            "other": "", "shape": "fix", "house": "house spelling", "archaic": "IRV form"},
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
    "rebuild": "Rebuild dictionary_or from the OV Odia + extra_words.txt",
    "rules_link": "Odia rules",
    "issue_types": ["Possible typo", "Confirmed typo", "Spelling consistency", "Vowel length / ଡ଼ / phala", "Grammar",
                    "Punctuation", "Encoding", "Font / rendering", "Name consistency", "Reviewer decision", "Other"],
    "settings_warn": {"double_space": "double space", "digits": "digits in verse text", "defects": "(not used for Odia)",
                      "space_before_note_close": "space before a footnote end", "double_punct": "double punctuation (,, ।।)",
                      "no_space_after_punct": "no space after , ; ! ? ।", "space_before_punct": "space before । , ; :",
                      "repeated_word": "(see Style below)"},
    "word_re": T.TOKEN_RE.pattern,
    "zw_strip": "[\u200B\uFEFF\u2060]",                       # the editor keeps ZWJ / ZWNJ: they are in the text
}


def related_norm(lex):
    return canon


def build_args(config) -> list[str]:
    args = ["--lang", "or", "--out-dir", str(config.get("dict_dir") or REPO_ROOT / "dictionary_or")]
    if config.get("irv_dir"):
        args += ["--irv-dir", str(config["irv_dir"])]
    return args


FORBIDDEN_IN_TEXT = {"\\": "backslash", "\r": "CR", "\n": "LF", "\t": "TAB", "\u200B": "U+200B", "\uFEFF": "U+FEFF"}

PROFILE = Language(
    code="or", name="Odia", script=T.SCRIPT, file_re=T.FILE_RE,
    dict_dir_default=REPO_ROOT / "dictionary_or", data_dir_default=APP_DIR / "data_or", port_default=8769,
    html_lang="or", app_title="Odia IRV spell-check editor",
    token_re=T.TOKEN_RE, grapheme_start=GRAPHEME_START, default_settings=DEFAULT_SETTINGS,
    malformed=malformed, rank=rank, extra_suggestions=extra_suggestions,
    defect_patterns=[], punct_run_re=re.compile("[,;:।]{2,}"), ok_punct_runs={"?!", "!?"},
    no_space_after_re=re.compile(rf"[,;!?।](?=[{ORI}])"),
    # the IRV writes a space before ? and ! (facts M9): only । , ; : must follow the word directly
    space_before_re=re.compile(rf"(?<=[{T.TOKEN_CH}\d୦-୯)”’])( +)(?=[,;:।])"),
    token_warning=token_warning, related_norm=related_norm, build_args=build_args,
    irv_dir_default=str(paths.irv_dir("or")),
    more_candidates=more_candidates, classify_first=classify_first, classify_extra=classify_extra,
    line_leads=line_leads, chapter_leads=chapter_leads, extra_warnings=extra_warnings,
    on_index_book=on_index_book, on_build_index=on_build_index, load_lexicon_extra=load_lexicon_extra,
    info_extra=info_extra, rules_payload=rules_payload, report_block=report_block,
    rules_page="rules_or.html", labels=LABELS,
    font_family="Noto Sans Oriya", font_fallback=("Nirmala UI", "Kalinga"),
    features={"sandhi": False, "rule_pack": True, "consistency": True, "encoding": True},
    zero_width_re=NEVER_RE, forbidden_in_text=FORBIDDEN_IN_TEXT,
    detect_file_re=T.DETECT_FILE_RE, detect_letters_re=T.DETECT_LETTERS_RE,
    line_ending="\n",                                   # the IRV Odia files have LF line ends (facts M1)
)
