# Language QA — offline, Tamil first

Tracking: [#169](https://github.com/RevantCI/bridge/issues/169).
The user's complete framework is preserved in
[LANGUAGE_QA_TAMIL_SPECIFICATION.md](LANGUAGE_QA_TAMIL_SPECIFICATION.md).

## Problem and scope

Translators need automatic language checks after import/open and Scripture edits,
without network access, large language models, or blocking the editor. This work
lives on `language-qa` as a separate target-text QA layer. The supplied 56-check,
six-gate Tamil publication framework is the roadmap, not a claim that a rule
engine can certify publication readiness.

## Smallest useful implementation (LQA-1)

- Automatically inspect the current book after opening/importing it and after
  Bridge Scripture edits. Refresh external chapter changes during status polling.
- Use declared language plus sampled Unicode script evidence. Tamil script can
  suggest Tamil; conflicting metadata remains explicit. A shared script alone
  cannot distinguish Hindi/Marathi/Nepali or Bengali/Assamese. Unknown languages
  receive only common technical checks, with language-specific coverage unavailable.
- Check replacement/private-use/control characters, normalization, suspicious
  whitespace, repeated punctuation and words, and invalid Tamil dependent-sign
  sequences. Respect legal decomposed Tamil vowels, Grantha letters, க்ஷ and ஸ்ரீ.
- Keep original Unicode offsets and content hashes. Findings are read-only review
  candidates, never automatic Scripture edits or publication approvals.
- Display progress, actual coverage, limitations, errors, pause/resume and paged
  findings in a separate Language QA panel. Preserve exact verse bridges/segments.

## Resource and concurrency contract

No new runtime dependency, model, dictionary download, vendor tree or database
migration. Use Python's Unicode library and the already bundled `regex` package.
One short-lived worker per engine; debounce edits, yield between verses and wait
briefly after foreground requests. Read one bounded chapter at a time; reuse
unchanged chapter results in memory using SHA-256 content hashes (timestamps alone
are insufficient). Bound chapter/verse sizes, retained findings
and response pages; report limits as incomplete coverage. Changing books or editing
invalidates old results immediately and prevents old workers publishing them.

LQA-1 results are disposable in-memory analysis, automatically regenerated on
reopen. They are not review decisions, and no audit history is deleted. Durable
review dispositions and cross-session caches belong to a later approved workbench
migration. Preserved original USFM is not current edited Scripture: scan target
chapter JSON. Dedicated USFM checks remain authoritative for the markup itself.

### Coverage of verses with inline USFM (2026-09-24)

Verses with inline USFM are scanned. Until 2026-09-24 any verse containing a
backslash was skipped, which left every footnoted verse unchecked.
`language_qa.lift_inline_usfm` now builds the text a reader sees:

- `\f … \f*` and `\x … \x*` are removed with their contents. It uses the same
  pattern and the same swallow-one-space rule as the frontend's
  `parseVerseNotes`, and a shared test table in both suites keeps them in step.
- Character markers (`\wj`, `\add`, `\nd`, `\qt`, `\w`, nested `\+…`, closers,
  milestones) are removed and their content is kept.
- Word attributes (`|lemma="…"`) are dropped.

Every rule reads that visible text: the character rules, வல்லினம், the termbase
and the wordlist counts. Every finding is reported in exact raw code points, so
`originalText == verse[start:end]` and a suggested fix splices the raw verse
unchanged.

Remaining limitations, all reported rather than hidden:

- **Crossing candidates.** A candidate whose span would cross lifted markup is
  dropped, for example a வல்லினம் boundary with `\wj*` between the two words.
  No single raw span can hold it without covering markup. The verse records
  `N candidate(s) spanning inline USFM markup omitted.`
- **Markup that cannot be lifted safely.** The verse is skipped, with its reason
  named, when:
  - paired markers are unbalanced (`usfm.marker_balance_issues`, for example
    `Unbalanced \f: 1 open, 0 close; verse not checked.`);
  - note markup such as `\ft` sits outside a complete note;
  - a backslash is not a marker;
  - word attributes (`|…`) are not closed by a marker. One local development
    project does this, with a custom `\zsem-s |…"*` milestone closed by a bare
    `*`; scanning it would treat its Greek and English glosses as Scripture.
- **Note text is not checked.** Footnote and cross-reference contents are not
  scanned by this pass.

Budgets to verify: under 100 KiB new runtime source; no dependency change; bounded
2 MiB chapter input, 20,000 code points per verse, 100 findings per verse, 3,000
retained findings per book and 100 per response. Measure pure-check throughput and
foreground responsiveness under load. These are budgets, not a promise of zero
CPU use on every computer. Frozen-size and installed 1366×768 acceptance must be
measured before release.

## Staged coverage of the supplied framework

| Stage | Framework items | Delivery and evidence needed |
|---|---|---|
| LQA-1 | Parts of 1, 9, 35–38; automatic trigger portion of 55; text/rule hashes from 56 | Common technical rules and Tamil character integrity, separate background UI. UTF-8 file errors are explicit; NFC is advisory. No general spellchecker or grammar certification. |
| LQA-2 | 1–10, 20–21, 49–51 | Approved, versioned Tamil house-style/termbase/name packs with allowed and prohibited variants; dictionary and morphology feasibility measured offline. Sandhi, agreement, suffixes and joined/split forms need expert-labelled correct/incorrect examples, exceptions and validation before enabling each rule. Rare words or spelling similarity alone are never errors. |
| LQA-3 | 11–17, 22–24, 53 | Integrate existing source/semantic evidence and approved references with clear applicability and uncertainty. Source meaning, textual basis, theology and consultant decisions cannot be certified from text-only heuristics. No silent source substitution. **Never offline (layered-rules Phase 7):** agreement (திணை/பால்/எண்), pronoun and number shifts, meaning shifts, omissions and additions, textual basis and theology stay out of the offline Language QA engine. They need the source, a reference or human judgement. The panel lists them as "does not check" (`language_qa.coverage()`), and [LANGUAGE_QA_REVIEW_HANDOFF.md](LANGUAGE_QA_REVIEW_HANDOFF.md) names who checks each one. A request to check one offline is an architecture question to raise, not a rule to add. **Amended 2026-10-07:** the indic-qa profile packs (pa, ml, hi, or) list corpus-attested agreement leads (`grammar`), panel-only and never blocking; agreement across a whole clause stays out, and Tamil has no such rule (DECISIONS 2026-10-07). |
| LQA-4 | 25–34, 48, remainder of 55–56 | Surface existing USFM/versification checks and extend current-text/notes/headings/reference coverage through the established parser work; avoid a new competing parser. Durable exceptions/provenance need a separately approved schema proposal. |
| LQA-5 | 18–19, 39–47, 52, 54 | Reader, community and consultant workflows; typeset PDF/font/layout checks require final production artifacts. Record explicit completion, never infer it from a clean text scan. |
| LQA-6 | Expansion across Indian languages, then other languages | Register independently tested small language packs. Shared Unicode checks remain available everywhere; report unsupported language rules honestly. Test shared-script ambiguity and mixed-language input for each addition. **In progress (2026-10-07):** Punjabi, Malayalam, Hindi and Odia run indic-qa's checker as profile packs, panel-only, with verse-text parity measured against indic-qa itself (LANGUAGE_QA_PACKS.md, "Profile packs"). |

### Layered rule system (LQA-2 → LQA-3 boundary), 2026-09-24

The work moves from hard-coded triggers to three data layers under one engine:
- **pattern rules**, a versioned pack whose labelled examples run as tests;
- **a corpus lexicon**, with ranked suggestions;
- **project house style**, learned from decisions, which may only narrow.

A benchmark decides per rule whether it may be drawn inline. The phases run
in order, and each is its own commit series:

| Phase | Scope | Status |
|---|---|---|
| 1 | Finding model (layer/category/confidence/suggestions/ruleId/packVersion); marks per category; menu with ranked suggestions, Edit-in-place, false positive; decision history; ignore-expiry on rule change; termbase overwrite guard | Done, source-verified (A56–A60); desktop pending |
| 2 | Benchmark harness over the Round 2 / Pass 3 review CSVs; labelled fixtures; `--gate`; latency gate; one status channel | Done (A61, A62). Baseline: the only inline rule, `tamil.vallinam-missing`, is at 37.9% strict precision, so the accuracy gate fails. See [LANGUAGE_QA_BENCHMARK.md](LANGUAGE_QA_BENCHMARK.md) |
| 3 | `ta-irv` rule pack as data; B1–B4 migrated to shape rules; known IRV defect rules | Done (A63–A66). `ta-irv@1.0.0` has 11 rules; every வல்லினம் rule is inline on the maintainer's sign-off (41–55% strict; manner adverbs 15%). Sandhi recall is 28.3%, up from 6.8%. See [LANGUAGE_QA_RULE_PACK.md](LANGUAGE_QA_RULE_PACK.md) |
| 4 | Language QA as a check-framework stage; reports, exception queue, publication gate; collection runner | Done (A67–A71). 4.1: a check-job stage, counted in the rollup, persisted in `language_qa_cache` (workbench v4); a decision rescans nothing, and a live edit rescans one verse. 4.2: reports, exception queue, publication gate. 4.3: one review surface. 4.4: collection runner. 4.5: export gate with a recorded override |
| 5 | Corpus lexicon and Tamil confusion-set distance | Done (A72). `ta-irv-lexicon@1`: 19,618 forms and 151 curated pairs, 3 MB, loaded lazily. `lexicon.rare-near-common` (76 findings, 2.6%, panel-only) replaces the wordlist audit. `lexicon.known-misspelling` is not independently measured. Typo recall is 20.9%, up from 8.9%. See [LANGUAGE_QA_BENCHMARK.md](LANGUAGE_QA_BENCHMARK.md) |
| 6 | Termbase v3, name pack, scoped ignores, house-style learner, export ledger | Done (A73–A77). The termbase matches inflected and prefix forms. House style is data (workbench v5) with scoped Ignores, a learner (3 ignores → learned, with Undo), proposals, preferences and export/import. The name pack is curated proper nouns plus `name.minority-spelling`. An export ledger CSV is written beside each export. See [LANGUAGE_QA_HOUSESTYLE.md](LANGUAGE_QA_HOUSESTYLE.md) |
| 7 | Structured "checks / does not check" boundary shown in the panel | Done (A78). `languageQa.status` carries `coverage: {inScope, outOfScope, handOff, summary}` from `language_qa.coverage()`, in Tamil and English. The panel always shows it, not behind a disclosure. The hand-off is [LANGUAGE_QA_REVIEW_HANDOFF.md](LANGUAGE_QA_REVIEW_HANDOFF.md). The Tamil strings are pending native review |

The "Ignore ▸ this word / this rule" menu scopes arrive with Phase 6.

### Human review of 2026-09-28, and what it changed

A Tamil reviewer labelled 598 of the engine's own findings on GEN, PSA and JHN
(`benchmark/human/2026-09-28/`). Inline is now decided on human-labelled
precision only: ≥ 0.90 on ≥ 20 labelled findings, gated in CI (DECISIONS.md).
The pack (`ta-irv@1.1.0`), the lexicon (`ta-irv-lexicon@2`) and the house style
changed to follow it; BUILD_LOG 2026-09-28 has the numbers. Flagged for the next
review round, because each is at 100% on fewer than 20 labels:
`sandhi.vallinam.demonstrative` (7), `sandhi.vallinam.manner-adverb` (1) and
`common/spacing.extra` (15, 100%). Not yet labelled: `wrong-consonant`,
`compound.direction`, `clitic.fused` and the `typo.*` rules.

### Split words: curated pairs fed by review rounds (`lexicon.known-split`)

The `word-joining.orphan-syllable` heuristic scoped here on 2026-09-28 is
**retired**. Round 2 (2026-09-29) measured the whole Bible:
- only 23 single-grapheme non-words exist;
- 10 of them are real splits;
- the rest are names (சோ, நோ), an interjection (சீ) or a word (பை).

A heuristic is not worth that.

Splits are instead curated word pairs in the lexicon's `splits` map, built only
from a reviewer's `SPLIT` verdicts: சு வரை → சுவரை, நே போ → நேபோ. The tokens the
reviewer judged a word are `protected`. `lexicon.known-split` flags a pair in the
verse and suggests the joined form. It is inline on its whole population (11 of
11 labelled, DECISIONS.md 2026-09-29). A new split enters the map only through
a review round.

Performance contract status after Phase 2:
- Met: one Language QA status poll (500 ms active, 10 s idle); the p95 gate
  for the RPCs Language QA owns (in CI); the click budget.
- Not met, and outside Language QA:
  - `verse.edit`, `project.open` and a non-Language-QA `verse.decide` exceed
    50 ms with or without a scan;
  - the app's 800 ms navigation poll stays awake when idle.

See BUILD_LOG, Phase 2.

Execution of LQA-1 is authorized by the user's request. Later linguistic packs
require approved project-specific data; the broad prose framework does not supply
a dictionary, exhaustive grammar, reference corpus or production PDFs. Fully
automatic scheduling and detection are feasible; fully automatic publication
sign-off is not supported by these inputs.

## Sources for character rules

- Unicode Standard, Tamil section:
  https://www.unicode.org/versions/Unicode17.0.0/core-spec/chapter-12/#G11280
- W3C Tamil layout resources: https://www.w3.org/International/ilreq/tamil/

These inform implementation only; runtime checks never access them.

## Verification

Record executed tests and measurements here and in BUILD_LOG.md. Include legal
and malformed Tamil, NFD equivalence, mixed scripts, stable finding identity,
exact raw spans, edit/switch races, pauses, external edits, missing/oversize/bad
files, report limits, unchanged-input reuse and UI stale-response isolation.
Run the repository's engine and frontend gates. Frozen and desktop acceptance
remain separate release requirements.

### LQA-1 implementation and verification, 2026-09-22

Implemented on `language-qa`: `tc_ai_bridge/language_qa.py` (rules/detection),
`language_qa_jobs.py` (bounded disposable background analysis), automatic open/import
and committed-edit hooks, `languageQa.status/pause`, and the separate collapsible
Language QA panel. A request carries its project path, so a delayed old-book pause
cannot pause the new book. Switching books, edits and pause invalidate generations;
project transaction-recovery failures block scanning. No schema or dependency changed.

- Full engine suite: **1,313 passed** (serial, 20m50s; xdist absent locally).
  Final focused suite: **31 passed**, including checks added after that full run.
- Frontend: **480 passed**; final panel checks **6 passed**; Svelte **0 errors,
  0 warnings**; production build passed (existing large-chunk notice remains).
- Final source protocol benchmark: 401 verses, 15.328 s background wall time while
  deliberately yielding; foreground ping median 0.264 ms / max 0.831 ms.
- Final frozen protocol benchmark: 401 verses, 15.484 s; ping median 0.327 ms / max
  1.321 ms. Pause, explicit Scripture edit, resume and reuse of three unchanged
  chapters passed. These are measurements on this machine, not universal guarantees.
- Typical 191-character rule pass median 0.6–0.8 ms; 20,000-character stress
  inputs measured 7.6–44.9 ms in a standalone check.
- The new engine modules occupied approximately **16 KB compressed** in the PYZ
  archive (15,788 bytes in the final build); no model, external service, dictionary
  download or new package is added.
  This is module payload, not a controlled installer-size comparison.
- Both frozen executables built in `engine/dist/language-qa/`. The standard
  whole-sidecar smoke is **blocked by pre-existing #170**: the package says 0.12.0,
  but both engine version constants at the starting HEAD say 0.11.0. The Language
  QA-specific frozen benchmark passes independently. #130 remains a separate known
  later smoke issue; it was not reached in this run.
- Installed-app visual/interactive acceptance: **NOT RUN**. Browser automation
  inventory was empty. No installer was published or installed.

Run the reproducible scoped source/frozen acceptance with:

```powershell
.\engine\.venv\Scripts\python.exe scripts\benchmark_language_qa.py
.\engine\.venv\Scripts\python.exe scripts\benchmark_language_qa.py --engine engine\dist\language-qa\bridge-engine.exe
```

Next stage needs approved Tamil house-style, spelling variants, terminology/names,
and reviewed grammar/sandhi examples. The supplied framework specifies categories;
it does not establish correct answers for arbitrary Tamil sentences. LQA-2 through
LQA-6 remain planned, and LQA-1 is not a publication-readiness certification.
