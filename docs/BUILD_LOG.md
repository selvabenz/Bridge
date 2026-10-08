# Build log: Bridge v0.9.4

Updated: 2026-09-07

> **Start with [`DEVELOPER_GUIDE.md`](DEVELOPER_GUIDE.md) instead** for an
> oriented, up-to-date summary of the stack decisions, phase roadmap, and
> dependencies. Come here for the full investigation behind a specific
> decision or gotcha — exact root causes, file:line references, and the
> session-by-session narrative that the summary distills. This file is the
> continuously-updated detailed record; `DEVELOPER_GUIDE.md` is what to read
> first to get oriented.

## Stage 9A.4 follow-up - QA queue follows the active canonical scope (2026-09-04)

Installed acceptance showed that analysis correctly ran a newly selected
range, but `AlignmentQaMode` then refreshed `qaReview.getQueue` without that
range. The backend therefore returned every persisted project finding: after
PHP 1:3-1:6 followed by PHP 1:1, both scopes appeared together.

Schema v10 adds `qa_finding_scope_references`, populated from each finding's
source and target semantic units and backfilled during migration. Queue
filtering now occurs in SQLite before total count, ordering and keyset
pagination. `SOURCE_COVERAGE` follows canonical source-unit ownership;
`TARGET_SUPPORT` follows canonical target-unit ownership. This keeps a Greek
PHP 1:3 relationship in the selected source scope even when Tamil realizes it
in 1:6, without filtering only by the displayed target verse. Existing rows,
human dispositions, notes and append-only history are never deleted.

The complete wire path now carries `canonicalReferences` through Svelte,
TypeScript, Tauri/Rust, the Python protocol/service and the repository. Scope
input changes clear the previous queue immediately; late responses are
generation-gated; the persisted completed job is authoritative for an
affected-only run. The UI identifies the default as **Review scope: Current
analysis range**.

Verification: 42 focused queue/migration tests, 101 repository/runtime/review
tests, 126 frontend tests, 5 Rust tests, and the complete 599-test Python suite
pass. Svelte/TypeScript reports 0 errors and 0 warnings; Cargo check, the
production frontend build, frozen-sidecar rebuild, Tauri release build and
NSIS packaging pass. The exact installer was installed successfully, and a
disposable installed-sidecar acceptance verified 26 persisted findings split
13/13 between PHP 1:1 and PHP 1:3-1:6, restored a reviewed decision/note after
switching back, retained a real cross-verse 1:3-to-1:6 association, and
reported scoped pagination/counts accurately.

Separate blocker: the repository-wide frozen smoke reaches project import but
currently expects `exactDuplicate` where the engine returns
`possibleDuplicate`. No duplicate-import code was changed in this scoped fix.
Stage 9B remains unstarted.

## Stage 9A.4 — Analysis orchestration and queue population (2026-09-03)

The previously recorded empty-queue product gap is resolved. Alignment
Review QA mode now has an explicit **Run analysis** control for current
passage, chapter, book, or selected range. It runs the frozen Stage 5, 6A,
6B, 7 and 8 engines in dependency order on a background worker, polls a
durable job snapshot, shows stage-based progress, supports cooperative
cancellation, and refreshes the indexed QA queue when Stage 8 completes.
Opening a project or the review surface only reads analysis state; it never
starts analysis automatically.

Schema v9 adds `analysis_jobs`, CAS revisions, recovery of abandoned workers,
and a partial unique index enforcing one queued/running job per project even
across manager instances. Jobs record scope and fingerprints, reused and
created run ids, provider capability, warnings/failures, per-stage timings,
and Stage 8's source-coverage/target-support/synthesis/persistence profile.
Content-addressed Stage 5–8 caches remain authoritative and unchanged.

After a target edit, Bridge compares current per-reference hashes, expands
changed verses to the smallest available structural passage, and offers
**Re-run affected analysis**. A smaller refreshed run composes with still-
current cached results so its parent chapter/book scope does not remain
incorrectly stale. Interrupted or failed jobs never appear current.

The UI distinguishes `NOT_ANALYZED`, `PARTIALLY_ANALYZED`, `STALE`, `RUNNING`,
`FAILED`, `SEARCH_INCOMPLETE`, and current-with-no-findings. Incomplete Stage
6B search remains `SEARCH_INCOMPLETE`; Stage 8's existing gate still prevents
an unresolved search from becoming an omission finding.

Normal runtime never uses fixture vectors. `SemanticEmbeddingProvider`
descriptors now say whether a provider is fixture-only, normal orchestration
rejects such providers, and the PHP seeder opts in explicitly while itself
running through the same orchestration path. With no production multilingual
embedding provider, analysis remains available but visibly reports limited
semantic retrieval; persisted findings remain reviewable.

Verification: 17 Stage 9A.4 Python tests, 11 PHP walkthrough tests, 104
frontend tests, 5 Rust tests, and the complete 577-test Python suite pass.
Svelte/TypeScript has 0 errors and 0 warnings; the production frontend build
passes. Analysis leaves editable Scripture JSON, preserved imported USFM, and
native translationCore alignment data unchanged.

Still deferred: whole-Bible orchestration, Stage 8 write batching (it remains
persistence-bound), a production multilingual embedding provider, Stage 9B
correction generation/application, and installed-app manual acceptance.

## Stage 9A.3 — PHP 1:3-6 review fixture, and a Stage 8 read-only bug (2026-09-02)

Closes Stage 9A: a seeded fixture project a human can actually open, the
Philippians walkthrough asserted end to end, and one real defect found by
running it.

### The bug: a QA audit made its own project read-only

`scripts/seed_review_fixture.py` seeded a project fine, but *reopening* it
threw `attempt to write a readonly database` from
`bind_project_metadata` — during `PassageSemanticRuntime.__init__`, so the
project could not be opened at all.

Cause: `FoundationRepository.__init__` runs `recovery_check()`, which sets
`self.read_only = True` if it finds any integrity problem. Its
`known_record_tables` map did not contain `QA_RUN`, but Stage 8's
`save_qa_audit_run` registers `QA_RUN` dependency edges. So every dependency
edge Stage 8 wrote was reported as `unknown-record-dependency-type`, the
database flipped to read-only on the next open, and the next write failed.

**Any project that had run a QA audit was unusable from its second open
onward.** Introduced by Stage 8 (`807353d`) and invisible until something
opened a companion database twice — which nothing in the test suite did,
because tests build a fresh project per test. This is exactly the
second-call class of bug the vendored-tool notes warn about, in Bridge's own
code this time.

Fix: `QA_RUN` (and `LOCATION_RELATIONSHIP`, for symmetry with the stale-
propagation map) added to `known_record_tables`, with a comment tying the
two maps together. Regressions added: one asserting a QA_RUN edge is
recognised, one parametrized over every dependency type the engine writes,
and one confirming a genuinely unknown type is *still* reported — the check
had to keep working, not just stop complaining.

### The fixture project

`scripts/seed_review_fixture.py` builds a real translationCore-compatible
IRV Tamil Philippians project and runs Stages 5-8 over it with a fixture
embedding provider, leaving the results in the project's own companion
database.

This is what makes the review UI exercisable in the desktop app at all. The
shipped app has no embedding provider (`available = False`), so it cannot
produce the reordered-passage analysis itself — but the review surface only
ever *reads* persisted findings, so a pre-seeded project works. The location
run fingerprint includes the embedding descriptor, so the app will not
mistake a seeded run for one of its own.

Generated rather than committed, matching the repository's practice of not
committing companion databases. Seeded output: 28 relationships, 12
cross-verse, `reordered: True`, 12 findings in the review queue.

### The walkthrough

`engine/tests/review/test_php_review_walkthrough_stage9a.py` drives the review APIs
over the reordered passage — Greek 1:3 to Tamil 1:6, 1:4 to 1:4, 1:5 to 1:3,
1:6 to 1:5 — and imports the seeder, so what a human opens is what the tests
assert on.

The load-bearing assertion is
`test_no_omission_is_raised_merely_because_a_verse_moved`: every
POSSIBLE_OMISSION must correspond to a source unit with no located
realization anywhere, never to one simply found in a different verse. A
reordered translation must not read as a missing one. The rest cover
cross-verse relationships being visible and marked LOCATED rather than as a
failure to locate, location and meaning being reported from separate records
with neither field leaking into the other, every finding exposing its
evidence layers, a reviewer accepting, rejecting and deferring with the
reason landing in structured history, a stale revision being refused, and —
directly — that reviewing the passage leaves both `php/1.json` and
`php.usfm` byte-identical.

### Stop condition

A human can open Alignment Review, choose QA mode, select a possible issue,
see the source evidence, the Stage 6B location, the Stage 7 meaning
assessment, the Stage 8 coverage/support reasoning, the applicable resource
evidence, the alternatives and the history, decide, and move to the next
issue — without modifying Scripture. No correction generation exists, and no
export path changed.

**Not verified here:** the final click-through in the running desktop app.
That needs the Tauri build plus both sidecars, and jsdom cannot check real
1366x768 layout. The structure is asserted and the engine paths are covered;
the pixels are not.

## Stage 9A.2 — Alignment Review UI: shell, QA mode, evidence inspector (2026-09-02)

The first UI for the Stage 4-8 semantic pipeline. A reviewer can now open
Alignment Review, work the QA queue, inspect a finding's evidence in layers,
and record one of four decisions - without any route to changing Scripture.
Frontend only: no Python or Rust changed in this step, and the backend tree
is byte-identical to the one that passed 532 tests at Stage 9A.1.

Semantic and Passage modes landed in the same pass rather than as
placeholders, since both turned out to need no new backend: the read chain
already existed (a finding's location `runId` to `semanticLocation.getRange`
to `targetSemantic.getRange`). The remaining Stage 9A.3 work is the seeded
fixture project, the PHP 1:3-6 walkthrough, and final verification.

### Vitest

Bridge had no frontend test framework; `svelte-check` plus `npm run build`
were the whole gate. Added Vitest + @testing-library/svelte + jsdom
(`vitest.config.ts`, `npm test`). Playwright was deliberately **not** added:
it roughly doubles the test-infra surface, and Bridge ships offline into a
Tauri shell where a headless-browser harness buys little over asserting on
the DOM these components actually produce. The cost of that choice is
recorded under "what these tests cannot check" below.

### Components

- `AlignmentReview.svelte` - the shell, with Word / Semantic / Passage / QA
  tabs following the WAI-ARIA tabs pattern (one tab stop, arrow keys move,
  each panel labelled by its tab). Opens in QA mode.
- `SemanticAlignmentMode.svelte` - the same focused record presented
  relationship-first: source meaning, target realization, and an assessment
  strip keeping location, meaning and coverage in separate cells. Each
  realization kind (lexical, grammatical, pronominalized, implicit) and each
  property (split, merged, cross-verse) carries a sentence saying what it
  means, described neutrally rather than as a defect.
- `PassageAlignmentMode.svelte` + `VirtualPassageStream.svelte` - the target
  passage as a windowed stream of collapsible verse rows, never one column
  per verse. Connector detail is drawn only for the focused relationship; an
  unfocused linked verse gets a count instead, which is what keeps the view
  from becoming a full-passage spaghetti graph. Relationships are matched to
  verses through their target token ids, since a relationship carries tokens
  rather than references - which is also what makes cross-verse realization
  visible, one relationship's tokens falling in more than one verse.

  When a run had no embedding provider, the mode says so and reports how many
  relationships were found, so sparse linking reads as the capability limit
  it is rather than as an indictment of the translation.
- `AlignmentQaMode.svelte` - queue on the left, evidence and decision on the
  right; filter chips for order, review state and issue type.
- `QaFindingList.svelte` - the queue, windowed above 60 rows so a
  thousand-finding queue puts a few dozen rows in the DOM rather than a
  thousand. Fixed row height so the window is computed from `scrollTop`
  without measuring.
- `QaFindingDetail.svelte` - the four reviewer actions, the note field, and
  the opt-in promotion checkbox.
- `EvidenceInspector.svelte` - FINDING / SOURCE / LOCATION / MEANING /
  COVERAGE / RESOURCES / HISTORY as separate sections.
- `ReviewStatusBadge.svelte` - status pills.
- `reviewStores.ts`, `reviewLabels.ts`, `__tests__/fixtures.ts`.

`AlignmentModal.svelte` is mounted unchanged as Word mode. Nothing converts
Bridge semantic relationships into translationCore alignment groups.

### Where the design decisions actually live

**Wording is centralized in `reviewLabels.ts`** so no component can invent
its own. Everything Stage 8 produces reads as "Possible omission", never
"Error"; the confirmed forms exist only because a reviewer can promote a
finding explicitly. `AI_PROPOSED` renders as **"Machine-proposed"**, not "AI
proposed" - Stages 6B-8 are deterministic and no language model is involved,
so calling it AI would be simply untrue.

**Severity is labelled "… priority"** with a tooltip saying it sets review
order and does not mean the issue is confirmed. Nothing about severity is
styled to look like a verdict.

**Location and meaning are separate sections by construction**, and the
inspector ends with a plain-language "What this means" that names which of
the two the reviewer is looking at: mapping uncertain, mapping probably
right so this is about the translation, or nothing located at all. That is
the mapping-error / translation-error distinction made explicit rather than
left for the reviewer to infer.

**Coverage statuses carry their own justification.** GRAMMATICALLY_REQUIRED
and EXPLICITATION_SUPPORTED render with a sentence explaining why an
apparent extra word may be perfectly correct, so the UI does not nudge
toward treating every addition as a fault. Cross-verse, split and reordered
realizations get the same treatment.

**Alternatives are always shown when the engine retained them**, and say so
explicitly when it retained none, so the UI never implies a single candidate
where several competed.

**STALE is never hidden.** A stale finding carries a badge in the list, a
badge in the header, and a notice at the top of the inspector saying it was
produced against an earlier revision and must be re-evaluated - while its
human decision stays visible in history.

**There is no "Apply correction" anywhere**, and a test asserts its absence
rather than trusting that nobody adds one.

### Concurrency in the UI

`decideFinding` sends the `revision` and `targetContentHashes` the reviewer
actually saw. A `revision_conflict` is never retried: the finding is
reloaded and the reviewer is told it changed since they opened it, so the
decision is made against what it says now.

### Accessibility

Status never depends on colour alone - every badge pairs a Unicode glyph
with its own text, selection is marked by a left rule as well as a fill, and
filter chips use border weight plus `aria-pressed`. Unicode glyphs rather
than an icon font, per the existing gotcha about offline builds not reaching
a CDN. The queue is one tab stop with arrow/Home/End navigation and
`aria-activedescendant`, so reaching row 900 does not mean 900 tab presses.
Meaning components are a real table with row and column headers.

### Small-screen behaviour

The decision controls live in a sticky footer that is a *sibling* of the
scrolling evidence area, not inside it, so long evidence cannot push them
below the fold - the specific failure Bridge's alignment UI had before. The
filter block is capped and scrolls rather than pushing the queue off-screen;
the panes stack below ~900px; Scripture wraps with `overflow-wrap: anywhere`
and a line height that suits Tamil and Hebrew.

### What these tests cannot check

jsdom does not lay out or paint. The viewport tests assert *structure* -
scroll containers exist, the action bar is outside the scrolling region,
long text is not truncated, a 400-row queue windows correctly - not measured
pixels. **Real 1366x768 behaviour still needs a pass in the running desktop
app**, which needs the Tauri build and both sidecars. The tests keep the
structure from regressing between those passes; they do not replace one.

### Entry point

Alignment Review is a new top-level surface (`screen === "review"`) reached
from the editor toolbar, alongside ReviewPanel's existing per-verse alignment
modal rather than replacing it. `resetReviewState()` is called wherever
`resetBookState()` is, so a finding id from one book can never appear under
another - the same class of bug as gotcha 7.

**Tests:** 97 frontend tests across 9 files - evidence layering, possible-vs-
confirmed wording, the four dispositions, promotion gating, note-without-
deciding, revision conflict, queue paging and dedup, virtualization,
keyboard navigation, accessibility, small-viewport structure, realization and
property wording, passage windowing, focused-relationship marking, and
passage search.

**Files:** `vitest.config.ts`, `package.json`, `src/App.svelte`,
`src/lib/components/TopBar.svelte`, `src/lib/reviewStores.ts`,
`src/lib/utils/reviewLabels.ts`, `src/lib/types/qaReview.ts`, and the six new
components plus `src/lib/components/__tests__/`.

## Stage 9A.1 — Stable finding identity and the human review APIs (2026-09-02)

Backend half of Stage 9A. A reviewer can now be given a deterministic queue,
layered evidence, and four decisions to make, with none of it touching
Scripture. No UI yet.

### Finding ids are re-keyed off stable semantic identity

Stage 8 hashed the *run fingerprint* into every finding id
(`_build_finding`), so any upstream change — a target edit, a policy bump —
minted brand-new ids and orphaned every human decision recorded against the
old ones. That made the spec's "a stale human-confirmed issue is preserved
and re-evaluated" unimplementable: after an edit there was no identity left
to preserve it against. This is the same lesson `_stable_finding_id()` in
`bridge_service.py` already encodes for the Greek Room findings (CLAUDE.md
gotcha 3); Stage 8 had not applied it.

`QaAuditEngine._stable_finding_id()` now keys on
kind + direction + coverage dimension + source unit ids + target anchors, and
deliberately excludes the run fingerprint **and** the engine/policy versions —
keying on policy would orphan decisions on every policy bump. Both are still
recorded as fields.

Verified before relying on it, rather than assumed:

- **Source unit ids are stable.** `source-unit-<fingerprint>` hashes
  kind + token ids + rule + policy, and the source resource is locked, so
  they survive target edits by construction.
- **Target unit ids are not.** `target-unit-<fingerprint>` includes
  `targetRevision`, and `PassageSemanticRuntime.text_revision()` is
  per-verse — so editing one verse re-ids every target unit in it. Target
  support findings therefore anchor on
  reference + normalized surface + occurrence instead
  (`_finding_anchors`), which holds an addition finding's identity steady
  when an unrelated word in the same verse changes, and breaks it only when
  the word the finding is actually about changes — at which point it
  genuinely is a different finding.
- **Relationship ids embed the run fingerprint** and are correctly excluded.
- **Coverage account ids** derive from the source inventory fingerprint, so
  they are stable across target edits; the dimension is used rather than the
  id itself.

No golden fixture had to change: there is no Stage 8 golden file, and
`test_qa_audit_stage8.py` never asserts a literal finding id — it derives
`finding["id"]` at runtime everywhere.

### save_qa_finding became an upsert that cannot overwrite a decision

Stable ids mean a re-run now reaches an existing row instead of inserting a
fresh one. `save_qa_finding` merges the machine fields and preserves
`qaDisposition` / `reviewStatus` / `revision` exactly as the reviewer left
them (`_HUMAN_FINDING_FIELDS`). If nothing the machine produced actually
changed it writes nothing at all, so re-runs do not churn revisions. When it
does refresh a finding that already carries a human decision it appends a
SYSTEM ReviewRecord, leaving an audit trail rather than a silent overwrite.

Measured end to end: confirm a finding → edit the target verse → the finding
goes STALE with the decision intact → re-run → same id, back to ACTIVE,
decision still intact, history `[HUMAN, SYSTEM]`. A wording change never
becomes CORRECTED on its own; only a future Stage 9B recheck may conclude
that.

### Review APIs

New `engine/tc_ai_bridge/qa_review.py` (`QaReviewService`), deliberately
separate from the `qaAudit.*` analysis methods so that analysis stays
read-only and every human write leaves a ReviewRecord:

`qaReview.getQueue` · `qaReview.getFinding` · `qaReview.decideFinding` ·
`qaReview.addNote` · `semanticReview.decideLocation` ·
`semanticReview.decideMeaning` · `reviewHistory.getEntityHistory`

- **Disposition → review status is now explicit.** The old derivation folded
  everything decided into HUMAN_APPROVED. FALSE_POSITIVE is the one case
  where the human rejects the machine's claim outright, so it is the only
  disposition yielding HUMAN_REJECTED; ACCEPTABLE_TRANSLATION still approves
  the *observation* and carries its nuance in the disposition.
- **Promotion is opt-in only.** POSSIBLY_MISSING → MISSING and
  POSSIBLY_UNSUPPORTED → UNSUPPORTED happen only via `promote: true`
  alongside CONFIRMED_TRANSLATION_ERROR, and are expressed on the coverage
  account rather than by adding bare OMISSION/ADDITION finding kinds.
  Opening or deciding a finding never promotes.
- **Mapping and meaning are reviewable independently.** Rejecting a Stage 6B
  location marks it HUMAN_REJECTED (or HUMAN_MODIFIED when an alternative
  candidate is chosen) and invalidates dependent Stage 7/8 records without
  rewriting their history; the QA disposition is untouched, because a mapping
  verdict decides nothing about whether the translation is wrong. Overriding
  a Stage 7 meaning assessment works the same way.
- **Notes do not decide.** `append_standalone_note` records a note against
  the entity without touching its review state or bumping its revision.

`record_human_review()` is the single generic write path (CAS + ReviewRecord
+ optional dependent invalidation) for every reviewable entity;
`_REVIEWABLE_TABLES` names them.

### Concurrency

`decideFinding` takes both `expectedEntityRevision` and
`expectedTargetContentHashes`. `FoundationConflict` and
`FoundationValidationError` now have explicit handlers at the protocol
boundary and surface as `revision_conflict` / `semantic_validation_error`
instead of falling through to `internal_error`.

### Found by running it: Stage 7 evidence is not in evidence_records

`qaReview.getFinding` crashed on `Unknown evidence record:
meaning-evidence-…`. Stage 7 synthesizes per-component `meaning-evidence-*`
ids *inside* the assessment payload and never writes them to the
`evidence_records` table, but Stage 8 copies those ids onto the finding's
supporting/conflicting evidence lists. The resolver now reads both stores and
tags each item `EVIDENCE_RECORD` / `MEANING_ASSESSMENT` / `UNRESOLVED` — a
reviewer should see that a piece of evidence is missing, not be blocked from
reviewing the finding at all.

### Command layer

Stage 8 shipped no Tauri commands, so its methods were unreachable from the
UI. Added all seven `qa_audit_*` commands plus the seven review commands to
`commands.rs`, registered in `main.rs`, with matching `bridgeClient.ts`
methods and a new `src/lib/types/qaReview.ts`. Stage 9A.2 is therefore pure
Svelte. (`passage_semantic_wire.rs` holds schema-validation serde types, not
the command layer — no additions needed there.)

**Files:** `engine/tc_ai_bridge/qa_review.py` (new),
`engine/tc_ai_bridge/qa_audit.py`,
`engine/tc_ai_bridge/passage_semantic_repository.py`,
`engine/tc_ai_bridge/passage_semantic_models.py`,
`engine/tc_ai_bridge/passage_semantic_runtime.py`,
`engine/bridge_service.py`, `src-tauri/src/commands.rs`,
`src-tauri/src/main.rs`, `src/lib/api/bridgeClient.ts`,
`src/lib/types/qaReview.ts` (new),
`engine/tests/review/test_qa_review_service_stage9a.py` (new, 37 tests).

## Stage 9A.0 — Review-queue storage preflight (2026-09-02)

Preflight for Stage 9A (human QA review). No review API and no UI yet; this
step only makes storage able to answer review-queue questions and measures
where Stage 8 actually spends its time.

**Regression baseline before any change** — 478 Python tests passed (7m49s),
`svelte-check` 0/0, `npm run build` OK (801.59 kB bundle, already past Vite's
500 kB warn threshold), `cargo check` clean.

**Schema v8** (`_MIGRATION_V8`, `passage_semantic_repository.py`). Stage 8
stored everything about a finding inside `qa_findings.payload_json`; the table
itself had no `book`, `kind`, `severity` or reference columns. A review queue
that orders by canonical position or severity and filters by type/state could
only have been built by scanning and re-parsing every payload. v8 lifts those
values into real columns (`book`, `kind`, `direction`, `severity`,
`severity_rank`, `sort_chapter`, `sort_verse`, `displayed_reference`) with
three supporting indexes, backfilling existing rows from the payload via
`json_extract`. `severity_rank` exists because `QaFindingSeverity` does not
sort lexicographically — CRITICAL/HIGH/MEDIUM/LOW/INFO is review priority, not
alphabetical order. Verified against a real v7 database built from the shipped
v1-v7 migration scripts: it upgrades, backfills, and leaves the automatic
`backups/pre-schema-v8-*` snapshot.

`sort_chapter`/`sort_verse`/`displayed_reference` currently backfill empty:
`QaFinding` carries no reference of its own (`_finding_to_dataclass` sets
`passage_id` to the *book*). `_queue_sort_key()` already reads an optional
`displayed_references` attribute, so those columns populate themselves once
Stage 9A.1 adds that field — no repository change needed then.

**`query_qa_findings()`** — keyset (not OFFSET) pagination, so a human
decision made mid-review cannot shift rows across a page boundary. Both
orderings end in the finding id, making order fully deterministic for
equal-priority findings.

**Reviewer notes.** `_append_review()` hardcoded `note=""`, so no write path
could record one — only `import_review_record` accepted a note. The parameter
is now threaded through `update_qa_disposition`. The existing revision-CAS and
`FoundationConflict` behaviour is unchanged.

**Stage 8 profiling** (the gap the Stage 8 report flagged). `PhaseProfiler`
does exclusive accounting — time in a nested phase is subtracted from its
parent, so phases sum to the total instead of double-counting. Persistence is
attributed through `_ProfiledRepository`, a transparent forwarding proxy that
times only `save_*`/`update_*`/`create_*`, so no save call site changed. The
proxy is restored in a `finally`. Results, measured on the PHP Tamil fixture:

| Range | Findings | Elapsed | persistence | sourceCoverageAudit | targetSupportAudit |
|---|---|---|---|---|---|
| PHP 1:3 | 6 | 0.41s | 0.376s (92%) | 0.031s | 0.002s |
| PHP 1:3-1:6 | 29 | 0.96s | 0.782s (81%) | 0.177s | 0.002s |

**Stage 8 is persistence-bound, not analysis-bound** — 81-92% of its runtime
is SQLite writes, because every `save_qa_finding`/`save_coverage_account` call
opens its own connection and commits individually. The audit logic itself is
cheap. Extrapolated to a whole book this is minutes of mostly-commit time.
Batching a run's writes into one transaction is the obvious fix, but it
changes Stage 8 persistence semantics and is deliberately **not** done here.
Not a blocker for review UI work, which reads rather than writes.

Cache hits short-circuit before any audit pass; a cached run reports only
`cachedRetrieval` (~1.3ms).

**Files:** `engine/tc_ai_bridge/passage_semantic_repository.py`,
`engine/tc_ai_bridge/qa_audit.py`,
`engine/tests/persistence/test_qa_review_stage9a.py` (new, 17 tests).

### Found while surveying: Stage 8 has no Rust wire and no client methods

Stages 4, 5, 6A, 6B and 7 each shipped their own `src-tauri/src/commands.rs` +
`passage_semantic_wire.rs` + `bridgeClient.ts` additions. Stage 8 shipped
none: its commit touched only `src/lib/types/passageSemanticV1.ts` on the
frontend side. `bridgeClient.ts` has `semanticLocation*` and
`meaningAnalysis*` but no `qaAudit*`, and `passage_semantic_wire.rs` contains
no qa_audit entries. Because each protocol method needs a named
`#[tauri::command]`, Stage 8's methods are currently reachable only over the
raw sidecar protocol, not from the UI. Stage 9A must add that wiring for the
existing `qaAudit.*` methods as well as for its own review APIs.

## Retrospective: Stages 4-7 (added 2026-09-02, reconstructed)

> **Marked retrospective.** These stages shipped without BUILD_LOG entries.
> Reconstructed only from commit contents, the code, and the tests that exist
> today — deliberately limited to what those verify. The investigation
> narrative that the other entries in this file carry was not recorded at the
> time and is not reproduced here rather than guessed at.

Numbering note: these are passage-semantic **Stages**, a different axis from
the Greek Room **Phases** 4-7 documented further down this file (USFM
checker, versification, names, alignment statistics). Both numbering schemes
are in use; they do not correspond.

**Stage 4 — passage runtime and stale invalidation** (`78fdf4b`, 2026-09-01).
Added `passage_semantic_runtime.py` (1,171 lines) as the composition point for
the passage-semantic pipeline, plus a large repository expansion (+734) for
target-revision tracking and dependency-driven staleness — `record_dependencies`
with `_stale_generic_dependencies` walking the dependency graph, and the
`prepare_`/`apply_`/`cancel_target_invalidation` intent flow. First stage to
add Rust wire (`passage_semantic_wire.rs`, +161) and client methods.
Tests: `test_passage_semantic_runtime.py` (547 lines).

**Stage 5 — source semantic inventory** (`82d6664`, 2026-09-01).
`source_semantic_inventory.py` (736 lines): the source-side obligation
inventory, carrying `SemanticObligationStrength`, `CoverageAccountingRole`,
`CoverageDimension` and `AuditEligibility` — the fields Stage 8's coverage
audit later gates on. Golden fixture `stage5-source-golden-v1.json` was added
in the following commit. Tests: `test_source_semantic_inventory_stage5.py`.

**Stage 6A — target semantic inventory** (`de8ce4c`, 2026-09-02).
`target_semantic_inventory.py` (398 lines), built as language-independent —
the target side is inventoried without source knowledge, which is what makes
the later target-support audit a genuinely independent direction rather than a
re-reading of source coverage. Tests:
`test_target_semantic_inventory_stage6a.py`.

**Stage 6B — passage-aware semantic location** (`91e5394`, 2026-09-02).
`semantic_location.py` (809 lines) plus `semantic_location_benchmark.py`.
Locates where source meaning was realized in the target, across verse
boundaries, producing `LocationOutcome` + `Realization` + `RelationshipProperty`
without judging meaning — `test_semantic_location_stage6b.py` asserts
`all("meaningStatus" not in item for item in result["relationships"])`,
enforcing the location/meaning separation at the data level. Golden fixture
`stage6b-location-golden-v1.json` pins the reordered IRV Tamil PHP 1:3-6
mapping (Greek 1:3→Tamil 1:6, 1:4→1:4, 1:5→1:3, 1:6→1:5) and the test asserts
it is discovered without book-specific engine rules. This commit also touched
`versification.py` (+83).

`SemanticEmbeddingProvider` is defined here with `available = False`, and
`PassageSemanticRuntime` constructs `SemanticLocationEngine(self)` with no
provider — so **the shipped app runs without embeddings**; the reordering
fixture passes only because tests inject a `FixtureEmbeddingProvider` with
hand-built paired vectors. Embeddings are candidate retrieval only
(`EmbeddingRole.CANDIDATE_RETRIEVAL_ONLY`), never a meaning judge.

**Stage 7 — meaning preservation** (`0289ae5`, 2026-09-02).
`meaning_analysis.py` (512 lines) plus `meaning_benchmark.py`. Assesses
whether located realizations preserve meaning, producing `MeaningStatus` and
per-dimension `MeaningComponentStatus` so that a component judgement
(e.g. quantity contradicted) stays visible instead of collapsing into one
score. `MeaningAssessmentReason` records why an assessment was *not* made
(`NO_LOCATED_REALIZATION`, `AMBIGUOUS_LOCATION`, `SEARCH_INCOMPLETE`), keeping
"not assessed" distinct from "assessed as fine". Tests:
`test_meaning_analysis_stage7.py` (365 lines).

Stages 5-8 are all deterministic: none of `source_semantic_inventory.py`,
`target_semantic_inventory.py`, `semantic_location.py`, `meaning_analysis.py`
or `qa_audit.py` imports `ai_client` or `model_router`. The `AI_PROPOSED`
review status Stage 8 stamps on findings marks them as machine-proposed rather
than human-decided; it does not indicate a language model was involved.

## Stage 8 — Bidirectional Source Coverage, Target Support, and Translation QA (2026-09-02)

First stage in the passage-semantic pipeline allowed to produce
translation-problem findings. Synthesizes evidence already produced by
Stage 5 (source obligations), Stage 6A (target inventory), Stage 6B
(location), and Stage 7 (meaning preservation) — it never re-runs location
search or re-judges meaning, only reads their frozen, already-persisted
run payloads.

**New module** `engine/tc_ai_bridge/qa_audit.py`: `QaAuditPolicy` (a single
versioned deterministic gate/precedence/severity policy, `qa-policy-v1`)
plus `QaAuditEngine`, mirroring `meaning_analysis.py`'s shape exactly
(fingerprint over upstream fingerprints + engine/policy versions, cache
check via `qa_audit_for_fingerprint`, `run_range`/`status`/`get_range`/
`get_source_coverage`/`get_target_support`/`get_finding`/`get_diagnostics`).

**A real discovery that shrank this stage's scope**: the persistence
foundation for QA was already built in Stage 3 and simply never called —
`FoundationRepository.save_qa_finding(QaFinding)`,
`save_coverage_account(SemanticCoverageAccount)`, and
`update_qa_disposition(...)` (the human-confirmation-boundary transition,
item 22 of the spec) were all fully functional, unused code. Stage 5
already seeds one `SemanticCoverageAccount` per `(auditOwnerUnitId,
coverageDimension)` for every `PRIMARY`-role source unit, with
`findingId=None` placeholders — Stage 8 finalizes those same rows in place
(new `update_coverage_account_status`, optimistic-concurrency, same
pattern as `update_qa_disposition`) rather than inserting duplicates.
Target-side accounting needed no new Stage 6A structure either —
`TargetSemanticUnit` already carries `auditEligibility`/`accountingRole`/
`auditOwnerUnitId`/`coverageDimension` (`target_semantic_inventory.py:183`)
so `PRIMARY`+`ELIGIBLE` units are exactly the reverse-audit candidates.

**Schema**: migration v6→v7 adds one table, `qa_audit_runs` (mirrors
`meaning_analysis_runs` — id/fingerprint/`meaning_run_id` FK/payload_json/
`UNIQUE(project_id,book,range_key,fingerprint)`). `qa_findings` and
`coverage_accounts` needed no column changes (both existed unused since
schema v1); run traceability goes through `record_dependencies` the same
way `MEANING_RUN`/`LOCATION_RUN` already do (`QA_RUN`→`MEANING_RUN`,
`COVERAGE_ACCOUNT`/`QA_FINDING`→`QA_RUN`), so the existing
`_stale_generic_dependencies` BFS cascades edits all the way from a
`SOURCE_RESOURCE`/target-text change down through `QA_FINDING.lifecycleStatus
= STALE` with one new map entry (`"QA_RUN": "qa_audit_runs"`).

**Model changes** (`passage_semantic_models.py`): `QaFindingKind` gained 12
new values (`POSSIBLE_OMISSION`, `POSSIBLE_ADDITION`,
`POSSIBLE_UNDERTRANSLATION`, `POSSIBLE_OVERTRANSLATION`, `MEANING_SHIFT`,
`CONTRADICTION`, `NEGATION_PROBLEM`, `QUANTITY_PROBLEM`, `TEMPORAL_PROBLEM`,
`PARTICIPANT_PROBLEM`, `REFERENT_PROBLEM`, `SOURCE_VARIANT_REVIEW`) —
the original 7 (`POSSIBLY_MISSING`/`MISSING`/etc.) are untouched. New
`QaFindingSeverity` and `QaRunStatus` enums; `EvidenceKind` gained
`SOURCE_VARIANT`. `QaFinding` gained 13 fields the Stage-3 foundation
hadn't anticipated (severity, meaning-assessment/coverage-account id links,
location/meaning snapshots, supporting/conflicting/resource evidence id
lists, target/source hashes, engine/policy versions, fingerprint) — all
required, since every real construction site is new Stage 8 code.
`SemanticCoverageAccount` gained one field, `coverage_status: str =
"NOT_CHECKED"` (a `SourceCoverage` value for `SOURCE_COVERAGE`-direction
accounts, `TargetSupport` for `TARGET_SUPPORT`-direction, validated in
`__post_init__`); the default keeps Stage 5's existing construction site
unmodified. This tripped the existing canonical-schema parity tests
(`test_python_record_fields_match_canonical_schema`,
`test_python_and_typescript_controlled_enums_match_canonical_schema`,
which assert every dataclass field and enum value is mirrored 1:1 in
`schemas/bridge-passage-semantic-v1.schema.json` and
`src/lib/types/passageSemanticV1.ts`) — both were updated to match, plus
one pre-existing foundation test
(`test_passage_evidence_qa_exportability_and_review_round_trip`) that
constructed a bare `QaFinding` and needed the new fields filled in.

**Source coverage gates** (items 5–9 of the spec): `NOT_LOCATED` only
becomes `POSSIBLY_MISSING` when every relationship touching that obligation
resolved to `NOT_LOCATED` (never `AMBIGUOUS`/`SEARCH_INCOMPLETE`/
`UNSUPPORTED_ANALYSIS`, which gate to `UNCERTAIN` instead) and no
documented source-variant evidence explains the absence (checked via the
owner unit's real `evidence_ids` against `EvidenceRecord.kind ==
SOURCE_VARIANT`, falling back to `SOURCE_VARIANT_REVIEW` instead of
`POSSIBLE_OMISSION` when one exists). A `LOCATED` relationship whose Stage 7
assessment is `PRESERVED`/`PRESERVED_WITH_RESTRUCTURING` becomes `COVERED`
or `COVERED_BY_RESTRUCTURING` depending on whether `RelationshipProperty`
is non-empty or realization isn't `LEXICALLY_REALIZED`. `POSSIBLY_MISSING`
is never auto-promoted to `MISSING` — that stays a human-only transition
via `update_qa_disposition`.

**Target support gates** (items 12–17): a target unit with zero
referencing relationship becomes `GRAMMATICALLY_REQUIRED` for a small
controlled function-word list, `EXPLICITATION_SUPPORTED` for licensed
explicitation targets (reusing `DeterministicMeaningComparator
.LICENSED_EXPLICITATIONS`'s target side from Stage 7), `POSSIBLY_UNSUPPORTED`
only for an unmatched specificity marker (reusing `SPECIFICITY_MARKERS`),
else the conservative default `UNCERTAIN` — deliberately not
`POSSIBLY_UNSUPPORTED`, since a v1 deterministic policy can't yet
positively rule out every legitimate grammatical/explicitation reason for
an unmatched word, and false positives are worse than an `UNCERTAIN`.

**Meaning-failure pass** (items 10–11, 24, 27): for every `LOCATED`
relationship whose Stage 7 meaning isn't preserved/unverifiable, a single
component-aware precedence chain picks the finding kind — a `CONTRADICTED`/
`ALTERED` component on `POLARITY`/`QUANTITY`/`TEMPORAL_ASPECTUAL`/
`PARTICIPANT`/`REFERENT` wins over the generic `MEANING_SHIFT`/
`CONTRADICTION`/`POSSIBLE_UNDERTRANSLATION`/`POSSIBLE_OVERTRANSLATION`
fallback from the aggregate status, and a `CONFLICTING` resource-evidence
status on any component overrides everything to `RESOURCE_CONFLICT`. One
finding per relationship, not per component (verified by
`test_deduplication_one_finding_per_relationship`).

**Benchmarks** (`engine/resources/qa_audit/{omission,addition}-benchmark-v1.json`,
15 cases each, `reviewStatus: "MACHINE_PROPOSED"` guarded the same way
`meaning_benchmark.py` guards its own): new `qa_benchmark.py` drives
`QaAuditPolicy.source_coverage_for`/`target_support_for` directly from
synthetic gate inputs (mirroring how `meaning_benchmark.py` drives
`DeterministicMeaningComparator.compare`), plus `false_positive_metrics()`
reporting possible-omission/addition precision/recall, false-omission/
addition rate, legitimate-restructuring false-positive count, and
ambiguity/search-incomplete-to-error leakage separately — not one generic
accuracy number, per the spec's explicit false-positive emphasis. Current
deterministic baseline: 100% accuracy, zero leakage on both TEST splits
(15/15 cases here are the deterministic-policy self-check, not a
human-reviewed calibration claim).

**Philippians 1:3–6**: ran Stage 8 over the existing `REORDERED` Stage 6B
relationships (Greek 1:3→Tamil 1:6, 1:4→1:4, 1:5→1:3, 1:6→1:5). Confirmed
none of the well-covered content lemmas (the 19 `PHP_PAIRS` used since
Stage 6B/7) are falsely flagged `POSSIBLE_OMISSION` despite the cross-verse
reordering, and that at least one of them reaches `COVERED`/
`COVERED_BY_RESTRUCTURING`. Some genuinely-uncovered function
words/particles in the real UGNT text (not in the 19-pair fixture) do
legitimately gate to `POSSIBLY_MISSING` — that's correct behavior given
the fixture's vocabulary coverage, not a false positive, and the test
(`test_php_reordered_passage_produces_no_false_omissions`) asserts against
the covered-lemma set specifically rather than a blanket zero-omissions
claim. Did not implement a QA-specific verdict for the ἐπιτελέσει/நடத்தி
வருவார் completion-vs-continuation case beyond what Stage 7 already scores
(`TARGET_WEAKENS_SPECIFICITY` → `POSSIBLE_UNDERTRANSLATION` via the generic
precedence path) — no dedicated completion/continuation finding kind was
requested by the spec.

**Tests**: `engine/tests/semantic/test_qa_audit_stage8.py`, 27 cases — precedence/
severity policy (parametrized over all 5 dimension→kind mappings plus
generic status precedence and resource-conflict override), all→some
quantity problem, genuine absence, ambiguous/search-incomplete blocking
omission, the PHP reordered passage, grammatical function words and
unsupported specificity on the target side, human-confirmation +
edit-staleness preservation, QA-run cache hit, full `qaAudit.*` protocol
round-trip via `BridgeEngine`, Hebrew (Gen 2:5) and Aramaic (Dan 2:4)
reuse, and finding deduplication.

**Not implemented** (explicitly out of scope, per spec): automatic
Scripture correction, `CorrectionProposal` construction (the dataclass/
table are untouched — no finding requires one), final QA UI, Scripture
Burrito export, new translationCore projection behavior. `MISSING`/
`UNSUPPORTED` and their `OMISSION`/`ADDITION` finding equivalents remain
human-only transitions.

**Unresolved risks**: the target-support gate's function-word/explicitation/
specificity-marker lists are small, deliberately controlled fixtures
(mirroring Stage 7's own comparator lists) — real-world coverage across
languages is unvalidated beyond the English/Tamil/Hebrew/Aramaic cases
tested here. `QaFinding.severity` thresholds (0.85/0.9 confidence cutoffs
for `HIGH`→`CRITICAL`) are uncalibrated, same caveat as every confidence
value elsewhere in this pipeline. No dedicated target-support account for
`AGGREGATE`/`EVIDENCE_ONLY`-role target units (`NOT_CHECKED`, consistent
with the source side) — if a future stage needs aggregate-level target QA,
that's unbuilt.

**Verified**: focused suite 27 passed; full Python suite 478 passed (up
from 428 at the end of Stage 6B); `cargo check` clean; `npm run check` 0
errors/0 warnings; `npm run build` succeeds; `git diff --check` clean.
Stage 6B golden locations and Stage 7 golden meaning statuses unchanged
(no test in `test_semantic_location_stage6b.py`/`test_meaning_analysis_stage7.py`
was modified, and both suites still pass unmodified). Existing
translationCore behavior unchanged (no `tc_project.py`/alignment/import
code touched).

**Note for Benz**: verifying this repo against the Stage 5–7 handoff doc
turned up a pre-existing gap this session didn't create or fix — Stages 4
through 7's commits (`78fdf4b`, `82d6664`, `de8ce4c`, `91e5394`, `0289ae5`)
have no corresponding `BUILD_LOG.md` narrative (no reported test counts,
no session notes), unlike every other feature landed in this file.
`DEVELOPER_GUIDE.md`'s phase-roadmap table also has no row for any of
these stages. Worth backfilling at some point so the record stays
continuous, independent of Stage 8's own entry above.

## Flag for Benz: duplicate-detection fingerprint regression from Stage 4 passage-runtime merge (2026-09-02)

Found while pulling `78fdf4b` (feat(semantic): integrate Stage 4 passage
runtime and stale invalidation) and re-verifying the frozen sidecar with
`scripts/smoke_sidecars.py` — the smoke test's own duplicate-classification
assertion started failing. Reproduced in source mode too (not a
PyInstaller/freeze artifact), so this is a real regression in the merged
code, not a packaging issue.

**Root cause:** `project.open` now constructs a `PassageSemanticRuntime`
after `ProjectRegistry.register()` has already run. That runtime writes new
state — `.apps/translationCoreAI/passageSemantic/bridge-semantic.sqlite3`
plus per-migration backup snapshots under `.../passageSemantic/backups/` —
directly into the project directory. For any project whose identity falls
back to a whole-tree hash (`_source_fingerprint()` in
`engine/tc_ai_bridge/project_registry.py:201`, used whenever a project has
no `.bridge/import.json` provenance — e.g. a hand-placed or externally
created translationCore project, not one imported through Bridge's own
`project.import` flow), `register()` snapshots the tree *before* those
passageSemantic files exist. Any later `project.inspectImport` call
recomputes the tree hash via `source_fingerprints()`/`_tree_fingerprint()`
(`project_registry.py:74`) against the *current* tree, which now includes
the new sqlite/backup files — so the project no longer hashes the same as
its own stored fingerprint. Effect: re-inspecting a project you already
opened classifies it as `possibleDuplicate` of itself instead of
`exactDuplicate`.

`_tree_fingerprint()` already excludes `.bridge/project.json` and
`.bridge/collection.json` for exactly this kind of reason (Bridge-local
identity shouldn't affect the project's own source fingerprint) — the new
`.apps/translationCoreAI/passageSemantic/` tree needs the same treatment,
or `register()` needs to snapshot the fingerprint after passage-runtime
attach instead of before. Projects imported through Bridge's normal import
flow are unaffected (their fingerprint comes from `.bridge/import.json`'s
stored SHA-256, not a tree hash), so this is scoped to
externally-created/hand-placed projects — real but not the common path.

Not fixed yet — surfacing this for Benz since it's their in-flight feature;
did not want to patch someone else's just-landed identity/lineage logic
without a decision on whether the exclusion belongs in the fingerprint
function or in ordering `register()` after runtime attach.

Repro: open any tC-shaped project lacking `.bridge/import.json` (e.g. a
hand-built fixture, as `scripts/smoke_sidecars.py`'s fixture project does),
then call `project.inspectImport` on the same path again — `classification`
comes back `possibleDuplicate`/`bookLanguageBible` instead of
`exactDuplicate`/`sourceFingerprint`.

## Automatic alignment during AI review; alignment popup goes read/decide-only (2026-08-31)

Issues #20/#21, agreed with Benz: the "Automatic AI review" card's This
verse/Chapter/Whole book buttons only ran tN/tW evidence review; alignment
stayed a fully separate manual popup action. Both are now one automatic
pass, and the popup's manual controls shrink to match.

- **#20** — `prepare_verse_review` (`tc_ai_bridge/ai_client.py`) already
  computed a gap_fill alignment proposal internally whenever a verse's
  alignment was incomplete, purely to ground the tN/tW review against —
  the proposal was then discarded (only surfaced as an unused
  `alignmentProposal` field, never rendered anywhere in the UI). No second
  AI call was needed: `_run_ai_review_for_project` (`bridge_service.py`)
  now saves that same in-memory proposal through the normal
  identity-checked `_save_alignment` pipeline whenever it actually differs
  from the verse's current alignment, deliberately bypassing Bridge's usual
  human-confirm-before-apply gate for this one path (explicit call by
  Revant + Benz, not an oversight). A failed auto-align (concurrent edit,
  validation edge case) is swallowed rather than failing the verse's tN/tW
  result — the verse just stays flagged unaligned.
- **#21** — `AlignmentModal.svelte` no longer has "Ask AI to propose
  alignment" / "Apply proposal" (alignment is filled automatically per
  #20); manual align/unalign token-click controls are unchanged. A new
  `_finish_alignment_mutation` helper in `bridge_service.py` is now the
  shared tail for every alignment-mutating path (`_save_alignment`,
  `undo_alignment`/`restore`) — it auto-marks the verse `completionState:
  "completed"` the instant every word is grouped, no button click. The
  manual "Mark alignment complete" button is removed; the existing "✓
  Human-completed" label is kept as-is (explicit decision: don't rename to
  "approved"). A new banner flags an incomplete verse in the popup itself;
  the verse-list's existing per-verse indicator (`VerseList.svelte`, ●
  complete/◐ partial/○ untouched/! invalid) needed no change — it was
  already driven by the same data-derived `status`, not the button-gated
  `completionState`.
- Deliberately left alone: `alignment.complete`/`alignment.aiPropose`/
  `alignment.aiApplyProposal` (backend + `bridgeClient.ts` wrappers) still
  exist, just unreachable from any current UI — removing a still-tested,
  independently-useful capability wasn't part of either issue's ask.

Verified: full engine suite 321 passed (2 new tests added in
`test_ai_review_auto_align.py`); `npm run check` 0 errors/0 warnings;
`npm run build` succeeds.

## Stage 3 human validation workflow (2026-08-29)

- Added an Advanced-mode validation screen for the 40 ranked IRVTam
  `MACHINE_PROPOSED` candidates. It supports status/relationship/text filters,
  source and exact-target evidence, passage navigation, reviewer notes, and
  confirm/reject/correct/needs-discussion decisions.
- Confirmation is allowed only while every proposed target span still exactly
  matches the open imported project. Human corrections support multiple target
  spans and optional explicit offsets for repeated target text; all corrected
  relationships, meaning states, confidence values, and cross-verse
  classifications are deterministically validated.
- Decisions are stored in an append-only per-project companion audit with
  reviewer, timestamp, manifest SHA-256, candidate mapping fingerprint, note,
  provenance, and the exact accepted/corrected mapping. The latest decision is
  indexed for restart recovery; no validation path writes USFM, checkData, or
  alignment data.
- Added live calibration summaries for reviewed proposals overall and by model
  confidence band/relationship. Unconfirmed and needs-discussion rows are not
  counted as model failures. Threshold or classification changes remain gated
  on the first 15–20 human decisions.
- Release packaging copies the single checked-in validation artifact into
  Tauri resources, avoiding a second tracked copy while keeping development and
  installed builds on the same SHA-pinned queue.

Verified on Windows/Python 3.12.4:

- Complete Python suite: **319 passed in 320.31s**.
- Focused semantic validation/Stage 3 suites: **28 passed in 16.29s**.
- Svelte/TypeScript: **0 errors, 0 warnings**.
- Production Vite build: passed (existing >500 kB chunk warning only).
- UI state tests: **4 passed**.
- Rust command/sidecar tests: **2 passed**; changed command file passes rustfmt.

## Stage 3 IRVTam discovery and adaptive-search hardening (2026-08-29)

- Replaced implicit radius behavior with explicit language-independent search
  budgets for model calls, adjacent structural layers, windows, segments, and
  target characters. Verse counts are not semantic boundaries.
- Search-budget exhaustion now surfaces as
  `needs_extended_passage_review`; it cannot become omission or
  Nothing-to-Select.
- Fixed expanded-result cache lookup so a persisted wide-passage result is
  reused before any repeat seed-window model call.
- Hardened Basic-mode application: Stage 3 mappings must be same-verse,
  meaning-preserved, at least 90% confident, non-uncertain, exactly grounded in
  imported USFM, compatible with the native selection, and free of matching
  contradictory QA evidence. Advanced-mode passage mappings remain advisory.
- Added a reproducible IRVTam discovery tool. After explicit data-transfer
  authorization, a full `gpt-5.6` pass generated 90 validator-accepted
  mappings and ranked a 40-row `MACHINE_PROPOSED` / `UNCONFIRMED` review queue
  across Luke (28) and Philippians (12). All 43 overt spans were independently
  verified against exact imported-USFM offsets.
- The PHP 1:3 -> 1:6 `τῷ Θεῷ μου` -> `என் தேவனை` regression is preserved as
  `CROSS_VERSE_REORDERED`, meaning preserved, at 99% model confidence. This is
  an unconfirmed validation candidate, not a Tamil-specific rule.
- One Luke 11:2-4 batch was rejected when a target quote was not an
  unambiguous exact USFM match. The generator now checkpoints every batch,
  retains rejection diagnostics, and reuses content-fingerprinted validated
  results without weakening production validation.
- Mapping remains companion-only and a regression test verifies the input USFM
  stays byte-identical.

Verified on Windows/Python 3.12.4:

- Complete Python suite: **315 passed in 314.32s**.
- Focused Stage 3/corpus suites: **24 passed in 16.19s**.
- Svelte/TypeScript: **0 errors, 0 warnings**.
- Production Vite build: passed (existing >500 kB chunk warning only).
- UI state tests: **4 passed**.
- Rust desktop tests: **2 passed**.

## Current release state

The current working release adds the complete manual word-alignment loop and
Milestone 3A's offline original-language source baseline. The
authoritative design and limitations are in `docs/ALIGNMENT.md`; the release
gate is `docs/QA_TEST_MATRIX.md`.

- Protocol: `alignment.get/status/realign/unalign/save/complete/undo/backups/restore`.
- UI: per-verse alignment modal with occurrence-aware source/target token
  selection, all four group cardinalities, word bank, issues, status, completion,
  undo and selected-history restore. RTL/LTR direction is applied independently.
- Persistence: optimistic conflict comparison, exact token-identity validation,
  transaction-journal rollback, per-verse durable history, restart persistence,
  and tC word-alignment completed/invalid/pending markers.
- Rechecking: each alignment mutation immediately reruns local and Greek Room
  verse checks and refreshes editor state.
- AI review navigation: verse progress/results follow only the exact verse,
  chapter jobs follow only their chapter, and whole-book jobs follow only their
  project. Active off-reference jobs remain visible as background work without
  presenting an old result as belonging to the newly selected reference.
- Translation Helps navigation: an explicit reserved loading surface prevents
  the panel from collapsing and expanding while a newly selected reference is
  prepared.
- Issue resolution: saved tN/tW resolutions carry exact target text, correction,
  evidence and reviewer notes; Paratext Project Note handoff is identity-gated,
  crash-safe and idempotent. Edited resolved verses enter a persisted automatic
  recheck lifecycle, while Advanced-mode AI proposals require explicit human
  acceptance before a safe pass can close an issue.
- AI selection consistency: an applicable pass may no longer become **Nothing
  to Select** merely because a provider omitted target IDs. A uniquely quoted
  phrase found exactly once in the current verse is recovered transparently;
  ambiguous or missing target text remains pending instead of being guessed.
- AI provider compatibility: structured Responses requests first use the
  configured reasoning effort. If and only if a provider/model returns an
  explicit HTTP 400 unsupported-`reasoning` error, Bridge retries once without
  that optional object and records `provider-default`; unrelated 400 errors
  remain failures.
- Export/import: nested many-to-many `zaln`/`w` milestones are parsed into tC
  groups and aligned export writes re-importable USFM 3 over the retained source
  template.
- Original-language resources: raw OT/NT imports are initialized from exact,
  checksum-verified unfoldingWord UHB v3.0.0/UGNT v0.34 packs. All 66 books,
  31,103 verses, and 443,131 canonical tokens are bundled with CC BY-SA 4.0
  licenses, attribution, exact upstream commits, source/artifact hashes, and a
  reproducible generator. Aligned USFM and native tC projects are never
  overwritten; legacy raw-import recovery only fills empty source arrays and
  stops on a resource-version mismatch.
- Automated source gate: the complete Python suite passes in the maintained Windows/
  Python 3.12.4 environment. Four focused frontend navigation-state tests,
  clean Svelte diagnostics, the production frontend build, and 2 Rust tests
  also pass. Beta 13 frozen-sidecar and NSIS results, including exact artifact
  hashes and the remaining installed GUI acceptance, are recorded in the QA
  matrix. The former load-sensitive versification wall-clock bound is now a
  deterministic concurrency-invariant test.
- Explicitly deferred: live original-source resource downloads and automatic
  continuous Paratext/Logos synchronization. Explicit one-shot Paratext issue
  handoff is implemented and manually verified. AI alignment proposals and UAlign-derived
  corpus statistics (count/probability/PMI/SED-boost) are implemented; see the
  Phase 6/7 sections further down.
- Word-info lexicon popup: clicking a source token in the alignment modal
  opens a popup with decoded morphology and a Strong's dictionary gloss
  (lemma, transliteration, Meaning/Usage/Source), matching translationCore's
  own word-details popup. New vendored resource (`openscriptures/strongs`,
  public domain) plus a `lexicon.getEntry` protocol method. See "Alignment
  word-info lexicon popup" further down for the full detail, including two
  real bugs found and fixed in the same pass (one a genuine regression, one
  pre-existing).

## Phase roadmap status — read this first before picking up new work

The original plan (from the Claude Code sessions that did Phases 1-3, see
below) laid out 7 phases. Actual status as of 2026-08-21:

- **Phases 1-3**: done.
- **Phase 4 (USFM Checker + Versification): done (2026-08-21).** The USFM
  structural checker was completed first (vendored, wired up, verified
  against both source and a frozen packaged build — see that section
  further down). **Versification (detection, org-normalization, and a
  back-versification map) is now also done** — vendored separately, wired
  into the protocol as `versification.detect`/`orgRef`/`backVersificationMap`,
  and verified against both source and a real frozen build (see the
  Versification section further down). Nothing in Phase 4 has UI beyond
  what already existed — both halves are backend/protocol-only this pass,
  matching how the USFM checker itself shipped without a dedicated panel.
- **Phase 5 (Names & Transliteration, Uroman + Smart Edit Distance): done
  (2026-08-21).** A whole-book spelling-consistency check (Uroman +
  vendored Smart Edit Distance) is wired into `verse.runChecks` behind the
  existing `"local"` checks list — no frontend change needed, same as the
  USFM checker. See "Names & Transliteration — Phase 5 complete" further
  down.
- **Phase 6 (Alignment Intelligence, UAlign corpus stats): statistics engine
  done (2026-08-24).** The manual word-alignment editor added in
  `feat(alignment)` (see `docs/ALIGNMENT.md`) wasn't in the original plan at
  all — it was a prerequisite Phase 6 actually needed, since you can't
  compute corpus statistics over "human-approved alignments" if there was
  previously no way to create or approve one inside Bridge. That gap closed
  first; Phase 6's actual statistics work is now built on top of it —
  co-occurrence counts, translation probability, PMI, and an optional
  Smart-Edit-Distance phonetic boost, computed directly from Bridge's own
  completed alignments (not a vendored `ualign.py` — see the dedicated
  section further down for why). Backend/protocol-only this pass, same
  shape as Phases 4-5: two new read-only methods
  (`alignment.corpusStats.summary`/`forVerse`), no UI yet, no QaFinding
  output — AI alignment proposals stay in Phase 7, a scope decision made
  explicitly with the user before writing any code.
- **Phase 7 (Paratext/Logos connectors, AI explain, drag-and-drop): all four
  slices have real, tested work as of 2026-08-24, on a best-effort basis for
  the two that need a live external application to fully verify.** AI
  alignment proposals and drag-and-drop import are done and verified
  end-to-end (source + frozen). AI explain is wired and tested against real
  materialized evidence (a real TWL resource-layout bug and a missing
  translationAcademy bundle were both found and fixed along the way — see
  below), verified with a fake transport since no real API key was available
  this session. The Paratext connector's real, previously-missing companion
  plugin now exists and compiles against Paratext's actual installed
  interfaces, but was not deployed or loaded by a running Paratext instance
  this session (a protected-system-directory write was correctly blocked by
  this session's own safety controls). The Logos connector's real,
  previously-missing PowerShell/COM bridge script now exists and its
  process/protocol wiring is genuinely tested, but the actual COM automation
  calls inside it are unverified — Logos was not installed on this machine.
  See "Paratext/Logos connectors and AI explain — Phase 7 continued" further
  down for the full detail, what's verified vs. not, and exact next steps.

Between Phase 3 and now, real unplanned work also landed that mattered more
than staying on the numbered track: a 66-book import that took 4-6 minutes
and hit a hard timeout is now ~5-6 seconds (lazy per-book normalization), a
real security fix (plaintext API keys could persist to disk), and a
cancellable/retryable background job system replacing a blocking frontend
loop. None of that was in the original 7 phases either — it was necessary,
so it got done. Don't assume the next piece of work has to be the next
numbered phase in sequence; check what's actually broken or blocking first.

**A hard-won practice from this project so far, worth continuing**: every
external integration attempted (Wildebeest, the USFM checker, versification)
turned out to have a real, non-obvious problem that only surfaced by actually
running the code — wrong PyPI package name, a Python 3.13 compatibility
break, an unpublished dependency, a Windows-only `strftime` crash, a
version-skew bug between upstream's own GitHub and PyPI releases, a
class-level-state crash on a second call in the same process, a silently
different data license hiding inside an otherwise BSD-licensed vendor tree.
Don't trust a doc's
description of what a new integration will do — install it, run it against
real input, and read what actually happens before writing an adapter around
it. This is equally true of *this* documentation: verify claims made here
against the actual code before relying on them for follow-up work, the same
way you'd verify any third-party dependency's claims about itself.

### Versification — now done, see the dedicated section further down

This section originally recorded a research breadcrumb ("versification.py
was located in the same upstream repo, but not read, not vendored, and not
verified — do that next"). That work is now done (2026-08-21); see
"Versification — Phase 4 complete" further down in this document, and
`engine/vendor/greekroom-versification/NOTICE.md` for full provenance. Two
things from that breadcrumb turned out to matter and weren't visible until
the code was actually read and run: `versification.py` is a genuine
importable library (unlike the USFM checker's monolithic CLI script), so
it's wired in as a direct import, not a subprocess/helper executable; and
its `data/standard_mappings/*.json` files carry a **different license (CC
BY-SA 4.0) than the BSD-3-Clause code around them** — see the dedicated
section for why that's a real distinction, not a rubber-stamp of the USFM
checker's licensing precedent.

## Project context (carried over from the Claude Code sessions that did Phases 1-3)

This section summarizes what the earlier Claude Code work (`docs/CLAUDE_CODE_HANDOVER.md`,
not committed to this repo — it lives in the original handover doc) established, so this
file is self-contained for whoever picks it up next.

**What Bridge is:** a local-first Bible translation QA workbench replacing a legacy Python
Tkinter tool. One window, one Python sidecar process, Tauri/Svelte desktop shell.

**Core principle** (see `docs/ARCHITECTURE.md`):
- Greek Room says: "This is objectively suspicious."
- AI says: "Here is what it may mean in this passage."
- Human says: "This is what the translation should be."
- Nothing auto-applies to project files without human approval.

**Tech stack:** Tauri v2 (Rust) shell, Svelte 4 + TypeScript + Tailwind frontend, Python 3.13
PyInstaller-bundled sidecar (`bridge-engine`), Greek Room (Wildebeest adapter, mock fallback
if the real package isn't installed) plus 29 pre-existing `tc_ai_bridge` business-logic
modules (alignment, Paratext, Logos, AI, now also import), JSON-over-stdio protocol, pytest.
Windows is the primary target (`x86_64-pc-windows-msvc`); macOS/Linux are planned.

**Repo layout skeleton:**

```
engine/
  bridge_service.py          ← the sidecar dispatcher, read this first
  main.py                    ← sidecar entrypoint (PyInstaller target)
  greek_room_engine/         ← QA adapter layer (engine.py, models/finding.py,
                                protocol.py, adapters/, transport/stdio_transport.py)
  tc_ai_bridge/               ← 29 existing business-logic modules, not rewritten
                                (now 30, with project_import.py added — see below)
  tests/                      ← pytest suite

src/
  App.svelte, lib/stores.ts, lib/api/bridgeClient.ts, lib/types/finding.ts,
  lib/components/{ImportScreen,TopBar,VerseList,ReviewPanel,SettingsModal,ExportModal}.svelte

src-tauri/
  src/{main.rs,sidecar.rs,commands.rs}
  tauri.conf.json, capabilities/default.json, binaries/, icons/

docs/
  ARCHITECTURE.md, DEVELOPER_GUIDE.md, BUILD_LOG.md (this file), IMPORTS.md
```

### Phases 1-3 (done, before this import work)

- **Phase 1 — Protocol & sidecar consolidation:** `BridgeEngine` composes
  `GreekRoomEngine` + `tc_ai_bridge` behind one JSON protocol (`ping`, `engine.info`,
  `project.open`, `project.scan`, `chapter.verses`, `chapter.verseData`, `verse.get`,
  `verse.runChecks`, `verse.decide`, `verse.edit`, `settings.get`, `settings.set`,
  `export.aligned`, `export.nonAligned`). 24/24 pytest passing against a real fixture
  project with verified real file writes.
- **Phase 2 — Svelte frontend wired to the real sidecar:** full single-window UI
  (ImportScreen, TopBar, VerseList with inline colored findings, ReviewPanel with live
  Greek Room re-check and Accept/Reject/Ignore/Edit). Windows UTF-8 stdout fix applied.
- **Phase 3 — Decision persistence, chapter switching, whole-book, Settings & Export:**
  stable finding ids so decisions survive re-runs, chapter switching, "Run whole book"
  automation, `SettingsModal` (any OpenAI-compatible provider/endpoint/model/key),
  `ExportModal` (aligned JSON + simplified USFM export). Automation now uses
  sidecar-owned `checks.start/status/cancel/retry` jobs with real per-stage
  progress; cancellation is cooperative at check boundaries and failed or
  cancelled jobs remain retryable.

**This import-pipeline work (below) was new ground, not the originally planned Phase 4.**
The original roadmap after Phase 3 was: Phase 4 = USFM structural checker
+ versification; Phase 5 = names/transliteration (Uroman + SED); Phase 6 = alignment
intelligence (UAlign corpus stats); Phase 7 = Paratext/Logos connectors wired to the
protocol + AI explain + drag-and-drop import. Import work was picked up first because a
working import pipeline blocks everything downstream (you need real Scripture in the app
before checks/alignment/names work matter). **The USFM structural checker half of Phase 4
is now done (2026-08-20, see below)** — versification, and Phases 5-7, still haven't been
started.

### Gotchas still in force (from Phases 1-3, verified still true in the current code)

1. `TranslationCoreProject.summary` is a `@property`, not a method — calling `summary()` crashes.
2. `TranslationCoreProject.__init__` creates its own `self.journal`. Never create a second one.
3. Finding ids are stable (`_stable_finding_id()` in `bridge_service.py`, a sha1 of
   `chapter:verse:engine:check_type:disambiguator`) so decisions persist across runs.
   Don't revert to random `uuid4()` ids.
4. `verse.runChecks` re-applies prior decisions from `qa_decisions_for_verse()` after
   running checks — this must stay in the check flow, not a separate call.
5. Windows stdout UTF-8 fix (`sys.stdout.reconfigure(encoding="utf-8")` in
   `stdio_transport.py`) is critical — without it, non-Latin verse text crashes the
   sidecar silently and the Rust side just sees a timeout. Never remove it.
6. `plugins.shell.sidecar` must NOT be in `tauri.conf.json` — not a valid field, causes
   a startup panic. Sidecar permission comes entirely from `capabilities/default.json`'s
   `shell:allow-execute` entry.
7. Store keys are composite `chapter:verse` (e.g. `"1:3"`) — use `verseKey()` from
   `stores.ts`, not verse-only keys (silently collides data across chapters).
8. `ai_client.py`'s `OpenAIResponsesClient` endpoint is configurable via `base_url`;
   don't reintroduce a hardcoded `ENDPOINT` constant.
9. The sidecar binary name must match the Rust target triple exactly
   (`bridge-engine-x86_64-pc-windows-msvc.exe` on Windows — get the triple from `rustc -vV`).
10. Don't use icon-font classes (`ti-settings` etc.) for icon-only controls with no
    fallback label — PyInstaller/offline builds can't reach CDN icon fonts and they
    render as empty boxes. Use Unicode characters or pair with a text label.

### Known gaps still open (from Phases 1-3, verified still true in the current code)

- ~~USFM edit round-trip is still a stub~~ — **done 2026-08-20.** The real
  write logic wasn't missing from `tc_ai_bridge` at all — it turned out
  `TranslationCoreProject.apply_scripture_edit()` in `tc_project.py` was
  already a complete, working implementation (writes the target chapter
  JSON, reconciles alignment by word/occurrence signature, marks word
  alignment invalid, flags touched tN/tW index entries `verseEdits=True`,
  full journal transaction with rollback). `bridge_service.py`'s
  `edit_verse()` was just never calling it — it had its own separate no-op
  stub instead. Now it does: `edit_verse()` is a thin wrapper again, matching
  every other method in this file. The frontend (`ReviewPanel.svelte`) was
  already fully built for this (save, update store, re-run checks) and
  needed no changes beyond two small robustness fixes found while wiring
  this in: saving unchanged text is now a silent no-op instead of throwing
  an unhandled rejection (`apply_scripture_edit` correctly rejects a no-op
  edit; the UI just didn't call it before, so it never surfaced), and a real
  failure now shows an inline error instead of vanishing silently. Covered
  by two new tests in `test_bridge_service.py` that verify the edit actually
  lands (not just "committed": true) and that `WA_INVALID` surfaces on the
  next `verse.runChecks`. Full suite: 39/39 passing. Not click-tested in a
  running Tauri window this session (same build constraint as the other
  frontend work above) — worth a real click-through: edit a verse, confirm
  the new text persists after switching chapters and reopening the project,
  and that a stale-alignment finding appears.
- ~~`export.nonAligned` was a simplified reconstruction~~ — **superseded.** Both
  USFM exporters now use the retained source as a structural template and
  report the simplified fallback only for projects with no source USFM.
- **OWL repeated-word adapter doesn't exist yet** (`adapters/owl_adapter.py`).
- ~~Real Wildebeest package is still untested~~ — **done 2026-08-20, real
  engine wired up and passing tests. Requires Python 3.12, not 3.13.**

  **Root cause of the original blocker**: `pip install wildebeest` installs
  the wrong package (a same-named, unrelated ShopRunner image-processing
  library — never use it). The real one, from Ulf Hermjakob/USC-ISI, is on
  PyPI as **`wildebeest-nlp`** (only release: 0.9.2). Installing that under
  Python 3.13 fails to *compile* with `UnicodeEncodeError: ...surrogates not
  allowed` — one of its docstrings contains a literal `\uDC80`-`\uDCFF`
  escape as prose (describing surrogateescape handling, ironically), and
  **Python 3.13 newly disallows lone-surrogate escapes in docstrings at
  compile time** ([CPython issue #142411](https://github.com/python/cpython/issues/142411)
  confirms this is an intentional 3.13 change). Confirmed the same broken
  docstring is still on upstream's GitHub `master`, so installing from
  GitHub wouldn't have helped either. **Python 3.12.10 does not hit this
  restriction — confirmed working.** The machine this was developed on has
  since been switched from 3.13 to 3.12 entirely.

  **Engine now has a dedicated venv**: `engine/.venv` (gitignored), built
  with `py -3.12 -m venv .venv`, populated via
  `.venv\Scripts\python.exe -m pip install -e ".[dev,wildebeest]"`. The
  `wildebeest` extra in `pyproject.toml` pins `wildebeest-nlp==0.9.2` and is
  optional, not a hard dependency — `WildebeestAdapter` degrades to its mock
  whether it's installed or not, so a plain `pip install -e ".[dev]"` on any
  supported Python still works. Run tests via
  `engine\.venv\Scripts\python.exe -m pytest tests/ greek_room_engine/tests/ -q`
  (45 passed, includes 6 new real-engine tests, auto-skipped when the extra
  isn't installed).

  **Found and fixed a real related bug while investigating**: the adapter's
  `try: import wildebeest.wb_analysis ... except ImportError` only caught
  `ImportError`. Since the real failure mode is `UnicodeEncodeError`, simply
  having `wildebeest-nlp` installed (even with zero other code changes)
  would have crashed the entire sidecar at startup instead of degrading to
  the mock. Widened to `except Exception` — done and safe regardless of
  which Python version anyone else builds with.

  **What `_check_with_wildebeest()` now actually does** (previous version
  called a `wb_ana.check()` function that doesn't exist in the real
  package — that whole implementation was speculative and never once run
  against the real dependency). The real entry point is
  `wb_analysis.process(string=, lang_code=, json_output=<IO>)`, which
  returns an aggregate analysis report grouped by category — not a flat
  per-position issue list. Verified directly against 0.9.2 with real inputs
  (not documented anywhere upstream). Only three of its categories are
  wired into findings, each checked against real triggering input:
  `notable-token` (mixed-script tokens), `non-canonical` (NFD vs NFC form,
  with a real suggested-replacement), and zero-width/invisible characters
  from `block.ZERO_WIDTH`. Deliberately **not** treating every top-level key
  as a finding — most of the rest (`letter-script`, `block`'s ordinary
  per-character tallies) are corpus-level descriptive counts, not defects;
  verified a clean real Tamil verse produces zero findings from the real
  engine. `char-conflict` and `pattern` are real categories in the schema
  that no test input in this session happened to trigger — their shape is
  unverified, so they're not wired up. Don't guess it; verify against real
  triggering input first, the same way the rest of this was done.

  **Not yet done**: PyInstaller packaging for the real engine wasn't
  attempted this session. `wildebeest-nlp` ships a `data/` directory
  alongside its code — untested whether `wb_analysis.process()` reads
  anything from it at runtime (none of the three wired categories needed it
  in testing, but that doesn't rule out other paths). If building a frozen
  `.exe` with the real engine included, add
  `--collect-data wildebeest` to the PyInstaller command below as a
  defensive measure and verify carefully — this is unverified insurance,
  not a confirmed-necessary step.
- **Drag-and-drop import is still not wired** — no `onDragDropEvent` listener from
  `@tauri-apps/api` exists in `src/`; `ImportScreen` still requires the file picker.

### USFM structural checker — Phase 4 half done (2026-08-20)

New adapter, `engine/greek_room_engine/adapters/usfm_adapter.py`, registered
in `GreekRoomEngine`. Catches duplicate/missing verse numbers, unclosed
inline markers, and other structural USFM problems — a real gap nothing
else in the app checked before this.

**This is vendored, unpublished third-party code, not a normal
dependency — read this before touching it.** Unlike Wildebeest,
`greekroom`'s `usfm` submodule is **not published on PyPI** (only `owl`
and `gr_utilities` are, confirmed by inspecting the actual installed
wheel — `usfm`/`versification`/`wildebeest` exist only in the
`BibleNLP/greek-room` GitHub source tree). The decision to vendor it
anyway — rather than wait on upstream packaging or build a lesser
in-house checker — was made explicitly, with the license and long-term
maintenance cost discussed first. Don't undo that decision by casually
"cleaning up" or replacing this without the same consideration.

**Where it lives**: `engine/vendor/greekroom-usfm/` — `usfm_check.py`,
`ualign_utilities.py`, `Bible_USFM_tag_data.jsonl`,
`Bible_USFM_explanations.txt`, and a nested `greekroom/gr_utilities/`
package (see below for why). Full provenance — source URL, pinned commit
`18ddcf0e6c03fa2774b73b21186115d712e4cba9`, BSD 3-Clause license text and
attribution, and an explicit "don't edit these files in place" policy —
is in `NOTICE.md` in that directory. **Read `NOTICE.md` before re-syncing
against upstream or making any change here.**

**How it's invoked**: as an isolated subprocess (`UsfmAdapter.check_book()`),
not an import. In source mode the adapter uses the active Python interpreter
and the vendored script. In a frozen build it resolves the sibling
`bridge-usfm-checker[.exe]`, a separate PyInstaller artifact with a
Bridge-owned entrypoint (`engine/usfm_checker_main.py`). This matters:
`bridge-engine.exe` always starts the JSON-RPC loop and cannot be used as
`python.exe usfm_check.py`. The helper bundles the pinned vendor directory,
`regex`, both checker data files, and the license/notice. The adapter passes
stdin as `DEVNULL`, validates the exit code and report, and returns an
explicit `checker_error` for a timeout/crash/missing report instead of
silently treating failure as a clean book.

**Its own `-j/--json` output flag is dead code** — accepted by its
argparse setup but never referenced anywhere else in the 4,000 lines.
Verified by reading the source, not assumed; don't try to use `-j` and
expect a file to appear. The `.txt` report is the only real output to
parse, and its indentation depth to reach an individual issue varies by
category (3 or 4 levels) — `_parse_report()` in the adapter tracks an
indentation stack rather than assuming a fixed depth, and recognizes three
distinct real location-reference formats found by testing (see the
function's own docstring and `test_usfm_adapter.py` for the real captured
report text each was found in).

**Two real bugs found and fixed while integrating** — both documented
inline where fixed, in case they inform an upstream bug report later
(see the earlier discussion in this doc about contributing a packaging
fix upstream):
- A line in `usfm_check.py` used `%-d`/`%-H` in a `strftime` format string
  — glibc-only extensions Windows' C runtime doesn't support, raising
  `ValueError` immediately on startup on this Windows dev machine.
  Patched in place (marked `# BRIDGE PATCH`, per `NOTICE.md`'s policy for
  changes that are genuinely unavoidable) to the portable zero-padded
  equivalent — cosmetic only, just a report timestamp.
- The vendored `usfm_check.py` (pinned commit) calls
  `general_util.mkdirs_in_path()`, which doesn't exist in the *published*
  `greekroom==0.0.20` PyPI package's `gr_utilities.general_util` —
  **upstream's own GitHub source and PyPI release have already drifted
  apart from each other.** Fixed by also vendoring `general_util.py` from
  the *same* pinned commit (`greekroom/gr_utilities/` inside the vendor
  directory) and prioritizing it on the subprocess's own `PYTHONPATH`,
  rather than relying on the separately pip-installed `greekroom` package
  for this specific tool's dependencies. This is why the subprocess's
  `PYTHONPATH` matters — don't "simplify" it to just use whatever
  `greekroom` happens to be pip-installed.

**Wiring into checks**: whole-book, not per-verse — see
`UsfmAdapter`'s and `BridgeEngine._usfm_findings_for_book()`'s own
docstrings for why (each run spawns a subprocess loading a real tag
database). `bridge_service.py` computes and caches findings once per
book (keyed by project path), then filters to the requested chapter/verse
inside `run_verse_checks` whenever `"local"` or `"usfm"` is requested —
so it activates automatically with the frontend's existing default checks
list, no frontend change needed. A finding with no specific verse (e.g. a
whole-chapter "missing verse N") surfaces on that chapter's first verse,
since the UI has no chapter-level display slot. **Not invalidated by
`verse.edit`** — re-running the subprocess after every edit would be far
too slow, and a single verse edit essentially never changes book-wide
structure; re-opening the project re-runs it fresh. Findings get the same
stable-id treatment as Wildebeest/tN/tW findings, so decisions persist
across sessions.

**Verified**: `test_usfm_adapter.py` (parser unit tests against real
captured report text, not synthetic guesses; real end-to-end subprocess
tests against real broken and clean USFM; a subprocess-failure test
confirming explicit failure rather than false-clean degradation) plus
book-level success/failure caching tests in `test_bridge_service.py`.
`scripts/smoke_sidecars.py` runs the actual frozen `bridge-engine.exe` and
helper against balanced USFM containing duplicate and missing verses, then
asserts real `engine="usfm"` findings. Verified on Windows 2026-08-20.
Historical result at that milestone: 56 passed, 1 optional-Wildebeest module skipped.

**Packaging**: run `scripts/build-sidecars.ps1`; it builds both committed
specs and copies both target-suffixed artifacts into `src-tauri/binaries/`.
Tauri declares both in `bundle.externalBin`. Chapter/book automation starts a
sidecar-owned background job and polls lightweight status snapshots, so the
stdio dispatcher stays responsive while the helper runs. `verse.runChecks`
retains a 150-second timeout for the separate live per-verse recheck path.

### Versification — Phase 4 complete (2026-08-21)

New module, `engine/tc_ai_bridge/versification.py`, wrapping the vendored
Greek Room versification tool at `engine/vendor/greekroom-versification/`
(see that directory's `NOTICE.md` for full provenance, license, and the real
bugs found while integrating it — summarized below). Adds three sidecar
protocol methods: `versification.detect`, `versification.orgRef`,
`versification.backVersificationMap`.

**Different books/traditions number chapters and verses differently** — the
concrete motivating example: Psalm 3's Hebrew ('org') text opens with a
descriptive title ("A Psalm of David, when he fled from Absalom his son")
counted as verse 1, which most English ('eng') Bibles don't number as its
own verse. So eng verse 1 ("LORD, how are they increased that trouble me!")
is org verse 2, and the whole rest of the chapter is shifted by one. Six
standard schemas are supported: `org` (original Hebrew/Greek), `eng`
(English/Protestant), `rsc`/`rso` (Russian Synodal canonical/Orthodox),
`vul` (Vulgate/Catholic), `lxx` (Septuagint/Orthodox) — the same six
`versification.py` itself defines, sourced from real Paratext/Copenhagen
Alliance mapping tables, not invented by Bridge.

**This is vendored, unpublished third-party code, like the USFM checker —
but integrated differently, not by copying that precedent blindly.**
Confirmed freshly this session (not assumed from the USFM checker's
already-established facts): still not on PyPI; still BSD-3-Clause code from
the same pinned commit `18ddcf0e6c03fa2774b73b21186115d712e4cba9`. But two
things turned out to be genuinely different, found only by reading the
source and running it against real data:

1. **`versification.py` is a real library, not a CLI script.**
   `BibleStructure`, `Versification`, `VersifiedCorpus`,
   `VersificationMatch`, and `BackVersification` are classes with methods
   that operate on in-memory dicts — unlike `usfm_check.py`'s 4,000 lines
   with no reusable functions. So this is **imported directly into the
   long-lived `bridge-engine` process**, not run as a subprocess/helper
   executable. `tc_ai_bridge/versification.py` builds `VersifiedCorpus`
   objects straight from Bridge's own already-parsed chapter/verse text
   (`TranslationCoreProject.verses()`/`target_verse_text()`) — it never
   calls the vendored tool's file-based `load_corpus`/`write_corpus`/`main()`
   entry points at all.
2. **The mapping data carries a different license than the code around
   it.** `data/standard_mappings/*.json` originates from the Copenhagen
   Alliance Versification Working Group, not greek-room itself, and that
   project's own `LICENSE.md` splits code (Apache 2.0) from **data (CC
   BY-SA 4.0 — attribution + share-alike)**. Bridge's own root license is
   GPLv3 (already copyleft), which makes bundling attributed, unmodified CC
   BY-SA 4.0 reference data low-risk — but this was a real, separate
   decision, not a rubber-stamp of the USFM checker's BSD-3-Clause-only
   precedent. See `engine/vendor/greekroom-versification/NOTICE.md` for the
   full reasoning; re-review if these JSON files are ever modified before
   redistribution.

**Two real bugs found by actually running the vendored code against real
data** (documented in full in that directory's `NOTICE.md`, including how
each was reproduced):

- `Versification.load_versifications()` keeps **class-level** state
  (`Versification.versification_d`, `Versification.org`) that is never
  reset. A second real call in the same process — exactly what a naive
  per-project-open call from a long-lived `bridge-engine.exe` would do —
  hits a duplicate-schema branch, logs an error, and returns a
  half-constructed object with no `verse_id_list`; the very next line then
  crashes with `AttributeError`. Reproduced directly by calling it twice,
  not assumed from the USFM checker's own gotchas. Fixed in Bridge's
  wrapper by loading exactly once per process (lock + flag guard) —
  nothing outside `tc_ai_bridge/versification.py` should import the
  vendored `versification` module or call `load_versifications()` directly.
- `VersifiedCorpus.load_corpus()`/`write_corpus()` (and `main()`'s other
  file opens) use bare `open()` with no explicit encoding, which reproduces
  the exact same Windows `cp1252` `UnicodeDecodeError` already found and
  patched in `usfm_check.py`, confirmed here with a real Tamil string. **Not
  patched**, unlike the USFM checker's two `# BRIDGE PATCH` markers — Bridge's
  usage never calls those file-based methods (point 1 above), so this bug is
  architecturally avoided rather than fixed. If anything ever calls
  `load_corpus`/`write_corpus`/`main()` directly, patch it the same way.
- `vref.txt` (390 KB) and `psalm-descriptive-titles.txt` were deliberately
  **not** vendored: verified by reading the source that neither is read by
  any of the five classes Bridge actually calls, only by the CLI `main()`
  Bridge never invokes.

**Protocol methods** (all whole-book-scoped, cached per project path like
the USFM findings, computed lazily on first request rather than on every
`project.open`):

- `versification.detect` — sniffs the best-fitting schema for the open
  project's book against all six standard schemas and returns every
  schema's match cost, not just a bare label, so a caller can show how
  confident the detection is.
- `versification.orgRef` — normalizes one chapter:verse into its `org`
  equivalent (defaulting to the project's own detected schema). Returns a
  `mapping` field — `same` / `mapped` / `merge` / `split` — because
  cross-tradition shifts aren't always 1:1; callers should branch on that
  field rather than parsing `orgRef` as free text.
- `versification.backVersificationMap` — the inverse: every `org` verse in
  the book mapped back to the project's own numbering, for display/export
  use without hand-rolling the mapping direction.

**Verified**: `engine/tests/versification/test_versification.py` (22 tests against the
real vendored data — including the Psalm 3 shift and its back-versification
round trip, real merge/split mappings, unknown books, USFM verse
bridges/segments passed straight through, and a direct real-bug-reproduction
test that calls the module's public functions twice in a row to confirm the
class-level-state crash found above stays fixed) plus protocol-level
coverage in `test_bridge_service.py` (a Ruth fixture proving identity
mapping for a fully-canonical book, and a dedicated Psalms fixture proving
the real cross-tradition shift flows end-to-end through `handle_request`).
Full source suite: 122 passed.

**Concurrency — a second real bug found, this time by writing concurrency
edge case tests rather than by reading the source.** `detect_schema()`'s
scan over a schema's full verse list is pure Python and ~0.5s
single-threaded — but running several of those scans on different threads
*at once* doesn't scale proportionally under CPython's GIL, it degrades
catastrophically: measured directly at 16 concurrent callers taking **~47
seconds each** (not the ~8s naive linear scaling predicts — over 90x worse
than sequential). Reproduced with a genuinely fresh subprocess and
fine-grained per-thread timing, not assumed from general GIL folklore.
Since `bridge_service.py` calls this once per book on first request and
caches the result, the realistic trigger is a burst of near-simultaneous
`versification.detect` calls before any of those caches are warm — which
would look exactly like the sidecar hanging. Fixed by serializing the scan
with the same lock `_ensure_loaded()` already uses; the identical 16-thread
scenario then completes in ~8s total. `to_org_ref`/`back_versification_map`
do plain dict lookups (not this scan) and were separately measured safe
unlocked under the same concurrency — this isn't a "lock everything"
fix, and don't assume it's free insurance for code added to this module
later without re-measuring. Guarded by `test_versification_concurrency.py`,
which deterministically instruments the expensive matcher boundary and
asserts that concurrent callers never overlap there. Full details in
`engine/vendor/greekroom-versification/NOTICE.md`'s finding 4.

**Packaging — a real gap found and fixed, not assumed to be handled by
precedent**: because this is imported directly into `bridge-engine.exe`
rather than run as a separate helper (unlike the USFM checker), the
vendored `versification.py` + `data/` tree and its one third-party
dependency (`regex`) are invisible to PyInstaller's static import analysis
— `main.py` never imports them directly; `tc_ai_bridge/versification.py`
only reaches them via a runtime `sys.path.insert` + `import`. Left alone,
a frozen build would have shipped with `versification.detect` crashing on
first use. Fixed in `bridge-engine.spec`: `datas` now includes
`vendor/greekroom-versification`, extracted under `sys._MEIPASS` in a
frozen build (`tc_ai_bridge/versification.py`'s `_vendor_root()` resolves
the same way `resource_materializer.bundled_resources_source()` already
does), and `hiddenimports` now includes `regex`. **Verified against a real
PyInstaller build this session**, not left as an assumption: built
`bridge-engine.exe` from the updated spec, ran it as a real subprocess over
its stdio JSON-RPC protocol, and confirmed `versification.detect`/`orgRef`/
`backVersificationMap` all return correct real results (including the
Psalm 3 shift) from inside the genuinely frozen executable. The existing
`scripts/smoke_sidecars.py` frozen-sidecar smoke test also still passes
against the rebuilt executable.

**Not done in this pass**: no UI surfaces any of this yet (no schema
indicator, no dual-reference display, no export-time renumbering) — matching
how the USFM checker itself shipped as backend/protocol-only. `versification
.detect`'s result isn't wired into the findings feed as an informational
notice; it's exposed as project metadata a future UI can call on demand,
not injected automatically. Per-book detection results are cached only for
the lifetime of the process/current project (same lifetime as the USFM
findings cache), not persisted to disk.

### Names & Transliteration — Phase 5 complete (2026-08-21)

This section originally recorded a research breadcrumb (nothing wired up,
just an investigation of whether Uroman and Smart Edit Distance were even
real, installable dependencies). That work is now done — a whole-book
names/spelling-consistency check is wired into the protocol, tested against
both source and a real frozen build. The original research findings below
are kept as-is (still accurate, still worth reading for *why* things are
built the way they are); the "What was actually built" subsection further
down covers the implementation, the two real bugs found while building it,
and what's verified. **Verify all of this again before relying on it** —
same standing instruction as everywhere else in this doc.

**Uroman is a real, currently installable PyPI dependency — it does not
follow the Wildebeest name-trap pattern.** `pip install uroman` installs
the genuine package by Ulf Hermjakob, USC/ISI (same research group as
Wildebeest and the vendored `greek-room` tools), current release
`1.3.1.1`, `Requires-Python: >=3.10`, one real dependency
(`regex>=2024.5.15`, compatible with the `regex>=2023.10.3` floor already
in `engine/pyproject.toml` for the USFM checker). Confirmed by actually
installing it and reading the wheel's own METADATA — not assumed from the
package name alone, per this project's standing rule.

- **API, verified by direct use, not docs**: `uroman.Uroman()` is the
  entry point. Construction loads the full romanization table set — real
  measured cost on this machine was **1.8-2.1 seconds** — after which
  `romanize_string(s, lcode=...)` calls measured effectively instant
  (0.0000s) on repeat calls, confirmed by timing both a first and second
  call. This is a real, substantial cost that validates
  `docs/ARCHITECTURE.md`'s "loaded once, not per call, per Uroman's own
  documented recommendation" line — that line was written before anyone
  had actually read Uroman's docs or run the code; it's now confirmed true
  for a concrete, non-hypothetical reason (a multi-second table load), not
  rubber-stamped.
- **Data**: ships ~4.2 MB of real resource files inside the wheel itself
  (`UnicodeData.txt` at 1.9 MB, `romanization-auto-table.txt` at ~1 MB,
  `Chinese_to_Pinyin.txt`, `Scripts.txt`, etc. — 13 files total), resolved
  at runtime via `Path(__file__).parent / "data"` (confirmed by reading
  `Uroman.default_data_dir()`'s source directly). This is the same shape
  of packaging risk already flagged as unverified insurance for
  `wildebeest-nlp`'s own `data/` directory in the Real Wildebeest section
  above, and the same shape of problem versification's vendor tree solved
  with `bridge-engine.spec`'s `datas` entry — **not yet tested against a
  frozen PyInstaller build this session**; treat as a real open item, not
  a formality, before shipping.
- **Verified against real cross-script Biblical name data on Windows**,
  not synthetic strings: Hindi/Urdu/English "Nepal" (नेपाल → `nepaal`,
  نیپال → `nipal`, matching Uroman's own paper example exactly), Greek
  Ἰωάννης → `Ioannes`, Tamil யோவான் → `yoovaan`, Arabic محمد → `mhmd`,
  Hebrew יוֹחָנָן → `yochanan`. No network access was used or required —
  entirely offline, matching Bridge's own connectivity requirement.
- **License — real, confirmed drift, same shape of bug as the
  `greekroom` package's Apache/BSD classifier mismatch already recorded
  above, not a new problem shape.** Both the published wheel's METADATA
  *and* upstream's own `pyproject.toml` on GitHub classify the license as
  `"License :: OSI Approved :: Apache Software License"`. The actual
  bundled `LICENSE.txt` (confirmed identical between the PyPI wheel and
  upstream's GitHub `LICENSE.txt`, so this is an upstream authoring
  mistake, not a wheel-build artifact) is **not Apache 2.0 text at all** —
  it's a custom MIT-style permissive license with a mandatory attribution
  clause: *"Any publication of projects using uroman shall acknowledge its
  use: 'This project uses the universal romanizer software "uroman"
  written by Ulf Hermjakob, USC Information Sciences Institute
  (2015-2020)'."* This confirms `docs/ARCHITECTURE.md`'s previously
  unverified "Uroman has its own attribution requirement" note was right,
  but for a more specific reason than assumed — it isn't an extra clause
  layered on top of Apache 2.0, the Apache classifier itself is simply
  wrong. Bridge will need to surface that exact acknowledgment string
  somewhere real (an about screen, a NOTICE.md, export metadata), not just
  bundle a copy of the license file.

**Smart Edit Distance (SED) is not a separately published tool and not
part of a different repo — it lives in the same pinned `BibleNLP/greek-room`
commit (`18ddcf0e6c03fa2774b73b21186115d712e4cba9`) already vendored for
the USFM checker and versification**, at
`smart_edit_distance/src/smart_edit_distance.py` plus
`smart_edit_distance/data/string-distance-cost-rules.txt` (general rules)
and a second Devanagari-specific cost file. Confirmed not on PyPI under
`smart-edit-distance`, `smart_edit_distance`, or inside the published
`greekroom` PyPI package (`0.0.20`, which — per the USFM checker's own
established precedent — only ships `owl`/`gr_utilities`, not this). Same
integration shape as the USFM checker and versification: unpublished,
vendor it from the pinned commit, don't wait on upstream packaging.

- **Shape: closer to versification.py than to usfm_check.py.** It's a
  single ~430-line file, pure Python standard library only (`argparse`,
  `logging`, `re`, `sys`, `typing` — zero third-party imports, and no
  import of Uroman itself despite the module's own docstring describing
  the two as complementary). A `SmartEditDistance` class holds
  per-instance state (`self.ht`, `self.max1`/`self.max2`, etc.) — no
  class-level shared state like the real bug found in
  `Versification.load_versifications()`, so the same second-call crash
  class of bug doesn't reproduce here (checked directly by instantiating
  it twice). This argues for a direct import into the long-lived
  `bridge-engine` process, the same way `versification.py` was integrated,
  not a subprocess/helper executable like the USFM checker.
- **License: plain BSD-3-Clause, same as the repo root — no hidden
  second license this time.** Checked directly because versification's
  CC BY-SA data-license surprise means this can no longer be assumed;
  this time there wasn't one. The cost-rule `.txt` data files carry no
  separate license notice of their own.
- **Real bug found by actually running it against the real data file —
  the third confirmed instance of the exact same bug class this project
  keeps finding in every one of these vendored/adjacent tools.**
  `SmartEditDistance.load_smart_edit_distance_data()` calls bare
  `open(raw_cost_file)` with no explicit encoding when given a string
  path. The real `string-distance-cost-rules.txt` contains 117 non-ASCII
  bytes (confirmed by reading it as raw bytes, not assumed from the
  filename). Loading it under this machine's default Windows locale
  (`cp1252`) throws `UnicodeDecodeError: 'charmap' codec can't decode byte
  0x90 in position 3802: character maps to <undefined>` — reproduced live,
  not inferred. Needs the same explicit `encoding="utf-8"` fix already
  applied to `usfm_check.py` (2 call sites) and identified-but-architecturally-
  avoided in `versification.py`'s file-based methods.
- **Functional verification, combined with real Uroman output, not
  hypothetical pairs**: loaded the real cost-rules file (417 entries / 834
  compiled rules) and ran `string_distance_cost()` on name pairs. Results
  matched the module's own docstring examples exactly: `"Josef Schumann"`
  vs `"Joseph Schuman"` scored **0.03** (plain Levenshtein: 3),
  `"Muhammad"` vs `"Mohamed"` scored **0.22** (plain Levenshtein: 3),
  `"Jim"` vs `"Kim"` correctly stayed at the default substitution cost of
  **1.0** (matching the docstring's own claim that these should read as
  more different than the phonetic-variant pairs above). A live
  cross-script test combining both tools — Tamil யோவான் (uroman:
  `yoovaan`) vs English `"John"` — scored **1.52**, correctly landing
  between "same word, different spelling convention" and "unrelated
  strings." This is the first time in this session anything actually
  chained Uroman's output into SED's cost function, not just tested each
  tool in isolation.

The above was investigation only when first written — nothing was built
yet, deliberately, until the real shape of both dependencies was confirmed.
Everything below this point was added afterward, once the user confirmed
the check's actual design.

#### What was actually built

**Design decision, made explicitly with the user before writing any code**:
of three options presented (a general whole-book phonetic spelling check; a
translationWords-names-anchored consistency check requiring a new,
unproven candidate-extraction heuristic; or infrastructure-only with no
check at all), the user chose the general whole-book check. It compares
every pair of distinct target-language word types used in the open book
and flags pairs whose *romanized* forms are suspiciously close (low Smart
Edit Distance cost) but not identical. It never claims two spellings are
the same name or that either is wrong — only that they're objectively
close — keeping it on the "Greek Room says: this is objectively
suspicious" side of the architecture doc's three-way design boundary. The
names-anchored alternative was explicitly rejected as riskier: it would
have required guessing which target word renders a given name from
translationWords occurrence data alone (no word alignment exists for most
verses), a real semantic claim Greek Room has no reliable way to verify.

**Vendored**: `engine/vendor/greekroom-smart-edit-distance/` — pinned to
the exact same `BibleNLP/greek-room` commit already used for the USFM
checker and versification (`18ddcf0e6c03fa2774b73b21186115d712e4cba9`).
Confirmed via the GitHub API tree listing that `smart_edit_distance.py`
plus two cost-rule data files are the *entire* contents of that directory
at this commit. See that directory's `NOTICE.md` for full provenance.
`uroman>=1.3.1.1` was added as a real, hard dependency in
`engine/pyproject.toml` (not optional/mock-fallback like Wildebeest — it
has no known installability problem on any currently supported Python
version). `regex`'s floor was raised from `>=2023.10.3` to `>=2024.5.15`
to satisfy uroman's own requirement — same package, no real conflict.

**New code**: `engine/greek_room_engine/adapters/names_adapter.py`
(`NamesAdapter`, registered in `GreekRoomEngine`), a new
`GreekRoomEngine.check_book_names()` method mirroring
`check_book_usfm()`'s shape, and `BridgeEngine._names_findings_for_book()`
in `bridge_service.py` mirroring `_usfm_findings_for_book()` — same
whole-book caching-per-project-path pattern, same cache-clearing on
`project.open`/`project.import`, same stable-id treatment (here keyed on
the two spellings being compared, sorted, so decisions survive repeat
runs regardless of which spelling a given run happens to treat as
"majority"). Wired into `run_verse_checks` behind `"local" in checks or
"names" in checks` — the exact same gating shape as the USFM checker —
which is why **no frontend change was needed**: `App.svelte` and
`ReviewPanel.svelte` already always send `checks: ["local", "greekroom"]`.

**Two real bugs found by actually running this against real data, not by
reading the source**, on top of the encoding bug already found and
documented (architecturally avoided, not patched) during the research
phase and recorded in the vendor directory's `NOTICE.md`:

1. **A real false-positive class**: testing against plain English
   "church"/"churches" — an ordinary, correctly-spelled singular/plural
   pair, not a spelling inconsistency — scored **0.70** under the general
   cost-rules file, because rules tuned for name-like variation (dropped
   vowels, consonant doubling) also happen to cover common inflectional
   endings. The vendored module's own docstring frames its "Jim"/"Kim"
   example (cost 1.0) as meaningfully different, which suggested a
   threshold just under 1.0 — but 1.0 alone isn't a safe ceiling. The
   threshold (`_MAX_COST` in `names_adapter.py`) was tuned down to **0.4**,
   the highest value that still keeps every real phonetic-variant pair
   found this session (Josef Schumann/Joseph Schuman = 0.03, Muhammad/
   Mohamed = 0.22, a synthetic Titus/Tituss typo = 0.02, Yohaan/Yohan =
   0.02) while excluding the church/churches false positive. This is an
   inherent limitation of an edit-distance-family metric applied to
   morphologically rich languages, not something a threshold alone fully
   solves — expect some inflectional false positives to still surface near
   this ceiling; the human reviewer is the actual filter, per this
   adapter's own design-boundary docstring.
2. **A real, serious performance bug**: the first working version pruned
   comparison candidates by length-bucket only (only compare romanized
   forms within a couple of characters of each other in length). Measured
   directly against a synthetic ~3000-distinct-word-type vocabulary (a
   plausible single-book size): **133 seconds**. Length-bucket pruning
   alone still leaves well over a million length-compatible pairs, and
   each `string_distance_cost` call is a non-trivial DP. Fixed with
   character-bigram "blocking" (`NamesAdapter._candidate_pairs`), a
   standard approximate record-linkage technique: a real near-duplicate
   pair (1-2 edits) shares almost all of its bigrams, so requiring most
   bigrams to overlap before ever calling the expensive comparison throws
   away the overwhelming majority of unrelated pairs cheaply first.
   Measured result on the same 3000-token benchmark: **5.5 seconds** (a
   ~24x improvement). An 8000-token benchmark (worse than most single
   books) took 27.9s — still tolerable for a backgrounded preflight job
   (the same job shape already tolerates the USFM checker's 120s subprocess
   timeout), but scaling is worse than linear with the current blocking
   parameters (`_MAX_BIGRAM_MISMATCH`, `_MAX_BIGRAM_BUCKET` in
   `names_adapter.py`) — a real area for future tuning if a genuinely huge
   single-book vocabulary turns out to need it, not claimed as fully solved.
   Both benchmarks used adversarial uniformly-random letter strings (worst
   case for bigram blocking, since real language text has far more skewed
   bigram frequency); real target-language vocabulary should perform at
   least as well.

**Known, deliberately unaddressed limitations** (documented rather than
silently absent, matching this project's own established practice):
whole-book in-process comparison has no mid-flight cancellation support
(unlike the USFM checker's subprocess, which the check-job preflight can
terminate) — acceptable given the measured timings above, revisit if real
usage shows otherwise. The Devanagari-specific supplementary cost-rules
file is vendored but not loaded (see the vendor directory's `NOTICE.md`).
No UI surfaces this beyond the existing findings list — same as how the
USFM checker and versification both shipped backend/protocol-only.

**Verified**:
- `engine/greek_room_engine/tests/test_names_adapter.py` (9 tests) — real
  uroman + real vendored SED, no mocks: a planted typo gets flagged and
  anchored at its own occurrence; the real Muhammad/Mohamed cost (0.22) is
  asserted exactly, not just "some finding exists"; the church/churches
  false-positive class and the vendored module's own Jim/Kim example are
  both asserted to NOT be flagged; a bigram-blocking performance regression
  guard.
- `engine/tests/service/test_names_check.py` (6 tests) — protocol-level, through
  `BridgeEngine.handle_request`: a real finding surfaces via
  `verse.runChecks` with `checks: ["names"]`, is correctly absent when
  "names" isn't requested, has a stable id across a fresh `BridgeEngine`
  instance (simulating an app restart), is listed as `usingRealEngine` in
  `engine.info`, doesn't leak across a `project.open` switch to a different
  project, and — importantly, since every other test above this one only
  exercised English, where Uroman's romanization step is close to a no-op
  — a real Tamil case through full verse sentences (not isolated words):
  an inconsistently included/omitted long-a vowel sign on the same name
  ("யோவான்" vs "யோவன்") is correctly flagged, exercising
  `whitespace_tokens`' real combining-mark/punctuation handling on actual
  target-language text, not bypassing it with synthetic tokens. Bridge's
  real target languages are mostly non-Latin, so this was worth confirming
  before treating the check as done, not just a nice-to-have extra test.
- Full source suite: **137 passed** (122 before this phase + 15 new).
  `npm run check` still reports 0 errors/0 warnings (no frontend files
  touched).
- **Frozen build, verified this session** (not left as an open item like
  Wildebeest's packaging was): built both sidecars via
  `scripts/build-sidecars.ps1` against the updated
  `engine/bridge-engine.spec` (now also bundling
  `vendor/greekroom-smart-edit-distance` and uroman's data files via
  `collect_data_files('uroman')`, the same `sys._MEIPASS` extraction
  pattern versification's vendor tree already uses). Ran the real frozen
  `bridge-engine.exe` as an actual subprocess over its stdio JSON-RPC
  protocol with a real planted "Tituss"/"Titus" typo: `engine.info`
  reports `usingRealEngine: true` for the `names` adapter, and
  `verse.runChecks` returned the correct real finding
  (`original_text: "Tituss"`, `suggested_replacement: "Titus"`, cost
  `0.02`) — confirming uroman's ~4.2MB bundled data directory and the
  vendored SED tree both actually resolve correctly under `sys._MEIPASS`
  in a genuinely frozen executable, not just in source mode. This had been
  explicitly flagged as unverified in the original research breadcrumb;
  it no longer is.

### Alignment corpus statistics — Phase 6 complete (2026-08-24)

**Investigation first, same discipline as every prior phase.** "UAlign" was
an unresearched name in the roadmap, same as Uroman/SED were before Phase 5.
The first, false lead: `ualign_utilities.py` already sits vendored in two
places (`engine/vendor/greekroom-usfm/` and
`engine/vendor/greekroom-versification/greekroom/usfm/`) and looked, from
the name alone, like it might already be the statistics engine. Reading it
showed otherwise — it's a small set of generic Bible-reference/HTML utility
classes (`BibleUtilities`, `BibleRefSpan`, `ScriptDirection`, ...) already
actively imported by `usfm_check.py` and `versification.py`, not dead
weight and not UAlign itself. Its own docstring pointed at the real answer:
"utilities, taken from script `ualign.py`."

Pulling the pinned `BibleNLP/greek-room` commit's full GitHub tree (no `gh`
CLI on this machine — used the raw GitHub API and
`raw.githubusercontent.com` directly instead) found `utilities/ualign.py`:
a real, 3,598-line, unpublished script at the same pinned commit
(`18ddcf0e6c03fa2774b73b21186115d712e4cba9`) already vendored three times
for the USFM checker, versification, and Smart Edit Distance. Confirmed not
on PyPI under any plausible name and outside the published `greekroom`
package (`0.0.20`, which — per the USFM checker's own established
precedent — only ships `owl`/`gr_utilities`). Its `AlignmentModel` class
computes exactly the statistics ARCHITECTURE.md's own non-goal section
names ("local statistical recomputation — fertility, PMI, frequency"):
bilingual co-occurrence counts, per-word fertility distributions, joint
counts, and a Smart-Edit-Distance-boosted translation probability. Its own
docs page (`site/content/en/align.md`) independently confirms the same
statistic vocabulary (count, probability, joint count, phonetic/SED score)
in the context of a word-alignment *visualization* tool.

**Decision: reimplement the statistics against Bridge's own data, don't
vendor `ualign.py`.** Its actual I/O contract is built for a
`fast_align`-style pipeline — Pharaoh-format alignment files, `"e ||| f |||
ref"` triple-pipe parallel-text files, before/after ttable model files —
plus it bundles HTML visualization, morphology-variant checking, and a
spell-checker Bridge doesn't want. None of that matches tC's own
`alignmentData/<book>/<chapter>.json` shape or Bridge's in-memory
`VerseAlignment`/`TokenRef` objects. Vendoring it and subprocessing it like
`usfm_check.py` would mean synthesizing fake files in its exact expected
format just to extract a few numbers back out of HTML/log output designed
for a different UI. License-wise this also sidesteps the one real
entanglement risk `ualign.py`'s usage comment references — an external,
separately-licensed `fast_align` binary to produce an initial alignment —
since Bridge already has human-approved alignments as input and never needs
to run an aligner from scratch. The formulas below mirror `ualign.py`'s own
`AlignmentModel.support_probability()` (verified by reading that method
directly) but are original, small, textbook implementations, not copied
code — no new vendor license obligation beyond the SED tree already
vendored and licensed for Phase 5.

**A related false lead worth recording**: `tc_ai_bridge/
alignment_reliability.py` already exists and sounds on-topic, but it's
AI-link-proposal compilation (confidence thresholds, protected-group
merging for Phase 7's AI alignment suggestions), not corpus statistics —
real evidence Phase 7 already has scaffolding, and further reason to keep
this phase scoped to statistics only. That scope split (statistics this
session, AI proposals deferred to Phase 7) was decided explicitly with the
user before any code was written, matching ARCHITECTURE.md's v0.9.x
roadmap line which had bundled both under one entry.

**What was built**: `engine/tc_ai_bridge/alignment_statistics.py`
(`build_corpus_stats()`, `CorpusStatsTable`, `CorpusPairStats`) plus two new
`bridge_service.py` protocol methods, `alignment.corpusStats.summary` and
`alignment.corpusStats.forVerse`. Scans every verse marked complete — tC's
own `tools/wordAlignment/completed/<chapter>/<verse>.json` markers, the
same signal `alignment.complete` writes via `mark_word_alignment_completed`
— across the open book, plus (by default) every already-normalized sibling
book in the same multi-book collection (`.bridge/collection.json`); a
sibling still marked lazy is skipped rather than force-materialized just to
compute statistics. The scan reads only chapters that actually contain a
completed-verse marker, and reads each such chapter's alignment JSON
exactly once regardless of how many completed verses it holds — the real
cost driver is completed verses, not the project's total verse count,
which matters for a whole-Bible-sized collection where most verses are
never touched by manual alignment. `corpusStats.forVerse` returns, for
every top↔bottom link in one verse's *current* alignment groups (it does
not need to be complete itself): joint count, source/target counts,
translation probability, PMI, and — when Uroman and the vendored SED are
both available — a romanized SED cost and phonetic-boosted probability for
sparse pairs. PMI is reimplemented directly in the new module (the same
standard formula also sitting unused in vendored `ualign_utilities.py`,
not imported from it, to avoid coupling this module to
`versification.py`'s vendor-path/sys.path lifecycle for two lines of math
with no vendor-specific tuning). The SED-boost path reuses the exact same
vendored Smart Edit Distance tree and loading pattern already established
in `names_adapter.py`, plus its own separate lazy Uroman singleton — a
deliberate, documented tradeoff: sharing Uroman's singleton across the
`greek_room_engine`/`tc_ai_bridge` layers would need a real refactor of
already-shipped, tested Phase 5 code, out of scope here, so a session using
both the names check and corpus stats pays Uroman's one-time ~1.8-2.1s
table load twice rather than once.

Caching mirrors the USFM/versification/names pattern (per project path,
cleared on `project.open`) but goes one step further: it's *also*
invalidated by every alignment-mutating call (`realign`/`unalign`/`save`/
`complete`/`undo`) for the currently open book, rather than only on the
next project reopen — cheap enough (a linear scan over already-completed
verses, not a subprocess or whole-book vocabulary comparison) that keeping
it fresh on every mutation was worth it.

**Measured, not guessed, per this project's own standing rule**: a
synthetic but realistically-shaped 2,000-completed-verse corpus (50
chapters × 40 verses × 6 token pairs, comparable to a heavily-aligned large
book) scanned in well under a second of real compute — the entire pytest
process for that one test, including Python startup, was 2.42s. This is
genuinely CPU-only, zero AI/API cost: no LLM tokens, no network calls, just
counting already-known token pairs from alignment JSON already on disk
plus a few arithmetic formulas over those counts.

**Verified**: `engine/tests/alignment/test_alignment_statistics.py` (7 new tests, no
mocks) — completed-only filtering (an incomplete sibling verse is excluded),
hand-computed PMI/probability values checked against the formula directly,
multi-book aggregation with a real lazy-sibling skip, protocol-level
`summary`/`forVerse` calls through `BridgeEngine.handle_request`, cache
invalidation when a verse is newly marked complete, a real (not mocked)
Uroman + vendored-SED case (a Greek name romanizing to "Ioannes" paired
against a target spelling romanizing to "Ioanes" scores a real SED cost of
0.02 and a boosted probability at or above the plain co-occurrence
probability — and, checked separately, not every cross-script "same name"
pair clears SED's `max_cost=1` ceiling: Ἰωάννης/"Ioannes" against Tamil
யோவான்/"yoovaan" — the exact real romanization pair from Phase 5's own
investigation — scores no cost at all, handled by the None/None graceful
fallback, which is expected: SED is tuned for near-duplicate spellings, not
open transliteration variance), and the 2,000-verse performance measurement
above. Full source suite: **144 passed** (137 + 7 new), plus the one
pre-existing, load-sensitive `test_versification_concurrency.py` failure
noted at the top of this document — confirmed unrelated to this phase's
changes, reproducible on unmodified `main`.

**Frozen build, verified this session (2026-08-24)**: no new PyInstaller
`datas`/`hiddenimports` entries were needed — this module reuses the
`vendor/greekroom-smart-edit-distance` tree and `uroman` data files already
bundled for Phase 5 — but "expected to work" was checked rather than
assumed. `scripts/smoke_sidecars.py` was extended to call
`alignment.corpusStats.summary`/`forVerse` immediately after its existing
`alignment.complete` step (the fixture verse's realigned single group is
still complete at that point, before the script's own `alignment.undo`
call). Ran the real frozen `bridge-engine.exe` as an actual subprocess over
its stdio JSON-RPC protocol: `corpusStats.summary` correctly reported
`versesScanned: 1`, and `corpusStats.forVerse` returned a real
`jointCount: 1`/`translationProbability: 1.0` pair for the completed
verse's own link. `bridge-engine.spec` needed no changes for this.

**Not done in this pass**: no UI surfaces this yet (no reliability
color-coding in the alignment editor, no corpus-stats panel) — matching how
the USFM checker, versification, and the names check all shipped their
first pass backend/protocol-only. No QaFinding output — this phase was
scoped to data only, per the explicit choice made with the user; a future
phase could flag statistically-outlier links in already-completed
alignments, the same way Phase 5's names check flags outlier spellings, but
that needs its own threshold-tuning investigation against real data, the
same way Phase 5's 0.4 cost ceiling was tuned rather than guessed. All
testing so far — this phase and every prior one — uses small, hand-written
synthetic fixtures (a handful of made-up words per test), not a real
published translation at any real scale (e.g. a real Tamil IRV-sized
corpus); real tools (Uroman, SED) have been verified against real
individual words/names, but never against a large real corpus's actual
statistical distribution. That's a real gap worth closing before trusting
these statistics' *usefulness* (as opposed to their correctness) on an
actual translation project, not yet attempted.

### AI alignment proposals and drag-and-drop import — Phase 7, part 1 (2026-08-24)

A prior session's investigation (recorded in the previous revision of this
section) found that `tc_ai_bridge/ai_client.py`'s `propose_alignment()` and
`tc_ai_bridge/alignment_reliability.py`'s `compile_link_proposal()` were
already real, complete implementations from Phases 1-3 — deterministic
confidence thresholds, protected/locked existing-group handling, connected-
component group compilation — with **zero test coverage anywhere in the repo**
and no protocol method calling either one. This session verified that was
still true (`grep -rl "OpenAIResponsesClient\|compile_link_proposal"
--include=*.py` outside `tc_ai_bridge/` matched only `bridge_service.py`'s
unrelated `structural_issues` import), then wired and tested it, and
separately wired drag-and-drop import. The user was asked explicitly which
Phase 7 slice(s) to prioritize (see the AskUserQuestion in this session);
AI explain and the live Paratext/Logos connectors were deliberately not
attempted — see their own subsections below for the real, confirmed
blockers.

**AI alignment proposals — new protocol methods, `alignment.aiPropose` and
`alignment.aiApplyProposal`, in `bridge_service.py`.** Deliberately two
separate calls, not one: `aiPropose` is read-only (asks AI for individual
token links, then `compile_link_proposal` compiles them deterministically
into legal tC groups) and writes nothing to project files; `aiApplyProposal`
is a separate, explicit, human-triggered step. This keeps AI alignment on
the same "nothing auto-applies without human approval" side of
`docs/ARCHITECTURE.md`'s three-way design boundary as every other Greek
Room/AI feature in Bridge.

- `BridgeEngine.__init__` gained an `ai_transport: Optional[Transport] = None`
  parameter — the same `Callable[[url, headers, body, timeout], (status,
  bytes)]` shape `ai_client.OpenAIResponsesClient` already accepted,
  threaded one level up so `propose_ai_alignment()` can be unit-tested with
  a fake HTTP transport instead of a real OpenAI-compatible API key. This is
  exactly the dependency-injection pattern the previous session's
  investigation flagged as the way to test this without live network
  access. Production code paths still pass `None`, meaning "use the real
  network" (`ai_client.default_transport`).
- `apply_ai_alignment_proposal()` reuses the *exact* identity-checked save
  pipeline manual realign/save already goes through (`_save_alignment`,
  including `validate_preparation_proposal`'s defense-in-depth check that an
  AI proposal cannot detach/remap an already-established group, on top of
  `compile_link_proposal`'s own protection) — an AI-sourced edit gets no
  more trust than a manual one.
- `propose_ai_alignment()`'s returned `proposal` object keeps
  `compile_link_proposal`'s own snake_case field names
  (`top_ids`/`bottom_ids`/`requires_human_review`/...) verbatim, breaking
  this file's usual camelCase protocol convention on purpose: the object
  must round-trip byte-for-byte from `aiPropose`'s response back into
  `aiApplyProposal`'s request body, which calls `alignment_engine.
  apply_proposal()` expecting those exact keys. Re-keying it in either
  direction would risk a lossy/asymmetric conversion for no real benefit,
  since the frontend only needs to read `top_ids`/`bottom_ids` generically
  to resolve token labels — the same way `AlignmentModal.svelte` already
  does for ordinary alignment groups. See the comment at the return
  statement in `propose_ai_alignment()` for the same rationale inline.
- `settings.record_ai_usage()` (`tc_ai_bridge/secret_store.py`) was itself
  real, already-implemented, dead code before this session — nothing called
  it, confirmed by grepping the whole `engine/` tree. `alignment.aiPropose`
  is now its first real caller, so `settings.get`'s `aiUsage` total actually
  accumulates real token/cost data instead of always reading zero.
- Rust: `alignment_ai_propose`/`alignment_ai_apply_proposal` commands added
  to `commands.rs` and registered in `main.rs`'s `generate_handler!`, the
  same thin-wrapper shape as every other alignment command.
  `sidecar.rs`'s per-method timeout table gained
  `"alignment.aiPropose" => 260` — `ai_client.py`'s own HTTP timeout is 240s
  with retries, so the default 30s interactive timeout would have made the
  UI report "timed out" while the sidecar was still legitimately waiting on
  a real model call. This one call is a direct blocking request (like
  `verse.runChecks`'s existing 150s), not routed through the `checks.start`
  background-job system — consistent with that method's own precedent for a
  single-verse operation, and simpler than building job-tracking for what
  is, from the UI's perspective, one blocking button press.
- Frontend: `AlignmentModal.svelte` gained an "Ask AI to propose alignment"
  button next to the existing manual align/unalign controls, and a
  preview panel for the returned proposal — shows only the non-`existing`
  groups (what would actually change), a `requires_human_review` warning
  banner summarizing conflict/uncertain-link/target-only counts when
  present, and explicit "Apply proposal & save" / "Discard proposal"
  buttons. Applying reuses the same `refreshChecks()` path (re-run
  local+Greek Room checks, update stores) every other alignment mutation in
  this component already uses. `bridgeClient.ts` gained
  `aiProposeAlignment`/`aiApplyAlignmentProposal`, and `types/finding.ts`
  gained `AlignmentAiProposal`/`AlignmentAiProposeResponse` (documented
  inline with the same snake_case rationale as above).

**Verified**: `engine/tests/alignment/test_ai_alignment_propose.py` (4 new tests, real
`compile_link_proposal`/`apply_proposal` logic exercised through
`BridgeEngine.handle_request` with a fake transport, no mocks of Bridge's
own code) — `alignment.aiPropose` fails with a clear `ai_error` when no API
key is configured; a real accepted link compiles into a new group while the
existing protected group survives untouched, and `settings.
get_ai_usage_totals()` reflects the call; a cross-link between two different
already-established groups is correctly rejected as a
`protected_alignment_conflict` (not applied, `requires_human_review: true`)
rather than silently merging two independent human decisions; and
`alignment.aiApplyProposal` saves a proposal through the normal
identity-checked pipeline, filling the previously-empty word bank and
empty-bottom group. One real fixture bug found while writing these: the
project's existing test fixtures for alignment data always include
`"type": "bottomWord"` on every bottom-side token because
`TokenRef.to_dict(bottom=True)` always adds it — a first draft of this
session's fixture helper omitted it, which made `alignment.aiApplyProposal`
spuriously report "changed on disk" (the raw-file identity check in
`save_verse_alignment` compares byte-for-byte against `expectedOriginal`,
which came from a real `to_dict()` call that *does* include `type`). Fixed
in the test fixture, not in product code — this is a pre-existing,
already-correct on-disk data contract this session hadn't matched yet, not
a Bridge bug. Full source suite: 148 passed (144 + 4 new), plus the one
pre-existing, load-sensitive `test_versification_concurrency.py` failure
noted at the top of this document (confirmed unrelated, reproduced again
this session under background load). `npm run check` (0 errors/0 warnings)
and `npm run build` (succeeds, same pre-existing chunk-size warning) both
still pass. `cargo check` succeeds.

**Not done in this pass**: not click-tested in a running Tauri window (no
sidecar binary was built this session — same build constraint noted
elsewhere in this document). `mode: "audit"` is wired end-to-end on the
backend and typed on the frontend but the UI only ever requests
`"gap_fill"` — `compile_link_proposal`'s own docstring frames `audit` as a
read-only whole-verse comparison "not meant to be applied directly", and
`aiApplyAlignmentProposal`'s `validate_preparation_proposal` call correctly
rejects an audit-mode proposal that would detach an established group, but
no UI surfaces an audit-only read-only comparison view yet. AI usage
totals accumulate in `settings.json` but are still not displayed anywhere
in `SettingsModal.svelte` — `get_ai_usage_totals()`'s data has been real
since this phase but remains invisible to the user; a small, separate UI
gap worth closing later.

**Drag-and-drop import — `bridgeClient.ts` gained `onFileDrop()`**, using
Tauri v2's native OS drag-and-drop (`getCurrentWebview().onDragDropEvent`,
confirmed against the installed `node_modules/@tauri-apps/api/webview.d.ts`
rather than assumed from memory — its `DragDropEvent` union is
`{type:'enter'|'over'|'drop', paths, position} | {type:'leave'}`), kept
behind the same "only `bridgeClient.ts` imports `@tauri-apps/api`" rule the
file's own header comment already states. `ImportScreen.svelte` listens in
`onMount`/unlistens in `onDestroy`, reuses the exact same `inspect(path)`
function the existing file/folder pickers already call (so drag-drop gets
every validation/preview/warning path the picker flow already has, free),
shows a dashed drop-target highlight only while a drag is over the initial
picker screen (not during import-review, where a drop is intentionally
ignored — dropping a second source while reviewing the first would be
confusing, matching how the existing "Choose another source" flow requires
an explicit reset first), and rejects a multi-path drop with a clear error
instead of silently importing only the first path and discarding the rest.
No Rust/`tauri.conf.json` change was needed — Tauri v2's window-level
`dragDropEnabled` defaults to `true` and nothing in `tauri.conf.json` turns
it off (checked, not assumed). **Not click-tested in a running Tauri
window** in this session (same build constraint as above) — `npm run
check`/`npm run build` verify the code compiles and type-checks, not that
a real OS-level file drag actually reaches the webview and imports
correctly; worth a real click-through (drag a `.usfm` file, then a
multi-book folder, onto the window) before treating this as fully verified.

### Paratext/Logos connectors and AI explain — Phase 7 continued (2026-08-24)

Picked up immediately after part 1 above, in the same session, on the user's
explicit "go ahead on all counts on a best-effort basis" instruction — with
the honest caveat given back at the time: two of these three slices
fundamentally need a live external application (Paratext, Logos) this
machine either didn't have running or didn't have installed at all, so
"best effort" here means real, compiling, protocol-correct code that has
never been exercised against the real external app. Every such gap is
flagged explicitly below and in the new files' own README/header comments —
never silently presented as more verified than it is.

**translationWordsLinks resource-layout bug — found and fixed first, because
AI explain depends on it.** `knowledge_base.py`'s `twl_occurrences()` reads
`translationWordsLinks/<version>/{kt,names,other}/groups/<book>/<term>.json`
— a *resource-level* layout, keyed by category and term — but
`resource_materializer.materialize_translation_words()` only ever wrote the
*project-level* check-index shape
(`.apps/translationCore/index/translationWords/<book>/<group>.json`),
confirmed by reading both functions directly, not assumed from the earlier
research breadcrumb. Fixed with a new function,
`materialize_translation_words_links_index()`, that parses the exact same
bundled `twl_<BOOK>.tsv` a second time (no shared-parsing risk with the
already-tested project-level writer) into the resource-level shape,
called from `materialize_book_checks()` alongside the two existing
materializers. Verified with 3 new tests, including one that calls
`TranslationHelpsKnowledgeBase.twl_occurrences()` directly and confirms it
now returns real data — not just "the files got written."

**translationAcademy — bundled for the first time, and its own real reading
bug found and fixed.** Two real gaps stacked here, found only by actually
downloading and inspecting the content, the same way every other resource
gap in this project has been found:

1. `ensure_resources_installed()`'s resource list was
   `('translationNotes', 'translationWordsLinks', 'translationWords')` —
   `translationAcademy` was never in it, so even after bundling real content
   under `engine/resources/`, nothing would ever copy it into application
   storage. Fixed by adding it to that tuple.
2. The real content itself: downloaded the actual
   `git.door43.org/unfoldingWord/en_ta` repository at tag `v90` (the same
   tag already used for tN/TW/TWL — confirmed to exist via that Gitea
   instance's own API, not GitHub; `unfoldingWord/en_ta` and
   `unfoldingWord/en_tn` both 404 on api.github.com, confirming this
   content was never on GitHub proper for either resource, and the earlier
   P0 bundling pass's own "Door43" references meant the Gitea instance all
   along) — a real 2.2MB, 728-file, CC BY-SA 4.0 archive, not a synthetic
   fixture. Extracting and inspecting it directly showed
   `knowledge_base.py`'s `_find_article()`/`global_checking_evidence()` were
   both written for a **flat** `"<identifier>.md"` file shape — correct for
   translationWords (confirmed by the earlier, already-passing P0
   acceptance tests) but wrong for translationAcademy, whose real articles
   are **directories** (`checking/accuracy-check/{title.md, sub-title.md,
   01.md}`). This is the same bug *class* every vendored/bundled
   integration in this project has hit — an assumption written before real
   content existed to check it against — just newly found in
   `knowledge_base.py` instead of a vendored tool. Fixed with a dedicated
   `_find_ta_article_dirs()` (left `_find_article()` itself untouched, since
   translationWords' flat-file use of it is correct and already tested) plus
   updated `ta_articles()`/`global_checking_evidence()` to read `01.md` for
   body content and `title.md` for a real human-readable title instead of
   using the raw `"01"` filename stem. Verified with 4 new tests against the
   real downloaded content, including all 13 of `global_checking_evidence()`'s
   hardcoded checking-category identifiers confirmed as real, existing
   slugs (not guessed).

**`ai.explain` — new protocol method wiring `ai_client.OpenAIResponsesClient
.prepare_verse_review()`**, itself real, complete, already-implemented code
from Phases 1-3 that had zero protocol wiring and zero test coverage before
this pass (same shape of gap as `alignment.aiPropose`'s scaffolding in part
1 above). Read-only — nothing is written to project files; the human
reviewer sees AI's evidence-backed check-review preparation and whole-verse
QA as something to confirm or reject, same "AI says what it may mean, human
decides" boundary as everywhere else in Bridge. Verified with 2 tests using
the same fake-transport injection seam as `alignment.aiPropose`, against
*real* materialized translationNotes/translationWords evidence (a real
import + `verse.runChecks` preflight, not synthetic fixtures) — the fake AI
response's `check_reviews` are built from checkIds discovered from the real
project data, not guessed, so the test genuinely exercises
`prepare_verse_review`'s "every supplied check must come back or the model
response is rejected" validation. `bridge-engine.spec` needed no new
`datas`/`hiddenimports` entries (this reuses the `resources` tree, now
including translationAcademy, already bundled wholesale). Frozen build
verified via an extended `scripts/smoke_sidecars.py`: since that fixture
project has no application-storage `resources/` folder of its own, `ai
.explain` legitimately hits `knowledge_base_error` before `ai_client.py`'s
own missing-API-key check ever runs — the smoke test accepts either clean
error code, since both prove the bundle (imports, and now translationAcademy
data) is genuinely intact rather than crashing.

**Frontend**: `ReviewPanel.svelte` gained a "🤖 Explain with AI" button and
a results section (summary, per-check verdict/rationale/suggested
correction, whole-verse QA issues) — deliberately no new evidence-browser
UI; check reviews and issues render with the same finding-card visual
language the rest of the panel already uses. `bridgeClient.ts` gained
`aiExplainVerse()`; `types/finding.ts` gained `AiExplainResult`/
`AiCheckReview`/`AiQaIssue` (documented inline with the same snake_case
wire-shape rationale as `AlignmentAiProposal` in part 1 — these mirror
`AICheckReview.to_dict()`/`QAIssue.to_dict()`'s real Python output verbatim,
since it's read-only display data with no round-trip requirement to get
"wrong" the way the alignment proposal has, but declaring the true shape
still beats a silently-incorrect camelCase guess).

**Paratext companion plugin — the real, previously-missing artifact now
exists.** `paratext_connector.py`'s `ParatextConnectorClient` only ever
talked to a companion plugin over a named pipe
(`\\.\pipe\translationCoreAIBridge`) that did not exist anywhere in this
repo; building one had been flagged as "a different technology stack, a
genuinely separate undertaking" by the investigation earlier this session.
It turned out to be more tractable than that framing suggested, once
actually investigated rather than assumed:

- The real Paratext plugin interface DLLs
  (`PluginInterfaces.dll`/`CorePluginInterfaces.dll`/
  `EmbeddedUiPluginInterfaces.dll`) are already installed locally at
  `C:\Program Files\Paratext 9`. Reflecting into them directly (PowerShell's
  `[System.Reflection.Assembly]::LoadFrom` + `GetTypes()`/`GetMethods()`,
  not documentation) gave the real interface surface: `IPluginHost
  .add_VerseRefChanged(ReferenceChangedHandler)`,
  `SetReferenceForSyncGroup(IVerseRef, SyncReferenceGroup)`, `IVerseRef`,
  `IParatextChildState`, `IProject`/`IReadOnlyProject`, and
  `ParatextInternal.IParatextPlugin` (the real, if oddly-namespaced, base
  interface every plugin implements) — everything
  `tc_ai_bridge/navigation.py`'s `NavigationBroker` design already needs.
- No Visual Studio, modern .NET SDK, or NuGet install was needed: the C#
  compiler bundled with Windows' own .NET Framework
  (`csc.exe`) compiles directly against those installed DLLs plus
  `System.Web.Extensions.dll` (bundled, provides `JavaScriptSerializer` for
  JSON with no external dependency) and `netstandard.dll` (also already
  present — the plugin interfaces are themselves built against
  netstandard2.0, discovered from the compiler's own first-attempt `CS0012`
  errors, not assumed).
- The real plugin *deployment* mechanism was confirmed from Paratext's own
  official demo-plugins wiki (`ubsicap/paratext_demo_plugins`, fetched
  directly via `raw.githubusercontent.com`, not assumed): a compiled DLL
  renamed to `.ptxplg`, copied into
  `C:\Program Files\Paratext 9\plugins\{PluginFolder}\` while Paratext is
  closed. No marketplace registration or signing is required for local
  development use.
- New code: `paratext_plugin/TranslationCoreAIBridgePlugin.cs` — implements
  `get_state` (reads the active window's verse reference/project/sync
  group) and `set_reference` (calls `SetReferenceForSyncGroup`) over the
  exact newline-delimited JSON protocol `paratext_connector.py`'s
  `_exchange()` already speaks. **`create_note` is deliberately NOT
  implemented** — it returns a clear "not implemented" error — because
  Bridge already has a complete, working Paratext Notes 1.1 XML writer
  (`tc_ai_bridge/paratext_notes.py`) that writes notes directly to disk
  without needing the plugin at all; a live `AddNote()` call would need an
  `IWriteLock`/`IScriptureTextSelection`/`CommentParagraph` this session had
  no way to construct or verify against a real running Paratext instance.
- **What's verified**: the plugin compiles cleanly (`paratext_plugin
  /build.ps1`) against the real reflected interfaces. **What's not**:
  it has never been loaded by a running Paratext instance. Deploying it
  requires writing into `C:\Program Files\Paratext 9\plugins\...`, a
  protected system directory — this session's own safety controls correctly
  blocked that write rather than silently proceeding. See
  `paratext_plugin/README.md` for the exact remaining steps (close
  Paratext, run `build.ps1 -Deploy` elevated, check
  `%LOCALAPPDATA%\Paratext95\ParatextLog.log` for the plugin loading).

**Logos bridge script — the real, previously-missing artifact now exists,
with its own genuinely-tested process wiring.**
`logos_connector.py`'s `LogosConnectorClient` spawns
`logos_connector/logos_bridge.ps1` as a persistent `-STA` PowerShell helper
and talks to it over its own stdin/stdout — that script did not exist
anywhere in the repo before this pass, and Logos is not installed on this
machine (the user is installing it; a colleague with a working Logos
install will do the real functional testing).

- The real COM API surface (type library `Logos4Lib`, GUID
  `{81490292-5570-4D02-A2AC-7B828DBD0A8A}`; `new LogosLauncher().Application`;
  `LogosApplication.ApiVersion/.Activate()/.Exit()/.ExecuteUri()/
  .CreateNavigationRequest()/.Navigate(request)/.DataTypes.LoadReference()/
  .GetDataType()`; `PanelActivated`/`PanelChanged`/`PanelOpened`/
  `PanelClosed`/`Exiting` events) was pulled from `LogosBible
  /Logos4ComApiDemo`'s actual `.cs`/`.csproj` source, fetched directly from
  `raw.githubusercontent.com` (the wiki page itself 403'd), the same
  standard this project applies to every other integration.
- **Two things are genuinely unverified and flagged inline in the script's
  own header** (the most likely things to need a real fix once tested
  against live Logos): the exact COM ProgID string
  (`"Logos4Lib.LogosLauncher"` follows the standard `tlbimp` naming
  convention but was never seen registered for real — `Get-LogosLauncher`
  searches the registry for a plausible alternative and reports it if the
  literal string fails), and reading the *currently active panel's*
  reference (`Get-CurrentReferenceInfo`'s `$app.ActivePanel` guess) — the
  official demo only shows *pushing* a reference via `Navigate()`, never
  reading one back, so there was no real source to confirm this against.
  Both paths are wrapped in defensive `try`/`catch` so a wrong guess
  degrades to an empty/error response rather than crashing the helper.
- No live COM event push is attempted — a plain PowerShell script has no
  message loop to reliably pump COM callbacks, and `navigation.py`'s
  `NavigationBroker` is already designed around a *polling* connector
  (its echo-suppression/settling-window logic exists specifically to make
  repeated polling safe), so a poll-only helper matches the existing
  design rather than falling short of it.
- **What's genuinely verified, real subprocess-level testing, not just
  syntax-checking**: `engine/tests/connectors/test_logos_connector.py` (4 tests) proves
  `LogosConnectorClient` actually spawns this exact script in `-STA` mode,
  exchanges real newline-delimited JSON, and a real "Logos isn't installed"
  COM failure round-trips as a clean `LogosConnectorError` — not a hang, not
  a malformed-response error. A real bug was found and fixed this way: the
  first draft double-printed the `close` action's response (an inline
  `WriteLine` inside the request-dispatch `switch` *and* the general
  response-write path after it) — caught by actually running the script
  with real stdin input, not by reading the code.

**Python-side connector protocol wiring — deliberately scoped to direct
pass-through, not full automatic live sync.** New methods:
`paratext.getState`/`paratext.setReference`,
`logos.getState`/`logos.setReference`. `BridgeEngine` caches one
`LogosConnectorClient` instance per process (unlike Paratext's stateless
per-call named-pipe open, spawning a fresh `-STA` PowerShell process on
every poll would be far too slow — real measured cold-start well over a
second), registered with `atexit` for best-effort clean shutdown (a known,
documented limitation: `atexit` does not run on a hard kill, e.g. Tauri
force-terminating the sidecar — see `logos_connector/README.md`). This
deliberately does **not** wire `navigation.py`'s
`NavigationBroker`/`NavigationOwnership` into an automatic background
polling loop yet — that's a real, separate UX design (conflict handling, a
background job, a live-sync toggle) worth its own pass once these two
connectors have been proven against real running Paratext/Logos instances.
What's here is already useful on its own: a future "Connections" panel can
show live state and let a reviewer manually push Bridge's current verse
into either application. Verified with 4 protocol-level tests (clean error
codes for both connectors with no companion running) plus the real Logos
subprocess tests above, run again through the full `BridgeEngine
.handle_request` dispatch this time. Frozen build verified via
`scripts/smoke_sidecars.py`: `paratext.getState` fails cleanly with no
companion plugin, and `logos.getState` genuinely spawns the bundled
`logos_bridge.ps1` from under `sys._MEIPASS` (confirming a new
`bridge-engine.spec` `datas` entry — `logos_connector/`, invisible to
PyInstaller's static analysis the same way every other runtime-resolved
vendor/helper path in this project has been) and gets the real "Logos isn't
installed" COM error, not a missing-file/spawn error.

**Full source suite after this continuation**: see `docs/QA_TEST_MATRIX.md`'s
A20-A26 rows for the exact test counts and what each verifies. `npm run
check` (0 errors/0 warnings) and `npm run build` both still pass. `cargo
check` succeeds with the six new Tauri commands
(`ai_explain`/`paratext_get_state`/`paratext_set_reference`/
`logos_get_state`/`logos_set_reference`, plus the two from part 1)
registered and per-method sidecar timeouts tuned (`ai.explain` needs up to
two sequential real model calls; `logos.*` needs headroom for the helper's
slow cold start).

**Not done in this pass**: no click-through in a running Tauri window for
any of this (same build constraint as part 1 — no interactive window
automation available in this environment, confirmed by two failed
standard Windows foreground-focus tricks earlier in the session). The
Paratext plugin has never been loaded by real Paratext. The Logos bridge
script has never made a real COM call. No "Connections" panel UI exists yet
for either connector — only the protocol methods and (for Logos) the fake
`get_state`/`set_reference` failure paths are exercised. `mode: "audit"`
for `alignment.aiPropose` remains backend-only with no UI. AI usage totals
still accumulate in `settings.json` but still aren't surfaced anywhere in
`SettingsModal.svelte`.

Continue building Bridge's import workflow so users can bring in individual
USFM/SFM files, whole-Bible folders, Paratext folders, and translationCore
projects, then use the normalized data for local QA, Greek Room,
translationNotes, translationWords, and word alignment.

The import foundation described below is implemented and verified. It has since
been committed as `9ed60cb feat(import): add translationCore-compatible Scripture
imports` — the working tree is currently clean. The "Working-tree warning" section
near the end of this document is kept for historical reference (it describes what
that commit touched) but no longer describes an uncommitted state.

## What is implemented

### Backend normalization

`engine/tc_ai_bridge/project_import.py` provides two public entry points:

- `inspect_import(source_path)`: read-only detection and metadata preview.
- `import_source(source_path, destination_root, metadata)`: staged,
  non-overwriting import and normalization.

Accepted input:

- `.usfm` and `.sfm` files.
- Marker-based `.txt` Scripture files.
- Folders containing one or many Scripture books.
- Paratext-style folders with `Settings.xml`.
- Existing translationCore project folders.
- `.tcore`, `.tstudio`, and ZIP project archives.

For raw Scripture, each book becomes a translationCore-compatible book project:

```text
<project>/
  manifest.json
  <book>.usfm
  <book>/headers.json
  <book>/<chapter>.json
  .apps/translationCore/alignmentData/<book>/<chapter>.json
  .apps/translationCore/index/translationNotes/<book>/
  .apps/translationCore/index/translationWords/<book>/
  .apps/translationCore/checkData/...
  .bridge/import.json
```

Important behavior:

- Original source bytes are preserved.
- `.bridge/import.json` records SHA-256 provenance and capability status.
- Existing translationCore indexes, decisions, comments, and alignments are
  copied intact.
- Older tC/tS projects with target chapter JSON but no alignment data receive
  unaligned target word banks so `TranslationCoreProject` can open them.
- Each unaligned target token is represented once in `wordBank`, with correct
  `occurrence` and `occurrences` values.
- Nested USFM 3 `zaln`/`w` alignment milestones are converted into occurrence-aware
  translationCore 1:1, 1:many, many:1, and many:many groups.
- Malformed alignment structures are not guessed; target words remain in
  `wordBank` for review.
- Multi-book folders produce one compatible project entry per book. All source
  files are copied immediately, the first book is normalized and opened, and
  remaining books carry `.bridge/lazy-import.json` until first open. Every
  sibling has `.bridge/collection.json`, so the full selector survives restart.
- ZIP entries are validated against path traversal before extraction.
- Imports use private staging and collision suffixes instead of deleting or
  overwriting existing projects.

### Sidecar protocol

`engine/bridge_service.py` adds:

- `project.inspectImport`
- `project.import`

The project response now also supplies confirmed `targetLanguageId`,
`targetLanguageDirection`, `projectName`, and `bibleName`.

Greek Room now receives the confirmed language identifier from
`manifest.target_language.id`, rather than the display-language name. Local QA
already gets its language context from the manifest via `PluginRegistry`.

USFM verse bridges/segments such as `3-4` and `3a` no longer crash local finding
conversion. Findings use the first numeric component as their numeric anchor,
while project navigation retains the exact verse string.

### Tauri and frontend

The Tauri layer adds:

- Native import-file picker with USFM/SFM/TXT/TCORE/TSTUDIO/ZIP filters.
- Thin commands for inspection and import.
- A five-minute sidecar safety timeout remains for import, but whole-Bible raw
  import no longer relies on it: the real 66-book OV Tamil source measured
  5.17 seconds from source and 6.21 seconds through the frozen packaged sidecar
  after lazy book normalization was introduced.

`src/lib/components/ImportScreen.svelte` now provides:

- Separate file import, folder import, and open-existing-project actions.
- Read-only preview before import.
- Detected-book list, verse counts, alignment status, and warnings.
- Required Language, Project name, Bible/translation name, and text direction.
- Offline searchable ISO 639-3 catalog using `iso-639-3@3.0.1`.
- Import is disabled until the required metadata is valid.

The complete catalog adds about 94 KB gzip to the production bundle and causes
Vite's non-fatal 500 KB chunk warning. It can be split later if startup size
becomes a concern.

## Critical design boundary: tN/tW are not fabricated

Raw USFM contains Scripture, not translationNotes or translationWords checks.
translationCore first imports Scripture and later materializes tool indexes from
installed, versioned checking resources. Bridge follows that boundary.

Current behavior:

- Imported existing translationCore projects immediately expose any real tN/tW
  indexes they already contain.
- Raw imports record `requires-resource-index` for translationNotes and
  translationWords until the first background-check preflight for that book.
- Compatible index directories are created, but no fake/empty check entries are
  generated.
- Local Scripture QA, Greek Room, and word-alignment preparation work now.
- Bundled-resource acquisition and real per-book tN/tW materialization are
  implemented; online resource/version selection is still future work.

## Recommended next work

### P0 — Resource acquisition and tN/tW index materialization (done 2026-08-20)

**Approach taken:** bundle a pinned English tN/tW/TWL snapshot in the repo
(matching real translationCore's own default of shipping English checking
helps in the installer) rather than fetching on demand — Bridge's own
premise is field translation teams with unreliable connectivity, so a raw
import must produce real tN/tW checks with zero network access.

What's bundled, under `engine/resources/en/translationHelps/` (~42 MB,
committed):

- `translationNotes/v90_unfoldingWord/tn_<BOOK>.tsv` — raw Door43
  `unfoldingWord/en_tn` tag v90, 56 of 66 books.
- `translationWordsLinks/v90_unfoldingWord/twl_<BOOK>.tsv` — raw
  `unfoldingWord/en_twl` tag v90, same 56 books.
- `translationWords/v90_unfoldingWord/bible/{kt,names,other}/*.md` — raw
  `unfoldingWord/en_tw` tag v90 articles (953 files), plus each resource's
  own `manifest.yaml`/`LICENSE.md` for provenance and attribution.
- **10 Old Testament books are not currently in the upstream release**
  (Numbers, 1-2 Chronicles, Ecclesiastes, Isaiah, Jeremiah, Ezekiel, Daniel,
  Amos, Zechariah) — verified directly against the live Door43 catalog on
  2026-08-20, not a gap in this bundling pass. Re-run the same fetch against
  a newer tag later to pick up whichever of these ship next; nothing else
  needs to change.

**New code:**

- `engine/tc_ai_bridge/resource_materializer.py` — pure parser/writer.
  `ensure_resources_installed(app_resources_root)` copies the bundled
  snapshot into application-owned storage once (mirrors how `project_root`
  itself is a copy separate from the repo — `TranslationHelpsKnowledgeBase`
  resolves resources relative to a project's own path, i.e.
  `settings_root/resources/...`, never the repo). `materialize_book_checks()`
  parses the bundled TN/TWL TSVs for one book and writes real
  `.apps/translationCore/index/{translationNotes|translationWords}/<book>/<group>.json`
  entries with a full `contextId` (`reference`, `tool`, `groupId` — the TA
  slug for tN, the term slug for tW — `checkId`, `quoteString`,
  `occurrenceNote`). Always fully regenerates those files: safe, because
  Bridge's own Accept/Reject/Ignore decisions live in the project's separate
  `decisions/` companion directory (stable finding ids) and are re-applied
  onto findings after checks run — never stored inside these index files.
- `project_import.py` gained `apply_resource_materialization()`, which pins
  `tc_en_check_version_translationNotes`/`...translationWords` in
  `manifest.json` and records real `ready`/`unavailable` capability status
  (never a fabricated `ready` with zero checks) in `.bridge/import.json`.
- `bridge_service.py` prepares these indexes in the background checking
  preflight, and **only for raw USFM/SFM/Paratext imports**. Import no longer
  blocks on every book's tN/tW data. An imported existing translationCore/
  translationStudio project keeps its own real indexes untouched, per the
  tN/tW design boundary in `docs/IMPORTS.md`. tN/tW are gateway-language
  (English) checking helps applied to any target-language translation, so this
  doesn't depend on the imported project's target language.

**Verified:** `test_resource_materializer.py` (5 tests, all against the real
bundled Titus TN/TWL slice, not a synthetic fixture) — parses into the
correct `contextId` shape, is idempotent, correctly reports `unavailable`
for an unreleased book (tested with Isaiah), and an end-to-end
import→`verse.runChecks` call surfaces real `translation_note`/
`translation_word` findings. Historical suite after lazy whole-Bible import: 79/79
passing (2026-08-21).

**Still open / not done in this pass:**

- **`knowledge_base.py`'s own TWL reader is still unfed.** It expects a
  *different*, pre-materialized layout —
  `translationWordsLinks/<version>/{kt,names,other}/groups/<book>/<term>.json`
  — used only by `ai_client.py`'s evidence-gathering (the unwired Phase 7
  `ai.explain`). This pass bundled the raw TWL TSVs and materializes
  *project-level* check indexes from them directly; it does not also build
  that second, resource-level grouped-JSON shape. Needed before Phase 7 can
  gather TW evidence, not needed for `verse.runChecks`.
- **`translationAcademy` was not bundled.** Nothing on the `verse.runChecks`
  path reads it today (`local_checks.py` never imports `knowledge_base.py`);
  it's only needed for TA article evidence in the same unwired `ai.explain`
  path. `knowledge_base.py.resolve('translationAcademy')` will raise
  `KnowledgeBaseError` if something calls it before this exists — currently
  nothing on any wired protocol method does.
- **PyInstaller build needs `--add-data` to ship the bundle**, and should
  now be run from the `engine/.venv` Python 3.12 environment (see the real
  Wildebeest section above), e.g. (from `engine/`, Windows path separator
  is `;`):
  `.venv\Scripts\python.exe -m PyInstaller --onefile --name bridge-engine --add-data "resources;resources" main.py`.
  Without `--add-data`, a frozen `.exe` has no bundled tN/tW snapshot and
  every raw import falls back to `unavailable` on a machine where
  `ensure_resources_installed` has never run before (dev-mode runs are
  unaffected since `bundled_resources_source()` reads straight from
  `engine/resources`). If the real Wildebeest engine (`wildebeest-nlp`,
  installed via the `wildebeest` extra) should be in the build, also add
  `--collect-data wildebeest` — unverified insurance for its `data/`
  directory, see the real Wildebeest section above. This was not verified
  against an actual PyInstaller build in this session — no sidecar binary
  was built. Confirm the frozen `.exe` actually finds the resource bundle
  via `sys._MEIPASS`, and that Greek Room findings still say
  `usingRealEngine: true` in `engine.info`, before shipping.
- **Only English is bundled.** Non-English tN/tW (or a refreshed English
  version) still needs the online path described in the original P0 below —
  not built in this pass, since it wasn't required to satisfy the
  acceptance criteria against real data.

Original acceptance criteria (all met):

- A raw USFM import followed by resource preparation produces non-zero real
  tN/tW checks for known verses. ✓
- `verse.runChecks` returns those items as translation-note/translation-word
  findings. ✓
- Resource versions and hashes are visible in provenance (pinned
  `tc_en_check_version_*` fields; `knowledge_base.py`'s existing
  `provenance_manifest()` hashes each bundled `manifest.yaml`). ✓
- Re-running indexing is deterministic and does not erase human decisions. ✓

### P0 — Multi-book collection navigation (done 2026-08-20)

`TopBar.svelte`'s book `<select>` was previously a dead placeholder — one
hardcoded option, no `on:change`. It now lists every sibling from
`ProjectInfo.importedProjects` and calls `project.open` on selection.

Implementation notes for whoever touches this next:

- `project.open` does **not** echo back `importedProjects` (only
  `project.import` does — see `bridge_service.py`'s `_project_info()` vs
  `import_project()`). The sibling list is therefore carried forward on the
  frontend across a switch (`App.svelte`'s `switchBook()`) rather than
  re-fetched from the backend. If the app is closed and reopened, or a book is
  opened individually via "Open an existing project," the sibling list is
  gone and the selector falls back to showing just that one book — this is
  accepted as in-session-only, matching the P0 scope (switching immediately
  after import).
- `stores.ts` gained `resetBookState()`, called before switching: chapter and
  verse numbers restart at 1 in every book, so `chapterVerseNums`,
  `verseTexts`, `findingsByVerse`, and `loadedChapters` must be cleared on
  switch or the new book would show the old book's data under matching
  chapter/verse keys — the same class of bug gotcha #7 was written to avoid,
  just at the book level instead of the chapter level.
- `App.svelte` factored the "land on first chapter, load it, select first
  verse" sequence out of `handleOpened()` into `enterCurrentProject()`, shared
  by both initial open and `switchBook()`.
- Verified: `npm run check` (0 errors), `npm run build` (succeeds, same
  pre-existing chunk-size warning as before). Not exercised in a running
  Tauri window in this session — no sidecar binary was built/available to
  launch `npm run tauri dev` end-to-end. Whoever picks this up next should
  do a real click-through (import a multi-book folder, switch between books,
  confirm chapter/verse state doesn't bleed across books) before treating
  this as fully verified.

### P1 — Maintained full USFM parser

The current parser is conservative and the original source is always preserved,
but normalized extraction still uses regular expressions. Replace or augment it
with a maintained USFM parser for full marker placement, verse bridges/segments,
tables, peripheral material, and project validation. Nested alignment milestones
are supported by the current targeted parser and source-template export; retain
both behaviors during that migration.

### P1 — Direct Paratext import

Local Paratext folders are detected and `Settings.xml` is used for metadata.
Direct Scripture retrieval from Paratext/API is not wired. Keep that separate
from the existing note connector and require explicit project selection.

### P1 — Import reporting and recovery

Add an import-results screen showing all created project paths, warnings,
unaligned milestone counts, resource-index status, and a way to open any book.
For a multi-book failure, either make the whole collection atomic or clearly
report which book projects completed.

## Verification completed

From `engine/`:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m pytest tests/ greek_room_engine/tests/ -q -p no:cacheprovider
Remove-Item Env:PYTHONDONTWRITEBYTECODE
```

Result: `32 passed`.

From the repository root:

```powershell
npm run check
npm run build
```

Results:

- Svelte check: 0 errors and 0 warnings.
- Production build: successful, with only the language-catalog chunk-size
  warning described above.

From `src-tauri/`:

```powershell
cargo check
```

Result: successful.

`git diff --check` also passes; only Windows LF-to-CRLF notices are printed.

`npm install` reports seven dependency advisories (six moderate and one high).
No automatic `npm audit fix --force` was run because it can introduce breaking
dependency changes. Audit and upgrade these separately.

## Tests added

`engine/tests/project_io/test_project_import.py` covers:

- Read-only USFM preview and missing-language detection.
- Raw SFM normalization and provenance.
- Multi-book folder import.
- Basic USFM 3 alignment preservation.
- Existing tC archive check-index preservation.
- ZIP path-traversal rejection.
- End-to-end sidecar import and automatic project opening.
- Verse-bridge import and checking.

## Upstream translationCore research

The implementation was compared against:

- https://github.com/unfoldingWord/translationCore/blob/develop/src/js/actions/Import/LocalImportWorkflowActions.js
- https://github.com/unfoldingWord/translationCore/blob/develop/src/js/helpers/FileConversionHelpers/UsfmFileConversionHelpers.js
- https://github.com/unfoldingWord/translationCore/blob/develop/src/js/helpers/FileConversionHelpers/ZipFileConversionHelpers.js
- https://github.com/unfoldingWord/translationCore/blob/develop/src/js/helpers/manifestHelpers.js
- https://github.com/unfoldingWord/translationCore/blob/develop/src/js/helpers/ProjectValidation/ProjectStructureValidationHelpers.js

Key upstream behavior confirmed:

- tC accepts USFM/SFM/TXT and TCORE/TSTUDIO files through its local file picker.
- USFM import generates a manifest, copies the source, and creates target chapter
  JSON.
- Alignment data is created when alignment milestones are present.
- Missing project/language details are handled during validation.
- Upstream translationCore rejects multiple-book projects; Bridge deliberately
  imports them as a collection of book-wise projects instead.

## Working-tree warning (historical — now committed as `9ed60cb`)

At the time this document was originally written the working tree was uncommitted;
it has since been committed. Files involved in this import work were:

- `engine/tc_ai_bridge/project_import.py` (new)
- `engine/tests/project_io/test_project_import.py` (new)
- `engine/bridge_service.py`
- `src/lib/components/ImportScreen.svelte`
- `src/lib/api/bridgeClient.ts`
- `src/lib/types/finding.ts`
- `src-tauri/src/commands.rs`
- `src-tauri/src/main.rs`
- `src-tauri/src/sidecar.rs`
- `package.json`
- `package-lock.json`
- `docs/IMPORTS.md` (new)
- `README.md`

`src-tauri/Cargo.toml` and `vite.config.ts` were also modified in the broader
working session and are part of the same commit.

## Alignment word-info lexicon popup (2026-08-26)

**What it does:** clicking the small "i" button on a source (Hebrew/Greek)
token in the Word Alignment modal opens a popup with that word's decoded
morphology and a Strong's dictionary gloss — lemma, transliteration,
pronunciation, Meaning, Usage, and Source (etymology) — the same information
translationCore's own word-details popup shows. Compound tokens (a lexeme
fused with a Hebrew proclitic prefix, e.g. the "the" in "the earth") show one
popup with a segment per morpheme rather than needing any change to how
tokens are split.

### Data source: `openscriptures/strongs`, not UHAL/UGL

The user's reference screenshot's exact phrasing ("Meaning: the 'earth'
(at large...)", "Usage: × common, country...", "Source: from an unused
root...") turned out to be verbatim classic Strong's Dictionary text, not
unfoldingWord's UHAL/UGL (which write numbered-sense prose entries — a
different shape entirely). The real match, confirmed by cloning the repo and
reading the actual data rather than trusting a description of it (same rule
as every other vendored dependency in this project): `openscriptures/strongs`
(github.com/openscriptures/strongs, commit `0acd2f251c2d35ff8db2dece4e0593979d3ac223`).
Its `hebrew/strongs-hebrew-dictionary.js` and `greek/strongs-greek-dictionary.js`
are plain CommonJS modules exporting a Strong's-number-keyed object —
`require()`-able directly from the vendoring script, no XML parsing needed.
8,674 Hebrew + 5,523 Greek entries. License: each file's own header states
"Copyright 2009/2010, Open Scriptures. CC-BY-SA" (no version number given);
the underlying 1890s Strong's Concordance text itself is public domain.

New vendor script: `scripts/vendor-strongs-lexicon.mjs`, same shape as
`vendor-original-language-resources.mjs` — takes `--checkout <path>`, verifies
the pinned commit, emits one gzipped JSON index per language under
`engine/resources/{hbo,el-x-koine}/lexicons/strongs/v1.0.2_openscriptures/`
with the same `NOTICE.md`/`PROVENANCE.json`/`index.json` convention as the
UHB/UGNT packs.

### Real data quirks found (not assumed) while wiring up lookups

- Hebrew Strong's numbers on tokens are inconsistently zero-padded (`H0430`
  vs `H7225`) and sometimes carry an OSHB-only trailing homonym letter not
  present in classic Strong's numbering (`H1254a` for ברא in Genesis 1:1).
- Greek (UGNT) Strong's numbers always carry one extra trailing "variant"
  digit beyond the 4-digit base that classic Strong's numbering doesn't have
  (`G23160` → base `G2316`).
- Compound tokens use a colon-joined `strong`/`morph` pair per morpheme
  (e.g. strong `"d:H0776"`, morph `"He,Td:Ncbsa"` for "the earth") — the
  prefix side (`b`/`c`/`d`/`k`/`l`/`m`) has no Strong's number of its own.
  Confirmed which letter means what by cross-checking ~30 real verses
  (Genesis 1-2, Psalm 119, Deuteronomy 6) against their paired morph codes,
  not guessed — see `HEBREW_PREFIX_LABELS` in `lexicon_resources.py`.

All three are handled by `_normalize_strong()`/compound-splitting in
`engine/tc_ai_bridge/lexicon_resources.py` before the dictionary lookup.

### Morphology decoding

`engine/tc_ai_bridge/morphology_codes.py` decodes the raw codes already
present on every source token into readable labels:

- **Hebrew** (`He,Ncmsa` etc.): the OpenScriptures Hebrew Bible (OSHB)
  parsing scheme, doc-verified against
  `github.com/openscriptures/morphhb/blob/master/parsing/HebrewMorphologyCodes.html`.
- **Greek** (`Gr,V,IAA3,,S,` etc.): **no locatable specification document** —
  repeated lookups against door43/GitHub for UGNT's own morphology docs found
  nothing. The mapping was instead reverse-engineered by cross-checking every
  real UGNT token in John 3:16 and Titus 1:1 (both unambiguous, well-known
  grammar) against their known parsing, field position by field position.
  Only codes actually confirmed that way are mapped; anything unrecognized
  falls back to showing the raw code rather than guessing — see the module's
  own docstring for the full reasoning.

### New protocol method: `lexicon.getEntry`

`Methods.LEXICON_GET_ENTRY = "lexicon.getEntry"` (`bridge_service.py`), takes
`{strong, morph}` off the `AlignmentToken` the frontend already has — no
`languageId` param needed; `decode_morph()` reads the `"He,"`/`"Gr,"` prefix
itself. Returns `{languageId, segments: [...]}`, one segment per
colon-separated morpheme, each with `strong`/`morphLabel`/`partOfSpeech`/
`lemma`/`translit`/`pron`/`meaning`/`usage`/`source` (all nullable — a
segment with no lexicon hit still gets its decoded morphology, and non-numeric
Hebrew prefixes get a label from `HEBREW_PREFIX_LABELS` instead). Wired
straight through: `commands.rs`'s `lexicon_get_entry` → `main.rs` invoke list
→ `bridgeClient.ts`'s `getLexiconEntry()` → new `LexiconPopup.svelte`.

### Two real bugs found and fixed in the same pass

**1. A genuine regression, caught from the user's live bug report, not from
a test.** The first version of this work also added a copy step to
`resource_materializer.py`'s `ensure_resources_installed()`, mirroring the
existing UHB/UGNT copy loop, to make the new lexicon's license/provenance
discoverable in app storage. After shipping it, opening a KJV Genesis project
threw `sidecar request 'checks.status' timed out` and `check.listForVerse`
timed out too. Root cause, traced end to end (not guessed):
`materialize_book_checks()` — which parses Genesis's entire English tN/tW
TSVs (5,758 + 5,640 checks, 442 file writes) — already ran synchronously
inside a `with self._checker_lock:` block on a background thread, and was
**already** taking ~15-19s cold (confirmed by `git stash`-ing just the
`resource_materializer.py` change and re-timing: 18.9s baseline vs. 26.2s
with the added copy step). `checks.status`/`check.listForVerse` are
hard-coded to a 30s timeout specifically so they *never* wait on that lock
(see `sidecar.rs`'s `interactive_check_requests_keep_the_short_timeout`
test and `list_checks_for_verse()`'s non-blocking lock acquire) — but with
one Python process and the GIL, sustained CPU/IO-heavy work on the
background job thread can still starve the main stdio dispatcher's ability
to read and answer the next request in time. The added copy step (~7s more)
was enough to tip an already-borderline first-time cost over that 30s
budget. **Fix:** removed the copy step entirely — it turned out to be
unnecessary anyway, since `lexicon_resources.py` always reads straight from
the bundled/source resources directory (same pattern as
`original_language_resources.py`), never from the app-storage copy. Re-timed
after the fix: back to ~15-17s, matching the pre-existing baseline.

**2. A CSS bug, then a second CSS bug it exposed.** The user reported the
popup had an unwanted horizontal scrollbar. Cause: `LexiconPopup.svelte`'s
`<dl>` grid and the header's `<span class="headword">` are flex/grid items
that default to `min-width: auto`, which for an unbreakable Hebrew word (no
spaces to wrap on) means "don't shrink below this word's full width" — wider
content pushed the whole 440px popup wider, so `overflow: auto` added a
horizontal scrollbar to compensate. Fixed with `min-width: 0` +
`overflow-wrap: anywhere` on the grid/flex items, `minmax(0, 1fr)` on the
grid track, and `overflow-x: hidden` on the popup itself. That fix then
exposed a **second, latent** bug: the header's `"WORD DETAILS"` eyebrow used
`flex: 0 0 100%` to force itself onto its own row, but `header` never
actually had `flex-wrap: wrap` — it had only ever *looked* like two rows
because the resulting horizontal overflow was visible. Once overflow was
clipped instead of scrolled, the eyebrow's now-enforced full-width claim
squeezed the headword down to near-zero width (rendering one Hebrew letter
per line) and pushed the close button out of the visible/clickable area.
Real fix: add `flex-wrap: wrap` to `header` so the eyebrow genuinely wraps to
its own row, leaving the second row's full width for the headword and close
button.

### Files touched

New: `scripts/vendor-strongs-lexicon.mjs`,
`engine/tc_ai_bridge/lexicon_resources.py`,
`engine/tc_ai_bridge/morphology_codes.py`,
`engine/tests/resources/test_lexicon_resources.py`,
`engine/tests/resources/test_morphology_codes.py`,
`src/lib/components/LexiconPopup.svelte`,
`engine/resources/{hbo,el-x-koine}/lexicons/strongs/v1.0.2_openscriptures/`
(vendored data + NOTICE.md/PROVENANCE.json/index.json).

Modified: `engine/bridge_service.py` (new method + dispatch branch),
`engine/tc_ai_bridge/resource_materializer.py` (net no-op after the fix above
— comment explains why the lexicon is deliberately *not* mirrored into app
storage), `engine/tests/service/test_bridge_service.py`,
`src-tauri/src/commands.rs`, `src-tauri/src/main.rs`,
`src/lib/api/bridgeClient.ts`, `src/lib/types/finding.ts`,
`src/lib/components/AlignmentModal.svelte` (per-token info button, wired to
the popup; existing selection behavior untouched).

### Verified

- `pytest tests/ greek_room_engine/tests/ -q` — 240 passed, no regressions;
  new lexicon/morphology tests cross-check decoder output against real
  vendored data (Genesis 1:1, Titus 1:1, John 3:16), not synthetic fixtures.
- `npm run check` — 0 errors, 0 warnings. `npm run build` — succeeds.
- **Not verified:** `cargo check`/`cargo tauri dev` — this checkout has no
  built sidecar binaries (`build-sidecars.ps1` not yet run this session), a
  pre-existing environment gap unrelated to this work. The new Rust command
  is a direct structural copy of the existing `alignment_get` command.

### Still open

- The pre-existing ~15-19s synchronous first-time cost of
  `materialize_book_checks()` for a large book (confirmed independent of
  this work, see bug #1 above) is close enough to the 30s
  `checks.status`/`check.listForVerse` timeout that it can plausibly still
  time out occasionally on a slower disk, with no lexicon-popup code
  involved at all. The code's own comments describe the intended design as
  never blocking the dispatcher this way; worth a dedicated pass — likely
  moving `_ensure_resource_indexes()` off any synchronous call path
  (`run_verse_checks`, `saveEdit`'s `["local", "greekroom"]` call) the same
  way `list_checks_for_verse()` already avoids blocking.
- `LexiconPopup.svelte` has no client-side caching — refetches on every open.
  Fine given the tiny payload and Python-side `lru_cache`; revisit only if
  it's ever visibly slow.
- Greek morphology decoding covers every code actually observed in testing,
  not necessarily every code that exists in UGNT — unrecognized codes fall
  back to the raw string rather than a wrong label, by design.

## Stage 3 semantic passage mapping merge (2026-08-28)

Merged the Stage 3 builder-handoff package (6 new `tc_ai_bridge` modules,
`models.py`/`ai_client.py`/`bridge_service.py` integration, Svelte review-UI
changes, regression tests) per `patches/BETA14_STAGE3_CHECKLIST.md`. Stage 3
locates where a source tN/tW meaning is actually realized across a target
passage — including when the target moved it to a different verse — instead
of assuming every check's meaning stays verse-local, and gates unsafe
verse-local application of a mapping it can't ground with high confidence.

### Two real bugs found only by running the merged code, not by reading the patch

1. **Silent no-op on any per-unit mapping failure.**
   `semantic_mapping_bridge.prepare_semantic_mappings_for_review`'s
   `except Exception` handler on a `map_units` failure returned
   `checkStates: anchor_unresolved` — correct for anchor-lookup failures
   discovered *before* `map_units` runs, but anchor_unresolved is empty for
   units that were found and only failed *later* (e.g. the model's proposed
   target span didn't validate). Every check in that batch — not just the
   one that triggered the exception — silently reverted to identical-looking
   non-Stage-3 review with zero visible signal anything went wrong,
   defeating the fail-closed design's whole point (surfaced review, not
   silent bypass). Confirmed live: opening PHP 1:3 in a real Hindi IRV
   project produced no semantic-mapping card at all; the saved AI review's
   `semanticMapping.diagnostic` on disk (`.apps/translationCoreAI/aiReview/
   php/1/3.json`) showed the mapping attempt had actually failed. Fixed by
   populating one `mapping_error` check-state entry per unit in that except
   block, and adding the missing `mapping_error` branch to the Svelte
   semantic-map-card (the original patch's card only handled the other six
   states).
2. **Model-supplied target-span offsets are untrustworthy for non-Latin
   scripts.** `semantic_mapping.py`'s span validator hard-rejected a mapping
   whenever `seg_text[start:end] != quote` for model-supplied integer
   offsets — and the prompt never tells the model what indexing convention
   `start`/`end` use in the first place. This is exactly the failure
   observed for the Hindi case above (`"Target quote/offset mismatch ...
   hallucinated or normalized text rejected"`) — LLMs are unreliable at
   counting exact character offsets in complex/non-Latin scripts (Devanagari
   conjuncts/matras and similar), independent of whether the quoted text
   itself was genuine. Fixed by never trusting model-supplied offsets:
   always re-derive the span via exact literal-text search
   (`_literal_positions`), requiring the quote occur exactly once,
   unambiguously, in the target segment. This keeps the real
   anti-hallucination guarantee (the quoted text must genuinely be present)
   while removing a source of false-positive rejections that had nothing to
   do with hallucination.

### Also found: test-suite-wide isolation gap

`default_semantic_source_db_path()`'s dev-mode fallback resolves relative to
the checked-out source tree (`Path(__file__).resolve().parent.parent /
'resources' / ...`), so *any* test running `prepare_verse_review` from
source — not just Stage 3's own tests — silently picked up the real,
installed production DB and exercised genuine Stage 3 review policy. This
broke 5 pre-existing `test_ai_explain.py` tests that predate Stage 3 and
assume plain review behavior. Fixed at the root: `engine/conftest.py`'s
autouse `isolate_bridge_app_data` fixture now also sets
`BRIDGE_SEMANTIC_SOURCE_DB` to a path that can't exist, so Stage 3 defaults
to `state: "unavailable"` for the whole suite. `test_semantic_mapping_stage3.py`
constructs its DB path explicitly and is unaffected.

### Known gap, not yet fixed

`openSemanticSpan` in `TranslationHelpsReview.svelte` (click a mapped target
span to jump to it) only navigates — it sets `currentChapter`/`selectedVerse`
but does not highlight the `start:end` span. There's no existing mechanism in
this app to highlight an arbitrary span from outside a verse's own
finding-based highlighting (`utils/highlight.ts` `buildSegments`); that needs
new plumbing through `ReviewPanel.svelte`/the verse editor, deliberately
deferred as a follow-up.

### The production DB is not committed

`engine/resources/semantic_mapping/*.sqlite` (~120MB) is gitignored — it's
over GitHub's 100MB single-file push limit and this repo doesn't use Git
LFS. See `docs/DEVELOPER_SETUP.md`'s "Stage 3 semantic mapping DB" section
for how a teammate installs it locally via
`scripts/install_stage3_files.py` from the builder-handoff package.

### Verified

- `pytest engine/tests -q` — 269 passed, against the real full production DB
  installed locally (not just the regression-scope one).
- `npm run check` — 0 errors, 0 warnings. `npm run build` — succeeds.
  `npm run test:ui-state` — 4/4 pass.
- Frozen sidecar smoke test (`scripts/smoke_sidecars.py`) passes after
  rebuilding with `build-sidecars.ps1`.
- Live desktop app (`npm run tauri dev`), real Hindi IRV Philippians
  project, PHP 1:3 — this is what surfaced both bugs above; not caught by
  the test suite alone.

### Still open

- The navigation-only `openSemanticSpan` gap above.
- `prepare_verse_review` now computes the semantic-mapping pack
  unconditionally on every verse review (not only when alignment is
  incomplete) — one extra AI request per verse review, cached per
  passage-fingerprint so re-opening an unchanged passage is a cache hit.
  Worth watching real-world cost/latency impact.

## Brokered Paratext/Logos verse navigation (2026-09-03)

Bridge now has opt-in two-way verse navigation for Paratext and Logos. The existing connector
clients and `NavigationBroker`/`NavigationOwnership` are wired through
`navigation.status/poll/bridgeChanged/resolve`; Svelte polls cached state and explicitly accepts
or rejects each external candidate after verifying the destination exists in the open collection.
Bridge-originated changes go to every enabled connector, while a Paratext- or Logos-originated
change is forwarded only to the other connector after Bridge loads it.

Connector calls run in one bounded daemon probe rather than the synchronous stdio dispatcher.
Unavailable applications therefore cannot freeze verse review, and the latest outbound Bridge
reference remains queued for reconnect. Echoes, stale settling observations, duplicate polls, and
same-context rejected jumps are suppressed. A per-Windows-user mutex prevents two Bridge windows
from driving the desktop applications simultaneously. Incoming navigation is rejected while a
verse edit is active, and invalid cross-book destinations restore the prior Bridge location.

The new Settings → Connections pane controls Paratext and Logos independently and reports live
connection/reference/error state; the top bar exposes the same state compactly. Automated gates
cover non-blocking behavior, both navigation directions, rejection retry semantics, ownership,
and reconnect catch-up.

Live Paratext acceptance read IRVTam at PHP 1:3 in sync group B and a same-reference outbound
request returned `reference_set_by_bridge` with the expected origin ID. Logos was installed but
closed. That check found and fixed two defects in the older unverified helper: its ProgID is the
documented and locally registered `LogosBibleSoftware.Launcher` (not
`Logos4Lib.LogosLauncher`), and active state uses the documented `GetActivePanel()` plus
`GetCurrentReferencesAndHeadwords()` calls. The real helper now returns clean disconnected state
while Logos is closed.

On 2026-09-04, live Logos 53.1 acceptance exposed a second interop layer issue:
PowerShell's .NET COM wrapper could create the correct launcher but rejected typed return
objects with HRESULT `0x80131165`, even with the Logos type library registered. A bundled
native-`IDispatch` VBScript shim now performs panel/reference traversal and `ExecuteUri`
navigation while the persistent PowerShell helper retains the JSON-lines transport. Live state
read the ESV panel at PHP 1:5, and Bridge-to-Logos navigation to PHP 1:5 round-tripped with API
version 3. The Connections-pane flicker was also fixed by retaining the last connected/error
detail while routine background probes run; failed Bridge-originated publish RPCs now retry.


## Stage 9A.4 follow-up (1 of 3) — a running analysis job disappeared on navigation (2026-09-04)

Picks up the "Stage 9A.4 bug fixes" pass (commit `bcb9a7e`), which was
committed unfinished. That pass made a job's identity content-addressed:
`AnalysisJobManager._analysis_identity()` now hashes project, scope kind,
canonical start/end, per-reference target hashes, target revision, source
resource id/version/hash, provider capability, and a `policyVersions` map
collected from every Stage 5-8 engine/calibration/confidence policy. That
digest is the `analysisFingerprint`; `analysisJob.start` requires the caller
to pass back the fingerprint it resolved and refuses to start when it no
longer matches. The UI (`AnalysisControls.svelte`) was made generation-guarded
to match: out-of-order scope lookups are discarded, and `Run analysis` stays
disabled until the displayed status matches the current selection.

That UI change also added a reactive block calling `changeScope()` whenever
`chapter`/`verse` changes. `changeScope()` unconditionally set `job = null`
and cleared the poll timer. But navigation is exactly what a reviewer does
*while* analysis runs - the QA queue is the work surface, and a
`CURRENT_BOOK` run is long. Clicking any finding therefore killed the poll
and dropped the job from the UI: no stage progress, no **Cancel**, and **Run
analysis** re-enabled - where a second click hit the engine's `Analysis job
<id> is already running` conflict as a raw error string.

Fixed by separating "the job being tracked" from "the latest job for the
displayed scope": `isActive()` gates the three places that could replace it
(`changeScope`, `refreshScopeStatus`, `poll`), so only a terminal job is
replaced by whatever the newly selected scope reports. `poll()` now also
works from a captured snapshot, because `refreshScopeStatus()` could
reassign `job` across its `await` and make the `completed` event fire with
the wrong (or a null) job.

Regression test: `AnalysisControls.test.ts` › "keeps a running book job
visible when current navigation changes" - confirmed to fail on `bcb9a7e` at
the post-navigation assertion with the label and Cancel button gone, and to
pass with the fix.

While there: the range inputs had moved from `on:change` to `on:input`, so
every keystroke fired a scope-status RPC, and clearing a field to retype it
sent an incomplete range that the engine rejects - surfacing "Selected range
requires start and end references" as a red error mid-typing. `isResolvable()`
now withholds the request until all four fields are present, and lookups are
debounced 150ms.

Verified: `npm run check` (0 errors/warnings), `npm run test` (110 passed,
was 109), `npm run build` (succeeds).

## Stage 9A.4 follow-up (2 of 3) — the Logos VBScript shim went silent on any COM hiccup (2026-09-04)

**VBScript scopes `On Error Resume Next` per procedure.** `logos_com.vbs`
(added in `bcb9a7e` to work around PowerShell's .NET COM wrapper rejecting
Logos's typed return values with HRESULT `0x80131165`) set it once at file
scope, so it did *not* cover `Sub EmitState` - every `If Err.Number = 0`
guard inside that Sub was dead code. The first COM error there (a non-Bible
panel active, a panel Logos will not hand over, a build without
`LogosPanel.Kind`) aborted the whole Sub, and execution resumed in the
caller at `WScript.Quit 0`: **exit code 0, nothing printed at all.**
`logos_bridge.ps1` then reported `Native Logos COM shim returned no
response`, so Bridge showed a hard Logos error while Logos was running and
connected. The same path follows a *successful* `ExecuteUri`, so an outbound
navigation that actually worked could still be reported as a failure.

The live 53.1 acceptance in `bcb9a7e` did not catch this because it ran with
an ESV Bible panel active - the one path where nothing throws.

Confirmed by running the real `EmitState` against a `FakeApp` whose
`GetActivePanel()` raises: before, zero output; after, `ok=1 connected=1
api_version=3` with empty book/chapter/verse. `EmitState` now opens with its
own `On Error Resume Next` and clears `Err` before emitting, so a panel with
no Bible reference reports *connected without a reference* rather than
failing.

Regression test: `test_logos_connector.py` ›
`test_state_is_still_reported_when_the_active_panel_raises` (skipped where
`cscript` is unavailable). It lifts the real `Sub EmitState` out of the
shipped `.vbs` rather than copying it, so it tracks the file it guards.
Confirmed to fail against `bcb9a7e` ("EmitState went silent") and pass with
the fix.

Still open:
- Not re-verified against a live Logos session or a frozen sidecar build:
  the shim fix is proven against a fault-injected `EmitState`, but
  `build-sidecars.ps1` + `scripts/smoke_sidecars.py` and a real Logos 53.1
  panel-switch acceptance still need a run on a machine with Logos installed.
- `bridge_to_logos_uri()` upper-cases the whole reference, so a lettered
  verse segment becomes `logosref:Bible.Php1.3A` rather than the
  conventional lowercase `...3a`. Whether Logos accepts the upper-case form
  is unverified - left alone deliberately rather than guessed at.

Verified: `pytest tests/ greek_room_engine/tests/ -q` (589 passed, was 588).

## Stage 9A.4 follow-up (3 of 3) — bounded the Bridge navigation publish retry (2026-09-04)

`App.svelte`'s navigation-publish retry (added in `bcb9a7e` alongside the
fingerprinting work) cleared `lastNavigationReference` on failure to re-enter
the reactive publish block, with no attempt counter - a persistently failing
`navigation.bridgeChanged` call would have spun one RPC every 800ms for the
rest of the session. Capped at 3 attempts per reference, reset on success or
when the reference changes.

Verified: `npm run check`, `npm run test`, `npm run build` all still pass.

## AI review never auto-selected tN/tW words — two Stage 3 defects (2026-09-04)

Reported as "running the AI check still isn't auto-selecting the words, it's
still showing Pending". Diagnosed against the reporter's own saved reviews
under `<project>/.apps/translationCoreAI/aiReview/<book>/<ch>/<vs>.json`,
which persist `selection_state`, `semantic_mapping` and the Stage 3 pack —
so the failure is reconstructable offline, with no API key and no rerun.

Aggregate over the 53 saved reviews on that machine: 70 checks ended
`mapping_error` against 9 `found_this_verse`, and 9 of the 12 most recent
Stage 3 packs had `mappings: []`. **Two independent bugs, either one alone
enough to leave every check Pending.**

### 1. One bad model row discarded the whole batch

`SemanticMappingEngine._validate_result` raised on the first offending
mapping row, so a single unusable row threw away every *other* unit's
perfectly good mapping in the same call.
`prepare_semantic_mappings_for_review` then caught that and — correctly, for
the transport/schema failure its comment describes — marked *every* unit
`mapping_error`, which `apply_semantic_review_policy` treats as `_UNRESOLVED`
and clears the proposal for. One hallucinated target quote therefore took out
an entire verse.

PHP 1:6 is the clean example: all 10 checkStates carry the identical detail
`Target quote for translationNotes:qhmh ... was not found as an unambiguous
exact match`, i.e. nine checks were failed by a tenth check's row. The
observed triggers were all single-row: a hallucinated/ambiguous quote (×3),
`Model changed canonical source token IDs` (×3), `Model returned unknown
source unit` (×1).

Row validation moved into `_validate_mapping_row`; the caller catches
`SemanticMappingValidationError` and quarantines that unit as unresolved, so
the adaptive search retries it on the next layer while every accepted mapping
survives. `_MappingRowRejected` carries a per-row `reason` (`AMBIGUOUS` for
the quote case). Unattributable rows — unknown or duplicated `source_unit_id`,
either list — are dropped rather than fatal; the `missing` backstop still
catches any unit that goes unmentioned. A unit the model both maps and
unresolves now fails closed: the mapping is withdrawn and the unit stays
pending. Only genuine top-level schema breaks still raise.

Quarantined units land as `needs_extended_passage_review`, which is still
`_UNRESOLVED` — so that one check stays Pending, as it should, and the rest
of the verse proceeds.

### 2. The auto-apply gate compared clause spans to word tokens

Independent of #1, and the reason even a *clean* Stage 3 run selected
nothing. `semantic_review_policy.native_tc_apply_allowed` required the
proposed translationCore selections to equal the verified Stage 3 target
spans (`selection_text == span_quotes`, or a single span joined from every
selection). Those are different granularities and essentially never equal: a
Stage 3 span is the clause the meaning is realized in, a tC selection is the
word tokens inside it. So `_safe_ai_selection_reason` returned "Stage 3
mapping is not safe for a verse-local automatic selection" for every mapped
check.

PHP 1:19 is the proof — Stage 3 `ready`, 8 checks `found_this_verse` /
`PRESERVED` / confidence 1.0, and zero selections on disk. E.g. proposed
`["यीशु", "मसीह"]` against span `"यीशु मसीह की आत्माके दान के द्वारा,"`.

Replaced with containment: each proposed token must fall inside a verified
span for this verse. Substring containment is deliberate — it also admits the
compounded/suffixed target forms the review prompt explicitly asks the model
to select (`आत्मा` within `आत्माके`). It does not weaken the anti-hallucination
guard: the token comes from a supplied bottomWord ID (already checked against
`known_ids`), `validate_check_selection` re-verifies its occurrence against
the verse text, and `save_check_selection` still refuses to overwrite
imported/human choices.

Re-running the real gate over the untouched PHP 1:19 record: 8 of 9 checks
now APPLY, the 9th correctly skipped as `target_not_located` + verdict
`review`. Before: 0 of 9.

### Note for anyone reproducing this

`start_ai_review_job` skips verses whose cached review is `current`, so
chapter/book scope will *not* revisit a verse already reviewed under the
broken behaviour — it reports them as skipped-because-current. Rerun at
**verse** scope (which never skips) to see the fix on an
already-reviewed verse. Verses hit by bug #1 need a fresh model call either
way; their stored `mapping_error` states are what they are.

`skippedSelections` still carries a per-check `reason` all the way to the
frontend (`AIReviewResult` in `types/finding.ts`) and nothing renders it —
which is why this presented as a silent "nothing happened". Surfacing it is
not done.

Regression tests (`tests/semantic/test_semantic_mapping_stage3.py`):
`test_one_rejected_row_does_not_discard_the_rest_of_the_batch`,
`test_word_selections_inside_a_clause_span_are_applicable`, and
`test_hallucinated_target_quote_is_rejected` rewritten — it asserted the
old whole-batch abort, and now asserts the stronger invariant it was
protecting: the hallucinated quote never becomes a mapping and the unit ends
unresolved, never an omission.

Verified: `pytest tests/ greek_room_engine/tests/ -q` (597 passed).
Not verified in the running desktop app — that needs a real API key and a
live rerun.

## Surfaced the automatic-selection outcome in the UI (2026-09-04)

Follow-up to the two Stage 3 defects above. The engine had always decided, per
check, whether it could select the target words automatically and — when it
could not — why; `_safe_ai_selection_reason` returns real sentences ("AI
confidence is below the 82% automatic-selection threshold", "Contradictory QA
evidence requires human review"). Nothing rendered them, so a check the AI
deliberately declined was visually identical to one no AI had ever seen: the
same bare "Pending" pill. That is the direct reason the two bugs above read as
"the AI check does nothing" rather than as a specific, diagnosable refusal.

`appliedSelections` / `skippedSelections` were also the wrong carrier for this:
they ride along with one verse's job result, and `latestResult` holds only the
most recent verse — so on a chapter run the reasons for every earlier verse
were already gone, and navigating away lost them entirely.

**Persisted instead.** `TranslationCoreProject.record_ai_selection_outcomes`
merges an `automaticSelection` map (`"<tool>:<checkId>" -> {outcome, reason}`)
into the saved AI review record, written at the end of
`_apply_safe_ai_selections` — after `rebase_ai_review_fingerprint`, since both
rewrite that file. `list_checks_for_verse` reads it back onto each check row, so
the question survives navigation and app restart.

`_check_review_from_entry` declares `automaticSelection: None` as its default.
Without it a row returned by `save_check_selection` would be missing the field
the UI reads — and that default is also correct on its own terms: a human who
has just saved over a selection is no longer described by the AI's outcome.

UI, all in the two places a reviewer is already looking:
- `TranslationHelpsReview` — per check, "✓ Selected automatically by the AI
  review" (guarded on `provenance === "bridge_ai"`, so a human takeover drops
  the claim) or "Left for you — <engine's own reason>" on a still-pending
  check. Reasons render verbatim; re-mapping them in TypeScript would be a
  second copy of the policy, free to drift from the engine's.
- A verse summary bar replacing the old unconditional notice: "N of M checks
  complete · K selected by AI review" / "J pending". Green once nothing is
  pending.
- `ReviewPanel`'s job status gains a run-wide roll-up from the counts already
  in the snapshot (`appliedCount`/`skippedCount` per verse) — the "how much did
  it actually do" answer for a chapter or book pass, where no single verse is
  on screen.

Tests: `test_ai_explain.py` ›
`test_automatic_selection_outcome_survives_the_job_result` reads the verse back
through `check.listForVerse` with no job snapshot in hand and asserts every
applied/skipped row's reason round-trips. New
`__tests__/TranslationHelpsReview.test.ts` (4 tests) covers the declined-reason
line, the applied marker plus tally, the human-takeover case, and the unchanged
"run AI review" prompt when no automatic pass has run.

Verified: `pytest tests/ greek_room_engine/tests/ -q` (598 passed),
`npm run check` (0/0), `npm run test` (127 passed), `npm run build`.
Not verified in the running desktop app.

## Stage 9B.0 — correction schema/API design and dependency repair (2026-09-04)

Foundation only. No wording generation, no correction UI, no Scripture
application, no post-correction rerun — those are 9B.1 and later. The goal was
to make the backend safe enough that 9B.1 can build on it.

### Safety boundary

`test_stage_9b0_never_alters_scripture` hashes every chapter JSON, the
preserved imported USFM and all translationCore alignment data, runs
everything this stage can do (eligibility over every finding in the fixture,
current-text validation, saving a v2 proposal, both read APIs), and asserts
the digests are byte-identical. `[Create Correction Proposal]` is not exposed
in the UI; no frontend file was touched.

### CorrectionProposal v2 (`proposalSchemaVersion` 2)

Added alongside v1, which is preserved. New `AffectedTargetSpan` carries the
exact half-open code-point span plus `targetTextRevision`/`targetContentHash`;
`CorrectionIntent` carries `failedDimension` (reusing `CoverageDimension` —
no duplicate polarity/quantity enums), observed vs required meaning, and the
affected source units. `proposedText` is deliberately allowed to be empty: a
9B.0 proposal states *what must change*, not *how to say it*.

`is_applicable` is structural only — it says the record carries the
coordinates an application needs. It is not authorization; eligibility,
current-text validation and human approval are all separate.

### Migration v11

`ALTER TABLE` adds `proposal_schema_version`, `verification_status`,
`applicable` (default 0), plus `correction_application_intents` and a
finding-lookup index. Pre-v11 proposals predate the exact-span contract, so
they are stamped schema v1 and stay `applicable=0`: **Bridge must not infer a
span retroactively**, since a guessed span is exactly the silent Scripture
damage this stage exists to prevent.
`test_v10_to_v11_migration_preserves_legacy_proposals` builds a real v10
database with a v1 proposal and asserts the upgrade, the pre-migration backup,
`recovery_check().ok`, preserved payload history, and `applicable is False`.

Widening the table also broke the positional `INSERT INTO correction_proposals
VALUES(?…)` — now column-named, so the next migration cannot repeat it.

### Eligibility (`correction_eligibility.py`)

One authority, `CorrectionEligibilityService.evaluate(finding_id)`. Requires
CONFIRMED_TRANSLATION_ERROR + HUMAN_APPROVED + ACTIVE, a finding that still
matches current Scripture, usable location evidence, no human-rejected
mapping, no meaning overridden to PRESERVED, no unresolved resource conflict,
and no conflicting proposal. Blocks UNRESOLVED / ACCEPTABLE_TRANSLATION /
FALSE_POSITIVE / NEEDS_DISCUSSION / stale / AMBIGUOUS / SEARCH_INCOMPLETE /
UNSUPPORTED_ANALYSIS.

`NOT_LOCATED` is deliberately **not** in the unusable set: a confirmed genuine
omission is a NOT_LOCATED source unit, and inserting text is exactly what
should be able to fix it.

Returns structured reasons, never a bare boolean, and accumulates every
blocker rather than short-circuiting — a reviewer clearing one blocker only to
meet the next is a bad loop.

### Current-text validation

Stage 9A's `_check_target_hashes` compares a submitted hash against the
finding's own snapshot, which only proves the reviewer saw what the finding
recorded — it cannot detect a later edit. Eligibility additionally re-reads the
authoritative chapter JSON via `current_target_text()` and compares revision,
content hash and exact span text. Exact comparison only; no fuzzy matching
anywhere on this path.

### Dependency graph repair

The record-type→table map existed as **three** hand-maintained copies. Stage 8
added QA_RUN edges and taught only one, so every project that had run a QA
audit failed recovery on its next open and went read-only. Now one
`RECORD_DEPENDENCY_TABLES` constant, with `RECORD_DEPENDENCY_ANCHOR_TYPES` for
legitimate upstream-only anchors, and
`test_every_writable_dependency_type_is_registered` scans the repository
source for edge literals and asserts every one is known.

Edges added: `LOCATION_RELATIONSHIP → LOCATION_RUN` (missing entirely — a
re-run staled the run but left every individual relationship looking current);
`MEANING_ASSESSMENT → LOCATION_RELATIONSHIP`; `QA_FINDING →
MEANING_ASSESSMENT / SEMANTIC_UNIT / SEMANTIC_RELATIONSHIP`; and the full
`CORRECTION_PROPOSAL → QA_FINDING / TARGET_REFERENCE / SEMANTIC_UNIT /
MEANING_ASSESSMENT / SEMANTIC_RELATIONSHIP / EVIDENCE_RECORD` set.

**Latent bug the new invariant test caught:** `_stale_generic_dependencies`
unconditionally did `revision=revision+1`, but `source_inventory_runs` has no
`revision` column. It never fired only because nothing currently registers that
type downstream — the first edge that did would have raised mid-propagation and
aborted the whole invalidation, leaving downstream records falsely *current*.
Propagation is now schema-aware and always propagates STALE.

`recovery_check` also now reports an unrecognized *upstream* type instead of
skipping it silently — the same drift class in the other direction.

### record_correction_applied neutralized

It used to flip the finding straight to CORRECTED, making the strongest claim
in the system a side effect of writing bytes, with no evidence the edit
achieved anything. Renamed to `record_correction_application_metadata`, with
the old name kept as a thin alias (deleting it would lose callers; keeping it
as-is would preserve the misreading). It now stamps `applied_*`, moves the
proposal to `verificationStatus = PENDING`, and marks the finding STALE —
historical, pending re-analysis — while leaving its disposition and review
status exactly as the human set them.

### Verification model

`VerificationStatus` = NOT_RUN / PENDING / PASSED / FAILED / UNCERTAIN,
independent of `QaDisposition`. The invariant **applied ≠ corrected** is
covered three ways: application leaves the disposition alone, the deprecated
name cannot mint CORRECTED, and PASSED alone still does not produce CORRECTED.

### Application transaction model (design only)

`CorrectionApplicationIntent` + `CorrectionApplicationState` and the
`correction_application_intents` table. Nothing writes Scripture through it.
`APPLIED_SCRIPTURE` is kept distinct from `COMPLETED` on purpose: the window
between "Scripture written" and "bookkeeping finished" is exactly where a crash
needs recovery, and collapsing them makes that state unrepresentable.

### Canonical edit path

`test_correction_modules_contain_no_scripture_writer` asserts the correction
module contains no writer at all.
`test_apply_scripture_edit_still_lacks_the_strict_preconditions` pins the gap:
`apply_scripture_edit()` takes no `expected_target_revision`,
`expected_target_content_hash` or `expected_old_text`, so it cannot yet fail
closed on a concurrent edit. The test fails once those land, which is 9B.3's
signal to update it.

### Span contract

Half-open Unicode **code-point** offsets `[start, end)`; `[n, n)` is valid and
is how a genuine omission is repaired. Fixtures cover Tamil combining marks,
Hebrew points, Greek diacritics and supplementary-plane characters — the last
being the one that silently breaks a JavaScript caller, where those characters
are one code point here and two UTF-16 units there.

### Tests and gates

`tests/correction/test_correction_stage9b0.py` — 55 tests, all passing.

Two pre-existing tests updated, both by the spec's own requirements:
`test_correction_application_requires_human_and_stales_dependencies` asserted
the old CORRECTED behaviour and now asserts the new invariant; two Stage 9A
tests pinned `DATABASE_SCHEMA_VERSION == 10`.

`npm run check` (0/0), `npm run test` (132), `npm run build`,
`git diff --check` — all clean.

**Rust gates not run:** `cargo check` fails in `tauri_build::try_build` with
`PermissionDenied` because a `cargo run --no-default-features` (a `tauri dev`
session) is live and holds the sidecar binaries. Stage 9B.0 touched no Rust,
`.rs`, or `tauri.conf.json` file, so this is contention with a running app, not
a regression — but `cargo check`/`cargo test` still need a run once that dev
session is stopped.

## Project QA report — Generate report on the project screen (2026-09-04)

Request: a **Generate report** button in the project (dashboard) top bar
that lists every check Bridge has run and every issue it found across the
whole collection — books with per-check progress on the left, an
Allure-style right panel (filters, charts, an issue table with error
category / book / chapter / verse / issue + explanation / AI proposal / fixed
by / pass-fail) and CSV / TSV / PDF export of the filtered rows.

### What existed, and the gap it exposed

`project.report` (`ReportService.build_book_report`) is single-book and
built for the dashboard's exception queue; `project.collectionReport`
aggregates gates and coverage counts, not issues. Neither could list issues
across books. Investigating what the collection actually has on disk turned
up the real gap: **per-verse findings were never persisted.** A check job's
Wildebeest and local (alignment / editorial / tC) findings lived only in the
job's in-memory result; the progress rollup (`.bridge/progress.json`) kept
finding *id → status* and nothing else, and `checkCache.json` only ever held
the whole-book USFM/Names passes (the Wildebeest warm-up was reverted in
issue #24). So a fully checked book had open Wildebeest findings recorded
by id alone — nothing to put in a table.

### Finding snapshots (`checkFindings/<book>/<chapter>.json`)

`BridgeEngine._on_check_job_complete` now also writes
`.apps/translationCoreAI/checkFindings/<book>/<chapter>.json` — the
`QaFinding.to_dict()` list per verse for exactly the chapters a *succeeded*
job covered, via `TranslationCoreProject.save_check_findings_snapshot`.
Written after the rollup so a crash between the two leaves the rollup (what
the dashboard reads) intact. The rollup stays authoritative for status; the
snapshot only supplies content. `verse.runChecks` (the interactive
single-verse path) does not write one — that path never updated the rollup
either, and "checked" continues to mean "a chapter job succeeded".

### `tc_ai_bridge/qa_report.py`

`build_book_qa_report(project)` reads only persisted state — snapshots,
`checkCache.json`, the rollup, `qaDecisions/`, the live tN/tW index,
`checkData/{selections,verseEdits}`, `tools/wordAlignment/{completed,invalid}`,
alignment chapter JSON and `aiReview/` — and returns per-book check
coverage plus flat rows. Row rules worth knowing:

- **Categories** are the report's own axis (`greekRoom`, `translationNotes`,
  `translationWords`, `alignment`, `aiReview`), not `QaFinding.category`:
  Wildebeest, USFM, Names and the local editorial checks all fold into one
  Greek Room bucket with the engine kept on the row.
- **Resolved / pass** = status not in {open, needs_discussion} — the exact
  PASS rule `reporting._verse_coverage` already uses, so the report and the
  dashboard's coverage bar never disagree.
- **tN/tW rows come from the live index**, not the snapshot: a check
  pending at the last job may have been selected since. Every check is a
  row (pending / invalidated / stale = fail; selected / nothing-to-select =
  pass), because a completed check with a provenance is the only thing that
  can be "fixed by machine". Provenance comes from one walk of
  `checkData/selections` (`_SelectionIndex`) instead of
  `_latest_state_for_check`'s per-check glob — username `Bridge AI` →
  machine, any other → human (named), no Bridge record → human
  (`translationCore`). Staleness reproduces `check_staleness` exactly
  (selection timestamp ≤ latest verse-edit timestamp).
- A pending/invalidated tN/tW check the reviewer **Ignored** through
  `verse.decide` is resolved by that human decision; the report re-derives
  the finding id (`stable_finding_id`, a duplicate of
  `bridge_service._stable_finding_id` pinned by a test because
  `bridge_service` imports this module).
- **AI proposal**: Greek Room `suggested_replacement`; tN/tW the AI review's
  `suggested_correction` for that check (or `Select: …` from its proposed
  selection text when nothing is selected yet). AI `qaIssues` are rows only
  while the review is `current`; a stale review describes text that no
  longer exists and is dropped rather than shown as an open failure.
- **Alignment**: `WA_INVALID` rows for every currently-invalid mark (not
  only chapters a job covered), plus whatever `ALIGN_*` findings the
  snapshot holds.
- **Check-level pass/fail** (the "checks run" tiles and chart) is separate
  from row pass/fail: Greek Room per checked verse, tN/tW per check,
  alignment per touched verse; untouched/unrun work is *not run*, never a
  failure. AI review is advisory and excluded from those totals.
- Lazy (never opened) and missing siblings are listed as not checked
  without being materialized — generating a report must not turn into
  normalizing the whole Bible.

### `report_jobs.py` and the protocol

`ReportJobManager` is the `ProjectSweepManager` pattern with its own lock
domain (a report must never be refused because a sweep or chapter job is
running). It builds one `TranslationCoreProject` per sibling on a worker
thread and never touches `BridgeEngine.project`. Methods: `report.generate`
(start), `report.status` (small snapshot, safe to poll every 500 ms),
`report.get` (the payload, fetched once), `report.cancel`, and
`report.export` (`{outputPath, format: csv|tsv, rows, columns}` — the
frontend sends the rows it has filtered to; Python writes them with the
`csv` module, UTF-8 BOM so Excel opens Tamil/Odia/Hebrew as text). Only the
latest job's payload is kept in memory. Rust: five thin commands;
`report.get`/`report.export` get the 180 s timeout class, the rest stay
interactive (a sixth `sidecar::tests` case pins that).

### Frontend

- `TopBar`: **Generate report** (primary) on the dashboard screen; a
  `QA report` breadcrumb on the new top-level `report` screen.
- `App.svelte`: `showingReport`, a 500 ms poll (`generateReport` /
  `pollReport`, generation-guarded like the check monitor), the payload kept
  for the session, and `navigateToReportRow` (verse link in a row → that
  verse in the editor, switching books first when needed). A `report_conflict`
  on generate (double click) follows the running job instead of failing.
- `ProjectReportScreen.svelte`: left panel = every book with Greek Room /
  tN / tW / alignment / AI-review progress bars (clicking scopes the right
  panel); right panel = one filter row (category chips, book, chapter, fixed
  by, result, severity, search) that scopes the stat tiles, four charts
  (issues by category donut, fixed-vs-unresolved bars, checks
  passed/failed/not-run bars, fixed-by donut) and the table. Charts are
  hand-drawn SVG/HTML (`ReportDonut`, `ReportBars`) — the app is offline, no
  CDN chart library. Colours are the app's finding legend in the order tN,
  tW, Greek Room, alignment, AI (violet, new `--ai` token) — validated with
  the dataviz palette validator; the one WARN pair (amber↔green, deutan) is
  covered by the mandatory legend + direct labels, and the fixed-by ring
  uses cyan/violet/red because accent blue↔violet failed all-pairs. The
  table pages 100 rows at a time.
- **Export**: CSV/TSV via `pickSavePath` + `report.export`. **PDF = print**:
  a `.print-root` / `.print-expand` / `.no-print` convention in `index.css`
  prints only the right panel with every scroll container un-clipped, and
  the table renders every filtered row while printing. A pure-Python PDF
  writer was rejected on purpose: it cannot shape Tamil/Odia/Hebrew without
  an embedded shaping engine, and the webview already does.
- `utils/reportStats.ts` holds the filter/aggregate logic so it is
  unit-testable without the DOM.

### Verification

`tests/service/test_qa_report.py` — 11 tests (snapshot written by a real check job
and read back with a decision applied; lazy/missing siblings not
materialized; tN/tW provenance for Bridge AI / human / translationCore /
invalidated / nothing-to-select with an AI proposal merged in; Ignore on a
pending tN check; a `WA_INVALID` mark; cached USFM findings + collection
totals; export CSV/TSV with BOM and quoting; error paths). The complete Python
suite passes. Frontend: 19 new Vitest tests (`reportStats.test.ts`,
`ProjectReportScreen.test.ts`) — 151 total; `npm run check` 0/0; `npm run
build` OK. `cargo check` and `cargo test` (6 tests) pass. A raw stdio smoke
(`python main.py`: open → checks.start → report.generate/status/get/export)
was run against a throwaway project. **Not run:** the installed desktop app
— the report screen has not yet been looked at in a real Tauri window, and
print-to-PDF has not been exercised in WebView2.

## Stage 9B.1 — correction wording generation only (2026-09-05)

Baseline: `main` / `5df5c0d`. The Project QA Report added after `c7488df` was
identified across Python, Rust, Svelte, tests, and docs before implementation
and kept as a frozen regression boundary. No report implementation file was
modified.

### Architecture and safety boundary

Added `tc_ai_bridge/correction_wording.py` with a vendor-neutral
`CorrectionSuggestionProvider` contract and three implementations: no-provider
(offline human flow), deterministic fixture provider, and an adapter over the
existing configured Responses-compatible client. Provider input is restricted
to the exact `CorrectionIntent`, current target span/verse and relevant passage
context, source/target semantic units, locations, meaning assessments,
coverage/QA reasoning, and relevant resource evidence. The prompt requires the
smallest defensible semantic repair and forbids unrelated stylistic rewriting.

Provider output is never authoritative. It cannot change Scripture, QA
disposition, alignment, review approval, or verification. Provider/model,
prompt-policy version, response fingerprint, evidence IDs, alternatives and
warnings are persisted; keys, authorization headers and secret settings are
not. The configured adapter updates the existing AI privacy manifest with the
actual Stage 9B.1 context field classes and marks unrelated project files as
not sent.

`CorrectionProposalV2` now carries alternative wordings, provider metadata,
warnings, original machine wording, location relationship IDs, and an optional
`supersedesProposalId`. Canonical creation modes add `MACHINE_SUGGESTED` and
`MACHINE_SUGGESTED_HUMAN_EDITED` while retaining old enum values for existing
data. Alternatives share the parent's exact intent/span/revision/hash and keep
their own wording, explanation, evidence and provenance.

Eligibility and current-text/span validation run once before any provider call
and again immediately before persistence. This prevents a slow provider result
from being saved after the finding or target changed. The SQLite insert also
checks competing ACTIVE/INACTIVE ownership inside `BEGIN IMMEDIATE`, closing
the concurrent-create race. All comparisons remain exact Unicode code-point
comparisons; no fuzzy search or silent span relocation was introduced.

Human proposal editing requires `expectedProposalRevision`, rejects stale or
inactive proposals, retains the original machine wording, and appends history.
Rejection keeps all proposal data and sets `HUMAN_REJECTED` / `INACTIVE`.
Regeneration is one transaction: the old row becomes `SUPERSEDED`, and a new
row with `supersedesProposalId` is inserted. It never overwrites the reviewed
record.

### Persistence and APIs

Database schema advanced from v11 to **v12**. Migration creates
`correction_proposal_events` with full snapshots and controlled events:
`CREATED`, `SUGGESTED`, `EDITED`, `REJECTED`, `SUPERSEDED`, `STALE`. Every
event records actor, timestamp, base/new revision, note/reason and provider
metadata. Existing proposals receive a migration-authored creation snapshot;
the standard pre-migration SQLite backup remains in force.

Python sidecar protocol additions:

```text
correction.createProposal
correction.editProposal
correction.rejectProposal
correction.regenerateProposal
correction.getProposalHistory
```

Existing `correction.getEligibility`, `correction.getProposal`, and
`correction.listForFinding` remain. No `correction.applyProposal` exists.
There is no Stage 9B.1 frontend or Rust command because correction UI and Apply
remain outside this stage.

### Tests and regression gates

`engine/tests/correction/test_correction_stage9b1.py`: **33 passed**. Coverage includes
real backend eligibility, immediate pre-persistence recheck, offline human
wording, provider fallback/privacy/provenance, deterministic suggestions,
alternative ownership, all requested semantic repair shapes, exact span/hash/
revision storage, CAS conflict, stale edit, original-suggestion preservation,
rejection, atomic supersession, zero-length omission insertion, multilingual
Unicode spans, protocol round trips, restart/recovery, append-only history, and
byte-identical chapter JSON/imported USFM/alignment artifacts across every
Stage 9B.1 operation.

Final gates:

```text
full Python suite                         705 passed
frontend Vitest                          151 passed (17 files)
ProjectReportScreen/reportStats          included and green
npm run check                            0 errors, 0 warnings
npm run build                            passed; existing large-chunk warning
cargo test                               6 passed
cargo check                              passed
git diff --check                         passed; only CRLF notices
```

Remaining limitations: unresolved resource conflicts still block proposal
creation by the existing conservative policy; no installed-app acceptance was
needed for this backend-only stage; proposal UI, Apply, strict Scripture-edit
CAS, post-edit realignment/reanalysis, `CORRECTED`, and export are deliberately
not implemented. Next authorized unit is Stage 9B.2 UI only, after approval.

## Stage 9B.2 — correction review UI (2026-09-05)

### Checkpoint and boundary

Implementation started from committed Stage 9B.1 checkpoint `8722d0c`. The
post-9B.0 Project QA Report surfaces were rechecked and treated as frozen: no
report implementation file was modified. This stage adds proposal review only;
it adds no Apply command, Scripture/translationCore writer, analysis rerun,
`CORRECTED` transition, or export behavior.

### Current-text review context

The frontend could not safely construct `CorrectionIntent` from the existing QA
detail payload: Stage 6B exposed stable span IDs/quotes, while Stage 9B.1
requires the current per-reference target revision/hash and exact Unicode
coordinates. A read-only `CorrectionWordingService.review_context()` was added
rather than duplicating repository rules in Svelte. `correction.getReviewContext`
resolves location span IDs through the stored Stage 6B run/target inventory,
re-reads current editable target text, computes its current hash/revision, and
returns an exact candidate only when the stored quote still matches at the same
code-point coordinates. It never fuzzy-searches or relocates a stale span.
Versification-normalized canonical references are retained from source semantic
units and refined from a concrete target location when present.

The method was wired through `passage_semantic_runtime.py`, `bridge_service.py`,
nine thin Rust/Tauri correction commands, and typed `bridgeClient.ts` methods.
Provider create/regenerate calls use the 260-second provider timeout; review
reads and local proposal writes retain the 30-second interactive timeout.

### Correction review workflow

`CorrectionReviewPanel.svelte` now appears inside Alignment Review's QA finding
detail, after the existing evidence inspector. It always asks backend
`correction.getEligibility`; blocker reasons remain visible. Confirming a Stage
9A finding leaves that item open instead of immediately advancing and increments
the finding revision passed to the panel, which triggers a new eligibility and
review-context load.

Eligible findings expose one explicit start action and then separate offline
`Write correction manually` and configured-provider `Suggest wording` paths.
The draft requires an exact backend candidate span or a reviewer-selected
grapheme-boundary insertion point. Existing proposals display:

```text
current target context + highlighted [start,end) span / insertion caret
primary proposal + grapheme-safe diff + alternatives
why / observed meaning / required meaning / failed dimension
source evidence / target location / tN-tW-TWL resources
machine/human provenance / provider metadata / append-only history
```

Alternative selection uses the existing CAS edit API and retains the same
intent/span. Ordinary edits preserve the original provider suggestion.
Revision conflict reloads and informs rather than silently retrying. Rejection
keeps content and history. Regeneration creates a superseding proposal and keeps
the earlier proposal selectable. Stale and newly ineligible proposal states are
prominent and disable current actions.

`unicodeDiff.ts` uses grapheme segmentation for display and explicit Unicode
code-point slicing for persisted coordinates, including supplementary-plane
characters. Insertion, deletion, and replacement are visually distinct.
Evidence/proposal content has a capped independent scroll area; actions are a
sibling sticky region. Long-content fixtures cover 1366 px and 820 px widths,
long Tamil text/evidence/history and 12 alternatives. The existing 1,000-row QA
fixture continues to verify list virtualization.

### Verification

Controlled UI/runtime fixtures demonstrate the quantity proposal/edit/history
flow, a zero-length omission insertion, and PHP 1:3 source evidence attached to
one exact PHP 1:6 target correction span. No test or UI path collapses this to a
same-verse lexical alignment.

```text
Stage 9B.2 focused frontend              68 passed / 6 files
Stage 9B.0 + 9B.1 focused Python        90 passed
Stage 5–9A.4 + Project Report backend   211 passed
Project Report focused frontend          19 passed
Full Python (engine + Greek Room)        707 passed
Full frontend Vitest                     176 passed / 19 files
npm run check                            0 errors / 0 warnings
npm run build                            passed; existing >500 kB chunk warning
cargo test                                7 passed
cargo check                              passed
git diff --check                         passed; line-ending notices only
```

The companion database remains schema v12; there is no migration. Full Stage
9B.1 byte-identity tests continue to pin Scripture/imported USFM/alignment files
across create/edit/reject/regenerate, and the new review-context protocol test
is read-only. The Project QA Report focused and full tests remain green.

Installed/manual limitation: the new panel has not yet been inspected in a real
installed WebView2 window or exercised against a live provider and disposable
project. Production frontend compilation and controlled DOM/runtime acceptance
are complete. Perform that visual/provider pass before release packaging.

Next boundary is Stage 9B.3 only: explicit Apply, strict current-text/proposal
CAS, durable transaction/recovery/rollback and dependent-record staling. It is
not implemented and requires separate approval. Post-edit Stage 6B/7/8 reruns,
`CORRECTED`, and export remain later work.

## Stage 9B.3a — strict persistence/recovery foundation (2026-09-05)

### Boundary

Implemented only the non-mutating foundation approved after the Stage 9B.3
preflight. There is still no `correction.applyProposal`, Rust/Tauri Apply
command, frontend Apply control, strict writer invocation, correction-driven
chapter JSON write, affected Stage 6B-8 run, CORRECTED transition, or export
change. The Project QA Report remains a frozen regression surface.

### Test-first implementation

Added `tests/correction/test_correction_stage9b3a.py` before the implementation. Its first
collection exposed an undeclared `jsonschema` test dependency; the test was
corrected to validate exact canonical-schema fields/enums using the repository's
existing dependency-free schema strategy rather than widening production or
development dependencies.

Database schema advanced from v12 to **v13**. Migration v13:

- creates a pre-migration SQLite backup through the established backup path;
- adds lifecycle/revision columns to immutable token instances and backfills
  their JSON payloads;
- adds historical target-token -> `TARGET_REFERENCE` and target semantic-unit
  -> `TARGET_INVENTORY` edges;
- rebuilds the design-only correction application table with the full exact
  snapshot, state revision, failure/recovery/result metadata, and a uniqueness
  constraint on proposal ID + expected proposal revision;
- preserves legacy application records as non-runnable
  `RECOVERY_REQUIRED` history rather than guessing missing coordinates/hashes.

Python, canonical JSON Schema, Rust serde wire types, and TypeScript wire types
now agree on `CorrectionApplicationState`, `CorrectionApplicationActor`, the
v13 application intent, token-instance currentness, and the frozen
`StrictScriptureEditContext` contract.

Repository operations now atomically prepare application intent plus target
invalidation, retrieve/find/list attempts, enforce legal transitions with
revision CAS, record recovery-required evidence, and prepare a checksummed
semantic database backup linked to the application. Retries in every durable
state return the original attempt. An invalidation-preparation mismatch rolls
the transaction back, leaving no mutation-ready record.

Project startup calls `TranslationCoreProject.recover_incomplete_transactions()`
before constructing `PassageSemanticRuntime`. An incomplete rollback exposes
`RECOVERY_REQUIRED`/`correctionWritesBlocked` without allowing semantic startup
against partial Scripture. Runtime startup then replays prepared invalidations,
synchronizes authoritative current JSON text, and runs the new exact-hash
`CorrectionApplicationRecoveryCoordinator`.

Recovery never writes Scripture. Before-hash attempts are failed as not
committed; intended-after attempts are never applied twice and continue only
through invalidation and idempotent proposal/finding bookkeeping; neither-hash
and journal/invalidation contradictions fail closed into RECOVERY_REQUIRED.
Proposal bookkeeping is itself restart-safe: a crash after stamping applied
metadata but before completing the application ledger does not stamp it twice.
Application records set verification PENDING and leave the finding disposition
unchanged; applied still does not mean CORRECTED. No semantic failure rolls
back already committed Scripture.

Target invalidation now stales target token instances directly and target
semantic units through their inventory dependency, retaining both as history.
Source token instances remain current. Exact content-addressed inventory
rebuilds may reactivate only the exact old token/unit identities. The common
dependency invariant was widened to scan all edge-writing package modules.

Unicode snapshot tests cover Tamil combining marks, Hebrew niqqud/cantillation,
Greek precomposed/decomposed forms, supplementary-plane characters, and a
zero-length insertion. PHP source provenance 1:3 and editable target 1:6 remain
separate, and no lexical group is created. A byte-hash guard proves all 9B.3a
operations leave chapter JSON, preserved imported USFM, and translationCore
alignment data unchanged.

The full suite found one unrelated Windows checkout defect: the Strong's
`PROVENANCE.json` files are hash-verified at runtime, but their lexicon
directories were missing the `-text -diff` protection already used for UHB and
UGNT. Git therefore checked the LF blobs out as CRLF and both resource hashes
failed. `.gitattributes` now preserves both vendored Strong's trees byte-for-
byte; a regression test pins those rules. The normalized files exactly match
their committed content and expected checksums.

### Verification

```text
Stage 9B.3a focused Python               34 passed
Stage 9B.0 + 9B.1 Python                 90 passed
foundation/runtime Python                61 passed
Stage 5-8 Python                         90 passed
Stage 9A.4 + Project QA Report Python   126 passed
frontend Vitest                         176 passed / 19 files
npm run check                            0 errors / 0 warnings
npm run build                            passed; existing >500 kB chunk warning
cargo test                                7 passed
cargo check                              passed
git diff --check                         passed; line-ending notices only
full Python + Greek Room                742 passed in 21m29s
```

No installed acceptance run was required because this checkpoint has no
user-facing Apply action and must not mutate Scripture.

### Remaining Stage 9B.3b blockers

`TranslationCoreProject.apply_scripture_edit()` still does not accept the
strict context. Stage 9B.3b must add exact proposal/finding/current-target CAS,
one-verse/one-contiguous-span application, re-evaluate correction eligibility
immediately before mutation, create the backup and prepared invalidation first,
then reuse that canonical tC writer and its alignment/word-bank/edit-audit
behavior. The composed final verse must not be stripped. Stage 9B.3a exposes no
application protocol, so explicit human Apply, duplicate-submit orchestration,
and installed acceptance remain for Stage 9B.3b. Affected analysis and positive
verification remain later boundaries unless separately approved.

## Stage 9B.3b — strict explicit-human correction application (2026-09-05)

Implemented the first authorized correction write on schema v13. The new
application service owns eligibility, CAS and idempotency orchestration but
does not write project files. It prepares the ledger, invalidation and semantic
backup, then invokes the existing `BridgeEngine.edit_verse()` →
`TranslationCoreProject.apply_scripture_edit()` path with a strict context.
Ordinary manual-edit trimming is unchanged; strict correction output is exact.

Strict mode checks the current full verse/hash/revision and exact code-point
span/original text, rejects relocation, reuses the prepared invalidation, and
links the translationCore journal to the application/proposal/final hash. The
existing alignment reconciliation, invalid marker, completed removal,
wordBank behavior, tN/tW verseEdits, audit, backup and rollback remain intact.

`correction.applyProposal` and `correction.getApplicationStatus` now cross
Python, Rust/Tauri and TypeScript. The UI requires reviewed wording and a
separate confirmation with CURRENT, PROPOSED FINAL, Unicode-safe diff, exact
target/span, and alignment/pending-verification warnings.

Success reaches COMPLETED, stales dependent semantic/QA records, leaves
verification PENDING and preserves CONFIRMED_TRANSLATION_ERROR. It starts no
analysis and never writes CORRECTED. Duplicate requests resume/return the
proposal-revision application and cannot write twice.

```text
Stage 9B.3b focused Python                9 passed
Stage 9B.3a + corrected historical guard 44 passed
frontend Vitest                         177 passed / 19 files
npm run check                            0 errors / 0 warnings
npm run build                            passed; existing >500 kB warning
cargo test                                7 passed
cargo check                              passed
full Python + Greek Room                750 passed, 1 obsolete guard failed
obsolete guard rerun                    passed (included in focused 44)
git diff --check                         passed; line-ending notices only
```

The full-suite failure was the obsolete Stage 9B.1 assertion that the Apply
protocol must not exist. Stage 9B.3b intentionally introduces it; the updated
guard still proves the Stage 9B.1 wording service has no writer. Installed
acceptance remains required on a disposable project. Stage 9B.3c affected
analysis, verification verdicts, CORRECTED, export and multi-verse correction
remain out of scope.

## Stage 9B.3c — affected re-analysis after human Apply (2026-09-07)

Implemented explicit, correction-aware affected re-analysis on baseline
`4e5a94a` while retaining schema v13. `correction.applyProposal` still stops at
COMPLETED application, STALE historical finding and PENDING verification; it
does not start analysis.

The new `CorrectionAffectedScopeResolver` derives source obligation context
and the edited target from durable repository records. It maps canonical
references through the existing versification model, expands endpoints via
the current-text/USFM-structure overlay, and rejects scopes that fail to
contain both semantic provenance and the edited target. Current managed
chapter JSON remains authoritative for every target hash and target semantic
input. In the PHP regression, source `PHP 1:3` and target `PHP 1:6` remain
separate and resolve to the structural range `PHP 1:3–1:6`.

`CorrectionAffectedAnalysisService` delegates to the existing Stage 9A.4
`AnalysisJobManager`; no second Stage 5–8 pipeline was introduced. The normal
analysis fingerprint/cache path reuses the current source inventory and
rebuilds content-dependent target/location/meaning/QA records. A CAS-protected
v13 metadata ledger links application and analysis job with actor/time,
resolved refs/range, target revision/hash and analysis fingerprint. It returns
an equivalent running or completed job, requires explicit retry after a
technical failure/cancellation/incomplete search, and rediscovers durable jobs
after restart. Public `analysisJob.start` rejects manufactured private
correction scopes.

The correction UI now shows application, verification and affected-analysis
states independently, provides explicit run/retry/cancel actions, restores
persisted job state, uses the existing stage-level progress, and refreshes the
current QA scope after completion. Relationship evidence exposes independent
source/target refs, grouped cardinality and properties; null endpoints are not
called omissions or additions. The read-only application inspector reports
the associated affected job and resolved scope.

Safety tests prove analysis does not rewrite Scripture or re-apply the
correction, uses the Bridge-managed runtime copy, preserves historical finding
state and PENDING verification, and keeps Word Alignment independent. Existing
alignment tests remain the authoritative grouped-cardinality/invariant matrix;
the Stage 9B.3c frontend covers all six displayed cardinalities plus a
cross-verse composite case.

The installed-acceptance procedure is documented in `HANDOFF.md` §37.5. A
fresh disposable source fixture was generated locally at
`C:\Users\Benz\Bridge-Test-Projects\stage9b3c-affected` (12 current QA
findings, 28 location relationships, including 12 cross-verse relationships).
It must be imported so the desktop pass runs against Bridge's managed copy.
The fixture is not committed, and unit/integration tests do not claim that the
installed WebView2 acceptance has been performed.

```text
Stage 9B.3c focused Python                9 passed
Stage 9B.3b focused Python               13 passed
full Python + Greek Room                765 passed
frontend Vitest                         192 passed / 19 files
npm run check                            0 errors / 0 warnings
npm run build                            passed; existing >500 kB warning
cargo test                                7 passed
cargo check                              passed
git diff --check                         passed; line-ending notices only
```

Stage 9B.4 remains forbidden without explicit approval. In particular, Stage
9B.3c never changes verification from PENDING, never writes CORRECTED, and
never treats disappearance of a current finding as proof of correction.

## Stage 9B.4 — positive semantic verification and explicit CORRECTED (2026-09-07)

Implemented on baseline `6631a07` (0.9.1). This closes the Stage 9B pipeline.
`correction.applyProposal` still stops at COMPLETED, Stage 9B.3c re-analysis
still leaves verification PENDING, and neither of them can reach CORRECTED.
What is new is a backend service that decides PASSED/FAILED/UNCERTAIN from
current post-correction evidence, and a separate explicit human action that is
the only way a finding becomes CORRECTED.

### The thing this stage exists to prevent

The tempting implementation is "re-run QA; if the finding is gone, the
correction worked". That is wrong twice over, and both halves are now covered
by tests. A finding can disappear because the search was incomplete, because
the target inventory changed shape, or because Stage 8's gate landed on
`UNCERTAIN` instead of `POSSIBLY_MISSING` — none of which says the required
meaning is now expressed. And a finding can *recur with the same stable id*
while the correction is entirely successful, because ids are keyed on kind +
direction + dimension + source units + target anchors and survive re-runs by
design (that stability was Stage 9A.1's whole point).

So verification traces the *original failed obligation* — the proposal
intent's `failedDimension`, `observedMeaning`, `requiredMeaning` and affected
source semantic unit ids — and asks whether current Stage 6B/7/8 evidence
positively satisfies it.

### Where the evidence comes from

The affected-analysis job the application is linked to carries the run ids for
this correction's Stage 6B/7/8 pass in `stageStatuses.{LOCATION,MEANING,QA}`.
Verification reads those runs and nothing else. That turned out to be free
staleness protection: `semantic_location_run`, `meaning_analysis_run` and
`qa_audit_run` all already refuse a non-ACTIVE row, so evidence superseded by
a later edit cannot be read as current at all — it surfaces as a
`STALE_EVIDENCE` conflict rather than quietly producing a verdict.

Currentness is additionally hash-exact: the job's recorded
`targetHashes[reference]` must equal the live hash of the chapter JSON verse.
That is what makes "the reviewer edited the verse again after verifying" a
detected condition rather than a silent stale pass.

### A gap in Stage 7 that forced a design decision

Stage 7 scores a source unit only on that unit's own `coverageDimension`. But
the reviewer picks the failed dimension when authoring the correction, and the
two need not match — the Stage 9B.2 draft form offers all eleven dimensions
against whatever unit the finding names. A QUANTITY correction recorded
against a `LEXICAL_CONTENT` unit therefore has **no quantity component in the
persisted assessment at all**, and a verifier that only read components would
return UNCERTAIN for most of the dimension matrix.

Two options: read the aggregate meaning status instead (too coarse — it cannot
distinguish which dimension survived), or re-apply the existing comparator for
the specific dimension. Chose the second, with a hard constraint: it must not
become a second meaning engine. So it is literally
`DeterministicMeaningComparator.compare(...)` — the same class, same version —
called with the corrected dimension over the current located source text and
the current selected candidate's quotes, and it **never overrides** a persisted
component. Where the two disagree the result is UNCERTAIN with
`CONFLICTING_CURRENT_ASSESSMENT`. Abstention, not arbitration.

`test_a_dimension_stage_seven_never_scored_is_still_verified` is the case that
motivated it: components hold only `LEXICAL_CONTENT: PRESERVED`, the
correction targeted QUANTITY, source `all` versus target `சிலர்`, and
verification correctly returns FAILED off the recheck alone.

### Why schema v14 rather than v13 metadata

The brief preferred v13 and asked for the invariant if v14 was needed. The
invariant is:

> At most one verification may exist for a given (application, analysis job,
> target content hash, verifier fingerprint), and a repeated request must
> return that same record.

Verification is a button. A reviewer can double-click it while a
read-modify-write of `correction_application_intents.result_metadata_json` is
in flight, and an append into a JSON blob duplicates under exactly that
concurrency. v13 already solved the identical problem for applications with
`UNIQUE(proposal_id, expected_proposal_revision)`; this is the same shape.
Two further needs are structural, not cosmetic: acknowledgement takes CAS on a
`revision` column so "mark corrected" fails closed against a verification that
went stale between render and click, and superseded verifications must stay
queryable per application as history.

`_MIGRATION_V14` adds one table and nothing else — no existing row is
rewritten, so a v13 project upgrades additively. One implementation detail
worth recording: the primary key column is named `id`, not `verification_id`,
because `_stale_generic_dependencies` addresses every record table as
`WHERE id=?`. Naming it otherwise compiled and passed its own tests but would
have thrown mid-propagation on the first target edit that reached it.
`CORRECTION_VERIFICATION` is registered in `RECORD_DEPENDENCY_TABLES`, which
the existing source-scanning invariant test enforces.

### Policy shape

`CorrectionVerificationPolicy` decides per obligation, then aggregates (any
FAILED wins, else any UNCERTAIN, else PASSED). Run-level caution is applied
last:

- `SEARCH_INCOMPLETE` downgrades any PASSED. An incomplete search cannot
  license a positive claim, and the reverse rule — search failure must never
  become a false absence — is why a coverage-based FAILED also becomes
  UNCERTAIN under it. A *located deterministic contradiction* is unaffected:
  search completeness has no bearing on it.
- `PROVIDER_LIMITED` (the standing "no production multilingual embedding
  provider" warning) is recorded and lowers confidence, but downgrades a
  PASSED only when the deciding evidence is non-deterministic. This is the
  brief's §9 rule made concrete: inspect the warning, do not map it to
  UNCERTAIN. `test_completed_with_warnings_can_still_pass_on_deterministic_evidence`
  pins a quantity pass under that exact warning.

Null semantics are preserved on both sides. `1 -> null` with
`GRAMMATICALLY_REALIZED`/`IMPLICIT`/restructured realization and
`COVERED_BY_RESTRUCTURING` coverage passes; it fails only when Stage 8
positively concluded the obligation is uncovered. `null -> 1` checks target
units *inside the exact corrected span* against their current target-support
account, and treats `SOURCE_SUPPORTED`/`CONTEXT_SUPPORTED`/
`GRAMMATICALLY_REQUIRED`/`EXPLICITATION_SUPPORTED` as legitimate.

### A Stage 7 defect surfaced by writing the dimension matrix

Building the POLARITY row failed on a pairing that should obviously pass. The
cause is in `meaning_analysis._comparison_norm`, which does
`re.findall(r"[^\W_]+")` over NFD-decomposed text. Indic combining marks are
not alphanumeric, so a Tamil word is **split at every virama and vowel sign**
and the marks are discarded: `இல்லை` becomes `இல ல`, two tokens. The
docstring explicitly claims Tamil vowel signs "remain intact". They do not.

`_category` survives this because it substring-matches, so QUANTITY, TEMPORAL
and PARTICIPANT still work. The POLARITY branch does not — it tests whole
tokens with `item in target.split()` — so it cannot see the negative in
`இல்லை` and returns `CONTRADICTED` against a Greek negative. A false
contradiction, on this project's primary target language.

Deliberately not fixed here. §39 forbids Stage 9B.4 re-judging Stage 7
meaning, and a fix changes Stage 7 and Stage 8 verdicts and needs both goldens
re-baselined. Instead the behavior is pinned by
`test_tamil_negation_polarity_limit_is_pinned_not_worked_around`, which
asserts the comparator's current output *and* that verification faithfully
reports the resulting disagreement as UNCERTAIN rather than papering over it.
When Stage 7 is fixed, that test fails on purpose. **This should be
scheduled.**

### UI

`CorrectionReviewPanel` gained a Correction verification section under the
existing affected-analysis block. The status list now shows four independent
rows — correction application, affected analysis, semantic verification, your
decision — and never merges them. Wording is deliberate: FAILED says the
original obligation is still unsatisfied and that what to do next is the
reviewer's call; UNCERTAIN says Bridge cannot tell and explicitly does not
call the translation wrong. Neither auto-undoes Scripture or reopens the
proposal.

**Mark correction as corrected** appears only for a current PASSED
verification, behind a confirmation dialog carrying the original issue, the
applied correction, the verification result, the source semantic reference,
the target realization, the affected dimension and the current supporting
evidence, with Mark corrected / Cancel — never a generic Save. A frontend test
greps the component source to assert it contains no client-side verdict
computation.

One collision worth noting: adding an `<h5>Semantic verification</h5>` heading
broke six existing tests that resolve the status row by
`getByText("Semantic verification").closest("dl")`. Renamed the heading to
"Correction verification", which also reads better — it names what is being
verified.

### Safety

Scripture, imported USFM and alignment JSON are hashed before and after the
whole verify -> acknowledge flow and asserted byte-identical. PHP 1:6 Word
Alignment remains invalid/reviewable after a PASSED, acknowledged correction —
that is allowed and tested. CORRECTED does not immunize the finding: a later
edit makes the verification non-current and reports the acknowledgement as
`current: false` while retaining the historical CORRECTED event in full.

```text
Stage 9B.4 focused Python                55 passed
Stage 9B.3b + 9B.3c focused Python       22 passed
Stage 9B.0/9B.1/9B.3a + 9A review       222 passed (re-run after the v14 bump)
full Python + Greek Room                921 passed
Correction Review frontend               51 passed (33 existing + 18 new)
Full frontend Vitest                    306 passed / 24 files
npm run check                             0 errors / 0 warnings
npm run build                            passed; existing >500 kB chunk warning
cargo test                               12 passed
cargo check                              passed
git diff --check                         passed; line-ending notices only
```

The v14 bump correctly tripped seven pre-existing assertions that hardcoded
`== 13` across `test_correction_stage9b0/9b1/9b3a` and `test_qa_review_stage9a`
-- they pin "the current schema", so they were updated to 14 rather than
loosened. A dedicated `test_v13_to_v14_migration_is_additive_and_keeps_v13_data_readable`
was added covering a real v13 database with a finding, an applied proposal and
append-only proposal history: everything stays readable at its original
revision, no row is rewritten, the new table starts empty, and a v13 project
with no verification history reads as PENDING rather than as an error.

Installed desktop acceptance has **not** been performed and is not claimed by
the suite; the procedure is in `HANDOFF.md` §37.6. The post-9B.4 cross-verse
visualization and v1 UI pass remain unauthorized.

## Beta 15 — Stage 9B.3c responsive QA review acceptance fix (2026-09-07)

Installed acceptance showed the full `QaFindingDetail` decision footer
obscuring evidence, especially around 1366×500 and shorter content areas. The
root cause was `position: sticky; bottom: 0` on the entire decision form inside
the detail scroller. Its textarea, optional promotion control, explanatory
copy and wrapped action buttons occupied a large sticky layer. Correction
Review also imposed a nested 24/19rem scrolling region with sticky actions.

The detail pane is now the single vertical scroll owner. Evidence and history
come first, the complete decision form follows in normal flow, and Correction
Review follows the decision. Correction evidence/actions and draft editing
also use normal flow; only the independent confirmation modal keeps a bounded
scroll. Text containers remain width-constrained/wrapping and the detail pane
clips horizontal overflow. No decision, correction, affected-analysis, QA, or
verification semantics changed.

Automated layout coverage renders long Tamil text and long resource evidence
at 1920×760, 1366×768, 1366×500, 1550×350, and 820×768. It asserts all evidence
headings remain present in review order, History/decision/Correction remain
reachable, decision and correction actions are non-sticky, one vertical
scroll owner is used, keyboard focus advances normally, and horizontal
overflow is contained.

All active build metadata is now `0.8.0-beta.15`, including npm lock metadata,
Cargo lock metadata, Tauri config, Python package/engine identity, Greek Room,
project-import generator metadata, provider user-agent and frozen smoke
expectations. Frozen `engine.info` and the installed sidecar both report Beta
15. The broad sidecar smoke continues to stop at the already documented
duplicate-import classification mismatch (`exactDuplicate` expected versus
`possibleDuplicate` returned); it passed its version gate before that point.

```text
Focused QA/correction/layout frontend    65 passed / 3 files
Full frontend                           242 passed / 23 files
npm run check                            0 errors / 0 warnings
npm run build                            passed; existing >500 kB warning
cargo test                                7 passed
cargo check                              passed
Tauri release + NSIS                    passed
Installed application version           0.8.0-beta.15
git diff --check                         passed; line-ending notices only
```

Installer:
`src-tauri/target/release/bundle/nsis/Bridge_0.8.0-beta.15_x64-setup.exe`
(55,732,149 bytes; SHA-256
`EF666F73475D4609D53B9F9D22AD4BF7A651F7D279584DA130D0254C93DF8DAD`).
Stage 9B.3c installed acceptance may resume. Stage 9B.4 remains unauthorized.

## AI triage — false-positive scoring for Greek Room findings (2026-09-07)

Greek Room says what is *objectively* suspicious, which by design includes a
lot that is fine: punctuation correct for the language, real reduplication,
an inflecting proper noun. This adds an optional, **online-only** pass that
scores how likely each already-persisted finding is to be a false positive,
and a slider on the QA report that hides the noisiest ones.

Strictly an overlay. No check, report, decision or export path reads a
verdict; a `QaFinding` is never rewritten; with no API key and no stored
verdicts the report screen renders exactly as it did before. That is the
local-first constraint, and the component test
`renders exactly as before when no triage has ever run` pins it.

### Two questions answered before any code

**Was the report UI actually built?** Yes — `ProjectReportScreen.svelte`,
`report.generate/status/get/cancel/export`, `qa_report.py`, `report_jobs.py`.
The slider depends on it, so this was checked first rather than assumed.

**Did the `reasoning.effort` guard exist?** Yes, under a different name:
`OpenAIResponsesClient._model_supports_reasoning_effort()`
(`ai_client.py:126`) gates the parameter to `o1`/`o3`/`o4`/`gpt-5` prefixes,
with a 400-response fallback at `:175` and a test at
`test_ai_client_compatibility.py:22`. No separate guard commit was needed.

### Storage: one JSON file per book, not per chapter, and not SQLite

The user asked whether this and the report's other inputs should move to a
database. Measured first, on the real 66-book collections under
`%LOCALAPPDATA%\Bridge\data\projects`:

| Probe | Result |
|---|---|
| First open of any small JSON file (Windows) | **~20–27 ms**, size-independent; second open ~0.1 ms |
| 1,189 tC index files (11.2 MB) | 23.7 s cold, 146 ms warm |
| `build_book_qa_report`, Judges (618 verses, 4,483 rows) | tC index read **8.8 s cold → 85 ms warm**; everything else ~1 s cold |
| Semantic DB open + `PRAGMA integrity_check` (35 MB) | 15 ms + **817 ms**, on every `FoundationRepository` construction |

File *count* dominates, not bytes — the on-access-scan signature. So triage
stores one `.apps/translationCoreAI/triage/<book>.json` per book: 66 files
read in ~2 s where 1,189 per-chapter files would take ~25–30 s.

SQLite was rejected for this, despite already shipping in Bridge
(`passageSemantic/bridge-semantic.sqlite3`, schema v13). The companion
directory is **per book**, so "one query for the collection" does not exist —
every option is 66 stores. Joining the existing DB would additionally inherit
the passage-semantic runtime's `RECOVERY_REQUIRED`/read-only states, a v14
migration with a full-DB backup per book, and that 817 ms integrity check per
open. A separate small `.sqlite3` would be ~2x the work for the same 66 files.
The one measured SQLite number in this repo also points away from it: Stage 8
is 81–92% commit time because of a connection-per-save pattern (see the Stage
8 profiling table earlier in this log).

**On moving the report's inputs to a DB: no.** Four of the report's nine
sources (`index/{tN,tW}`, `checkData/{selections,verseEdits}`,
`tools/wordAlignment`, `alignmentData`) are translationCore's own format,
read live by design, and are also the measured hot spot — a Bridge-owned
index of the other five removes under 10% of cold time while creating a
second source of truth. Worse, there is no cheap staleness check when tC,
Paratext or a `git pull` edits the project outside Bridge: NTFS directory
mtimes change only for direct children, so noticing a new selection file
means stat-ing all ~31k verse directories — the walk the index was meant to
avoid. Only **Tier 1 (measure)** was done here.

### Tier 1: the report now records how long it took

`build_book_qa_report` records `durationMs`, and the job snapshot carries
`bookDurationsMs`/`totalDurationMs`. Nothing in this repo had ever measured
report generation — the 2026-09-04 report entry records test counts only, and
`QA_TEST_MATRIX` D11 (installed-app report generation) is still NOT RUN — so
there was no baseline any optimisation could have been argued against.
Measuring first is the whole change.

### Design points worth keeping

**The triage hash is not the finding id.** Records are keyed by a sha256 of
book/chapter/verse/check-type plus NFC- and whitespace-normalised evidence
(`original_text`, `suggested_replacement`, `explanation`, evidence pairs).
Deliberately *not* `_stable_finding_id`, whose entire purpose is to stay
stable across an edit so a human decision survives — whereas a verdict about
evidence that changed is worthless and must be discarded. Offsets are
excluded, so text shifting inside a verse does not orphan every verdict in
it. `chapter`/`verse` come from the snapshot's *row keys*, which preserve
verse bridges (`3-4`) and segments (`3a`); `QaFinding`'s own fields are ints
that collapse both (gotcha 12).

**One enumeration path.** `_BookReport._persisted_findings` moved into
`triage.py` and both the report builder and the triage worker call it. Had
they diverged, triage would have bought verdicts for rows the report never
shows, or left visible rows permanently unscored.

**Four prompt families, all reachable.** Routing was written against the
check types the engines actually emit, not invented ones — `usfm.<slug>`
(`usfm_adapter.py:277`), `wildebeest.*` (`:148-224`),
`names.spelling_similarity` (`names_adapter.py:368`),
`alignment.inconsistent_rendering` (`bridge_service.py:2716`), plus
`local_checks.py`'s uppercase codes, which carry a *variable* language prefix
(`TA_`/`LANG_`, `:45`) and so match on substring rather than prefix. Category
alone was not enough: every Wildebeest check carries category `unicode`
whatever it looked at, so category routing would never have reached the
consistency prompt at all. A test asserts every family is reachable.

**`_post_text` split out of `_post_structured`.** Strict `json_schema` is
still sent, but OpenAI-compatible endpoints behind `api_base_url` (vLLM, LM
Studio, Ollama) routinely ignore it and wrap output in markdown fences, so
triage parses raw text itself — fences stripped, `{"results": [...]}`, a bare
array and `{"findings": [...]}` all accepted, and a provider answering
`0.0-1.0` instead of `0-100` rescaled (only strictly between 0 and 1; reading
`1.0` as 100 would be the dangerous direction).

### Two defects found by driving a live sidecar

Unit tests passed and the real protocol path still had bugs — the standing
rule in this repo, again. Driving a real `main.py` with a key configured and
`api_base_url` pointing at a closed port:

1. **Every failure blamed the parser.** A connection refusal stored the reason
   *"The model's response for this batch could not be parsed"* — a string
   shown to the reviewer on the finding, sending them to debug the wrong
   thing. Network and parse failures now say which happened.
2. **A run whose every batch failed reported `succeeded`** with no error,
   because only a failed *book* set one. A reviewer would read that as "every
   finding has been judged" when nothing had. Such a run is now `failed`; a
   partly-failed one stays `succeeded` but carries the failure in its message.

In both cases the findings are still recorded as `uncertain` at confidence 0 —
the one shape the slider can never hide, so a finding nobody judged always
stays visible.

### The hiding rule

Only a `false_positive` verdict at or above the threshold hides anything.
`uncertain` and `true_positive` are always shown, whatever the confidence,
because hiding a real translation error is a far worse failure than leaving a
false positive on screen. Default 90, floor 50, with an off position. A
reviewer's thumbs-down hides regardless of the model's confidence — a human
judgement is definite, not a scored guess — and a thumbs-up always reveals.
An override is never re-sent even under `force`, so overriding is also how a
reviewer stops paying for a finding they have already judged.

Triage hiding is deliberately **not** part of `ReportFilters`: folding it in
would make "Clear filters" silently unhide findings the reviewer chose to
hide, and make the filtered-row count ambiguous about which mechanism removed
a row. Filters run first, so "N hidden" always counts within what the filters
selected, and the hidden set is computed even while revealed so the count
stays truthful.

### Concurrency

`triage.override` (dispatcher thread) and the run worker both load-merge-save
the same book file under one `BridgeEngine._triage_lock`, and the worker
re-reads immediately before merging each batch, so an override recorded while
a request was in flight survives. A test gates a fake client mid-call and
overrides underneath it. This is the same lost-update class
`.bridge/progress.json` still has between `_on_check_job_complete` (worker)
and `_apply_decision_to_progress` (dispatcher) — unchanged here, still open.

The job's cancel event is handed to the per-book callback rather than looked
up after `start()` returns, so a cancel in the first instants of a run cannot
be missed. A cancelled run also skips the prune pass: its picture of which
findings still exist is incomplete, and pruning against it would delete
verdicts it never reached.

### Verification

```text
triage focused Python (module + RPC)    100 passed
full Python + Greek Room                866 passed (765 before this work)
frontend Vitest                         262 passed / 22 files (192 before)
npm run check                             0 errors / 0 warnings
npm run build                           passed; existing >500 kB warning
cargo test                                8 passed (6 before, +2 triage timeout class)
raw stdio smoke, no API key             triage.run -> unavailable; override -> clean error;
                                        report.generate unaffected; sidecar alive
raw stdio smoke, dead endpoint          run -> failed; uncertain/0 records stored with a
                                        network reason; ping still answers; exit 0
```

**NOT run, and not claimed:** live model behaviour (no request has ever been
sent to a real provider — every test and smoke uses an injected callable, a
fake transport or a closed port), so prompt quality and real verdict
distribution are entirely unmeasured; the installed desktop app, so the
slider, thumbs and progress line have never been seen rendered (Vitest uses
jsdom, which does not lay out or paint); and the frozen PyInstaller sidecar,
which needs a `build-sidecars.ps1` run.

### Two follow-ups this deliberately did not do

1. **`checkFindings` snapshots are fat.** ~1.2 KB per finding (~350 MB of
   JSON for a checked Bible), and 55–80% of the rows in the real snapshots are
   `translationCore`-category rows `qa_report` discards at `:353-356`. The
   snapshot writer persists all 24 `QaFinding` keys; the report reads ~9.
   Slimming it (schemaVersion 2, reader tolerating v1) is the real
   Bridge-owned report cost, and is worth doing *after* Tier 1 timings confirm
   it against a real collection.
2. **`_tree_fingerprint` still hashes everything under `.apps/`**
   (`project_registry.py:74-88`), so any Bridge-written file there makes a
   hand-placed project look like a `possibleDuplicate` of itself on
   re-inspect. Flagged 2026-09-02 for `passageSemantic/`, still open; triage
   adds one more file per book of the same class. The fix belongs in
   `_tree_fingerprint`, not in each feature that writes state.


---

## Issue #38 — finding context menu, and closing its keyboard gap (2026-09-07)

Most of issue #38 landed earlier the same day in two commits by Benz, merged
in #41:

| Commit | Scope |
| --- | --- |
| `831f35c` `feat(editor): add contextual finding actions` | `FindingContextMenu.svelte` (new), `VerseList.svelte` wiring, `findingActions.ts` (new), `applySuggestedFindingFix` in `verseEditor.ts` |
| `dec34d5` `feat(qa): add contextual review and correction actions` | `QaFindingList.svelte` dispatches `contextmenu`; `AlignmentQaMode.svelte` handles it via `applyCorrectionProposal`/`decideFinding`; `correctionApplication.ts` (new) |

This session verified those against the issue's scope list, closed the one
criterion that was genuinely unmet, and documented the feature — it had
shipped with no mention anywhere in `docs/` (a `grep` for "context menu" and
"right-click" across `docs/` and `*.md` returned nothing).

### What was already true, verified against the code

- **Trigger.** `VerseList.svelte` binds `on:contextmenu` on the
  `<mark class={piece.seg.className}>` span; `QaFindingList.svelte` binds it on
  `.row` and dispatches `{ id, x, y }` upward.
- **Menu contents.** `AlignmentQaMode.svelte:93` builds its decision items from
  `REVIEWER_ACTIONS` (`src/lib/utils/reviewLabels.ts:241`), the same export the
  review panel uses. No second vocabulary on that side.
- **Disabled, not hidden.** Both call sites emit the `apply` item
  unconditionally with `disabled` plus an explanatory `title`.
- **Same code path as the panel.** `decideLocalFinding`
  (`src/lib/findingActions.ts`) is shared with `ReviewPanel.svelte:68`; the QA
  side reuses `applyCorrectionProposal`/`decideFinding`. Decisions stay keyed by
  stable finding id.
- **Escape / outside click / clamping.** All in `FindingContextMenu.svelte`
  (`onKeydown`, `onOutsidePointer`, `positionInsideViewport`, `EDGE_GAP = 8`),
  with roving arrow-key focus and focus restore on Escape.

### The gap: keyboard parity in the editor

The QA side satisfied it — `QaFindingList.svelte` handles `ContextMenu` and
`Shift+F10` on both the listbox viewport (`:87-99`) and each row, and
advertises `aria-keyshortcuts="Shift+F10"`.

The editor did not, and the panel route the issue offers as the alternative
does not exist there: a `grep` for `applySuggestedFindingFix` across `src/`
returns only `verseEditor.ts`, `VerseList.svelte` and a test —
`ReviewPanel.svelte` has no apply-fix control at all (its open-finding actions
are **Accept and edit** and **Ignore**). The `<mark>` carried
`aria-haspopup="menu"` but had no `tabindex`, no `role` and no keydown handler.
So "Apply proposed fix" was right-click-only — the exact failure mode the issue
calls out.

### What was built (`889e355`)

Approach 1 from the issue's two options — a key binding on the finding —
rather than adding a panel control, because it also gives the keyboard the
*other* three menu actions, not just apply, and it forces the
`aria-haspopup`-on-a-non-interactive-element defect to be resolved rather than
left standing.

The shape mirrors `QaFindingList` deliberately: **the verse row stays the
single tab stop.** Making each `<mark>` focusable would add one tab stop per
finding, so tabbing through a checked chapter would stop on hundreds of words
inside a `role="button"` element. Instead, on the verse row:

- Left/Right walk that verse's underlined findings; the active one gets a
  visible ring (`mark.active-finding`), not colour alone — the underline
  classes already carry the finding's source colour.
- `ContextMenu` / `Shift+F10` open the same menu, anchored from the active
  `<mark>`'s `getBoundingClientRect()` (falling back to the row's), exactly as
  `QaFindingList.svelte:88-99` does.
- `aria-haspopup="menu"` and `aria-keyshortcuts="Shift+F10"` move onto the row,
  which is a real `role="button"` tab stop, and come off the `<mark>`.

`markedFindingIds()` reproduces the same filter and sort `buildSegments` and
`findingNumbers` use (`start_offset`/`end_offset` non-null, `end_offset <=
text.length`, ordered by offset then id), so the keyboard walks the marks in
the order their superscript numbers run. It runs over the *remapped* findings
(footnotes lifted out) for ordering, but the menu is opened against the raw
`findings` entry, because `applySuggestedFindingFix` indexes
`verseTexts[key]` — the raw string.

**One non-obvious Svelte thing.** `activeIndexFor` had to become a reactive
assignment (`$: activeIndexFor = (key, count) => ...`) rather than a plain
function. The `{@const activeFindingId = ...}` inside the `{#each}` calls it,
and Svelte invalidates on the *reference* to `activeIndexFor`, not on variables
read inside a function body — as a plain function, arrow-key presses updated
`activeFindingIndex` and nothing re-rendered. The first version of the arrow
test failed exactly this way (`expected 'alpha' to be 'beta'`).

`setup.ts` now stubs `Element.prototype.scrollTo`. jsdom implements no
scrolling at all, so the method is simply absent; the new tests select a verse,
which makes `VerseList.scrollSelectedToTop` call it and reject out of band.
Vitest reported two unhandled errors alongside passing tests until this was
added.

### Does an applied fix actually clear its underline?

Yes, but by an indirect route worth writing down, because `edit_verse`
(`engine/bridge_service.py:3308`) deliberately does not invalidate the
USFM/names caches — a finding whose text was just corrected can still be
produced by the post-save recheck.

Traced end to end:

1. `applySuggestedFindingFix` calls `setPendingAcceptFinding(finding.id)`, then
   `saveVerseEdit`.
2. `saveVerseEdit` writes the text, runs `bridge.runVerseChecks(["local",
   "greekroom"])`, and replaces `findingsByVerse[key]` with the result. If the
   stale cache re-emits the finding it comes back with the **same** id — the id
   is a sha1 of `chapter:verse:engine:check_type:disambiguator`
   (`_stable_finding_id`), and a byte-identical cached issue produces a
   byte-identical disambiguator — and with status re-applied from
   `qa_decisions_for_verse` (`bridge_service.py:3085`), which is still `open` at
   this point.
3. `saveVerseEdit` then calls the hook `ReviewPanel` registered via
   `setVerseEditSavedHook`, which calls `decide(acceptFindingId, "accepted")`.
4. `decideLocalFinding` persists and flips the store entry to `accepted`, and
   `VerseList`'s `highlightFindings` filter drops `accepted` — the underline
   clears.

So the underline can flash back for one round trip before clearing, and the
clearing depends on `ReviewPanel` being mounted (`App.svelte:1056-1058` — it
always is, as a sibling of `VerseList`) and on `$selectedVerse` still matching
when the hook fires. `VerseList.openFindingMenu` calls `onSelect(verse)` before
opening, so it matches — **unless the reviewer navigates to another verse
during the save**, in which case the accept is skipped silently and a stale
underline survives until the project is reopened. Narrow, real, and not fixed
here: it is a wart in the `edit_verse` cache limitation, not in the context
menu. Matrix row M40 covers verifying it in the installed app.

### Vocabulary: two opposite senses of "accept"

`VerseList` writes engine `FindingStatus` values (`accepted` / `rejected` /
`needs_discussion`); the QA queue writes `QaDisposition`
(`CONFIRMED_TRANSLATION_ERROR` / `ACCEPTABLE_TRANSLATION` / ...). Two models
over two data sources, so this is not the "second vocabulary" the issue warns
against — but the *labels a reviewer reads* do collide: **Accept finding**
(editor: "this is a real problem") reads as the opposite of **Accept
translation as correct** (queue: "there is no problem here").

Not renamed. The editor's `accepted` already surfaces as **Accept and edit**
and an **Accepted (n)** section in `ReviewPanel`, and renaming only the menu
item would break the panel's internal consistency to fix a cross-view one.
Instead each of the three decision items gained a `title` hint saying which way
it points, reusing the queue's own wording where the meaning matches
("Defer this for the team to decide."). `FindingContextMenu` already renders
`title`. The manual calls the collision out explicitly, and matrix row M41
covers it. A true reconciliation is a bigger, separate call.

Two smaller observations, left alone: `rejected` and `needs_discussion` have no
distinct rendering in `ReviewPanel` — a finding in either state falls into
`grOpenFindings` and shows a generic `badge-decided` badge with the raw status
string; and `USER_MANUAL.md` section 6.4's "Decide: Accept, Reject, Ignore, or
Edit verse" already did not match the panel's actual buttons before this
session.

### Verification

```text
frontend Vitest                285 passed / 24 files (279 before, +6)
npm run check                    0 errors / 0 warnings
```

Nothing under `engine/` was touched, so `pytest` was not re-run.

**NOT run, and not claimed:** the installed desktop app. Vitest uses jsdom,
which does not lay out or paint, so `positionInsideViewport` near a real window
edge, the active-finding ring, focus restore on Escape, and the apply →
re-check → underline-clears sequence have never been seen rendered. Matrix rows
M37-M41 exist for exactly that.

### Follow-up the same day: two actions, not four (`afefbbc`)

User review of the shipped menu, against a real Hindi Ephesians project:
`Apply proposed fix` and `Accept finding` are the same act to a reviewer,
and `Reject finding` / `Needs discussion` were noise. The menu is now
exactly what `ReviewPanel` offers on an open Greek Room finding:

- **Accept finding** — applies the proposed correction through the existing
  `applySuggestedFindingFix` (edit → save → re-check → the save hook files the
  accept), or, when the check proposed no replacement, records the accept
  directly and leaves the verse text alone. The `title` hint says which of the
  two it is about to do.
- **Ignore** — `decideLocalFinding(..., "ignored")`, which moves the finding
  into the panel's **Ignored** accordion and drops its underline (the
  `highlightFindings` filter already excludes `ignored`).

Removing the separate apply item also disposes of the awkward case the
original issue tried to legislate for ("show it disabled rather than hiding it
so the menu doesn't shift position"): with two items that are always enabled,
the menu is a fixed size for every finding. That issue-#38 bullet is therefore
satisfied in effect but no longer literally — there is nothing left to grey
out. Worth knowing before someone "restores" it.

**A failed correction is not silently accepted.** `applySuggestedFindingFix`
already refuses a stale fix (`original_text` no longer matching the span) and
an edit that cannot start; on any of those the reason is shown and the menu
stays open rather than falling through to a plain accept. Tested.

`rejected` and `needs_discussion` remain valid `FindingStatus` values in the
engine (`greek_room_engine/models/finding.py:17-23`) — nothing writes them from
the UI now, which is the status quo ante for `rejected`: `ReviewPanel` never
had a control for either, and rendered both as an open finding with a raw
`badge-decided` badge.

#### A real bug found while rewriting the handler

The decide path was passing `String(finding.chapter)` / `String(finding.verse)`.
Those are **numeric anchors** — `_qaissue_to_finding` takes the first numeric
component (`bridge_service.py:238`) precisely because USFM verse bridges
(`\v 3-4`) and segments (`3a`) are real input. So on a bridged verse the menu
filed its decision under `"3"`, while `ReviewPanel.decide()` — which uses
`$currentChapter`/`$selectedVerse`, the exact displayed strings — filed the
same decision under `"3-4"`. Two routes to the same decision, two different
keys, and the local `findingsByVerse` update silently missed because the store
key `"1:3"` does not exist.

Fixed by carrying the exact verse on the `contextMenu` state and keying off
`$currentChapter` + that verse. This is issue #38's own "must go through the
same code that the review panel uses so decisions stay keyed by stable finding
id" criterion — the finding id was stable, but the verse it was filed under was
not. Matrix row M42 covers verifying it against a bridged book.

**Still unfixed, and safe:** `applySuggestedFindingFix` (`verseEditor.ts:137-139`)
has the same `String(finding.chapter)`/`String(finding.verse)` pattern for its
own `verseTexts` lookup. On a bridged verse that lookup misses and it returns
"The verse text is no longer loaded." — it fails loudly and writes nothing,
rather than editing the wrong verse, so it was left alone rather than widened
into this change.

#### Verification

```text
frontend Vitest                288 passed / 24 files (285 before, +3 net;
                               2 menu tests replaced by 5)
npm run check                    0 errors / 0 warnings
npm run build                  passed; existing >500 kB warning
```

Still not run: the installed desktop app. Nothing under `engine/` was touched.

---

## The sidecar was never stopped when the app exited (2026-09-07)

Reported as `npm run tauri dev` failing to build. The message points nowhere
near the cause:

```text
error: failed to run custom build command for `translationcore-ai-bridge`
  cargo:rerun-if-changed=binaries\bridge-engine-x86_64-pc-windows-msvc.exe
  thread 'main' panicked at tauri-build-2.6.3/src/lib.rs:80:30:
  called `Result::unwrap()` on an `Err` value:
      Os { code: 5, kind: PermissionDenied, message: "Access is denied." }
```

`tauri-build` copies each `externalBin` into `target/`, and Windows refuses to
overwrite a running executable. Two orphaned `bridge-engine.exe` processes were
holding `target\debug\bridge-engine.exe` open — one the PyInstaller bootloader,
the other the Python child it spawns. The parent app was already gone. Freshly
built sidecars (14:24) plus a locked destination copy (12:00) meant every
subsequent build failed until the processes were killed.

Nothing in `src-tauri/src/` ever stopped the sidecar. There was no `.kill()`
anywhere, no window-event handler, and `main.rs` went straight from
`.invoke_handler(...)` to `.run(tauri::generate_context!())`. `CommandChild`
just sat in app state and was dropped with the process.

### Closing stdin, not killing

`EngineSidecar::shutdown` is called from a new `RunEvent::Exit` arm. It is
synchronous and runs on the main thread — there is no async runtime left to
await on at that point, so it uses `Mutex::blocking_lock`.

The mechanism is **closing stdin**, not killing. `run_stdio_loop` iterates
`for line in sys.stdin` (`transport/stdio_transport.py:41`), so EOF ends the
loop and Python returns from `main` on its own — which also lets the
PyInstaller bootloader delete its `_MEI…` temp directory. A forced kill leaks
one of those per run. There is no explicit close in the plugin API: dropping
`CommandChild` drops the `stdin_writer` it owns (`tauri-plugin-shell-2.3.5`,
`src/process/mod.rs:65-68`), and that is what closes the pipe.

The forced kill after a 1.5s grace period is a backstop, not the plan. The
engine only reaches the read loop *between* requests, so a sidecar inside a
long `handle_request` — the isolated USFM checker allows itself 120s — would
otherwise outlive the window the user just closed.

**The backstop has to kill the tree.** `CommandChild::kill()` calls
`SharedChild::kill()` → `TerminateProcess` on the PyInstaller bootloader alone,
leaving the real Python child running and still holding the handle on
`bridge-engine.exe`. That is exactly the orphan pair this bug produced, so
using `kill()` would have reproduced the failure rather than fixed it. Hence
`taskkill /PID <pid> /T /F`, spawned with `CREATE_NO_WINDOW` so a release build
(`windows_subsystem = "windows"`, no console) does not flash one up as it dies.
Its return value is meaningful: a non-zero status is the *expected* result of a
clean exit, because the pid is already gone, and that is what distinguishes
"exited on stdin close" from "forced after the grace period" in the log.

An exit while `shutting_down` is set now logs at `info` rather than `error`, so
a normal close stops filing a fault in the diagnostics panel.

### Verified against the running app, not just compiled

```text
cargo test                     10 passed (8 before, +2 kill_process_tree)
window close (CloseMainWindow) app 26484 -> bootloader 21920 -> python 22396
                               all gone; log: "Sidecar stopped on app exit
                               (pid 21920, exited on stdin close)"
hard kill of the app process   no bridge-engine remains
```

The hard-kill result was a surprise worth recording: killing the app closes its
pipe handles, which gives the sidecar the same EOF, so even an abrupt death
usually cleans up. That narrows what can actually orphan a sidecar to **a
sidecar that is not reading stdin** — i.e. one busy inside a long request when
the app dies without reaching `RunEvent::Exit`. That is the one case the exit
hook cannot cover, and the likely origin of the pair found today. Matrix row
M44 records it; a preflight in `scripts/dev_desktop.mjs` (which already runs
before cargo builds, via `beforeDevCommand`) would close it, and was not done
here.

The two `kill_process_tree` tests spawn `cmd /c ping -n 30 127.0.0.1` rather
than `timeout` — `timeout.exe` aborts immediately when stdin is redirected, so
the first version of the test killed a process that had already exited and
failed on its own assertion.

### Unrelated to the frontend work

Nothing in this touches the Svelte tree; the same session's context-menu
changes compiled and tested clean throughout. Worth stating because the report
arrived as "error on running the app" immediately after a frontend change.

## `correction.applyProposal` sidecar timeout (2026-09-07)

`request_timeout_seconds()` in `src-tauri/src/sidecar.rs` had no arm for
`correction.applyProposal`, so Stage 9B.3b's Scripture-changing apply ran on
the `_ => 30` default. Confirmed by reading the table, not assumed: every
other expensive method has an explicit commented arm, including the two
sibling correction methods added in the same stage at 260s.

Verified what the call actually does before picking a number.
`CorrectionApplicationService.apply()` persists the application intent and
PREPARED invalidation, then `record_application_backup()` →
`PassageSemanticRepository.backup()`, which is a full `sqlite3` backup of the
project's semantic DB followed by `hashlib.sha256(backup_db.read_bytes())` —
a whole-file read into memory. It then delegates to `edit_verse()` →
`apply_scripture_edit()` for translationCore reconciliation, marker handling,
word-bank movement, the verse-edit audit, tN/tW `verseEdits`, a filesystem
backup and a journal write.

Measured rather than guessed, driving the real service through the Stage
9B.3b fixture (`_fixture`/`_apply` in `tests/correction/test_correction_stage9b3b.py`)
with the semantic DB padded to a range of sizes. `scripts/inspect_correction_application.py`
was not usable for this: it is a read-only inspector for an already-applied
correction on an installed project and times nothing.

```text
db size      apply     backup   applicationState
 0.57MB     0.447s     0.017s   COMPLETED
 8.59MB     0.953s     0.090s   COMPLETED
28.61MB     1.205s     0.222s   COMPLETED
100.71MB    1.220s     0.533s   COMPLETED
200.84MB    1.596s     1.051s   COMPLETED
```

Each row is a real apply — the Tamil verse text changed, a journal transaction
id was written, and a backup DB appeared under
`correction-application-backups/`. The 0.57MB baseline is a two-verse project
and matches the ~500–600KB the Stage 9B.3b artifacts showed. Backup cost is
linear at roughly 5ms/MB.

So 30s was **not** actually tight on this hardware (NVMe, warm cache) — worth
stating plainly, because the reported concern was a real design gap rather
than a live failure. The reason to fix it anyway: this is whole-file I/O, so
antivirus scanning the copy, a spinning disk or a sync-backed project folder
can cost an order of magnitude more, and a 200MB DB needs only ~20x slower I/O
to cross 30s. The failure direction is bad — Rust stops waiting while Python
is mid-apply, so the user is told the correction failed when it may already be
written and journalled.

Set to 180s: the table's existing class for expensive local I/O (`report.get`,
`project.inspectImport`), leaving ~110x headroom on the measurement.
`getApplicationStatus` and `reanalyzeAffected` deliberately stay on the
default — a ledger read and a background-job start have nothing to wait for.
Covered by `sidecar::tests::correction_apply_has_room_for_a_full_semantic_db_backup`;
`cargo test` 11 passed.

## Stage 9B.4 pre-installed-acceptance gate — 1→null verification semantics (2026-09-07)

Run before Stage 9B.4 was ever committed, and before the 0.9.2 release. It
asked one question of the shipped policy: can `POSSIBLY_MISSING`, on its own,
produce a verification FAILED? It could, and that is now fixed.

### Defect 1 — POSSIBLY_MISSING was treated as a positive absence

`correction_verification.py` had a single set:

```python
_NEGATIVE_COVERAGE = {SourceCoverage.POSSIBLY_MISSING.value, SourceCoverage.MISSING.value}
```

and the coverage branch returned FAILED with `COVERAGE_STILL_MISSING` whenever
coverage landed in it and no positive dimension reason had accumulated. So an
obligation Stage 8 explicitly declined to resolve was reported to the reviewer
as a positively failed correction. That is the `POSSIBLY_MISSING → MISSING`
auto-promotion §39 of `HANDOFF.md` forbids outright and §36 reserves for human
confirmation — reached here silently, by a verification pass, with no human in
the loop.

It also broke the stage's own governing rule in the opposite direction from the
one the stage was designed around. Stage 9B.4 was built so a *disappeared*
finding could not prove success; this let an *unresolved* obligation prove
failure. Both are the same error: treating an absence of evidence as evidence.

The set is now split three ways, and `NOT_CHECKED` is deliberately in none of
them — it means Stage 8 made no coverage claim at all and must not veto
positive dimension evidence, unlike `POSSIBLY_MISSING`/`UNCERTAIN`, which mean
Stage 8 looked and could not resolve:

```text
COVERED / COVERED_BY_RESTRUCTURING  -> may support PASSED
MISSING                             -> may support FAILED  (positive absence)
POSSIBLY_MISSING / UNCERTAIN        -> always UNCERTAIN    (unresolved)
NOT_CHECKED                         -> no coverage claim; falls through
```

The unresolved branch always abstains. It cannot fail, because absence was never
established; it cannot pass either, because coverage is genuinely open — and
when positive dimension evidence *does* exist alongside it, that is two current
assessments disagreeing, which this policy has always resolved as UNCERTAIN.
Two new reason codes carry the difference to the reviewer rather than reusing
the failure code: `COVERAGE_POSSIBLY_MISSING` ("a possible omission is not an
established one") and `COVERAGE_UNRESOLVED`.

### Defect 2 — run-level searchIncomplete could not stop an absence-based FAILED

`aggregate()` consulted `search_incomplete` only on the PASSED path. The
per-obligation guard that does block absence under an incomplete search reads
`unusable`, which is built from per-relationship `locationOutcome` values — so a
job whose *run-level* `searchIncomplete` warning was set, with relationships
that individually looked clean, still produced FAILED off nothing but absence.

`aggregate()` now downgrades a FAILED to UNCERTAIN when every failing verdict is
absence-based, meaning its reason codes are a subset of
`{COVERAGE_STILL_MISSING}`. A located contradiction carries
`DIMENSION_CONTRADICTED`/`DIMENSION_ALTERED`/`DIMENSION_STILL_WEAKENED` and is
untouched, because search completeness has no bearing on a contradiction that
was found — a run mixing the two still fails.

(The pre-existing per-obligation `SEARCH_INCOMPLETE in unusable` checks inside
the coverage branches are unreachable in practice: the blanket `if unusable:`
return fires earlier. They are kept as defense in depth, not relied on.)

### Policy version bumped to v2

`VERIFICATION_POLICY_VERSION` moves to `correction-verification-policy-v2`. The
verifier fingerprint hashes it, so every verification recorded under the buggy
rule becomes non-current and re-evaluatable instead of carrying a wrong verdict
forward — which is exactly what that fingerprint was built for. Stage 9B.4 was
never committed or released, so no shipped record is affected; the bump protects
records from local acceptance runs.

### The decision rule, stated once

```text
positive current evidence obligation satisfied      -> PASSED
positive current evidence obligation unsatisfied    -> FAILED
unresolved / ambiguous / incomplete / conflicting   -> UNCERTAIN
```

No frontend or Rust change was needed. `reasonExplanations` is served from
Python's `REASON_EXPLANATIONS` map and `reasonCodes` is typed `string[]`, so
both new codes reach the panel with their prose automatically.

### Verification

```text
Stage 9B.4 focused Python                61 passed  (55 + 6 new)
Stage 8 + 9B.3b + 9B.3c Python           49 passed
full Python + Greek Room                927 passed  (921 + 6)
git diff --check                         clean; line-ending notices only
```

Frontend and Rust gates were not re-run: no frontend or Rust file was touched by
this gate. They passed on this working tree earlier the same day — svelte-check
0/0, 306 Vitest across 24 files, `cargo test` 12 passed.

Installed desktop acceptance of the verify → Mark corrected flow remains NOT
RUN, and this gate does not change that.

---

# Stage 8 → Stage 9B target-hash contract repair (2026-09-07)

## The defect

Stage 8 persisted the **Stage 6A target-inventory `targetContentHash`** into
`qaFinding.targetContentHashes`:

```python
# qa_audit.py, _build_finding, before
"targetContentHashes": [str(target.get("targetContentHash") or "")],
```

`target` there is the Stage 6A target inventory, and its `targetContentHash` is
`FoundationRepository.target_content_hash(...)` — a SHA-256 over the canonical
JSON object holding **every verse in the analyzed range**.

Stage 9B correction eligibility reads the same field as an exact **per-verse**
hash and compares it with `runtime.text_hash(current_verse_text)`, which is
SHA-256 over the raw verse string. A JSON-object fingerprint can never equal a
raw-verse-text hash, so `_check_current_text` raised `TARGET_TEXT_CHANGED` for
every naturally emitted finding, on Scripture that had never been edited.

Observed directly on the real pipeline before the fix: a Tamil PHP 1:3–1:6 run
emitted 12 findings and **all 12** carried the identical hash
`59a13fd6af7c…`, the range fingerprint — including findings anchored at four
different verses.

## Why the write side was fixed, not the read side

The two hashes answer different questions and must stay separate:

| | Stage 6A `targetInventory.targetContentHash` | Stage 8 `qaFinding.targetContentHashes` |
|---|---|---|
| Scope | the whole analyzed target range | the finding's own target reference(s) |
| Shape | SHA-256 of canonical JSON of `{ref: text}` | SHA-256 of each raw verse string |
| Question | is this analysis run still current? | is the wording the reviewer confirmed still on disk? |
| Reacts to | any edit anywhere in the range | an edit to the correction target only |

Teaching eligibility to compare range fingerprints would have made a
neighbouring-verse edit indistinguishable from an edit to the verse a
correction rewrites. Semantic-analysis freshness and exact correction-target
CAS are separate mechanisms and neither may impersonate the other.

## The contract, stated once

`engine/tc_ai_bridge/qa_target_hash.py` is new and holds the whole contract —
the hash and the reference resolution — so Stage 8 and Stage 9B cannot drift:

```text
qaFinding.targetContentHashes[i]
    == canonical_text_hash(current authoritative text at target_references[i])

target_references = displayed references of the finding's target semantic
                    units, deduplicated, order preserved
                    (falling back to the finding's displayedReferences when the
                    finding has no target units at all — an omission finding
                    has no separate target realization, so its displayed
                    references *are* its target references)
```

Authoritative text is `<project>/<book>/<chapter>.json`, read through
`current_target_text(project)` — the same snapshot Stage 9B re-reads. Preserved
imported USFM is never hashed.

`canonical_text_hash` is now the single implementation:
`PassageSemanticRuntime._sha256_text` (and therefore `runtime.text_hash`,
`text_revision`, and every `_json_hash`) and `qa_audit._sha` both delegate to
it. The same target verse string produces bit-identical hashes at Stage 8
persistence, Stage 9B eligibility, and correction proposal/application
validation.

Resolution is all-or-nothing: if any target semantic unit cannot be resolved,
both sides fall back to `displayedReferences` rather than emit a shorter list
that silently misaligns every later hash. A target reference with no current
Scripture yields an empty hash rather than being dropped — it can never match,
so the failure is closed rather than skipped.

## The cross-verse case

For the canonical shape — source semantics at PHP 1:3, target realization at
PHP 1:6, analysis range PHP 1:3–1:6 — the finding's `displayedReferences` is
`["PHP 1:3", "PHP 1:6"]` because `_finding_anchors` deliberately carries both
sides. Only the target side is content-addressed:

```text
targetContentHashes == [canonical_text_hash(current PHP 1:6 text)]
```

No source relationship at PHP 1:6 is manufactured to make the references line
up, and the source unit's own reference stays PHP 1:3.

Eligibility therefore had to stop pairing stored hashes positionally against
`displayedReferences`. It now resolves the finding's target references through
the shared helper (`CorrectionEligibilityService.target_references`) and pairs
against those. `TARGET_REFERENCE_MISSING` still covers every displayed
reference; only the hash comparison narrowed. Findings with no target semantic
units — every hand-built Stage 9B fixture, and every real POSSIBLE_OMISSION —
resolve to exactly what they resolved to before, so the reader contract those
tests pin is unchanged.

## Edit behaviour, verified

```text
edit the exact target verse (PHP 1:6)
  -> TARGET_TEXT_CHANGED  +  FINDING_STALE      (both fire; both independent)

edit a neighbouring verse (PHP 1:3, the source-side reference)
  -> TARGET_TEXT_CHANGED absent; the stored target hash still equals the
     current PHP 1:6 hash
  -> FINDING_STALE only — the currentness machinery decides, as designed
```

That second row is the separation the repair exists to protect, and it is
pinned by
`test_editing_a_neighbouring_verse_leaves_the_correction_target_hash_alone`.

## Findings already persisted by the broken writer

Old range hashes are **not** reinterpreted, and there is no heuristic
hash-type detection. A finding still carrying a range fingerprint is treated as
what it literally is — a hash that does not match the verse — and eligibility
blocks it with `TARGET_TEXT_CHANGED`. It becomes correctable only after
re-analysis re-emits it. Finding ids are stable (`_stable_finding_id` excludes
the run fingerprint and every version), and `save_qa_finding` refreshes machine
fields while preserving `qaDisposition`/`reviewStatus`, so re-analysis repairs
the hash without costing the reviewer their decision.

### Why `QA_ENGINE_VERSION` was not bumped

Bumping it was the obvious way to force that re-analysis everywhere — the
engine version is part of the QA run fingerprint, so every cached Stage 8 run
would miss. It was implemented, tested, and **reverted**, because it does not
work:

```text
run 1 with QA_ENGINE_VERSION=v1                       -> MISS, persists
run 2 with QA_ENGINE_VERSION=v2, same target inventory
  -> FoundationConflict: UNIQUE constraint failed:
     coverage_accounts.project_id, passage_id, direction,
     audit_owner_unit_id, coverage_dimension, semantic_fingerprint
```

`_build_target_support_account`'s account fingerprint hashes
`{owner, dimension, policy}` — the **policy** version, not the engine version.
So an engine-version bump alone changes the run fingerprint (forcing a re-run)
without changing the coverage-account identity, and the target-support pass
re-`INSERT`s a byte-identical row into `coverage_accounts` and dies before it
can repair a single hash. Verified directly, not inferred; a policy-version
change does *not* reproduce it, precisely because the policy version is inside
the account fingerprint.

Making that insert idempotent would mean overwriting coverage accounts that
carry human promotion state — exactly what `FoundationConflict` guards — so it
is out of scope here and recorded as a **separate open defect**: Stage 8 cannot
be re-run against an unchanged target inventory under a changed engine, model,
or calibration version.

Existing broken findings are therefore repaired the next time the QA run
fingerprint legitimately misses cache — any Scripture edit, source-lock change,
policy change, or fresh import — and new analyses emit the correct form from
the start.

## A second, independent production blocker (found, not fixed)

While proving the gate, every naturally emitted **meaning-failure** finding
(CONTRADICTION, MEANING_SHIFT, POSSIBLE_UNDER/OVERTRANSLATION, the dimension
kinds) turned out to be blocked by `RESOURCE_CONFLICT_REQUIRES_REVIEW`:

`meaning_analysis._assessment` puts every component whose status is `ALTERED`,
`CONTRADICTED`, `TARGET_WEAKENS_SPECIFICITY`, `TARGET_ADDS_SPECIFICITY` or
`PARTIALLY_PRESERVED` into `conflictingEvidenceIds` — i.e. the evidence *that
the finding is real*. `correction_eligibility._check_resource_conflicts` then
blocks on any non-empty `conflictingEvidenceIds`, a rule written for
*resource* conflicts ("the sources disagree about what the target should say").
The two meanings of "conflicting" are not the same, and a meaning-failure
finding cannot satisfy eligibility today.

Coverage findings (POSSIBLE_OMISSION, POSSIBLE_ADDITION) carry no conflicting
evidence and reach `ELIGIBLE` cleanly, which is what the production gate below
demonstrates end to end. This blocker is unrelated to the hash contract, is
**not** fixed here, and needs its own approved scope.


## A pre-existing test flake found while running this gate (not fixed)

Two Stage 9B.1 tests assert on the *last* correction-proposal event:

```python
correction_proposal_history(id)[-1]["eventType"] == "STALE"       # 9b1:528
correction_proposal_history(machine["id"])[-1]["eventType"] == "SUPERSEDED"  # 9b1:721
```

`correction_proposal_history` orders by `created_at, id`. Event ids are
`uuid.uuid4()` and `created_at` is `datetime.now(timezone.utc).isoformat()`.
On this Windows workstation that clock is coarse: **200,000 consecutive calls
produced only 91 distinct ISO timestamps** (~1.8 ms granularity). Two events
written milliseconds apart therefore share a `created_at` regularly, and the
tiebreak falls to a random UUID.

Measured on the correction-proposal scenario, 60 fresh runs each:

```text
with this change     60 runs -> 2 timestamp collisions, 2 wrong history[-1]
clean HEAD (34a8565) 60 runs -> 1 timestamp collision,  1 wrong history[-1]
```

The two are the same population; the failure rate is roughly 2-3% per run and
rises when the machine is loaded (both observed failures happened while another
pytest process was running). This is **not** a regression from the target-hash
change, which touches neither `correction_proposal_events` nor its ordering:
the flake reproduces with those files reverted to `HEAD`.

Not fixed here: the ordering has no monotonic tiebreak column, and inventing
one is a repository change outside this gate's scope. Worth scheduling — a
non-deterministic gate is worth less than the tests in it.

## Correction to the earlier Stage 9B.3b / 9B.3c acceptance claim

The 9B.3b and 9B.3c installed acceptance used a **controlled pre-seeded
finding**: `tests/correction/test_correction_stage9b3b.py::_fixture` calls
`repo.create_qa_finding(...)` and then `UPDATE qa_findings SET payload_json=?`
with `"targetContentHashes": [_hash(before)]` — hashes that already conformed
to the Stage 9B reader contract.

What that acceptance genuinely validated stands: the correction application
transaction, exact Scripture mutation, invalidation, affected re-analysis,
cross-verse provenance, and persistence/recovery.

What it did **not** establish, and must not be described as having
established: that a finding **naturally emitted by production Stage 8** can
enter Stage 9B at all. It could not have — the writer defect above made that
impossible for every such finding. The gap was found while preparing Stage
9B.4 installed acceptance. Earlier acceptance is not "fully production
end-to-end"; it was production end-to-end **downstream of a seeded finding**.

## Files changed

```text
engine/tc_ai_bridge/qa_target_hash.py             new — the contract, one place
engine/tc_ai_bridge/qa_audit.py                   writer: per-target-ref hashes
engine/tc_ai_bridge/correction_eligibility.py     reader: pair against target refs
engine/tc_ai_bridge/passage_semantic_runtime.py   _sha256_text delegates
engine/tests/semantic/test_qa_target_hash_contract_stage8_9b.py   new — production path
```

Schema unchanged (**v14**). Version unchanged (**0.9.2**). No frontend, Rust or
wire-shape change: `targetContentHashes` is still `string[]` and still
positionally ordered; only which references it is taken over changed.

# Indic and source-script font support (2026-09-08)

Implements `docs/plans/FONT_SUPPORT_PLAN.md`. Before this, `src/index.css`
set one Latin stack (`-apple-system, "Inter", Segoe UI, Helvetica, Arial`)
on `body`, and none of those faces cover Tamil, Devanagari, Bengali, Telugu,
Kannada, Malayalam, Gujarati, Gurmukhi, Odia or Urdu. Target Scripture
rendered through whatever WebView2 fell back to — a face the reviewer did
not choose, varying by machine, and tofu on a machine with no Indic font
installed.

## Fallback-by-coverage, not language detection

The design decision worth keeping: nothing reads `targetLanguageId` and maps
it to a script. CSS already resolves font fallback **per glyph** by coverage,
so a single stack that names every script face gets Latin chrome to Inter,
Tamil past Inter to Vijaya, and Devanagari past both to Noto Serif
Devanagari. Three consequences, all load-bearing:

- The nine components that render target text (`AlignmentModal`,
  `CorrectionReviewPanel`, `EvidenceInspector`, `FindingContextMenu`,
  `ProjectReportScreen`, `SemanticAlignmentMode`, `TopBar`,
  `VerseNotesPopup`, `VerseList`) needed **no** change to render correctly.
  One `body` rule did it.
- A mixed-script string renders correctly inside one text node — a finding
  message like `Missing word "அருள்" in verse 3` puts the Latin in Inter and
  the Tamil in Vijaya without being split.
- Adding a gateway language is a data change, not a code change, so long as
  its script is already in `--font-indic`.

Declaring thirteen `@font-face` families costs nothing at runtime: the
webview reads a font file off disk only when a glyph actually lands on it, so
a Tamil-only project never touches the other twelve.

## What is bundled, and what deliberately is not

`public/fonts/` (new; Vite's default `publicDir`, so no `vite.config.ts`
change was needed — files are copied verbatim into `dist/` and served from
`tauri://localhost/fonts/…`). 23 files, 1.8 MB, produced by the new
`scripts/vendor-fonts.py` and committed rather than downloaded at build time,
because Bridge must build and run offline.

**Vijaya and Nirmala UI are named in the CSS stack and must never be
bundled.** They ship with Windows and Microsoft grants no redistribution
right; putting either TTF in the repo or the installer is a licence
violation. Naming them resolves the installed system font by family name in
WebView2 and needs no permission. Since the build target is
`x86_64-pc-windows-msvc`, Vijaya is present on essentially every real user's
machine — the bundled Noto faces are the floor for macOS/Linux dev machines
and stripped Windows installs. `public/fonts/README.md` says this where
someone adding a font would go looking.

Bundled versions:

```text
Noto Serif Tamil / Devanagari / Bengali / Telugu / Kannada / Malayalam /
Gujarati / Gurmukhi / Oriya, Noto Nastaliq Urdu
        google/fonts@5e35378e6bda803962ee6fd257e444a7d459660d, OFL 1.1
Gentium Plus (Regular + Bold)   same commit, OFL 1.1
Ezra SIL 2.51 (Regular)         software.sil.org, OFL 1.1 + MIT/X11
```

Per-file source sha256 is recorded in `public/fonts/README.md`.

## Two things about the upstream fonts that the plan could not have known

Both found by fetching them rather than reading about them, per this repo's
standing rule:

1. **The Noto families are published only as variable fonts.** google/fonts
   ships `NotoSerifTamil[wdth,wght].ttf`, not a static pair. The plan called
   for statics on size grounds and was right to: measured, the variable
   Tamil WOFF2 is **181 KB** against **66 KB** for the static Regular+Bold
   pair. So `vendor-fonts.py` instances each family at `wght` 400/700
   (pinning `wdth` 100 where that axis exists) with
   `fontTools.varLib.instancer`. Instancing resolves variation deltas and
   does not touch the glyph set or shaping tables; the script asserts glyph
   count and GSUB/GPOS lookup counts are unchanged after every instance and
   refuses to write a font where they are not.

2. **There is no "Noto Serif Greek" family.** Greek lives in plain Noto
   Serif's `greek-ext` subset. `--font-greek` therefore leads with **Gentium
   Plus** — the plan's own second choice, OFL, published as real statics, and
   a stronger polytonic face than Noto Serif — with Noto Serif named behind
   it. Gentium Plus is 600 KB of the 1.8 MB total, by far the largest item;
   it is a very large Latin/Greek/Cyrillic/IPA font and is not subsetted (see
   below).

Also: **Ezra SIL has no bold cut.** It gets one `@font-face`. Bridge never
bolds source Scripture, so a bold context would get synthetic bold rather
than a wrong face.

Nothing is subsetted, deliberately. Complex-script shaping lives entirely in
GSUB/GPOS, and a subsetter that drops the wrong lookup breaks Tamil conjuncts
or Hebrew mark positioning in a way that presents as a rendering bug rather
than a font bug — expensive to debug later, and not worth ~100 KB.

## Verification actually performed

Source- and asset-level only. **The visual desktop checks in the plan's §8
steps 2–6 have not been run** — they need the real window.

```text
npm run check    0 errors, 0 warnings
npm run test     306 passed (24 files)
npm run build    clean; all 23 files in dist/fonts/, every one referenced
                 by the built CSS; served with content-type font/woff2 and
                 a valid wOF2 header
```

Coverage against real project input, not hand-picked samples:

```text
IRVTam Luke + Philippians   all 90 distinct chars resolve in Noto Serif Tamil
UHB, all 39 books           all 77 Hebrew-block codepoints resolve in Ezra
                            SIL, cantillation included
UGNT, all 27 books          all 166 polytonic codepoints resolve in Gentium
```

And shaped through HarfBuzz — the engine the webview itself uses — because
cmap coverage alone would not catch instancing having broken a GSUB lookup:

```text
IRVTam Tamil        22 chars -> 21 glyphs   (the conjunct forms)
Nastaliq Urdu       10 chars -> 18 glyphs
UHB Genesis 1:1    109 chars -> 110 glyphs  through Ezra SIL
UGNT John 1:1       36 chars ->  47 glyphs  through Gentium Plus
```

Zero `.notdef` in any of them.

## Deviations from the plan in the pane tuning

- `AlignmentModal`'s `.interlinear` was to get `--font-hebrew` outright. That
  would have put Ezra SIL on every NT book's Greek source column. The markup
  already sets `dir={context.sourceDirection}` on that element, so the face
  keys off it: `[dir="rtl"]` → Hebrew, `[dir="ltr"]` → Greek.
- `--font-target` went on `.token.target` rather than the planned
  `.aligned-card .word`. One rule then covers the aligned cards inside the
  interlinear — where it also has to undo the source face they would
  otherwise inherit — and the word-bank tokens outside it. `.drag-ghost`
  carries a target word too and takes the token as well.
- `LexiconPopup`'s `.headword`/`.lemma` were flagged as possibly needing more
  plumbing than they were worth, with Greek left to fall back. They did not:
  both already render `dir={direction}`, so two attribute selectors gate the
  face and Greek gets Gentium Plus properly.
- `VerseList`'s `.vedit textarea` line-height went 1.7 → 1.85 to match
  `.vtext`, so entering edit mode does not reflow the verse.

## Known adjustment deferred

**Urdu needs roughly `line-height: 2.0`.** Nastaliq's steep baseline cascade
clips at the 1.85 the other scripts use. Bridge has no Urdu project today, so
no special case was added — this is the note for whenever the first one lands.
A per-script line-height needs a signal the fallback chain deliberately does
not carry, so it would go on the scripture panes via `targetLanguageId`, not
into `--font-indic`.

## CSP

`src-tauri/tauri.conf.json` has `"csp": null`, so no `font-src` directive was
needed. **If CSP is ever tightened, it must include `font-src 'self'`** or
every bundled font is blocked silently. `"resources": ["resources/"]` in the
same file is for the Python sidecar and is unrelated — fonts ride inside the
frontend `dist/`.

## Files changed

```text
public/fonts/                            new — 23 WOFF2 + OFL.txt,
                                         GentiumPlus-OFL.txt,
                                         EzraSIL-Licenses.txt, README.md
scripts/vendor-fonts.py                  new — how they were produced
src/index.css                            @font-face block, font tokens, body
src/lib/components/VerseList.svelte      .vtext, .vedit textarea
src/lib/components/AlignmentModal.svelte .interlinear[dir], .token.target,
                                         .drag-ghost
src/lib/components/LexiconPopup.svelte   .headword[dir], .lemma[dir]
src/lib/components/SettingsModal.svelte  fonts attribution in resources pane
```

No engine, Rust, wire-shape or schema change. Version unchanged (**0.9.3**).

# Meaning failure is not resource disagreement (2026-09-08)

The second production blocker recorded above, now fixed. Before this, **no
meaning-failure finding the pipeline could emit was ever correctable**, however
clean the Scripture and however firmly a reviewer had confirmed it.

## Root cause: one field, two meanings

`meaning_analysis._assessment` split its components into two lists:

```python
supporting  = components whose status is PRESERVED / NOT_EXPLICIT_BUT_RECOVERABLE
conflicting = components whose status is ALTERED, CONTRADICTED,
              TARGET_WEAKENS_SPECIFICITY, TARGET_ADDS_SPECIFICITY,
              PARTIALLY_PRESERVED
```

and wrote the second list to `conflictingEvidenceIds`. Read on its own terms
that is right: the component evidence *conflicts with the claim that meaning
was preserved*. Stage 8 copied the field onto the finding verbatim.

`correction_eligibility._check_resource_conflicts` then read the same field
under the other meaning of the word — a **resource** conflict, "the sources
disagree about what the target should say, so a human must choose first" — and
raised `RESOURCE_CONFLICT_REQUIRES_REVIEW` for every id in it.

So the evidence that a translation is wrong was being read as a reason not to
fix it. Reproduced on the real PHP 1:3 → 1:6 pipeline before the change:

```text
POSSIBLE_OVERTRANSLATION  qa-finding-fa51bd2a4fdc67c54ebc606ed2eed4fb
  component  LEXICAL_CONTENT  TARGET_ADDS_SPECIFICITY  resourceStatus=SUPPORTING
  conflictingEvidenceIds  ['meaning-evidence-cfc862dd...', 'source-evidence-2a52a06b...']
  eligibility codes       {'RESOURCE_CONFLICT_REQUIRES_REVIEW'}
```

Note the second id: `source-evidence-2a52a06b...` is a real tN/tW record whose
own `validationStatus` is **SUPPORTING**. It was blocking a correction while
agreeing with it.

## The second half of the root cause: the rule that never fired

`_check_resource_conflicts` also walked `resourceEvidenceIds` and blocked on

```python
str(evidence.get("resourceValidationStatus") or "") == "CONFLICTING"
```

`EvidenceRecord` has a `validation_status` field, which `to_wire` serializes as
**`validationStatus`**. No evidence record has ever carried a
`resourceValidationStatus` key, so that branch never matched anything. The only
thing actually enforcing resource protection was the overloaded meaning field —
which is why simply deleting the overloaded check would have removed the
protection outright rather than narrowing it.

## The contract now

Two explicitly typed fields, on both the Stage 7 assessment and the Stage 8
finding:

```text
conflictingEvidenceIds        meaning-failure evidence: the target meaning
                              differs from the source. Positive
                              translation-error evidence. Never blocks a
                              correction.
resourceConflictEvidenceIds   genuine resource disagreement: applicable
                              tN/tW/TWL records that contradict each other.
                              Blocks until a human resolves it.
```

`resourceConflictEvidenceIds` is derived from data Stage 7 already had and had
never surfaced: each component's `evidence.resourceStatus`, which the
comparator computes from the `validationStatus` of the resource records
attached to the source unit.
`meaning_analysis.component_resource_conflict_evidence_ids()` collects the
resource ids of every `CONFLICTING` component;
`resource_conflict_evidence_ids(assessment)` reads the stored field, falling
back to **proving** it from the assessment's own `componentAssessments` when
the assessment predates the field. Stage 8 calls the second one, so a re-run
over a cached pre-split Stage 7 run still writes the correct typed value
instead of an optimistic empty list.

Eligibility now reads only `resourceConflictEvidenceIds` plus the (repaired)
live `validationStatus` check on `resourceEvidenceIds`.

## Backward compatibility: absence is the discriminator

A finding written before the split has `conflictingEvidenceIds` and **no**
`resourceConflictEvidenceIds` key at all. Nothing on such a record says which
of the two kinds its ids are, so eligibility fails closed on it:

```python
typed = finding.get("resourceConflictEvidenceIds")
if typed is None:      # pre-split record -- cannot prove, so block
    block every conflictingEvidenceIds entry
else:                  # post-split record -- trust the typed field
    block every entry of typed
```

Deliberately *not* done: inspecting the ids' prefixes (`meaning-evidence-`
versus `source-evidence-`) to guess which kind they are. That is exactly the
heuristic reinterpretation the hash-contract repair also refused.

The way out is re-analysis, not reinterpretation. Finding ids are stable
(`_stable_finding_id` excludes engine/policy versions and this field), and
`save_qa_finding` preserves `qaDisposition` / `reviewStatus` / `revision` on a
re-run, so a repaired finding keeps the reviewer's decision.
`test_re_analysis_repairs_a_legacy_finding_and_keeps_the_human_decision` pins
all of that.

**No engine or policy version was bumped**, on purpose. `QA_ENGINE_VERSION`,
`MEANING_ENGINE_VERSION` and the model/calibration versions all feed the Stage
8 run fingerprint but not the coverage-account fingerprint, so bumping any of
them makes the first Stage 8 re-run against an unchanged target inventory die
with `FoundationConflict` on a duplicate `coverage_accounts` row — the separate
open defect recorded above. Existing findings are therefore repaired the next
time the run fingerprint legitimately misses cache (any Scripture edit,
source-lock change, policy change or fresh import), and are blocked rather than
misread until then.

## What the real pipeline can and cannot emit

Established while proving the matrix, and worth recording because it bounds
what "dimension coverage" can honestly mean here:

- `SemanticLocationEngine.run_range` only searches **coverage-account owner
  units** (`primary`). `source_semantic_inventory` creates REFERENT,
  PARTICIPANT and TEMPORAL_ASPECTUAL units with `role=COMPONENT` and
  `eligibility=CONDITIONAL`, so they are never owners, never located, and
  cannot produce a meaning-failure finding today. Only LEXICAL_CONTENT
  (LEXICAL), QUANTITY (QUANTIFIER) and POLARITY (NEGATION) units are
  PRIMARY/ELIGIBLE.
- `DeterministicMeaningComparator.compare` never returns `ALTERED` on any input
  path. `MeaningPolicy.aggregate` handles it and an AI comparator would produce
  it, but the shipped deterministic one cannot.
- Bridge's own resource validation attaches evidence only to tokens it
  **matched**, and marks unmatched evidence `CONFLICTING` — so a CONFLICTING
  record is never attached to a unit, and `evidence.resourceStatus` is never
  `CONFLICTING` in an unmodified run. The resource-conflict tests write that
  state through the store on purpose and say so in the docstring.

All three are pre-existing properties of the inventory and comparator, not
consequences of this repair. They are the reason the matrix is covered in two
layers: the real pipeline where it reaches, and the real Stage 7 writer plus
the real eligibility rule where it does not.

## Verified on the real pipeline

Real Stage 5 → 6A → 6B → 7 → 8, no hand-built finding, confirmed through
`qa_review.decide_finding`:

```text
kind                        POSSIBLE_OVERTRANSLATION
finding id                  qa-finding-fa51bd2a4fdc67c54ebc606ed2eed4fb (unchanged)
component                   LEXICAL_CONTENT / TARGET_ADDS_SPECIFICITY
displayedReferences         ['PHP 1:3', 'PHP 1:6']  -- source 1:3, target 1:6
resourceConflictEvidenceIds []
eligibility codes           {'ELIGIBLE'}
correction proposal         created, edited to HUMAN_MODIFIED, applied
```

Naturally emitted meaning failures now reaching eligibility, each from its own
real run:

```text
QUANTITY_PROBLEM           QUANTITY         CONTRADICTED                ALL -> SOME (pas)
NEGATION_PROBLEM           POLARITY         CONTRADICTED                ou dropped
POSSIBLE_UNDERTRANSLATION  LEXICAL_CONTENT  TARGET_WEAKENS_SPECIFICITY  epiteleo -> "carry on"
POSSIBLE_UNDERTRANSLATION  LEXICAL_CONTENT  PARTIALLY_PRESERVED         enarchomai -> "finish"
POSSIBLE_OVERTRANSLATION   LEXICAL_CONTENT  TARGET_ADDS_SPECIFICITY     unlicensed "only"
```

The POLARITY row is Greek → English on purpose: the open Tamil polarity
tokenizer defect must not be what decides this result.

Resource protection, proven twice:

```text
one of two resource records on a located source unit set CONFLICTING
  -> Stage 7 component resourceStatus=CONFLICTING
  -> Stage 8 emits kind RESOURCE_CONFLICT, id in resourceConflictEvidenceIds
  -> eligible=False, RESOURCE_CONFLICT_REQUIRES_REVIEW

an already-eligible confirmed finding whose resourceEvidenceIds record later
turns CONFLICTING  ->  eligible=False   (the branch that had never once fired)
```

## Verification on 2026-09-08

```text
new meaning-failure -> 9B production suite   76 passed  (new file, run alone)
Stage 7 + Stage 8 + 9B.0/9B.1/9B.3a/9B.3b/
  9B.3c/9B.4 + 9A review + foundation +
  Stage8->9B hash contract + new           405 passed
full Python + Greek Room                  1014 passed, 0 failed  (21m36s)
frontend Vitest                            307 passed (24 files)
npm run check                              0 errors, 0 warnings
npm run build                              built
cargo check                                clean
cargo test                                 12 passed
git diff --check                           clean
```

An earlier combined focused run reported one failure in the new file
(`test_confirmed_meaning_failure_can_have_a_correction_proposed`). That was a
wrong assertion in the test, not in the code: after a proposal exists,
eligibility legitimately reports `CONFLICTING_CORRECTION` naming that proposal,
which the review panel filters out for a proposal it already holds. The test
now asserts that shape, and re-evaluates with `ignore_proposal_ids` to confirm
nothing else blocks. The full 1014-test run above includes the corrected file.

The pre-existing `correction_proposal_history(...)[-1]` ordering flake
documented in BUILD_LOG did not reproduce in any run of this gate.

Installed desktop acceptance: **NOT RUN**, deliberately. Nothing released.

## Files changed

```text
engine/tc_ai_bridge/meaning_analysis.py           typed resourceConflictEvidenceIds
                                                  + the two derivation helpers
engine/tc_ai_bridge/qa_audit.py                   carries it onto the finding
engine/tc_ai_bridge/passage_semantic_models.py    QaFinding field
engine/tc_ai_bridge/correction_eligibility.py     reads only typed conflicts;
                                                  validationStatus key repaired;
                                                  pre-split records fail closed
engine/tc_ai_bridge/qa_review.py                  resourceConflictEvidence section
engine/tests/correction/test_meaning_failure_eligibility_stage9b.py   new
engine/tests/correction/test_correction_stage9b0.py          decision table extended
schemas/bridge-passage-semantic-v1.schema.json    QaFinding + MeaningAssessment
src/lib/types/passageSemanticV1.ts                optional new field
src/lib/types/qaReview.ts                         resourceConflictEvidence
src/lib/components/EvidenceInspector.svelte       "Meaning differs" versus
                                                  "Resource conflict" badges
src/lib/components/__tests__/                     fixtures + two tests
src-tauri/src/passage_semantic_wire.rs            MeaningAssessment, serde default
```

Companion database schema unchanged (**v14**) — both records are stored as
`payload_json`, so the new field needed no column and no migration.
Verification policy unchanged (`correction-verification-policy-v2`). Version
unchanged (**0.9.3**); nothing released.

# Stage 9B.4 installed-acceptance preparation, and three defects it exposed (2026-09-08)

Bridge **0.9.4**, prepared as the installed-acceptance candidate. **Not
released.** Companion schema stays **v14**; verification policy stays
`correction-verification-policy-v2`.

Preparing the acceptance turned out to be the first time the Stage 9B.4 flow
had ever been driven end to end outside its own unit fixtures, and it broke in
three separate places before it produced a verdict at all.

## Defect 1: the correction review UI could never offer a span

`FoundationRepository.semantic_location_relationship()` selected `r.run_id`,
used it to validate the run, and then returned `json.loads(row["payload_json"])`
— dropping it. The run id is a column, not part of the immutable payload.

`correction_wording.review_context` walks the finding's locations and does:

```python
run_id = str(location.get("runId") or "")
if not run_id:
    continue
```

So every location was skipped, `candidateSpans` came back `[]`, and the
candidate `alternatives` list (which reads `location.get("runId", "")` too) was
always empty. The Stage 9B.2 reviewer could not pick a span to correct at all.
Nothing caught it because every 9B test builds its `CorrectionIntent` directly
rather than choosing from `candidateSpans`.

The reader now returns `{**payload, "runId": row["run_id"]}`. With that, the
production review context offers exactly `PHP 1:6 · "some" · [0, 4)` for a
finding whose source obligation is at PHP 1:3.

## Defect 2: every affected re-analysis died on a duplicate coverage account

`save_coverage_account` was a bare `INSERT`, and the account id is a
deterministic fingerprint of `{owner, dimension, policy}` — deliberately stable
across runs, because the design is *seed once, then update the same identity in
place* (`update_coverage_account_status`'s own docstring says so).

Re-analysis therefore re-derives ids that already exist. Clicking
**Re-analyze affected passage** after any Apply produced:

```text
QA  FAILED
  FoundationConflict: Coverage account conflicts with an active obligation:
  UNIQUE constraint failed: coverage_accounts.id
```

with Stages 5, 6A, 6B and 7 all completing first. The affected range covers the
verses the correction did *not* touch, so their accounts always collide — 77 of
them in the acceptance fixture (29 source-coverage, 48 target-support).

BUILD_LOG had recorded this as "Stage 8 cannot be re-run against an unchanged
target inventory under a changed engine, model, or calibration version". That
understated it: **nothing had changed here but the Scripture edit**. It broke
the ordinary post-correction path, which is why Stage 9B.4 installed acceptance
had never been runnable.

Re-seeding an identical account is now a no-op. The stored row keeps its
coverage status, its finding link and — the reason the guard existed — its
review status, so a human promotion recorded against that obligation survives
re-analysis untouched. Only a real difference in the identity-bearing fields
still raises `FoundationConflict`:

```python
_COVERAGE_ACCOUNT_MUTABLE = {
    "coverageStatus", "coveredByRelationshipIds", "findingId",
    "reviewStatus", "lifecycleStatus", "revision",
}
```

The Stage 8 target-support pass also had to stop passing its freshly built
`account.revision` to `update_coverage_account_status`: on a re-run the row it
kept is already past revision 1. It now reads the stored revision, exactly as
the source-coverage pass above it already did.

With both fixed, the affected re-analysis went **FAILED → COMPLETED_WITH_WARNINGS**
and verification produced its first real semantic verdict.

## Finding 3: a cross-language PASSED is unreachable in 0.9.4

The first real verdict was not PASSED:

```text
source PHP 1:3 -> target PHP 1:6, structural range PHP 1:3-1:6
verse now "all remembrance of you remains with me ..."
UNCERTAIN  ['COVERAGE_POSSIBLY_MISSING', 'PROVIDER_LIMITED',
            'RECURRING_FINDING_SAME_DIMENSION']
qaDisposition CONFIRMED_TRANSLATION_ERROR   mayAcknowledgeCorrected false
```

PASSED requires Stage 6B to positively **re-locate** the corrected obligation.
Every route to `located_minimum = 0.36` is closed for a cross-language project:

```text
SEMANTIC_SIMILARITY  weight 0.42  no production provider ships (available=False)
  (embedding cache)              _embedding_map returns {} *before* consulting
                                 the cache when the provider is unavailable
HUMAN_PRECEDENT      weight 0.65  human_approved_lexical_precedents() is always
                                 empty: LexicalAlignmentGroup is constructed
                                 only in the model module and one test file, so
                                 no production path ever writes lexical_groups
LEXICAL              weight 0.38  Greek/English overlap is 0
CONCEPT              weight 0.15  0.95 for a matching QUANTIFIER kind
STRUCTURAL_PROXIMITY weight 0.05  0.8 within the same canonical reference
EXACT_SPAN           weight 0.01  1.0
                                 -> everything available sums to about 0.19
```

Target token instance ids fold in `text_revision` and the raw form, so a
precedent seeded before the correction would reference the old token anyway.

**This is correct behaviour, not a defect.** The verifier refuses to claim a
success it cannot demonstrate, and says why with `PROVIDER_LIMITED`. But it
means no real production flow can reach PASSED until either a production
multilingual embedding provider exists, or something in the app actually writes
human-approved lexical groups. Recorded as an open product gap; not fixed here.

## What the acceptance package therefore is

`scripts/seed_correction_acceptance.py` builds three projects, and
`docs/archive/STAGE_9B4_ACCEPTANCE.md` is the click-by-click script.

```text
A  PASSED     controlled verification fixture   DIMENSION_PRESERVED, COVERAGE_COVERED
B  FAILED     controlled verification fixture   DIMENSION_CONTRADICTED
C  UNCERTAIN  REAL Stage 5->6A->6B->7->8 run    COVERAGE_POSSIBLY_MISSING,
                                                PROVIDER_LIMITED
```

Only C is production end-to-end, and the document says so in as many words. A
and B reuse the Stage 9B.4 unit tests' own controlled-evidence builders rather
than copying them, so fixture and test cannot drift.

C is the canonical cross-verse case: a naturally emitted `QUANTITY_PROBLEM`
whose source obligation is `πᾶς` at PHP 1:3 and whose target realization is
"some" at PHP 1:6, left at `UNRESOLVED`/`AI_PROPOSED` for the tester to confirm,
correct to "all", apply, re-analyse and verify.

Validated through `BridgeEngine` on a copy, so the shipped fixtures stay
unverified:

```text
[A] PASSED  (expected PASSED)   mayAcknowledgeCorrected True   disposition unchanged
[B] FAILED  (expected FAILED)   mayAcknowledgeCorrected False  disposition unchanged
[C] QUANTITY_PROBLEM ['PHP 1:3','PHP 1:6']  UNRESOLVED/AI_PROPOSED
    candidateSpans [('PHP 1:6', 'some')]
```

## Inspector

`scripts/inspect_correction_application.py` gained a read-only **Word
Alignment** block, because the acceptance has to show that semantic
verification neither approves nor rebuilds an alignment. It reads the
`completed/` and `invalid/` markers under
`.apps/translationCore/tools/wordAlignment/` and reports
`INVALID` / `COMPLETED` / `PENDING`. On case A after a PASSED verification it
reports `INVALID` for PHP 1:6 — invalidated by the Apply, untouched by the
verifier.

## Version

0.9.4 across npm + lockfile, Cargo + lockfile, Tauri config, the Python
package, `BRIDGE_VERSION`, the Greek Room engine version, the frozen sidecar
assertion in `scripts/smoke_sidecars.py`, the project-import generator
metadata, the provider User-Agent, and the current-version docs. Historical
version statements in BUILD_LOG and HANDOFF are left as written. `engine.info`
reports `bridgeVersion` and `greekRoom.engineVersion` 0.9.4; `cargo metadata`
reports 0.9.4 and accepts the lock.

# Current continuation handoff refresh (2026-09-08)

Updated `docs/archive/HANDOFF.md` §37.10 with the exact repository, installed candidate,
last regression-gate, read-only Stage 9B.3c evidence, Stage 9B.4 acceptance, and
stop-boundary state needed by the next developer. No source code, Scripture,
acceptance fixture, database, build metadata, or application behavior changed.

The next authorized operational boundary remains the installed 0.9.4 A/B/C
acceptance described in `docs/archive/STAGE_9B4_ACCEPTANCE.md`. Version 0.9.4 remains a
candidate and has not been released.

# Stage 9B.4 acceptance-fixture queue-visibility repair (2026-09-08)

## Symptom

The installed 0.9.4 candidate opened acceptance case A correctly, but
Alignment Review → QA showed `Not analyzed` / `Showing 0 of 0 possible
issues` / `No findings match these filters`, even with **Stale only**
selected. The read-only inspector proved the finding was present and correct
in both the seeded folder and Bridge's managed copy
(`CONFIRMED_TRANSLATION_ERROR` / `HUMAN_APPROVED` / `STALE`, proposal-1
`ACTIVE`+`PENDING`, `acceptance-a-apply` `COMPLETED`, affected analysis
`COMPLETED`, PHP 1:3 → PHP 1:6, word alignment `INVALID`), so project
resolution and copying were not the defect.

## Confirmed root cause

The A/B controlled fixtures reuse the Stage 9B unit-test `_fixture()` in
`engine/tests/correction/test_correction_stage9b3b.py`. It called
`repository.create_qa_finding()` — which inserts a *minimal* row — and then
wrote the human-decided state with raw SQL touching only
`qa_disposition`, `review_status`, `lifecycle_status` and `payload_json`.

Every schema-v8 Stage 9A queue index column therefore kept its empty
create-time default, confirmed by reading the seeded database directly:

```text
book = ''                 kind = 'NEEDS_PASSAGE_REVIEW'
severity = ''             severity_rank = 99
sort_chapter = 0          sort_verse = 0
displayed_reference = ''  qa_finding_scope_references: []
```

The decisive one is the empty `qa_finding_scope_references` table.
`query_qa_findings()` resolves a canonical scope through that table
(`side='SOURCE'` for a `SOURCE_COVERAGE` finding), so with no rows the
fixture could never match a real UI scope, and the empty `book` column
failed the book filter as well. Measured against the seeded case A database
before the fix:

```text
no scope, STALE          total=1  ['finding-1']
scoped PHP 1:3–1:6       total=0  []
book=PHP                 total=0  []
```

`qa_finding(id)`, the correction services and the inspector all read the
payload, which was complete — which is exactly why every earlier check
passed while the queue stayed empty.

## Fix

`_fixture()` now constructs a real `QaFinding` and persists it through
`repository.save_qa_finding()`, the same canonical path Stage 8's
`QaAuditEngine` uses — one atomic write that maintains `payload_json`, all
denormalized queue columns and the scope-reference rows together. Severity
comes from `QaAuditPolicy.severity_for()` rather than a literal, so the
fixture cannot drift from the product's own severity policy. No column is
hand-patched, `query_qa_findings()` is unchanged, nothing special-cases
`finding-1`, and the UI does not bypass the queue.

The fixture's identity is unchanged: `finding-1`,
`CONFIRMED_TRANSLATION_ERROR`, `HUMAN_APPROVED`, `STALE` after apply,
PHP 1:3 → PHP 1:6, revision 1 at creation so every existing
`expected_finding_revision=1` caller still holds. Proposal, application,
affected-analysis and verification fixture state are untouched.

## "Not analyzed" is not a suppressor

`AlignmentQaMode.svelte` renders the `NOT_ANALYZED` sentence *inside*
`{#if scopeReady && !$reviewLoading && $reviewTotal === 0}` — it is a
consequence of an empty queue, not a cause. The queue list renders
independently. No frontend repair was needed, and the tester still must not
click Run analysis on A or B.

## Regression

`engine/tests/correction/test_correction_acceptance_queue_visibility.py` seeds A and B
with the real seeder into a fresh folder, opens them through
`BridgeEngine`/`project.open`, and reads the actual `qaReview.getQueue` API
(never SQLite) at the acceptance UI scope PHP 1:3–PHP 1:6 with
`lifecycleStatuses=["STALE"]`, asserting `totalCount >= 1`, that `finding-1`
is returned, and that `qaReview.getFinding` plus
`correction.listForFinding` still reach the proposal and application. It
repeats all of that against the authoritative managed copy under
`<app data>/projects` (registry `managed=True`), and a third test asserts the
denormalized columns and scope rows directly, so a payload-only regression
cannot pass it. All six cases fail on the pre-fix helper and pass after it.

## Gates

`test_correction_acceptance_queue_visibility.py` 6 passed;
`test_correction_acceptance_scripts.py` + `test_correction_stage9b3b/3c/9b4.py`
85 passed; `test_qa_review_service_stage9a.py` +
`test_qa_review_stage9a.py` + `test_php_review_walkthrough_stage9a.py`
90 passed; `git diff --check` clean.

**Only test and acceptance-fixture code changed. No product frontend,
backend or Rust code changed, so the 0.9.4 installer does not require
rebuilding.** `C:\bridge-acceptance` was deleted and reseeded, and the
inspector re-verified case A. Queue visibility was then confirmed on copies
of the reseeded projects (leaving the seeded folders untouched for the
tester): case A source `totalCount=1`, case A managed `totalCount=1`, case B
source `totalCount=1`, case B managed `totalCount=1`, each returning
`finding-1` as `CONFIRMED_TRANSLATION_ERROR` / `HUMAN_APPROVED` / `STALE`.

0.9.4 remains an unreleased candidate. Stage 9B.4 verification semantics are
unchanged, and installed acceptance has not been run.

# Bridge 0.9.4 release authorization (2026-09-08)

The project owner subsequently authorized `v0.9.4` for publication as the
latest stable GitHub release after the recorded gates completed. This explicit
decision supersedes the candidate-only hold above. Release notes are stored in
`docs/RELEASE_0.9.4.md`; the Windows release asset is
`Bridge_0.9.4_x64-setup.exe`, SHA-256
`6F3960A6AEB6BF03568267A9E421EB9B62C09EF893364126E56905A32D763951`.

# Bridge 0.9.5 release (2026-09-08)

The project owner authorized `v0.9.5` for publication as the latest stable
GitHub release, published directly rather than as a draft. 0.9.5 is a
maintenance release: version fields moved 0.9.4 → 0.9.5 across `package.json`,
`package-lock.json`, `src-tauri/tauri.conf.json`, `src-tauri/Cargo.toml`,
`engine/pyproject.toml`, `BRIDGE_VERSION`, `ENGINE_VERSION`, the
`translationCore-AI-Bridge` user agent, the import generator build stamp and
`tc_ai_bridge.__version__`. The only functional change over 0.9.4 is the
Stage 9B.4 acceptance-fixture queue-visibility repair recorded above; no
product frontend, backend or Rust code changed, and the installer was rebuilt
only so the numbered build matches the version fields.

Release notes are stored in `docs/RELEASE_0.9.5.md`; the Windows release asset
is `Bridge_0.9.5_x64-setup.exe`, SHA-256 `58ABC03FAF19D6880F093A9AA7A722F94302FDBCB0B89339B6B335CEF4008F0C`.

# Issue backlog reconciliation and #28 governance decision (2026-09-09)

No product code changed. This session reconciled the GitHub issue tracker and
project board #6 ("Bridge: Full Bible QA Orchestrator") against repository
reality at `5e220a5`, and recorded one governance decision.

## The board and the issue state had diverged

Eleven issues were marked **Done** on the board while still **OPEN** on GitHub.
Each was checked against the actual code rather than trusting either source.
Eight were genuinely complete and were closed with the proving commit and
file:line recorded on the issue:

```text
#29  verse actions to top, three AI review scopes   ReviewPanel.svelte:619-630
                                                    1ed775c 3a50d76 967547b 1d3cc59
#30  alignment occurrence counter                   AlignmentModal.svelte:97-99,325,341
                                                    7c215c0
#32  correction.applyProposal sidecar timeout       sidecar.rs:176 (=180), test :556
#35  Automatic AI review panel removed              panel gone, progress bar kept
                                                    ReviewPanel.svelte:654
#37  CI release build and publish                   .github/workflows/release.yml
#38  finding context menu                           FindingContextMenu.svelte
                                                    831f35c 889e355 12eb081
#39  base font size and Tamil→Vijaya                a06b3a2 + index.css:79-89
#40  settings panel closes after save               86843a0
```

Three were **not** implemented despite the Done marking, and were moved back to
Backlog on the board:

- **#23** (passage-aware many:many alignment). The AI proposal schema is still
  strictly pairwise 1:1 — `normalize_link_response()`
  (`alignment_reliability.py:150`) requires exactly one `top_id` and one
  `bottom_id` per link and raises otherwise (lines 159-162). Multi-token groups
  appear only downstream when `compile_link_proposal` merges pairwise links.
  What *did* land is `propose_alignment()`'s `cross_verse_alignment_exclusions()`
  call (`ai_client.py:348`), which is a **protective** use of the semantic layer
  enforcing §39's *never fake cross-verse alignment for translationCore* — not
  richer proposal generation.
- **#28** (tN/tW auto-apply above 85%). `bridge_service.py:1869` still gates at
  `0.82` with all five policy checks intact in `_safe_ai_selection_reason()`
  (line 1856).
- **#31** (semantic filtering of Greek Room findings). No suppression state
  exists in the QA list UI. The confusion is likely with AI triage (`68ae7cc`,
  `b568bf9`, merged as #41), which is model-based false-positive *scoring* on
  the project report — online-only, per-finding model cost, a different surface
  and a different mechanism from reusing already-computed Stage 6B/7/8 evidence.

The lesson matches this repo's standing rule: a status marker is a claim about
the code, and claims get verified against the code.

## #28 decided: overwrite protection is kept

The open governance question flagged in `4e5a94a` and HANDOFF §"Queued change
that touches the §39 boundary" was settled by the project owner. "Confidence
alone decides" applies to **empty** selections only; AI still never overwrites
an imported or human-made tN/tW selection. `save_check_selection()` keeps that
protection and `test_basic_ai_never_overwrites_a_human_selection` remains its
regression cover. HANDOFF is updated in place with the decision.

## Six thin issues expanded

#12, #14, #16, #17, #18 and #19 were one-to-three-line stubs (148-298 chars).
Each was rewritten against current code with a verified current-state section,
scope, acceptance criteria and the real hazards recorded in this repo. The
substantive finding, in #12:

**There are now five independent job managers, not one**, each with its own
conflict exception, each running a single thread, each allowing one active job:

```text
engine/check_jobs.py                CheckJobManager       280 lines  in-memory
engine/ai_review_jobs.py            AIReviewJobManager    280 lines  in-memory
engine/report_jobs.py               ReportJobManager      198 lines  in-memory
engine/project_sweep.py             ProjectSweepManager   170 lines  in-memory
engine/tc_ai_bridge/analysis_jobs.py AnalysisJobManager   720 lines  PERSISTS
```

Only `AnalysisJobManager` survives a restart. The other four hold state purely
in process memory, so "resume after app restart" is not one feature but four
modules needing a persistence story they do not have. #12's original body also
pointed at `tc_ai_bridge/check_jobs.py`; the file is `engine/check_jobs.py`.

## #44-#47 confirmed as a real direction, conflicts recorded

The project owner confirmed that database persistence, user management,
real-time collaboration and project-management workflows are planned direction,
not speculative capture. The architecture conflicts were recorded on #44 with
cross-references from #45, #46 and #47, so they are decided deliberately rather
than discovered mid-implementation. The load-bearing points:

- **Bridge already has a database.** `passage_semantic_repository.py` is real
  SQLite at `DATABASE_SCHEMA_VERSION = 14` with forward migration and a
  newer-than-supported guard (line 1092). #44 is therefore not "files →
  database" but "which remaining file stores move, and which must not".
- **The translationCore-compatible on-disk shape is a compatibility contract.**
  `manifest.json`, `<book>.usfm`, `<book>/<chapter>.json` and
  `.apps/translationCore/**` cannot move without ending interoperability.
  Bridge-private `.bridge/**` and `.apps/translationCoreAI/**` are fair game.
- **`actor_id` defaults to the literal string `"human"`** (lines 3695, 4212,
  4275). Every existing decision and correction is signed that way, so #45
  needs an explicit migration decision — silently reassigning them would
  misattribute human decisions, which §39 protects. Roles also create an
  unanswered policy question: may user B overwrite user A's confirmed decision?
- **`expected_revision` optimistic concurrency exists** (line 178 schema, lines
  3695/4212/4275 API) and is real groundwork for #46, but it is single-writer
  shaped: it rejects a stale write rather than merging.
- **One desktop window, one sidecar** is the current process model and is the
  largest conflict, upstream of #44/#45/#46 alike.

## Gates

Documentation and issue-tracker changes only. No Python, frontend or Rust
source changed, so per the repository's testing policy the full Python suite
was **not** run and is not claimed. `git diff --check` passes.

# Bridge 0.9.6 release (2026-09-10)

The project owner authorized a safe new `v0.9.6` release instead of moving
the already-public `v0.9.5` tag. Version metadata was synchronized across
Node, Tauri/Rust, the Python engine, Greek Room, API user-agent, import
generator, active documentation and acceptance tooling.

0.9.6 contains the Stage 9B.4 real Case C repair:

- Stage 5 immutable semantic-unit persistence identities now bind their exact
  resource evidence and audit owner while stable semantic fingerprints remain
  meaning-only;
- a changed tN/tW/TWL snapshot can no longer combine old semantic-unit rows
  with a new evidence payload;
- correction verification is refreshed from the backend after every terminal
  affected-analysis state;
- a real Stage 5 through Stage 8 cross-verse correction/re-analysis regression
  protects PHP 1:3 source provenance, PHP 1:6 target mutation, the PHP
  1:3--1:6 affected range and invalid/reviewable Word Alignment.

Release gates:

```text
focused Stage 5/foundation/invalidation/Stage 9B.3a-9B.4/real Case C  159 passed
full Python + Greek Room                                                1026 passed
frontend Vitest                                                         310 passed / 24 files
CorrectionReviewPanel Vitest                                             54 passed
npm run check                                                             0 errors / 0 warnings
npm run build                                                             passed
cargo check                                                               passed
cargo test                                                                12 passed
Tauri/NSIS release build                                                  passed
git diff --check                                                          passed
```

The frozen-sidecar smoke check passes the 0.9.6 version handshake and then
stops at the already-documented synthetic duplicate-project classification
mismatch. It is not claimed as passing.

Release notes are stored in `docs/RELEASE_0.9.6.md`. The Windows asset is
`Bridge_0.9.6_x64-setup.exe`, 57,751,384 bytes, SHA-256
`C7328D6C0BD48C570B0A24391630744D6F0449CF7FE6217ECF4F6EF0BC7D0C3D`.

# V1.1 world-language Unicode semantic normalization (2026-09-10)

Baseline: `main` at release commit/tag `b0de092` / `v0.9.6`, companion
schema v14, correction verification policy v2. This stabilization changes
semantic comparison only; it does not change the public app version, the
database schema, authoritative Scripture, translationCore alignment data, or
persistent coordinate semantics.

## Root cause and dependency trace

Stage 7's `_comparison_norm()` decomposed input to NFD and then ran Python
stdlib `re.findall(r"[^\W_]+")`. Python's word class excludes Unicode Mark
code points, so orthography was fragmented at vowel signs, viramas and
diacritics. Examples from the old function included `இல்லை -> இல ல`,
`नहीं -> नह`, `عَرَبِيّ -> ع ர ب ي`, `Ἰησοῦς -> ι ησου σ`, and
`Việt -> vie t`. The POLARITY path requires whole tokens, so Greek `οὐ` and
Tamil `இல்லை` were falsely classified as contradictory.

The audited dependency path is:

```text
current raw target Scripture (chapter JSON; never rewritten)
  -> Stage 6A NFC token metadata / raw code-point spans (unchanged)
  -> Stage 6B frozen location and exact raw quote (unchanged)
  -> unicode_comparison.py transient NFC + casefold + grapheme runs (new)
  -> Stage 7 deterministic component comparison (fixed)
  -> Stage 8 coverage/support synthesis (consumes fresh Stage 7)
  -> Stage 9B.4 direct Stage 7 recheck and verification (fresh fingerprint)

frontend code-point helpers / correction CAS (inspected; unchanged)
```

Other normalization paths were inspected. `passage_semantic_runtime.py`
already tokenizes with Unicode Letter/Mark/Number properties and persists raw
code-point spans; `semantic_location.py` uses NFC/casefold but does not strip
marks; `semantic_mapping.py` retains L/M/N source quote material; and the
frontend uses `Array.from()`/explicit conversion helpers at semantic span
edges. No source-resource, Scripture-write, or translationCore normalization
path was changed.

## Architecture

`unicode_comparison.py` defines
`unicode-comparison-nfc-grapheme-v2`. It uses canonical NFC (never NFKC/NFKD),
Unicode `casefold()`, a second NFC stabilization after folding, and one
module-compiled `regex` `\X` expression. Comparison tokens are conservative
punctuation/separator/symbol-delimited orthographic runs. Letter and Number
bases plus all attached Mark clusters survive. Internal format controls such
as ZWJ/ZWNJ/WORD JOINER survive; standalone leading/trailing directional
controls do not become tokens. Empty, punctuation-only, mark-only, emoji and
supplementary-plane inputs fail safely.

Stage 7's controlled UHB inventory retains its prior intentional ability to
compare pointed/cantillated Hebrew with unpointed category forms through an
explicit opt-in Biblical-Hebrew annotation fold. The default Unicode
normalizer preserves Hebrew marks, all Arabic harakat, and marks in every
other script. This policy derives a transient key only; it never changes UHB
text, token identity or evidence IDs.

The bounded 8,192-entry in-process key cache avoids repeatedly segmenting the
small controlled Stage 7 inventories. A 13-script microbenchmark over 10,000
calls measured the legacy regex at 13.90 microseconds/call, a cold grapheme
normalization at 202.34 microseconds/call, and the normal cached Stage 7 path
at 0.38 microseconds/call. The Unicode-correct cold path is costlier but still
sub-millisecond for the representative multi-script string; regexes are not
compiled per call.

## Cache and persistence safety

- Stage 7 engine: `bridge-meaning-analysis-v1` ->
  `bridge-meaning-analysis-v2`.
- Stage 7 deterministic model: `deterministic-component-comparator-v1` ->
  `deterministic-component-comparator-v2`.
- New explicit comparison version:
  `unicode-comparison-nfc-grapheme-v2`.
- Analysis-job `policyVersions`, the Stage 7 run fingerprint/payload, and the
  Stage 9B.4 verifier fingerprint carry the comparison version. Stage 8's key
  already consumes the Stage 7 run ID/fingerprint, so it cannot reuse an old
  Stage 8 result after this change.
- Existing stable target coverage-account identities remain stable so human
  review survives, but Stage 8 now refreshes their derived status and covered
  relationships on every genuine cache miss. An old algorithm's mutable
  status can no longer remain attached to a fresh run.

Schema remains v14. Persistent spans remain exact half-open Unicode code-point
offsets `[startCodePoint,endCodePoint)` over raw Scripture. Correction apply
still requires exact reference, raw span text, revision/hash and CAS; it never
uses a normalized key and never performs fuzzy relocation.

## Regression matrix and semantic consequence

Focused tests cover Tamil, Devanagari, Malayalam, Telugu, Bengali, Kannada,
Gujarati, Gurmukhi, Odia, Sinhala, Hebrew, Arabic, Greek, Vietnamese, Latin,
Thai, Khmer, Myanmar and Lao, plus NFC/NFD equivalence, punctuation,
ZWJ/ZWNJ/WORD JOINER, malformed mark-only data, emoji and supplementary-plane
letters. The Tamil production-path regression runs the bundled UGNT source for
PHP 1:22 through Stage 5 -> 6A -> 6B -> 7 -> 8 against an NFD-encoded `இல்லை`
target fixture. Stage 7 now records PRESERVED polarity, Stage 8 emits no false
NEGATION_PROBLEM, and the chapter JSON remains byte-identical. Stage 9B.4's
fresh direct recheck agrees and can pass only because persisted current
evidence is independently PRESERVED/COVERED. Its `POSSIBLY_MISSING ->
UNCERTAIN` contract is unchanged.

No second real Indic or Vietnamese translation corpus is bundled with the
repository. Those scripts are therefore covered at the normalization/property
layer, not presented as production semantic-language validation; adding
invented lexical semantics solely for a test would violate this task's
language-independent boundary. Thai/Khmer/Lao/Myanmar grapheme integrity is
guaranteed, but dictionary-quality word segmentation is explicitly not
claimed.

## Verification

```text
focused Unicode + Stage 7 + Stage 8 + analysis cache + Stage 9B.4 + Case C
                                                                    175 passed
frontend Vitest                                              310 passed / 24 files
npm run check                                                0 errors / 0 warnings
npm run build                                                passed; existing >500 kB warning
cargo test                                                   12 passed
cargo check                                                  passed
full Python + Greek Room                         1069 passed / 0 failed (36m32s)
git diff --check                                             passed; line-ending notices only
```

The first sandboxed Cargo runs reported both process-tree termination tests as
failed because `taskkill` lacked permission. The required unsandboxed rerun
passed all 12; no Rust change was made. No installer or public release was
built.

# V1.1 installed-acceptance build, real Tamil-negation fixture, and V11-001
verse-editor fix (2026-09-11)

## Local acceptance build

`.\scripts\build-sidecars.ps1` rebuilt both frozen executables from V1.1 HEAD
(they had been stale since the pre-V1.1 `b0de092` release build). `npm run
tauri build` then produced the installer. Because the app version is
unchanged (`0.9.6`), the NSIS bundler writes the same filename as the
published release, `Bridge_0.9.6_x64-setup.exe` — so the existing published
installer (sha256 `c7328d6c0bd48c570b0a24391630744d6f0449cf7fe6217ecf4f6ef0bc7d0c3d`,
built 2026-09-09 from `b0de092`) was copied out before the build. After the
build, the new artifact was copied to a distinct name,
`Bridge_V1.1-prerelease_x64-setup.exe` (sha256
`c961c640c60ba73485da2f4c840829b3da74266982bbe2a05a7acef30112b52c`, size
57,763,124 bytes, embedded `FileVersion`/`ProductVersion` still `0.9.6`), and
the original published bits were restored to their original filename —
verified byte-identical (same sha256) before and after. Neither file was
deleted or silently overwritten. A `smoke_sidecars.py` run against the fresh
frozen `bridge-engine.exe` failed one unrelated assertion (project-duplicate
classification returned `possibleDuplicate` instead of `exactDuplicate` on
self-check); `git show --stat` on all three V1.1 commits confirmed none touch
`project_registry.py`/`project_import.py`, so this is a pre-existing,
out-of-scope issue, not a V1.1 regression — likely local project-registry
state accumulated across repeated dev-machine runs, not investigated
further. Full acceptance-case detail lives in `docs/archive/V1_1_UNICODE_ACCEPTANCE.md`.

## Real Tamil-negation acceptance fixture (V1.1 Case A)

The pre-existing Stage 9B.4 acceptance fixtures (`scripts/seed_correction_acceptance.py`,
`seed_case_a`/`seed_case_b`/`seed_case_c`) exercise the correction loop but
never the Unicode-comparison fix itself. Added `seed_case_a_negation()`,
wired into `main()` as a fourth destination folder `D-tamil-negation`
(deliberately not reusing `A`, which already names the Stage 9B.4
PASSED-controlled fixture). It restages, verbatim, the exact source/target
text already validated by two `ef49e9e` unit tests:

- `test_meaning_analysis_stage7.py::test_tamil_negation_preserves_polarity_with_combining_marks`
  — `DeterministicMeaningComparator.compare("οὐ", "இல்லை", "POLARITY", "NEGATION", ...)`.
- `test_qa_audit_stage8.py::test_tamil_negative_preservation_does_not_emit_false_negation_problem`
  — book `PHP`, chapter `1`, verse `22`, language `ta`, target text `இல்லை`.

Real bundled UGNT `PHP 1:22` genuinely ends "...οὐ γνωρίζω", which is why
that reference was chosen — not an invented pairing. Unlike `seed_case_a`/
`seed_case_b` (which publish controlled evidence directly), this follows
`seed_case_c`'s pattern: builds a minimal project at that reference,
registers it, and runs the real Stage 5→6A→6B→7→8 pipeline through
`AnalysisJobManager` with a `FixtureEmbeddingProvider` pairing `οὐ`/`இல்லை`.

Before trusting the result, confirmed the source panel genuinely reflects
bundled Greek, not the "no original-language source" failure mode a wrong
reference would produce — read the persisted source semantic unit and its
underlying token instance directly: `rawSurface: "οὐ"`, `provenance:
"DETERMINISTIC_RULE"`, and critically `resourceId: "ugnt"`, `resourceVersion:
"0.34"`, `resourceHash: "319eaef950cd855aae56a293483223cee6240df03e72e5364ee674e05eee8472"`,
`strong: "G3756"`, `morphology: "Gr,D,,,,,,,,,"` — a real bundled token, not
fabricated.

Result, first try, no adjustment: Stage 7 POLARITY component `PRESERVED`,
confidence `0.96`, explanation "Explicit negative polarity is present on
both sides." Stage 8: 9 findings, all `POSSIBLE_OMISSION` (expected — only
`οὐ` carries a matching fixture embedding vector, so every other real PHP
1:22 source token legitimately has no located target correspondence), zero
`NEGATION_PROBLEM`. Reproduced identically (same deterministic source-unit
id) across two independent seeding runs. Additive only:
`git diff --stat` showed 111 insertions, 0 deletions, and `seed_case_a`/
`seed_case_b`/`seed_case_c` and their destination folders are untouched
(confirmed via the two existing tests that import them by name,
`test_correction_case_c_production.py` and
`test_correction_acceptance_queue_visibility.py`, neither of which
references `main()`'s case list or count).

## V11-001: verse editor did not refresh after a correction applied

**Symptom**, reproduced in installed acceptance: after applying a
`some`→`all` correction at PHP 1:6, the editor pane kept showing "some
remembrance..." until a full project reload, even though Word Alignment
already showed the token "all".

**Root cause**, confirmed by reading, not re-derived: `CorrectionReviewPanel
.svelte`'s `applyCorrection()` — on `applicationState === "COMPLETED"` it
refreshed only eligibility, context, and `reloadProposals()`, never the
shared `verseTexts` store. `saveVerseEdit()` in `verseEditor.ts` (the
direct-edit path) does update that store, which is why direct edits render
immediately and corrections did not. `App.svelte`'s `ensureChapterData()`
early-returns once a chapter's verses are already cached, so nothing
re-fetches on its own short of a full reload.

**Fix**, respecting the Phase 2 invariant that frontend state must mirror
persisted backend state rather than compute it locally: traced
`correction_application.py`'s `apply()` — it writes the final text, hash-
verifies the write, and persists the writer's own result into
`result_metadata={"canonicalEdit": result}`, which `bridge_service.py`
returns straight through as part of `CorrectionApplicationIntent`. Confirmed
against a real applied record (read during the acceptance-build work above)
that the wire shape is exactly `resultMetadata.canonicalEdit.newText` in
camelCase — the backend's own authoritative record of what it wrote, not
something to recompute from `finalVerse`/`selectedProposal.proposedText`.
Added `refreshVerseTextFromApplication()` to `verseEditor.ts`: parses the
application's `targetDisplayedReference` (the correction's target verse —
`selectedSpan`'s reference, never the finding's source reference, which
differ for cross-verse corrections) using the same "BOOK C:V" regex
`SemanticMappingValidation.svelte`'s `navigate()` already relies on (not a
new ad-hoc pattern), then updates `verseTexts` and marks
`alignmentStatusByVerse` `invalid` for that verse — mirroring
`saveVerseEdit()`'s existing update shape. One import and one call site
added to `CorrectionReviewPanel.svelte`; no restructuring, no backend or
protocol change.

**Test-first.** Wrote two Vitest cases in `CorrectionReviewPanel.test.ts`
first: applying a cross-verse correction (source `PHP 1:3`, target
`PHP 1:6`) must update only the target verse's text and mark its alignment
invalid, leaving the source verse's text untouched; a non-`COMPLETED`
application state must touch neither store. Proved the first case fails
without the fix — `git stash`ed just the two source files (not the test),
reran: 55 passed, 1 failed (`expected 'some remembrance...' to be 'all
remembrance...'`); the second case passed even pre-fix, correctly, since
nothing should touch the store either way when the state isn't `COMPLETED`.
Restored the fix (`git stash pop`) and reran: 56/56 passed.

## Verification

```text
Vitest (full suite)          312 passed / 24 files  (baseline 310/24; delta
                                                      is exactly the 2 new tests)
npm run check                0 errors / 0 warnings
npm run build                passed; existing >500 kB warning
  (+0.36 kB from the new code)
git diff --check             passed
```

Schema (`v14`), verification policy (`correction-verification-policy-v2`),
and app version (`0.9.6`) are unchanged. Nothing was committed or pushed as
part of building/testing this work; `docs/archive/HANDOFF.md` §44.10 records the
same summary. Full Python/Greek Room and Rust suites were not rerun this
session (explicitly deferred, not silently skipped) — the last confirmed
full-Python run this cycle was 1063 passed / 1 skipped / 0 failed, and nothing
touched under `engine/` since.

---

## 2026-09-11 — #74 steps 1–2: packaged test suite, markers, xdist; the baseline it was measured against

Context: `docs/TEAM_ARCHITECTURE.md` §9. The test-speed work lands before the
persistence, identity and hub steps (#75–#81) because every one of those adds its own
test package. Two commits: `8952fca` (step 1, the move) and the commit carrying this
entry (step 2, config + parallelism).

### Serial baseline, before any change (isolated git worktree at `ae30e17`)

```
pytest tests/ greek_room_engine/tests/ -q -p no:cacheprovider --durations=0 -rs
1078 passed, 0 skipped in 3713.57s (1:01:53)      Windows 11, Core 7 150U (10 cores / 12 threads), Python 3.12.10
```

Caveat on the wall time: the first ~25 minutes overlapped with a 366-test subset run in
the main checkout, so the true serial figure is lower. The Windows CI runner measured
41m39s for 1063 tests (`ci.yml`). 0 skipped rather than CI's 1 because real
`wildebeest-nlp` is installed here.

What the durations actually say — the cost is repeated fixture **setup**, not slow
tests. 1021 tests recorded ≥ 5 ms; 148 exceeded 5 s; 59 exceeded 20 s; the top
`setup` entries are 45–63 s each and all rebuild the Stage 5–8 pipeline over the Tamil
PHP fixture from scratch, once per test:

| file (pre-move path) | total s | tests | > 5 s |
|---|---:|---:|---:|
| test_qa_review_service_stage9a.py | 660 | 37 | 33 |
| test_qa_target_hash_contract_stage8_9b.py | 465 | 9 | 9 |
| test_meaning_failure_eligibility_stage9b.py | 377 | 72 | 13 |
| test_source_semantic_inventory_stage5.py | 254 | 16 | 12 |
| test_ai_explain.py | 203 | 11 | 9 |
| test_semantic_location_stage6b.py | 195 | 14 | 14 |
| test_qa_audit_stage8.py | 169 | 30 | 13 |
| test_correction_stage9b0.py | 151 | 50 | 4 |
| test_correction_stage9b4.py | 138 | 61 | 2 |
| test_resource_materializer.py | 129 | 10 | 8 |

Roughly 2,700 of 3,706 measured test-seconds are this pattern. Filed as #82 (share the
pipeline build at module scope) — it belongs with #74 step 4's `tests/support/`, not
before it. Parallelism divides this cost by the core count; it does not remove it.

### Step 1 — the move (`8952fca`)

62 test files → `tests/{service,jobs,persistence,semantic,review,correction,alignment,
ai,project_io,connectors,resources,versification}/`; `fixtures/` and both goldens
untouched. `--collect-only` = 1078 before and after. The 17 files whose rewrites only
execute at test time (cross-package imports, the 9B.0 load-by-path, every
`Path(__file__).parents[N]` site now via `tests/support/paths.py`) ran green: 366
passed. Two facts the exploration got wrong and the move corrected: `tests/` *was*
already a package (`tests/__init__.py`), and `scripts/seed_correction_acceptance.py`
imports two test modules, so it had to move with them.

### Step 2 — config, markers, xdist (this commit)

- `engine/pyproject.toml` `[tool.pytest.ini_options]`: `testpaths`, `pythonpath`
  (`.`, `..`, `../scripts`), `--strict-markers --durations=25 --durations-min=1.0`,
  `tmp_path_retention_policy = "failed"`, six registered markers.
- `tests/conftest.py` applies directory/file markers automatically; `slow` is
  file-level, on the twelve files above plus the two subprocess files. Split:
  **220 slow / 858 not slow**.
- `pytest-xdist>=3.5` in the dev extra, pinned `3.8.0` (+ `execnet==2.1.2`) in
  `constraints-py312-windows.txt`; `ci.yml` runs `-n auto`.

```
pytest -q -p no:cacheprovider -n auto -rs            (12 workers, main checkout, otherwise idle)
1077 passed, 1 failed in 1136.11s (0:18:56)
```

So ~19 min against a ~42–62 min serial run: about **3×, not 12×**. Individual tests
slowed 1.5–2.7× under 12 workers (e.g. `test_correction_case_c_production` 49 s → 135 s;
the 60 s pipeline setups became 75–88 s) — the work is CPU-bound and 12 threads are 10
cores. This is why #82 is the real lever and xdist is only the first.

The one failure was `tests/jobs/test_analysis_jobs_stage9a4.py::
test_normal_unseeded_runtime_runs_without_fixture_vectors`: a real Stage 5–8 job with a
15 s wait, 9.9 s serial, 7.7 s alone, over budget only under 12-way contention. Its wait
is now 60 s with a comment; nothing it asserts changed. It passed alone before the edit.

Not done yet: step 3 (`scripts/affected_tests.py`, selection on PRs, full suite on
every push to `main`, nightly) and step 4 (RPC-wiring tests out of the stage files,
shared builders in `tests/support/`). Not verified: the CI runner's wall time under
`-n auto` on 4 vCPUs — the first push carrying this entry will show it.

### CI measurement (same day) — `-n auto` reverted on the runner

The push carrying step 2 measured what the local run could not:

| run | engine job | pytest summary |
|---|---|---|
| 34598941837 (step 1, serial) | 36m01s | 1072 passed, 1 skipped in 35:18 |
| 34603932107 (step 2, `-n auto`) | 55m38s | 1072 passed, 1 skipped in 54:51 |

Parallel was **1.55× slower** on windows-latest. The slowest-25 list shows why: the
same Stage 5–8 setups that take ~60 s serial took 150–163 s with four workers on four
shared vCPUs (`test_correction_case_c_production` 49 s → 263 s). Locally `-n auto` is
a ~3× win on 10 real cores; on the runner it is a loss until #82 removes the repeated
setup. `ci.yml` is back to the serial command with the measurement in its comment;
`pytest-xdist` stays installed and stays the documented local default. CI collects
1073 rather than the local 1078 because `wildebeest-nlp` is not installed there, so
`greek_room_engine/tests/test_wildebeest_real.py` skips at import (one skip entry,
five tests never collected) — identical in both runs, not an effect of this work.


### #82 — the "slow fixture setup" was SQLite fsync, not stage logic (2026-09-11)

The step 0 baseline blamed the slow tail on per-test Stage 5–8 rebuilds (#82). A
cProfile of one Tamil PHP 1:3–6 build (`tests/review/test_qa_review_service_stage9a.py`'s
`_project` + `_run`, run on its own) says the rebuild itself is cheap and the
repository's I/O pattern is not:

| where | one pipeline build |
|---|---:|
| total | 11.4 s |
| `sqlite3.Connection.commit` × 720 | 5.4 s |
| `Connection.close` × 1,789 (WAL checkpoint on last close) | 1.9 s |
| `Connection.execute` × 12,200 | 1.8 s |
| `sqlite3.connect` × 1,789 | 0.8 s |

`FoundationRepository._connect` opens a fresh connection per method call with
`PRAGMA synchronous = FULL` + WAL and closes it; every commit is an fsync. Swapping
only the pragmas (nothing else) on the same build: FULL/WAL 10.0 s, OFF/WAL 5.9 s,
OFF/MEMORY 3.1 s. `synchronous = NORMAL` was no faster than FULL here.

**Landed:** a session-scoped autouse fixture in `tests/conftest.py` that replaces
`_connect` with `synchronous = OFF` + `journal_mode = MEMORY`; `BRIDGE_TEST_DURABLE_SQLITE=1`
restores the product pragmas. The product code keeps FULL — that database is months of
a team's work and the durability guarantee is not the test suite's to trade.

| run | before | after |
|---|---|---|
| `test_qa_target_hash_contract_stage8_9b.py`, 9 tests, serial, local | 98.8 s | 23.7 s |
| full suite, `-n auto`, local (10 cores) | 18:56 (step 2, 1077 passed) | **3:17** (1079 passed) |

Nothing asserted changed; the count is 1078 + one new regression test. Not measured:
the serial CI runner, which the first push carrying this entry will show — its 46–62 s
setups against the same code's 10 s locally point at slower fsync on the runner's disk,
so the gain there should be at least proportional. `-n auto` on the runner is worth
re-trying once that number is in (the step 2 loss was fsync contention across four
workers, which this removes); not changed in this commit. The `_SLOW_FILES` marker list
was chosen at ~5 s average per test under the old I/O and is now stale as a *measure*;
re-measure it as part of step 4 rather than guessing.

**What the speed-up exposed (#84):** with fsync gone, `test_correction_stage9b1.py`
failed in two of four runs with the proposal history in the wrong order
(`['CREATED', 'REJECTED', 'EDITED']`). `correction_proposal_history`,
`correction_verification_history` and `review_records` ordered by `created_at,id`;
ids are `uuid4` and on Python 3.12/Windows `datetime.now()` advances about every 15 ms
(200,000 calls → 48 distinct values), so events written in one tick sorted at random.
The fsync between writes had been hiding it; the app's apply → re-analysis flow can
write several events to one proposal inside one request, so the audit trail could
already come back out of order on a fast disk. Fixed by tie-breaking on `rowid`
(insertion order; no table is `WITHOUT ROWID`, the ledgers never delete, nothing runs
`VACUUM`) with a regression test that freezes `_now` over ten events and fails on the
old query. The durable fix is a monotonic sequence column per ledger table — a v15
migration, and the maintainer's call — and the same rule applies to #75's `change_log`.
The repository-level cost itself (a connect + checkpoint + fsync round trip per method
call, in the app too) is recorded on #43.

**What #82's original proposal is now:** still valid, second-order. A build costs ~3 s
after this, and per-test rebuilds are what keeps tests isolated from each other's
mutations; module-scoping is worth doing where a test is read-only, after step 4 gives
the builders a home.

### CI measurement (same day) — serial runner with fsync off

Run 34615835615 (push 369f648, windows-latest, serial): **1073 passed, 1 skipped in
14:50**, engine job 15m38s. The serial baseline one commit before the step 2 work
(34598941837) was 35:18 / 36m01s, so the runner gained 2.4× against the local 5.7× —
the runner's disk was less fsync-bound than its 46–62 s setups suggested, and what
remains there is CPU. That makes `-n 2` on the runner worth one nightly measurement
(step 3), not a gate change.

## 2026-09-11 — #75: workbench SQLite repository skeleton

`engine/tc_ai_bridge/workbench_repository.py` (`WorkbenchRepository`, schema v1) and
`engine/tc_ai_bridge/workspace_repository.py` (`WorkspaceRepository`, a bare `devices`
table so far). `TranslationCoreProject.__init__` now opens `bridge-workbench.sqlite3`
beside the journal (`tc_project.py`); creating an empty database is the only
observable change — no existing store moved, the v14 database untouched, per the
issue's own "Does not" list.

Schema v1 creates all nineteen tables from `TEAM_ARCHITECTURE.md` §§3.1/3.2 plus
`change_log`, using the connection discipline and migration-ladder shape copied from
`FoundationRepository` (`passage_semantic_repository.py:1070-1150`). One generic
`_write()` does the revision check and the change_log append inside the same
`BEGIN IMMEDIATE`, matching `record_human_review`'s pattern
(`passage_semantic_repository.py:3712-3736`); `expected_revision=None` is
last-writer-wins. `change_log.seq` is `AUTOINCREMENT`, and `change_log_entries()`
orders by it rather than `created_at` — deliberately, since #84 (fixed the same day,
above) is exactly this failure mode: uuid4-derived ids plus ~15 ms `datetime.now()`
granularity on Python 3.12/Windows make `created_at` ties non-deterministic, and
that entry names `change_log` by name as needing the same monotonic-column fix.
`BEFORE DELETE`/`BEFORE UPDATE` triggers on `change_log` reject everything except
`synced_at`, using `IS NOT` rather than `<>` so a NULL `book_id` can't make the
trigger's `WHEN` clause silently pass.

**Verified by running.** `pytest tests/persistence -q`: 114 passed — the package's
existing 87 plus this step's new `test_workbench_repository.py` (27), covering schema
creation, both triggers, a revision conflict, one-change_log-row-per-write across
every one of the 19 mutable tables, and a static grep guard that nothing in
`tc_ai_bridge/*.py` updates `change_log` except `synced_at`.

**Surprise, caught by the full suite, not by reading.** First full run
(`pytest -n auto`): 1101 passed / **3 failed** / 1 skipped, 10:45. One failure was
real:
`test_alignment_statistics.py::test_build_corpus_stats_performance_over_a_realistically_sized_completed_corpus`
went from comfortably under its 5.0 s ceiling to 5.49 s. `WorkbenchRepository` copied
`FoundationRepository`'s connection discipline exactly, including `synchronous=FULL`
+ `journal_mode=WAL`, but the suite's fsync opt-out above (#82) only ever patched
`FoundationRepository._connect`. Every `TranslationCoreProject.__init__` now also
pays one real fsync'd schema migration — precisely the cost #82 removed for the v14
database, reintroduced here for the new one. Fixed by extending
`tests/conftest.py`'s `_sqlite_without_fsync` fixture to patch both classes. Re-run:
1103 passed / **1 failed** / 1 skipped, **7:03** — faster than the first run despite
the new component, because every project-opening test stopped paying WAL/fsync
twice over. The remaining failure (`test_ai_explain.py`, "AI review job did not
finish") is a different test than the corpus-stats one, reproduces the same failure
shape a different test hit on the first run
(`test_ai_review_stale_after_apply.py`), and matches the step-2 entry above's own
"1077 passed, 1 failed" under `-n auto` — a pre-existing fixed-10s async-job-poll
flake under 12-way contention, not caused by this change; both failing tests pass
individually in isolation. Filed as #85 rather than fixed here.

Not measured: CI, since `-n auto` is not the gate there. Not done: any #76/#77 store
migration, or wiring `WorkspaceRepository`'s path into `bridge_service.py` — both out
of scope for this issue.

## Verse right-click menu (#69), clickable alignment glyph (#70), and the V1.1
acceptance 01 build (2026-09-12)

Both issues were filed from the same 2026-09-11 stakeholder wireframe
(`ContextMenu`/`Main`/`GlyphStates` artboards) and landed in this order.

**#70 — the alignment glyph became a real control.** `VerseList.svelte`'s
per-row indicator was a plain `<span>` cycling `● ◐ ! ○` by completion state,
with no click handler — the only way to open Align Words was
`ReviewPanel.svelte`'s separate "⇄ Align words" button, scoped to whichever
verse the side panel already had selected. Per the maintainer's own comment
on the issue ("Use this icon ⇄"), the glyph is now a single `⇄` character
for every state, colored the same way the four states already were
(`--success`/`--warning`/`--danger`/`--text-3`), wired as a real `<button>`.
Opening the modal needed the same open/verse-key state in two components
without either reaching into the other's internals, so it moved into a new
`src/lib/alignmentUi.ts` (`alignmentOpen`, `alignmentKey`, `openAlignment()`)
— same shape as the pre-existing `verseEditor.ts` split for the inline edit
textarea. The row's guard order matters: `openAlignmentFromList()` calls
`openAlignment()` first and only then selects the verse, not the other way
around, because a disabled `<button>` can still receive a synthetic click in
tests (and, it turns out, in some real input-dispatch paths) — select-first
would have changed `$selectedVerse` even when the guard correctly refused to
open the modal.

**#69 — a verse with no findings finally has something to right-click.**
`FindingContextMenu.svelte` (the #38 component, reused rather than
duplicated per this issue's own text) gained exactly one level of flyout
submenu: an action can carry a `submenu` array, in which case clicking it
opens a second `.finding-menu` positioned off the first (flips to the left
of the parent when it wouldn't fit on the right) instead of dispatching
`action`; a leaf inside it dispatches `action` exactly like a top-level item,
so no caller needs to know which level an id came from. Keyboard: ArrowRight
opens a submenu-capable focused item and moves focus in, ArrowLeft closes it
and returns focus to the parent, Escape closes only the innermost open
level. `VerseList.svelte` adds a second menu instance (`verseMenu`, separate
from the existing finding-only `contextMenu` — a right-click on a `<mark>`
still `stopPropagation`s before it reaches the row) offering **AI review ▸
Verse/Chapter/Book** and **Edit verse**. Shift+F10 on a verse with no
findings used to simply return; it now opens this menu at the row instead
(`onVerseKeydown`'s `findingIds.length === 0` branch), and the row's
`aria-haspopup`/`aria-keyshortcuts` are unconditional now for the same
reason. "Edit verse" calls `startVerseEdit()` directly — already shared via
`verseEditor.ts`. AI review needed the same cross-component problem #70 just
solved, but `ReviewPanel.svelte`'s `startAIReview()` carries real job/polling
state (`aiJob`, `aiPollTimer`, `processedAIResults`, …) that has no reason to
move, so the new `src/lib/aiReviewUi.ts` is a request, not a state move:
`requestAIReview(chapter, verse, scope)` sets `aiReviewRequest`, which
`ReviewPanel` consumes with `$: if ($aiReviewRequest) { ...; void
startAIReview(...) }`, and mirrors its own `aiJobBusy` out through
`aiJobActive` so the menu can disable its scopes without reaching into
`ReviewPanel`'s internals either. Every menu item disables under exactly the
same conditions as the review panel's own buttons (`checkingProgress`,
`editSaving`, `recheckingKey`, `aiJobActive`, plus "already editing this
verse" for Edit verse) — deliberately, so the menu and the panel can never
disagree about when an action is available.

**Caught while building the visual reference, not while implementing.**
Producing a code-accurate recreation of the open submenu for the issue's
closing comment (no GUI automation is available to actually screenshot the
desktop app in this environment) showed the "AI review" parent item going
visually unremarkable the instant its submenu opened — focus moves into the
submenu immediately, so nothing was left to indicate "this is the expanded
one." Fixed in the same commit: `button[aria-expanded="true"]` in
`FindingContextMenu.svelte` keeps the accent background.

**Frontend gates:** `svelte-check` 0 errors/0 warnings; Vitest 335 passed /
24 files (16 new: 7 in `VerseList.test.ts` for the alignment glyph, 8 more
in `VerseList.test.ts` plus 5 in `FindingContextMenu.test.ts` for the verse
menu and its submenu mechanics — open/close, disabled states, keyboard
navigation, and the finding-menu-still-wins-on-a-mark regression check);
production build passed. Two commits: `9baac8c` (#70), `d354b23` (#69), both
pushed directly to `main` per the project's own no-branch-protection
workflow.

**V1.1 acceptance 01 — installed build.** Rebuilt both sidecars
(`.\scripts\build-sidecars.ps1`, real Wildebeest 0.9.2) and ran
`npm run tauri build` from `d354b23` — a first release compile (no prior
`target/release`), 6m40s. App version stayed `0.9.6`, unchanged, same
convention as the prior `docs/archive/V1_1_UNICODE_ACCEPTANCE.md` build; there was
no pre-existing release artifact in this local `target/` to protect this
time, so the NSIS output was simply copied to a second, distinctly-named
file rather than needing the backup/restore dance that build required.
Installer: `Bridge_V1.1-acceptance-01_x64-setup.exe`, 57,662,277 bytes,
SHA-256 `6808c0f6a40bffc3ee3362364fa274b7c5b111695a11222f76c5a6531ee1d3eb`.
Full regression gate at this checkpoint: engine + Greek Room `pytest`
(serial — `pytest-xdist` is not installed in this local `.venv`; CI itself
runs serial too) **1112 passed, 0 failed, 0 skipped**, 19m10s; `cargo test`
**12 passed**. `smoke_sidecars.py` against the freshly frozen
`bridge-engine.exe` reproduced the same known, pre-existing
`possibleDuplicate`-instead-of-`exactDuplicate` `project.inspectImport`
mismatch the V1.1 Unicode acceptance build already disclosed — confirmed via
`git show --stat` that neither `9baac8c` nor `d354b23` touches
`project_registry.py`/`project_import.py`, so this is not a new regression.
Full checkpoint metadata is in `docs/archive/V1_1_ACCEPTANCE_01.md`.

**Installed acceptance, same day.** The reviewer ran the checklist above
against this installer and recorded PASS for both #69 and #70, plus six
already-shipped V11 fixes verified in the same pass for the first time
against a real installed build: V11-011 (in-app version display), V11-002/
V11-005 (reviewer identity seeded from the OS account), V11-010 (verse
edits — both direct and correction-application — journal the real
reviewer), V11-001 (editor refresh after a correction applies, fixed
2026-09-11 above, now installed-confirmed), and V11-002/V11-006 (history
actor attribution, and the `providerMetadata` truthiness defect that
produced literal `undefined · undefined` rows). Full detail is in
`docs/archive/V1_1_ACCEPTANCE_01.md`. Still outstanding: the
`docs/archive/V1_1_UNICODE_ACCEPTANCE.md` Cases A–D walkthrough, blocked behind #54
(Stage 6B does not consult completed Word Alignment for cross-language
location) — not attempted in this pass, and #57/#58/#61/#62/#63 remain open
from the same round.

## V11-000a: Stage 6B consults completed Word Alignment as location evidence
(2026-09-12)

Implements `docs/archive/V11-000_STAGE6B_ALIGNMENT_SPIKE.md`'s Part B against
same-verse source→target links only (cross-verse — the harder half, needing
the embedding-provider direction — stays on #54). No weight or threshold was
retuned: the pre-existing `HUMAN_PRECEDENT` component already proved 0.65 is
enough to dominate `located_minimum`/`ambiguity_margin`, and the new
component reuses that exact number.

**New module, `tc_ai_bridge/word_alignment_evidence.py`.** Two resolvers plus
a projection:

- `resolve_source_token_id` matches a tC `topWord` onto the pinned UHB/UGNT
  pack's own token identity — exact NFC word+occurrence required,
  lemma/Strong's/morph only reinforce a candidate that already passes that
  filter (`normalize_strong`, promoted from `lexicon_resources._normalize_strong`,
  handles the five-digit-UGNT-variant-vs-classic-dictionary difference).
  Hashes the *pack's own* raw token fields, never `topWord`'s, so an
  NFD-normalized tC entry can't mint a different id than Stage 5 already
  stored — `source_token_identity` was pulled out of
  `SourceSemanticInventory._ensure_token` as a pure function so both call
  sites share one formula.
- `resolve_target_token_id` matches a `bottomWord` onto a fresh
  `bridge-unicode-word-v1` retokenization of the *current* verse text.
  tC's own tokenization and Bridge's genuinely disagree (whitespace +
  edge-trimmed punctuation vs. Unicode-word regex with punctuation as its
  own token) — confirmed empirically, not assumed, against the real
  tokenizers before writing a single assertion: "3:16" splits three ways
  under Bridge but stays one token under tC; quoted words attach the quote
  mark differently; a repeated word with interleaving punctuation gets a
  different `occurrences` total under each. Every one of those is a real
  test (`tests/semantic/test_word_alignment_evidence.py`), matched against
  the tokenizers directly rather than guessed at from the disagreement
  description alone — which is also why the hyphenated/apostrophised-word
  cases in the prompt turned out to be *agreement* tests, not disagreement
  ones, once actually run. Anything but exactly one match on
  (normalized form, occurrence, occurrences) — including an occurrences-total
  mismatch alone — returns unresolved; a dropped group costs a NOT_LOCATED
  (today's status quo without this evidence), never a guess.
  `target_token_identity` was pulled out of
  `PassageSemanticRuntime._ensure_target_tokens` the same way, for the same
  reason.
- `alignment_precedents_for_range` walks the target range's displayed
  references, keeps only verses `word_alignment_state(...) == "completed"`,
  excludes any group with a within-verse duplicated top/bottom signature
  (the same `Counter` check `local_checks.alignment_integrity_checks`
  already runs for `ALIGN_DUP_TOP`/`ALIGN_DUP_BOTTOM`, not re-walked from the
  compatibility-scan's quarantine records), and emits
  `{"sourceTokenInstanceIds": [...], "targetTokenInstanceIds": [...]}` in the
  exact shape `human_approved_lexical_precedents()` already returns. A
  verse-bridge alignment key ("3-4") is out of scope for this pass — deciding
  which individual verse each token belongs to is itself an unresolved-or-guess
  problem, so those verses simply contribute nothing rather than being split
  heuristically. Never raises for a verse-scoped data problem (a malformed
  legacy file, a missing revision row): like `HUMAN_PRECEDENT`, this is
  optional evidence, so a problem with it costs that verse's contribution,
  not `SEARCH_INCOMPLETE` for the whole run.

**Identity.** A new `LocationEvidenceKind.WORD_ALIGNMENT` component (weight
0.65, alongside `HUMAN_PRECEDENT`, per the same source→target-token-set
intersection test) — a separate kind rather than reusing `HUMAN_PRECEDENT`,
since the two are different provenances with different staleness behaviour
and a reviewer asking *why* a finding located where it did should be able to
tell them apart. `LOCATION_ENGINE_VERSION` bumped `-v1` → `-v2` (a real
capability change, not tuning); new `ALIGNMENT_EVIDENCE_VERSION =
"tc-word-alignment-v1"` threaded into the Stage 6B run fingerprint (alongside
a book-wide alignment digest — see below), `analysis_jobs.policy_versions()`
(auto-stales every existing analysis job via its existing exact-dict-equality
check, no extra code), and the Stage 9B.4 verifier fingerprint
(`correction_verification.py`). Also added `WORD_ALIGNMENT` to the
`LocationEvidenceKind` enum in `passage_semantic_models.py` — and, caught
only by the full suite, not by reasoning about it in advance, its two other
copies: `schemas/bridge-passage-semantic-v1.schema.json` and
`src/lib/types/passageSemanticV1.ts` are asserted byte-for-byte against the
Python enum by
`test_python_and_typescript_controlled_enums_match_canonical_schema`, and
`src-tauri/src/passage_semantic_wire.rs`'s own `wire_enum!` copy has no such
test but would have silently failed to deserialize a real
`"WORD_ALIGNMENT"` payload from the sidecar if left out. All three are
updated now.

**Invalidation — no schema bump.** `RECORD_DEPENDENCY_ANCHOR_TYPES` gained a
third anchor, `WORD_ALIGNMENT` (book-scoped, not per-verse: one
`alignment_dependency_id(project_id, book)` per book, matching the book-wide
digest the evidence above and the pre-existing compatibility scan both key
off — over-invalidating a run whose range didn't touch the changed verse is
safe, under-invalidating is not). Every `LOCATION_RUN` registers a dependency
edge on it unconditionally, even with zero alignment evidence found that
run, since a *future* completed alignment could still change the outcome.
`apply_alignment_invalidation` walks it forward through the existing generic
`_stale_generic_dependencies` BFS — the exact same propagation
`apply_target_invalidation` already uses for Scripture edits, so `MEANING_RUN`
and `QA_RUN` records downstream of a staled `LOCATION_RUN` go stale too, for
free.

Rather than a new `pending_invalidations`-shaped prepared-intent/CAS table
(which is specifically shaped around a two-phase edit spanning a
request/response boundary — an alignment mutation is a single synchronous
server-side operation, already durable through `tc_project`'s own journal by
the time invalidation needs to run), `PassageSemanticRuntime
.synchronize_alignment_state()` reuses the existing `migration_run` tracking
the compatibility scan already had: content-addressed against
`alignment_state_digest` (book-wide alignment content *and*
`tools/wordAlignment/completed|invalid` markers combined — a completion-only
change, with no content byte different, still has to stale, since
`complete_alignment()` can flip that with no `save_verse_alignment` call at
all), tracked as its own `migration_run` row under a distinct source-path
suffix so it never collides with the legacy compatibility scan's own
content-only tracking (unchanged, so calling both in one session never
double-quarantines the same legacy issue). Crash-safe by construction: a
crash between staling and its own save just leaves that digest reading as
unprocessed next time, which redoes it safely (staling something already
STALE is a no-op in effect). Called both at `PassageSemanticRuntime`
construction (an external edit made outside a session) and immediately from
`bridge_service._finish_alignment_mutation` (the shared tail for
realign/unalign/save/undo/AI-align) — in the same session, not only after a
restart.

**A real bug the fixture-driven tests caught.** The first version of this
fingerprinted Stage 6B runs against content-only
(`alignment_directory_digest`) while staling against content+completion
(`alignment_state_digest`) — a completion-only change (same alignment
content, freshly marked complete) correctly staled the old run but produced
an *identical* fingerprint for the fresh one, colliding on the
`(project, book, range, fingerprint)` unique constraint on re-insert
(`sqlite3.IntegrityError`). Fixed by fingerprinting on
`alignment_state_digest` too, so anything that can change what the evidence
finds also changes what a run is keyed by. Caught by
`test_completion_state_alone_stales_even_with_identical_alignment_content`,
written because the task's own definition of done named this exact scenario
("completing an alignment... without a content byte changing") — not found
by reading the code, found by running it.

**Verified.** New tests: 20 in `tests/semantic/test_word_alignment_evidence.py`
(both resolvers against real bundled PHP UGNT tokens and the real
tokenizers; precedent projection against a real completed alignment; a
same-verse alignment reaching `LOCATED` with Stage 7 running cleanly against
it) and 4 in `tests/semantic/test_word_alignment_invalidation.py`
(same-session staling, completion-only staling, the crash-safe no-op
re-check, the unconditional dependency edge). Full engine + Greek Room
suite: **1132 passed, 0 failed** (up from 1112 before this issue). `cargo
check` + `cargo test`: 12/12. `svelte-check`: 0/0. Vitest: 335/335.
Production build: passed. Schema stayed **v14** — no migration, per the
"prefer a design that does not need a schema bump" instruction. The Stage 6B
golden (`stage6b-location-golden-v1.json`) was **not** re-baselined: its own
test still passes unchanged, because none of its fixture verses have
completed alignment data, so this evidence source contributes exactly 0
there — confirmed by running it, not assumed.

Not done, on purpose: cross-verse alignment (stays on #54), a `#54` split
into a separate implementable issue for this same-verse slice (the spike
doc's own recommendation — filing that is a maintainer call, not made
here), and any embedding-provider work.

## V11-000a review fixes: exact-NFC matching, atomic WORD_ALIGNMENT edge,
verse-bridge guard, alignment-memo refresh (2026-09-12)

Review of `e3ff0de` (`docs/archive/V11-000a_REVIEW_FIX_PROMPT.md` Part A) found the
design sound but flagged three real defects (F1-F3), fixed in a first pass.
A second review pass of that fix, before it was ever pushed, found one more
(F6) — this entry covers all four. No weight, threshold,
`tokenize_target_text`, or the pack changed; the golden stayed
byte-identical (confirmed unchanged, not assumed).

**F1 — casefolding tied together tokens the counting told apart.** Both
resolvers' word-identity comparison went through `_norm()`, which casefolds.
But `tokenize_target_text` and tC's own aligner both count
`occurrence`/`occurrences` over the exact NFC string, case included — so
"Grace" and "grace" each get `occurrence=1` independently, and a casefolded
comparison in the resolver merged them back into a spurious `len()==2`
ambiguity, silently dropping the whole alignment group. Fixed by comparing
via bare `unicodedata.normalize("NFC", ...)` for word/occurrence identity in
both `resolve_source_token_id` and `resolve_target_token_id`, with no
casefold fallback — a case mismatch is `ALIGN_TARGET_MISMATCH`
(`local_checks.py:36-40`), not something to resolve through. `_norm()`
(casefold) is now restricted to the lemma reinforcement score only, which
sits outside the (word, occurrence) join and stays safe to casefold.
Verified against constructed English fixtures (a same-verse cased pair, a
sentence-initial-capital case, a capitalized-article case), a monkeypatched
source-side Greek pair (Χάρις/χάρις), and real UGNT data — 1CO 2:11's
πνεῦμα/Πνεῦμα pair (strong G4151), found by scanning every NT book's pack for
a (lemma, strong) key with two case-differing word forms rather than
constructed. Each new test asserts a specific resolved id and that the two
cased forms resolve to *different* ids, not just "not None." All prior
controls (the Tamil fixtures, `test_target_resolution_matches_a_plain_word`,
all six tokenization-disagreement tests) still pass unmodified.

**F2 — the WORD_ALIGNMENT dependency edge was one commit, not zero.** It was
previously registered via a separate post-commit `add_record_dependency()`
call, on its own connection, after `save_semantic_location_run`'s own
transaction had already committed — a crash between the two commits could
leave an `ACTIVE` `LOCATION_RUN` permanently immune to invalidation. Fixed
by adding a new `alignment_dependency_id` parameter to
`save_semantic_location_run` and writing that edge in the *same*
`executemany` insert as the run's other dependency edges, inside the same
transaction; the call site in `semantic_location.py` now passes it in rather
than making a trailing call. Verified with a real atomicity test: a
`sqlite3.connect` patch (the C-typed `Connection` itself can't have a method
monkeypatched, so the patch wraps the real connection in a proxy that fails
only the `record_dependencies` `executemany`) forces the edge write to fail,
and the test then confirms — using the real connection again — that no
`semantic_location_runs` row exists for that run id. Fixing this correctly
surfaced a genuine, pre-existing interaction, not a new bug: applying a
correction invalidates word alignment as a side effect
(`tc_project.py:2090-2091`), and now that the edge is reliably registered, a
freshly reopened runtime correctly stales a hand-published test fixture's
location run unless the test settles alignment state first — one
`test_correction_stage9b4.py` fixture was updated to call
`runtime.synchronize_alignment_state()` at the right point to reflect that
realistic sequencing.

**F3 — the verse-bridge exclusion was promised, not enforced.** The module
docstring already said a bridged tC alignment group ("PHP 1:2-3") is out of
scope, but nothing actually skipped one before attempting to load and
resolve it. Confirmed empirically (via `rebuild_current_passage`, not
assumed) that a bridge's displayed reference reaches
`alignment_precedents_for_range` as e.g. `"PHP 1:2-3"`, then added a
`_VERSE_BRIDGE = re.compile(r"\d+-\d+")` guard — the same pattern
`original_language_resources.py` already uses to recognize a bridge — that
skips the reference before any load or resolve attempt. Verified with a
fixture project holding a completed alignment stored under a bridge key:
zero precedents, no exception, and (patched and counted)
`resolve_source_token_id` is never called.

**Version bump.** `ALIGNMENT_EVIDENCE_VERSION` moved `"tc-word-alignment-v1"`
→ `"tc-word-alignment-v2"`: v1 is now public on `main`, and F1 changes what
evidence is found, so without a bump a project analyzed under v1 would have
its evidence-dropped runs served as current cache hits post-fix. No other
version moved — `LOCATION_ENGINE_VERSION` stays `-v2`, schema stays v14.
Pinned by three tests: the literal itself, its presence in
`AnalysisJobManager.policy_versions()`'s output, and — the way
`test_analysis_jobs_stage9a4.py:332-335` pins the Unicode comparison
version — that it's a real input to the Stage 9B.4 verifier fingerprint
(reconstructed manually with both v1 and v2 strings; the real fingerprint
matches only the v2 reconstruction).

**F6 — the memo could still lag disk on two more paths, and the 9B.4 test's
own fix hid it.** Found in a second review pass of this same fix, before it
was ever pushed. `apply_scripture_edit` (both the manual-edit and the
correction-application route) and `complete_alignment` (the RPC) each
change what Stage 6B's `WORD_ALIGNMENT` evidence should find — a fresh
`invalid` marker, a fresh `completed` marker — but neither called
`synchronize_alignment_state()` to refresh the memo that decides whether a
downstream `LOCATION_RUN` is stale. A location run published right after
either call already reflects the post-change alignment state and carries a
correct `WORD_ALIGNMENT` edge (per F2), but the next project reopen
compares current disk state against a memo that never advanced, finds a
mismatch, and over-invalidates that (actually-current) run along with
everything downstream of it: apply a correction, re-analyze, verify, close,
reopen — the whole book's analysis goes stale again for no real reason.
F2's own fix in this same commit is what made this visible: the
`test_correction_stage9b4.py` fixture had papered over it with a manual
`runtime.synchronize_alignment_state()` call and a comment claiming the
reopen "correctly" staled the run — it does not, and that call is reverted
here. Fixed at both call sites instead of in the test: `apply_scripture_edit`
now calls `synchronize_alignment_state()` right after
`self.journal.commit(...)` — the one point both the editor-edit and
correction-application routes reach unconditionally (`tc_project.py`) — and
`complete_alignment` calls it right after `mark_word_alignment_completed`
(`bridge_service.py`), since unlike `realign`/`unalign`/`save`/`undo` this
RPC does not go through the shared `_finish_alignment_mutation` tail that
already did this. Verified by two new regression tests in
`test_word_alignment_invalidation.py`, one per path: publish a real `ACTIVE`
location run, mutate alignment state through the real call
(`apply_scripture_edit` / `complete_alignment`), publish another real run,
construct a **fresh** `PassageSemanticRuntime` on the same on-disk project
exactly as `project.open` does, and assert the just-published run is still
`ACTIVE` and the fresh runtime's own `synchronize_alignment_state()` returns
`{"changed": False, "staled": 0}`. Both were confirmed to fail against the
unfixed code first — `git stash` of just the two source files reproduced
`STALE` where `ACTIVE` was expected on both — then pass with the fix
restored.

**Verified.** `tests/semantic/test_word_alignment_evidence.py`: 25 tests (up
from 20). `tests/semantic/test_word_alignment_invalidation.py`: 7 tests (up
from 4: the F2 atomicity test plus the two F6 regressions). All prior tests
in both files, and in `test_correction_stage9b4.py`, pass unmodified except
the one fixture edit named under F2/F6 above. Full engine + Greek Room
suite: **1144 passed, 0 failed** (up from 1132). `cargo check` clean,
`cargo test` 12/12. `svelte-check` 0/0. Vitest 335/335. Production build
passed. Schema stayed v14; the Stage 6B golden stayed byte-identical. O1/O2
(the two optional fixes the review also named) were not attempted —
deferred to their own commits, only after these land.

## V11-003 / #57: a human-authored proposal needs no Edit->Save round-trip
(2026-09-12)

Implements `docs/archive/V11-003_ISSUE57_PROMPT.md`'s Part B. `mayApply` in
`CorrectionReviewPanel.svelte` was already correctly gated on
`proposalReviewed` (`reviewStatus` is `HUMAN_MODIFIED` or `HUMAN_APPROVED`);
the bug was that a brand-new human-authored proposal landed `UNREVIEWED`
(`correction_wording.py`'s `_build_proposal`), so "Review application"
never appeared until a pointless Edit→Save round-trip — pointless because
`update_correction_proposal_wording` already accepts that call with no
check on who performs it, i.e. today's workaround is already self-review by
the proposal's own author. The frontend gate itself is untouched; both
fixes are backend, per Part A's decision.

**W1 — new default.** `_build_proposal`'s `review_status` now reads
`AI_PROPOSED` (unchanged) if an AI result exists, else `HUMAN_APPROVED` if
`human_proposed_text.strip()` is non-empty, else `UNREVIEWED`. No new
history event: the `CREATED` event already snapshots the whole payload,
`reviewStatus` included. Renamed the one existing test this flips
(`test_human_authored_proposal_works_offline_and_remains_unapproved` →
`..._and_defaults_to_approved`) and added a companion proving the
`else`-`else` path: a proposal with *only* whitespace wording (the sole way
to reach it — a wholly empty `proposed_text` is rejected earlier) stays
`UNREVIEWED`, because nobody has written anything to review yet. The AI
path's own existing test already asserted `AI_PROPOSED` unmodified.

**W2 — lazy reseed.** An existing `UNREVIEWED` + `HUMAN_AUTHORED` proposal
is reseeded to `HUMAN_APPROVED` the first time it is read through either
`FoundationRepository.correction_proposal` or
`correction_proposals_for_finding` — both call one new choke point,
`_reseed_review_status_if_needed`, so a proposal can never come back
reseeded through one path and not the other. Eligibility (all must hold):
`reviewStatus == UNREVIEWED`, `creationMode == HUMAN_AUTHORED`,
`lifecycleStatus == ACTIVE`, no AI provenance (`providerMetadata` and
`originalSuggestedText` both empty/absent), **and** `proposedText.strip()`
non-empty. That last condition isn't in the spec's own eligibility list —
found by running the tests, not by reading: a whitespace-only proposal
freshly created under W1's own new default is `UNREVIEWED` +
`HUMAN_AUTHORED` + `ACTIVE` + no AI provenance, identical in shape to a
genuine pre-fix leftover, and the very read that returns it from
`create_proposal` would otherwise reseed it to `HUMAN_APPROVED` immediately
— wrong, since W1 itself says that proposal should stay `UNREVIEWED`
forever, not just at creation. Checking the actual wording distinguishes
"nothing to review" from "real wording written before this fix landed."

A cheap in-memory pre-filter (`_looks_reseedable`) runs on every read
before any write connection opens; only a plausible row triggers a fresh
`BEGIN IMMEDIATE` re-check against the current disk state (never trusting
the outer, possibly-stale read) before writing. **`revision` is never
bumped** — `review_status` and payload `reviewStatus` are updated in place,
which Part A calls out as a deliberate, documented exception to CAS
discipline: the review panel caches `revision` and sends it back on
edit/reject/apply, so a reseed that bumped it would turn a silent bug into
a confusing `REVISION_CONFLICT` on the user's very next action. Attributed
to `ActorType.MIGRATION` (never `HUMAN` — automatic events must never claim
the named reviewer did this), audited with one new event type,
`REVIEW_STATUS_BACKFILLED`, `base_revision` equal to the unchanged
revision. Guarded on `self.read_only`: a reseed attempted during
`recovery_check`'s read-only/recovery mode is skipped rather than raised,
so it can never break project open.

**Verified before building, not assumed:** grepped
`correction_verification.py`, `analysis_jobs.py`, and `semantic_location.py`
for `reviewStatus`/`review_status` — it feeds no engine fingerprint or
policy identity, so this change needed no `LOCATION_ENGINE_VERSION` or
policy-version bump.

**A real, unanticipated schema conflict, found and resolved before writing
the reseed.** The implementation prompt asked to verify the new event type
wasn't pinned in the JSON schema or the Rust wire enum (confirmed clean by
grep, exactly as it predicted) — but missed a third pinning point:
`correction_proposal_events.event_type` carries its own SQLite `CHECK`
constraint, unchanged since v12, enumerating exactly the prior six
literals. Confirmed empirically (a minimal reproduction) that SQLite
enforces it: `REVIEW_STATUS_BACKFILLED` would raise a real
`sqlite3.IntegrityError` on the very first reseed. Per this repo's
schema-migration discipline, widening a `CHECK` constraint is a schema
change like any other, conflicting with the task's own "no version bump"
scope — flagged and resolved with the maintainer before writing any reseed
code, who chose the schema bump over silently reusing an existing event
type (which would have mislabeled the audit trail) or dropping the event
entirely (which would have abandoned the spec's own auditability
requirement).

**Schema v15.** `correction_proposal_events` rebuilt (rename, recreate with
the widened `CHECK`, copy every row across, drop the old table) — the same
shape v13 used for `correction_application_intents`. No column, index, or
row semantics changed; purely one more admitted `event_type` literal. New
`test_v14_to_v15_migration_is_additive_and_keeps_v14_data_readable` (build a
v14 database directly from the migration blocks, insert a pre-fix
`UNREVIEWED`/`HUMAN_AUTHORED` proposal and a legacy event under the old
constraint, migrate forward, confirm both survive unmodified, then confirm
the *running* repository's reseed on read both succeeds and actually uses
the widened constraint — not just that the migration script looks right).
Every other "build an old database, migrate forward, assert
`schema_version() == DATABASE_SCHEMA_VERSION`" test across the suite needed
its hardcoded version literal bumped `14` → `15` alongside it — a
mechanical, expected part of any schema bump in this codebase (the same
tests already carried a `13` → `14` bump when v14 landed), not specific to
this change.

**New TypeScript member.** `CorrectionEventType`
(`src/lib/types/correctionReview.ts`) gained
`"REVIEW_STATUS_BACKFILLED"` — a one-line addition, not a three-surface
update, since (confirmed by grep) no JSON schema or Rust wire enum mirrors
this specific type.

**Verified.** New tests: `test_human_authored_proposal_with_only_whitespace_wording_stays_unreviewed`
and the renamed default test (`test_correction_stage9b1.py`); a new
dedicated file, `tests/correction/test_review_status_backfill_v11_003.py`
(9 tests: reseed + one event, idempotence on a second read, AI-proposed
left alone, human-rejected left alone, non-ACTIVE-but-UNREVIEWED left
alone, UNREVIEWED-with-providerMetadata left alone, both read paths
identical, the concurrency regression, and the read-only guard); the v15
migration test above; and one Vitest case
(`CorrectionReviewPanel.test.ts`) asserting a `HUMAN_APPROVED` +
`HUMAN_AUTHORED` proposal reaches "Review application" and opens the
application-review dialog with `api.edit` never called. The concurrency
regression captures the proposal's revision from the database column
*before* the reseed-triggering read (not from that read's own return
value), so it fails even against a "fix" that bumps `revision` and
correctly reflects the bump in what it returns — not just a naively broken
one. Full engine + Greek Room suite: **1155 passed, 0 failed** (up from
1144). `cargo check` clean, `cargo test` 12/12. `svelte-check` 0/0. Vitest
336/336 (up from 335). Production build passed. Schema now **v15**; no
engine or policy version changed. `docs/QA_TEST_MATRIX.md` has no existing
row for this manual-write path, so none was updated (not fabricated new).

## 2026-09-14 — #85 wait budgets, the v15 doc drift, and three V11-round UI issues

A bookkeeping-and-easy-wins session on top of the weekend's work (0.9.7,
#75, the #54 tractable half, #57, #69/#70/#86). Nothing here touches the
schema, a golden, a threshold, or the engine's behaviour.

### #85: background-job wait budgets are now environment-scaled

#85 was filed as an artifact of `-n auto` contention: two different tests,
in two different local runs, failing with `_wait_for_ai_job`'s
`AssertionError: AI review job did not finish` under 12 workers. The new
evidence is that the **same 10 s budget expired on serial CI** — run
34806216124, the 0.9.7 release-notes push,
`test_ai_explain.py::test_manual_override_review_still_applies_safe_selections`,
1 failed / 1148 passed in 26:54. `ci.yml` deliberately does not pass
`-n auto` (commented at the pytest step), so worker-count scaling alone —
#85's own suggested fix — would not have prevented that failure.

New `engine/tests/support/waits.py`: `job_timeout(base)` returns
`max(base * worker_count(), FLOOR_SECONDS)` with `FLOOR_SECONDS = 60`,
reading `PYTEST_XDIST_WORKER_COUNT` (absent means 1) and falling back to
serial on a malformed value rather than raising during collection. Applied
to 14 loops across 9 files: the eight named helpers (`_wait_for_ai_job`
twice, `wait_for_triage`, `wait_for_report`, `wait_for_job`,
`wait_for_sweep`, `_wait` twice, `wait_for`) plus four inline
`deadline = time.monotonic() + N` loops of the same shape. Both tests #85
names are covered: the second, `test_ai_review_stale_after_apply.py`,
imports `_wait_for_ai_job` from `test_ai_explain.py` rather than defining
its own.

The design argument for a floor this generous: **every one of these loops
returns as soon as the state is terminal**, so a larger budget is only ever
spent on a run that was going to fail anyway. The cost is a slower report
of a genuine hang (60 s rather than 10 s per hung test, against a ~15 min
serial CI run); the benefit is a gate that can be trusted on every push to
main. Not touched: `threading.Event` gates holding a lock open in
concurrency tests, and `test_correction_case_c_production.py`'s existing
300 s budget — the first assert the timeout rather than tolerate it, the
second is already generous.

`engine/tests/support/test_waits.py` covers the floor, the scaling, the
serial default, and the malformed-env fallback. Verified with the 11
affected files under `-n auto`: 169 passed in 66 s.

### CLAUDE.md said v14; the code says v15

9053b5d (#57, 2026-09-12) bumped `DATABASE_SCHEMA_VERSION` to 15 with a
real `_MIGRATION_V15` block and
`test_v14_to_v15_migration_is_additive_and_keeps_v14_data_readable`.
CLAUDE.md was edited in the same window for the *workbench* ladder (#75)
but kept saying v14 for the semantic database in four places — including
the "Stop and ask before writing any code" list, which is the one where a
stale number actually misleads. Corrected, along with the
`_MIGRATION_V1 … _V14` range and the cited migration-test name.

### #84 re-pointed, with the rowid question actually tested

#84 was left open "for the sequence-column decision (v15)". v15 came and
went on an unrelated change without adding one, so that decision now points
at v16. The other half — "a design note for #75's `change_log`" — is
already absorbed: `change_log` ships with
`seq INTEGER PRIMARY KEY AUTOINCREMENT` and `ix_change_log_project_seq`, so
workbench-side appends never depend on a timestamp tie-break.

Worth recording because it was checked rather than assumed:
`_MIGRATION_V15` **rebuilds** `correction_proposal_events` (rename,
recreate, `INSERT … SELECT`, drop), which reassigns every rowid — the exact
key 7dbcb35's `ORDER BY created_at, rowid` depends on. Tested against
SQLite 3.49.1 with ten events tied on `created_at` and ids deliberately
unsorted: order preserved, both with the index dropped (what v15 does) and
with a covering index left in place (the planner still chose `SCAN`). So
**v15 did not permute anyone's proposal history** — but that is incidental,
not guaranteed: SQLite defines row order without `ORDER BY` as undefined.
The existing migration test asserts old rows are *readable*, not that they
come back *in order*, so nothing would catch a future rebuild that did
permute one. Both follow-ups recorded on #84.

### #61: filter selections survive a reload

Filter state splits in two and only one half should persist. `order`,
`dispositions`, `kinds`, `coverageDimensions` and `lifecycleStatuses` are
*preferences* — a reviewer's working style — and now round-trip through
`localStorage` (the app's first use of it) including across
`resetReviewState()`, which is what both project-reload call sites in
`App.svelte` go through. `book`, `chapter` and `canonicalReferences` are
*navigation scope*, set from the project being opened; restoring a previous
project's book would silently point the queue at text the reviewer is not
looking at, so these still reset, with a test asserting they are never
written.

Everything read back is validated rather than trusted — the stored value
outlives the build that wrote it, and a disposition or coverage dimension
this build no longer knows would otherwise reach the engine as a filter
nothing can match, hiding the whole queue. Every storage access is wrapped:
losing a preference must never break review.

`coverageDimensions` was added mid-session by f9140c6 (semantic dimension
chips) and folded in as a second commit — without it the new chips would
have been the one row still resetting.

### #73: edit pencil above the alignment arrow, and a defect it exposed

Pencil and arrow now share a `.row-actions` column in `VerseList.svelte`,
pencil above, reusing the existing `beginEditFromList` action — a new
trigger location, no new edit logic. U+270E rather than an icon font
(gotcha 9), same disabled guard as the arrow, absent entirely while that
verse is being edited.

**Pre-existing defect found while adding it:** `beginEditFromList` selected
the verse *before* asking `startVerseEdit` whether it could open, so when
the edit was refused — background check, in-flight save, or recheck — the
reader's selection moved for an edit that never appeared.
`openAlignmentFromList` was deliberately written the other way round in
#70, with a comment explaining exactly this. The double-click route was the
one really exposed, having no `disabled` attribute to hide behind. Fixed by
reordering, now consistent with #70; both paths pinned by tests confirmed
to fail without the reorder (2 failed / 41 passed on the reverted code).

### #53: previous/next chapter

Two arrow buttons flanking the existing chapter dropdown in
`TopBar.svelte`, editor screen only. Both route through the same
`onChapterChange` the dropdown uses, so project, book, view mode and
unsaved-edit handling follow by construction rather than by a second
implementation that could drift.

"Previous" and "next" are defined by **position in the book's chapter
list** — the same list the dropdown renders — not by numeric value: chapter
ids arrive from the engine as strings with no guarantee of a clean 1..n
run, and stepping must agree with what the reader can see. Disabled on the
first and last chapter, on a single-chapter book, and when the current
chapter is not in the list at all (briefly true mid-book-switch, where
stepping would be a guess). Real `<button>`s in a labelled `role="group"`.
First test file for `TopBar`.

### Verified

`npm run check` 0 errors / 0 warnings; `npm run test` **364 passed** across
26 files (up from 336, 28 new); `npm run build` clean. Engine: the 11 files
affected by the wait-budget change under `-n auto`, 169 passed — the full
Python suite was not re-run locally, since nothing outside `tests/` changed
on the engine side; CI on main is the gate for that.

**Not verified:** none of the three UI changes has been seen in the running
desktop app. jsdom does not lay out or paint, so the pencil's position
above the arrow and the chapter arrows' fit beside the dropdown at 1366x768
still need a human look — as does whether the persisted filters read
sensibly after a real project reload.

## 2026-09-14 — #76 step 1: the workbench migration runner, and a local user id

#76 as filed is one issue covering ten store groups in `tc_project.py`, three
collaborator modules, a lazy-migration framework and the `audit/` →
`change_log` switch. On the maintainer's call it is being landed as a
sequence, and this is the first piece: **the mechanism only**. `REGISTRY` is
empty, so on a real project this code does nothing at all. There is a test
asserting that, so a later step cannot register a store by accident.

Landing the runner alone is deliberate. The behaviour worth reviewing is what
happens when a migration is interrupted or fails — on a translation team's
only copy of months of work — and that is much harder to see once ten real
store moves are layered on top of it.

### The identity problem, which had to be solved first

`_write()` requires an `actor_id` on every row, and `change_log` carries
`BEFORE UPDATE`/`BEFORE DELETE` triggers that reject every column but
`synced_at`. So whatever `actor_id` the store moves stamp is **permanent and
cannot be backfilled**.

The only identity that existed was `AppSettings.reviewer_name` — a *display
name* the user can change in Settings at any time, and which V11-005 reseeds
from the OS account. Stamping that would mean one person's history splits
irreversibly into two un-linkable sets of immutable rows the first time they
rename themselves.

`TEAM_ARCHITECTURE.md` §5 already specifies the right shape ("every workbench
write carries `actor_id = user_id`; display names resolve at read time") — but
§10 schedules identity (step 3, #78) *after* the store moves (step 2, #76/#77),
which cannot work for the reason above. That is an internal contradiction in
the design doc, not a judgement call, so §5 now records why the `user_id` has
to come first.

`workspace.sqlite3` gains a `users` table and
`WorkspaceRepository.get_or_create_local_user(display_name)`: a stable uuid4
`user_id`, with the display name refreshed from the caller on every call.
Renaming changes how you are shown everywhere, past rows included, without
detaching you from the history you already wrote — which is the entire reason
the two are separate columns. Roles, `authorize()` and choosing between
several users stay #78's.

### The runner

New `engine/tc_ai_bridge/workbench_migration.py`:
`WorkbenchIdentity` (project_id, book_id, actor_id, device_id — a value object
with no defaults, because every field lands verbatim on rows that can never be
edited), `StoreMigration`, an ordered `REGISTRY`, and
`run_pending_migrations()`. Progress lives in `project_state('migration')`.

Three properties, each with a test:

- **Never breaks project open.** A store that raises leaves the migration
  incomplete and the project usable, reading from its files exactly as before.
  The runner stops rather than skipping ahead, since a later store may depend
  on an earlier one and finishing out of order would record a completion that
  is not true. `KeyboardInterrupt` is deliberately *not* caught — a real kill
  should not be swallowed — and the state written up to that point is still
  correct, which is what the resume test exercises.
- **Resumable, not restartable.** Each store records itself the moment it
  finishes; a run interrupted after the third of five resumes at the fourth.
- **Idempotent at the row level.** New `natural_row_id()` in
  `workbench_repository.py` derives the primary key from a store's natural key,
  so a store interrupted *mid-way* — after some rows but before recording
  itself — re-runs safely. Parts are length-prefixed so `("a","bc")` and
  `("ab","c")` cannot collide, and `None` is distinct from the string `"None"`.
  This is also what will let the hub match rows across devices later; a uuid4
  would satisfy neither.

Per-store atomicity is therefore *not* required, which is the property that
makes `_write`'s one-transaction-per-row sufficient. The consequence, recorded
in both the module docstring and §3.5: a store migration must never append to
a list it read from the database, only rewrite it from the file.

Progress is scoped per project **and book** — Bridge imports one project per
book, so PHP finishing says nothing about TIT — and `is_store_migrated()` is
per store, not per project, so a reader for a moved store is not held back by
one that has not moved yet.

### A second design-doc correction

§3.5 says the migration runs "inside `TranslationCoreProject.__init__` after
journal recovery". Both halves cannot hold: journal recovery is not in the
constructor. It is `recover_incomplete_transactions()`, which
`bridge_service.py:596` calls on the already-constructed project. The ordering
it asks for is real — migrating from files the journal is about to roll back
would copy state the project is about to disown — so the seam is a separate
method, `TranslationCoreProject.run_workbench_migration(identity)`, to be
called straight after recovery. §3.5 now says so.

### Deliberately not done

`bridge_service.py` is **not** wired to call the seam yet. Doing so would make
every project open construct a workspace repository and create
`%LOCALAPPDATA%\Bridge\data\workspace.sqlite3` — an observable change, for a
runner that has nothing to run. It lands with the first real store, where the
wiring is worth something. No store is moved, no reader is redirected, no
`audit/` behaviour changes, and no schema moved: the v1 workbench schema
already carries every lifted column §3.1 calls for, verified rather than
assumed, so this needed no workbench bump and did not go near the v15
companion database.

### Verified

`pytest tests/persistence/test_workbench_migration.py`: 18 passed. Full engine
+ Greek Room suite under `-n auto`: **1184 passed, 0 failed**. No frontend or
Rust surface touched.

### Found while reading, not fixed here

`tc_project.py:1787` returns `'audit': str(audit)` — a companion-dir path — in
a public result, and `tests/service/test_check_selection.py:239` reads the file
at that path. #76's "stop writing `audit/` files" therefore breaks a public
contract, which the issue does not mention. Recorded on the issue rather than
decided here.

## 2026-09-14 (afternoon) — running the app: #61/#73/#53 verified, #89 found, #83 closed properly

### #83: the half that `cancel-in-progress` never covered

9bf8857 (this morning) scoped `cancel-in-progress` to `pull_request`, and the
issue was closed on the strength of "the guard has held over 16 commits". That
closure was wrong and today's own pushes disproved it within the hour.

`cancel-in-progress` governs only a run that is already **in progress**. A
**pending** run is evicted whenever a newer run joins the concurrency group,
unconditionally, because the default `queue: single` means "at most one run
may be pending, and a new one replaces it". Checked against the docs rather
than inferred — [Using concurrency](https://docs.github.com/en/actions/using-jobs/using-concurrency),
verbatim: *"At most one job or workflow run can be `pending` in the
concurrency group."*, *"When a new job or workflow run is queued, any
existing `pending` job or workflow run in the same group is canceled and
replaced."*, and *"To **also** cancel any currently running job or workflow
in the same concurrency group, specify `cancel-in-progress: true`."*

What happened, all `push` events on `main`:

| run | commit | started | outcome |
|---|---|---|---|
| 34816856162 | `b6c77f8` | 07:13 | success (17m26s) |
| 34817605819 | `fc57811` | 07:23 | **cancelled** |
| 34818151211 | `057b328` | 07:30 | success |

`fc57811` sat pending behind `b6c77f8`'s 17-minute run; pushing `057b328`
evicted it. And `fc57811` was the merge commit carrying f9140c6
(`bridge_service.py`, `passage_semantic_repository.py`, `qa_review.py`,
`commands.rs`, four frontend files). f9140c6 and fc57811 were pushed together
so — correctly — only the head commit got a run, which makes that cancelled
run **the only one that would ever have covered f9140c6's engine changes**.
The next run skipped the `engine` job entirely, because its own path filter
only sees its own diff.

That combination is what makes this worth fixing rather than living with: the
eviction is silent *and* the safety net one would assume exists (the next run
covers it) does not. The gap was closed by hand — full engine + Greek Room
**1166 passed**, `cargo check` clean, `cargo test` **12 passed**, frontend
**364 passed** — so nothing was actually broken, only unchecked.

**Fix (ae0bd73):** `queue: max`, `cancel-in-progress` removed. Up to 100
pending runs, FIFO, GA since 2026-05-07. The two cannot coexist (workflow
validation error) and the maintainer's call was that cancelling superseded PR
runs is not worth losing a gate over. Confirmed `queue` was real before
touching the only automated gate, and confirmed the file still parsed and that
both keys could not both be set. **Verified live on the next push**: `92d9fd0`
pending behind `ae0bd73` in progress, both green — under the old config the
pending one would have been discarded. `release.yml` untouched: two triggers
fire for one tag and the job is idempotent, so collapsing there is deliberate.

### Running the app, and what that cost

Bridge is Tauri/WebView2 on native Windows, so there is no CDP endpoint and
no `_electron` driver; UI Automation returns zero elements because WebView2
does not expose its tree by default. Driving it meant a small Win32 harness —
`GetWindowRect` + `CopyFromScreen` for capture, `SetCursorPos` + `mouse_event`
for clicks, `SendKeys` for keys.

Two traps worth recording for anyone who does this again:

- **DPI awareness is per-process, and every PowerShell invocation is a new
  process.** `GetWindowRect` returned `1550x926` in one and `1938x1158` in the
  next — exactly the 1.25 factor of a 125% display. Screenshots were being
  captured in one coordinate space and clicks synthesised in another, so early
  clicks silently missed. Fixed by calling
  `SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)` at the top of every
  invocation, before anything queries a rect.
- **`ShowWindow(hwnd, SW_RESTORE)` un-maximizes the window you are about to
  capture.** Use `SW_SHOW`.

If driving the app becomes routine this belongs in a project skill via
`/run-skill-generator` rather than being rediscovered.

### #61, #73, #53 verified

Sidecars rebuilt first (the ones on disk were from 2026-09-08 and predated
f9140c6's `coverageDimensions` protocol change), then `npm run tauri dev`.

- **#53** against every boundary it claims: Genesis Ch 1/50 and Ruth Ch 1/4
  (`‹` disabled), Ruth Ch 2/4 (both enabled, stepped 1→2→3→4 with the status
  line tracking), Ruth Ch 4/4 (`›` disabled, tooltip "Already at the last
  chapter"), Obadiah Ch 1/1 (both disabled).
- **#73** pencil renders above the arrow and opens the inline editor.
- **#61** the way it actually matters: filters set in Tamil Ruth → Projects →
  a *different* project (Hindi Genesis). Preferences followed; `Review scope`
  correctly reset to `GEN 1:1 - GEN 1:2`, so the reviewer is not silently
  pointed at a previous book's text.

### #89: the filter chips that filtered but never looked selected

Clicking any chip in the QA **Issue type** row did nothing visible; Order and
Review state worked. The filter was applied the whole time — click fired,
store updated, queue re-queried, value even round-tripped through
`localStorage`. Only `aria-pressed` never changed.

```svelte
aria-pressed={issueFilterSelected(option)}          <- #89, never updates
aria-pressed={$reviewFilters.order === "CANONICAL"} <- fine
```

`issueFilterSelected` read `$reviewFilters` **inside its own body**. Svelte
resolves a template expression's dependencies syntactically, so it registered
`issueFilterSelected` and `option` and never the store; the expression
rendered once on mount and never again. `.chip[aria-pressed="true"]` is the
only selected styling, so the row looked permanently off — worse than an inert
control, since the queue silently narrows with no way to see or undo it, and
`aria-pressed` is what a screen reader announces.

The confirmation was exact, and came from #61's own reload rather than from
reasoning: after a fresh mount the chips rendered correctly pressed, with
`Negation` the only one off — matching the fact that it was the one chip
clicked twice (on, then off) while probing. Correct on mount, never updated
after.

Fixed (e0330e1) by passing the filters in, so the store is named at the call
site. Three regression tests, each confirmed to fail against the old shape
(3 failed / 11 passed) and pass against the new. The test f9140c6 added
asserts the *query* (`coverageDimensions: ["POLARITY"]`) rather than the chip,
which is why the suite stayed green — correct as far as it went.

**Worth a grep when touching this file:** any `aria-*` or `class:` binding
computed by a helper that reads a store internally has this bug latent.

### A defect #61 introduced, caught by #89's own tests

Because filters now persist, `resetReviewState()` rehydrates from
`localStorage` — and jsdom keeps one storage per test file. So a test that
clicked a filter chip silently armed the next test that called
`resetReviewState()` *for isolation*. That is precisely how the new #89 tests
first failed (`Negation` already `aria-pressed="true"` before the click).
Fixed in `setup.ts` with a global `beforeEach` clear rather than by patching
the one file that noticed — forgetting it produces a confusing failure in a
test that never mentions storage.

### #73 glyph

U+270E is emoji-presentation-capable, so Windows resolved it through Segoe UI
Emoji and drew a filled colour glyph directly above the alignment arrow's thin
monochrome one. U+FE0E (variation selector-15) plus `font-variant-emoji: text`
asks for text presentation; both are inert where unsupported. Not re-checked in
the desktop app — worth a glance next time it is open.

### Verified

`npm run check` 0/0; `npm run test` **367 passed** across 26 files; `npm run
build` clean. Engine untouched this half of the day. CI green on `main` at
`de2b9a3`, `ae0bd73` and `92d9fd0`.

## 2026-09-14 (evening) — the pre-release data reset: #76 becomes a cutover, #84 lands as v16

### The decision

There is no user data to preserve, on any machine. Bridge is pre-release, every
project on disk is a development import, fresh imports are cheap. Confirmed by
the maintainer for his own machine and Benz's.

That single fact removes the largest source of complexity in the #44 line, since
most of it existed to carry existing data forward. What it does **not** touch:
the append-only invariants, token lineage, staleness propagation. Those are
properties of future data, not of migration.

Before acting, the actual contents of disk were checked rather than assumed: 202
projects, of which **twelve hold real review decisions and audit trails** (up to
19 decision files and 85 audit files each, some with semantic databases). That
was reported back so the call was informed, and a verified archive taken first —
96 MB, 9,116 entries, all twelve top-level directories, decision payloads
spot-checked by opening the zip rather than trusting `Compress-Archive`'s exit
code. Nothing was deleted from anyone's disk; the guard in the cutover will make
old projects refuse to open, which reaches the same end without destroying
anything, and deleting is the maintainer's call.

### A: the migration runner deleted (aeccc47)

`workbench_migration.py` and its tests, 541 lines, removed one day after
landing. Worth being clear that this is the right outcome rather than a waste:
the machinery existed to answer "what happens if this dies halfway through a
team's only copy of months of work", and the answer is now "that cannot arise
yet". It is in git history, and building it later against real requirements will
beat keeping speculative code alive — dead code that looks load-bearing is worse
than none.

Kept, because neither was ever about migration: `natural_row_id` (a row id
derived from a natural key is what makes the same record land on the same id on
every machine, which the hub will need) and `WorkbenchIdentity`, rehoused into
`workbench_repository.py` beside the `_write` that consumes it. Their tests moved
into `test_workbench_repository.py` rather than being deleted with the file.

`TEAM_ARCHITECTURE.md` §3.5 rewritten from "Migration and the API seam" to
"Cutover and the API seam", recording why the machinery was built and then
removed rather than quietly dropping the section — a reader who finds the design
doc later should not have to reconstruct that from git.

Suite after A: **1173 passed** = 1184 − 18 deleted migration tests + 7 rescued.

### C: #84 as schema v16 (78c7634)

Deferred on #84 because a sequence column needed a migration and a migration was
a stop-and-ask. With the reset that constraint is gone.

`seq INTEGER` added to `correction_proposal_events`, `correction_verifications`
and `review_records`, backfilled `SET seq = rowid`, one index per ledger, all
three reads moved from `ORDER BY created_at, rowid` to `ORDER BY seq`. `seq` is
assigned `MAX(seq)+1` inside the writing transaction: ordered, unique per table,
independent of both clock resolution and storage internals. The backfill is
correct precisely because the rowid ordering was empirically verified this
morning to have survived v15's table rebuild.

**A base-schema edit was considered and rejected.** A fresh database is built by
running the ladder V1…V16 in order, and v13 and v15 *rebuild* two of these three
tables — a column added to `_MIGRATION_V1` would be silently dropped on the way
up. Editing the base plus every later rebuild block is fragile for no gain, so
v16 is an ordinary forward block. It will never actually run on any database,
since every database will now be created fresh, but the ladder stays honest and
the collapse-to-one-v1 option stays open for later.

Seven `== 15` literals bumped to 16 across five test files.

### The call-site fan-out, which is the part worth remembering

Adding one column broke **five** insert statements. Grep found three.

- Two positional `VALUES(?,?,…)` inserts, fixed in the first pass by naming
  columns.
- A third — the `STALE` event written by dependency invalidation, nested in a
  loop inside a conditional — was missed and failed **loudly**: 93 tests.
- A fourth, `import_review_record`, used `INSERT OR IGNORE`, so a search for
  `INSERT INTO` never saw it. This one did **not** fail loudly. It sits inside an
  `except Exception` that quarantines malformed legacy records, so the broken
  insert was swallowed and surfaced as `KeyError: 'reviewStatus'` on a migration
  report — one failing test, three layers from the cause, with nothing in the
  stack trace pointing at the change. Confirmed it was genuinely new by stashing
  the repository diff and watching the test pass.

Hence a static guard, following the idiom the repository already uses for
`change_log`: strip the `_MIGRATION_V*` scripts (which legitimately rebuild these
tables with their own column lists) and assert every remaining runtime insert
into a ledger assigns `seq`, matching `INSERT OR IGNORE/REPLACE/ABORT` as well as
the bare form. A positional insert into an append-only ledger is exactly the
shape that breaks silently on the next column, so it is forbidden outright rather
than left to the next person's grep. The first version of that guard scanned
backwards through the source to detect migration blocks; it was rewritten to
strip them with one regex, which is both shorter and actually correct.

C was estimated at one hour, high confidence. It took roughly three, all of it
call-site fan-out rather than schema work. That is the honest signal to carry
into B, which is the same pattern at twenty times the scale.

Suite after C: **1175 passed** (1173 + 2 new).

### D: `CLAUDE.md`'s schema invariant amended

"Schema changes are migrations" now carries a dated pre-release amendment.
Until first release: no data-preservation migration test is required per bump
(write one when the migration does something a reader should not take on trust —
v16 has one because it backfills an ordering column), and a schema bump is no
longer a stop-and-ask, removed from that list.

What does not relax, and is spelled out because it is the trap: the version bump
and forward block still happen, since a fresh database is built by running the
whole ladder, so editing an earlier block is still wrong. The amendment
instructs its own deletion when Bridge has a first real user.

The on-disk shape section now says the human-owned stores are mid-cutover and
points at §3.5. `QA_TEST_MATRIX.md` is deliberately untouched: the row it needs
is "a pre-cutover project refuses to open", which belongs with the cutover.

## 2026-09-15 — #76 finishes: the last two semantic stores, the legacy import, and `audit/`

Four commits. The first two are the mechanical half of the cutover; the third and
fourth are behaviour changes, and both of them turned up something the briefs
that scoped this work had wrong.

### The two remaining stores

`SemanticMappingStore` wrote `semanticMappings/<book>/<fingerprint>.json`;
`semantic_validation_service` wrote one read-modify-write blob at
`semanticValidation/irvtam-v0.1.json`. Both target tables already existed at
workbench v1 with the right lifted columns — `semantic_mappings.fingerprint`
with `UNIQUE(project_id, book_id, fingerprint)`, and
`semantic_validation_runs.suite_id` — so neither needed a schema bump.

Two things worth keeping in mind for the next store of this shape:

**Book-case normalisation stopped being cosmetic.** `path_for()` lowercased its
directory component, so callers had always been free to pass either case:
`semantic_mapping_service` passes upper, `map_units` passes whatever the target
index carries. As a directory, a case mismatch was a miss you would notice. Under
`UNIQUE(project_id, book_id, fingerprint)` it is a *second row*, silently. The
store normalises to lowercase, matching `book_id` everywhere else.

**Both directories had to join `_PRE_CUTOVER_STORE_DIRS` in the same commit as
their redirect.** Without that, a project holding the old files opens and ignores
them — the same silent-empty failure this cutover has now produced four distinct
ways.

The validation blob keeps its append-only `audit` list by staying a
read-modify-write of the whole payload, and each decision additionally appends to
`change_log`. A row image can in principle be rewritten by a later caller; a
`change_log` row cannot, because the triggers reject it. That is CLAUDE.md's
"compacting a record must not compact its lifecycle events" enforced rather than
intended.

`auditPath` came out of both `semantic_validation_list` and
`semantic_validation_decide`. The brief flagged only the `decide` return — there
was a second one in `list`. Nothing consumed either: `SemanticMappingValidation.svelte`
never reads the field, and `npm run check` is clean with it gone from
`finding.ts` and `bridgeClient.ts`.

### The legacy import was not untested, and it was not harmless

`_migrate_legacy_companions()` ran inside `PassageSemanticRuntime.__init__`, so
it re-ran on every runtime construction, and it de-duplicated on
`(project_id, source_path, source_hash)` — a *content* hash. Both its remaining
sources were rewritten by ordinary human work, so every confirm/reject/correct
changed the digest and imported again. `save_evidence_record` is a plain INSERT
with no `ON CONFLICT`, and the evidence id was minted per revision, so each
decision left another `AI_RATIONALE` record, another `legacy-review` record, and
`record_dependencies` edges to every target reference in the file — which pulled
each orphan into the staleness graph, so every scripture edit marked them STALE.
Nothing read any of it: `AI_RATIONALE` has no reader that selects by kind, and
the records carried empty source/target unit ids so nothing could cite them. The
only full scan is `integrity_check`, which re-verifies their content hash. The
accretion made that slower and did nothing else.

So it was deleted, not repointed — the same call made for `aiReview` earlier in
#76, but for a stronger reason. Repointing would have made a live defect
permanent. The usual "preserve current behaviour" instinct is wrong when the
current behaviour *is* the defect.

**The brief said there was zero test coverage. That was wrong, and the way it was
wrong is worth recording.** The claim rested on greps for
`_migrate_legacy_companions`, `_import_legacy_file`, `legacy-evidence` and
`legacy-review`. All four greps were accurate. But three tests in
`test_passage_semantic_runtime.py` (8 cases) pinned this machinery under names
containing none of those strings — `test_legacy_review_state_mapping_is_conservative`,
`test_legacy_validation_is_history_only_and_hash_mismatch_is_stale`,
`test_legacy_record_is_current_only_when_source_and_target_hashes_match` — and
all eight failed the moment the code went. This is the third time in this cutover
that a grep-shaped answer was confidently wrong; the lesson is the same one as
#84's fifth insert site. A grep proves a string is absent, not that a behaviour
is unpinned.

Those eight tested only the deleted code, so they went with it. Replacing them:
`test_reopening_after_a_decision_does_not_accrete_evidence_records`. Since
nothing pinned the broken behaviour, the new test was checked against `aceb06f`
with the two store redirects reverted, where it fails with exactly the accreted
`legacy-evidence-<digest>` record. A test written after the fix is worth what its
red run is worth.

### `audit/`

Three writers, zero readers, every file a duplicate of a native
`checkData/` write made in the same transaction under a namespace Bridge
invented. `sync_comment` and `apply_scripture_edit` are deleted outright.

`_persist_check_selection` is redirected instead, because its record carries
`provenance` (human vs bridge_ai) and `metadata` (which interface, and whether AI
evidence was on screen) and those exist nowhere else — the native selection
record has `username`, a display string. Given the three-way split the whole
design rests on, that is not reconstructable once it stops being written.

The brief proposed `change_log` with `table_name='check_selections'`. That would
raise: `append_event` calls `_require_mutable_table`, and `check_selections` is
neither in `MUTABLE_TABLES` nor a table. It goes to `human_decisions`, where
check decisions already live, so the decision and its provenance share a row key.

Also not in the brief, and the more valuable of the two test changes:
`test_native_and_audit_files_roll_back_if_transaction_write_fails` injected its
failure at the audit file write, which no longer exists. Rewritten to inject at
the provenance write. The guarantee is unchanged and matters more now — a
selection whose provenance never recorded is one nobody can attribute.

### Gates

Engine **1182 passed, 0 failed, serially** (10m37s) — the configuration CI
actually runs. Frontend `npm run check` 0/0, `npm run test` 367 passed,
`npm run build` clean. `src-tauri/` untouched, so cargo was not run.

Under `-n auto` two wall-clock budget tests each failed once and passed alone in
under two seconds: `test_live_greek_room_and_status_stay_responsive_during_check_preparation`
and `test_build_corpus_stats_performance_over_a_realistically_sized_completed_corpus`.
Both pass in the serial run above, so this is parallel contention, not a
regression. They are not covered by `tests/support/waits.py`, which scales
background-job waits rather than performance budgets. Filed separately rather
than fixed here.

## 2026-09-15 — #77: derived stores, the workspace ladder, the rollup cache, sync readiness

Seven commits on `main`, one store group or one mechanism each, plus a docs
commit. #76 was placement: same data, new home, one seam. #77 is cache
coherency: the dashboard reads a copy of data that lives in sixty-six other
databases, and the question is when that copy is allowed to be trusted.

### The decision taken first: a third ladder

`workspace_repository.py` created `devices` and `users` with
`CREATE TABLE IF NOT EXISTS` and no version. Adding `projects`, `settings_kv`
and `project_progress_cache` forced the question CLAUDE.md's "schema changes
are migrations" rule already answers for the other two databases. The
trade-off put to the maintainer: a versioned ladder (consistent, one more
version number, ~80 lines copied from the workbench runner) against keeping
`IF NOT EXISTS` and calling the database disposable — which is false for one
table, because `users` and `devices` ids are stamped on immutable
`change_log` rows. He chose the ladder. `_MIGRATION_V1` is deliberately what
the unversioned first cut created, `IF NOT EXISTS` kept so an existing file is
adopted with its ids; v2 adds the three tables. Backups only for a database
that already held tables (the first cut of that check archived an empty file
on every fresh install; a test caught it). `docs/DECISIONS.md` has the
five-line version.

### The crux: `read_progress_rollup`, and what replaced it

`list_book_progress` walked every sibling in a collection and read
`.bridge/progress.json` without constructing a project, because most siblings
are lazy stubs. The workbench is per project, so the naive redirect is "open
66 SQLite files to draw one dashboard". What landed instead:

- `progress.json` became `progress_chapters` / `progress_findings` /
  `progress_totals`; `load_progress_rollup()` reassembles exactly the dict the
  file held, so `qa_report`, `reporting` and `analytics` did not change. **One
  thing the normalisation lost and the existing coverage test caught:** a
  verse a check job found nothing in has no finding row, but it was checked,
  and the QA report's PASS coverage depends on knowing so. The chapter row now
  lists its `checkedVerses`.
- `save_progress_totals` writes the totals row and then the workspace
  `project_progress_cache` entry, carrying the `change_log.seq` of that write
  as `source_seq`. `list_book_progress` reads the cache in one query.
- `sync_progress_cache()` runs on every `project.open`, after journal
  recovery: the entry is trusted only if `source_seq` equals the seq of the
  latest `progress_totals` write **and** it names this project id and book.
  Seq alone is not enough — a re-import at the same folder starts a fresh
  workbench whose seq restarts at 1, and the stale entry would look current.
  The result is reported on the open result as `progressCache: {state}`
  (`fresh` / `repaired` / `cleared` / `error`), never fatal: the record is the
  workbench, the cache is a copy.
- A materialized sibling with no entry (workspace reset; folder copied from
  another machine) is peeked read-only — `peek_progress_totals`, a `mode=ro`
  URI connection that never creates or migrates — and its entry written back,
  all peeked entries in one commit. `forget`, `delete` and `import` drop
  entries for the folders they touch.

`WorkbenchRepository` needed three things the skeleton did not have:
`batch()` (many writes and deletes in one transaction, so a book-wide check job
is one commit rather than one per finding), `_delete()` (logged as a `delete`
event carrying the row's last image), and `seq` on every receipt.

### The other stores

`check_findings` (one row per chapter), `check_cache` (one row per section,
starts empty), `triage_verdicts` (one row per book, the whole map — the file
was read whole for the same reason), `metrics_events` + `metrics_counters`
(one batch per event), `file_backups` (an index; the files stay under
`backups/`, TEAM_ARCHITECTURE ss3.4). `read_triage_records` keeps its
"sibling without a project" contract through the same read-only peek.

The registry became the `projects` table (whole-list model kept; load and
save are one SELECT and one replace-all transaction); a pre-#77
`project-registry.json` is read once into an empty table and then renamed
`*.imported-<stamp>.json`, because "read once" has to survive the table being
emptied by `forget`. `settings.json` keeps only `*_dpapi` secrets; every other
setting is a `settings_kv` row, adopted once from an older file.

Sync readiness: `unsynced(project_id, after_seq)`, `mark_synced`,
`export_events` / `import_events` as JSON lines. Import is idempotent on
`event_id` and applies row events only when the local row is exactly at the
event's `base_revision`; anything else comes back as a conflict
(`revision_mismatch` / `missing_base`) and the row is left alone. This needed
**workbench v2**: a `change_log` row held the payload but not the lifted
columns (`kind`, `chapter`, `key`, ...) the table also requires, so no generic
importer could rebuild a `human_decisions` row from it. `columns_json` was
added and the immutability trigger rebuilt to cover it. The prep comment's
"the workbench needs no bump" held for the derived stores; it did not hold for
sync, and the bump has its v1→v2 test because a rebuilt trigger is not
something to take on trust.

### Three things found the hard way

**Rows are keyed by the registered project id, and a fixture wrote before
registering.** `test_triage_rpc._plant` constructed a project before
`project.open`, which keyed its snapshot to the path-derived fallback id; the
opened project looked it up by the registered id and found nothing. Fourteen
tests failed with "findings: 0" and no error anywhere. Files had no project id,
so this could not happen before. The fixture now writes `.bridge/project.json`
first, as every real project has by the time a check job runs; production has
no pre-registration writer (`project_import.py`'s one construction only reads).
Recorded in `HANDOFF.md` as a trap.

**`created_at` ties.** `rows()` orders by `created_at` then `id`, and Windows'
clock is about a millisecond. Three metrics events written in one tick came
back in uuid order. Event ids now carry a nanosecond prefix. Any other
append-only store that cares about order has the same problem.

**The synthetic "before" was wrong about what it measured.** The 66-book
Kannada collection on the maintainer's disk has 64 lazy siblings, so the
"66 file reads" the issue describes were 2 reads and 64 stat calls, ~7.5 ms.
Opening all 66 for real is ~2 minutes per book on this machine (materialise +
fingerprint + semantic runtime), so the fully-opened shape was built
synthetically: each lazy sibling given a manifest, RUT's rollup, and (after)
progress rows plus a cache entry. That measures the exact read path both
builds take; it does not measure opening.

### Timings

In-process `BridgeEngine.handle_request`, product pragmas (FULL sync, WAL),
scratchpad copy of the real 66-book Kannada collection, medians of 7 after a
warm-up (report: 3). "before" is `fc26601` in a worktree; "after" is `8b8ad19`
plus the dashboard tweak in the same series. Two before runs and three after
runs were taken; the spread is real and is shown.

| Call | Before | After |
|---|---|---|
| `project.listBookProgress`, 64 lazy + 2 materialized | 7.5 ms | 15–23 ms |
| `project.listBookProgress`, all 66 materialized (synthetic), cache warm | 19 ms (66 file reads) | 25–32 ms (one query) |
| `project.listBookProgress`, all 66, cache cold (66 peeks + refill) | — | 0.75–1.0 s, once |
| `verse.decide`, RUT 1:1 | 41–112 ms | 32–106 ms |
| `project.report`, RUT | 1.05–1.10 s | 0.90–1.39 s |
| `triage.results`, 2 materialized | — | 35–51 ms |
| `triage.results`, all 66 materialized | 25 ms | 0.95–1.13 s |

Decomposed (all-66 shape, medians of 9): `collection_projects` 6.7 ms,
`project_path_key` ×66 4.5 ms, the cache query 5.9 ms, reading 66 small warm
JSON files 3.1 ms, a workspace connection 0.7 ms. So:

- **The dashboard is not faster than warm file reads, and was never going to
  be.** 66 small files the OS already has cached cost 3 ms. The cache's job is
  the comparison the issue actually poses — against opening 66 SQLite files —
  and that is the cold row: ~1 s once, then ~25 ms. Without the cache every
  dashboard load would be the cold row.
- `verse.decide` went from one file rewrite to four workbench commits and one
  workspace commit and is no slower; the fsync dominates either way.
- `project.report` is within noise.
- **`triage.results` regressed on a fully opened collection**, from a stat miss
  per untriaged sibling to a ~14 ms read-only SQLite open per sibling. Filed
  as #94 rather than fixed here; the report screen, not the editor loop.

### Known gaps carried

- The progress rollup's lost-update class between its two writers (dispatcher
  `decide_verse` vs the job thread's `_on_check_job_complete`) shrinks rather
  than disappears: each now touches its own rows, and what remains is the
  recomputed `progress_totals` row, last writer wins.
- `MetricsStore.event` has no production caller; `list_alignment_backups` /
  `restore_alignment_backup` have none. Both on #93.
- `TeamWorkflow` did not move (dead writers) — #78. `hub_credentials` and a
  `sync_conflicts` table — the hub slice.

### Gates

Engine suite serially, the configuration CI runs: **1245 passed, 0 failed**
(11m01s), with the perf tweak in the tree. Frontend and `src-tauri/` untouched (the one protocol change is
an additive `progressCache` field on `project.open`), so those gates were not
run.

## 2026-09-16 — a 66-book import "timed out" after finishing: the alignment scan's 20,612 commits

First import after the 0.10.0 data reset: hin-irv, 66 books, and the UI
reported `sidecar request 'project.import' timed out`. On disk the import was
complete: all 66 folders written 11:45:08–11:45:13 (Genesis normalized, 65 lazy
stubs), all 66 rows in `workspace.sqlite3` by 11:45:14.85, Genesis opened at
11:45:15. Then nothing observable until the Genesis semantic DB stopped being
written at 11:51:09, six minutes later. Rust gave up at the 300 s import
timeout in between; the engine's reply landed on nobody.

### Root cause

`PassageSemanticRuntime.__init__` ends with `synchronize_alignment_state()`, a
content-addressed compatibility scan of `.apps/translationCore/alignmentData/`.
A raw import writes every unaligned source word as its own group with
`bottomWords: []`, and the scan quarantines each of those as
`LEGACY_EMPTY_BOTTOM_WORDS_AMBIGUOUS`. Genesis has 20,612 source words, so
20,612 quarantine records — and `quarantine_migration_record` opened a
connection and committed per record, with `PRAGMA synchronous = FULL`, so one
fsync each. The scan's own `migration_runs` row says it: started
06:15:58.42Z, completed 06:21:09.10Z, `groupsScanned: 20612, quarantined: 20612`.
~66 records/s on this laptop's SSD with the app running.

Not a regression: the writer is from Stage 3 (`ccab58a`), the scan from Stage 4
(`78fdf4b`), the stub shape from the import feature. It surfaces now because a
whole-Bible import opens Genesis first, and Genesis is one of the largest
books. **`project.open` has only the 30 s default**, so before this fix every
first open of a book over ~2,000 source words — most of the Bible — would have
timed out the same way, then succeeded on a retry once the memoized scan had
finished in the background.

### Fix

`FoundationRepository.quarantine_migration_records_bulk(records)`: one
`BEGIN IMMEDIATE`, one `executemany`, one commit, one timestamp for the batch
(the babdee1 convention). `quarantine_migration_record` is now a one-element
call to it. The scan collects into a list and writes once before its
`save_migration_run`. Crash-safety is unchanged: both are still plain
content-addressed idempotency checks, and a crash between the batch and the run
row just redoes the scan.

Measured in a scratch repository, Genesis-sized batch of 20,612 records: **0.25 s**
bulk. 200 per-record commits took 1.49 s (7.5 ms each → ~2.6 min extrapolated;
the live run was slower at 5m11s, presumably antivirus and the app's own I/O).

### Trace lines for the unexplained 43 s

Between the registry touch (11:45:15) and the scan starting (11:45:58) something
took 43 s that nothing recorded. Rather than guess, `open_project` and
`import_project` now emit one `[trace]` line each to stderr with per-phase
wall-clock seconds (`materialize_lazy`, `original_language`, `load_project`,
`register`, `tc_recovery`, `progress_cache`, `semantic_runtime[…]`,
`project_info`; the runtime's own constructor phases nested). sidecar.rs relays
stderr into the diagnostics panel and `engine-events.log` under the app log
directory — as level `warn`, because the relay only distinguishes tracebacks;
a Rust-side `[trace]` → `info` mapping is a one-line follow-up that needs a
`cargo test`, which a running `bridge-engine.exe` blocks (gotcha 13).

### Open question for the maintainer, not decided here

An empty target side is the shape the raw importer itself writes today for
every unaligned word. Quarantining it as "legacy ambiguous" is arguably wrong
in principle, not just slow — 20,612 quarantine rows per large book, one per
word, of a kind nothing reads. Whether the scan should skip the raw-import stub
shape entirely is a design change to the scan and was left alone.

### Gates

`tests/service tests/persistence tests/semantic tests/correction tests/review
tests/alignment`, `-m "not slow"`, `-n auto`: **766 passed**. Two new tests:
the batch writer opens one connection for 500 records and none for an empty
batch; the scan hands the repository exactly one batch and never the
per-record method, and a second open of the same folder writes nothing.
Frontend and `src-tauri/` untouched.

### The 43 s, found: `synchronize_current_text`, one commit per verse

The trace line answered it on the first run from source (scratch roots, hin-irv
66 books, this laptop, app running):

```
[trace] project.import 66 book(s) total=35.13s inspect=1.49s classify=0.03s import_source=10.45s register=0.95s open_primary=22.20s
[trace] project.open gen total=22.20s … semantic_runtime=21.60s … semantic_runtime[… current_text=20.78s … alignment_scan=0.53s]
[trace] project.open exo total=22.22s materialize_lazy=4.69s … current_text=16.45s … alignment_scan=0.38s]   (first open)
[trace] project.open exo total=2.99s  … current_text=2.52s …                                                (memoized re-open)
```

`PassageSemanticRuntime.synchronize_current_text` establishes a
`current_target_revisions` row per verse on first open, and did it as one
read connection plus one commit per verse: 1,533 verses of Genesis → 1,533
fsyncs → 20.8 s. Even a memoized re-open paid 1,533 read connections (2.5 s).
Exodus's first open at 22 s from source is inside the 30 s `project.open`
timeout only on this machine with nothing else running; the live 5m11s vs
2.6 min bench ratio says the installed app would have crossed it.

Fix: read the book's revisions once (`current_target_revisions`, which the
tombstone pass already called) into a dict, collect every new reference, and
write them with `establish_target_revisions_bulk` — one transaction, one
timestamp, the same upsert so it stays idempotent; `establish_target_revision`
is now a one-element call to it. A verse whose text changed still takes the
per-reference prepare/apply intent pair (the crash-safe path; rare on open).
The tombstone pass iterates the pre-loop snapshot, which is equivalent: every
row the loop touched has a reference in the current text and is skipped.

After, same script, same machine:

| Request | Before | After |
|---|---|---|
| `project.import`, 66 books | >300 s (timed out); 35.1 s with the scan batched | **8.6 s** |
| `project.open` GEN inside the import | 5m11s live; 22.2 s | **1.1 s** |
| `project.open` EXO, first open (3.3 s of it is the lazy materialization) | 22.2 s | **4.3 s** |
| `project.open` EXO, re-open | 3.0 s | **0.36 s** |

Gates: same engine areas plus `tests/jobs`, `-m "not slow"`, `-n auto`:
**790 passed**. New test: a first open reads no verse individually and hands the
repository exactly one establish batch of `len(current_target_text)` rows; a
second open establishes nothing.

## 2026-09-16 — simplification audit and a current-state ARCHITECTURE.md

Prompted by the maintainer asking what is causing development delay and runtime
slowness, and which features can go (they had just filed #100 for the Validate
semantic mappings screen), and asking for an architecture picture now that the
workbench cutover is done. Docs only; no code changed.

**Method.** Three read-only sweeps against `83221fd` — every frontend surface and
which `bridge.*` method each calls; every engine module, protocol method, table
and file store; size, test-cost and performance signals — then each claim
re-checked with `grep`/`wc`, then an independent second ranking of the removal
candidates. The second pass caught two errors in the first draft: #99 was
already fixed at HEAD (the draft still quoted the pre-fix "2 min per book"
figure), and `passage_semantic_wire.rs` (1491 lines) turned out to be referenced
only by `mod` in `main.rs` and its own tests.

**Written.** `docs/ARCHITECTURE.md` replaced with a current-state map (process
boundary and the four-layer RPC path, the three databases and the remaining
file trees, the QA pipelines and AI overlays, the UI surface map, engine
subsystems), Mermaid diagrams. `docs/SIMPLIFICATION_AUDIT_2026-09.md` records
the causes, the ranked candidates in three groups (dead today / needs one
decision / load-bearing keep), the runtime and velocity fixes that are not
removals, a suggested order, and draft Idea bodies for the safe group. CLAUDE.md
and DEVELOPER_GUIDE.md pointers updated. The pre-cutover ARCHITECTURE.md is at
`git show 83221fd:docs/ARCHITECTURE.md`.

**Headline numbers.** 148 protocol methods; 50 wired but uncalled by any
component, 13 with no Rust command at all. Stage 3 semantic mapping is a second
semantic stack (about 2.3k LOC, a 119 MB bundled SQLite that is 70% of the
resource bundle, two workbench tables) whose remaining consumers after #100 are
the tN/tW AI-review prompt and a guard on an AI-alignment method the UI never
calls. Five identical background-job runners. 38 of 72 engine test files import
`bridge_service`. `docs/` is 20.8k lines.

**Decisions taken by the maintainer during the audit.** Rank by both developer
velocity and runtime speed. Remove Stage 3 and the AI alignment-proposal path
(#23 is off the roadmap). Keep triage; fix #94. Replace `ARCHITECTURE.md` rather
than keep two architecture docs.

**Not done here, on purpose.** No issues filed (the draft bodies are in the audit
doc for the maintainer to file), no code or resource deleted, no golden touched,
no test run (nothing they gate changed).

## 2026-09-16 — #100: the semantic validation queue is removed; the Stage 3 engine stays

The dashboard's **Validate semantic mappings** button (shown as **Enable
semantic validation** outside Advanced mode) opened the Stage 3 IRVTam
validation queue: 40 machine-proposed cross-verse mappings in Luke and
Philippians that a human confirmed, corrected or rejected. That review finished
on 2026-08-31 (38 / 1 / 1, 95% agreement — the table is in
`DEVELOPER_GUIDE.md`'s Beta 15 snapshot). The maintainer asked for the feature
to go; #100 is the plan and this is the record of what actually happened.

### What was removed

Two commits, frontend first so no caller outlived the method it called.

Frontend and shell: `SemanticMappingValidation.svelte` (389 lines, no other
importer); the `.validation-entry` block, its CSS and the `advancedMode` /
`onOpenSemanticValidation` / `onRequestAdvancedMode` props on
`ProjectDashboard.svelte`; in `App.svelte` the `showingSemanticValidation`
state (nine sites), `VALIDATION_BOOK_IDS`, `openSemanticValidation()`,
`switchValidationBook()`, the `$:` guard that bounced the screen when Advanced
mode was switched off, the `"validation"` member of the `screen` union and its
render branch, and the `validation:` field of the external-navigation snapshot;
`listSemanticValidationCandidates` / `decideSemanticValidationCandidate` in
`bridgeClient.ts`; five `SemanticValidation*` types in `finding.ts`; the two
`semantic_validation_*` Tauri commands and their `main.rs` registrations.
`allowManualOverride` lost its import in `App.svelte` (nothing else there read
it) but the store and the Settings toggle stay — `TranslationHelpsReview.svelte`
still uses them.

Engine and build: `semantic_validation_service.py`; its import, the two
`Methods` constants, the two `BridgeEngine` methods and the two dispatcher
branches in `bridge_service.py`; `tests/semantic/test_semantic_validation.py`
and its `stage3db` entry in `tests/conftest.py`; the block in
`build-sidecars.ps1` that copied the manifest into Tauri resources (and its
hard `throw` when the file was missing); `scripts/generate_irvtam_mapping_candidates.py`.

### What deliberately stayed, and why

- **The Stage 3 mapping engine** — `semantic_mapping.py`,
  `semantic_mapping_bridge.py`, `semantic_mapping_service.py`,
  `semantic_review_policy.py`, `semantic_alignment_guard.py`,
  `semantic_corpus_discovery.py`, the `semantic_mappings` table and the
  Stage 3 DB download in `ci.yml` / `release.yml`. `ai_client.py` builds its
  review pack from `prepare_semantic_mappings_for_review` and runs
  `apply_semantic_review_policy_all` over the results, and
  `TranslationHelpsReview.svelte` renders `semantic_mapping` on AI check
  reviews. Removing that is a separate decision (the 2026-09-16 architecture
  rewrite records it as "removal decided"); it was not made here.
- **`semantic_validation_error`** in `bridge_service.py`'s dispatcher. Despite
  the name it maps Stage 9A's `FoundationValidationError`, and
  `test_qa_review_service_stage9a.py` asserts it. Left exactly as it was.
- **`'semanticValidation'` in `_PRE_CUTOVER_STORE_DIRS`.** It identifies a
  project written by a pre-cutover build; that stays true after removal, and
  dropping it would reintroduce the silent-empty open the cutover was built to
  refuse.
- **The `semantic_validation_runs` table and its `MUTABLE_TABLES` entry.** The
  table is in the workbench v1 block, and the ladder is never edited; a `DROP`
  would be a v3 migration for an empty table. If a v3 happens for another
  reason, the drop can ride along.
- **`docs/validation/irvtam-semantic-mapping-candidates.json`**, now with a
  README that says what it was and that nothing reads it.

### The audit files are gone

The plan's step 0 was to copy the two per-project audit files
(`semanticValidation/irvtam-v0.1.json` under `tam_irv_luk` and `tam_irv_php`)
into `docs/validation/` before the reader code went. They do not exist: the
Tamil IRV projects were re-imported after the #76 cutover, and a search of the
whole machine — `%USERPROFILE%`, `C:\code`, every `bridge-workbench.sqlite3`
(all eight hold zero `semantic_validation_runs` and zero `semantic_mappings`
rows) — found only a pytest temp fixture. The Beta 15 table in
`DEVELOPER_GUIDE.md` is the surviving record, and both that section and
`docs/validation/README.md` now say so. QA matrix rows M29–M35 are `RETIRED`.

### Gates

Run in a fresh worktree (`.claude/worktrees/`), which is worth knowing about
because two things a fresh checkout lacks showed up as failures first:

- Frontend: `npm run check` 0 errors / 0 warnings; `npm run test` 26 files,
  **367 passed**; `npm run build` clean.
- Shell: `cargo check` clean (cold target, 4m58s); `cargo test` **12 passed**.
- Engine: `pytest -n auto -m "not slow"` **1009 passed, 13 failed** on the first
  run. Twelve were `test_semantic_mapping_stage3.py` raising
  `SemanticMappingError: Semantic source database…` — the 125 MB Stage 3 DB is
  git-ignored and a worktree does not have it (exactly the failure `ci.yml`'s
  comment describes). With the file copied in: **21 passed**. The thirteenth
  was `test_logos_get_state_spawns_the_real_helper…` under xdist while the
  other session's `cargo tauri dev` was running; alone it **passed**.
  `test_qa_review_service_stage9a.py::test_invalid_review_input_is_a_validation_error_not_a_crash`
  (the `semantic_validation_error` assertion) **passed**; the workbench
  repository and sync tests **passed**.
- Not run: `build-sidecars.ps1` and `smoke_sidecars.py`. The script change is a
  pure deletion of a copy step, and a full PyInstaller build would have
  contended with the release build running in the other session. The next
  release build exercises it.

## 2026-09-16 — #101: `semantic_corpus_discovery.py` and its test are removed

Group A of the simplification audit (A1). #100 had already deleted
`scripts/generate_irvtam_mapping_candidates.py`; what remained was the generator
module itself, `engine/tc_ai_bridge/semantic_corpus_discovery.py` (403 lines), and
`engine/tests/semantic/test_semantic_corpus_discovery.py` (79 lines, one of the
`_SLOW_FILES` at about 70 s, `stage3db`-marked).

Re-verified against HEAD before deleting: the test file was the only importer
anywhere under `engine/` (`grep -rn semantic_corpus_discovery`), and no other test
referenced its names. Removed with it: the two `conftest.py` entries
(`_SLOW_FILES`, `_FILE_MARKERS`), the file's mention in `ci.yml`'s comment on why
the Stage 3 DB is downloaded, and its cell in `ARCHITECTURE.md`'s module table.
The `stage3db` marker and the Stage 3 engine stay (#109 is not started).

### Gates

Run in a fresh worktree with the Stage 3 DB, sidecar exes and resources copied
in first (the four git-ignored artifacts noted under #100):
`pytest -n auto -m "not slow"` from `engine/`: **1022 passed** in 2m53s, 0 failed.
No frontend or Rust change, so those gates were not run.

## 2026-09-16 — #102: the 13 engine methods with no Tauri command are removed

Group A of the simplification audit (A2). `project.sweepStart/Status/Cancel`,
`semanticMapping.getForVerse/confirm/rerunForVerse`,
`versification.detect/orgRef/backVersificationMap`,
`alignment.corpusStats.summary/forVerse` and `verse.evidence` had no
`#[tauri::command]`, so no frontend could reach them. Their `Methods` constants,
handler methods, dispatcher branches and the three `Sweep*` exception mappings are
gone, and `engine/project_sweep.py` (170 lines) with them. `bridge_service.py` is
309 lines shorter.

### What the re-verification changed about the plan

- **`project_sweep.py` was not used only by the sweep methods.** Its `SweepBook`
  dataclass was the return type of `_sibling_sweep_books`, which
  `build_collection_report` (`project.collectionReport`, engine side kept for
  #108) also calls. The helper is now `_materialized_collection_books` and returns
  `report_jobs.ReportBook`, the same three fields plus two defaults; the
  distinction from `_report_books` (skip a missing sibling vs. list it as not
  checked) is unchanged. `_run_layer1_checks_for_book`, the sweep's worker
  callback, had no other caller and went with the sweep.
- **The corpus-stats cache stays.** `_corpus_stats_by_book` and its invalidation
  in `_save_alignment`/`complete_alignment` are read by the live
  inconsistent-rendering finding (`_consistency_findings_for_book`), not only by
  the two removed RPC readers. Only `_corpus_stats_for_book`,
  `corpus_stats_summary` and `corpus_stats_for_verse` were deleted. A side effect
  worth knowing: the RPC path built its table with `include_collection=True` and
  the finding path without, under the same cache key, so whichever ran first
  decided what the other saw. That ambiguity is gone with the RPC path.
- **`versification_tool` stays imported** in `bridge_service.py` for the
  `VersificationUnavailable` mapping in `handle_request`: the Stage 4 runtime
  (`passage_semantic_runtime.py:442,774`) calls the library inside dispatched
  requests. `detect_versification` and the `_versification_by_book` cache had no
  caller left and were removed.
- **`test_versification_concurrency.py` is untouched.** It drives
  `tc_ai_bridge.versification` directly in a fresh subprocess, not the RPC.
- **`verse_evidence.py` now has no importer outside tests.** The audit called it
  load-bearing on the strength of `bridge_service.py:103`, which was exactly the
  import the `verse.evidence` handler needed. The module stays as the task
  required; filed as its own Idea issue rather than deleted here.
- **The semantic-mapping imports** (`semantic_mapping_service`,
  `semantic_mapping_bridge`) left `bridge_service.py` with the three handlers.
  Both modules stay for #109.

### Tests and scripts that followed

Nine protocol tests removed: four sweep tests and `wait_for_sweep` in
`test_bridge_service.py`, the two versification protocol tests with the Psalm 3
fixture (the same shift is still asserted at module level in
`tests/versification/`), the two `verse.evidence` tests (the four pure-resolver
tests stay), and `test_corpus_stats_protocol_summary_and_for_verse`.
`test_corpus_stats_cache_invalidated_when_a_verse_is_newly_completed` was
rewritten to observe `_corpus_stats_by_book` through the finding path instead of
the RPC, so the invalidation lines keep a test. `scripts/smoke_sidecars.py` lost
its versification and corpus-stats blocks. `QA_TEST_MATRIX.md` rows A10 and A11
are `RETIRED (#102)`; A12 still covers the library. Docstrings in `reporting.py`,
`qa_report.py` and `report_jobs.py` that pointed at `project_sweep.py` were
reworded.

### Gates

`pytest -n auto -m "not slow"` from `engine/`: **1013 passed**, 0 failed, in
2m16s (1022 before, minus the nine removed tests). `pyflakes` on the touched
files reports only two unused imports (`os` in `bridge_service.py`, `shutil` in
`test_bridge_service.py`) that were already present at HEAD. No frontend or Rust
change; those gates were not run. `smoke_sidecars.py` was not run against a
frozen build (that needs `build-sidecars.ps1`; the next release build exercises
it).

## 2026-09-16 — #105: the `ai.explain` path is removed; its gate tests move to `ai.review`

Group A of the simplification audit (A5). `ai.explain` (`explain_verse`) was a
synchronous, thinner duplicate of what the `ai.review` job does per verse: both
called `prepare_verse_review` and returned its check reviews. No component called
`bridge.aiExplainVerse`. Removed: the `Methods` constant, the handler and
dispatcher branch, the `ai_explain` Tauri command and its `main.rs` registration,
the `"ai.explain" => 300` timeout entry in `sidecar.rs`, the `aiExplainVerse`
client method, and the `ai-explain-no-key` block in `scripts/smoke_sidecars.py`.

### What the re-verification changed about the plan

- **The test file was not an `ai.explain` test file.** `tests/ai/test_ai_explain.py`
  held eleven tests; five called `ai.explain`, six drove `ai.review.start`, the
  Tamil plugin guidance and `gate_check_reviews`, and two other files imported its
  fixtures (`imported_titus_project`, `_grounded_fake_transport`,
  `_wait_for_ai_job`). Deleting it would have removed live coverage. It is now
  `tests/ai/test_ai_review_protocol.py` with nine tests. Two `ai.explain`-only
  tests (missing API key; evidence-backed run) went. The other three exercised
  `prepare_verse_review`'s selection-consistency gate (recover the exact quoted
  target, keep an ambiguous pass pending, never turn a problem into
  nothing-to-select) and only used `ai.explain` as the way in; they now run the
  live `ai.review.start` job and read `latestResult.result.checkReviews`, the same
  `AICheckReview.to_dict()` rows. Assertions are unchanged.
- **The 203 s does not go away.** That figure was the whole file, and almost all of
  it is the shared `imported_titus_project` fixture (a real import plus
  materialization), which the nine remaining tests still need. Only two tests'
  worth of setup is saved. The `_SLOW_FILES` entry follows the rename.
- **`AiExplainResult` stays in `finding.ts`.** `ReviewPanel.svelte` uses it as the
  display type for the `ai.review` job result; only `bridgeClient.ts`'s import of
  it went with the method. Its name is now misleading; left for #103 or a rename
  when the panel is next touched.
- **The smoke block's stated purpose was already covered.** It existed to prove
  `knowledge_base.py` is importable inside the frozen executable.
  `bridge_service.py` imports `KnowledgeBaseError` at module level, so the frozen
  `ping` proves the same thing. QA matrix rows A22 and A23 are `RETIRED (#105)`.
- Two historical docstrings (`resource_materializer.py:269`,
  `test_resource_materializer.py:170`) still say a gap was found "while
  investigating ai.explain". That is what happened; left as history.

### Gates

- Engine: `pytest tests/ai/test_ai_review_protocol.py tests/alignment/test_ai_review_stale_after_apply.py`
  (both `slow`, so run explicitly): **13 passed** in 3m34s.
  `pytest -n auto -m "not slow"`: **1013 passed**, 0 failed (4m08s, slowed by the
  concurrent cargo build).
- Frontend: `npm run check` 0 errors / 0 warnings; `npm run test` 26 files,
  **367 passed**; `npm run build` clean.
- Shell: `cargo check` clean (5m52s, cold target). `cargo test` ran once for the
  batch of Rust-touching issues (#105, #103, #106); its result is under the #103
  entry below.

## 2026-09-16 — #103: the Rust and TypeScript layers of 47 UI-dead methods, and `passage_semantic_wire.rs`

Group A of the simplification audit (A3). A survey of `bridgeClient.ts` against
every `.ts`/`.svelte` under `src/` (excluding the client itself and tests) found
53 of 137 client methods with no caller. 47 of them are the ones the issue names
and went here: `scanProject`, `projectCollectionReport`, `triageClear`,
`saveAlignment`, `completeAlignment`, `alignmentBackups`, the six
`passageSemantic*`, five `sourceSemantic*`, five `targetSemantic*`, five
`semanticLocation*`, six `meaningAnalysis*` and seven `qaAudit*` direct-access
methods, `semanticReviewDecideLocation/Meaning`, `reviewHistoryGetEntityHistory`,
`analysisJobGetRecent`, `paratextSetReference`, `logosGetState/SetReference`.
Each lost its client method, its `#[tauri::command]` and its `main.rs`
registration. `commands.rs` goes from 137 commands to 90 (669 lines), the client
from 137 methods to 90. `semanticLocationGetRange` and `targetSemanticGetRange`
stay: `PassageAlignmentMode.svelte` calls both. Every engine handler stays.

`src-tauri/src/passage_semantic_wire.rs` (1,491 lines) is gone with its `mod`
line. It was referenced by nothing but `main.rs` and its own three tests. The
JSON schema it mirrored (`schemas/bridge-passage-semantic-v1.schema.json`) stays:
`passage_semantic_models.py`, two engine test files and `passageSemanticV1.ts`
reference it. Its test fixture `schemas/fixtures/unicode-spans-v1.json` stays too,
read by `test_passage_semantic_foundation.py`.

### What else the re-verification turned up

- **Six more dead client methods the issue did not name**: `chapterVerses`,
  `getVerse`, `alignmentStatus`, `correctionGetProposal`, and the two
  AI-alignment ones (`aiProposeAlignment`, `aiApplyAlignmentProposal`, which are
  #109's). The Rust command `verse_run_checks` has no TS caller either (the UI
  runs checks through `checks.start`). Left in place; the first four and the Rust
  command are filed as one Idea issue.
- **Types.** Pruning the client's type imports and then surveying every
  `export` under `src/lib/types/` for references outside its own file found 27
  unused declarations. Eleven were orphaned by this change and went:
  `CollectionReport` (`finding.ts`); `PassageSemanticRuntimeStatus`,
  `PassageSemanticProjectMetadata`, `CurrentPassageSnapshot`,
  `PassageSemanticStaleSummary`, `PassageSemanticMigrationReport`,
  `SourceSemanticInventory`, `MeaningAnalysisRun` (`passageSemanticV1.ts`);
  `DecideLocationResult`, `DecideMeaningResult`, `EntityHistory` (`qaReview.ts`).
  The other sixteen were already unused at HEAD (the same survey run against
  `git archive HEAD`), so they are in the Idea issue, not deleted here.
  `ReportRow` was imported by the client and mentioned only in a comment; the
  import went.
- **`sidecar.rs`.** The `logos.getState | logos.setReference => 20` timeout entry
  went: no Rust command sends those strings any more (the engine keeps the
  handlers; Logos is reached through `navigation.*`). One assertion in
  `triage_polling_stays_interactive…` checked the default timeout for
  `triage.clear`, which Rust no longer sends; that line went, the test stays.
- **Docs.** `CLAUDE.md`'s `cargo test` note now says `sidecar::tests`, 9 tests;
  `ARCHITECTURE.md`'s "cost of the shape" paragraph records the post-#102/#103/#105
  counts; QA matrix A04 records 9 tests and why. `ci.yml`'s stub comment no longer
  names the wire file.

### Gates (run once for the #105, #103, #106 batch, after all three were applied)

- `cargo check` clean (warm, 6s; no dead-code warnings). `cargo test` **9 passed**,
  0 failed.
- `npm run check` 0 errors / 0 warnings; `npm run test` 26 files, **367 passed**;
  `npm run build` clean.
- No engine change in #103 or #106; the engine suite ran for #105 (above).

## 2026-09-16 — #106: the dead `showSource` store

Group A of the simplification audit (A7). `showSource` in `stores.ts` was a
`writable(false)` nothing ever set; `VerseList.svelte` imported it and toggled
`class:show-source` on it, and no stylesheet defined `.show-source`. Both went.
#100 had already removed the `"validation"` screen literal and the
`SemanticMappingValidation` gating the issue also listed. If #67 (show the
original-language text in the editor) wants a source toggle, it adds one that is
wired. Gates: the batch run recorded under #103.

## 2026-09-16 — #107: six point-in-time docs and `HANDOFF.md` move to `docs/archive/`

Group A of the simplification audit (A8). `docs/archive/` now holds
`V11-000_STAGE6B_ALIGNMENT_SPIKE`, `V11-000a_REVIEW_FIX_PROMPT`,
`V11-003_ISSUE57_PROMPT`, `V1_1_UNICODE_ACCEPTANCE`, `V1_1_ACCEPTANCE_01`,
`STAGE_9B4_ACCEPTANCE` and `HANDOFF.md`, moved with `git mv` so their history
follows, plus a one-table `README.md` saying what each was. `HANDOFF.md` carries
an "archived" banner; it is kept verbatim because code comments and this log cite
its section numbers (§31, §39 …).

The obligation moved: `CLAUDE.md`'s "schema changes are migrations" rule now
routes the note to `docs/BUILD_LOG.md`, `CONTRIBUTING.md`'s Friday rhythm updates
`BUILD_LOG.md`, `DEVELOPER_GUIDE.md`'s audit-snapshot pointer and
`TEAM_ARCHITECTURE.md`'s list of docs-to-update no longer name `HANDOFF.md`.

Inbound links were re-pointed mechanically (`docs/<name>.md` →
`docs/archive/<name>.md`) across every tracked `.md`, `.py`, `.yml`, `.ts`,
`.svelte` and `.mjs` file except `SIMPLIFICATION_AUDIT_2026-09.md`, which
describes the pre-move state on purpose. That touched `BUILD_LOG.md` (its own
history), `QA_TEST_MATRIX.md`, `DEVELOPER_GUIDE.md`, the 0.9.5 and 0.9.7 release
notes, `scripts/seed_correction_acceptance.py`, seven engine modules and tests
whose comments cite the review docs, and the archived docs' links to each other.
Two bare `HANDOFF.md §39` citations (`correction_verification.py`,
`test_correction_stage9b4.py`) now say `docs/archive/HANDOFF.md`. About 5.9k
lines leave the `docs/` reading path; nothing is deleted.

### Gates (once for #107 and #104)

`pytest -n auto -m "not slow"` from `engine/`: **1008 passed, 1 failed** in
2m45s. The failure was not this change's: `test_correction_stage9b3a.py` read
the Rust wire file #103 deleted (see the #103 follow-up entry below). Every
Python file whose comments were re-pointed here imported and ran.

## 2026-09-16 — #104: five engine modules with no importer

Group A of the simplification audit (A4). A precise import grep over `engine/`
and `scripts/` (excluding `tests/` and `vendor/`) found no importer for
`semantic_location_benchmark.py` (205 lines), `qa_benchmark.py` (157),
`paratext_api.py` (117), `text_graphemes.py` (81) or `identity.py` (51). Only the
first had a test (`tests/semantic/test_semantic_location_benchmark.py`, 85 lines,
3 tests); the other four had none. All six files are deleted; `ARCHITECTURE.md`'s
connector row no longer lists `paratext_api.py`.

Two checks the issue asked for: `issueResolution.queueParatext/retryParatext`
never reached `paratext_api.py` (`paratext_notes.py` and `paratext_connector.py`
import nothing from it; the Paratext handoff goes through the companion plugin,
not the registry HTTPS client). `text_graphemes.py` was Tk caret-safety code from
the legacy Tkinter tool, with no Tk left to protect.

**The benchmarks were deleted, not moved.** The issue offered "move the two
benchmarks under `scripts/` if anyone still runs them". Nothing runs them: neither
module has an entry point, no script or CI step calls them, and `HANDOFF.md` §"At
the end of Stage 9A" records that every Stage 6B/7/8 benchmark is
`MACHINE_PROPOSED` only, never human-reviewed. The evaluators are one `git show`
away (`279b6e6:engine/tc_ai_bridge/semantic_location_benchmark.py`) if a runner
is ever built. What they read stays: `engine/resources/semantic_location/benchmark-v1.json`
and `engine/resources/qa_audit/{omission,addition}-benchmark-v1.json` (15 KB
together) are still bundled and are now read by nothing. `meaning_benchmark.py`
and its test are the same shape but were not named by the issue and stay.

### Gates

Run once for #107 and #104 together: `pytest -n auto -m "not slow"` from
`engine/`, **1008 passed, 1 failed** (2m45s); the failure was #103's, see the
next entry. 1013 before, minus the three benchmark tests removed here, minus
the one failing test, plus none added.

## 2026-09-16 — #103 follow-up: one engine test read the deleted Rust mirror

The audit's claim that `passage_semantic_wire.rs` was "referenced only by `mod`
in `main.rs` and its own tests" was wrong by one reference:
`tests/correction/test_correction_stage9b3a.py::test_application_and_strict_context_validate_against_canonical_schema`
read the file as text and asserted that eight `pub` fields of the Rust
`CorrectionApplicationIntent` mirror existed. Rust never deserialized that type
(the audit's point stands), so the assertion was checking a mirror nothing used.
The test now checks the JSON schema and the TypeScript mirror only, with a comment
saying why the Rust half went.

Why the batch gates missed it: #103 changed no Python, so the #105/#103/#106
batch ran the frontend and Rust gates but not pytest, and pytest was where the
reference lived. The failure surfaced on the next engine run (#104/#107). Lesson
for the next cross-layer deletion: a `grep` for the file's *path* across the whole
repo, not just its module name, and run every gate for a batch regardless of
which layer changed. `279b6e6` is on `main` with the failing test; the fix
follows it in the same push as #104/#107.

### Gates

`pytest tests/correction/test_correction_stage9b3a.py`: **34 passed** in 22s.
The full fast run that found it:
`pytest -n auto -m "not slow"` **1008 passed, 1 failed** (this test), 0 other
failures across #104 and #107.

## 2026-09-16 — #110: the navigation poll makes no round-trip while sync is off

Audit item B6. `App.svelte` ran `pollNavigation` every 800 ms for the app's
lifetime, guarded only by an in-flight flag and `engineStatus === "ready"`; with
neither Paratext nor Logos sync enabled the engine's coordinator returned at once
(`navigation.py:368`), so each tick was a Rust → stdio → Python round-trip that
did nothing except occupy the single-threaded dispatcher about 75 times a minute.

The change is the smallest of the two the issue offered: the timer keeps ticking,
but `pollNavigation` returns before the RPC when `$navigationStatus.enabled` is
false. For that gate to have a real value, a new `refreshNavigationStatus()`
(one `navigation.status` call, no connector probe) runs once when the engine
reports ready and once after a sidecar respawn. Turning sync on later already
works without more code: `SettingsModal.save()` calls `bridge.navigationStatus()`
and sets the same store, so the next tick polls. Turning it off sets
`enabled: false` the same way and the ticks go quiet again. Nothing changes while
a connector is enabled; the connectors, `navigation.bridgeChanged` publishing and
the candidate-handling path are untouched.

**Not verified in the installed app.** The issue asks for a real-app check that
enabling sync after startup begins polling. The reasoning above is from the code;
the desktop walk-through (start with sync off, open a project, enable Paratext or
Logos in Settings, confirm the connector shows connected and follows a reference)
is still to do, so #110 stays open with that one item.

### Gates (once for #110 and #112, both frontend/Rust)

`npm run check` 0 errors / 0 warnings; `npm run test` 26 files, **367 passed**;
`npm run build` clean; `cargo check` clean; `cargo test` **9 passed**.

## 2026-09-16 — #112 step one: `project.open` joins the 180 s timeout class

`project.open` was absent from `request_timeout_seconds` and so got the 30 s
default. After #99 it measures about 1 s for Genesis from source and about 4 s for a
lazy sibling's first open on a fast machine, about 2x that in the installed app,
and it is whole-file local I/O, the same shape as `project.inspectImport`. A false
timeout here is the bad direction: Rust stops waiting while Python finishes
opening, and the UI is left with no project. It is now 180, with a comment in the
table saying why, an assertion in
`large_project_discovery_and_inspection_have_bounded_headroom`, and
`ARCHITECTURE.md`'s timeout paragraph updated.

Step two (return project info after `TranslationCoreProject` loads and build
`PassageSemanticRuntime` on a worker) changes what the UI sees during open and is
not a no-impact change; it stays open on #112.

## 2026-09-16 — #115: one generic `engine_call` command replaces 86 Rust forwarders

Audit §5.2, done after #103 so the mapping was built from the live surface. Every
`#[tauri::command]` that forwarded to the engine did exactly one thing:
`sidecar.send_request("<method>", json!({...}))`. `commands.rs` now holds one
`engine_call(method: String, params: Option<Value>)` that forwards both verbatim,
plus the four commands that are not engine calls (`engine_log_recent`,
`pick_project_folder`, `pick_import_file`, `pick_save_path`). 1,381 lines → 84;
`main.rs` registers five handlers. The per-method timeout table in `sidecar.rs`
keys on the method string and is untouched. `capabilities/default.json` is
untouched: it permits the sidecar, not commands.

On the client, `bridgeClient.ts` gained an `EngineMethod` union of the 86 live
method strings, and `call<T>(method: EngineMethod, params?)` invokes
`engine_call` and unwraps the envelope as before. Every `bridge.*` wrapper kept
its signature; each now names the engine method instead of a snake_case command.
Adding a protocol method is now an engine handler, one union member and one
wrapper, with no Rust change and no Rust rebuild, which is the cost profile the
cross-verse alignment slices (#116–#119) need.

### What the re-verification settled

- **Defaults.** About 30 forwarders filled optionals before sending (`jobId`
  → `""`, `force` → `false`, `limit` → `50`, `order` → `"CANONICAL"`, `mode` →
  `"gap_fill"`, `metadata` → `{}` …). Every one of those engine dispatcher
  branches already reads the key with the same default (`p.get(k, default)` or
  `p.get(k) or default`), and JSON serialization drops `undefined` values, so the
  wrappers pass their existing argument objects straight through. Checked branch
  by branch, not assumed.
- **`settings.set`** was the one reshaped call: Rust took `params: Value` and the
  client wrapped it as `{ params }`. The engine spreads the object
  (`set_settings(**p)`), so the wrapper now passes it directly.
- **`runVerseChecks`** used a raw `invoke` (it reads `findings` off the envelope,
  not `result`) and now goes through `engine_call` the same way. That call was
  invisible to #103's survey regex, so #121's "`verse_run_checks` has no TS
  caller" is wrong and is corrected on the issue.
- **A regression guard** for the new seam: `src/lib/api/__tests__/bridgeClient.test.ts`
  mocks `@tauri-apps/api/core` and asserts the command name, method string and
  params for a plain call, the `settings.set` pass-through, the `ping` no-param
  case, the `findings` envelope path and the error unwrap.

**Not verified in the installed app.** `cargo check`/`test` prove the Rust side
compiles and registers; the Vitest guard proves what the client sends; the engine
side is unchanged. The missing piece is one real run: open a project, run checks,
save a setting. #115 stays open for that, and the first cross-verse slice will
exercise it anyway.

### Gates

`cargo check` clean; `cargo test` **9 passed**. `npm run check` 0 errors /
0 warnings; `npm run test` 27 files, **374 passed** (7 new); `npm run build`
clean. No engine change.

## 2026-09-16 — #116: the Cross-verse alignment page, slice 1 (range view, same-verse editing, gap overview)

The first of the four cross-verse slices (#116 → #119), started once #115 had landed so
the new engine method cost one handler, one `EngineMethod` member and one wrapper —
no Rust change, no `cargo` gate. The maintainer's design defaults from the brief were
taken as fixed: same-verse drops go through the existing `alignment.realign` /
`alignment.unalign`; a cross-verse drop is refused with a notice until the link
store lands (#117); verse numbers are the opaque strings from `verseNums`, ordered
by index into the chapter list, never parsed; drag is pointer-based and shared.

### What was built

- **`alignment.getRange(chapter, verses[])`** (`bridge_service.py`). Validates every
  verse string against the chapter (unknown → `project_error`), preserves caller
  order, de-duplicates, and composes one `_alignment_context` per verse. The
  context function gained a keyword-only `chapter_counts` so the range computes
  `alignment_status(chapter)` **once** instead of once per verse (the per-call
  rescan at the old `:1582` made a range O(N²) in verses). Every context — the
  single-verse `alignment.get` too — now carries `gaps: {sourceUnmatched,
  targetUnmatched}`: top tokens in no group with a bottom word, bottom tokens in
  no group.
- **`src/lib/alignmentDrag.ts`**: the pointer drag state machine that lived in
  `AlignmentModal.svelte` (`createPointerDrag` → a Svelte store, `start/move/up`,
  `attach(window)`, `consumeSuppressedClick`). Drop targets are still the two
  data attributes; their *values* are opaque to the module, which is what lets
  the range page put `"verse|H001"` in a column and the verse in a bank while the
  old modal keeps `H001` and `"true"`. `document.elementFromPoint` is injectable
  so the state machine is testable under jsdom.
- **`src/lib/alignmentGroups.ts`**: the pure group algebra both editors share
  (`groupForTarget`, `alignedTargetsFor`, `unalignedTargets`, `unmatchedSources`,
  `gapCounts`, and `bottomIdsAfterDrop` — the "resend the column's existing words"
  rule that `realign` needs).
- **`AlignmentModal.svelte`** consumes both modules; no behaviour change intended,
  and a "Cross-verse alignment ›" link under its title.
- **`CrossVerseAlignmentModal.svelte`**: range picker (from/to selects over
  `$verseNums` plus toggle chips for the span), gap strip (per verse: status,
  source/target counts; click filters both columns to that verse's gaps), two
  vertically scrolling columns — source rows with lemma, lexicon tooltip and
  `LexiconPopup` on click and a drop cell each; per-verse word banks in verse
  order. A same-verse drop or click-drop calls `realignWords` / `unalignWords`
  and then reruns `runVerseChecks(["alignment","greekroom"])` for that verse and
  updates `alignmentStatusByVerse` / `checkStatusByVerse` / `findingsByVerse`,
  exactly as the single-verse modal does. It closes itself if the chapter changes.
- **Entry points**: a "Cross-verse alignment" button in the editor toolbar next to
  "Alignment Review" (`App.svelte`), and the link in the single-verse modal; both
  go through `openCrossVerse` in `alignmentUi.ts`, which applies the same guards
  as `openAlignment` and closes the single-verse modal first.
- `src/lib/crossVerseRange.ts`: `defaultRange` (selected ±1 by index),
  `rangeBetween`, `toggleVerse`, `spanOf`, and the `"verse|id"` composite helpers.

### Verified in the real app (dev build, 1366×768)

`npm run tauri dev` from the worktree with the freshly built sidecars, window sized
to 1366×768 (viewport 1352×731 CSS px), driven through WebView2's remote-debugging
port so the drags were real `Input.dispatchMouseEvent` pointer sequences, not
synthetic DOM events. Project: the imported Tamil IRV collection, Genesis 1.

- Toolbar button present; with 1:2 selected the page opened on **1:1–1:3**
  (chips 1, 2, 3 on). Gap strip read v.1 7/5, v.2 14/12, v.3 6/6 — matching the
  alignment file on disk. Both columns scroll vertically only: each `.scroll`
  reported `scrollWidth == clientWidth` (690 and 549), and the document had no
  horizontal overflow at 1352 px.
- **Same-verse drag**: `பூமியானது` from the 1:2 word bank onto the `וְ⁠הָ⁠אָ֗רֶץ`
  cell of 1:2. The cell highlighted while the pointer was over it (`.drop-hover`
  on `2|H001`), the release saved, the notice read "Alignment saved. Local and
  Greek Room checks for 1:2 are current.", v.2 went to `partial` 13/11, the bank
  word greyed, the verse-list glyph for 1:2 turned `partial`, and
  `alignmentData/gen/1.json` verse 2 held the new group.
- **Cross-verse drag**: `ஒழுங்கற்றதாகவும்` from 1:2 onto the first cell of 1:1. The
  1:1 cell highlighted, the release showed the "saved in the next slice (#117)"
  notice, no engine call was made, and verse 1 on disk was unchanged.
- **Old modal regression**: "⇄ Align words" for 1:2 rendered on the shared drag
  module; a real drag onto its second column saved and both cells showed their
  words. Its new link closed it and opened the page on 1:1–1:3.
- The dev project was returned to its original state with "Restore selected" on
  the oldest backup (verse 2: 0 groups with bottom words, 12 in the bank).

Observed while doing that, and **not** from this change: pressing "Undo last change"
twice in a row re-applies the change, because each undo writes a `restore` history
row whose backup is the pre-undo state and the next undo restores that latest
backup. Filed as #122 rather than fixed here.

### Deliberately not done

No cross-verse persistence, no `completionState` change, no aligned USFM change, no
change to the Semantic or Passage tabs of Alignment Review (still held), no golden
or threshold change. The single-verse modal keeps its horizontal interlinear row
(#72); the range page's vertical layout is the answer to #72 for this surface only.

### Gates

Engine: `pytest tests/alignment/test_alignment_get_range.py -v` **7 passed**;
`pytest -n auto tests/alignment tests/service -m "not slow"` **166 passed** (58 s);
`pytest -n auto -m "not slow"` **1016 passed** in 2 m 58 s (12 cores). Frozen pair:
`scripts/smoke_sidecars.py` passed apart from the known pre-existing
`project.inspectImport` duplicate-classification mismatch. Frontend: `npm run
check` 0 errors / 0 warnings; `npm run test` 30 files, **399 passed** (32 new across
`alignmentDrag`, `crossVerseRange`, `CrossVerseAlignmentModal`); `npm run build`
clean. No Rust change, so `cargo` was not run.

## 2026-09-16 — #117: cross-verse alignment, slice 2 — the Bridge-private link store, link/unlink, status, invalidation

The rule this slice rests on is the one `semantic_alignment_guard.py` states:
translationCore alignment groups are verse-local, and Bridge never fakes a cross-verse
link inside them. `_validate_alignment_identity` enforces it structurally on every save.
So a reviewer's judgement that a source token of verse 1 is realized in verse 2's target
text now lives in `bridge-workbench.sqlite3`, and nowhere in `alignmentData/`. The tC
group for the source token stays empty, the target word stays in its own verse's word
bank, and `completionState` never turns complete for either verse.

### What was built

- **Workbench v2 → v3** (`workbench_repository.py`, `_MIGRATION_V3`): table
  `alignment_cross_verse_links` with the nine common columns plus lifted `chapter, verse,
  source_signature, target_chapter, target_verse, target_signature, state`, two scope
  indexes and a UNIQUE index on the pair per project/book. Added to `MUTABLE_TABLES`, so
  it rides `_write` / `_delete` / export / import unchanged and the two per-table
  parametrised tests cover it for free. Migration test in the established style
  (`test_workbench_v2_to_v3_adds_the_cross_verse_link_table_and_keeps_v2_data`: build v2
  from the raw v1+v2 scripts, insert a row and an event, open, assert v3, old data
  readable, `pre-workbench-v3-*` backup taken, new table writable, UNIQUE enforced).
- **`tc_ai_bridge/cross_verse_links.py`** (`CrossVerseLinkStore`, reached as
  `project.cross_verse_links`): `link`, `unlink`, `links_for_verse` (both directions),
  `invalidate_missing_targets`. Identity is the pair of tC token signatures
  (`word U+241F occurrence U+241F occurrences`) plus chapter and verse on both sides; the
  positional `H001`/`T001` ids are resolved on the way in and back on the way out and are
  never stored. Every change is three writes: the row (its change_log row image comes
  with it), a domain event (`crossVerseLink` / `crossVerseUnlink` /
  `crossVerseInvalidate`), and an `alignment_history` row **without** `backupPath`, so it
  is in the history and invisible to `alignment.restore`, which only restores files.
  Re-linking an `invalid` pair reactivates it; re-linking an `active` pair is refused.
- **`alignment.crossVerse.link` / `.unlink`** (`bridge_service.py`). Params
  `{source: {chapter, verse, topId}, target: {chapter, verse, bottomId}}` and
  `{linkId}`; both return `{link, source: context, target: context}`. Refused
  (`alignment_error`): same verse on both ends, source token already has a target word in
  its own verse, target word already grouped in its own verse, target word absent from
  the current text, stale id. Unknown chapter/verse or missing fields are
  `project_error`. `WorkbenchConflict` / `WorkbenchValidationError` now map to
  `revision_conflict` / `workbench_validation_error` instead of falling to
  `internal_error`.
- **Status semantics.** Every alignment context now carries `crossVerseLinks` (with
  this verse's resolved `sourceTopId` / `targetBottomId`), `crossVerseAccountedIds`,
  `crossVerseRealizedIds`, the two counts, and `fullyAccounted`. `gaps` is net of active
  links. `status` and `completionState` are **unchanged** and keep telling the tC truth:
  a fully-accounted verse still reads `partial`/`untouched` and `pending`, `canComplete`
  stays false because its word bank is not empty. The editors drop the "not fully
  aligned" flag from `fullyAccounted`, not from `status`. A fifth status value was
  considered and rejected here: it would ripple into `qa_report.py`'s alignment counts and
  the report types, which is outside this slice.
- **Invalidation.** `apply_scripture_edit` computes the target signatures the reconcile
  step dropped and, as the last step inside its journal try-block, marks every active
  link pointing at one of them `invalid` (with a reason). A failed edit therefore rolls
  back before any link is touched; a word that survives the edit under the same
  signature keeps its link. The result reports `crossVerseLinksInvalidated`.
- **Page and modal.** A drop across verses now calls `crossVerseLink` (source = the
  column's verse and token, target = the dragged word), patches both verses' contexts and
  reruns each verse's local checks; the source row shows a dashed "realized in v.N" chip
  with a remove control, the target word stays in its bank greyed with "↔ v.N" and a
  remove control, both calling `crossVerseUnlink`; an invalid link shows "link invalid"
  with the reason as its tooltip; the gap strip adds "↔ N linked across verses". Dropping
  into another verse's word bank is refused with guidance. The single-verse Align Words
  modal counts only unaccounted words in its flag, appends "N word(s) linked across
  verses", greys accounted bank words with "↔", and shows a dedicated line when
  everything left is linked.
- **Not called yet:** `synchronize_alignment_state()` after a link change. Its memo keys
  on the tC alignment digest, which a link does not change, so the call would be a
  no-op; #119 extends the digest and wires the staleness edge properly.

### Verified in the real app (dev build, 1366×768, Tamil IRV Genesis 1)

Same harness as #116: `npm run tauri dev` from the worktree with freshly built sidecars,
WebView2 remote debugging, real pointer sequences.

- Opening the collection migrated the real Genesis workbench database from v2 to v3
  (`backups/pre-workbench-v3-...` appeared beside the existing `pre-workbench-v2-...`),
  and the project opened normally. After the check the v2 file was put back from the copy
  taken beforehand, so the installed 0.10.3 app, which refuses a v3 database, still opens
  it.
- Page on 1:1–1:3; gap strip 7/5, 14/12, 6/6 as on disk.
- **Cross-verse drag**: the first 1:2 word bank token onto the first source cell of 1:1.
  The 1:1 cell highlighted (`1|H001`), the release saved, the notice read "Cross-verse
  link saved. Local and Greek Room checks for 1:2 are current.", v.1 became 6/5 and v.2
  14/11 with "↔ 1 linked across verses" on both, the 1:1 cell showed the word with
  "realized in v.2" and a remove control, the 1:2 bank showed the same word greyed with
  "↔ v.1" and 11 words still draggable, and both verse-list glyphs stayed `untouched`. In
  `bridge-workbench.sqlite3`: one row keyed by the two token signatures with state
  `active`, change_log `upsert` (rev 1) + `crossVerseLink` (no revision), one
  `alignment_history` row for 1:1 with operation `crossVerseLink`.
  `alignmentData/gen/1.json`: verse 1 still 0 groups with bottom words / bank 5, verse 2
  still 0 / 12 — untouched.
- **Align Words for 1:2** flagged "11 target words still need a source word" plus "1
  word linked across verses (Bridge-private; completion stays with translationCore)",
  showed the accounted word greyed with "↔", and offered 11 draggable words.
- **Unlink** from the page's remove control on the 1:1 chip: notice "Cross-verse link
  removed.", gaps back to 7/5 and 14/12, 0 rows, events `upsert, crossVerseLink, delete,
  crossVerseUnlink`.

### Deliberately not done

No change to tC `completionState`, `canComplete`, or aligned USFM export. No fifth
alignment status value (see above); `VerseList` and the chapter summary keep showing the
tC work state. No Stage 6B evidence or staleness wiring (#119). No range pre-fill, finding
entry point or multi-select (#118). No golden or threshold change. No Semantic/Passage tab
change.

### Gates

Engine: `tests/alignment/test_alignment_cross_verse.py` **8 passed**;
`tests/persistence/test_workbench_sync.py` + `test_workbench_repository.py` +
`tests/alignment/test_alignment_get_range.py` **99 passed** together (26 s);
`pytest -n auto tests/alignment tests/service tests/persistence tests/project_io -m "not slow"`
**390 passed** (81 s); `pytest -n auto -m "not slow"` **1027 passed** in 2 m 45 s.
Frontend: `npm run check` 0 errors / 0 warnings; `npm run test` 30 files, **402 passed**
(3 new); `npm run build` clean. No Rust change, so `cargo` was not run. Docs bumped in
the same commit: CLAUDE.md (on-disk shape, ladder paragraph), ARCHITECTURE.md §3,
TEAM_ARCHITECTURE.md §3.1 table and §4 sync note.

## 2026-09-16 — #118: cross-verse alignment, slice 3 — range suggestions, open from a finding, multi-select

Frontend only. The page from #116/#117 asked the reviewer to pick the range by hand;
Bridge already knew better in three places, and this slice wires each of them in.

### What was built

- **Range pre-fill from the last Stage 6B run** (`src/lib/crossVerseSuggest.ts`).
  `suggestCrossVerseRange(chapter)` reads `analysisJob.getScopeStatus` for
  `{kind: "CURRENT_CHAPTER", chapter}`, takes the latest job's `LOCATION` stage run id,
  loads the run and its target inventory through the two existing range getters, and
  returns the chapter's verses that `CROSS_VERSE` relationships land in — resolved
  through the target tokens' displayed references, the same route
  `PassageAlignmentMode` takes. It never throws: no completed analysis, no location
  stage, or any failed read means no suggestion. The page loads its default range
  first, then widens it with the suggestion, shows "↔ Range widened with verse(s) …"
  and a "Back to N ±1" control; an explicit range (multi-select or a finding) skips
  the suggestion. The picker stays manual throughout.
- **Open from a finding.** `QaFindingDetail` shows a "Cross-verse alignment ›" button
  for `POSSIBLE_OMISSION` / `POSSIBLE_ADDITION` findings with references and dispatches
  a `crossVerse` event (the component stays presentational); the QA queue's context menu
  (`AlignmentQaMode`) gains a "Cross-verse alignment" item enabled for the same kinds.
  Both go through `versesForReferences`, which splits `"BOOK c:v"` on the last space and
  the first colon so `"PHP 1:2-3"` keeps its bridge string, keeps the first chapter's
  verses only (the page is chapter-scoped), and then `requestCrossVerse(chapter, verses)`
  in `alignmentUi.ts`. `App.svelte` resolves the request: it switches chapter through
  `activateChapter` when the finding is elsewhere, then opens the page on those verses.
- **Ctrl/Shift-click multi-select in `VerseList`.** A new chapter-scoped
  `selectedVerseSet` store (`stores.ts`, reset in `resetBookState` and on every chapter
  activation). Ctrl/Cmd-click toggles a verse in the set, seeded from the active verse;
  Shift-click selects the run from the active verse to the clicked one by index in
  `verseNums`, never by number, so bridges ride along; a plain click clears the set.
  `selectedVerse` stays the single active verse in every case. Selected rows get an
  accent bar, and the toolbar button reads "Cross-verse alignment (N)" and opens the
  page on exactly those verses. One verse is not a range: the set empties below two.
- `CrossVerseAlignmentModal` gained an `initialVerses` prop; `openCrossVerse(verse,
  verses)` carries the set or the references into it via `crossVerseInitialVerses`.

### Verified in the real app (dev build, 1366×768, Tamil IRV Genesis 1)

Same harness as #116/#117 (dev build from the worktree, the #117 sidecars reused since the
engine did not change, WebView2 remote debugging, real pointer sequences with modifier
bits). The app restored straight into Genesis 1.

- **Multi-select.** Plain click on 1:2; Ctrl-click on 1:4 → rows 1:2 and 1:4 marked, active
  verse 1:4, toolbar "Cross-verse alignment (2)"; Shift-click on 1:1 → 1:1–1:4 marked,
  active 1:1, "(4)"; Ctrl-click on 1:3 → toggled off, "(3)". The toolbar button then opened
  the page on **verses 1–4 with chip 3 off** and only 1:1, 1:2 and 1:4 in both columns.
  (A first attempt clicked while the session restore was still finishing and was reset by
  `activateChapter`'s own verse selection; repeated once the editor had settled it behaved
  as above — a timing artefact of the harness, not of the feature.)
- **Open from a finding.** Alignment Review → QA tab reported GEN 1:1 not analyzed, so a
  real "Current passage" analysis was run there (5 stages, 5 possible-omission findings).
  Right-click on a `GEN 1:1 · Possible omission` row: the menu listed "Cross-verse
  alignment" first, enabled, with "Apply proposed fix" disabled and the four decisions
  below it; choosing it opened the page over the review screen on **1:1–1:2** (a single
  reference falls back to anchor ±1). Selecting the row showed the detail pane's
  "Cross-verse alignment ›" button beside "Add note only"; it opened the page the same way.
- **Range suggestion.** A "Current chapter" analysis of GEN 1:1–1:31 was then run in the app
  (completed with warnings, `SEARCH_INCOMPLETE` on most relationships: the 31-verse
  lexical-only search hits its evaluation budget). Opening the page on 1:2 afterwards
  showed the default 1–3 and **no widening**. That is the right answer, not a miss: the
  semantic database holds 425 relationships for that run and **none carries
  `CROSS_VERSE`** (Hebrew→Tamil with no embedding provider locates almost nothing), so
  the suggestion has nothing to add. The widening itself is covered by the Vitest case
  with a mocked run; a real-app widening needs a project whose Stage 6B run has
  cross-verse relationships, which the Tamil PHP golden fixture has and Genesis does not.
- The dev build migrated the real Genesis workbench to v3 again on open; the v2 file was
  restored afterwards as in #117.

### Deliberately not done

No engine change (the sidecars from #117 were reused). No change to `selectedVerse`
semantics, to the QA queue's filters, or to how findings are decided. Cross-chapter
ranges are still out of scope: a finding whose references span chapters opens on the
first chapter's verses. No golden or threshold change; Semantic/Passage tabs untouched.

### Gates

`npm run check` 0 errors / 0 warnings; `npm run test` 33 files, **419 passed** (17 new
across `crossVerseSuggest`, `VerseListMultiSelect`, `QaFindingDetailCrossVerse` and two
new `CrossVerseAlignmentModal` cases); `npm run build` clean. No engine or Rust change,
so pytest and `cargo` were not run.

## 2026-09-16 — #119: cross-verse alignment, slice 4 — Stage 6B reads human cross-verse links as location evidence

The cross-verse half of #54. V11-000a taught Stage 6B to read completed same-verse tC
alignment as `WORD_ALIGNMENT` evidence and left the cross-verse half on #54 because tC
alignment cannot express it. Since #117 a human record of "this source token of verse A
is realized in verse B" exists for the first time, in `alignment_cross_verse_links`; this
slice makes Stage 6B read it. Engine only, no schema change (the links stay in the
workbench DB), no weight or threshold change.

### What was built

- **`word_alignment_evidence.py`**: `alignment_precedents_for_range` now also returns one
  precedent per *active* cross-verse link touching the range (`_cross_verse_precedents`).
  The source end resolves onto the pinned UHB/UGNT pack identity exactly as the same-verse
  path does (`resolve_source_token_id`: exact NFC word + occurrence, Strong's/lemma/morph
  as tie-breakers, no guessing); the target end resolves onto the current revision of its
  *own* verse through `resolve_target_token_id`, using the range's passage text when the
  verse is in range and a single-verse `rebuild_current_passage` when it is not, so the
  text revision is the one Stage 6A uses. Output shape unchanged, so `_score_candidate`
  scores it at the existing 0.65 and a located relationship acquires `CROSS_VERSE` on its
  own (source and target canonical verses differ). Invalid links, links on bridged verses
  and any verse-scoped read problem contribute nothing rather than raising.
  `ALIGNMENT_EVIDENCE_VERSION` → `tc-word-alignment-v3`, so a run fingerprinted before
  links existed is never served as a cache hit.
- **Staleness.** `CrossVerseLinkStore.digest()` (every link row: id, state, revision,
  updated_at) is folded into `alignment_state_digest`, which is both Stage 6B's run
  fingerprint input (`alignment_evidence_digest`) and the key `synchronize_alignment_state`
  memoises its staling on. So a link, unlink or invalidation stales downstream Stage 6B/7/8
  records through the existing book-level `WORD_ALIGNMENT` anchor, in the same session:
  `alignment.crossVerse.link` / `.unlink` now call `synchronize_alignment_state()` before
  returning, and a text edit that invalidates a link already went through the post-commit
  call in `apply_scripture_edit`. No new anchor type, no new dependency table.
- `CrossVerseLinkStore.active_links()` for the projection.

### What the measurement said (recorded, not tuned)

On the test fixture (PHP 1:3 "God", 1:4 "always", one link δεήσει@1:4 → God@1:3, no
embedding provider): without the link the δεήσει unit is **NOT_LOCATED** (top candidate
0.16); with it, every top candidate contains the linked token and carries the
`WORD_ALIGNMENT` component, the best at **0.81**, well over `located_minimum` 0.36. The
outcome is **AMBIGUOUS**, not LOCATED: the split pseudo-span pairing "God" with its
source-verse neighbour "always" also contains the linked token, also earns the component,
and wins `STRUCTURAL_PROXIMITY` for touching the source verse — 0.81 against 0.77 for
"God" alone, inside the 0.07 ambiguity margin. That is candidate generation's tie, not
this evidence's, and resolving it means touching Stage 6B scoring, which is a stop-and-ask;
filed as **#123** and left alone. The test asserts the measured delta (NOT_LOCATED →
credible, linked-token candidates on top, `CROSS_VERSE` when LOCATED) so a later fix to
#123 tightens it rather than breaking it.

### The golden did not move

`tests/semantic/test_semantic_location_stage6b.py::test_irvtam_php_passage_reordering_is_discovered_without_engine_book_rules`
passed by name (4.2 s) and `git status` on `engine/tests/fixtures/` is clean: the golden's
fixture project has no completed alignment and no cross-verse links, so the new component
contributes exactly zero there. Not re-baselined.

### Deliberately not done

No weight, threshold or search-policy change (#123 records the one place that would
help). No new dependency anchor. No semantic-DB schema change. No UI change. The archived
HANDOFF.md still says the cross-verse half is blocked on the embedding-provider direction;
that document is an archive and is left as written — this entry and #54's closing comment
are the correction.

### Gates

Engine: `tests/semantic/test_word_alignment_evidence.py` + `test_word_alignment_invalidation.py`
**36 passed** (53 s; 4 new); the Stage 6B golden test by name **1 passed**;
`pytest -n auto -m "not slow"` **1030 passed** in 2 m 37 s. No frontend or Rust change, so
`npm` and `cargo` were not run.

## 2026-09-17 — docs consolidation: one doc map, one invariants file, `HANDOFF.md` deleted

A board-and-docs audit (issues #124 to #135 filed from it separately) found 33
Markdown files outside the code directories, three of them narrating the same
architecture, four different doc maps that disagreed with each other, and two
files still describing the app at v0.9.6. This entry is the consolidation the
maintainer approved from that audit.

### What moved, verbatim

- **`DEVELOPER_GUIDE.md` §1 "Tech stack and why"** → `ARCHITECTURE.md` §2.1. The
  stack table, the accepted Rust trade-off and the "never integrated directly"
  note are the *why* behind the process boundary §2 already draws; they now sit
  together. The section's "core architectural principle" paragraph was dropped
  as a duplicate of `ARCHITECTURE.md` §1.
- **`DEVELOPER_GUIDE.md` §1's Unicode semantic-comparison invariant** →
  `INVARIANTS.md` §21a, beside the span contract it is the comparison half of.
- **`DEVELOPER_GUIDE.md` §4 "Vendored packages & bundled data"** →
  `ARCHITECTURE.md` §3.1 (it is storage, and the numbers were current), except
  its **"tN/tW are not fabricated"** boundary → `IMPORTS.md`, where the tN/tW
  materialization it governs is described.
- **`DEVELOPER_GUIDE.md` L217-317, the dated Beta 15 handoff** →
  `docs/archive/BETA15_HANDOFF_2026-08-31.md`.
- **`docs/plans/FONT_SUPPORT_PLAN.md`** → `docs/archive/` (self-declared
  implemented 2026-09-08; `docs/plans/` is now empty and gone).

### One doc map

`ARCHITECTURE.md` §9 is now the repository's only doc map, extended to name
`DEVELOPER_SETUP.md`, `DECISIONS.md`, `INVARIANTS.md`,
`passage-aware-semantic-alignment.md` (previously reachable only from the
archived handoff), the release notes and the archive. The three lists that
disagreed with it — `DEVELOPER_GUIDE.md` §7, `TEAM_ARCHITECTURE.md` §12 and
CLAUDE.md's "Working in this repo" — now point at it in one line each.

### `HANDOFF.md` is gone; `INVARIANTS.md` replaces the part that mattered

`docs/archive/HANDOFF.md` was 4,139 lines in 44 numbered sections. Its status
records (§35 to §37.14, §43, §44.x) duplicated `BUILD_LOG.md` — its own archive
banner said so — and its transfer instructions (§40, §41, the "where to pick up"
block) describe handing the project to a second developer, which no longer
applies. Six sections were project knowledge with no other home and are now
`docs/INVARIANTS.md`, **lifted verbatim with their original numbers kept**
because engine code cites them by number:

| Section | What it fixes |
|---|---|
| §20 | Stable token identity |
| §21 + §21a | The Unicode span contract and the comparison invariant |
| §31 | Embeddings are retrieval evidence, never truth |
| §36 | The end-of-Stage-9A limitations list, under a note saying it is a dated snapshot and naming its two now-false bullets |
| §39 | The hard non-negotiable constraints |
| §44.8 | The continuity rules |

The four live citations in code (`correction_verification.py`,
`test_correction_stage9b4.py`, `cross_verse_links.py`,
`workbench_repository.py`) now say `INVARIANTS.md §39` instead of
`HANDOFF.md §39`. The 17 mentions inside `BUILD_LOG.md` are historical log text
and are left exactly as written.

### Stale text corrected, not rewritten

- `README.md`: status `v0.9.6` → `v0.11.0`; the "Current status" narrative,
  which still described Beta 11/14/15 and the 40-candidate validation queue that
  #100 removed, replaced by two paragraphs naming the 0.10.0 database cutover
  and the 0.11.0 cross-verse page, with pointers to the release notes. The
  duplicated developer quick-start and run-locally sections became a pointer to
  `DEVELOPER_SETUP.md`, which already covers both. The licence pointer follows
  the vendoring tables to `ARCHITECTURE.md` §3.1.
- `DEVELOPER_GUIDE.md`: "BUILD_LOG is ~1850 lines" (it is 9,100+) dropped;
  "Schema is **v14**" in the roadmap table now points at `ARCHITECTURE.md` §3
  rather than restating a number that is v16. The two other v14 mentions are
  inside a block explicitly labelled as a 2026-09-11 baseline snapshot and are
  left as the record they are, with a dated note added above them.
- `ALIGNMENT.md`: the "Bridge v0.9.6 provides" version line dropped; a new
  section documents the Bridge-private cross-verse link store (#117) and the
  Stage 6B evidence (#119), which the doc had no mention of.
- `USER_MANUAL.md`: a banner at the top saying it is written against 0.9.6 and
  naming the two shipped things it does not cover, plus a short Cross-verse
  alignment subsection in §6.5. A real rewrite is still owed.
- `QA_TEST_MATRIX.md`: the "Python: 319 tests" line in Automated evidence
  contradicted row A01's current count; it now says the suite is at 1,030 and
  keeps the original list as what that 319-test suite covered.
- `SIMPLIFICATION_AUDIT_2026-09.md`: the sequencing list still said B6 was "not
  yet filed"; it is #110 and shipped in `50c7620`.

### Not done

`BUILD_LOG.md`'s own internal duplication (the undated "current release state"
and "phase roadmap status" sections inside an otherwise chronological log) was
left alone: it is an append-only record and editing its middle is worse than the
duplication. The user manual rewrite is still outstanding. No code behaviour
changed — the four Python edits are comments and one docstring.

### Gates

`tests/correction/test_correction_stage9b4.py` + `tests/alignment/test_alignment_cross_verse.py`
**72 passed** (2 m 3 s) — the two files whose comments changed. A link check over
every Markdown file outside the code directories found no broken relative link
except the pre-existing screenshot placeholders in `USER_MANUAL.md`. No frontend
or Rust change.

---

## 2026-09-17 — Automating cross-verse alignment (#136–#140)

Follows #116–#119, which built the cross-verse page and the link store by hand.
The question asked here was whether Bridge could *find* the gaps itself and
suggest where a paraphrased source word was actually realized.

### What was already there, verified by reading the code

- **Gap detection**, in `_alignment_context`, but only as counts: the identities
  were thrown away and recomputed client-side in `alignmentGroups.ts`.
- **A cross-verse search engine**, Stage 6B, which marks a relationship
  `CROSS_VERSE`.
- **A translation memory**, `alignment_statistics.build_corpus_stats` — UAlign-style
  co-occurrence, translation probability, PMI and an SED phonetic boost over the
  project's own completed alignments. Already cached per book on the service.

### The finding that shaped the design

**Stage 6B cannot find a cross-verse realization unaided in the shipped app.**
Its weights are SEMANTIC_SIMILARITY 0.42 + LEXICAL 0.38 + CONCEPT 0.15 +
MORPHOLOGY 0.12 + STRUCTURAL_PROXIMITY 0.05 + EXACT_SPAN 0.01, against
`LocationSearchPolicy.located_minimum` 0.36. `SemanticEmbeddingProvider.available`
is `False` in the shipped app, so the first is always 0; `_lexical_score` between
an NFC Greek/Hebrew string and a target-language one is 0 for any real
translation pair. The remaining components sum to at most 0.33. The two that can
carry a relationship over the line, HUMAN_PRECEDENT and WORD_ALIGNMENT, are both
0.65 and both mean "a human already said so" (#119) — which a token that is still
a gap does not have.

So an automation built on Stage 6B would have found nothing new while looking
authoritative. The corpus table was used instead, and `cross_verse_proposals.py`'s
docstring records this reasoning so it is not "fixed" later by wiring Stage 6B in.

### Dead code found while doing #137

`_alignment_context` computed the gap sets twice. `bridge_service.py:1570-1580`
built `matched_top_ids` / `matched_bottom_ids` and a `gaps` from them; none of the
three was read again, because #117 had added a link-aware recomputation ~25 lines
below under different names and reassigned `gaps` over the top. Only statement
ordering decided which definition shipped. Removed as part of consolidating both
into `alignment_gaps.py`; recorded on #137 rather than fixed silently.

### Design calls worth keeping

- **`strong_key` folds UGNT's trailing variant digit** ("G23160" == "G2316") using
  the H/G prefix instead of plumbing a `language_id` through, and only on a
  5-digit number — classic Greek numbering stops at four, so truncating a 4-digit
  number would silently produce a different lemma. Found by writing the test, not
  by reading.
- **The ambiguity margin excludes PROXIMITY.** The first version separated two
  otherwise-identical candidates purely on which verse sat nearer and called the
  winner PROPOSED. Distance orders the list; it never settles it.
- **Suggestions are loaded on an explicit click.** Measured: ~1.9 s for the first
  call in a process (Uroman/SED one-time table load) plus the completed-verse
  scan; ~14 ms for 200 candidate pairs in steady state. The naive reading of a
  single timing is entirely the load. A romanization memo was written, measured
  to make no difference, and reverted rather than kept as unearned complexity in
  shared code.
- **Nothing auto-applies.** Accept is an ordinary `alignment.crossVerse.link`;
  `alignment_reliability.AUTO_LINK_THRESHOLD` is the pattern deliberately not
  revived.

### Not done

#140 (persisted dismissals, workbench schema v4) is filed and not built — it is
the only part needing a schema bump, and it carries an open question about
whether a dismissal is per-actor or per-project on a team project. Dismiss is
session-local until then. Neither the layout change nor the suggestions strip has
been seen in the real desktop app; jsdom does not lay out or paint.

### Gates

Frontend: `npm run check` 0 errors/0 warnings, `npm run test` 426 passed (20 in
`CrossVerseAlignmentModal.test.ts`), `npm run build` clean.

Engine: full `pytest -n auto` — **1273 passed** (7 m 55 s).

The first run of it came back 1272 passed / 1 failed, on
`tests/connectors/test_desktop_connectors.py::test_logos_get_state_spawns_the_real_helper_and_returns_environment_safe_result`.
That test passes on its own, passes with `-n auto` over `tests/connectors`
alone, and passed on the clean re-run above; it spawns a real helper process, so
it flakes under the load of a full 20-worker run, and nothing in this work
touches the Logos connector. Recorded rather than dropped, because it is the
second time this class of test has flaked only in parallel — `pyproject.toml`'s
`-n auto` is a local convenience and `ci.yml` runs serial for exactly this
reason.

No Rust change — since #115 a new RPC is an engine handler, an `EngineMethod`
member and a `bridge.*` wrapper.

## 2026-09-17 — The source-word label is a meaning, not a lemma (#145)

Both alignment surfaces printed the token's `lemma` under the Greek or Hebrew
word. The maintainer, reviewing alignments: *"I can't read Greek or Hebrew so
it's not meaningful for me."* That is the whole finding — a lemma is one more
string in a script the reviewer may not read, so the label was decoration
occupying the one place where what the word *means* could go.

### The bundled lexicon holds definitions, not glosses

Checked against the real data rather than assumed. `lexicon_entry_for_strong`
returns Open Scriptures entries whose `meaning` is a full dictionary definition:

| Strong's | `meaning` |
|---|---|
| G746 | `(properly abstract) a commencement, or (concretely) chief (in various applications of order, time, place, or rank)` |
| G2632 | `to judge against, i.e. sentence` |
| G26 | `love, i.e. affection or benevolence; specially (plural) a love-feast` |
| H430 | `gods in the ordinary sense; but specifically used (in the plural thus, especially with the article) of the supreme God; …` |

Dropped verbatim into a 150px cell that is worse than the lemma was. But the
sense a reader needs is reliably at the *front*, ahead of the first `i.e.`, the
first `;`, and outside the parenthetical hedges — which is what `shortGloss`
keeps (42 characters, broken on a comma rather than mid-word). The unabridged
text stays in the hover title and in the lexicon popup, both unchanged in
substance. `usage` was considered for the short label and rejected: it is the
KJV rendering list and carries its own artifacts (`angels, × exceeding, God
(gods) (-dess, -ly), …`).

A Hebrew proclitic is its own morpheme segment — `b:H7225` decodes to a
preposition plus a lexeme — so the *word's* gloss joins the segments in order
("Preposition (in/on/with) + the first, in place, time, order or rank"), not the
lexeme's gloss alone. When the lexicon resolves nothing the label falls back to
the lemma, which is exactly what it used to show; an unresolved Strong's number
must not leave the cell blank.

### Two copies of the same lookup, now one

`AlignmentModal` and `CrossVerseAlignmentModal` each had their own
`loadMeanings`/`sourceTitle` pair resolving the same entries keyed on the same
`strong|morph`. Both moved into `src/lib/lexiconGloss.ts` (`SourceGlossCache`).
The shared version also claims a key before awaiting, so the cross-verse modal —
which reloads on every range change — no longer fires the same lookup twice when
two range changes overlap.

The new CSS has one non-obvious job: the gloss sits inside a container that sets
the Greek/Hebrew face and, for an OT book, `dir=rtl`. Both were right for a
lemma and wrong for an English definition, so `.gloss` re-declares
`font-family: var(--font-ui)` and `direction: ltr`, and clamps to two lines so a
definition cannot stretch an interlinear row taller than the words in it.

### Cross-verse rows stack

Asked for in the same breath, and the reason is space: the source word now sits
*above* its drop cell rather than beside it, the way the single-verse
interlinear already did. A stacked cell needs roughly half the width, so the
`auto-fill` track from #136 drops from 230px to 150px and the same column holds
two cells where it held one.

### Gates

Frontend: `npm run check` 0 errors/0 warnings, `npm run test` **446 passed**
(34 files; 11 new — 10 in `lexiconGloss.test.ts`, one in
`CrossVerseAlignmentModal.test.ts` asserting the cell shows the gloss and the
title still carries the lemma), `npm run build` clean.

Engine untouched, so no pytest run; no Rust change.

### Not seen in the real app

jsdom does not lay out or paint, so the two-line clamp, the narrower 150px
cross-verse track and the stacked rows have been verified as structure only. The
visual check at 1366x768 is still owed.

### Amended same day: renderings, not definitions (#145)

The maintainer looked at the result and asked for the other field: `usage`
under the word, `meaning` in the tooltip. That is the better call and the
reason is worth keeping — under a source word a reviewer wants to know what
the word *gets translated as*, not what it is *defined as*. G2632 reads
"condemn, damn" instead of "to judge against".

`shortUsage` does for the rendering list what `shortGloss` does for the
definition, but the shape is different enough to need its own function: `usage`
is a comma-separated list, so it is truncated by dropping whole renderings at
the 42-character budget rather than by cutting a sentence.

**The KJV marker is spelled two ways in the bundled data.** Found by checking
real entries rather than one: H430 writes `× exceeding`, G2316 writes an ASCII
`X exceeding`. Only stripping `×` would have left the commonest source token in
the New Testament labelled "X exceeding, God, god". Both are stripped, and only
where the concordance puts one — alone at the head of a rendering — so a real
word beginning with X survives. The parenthetical infixes (`chief(-est)`,
`first(-fruits, part, time)`, `(feast of) charity(-ably)`) are removed before
the comma split, because the ones holding an infix list contain commas of their
own.

Two fallbacks, in order, when an entry has no `usage`: a Hebrew proclitic's
`HEBREW_PREFIX_LABELS` text passes through whole — its parenthetical
("Preposition (in/on/with)") is the part that carries the sense, and
`shortGloss` would strip it as a hedge — and otherwise `shortGloss(meaning)`,
which is what the label showed before this amendment.

One consequence to be aware of rather than fix: `usage` is ordered
alphabetically by the KJV concordance, not by frequency, so H430 labels as
"angels, exceeding, God, great, judges" and G2316 as "exceeding, God, god".
The most common rendering is not necessarily first. That is the resource's own
ordering; reordering it would be Bridge inventing a claim the lexicon does not
make.

Gates after the amendment: `npm run check` 0/0, `npm run test` **453 passed**
(34 files), `npm run build` clean. Still not seen in the real desktop app.

## 2026-09-18 — Asking a model for cross-verse links, gated on agreement (#146)

Cross-verse Suggest (#139) learns from this project's own completed alignments,
so on a book nobody has finished it returns `no-completed-alignments` and helps
least where the work is hardest. The maintainer asked for an OpenAI-backed
proposer that could also link automatically on the click.

### The decision this reverses, stated plainly

#23 (extend AI auto-align) was closed 2026-09-16 and #109 — still open —
proposes deleting the existing AI alignment path. `cross_verse_proposals.py` and
`docs/ALIGNMENT.md` both asserted there was no cross-verse auto-apply at any
confidence. The maintainer reversed that on 2026-09-18. All three documents were
corrected in the same commit rather than left contradicting the code, and
CLAUDE.md's stop-and-ask entry now says explicitly that this was decided once,
narrowly, and is not a precedent for widening.

What the reversal does **not** touch, checked rather than assumed: a cross-verse
link is a Bridge-private workbench row. `test_alignment_cross_verse.py:87` already
asserts `alignmentData/` is untouched, unlink is ordinary, and the write is
journalled to an append-only `change_log`. CLAUDE.md's rule is about *project
files*, and this is not one.

### What was already there, found by reading rather than assuming

Most of this feature turned out to be plumbing that already existed:

- `OpenAIResponsesClient` is the only provider and *is* OpenAI-shaped —
  Responses API, strict `json_schema`, `Bearer` auth, stdlib `urllib`, a
  `base_url` override for Azure/Ollama/proxies, DPAPI-wrapped key with an
  `OPENAI_API_KEY` env override. Settings already exposes provider, base URL,
  model and key.
- `alignment.aiPropose`/`aiApplyProposal` are a complete, tested propose→apply
  pair for *in-verse* alignment with no UI caller at all, and `AUTO_LINK_THRESHOLD
  = 0.72` is live on that path — `test_ai_review_auto_align.py` shows AI review
  already auto-applies gap-fill proposals. So auto-apply was never unprecedented
  in Bridge; the "deliberately not revived" note was about the cross-verse
  proposer specifically. Worth knowing before quoting it as a global rule.
- The structured-`unavailable` shape from `start_triage` is the canonical way to
  say "no key", and is what this uses.

### Three rules, and why each is load-bearing

**The model picks from a closed menu.** It gets opaque `S1`/`T1` handles for the
gap tokens only — never the positional ids, which are meaningless outside one
load and would invite a confident reply pointing at the wrong token. Every id in
the reply is resolved back through the handle table; an unknown one raises rather
than being skipped, because a model returning ids Bridge never sent means the
prompt or the provider is wrong and dropping it silently would hide that forever.
The model cannot invent a word; the worst it can do is mis-pick from a list
Bridge wrote. Same discipline as `run_full_review`'s evidence ids.

**`autoLinkable` is agreement, never confidence.** The gate is: the model's pick
and `cross_verse_proposals`' top candidate are the same pair, and it is
uncontested. Two methods scoring by unrelated evidence reaching the same answer.
Thresholding the model's self-reported number instead was considered and rejected
— it would be a second uncalibrated number stacked on the first
(`cross-verse-uncalibrated-v1`), against a provider that has never been measured.
A useful consequence falls out for free: on a cold-start book the corpus proposes
nothing, so **nothing is auto-linkable there**, which is exactly the case with no
corroboration.

**Still one writer.** The proposer writes nothing —
`test_ai_proposing_writes_nothing` byte-compares the chapter JSON, mirroring
#139's own guarantee. The caller applies an agreed proposal through the ordinary
`alignment.crossVerse.link`.

### Details worth recording

- **Provenance needed no schema bump.** `workbench.append_event` takes an
  arbitrary payload dict, so `origin: "ai-auto"` rides on the `change_log` event.
  Deliberately not on the row: the *fact* recorded is identical however it was
  reached, and the append-only event is precisely where "who decided this?"
  belongs. Same move CLAUDE.md records for check-selection provenance.
- **A new RPC needed a Rust change after all.** Since #115 a new method is an
  engine handler plus a TS union member — but `request_timeout_seconds` in
  `sidecar.rs` still keys on the method name, and the default is 30s. A provider
  call at 30s would die mid-request. `alignment.crossVerse.aiPropose` joins the
  260s class; its offline sibling must stay at 30s, and the two differ only by a
  suffix, so there is a `cargo test` asserting both.
- **A separate method, not a flag**, for that reason: one timeout key cannot
  serve an interactive local call and a provider round trip.
- **The gloss needed a fallback.** Language for a lexicon lookup comes from the
  morph code, but a source token can carry `strong` with no `x-morph` — the test
  fixture does, and so does older alignment data. `_gloss_for_strong` falls back
  to the H/G prefix. Found by a test failing for the right reason: without it the
  prompt says `θεοῦ` where it could say `θεοῦ (God)`.
- **Nothing to ask about spends no request.** A range whose verses are fully
  aligned has an empty menu; billing a round trip to be told so is waste.

### Known unmeasured

Per #131 / QA matrix D12, **no Bridge AI path has ever been sent a real request**.
Every test here uses the `ai_transport` fake. The plumbing is proven; prompt
quality, verdict spread and the agreement-gate hit rate are not, and will not be
until a real-key run over a real book is done and recorded. The cost model says
~2.0k input / ~600 output tokens per click, ≈ $0.028 at `gpt-5.6` — also
unverified against a real bill.

Adjacent, filed not fixed: `model_router.estimate_cost` falls back to
`gpt-5.6-sol` (the priciest tier) for any unrecognised model, so the lifetime
usage counter over-reports ~25× on e.g. `gpt-4o-mini`; `ModelRouter` and
`OpenAIResponsesClient.test_connection()` are dead code.

### Gates

Frontend `npm run check` 0/0, `npm run test` **461 passed** (34 files; 8 new in
`CrossVerseAlignmentModal.test.ts`), `npm run build` clean.
Rust `cargo check` and `cargo test` — **10 passed**, including the new timeout
assertion.
Engine `pytest -n auto` — **1287 passed**, 12 of them new in
`tests/alignment/test_cross_verse_ai_proposals.py`.

The first engine run came back 1275 passed / 12 failed, all in
`tests/semantic/test_semantic_mapping_stage3.py` with `Semantic source database
...`. That is the known fresh-worktree gap, not a regression: the 119 MB Stage 3
SQLite, the two sidecar exes and `src-tauri/resources` are git-ignored, so a new
worktree needs them copied in before the gates mean anything. With the database
in place the same 21 tests pass in 0.68 s. `cargo check` fails the same way and
for the same reason (`resource path ... doesn't exist`) until the binaries and
resources are copied — worth knowing before reading either failure as real.

### Not done

No real-provider run (#131 covers it). No chapter-wide job: one click is one
range, deliberately, until the verdict quality above is known. Dismissals are
still session-local (#140). Not seen in the real desktop app — jsdom does not lay
out or paint, so the AI button, the "both agree" / "AI only" badges and the
auto-link notice are verified as structure only.

---

## 2026-09-18 — Align Words popup: the wrap (#72), and what its chapter tally was actually counting (#148, #149)

Two rounds of maintainer feedback on the single-verse Align Words popup
(`AlignmentModal.svelte`), from screenshots of Genesis 1:1. Frontend only, one
component; no engine, no RPC, no schema.

### #72 — the interlinear wraps instead of scrolling sideways

`#72` was open since 2026-09-11 and deliberately parked: deleting `overflow-x`
alone clips a long verse rather than removing the need to scroll, so the issue
asked for a replacement *layout* first, and the maintainer's own comment said to
decide once the cross-verse page (#116) had been used in anger. It has been, and
the answer is the same shape as `dd078d7` (#136) applied to the other surface.

`.interlinear` is now `flex-wrap: wrap` with `overflow-x: hidden`, capped at
`48vh` with its own `overflow-y: auto`. CSS only — no markup, no JS.

- **flex-wrap, not the cross-verse `auto-fill` grid.** That page's unit is a
  side-by-side source+cell *pair*, which needs a consistent track width; here the
  unit is a self-sizing stacked column, so natural widths pack a line tighter and
  no token rule (`min-width: 62px`, the lemma's `max-width`) has to be relaxed.
- **`flex-shrink: 0` stays.** Wrapping is what absorbs the overflow now; columns
  still must not be squeezed. The new test asserts this, because "make it fit" by
  shrinking is the obvious wrong fix.
- **The height cap is about dragging, not tidiness.** The pointer drag has no
  auto-scroll, so a column that cannot be on screen at the same time as the word
  bank is unreachable by drag. Capping the block keeps the bank and the
  Undo/Restore footer in place while the columns scroll.
- **No JS changed because none needed to.** `alignmentDrag.ts` resolves its drop
  target per pointer-move via `elementFromPoint` → `closest('[data-drop-column]')`,
  so it is geometry- and order-independent, and nothing in the component measures
  widths, calls `scrollIntoView`, or walks columns by arrow key.
- The original concern that wrapping "breaks the straight-line source→target
  visual link" does not hold: each source word still sits directly above its own
  target chips. Only the single continuous reading line breaks, into several,
  which is how a printed interlinear reads anyway.

### #148 — what "Chapter: 0 complete 3 partial 28 untouched" was counting

The question that started the second half was literally "what is it tallying?".
Traced rather than guessed: `alignment_status` (`bridge_service.py:1465-1480`)
loops `self.project.verses(chapter)`, skips `front`, and buckets **each verse**
through `_alignment_verse_status` (`:1457-1463`). The four numbers are therefore a
partition of the chapter's verse count — 0 + 3 + 28 = 31 = Genesis 1's 31 verses.
Per `_alignment_work_state` (`tc_project.py:480-491`): `untouched` = no target
word placed in any group; `partial` = some placed but the word bank is non-empty
or a source group has none; `complete` = every target word placed and no empty
source group; `invalid` = the tC `tools/wordAlignment/invalid/` marker.

Worth keeping straight: **`complete` here is "fully aligned", not the
translationCore "completed" marker** — that is the separate `completionState`
field (`bridge_service.py:1602`), which is why a verse can be `complete` in this
tally and still not be human-completed. The numbers were never wrong; the label
"Chapter:" was, because it reads as a count of chapters. It now says
`Chapter 1 — 31 verses: 0 complete · 3 partial · 28 untouched`, each word carrying
a tooltip with the definition above.

The rest of #148 is chrome the same screenshot was carrying:

- The full-width "⚑ Not fully aligned — 2 target words still need a source word.
  Drag or click a word bank item below…" banner became a `2 remaining` chip beside
  the **Target words** heading. Nothing was lost: its instruction is already the
  word bank's own hint one line below it.
- Its second line became a second chip, `↔ 2 linked across verses`, with the full
  Bridge-private sentence as the tooltip.
- The always-open structural-issues block moved behind a red `⚠ n issues` button
  at the end of the status row — hover reveals, click pins, Escape unpins before
  it closes the modal. Anchored `position: absolute`, not a centred overlay like
  `LexiconPopup`: it belongs to the row it came from. The outside-`pointerdown`
  close is the capture-phase one `FindingContextMenu` already uses.
- The title now reads `Genesis (GEN) 1:1`. `bookName`/`bookId` were already on the
  `project` store, so no new RPC — but `bookName` can be a *vernacular* header
  name (import reads USFM `\h`/`\toc2`) and falls back to the raw book id when the
  tC manifest has no `project.name`, so the code is only joined when it adds
  something. Otherwise the title would read "gen (GEN)".
- The source columns got a `Source words` heading, which the target side always had.

### #149 — filed, not fixed

While tracing the above: the flag "⚑ Alignment has structural issues — see
below." fired on `status === "invalid"`, which comes **solely** from the tC
`invalid/` marker file and has nothing to do with `context.issues`. A verse can be
tC-invalid with an empty issues list, or carry several issues while reporting
`partial`. Only the one string this change made stale was fixed (it now reads
"⚑ Marked invalid in translationCore."); the real question — what, if anything,
should surface when a verse has issues but no marker, and whether the engine's
duplicate-granularity issue reporting should be collapsed — is #149.

### Gates

Frontend: `npm run check` 0 errors/0 warnings, `npm run test`, `npm run build`.
New `AlignmentModal.test.ts` — the component had none, because `AlignmentReview`
mocks it out for reaching Tauri and the stores; `CrossVerseAlignmentModal.test.ts`
shows the `vi.hoisted` + `vi.mock("../../api/bridgeClient")` pattern that makes it
renderable.

Engine untouched, so pytest was not run and this records that rather than
implying it passed. No Rust change.

### Not done

Neither change has been seen in the real desktop app. jsdom does not lay out or
paint, so the wrap in particular needs a visual check at 1366x768 — including
that `dir="rtl"` fills each line right-to-left with lines stacking downward, and
that a drop onto a column on the second line works.

## 2026-09-21 — full-app manual QA plan (#150) and SRS baseline (#168)

The release matrix had broad manual rows but no reusable, step-by-step full-app
execution artifact. `FULL_APP_MANUAL_QA_CHECKLIST.md` now supplies one: 241
stable test-case IDs across preparation/smoke and 16 functional categories,
with P0/P1/P2 priority, concrete expected outcomes, evidence/defect handling,
and release sign-off. The matrix links to it without rewriting any historical
PASS or NOT RUN evidence.

GitHub issue #150 is the master run. Issues #151–#167 are native sub-issues for
preparation/smoke and each functional category; every child repeats its own
steps, category checkboxes, evidence instructions, and completion gate. The 18
items are Todo on Project 6's `QA` board view, filtered by `manual_testing`.

`SOFTWARE_REQUIREMENTS_SPECIFICATION.md` establishes an as-built 0.12.0 SRS at
commit `c8fa74d`: 251 unique requirement IDs covering functional behavior, data,
interfaces, quality attributes, business/safety rules, release acceptance, and
future work kept explicitly non-normative. It uses the code-verified schema
constants (semantic v16, workbench v3, workspace v2), not older numbers in
narrative guidance. README and the Architecture document map now link it. Issue
#168 tracks maintainer review/adoption and is Todo on the main project board.

Documentation verification: all SRS IDs are unique, referenced local Markdown
files exist, GitHub's master QA body matches the local checklist, all 17 native
sub-issues and 18 QA board items were read back, and `git diff --check` is clean
apart from Git's existing LF→CRLF notices. No engine, frontend, Rust, frozen, or
installed-app test was run because this work changes documentation and project
tracking only.

## 2026-09-22 — offline Language QA foundation, Tamil first (#169)

User requested a separate automatic offline language QA layer, starting with the
supplied Tamil 56-check publication framework, with a small package footprint and
background execution. Work is on the requested `language-qa` branch. The supplied
framework is preserved in `LANGUAGE_QA_TAMIL_SPECIFICATION.md`; staged scope,
budgets, coverage and limits are in `LANGUAGE_QA_PLAN.md`.

LQA-1 adds two small Python modules and a separate collapsible frontend panel.
Project open/import starts a debounced worker; committed Scripture edits invalidate
its generation and schedule a recheck. The worker reads current chapter JSON,
never preserved original USFM or a live database connection. One worker yields
between verses, bounds input/output, and exits after each pass. Status polling
schedules a refresh every 15 seconds for external edits. SHA-256 chapter hashes
permit reuse without trusting timestamps/size; per-verse hashes and raw code-point
spans accompany stable, occurrence-aware finding IDs. Old-project requests/results
cannot affect a new book. Pause/resume and transaction-recovery blocks are explicit.
Results are disposable session analysis, not persisted decisions or automatic edits.

Detection combines metadata with a bounded script sample. It suggests Tamil when
supported, exposes conflicts/mixed scripts, and does not infer Hindi from
Devanagari. Tamil rules accept decomposed vowels and Grantha conjuncts and flag
invalid dependent signs, repeated words and mixed Tamil/Latin words. Common rules
cover Unicode corruption, private-use/invisible characters, normalization, spacing
and repeated punctuation. Lone-surrogate JSON receives an ASCII-safe diagnostic.
Inline-USFM verses, bad files and resource limits are reported as incomplete;
unsupported grammar, source-fidelity and publication checks never count as passed.

An initial load test exposed worker starvation under continuous foreground polls.
Deferral is now limited to once per half-second, with a regression test. The source
401-verse benchmark completed in 15.297 seconds including deliberate sleeps, with
ping median/max 0.300/2.647 ms. Frozen: 15.328 seconds, ping 0.311/2.252 ms. Both
verified pause/edit/resume, three reused chapters and unchanged Scripture before
the explicit edit. Typical pure checks took about 0.8 ms; 20,000-character stress
checks took 7.6–44.9 ms. These are local observations, not a zero-cost guarantee.

Verification: full engine **1,313 passed** in 20m50s, serial because pytest-xdist was
absent. Final focused engine **31 passed**, including recovery, starvation,
same-timestamp/size external edits and malformed surrogates. Frontend **480 passed**;
final panel **6 passed**; Svelte **0 errors / 0 warnings**; production build passed
with the existing large-chunk notice. Both PyInstaller executables built under
`engine/dist/language-qa/`; new module payload is approximately 16 KB compressed.
No dependency, model, schema, vendor code, semantic golden or Scripture writer was
added. Rust source was unchanged; generic RPC forwarding needed no Rust change.

The standard frozen-pair smoke stopped at an existing version mismatch: package
and Tauri 0.12.0 versus engine constants 0.11.0 (also confirmed at starting HEAD).
Filed **#170** separately; the known #130 import-classification failure was not
reached. The Language-QA-specific frozen benchmark passed independently. Installed
visual acceptance remains **NOT RUN**: no browser automation surface was available.
The temporary preview server and its fixture files were cleaned up. No installed
app was replaced, release published, commit made or branch pushed.

Later stages need approved Tamil spelling/style/terminology/name data and reviewed
morphology/sandhi examples. Source judgments, comprehension, typesetting and human
sign-off remain separate gates. LQA-1 covers technical target-text checks on the
open book, not all 56 publication checks or whole-collection grammar certification.

Final post-hardening source/frozen reruns also passed: 401 verses in 15.328/15.484 s,
ping maxima 0.831/1.321 ms; both reused three unchanged chapters after the explicit
edit. Final compressed module payload: 15,788 bytes. `git diff --check` is clean
apart from the repository's existing LF-to-CRLF notices. The full-suite count above
precedes the last five focused cases; all 31 focused cases passed on the final code.

### 2026-09-22 desktop smoke follow-up — idle rescan loop

The first real desktop check showed a completed Language QA pass returning to
queued/running every 15 seconds. `status()` was scheduling a complete pass whenever
the refresh interval elapsed, even when no chapter had changed. Idle refresh now
compares a bounded `(filename, mtime, size)` chapter signature and schedules work
only when it differs. A worker still hashes every bounded chapter it reads, so an
explicit Bridge edit/invalidation never reuses changed content merely because an
editor preserved metadata. Focused regressions pin both no idle restart and normal
external-edit discovery. This follow-up came directly from installed-app evidence.
The focused Language QA suite is now **33 passed**.

### 2026-09-22 desktop smoke follow-up — pause/resume restarted the whole book

Retesting the rebuilt sidecars (with the idle-rescan fix above) surfaced a second,
related defect: the installed-app checklist item "resume continues from where
paused" failed — resume visibly restarted the whole book (a 150-chapter Psalms-like
project, `completed · 19/150 chapters`) instead of continuing. Root cause was the
same class of bug as the idle-rescan one, in a different call path: `pause()` called
the shared `_schedule()` helper unconditionally on both pause and resume, which
always wipes `findings`/`completedChapters`/`totalChapters` back to empty/zero and
starts a fresh pass — even when nothing on disk changed while paused. It is worse
than a cosmetic reset for a book where most/every chapter carries a per-verse
limitation (here, inline USFM markers in the target JSON): `_scan()` deliberately
never caches a chapter that produced any limitation (so a later pass can retry it if
the book-wide finding budget frees up), so an unconditional reschedule redoes the
exact same, unchanged per-verse work for the exact same result on every resume.

Fix: `pause()` now (1) keeps the last completed summary on screen while paused,
only flipping `state` to `"paused"`, instead of blanking it, and (2) on resume,
reuses the same bounded chapter-signature check the idle-rescan fix introduced —
if nothing changed while paused, it restores `"completed"` directly, synchronously,
with no worker thread and no rescan. An edit made while paused (verified with a
direct external-file write, not just `invalidate()`) still produces a signature
mismatch and rescans normally on resume. Two new focused regressions cover both
paths. The focused Language QA suite is now **35 passed**.

### 2026-09-22 LQA-2, first slice: whole-Bible wordlist audit (item 49)

First LQA-2 delivery. Scope was narrowed deliberately across two rounds of
questions with the maintainer: most of LQA-2 (items 1-10 grammar/morphology,
20-21/50 termbase) needs either a real Tamil dictionary/morphological analyzer or
expert-labelled examples neither of which exist yet, so this slice covers only
the part of item 49 that's pure corpus statistics needing no external data —
same word two ways, one-character variants, pulli presence/absence, and a rare
form paired with a much more common near-duplicate. Per the LQA-2 scope note's
own guardrail ("Rare words or spelling similarity alone are never errors"), the
rule requires *both* rarity and similarity together; neither alone is ever a
finding. Compound joined/split and name/theological-term variants are explicitly
**not** covered — item 49 is not fully closed by this slice.

`language_qa.py` gained `word_occurrences()` (reuses the existing `WORD`
tokenizer, does not add a second one) and `wordlist_findings()` (pure function:
counts + first-occurrence locations in, findings out). Near-duplicate matching
uses SymSpell-style deletion-neighbor buckets rather than naive length bucketing
(which is genuinely quadratic on Tamil's clustered word-length distribution) —
this doubles as the pulli-variant case for free, since inserting/deleting one
pulli is just an ordinary edit-distance-1 case under this scheme; an early
version of this code only handled same-length substitutions and silently never
caught the insertion/deletion case including the pulli example itself, caught by
the first real test run, not by review — fixed by also checking each word's own
deletion-neighbors against the vocabulary directly.

`language_qa_jobs.py`'s `_scan()` now accumulates a book-wide word-frequency
table and first-occurrence map alongside the existing per-chapter cache (a
cache-hit chapter contributes without re-tokenizing); a verse that produced any
limitation (inline USFM, corrupted, oversize) is excluded from word-counting so
it can't manufacture spurious rare forms; a truncated pass (either book-finding
or diagnostic-limit cap, or the pre-existing MAX_CHAPTERS cap) skips the audit
entirely with an explicit limitation, since a word genuinely common in an unread
tail chapter would otherwise look artificially rare. The finding id is
`sha1(book:tamil.wordlist-variant:rare:common)` — word-pair only, no
chapter/verse — so it survives an unrelated later edit that moves or removes the
rare word's anchor occurrence elsewhere in the book; a focused regression pins
this by moving the anchor and checking the id is unchanged.

**Constants are an unvalidated starting point, not a calibrated default** —
mirrored from `bridge_service.py`'s existing `_CONSISTENCY_MIN_OCCURRENCES`/
`_MIN_RENDERINGS`/`_DOMINANCE_THRESHOLD` precedent for the same class of
whole-book statistical check. This environment has no real, previously-reviewed
Tamil Bible book to run a false-positive calibration against (only small
synthetic fixtures exist in the test suite) — that calibration is still
outstanding and should happen against a real project before this rule is
trusted at scale. What *was* measured: a synthetic 20,000-distinct-word
vocabulary (the `MAX_WORDLIST_TERMS` cap) runs in ~0.25s; realistic single-book
sizes (2,000-5,000 distinct words, closer to what a real book's vocabulary is
likely to be) run in 20-50ms. That whole pass runs as one atomic call at the end
of `_scan()` with no internal yield point, so a pause/cancel request arriving
during it can wait up to that ~0.25s worst case before taking effect — a known,
minor responsiveness gap, not fixed here since real single-book sizes are far
below the cap.

9 new focused tests (5 pure-function unit tests on `wordlist_findings`, 4
integration tests through `LanguageQaManager` covering end-to-end detection,
limitation-verse exclusion, truncation skip, and id stability across a moved
anchor). Focused Language QA suite: 35 -> 44. Full engine suite reran clean:
1111 passed, 1 skipped, 0 failed (up from 1102 before this change, matching the
9 new tests). No frontend changes needed or made — `LanguageQaPanel.svelte`
already renders any finding generically by iterating `status.findings`, with no
rule-id-specific handling, confirmed by reading the render loop directly.

Grammar checks (item 3, சந்தி வல்லினம் doubling) remain blocked pending the
maintainer's labelled examples and a precise rule statement, per the approved
plan; not started in this slice.

### 2026-09-22 LQA-2 Part B1: closed-class வல்லினம் மிகுதல் after அந்த/இந்த/எந்த

First real grammar rule. The maintainer supplied a precise, bounded rule
statement rather than general sandhi: demonstrative அந்த/இந்த/எந்த immediately
followed (pure whitespace only) by a word beginning க/ச/த/ப must carry the
matching linking consonant + pulli at the end of the demonstrative, e.g.
அந்த + காகம் → அந்தக் காகம். New rule id `tamil.vallinam-missing`, merged into
`scan_text`'s existing previous/current word walk (the same one
`tamil.repeated-word` already does) rather than a third pass over the text.

Every abstention the maintainer listed falls out of the existing design with no
extra code: "linking consonant already present" is excluded because the
previous token would then literally be `"அந்தக்"`, not `"அந்த"` — the exact-match
check itself is the abstention. Punctuation/other boundaries reuse the same
pure-whitespace gap `tamil.repeated-word` already requires. Verse/chapter
boundaries and USFM markup are excluded by `scan_text`'s existing one-verse,
early-bail-out signature — no new code needed for either. "Merely similar to"
அந்த/இந்த/எந்த (e.g. a fused compound like அந்தநாள்) is excluded by exact-string
membership, not a prefix match. அது/இது/எது are simply never in the trigger
set — confirmed by an explicit regression, per the maintainer's strongest
warning not to let this generalize.

Span covers the whole two-word phrase (not just one token, unlike
`tamil.repeated-word`'s convention) so the evidence line in the panel shows the
actual boundary. Severity `medium` (identifiable pattern needing human
judgement, not a certain structural defect); message is hedged
("Possible... Verify before editing") matching the maintainer's own wording.
`RULE_VERSION` bumped `language-qa-1` -> `language-qa-2` (confirmed
informational only — not part of the in-memory chapter cache key, and the one
hardcoded `"language-qa-1"` string in the frontend is an arbitrary mock-fixture
literal in `LanguageQaPanel.test.ts`, not a comparison against this constant,
so the bump needs no frontend change).

23 new focused tests, directly against every example the maintainer gave
(6 incorrect forms flagged, 6 correct forms not flagged) plus non-trigger
initials, punctuation boundaries, exact-token matching (including a fused
compound), and the explicit அது/இது/எது non-generalization regression. Focused
Language QA suite: 44 -> 67. Full engine suite reran clean: 1134 passed, 1
skipped, 0 failed (up from 1111, matching the 23 new tests).

Scope: this is Part B1 only, one closed-class environment. Accusative -ஐ,
dative -க்கு, compounds, adjectival compounds, verbal participles,
குற்றியலுகரம், and general noun+noun joining are explicitly not covered and
would each need their own rule and fixtures, per the maintainer's own scope
note.

### 2026-09-22 Part B1 installed desktop acceptance -- confirmed PASS

Rebuilt sidecars and the dev app at `358ab9f` (clean tree at that commit) and
ran a guided installed-app acceptance, the same discipline used for the two
earlier scheduling fixes. Controlled test project (`vallinam-test`, book
Philippians, one chapter, ten verses) built and imported through Bridge's
normal Import-a-file flow rather than modifying any real translation
project -- import always copies the source, so this carried zero risk to
existing project data. Verses 1/3/5/7 covered all four consonant classes with
an incorrect boundary; 2/4/6/8 were the same phrases already correctly
linked; 9 used a non-trigger initial (வ); 10 put a comma at the boundary.

Result: all four incorrect boundaries flagged as `tamil.vallinam-missing`
with exactly the expected wording, raw-text span, verse anchor and `medium`
severity -- message text matched the code's output character-for-character
(e.g. `"அந்த க..." normally takes "அந்தக் க..."`). All six negative cases
(correct forms, non-trigger initial, punctuation boundary) produced zero
findings -- "4 review candidates" total, matching exactly the four verses
meant to flag. Verse text unchanged in the editor, confirming no auto-edit.
Findings survived verse navigation, Pause checks (stayed visible, state
flipped to `paused` without blanking), Resume checks (returned directly to
`completed`, no restart flash), and a 15+ second idle wait (stayed
`completed`) -- exercising both earlier scheduling fixes together with B1 and
finding no regression. One unrelated observation noted and dismissed: a
`எந்த¹,²` superscript in the verse-7 editor view, traced to the separate
tN/tW/Alignment check system's own "15 open findings" (a standard import-time
artifact), not Language QA.

No code changes made during this verification -- none were needed. Per the
maintainer's sequencing: LQA-2 sandhi work pauses here. Next track is
termbase/proper-name infrastructure (framework items 20-21/50), to be scoped
separately once requested. If sandhi work resumes later, the next rule should
again be narrowly bounded and validated before implementation, the same way
B1 was.

### 2026-09-22 LQA-2 termbase v1: explicit exact-match deprecated-form consistency (items 20-21/50)

First implementation slice of the termbase track, following the architecture
document accepted this session (four explicit maintainer decisions overrode
two of that document's own recommendations):

- **Pipeline**: routed through the existing, already-disposable Language QA
  flow (`language_qa.py`/`language_qa_jobs.py`), not a new Greek-Room-style
  adapter with its own persistence/review lifecycle as the architecture doc
  had recommended. No second findings model, no parallel job manager.
- **Storage**: the termbase itself (the curated data, as opposed to the
  findings it produces) is durable, DB-backed — finishing the orphaned
  `record_terminology_rule`/`terminology_rules()` write path in
  `tc_project.py` (`human_decisions`, `kind='terminology'`), which already
  existed with almost the right shape but zero callers anywhere. Extended,
  not replaced: added `category` (person_name/place_name/key_term/other),
  `provenance` (human/imported), a real `status` enum
  (approved/provisional/imported, replacing a single hardcoded literal), and
  validation (concept id required, at least one rendering list non-empty,
  enum values checked, book-scope-only for now). Kept the existing
  `approvedRenderings`/`allowedAlternatives`/`rejectedRenderings` field names
  unchanged — `analytics.py`'s `translation_words_book_analytics()` is an
  *active* reader of `approvedRenderings` specifically, and renaming it for
  cosmetic clarity would have silently broken that consumer for no v1
  benefit. `schemaVersion` bumped 1->2 inside the payload only; no SQL
  migration, `WORKBENCH_SCHEMA_VERSION` stays at 3 — the payload column is
  schema-less JSON and there was no production data anywhere to migrate
  (zero prior callers, confirmed).

**Authority, not frequency** (the maintainer's own framing): only entries
with `status == "approved"` are ever matchable. A `provisional`/`imported`
entry's `rejectedRenderings` are inert until something explicitly promotes
them — including a pre-existing payload from before this change that has no
`status` key at all, which degrades to "not approved" (the safe default),
never silently authoritative. Automatic discovery/promotion is not built in
this slice; `record_terminology_rule` represents explicit human/API
curation and defaults to `approved` for that reason alone.

**New domain module** `engine/tc_ai_bridge/terminology.py` — pure matching
logic, no I/O, no orchestration, per the maintainer's explicit "keep
terminology/domain logic separate from orchestration" instruction.
`TermIndex` builds a phrase->term lookup from raw `terminology_rules()` rows
(sorted by `conceptId` first, so two entries that happen to register the
same phrase resolve deterministically rather than by loader iteration-order
luck); `find_deprecated_forms()` does word/phrase-boundary-aware exact
matching (multi-word phrases supported, whitespace-only gaps, same
discipline as `tamil.vallinam-missing`'s two-token boundary check) — never a
naive substring search. `language_qa.py` gained one small, behavior-preserving
refactor: `add()`'s inline id-hashing formula was extracted to a top-level
`stable_finding_id()` so the new rule can mint ids identically without
duplicating the formula; full suite reran green immediately after, confirming
no behavior change from the extraction alone.

**Integration** (`language_qa_jobs.py`'s `_scan()`): the termbase is loaded
fresh every scan pass (a single lightweight read, not worth caching across
passes) via a loader reference captured at `bind()` time
(`getattr(project, "terminology_rules", None)`) — the `SimpleNamespace` test
fixture never has to provide one unless a test opts in, which is what makes
"no termbase installed" degrade to zero findings with no code path change.
A loader exception is caught and degrades to an empty termbase plus a
"Terminology unavailable" limitation, never a crash. New rule
`terminology.deprecated-form`, severity `high` (a curated fact, not a
heuristic — deliberately distinct from `tamil.wordlist-variant`'s `low`
corpus-suspicion tier, per the maintainer's explicit instruction to keep
those two confidence classes separate), hedged message wording ("is marked
deprecated... Verify this occurrence" — never an automatic-correction
command). A curated term changing must invalidate every cached chapter's
findings, not just whatever chapter's edit triggered the pass — the
per-chapter cache key gained a third component, a hash of the raw termbase,
alongside the existing content-digest and language-pack — proven by a
dedicated regression that edits chapter 2 only and confirms chapter 1's
stale-but-uncached finding still appears.

**Tests**: 26 new, `engine/tests/service/test_terminology.py` — 10 pure
`TermIndex`/`find_deprecated_forms` unit tests (approved-only authority,
malformed/overlong-phrase defensive skipping, single- and multi-word
matching, whitespace-only boundary, raw-span-vs-normalized-comparison,
deterministic conflict resolution, pre-existing-payload backward
compatibility), 5 real `TranslationCoreProject` storage round-trip tests
(record/read, restart/reopen, upsert-by-concept-id, empty-entry and
unknown-enum validation), 8 `LanguageQaManager` integration tests (clean on
preferred/allowed forms, stable id across an unrelated rescan, finding
clears on edit and on verse removal, no-termbase and malformed-loader
graceful degradation, the cross-chapter cache-invalidation regression above),
1 full real-project-to-manager end-to-end test. Existing suites unaffected:
`test_language_qa.py` (67) and the full engine suite both reran green.
Full engine suite: **1160 passed, 1 skipped** (up from 1134, matching the 26
new tests exactly).

**Performance** (synthetic, no real Tamil termbase or reviewed book
available in this environment, same caveat as item 49's): 300 concepts /
602 rejected forms against 30,000 generated verses (a generous whole-Bible
upper bound) — 0.048 ms/verse, 1.45s total. The benchmark's "3204 incidental
matches" is a synthetic-data artifact (both the termbase and verse text were
generated by sampling the same small random-letter alphabet, producing
coincidental collisions no real termbase against real Scripture would show)
— not a false-positive-rate measurement, and not claimed as one.

**Deferred, not built**: fuzzy/near-spelling suggestions, automatic
variant/rendering promotion, general spelling correction, inferred preferred
forms, full proper-name discovery, theological semantic inference, Tamil
morphological parsing (case-suffix/sandhi-aware matching), whole-project/
whole-Bible scope, any curation UI (this is backend/storage/domain only, by
design), cross-entry write-time conflict validation (conflicts resolve
deterministically at match time instead, per the dedicated regression — not
rejected at write time).

Desktop acceptance **not yet run** — this entry documents source-level
verification only; the maintainer will test the actual build directly per
`docs/QA_TEST_MATRIX.md`'s A45 row before this is called accepted.

### 2026-09-22 termbase v1 installed desktop acceptance -- confirmed PASS

Rebuilt sidecars at the two commits above and ran a guided installed-app
check reusing the existing `vallinam-test` project from Part B1's
acceptance run rather than building a new one -- this slice has no curation
UI yet, so seeding was one `record_terminology_rule` call against the real
project's workbench DB (concept `god`, preferred `இறைவன்`, rejected
`கடவுள்`), then a single verse edited through Bridge's own editor (verse 9,
previously a Part B1 non-trigger case, its job already verified) to contain
the deprecated form.

Result: `PHP 1:9` flagged as `terminology.deprecated-form`, severity `high`,
evidence `கடவுள்`, message text matching the code's output exactly --
`"கடவுள்" is marked deprecated for god. Preferred form: இறைவன். Prefer
இறைவன். Verify this occurrence.` Findings count went 4 -> 5 (the four B1
வல்லினம் findings, unaffected, plus this one) and back to 4 the moment the
verse was corrected to `இறைவன் இருக்கிறார்.` -- confirming the finding
clears on a genuine fix, coexists cleanly with an unrelated rule already in
the same panel, and Scripture itself was never auto-edited (the verse text
in the editor was exactly what was typed, nothing rewritten by the check).

No code changes made during this verification -- none were needed. This
closes A45's "desktop acceptance NOT YET RUN" note in
`docs/QA_TEST_MATRIX.md`.

### 2026-09-22 termbase v2: inline double-underline + right-click suggest/edit/ignore

Backend and frontend for the UI slice the maintainer asked for next,
explicitly scoped to `terminology.deprecated-form` only (not வல்லினம்/
wordlist-variant) and built almost entirely out of existing machinery --
the architecture-discovery pass before implementation found a full
"right-click flagged span -> apply a suggested replacement -> durably
recorded" flow already shipped for other finding types, plus a generic,
finding-type-agnostic context menu and a generic, opaque-string-keyed
decision store. The one real gap: nothing in Language QA's scan loop
consulted any decision store, so recording an "ignore" would have done
nothing -- the same finding would have reappeared on the very next pass.

**Backend** (`terminology.py`, `language_qa_jobs.py`): match results now
carry `suggestedReplacement` (the term's first `approvedRendering`, or
`null` when only rejected forms are recorded -- never invented).
`LanguageQaManager` gained a second loader mirroring the termbase one
exactly, `project_qa_decisions()` (already existed, already the same
`kind='qa'` bucket `verse.decide` writes into -- no new backend endpoint).
Suppression is scoped narrowly: only the terminology-matching block checks
decisions, nowhere else in `_scan()` -- not a general Language QA decision
framework. The per-chapter cache key gained a fourth component (a hash of
decisions, alongside the existing termbase-version hash) so a review
decision invalidates every cached chapter's findings the same way an edited
chapter or a changed term does, proven by a dedicated cross-chapter
regression mirroring the termbase-version one from v1. Occurrence
numbering for stable ids still advances even for a suppressed match, so a
later undecided occurrence of the same word in the same verse never shifts
onto an unstable id once an earlier one is ignored -- pinned by a dedicated
test.

**Frontend**: `buildSegments()` gained a fourth parameter
(`languageQaFindings`), mapped into its span model the same way
`nativeChecks`/`aiReviews` already are -- never cast into a fake `QaFinding`,
keeping Language QA's disposable data model separate from `QaFinding`'s
persistent one at the data layer, unified only at the render call site. New
`.m-term` mark (double `border-bottom-style`, its own `--term` colour,
distinct from the four existing Greek Room/tN/tW/alignment sources -- this
is a different confidence class, a curated fact rather than an engine's
suspicion). A new shared store, `languageQaFindingsByVerse`, is the one
piece that didn't already exist: `LanguageQaPanel.svelte` was the only
Language QA poller and kept its data entirely local, and -- a real gap
found during design, not assumed -- it only ever fetched real finding
objects when the panel was manually expanded (`limit=0` while collapsed).
Fixed by requesting a real page (100) even collapsed, so inline decoration
works without the reviewer ever opening the side panel; the panel's own
50-per-page pagination text is untouched when expanded. A third
`FindingContextMenu` instance offers `Use "<preferred>"` (omitted, not
disabled, when there's no suggestion), `Edit`, `Ignore` -- reusing the
existing menu component verbatim. `Use` calls a new
`applyLanguageQaSuggestedFix()` (verseEditor.ts), the same splice/save/
re-check sequence as the existing `applySuggestedFindingFix`, adapted for
`start`/`end` field names, calling `bridge.decideVerse(..., "accepted")`
directly afterward since Language QA has no `onSaved`-hook equivalent of
its own. `Ignore` calls `bridge.decideVerse(..., "ignored")` and removes
the finding from the store optimistically; the next real scan pass
independently confirms the suppression server-side.

**Tests**: 9 new backend (`test_terminology.py`, 26 -> 35): ignored/accepted
decisions suppress a finding, a decision on one occurrence never suppresses
another in the same verse, no-decisions-loader shows everything, a
malformed decisions loader degrades gracefully (never a crash), a decision
change invalidates an unrelated cached chapter, `suggestedReplacement`
present/absent. Full engine suite: **1169 passed, 1 skipped** (up from
1160, matching exactly). 7 new frontend (Vitest, 480 -> 487): the store
populates from only `terminology.deprecated-form` entries grouped by verse
key; `buildSegments` produces the `.m-term` mark for a terminology finding
and nothing for any other Language QA rule; `applyLanguageQaSuggestedFix`'s
edit/re-check/decide sequence, its staleness guard, its no-suggestion
refusal, and that a decision-recording failure doesn't turn an already-
successful text fix into a reported failure. One existing test updated
(`LanguageQaPanel.test.ts`) to expect the new collapsed-state fetch
behaviour rather than the old count-only one -- a deliberate behaviour
change, not a regression fix. `npm run check` 0/0, `npm run build` clean
(pre-existing large-chunk notice only).

**Performance**: synthetic only, same caveat as every Language QA
performance number so far -- 5,000 decided findings hashed 50 times (a
generous whole-book, long-session estimate): 4.1 ms/pass, negligible
against a full scan.

Desktop acceptance not yet run -- awaiting the maintainer testing the
actual build, same discipline as every prior slice this session.

### 2026-09-23 termbase v2: two more bugs from the same desktop retest, both fixed

The retest of `3dd074a` (the accepted-decision fix) surfaced two further,
real bugs in the same feature, not hypothetical:

**"Ignore" never actually took effect.** Recording an "ignored" decision has
no text change for Language QA to notice on its own the way an edit does --
`bridge_service.py`'s `decide_verse()` only ever wrote the decision and
updated the progress rollup; nothing told `LanguageQaManager` to invalidate
and rescan. The frontend's optimistic local removal (from `29c17a7`) did
work, but the panel's very next poll (2-5s later) refetched the *old,
unssuppressed* summary, since Language QA truly never rescanned, and
silently undid the optimistic removal -- so the double underline reliably
came back, indefinitely, not as a transient flicker. Fixed by having
`decide_verse()` also call `self._language_qa.invalidate(chapter)`,
mirroring exactly what `edit_verse()` already does. Unconditional, same as
`edit_verse` -- `invalidate()` is a no-op when Language QA isn't bound, and
the manager's own debounce coalesces a burst of decisions into one rescan.
New real-dispatcher test (`test_verse_decide_ignored_actually_takes_effect_through_the_real_dispatcher`)
proves the full RPC path end to end: record a term, get flagged, decide
"ignored" through `verse.decide`, confirm the next scan no longer shows it
-- not just that the backend function was called.

**A stale double-underline briefly appeared on the *fixed* word after
"Use".** `edit_verse()` already invalidates Language QA (unlike decide, this
path was never broken), so the fix eventually self-corrected -- but
`languageQaFindingsByVerse` still held the pre-edit finding with its
now-stale offsets until the next poll caught up, and rendering that stale
span against the *new* verse text put the mark on whatever text happened to
fall in the old range -- often overlapping the just-applied preferred word.
Fixed on the frontend: after a successful "Use", the whole verse's entry in
`languageQaFindingsByVerse` is cleared immediately (not just the one
finding id -- every offset in that verse is suspect once the text changes),
the same way the existing `QaFinding` accept-flow replaces
`findingsByVerse` wholesale after a recheck rather than waiting on a poll.

Both fixes are small and were caught by actually driving the feature in the
installed app, not by the test suite -- worth naming plainly, since neither
gap would have been obvious from source review alone. Full engine suite
reran clean. Same discipline as every fix this session: commit once
verified, desktop-retest before calling it done.

### 2026-09-23 "not getting the double underline on the new verse" -- investigated, no code defect

A third desktop report on the same retest ("i am not getting the double
underline on the new verse" after ignoring verse 9's occurrence, then editing
verse 10 to introduce a fresh one) looked at first like a third real bug in
the same run. Two new regression tests were written to reproduce the exact
sequence and both passed cleanly: `test_verse_edit_after_an_ignore_still_detects_a_new_occurrence_elsewhere`
(real `BridgeEngine` dispatcher, ignore one verse then edit a different one
in the same chapter) and a matching `LanguageQaPanel.test.ts` case simulating
two consecutive polls. Full engine suite (1385 passed) and frontend gate all
green with both added -- committed as `7ed01e9` regardless, since they're
real coverage of a sequence that was previously untested.

The maintainer retested with a longer wait and the underline still never
appeared, even on a freshly typed, never-touched verse (verse 8) -- ruling
out both of the leading hypotheses (a logic bug in the ignore-then-edit path,
and simple poll latency). Direct inspection of the live project's
`bridge-workbench.sqlite3` (`human_decisions` table) showed the real cause:
**zero rows, zero `change_log` history for that table, ever**, in this
project instance. The `vallinam-test` project had been freshly reimported
earlier that session (created 04:32:52 the same day), and the termbase rule
(concept `god`, preferred `இறைவன்`, rejected `கடவுள்`) that made every earlier
round of testing work was never re-seeded into the new instance -- because,
per the 2026-09-22 entry above, there is still no UI to add one; it has
always been a one-off `record_terminology_rule()` script call. With zero
termbase rows, no text was ever going to be flagged, on any verse, ignored
or not -- which is exactly what was observed once the actual data was
checked instead of the code.

Reseeded the identical rule directly against the live project via the real
`TranslationCoreProject.record_terminology_rule()` API (SQLite WAL mode
makes this safe with the app still running against the same file). The
running `LanguageQaManager` doesn't notice a termbase change on its own --
its idle-refresh check only watches chapter file mtimes/sizes, not termbase
state -- so a project reopen (fresh `bind()`, full rescan) was needed to
pick it up. After reopening: all three verses (8, 9, 10) showed the double
underline, panel count went 4 -> 7 (4 வல்லினம் + 3 terminology), matching
expectations exactly.

No code changes were needed for the actual reported symptom -- the pipeline,
cache invalidation, and decision suppression all check out. Filed #171
separately for the real underlying gap this surfaced: no UI (and no
"termbase is empty" signal anywhere) makes an empty termbase indistinguishable
from "checked, nothing found," which is what made a data-seeding gap look
like a live regression. Worth naming as its own lesson: two passing,
faithful regression tests were necessary but not sufficient here -- they
correctly proved the *code* had no defect, but only inspecting the actual
live project data surfaced what was really different about the failing
case.

### 2026-09-23 #171: minimal termbase curation UI -- add a rule without a script

The gap filed as #171 (above) got fixed the same day it was found. Two new
thin RPC methods, `terminology.list`/`terminology.record`
(`bridge_service.py`), wrap the already-built and already-tested
`tc_project.py` storage API (`terminology_rules()`/`record_terminology_rule()`
from termbase v1) with the same `_require_project()` gate and
`ProjectError` -> `project_error` handling every other book-scoped method
already has -- no new error-handling pattern, no schema change.

Frontend: a new "Terminology" pane in `SettingsModal.svelte`, placed there
rather than as a new top-level workspace (the maintainer's call, explicitly
revisiting the original termbase v1 Decision 3 for this minimal first slice
only -- Settings already hosts project-scoped content this way, via the
existing "Resources & licenses" pane). Lists existing rules read-only
(concept id, joined preferred/rejected renderings) and a 3-field add form
(concept id, preferred renderings, rejected renderings -- the latter two as
comma-separated text, split client-side into the `string[]` the backend
expects). Deliberately does not expose edit, delete, `allowedAlternatives`,
`category`, `sourceLemma`, `strong`, `note`, or `status` -- those all stay at
their existing defaults; the backend already supports them if a later slice
needs to expose them. Rules load lazily (only once the pane is opened, and
only when `$project` is set, mirroring the Resources pane's own
`{#if $project}` gate) rather than eagerly on every Settings open, since
they're book-scoped and the RPC would otherwise reject with `project_error`
on the common "Settings opened before any project" path.

Tests: 6 new backend dispatcher-level tests (`test_terminology.py`,
37 -> 43) covering both methods with no project open, list-when-empty,
record-then-list round trip, both validation failures (missing concept id,
no renderings at all), and -- directly reproducing what #169's incident
actually needed -- a rule added through `terminology.record` being picked up
by the very next `LanguageQaManager` scan pass with no script involved. Full
engine suite 1391 passed / 1 skipped (up from 1385). 6 new frontend tests
(`SettingsModal.test.ts`, 9 -> 15): empty-project message instead of a failed
fetch, rendering existing rules, the empty-termbase message, adding a rule
with comma-split renderings and the list refreshing from the response value
(no second round trip), and the RPC's own validation error surfacing in the
pane rather than being swallowed. Full Vitest suite 493 passed (up from 488).
`npm run check` 0/0, `npm run build` clean (pre-existing large-chunk notice
only).

Desktop acceptance not yet run -- awaiting the maintainer testing the actual
build, same discipline as every prior slice this session.

### 2026-09-23 #171 desktop retest: terminology.record never told Language QA to rescan

First desktop retest of A47 found a real bug immediately: added a "water"
rule (preferred நீர், rejected தண்ணீர்) through the new Terminology pane,
confirmed it appeared in the list, but the pre-existing occurrences of
தண்ணீர் at verses 5-6 (already on screen from Part B1's fixture) never grew
a double underline, and the panel's total stayed unchanged.

Root cause, confirmed the same way as the earlier `decide_verse` bug
(2026-09-23, above): `terminology_record()` wrote the rule through the real
storage API but never invalidated `LanguageQaManager`. Unlike an edit, a
termbase-only change touches no chapter file, so nothing else notices --
the idle-refresh check only watches chapter file mtimes/sizes
(`_chapter_signature`), never termbase state. The rule sits correctly
persisted and correctly readable, just invisible to the running scan until
something unrelated (an edit, a decision) happens to trigger a fresh pass.

The test written for exactly this scenario
(`test_terminology_record_through_the_dispatcher_is_picked_up_by_the_next_language_qa_scan`)
had passed anyway -- a real process gap, not a false negative caught later.
It called `terminology.record` immediately after `project.open`, before
that open's own initial scan had settled; a fast test process let the
initial pass happen to run *after* the record call and pick up the rule by
luck of thread timing, never proving the RPC itself invalidated anything.
Rewritten to `wait()` for the initial scan to fully complete first --
matching how a translator actually uses this (open a project, let it settle
to "completed", *then* add a rule) -- and it correctly failed against the
unfixed code before the fix below.

Fixed with a new `LanguageQaManager.invalidate_all()`
(`language_qa_jobs.py`): clears the *whole* per-chapter cache rather than
one chapter's entry, since a termbase change is book-wide by nature, unlike
`verse.decide`/`verse.edit`'s existing chapter-scoped `invalidate(chapter)`.
Called from `terminology_record()` right after the write. Full engine suite
reran clean (1391 passed / 1 skipped).

Verified the fix directly against the rebuilt frozen binary and the real
`vallinam-test` project before asking for a second desktop retest, not just
the source-level test suite -- a raw JSON-lines session against
`bridge-engine.exe`: open the project, poll `languageQa.status` until the
initial scan settles to `completed`, call `terminology.record` with a
rejected form (`செய்தி`) that actually appears in the fixture text, then
poll again with no manual intervention. Picked up the new occurrence
cleanly, `totalFindings` 7 -> 9. (The first attempt at this same check used
"தேவன்" as the rejected form, copied from a different test fixture's
convention -- this project's text never contains that word, so it produced
a false failure. Worth naming: a script-level smoke check is exactly as
easy to get wrong as a unit test's fixture data, and needs the same
scrutiny before trusting its result either way.) Cleaned up both throwaway
rules from the project's database afterward via the proper
`WorkbenchRepository._delete` API (journaled through `change_log`, not a
raw `DELETE`), the same way the earlier #169 seed/cleanup was done.

Second desktop retest 2026-09-23 confirmed clean end to end: added a
`water` rule (preferred நீர், rejected தண்ணீர்) through the Terminology
pane, both pre-existing occurrences (verses 5-6) grew the double underline
immediately with no extra step; "Use" on verse 5 applied நீர் and
re-checked the verse ("Fix applied and verse re-checked"); verse 6 stayed
correctly flagged; the earlier-ignored கடவுள் occurrence at verse 9 (from
#169's own testing) correctly stayed suppressed while a different
occurrence at verse 10 stayed flagged, confirming the fix didn't disturb
existing decision-suppression behavior. #171 closed.

### 2026-09-23 LQA-2 Part B2: வல்லினம் மிகுதல் after அப்படி/இப்படி/எப்படி

A second maintainer-specified bounded rule, in the same closed-class family
as Part B1 (2026-09-22, above): அப்படி/இப்படி/எப்படி (manner-adverbs, "in
that way / in this way / how") followed by a க/ச/த/ப-initial word needs the
matching linking consonant, exactly like B1's demonstratives.

Reuses B1's mechanism with zero new logic -- the maintainer's explicit
instruction was to inspect and reuse B1's implementation rather than build a
second tokenizer or a parallel sandhi engine, and the actual change is one
line: `VALLINAM_TRIGGERS` gained the three new words. Every abstention case
in the spec turned out to already be covered by the existing exact-match-
against-the-bare-trigger-token check, with no new exclusion logic:

- A token that already carries the linking consonant (`அப்படிக்`) tokenizes
  as one word, distinct from the bare trigger `அப்படி` -- never matches.
- Look-alike words that merely contain a trigger as a prefix
  (அப்படித்தான், இப்படியும், எப்படியோ, அப்படியான், இப்படிப்பட்ட) are each
  one glued token (letters+marks, no internal whitespace) -- also never
  equal to the bare trigger.
- Punctuation and verse/USFM boundaries were already handled by the
  existing whitespace-only-adjacency check and `scan_text()`'s own early
  abstention on inline USFM markers.

`RULE_VERSION` bumped `language-qa-2` -> `language-qa-3` (informational
only, same as B1's own bump).

**Tests**: 45 new focused cases in `test_language_qa.py`, directly against
every example in the maintainer's spec -- all 12 missing-form combinations
(3 triggers x 4 consonant classes) flagged with exact wording/span, all 12
corrected forms clean, non-trigger-initial words clean, a punctuation
boundary for each trigger, all 5 named look-alike/suffixed forms clean,
trigger-at-end-of-verse (3 cases, no crash/no flag), inline-USFM
abstention, NFD-decomposed input still matches with the raw span preserved,
finding identity stable across repeated scans, the input text is never
mutated, B1 and B2 triggers coexist correctly in one verse, and an explicit
direct check that B1's own behavior is unaffected (B1's existing test
functions were left untouched, satisfying "B1 regression tests remain
unchanged" as specified). Full engine suite: **1436 passed / 1 skipped**,
up from 1391 -- the delta is exactly the 45 new tests, confirming nothing
else moved. No frontend files touched.

Desktop acceptance 2026-09-23 confirmed against the real `vallinam-test`
project, all live through the panel list (this rule was never meant to be
inline-decorated -- see the next entry): all three triggers flagged
positively across representative consonant classes (அப்படி/க, அப்படி/த,
அப்படி/ப, அப்படி and இப்படி/ச, எப்படி/க, எப்படி/ப -- ச and the remaining
combinations already covered by the 45 focused tests), and every negative
case tried live stayed clean (அப்படித்தான் கூறினான் look-alike, அப்படி,
கூறினான் punctuation boundary, எப்படி முடியும் non-trigger-initial).
Multiple findings coexisting in one verse rendered and listed correctly.
B1's own findings (verse 1, verse 3) stayed correct throughout. Part B2
closed.

### 2026-09-23 Extend inline highlight + suggest/edit/ignore to வல்லினம் (#173)

After confirming B1/B2 work through the panel, the maintainer asked for the
same inline treatment termbase v2 built (#171's predecessor): a flagged
span shown directly in the verse editor, right-click offering the suggested
fix, Edit, and Ignore. Termbase v2 had deliberately scoped that mechanism
to `terminology.deprecated-form` only when it shipped -- this extends it to
`tamil.vallinam-missing`, the second rule to ever get it.

Style: a filled yellow highlight (`--vallinam`/`--vallinam-bg`,
`mark.m-vallinam`), not another colored underline -- every existing mark,
termbase's double underline included, is border-only. Confirmed with the
maintainer via a side-by-side comparison (yellow highlight vs. a wavy
grammar-checker-style underline) before writing any code.

Traced the whole mechanism end to end before touching anything, per the
maintainer's own standing instruction to reuse existing patterns rather
than build a parallel one: `applyLanguageQaSuggestedFix`
(`src/lib/verseEditor.ts`), `FindingContextMenu`, `bridge.decideVerse`, and
the per-chapter cache's `decisions_version` were all already fully generic
-- `term_decisions` in `language_qa_jobs.py` (despite its name) is built
from every `kind='qa'` decision in the project, not scoped to terminology
by construction, only by which code paths chose to consult it. That left
exactly two real gaps to close, both narrow:

1. **A structured suggested fix.** `add()` (`language_qa.py`) gained an
   optional `suggested_replacement` parameter; the வல்லினம் block now
   computes the *whole* corrected span (raw trigger text + the inserted
   linking consonant + the untouched original whitespace and following
   word, exactly as written) rather than only interpolating `corrected`
   into the message string, which is all it did before.
2. **Ignore-suppression.** `_scan()`'s வல்லினம் merge point had no
   `term_decisions` check at all, unlike the terminology block just above
   it -- an "Ignore" on this rule would have recorded a decision that
   nothing ever consulted. Added the same `== "ignored"` check, scoped
   narrowly to `tamil.vallinam-missing` -- deliberately not a general
   suppression framework for every `scan_text` rule (wordlist-variant and
   the rest stay exactly as disposable as before, matching how termbase v2
   itself was scoped).

`RULE_VERSION` bumped `language-qa-3` -> `language-qa-4` (informational
only, same as the last two bumps -- finding *shape* changed, not matching
logic). Renamed the termbase-specific menu plumbing in `VerseList.svelte`
(`termContextMenu` -> `langQaContextMenu`, etc.) since "term" was actively
misleading once a second rule used it -- `Settings > Terminology` (#171) is
a different, unrelated feature. Purely mechanical; the logic inside was
already keyed off `finding.suggestedReplacement`/`finding.id`, never
`finding.rule`.

**Tests**: the existing B1/B2 parametrized flagged-form tests gained one
more assertion each (`suggestedReplacement` equals the text with the
linking consonant inserted, derived from the existing `flagged`/`initial`
params -- no new parametrize table). New: every other `scan_text` rule
still defaults `suggestedReplacement` to `None`; an "ignored" decision on a
வல்லினம் finding suppresses it on the next scan pass; a decision on one
occurrence doesn't suppress a different one; a real-dispatcher test
mirroring `test_terminology.py`'s own (`verse.decide` "ignored" through the
actual `BridgeEngine`, not just the manager). Full engine suite: **1440
passed / 1 skipped** (up from 1436). Frontend: a `tamil.vallinam-missing`
finding now produces `.m-vallinam`; `applyLanguageQaSuggestedFix` proven
explicitly rule-agnostic with a வல்லினம்-shaped finding rather than left
merely inferred. Full Vitest suite: **495 passed** (up from 493). `npm run
check` 0/0, `npm run build` clean.

Desktop acceptance not yet run -- awaiting the maintainer testing the
actual build.

### 2026-09-23 #173 desktop retest: LanguageQaPanel had its own, separate copy of the rule filter

First desktop retest found no yellow highlight anywhere, on any verse, even
though the backend was independently verified correct beforehand (a raw
JSON-lines session against the rebuilt frozen `bridge-engine.exe`, not just
the source test suite -- confirmed a structured `suggestedReplacement`
computed correctly and ignore-suppression working end to end). What
appeared instead were red single-underline marks on இப்படி/எப்படி in one
test verse -- the same coincidental, unrelated Greek Room "spelling
similarity" finding pattern identified earlier during #169's own
investigation, not வல்லினம் at all (`எப்படி முடியும்` is a confirmed
negative case; it can never produce a வல்லினம் mark).

Root cause: `buildSegments()` (`highlight.ts`) was correctly extended
earlier today to handle both `terminology.deprecated-form` and
`tamil.vallinam-missing`, but `LanguageQaPanel.svelte`'s
`updateInlineStore()` -- which populates `languageQaFindingsByVerse`, the
store `buildSegments` actually reads from -- had its own separate,
hardcoded `rule !== "terminology.deprecated-form"` filter, missed entirely
during today's earlier research pass. It dropped every வல்லினம் finding
before it ever reached the store, so `buildSegments` never even saw one to
decorate, despite handling the rule correctly itself. Two independently
maintained copies of the same rule list, and they had already drifted the
moment the first one was written.

Fixed by exporting `INLINE_LANGUAGE_QA_MARKS` from `highlight.ts` as the
one source of truth and having `LanguageQaPanel`'s filter check membership
in it (`finding.rule in INLINE_LANGUAGE_QA_MARKS`) instead of keeping a
second, driftable copy. Swept the rest of `src/` for any other hardcoded
`"terminology.deprecated-form"` checks afterward -- none remained outside
this shared map and doc comments, confirmed by grep, not assumed. Extended
the existing `LanguageQaPanel.test.ts` coverage (previously titled "...only
terminology.deprecated-form entries...", now proven to also include
வல்லினம்) rather than adding a parallel test. Full Vitest suite reran clean
(495 passed, same count -- an existing test grew stronger rather than a new
one being added). Commit `b72b75f`.

### 2026-09-23 Researched and rejected external Tamil dictionary/morphology libraries for LQA-2 items 1/2/6

Surveyed the full 56-item framework (`docs/LANGUAGE_QA_TAMIL_SPECIFICATION.md`)
against what LQA-2 actually covers so far: items 1 (spelling), 2 (morphology),
4-8 (word division/case/agreement/syntax/completeness), 10 (register) are all
unstarted and explicitly blocked on "dictionary and morphology feasibility
measured offline" (LQA-2's own scope note in issue #169). Investigated whether
an external Tamil NLP library could unblock that, following this project's
standing discipline for every external dependency so far: install it, run it
against real input, don't trust the docs.

**Open-Tamil** (`pip install open-tamil`, MIT): installed and tested three
components against real vallinam-test vocabulary and B1's own hand-verified
correct/incorrect pairs.
- `solthiruthi.dictionary.TamilVU` (bundled 63,896-word dictionary): only has
  root forms. Ordinary inflected verbs (கூறினான், பறந்தது, இருந்தது, செய்தான்,
  இருக்கிறார்) all return `isWord() == False` despite being perfectly correct
  Tamil.
- `tamilstemmer.TamilStemmer`: tried to fix the above by stemming before
  lookup. Makes it worse, not better -- mis-stems கடவுள் (correct, already a
  dictionary word on its own) to கட (not a word, false positive), and
  மொ-stems இருக்கிறார் to இர் (a *different*, unrelated dictionary word --
  an accidental match for the wrong reason, not evidence of correctness).
- `tamilsandhi.check_sandhi`: the component most relevant to further B-series
  sandhi rules. Tested directly against B1's own known-correct sentence
  ("அந்தக் காகம் பறந்தது") -- it reports 2 separate spurious errors on text
  we already know is correct, and its own "fixed" output is always identical
  to the input (never actually corrects anything). Its source has ~24 named
  rules (விதி 1, விதி 2, ...) covering similar territory to B1/B2
  (சுட்டு/வினா-derived words, numerals, specific word-final patterns) --
  possibly useful as *reading material* for hand-verifying a future B3 spec,
  never as a component to run.

**AI4Bharat / `indic-nlp-library`** (`pip install indic-nlp-library` +
a separate 258 MB resource download, MIT, from IIT Madras -- a real academic
lab, not a hobby package): its `UnsupervisedMorphAnalyzer` (morfessor-based)
is genuinely better-behaved than Open-Tamil's stemmer -- it never mangled a
correctly-spelled word into an unrelated wrong root in this testing. But
combined with the same TamilVU dictionary for a spell-check signal (whole
word or any segment matches a dictionary entry), coverage is still the
blocker, not segmentation quality: of 17 real test words, 9 correctly-spelled
ordinary words got no dictionary signal at all, and one real misspelling
(கடவுல்) slipped through as looking fine because it coincidentally segmented
into a dictionary-valid piece.

**Conclusion: rejected, not integrated.** A general-purpose 64K-root Tamil
dictionary -- classical/academic in origin, not Biblical-register -- doesn't
have adequate coverage of ordinary running Tamil text regardless of which
segmentation/stemming algorithm sits in front of it. Wiring either library in
as an automated spelling/morphology check would produce a real stream of
false positives on correctly-spelled text, exactly the trust-eroding failure
mode this project has been careful to avoid with every rule shipped so far
(B1/B2 only landed after validation against the maintainer's own
hand-supplied correct/incorrect pairs). Both packages uninstalled, the 258 MB
resource clone removed, engine venv confirmed clean and fully functional
afterward (`pytest tests/service/test_language_qa.py`, 116 passed) -- nothing
was ever added to `pyproject.toml` or vendored.

**Direction going forward, per the maintainer:** build a corpus-internal
vocabulary baseline instead of relying on an external dictionary --
extending item 49's wordlist-audit approach (already built, already proven)
using the Tamil IRV project's own already-translated text as the vocabulary
source, rather than a generic dictionary that doesn't match this project's
register. Explicit caution from the maintainer, worth recording permanently:
**the IRV project's own existing text has known quality problems -- that is
the whole reason Bridge exists.** Any corpus-derived resource built from it
needs human curation/review layered on top, not automatic ingestion, or it
will enshrine the project's existing errors as "valid vocabulary" instead of
catching them. Not yet scoped as concrete work; no code changed in this
session.

### 2026-09-24 LQA-2 Part B3: வல்லினம் மிகுதல் after explicit dative -க்கு

A third maintainer-specified bounded rule, again reusing B1/B2's mechanism
with zero new logic -- `VALLINAM_TRIGGERS` gained three more words:
எனக்கு, உங்களுக்கு, தேவனுக்கு (explicit fourth-case/நான்காம் வேற்றுமை விரி
surface forms), each followed by a க/ச/த/ப-initial word requiring the
matching linking consonant, exactly like B1's demonstratives and B2's
manner-adverbs.

**How the candidate rule was found.** #169's own checklist had items 1/2/6
blocked on "dictionary and morphology feasibility measured offline" since
the 2026-09-23 external-library rejection (previous entry). The maintainer
asked to research an authoritative source for B3 rather than requiring
hand-supplied examples from scratch. Web research (Tamil Virtual Academy,
Tamil Wikipedia's வல்லினம் மிகும் இடங்கள் article, cross-checked against
independent grammar sites including an 8th-standard textbook chapter --
same rule, same canonical examples, not a one-source claim) found
"நான்காம் வேற்றுமை விரியில் வல்லினம் மிகும்" as a real, textbook-documented
rule, directly quoted (not paraphrased) from the Wikipedia article:
தந்தைக்குக் கொடுத்தான், தாய்க்குச் சொன்னான், தங்கைக்குத் தந்தான். The same
research explicitly did *not* find equivalent support for genitive -உடைய or
the quantifier எல்லா as general phrase-boundary rules -- the documented
genitive rule only covers a fused compound (நாய்க்குட்டி, one word), not a
spelled-out phrase like அவர்களுடைய + a separate following word, and no
source addressed எல்லா at all. Reported back to the maintainer with an
explicit caveat that a Wikipedia summary is a claim to verify, not a rule
to ship from directly -- same discipline as every other external source
this project has used.

**The maintainer's actual spec (2026-09-24) is what B3 implements**, and it
corrected two things the web research alone got wrong or left open:

1. **-உடைய is the *opposite* direction, not merely "unconfirmed."** Standard
   modern Tamil does *not* geminate after உடைய (அவர்களுடைய பெயர்கள் is
   correct; அவர்களுடையப் பெயர்கள் is not) -- a candidate future "excess
   வல்லினம்" rule if ever built, never folded into B3. This directly
   concerns the 2026-09-24 Philippians Round 2 QA state doc's 3:19 row,
   marked "Disputed" there for an unrelated reason (a live-file-content
   contradiction, not a rule question) -- worth the maintainer knowing this
   rule question and that factual dispute are two separate things about the
   same verse.
2. **The morphological guard.** `token.endswith("க்கு")`-style suffix
   matching is explicitly unsafe -- ordinary Tamil lexical words can
   themselves end in க்கு without being a fourth-case form, and Bridge has
   no morphological analyzer to disambiguate. B3 ships as an allowlist of
   three independently verified dative surface forms, the same
   exact-match-against-the-bare-trigger-token mechanism B1/B2 already use
   for their own closed trigger sets -- not a new mechanism, just a
   pre-existing one applied to a third list. A new dative form gets added
   to the allowlist only when its case analysis is independently verified,
   same bar as B1/B2's own trigger words.

**Explicitly out of scope for B3** (per the maintainer's spec, not to be
inferred back in later without a fresh bounded rule statement): உடைய
genitive, எல்லா, accusative -ஐ, any other case suffix, compounds,
inferred/hidden fourth-case தொகை forms, general words merely ending in
க்கு, morphology guessing, automatic Scripture correction.

**Round 2 QA re-review.** The maintainer's spec explicitly warned against
treating the Philippians Round 2 QA pass's own "Rejected"/"Fixed"
dispositions as linguistic ground truth for this question -- that pass
recorded human review outcomes on a different, broader question (is this
inconsistency worth an editorial fix), not a validated grammatical rule.
Three of the pass's dative-sandhi rows are exact instances of this rule:
php 1:29 (உங்களுக்கு கொடுக்கப்பட்டிருக்கிறது, previously "Rejected"), php
4:15 (தேவனுக்கு ... சுகந்த, the bare comparison point behind the already-
"Rejected" 4:18 row), and php 1:12 (எனக்கு சம்பவித்தவைகள், the one row the
maintainer had already marked "Fixed" -- consistent with this rule, not
contradicting it). B3's own tests include a dedicated parametrized case
(`test_vallinam_b3_matches_the_maintainers_philippians_fixtures`) against
the maintainer's exact fixture text for these three, tied back to their
verse references rather than left as synthetic examples. The Round 2 QA
workbook/state doc themselves were not edited in this session -- that is
tracked separately in `Claude outputs/philippians-round2-qa-state.md`, not
part of the Bridge repository.

`RULE_VERSION` bumped `language-qa-4` -> `language-qa-5` (informational
only, same as B1->B2's own bump).

**Tests**: 45 new focused cases -- 12 against every trigger x initial
combination (3 triggers x 4 consonant classes), reusing only vocabulary
already attested either in the maintainer's own spec or the existing B1/B2
test suite (கொடு, தெரியும், பயன், சம்பவித்தவைகள், செய்தான், சுகந்த,
கொடுக்கப்பட்டிருக்கிறது) rather than inventing new Tamil text -- same
caution as everywhere else in this project about not trusting unverified
linguistic judgment, including its own. Plus: the 3 maintainer-fixture cases
above, correct-forms-not-flagged (12 cases), non-trigger-initial abstention
(3), punctuation-boundary abstention (3), look-alike/suffixed-form
abstention (3: எனக்குள், உங்களுக்குள், தேவனுக்குரிய -- the concrete
demonstration of the morphological guard), trigger-at-end-of-verse (3),
inline-USFM abstention, NFD-decomposed input, finding-identity stability, no
input mutation, B1+B2+B3 coexistence in one verse, and an explicit
B1/B2-regression-unaffected check. `test_language_qa.py` alone: **161
passed**, up from 116 -- the delta is exactly the 45 new cases, confirming
nothing else moved. Full engine suite (serial, ~19 minutes): **1491 passed,
0 failed, 0 skipped**.

**Round 2 QA re-audit, done immediately after (2026-09-24).** Per the
maintainer's explicit instruction not to treat the Philippians Round 2 QA
pass's own dispositions as linguistic ground truth, ran a systematic B3-rule
re-scan of every எனக்கு/உங்களுக்கு/தேவனுக்கு occurrence in that book (same
tokenizer as production, run against the same `staged/parsed.json` used for
the original pass) rather than relying on the earlier manual read. Found 3
genuine violations the original verse-by-verse read never caught -- php
1:10 and 2:11 (both தேவனுக்கு + க-initial, bare), php 4:15 (எனக்கு +
க-initial, bare) -- and one wrong citation in the original workbook: its
4:18 row cited "bare at 4:15" as the comparison point, but 4:15 contains no
தேவனுக்கு at all. php 1:29 changed from "Rejected" to "Confirmed" on this
basis. All of this lives in `Claude outputs/philippians-round2-qa-state.md`
and the regenerated workbook, not in the Bridge repository -- noted here
only because it is the concrete evidence that B3 is a real, previously-
undetected gap, not a synthetic example.

No frontend changes needed or made: `INLINE_LANGUAGE_QA_MARKS` in
`highlight.ts` keys on `finding.rule` (`"tamil.vallinam-missing"`), never on
which trigger word fired, so B3 findings get the same yellow inline
highlight, suggest/edit/ignore, and panel listing as B1/B2 automatically --
confirmed by reading the mechanism, not assumed from B2's own precedent.

Desktop acceptance not yet run -- awaiting the maintainer testing the actual
build, same as every rule so far.

### 2026-09-24 LQA-2 Part B4: வல்லினம் மிகுதல் after explicit accusative -ஐ

A fourth maintainer-specified bounded rule, again reusing B1/B2/B3's
mechanism with zero new logic -- `VALLINAM_TRIGGERS` gained five more words:
என்னை, உங்களை, அவனை, அதை, எதை (explicit second-case/accusative,
இரண்டாம் வேற்றுமை விரி surface forms), each followed by a க/ச/த/ப-initial
word requiring the matching linking consonant.

**How this candidate was actually found is worth recording precisely,
because the process itself surfaced two real citation/verification
failures worth remembering.** After B3, the maintainer asked for another
researched B4 candidate. A first web-research pass proposed single-letter
words (கை/தீ/தை/பூ/மை) and confirmed they are real vல்லினம் rules -- but
they turned out to be *compound-word formation* (கை + குழந்தை = one fused
word கைக்குழந்தை), not the *adjacent-word* sandhi B1-B3's mechanism handles;
dropped as the wrong structural type, not wrong grammar. Accusative -ஐ and
ஈறுகெட்ட எதிர்மறைப் பெயரெச்சம் were then identified as candidates with the
right structural shape, but a scan of Philippians found zero real
occurrences of either -- the book is short enough that not every sandhi
environment shows up in it.

The maintainer then supplied an extensive external research package (a
133-rule catalogue, 42 sources, and a claimed local Psalms corpus archive
at `D:\GPT Lab\...`) arguing for both candidates. Two things in it needed
independent verification before use, same discipline as every source this
project has ever used:

1. **One citation was wrong.** The package's citation for accusative -ஐ's
   `கண்ணனைக் கண்டான்` example pointed at a real TVA page that, when fetched
   directly, turned out to be about அரை/பாதி instead -- a genuine citation
   error. A second round from the maintainer supplied a *different*, correct
   URL for the same claim, which was independently fetched and does contain
   `கண்ணனைக் கண்டான்` under இரண்டாம் வேற்றுமை. Net effect: the underlying
   grammar claim holds; "fabricated" (this session's first word for it) was
   an overstatement of what was actually a wrong citation, corrected on
   review -- worth recording as its own small calibration lesson.
2. **The Psalms corpus evidence could not be verified and was not used.**
   `D:\GPT Lab\...` is not a path on this machine, was never referenced
   anywhere else in this session, and there is no way to confirm the claimed
   archive, its SHA-256 hashes, or its extracted examples (E01-E06) are
   genuine rather than constructed to look genuine. Separately, it was
   Psalms, not Philippians -- the wrong book for this review regardless.
   None of that corpus evidence was used for B4; it is not cited anywhere in
   this rule's tests or specification.

**What actually grounds B4 is a third, independently-verified pass**: the
maintainer supplied three specific Philippians line/verse claims
(php 2:28, 3:14, 4:18) against `Claude outputs/staged/51PHPIRVTam.SFM` --
a real, local, directly-readable file from earlier this session. All three
were read directly (not trusted from the claim) and confirmed byte-for-byte
correct: php 2:28 (`அவனை சீக்கிரமாக`, bare, a pronoun -- in scope), php 3:14
(`பரிசை பெற்றுக்கொள்ள`, bare, an ordinary noun -- correctly excluded from
B4's scope, would need a noun/participle recognizer Bridge doesn't have),
php 4:18 (`அனுப்பப்பட்டவைகளை தேவனுக்குச்`, bare, a participial noun --
also correctly excluded, and a *second*, previously-uncaught boundary in
the same verse already covered once by B3 for `தேவனுக்குச் சுகந்த`). Only
php 2:28 is inside B4's actual scope, and it is the rule's real fixture.

**Explicitly out of scope for B4**, same as every case-suffix rule so far:
ordinary nouns/participles ending in bare ஐ (பரிசை, அனுப்பப்பட்டவைகளை --
real, confirmed bare boundaries in Philippians, but excluded because
distinguishing "accusative noun" from "any word ending in ஐ" needs a real
morphological analyzer, not an allowlist), -உடைய, எல்லா, any other case
suffix, compounds, தொகை forms, ஈறுகெட்ட எதிர்மறைப் பெயரெச்சம் (real grammar,
zero verified Philippians occurrences, not pursued further this round),
அரை/பாதி (same), and general words that merely end in ஐ.

`RULE_VERSION` bumped `language-qa-5` -> `language-qa-6` (informational
only, same as every earlier bump).

**Tests**: 63 new focused cases -- 20 against every trigger x initial
combination (5 triggers x 4 consonant classes), reusing only vocabulary
already attested in the maintainer's own B3 spec or the existing B1/B2/B3
test suite; 1 tied directly to the real, independently-verified php 2:28
text (not a synthetic fixture); 20 correct-forms-not-flagged; 5
non-trigger-initial abstention; 3 punctuation-boundary abstention; 3
look-alike/suffixed-form abstention (என்னைவிட, அதைவிட, அவனைப்போல -- real
Tamil comparative/similative constructions, each tokenizing as one word);
5 trigger-at-end-of-verse; inline-USFM abstention; NFD-decomposed input;
finding-identity stability; no input mutation; B1+B2+B3+B4 coexistence in
one verse; and an explicit B1/B2/B3-regression-unaffected check.
`test_language_qa.py` alone: **224 passed**, up from 161 -- the delta is
exactly the 63 new cases. Full engine suite (serial, ~19 minutes): **1554
passed, 0 failed, 0 skipped** (up from 1491, confirming nothing else moved).

No frontend changes needed or made, same reasoning as B3: `finding.rule`
stays `"tamil.vallinam-missing"` regardless of which trigger fired.

Desktop acceptance not yet run -- awaiting the maintainer testing the
actual build, same as every rule so far.

## 2026-09-24 — Language QA review fixes: inline coverage, footnoted verses, rollup isolation, offsets

A review of the inline வல்லினம்/termbase work found four defects. They are
fixed in order, one commit each. None of them changes a finding id, a rule,
a trigger list, the CSS or the termbase format.

### 1. Inline marks now cover the whole book, not the panel's first page

**Defect.** `LanguageQaPanel.svelte` filled `languageQaFindingsByVerse` (the
store `VerseList` reads for the double-underline and the yellow வல்லினம்
highlight) from its own `languageQa.status` page. That page is `limit=100`
(50 once expanded), ordered book → chapter → verse, and mixes in every rule.
In a book with more than 100 Language QA findings, any inline finding past
the first page got no mark and no right-click menu. It still showed in the
panel once the panel was paged far enough. Chapter 1 looked fine; later
chapters silently lost their marks.

**Cause.** One store fed two consumers with different needs. The panel
wants a bounded, paged list. The editor wants every inline finding for the
chapter on screen. The panel's page was the only source of both.

**Fix.**
- Engine: a new `languageQa.inline` method (`Methods.LANGUAGE_QA_INLINE`,
  project-guarded like every other `languageQa.*` method; `chapter` must be
  a string when given). It returns every finding of an inline rule for one
  chapter, or the whole book without a chapter, from the current summary.
  It is unpaged. It stays bounded by the existing `MAX_VERSE_FINDINGS` and
  `MAX_BOOK_FINDINGS`, which are unchanged. The inline rules are one engine
  constant, `language_qa.INLINE_RULES`, and `languageQa.status` now reports
  them as `inlineRules`. The idle-refresh probe `status()` already ran was
  factored into `_refresh_if_due()`, so `inline()` runs it too.
- Frontend: new `src/lib/languageQaInline.ts`. It polls `languageQa.inline`
  for `$currentChapter` every 5 s and immediately on a chapter change. It
  groups by exact `chapter:verse`, keeping verse bridges, and is the only
  writer of `languageQaFindingsByVerse`. A sequence ticket means only the
  newest request writes the store or schedules the next poll. An answer for
  another project or an old chapter is discarded. A failed poll keeps the
  last marks, and stopping the poller clears them. `App.svelte` starts it
  when a project is open and stops it on a project change or teardown.
- `LanguageQaPanel.svelte` no longer imports the store. While collapsed it
  goes back to `limit=0` (count only).
- `INLINE_LANGUAGE_QA_MARKS` in `highlight.ts` now only maps a rule to a CSS
  class. `test_inline_rules_match_the_frontend_class_map` parses it out of
  the TypeScript source and asserts its keys equal `INLINE_RULES`, so the
  two cannot drift the way the panel's filter and `buildSegments`' did in
  #173.

**Verification.**
- Engine (`test_language_qa.py`):
  - A three-chapter project has 360 findings, 180 of them inline. Asking
    for chapter 3 returns all 60 of chapter 3's inline findings, even though
    they sit past offset 100. `status(offset=0, limit=100)` still returns a
    100-item page.
  - With no chapter, `inline()` returns the whole book and only inline rules.
  - The class-map parity test above.
  - Through the real dispatcher, the method refuses a wrong project path and
    an integer chapter.
- Frontend: `languageQaInline.test.ts` is new, with six tests.
  - 150 findings reach the store, and `buildSegments` marks the 150th verse.
  - Verse bridges are grouped exactly.
  - A late answer for the previous chapter is discarded.
  - A finding that moves verse on a later pass is tracked.
  - An answer for another project is ignored.
  - A failed poll keeps the marks, and stopping clears them and ends
    polling.
- `LanguageQaPanel.test.ts`:
  - Collapsed now asserts `(path, 0, 0)`.
  - The two tests that asserted the panel populates the store were replaced
    by one that asserts it never writes the store while paging.
- Gates: `npm run check` 0/0; `npx vitest run` 500 passed; `npm run build`
  ok; engine `pytest -n auto` 1558 passed,
  up from 1554 by exactly the four new tests.
- Desktop acceptance not yet run.

### 2 (and 4). Verses containing inline USFM are scanned, and a fix after a footnote lands on the right word

**Defect 2.** `scan_text` returned early for any verse containing a
backslash: "Inline USFM verse omitted from this text-only pass". Every
footnoted or cross-referenced verse, and every `\wj` verse, got no Language
QA at all. That meant no வல்லினம், no termbase and no character checks, and
their words were missing from the book-wide wordlist.

**Defect 4.** In `VerseList.svelte` the right-click menu received the
*display* copy of each Language QA finding, whose offsets had been shifted
onto the notes-lifted text for drawing. `applyLanguageQaSuggestedFix`
splices the *raw* verse, so on a verse with a footnote before the flagged
span it cut at the wrong place. Its own `originalText` guard then refused
the fix as "stale". This could only be reached once defect 2 was fixed,
which is why the two fixes are in one commit.

**Cause.** The engine had no notion of visible text, so bailing out was the
only way to keep spans exact over raw text. The frontend used one mapped
list for two jobs with different coordinate systems: drawing needs display
offsets, splicing needs raw ones.

**Fix (engine, `language_qa.py`).**
- New `lift_inline_usfm(raw)` returns `(LiftedVerse | None, reason)`.
  `LiftedVerse` is the visible text built by deletion only, plus
  `raw_index`, the raw offset of every kept code point. What it lifts:
  - `\f … \f*` and `\x … \x*` with their contents. The pattern and the
    swallow-one-space rule are the frontend's `parseVerseNotes`, pinned by
    one shared table in `test_language_qa.py` and `usfmNotes.test.ts`. The
    table also records one inherited quirk: `"a \f…\f*"` at the end of a
    verse keeps its trailing space, exactly as the frontend does.
  - Character markers, closers, nested `\+…` and milestones. Content is
    kept, and an opener's one following space goes with the opener.
  - `|…` attributes up to a closing marker.
- `lift_inline_usfm` refuses the verse, with a named reason, when:
  - markers are unbalanced (`usfm.marker_balance_issues`, e.g.
    `Unbalanced \f: 1 open, 0 close; verse not checked.`);
  - `\f`/`\x`-family markup sits outside a complete note (`\fig` is
    exempt);
  - a backslash is not a marker;
  - an attribute bar survives lifting.
- The last guard came from running the lifter read-only over the two local
  Tamil projects. `ta_irv_phm_book`, a development import, carries a custom
  `\zsem-s |x-content="δέσμιος…" x-note="…"*` milestone closed by a bare `*`.
  Without the guard, 131 `unicode.nfc` and dozens of other "findings" came
  from its Greek and English attribute text. With it, those verses are
  refused by name.
- `scan_text` keeps the length and surrogate guards on the raw text, lifts
  the verse, then runs every rule on the visible text.
  - `add()` translates each span through `raw_span()`. A span that would
    cross lifted markup returns `None`. That candidate is dropped before any
    id or occurrence number is assigned, and it is counted in the verse
    limitation `N candidate(s) spanning inline USFM markup omitted.`
  - Every finding's `start`/`end` is raw, and
    `originalText == raw[start:end]`.
  - The result gains `checked`, which is False only when the verse was not
    scanned.
  - A verse without markup lifts to itself, so its findings, ids and
    offsets are unchanged (pinned by a test).

**Fix (engine, `language_qa_jobs.py`).**
- A verse is counted as skipped only when `checked` is False. Before, *any*
  limitation, including the per-verse finding limit, made it skipped and
  dropped its terminology and wordlist input. That no longer happens,
  because a crossing drop would otherwise have silently removed the
  termbase check from every footnoted verse. Such limitations are still
  reported, still mark the pass incomplete, and still keep the chapter out
  of the cache.
- Terminology matching and the wordlist counts run on the same visible text
  and are mapped back to raw. A terminology match that crosses markup is
  dropped and counted. A word split by markup is not counted.
- The language-detection sample uses visible text instead of skipping
  marked verses.

**Fix (frontend).** `VerseList.svelte` passes the store's raw findings to
the menu and to `onMarkContextMenu`. Only `buildSegments` gets the display
copies, now `displayLanguageQaFindings`. `applyLanguageQaSuggestedFix` and
its staleness guard are unchanged.

**Verification.**
- Engine: 286 passed in `test_language_qa.py` plus `test_terminology.py`.
  - The five tests that pinned the old bail-out now pin the new contract.
    B2/B3/B4 inside `\wj` are found with raw offsets. The wordlist exclusion
    test uses an unbalanced `\wj`. A `\wj` word is counted at raw offsets.
  - New cases:
    - a footnote after `அந்த பட்டணம்`, and a finding after a footnote at
      its raw offset;
    - `\wj அவனை கொன்றார்கள்`;
    - a doubled space inside a footnote gives no finding, while a doubled
      space inside `\wj` is found;
    - an unbalanced `\f` is skipped with its named reason, as are note
      markup outside a note, a stray backslash and the real unclosed
      milestone shape;
    - a crossing candidate is dropped and counted;
    - `\w` and `\zaln` attributes are lifted;
    - the shared swallow table;
    - a plain verse is unchanged;
    - through the manager, a footnoted verse is checked, not skipped, and
      its வல்லினம் and termbase findings carry raw offsets.
- Frontend:
  - `VerseList.test.ts`: right-click Use on a vallinam mark after a
    footnote calls `editVerse` with exactly the manual raw splice. This test
    fails on the previous `VerseList.svelte`, where the fix was refused as
    stale. It also checks the mark is drawn on the flagged words.
  - `verseEditor.test.ts`: the fix on a footnoted verse keeps the note
    byte-identical.
  - `usfmNotes.test.ts`: the shared table.
- Real data, read-only, scratch script:
  - `ta_irvv_phm_book` (the IRV import): all 8 verses with notes are now
    checked, every `originalText == raw[start:end]`, and no crossings.
  - `ta_irv_phm_book`: 25 malformed-milestone verses refused by name, 1
    checked.
- Gates: `npm run check` 0/0; `npx vitest run` 503 passed; `npm run build`
  ok; engine `pytest -n auto` 1573 passed.
- Desktop acceptance not yet run.

**Remaining limitation, documented in `LANGUAGE_QA_PLAN.md`.** A candidate
that crosses markup cannot be reported. A வல்லினம் boundary between two
`\w`-wrapped words is the common case, although translationCore imports
normally flatten `\w`. Footnote text itself is not checked.

**Observed, not changed.** `parseVerseNotes`' `mapOffset` indexes UTF-16
code units while engine offsets are code points. The two agree for Tamil
and every other BMP script, but would drift for astral-plane text on the
display path. This is pre-existing and out of scope here.

### 3. Language QA decisions no longer move the review-progress totals

**Defect.** Use and Ignore on a Language QA mark call `verse.decide`, which
went straight to `_apply_decision_to_progress`. So every வல்லினம் or
termbase decision became a finding row in the book's progress rollup, and
moved `findingCount`, `approvedFindingCount` and `reviewedVerseCount`, plus
the dashboard's cached copy of those totals. Language QA findings are
disposable text-only review candidates. They are not the checked QaFindings
those totals measure, so an "ignored" vallinam made a verse look reviewed.

**Cause.** `verse.decide` had no way to know what kind of finding it was
deciding, and nothing told it.

**Fix.**
- Every Language QA finding carries `"source": "languageQa"`
  (`language_qa.FINDING_SOURCE`). That covers `scan_text`'s findings,
  terminology findings and wordlist findings.
- `decide_verse` takes an optional `issue` dict. The dispatcher rejects a
  non-object value. `issue` is stored as the decision's payload through the
  existing `record_qa_decision(issue=…)`, which needs no storage change.
- The rollup update is skipped when `issue.source == "languageQa"`. Origin
  comes only from `issue`, never from the finding id.
- The decision is still recorded in `human_decisions` and `change_log`, and
  the chapter is still invalidated.
- Semantics are unchanged: "ignored" still suppresses, and "accepted" still
  does not.
- Frontend: `findingActions.ts` gains `languageQaDecisionIssue(finding)`
  and `decideLanguageQaFinding`. The issue is `{source, rule, ruleVersion,
  originalText, suggestedReplacement, message, start, end}`, and its
  `source` is always `"languageQa"`, whatever the finding object carries.
  `bridge.decideVerse` gains an optional `issue`. VerseList's Ignore and
  `applyLanguageQaSuggestedFix`'s accepted both use the helper.

**Verification.**
- Engine:
  - Through the real dispatcher, accepting and then ignoring a vallinam
    finding leaves `load_progress_rollup()` exactly equal to before: no
    finding row, no totals change, no verse entry. This test fails without
    the `decide_verse` change.
  - The payload records `issue.source`, `issue.rule` and
    `issue.originalText`.
  - "accepted" leaves the finding in place, and the next scan suppresses
    the ignored one.
  - Greek Room regression, parametrized over no issue, an empty issue and an
    issue with another source: the rollup still records the decision and
    counts it.
  - A non-object `issue` is refused and nothing is recorded.
  - Every Language QA producer's findings carry `source`.
- Frontend: `verseEditor.test.ts` asserts the exact issue sent on Use.
  `VerseList.test.ts` asserts it on Ignore, and that the mark disappears.
- Gates: `npm run check` 0/0; `npx vitest run` 504 passed; `npm run build`
  ok; engine `pytest -n auto` 1579 passed.
- Desktop acceptance not yet run.

### 4. Offset consistency, stale marks after any edit, and a decision cache key that only Language QA can move

**Offset consistency (defect 4).** Its regression test landed with its fix
in commit 2 (`VerseList.test.ts`). With a footnote before the flagged span,
right-click Use calls `editVerse` with exactly the manual raw splice. This
commit adds two smaller items the review asked for in the same PR.

**`saveVerseEdit` now clears `languageQaFindingsByVerse[verseKey]` on
success.**
- Defect: only VerseList's own Use handler cleared a verse's Language QA
  marks. After a typed edit, or a Greek Room fix applied through the same
  save path, the old marks kept their old offsets, and drew on the wrong
  words until the next inline poll replaced them.
- Fix: the verse's entry is cleared right after `editVerse` succeeds, next
  to the other per-verse derived stores that are already cleared there. A
  failed save leaves the marks alone.

**`decisions_version` counts only decisions that may concern Language QA.**
- Defect: the key was a hash of *every* QA decision in the book, and it is
  part of every chapter's cache key. So any Greek Room accept anywhere
  forced a full-book Language QA rescan.
- Fix: the key is built only from rows whose `issue.source == "languageQa"`,
  plus legacy rows whose `issue` has no `source` key at all. Those are kept
  rather than guessed away (`_may_concern_language_qa`).
- To make a new non-Language-QA decision distinguishable from a legacy row,
  `decide_verse` now stamps `issue.source = "unspecified"`
  (`language_qa.UNSPECIFIED_DECISION_SOURCE`) when its caller names none.
  That records that the caller did not say. It is not an inference from the
  id.
- Suppression still reads every decision, so an "ignored" from any caller
  keeps working, and `decide_verse` still invalidates its own chapter.
- Nothing else reads `issue`: checked by grep before relying on it.

**Verification.**
- Engine: a two-chapter project through the real dispatcher.
  - A Greek Room decision on chapter 1 leaves chapter 2 reused. This test
    fails on the previous `language_qa_jobs.py` with 0 reused, expected 1.
  - The recorded issue is `{"source": "unspecified"}`.
  - A Language QA decision on chapter 2 rescans chapter 1.
  - A legacy row with no source rescans; a row stamped "unspecified" does
    not.
- Frontend: `verseEditor.test.ts`:
  - A successful save clears only that verse's marks. This fails without
    the change.
  - A failed save keeps them.
- Gates: `npm run check` 0/0; `npx vitest run` 506 passed; `npm run build`
  ok; engine `pytest -n auto` 1580 passed.
- Desktop acceptance not yet run.

### Audit follow-up: two test gaps closed, RULE_VERSION bumped, acceptance guide added

An item-by-item audit of the four commits against the review brief found
every fix present and behaving as specified. It also found two places where
the tests fell short of the brief's wording. Both are closed here, with no
behaviour change:

- **The brief's exact footnote example is now a test.** The input is
  `அந்த பட்டணம்\f + \fr 1:1 \ft note\f* வந்தான்`, `\fr` reference included.
  The suite previously covered only a close variant. The finding is at raw
  `[0, 12)` and is exact.
- **A hand-typed save now has a test.** The flow is `startVerseEdit`, typed
  text, `saveVerseEdit`, with no Use involved. It must clear that verse's
  marks until the next poll. The earlier test reached the same save
  function only through the suggested-fix path. The new test fails against
  `verseEditor.ts` as of `bfd717f` and passes now.
- **`RULE_VERSION` is `language-qa-6` → `language-qa-7`.** This is
  informational, as with every earlier bump. Findings gained `source`, and
  verses with inline USFM are now scanned. Finding ids do not include the
  version, so they are unchanged. The frontend test fixtures still say
  `language-qa-6`, but those are fixture data, not assertions against the
  engine.
- **`docs/LANGUAGE_QA_REVIEW_FIXES_ACCEPTANCE.md`** gives step-by-step
  desktop cases for A52–A55. It includes a generated Titus fixture, because
  real IRV Philippians has only 31 findings and no `\wj`. Every expected
  number and text in it comes from importing that fixture in a scratch
  folder and scanning it: 244 findings, chapter 3's first mark at item 162,
  and the exact raw text after each Use.

~~Also noted by the audit: the brief asks for `docs/DECISIONS.md` to be read,
but that file does not exist in the repository.~~

**Correction (Phase 2 entry, below).** That was wrong. `docs/DECISIONS.md`
exists and always did; the check that "found" it missing ran from `engine/`
and looked for `engine/docs/`. It had not been read during the review fixes.
It has been read since, and none of its decisions bear on Language QA.

- Gates: `npm run check` 0/0; `npx vitest run` 507 passed; `npm run build`
  ok; engine `pytest -n auto` 1581 passed.
- Desktop acceptance not yet run.

### Regression from `ca4dc5f`: poetry line breaks were reported as control characters

**How it was found.** The layered-rules brief asks for a Psalms wall-time
and memory baseline before its Phase 1. The first measurement showed
something wrong: IRV Psalms produced 3,000 findings, 2,977 of them
`unicode.invisible`, and the book cap stopped the pass at chapter 87.
**Chapters 88–150 were never checked.** The Psalms SFM contains no
invisible characters at all; a survey of all 66 IRV books found one ZWNJ, in
1 Chronicles.

**Cause.** Chapter JSON keeps a verse's USFM line structure. Poetry is
stored like Psalm 23:1:
`யெகோவா என் மேய்ப்பராக இருக்கிறார்;\n\q நான் தாழ்ச்சி அடையமாட்டேன்.\n\q`.
Before `ca4dc5f` every such verse was skipped, because it contains a
backslash. Once it was lifted and scanned, the `\q` markers were removed,
but the line feeds stayed. `unicode.invisible` then reported each line feed,
because it is category `Cc`. I introduced this regression in the
inline-USFM fix, and nothing in its tests had poetry.

**Fix.** In USFM a line break is whitespace, so `\n` and `\r` are no longer
reported as control or unusual-space characters (`USFM_LINE_BREAKS`). A
line break still separates words, as any whitespace does:
- `அந்த\nகாகம்` is still a வல்லினம் candidate.
- `அந்த\n\q காகம்` is a crossing candidate, dropped and counted like any
  other.

**Verification.**
- New tests:
  - the Psalm 23:1 shape gives no control or unusual-space finding, and no
    limitation;
  - a bare line break still separates words;
  - `\q` between the two words is a counted crossing;
  - ZWSP, ZWNJ and BEL are still reported.
- Read-only rescan of all 66 IRV books, each imported into a scratch folder:
  - 0 skipped verses anywhere;
  - no floods;
  - 119.7 s for the whole Bible, imports included.
- Psalms: 2,461 of 2,461 verses checked, 226 findings, no limitations. That
  is 2.0 s wall and 68 MB peak working set; before the fix it was 1.1 s,
  but only because the pass stopped at chapter 87.
- Totals across the Bible:

  | Rule | Findings |
  |---|---|
  | `tamil.wordlist-variant` | 5,025 |
  | `tamil.vallinam-missing` | 523 |
  | `tamil.repeated-word` | 87 |
  | `spacing.extra` | 14 |
  | `punctuation.repeated` | 1 |
  | `unicode.invisible` | 1 (the real ZWNJ) |

- The only remaining limitations are crossing candidates, where `\wj`
  sits between two words. There are about 120 verses in Matthew, Mark,
  Luke, John, Acts and Revelation.
- Gates: `npm run check` 0/0; `npx vitest run` 507 passed; `npm run build`
  ok; engine `pytest -n auto` 1584 passed.

**Found, not fixed (reported).** `wordlist_findings` stops at
`MAX_WORDLIST_FINDINGS` (200) without adding a limitation. Larger books,
Psalms among them, reach exactly 200, so the cut is invisible to the
reviewer. It should become a reported limitation like every other cap.

## 2026-09-24 — Layered-rules Phase 1: finding model, category marks, menu, history, ignore-expiry

This is Phase 1 of the layered, data-driven Tamil rule brief. It closes the
gaps in the current model that every later layer depends on. It adds no new
linguistic rule, and changes no finding id and no rule's matching.
Prerequisites: the four review fixes, `e74eb2d`…`0887ce5`, plus the
line-break regression fix `ec9b55b`.

**Performance baseline (the brief measures it before Phase 1).** Measured
on this machine; IRV Psalms is imported to a scratch folder and given a full
Language QA pass:

| When | Wall | Peak working set | Verses checked | Findings |
|---|---|---|---|---|
| Before the line-break fix | 1.1 s | 46 MB | 1,364, truncated at chapter 87 | 3,000 |
| After `ec9b55b` (the real baseline) | 2.0 s | 68 MB | 2,461 | 226 |
| After Phase 1 | 2.0 s | 68 MB | 2,461 | 226 |

Phase 1 adds no measurable time or memory.

### 1.1 Finding model

- `language_qa.RULES` gives every existing rule a `RuleMeta`: pack
  (`common` / `ta-irv` / `project`), layer, category, confidence and its
  own `revision`. `rule_fields()` stamps these on every finding, from
  `scan_text`, the terminology pass and the wordlist audit alike.
- Each finding now carries:
  - `source`, `layer`, `category`, `confidence`;
  - `suggestions[]`, ranked, at most 5, each `{text, rank, source,
    rationale}`;
  - `ruleId`, pack-qualified, e.g. `ta-irv/tamil.vallinam-missing`;
  - `packVersion`, `ruleRevision` and `inline`.
- `rule` and `suggestedReplacement` stay for one release as aliases;
  `suggestedReplacement` is `suggestions[0].text` or None.
- `packVersion` is `RULE_VERSION` until the Phase 3 pack exists.
- `unicode.invisible` and `spacing.unusual` are at revision 2, because the
  line-break fix changed what they match.
- Confidence labels are categorical and provisional until the Phase 2
  benchmark measures each rule. They are not calibrated probabilities.
- `ruleRevision` is not in the brief's field list. Ignore-expiry (1.5)
  needs the rule's own version, and `ruleVersion` already means the pack
  version, so this was the least confusing name.

### 1.2 Inline indicator per category

- Each finding's `inline` flag, set by the engine from `INLINE_RULES`, is
  now the only authority on what is drawn; `languageQa.inline` filters on
  it.
- `highlight.ts` maps category → class through
  `LANGUAGE_QA_CATEGORY_MARKS`. A parity test checks it against the
  engine's `CATEGORIES`, and another checks `languageQa.ts`'s unions
  against `LAYERS` / `CATEGORIES` / `CONFIDENCES`.
- CSS, every style told apart by line style as well as colour:

  | Findings | Style |
  |---|---|
  | typo, high confidence | red wavy (`m-lqa-typo-high`) |
  | typo otherwise, and any lexicon finding | amber dotted |
  | sandhi / word-joining | green dashed |
  | punctuation / spacing | grey thin solid |
  | unicode | grey hatch |
  | termbase / name | purple double (`m-term`, unchanged) |

- **The yellow வல்லினம் highlight chosen in #173 is replaced by the green
  dashed underline.** The brief's table requires it. Flagged here because
  the yellow was a maintainer choice.
- Overlap: a segment keeps every finding id and every non-Language-QA
  class, but only one Language QA class, chosen by severity and then the
  table's category order (`languageQaMarkRank`). Language QA ids follow in
  rank order, so the menu opens on the primary finding. This replaces the
  stale "first-match-wins" comment.

### 1.3 Context menu

- The menu offers:
  - one `Use "<suggestion>"` per ranked suggestion, at most 5, with the
    rationale as tooltip;
  - **Edit…**, which opens the editor with the flagged span selected. Code
    points are converted to UTF-16 by `codePointToUtf16`, tested with an
    astral-plane character before the span;
  - **Ignore this occurrence**;
  - **Mark as false positive**.
- A false positive is decision `rejected`. It is hidden like an ignore and
  listed in the panel's own **False positives** list.
- Decisions now apply to every rule's findings, not only the two inline
  ones (`apply_decisions` / `decision_effect`). It is one code path.
- The decision payload carries source, rule, ruleId, pack version, rule
  revision, layer, category, original text, the chosen suggestion and its
  rank, message and span.
- **No click waits on the engine.**
  - Ignore and False positive drop the mark first and restore it only if
    recording fails (`decideLanguageQaFindingOptimistically`).
  - Use shows the corrected verse and closes the editor before
    `verse.edit` answers (`saveVerseEdit({optimistic: true})`). It rolls
    back the text and everything derived from it if the save fails.
  - A typed save still waits, so a refused save can be fixed in place.
- **Ignore ▸ this word in this book / this rule for this project are not
  built yet.** They need Phase 6's house-style store, so the item is a
  plain "Ignore this occurrence" until then. A one-item submenu, or two
  disabled items, would be worse for translators.

### 1.4 Decision history

- New `languageQa.history(projectPath, chapter, verse, findingId?)`,
  project-guarded, with parameters validated. It reads each Language QA
  decision row's append-only `change_log` images
  (`TranslationCoreProject.language_qa_decision_history`) and returns
  every decision in order: verdict, chosen suggestion and rank, time,
  revision, ruleId.
- The panel shows it under each finding (History toggle); the verse
  right-click menu has **Language QA history…**. Both load in the
  background behind a placeholder.
- Decisions recorded before issues carried a source are not listed; they
  cannot be told apart from Greek Room ones.

### 1.5 Ignore-expiry

- An "ignored" or "rejected" decision recorded under a different
  `packVersion` or `ruleRevision` is not re-applied. The finding comes
  back with `previouslyIgnored: true` and is listed in the panel's
  **Re-check** list; `languageQa.status` gains `view`.
- Decisions from before Phase 1 recorded the pack version as `ruleVersion`,
  and that is compared too.
- A decision with no version at all still suppresses, because it cannot be
  compared.
- **Consequence to know about:** every `RULE_VERSION` bump now sends every
  existing ignore back for re-checking, as the brief specifies. The ignores
  recorded before this commit, under `language-qa-6`/`-7`, are listed for
  re-check where their version differs.

### 1.6 Termbase and cache scope

- `decisions_version` was already scoped to Language QA decisions
  (`0da441a`).
- `terminology.record` from the Settings pane no longer replaces an
  existing concept silently. Without `overwrite: true` it writes nothing
  and returns the existing rule as `conflict`; the pane asks inline
  (Replace / Keep existing).
- `TranslationCoreProject.record_terminology_rule` itself still upserts, as
  its own test pins; the guard is on the Settings path.

### Performance contract

- Delivered in this phase:
  - no Language QA menu action waits on the engine;
  - the inline poller patches the store per verse
    (`patchInlineFindings`). An unchanged verse keeps its array, and an
    unchanged poll writes nothing. A Svelte store notifies on every object
    write, same reference or not, so an unchanged answer now wakes no
    subscriber.
- **Not yet done, and not claimed:**
  - one status channel in place of three pollers;
  - the p95 foreground-RPC and click-to-paint measurements;
  - their CI gates.

  They need the Phase 2 measurement harness and are scheduled there.

### Verification

- Engine (`test_language_qa.py` with `test_terminology.py`: 326 passed).
  New tests cover:
  - finding shape for every producer, and the metadata registry;
  - category and type parity with the frontend;
  - the inline flag;
  - `decision_effect` over twelve cases;
  - `apply_decisions`;
  - false positives, hidden and listed;
  - decisions applying to a non-inline rule;
  - pack-version and rule-revision expiry, and re-deciding;
  - status view validation;
  - history order, chosen suggestion, revisions, filtering, validation and
    project guard;
  - terminology conflict and overwrite.
- Frontend (535 passed, up from 507). New or updated tests cover:
  - class per category, all 11 cases;
  - overlap priority in both orders, and the tie-break order;
  - coexistence with other sources' classes;
  - the menu with 0, 1 and 5+ suggestions, Use of the second suggestion
    recording its rank, Use changing the verse before the engine answers,
    Use rolling back on failure, and a false positive restored on failure;
  - Edit… selection past an astral character;
  - verse-menu history;
  - the panel's re-check and false-positive lists, and history loading
    lazily;
  - per-verse store patching;
  - the Settings conflict flow.
- The fixtures now come from one builder, `languageQaFixture.ts`.
- Gates: `npm run check` 0/0; `npx vitest run` 535 passed; `npm run build`
  ok; engine `pytest -n auto` 1613 passed.
- Desktop acceptance not yet run. `LANGUAGE_QA_REVIEW_FIXES_ACCEPTANCE.md`
  is updated for the new mark style and menu labels.
- Also fixed: the audit follow-up's gate lines had been left after the
  regression entry in `ec9b55b`; they are moved back to their own section.

## 2026-09-24 — Layered-rules Phase 2: benchmark harness, latency gate, one status channel

This is Phase 2 of the layered-rules brief. It measures before any rule
changes. No rule logic changed in this phase.

### Inputs, and a maintainer decision about them

The reports: `D:\Claude Lab\Revant work\Claude outputs`, 16 Round 2 CSVs
and 5 Pass 3 CSVs, 9,334 rows in total.
- The Round 2 CSVs cover GEN–2CH, EZR, RUT and PSA.
- The Pass 3 CSVs cover GEN, EXO, LEV, NUM and DEU.
- `v1-before-split/` is not read: superseded.

Also used: the human-reviewed Philippians CSV, and all 66 IRV books.

The maintainer ruled that every AI proposal is reliable and to be used, and
that contradicting results are "maybe". So:
- positives are all in-scope AI rows;
- "maybe" covers:
  - rows at one place proposing different fixes;
  - reversals;
  - rows flagging a form `IRV_Pass3_Handoff.md` §5 confirms is house style
    (`இந்த` + bare, `அந்த தேச-` bare, `-விட` bare, names in `-க்கு`, and
    numerals spelt out);
  - any human verdict against a row;
- there are no negatives.

The brief suggested seeding negatives from leads recorded as rejected
(php 1:29, 4:18). That does not hold:
- php 1:29 later went from Rejected to Confirmed;
- php 3:14 and 4:18 are real bare boundaries that B4 excludes only for
  scope;
- the maintainer had already said the Philippians dispositions are not
  linguistic ground truth.

So Philippians Rejected rows are "maybe" too. **Precision is therefore
agreement with the AI review, not accuracy**, and
`docs/LANGUAGE_QA_BENCHMARK.md` says so first.

### Harness

**Code.** `engine/tc_ai_bridge/language_qa_benchmark.py` holds the harness,
tested; `scripts/language_qa_benchmark.py` is the command line.

**Scanning.** Each reviewed book is parsed with `parse_scripture_file` and
`imported_verse_text`, the latter a new public wrapper around the import's
own flattening. It is then scanned by the app's own `LanguageQaManager`
over temporary chapter JSON. There is no second copy of the rules.

**Matching.** A finding is a true positive when it overlaps a positive row
at the same verse and the row's type is one the rule can find
(`RULE_BUCKETS`).

**Counting.** Per rule: precision strict and lenient, and false positives on
house forms. Per bucket: recall over anchored rows, with unanchored rows
counted separately.

**Outputs.**
- The full result goes to `benchmark/results/` (git-ignored): it quotes
  Scripture and review text, and lists every unmatched finding and row for a
  human to label.
- Aggregate numbers go to `benchmark/baseline.json`.
- The table goes into the doc between generated markers.

**`--gate`** fails when:
- an inline rule's strict precision is below 0.90;
- a rule falls more than 2 points below the baseline (rules with 10 or more
  findings only).

It runs locally only, by maintainer decision, recorded in `DECISIONS.md`.

**Labelled fixtures (2.4).** `engine/tests/fixtures/language_qa/labelled/`
holds five JSONL files, one per bucket, about 155 KB. Each has every
"maybe" row plus a strided sample of 40 positives, with verse text, span,
fix and origin. `test_language_qa_labelled.py` checks the format, and checks
that every positive credited to a current rule is still found by it: a
regression guard in CI.

### Baseline, before any Phase 3 change

| Rule | Inline | Findings | Strict precision | False positives on a house form |
|---|---|---|---|---|
| `tamil.vallinam-missing` | yes | 311 | **37.9%** (lenient 43.4%) | 77 of 193 |
| `tamil.wordlist-variant` | no | 2,473 | **2.0%** | — |
| `tamil.repeated-word` | no | 33 | 0% | — |
| `punctuation.repeated` | no | 1 | 100% | — |
| `unicode.invisible` | no | 1 | 100% | — |

- **The gate fails on the baseline.** The only inline rule is far below
  0.90. That is the finding Phase 3 exists to act on: house-form abstains
  and a proper-noun abstain are the obvious first levers, since 77 of its
  193 false positives are house forms.
- Recall, strict: typo 6.3%, sandhi 6.8%, punctuation 2.0%, name 0%, usfm 0%.
  Name and usfm have no rules yet.
- The wordlist audit is mostly sandhi variants (`அதைச்`/`அதைக்`) and
  proper names flagged as spelling variants. Phase 5's lexicon replaces it.
- All `tamil.repeated-word` findings are distributive reduplication, which
  the reviews treat as house style.

### Performance contract

**Latency benchmark.** `scripts/benchmark_language_qa.py` now times
foreground RPCs while a pass runs, on a synthetic 400-verse project. It
has:
- `--cores N`, which pins the engine to N cores via the Windows affinity
  API. RAM and disk throttling are **not** emulated.
- `--without-scan`, which samples the same RPCs with Language QA paused.
- `--gate`.

**Results, pinned to 2 cores, p95:**

| RPC | During a scan | Without a scan |
|---|---|---|
| `verse.decide` on a Language QA finding | 16.6 ms | 17.6 ms |
| `verse.get` | 3.4 ms | 2.1 ms |
| `languageQa.status` | 0.3 ms | 0.5 ms |
| `languageQa.inline` | 0.4 ms | 0.4 ms |
| `ping` | 0.5 ms | 0.1 ms |
| `verse.decide` on another finding | 85 ms | 121 ms |
| `verse.edit` | 412 ms | 247 ms |
| `project.open` | 174 ms | 195 ms |

- The gate covers the first five, which are the RPCs Language QA owns or
  runs beside. They pass, and run in CI (`ci.yml`, engine job).
- **The shared write paths are over the 50 ms budget with or without a
  scan, so the scan is not the cause.** Moving the scanner into a worker
  process, the brief's remedy, would not fix them.
- A cProfile of `verse.decide` puts the cost in the progress rollup. It
  opens a fresh SQLite connection per query (45 closes: 124 ms over 10
  calls) and commits, with an fsync, several times per decision (25
  commits: 156 ms).
- `verse.edit` is the journalled Scripture write.
- Both are outside Language QA and involve durable-write design, so they
  are **reported, not changed** (candidate follow-up issue). The Language QA
  UI does not wait on them anyway: Use is optimistic since Phase 1.
- `checks.status` needs a running check job; it is measured once Language
  QA is a check stage (Phase 4).

**Click budget.** A new `VerseList` test fires Use, Ignore and False positive
with the engine never answering. It asserts the screen changed in under
100 ms: this is the DOM change, since jsdom does not paint. It runs in CI.

**One status channel.**
- The engine cannot push: `sidecar.rs` drops a stdout line with no pending
  request id. So the brief's fallback applies.
- `languageQaInline.ts` is now Language QA's only poller. It makes one
  count-only `languageQa.status` call, published as `languageQaChannel`, at
  500 ms while a pass is queued or running and 10 s when idle. A local edit,
  decision or pause nudges it (`nudgeLanguageQa`).
- `LanguageQaPanel` no longer polls. It reads the channel, and fetches a
  page only when opened, paged, switched to another list, or when a new
  generation or state lands.
- Marks are fetched only for a completed pass not yet drawn, or on a
  chapter change. They no longer blink out during every rescan, which the
  old 5 s inline poll made them do.
- Two idle pollers every 2–5 s became one every 10 s.
- **Not Language QA, not changed:** `App.svelte`'s 800 ms navigation-sync
  poll runs whenever the app is open. The brief's "zero pollers awake when
  idle" cannot hold while it does; reported.

### Correction

My review-fix audit said `docs/DECISIONS.md` did not exist. It does: that
check globbed from `engine/`. The false claim in the audit follow-up entry
is struck through with the correction beside it. The file has been read
now: nothing in it bears on Language QA. It gains four entries: the
benchmark's labels, the local accuracy gate versus the CI latency gate, the
polled channel, and ignore-expiry.

### Verification

- Engine: `test_language_qa_benchmark.py` (9 tests, on a synthetic book and
  report), covering:
  - labels by scope, verdict and contradiction;
  - the three contradiction kinds, including digits;
  - matching against compatible rows only;
  - recall over anchored rows;
  - listing of unmatched findings with their house form;
  - the gate's two conditions and small-rule skip;
  - that the baseline carries no text;
  - that labelled examples quote their verse;
  - the CLI's exit codes and outputs.

  `test_language_qa_labelled.py` adds 489 parametrized checks over the
  committed fixtures.
- Frontend:
  - the channel tests: marks redrawn only for a new completed pass and kept
    mid-pass, active versus idle cadence, a nudge, errors on the channel, no
    refetch for an unchanged pass, per-verse patching;
  - the panel tests, driven by the channel: no request while collapsed, a
    page refetched only for a new generation;
  - the click-budget test.
- Gates:
  - `npm run check` 0/0;
  - `npx vitest run` 540 passed;
  - `npm run build` ok;
  - engine `pytest -n auto` 2111 passed, previously 1613: 9 harness tests,
    489 fixture checks, 0 new warnings;
  - `benchmark_language_qa.py --gate --cores 2` passes;
  - `language_qa_benchmark.py --gate` **fails**: the baseline finding above.
- Desktop acceptance not yet run.

## 2026-09-24 — Layered-rules Phase 3: the `ta-irv` rule pack, B1–B4 migrated, IRV defect rules

This is Phase 3 of the layered-rules brief. The Tamil rules move out of Python
into a data pack. The single B1–B4 rule becomes shape rules with corpus
abstains, and the known IRV defect shapes from the reports become rules. The
schema is in `docs/LANGUAGE_QA_RULE_PACK.md`.

### The pack

- **Location.** `engine/tc_ai_bridge/language_packs/`: the loader, plus
  `ta-irv/pack.json` and 11 rule files, about 84 KB.
- **Loading.** The pack loads lazily, once per process (`default_pack`,
  `lru_cache`): 162 ms, within the 200 ms budget.
- **Self-test.** Every rule's examples run through the real `scan_text` at
  load, and a failure raises `PackError` naming the rule and the example's
  origin.
- **Validation.** Unknown keys, bad regexes, a raw-text match outside the
  integrity layer, and an `inlineSignOff` without `by`/`date` are all refused.
- **Primitives.** The closed set: token-context (a word pair across
  whitespace only, with the previous word split into base and link, and
  `link` = none/mismatch/any) and regex (visible text or raw text). Fixes
  are insert-link, replace-link, fuse-link, replace and expand.
- **Generation.** `scripts/build_ta_irv_pack.py` builds the pack from the 66
  IRV books and the review reports, and the output is committed.
  - Abstains carry their corpus counts in `origin`.
  - Examples are real verses, preferring places the reviews also flagged.
  - Where a shape has fewer than twelve real occurrences (wrong-consonant,
    clitic, vowel-drop, dropped-tha), the rest are derived from a real verse
    and labelled "derived from …".
- **Wiring.**
  - `scan_text(..., pack=, lists=)` calls `pack.pair_candidates` in its word
    loop and `pack.regex_candidates` once per verse.
  - `VALLINAM_TRIGGERS` and the hard-coded B1–B4 block are removed.
    `INLINE_RULES` keeps only `terminology.deprecated-form`; pack rules carry
    their own `inline`.
  - `language_qa_jobs.project_rule_pack` applies the project overrides. The
    pack fingerprint is appended to the per-chapter cache key.
  - The summary gains `rulePack` and `inlineRules`.
- **Frozen build.** `engine/bridge-engine.spec` lists the pack as `datas`,
  because import analysis cannot see JSON. Verified on a scratch PyInstaller
  build of the spec: the frozen exe opened a Tamil project and completed a
  pass reporting `rulePack: ta-irv@1.0.0`. It flagged `அந்த காகம்` →
  `அந்தக் காகம்` and abstained on `அந்த தேசத்தில்`.

### The rules

| Rule | From | Inline | Strict precision | Findings |
|---|---|---|---|---|
| `sandhi.vallinam.demonstrative` | B1 | yes, signed off | 41.2% (lenient 55.0%) | 80 |
| `sandhi.vallinam.manner-adverb` | B1 | yes, signed off | 15.4% | 13 |
| `sandhi.vallinam.accusative` | B2/B4, generalised to any -ஐ form | yes, signed off | 55.0% | 500 |
| `sandhi.vallinam.dative` | B3, generalised to any -க்கு form | yes, signed off | 49.8% | 396 |
| `sandhi.vallinam.wrong-consonant` | new | yes, signed off | no benchmark findings | 0 |
| `sandhi.clitic.fused` | new | no | 0% (0 of 4) | 4 |
| `typo.divine-name.vowel-drop` | Round 2 defect | no | 100% | 11 |
| `typo.divine-name.dative-stem` | Round 2 defect | no | 69.2% | 13 |
| `typo.suffix.dropped-tha` | Round 2 defect | no | 100% | 2 |
| `integrity.space-before-note-end` | Round 2/Pass 3 USFM rows | no | 74.3% | 74 |
| `integrity.digits-in-text` | — | disabled | — | — |

**Before and after, on the four migrated vallinam rules taken together:**

| | Findings | Strict TP | Strict precision | Sandhi recall |
|---|---|---|---|---|
| Phase 2 baseline | 311 | 118 | 37.9% | 6.8% |
| Phase 3 | 989 | 507 | 51.3% | 28.3% |

House-form false positives on the demonstrative rule fell from 77 to 13.
Recall rose in other buckets too: typo 6.3% → 8.9%, punctuation 2.0% → 18.0%,
usfm 0% → 24.4%. Name recall is still 0%.

### The maintainer's decisions in this phase, and how each was applied

**All வல்லினம் rules inline.** The maintainer said: "Can we keep all
வல்லினம் inline. if it is false possitive the user will click ignore. then
the system will lean from its mistake."

- Every vallinam rule carries an `inlineSignOff` with the precision it was
  signed off at.
- The benchmark gate now holds a signed-off rule to within 2 points of that
  figure, rather than to the 90% floor.
- The 90% floor still applies to any unsigned inline rule.
- A sign-off with no measured precision fails the gate. None does today:
  wrong-consonant records `null` precision and has no findings, so the gate
  has nothing to hold it to. It must be measured once it has findings.
- Learning from ignores is Phase 6.4, which the maintainer approved here.

**"Army is a plain noun": a rule, not a list.** The maintainer asked for a
rule that tells root nouns from case forms, as the வல்லினம் decision about
படை had done. The root-noun test (`root_nouns` in the builder) is a
morphological one, run on the corpus:

- A word ending in ை is a root if its +யை accusative is attested
  (படை→படையை, மலை→மலையை). A real accusative never takes a second one: there
  is no அதையை.
- A word ending in க்கு is a root if its -க்கில் locative or -க்குக்கு dative
  is attested (கிழக்கு→கிழக்கில்). A real dative has neither: there is no
  எனக்கில்.

The resulting words are a `notLexical` condition on the accusative, dative,
wrong-consonant and clitic rules. `ஈசாக்கு` and `ஏனோக்கு` are added from
Pass 3 §5 (names in -க்கு are nominatives).

**Also abstaining:**

- house forms, from the bare/doubled counts per trigger and stem (at least 3
  contexts, bare majority): for example அந்த தேச- (bare 31, doubled 8) and
  அந்த தேவ- (3/0);
- clitics and quotatives (தான், கூட, மட்டும், ஆவது, போல, என்று, என,
  எனும்);
- `housestyle.properNouns`.

### Rejected, and why

- **Seeding `housestyle.properNouns` from the names adapter.** Its majority
  forms include common words, and it takes about 15 s per book. The list
  ships empty until Phase 6 (DECISIONS.md).
- **Enabling `integrity.digits-in-text`.** Pass 3 §5 confirms digits are IRV
  house form, and IRV has 2,423 of them. The rule ships disabled, and an
  override cannot enable it.
- **A new rule for ZWNJ.** It stays under `common/unicode.invisible`, with
  no pack duplicate.

### A bug found and fixed on the way

`decision_effect` split a pack `ruleVersion` (`ta-irv@1.0.0#1`) the way it
splits a legacy one (`language-qa-7#2`). It therefore read a decision made a
moment earlier as recorded under another version, and a **fresh ignore
immediately came back as `previouslyIgnored`**. It now parses both shapes. A
regression test ignores a pack finding and checks that it stays suppressed.

### Behaviour change a translator will see

Existing B1–B4 findings keep their `rule` (`tamil.vallinam-missing`, through
`legacyId`) and their finding ids, so their decisions still match. Their
`ruleVersion` changed, though, so **every existing வல்லினம் ignore comes back
once for re-check**, flagged as previously ignored. This is the Phase 1
ignore-expiry working as designed. There are no users yet, but it is recorded
here so it is not mistaken for a regression.

### Docs

- New: `docs/LANGUAGE_QA_RULE_PACK.md`, the schema.
- `docs/LANGUAGE_QA_TAMIL_SPECIFICATION.md` is replaced by an owned version.
  The six gates and 56 items are kept as tables with Status, Owner and pack
  ids. The 20 `utm_source` links are removed, and the sources are listed
  once.
- DECISIONS.md gains five entries: rules as data; overrides only narrow; the
  inline sign-off; the root-noun test; the proper-noun seed rejected.
- Also updated: `LANGUAGE_QA_PLAN.md` phase table; the
  `LANGUAGE_QA_BENCHMARK.md` history row and regenerated results;
  `ARCHITECTURE.md` §9; QA matrix A63–A66.
- Labelled fixtures and `benchmark/baseline.json` are regenerated against
  the pack. These are benchmark data, not goldens, and neither golden moved.

### A latency regression caught by the gate, and fixed

The first run of `benchmark_language_qa.py --gate --cores 2` on the pack
**failed**. `languageQa.status` p95 was 82 ms, where Phase 2 measured 0.3 ms.

**Cause.** `status()` asked `inline_rule_names()`, which called the lazy
`default_pack()`. That was an `lru_cache`, which does not serialise
concurrent first calls. A poll arriving during the worker's first load
therefore ran a second full load itself, including every example (about
160 ms).

**Fix.**
- `default_pack` is now a lock-guarded load-once.
- A new `loaded_pack()` returns the pack only if it is already loaded.
- The manager's `_inline_rules` never loads the pack. Before the pass has
  loaded it, no pack finding exists, so the non-pack list is the right
  answer.
- The pass publishes its `inlineRules` as soon as it has the pack.

**After:** `languageQa.status` 0.34 ms. A regression test covers both
halves: a status request loads nothing, and four concurrent first calls load
once.

### Verification

- **Engine.**
  - `test_language_pack.py`: 41 tests, covering:
    - the pack loads, and every example passes;
    - legacy names and ids;
    - inline rules are exactly the signed-off ones;
    - the case rules on real case forms;
    - roots, names, house forms and clitics abstain;
    - wrong-consonant, and consonant-final names left alone;
    - clitic fusion;
    - the defect shapes;
    - the raw-offset note rule;
    - digits disabled;
    - the house-style list abstain;
    - eleven malformed-rule refusals;
    - failing and wrong-fix examples refuse the pack;
    - override narrowing and refusal, and an override file honoured by the
      manager;
    - the latency regression above.
  - `test_language_qa.py` / `test_language_qa_benchmark.py` /
    `test_language_qa_labelled.py` were updated for the pack's rule ids and
    sign-off.
  - Language QA files: 868 passed.
  - Full `pytest -n auto`: 2155 passed after the latency fix (2154 before
    it), 0 warnings.
- **Frontend** (no frontend change in this phase):
  - `npm run check` 0 errors / 0 warnings;
  - `npx vitest run` 540 passed;
  - `npm run build` ok.
- **Gates.**
  - `language_qa_benchmark.py --gate` **passes** (exit 0) on the sign-offs.
  - `benchmark_language_qa.py --gate --cores 2` **passes** after the fix. p95:
    `verse.decide` (Language QA) 15.5 ms, `verse.get` 4.0 ms,
    `languageQa.status` 0.34 ms, `languageQa.inline` 0.3 ms, `ping` 0.44 ms.
  - The shared write paths are still over budget, as in Phase 2 and outside
    Language QA: another `verse.decide` 262 ms, `verse.edit` 238 ms,
    `project.open` 180 ms. These numbers are noisy on a loaded machine.
- **Frozen.** A scratch PyInstaller build of `bridge-engine.spec` finds the
  pack and produces the வல்லினம் finding (above).
  `scripts/smoke_sidecars.py` was not run: the USFM checker exe was not
  rebuilt.
- **Desktop acceptance: not run.** Watch in particular for existing
  வல்லினம் ignores coming back once for re-check, which is expected.

## 2026-09-24 — Layered-rules Phase 4.1: Language QA as a check-job stage, persisted

This is Phase 4.1 of the layered-rules brief. Language QA stops being a
side-car: its findings are produced by a stage of the ordinary check job,
count in the progress rollup, and persist across reopen.

### One scan, one cache, two callers

**The scan.** `language_qa_jobs.scan_verse` is the pure per-verse scan,
factored out of the old `_scan` loop. It returns a verse's raw findings
(before decisions), its coverage notes, and its word counts for the
wordlist audit. A pass then:
1. reuses a verse's entry while its text hash and the chapter key are
   unchanged. The chapter key covers `SCAN_CACHE_VERSION`, `RULE_VERSION`,
   the detected language, the pack fingerprint (overrides included) and the
   termbase;
2. rescans the verses that changed;
3. assembles the book, applying decisions and the book finding cap afresh
   every time.

**The persisted cache.**
- It lives in the new workbench table `language_qa_cache`, one row per
  chapter (**workbench v3 → v4**, forward block `_MIGRATION_V4`, plus a
  v3→v4 test).
- It does not use `check_cache`: every reader of that table loads all of a
  book's rows, and 150 chapter payloads would ride along. See DECISIONS.md.
- The manager keeps an in-memory mirror, read once per bind in one query.
- Rescanned chapters are written in one transaction per pass. With more than
  50 chapters (`FLUSH_CHAPTERS`) the write is chunked.
- A cancelled pass still flushes what it computed, because each entry is
  keyed by its own text.
- `status.storage` now reads "Persisted in the project workbench."

**What changes for decisions and edits.**
- Decisions are no longer part of any cache key, so **a decision rescans
  nothing**. Previously any Language QA decision rescanned the whole book
  (`decisions_version`).
- A live edit rescans **only the edited verse**, which is the brief's
  verse-level live path. `invalidate()` no longer discards anything; it only
  schedules a pass.

**The job stage.**
- `checks.start` accepts `languageQa`. `check_jobs._stages` adds a
  "Language QA" stage after "QA".
- The preflight runs `LanguageQaManager.run_pass`: the same scan and cache
  as the background worker, on the job's thread, serialised with the worker
  by a pass lock.
- Even a chapter job passes the whole book, because the wordlist audit needs
  every chapter. Unchanged verses come from the cache.
- Each verse's share (`{findings, decided}`) goes into the job result under
  **`languageQa`, not `findings`**, so everything that reads `findings` as
  QaFinding dicts is unaffected.
- `checks.status` gains a `languageQa` block (state, chapters, findings,
  limitations).
- The pass publishes a new generation, and the frontend nudges the status
  channel when a job ends, so the marks redraw from the job's result.
- The app's chapter and book jobs now request `["local", "greekroom",
  "languageQa"]`.

**Two departures from the brief, both recorded in DECISIONS.md.**
- The stage does not hold `_checker_lock`. The dispatcher takes that lock
  for `verse.runChecks`, and a save must not wait on a book pass.
- `_run_verse_checks_for_project` does not run Language QA. It returns
  `QaFinding`s, and a verse-level Language QA check is already the live
  path.

### Progress rollup

- `_on_check_job_complete` now counts Language QA findings: open ones as
  `open`, decided ones with their decision.
- The Phase 1 skip in `decide_verse` is replaced, as the brief requires.
  A Language QA decision now updates the rollup **when the rollup already
  has that finding**, that is, once a job has reported it.
- A decision on a finding no job has reported still adds nothing. Without
  that guard, a decision alone would add a finding row, and a verse could
  look reviewed on the strength of findings that were never counted. That
  was the original bug.

### Performance

**Psalms full pass**, measured with the Phase 1 script on this machine:

| When | Wall | Peak working set | Findings |
|---|---|---|---|
| Phase 3 | 2.2 s | 68 MB | 309 |
| Phase 4.1, first try: one transaction per chapter | **5.3 s** | 73 MB | 309 |
| Phase 4.1, one transaction per pass | 3.4 s | 74 MB | 309 |
| Phase 4.1, compact word map (final) | **2.45 s** cold | 70.5 MB | 309 |
| Phase 4.1, reopen (all from the cache) | **0.47 s** | 72 MB | 309 |

**Two budget misses, both fixed before commit.**
- **One fsync'd commit per chapter.** It more than doubled the pass, which
  breaks the "no phase may double wall time" rule. The fix is one
  transaction per pass.
- **The payload.** It was 3.45 MB for Psalms, and 2.64 MB of that was the
  wordlist's full first-seen location per word per verse. It is now
  `word → [count, start, end]`, with the text sliced from the verse at
  assembly: 1.18 MB, `SCAN_CACHE_VERSION` 2.

**Known cost, not fixed.** Every cache write also appends a `change_log`
row, as `check_cache` writes already do. A cache is regenerable, so that
history is not needed, but the workbench has one write path. Candidate
follow-up.

**Latency gate** (`--cores 2`): passes. Language QA `verse.decide` 18.2 ms,
`verse.get` 2.8 ms, `languageQa.status` 0.38 ms, `languageQa.inline`
0.32 ms, `ping` 0.41 ms. The 400-verse background pass takes 15.6 s, against
15.3 s recorded before.

### Verification

- **New engine tests.**
  - A decision rescans nothing, yet takes effect. This replaces the
    `decisions_version` test, whose premise is gone.
  - Results persist across reopen with nothing rescanned, and a live edit
    rescans exactly one verse.
  - The stage runs in a book job:
    - its findings sit under `languageQa`, and `findings` stays clean;
    - `checks.status` has the block;
    - the rollup holds the finding as `open`;
    - an ignore moves the open count down by one;
    - a re-run reports the finding as `decided`.
  - A Language QA decision never marks a verse reviewed while another
    finding is open.
  - The job path and the live path produce identical finding ids, and the
    job pass rescans nothing after a live pass.
  - A job without the stage carries no `languageQa`.
  - Workbench v3→v4 migration.
- **Suites and gates.**
  - `tests/service tests/jobs tests/persistence`: 1230 passed.
  - `npm run check` 0/0; `npx vitest run` 540 passed.
- **Not covered by a test:** the App's `beginChecks` list and the post-job
  nudge. There is no App-level test harness.

## 2026-09-24 — Layered-rules Phase 4.2: Language QA in the reports, the exception queue and the publication gate

This is Phase 4.2 of the layered-rules brief.

### One reader

**What a job leaves behind.**
- A job with the Language QA stage leaves each chapter's Language QA
  findings beside its QaFinding snapshot, in
  `check_findings.payload.languageQa`.
- This includes the findings decisions hide, each carrying its `decision`.
  The stage now returns them as `hidden`.
- A job without the stage keeps the chapter's previous Language QA share
  rather than erasing it.

**`language_qa_jobs.reported_language_qa(project)`** is the one reader of
that snapshot. It gives each finding its current status: the rollup's
status first (a decision made since the job updates it), then the decision
that hid it, then `open`. The QA report, the exception queue and the
publication gate all read through it, so they cannot disagree. Like the rest
of the report, it reads persisted state only and never scans.

### Where it shows up

**QA report** (`qa_report.py`).
- A new category, `languageQa`, and a `languageQa` block in each book's
  `checks`: open, resolved, blocking and open-medium counts, plus
  `byCategory` sub-rows (typo, sandhi, ...).
- The block is advisory, like AI review. It is not a scored family, so the
  collection's pass/fail totals are unchanged.
- Rows gain these columns, empty on non-Language-QA rows:
  `languageQaCategory`, `ruleId`, `packVersion`, `layer`, `confidence`,
  `suggestions` (joined with ` | `), and `houseStyleSuppressed`. The last is
  always empty until Phase 6.
- The CSV/TSV defaults and the report screen's export columns include them.

**Exception queue** (`analytics.py`).
- A verse with an open Language QA finding of severity high **or**
  confidence high enters the queue.
- The row carries `languageQa` (the count) and `languageQaFindings`.
- Ranking is `(-critical, -high, -languageQa, -invalidChecks, ...)`. The
  brief says "after AI critical issues, before tN/tW invalid checks";
  placing it after `high` as well keeps AI high issues above it.

**Publication gate** (`reporting.py`).
- Open Language QA findings with severity high **and** confidence high are
  a blocking input (`languageQaBlocking`). Today that is
  `terminology.deprecated-form` and nothing else.
- More than N open medium findings adds an advisory line (`advisories`) and
  never blocks. N comes from the `language_qa_medium_advisory` setting and
  defaults to 50. There is no Settings UI for it yet.
- The gate stays labelled advisory.
- The book report gains `languageQa`: totals, by category, and the blocking
  findings.
- The HTML report gains a Language QA section.
- `ReportService.export` also writes `<book>_translation_qa_report_language_qa.csv`.
- `collection.report` sums the Language QA totals and lists them per book.

**Frontend.**
- `ReportCategory` gains `languageQa`, with a label, a long label, a colour
  (`--lqa`) and a place in the legend order.
- `ReportRow` and `BookChecks` are typed for the new fields.

### Verification

- **Engine** (`test_qa_report.py`):
  - Language QA rows carry the rule columns and the CSV header;
  - an ignore through `verse.decide` resolves the row with no new job;
  - a termbase finding (high/high) blocks the gate and enters the exception
    queue;
  - the advisory line appears at 51 open medium findings, not at 50, and
    never blocks;
  - a job without the stage keeps the previous snapshot.
- **Frontend.** `reportStats` legend order includes `languageQa`.
  `npm run check` 0/0, report tests pass, `npm run build` ok.
- **Not covered by a test:**
  - The exception queue's ranking position for Language QA was verified by
    reading the code only. A queue with AI high issues and invalid checks
    needs AI review fixtures that no test here builds.
  - The HTML section was checked only by the report tests building without
    error. Nobody has looked at it rendered.

## 2026-09-24 — Layered-rules Phase 4.3: one review surface

This is Phase 4.3 of the layered-rules brief: a Language QA finding can be
acted on where every other finding is.

- **ReviewPanel: a "Language QA" tab.** It lists the selected verse's
  Language QA findings, inline or panel-only, from a new RPC,
  `languageQa.verse`.
  - That RPC returns one verse's share of the last completed pass: open
    findings, and `hidden` ones with the decision that hides them.
  - Each finding offers **Use "…"** for each ranked suggestion,
    **Ignore** and **False positive**. Use goes through
    `applyLanguageQaSuggestedFix`; Ignore and False positive go through
    `decideLanguageQaFindingOptimistically`, so a decision from either
    surface is the same `verse.decide` call with the same `issue` payload.
  - A finding leaves the list the moment its button is clicked, and comes
    back only if recording fails.
  - A "Decided (n)" section lists the hidden ones.
  - The tab refetches when the verse changes and when a new **completed**
    generation lands on the status channel, never on every tick.
  - `LanguageQaPanel` keeps the book-level lists and is no longer the only
    place a panel-only finding can be acted on.
- **A span with findings from two sources.** A right-click now opens one
  menu with a section per finding (for example "wildebeest: Mixed script",
  or "Language QA: அந்த காகம் — …"). Each section's submenu holds that
  finding's own actions. Before this, the QaFinding won and the Language QA
  finding on the same words could not be reached. A span with only one
  source keeps its old menu.
- **Keyboard.** Left/Right now walk Language QA marks too, in reading order
  with the other findings by display offset. Shift+F10 opens the Language
  QA menu on the store's raw finding (not the display copy), because the
  fix splices the raw verse.
- **✓ indicator.** A drawn Language QA mark counts as open, so a verse is not
  shown as clean while one is on it.
- The `highlight.ts` "first-match-wins" comment the brief names had already
  been corrected in Phase 1. The stale comment in `findingActions.ts`
  ("keeps the decision out of the rollup") is corrected for Phase 4.1.

### Verification

- **Engine.** `languageQa.verse` lists a verse's open findings. After a
  false-positive decision, the finding moves to `hidden` with `rejected`.
  The RPC refuses another project's path and a non-string chapter.
- **Frontend.**
  - New `ReviewPanelLanguageQa.test.ts` (3 tests):
    - a panel-only finding is listed, and Ignore removes it before the
      engine answers, sending the Language QA issue;
    - the tab refetches only on a new completed generation;
    - a failed decision puts the finding back and shows the error.
  - VerseList (3 new tests): the mixed-source menu reaches the Language QA
    action; Shift+F10 opens a Language QA mark's menu; no ✓ while a mark is
    drawn.
  - `npm run check` 0/0; `npx vitest run` 546 passed; `npm run build` ok.
- **Not verified:** how the new tab and the mixed menu look at 1366×768.
  jsdom does not lay out, so that needs the desktop app.

## 2026-09-24 — Layered-rules Phases 4.4 and 4.5: collection runner and export gate

### 4.4 `collection.runChecks`

**The runner** (`engine/collection_jobs.py`) follows the `check_jobs.py`
pattern:
- one active run, on a worker thread, with JSON snapshots;
- it owns ordering, skipping, pause, cancel and timing.

**The engine supplies the work.** `_run_collection_book` handles one book:
1. it materializes a lazy sibling;
2. it builds a fresh `TranslationCoreProject` and a Language QA manager bound
   with `autostart=False`;
3. it runs the ordinary whole-book check job on a **private
   `CheckJobManager`**, through the same `_start_check_job_from_spec`, now
   parametrised with its Language QA manager and job manager;
4. it waits, then releases the book.

The editor's open project, its Language QA and its check job are never
touched. The job's own completion hook writes the book's rollup and
snapshots as usual.

**Resumable.**
- Each finished book is upserted into the opened project's
  `.bridge/collection.json` → `qaRuns[]` as `{bookId, state, completedAt,
  jobId, contentHash, checks, elapsedSeconds, findingsByCategory,
  checkedVerses}`.
- The next run skips a book whose recorded run is `done`, whose content hash
  (sha256 of its chapter JSON files) is unchanged, and whose recorded checks
  cover the requested ones.
- `force` re-runs everything.
- A cancelled book records nothing, so a crash or cancel resumes at that
  book.

**Controls.**
- Pause holds the next book, not the one in flight.
- Cancel stops the book in flight after its current verse, through the
  check job's own cancellation.
- `checks.start` is refused while a run is active.
- One failing book does not stop the others.

**Final stage.** It runs once, after every book, and writes reports only:
- **termbase coverage** per book: approved renderings never used, and
  rejected ones still present. This is a substring count, because Tamil
  inflects the rendering.
- **cross-book name consistency**: the names adapter over the union of every
  book's tokens.
- **house-style propagation**: reported as unavailable until Phase 6.

Its summary is kept in `collection.json` → `qaFinalStage`.

**RPCs.** `collection.runChecks {checks?, force?}`,
`collection.qaStatus {jobId?}`, `collection.pauseChecks {paused}` and
`collection.cancelChecks`. With no run in the session, `qaStatus` returns an
idle snapshot built from `qaRuns[]`, so the screen shows each book's last
run after a restart.

**UI.**
- `CollectionQaPanel` appears on the dashboard for any collection with more
  than one book. It shows one row per book: state, verses checked, open
  findings by source, last run, time, and an Open-book link.
- It also shows elapsed time and an estimate from the measured books, plus
  Pause/Resume/Cancel and "Run all again" (force).
- `collectionQa.ts` polls once a second, and only while a run is active.
- While a run is active, `checkingProgress.running` is held (so editing,
  alignment and AI review are disabled) and `switchBook` refuses.

**The import offer.** The brief asks for "Run QA on all N books" from the
import summary. A multi-book import already lands on the dashboard, so the
panel's button is that offer. It is never started automatically
(DECISIONS.md).

**Whole-Bible wall time: 6031.8 s (1 h 41 min).** The run imports all 66
IRV books (6.8 s) and runs `collection.runChecks` with the default checks on
this machine.
- **Result:** `state=succeeded` on 2026-09-24; 66/66 books; no book error.
- **Size:** 31,092 verses and 7,401 open Language QA findings.
- **Time split:** the books took 5946 s, and the final stage (termbase
  coverage, cross-book names) about 80 s.
- Genesis took 260 s with the machine otherwise idle.
- Exodus took 650 s, and several other books ran slower, while the engine
  test suite and gates ran beside the collection. The figure is therefore
  an upper bound for an idle machine.
- After two books the runner estimated 5 to 8 hours. Its estimate fell as
  the shorter books ran.

This confirms the brief's "run-overnight operation" and the no-auto-start
decision. Most of the per-book time is the pre-existing checks, not
Language QA: a full Language QA pass on Psalms is 2.45 s cold.

The process started before Phase 6 was committed. Its final stage therefore
reported house-style propagation as "not built yet", so the Phase 6
proposals across 66 books are still unmeasured (see the Phase 6.2–6.5
entry).

### 4.5 Export gate

`reporting.publication_gate(project)` is the single definition of what
blocks an export:
- open AI critical issues;
- open Language QA findings with severity high **and** confidence high;
- tN/tW checks marked needs-discussion.

It reads persisted state only, because export runs on the dispatcher and the
full book report takes minutes. The book report embeds it as
`publicationGate.exportBlocking`.

`export.aligned` and `export.nonAligned` consult it first:
- With blocking items and no `override`, nothing is written. The answer is
  `{written: false, blocked: true, gate}`, a success response, because
  `EngineResponse.fail` cannot carry the items.
- With `override: true`, the export proceeds, and a `kind='qa'` decision
  with key `export.override` records the items open at that moment. It lands
  in `change_log` for the Phase 6.5 export ledger.

`ExportModal` lists the blocking items. "Export anyway" stays disabled until
the override box is ticked, then reuses the chosen path.

### Verification

- **Engine.**
  - `tests/jobs/test_collection_jobs.py` (7 tests):
    - every book runs in order, each is recorded, and the final stage runs
      once;
    - an unchanged book is skipped, while a changed book, fewer recorded
      checks or `force` all rerun;
    - cancel records nothing and skips the final stage;
    - pause holds the next book, and a second start is refused;
    - one failing book does not stop the rest;
    - a real three-book run records `qaRuns` and the final stage, and an
      edited book reruns while the others are skipped;
    - `checks.start` is refused during a run.
  - `test_qa_report.py`, export gate for both formats (2 tests): not blocked
    when nothing is open; blocked with nothing written; override writes the
    file and records the decision with its open items; the report's gate
    lists the same items.
- **Frontend.** `CollectionQaPanel.test.ts` (2 tests): last runs shown, never
  auto-started; the app is held read-only during a run and released after.
  `ExportModal.test.ts` (1 test).
- **Not covered by a test:** the App-level wiring (the panel's placement, and
  `switchBook` refusing during a run). There is no App test harness.

## 2026-09-24 — Layered-rules Phase 5: corpus lexicon, confusion-set distance, ranked suggestions

### 5.2 `tamil_distance`

`language_packs/tamil_distance.py` is a weighted edit distance over grapheme
clusters (`regex` `\X`), with NFC comparison keys. The text itself is never
normalised.

Each of these confusions costs 0.5; everything else costs 1:
- ர/ற, ல/ள/ழ, ண/ன/ந, with the same vowel sign;
- the independent vowels எ/ஏ, ஒ/ஓ, இ/ஈ, உ/ஊ;
- the same pairs as vowel signs (ெ/ே, ொ/ோ, ி/ீ, ு/ூ), which the brief did
  not list but is where most length confusions occur;
- ஐ/அய் and ஔ/அவ் (one cluster against two);
- pulli present or absent, and ா present or absent.

The brief's Round 2 typos come out at 1.0 (`உடன்பட்டிக்கையை`, one cluster
deleted) and 0.5 (`ராஜ்யாபாரமும்`, an ா).

The ai/அய் equivalence is between whole clusters only. A sign-level ை
against ய் inside a syllable (கை against கய்) is not modelled.

### 5.1 The lexicon

`scripts/build_tamil_lexicon.py` builds `language_packs/ta-irv/lexicon.json`
from all 66 IRV books, using the runtime's own tokens (`imported_verse_text`
→ `lift_inline_usfm` → `word_occurrences`):

| | |
|---|---|
| Verses / tokens / distinct forms | 31,092 / 470,413 / 79,208 |
| Listed (≥ 3 occurrences) | 19,618 |
| Common (≥ 6), with one-cluster deletion buckets precomputed | 9,515 words, 55,018 keys |
| Curated pairs (`--curated`, the Round 2 / Pass 3 reports) | 151 |
| Size / parse time / resident memory once loaded | 3.0 MB / ~150 ms / ~15 MB |

The curated pairs come from positive typo rows whose Original and Suggested
differ in exactly one word. Candidates were dropped in these cases:

| Dropped because | Pairs |
|---|---|
| the wrong form is common in the corpus (more than 2 occurrences) | 158 |
| the right form is unattested | 166 |
| the reviews disagree | 6 |
| the two are more than distance 2 apart | 20 |
| the row changed more than one word | 196 |

Some kept pairs are style or grammar edits rather than typos, for example
இதயத்தில் → இருதயத்தில். They are kept because the maintainer ruled every AI
proposal reliable. The rule's name and message call them "reviewed
corrections", not misspellings of fact.

The lexicon ships inside the pack directory, which `bridge-engine.spec`
already bundles, so there is no spec change. The data budget is recorded in
DECISIONS.md.

### 5.3 The rules

`language_packs/lexicon.py` defines two rules.

**`lexicon.rare-near-common`** (typo, confidence medium, severity low,
panel-only):
- It flags a word at most twice in the book and at most twice in the corpus
  (absent from the lexicon counts as rare) that is within **0.5** of a word
  occurring at least 6 times in the corpus and at least 5 times as often.
- It offers up to 5 suggestions, ranked by distance, then corpus count, then
  same-book count. Each carries its evidence: "occurs 1007× in the corpus,
  0× in this book (distance 0.5)".

**`lexicon.known-misspelling`** (typo, confidence high, severity medium):
it applies the curated map with the correction as the suggestion. Severity
is medium, so it never blocks the publication gate, which blocks only
high/high.

**Where the rules run.** In `_scan`, the lexicon audit replaces
`wordlist_findings` for the `ta-irv` pack. The within-book audit remains
the fallback for a pack without a lexicon, and its tests pin that fallback
with the lexicon switched off.

**The threshold was chosen by measurement.** At distance 1.0 (any
one-cluster edit), Tamil inflection flooded the rule: 2,931 findings at
0.8%. At 0.5 it gives 76 at 2.6%, against the old wordlist's 2,473 at 2.0%.

**A capping bug, found and fixed.** The two rules first shared one
200-finding cap, and the noisy rule consumed it: known-misspelling showed 59
findings instead of 140. Each rule is now capped separately.

### 5.4 Feedback, bounded

`scripts/lexicon_feedback_report.py` lists every Language QA decision on a
`lexicon.*` finding, across the project folders given: the Use choice, or a
false positive. It writes CSV for a person to fold into `--curated`. It never
writes the lexicon, and a test checks the file's bytes are unchanged
(DECISIONS.md).

### Benchmark (`--gate` passes; baseline and labelled fixtures regenerated)

| Rule | Findings | Strict precision |
|---|---|---|
| `lexicon.rare-near-common` | 76 | 2.6% |
| `lexicon.known-misspelling` | 140 | 92.9%, **not independent**: built from these reviews |
| `tamil.wordlist-variant` (Phase 4, replaced) | 2,473 | 2.0% |

Typo recall is 20.9%, up from 8.9%, mostly through known-misspelling, which
carries the same caveat. Sandhi, punctuation and usfm are unchanged.

### Performance

These are Psalms full passes, measured while a whole-Bible collection run
was also using the CPU. They are comparable with each other, not with
earlier, unloaded figures.

| | Cold | Reopen | RSS after | Findings |
|---|---|---|---|---|
| Lexicon off | 3.16 s | 0.60 s | 51 MB | 309 |
| Lexicon on | 3.19 s | 0.56 s | 66 MB | 149 |

The lexicon adds no measurable wall time and about 15 MB RSS, within the
contract's 50 MB.

### Verification

- **Engine.** `test_lexicon.py` (30 tests):
  - each confusion pair costs 0.5 in both directions;
  - other edits cost 1;
  - the Round 2 typos come out at 1.0 and 0.5;
  - distance is measured over clusters;
  - the lexicon is bounded, and its buckets are precomputed;
  - the curated map keeps only safe pairs;
  - it loads within budget;
  - ranked suggestions carry evidence;
  - a common word is never flagged;
  - a known misspelling is high confidence;
  - the ratio guard holds;
  - the manager uses the lexicon for a Tamil book;
  - the feedback report lists decisions and does not change the lexicon.
- `test_language_qa.py` and `test_language_qa_benchmark.py` run with the
  lexicon off, as the fallback and synthetic-harness tests they are.
- `test_language_qa_labelled.py`: the regenerated fixtures now credit five
  typo positives to the lexicon rules. These are book-level, so the test
  runs them through `lexicon_findings` over the example verse (a book of
  one) instead of `scan_text`. This was caught by the first full run: 5
  failed, 2202 passed.
- Full engine suite: 2202 passed, plus the 489 labelled checks, which pass
  after that fix.

## 2026-09-24 — Layered-rules Phase 6.1: termbase v3

`record_terminology_rule` writes **`schemaVersion` 3**, which adds two
fields:
- `inflectedForms`: a rejected rendering → its forms.
- `matchMode`: `exact` (the default) or `prefix`.

The `terminology.record` RPC takes both, plus `allowedAlternatives`, and
refuses an unknown mode. A v2 row reads as `exact` with no forms, so there
is no migration and no data change.

**Matching** (`terminology.TermIndex`, pure).
- **Listed forms** are indexed as exact tokens and are as authoritative as
  the rendering: confidence high.
- **`prefix`** generates each rejected rendering's inflected forms from a
  closed list, `CASE_SUFFIXES`: ஐ, க்கு, உக்கு, இல், ஆல், ஓடு, உடன், இன்,
  உம், இடம், இலிருந்து, கள், களை, களுக்கு, களின், களால், களோடு, களும்.
  - `join_suffix` joins them as written Tamil does: a pulli-final stem and a
    vowel-initial ending fuse into one syllable, so தேவன் + ஐ = தேவனை and
    தேவன் + உக்கு = தேவனுக்கு. A multi-word rendering inflects at its last
    word.
  - Such a match is marked **confidence medium**, and its message names the
    ending, so the reviewer confirms it.
  - The suggestion is the preferred form with the **same ending**: தேவனை →
    இறைவனை.
  - A generated form never displaces a listed one.
  - This generates forms rather than stripping suffixes from the text, so a
    match is still an exact token. Nothing matches by guesswork, and a form
    the closed list cannot produce is added by hand.
- **Suggestions.** They are ranked: preferred renderings first (so
  `approvedRenderings[1:]` now appear), then allowed alternatives, each with
  its rationale and each inflected alike.

**Settings → Terminology.**
- Each rule shows its allowed alternatives, its inflected forms, and
  "also with case endings".
- **Edit** loads a rule into the form, including its inflected forms (one
  line per rendering: `rendering: form, form`) and the prefix checkbox.
  Saving an edited rule replaces it.
- Adding a new concept that collides with an existing rule still asks first
  (Phase 1.6).

### Verification

- **Engine.** `test_terminology.py` has 52 tests:
  - `join_suffix` on five real joins;
  - a prefix match of தேவனை at medium confidence, with suggestions
    இறைவனை / கடவுளை / ஆண்டவனை;
  - exact mode leaves case forms alone;
  - a listed form matches at high confidence;
  - the RPC round trip, with an unknown mode refused;
  - schemaVersion 3 in the round-trip test.
- **Frontend.** `SettingsModal.test.ts` (17 tests): editing shows the v3
  fields and saves them with `overwrite`. Three existing expectations were
  updated for the new fifth argument.

## 2026-09-24 — Layered-rules Phases 6.2–6.5: house style as data, the learner, the name pack, the export ledger

The design is in `docs/LANGUAGE_QA_HOUSESTYLE.md` and the two DECISIONS.md
entries of this date.

### Storage: workbench v4 → v5

House-style entries are `human_decisions` rows of a new kind, `housestyle`.

**The migration.** `kind` carried a CHECK constraint, which SQLite cannot
alter, so `_MIGRATION_V5` rebuilds the table:
- the widened CHECK is on a new table;
- every row is copied with its columns named;
- the old table is dropped and the new one renamed;
- the one index is recreated.

It gets a **data-preservation test** (v4→v5), because a rebuild is exactly
what a reader should not take on trust. The test checks:
- every column and revision of a planted row survives;
- the natural unique key still holds;
- `housestyle` is writable;
- an unknown kind is still refused.

`tc_project.record_housestyle_entry` writes the entry;
`housestyle_entries` reads them. Remove, Undo and confirm-imported write a
new state onto the same row, with no delete, and change_log keeps each
state.

### Applying it (`housestyle.house_style`, in `_scan`)

**At assembly** (like decisions), so no change rescans:
- word entries hide the rule's findings on that text;
- rule entries hide the rule;
- a learned preference ranks a suggestion first, never adding one.

A hidden finding goes to the verse's `hidden` list with
`houseStyleSuppressed`, and counts as decided in the rollup. The status
carries `houseStyleSuppressed` per rule.

**The lists** (`housestyle.properNouns`) feed the pack's proper-noun abstain
inside the verse scan. So the lists' fingerprint joins the chapter cache
key, and a change to them rescans.

### The learner (`HouseStyleLearner`)

**Incremental.** Each Language QA `verse.decide` recomputes only its (rule,
word) pair. The pair map is built once per book from its decisions and
forgotten on `project.open`.

**Learning.** 3 ignores or false positives with no Use since create a
learned `word-in-book` entry at once. The decision's answer carries
`houseStyle.learned`, and the verse list shows "Learned: … — Undo". An undone
or removed pair is never learned again.

**Proposals** (`project_proposals`). These are computed when Settings opens
and in the collection run's final stage, which now reports real proposals
instead of "not built yet":
- word-in-project, once a pair is learned in 2 books;
- rule-in-project, at 20 or more decisions with 80% or more ignored.

**Preferences** (`preferences_from`) come from 3 Uses of the same
suggestion.

### RPCs and UI

**RPCs.** `housestyle.list`, `housestyle.record` (a project scope is written
to every materialized book), `housestyle.setState`,
`housestyle.nameSuggestions`, `housestyle.export`, `housestyle.import`.

**UI.**
- Verse menu: a new **Ignore more widely ▸** submenu offers the four scopes.
  The occurrence is ignored at once, and the explicit entry is recorded
  after.
- Settings → Terminology → **House style**:
  - entries with a provenance badge (or "imported"), scope, evidence count,
    Remove and Confirm;
  - proposals with Accept and Dismiss (Dismiss records `removed`, so it is
    not proposed again);
  - "Add a proper noun";
  - suggested names from the names check, each approved one by one;
  - Export… and Import….
- **A new Tauri command, `pick_json_file`**, for Import. `cargo check`
  passes.

### The name pack (6.2)

- The approved `properNouns` list is **curated**. Names are added in
  Settings, or approved from the names check's **cached** majority spellings
  (`name_suggestions` reads `check_cache` and never runs the adapter). The
  Phase 3 finding stands: those majority forms include common words, so
  none is added unreviewed.
- The list abstains the வல்லினம் rules.
- A new rule, `project/name.minority-spelling` (category name, housestyle,
  medium, panel-only), flags a word at most twice in the book that is within
  distance 1.0 of an approved name, and suggests that name.

### The benchmark (`--housestyle`)

`scripts/language_qa_benchmark.py --housestyle <project>` applies a
project's style.
- Hidden findings are reported per rule, and never counted as a true or
  false positive.
- The project's false-positive marks are exported as reviewer-labelled
  negatives, `benchmark/results/<date>-reviewer-negatives.jsonl`.

### The export ledger (6.5)

Both exports now write `<book>.language-qa-changes.csv` beside the file. It
lists each Language QA Use (chapter, verse, rule, original, replacement, time,
actor) and each export made over the gate. It is built from change_log, so
a Use of a finding decided again later is still listed.

### Verification

- **Engine.**
  - `test_housestyle.py` (13 tests): validation; application;
    preferences; the thresholds at their boundaries (2 vs 3 ignores, a Use
    resetting the streak, a false positive counting, 1 vs 2 books, 19 vs 20
    decisions, 0.75 vs 0.80, 2 vs 3 Uses); undone never re-learned; the
    scoped ignore through the engine; the learner through `verse.decide`
    with Undo; export/import marking entries imported until confirmed; the
    ledger; the name pack.
  - `test_language_qa_benchmark.py`: suppressions are reported, not scored.
  - Workbench v4→v5 test.
  - `test_collection_jobs.py`: propagation proposals.
  - `test_qa_report.py`: the ledger records an export override.
- **Frontend.**
  - `SettingsModal.test.ts` (19 tests): house style shows provenance and
    evidence; Accept, Remove, curated name.
  - `VerseList.test.ts` (59 tests): the scoped Ignore records house style;
    the learned notice's Undo.
- **Not covered by a test:** proposals across 66 real books. Settings opens
  every materialized sibling's workbench to compute them, on the dispatcher.
  That is fine for a few books and unmeasured for a Bible.
- **Full gates:**
  - engine suite: 2230 passed;
  - svelte-check: 0 errors, 0 warnings;
  - Vitest: 554 passed;
  - `npm run build`: ok;
  - `cargo check`: ok, and `cargo test`: 10 passed.

## 2026-09-24 — Layered-rules Phase 7: what Language QA does and does not check

**What changed.** `languageQa.status` used to carry `coverage` as a single
sentence. It now carries `language_qa.coverage()`, a structured statement
with four fields:
- `inScope`: one row per engine category (`CATEGORIES`), with an English and
  a Tamil label;
- `outOfScope`: agreement (திணை/பால்/எண்), pronoun and number shifts, meaning
  shifts, omissions and additions, textual basis, and theology. Each row has
  a label and a reason in both languages;
- `handOff`: the path `docs/LANGUAGE_QA_REVIEW_HANDOFF.md`;
- `summary`: the old sentence.

**In the panel.** The open Language QA panel shows "Checks / சரிபார்ப்பவை"
and "Does not check / சரிபார்க்காதவை" directly under the language line. The
block is not behind a disclosure, and it is there whether or not the book
has findings. A clean result can therefore not be read as a review.

**Where the boundary is recorded.** `LANGUAGE_QA_PLAN.md` LQA-3 now records
that these items must never be checked by the offline engine. A request to
add one is an architecture question to raise, not a rule to write.

**Tamil strings: pending native review.** The Tamil labels and reasons were
written by an AI assistant. A native Tamil reviewer must check them before
release. They live in one place, `_IN_SCOPE_LABELS` and `_OUT_OF_SCOPE` in
`language_qa.py`.

**The hand-off doc is a placeholder.** The project's own Round 2 procedure
should be linked there. The doc path is shown as text, because the desktop
app has no in-app doc viewer.

**Verification.**
- `test_status_states_what_language_qa_does_and_does_not_check` checks four
  things:
  - `inScope` equals `CATEGORIES`;
  - the key out-of-scope items are present;
  - every row is bilingual, and no category is both in and out of scope;
  - the hand-off file exists.
- A `LanguageQaPanel.test.ts` test checks that the block is visible with no
  findings, holds both languages and the reason, and is not inside a
  `<details>`.

## 2026-09-28 — Language QA on human labels: benchmark, rule pack, lexicon, house style (#169)

**The data.** Yesu Selva Benz, a Tamil reviewer, labelled a stratified sample of the
engine's own findings on Genesis, Psalms and John:
- 598 items, 597 answered: R0102, PSA 119:54, is unanswered and excluded;
- none "unsure", with a grammatical reason on every row.

The reviewer's workbook was converted by `ta_irv_labels_to_candidate.py`. The output is
committed as data in `benchmark/human/2026-09-28/`, and never regenerated.

Before this, "precision" was agreement with the Round 2 AI review rows. The 15–55% that
put the வல்லினம் rules inline were therefore lower bounds. From now on the human number
is the only one that decides inline.

### Step 1 — the human labels become the authoritative score

- **Scorer.** `language_qa_benchmark.human_score()`:
  - a flagged item is scored when a current finding sits at exactly its book, chapter,
    verse, start and end, with the same NFC text, credited to the finding's own rule;
  - precision = TP ÷ (TP + FP) over labelled findings; house forms are reported apart;
  - lost TPs and removed FPs are reported;
  - the abstained items give recall *proxies* per abstain class.
- **Gate.** `human_gate()` fails in four cases:
  - an inline rule below 0.90 human precision, or with fewer than 20 labelled findings;
  - any rule below `benchmark/human/baseline.json`;
  - a lost human-confirmed finding;
  - a label that no longer anchors.
- **CI.** The gate runs in `ci.yml` after the latency gate, in 1.6 s locally. It needs no
  IRV corpus, because `verses.jsonl` (the 332 labelled verses) is committed beside the
  labels. The engine path filter now includes `benchmark/**`.
- **Scorer check.** On the unchanged pack it reproduces the converter's `summary.json`
  exactly for every rule (TP/FP), and the recall proxies match `pack_changes.md`. This
  is the check that the scorer is the reviewer's scorer.
- **The two IRV copies differ.** The labels anchor 224/224 in `D:\Claude Lab\IRV Tamil`,
  the copy the reviewer used, but only 163/224 in `C:\Users\Benz\Documents\IRV Tamil`,
  which the pack builder and the AI-agreement benchmark read. The two copies differ in
  61 labelled verses (for example GEN 6:7: `பூமியின்மேல்` against `பூமியின் மேல`).
  `verses.jsonl` is built from the reviewer's copy. The builder is left on its
  documented corpus, so that the pack changes only where the brief asks. Which copy is
  canonical is a question for the maintainer.
- **The waiver is removed.** `inlineSignOff` is gone from `gate()`, the loader (a rule
  file carrying it now fails to load as an unknown key) and every rule file. The
  builder's `SIGN_OFF` table becomes `INLINE`, which holds the human number behind each
  inline rule. The AI-agreement `gate()` keeps only its regression check. DECISIONS.md
  2026-09-28 supersedes the 2026-09-24 sign-off.
- **Inline at step 1, on the human numbers.**
  - Only `sandhi.vallinam.dative` qualifies: 93.9%, 46/49.
  - Off inline: accusative (70.0%, 35/50), demonstrative (8 labels), manner-adverb
    (1 label) and wrong-consonant (0 labels).
- **Rebuild.** The pack was rebuilt with the documented command. Per rule, with the
  examples set aside, the only differences from HEAD are `inline` and the removed
  `inlineSignOff`. The examples differ, because the builder picked different real
  verses. It prefers verses the reviews also flagged, and this machine's review folder
  differs from the one the original build saw.
- **Also fixed on the way.**
  - `bba1275`: the engine's version strings were at 0.11.0 inside a 0.12.0 app, which
    stopped `smoke_sidecars.py` at its first check.
  - `e885f55`: the benchmark script now writes UTF-8 to stdout. Its `—` crashed a UTF-8
    reader on a Windows pipe.
- **Tests.**
  - `test_language_qa_benchmark.py`:
    - the human scorer (TP/FP/house/excluded/lost TP/recall proxy);
    - the human gate (sample size, floor, baseline, lost TP);
    - a label that no longer anchors;
    - every committed label anchors in `verses.jsonl`;
    - the CI command itself;
    - the AI gate is regression-only.
  - Tests that used a demonstrative pair as their "inline" example now use a dative pair.
  - Fast engine suite: 2022 passed.

| Rule (human precision, step 0) | Labelled | TP | FP | Precision |
|---|---|---|---|---|
| `sandhi.vallinam.dative` | 49 | 46 | 3 | 93.9% |
| `sandhi.vallinam.accusative` | 50 | 35 | 15 | 70.0% |
| `sandhi.vallinam.demonstrative` | 8 | 7 | 1 | 87.5% |
| `sandhi.vallinam.manner-adverb` | 1 | 1 | 0 | 100% |
| `lexicon.known-misspelling` | 43 | 43 | 0 | 100% |
| `common/spacing.extra` | 15 | 15 | 0 | 100% |
| `typo.divine-name.vowel-drop` | 1 | 1 | 0 | 100% |
| `lexicon.rare-near-common` | 21 | 0 | 21 | 0% |
| `tamil.repeated-word` | 20 | 0 | 20 | 0% |
| `integrity.space-before-note-end` | 15 | 0 | 15 | 0% |

### Step 2 — the reviewer's fixtures

- **Merge.** `benchmark/human/2026-09-28/labelled/*.jsonl` is appended to
  `engine/tests/fixtures/language_qa/labelled/`:
  - sandhi 73 → 240;
  - typo 50 → 115;
  - punctuation 40 → 55;
  - usfm 41 → 56.

  The fixtures are unedited. `--write-labelled` now keeps every human-review line, so a
  future regeneration of the Phase 2.4 sample cannot delete the reviewer's data.
- **Test.** `test_language_qa_labelled.py` gains:
  - a `negative` span must yield no finding of its `ruleId`.
    `integrity.space-before-note-end` is the one reading of "not a text error": it is
    satisfied by a low-severity `usfm` markup finding, which is what step 3 makes it;
  - a human-review `maybe` (a split word: கை கோலில், சு வரை) must not be claimed by any
    sandhi finding;
  - where a finding covers exactly a human-confirmed span, the reviewer's fix must be
    among its suggestions.

  Anchoring collapses whitespace, because the reviewer writes a pair across a poetry
  line with a space. A category the benchmark scores against the bucket is accepted
  (`spacing` in the punctuation bucket).
- **111 failures, each one the pack disagreeing with the reviewer.** They are recorded
  as strict `xfail` in `PENDING_PACK_CHANGES`:
  - 75 confirmed false alarms: `tamil.repeated-word` 20, `lexicon.rare-near-common` 21,
    `integrity.space-before-note-end` 15, accusative 15, dative 3, demonstrative 1;
  - 36 missed positives: dative 23 (the `-ற்கு` datives, the `-க்கு` exception words
    and a poetry boundary), demonstrative 9 (the house-form prefixes) and accusative 4.

  Strict means each entry must start passing when its fix lands, and is then deleted.
  Result: 1914 passed, 111 xfailed.

### Step 3 — the rule pack, `ta-irv@1.1.0`

Built from `D:\Claude Lab\IRV Tamil` (the reviewer's copy, the maintainer's choice) and the
labels, with the documented `--reviews`.

**Mechanism.**
- Loader:
  - condition keys `notPrefix`, `notSuffix` and `notSuffixLexical`;
  - category `usfm` (layer integrity);
  - a pair candidate carries its first word's end and a first-word fix.
- `scan_text`: a pair across a `\q` line or lifted markup is flagged on its first word
  (`\wj அந்த\wj* காகம்` → `அந்த`, fix `அந்தக்`). It is dropped only when the first word
  itself is split.
- Built-in rules: `RuleMeta.enabled`; `tamil.repeated-word` is disabled.
- `INLINE_RULES` gains `lexicon.known-misspelling`.
- `SCAN_CACHE_VERSION` 3.
- Frontend: `usfm` joins the category union and the mark table (drawn like spacing).

**Pack, as the brief and `pack_changes.md` ask, with one deliberate deviation.**
- **Every வல்லினம் rule.**
  - It abstains before தேவ- (`notPrefix` தேவை) and before the clitics and quotatives.
  - The proper-noun abstain is gone. The `listRef` still works for a project override.
- **Lists.** The builder's bare-majority criterion (`pair_stats`, `bare_majority`,
  `stem_abstains`, `MIN_CONTEXTS`) is gone. `notLexical` is now built from three sources:
  1. the corpus root nouns (attested inflection, now also -ற்கு);
  2. the reviewer's root nouns and house forms (accusative 202, dative 17), plus
     `KEEP_EXCLUDED` கை and வரை (split words, not sandhi);
  3. minus the reviewer's case forms (accusative 15, dative 7: யெகோவாவை, ஜீவனை, சபைக்கு, யோபுக்கு …).

  The four directions stay excluded from the dative rule.
- **Accusative.** `notSuffixLexical` holds முறை, வினை, வகை, தொண்டை only.
  - **Deviation:** `notSuffix` மை and the தரை element are not applied. On the corpus,
    -மை also ends உம்மை 9/166, நம்மை 6/58, தம்மை 4/29 and every -ம் name accusative
    (எருசலேமை 1/23 …), and -தரை ends கர்த்தரை 5/13 and மனிதரை.
  - The maintainer chose exact words (DECISIONS.md). The labelled result is the same
    as the brief projected.
- **Dative.** Suffix `(?:க்கு|ற்கு)$`.
- **Demonstrative and manner-adverb.** No per-trigger stems.
- **New rule.** `sandhi.compound.direction` v1: panel-only, medium confidence. Its 15
  incorrect examples include PSA 48:7, 78:26 and GEN 29:1.
- **`sandhi.clitic.fused` v2.**
  - It matches தான் only. ஆவது fuses by vowel sandhi, which has no linking-consonant fix,
    so it is not matched; கூட is house-style apart.
  - Its correct examples come from accusative pronouns, manner adverbs and datives, no
    longer from verbs (வைத்தான்).
- **`integrity.space-before-note-end` v2.** Category `usfm`, severity low, reworded.
- **Versions.** Every touched rule is at v2. The pack is 1.1.0, so ignores recorded under
  1.0.0 come back once for re-check, as designed.

**The review as pack examples.** Every reviewer-confirmed sandhi finding becomes an
incorrect example of the rule that now finds it, and every false alarm a correct example.
Each is a two-word window, and each pack load re-checks the review:
- demonstrative 16 + 1;
- accusative 40 + 13;
- dative 66 + 3;
- direction 3;
- manner-adverb 1.

A confirmed finding no rule finds fails the build. The two false alarms the accusative
still raises are printed as residuals: GEN 21:9 செய்கிறதை சாராள் and GEN 35:4 அவைகளை
சீகேம், both before a name. Names need doubling in 17 of 25 cases, so there is no rule to
write.

**Human numbers, step 0 → step 3.**

| Rule | Step 0 | Step 3 | Inline |
|---|---|---|---|
| dative | 93.9% (46/49) | 100% (46/46) | yes |
| accusative | 70.0% (35/50) | 94.6% (35/37) | **yes (new)** |
| demonstrative | 87.5% (7/8) | 100% (7/7) | no, 7 labels |
| manner-adverb | 100% (1/1) | 100% (1/1) | no |
| `lexicon.known-misspelling` | 100% (43/43) | 100% (43/43) | **yes (new)** |
| `tamil.repeated-word` | 0% (0/20) | disabled, 20 false alarms removed | — |

Recall proxies, reviewer "missed" now found:

| Class | Found |
|---|---|
| -ற்கு datives | 17/17 |
| -க்கு exceptions | 5/5 |
| poetry-line pairs | 2/2 |
| demonstrative house forms | 9/9 |
| bare-majority -ஐ | 4/5 (the fifth is the split word சு வரை, correctly left) |

No confirmed finding was lost. `benchmark/human/baseline.json` is rewritten with the
step-3 numbers.

**Tests.**
- The labelled fixtures:
  - 84 of the 111 pending cases pass and are deleted;
  - the 2 name residuals are a separate strict `RESIDUAL` set;
  - the 21 left are `lexicon.rare-near-common`, for step 4;
  - a "missed" item may be found by any sandhi rule, because the converter guessed the
    rule and the reviewer judged only "doubling needed". கிழக்கு காற்று is the direction
    rule's.
- Tests that encoded the old pack are updated to the reviewed behaviour:
  - repeated-word is off;
  - a name does not block doubling;
  - இந்த தேசத்தில் is flagged;
  - a pair across markup is flagged on its first word;
  - the pack is 1.1.0;
  - the inline set is dative and accusative.
- New tests: the reviewed misses and false alarms, and தேவை is still checked.

**Performance.** The pack load (examples included) goes from 71 ms to 132 ms, median of 5.
It is paid once per process, on the worker's first Tamil scan. A status poll never loads
it (`loaded_pack`). Full verses made it 179 ms before the review examples were windowed.

### Step 4 — the lexicon, `ta-irv-lexicon@2`

- **Rebuild.** Rebuilt from `D:\Claude Lab\IRV Tamil` with
  `--curated "D:\Claude Lab\Revant work\Claude outputs" benchmark/human/2026-09-28/lexicon_curated.csv`.
  The current `@1` was built from the review folder alone: rebuilding it that way
  reproduces it, except for 2 pairs from rows added to the folder since. So the
  reviewer's CSV is added to the AI-review pairs rather than replacing them. Dropping
  the ~107 pairs the review never sampled would lose coverage.
- **Result.** 156 pairs (151 + 5). All 43 human-confirmed pairs are in, listed as
  `humanConfirmed`. They skip the "wrong form is rare in the corpus" filter: a
  person confirmed them, and IRV repeats some misspellings (கர்ச்சிக்கிற). 21
  `protected` words. The file is 3.09 MB.
- **`lexicon.rare-near-common`** is disabled (`RuleMeta.enabled=False`, passed to
  `lexicon_findings(rare_near_common=)`): 0/21. `protected` words are never flagged by
  either lexicon rule. It can be re-enabled only with a morphology filter and a new
  human sample ≥ 0.90 (DECISIONS.md).
- **`lexicon.known-misspelling`** is inline (step 3's `INLINE_RULES`): 43/43. It is
  still reported once per book at the first occurrence, so the inline mark is on that
  occurrence only.
- **Human gate.** It passes. rare-near-common now shows 21 false alarms no longer
  produced. The labelled fixtures' `PENDING_PACK_CHANGES` is empty; only the 2 name
  residuals remain as strict xfail.
- **Tests.** `test_lexicon.py`:
  - the curated map holds the 43 confirmed pairs and 21 protected words;
  - the frequency guard exempts only confirmed pairs;
  - the manager leaves rare-near-common off by default and still uses the corpus
    lexicon when it is enabled.

  Service tests: 2619 passed, 2 xfailed.

### Step 5 — house style

- **Curated house style in the pack.** The தேவ- rule and the clitic policy are curated
  project house style, and they live in the pack as abstains on every வல்லினம் rule
  (step 3). `LANGUAGE_QA_HOUSESTYLE.md` records them as such. No per-word entries.
- **The bundled seed.** `language_packs/ta-irv/housestyle-seed.json` is the review's
  `housestyle_import.json`: one `properNouns` entry, சேத்து.
  - `housestyle.bundled_seed()` reads it, validated and marked `seed`.
  - `with_seed()` merges it under a project's entries: an own entry with the same key
    replaces it, whatever its state.
  - The scan applies the merge for a Tamil project. `housestyle.list` returns the seed
    apart (`seed`), without the keys a project has overridden.
  - Settings shows seed rows as **bundled**, with a Remove that records the project's
    own removed entry.
  - Nothing is written into a workbench on open. The seed ships in the frozen sidecar,
    because `bridge-engine.spec` bundles the whole `ta-irv` folder.
- **No name abstains from the sandhi rows.** The seed carries only the lexicon's
  `FP_NAME`, as the brief says.
- **Tests.**
  - `test_housestyle.py`: the seed is read-only and merged; the list returns it; a
    removal replaces it.
  - `SettingsModal.test.ts`: the bundled row, and its Remove.

### Step 6 — the split-word check is scoped, not built

Two reviewer corrections were joins, not sandhi: `கை கோலில்` → கைக்கோலில் and
`சு வரை` → சுவரை. `LANGUAGE_QA_PLAN.md` now scopes `word-joining.orphan-syllable`:
- the shape: a 1–2 cluster token that is not a known word, and that joins with its
  neighbour into a known word;
- the guardrails;
- the requirement for its own labelled sample (≥ 20) before any precision claim.

Nothing is implemented. The plan also lists the rules flagged for the next review round:
demonstrative 7, manner-adverb 1 and spacing.extra 15 labels.

### Found while re-measuring: the seed name made `name.minority-spelling` noisy

The same-text Bible scan showed `project/name.minority-spelling` going from 0 to 86. The
new trigger is the bundled seed name சேத்து (step 5). A three-cluster name is one cluster
(distance 1.0) away from ordinary words. It flagged காத்து, செத்து, சேர்த்து, பத்து,
சொத்து, பூத்து, தைத்து, சேராது and சேமித்து as misspellings of it.

Two guards fix it:
1. A word the corpus uses commonly (lexicon count ≥ `COMMON_MIN`) is never a misspelt
   name. This is the lexicon's own rule, and the scan passes `corpus_count=lexicon.count`.
   That alone left 6.
2. A name of at most `NAME_SHORT_CLUSTERS` = 3 clusters admits only a single typist
   confusion (distance ≤ 0.5). That leaves none.

Cost: IRV's own two-cluster spelling சேத் is no longer offered as a variant of சேத்து.
A longer name keeps its one-cluster variants: பார்வொன் → பார்வோன் is still found.

After the fix the rule raises 0 Bible-wide. Test:
`test_a_short_approved_name_does_not_claim_ordinary_words`.

### Step 7 — re-measured

**Human benchmark, final (`ta-irv@1.1.0`, `ta-irv-lexicon@2`).**

| Rule | Step 0 | Final | Inline |
|---|---|---|---|
| `sandhi.vallinam.dative` | 93.9% (46/49) | 100% (46/46) | yes |
| `sandhi.vallinam.accusative` | 70.0% (35/50) | 94.6% (35/37) | yes |
| `sandhi.vallinam.demonstrative` | 87.5% (7/8) | 100% (7/7) | no, 7 labels |
| `sandhi.vallinam.manner-adverb` | 100% (1/1) | 100% (1/1) | no |
| `lexicon.known-misspelling` | 100% (43/43) | 100% (43/43) | yes |
| `common/spacing.extra` | 100% (15/15) | 100% (15/15) | no, 15 labels |
| `typo.divine-name.vowel-drop` | 100% (1/1) | 100% (1/1) | no |
| `lexicon.rare-near-common` | 0% (0/21) | disabled; 21 false alarms gone | — |
| `tamil.repeated-word` | 0% (0/20) | disabled; 20 false alarms gone | — |
| `integrity.space-before-note-end` | 0/15 as a text error | markup item (`usfm`, low) | — |

- **Precision.** Every rule's human precision is at or above its step-0 value.
- **Lost findings.** No human-confirmed finding was lost.
- **Missed contexts.** Every reviewer-labelled "missed" context is now found, except
  the split word சு வரை, which the pack correctly leaves to a split-word check (step 6):
  - -ற்கு 17/17;
  - -க்கு exceptions 5/5;
  - poetry lines 2/2;
  - demonstrative house forms 9/9;
  - bare-majority 4/5.
- **Residuals.** The two accusative false alarms before a name are the only strict
  residuals.

**The same text, before and after.** Language QA alone over the 66 books of
`D:\Claude Lab\IRV Tamil`. Before is b5688ef, the pre-task engine; after is the final
code.

| Rule | Before | After | Change | Drawn inline after |
|---|---|---|---|---|
| `ta-irv/sandhi.vallinam.accusative` | 982 | 1000 | +18 | 1000 |
| `ta-irv/sandhi.vallinam.dative` | 643 | 789 | +146 | 789 |
| `ta-irv/integrity.space-before-note-end` | 224 | 224 | +0 | 0 |
| `ta-irv/lexicon.known-misspelling` | 154 | 160 | +6 | 160 |
| `ta-irv/sandhi.vallinam.demonstrative` | 101 | 210 | +109 | 0 |
| `common/spacing.extra` | 91 | 91 | +0 | 0 |
| `ta-irv/lexicon.rare-near-common` | 179 | 0 | -179 | 0 |
| `ta-irv/tamil.repeated-word` | 146 | 0 | -146 | 0 |
| `ta-irv/sandhi.compound.direction` | 0 | 92 | +92 | 0 |
| `ta-irv/sandhi.vallinam.manner-adverb` | 22 | 22 | +0 | 0 |
| `ta-irv/typo.divine-name.dative-stem` | 13 | 13 | +0 | 0 |
| `ta-irv/typo.divine-name.vowel-drop` | 11 | 11 | +0 | 0 |
| `ta-irv/sandhi.clitic.fused` | 9 | 6 | -3 | 0 |
| `ta-irv/typo.suffix.dropped-tha` | 2 | 2 | +0 | 0 |
| `common/unicode.invisible` | 1 | 1 | +0 | 0 |
| `ta-irv/sandhi.vallinam.wrong-consonant` | 1 | 1 | +0 | 0 |
| `common/punctuation.repeated` | 1 | 1 | +0 | 0 |
| **Total** | **2580** | **2623** | **+43** | **1949** (before 1749) |

- **False alarms fall by 328:** rare-near-common −179, repeated-word −146, clitic −3.
- **New true checks add 371:** dative +146 (-ற்கு), demonstrative +109 (no house-form
  stems), direction +92, accusative +18, known-misspelling +6.
- **Net.** The total rises by 43, and inline marks rise from 1,749 to 1,949. They now
  come only from rules at 94.6–100% human precision: before, they came from five
  வல்லினம் rules on a sign-off.

**The whole-Bible collection run.** `collection.runChecks`, with the checks local,
greekroom and languageQa, on the 66 books of `D:\Claude Lab\IRV Tamil`, on an idle
machine:
- **Result:** `succeeded`; 66/66 books; no book error; import 10.9 s.
- **Wall time:** 3,745.9 s (62 min).
- **Language QA findings:** 2,623, exactly the same-text scan's total.
- **Final stage:** house-style propagation available, 0 proposals.

The brief asked for a comparison with the earlier **7,401**. That figure is not
comparable, and not only because the text changed:
- It came from a process started on 2026-09-24 on mid-Phase-6 code.
- Genesis on the same older text gives 110 Language QA findings with the pre-task engine
  and 124 with the final one. Both were measured through the check job, with the
  collection's own check set, counting each finding exactly once. The earlier run
  reported 295.
- So the per-verse sum that run reported is not reproducible with either committed
  engine.
- The like-for-like numbers are the same-text scan above: 2,580 → 2,623.

The earlier 6,031.8 s wall time also ran beside test suites; 3,745.9 s is the idle
figure.

**Performance contract.**
- **Psalms** (`D:` copy, three cold passes each):

  | | Before | After |
  |---|---|---|
  | Cold wall | 2.52–2.59 s | 2.50–2.55 s |
  | Peak RSS | 60.6 MB | 71.3 MB (+11 MB, limit +50) |
  | Warm reopen | 0.43–0.46 s | 0.39 s |

- **Latency gate** (`benchmark_language_qa.py --gate --cores 2`): pass. p95
  `verse.decide` (Language QA) 23.9 ms, `verse.get` 3.6 ms, `languageQa.status` 1.1 ms,
  `languageQa.inline` 0.4 ms. The shared write paths are over, as before and outside
  Language QA: `verse.edit` 244 ms, `project.open` 166 ms.
- **Pack load:** 71 → 132 ms, once per process.

**Review date and reviewer:** 2026-09-28, Yesu Selva Benz. 598 items on GEN/PSA/JHN,
597 answered; R0102 (PSA 119:54) excluded, and not re-labelled.

**Final gates (2026-09-28).**
- Full engine suite (`pytest tests/ greek_room_engine/tests/ -n auto`, slow tests
  included): 3779 passed, 2 xfailed (the name residuals).
- svelte-check 0/0; Vitest 556 passed; `npm run build` ok.
- Human gate pass. Latency gate pass.
- Rust untouched, so cargo was not re-run in this task.

## 2026-09-29 — Language QA, human-review round 2: promote, split the misspelling map, resolve தான், known splits (#169)

**The data.** The same reviewer, Yesu Selva Benz, labelled 220 items on 2026-09-29.
- **Scope.** They come from all 66 books of `D:\Claude Lab\IRV Tamil`, and none was seen
  in round 1.
- **Answers.** All 220 were answered, with a reason on every row.
- **Where.** Committed as data in `benchmark/human/2026-09-29/`, with `verses.jsonl`
  (211 verses from the D: copy).

### Step 1 — merge round 2 into the benchmark

- **Reading the rounds together.** `language_qa_benchmark.load_human_rounds()` reads every
  `benchmark/human/*/human_labels.jsonl` and tags each row with its round. It refuses a
  verse whose text differs between rounds.
- **Scoring.** `human_score()` scores the new `split` rows (SPLIT = tp, WORD or
  PARTICLE = fp, when a finding covers the span; otherwise a recall proxy) and reports
  every rule per round and combined.
- **The gate: inline and regression read different numbers.**
  - Inline reads the combined figure.
  - Regression is judged per round: a round may not fall below its own baseline.
  - With round 2 added, the old single-round baseline would have "failed" dative
    (100% → 98.8%) and known-misspelling (100% → 92.2%). Those are new data, not a
    regression; round 1 is unchanged.
  - `benchmark/human/baseline.json` now records rounds.
  - CI passes `--human-labels ../benchmark/human`.
- **Combined numbers.** They match the brief exactly:
  - dative 85/86;
  - accusative 74/77;
  - demonstrative 24/24;
  - manner-adverb 21/21;
  - direction 25/25;
  - spacing.extra 23/23;
  - known-misspelling 47/51 (round 2: 4/8).

  All 818 rows anchor.
- **Fixtures.** Round-2 fixtures are appended (sandhi +165, typo +18, punctuation +8), and
  `word-joining.jsonl` is a new bucket (23 rows). The labelled test reads it:
  - the round-2 origin tag "(human review round 2)" counts as human;
  - the converter's candidate rule name `word-joining.orphan-syllable` is an alias of
    `lexicon.known-split`.
- **18 strict pending cases**, each the pack disagreeing with the reviewer:
  - 2 root nouns (step 2);
  - 4 AI misspelling pairs (step 3);
  - 2 தான் cases (step 4);
  - 10 splits (step 5).

### Step 2 — promote on evidence; settle the small-population rules

- **(a) Promoted: at least 20 labelled at ≥ 0.90, over both rounds.**
  - `sandhi.vallinam.demonstrative` 24/24;
  - `sandhi.vallinam.manner-adverb` 21/21;
  - `sandhi.compound.direction` 25/25;
  - `common/spacing.extra` 23/23, in `INLINE_RULES`.

  The pack rules are set in the builder's `INLINE` table.
- **(b) The whole population is labelled** (DECISIONS.md 2026-09-29).
  - `benchmark/human/population.json` is written from the 66 books of the D: copy with
    `--write-population`. It records 10 small rules and a definition hash for each.
  - The gate checks coverage and staleness.
  - Inline under it: `typo.suffix.dropped-tha` 2/2 and `sandhi.vallinam.wrong-consonant`
    1/1.
  - Panel-only, in the next sample: `typo.divine-name.vowel-drop` 5/11 and `dative-stem`
    4/13.
- **Reviewer fixes.** The builder now reads every round, for word verdicts and for
  examples. So சேட்டை (accusative, `FP_ROOT`) and தோவேக்கு (the name Doeg,
  dative, `FP_ROOT`) go into `notLexical`: accusative roots 203, dative 18.
  - Every round-2 sandhi verdict is now a pack example as well: demonstrative +17, manner
    +20, accusative +40, dative +40, direction +25, wrong-consonant +1. Each carries its
    round's date in `origin`.
  - **Not a change: the brief's "dropped-tha missed செய்வற்கு".** It does not hold. The
    rule already flags செய்வற்கு at 1CH 23:5 with fix செய்வதற்கு, and that verse was
    already one of its incorrect examples. Only the clitic rule also fired on the pair
    (step 4).
- **Human gate.** It passes.
  - accusative 74/76 (97.4%; round 2 39/39 now that சேட்டை is excluded);
  - dative 85/85.
- **Tests.**
  - The inline tests' panel-only finding is now `,,`, because `spacing.extra` is inline.
  - The inline-set assertions are updated.
  - New: `test_a_rule_whose_whole_population_is_labelled_may_be_inline` (confirmed,
    incomplete, stale).
  - Service and jobs: 3493 passed.

### Step 3 — the misspelling map split by provenance (`ta-irv-lexicon@3`)

- **Rebuild.** Rebuilt on the D: copy with `--curated` covering the review folder and
  both rounds' `lexicon_curated.csv`. `--human-labels` now reads every round.
- **The map.** 182 pairs:
  - **47 `human`:** round 1's 43, plus அணிந்துக், நிற்க்கும்போது, போர்வையைக்,
    வார்தையின்படியே;
  - **135 `ai-review`**, including ஐபிரத் and மகிழுகிறதுபோல, which the reviewer
    marked unsure;
  - **removed:** the four pairs the reviewer rejected — திடமனதாயிரு (meaning),
    கவனிக்காதே (imperative → participle), பூட்டுக்களையும் (valid plural),
    பெருந்தொனியாய் (style). They are now `protected` (25 in all).
- **Why the map grew.** The review folder gained five AI Round 2 reports on 2026-09-29
  (EST, ECC, SNG, PRO, ISA), adding 30 ai-review pairs. As ai-review pairs they are
  panel-only.
- **`lexicon.known-misspelling`.** Human pairs are high and inline. Ai-review pairs are
  medium, not inline, and say "awaits human confirmation". Each finding carries
  `provenance`.
- **Fragments.** Single-grapheme tokens outside `MONOSYLLABLES` are not counted as words
  (சு, நே gone; கை kept).
- **Human gate.** It passes. Known-misspelling is now 47/47, and the 4 rejected pairs
  are no longer produced.
- **Tests.** The four round-2 misspelling negatives pass. Service tests: 3468 passed.

### Step 4 — தான்: the clitic rule measured the wrong thing

- **Disabled.** `sandhi.clitic.fused` is `enabled: false` (v3). All six of its round-2
  findings were the reflexive pronoun, which the reviewer writes separately with
  doubling.
- **No longer abstained.** The வல்லினம் rules drop தான் and ஆவது from the clitic
  abstain. ஆவது is vowel-initial, so no rule fires on it anyway.
  - The rules get a new pack field, `contexts` (loader: `Context`,
    `Candidate.confidence`/`alternatives`; `scan_text` adds the alternatives and the
    context's confidence).
  - After a case form, `X தான்` is flagged with two ranked suggestions,
    `Xத் தான்` ("pronoun: separate, doubled") and then `Xத்தான்` ("clitic: fused"),
    at medium confidence, with the reviewer's Tamil message.
  - Both correct forms are left alone.
  - Rule versions: the வல்லினம் rules v3, direction v2, wrong-consonant v3.
- **Human gate.** It passes.
  - Dative is now 87/87: the two round-2 தான் labels, ACT 7:46 and 1CH 23:5, are
    credited to it, and its rank-1 fix is the reviewer's form.
  - The fixture for 1CH 23:5 stays a strict xfail of its own (`COMBINED_FIX`). The
    reviewer's `செய்வதற்குத் தான்` combines two findings: the typo and the link.
- **Tests.**
  - The clitic tests are replaced by
    `test_bare_tan_offers_the_pronoun_first_and_the_clitic_second`.
  - A reviewed finding whose rule is now disabled may be found by any sandhi rule.
  - Service and jobs: 3499 passed.

### Step 5 — known splits as data (`ta-irv-lexicon@4`)

- **`lexicon.known-split`** (in-code, category word-joining, high).
  - It flags an adjacent pair that the lexicon's `splits` map names, and suggests the
    joined form.
  - The builder takes the map only from the reviewer's `SPLIT` rows: சு வரை → சுவரை,
    நே போ → நேபோ.
  - The one-grapheme tokens judged a word, name or interjection (சீ, சோ, நோ, பை) are
    added to `protected` (29 in all).
- **Inline under rule (b).** The population file shows 11 of 11 findings confirmed: the
  10 round-2 `SPLIT` rows, plus round 1's PSA 48:13 row, which overlaps the fourth
  சு வரை. Its definition hash includes the split map.
- **Cache.** The chapter cache key now includes `lexicon_fingerprint()`, which now
  hashes the split map, so a rebuilt lexicon rescans the verse-level splits.
- **The plan.** The `word-joining.orphan-syllable` entry is replaced by "split words:
  curated pairs fed by review rounds".
- **Tests.** `test_known_splits_are_curated_pairs_found_in_the_verse`. The 10 split
  fixtures pass, and `PENDING_PACK_CHANGES` is empty. Only the 2 name residuals and
  the 1 combined fix remain as strict xfail. Service and jobs: 3510 passed.

### Step 6 — re-measured after round 2

**Human gate (combined, both rounds).** It passes.

| Rule | Labelled | Human precision | Inline | On |
|---|---|---|---|---|
| `sandhi.vallinam.dative` | 87 | 100% (87/87) | yes | (a) |
| `sandhi.vallinam.accusative` | 76 | 97.4% (74/76) | yes | (a) |
| `sandhi.vallinam.demonstrative` | 24 | 100% | yes (new) | (a) |
| `sandhi.vallinam.manner-adverb` | 21 | 100% | yes (new) | (a) |
| `sandhi.compound.direction` | 25 | 100% | yes (new) | (a) |
| `common/spacing.extra` | 23 | 100% | yes (new) | (a) |
| `lexicon.known-misspelling` | 47 | 100% (human pairs only) | yes for human pairs | (a) |
| `lexicon.known-split` | 10 | 100%; population 11/11 | yes (new) | (b) |
| `typo.suffix.dropped-tha` | 2 | 100%; population 2/2 | yes (new) | (b) |
| `sandhi.vallinam.wrong-consonant` | 1 | 100%; population 1/1 | yes (new) | (b) |
| `typo.divine-name.vowel-drop` | 5 | 100%; population 5/11 | no, until labelled | — |
| `typo.divine-name.dative-stem` | 4 | 100%; population 4/13 | no, until labelled | — |
| `sandhi.clitic.fused` | — | disabled (தான் resolved in the வல்லினம் rules) | — | — |

Every fixture passes, except three strict, documented xfails: the two accusatives before
a name, and the 1CH 23:5 fix that combines two findings.

**The same text, round 1 final (de6feef) against round 2.** Language QA alone, 66 books,
D: copy.

| Rule | Round 1 final | Round 2 | Change | Inline before → after |
|---|---|---|---|---|
| `ta-irv/sandhi.vallinam.accusative` | 1000 | 999 | -1 | 1000 → 999 |
| `ta-irv/sandhi.vallinam.dative` | 789 | 790 | +1 | 789 → 790 |
| `ta-irv/integrity.space-before-note-end` | 224 | 224 | +0 | 0 → 0 |
| `ta-irv/sandhi.vallinam.demonstrative` | 210 | 210 | +0 | 0 → 210 |
| `ta-irv/lexicon.known-misspelling` | 160 | 185 | +25 | 160 → 58 |
| `ta-irv/sandhi.compound.direction` | 92 | 92 | +0 | 0 → 92 |
| `common/spacing.extra` | 91 | 91 | +0 | 0 → 91 |
| `ta-irv/sandhi.vallinam.manner-adverb` | 22 | 22 | +0 | 0 → 22 |
| `ta-irv/typo.divine-name.dative-stem` | 13 | 13 | +0 | 0 → 0 |
| `ta-irv/typo.divine-name.vowel-drop` | 11 | 11 | +0 | 0 → 0 |
| `ta-irv/lexicon.known-split` | 0 | 11 | +11 | 0 → 11 |
| `ta-irv/sandhi.clitic.fused` | 6 | 0 | -6 | 0 → 0 |
| `ta-irv/typo.suffix.dropped-tha` | 2 | 2 | +0 | 0 → 2 |
| `ta-irv/sandhi.vallinam.wrong-consonant` | 1 | 1 | +0 | 0 → 1 |
| `common/unicode.invisible` | 1 | 1 | +0 | 0 → 0 |
| `common/punctuation.repeated` | 1 | 1 | +0 | 0 → 0 |
| **Total** | **2623** | **2653** | **+30** | **1949 → 2276** |

- **Inline marks rise by 327**, from 1,949 to 2,276:
  - demonstrative +210, direction +92, spacing +91, manner-adverb +22, known-split +11;
  - known-misspelling falls from 160 to 58, because only human-confirmed pairs are
    drawn now.
- **The total rises by 30** where the brief expected a small fall. That is +25
  known-misspelling findings from the 30 ai-review pairs in the five AI reports added to
  the review folder on 2026-09-29. They are panel-only, "awaits confirmation".
  Without those 25 the total would be 2,628, or +5 on 2,623: the 11 new known splits outweigh the 6 clitic findings removed.
- **Removed:** clitic.fused's 6 findings.
- **தான்:** the four clitic findings already written `Xத் தான்` are now correctly
  silent. Only two bare `X தான்` exist in the Bible, ACT 7:46 and 1CH 23:5, both
  already labelled.

**The whole-Bible collection run.** Local + greekroom + languageQa, on the D: copy:
- **Result:** 66/66 succeeded; import 7.4 s.
- **Wall time:** 4,447.9 s.
- **Language QA findings:** 2,653, exactly the same-text total.
- **Final stage:** house-style propagation available, 0 proposals.

The wall time is 12 min above round 1's 3,745.9 s, but the machine is slower today. The
unchanged pre-task engine's Psalms pass rose from 2.52 s to 2.94–3.26 s. The clean
comparison is the Psalms pass run alternately in one session:

| | Round 1 final | Round 2 |
|---|---|---|
| Cold wall | 2.91–3.04 s | 3.03–3.14 s (≈ +2%, within the spread) |
| Warm reopen | 0.45–0.55 s | 0.43–0.46 s |
| Peak RSS | 61.4 MB | 68.7 MB (+7 MB) |

The pack load, which now holds 523 examples, is 256 ms, once per process.

**Housekeeping.** The measurement run registered one throwaway project (`coll3`) in the
workspace registry. It was removed, and the registry was backed up first.

**Next sample, for the reviewer.**
- The 6 + 9 unlabelled divine-name findings: vowel-drop 6 of 11 and dative-stem 9 of 13.
  Labelling them lets rule (b) put both inline.
- The two unsure ai-review pairs: ஐபிரத் → ஐபிராத் (name spelling) and மகிழுகிறதுபோல
  → மகிழ்கிறதுபோல (verb stem).
- 20 or more of the 135 ai-review misspelling pairs. That is what stands between them
  and `human` provenance, including the 30 from the five new reports.
- தான்: no new bare `X தான்` findings exist beyond the two already labelled. The batch
  can only come from future text.

**Final gates (2026-09-29).**
- **Full engine suite** (`-n auto`, slow tests included): 4637 passed, 3 xfailed, 1
  failed. The failure was `test_unreadable_chapter_is_incomplete_not_clean`, a single
  parameter, which waits on a background scan with a timeout.
  - It then passed in isolation, and 15 of 15 times under `-n auto`.
  - Round 2 does not touch its path.
  - Recorded as a timing flake under a 12-minute parallel load, not fixed here.
- **Frontend:** svelte-check 0/0; Vitest 556 passed; build ok.
- **Human gate:** pass.
## 2026-09-28 — The release version is declared in five places (#170)

Release 0.12.0 bumped `package.json`, `src-tauri/tauri.conf.json` and
`src-tauri/Cargo.toml` but left `BRIDGE_VERSION` (`engine/bridge_service.py`)
and `ENGINE_VERSION` (`engine/greek_room_engine/engine.py`) on `0.11.0`. Both
are now `0.12.0`.

**Why nothing caught it.** The only existing check is the frozen-pair smoke's
version assertion, and `scripts/smoke_sidecars.py` is `continue-on-error` in
`release.yml` because of the separate #130 `inspectImport` mismatch. So a release
could go green carrying a sidecar that reported the previous version, and the
first symptom was a smoke run that never reached its later steps.

**The gate.** `engine/tests/service/test_version_consistency.py` treats
`package.json` as the single source of truth — the same source the smoke script
already compares the frozen engine against — and asserts the two Python constants
and both Rust-side manifests agree with it, plus that each is a bare
`MAJOR.MINOR.PATCH` string. It runs in the ordinary engine suite, so CI catches a
half-applied bump on the pull request rather than at release time.

**Blast radius, checked rather than assumed.** `BRIDGE_VERSION` is reported by
`engine.info` and stamped as `engine_version` provenance on each `QaFinding`.
It is *not* part of `_stable_finding_id()` (sha1 of
`chapter:verse:engine:check_type:disambiguator`, where `engine` is the adapter
name), so no saved decision is detached by the bump. The other `*_ENGINE_VERSION`
constants in `tc_ai_bridge/` are pipeline-stage identifiers such as
`bridge-meaning-analysis-v2`, not release numbers, and were deliberately left
alone. `semantic_mapping.py`'s `3.0.1-stage3` is Stage 3's own and is untouched.

**Found and not fixed here.** `project_import.py:505` writes
`"generator": {"name": "Bridge", "build": "0.11.0"}` into every imported
project's `manifest.json` — the same stale-literal bug, filed as #176 rather
than folded in, because it changes what Bridge writes into a
translationCore-compatible manifest rather than into a diagnostic response.
Nothing reads `generator.build` today.

**Verification.** The new file: 4 passed, and the gate was confirmed to actually
fail by reverting `BRIDGE_VERSION` to `0.11.0` and watching it report the
mismatch. `tests/service`: 145 passed. Frozen-pair smoke NOT rerun — that needs a
PyInstaller rebuild, and #130 still fails it at a later step regardless.

## 2026-09-28 — One Bridge version constant, not three (#176)

#170 aligned the two sidecar constants. Grepping for the old number while doing
that turned up a third: `project_import.py` wrote
`"generator": {"name": "Bridge", "build": "0.11.0"}` into the `manifest.json` of
every imported project, so a project imported by a 0.12.0 build claimed on disk
to have been made by 0.11.0. Nothing read `generator.build`, and no test or
fixture referenced it, which is why it survived three releases.

**Where the number lives now.** `engine/tc_ai_bridge/version.py` holds
`BRIDGE_VERSION` once. `bridge_service.py` re-exports it (as a module-level name,
so the existing `monkeypatch.setattr(bridge_service, "BRIDGE_VERSION", ...)` in
`test_bridge_service.py` still works) and `project_import.py` imports it directly.
It sits in `tc_ai_bridge` rather than in `bridge_service` because `bridge_service`
imports `tc_ai_bridge`, so the other direction is a cycle.

`package.json` is still the release source of truth, but the engine cannot read it
at runtime — a frozen PyInstaller sidecar does not ship it — so the constant has
to exist in Python. The rule enforced by the gate is that it exists *exactly
once*.

**Gate extended.** `test_version_consistency.py` grew two cases: that
`bridge_service.BRIDGE_VERSION` is the same object as the canonical constant
rather than a second literal, and that `project_import.py` stamps
`BRIDGE_VERSION` with no hardcoded `"build": "..."` string left in the file.

**Verified by writing a real manifest, not only by reading the source.** A
throwaway import of a small Titus USFM produced
`en_ulb_tit/manifest.json  generator={'name': 'Bridge', 'build': '0.12.0'}`.

**Existing projects are left alone.** A project on disk records the build that
actually imported it; rewriting that would falsify provenance. Nothing migrates.

## 2026-09-29 — Alignment Review is the QA surface alone (#129)

Audit B7 held `SemanticAlignmentMode.svelte`, `PassageAlignmentMode.svelte` and
`VirtualPassageStream.svelte` "until the cross-verse alignment page is built and
accepted; that page supersedes these views, so decide then". #116–#119 shipped in
0.11.0, so the hold expired. The maintainer decided on 2026-09-29: remove them,
and remove the Word tab with them.

**What went.** The three components, their two test files, the `WordModeStub`
the shell test used, and the whole tablist — one remaining mode needs no tabs, so
`AlignmentReview.svelte` now mounts `AlignmentQaMode` directly and keeps only its
title and Close button.

**What deliberately stayed.** `AlignmentModal` — the translationCore-compatible
editor the Word tab hosted — is untouched. The tab was a *second* entry point to
it; it still opens as the Align Words popup from `ReviewPanel.svelte:744`. That
matters beyond tidiness: the editor is the only thing that produces completed
alignments, and completed alignments are the offline half of #146's cross-verse
agreement gate. Removing it would have made the AI auto-link permanently
impossible.

`semanticLocationGetRange` and `targetSemanticGetRange`, the two client methods
B7 noted, also stay: `crossVerseSuggest.ts` now uses both.

**Verification.** `npm run check` 0 errors / 0 warnings; Vitest 447 passed across
33 files; `npm run build` clean. The shell test was rewritten to assert the
absence of any `tab`/`tablist` role and of the three removed labels, rather than
deleted — the removal is the behaviour now.

## 2026-09-29 — The alignment compatibility scan skips raw-import stubs (#99)

`alignment_compatibility_scan` quarantined every alignment group whose
`bottomWords` was `[]` as `LEGACY_EMPTY_BOTTOM_WORDS_AMBIGUOUS`. But that is
exactly what a raw import writes for each not-yet-aligned source word —
`blank_source_alignments` emits `{"topWords": [<one token>], "bottomWords": []}`
per word — so a freshly imported book quarantined one row per word of a kind
nothing ever reads. The maintainer answered the design question this issue held
open: recognise the stub and skip it.

**The skip is shape-exact, deliberately.** `_is_raw_import_stub` requires the
group to have exactly the two keys, an empty bottom, and exactly one dict
topWord. A real translationCore group that merely lost its target side, or one
carrying a key we do not understand, is still something a human should see, so it
keeps being quarantined. Matching on `bottomWords == []` alone would have
silently swallowed those.

**Measured on the real project**, not on a fixture:
`hin_indian-revised-version_gen`'s `alignmentData` holds **20,588** groups of the
stub shape, **0** other empty-bottom groups, and 23 groups with a target side. So
the change drops 20,588 quarantine rows and loses nothing on that book.

The scan reports `rawImportStubsSkipped` alongside the existing counters, so a
skip is visible rather than merely absent.

**Two existing tests asserted the old behaviour** and were updated rather than
deleted, because the behaviour is what changed:
`test_native_alignment_ambiguities_are_quarantined_without_rewrite` and
`test_alignment_compatibility_scan_quarantines_in_one_batch` now build
genuinely-ambiguous records (two topWords, empty bottom) so they still test
quarantining and batching. `test_raw_import_stubs_are_skipped_but_real_empty_bottoms_are_not`
is new and pins both halves of the rule.

**Also in this commit, from the same issue.** `sidecar.rs` logged the `[trace]`
per-phase timing lines that `project.open` and `import_project` emit on every
successful call at `warn`, so a normal open read in the diagnostics panel as
though something had gone wrong. They are `info` now.

**Still open on #99**: verifying in a release build that a first open of a large
unopened book (Exodus, Psalms) completes well inside 30 s, and accounting for the
unexplained 43 s between the registry touch and the scan starting in the original
trace. Both need an installed build, so neither is done here.

**Verification.** `tests/semantic/test_passage_semantic_runtime.py` 32 passed;
`cargo check` clean; `cargo test` 10 passed.

## 2026-09-29 — Section headings leave the verse text they trailed (#180)

A verse's content ran from its `\v` to the next one, so a `\s` heading sitting
between two verses was stored **inside the earlier verse**. Two consequences:
a reader of verse 2 saw the next section's heading glued onto it, and the
heading's words were tokenised as alignable target words.

**Found by the first real-key AI run, not by reading code.** The 2026-09-29
`alignment.crossVerse.aiPropose` probe on `hin_indian-revised-version_gen`
Genesis 1 returned three proposals, and all three linked a Hebrew word to a
heading token — `יוֹם` to `दिन—उजियाला`, `אֶחָד` to `पहला`, `רָקִיעַ` to
`दिन—आकाश`, at 0.82–0.94 model confidence. The model's reasoning was sound
("the preceding heading carries the day designation"); it was handed a bad menu.

**Where headings live now.** `_split_trailing_headings` separates them at parse
time, and they are written to `<book>/<chapter>.headings.json`, keyed by the
verse each *introduces* — a heading between 2 and 3 belongs to 3. A sibling file
rather than a key inside `<chapter>.json`, because every reader of that file
treats each key as a verse number. Nothing mistakes it for a chapter: all chapter
globs filter on `stem.isdigit()`, and `1.headings` is not a digit.

A heading before the first `\v` of a chapter is captured too — the verse loop
never sees that text, so it would otherwise be lost.

**`\d` is deliberately not split out.** A Psalm superscription is translated
content in its own right; pulling it out would remove real text from alignment.
The set is `\s1-5`, `\ms1-3`, `\mr`, `\r`, `\sr`, `\sp`.

**On screen.** `chapter.verseData` carries `headings` alongside `verses` rather
than earning its own RPC — the editor needs both at the same moment, and that
call exists precisely to avoid a second round trip. `VerseList` renders each
above the verse it introduces, in `--font-target`, not selectable and not
checkable.

**Why this may matter well beyond display.** `can_complete`
(`bridge_service.py:1582`) requires `not alignment.word_bank`. A heading word sits
in the word bank and can never be aligned to any source word, so a verse carrying
a heading could never be marked complete — and completed alignments are the
offline half of #146's cross-verse agreement gate. That is consistent with the
measured state of this machine: **zero completed alignments across all 132
projects**, and a cross-verse AI probe that returned 0 auto-linkable proposals
because the corpus was empty. The mechanism is confirmed in code; that it is the
*sole* cause is not — those projects may simply never have been hand-aligned.
Worth testing directly on a fresh import of a book with headings.

**Existing projects are unaffected and keep working.** `chapter_headings` returns
`{}` when the sibling file is absent, so a project imported before this shows no
headings rather than failing. Their verse text still carries the heading inline;
re-import is what moves it.

**Verification.** Five new engine tests (split, keying by the introduced verse,
heading words absent from the alignable targets, the reader plus `chapter.verseData`,
and no sibling file when a book has none) and four new frontend tests. Engine
`tests/project_io` + `tests/service`: 192 passed. `npm run check` 0/0; Vitest 451
passed; `npm run build` clean.

## 2026-09-29 — A stable user id on every write, not a display name (#78, identity slice)

Scoped by the maintainer to identity only; the roles enum and `authorize()` from
#78's original scope are deferred to #46, which is when something will actually
consume them (#111 flags building that scaffolding ahead of its feature).

**Two competing answers to "who is this person".** `TranslationCoreProject.identity`
passed `AppSettings._seed_reviewer_name()` — the OS account — to
`get_or_create_local_user()` unconditionally. So `users.display_name` was always
the OS account, while `settings.reviewer_name` held whatever the person typed in
Settings, and nothing reconciled them. Confirmed against the repository before
changing anything: a rename is silently reverted on the next call. The `user_id`
was stable throughout, so no history ever detached — only the name shown was
wrong.

**Worse, writes recorded the name.** Eight dispatcher sites read
`p.get("actorId") or self.settings.reviewer_name or "human"`, and the frontend's
`reviewerActorId()` returned `settings.reviewerName`. Renaming yourself would
therefore have left old rows under the old name and new rows under the new one —
one person appearing as two actors, with nothing to tell you they were one.

**Now.** `BridgeEngine.local_user()` / `current_actor_id()` resolve the one local
user lazily (working it out opens the workspace database, and most requests never
write). Every dispatcher site stamps the id. `settings.get` returns `localUserId`
and the UI sends that instead of the name. A Settings rename refreshes the users
row immediately rather than at the next project open, and because the id is
untouched it re-labels every row that person has already written.

**Test updated, not deleted.** `V11-002: every actor-attributed call site sends
the identical, non-empty settings reviewer name` asserted exactly the contract
this reverses. Its intent — one shared helper so the eight sites cannot diverge —
still holds, so it now asserts the identical non-empty *local user id*, plus a new
guard that the display name never becomes the actor id again.

**Deliberately not in this slice**, to keep it reviewable:
- making `actor_id` a required parameter on the seven repository methods that
  still default it to `"human"` (about 50 call sites, mostly tests, entirely
  mechanical);
- whether a client may assert an identity at all. The engine still honours a
  wire-supplied `actorId`, which 26 engine tests use as a hook to stamp a known
  actor. Once there is a hub it must not, and that belongs with #46.

**Verification.** Engine `persistence` + `service` + `correction`: 649 passed,
including three new tests (the users row follows the chosen name; a rename keeps
the id; a write records an id that is neither the display name nor `"human"`).
`npm run check` 0/0; Vitest 451 passed; `npm run build` clean.

## 2026-09-29 — The frozen-sidecar smoke is a hard gate again (#130)

`project.inspectImport` reported a project it had just opened as a
`possibleDuplicate` of *itself* (`bookLanguageBible`) where the smoke expects
`exactDuplicate` (`sourceFingerprint`). `scripts/smoke_sidecars.py` therefore
failed on every release build, so `release.yml` had run it
`continue-on-error: true` since 2026-09-07 — and, as CLAUDE.md said for three
weeks, a green release build was not evidence the sidecars were healthy.

**Cause**, already diagnosed on the issue: `project.open` builds
`PassageSemanticRuntime`, which writes under `.apps/translationCoreAI/` *after*
`ProjectRegistry.register()` has snapshotted the tree. A project with no
`.bridge/import.json` falls through to `_tree_fingerprint()`, which hashed that
directory, so the project no longer matched its own fingerprint.

**Fixed wider than the issue proposed.** #130 suggested excluding
`.apps/translationCoreAI/passageSemantic/`. The whole directory is excluded
instead: it also holds `bridge-workbench.sqlite3`, which changes on every
decision, `transactions/`, which changes on every edit, and `backups/`. Excluding
only `passageSemantic/` would have left the identical bug reappearing the moment
anyone used the project. None of that tree is the project; it is Bridge's own
derived state.

**The tests were confirmed to fail without the fix**, not merely to pass with it:
disabling the exclusion reproduces `possibleDuplicate` on the first and a
fingerprint mismatch on the second. The second also asserts the fingerprint still
*moves* when a real project file changes, so the exclusion cannot quietly make it
useless.

**Verification, in the order that matters.** `tests/project_io` 47 passed. Then
the part this issue exists for: both sidecars rebuilt with
`scripts/build-sidecars.ps1` and `python scripts/smoke_sidecars.py
engine/dist/bridge-engine.exe` run against the frozen pair — **exit 0**, first
clean run:

> Frozen sidecar smoke test passed: real Wildebeest/Uroman loaded; pinned UGNT
> source tokens, versification, names/transliteration, alignment
> statistics/proposal packaging, AI explain packaging, desktop connectors,
> project registry/duplicate import, first-open live-review responsiveness,
> alignment/export/undo, and duplicate/missing-verse checks succeeded.

`continue-on-error` is gone from `release.yml` and CLAUDE.md's standing warning
is updated to match.

## 2026-09-30 — The Language QA project guard compared paths as strings (#184)

`main` was red from the language-qa merge. The Python suite itself passed
(4649); what failed was the separate **Language QA foreground latency gate**
step, with `project_error: Language QA request belongs to a different project.`

**The guard** (`bridge_service.py`, the five `languageQa.*` methods) was

```python
if p.get("projectPath") != str(self.project.path):
```

a raw string comparison. `project.open` *resolves* the path it is given, so a
caller echoing back the path it used is rejected whenever the two spellings
differ. On Windows `tempfile.TemporaryDirectory()` returns the 8.3 short name:

```
raw     : C:\Users\REVANT~1\AppData\Local\Temp\...\project     <- what the client sends
resolved: C:\Users\Revant C Idikulay\AppData\Local\Temp\...\project   <- what the engine stored
```

Every UI path worked, which is why this never showed in the app: the UI only
ever echoes a path the engine itself produced. `scripts/benchmark_language_qa.py`
builds its own project under a temp root and sends that spelling, so it was the
one caller that could trip it.

Now compared with `canonical_path_key()` — the helper that already existed in
`project_registry.py` for exactly this ("treats Windows path aliases
consistently"). Symlinked temp roots and case differences are covered by the
same change.

**Why three earlier reproductions found nothing.** The assertions discarded the
engine's response, so every run printed only `assert False`. `b48f202` made 25
of them report the response; the very next CI run named the cause. Worth
remembering: the fix was two lines, and the cost was entirely in not being able
to see the error.

**Verification.** The gate itself now exits 0 (p95 within budget: ping 1.26 ms,
`languageQa.status` and the other owned RPCs under their 50 ms contract). The
new test was confirmed to fail with the canonical comparison reverted, and it
also asserts a genuinely different path is still refused, so the guard still
guards. `tests/service`: 3488 passed.

## 2026-09-30 — #74 phase 4, stopped deliberately with the numbers written down

Phase 4 set out to take `bridge_service` importers among the tests from 32 to
~15, so that affected-test selection (phase 3) would stop degrading to a full
run. Two slices landed; the rest was stopped after measuring what it would cost
and what the ceiling actually is.

**What landed.**
- `tests/support/semantic.py`: one `semantic_runtime` builder replacing three
  drifted copies of `_runtime`, plus the `TAMIL` fixture that was byte-identical
  in all three files. Stage 6B/7/8's protocol tests moved to
  `tests/service/test_semantic_protocol_apis.py`; those three stage files no
  longer import the dispatcher.
- `tests/support/projects.py`: `fixture_project`, `two_book_collection`,
  `_write_minimal_book`, `call` and `wait_for_job`, which seven modules had been
  importing out of `tests/service/test_bridge_service.py`. **Cross-test imports
  7 -> 0** — a test module importing another test module was phase 1's
  unfinished business, and it also meant every borrower inherited the dispatcher
  transitively.

Importers 44 -> 42. The count moves slowly because freeing N files while creating
one destination file nets N-1.

**Why the rest was not worth doing.** Of the 42, **11 are inside
`tests/service/` and should import the dispatcher**, and **14 more are genuinely
coupled** — the dispatcher is used by a module-level helper every test in the
file calls, or every test in the file is a protocol test. Only **18 are
freeable**, and freeing all of them costs about four to five hours and lands at
roughly 29 importers once destination files are counted, not the 15 the issue
assumed. The issue's target appears to have been set without separating
legitimate importers from incidental ones.

At 29, a change to any `tc_ai_bridge` module still reaches about a third of the
suite, and `scripts/affected_tests.py` falls back to a full run at 60%. So
finishing phase 4 would most likely still not make phase 3 pay off.

The correction family alone illustrates the ratio: 5 files, 7 tests, **net -4
importers**, and it requires extracting 370 lines of helpers (`_publish_evidence`
157, `_fixture` 110, `_analysis_job` 63) that are each still used by 4-19 tests
staying behind — in the Stage 9B correction-ledger tests, the most
safety-critical area in the suite.

**Three mechanical hazards, all caught by running the tests, none by review.**
Recorded because the next person to attempt this will hit them:
- `ast.get_source_segment()` starts at the `def`, not the decorator. Copying that
  way dropped `@pytest.fixture` from two fixtures; removing that way left the
  decorators orphaned above whatever followed. 99 errors.
- An extracted `wait_for_job` called `job_timeout`, imported at the top of the
  file it came from. The copy lost the import and every job-waiting test raised
  `NameError`.
- A shared constant assembled from a partial read (`head -5`) silently lost the
  fourth verse of a four-verse fixture.

The guard that caught all three quickly was running `--collect-only` after every
move (one second, and the count must stay at 4659) and the touched files straight
after. Neither review nor type checking would have found any of them.

**Take it up again** if the suite becomes slow enough to be painful; the analysis
above is the starting point, not something to redo.

## 2026-09-30 — The Language QA latency gate gets a CI-only budget (#189)

Fixing the project-guard path comparison (#184) let this gate reach its
assertions for the first time since it was added, and it then failed:
`verse.decide (languageQa)` p95 **112.27 ms** against a 50 ms contract. So the
budget had been going unevaluated, not passing -- the script died before the
check, and the step reported neither outcome.

Identical code measures **13.79 ms** p95 on a Windows dev machine and 112.27 ms
on the CI runner's two contended cores. About 8x, for an environment no
translator uses. The pure scan is fine on both (p95 0.84 ms on CI), so this is
the foreground RPC under contention rather than the scanner.

**The maintainer chose to raise the budget so CI stops failing.** It is applied
as `--p95-budget-ms 300` on the CI step only; the script's default stays 50 ms,
which is what a developer gets locally and what the contract actually means.
Raising the default instead would have let a 20x local regression through
unnoticed -- the measured local number is 13.79 ms, so a flat 300 ms budget would
not notice anything short of catastrophe.

No code changed: `benchmark_language_qa.py` already took `--p95-budget-ms`.

**What this does and does not buy.** A green CI run now means the gate ran and
nothing took longer than 300 ms on two contended cores. It does not mean the
50 ms contract holds; only a local run says that. If the CI step starts failing
at 300 ms, that is a real signal rather than runner noise.

Verified locally: `--p95-budget-ms 300` passes (exit 0); the default 50 ms also
passes locally at 13.79 ms, so the local gate keeps its teeth.

## 2026-09-29 — One USFM parser for import: usfmtc behind `usfm_parser.py` (#91, step 1–2)

**Re-evaluated first.** The #91 plan (2026-09-17) made "import through a real
parser" the #92 fix. #180 landed today and already split headings out with a line
regex, so on the two IRV fixtures the #92 symptom was gone at the token level
(0 differing verses against usfmtc). What remained of #91 was consolidation, plus
anything the regex still got wrong — which only a wider corpus could show.

**What landed.** `engine/tc_ai_bridge/usfm_parser.py` wraps usfmtc 0.4.8 (pinned
exactly; the only module allowed to import it). usfmtc decides where each verse
starts and ends and which paragraphs are headings; the verse string is then cut
**verbatim** from the source using usfmtc's element line/column positions, not
re-serialised — so `\zaln`/`\w` markup, notes and every finding offset survive
byte-for-byte. `parse_scripture_file` reads through it; the `\id`/`\c`/`\v`/header
regexes and `_split_trailing_headings` are gone from `project_import.py`.
`\zaln` alignment extraction (`_verse_alignment`) still walks the stored slice with
its own regexes, deliberately: it operates on in-verse markup, which is unchanged.

**Parity, measured over 443 distinct local USFM files (209,715 verses).** Every
verse tokenises identically to the old importer except where the old one was
wrong:
- 263 verses the regex **lost outright** — a `\v` that does not start its line
  (`\q1 \v 1 …`, all of ESV poetry's chapter openings), because text before a
  chapter's first line-initial `\v` was discarded.
- 6 "verses" it **invented** from a `\v` with no number: IRV Isaiah's
  `\v \x - \xo 61:2 …` was stored under the verse key `\x`. The text now stays
  with the verse before it, the numberless token is removed, and a warning names
  the line. One case (a Kachchi Psalms `\v \x … \x*6 …`) is really verse 6 with its
  number after the xref, so it is now folded into 32:5 — wrong, but kept and
  reported, where the regex's `\x` key was wrong and silent. A judgement call,
  easy to change.
- Byte-level differences with identical tokens in 3 verses, all from that fold.

**usfmtc is quadratic, and slow.** Its lexer matches each tag and attribute
against `self.txt[m.end():]` — a copy of the rest of the input — so aligned USFM
(a `\w` and a `\zaln` on every word) exploded: 277 s for a 1.9 MB aligned
Psalms, 71 s for an aligned Genesis, past the 300 s import timeout on the first.
Parsing per chapter (each chunk padded with newlines so line numbers stay
absolute) took those to 7.8 s and 2.9 s, with a test that chunked equals whole on
both IRV fixtures. Even linear it runs at ~2 µs a character, ~20× the regex: all
66 IRV books take ~11 s. So the import **preview** now reads identity from each
book's preamble (`identify_usfm`) and fully parses only the first book — the one
an import normalizes eagerly — keeping `test_import_rejects_duplicate_verse_numbers`
meaningful. A malformed *later* book in a collection is now reported on first
open rather than in the preview: a real behaviour change. Parse and identity
results are cached by (path, size, mtime) because `project.import` previews again
inside the same sidecar.

**Other measured traps**, recorded in the module docstring: `USX.fromUsfm(str)`
opens its argument as a file when `os.path.exists` says so, and raises
FileNotFoundError on any short single-line string (input always goes in as a
StringIO); `\usfm` is folded into a document attribute, so the header is read back
from the source; the parse path does not print, but runs under `redirect_stdout`
anyway because stdout is the protocol.

**Not done in this step, and why** (the revised #91 plan has the detail): Stages
4–8 still read through `usfm_passages.py` / `passage_semantic_runtime.py`'s line
parsers, which feed Stage 6B windows and the goldens and parse synthetic USFM built
from stored verse strings — moving them needs a window-boundary parity snapshot
first. `strip_usfm`/`whitespace_tokens`, `aligned_usfm.py` masking and
`usfmNotes.ts` operate on the stored in-verse string, not on documents; the
frontend one re-parses live while editing, so moving it to the engine is a UI
design question, not a parser swap.

**Verification.** 20 new tests in `tests/project_io/test_usfm_parser.py`, plus
`test_a_malformed_later_book_is_reported_when_it_is_first_opened` (a duplicate
verse in a collection's second book raises on first open and leaves its source
untouched). Full engine suite on the branch rebased onto 0104670 (#130):
4,670 passed, 1 skipped, 3 xfailed, 0 failed (both goldens included, fixture
files untouched).

Frozen, rebuilt on that base: `smoke_sidecars.py` exactly as `release.yml` runs it
(the hard gate since #130) **passes end to end**, including the new check that the
frozen exe bounds a heading and a mid-line `\v`.

**The cost, stated plainly.** The smoke's optional `--import-source` 66-book gate
(10 s, not run by release): branch 10.21 s and 10.74 s, a frozen build of main
0104670 9.77 s and 9.36 s — **about +0.8 s on a cold 66-book import**, which puts
the branch just over a budget main clears by under a second. Split, in source:
`import usfmtc` 0.11 s, identifying 66 preambles 0.27 s, the first book's full parse
(Genesis, 1,533 verses) 0.44 s. The first two could only go by reading headers
with a regex again, which is the thing #91 removes; the third is the parser. So it
is left as a decision rather than tuned away: raise that budget, or accept a
second, header-only reader. (An earlier alternating benchmark that ran the preview
*before* timing the import showed the branch faster — 6.8–7.8 s vs 7.7–8.8 s — but
that preview warmed both caches, so it is not the cold number.) A single aligned
Psalms: 4.6 s → 9.0 s preview-plus-import. No frontend or Rust changes, so
npm/cargo gates were not re-run. Desktop not run.

**Decision (maintainer, 2026-09-30, while rebasing the branch onto `main` efb2683 for
merge):** raise the smoke's default `--max-import-seconds` from 10 to 15 rather than
add a header-only regex reader back. The +0.8 s is the parser doing real work on the
first book and 66 preambles, so the new limit carries that cost in its failure message;
a miss beyond it is a regression. A perf issue for `identify_usfm` is filed separately.
The rebase itself conflicted only in this file (both sides appended a section) and was
resolved by keeping both in date order. The full suite on the rebased branch:
4,670 passed, 1 skipped, 3 xfailed, and one failure —
`test_progress_cache.py::test_a_decision_is_one_workbench_commit_per_table_not_one_per_row`
— which passes alone on both the branch and `main`, so it is flaky under `-n auto`,
not a parser regression; filed rather than fixed here.

Re-measured on the maintainer's machine after the rebase, three cold runs each of the
same `project.import` alternating a frozen build of `main` efb2683 and the rebased
branch (`C:\code\tamil\IRV_Tamil`, 66 books): main 18.2 / 17.3 / 19.9 s, branch
19.2 / 20.0 / 17.8 s. The branch is inside main's own spread, so no regression — but
this machine is roughly twice as slow on this import as the one the 10.2–10.7 s came
from, and the new 15 s default fails here for `main` too. The optional gate is a
per-machine benchmark; the number that matters is branch against main on the same
box, and the budget default is left at 15 s pending a decision on whether it should
track a slower reference machine.

## 2026-09-30 — A Scripture write survives a reader holding the file (#184)

CI failed on `test_job_path_and_live_path_produce_identical_findings` with

    [WinError 5] Access is denied: ...\pytest-0\...\rut\rut\1.json

4649 passed, 1 error. The test passes locally in isolation in 4 seconds.

**The mechanism.** On Windows `os.replace` fails with `PermissionError` if ANY
handle holds the destination -- Python's `open()` does not request
FILE_SHARE_DELETE. Language QA's background pass reads every chapter of the book
(`language_qa_jobs.py` `_scan_locked` globs `*.json` and reads each), and the read
window is only a few milliseconds, but a `verse.edit` landing inside it is refused
outright. `apply_scripture_edit` writes through `_write_json_atomic`, whose last
step is exactly that replace.

**The fix is a bounded retry, not coordination.** `_replace_retrying` retries for
two seconds with exponential backoff, then raises the original error. Retrying
rather than quiescing the scanner, because the scanner is not the only thing that
opens these files: on Windows an antivirus or the search indexer does the same,
and no amount of internal coordination helps there.

It does not weaken the write. `os.replace` stays atomic; the retry only chooses a
later moment, and the temp file has already been written, fsynced and validated.
And it is not silent -- a permanently locked file still fails loudly after the
budget, so a real permission problem is not absorbed.

**Both tests were confirmed to fail without it**: with the retry reverted, the
first reproduces the exact CI error (`PermissionError: [WinError 5] Access is
denied`) and the second shows the write giving up in 0.0 s. The second also pins
the budget behaviour -- it must raise after roughly a second or two, not hang and
not swallow.

**Verification.** `tests/persistence` + `tests/service/test_language_qa.py` +
`tests/project_io`: 541 passed.

## 2026-09-30 — The write retry polls flat, not exponential (#184, #189)

The retry added earlier the same day (`d077aa0`) fixed the `WinError 5` failure
but took the Language QA latency gate from **112 ms to 410 ms p95** on CI, over
the 300 ms budget set in `efb2683`. So the fix traded a rare hard failure for a
slower write under contention -- a bad trade on a path a translator hits.

**Cause: the backoff, not the retrying.** The delay doubled 5 -> 10 -> 20 -> 40
-> 80 -> 100 ms, so a write that needed five attempts idled about 155 ms *after*
the reader had already let go. On a developer machine that is invisible (measured
15.89 ms without the retry, 19.37 ms with it, +3.5 ms); on two contended CI cores
the scanner holds files longer, more attempts miss, and the idle time dominates.

Replaced with a **flat 1 ms poll**. The reader holds the file for single-digit
milliseconds, so what matters is noticing quickly that it has let go, not being
polite about asking. Local p95 19.37 -> **17.39 ms**, and the CI number should
fall by roughly the idle time the doubling was adding.

The two-second ceiling is unchanged, and so is the behaviour it guards: a
permanently locked file still raises the original `PermissionError` rather than
hanging or being swallowed. Both race tests still pass, including the one that
pins the give-up budget.

**Worth stating plainly**: the first version of this fix was measured only
locally, where its cost was 3.5 ms and invisible. It took a CI run to show the
cost was contention-dependent. A latency budget in CI is what caught it -- the
same gate that had been failing to report anything at all until this morning.

## 2026-10-01 — Export writes into the parser's verse spans (#190, #91 Phase 0.5)

**The bug.** `bridge_service._source_preserving_usfm` rebuilt an export by
replacing everything from each `\v N` up to the next `\v`/`\c` with the current
stored text. Two things sat inside that range after #180 and #91:

- Section headings. #180 moved the `\s …` line and the `\p` after it out of the
  verse text into `<chapter>.headings.json`, so the export overwrote the source
  lines that held them and never wrote them back. Measured on an *unedited*
  import of Tamil IRV Luke: 285 source lines missing from the export, 174 of them
  heading lines; ESV Job lost 14. The existing export test only had a heading
  before verse 1, outside any replacement range, so it passed.
- Mid-line verses. The `^[ \t]*\v` regex never matched `\q1 \v 1 …`, so those
  verses — the ones #91 step 2 started importing — had their edits silently not
  exported.

**The fix.** The parser already knows where each verse's text came from; it now
says so. `UsfmVerse` gained `start` (just after the `\v N ` marker), `head_end`
(end of the verse's text before any heading inside it, whitespace excluded) and
`tail_spans` (Scripture lines that followed a heading *inside* the verse and were
folded into the stored text). `close_verse` computes ranges instead of strings
and derives `text` from them, so the 20 existing parser tests prove the text did
not move; a new test asserts the spans reproduce `text` for every verse of both
IRV fixtures. The exporter writes the current text into `[start, head_end)`,
deletes each tail span (the whole text is now at the head), and touches nothing
else. A numberless `\v` marker token the parser dropped at import is inside the
head region, so an export no longer carries it — the text it introduced is
already in the verse.

`read_usfm_text` in `usfm_parser.py` is now the one decoder for the preserved
source (UTF-8 with or without BOM, then the UTF-16 variants, newlines
normalised); `project_import._read_text` delegates to it and the exporter's
inline copy is gone. `passage_semantic_runtime._read_usfm` is the third copy and
moves in Phase 3, which rewrites that file anyway.

**Verified.** Engine: the new export test (heading between verses + `\q1 \v 2`
edited + round-trip re-import), two span tests, the existing export/aligned
round-trip tests unchanged; full suite below. Real data, unedited export diffed
against source: ESV Psalms, ESV Job, IRV Luke, IRV Psalms **all byte-identical**
on this branch (on `main`: Luke −285 lines, Job −14, the two Psalms identical
because their headings sit before verse 1). Frozen: sidecars rebuilt and the
release-style smoke run (see the commit). Not run: desktop export; QA matrix A88
says so.

Not changed: `export_aligned`'s `\usfm 3.0` insertion still uses its two small
regexes on the *rendered* output — they operate on text this function just
produced, not on a parse of the source, and the plan's note to move them to
`ParsedUsfm.header()` is left for Phase 4 cleanup.

## 2026-10-01 — One fragment reader: `usfm_verse.lift_verse` (#91 Phase 1a)

**What.** `engine/tc_ai_bridge/usfm_verse.py` is the one answer to "what does
this stored verse string show": `lift_verse(raw) -> LiftedVerse` with `plain`,
`notes` (kind, caller, reference, parts, text, position — the frontend's
`parseVerseNotes` shape), `styles` (character-style spans on the plain text),
`removed` (merged raw ranges), `raw_index`, `map_offset`, `raw_span` and
`to_dict` (the display payload Phase 2 will carry). Built by deletion only, so a
plain span that crosses no removed range is byte-identical to its raw span.
Total: it never refuses a verse; problems are `warnings`.

**Grown from, not beside.** The scanner is `language_qa.lift_inline_usfm`'s,
generalised: that function is now a thin adapter (`LiftedVerse(visible,
raw_index)` from `lift_verse`, and the refusal it always had — unbalanced
markers, note markup outside a note, a stray backslash, an unclosed attribute
bar — reads `warnings`, with the same message texts). Its 290+ tests pass
unchanged, including the lift table that mirrors the frontend's swallow rule.
The four regexes that lived in `language_qa.py` are gone.

**Marker classes** (the one list the parity test guards): notes `\f \fe \ef \x
\ex` removed with content and recorded; non-Scripture character content `\va
\vp \ca \cp \fig \rq` removed with content (the old lifter kept the alternate
verse *number* as text); attributes `|…` removed; every other marker token
removed, content kept, an opener's one following space going with it and a
closer's staying. `\fe`/`\ef`/`\ex` are new as notes — the old lifter refused
such a verse as "note markup outside a complete note"; now it is lifted.

**Not usfmtc, and why — measured.** usfmtc has element positions but no
text-node offsets, and per-verse readers run on every chapter open, check and
save. Instead `tests/project_io/test_usfm_verse.py` carries the parity test:
for every stored verse of both IRV fixtures (104 + 1,140), `lift_verse(s).plain`
collapsed equals the text walked out of usfmtc's own parse of `\id X\n\c 1\n\v 1
{s}` — **1,244/1,244 agree**, plus a hand-written edge table (nested `\+it`,
`\w|attr`, `\zaln`, `\qt-s` milestones, `\fe`, `\va`, `\rb|gloss`, an inline
`\q2`). The USX walker lives in the test; production has one fragment reader.

**Characterisation of today's `strip_usfm` against the new reader** (the input
Phase 1b needs before switching it; nothing switched here):

| Book | verses | `strip_usfm` text differs | `whitespace_tokens` differ |
|---|---|---|---|
| IRV PHP | 104 | 0 | 0 |
| IRV LUK | 1,140 | 14 | 0 |
| IRV PSA | 2,461 | 7 | 0 |
| ESV PSA | 2,461 | 383 | 7 |
| ESV JOB | 1,070 | 4 | 0 |
| ESV LUK | 1,149 | 45 | 45 |

Three causes, all understood. (1) `strip_usfm` replaces a marker with a *space*,
so `\nd Lord\nd*,` becomes `Lord ,` — text differs, tokens do not; the new
reader is right. (2) `\nd Lord\nd*’s` → old tokens `Lord`, `s`; new `Lord’s`
(ESV PSA's 7) — the new reader is right. (3) **A character-style opener glued to
the previous word**: ESV Luke has `lepers\wj*\wj in Israel` 45 times, and the
spec reading — the opener's following space is marker syntax — yields
`lepersin`. **usfmtc reads it the same way** (checked), so the scanner is
faithful to the reference and the parity test holds; the ESV file relies on a
space its markup does not provide. Whether Bridge should keep the word boundary a
reader would see there is a Phase 1b decision, recorded here rather than taken
quietly: today's `strip_usfm` happens to give `lepers in`, and switching it
changes alignment tokens on that text.

**Verified.** 18 new tests; `tests/service/test_language_qa.py` unchanged and
green; full suite and frozen smoke in the commit message. Frontend untouched
(`usfmNotes.ts` still parses until Phase 2; its "change both or neither" parity
comment in `test_language_qa.py` stays true).

## 2026-10-01 — Redundant `\wj*\wj ` pairs: a check with a one-click fix, and a script (#203)

**Found while measuring #91 Phase 1a.** The ESV source closes and reopens
`\wj` clause by clause with the space *inside* the reopened marker (`many
lepers\wj*\wj in Israel`). By the USFM spec that space is marker syntax, so a
faithful reader shows `lepersin` — usfmtc does, and so does `usfm_verse.lift_verse`.
Counted with the engine's decoder over 1,899 local files: 2,708 such `\wj` pairs,
2,674 glued to the previous word, all in the ESV. The old `strip_usfm` happened to
give `lepers in` because it replaces markers with spaces.

**Why the reader is not changed.** The KJV has 53 `\w*\w ` pairs such as
`who\w*\w soever`, where the glued reading "whosoever" is the *right* one, and
the corpus has 2,479 back-to-back notes (`\f*\f `, `\x*\x `) that are simply two
notes. A reader rule that keeps the space whenever an opener is glued to the
previous word would be wrong in every one of those. The markup is what is wrong,
and the maintainer chose to fix the markup.

**What landed.** `usfm_verse.redundant_style_reopens(raw)` finds every `\X*\X `
for a character-style `X` (`STYLE_MARKERS`: `wj nd it bd em add qt k sc tl …`;
never notes, never `\w`) and proposes the repair: drop the pair, keep one space if
it was glued. `normalize_style_reopens` applies them. Two consumers:

- **`USFM_REDUNDANT_MARKER`**, a local check in `local_checks.usfm_checks`, one
  finding per pair with the raw code-point span and the replacement. `QAIssue`
  gained optional `start_offset`/`end_offset`/`original_text`/`suggested_replacement`
  and `_qaissue_to_finding` carries them, so the frontend's existing
  `applySuggestedFindingFix` offers the fix with no UI change and writes it
  through `verse.edit`, the one Scripture writer, on a reviewer's click. Ids
  disambiguate by marker and ordinal, so a decision survives a re-check.
- **`scripts/normalize_usfm_markup.py`**, dry run by default, `--write` to repair
  source files in place keeping encoding and line endings — for reference texts,
  where 2,707 clicks is not a workflow. Dry run on the local ESV: 2,707 pairs in 8
  files (Revelation 124); KJV and IRV Tamil: 0. **Not run with `--write`** — the
  maintainer owns that corpus.

**Verified.** Detector and normaliser tests (glued vs not, nested `\+nd`, notes
and `\w` untouched), the RPC test that gets three findings on ESV Luke 4:27 and
applies them through `verse.edit` until none remain, the script test (dry run
changes nothing; `--write` keeps a UTF-8 BOM and UTF-16 with CRLF). Full suite and
frozen smoke in the commit. Desktop not run (QA matrix A89).

## 2026-10-01 — The tokeniser, the aligned exporter and the names check read through the fragment reader (#91 Phase 1b)

**What moved.** `usfm.py` is now a shim: `strip_usfm` is `usfm_verse.plain_text`
(visible text, whitespace collapsed -- collapsed on purpose, because tN/tW
selection counting in `tc_project._normalize_check_selections` and the dead
`TA_DOUBLE_SPACE` check, #191, both read it) and `whitespace_tokens` is
`usfm_verse.tokens`; `PAIRED_MARKERS`, `marker_balance_issues` and the one copy of
`WHITESPACE_TOKEN_TRIM_CHARS` live in `usfm_verse.py`. Every producer and locator
of tC `word/occurrence/occurrences` tokens -- import's word bank, the alignment
context, the edit reconcile, checks, the aligned exporter, the names check --
therefore tokenises the same text, which is the property that makes an alignment
written at import still match at check time. `aligned_usfm._masked_ranges` is the
reader's `removed` ranges; `bridge_service._first_token_span` looks for the token
in the plain text and maps the span back through `raw_span`, so a names finding on
`\nd Lord\nd*,` now gets offsets where the raw-string scan could not see it.
`scripts/build_ta_irv_pack.py`'s own `NOTE` regex is gone too (`lift_verse().notes`).

**What changed, measured, and pinned.** `tests/project_io/test_usfm_tokens_characterisation.py`
carries a verbatim copy of the regex implementation and asserts, over every stored
verse of both IRV fixtures (1,244): **tokens identical**, and the collapsed text
differing on exactly 0 (PHP) and 14 (LUK) verses, each by nothing but the space the
old code inserted for a marker next to punctuation. On English: `Lord’s` is one
token where the old code gave `Lord`, `s`; `\w word|x-occurrence="1"\w*` tokenises
as `word`, not `word|x-occurrence="1`. Both are corrections; both change tokens on
texts that have them, and pre-release that is a re-import, not a migration. The
glued-opener case (`lepers\wj*\wj in`) was settled by #203: the markup is repaired,
the reader is not bent. Delete the characterisation test in Phase 4.

**Verified.** 488 tests across every moved consumer (`tests/alignment`, names check,
Language QA, language pack, import, the #203 check) green; full suite and frozen smoke
in the commit. Not run: desktop (QA matrix A90).

## 2026-10-01 — The protocol carries what the reader shows (#91 Phase 2a)

**What.** `bridge_service.verse_display(text)` is `lift_verse(text).to_dict()`:
`{plain, notes, removed, styles, warnings}`, every offset a code point, the same
unit engine findings use. It rides on `verse.get`, on every row of
`chapter.verseData`, on the `verse.edit` result (the saved text's display, so the
reader refreshes from the response) and inside a correction application's
`canonicalEdit` result metadata (the frontend already reads `newText` there to
refresh a verse after Stage 9B.3b applies). `text` stays on every one of those:
the editor edits the raw string and suggested fixes splice it by raw offset.

**Why engine-side.** The frontend's `usfmNotes.ts` parses the raw string live on
every render and remaps finding offsets through its own map. With the payload
here, Phase 2b deletes that parser: one definition of "what the verse shows",
computed once per read next to the text it describes, instead of twice on two
sides of the protocol with a "change both or neither" comment holding them
together. Cost: `lift_verse` is microseconds per verse; `chapter.verseData` for
Psalm 119 (176 verses) adds well under a millisecond.

**Verified.** Four RPC tests (`test_verse_display_payload.py`: a footnoted,
styled Tamil verse through `chapter.verseData` and `verse.get`; `verse.edit`
returns the new text's display and a later read agrees; a plain verse has an
identity display) and one assertion in the production correction walkthrough
that `canonicalEdit.display.plain` is the corrected text. The frozen smoke's
Titus fixture gained `\nd eternal\nd*` in verse 2 and asserts `display.plain`
and the `nd` span in the frozen exe. Full suite and frozen smoke in the commit.
No frontend change; QA matrix A91.

## 2026-10-01 — The reader stops parsing USFM (#91 Phase 2b)

**What.** `usfmNotes.parseVerseNotes`, its `NOTE_RE` and its offset map are gone.
`VerseList` renders the engine's `display` (Phase 2a): `verseDisplay` is a store
keyed by `verseKey`, filled by `App.ensureChapterData` from `chapter.verseData`,
refreshed by `verseEditor` from the `verse.edit` result and a correction
application's `canonicalEdit.display`, cleared by `resetBookState`. The raw-offset
remap that `parseVerseNotes` used to do is `utils/verseDisplay.ts`: `plainOffset`
walks the engine's `removed` ranges (an offset inside one collapses to where the
removal left off, so a finding that lived inside a note covers nothing),
`utf16Offset` then converts the code-point result to the UTF-16 index a JS slice
needs, and note positions go through `plainToUtf16` before `withNoteMarkers`, which
is all `usfmNotes.ts` still does. That second step is the latent fix: the old map
mixed code points (engine) with UTF-16 (JS), invisible in Tamil only because Tamil
is in the BMP. `\it`, `\nd`, `\wj` disappear from the reader; they arrive as
`styles` spans and are not drawn yet (optional Phase 5).

**Two edges decided.** (1) A verse with no display renders its raw text through
`identityDisplay`: the optimistic Language QA "Use" shows the new raw text for one
round trip, then the engine's display — the old display must not stay, because it
describes the old text (`showVerseText` drops or replaces it in the same tick; the
undo restores it). An older engine that sends no `display` leaves the reader on
raw text, never broken. (2) `TranslationHelpsReview` counts selection occurrences
on `display.plain`, not the raw string, so the selection editor, the reader's
highlight and the engine's `strip_usfm` count agree by construction.

**Tests.** Fixtures are the engine's own output: `verseDisplayFixtures.ts` is
generated from `usfm_verse.lift_verse` for every raw string the frontend tests
seed (18), with `displayFor(raw)` falling back to the identity display, so no test
re-implements the lifter. `VerseList.test.ts`'s "leaves styling markers in the
text" became "does not show styling markers" — the one assertion flipped on
purpose. `verseDisplay.test.ts` carries the three offset-mapping cases that used
to live in `usfmNotes.test.ts` plus a deletion-only check over every fixture;
`verseEditor.test.ts` pins the optimistic-save display handling and the no-display
fallback. `test_language_qa.py`'s "change both or neither" comment is rewritten:
the engine is the only side.

**Verified.** `npm run check` 0 errors / 0 warnings; Vitest 530 passed (one
click-budget timing test failed once under another session's cargo build and
passed twice alone); `npm run build` clean. Engine untouched in this slice except
that comment. Not run: desktop (QA matrix A92).

## 2026-10-01 — Phase 3a: a snapshot of the passage windows, and a two-level cache on the overlay (#91)

**Why first.** Phases 3b/3c move `UsfmPassageIndex.from_text` and
`build_current_text_overlay` from their line regexes onto `usfm_parser`. Window
boundaries feed Stage 6B's structural scopes and the passage fingerprint, so a
boundary that moves would move the Stage 6B golden — and a moved golden is a
finding, never a re-baseline. So before anything is swapped: record what today's
code produces, and make the overlay cheap enough that a parser twenty times slower
than the regex can sit under `rebuild_current_passage`, which runs in loops.

**The snapshot.** `tests/semantic/test_passage_windows_snapshot.py` records, for
both IRV fixtures (`UsfmPassageIndex.from_path`) and three fixture-project shapes
(`build_current_text_overlay` on the shared `semantic_runtime` project, the Stage
6A test's heading-and-`\q1` shape, and the runtime test's marker-bearing chapter
JSON): every window's id, references and fingerprint, every segment's reference
and text hash, the structure markers and the mismatches. IRV PHP: 104 segments in
84 windows; IRV LUK: 1,140 in 983. The file,
`tests/fixtures/passage-windows-snapshot-v1.json`, has no `golden` in its name on
purpose — it is a characterisation snapshot, written from **main's module before
this refactor** (checked: the module that wrote it has no `clear_overlay_caches`),
and it is deleted in Phase 4. Regenerating it is `BRIDGE_WRITE_PASSAGE_SNAPSHOT=1`,
a deliberate act.

**The cache.** `build_current_text_overlay` used to interleave the regex pass over
the preserved source with the current-text matching, so nothing could be cached.
The pass is now `_source_skeleton(source)`: a pure function of the file text that
yields chapter/verse/marker events in document order with every body discarded.
Level 1 caches those events by the file's SHA-256, which the overlay already
computed for `structure_hash` (the file is now read once, not twice). Level 2
caches the finished `CurrentTextOverlay` by `(path, source hash, hash of the
current chapter JSON)`. A verse edit misses level 2 and hits level 1; a changed
source misses both; the authoritative-text guard runs on every fill and raises
before anything is stored, so a bad build is never served. Both are small LRUs
(8 entries) behind one lock; `clear_overlay_caches()` exists for tests.

**Measured** (`pytest tests/semantic --durations=40`, each run alone):
233 tests in 311.8 s before; 239 tests (the six new ones included) in 277.3 s
after. Over the 34 slowest tests present in both lists: 183.8 s → 149.2 s. The
biggest single moves are the Stage 8/9B tests that rebuild passages repeatedly
(7.8 s → 4.4 s, 8.8 s → 5.6 s). This is the unchanged regex code; 3c puts usfmtc
under it.

**Verified.** The snapshot test passes against both the pre-refactor module and
this one; five cache tests (`test_overlay_cache.py`); the 34 runtime tests
unchanged; full suite and frozen smoke in the commit. Both goldens pass by name
and their fixture files are untouched.

## 2026-10-01 — Phase 3b: the passage index reads through the parser (#91)

**What.** `ParsedUsfm` gained `structure`: every chapter, verse and
paragraph-level marker of the body in document order, each with the verse it
introduces (a `\p` or `\s` before `\v 3` carries "3"; one after a chapter's last
verse carries ""). `UsfmPassageIndex.from_text` now builds one segment per parsed
verse, its text from `usfm_verse.plain_text`, and a window boundary before the
first verse of a chapter and before any verse preceded by a strong paragraph
marker (`_STRONG_BOUNDARY_MARKERS`, unchanged); the terminal-punctuation flush is
unchanged. `strip_usfm_inline` is an alias for `plain_text`; the index's seven
regexes and its non-Scripture marker set are gone. `from_path` decodes through
`read_usfm_text`. Public API unchanged; the runtime's three call sites and the
Stage 3 tests needed no edit.

**What the snapshot caught.** Window boundaries: **zero moved**, across IRV PHP
(84 windows), IRV LUK (983) and the three overlay shapes; structure markers and
mismatches identical. Segment text: 150 Tamil Luke verses changed, every one by
spacing only and every one longer than before — the old `_INLINE_MARKER_RE`
(`\[A-Za-z0-9]+\*?(?:\s+)?`) deleted the whitespace after a *closing* marker as
well as an opener's, so `\wj text\wj* more` read as "textmore". The fragment
reader keeps a closer's following space (a rule pinned since Phase 1a). Those 150
fingerprints are the whole diff of the re-recorded snapshot (300 lines: 150
segment hashes, 150 window fingerprints), done in its own commit. Nothing reads
Luke's segment text into a golden; Stage 6B's fixture verses carry no markup.

**Also fixed on the way, by construction:** a mid-line `\q1 \v 1` is a verse for
the index now (the regex folded it into the previous verse with the number as
text), and `\d` superscriptions and verse continuation lines behave exactly as
before (they were in the verse's text region already).

**Verified.** Two new parser tests (structure events on a synthetic book; one
verse entry per verse on both IRV files); the snapshot test against the
re-recorded file; `test_semantic_mapping_stage3.py`, the runtime and cache tests,
and **both goldens by name** — 114 tests in that targeted run; full suite and
frozen smoke in the commit. Fixture files other than the snapshot untouched.

## 2026-10-01 — Phase 3c: the overlay's skeleton comes from the parser; no USFM regex left in the engine (#91)

**What.** `passage_semantic_runtime._source_skeleton` -- the pure, cached
(Phase 3a) function that turns the preserved source into chapter/verse/marker
events -- now reads `ParsedUsfm.headers` (the pre-chapter `\id`, `\usfm`, `\ide`,
`\h`, `\toc*`, `\mt*` lines, with the metadata ones still kept out of the
synthetic text) and `ParsedUsfm.structure` (every chapter, verse and paragraph
marker of the body, in order). The inline markers recorded for a verse or
paragraph line are `usfm_verse.marker_names` of that line after its own marker,
cut at the next structural element -- so a mid-line `\q1 \v 2` is a paragraph
event then a verse event, where the line regex saw a paragraph whose body
contained a `\v`. A `\c` that does not start its line is a chapter now. The
runtime's `_CHAPTER`, `_VERSE`, `_LINE_MARKER` and `_INLINE_MARKER` regexes are
gone; `_decode_usfm` is `usfm_parser.decode_usfm_text` plus this module's own
"no `\c` or `\v` means no skeleton" fallback. The synthetic text, the matching
against current verses, the mismatches and the authoritative-text guard are
untouched.

**One usfmtc quirk surfaced, fixed in the parser.** usfmtc opens an *implicit*
`\p` when a verse follows `\c` with no paragraph marker between them, and reports
it at the chapter marker's position. `parse_usfm` passed it into `structure` as a
paragraph, which put a phantom PARAGRAPH marker into the overlay (the Phase 3a
snapshot caught it on the Stage 6A shape: order 5 was `p` where `v` had been). A
real paragraph's source marker matches its style; the phantom's does not, so the
parser now drops it. This also tightens Phase 3b's `from_text`, which had been
taking a boundary from it -- harmless there, because it only ever sat before a
chapter's first verse, which is a boundary anyway.

**Verified.** `test_overlay_skeleton.py` (the event stream for a book with
headers, notes, styles, a mid-line verse and a mid-line chapter; the overlay's
structure markers for a note and a style on the verse line); the Phase 3a
snapshot **identical, structure markers and mismatches included**; the runtime,
cache, parser, reader and import tests -- 122 in the targeted run; both goldens
by name; full suite and frozen smoke in the commit.

**Where #91 stands after Phase 3.** Import, export, checks, alignment, the
reader, Language QA and Stages 4–8 all read USFM through two modules:
`usfm_parser.py` for a book, `usfm_verse.py` for a verse. What remains is Phase
4: delete the Phase 1b characterisation test and the Phase 3a snapshot, the
`export_aligned` header regexes, `paratext_notes`' `startswith("\v ")`, and
bring ARCHITECTURE/INVARIANTS/IMPORTS up to date.

## 2026-10-01 — Phase 4: cleanup, and #91 closed

**Deleted.** `tests/project_io/test_usfm_tokens_characterisation.py` carried a
verbatim copy of the regex tokeniser to prove Phase 1b changed no Tamil token;
that regex is gone, so the copy has nothing to characterise. `tests/semantic/
test_passage_windows_snapshot.py` and `tests/fixtures/passage-windows-snapshot-v1.json`
guarded Phases 3b/3c; both landed with every boundary unchanged. Neither was a
golden, and the goldens -- which exercise the same pipeline end to end -- stay.
`test_overlay_cache.py` stays too: the cache is production behaviour.

**The last two small readers.** `export_aligned` found or inserted `\usfm 3.0`
with two regexes over the rendered output; `UsfmHeader` now carries the marker's
source offset and the exporter uses `parse_usfm(content).headers` to replace the
version line or insert after the `\id` line. `paratext_notes._verse_snapshot`'s
`startswith("\v ")` is left alone on purpose: it tests whether a snapshot string
is already in Paratext's CommentList shape, which is a format check on Bridge's
own output, not a read of USFM.

**Frontend.** `usfm-js` was a listed dependency with no import anywhere in
`src/`; removed, and its row in DEVELOPER_GUIDE's dependency table with it. The
frontend has no USFM parser, in code or in `node_modules`.

**Docs.** CLAUDE.md gotcha 14 is rewritten as the end state rather than a phase
log: two modules, what each owns, the usfmtc quirks the parser hides, and the
rule that a new USFM edge case is fixed in one of them with a parity or snapshot
test, never with a regex elsewhere. ARCHITECTURE's module table names both
modules; INVARIANTS §44.8 gains the rule; IMPORTS describes the fragment reader
beside the document parser.

**What #91 delivered, in one place.** usfmtc behind `usfm_parser.py` decides verse
boundaries, headings, structure and headers for import and export; `usfm_verse.py`
decides what a verse shows for checks, tokens, alignment export, the names check,
Language QA, the passage index and the reader; the protocol carries `display`
and the frontend renders it. Corpus parity: 443 files / 209,715 verses identical
to the old import except its own errors; 1,244 fixture verses identical between
the fragment reader and usfmtc; zero passage-window boundaries moved. Found and
fixed on the way: export lost between-verse headings (#190); `\wj*\wj ` pairs glued
words in the ESV (#203, check + script); the regex index glued words after a
closing marker (150 Luke verses); usfmtc's implicit paragraph. Filed, not fixed:
#191 dead double-space check, #192 preview parser cost, #193 flaky test. Left as
the maintainer decided: the 15 s import smoke budget.

**Verified.** Export and parser tests; full engine suite, `npm run check`/`test`/
`build` and the frozen smoke in the commit.

## 2026-10-07 — The Collection QA runner gets an issue and a place in the docs (#215)

The "Run QA on all N books" panel at the top of the dashboard was asked about and
turned out to have no issue of its own and no entry in ARCHITECTURE. It is Benz's
`46e0e3d` (2026-09-24, layered-rules Phases 4.4 and 4.5), tracked only as one line
in #169's checklist and QA matrix row A70. #215 now records the feature as
shipped, verified against the code today: the ordinary check job per book (local,
greekroom, languageQa) in a private project handle, never automatic, read-only
app while active, resumable through `collection.json` `qaRuns[]` keyed by a sha256
of the chapter files, pause between books, cancel keeps nothing half-written, one
final whole-collection stage (termbase coverage, cross-book names; house-style
propagation reported unavailable), and the measured whole-Bible runs (1 h 41 min on
2026-09-24, 62 min on 2026-09-28).

**Docs changed.** ARCHITECTURE §3 now says `collection.json` carries `qaRuns[]` and
`qaFinalStage`; §5 adds `CollectionQaPanel` to the dashboard node. CLAUDE.md's
multi-book section gains a paragraph on the runner. DEVELOPER_GUIDE's roadmap
table had no Language QA row at all, so the whole of #169 was invisible there; it
now has one, pointing at LANGUAGE_QA_PLAN. Not touched, already stale and noted on
#215: ARCHITECTURE §5 still lists the Semantic and Passage tabs retired by #129.

**Verified.** The six engine test files `affected_tests.py` selects for a docs-only
change (165 tests) pass. No code changed.

## 2026-10-08 — Automatic cross-verse alignment, part 1: null alignment decisions (#216)

The first of seven parts of automatic cross-verse alignment (#216–#222, extending
#214; plan agreed with Benz 2026-10-07/08, mockup
https://claude.ai/artifact/C75bSQPQEfCUEAs2dyNZXc). This part gives Bridge
somewhere to say "this word has no counterpart, and that is correct". Before it,
a Greek article with no Tamil word, or a Tamil word that is pure grammar, could
only stay a gap, and Stage 8 could only read it as a possible omission or
addition.

**Storage: workbench v5 → v6.** Two tables. `alignment_null_decisions` is a
sibling of v3's link table: chapter, verse, side, signature, reason and state are
lifted, and it is unique on (book, chapter, verse, side, signature).
`alignment_verdicts` is the per-verse cache the automatic pass (#219–#221) will
write; it is created now so the series needs one bump, not two. Neither carries a
CHECK on side or reason. v5 shows that widening a CHECK costs a table rebuild;
both sets are validated in `alignment_null_decisions.validate`. **Schema-number
collision:** `indic-qa-editor` (not on main) also takes v6 for its Language QA
batch tables. Whichever branch merges second renumbers its block to v7.

**Store.** `alignment_null_decisions.py` mirrors `cross_verse_links.py`: three
writes per change, history without a backup, and invalidation on a text edit
inside `apply_scripture_edit`'s transaction (reported as
`nullDecisionsInvalidated`). Decisions on the source side are never invalidated
by an edit, because no edit removes a source token. A source signature the load
cannot resolve is reported `stale` on read, not written. Two differences from
the link store, both deliberate:
- `origin` is on the row, not only the event, so the automatic pass can
  supersede only its own rows;
- the history id includes the row id. `_timestamp` has millisecond resolution,
  and a burst of decisions in one verse would otherwise overwrite each other's
  history row. The link store has the same latent collision; the automatic
  pass's link groups (part 2) will need the same fix there.

**Service.** `alignment.null.set` / `.clear`. A token that already has a home (a
group with target words, any target group, or either end of an active link)
cannot be marked, and a marked token cannot be linked. `gap_ids` takes
`null_source_ids` / `null_target_ids`, so `_alignment_context` and `gapScan`
share one definition. The context gains `nullDecisions`, `accountedBy` and
`accounted`. `fullyAccounted` now counts nulls as well as links. The digest is
folded into `alignment_state_digest` only when a book has decisions, so every
existing project keeps the digest, and the cached runs, it had.

**Not in this part.** `_save_alignment` (realign / save / aiApplyProposal) does
not yet refuse a token that has a null or a link. That is part 2 (#217), with
the N:M link groups.

**Verified.** 13 new tests (`tests/alignment/test_alignment_null_decisions.py`,
`tests/service/test_alignment_null_rpc.py`) pass. Full engine suite with
`-n auto`: 4735 passed, 1 skipped, 3 xfailed. `npm run check` is clean. Neither
golden moved. Frozen sidecars not rebuilt for this part.

## 2026-10-08 — Automatic cross-verse alignment, part 2: one token one home, N:M link groups (#217)

**The hole it closes.** The #117 link store refused only an exact duplicate pair.
One Greek word could be linked into two different verses with nothing tying the
links together, and after part 1 a word could also be "grammatical" and aligned
at the same time.

**Groups.** `groupId` lives in the link payload. It is a sha1 of the members'
chapter:verse:signature, prefixed `xvg_`. The unique pair index, the per-verse
reads and the Stage 6B reader are unchanged. No column was added and there is
no schema bump; grouping is done in Python over the rows of one source verse.
A group is written and removed as a unit: N×M rows and events, one history row.
Membership never changes in place. A single-pair drop onto a word already
grouped between the same two verses extends the group by unlinking it and
linking the union, because refusing it would make dragging a second word
useless. A text edit that removes any target member invalidates the whole
group. Unlinking any pair removes the whole group. This is visible in today's
UI: × on one card of a 1:N group removes them all. The part 7 UI draws the
group.

**One home.** Each of these refuses a token that already has a home elsewhere:
`link_cross_verse`, `alignment.null.set`, and the new
`_refuse_if_homed_elsewhere` in `_save_alignment`. In `_save_alignment` only
tokens that *gain* a group in that save are checked. A token that was already
double-homed before this change is not repaired silently; I don't know of any.
The AI review's auto-align path also goes through `_save_alignment`; a refusal
there is caught by its existing "must not sink the review" handler.

**History id collision**, noted in part 1, is fixed in the link store too: the
record id is now part of the history id.

**Verified.** 8 new tests (`tests/service/test_alignment_one_home.py`). The
existing `test_alignment_cross_verse.py` passes unchanged once the
exact-duplicate case refuses instead of "extending" into itself, which was the
one bug the existing tests caught. Full engine suite with `-n auto`, run on this
part alone (later parts stashed): 4743 passed, 1 skipped, 3 xfailed.
`npm run check` is clean.

## 2026-10-08 — Automatic cross-verse alignment, part 3: Stage 6B/8 read groups and null decisions (#218)

**Why.** On a verse with no alignment, Stage 8 raises `POSSIBLE_OMISSION` for
every unlocated content word. Parts 1 and 2 give a reviewer, or the automatic
pass, a way to say "grammatical", "implicit" or "this whole group, across the
verse boundary". Stage 8 still could not hear it: links reached Stage 6B only
as 1:1 pairs, and decisions did not reach it at all.

**Evidence.** `_cross_verse_precedents` folds rows by `groupId` into one
precedent per group, with every member or none. The new
`null_precedents_for_range` resolves active decisions in the range through the
same resolvers the tC path uses: exact NFC word plus occurrence on the pinned
pack for a source token, and the current text revision for a target word. It
never raises.

**Stage 8.** `source_coverage_for` and `target_support_for` take an optional
`null_precedent`. `QaAuditEngine._unit_null` applies it to a unit only when
every token of the unit carries a decision with the same reason, so a decision
on one word never explains its neighbour. A source decision explains only an
absence (NOT_LOCATED, or no relationship). AMBIGUOUS and SEARCH_INCOMPLETE stay
UNCERTAIN; I didn't widen that. The plan listed "should a *human* null override
AMBIGUOUS?" as a question, and it is still open. A target decision comes before
the English and Tamil word lists.

**Versions.** `tc-word-alignment-v4` and `qa-policy-v2`. Neither string appears
in either golden. The three pins in `test_word_alignment_evidence.py` move with
the bump, and that file's version test is renamed `..._is_v4`.

**Verified.** 8 new tests (`tests/semantic/test_null_and_group_evidence.py`).
The end-to-end test shows θεός, unlocated in a Tamil verse that does not name
God, going from `POSSIBLE_OMISSION` to `COVERED_BY_RESTRUCTURING` once marked
IMPLICIT. The run fingerprint moves, and every other unlocated word in the verse
is still reported. Full engine suite with `-n auto`, run on this part with later
parts' files ignored: 4751 passed, 1 skipped, 3 xfailed. Both golden tests pass
unchanged.
