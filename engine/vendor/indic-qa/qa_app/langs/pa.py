"""Punjabi (Gurmukhi) profile for the IRV editor.  Rules: docs/PUNJABI_RULES.md (`pa.*` ids).

Tables live in pa_tables.py, the algorithms in qa_app/kinds.py; this module wires them into the
checker's hooks.  Nothing here is used when the editor runs Tamil.

Statuses a Punjabi token can get (besides the shared ok / ignored / compound / unknown):
  malformed     a shape fault (§3), a known misspelling or a divine-name variant; errors are
                underlined, warnings (`panel`: true) are only listed
  suspect       a rare known word next to a much commoner near form (panel)
  archaic       an OV form the IRV replaced (panel, off by default)
  inflected_ok  a known word plus a listed ending (grey)
  irv_ok        not in the OV but used often in the IRV itself (grey, listed under Book words)
"""
from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from pathlib import Path

from .. import paths
from .. import kinds
from . import APP_DIR, REPO_ROOT, Language
from . import pa_tables as T

GUR = "\u0A00-\u0A7F"

# ---------------------------------------------------------------------------------------------
# rule catalogue: id -> (group, plain-language label, inline?, on by default?)
# ---------------------------------------------------------------------------------------------
RULES: dict[str, tuple[str, str, bool, bool]] = {
    "pa.norm.encoding": ("Encoding", "Encoding: not NFC, precomposed nukta letter, invisible character, ASCII | or Latin l "
                                     "typed as the danda, Gurmukhi digit (one fix per text run)", True, True),
    "pa.norm.latin-in-text": ("Encoding", "Latin letter inside Gurmukhi text", True, True),
    "pa.norm.quotes": ("Encoding", "Straight quote \" or ' (the IRV uses curly quotes)", True, True),
    "pa.shape.errors": ("Shape", "Impossible sign sequence: vowel sign / nukta / tippi / bindi / adhak / virama in a "
                                 "place Gurmukhi does not allow (§3)", True, True),
    "pa.shape.warnings": ("Shape", "Unusual sign sequence (virama before a letter other than ਹ ਰ, adhak after a long "
                                   "vowel or before a rare consonant) in a word not in the dictionary", False, True),
    "pa.shape.single-letter": ("Shape", "A one-letter word other than ਆ ਈ ਏ ਓ", False, True),
    "pa.shape.long-token": ("Shape", "A word of 18+ characters (a missing space?) not in the dictionary", False, True),
    "pa.shape.mixed-script": ("Shape", "Devanagari letter in the text (Hindi keyboard look-alike)", True, True),
    "pa.shape.hyphen": ("Shape", "Loose hyphen: at a word edge, doubled, or with a space inside a compound", False, True),
    "pa.punct.space-before-danda": ("Punctuation", "Space before । or other punctuation", True, True),
    "pa.punct.double": ("Punctuation", "Doubled punctuation (,? ,! ।।)", True, True),
    "pa.norm.spacing": ("Punctuation", "Double space, or no space after , ; : ! ? ।", True, True),
    "pa.punct.punct-before-danda": ("Punctuation", "! ? , ; : directly before । (house style to decide)", False, True),
    "pa.punct.double-danda": ("Punctuation", "॥ outside “॥ ਸਲਹ ॥”", False, True),
    "pa.punct.quotes-unbalanced": ("Punctuation", "Opening and closing quotes do not balance in the chapter", False, True),
    "pa.punct.ascii-fullstop": ("Punctuation", "ASCII full stop after a Gurmukhi word (not an abbreviation)", False, True),
    "pa.lex.unknown": ("Lexicon", "Word not in the dictionary (OV Punjabi + extra words) and rare in the IRV", True, True),
    "pa.lex.rare-near-common": ("Lexicon", "Rare word (≤ 2× in OV + IRV) next to a much commoner near form", False, True),
    "pa.lex.known-misspelling": ("Lexicon", "Listed in known_misspellings.tsv", True, True),
    "pa.style.divine-names": ("Lexicon", "Divine name spelled another way than the IRV's (ਪਰਮੇਸ਼ੁਰ, ਯਹੋਵਾਹ, ਯਿਸੂ, ਪ੍ਰਭੂ …)", True, True),
    "pa.lex.archaic-form": ("Lexicon", "OV (older) spelling the IRV replaced, e.g. ਅਰ → ਅਤੇ", False, False),
    "pa.style.nukta-consistency": ("Consistency", "Minority spelling of a word that the IRV spells with/without nukta", False, True),
    "pa.style.haha-consistency": ("Consistency", "Minority spelling with/without ੍ਹ", False, True),
    "pa.style.rara-consistency": ("Consistency", "Minority spelling ੍ਰ vs ਰ (ਪ੍ਰਗਟ / ਪਰਗਟ)", False, True),
    "pa.style.adhak-consistency": ("Consistency", "Minority spelling with/without adhak ੱ", False, True),
    "pa.gram.genitive-gender": ("Grammar", "ਦਾ/ਦੀ, ਮੇਰਾ/ਮੇਰੀ … disagrees with the gender of the next noun", False, True),
    "pa.gram.oblique": ("Grammar", "Direct -ਾ form before a postposition where the corpus uses -ੇ (ਮੁੰਡਾ ਨੂੰ → ਮੁੰਡੇ ਨੂੰ)", False, True),
    "pa.style.reduplication-hyphen": ("Style", "“X X” where the IRV writes “X-X”", False, True),
    "pa.style.repeated-word": ("Style", "The same word twice", False, False),
}
SHAPE_RULE_SWITCH = {r.id: ("pa.shape.errors" if r.severity == "error" else "pa.shape.warnings") for r in T.SHAPE_RULES}
SHAPE_RULE_SWITCH.update({"pa.shape.single-letter": "pa.shape.single-letter", "pa.shape.long-token": "pa.shape.long-token"})
GENERIC_WARNING_RULE = {"double_space": "pa.norm.spacing", "no_space_after_punct": "pa.norm.spacing",
                        "space_before_punct": "pa.punct.space-before-danda", "double_punct": "pa.punct.double",
                        "zero_width": "pa.norm.encoding", "digits_in_verse": "pa.norm.encoding",
                        "space_before_note_close": "pa.norm.spacing", "repeated_word": "pa.style.repeated-word"}
CONSISTENCY_RULE = {"nukta": "pa.style.nukta-consistency", "haha": "pa.style.haha-consistency",
                    "rara": "pa.style.rara-consistency", "adhak": "pa.style.adhak-consistency"}
# ਜੀਉਂਦਾ / ਜਿਉਂਦਾ: a house-style choice only (no lead until the reviewer picks a form)
STYLE_ONLY = {"iu": lambda w: w.replace("ੀਉ", "ਿਉ")}

SUFFIX_STOP = ["ਆਂ", "ਾਂ", "ਦਾ", "ਦੇ", "ਦੀ", "ਨੂੰ", "ਨੇ", "ਤੋਂ", "ਵਾਲਾ", "ਵਾਲੇ", "ਵਾਲੀ", "ਹੀ", "ਵੀ", "ਗਾ", "ਗੇ", "ਗੀ",
               "ਕੇ", "ਣਾ", "ਣੇ", "ਣੀ", "ਨਾ", "ਨੀ", "ਿਆ", "ਈਆਂ", "ਇਆ"]
# endings stripped for inflected_ok (§4.1 step 4), longest first; a stem may also be the -ਾ / -ੀ citation form
ENDINGS = sorted(["ਹੀ", "ਵੀ", "ਨਾ", "ੀਆਂ", "ਿਆਂ", "ਆਂ", "ਾਂ", "ਓ", "ਿਓ"], key=len, reverse=True)

DEFAULT_SETTINGS: dict = {
    "reviewer": "",
    "checked_contexts": ["verse", "heading", "psalm_title", "footnote_text", "intro", "title"],
    "suggest": {"max": 5, "lemma_min": 3, "irv_min": 3},
    "compound": {"enabled": True, "min_first": 3, "min_second": 3, "min_freq": 3, "suffix_stop": SUFFIX_STOP},
    "warnings": {"double_space": True, "digits": False, "defects": False, "space_before_note_close": False,
                 "double_punct": True, "no_space_after_punct": True, "space_before_punct": True, "repeated_word": False},
    "lex": {"irv_accept_min": 5, "inflect_min_stem": 3, "rare_max": 2, "rare_ratio": 50, "rare_cost": 0.5},
    "consistency": {"minority_max": 0.25, "close": 0.8, "style_min": 20},
    "agreement": {"gender_min": 20, "gender_pct": 0.95, "oblique_min": 10, "oblique_direct_max": 0.10},
    "rules": {rid: v[3] for rid, v in RULES.items()},
    "style": {},
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
    d["misspell"] = {}
    for r in _tsv(dict_dir / "known_misspellings.tsv"):
        wrong, right = T.normalize_text(r.get("wrong", "").strip()), T.normalize_text(r.get("right", "").strip())
        if wrong and right and not wrong.startswith("#"):
            d["misspell"][wrong] = (right, (r.get("rule") or "pa.lex.known-misspelling").strip())
    d["gender"] = {r["word"]: (int(r["m"]), int(r["f"])) for r in _tsv(dict_dir / "gender.tsv")}
    d["oblique"] = {r["stem"]: (int(r["direct"]), int(r["oblique"])) for r in _tsv(dict_dir / "oblique.tsv")}
    d["archaic"] = {r["ov_word"]: (r["modern"], int(r["aligned"]), int(r["slots"])) for r in _tsv(dict_dir / "archaic.tsv")}
    d["misspell_path"] = str(dict_dir / "known_misspellings.tsv")


# ---------------------------------------------------------------------------------------------
# corpus state (per book, refreshed like the Tamil sandhi pair counts)
# ---------------------------------------------------------------------------------------------

def on_index_book(checker, code: str, book, checked: set) -> None:
    hyph, space = Counter(), Counter()
    for line in book.lines:
        for seg in line.segs:
            if seg.kind != "t" or seg.context not in checked:
                continue
            for w in T.TOKEN_RE.findall(seg.text):
                parts = w.split("-")
                if len(parts) == 2 and parts[0] == parts[1]:
                    hyph[parts[0]] += 1
            for w1, w2, _, _ in kinds.adjacent_pairs(seg.text, T.TOKEN_RE):
                if w1 == w2:
                    space[w1] += 1
    books = checker.data.setdefault("redup_book", {})
    books[code] = (hyph, space)
    checker.data["redup_hyph"] = sum((h for h, _ in books.values()), Counter())
    checker.data["redup_space"] = sum((s for _, s in books.values()), Counter())


def on_build_index(checker) -> None:
    # words the IRV itself uses often (modern forms the OV lacks, ਬਲ਼ਦ) are suggestion targets too
    accept = checker.settings["lex"].get("irv_accept_min", 5)
    for w, n in sorted(checker.irv_count.items()):
        if n >= accept and not T.shape_findings(w):
            checker.index.add(w)
    loose = defaultdict(list)
    for w in checker.index.words:
        loose[T.loose_skeleton(w)].append(w)
    checker.data["loose"] = loose
    irv = {w: n for w, n in checker.irv_count.items() if n > 0}
    checker.data["clusters"] = {kind: kinds.consistency_clusters(irv, fn)
                                for kind, fn in {**T.SKELETONS, **STYLE_ONLY}.items()}


# ---------------------------------------------------------------------------------------------
# classification
# ---------------------------------------------------------------------------------------------

def _info(status, **kw):
    from ..checker import WordInfo
    return WordInfo(status, **kw)


def _panel(extra: dict | None = None) -> dict:
    return {"panel": True, **(extra or {})}


def classify_first(checker, w: str, irv: int, ov: int):
    lex = checker.lex
    if w in lex.ignored:
        return None
    ms = lex.data.get("misspell", {}).get(w)
    if ms and on(checker, ms[1] if ms[1] in RULES else "pa.lex.known-misspelling"):
        return _info("malformed", rule=ms[1], severity="error", sugg=checker.suggest(w), irv=irv, ov=ov, ov_lemma=ov)
    found = [f for f in T.shape_findings(w) if not f.gated and on(checker, SHAPE_RULE_SWITCH.get(f.rule, f.rule))]
    if found:
        f = next((x for x in found if x.severity == "error"), found[0])
        return _info("malformed", rule=f.rule, severity=f.severity, sugg=checker.suggest(w), irv=irv, ov=ov, ov_lemma=ov,
                     extra={} if f.severity == "error" else _panel())
    if on(checker, "pa.lex.archaic-form"):
        a = lex.data.get("archaic", {}).get(w)
        if a:
            sugg = [{"w": a[0], "op": "sub", "from": w, "to": a[0], "at": 0, "cls": "archaic", "pos": "medial",
                     "freq": checker.freq(a[0])}]
            return _info("archaic", rule="pa.lex.archaic-form", severity="warning", sugg=sugg, irv=irv, ov=ov, ov_lemma=ov,
                         extra=_panel({"why": [f"the IRV has {a[0]} in {a[1]} of {a[2]} aligned verses"]}))
    if on(checker, "pa.lex.rare-near-common") and lex.known(w):
        s = checker.settings["lex"]
        if ov + irv <= s.get("rare_max", 2) and ov < 3:
            best = _near_common(checker, w, ov + irv, s)
            if best:
                return _info("suspect", rule="pa.lex.rare-near-common", severity="warning", sugg=[best], irv=irv, ov=ov,
                             ov_lemma=ov, extra=_panel())
    return None


def _near_common(checker, w, freq, s):
    best = None
    for c in checker.data.get("loose", {}).get(T.loose_skeleton(w), ()):
        if c == w or checker.freq(c) < s.get("rare_ratio", 50) * max(1, freq):
            continue
        h = rank(checker, w, c, limit=s.get("rare_cost", 0.5))
        if h and h[0] <= s.get("rare_cost", 0.5) and (best is None or h[:4] < best[:4]):
            best = h
    return best[4] if best else None


def classify_extra(checker, w: str, irv: int, ov: int):
    """After the lexicon and hyphen checks failed: gated shape warnings, known word + ending, IRV-frequent."""
    lex = checker.lex
    if on(checker, "pa.shape.warnings") or on(checker, "pa.shape.long-token"):
        gated = [f for f in T.shape_findings(w) if f.gated and on(checker, SHAPE_RULE_SWITCH.get(f.rule, f.rule))]
        if gated:
            f = gated[0]
            return _info("malformed", rule=f.rule, severity=f.severity, sugg=checker.suggest(w), irv=irv, ov=ov, ov_lemma=ov,
                         extra=_panel())
    s = checker.settings["lex"]
    min_stem = s.get("inflect_min_stem", 3)
    for end in ENDINGS:
        if w.endswith(end) and len(w) - len(end) >= min_stem:
            stem = w[:-len(end)]
            for base in (stem, stem + "ਾ", stem + "ੀ", stem + "ੇ"):
                if lex.known(base):
                    return _info("inflected_ok", bare=base, irv=irv, ov=ov, ov_lemma=lex.lemma_count.get(base, 0))
    if irv >= s.get("irv_accept_min", 5):
        return _info("irv_ok", irv=irv, ov=ov, ov_lemma=ov)
    return None


def malformed(w: str):
    return None                        # Punjabi shape rules need settings: see classify_first


# ---------------------------------------------------------------------------------------------
# suggestions
# ---------------------------------------------------------------------------------------------
POS_RANK = {"medial": 0, "initial": 1, "final": 2}


def _extra_targets(checker, w: str) -> set:
    return {h[3] for h in extra_suggestions(checker, w)}


def rank(checker, w: str, c: str, limit: float = 2.0):
    if c in _extra_targets(checker, w):
        return None                      # already offered as the shape fix / house spelling
    cost, ops = kinds.confusion_distance(w, c, T.CONFUSION, limit=limit)
    if cost == float("inf"):
        return None
    _, cls, op, frm, to, at = ops[0] if ops else (0, "other", "sub", "", "", 0)
    n = len(kinds.units(w, T.CONFUSION))
    pos = "initial" if at == 0 else "final" if at >= n - 2 else "medial"
    f = checker.freq(c)
    return (round(cost, 3), POS_RANK[pos], -f, c,
            {"w": c, "op": op, "from": frm, "to": to, "at": at, "cls": cls, "pos": pos, "freq": f, "cost": round(cost, 2)})


def more_candidates(checker, w: str) -> set:
    return set(checker.data.get("loose", {}).get(T.loose_skeleton(w), ()))


def extra_suggestions(checker, w: str) -> list:
    out = []
    ms = checker.lex.data.get("misspell", {}).get(w)
    if ms:
        out.append((-2, 0, -checker.freq(ms[0]), ms[0], {"w": ms[0], "op": "sub", "from": w, "to": ms[0], "at": 0,
                                                         "cls": "house", "pos": "medial", "freq": checker.freq(ms[0])}))
    for f in T.shape_findings(w):
        fixed = f.fixed(w)
        if fixed and fixed != w:
            out.append((-1, 0, -checker.freq(fixed), fixed, {"w": fixed, "op": "fix", "from": w[f.start:f.end], "to": f.fix,
                                                             "at": f.start, "cls": "shape", "pos": "medial",
                                                             "freq": checker.freq(fixed)}))
            break
    return out


# ---------------------------------------------------------------------------------------------
# warnings (text runs)
# ---------------------------------------------------------------------------------------------
PRECOMPOSED_RE = re.compile("[\u0A33\u0A36\u0A59-\u0A5B\u0A5E]")
ZW_RE = re.compile("[\u200B\u200C\u200D\uFEFF\u00A0]")
# ASCII | or a Latin l / I typed for the danda after a word (with or without a space): "ਸਨ l", "ਗਈl", "(1:2)l"
DANDA_TYPED_RE = re.compile(rf"(?<=[{GUR}”’\"'!?)]) *(?:\||[lI](?=\s|$|[”’\"')]))")
LATIN_RE = re.compile("[A-Za-z]+")
DEVANAGARI_RE = re.compile("[\u0900-\u0963\u0966-\u097F]")
STRAIGHT_QUOTE_RE = re.compile("[\"']")
PUNCT_BEFORE_DANDA_RE = re.compile("[!?,;:]।")
DOUBLE_DANDA_RE = re.compile("॥")
SELAH_RE = re.compile(r"॥\s*ਸਲਹ\s*॥")
ASCII_STOP_RE = re.compile(rf"(?<=[{GUR}]{{3}})\.(?!\d)")
HYPHEN_RE = re.compile(rf"(?<=[{GUR}])- +(?=[{GUR}])|(?<=[{GUR}]) +-(?=[{GUR}])|--+")


def normalize_run(text: str) -> tuple[str, list[str]]:
    """The L0 pipeline for one text run: (normalized text, what changed)."""
    import unicodedata
    what = []
    t = unicodedata.normalize("NFC", text)
    if t != text:
        what.append("precomposed nukta letter" if PRECOMPOSED_RE.search(text) else "not NFC")
    if ZW_RE.search(t):
        what.append("invisible character")
        t = ZW_RE.sub(lambda m: " " if m.group() == "\u00A0" else "", t)
    if DANDA_TYPED_RE.search(t):
        what.append("| or Latin l typed as the danda")
        t = DANDA_TYPED_RE.sub("।", t)
    d = t.translate(T.GUR_DIGITS)
    if d != t:
        what.append("Gurmukhi digit")
        t = d
    return t, what


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

    if on(checker, "pa.norm.encoding"):
        norm, what = normalize_run(text)
        if norm != text:
            s, e, e2 = _span(text, norm)
            if e == s:                                   # pure insertion: widen to one character
                e, e2 = s + 1, e2 + 1
            add("pa.norm.encoding", s, e, fix=norm[s:e2], fix_label="Fix the encoding (" + ", ".join(what) + ")",
                label="encoding: " + ", ".join(what))
    danda = [m.span() for m in DANDA_TYPED_RE.finditer(text)]
    if on(checker, "pa.norm.latin-in-text"):
        for m in LATIN_RE.finditer(text):
            if not any(a <= m.start() < b for a, b in danda):
                add("pa.norm.latin-in-text", m.start(), m.end(), label="Latin letters in Gurmukhi text")
    if on(checker, "pa.shape.mixed-script"):
        for m in DEVANAGARI_RE.finditer(text):
            add("pa.shape.mixed-script", m.start(), m.end(), label="Devanagari letter")
    if on(checker, "pa.norm.quotes"):
        for m in STRAIGHT_QUOTE_RE.finditer(text):
            add("pa.norm.quotes", m.start(), m.end(), label="straight quote (the IRV uses “ ” ‘ ’)")
    if on(checker, "pa.shape.hyphen"):
        for m in HYPHEN_RE.finditer(text):
            add("pa.shape.hyphen", m.start(), m.end(), panel=True, label="loose hyphen")
    if on(checker, "pa.punct.punct-before-danda"):
        for m in PUNCT_BEFORE_DANDA_RE.finditer(text):
            add("pa.punct.punct-before-danda", m.start(), m.end(), panel=True, fix=m.group()[0],
                fix_label=f"Keep only {m.group()[0]}", label=f"{m.group()} (punctuation before the danda)")
    if on(checker, "pa.punct.double-danda"):
        selah = [m.span() for m in SELAH_RE.finditer(text)]
        for m in DOUBLE_DANDA_RE.finditer(text):
            if not any(a <= m.start() < b for a, b in selah):
                add("pa.punct.double-danda", m.start(), m.end(), panel=True, label="॥ outside ॥ ਸਲਹ ॥")
    if on(checker, "pa.punct.ascii-fullstop"):
        for m in ASCII_STOP_RE.finditer(text):
            add("pa.punct.ascii-fullstop", m.start(), m.end(), panel=True, fix="।", fix_label="Change to ।",
                label="ASCII full stop after a word")
    hyph, space = checker.data.get("redup_hyph", Counter()), checker.data.get("redup_space", Counter())
    red_on, rep_on = on(checker, "pa.style.reduplication-hyphen"), on(checker, "pa.style.repeated-word")
    if red_on or rep_on:
        for w1, w2, a, b in kinds.adjacent_pairs(text, T.TOKEN_RE):
            if w1 != w2:
                continue
            if hyph[w1] >= 3 and hyph[w1] > space[w1] and red_on:      # the hyphen is the IRV's habit for this word
                add("pa.style.reduplication-hyphen", a, b, panel=True, fix=f"{w1}-{w1}", fix_label=f"Change to {w1}-{w1}",
                    label=f"the IRV writes {w1}-{w1} {hyph[w1]}× (and “{w1} {w1}” {space[w1]}×)")
            elif rep_on and not hyph[w1] and space[w1] < 3:
                add("pa.style.repeated-word", b - len(w2), b, panel=True, label="word repeated")
    return out


def token_warning(checker, text, w, a, b, s):
    return None


# ---------------------------------------------------------------------------------------------
# chapter-level: quotes that do not balance
# ---------------------------------------------------------------------------------------------
PARAGRAPH_STYLES = {"p", "m", "pi", "pi1", "pi2", "q", "q1", "q2", "q3", "q4", "nb", "pc", "li", "li1", "li2", "d", "s", "s1"}


def chapter_leads(checker, book, n, blocks) -> None:
    if not on(checker, "pa.punct.quotes-unbalanced"):
        return
    opens: list[tuple[dict, int]] = []
    reopen = False
    for block in blocks:
        if block.get("style") in PARAGRAPH_STYLES:
            reopen = bool(opens)
        for seg in block["segs"]:
            if seg.get("k") != "t" or not seg.get("checked") or seg.get("stream", 0) != 0 or "warnings" not in seg:
                continue
            for i, ch in enumerate(seg["t"]):
                if ch == "“":
                    if reopen and opens:
                        reopen = False             # “ re-opened at a new paragraph: the same quotation continues
                        continue
                    opens.append((seg, i))
                    reopen = False
                elif ch == "”":
                    reopen = False
                    if opens:
                        opens.pop()
                    else:
                        seg["warnings"].append({"kind": "pa.punct.quotes-unbalanced", "s": i, "e": i + 1, "panel": True,
                                                "rule": "pa.punct.quotes-unbalanced", "label": "” without an opening “ in this chapter"})
    for seg, i in opens:
        seg["warnings"].append({"kind": "pa.punct.quotes-unbalanced", "s": i, "e": i + 1, "panel": True,
                                "rule": "pa.punct.quotes-unbalanced", "label": "“ not closed in this chapter"})


# ---------------------------------------------------------------------------------------------
# leads: spelling consistency and agreement
# ---------------------------------------------------------------------------------------------
M_TO_F = {"ਦਾ": "ਦੀ", "ਦੇ": "ਦੀ", "ਦੀ": "ਦਾ", "ਦੀਆਂ": "ਦੇ"}


def _flip_marker(w: str) -> str:
    if w in M_TO_F:
        return M_TO_F[w]
    if w.endswith("ੀਆਂ"):
        return w[:-3] + "ੇ"
    if w.endswith("ਾ") or w.endswith("ੇ"):
        return w[:-1] + "ੀ"
    if w.endswith("ੀ"):
        return w[:-1] + "ਾ"
    return w


def style_choice(checker, key: str) -> str:
    return checker.settings.get("style", {}).get(key, "auto")


def consistency_lead(checker, w: str):
    """Memoized per word; the memo is dropped when the lexicon, settings or index change."""
    key = (checker.lex.generation, checker.settings_gen, id(checker.data.get("clusters")))
    memo = checker.data.get("cons_memo")
    if memo is None or memo[0] != key:
        memo = checker.data["cons_memo"] = (key, {})
    if w not in memo[1]:
        memo[1][w] = _consistency_lead(checker, w)
    return memo[1][w]


def _consistency_lead(checker, w: str):
    s = checker.settings["consistency"]
    lex = checker.lex
    clusters = checker.data.get("clusters", {})
    for kind, fn in {**T.SKELETONS, **STYLE_ONLY}.items():
        rid = CONSISTENCY_RULE.get(kind)
        if rid and not on(checker, rid):
            continue
        sk = fn(w)
        forms = clusters.get(kind, {}).get(sk)
        if not forms or w not in forms:
            continue
        key = f"{kind}:{sk}"
        choice = style_choice(checker, key)
        ranked = sorted(forms.items(), key=lambda kv: (-kv[1], kv[0]))
        total = sum(forms.values())
        if choice == "off":
            continue
        if choice == "auto":
            if kind in STYLE_ONLY:
                continue                                   # house style only: the reviewer decides
            top, n_top = ranked[0]
            close = len(ranked) > 1 and ranked[1][1] >= s.get("close", 0.8) * n_top
            if w == top or close or forms[w] / total >= s.get("minority_max", 0.25):
                continue
            if lex.lemma_count.get(w, 0) >= 3:            # the minority form is an OV word in its own right
                continue
            target = top
        else:
            if w == choice or choice not in forms:
                continue
            target = choice
        why = ["IRV forms: " + ", ".join(f"{f} {c}×" for f, c in ranked[:5])]
        if choice != "auto":
            why.append(f"house style chosen in Settings: {choice}")
        return {"kind": "consistency", "rule": rid or "pa.style.house", "proposed": target, "basis": kind, "strength": "panel",
                "panel": True, "w2": "≈" + kind, "pair": w, "why": why,
                "tag": f"{kind} {forms[w]}:{forms[target]}", "title": f"spelling consistency ({kind})",
                "issue_type": "Spelling consistency", "ignore_label": f"Stop flagging {w} for {kind} consistency"}
    return None


def agreement_leads(checker, w1: str, w2: str):
    a = checker.settings["agreement"]
    out = []
    g = T.GENDER_MARKERS.get(w1)
    if g and on(checker, "pa.gram.genitive-gender") and w2 not in T.GENDER_STOP:
        m, f = checker.lex.data.get("gender", {}).get(w2, (0, 0))
        tot = m + f
        if tot >= a.get("gender_min", 20):
            noun_g = "M" if m / tot >= a.get("gender_pct", 0.95) else "F" if f / tot >= a.get("gender_pct", 0.95) else None
            if noun_g and noun_g != g:
                out.append({"kind": "agreement", "rule": "pa.gram.genitive-gender", "proposed": _flip_marker(w1),
                            "basis": "gender", "strength": "panel", "panel": True, "w2": w2, "on": 1,
                            "why": [f"{w2} is {'masculine' if noun_g == 'M' else 'feminine'} in {max(m, f)} of {tot} "
                                    f"corpus contexts (M {m} · F {f})"],
                            "tag": f"gender {m}:{f}", "title": f"gender agreement with {w2}", "issue_type": "Grammar"})
    if w2 in T.POSTPOSITIONS and w1.endswith("ਾ") and on(checker, "pa.gram.oblique"):
        stem = w1[:-1]
        d, o = checker.lex.data.get("oblique", {}).get(stem, (0, 0))
        if o >= a.get("oblique_min", 10) and d / (d + o) <= a.get("oblique_direct_max", 0.10):
            out.append({"kind": "agreement", "rule": "pa.gram.oblique", "proposed": stem + "ੇ", "basis": "oblique",
                        "strength": "panel", "panel": True, "w2": w2, "on": 1,
                        "why": [f"before a postposition the corpus has {stem}ੇ {o}× and {w1} {d}×"],
                        "tag": f"oblique {d}:{o}", "title": f"oblique case before {w2}", "issue_type": "Grammar"})
    return out


def line_leads(checker, toks: list, line) -> list:
    leads = []
    for i, (w, r, s, e, adj, _) in enumerate(toks):
        c = consistency_lead(checker, w)
        if c:
            leads.append({**c, "w1": w, "run": r, "s": s, "e": e, "ignored": (w, c["w2"]) in checker.ignored_pairs})
        if i + 1 < len(toks) and toks[i + 1][4]:
            w2 = toks[i + 1][0]
            for g in agreement_leads(checker, w, w2):
                g.pop("on")
                leads.append({**g, "w1": w, "run": r, "s": s, "e": e, "ignored": (w, w2) in checker.ignored_pairs})
    return leads


# ---------------------------------------------------------------------------------------------
# reports, rules page, settings spec
# ---------------------------------------------------------------------------------------------
STATUS_RULE = {"unknown": "pa.lex.unknown"}


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
    return {"lang": "pa", "rules": rows, "statuses": statuses, "books_counted": books, "errors": [], "file": path,
            "misspellings": len(checker.lex.data.get("misspell", {})), "lexicon_gen": checker.lex.generation,
            "message": f"{len(checker.lex.data.get('misspell', {}))} known misspellings loaded from {path}"}


def style_toggles(checker) -> list[dict]:
    s = checker.settings["consistency"]
    out = []
    for kind, groups in checker.data.get("clusters", {}).items():
        for sk, forms in groups.items():
            ranked = sorted(forms.items(), key=lambda kv: (-kv[1], kv[0]))
            if len(ranked) < 2 or ranked[1][1] < s.get("style_min", 20):
                continue
            (a, na), (b, nb) = ranked[0], ranked[1]
            close = nb >= s.get("close", 0.8) * na
            auto = ("undecided, forms within 20 % — please choose" if close or kind in STYLE_ONLY
                    else f"majority: {a}")
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
    return {"settings_extra": {
        "legend": "Punjabi checks (docs/PUNJABI_RULES.md)",
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
    "status": {"malformed": "Gurmukhi spelling fault", "irv_ok": "not in the OV, but common in the IRV (accepted)",
               "inflected_ok": "known word + ending", "suspect": "rare form of a much commoner word",
               "archaic": "older (OV) spelling"},
    "cls": {"nukta": "਼ nukta", "adhak": "ੱ adhak", "haha": "੍ਹ", "rara": "੍ਰ / ਰ", "nasal": "ੰ / ਂ",
            "vowel_length": "vowel length", "inflection": "ending", "retroflex": "ਨ/ਣ ਡ/ੜ ਤ/ਟ", "aspiration": "aspiration",
            "transpose": "swap", "other": "", "shape": "fix", "house": "house spelling", "archaic": "IRV form"},
    "warn": {rid: v[1] for rid, v in RULES.items()} | {
        "double_space": "double space", "no_space_after_punct": "no space after the punctuation mark",
        "space_before_punct": "space before punctuation", "double_punct": "double punctuation",
        "zero_width": "invisible character", "digits_in_verse": "digits in verse text"},
    "legend": [["unknown", "unknown"], ["malformed", "spelling fault"], ["malformed panel", "listed fault"],
               ["irv_ok", "IRV word"], ["inflected_ok", "word + ending"], ["compound", "compound?"],
               ["lead-panel", "consistency / grammar lead"], ["warn", "warning"], ["learned", "changed before"], ["ignored", "ignored"]],
    "counts_leads": "leads",
    "leads_heading": "Consistency and grammar leads",
    "ignored_pairs_heading": "Ignored leads",
    "book_word_statuses": "unknown,compound,malformed,irv_ok,inflected_ok,suspect,archaic",
    "quiet_statuses": ["irv_ok", "inflected_ok"],
    "issue_sections": [["suspect", "Rare forms of common words"], ["archaic", "Older (OV) spellings"]],
    "rebuild": "Rebuild dictionary_pa from the OV Punjabi + extra_words.txt",
    "rules_link": "Punjabi rules",
    "issue_types": ["Possible typo", "Confirmed typo", "Spelling consistency", "Nukta / adhak / ੍ਹ / ੍ਰ", "Grammar",
                    "Punctuation", "Encoding", "Font / rendering", "Name consistency", "Reviewer decision", "Other"],
    "settings_warn": {"double_space": "double space", "digits": "digits in verse text", "defects": "(not used for Punjabi)",
                      "space_before_note_close": "space before a footnote end", "double_punct": "double punctuation (,? ,! ।।)",
                      "no_space_after_punct": "no space after , ; : ! ? ।", "space_before_punct": "space before । or punctuation",
                      "repeated_word": "(see Style below)"},
    "word_re": f"[{T.GUR}]+(?:-[{T.GUR}]+)*",
}


def related_norm(lex):
    return lambda w: w


def build_args(config) -> list[str]:
    return ["--lang", "pa", "--out-dir", str(config.get("dict_dir") or REPO_ROOT / "dictionary_pa")]


PROFILE = Language(
    code="pa", name="Punjabi", script=T.SCRIPT, file_re=T.FILE_RE,
    dict_dir_default=REPO_ROOT / "dictionary_pa", data_dir_default=APP_DIR / "data_pa", port_default=8766,
    html_lang="pa", app_title="Punjabi IRV spell-check editor",
    token_re=T.TOKEN_RE, grapheme_start=re.compile("[\u0A05-\u0A14\u0A15-\u0A39\u0A59-\u0A5E]"),
    default_settings=DEFAULT_SETTINGS,
    malformed=malformed, rank=rank, extra_suggestions=extra_suggestions,
    defect_patterns=[], punct_run_re=re.compile("[,.;:!?।]{2,}"),
    ok_punct_runs={"?!", "!?", "!।", "?।", ",।", ";।", ":।"},
    no_space_after_re=re.compile(rf"[,;:!?।](?=[{GUR}])"),
    space_before_re=re.compile(rf"(?<=[{GUR}\d)])( +)(?=[,;:.!?।])"),
    token_warning=token_warning, related_norm=related_norm, build_args=build_args,
    irv_dir_default=str(paths.irv_dir("pa")),
    more_candidates=more_candidates, classify_first=classify_first, classify_extra=classify_extra,
    line_leads=line_leads, chapter_leads=chapter_leads, extra_warnings=extra_warnings,
    on_index_book=on_index_book, on_build_index=on_build_index, load_lexicon_extra=load_lexicon_extra,
    info_extra=info_extra, rules_payload=rules_payload, report_block=report_block,
    rules_page="rules_pa.html", labels=LABELS,
    font_family="Noto Sans Gurmukhi", font_fallback=("Nirmala UI", "Raavi"), features={"sandhi": False, "rule_pack": True},
)
