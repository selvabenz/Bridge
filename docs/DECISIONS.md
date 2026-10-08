# Decisions

Architectural and product decisions, newest first. Five lines each — this is a log, not
a design document.

Every entry answers: what was decided, why, and what it rules out. A decision with no
recorded reason gets re-litigated every few weeks, which is the thing this file exists
to prevent. Rejected ideas belong here too.

**Format**

```
## YYYY-MM-DD — Short title
**Decision:** what we are doing.
**Because:** the reason, including the alternative we didn't take.
**Rules out:** what this closes off, and what would have to change to reopen it.
**Revisit when:** a condition, or "not planned".
```

---

## 2026-09-11 — Bridge-private state gets a second per-project SQLite, not v15

**Decision:** The file-based Bridge-private stores (decisions, QA dispositions, audit
copies, progress, AI review, triage, issue resolutions, alignment history, metrics, team)
move into a new `bridge-workbench.sqlite3` beside the v14 semantic DB, with an append-only
`change_log`. translationCore-compatible files stay exactly as they are. A small app-level
`workspace.sqlite3` holds users, devices, the registry and a per-project rollup cache.
**Because:** the v14 DB's invariants (`RECORD_DEPENDENCY_TABLES`, `recovery_check`) are about
analysis records with a lifecycle; forcing decision tables into them weakens the tests that
protect staleness. A project folder must stay self-contained so a copied or shared project
keeps its review history, which rules out one app-level store as the primary.
**Rules out:** extending the semantic schema for non-analysis data; any Bridge feature that
needs the app-level DB to open a project. Long form: `docs/TEAM_ARCHITECTURE.md`.
**Revisit when:** the two databases need a cross-file transaction that the transaction
journal cannot provide.

## 2026-09-11 — Identity is a chosen name plus a device, never a password

**Decision:** Every recorded action carries a `user_id` and `device_id`. Locally a user
picks a display name at startup. In team mode an admin issues a join code that becomes a
per-device token. Roles are `project_admin`, `editor`, `reporter`. Historical `"human"`
rows stay as an explicit `legacy:human` actor and are not backfilled.
**Because:** the need is "who did it", not account security. Passwords add support cost for
field teams; username-only on a shared hub lets anyone act as anyone. A join code is the
smallest thing that makes attribution and roles real.
**Rules out:** password accounts and reset flows; reassigning past decisions to a named
user. Narrows #45 to this scope.
**Revisit when:** Bridge is hosted for people who are not already a known team.

## 2026-09-11 — The team hub is an optional sync peer, not the primary store

**Decision:** Collaboration is an optional `bridge-engine --serve` hub that exchanges
per-project change-log events with desktops and serves a separate browser dashboard. The
first slice syncs decisions, findings, progress and assignments only. Scripture text never
travels through the hub. Hub code lives behind an optional `[server]` extra and is never in
the desktop sidecar.
**Because:** offline-first is an invariant (entry below). A device must keep working with
the hub unreachable, so the desktop's own databases stay authoritative and the hub merges
events with the existing `expected_revision` check. Keeping Scripture out preserves Stage
9B.3b as the only Scripture writer.
**Rules out:** client-server Bridge; the hub as a second Scripture writer; a dashboard the
desktop exe depends on. Narrows #46 and #47.
**Revisit when:** a team needs live co-editing of verse text rather than shared review state.

## 2026-09-11 — Ideas are issues; no proposal stage, no status labels

**Decision:** New ideas go straight to an issue using the Idea template. There is no
Discussions stage, no `status:proposed`/`status:accepted` gate, and no ceremony
between filing and working. Triage is the maintainer reading open issues, labelling
them by `effort:`/`area:`, or closing them with a written reason.
**Because:** the alternative considered was ideas-in-Discussions promoted to issues on
acceptance, which keeps the board short and true. On a two-person project that is more
bookkeeping than it buys: it needs Discussions enabled, three more labels, and a
promotion step that is really just the maintainer deciding — which they can do on the
issue itself.
**Rules out:** reading "issue exists" as "we agreed to do this". The board is now a
mix of committed work and unsorted ideas, and the labels are what distinguish them.
**Revisit when:** the open-issue count stops being readable in one sitting, or a third
person joins.

## 2026-09-11 — Work goes directly onto main; CI is the only gate

**Decision:** No branch protection, no required review, no CODEOWNERS. Commits land on
`main`. `.github/workflows/ci.yml` runs the frontend, engine and Rust suites on every
push to `main` and on every PR.
**Because:** with two people, a required second review is a queue of one, and the real
defect risk in this project is not "nobody looked" — it is that the Python analysis
engine had no automated test run at all. Fixing the gate was worth more than adding
process around the merge.
**Rules out:** relying on review as a safety net. Everything now depends on CI being
honest, which is why `golden-notice` warns rather than blocks and why a red run on
`main` is treated as a real failure rather than a formality.
**Revisit when:** a third person joins, or a defect reaches a translation team that a
second reader would plausibly have caught.

## 2026-09-11 — Offline-first is an invariant, not a default

**Decision:** No feature may require a network call, an account or a login on a path a
translator uses during normal work.
**Because:** the users are translation teams working in the field, often without reliable
internet. It is the product's actual differentiator.
**Rules out:** a hosted database as the primary store, server-side analysis, and
login-gated features. A collaborative Bridge has to be sync-based, not client-server.
**Revisit when:** never for the desktop app. A separate web product is a separate decision.

## 2026-09-15 — The workspace database gets its own versioned ladder

**Decision:** `workspace.sqlite3` is the third versioned schema (`WORKSPACE_SCHEMA_VERSION`),
alongside the semantic database (v16) and the workbench (v2). v1 is exactly the unversioned
`devices`/`users` the first cut created, kept as `IF NOT EXISTS` so an existing file is
adopted; every later change is a forward block. No CLAUDE.md carve-out.
**Because:** the alternative -- keep `CREATE TABLE IF NOT EXISTS` and call the database
disposable -- is false for one table: `users` and `devices` ids are stamped on immutable
workbench `change_log` rows, so they must persist. Consistency with the other two ladders
costs one more version number and ~80 lines; the first non-additive change then has a home.
**Rules out:** ad-hoc column checks in `workspace_repository.py`; recreating the identity
tables. Long form: `docs/TEAM_ARCHITECTURE.md` §4, `docs/BUILD_LOG.md` 2026-09-15 (#77).
**Revisit when:** never for the ladder itself; the *contents* are revisited when the hub
adds `hub_credentials`.

## 2026-09-16 — One local user per installation until after v1; the hub after v1

**Decision:** Bridge keeps a single local user per installation (the OS-seeded name,
editable in Settings) through the v1 release. No user picker at startup, no in-app
user switching, no per-user or owner-annotated dashboard. Hub login and sync (#80)
are taken up after v1. Identity plumbing (#78: required `actor_id`, roles enum,
`authorize()`) still lands, because rows written without it cannot be re-attributed.
**Because:** the maintainer wants v1 simple, and today's users are one person per
laptop. Every workbench row already carries a stable `user_id` and `device_id`, so
adding a picker later attributes new rows correctly without touching old ones.
**Rules out:** for now, #97 (local login, switch user, owner column) — closed as
deferred, not rejected; anything in the UI that assumes several users on one machine.
**Revisit when:** v1 has shipped and a team shares a machine, or the hub slice starts.

## 2026-09-24 — Language QA benchmark: AI review rows are the positives, contradictions are "maybe"

**Decision:** The Phase 2 benchmark treats every in-scope row of the IRV Round 2 and Pass 3 reports as a positive (maintainer instruction). A row the reports contradict (a disagreeing fix, a reversal, a Pass 3 confirmed house form) or a human verdict against it is "maybe". Nothing is a negative.
**Because:** these are the only labelled data there is. The alternatives were strict human-confirmed rows only (5 in Philippians) or waiting for human review. The Philippians Rejected rows answered a different question and are not ground truth.
**Rules out:** reading benchmark precision as accuracy against verified truth; it is agreement with the AI review, and `docs/LANGUAGE_QA_BENCHMARK.md` says so at the top.
**Revisit when:** a human-labelled set exists; it fills the reserved "negative" class.

## 2026-09-24 — The accuracy gate is local; the latency gate is in CI

**Decision:** `scripts/language_qa_benchmark.py --gate` runs locally before a rule change and its output goes in BUILD_LOG. `scripts/benchmark_language_qa.py --gate --cores 2` runs in CI.
**Because:** the IRV text and review reports live outside the repository and are not committed. The latency benchmark uses a synthetic project and runs anywhere.
**Rules out:** CI catching a precision regression by itself; committing the IRV corpus or the reports to make it do so.
**Revisit when:** a redistributable benchmark slice is approved for the repository, or a self-hosted runner with the corpus exists.

## 2026-09-24 — Language QA has one polled status channel, not engine push

**Decision:** One count-only `languageQa.status` poll (`languageQaInline.ts`) feeds the marks and the panel. It runs at 500 ms while a pass is active and backs off to 10 s when idle, and a local edit or decision nudges it.
**Because:** the sidecar reader (`sidecar.rs`) routes stdout only to a pending request id, so the engine cannot push. Adding push needs a Rust and protocol change, larger than this phase. The brief allows the polled fallback.
**Rules out:** per-component Language QA pollers, since the panel no longer polls.
**Revisit when:** the protocol gains server-initiated messages for any other reason.

## 2026-09-24 — An ignore expires when its rule or pack version changes

**Decision:** An "ignored" or "rejected" Language QA decision recorded under a different pack version or rule revision is not re-applied. The finding returns flagged `previouslyIgnored` for re-checking.
**Because:** the layered-rules brief requires it: a changed rule may now be right where it was wrong. The alternative, silently keeping old ignores, hides a changed rule's new findings.
**Rules out:** treating an ignore as permanent. Every `RULE_VERSION` bump sends existing ignores back for re-check.
**Revisit when:** per-rule revisions replace the pack-wide version as the only trigger (Phase 3 pack).

## 2026-09-24 — Language QA rules are bundled data, generated from the corpus

**Decision:** Tamil rules live in a JSON rule pack (`engine/language_packs/ta-irv/` since 2026-09-30, formerly `engine/tc_ai_bridge/language_packs/ta-irv/`), loaded once per process and self-tested at load: every rule's examples run through the real `scan_text`, and a failure refuses the pack. The pack is generated by `scripts/build_ta_irv_pack.py` from the IRV corpus and the review reports, and is committed.
**Because:** abstains and examples need numbers from the corpus, such as "அந்த தேச- bare 31, doubled 8". Hand-written Python rules had no record of where their exceptions came from. The alternative, a grammar engine, is far beyond what the benchmark can justify.
**Rules out:** rules edited in Python; match primitives outside the closed set in `docs/LANGUAGE_QA_RULE_PACK.md`; shipping a pack whose examples fail.
**Revisit when:** a second language pack needs a primitive the set lacks.

## 2026-09-24 — A project override may only narrow a bundled rule

**Decision:** `.apps/translationCoreAI/language-packs/ta-irv/overrides.json` may disable a rule, take it off inline display, or add abstains. Anything else is refused, listed in the pack's `problems`, and the rest of the override still applies.
**Because:** a project that widens a rule changes what the benchmark measured without the benchmark knowing. Widening, including enabling `integrity.digits-in-text`, is a pack change.
**Rules out:** per-project new rules and per-project inline promotion.
**Revisit when:** Phase 6 scoped ignores need a new override shape.

## 2026-09-24 — Every வல்லினம் rule is inline on the maintainer's sign-off, below the 90% floor

**Decision:** The four vallinam rules and the wrong-consonant rule are drawn inline. Each carries an `inlineSignOff` holding the precision it was signed off at (41.2% to 55.0% strict; 15.4% for manner adverbs; none measured for wrong-consonant). The gate fails a signed-off rule that falls more than 2 points below its sign-off. The 90% floor still applies to any rule without one.
**Because:** this was the maintainer's instruction: a false positive costs one Ignore, and Phase 6 learns from ignores. The measured precision is agreement with a minority-form AI review, not accuracy, so it understates the rules.
**Rules out:** reading "inline" as "90% precise"; promoting a rule inline without a recorded sign-off.
**Revisit when:** a human-labelled set exists, or the ignore rate on a rule shows it is noise.
**Superseded 2026-09-28:** a human-labelled set now exists; see "Inline means at least 0.90 human-labelled precision" below.

## 2026-09-24 — A bare ை/க்கு word before a hard consonant is a root noun when the corpus inflects it

**Decision:** The accusative and dative rules abstain on words the IRV corpus shows to be root nouns rather than case forms. For ை, that is a word whose +யை form is attested (படை→படையை). For க்கு, it is a word whose ‑ில் or +க்கு form is attested (கிழக்கு→கிழக்கில்). The list is computed by the pack builder and stored as a `notLexical` abstain with its counts.
**Because:** "படை" (army) ends in ை but is not an accusative, and it takes no வல்லினம். The maintainer asked for a rule, not a word-by-word list. Attested inflection is the evidence available offline.
**Rules out:** a morphological analyser; hand-curated exception lists for this shape.
**Revisit when:** the Phase 5 lexicon provides part of speech.

## 2026-09-24 — The names adapter is not a seed for the house-style proper-noun list

**Decision:** `housestyle.properNouns` ships empty, and the vallinam rules abstain on its members once Phase 6 fills it. The names adapter's majority forms were evaluated as a seed and rejected.
**Because:** its majority forms include common words (they are transliteration matches, not a name list), and it takes about 15 s per book, over the pack load budget.
**Rules out:** deriving the name list from the adapter at scan time.
**Revisit when:** Phase 6 builds the name pack from a curated source.

## 2026-09-24 — The persisted Language QA scan is its own workbench table, holding results before decisions

**Decision:** Language QA results persist in a new `language_qa_cache` table (workbench v4), one row per chapter. Each row holds every verse's raw findings keyed by the verse's text hash. Decisions are applied on every pass, never cached.
**Because:** the brief named `check_cache`. But every reader of that table (`load_check_cache`: USFM, names, QA report, triage, analytics) loads all of a book's rows, and 150 chapter payloads would ride on each of those reads. Caching before decisions means a decision rescans nothing, and a live edit rescans only the edited verse.
**Rules out:** storing Language QA sections in `check_cache`; decision state inside the cache key.
**Revisit when:** `check_cache` gains per-section reads.

## 2026-09-24 — The Language QA job stage runs outside `_checker_lock`

**Decision:** The check-job stage runs the Language QA book pass on the job's thread in its preflight. It is serialised with the background worker by Language QA's own pass lock, not by `_checker_lock`.
**Because:** the dispatcher takes `_checker_lock` for `verse.runChecks`, so holding it for a book pass would make a save wait seconds. This violates the performance contract. The brief asked for `_checker_lock`; the pass lock gives the same guarantee, one authoritative pass at a time, without that cost.
**Rules out:** a second concurrent Language QA worker; blocking the dispatcher on Language QA.
**Revisit when:** Language QA needs a resource that `_checker_lock` guards.

## 2026-09-24 — A collection QA run is offered after import, never started automatically

**Decision:** `collection.runChecks` runs only when the user clicks "Run QA on all N books". The button is on the dashboard, which is where a multi-book import lands. While a run is active the app is read-only: no editing, no book switching, no `checks.start`. Pause takes effect between books.
**Because:** the brief requires it. A whole Bible is 66 sequential book jobs on one worker thread, which is an unattended operation (measured wall time in BUILD_LOG). An automatic start would lock a translator out of the book they just imported.
**Rules out:** background whole-collection checking while a translator works; pausing mid-book.
**Revisit when:** checks run in a separate worker process that can yield to edits.

## 2026-09-24 — Export is gated by one function, and the override is a recorded decision

**Decision:** `export.aligned` and `export.nonAligned` consult `reporting.publication_gate(project)`. Its blocking items are open AI critical issues, open Language QA findings with severity high and confidence high, and tN/tW checks marked needs-discussion. With blocking items open, nothing is written until the reviewer ticks "Export anyway". The override is then recorded as a `kind='qa'` decision with key `export.override`, listing the items open at that moment, so it lands in `change_log`.
**Because:** the brief requires one gate definition fed by every source. The full book report takes minutes, so the export gate reads persisted state only. Blocking with an override keeps export possible while making it deliberate.
**Rules out:** an export that silently ignores open blocking findings; a hard block with no override; a second gate definition.
**Revisit when:** a team role model decides who may override (TEAM_ARCHITECTURE §5).

## 2026-09-24 — The lexicon's data budget: about 3 MB, bounded by frequency

**Decision:** `language_packs/ta-irv/lexicon.json` lists every IRV word seen at least 3 times (19,618 of 79,208 distinct forms, capped at `--top`, default 60,000). It also carries the 9,515 words common enough to suggest (at least 6 occurrences), their precomputed one-cluster deletion buckets (55k keys), and 151 curated pairs. It is 3.0 MB on disk, takes about 150 ms to parse, and adds about 15 MB of resident memory once loaded. It is loaded lazily, off the dispatcher.
**Because:** the LQA-1 100 KiB budget was for runtime source, not data. The performance contract caps added RSS at 50 MB and startup at 200 ms, and a lazy load adds nothing to startup. A word left out of the list is by definition rare, which is the only thing the rule asks of it. Precomputing the buckets is what keeps a lookup a dict read.
**Rules out:** shipping the full 79k-form table or building buckets at load.
**Revisit when:** a second language pack needs a lexicon, or the frozen build's per-launch extraction makes 3 MB noticeable.

## 2026-09-24 — The lexicon is never updated from decisions

**Decision:** Translator decisions on lexicon findings (a Use, a false positive) are listed by `scripts/lexicon_feedback_report.py` for a person to fold into the curated corrections. Neither the engine nor the scan ever writes the lexicon.
**Because:** the brief, and `passage-aware-semantic-alignment.md` §31: learning may improve ranking, never become an unconditional rule. A decision is one reviewer's call at one verse, while the curated map is a hard rule applied everywhere.
**Rules out:** automatic lexicon growth; a decision silently becoming a `known-misspelling` pair.
**Revisit when:** not planned. House-style learning (Phase 6) narrows and ranks, and it is a separate mechanism.

## 2026-09-24 — House style is learned from decisions, and may only narrow or rank

**Decision:** The project's house style is derived from translator decisions by a visible, deterministic learner (`housestyle.py`).
- Three ignores of one (rule, word) in a book, with no Use since, create a learned `word-in-book` entry at once, with its evidence and an Undo.
- Project scope and rule disabling are only ever *proposed*.
- Three Uses of one suggestion rank it first.

An entry may hide findings, add list members, reorder suggestions, or propose a disable. It may never add or widen a match, change a severity, or make a rule inline.
**Because:** the maintainer asked that "the system will learn from its mistake" (2026-09-24). The brief and §31 require that learning improve ranking and never become an unconditional rule.
**Rules out:** hidden models; auto-applied project-wide changes; learning that widens what is flagged; silently raising a rule's benchmark precision (house-style hides are reported separately).
**Revisit when:** a team role model decides who may accept project-wide proposals.

## 2026-09-24 — House-style scopes, thresholds and storage

**Decision:** There are four scopes: `word-in-book`, `word-in-project`, `rule-in-book` and `rule-in-project`. The thresholds are 3 ignores to learn, 2 books to propose project scope, 20 decisions at an 80% ignore rate to propose a rule disable, and 3 Uses for a preference. Entries are `human_decisions` rows of kind `housestyle`. That kind needed workbench v5, a table rebuild to widen the kind CHECK constraint, with a data-preservation test. A project scope is written into every materialized book. Remove and Undo write a new state and never delete. An undone pair is never re-learned.
**Because:** the brief's thresholds. Per-book rows keep every scan reading only its own workbench. A second table would duplicate the decision machinery (sync, change_log, history) the decisions table already has.
**Rules out:** deleting house style; a project-scope store outside the books (a lazy book misses an entry until it is accepted again, documented in LANGUAGE_QA_HOUSESTYLE.md).
**Revisit when:** the team hub (TEAM_ARCHITECTURE §7) gives a project-level store.

## 2026-09-24 — Language QA states its own boundary, permanently

**Decision:** Every Language QA status carries a structured `coverage` from `language_qa.coverage()`: the categories it checks, and the ones it never checks (agreement, pronoun/number shifts, meaning shifts, omissions/additions, textual basis, theology). Each item is in Tamil and English, and the statement names a hand-off doc. The panel shows it whenever it is open, not behind a disclosure. The out-of-scope list is a permanent boundary of the offline engine (LANGUAGE_QA_PLAN LQA-3), not a backlog.
**Because:** a clean text-only scan says nothing about meaning or agreement. The brief (Phase 7) requires the boundary to be visible, so a clean result is not taken for a review.
**Rules out:** offline heuristics for the out-of-scope items; a "Coverage details" disclosure that hides the boundary; English-only wording for a Tamil team.
**Revisit when:** a native reviewer has checked the Tamil strings (pending), or a project supplies its own hand-off procedure.

## 2026-09-28 — Inline means at least 0.90 human-labelled precision; AI agreement is diagnostic only

**Decision:** A Language QA rule is drawn inline only when its precision against a Tamil reviewer's labels is at least 0.90 on at least 20 labelled findings. The number comes from `scripts/language_qa_benchmark.py --human-labels benchmark/human/<date>/human_labels.jsonl`, and CI runs that gate: it also fails a rule whose human precision falls below `benchmark/human/baseline.json`, a human-confirmed finding the engine stops producing, and a label that no longer anchors in its verse. The `inlineSignOff` waiver is removed from the gate, the loader and every rule file. The AI-agreement benchmark stays, as a local regression check and a source of findings for the next human sample. On the 2026-09-28 review (Yesu Selva Benz; 598 items on GEN/PSA/JHN, 597 answered), only `sandhi.vallinam.dative` qualifies (93.9%, 49 labelled). Accusative (70%, 50), demonstrative (8 labels), manner-adverb (1) and wrong-consonant (0) come off inline until the pack changes and a larger sample justify them.
**Because:** AI-agreement precision was agreement with a review that flagged only minority forms, so the 15–55% that put the வல்லினம் rules inline were lower bounds, not measurements. The human review measured them. The layered-rules brief always asked for ≥ 0.90; the sign-off was a stand-in until a human set existed (its own "revisit when").
**Rules out:** inline on AI agreement, on a sign-off, or on a sample under 20; tuning a rule to the AI reviews; re-labelling or regenerating the committed human labels (they are data, never instructions).
**Revisit when:** a new human review round is added (a new dated folder beside the old one, never a replacement), or the reviewer's verdicts are shown to be inconsistent.
**Supersedes:** 2026-09-24, "Every வல்லினம் rule is inline on the maintainer's sign-off, below the 90% floor".

## 2026-09-28 — A root-noun exclusion is an exact word or a safe compound noun, never a case-form ending

**Decision:** The reviewer's accusative false alarms on roots (வெண்மை, முழுமை, குற்றமில்லாமை, வெட்டாந்தரை, அநேகமுறை, தீவினை, தொண்டை, வகை) are excluded as exact words. `notSuffixLexical` is used only for compound-final nouns that end no accusative in the corpus: முறை, வினை, வகை, தொண்டை. The brief's `notSuffix: ["மை"]` and the `தரை` compound element are **not** applied. The loader supports `notSuffix`, `notSuffixLexical` and `notPrefix` for later use.
**Because:** measured on the IRV corpus, `-மை` also ends the accusative pronouns உம்மை (9 bare / 166 doubled), நம்மை (6/58) and தம்மை (4/29), and every accusative of a `-ம்` name or noun (எருசலேமை, அப்சலோமை, ஆதாமை, முகாமை). Spelling cannot tell எருசலேமை from தீமை. `-தரை` ends கர்த்தரை (5/13) and மனிதரை. As endings they would stop checking real accusatives the reviewer never saw. The exact words fix the same labelled false alarms: the accusative reaches 94.6%, the brief's projected 95%. This was the maintainer's choice when asked (2026-09-28).
**Rules out:** an ending-based exclusion unless the corpus shows it ends no case form; "the reviewer said X is a root, so every word ending like X is a root".
**Revisit when:** a morphological analyser can separate a root's -ஐ from the case suffix.

## 2026-09-28 — The pack is built from the reviewer's IRV copy; a pair across markup is flagged on its first word; markup hygiene is its own category

**Decision:**
- **The corpus.** `ta-irv@1.1.0` is built from `D:\Claude Lab\IRV Tamil`, the copy the 2026-09-28 reviewer labelled (all 224 flagged labels anchor there, 163 in `Documents\IRV Tamil`). The step-7 Bible run uses the same copy. The AI-agreement benchmark stays on the copy its reviews were made against.
- **Pairs across markup.** A வல்லினம் pair that straddles a poetry line (`\q`) or lifted inline markup is flagged on its first word, with the fix confined to that word. Before, it was dropped and counted as a limitation. It is still dropped when the first word itself is split by markup.
- **The `usfm` category.** A new category, `usfm` (layer integrity), holds `integrity.space-before-note-end`: severity low, panel-only, reworded as "markup formatting, not a text error".
**Because:**
- **The corpus:** the maintainer chose the corrected copy (2026-09-28).
- **Pairs across markup:** the reviewer confirmed that both sampled poetry-line pairs (PSA 135:21, 143:11) need doubling.
- **The category:** the reviewer rejected all 15 sampled note-end spaces as text errors ("USFM formatting").
**Rules out:**
- comparing the new Bible-wide finding count with the old 7,401 as like-for-like: the text changed too, so the before/after is also measured on the new copy;
- drawing a finding over markup.
**Revisit when:** the maintainer declares one IRV copy canonical for every tool.

## 2026-09-28 — `lexicon.rare-near-common` is off by default; `lexicon.known-misspelling` is inline

**Decision:**
- **`rare-near-common`.** It ships disabled (`RuleMeta.enabled=False`). Its 21 reviewed false alarms are `protected` words in `lexicon.json`, never flagged by either lexicon rule.
- **`known-misspelling`.** It is inline. The 43 human-confirmed pairs are kept in the curated map without the frequency filters that guard AI-review pairs, and are listed as `humanConfirmed`.
- **Re-enabling `rare-near-common`** needs two things: a morphology filter (never propose a form that is itself an attested inflection: -ஆள்/-ஆல்/-ஆன்/-ஆர் endings, -ையும் …), and at least 0.90 on a new human sample of at least 20.
**Because:** the 2026-09-28 reviewer marked all 21 sampled rare-near-common findings wrong, since each suggestion was another real word (a gender/tense contrast, வாள்/வாழ், காலை/காளை, a name). They confirmed all 43 known-misspelling findings.
**Rules out:** re-enabling rare-near-common by tuning `MAX_DISTANCE` or the rarity thresholds alone; learning protected words or pairs from decisions (the lexicon is still never updated from decisions).
**Revisit when:** a morphology filter exists, or a project wants the audit as a panel-only report.

## 2026-09-29 — A rule may be inline on its whole population, when that is under 20

**Decision:** A Language QA rule may be drawn inline when either:
- **(a)** at least 20 of its findings are human-labelled, at ≥ 0.90 combined precision over the review rounds; or
- **(b)** every finding it produces in the whole collection has a confirming human label, and none of its labels is wrong.

**How (b) is checked:**
- **The population file.** `benchmark/human/population.json` is written locally from the full corpus (`--write-population --irv-dir`), because CI has no corpus. It records each small rule's findings and a hash of the rule's definition.
- **The gate.** It fails a (b) rule whose definition changed since the file was written, or whose findings are not all confirmed.
- **Confirming labels.** A TP or SPLIT label, or an abstained MISSED row, at the same verse, overlapping the finding.

**Under (b) today:**
- inline: `typo.suffix.dropped-tha` (2 of 2) and `sandhi.vallinam.wrong-consonant` (1 of 1);
- panel-only: `typo.divine-name.vowel-drop` (5 of 11) and `dative-stem` (4 of 13), until the rest are labelled.

**Promoted under (a)** with both rounds: demonstrative (24, 100%), manner-adverb (21), direction (25), `common/spacing.extra` (23).

**Because:** the ≥ 20 rule cannot be met by a rule whose whole-Bible population is under 20. For such a rule, labelling everything is a stronger claim than a sample (brief, round 2).

**Rules out:**
- (b) on a sample;
- (b) with a stale population;
- (b) with any false positive among the labels.

**Revisit when:** the collection grows beyond the IRV (a new book's findings are unlabelled, which the gate reports as incomplete coverage once the population is rewritten).

## 2026-09-29 — A misspelling pair is trusted only when a human confirmed it

**Decision:**
- **Provenance.** Every pair in the lexicon's `deprecated` map carries a provenance: `human` (confirmed in a review round) or `ai-review` (from the Round 2 / Pass 3 CSVs only).
- **What each provenance gets.** `lexicon.known-misspelling` reports a human pair at high confidence and inline. It reports an ai-review pair at medium confidence, panel-only, with "awaits human confirmation".
- **Rejected pairs.** Pairs the reviewer rejected are removed, and their wrong side becomes a `protected` word.
- **Unsure pairs.** Pairs the reviewer was unsure of stay ai-review and go into the next sample.
- **Fragments.** Single-grapheme tokens are not counted as words unless they are real monosyllables.
**Because:** round 2 found 4 of 8 sampled pairs were not misspellings but meaning or style changes inherited from the AI rows: திடமனதாயிரு→திடமானதாயிரு changes meaning; கவனிக்காதே→கவனிக்காமல் turns an imperative into a participle; பூட்டுக்களையும் is a valid plural; பெருந்தொனியாய்→ஆக is style. The human-confirmed pairs are 47 of 47.
**Rules out:** an AI-review pair drawn inline; an AI-review pair at high confidence; learning pairs from decisions.
**Revisit when:** a review round confirms or rejects the remaining ai-review pairs (135 today, 30 of them from reports added on 2026-09-29).

## 2026-09-29 — தான் after a case form: offer the pronoun first, the clitic second

**Decision:** `sandhi.clitic.fused` is disabled. The வல்லினம் rules no longer abstain on தான் (or ஆவது, which starts with a vowel and so never triggers them). A bare `X தான்` after a case form is flagged with two ranked suggestions:
- rank 1, `Xத் தான்`: the reflexive pronoun, written apart with doubling;
- rank 2, `Xத்தான்`: the clitic, written fused.

The finding is medium confidence, with the message "தான்: பிரதிப்பெயர் என்றால் பிரித்து ஒற்றுடன்; ஒட்டுச்சொல் என்றால் ஒட்டி". The mechanism is a rule-level `contexts` entry in the pack, a next-word condition with its own confidence, message and ranked alternatives. The spaced clitics கூட, மட்டும், போல, என்று, என, எனும் keep the abstain.
**Because:** all six `sandhi.clitic.fused` findings in round 2 were the pronoun (David, Jonah, the offerer), which the reviewer writes separately with doubling (பெட்டிக்குத் தான், தேவனுக்குத் தான்). Round 1 established that the clitic is written fused (அதைத்தான்). So a bare `X தான்` is always a defect, and which fix is right depends on syntax the engine cannot see.
**Rules out:** choosing one form automatically; the clitic rule's fused-only suggestion.
**Revisit when:** a review round labels a batch of the new தான் findings (the next sample); if the clitic reading turns out common, reorder the suggestions per context.

## 2026-09-29 — Split words are curated pairs, not a heuristic

**Decision:** `lexicon.known-split` matches the lexicon's `splits` map as adjacent word pairs in each verse and suggests the joined form. It is high confidence, category word-joining, and inline under rule (b).
- **The map.** It holds only pairs a reviewer judged `SPLIT`: சு வரை → சுவரை (×4 in the Bible), நே போ → நேபோ (×7).
- **Protected tokens.** One-grapheme tokens judged a word, name or interjection (சீ, சோ, நோ, பை) are `protected`.
- **Retired.** The `word-joining.orphan-syllable` heuristic is retired.
- **Cache.** The chapter cache key now includes the lexicon fingerprint, split map included, so a rebuilt map rescans.
**Because:** the whole Bible has 23 single-grapheme non-words, of which 10 are real splits. The rest are names and interjections a heuristic would join.
**Rules out:** joining a fragment by rule; adding a split without a human verdict.
**Revisit when:** a review round finds splits of a different shape (more than two tokens, or not single-grapheme).

## 2026-10-07 — indic-qa's Tamil checker runs as a layer of ta-irv, over the OV dictionary
**Decision:** This replaces one line of the first 2026-10-07 indic-qa entry
below ("Rules out: running indic-qa's Tamil profile"). The maintainer asked for
everything indic-qa's `webapp` Tamil QA has and Bridge did not.
- **ta-irv stays.** Its JSON rules, its pack version (1.1.0, so no reviewer's
  ignore expires) and its rule ids are unchanged.
- **The layer.** `pack.json` gains an `indicQa` block. The vendored Tamil
  profile runs once per book over the BSI 1957 OV dictionary, in
  `ta-irv/dictionary/` through `scripts/sync_indic_qa.py`, at the same pinned
  commit. Its findings are 17 `indicqa.*` rules: catalogue in
  `indic_qa_tamil.py`, Bridge view in `ta-irv/indic_qa_rules.json`.
- **No duplicates.** A layer finding is dropped when the pack or the common
  rules flagged the same verse span in the same category, counting raw findings
  before decisions. In verse text the layer does not run the checks Bridge has
  (double space, zero width, repeated punctuation, space before punctuation, the
  யெகோவா/-வதற்கு defects). Digits, repeated words and a space before a note's
  end stay off, as the 2026-09-28 review decided.
- **Headings and footnotes.** The layer also checks section headings and
  footnote prose. A footnote finding has raw verse offsets, and its fix goes
  through the one verse writer. A heading finding offers no fix: its offsets
  index the heading, which no Bridge writer edits.
- **Unknown words.** One rarely used in the IRV (≤ 2×) whose best OV
  suggestion is a typing slip or a reviewed correction is
  `indicqa.lex.near-miss`. One that splits into two known words is
  `indicqa.lex.compound`. Any other unknown word is `indicqa.lex.unknown`, which
  is off.
- **Policy.** Phase-1 rules apply: everything is panel-only, and nothing is
  high severity with high confidence.
**Because:** measured on the IRV, 2,200–2,700 OV-unknown tokens per book are
mostly valid modern Tamil (honorific -ார், IRV-only words). The book cap is
3,000, so reporting them would crowd out everything else. The rare,
slip-shaped ones are about 10 per book and mostly real (யெகோவவை, கீல்→கீழ்).
A frequent form is house practice, as the OV dictionary's own REPORT.md says.
**Rules out:** a second Tamil pack; running the layer's checks over verse text
that Bridge already checks; applying a fix to a heading; turning
`indicqa.lex.unknown` on without counts; changing ta-irv's version for the layer.
**Revisit when:** reviewer labels exist for an `indicqa.*` rule (inline gate), or
a heading writer exists.

## 2026-10-07 — indic-qa findings are drawn in the text by default, behind the reviewer's own threshold
**Decision:** Every enabled indic-qa rule (pa/ml/hi/or packs and ta-irv's
layer) is drawn in the verse text unless the reviewer's Settings > Language QA
threshold hides it. Two settings must both pass: a minimum measured reviewer
precision (0–100, default 0) and a lowest rule confidence (default low). A
rule nobody labelled has no measured precision and passes the slider.
Precision ships per pack as `rule_precision.json`, written by
`language_qa_benchmark.py --write-precision`. A finding now carries two
answers. `inline` is the reviewed flag and stays exactly as before. `drawn`
is what the text shows, worked out per request by `language_qa_drawn.py`.
**Because:** the maintainer of the indic-qa work (Benz) wants the reviewer to
see what indic-qa sees, as indic-qa's own editor shows it, and to narrow it
themselves. Flipping `inline: true` in the pack files instead would fail the
CI human gate for every rule with fewer than 20 labels, and Odia and Punjabi
have none. It would also erase the difference between "reviewed as reliable"
and "shown because the reviewer asked".
**Rules out:** treating `drawn` as review evidence. The human gate, the
2026-09-28 rule and CI still read only `inline`, and promoting a rule is still
a `rule_versions.json` edit with its own entry here. It also rules out
applying the threshold during a pass: it must never rescan, touch a cache key
or change a finding id.
**Supersedes:** the "panel-only" clause of "indic-qa findings are recomputed
per pass…" and the Policy bullet of the Tamil layer entry, as far as the
verse text goes. Both still hold for the reviewed `inline` flag.
**Revisit when:** a team shares one threshold (the hub, #46), or reviewers
find the default too noisy and want a different starting value.

## 2026-10-07 — Learned fixes come from the reviewer's own single-word edits, per book, and never touch a dictionary
**Decision:** When a saved verse edit replaces exactly one word and changes
nothing else visible, Bridge records (old → new) in the workbench
(`language_qa_learned_fixes`). Recurrences of the old word in the same book
become ordinary Language QA findings (`learned.replacement`, category
`learned`, blue dotted) offering the new word. Typing the word back retracts
the fix; Forget and Restore re-write the row. Settings > Language QA turns the
feature off. One tokenizer, `language_qa.word_occurrences` over the lifted
text, does both the recording and the offering.
**Because:** this is indic-qa's editor feature, and the reviewer's own change
is the most trustworthy suggestion Bridge can make. It is drawn inline as
project data, like the termbase.
**Rules out:**
- Writing the word into a dictionary file (NOTICE contract 2).
- Using indic-qa's `single_token_change` and per-profile `token_re` for
  recording. They disagree with Bridge's tokenizer about what one word is.
- Sharing fixes across a collection's books in this version. That would mean
  one fsync'd write per sibling on every edit, up to 65 for a Bible.
**Revisit when:** reviewers ask for a fix learned in one book to apply in its
siblings (the read side could merge the siblings' tables once per pass).

## 2026-10-07 — A scoped Language QA correction is one journalled edit per verse, within one book, confirmed first
**Decision:** "Use in this chapter / this book" changes the same finding
(same rule and text, or same rule for a warning) everywhere the last pass
reported it in that scope. Before anything is written, the reviewer sees
every place with its before and after. Each verse is then written by
`apply_scripture_edit`, the same call `verse.edit` makes, with its own
journal transaction, backup and verseEdits record. A
`language_qa_batches` row groups them for one Undo. A verse whose text
changed since the pass (its hash) is skipped and reported. Undo leaves alone
any verse edited after the batch. Ignoring across a scope writes no
Scripture: decisions, plus a house-style entry at book scope.
**Because:** the maintainer of the indic-qa work chose verse, chapter and book
scope (2026-10-07). The cross-book "whole Bible" scope indic-qa has was left
out deliberately.
**Rules out:**
- a scoped write across a collection's books;
- a free-text search-and-replace over Scripture: only findings of the last
  pass are touched;
- a new writer or journal format;
- correcting a heading or footnote finding from the verse panel.
**Revisit when:** the maintainer rules on whether a bulk Use is an
"alternative path for applying corrections" (CLAUDE.md stop-and-ask), or
asks for the cross-book scope.

## 2026-10-07 — A project word is a house-style list entry, applied after the scan
**Decision:** indic-qa's "Add to dictionary" is a house-style entry with
`list: "projectWords"`, scope word-in-book or word-in-project. It hides
typo, name, consistency and learned-fix findings on that word. It is applied
in `HouseStyle.suppresses`, after the scan, and kept out of
`list_fingerprint`, so adding or removing a word never rescans a book.
**Because:** a translator's "this word is fine" is a project decision, and
house style already holds those, with provenance, removal and sync. The
vendored dictionaries are read-only (NOTICE contract 2), and a dictionary
write would also change every other project of that language.
**Rules out:**
- writing `extra_words.txt`;
- calling the vendored `Checker.set_ignored`, which is not needed when
  suppression happens after the scan;
- hiding a grammar or sandhi lead because one of its words is listed.
**Revisit when:** the indic-qa checker's own consistency majorities should
count a project word (they do not today).

## 2026-10-07 — The reference Bible is a folder the reviewer chooses; related words are ported, not vendored
**Decision:** indic-qa's OV panel, "OV occurrences" and related words read
a reference Bible from a folder set per pack in Settings › Language QA. The
folder is an indic-qa dictionary folder with `verses.tsv`, or a folder of
USFM books read through Bridge's importer. Tamil falls back to the 1957 OV
that ta-irv already ships. Its plain text is cached under
`%LOCALAPPDATA%\Bridge\data\reference-cache\`, keyed by the folder's
file names, sizes and mtimes. Related words are Bridge's own port of
upstream `related.py`, built in memory once per session and pack.
**Because:** the OV text for Hindi, Malayalam, Odia and Punjabi has no
settled licence (NOTICE.md), so Bridge must not ship it, and a reviewer who
has a copy can still use it. Upstream `related.py` reads indic-qa's own
book objects and private checker counts; porting ~150 lines onto Language
QA's tokenizer keeps one meaning of "word" across Bridge.
**Rules out:**
- bundling any OV-derived file for hi, ml, or or pa;
- a network fetch of a reference text;
- parsing the reference folder's USFM anywhere but the importer;
- writing to the reference folder.
**Revisit when:** the OV licence question is answered.

## 2026-10-07 — Learned fixes are shared across a collection's books
**Decision:** A fix learned in one book is offered in every materialized
book of its collection. Benz chose this on 2026-10-07. It supersedes the
"Rules out: sharing fixes across a collection's books in this version" line
of the earlier learned-fixes entry, which is otherwise unchanged. The design
is the one that entry's "Revisit when" named: the read side merges the
siblings' tables.
- Each book keeps its own rows.
- A pass reads the merged view. Siblings are read read-only, once per open
  project, and again after this project writes a fix.
- Retractions count against the collection's net total, which never goes
  below zero.
- Forget and Restore write every book that holds the fix, and forgotten
  anywhere means forgotten.

**Because:** the reviewer asked for it. A translation team's spelling
decisions are about the language, not one book. Reading the siblings
avoids the cost the earlier entry rejected: a fsync'd write to up to 65
siblings on every edit.

**Rules out:**
- writing a learned edit into every sibling's table;
- a collection-level store outside the books' own workbenches;
- materializing a lazy book to read or write its fixes.

**Revisit when:** the team hub (#46) syncs workbench rows; a shared table
would then be simpler than a merge.

## 2026-10-08 — Automatic alignment writes what two passes agree on, without a click (#219)

**Decision:** `alignment.window.autoAlign` asks the same verse window twice, once source-first and once target-first, and writes everything **both** passes give, with no per-item click:
- same-verse groups to tC `alignmentData/`, through the existing compiler and `_save_alignment`;
- cross-verse link groups;
- null decisions (grammatical, implicit or explicitation, with the identical reason in both passes).

Every write carries `origin: "ai-auto"` and a `runId`. Anything only one pass gave, or whose null reasons differ, is a suggestion with ✓/×. A token neither pass placed is reported as a possible omission or addition and is never forced. A reviewer's existing group, link or decision is never written over.

Once the project has completed alignments, an agreed **cross-verse** edge is also checked against the offline corpus scorer (#138). It is blocked when the corpus's top candidate for that source word is a different target word, or when the target word is contested. A corpus that says nothing about the word does not block it. On a cold project, two-pass agreement is the whole gate.

**Who decided:** @RevantCI. Sign-off was obtained by @selvabenz on 2026-10-08 and posted on #214 (comment 6051693007), with a request to object there if this is not what was approved.
**Because:** #214 made every model pick a proposal, so on a fresh book a verse could only come out aligned word by word, even where the model was plainly right. CLAUDE.md's rule is "two methods agreeing is the gate; a confidence threshold on one number is still not". Two readings from opposite directions are two methods in that sense, though weaker ones than #146's model-plus-corpus. Confidences are recorded and decide nothing.
**Rules out:** writing on one pass's say-so at any confidence; overwriting a reviewer's alignment; widening this to Scripture text or to findings, which stays a fresh question.
**Limits, recorded so nobody takes them for guarantees:** two samples of one model are correlated, so agreement lowers the error rate but does not bound it. Every number is uncalibrated (`two-pass-agreement-v1`). No real-provider request has been sent yet (#131), so precision is unknown until the `external` test is run with a key.
**Revisit when:** the first real-provider measurement on the PHP fixtures is recorded; or the maintainer objects on #214.

## 2026-10-08 — Unknown words are drawn, under the Settings slider
**Decision:** A word not in the language's dictionary is reported as the
indic-qa web app reports it: `<code>.lex.unknown` for hi, ml, or and pa, and
`indicqa.lex.unknown` for the Tamil layer. Each is on, at low severity and low
confidence, and never `inline`. Being drawn follows the reviewer's threshold
(language_qa_drawn): the default draws it, and a confidence floor of "medium"
hides it from the text but keeps it in the panel. Benz chose this on
2026-10-08.
- **Tamil.** The profile has no `lex.irv_accept_min`, unlike the other four
  (default 5), so the layer applies that bar itself (`IRV_ACCEPT_MIN = 5`). A
  word the IRV uses five times or more is house practice and is not
  reported. A compound the IRV uses more than twice is accepted, as the web
  app's grey compound mark says. The reviewed near-miss narrowing is
  unchanged.
- **Measured on 2026-10-08:**

  | Book | Verses | `lex.unknown` | All indic-qa findings |
  |---|---|---|---|
  | Tamil Ruth | 85 | 129 | 159 |
  | Tamil Genesis | 1,533 | 1,152 | 1,447 |
  | Tamil Luke | 1,140 | 1,140 | 1,266 |
  | Malayalam Genesis | 1,533 | 658 | 1,645 |
  | Hindi Genesis | 1,533 | 4 | 24 |

  The 2026-10-07 Tamil count of 2,200–2,700 was without the IRV bar.
- **Human labels.** Malayalam has 27 human-labelled `ml.lex.unknown`
  findings, all 27 confirmed (100%). The gates pass for all three
  languages.
- **The book finding limit** now drops the least certain book-stage findings
  first (confidence, then severity), so unknown words give way before a
  typing slip. Each rule's omitted count is a coverage note.
- **The layer's de-duplication** defers only to a finding that is drawn
  (reviewed inline). A listed-only overlap no longer removes the word's only
  mark.

**Because:** an unmarked word has no menu, so no suggestions and no "Add".
That was the most visible gap against the web app. The confidence floor
already gives the reviewer a one-step way to hide them.

**Supersedes:**
- the "`<code>.lex.unknown` is off" clause of "indic-qa findings are
  recomputed per pass ... panel-only";
- the "Any other unknown word is `indicqa.lex.unknown`, which is off" and
  "turning `indicqa.lex.unknown` on without counts" clauses of "indic-qa's
  Tamil checker runs as a layer".

**Rules out:**
- drawing the accepted-word grey marks (irv_ok, inflected_ok, compound,
  sandhi_ok, learned);
- flipping any reviewed `inline` flag;
- a confidence above low for an unknown word.

**Revisit when:** reviewer labels for `hi.lex.unknown` or
`indicqa.lex.unknown` exist, or the maintainer wants a different Tamil IRV
bar.

<!-- New entries go above this line. -->

## 2026-10-07 — indic-qa's checker core is vendored in-process for Punjabi, Malayalam, Hindi and Odia
**Decision:** Language QA for pa, ml, hi and or runs indic-qa's own checker
(`selvabenz/indic-qa`, pinned). The code is a byte-exact copy in
`engine/vendor/indic-qa/`, imported into the engine like versification. Its
dictionaries are pack data in `engine/language_packs/<code>-irv/dictionary/`.
Tamil stays on `ta-irv`. This answers CLAUDE.md's "stop and ask" item for a
new vendored tree; the maintainer chose it on 2026-10-07.
**Because:** those rules already went through reviewer rounds (1–3 Oct 2026).
Porting each profile's tables into rule-pack JSON would take weeks per language
and lose that review. A pip git dependency would need an upstream package and a
network fetch at build time.
**Rules out:** editing the vendored files (adaptations live in Bridge's
adapter); running indic-qa's Tamil profile; writing to its dictionaries; any
part of its editor, store or `.SFM` writer.
**Revisit when:** indic-qa is published as a package, or a language needs
behaviour that its profile hooks cannot express.

## 2026-10-07 — Corpus-attested agreement leads are in scope for the indic-qa packs, listed only
**Decision:** The Hindi, Punjabi, Malayalam and Odia packs report indic-qa's
grammar leads under a new `grammar` category. These are genitive gender,
oblique case, honorific case and bigram rules, each backed by corpus counts.
They are never drawn inline and never block export. The coverage statement
says agreement across a whole clause is still not checked.
**Because:** the maintainer chose to include them. Each lead names its
corpus evidence. LQA-3's line was drawn against clause-level judgement, which
these leads do not claim.
**Rules out:** a Tamil agreement rule without its own decision; drawing a
grammar lead inline before the human gate; treating a clean pass as a grammar
review.
**Revisit when:** labelled rounds give a grammar rule ≥ 0.90 on ≥ 20 labels.

## 2026-10-07 — indic-qa findings are recomputed per pass, from a whole-IRV snapshot, and panel-only
**Decision:** A profile pack's findings come from one book step each pass. They
are not cached per verse. IRV-wide word counts come from a shipped snapshot of
indic-qa's own index; the open book's live text replaces its share. In phase 1
every rule is `inline: false`, no rule is high severity with high confidence,
and `<code>.lex.unknown` is off.
**Because:**
- The checker's leads depend on the whole book and on the whole IRV.
- `words.tsv` misses every IRV-only word.
- The CI human gate fails any inline rule without 20 labels.
**Rules out:** seeding from `words.tsv`; drawing a rule inline because indic-qa
does; rebuilding the clusters on a pass where the text did not change.
**Revisit when:** reviewer-workbook labels are imported for a rule, or the edit
latency of the cluster rebuild needs fixing (Malayalam: 2.5 s).

## 2026-10-07 — A project's Language QA pack is a manifest key, set in Settings
**Decision:** `manifest.json` `language_qa.pack` holds `auto`, `off` or a
registered pack name. Settings > Language QA writes it through
`languageQa.setPack`, as one journalled transaction that touches no other key.
The engine then rebinds and starts a pass.
**Because:** the ml-irv registry already reads this key. A shared-script
language (Devanagari) is never guessed, so a project whose metadata is wrong
or missing needs a person to choose. translationCore ignores keys it does not
know.
**Rules out:** guessing a pack from the script for Devanagari or Bengali; a
setting stored per machine rather than with the project.
**Revisit when:** the team hub (#46) syncs project settings.
