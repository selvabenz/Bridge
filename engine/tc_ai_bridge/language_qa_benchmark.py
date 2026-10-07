"""Language QA benchmark: per-rule precision and recall against the IRV
Round 2 and Pass 3 review reports (layered-rules brief, Phase 2).

What the numbers mean. Every review row is an AI proposal; the maintainer
decided on 2026-09-24 that they are all to be used as positives (a false
positive can be corrected later). A row is "maybe" instead when the reports
contradict themselves on it (see contradictions()) or a human verdict
contradicts it. Nothing in the inputs is a verified negative. So precision
here measures agreement with the AI review, not with verified truth --
docs/LANGUAGE_QA_BENCHMARK.md says so next to every table.

The scan is the app's own: every book is scanned by LanguageQaManager over
chapter JSON built the way import builds it (parse_scripture_file +
imported_verse_text), so nothing here can drift from what a translator sees.
No Scripture is written anywhere but a temporary directory.
"""
from __future__ import annotations

import csv
import json
import re
import tempfile
import time
import unicodedata
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable

from .language_qa import PACK_VERSION
from .language_qa_jobs import LanguageQaManager

# The pack every committed human label round is about (benchmark/human/): the
# labels are Tamil IRV findings. The scans below declare Tamil, so the app's
# own registry resolves them to this pack.
HUMAN_PACK = "ta-irv"
from .project_import import BOOK_NAMES, imported_verse_text, parse_scripture_file

# Issue Type -> the offline engine's bucket. Everything else is reported as
# out of scope (LQA-3+): meaning, grammar and source comparison are not
# text-only checks.
SCORED_TYPES = {
    "Confirmed typo": "typo",
    "Possible typo": "typo",
    "Sandhi / word-joining": "sandhi",
    "Punctuation": "punctuation",       # only spacing / repetition / space-before; see _punctuation_in_scope
    "Name consistency": "name",
    "USFM marker": "usfm",
    "Footnote / cross-reference": "usfm",
}
BUCKETS = ("typo", "sandhi", "punctuation", "name", "usfm")

# Which review bucket a finding can agree with, by the finding's category. A
# finding that only overlaps a row of another bucket is not a true positive.
# Keyed by category, not rule, so a new pack rule is scored without an edit here.
CATEGORY_BUCKETS = {
    "sandhi": {"sandhi"},
    "word-joining": {"sandhi"},
    "typo": {"typo"},
    "unicode": {"typo"},
    "punctuation": {"punctuation", "usfm"},
    "spacing": {"punctuation", "usfm"},
    "termbase": {"name", "typo"},
    "name": {"name"},
    "usfm": {"usfm"},
}


def finding_buckets(finding: dict[str, Any]) -> set[str]:
    return CATEGORY_BUCKETS.get(finding.get("category", ""), set())

# House forms the Pass 3 reviewers confirmed are NOT errors
# (IRV_Pass3_Handoff.md, section 5, "Method notes carried forward"). A row
# that flags one of them contradicts that finding, so it is "maybe".
SANDHI_HOUSE_FORMS = (
    ("இந்த + bare hard consonant", re.compile(r"(?:^|\s)இந்த\s+[கசதப]")),
    ("அந்த தேச- bare", re.compile(r"(?:^|\s)அந்த\s+தேச")),
    ("-விட bare", re.compile(r"விட\s+[கசதப]")),
    ("proper name ending in -க்கு (ஈசாக்கு, ஏனோக்கு) is a nominative", re.compile(r"(?:ஈசாக்கு|ஏனோக்கு)\s")),
)


def _house_form(row: "ReviewRow") -> str | None:
    """The confirmed house form a row flags, if any."""
    original = nfc(row.original)
    if row.bucket == "sandhi":
        for name, pattern in SANDHI_HOUSE_FORMS:
            if pattern.search(original):
                return name
    # Digits: only a proposal to write out numerals contradicts the house form.
    if row.bucket in {"typo", "punctuation"} and re.search(r"\d", original) \
            and row.suggestion and not re.search(r"\d", row.suggestion):
        return "digits in verse text"
    return None

# A human verdict against an AI proposal is a contradiction, so "maybe", not a
# negative: the Philippians pass answered a different question (is this
# inconsistency worth an editorial fix), and the maintainer said its
# dispositions are not linguistic ground truth (BUILD_LOG, B3 entry; php 1:29
# later went from Rejected to Confirmed). Nothing in the inputs is a verified
# negative, so "negative" is reserved for a future human-labelled set.
HUMAN_NEGATIVE: set[str] = set()
HUMAN_MAYBE = {"rejected", "disputed"}

_BOOK_CODES = {name.lower(): code for code, name in BOOK_NAMES.items()}
_BOOK_CODES.update({code: code for code in BOOK_NAMES})
_BOOK_CODES.update({"psalm": "psa", "song of songs": "sng", "song of solomon": "sng"})


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text or "")


def book_code(value: str) -> str | None:
    return _BOOK_CODES.get((value or "").strip().lower())


@dataclass
class ReviewRow:
    source: str          # file name
    family: str          # "Round2" | "Pass3" | "Reviewed"
    book: str
    chapter: str
    verse: str
    issue_type: str
    bucket: str | None   # None: out of scope
    severity: str
    confidence: str
    original: str
    suggestion: str
    explanation: str
    status: str
    label: str = "positive"   # "positive" | "maybe" | "negative" | "out-of-scope" | "skipped"
    reason: str = ""
    anchored: bool = False    # Original Tamil found in the scanned verse text
    matched_by: list[str] = field(default_factory=list)


def _family(path: Path) -> str:
    name = path.name
    if "Pass3" in name:
        return "Pass3"
    if "Round2" in name and "Issues" in name and "QA_Issues" not in name:
        return "Round2"
    return "Reviewed"


def _punctuation_in_scope(original: str, suggestion: str, explanation: str) -> bool:
    """Spacing, repetition and space-before only; comma/semicolon policy is out."""
    squeeze = lambda s: re.sub(r"\s+", "", nfc(s))  # noqa: E731
    if suggestion and squeeze(original) == squeeze(suggestion) and nfc(original) != nfc(suggestion):
        return True
    if re.search(r" {2,}|([,;:!?.])\1| [,;:!?.]", original):
        return True
    return bool(re.search(r"\b(space|spacing|double|repeated|duplicated|stray)\b", explanation, re.I))


def load_review_rows(paths: Iterable[Path]) -> list[ReviewRow]:
    rows: list[ReviewRow] = []
    for path in paths:
        with Path(path).open(encoding="utf-8-sig", newline="") as handle:
            for raw in csv.DictReader(handle):
                get = lambda key: (raw.get(key) or "").strip()  # noqa: E731
                issue_type = get("Issue Type")
                row = ReviewRow(
                    source=Path(path).name, family=_family(Path(path)),
                    book=book_code(get("Book")) or get("Book").lower(),
                    chapter=get("Chapter"), verse=get("Verse"), issue_type=issue_type,
                    bucket=SCORED_TYPES.get(issue_type), severity=get("Severity"),
                    confidence=get("Confidence"), original=get("Original Tamil"),
                    suggestion=get("Suggested Correction"), explanation=get("Explanation"),
                    status=get("Status"),
                )
                rows.append(row)
    classify(rows)
    return rows


def classify(rows: list[ReviewRow]) -> None:
    """Scope first, then human verdicts, then contradictions."""
    for row in rows:
        if not row.chapter.isdigit() or not row.verse or not row.verse[:1].isdigit():
            row.label, row.reason = "skipped", "not a verse (Intro, book-level, or no verse)"
        elif not row.original:
            row.label, row.reason = "skipped", "no Original Tamil to match"
        elif row.bucket is None:
            row.label, row.reason = "out-of-scope", f"{row.issue_type}: not a text-only check (LQA-3+)"
        elif row.bucket == "punctuation" and not _punctuation_in_scope(row.original, row.suggestion, row.explanation):
            row.bucket, row.label, row.reason = None, "out-of-scope", "punctuation policy, not spacing"
        elif row.status.lower() in HUMAN_NEGATIVE:
            row.label, row.reason = "negative", f"human verdict: {row.status}"
        elif row.status.lower() in HUMAN_MAYBE:
            row.label, row.reason = "maybe", f"human verdict: {row.status} (contradicts the AI proposal)"
    for row, reason in contradictions(rows):
        if row.label == "positive":
            row.label, row.reason = "maybe", reason


def contradictions(rows: list[ReviewRow]) -> list[tuple[ReviewRow, str]]:
    """Rows the reports themselves disagree on:
    - two rows at one place, on the same text, proposing different fixes;
    - a fix that another row at the same verse proposes to undo;
    - a flag on a form the Pass 3 reviewers confirmed is house style."""
    found: list[tuple[ReviewRow, str]] = []
    in_scope = [r for r in rows if r.label == "positive"]
    by_place: dict[tuple[str, str, str, str], list[ReviewRow]] = defaultdict(list)
    by_verse: dict[tuple[str, str, str], list[ReviewRow]] = defaultdict(list)
    for row in in_scope:
        by_place[(row.book, row.chapter, row.verse, nfc(row.original))].append(row)
        by_verse[(row.book, row.chapter, row.verse)].append(row)
    for group in by_place.values():
        fixes = {nfc(r.suggestion) for r in group if r.suggestion}
        if len(fixes) > 1:
            found.extend((r, "reports propose different fixes for the same text") for r in group)
    for group in by_verse.values():
        for a in group:
            for b in group:
                if a is not b and a.suggestion and nfc(a.suggestion) == nfc(b.original) \
                        and nfc(b.suggestion) == nfc(a.original):
                    found.append((a, "another row at this verse proposes the opposite change"))
    for row in in_scope:
        name = _house_form(row)
        if name:
            found.append((row, f"Pass 3 confirmed house form: {name}"))
    return found


def book_verses(sfm: Path) -> tuple[str, dict[str, dict[str, str]]]:
    """(book id, chapter -> verse -> text) exactly as an import writes it."""
    parsed = parse_scripture_file(sfm)
    return parsed.book_id, {
        chapter: {verse: imported_verse_text(text) for verse, text in verses.items()}
        for chapter, verses in parsed.chapters.items()
    }


def scan_book(book: str, chapters: dict[str, dict[str, str]], *,
              terminology: list[dict[str, Any]] | None = None,
              housestyle: list[dict[str, Any]] | None = None, timeout: float = 600.0,
              language: str = "tam") -> dict[str, Any]:
    """Scan with the app's own LanguageQaManager over temporary chapter JSON.
    `housestyle` (a project's entries, --housestyle) is applied as in the app;
    what it hides is reported per rule, never counted as a false positive.
    `language`: the project's declared language, which picks the pack (an
    indic-qa profile pack for hin/mal/ory/pan)."""
    with tempfile.TemporaryDirectory(prefix="lqa-bench-") as tmp:
        root = Path(tmp)
        folder = root / book
        folder.mkdir()
        for chapter, verses in chapters.items():
            (folder / f"{chapter}.json").write_text(json.dumps(verses, ensure_ascii=False), encoding="utf-8")
        project = SimpleNamespace(path=root, book_id=book, book_dir=folder,
                                  manifest={"target_language": {"id": language}},
                                  terminology_rules=lambda: list(terminology or []),
                                  housestyle_entries=lambda: list(housestyle or []))
        manager = LanguageQaManager(debounce=0, yield_seconds=0)
        started = time.perf_counter()
        manager.bind(project)
        try:
            deadline = time.monotonic() + timeout
            while manager.status()["state"] not in {"completed", "failed"}:
                if time.monotonic() > deadline:
                    raise TimeoutError(f"Language QA did not finish {book}")
                time.sleep(0.01)
            status = manager.status()
            findings = [f for offset in range(0, status["totalFindings"], 100)
                        for f in manager.status(offset=offset, limit=100)["findings"]]
        finally:
            manager.unbind()
    return {"book": book, "wall": time.perf_counter() - started, "findings": findings,
            "limitations": status.get("limitations", []), "checkedVerses": status.get("checkedVerses", 0),
            "houseStyleSuppressed": dict(status.get("houseStyleSuppressed") or {})}


def _overlaps(a: str, b: str) -> bool:
    a, b = nfc(a), nfc(b)
    return bool(a and b) and (a in b or b in a)


def score(rows: list[ReviewRow], scans: dict[str, dict[str, Any]],
          verses: dict[str, dict[str, dict[str, str]]]) -> dict[str, Any]:
    """Match findings to rows; count per rule and per bucket."""
    books = set(scans)
    scored = [r for r in rows if r.book in books and r.label in {"positive", "maybe", "negative"}]
    for row in scored:
        text = verses.get(row.book, {}).get(row.chapter, {}).get(row.verse)
        row.anchored = text is not None and nfc(row.original) in nfc(text)
    by_verse: dict[tuple[str, str, str], list[ReviewRow]] = defaultdict(list)
    for row in scored:
        by_verse[(row.book, row.chapter, row.verse)].append(row)

    per_rule: dict[str, Counter] = defaultdict(Counter)
    unmatched_findings: list[dict[str, Any]] = []
    for book, scan in scans.items():
        for finding in scan["findings"]:
            buckets = finding_buckets(finding)
            candidates = [r for r in by_verse.get((book, finding["chapter"], finding["verse"]), [])
                          if r.bucket in buckets and _overlaps(finding["originalText"], r.original)]
            labels = {r.label for r in candidates}
            for r in candidates:
                r.matched_by.append(finding["ruleId"])
            stats = per_rule[finding["ruleId"]]
            stats["findings"] += 1
            stats["inline"] = int(bool(finding.get("inline")) or stats.get("inline", 0))
            if "negative" in labels:
                stats["fp_strict"] += 1
                stats["fp_lenient"] += 1
                stats["matched_negative"] += 1
            elif "positive" in labels:
                stats["tp_strict"] += 1
                stats["tp_lenient"] += 1
            elif "maybe" in labels:
                stats["fp_strict"] += 1
                stats["tp_lenient"] += 1
                stats["matched_maybe"] += 1
            else:
                stats["fp_strict"] += 1
                stats["fp_lenient"] += 1
                # A finding on a form the Pass 3 reviewers confirmed is house
                # style: the abstains Phase 3's rule pack needs.
                house = next((name for name, pattern in SANDHI_HOUSE_FORMS
                              if pattern.search(nfc(finding["originalText"]))), None) \
                    if "sandhi" in buckets else None
                if house:
                    stats["fp_house_form"] += 1
                unmatched_findings.append({
                    "houseForm": house,
                    "book": book, "chapter": finding["chapter"], "verse": finding["verse"],
                    "ruleId": finding["ruleId"], "originalText": finding["originalText"],
                    "suggestion": finding.get("suggestedReplacement"), "message": finding["message"],
                })

    per_bucket: dict[str, Counter] = {bucket: Counter() for bucket in BUCKETS}
    unmatched_rows: list[dict[str, Any]] = []
    for row in scored:
        stats = per_bucket[row.bucket]
        stats[f"rows_{row.label}"] += 1
        if not row.anchored:
            stats["unanchored"] += 1
            continue
        if row.label == "negative":
            continue
        stats[f"anchored_{row.label}"] += 1
        if row.matched_by:
            stats[f"found_{row.label}"] += 1
        elif row.label == "positive":
            unmatched_rows.append({k: v for k, v in asdict(row).items() if k != "matched_by"})

    def ratio(numerator: int, denominator: int) -> float | None:
        return round(numerator / denominator, 4) if denominator else None

    rule_keys = ("findings", "tp_strict", "fp_strict", "tp_lenient", "fp_lenient",
                 "matched_maybe", "matched_negative", "fp_house_form")
    rules_out = {}
    # Findings a project's house style hid (--housestyle): reported, not scored,
    # so a learned entry can never raise a rule's precision silently.
    suppressed: Counter = Counter()
    for scan in scans.values():
        suppressed.update(scan.get("houseStyleSuppressed") or {})
    for rule_id in suppressed:
        per_rule.setdefault(rule_id, Counter())
    for rule_id, stats in sorted(per_rule.items()):
        rules_out[rule_id] = {
            **{key: stats.get(key, 0) for key in rule_keys},
            "inline": bool(stats.get("inline")),
            "suppressedByHouseStyle": suppressed.get(rule_id, 0),
            "precision_strict": ratio(stats["tp_strict"], stats["tp_strict"] + stats["fp_strict"]),
            "precision_lenient": ratio(stats["tp_lenient"], stats["tp_lenient"] + stats["fp_lenient"]),
        }
    bucket_keys = [f"{prefix}_{label}" for prefix in ("rows", "anchored", "found")
                   for label in ("positive", "maybe", "negative")] + ["unanchored"]
    buckets_out = {}
    for bucket, stats in per_bucket.items():
        buckets_out[bucket] = {
            **{key: stats.get(key, 0) for key in bucket_keys},
            "recall_strict": ratio(stats["found_positive"], stats["anchored_positive"]),
            "recall_lenient": ratio(stats["found_positive"] + stats["found_maybe"],
                                    stats["anchored_positive"] + stats["anchored_maybe"]),
        }
    labels = Counter((r.label, r.family) for r in rows)
    return {
        "packVersion": pack_version_label(),
        "books": sorted(books),
        "rows": {"total": len(rows), **{f"{label}/{family}": n for (label, family), n in sorted(labels.items())}},
        "outOfScope": dict(Counter(r.issue_type for r in rows if r.label == "out-of-scope" and r.book in books)),
        "rules": rules_out, "buckets": buckets_out,
        "unmatchedFindings": unmatched_findings, "unmatchedRows": unmatched_rows,
        "maybeReasons": dict(Counter(r.reason.split(":")[0] for r in scored if r.label == "maybe")),
        "scan": {b: {"wall": round(s["wall"], 2), "checkedVerses": s["checkedVerses"],
                     "findings": len(s["findings"])} for b, s in scans.items()},
    }


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def markdown_tables(result: dict[str, Any]) -> str:
    lines = [
        f"Pack version `{result['packVersion']}`; books: {', '.join(b.upper() for b in result['books'])}.",
        "",
        "Per rule (a finding is a true positive when it overlaps a review row of a compatible type at the same verse):",
        "",
        "| Rule | Inline | Findings | TP (strict) | FP (strict) | of which on a house form | Precision strict | Precision lenient | Matched maybe | Matched negative |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for rule_id, s in result["rules"].items():
        lines.append(f"| `{rule_id}` | {'yes' if s['inline'] else 'no'} | {s.get('findings', 0)} | {s.get('tp_strict', 0)} "
                     f"| {s.get('fp_strict', 0)} | {s.get('fp_house_form', 0)} | {_pct(s['precision_strict'])} "
                     f"| {_pct(s['precision_lenient'])} | {s.get('matched_maybe', 0)} | {s.get('matched_negative', 0)} |")
    suppressed = {r: s["suppressedByHouseStyle"] for r, s in result["rules"].items() if s.get("suppressedByHouseStyle")}
    if suppressed:
        lines += ["", "Suppressed by house style (--housestyle; not counted above): "
                  + ", ".join(f"`{r}` ({n})" for r, n in suppressed.items())]
    lines += [
        "",
        "Per review bucket (recall counts only rows whose Original Tamil is found in the scanned verse):",
        "",
        "| Bucket | Positive rows | Maybe rows | Negative rows | Unanchored | Found (positive) | Recall strict | Recall lenient |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for bucket, s in result["buckets"].items():
        lines.append(f"| {bucket} | {s.get('rows_positive', 0)} | {s.get('rows_maybe', 0)} | {s.get('rows_negative', 0)} "
                     f"| {s.get('unanchored', 0)} | {s.get('found_positive', 0)} | {_pct(s['recall_strict'])} "
                     f"| {_pct(s['recall_lenient'])} |")
    return "\n".join(lines)


def gate(result: dict[str, Any], baseline: dict[str, Any] | None, *,
         max_drop: float = 0.02, min_findings: int = 10) -> list[str]:
    """The AI-agreement gate, diagnostic only: failures when a rule's strict
    precision fell more than `max_drop` below the committed baseline (rules
    with fewer than `min_findings` findings in either run are too small to
    compare and are skipped). Agreement with the AI review is a lower bound,
    not accuracy, so it never decides inline; human_gate() does
    (DECISIONS.md, 2026-09-28)."""
    failures = []
    for rule_id, stats in result["rules"].items():
        precision = stats["precision_strict"]
        before = (baseline or {}).get("rules", {}).get(rule_id)
        if before and precision is not None and before.get("precision_strict") is not None \
                and stats.get("findings", 0) >= min_findings and before.get("findings", 0) >= min_findings \
                and precision < before["precision_strict"] - max_drop:
            failures.append(f"{rule_id} strict precision fell from {_pct(before['precision_strict'])} to {_pct(precision)}")
    return failures


def baseline_of(result: dict[str, Any]) -> dict[str, Any]:
    """The committed baseline: aggregate numbers only, no Scripture or review text."""
    keep = ("findings", "tp_strict", "fp_strict", "precision_strict", "precision_lenient", "inline")
    return {"packVersion": result["packVersion"], "books": result["books"],
            "rules": {rule: {k: s.get(k) for k in keep} for rule, s in result["rules"].items()},
            "buckets": {bucket: {k: s.get(k) for k in ("recall_strict", "recall_lenient", "rows_positive")}
                        for bucket, s in result["buckets"].items()}}


def labelled_examples(rows: list[ReviewRow], verses: dict[str, dict[str, dict[str, str]]], *,
                      per_bucket: int = 40) -> dict[str, list[dict[str, Any]]]:
    """Phase 2.4 fixtures, per bucket, deterministic and bounded:
    - every anchored negative row: {"text", "expect": [], "rejectedSpan"};
    - every anchored maybe row: {"text", "expect": [], "maybe": [{span, reason}]}
      -- a rule may or may not flag it; a test must not insist either way;
    - a strided sample of anchored positive rows: {"text", "expect": [{ruleId,
      category, span, fix}]}.
    ruleId is the current rule that found the row, or None where no rule
    does yet; the Phase 3 rule pack assigns its own."""
    out: dict[str, list[dict[str, Any]]] = {bucket: [] for bucket in BUCKETS}
    order = {"negative": 0, "maybe": 1, "positive": 2}
    for bucket in BUCKETS:
        candidates = sorted(
            (r for r in rows if r.bucket == bucket and r.anchored and r.label in order),
            key=lambda r: (order[r.label], r.book, int(r.chapter), r.verse, r.original))
        fixed = [r for r in candidates if r.label != "positive"]
        positives = [r for r in candidates if r.label == "positive"]
        stride = max(1, len(positives) // per_bucket) if positives else 1
        for row in fixed + positives[::stride][:per_bucket]:
            text = verses[row.book][row.chapter][row.verse]
            origin = f"{row.book.upper()} {row.chapter}:{row.verse} ({row.source})"
            if row.label == "negative":
                out[bucket].append({"text": text, "expect": [], "rejectedSpan": row.original, "origin": origin})
            elif row.label == "maybe":
                out[bucket].append({"text": text, "expect": [], "origin": origin,
                                    "maybe": [{"span": row.original, "fix": row.suggestion or None,
                                               "reason": row.reason}]})
            else:
                out[bucket].append({"text": text, "origin": origin, "expect": [{
                    "ruleId": next(iter(row.matched_by), None), "category": bucket,
                    "span": row.original, "fix": row.suggestion or None}]})
    return out


def pack_version_label(pack_name: str = HUMAN_PACK) -> str:
    """Both versions a result depends on: the in-code rules and the pack."""
    from .language_packs import default_pack
    return f"{PACK_VERSION}+{default_pack(pack_name).pack_version}"


# ---- human-labelled precision (2026-09-28 review) -------------------------
#
# A Tamil reviewer labelled a stratified sample of the engine's own findings
# on GEN, PSA and JHN (benchmark/human/<date>/human_labels.jsonl). Those
# verdicts are ground truth; the AI-agreement numbers above are diagnostic
# only. A rule may be drawn inline only on its human number
# (docs/DECISIONS.md, 2026-09-28).

HUMAN_INLINE_MIN_PRECISION = 0.90
HUMAN_INLINE_MIN_LABELLED = 20
HUMAN_EXCLUDED = {None, "", "UNSURE"}   # unanswered or "unsure": never scored
_COUNTS = ("labelled", "tp", "fp", "house", "lost_tp", "removed_fp", "removed_house", "reattributed", "excluded")


# Split rows (round 2): SPLIT is a real split to join; WORD and PARTICLE are a
# word or an interjection (சீ என்று) that must not be joined.
SPLIT_VERDICTS = {"SPLIT": "tp", "WORD": "fp", "PARTICLE": "fp"}


def human_verdict(code: str | None) -> str | None:
    """"tp" | "fp" | "house" for a scored verdict; None when excluded."""
    if code in HUMAN_EXCLUDED:
        return None
    if code in SPLIT_VERDICTS:
        return SPLIT_VERDICTS[code]
    if code.startswith("TP"):
        return "tp"
    if code.startswith("FP"):
        return "fp"
    if code == "HOUSE":
        return "house"
    return None


def load_human_labels(path: Path) -> list[dict[str, Any]]:
    with Path(path).open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def human_label_files(path: Path) -> list[Path]:
    """One round's labels (a file), or every round under a folder
    (benchmark/human/<date>/human_labels.jsonl), oldest first."""
    path = Path(path)
    return [path] if path.is_file() else sorted(path.glob("*/human_labels.jsonl"))


def load_human_rounds(path: Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, dict[str, str]]]]:
    """(labels tagged with their round, verses of every round). A round is
    the labels file's folder name (its review date). Verse text is the same
    IRV copy in every round; a verse labelled twice must read the same."""
    labels: list[dict[str, Any]] = []
    verses: dict[str, dict[str, dict[str, str]]] = defaultdict(lambda: defaultdict(dict))
    for file in human_label_files(path):
        round_name = file.parent.name
        labels += [{**label, "round": round_name} for label in load_human_labels(file)]
        for book, chapters in load_human_verses(human_verses_path(file)).items():
            for chapter, items in chapters.items():
                for verse, text in items.items():
                    known = verses[book][chapter].get(verse)
                    if known is not None and known != text:
                        raise ValueError(f"{book.upper()} {chapter}:{verse} differs between rounds")
                    verses[book][chapter][verse] = text
    return labels, {book: dict(chapters) for book, chapters in verses.items()}


def human_verses_path(labels_path: Path) -> Path:
    return Path(labels_path).with_name("verses.jsonl")


def load_human_verses(path: Path) -> dict[str, dict[str, dict[str, str]]]:
    """book -> chapter -> verse -> text, as an import writes it."""
    verses: dict[str, dict[str, dict[str, str]]] = defaultdict(lambda: defaultdict(dict))
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                verses[row["book"]][row["chapter"]][row["verse"]] = row["text"]
    return {book: dict(chapters) for book, chapters in verses.items()}


def human_label_verses(labels: list[dict[str, Any]], irv_dir: Path) -> list[dict[str, str]]:
    """The imported text of every verse a label points at, from the IRV SFM,
    so the human gate can run where the corpus is not available (CI)."""
    wanted = sorted({(r["book"], r["ch"], r["v"]) for r in labels if r.get("book")},
                    key=lambda k: (k[0], int(k[1]), k[2]))
    books = {w[0] for w in wanted}
    by_book: dict[str, dict[str, dict[str, str]]] = {}
    for sfm in sorted(Path(irv_dir).glob("*.SFM")):
        book, chapters = book_verses(sfm)
        if book in books:
            by_book[book] = chapters
    out = []
    for book, chapter, verse in wanted:
        text = by_book.get(book, {}).get(chapter, {}).get(verse)
        if text is None:
            raise ValueError(f"{book.upper()} {chapter}:{verse} is not in {irv_dir}")
        out.append({"book": book, "chapter": chapter, "verse": verse, "text": text})
    return out


def _anchor(label: dict[str, Any], text: str) -> tuple[int, int] | None:
    """The label's span in the verse. Offsets are raw code points from the
    engine that produced it; a pair across a poetry line has none (-1), and
    is anchored at its previous word."""
    start, end = label.get("start", -1), label.get("end", -1)
    if isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(text):
        return start, end
    for needle in (label.get("original") or "", label.get("prev") or ""):
        at = nfc(text).find(nfc(needle)) if needle else -1
        if at >= 0:
            return at, at + len(nfc(needle))
    return None


def human_score(labels: list[dict[str, Any]], scans: dict[str, dict[str, Any]],
                verses: dict[str, dict[str, dict[str, str]]], *, pack: str = HUMAN_PACK) -> dict[str, Any]:
    """Score the current findings against the reviewer's verdicts.

    - Flagged rows (the findings the reviewer judged): a current finding at
      exactly the label's book/chapter/verse/start/end, with the same text
      (NFC), is credited to the finding's own rule as TP, FP or house form.
      Precision = TP / (TP + FP) over labelled findings only; house forms are
      reported apart. A labelled finding the engine no longer produces is
      counted as lost (a TP) or removed (an FP or a house form).
    - Abstained rows (contexts the pack skipped on purpose): recall proxies
      per class -- whether a sandhi finding now overlaps the pair. They are
      proxies, not recall: the sample is of the abstains, not of the text."""
    findings_at: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for book, scan in scans.items():
        for finding in scan["findings"]:
            findings_at[(book, finding["chapter"], finding["verse"])].append(finding)
    rules: dict[str, Counter] = defaultdict(Counter)
    per_round: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
    proxies: dict[str, Counter] = defaultdict(Counter)
    mismatches: list[str] = []
    for label in labels:
        kind = label.get("kind")
        if kind not in {"flagged", "abstained", "split"}:
            continue  # word rows (sheet 3) judge list membership, not a finding
        round_name = label.get("round", "")
        where = f"{round_name + ' ' if round_name else ''}{label['id']} {label['book'].upper()} {label['ch']}:{label['v']}"
        text = verses.get(label["book"], {}).get(label["ch"], {}).get(label["v"])
        if text is None:
            mismatches.append(f"{where}: verse not available")
            continue
        here = findings_at.get((label["book"], label["ch"], label["v"]), [])
        if kind == "split":
            # A candidate join the reviewer judged. Scored like a flagged item
            # when a finding covers exactly its span; otherwise a recall proxy.
            verdict = human_verdict(label.get("verdict"))
            span = (label.get("start", -1), label.get("end", -1))
            hit = next((f for f in here if (f["start"], f["end"]) == span), None)
            if verdict is None:
                proxies["split"]["excluded"] += 1
            elif hit is not None:
                for stats in (rules[hit["ruleId"]], per_round[hit["ruleId"]][round_name]):
                    stats["labelled"] += 1
                    stats[verdict] += 1
            else:
                stats = proxies["split"]
                stats["missed" if verdict == "tp" else "correct_skip"] += 1
            continue
        if kind == "flagged":
            verdict = human_verdict(label.get("verdict"))
            if verdict is None:
                rules[label["rule"]]["excluded"] += 1
                continue
            span = (label["start"], label["end"])
            if nfc(text[span[0]:span[1]]) != nfc(label["original"]):
                mismatches.append(f"{where}: label text does not match the verse at {span}")
                continue
            hit = next((f for f in here if (f["start"], f["end"]) == span
                        and nfc(f["originalText"]) == nfc(label["original"])), None)
            if hit is None:
                rules[label["rule"]][{"tp": "lost_tp", "fp": "removed_fp", "house": "removed_house"}[verdict]] += 1
                continue
            for stats in (rules[hit["ruleId"]], per_round[hit["ruleId"]][round_name]):
                stats["labelled"] += 1
                stats[verdict] += 1
            if hit["ruleId"] != label["rule"]:
                rules[hit["ruleId"]]["reattributed"] += 1
        else:
            key = {"MISSED": "missed", "CORRECT_SKIP": "correct_skip", "HOUSE": "house"}.get(label.get("verdict"))
            stats = proxies[label.get("cls") or "unknown"]
            if key is None:
                stats["excluded"] += 1
                continue
            anchor = _anchor(label, text)
            stats[key] += 1
            if anchor is not None and any(
                    f.get("category") in {"sandhi", "word-joining"} and f["start"] < anchor[1] and anchor[0] < f["end"]
                    for f in here):
                stats[f"{key}_now_flagged"] += 1

    def ratio(numerator: int, denominator: int) -> float | None:
        return round(numerator / denominator, 4) if denominator else None

    inline = human_inline_rules(pack)
    for rule_id in inline:
        rules.setdefault(rule_id, Counter())
    rules_out = {}
    for rule_id, s in sorted(rules.items()):
        rules_out[rule_id] = {**{k: s.get(k, 0) for k in _COUNTS},
                              "precision": ratio(s["tp"], s["tp"] + s["fp"]), "inline": rule_id in inline,
                              "rounds": {name: {"labelled": r["labelled"], "tp": r["tp"], "fp": r["fp"],
                                                "precision": ratio(r["tp"], r["tp"] + r["fp"])}
                                         for name, r in sorted(per_round.get(rule_id, {}).items())}}
    proxies_out = {}
    for cls, s in sorted(proxies.items()):
        proxies_out[cls] = {
            **{k: s.get(k, 0) for k in ("missed", "missed_now_flagged", "correct_skip",
                                        "correct_skip_now_flagged", "house", "house_now_flagged", "excluded")},
            "still_missed_rate": ratio(s["missed"] - s["missed_now_flagged"],
                                       s["missed"] + s["correct_skip"] + s["house"]),
        }
    rounds = sorted({label.get("round", "") for label in labels} - {""})
    return {"packVersion": pack_version_label(pack), "labels": len(labels), "rounds": rounds,
            "books": sorted(scans), "rules": rules_out, "recallProxies": proxies_out,
            "mismatches": mismatches}


# ---- (b) a whole population labelled (DECISIONS.md 2026-09-29) -------------
#
# A rule whose whole-collection population is under 20 can never reach
# HUMAN_INLINE_MIN_LABELLED. It may be inline instead when every finding it
# produces in the collection has a confirming human label and none of its
# labels is wrong. The population is written locally from the full corpus
# (--write-population), because CI has no corpus; each rule's entry carries a
# hash of the rule's definition, and a changed definition makes it stale.

POPULATION_MAX = 100          # rules with more findings than this are not recorded
CONFIRMING = {"tp"}           # human_verdict()s that confirm a finding
CONFIRMING_CODES = {"MISSED"}  # an abstained row that says "this needs changing"


def rule_definition_hash(rule_id: str) -> str | None:
    """What decides a rule's findings, without its inline flag, examples or
    prose. A pack rule hashes its source; an in-code rule its revision."""
    import hashlib
    from .language_packs import default_pack
    from .language_qa import RULES, RULE_VERSION
    pack = default_pack(HUMAN_PACK)
    pack_name, _, name = rule_id.partition("/")
    rule = pack.by_id(name) if pack_name == pack.name else None
    if rule is not None and rule.legacy_version:
        # A rule moved out of the engine's code keeps the identity it had
        # there (loader: legacyVersion), so its population stays current.
        extra = ""
        if rule.params.get("check") == "known-split":  # the rule is its curated map
            extra = json.dumps(getattr(pack.lexicon(), "splits", {}), ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(f"{rule.legacy_version}#{rule.version}#{rule.name}#{extra}".encode("utf-8")).hexdigest()
    if rule is not None:
        source = {k: v for k, v in pack.by_id(name).source.items()
                  if k not in {"inline", "examples", "provenance", "title", "message", "rationale"}}
        return hashlib.sha1(json.dumps(source, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    if name in RULES:
        return hashlib.sha1(f"{RULE_VERSION}#{RULES[name].revision}#{name}#".encode("utf-8")).hexdigest()
    return None


def population_of(scans: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Every finding of each small rule (at most POPULATION_MAX) in the scans."""
    found: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for book, scan in sorted(scans.items()):
        for f in scan["findings"]:
            found[f["ruleId"]].append({"book": book, "chapter": f["chapter"], "verse": f["verse"],
                                       "start": f["start"], "end": f["end"], "text": f["originalText"]})
    return {"packVersion": pack_version_label(), "books": len(scans),
            "rules": {rule: {"definition": rule_definition_hash(rule), "findings": items}
                      for rule, items in sorted(found.items()) if len(items) <= POPULATION_MAX}}


def population_coverage(population: dict[str, Any] | None, labels: list[dict[str, Any]]) -> dict[str, Any]:
    """Per recorded rule: its population size, how many findings a human label
    confirms (a TP / SPLIT label, or an abstained MISSED row, overlapping it at
    the same verse), and whether the entry is current."""
    out: dict[str, Any] = {}
    by_verse: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for label in labels:
        if label.get("book"):
            by_verse[(label["book"], label["ch"], label["v"])].append(label)
    for rule, entry in ((population or {}).get("rules") or {}).items():
        confirmed = 0
        for f in entry["findings"]:
            for label in by_verse.get((f["book"], f["chapter"], f["verse"]), []):
                start, end = label.get("start", -1), label.get("end", -1)
                overlaps = isinstance(start, int) and start >= 0 and start < f["end"] and f["start"] < end
                confirms = human_verdict(label.get("verdict")) in CONFIRMING or label.get("verdict") in CONFIRMING_CODES
                if overlaps and confirms:
                    confirmed += 1
                    break
        out[rule] = {"total": len(entry["findings"]), "confirmed": confirmed,
                     "current": entry.get("definition") == rule_definition_hash(rule)}
    return out


def human_inline_rules(pack_name: str = HUMAN_PACK) -> set[str]:
    """ruleIds drawn inline, from the pack and the in-code rules. The
    project's own data (house style, termbase: pack "project") is not a rule
    the benchmark can label, and is not gated. Another language's rounds
    (benchmark/human/<code>/) gate only that pack's own rules: the common
    rules are gated by the Tamil rounds, which label them."""
    from .language_packs import default_pack
    from .language_qa import INLINE_RULES, RULES
    pack = default_pack(pack_name)
    inline = {f"{pack.name}/{r.id}" for r in pack.rules if r.enabled and r.inline}
    if pack_name == HUMAN_PACK:
        inline |= {f"{RULES[name].pack}/{name}" for name in INLINE_RULES if RULES[name].pack != "project"}
    return inline


def human_gate(result: dict[str, Any], baseline: dict[str, Any] | None) -> list[str]:
    """Failures, empty when the gate passes:
    - an inline rule below HUMAN_INLINE_MIN_PRECISION, or with fewer than
      HUMAN_INLINE_MIN_LABELLED labelled findings;
    - a rule whose human precision in a review round fell below that
      round's committed baseline. Each round is compared with itself: a new
      round changes the combined denominator, which is not a regression, so
      inline reads the combined figure and regression reads the rounds;
    - a human-confirmed finding the engine no longer produces;
    - a label that no longer anchors in its verse (the text or the offsets
      moved, so the score would shrink without anyone noticing)."""
    failures = [f"label not scored: {m}" for m in result["mismatches"]]
    population = result.get("population") or {}
    for rule_id, s in result["rules"].items():
        precision = s["precision"]
        whole = population.get(rule_id)
        if s["inline"] and s["labelled"] < HUMAN_INLINE_MIN_LABELLED and whole is not None:
            # (b): the whole collection's findings are labelled, and none is wrong.
            if not whole["current"]:
                failures.append(f"{rule_id} is inline on its whole population, but the rule changed since "
                                f"benchmark/human/population.json was written (--write-population)")
            elif whole["confirmed"] < whole["total"] or s["fp"]:
                failures.append(f"{rule_id} is inline on its whole population, but {whole['confirmed']} of "
                                f"{whole['total']} findings are confirmed and {s['fp']} labelled wrong")
        elif s["inline"]:
            if s["labelled"] < HUMAN_INLINE_MIN_LABELLED:
                failures.append(f"{rule_id} is inline with {s['labelled']} human-labelled findings "
                                f"(< {HUMAN_INLINE_MIN_LABELLED})")
            elif precision is None or precision < HUMAN_INLINE_MIN_PRECISION:
                failures.append(f"{rule_id} is inline but human precision is {_pct(precision)} "
                                f"(< {_pct(HUMAN_INLINE_MIN_PRECISION)})")
        if s["lost_tp"]:
            failures.append(f"{rule_id}: {s['lost_tp']} human-confirmed finding(s) no longer produced")
        recorded = (baseline or {}).get("rules", {}).get(rule_id, {})
        if not result.get("rounds"):
            # Unnamed labels (one round): compare the combined figure.
            before = recorded.get("precision")
            if before is not None and precision is not None and precision < before:
                failures.append(f"{rule_id} human precision fell from {_pct(before)} to {_pct(precision)}")
            continue
        before_rounds = recorded.get("rounds")
        if not before_rounds and recorded.get("precision") is not None:
            # A baseline from before rounds existed holds its first round only.
            first = ((baseline or {}).get("rounds") or result["rounds"])[0]
            before_rounds = {first: {"precision": recorded["precision"]}}
        for name, before in (before_rounds or {}).items():
            now = (s.get("rounds") or {}).get(name, {}).get("precision")
            if before.get("precision") is not None and now is not None and now < before["precision"]:
                failures.append(f"{rule_id} human precision in round {name} fell from "
                                f"{_pct(before['precision'])} to {_pct(now)}")
    return failures


def human_baseline_of(result: dict[str, Any]) -> dict[str, Any]:
    keep = ("labelled", "tp", "fp", "house", "precision", "inline")
    return {"packVersion": result["packVersion"], "rounds": result.get("rounds") or [],
            "books": result["books"],
            "rules": {rule: {**{k: s.get(k) for k in keep}, "rounds": s.get("rounds") or {}}
                      for rule, s in result["rules"].items()},
            "recallProxies": {cls: {k: s.get(k) for k in ("missed", "missed_now_flagged", "still_missed_rate")}
                              for cls, s in result["recallProxies"].items()}}


def human_precision_table(result: dict[str, Any], pack_name: str) -> dict[str, Any]:
    """The pack's `rule_precision.json`: each of its rules that reviewers
    labelled, with the human precision and the label count. It ships with the
    pack, so the engine can apply a user's inline threshold. Aggregates only,
    no Scripture or review text. A rule with no label is left out, and the
    engine treats a missing rule as unmeasured."""
    prefix = f"{pack_name}/"
    rules = {rule_id: {"precision": s["precision"], "labelled": s.get("labelled", 0)}
             for rule_id, s in sorted(result["rules"].items())
             if rule_id.startswith(prefix) and s.get("labelled") and s.get("precision") is not None}
    return {"packVersion": result["packVersion"], "labelsFile": result.get("labelsFile", ""),
            "generatedAt": result.get("generatedAt", ""), "rules": rules}


def human_markdown(result: dict[str, Any]) -> str:
    lines = [
        f"Pack version `{result['packVersion']}`; {len(result['books'])} books; "
        f"{result['labels']} label rows over {len(result.get('rounds') or [1])} review round(s) "
        f"({', '.join(result.get('rounds') or [])}). The gate reads the combined numbers.",
        "",
        "| Rule | Inline | Labelled | TP | FP | House form | Human precision (combined) | Per round (TP/labelled) "
        "| Lost TP | FP no longer produced |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for rule_id, s in result["rules"].items():
        rounds = "; ".join(f"{name}: {r['tp']}/{r['labelled']}" for name, r in (s.get("rounds") or {}).items()) or "—"
        lines.append(f"| `{rule_id}` | {'yes' if s['inline'] else 'no'} | {s['labelled']} | {s['tp']} | {s['fp']} "
                     f"| {s['house']} | {_pct(s['precision'])} | {rounds} | {s['lost_tp']} | {s['removed_fp']} |")
    lines += [
        "",
        "Recall proxies over the contexts the pack skipped on purpose (a sample of the abstains, not of "
        "the text, so these are not recall):",
        "",
        "| Abstain class | Reviewer: missed | now flagged | Reviewer: correct skip | now flagged "
        "| House form | now flagged | Still-missed rate |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for cls, s in result["recallProxies"].items():
        lines.append(f"| {cls} | {s['missed']} | {s['missed_now_flagged']} | {s['correct_skip']} "
                     f"| {s['correct_skip_now_flagged']} | {s['house']} | {s['house_now_flagged']} "
                     f"| {_pct(s['still_missed_rate'])} |")
    covered = {rule: c for rule, c in (result.get("population") or {}).items()
               if c["total"] and result["rules"].get(rule, {}).get("labelled", 0) < HUMAN_INLINE_MIN_LABELLED}
    if covered:
        lines += ["", "Whole-population coverage for rules under 20 labels (inline rule (b): every finding in the "
                  "collection confirmed, none wrong):", ""]
        lines += [f"- `{rule}`: {c['confirmed']} of {c['total']} confirmed" + ("" if c["current"] else " (stale)")
                  for rule, c in sorted(covered.items())]
    if result["mismatches"]:
        lines += ["", "Labels not scored: " + "; ".join(result["mismatches"])]
    return "\n".join(lines)
