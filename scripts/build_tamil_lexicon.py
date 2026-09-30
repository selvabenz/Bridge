"""Build the ta-irv corpus lexicon (layered-rules Phase 5.1).

    python scripts/build_tamil_lexicon.py --irv-dir "C:/…/IRV Tamil" [--curated DIR_OR_CSV …] [--top N]

Writes engine/language_packs/ta-irv/lexicon.json.gz (gzip, deterministic):

- `forms`: word -> [corpus count, number of books], for every word seen at
  least MIN_LISTED_COUNT times (bounded at `--top`). A word absent from the
  list is rare in the corpus -- which is exactly what the lexicon rule asks.
- `common` and `buckets`: the words frequent enough to be suggested (count >=
  COMMON_MIN) and their one-cluster deletion neighbours, precomputed here so
  the engine never rebuilds them at load (performance contract).
- `deprecated`: wrong -> right from the Round 2 / Pass 3 reports' positive
  typo rows (`--curated`), kept only where the wrong form is rare in the
  corpus and the right form is attested. A hard rule; the frequency table is
  evidence only. A pair a human reviewer confirmed (`--human-labels`,
  `lexicon.known-misspelling` verdict TP) is kept without those filters, and
  listed in `humanConfirmed`.
- `protected`: words a human reviewer judged correct when a lexicon rule
  flagged them (2026-09-28: rare-near-common 21 of 21 were real words --
  வாள்/வாழ், காலை/காளை, a feminine past -ஆள் against a conditional -ஆல், the
  name சேத்து; 2026-09-29: four known-misspelling pairs were a meaning or style
  change, not a misspelling -- திடமனதாயிரு, கவனிக்காதே, பூட்டுக்களையும்,
  பெருந்தொனியாய்). Never flagged again, and never proposed.
- `provenance`: for every `deprecated` pair, `human` (confirmed by a reviewer
  in any round) or `ai-review` (from the Round 2 / Pass 3 CSVs only, awaiting
  confirmation). Only a human pair is reported with high confidence and
  drawn inline.
- Single-grapheme tokens are not words unless they are real monosyllables
  (MONOSYLLABLES): fragments such as சு, நே, சோ were counted as words.
- `splits`: a curated phrase map of word pairs a reviewer confirmed are one
  word written apart ("சு வரை" -> "சுவரை", "நே போ" -> "நேபோ"), for
  `lexicon.known-split`. Only from human SPLIT verdicts; a split the reviewer
  judged a word, name or interjection (சீ, சோ, நோ, பை) is protected instead.

Tokens are the runtime's own: imported_verse_text -> lift_inline_usfm ->
language_qa.word_occurrences (NFC keys; the text is never rewritten).
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "engine"))

from tc_ai_bridge import language_qa_benchmark as bench  # noqa: E402
from tc_ai_bridge.language_packs import default_pack  # noqa: E402
from tc_ai_bridge.language_packs.indic.confusion import clusters  # noqa: E402
from tc_ai_bridge.language_packs.lexicon import (  # noqa: E402
    COMMON_MIN, LEXICON_VERSION, MIN_LISTED_COUNT, deletion_keys, write_lexicon_json,
)
from tc_ai_bridge.language_qa import lift_inline_usfm, word_occurrences  # noqa: E402
from tc_ai_bridge.project_import import imported_verse_text, parse_scripture_file  # noqa: E402

OUT = REPO / "engine" / "language_packs" / "ta-irv" / "lexicon.json.gz"


def tamil_distance(a: str, b: str) -> float:
    """ta-irv's confusion-set distance (confusion.json)."""
    return default_pack("ta-irv").confusion().distance(a, b)
# The one-grapheme tokens that are Tamil words (2026-09-29 review). Any other
# single grapheme is a fragment of a split word or a name, not a word form.
MONOSYLLABLES = frozenset({"நீ", "போ", "வா", "கை", "ஆ", "வை", "தா", "பூ", "ஓ", "ஏ", "ஈ", "ஊ", "கா", "பா", "தீ"})


def corpus_counts(irv_dir: Path) -> tuple[collections.Counter, dict[str, set], int, int, list[str]]:
    counts: collections.Counter = collections.Counter()
    books: dict[str, set] = collections.defaultdict(set)
    verses = tokens = 0
    book_ids = []
    for sfm in sorted(irv_dir.glob("*.SFM")):
        book = parse_scripture_file(sfm)
        book_ids.append(book.book_id)
        for items in book.chapters.values():
            for raw in items.values():
                lifted, _ = lift_inline_usfm(imported_verse_text(raw))
                if lifted is None:
                    continue
                verses += 1
                for word, _, _ in word_occurrences(lifted.visible):
                    if len(clusters(word)) == 1 and word not in MONOSYLLABLES:
                        continue  # a fragment (சு of சு வரை), not a word form
                    counts[word] += 1
                    books[word].add(book.book_id)
                    tokens += 1
    return counts, books, verses, tokens, book_ids


def curated_pairs(paths: list[str], counts: collections.Counter) -> tuple[dict[str, str], dict[str, int]]:
    """wrong -> right from positive typo rows whose Original and Suggested
    differ in exactly one word; a conflicting or unsafe pair is dropped."""
    files: list[Path] = []
    for value in paths:
        path = Path(value)
        # Top level only, as the benchmark reads them: v1-before-split/ is superseded.
        files.extend(sorted(path.glob("*_Issues.csv")) if path.is_dir() else [path])
    rows = bench.load_review_rows(files)
    bench.classify(rows)
    proposals: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    stats = collections.Counter()
    for row in rows:
        if row.label != "positive" or row.bucket != "typo":
            continue
        before = [w for w, _, _ in word_occurrences(row.original)]
        after = [w for w, _, _ in word_occurrences(row.suggestion)]
        if len(before) != len(after):
            stats["different word count"] += 1
            continue
        changed = [(a, b) for a, b in zip(before, after) if a != b]
        if len(changed) != 1:
            stats["not one word"] += 1
            continue
        proposals[changed[0][0]][changed[0][1]] += 1
    deprecated = {}
    for wrong, rights in sorted(proposals.items()):
        if len(rights) > 1:
            stats["reviews disagree"] += 1
            continue
        right = next(iter(rights))
        if counts[wrong] > 2:
            stats["wrong form is common in the corpus"] += 1
            continue
        if counts[right] < 1:
            stats["right form unattested"] += 1
            continue
        if tamil_distance(wrong, right) > 2.0:
            stats["too far apart to be a typo"] += 1
            continue
        deprecated[wrong] = right
    return deprecated, dict(stats)


def human_splits(labels_path: Path | None) -> tuple[dict[str, str], set[str]]:
    """(confirmed split phrase -> joined form, protected tokens) from the
    reviewer's split rows (round 2): SPLIT joins; WORD or PARTICLE protects the
    one-grapheme token so no future rule joins it."""
    if labels_path is None or not labels_path.exists():
        return {}, set()
    splits: dict[str, str] = {}
    protected: set[str] = set()
    labels, _ = bench.load_human_rounds(labels_path)
    for label in labels:
        if label.get("kind") != "split":
            continue
        phrase = " ".join(bench.nfc(label["original"]).split())
        if label.get("verdict") == "SPLIT" and label.get("correctForm"):
            if len(phrase.split()) != 2:
                raise SystemExit(f"{label['id']}: a known split is a word pair, not {phrase!r}")
            splits[phrase] = bench.nfc(label["correctForm"])
        elif label.get("verdict") in {"WORD", "PARTICLE"}:
            protected |= {token for token in phrase.split() if len(clusters(token)) == 1}
    return splits, protected


def human_lexicon(labels_path: Path | None) -> tuple[dict[str, str], set[str]]:
    """(confirmed wrong -> right, protected words) from the reviewer's labels,
    every round (a folder) or one (a file)."""
    if labels_path is None or not labels_path.exists():
        return {}, set()
    confirmed: dict[str, str] = {}
    protected: set[str] = set()
    labels, _ = bench.load_human_rounds(labels_path)
    for label in labels:
        if label.get("kind") != "flagged":
            continue
        verdict = bench.human_verdict(label.get("verdict"))
        word = bench.nfc(label["original"])
        if label["rule"] == "ta-irv/lexicon.known-misspelling" and verdict == "tp":
            right = bench.nfc(label.get("correctForm") or (label.get("suggestions") or [""])[0])
            if right and right != word:
                confirmed[word] = right
        elif label["rule"] in {"ta-irv/lexicon.rare-near-common", "ta-irv/lexicon.known-misspelling"} \
                and verdict == "fp":
            protected.add(word)  # a correct word, or a meaning/style change, not a misspelling
    confirmed = {w: r for w, r in confirmed.items() if w not in protected}
    return confirmed, protected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--irv-dir", required=True, type=Path)
    parser.add_argument("--curated", nargs="*", default=[])
    parser.add_argument("--top", type=int, default=60_000, help="bound on listed forms (most frequent first)")
    parser.add_argument("--human-labels", type=Path,
                        default=REPO / "benchmark" / "human")
    args = parser.parse_args()
    counts, books, verses, tokens, book_ids = corpus_counts(args.irv_dir)
    listed = [w for w, c in counts.most_common() if c >= MIN_LISTED_COUNT][:args.top]
    common = sorted(w for w in listed if counts[w] >= COMMON_MIN)
    index = {w: i for i, w in enumerate(common)}
    buckets: dict[str, list[int]] = collections.defaultdict(list)
    for word in common:
        for key in {word, *deletion_keys(word)}:
            buckets[key].append(index[word])
    deprecated, curated_stats = curated_pairs(args.curated, counts) if args.curated else ({}, {})
    confirmed, protected = human_lexicon(args.human_labels)
    splits, split_protected = human_splits(args.human_labels)
    protected |= split_protected
    for wrong, right in confirmed.items():
        if deprecated.get(wrong, right) != right:
            curated_stats["human confirmation overrides a review pair"] =                 curated_stats.get("human confirmation overrides a review pair", 0) + 1
        deprecated[wrong] = right  # confirmed by a person: no frequency filter
    for word in protected:
        deprecated.pop(word, None)
    data = {
        "version": LEXICON_VERSION,
        "corpus": {"books": len(book_ids), "verseCount": verses, "tokenCount": tokens,
                   "distinctForms": len(counts), "listedForms": len(listed),
                   "minListedCount": MIN_LISTED_COUNT, "commonMin": COMMON_MIN},
        "forms": {w: [counts[w], len(books[w])] for w in listed},
        "common": common,
        "buckets": {k: v for k, v in sorted(buckets.items())},
        "deprecated": dict(sorted(deprecated.items())),
        "humanConfirmed": sorted(w for w in confirmed if w in deprecated),
        "provenance": {w: ("human" if w in confirmed else "ai-review") for w in sorted(deprecated)},
        "protected": sorted(protected),
        "splits": dict(sorted(splits.items())),
        "curatedStats": curated_stats,
    }
    write_lexicon_json(OUT, data)
    print(f"{len(book_ids)} books, {verses} verses, {tokens} tokens, {len(counts)} distinct forms", file=sys.stderr)
    print(f"listed {len(listed)} (count >= {MIN_LISTED_COUNT}), common {len(common)} (>= {COMMON_MIN}), "
          f"{len(buckets)} bucket keys, {len(deprecated)} deprecated pairs {curated_stats}", file=sys.stderr)
    print(f"human review: {len(confirmed)} confirmed pairs, {len(protected)} protected words, "
          f"{len(splits)} known splits", file=sys.stderr)
    print(f"{OUT} {OUT.stat().st_size / 2**20:.2f} MB", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
