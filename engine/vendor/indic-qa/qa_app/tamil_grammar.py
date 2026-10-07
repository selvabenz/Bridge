"""Tamil புணர்ச்சி (sandhi) rules: does a word take a doubled hard consonant (ஒற்று) before க/ச/த/ப?

The rules live in `dictionary/sandhi_rules.tsv` so a reviewer can audit and correct them; the table
below seeds that file the first time and is the fallback when it is missing.  Each row matches the
*bare* form of the first word (the word without any ஒற்று it carries):

    id  verdict  match  exclude  guard  classes  short  name_ta  name_en  note

- verdict:  double (ஒற்று மிகும்) | bare (ஒற்று மிகாது) | ambiguous (grammar cannot tell; stops later rows)
            | skip (a reviewer decision: no lead at all for these words, whatever the OV or IRV habit says)
- match / exclude:  `=w1|w2` exact words, `-e1|-e2` word endings, `re:<regex>` (anchored at the word end)
- a match that starts with `next:` is about the *following* word instead: `next:^b1|^b2` its beginnings,
  `next:=w` exact words (the exclude of such a row uses the same `^` / `=` forms, without `next:`).
  These rows are a house style that holds whatever the first word is (no ஒற்று before தேவன்): they are
  tried before every first-word row, and the OV and IRV habits do not override them.
- guard:  a named check in GUARDS that needs the lexicon (e.g. the stem before a dative ending is a word)
- classes:  letters of KSTP (K=க S=ச T=த P=ப) the verdict holds for; blank = all four.  Before any
  other class the verdict is "ambiguous".

The first row that matches wins.  A row whose exclude or guard rejects the word falls through to the
rows below it, so specific rows go first (exact words, long endings, then short endings).
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import usfm_doc as ud

bd = ud.bd
KSTP = bd.KSTP
PULLI = "்"
FIELDS = ["id", "verdict", "match", "exclude", "guard", "classes", "short", "name_ta", "name_en", "note"]
VERDICTS = ("double", "bare", "ambiguous", "skip")
RULES_FILE = "sandhi_rules.tsv"


# --------------------------------------------------------------------------------------
# Guards: morphological checks that need the lexicon
# --------------------------------------------------------------------------------------

def _dative_stem(bare: str, lex) -> bool:
    """-ுக்கு is a dative only when what precedes it is a word: அவன்+உக்கு, தாவீது, வீடு (வீட்ட),
    கண் (கண்ண), தேசம் (தேசத்த), ராஜா (ராஜாவ), or the postposition -இடத்து.  Rejects பெருக்கு."""
    stem = bare[:-5]
    if not stem:
        return False
    if stem.endswith("ிடத்த") or stem.endswith("ீடத்த"):
        return True
    cands = [stem + PULLI, stem + "ு"]
    if len(stem) >= 3 and stem[-2] == PULLI and stem[-3] == stem[-1]:
        cands += [stem[:-1], stem[:-2] + "ு"]
    if stem.endswith("த்த"):
        cands.append(stem[:-3] + "ம்")
    if len(stem) >= 2 and stem[-1] == "வ" and stem[-2] in "ாுூோ":
        cands.append(stem[:-1])
    return any(lex.known_bare(c) for c in cands)


def _acc_stem(bare: str, lex) -> bool:
    """-னை/-ளை/-ரை is an accusative when the stem + ் is a word (அவனை → அவன்); ஆராதனை is not."""
    return lex.known_bare(bare[:-1] + PULLI)


def _oblique_stem(bare: str, lex) -> bool:
    """வீட்டை → வீடு, ஆற்றை → ஆறு."""
    return lex.known_bare(bare[:-3] + "ு")


def _inf_iya(bare: str, lex) -> bool:
    """-இய is an infinitive when the verb also has a -ந்து form: அழிய (அழிந்து), அறிய (அறிந்து);
    the participles பெரிய, தேடிய, கூறிய have none."""
    stem = bare[:-1]
    return lex.known_bare(stem + "ந்து") or lex.known_bare(stem + "ந்த")


def _inf_ttu(bare: str, lex) -> bool:
    """-ட்ட/-த்த is the infinitive of a -ட்டு/-த்து verb when that verb has -உ and -இன forms:
    கட்ட (கட்டு, கட்டின), நடத்த (நடத்து, நடத்தின); the participles விட்ட, கொடுத்த have no -இன form."""
    return lex.known_bare(bare + "ு") and any(lex.known_bare(bare + s) for s in ("ின", "ினான்", "ினார்", "ினார்கள்"))


def _neg_aa(bare: str, lex) -> bool:
    """A word in -ஆ is a negative participle when the -ஆத form exists: வணங்கா (வணங்காத), இல்லா."""
    return lex.known_bare(bare + "த")


def _not_m_stem(bare: str, lex) -> bool:
    """False when bare + ம் is a word: a -ம் noun in a compound drops ம் and takes ஒற்று (வானப் பறவை)."""
    return not lex.known_bare(bare + "ம்")


GUARDS: dict[str, Callable] = {"dative_stem": _dative_stem, "acc_stem": _acc_stem, "oblique_stem": _oblique_stem,
                               "inf_iya": _inf_iya, "inf_ttu": _inf_ttu, "neg_aa": _neg_aa, "not_m_stem": _not_m_stem}


# --------------------------------------------------------------------------------------
# Default rule table (OV 1957 counts: doubled / bare, from dictionary/sandhi_pairs.tsv)
# --------------------------------------------------------------------------------------

NON_DATIVE_UKKU = "பெருக்கு|செருக்கு|உருக்கு|அடுக்கு|தடுக்கு|இடுக்கு|முடுக்கு|கொடுக்கு|கருக்கு|மறுக்கு|குலுக்கு|திருக்கு"
NUMERALS = ("ஒரு|இரு|இரண்டு|மூன்று|நான்கு|ஐந்து|ஆறு|ஏழு|ஒன்பது|நூறு|இருபது|முப்பது|நாற்பது|ஐம்பது|அறுபது|"
            "எழுபது|எண்பது|தொண்ணூறு")

# (id, verdict, match, exclude, guard, classes, short, name_ta, name_en, note)
DEFAULT_ROWS: list[tuple] = [
    # ---- house style about the following word (tried before every first-word row) ----
    ("no_theva", "bare", "next:^தேவ", "^தேவை|^தேவதாரு|^தேவி|^தேவான்", "", "", "தேவ- முன்",
     "தேவன், தேவ- சொற்களுக்கு முன் ஒற்று மிகாது (நடைவிதி)", "no ஒற்று before தேவன் and தேவ- words (house style)",
     "reviewer Yesu Selva Benz, Oct 2026: 10 of 10 rows (அதை தேவன், உமக்கு தேவன், இந்த தேவன்); தேவை, தேவதாரு, தேவி, தேவான் are other words"),
    ("no_clitic", "bare", "next:=கூட|=போல", "", "", "", "கூட/போல முன்", "இடைச்சொல் கூட, போல தனியே, ஒற்று மிகாமல் (நடைவிதி)",
     "no ஒற்று before the particles கூட and போல (house style)",
     "reviewer Yesu Selva Benz, Oct 2026: கூட, போல are written apart and without ஒற்று"),
    # ---- exact words ----
    ("dem", "double", "=அந்த|இந்த|எந்த|அந்தந்த|எந்தெந்த|மற்றெந்த|வேறெந்த", "", "", "", "அந்த/இந்த/எந்த",
     "சுட்டுப் பெயரடை அந்த, இந்த, எந்த", "demonstrative adjective",
     "OV K 380/2, S 249/2, P 523/6; before த the OV varies (254/97) but the reviewer (Yesu Selva Benz, Oct 2026) wants ஒற்று: 10 of 10 rows"),
    ("dem_padi", "double", "=அப்படி|இப்படி|எப்படி", "", "", "", "அப்படி/இப்படி",
     "சுட்டு வினையடை அப்படி, இப்படி, எப்படி", "demonstrative adverb", "365/11"),
    ("dem_acc", "double", "=அதை|இதை|எதை", "", "", "", "அதை/இதை",
     "சுட்டுப் பெயர், இரண்டாம் வேற்றுமை", "demonstrative pronoun, accusative", "அதை before க 306/1"),
    ("ellaa", "double", "-எல்லா|-ெல்லா", "", "", "KSP", "எல்லா",
     "எல்லா (பெயரடை)", "'all' (adjectival)", "258/21; before த 26/18"),
    ("particle_dbl", "double", "=கூட|போல|மற்ற|கிட்ட|சொந்த|கள்ள", "", "", "", "கூட/போல/மற்ற",
     "ஒற்று மிகும் இடைச்சொல்/அடை: கூட, போல, மற்ற, கிட்ட, சொந்த, கள்ள", "particles and adjectives that take ஒற்று",
     "கூட 20/1, போல 7/0, மற்ற 84/9"),
    ("east", "double", "=கிழக்கு", "", "", "", "கிழக்கு", "கிழக்கு (பெயரடை: கிழக்குக் காற்று)", "'east' as a modifier",
     "reviewer Yesu Selva Benz, Oct 2026: 3 of 3 rows (கிழக்குக் காற்று, கிழக்குத் தேசம்); the IRV mostly writes it bare"),
    ("root_reviewed", "skip", "=அணை|அரண்மனை|அழியும்வரை|உருளை|ஏழை|முளை|மூட்டை|வீணை|கல்லறை|வாக்கு|கணக்கு|குத்துவிளக்கு",
     "", "", "", "வேர்ச்சொல்", "வேர்ச்சொல்: ஈற்று ஐ / க்கு உருபு அல்ல (மதிப்பாய்வு)", "root words, not case endings (reviewed)",
     "reviewer Yesu Selva Benz, Oct 2026: the ending is part of the word; do not flag"),
    ("enru", "bare", "=என்று", "", "", "", "என்று", "என்று (மேற்கோள்)", "quotative", "0/2611"),
    ("numeral", "bare", "=" + NUMERALS, "", "", "", "ஒரு/இரண்டு…",
     "எண்ணுப்பெயர் (ஒரு, இரண்டு, மூன்று, நான்கு, ஐந்து, ஆறு, ஏழு, ஒன்பது, நூறு…)", "cardinal numerals",
     "ஒரு 0/1264, இரண்டு 0/292; பத்து and எட்டு double (-த்து, -ட்டு rows)"),
    ("pron_bare", "bare", "=நீ|அவை|இவை|எவை", "", "", "", "நீ/அவை", "நீ, அவை, இவை, எவை", "pronouns that stay bare",
     "நீ 0/926"),
    ("adj_bare", "bare", "=சகல|மகா|அநேக|அதிக|வெகு|சில|பல|நல்ல|பிற", "", "", "", "சகல/மகா/நல்ல",
     "ஒற்று மிகா அடைகள்: சகல, மகா, அநேக, அதிக, வெகு, சில, பல, நல்ல, பிற", "adjectives that stay bare",
     "மகா 0/208, சகல 2/274, நல்ல 0/70"),
    # ---- datives, -ஆக, -ஆய் ----
    ("noun_perukku", "bare", "-பெருக்கு", "", "", "", "-பெருக்கு", "பெருக்கு (பெயர்: வெள்ளப்பெருக்கு), நான்காம் வேற்றுமை அல்ல",
     "the noun பெருக்கு 'flood', not a dative", "reviewer Yesu Selva Benz, Oct 2026: வெள்ளப்பெருக்கு பூமியின்மேல் takes no ஒற்று"),
    ("dat_kalukku", "double", "-களுக்கு", "", "", "", "-களுக்கு", "நான்காம் வேற்றுமை, பன்மை -களுக்கு",
     "dative plural (also when the stem is not in the dictionary)", "reviewer Yesu Selva Benz, Oct 2026: மெய்க்காப்பாளர்களுக்குத் தலைவன்"),
    ("dat_ukku", "double", "-ுக்கு", "=" + NON_DATIVE_UKKU, "dative_stem", "", "-ுக்கு",
     "நான்காம் வேற்றுமை உருபு -கு (-ுக்கு)", "dative -ukku", "5318/112; with the stem check 4961/101"),
    ("dat_rku", "double", "-ற்கு", "=தெற்கு|மேற்கு|வடமேற்கு|தென்மேற்கு", "", "", "-ற்கு",
     "நான்காம் வேற்றுமை -ற்கு (அதற்கு, -த்திற்கு, -வதற்கு)", "dative -rku", "922/24"),
    ("dat_pron", "double", "-னக்கு|-மக்கு", "", "", "", "எனக்கு/உமக்கு",
     "நான்காம் வேற்றுமை: எனக்கு, உனக்கு, தனக்கு, உமக்கு, நமக்கு, தமக்கு", "dative of personal pronouns", "1596/13"),
    ("dat_aikku", "double", "-ைக்கு", "", "", "", "-ைக்கு", "நான்காம் வேற்றுமை -ைக்கு", "dative -aikku", "402/6"),
    ("dat_ikku", "double", "-ிக்கு", "-படிக்கு", "", "", "-ிக்கு", "நான்காம் வேற்றுமை -ிக்கு", "dative -ikku", "148/4"),
    ("amb_padikku", "ambiguous", "-படிக்கு", "", "", "", "-படிக்கு", "-படிக்கு (சாகாதபடிக்கு): ஓ.வி. நடை மாறுபடும்",
     "-padikku: the OV itself varies", "145/23"),
    ("amb_kku_noun", "ambiguous", "-ாக்கு|-ேக்கு|-ோக்கு|-ூக்கு", "", "", "", "-ாக்கு/-ேக்கு",
     "பெயர், பெயர்ச்சொல் -க்கு ஈறு (ஈசாக்கு, விளக்கு, நாக்கு)", "names and nouns in -kku, not datives", ""),
    ("ben_kaaka", "double", "-க்காக|-ற்காக", "", "", "", "-க்காக", "-ஆக (பயன்): -க்காக, -ற்காக", "benefactive -kkaaka",
     "333/24"),
    ("adv_aaka", "double", "-ாக", "", "", "", "-ஆக", "வினையடை -ஆக (-மாக)", "adverbial -aaka", "-மாக 374/16, other 1036/56"),
    ("adv_aay", "double", "-மாய்|-யாய்|-ளாய்|-னாய்|-ராய்|-றாய்|-டாய்|-ணாய்", "", "", "", "-ஆய்",
     "வினையடை -ஆய் (-மாய், -யாய், -ளாய், -னாய்…)", "adverbial -aay", "about 1460/60"),
    ("amb_aay", "ambiguous", "-ாய்", "", "", "", "-தாய்/-வாய்",
     "-ஆய்: பெயர் (வாய், தாய், காய்), முன்னிலை வினை (வந்தாய்) அல்லது வினையடை", "-aay: noun, 2nd-person verb or adverb",
     "-தாய் 67%, -வாய் 64%, -காய் 8%"),
    # ---- accusatives ----
    ("acc_kalai", "double", "-களை", "", "", "", "-களை", "இரண்டாம் வேற்றுமை -ஐ, பன்மை -களை", "accusative plural", "3713/57"),
    ("acc_nnai", "double", "-ன்னை", "", "", "", "என்னை/உன்னை", "இரண்டாம் வேற்றுமை: என்னை, உன்னை, தன்னை",
     "accusative of pronouns", "1048/18"),
    ("acc_ttai", "double", "-த்தை", "-வார்த்தை|=வித்தை|=அத்தை", "", "", "-த்தை", "இரண்டாம் வேற்றுமை -த்தை (-ம் ஈற்றுப் பெயர்)",
     "accusative -ttai", "2143/55"),
    ("acc_nlr", "double", "-னை|-ளை|-ரை", "=எத்தனை|அத்தனை|இத்தனை|தீவினை", "acc_stem", "", "-னை/-ளை/-ரை", "இரண்டாம் வேற்றுமை -னை, -ளை, -ரை",
     "accusative after ன்/ள்/ர் stems", "with the stem check 5678/97; ஆராதனை, பிள்ளை, கட்டளை fall through"),
    ("acc_glide", "double", "-ியை|-ீயை|-ையை|-ேயை|-ுவை|-ூவை|-ோவை|-ாவை", "-கிரியை", "", "", "-யை/-வை",
     "இரண்டாம் வேற்றுமை, உடம்படுமெய் -யை/-வை", "accusative with a glide", "-ியை 664/27, -ையை 786/8, -ாவை 301/4"),
    ("acc_obl", "double", "-ட்டை|-ற்றை", "", "oblique_stem", "", "-ட்டை/-ற்றை",
     "இரண்டாம் வேற்றுமை, இரட்டித்த ஒற்று (வீட்டை, ஆற்றை)", "accusative of -டு/-று nouns", "92/1"),
    ("acc_thal", "double", "-தலை", "", "acc_stem", "", "-தலை", "இரண்டாம் வேற்றுமை, தொழிற்பெயர் -தல் + ஐ (மீறுதலை)",
     "accusative of verbal nouns in -தல்", "reviewer Yesu Selva Benz, Oct 2026: மீறுதலைக் கோலினாலும்"),
    ("amb_ai", "ambiguous", "-ை", "", "", "", "-ை", "ஐகார ஈற்றுப் பெயர் (நன்மை, கொலை, கிருபை) அல்லது இரண்டாம் வேற்றுமை",
     "noun ending in ai, or accusative", "3971/786"),
    # ---- verbal participles and குற்றியலுகரம் ----
    ("vp_ttu", "double", "-த்து", "=பிலாத்து|லோத்து|தீத்து|எலிசபெத்து|ஆபத்து|வித்து|எழுத்து|சொத்து", "", "", "-த்து",
     "வன்தொடர்க் குற்றியலுகரம் -த்து (எடுத்து, குறித்து, பத்து)", "hard-cluster -ttu (verbal participle, noun)",
     "1119/94"),
    ("vp_ttu2", "double", "-ட்டு", "=சீட்டு", "", "", "-ட்டு", "வன்தொடர்க் குற்றியலுகரம் -ட்டு (விட்டு, கேட்டு, எட்டு)",
     "hard-cluster -ttu (retroflex)", "734/28"),
    ("amb_ppu", "ambiguous", "-ப்பு|-ற்று", "", "", "", "-ப்பு/-ற்று", "வன்தொடர்க் குற்றியலுகரம் -ப்பு, -ற்று: ஓ.வி. மாறுபடும்",
     "-ppu, -rru: the OV varies", "-ப்பு 207/98, -ற்று 41/12"),
    ("inf_kolla", "double", "-கொள்ள", "", "", "KP", "-கொள்ள", "செயவென் எச்சம் -கொள்ள", "infinitive -kolla",
     "24/3; before ச, த the OV varies"),
    ("adj_takka", "bare", "-தக்க", "", "", "", "-தக்க", "தக்க (அடை)", "adjective 'fitting'", "0/79"),
    ("inf_kka", "double", "-க்க", "=மிக்க", "", "", "-க்க", "செயவென் எச்சம் -க்க (இருக்க, செய்விக்க)", "infinitive -kka",
     "252/13 once -தக்க is taken out"),
    ("inf_ppa", "double", "-ம்ப|-ப்ப", "", "", "", "-ம்ப/-ப்ப", "செயவென் எச்சம் -ப (திரும்ப, அனுப்ப, தப்ப)",
     "infinitive -mpa/-ppa", "89/7"),
    ("inf_rka", "double", "-ட்க|-ற்க", "", "", "", "-ட்க/-ற்க", "செயவென் எச்சம் -க (கேட்க, நிற்க)", "infinitive -tka/-rka",
     "27/4"),
    ("inf_poka", "double", "-போக|-வர", "", "", "", "போக/வர", "செயவென் எச்சம் போக, வர (கொண்டுபோக, அழைத்துவர)",
     "infinitive poka, vara", "போக 44/0, வர 32/5"),
    # ---- endings that never take ஒற்று ----
    ("rel_kira", "bare", "-கிற", "", "", "", "-கிற", "நிகழ்கால பெயரெச்சம் -கிற", "present relative participle", "0/1376"),
    ("gen_udaiya", "bare", "-ுடைய", "", "", "", "-உடைய", "ஆறாம் வேற்றுமை -உடைய", "genitive -udaiya", "0/4764"),
    ("rel_aakiya", "bare", "-ாகிய", "", "", "", "ஆகிய", "ஆகிய (பெயரெச்சம்)", "'which is'", "0/2552"),
    ("adj_aana", "bare", "-ான", "", "not_m_stem", "", "-ஆன", "-ஆன (பெயரெச்சம்)", "adjectival -aana", "1/1529"),
    ("soc_odu", "bare", "-ோடு|-ோடே", "", "", "", "-ஓடு", "மூன்றாம் வேற்றுமை -ஓடு, -ஓடே", "sociative -odu", "0/892"),
    ("neg_aatha", "bare", "-ாத", "", "", "", "-ஆத", "எதிர்மறைப் பெயரெச்சம் -ஆத", "negative relative participle", "1/221"),
    ("inf_iya", "double", "-ிய", "=உரிய", "inf_iya", "", "-இய (எச்சம்)", "செயவென் எச்சம் -இய (அழிய, ஒழிய, அறிய)",
     "infinitive -iya (the verb has a -ந்து form)", ""),
    ("rel_iya", "bare", "-ிய", "", "not_m_stem", "", "-இய", "பெயரெச்சம், அடை -இய (பெரிய, புதிய)", "adjectival -iya",
     "15/3288; the 15 are infinitives like அறிய"),
    ("rel_ntha", "bare", "-ந்த", "", "not_m_stem", "", "-ந்த", "இறந்தகால பெயரெச்சம் -ந்த (வந்த, இருந்த)", "past relative participle -ntha",
     "34/832 without அந்த/இந்த/எந்த"),
    ("inf_ttha", "double", "-ப்படுத்த|-செலுத்த|-நடத்த", "", "", "", "-படுத்த",
     "செயவென் எச்சம் -த்த (செலுத்த, வெளிப்படுத்த): பெயரெச்சத்துடன் ஒத்த ஈறு, ஆனால் எச்சம்", "infinitive in -ttha", "24/1"),
    ("inf_ttu", "double", "-ட்ட|-த்த", "", "inf_ttu", "", "-ட்ட/-த்த (எச்சம்)",
     "செயவென் எச்சம் -ட்ட, -த்த (கட்ட, நீட்ட, நடத்த)", "infinitive of -ttu verbs (the verb has a -இன form)", ""),
    ("rel_ttha", "bare", "-த்த", "=பரிசுத்த|சுத்த|இரத்த|மெத்த", "not_m_stem", "", "-த்த", "இறந்தகால பெயரெச்சம் -த்த (கொடுத்த)",
     "past relative participle -ttha", "55/611"),
    ("rel_tta", "bare", "-ட்ட", "", "not_m_stem", "", "-ட்ட", "இறந்தகால பெயரெச்சம் -ட்ட (விட்ட, பட்ட)", "past relative participle -tta",
     "17/465"),
    ("rel_nra", "bare", "-ன்ற", "", "", "", "-ன்ற", "பெயரெச்சம் -ன்ற (என்ற, நின்ற)", "relative participle -nra", "2/27"),
    ("rel_ulla", "bare", "-ள்ள", "", "", "", "உள்ள", "உள்ள (பெயரெச்சம்)", "'having'", "29/715"),
    ("rel_ina", "bare", "-ின|-ோன|-ன்ன", "", "", "", "-இன/-ன்ன", "இறந்தகால பெயரெச்சம் -இன, -ன (முந்தின, போன, சொன்ன)",
     "past relative participle -ina/-na", "7/591"),
    ("amb_mpu", "ambiguous", "-ம்பு", "", "", "", "-ம்பு", "-ம்பு ஈற்றுப் பெயர் (இரும்புக் கோல், பாம்புக் குட்டி) தொகையில் மிகும்",
     "nouns in -mpu double in compounds", ""),
    ("vp_soft_u", "bare", "-ண்டு|-ந்து|-ன்று|-ய்து|-ன்பு", "", "", "", "-ண்டு/-ந்து",
     "மென்தொடர், இடைத்தொடர்க் குற்றியலுகரம் (கண்டு, வந்து, என்று, செய்து, முன்பு)", "soft or medial cluster + u",
     "1/5832; -ன்பு 4/270"),
    ("u_soft", "bare", "-வு|-ரு|-லு", "", "", "", "-வு/-ரு/-லு", "முற்றியலுகரம் -வு, -ரு, -லு (உத்தரவு, ஒவ்வொரு, நாலு)",
     "-u after v, r, l", "4/604"),
    ("thu", "bare", "-து", "", "", "", "-து", "-து ஈறு (அது, செய்தது, இருக்கிறது, அப்பொழுது)", "words ending in -thu",
     "6/4666"),
    ("u_single", "bare", "re:(?<!்)[கசடபற]ு", "", "", "", "வீடு/நாடு",
     "நெடில்தொடர், உயிர்த்தொடர்க் குற்றியலுகரம் (வீடு, நாடு, வெகு)", "-u after a single hard consonant", "0/620"),
    ("clitic_e_o", "bare", "-ே|-ோ", "", "", "", "-ஏ/-ஓ", "ஏகாரம், ஓகாரம் (அப்படியே, அங்கே)", "emphatic and question clitics",
     "2/6959"),
    ("neg_aa", "double", "-ா", "=ஏலா", "neg_aa", "", "-ஆ (எதிர்மறை)", "ஈறுகெட்ட எதிர்மறைப் பெயரெச்சம் (வணங்கா, இல்லா, அடங்கா)",
     "negative participle -aa (the -ஆத form exists)", ""),
    ("final_aa", "bare", "-ா", "", "", "", "-ஆ", "ஆகார ஈறு (ராஜா, மகா, யூதா)", "words ending in -aa", "21/1135"),
    # ---- documented ambiguities: the OV word or ending statistics decide ----
    ("amb_i", "ambiguous", "-ி", "", "", "", "-இ", "வினையெச்சம் (ஓடி, நோக்கி) அல்லது பெயர் (பூமி, சாட்சி)",
     "verbal participle or noun in -i", "1358/1707"),
    ("amb_yya", "ambiguous", "-ய்ய", "", "", "", "-ய்ய", "செயவென் எச்சம் -ய்ய (செய்ய): ஓ.வி. மாறுபடும்", "infinitive -yya",
     "56/9"),
    ("amb_nga_lla", "ambiguous", "-ங்க|-ல்ல|-ண|-ட", "", "", "", "-ங்க/-ல்ல",
     "செயவென் எச்சம் (வணங்க, சொல்ல) அல்லது அடை (சர்வாங்க, நல்ல)", "infinitive or adjective", ""),
    ("amb_y_zh", "ambiguous", "-ய்|-ழ்", "", "", "", "-ய்/-ழ்", "-ய், -ழ் ஈறு (போய், தமிழ்): தொகைப் பொருள் பொறுத்தது",
     "final y/zh: depends on the reading", ""),
]


# --------------------------------------------------------------------------------------
# Rule objects and parsing
# --------------------------------------------------------------------------------------

def compile_matcher(spec: str) -> tuple[frozenset, re.Pattern | None]:
    """`=a|b` exact words, `-x|y` word endings, `re:...` a regex anchored at the word end.
    Alternatives may switch kind with their own prefix: `-வார்த்தை|=வித்தை` is one ending and one word."""
    spec = spec.strip()
    if not spec:
        return frozenset(), None
    if spec.startswith("re:"):
        rx = spec[3:].strip()
        return frozenset(), re.compile(rx if rx.endswith("$") else f"(?:{rx})$")
    if spec[0] not in "=-":
        raise ValueError(f"match must start with =, - or re: ({spec!r})")
    mode = spec[0]
    words, ends = set(), []
    for alt in spec[1:].split("|"):
        alt = alt.strip()
        kind = mode
        if alt[:1] in "=-" and alt[:1]:
            kind, alt = alt[0], alt[1:].strip()
        if not alt:
            continue
        (words.add(alt) if kind == "=" else ends.append(alt))
    if not words and not ends:
        raise ValueError("empty match")
    rx = re.compile("(?:" + "|".join(re.escape(a) for a in sorted(ends, key=len, reverse=True)) + ")$") if ends else None
    return frozenset(words), rx


def compile_next(spec: str) -> tuple[frozenset, re.Pattern | None]:
    """`^b1|^b2` word beginnings, `=w` exact words (the `next:` rows)."""
    spec = spec.strip()
    if not spec:
        return frozenset(), None
    words, begins = set(), []
    for alt in spec.split("|"):
        alt = alt.strip()
        if alt.startswith("="):
            words.add(alt[1:].strip())
        elif alt.startswith("^"):
            begins.append(alt[1:].strip())
        elif alt:
            raise ValueError(f"next: alternatives start with ^ (beginning) or = (word) ({alt!r})")
    rx = re.compile("^(?:" + "|".join(re.escape(b) for b in sorted(begins, key=len, reverse=True)) + ")") if begins else None
    return frozenset(w for w in words if w), rx


@dataclass(frozen=True)
class Rule:
    id: str
    verdict: str
    match: str
    exclude: str = ""
    guard: str = ""
    classes: str = "KSTP"
    short: str = ""
    name_ta: str = ""
    name_en: str = ""
    note: str = ""
    words: frozenset = field(default=frozenset(), compare=False, repr=False)
    rx: re.Pattern | None = field(default=None, compare=False, repr=False)
    ex_words: frozenset = field(default=frozenset(), compare=False, repr=False)
    ex_rx: re.Pattern | None = field(default=None, compare=False, repr=False)
    on_next: bool = field(default=False, compare=False)        # a `next:` row: matches the following word

    @classmethod
    def make(cls, id, verdict, match, exclude="", guard="", classes="", short="", name_ta="", name_en="", note="") -> "Rule":
        if not re.fullmatch(r"[A-Za-z0-9_]+", id or ""):
            raise ValueError(f"bad id {id!r}")
        if verdict not in VERDICTS:
            raise ValueError(f"verdict must be one of {', '.join(VERDICTS)}")
        if guard and guard not in GUARDS:
            raise ValueError(f"unknown guard {guard!r} (known: {', '.join(GUARDS)})")
        classes = (classes or "KSTP").upper()
        if set(classes) - set("KSTP"):
            raise ValueError("classes may only contain K S T P")
        on_next = match.strip().startswith("next:")
        compile_ = compile_next if on_next else compile_matcher
        words, rx = compile_(match.strip()[5:] if on_next else match)
        if not words and rx is None:
            raise ValueError("empty match")
        if on_next and guard:
            raise ValueError("a next: row cannot have a guard")
        ex_words, ex_rx = compile_(exclude)
        return cls(id, verdict, match, exclude, guard, classes, short or id, name_ta, name_en, note,
                   words, rx, ex_words, ex_rx, on_next)

    def matches(self, bare: str, lex) -> bool:
        """`bare` is the first word's bare form, or for a `next:` row the following word."""
        if bare not in self.words and (self.rx is None or not self.rx.search(bare)):
            return False
        if bare in self.ex_words or (self.ex_rx is not None and self.ex_rx.search(bare)):
            return False
        return not self.guard or GUARDS[self.guard](bare, lex)

    def to_json(self) -> dict:
        return {k: getattr(self, k) for k in FIELDS}


@dataclass(frozen=True)
class RuleHit:
    rule: Rule
    verdict: str                  # the rule's verdict, or "ambiguous" before a class outside rule.classes

    def to_json(self) -> dict:
        return {"rule": self.rule.id, "rule_short": self.rule.short, "rule_ta": self.rule.name_ta,
                "rule_en": self.rule.name_en, "rule_verdict": self.verdict}


def parse_rules(text: str) -> tuple[list[Rule], list[str]]:
    """Parse the TSV.  Bad rows are skipped and reported, never fatal."""
    rules: list[Rule] = []
    errors: list[str] = []
    seen: set[str] = set()
    lines = [(i, ln) for i, ln in enumerate(text.splitlines(), 1) if ln.strip() and not ln.lstrip().startswith("#")]
    if not lines:
        return rules, ["the rule file has no rows"]
    header = lines[0][1].split("\t")
    if [h.strip() for h in header[:len(FIELDS)]] != FIELDS:
        return rules, [f"line {lines[0][0]}: the first row must be the header: {' | '.join(FIELDS)}"]
    for n, ln in lines[1:]:
        cols = next(csv.reader(io.StringIO(ln), delimiter="\t", quoting=csv.QUOTE_NONE))
        cols = [c.strip() for c in cols] + [""] * (len(FIELDS) - len(cols))
        try:
            rule = Rule.make(*cols[:len(FIELDS)])
        except (ValueError, re.error) as e:
            errors.append(f"line {n} ({cols[0] or 'no id'}): {e}")
            continue
        if rule.id in seen:
            errors.append(f"line {n}: duplicate id {rule.id}")
            continue
        seen.add(rule.id)
        rules.append(rule)
    return rules, errors


def default_tsv() -> str:
    out = ["# Tamil sandhi (புணர்ச்சி) rules for the QA app.  Edit freely; press 'Reload grammar rules' in Settings.",
           "# Columns are TAB-separated.  First matching row wins; see qa_app/tamil_grammar.py for the format.",
           "\t".join(FIELDS)]
    for row in DEFAULT_ROWS:
        out.append("\t".join(row))
    return "\n".join(out) + "\n"


DEFAULT_RULES, _default_errors = parse_rules(default_tsv())
assert not _default_errors, _default_errors


def load_rules(dict_dir: Path) -> tuple[list[Rule], list[str], Path]:
    """Read dict_dir/sandhi_rules.tsv, writing the default table there first if it is missing."""
    path = Path(dict_dir) / RULES_FILE
    if not path.exists():
        try:
            path.write_text(default_tsv(), encoding="utf-8", newline="\n")
        except OSError as e:
            return list(DEFAULT_RULES), [f"could not write {path}: {e}; using the built-in rules"], path
    rules, errors = parse_rules(path.read_text(encoding="utf-8-sig"))
    if not rules:
        return list(DEFAULT_RULES), errors + ["no usable rows; using the built-in rules"], path
    return rules, errors, path


# --------------------------------------------------------------------------------------
# Lookup and measurement
# --------------------------------------------------------------------------------------

def match_rule(bare: str, lex, rules: list[Rule]) -> Rule | None:
    for rule in rules:
        if not rule.on_next and rule.matches(bare, lex):
            return rule
    return None


def next_rule(w2: str, cls: str, rules: list[Rule]) -> RuleHit | None:
    """The first `next:` row that matches the following word, if any."""
    for rule in rules:
        if rule.on_next and rule.matches(w2, None):
            return RuleHit(rule, rule.verdict if cls in rule.classes else "ambiguous")
    return None


def sandhi_rule(bare: str, cls: str, lex, rules: list[Rule]) -> RuleHit | None:
    rule = match_rule(bare, lex, rules)
    if rule is None:
        return None
    return RuleHit(rule, rule.verdict if cls in rule.classes else "ambiguous")


def measure(lex, rules: list[Rule]) -> dict[str, dict]:
    """How the OV treats the words each rule claims: {id: {with, without, forms, by_class}} ("_none" = no rule)."""
    out: dict[str, dict] = {}
    first: dict[str, str] = {}
    for (bare, cls), (wi, wo) in lex.sandhi.items():
        if not wi and not wo:
            continue
        rid = first.get(bare)
        if rid is None:
            r = match_rule(bare, lex, rules)
            rid = first[bare] = r.id if r else "_none"
        d = out.setdefault(rid, {"with": 0, "without": 0, "forms": set(), "by_class": {k: [0, 0] for k in "KSTP"}})
        d["with"] += wi
        d["without"] += wo
        d["forms"].add(bare)
        d["by_class"][cls][0] += wi
        d["by_class"][cls][1] += wo
    for d in out.values():
        d["forms"] = len(d["forms"])
    return out


def rules_json(rules: list[Rule], stats: dict[str, dict]) -> list[dict]:
    empty = {"with": 0, "without": 0, "forms": 0, "by_class": {k: [0, 0] for k in "KSTP"}}
    return [{"order": i + 1, **r.to_json(), "ov": stats.get(r.id, empty)} for i, r in enumerate(rules)]


# --------------------------------------------------------------------------------------
# How a number written in digits is read aloud (its first letter decides the sandhi)
# --------------------------------------------------------------------------------------

_UNITS = ["", "ஒன்று", "இரண்டு", "மூன்று", "நான்கு", "ஐந்து", "ஆறு", "ஏழு", "எட்டு", "ஒன்பது"]
_TENS = ["", "பத்து", "இருபது", "முப்பது", "நாற்பது", "ஐம்பது", "அறுபது", "எழுபது", "எண்பது", "தொண்ணூறு"]
_HUNDREDS = ["", "நூறு", "இருநூறு", "முந்நூறு", "நானூறு", "ஐந்நூறு", "அறுநூறு", "எழுநூறு", "எண்ணூறு", "தொள்ளாயிரம்"]


def number_reading(digits: str) -> str | None:
    """The first spoken word of a number, enough to know its first letter: 10 -> பத்து, 22 -> இருபது (இருபத்திரண்டு),
    900 -> தொள்ளாயிரம், 10,000 -> பத்து (பத்தாயிரம்).  None when unsure (a lakh or more, or not a number)."""
    d = digits.replace(",", "")
    if not d.isdigit():
        return None
    n = int(d)
    if n <= 0 or n >= 100000:
        return None
    if n >= 1000:
        t = n // 1000
        return "ஆயிரம்" if t == 1 else number_reading(str(t))
    if n >= 100:
        return _HUNDREDS[n // 100]
    if n >= 10:
        return _TENS[n // 10]
    return _UNITS[n]

