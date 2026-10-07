"""Malayalam tables: character classes, the canonical lookup key, the L0 normalisation pipeline,
the L1 shape grammar, the confusion costs, the junction (sandhi) rules, ending classes and the
consistency skeletons.  Pure data plus small pure functions; shared by scripts/ml_build.py and
the app's Malayalam profile (qa_app/langs/ml.py).  Normative source: docs/MALAYALAM_RULES.md
(section numbers below refer to it).  Nothing here is imported when the editor runs Tamil or
Punjabi.
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

from ..kinds import ConfusionCosts, Finding, ShapeRule, sign_sequence

# --------------------------------------------------------------------------------------
# §1 character inventory
# --------------------------------------------------------------------------------------
ML = "ഀ-ൿ"                 # the Malayalam block
ZWJ, ZWNJ = "‍", "‌"
ZW = ZWNJ + ZWJ
TOKEN_RE = re.compile(rf"[{ML}{ZW}]+(?:-[{ML}{ZW}]+)*")
SCRIPT = ML
FILE_RE = re.compile(r"^(\d\d)([A-Z0-9]{3})IRVMal\.SFM$", re.IGNORECASE)

VIRAMA, ANUSVARA, VISARGA = "്", "ം", "ഃ"
DOT_REPH, AU_LEGACY, AU_SIGN = "ൎ", "ൌ", "ൗ"
CONS = "ക-നപ-ഹ"                 # ക–ഹ without ഩ U+0D29 (archaic: flagged)
CONS_ALL = "ക-ഹ"
IVOWEL = "അ-ഔ"
VSIGN = "ാ-ൃെ-ൈൊ-ൌൗ"   # ാ…ൃ െ േ ൈ ൊ ോ (ൌ legacy) ൗ
CHILLU = "ൺ-ൿ"
LETTER = f"{CONS_ALL}{IVOWEL}{CHILLU}"               # a Malayalam letter (consonant, vowel, chillu)

CHILLU_OF = {"ന": "ൻ", "ര": "ർ", "ല": "ൽ", "ള": "ൾ", "ണ": "ൺ", "ക": "ൿ"}
CONS_OF = {v: k for k, v in CHILLU_OF.items()}
CHILLU_BASES = "".join(CHILLU_OF)                    # നരലളണക

# three encodings of the nta cluster (§0.2); canonical key uses the Unicode-recommended ന്റ
NTA_FORMS = {"ന്‍റ": "ന്‍റ", "ൻ്റ": "ൻ്റ", "ന്റ": "ന്റ"}
NTA_NO_VIRAMA = "ൻറ"
NTA_RE = re.compile("ന്‍റ|ൻ്റ|ന്റ")
LEGACY_CHILLU_RE = re.compile(f"([{CHILLU_BASES}]){VIRAMA}{ZWJ}")
LEGACY_CHILLU_NOT_NTA_RE = re.compile(f"([{CHILLU_BASES}]){VIRAMA}{ZWJ}(?!റ)")

# dependent sign of each independent vowel (sandhi joins); അ has none (inherent)
SIGN_OF = {"അ": "", "ആ": "ാ", "ഇ": "ി", "ഈ": "ീ", "ഉ": "ു", "ഊ": "ൂ", "ഋ": "ൃ", "എ": "െ", "ഏ": "േ", "ഐ": "ൈ",
           "ഒ": "ൊ", "ഓ": "ോ", "ഔ": "ൗ"}
IVOWEL_OF = {v: k for k, v in SIGN_OF.items() if v}

ML_DIGITS = str.maketrans({chr(0x0D66 + i): str(i) for i in range(10)})

# Tamil / Devanagari look-alikes a Malayalam typist may hit on the wrong keyboard (§3.4 mixed-script)
TAMIL_LOOKALIKE = str.maketrans({
    "அ": "അ", "ஆ": "ആ", "இ": "ഇ", "ஈ": "ഈ", "உ": "ഉ", "ஊ": "ഊ", "எ": "എ", "ஏ": "ഏ", "ஐ": "ഐ", "ஒ": "ഒ", "ஓ": "ഓ", "ஔ": "ഔ",
    "க": "ക", "ங": "ങ", "ச": "ച", "ஞ": "ഞ", "ட": "ട", "ண": "ണ", "த": "ത", "ந": "ന", "ன": "ന", "ப": "പ", "ம": "മ", "ய": "യ",
    "ர": "ര", "ற": "റ", "ல": "ല", "ள": "ള", "ழ": "ഴ", "வ": "വ", "ஜ": "ജ", "ஷ": "ഷ", "ஸ": "സ", "ஹ": "ഹ",
    "ா": "ാ", "ி": "ി", "ீ": "ീ", "ு": "ു", "ூ": "ൂ", "ெ": "െ", "ே": "േ", "ை": "ൈ", "ொ": "ൊ", "ோ": "ോ", "ௌ": "ൗ", "்": "്", "ஃ": "ഃ",
})


# --------------------------------------------------------------------------------------
# §2 canon(): the lookup key (NFC → ൎ→ർ, ൌ→ൗ → legacy chillu → atomic → nta → ന്റ → drop ZW → ു→്)
# --------------------------------------------------------------------------------------

@lru_cache(maxsize=200000)
def canon_enc(w: str) -> str:
    """Encoding-canonical form: everything canon() does except the word-final ു → ് fold."""
    w = unicodedata.normalize("NFC", w)
    w = w.replace(DOT_REPH, "ർ").replace(AU_LEGACY, AU_SIGN)
    w = LEGACY_CHILLU_RE.sub(lambda m: CHILLU_OF[m.group(1)], w)          # ന്‍ → ൻ (so ന്‍റ → ൻറ)
    w = w.replace("ൻ്റ", "ന്റ").replace("ൻറ", "ന്റ")      # ൻ്റ, ൻറ → ന്റ
    w = w.replace(ZWJ, "").replace(ZWNJ, "")
    return w.replace("ു്", VIRAMA)                               # ു് → ് (samvruthokaram encoding)


FINAL_U_RE = re.compile("ു$")


@lru_cache(maxsize=200000)
def canon(w: str) -> str:
    return FINAL_U_RE.sub(VIRAMA, canon_enc(w))


def surface_nta(key: str, target: str) -> str:
    """A canonical key written with the project's nta encoding (ന്‍റ / ൻ്റ / ന്റ)."""
    if target in NTA_FORMS and target != "ന്റ":
        return key.replace("ന്റ", NTA_FORMS[target])
    return key


# --------------------------------------------------------------------------------------
# §2 L0 normalisation of a text run (the build applies it to OV lines; the app reports changes)
# --------------------------------------------------------------------------------------
INVISIBLE_RE = re.compile("[​﻿ ⁠]")
ZERO_WIDTH_RE = INVISIBLE_RE                     # the core "zero_width" warning for Malayalam: ZWJ/ZWNJ are legitimate
ZWNJ_MISPLACED_RE = re.compile(f"(?<!{VIRAMA}){ZWNJ}")
ZWNJ_FINAL_RE = re.compile(f"{VIRAMA}{ZWNJ}(?![{ML}])")
ZWNJ_AFTER_VIRAMA_RE = re.compile(f"{VIRAMA}{ZWNJ}(?=[{CONS_ALL}])")
ZWJ_STRAY_RE = re.compile(f"(?<![{CHILLU_BASES}]{VIRAMA}){ZWJ}")
RARE_CP_RE = re.compile("[ഀഁഄഩഺ-഼ൄൟൢൣ൰-൹]")
ML_DIGIT_RE = re.compile("[൦-൯]")
# a colon typed for the visarga: between two letters, no space, and the part before it is ≤ 3 code points
# (ദു:ഖം, മന:പൂർവ്വം, പുന:സ്ഥാപനം); a colon after a longer word (അരുളിച്ചെയ്യുന്നു:നിന്റെ) is a spacing fault
COLON_RE = re.compile(f"(?<=[{CONS_ALL}{VSIGN}]):(?=[{CONS_ALL}])")


def visarga_colons(text: str) -> list[int]:
    out = []
    for m in COLON_RE.finditer(text):
        i = m.start()
        j = i
        while j > 0 and not text[j - 1].isspace() and text[j - 1] not in "“‘\"'(":
            j -= 1
        if i - j <= 3:
            out.append(i)
    return out


def nta_counts(text: str) -> dict[str, int]:
    c = {k: 0 for k in NTA_FORMS}
    for m in NTA_RE.finditer(text):
        s = m.group()
        for k, v in NTA_FORMS.items():
            if s == v:
                c[k] += 1
    return c


COMPOSE_RE = re.compile(f"[{IVOWEL}][ാിീുൂൃെേൈൊോൌൗ]")


def compose_vowels(t: str) -> str:
    """An independent vowel followed by a sign, as left behind when an invisible character between them is
    removed (ഒ + ാ): the precomposed vowel (ഓ) where one exists."""
    return COMPOSE_RE.sub(lambda m: _fix_independent(m.group(), 0, 2, "") or m.group(), t)


def normalize_run(text: str, nta_target: str | None = None, nta_majority: str | None = None,
                  zwnj_final: bool = False, zwnj_after_virama: bool = False,
                  conjunct_pairs: tuple = ()) -> tuple[str, list[tuple[str, int, str]]]:
    """The L0 pipeline for one text run: (normalized text, [(rule id, count, note)]).
    `nta_target`: one of ന്‍റ / ൻ്റ / ന്റ rewrites every nta cluster; None flags a mix only (the
    no-virama ൻറ is always rewritten, to `nta_target` or `nta_majority` or ന്റ)."""
    what: list[tuple[str, int, str]] = []
    t = unicodedata.normalize("NFC", text)
    if t != text:
        what.append(("ml.norm.nfc", 1, "not NFC"))
    n = len(LEGACY_CHILLU_NOT_NTA_RE.findall(t))
    if n:
        def _chillu(m):
            nxt = t[m.end():m.end() + 1]
            if CHILLU_OF[m.group(1)] + nxt in conjunct_pairs:
                return m.group(1) + VIRAMA                 # the project writes this pair as a conjunct (Q4)
            return CHILLU_OF[m.group(1)]
        t = LEGACY_CHILLU_NOT_NTA_RE.sub(_chillu, t)
        what.append(("ml.norm.chillu-zwj", n, "legacy ZWJ chillu → atomic chillu"))
    counts = nta_counts(t)
    target = nta_target if nta_target in NTA_FORMS else None
    fallback = target or (nta_majority if nta_majority in NTA_FORMS else None) or "ന്റ"
    if NTA_NO_VIRAMA in t:
        n = t.count(NTA_NO_VIRAMA)
        t = t.replace(NTA_NO_VIRAMA, NTA_FORMS[fallback])
        what.append(("ml.norm.nta-encoding", n, f"ൻറ without virama → {fallback}"))
        counts = nta_counts(t)
    if target:
        n = sum(v for k, v in counts.items() if k != target)
        if n:
            t = NTA_RE.sub(NTA_FORMS[target], t)
            what.append(("ml.norm.nta-encoding", n, f"nta cluster → {target}"))
    elif nta_majority in NTA_FORMS:
        minority = sum(v for k, v in counts.items() if k != nta_majority)
        if minority and counts.get(nta_majority, 0) + minority:
            # a mix: reported, never rewritten until the target is chosen (Q1)
            what.append(("ml.norm.nta-encoding", minority,
                         "nta cluster written " + ", ".join(f"{k} {v}×" for k, v in counts.items() if v)
                         + f" (project majority {nta_majority}; no fix until the target is chosen)"))
    if AU_LEGACY in t:
        what.append(("ml.norm.au-sign", t.count(AU_LEGACY), "legacy AU sign ൌ → ൗ"))
        t = t.replace(AU_LEGACY, AU_SIGN)
    if DOT_REPH in t:
        what.append(("ml.norm.dot-reph", t.count(DOT_REPH), "dot reph ൎ (old lipi) → ർ"))
        t = t.replace(DOT_REPH, "ർ")
    n = len(ZWNJ_MISPLACED_RE.findall(t))
    if n:
        t = ZWNJ_MISPLACED_RE.sub("", t)
        what.append(("ml.norm.zwnj-misplaced", n, "ZWNJ not after a virama removed"))
    if zwnj_final:
        n = len(ZWNJ_FINAL_RE.findall(t))
        if n:
            t = ZWNJ_FINAL_RE.sub(VIRAMA, t)
            what.append(("ml.norm.zwnj-final", n, "word-final ZWNJ removed"))
    if zwnj_after_virama:
        n = len(ZWNJ_AFTER_VIRAMA_RE.findall(t))
        if n:
            t = ZWNJ_AFTER_VIRAMA_RE.sub(VIRAMA, t)
            what.append(("ml.norm.zwnj-after-virama", n, "ZWNJ after a virama removed"))
    n = len(ZWJ_STRAY_RE.findall(t))
    if n:
        t = compose_vowels(ZWJ_STRAY_RE.sub("", t))
        what.append(("ml.norm.zwj-stray", n, "stray ZWJ removed"))
    n = len(ML_DIGIT_RE.findall(t))
    if n:
        t = t.translate(ML_DIGITS)
        what.append(("ml.norm.ml-digits", n, "Malayalam digit → ASCII digit"))
    if "ഩ" in t:
        what.append(("ml.norm.rare-codepoints", t.count("ഩ"), "archaic ഩ → ന"))
        t = t.replace("ഩ", "ന")
    return t, what


def normalize_text(text: str) -> str:
    """The build's L0 for OV lines: every mechanical fix, invisibles stripped, Malayalam digits as ASCII."""
    t, _ = normalize_run(text, nta_target="ന്റ", zwnj_final=True)
    t = INVISIBLE_RE.sub(lambda m: " " if m.group() == " " else "", t)
    return t


# --------------------------------------------------------------------------------------
# §3 L1 shape grammar: one class letter per code point
#   C consonant  V independent vowel  v dependent vowel sign (incl. ൌ, ൎ)  _ virama  M anusvara
#   H visarga  L chillu  J ZWJ  N ZWNJ  D Malayalam digit  X unexpected code point  - hyphen
# --------------------------------------------------------------------------------------
CLASS: dict[str, str] = {}
for _o in range(0x0D15, 0x0D3A):
    CLASS[chr(_o)] = "C"
CLASS["ഩ"] = "X"
for _o in range(0x0D05, 0x0D15):
    CLASS[chr(_o)] = "V"
for _ch in "ാിീുൂൃെേൈൊോൌൗ" + DOT_REPH:
    CLASS[_ch] = "v"
CLASS.update({VIRAMA: "_", ANUSVARA: "M", VISARGA: "H", ZWJ: "J", ZWNJ: "N", "-": "-"})
for _o in range(0x0D7A, 0x0D80):
    CLASS[chr(_o)] = "L"
for _o in range(0x0D66, 0x0D70):
    CLASS[chr(_o)] = "D"


def cls(ch: str) -> str:
    return CLASS.get(ch, "X")


INDEPENDENT_FIX = {"അാ": "ആ", "ഇീ": "ഈ", "ഉൂ": "ഊ", "എേ": "ഏ", "എാ": "ഏ", "എൈ": "ഐ", "ഒാ": "ഓ", "ഒൗ": "ഔ", "ഒൌ": "ഔ",
                   "അ്": "അ"}


def _fix_independent(w, s, e, c):
    """അ+ാ → ആ (a long vowel typed as short vowel + sign), ഏ+േ → ഏ (a vowel's own sign repeated)."""
    pair = w[s:e]
    if pair in INDEPENDENT_FIX:
        return INDEPENDENT_FIX[pair]
    if len(pair) == 2 and SIGN_OF.get(pair[0]) == pair[1]:
        return pair[0]
    return None


def _fix_double_sign(w, s, e, c):
    return w[s] if w[s] == w[s + 1] else None


def _fix_chillu_sign(w, s, e, c):
    base = CONS_OF.get(w[s])
    return base + w[s + 1:e] if base else None


def _const(v):
    return lambda w, s, e, c: v


def _fix_digit(w, s, e, c):
    return w[s:e].translate(ML_DIGITS)


SHAPE_RULES: list[ShapeRule] = [
    ShapeRule("ml.shape.unexpected-codepoint", "error", "cls", re.compile("X")),
    ShapeRule("ml.shape.ml-digit", "error", "cls", re.compile("D+"), _fix_digit),
    ShapeRule("ml.shape.zw-run", "error", "cp", re.compile(f"[{ZW}]{{2,}}")),
    ShapeRule("ml.shape.visarga-position", "warning", "cls", re.compile("^H|_H")),
    ShapeRule("ml.shape.sign-at-start", "error", "cls", re.compile("^[v_MH]")),
    ShapeRule("ml.shape.sign-on-independent-vowel", "error", "cls", re.compile("V[v_]"), _fix_independent),
    ShapeRule("ml.shape.double-sign", "error", "cls", re.compile("v[v_]"), _fix_double_sign),
    ShapeRule("ml.shape.double-virama", "error", "cls", re.compile("__"), _const(VIRAMA)),
    ShapeRule("ml.shape.nasal-after-virama", "error", "cls", re.compile("_[MH]")),
    ShapeRule("ml.shape.sign-after-nasal", "error", "cls", re.compile("[MH][v_MH]")),
    ShapeRule("ml.shape.chillu-plus-sign", "error", "cls", re.compile("L[vMH]"), _fix_chillu_sign),
    ShapeRule("ml.shape.chillu-plus-virama", "error", "cp", re.compile(f"[ൺർൽൾൿ]{VIRAMA}|ൻ{VIRAMA}(?!റ)")),
    ShapeRule("ml.shape.zwj-position", "error", "cp", re.compile(f"(?<![{CHILLU_BASES}]{VIRAMA}){ZWJ}"), _const("")),
    ShapeRule("ml.shape.zwnj-position", "error", "cp", re.compile(f"(?<!{VIRAMA}){ZWNJ}"), _const("")),
    ShapeRule("ml.shape.independent-vowel-mid-word", "warning", "cls", re.compile("(?<=[CvL_MHJN])V")),
]
SINGLE_LETTER_ALLOW = {"ആ", "ഈ", "ഏ", "ഓ", "ഒ", "എ", "അ"}
LONG_TOKEN = 32


@lru_cache(maxsize=200000)
def _shape_findings(w: str) -> tuple:
    out = [f for f in sign_sequence(w, cls, SHAPE_RULES)
           if not (f.rule == "ml.shape.independent-vowel-mid-word" and f.start <= 2)]
    if len(w) == 1 and w not in SINGLE_LETTER_ALLOW and cls(w) not in ("X", "L"):
        out.append(Finding("ml.shape.single-letter", "warning", 0, 1, None, False))
    if len(w.replace(ZWJ, "").replace(ZWNJ, "")) >= LONG_TOKEN and "-" not in w:
        out.append(Finding("ml.shape.long-token", "warning", 0, len(w), None, True))
    return tuple(out)


def shape_findings(w: str) -> list[Finding]:
    """§3 sign-sequence findings plus the word-level ones (single letter, long token); memoized."""
    return list(_shape_findings(w))


def lexicon_fault(w: str, findings: list[Finding]) -> bool:
    """True when an OV form must stay out of wordlist.txt: any shape error, or an independent vowel
    inside the word (a missing space in the OV: സകലഉപകരണങ്ങളും)."""
    return any(f.severity == "error" or f.rule == "ml.shape.independent-vowel-mid-word" for f in findings)


# --------------------------------------------------------------------------------------
# §4.2 confusion costs M01–M19.  Units: ്ര ്യ ്വ (post-consonant signs) and C് before a consonant are
# single units, so a geminate (ള്ള ↔ ളള) and a chillu/conjunct pair (ൽപ ↔ ല്പ) are one substitution.
# --------------------------------------------------------------------------------------
UNITS_RE = re.compile(f"{VIRAMA}[രയവ]|[{CONS_ALL}]{VIRAMA}(?![രയവ])(?=[{CONS_ALL}])|.", re.S)


def _pairs(spec: str, cost: float, kind: str) -> dict:
    out = {}
    for pair in spec.split():
        a, b = pair.split("/")
        out[frozenset((a, b))] = (cost, kind)
    return out


_SUB: dict = {}
for _c in [chr(o) for o in range(0x0D15, 0x0D3A)]:
    _SUB[frozenset((_c, _c + VIRAMA))] = (0.2, "gemination")                                   # M04 ഉളള/ഉള്ള
for _full, _ch in CHILLU_OF.items():
    _SUB[frozenset((_full + VIRAMA, _ch))] = (0.2, "chillu_conjunct")                          # M14 ൽപ/ല്പ
for _table in (
    _pairs("ര/റ", 0.3, "ra_rra"),                                                              # M05
    _pairs("ന/ണ ല/ള ള/ഴ ല/ഴ ൻ/ൺ ൽ/ൾ", 0.4, "retroflex"),                                       # M06
    _pairs("ി/ീ ു/ൂ െ/േ ൊ/ോ ഇ/ഈ ഉ/ഊ എ/ഏ ഒ/ഓ", 0.4, "vowel_length"),                           # M07
    _pairs("ശ/ഷ ശ/സ ഷ/സ", 0.4, "sibilant"),                                                   # M10
    _pairs("ക/ഖ ഗ/ഘ ച/ഛ ജ/ഝ ട/ഠ ഡ/ഢ ത/ഥ ദ/ധ പ/ഫ ബ/ഭ", 0.5, "aspiration"),                   # M11
    _pairs(f"ം/ങ{VIRAMA} ം/ഞ{VIRAMA} ം/ണ{VIRAMA} ം/ന{VIRAMA} ം/മ{VIRAMA}", 0.3, "nasal"),      # M12 ംക/ങ്ക
    _pairs(f"ൃ/{VIRAMA}ര", 0.4, "vocalic_r"),                                                  # M15 ഹ്രിദയം/ഹൃദയം
    _pairs("ൗ/ഔ", 0.5, "other"),                                                                # M16
    _pairs("യ/വ", 0.7, "glide"),                                                                # M18
):
    for _k, _v in _table.items():
        if _k not in _SUB or _v[0] < _SUB[_k][0]:
            _SUB[_k] = _v
for _k, _v in list(_SUB.items()):
    _a, _b = sorted(_k)
    if len(_a) == 1 and len(_b) == 1 and CLASS.get(_a) == "C" and CLASS.get(_b) == "C":
        _SUB.setdefault(frozenset((_a + VIRAMA, _b + VIRAMA)), _v)             # the same pair as conjunct units
CONFUSION = ConfusionCosts(
    units=UNITS_RE,
    sub=_SUB,
    indel={**{chr(o) + VIRAMA: (0.3, "gemination") for o in range(0x0D15, 0x0D3A)},               # M04 യോർദാൻ/യോർദ്ദാൻ
           f"യ{VIRAMA}": (0.3, "yk"), f"{VIRAMA}വ": (0.3, "gemination"), f"{VIRAMA}യ": (0.3, "gemination"),   # M13; ർവ്വ/ർവ
           f"{VIRAMA}ര": (0.4, "vocalic_r"), VIRAMA: (0.4, "gemination"), ANUSVARA: (0.5, "nasal"),
           **{s: (0.5, "vowel_length") for s in "ാിീുൂൃെേൈൊോൗ"}},                                # a dropped sign: പിടച്ച്/പിടിച്ച്
    sub_final=_pairs("െ/േ", 1.0, "vocative"),                                                   # M08 excluded: മകനെ/മകനേ
    transpose=(0.8, "transpose"),                                                               # M19
    indel_final={VIRAMA: (1.0, "participle"), ANUSVARA: (1.0, "compound_head")},                # M09 excluded
)
EDIT_CLASSES = ("encoding", "samvruthokaram", "gemination", "ra_rra", "retroflex", "vowel_length", "sibilant",
                "aspiration", "nasal", "yk", "chillu_conjunct", "vocalic_r", "glide", "vocative", "participle",
                "compound_head", "transpose", "other")

# Loose skeleton: every form within cost ≈ 0.5 of another shares it (candidate pairs for suggestions
# and for the rare-near-common test).
_LOOSE = str.maketrans({"ീ": "ി", "ൂ": "ു", "േ": "െ", "ോ": "ൊ", "ഈ": "ഇ", "ഊ": "ഉ", "ഏ": "എ", "ഓ": "ഒ",
                        "ണ": "ന", "ള": "ല", "ഴ": "ല", "റ": "ര", "ൺ": "ൻ", "ൾ": "ൽ", "ഷ": "ശ", "സ": "ശ",
                        "ഖ": "ക", "ഘ": "ഗ", "ഛ": "ച", "ഝ": "ജ", "ഠ": "ട", "ഢ": "ഡ", "ഥ": "ത", "ധ": "ദ", "ഫ": "പ", "ഭ": "ബ"})
GEMINATE_RE = re.compile(f"([{CONS_ALL}]){VIRAMA}\\1")
REPEATED_CONS_RE = re.compile(f"([{CONS_ALL}])\\1")
CHILLU_BEFORE_CONS_RE = re.compile(f"([ൺൻർൽൾ])(?=[{CONS_ALL}])")
HOMORGANIC_RE = re.compile(f"[ങഞണനമ]{VIRAMA}(?=[{CONS_ALL}])")


def loose_skeleton(w: str) -> str:
    w = canon(w).replace(f"യ{VIRAMA}ക{VIRAMA}ക", f"ക{VIRAMA}ക")
    w = CHILLU_BEFORE_CONS_RE.sub(lambda m: CONS_OF[m.group(1)] + VIRAMA, w)
    w = GEMINATE_RE.sub(r"\1", w)
    w = REPEATED_CONS_RE.sub(r"\1", w)                                      # ഉളള (virama dropped) / ഉള്ള
    w = HOMORGANIC_RE.sub(ANUSVARA, w)
    return w.translate(_LOOSE)


# --------------------------------------------------------------------------------------
# §4.1 archaic ↔ modern equivalences (applied at lookup, never as rewrites of the OV)
#   (modern ending, archaic ending): an IRV word is looked up as itself and as each archaic variant
# --------------------------------------------------------------------------------------
ARCHAIC_MAP: list[tuple[str, str, str, str]] = [
    # modern, archaic, where ("end" | "any"), example
    ("ിലേയ്ക്ക്", "ിലേക്ക്", "end", "വീട്ടിലേയ്ക്ക് / വീട്ടിലേക്ക്"),
    ("യ്ക്ക്", "െക്ക്", "end", "യഹോവയ്ക്ക് / യഹോവെക്ക്"),
    ("ിന്", "ിന്ന്", "end", "ദൈവത്തിന് / ദൈവത്തിന്ന്"),
    ("ന്", "ന്ന്", "end", "അവന് / അവന്ന്"),
    ("ര്യ", "ർയ്യ", "any", "കാര്യം / കാൎയ്യം"),
]


def archaic_variants(key: str) -> list[str]:
    """OV spellings an IRV key may correspond to (combinations included), the key itself excluded."""
    forms = {key}
    for modern, archaic, where, _ in ARCHAIC_MAP:
        new = set()
        for f in forms:
            if where == "end":
                if f.endswith(modern) and not (modern == "ന്" and f.endswith("ന്ന്")) \
                        and not (modern == "ന്" and f.endswith("ിന്")):
                    new.add(f[:-len(modern)] + archaic)
            elif modern in f:
                new.add(f.replace(modern, archaic))
        forms |= new
    forms.discard(key)
    return sorted(forms)


REAL_NN_WORDS = {"നിന്ന്", "തിന്ന്", "എന്ന്", "ഒന്ന്", "മൂന്ന്", "ഇന്ന്", "മുന്ന്", "ഉന്ന്", "പിന്ന്", "ചിന്ന്", "കുന്ന്"}


def modern_of(ov_key: str) -> str:
    """The modern spelling of an OV archaism (for suggestions: an OV archaism is never proposed)."""
    w = ov_key
    for modern, archaic, where, _ in ARCHAIC_MAP:
        if where == "end" and w.endswith(archaic):
            if archaic in ("ന്ന്", "ിന്ന്") and (w in REAL_NN_WORDS or len(w) <= 5):
                continue
            if archaic == "ന്ന്" and w.endswith("ിന്ന്"):
                continue
            w = w[:-len(archaic)] + modern
        elif where == "any" and archaic in w:
            w = w.replace(archaic, modern)
    return w


# --------------------------------------------------------------------------------------
# §4.1 junction (sandhi) rules — sandhi.tsv — and the suffix list for inflected_ok
# --------------------------------------------------------------------------------------
SANDHI_RULES: list[tuple[str, str, str, str]] = [
    # id, stem-final, join, example
    ("y-glide", "ി ീ െ േ ൈ ാ consonant(അ)", "insert യ + sign", "ഭൂമി + ിൽ → ഭൂമിയിൽ"),
    ("v-glide", "ു ൂ ൊ ോ", "insert വ + sign", "ഗുരു + ിനെ → ഗുരുവിനെ"),
    ("u-drop-dative", "ു", "drop ു before ിന്", "അവനു + ിന് → അവന്"),
    ("m-tt", "ം before a case ending", "ം → ത്ത", "ദൈവം + ിന്റെ → ദൈവത്തിന്റെ"),
    ("m-m", "ം elsewhere", "ം → മ", "ദൈവം + ആയി → ദൈവമായി"),
    ("m-um", "ം + ഉം", "→ വും", "ദൈവം + ഉം → ദൈവവും"),
    ("samvruthokaram", "് (word-final)", "drop ് and join", "അത് + ിനെ → അതിനെ"),
    ("chillu", "ൻ ൽ ർ ൾ ൺ", "full consonant + sign", "അവൻ + ോട് → അവനോട്"),
    ("numeral-i", "ത്തി / റ്റി (numerals)", "drop ി, attach the vowel", "മുന്നൂറ്റി + അമ്പത് → മുന്നൂറ്റമ്പത്"),
    ("locative-gemination", "stems in stems.tsv marked doubles", "C് → C്C before ിൽ ിലേക്ക് ിലെ ിലും ിലുള്ള", "വീട് + ിൽ → വീട്ടിൽ"),
]
# §4.1 / Bridge L2_Oblique_Stems: stems whose final stop doubles in the locative (കയറ് is still REVIEWER: out)
STEMS_DOUBLE = ["വീട്", "കാട്", "നാട്", "കൂട്", "വയറ്"]
STEMS_NO_DOUBLE = ["ചൂട്", "ഓട്", "കാറ്", "കർത്താവ്", "ആത്മാവ്", "കയറ്", "ആറ്"]
LOCATIVE_SUFFIXES = ("ിൽ", "ിലേക്ക്", "ിലെ", "ിലും", "ിലുള്ള")
VOWEL_SUFFIX_START = "ിീുൂെേൈൊോാൗ"             # suffixes that begin with a vowel sign

# reviewed Bridge list + the endings the IRV measurement found productive (rules §4.1)
SUFFIX_SEED = """ും ോ ിൽ ിന്റെ ിന് ിനെ ിനോട് ിനാൽ ുടെ ക്ക് ങ്ങൾ കൾ മാർ
ിലും ിലെ ിലേക്ക് ിനും ിനുള്ള ിലുള്ള ിനായി ോട് ോടും ാൽ ായി ായ ാണ് ത്തിൽ ത്തിന്റെ ത്തിന് ത്തെ ത്തോട് ത്താൽ
ങ്ങളെ ങ്ങളുടെ ങ്ങളിൽ ങ്ങളും കളെ കളുടെ കളിൽ കളും മാരെ മാരുടെ വും യും
യെ നെ ളെ ന്റെ യുടെ യിൽ വിൽ യോട് വോട് യാൽ യിലും യ്ക്ക് വിന് വിന്റെ വിനെ വിനോട് ത്തിലും ത്തിലെ ത്തിലേക്ക് ത്തോടെ ാകുന്നു""".split()


CASE_SUFFIXES = {"ിന്റെ", "ിന്", "ിനെ", "ിനോട്", "ിനാൽ", "ിനായി", "ിനും", "ിനുള്ള", "ിൽ", "ിലെ", "ിലും", "ിലേക്ക്",
                 "ിലുള്ള", "ോട്", "ോടും", "ാൽ", "െ", "ോടെ"}


def join(stem: str, suffix: str) -> str:
    """Attach `suffix` (a case ending or the clitic ഉം / ഓ) to `stem` with the junction rules (sandhi.tsv)."""
    if not suffix:
        return stem
    vowel = suffix[0] in SIGN_OF or suffix[0] in VOWEL_SUFFIX_START
    if suffix[0] in SIGN_OF:                                              # an independent vowel: its sign form
        suffix = SIGN_OF[suffix[0]] + suffix[1:]
    if not suffix:
        return stem
    if stem.endswith(ANUSVARA):
        if suffix == "ും":
            return stem[:-1] + "വും"
        return stem[:-1] + ("ത്ത" if suffix in CASE_SUFFIXES else "മ") + suffix
    if stem.endswith(VIRAMA):                                             # samvruthokaram / bare consonant
        if not vowel:
            return stem + suffix
        if stem.endswith(tuple(STEMS_DOUBLE)) and suffix in LOCATIVE_SUFFIXES:
            return stem + stem[-2] + suffix                               # വീട് + ിൽ → വീട്ടിൽ
        return stem[:-1] + suffix
    if stem[-1] in CONS_OF:                                               # chillu
        return (stem[:-1] + CONS_OF[stem[-1]] + suffix) if vowel else stem + suffix
    if not vowel:
        return stem + suffix
    if stem[-1] in "ുൂൊോ":
        return stem + "വ" + suffix
    if stem.endswith("ത്തി") or stem.endswith("റ്റി"):                   # numerals: മുന്നൂറ്റി + അമ്പത് → മുന്നൂറ്റമ്പത്
        return stem[:-1] + suffix
    return stem + "യ" + suffix                                            # ി ീ െ േ ൈ ാ or a bare consonant


def sandhi_join(a: str, b: str) -> str | None:
    """The sandhi form of a token written as two parts with an independent vowel at the junction
    (മറ്റേഅറ്റം → മറ്റേയറ്റം, മുന്നൂറ്റിഅമ്പത് → മുന്നൂറ്റമ്പത്); None when b does not start with a vowel."""
    if not a or not b or b[0] not in SIGN_OF:
        return None
    sign = SIGN_OF[b[0]]
    rest = b[1:]
    if a.endswith(ANUSVARA):
        if b == "ഉം":
            return a[:-1] + "വും"
        return a[:-1] + "മ" + sign + rest
    if a.endswith(VIRAMA):
        return a[:-1] + sign + rest
    if a[-1] in CONS_OF:
        return a[:-1] + CONS_OF[a[-1]] + sign + rest
    if (a.endswith("ത്തി") or a.endswith("റ്റി")):
        return a[:-1] + sign + rest
    if a[-1] in "ുൂൊോ":
        return a + "വ" + sign + rest
    return a + "യ" + sign + rest


def stem_candidates(stem: str, suffix: str) -> list[str]:
    """Base forms a stem may have had before `suffix` was attached (undoing the junction rules)."""
    out = []
    if stem and stem[-1] in CHILLU_OF:                                   # വാള + ും ← വാൾ (before വാള)
        out.append(stem[:-1] + CHILLU_OF[stem[-1]])
    if stem.endswith("ത്ത"):                                              # ദൈവത്ത + ിന്റെ ← ദൈവം (before ദൈവത്ത)
        out.append(stem[:-3] + ANUSVARA)
    if stem and cls(stem[-1]) == "C":
        out.append(stem + ANUSVARA)                                       # പാപ + ത്തിന്റെ ← പാപം
    out += [stem, stem + ANUSVARA]
    if stem and cls(stem[-1]) == "C":
        out.append(stem + VIRAMA)
    if stem and stem[-1] in CHILLU_OF:                                   # അവന + ോട് ← അവൻ
        out.append(stem[:-1] + CHILLU_OF[stem[-1]])
    if stem.endswith("ത്ത"):                                              # ദൈവത്ത + ിന്റെ ← ദൈവം
        out.append(stem[:-3] + ANUSVARA)
    if stem.endswith("മ") and suffix[:1] in VOWEL_SUFFIX_START:              # ദൈവമ + ായി ← ദൈവം
        out.append(stem[:-1] + ANUSVARA)
    if stem.endswith("യ") or stem.endswith("വ"):                           # glide
        base = stem[:-1]
        out += [base, base + ANUSVARA]
        if base and cls(base[-1]) == "C":
            out.append(base + VIRAMA)
        if stem.endswith("വ"):
            out.append(base + "ു")
    if len(stem) >= 3 and stem[-2] == VIRAMA and stem[-1] == stem[-3]:  # വീട്ട + ിൽ ← വീട്
        out.append(stem[:-1])
    if suffix.startswith("ിന") and stem and cls(stem[-1]) == "C":        # അവന് ← അവനു (dative drops ു)
        out.append(stem + "ു")
    seen, uniq = set(), []
    for c in out:
        c = canon(c)
        if len(c) >= 2 and c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


# --------------------------------------------------------------------------------------
# §6 ending classes for the samvruthokaram house-style toggle (never on verb forms)
# --------------------------------------------------------------------------------------
VERB_END_RE = re.compile(f"(ച{VIRAMA}ച|ഞ{VIRAMA}ഞ|ന{VIRAMA}ന|ട{VIRAMA}ട|ത{VIRAMA}ത|ണ{VIRAMA}ട|റ{VIRAMA}റ|യ){VIRAMA}$")
PRONOUNS = {"അത്", "ഇത്", "എന്ത്", "ഏത്", "ഇതൊക്കെ", "അതൊക്കെ"}
NUMERALS = ("ഒന്ന്", "രണ്ട്", "മൂന്ന്", "നാല്", "അഞ്ച്", "ആറ്", "ഏഴ്", "എട്ട്", "ഒമ്പത്", "ഒൻപത്", "പത്ത്", "നൂറ്", "ഒൻപത്")
ENDING_CLASSES = {"pronoun": "pronouns അത് / ഇത് / എന്ത്", "dative": "dative -ക്ക് / -ന് (എനിക്ക്, അവന്)",
                  "sociative": "sociative -ോട് (അവനോട്)", "quotative": "quotative എന്ന്",
                  "numeral": "numerals (രണ്ട്, പത്ത്)", "noun_at_av": "nouns in -ാട് / -ാവ് (അരുളപ്പാട്, കർത്താവ്)"}


def ending_class(key: str) -> str | None:
    """The samvruthokaram class of a canonical key, or None when the ്/ു alternation is grammar
    (verb forms) or the ending is not one the rule covers."""
    if not key.endswith(VIRAMA):
        return None
    if key == "എന്ന്":
        return "quotative"
    if key in PRONOUNS:
        return "pronoun"
    if key.endswith(NUMERALS):
        return "numeral"
    if VERB_END_RE.search(key):
        return None
    if key.endswith(f"ക{VIRAMA}ക{VIRAMA}") or (key.endswith(f"ന{VIRAMA}") and not key.endswith(f"ന{VIRAMA}ന{VIRAMA}")):
        return "dative"
    if key.endswith(f"ോട{VIRAMA}"):
        return "sociative"
    if key.endswith(f"ാട{VIRAMA}") or key.endswith(f"ാവ{VIRAMA}"):
        return "noun_at_av"
    return None


# --------------------------------------------------------------------------------------
# §6 consistency skeletons.  All but the first work on canon() (ു folded); the samvruthokaram one
# works on canon_enc() so that the ending is still visible.
# --------------------------------------------------------------------------------------
_VLEN = str.maketrans({"ീ": "ി", "ൂ": "ു", "േ": "െ", "ോ": "ൊ", "ഈ": "ഇ", "ഊ": "ഉ", "ഏ": "എ", "ഓ": "ഒ"})
_ASP = str.maketrans({"ഖ": "ക", "ഘ": "ഗ", "ഛ": "ച", "ഝ": "ജ", "ഠ": "ട", "ഢ": "ഡ", "ഥ": "ത", "ധ": "ദ", "ഫ": "പ", "ഭ": "ബ"})


def sk_samvruthokaram(w_enc: str) -> str:
    """Erase a word-final ്/ു; forms outside the nominal ending classes get a unique skeleton."""
    if w_enc.endswith(("ു", VIRAMA)):
        key = canon(w_enc)
        if ending_class(key):
            return key[:-1] + "#"
    return "\0" + w_enc


def sk_samvruthokaram_all(w_enc: str) -> str:
    return re.sub("[ു്]$", "#", w_enc)


def sk_yk(w: str) -> str:
    return canon(w).replace(f"യ{VIRAMA}ക{VIRAMA}ക", f"ക{VIRAMA}ക")


def sk_chillu_conjunct(w: str) -> str:
    return CHILLU_BEFORE_CONS_RE.sub(lambda m: CONS_OF[m.group(1)] + VIRAMA, canon(w))


GEMINATE_NOT_KR_RE = re.compile(f"([{CONS_ALL.replace('ക', '').replace('റ', '')}]){VIRAMA}\\1".replace("ക-ഹ", "ഖ-ഹ"))


def sk_gemination(w: str) -> str:
    """C്C erased, except ക്ക and റ്റ: those separate a plain verb from its causative (ആകുന്നു / ആക്കുന്നു)."""
    return re.sub(f"([^കറട{VIRAMA}]){VIRAMA}\\1", r"\1", canon(w))


def sk_vowel_length(w: str) -> str:
    w = canon(w)
    if w and w[-1] in "െേ":                                                # M08: the vocative is grammar
        return w[:-1].translate(_VLEN) + w[-1]
    return w.translate(_VLEN)


def sk_nasal_form(w: str) -> str:
    return HOMORGANIC_RE.sub(ANUSVARA, canon(w))


def sk_name(w: str) -> str:
    """M05 ര/റ, M10 sibilants, M11 aspiration, M07 length, M04 gemination erased (proper names)."""
    w = GEMINATE_RE.sub(r"\1", canon(w)).replace("റ", "ര").replace("ഷ", "ശ").replace("സ", "ശ")
    w = w.translate(_ASP)
    if w and w[-1] in "െേ":                                                # M08: the vocative of a name is grammar
        return w[:-1].translate(_VLEN) + w[-1]
    return w.translate(_VLEN)


_FOLD2 = str.maketrans({"റ": "ര", "ഷ": "ശ", "സ": "ശ", **{k: v for k, v in zip("ഖഘഛഝഠഢഥധഫഭ", "കഗചജടഡതദപബ")},
                        "ീ": "ി", "ൂ": "ു", "േ": "െ", "ോ": "ൊ", "ഈ": "ഇ", "ഊ": "ഉ", "ഏ": "എ", "ഓ": "ഒ"})


def name_head(w: str) -> str:
    """The first two code points with the name-skeleton folds: two spellings of one name always share it
    (no doubling can fall inside the first two code points), so it prefilters the proper-name pass."""
    return canon(w)[:2].translate(_FOLD2)


SKELETONS = {
    "samvruthokaram": sk_samvruthokaram,
    "yk": sk_yk,
    "chillu_conjunct": sk_chillu_conjunct,
    "gemination": sk_gemination,
    "vowel_length": sk_vowel_length,
    "nasal_form": sk_nasal_form,
}
# A word outside these can never share a skeleton with another word of the same kind (its skeleton is
# itself and contains nothing the skeleton maps to), so the cluster pass may skip it.
_CHILLU_OR_CONJ = ("ൺ", "ൻ", "ർ", "ൽ", "ൾ") + tuple(c + VIRAMA for c in "ണനരലള")
_NASAL = (ANUSVARA,) + tuple(c + VIRAMA for c in "ങഞണനമ")
SKELETON_CANDIDATE = {
    "samvruthokaram": lambda w: w.endswith(("ു", VIRAMA)),
    "yk": lambda w: f"ക{VIRAMA}ക" in w,
    "chillu_conjunct": lambda w: any(x in w for x in _CHILLU_OR_CONJ),
    "nasal_form": lambda w: any(x in w for x in _NASAL),
}
CHILLU_PAIRS = {"ൽപ": "ല്പ", "ൽക": "ല്ക", "ൻമ": "ന്മ", "ൺമ": "ണ്മ"}


def chillu_pair(w: str) -> str | None:
    """Which chillu/conjunct pair a form belongs to (for the per-pair toggle), e.g. ഉൽപത്തി → ൽപ."""
    w = canon(w)
    for m in CHILLU_BEFORE_CONS_RE.finditer(w):
        ch, nxt = m.group(1), w[m.end()]
        pair = ch + nxt
        if pair in CHILLU_PAIRS:
            return pair
    for conj, ch in ((v, k) for k, v in CHILLU_PAIRS.items()):
        if conj in w:
            return ch
    return None


# --------------------------------------------------------------------------------------
# §6 divine names and Bridge rejects: the seed of known_misspellings.tsv (wrong → right; a trailing
# "-" marks a prefix that also covers the case forms)
# --------------------------------------------------------------------------------------
# wrong → right.  "X-" covers the word X and its glide inflections (Xയുടെ, Xയെ, Xയ്ക്ക് …) but not other
# words that begin with X (യെഹോവ- does not touch the kings യെഹോവാശ് / യെഹോവാഹാസ്, മോശ- does not touch
# മോശമായ "bad"); "X*" covers every word that begins with X (the augment / chillu stem of a name).
KNOWN_MISSPELLINGS_SEED = [
    ("യാഹോവ-", "യഹോവ-", "ml.style.divine-names", "IRV spelling of the divine name (യഹോവ 7,097×)"),
    ("യെഹോവ-", "യഹോവ-", "ml.style.divine-names", "rejected by the reviewer (Bridge, 29 Sep 2026); not the names യെഹോവാശ്, യെഹോവാഹാസ്"),
    ("ഈശോ-", "യേശു-", "ml.style.divine-names", "rejected by the reviewer; the IRV writes യേശു"),
    ("മെസ്സീയ-", "ക്രിസ്തു-", "ml.style.divine-names", "rejected by the reviewer; IRV majority ക്രിസ്തു (Q7: approved rendering pending)"),
    ("മോശ-", "മോശെ-", "ml.style.rejected-names", "rejected by the reviewer; the IRV form is മോശെ (not മോശം / മോശമായ, bad)"),
    ("അബ്രഹാം-", "അബ്രാഹാം-", "ml.style.rejected-names", "rejected by the reviewer"),
    ("അബ്രഹാമ*", "അബ്രാഹാമ*", "ml.style.rejected-names", "rejected by the reviewer (case forms: അബ്രഹാമിന്റെ)"),
    ("ഇസ്രായേൽ-", "യിസ്രായേൽ-", "ml.style.rejected-names", "rejected by the reviewer"),
    ("ഇസ്രായേല*", "യിസ്രായേല*", "ml.style.rejected-names", "rejected by the reviewer (case forms: ഇസ്രായേലിന്റെ)"),
    ("ജെറുസലേം-", "യെരൂശലേം-", "ml.style.rejected-names", "rejected by the reviewer"),
    ("ജെറുസലേമ*", "യെരൂശലേമ*", "ml.style.rejected-names", "rejected by the reviewer (case forms)"),
    ("എലാം", "ഏലാം", "ml.style.rejected-names", "Elam (NEH 1:1); reviewed 1 Oct 2026 (round 3)"),
    ("വ്യാഖാന*", "വ്യാഖ്യാന*", "ml.lex.known-misspelling", "വ്യാഖ്യാനം (1TI 3:11); reviewed 1 Oct 2026 (round 4)"),
    ("ഈജിപ്ത്കാര*", "മിസ്രയീമ്യര*", "ml.style.rejected-names", "Egyptians: മിസ്രയീമ്യർ (reviewed 1 Oct 2026)"),
    ("ഈജിപ്ത്", "മിസ്രയീം", "ml.style.rejected-names", "rejected by the reviewer"),
    ("ഈജിപ്ത*", "മിസ്രയീമ*", "ml.style.rejected-names", "rejected by the reviewer (case forms: ഈജിപ്തിലെ)"),
    ("യോർദാൻ-", "യോർദ്ദാൻ-", "ml.style.rejected-names", "rejected by the reviewer"),
    ("യോർദാന*", "യോർദ്ദാന*", "ml.style.rejected-names", "rejected by the reviewer (case forms: യോർദാനിൽ)"),
    ('ഒരോ*', 'ഓരോ*', 'ml.lex.known-misspelling', 'reviewer decision 1 Oct 2026: ഓരോ (IRV 230×; ഒരോ- 11×)'),
    ('ദുഷ്ക്കർമ്മി*', 'ദുഷ്കർമ്മി*', 'ml.lex.known-misspelling', 'final review 1 Oct 2026: ദുഷ്കർമ്മി'),
    ('വാഞ്ചി*', 'വാഞ്ഛി*', 'ml.lex.known-misspelling', 'final review 1 Oct 2026: വാഞ്ഛിക്കുന്നു'),
    ('വെളിപാട*', 'വെളിപ്പാട*', 'ml.lex.known-misspelling', 'final review 1 Oct 2026: വെളിപ്പാട്'),
    ('യെരിഹോ*', 'യെരീഹോ*', 'ml.style.rejected-names', "final review 1 Oct 2026: യെരീഹോ is the IRV's usual spelling"),
    ('ഹിസ്ക്കീയാവ*', 'ഹിസ്കീയാവ*', 'ml.style.rejected-names', 'final review 1 Oct 2026: ഹിസ്കീയാവ് (40×) is approved'),
]
# §7 Q11: the first batch of proper-name stems for names.tsv
NAMES_SEED = ["സേലാ", "ശമൂവേൽ", "യോർദ്ദാൻ", "അശ്ശൂർ", "അബീരാം", "ഊറീം", "റബ്-ശാക്കേ", "മയസേയാവ്", "സിമ്രി", "യിഫ്താഹ്",
              "ഗിദെയോൻ", "മിസ്രയീം", "യിസ്രായേൽ", "യെഹൂദാ", "മോശെ", "നെബൂഖദ്നേസർ", "മേശക്ക്"]
# Final review (1 Oct 2026) of the names.tsv candidates: names (use=yes; the IRV's own forms, and യെരീഹോ /
# ഹിസ്കീയാവ് as the approved forms of the variants), variants flagged through KNOWN_MISSPELLINGS_SEED, and
# ordinary words that are not name variants.  The build writes them with these use values, so they are not asked again.
NAMES_REVIEWED = ['അന്ത്യൊക്യയിലേക്ക്', 'അന്ത്യൊക്യയിൽ', 'അഹീതൂബ്', 'ഇമ്മാനുവേൽ', 'എലിസബെത്ത്', 'എസ്ഥേറിനെ', 'എസ്ഥേറിനോട്', 'എസ്ഥേറിന്റെ', 'ഏദോമിനെക്കുറിച്ച്', 'ഏദോമ്യരെ', 'ഏദോമ്യർ', 'ഏലിയാവിനും', 'ഏലിയാവും', 'ഏലിയാവെ', 'ഏലിയാവ്', 'ഓബേദ്-ഏദോം', 'ഓബേദ്-ഏദോമിനെയും', 'ഓബേദ്-ഏദോമിന്റെ', 'താമാറിനെ', 'തർസോസിലേക്ക്', 'ദൊഫ്കയിൽ', 'നിക്കോദെമോസ്', 'പീലാത്തോസിനോട്', 'പീലാത്തോസിന്റെ', 'പീലാത്തോസും', 'പീലാത്തോസ്', 'ബേത്ലേഹേം', 'ബേത്ലേഹേമിന്റെ', 'ബേത്ലേഹേമിൽ', 'ബേത്ലേഹേമ്യനായ', 'യായിറോസ്', 'ലാസറിനെയും', 'ലാസറെ', 'ലൂക്കോസും', 'സിബ്മയിലെ', 'ഹാഗാറിന്റെ', 'യെരീഹോ', 'ഹിസ്കീയാവ്']
NAMES_VARIANT = ['യെരിഹോനിവാസികൾ', 'യെരിഹോരാജാവിനോട്', 'യെരിഹോവിനെതിരെ', 'യെരിഹോവിലേക്ക്', 'ഹിസ്ക്കീയാവ്']
NOT_NAMES = ['ഉള്ളൊര്', 'കല്ത്തളം', 'കിണറിനരികെ', 'തീയതി', 'തീയതിമുതൽ', 'ദുഷ്ക്കർമ്മി', 'ധനവാനായൊര്', 'പണസഞ്ചി', 'പെണ്ണാടിനെ', 'മഹത്ത്വം', 'മഹത്ത്വപ്പെടുത്തും', 'മഹത്ത്വപ്പെട്ടിരിക്കുന്ന്', 'മഹത്ത്വമുള്ള', 'രക്ഷപെടും', 'വാഞ്ചിക്കുന്ന്', 'വീടിനകത്ത്', 'വീടിനരികെ', 'വെളിപാടിനാൽ']
# Never paired by a consistency rule: different people, places or words that are spelled alike.
NEVER_PAIR = ["പത്രോസ",            # Pathros, a region of Egypt (EZK 29:14), not Peter
              "പത്രൊസ", "പൗലൊസ", "പൗലോസ",  # Q7: Peter / Paul spellings wait for the reviewer
              "തേക്ക",              # teak (ISA 44:14), not തെക്ക് south
              "യിപ്താഹ",            # Iphtah, a town (JOS 15:43), not Jephthah
              "ശിമ്രി",              # Shimri (1CH 26:10), not Zimri
              "അസ്സൂർ",             # Azzur (NEH 10:17), not Asshur
              "ഇവ്വ", "അവ്വ",        # the places Ivvah, Avva (2KI 17:24, 19:13), not ഇവ / അവ
              "കാടി",               # sour wine (NUM 6:3), not കാട്ടി
              "കൊയ്ത",              # കൊയ്ത് having reaped / കൊയ്ത്ത് harvest
              "വെളി",               # വെളിയിൽ outside / വെള്ളിയിൽ of silver (1KI 10:21)
              "മറക്ക", "മറയ്ക്ക",     # മറക്കുക forget / മറയ്ക്കുക hide
              "വിലക്ക",             # വിലക്ക് prohibition, not വില + dative
              "യിസ്രയേ"]            # Jezreel (യിസ്രയേലിൽ, യിസ്രയേല്യനായ നാബോത്ത്), not Israel യിസ്രായേൽ


NEVER_PAIR_EXACT = {"കൂടം", "കുടം",          # hammer / anvil (ISA 41:7), pot
                    "മുന്നിൽ", "മൂന്നിൽ",      # in front (LUK 18:39), in three
                    "ഏലാം"}                    # Elam, the country (not എല്ലാം all)


def never_pair(key: str) -> bool:
    return key in NEVER_PAIR_EXACT or key.startswith(tuple(NEVER_PAIR))


DIVINE_NAMES = ["യഹോവ", "ദൈവം", "യേശു", "ക്രിസ്തു", "കർത്താവ്", "പരിശുദ്ധാത്മാവ്", "മശീഹ"]
