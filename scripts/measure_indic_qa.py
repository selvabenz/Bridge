"""Measure a profile pack against indic-qa itself, on one real IRV book.

    python scripts/measure_indic_qa.py hi "D:\\Claude Lab\\IRV Hindi" GEN

Two runs over the same book:

- **indic-qa, as its editor runs it:** every book of the folder indexed with
  indic-qa's own USFM parser, then `check_chapter` over the book. This is
  the reference, and only this measurement script reads USFM this way; the
  engine never does (CLAUDE.md gotcha 14).
- **Bridge:** the book imported through Bridge's importer, then one real
  `LanguageQaManager` pass with the project declaring the language.

The reference indexes and checks verse text only, like Bridge, so the word
counts behind every majority call match. It reports whether the verse-text
findings are the same multiset of
(chapter, verse, rule, text), what indic-qa found outside verse text (headings,
footnotes: Bridge's Language QA reads verses only), the Bridge pass time
(first pass includes loading the pack), check_chapter time per chapter, and
the process's peak working set on Windows. Read-only: nothing is written.
"""
from __future__ import annotations

import collections
import ctypes
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "engine"))

from tc_ai_bridge.language_packs import indic_qa_vendor  # noqa: E402
from tc_ai_bridge.language_packs.registry import packs_dir  # noqa: E402
from tc_ai_bridge.language_qa_benchmark import book_verses  # noqa: E402
from tc_ai_bridge.language_qa_jobs import LanguageQaManager  # noqa: E402

DECLARED = {"hi": "hin", "ml": "mal", "or": "ory", "pa": "pan"}


def peak_mb() -> float | None:
    if sys.platform != "win32":
        return None

    class Counters(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
    counters = Counters()
    counters.cb = ctypes.sizeof(Counters)
    kernel = ctypes.windll.kernel32
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    psapi = ctypes.windll.psapi.GetProcessMemoryInfo
    psapi.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
    if not psapi(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        return None
    return round(counters.PeakWorkingSetSize / 2**20, 1)


def native(code: str, folder: Path, book_code: str, *, verses_only: bool
           ) -> tuple[collections.Counter, collections.Counter]:
    """(verse findings, findings elsewhere by context) from indic-qa itself.
    `verses_only`: index and check verse text only, as Bridge reads it, so
    the word counts behind every majority call are the same; the comparison
    is then like for like. Without it, indic-qa's own default contexts."""
    mods = indic_qa_vendor.modules()
    lang = mods.langs.get(code)
    profile = indic_qa_vendor.profile(code)
    switch = getattr(profile, "SHAPE_RULE_SWITCH", {})
    status_rule = getattr(profile, "STATUS_RULE", {})
    generic = getattr(profile, "GENERIC_WARNING_RULE", {})
    rules = json.loads((packs_dir() / f"{code}-irv" / "rule_versions.json").read_text(encoding="utf-8"))["rules"]
    checker = mods.checker.Checker(mods.checker.Lexicon.load(packs_dir() / f"{code}-irv" / "dictionary", lang),
                                   {"rules": {r: v["enabled"] for r, v in rules.items()},
                                    **({"checked_contexts": ["verse"]} if verses_only else {})}, lang)
    books = {}
    for path in sorted(p for p in folder.iterdir() if p.suffix.lower() in (".sfm", ".usfm")):
        lines = path.read_bytes().decode("utf-8").replace("\r\n", "\n").split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        book = mods.usfm_doc.parse_book(path.stem[-3:] if "-" in path.stem else path.stem[2:5], lines)
        checker.index_book(book.code, book)
        books[book.code] = book
    checker.build_index()
    verses, elsewhere = collections.Counter(), collections.Counter()
    book = books[book_code]
    for n in range(1, len(book.chapters)):
        for block in checker.check_chapter(book, n):
            for seg in block["segs"]:
                if seg.get("k") != "t" or not seg.get("checked"):
                    continue
                items = ([(t.get("rule") or status_rule.get(t["status"], ""), t) for t in seg.get("tokens", ())
                          if t["status"] in {"malformed", "suspect", "archaic", "rare_near_common", "unknown"}]
                         + [(lead.get("rule", ""), lead) for lead in seg.get("sandhi", ())]
                         + [(w.get("rule") or generic.get(w["kind"], ""), w) for w in seg.get("warnings", ())])
                for rule, item in items:
                    rule = switch.get(rule, rule)
                    if not rules.get(rule, {}).get("enabled"):
                        continue
                    if seg["ctx"] == "verse":
                        verse = block["ref"].split(":")[1]
                        verses[(str(n), verse, rule, seg["t"][item["s"]:item["e"]])] += 1
                    else:
                        elsewhere[(seg["ctx"], rule)] += 1
    return verses, elsewhere


def bridge(code: str, sfm: Path) -> tuple[collections.Counter, list[float], int]:
    book, chapters = book_verses(sfm)
    walls = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        folder = root / book
        folder.mkdir()
        for chapter, verses in chapters.items():
            (folder / f"{chapter}.json").write_text(json.dumps(verses, ensure_ascii=False), encoding="utf-8")
        project = SimpleNamespace(path=root, book_id=book, book_dir=folder,
                                  manifest={"target_language": {"id": DECLARED[code]}},
                                  terminology_rules=lambda: [], housestyle_entries=lambda: [])
        for _ in range(2):
            manager = LanguageQaManager(debounce=0, yield_seconds=0)
            started = time.perf_counter()
            manager.bind(project)
            while manager.status()["state"] not in {"completed", "failed"}:
                time.sleep(0.01)
            walls.append(time.perf_counter() - started)
            status = manager.status()
            findings = [f for o in range(0, status["totalFindings"], 100)
                        for f in manager.status(offset=o, limit=100)["findings"]]
            manager.unbind()
    assert status["language"]["pack"] == f"{code}-irv", status["language"]
    # Bridge's own rule over the general corpus is not indic-qa's to find: it
    # is reported beside the parity, never inside it (#246).
    slips = [f for f in findings if f["rule"].endswith(".lex.irv-consistent-slip")]
    pack_findings = [f for f in findings if f["ruleId"].startswith(f"{code}-irv/") and f not in slips]
    found = collections.Counter((f["chapter"], f["verse"], f["rule"], f["originalText"]) for f in pack_findings)
    accepted = next((int(n.split()[0]) for n in status.get("limitations", ())
                     if "accepted from the general corpus" in n), 0)
    global CORPUS_REPORT
    CORPUS_REPORT = {"acceptedByCorpus": accepted, "irvConsistentSlips": len(slips),
                     "irvConsistentSlipWords": sorted({f["originalText"] for f in slips})[:50]}
    return found, walls, len(findings)


CORPUS_REPORT: dict = {}


def chapter_times(code: str, sfm: Path) -> list[float]:
    """check_chapter per chapter, on the resident checker after a pass."""
    from tc_ai_bridge.language_packs import indic_qa_adapter as adapter
    from tc_ai_bridge.language_qa import MAX_VERSE_CHARS, lift_inline_usfm
    book, chapters = book_verses(sfm)
    resident = adapter._RESIDENT
    synthetic, _refs, _notes = adapter.build_book(book, chapters, lift=lift_inline_usfm,
                                                  max_verse_chars=MAX_VERSE_CHARS)
    times = []
    for n in range(1, len(synthetic.chapters)):
        started = time.perf_counter()
        resident.checker.check_chapter(synthetic, n)
        times.append((time.perf_counter() - started) * 1000)
    return times


def main() -> int:
    # --without-corpus: the checker alone, as indic-qa runs it, so the parity
    # comparison below is between the same two things (#246).
    argv = [a for a in sys.argv[1:] if a != "--without-corpus"]
    if len(argv) != len(sys.argv) - 1:
        from tc_ai_bridge.language_packs import general_corpus
        general_corpus.DISABLED = True
    code, folder, book_code = argv[0], Path(argv[1]), argv[2].upper()
    sfm = next(p for p in sorted(folder.iterdir()) if p.suffix.lower() in (".sfm", ".usfm")
               and book_code in p.stem.upper())
    found, walls, total = bridge(code, sfm)
    times = chapter_times(code, sfm)
    reference, _ = native(code, folder, book_code, verses_only=True)
    _, elsewhere = native(code, folder, book_code, verses_only=False)
    missing, extra = reference - found, found - reference
    # Whitespace between verses (e.g. a line starting "  \q \v 2") is the end
    # of the previous verse in Bridge's import and the start of the next line
    # in indic-qa's own reading: the same finding under a neighbouring verse.
    by_chapter = lambda c: collections.Counter({(ch, rule, text): n for (ch, _v, rule, text), n in c.items()})  # noqa: E731
    chapter_missing, chapter_extra = by_chapter(missing) - by_chapter(extra), by_chapter(extra) - by_chapter(missing)
    result = {
        "language": code, "book": book_code,
        "bridgePassSeconds": {"first": round(walls[0], 2), "second": round(walls[1], 2)},
        "checkChapterMs": {"p50": round(statistics.median(times), 1),
                           "p95": round(sorted(times)[int(len(times) * 0.95) - 1], 1), "max": round(max(times), 1)},
        "peakWorkingSetMB": peak_mb(),
        "bridgeFindings": {"pack": sum(found.values()), "all": total},
        "indicQaVerseFindings": sum(reference.values()),
        "verseParity": not missing and not extra,
        "missingFromBridge": sum(missing.values()), "onlyInBridge": sum(extra.values()),
        "ignoringVerse": {"missingFromBridge": sum(chapter_missing.values()),
                          "onlyInBridge": sum(chapter_extra.values()),
                          "missingByRule": dict(collections.Counter(r for (_c, r, _t) in chapter_missing.elements()))},
        "bookCapReached": total >= 3000,
        "indicQaOutsideVerses": {f"{ctx} {rule}": n for (ctx, rule), n in sorted(elsewhere.items())},
        "byRule": dict(collections.Counter(rule for (_c, _v, rule, _t), n in found.items() for _ in range(n))
                       .most_common()),
        "generalCorpus": CORPUS_REPORT,
    }
    if missing or extra:
        result["examples"] = {"missing": [list(k) for k in list(missing)[:10]],
                              "extra": [list(k) for k in list(extra)[:10]]}
    print(json.dumps(result, ensure_ascii=False, indent=1))
    # With the general corpus on, the words it accepted are the only expected
    # difference from indic-qa's own run (#246 review): not a parity failure.
    accepted_only = (not result["onlyInBridge"]
                     and result["missingFromBridge"] == CORPUS_REPORT.get("acceptedByCorpus", 0))
    return 0 if result["verseParity"] or accepted_only else 1


if __name__ == "__main__":
    raise SystemExit(main())
