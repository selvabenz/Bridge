"""Odia tables: character classes, the lookup key, the L1 shape grammar, L0 normalization, the confusion costs,
the consistency skeletons and the word lists of the grammar leads.  Pure data plus the small fix functions the
shape rules need; shared by scripts/or_build.py and the app's Odia profile (`langs/odia.py`, code "or": the
module cannot be called or.py, a Python keyword).
Normative source: docs/ODIA_RULES.md, built on the measurements in docs/ODIA_CORPUS_FACTS.md (section numbers
below refer to the rules doc).
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

from ..kinds import BigramRule, ConfusionCosts, Finding, ShapeRule, sign_sequence

ORI = "ଁ-ୣୱ"          # letters and signs of the Odia block (not the digits ୦-୯, ୰, fractions)
ZW = "\u200C\u200D"
APOS = "’'ʼ"                     # the elision apostrophe (ମୋ’, କ’ଣ, ତା’ପରେ); OV ʼ U+02BC, IRV ’ and '
LETTER = ORI + ZW
TOKEN_CH = LETTER
# a word: Odia letters, an elision apostrophe only between letters (କ’ଣ, ତା’ପରେ), hyphenated parts (§1.3)
_PART = f"[{LETTER}]+(?:[{APOS}][{LETTER}]+)*"
TOKEN_RE = re.compile(f"{_PART}(?:-{_PART})*")
SCRIPT = "଀-୿"
DETECT_LETTERS_RE = re.compile("[ଅ-ହଡ଼-ୡୱ]")
# the IRV and OV Odia files are 01-GEN.usfm … 67-REV.usfm, the same names as other folders on this PC (Hindi,
# Nepali): read under --lang or only; a folder is recognised as Odia by name only when it uses …IRVOri.SFM
FILE_RE = re.compile(r"^(\d\d)-?([A-Z0-9]{3})(?:IRVOri)?\.(?:usfm|sfm)$", re.IGNORECASE)
DETECT_FILE_RE = re.compile(r"^(\d\d)([A-Z0-9]{3})IRVOri\.SFM$", re.IGNORECASE)

NUKTA, VIRAMA, ANUSVARA, CANDRABINDU, VISARGA = "଼", "୍", "ଂ", "ଁ", "ଃ"
ZWJ, ZWNJ = "\u200D", "\u200C"
DANDA = "।"
VA, WA, BA = "ଵ", "ୱ", "ବ"          # ଵ (OV), ୱ (IRV), ବ
YA, YYA = "ଯ", "ୟ"                       # ଯ, ୟ
AI_MARK, AU_MARK = "ୖ", "ୗ"              # ୖ ୗ (only inside ୈ = େ+ୖ and ୌ = େ+ୗ)
VARGA = {"ଙ": "କଖଗଘ", "ଞ": "ଚଛଜଝ", "ଣ": "ଟଠଡଢ", "ନ": "ତଥଦଧ", "ମ": "ପଫବଭ"}
SIGNS = "ାିୀୁୂୃୄେୈୋୌ"


def cls(ch: str) -> str:
    """§1 classes, one letter per code point: C consonant, N nukta, v vowel sign, V independent vowel, M nasal
    (ଁ ଂ), H visarga, _ virama, Z ZWJ/ZWNJ, A avagraha, ' apostrophe, - hyphen, X anything else."""
    o = ord(ch)
    if 0x0B15 <= o <= 0x0B39 or o in (0x0B5C, 0x0B5D, 0x0B5F, 0x0B71):
        return "C"
    if o == 0x0B3C:
        return "N"
    if 0x0B3E <= o <= 0x0B4C or o in (0x0B55, 0x0B56, 0x0B57, 0x0B62, 0x0B63):
        return "v"
    if 0x0B05 <= o <= 0x0B14 or o in (0x0B60, 0x0B61):
        return "V"
    if o in (0x0B01, 0x0B02):
        return "M"
    if o == 0x0B03:
        return "H"
    if o == 0x0B4D:
        return "_"
    if o in (0x200C, 0x200D):
        return "Z"
    if o == 0x0B3D:
        return "A"
    if ch in APOS:
        return "'"
    if ch == "-":
        return "-"
    return "X"


# --------------------------------------------------------------------------------------------------
# §2 the lookup key (like Hindi's canon): NFC, joiners removed, ଵ read as ୱ (the OV writes ପରମେଶ୍ଵର with ଵ U+0B35
# 14,074 times, the IRV ପରମେଶ୍ୱର with ୱ U+0B71; one letter in two encodings), the apostrophe as ’.  The editor
# keeps the raw text, so underlines and edits land on what is written.
# --------------------------------------------------------------------------------------------------
_KEY = str.maketrans({ZWJ: None, ZWNJ: None, VA: WA, "'": "’", "ʼ": "’"})
# after ମ୍ the OV's ଵ is ବ: OV ମ୍ଵ 1,159 = IRV ମ୍ବ 1,429 (କିମ୍ବା, ତମ୍ବୁ, ସମ୍ବାଦ); everywhere else it is ୱ (facts M3)
MA_VA_RE = re.compile("(?<=ମ୍)([\u200C\u200D]?)ଵ")


def va_letter(w: str) -> str:
    """ଵ as the IRV writes it: ବ after ମ୍, otherwise ୱ."""
    return MA_VA_RE.sub(lambda m: m.group(1) + BA, w).replace(VA, WA)


@lru_cache(maxsize=262144)
def canon(w: str) -> str:
    if not unicodedata.is_normalized("NFC", w):
        w = unicodedata.normalize("NFC", w)
    return (va_letter(w) if VA in w else w).translate(_KEY)


# §2 L0 normalization of whole text (the build uses it on OV lines; the app reports each change)
INVISIBLE_RE = re.compile("[\u200B\uFEFF\u00A0\u2060\u0080-\u009F\uF000-\uF0FF⇧]")
PIPE_DANDA_RE = re.compile(f"(?<=[{ORI}”’])\\s*\\|")
COLON_IN_WORD_RE = re.compile(f"(?<=[{ORI}]):(?=[{ORI}])")
NN_DENTAL_RE = re.compile("ଣ୍ତ")                    # OV legacy-font artefact: ଣ୍ତ for ଣ୍ଡ (1,925 in the OV, 0 in the IRV)


def normalize_text(text: str) -> str:
    """NFC (ଡ଼ ଢ଼ U+0B5C/0B5D decompose; େ+ା/ୖ/ୗ compose to ୋ ୈ ୌ), the OV's private-use spaces (U+F020, U+F0A0)
    and NBSP as a space, the OV's ⇧ and other invisible characters removed, ASCII | as the danda, the visarga
    typed as a colon inside a word, ʼ as ’, ଣ୍ତ as ଣ୍ଡ."""
    t = unicodedata.normalize("NFC", text)
    t = INVISIBLE_RE.sub(lambda m: " " if m.group() in "\u00A0\uF020\uF0A0\uF02F" else "", t)
    t = PIPE_DANDA_RE.sub(DANDA, t)
    t = COLON_IN_WORD_RE.sub(VISARGA, t)
    t = NN_DENTAL_RE.sub("ଣ୍ଡ", t)
    return t.replace("ʼ", "’")


# --------------------------------------------------------------------------------------------------
# §3 L1 shape grammar
# --------------------------------------------------------------------------------------------------
INDEPENDENT_FIX = {"ଅା": "ଆ", "ଏୗ": "ଐ", "ଏୖ": "ଐ", "ଓୗ": "ଔ"}


def _const(v):
    return lambda w, s, e, c: v


def _map(table):
    return lambda w, s, e, c: table.get(w[s:e])


def _nukta_order(w, s, e, c):           # ପଗଡି଼ → ପଗଡ଼ି: the nukta typed after the vowel sign
    return w[s + 1] + w[s]


def _keep_one(w, s, e, c):              # ମୃତ୍ୟୁୁ → ମୃତ୍ୟୁ; two different signs have no safe fix
    return "" if w[s] == w[s - 1] else None


def _length_mark(w, s, e, c):
    """A bare length mark (NFC already composes େ+ୖ / େ+ୗ into ୈ / ୌ): typed before the େ (ଧୖେର୍ଯ୍ୟ → ଧୈର୍ଯ୍ୟ), or for
    the whole sign after a consonant (ଧୖର୍ଯ୍ୟ → ଧୈର୍ଯ୍ୟ), or doubled after ୈ / ୌ (ଦୈୖର୍ଘ୍ୟ → ଦୈର୍ଘ୍ୟ)."""
    prev = w[s - 1] if s else ""
    if prev in ("ୈ", "ୌ"):
        return ""
    if c[s - 1:s] in ("C", "N"):
        return "ୈ" if w[s] == AI_MARK else "ୌ"
    return None


def _drop_nukta(w, s, e, c):
    return w[s:e].replace(NUKTA, "")


SHAPE_RULES: list[ShapeRule] = [
    ShapeRule("or.norm.split-independent-vowel", "error", "cp", re.compile("ଅା|ଏ[ୖୗ]|ଓୗ"), _map(INDEPENDENT_FIX)),
    ShapeRule("or.norm.length-mark", "error", "cp", re.compile("[ୖୗ]+େ?"), _length_mark),
    ShapeRule("or.norm.nukta-order", "error", "cp", re.compile(f"(?<=[ଡଢ])[{SIGNS}]଼"), _nukta_order),
    ShapeRule("or.shape.nn-dental", "error", "cp", re.compile("ଣ୍ତ"), _const("ଣ୍ଡ")),
    ShapeRule("or.shape.ya-initial", "error", "cp", re.compile("^ୟ"), _const(YA)),
    # the ya-phala is ୍ୟ; ୍ଯ only follows the reph (କାର୍ଯ୍ୟ, ପର୍ଯ୍ୟନ୍ତ)
    ShapeRule("or.shape.ya-phala", "error", "cp", re.compile("(?<!ର)୍ଯ"), _const(VIRAMA + YYA)),
    ShapeRule("or.shape.unexpected-codepoint", "error", "cp", re.compile("[଀ଌୄ୕ୠ-ୣ]")),
    # a joiner that does not follow a virama (ଗେର୍ଶୋନ\u200D, ଅବ୍ରହାମ\u200Cଙ୍କ, a second ZWNJ): it does nothing; remove it
    ShapeRule("or.shape.zwj-misplaced", "error", "cls", re.compile("(?<!_)Z"), _const("")),
    ShapeRule("or.shape.double-vowel-sign", "error", "cls", re.compile("(?<=v)v"), _keep_one),
    ShapeRule("or.shape.sign-on-independent-vowel", "error", "cls", re.compile("(?<=V)v"), _const("")),
    ShapeRule("or.shape.sign-without-consonant", "error", "cls", re.compile("(?:^|(?<=[^CNvVZ]))v")),
    ShapeRule("or.shape.nukta-base", "error", "cp", re.compile("(?<![ଡଢ])଼"), _const("")),
    ShapeRule("or.shape.double-nasal", "error", "cp", re.compile("(?<=[ଁଂ])[ଁଂ]"), _const("")),
    ShapeRule("or.shape.nasal-position", "error", "cls", re.compile("^M|(?<=_)M|M(?=[vN])")),
    ShapeRule("or.shape.visarga-position", "error", "cls", re.compile("^H|(?<=_)H|H(?=[vMN])")),
    ShapeRule("or.shape.flap-initial", "error", "cp", re.compile("^[ଡଢ]଼"), _drop_nukta),
    # ଖଡ଼୍ଗ → ଖଡ୍ଗ, ଧନାଢ଼୍ୟ → ଧନାଢ୍ୟ: the flap never enters a conjunct (a word-final ଡ଼୍ is left alone)
    ShapeRule("or.shape.flap-in-conjunct", "error", "cp", re.compile("[ଡଢ]଼(?=୍[\u200C\u200D]?[କ-ହୟୱ])"),
              _drop_nukta),
    ShapeRule("or.shape.halant-position", "error", "cls", re.compile("^_|(?<=[vMHV_])_")),
    ShapeRule("or.shape.halant-follower", "error", "cls", re.compile("_(?=[^CZ'\\-])")),
    ShapeRule("or.shape.initial-letter", "warning", "cp", re.compile("^[ଙଞଣଳ]"), gated=True),
    # five consonants: ଉର୍ଦ୍ଧ୍ୱ (reph + ଦ୍ଧ୍ୱ) is a regular word
    ShapeRule("or.shape.long-conjunct", "warning", "cls", re.compile("CN?(?:_Z?CN?){4,}"), gated=True),
]
# ଓ (and), ନ (not), ଏ (this), ତ (emphatic), ଆ (come!), ଛ (six: ଛ ଶହ) are words (§3.6, facts M8)
SINGLE_LETTER_ALLOW = {"ଓ", "ନ", "ଏ", "ତ", "ଆ", "ଉ", "ଅ", "ଛ"}
LONG_TOKEN = 24


def shape_findings(w: str) -> list:
    """§3 sign-sequence findings plus the §3.6 word-level ones (single letter, long token)."""
    out = sign_sequence(w, cls, SHAPE_RULES)
    k = canon(w)
    if len(k) == 1 and k not in SINGLE_LETTER_ALLOW and cls(k) in "CV":
        out.append(Finding("or.shape.single-letter", "warning", 0, len(w), None, True))
    if len(k) >= LONG_TOKEN and "-" not in w:
        out.append(Finding("or.shape.long-token", "warning", 0, len(w), None, True))
    return out


def lexicon_fault(w: str, findings: list) -> bool:
    """True when an OV form must stay out of wordlist.txt: any shape error."""
    return any(f.severity == "error" for f in findings)


def fixed_word(w: str) -> str | None:
    """The word with every fixable shape error fixed (None when nothing is fixable)."""
    out, changed = w, False
    for _ in range(4):
        fs = [f for f in sign_sequence(out, cls, SHAPE_RULES) if f.severity == "error" and f.fix is not None]
        if not fs:
            break
        out = fs[0].fixed(out)
        changed = True
    return out if changed else None


# --------------------------------------------------------------------------------------------------
# §4.2 confusion costs (O01–O20).  Units: a consonant with its nukta; a homorganic nasal + virama + stop (ନ୍ତ)
# and an anusvara + stop (ଂତ) so that the two spellings are one substitution; the reph ର୍; the phalas ୍ର ୍ୟ ୍ୱ ୍ବ.
# Odia has no length contrast in speech, so ି/ୀ and ୁ/ୂ are the cheapest real typos (facts M10).
# --------------------------------------------------------------------------------------------------
_NASAL_STOP = "|".join(f"{n}୍[{s}]" for n, s in VARGA.items())
UNITS_RE = re.compile(f"(?:{_NASAL_STOP})|ଂ[କ-ଭ]|ର୍(?=[କ-ହୟୱ])|୍[ରୟୱବଯ]|[କ-ହ]଼?|.", re.S)


def _costs() -> ConfusionCosts:
    sub: dict = {}

    def add(a, b, c, k):
        key = frozenset((a, b))
        if key not in sub or c < sub[key][0]:
            sub[key] = (c, k)

    for a, b in (("ି", "ୀ"), ("ୁ", "ୂ"), ("ଇ", "ଈ"), ("ଉ", "ଊ")):
        add(a, b, 0.3, "vowel_length")                                               # O01
    add("ଡ", "ଡ଼", 0.2, "flap")                                                      # O02
    add("ଢ", "ଢ଼", 0.2, "flap")
    add("୍ୱ", "୍ବ", 0.2, "phala")                                                    # O03
    add("ୱ", "ବ", 0.4, "ba_wa")
    add("ଯ", "ୟ", 0.2, "ya")                                                         # O04
    add("୍ୟ", "୍ଯ", 0.2, "ya")
    add("ଜ", "ଯ", 0.5, "ja_ya")                                                      # O05
    add("ଲ", "ଳ", 0.3, "la_lla")                                                     # O06
    add("ନ", "ଣ", 0.4, "na_nna")                                                     # O07
    for a, b in (("ଶ", "ଷ"), ("ଶ", "ସ"), ("ଷ", "ସ")):
        add(a, b, 0.4, "sibilant")                                                   # O08
    add(ANUSVARA, CANDRABINDU, 0.2, "nasal")                                         # O09
    for n, stops in VARGA.items():
        for s in stops:
            add(n + VIRAMA + s, ANUSVARA + s, 0.2, "pancham")                        # O10
            add(ANUSVARA + s, s, 0.3, "nasal")
    for a, b in (("େ", "ୈ"), ("ୋ", "ୌ"), ("ଏ", "ଐ"), ("ଓ", "ଔ")):
        add(a, b, 0.4, "diphthong")                                                  # O11
    add("ୃ", "୍ର", 0.4, "ri")                                                        # O12
    add("ୃ", "ି", 0.6, "ri")
    add("ଣ୍ଡ", "ଣ୍ତ", 0.1, "shape")
    for a, b in zip("କଗଚଜଟଡତଦପବ", "ଖଘଛଝଠଢଥଧଫଭ"):
        add(a, b, 0.7, "aspiration")                                                 # O13
    for a, b in zip("ତଥଦଧ", "ଟଠଡଢ"):
        add(a, b, 0.7, "retroflex")                                                  # O14
    indel = {ANUSVARA: (0.3, "nasal"), CANDRABINDU: (0.3, "nasal"),                  # O09
             "ର" + VIRAMA: (0.15, "reph")}                                           # O15: a moved reph = 2 × 0.15
    return ConfusionCosts(units=UNITS_RE, sub=sub, indel=indel, sub_final={},
                          indel_final={VIRAMA: (0.2, "halant")}, transpose=(1.0, "transpose"))   # O16, O20


CONFUSION = _costs()
EDIT_CLASSES = ("vowel_length", "flap", "phala", "ba_wa", "ya", "ja_ya", "la_lla", "na_nna", "sibilant", "nasal",
                "pancham", "diphthong", "ri", "halant", "reph", "aspiration", "retroflex", "transpose", "shape", "other")
# edits that are a house-style choice, not a typo: a rare word that differs from a common one only by these is a
# consistency question (§6), never a rare-near-common lead
HOUSE_CLASSES = {"pancham", "halant"}


# --------------------------------------------------------------------------------------------------
# §6 consistency skeletons (counts in docs/ODIA_CORPUS_FACTS.md M6)
# --------------------------------------------------------------------------------------------------
_PANCHAM_RE = re.compile("|".join(f"{n}୍(?=[{s}](?!଼))" for n, s in VARGA.items()))


def sk_pancham(w):
    return _PANCHAM_RE.sub(ANUSVARA, w)


def sk_halant(w):
    return w[:-1] if w.endswith(VIRAMA) and len(w) > 1 else w


def sk_length(w):
    return w.translate(_LENGTH)


_LENGTH = str.maketrans({"ୀ": "ି", "ୂ": "ୁ", "ଈ": "ଇ", "ଊ": "ଉ"})


def sk_flap(w):
    return w.replace(NUKTA, "")


def flap_expected(w: str) -> str:
    """The word with ଡ/ଢ written as Odia spells them: the flap ଡ଼ ଢ଼ inside a word (ଗୁଡ଼ିକ, ବଡ଼, ଦୃଢ଼), the plain
    letter at the start of a word or a compound part (ଡାକିଲେ, ମହା-ଡାଳ) and inside a conjunct (ଖଣ୍ଡ, ଖଡ୍ଗ)."""
    s = sk_flap(w)
    out = []
    for i, ch in enumerate(s):
        out.append(ch)
        if ch in "ଡଢ":
            prev, nxt = (s[i - 1] if i else ""), (s[i + 1] if i + 1 < len(s) else "")
            if i and prev not in "-’" + VIRAMA + ZW and nxt != VIRAMA:
                out.append(NUKTA)
    return "".join(out)


def sk_phala(w):
    return w.replace(VIRAMA + BA, VIRAMA + WA)


def sk_ya(w):
    return w.replace(VIRAMA + YYA, "\x01").replace(YYA, YA).replace("\x01", VIRAMA + YYA)


def sk_lla(w):
    return w.replace("ଳ", "ଲ")


def sk_house(w):
    """Every house-style fold at once (pancham, final ୍): a word whose folded form is an OV word is a spelling
    variant of it (ସଂଗ / OV ସଙ୍ଗ), not an unknown word."""
    return sk_halant(sk_pancham(w))


SKELETONS = {"pancham": sk_pancham, "halant": sk_halant, "length": sk_length, "flap": sk_flap, "phala": sk_phala,
             "ya": sk_ya, "lla": sk_lla}

# Loose skeleton: forms within weighted cost ≈ 0.5 of each other share it (candidate pairs for suggestions)
_LOOSE = str.maketrans({NUKTA: None, ANUSVARA: None, CANDRABINDU: None, VIRAMA: None,
                        "ୀ": "ି", "ୂ": "ୁ", "ଈ": "ଇ", "ଊ": "ଉ", "ୈ": "େ", "ୌ": "ୋ", "ଐ": "ଏ", "ଔ": "ଓ",
                        "ଷ": "ସ", "ଶ": "ସ", "ଣ": "ନ", "ୱ": "ବ", "ୟ": "ଯ", "ଳ": "ଲ", "ୃ": "ି"})


def loose_skeleton(w: str) -> str:
    w = re.sub("ର୍(?=[କ-ହୟୱ])", "", w)               # the reph, wherever it stands
    w = _PANCHAM_RE.sub("", w)
    return w.translate(_LOOSE)


# --------------------------------------------------------------------------------------------------
# §5 grammar word lists
# --------------------------------------------------------------------------------------------------
# §5.1 bound endings written as a word of their own (ଲେଖକ ଙ୍କୁ → ଲେଖକଙ୍କୁ, ଈଶ୍ୱରଙ୍କ ଠାରୁ → ଈଶ୍ୱରଙ୍କଠାରୁ).  Not ମାନେ
# (also "obeys": କଥା ମାନେ), not ଗଣ (also "count"), not ଜଣ (a classifier written apart: ପଚାଶ ଜଣ), not ରେ (also
# the vocative: ରେ ଦୁଷ୍ଟ ଦାସ).
DETACHED_SUFFIXES = frozenset("""ଠାରୁ ଠାରେ ଙ୍କ ଙ୍କୁ ଙ୍କର ଙ୍କଠାରେ ଙ୍କଠାରୁ ମାନଙ୍କ ମାନଙ୍କୁ ମାନଙ୍କର ମାନଙ୍କଠାରେ ମାନଙ୍କଠାରୁ
ଗୁଡ଼ିକ ଗୁଡ଼ିକର ଗୁଡ଼ିକୁ ଗୁଡ଼ାକ ଗଣର ଗଣଙ୍କ କୁ ରୁ ର""".split())

# §5.2 pronoun + copula side by side: the copula's person must be the pronoun's (ଆମ୍ଭେ ଅଛି → ଅଛୁ).  Not ସେ / ଯେ
# (also "that"), not ତୁ (ତୁ ଅଛୁ is right: the intimate 2nd person takes -ଉ like ଆମ୍ଭେ).
_FIRST_SG = frozenset({"ମୁଁ"})
_FIRST_PL = frozenset({"ଆମ୍ଭେ", "ଆମ୍ଭେମାନେ", "ଆମେ", "ଆମେମାନେ"})
_SECOND = frozenset({"ତୁମ୍ଭେ", "ତୁମ୍ଭେମାନେ", "ତୁମେ", "ତୁମେମାନେ"})
_THIRD_PL = frozenset({"ସେମାନେ", "ଏମାନେ"})
BIGRAM_RULES: list[BigramRule] = [
    BigramRule("or.gram.pronoun-copula", _FIRST_SG, frozenset({"ଅଛୁ", "ଅଛ", "ଅଛନ୍ତି"}), "right", "ଅଛି", "ମୁଁ takes ଅଛି"),
    BigramRule("or.gram.pronoun-copula", _FIRST_SG, frozenset({"ଅଟୁ", "ଅଟ", "ଅଟନ୍ତି"}), "right", "ଅଟେ", "ମୁଁ takes ଅଟେ"),
    BigramRule("or.gram.pronoun-copula", _FIRST_PL, frozenset({"ଅଛି", "ଅଛ", "ଅଛନ୍ତି"}), "right", "ଅଛୁ", "ଆମ୍ଭେ takes ଅଛୁ"),
    BigramRule("or.gram.pronoun-copula", _FIRST_PL, frozenset({"ଅଟେ", "ଅଟ", "ଅଟନ୍ତି"}), "right", "ଅଟୁ", "ଆମ୍ଭେ takes ଅଟୁ"),
    BigramRule("or.gram.pronoun-copula", _SECOND, frozenset({"ଅଛି", "ଅଛୁ", "ଅଛନ୍ତି"}), "right", "ଅଛ", "ତୁମ୍ଭେ takes ଅଛ"),
    BigramRule("or.gram.pronoun-copula", _SECOND, frozenset({"ଅଟେ", "ଅଟୁ", "ଅଟନ୍ତି"}), "right", "ଅଟ", "ତୁମ୍ଭେ takes ଅଟ"),
    BigramRule("or.gram.pronoun-copula", _THIRD_PL, frozenset({"ଅଛି", "ଅଛୁ", "ଅଛ"}), "right", "ଅଛନ୍ତି",
               "a plural subject takes ଅଛନ୍ତି"),
    BigramRule("or.gram.pronoun-copula", _THIRD_PL, frozenset({"ଅଟେ", "ଅଟୁ", "ଅଟ"}), "right", "ଅଟନ୍ତି",
               "a plural subject takes ଅଟନ୍ତି"),
    BigramRule("or.gram.pronoun-copula", frozenset({"ତୁ"}), frozenset({"ଅଛି", "ଅଛ", "ଅଛନ୍ତି"}), "right", "ଅଛୁ", "ତୁ takes ଅଛୁ"),
]

# §5.3 honorific declension: a person spoken of with honour takes -ଙ୍କ before the case ending (ଯୀଶୁଙ୍କୁ, ସଦାପ୍ରଭୁଙ୍କର).
# plain ending -> the honorific one
HONORIFIC_ENDINGS = {"କୁ": "ଙ୍କୁ", "ର": "ଙ୍କର", "ରୁ": "ଙ୍କଠାରୁ", "ରେ": "ଙ୍କଠାରେ"}
# quantifiers: ସମସ୍ତଙ୍କୁ is "everyone", ସମସ୍ତକୁ "everything"; both are right.  Bases ending in ମାନ are plurals.
HONORIFIC_STOP = frozenset("ସମସ୍ତ ଅନେକ କେତେକ ଅନ୍ୟ ଉଭୟ ପ୍ରତ୍ୟେକ ଅଧିକାଂଶ".split())

# §4.3 known misspellings (seed): the build keeps only rows whose right form the corpora use and whose wrong form
# is not an OV word (facts M11)
MISSPELLING_SEED = [
    # vowel length (O01): Odia speech has no long/short contrast
    ("ସେହୀ", "ସେହି"), ("ସେହିକ୍ଷଣୀ", "ସେହିକ୍ଷଣି"), ("ଦ୍ୱୀତୀୟ", "ଦ୍ୱିତୀୟ"), ("ସମ୍ପୁର୍ଣ୍ଣ", "ସମ୍ପୂର୍ଣ୍ଣ"), ("ଦୁରୀକୃତ", "ଦୂରୀକୃତ"),
    ("ମୁର୍ତ୍ତି", "ମୂର୍ତ୍ତି"), ("ମୁଲ୍ୟ", "ମୂଲ୍ୟ"), ("ନୁତନ", "ନୂତନ"), ("ନିଗୁଢ଼", "ନିଗୂଢ଼"), ("ବ୍ୟତିତ", "ବ୍ୟତୀତ"),
    ("ପରିକ୍ଷା", "ପରୀକ୍ଷା"), ("ସମୂଦାୟ", "ସମୁଦାୟ"), ("ପୁର୍ବ", "ପୂର୍ବ"), ("ପୁର୍ଣ୍ଣ", "ପୂର୍ଣ୍ଣ"), ("ଧୁଳି", "ଧୂଳି"),
    ("ଭୁମି", "ଭୂମି"), ("ଜିବନ", "ଜୀବନ"), ("ଦିର୍ଘ", "ଦୀର୍ଘ"), ("ଗଭିର", "ଗଭୀର"), ("ଶାନ୍ତୀ", "ଶାନ୍ତି"), ("ଶକ୍ତୀ", "ଶକ୍ତି"),
    ("ପୃଥିବି", "ପୃଥିବୀ"), ("ସ୍ତ୍ରି", "ସ୍ତ୍ରୀ"), ("ଆଶିର୍ବାଦ", "ଆଶୀର୍ବାଦ"), ("ଶୂଦ୍ଧ", "ଶୁଦ୍ଧ"), ("ଶୂଚି", "ଶୁଚି"),
    ("ସାଧୂ", "ସାଧୁ"),
    # au / o (O11)
    ("କୋଣସି", "କୌଣସି"), ("ଯେକୋଣସି", "ଯେକୌଣସି"),
    # sibilants (O08), reph and order
    ("ଧ୍ୱଂଶ", "ଧ୍ୱଂସ"), ("ନିର୍ଦ୍ଦୋଶ", "ନିର୍ଦ୍ଦୋଷ"), ("ବସୀଭୂତ", "ବଶୀଭୂତ"), ("ମନୁଶ୍ୟ", "ମନୁଷ୍ୟ"), ("ଶିଶ୍ୟ", "ଶିଷ୍ୟ"),
    ("ବିସ୍ୱାସ", "ବିଶ୍ୱାସ"), ("ଆର୍ଶୀବାଦ", "ଆଶୀର୍ବାଦ"), ("ପ୍ରାଥର୍ନା", "ପ୍ରାର୍ଥନା"), ("ସ୍ରୁଷ୍ଟି", "ସୃଷ୍ଟି"), ("ଦୃଷ୍ଠି", "ଦୃଷ୍ଟି"),
    ("ଉଦ୍ଦାର", "ଉଦ୍ଧାର"), ("ଉଦ୍ଦାରକର୍ତ୍ତା", "ଉଦ୍ଧାରକର୍ତ୍ତା"), ("ଇତିହାସିକ", "ଐତିହାସିକ"),
    # the flap ଡ଼ / ଢ଼ (O02)
    ("ଆଡେ", "ଆଡ଼େ"), ("ଆଡକୁ", "ଆଡ଼କୁ"), ("ବଡ", "ବଡ଼"), ("ଛାଡି", "ଛାଡ଼ି"), ("ପଡି", "ପଡ଼ି"), ("ପୀଡିତ", "ପୀଡ଼ିତ"),
    ("ଛଡା", "ଛଡ଼ା"),
    # other measured slips (facts M10)
    ("ଆମ୍ବମାନଙ୍କୁ", "ଆମ୍ଭମାନଙ୍କୁ"), ("ଯାଉଥିୱା", "ଯାଉଥିବା"), ("ପାଫ", "ପାପ"), ("ୱନ୍ଦ", "ବନ୍ଦ"), ("ମହାରାଜ୍", "ମହାରାଜ"),
    ("ଈଶ୍ୱବରଙ୍କ", "ଈଶ୍ୱରଙ୍କ"),
]
# §6 divine names: the IRV forms (ସଦାପ୍ରଭୁ, ପରମେଶ୍ୱର, ଯୀଶୁ, ଖ୍ରୀଷ୍ଟ, ପ୍ରଭୁ, ଈଶ୍ୱର) and variants a typist makes; each
# also with the honorific case endings
_DIVINE = [("ପରମେଶର", "ପରମେଶ୍ୱର"), ("ପରମେସ୍ୱର", "ପରମେଶ୍ୱର"), ("ପରମେଶ୍ୱାର", "ପରମେଶ୍ୱର"), ("ଯିଶୁ", "ଯୀଶୁ"),
           ("ଯୀସୁ", "ଯୀଶୁ"), ("ଯୀଶୂ", "ଯୀଶୁ"), ("ଖ୍ରିଷ୍ଟ", "ଖ୍ରୀଷ୍ଟ"), ("ଖ୍ରୀଷ୍ଠ", "ଖ୍ରୀଷ୍ଟ"), ("ଖ୍ରୀସ୍ଟ", "ଖ୍ରୀଷ୍ଟ"),
           ("ସଦାପ୍ରଭୂ", "ସଦାପ୍ରଭୁ"), ("ସଦାପ୍ରଭୁଁ", "ସଦାପ୍ରଭୁ"), ("ପ୍ରଭୂ", "ପ୍ରଭୁ"), ("ଇଶ୍ୱର", "ଈଶ୍ୱର")]
DIVINE_NAME_VARIANTS = [(a + e, b + e) for a, b in _DIVINE for e in ("", "ଙ୍କ", "ଙ୍କର", "ଙ୍କୁ", "ଙ୍କଠାରେ", "ଙ୍କଠାରୁ")]

# abbreviations seen before "." in the IRV that are not book names
ABBREVIATION_SEED = {"ଖ୍ରୀ", "ପୂ", "ଖ୍ରୀଃ", "କି", "ମି"}
