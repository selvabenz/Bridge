"""Language QA accuracy benchmark (layered-rules brief, Phase 2).

    python scripts/language_qa_benchmark.py --irv-dir "D:/…/IRV Tamil" \
        --reviews "D:/…/Claude outputs" "C:/…/Philippians_Round2_QA_Issues.csv" \
        [--gate] [--write-baseline] [--update-doc] [--write-labelled]

Run it with the engine's interpreter (engine/.venv). It reads the IRV SFM
books and the review CSVs, scans every reviewed book with the app's own
Language QA, and prints per-rule precision and per-bucket recall. The full
result (with verse text and review rows, for a human to label the
unmatched findings) is written to benchmark/results/, which is not
committed; only aggregate numbers go into benchmark/baseline.json and
docs/LANGUAGE_QA_BENCHMARK.md. See that doc for what the numbers mean.

Two modes, one gate each:

- AI agreement (--irv-dir, --reviews): precision is agreement with the
  Round 2 / Pass 3 AI review rows, a lower bound, so it is diagnostic only.
  --gate fails only when a rule's strict precision fell more than 2 points
  below benchmark/baseline.json. It is local: the IRV text and the review
  reports live outside the repository.
- Human labels (--human-labels benchmark/human/<date>/human_labels.jsonl):
  precision against a Tamil reviewer's verdicts, the only number that may
  put a rule inline (DECISIONS.md, 2026-09-28). --gate fails an inline rule
  below 0.90 human precision or with fewer than 20 labelled findings, any
  rule below benchmark/human/baseline.json, a human-confirmed finding no
  longer produced, and a label that no longer anchors. The labelled verses
  are committed beside the labels (verses.jsonl), so CI runs this gate.

    python scripts/language_qa_benchmark.py \
        --human-labels benchmark/human/2026-09-28/human_labels.jsonl --gate
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "engine"))

from tc_ai_bridge import language_qa_benchmark as bench  # noqa: E402
from tc_ai_bridge.language_packs.registry import select_pack  # noqa: E402

DOC = REPO / "docs" / "LANGUAGE_QA_BENCHMARK.md"
START, END = "<!-- benchmark:start -->", "<!-- benchmark:end -->"


def review_files(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for value in paths:
        path = Path(value)
        # Top level only: v1-before-split/ holds superseded reports.
        files.extend(sorted(path.glob("*_Issues.csv")) if path.is_dir() else [path])
    return files


ID_LINE = re.compile(r"\\id\s+([0-9A-Za-z]{3})")


def sfm_by_book(irv_dir: Path) -> dict[str, Path]:
    """Book code -> SFM file, from each file's own \\id line."""
    found: dict[str, Path] = {}
    for sfm in sorted(irv_dir.glob("*.SFM")) + sorted(irv_dir.glob("*.usfm")):
        with sfm.open(encoding="utf-8-sig", errors="replace") as handle:
            match = ID_LINE.search(handle.read(512))
        if match:
            found.setdefault(match.group(1).lower(), sfm)
    return found


HUMAN_BASELINE = REPO / "benchmark" / "human" / "baseline.json"
HUMAN_POPULATION = REPO / "benchmark" / "human" / "population.json"
HUMAN_START, HUMAN_END = "<!-- human-benchmark:start -->", "<!-- human-benchmark:end -->"


def human_main(args: argparse.Namespace) -> int:
    """--human-labels: precision against the reviewer's verdicts. Needs no IRV
    corpus (the labelled verses are committed beside the labels), so CI runs it."""
    # A labels file is one round; a folder (benchmark/human) is every round in it.
    if args.write_human_verses:
        if not args.irv_dir:
            raise SystemExit("--write-human-verses needs --irv-dir")
        for file in bench.human_label_files(args.human_labels):
            rows = bench.human_label_verses(bench.load_human_labels(file), args.irv_dir)
            verses_path = bench.human_verses_path(file)
            with verses_path.open("w", encoding="utf-8", newline="\n") as handle:
                for row in rows:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(f"{len(rows)} verses written to {verses_path}", file=sys.stderr)
    labels, verses = bench.load_human_rounds(args.human_labels)
    if args.write_population:
        if not args.irv_dir:
            raise SystemExit("--write-population needs --irv-dir")
        corpus = {}
        for sfm in sfm_by_book(args.irv_dir).values():
            book, chapters = bench.book_verses(sfm)
            corpus[book] = bench.scan_book(book, chapters)
        population = bench.population_of(corpus)
        population["corpus"] = args.irv_dir.name
        HUMAN_POPULATION.write_text(json.dumps(population, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"population of {len(population['rules'])} rules written to {HUMAN_POPULATION}", file=sys.stderr)
    # Another language's rounds live in benchmark/human/<code>/ (a book id
    # alone cannot tell Hindi Genesis from Tamil Genesis); --language picks the
    # pack they are scored with. Its baseline sits beside its rounds, and the
    # whole-population file is Tamil's.
    tamil = args.language == "tam"
    pack = bench.HUMAN_PACK if tamil else select_pack(args.language)
    if not pack:
        raise SystemExit(f"--language {args.language}: no Language QA pack is registered for it")
    scans = {book: bench.scan_book(book, chapters, language=args.language) for book, chapters in verses.items()}
    result = bench.human_score(labels, scans, verses, pack=pack)
    population = (json.loads(HUMAN_POPULATION.read_text(encoding="utf-8"))
                  if tamil and HUMAN_POPULATION.exists() else None)
    result["population"] = bench.population_coverage(population, labels)
    result["generatedAt"] = dt.datetime.now().isoformat(timespec="seconds")
    result["labelsFile"] = args.human_labels.as_posix()
    table = bench.human_markdown(result)
    print(table)
    baseline_path = (args.baseline if args.baseline != REPO / "benchmark" / "baseline.json"
                     else HUMAN_BASELINE if tamil else Path(args.human_labels) / "baseline.json")
    if args.update_doc:
        doc = DOC.read_text(encoding="utf-8")
        block = (f"{HUMAN_START}\n_Generated {result['generatedAt']} by scripts/language_qa_benchmark.py "
                 f"--human-labels {result['labelsFile']}._\n\n{table}\n{HUMAN_END}")
        head, _, rest = doc.partition(HUMAN_START)
        _, _, tail = rest.partition(HUMAN_END)
        DOC.write_text(head + block + tail, encoding="utf-8")
        print(f"updated {DOC}", file=sys.stderr)
    baseline = json.loads(baseline_path.read_text(encoding="utf-8")) if baseline_path.exists() else None
    if args.write_baseline:
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(json.dumps(bench.human_baseline_of(result), ensure_ascii=False, indent=1) + "\n",
                                 encoding="utf-8")
        print(f"human baseline written to {baseline_path}", file=sys.stderr)
    if args.gate:
        failures = bench.human_gate(result, baseline)
        for failure in failures:
            print(f"GATE: {failure}", file=sys.stderr)
        print("human gate: " + ("FAIL" if failures else "pass"), file=sys.stderr)
        return 1 if failures else 0
    return 0


def main() -> int:
    # The tables carry Tamil text and "—"; a Windows pipe defaults to cp1252.
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--irv-dir", type=Path, help="the IRV SFM folder (AI-agreement mode, --write-human-verses)")
    parser.add_argument("--reviews", nargs="+", help="review CSVs or folders (AI-agreement mode)")
    parser.add_argument("--human-labels", type=Path,
                        help="human_labels.jsonl: score against the reviewer's verdicts instead (the gate "
                             "that decides inline); verse text comes from verses.jsonl beside it")
    parser.add_argument("--write-human-verses", action="store_true",
                        help="with --human-labels and --irv-dir: write verses.jsonl from the SFM")
    parser.add_argument("--write-population", action="store_true",
                        help="with --human-labels and --irv-dir: scan every book and write "
                             "benchmark/human/population.json, each small rule's whole-collection findings "
                             "(inline rule (b), DECISIONS.md 2026-09-29)")
    parser.add_argument("--language", default="tam",
                        help="with --human-labels: the rounds' language (default tam). Another language's "
                             "rounds are benchmark/human/<code>/, scored with that language's pack")
    parser.add_argument("--books", nargs="*", help="limit to these book codes")
    parser.add_argument("--out-dir", type=Path, default=REPO / "benchmark" / "results")
    parser.add_argument("--baseline", type=Path, default=REPO / "benchmark" / "baseline.json")
    parser.add_argument("--gate", action="store_true")
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--update-doc", action="store_true")
    parser.add_argument("--write-labelled", action="store_true")
    parser.add_argument("--housestyle", type=Path,
                        help="a project whose house style is applied; what it hides is reported, not scored, "
                             "and its false-positive marks are exported as reviewer-labelled negatives")
    args = parser.parse_args()
    if args.human_labels:
        return human_main(args)
    if not args.irv_dir or not args.reviews:
        parser.error("--irv-dir and --reviews are required unless --human-labels is given")

    housestyle: list = []
    if args.housestyle:
        from tc_ai_bridge.tc_project import TranslationCoreProject
        styled = TranslationCoreProject(args.housestyle)
        housestyle = styled.housestyle_entries()
        negatives = [
            {"book": styled.book_id, "chapter": d.get("chapter"), "verse": d.get("verse"),
             "ruleId": (d.get("issue") or {}).get("ruleId"), "originalText": (d.get("issue") or {}).get("originalText"),
             "decidedAt": d.get("modifiedTimestamp")}
            for d in styled.project_qa_decisions()
            if d.get("decision") == "rejected" and (d.get("issue") or {}).get("source") == "languageQa"
        ]
        args.out_dir.mkdir(parents=True, exist_ok=True)
        negatives_path = args.out_dir / f"{dt.date.today().isoformat()}-reviewer-negatives.jsonl"
        negatives_path.write_text("".join(json.dumps(n, ensure_ascii=False) + "\n" for n in negatives), encoding="utf-8")
        print(f"house style: {len(housestyle)} entries; {len(negatives)} reviewer-labelled negatives -> "
              f"{negatives_path}", file=sys.stderr)

    rows = bench.load_review_rows(review_files(args.reviews))
    wanted = {b.lower() for b in args.books} if args.books else None
    books = sorted({r.book for r in rows if r.book in bench.BOOK_NAMES and (wanted is None or r.book in wanted)})
    sfms = sfm_by_book(args.irv_dir)
    verses, scans = {}, {}
    for book in books:
        if book not in sfms:
            print(f"warning: no SFM for {book.upper()} in {args.irv_dir}; its rows are not scored", file=sys.stderr)
            continue
        _, chapters = bench.book_verses(sfms[book])
        verses[book] = chapters
        scans[book] = bench.scan_book(book, chapters, housestyle=housestyle)
        print(f"scanned {book.upper()}: {scans[book]['checkedVerses']} verses, "
              f"{len(scans[book]['findings'])} findings, {scans[book]['wall']:.2f}s", file=sys.stderr)
    result = bench.score(rows, scans, verses)
    result["generatedAt"] = dt.datetime.now().isoformat(timespec="seconds")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.date.today().isoformat()
    out = args.out_dir / f"{stamp}-{result['packVersion']}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    table = bench.markdown_tables(result)
    print(table)
    print(f"\nfull result: {out}", file=sys.stderr)

    if args.write_labelled:
        target = REPO / "engine" / "tests" / "fixtures" / "language_qa" / "labelled"
        target.mkdir(parents=True, exist_ok=True)
        for bucket, examples in bench.labelled_examples(rows, verses).items():
            path = target / f"{bucket}.jsonl"
            # The human-review fixtures (benchmark/human/<date>/labelled/, appended)
            # are data from a reviewer, never regenerated: keep them.
            kept = [line for line in (path.read_text(encoding="utf-8").splitlines() if path.exists() else [])
                    if "(human review" in json.loads(line).get("origin", "")]
            with path.open("w", encoding="utf-8", newline="\n") as handle:
                for example in examples:
                    handle.write(json.dumps(example, ensure_ascii=False) + "\n")
                for line in kept:
                    handle.write(line + "\n")
        print(f"labelled fixtures written to {target}", file=sys.stderr)
    if args.update_doc:
        doc = DOC.read_text(encoding="utf-8")
        block = f"{START}\n_Generated {result['generatedAt']} by scripts/language_qa_benchmark.py._\n\n{table}\n{END}"
        head, _, rest = doc.partition(START)
        _, _, tail = rest.partition(END)
        DOC.write_text(head + block + tail, encoding="utf-8")
        print(f"updated {DOC}", file=sys.stderr)
    baseline = json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline.exists() else None
    if args.write_baseline:
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text(json.dumps(bench.baseline_of(result), ensure_ascii=False, indent=1) + "\n",
                                 encoding="utf-8")
        print(f"baseline written to {args.baseline}", file=sys.stderr)
    if args.gate:
        failures = bench.gate(result, baseline)
        for failure in failures:
            print(f"GATE: {failure}", file=sys.stderr)
        print("gate: " + ("FAIL" if failures else "pass"), file=sys.stderr)
        return 1 if failures else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
