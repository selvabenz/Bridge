# Developer Guide

The "why" and "where things stand" companion to
[`DEVELOPER_SETUP.md`](DEVELOPER_SETUP.md) (the "how to get it running"
doc). This is a curated summary for personal/team reference — the full
detail always lives in [`ARCHITECTURE.md`](ARCHITECTURE.md) (current-state
architecture and the repository's doc map, §9) and
[`BUILD_LOG.md`](BUILD_LOG.md) (session-by-session build log). Read those two
when you need the full story on something; use this doc to find out *whether*
you need to.

---

## 2. Phase roadmap — planning vs. actual outcome

The original plan (Claude Code sessions that did Phases 1-3) laid out 7
phases. What actually shipped often diverged from the plan, for good
reasons — this table is the fast way to see both.

| Phase | Planned | Actual outcome |
|---|---|---|
| **1** | Protocol & sidecar consolidation | ✅ Done. `BridgeEngine` = `GreekRoomEngine` + `tc_ai_bridge` behind one JSON protocol. |
| **2** | Svelte frontend wired to real sidecar | ✅ Done. Single-window UI, confirmed on a real Windows machine with a real translationCore project. |
| **3** | Decision persistence, chapter switching, whole-book, Settings, Export | ✅ Done. Stable finding IDs, `checks.start/status/cancel/retry` background jobs replacing a blocking frontend loop. |
| *(unplanned)* | — | Import pipeline built first, ahead of schedule — a working import blocks everything downstream. Also: a 66-book import that took 4–6 min and hit a hard timeout is now ~5–6s (lazy per-book normalization); a real security fix (plaintext API keys could persist to disk); background check jobs; and Milestone 3B.3's schema-constrained, evidence-grounded automatic tN/tW review with resumable verse/chapter/book jobs and persisted current/stale results. |
| **4** | USFM structural checker + versification | ✅ Done (2026-08-20 / 2026-08-21). Both vendored from `BibleNLP/greek-room`, wired into the existing check pipeline. Backend/protocol-only — no dedicated UI panel, matching how other checks surface as inline findings. |
| **5** | Names & Transliteration (Uroman + Smart Edit Distance) | ✅ Done (2026-08-21). Whole-book spelling-consistency check wired into `verse.runChecks`'s existing `"local"` checks list — no frontend change needed. |
| **6** | Alignment Intelligence (UAlign corpus stats) | ✅ Statistics engine done (2026-08-24). Turned out to need a real prerequisite not in the original plan: you can't compute stats over "human-approved alignments" with no way to create one — so the **manual word-alignment editor** (see `ALIGNMENT.md`) was built first, then corpus statistics (co-occurrence, translation probability, PMI, optional SED phonetic boost) computed from Bridge's own completed alignments — not a vendored `ualign.py`. Backend/protocol-only, two read-only methods, no UI yet. |
| **7** | Paratext/Logos connectors, AI explain, drag-and-drop | ✅ All four slices have real work. AI alignment proposals and drag-and-drop are verified end-to-end. AI explain is wired to real materialized tN/tW evidence. The Paratext companion performs identity-gated, idempotent Project Note handoff and preserves sent state after restart. The Logos PowerShell/COM bridge is process/protocol tested; later live Logos 53.1 inbound/outbound navigation was also verified. |
| *(Stage 3 follow-up)* | — | Language-independent semantic passage mapping and a 40-case IRVTam discovery queue are built. All 40 Luke/Philippians proposals were human-reviewed and verified after restart: 38 confirmed, one corrected, and one rejected (95% combined proposal agreement). This became the validation evidence base for the later passage-semantic stages; the Beta 15 instructions below are retained as a historical checkpoint, not the current resume boundary. |
| *(Passage-semantic Stages 4-8)* | Source/target semantic inventories, passage-aware location, meaning preservation, bidirectional QA | ✅ Done (2026-09-01 / 2026-09-02). Deterministic throughout — none of these stages uses a language model. Note the numbering collision: these are semantic **Stages**, a different axis from the Greek Room **Phases** above. Production caveat: `SemanticEmbeddingProvider.available` is `False` in the shipped app, so location runs there use lexical/structural evidence only; `scripts/seed_review_fixture.py` seeds a project with a fixture provider for exercising the review UI. |
| *(Stage 9A)* | Human QA review, evidence inspection, disposition workflow | ✅ Done, including Stage 9A.4 orchestration (2026-09-03). A 2026-09-04 follow-up kept a running analysis job visible while the reviewer navigates (it was previously dropped), and fixed the Logos VBScript shim going silent on any COM error, and bounded the Bridge navigation publish retry. Alignment Review is a top-level Word/Semantic/Passage/QA surface; `qaReview.*`, `semanticReview.*` and `reviewHistory.*` record decisions with optimistic concurrency. **Run analysis** now executes Stages 5–8 as a persisted background job for passage/chapter/book/range and refreshes the queue. Findings are classified only — no correction generation or application, which remains Stage 9B. Normal runtime visibly reports limited retrieval until a production multilingual embedding provider is configured. |
| *(Stage 9B)* | Correction wording, review, explicit apply, affected re-analysis, semantic verification | ✅ Done through **9B.4**. 9B.0 schema/eligibility, 9B.1 wording generation, 9B.2 review UI, 9B.3a persistence/recovery, 9B.3b the first authorized Scripture write behind explicit human confirmation, 9B.3c affected re-analysis, and 9B.4 positive semantic verification plus explicit `CORRECTED` acknowledgement are present. For the current schema versions of the three databases see `ARCHITECTURE.md` §3. Stage 8 target-hash and resource-conflict blockers, Case C source-inventory consistency, and terminal verification refresh were repaired before release v0.9.6. A correction is never verified merely because a finding disappeared: current Stage 6B/7/8 evidence must positively satisfy the original obligation, and `PASSED` alone never sets `CORRECTED`. |
| *(Project QA report)* | — | ✅ Done (2026-09-04). **Generate report** on the project screen builds a whole-collection QA report in a background sidecar job (`report.generate/status/get/cancel/export`, `tc_ai_bridge/qa_report.py`, `report_jobs.py`): every book's Greek Room / tN / tW / alignment / AI-review progress, and every issue as a filterable row (category, book, chapter, verse, issue, AI proposal, fixed by human/machine, pass/fail) with charts and CSV / TSV / print-to-PDF export. Needed one piece of new persistence: a succeeded check job now snapshots its findings per chapter (the rollup only ever kept ids) — originally `.apps/translationCoreAI/checkFindings/<book>/<chapter>.json`, a `check_findings` row in `bridge-workbench.sqlite3` since #77. Installed-app acceptance still NOT RUN. |
| *(AI triage)* | — | ✅ Done (2026-09-07). Optional, **online-only** false-positive scoring layered on that report — see §5. Backend, protocol and report-screen UI; live model behaviour and installed-app acceptance NOT RUN. |
| *(Offline Language QA, #169)* | — | ✅ Layered-rules phases 1–7 done on `language-qa` and merged to `main` (2026-09-24 through 2026-09-29, Benz). Tamil `ta-irv` rule pack as data, corpus lexicon, termbase v3, house style, a human-labelled benchmark gating inline marks in CI, Language QA as a check-job stage, the **Collection QA** runner on the dashboard (`collection.runChecks`: every book in turn, resumable via `collection.json` `qaRuns[]`, never automatic; #215) and the export gate. Whole-Bible IRV run measured at 62 min on 2026-09-28. Desktop acceptance (A56–A82) still pending. See `docs/LANGUAGE_QA_PLAN.md`. |

### 2.1 Complete project history and current continuation

Bridge has two numbering systems. The original product roadmap uses **Phases
1–7**; the later passage-semantic architecture uses **Stages 1–9**. They are
different axes, and both are now implemented. This chronology reconciles the
historical checkpoints below with the current repository.

#### Original Phases 1–7

1. **Protocol and sidecar consolidation.** `BridgeEngine` unified Greek Room
   and `tc_ai_bridge` behind the JSON sidecar protocol. Existing project,
   decision, edit, and transaction-journal behavior stayed authoritative.
2. **Real desktop frontend.** Svelte/Tauri was connected to the real sidecar,
   with a single-window Windows/WebView2 project, chapter, verse, finding, and
   navigation workflow.
3. **Persistent application workflow.** Stable projects, decisions, edits,
   settings, background check jobs, aligned/non-aligned export, restart
   recovery, safe provider settings, and collection-aware import landed. The
   import path was reduced from roughly 4–6 minutes for 66 books to about 5–6
   seconds through lazy per-book normalization.
4. **USFM and versification.** The Greek Room structural checker and
   deterministic versification detection, org-reference normalization, and
   back-versification map were integrated. Reference numbers remain anchors,
   not universal semantic boundaries.
5. **Names and transliteration.** Whole-book consistency checking combined
   Uroman with vendored Smart Edit Distance and performance-safe candidate
   blocking. Similarity remains evidence, never proof of an error.
6. **Alignment intelligence.** Bridge first added the manual Word Alignment
   editor required to create human-approved data, then calculated
   co-occurrence, translation probability, PMI, and optional phonetic-boost
   statistics from Bridge's completed alignments. Native translationCore
   alignment remains the verse-local lexical representation.
7. **External integrations and AI assistance.** AI alignment proposals,
   evidence-grounded AI explanations, native drag-and-drop import,
   identity-gated Paratext note handoff, and Paratext/Logos navigation were
   implemented. Proposals require the appropriate human action; external
   availability never becomes authority over Bridge state.

#### Translation-help and Beta 6–15 evolution

The beta sequence progressively added occurrence-aware tN/tW review, Basic and
Advanced modes, resumable verse/chapter/book AI jobs, current/stale lifecycle,
clear cancellation and retry, exact target selections, persisted resolutions,
Paratext handoff, language-aware passage mappings, and the ranked semantic
validation UI. Installed acceptance established project preservation,
human-selection protection, restart persistence, explicit Apply AI proposal,
`Cancelled` status, and alignment/export behavior.

The IRVTam validation corpus supplied 40 machine proposals across Luke and
Philippians. Human review produced 38 confirmations, one correction, one
rejection, and 95% combined proposal agreement. The durable cross-verse
regression is:

```text
source PHP 1:3  τῷ Θεῷ μου
target PHP 1:6  என் தேவனை
CROSS_VERSE_REORDERED · meaning preserved
```

This is a representative corpus case, not a Tamil rule.

The validation queue that recorded those decisions — the dashboard's
**Validate semantic mappings** button, `SemanticMappingValidation.svelte`,
`semanticValidation.list/decide` and `semantic_validation_service.py` — was
removed on 2026-09-16 (#100) once the review was complete. The per-project
audit files it wrote no longer exist on any machine (the Tamil IRV projects were
re-imported after the #76 cutover), so this summary and the Beta 15 table below
are the surviving record of the review. The Stage 3 mapping engine underneath
(`semantic_mapping*.py`, `semantic_review_policy.py`) was kept: the AI review
pack in `ai_client.py` still uses it.

#### Passage-semantic Stages 1–9

- **Stages 1, 2, and 2.1:** repository analysis, codebase-specific technical
  design, and the review/lifecycle, semantic-unit, ownership, lineage,
  Unicode-coordinate, policy-version, and SQLite amendments.
- **Stage 3:** canonical schemas, token lineage/instances, semantic units,
  lexical solutions, coverage accounts, correction proposals, SQLite
  migration/recovery/backup, and cross-language validation foundation.
- **Stage 4:** current-text authority and runtime integration. Editable chapter
  JSON supplies wording; preserved imported USFM supplies structure. Edits
  stale dependent records without silently relocating human work.
- **Stage 5:** comprehensive UHB/UGNT source semantic inventory. tN/tW/TWL
  enrich and validate the inventory rather than defining it.
- **Stage 6A:** an independent target semantic inventory whose construction
  does not assume source expectations.
- **Stage 6B:** passage-aware source-to-target location with structural-window
  expansion and controlled search budgets. Exhaustion means review is needed,
  not omission.
- **Stage 7:** deterministic meaning-preservation assessment, separate from
  location and coverage.
- **Stage 8:** separate source-coverage and target-support audits, conservative
  possible-error classifications, and explicit resource-conflict evidence.
- **Stage 9A:** the Word/Semantic/Passage/QA Alignment Review surface,
  evidence inspection, review history, dispositions, scoped queues, and
  persisted analysis jobs.
- **Stage 9B.0–9B.4:** correction eligibility and wording, proposal review,
  crash-safe application ledger, explicit human apply with exact CAS,
  affected-passage re-analysis, positive semantic verification, and separate
  human `CORRECTED` acknowledgement.

#### Stabilization and releases

*The release list and the baseline block below are a snapshot taken on
2026-09-11, kept as a record. Releases since then (0.10.0 to 0.11.0) are in
`BUILD_LOG.md` and the `RELEASE_*.md` notes; current schema versions are in
`ARCHITECTURE.md` §3.*

- **v0.9.4:** Stage 9B.4 acceptance boundary and correction verification.
- **v0.9.5:** canonical acceptance-fixture repair.
- **v0.9.6:** Case C source-inventory consistency and terminal verification
  refresh repair. This is the current public release at commit `b0de092`.
- **V1.1, local after v0.9.6:** Stage 7 comparison became Unicode-canonical and
  grapheme-safe. The Tamil polarity defect was fixed without a language branch;
  semantic/cache versions were advanced while schema stayed v14 and the public
  app version stayed 0.9.6.

Local baseline immediately before this documentation consolidation:

```text
branch                         main
public baseline                b0de092 / v0.9.6
V1.1 implementation            5cc3ce7
V1.1 tests                     ef49e9e
V1.1 handoff                   4444ea7
companion schema               v14
public application version     0.9.6
remote status                  local main 3 commits ahead before this docs commit
```

The verified V1.1 gates are 175 focused tests, 1069 full Python/Greek Room
tests, 310 frontend tests, Svelte check with zero errors/warnings, production
frontend build, 12 Rust tests, `cargo check`, and `git diff --check`.

The next safe operational sequence is to push V1.1 only when authorized, build
an internal installed-acceptance package without publishing a release, verify
real multilingual projects and cache invalidation, record the results, and
obtain an explicit V1.2 boundary. Do not begin export/Scripture Burrito,
cross-verse visualization, new providers, or new semantic dimensions merely
because the numbered phases and stages are complete.

## 3. Dependencies

### Python (`engine/pyproject.toml`)

| Package | Type | Notes |
|---|---|---|
| `regex>=2024.5.15` | Required | Needed by the vendored USFM checker and by `versification.py`'s own dependency; floor raised to also satisfy `uroman`'s requirement. |
| `uroman>=1.3.1.1` | Required | Real PyPI package (name-checked — unlike Wildebeest, not a name trap). Same author (Ulf Hermjakob, USC/ISI) as Wildebeest and the vendored Greek Room tools. No known installability problems on any current Python version, so it's a hard dependency, not optional. License note: PyPI/upstream both claim "Apache" but the actual bundled `LICENSE.txt` is a custom MIT-style license with its own mandatory attribution clause — verified by reading the installed package, not the metadata. |
| `usfmtc==0.4.8` | Required | The one USFM parser (#91): the USFM Technical Committee's reference implementation, wrapped by `tc_ai_bridge/usfm_parser.py` (the only module allowed to import it). Pure Python; pulls in `rply` 0.7.8 (BSD-3-Clause) and `appdirs` 1.4.4 (MIT), plus the `regex` above. Its own bundled `LICENSE` is MIT (read from the installed package). Pinned exactly because stored verse boundaries are cut from its element positions. Measured quirks — a string argument that `os.path.exists` accepts is opened as a file; a lexer quadratic in input length, so books are parsed per chapter — are in that module's docstring. |
| `wildebeest-nlp==0.9.2` | Optional (`[wildebeest]` extra) | Real package name is `wildebeest-nlp`, **not** `wildebeest` (that name belongs to an unrelated ShopRunner image-processing package). Pinned to the only release that exists. Does not install under Python 3.13 (a docstring contains a lone-surrogate escape 3.13 rejects at compile time — confirmed still broken on upstream's GitHub HEAD too). The `WildebeestAdapter` degrades to a mock automatically whether this extra is installed or not, so leaving it uninstalled is always safe — just means Wildebeest-specific checks won't run for real. |
| `greekroom` (published PyPI package) | **Not used** | Left commented out in `pyproject.toml`. Only ships `owl` and `gr_utilities` submodules — USFM checker, versification, and Smart Edit Distance are none of those, so all three are vendored separately from source instead (see §4). |
| `pytest>=7.0`, `pyinstaller>=6.0` | Dev only | Test running and sidecar packaging. |

### Frontend (`package.json`)

| Package | Notes |
|---|---|
| `@tauri-apps/api`, `@tauri-apps/plugin-dialog`, `@tauri-apps/cli` | Tauri v2 core + native file dialogs. |
| `iso-639-3` | Offline searchable language catalog for import metadata (adds ~94 KB gzip to the bundle — Vite's non-fatal 500 KB chunk warning is from this; splittable later if startup size becomes a concern). |
| `svelte`, `svelte-check`, `@sveltejs/vite-plugin-svelte`, `@tsconfig/svelte` | Svelte 4 + TS tooling. |
| `tailwindcss`, `postcss`, `autoprefixer` | Styling. |
| `word-aligner` | Alignment-related utility (translationCore ecosystem package). |
| `vite`, `typescript` | Build tooling. |

---

## 5. AI triage — optional false-positive scoring

Greek Room checks are deliberately noisy: they say "this is objectively
suspicious," and a lot of what is suspicious is fine. AI triage is an
**optional, online-only overlay** that asks the configured model how likely
each already-persisted finding is to be a false positive, so the report
screen can hide the noisiest ones behind a slider.

It is an overlay in the strict sense: no check, report, decision or export
path reads a verdict, a finding is never rewritten, and with no API key and
no verdicts the report renders exactly as it did before triage existed.
That is the local-first requirement — nothing offline may depend on it.

**Modules.** `tc_ai_bridge/triage.py` (hashing, batching, context, parsing,
the per-book run), `tc_ai_bridge/triage_prompts.py` (four prompt families,
kept separate so wording can be tuned without touching logic), and
`triage_jobs.py` (a `ReportJobManager`-shaped background job in its own
lock domain).

**RPCs.** `triage.run` (`book?`, `force?`), `triage.status`,
`triage.cancel`, `triage.override` (`book`, `hash`, `verdict` — empty
clears), `triage.clear`, `triage.results`. `triage.results` is in
`report.get`'s 180 s timeout class; everything else stays interactive at
30 s so a long run can always be cancelled.

**Stored shape** — one `triage_verdicts` row per book in `bridge-workbench.sqlite3` (until #77, `.apps/translationCoreAI/triage/<book>.json`); the payload is unchanged:

```json
{"schemaVersion": 1, "bookId": "rut", "updatedAt": "...",
 "entries": {"<hash20>": {
    "findingId": "...", "chapter": "1", "verse": "3-4",
    "checkType": "wildebeest.script.mixed", "family": "mechanical",
    "verdict": "false_positive|true_positive|uncertain",
    "confidence": 0, "reason": "...", "model": "...", "timestamp": "...",
    "userOverride": null }}}
```

Four design points worth knowing before changing any of it:

1. **One file per book, not per chapter, and not SQLite.** Measured on the
   real 66-book collections: the first open of any file on Windows costs
   ~20–27 ms regardless of size, so file *count* dominates — 66 files read
   in ~2 s where 1,189 would take ~25–30 s. Bridge's existing
   `bridge-semantic.sqlite3` is per book too, so joining it would not have
   made a collection read one query; it would only have inherited the
   passage-semantic runtime's recovery states, migrations and per-open
   integrity check.
2. **Records are keyed by a hash of the finding's *evidence*** — NFC- and
   whitespace-normalised `original_text`, `suggested_replacement`,
   `explanation` and evidence pairs, plus book/chapter/verse/check type.
   Deliberately **not** `_stable_finding_id`, which must stay stable across
   an edit so a human decision survives. A verdict about evidence that
   changed is worthless, so it is discarded rather than carried forward.
   Offsets are excluded so text shifting inside a verse does not orphan
   every verdict in it. `qa_report` stamps this hash on Greek Room rows as
   `triageHash`, and the report screen merges verdicts by dict lookup.
3. **Nothing is ever re-bought.** A run over unchanged findings makes zero
   model calls. A `userOverride` is skipped even under `force` — overriding
   is also how a reviewer stops paying for a finding they have judged.
4. **Failure is always survivable.** An unparseable response or a network
   error degrades that batch to `uncertain` at confidence 0 — the one shape
   the slider can never hide — and logs the raw text. The reason names the
   actual cause. A run whose every batch failed reports `failed`, not
   `succeeded`.

**Concurrency.** `triage.override` (dispatcher thread) and the run worker
both load-merge-save the same book file under one `BridgeEngine._triage_lock`,
and the worker re-reads immediately before merging each batch, so an
override recorded mid-run survives. The progress rollup had the same
lost-update class between its two writers while it was one file; since #77 each
writer touches only its own rows, and what remains is the recomputed totals row
(last writer wins) — see the known gaps in `BUILD_LOG.md`.

**The slider** lives in `AppSettings.triage_hide_threshold` (default 90,
`0` = off, otherwise clamped to 50–100). Only a `false_positive` verdict at
or above the threshold hides anything; `uncertain` and `true_positive` are
always shown, because hiding a real translation error is a far worse
failure than leaving a false positive on screen.

---

## 6. Finding context menu — one component, three menus

`src/lib/components/FindingContextMenu.svelte` is presentation only: it takes
`x`/`y`, a `findingLabel` and an `actions` array, and dispatches `action` and
`close`. It owns Escape/Tab/outside-click dismissal, roving arrow-key focus,
focus restore, and viewport clamping (`positionInsideViewport`, `EDGE_GAP = 8`).
Three different menus mount it, and **none of them owns it** — put dismissal
or positioning behaviour in the component, and only the action list in a caller:

| Caller | Findings from | Menu offers | Decisions written as |
|---|---|---|---|
| `VerseList.svelte`, on a finding span (`contextMenu` state) | `findingsByVerse` store — engine `QaFinding`s | **Accept finding** (applies the proposed correction, then files the accept) / **Ignore** | `FindingStatus` via `decideLocalFinding`, shared with `ReviewPanel.svelte` |
| `AlignmentQaMode.svelte` (QA review queue) | `QaFindingList.svelte` rows, which dispatch `contextmenu` upward | **Apply proposed fix** plus the four `REVIEWER_ACTIONS` | `QaDisposition` via `decideFinding`, labels from `REVIEWER_ACTIONS` |
| `VerseList.svelte`, on the verse row itself (`verseMenu` state, issue #69) | Not finding-scoped — this is the fallback for a verse with nothing to right-click | **AI review ▸ Verse/Chapter/Book** (one level of flyout) / **Edit verse** | Not a decision — triggers `startVerseEdit()` (shared, `verseEditor.ts`) or `requestAIReview()` (new, `aiReviewUi.ts`) directly |

An action can carry a `submenu: FindingMenuAction[]` (added for #69, installed-
acceptance-verified 2026-09-12 — `docs/archive/V1_1_ACCEPTANCE_01.md`): clicking it
opens a second `.finding-menu` instead of dispatching `action`, positioned off
the parent item (flips to its left if it wouldn't fit on the right); a leaf
inside dispatches `action` exactly like a top-level item, so a caller never
needs to know which level an id came from. `button[aria-expanded="true"]`
keeps the parent visibly highlighted while its flyout is open — focus moves
into the submenu the instant it opens, so without that rule the parent looked
unremarkable while it was the one thing on screen with an open flyout.

The verse-row menu's two actions each needed state shared with `ReviewPanel.svelte`
without either component reaching into the other's internals — the same
problem #70's alignment glyph solved for the Align Words modal
(`alignmentUi.ts`). AI review couldn't use that exact shape: `startAIReview()`
carries real job/polling state (`aiJob`, `aiPollTimer`, …) that has no reason
to move out of `ReviewPanel`. `aiReviewUi.ts` is a request, not a state move:
`requestAIReview(chapter, verse, scope)` sets a store `ReviewPanel` consumes
reactively, and mirrors `ReviewPanel`'s own `aiJobBusy` back out as
`aiJobActive` so the menu can disable its scopes to match.

Three rules a new contributor will otherwise get wrong:

1. **The menu is never the only route to an action** — that is an
   accessibility defect, and it is what issue #38 was reopened for. All
   three menus must respond to `ContextMenu` and `Shift+F10` and advertise
   `aria-keyshortcuts` — including the verse-row menu on a verse with no
   findings, where Shift+F10 used to simply do nothing before #69.
2. **One tab stop per list, not per finding.** `QaFindingList`'s listbox
   viewport and `VerseList`'s verse row are each a single tab stop, with arrow
   keys moving the active item inside them. Making every row or every
   `<mark>` focusable would put hundreds of tab stops in a checked chapter.
3. **The two surfaces do not share a decision vocabulary, on purpose** — and
   "accept" means opposite things in them (`Accept finding` = "this is a real
   problem"; `Accept translation as correct` = "there is no problem"). Every
   item carries a `title` hint saying which way it points. Don't unify the two
   models to make the labels match.
4. **The editor menu mirrors `ReviewPanel`'s own two actions on an open Greek
   Room finding, and should keep doing so.** It deliberately has no separate
   apply-fix item: applying a correction and accepting the finding that
   prompted it are one act, so `Accept finding` does both when
   `suggested_replacement` and offsets are present and files the accept alone
   when they are not. A correction that fails to apply is *not* accepted
   instead — surface the reason and leave the menu open.
5. **Key decisions by the displayed verse string, not `QaFinding.verse`.**
   That field is a numeric anchor (`bridge_service.py:238` takes the first
   numeric component), so a verse bridge `3-4` becomes `3` and the decision
   lands under the wrong reference and misses the `"chapter:verse"` store key.
   `ReviewPanel` uses `$currentChapter`/`$selectedVerse`; `VerseList` carries
   the exact verse on its `contextMenu` state for the same reason.

---

## 7. Where the deeper docs live

One doc map for the repository, in [`ARCHITECTURE.md`](ARCHITECTURE.md) §9.
