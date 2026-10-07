"""The ta-irv pack's indic-qa layer: indic-qa's Tamil checker, beside the pack's own rules.

ta-irv's JSON rules (sandhi shapes, known defects, the lexicon audit) stay as
they are. A pack.json `indicQa` block adds a second, book-stage layer: the
vendored checker's Tamil profile (qa_app/langs/ta.py, qa_app/tamil_grammar.py)
over the BSI 1957 Old Version dictionary in ta-irv/dictionary/. What it adds,
and ta-irv has no rule for:

- a reference lexicon that is not the text under review (94k OV forms), so a
  word can be near a known word without being common in the IRV itself;
- near-miss suggestions ranked by Tamil typing slips (ல/ள/ழ, ன/ண/ந, ர/ற,
  vowel length, புள்ளி, transposition) and the reviewer's corrections.tsv;
- words that split into two known words;
- the 71-row sandhi grammar (tamil_grammar.py) with the OV's own doubling
  statistics, the IRV veto, and "remove this ஒற்று" leads, not only "add";
- an ஒற்று with nothing to join (dangling) and with the wrong letter;
- three warnings: no space after , ; : ! ?, a word that starts with a
  consonant + புள்ளி, a word that starts with a letter no Tamil word starts with;
- the same checks over section headings and footnote text, which the JSON
  rules never read.

indic-qa's Tamil profile has no RULES catalogue (it predates them), so this
module is that catalogue, in the shape the profile packs use: rule id ->
(group, label, inline upstream, on by default). The pack's Bridge view of each
rule (category, severity, revision, enabled) is ta-irv/indic_qa_rules.json.

What it never adds, because Bridge already reports it (CLAUDE.md: a finding
reported twice is two decisions for one problem): double spaces, zero-width
characters, repeated punctuation, a space before punctuation and the known
யெகோவா/-வதற்கு defects *in verse text*. Those checks still run in headings and
footnotes, which nothing else reads. Digits, repeated words and a space before
a footnote's end are off: the 2026-09-28 review rejected all three.

Bridge policy, not indic-qa's: a word missing from the OV is reported only
when the IRV itself uses it at most RARE_MAX times (a frequent form is house
practice, as the dictionary's own REPORT.md says), and only as a near miss when
its best suggestion is a typing slip or a reviewed correction. Every other
unknown word is `indicqa.lex.unknown`, which ships disabled: on the IRV it is
2,000+ per book, mostly valid modern Tamil the 1957 OV does not have.
"""
from __future__ import annotations

from typing import Any, NamedTuple

PROFILE = "ta"
# pack.json key, and the file of Bridge's per-rule view.
LAYER_KEY = "indicQa"
RULES_FILE = "indic_qa_rules.json"

# How often the IRV may use an out-of-OV word before it counts as house practice.
RARE_MAX = 2
# Suggestion classes (indic-qa's bd.edit_class, plus its "reviewed"/"learned")
# that make an unknown word a probable typing slip.
SLIP_CLASSES = frozenset({"reviewed", "learned", "consonant_confusable", "vowel_length", "pulli", "transpose"})

# rule id -> (group, label, inline upstream, on by default). The group picks
# the default Bridge entry (indic_qa_adapter.default_entry).
RULES: dict[str, tuple[str, str, bool, bool]] = {
    "indicqa.lex.near-miss": ("Lexicon", "Not in the OV dictionary, and one typing slip from a known word", False, True),
    "indicqa.lex.compound": ("Lexicon", "Not in the OV dictionary, but splits into two known words", False, True),
    "indicqa.lex.unknown": ("Lexicon", "Not in the OV dictionary", False, False),
    "indicqa.shape.malformed": ("Shape", "Malformed Tamil spelling", False, True),
    "indicqa.shape.initial-pulli": ("Shape", "Word starts with a consonant + புள்ளி (broken word?)", False, True),
    "indicqa.shape.unusual-initial": ("Style", "Unusual first letter: no Tamil word starts with ங ண ழ ள ற ன ட", False, True),
    "indicqa.sandhi.missing": ("Grammar", "ஒற்று மிகும்: the linking consonant is missing", False, True),
    "indicqa.sandhi.extra": ("Grammar", "ஒற்று மிகாது: this linking consonant should go", False, True),
    "indicqa.sandhi.wrong-class": ("Grammar", "ஒற்று does not match the next word's first letter", False, True),
    "indicqa.sandhi.dangling": ("Grammar", "ஒற்று with nothing to join", False, True),
    "indicqa.sandhi.conflict": ("Grammar", "ஒற்று: the OV, the grammar and the IRV disagree", False, True),
    "indicqa.punct.no-space-after": ("Punctuation", "No space after , ; : ! ?", False, True),
    # Run in headings and footnotes only; Bridge's common rules cover verse text.
    "indicqa.spacing.double-space": ("Punctuation", "Double space (heading or footnote)", False, True),
    "indicqa.unicode.zero-width": ("Encoding", "Zero-width character (heading or footnote)", False, True),
    "indicqa.punct.repeated": ("Punctuation", "Repeated punctuation (heading or footnote)", False, True),
    "indicqa.punct.space-before": ("Punctuation", "Space before punctuation (heading or footnote)", False, True),
    "indicqa.typo.known-defect": ("Style", "Known IRV defect pattern (heading or footnote)", False, True),
}

# Bridge's view of each rule, written once by
# scripts/build_indic_qa_packs.py --ta-layer; the file, not this table, is
# what runs. Same shape and validation as a profile pack's rule_versions.json.
DEFAULT_ENTRIES: dict[str, dict[str, Any]] = {
    "indicqa.lex.near-miss": {"category": "typo", "layer": "lexicon", "severity": "medium", "confidence": "medium"},
    "indicqa.lex.compound": {"category": "word-joining", "layer": "lexicon", "severity": "low", "confidence": "low"},
    "indicqa.lex.unknown": {
        "category": "typo", "layer": "lexicon", "severity": "low", "confidence": "low", "enabled": False,
        "note": "off in Bridge: on the IRV, 2,000+ per book are valid modern Tamil the 1957 OV lacks"},
    "indicqa.shape.malformed": {"category": "unicode", "layer": "integrity", "severity": "medium", "confidence": "medium"},
    "indicqa.shape.initial-pulli": {"category": "word-joining", "layer": "pattern", "severity": "medium",
                                    "confidence": "medium"},
    "indicqa.shape.unusual-initial": {"category": "typo", "layer": "pattern", "severity": "low", "confidence": "low"},
    "indicqa.sandhi.missing": {"category": "sandhi", "layer": "pattern", "severity": "medium", "confidence": "medium"},
    "indicqa.sandhi.extra": {"category": "sandhi", "layer": "pattern", "severity": "medium", "confidence": "medium"},
    "indicqa.sandhi.wrong-class": {"category": "sandhi", "layer": "pattern", "severity": "medium", "confidence": "medium"},
    "indicqa.sandhi.dangling": {"category": "sandhi", "layer": "pattern", "severity": "low", "confidence": "medium"},
    "indicqa.sandhi.conflict": {"category": "sandhi", "layer": "pattern", "severity": "low", "confidence": "low"},
    "indicqa.punct.no-space-after": {"category": "punctuation", "layer": "integrity", "severity": "medium",
                                     "confidence": "medium"},
    "indicqa.spacing.double-space": {"category": "spacing", "layer": "integrity", "severity": "medium",
                                     "confidence": "medium"},
    "indicqa.unicode.zero-width": {"category": "unicode", "layer": "integrity", "severity": "low", "confidence": "medium"},
    "indicqa.punct.repeated": {"category": "punctuation", "layer": "integrity", "severity": "medium",
                               "confidence": "medium"},
    "indicqa.punct.space-before": {"category": "punctuation", "layer": "integrity", "severity": "medium",
                                   "confidence": "medium"},
    "indicqa.typo.known-defect": {"category": "typo", "layer": "pattern", "severity": "medium", "confidence": "medium"},
}

# The checker's own switches for this layer, merged over ta.DEFAULT_SETTINGS.
SETTINGS: dict[str, Any] = {
    "warnings": {"digits": False, "repeated_word": False, "space_before_note_close": False},
}

# Warning kind -> rule. The second set is reported outside verse text only.
WARNING_RULES = {
    "no_space_after_punct": "indicqa.punct.no-space-after",
    "initial_pulli": "indicqa.shape.initial-pulli",
    "unusual_initial": "indicqa.shape.unusual-initial",
    "double_space": "indicqa.spacing.double-space",
    "zero_width": "indicqa.unicode.zero-width",
    "double_punct": "indicqa.punct.repeated",
    "space_before_punct": "indicqa.punct.space-before",
    "defect_yehova_vowel": "indicqa.typo.known-defect",
    "defect_yehova_dative": "indicqa.typo.known-defect",
    "defect_vatharku": "indicqa.typo.known-defect",
}
VERSE_COVERED = frozenset({"double_space", "zero_width", "double_punct", "space_before_punct",
                           "defect_yehova_vowel", "defect_yehova_dative", "defect_vatharku"})
WARNING_LABELS = {
    "defect_yehova_vowel": "known defect யெகோவவ", "defect_yehova_dative": "known defect யெகோவாக்க",
    "defect_vatharku": "possible -வதற்கு defect",
}
DANGLING_REASONS = {"vowel": "before a word that does not start with க/ச/த/ப", "digit": "before a number",
                    "punct": "before punctuation", "end": "at the end of the text"}
BASIS = {"ov": "the OV's habit for this word", "grammar": "the sandhi grammar", "suffix": "the OV's habit for "
         "this ending", "dangling": "nothing follows to join", "irv": "the IRV's own habit", "ov_pooled":
         "the OV's habit for this word before any க/ச/த/ப"}


class Item(NamedTuple):
    """One finding the checker reported in a line, before Bridge maps it.
    `start`/`end` are offsets in the line (the run's own offset added);
    `seg_start`/`stream` say which run, so a footnote's text maps to its
    own raw range."""
    rule: str
    start: int
    end: int
    suggestions: list
    why: str
    detail: str
    seg_start: int
    stream: int
    context: str


def _suggestion(text: str, source: str, rationale: str) -> dict[str, Any]:
    return {"text": text, "rank": 1, "source": source, "rationale": rationale}


def _word_suggestions(token: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for s in token.get("sugg", ()):
        if not s.get("w"):
            continue
        cls = s.get("cls") or s.get("op") or ""
        why = {"reviewed": "the reviewer's correction (corrections.tsv)", "learned": "changed before"}.get(cls, cls)
        out.append(_suggestion(s["w"], "lexicon", why + (f" · {s['freq']}× in the OV/IRV" if s.get("freq") else "")))
    return out


def _counts(token: dict[str, Any]) -> str:
    """The OV/IRV evidence for a word (point 9 of the 2026-10-07 comparison)."""
    return f"OV {token.get('ov', 0)}× (with its sandhi forms {token.get('ov_lemma', 0)}×), IRV {token.get('irv', 0)}×"


def _token_item(token: dict[str, Any]) -> tuple[str, list, str] | None:
    """(rule, suggestions, why) for a word, or None when the checker accepted it."""
    status = token["status"]
    if token.get("ignored_once") or status not in ("malformed", "unknown", "compound"):
        return None
    suggestions = _word_suggestions(token)
    evidence = _counts(token)
    if status == "malformed":
        return "indicqa.shape.malformed", suggestions, f"{token.get('rule', '')}; {evidence}"
    top = (token.get("sugg") or [{}])[0]
    rare = token.get("irv", 0) <= RARE_MAX
    if rare and top.get("w") and top.get("cls") in SLIP_CLASSES:
        return "indicqa.lex.near-miss", suggestions, f"{evidence}; nearest known word {top['w']}"
    if status == "compound" and rare:
        first, second, how = (list(token.get("parts") or ()) + ["", "", ""])[:3]
        split = [_suggestion(f"{first} {second}", "rule", f"two known words ({how} junction)")] if first else []
        return "indicqa.lex.compound", split + suggestions, f"{first} + {second}; {evidence}"
    return "indicqa.lex.unknown", suggestions, evidence


def _lead_item(lead: dict[str, Any]) -> tuple[str, list, str, str] | None:
    """(rule, suggestions, why, detail) for a sandhi lead."""
    if lead.get("ignored"):
        return None
    kind = lead.get("kind")
    proposed = lead.get("proposed")
    ov = f"OV {lead.get('with', 0)} with ஒற்று / {lead.get('without', 0)} without"
    irv = (f"; IRV {lead.get('irv_with', 0)} / {lead.get('irv_without', 0)}"
           if "irv_with" in lead else "")
    grammar = f"; rule {lead['rule']}: {lead.get('rule_en', '')} ({lead.get('rule_ta', '')})" if lead.get("rule") else ""
    if kind == "dangling":
        rule = "indicqa.sandhi.dangling"
        why = f"{DANGLING_REASONS.get(lead.get('reason', ''), lead.get('reason', ''))}" + (
            f" ({lead['w2']})" if lead.get("w2") else "") + f"; OV {lead.get('with', 0)} / {lead.get('without', 0)}"
    elif kind == "wrong_class":
        rule, why = "indicqa.sandhi.wrong-class", f"before {lead.get('w2', '')}; {ov}"
    elif lead.get("strength") == "conflict":
        objecting = ", ".join(BASIS.get(s, s) for s in lead.get("objecting") or ())
        agreeing = ", ".join(BASIS.get(s, s) for s in lead.get("agreeing") or ())
        rule = "indicqa.sandhi.conflict"
        why = (f"{'add' if kind == 'missing' else 'remove'} ஒற்று before {lead.get('w2', '')}: {objecting} "
               f"say so, {agreeing} agree with the text; {ov}{irv}{grammar}")
    elif kind in ("missing", "extra"):
        rule = "indicqa.sandhi.missing" if kind == "missing" else "indicqa.sandhi.extra"
        why = f"before {lead.get('w2', '')}, by {BASIS.get(lead.get('basis', ''), lead.get('basis', ''))}; {ov}{irv}{grammar}"
    else:
        return None
    suggestions = ([_suggestion(proposed, "rule", BASIS.get(lead.get("basis", ""), "sandhi"))]
                   if proposed and proposed != lead.get("w1") else [])
    return rule, suggestions, why, str(lead.get("rule") or kind)


def _warning_item(warning: dict[str, Any], text: str, context: str) -> tuple[str, list, str] | None:
    if warning.get("ignored"):
        return None
    kind = warning.get("kind", "")
    rule = WARNING_RULES.get(kind)
    if rule is None or (context == "verse" and kind in VERSE_COVERED):
        return None
    span = text[warning["s"]:warning["e"]]
    fix = {"no_space_after_punct": span + " ", "double_space": " ", "space_before_punct": ""}.get(kind)
    suggestions = [_suggestion(fix, "rule", kind.replace("_", " "))] if fix is not None else []
    return rule, suggestions, WARNING_LABELS.get(kind, "")


def items(block: dict[str, Any]) -> list[Item]:
    """Every finding in one checked line (Checker.check_line's block)."""
    out: list[Item] = []
    for seg in block["segs"]:
        if seg.get("k") != "t" or not seg.get("checked"):
            continue
        at, stream, context = seg["s"], int(seg.get("stream") or 0), str(seg.get("ctx") or "verse")
        for token in seg.get("tokens", ()):
            hit = _token_item(token)
            if hit:
                out.append(Item(hit[0], at + token["s"], at + token["e"], hit[1], hit[2],
                                token.get("rule") or token["status"], at, stream, context))
        for lead in seg.get("sandhi", ()):
            hit = _lead_item(lead)
            if hit:
                out.append(Item(hit[0], at + lead["s"], at + lead["e"], hit[1], hit[2], hit[3], at, stream, context))
        for warning in seg.get("warnings", ()):
            hit = _warning_item(warning, seg.get("t", ""), context)
            if hit:
                out.append(Item(hit[0], at + warning["s"], at + warning["e"], hit[1], hit[2],
                                warning.get("kind", ""), at, stream, context))
    return out
