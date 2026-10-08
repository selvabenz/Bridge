# Bridge — invariants

The parts of the Stage 4 to 9 design that are rules rather than history: token
identity, the Unicode span contract, the embeddings policy, the hard
constraints, and the continuity rules. Engine code and `BUILD_LOG.md` cite
these by section number, so **the original numbering is kept** — `§39` here is
`§39` everywhere it is cited.

Every section below is lifted **verbatim** from `docs/archive/HANDOFF.md`
(2026-09-17), which was deleted in the same change: the rest of that file was
either duplicated by `BUILD_LOG.md`, covered by
`passage-aware-semantic-alignment.md`, or transfer instructions for a second
developer that no longer apply. Where a section is a dated snapshot rather
than a standing rule, a note says so above it — the text itself is not edited.

Current-state architecture is `ARCHITECTURE.md`; the session record is
`BUILD_LOG.md`; the requirements spec these rules come from is
`passage-aware-semantic-alignment.md`.

---

# 20. Stable Token Identity

Separate:

```text
TokenLineage
```

from:

```text
TokenInstance
```

Edits create new target token instances.

Lineage may suggest correspondence but must never silently relocate a
human-approved alignment.

Canonical identity includes versioned fields such as:

```text
resource/project
book
displayed reference
canonical reference
token layer
index
occurrence
raw form
normalized form
character span
tokenization version
resource/text revision
```

Temporary `H###` / `T###` IDs are UI aliases only.

---

# 21. Unicode Span Contract

Persist half-open Unicode code-point coordinates:

```text
[startCodePoint, endCodePoint)
```

against raw unnormalized text.

Cross-language rules:

- Python: Unicode code points
- Rust: `.chars()`, not byte indexing
- TypeScript: convert code-point ranges; native UTF-16 string indices are
  not canonical

Utilities convert among:

```text
code point
UTF-8
UTF-16
grapheme boundaries
```

Tests cover Tamil combining characters, Hebrew niqqud/cantillation, Greek
diacritics, and supplementary Unicode.

---

# 21a. Unicode semantic-comparison invariant (V1.1)

*Moved verbatim from `DEVELOPER_GUIDE.md` section 1, 2026-09-17; it is the
comparison-key half of the span contract above.*

**Unicode semantic-comparison invariant (V1.1):** authoritative Scripture is
never normalized or rewritten for comparison. Stage 7 derives transient NFC,
Unicode-case-folded comparison keys by walking extended grapheme clusters and
preserving Letter, Number, and Mark material. Canonically equivalent NFC/NFD
spellings therefore compare consistently, while compatibility distinctions are
not erased. Punctuation and symbols remain comparison boundaries. Internal
ZWJ/ZWNJ/WORD JOINER controls are retained in orthographic runs; standalone
directional controls do not become semantic tokens. The one explicit exception
is Stage 7's controlled Biblical-Hebrew category matching, which can derive an
unpointed consonantal key without changing UHB token identity or stored text.

Grapheme safety is not universal lexical segmentation. The generic comparison
tokenizer intentionally does not claim dictionary-quality boundaries for
Thai, Khmer, Lao, Myanmar, or other no-space writing systems. Persistent spans
remain exact half-open Unicode code-point offsets over raw text, and normalized
comparison keys must never be used to apply a correction.

---

# 31. Embeddings

Embeddings are candidate-retrieval evidence, not truth. Provider metadata is
versioned. No production multilingual embedding model is currently bundled.
Bridge does not depend on an online API. Embedding similarity never
overrides deterministic contradiction evidence (enforced in both Stage 7's
`DeterministicMeaningComparator` and Stage 8's gate policy).

---

# 36. Current Limitations

> **Dated snapshot, not a standing rule.** This list was written at the end of
> Stage 9A and is kept because most of it is still the honest state of the
> pipeline. Two bullets are known to be out of date: the Tamil-negation bullet
> describes a comparator that no longer behaves that way (see #128), and
> reviewer identity is now a named local user with a device id, not the string
> `"human"` (#75, #77). Check the code before relying on any single line.

At the end of Stage 9A:

- no production multilingual embedding model bundled
- Stage 6B location benchmark and Stage 7/8 benchmarks are all
  `MACHINE_PROPOSED` only — none are human-reviewed; do not claim
  production calibration from any of them
- requested passage range is currently a hard computational boundary; full
  automatic structural expansion beyond requested range remains future work
- paragraph/sentence precision depends on available structural metadata
- contextual evidence is stored but zero-weight until calibration supports
  it
- Stage 8's `QaFinding.severity` confidence thresholds (0.85/0.9 cutoffs)
  are uncalibrated, same caveat as every confidence value elsewhere in this
  pipeline
- Stage 8's target-support gate's function-word/explicitation/specificity
  lists are small, deliberately controlled fixtures (mirroring Stage 7's
  own comparator lists) — real-world coverage across languages is
  unvalidated beyond the English/Tamil/Hebrew/Aramaic cases actually tested
- `passage_semantic_models.QaFinding` (Stage 8's output) reaches the UI via
  Stage 9A's Alignment Review surface, *not* via ReviewPanel — it remains a
  deliberately separate model from `greek_room_engine.models.finding.QaFinding`
  (see §34), and the two are still unreconciled. The two review surfaces sit
  side by side; whether they should converge is an open product question
- ~~no correction-generation workflow~~ — built across Stage 9B.0–9B.4:
  wording, review, explicit apply, affected re-analysis, positive semantic
  verification and explicit `CORRECTED` acknowledgement (§37.6). Installed
  desktop acceptance of the verify → Mark corrected flow is not yet run
- Stage 7's `_comparison_norm` splits Indic text at every virama and vowel
  sign, so its whole-token POLARITY check reports a false `CONTRADICTED` on
  Tamil negation. Found during Stage 9B.4, pinned by a test, deliberately
  not fixed there (§37.6) — a fix must re-baseline the Stage 7/8 goldens
- ~~nothing in the app produces Stage 5-8 analysis~~ — resolved by Stage
  9A.4's explicit, persisted background orchestration. Whole-Bible scope is
  still deferred, and normal projects visibly use limited lexical/structural
  retrieval until a production multilingual embedding provider is configured
- the Stage 9A review UI has **never been observed rendering a populated
  queue in the desktop app**; jsdom cannot lay out or paint, so small-screen
  behaviour at 1366x768 is asserted structurally only
- reviewer identity is the single local string `"human"`; `TeamWorkflow` in
  `team.py` is not wired into semantic review records
- ~~no final Alignment Review UI~~ — built in Stage 9A (review only; see
  the unverified-click-through caveat in the Stage 9A record above)
- no Scripture Burrito export
- no new native translationCore projection behavior
- ~~no narrative for Stages 4 through 7~~ — retrospective entries added in
  Stage 9A.0, explicitly marked as reconstructed from commits, code and
  tests, and limited to what those verify

---

# 39. Hard Non-Negotiable Constraints

Do not:

```text
hardcode Tamil
hardcode Philippians
hardcode four verses
assume same verse number = same semantic location
reuse source tokens across active lexical groups
reuse target tokens across active lexical groups
equate null alignment with omission/addition
equate unaligned with null-aligned
force mapping when uncertain
treat embeddings as proof
treat tN/tW/TWL as infallible
silently overwrite human-approved work
silently relocate stale alignments
silently migrate changed source tokens
inject rich Bridge semantics into canonical USFM
fake cross-verse alignment for translationCore
automatically rewrite Scripture
use imported USFM wording after target edit
use target text to construct source inventory
use source expectations to construct target inventory
let computation failure become NOT_LOCATED/MISSING
let meaning mismatch silently change Stage 6B location
let Stage 8 re-run Stage 6B search or re-judge Stage 7 meaning
auto-promote POSSIBLY_MISSING/POSSIBLY_UNSUPPORTED without human action
```

Do:

```text
align semantic realization before judging errors
use passage-aware search
keep source and target inventories independent
audit source→target and target→source separately
preserve exclusive lexical ownership
support null explicitly
preserve raw Scripture
pin source versions
normalize versification
retain evidence/provenance
abstain under uncertainty
protect human decisions
separate semantic validity from exportability
separate mapping error from translation error
require human approval for corrections
rerun analysis after edits
verify repository reality against this document before relying on it
```

---

## 44.8 Non-negotiable continuity rules

- Never hardcode Tamil, Philippians, or a fixed verse window.
- Verse numbers are reference anchors, not semantic boundaries.
- Never rewrite Scripture automatically or reuse stale imported wording.
- Never silently move, replace, or approve human work.
- Never equate unaligned, null-aligned, not-located, and missing. A null
  alignment is a stored, reasoned row (`alignment_null_decisions`, #216); the
  absence of a row means unaligned, and an empty tC group is never read as
  null-aligned.
- Never convert search/computation failure into omission/addition.
- Never treat embeddings or translation helps as proof.
- Never manufacture same-verse translationCore alignment for cross-verse
  meaning.
- Persistent spans remain half-open Unicode code-point offsets over raw text.
- Correction Apply requires exact reference, span, current text,
  revision/hash, CAS, and explicit human action; normalized text is never an
  edit coordinate.
- Keep dependency/coverage DAGs separate from cyclic semantic relation graphs.
- Preserve one authoritative active lexical solution per scope/profile/layers
  and exclusive membership within each lexical layer.
- Keep `ReviewStatus`, `LifecycleStatus`, and `QaDisposition` independent.
- Preserve clean USFM and native translationCore behavior.
- USFM is read only through `usfm_parser.py` (a book) and `usfm_verse.py` (a
  verse string), engine and frontend alike; no other module parses it (#91).
  The stored verse string is a verbatim slice of the source; the display the
  reader shows is derived from it by deletion only, so raw offsets map exactly.

---
