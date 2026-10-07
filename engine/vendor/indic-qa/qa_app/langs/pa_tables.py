"""Punjabi (Gurmukhi) tables: character classes, the L1 shape grammar, L0 normalization, the
confusion costs, and the word lists of the agreement rules.  Pure data plus the small fix
functions the shape rules need; shared by scripts/pa_build.py and the app's Punjabi profile.
Normative source: docs/PUNJABI_RULES.md (section numbers below refer to it).
"""
from __future__ import annotations

import re
import unicodedata

from ..kinds import ConfusionCosts, Finding, ShapeRule, sign_sequence

GUR = "ਁ-ੵ"                        # letters, signs, digits of the Gurmukhi block (not । ॥)
TOKEN_RE = re.compile(rf"[{GUR}]+(?:-[{GUR}]+)*")
SCRIPT = "਀-੿"

NUKTA, TIPPI, BINDI, ADHAK, VIRAMA = "਼", "ੰ", "ਂ", "ੱ", "੍"
DANDA, DOUBLE_DANDA = "।", "॥"
NUKTA_BASES = "ਸਜਫਖਗਲ"

# §1 classes, one letter per code point:
#   C consonant  N nukta  s short sign taking tippi (ਿ ੁ ੂ)  l sign taking bindi (ਾ ੀ ੇ ੈ ੋ ੌ)
#   a independent vowel taking tippi (ਅ ਇ)  o independent vowel taking bindi (ਆ ਈ ਉ ਊ ਏ ਐ ਓ ਔ)
#   T tippi  B bindi  A adhak  _ virama  U bare bearer (ੲ ੳ)  X unexpected code point  - hyphen
_CONS = {chr(c) for c in range(0x0A15, 0x0A3A)} - {"਩", "਱", "਴", "਷"}
_CONS |= {chr(c) for c in range(0x0A59, 0x0A5F)} - {"੝"}
CLASS: dict[str, str] = {ch: "C" for ch in _CONS}
CLASS.update({NUKTA: "N", TIPPI: "T", BINDI: "B", ADHAK: "A", VIRAMA: "_", "ੲ": "U", "ੳ": "U", "-": "-"})
CLASS.update({ch: "s" for ch in "ਿੁੂ"})
CLASS.update({ch: "l" for ch in "ਾੀੇੈੋੌ"})
CLASS.update({ch: "a" for ch in "ਅਇ"})
CLASS.update({ch: "o" for ch in "ਆਈਉਊਏਐਓਔ"})


def cls(ch: str) -> str:
    return CLASS.get(ch, "X")


# §2 L0 normalization of whole text (the build uses it on OV lines; the app reports each change)
INVISIBLE_RE = re.compile("[​‌‍﻿ ]")
GUR_DIGITS = str.maketrans({chr(0x0A66 + i): str(i) for i in range(10)})
PIPE_DANDA_RE = re.compile(r"\|")


def normalize_text(text: str) -> str:
    """NFC, invisible characters removed, ASCII | as the danda, Gurmukhi digits as ASCII."""
    t = unicodedata.normalize("NFC", text)
    t = INVISIBLE_RE.sub(lambda m: " " if m.group() == " " else "", t)
    t = PIPE_DANDA_RE.sub(DANDA, t)
    return t.translate(GUR_DIGITS)


# §3 L1 shape grammar
INDEPENDENT_FIX = {"ਅਾ": "ਆ", "ਅੈ": "ਐ", "ਅੌ": "ਔ", "ਉੁ": "ਉ", "ਓੁ": "ਓ", "ਏੇ": "ਏ", "ਇਿ": "ਇ"}


def _fix_independent(w, s, e, c):
    return INDEPENDENT_FIX.get(w[s:e])


def _fix_double_sign(w, s, e, c):
    return w[s] if w[s] == w[s + 1] else None


def _fix_double_nasal(w, s, e, c):
    prev = c[s - 1] if s else ""
    if prev in ("l", "o"):
        return BINDI
    if prev in ("C", "N", "s", "a"):
        return TIPPI
    return None


def _const(v):
    return lambda w, s, e, c: v


CONSONANT = "ਕ-ਹਖ਼-ਫ਼"
SHAPE_RULES: list[ShapeRule] = [
    ShapeRule("pa.shape.unexpected-codepoint", "error", "cls", re.compile("X")),
    ShapeRule("pa.shape.bare-bearer", "error", "cls", re.compile("U")),
    ShapeRule("pa.shape.sign-on-independent-vowel", "error", "cls", re.compile("[ao][sl]"), _fix_independent),
    ShapeRule("pa.shape.double-vowel-sign", "error", "cls", re.compile("[sl][sl]"), _fix_double_sign),
    ShapeRule("pa.shape.sign-without-consonant", "error", "cls", re.compile("(?:^|(?<=[TBA_UX-]))[sl]")),
    ShapeRule("pa.shape.double-nukta", "error", "cls", re.compile("NN"), _const(NUKTA)),
    ShapeRule("pa.shape.nukta-after-sign", "error", "cls", re.compile("(?<=[slTBA_aoU])N")),
    ShapeRule("pa.shape.nukta-base", "error", "cp",
              re.compile(rf"(?:^|(?<=[ਅ-ਔ{CONSONANT}]))(?<![{NUKTA_BASES}]){NUKTA}")),
    ShapeRule("pa.shape.double-nasal", "error", "cls", re.compile("[TB][TB]"), _fix_double_nasal),
    ShapeRule("pa.shape.tippi-for-bindi", "error", "cls", re.compile("(?<=[lo])T"), _const(BINDI)),
    ShapeRule("pa.shape.bindi-for-tippi", "error", "cls", re.compile("(?<=[CNsa])B"), _const(TIPPI)),
    ShapeRule("pa.shape.nasal-position", "error", "cls", re.compile("^[TB]|(?<=[A_U-])[TB]|[TB](?=[sl])")),
    ShapeRule("pa.shape.double-adhak", "error", "cls", re.compile("AA"), _const(ADHAK)),
    # adhak before ਈ inside a word (ਗਵੱਈਆਂ, ਮੁਹੱਈਆ) is an accepted spelling
    ShapeRule("pa.shape.adhak-position", "error", "cp", re.compile(rf"^{ADHAK}|{ADHAK}$|{ADHAK}(?=[^{CONSONANT}ਈ])")),
    ShapeRule("pa.shape.virama-position", "error", "cp",
              re.compile(rf"^{VIRAMA}|{VIRAMA}$|(?<![{CONSONANT}{NUKTA}]){VIRAMA}|{VIRAMA}(?=[^{CONSONANT}])")),
    ShapeRule("pa.shape.virama-follower", "warning", "cp", re.compile(rf"{VIRAMA}[^ਹਰ]")),
    ShapeRule("pa.shape.virama-bearer", "warning", "cp",
              re.compile(rf"(?<![ਨੜਪਰਲਕਤਮਦਗਸ]){VIRAMA}ਹ|(?<![ਪਤਕਸਦਗਭ{NUKTA}]){VIRAMA}ਰ"), gated=True),
    ShapeRule("pa.shape.adhak-after-long-vowel", "warning", "cp", re.compile(f"(?<=[ਾੀੂੇੈੋੌਆਈਊਏਐਓਔ]){ADHAK}"), gated=True),
    ShapeRule("pa.shape.adhak-before-rare-consonant", "warning", "cp",
              re.compile(f"{ADHAK}(?=[ਹਰੜਣਨਮਯਵ]|ਲ{NUKTA})"), gated=True),
]
SINGLE_LETTER_ALLOW = {"ਆ", "ਈ", "ਏ", "ਓ"}
LONG_TOKEN = 18


# §4.2 confusion costs (P01..P16).  ੍ਹ and ੍ਰ are single units.
def _pairs(spec: str, cost: float, kind: str) -> dict:
    out = {}
    for pair in spec.split():
        a, b = pair.split("/")
        out[frozenset((a, b))] = (cost, kind)
    return out


_SUB: dict = {}
for table in (
    _pairs("ਰ/੍ਰ", 0.3, "rara"),                                                       # P04
    _pairs("ੰ/ਂ", 0.1, "nasal"),                                                        # P06
    _pairs("ਿ/ੀ ੁ/ੂ ੇ/ੈ ੋ/ੌ ਇ/ਈ ਉ/ਊ ਏ/ਐ ਓ/ਔ ਅ/ਆ", 0.5, "vowel_length"),              # P07 (P15 = ਿ/ੀ before ਆ ਓ ਉ)
    _pairs("ਨ/ਣ ਡ/ੜ ਰ/ੜ", 0.5, "retroflex"),                                            # P09
    _pairs("ਬ/ਵ ਵ/ਓ ਯ/ਜ ਯ/ਈ", 0.7, "other"),                                            # P11
    _pairs("ਤ/ਟ ਦ/ਡ ਥ/ਠ ਧ/ਢ", 0.8, "retroflex"),                                        # P12
    _pairs("ਕ/ਖ ਗ/ਘ ਚ/ਛ ਜ/ਝ ਟ/ਠ ਡ/ਢ ਤ/ਥ ਦ/ਧ ਪ/ਫ ਬ/ਭ", 0.8, "aspiration"),           # P13
    _pairs("ਹ/੍ਹ", 0.6, "haha"),                                                        # P14 (ਵਰਹੇ/ਵਰ੍ਹੇ)
):
    for k, v in table.items():
        if k not in _SUB or v[0] < _SUB[k][0]:
            _SUB[k] = v
CONFUSION = ConfusionCosts(
    units=re.compile(f"{VIRAMA}[ਹਰ]|."),
    sub=_SUB,
    indel={NUKTA: (0.2, "nukta"), ADHAK: (0.3, "adhak"), f"{VIRAMA}ਹ": (0.3, "haha"),       # P01 P02 P03
           TIPPI: (0.3, "nasal"), BINDI: (0.3, "nasal"), "ਹ": (0.6, "haha")},             # P05 P14
    sub_final=_pairs("ਾ/ੇ ੇ/ੀ ਾ/ੀ", 0.6, "inflection"),                                  # P08
    transpose=(1.0, "transpose"),                                                        # P16
)
EDIT_CLASSES = ("nukta", "adhak", "haha", "rara", "nasal", "vowel_length", "inflection", "retroflex", "aspiration",
                "transpose", "other")

# Loose skeleton: every form within cost 0.5 of another shares it (used to find candidate pairs).
_LOOSE = str.maketrans({NUKTA: None, ADHAK: None, TIPPI: None, BINDI: None,
                        "ੀ": "ਿ", "ੂ": "ੁ", "ੈ": "ੇ", "ੌ": "ੋ", "ਈ": "ਇ", "ਊ": "ਉ", "ਐ": "ਏ", "ਔ": "ਓ", "ਆ": "ਅ",
                        "ਣ": "ਨ", "ੜ": "ਰ", "ਡ": "ਰ"})


def loose_skeleton(w: str) -> str:
    return w.replace(f"{VIRAMA}ਹ", "").replace(f"{VIRAMA}ਰ", "ਰ").translate(_LOOSE)


# §6 consistency skeletons (as measured in the rules doc)
SKELETONS = {
    "nukta": lambda w: w.replace(NUKTA, ""),
    "haha": lambda w: w.replace(f"{VIRAMA}ਹ", ""),
    "rara": lambda w: w.replace(f"{VIRAMA}ਰ", "ਰ"),
    "adhak": lambda w: w.replace(ADHAK, ""),
}

# §5.1 genitive / possessive markers and their gender
GENDER_MARKERS: dict[str, str] = {"ਦਾ": "M", "ਦੇ": "M", "ਦੀ": "F", "ਦੀਆਂ": "F"}
for stem in ("ਮੇਰ", "ਤੇਰ", "ਸਾਡ", "ਤੁਹਾਡ", "ਆਪਣ", "ਉਹਦ", "ਜਿਹਦ", "ਕਿਹਦ"):
    GENDER_MARKERS.update({stem + "ਾ": "M", stem + "ੇ": "M", stem + "ੀ": "F", stem + "ੀਆਂ": "F"})
# §5.2 postpositions that take the oblique case
POSTPOSITIONS = set("ਨੂੰ ਨੇ ਤੋਂ ਦਾ ਦੇ ਦੀ ਦੀਆਂ ਵਿੱਚ ਵਿੱਚੋਂ ਨਾਲ ਉੱਤੇ ਲਈ ਕੋਲ ਕੋਲੋਂ ਵੱਲ ਵੱਲੋਂ ਵਾਂਗੂੰ ਬਾਰੇ ਪਾਸ ਹੇਠ ਅੱਗੇ ਪਿੱਛੇ ਉੱਪਰ ਸਾਹਮਣੇ ਵਿਚਕਾਰ".split())
# words after a genitive marker that are not its head noun (§5.1 list + the markers + postpositions)
GENDER_STOP = set("""ਹੀ ਵੀ ਨਾ ਇੱਕ ਦੋ ਤਿੰਨ ਸਾਰੇ ਸਾਰੀ ਸਾਰੀਆਂ ਸਾਰਾ ਸਾਰਿਆਂ ਸਭ ਕੋਈ ਕੁਝ ਵੱਲ ਵੱਲੋਂ ਬਾਕੀ ਪਵਿੱਤਰ ਉਹ ਇਹ ਉਸ ਇਸ ਉਨ੍ਹਾਂ
ਇਨ੍ਹਾਂ ਜੋ ਜਿਹੜਾ ਜਿਹੜੀ ਜਿਹੜੇ ਹੈ ਸੀ ਹਨ ਸਨ ਹਾਂ ਹੋ ਅਤੇ ਤੇ ਕਿ ਜਾਂ ਪਰ ਬਹੁਤ ਵੱਡਾ ਵੱਡੀ ਵੱਡੇ ਛੋਟਾ ਛੋਟੀ ਚੰਗਾ ਚੰਗੀ ਨਵਾਂ ਨਵੀਂ
ਆਪਣਾ ਆਪਣੀ ਆਪਣੇ ਹਰ ਕਈ ਬਹੁਤੇ ਹੋਰ ਪਹਿਲਾ ਪਹਿਲੀ ਪਹਿਲੇ ਦੂਜਾ ਦੂਜੀ ਦੂਜੇ""".split()) | set(GENDER_MARKERS) | POSTPOSITIONS
# compound postpositions (ਦੇ ਅਨੁਸਾਰ, ਦੇ ਰਾਹੀਂ, ਦੀ ਥਾਂ …): they follow the marker but are not its head noun
COMPOUND_POSTPOSITIONS = set("""ਅਨੁਸਾਰ ਰਾਹੀਂ ਕਾਰਨ ਕਰਕੇ ਵਿਰੁੱਧ ਨੇੜੇ ਬਾਅਦ ਪਿੱਛੋਂ ਮਗਰੋਂ ਬਿਨਾਂ ਸਮੇਤ ਵਾਂਗ ਵਾਂਗੂੰ ਵਰਗਾ ਵਰਗੀ ਵਰਗੇ ਜਿਹਾ ਜਿਹੀ
ਜਿਹੇ ਆਲੇ-ਦੁਆਲੇ ਆਸ-ਪਾਸ ਅੰਦਰ ਬਾਹਰ ਉੱਤੋਂ ਹੇਠਾਂ ਹੇਠੋਂ ਵਿਚਕਾਰੋਂ ਦੁਆਰਾ ਖ਼ਾਤਰ ਖਾਤਰ ਬਦਲੇ ਲਾਗੇ ਕੋਲੋਂ ਸਾਹਮਣਿਓਂ ਥਾਂ ਆਪ ਤੀਕ ਤੀਕਰ ਤੱਕ""".split())
GENDER_STOP |= COMPOUND_POSTPOSITIONS

# §6 divine names: seed of known_misspellings.tsv (wrong -> right)
DIVINE_NAME_VARIANTS = [
    ("ਪਰਮੇਸ਼ਰ", "ਪਰਮੇਸ਼ੁਰ"), ("ਪਰਮੇਸੁਰ", "ਪਰਮੇਸ਼ੁਰ"), ("ਯਹੋਵਾ", "ਯਹੋਵਾਹ"), ("ਯਿਸ਼ੂ", "ਯਿਸੂ"),
    ("ਪ੍ਰਭੁ", "ਪ੍ਰਭੂ"), ("ਪਵਿਤਰ", "ਪਵਿੱਤਰ"),
]

# IRV file names: 01GENIRVPun.SFM … 67REVIRVPun.SFM
FILE_RE = re.compile(r"^(\d\d)([A-Z0-9]{3})IRVPun\.SFM$", re.IGNORECASE)


def shape_findings(w: str) -> list:
    """§3 sign-sequence findings plus the §3.6 word-level ones (single letter, long token)."""
    out = sign_sequence(w, cls, SHAPE_RULES)
    if len(w) == 1 and w not in SINGLE_LETTER_ALLOW and cls(w) != "X":
        out.append(Finding("pa.shape.single-letter", "warning", 0, 1, None, False))
    if len(w) >= LONG_TOKEN and "-" not in w:
        out.append(Finding("pa.shape.long-token", "warning", 0, len(w), None, True))
    return out


ARCHAIC_VIRAMA = "ਵਯ"          # ੍ਵ ੍ਯ: old spellings (ਸ੍ਵਾਦ, ਬਿਨ੍ਯਾਮੀਨ), kept in the lexicon


def lexicon_fault(w: str, findings: list) -> bool:
    """True when an OV form must stay out of wordlist.txt: any shape error, or a virama before a
    letter other than ਹ ਰ (a typing fault) unless it is the archaic ੍ਵ / ੍ਯ."""
    for f in findings:
        if f.severity == "error":
            return True
        if f.rule == "pa.shape.virama-follower" and w[f.end - 1] not in ARCHAIC_VIRAMA:
            return True
    return False
