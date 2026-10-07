"""Small offline target-text checks. No dictionaries, source judgments or writes.

Raw code-point spans always refer to the input, never its normalized copy.
Unicode Tamil §12.6 permits decomposed two-part vowels and Grantha conjuncts.
"""
from __future__ import annotations

import hashlib
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Any

import regex

from .language_packs.registry import language_code, language_name, select_pack
from .language_packs.tokens import GRAPHEME, WORD
from .usfm_verse import lift_verse

RULE_VERSION = "language-qa-7"
MAX_VERSE_CHARS = 20_000
MAX_VERSE_FINDINGS = 100
# Whole-book wordlist audit (item 49). Rarity/frequency constants below are an
# unvalidated starting point (mirroring bridge_service.py's
# _CONSISTENCY_MIN_OCCURRENCES/_MIN_RENDERINGS/_DOMINANCE_THRESHOLD) -- measure
# false-positive rate against a real, previously-reviewed Tamil book before
# trusting these defaults. Rarity or spelling similarity alone are never
# findings; both must hold together (docs/LANGUAGE_QA_PLAN.md's LQA-2 row).
MAX_WORDLIST_TERMS = 20_000
WORDLIST_MIN_LENGTH = 4
WORDLIST_RARE_MAX = 2
WORDLIST_COMMON_MIN = 6
WORDLIST_RATIO_MIN = 5
MAX_WORDLIST_FINDINGS = 200
# Every language-specific rule is data, not code: the project's rule pack
# (engine/language_packs/<pack>/, chosen through language_packs/index.json),
# whose matchers are compiled and whose examples run as tests at load
# (layered-rules Phase 3; docs/LANGUAGE_QA_PACKS.md). The Tamil code rules
# that lived here (tamil.dependent-sign, tamil.mixed-word,
# tamil.repeated-word, tamil.wordlist-variant, lexicon.*) are ta-irv rules on
# the generic kinds since 2026-09-30, with their names and version stamps
# kept (legacyVersion), so their findings and decisions are unchanged.
# The B1–B4 trigger lists this file used to hold, and the scope reasoning
# behind each, are in docs/BUILD_LOG.md's dated entries and in each rule's
# `provenance`.
#
# The non-pack rules drawn inline in the verse text (a pack rule carries its
# own `inline`). The engine alone decides: every finding carries `inline`,
# languageQa.inline filters on it, and languageQa.status lists the drawn rule
# names. The frontend only styles a finding by its category.
# Inline on human-labelled precision only (DECISIONS.md 2026-09-28):
# spacing.extra 23/23 over both rounds (2026-09-29). (lexicon.known-misspelling
# and lexicon.known-split are ta-irv pack rules now, inline in the pack.)
INLINE_RULES = frozenset({"terminology.deprecated-form", "spacing.extra"})
# Every Language QA finding carries this, and the frontend sends it back in the
# `issue` of a verse.decide call. decide_verse keys on it to keep Language QA
# decisions out of the review-progress rollup. Origin is never inferred from
# the finding id.
FINDING_SOURCE = "languageQa"
# What decide_verse records as `issue.source` when its caller names none, so a
# new non-Language-QA decision is distinguishable from a legacy row that has no
# source key (language_qa_jobs._may_concern_language_qa).
UNSPECIFIED_DECISION_SOURCE = "unspecified"


@dataclass(frozen=True)
class RuleMeta:
    """What a finding says about the rule that produced it (layered-rules
    brief, Phase 1.1). `pack` qualifies `ruleId`: "common" for the
    language-independent integrity checks, "project" for the project's own
    house-style data. (A pack rule's fields come from its pack.) `revision` is the
    rule's own version, bumped only when that rule's matching changes;
    together with PACK_VERSION it decides when an old "ignored" decision
    stops applying (Phase 1.5). `confidence` is a categorical label, not a
    calibrated probability, and is provisional until the Phase 2 benchmark
    measures each rule."""
    pack: str
    layer: str       # "pattern" | "lexicon" | "housestyle" | "integrity"
    category: str    # one of CATEGORIES
    confidence: str  # "high" | "medium" | "low"
    revision: int = 1
    # False: not run by default. Only a pack or code change turns it back on
    # (a project override may only narrow).
    enabled: bool = True


LAYERS = ("pattern", "lexicon", "housestyle", "integrity")
# highlight.ts's LANGUAGE_QA_CATEGORY_MARKS must have exactly these keys
# (test_category_marks_match_the_engine).
# "usfm": markup hygiene that does not change the text (2026-09-28 review).
# "consistency" and "grammar": the indic-qa profile packs (pa, ml, hi, or),
# corpus-attested spelling consistency and agreement leads, panel-only
# (DECISIONS 2026-10-07).
CATEGORIES = ("typo", "sandhi", "word-joining", "punctuation", "unicode", "spacing", "termbase", "name", "usfm",
              "consistency", "grammar")
# What a Tamil (ta-irv) or common-checks project lists as in scope: unchanged
# since Phase 7. A profile pack lists the categories its enabled rules use.
DEFAULT_COVERAGE = CATEGORIES[:9]
CONFIDENCES = ("high", "medium", "low")

# What Language QA checks and what it never checks (layered-rules Phase 7).
# Shown permanently in the panel, so a clean result cannot be read as a
# review. The out-of-scope items need the source text, a reference or human
# judgement, and must never be checked offline (LANGUAGE_QA_PLAN.md LQA-3).
# The Tamil strings are pending native review (BUILD_LOG Phase 7).
COVERAGE_HANDOFF = "docs/LANGUAGE_QA_REVIEW_HANDOFF.md"
_IN_SCOPE_LABELS = {
    "typo": ("Spelling and typing errors", "எழுத்துப் பிழைகள், தட்டச்சுப் பிழைகள்"),
    "sandhi": ("Sandhi (வல்லினம் doubling)", "சந்திப் பிழைகள் (வல்லினம் மிகுதல்)"),
    "word-joining": ("Words joined or split", "சொற்கள் சேர்ந்தும் பிரிந்தும் எழுதப்படுதல்"),
    "punctuation": ("Punctuation", "நிறுத்தற்குறிகள்"),
    "unicode": ("Unicode and encoding", "யூனிகோடு, குறியாக்கம்"),
    "spacing": ("Spacing", "இடைவெளி"),
    "termbase": ("Approved key terms", "அங்கீகரிக்கப்பட்ட முக்கியச் சொற்கள்"),
    "name": ("Proper-name spelling", "பெயர்ச்சொல் எழுத்துக்கூட்டல்"),
    "usfm": ("USFM markup formatting (not a text change)", "USFM குறியீட்டு வடிவமைப்பு (உரை மாற்றம் அல்ல)"),
    # Profile packs only; no Tamil label is shown for them.
    "consistency": ("Spelling consistency with the rest of the translation", ""),
    "grammar": ("Agreement and case leads attested in the corpus (listed, not drawn)", ""),
}
# A profile pack flags corpus-attested agreement pairs, so it cannot say
# agreement is never checked; it says what it does not check instead.
_PROFILE_AGREEMENT = (
    "clause-agreement", "Agreement across a whole clause",
    "", "Only word pairs the corpus attests are flagged (grammar leads); the clause needs a reviewer.", "")
_OUT_OF_SCOPE = (
    ("agreement", "Agreement: திணை, பால், எண் (subject–verb, person, gender, number)",
     "திணை, பால், எண் இயைபு (எழுவாய்–பயனிலை)",
     "Needs grammatical judgement of the whole clause; checked in the Round 2 linguistic review.",
     "முழு வாக்கியத்தின் இலக்கண மதிப்பீடு தேவை; இரண்டாம் சுற்று மொழியியல் மதிப்பாய்வில் சரிபார்க்கப்படும்."),
    ("pronoun-number-shift", "Pronoun and number shifts against the source",
     "மூலத்தோடு ஒப்பிடும்போது பதிலிடுபெயர், எண் மாற்றங்கள்",
     "Needs the source text; checked by Round 2 review and the consultant.",
     "மூல உரை தேவை; இரண்டாம் சுற்று மதிப்பாய்வும் ஆலோசகரும் சரிபார்ப்பர்."),
    ("meaning-shift", "Meaning shifts", "பொருள் மாற்றங்கள்",
     "Needs the source text and human judgement; checked by the consultant (Stage 7 shows candidates).",
     "மூல உரையும் மனித மதிப்பீடும் தேவை; ஆலோசகர் சரிபார்ப்பார்."),
    ("omission-addition", "Source words missed or added", "மூலச் சொற்கள் விடுபடுதல், சேர்க்கப்படுதல்",
     "Needs the source text; checked by the consultant (Stage 8 shows candidates).",
     "மூல உரை தேவை; ஆலோசகர் சரிபார்ப்பார்."),
    ("textual-basis", "Textual-basis differences", "மூலபாட அடிப்படை வேறுபாடுகள்",
     "Needs the source editions; checked in the consultant's textual-basis review.",
     "மூலப் பதிப்புகள் தேவை; ஆலோசகரின் மூலபாட மதிப்பாய்வில் சரிபார்க்கப்படும்."),
    ("theology", "Theological consistency", "இறையியல் ஒத்திசைவு",
     "Needs human judgement; checked by the consultant with the termbase and tW.",
     "மனித மதிப்பீடு தேவை; சொற்களஞ்சியம், tW உதவியுடன் ஆலோசகர் சரிபார்ப்பார்."),
)


def coverage(categories: Any = None) -> dict[str, Any]:
    """The structured coverage statement every Language QA status carries.
    `categories`: what the project's pack can report (profile packs); None
    keeps the Tamil/common statement (DEFAULT_COVERAGE)."""
    profile = categories is not None
    shown = [c for c in CATEGORIES if c in set(categories)] if profile else list(DEFAULT_COVERAGE)
    out_of_scope = [_PROFILE_AGREEMENT if row[0] == "agreement" and profile else row for row in _OUT_OF_SCOPE]
    return {
        "inScope": [{"category": c, "label": _IN_SCOPE_LABELS[c][0], "labelTa": _IN_SCOPE_LABELS[c][1]}
                    for c in shown],
        "outOfScope": [{"category": c, "label": label, "labelTa": label_ta, "reason": reason, "reasonTa": reason_ta}
                       for c, label, label_ta, reason, reason_ta in out_of_scope],
        "handOff": COVERAGE_HANDOFF,
        "summary": "Enabled technical checks only; no grammar or publication certification.",
    }

RULES: dict[str, RuleMeta] = {
    "unicode.nfc": RuleMeta("common", "integrity", "unicode", "low"),
    "unicode.corruption": RuleMeta("common", "integrity", "unicode", "high"),
    "unicode.private-use": RuleMeta("common", "integrity", "unicode", "medium"),
    "unicode.invisible": RuleMeta("common", "integrity", "unicode", "low", revision=2),  # 2: line breaks no longer reported
    "spacing.unusual": RuleMeta("common", "integrity", "spacing", "low", revision=2),    # same change
    "spacing.extra": RuleMeta("common", "integrity", "spacing", "medium"),
    "punctuation.repeated": RuleMeta("common", "integrity", "punctuation", "medium"),
    "punctuation.space-before": RuleMeta("common", "integrity", "punctuation", "medium"),
    # The project's approved proper nouns (house style, Phase 6.2).
    "name.minority-spelling": RuleMeta("project", "housestyle", "name", "medium"),
    "terminology.deprecated-form": RuleMeta("project", "housestyle", "termbase", "high"),
}
# The version of the rules above, which live in code. A pack rule's findings
# carry the pack's own version instead ("ta-irv@1.0.0") and the rule's own
# version as ruleRevision (or, for a rule moved out of this file, the version
# it carried here: its legacyVersion).
PACK_VERSION = RULE_VERSION
MAX_SUGGESTIONS = 5


def suggestion(text: str, source: str, rationale: str, rank: int = 1) -> dict[str, Any]:
    """One ranked fix. `source` is "rule" | "lexicon" | "termbase" |
    "majority-form" | "housestyle"; `rationale` is shown as the menu item's tooltip."""
    return {"text": text, "rank": rank, "source": source, "rationale": rationale}


def rule_fields(rule: str, suggestions: list[dict[str, Any]] | None = None, *,
                pack: Any = None, pack_rule: Any = None) -> dict[str, Any]:
    """The Phase 1.1 finding fields every producer shares (scan_text's
    add(), the terminology pass, the wordlist audit). A pack rule's fields
    come from the pack (`pack`, `pack_rule`); every other rule's from RULES.
    `rule` and `suggestedReplacement` stay as aliases for one release:
    suggestedReplacement is suggestions[0].text, or None."""
    ranked = [dict(s, rank=i) for i, s in enumerate((suggestions or [])[:MAX_SUGGESTIONS], start=1)]
    if pack_rule is not None:
        fields = {
            "layer": pack_rule.layer, "category": pack_rule.category, "confidence": pack_rule.confidence,
            "ruleId": f"{pack.name}/{pack_rule.id}", "packVersion": pack.stamp(pack_rule)[0],
            "ruleRevision": pack_rule.version, "inline": pack_rule.inline,
        }
    else:
        meta = RULES[rule]
        fields = {
            "layer": meta.layer, "category": meta.category, "confidence": meta.confidence,
            "ruleId": f"{meta.pack}/{rule}", "packVersion": PACK_VERSION,
            "ruleRevision": meta.revision, "inline": rule in INLINE_RULES,
        }
    return {"source": FINDING_SOURCE, **fields, "suggestions": ranked,
            "suggestedReplacement": ranked[0]["text"] if ranked else None}


def inline_rule_names(pack: Any = None) -> list[str]:
    """The `rule` names drawn inline: the non-pack INLINE_RULES plus every
    enabled inline rule of the pack (none: common checks only)."""
    rules = pack.rules if pack is not None else ()
    return sorted(INLINE_RULES | {r.name for r in rules if r.enabled and r.inline})


SCRIPT_NAMES = ("TAMIL", "DEVANAGARI", "BENGALI", "TELUGU", "KANNADA",
                "MALAYALAM", "GUJARATI", "GURMUKHI", "ORIYA", "SINHALA",
                "ARABIC", "HEBREW", "LATIN", "CYRILLIC", "GREEK")
LANGUAGE_SCRIPTS = {
    "ta": "TAMIL", "tam": "TAMIL", "hi": "DEVANAGARI", "hin": "DEVANAGARI",
    "mr": "DEVANAGARI", "mar": "DEVANAGARI", "ne": "DEVANAGARI", "nep": "DEVANAGARI",
    "sa": "DEVANAGARI", "san": "DEVANAGARI", "bn": "BENGALI", "ben": "BENGALI",
    "as": "BENGALI", "asm": "BENGALI", "te": "TELUGU", "tel": "TELUGU",
    "kn": "KANNADA", "kan": "KANNADA", "ml": "MALAYALAM", "mal": "MALAYALAM",
    "gu": "GUJARATI", "guj": "GUJARATI", "pa": "GURMUKHI", "pan": "GURMUKHI",
    "or": "ORIYA", "ori": "ORIYA", "od": "ORIYA", "ory": "ORIYA",
    "ur": "ARABIC", "urd": "ARABIC", "en": "LATIN", "eng": "LATIN",
}


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="surrogatepass")).hexdigest()


def stable_finding_id(book: str, chapter: str, verse: str, rule: str,
                       original: str, occurrence: int) -> str:
    """The one id formula every Language QA finding uses -- deterministic
    across re-scans of unchanged text. `occurrence` disambiguates repeats of
    the same (rule, original) pair within whatever the caller's scope is
    (one verse for scan_text's own rules; also one verse for terminology
    matches, counted independently per verse by the caller)."""
    identity = f"{book}:{chapter}:{verse}:{rule}:{original}:{occurrence}"
    return hashlib.sha1(identity.encode("utf-8", errors="surrogatepass")).hexdigest()[:20]


def detect_language(sample: str, declared: str = "") -> dict[str, Any]:
    """Which pack a project's text gets. Script evidence is not a general
    language classifier. Never guess Hindi.

    Declared metadata decides first: the declared language's registered pack
    (language_packs/index.json) runs when the text is in that language's
    script. With nothing declared, the dominant script suggests a pack only
    when exactly one language in LANGUAGE_SCRIPTS is written in it (Tamil,
    Malayalam; never Devanagari or Bengali). A declared language the text's
    script contradicts, or text in mixed scripts, gets the common checks only,
    and the message names both, so a person decides.

    The 80% dominance / 20 letter minimum are routing heuristics, not calibrated
    probabilities; expose the counts and the basis instead of a confidence score.
    """
    declared = declared.strip().lower().replace("_", "-")
    code = declared.split("-")[0]
    counts: Counter[str] = Counter()
    for char in sample[:MAX_VERSE_CHARS]:
        if unicodedata.category(char).startswith("L"):
            name = unicodedata.name(char, "")
            script = next((s for s in SCRIPT_NAMES if name.startswith(s + " ")), "OTHER")
            counts[script] += 1
    total = sum(counts.values())
    script, count = counts.most_common(1)[0] if counts else ("UNKNOWN", 0)
    dominant = total >= 20 and count / total >= .8
    expected = LANGUAGE_SCRIPTS.get(code)
    # BCP-47 explicit scripts take precedence over the usual script for a language.
    explicit = {"taml": "TAMIL", "latn": "LATIN", "deva": "DEVANAGARI",
                "arab": "ARABIC", "beng": "BENGALI", "guru": "GURMUKHI"}
    for part in declared.split("-")[1:]:
        expected = explicit.get(part, expected)
    conflict = bool(expected and dominant and script != expected)
    mixed = total >= 20 and not dominant
    if code:
        language = language_code(code)
        # The pack is for the language in its own script: ta-Latn is not ta-irv.
        pack = select_pack(language) if expected and expected == LANGUAGE_SCRIPTS.get(language) else None
    else:
        writers = {language_code(c) for c, s in LANGUAGE_SCRIPTS.items() if s == script}
        language = next(iter(writers)) if dominant and len(writers) == 1 else ""
        pack = select_pack(language) if language else None
    if conflict or mixed:
        pack = None
    if conflict:
        message = (f"The project declares {declared} ({expected.title()} script), but the text is mostly "
                   f"{script.title()} script; common checks only. Choose the language under Settings > "
                   f"Language QA.")
    elif pack:
        message = f"{language_name(language)} character rules available; grammar and spelling dictionaries are not included."
    else:
        message = "Common technical checks only; language-specific checks unavailable."
    resolved = language if pack else code or "und"
    return {
        "declared": declared, "language": resolved,
        # Display name from the registry ("Tamil"), or the code itself when the
        # registry does not know it; "" when nothing is known.
        "name": "" if resolved == "und" else language_name(resolved),
        "script": script, "scriptCounts": dict(counts), "sampleLetters": total,
        "basis": "metadata-conflict" if conflict else "mixed-script" if mixed else
                 "metadata" if code else "script-suggestion" if pack else "undetermined",
        "pack": pack or "common",
        "message": message,
    }


# What a verse shows is decided by usfm_verse.lift_verse (#91): notes lifted
# with the frontend's swallow rule, attributes and markers removed, content
# kept. The note/attribute/marker regexes that used to live here are there now.
CROSSING_LIMITATION = "candidate(s) spanning inline USFM markup omitted."
# Chapter JSON keeps a verse's USFM line structure: poetry is stored as
# "…;\n\q நான் …\n\q". In USFM a line break is whitespace, so it is never
# reported as a control or unusual-space character. It still separates words,
# as any whitespace does. Reporting it flooded IRV Psalms with 2,977 false
# findings that filled the book cap by chapter 87.
USFM_LINE_BREAKS = frozenset("\n\r")


@dataclass(frozen=True)
class LiftedVerse:
    """A verse with its inline USFM lifted out, and where every kept code
    point came from. The visible text is built by deletion only, never by
    rewriting or reordering, so a span of it that does not cross lifted
    markup is byte-identical to the raw span it maps to."""
    visible: str
    raw_index: tuple[int, ...]  # raw_index[i] is the raw offset of visible[i]

    def raw_span(self, start: int, end: int) -> tuple[int, int] | None:
        """Raw half-open span for visible[start:end], or None when that span
        crosses lifted markup (it would not be one contiguous piece of raw text)."""
        if not 0 <= start < end <= len(self.visible):
            return None
        raw_start, raw_end = self.raw_index[start], self.raw_index[end - 1] + 1
        return (raw_start, raw_end) if raw_end - raw_start == end - start else None


def lift_inline_usfm(raw: str) -> tuple[LiftedVerse | None, str]:
    """The text a reader sees, for Language QA to scan: footnotes and
    cross-references removed with their contents (the frontend's
    parseVerseNotes, including its swallow-one-space rule), then character
    markers removed but their content kept, and word attributes dropped.

    Returns (None, reason) instead of guessing when the markup cannot be lifted
    safely: unbalanced paired markers (usfm.marker_balance_issues), note markup
    outside a complete note, or a backslash that is not a marker."""
    # The lifting itself is usfm_verse.lift_verse (#91 Phase 1a), the one
    # fragment reader; this keeps Language QA's refusal: a verse whose markup
    # could not be lifted cleanly is not scanned, and the first problem is the
    # reason (all of them when markers are unbalanced, as before).
    lifted = lift_verse(raw)
    if lifted.warnings:
        unbalanced = [w for w in lifted.warnings if w.startswith("Unbalanced ")]
        reason = "; ".join(unbalanced) if unbalanced else lifted.warnings[0]
        return None, f"{reason}; verse not checked."
    return LiftedVerse(lifted.plain, lifted.raw_index), ""


def scan_text(text: str, *, book: str, chapter: str, verse: str,
              pack: Any = None, lists: dict[str, frozenset] | None = None) -> dict[str, Any]:
    """Every rule runs on the verse's visible text (lift_inline_usfm); every
    finding's start/end/originalText is exact raw code points, so
    originalText == text[start:end]. A candidate that would cross lifted
    markup is dropped and counted as a limitation. `checked` is False only
    when the verse was not scanned at all.

    The common rules always run. The project's rule pack (`pack`, a
    loader.RulePack; None: common checks only) contributes its pair-stage
    and verse-stage rules; `lists` resolves its `listRef`s (house-style
    lists)."""
    digest = text_hash(text)
    result: dict[str, Any] = {"textHash": digest, "findings": [], "limitations": [], "checked": False}
    if len(text) > MAX_VERSE_CHARS:
        result["limitations"].append("Verse exceeds 20,000 code points; not checked.")
        return result
    for index, char in enumerate(text):
        if unicodedata.category(char) == "Cs":
            # JSON can encode lone surrogates, but the UTF-8 stdio protocol cannot
            # print them. Keep the diagnostic ASCII-safe and never echo bad text.
            result["limitations"].append(
                f"Isolated Unicode surrogate U+{ord(char):04X} at code-point {index}; verse not checked.")
            return result
    lifted, reason = lift_inline_usfm(text)
    if lifted is None:
        result["limitations"].append(reason)
        return result
    result["checked"] = True
    raw, text = text, lifted.visible  # every rule below reads the visible text
    findings = result["findings"]
    occurrences: Counter[tuple[str, str]] = Counter()
    crossing = 0

    def add(rule: str, start: int, end: int, message: str, severity: str = "low",
            suggestions: list[dict[str, Any]] | None = None, *, pack_rule: Any = None,
            raw_offsets: bool = False) -> None:
        nonlocal crossing
        span = (start, end) if raw_offsets else lifted.raw_span(start, end)
        if span is None:
            crossing += 1
            return
        if len(findings) >= MAX_VERSE_FINDINGS:
            if "Finding limit reached; additional candidates omitted." not in result["limitations"]:
                result["limitations"].append("Finding limit reached; additional candidates omitted.")
            return
        raw_start, raw_end = span
        original = raw[raw_start:raw_end]
        occurrences[(rule, original)] += 1
        findings.append({
            "id": stable_finding_id(book, chapter, verse, rule, original, occurrences[(rule, original)]),
            "book": book, "chapter": chapter, "verse": verse, "rule": rule,
            "severity": severity, "start": raw_start, "end": raw_end,
            "originalText": original, "message": message, "textHash": digest,
            "ruleVersion": pack.stamp(pack_rule)[1] if pack_rule is not None else RULE_VERSION,
            "status": "review-needed",
            **rule_fields(rule, suggestions, pack=pack, pack_rule=pack_rule),
        })

    def add_candidate(candidate: Any) -> None:
        pack_rule = candidate.rule
        start, end, replacement = candidate.start, candidate.end, candidate.replacement
        if not candidate.raw and candidate.first_word_end is not None \
                and lifted.raw_span(start, end) is None and lifted.raw_span(start, candidate.first_word_end):
            # The pair straddles a poetry line (\q) or lifted markup, so the
            # pair is not one piece of raw text. The first word is: flag it,
            # with the fix confined to it (the linking consonant it needs).
            end, replacement = candidate.first_word_end, candidate.first_word_fix
        ranked = [suggestion(replacement, candidate.source, candidate.rationale)] if replacement else []
        if end == candidate.end:  # alternatives are whole-pair fixes; not for a first-word finding
            ranked += [suggestion(text, "rule", why) for text, why in candidate.alternatives]
        before = len(findings)
        add(pack_rule.name, start, end, candidate.message, pack_rule.severity, ranked,
            pack_rule=pack_rule, raw_offsets=candidate.raw)
        if candidate.confidence and len(findings) > before:
            findings[-1]["confidence"] = candidate.confidence

    lists = lists or {}

    if not unicodedata.is_normalized("NFC", text):
        # Report a small exact span rather than copying an entire verse into a finding.
        for cluster in GRAPHEME.finditer(text):
            if not unicodedata.is_normalized("NFC", cluster.group()):
                add("unicode.nfc", *cluster.span(), "Canonically equivalent non-NFC text; review project normalization policy.")
    for index, char in enumerate(text):
        if char in USFM_LINE_BREAKS:
            continue  # USFM line structure, i.e. whitespace -- not a control character in the text
        category = unicodedata.category(char)
        if char == "\ufffd":
            add("unicode.corruption", index, index + 1, "Replacement character or isolated surrogate; inspect the source encoding.", "high")
        elif category == "Co":
            add("unicode.private-use", index, index + 1, "Private-use character; verify the intended Unicode text.", "medium")
        elif category in {"Cf", "Cc", "Zl", "Zp"}:
            add("unicode.invisible", index, index + 1, f"Review {unicodedata.name(char, 'control character')} (U+{ord(char):04X}); it may be intentional.")
        elif char.isspace() and char != " ":
            add("spacing.unusual", index, index + 1, f"Review unusual space U+{ord(char):04X}; it may be intentional.")
    for match in regex.finditer(r" {2,}|^ +| +$", text):
        add("spacing.extra", *match.span(), "Repeated or edge spaces; check the intended spacing.")
    # Ellipsis and ?! are legitimate style choices, so do not flag them here.
    for match in regex.finditer(r"([,;:!?])\1+", text):
        add("punctuation.repeated", *match.span(), "Repeated punctuation; check project style.")
    for match in regex.finditer(r" +(?:[,;:!?]|\.(?!\.))", text):
        add("punctuation.space-before", *match.span(), "Space before punctuation; check project style.")
    if pack is not None:
        previous = None
        for word in WORD.finditer(text):
            if previous and text[previous.end():word.start()].isspace():
                # The pair kinds (a lexicon's known splits, reduplication), then
                # the token-context rules (வல்லினம் and the rest). A token-context
                # fix is the whole flagged span with only the linking consonant
                # changed; the raw words and whitespace are kept as written.
                for candidate in pack.pair_kind_candidates(previous, word):
                    add_candidate(candidate)
                for candidate in pack.pair_candidates(text, previous, word, lists):
                    add_candidate(candidate)
            previous = word
        for candidate in pack.verse_candidates(text, raw):
            add_candidate(candidate)
    if crossing:
        result["limitations"].append(f"{crossing} {CROSSING_LIMITATION}")
    return result


def word_occurrences(text: str) -> list[tuple[str, int, int]]:
    """NFC-normalized (word, start, end) triples; spans are raw offsets into
    `text`. Same tokenizer as tamil.repeated-word -- do not add a second one."""
    return [(unicodedata.normalize("NFC", m.group()), *m.span()) for m in WORD.finditer(text)]


def _deletion_neighbors(word: str) -> list[str]:
    return [word[:i] + word[i + 1:] for i in range(len(word))]


def wordlist_findings(book: str, counts: dict[str, int],
                       first_seen: dict[str, tuple[str, str, int, int, str, str]],
                       *, pack: Any, rule: Any) -> list[dict[str, Any]]:
    """The `wordlist-variant` kind: the within-book fallback for a pack with
    no lexicon. Pure function, no I/O. `first_seen` maps a normalized word to
    (chapter, verse, start, end, originalText, textHash) for its first
    occurrence in book-walk order. Flags a rare word only when it is also an
    edit-distance-1 near-duplicate (including a single virama insertion/
    deletion, which is just an ordinary edit-distance-1 case here) of a much
    more common word in the same book -- rarity alone and spelling similarity
    alone are never findings on their own. Thresholds are the rule's
    (`rule.params`, defaults as WORDLIST_* above).
    """
    params = rule.params
    words = [w for w in counts if len(w) >= params["minLength"]]
    word_set = set(words)
    candidates: dict[str, set[str]] = {}

    def link(a: str, b: str) -> None:
        candidates.setdefault(a, set()).add(b)
        candidates.setdefault(b, set()).add(a)

    # Same-length substitutions: two words that reduce to the same string when
    # the same relative letter is deleted from each share a deletion-neighbor
    # bucket. Every entry in one bucket has the same length by construction
    # (a word's deletion-neighbors are always exactly one codepoint shorter).
    buckets: dict[str, list[str]] = {}
    for word in words:
        for neighbor in _deletion_neighbors(word):
            buckets.setdefault(neighbor, []).append(word)
    for group in buckets.values():
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                link(a, b)
    # Insertion/deletion pairs (including a single pulli inserted or dropped):
    # a word's own deletion-neighbor that happens to itself be a real word in
    # this book is a direct edit-distance-1 match, no bucket needed.
    for word in words:
        for neighbor in _deletion_neighbors(word):
            if neighbor in word_set:
                link(word, neighbor)
    findings: list[dict[str, Any]] = []
    for rare in sorted(candidates):
        rare_count = counts[rare]
        if rare_count > params["rareMax"]:
            continue
        common_options = [w for w in candidates[rare]
                          if counts[w] >= params["commonMin"]
                          and counts[w] >= rare_count * params["ratioMin"]]
        if not common_options:
            continue
        # Deterministic tie-break: most frequent common match, then lexical order.
        common = sorted(common_options, key=lambda w: (-counts[w], w))[0]
        chapter, verse, start, end, original, text_hash = first_seen[rare]
        identity = f"{book}:{rule.name}:{rare}:{common}"
        findings.append({
            "id": hashlib.sha1(identity.encode("utf-8", errors="surrogatepass")).hexdigest()[:20],
            "book": book, "chapter": chapter, "verse": verse, "rule": rule.name,
            "severity": rule.severity, "start": start, "end": end, "originalText": original,
            "message": rule.message.format(word=rare, count=rare_count, common=common,
                                           commonCount=counts[common]),
            "textHash": text_hash, "ruleVersion": pack.stamp(rule)[1], "status": "review-needed",
            **rule_fields(rule.name, pack=pack, pack_rule=rule),
        })
        if len(findings) >= params["maxFindings"]:
            break
    return findings
