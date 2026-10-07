"""Malayalam profile for the IRV editor.  Rules: docs/MALAYALAM_RULES.md (`ml.*` ids).

Tables live in ml_tables.py, the algorithms in qa_app/kinds.py; this module wires them into the
checker's hooks.  Nothing here is used when the editor runs Tamil or Punjabi.

Every lookup goes through `canon()` (rules §2): NFC, ൎ→ർ, ൌ→ൗ, legacy ZWJ chillu → atomic, the three
nta encodings → ന്റ, ZWJ/ZWNJ dropped, word-final ു → ്.  The tokenizer keeps ZWJ/ZWNJ inside the
token, so underlines land on the raw text and no offset map is needed.

Statuses a Malayalam token can get (besides the shared ok / ignored / compound):
  malformed         a shape fault (§3), a known misspelling or a divine-name variant; errors are
                    underlined, warnings (`panel`: true) are only listed
  inflected_ok      a known word plus a listed ending, undone with the junction rules (grey)
  irv_ok            not in the OV but used ≥ 5× in the IRV itself (grey, listed under Book words)
  unknown           not verifiable (light grey: see rules §0 — about one word in twelve)
  rare_near_common  an unknown word next to a frequent near form (red once the rule is inline)
"""
from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from pathlib import Path

from .. import paths
from .. import kinds
from . import APP_DIR, REPO_ROOT, Language
from . import ml_tables as T

ML = T.ML
MLZ = T.ML + T.ZW                       # a Malayalam letter or the ZWJ / ZWNJ inside a word (ന്‍)
canon = T.canon

# ---------------------------------------------------------------------------------------------
# rule catalogue: id -> (group, plain-language label, inline?, on by default?)
# ---------------------------------------------------------------------------------------------
RULES: dict[str, tuple[str, str, bool, bool]] = {
    "ml.norm.encoding": ("Encoding", "Encoding: not NFC, legacy ZWJ chillu (ന്‍ for ൻ), legacy AU sign ൌ, dot reph ൎ, "
                                     "misplaced ZWNJ, stray ZWJ, Malayalam digit, archaic ഩ (one fix per text run)", True, True),
    "ml.norm.nta-encoding": ("Encoding", "The nta cluster written another way than the project's form (ന്‍റ / ൻ്റ / ന്റ): "
                                         "listed only until the target form is chosen; ൻറ without a virama is always fixed", True, True),
    "ml.norm.zwnj-final": ("Encoding", "Word-final ZWNJ after a virama (ദാവീദ്‌): removed (Q3, reviewed 1 Oct 2026)", True, True),
    "ml.norm.zwnj-after-virama": ("Encoding", "ZWNJ after a virama before a consonant (ചെയ്‌വാൻ): deliberate, removed only when on", True, False),
    "ml.norm.invisible": ("Encoding", "Zero-width space, BOM, no-break space or word joiner in the text", True, True),
    "ml.norm.colon-for-visarga": ("Encoding", "ASCII colon typed for the visarga (ദു:ഖം → ദുഃഖം)", True, True),
    "ml.shape.errors": ("Shape", "Impossible sign sequence: vowel sign / virama / anusvara / visarga in a place Malayalam does "
                                 "not allow, a sign or virama on a chillu, ZWJ/ZWNJ out of place, an unexpected code point (§3)", True, True),
    "ml.shape.independent-vowel-mid-word": ("Shape", "An independent vowel inside a word (മറ്റേഅറ്റം): a missing space or a sandhi "
                                                     "join", True, True),
    "ml.shape.visarga-position": ("Shape", "Visarga ഃ at a word start or after a virama", False, True),
    "ml.shape.single-letter": ("Shape", "A one-letter word other than ആ ഈ ഏ ഓ ഒ എ അ", False, True),
    "ml.shape.long-token": ("Shape", "A word of 32+ code points (a missing space?) not in the dictionary", False, False),
    "ml.shape.mixed-script": ("Shape", "Tamil, Devanagari or Latin letter inside Malayalam text (wrong keyboard)", True, True),
    "ml.shape.hyphen": ("Shape", "Loose hyphen: at a word edge, doubled, or with a space inside a compound", False, True),
    "ml.punct.double-space": ("Punctuation", "Double space", True, True),
    "ml.punct.space-before": ("Punctuation", "Space before , ; : . ! ?", True, True),
    "ml.punct.no-space-after": ("Punctuation", "No space after , ; : ! ?", True, True),
    "ml.punct.double": ("Punctuation", "Doubled punctuation (?. ;. ,,)", True, True),
    "ml.punct.straight-quote": ("Punctuation", "Straight quote \" or ' (the IRV uses “ ” ‘ ’)", True, True),
    "ml.punct.quotes-unbalanced": ("Punctuation", "Opening and closing quotes do not balance in the chapter (counts only until the "
                                                  "quotation policy is decided, Q9)", False, True),
    "ml.punct.dash": ("Punctuation", "En dash – where the IRV uses the em dash —", False, True),
    "ml.punct.number-case": ("Punctuation", "Case ending after a number written as a separate word (64 ൽ → 64-ൽ)", False, True),
    "ml.lex.unknown": ("Lexicon", "Word not verifiable from the OV + extra words + endings + compounds, and rare in the IRV (grey)", True, True),
    "ml.lex.rare-near-common": ("Lexicon", "Unknown word (≤ 3× in OV + IRV) next to a ≥ 50× commoner near form (red)", True, True),
    "ml.lex.known-misspelling": ("Lexicon", "Listed in known_misspellings.tsv", True, True),
    "ml.style.divine-names": ("Lexicon", "Divine name spelled another way than the IRV's (യഹോവ, യേശു, ക്രിസ്തു …)", True, True),
    "ml.style.rejected-names": ("Lexicon", "A name form the reviewer rejected (മോശ-, അബ്രഹാം, ഇസ്രായേൽ, ജെറുസലേം, ഈജിപ്ത്, യോർദാൻ)", True, True),
    "ml.lex.archaic-form": ("Lexicon", "OV (older) spelling the IRV otherwise never uses (അവന്ന് → അവന്, ജാതികൾ → ജനതകൾ)", False, True),
    "ml.style.samvruthokaram": ("Consistency", "Minority ്/ു ending in a nominal ending class (ഇത് / ഇതു, നിനക്ക് / നിനക്കു); never on verb forms", False, True),
    "ml.style.yk": ("Consistency", "യ്ക്ക / ക്ക by the sound before it (യഹോവയ്ക്ക്, ഇരിക്കും) or the IRV majority", True, True),
    "ml.style.chillu-conjunct": ("Consistency", "Minority spelling chillu vs conjunct before a consonant (നൽകി / നല്കി, ൻമ / ന്മ)", True, True),
    "ml.style.gemination": ("Consistency", "Minority spelling with/without a doubled consonant (യോർദ്ദാൻ / യോർദാൻ)", True, True),
    "ml.style.vowel-length": ("Consistency", "Minority spelling differing in vowel length (ശമൂവേൽ / ശമുവേൽ); not the vocative", True, True),
    "ml.punct.quote-direction": ("Punctuation", "A quote mark facing the wrong way: ” opening or “ closing a quotation", True, True),
    "ml.style.nasal-form": ("Consistency", "Minority spelling anusvara vs homorganic nasal (വലങ്കൈ / വലംകൈ)", True, True),
    "ml.style.proper-names": ("Consistency", "A proper name (names.tsv) spelled two ways in the IRV (സേലാ / ശേലാ)", False, True),
    "ml.gram.medial-vowel-sandhi": ("Grammar", "Sandhi form for an independent vowel inside a word (മുന്നൂറ്റിഅമ്പത് → മുന്നൂറ്റമ്പത്)", True, True),
    "ml.gram.standalone-um": ("Grammar", "ഉം written as a separate word: fuse with the previous word", False, True),
    "ml.gram.standalone-o": ("Grammar", "ഓ / ഏ written as a separate word (not before ! or ,)", False, True),
    "ml.gram.anusvara-plural": ("Grammar", "Plural written ംകൾ for ങ്ങൾ", False, True),
    "ml.gram.locative-gemination": ("Grammar", "Locative of a stem that doubles (വീട് → വീട്ടിൽ) written without the doubling", False, True),
    "ml.style.reduplication-hyphen": ("Style", "“X X” where the IRV writes “X-X”", False, True),
    "ml.style.repeated-word": ("Style", "The same word twice (reduplication is usual: നീക്കി നീക്കി)", False, False),
}
SHAPE_RULE_SWITCH = {r.id: "ml.shape.errors" for r in T.SHAPE_RULES if r.severity == "error"}
SHAPE_RULE_SWITCH.update({"ml.shape.independent-vowel-mid-word": "ml.shape.independent-vowel-mid-word",
                          "ml.shape.visarga-position": "ml.shape.visarga-position",
                          "ml.shape.single-letter": "ml.shape.single-letter", "ml.shape.long-token": "ml.shape.long-token"})
GENERIC_WARNING_RULE = {"double_space": "ml.punct.double-space", "no_space_after_punct": "ml.punct.no-space-after",
                        "space_before_punct": "ml.punct.space-before", "double_punct": "ml.punct.double",
                        "zero_width": "ml.norm.invisible", "digits_in_verse": "ml.norm.encoding",
                        "space_before_note_close": "ml.punct.double-space", "repeated_word": "ml.style.repeated-word"}
CONSISTENCY_RULE = {"samvruthokaram": "ml.style.samvruthokaram", "yk": "ml.style.yk", "chillu_conjunct": "ml.style.chillu-conjunct",
                    "gemination": "ml.style.gemination", "vowel_length": "ml.style.vowel-length",
                    "nasal_form": "ml.style.nasal-form", "proper_names": "ml.style.proper-names"}
NTA_CHOICES = ["ന്‍റ", "ൻ്റ", "ന്റ"]

DEFAULT_SETTINGS: dict = {
    "reviewer": "",
    "checked_contexts": ["verse", "heading", "psalm_title", "footnote_text", "intro", "title"],
    "suggest": {"max": 5, "lemma_min": 3, "irv_min": 3},
    "compound": {"enabled": True, "min_first": 3, "min_second": 3, "min_freq": 1, "suffix_stop": []},
    "warnings": {"double_space": True, "digits": False, "defects": False, "space_before_note_close": False,
                 "double_punct": True, "no_space_after_punct": True, "space_before_punct": True, "repeated_word": False},
    # rare_max 3: the rules doc's own finds include ഒരോ 3× vs ഓരോ 406× (§4.3), so the threshold is "≤ 3", not "≤ 2"
    "lex": {"irv_accept_min": 5, "inflect_min_stem": 3, "rare_max": 3, "rare_ratio": 50, "rare_cost": 0.5},
    "consistency": {"minority_max": 0.25, "close": 0.8, "style_min": 20, "min_total": 3, "ov_veto": 3, "abstain_after": 3},
    "rules": {rid: v[3] for rid, v in RULES.items()},
    # house style: "auto" = the majority measured in the project text (off when the two counts are within 20 %)
    "style": {"unknown_color": "grey", "nta": "ൻ്റ", "quotes": "auto",
              "chillu:ൽപ": "ല്പ", "chillu:ൻമ": "ന്മ", "chillu:ൺമ": "ണ്മ", "chillu:ൽക": "ല്ക"},
}


def on(checker, rid: str) -> bool:
    return bool(checker.settings.get("rules", {}).get(rid, RULES.get(rid, ("", "", False, True))[3]))


def style_choice(checker, key: str, default: str = "auto") -> str:
    v = checker.settings.get("style", {}).get(key, default)
    return v if v is not None else default


# ---------------------------------------------------------------------------------------------
# lexicon tables (dictionary_ml/)
# ---------------------------------------------------------------------------------------------

def _tsv(path: Path):
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE))


def load_lexicon_extra(lex, dict_dir: Path) -> None:
    d = lex.data
    d["misspell"], d["misspell_prefix"] = {}, []
    rows = _tsv(dict_dir / "known_misspellings.tsv") or [
        {"wrong": a, "right": b, "rule": r, "note": n} for a, b, r, n in T.KNOWN_MISSPELLINGS_SEED]
    for r in rows:
        wrong, right = (r.get("wrong") or "").strip(), (r.get("right") or "").strip()
        rule = (r.get("rule") or "ml.lex.known-misspelling").strip()
        if not wrong or not right or wrong.startswith("#"):
            continue
        if wrong[-1] in "-*" and right[-1] in "-*":
            d["misspell_prefix"].append((T.canon_enc(wrong[:-1]), T.canon_enc(right[:-1]), rule, wrong[-1] == "*"))
        else:
            d["misspell"][canon(wrong)] = (canon(right), rule)
    d["misspell_path"] = str(dict_dir / "known_misspellings.tsv")
    suf = [r["suffix"] for r in _tsv(dict_dir / "suffixes.tsv") if r.get("suffix")]
    d["suffixes"] = sorted(set(suf or T.SUFFIX_SEED), key=len, reverse=True)
    stems = _tsv(dict_dir / "stems.tsv")
    d["stems_double"] = {canon(r["stem"]) for r in stems if r.get("doubles") == "yes"} or set(T.STEMS_DOUBLE)
    d["stems_lead"] = {canon(r["stem"]) for r in stems if r.get("doubles") == "yes" and not (r.get("note") or "").startswith("REVIEWER")} \
        or set(T.STEMS_DOUBLE)
    d["names"] = sorted({canon(r["name"]) for r in _tsv(dict_dir / "names.tsv")
                         if r.get("name") and (r.get("use") or "yes").strip().lower() == "yes"} or set(T.NAMES_SEED))
    d["archaic"] = {r["ov_word"]: (r["modern"], int(r["aligned"]), int(r["slots"])) for r in _tsv(dict_dir / "archaic.tsv")}
    d["extra_canon_gen"] = -1


def _extra_canon(lex) -> set:
    d = lex.data
    if d.get("extra_canon_gen") != lex.generation:
        d["extra_canon"] = {canon(w) for w in lex.extra}
        d["extra_canon_gen"] = lex.generation
    return d["extra_canon"]


def known_basis(lex, key: str) -> str | None:
    """Why a canonical key is known: 'ov', 'extra', 'archaic' (through archaic_map), or None."""
    if key in lex.ov:
        return "ov"
    if key in _extra_canon(lex):
        return "extra"
    for v in T.archaic_variants(key):
        if v in lex.ov:
            return "archaic"
    return None


def is_name(lex, key: str) -> bool:
    for n in lex.data.get("names", ()):
        if key.startswith(n) and len(n) >= 3:
            return True
    return False


# ---------------------------------------------------------------------------------------------
# corpus state (per book, refreshed like the Tamil sandhi pair counts)
# ---------------------------------------------------------------------------------------------
QUOTE_CONTEXTS = {"verse", "heading", "psalm_title"}


# "X X": a word followed, after whitespace only, by the same word (the lookahead lets "X X X" count twice)
REPEAT_RE = re.compile(rf"(?<![{MLZ}-])([{MLZ}]+(?:-[{MLZ}]+)*)(?=\s+\1(?![{MLZ}-]))")


def on_index_book(checker, code: str, book, checked: set) -> None:
    raw, nta, space = Counter(), Counter(), Counter()
    quotes = [0, 0]
    for line in book.lines:
        for seg in line.segs:
            if seg.kind != "t" or seg.context not in checked:
                continue
            text = seg.text
            raw.update(T.TOKEN_RE.findall(text))
            if "റ" in text:
                for k, v in T.nta_counts(text).items():
                    nta[k] += v
            if seg.context in QUOTE_CONTEXTS:
                quotes[0] += text.count("“")
                quotes[1] += text.count("”")
            for m in REPEAT_RE.finditer(text):
                space[canon(m.group(1))] += 1
    # each raw form is canonicalised once per book, not once per token
    enc, can, hyph = Counter(), Counter(), Counter()
    for w, n in raw.items():
        enc[T.canon_enc(w)] += n
        can[canon(w)] += n
        if "-" in w:
            parts = w.split("-")
            if len(parts) == 2 and parts[0] == parts[1]:
                hyph[canon(parts[0])] += n
    d = checker.data
    books = d.setdefault("book_state", {})
    old = books.get(code)
    for total_key, new in (("irv_enc", enc), ("irv_canon", can), ("nta", nta), ("redup_hyph", hyph), ("redup_space", space)):
        total = d.setdefault(total_key, Counter())
        if old:
            total.subtract(old[total_key])
            for k in [k for k, v in total.items() if v <= 0]:
                del total[k]
        total.update(new)
    books[code] = {"irv_enc": enc, "irv_canon": can, "nta": nta, "redup_hyph": hyph, "redup_space": space}
    d.setdefault("quotes_book", {})[code] = tuple(quotes)


def samvruthokaram_clusters_all(checker) -> dict:
    """Every ്/ു cluster, verb forms included (the rules doc's 1,659; for the rules page and the tests)."""
    d = checker.data
    if "clusters_all_samvr" not in d:
        enc = {w: n for w, n in d.get("irv_enc", Counter()).items() if n > 0}
        d["clusters_all_samvr"] = kinds.consistency_clusters(enc, T.sk_samvruthokaram_all)
    return d["clusters_all_samvr"]


def conjunct_pairs(checker) -> tuple:
    """Chillu pairs the project writes as conjuncts (Q4: ൽപ → ല്പ ...), as chillu + next letter."""
    return tuple(p for p, conj in T.CHILLU_PAIRS.items() if style_choice(checker, f"chillu:{p}") == conj)


def nta_majority(checker) -> str:
    c = checker.data.get("nta", Counter())
    return max(NTA_CHOICES, key=lambda k: (c.get(k, 0), -NTA_CHOICES.index(k))) if c else "ന്റ"


def nta_target(checker) -> str | None:
    """The chosen nta encoding (Q1), or None while the choice is still 'auto'."""
    v = style_choice(checker, "nta")
    return v if v in T.NTA_FORMS else None


def surface(checker, key: str) -> str:
    """A canonical key as it should be written in this project: the nta target (or majority) encoding and,
    for a word in a samvruthokaram class, the ending the project writes it with: the class choice made in
    Settings, else the word's own majority in the IRV, else the class majority."""
    nta = nta_target(checker) or nta_majority(checker)
    c = T.ending_class(key)
    if not c:
        return T.surface_nta(checker.data.get("best_enc", {}).get(key, key), nta)
    w = T.surface_nta(key, nta)
    choice = style_choice(checker, f"samvruthokaram:{c}")
    if choice not in ("\u0D4D", "\u0D41"):
        forms = checker.data.get("clusters", {}).get("samvruthokaram", {}).get(key[:-1] + "#")
        if forms:
            choice = max(forms.items(), key=lambda kv: (kv[1], kv[0]))[0][-1]
        else:
            choice = samvruthokaram_choice(checker, c)
    if choice == "\u0D41":
        w = w[:-1] + "\u0D41"
    return w


def samvruthokaram_choice(checker, ending_cls: str) -> str:
    """'്', 'ു' or 'off' for an ending class: the Settings choice, else the measured majority unless the
    two counts are within 20 % (then 'off': listed for Benz, no lead)."""
    choice = style_choice(checker, f"samvruthokaram:{ending_cls}")
    if choice in ("\u0D4D", "\u0D41", "off"):
        return choice
    v, u = checker.data.get("samvr_class", {}).get(ending_cls, (0, 0))
    if not v and not u:
        return "off"
    if min(v, u) >= checker.settings["consistency"].get("close", 0.8) * max(v, u):
        return "off"
    return "\u0D4D" if v >= u else "\u0D41"


def on_build_index(checker) -> None:
    lex = checker.lex
    d = checker.data
    accept = checker.settings["lex"].get("irv_accept_min", 5)
    can: Counter = d.get("irv_canon", Counter())
    # words the IRV itself uses often are suggestion targets too (modern forms the OV lacks)
    for w, n in sorted(can.items()):
        if n >= accept and not any(f.severity == "error" for f in T.shape_findings(w)):
            checker.index.add(w)
    # an OV archaism is never proposed: its modern form stands in for it
    for w in list(checker.index.words):
        m = T.modern_of(w)
        if m != w and can.get(m, 0) >= 1:
            checker.index.add(m)
    loose = defaultdict(list)
    for w in checker.index.words:
        loose[T.loose_skeleton(w)].append(w)
    d["loose"] = loose
    # consistency clusters (§6)
    enc = {w: n for w, n in d.get("irv_enc", Counter()).items() if n > 0}
    canon_counts = {w: n for w, n in can.items() if n > 0}
    clusters = {}
    for kind, fn in T.SKELETONS.items():
        src = enc if kind == "samvruthokaram" else canon_counts
        can_cluster = T.SKELETON_CANDIDATE.get(kind)
        if can_cluster:                                    # skip words that can never share a skeleton with another
            src = {w: n for w, n in src.items() if can_cluster(w)}
        clusters[kind] = kinds.consistency_clusters(src, fn)
    names = lex.data.get("names", [])
    heads = {T.name_head(n) for n in names}
    near_names = {w: n for w, n in canon_counts.items() if w[:2].translate(T._FOLD2) in heads}
    clusters["proper_names"] = {sk: forms for sk, forms in kinds.consistency_clusters(near_names, T.sk_name).items()
                                if any(is_name(lex, f) for f in forms)} if names else {}
    d["clusters"] = clusters
    d.pop("clusters_all_samvr", None)
    # samvruthokaram counts per ending class (the house-style toggles, rules §6)
    cls_counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for w, n in enc.items():
        if w.endswith(("\u0D4D", "\u0D41")):
            c = T.ending_class(canon(w))
            if c:
                cls_counts[c][0 if w.endswith("\u0D4D") else 1] += n
    d["samvr_class"] = {k: tuple(v) for k, v in cls_counts.items()}
    # the form (ending kept) each canonical key is written with most often in the IRV, for suggestions
    best: dict[str, tuple[int, str]] = {}
    for e, n in enc.items():
        k = canon(e)
        if k not in best or (n, e) > best[k]:
            best[k] = (n, e)
    d["best_enc"] = {k: e for k, (n, e) in best.items() if e != k}
    pair_counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for w, n in canon_counts.items():
        if "ൽ" in w or "ൻ" in w or "ൺ" in w or "ല്" in w or "ന്" in w or "ണ്" in w:
            p = T.chillu_pair(w)
            if p:
                pair_counts[p][0 if p in w else 1] += n
    d["chillu_pairs"] = {k: tuple(v) for k, v in pair_counts.items()}
    d.pop("cons_memo", None)


# ---------------------------------------------------------------------------------------------
# classification (rules §4.1, steps 1–8)
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


def misspelling_of(lex, key: str):
    """(right form, rule id) for a known misspelling or rejected name form, by exact word or by prefix."""
    hit = _misspelling_of(lex, key)
    if not hit:
        return None
    right, rule = hit
    right = right.replace("േക്കാൾ", "െക്കാൾ")                          # മോശയേക്കാൾ → മോശെയെക്കാൾ
    m = re.match(f"(.*[{T.CONS_ALL}])(ക{T.VIRAMA}ക.*)$", right)
    if m and not m.group(1).endswith(T.VIRAMA) and rule == "ml.style.divine-names":
        right = m.group(1) + "യ" + T.VIRAMA + m.group(2)                   # യഹോവക്ക് → യഹോവയ്ക്ക് (the dative after -അ)
    return right, rule


def _misspelling_of(lex, key: str):
    ms = lex.data.get("misspell", {}).get(key)
    if ms:
        return ms
    suffixes = lex.data.get("suffixes") or T.SUFFIX_SEED
    for wrong, right, rule, any_rest in lex.data.get("misspell_prefix", ()):
        if not key.startswith(wrong) or key.startswith(right):
            continue
        rest = key[len(wrong):]
        if any_rest:
            return right + rest, rule
        if rest == "":
            return right, rule
        if rest == "േ" and right.endswith("െ"):
            return None                                  # the vocative of the right form: മോശേ "O Moses"
        if len(rest) == 1 and T.cls(rest[0]) == "v" and T.cls(right[-1]) == "v":
            return right, rule                           # a sign on a sign-final stem: the bare form
        if rest[0] in "യവ" and len(rest) > 1 and T.cls(rest[1]) in ("v", "_"):
            return right + rest, rule                    # a glide inflection: യാഹോവയുടെ, യാഹോവയ്ക്ക്, മോശയെ, ഈശോയുടെ
        if rest in suffixes and not rest.startswith(("മ", "ത്ത")):
            return right + rest, rule                    # a case ending (never the ം-stem augment മ / ത്ത: മോശമായ = bad)
    return None


def _house(checker, w: str, right: str) -> dict:
    out = surface(checker, right)
    return {"w": out, "op": "sub", "from": w, "to": out, "at": 0, "cls": "house", "pos": "medial", "freq": freq_c(checker, right)}


def classify_first(checker, w: str, irv: int, ov: int):
    lex = checker.lex
    if w in lex.ignored:
        return None
    key = canon(w)
    irv_n, ov_n = irv_c(checker, key, irv), lex.lemma_count.get(key, 0)
    ms = misspelling_of(lex, key)
    if ms and on(checker, ms[1] if ms[1] in RULES else "ml.lex.known-misspelling"):
        return _info("malformed", rule=ms[1], severity="error", sugg=checker.suggest(w), irv=irv_n, ov=ov_n, ov_lemma=ov_n)
    findings = [f for f in T.shape_findings(w) if not f.gated and on(checker, SHAPE_RULE_SWITCH.get(f.rule, f.rule))]
    errors = [f for f in findings if f.severity == "error"]
    if errors:
        f = errors[0]
        return _info("malformed", rule=f.rule, severity="error", sugg=checker.suggest(w), irv=irv_n, ov=ov_n, ov_lemma=ov_n)
    basis = known_basis(lex, key)
    if basis is None and "-" in key and all(known_basis(lex, p) for p in key.split("-") if p):
        basis = "hyphen"
    if basis:
        if on(checker, "ml.lex.archaic-form"):
            a = lex.data.get("archaic", {}).get(key)
            if a:
                sugg = [{"w": surface(checker, a[0]), "op": "sub", "from": w, "to": a[0], "at": 0, "cls": "archaic",
                         "pos": "medial", "freq": freq_c(checker, a[0])}]
                return _info("archaic", rule="ml.lex.archaic-form", severity="warning", sugg=sugg, irv=irv_n, ov=ov_n,
                             ov_lemma=ov_n, extra=_panel({"why": [f"the IRV has {a[0]} in {a[1]} of {a[2]} aligned verses"]}))
        return _info("ok", irv=irv_n, ov=ov_n, ov_lemma=ov_n, extra={"basis": basis} if basis != "ov" else {})
    if findings:
        f = findings[0]
        inline = RULES.get(SHAPE_RULE_SWITCH.get(f.rule, f.rule), ("", "", False))[2]
        return _info("malformed", rule=f.rule, severity="warning", sugg=checker.suggest(w), irv=irv_n, ov=ov_n, ov_lemma=ov_n,
                     extra={} if inline else _panel())
    return None


def _stem_known(checker, key: str) -> bool:
    return known_basis(checker.lex, key) is not None


def _memo(checker, name: str) -> dict:
    memo = checker.data.setdefault(name, {})
    gen = (checker.lex.generation, checker.settings_gen)
    if memo.get("gen") != gen or len(memo) > 60000:
        memo.clear()
        memo["gen"] = gen
    return memo


def inflected(checker, key: str):
    """(base, suffix) when `key` is a known word plus a listed ending (rules §4.1 step 5); memoized."""
    memo = _memo(checker, "inflect_memo")
    if key not in memo:
        s = checker.settings["lex"]
        memo[key] = kinds.strip_suffix(key, checker.lex.data.get("suffixes", T.SUFFIX_SEED), T.stem_candidates,
                                       lambda b: _stem_known(checker, b), s.get("inflect_min_stem", 3))
    return memo[key]


def _head_ok(checker, a: str) -> bool:
    for cand in T.stem_candidates(a, ""):
        if _stem_known(checker, cand) or inflected(checker, cand):
            return True
    return False


def _tail_ok(checker, b: str) -> bool:
    return _stem_known(checker, b) or bool(inflected(checker, b))


def compound(checker, key: str):
    s = checker.settings["compound"]
    if not s.get("enabled", True):
        return None
    return kinds.split_compound(key, lambda a: _head_ok(checker, a), lambda b: _tail_ok(checker, b),
                                lambda b: bool(GRAPHEME_START.match(b)), s.get("min_first", 3), s.get("min_second", 3),
                                checker.lex.data.get("suffixes", T.SUFFIX_SEED))


def classify_extra(checker, w: str, irv: int, ov: int):
    """After classify_first: gated shape warnings, known word + ending, IRV-frequent, compound, unknown
    (rare-near-common decides red)."""
    lex = checker.lex
    key = canon(w)
    irv_n, ov_n = irv_c(checker, key, irv), lex.lemma_count.get(key, 0)
    s = checker.settings["lex"]
    gated = [f for f in T.shape_findings(w) if f.gated and on(checker, SHAPE_RULE_SWITCH.get(f.rule, f.rule))]
    if gated:
        f = gated[0]
        return _info("malformed", rule=f.rule, severity=f.severity, sugg=checker.suggest(w), irv=irv_n, ov=ov_n, ov_lemma=ov_n,
                     extra=_panel())
    name = next((n for n in lex.data.get("names", ()) if len(n) >= 4 and key.startswith(n.rstrip(T.VIRAMA))
                 and key != n), None)
    if name and inflected(checker, key):
        return _info("inflected_ok", bare=name, irv=irv_n, ov=ov_n, ov_lemma=lex.lemma_count.get(name, 0),
                     extra={"suffix": key[len(name.rstrip(T.VIRAMA)):], "basis": "name"})
    hit = inflected(checker, key)
    if hit:
        base, suf = hit
        if base.endswith("ും"):
            deeper = inflected(checker, base)
            if deeper:
                base = deeper[0]
        if base.endswith(("യ്ക്ക്", "ക്ക്")):
            deeper = inflected(checker, base)
            if deeper:
                base = deeper[0]
        return _info("inflected_ok", bare=base, irv=irv_n, ov=ov_n, ov_lemma=lex.lemma_count.get(base, 0), extra={"suffix": suf})
    if irv_n >= s.get("irv_accept_min", 5):
        return _info("irv_ok", irv=irv_n, ov=ov_n, ov_lemma=ov_n)
    parts = compound(checker, key)
    if parts:
        sugg = []
        a, b = parts
        if a.endswith("െ") and b.startswith("പോലെ"):
            fixed = a + "പ" + T.VIRAMA + b
            hit = {"w": keep_form(w, surface(checker, fixed)), "op": "ins", "from": "", "to": "പ" + T.VIRAMA, "at": len(a),
                   "cls": "gemination", "pos": "medial", "freq": freq_c(checker, fixed)}
            return _info("rare_near_common", rule="ml.lex.rare-near-common", severity="warning", sugg=[hit],
                         irv=irv_n, ov=ov_n, ov_lemma=ov_n,
                         extra={} if RULES["ml.lex.rare-near-common"][2] else _panel())
        if a.endswith(T.VIRAMA) and T.cls(b[0]) == "C":
            raw_a = w[:len(w) - len(b)] if w.endswith(b) else a
            sugg = [{"w": f"{raw_a} {b}", "op": "split", "from": w, "to": f"{raw_a} {b}", "at": len(a), "cls": "split",
                     "pos": "medial", "freq": 0}]
        if a.endswith("ി") and b[:1] in "കചതപ" and b[1:2] != T.VIRAMA:
            doubled = a + b[0] + T.VIRAMA + b
            if freq_c(checker, doubled) >= 5 and freq_c(checker, doubled) > 5 * max(1, irv_n):
                sugg = [{"w": surface(checker, doubled), "op": "ins", "from": "", "to": b[0] + T.VIRAMA, "at": len(a),
                         "cls": "gemination", "pos": "medial", "freq": freq_c(checker, doubled)}] + \
                       [x for x in sugg if canon(x["w"]) != doubled]
        return _info("compound", parts=(parts[0], parts[1], "plain"), sugg=sugg, irv=irv_n, ov=ov_n, ov_lemma=ov_n)
    sugg = checker.suggest(w)
    if on(checker, "ml.lex.rare-near-common"):
        for fixed in morphology_fixes(key):
            if known_basis(lex, fixed) or inflected(checker, fixed) or freq_c(checker, fixed) >= 5:
                hit = {"w": keep_form(w, surface(checker, fixed)), "op": "sub", "from": w, "to": fixed, "at": 0,
                       "cls": "gemination" if "പ്പെ" in fixed else "vowel_length", "pos": "medial", "freq": freq_c(checker, fixed)}
                return _info("rare_near_common", rule="ml.lex.rare-near-common", severity="warning",
                             sugg=[hit] + [x for x in sugg if x["w"] != hit["w"]], irv=irv_n, ov=ov_n, ov_lemma=ov_n)
    if on(checker, "ml.lex.rare-near-common") and ov_n + irv_n <= s.get("rare_max", 2) and ov_n < 3:
        best = _near_common(checker, key, ov_n + irv_n, s, w)
        if best:
            inline = RULES["ml.lex.rare-near-common"][2]
            return _info("rare_near_common", rule="ml.lex.rare-near-common", severity="warning",
                         sugg=[best] + [x for x in sugg if x["w"] != best["w"]], irv=irv_n, ov=ov_n, ov_lemma=ov_n,
                         extra={} if inline else _panel())
    return _info("unknown", sugg=sugg, irv=irv_n, ov=ov_n, ov_lemma=ov_n)


PASSIVE_RE = re.compile(f"(?<=[ാിീുൂെേൈൊോൗ{T.CONS_ALL}])(?<!{T.VIRAMA})പെ(?=ട)")


def morphology_fixes(key: str) -> list[str]:
    """The passive auxiliary doubles its പ after a vowel (രക്ഷപെടുക → രക്ഷപ്പെടുക); the clitic -ലോ is long
    (ആണല്ലൊ → ആണല്ലോ)."""
    out = []
    if "പെട" in key and PASSIVE_RE.search(key):
        out.append(PASSIVE_RE.sub("പ്" + "പെ", key, count=1))
    if key.endswith("ല്ലൊ"):
        out.append(key[:-1] + "ോ")
    return out


def _near_common(checker, key: str, freq: int, s: dict, raw: str | None = None):
    """The frequent near form of a rare word, if any: a candidate from the skeleton group (substitutions)
    or from the one-deletion index (a dropped or added sign: പിടച്ച് / പിടിച്ച്) at cost ≤ rare_cost."""
    best = None
    cands = set(checker.data.get("loose", {}).get(T.loose_skeleton(key), ())) | checker.index.candidates(key)
    for c in sorted(cands):
        if c == key or freq_c(checker, c) < s.get("rare_ratio", 50) * max(1, freq):
            continue
        h = rank(checker, raw or key, c, limit=s.get("rare_cost", 0.5))
        if h:
            _, ops = kinds.confusion_distance(key, c, T.CONFUSION, limit=s.get("rare_cost", 0.5))
            if any(o[1] in ("chillu_conjunct", "nasal") for o in ops):
                continue                                   # a house-style question (Q4 / Q5), not a misspelling
        if h and h[0] <= s.get("rare_cost", 0.5) and (best is None or h[:4] < best[:4]):
            best = h
    if best and "കയ്യ" in best[3]:
        alt = best[3].replace("കയ്യ", "കൈയ")                               # the standard form is കൈയിൽ
        if alt in checker.index.id or freq_c(checker, alt):
            return {**best[4], "w": surface(checker, alt), "freq": freq_c(checker, alt)}
    return best[4] if best else None


def malformed(w: str):
    return None                        # Malayalam shape rules need settings: see classify_first


# ---------------------------------------------------------------------------------------------
# suggestions
# ---------------------------------------------------------------------------------------------
POS_RANK = {"medial": 0, "initial": 1, "final": 2}
GRAPHEME_START = re.compile(f"[{T.CONS_ALL}{T.IVOWEL}{T.CHILLU}]")


def rank(checker, w: str, c: str, limit: float = 2.0):
    key = canon(w)
    if c == key or c == w:
        return None
    m = T.modern_of(c)
    if m != c and checker.data.get("irv_canon", {}).get(m, 0) >= 1:
        return None                      # an OV archaism: its modern form is the candidate
    if c in _extra_targets(checker, w):
        return None
    cost, ops = kinds.confusion_distance(key, c, T.CONFUSION, limit=limit)
    if cost == float("inf") or any(o[1] in ("participle", "compound_head", "vocative") for o in ops):
        return None
    _, cls, op, frm, to, at = ops[0] if ops else (0, "other", "sub", "", "", 0)
    n = len(kinds.units(key, T.CONFUSION))
    pos = "initial" if at == 0 else "final" if at >= n - 2 else "medial"
    f = freq_c(checker, c)
    out = keep_form(w, surface(checker, c))
    return (round(cost, 3), POS_RANK[pos], -f, c,
            {"w": out, "op": op, "from": frm, "to": to, "at": at, "cls": cls, "pos": pos, "freq": f, "cost": round(cost, 2)})


def more_candidates(checker, w: str) -> set:
    key = canon(w)
    out = set(checker.index.candidates(key)) if key != w else set()
    out |= set(checker.data.get("loose", {}).get(T.loose_skeleton(key), ()))
    return out


def _extra_targets(checker, w: str) -> set:
    """Canonical keys already offered by extra_suggestions (memoized: rank() asks once per candidate)."""
    memo = checker.data.setdefault("extra_targets", {})
    if memo.get("gen") != (checker.lex.generation, checker.settings_gen):
        memo.clear()
        memo["gen"] = (checker.lex.generation, checker.settings_gen)
    if w not in memo:
        if len(memo) > 20000:
            memo.clear()
            memo["gen"] = (checker.lex.generation, checker.settings_gen)
        memo[w] = {h[3] for h in extra_suggestions(checker, w)}
    return memo[w]


def space_is_right(a: str) -> bool:
    """A first part ending in the inherent -അ (സകല, നിത്യ, തങ്ക, വിശിഷ്ട) is an adjective before a noun: the
    words are written apart, not joined with the യ-glide (reviewed 1 Oct 2026)."""
    a = T.canon_enc(a)
    return bool(a) and (T.cls(a[-1]) in ("C", "L") or a in DETERMINERS)


DETERMINERS = {"എല്ലാ", "ഓരോ", "ആ", "ഈ", "ഏതു", "മറ്റു", "പല"}


def atomic(w: str) -> str:
    """Legacy ZWJ chillus written as atomic chillus (ര്‍ → ർ), the nta cluster left alone."""
    return T.LEGACY_CHILLU_NOT_NTA_RE.sub(lambda m: T.CHILLU_OF[m.group(1)], w)


def fix_numeral(w: str) -> str:
    """ഏഴ് (seven) is written with the long ഏ: എഴായിരം → ഏഴായിരം, എഴുനൂറ് → ഏഴുനൂറ്."""
    return re.sub("(^|ത്തി|റ്റി)എഴ(?=ാ|്|$)", r"\1ഏഴ", w)


def medial_vowel_split(w: str) -> tuple[str, str] | None:
    """(a, b) of a token with an independent vowel inside it, split at that vowel."""
    for f in T.shape_findings(w):
        if f.rule == "ml.shape.independent-vowel-mid-word":
            return w[:f.start], w[f.start:]
    return None


def extra_suggestions(checker, w: str) -> list:
    out = []
    key = canon(w)
    ms = misspelling_of(checker.lex, key)
    if ms:
        out.append((-2, 0, -freq_c(checker, ms[0]), ms[0], _house(checker, w, ms[0])))
    for f in T.shape_findings(w):
        fixed = f.fixed(w)
        if fixed and fixed != w:
            out.append((-1, 0, -freq_c(checker, canon(fixed)), canon(fixed),
                        {"w": fixed, "op": "fix", "from": w[f.start:f.end], "to": f.fix, "at": f.start, "cls": "shape",
                         "pos": "medial", "freq": freq_c(checker, canon(fixed))}))
            break
    split = medial_vowel_split(w)
    if split:
        a, b = split
        a, b = atomic(fix_numeral(a)), atomic(fix_numeral(b))
        joined = T.sandhi_join(T.canon_enc(a), b)
        space_first = space_is_right(a)
        if joined:
            out.append((-1, 1 if space_first else 0, 0, canon(joined), {"w": joined, "op": "sandhi", "from": w, "to": joined,
                                                                          "at": len(a), "cls": "sandhi", "pos": "medial",
                                                                          "freq": freq_c(checker, canon(joined))}))
        out.append((-1, 0 if space_first else 1, 0, a + " " + b, {"w": a + " " + b, "op": "split", "from": w,
                                                                    "to": a + " " + b, "at": len(a), "cls": "split",
                                                                    "pos": "medial", "freq": 0}))
    return out


# ---------------------------------------------------------------------------------------------
# warnings (text runs): the grouped L0 "Encoding" issue, punctuation, style
# ---------------------------------------------------------------------------------------------
TAMIL_RE = re.compile("[\u0B80-\u0BFF]+")
DEVANAGARI_RE = re.compile("[\u0900-\u0963\u0966-\u097F]+")
LATIN_IN_WORD_RE = re.compile(f"(?<=[{ML}])[A-Za-z]+|[A-Za-z]+(?=[{ML}])")
STRAIGHT_QUOTE_RE = re.compile("[\"']")
QUOTE_DIRECTION_RE = re.compile(rf"(?:(?<=^)|(?<=[\s(:,]))”(?=[{ML}])")
QUOTE_CLOSING_RE = re.compile(rf"(?<=[{MLZ}.!?,;])“(?=\s|$|[.,;:!?)])")              # ഇല്ല“ (PSA … round 3)
DASH_RE = re.compile("\u2013")
INVISIBLE_RE = re.compile("[\u200B\uFEFF\u00A0\u2060]")
NUMBER_CASE_RE = re.compile(rf"(?<=\d) (?=[ൺൻർൽൾ](?![{MLZ}])|(?:ിൽ|ിന്|ിലെ|ാം)(?![{MLZ}]))")
NEVER_RE = re.compile("(?!)")
HYPHEN_RE = re.compile(rf"(?<=[{MLZ}])- +(?=[{ML}])|(?<=[{MLZ}]) +-(?=[{ML}])|--+|(?<![{MLZ}\d\s])-(?=[{ML}])|(?<=[{MLZ}])-(?![{ML}\d])")


def _span(a: str, b: str) -> tuple[int, int, int]:
    """(start, end in a, end in b) of the part that differs."""
    i = 0
    while i < min(len(a), len(b)) and a[i] == b[i]:
        i += 1
    j = 0
    while j < min(len(a), len(b)) - i and a[len(a) - 1 - j] == b[len(b) - 1 - j]:
        j += 1
    return i, len(a) - j, len(b) - j


def extra_warnings(checker, seg, next_seg) -> list:
    text = seg.text
    out = []

    def add(kind, s, e, **kw):
        out.append({"kind": kind, "s": s, "e": max(e, s + 1) if s < len(text) else e, "rule": kw.pop("rule", kind), **kw})

    if on(checker, "ml.norm.encoding"):
        nta_on = on(checker, "ml.norm.nta-encoding")
        norm, what = T.normalize_run(text, nta_target=nta_target(checker) if nta_on else None,
                                     nta_majority=nta_majority(checker) if nta_on else None,
                                     zwnj_final=on(checker, "ml.norm.zwnj-final"),
                                     zwnj_after_virama=on(checker, "ml.norm.zwnj-after-virama"),
                                     conjunct_pairs=conjunct_pairs(checker))
        rules = Counter()
        for rid, n, _ in what:
            rules[rid] += n
        notes = ", ".join(f"{note} ({n}×)" if n > 1 else note for _, n, note in what)
        if norm != text:
            s, e, e2 = _span(text, norm)
            if e == s:
                e, e2 = s + 1, e2 + 1
            add("ml.norm.encoding", s, e, fix=norm[s:e2], fix_label="Fix the encoding (" + notes + ")",
                label="encoding: " + notes, rules=dict(rules))
        elif what:
            m = T.NTA_RE.search(text)
            s = m.start() if m else 0
            add("ml.norm.nta-encoding", s, s + 1, panel=True, label="encoding: " + notes, rules=dict(rules))
    if on(checker, "ml.norm.invisible"):
        for m in INVISIBLE_RE.finditer(text):
            i = m.start()
            between = 0 < i < len(text) - 1 and not text[i - 1].isspace() and not text[i + 1].isspace()
            fix = " " if between or m.group() == "\u00A0" else ""
            add("ml.norm.invisible", i, i + 1, fix=fix, fix_label="Replace with a space" if fix else "Remove it",
                label="invisible character (zero-width space, BOM, no-break space or word joiner)")
    if on(checker, "ml.punct.number-case"):
        for m in NUMBER_CASE_RE.finditer(text):
            add("ml.punct.number-case", m.start(), m.end(), panel=True, fix="-", fix_label="Join with a hyphen",
                label="a case ending after a number is joined with a hyphen (64-ൽ)")
    if on(checker, "ml.norm.colon-for-visarga"):
        for i in T.visarga_colons(text):
            add("ml.norm.colon-for-visarga", i, i + 1, fix="\u0D03", fix_label="Change to the visarga ഃ",
                label="ASCII colon typed for the visarga ഃ")
    if on(checker, "ml.shape.mixed-script"):
        for m in TAMIL_RE.finditer(text):
            add("ml.shape.mixed-script", m.start(), m.end(), fix=m.group().translate(T.TAMIL_LOOKALIKE),
                fix_label="Replace with the Malayalam look-alike letters", label="Tamil letters in Malayalam text")
        for m in DEVANAGARI_RE.finditer(text):
            add("ml.shape.mixed-script", m.start(), m.end(), label="Devanagari letters in Malayalam text")
        for m in LATIN_IN_WORD_RE.finditer(text):
            add("ml.shape.mixed-script", m.start(), m.end(), label="Latin letters inside a Malayalam word")
    if on(checker, "ml.punct.straight-quote"):
        depth = 0
        for m in STRAIGHT_QUOTE_RE.finditer(text):
            before = text[:m.start()]
            opening = not before or before[-1].isspace() or before[-1] in "(“‘"
            if m.group() == '"':
                fix = "“" if opening else "”"
            else:
                fix = "‘" if opening else "’"
            add("ml.punct.straight-quote", m.start(), m.end(), fix=fix, fix_label=f"Change to {fix}",
                label="straight quote (the IRV uses “ ” ‘ ’)")
            depth += 1
    if on(checker, "ml.punct.quote-direction"):
        for m in QUOTE_DIRECTION_RE.finditer(text):
            add("ml.punct.quote-direction", m.start(), m.end(), fix="“", fix_label="Change to “",
                label="closing quote ” used to open a quotation")
        for m in QUOTE_CLOSING_RE.finditer(text):
            add("ml.punct.quote-direction", m.start(), m.end(), fix="”", fix_label="Change to ”",
                label="opening quote “ used to close a quotation")
    if on(checker, "ml.shape.hyphen"):
        for m in HYPHEN_RE.finditer(text):
            add("ml.shape.hyphen", m.start(), m.end(), panel=True, label="loose hyphen")
    if on(checker, "ml.punct.dash") and seg.context == "verse":
        for m in DASH_RE.finditer(text):
            add("ml.punct.dash", m.start(), m.end(), panel=True, fix="\u2014", fix_label="Change to the em dash —",
                label="en dash where the IRV uses the em dash")
    hyph, space = checker.data.get("redup_hyph", Counter()), checker.data.get("redup_space", Counter())
    red_on, rep_on = on(checker, "ml.style.reduplication-hyphen"), on(checker, "ml.style.repeated-word")
    if red_on or rep_on:
        for w1, w2, a, b in kinds.adjacent_pairs(text, T.TOKEN_RE):
            if w1 != w2:
                continue
            k = canon(w1)
            if hyph[k] >= 3 and hyph[k] > space[k] and red_on:
                add("ml.style.reduplication-hyphen", a, b, panel=True, fix=f"{w1}-{w1}", fix_label=f"Change to {w1}-{w1}",
                    label=f"the IRV writes {w1}-{w1} {hyph[k]}× (and “{w1} {w1}” {space[k]}×)")
            elif rep_on and not hyph[k] and space[k] < 3:
                add("ml.style.repeated-word", b - len(w2), b, panel=True, label="word repeated")
    return out


def token_warning(checker, text, w, a, b, s):
    return None


# ---------------------------------------------------------------------------------------------
# chapter-level: quotes that do not balance (counts only until the quotation policy is decided, Q9)
# ---------------------------------------------------------------------------------------------

ARCHAIC_SKIP_CONTEXTS = {"heading", "intro", "title"}


def chapter_leads(checker, book, n, blocks) -> None:
    for block in blocks:
        for seg in block["segs"]:
            if seg.get("ctx") in ARCHAIC_SKIP_CONTEXTS and seg.get("tokens"):
                seg["tokens"] = [t for t in seg["tokens"] if t["status"] != "archaic"]
    if not on(checker, "ml.punct.quotes-unbalanced"):
        return
    opens = closes = 0
    first = None
    for block in blocks:
        for seg in block["segs"]:
            if seg.get("k") != "t" or not seg.get("checked") or seg.get("stream", 0) != 0 or "warnings" not in seg:
                continue
            if seg.get("ctx") not in QUOTE_CONTEXTS:
                continue
            opens += seg["t"].count("“")
            closes += seg["t"].count("”")
            if first is None and ("“" in seg["t"] or "”" in seg["t"]):
                first = seg
    if first is not None and opens != closes:
        i = min(x for x in (first["t"].find("“"), first["t"].find("”")) if x >= 0)
        first["warnings"].append({"kind": "ml.punct.quotes-unbalanced", "s": i, "e": i + 1, "panel": True,
                                  "rule": "ml.punct.quotes-unbalanced",
                                  "label": f"quotes in this chapter: “ {opens} / ” {closes} (do not balance)"})


# ---------------------------------------------------------------------------------------------
# leads: spelling consistency (§6) and grammar (§5)
# ---------------------------------------------------------------------------------------------

def consistency_lead(checker, w: str):
    """Memoized per word; the memo is dropped when the lexicon, settings or index change."""
    key = (checker.lex.generation, checker.settings_gen, id(checker.data.get("clusters")), len(checker.ignored_pairs))
    memo = checker.data.get("cons_memo")
    if memo is None or memo[0] != key:
        memo = checker.data["cons_memo"] = (key, {})
    if w not in memo[1]:
        memo[1][w] = _consistency_lead(checker, w)
    return memo[1][w]


def _ignores_of(checker, w2: str) -> int:
    return sum(1 for _, b in checker.ignored_pairs if b == w2)


def doubled_after_i(forms: dict, checker=None) -> str | None:
    """Of two forms that differ by a stop doubled after a vowel, followed by a vowel (പകുതിപ്പേർ / പകുതിപേർ,
    സാക്ഷ്യപ്പെട്ടകം / സാക്ഷ്യപെട്ടകം), the doubled one: the junction doubling is standard.  Not before a
    conjunct (മഹത്ത്വം / മഹത്വം is the house-style question Q5).  Both parts have at least 3 code points
    (not കൊതി / കൊത്തി, one word), and the stop is not the end of a known first word (ഇടത് + ഭാഗം)."""
    for f in forms:
        for m in re.finditer(f"(?<![{T.VIRAMA}ൺൻർൽൾൿ])([ചതപ]){T.VIRAMA}\\1(?!{T.VIRAMA})", f):
            single = f[:m.start() + 1] + f[m.start() + 3:]
            head, tail = f[:m.start()], f[m.start() + 2:]  # the second word starts at the doubled stop
            if len(head) < 3 or len(tail) < 3:
                continue
            if checker is not None and known_basis(checker.lex, head + m.group(1) + T.VIRAMA):
                continue                                   # the stop belongs to the first word (ഇടത്)
            if single in forms and (checker is None or known_basis(checker.lex, tail) or inflected(checker, tail)):
                return f                                   # a junction (സാക്ഷ്യ + പെട്ടകം), not the stem's own stop (വലതു)
    return None


def keep_form(w: str, out: str) -> str:
    """A suggestion keeps what the word itself has where the change is elsewhere: its ്/ു ending
    (നിൽക്കുന്നത് → നില്ക്കുന്നത്, not -തു) and a ZWNJ after a virama (ചെയ്‌വീൻ → ചെയ്‌വിൻ)."""
    enc = T.canon_enc(w)
    if enc[-1:] in ("്", "ു") and out[-1:] in ("്", "ു"):
        out = out[:-1] + enc[-1]
    for m in re.finditer(f"([{T.CONS_ALL}]){T.VIRAMA}{T.ZWNJ}([{T.CONS_ALL}])", w):
        plain = m.group(1) + T.VIRAMA + m.group(2)
        if plain in out and m.group() not in out:
            out = out.replace(plain, m.group(), 1)
    return out


def yk_by_sound(forms: dict) -> str | None:
    """The form a യ്ക്ക / ക്ക pair should take, from the sound before ക്ക: after the inherent -അ (or ാ െ േ) the
    dative and the verb take യ്ക്ക (യഹോവയ്ക്ക്, ജനതയ്ക്ക്, അയയ്ക്കും); after ി / ീ they take ക്ക (ഇരിക്കും,
    ഭൂമിക്ക്).  None when the sound does not decide (the IRV majority is used)."""
    yk = next((f for f in forms if f"യ{T.VIRAMA}ക{T.VIRAMA}ക" in f), None)
    k = next((f for f in forms if f is not yk and f"ക{T.VIRAMA}ക" in f), None)
    if not yk or not k:
        return None
    i = k.find(f"ക{T.VIRAMA}ക")
    before = k[i - 1] if i > 0 else ""
    after = k[i + 3:]
    if before == "ി":                                  # not ീ: തീയ്ക്ക് "to fire" keeps യ
        return k
    # after -അ only for a noun ending (dative ക്ക്, ക്കായി, ക്കുള്ള ...): a verb may be a different word
    # (മറക്കുന്നു "forgets" / മറയ്ക്കുന്നു "hides"), and after േ the IRV writes ക്ക (താഴേക്ക്, -ിലേക്ക്)
    if T.cls(before) == "C" and after in (T.VIRAMA, "ു", "ായി", "ുള്ള", "ോ", "ുവേണ്ടി", "ുതന്നെ"):
        return yk
    if T.cls(before) == "C":
        return KEEP_YK                                 # a verb after -അ: its യ്ക്ക form is standard (പകയ്ക്കുന്നു)
    return None


KEEP_YK = "\0keep-yk"


def _consistency_lead(checker, w: str):
    s = checker.settings["consistency"]
    lex = checker.lex
    clusters = checker.data.get("clusters", {})
    key, enc = canon(w), T.canon_enc(w)
    for kind in (*T.SKELETONS, "proper_names"):
        rid = CONSISTENCY_RULE[kind]
        if not on(checker, rid):
            continue
        fn = T.SKELETONS.get(kind, T.sk_name)
        form = enc if kind == "samvruthokaram" else key
        sk = fn(form)
        forms = clusters.get(kind, {}).get(sk)
        if not forms or form not in forms or sum(forms.values()) < s.get("min_total", 3):
            continue
        if kind != "samvruthokaram" and any(T.never_pair(canon(f)) for f in forms):
            continue                                       # different words or names that look alike (reviewed)
        w2 = f"≈{kind}:{sk}"
        if _ignores_of(checker, w2) >= s.get("abstain_after", 3):
            continue                                       # learned: the reviewer ignored this skeleton three times
        ranked = sorted(forms.items(), key=lambda kv: (-kv[1], kv[0]))
        total = sum(forms.values())
        choice = style_choice(checker, f"{kind}:{sk}")
        target, explicit = None, choice != "auto"
        if choice == "off":
            continue
        if choice == "auto":
            if kind == "samvruthokaram":
                ecls = T.ending_class(key) or ""
                cls_choice = style_choice(checker, f"samvruthokaram:{ecls}")
                if cls_choice == "off":
                    continue
                if cls_choice not in ("്", "ു"):
                    continue                           # Q2 / N3: silent until the class is set in Settings
                explicit = True
                target = sk[:-1] + cls_choice
            elif kind == "yk":
                target = yk_by_sound(forms)
                if target == KEEP_YK:
                    if f"യ{T.VIRAMA}ക{T.VIRAMA}ക" in form:
                        continue                       # never propose dropping the യ of a verb after -അ
                    target = None                      # the ക്ക form: the IRV majority decides
            elif kind == "gemination":
                target = doubled_after_i(forms, checker)
            elif kind == "chillu_conjunct":
                pair = T.chillu_pair(key)
                pc = style_choice(checker, f"chillu:{pair}") if pair else "auto"
                if pc == "off":
                    continue
                if pc in T.CHILLU_PAIRS or pc in T.CHILLU_PAIRS.values():
                    target, explicit = next((f for f in forms if pc in f), None), True
            if target is None:
                top, n_top = ranked[0]
                close = len(ranked) > 1 and ranked[1][1] >= s.get("close", 0.8) * n_top
                if form == top or close or forms[form] / total >= s.get("minority_max", 0.25):
                    continue
                target = top
            if form == target or target not in forms:
                continue
        else:
            target = choice if choice in forms else next((f for f in forms if choice in f), None)
            if target is None or form == target:
                continue
        if not explicit and kind != "samvruthokaram" and lex.lemma_count.get(canon(form), 0) >= s.get("ov_veto", 3):
            continue                                       # the minority form is an OV word in its own right
        proposed = keep_form(w, surface(checker, canon(target))) if kind != "samvruthokaram" \
            else T.surface_nta(target, nta_target(checker) or nta_majority(checker))
        why = ["IRV forms: " + ", ".join(f"{f} {c}×" for f, c in ranked[:5])]
        if choice != "auto":
            why.append(f"house style chosen in Settings: {choice}")
        label = kind.replace("_", " ")
        inline = RULES[rid][2]
        return {"kind": "consistency", "rule": rid, "proposed": proposed, "basis": kind,
                "strength": "rule" if inline else "panel", "panel": not inline,
                "w2": w2, "pair": w, "why": why, "tag": f"{label} {forms[form]}:{forms[target]}",
                "title": f"spelling consistency ({label})", "issue_type": "Spelling consistency",
                "ignore_label": f"Stop flagging {w} for {label} consistency"}
    return None


def locative_map(checker) -> dict:
    """undoubled locative form → (stem, doubled form) for the stems of stems.tsv, built once per lexicon."""
    memo = _memo(checker, "locative_map")
    if "map" not in memo:
        m = {}
        for stem in checker.lex.data.get("stems_lead", ()):
            for suf in T.LOCATIVE_SUFFIXES:
                m[stem[:-1] + suf] = (stem, stem + stem[-2] + suf)      # വീടിൽ → (വീട്, വീട്ടിൽ)
        memo["map"] = m
    return memo["map"]


def grammar_leads(checker, toks: list, i: int) -> list[dict]:
    """§5 leads for toks[i] = (word, run, s, e, adjacent_to_previous, text_after)."""
    w, r, s, e, adj, after = toks[i]
    key = canon(w)
    out = []
    prev = toks[i - 1] if i > 0 and adj and toks[i - 1][1] == r else None
    if key == "ഉം" and prev and on(checker, "ml.gram.standalone-um"):
        fused = T.join(T.canon_enc(prev[0]), "ഉം")
        out.append({"kind": "grammar", "rule": "ml.gram.standalone-um", "proposed": fused, "basis": "sandhi", "strength": "panel", "panel": True, "w1": prev[0], "w2": w, "s": prev[2], "e": e,
                    "pair": f"{prev[0]} {w}", "why": ["ഉം is a clitic: it joins the previous word (sandhi.tsv)"],
                    "tag": "clitic ഉം", "title": "standalone ഉം", "issue_type": "Grammar"})
    elif key in ("ഓ", "ഏ") and prev and on(checker, "ml.gram.standalone-o") and not after.lstrip().startswith(("!", ",")):
        fused = T.join(T.canon_enc(prev[0]), key)
        out.append({"kind": "grammar", "rule": "ml.gram.standalone-o", "proposed": fused, "basis": "sandhi", "strength": "panel",
                    "panel": True, "w1": prev[0], "w2": w, "s": prev[2], "e": e, "pair": f"{prev[0]} {w}",
                    "why": [f"{w} is a clitic: it joins the previous word (confidence 0.6)"], "tag": f"clitic {w}",
                    "title": f"standalone {w}", "issue_type": "Grammar"})
    if key.endswith("ംകൾ") and on(checker, "ml.gram.anusvara-plural"):
        proposed = key[:-3] + "ങ്ങൾ"
        out.append({"kind": "grammar", "rule": "ml.gram.anusvara-plural", "proposed": surface(checker, proposed), "basis": "plural",
                    "strength": "panel", "panel": True, "w1": w, "w2": "≈ംകൾ", "s": s, "e": e, "pair": w,
                    "why": ["the plural ending is ങ്ങൾ (ംകൾ is never written)"], "tag": "plural ങ്ങൾ",
                    "title": "plural written ംകൾ", "issue_type": "Grammar"})
    if on(checker, "ml.gram.locative-gemination"):
        hit = locative_map(checker).get(key)
        if hit:
            stem, proposed = hit
            out.append({"kind": "grammar", "rule": "ml.gram.locative-gemination", "proposed": surface(checker, proposed),
                        "basis": "stems", "strength": "panel", "panel": True, "w1": w, "w2": "≈locative", "s": s, "e": e,
                        "pair": w, "why": [f"{stem} doubles its final consonant in the locative (stems.tsv): {proposed}"],
                        "tag": "locative", "title": "locative without the doubling", "issue_type": "Grammar"})
    if on(checker, "ml.gram.medial-vowel-sandhi"):
        split = medial_vowel_split(w)
        if split:
            split = (atomic(fix_numeral(split[0])), atomic(fix_numeral(split[1])))
            joined = T.sandhi_join(T.canon_enc(split[0]), split[1])
            spaced = f"{split[0]} {split[1]}"
            if joined:
                first, second = (spaced, joined) if space_is_right(split[0]) else (joined, spaced)
                inline = RULES["ml.gram.medial-vowel-sandhi"][2]
                out.append({"kind": "grammar", "rule": "ml.gram.medial-vowel-sandhi", "proposed": first, "basis": "sandhi",
                            "strength": "rule" if inline else "panel", "panel": not inline, "w1": w, "w2": "≈sandhi", "s": s, "e": e, "pair": w,
                            "why": [f"independent vowel inside the word: {first}; or {second}"],
                            "tag": "sandhi", "title": "vowel inside a word", "issue_type": "Grammar"})
    return out


ABBREV_AFTER_RE = re.compile(r"\.?\s*\d+[:.]\d")


def line_leads(checker, toks: list, line) -> list:
    leads = []
    for i, (w, r, s, e, adj, after) in enumerate(toks):
        abbrev = len(w) <= 5 and (bool(ABBREV_AFTER_RE.match(after))           # (വെളി 1:9)
                                  or (line.style.startswith("toc") and after.startswith(".")))   # 	oc2 വെളി.
        visarga = after == ":" and len(w) <= 3                             # മന:പൂർവ്വം (colon typed for ഃ)
        c = None if abbrev or visarga else consistency_lead(checker, w)
        if c:
            leads.append({**c, "w1": w, "run": r, "s": s, "e": e, "ignored": (w, c["w2"]) in checker.ignored_pairs})
        for g in grammar_leads(checker, toks, i):
            leads.append({**g, "run": r, "ignored": (g["w1"], g["w2"]) in checker.ignored_pairs})
    return leads


# ---------------------------------------------------------------------------------------------
# reports, rules page, settings spec
# ---------------------------------------------------------------------------------------------
STATUS_RULE = {"unknown": "ml.lex.unknown"}


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
            if w.get("rules"):
                for rid, n in w["rules"].items():
                    c[rid] += n
            else:
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
    nta = checker.data.get("nta", Counter())
    qb = checker.data.get("quotes_book", {})
    unbalanced = sorted(((code, o, c) for code, (o, c) in qb.items() if o != c), key=lambda x: -abs(x[1] - x[2]))
    n_ms = len(checker.lex.data.get("misspell", {})) + len(checker.lex.data.get("misspell_prefix", []))
    return {"lang": "ml", "rules": rows, "statuses": statuses, "books_counted": books, "errors": [], "file": path,
            "misspellings": n_ms, "lexicon_gen": checker.lex.generation,
            "nta": {k: nta.get(k, 0) for k in NTA_CHOICES}, "nta_target": nta_target(checker), "nta_majority": nta_majority(checker),
            "samvruthokaram_classes": {k: list(v) for k, v in checker.data.get("samvr_class", {}).items()},
            "quotes_unbalanced_books": [{"book": b, "open": o, "close": c} for b, o, c in unbalanced],
            "message": f"{n_ms} known misspellings / rejected name forms loaded from {path}"}


def style_toggles(checker) -> list[dict]:
    s = checker.settings["consistency"]
    d = checker.data
    out = [{"id": "unknown_color", "label": "Unknown (not verifiable) words are shown",
            "options": [{"value": "grey", "label": "light grey (red only for rare-near-common)"}, {"value": "red", "label": "red, as for Tamil"}],
            "default": "grey", "close": False, "total": 10 ** 9, "fixed": True}]
    nta = d.get("nta", Counter())
    maj = nta_majority(checker)
    out.append({"id": "nta", "label": "nta cluster encoding (Q1): " + " / ".join(f"{k} {nta.get(k, 0)}" for k in NTA_CHOICES),
                "options": [{"value": "auto", "label": f"auto (flag a mix only; majority {maj})"}]
                + [{"value": k, "label": f"{k}  ({nta.get(k, 0)}×)" + ("" if k != "ന്റ" else " — Unicode-recommended")} for k in NTA_CHOICES]
                + [{"value": "off", "label": "no lead"}], "default": "auto", "close": False, "total": 10 ** 9 - 1, "fixed": True})
    for ecls, text in T.ENDING_CLASSES.items():
        v, u = d.get("samvr_class", {}).get(ecls, (0, 0))
        close = min(v, u) >= s.get("close", 0.8) * max(v, u, 1)
        auto = ("ask at every word (Q2); forms within 20 %" if close
                else f"ask at every word (Q2); majority {'്' if v >= u else 'ു'}")
        out.append({"id": f"samvruthokaram:{ecls}", "label": f"ending ്/ു, {text}: ് {v} / ു {u}",
                    "options": [{"value": "auto", "label": f"auto ({auto})"}, {"value": "\u0D4D", "label": f"് ({v}×)"},
                                {"value": "\u0D41", "label": f"ു ({u}×)"}, {"value": "off", "label": "no lead"}],
                    "default": "auto", "close": close, "total": v + u, "fixed": True})
    for pair, conj in T.CHILLU_PAIRS.items():
        a, b = d.get("chillu_pairs", {}).get(pair, (0, 0))
        if not a and not b:
            continue
        close = min(a, b) >= s.get("close", 0.8) * max(a, b, 1)
        auto = "undecided, forms within 20 % — please choose (Q4)" if close else f"majority: {pair if a >= b else conj}"
        out.append({"id": f"chillu:{pair}", "label": f"chillu vs conjunct {pair} / {conj}: {a} / {b}",
                    "options": [{"value": "auto", "label": f"auto ({auto})"}, {"value": pair, "label": f"{pair} ({a}×)"},
                                {"value": conj, "label": f"{conj} ({b}×)"}, {"value": "off", "label": "no lead"}],
                    "default": "auto", "close": close, "total": a + b, "fixed": True})
    rest = []
    for kind, groups in d.get("clusters", {}).items():
        for sk, forms in groups.items():
            ranked = sorted(forms.items(), key=lambda kv: (-kv[1], kv[0]))
            if len(ranked) < 2 or ranked[1][1] < s.get("style_min", 20):
                continue
            (a, na), (b, nb) = ranked[0], ranked[1]
            close = nb >= s.get("close", 0.8) * na
            auto = "undecided, forms within 20 % — please choose" if close else f"majority: {a}"
            opts = [{"value": "auto", "label": f"auto ({auto})"}] + \
                   [{"value": f, "label": f"{f}  ({c}×)"} for f, c in ranked[:4]] + [{"value": "off", "label": "no lead"}]
            rest.append({"id": f"{kind}:{sk}", "label": f"{kind.replace('_', ' ')}: " + " / ".join(f"{f} {c}" for f, c in ranked[:3]),
                         "options": opts, "default": "auto", "close": close, "total": na + nb})
    rest.sort(key=lambda t: (not t["close"], -t["total"], t["id"]))
    return out + rest[:60]


def info_extra(checker) -> dict:
    hits, _ = rule_hits(checker)
    by_switch = Counter()
    for rid, n in hits.items():
        by_switch[_rule_of_hit(rid)] += n
    return {"unknown_color": style_choice(checker, "unknown_color", "grey"),
            "settings_extra": {
                "legend": "Malayalam checks (docs/MALAYALAM_RULES.md)",
                "rules": [{"id": rid, "label": f"{label}{'' if inline else ' — listed, not underlined'}", "default": dflt,
                           "count": by_switch.get(rid, 0)} for rid, (g, label, inline, dflt) in RULES.items()],
                "style": style_toggles(checker),
                "numbers": [{"group": "lex", "key": "irv_accept_min", "label": "Accept a word used at least this often in the IRV",
                             "default": 5, "min": 1},
                            {"group": "consistency", "key": "minority_max", "label": "Consistency: flag a form below this share (0–1)",
                             "default": 0.25, "min": 0}],
                "reload": "Reload known_misspellings.tsv, names.tsv, suffixes.tsv, stems.tsv",
            }}


# ---------------------------------------------------------------------------------------------
# UI labels
# ---------------------------------------------------------------------------------------------
LABELS = {
    "status": {"malformed": "Malayalam spelling fault", "irv_ok": "not in the OV, but common in the IRV (accepted)",
               "inflected_ok": "known word + ending", "unknown": "not verifiable (not in the OV, no known stem + ending)",
               "rare_near_common": "rare word next to a much commoner near form", "archaic": "older (OV) spelling",
               "compound": "unknown, but splits into two known words"},
    "cls": {"encoding": "encoding", "samvruthokaram": "്/ു", "gemination": "doubling", "ra_rra": "ര/റ", "retroflex": "ന/ണ ല/ള ഴ",
            "vowel_length": "vowel length", "sibilant": "ശ/ഷ/സ", "aspiration": "aspiration", "nasal": "ം / nasal", "yk": "യ്ക്ക/ക്ക",
            "chillu_conjunct": "chillu / conjunct", "vocalic_r": "ൃ / ്ര", "glide": "യ/വ", "vocative": "vocative",
            "participle": "participle", "compound_head": "compound", "transpose": "swap", "other": "", "shape": "fix",
            "house": "house spelling", "archaic": "IRV form", "sandhi": "sandhi", "split": "split"},
    "warn": {rid: v[1] for rid, v in RULES.items()} | {
        "double_space": "double space", "no_space_after_punct": "no space after the punctuation mark",
        "space_before_punct": "space before punctuation", "double_punct": "double punctuation",
        "zero_width": "invisible character (zero-width space, BOM, no-break space)", "digits_in_verse": "digits in verse text"},
    "legend": [["unknown", "not verifiable"], ["rare_near_common", "rare near a common word"], ["malformed", "spelling fault"],
               ["malformed panel", "listed fault"], ["irv_ok", "IRV word"], ["inflected_ok", "word + ending"],
               ["compound", "compound?"], ["lead-panel", "consistency / grammar lead"], ["warn", "warning"], ["learned", "changed before"], ["ignored", "ignored"]],
    "counts_leads": "leads",
    "leads_heading": "Consistency and grammar leads",
    "ignored_pairs_heading": "Ignored leads",
    "book_word_statuses": "unknown,rare_near_common,compound,malformed,irv_ok,inflected_ok",
    "quiet_statuses": ["irv_ok", "inflected_ok"],
    "issue_sections": [["rare_near_common", "Rare forms of common words"], ["archaic", "Older (OV) spellings"]],
    "rebuild": "Rebuild dictionary_ml from the OV Malayalam + extra_words.txt",
    "rules_link": "Malayalam rules",
    "issue_types": ["Possible typo", "Confirmed typo", "Spelling consistency", "Encoding", "Font / rendering", "Grammar", "Punctuation",
                    "Name consistency", "Reviewer decision", "Other"],
    "settings_warn": {"double_space": "double space", "digits": "digits in verse text (dates and measures are normal)",
                      "defects": "(not used for Malayalam)", "space_before_note_close": "space before a footnote end",
                      "double_punct": "double punctuation (?. ;. ,,)", "no_space_after_punct": "no space after , ; : ! ?",
                      "space_before_punct": "space before punctuation", "repeated_word": "(see Style below)"},
    "word_re": f"[{ML}\u200C\u200D]+(?:-[{ML}\u200C\u200D]+)*",
    "zw_strip": "[\u200B\uFEFF\u2060]",                       # the editor keeps ZWJ / ZWNJ: they are letters here
}


def related_norm(lex):
    return canon


def build_args(config) -> list[str]:
    args = ["--lang", "ml", "--out-dir", str(config.get("dict_dir") or REPO_ROOT / "dictionary_ml")]
    if config.get("irv_dir"):
        args += ["--irv-dir", str(config["irv_dir"])]
    return args


FORBIDDEN_IN_TEXT = {"\\": "backslash", "\r": "CR", "\n": "LF", "\t": "TAB", "\u200B": "U+200B", "\uFEFF": "U+FEFF"}

PROFILE = Language(
    code="ml", name="Malayalam", script=T.SCRIPT, file_re=T.FILE_RE,
    dict_dir_default=REPO_ROOT / "dictionary_ml", data_dir_default=APP_DIR / "data_ml", port_default=8767,
    html_lang="ml", app_title="Malayalam IRV spell-check editor",
    token_re=T.TOKEN_RE, grapheme_start=GRAPHEME_START, default_settings=DEFAULT_SETTINGS,
    malformed=malformed, rank=rank, extra_suggestions=extra_suggestions,
    defect_patterns=[], punct_run_re=re.compile("[,.;:!?]{2,}"), ok_punct_runs={"?!", "!?"},
    no_space_after_re=re.compile(rf"[,;!?](?=[{ML}])|(?<=[{MLZ}]{{4}}):(?=[{ML}])"),
    space_before_re=re.compile(rf"(?<=[{MLZ}\d)”’])( +)(?=[,;:.!?])"),
    token_warning=token_warning, related_norm=related_norm, build_args=build_args,
    irv_dir_default=str(paths.irv_dir("ml")),
    more_candidates=more_candidates, classify_first=classify_first, classify_extra=classify_extra,
    line_leads=line_leads, chapter_leads=chapter_leads, extra_warnings=extra_warnings,
    on_index_book=on_index_book, on_build_index=on_build_index, load_lexicon_extra=load_lexicon_extra,
    info_extra=info_extra, rules_payload=rules_payload, report_block=report_block,
    rules_page="rules_ml.html", labels=LABELS,
    font_family="Noto Sans Malayalam", font_fallback=("Nirmala UI", "Kartika"),
    features={"sandhi": False, "rule_pack": True, "consistency": True, "encoding": True},
    zero_width_re=NEVER_RE, forbidden_in_text=FORBIDDEN_IN_TEXT,           # invisibles: ml.norm.invisible
)
