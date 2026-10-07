"""Build the bundled `ta-irv` Language QA rule pack from the IRV corpus.

    python scripts/build_ta_irv_pack.py --irv-dir "D:/Claude Lab/IRV Tamil" [--reviews DIR_OR_CSV …]

The rule DEFINITIONS below are hand-written: match shapes, messages, fixes,
and the reason for each abstain. Three inputs supply the rest, and this
script is how they were derived, so each can be rebuilt and reviewed.

1. The corpus (--irv-dir) supplies the root nouns: a -ஐ / -க்கு / -ற்கு word
   whose ending is part of the noun, shown by the corpus inflecting it
   (root_nouns). A corpus that writes a case form bare is the defect the
   rules exist to find, so "IRV mostly writes it bare" is no longer a reason
   to exclude a word (2026-09-28 review: 5 of 50 such words were real misses,
   and the per-trigger house-form stems were 9 of 9).
2. The human review (--human-labels, benchmark/human/<date>/human_labels.jsonl)
   supplies the reviewer's word verdicts -- root nouns to keep excluded, case
   forms to check -- and its confirmed findings and false alarms, which become
   each rule's examples. Reviewer verdicts are data: this script reads them,
   it never edits them.
3. Each rule's corpus examples, which run as tests every time the pack loads:
   - incorrect examples are real IRV occurrences the rule flags, preferring
     those the Round 2 / Pass 3 review also flagged;
   - correct examples are real occurrences it must not flag (the doubled
     form, a house form, a clitic);
   - where the corpus has fewer than 10 real occurrences, the rest are
     derived from a real verse by one stated edit, and their `origin` says so.

The pack is then loaded through the real loader, which runs every example.
A build that produces a failing example fails here, not in the app.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
import unicodedata
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "engine"))

import regex  # noqa: E402

from tc_ai_bridge import language_qa_benchmark as bench  # noqa: E402
from tc_ai_bridge.language_packs import loader  # noqa: E402
from tc_ai_bridge.language_qa import WORD, lift_inline_usfm, scan_text  # noqa: E402
from tc_ai_bridge.usfm_verse import lift_verse  # noqa: E402
from tc_ai_bridge.project_import import imported_verse_text, parse_scripture_file  # noqa: E402

PACK_DIR = REPO / "engine" / "language_packs" / "ta-irv"
# The kinds this builder generates. Every other rule in the pack (the
# data-driven kinds: lexicon-lookup, sign-sequence, ...) is not built here and
# is kept as it is, after the built ones, in its existing order.
BUILT_KINDS = {"token-context", "regex"}
PACK_VERSION = "1.1.0"
HUMAN_LABELS = REPO / "benchmark" / "human"  # every review round under it
HARD = ["க", "ச", "த", "ப"]
EXAMPLES = 12
DEMONSTRATIVES = ["அந்த", "இந்த", "எந்த"]
MANNER = ["அப்படி", "இப்படி", "எப்படி"]
DIRECTIONS = ["கிழக்கு", "மேற்கு", "வடக்கு", "தெற்கு"]
# After a trigger these begin a clitic or a quotative, never a doubling site.
# The 2026-09-28 reviewer: கூட, மட்டும், போல, என்று, என, எனும் are written
# apart without doubling (house style). தான் and ஆவது are not here any more
# (2026-09-29): see TAN below. ஆவது starts with a vowel, so no வல்லினம் rule
# fires on it.
CLITICS_AND_QUOTATIVES = ["கூட", "மட்டும்", "போல", "என்று", "என", "எனும்"]
# After a case form, a bare "X தான்" is always a defect, and which fix depends
# on syntax the engine cannot see. The 2026-09-29 reviewer: all six sampled were
# the reflexive PRONOUN தான் (David, Jonah, the offerer), written apart WITH
# doubling (பெட்டிக்குத் தான், தேவனுக்குத் தான், அதைத் தான்); round 1 says the
# CLITIC தான் is written fused (அதைத்தான்). So both are offered, pronoun first.
TAN = {"next": {"lexical": ["தான்"]}, "confidence": "medium",
       "message": {"en": 'தான்: பிரதிப்பெயர் என்றால் பிரித்து ஒற்றுடன்; ஒட்டுச்சொல் என்றால் ஒட்டி. '
                         '"{fix}" if தான் is the pronoun (he, himself); "{alternative}" if it is the '
                         'clitic (only, indeed).'},
       "rationale": "pronoun: separate, doubled",
       "alternatives": [{"fix": "fuse-link", "rationale": "clitic: fused"}],
       "origin": "2026-09-29 review: 6 of 6 sampled were the pronoun, wanting the separate doubled form."}
HOUSE_NAMES_IN_KKU = ["ஈசாக்கு", "ஏனோக்கு"]  # IRV_Pass3_Handoff.md §5: nominatives, not datives
# Compound-final nouns: a word ending in one is that noun, never a case form
# (ஒருமுறை, நல்வினை). Only elements that end no real accusative in the corpus:
# -மை and -தரை were measured and rejected -- உம்மை, எருசலேமை, கர்த்தரை and
# மனிதரை are accusatives (BUILD_LOG 2026-09-28, step 3; maintainer's choice).
COMPOUND_FINAL = ["முறை", "வினை", "வகை", "தொண்டை"]
# Kept excluded although the corpus or the review could suggest otherwise:
# `கை கோலில்` wants the compound கைக்கோலில், and `சு வரை` is a split word
# (சுவரை). Neither is a missing linking consonant (reviewer, GEN 47:31, PSA 48:13).
KEEP_EXCLUDED = ["கை", "வரை"]
# Accusative pronouns before a fused தான் (அதைத்தான்): the clitic rule's examples
# come from these, never from a verb whose stem ends in -ஐ (வைத்தான், அழைத்தான்).
CLITIC_ACCUSATIVES = ["அதை", "இதை", "எதை", "என்னை", "உன்னை", "அவனை", "அவளை", "அவரை", "அவர்களை",
                      "இவனை", "இவளை", "இவரை", "இவர்களை", "நம்மை", "உம்மை", "தம்மை", "எங்களை",
                      "உங்களை", "தங்களை"]
DEVA = {"next": {"prefix": ["தேவ"], "notPrefix": ["தேவை"]},
        "origin": "House style (reviewer 2026-09-28): no doubling before தேவன்/தேவ- forms "
                  "(11 of 13 'case form, no doubling here' verdicts). தேவை 'need' is still checked."}
MISSING_MESSAGE = ('Possible missing வல்லினம் at this word boundary: "{prev} {initial}..." normally takes '
                   '"{prev}{initial}் {initial}...". Verify before editing.')
MISSING_RATIONALE = '"{prev}" before a {initial}-initial word takes the linking {initial}்'
# Inline is decided by human-labelled precision only: >= 0.90 on >= 20
# findings a Tamil reviewer labelled (scripts/language_qa_benchmark.py
# --human-labels; DECISIONS.md 2026-09-28). Values: the human precision
# measured on this pack, and its sample.
INLINE = {
    # Rounds 2026-09-28 + 2026-09-29 combined; (a) >= 20 labelled at >= 0.90.
    "sandhi.vallinam.dative": {"humanPrecision": 0.9884, "labelled": 86},         # 85 TP / 1 FP
    "sandhi.vallinam.accusative": {"humanPrecision": 0.961, "labelled": 77},      # 74 TP / 3 FP
    "sandhi.vallinam.demonstrative": {"humanPrecision": 1.0, "labelled": 24},
    "sandhi.vallinam.manner-adverb": {"humanPrecision": 1.0, "labelled": 21},
    "sandhi.compound.direction": {"humanPrecision": 1.0, "labelled": 25},
    # (b) the whole collection's findings are labelled and none was wrong
    # (DECISIONS.md 2026-09-29; benchmark/human/population.json).
    "typo.suffix.dropped-tha": {"populationLabelled": "2/2"},
    "sandhi.vallinam.wrong-consonant": {"populationLabelled": "1/1"},
}
# Panel-only: typo.divine-name.vowel-drop (5 of 11 labelled) and dative-stem
# (4 of 13) until the rest of their populations are labelled; clitic.fused.


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


RAW_VERSES: list[tuple[str, str, str, str]] = []  # (book, chapter, verse, raw text), for raw-text rules


def corpus(irv_dir: Path) -> list[tuple[str, str, str, str]]:
    """(book, chapter, verse, visible text) for every liftable IRV verse."""
    verses = []
    for sfm in sorted(irv_dir.glob("*.SFM")):
        book = parse_scripture_file(sfm)
        for chapter, items in book.chapters.items():
            for verse, raw in items.items():
                text = imported_verse_text(raw)
                lifted, _ = lift_inline_usfm(text)
                if lifted is not None and "\\" not in lifted.visible:
                    verses.append((book.book_id, chapter, verse, lifted.visible))
                    RAW_VERSES.append((book.book_id, chapter, verse, text))
    return verses




def raw_note_examples(pack, rule_id: str) -> dict[str, list]:
    """A raw-text rule's examples: the word before a note plus the whole note,
    so the snippet stays well-formed markup."""
    incorrect, correct = [], []
    for book, chapter, verse, text in RAW_VERSES:
        # The fragment reader finds the notes (#91 Phase 1b); its raw range may
        # include the one space it swallows with a note, hence the strip.
        for note in lift_verse(text).notes:
            raw_note = text[note.raw_start:note.raw_end].strip()
            before = regex.search(r"\S+\s*$", text[:note.raw_start].rstrip() + " ")
            snippet = (before.group() if before else "") + raw_note
            findings = [f for f in scan_text(snippet, book="x", chapter="1", verse="1", pack=pack)["findings"]
                        if f["ruleId"] == f"ta-irv/{rule_id}"]
            item = {"text": snippet, "origin": f"{book.upper()} {chapter}:{verse}"}
            if findings and len(incorrect) < 40:
                incorrect.append({**item, "span": findings[0]["originalText"], "fix": findings[0]["suggestedReplacement"]})
            elif not findings and len(correct) < 40:
                correct.append(item)
    return {"incorrect": spread(incorrect, EXAMPLES), "correct": spread(correct, EXAMPLES)}


def root_nouns(verses) -> tuple[list[str], list[str]]:
    """Words whose ending is part of the noun itself, not a case suffix, judged
    by Tamil morphology as the corpus attests it:
    - a root in -ஐ takes -யை as its own accusative (படை -> படையை,
      மலை -> மலையை); a real accusative never takes a second one (no அதையை);
    - a root in -க்கு or -ற்கு has a locative in -க்கில் / -ற்கில் or a dative
      -க்குக்கு (கிழக்கு -> கிழக்கில், மேற்கு -> மேற்கில்); a real dative has
      neither (no எனக்கில்)."""
    tokens = {nfc(m.group()) for _, _, _, text in verses for m in WORD.finditer(text)}
    acc = sorted(t for t in tokens if t.endswith("ை") and t + "யை" in tokens)
    dat = sorted(t for t in tokens if t.endswith(("க்கு", "ற்கு"))
                 and (t[:-1] + "ில்" in tokens or t + "க்கு" in tokens))
    return acc, dat


def reviewer_words(labels: list[dict]) -> dict[str, set[str]]:
    """The reviewer's list verdicts, by rule family:
    - `accRoots` / `datRoots`: words to keep excluded -- sheet 3 ROOT_KEEP or
      HOUSE (a case form IRV writes bare as house style), and a flagged
      finding judged FP_ROOT (its first word is not a case form);
    - `accCaseForms` / `datCaseForms`: sheet 3 CASE_FORM_FLAG, real case forms
      that must be checked, whatever the corpus says."""
    out = {key: set() for key in ("accRoots", "datRoots", "accCaseForms", "datCaseForms")}
    for label in labels:
        if label.get("kind") == "word":
            family = "acc" if label["cls"].startswith("acc") else "dat" if label["cls"].startswith("dat") else None
            if family is None:
                continue  # clitic policy and demonstrative house forms are rule-level, not lists
            if label["verdict"] in {"ROOT_KEEP", "HOUSE"}:
                out[f"{family}Roots"].add(nfc(label["word"]))
            elif label["verdict"] == "CASE_FORM_FLAG":
                out[f"{family}CaseForms"].add(nfc(label["word"]))
        elif label.get("kind") == "flagged" and label.get("verdict") == "FP_ROOT":
            first = nfc(label["original"]).split()[0]
            out["accRoots" if label["rule"].endswith("accusative") else "datRoots"].add(first)
    return out


def definitions(acc_roots, dat_roots, reviewed: dict[str, set[str]]) -> list[dict]:
    acc_words = sorted((set(acc_roots) | reviewed["accRoots"] | set(KEEP_EXCLUDED)) - reviewed["accCaseForms"])
    # The four directions stay out of the dative rule: கிழக்குக் காற்று is
    # compound doubling (sandhi.compound.direction), not a dative.
    dat_words = sorted((set(dat_roots) | reviewed["datRoots"] | set(HOUSE_NAMES_IN_KKU) | set(DIRECTIONS))
                       - reviewed["datCaseForms"])
    trigger_base = "^(?:" + "|".join(DEMONSTRATIVES + MANNER) + ")$|ை$|(?:க்கு|ற்கு)$"
    clitic_base = "^(?:" + "|".join(MANNER) + ")$|ை$|(?:க்கு|ற்கு)$"
    common_abstain = [
        {"next": {"lexical": CLITICS_AND_QUOTATIVES},
         "origin": "Clitic or quotative after the trigger: never the spaced doubled form. The 2026-09-28 "
                   "reviewer: கூட/மட்டும்/போல/என்று/என/எனும் are written apart without doubling (house "
                   "style). தான் is not abstained: see the rule's தான் context (2026-09-29)."},
        DEVA,
    ]

    def missing(rule_id, title_ta, title_en, prev, provenance, confidence="medium", version=3):
        return {
            "id": rule_id, "version": version, "legacyId": "tamil.vallinam-missing",
            "category": "sandhi", "severity": "medium", "confidence": confidence, "inline": False,
            "title": {"ta": title_ta, "en": title_en},
            "message": {"en": MISSING_MESSAGE}, "rationale": MISSING_RATIONALE,
            "match": {"type": "token-context", "prev": prev, "gap": "whitespace",
                      "next": {"initial": HARD}, "link": "none"},
            "abstain": list(common_abstain), "fix": {"type": "insert-link"},
            "contexts": [TAN],
            "provenance": provenance + " v3 (2026-09-29): தான் is no longer abstained; X தான் is offered as the "
                                       "pronoun (Xத் தான்) and the clitic (Xத்தான்), medium confidence.",
        }

    review = ("2026-09-28 human review (GEN/PSA/JHN): proper nouns do not block doubling (17 of 25 needed "
              "it), so the proper-noun abstain is gone; தேவ- forms do (house style).")
    direction = missing("sandhi.compound.direction", "வல்லினம் மிகுதல் — திசைச் சொல்", "Vallinam doubling after a direction word",
                        {"lexical": DIRECTIONS},
                        "New (2026-09-28 review): the reviewer wants கிழக்குக் காற்று, கிழக்குத் தேசம் -- compound "
                        "doubling after a direction word, which is not a dative (the dative rule excludes these "
                        "four). Panel-only and medium confidence until benchmarked.", version=2)
    direction.pop("legacyId")
    return [
        missing("sandhi.vallinam.demonstrative", "வல்லினம் மிகுதல் — சுட்டு", "Vallinam doubling after a demonstrative",
                {"lexical": DEMONSTRATIVES},
                "B1 (BUILD_LOG 2026-09-22). IRV doubles after அந்த 89%, இந்த 84%, எந்த 86% of hard-initial "
                "contexts. v2: the per-trigger house-form stems (தேச-, தண்-, தரி-, திர-, தீங-, தூண-) are gone -- "
                f"the reviewer found all 9 sampled to be real errors. {review}"),
        missing("sandhi.vallinam.manner-adverb", "வல்லினம் மிகுதல் — விதம்", "Vallinam doubling after a manner adverb",
                {"lexical": MANNER},
                f"B2 (BUILD_LOG 2026-09-23). IRV doubles after அப்படி 97%, இப்படி 92%, எப்படி 87%. v2: as the "
                f"demonstrative rule, no per-trigger stems. {review}"),
        missing("sandhi.vallinam.accusative", "வல்லினம் மிகுதல் — இரண்டாம் வேற்றுமை", "Vallinam doubling after the accusative -ஐ",
                {"suffix": "ை$", "minLength": 3, "notLexical": acc_words, "notSuffixLexical": COMPOUND_FINAL},
                "Generalises B4 (five pronouns) to the -ஐ ending. notLexical "
                f"({len(acc_words)} words): (1) root nouns, {len(acc_roots)} words whose -ஐ is part of the noun, "
                "shown by their own accusative in -யை (படை -> படையை, மலை -> மலையை, கை -> கையை); a real "
                "accusative never takes a second (no அதையை); (2) the 2026-09-28 reviewer's root nouns and "
                f"house forms ({len(reviewed['accRoots'])}); minus (3) the {len(reviewed['accCaseForms'])} "
                "words the reviewer confirmed are real accusatives that need doubling (அசுத்தமானவைகளை, "
                "யெகோவாவை, ஜீவனை …). notSuffixLexical: compound-final nouns (அநேகமுறை, தீவினை). v2 drops "
                f"the corpus bare-majority list: a corpus writing a case form bare is the defect. {review}"),
        missing("sandhi.vallinam.dative", "வல்லினம் மிகுதல் — நான்காம் வேற்றுமை", "Vallinam doubling after the dative -க்கு / -ற்கு",
                {"suffix": "(?:க்கு|ற்கு)$", "minLength": 4, "notLexical": dat_words},
                "Generalises B3 (three datives) to the -க்கு ending, and in v2 to -ற்கு (the reviewer "
                "confirmed 17 of 17 sampled -ற்கு datives need doubling: அதற்குச் சித்னா). notLexical: (1) root "
                f"nouns and names in -க்கு / -ற்கு, {len(dat_roots)} words shown by a locative in -க்கில் / "
                "-ற்கில் or a dative -க்குக்கு (கிழக்கு -> கிழக்கில், விளக்கு, வழக்கு); a real dative has "
                "neither (no எனக்கில்); (2) the reviewer's root nouns; (3) the names IRV_Pass3_Handoff.md §5 "
                "lists as nominatives; (4) the four directions, which sandhi.compound.direction covers; minus "
                f"(5) the {len(reviewed['datCaseForms'])} words the reviewer confirmed are real datives "
                f"(சபைக்கு, யோபுக்கு …). v2 drops the corpus bare-majority list. {review}"),
        {
            "id": "sandhi.vallinam.wrong-consonant", "version": 3,
            "category": "sandhi", "severity": "medium", "confidence": "medium", "inline": False,
            "title": {"ta": "வல்லினம் — தவறான மெய்", "en": "Wrong linking consonant"},
            "message": {"en": 'The linking "{link}்" does not match the following "{initial}...": '
                              '"{prev}{initial}் {initial}..." is expected. Verify before editing.'},
            "rationale": 'The linking consonant repeats the next word\'s first consonant ({initial})',
            "match": {"type": "token-context", "gap": "whitespace", "link": "mismatch",
                      "prev": {"regex": trigger_base, "notLexical": sorted(set(acc_words) | set(dat_words))},
                      "next": {"initial": HARD}},
            "abstain": list(common_abstain), "fix": {"type": "replace-link"},
            "provenance": "Only after a base the வல்லினம் rules cover (a demonstrative, a manner adverb, "
                          "-ஐ, -க்கு or -ற்கு). In IRV every other 'wrong link' is a name ending in a "
                          "consonant (காத் Gad, மோவாப் Moab), which this must never touch. v2: -ற்கு, and the "
                          "exception lists of the rules it follows.",
        },
        direction,
        {
            "id": "sandhi.clitic.fused", "version": 3, "enabled": False,
            "category": "word-joining", "severity": "low", "confidence": "low", "inline": False,
            "title": {"ta": "இடைச்சொல் — சேர்த்து எழுதுதல்", "en": "Clitic written apart"},
            "message": {"en": '"{word} {next}" is normally written as one word, "{fix}". Verify before editing.'},
            "rationale": "தான் is a clitic: written fused, with the linking consonant",
            "match": {"type": "token-context", "gap": "whitespace", "link": "any",
                      "prev": {"regex": clitic_base, "notLexical": sorted(set(acc_words) | set(dat_words))},
                      "next": {"lexical": ["தான்"]}},
            "abstain": [], "fix": {"type": "fuse-link"},
            "provenance": "DISABLED v3 (2026-09-29 review): all six findings were தான் the reflexive PRONOUN, "
                          "not the clitic, which the reviewer writes apart with doubling; the வல்லினம் rules "
                          "now offer both forms (their தான் context). "
                          "Panel-only, low confidence. v2 (2026-09-28 review): தான் and ஆவது are written "
                          "fused, and கூட is written apart, so கூட is no longer matched. ஆவது fuses by "
                          "vowel sandhi (யாராவது), not by a linking consonant, so it has no fix this rule "
                          "could offer and is not matched either. Only after a manner adverb, -ஐ, -க்கு or "
                          "-ற்கு; never the spaced form அதைத் தான்.",
        },
        {
            "id": "typo.divine-name.vowel-drop", "version": 1,
            "category": "typo", "severity": "high", "confidence": "high", "inline": False,
            "title": {"ta": "யெகோவா — நெடில் விடுபட்டது", "en": "Divine name: dropped long vowel"},
            "message": {"en": '"{span}" drops the ா of யெகோவா; "{fix}" is expected. Verify before editing.'},
            "rationale": "யெகோவா keeps its long ா before the case ending",
            "match": {"type": "regex", "on": "visible", "pattern": "யெகோவவ"},
            "fix": {"type": "replace", "text": "யெகோவாவ"},
            "provenance": "Known IRV defect shape carried over from 14 books of Round 2. IRV: யெகோவவ 11, யெகோவாவ 3,488.",
        },
        {
            "id": "typo.divine-name.dative-stem", "version": 1,
            "category": "typo", "severity": "high", "confidence": "high", "inline": False,
            "title": {"ta": "யெகோவா — நான்காம் வேற்றுமை அடி", "en": "Divine name: dative stem"},
            "message": {"en": '"{span}" builds the dative without the -வு- stem; "{fix}" is expected. Verify before editing.'},
            "rationale": "The dative of யெகோவா is யெகோவாவுக்கு",
            "match": {"type": "regex", "on": "visible", "pattern": "யெகோவாக்க"},
            "fix": {"type": "replace", "text": "யெகோவாவுக்க"},
            "provenance": "Known IRV defect shape (Round 2). IRV: யெகோவாக்க 13.",
        },
        {
            "id": "typo.suffix.dropped-tha", "version": 1,
            "category": "typo", "severity": "medium", "confidence": "high", "inline": False,
            "title": {"ta": "-வதற்கு — த விடுபட்டது", "en": "Dropped த in -வதற்கு"},
            "message": {"en": '"{span}" drops the த of -வதற்கு; "{fix}" is expected. Verify before editing.'},
            "rationale": "The purposive verbal noun is -வதற்கு",
            # A verb stem (two or more letters) before -வற்கு, then the end of the
            # word. அவற்கு/இவற்கு-type pronoun forms have a one-letter stem.
            "match": {"type": "regex", "on": "visible",
                      "pattern": r"(?<![\p{L}\p{M}])(?P<span>(?P<stem>\p{L}[\p{L}\p{M}]{2,}?)வற்கு)(?![\p{L}\p{M}])"},
            "fix": {"type": "expand", "template": r"\g<stem>வதற்கு"},
            "provenance": "Known IRV defect shape (Round 2). IRV: 2 real occurrences (நடத்துவற்கு, செய்வற்கு), "
                          "each with its -வதற்கு form attested; -வதற்கு 328.",
        },
        {
            "id": "integrity.digits-in-text", "version": 1, "enabled": False,
            "category": "typo", "layer": "integrity", "severity": "low", "confidence": "low", "inline": False,
            "title": {"ta": "எண்கள் வசனத்தில்", "en": "Digits in verse text"},
            "message": {"en": 'Digits "{span}" in verse text; check the project\'s numeral policy.'},
            "rationale": "Numeral policy",
            "match": {"type": "regex", "on": "visible", "pattern": r"\p{Nd}+"},
            "provenance": "DISABLED: the Pass 3 reviewers confirmed digits in verse text are IRV house form "
                          "(IRV_Pass3_Handoff.md §5), and IRV has 2,423 of them; enabled, it would also crowd "
                          "the book finding cap. A project override cannot enable it (overrides only narrow): "
                          "enabling is a pack change for the maintainer.",
        },
        {
            "id": "integrity.space-before-note-end", "version": 2,
            "category": "usfm", "severity": "low", "confidence": "medium", "inline": False,
            "title": {"ta": "குறிப்பு முடிவுக்கு முன் இடைவெளி (குறியீட்டு வடிவமைப்பு)",
                      "en": "Markup: space before a note's end marker"},
            "message": {"en": "Markup formatting, not a text error: a space before the closing note marker. "
                              "Removing it does not change the verse text."},
            "rationale": "No space before \\f* or \\x*",
            "match": {"type": "regex", "on": "raw", "pattern": r"(?P<span> +)\\[fx]\*"},
            "fix": {"type": "replace", "text": ""},
            "provenance": "Layered-rules brief §3.4 (integrity.space-before-footnote-end), extended to \\x*. "
                          "Matched on the raw text, since notes are lifted out of the visible text. v2 "
                          "(2026-09-28 review, 0 of 15 as a text error): USFM hygiene, so category usfm and "
                          "severity low.",
        },
    ]


def window(text: str, start: int, end: int, words: int = 2) -> str:
    """The span with up to `words` words either side: a real snippet of the verse."""
    tokens = list(regex.finditer(r"\S+", text))
    first = next(i for i, t in enumerate(tokens) if t.end() > start)
    last = next(i for i, t in enumerate(tokens) if t.end() >= end)
    lo, hi = max(0, first - words), min(len(tokens) - 1, last + words)
    return text[tokens[lo].start():tokens[hi].end()]


def spread(items: list, count: int) -> list:
    if len(items) <= count:
        return items
    step = len(items) / count
    return [items[int(i * step)] for i in range(count)]


def reviewed_places(paths: list[str]) -> set[tuple[str, str, str]]:
    if not paths:
        return set()
    files = []
    for value in paths:
        path = Path(value)
        files.extend(sorted(path.glob("*_Issues.csv")) if path.is_dir() else [path])
    return {(r.book, r.chapter, r.verse) for r in bench.load_review_rows(files) if r.label in {"positive", "maybe"}}


# Places the brief names as a rule's examples; included whenever the rule flags them.
REQUIRED_PLACES = {"sandhi.compound.direction": {("psa", "48", "7"), ("psa", "78", "26"), ("gen", "29", "1")}}


def choose_examples(pack, verses, reviewed) -> dict[str, dict[str, list]]:
    flagged = collections.defaultdict(list)
    for book, chapter, verse, text in verses:
        for f in scan_text(text, book=book, chapter=chapter, verse=verse, pack=pack)["findings"]:
            if f["ruleId"].startswith("ta-irv/"):
                flagged[f["ruleId"].split("/", 1)[1]].append((book, chapter, verse, text, f))
    out = {}
    for rule in pack.rules:
        if not rule.enabled:
            out[rule.id] = {"incorrect": [], "correct": []}
            continue
        hits = flagged.get(rule.id, [])
        hits.sort(key=lambda h: ((h[0], h[1], h[2]) not in reviewed, h[0], int(h[1]), h[2]))
        required = [h for h in hits if (h[0], h[1], h[2]) in REQUIRED_PLACES.get(rule.id, set())]
        chosen = required + [h for h in spread([h for h in hits if (h[0], h[1], h[2]) in reviewed], EXAMPLES // 2)
                             if h not in required]
        chosen += [h for h in spread(hits, EXAMPLES) if h not in chosen][:max(0, EXAMPLES - len(chosen))]
        incorrect = []
        for book, chapter, verse, text, finding in chosen:
            snippet = window(text, finding["start"], finding["end"])
            incorrect.append({"text": snippet, "span": finding["originalText"],
                              "fix": finding["suggestedReplacement"],
                              "origin": f"{book.upper()} {chapter}:{verse}"
                                        + (" (also in the review reports)" if (book, chapter, verse) in reviewed else "")})
        out[rule.id] = {"incorrect": incorrect, "correct": correct_examples(rule, verses, flagged)}
    return out


CLITIC_FUSED = regex.compile(r"(?<![\p{L}\p{M}])(?:" + "|".join(MANNER + CLITIC_ACCUSATIVES)
                             + r"|\p{L}[\p{L}\p{M}]*(?:க்கு|ற்கு))"
                             r"த்தான்(?![\p{L}\p{M}])")


def correct_examples(rule, verses, flagged) -> list[dict]:
    """Real snippets the rule must not flag."""
    found = []
    if rule.match_type == "token-context":
        if rule.id == "sandhi.clitic.fused":
            for book, chapter, verse, text in verses:
                for m in CLITIC_FUSED.finditer(text):
                    found.append(("fused clitic", book, chapter, verse, window(text, m.start(), m.end(), 1)))
                for m in regex.finditer(r"\p{L}[\p{L}\p{M}]*ஓடு கூட(?![\p{L}\p{M}])", text):
                    found.append(("comitative கூட", book, chapter, verse, window(text, m.start(), m.end(), 1)))
        else:
            for book, chapter, verse, text in verses:
                words = list(WORD.finditer(text))
                for a, b in zip(words, words[1:]):
                    if not text[a.end():b.start()].isspace():
                        continue
                    prev, nxt = nfc(a.group()), nfc(b.group())
                    base, link = loader._split(prev)
                    if rule.prev is not None and not rule.prev.matches(base, {}):
                        continue
                    shape = None
                    if nxt[:1] in HARD and link == nxt[:1] and rule.link != "any":
                        shape = "correctly linked"
                    elif nxt[:1] in HARD and link is None and any(
                            (x.prev is None or x.prev.matches(base, {})) and (x.next is None or x.next.matches(nxt, {}))
                            for x in rule.abstain if x.origin.startswith(("House style", "Clitic"))):
                        shape = "house form"
                    if shape:
                        found.append((shape, book, chapter, verse, window(text, a.start(), b.end(), 1)))
    else:
        corrected = {"typo.divine-name.vowel-drop": r"யெகோவாவ[\p{L}\p{M}]*",
                     "typo.divine-name.dative-stem": r"யெகோவாவுக்கு",
                     "typo.suffix.dropped-tha": r"\p{L}[\p{L}\p{M}]*வதற்கு",
                     "integrity.space-before-note-end": None}[rule.id]
        if corrected:
            for book, chapter, verse, text in verses:
                m = regex.search(corrected, text)
                if m:
                    found.append(("correct form", book, chapter, verse, window(text, m.start(), m.end(), 1)))
        else:
            return []  # raw-text rules: raw_note_examples
    by_shape = collections.defaultdict(list)
    for item in found:
        by_shape[item[0]].append(item)
    # Round-robin over shapes (correctly linked, house form, …) so each is
    # represented and the total reaches EXAMPLES where the corpus allows.
    queues = [spread(items, EXAMPLES * 2) for _, items in sorted(by_shape.items())]
    picked = []
    while any(queues) and len(picked) < EXAMPLES * 2:
        for queue in queues:
            if queue:
                picked.append(queue.pop(0))
    examples = []
    seen = set()
    for shape, book, chapter, verse, snippet in picked:
        if snippet in seen:
            continue
        seen.add(snippet)
        origin = f"{book.upper()} {chapter}:{verse} ({shape})" if book else f"constructed ({shape})"
        examples.append({"text": snippet, "origin": origin})
    return examples[:max(EXAMPLES, 10)]


def top_up(examples: dict[str, list], rule_id: str, verses) -> None:
    """Where the corpus has fewer than 10 real incorrect occurrences, derive
    the rest from real verses by one stated edit."""
    incorrect = examples["incorrect"]
    if len(incorrect) >= 10:
        return
    edits = {
        "typo.suffix.dropped-tha": (r"(\p{L}[\p{L}\p{M}]{2,}?)வதற்கு(?![\p{L}\p{M}])", r"\1வற்கு", "த removed from -வதற்கு"),
        "sandhi.vallinam.wrong-consonant": None,
        "sandhi.clitic.fused": ("(?<![\\p{L}\\p{M}])(" + "|".join(MANNER + CLITIC_ACCUSATIVES)
                                + "|\\p{L}[\\p{L}\\p{M}]*(?:க்கு|ற்கு))த்தான்(?![\\p{L}\\p{M}])",
                                r"\1த் தான்", "fused தான் written apart"),
        "sandhi.compound.direction": ("(?<![\\p{L}\\p{M}])(" + "|".join(DIRECTIONS) + ")([கசதப])் ",
                                      r"\1 ", "linking consonant after the direction word removed"),
        "typo.divine-name.vowel-drop": (r"யெகோவாவ", "யெகோவவ", "ா removed"),
        "typo.divine-name.dative-stem": (r"யெகோவாவுக்க", "யெகோவாக்க", "வு removed"),
        "integrity.space-before-note-end": None,
    }.get(rule_id)
    if rule_id == "sandhi.vallinam.wrong-consonant":
        swap = {"க": "ச", "ச": "த", "த": "ப", "ப": "க"}
        pattern = regex.compile(r"(?<![\p{L}\p{M}])(அந்த|இந்த|எந்த|அப்படி|இப்படி|எப்படி|\p{L}[\p{L}\p{M}]*ை)([கசதப])் ([கசதப])")
        for book, chapter, verse, text in verses:
            m = pattern.search(text)
            if not m or m.group(2) != m.group(3):
                continue
            following = text[m.start(3):]
            if following.startswith("தேவ") and not following.startswith("தேவை"):
                continue  # before தேவ- the pack abstains (house style), so it would not be flagged
            wrong =f"{m.group(1)}{swap[m.group(2)]}் "
            snippet = window(text, m.start(), m.end() + 4, 1).replace(m.group(0), wrong + m.group(3), 1)
            incorrect.append({"text": snippet, "span": None, "fix": None,
                              "origin": f"derived from {book.upper()} {chapter}:{verse} (linking consonant changed)"})
            if len(incorrect) >= 10:
                break
        return
    if rule_id == "integrity.space-before-note-end":
        return
    if edits is None:
        return
    pattern, repl, how = edits
    for book, chapter, verse, text in verses:
        m = regex.search(pattern, text)
        if not m:
            continue
        snippet = window(text, m.start(), m.end(), 1)
        derived = regex.sub(pattern, repl, snippet, count=1)
        if derived == snippet or any(e["text"] == derived for e in incorrect):
            continue
        incorrect.append({"text": derived, "span": None, "fix": None,
                          "origin": f"derived from {book.upper()} {chapter}:{verse} ({how})"})
        if len(incorrect) >= 10:
            break


def fill_spans(pack, rule_id, examples) -> None:
    """Derived examples get their span and fix from the rule itself, then run
    through the loader's example check like every other example."""
    for example in examples["incorrect"]:
        if example.get("span") is None:
            findings = [f for f in scan_text(example["text"], book="x", chapter="1", verse="1", pack=pack)["findings"]
                        if f["ruleId"] == f"ta-irv/{rule_id}"]
            if not findings:
                raise SystemExit(f"{rule_id}: derived example not flagged: {example}")
            example["span"] = findings[0]["originalText"]
            example["fix"] = findings[0]["suggestedReplacement"]


def _squeezed(text: str) -> str:
    return regex.sub(r"\s+", " ", nfc(text)).strip()


def human_examples(pack, fixture_dirs: list[Path]) -> tuple[dict[str, dict[str, list]], list[str], list[str]]:
    """The reviewer's verdicts as rule examples, so every pack load re-checks
    the review: a confirmed finding (`expect`) is an incorrect example of the
    rule that now finds it, a confirmed false alarm (`negative`) a correct
    example of its rule. Returns (examples by rule, problems, notes). A
    confirmed finding no rule finds, or a false alarm its rule still raises,
    is a problem: the build fails on it. Noted, not failed: a confirmed
    finding whose right form differs from the rule's fix (TP_OTHER_FIX) --
    still an example that it is flagged, with no fix asserted -- and a false
    alarm the rule still raises, a residual the benchmark counts (the
    accusative's two before a name: names block doubling in 8 of 25 reviewed
    cases, not 17, so no name abstain exists to write)."""
    out: dict[str, dict[str, list]] = collections.defaultdict(lambda: {"incorrect": [], "correct": []})
    problems: list[str] = []
    notes: list[str] = []
    lines = [(folder.parent.name, line) for folder in fixture_dirs
             for line in (folder / "sandhi.jsonl").read_text(encoding="utf-8").splitlines()]
    for round_name, line in lines:
        example = json.loads(line)
        # Each example names the review round it came from (the folder's date).
        origin = regex.sub(r"\(human review (?:round \d+|2026)", f"(human review {round_name}", example["origin"], count=1)
        findings = scan_text(example["text"], book="x", chapter="1", verse="1", pack=pack)["findings"]
        for expected in example["expect"]:
            span = _squeezed(expected["span"])
            hits = [f for f in findings if f["ruleId"].startswith("ta-irv/sandhi.")
                    and (_squeezed(f["originalText"]) in span or span in _squeezed(f["originalText"]))]
            if not hits:
                problems.append(f"{origin}: the reviewer confirmed {expected['span']!r}; no rule finds it")
                continue
            hit = hits[0]
            rule_id = hit["ruleId"].split("/", 1)[1]
            fix = hit["suggestedReplacement"]
            if expected.get("fix") and _squeezed(fix or "") != _squeezed(expected["fix"]):
                notes.append(f"{origin}: {rule_id} suggests {fix!r}; the reviewer's form is {expected['fix']!r}")
                fix = None
            # Two words either side are all a pair rule reads: a snippet keeps the
            # pack load cheap, and the load re-checks that it still gives this span.
            out[rule_id]["incorrect"].append({"text": window(example["text"], hit["start"], hit["end"]),
                                              "span": hit["originalText"], "fix": fix, "origin": origin})
        for negative in example.get("negative", []):
            rule_id = negative["ruleId"].split("/", 1)[1]
            if pack.by_id(rule_id) is None:
                continue  # an in-code rule (tamil.repeated-word): its test is the labelled fixture
            span = _squeezed(negative["span"])
            raised = [f for f in findings if f["ruleId"] == negative["ruleId"]
                      and (_squeezed(f["originalText"]) in span or span in _squeezed(f["originalText"]))]
            if raised:
                notes.append(f"{origin}: residual false alarm {negative['span']!r} still raised by {rule_id}")
                continue
            where = regex.search(r"\s+".join(regex.escape(t) for t in negative["span"].split()), example["text"])
            text = window(example["text"], where.start(), where.end()) if where else example["text"]
            out[rule_id]["correct"].append({"text": text, "origin": f"{origin}; {negative['reason']}"})
    return out, problems, notes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--irv-dir", required=True, type=Path)
    parser.add_argument("--reviews", nargs="*", default=[])
    parser.add_argument("--human-labels", type=Path, default=HUMAN_LABELS)
    args = parser.parse_args()
    verses = corpus(args.irv_dir)
    print(f"{len(verses)} verses", file=sys.stderr)
    labels, _ = bench.load_human_rounds(args.human_labels)
    reviewed_words = reviewer_words(labels)
    print({k: len(v) for k, v in reviewed_words.items()}, file=sys.stderr)
    rules = definitions(*root_nouns(verses), reviewed_words)
    for rule in rules:
        rule["inline"] = rule["id"] in INLINE
    previous = json.loads((PACK_DIR / "pack.json").read_text(encoding="utf-8"))
    kept = [entry for entry in previous["rules"]
            if json.loads((PACK_DIR / entry).read_text(encoding="utf-8"))["match"]["type"] not in BUILT_KINDS]
    meta = {"pack": "ta-irv", "version": PACK_VERSION, "language": "ta", "script": "Taml",
            "description": "Tamil IRV rule pack: sandhi (வல்லினம்) shape rules and known IRV defect shapes. "
                           "Built by scripts/build_ta_irv_pack.py from the IRV corpus and the 2026-09-28 "
                           "human review; see docs/LANGUAGE_QA_RULE_PACK.md.",
            # indicQa: the OV dictionary layer (indic_qa_tamil.py), whose files
            # come from scripts/sync_indic_qa.py and build_indic_qa_packs.py.
            **{k: previous[k] for k in ("lexicon", "confusion", "indicQa") if k in previous},
            "rules": [f"rules/{rule['id']}.json" for rule in rules]}
    pack = loader._build(meta, rules)  # no examples yet: they are chosen with this pack
    examples = choose_examples(pack, verses, reviewed_places(args.reviews))
    reviewer, problems, notes = human_examples(
        pack, [file.parent / "labelled" for file in bench.human_label_files(args.human_labels)])
    for note in notes:
        print(f"REVIEW (noted): {note}", file=sys.stderr)
    if problems:
        for problem in problems:
            print(f"REVIEW: {problem}", file=sys.stderr)
        raise SystemExit(f"{len(problems)} reviewer verdicts the pack does not satisfy")
    for rule in rules:
        rule_examples = examples[rule["id"]]
        if rule["match"].get("on") == "raw" and rule.get("enabled", True):
            rule_examples = examples[rule["id"]] = raw_note_examples(pack, rule["id"])
        if rule.get("enabled", True):  # a disabled rule runs no examples, so gets none
            top_up(rule_examples, rule["id"], verses)
            fill_spans(pack, rule["id"], rule_examples)
        for kind in ("incorrect", "correct"):
            rule_examples[kind] += reviewer[rule["id"]][kind] if rule["id"] in reviewer else []
        rule["examples"] = rule_examples
        print(f"{rule['id']}: {len(rule_examples['incorrect'])} incorrect, {len(rule_examples['correct'])} correct"
              f" ({len(reviewer[rule['id']]['incorrect']) if rule['id'] in reviewer else 0} and "
              f"{len(reviewer[rule['id']]['correct']) if rule['id'] in reviewer else 0} from the review)",
              file=sys.stderr)
    (PACK_DIR / "rules").mkdir(parents=True, exist_ok=True)
    for old in (PACK_DIR / "rules").glob("*.json"):
        if f"rules/{old.name}" not in kept:
            old.unlink()
    for rule in rules:
        (PACK_DIR / "rules" / f"{rule['id']}.json").write_text(
            json.dumps(rule, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    meta["rules"] += kept
    (PACK_DIR / "pack.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1) + "\n",
                                        encoding="utf-8", newline="\n")
    loader.default_pack.cache_clear()
    loader.load_pack("ta-irv")  # runs every example; raises on the first failure
    print("pack built and every example passes", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
