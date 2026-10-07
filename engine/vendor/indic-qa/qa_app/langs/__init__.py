"""Language profiles.

Everything the editor knows about one language lives in one `Language` object: script,
tokenizer, file names, where its dictionary and state live, the shape (malformed) rules,
how near-miss suggestions are ranked, which warnings apply, and the optional features the
language has (Tamil: sandhi; Punjabi: consistency and agreement leads; Malayalam: a canonical
lookup key, suffix stripping and consistency leads).  `checker.py`,
`corpus.py`, `related.py` and `server.py` read these fields and never test a language code.

Tamil is `ta.PROFILE`, built only from the constants that existed before profiles did, so
Tamil results do not depend on anything added for another language.  See
docs/LANGUAGE_PROFILES.md.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

APP_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = APP_DIR.parent


class LanguageError(Exception):
    """Raised when the language cannot be chosen safely (mixed folder, disagreement, unknown code)."""


class MixedFolderError(LanguageError):
    """The IRV folder holds files of more than one language."""


@dataclass(eq=False)
class Language:
    # identity
    code: str
    name: str
    script: str                          # regex character-class body of the script's letters
    file_re: re.Pattern                  # IRV file names; group(2) is the book code
    dict_dir_default: Path
    data_dir_default: Path
    port_default: int
    html_lang: str
    app_title: str
    # tokenizer
    token_re: re.Pattern
    grapheme_start: re.Pattern           # where the second part of a compound may start
    # settings
    default_settings: dict
    # shape and suggestions
    malformed: Callable                  # (word) -> (rule, severity) | None
    rank: Callable                       # (checker, word, candidate) -> (k0, k1, k2, candidate, dict) | None
    extra_suggestions: Callable          # (checker, word) -> [(k0, k1, k2, w, dict)]
    # warnings
    defect_patterns: list
    punct_run_re: re.Pattern
    ok_punct_runs: set
    no_space_after_re: re.Pattern
    space_before_re: re.Pattern
    token_warning: Callable              # (checker, text, w, a, b, settings) -> dict | None
    # relation to the OV text
    related_norm: Callable               # (lexicon) -> (word -> word used for OV/IRV alignment)
    build_args: Callable                 # (server CONFIG) -> arguments to scripts/build_dictionary.py (Settings > Rebuild)
    irv_dir_default: str                 # IRV folder used when none is given or remembered
    # optional features: None when the language does not have them
    sandhi: Optional[object] = None      # Tamil sandhi constants (KSTP, final/eligible regexes, pulli)
    more_candidates: Optional[Callable] = None    # (checker, word) -> set of extra suggestion candidates
    classify_first: Optional[Callable] = None     # (checker, word, irv, ov) -> WordInfo | None, before everything
    report_block: Optional[Callable] = None       # (checker, report_extra: dict, block) -> None, in book_report
    classify_extra: Optional[Callable] = None     # (checker, word, irv, ov) -> WordInfo | None, after known/hyphen
    line_leads: Optional[Callable] = None         # (checker, toks, line) -> [lead] for one adjacency stream
    chapter_leads: Optional[Callable] = None      # (checker, book, n, blocks) -> None, may add leads/warnings
    extra_warnings: Optional[Callable] = None     # (checker, seg, next_seg) -> [warning]
    on_index_book: Optional[Callable] = None      # (checker, code, book, checked) -> None
    on_build_index: Optional[Callable] = None     # (checker) -> None
    load_lexicon_extra: Optional[Callable] = None  # (lexicon, dict_dir) -> None
    info_extra: Optional[Callable] = None         # (checker) -> dict merged into /api/info
    rules_payload: Optional[Callable] = None      # (checker) -> /api/rules payload (default: Tamil sandhi rules)
    rules_page: str = "rules.html"
    labels: dict = field(default_factory=dict)    # UI label overrides (empty for Tamil: the UI's own labels)
    features: dict = field(default_factory=dict)
    zero_width_re: Optional[re.Pattern] = None    # the "invisible character" warning; None = the checker's own (Tamil's)
    forbidden_in_text: Optional[dict] = None      # characters an edit may not type; None = usfm_doc.FORBIDDEN_IN_TEXT
    detect_file_re: Optional[re.Pattern] = None   # file names that identify the language; None = file_re.  Hindi's
                                                  # NN-BBB.usfm names are not language-specific, so only …IRVHin.SFM counts
    detect_letters_re: Optional[re.Pattern] = None  # the script's letters for detection; None = [script] (Hindi leaves
                                                  # out the danda and digits, which Gurmukhi text uses too)
    line_ending: str = "\r\n"                     # the IRV files' line ending: CRLF (a bare LF makes a book read-only),
                                                  # or LF for the Hindi files (then a CR makes it read-only); kept on save
    # fonts: the bundled Noto Sans face of the script (qa_app/static/fonts, fonts.css) renders first; the
    # Windows UI font and the script's older Windows font fill in whatever glyphs it lacks
    font_family: str = "Noto Sans"
    font_fallback: tuple = ("Nirmala UI",)

    def tokens(self, text: str) -> list[str]:
        return self.token_re.findall(text)

    def tokens_with_offsets(self, text: str) -> list[tuple[str, int, int]]:
        return [(m.group(), m.start(), m.end()) for m in self.token_re.finditer(text)]

    def book_code_from_filename(self, name: str) -> str | None:
        m = self.file_re.match(name)
        return m.group(2).upper() if m else None

    def fonts(self) -> dict:
        """CSS font stacks for the page: `script` for the Scripture text and every word shown in its
        script, `ui` for the chrome.  Noto Sans first, the Windows fonts as fallback (docs/LANGUAGE_PROFILES.md)."""
        return {"script": [self.font_family, *self.font_fallback, "Noto Sans", "sans-serif"],
                "ui": ["Noto Sans", "Segoe UI", "system-ui", "sans-serif"],
                "mono": ["Consolas", self.font_family, "Nirmala UI", "monospace"]}

    def info(self) -> dict:
        return {"lang": self.code, "name": self.name, "html_lang": self.html_lang, "title": self.app_title,
                "labels": self.labels, "features": self.features, "rules_page": "/rules", "fonts": self.fonts()}


_REGISTRY: dict[str, Language] = {}
MODULE = {"or": "odia"}                 # code -> module name where the code is not a usable name (`or` is a keyword)


def _load() -> dict[str, Language]:
    if not _REGISTRY:
        from . import ta
        _REGISTRY["ta"] = ta.PROFILE
        for code in ("pa", "ml", "hi", "or"):
            try:
                mod = __import__(f"{__name__}.{MODULE.get(code, code)}", fromlist=["PROFILE"])
            except ImportError:
                continue
            _REGISTRY[code] = mod.PROFILE
    return _REGISTRY


def get(code: str) -> Language:
    langs = _load()
    if code not in langs:
        raise LanguageError(f"unknown language {code!r}; known: {', '.join(sorted(langs))}")
    return langs[code]


def default() -> Language:
    return get("ta")


def all_codes() -> list[str]:
    return sorted(_load())


def _script_of(folder: Path, langs: dict[str, Language], max_files: int = 3, max_lines: int = 200) -> dict[str, int]:
    """Letters of each language's script (and "*": all letters) in the first verse lines of the first
    few .SFM files."""
    counts = {c: 0 for c in langs}
    counts["*"] = 0
    rxs = {c: l.detect_letters_re or re.compile(f"[{l.script}]") for c, l in langs.items()}
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in (".sfm", ".usfm"))[:max_files]
    for p in files:
        seen = 0
        with p.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line.startswith("\\v "):
                    continue
                for c, rx in rxs.items():
                    counts[c] += len(rx.findall(line))
                counts["*"] += sum(1 for ch in line[3:] if ch.isalpha() and ord(ch) > 0x2FF)
                seen += 1
                if seen >= max_lines:
                    break
    return counts


def detect(folder: Path) -> tuple[str, str]:
    """(language code, reason).  File names decide first (…IRVTam.SFM, …IRVPun.SFM); the script of
    the first verse lines must agree.  A folder with files of two languages, or names and script
    that disagree, is refused."""
    folder = Path(folder)
    if not folder.is_dir():
        raise LanguageError(f"IRV folder not found: {folder}")
    langs = _load()
    by_name = {c: sum(1 for p in folder.iterdir() if (l.detect_file_re or l.file_re).match(p.name)) for c, l in langs.items()}
    named = [c for c, n in by_name.items() if n]
    if len(named) > 1:
        raise MixedFolderError("the IRV folder mixes languages: "
                            + ", ".join(f"{by_name[c]} {langs[c].name} files" for c in named)
                            + "; keep one language per folder")
    script = _script_of(folder, langs)
    total = script.pop("*")
    top = max(script, key=script.get) if total else None
    if named:
        code = named[0]
        if total and script[code] < 0.5 * total:
            other = f"mostly {langs[top].name} script" if top and script[top] >= 0.5 * total else "not in its script"
            raise LanguageError(f"file names say {langs[code].name} but the verse text is {other}; "
                                f"pass --lang explicitly if this is intended")
        return code, f"{by_name[code]} file names match {langs[code].name}"
    if top and script[top] >= 0.8 * total and langs[top].detect_file_re is not None:
        raise LanguageError(f"the text looks {langs[top].name}, but its file names are not specific to one language; "
                            f"pass --lang {top} (or use the run_qa_app_{top}.bat launcher)")
    if top and script[top] >= 0.8 * total:
        raise LanguageError(f"no file name matches any language's IRV pattern (text looks {langs[top].name}); "
                            f"rename the files to the …IRV<Lang>.SFM pattern")
    raise LanguageError("cannot tell the language of the IRV folder; pass --lang")
