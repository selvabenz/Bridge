"""Tamil profile: the constants and helpers the editor used before language profiles existed,
moved here unchanged.  Tamil results are pinned by qa_app/tests/baseline (see its README);
nothing added for another language may change a value here.
"""
from __future__ import annotations

import re
from types import SimpleNamespace

from .. import paths
from .. import usfm_doc as ud
from . import APP_DIR, REPO_ROOT, Language

bd = ud.bd
PULLI = "்"

DEFAULT_SETTINGS: dict = {
    "reviewer": "",
    "checked_contexts": ["verse", "heading", "psalm_title", "footnote_text", "intro", "title"],
    "sandhi": {"min_total": 5, "minority_max": 2, "majority_min": 10, "include_weak": False, "weak_pct": 0.8,
               "report_wrong_class": True, "use_suffix": True, "suffix_min_forms": 20, "suffix_pct": 0.95, "irv_veto_min": 5, "irv_veto_pct": 0.35,
               "use_grammar": True, "show_conflicts": True, "dangling": True, "dangling_digits": True, "dangling_irv_min": 2},
    "suggest": {"max": 5, "lemma_min": 3, "irv_min": 3},
    "compound": {"enabled": True, "min_first": 3, "min_second": 4, "min_freq": 3,
                 "suffix_stop": ["கள்", "களை", "களின்", "களும்", "தான்", "வன்", "வள்", "வர்", "பின்", "படி", "மேல்",
                                 "கட்டும்", "போல", "ஆக", "உம்", "இல்", "என்", "எல்லாம்"]},
    "warnings": {"double_space": True, "digits": True, "defects": True, "space_before_note_close": False,
                 "double_punct": True, "no_space_after_punct": True, "space_before_punct": True,
                 "unusual_initial": True, "initial_pulli": True, "repeated_word": False},
}
DEFECT_PATTERNS = [("defect_yehova_vowel", re.compile("யெகோவவ")), ("defect_yehova_dative", re.compile("யெகோவாக்க")),
                   ("defect_vatharku", re.compile("வற்கு"))]
TA = "஀-௿"
PUNCT_RUN_RE = re.compile(r"[,.;:!?]{2,}")
OK_PUNCT_RUNS = {"?!", "!?", "!!", "??"}
# , ; : ! ? glued to the next Tamil word; a full stop only after 3+ Tamil letters (கி.மு., கி.மீ. are abbreviations)
NO_SPACE_AFTER_RE = re.compile(rf"[,;:!?](?=[{TA}])|(?<=[{TA}]{{3}})\.(?=[{TA}])")
SPACE_BEFORE_RE = re.compile(rf"(?<=[{TA}\d)])( +)(?=[,;:.!?])")
UNUSUAL_INITIAL = "ஙணழளறனட"                      # no native Tamil word starts with these
NATIVE_CONS = "கஙசஞடணதநனபமயரறலளழவ"            # Grantha ஸ் ஷ் ஜ் ஹ் may start a word (ஸ்தலம்)
GRAPHEME_START = re.compile(rf"[{bd.CONS}{bd.IVOWEL}]")

SANDHI = SimpleNamespace(kstp=bd.KSTP, kstp_letter=bd.KSTP_LETTER, final_re=bd.SANDHI_FINAL_RE,
                         eligible_re=bd.ELIGIBLE_RE, pulli=PULLI)


def malformed(w: str):
    for name, severity, rx in bd.MALFORMED_RULES:
        if rx.search(w):
            return name, severity
    return None


def rank(checker, w: str, c: str):
    e = bd.edit1(w, c)
    if not e:
        return None
    op, frm, to, i = e
    pos = bd.edit_position(i, len(w))
    cls = bd.edit_class(op, frm, to, pos)
    return (bd.EDIT_CLASS_RANK[cls], bd.POSITION_RANK[pos], -checker.freq(c), c,
            {"w": c, "op": op, "from": frm, "to": to, "at": i, "cls": cls, "pos": pos, "freq": checker.freq(c)})


def extra_suggestions(checker, w: str) -> list:
    # sandhi-aware: a known bare form carrying an ஒற்று of any class is proposed as itself
    if bd.SANDHI_FINAL_RE.search(w) and checker.lex.known_bare(w[:-2]):
        bare = w[:-2]
        return [(-1, 0, -checker.freq(bare), bare,
                 {"w": bare, "op": "del", "from": w[-2:], "to": "", "at": len(w) - 2, "cls": "sandhi", "pos": "final",
                  "freq": checker.freq(bare)})]
    return []


def token_warning(checker, text: str, w: str, a: int, b: int, s: dict):
    if w[1:2] == PULLI:
        if (s.get("initial_pulli", True) and w[0] in NATIVE_CONS and w[2:3] != "ஷ"
                and not text[:a].rstrip()[-1:].isdigit() and text[a - 1:a] != "."):
            # a case ending after a number or an abbreviation is fine: 1446 க்கும், கி.மீ.க்கு
            return {"kind": "initial_pulli", "s": a, "e": b}
    elif s.get("unusual_initial", True) and w[0] in UNUSUAL_INITIAL and not checker.lex.known(w):
        return {"kind": "unusual_initial", "s": a, "e": b}
    return None


def related_norm(lex):
    def norm(w: str) -> str:
        if bd.SANDHI_FINAL_RE.search(w) and lex.kstp_kind.get(w) != "lexical" and lex.known_bare(w[:-2]):
            return w[:-2]
        return w
    return norm


# The editor's Tamil wording.  These were the defaults written into static/js and index.html before
# every language, Tamil included, took its labels from its profile; moving them here changes no
# check result (the regression contract covers the checker, not the page).  Keys: docs/LANGUAGE_PROFILES.md.
LABELS = {
    "status": {"unknown": "not in the dictionary", "compound": "unknown, but splits into two known words",
               "malformed": "malformed Tamil spelling", "sandhi_ok": "bare form + ஒற்று (known)"},
    "cls": {"consonant_confusable": "ல/ள/ழ ன/ண/ந ர/ற", "vowel_length": "vowel length", "pulli": "புள்ளி", "transpose": "swap",
            "inflection": "ending", "clitic": "clitic", "other": "", "sandhi": "ஒற்று"},
    "warn": {"defect_yehova_vowel": "known defect யெகோவவ", "defect_yehova_dative": "known defect யெகோவாக்க",
             "defect_vatharku": "possible -வதற்கு defect",
             "unusual_initial": "unusual first letter (no Tamil word starts with ங ண ழ ள ற ன ட)",
             "initial_pulli": "word starts with a consonant + புள்ளி (broken word?)"},
    "verdict": {"double": "ஒற்று மிகும்", "bare": "ஒற்று மிகாது", "ambiguous": "no verdict"},
    "dangling_title": "ஒற்று with nothing to join",
    "dangling_prefix": "dangling ஒற்று",
    "pooled_letters": "க/ச/த/ப",
    "legend": [["unknown", "unknown"], ["sandhi", "sandhi lead"], ["sandhi conflict", "sources disagree"],
               ["sandhi dangling", "dangling ஒற்று"], ["malformed", "malformed"], ["compound", "compound?"],
               ["sandhi_ok", "sandhi form"], ["warn", "warning"], ["learned", "changed before"], ["ignored", "ignored"]],
    "rules_link": "grammar rules",
    "rules_link_title": "The Tamil sandhi rules the check uses, with how often the OV follows each",
    "counts_leads": "sandhi",
    "leads_heading": "Sandhi leads",
    "ignored_pairs_heading": "Ignored sandhi pairs",
    "rebuild": "Rebuild dictionary from the OV + extra_words.txt",
    "issue_types": ["Possible typo", "Confirmed typo", "Sandhi / word-joining", "Grammar", "Punctuation", "Plural agreement",
                    "Case/object marker", "Name consistency", "Encoding", "Font / rendering", "Reviewer decision", "Other"],
    "lead_issue_type": "Sandhi / word-joining",
    "settings_warn": {"double_space": "double space", "digits": "digits in verse text", "defects": "known IRV defect patterns",
                      "space_before_note_close": "space before a footnote end", "double_punct": "double punctuation (?, .,  ,,)",
                      "no_space_after_punct": "no space after , ; : ! ?", "space_before_punct": "space before punctuation",
                      "unusual_initial": "word starting with ங ண ழ ள ற ன ட",
                      "initial_pulli": "word starting with a consonant + புள்ளி (ள்ள)",
                      "repeated_word": "the same word twice (தங்கள் தங்கள் is often right)"},
    "sandhi_settings": {"legend": "Sandhi leads (OV reference counts)", "st-wrongclass": "report wrong-class ஒற்று",
                        "st-dangling": "report an ஒற்று with nothing to join (before a vowel, punctuation or the end)"},
    "word_re": f"[{TA}]+(?:-[{TA}]+)*",
}

PROFILE = Language(
    code="ta", name="Tamil", script=TA, file_re=ud.FILE_RE,
    dict_dir_default=REPO_ROOT / "dictionary", data_dir_default=APP_DIR / "data", port_default=8765,
    html_lang="ta", app_title="Tamil IRV spell-check editor",
    token_re=bd.TOKEN_RE, grapheme_start=GRAPHEME_START, default_settings=DEFAULT_SETTINGS,
    malformed=malformed, rank=rank, extra_suggestions=extra_suggestions,
    defect_patterns=DEFECT_PATTERNS, punct_run_re=PUNCT_RUN_RE, ok_punct_runs=OK_PUNCT_RUNS,
    no_space_after_re=NO_SPACE_AFTER_RE, space_before_re=SPACE_BEFORE_RE, token_warning=token_warning,
    related_norm=related_norm, build_args=lambda config: ["--expect", "bsi1957"],
    irv_dir_default=str(paths.irv_dir("ta")),
    sandhi=SANDHI, features={"sandhi": True}, labels=LABELS,
    font_family="Noto Sans Tamil", font_fallback=("Nirmala UI", "Latha"),
)
