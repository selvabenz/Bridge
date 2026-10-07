# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Read it before writing code here. It records the constraints that are not visible
from the source and the ones that are expensive to get wrong. If a request in your
session conflicts with anything in this file, stop and say so rather than working
around it — ask the maintainer (@RevantCI).

`CONTRIBUTING.md` is the companion: this file holds the technical constraints, that
one holds the idea → issue → PR flow.

## What this is

Bridge is a local-first Bible translation QA workbench (a rewrite of a legacy
Python/Tkinter tool): one Tauri+Svelte desktop window driving a single
long-lived Python sidecar process (`bridge-engine`) over a JSON-lines
stdio protocol. The sidecar composes Greek Room QA/NLP checks (Wildebeest,
USFM structural checker, versification) with 30+ pre-existing
`tc_ai_bridge` business-logic modules (translationCore project I/O,
alignment, import, Paratext/Logos, transaction safety) — none of that
existing logic was rewritten, only wrapped behind one protocol.

Windows (`x86_64-pc-windows-msvc`) is the only verified target so far;
macOS/Linux are planned but unverified.

## Commands

### Python engine (`engine/`)

```bash
cd engine
pip install -e ".[dev]"
pytest -n auto            # full suite, parallel (pytest-xdist) -- ~3x locally; CI stays serial, see ci.yml
pytest -n auto -m "not slow"   # inner loop: drops the sidecar/subprocess/concurrency tests
pytest tests/correction   # one area; tests/ is packaged by engine area (see below)
```

`testpaths`, `pythonpath`, markers and `--durations=25` live in
`engine/pyproject.toml` (`[tool.pytest.ini_options]`), so a bare `pytest` from
`engine/` collects both roots. `engine/tests/` is a package tree mirroring the
engine: `service` (dispatcher, stdio, protocol), `jobs`, `persistence`,
`semantic` (Stages 3–8), `review` (9A), `correction` (9B), `alignment`, `ai`,
`project_io`, `connectors`, `resources`, `versification`; shared helpers in
`tests/support/` (`paths.py` for repo paths — never `Path(__file__).parents[N]`;
`projects.py` for fixture projects and the protocol `call`/`wait_for_job`;
`semantic.py` for a passage-semantic runtime).
Markers are registered with `--strict-markers` (`slow`, `subprocess`, `desktop`,
`resources`, `stage3db`, `external`); directory- and file-level ones are applied
automatically in `tests/conftest.py`. `scripts/affected_tests.py` runs the tests
your uncommitted changes could affect — useful for frontend, docs and test-only
edits, but it degrades to the full suite for any engine module, for the reason in
the next paragraph. The full suite on every push to `main` is the gate; there is
no nightly or weekly run.

**Two rules when adding an engine test.** Both exist because
`bridge_service.py` is 5,300 lines and 42 test files import it, so "which tests
could this change affect?" has no useful answer (#74 phase 4, stopped
deliberately — see `docs/BUILD_LOG.md` 2026-09-30 for why finishing it does not
pay off):

1. **A test that drives an RPC belongs in `tests/service/`**, not appended to the
   test file for the module behind it. One protocol test at the bottom of a stage
   file makes that entire file import the dispatcher; that is how six stage files
   ended up coupled to it. `tests/service/test_semantic_protocol_apis.py` is
   where the Stage 6B/7/8 ones live.
2. **Never import from another test module.** `from tests.service.test_bridge_service
   import fixture_project` was in seven files and is now gone: a borrowed helper
   drags the whole importing module's dependencies with it. Shared builders go in
   `tests/support/`.

**If you extract a helper**, take its decorators and its imports with it.
`ast.get_source_segment()` starts at the `def`, so a copy loses `@pytest.fixture`
and a removal leaves it orphaned on whatever follows; and a helper that calls
something imported at the top of its old file breaks silently in its new one.
Both happened during #74 phase 4 and both were caught only by running the tests.

Single test file or test: `pytest tests/service/test_bridge_service.py -v` or
`pytest tests/service/test_bridge_service.py::test_open_real_fixture_project -v`.
Set `PYTHONDONTWRITEBYTECODE=1` first if re-running after editing vendored
files (stale `.pyc` in `vendor/*/__pycache__` can otherwise mask an edit).

Sanity-check the raw protocol without any UI: `echo '{"id":"1","method":"ping","params":{}}' | python main.py`.
`python demo.py` runs a live walkthrough against a throwaway fixture project.

Real Wildebeest (`wildebeest-nlp`, not the PyPI package literally named
`wildebeest` — that's an unrelated project) requires **Python 3.12, not
3.13** (a 3.13 compile-time change rejects a lone-surrogate escape in one of
its docstrings). Without it, `WildebeestAdapter` degrades to a mock
automatically — `pip install -e ".[dev]"` alone always works; add the
`wildebeest` extra only on a 3.12 interpreter.

### Frontend (`/`, repo root)

```bash
npm install
npm run check    # svelte-check, should be 0 errors/warnings
npm run test     # Vitest component/store tests (jsdom) — added Stage 9A.2
npm run build    # production Vite build
npm run dev      # browser-only dev server at localhost:1420 — no sidecar, bridge.ping() etc. will fail
```

`npm run check` + `npm run test` + `npm run build` are the frontend
verification gate. Vitest uses jsdom, which does not lay out or paint — it
catches logic/structure regressions, not real 1366x768 rendering; a visual
check still needs the actual desktop app.

### Full desktop app (Windows, MSVC toolchain + Rust required)

```powershell
npm install
.\scripts\build-sidecars.ps1
npm run tauri dev
```

`build-sidecars.ps1` builds **two** PyInstaller executables from
`engine/bridge-engine.spec` and `engine/bridge-usfm-checker.spec` and copies
both target-triple-suffixed binaries into `src-tauri/binaries/` — both are
required; `bridge-engine.exe` deliberately never re-invokes itself to run
the USFM checker script. Verify the frozen pair directly (not just source)
with `python scripts/smoke_sidecars.py engine/dist/bridge-engine.exe`.

`src-tauri/` has both: `cargo check`/`cargo build` for compile success, and a
handful of real `#[test]` unit tests (`sidecar::tests` — 9 as of 2026-09-16;
`passage_semantic_wire.rs` and its tests were removed in #103) run with
`cargo test`. Run both, not just the compile check.

## Architecture

### Process boundary

```
Bridge.exe (Tauri/Rust shell + Svelte frontend)
   │  spawns once at startup, JSON-lines over stdin/stdout
   ▼
bridge-engine (PyInstaller-bundled Python sidecar, stays alive all session)
   │
   ▼
tc_ai_bridge business logic  +  GreekRoomEngine QA adapters
```

`engine/bridge_service.py` (`BridgeEngine`) is the single dispatcher — read
it first. It composes `greek_room_engine/engine.py`'s `GreekRoomEngine`
(offline QA adapters) with the 30 `tc_ai_bridge/*.py` modules, and exposes
one flat JSON-RPC-style method namespace (`Methods` class + `if m ==
Methods.X:` chain in `handle_request`). Every result — whether from
Wildebeest, the USFM checker, or a `tc_ai_bridge` QAIssue — gets normalized
into one `QaFinding` shape (`greek_room_engine/models/finding.py`) so the
UI never special-cases which engine produced it.

Three-way division of responsibility that the whole design hinges on:
Greek Room says "this is objectively suspicious," AI (when wired up) says
"here's what it may mean," the human decides. Nothing auto-applies to
project files — every finding carries an explicit review `status`.

The one thing that now applies without a per-item click is a **cross-verse
link** (#146), and the boundary is worth stating precisely because it is the
boundary: a cross-verse link is a Bridge-private workbench row, not a project
file — `alignmentData/` is untouched, unlink is ordinary, and the write is
journalled to the append-only `change_log` with `origin: "ai-auto"`. It happens
only where an LLM proposal and the offline corpus scorer independently choose the
same uncontested pair, and only on an explicit "Suggest with AI" click. Two
methods agreeing is the gate; a confidence threshold on one number is still not,
anywhere. Nothing about Scripture, tC alignment data or findings changed.

### Two different vendoring shapes for the same upstream repo

Both the USFM structural checker (`engine/vendor/greekroom-usfm/`) and
versification support (`engine/vendor/greekroom-versification/`) are
unpublished code pulled from `BibleNLP/greek-room` at a pinned commit — not
real dependencies, not on PyPI. **They're integrated differently, and that
difference is deliberate, not inconsistent:**

- The USFM checker is a 4,000-line CLI script with no reusable functions,
  so it runs as an isolated subprocess/helper executable
  (`bridge-usfm-checker[.exe]`), invoked via `UsfmAdapter`.
- `versification.py` is a genuine library (real classes/methods on
  in-memory dicts), so `tc_ai_bridge/versification.py` imports it directly
  into the long-lived `bridge-engine` process — no subprocess.

Each vendor directory's `NOTICE.md` records provenance, license, and the
concrete bugs found while integrating it (Windows encoding crashes, an
upstream PyPI/GitHub version-skew bug, a class-level-state crash on a
second call in the same process, catastrophic GIL contention under thread
concurrency). Read the relevant `NOTICE.md` before touching either vendor
tree or adding a third — don't edit vendored files in place; adaptations
belong in Bridge's own adapter/wrapper code.

A second upstream repo is vendored the in-process way: **indic-qa's checker
core** (`engine/vendor/indic-qa/`, from `selvabenz/indic-qa`, maintainer's
decision 2026-10-07). It runs Language QA for Punjabi, Malayalam, Hindi and
Odia. Tamil stays on Bridge's own `ta-irv` pack. Its dictionaries are pack data
in `engine/language_packs/<code>-irv/dictionary/`, outside the onefile exe.
Rules for this tree:

- Files enter only through `scripts/sync_indic_qa.py`, which reads committed
  blobs and records sha256 in `VENDORED.json`. `.gitattributes` keeps both
  trees byte-exact.
- `qa_app.usfm_doc` imports `scripts/build_dictionary.py` from the vendor
  tree through `sys.path`. Bridge's own `scripts/` is on the pytest path, so
  never add a Bridge `scripts/build_dictionary.py`.
- Bridge builds the checker's `Line`/`Book` objects from chapter JSON. It never
  calls indic-qa's USFM parser (gotcha 14).
- The dictionaries are never written.

### On-disk project shape

A raw Scripture import becomes a translationCore-compatible book project:

```
<project>/manifest.json
<project>/<book>.usfm                          original source, preserved verbatim
<project>/<book>/<chapter>.json                 verse-keyed target Scripture
<project>/<book>/<chapter>.headings.json        section headings (\s, \ms, \r ...), keyed by the
                                                verse each INTRODUCES, not the one it trailed (#180).
                                                Written only when a chapter has any. Kept out of the
                                                verse map because every reader of that file treats
                                                each key as a verse number, and out of the verse TEXT
                                                because a heading is not a translation of any source
                                                word — its words must never become alignable targets.
                                                \d is deliberately NOT split out: a Psalm
                                                superscription is translated content.
<project>/.apps/translationCore/alignmentData/<book>/<chapter>.json
<project>/.apps/translationCore/index/{translationNotes,translationWords}/<book>/
<project>/.bridge/import.json                   SHA-256 provenance + per-tool capability status
<project>/.apps/translationCoreAI/bridge-workbench.sqlite3
                                                schema v5. Every Bridge-private store lives here:
                                                the human-owned ones (#76), the derived ones
                                                (#77) -- progress rollup, check-finding snapshots,
                                                check cache, triage verdicts, metrics, backups index --
                                                the cross-verse alignment links (#117), which
                                                tC alignmentData cannot hold because its groups are
                                                verse-local, and the persisted Language QA scan
                                                (`language_qa_cache`, #169).
<project>/.apps/translationCoreAI/backups/      the backup files themselves, indexed by the DB
%LOCALAPPDATA%\Bridge\data\workspace.sqlite3    app-level, schema v2 (#77): users, devices, the
                                                project registry, non-secret settings, and one cached
                                                progress rollup per project for the dashboard
%LOCALAPPDATA%\Bridge\data\settings.json       DPAPI-wrapped secrets only
```

**The file stores have moved (#76 and #77, 2026-09-15).** `decisions/`,
`qaDecisions/`, `review/`, `terminology/`, `aiReview/`, `issueResolutions/`,
`alignmentHistory/`, `alignmentDiagnostics/`, `semanticMappings/`,
`semanticValidation/` (#76) and `checkFindings/`, `triage/`, `metrics/`,
`checkCache.json`, `.bridge/progress.json` (#77) are rows in
`bridge-workbench.sqlite3`, not JSON files. There is **no migration**: the
maintainer confirmed there is no user data to preserve while Bridge is
pre-release, so a project holding any of them refuses to open and is
re-imported. Nothing is deleted from anyone's disk. The names are
`_PRE_CUTOVER_STORE_DIRS` and `_PRE_CUTOVER_STORE_FILES` in `tc_project.py` — a
store that moves must be added there in the same commit, or an old project opens
and silently ignores its own records. See `docs/TEAM_ARCHITECTURE.md` §3.5 for
why the lazy-migration machinery was built and then removed.

**The dashboard reads a cache, not the siblings.** `project.listBookProgress`
walks every book in a collection, most of them lazy stubs that have never been
opened, and the rollup is per project. So it reads `project_progress_cache` in
`workspace.sqlite3` in one query; a materialized sibling with no entry is peeked
read-only (`peek_progress_totals`, a `mode=ro` connection that never creates or
migrates) and its entry written back. Every totals write refreshes the entry
with the `change_log.seq` it came from, and `project.open` compares that seq,
the project id and the book (`sync_progress_cache`) and repairs, clears or
keeps the entry — reported on the open result as `progressCache`, never fatal.
The correctness question for that cache is invalidation, not placement.

`audit/` is the exception and is **not** in that list. It was a write-only shadow
of records already held natively under `.apps/translationCore/checkData/`, and
Bridge no longer writes it; the one field that existed nowhere else — a check
selection's `provenance`/`metadata` — is now a `change_log` event. Projects on
disk still carry the old tails, and their presence says nothing about which build
made the project, so they must not be treated as pre-cutover.

`TranslationCoreProject` (`tc_ai_bridge/tc_project.py`) is the reader/writer
for this; `project_import.py` is the normalizer. translationNotes/Words are
never fabricated for a raw import — they're `requires-resource-index` until
a real background materialization pass runs (`resource_materializer.py`),
matching real translationCore's own boundary between "imported Scripture"
and "materialized checking tool indexes."

### Multi-book collections

Upstream translationCore rejects multi-book projects; Bridge imports a
folder/Paratext project with several books as one project **per book**,
linked via `.bridge/collection.json` on every sibling. Only the first book
is normalized eagerly; the rest carry `.bridge/lazy-import.json` and
normalize on first open (this is why a 66-book import is ~5s, not minutes).

## Hard invariants

These are the things that cost real people real work if they break. Every one of
them is enforced somewhere in the code or the tests; none of them is aspirational.

**The correction ledger is append-only.** Proposals are never updated in place and
never deleted. An edit, a rejection or a supersession writes a new row and the
previous wording survives as a snapshot
(`passage_semantic_repository.py`'s proposal history, added in Stage 9B.1).
Crash recovery, the audit trail, and the ability to explain to a translation team
why a verse changed all depend on this. The same rule holds for the tN/tW
disposition history in `tc_project.py` — compacting a record must not compact its
lifecycle events.

**Token lineage is identity.** Tokens carry a `lineage_id` and an
`instance_fingerprint` (`token_lineages` / `token_instances`, unique on the pair).
Code that re-tokenises, re-imports, or rebuilds an inventory must resolve lineage
through `token_lineage_candidates` — `SAME_LINEAGE` / `POSSIBLE_SUCCESSOR` /
`SPLIT_FROM` / `MERGED_FROM` / `NO_CORRESPONDENCE` — not mint fresh ids. A broken
lineage silently detaches a team's review history from the text it was about.
This is the same class of rule as gotcha 3 below: identity has to survive a
re-run, or decisions keyed to it are lost.

**Staleness propagates through the dependency graph.** `record_dependencies`
plus `pending_invalidations` are what mark downstream analysis `STALE` rather
than silently keeping it. If you add a derived record type, register it — there
are two real tests asserting it, both in `engine/tests/correction/test_correction_stage9b0.py`:
`test_every_writable_dependency_type_is_registered` checks that every writable
record type appears in the authoritative `RECORD_DEPENDENCY_TABLES` map at the top
of `passage_semantic_repository.py`, and
`test_dependency_tables_all_exist_and_are_stale_propagatable` checks each mapped
table can actually carry a `STALE` lifecycle. (The comment at
`passage_semantic_repository.py:63` names a `test_dependency_graph_invariants` that
does not exist under that name — trust the test files, not the comment.) A derived value that never goes stale is worse
than no derived value.

**Latest job authoritative, history retained.** `lifecycle_status` carries
`SUPERSEDED` precisely so a superseded analysis attempt can stay on disk. Do not
prune superseded rows to save space.

**Schema changes are migrations.** The companion SQLite database is at
**schema v16** (`DATABASE_SCHEMA_VERSION` in
`engine/tc_ai_bridge/passage_semantic_repository.py`). There is no `migrations/`
directory and no `.sql` files — the schema and every `_MIGRATION_V1` … `_V16`
block live in that one module, applied in order. Any change needs: a version
bump, a new forward migration block, and a note in `docs/BUILD_LOG.md`. The
repository refuses to open a database newer than it understands, and never
downgrades. Never edit the schema in place.

**Pre-release amendment (2026-09-14, while Bridge has no users).** The
maintainer confirmed there is no user data to preserve on any machine: every
project on disk is a development import and fresh imports are cheap. Until first
release, two parts of the rule above are relaxed:

- **A data-preservation migration test is no longer required** for every bump —
  the `test_vN_to_vN+1_…_keeps_vN_data_readable` style. Write one when the
  migration does something a reader should not have to take on trust (v16 has
  one because it backfills an ordering column); skip it for a plain additive
  change.
- **A schema bump is no longer a "stop and ask"** and has been removed from that
  list below.

What does *not* relax: the version bump and forward migration block still happen
(a fresh database is built by running the ladder, so editing an earlier block is
still wrong — v13 and v15 rebuild tables and would silently drop a column added
above them), and the append-only invariants are unchanged. **When Bridge has its
first real user, restore both requirements and delete this amendment** — for a
translation team that database *is* months of work, and no reset is available.

This same discipline now has two more, independent ladders. `bridge-workbench.sqlite3`
(`WorkbenchRepository`, `engine/tc_ai_bridge/workbench_repository.py`,
`WORKBENCH_SCHEMA_VERSION`, currently v5 — v2 added `change_log.columns_json` for
sync and rebuilt the immutability trigger; v3 added `alignment_cross_verse_links`,
#117; v4 added `language_qa_cache`, #169; v5 rebuilt `human_decisions` to admit the `housestyle` kind, #169) and `workspace.sqlite3`
(`WorkspaceRepository`, `engine/tc_ai_bridge/workspace_repository.py`,
`WORKSPACE_SCHEMA_VERSION`, currently v2 — v1 is exactly the unversioned
devices/users the first cut created, kept as `IF NOT EXISTS` so an existing
database is adopted with its ids intact; v2 added `projects`, `settings_kv`,
`project_progress_cache`). Three databases, three version numbers; a bump in one
is never a bump in another, and each gets its own forward block. The pre-release
amendment above applies to all three.

**Offline operation is a product invariant, not a preference.** Do not introduce a
runtime dependency on a network service, a hosted API, or a login, in any code path
a translator hits during normal work. The one bundled network-shaped thing —
`ai_client.py` — is explicitly optional, human-invoked, and never on the path that
imports, checks, or opens a project. If a feature seems to need a network call, say
so and stop; that is an architecture decision, not an implementation detail.

## The analysis pipeline, and why stage numbers matter

The passage-aware semantic pipeline runs in numbered stages, followed by human
review and correction. Note the numbering collision flagged in
`DEVELOPER_GUIDE.md`: these semantic **Stages** are a different axis from the
Greek Room **Phases**, and the two are easy to confuse in older notes.

| Stage | Does | Lives in |
|---|---|---|
| 4 | Runtime integration | `passage_semantic_runtime.py` |
| 5 | Source semantic inventory, from UHB/UGNT | `source_semantic_inventory.py` |
| 6A | Target semantic inventory | `target_semantic_inventory.py` |
| 6B | Passage-aware source→target location | `semantic_location.py` |
| 7 | Meaning-preservation analysis | `meaning_analysis.py` |
| 8 | Bidirectional source-coverage / target-support QA | `qa_audit.py` |
| 9A | Human review UI: the QA findings queue | `qa_review` methods + Alignment Review |
| 9B.0–9B.4 | Correction proposal → review → authorized apply → affected re-analysis → positive verification → human `CORRECTED` acknowledgement | `correction_*` services |

Get the numbers right before quoting them: **6B is the location engine and 7 is
meaning preservation**, not 7 and 8. The test files are named for their stage
(`test_semantic_location_stage6b.py`, `test_meaning_analysis_stage7.py`,
`test_qa_audit_stage8.py`) and so are the goldens — use them as the source of
truth over any prose, including this file.

Each stage's module docstring states what it deliberately does *not* do, and those
refusals are load-bearing:

- **Stage 5 is source-only.** It never reads target Scripture.
- **Stage 6A is target-only, and is built independently of Stage 5 on purpose.**
  That independence is what makes the later comparison meaningful. Do not
  "optimise" Stage 6A by seeding it from Stage 5 output.
- **Stage 7 never relocates target expressions** — it analyses frozen Stage 6B
  locations.
- **Stage 8 never re-runs Stage 6B location search and never re-judges Stage 7.**
- **Stage 9B.4 never re-judges Stage 7 meaning.** Verification asks whether the
  original failed obligation is now positively satisfied by current Stage 6B/7/8
  evidence. A correction is never verified merely because a finding disappeared,
  and `PASSED` alone never sets `CORRECTED`.

## Goldens and thresholds

**The goldens are two files**, and they are not in a `goldens/` directory —
they sit beside the tests that read them:

```
engine/tests/fixtures/stage5-source-golden-v1.json    <- test_source_semantic_inventory_stage5.py
engine/tests/fixtures/stage6b-location-golden-v1.json <- test_semantic_location_stage6b.py
```

There are no Vitest snapshots and no `__snapshots__` directories anywhere in the
repo. `golden-notice` keys on the filename, so **a new golden must have `golden` in
its name** and live in that directory or CI will not flag a change to it.

**Never re-baseline a golden as part of another change.** If your change makes one
fail, that is a finding to report, not a file to regenerate. A re-baseline is its own
commit doing nothing else, with the reason in the message.
`.github/workflows/ci.yml`'s `golden-notice` job raises a warning naming the files
whenever one moves — it does not block, so it is a prompt to look, not a gate. Nothing
mechanical will stop you re-baselining by accident; this rule is the only thing that
does.

**The confidence thresholds are uncalibrated.** The 0.85 / 0.9 cut-offs in
`qa_audit.py`'s `severity_for()` (MEANING_SHIFT → HIGH, and HIGH → CRITICAL) are
placeholders, as is every confidence value elsewhere in the pipeline —
`MEANING_CALIBRATION_VERSION` is literally `"meaning-uncalibrated-v1"`, and raw
score and calibrated value are deliberately kept as separate fields so a real
calibration can land later. Do not build behaviour that assumes these numbers are
meaningful, and do not tune them to make a test pass.

**Known-pinned bug: Tamil negation.** `meaning_analysis._comparison_norm` does
`re.findall(r"[^\W_]+")` over NFD-decomposed text. Indic combining marks are not
alphanumeric, so a Tamil word is **split at every virama and vowel sign** and the
marks are discarded: `இல்லை` becomes two tokens. Stage 7's POLARITY branch tests
whole tokens, so it cannot see the negative and returns `CONTRADICTED` against a
Greek negative — a false contradiction, on this project's primary target language.
(`_category` survives because it substring-matches, so QUANTITY, TEMPORAL and
PARTICIPANT still work. The docstring's claim that Tamil vowel signs "remain
intact" is wrong.) This is pinned deliberately by
`test_tamil_negation_polarity_limit_is_pinned_not_worked_around`, which asserts
both the comparator's current output and that Stage 9B.4 verification reports the
resulting disagreement as `UNCERTAIN` rather than papering over it. When Stage 7
is fixed that test fails on purpose. Do not work around it locally in an unrelated
change. **This is scheduled work, not accepted behaviour.**

## Non-obvious gotchas (confirmed still true in current code, not assumed)

1. `TranslationCoreProject.summary` is a `@property` — `summary()` crashes.
2. `TranslationCoreProject.__init__` creates its own `self.journal`; never
   create a second one.
3. Finding ids must be **stable** (`_stable_finding_id()` in
   `bridge_service.py`, a sha1 of `chapter:verse:engine:check_type:disambiguator`),
   not `uuid4()` — decisions are keyed by finding id and must survive
   re-running checks.
4. `verse.runChecks` re-applies prior decisions from
   `qa_decisions_for_verse()` after running checks — keep this inline in
   the check flow, don't split it into a separate call.
5. Windows stdout must stay UTF-8
   (`sys.stdout.reconfigure(encoding="utf-8")` in `stdio_transport.py`) —
   removing it makes the sidecar crash silently on any non-Latin verse
   text and the Rust side just sees a timeout. Same failure mode shows up
   in file I/O throughout the vendored tools (default Windows `cp1252`
   choking on Tamil/Odia/Hebrew) — explicit `encoding="utf-8"`/`"utf-8-sig"`
   everywhere text touches disk, not just at the stdio boundary.
6. `plugins.shell.sidecar` must **not** appear in `tauri.conf.json` — not a
   valid field, causes a startup panic. Sidecar exec permission comes
   entirely from `src-tauri/capabilities/default.json`'s
   `shell:allow-execute` entry.
7. Store keys in `stores.ts` are composite `"chapter:verse"` — use
   `verseKey()`, not a bare verse number (silently collides data across
   chapters). The same class of bug exists at the book level when
   switching books; `resetBookState()` clears chapter/verse-keyed stores
   on `switchBook()` for exactly this reason.
8. The sidecar binary name must match the Rust target triple exactly
   (`bridge-engine-x86_64-pc-windows-msvc.exe` — get the triple from
   `rustc -vV`).
9. Avoid icon-font classes for icon-only controls with no text fallback —
   an offline/PyInstaller build can't reach a CDN icon font and they
   render as empty boxes; use Unicode glyphs or pair with a label.
10. `icons/icon.ico` missing → `cargo tauri dev` fails; placeholders are
    committed under `src-tauri/icons/`, referenced from `tauri.conf.json`'s
    `bundle.icon`.
11. `cargo metadata ... program not found` even though `cargo --version`
    works elsewhere usually means the current shell predates Rust's PATH
    changes — open a fresh terminal, don't fight it.
12. USFM verse bridges (`3-4`) and lettered segments (`3a`) are real,
    already-seen input — `_qaissue_to_finding`/finding conversion uses the
    first numeric component as the anchor while project navigation keeps
    the exact string; several vendored/wrapper functions pass these
    through as an identity fallback rather than crashing. Don't assume
    every `verse` parameter is a bare integer string.
13. A **running `bridge-engine.exe` blocks the next Rust build**:
    `tauri-build` copies each `externalBin` into `target/`, and Windows
    refuses to overwrite a running executable, so it panics with a bare
    `PermissionDenied: Access is denied.` that names nothing. `RunEvent::Exit`
    now stops the sidecar by closing its stdin (`run_stdio_loop` ends on EOF),
    with a `taskkill /T` backstop — a *tree* kill, because terminating the
    PyInstaller bootloader alone leaves the real Python child holding the
    file. A sidecar busy inside a long request when the app dies abruptly can
    still orphan; `Stop-Process -Name bridge-engine -Force` clears it.
14. **USFM is read by exactly two modules, and nothing else may regex-parse it**
    (#91, complete 2026-10-01). `engine/tc_ai_bridge/usfm_parser.py` reads a
    *book*: usfmtc 0.4.8 pinned exactly, the only module that may import it,
    behind Bridge's own walker. It decides where each verse starts and ends,
    which paragraphs are headings, and exposes `structure` (chapter/verse/
    paragraph events in order) and `headers`; the stored verse string is cut
    **verbatim** from the source at its element positions, so offsets, `\zaln`/
    `\w` markup and notes survive byte-for-byte, and export writes current text
    back into those same spans. `engine/tc_ai_bridge/usfm_verse.py` reads a
    *verse string*: `lift_verse` gives plain text, notes, style spans, the
    removed ranges and a raw→plain offset map, by deletion only and never
    refusing (warnings instead). It is Bridge's own scanner, not usfmtc (usfmtc
    has no text-node offsets and this runs per verse on hot paths), bound to
    usfmtc by a parity test over every stored verse of both IRV fixtures. On
    them: import, export, `strip_usfm`/`whitespace_tokens` (so every tC
    `occurrence` tokenises one text), the aligned exporter, the names check's
    offsets, Language QA, the passage index and the Stage 4 overlay, and the
    frontend, which receives `display` on every verse read and never parses.
    usfmtc quirks the parser hides: a string argument that `os.path.exists`
    accepts is opened as a file (always pass a StringIO); its lexer is
    quadratic, so books are parsed a chapter at a time and the import preview
    parses only the first book; it opens an implicit `\p` after a `\c` with no
    paragraph marker, reported at the chapter's position, which the parser
    drops. A new USFM edge case is fixed in one of these two modules, with a
    parity or snapshot test, never with a regex elsewhere.

## Working in this repo

**Never trust a doc's or an upstream dependency's description of what it
does — install/vendor it, run it against real input, and read what actually
happens before writing an adapter around it.** Every external integration
attempted so far (Wildebeest, the USFM checker, versification) turned out
to have a real, non-obvious problem invisible from reading the docs alone:
a wrong PyPI package name, a Python-version compile break, an unpublished
dependency, a Windows-only crash, upstream's own GitHub/PyPI releases
drifting apart, a class-level-state bug that only appears on a second call,
catastrophic slowdown only visible under real thread concurrency. This
applies to this file and to `docs/*.md` too — verify a claim against the
current code before relying on it for follow-up work.

Read `docs/DEVELOPER_GUIDE.md` first — it's the oriented summary of stack
decisions and the phase roadmap (planned vs. actual outcome per phase), kept
current. Don't assume the next task is just the next numbered phase; the
guide's roadmap table shows what's actually done.

`docs/BUILD_LOG.md` (formerly `DEVELOPER_HANDOFF.md`) is the authoritative,
continuously-updated detailed record underneath that summary — the full
investigation behind every decision and gotcha (exact root causes, file:line
references, session-by-session narrative). Read it when the guide's summary
isn't enough, and **keep appending to it** as work progresses — it's the
record `DEVELOPER_GUIDE.md` gets distilled from, not a doc to let go stale.

`docs/ARCHITECTURE.md` is the current-state map (process boundary, why the stack
is what it is, the three databases, vendored and bundled data, pipelines, UI
surfaces, subsystems) — and its **§9 is the repository's one doc map**: every
other doc is listed there with what it covers, so go there rather than guessing
from filenames. `docs/INVARIANTS.md` holds the rules the pipeline may not break,
still numbered as they were in the old handoff because engine code cites them by
number (§20 token identity, §21 the Unicode span contract, §39 the hard
constraints). `docs/QA_TEST_MATRIX.md` is the release gate — a feature isn't
release-ready because its unit tests pass; check the matrix's source/frozen/desktop
rows.

`docs/TEAM_ARCHITECTURE.md` (2026-09-11) is the design record for the direction
behind issues #44–#47: a second per-project `bridge-workbench.sqlite3` for the
Bridge-private stores (the semantic DB is not extended), a named user + device
on every write, an *optional* team hub that syncs review state, and a
hub-served dashboard. Its §3 and §4 (the two databases, #75–#77) are what the
code does as of 2026-09-15; §5–§8 (identity and roles, engine session model,
hub, dashboard) are still design. When one of those lands, this file's on-disk
shape and invariants sections must be updated in the same commit.

## How to work here

`CONTRIBUTING.md` has the full flow. The short version, and the parts that apply
to an AI-assisted session specifically:

1. **Non-trivial work should have an issue**, so the reason survives the commit.
   File it with the Idea template; there is no acceptance step to wait for.
2. **Small and single-purpose.** One issue per PR. A PR that touches the engine,
   the schema and the UI at once cannot be reviewed properly by one person.
3. **Work goes directly onto `main`.** There is no branch protection and no
   required review, so CI on `main` is the only automated gate — treat a red run
   as a real failure, not a formality. Branch when you want a second opinion
   before something lands, not as a matter of routine.
4. **Say what you verified.** In the PR description, separate what you ran and
   watched work from what you believe to be true because the code looks right.
   This matters more here than anywhere else: a confident explanation is not
   evidence. "I built the installer, imported a project and the findings
   appeared" is evidence. "The tests pass" is evidence. "This should now handle
   the edge case" is not.
5. **Report surprises, don't absorb them.** If you find a bug adjacent to your
   task, file it. Don't fix it quietly in the same PR.
6. **Verification gates.** Frontend: `npm run check` + `npm run test` + `npm run
   build`. Engine: the pytest suite. Shell: `cargo check` *and* `cargo test` —
   both, not just the compile. `.github/workflows/ci.yml` runs the first three on
   every PR; `docs/QA_TEST_MATRIX.md` is the release gate beyond that, and a
   feature is not release-ready just because its unit tests pass.
7. **Don't trust the frozen build because source passed.** The two have diverged
   before. `scripts/smoke_sidecars.py` checks the frozen pair, and since #130
   (2026-09-29) it is a **hard gate** in `release.yml` again — it had been
   `continue-on-error` since 2026-09-07 over a `project.inspectImport`
   self-duplicate mismatch, which is fixed. A green release build now does mean
   the frozen sidecars were exercised. Run it locally after any change to the
   engine, the spec files or the vendored trees; a source-only pass still proves
   nothing about the frozen pair.

## Stop and ask before writing any code

These are cheap to start and expensive to undo. If a task appears to require one,
raise it as a question in the issue rather than deciding it in a commit:

- Anything that adds a server, an account, a login, or a network round-trip on a
  runtime path a translator hits
- Re-baselining either golden
- Changing the confidence thresholds or the auto-apply behaviour. Still on this
  list after #146: that decision was taken once, for cross-verse links only, on
  an agreement-between-two-methods gate. Widening it — to Scripture, to tC
  alignment data, to findings, or to any single-score threshold — is a fresh
  question, not a precedent already set
- A second Scripture writer, or an alternative path for applying corrections to
  the text — Stage 9B.3b's authorized write behind an explicit human confirmation
  is deliberately the only one
- Bundling or switching the multilingual embedding model
  (`SemanticEmbeddingProvider.available` is `False` in the shipped app today, so
  production location runs use lexical/structural evidence only — that is a known
  state, not a bug to fix in passing)
- Adding a third vendored upstream tree under `engine/vendor/`

---

*Maintainer: @RevantCI. When in doubt, the answer is a question in the issue, not
a commit.*
