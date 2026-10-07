"""Hindi (Devanagari) tables: character classes, the lookup key, the L1 shape grammar, L0 normalization, the
confusion costs, the consistency skeletons and the word lists of the grammar leads.  Pure data plus the
small fix functions the shape rules need; shared by scripts/hi_build.py and the app's Hindi profile.
Normative source: docs/HINDI_RULES.md (with the decisions of 1 Oct 2026 at its end) and the measurements
in docs/HINDI_CORPUS_FACTS.md (section numbers below refer to the rules doc).
"""
from __future__ import annotations

import re
import unicodedata

from ..kinds import BigramRule, ConfusionCosts, Finding, ShapeRule, sign_sequence

DEV = "ऀ-ॣॱ-ॿ"          # letters and signs of the Devanagari block (not । ॥, digits, ॰)
ZW = "‌‍"
TOKEN_CH = DEV + ZW
TOKEN_RE = re.compile(f"[{TOKEN_CH}]+(?:-[{TOKEN_CH}]+)*")
SCRIPT = "ऀ-ॿ"
# detection counts letters only: the danda and the digits are shared with Gurmukhi text (§0.5)
DETECT_LETTERS_RE = re.compile("[ऄ-हऽॐक़-ॡॲ-ॿ]")
# the IRV Hindi files are 01-GEN.usfm … 67-REV.usfm (OV Nepali and OV Odia use the same names), so they are
# read under --lang hi only; a folder is recognised as Hindi by name only when it uses …IRVHin.SFM
FILE_RE = re.compile(r"^(\d\d)-?([A-Z0-9]{3})(?:IRVHin)?\.(?:usfm|sfm)$", re.IGNORECASE)
DETECT_FILE_RE = re.compile(r"^(\d\d)([A-Z0-9]{3})IRVHin\.SFM$", re.IGNORECASE)

NUKTA, HALANT, ANUSVARA, CHANDRABINDU, VISARGA = "़", "्", "ं", "ँ", "ः"
ZWJ, ZWNJ = "‍", "‌"
DANDA, DOUBLE_DANDA = "।", "॥"
NUQTA_BASES = "कखगजफ"                      # Perso-Arabic nuqta letters (house style, Q1); ड़ ढ़ are spelling
TOP_SIGNS = "िीेैोौॅॉ"   # signs that reach above the headline
VARGA = {"ङ": "कखगघ", "ञ": "चछजझ", "ण": "टठडढ", "न": "तथदध", "म": "पफबभ"}


def cls(ch: str) -> str:
    """§1 classes, one letter per code point: C consonant, N nuqta, v vowel sign, V independent vowel,
    M nasal (ँ ं), H visarga, _ halant, Z ZWJ/ZWNJ, A avagraha, O om, - hyphen, X anything else."""
    o = ord(ch)
    if 0x0915 <= o <= 0x0939 or 0x0958 <= o <= 0x095F or 0x0978 <= o <= 0x097F:
        return "C"
    if o == 0x093C:
        return "N"
    if 0x093E <= o <= 0x094C or o in (0x0962, 0x0963, 0x0955, 0x0956, 0x0957, 0x094E, 0x094F, 0x093A, 0x093B):
        return "v"
    if 0x0904 <= o <= 0x0914 or o in (0x0960, 0x0961) or 0x0972 <= o <= 0x0977:
        return "V"
    if o in (0x0900, 0x0901, 0x0902):
        return "M"
    if o == 0x0903:
        return "H"
    if o == 0x094D:
        return "_"
    if o in (0x200C, 0x200D):
        return "Z"
    if o == 0x093D:
        return "A"
    if o == 0x0950:
        return "O"
    if ch == "-":
        return "-"
    return "X"


# --------------------------------------------------------------------------------------------------
# the lookup key (like Malayalam's canon): NFC, joiners removed.  The OV writes परमेश्‍वर with a ZWJ after
# the halant 10,803 times (BSI convention), the IRV writes परमेश्वर; both are one word.  The editor keeps the
# raw text, so underlines and edits land on what is written.
# --------------------------------------------------------------------------------------------------
_KEY_DROP = str.maketrans({ZWJ: None, ZWNJ: None, "\u009D": None})


def canon(w: str) -> str:
    return unicodedata.normalize("NFC", w).translate(_KEY_DROP)


# §2 L0 normalization of whole text (the build uses it on OV lines; the app reports each change)
INVISIBLE_RE = re.compile("[​﻿ ⁠\u0080-\u009F]")
DEV_DIGITS = str.maketrans({chr(0x0966 + i): str(i) for i in range(10)})
PIPE_DANDA_RE = re.compile(r"(?<=[ऀ-ॣ”’])\s*\|")
COLON_IN_WORD_RE = re.compile("(?<=[ऀ-ॣ]):(?=[ऀ-ॣ])")


def normalize_text(text: str) -> str:
    """NFC, invisible and control characters removed (NBSP → space), ASCII | as the danda, the visarga typed
    as a colon inside a word (दु:ख → दुःख), Devanagari digits as ASCII (the IRV writes ASCII only, Q8)."""
    t = unicodedata.normalize("NFC", text)
    t = INVISIBLE_RE.sub(lambda m: " " if m.group() == " " else "", t)
    t = PIPE_DANDA_RE.sub(DANDA, t)
    t = COLON_IN_WORD_RE.sub(VISARGA, t)
    return t.translate(DEV_DIGITS)


def visarga_fold_final(text: str, visarga_words: set) -> str:
    """The OV writes a word-final visarga as a colon too (अत:, छ:); read it as ः when that word with ः is an
    IRV word (otherwise the colon is punctuation: कहता है:)."""
    def end(m):
        w = m.group(1)
        return w + VISARGA if canon(w + VISARGA) in visarga_words else m.group(0)
    return re.sub(f"([{TOKEN_CH}]+):", end, text)


# --------------------------------------------------------------------------------------------------
# §3 L1 shape grammar
# --------------------------------------------------------------------------------------------------
INDEPENDENT_FIX = {"अा": "आ", "अो": "ओ", "अौ": "औ", "एे": "ऐ", "अॉ": "ऑ"}
SIGN_FIX = {"ाे": "ो", "ाै": "ौ", "ेे": "े"}
NON_HINDI_FIX = {"ॆ": "े", "ॊ": "ो", "ऎ": "ए", "ऒ": "ओ", "ॄ": "ृ"}


def _const(v):
    return lambda w, s, e, c: v


def _map(table):
    return lambda w, s, e, c: table.get(w[s:e])


def _nuqta_order(w, s, e, c):          # बडे़ → बड़े: the nuqta typed after the vowel sign
    return w[s + 1] + w[s]


def _drop_sign_on_vowel(w, s, e, c):   # ऐे → ऐ, औै → औ
    return ""


def _keep_one(w, s, e, c):              # लिीए has no safe fix; ेे → े
    return "" if w[s] == w[s - 1] else None


def _drop_nuqta(w, s, e, c):
    return w[s:e].replace(NUKTA, "")


def _flap_after_nasal(w, s, e, c):
    """मुँण्ड़ा → मुँड़ा: after ँ the nasal is marked twice; drop ण्/न् and keep the flap (reviewed 1 Oct 2026).
    Without ँ (पण्ड़ित) drop the nuqta."""
    return w[s + 2:e] if s and w[s - 1] == CHANDRABINDU else w[s:e].replace(NUKTA, "")


SHAPE_RULES: list[ShapeRule] = [
    ShapeRule("hi.norm.split-independent-vowel", "error", "cp", re.compile("अ[ाोौॉ]|एे"),
              _map(INDEPENDENT_FIX)),
    ShapeRule("hi.norm.split-vowel-sign", "error", "cp", re.compile("ा[ेै]|ेे"), _map(SIGN_FIX)),
    ShapeRule("hi.norm.nuqta-order", "error", "cp", re.compile("(?<=[क-ह])[ा-ौ]़"), _nuqta_order),
    ShapeRule("hi.shape.vocalic-rr", "error", "cp", re.compile("ॠ"), _const("ऋ")),        # ॠतु → ऋतु (4 in the IRV)
    ShapeRule("hi.shape.non-hindi-sign", "error", "cp", re.compile("[ॆॊॄॢॣऎऒ]"),
              _map(NON_HINDI_FIX)),
    ShapeRule("hi.shape.unexpected-codepoint", "error", "cp",
              re.compile("[ऀऄऩऱळऴऺऻॎॏॕ-ॗॡॱ-ॿ]")),
    # a joiner that does not follow a halant (सुरूफ‍िनीकी, गुँधत‍ी, word-final त‍): it does nothing; remove it
    ShapeRule("hi.shape.zwj-misplaced", "error", "cls", re.compile("(?<!_)Z"), _const("")),
    ShapeRule("hi.shape.double-vowel-sign", "error", "cls", re.compile("(?<=v)v"), _keep_one),
    ShapeRule("hi.shape.sign-on-independent-vowel", "error", "cls", re.compile("(?<=V)v"), _drop_sign_on_vowel),
    ShapeRule("hi.shape.sign-without-consonant", "error", "cls", re.compile("(?:^|(?<=[^CNvVZ]))v")),
    ShapeRule("hi.shape.nuqta-after-sign", "error", "cls", re.compile("(?<=[vMH_])N")),
    ShapeRule("hi.shape.double-nuqta", "error", "cls", re.compile("(?<=N)N"), _const("")),
    ShapeRule("hi.shape.nuqta-base", "error", "cp", re.compile("(?<![कखगजडढफय़])़")),
    ShapeRule("hi.shape.chandrabindu-over-top-sign", "error", "cp", re.compile(f"(?<=[{TOP_SIGNS}])ँ"), _const(ANUSVARA)),
    ShapeRule("hi.shape.double-nasal", "error", "cp", re.compile("(?<=[ँं])[ँं]"), _const("")),
    ShapeRule("hi.shape.nasal-before-halant", "error", "cls", re.compile("M(?=_)")),
    ShapeRule("hi.shape.nasal-position", "error", "cls", re.compile("^M|(?<=_)M|M(?=v)")),
    ShapeRule("hi.shape.visarga-position", "error", "cls", re.compile("^H|(?<=_)H|H(?=[vM])")),
    ShapeRule("hi.shape.flap-initial", "error", "cp", re.compile("^[डढ]़"), _drop_nuqta),
    # §3.4 revised (facts, item 1): a flap after ँ or ं is correct (ढूँढ़, बेंड़े); only after ण्/न् is it a fault
    ShapeRule("hi.shape.flap-after-nasal", "error", "cp", re.compile("[णन]्[डढ]़"), _flap_after_nasal),
    ShapeRule("hi.shape.flap-in-conjunct", "error", "cp", re.compile("(?<=्)[डढ]़|[डढ]़(?=्)"), _drop_nuqta),
    ShapeRule("hi.shape.halant-position", "error", "cls", re.compile("^_|(?<=[vMHV_])_")),
    ShapeRule("hi.shape.halant-follower", "error", "cls", re.compile("_(?=[^CZ-])"), _const("")),   # रिश्आतइम → रिशआतइम
    ShapeRule("hi.shape.reph-before-sign", "error", "cp", re.compile("र्(?=[ा-ौ])")),
    ShapeRule("hi.shape.candra", "warning", "cp", re.compile("[ॅॉऍऑ]"), gated=True),
    ShapeRule("hi.shape.long-conjunct", "warning", "cls", re.compile("CN?(?:_Z?CN?){3,}"), gated=True),
]
# ई and स stand alone only in the abbreviations ई. पू. / ई.स.; न आ व are words (§3.6, facts item 11)
SINGLE_LETTER_ALLOW = {"न", "आ", "व", "ए", "ओ", "औ", "ई", "स"}
LONG_TOKEN = 20


def shape_findings(w: str) -> list:
    """§3 sign-sequence findings plus the §3.6 word-level ones (single letter, long token)."""
    out = sign_sequence(w, cls, SHAPE_RULES)
    k = canon(w)
    if len(k) == 1 and k not in SINGLE_LETTER_ALLOW and cls(k) in "CV":
        out.append(Finding("hi.shape.single-letter", "warning", 0, len(w), None, True))
    if len(k) >= LONG_TOKEN and "-" not in w:
        out.append(Finding("hi.shape.long-token", "warning", 0, len(w), None, True))
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
    if changed:                          # Q18: the conjuncts are written without joiners (क‍न्‍ने → कन्ने)
        out = out.replace(ZWJ, "").replace(ZWNJ, "")
    return out if changed else None


# --------------------------------------------------------------------------------------------------
# §4.2 confusion costs (H01–H20).  Units: a consonant with its nuqta; a homorganic nasal + halant + stop
# (न्द) and an anusvara or chandrabindu + stop (ंद) so that pancham ↔ anusvara is one substitution; the reph
# र्; the subscript ्र; a word-final ये / यी (the -ए/-ये house style).
# --------------------------------------------------------------------------------------------------
_NASAL_STOP = "|".join(f"{n}्[{s}]" for n, s in VARGA.items())
UNITS_RE = re.compile(f"य[ेी](?=[ँं]?$)|(?:{_NASAL_STOP})|[ँं][क-भ]|र्(?=[क-ह])"
                      f"|्र|[क-ह]़?|.", re.S)


def _costs() -> ConfusionCosts:
    sub: dict = {}

    def add(a, b, c, k):
        key = frozenset((a, b))
        if key not in sub or c < sub[key][0]:
            sub[key] = (c, k)

    for b in NUQTA_BASES:
        add(b, b + NUKTA, 0.2, "nuqta")                                              # H01
    add("ड", "ड" + NUKTA, 0.2, "flap")                                               # H02
    add("ढ", "ढ" + NUKTA, 0.2, "flap")
    add(ANUSVARA, CHANDRABINDU, 0.1, "nasal")                                        # H03
    for n, stops in VARGA.items():
        for s in stops:
            add(n + HALANT + s, ANUSVARA + s, 0.2, "pancham")                        # H04
            add(n + HALANT + s, CHANDRABINDU + s, 0.3, "pancham")
            add(ANUSVARA + s, CHANDRABINDU + s, 0.1, "nasal")
            add(ANUSVARA + s, s, 0.3, "nasal")                                       # H05
            add(CHANDRABINDU + s, s, 0.3, "nasal")
    for a, b in (("ि", "ी"), ("ु", "ू"), ("इ", "ई"), ("उ", "ऊ")):
        add(a, b, 0.4, "vowel_length")                                               # H06
    for a, b in (("े", "ै"), ("ो", "ौ"), ("ए", "ऐ"), ("ओ", "औ")):
        add(a, b, 0.5, "vowel_length")                                               # H07
    add("ये", "ए", 0.2, "ye_ending")                                                 # H08
    add("यी", "ई", 0.2, "ye_ending")
    add("ृ", HALANT + "र", 0.4, "ri")                                                # H11
    add("ृ", "ि", 0.6, "ri")
    for a, b in (("श", "ष"), ("श", "स"), ("ष", "स")):
        add(a, b, 0.5, "sibilant")                                                   # H12
    add("न", "ण", 0.5, "na_na")                                                      # H13
    add("ब", "व", 0.5, "ba_va")                                                      # H14
    for a, b in zip("कगचजटडतदपब", "खघछझठढथधफभ"):
        add(a, b, 0.7, "aspiration")                                                 # H17
    for a, b in zip("तथदध", "टठडढ"):
        add(a, b, 0.7, "retroflex")                                                  # H18
    indel = {ANUSVARA: (0.3, "nasal"), CHANDRABINDU: (0.3, "nasal"),                 # H05
             "र" + HALANT: (0.15, "reph")}                                           # H10: a moved reph = 2 × 0.15
    return ConfusionCosts(units=UNITS_RE, sub=sub, indel=indel, sub_final={},
                          indel_final={HALANT: (0.2, "halant")}, transpose=(1.0, "transpose"))   # H09, H20


CONFUSION = _costs()
EDIT_CLASSES = ("nuqta", "flap", "nasal", "pancham", "vowel_length", "ye_ending", "halant", "reph", "ri", "sibilant",
                "na_na", "ba_va", "conjunct", "aspiration", "retroflex", "transpose", "other")
# edits that are a house-style choice, not a typo: a rare word that differs from a common one only by these
# is a consistency question (§6), never a rare-near-common lead
HOUSE_CLASSES = {"nuqta", "pancham", "ye_ending", "halant"}


# --------------------------------------------------------------------------------------------------
# skeletons
# --------------------------------------------------------------------------------------------------
_PANCHAM_RE = re.compile("|".join(f"{n}्(?=[{s}](?!़))" for n, s in VARGA.items()))


def sk_nuqta(w):
    return re.sub(f"(?<=[{NUQTA_BASES}])़", "", w)


def sk_flap(w):
    return re.sub("(?<=[डढ])़", "", w)


def sk_chandra(w):
    return w.replace(CHANDRABINDU, ANUSVARA)


def sk_pancham(w):
    return _PANCHAM_RE.sub(ANUSVARA, w)


def sk_ye(w):
    if w.endswith(CHANDRABINDU):
        w = w[:-1] + ANUSVARA
    for a, b in (("यें", "एं"), ("यीं", "ईं"), ("ये", "ए"), ("यी", "ई")):
        if w.endswith(a) and len(w) > len(a):
            return w[:-len(a)] + b
    return w


def sk_halant(w):
    return w[:-1] if w.endswith(HALANT) and len(w) > 1 else w


def sk_house(w):
    """Every house-style fold at once (nuqta, ँ/ं, pancham, -ये/-ए, final ्): a word whose folded form is an OV
    word is a spelling variant of it (हजार / OV हज़ार), not an unknown word."""
    return sk_halant(sk_ye(sk_pancham(sk_chandra(sk_nuqta(w)))))


# §6 consistency skeletons (counts in docs/HINDI_CORPUS_FACTS.md M6)
SKELETONS = {"nuqta": sk_nuqta, "chandrabindu": sk_chandra, "pancham": sk_pancham, "ye": sk_ye, "halant": sk_halant}

# Loose skeleton: forms within weighted cost ≈ 0.5 of each other share it (candidate pairs for suggestions)
_LOOSE = str.maketrans({NUKTA: None, ANUSVARA: None, CHANDRABINDU: None, HALANT: None,
                        "ी": "ि", "ू": "ु", "ै": "े", "ौ": "ो", "ई": "इ", "ऊ": "उ", "ऐ": "ए", "औ": "ओ",
                        "ष": "स", "श": "स", "ण": "न", "ब": "व", "ृ": "ि"})


def loose_skeleton(w: str) -> str:
    w = sk_ye(w)
    w = re.sub("र्(?=[क-ह])", "", w)                 # the reph, wherever it stands (आर्शीवाद / आशीर्वाद)
    w = _PANCHAM_RE.sub("", w)
    return w.translate(_LOOSE)


# --------------------------------------------------------------------------------------------------
# §5 grammar word lists (revised from the measurements, facts items 5–8)
# --------------------------------------------------------------------------------------------------
GENDER_MARKERS: dict[str, str] = {"का": "M", "की": "F"}
for _stem in ("मेर", "तेर", "हमार", "तुम्हार", "अपन"):
    GENDER_MARKERS.update({_stem + "ा": "M", _stem + "ी": "F"})
for _stem in ("आपक", "उसक", "इसक", "उनक", "इनक", "जिसक", "किसक", "जिनक"):
    GENDER_MARKERS.update({_stem + "ा": "M", _stem + "ी": "F"})
# variable adjectives and ordinals follow a marker but are not its noun (का पहली, की दूसरा)
VARIABLE_ADJ = set("""पहला पहली पहले दूसरा दूसरी दूसरे तीसरा तीसरी तीसरे चौथा चौथी चौथे बड़ा बड़ी बड़े छोटा छोटी छोटे अच्छा अच्छी
अच्छे बुरा बुरी बुरे सारा सारी सारे नया नई नये नए ऐसा ऐसी ऐसे वैसा वैसी वैसे जैसा जैसी जैसे कैसा कैसी कैसे अपना अपनी अपने
सच्चा सच्ची सच्चे पूरा पूरी पूरे लम्बा लम्बी लम्बे ऊँचा ऊँची ऊँचे गहरा गहरी गहरे पुराना पुरानी पुराने काला काली काले
उजला उजली उजले हरा हरी हरे पीला पीली पीले ठंडा ठंडी ठंडे थोड़ा थोड़ी थोड़े बहुत अगला अगली अगले पिछला पिछली पिछले
इकलौता इकलौती इकलौते प्यारा प्यारी प्यारे भारी हल्का हल्की हल्के सीधा सीधी सीधे टेढ़ा टेढ़ी टेढ़े""".split())
GENDER_STOP = set("""ही भी तो एक दो तीन चार सब सारे कोई कुछ बहुत पवित्र वह यह उस इस जो है था थी हैं थे और कि न नहीं से
में पर को ने के का की हो वे ये उन इन तब जब फिर क्योंकि परन्तु परंतु लिये लिए साथ ओर तरह नाईं तक वरन् बल्कि अथवा या तथा
एवं सो यदि हर प्रत्येक किसी जिस जिन किन कौन क्या""".split()) | VARIABLE_ADJ | set(GENDER_MARKERS)
# §5.2 postpositions that take the oblique; साथ and लिए/लिये are also a noun and a verb (तेरा साथ दूँगा,
# नरसिंगा लिए हुए), so they are not used (facts item 8)
POSTPOSITIONS = set("ने को से में पर का के की तक पास द्वारा ओर विषय".split())

# §5.3 compound postpositions; कारण, विषय, साथ are nouns too (उसका कारण यह है), and निकट, बीच, ऊपर are an
# adjective or noun in the reviewed misses (की निकट कुटुम्बिनी, नाले का बीच, यरदन का ऊपर से बहता जल), so they are left out
KE_GROUP = frozenset("लिए लिये पास बाद द्वारा बारे सामने पीछे नीचे भीतर अनुसार समान बिना बदले आगे".split())
KI_GROUP = frozenset("ओर तरह नाईं".split())
# §5.4 verbs of saying / knowing / seeing that introduce a कि-clause (कहा की → कहा कि)
SAY_VERBS = frozenset("""कहा कहता कहती कहते बोला बोले बोली पूछा सोचा जानता जानते जानती देखा सुना लिखा बताया जान देखो सुनो
समझा समझो कहो कहे जानो""".split())
# §5.5 words after which ओर (side) is meant (चारों और → चारों ओर); एक और and की और are correct (item 6)
OR_LEFT = frozenset("चारों दाहिनी बाईं बायीं".split())
# "दाहिनी और बाईं ओर", "एक उसकी दाहिनी और एक उसकी बाईं": there और is "and" (reviewed: 5 of 9 were this)
OR_STOP_AFTER = frozenset("बाईं बायीं दाहिनी एक पाँच दूसरे दूसरी दोनों उसकी उनकी".split())
# a negative imperative / infinitive: literary Hindi writes न (चिन्ता न करो), ना is colloquial (reviewed LUK 12:21)
NA_RIGHT = frozenset("करो करना करें करे कीजिए डरो डरना घबराओ घबराना होना जाओ जाना".split())

BIGRAM_RULES: list[BigramRule] = [
    BigramRule("hi.gram.compound-postposition", frozenset({"का", "की"}), KE_GROUP, "left", "के",
               "a compound postposition takes के (के लिये, के समान, के निकट)"),
    BigramRule("hi.gram.compound-postposition", frozenset({"का", "के"}), KI_GROUP, "left", "की",
               "की ओर, की तरह, की नाईं take की"),
    BigramRule("hi.gram.ki-ki", SAY_VERBS, frozenset({"की"}), "right", "कि",
               "कि (that) introduces what is said or known; की is the genitive / did"),
    BigramRule("hi.gram.aur-or", OR_LEFT, frozenset({"और"}), "right", "ओर", "ओर is side / direction; और is and",
               stop_after=OR_STOP_AFTER),
    BigramRule("hi.gram.na-negative", frozenset({"ना"}), NA_RIGHT, "left", "न",
               "a negative imperative takes न (चिन्ता न करो); ना is colloquial"),
    BigramRule("hi.gram.pronoun-copula", frozenset({"मैं"}), frozenset({"है", "हैं"}), "right", "हूँ",
               "मैं takes हूँ"),
    # not आप: it is also the reflexive "himself" (वह आप है, 1SA 25:25, reviewed)
    BigramRule("hi.gram.pronoun-copula", frozenset({"हम", "वे", "ये"}), frozenset({"है"}), "right", "हैं",
               "a plural subject takes हैं"),
    BigramRule("hi.gram.pronoun-copula", frozenset({"तुम"}), frozenset({"है", "हैं"}), "right", "हो", "तुम takes हो"),
    BigramRule("hi.gram.pronoun-copula", frozenset({"तू"}), frozenset({"हैं"}), "right", "है", "तू takes है"),
]

# §6 pronoun + postposition: written as one word or two; the IRV decides per pair (facts item 2)
PRONOUNS = ("मैं", "मुझ", "तुझ", "हम", "तुम", "आप", "उस", "उन", "इस", "इन", "जिस", "जिन", "किस", "किन",
            "उन्हों", "इन्हों", "जिन्हों", "किन्हों")
JOIN_POSTPOSITIONS = ("ने", "को", "से", "में", "पर", "का", "की", "के", "तक")
JOIN_PAIRS = tuple((p, q) for p in PRONOUNS for q in JOIN_POSTPOSITIONS) + (("इस", "लिए"), ("इस", "लिये"))

# §4.3 known misspellings (seed; only those whose right form the corpora use, facts M11)
MISSPELLING_SEED = [
    ("आर्शीवाद", "आशीर्वाद"), ("प्राथर्ना", "प्रार्थना"), ("अत्याधिक", "अत्यधिक"), ("आधीन", "अधीन"),
    ("कृप्या", "कृपया"), ("द्वितिय", "द्वितीय"), ("परिक्षा", "परीक्षा"), ("इतिहासिक", "ऐतिहासिक"), ("नें", "ने"),
    ("उद्दार", "उद्धार"), ("उद्दारकर्ता", "उद्धारकर्ता"), ("जबकी", "जबकि"),
]
# §6 divine names: the IRV forms (परमेश्वर 5,742, यहोवा 7,193, प्रभु, यीशु, मसीह) and variants a typist makes
DIVINE_NAME_VARIANTS = [
    ("परमेशवर", "परमेश्वर"), ("परमश्वर", "परमेश्वर"), ("परमेस्वर", "परमेश्वर"), ("परमेश्र्वर", "परमेश्वर"),
    ("यहुवा", "यहोवा"), ("यहोव", "यहोवा"), ("यीसु", "यीशु"), ("यिशु", "यीशु"), ("यीशू", "यीशु"),
    ("मसिह", "मसीह"), ("प्रभू", "प्रभु"),
]
# decided in the reviewed workbook (1 Oct 2026): flagged even where the wrong form is the IRV's majority.
# A row ending in * is a prefix: every word that starts with it (परिक्षा, परिक्षाएँ → परीक्षा, परीक्षाएँ).
REVIEWED_MISSPELLINGS = [
    ("उज्जवल", "उज्ज्वल", "Q20"), ("हदास्सा", "हदस्सा", "EST 2:7 verse spelling"), ("वैद्द्यों", "वैद्यों", "doubled द"),
    ("निर्मोल", "अनमोल", "not a Hindi word"), ("खम्भें", "खम्भे", "direct plural of खम्भा"), ("मुर्झ*", "मुरझ*", "मुरझाना"),
    ("यिश्वीयों", "यिश्वियों", "oblique plural shortens ी"), ("प्रतिक्षा*", "प्रतीक्षा*", "प्रतीक्षा"), ("सदृश्य", "सदृश", "सदृश"),
    ("संगीतकारो", "संगीतकारों", "oblique plural"), ("परिक्ष*", "परीक्ष*", "परीक्षा"), ("बंधाया", "बँधाया", "from बाँधना"),
    ("बन्धाया", "बँधाया", "from बाँधना"), ("मुँण्ड़न", "मुण्डन", "the noun"),
]
# proper names the review confirmed (Q12): known words, never a rare-near-common or archaic lead
NAMES_SEED = [("शाप", "1CH 2:47, 49 (Shaaph)"), ("मास", "1CH 2:27 (Maaz)"), ("यर्हा", "1CH 2:34 (Jarha)"),
              ("बेर", "JDG 9:21 (Beer)"), ("बरसब्बास", "ACT 15:22 (Barsabbas)"), ("हदस्सा", "EST 2:7"),
              ("येशू", "LUK 3:29"), ("रिशआतइम", "JDG 3:8-10 (Cushan-rishathaim)")]
# correct words the review found among the unknown and compound words (added to extra_words.txt)
REVIEWED_WORDS = """औपचारिक पहनवा विशेषताओं सद्भावना आकर्षण सैन्य जातीय ग्रामीण संजोया अल्पसंख्यक संस्कृति प्रदर्शक आंशिक
निर्वासित दिख चिरपरिचित लेशमात्र दर्ज तृतीय अपर्याप्त सिर्फ स्वार्थपूर्ण अंधकारपूर्ण मुक्तिदान सर्वविदित महत्वहीन सर्वशक्ति
कपटपूर्ण मृत्युलोक अर्थहीन दयापूर्ण उत्तरकाल दूतकार्य कार्यकर्ता महासंकट कार्यभार शुभकामना आधारभूत प्रतिरक्षा शान्तिप्रिय
सदाचार अनुशासनहीन अतिभोग जीन""".split()          # जीन = saddle (2SA 16:1)
# -ये/-यी words whose य is part of the stem, not the -ए/-ई ending (गायें = cows, दायी = responsible)
YE_STEM_WORDS = frozenset("गायें गायों रायें दायी स्थायी न्यायी उपायी".split())

# gender the final review settled against the learned counts (CH-0174: याजकों की अन्नबलि)
GENDER_OVERRIDE = {"अन्नबलि": "F", "बलि": "F"}

# abbreviations seen before "." in the IRV that are not book names (ई. पू., ई.स.)
ABBREVIATION_SEED = {"ई", "पू", "स", "इति", "पद", "कि", "मी", "ओब"}       # ई. पू., ई.स., कि.मी.
