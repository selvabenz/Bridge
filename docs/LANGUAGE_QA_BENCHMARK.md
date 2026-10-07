# Language QA benchmark

Per-rule precision of Bridge's offline Language QA on the Tamil IRV, measured
two ways:

- **Human precision** against a Tamil reviewer's verdicts on a sample of the
  engine's own findings. This is the only number that may put a rule inline
  (DECISIONS.md, 2026-09-28), and CI gates on it. Since 2026-10-07 the same
  numbers also ship per pack as `rule_precision.json` (`--write-precision`).
  There they feed the reviewer's own Settings threshold, which decides what
  the text *draws*. That threshold never changes a rule's reviewed `inline`
  flag, which is what this gate reads (DECISIONS.md, 2026-10-07, "drawn in
  the text by default").
- **AI agreement** with the Round 2 and Pass 3 review reports (Phase 2 of the
  layered-rules plan in [LANGUAGE_QA_PLAN.md](LANGUAGE_QA_PLAN.md)). It is a
  lower bound, not accuracy, so it is diagnostic only: it guards against
  regression and lists unmatched findings for the next human sample.

## Human precision (review rounds of 2026-09-28 and 2026-09-29)

**The data.** Yesu Selva Benz labelled a stratified sample of the engine's
findings on Genesis, Psalms and John: 598 items, 597 answered (R0102, PSA
119:54, is unanswered and excluded), none "unsure", with a grammatical
reason on every row. The labels are committed as data and never
regenerated, in `benchmark/human/2026-09-28/`:

| File | What it is |
|---|---|
| `human_labels.jsonl` | every labelled item with its verdict code and the reviewer's note |
| `verses.jsonl` | the imported text of the 332 labelled verses, so the gate runs without the IRV corpus |
| `summary.json`, `pack_changes.md` | the converter's per-rule numbers and proposed pack edits |
| `labelled/*.jsonl` | the same items as test fixtures (merged into `engine/tests/fixtures/language_qa/labelled/`) |
| `housestyle_import.json`, `lexicon_curated.csv` | the house-style seed and the confirmed misspellings |
| `ta_irv_labels_to_candidate.py` | the converter that produced the files above from the reviewer's workbook |

**Round 2 (2026-09-29).** The same reviewer labelled 220 items from all 66
books, none seen in round 1: 220 answered, a reason on every row. They are in
`benchmark/human/2026-09-29/`, laid out as round 1. They include a new kind of
row, `split`: a candidate join, where `SPLIT` means join it and `WORD` or
`PARTICLE` means leave it (சீ என்று). The fixtures add a `word-joining` bucket.

**Rounds together.** `--human-labels benchmark/human` reads every
`*/human_labels.jsonl` and tags each row with its round (the folder name).
The table reports per round and combined:
- **Inline** is decided on the combined figure.
- **Regression** is judged per round: a round's precision may not fall below
  that round's baseline. A new round changes the combined denominator (dative
  46/46 in round 1, 85/86 combined), which is not a regression.

**The text.** The labels' offsets are exact in `D:\Claude Lab\IRV Tamil`, the
copy the reviewer worked from (224 of 224 flagged items). The older
`C:\Users\Benz\Documents\IRV Tamil`, which the pack builder and the
AI-agreement benchmark read, matches only 163 of them: the two copies differ
in 61 labelled verses. `verses.jsonl` is built from the reviewer's copy.

**Method.**
- A flagged item is scored when a current finding sits at exactly its book,
  chapter, verse, start and end, with the same text after NFC. It is
  credited to that finding's rule. Precision = TP ÷ (TP + FP) over labelled
  findings only. Verdicts `TP`, `TP1`, `TP23` and `TP_OTHER_FIX` are true
  positives; every `FP*` is a false alarm; `HOUSE` (an IRV house form) is
  reported apart and not counted; `UNSURE` and unanswered rows are excluded.
  Unlabelled findings are not scored.
- A labelled finding the engine no longer produces is reported. A lost TP
  fails the gate; a removed FP is the point of a fix.
- The abstained items (contexts the pack deliberately skipped) give **recall
  proxies** per abstain class: how many the reviewer said were missed, and
  how many a sandhi finding now covers. They sample the abstains, not the
  text, so they are not recall.

**The gate** (`--human-labels … --gate`, run in CI) fails when:
- an inline rule meets neither condition (DECISIONS.md 2026-09-29):
  - **(a)** human precision ≥ **0.90** on ≥ **20** labelled findings (combined
    over the rounds);
  - **(b)** every finding it makes in the whole collection is confirmed by a
    label, none is wrong, and the rule is unchanged since
    `benchmark/human/population.json` was written
    (`--write-population --irv-dir …`, local only).

  Project data (`project/*`: house style, the termbase) is not a labelled rule
  and is not gated;
- a rule's precision in any review round fell below that round's figure in
  `benchmark/human/baseline.json`;
- a human-confirmed finding is no longer produced;
- a label no longer anchors in its verse.

```powershell
.\engine\.venv\Scripts\python.exe scripts\language_qa_benchmark.py `
  --human-labels benchmark\human --gate
```

`--update-doc` rewrites the block below; `--write-baseline` rewrites
`benchmark/human/baseline.json`; `--write-human-verses --irv-dir …` rebuilds
`verses.jsonl`.

<!-- human-benchmark:start -->
_Generated 2026-09-29T14:38:36 by scripts/language_qa_benchmark.py --human-labels benchmark/human._

Pack version `language-qa-7+ta-irv@1.1.0`; 41 books; 818 label rows over 2 review round(s) (2026-09-28, 2026-09-29). The gate reads the combined numbers.

| Rule | Inline | Labelled | TP | FP | House form | Human precision (combined) | Per round (TP/labelled) | Lost TP | FP no longer produced |
|---|---|---|---|---|---|---|---|---|---|
| `common/spacing.extra` | yes | 23 | 23 | 0 | 0 | 100.0% | 2026-09-28: 15/15; 2026-09-29: 8/8 | 0 | 0 |
| `ta-irv/integrity.space-before-note-end` | no | 15 | 0 | 15 | 0 | 0.0% | 2026-09-28: 0/15 | 0 | 0 |
| `ta-irv/lexicon.known-misspelling` | yes | 47 | 47 | 0 | 0 | 100.0% | 2026-09-28: 43/43; 2026-09-29: 4/4 | 0 | 4 |
| `ta-irv/lexicon.known-split` | yes | 10 | 10 | 0 | 0 | 100.0% | 2026-09-29: 10/10 | 0 | 0 |
| `ta-irv/lexicon.rare-near-common` | no | 0 | 0 | 0 | 0 | — | — | 0 | 21 |
| `ta-irv/sandhi.clitic.fused` | no | 0 | 0 | 0 | 0 | — | — | 0 | 0 |
| `ta-irv/sandhi.compound.direction` | yes | 25 | 25 | 0 | 0 | 100.0% | 2026-09-29: 25/25 | 0 | 0 |
| `ta-irv/sandhi.vallinam.accusative` | yes | 76 | 74 | 2 | 0 | 97.4% | 2026-09-28: 35/37; 2026-09-29: 39/39 | 0 | 14 |
| `ta-irv/sandhi.vallinam.dative` | yes | 87 | 87 | 0 | 0 | 100.0% | 2026-09-28: 46/46; 2026-09-29: 41/41 | 0 | 4 |
| `ta-irv/sandhi.vallinam.demonstrative` | yes | 24 | 24 | 0 | 0 | 100.0% | 2026-09-28: 7/7; 2026-09-29: 17/17 | 0 | 1 |
| `ta-irv/sandhi.vallinam.manner-adverb` | yes | 21 | 21 | 0 | 0 | 100.0% | 2026-09-28: 1/1; 2026-09-29: 20/20 | 0 | 0 |
| `ta-irv/sandhi.vallinam.wrong-consonant` | yes | 1 | 1 | 0 | 0 | 100.0% | 2026-09-29: 1/1 | 0 | 0 |
| `ta-irv/tamil.repeated-word` | no | 0 | 0 | 0 | 0 | — | — | 0 | 20 |
| `ta-irv/typo.divine-name.dative-stem` | no | 4 | 4 | 0 | 0 | 100.0% | 2026-09-29: 4/4 | 0 | 0 |
| `ta-irv/typo.divine-name.vowel-drop` | no | 5 | 5 | 0 | 0 | 100.0% | 2026-09-28: 1/1; 2026-09-29: 4/4 | 0 | 0 |
| `ta-irv/typo.suffix.dropped-tha` | yes | 2 | 2 | 0 | 0 | 100.0% | 2026-09-29: 2/2 | 0 | 0 |

Recall proxies over the contexts the pack skipped on purpose (a sample of the abstains, not of the text, so these are not recall):

| Abstain class | Reviewer: missed | now flagged | Reviewer: correct skip | now flagged | House form | now flagged | Still-missed rate |
|---|---|---|---|---|---|---|---|
| A_ai_rootnoun | 1 | 0 | 29 | 1 | 0 | 0 | 3.3% |
| B_ai_baremajority | 5 | 5 | 45 | 0 | 0 | 0 | 0.0% |
| D_rku_dative | 17 | 17 | 0 | 0 | 0 | 0 | 0.0% |
| E_kku_exception | 5 | 5 | 7 | 0 | 0 | 0 | 0.0% |
| G_marker | 2 | 2 | 0 | 0 | 2 | 0 | 0.0% |
| H_demonstrative_houseform | 9 | 9 | 0 | 0 | 0 | 0 | 0.0% |
| T_theva_abstain | 0 | 0 | 4 | 0 | 16 | 0 | 0.0% |
| split | 0 | 0 | 13 | 0 | 0 | 0 | 0.0% |

Whole-population coverage for rules under 20 labels (inline rule (b): every finding in the collection confirmed, none wrong):

- `common/punctuation.repeated`: 0 of 1 confirmed
- `common/unicode.invisible`: 0 of 1 confirmed
- `ta-irv/lexicon.known-split`: 11 of 11 confirmed
- `ta-irv/sandhi.vallinam.wrong-consonant`: 1 of 1 confirmed
- `ta-irv/typo.divine-name.dative-stem`: 4 of 13 confirmed
- `ta-irv/typo.divine-name.vowel-drop`: 5 of 11 confirmed
- `ta-irv/typo.suffix.dropped-tha`: 2 of 2 confirmed
<!-- human-benchmark:end -->

## AI agreement: what the numbers mean — read this first

- **Every review row is an AI proposal.** The reports say so on every row
  ("AI proposal awaiting human review"). On 2026-09-24 the maintainer
  decided to use all of them as positives: a false positive can be corrected
  later. So **precision here is agreement with the AI review, not with
  verified truth.** A rule can be right where the review is silent, and
  wrong where the review agrees.
- **"Maybe"** marks a row the reports contradict themselves on:
  - two rows at one place proposing different fixes;
  - a fix that another row at the same verse proposes to undo;
  - a flag on a form the Pass 3 reviewers confirmed is house style
    (`IRV_Pass3_Handoff.md` §5). These are `இந்த` + bare hard consonant,
    `அந்த தேச-` bare, `-விட` bare, names ending in `-க்கு`, and digits in
    verse text.
- **Strict** counts only positives. **Lenient** also counts maybes as
  positives.
- **A human verdict against an AI proposal is also "maybe", not a
  negative.** The Philippians report's Rejected/Disputed rows are the only
  human verdicts in the inputs. That pass answered a different question:
  was the inconsistency worth an editorial fix. The maintainer said its
  dispositions are not linguistic ground truth (BUILD_LOG, B3 entry), and
  php 1:29 later went from Rejected to Confirmed.
- **The inputs contain no verified negatives.** The "negative" columns are
  reserved for a future human-labelled set. The rejected leads the brief
  suggested seeding negatives from do not hold up. php 1:29 was confirmed.
  php 3:14 and 4:18 are real bare boundaries that B4 excludes only for
  scope. So the labelled fixtures carry positive and "maybe" examples, and
  no invented negatives.
- The reviews flag **minority forms**: "in this book -க்கு before க/ச/த/ப is
  doubled 178 times and bare 10 times". A rule that also fires on a book's
  majority form counts those findings as false positives, although some may
  be real errors the review did not list. Every unmatched finding is written
  to the result file for a human to label.

## Inputs (outside the repository)

| Input | Expected layout |
|---|---|
| `--irv-dir` | A folder of IRV books as SFM, one per book, named like `01GENIRVTam.SFM`. Each file's own `\id` line identifies the book. |
| `--reviews` | Folders or files. A folder contributes its top-level `*_Issues.csv` files, so `v1-before-split/` is not read. |

The CSV columns are: `Book, Chapter, Verse, Issue Type, Priority Category,
Severity, Confidence, Original Tamil, Suggested Correction, Explanation,
Source / Reference Note, Reviewer Decision Needed, Status`. Some files name
the reference column `Source / ESV Reference Note` or add a `Review Scope`
or `Detected By` column; extra columns are ignored.

`Book` may be a code (`PSA`) or an English name (`1 Samuel`). Rows are
skipped when Chapter is `Intro`, `Book-level` or empty, when Verse is `—`
or empty, or when there is no `Original Tamil`.

The run of 2026-09-24 used:
- `C:\Users\Benz\Documents\IRV Tamil`, all 66 books;
- `D:\Claude Lab\Revant work\Claude outputs`: 16 Round 2 reports (GEN–2CH,
  EZR, RUT, PSA) and 5 Pass 3 reports (GEN, EXO, LEV, NUM, DEU);
- `C:\Users\Benz\Documents\Claude outputs\Philippians_Round2_QA_Issues.csv`,
  whose rows carry human verdicts.

## Scope

| Issue Type | Bucket | Engine layer that should find it |
|---|---|---|
| Confirmed typo, Possible typo | typo | lexicon, integrity |
| Sandhi / word-joining | sandhi | pattern rules |
| Punctuation, only spacing / repetition / space-before | punctuation | integrity |
| Name consistency | name | house style (Phase 6 name pack) |
| USFM marker, Footnote / cross-reference | usfm | integrity, where a text-only rule can see it |

Out of scope, and reported as counts only (LQA-3+): Grammar, Plural
agreement, Case/object marker, Source comparison, Possible missed source
word, Addition not in source, Textual-basis review, Theological consistency,
Reviewer decision, and Punctuation rows about comma or semicolon policy.

## Method

1. Each book is parsed with `parse_scripture_file` and
   `imported_verse_text`, exactly as import writes chapter JSON.
2. It is scanned by the app's own `LanguageQaManager`. There is no second
   copy of the rules.
3. A finding is a **true positive** when it overlaps a positive row at the
   same chapter and verse: the NFC-normalised texts must be substrings of
   one another in either direction. The row must also be of a type the
   rule can find (`RULE_BUCKETS`). A வல்லினம் finding cannot be "confirmed"
   by a Source comparison row that happens to cover the same words.
4. **Recall** is counted per bucket, over rows whose `Original Tamil` is
   found in the scanned verse text. Unanchored rows are counted separately:
   headings, `\ms` lines, text changed since the review.

## Running it

```powershell
.\engine\.venv\Scripts\python.exe scripts\language_qa_benchmark.py `
  --irv-dir "C:\Users\Benz\Documents\IRV Tamil" `
  --reviews "D:\Claude Lab\Revant work\Claude outputs" "C:\Users\Benz\Documents\Claude outputs\Philippians_Round2_QA_Issues.csv" `
  --gate
```

Options:

| Option | What it does |
|---|---|
| `--update-doc` | Rewrites the generated block below. |
| `--write-baseline` | Rewrites `benchmark/baseline.json`, the aggregate numbers only. |
| `--write-labelled` | Rewrites the Phase 2.4 fixtures in `engine/tests/fixtures/language_qa/labelled/`. |

The full result goes to `benchmark/results/<date>-<pack>.json`, which is not
committed because it quotes Scripture and review text. It holds every
unmatched finding and every unmatched row.

**This gate is local and diagnostic.** The IRV text and the reports live
outside the repository, so CI cannot run it. It no longer decides inline:
the human gate does (DECISIONS.md, 2026-09-28). Run `--gate` before
committing a change to a rule, and record its output in BUILD_LOG. It fails
only when a rule's strict precision fell more than 2 points below
`benchmark/baseline.json`. Rules with fewer than 10 findings are too small
to compare.

## AI agreement results

<!-- benchmark:start -->
_Generated 2026-09-24T22:02:07 by scripts/language_qa_benchmark.py._

Pack version `language-qa-7+ta-irv@1.0.0`; books: 1CH, 1KI, 1SA, 2CH, 2KI, 2SA, DEU, EXO, EZR, GEN, JDG, JOS, LEV, NUM, PHP, PSA, RUT.

Per rule (a finding is a true positive when it overlaps a review row of a compatible type at the same verse):

| Rule | Inline | Findings | TP (strict) | FP (strict) | of which on a house form | Precision strict | Precision lenient | Matched maybe | Matched negative |
|---|---|---|---|---|---|---|---|---|---|
| `common/punctuation.repeated` | no | 1 | 1 | 0 | 0 | 100.0% | 100.0% | 0 | 0 |
| `common/unicode.invisible` | no | 1 | 1 | 0 | 0 | 100.0% | 100.0% | 0 | 0 |
| `ta-irv/integrity.space-before-note-end` | no | 74 | 55 | 19 | 0 | 74.3% | 74.3% | 0 | 0 |
| `ta-irv/lexicon.known-misspelling` | no | 140 | 130 | 10 | 0 | 92.9% | 92.9% | 0 | 0 |
| `ta-irv/lexicon.rare-near-common` | no | 76 | 2 | 74 | 0 | 2.6% | 2.6% | 0 | 0 |
| `ta-irv/sandhi.clitic.fused` | no | 4 | 0 | 4 | 0 | 0.0% | 0.0% | 0 | 0 |
| `ta-irv/sandhi.vallinam.accusative` | yes | 500 | 275 | 225 | 0 | 55.0% | 55.2% | 1 | 0 |
| `ta-irv/sandhi.vallinam.dative` | yes | 396 | 197 | 199 | 0 | 49.8% | 49.8% | 0 | 0 |
| `ta-irv/sandhi.vallinam.demonstrative` | yes | 80 | 33 | 47 | 13 | 41.2% | 55.0% | 11 | 0 |
| `ta-irv/sandhi.vallinam.manner-adverb` | yes | 13 | 2 | 11 | 0 | 15.4% | 15.4% | 0 | 0 |
| `ta-irv/tamil.repeated-word` | no | 33 | 0 | 33 | 0 | 0.0% | 0.0% | 0 | 0 |
| `ta-irv/typo.divine-name.dative-stem` | no | 13 | 9 | 4 | 0 | 69.2% | 69.2% | 0 | 0 |
| `ta-irv/typo.divine-name.vowel-drop` | no | 11 | 11 | 0 | 0 | 100.0% | 100.0% | 0 | 0 |
| `ta-irv/typo.suffix.dropped-tha` | no | 2 | 2 | 0 | 0 | 100.0% | 100.0% | 0 | 0 |

Per review bucket (recall counts only rows whose Original Tamil is found in the scanned verse):

| Bucket | Positive rows | Maybe rows | Negative rows | Unanchored | Found (positive) | Recall strict | Recall lenient |
|---|---|---|---|---|---|---|---|
| typo | 839 | 13 | 0 | 80 | 159 | 20.9% | 20.6% |
| sandhi | 1877 | 34 | 0 | 110 | 501 | 28.3% | 28.5% |
| punctuation | 72 | 0 | 0 | 22 | 9 | 18.0% | 18.0% |
| name | 528 | 3 | 0 | 144 | 0 | 0.0% | 0.0% |
| usfm | 680 | 4 | 0 | 421 | 64 | 24.4% | 24.3% |
<!-- benchmark:end -->

## Lexicon rules (Phase 5)

**Since round 2 (2026-09-29, `ta-irv-lexicon@3`):**
- **Provenance.** Each misspelling pair is `human` (47, confirmed) or `ai-review`
  (135, from the AI reports, including 30 from reports added on 2026-09-29).
  Only human pairs are high confidence and inline. An ai-review finding is
  medium confidence, panel-only, and says it awaits confirmation.
- **Rejected pairs.** The four pairs the reviewer rejected are removed and protected.
- **Fragments.** Single-grapheme fragments (சு, நே, சோ) are not counted as words.

**Since the 2026-09-28 human review (`ta-irv-lexicon@2`):**

- `lexicon.rare-near-common` is **disabled by default**: 0 of 21 labelled
  findings were errors. Every suggestion was a different real word: a feminine
  past -ஆள் against a conditional -ஆல், வாள்/வாழ், காலை/காளை, உற்று/ஊற்று,
  and the name சேத்து. The 21 forms are the lexicon's `protected` words, never
  flagged again. It may be re-enabled only when a candidate passes a morphology
  filter (never propose a form that is itself an attested inflection) and a new
  human sample shows ≥ 0.90.
- `lexicon.known-misspelling` is **inline**: 43 of 43 labelled findings were
  confirmed. The 43 pairs are `humanConfirmed` in `lexicon.json` and are kept
  without the frequency filters that guard the AI-review pairs. The map has 156
  pairs (151 before, plus 5).
- A known misspelling is reported once per book, at its first occurrence, as
  the lexicon rules always were: the inline mark is on that occurrence only.

Rebuilt with:

```powershell
.\engine\.venv\Scripts\python.exe scripts\build_tamil_lexicon.py `
  --irv-dir "D:\Claude Lab\IRV Tamil" `
  --curated "D:\Claude Lab\Revant work\Claude outputs" benchmark\human\2026-09-28\lexicon_curated.csv
```

(`--human-labels` defaults to `benchmark/human/2026-09-28/human_labels.jsonl`.)

The thresholds live in one place, `engine/tc_ai_bridge/language_packs/lexicon.py`:

| Constant | Value | Meaning |
|---|---|---|
| `RARE_BOOK_MAX` | 2 | a word flagged by `lexicon.rare-near-common` occurs at most twice in the book |
| `RARE_CORPUS_MAX` | 2 | …and at most twice in the corpus. A word absent from the lexicon counts as rare: only words seen 3 or more times are listed |
| `COMMON_MIN` | 6 | a suggestion occurs at least 6 times in the corpus |
| `RATIO_MIN` | 5 | …and at least 5 times as often as the flagged word |
| `MAX_DISTANCE` | 0.5 | Tamil confusion-set distance (`tamil_distance.py`); 0.5 means one typist confusion |
| `MIN_CLUSTERS` | 3 | shorter words are not audited |
| `MAX_SUGGESTIONS` | 5 | ranked by distance, then corpus count, then same-book count |

**What these thresholds produced on the review set (2026-09-24):**

| `MAX_DISTANCE` | `rare-near-common` findings | Strict precision |
|---|---|---|
| 1.0, any one-cluster edit | 2,931 | 0.8% |
| **0.5, typist confusions only** | **76** | **2.6%** |

For comparison, the within-book wordlist audit it replaces produced 2,473
findings at 2.0%. Tamil inflection makes "one cluster away" true of most
rare forms, so 0.5 was kept. The rule stays panel-only.

**`lexicon.known-misspelling` is not independently measured.** Its 92.9%
(130 of 140) is measured on the same review rows its curated pairs were
built from, so the figure is close to 100% by construction. It says only
that the pairs are applied where they came from. A real measure needs
reviews the pairs were not built from (a new book), or a human-labelled
set. The typo bucket's recall rise, from 8.9% to 20.9%, is mostly this
rule, and carries the same caveat.

## History

| Date | Pack | Change | `tamil.vallinam-missing` strict precision / sandhi recall |
|---|---|---|---|
| 2026-09-24 | language-qa-7 | Phase 2 baseline, before any rule change. **The gate fails:** the only inline rule reaches 37.9% strict precision; 77 of its 193 false positives fall on confirmed house forms. | 37.9% / 6.8% |
| 2026-09-24 | language-qa-7+ta-irv@1.0.0 | Phase 3: rules move into the `ta-irv` pack; B1/B2 split into four vallinam rules with corpus abstains (house forms, root nouns, clitics); one wrong-consonant rule and five IRV defect-shape rules added. House-form false positives on the demonstrative rule fall from 77 to 13. **The gate passes on sign-offs, not on the 90% floor:** the maintainer signed off every vallinam rule for inline display at its measured precision (docs/DECISIONS.md). The column is now the best vallinam rule. | 55.0% (accusative; demonstrative 41.2%) / 28.3% |
| 2026-09-24 | language-qa-7+ta-irv@1.0.0, lexicon ta-irv-lexicon@1 | Phase 5: the corpus lexicon replaces the within-book wordlist audit. `lexicon.rare-near-common`: 76 findings at 2.6%, against the wordlist's 2,473 at 2.0%. `lexicon.known-misspelling`: 140 at 92.9%, not independent (built from these reviews). Typo recall is 20.9%, up from 8.9%. The vallinam rules are unchanged. | 55.0% / 28.3% |
