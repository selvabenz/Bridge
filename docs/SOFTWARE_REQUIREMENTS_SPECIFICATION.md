# Bridge Software Requirements Specification

| Field | Value |
|---|---|
| Document ID | `BRIDGE-SRS-001` |
| Revision | 1.0 |
| Status | Baseline draft for maintainer review |
| Date | 2026-09-21 |
| Product baseline | Bridge 0.14.0 |
| Source baseline | `main` at `c8fa74d` |
| Primary platform | Windows x86-64 |
| Product owner / maintainer | @RevantCI |
| Review tracker | [GitHub issue #168](https://github.com/RevantCI/bridge/issues/168) |

## Approval record

- [ ] Product scope reviewed by the maintainer.
- [ ] Functional requirements reviewed against the installed application.
- [ ] Data, safety, and offline invariants reviewed against current code.
- [ ] Non-functional requirements have measurable acceptance evidence.
- [ ] Approved as the requirements baseline for the next release.

## 1. Purpose

This Software Requirements Specification (SRS) defines the required behavior
and quality attributes of Bridge, a local-first desktop workbench for Bible
translation quality assurance. It covers the installed application, its
Python engine, project data, bundled resources, optional AI assistance,
external desktop integrations, and release acceptance boundary.

This is an as-built baseline. Requirements without a `FUTURE` marker describe
the current product and are normative for regression testing. Proposed team
hub, login, web dashboard, and synchronization capabilities are recorded only
as future requirements and must not be interpreted as current behavior.

## 2. Scope

Bridge shall support the complete local workflow:

```text
import → inspect → open → check → review → align → correct → verify → report → export
```

Bridge combines:

- offline Greek Room and Bridge-native checks;
- translationNotes and translationWords review;
- word-level alignment to bundled Hebrew and Greek source data;
- passage-aware source-to-target semantic analysis;
- human review and append-only decision history;
- explicitly authorized correction application and verification;
- project-wide reporting and export; and
- optional, human-invoked AI and desktop-application integrations.

## 3. Intended audience

| Audience | Use of this document |
|---|---|
| Translators | Understand which workflows and safeguards Bridge must provide. |
| Reviewers and team leads | Understand review, evidence, reporting, and audit requirements. |
| Maintainers | Make scope and architecture decisions without weakening invariants. |
| Developers | Implement changes with traceable functional and data requirements. |
| QA testers | Derive manual and automated acceptance coverage. |
| Release owners | Decide whether a candidate build is safe to ship. |

## 4. Definitions and conventions

| Term | Meaning |
|---|---|
| Bridge project | One translationCore-compatible book project managed by Bridge. |
| Collection | Sibling book projects imported from one multi-book source and linked by collection metadata. |
| Finding | A normalized `QaFinding` produced by a local checker or related QA source. |
| Native selection | A translationCore tN/tW target-text selection stored in the project. |
| Alignment | A source-token to target-token relationship for one verse. |
| Cross-verse link | A Bridge-private relationship showing that a source word is realized in a neighboring target verse. |
| Semantic pipeline | Deterministic Stages 5–8 for source inventory, target inventory, location, meaning, and QA. |
| Stage 9A | Human QA finding review and disposition workflow. |
| Stage 9B | Correction proposal, review, apply, re-analysis, verification, and acknowledgement workflow. |
| Authoritative | The latest current record used by the product while older records remain available for audit. |
| P0 | Failure risks Scripture, audit history, data integrity, or basic application use. |
| P1 | Failure blocks or materially degrades a primary workflow. |
| P2 | Quality, usability, or edge-case failure without immediate data risk. |

The words **shall** and **must** identify mandatory requirements. **Should**
identifies a recommended behavior. Requirement IDs are stable and shall not be
renumbered merely to improve presentation.

## 5. Referenced documents

| Document | Authority |
|---|---|
| `ARCHITECTURE.md` | Current process, storage, subsystem, and UI architecture. |
| `INVARIANTS.md` | Detailed semantic, lineage, Unicode, and continuity invariants. |
| `IMPORTS.md` | Import formats, normalization, duplicate safety, and provenance. |
| `ALIGNMENT.md` | Verse-local and cross-verse alignment behavior. |
| `DEVELOPER_GUIDE.md` | Implemented roadmap and known constraints. |
| `USER_MANUAL.md` | User-facing workflow and terminology. |
| `QA_TEST_MATRIX.md` | Release evidence and current automated/manual status. |
| `FULL_APP_MANUAL_QA_CHECKLIST.md` | Executable full-app manual test procedure. |
| `TEAM_ARCHITECTURE.md` | Shipped database design plus explicitly future team features. |
| `BUILD_LOG.md` | Detailed implementation and investigation record. |

When prose conflicts with current code, schema constants, tests, and observable
installed behavior take precedence until the documentation is corrected. At
this baseline the verified schema constants are semantic v16, workbench v3,
and workspace v2.

## 6. Product overview

### 6.1 Product perspective

Bridge is a Windows desktop application built from:

- a Tauri 2 / Rust desktop shell;
- a Svelte 4 / TypeScript frontend;
- one long-lived PyInstaller-packaged Python engine sidecar;
- one separately packaged USFM-checker helper;
- JSON-lines request/response communication over standard input/output;
- local SQLite databases and translationCore-compatible project files; and
- bundled, versioned original-language and translation-help resources.

### 6.2 Responsibility boundary

- Local QA engines identify objectively or statistically suspicious text.
- Optional AI provides bounded explanations, proposals, or triage evidence.
- A human reviewer decides what the translation should be.
- Bridge owns workflow state, persistence, review history, and authorized
  changes.

### 6.3 User classes

| User class | Primary needs |
|---|---|
| Translator | Import, inspect, edit, align, review, and export Scripture safely. |
| Checker / reviewer | Evaluate findings and evidence, record dispositions, and verify corrections. |
| Team lead | Monitor progress, generate reports, and identify unresolved risk. |
| Maintainer | Configure resources and integrations, diagnose failures, and produce releases. |
| Developer / QA | Exercise source, frozen-sidecar, and installed-app behavior. |

### 6.4 Operating environment

- Windows x86-64 is the only verified production target.
- The installed application shall use the system WebView2 runtime.
- The packaged Python runtime and both sidecars shall not require a user-managed
  Python installation.
- Normal translator workflows shall operate without internet connectivity.
- macOS and Linux are future, unverified targets.

### 6.5 Constraints

- Bridge shall remain compatible with translationCore project structures used
  by supported imports and exports.
- The application shall not require an account, server, or login for normal
  work.
- No second Scripture-writing route may be introduced without an explicit
  architecture decision.
- Schema changes shall use forward migrations and preserve prior data.
- Bundled and vendored resources shall retain their provenance and licenses.

## 7. Functional requirements

### 7.1 Application lifecycle and desktop shell

- `FR-LIFE-001 P0` The installer shall install a launchable Windows desktop
  application and the two required packaged workers.
- `FR-LIFE-002 P0` Bridge shall start one long-lived engine sidecar for the
  desktop session and exchange one JSON request or response per line.
- `FR-LIFE-003 P1` The shell shall correlate responses with request IDs and
  apply method-specific timeouts appropriate to interactive and long-running
  work.
- `FR-LIFE-004 P1` Long-running check, analysis, report, and AI operations shall
  use background jobs whose status can be polled without freezing navigation.
- `FR-LIFE-005 P0` Normal application exit shall close the engine input, stop the
  worker process tree, and avoid leaving a locked executable.
- `FR-LIFE-006 P0` If the engine restarts, Bridge shall report the restart and
  reconnect the open project when possible.
- `FR-LIFE-007 P1` Engine or helper unavailability shall produce an actionable
  error or limited-capability state rather than a silent timeout or false-clean
  result.
- `FR-LIFE-008 P1` The application shall expose recent engine diagnostics while
  keeping diagnostics non-blocking.

### 7.2 Project Home and project registry

- `FR-PROJ-001 P1` Project Home shall list managed and recent external projects.
- `FR-PROJ-002 P1` A user shall be able to open a registered project without
  browsing for its folder again.
- `FR-PROJ-003 P1` Missing project folders shall remain visible with Locate and
  Forget actions.
- `FR-PROJ-004 P0` Forget shall remove only the registry entry and shall not
  delete project files.
- `FR-PROJ-005 P1` Locate shall update the stored path while preserving the
  stable project identity.
- `FR-PROJ-006 P1` A corrupt registry shall be quarantined and recoverable
  managed projects shall be rediscovered.
- `FR-PROJ-007 P1` Project discovery shall remain bounded for large libraries and
  shall not repeatedly hash unchanged projects without need.
- `FR-PROJ-008 P1` The dashboard shall show collection books, progress, and
  current review/check state.
- `FR-PROJ-009 P0` Switching projects or books shall clear or re-key transient
  chapter and verse state so data cannot leak across books.
- `FR-PROJ-010 P1` Multi-book collection metadata shall remain portable when a
  collection parent is moved.

### 7.3 Import and normalization

- `FR-IMP-001 P0` Bridge shall inspect and import `.usfm`, valid `.sfm`/`.txt`,
  translationCore directories, Paratext-style directories, `.tcore`,
  `.tstudio`, and supported `.zip` archives.
- `FR-IMP-002 P1` File-picker and native file/folder-drop entry points shall use
  the same inspection, duplicate review, and import behavior.
- `FR-IMP-003 P1` Import preview shall show detected books, language, project,
  Bible, destination, warnings, and capability status before writing.
- `FR-IMP-004 P1` Unknown metadata shall require explicit, searchable language,
  project, and Bible values.
- `FR-IMP-005 P0` A raw Scripture import shall create one translationCore-
  compatible project per book and preserve the original source verbatim.
- `FR-IMP-006 P1` Multi-book imports shall link sibling projects as a collection
  and may normalize non-initial books lazily.
- `FR-IMP-007 P0` Existing translationCore alignments, selections, check state,
  and compatible metadata shall be preserved.
- `FR-IMP-008 P0` Archives shall be extracted safely and shall reject path
  traversal outside the destination.
- `FR-IMP-009 P0` Malformed markers, duplicate verses, and fatal structural
  errors shall be reported explicitly and shall not be represented as clean.
- `FR-IMP-010 P0` UTF-8, UTF-8 BOM, non-Latin text, combining marks, verse
  bridges, and lettered segments shall be preserved.
- `FR-IMP-011 P0` Exact duplicate imports shall prefer the existing project and
  shall require explicit intent before creating a separate copy.
- `FR-IMP-012 P0` Possible overlaps shall show content/metadata reasons and book
  coverage and shall not merge or overwrite automatically.
- `FR-IMP-013 P1` Cancelling inspection or preview shall not leave an incomplete
  project or registry entry.
- `FR-IMP-014 P1` Raw import shall use bundled source and translation-help
  resources where applicable without fabricating unavailable tN/tW indexes.
- `FR-IMP-015 P0` Existing source tokens shall not be silently replaced when the
  recorded original-language resource version conflicts with the bundled one.

### 7.4 Scripture workspace and navigation

- `FR-EDIT-001 P1` Users shall navigate by collection book, chapter, verse,
  previous/next chapter, breadcrumb, and supported reference jump.
- `FR-EDIT-002 P1` The selected reference, verse row, review panel, and enabled
  external-navigation reference shall remain synchronized.
- `FR-EDIT-003 P1` Stale asynchronous chapter/book responses shall not overwrite
  a newer user selection.
- `FR-EDIT-004 P1` The editor shall render Scripture, headings, poetry,
  footnotes, notes, milestones, and supported custom markers intelligibly.
- `FR-EDIT-005 P1` Footnote and note popups shall expose complete content and
  keyboard-accessible close/navigation behavior.
- `FR-EDIT-006 P0` A user shall explicitly enter edit mode before changing verse
  text and shall be able to save or cancel.
- `FR-EDIT-007 P0` A saved edit shall preserve exact Unicode content and verse
  identity and shall survive restart.
- `FR-EDIT-008 P0` A stale or concurrent edit shall fail closed rather than
  overwrite newer Scripture.
- `FR-EDIT-009 P0` Editing Scripture shall invalidate or reconcile dependent
  alignments, selections, findings, semantic records, and cached analysis.
- `FR-EDIT-010 P1` External navigation shall not discard or bypass an active
  verse edit.
- `FR-EDIT-011 P1` Verse bridge and lettered-segment navigation shall preserve
  the exact displayed reference while numeric anchors may be used internally.

### 7.5 Local checks and finding decisions

- `FR-QA-001 P0` Bridge shall run available local check adapters and normalize
  their results into the common `QaFinding` shape.
- `FR-QA-002 P0` Finding IDs shall be stable across equivalent re-runs so prior
  decisions remain attached.
- `FR-QA-003 P1` Verse, chapter, and book check jobs shall expose progress,
  completion, cancellation, failure, and retry.
- `FR-QA-004 P1` Cancelling a job shall not present incomplete results as a full
  success; retry shall avoid duplicating completed work.
- `FR-QA-005 P1` Findings shall expose engine, category, severity, message,
  reference, offsets where available, and review status.
- `FR-QA-006 P1` Users shall be able to Accept or Ignore a local finding through
  the review panel and accessible context-menu paths.
- `FR-QA-007 P0` A finding correction shall change Scripture only through the
  existing explicit edit/apply path; a failed correction shall not be recorded
  as accepted.
- `FR-QA-008 P1` Decisions shall persist across navigation, checking, restart,
  verse bridges, and lettered references.
- `FR-QA-009 P1` Checkers that are unavailable shall disclose the limitation and
  shall not return false-clean success.
- `FR-QA-010 P1` Finding menus shall be keyboard accessible and remain within the
  visible viewport.

### 7.6 Translation Notes, Translation Words, and AI review

- `FR-HELP-001 P1` Bridge shall materialize and display real tN/tW/TWL/tA data
  when available and shall show Preparing while first-open work runs.
- `FR-HELP-002 P1` Each check shall show source quote, occurrence, current target
  selection, provenance, lifecycle state, and available evidence.
- `FR-HELP-003 P0` Manual target selections shall be exact substrings of current
  target Scripture with valid occurrence data.
- `FR-HELP-004 P1` Users shall be able to create multiple selections, mark
  Nothing to select, edit, and clear, with lifecycle history retained.
- `FR-HELP-005 P0` AI review shall never overwrite an imported or human-owned
  selection without the policy's explicit human action.
- `FR-HELP-006 P1` Basic mode may apply only policy-approved, grounded,
  unambiguous, verse-local selections.
- `FR-HELP-007 P1` Advanced mode shall show proposals and evidence without
  changing native selection state until the user applies a proposal.
- `FR-HELP-008 P1` Verse, chapter, and book AI review jobs shall expose progress,
  cancellation, retry, and reference-correct results.
- `FR-HELP-009 P1` Editing a reviewed verse shall mark the result stale and offer
  an explicit re-run.
- `FR-HELP-010 P0` Cross-verse, split, reordered, implicit, or insecurely located
  meanings shall not be misapplied as native verse-local selections.
- `FR-HELP-011 P1` With no configured provider, all manual/offline translation-
  help functionality shall remain usable.

### 7.7 Manual word alignment

- `FR-ALN-001 P1` Users shall open the same Align Words surface from the verse
  row, review panel, and applicable review workflows.
- `FR-ALN-002 P0` Bridge shall support 1:1, 1:many, many:1, and many:many groups
  without losing token identity.
- `FR-ALN-003 P0` Repeated words shall retain occurrence and total-occurrence
  identity through save, restart, and export.
- `FR-ALN-004 P1` Users shall be able to align, unalign, undo, and restore groups
  through clear drag/drop or selection behavior.
- `FR-ALN-005 P0` Conflicting operations shall not silently merge or overwrite
  protected/established groups.
- `FR-ALN-006 P0` Stale editor saves shall fail without overwriting concurrent
  alignment work.
- `FR-ALN-007 P1` Verse completion shall be blocked until source and target
  tokens are accounted for by valid groups.
- `FR-ALN-008 P1` Completion, invalidation, and history shall persist and shall
  contribute only valid human-approved data to corpus statistics.
- `FR-ALN-009 P1` Missing original-language data shall produce guidance and shall
  never cause invented or silently downloaded tokens.
- `FR-ALN-010 P1` Hebrew/Greek text, morphology, glosses, punctuation, and RTL
  presentation shall remain usable.
- `FR-ALN-011 P1` AI alignment proposals shall be optional, shall propose before
  writing, and shall preserve protected human work.
- `FR-ALN-012 P0` Aligned USFM export shall round-trip valid `zaln`/`w` groups and
  occurrence identities.

### 7.8 Cross-verse alignment

- `FR-XVA-001 P1` Bridge shall provide a multi-verse range view centered on the
  selected or multi-selected passage.
- `FR-XVA-002 P1` Source and target passage streams shall support locating and
  selecting the intended references and tokens.
- `FR-XVA-003 P0` A cross-verse link shall be stored in the Bridge-private
  workbench database and shall not fabricate a translationCore verse-local
  alignment.
- `FR-XVA-004 P0` Link/unlink operations shall preserve token identities,
  provenance, lifecycle state, and append-only change history.
- `FR-XVA-005 P1` Completed same-verse alignment and current cross-verse links
  shall be consumable as human-precedent evidence by Stage 6B.
- `FR-XVA-006 P0` Target or source changes affecting a link shall invalidate the
  link and downstream semantic evidence.
- `FR-XVA-007 P1` Offline corpus suggestions shall remain available without an AI
  provider.
- `FR-XVA-008 P1` AI suggestions shall show human-readable reasons and
  distinguish AI-only evidence from AI/corpus agreement.
- `FR-XVA-009 P0` Only suggestions passing the configured corroboration/agreement
  gate may auto-link; uncorroborated suggestions require explicit acceptance.
- `FR-XVA-010 P0` Proposal generation alone shall write no link, and a batch shall
  stop safely at a refused or conflicting write.
- `FR-XVA-011 P0` A null alignment decision (a word with no counterpart, for a
  named reason) shall be stored Bridge-private, never as an empty tC group, and
  the absence of a decision shall mean unaligned (#216).
- `FR-XVA-012 P0` Every token shall have at most one home: a tC group, a
  cross-verse link group or a null decision; a 1:N/N:1/N:M cross-verse
  realization shall be one group written and removed as a unit (#217).
- `FR-XVA-013 P0` Automatic window alignment shall write only what a
  source-first and a target-first pass both give, shall never write over a
  reviewer's alignment, and shall report a word neither pass placed as a
  possible omission or addition without aligning it (#219).
- `FR-XVA-014 P1` A possible omission or addition from automatic alignment shall
  appear as a verse check finding with a stable id, silenced by a reviewer's
  link or null decision (#220).

### 7.9 Passage-semantic analysis

- `FR-SEM-001 P0` Stage 5 shall build source semantic inventory from current,
  version-locked UHB/UGNT resources and shall not read target Scripture.
- `FR-SEM-002 P0` Stage 6A shall build target semantic inventory independently of
  Stage 5 expectations.
- `FR-SEM-003 P1` Stage 6B shall locate source meanings in passage-aware target
  windows using bounded lexical, structural, resource, and approved alignment
  evidence.
- `FR-SEM-004 P1` Exhausted search budgets shall result in explicit review-needed
  states rather than omission claims.
- `FR-SEM-005 P0` Stage 7 shall analyze frozen Stage 6B locations and shall not
  relocate target expressions.
- `FR-SEM-006 P0` Stage 8 shall audit coverage/support without re-running Stage 6B
  or re-judging Stage 7.
- `FR-SEM-007 P1` Analysis shall expose scope, progress, cancellation, failure,
  limited-retrieval status, and current/stale state.
- `FR-SEM-008 P0` Unicode comparison, offsets, spans, and fingerprints shall be
  canonical and grapheme-safe for supported world scripts.
- `FR-SEM-009 P0` Current text and resource fingerprints shall prevent reuse of
  stale semantic results.
- `FR-SEM-010 P0` Human alignment changes shall stale dependent Stage 6B/7/8
  records through the registered dependency graph.

### 7.10 Stage 9A review

- `FR-REV-001 P1` Alignment Review shall provide Word, Semantic, Passage, and QA
  modes with QA as the primary Stage 9A work surface.
- `FR-REV-002 P1` Users shall run passage, chapter, book, or range analysis and
  continue navigating while the job remains visible.
- `FR-REV-003 P1` The QA queue shall be scoped to the active canonical analysis
  range and support filtering, counts, pagination, and empty/error states.
- `FR-REV-004 P1` Finding detail shall expose source obligation, target location,
  meaning relationship, evidence, resources, confidence, and provenance.
- `FR-REV-005 P1` Cross-verse findings shall navigate to the mapped target
  reference without offering an unsafe native verse-local apply.
- `FR-REV-006 P0` Review dispositions, reviewer, timestamp, notes, and optimistic
  concurrency state shall persist.
- `FR-REV-007 P0` Review history shall be append-only and remain available after
  later decisions supersede earlier ones.
- `FR-REV-008 P1` Switching analysed scopes shall restore their independent queue
  and history.
- `FR-REV-009 P1` No-findings, running, failed, cancelled, and limited-evidence
  states shall be distinguishable.

### 7.11 Stage 9B correction

- `FR-COR-001 P0` Only an eligible, human-confirmed QA issue may enter the
  correction workflow.
- `FR-COR-002 P1` Correction review shall show current text, proposed text,
  affected meaning, target span, source evidence, resources, provenance, and
  history.
- `FR-COR-003 P1` Users may create manual wording or request optional provider
  wording, but neither action shall change Scripture.
- `FR-COR-004 P0` Editing, rejecting, regenerating, or superseding a proposal
  shall append a new event or row; earlier wording shall not be mutated or
  deleted.
- `FR-COR-005 P0` Application shall require explicit human confirmation of exact
  before/after text.
- `FR-COR-006 P0` Stage 9B application shall remain the only authorized
  correction path that writes Scripture.
- `FR-COR-007 P0` Application shall use expected revision/hash/text preconditions
  and fail closed when current Scripture differs from the reviewed text.
- `FR-COR-008 P0` Application shall be journaled, backed up, idempotent, and
  recoverable across interruption without duplicate writes.
- `FR-COR-009 P0` Applying a correction shall invalidate and re-run only affected
  downstream analysis while retaining prior history.
- `FR-COR-010 P0` A correction shall not be verified merely because a finding
  disappeared or a stage returned `PASSED`.
- `FR-COR-011 P0` Verification shall require current positive Stage 6B/7/8
  evidence for the original obligation and may return passed, failed, or
  uncertain.
- `FR-COR-012 P0` Only a passed verification followed by explicit human
  acknowledgement may mark the issue `CORRECTED`.

### 7.12 Project QA report and triage

- `FR-RPT-001 P1` Users shall explicitly generate a whole-collection QA report as
  a background job.
- `FR-RPT-002 P1` Report progress, cancellation, retry, success, failure, and
  empty states shall be visible.
- `FR-RPT-003 P1` Reports shall summarize book/check/review/alignment progress and
  present finding rows with category, reference, issue, proposal, disposition,
  fixed-by, result, and severity information.
- `FR-RPT-004 P1` Users shall filter by supported book, category, result,
  fixed-by, severity, and text criteria; counts, charts, rows, and pagination
  shall agree.
- `FR-RPT-005 P1` A report row shall navigate to the correct project book,
  chapter, verse, and relevant review context.
- `FR-RPT-006 P1` Missing or lazy collection members shall be disclosed and shall
  not silently disappear from collection reporting.
- `FR-RPT-007 P1` Filtered CSV/TSV export shall use reviewer-facing columns,
  UTF-8 BOM, and correct quoting for non-Latin text, commas, and newlines.
- `FR-RPT-008 P1` Print-to-PDF shall print report content rather than application
  chrome and shall include all filtered rows across pages.
- `FR-RPT-009 P1` Optional AI triage shall score already-persisted findings as an
  overlay and shall not rewrite findings, decisions, or report authority.
- `FR-RPT-010 P1` Without an API key or after provider failure, the untriaged
  report shall remain fully usable.
- `FR-RPT-011 P1` Triage thresholds and human overrides shall change visibility
  only and shall remain hash-bound to current findings.

### 7.13 Settings, resources, and desktop integrations

- `FR-SET-001 P1` Settings shall expose AI, quality/reviewer mode, connections,
  resources, and security/privacy panes.
- `FR-SET-002 P1` Non-secret settings shall persist locally and be restored after
  restart.
- `FR-SET-003 P0` API keys shall be stored using OS-protected secret storage and
  shall not be serialized in plaintext project/settings files or logs.
- `FR-SET-004 P1` Resource status shall show current UHB/UGNT identity, version,
  publisher, language, license, and mismatch warnings.
- `FR-SET-005 P1` Bundled fonts shall support Indian scripts, Hebrew, Greek, and
  offline rendering without a CDN.
- `FR-SET-006 P1` Paratext and Logos connections shall be optional and shall
  report unavailable states cleanly.
- `FR-SET-007 P1` When enabled and available, supported Scripture references may
  synchronize between Bridge and the external desktop application without
  feedback loops.
- `FR-SET-008 P0` Bridge shall reject external navigation or note handoff when
  the active external project does not match the confirmed project identity.
- `FR-SET-009 P1` Paratext issue-resolution handoff shall save the local audit and
  Notes copy first, queue offline delivery, retry idempotently, and avoid
  duplicate threads.
- `FR-SET-010 P1` Disabling an integration shall stop its polling/publishing
  behavior.

### 7.14 Export and interoperability

- `FR-EXP-001 P0` Export availability shall reflect the current project's real
  readiness and shall provide an actionable disabled reason.
- `FR-EXP-002 P0` Bridge shall export valid, re-importable aligned USFM 3.
- `FR-EXP-003 P0` Bridge shall export valid, re-importable non-aligned USFM.
- `FR-EXP-004 P0` Export shall preserve supported headings, poetry, footnotes,
  milestones, ESFM/custom markers, verse bridges, and lettered segments without
  silent structural loss.
- `FR-EXP-005 P0` Source-template export shall preserve untouched source
  material; fallback behavior shall be disclosed when no source template is
  available.
- `FR-EXP-006 P1` Unicode content and directionality shall survive export,
  external inspection, and re-import.
- `FR-EXP-007 P1` Cancelling a save or encountering an unwritable destination
  shall not report false success or leave a corrupt completed file.
- `FR-EXP-008 P1` Scripture export and report export shall keep their file types
  and destination behavior distinct.

## 8. Data requirements

### 8.1 Project and application storage

- `DR-001 P0` One imported book shall remain a translationCore-compatible folder
  containing its manifest, preserved source USFM, verse JSON, alignment data,
  and tool indexes where available.
- `DR-002 P0` Bridge-private project data shall reside in
  `.apps/translationCoreAI/bridge-workbench.sqlite3` at schema v3.
- `DR-003 P0` Passage-semantic data shall reside in
  `.apps/translationCoreAI/passageSemantic/bridge-semantic.sqlite3` at schema
  v16.
- `DR-004 P1` Application registry, non-secret settings, and progress cache shall
  reside in the workspace database at schema v2.
- `DR-005 P0` Secrets shall remain separate from project-portable data and shall
  be protected using the operating system facility.
- `DR-006 P0` Collection identity shall be recorded on every sibling using
  portable metadata.

### 8.2 Identity and history

- `DR-010 P0` Finding identity shall be deterministic for equivalent chapter,
  verse, engine, check type, and disambiguator input.
- `DR-011 P0` Token lineage plus instance fingerprint shall survive retokenizing,
  re-import, split, merge, and successor resolution where correspondence exists.
- `DR-012 P0` Human decisions, correction proposals/events, tN/tW disposition
  events, alignment history, and change-log entries shall be append-only.
- `DR-013 P0` Superseded rows shall remain stored and readable.
- `DR-014 P0` Latest current job/result shall be authoritative without pruning
  prior attempts.
- `DR-015 P0` Every derived writable record type shall participate in dependency
  invalidation and support a stale lifecycle.

### 8.3 Transactions, backup, and migration

- `DR-020 P0` Scripture-changing actions shall use the existing transaction
  journal and recover incomplete work deterministically.
- `DR-021 P0` Material writes shall create the required checksummed backup before
  completing the authoritative change.
- `DR-022 P0` Each database shall maintain an independent, monotonically
  increasing schema version.
- `DR-023 P0` A schema change shall add a forward migration and a compatibility
  test proving previous-version data remains readable.
- `DR-024 P0` Bridge shall refuse to open a database newer than the version it
  understands and shall never downgrade it.
- `DR-025 P0` Migration shall not assume a translation team's project can be
  recreated from source.

### 8.4 Encoding and coordinate systems

- `DR-030 P0` All text protocol and file I/O shall use explicit UTF-8 or UTF-8
  with BOM where the external format requires it.
- `DR-031 P0` Semantic and correction spans shall use the documented Unicode
  code-point/grapheme-safe coordinate contract across Python and TypeScript.
- `DR-032 P0` Canonically equivalent NFC/NFD content shall compare consistently
  without discarding meaningful combining marks.
- `DR-033 P1` Stored timestamps shall be unambiguous and sufficient for ordered
  audit presentation.

## 9. External interface requirements

### 9.1 User interface

- `IR-UI-001 P1` The primary surfaces shall include Project Home, dashboard,
  Scripture editor/review panel, Alignment Review, Align Words, cross-verse
  alignment, project report, Settings, Export, and Diagnostics.
- `IR-UI-002 P1` Global navigation shall expose project, collection book,
  chapter, report, export, settings, and applicable sync actions.
- `IR-UI-003 P1` Destructive or Scripture-changing actions shall require clear,
  explicit confirmation at the point of action.
- `IR-UI-004 P1` Running, completed, failed, cancelled, stale, limited, and
  recovery-required states shall be visibly distinguishable.
- `IR-UI-005 P1` User-facing text shall avoid raw stack traces or unexplained
  protocol identifiers in normal operation.

### 9.2 Engine protocol

- `IR-RPC-001 P0` The sidecar protocol shall use UTF-8 JSON-lines with request
  `id`, dotted method name, params object, and correlated success/error reply.
- `IR-RPC-002 P1` Frontend method names and parameter/result types shall be
  represented in the typed client boundary.
- `IR-RPC-003 P1` Protocol errors shall include enough context for the UI to
  present an actionable failure without exposing secrets.
- `IR-RPC-004 P1` Lightweight status/read methods shall remain responsive while
  supported background work is active.

### 9.3 File interfaces

- `IR-FILE-001 P0` Supported import/export formats and their limitations shall be
  documented.
- `IR-FILE-002 P0` Bridge shall not modify the original source location during
  import.
- `IR-FILE-003 P0` Generated project and export files shall be deterministic
  enough for identity, duplicate, and round-trip checks.

### 9.4 Optional online AI interface

- `IR-AI-001 P0` No AI call shall occur on import, open, local check, manual
  review, alignment, report viewing, or export unless the user invokes an AI
  feature.
- `IR-AI-002 P1` Provider requests shall send only the bounded task context and
  relevant evidence needed for that feature.
- `IR-AI-003 P1` Provider/model incompatibility shall fail or use an explicitly
  supported fallback without weakening review policy silently.
- `IR-AI-004 P1` AI usage, latency, and error state shall be observable where the
  feature records them.

### 9.5 Paratext and Logos interfaces

- `IR-EXT-001 P1` Paratext integration shall use the companion/named-pipe and
  Notes mechanisms documented by the connector.
- `IR-EXT-002 P1` Logos integration shall use the packaged Windows PowerShell/COM
  bridge and fail cleanly when Logos is absent.
- `IR-EXT-003 P0` External applications shall never become authority over Bridge
  project identity, review state, or Scripture writes.

## 10. Non-functional requirements

### 10.1 Offline operation

- `NFR-OFF-001 P0` Import, open, navigation, local checks, manual review,
  alignment, semantic analysis, correction review/apply, reporting, and export
  shall function without network connectivity.
- `NFR-OFF-002 P0` Normal operation shall not require an account or login.
- `NFR-OFF-003 P1` Bundled source data, translation helps, fonts, and vendored
  checkers shall be sufficient for their documented offline workflows.

### 10.2 Performance and responsiveness

- `NFR-PERF-001 P1` Interactive navigation and status requests should normally
  complete within five seconds on supported field hardware.
- `NFR-PERF-002 P1` Expensive work shall expose progress and cancellation and
  shall not block the UI for its full duration.
- `NFR-PERF-003 P1` Large collection discovery/import shall avoid eager
  normalization of every sibling.
- `NFR-PERF-004 P1` Project open, large-book checks, report generation, and AI
  jobs shall be measured on real projects and recorded in release evidence.
- `NFR-PERF-005 P2` Long-running sessions shall not show unbounded memory, CPU,
  polling, or temporary-file growth.

### 10.3 Reliability and data integrity

- `NFR-REL-001 P0` A crash, timeout, cancellation, retry, or restart shall not
  produce a duplicate authorized write.
- `NFR-REL-002 P0` Partial work shall not be presented as complete success.
- `NFR-REL-003 P0` Writes shall fail closed when identity, revision, hash, or
  recovery preconditions do not match.
- `NFR-REL-004 P0` A failure in optional AI, Paratext, Logos, diagnostics, or one
  checker shall not make unrelated local workflows unusable.
- `NFR-REL-005 P1` Errors shall identify the affected operation and recovery or
  retry path.

### 10.4 Security and privacy

- `NFR-SEC-001 P0` Bridge shall not store API secrets in plaintext.
- `NFR-SEC-002 P0` Project, report, diagnostic, and export artifacts shall not
  unintentionally contain secrets or unrelated provider payloads.
- `NFR-SEC-003 P0` Archive extraction and file operations shall constrain output
  to the intended destination.
- `NFR-SEC-004 P1` AI data transmission shall occur only after an explicit
  feature invocation and shall be described in the UI.
- `NFR-SEC-005 P1` The installed application shall disclose signature state and
  expected Windows warnings in release evidence.

### 10.5 Accessibility and usability

- `NFR-A11Y-001 P1` Primary workflows shall be operable with keyboard only.
- `NFR-A11Y-002 P1` Interactive controls shall have accessible names, visible
  focus, logical order, and correct disabled/selected state.
- `NFR-A11Y-003 P1` Focus shall not be trapped and shall return to the invoker
  after closing menus, popups, and modals.
- `NFR-A11Y-004 P1` Status, severity, selection, and errors shall not rely on
  color alone.
- `NFR-A11Y-005 P1` The UI shall remain usable at 100%, 125%, and 150% Windows
  scaling and at the supported 1366×768 reference size.
- `NFR-A11Y-006 P1` Context menus and essential actions shall remain inside the
  viewport in narrow or edge-positioned layouts.
- `NFR-A11Y-007 P1` Supported Indic, Hebrew, Arabic, Greek, Southeast Asian, and
  Latin text shall render without missing glyph boxes or detached marks.
- `NFR-A11Y-008 P1` RTL Scripture and mixed-direction metadata shall remain
  readable and operable.

### 10.6 Compatibility and portability

- `NFR-COMP-001 P0` Windows x86-64 installer and runtime behavior shall be the
  release gate until another platform is explicitly verified.
- `NFR-COMP-002 P0` Exported USFM shall be re-importable by Bridge and compatible
  with the documented translationCore ecosystem expectations.
- `NFR-COMP-003 P1` Optional dependencies shall degrade gracefully when absent.
- `NFR-COMP-004 P1` Windows text I/O shall not depend on the machine's legacy
  code page.

### 10.7 Maintainability and testability

- `NFR-MNT-001 P1` Protocol methods shall remain centralized in the engine
  dispatcher and typed frontend client.
- `NFR-MNT-002 P0` Changes to schemas, invariants, golden files, confidence
  policy, Scripture-writing paths, vendored trees, or runtime networking shall
  follow the repository's explicit review rules.
- `NFR-MNT-003 P1` New derived record types shall be registered in the dependency
  graph and covered by stale-propagation tests.
- `NFR-MNT-004 P1` Automated coverage shall include source, packaged/frozen, and
  frontend behavior where the boundary can diverge.
- `NFR-MNT-005 P1` Visual/layout behavior not exercised by jsdom shall receive
  installed-app manual acceptance.
- `NFR-MNT-006 P1` Documentation and release evidence shall state what was
  actually observed separately from what is inferred from code.

### 10.8 Licensing and provenance

- `NFR-LIC-001 P0` Vendored source and bundled datasets shall retain their
  notices, pinned source revisions, licenses, and required attribution.
- `NFR-LIC-002 P1` Resource-generation scripts and provenance hashes shall make
  bundled data reproducible and auditable.
- `NFR-LIC-003 P1` Bridge shall not present third-party Scripture or resource
  data as Bridge-authored content.

## 11. Business and safety rules

- `BR-001 P0` A human is the final authority over translation decisions.
- `BR-002 P0` Greek Room or deterministic analysis may identify suspicion but
  shall not independently authorize a Scripture edit.
- `BR-003 P0` AI output is evidence or a proposal, never unquestioned authority.
- `BR-004 P0` Stage 9B's confirmed apply action is the only correction workflow
  authorized to write Scripture.
- `BR-005 P0` Human review and correction history shall not be compacted away.
- `BR-006 P0` Identity must survive re-runs: findings use stable IDs and tokens
  resolve lineage rather than receiving unrelated new identities.
- `BR-007 P0` Staleness shall propagate through registered dependencies.
- `BR-008 P0` Latest jobs are authoritative while prior jobs remain auditable.
- `BR-009 P0` Confidence numbers are policy inputs, not proof of correctness, and
  shall not be tuned merely to make a test pass.
- `BR-010 P0` A correction is not complete until current evidence positively
  verifies the original obligation and a human acknowledges it.
- `BR-011 P0` Offline capability is a product invariant, not a preference.

## 12. Verification and acceptance

### 12.1 Required evidence levels

| Level | Evidence |
|---|---|
| Source | Python, frontend, or Rust automated tests against source. |
| Frozen | Packaged sidecar smoke against the exact worker binaries. |
| Desktop | Installed Windows interaction with real rendering and native dialogs. |
| External | Live provider, Paratext, Logos, filesystem, or OS behavior where applicable. |

### 12.2 Release gates

- `AC-001 P0` P0 manual cases shall pass or have explicit maintainer disposition
  and linked follow-up before release.
- `AC-002 P1` Frontend verification shall run `npm run check`, `npm run test`, and
  `npm run build`.
- `AC-003 P1` Engine verification shall run the complete configured pytest suite.
- `AC-004 P1` Desktop shell verification shall run both `cargo check` and
  `cargo test`.
- `AC-005 P0` Both frozen workers shall be built and the exact packaged engine
  shall run the frozen smoke suite.
- `AC-006 P0` The installed-app manual checklist shall cover the candidate build,
  environment, fixtures, failures, blockers, and retained evidence.
- `AC-007 P1` Release artifacts shall record filenames, versions, sizes, hashes,
  signature state, and source commit.
- `AC-008 P1` `QA_TEST_MATRIX.md` shall be updated only with evidence actually
  observed.

### 12.3 Requirement-to-test traceability

| Requirement area | Primary automated/manual evidence |
|---|---|
| Lifecycle | QA checklist `SMK`, `INS`, `ROB`; matrix A04, A05, M43–M44 |
| Project and import | Checklist `PRJ`, `IMP`; matrix I01–I19, P01–P09 |
| Editor and checks | Checklist `EDT`, `CHK`; matrix A06–A15, M37–M42 |
| Translation helps / AI | Checklist `TH`; matrix A20–A35, D12–D14 |
| Alignment | Checklist `ALN`, `XVA`; matrix L01–L10, A16–A19, A40–A41 |
| Semantic review | Checklist `REV`; Stage 5/6B goldens and Stage 7/8/9A tests |
| Correction | Checklist `COR`; Stage 9B tests and installed acceptance |
| Report and triage | Checklist `RPT`; matrix A36–A37, D12–D13, E07–E08 |
| Settings/integrations | Checklist `SET`; matrix A08, A24–A26, A34–A35 |
| Export | Checklist `EXP`; matrix E01–E08 |
| Accessibility/localization | Checklist `A11Y`; matrix A39, M01–M05, M37–M47 |
| Reliability/data safety | Checklist `ROB`; database migration and recovery tests |

The current manual execution tracker is GitHub issue #150 with category
sub-issues #151–#167 and the project `QA` view.

## 13. Out of scope for the current baseline

- A required hosted server, cloud database, or login.
- Automatic modification of Scripture outside the authorized Stage 9B apply
  path.
- Neural model training or automatic learning from review decisions.
- Treating confidence thresholds as calibrated truth.
- Fabricating unavailable translationNotes or translationWords indexes.
- Replacing native translationCore verse-local alignment with Bridge-private
  cross-verse links.
- Verified macOS or Linux distribution.
- Unreviewed changes to bundled embedding models or a new vendored upstream
  tree.

## 14. Future requirements requiring separate approval

The following are design direction, not current release requirements:

- `FUTURE-001` Optional named users/devices and roles for every human write.
- `FUTURE-002` Optional team hub synchronization using the local change log.
- `FUTURE-003` Hub-served team dashboard and collaboration workflows.
- `FUTURE-004` Verified macOS and Linux packaging/runtime behavior.
- `FUTURE-005` A production multilingual embedding provider, if explicitly
  approved and kept compatible with offline operation.

Before implementation, each future item requires an issue, architecture review,
privacy/offline analysis, acceptance criteria, and an explicit decision about
which current requirements it may affect.

## 15. Change control

- Changes to this SRS shall identify affected requirement IDs.
- A requirement may be clarified without renumbering it.
- Removing or weakening a P0 requirement requires maintainer approval and a
  recorded decision.
- Implementation changes that reveal a mismatch shall update the SRS,
  applicable tests, and release evidence in the same work item where practical.
- Historical release evidence shall not be rewritten to imply a requirement was
  tested when it was not.
