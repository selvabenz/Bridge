# Manual word alignment

Bridge provides a human-controlled, translationCore-compatible
word-alignment editor. For raw Scripture imports it initializes source slots
from pinned, bundled UHB/UGNT token packs; it never guesses tokens and never
changes Scripture text as a side effect of alignment work.

## Data and protocol

The source of truth remains:

```text
.apps/translationCore/alignmentData/<book>/<chapter>.json
```

Each token keeps its `word`, `occurrence`, and `occurrences` identity. Source
tokens also retain Strong's, lemma, and morphology attributes when available.
The sidecar exposes:

- `alignment.get` and `alignment.status`
- `alignment.realign`, `alignment.unalign`, and `alignment.save`
- `alignment.complete`
- `alignment.undo`, `alignment.backups`, and `alignment.restore`

`realign` supports 1:1, 1:many, many:1, and many:many groups. Every save must
contain exactly the token identities loaded by the editor. The
`expectedOriginal` snapshot provides optimistic concurrency: if another process
or editor changed the verse on disk, the stale save is rejected.

## Safety and history

Approved changes are atomic and journaled. Before changing a chapter, Bridge
creates a backup and records per-verse history under:

```text
.apps/translationCoreAI/alignmentHistory/<book>/<chapter>/<verse>/
```

History and chapter data are committed in the same transaction. Undo restores
only the selected verse from its backup, so unrelated verses in the chapter are
not rolled back. Completion uses translationCore's native word-alignment state
files and survives an application restart. Editing Scripture reconciles the
target tokens and marks the alignment invalid for review.

## Completion and status

Verse status is `untouched`, `partial`, `complete`, or `invalid`. The editor and
chapter toolbar expose these states. Completion is blocked when:

- original-language tokens are absent;
- a source or target token remains unaligned;
- a token is missing, duplicated, or no longer matches the target text;
- a target group is non-contiguous and cannot be represented safely in aligned
  USFM.

After every alignment mutation the UI immediately reruns local and Greek Room
checks for that verse.

## Cross-verse links (Bridge-private, #117)

translationCore alignment groups are verse-local: each verse's groups may only
contain that verse's own target tokens, and `_validate_alignment_identity`
enforces it on every save by comparing the token multisets before and after.
Bridge never fakes a cross-verse link inside them (`semantic_alignment_guard.py`,
`INVARIANTS.md` §39). A reviewer's judgement that a source token of one verse is
realized in another verse's target text is therefore recorded outside
`alignmentData/`, in `alignment_cross_verse_links` in the per-project
`bridge-workbench.sqlite3` (schema v3).

```text
alignment.getRange(chapter, verses[])    one context per verse, plus per-verse gap counts
alignment.gapScan(chapter)               every verse's gaps NAMED, chapter-wide (#137)
alignment.crossVerse.propose(chapter, verses[])   ranked candidates, read-only (#139)
alignment.crossVerse.link                {source: {chapter, verse, topId}, target: {chapter, verse, bottomId}}
alignment.crossVerse.unlink              {linkId}
```

- A link is keyed by the two translationCore **token signatures**
  (`word`, `occurrence`, `occurrences`) plus chapter and verse on both sides.
  The positional `H001`/`T001` ids are resolved per request and never stored:
  `make_inventory` regenerates them on every load.
- It is refused when either token is already aligned inside its own verse, when
  both ends are the same verse (that is `alignment.realign`), or when the target
  word is not in the current text.
- Each change writes three things: the row, a `change_log` domain event
  (`crossVerseLink` / `crossVerseUnlink` / `crossVerseInvalidate`), and an
  `alignment_history` row with **no** `backupPath` — nothing on disk changed, so
  it is in the history but never offered for restore.
- **Status:** a verse's alignment context reports `crossVerseLinks`,
  `crossVerseAccountedIds`, `crossVerseRealizedIds`, counts and `fullyAccounted`,
  and its `gaps` are net of active links. `status` and `completionState` are
  unchanged and keep telling the translationCore truth: a fully-accounted verse
  still reads `partial` or `untouched` and `pending`, and `canComplete` stays
  false, because aligned USFM cannot express the link.
- A target edit that removes a linked word marks the link `invalid` with a
  reason, inside the same journal transaction as the edit, rather than deleting
  it.
- Stage 6B reads active links as `WORD_ALIGNMENT` location evidence at the same
  weight as completed same-verse alignment, and a link change stales the
  downstream Stage 6B/7/8 records (#119).

### Groups, and one token one home (#217)

The spec's lexical ownership rule (section 5) applies across verses too. A 1:N,
N:1 or N:M realization is **one** composite group, not several overlapping
pairs, and every token has at most one home: a tC group, a cross-verse group
or a null decision.

- A row is still one (source, target) pair, so the unique pair index and every
  reader are unchanged. `groupId` in the payload ties a group's rows together.
  It is derived from the members, so the same group gets the same id
  everywhere. A row written before groups existed is a group of one.
- `alignment.crossVerse.link` also takes `{sources: [...], targets: [...]}`.
  All sources must be in one verse and all targets in one other verse. N×M rows
  and events are written, plus **one** history row (`crossVerseLinkGroup`).
- Dropping a single word onto a word that is already in a group between the
  same two verses **extends** that group: it unlinks the group and links the
  union, under a new `groupId`. A word already linked into a *different* verse
  is refused, and so is an exact duplicate.
- `alignment.crossVerse.unlink` takes `{linkId}` or `{groupId}` and removes the
  whole group either way. Removing one pair would leave a claim nobody made.
- A text edit that removes any target word of a group invalidates the whole
  group.
- `_save_alignment` (realign, unalign, save, aiApplyProposal) refuses to put a
  token into a tC group when the token is at an end of an active link or
  carries an active null decision. Tokens leaving a group never conflict. A
  token that was already double-homed before this rule is left as it is, not
  repaired silently.
- The verse context adds `crossVerseGroups`
  (`{groupId, relation, sources, targets, sourceTopIds, targetBottomIds, linkIds, state}`)
  beside the flat `crossVerseLinks`. `relation` uses the labels
  `compile_link_proposal` gives tC groups.

## Null decisions: a word with no counterpart, for a reason (#216)

translationCore can say "this source word has these target words" or nothing.
An empty group and a word left in the word bank both mean *not aligned yet*.
Bridge records a third statement, the spec's NULL_ALIGNED: this word has no
counterpart, and that is correct. It is stored in `alignment_null_decisions`
(workbench v6), beside the link table, and never in `alignmentData/`.

```text
alignment.null.set    {chapter, verse, side: "source"|"target", id, reason, note?}
alignment.null.clear  {decisionId}
```

- **The reason set is closed and depends on the side.** A source word is
  `IMPLICIT` (context carries it) or `GRAMMATICAL` (an ending or word order, with
  no separate word). A target word is `GRAMMATICAL` (the language requires it)
  or `EXPLICITATION` (it states what the source implies). Nothing else is
  accepted.
- **No row means unaligned.** "Unresolved" is never stored, and an empty tC
  group is never read as a null.
- **One token, one home.** A word already aligned in its own verse, or at
  either end of an active link, cannot be marked. A marked word cannot be
  linked. Clear one home first.
- **Same discipline as a link.** Each change makes three writes: the row, a
  `nullDecide` / `nullClear` / `nullInvalidate` event, and a history row without
  a backup. A different reason is an explicit update that keeps
  `previousReason`. A text edit that removes a decided target word marks the
  decision `invalid`, inside the edit's journal transaction. `origin` (`human`
  or `ai-auto`) is kept on the row as well as on the event, so an automatic
  re-run can supersede its own decisions and never a reviewer's.
- **Status.** A null-decided token is not a gap. The verse context reports
  `nullDecisions`, `accountedBy {tc, crossVerse, null}` and `accounted`, which
  is true when every token has some home and there is a source and no
  structural issue. `status`, `completionState` and `canComplete` keep telling
  the translationCore truth, so a verse whose only remaining tokens are nulls is
  still `partial` there.
- The decisions' digest is folded into `alignment_state_digest`, so a change
  stales Stage 6B/7/8. It is folded only when a book has decisions, so existing
  projects keep their digests.

## Automatic alignment: two passes, agreement writes (#219)

```text
alignment.window.autoAlign   {chapter, verses: [2..5 consecutive], apply?: true}
alignment.autoAlign.revert   {chapter, verse}
alignment.autoAlign.verdict  {chapter, verse}
```

The window (`alignment_window.py`, #214's payload) is the whole source and the
whole target of the verses, in reading order. Each word has an opaque handle
(`S1`, `T1`); Bridge's own ids and signatures are never sent. A word a reviewer
has already placed is shown for context, marked `alreadyAligned`, and any claim
on it is discarded. The window is asked **twice**, and each pass must account
for every word on both sides:

- **source-first**: each source word is linked, marked null, or marked
  unplaced, then each target word left over;
- **target-first**: the same, starting from the target words.

`alignment_agreement.agree` compares the two (pure, keyed on side + verse +
signature):

| Both passes give | Result |
|---|---|
| the same edge | **written**. Same-verse edges are compiled into tC groups (1:1/1:N/N:1/N:M) by `compile_link_proposal` with thresholds 0.0, because agreement is the gate, and saved with operation `ai_auto_align`. Cross-verse edges become one link group per connected component, through `alignment.crossVerse.link`. |
| the same null, same reason | **written** to the null store with `origin: "ai-auto"`, unless either pass also linked the word |
| something only one pass gave, or reasons that differ | a **suggestion** with both votes; nothing written |
| nothing at all for a word | **POSSIBLE_OMISSION** (source) or **POSSIBLE_ADDITION** (target), with the model's notes; nothing written |

- **Corpus check.** Once the project has completed alignments, an agreed
  cross-verse edge is blocked (`CORPUS_DISAGREES`) when the offline scorer's top
  candidate for that source word is a different target word, or the target word
  is contested.
- **Spans.** A cross-verse component reaching more than two verses cannot be one
  group and is offered as a suggestion instead.
- **A reviewer's work is never written over.** Their groups, links and
  decisions are fixed homes. When both passes contradict them, that shows up
  once as a `CONFLICTS_WITH_HUMAN` suggestion.
- **The ledger.** Each verse's verdict row (`alignment_verdicts`) records what
  this run wrote: tC groups by signature set, link `groupId`s and decision ids.
  A re-run first undoes its predecessor's writes, but only where they are still
  exactly as written and, for a link, only where both ends lie inside the
  window. `alignment.autoAlign.revert` does the same for one verse and reports
  anything a reviewer has since changed as `skipped`.
- **Verdict.** `ALIGNED_CLEAN` when nothing is left to suggest or report and the
  verse is `accounted`; otherwise `NEEDS_REVIEW`. tC `status` and
  `completionState` stay honest, and a verse whose words all landed in
  same-verse groups is marked completed by the ordinary save path.
- **Offline.** No key means a structured `unavailable` and no request.
  Nothing is sent on open, import or check. The Rust timeout for the method is
  540 s (two provider calls).
- **Limits.** Two samples of one model are correlated: agreement lowers the
  error rate, it does not bound it. Every number is uncalibrated
  (`two-pass-agreement-v1`). No real-provider run has been recorded yet (#131).

### In the verse editor (#220)

The ordinary `alignment` check engine (and `local`) raises
`alignment.possible_omission` (high) and `alignment.possible_addition` (medium)
for every live gap of a verse that an automatic pass has run on, or that
translationCore marks completed. A gap is live when the word is not in a tC
group with a counterpart, not at either end of an active link and not
null-decided. Nothing is raised on a verse nobody has aligned, where
`ALIGN_UNALIGNED_*` already applies, or on one that was reverted.

- The finding id is stable: `group_id` is the token signature, so decisions are
  re-applied as usual.
- A target finding carries the word's raw span, so the editor underlines it.
- The explanation carries the model's note. If the verse changed after the
  pass, it says to run the pass again instead.

## What Stage 6B and Stage 8 read from all this (#218)

- A cross-verse **group** reaches Stage 6B as one `WORD_ALIGNMENT` precedent
  with all its sources and all its targets. That is the shape a same-verse N:M
  tC group already has, so no weight or threshold changes. If any member does
  not resolve, the whole group is dropped, the same all-or-nothing rule the
  same-verse path uses.
- A **null decision** has no target span, so Stage 6B never sees it.
  `null_precedents_for_range` resolves the active decisions in a range to Stage
  5/6A token instance ids, and Stage 8 reads them:
  - A source unit whose every token carries the same `GRAMMATICAL` or
    `IMPLICIT` decision is `COVERED_BY_RESTRUCTURING` instead of
    `POSSIBLY_MISSING`. This applies only when Stage 6B found it `NOT_LOCATED`
    or found no relationship. An `AMBIGUOUS` or incomplete search stays
    `UNCERTAIN`, and a located realization is still judged on its meaning.
  - A target unit whose every token carries the same `GRAMMATICAL` or
    `EXPLICITATION` decision is `GRAMMATICALLY_REQUIRED` or
    `EXPLICITATION_SUPPORTED`. The decision is checked before the hard-coded
    function-word and specificity lists, which only know English and a few
    Tamil forms.
- Stage 8 still never re-runs Stage 6B. Decisions reach Stage 8's fingerprint
  through `alignment_state_digest`, then the location run, then the meaning
  run. `ALIGNMENT_EVIDENCE_VERSION` is `tc-word-alignment-v4` and
  `QA_POLICY_VERSION` is `qa-policy-v2`, so every cached run goes stale once.
- The Stage 6B golden has no alignment data, links or decisions, so it is
  unaffected, and so is the Stage 5 golden.

## Finding the gaps, and suggesting what fills them (#137–#139)

A **gap** is a source token with no target word in its own verse, or a target
word with no source token — always **net of active cross-verse links**, so a
linked word is no longer a gap. `alignment_gaps.py` is the single definition of
that; `_alignment_context` and `alignment.gapScan` both call it, so a count and a
named list cannot disagree. `alignment.gapScan` covers a whole chapter and reads
the chapter JSON and the link table once each, rather than looping
`_alignment_context` (which re-parses the whole chapter per verse).

`cross_verse_proposals.py` then ranks, for each unmatched source token, the
unaccounted target words of the *other* verses in range. Two things about it are
easy to get wrong later:

- **It does not consult Stage 6B, on purpose.** Stage 6B marks a relationship
  `CROSS_VERSE`, but in the shipped app it cannot find one unaided:
  `SemanticEmbeddingProvider.available` is `False`, so SEMANTIC_SIMILARITY (0.42)
  is always 0, and `_lexical_score` between an original-language string and a
  target-language one is 0 as well — the remaining components sum to at most 0.33
  against a `located_minimum` of 0.36. The components that *can* carry a
  relationship over it, HUMAN_PRECEDENT and WORD_ALIGNMENT, both mean "a human
  already said so", and a token that is still a gap has no such judgement. Wiring
  Stage 6B in would look like evidence and be tautology. If the embedding provider
  ever ships, that is the moment to revisit.
- **The evidence is the project's own completed alignments**, through
  `alignment_statistics.CorpusStatsTable` — translation probability, PMI and the
  Smart-Edit-Distance phonetic boost — plus a Strong's-keyed index beside the
  surface one, because every inflected form of a source word is otherwise its own
  sparse key. This is the only offline signal that is genuinely bilingual. Its
  honest limit is that the table is built from **completed** verses only, so a
  book with none teaches it nothing and the result says so rather than returning
  an empty list.

Every weight and cut-off is an uncalibrated placeholder
(`PROPOSAL_CALIBRATION_VERSION`), with raw and weighted scores kept apart per
component. The ambiguity margin is measured on the score **excluding** proximity:
verse distance orders the candidates but never settles them, and two source
tokens whose best candidate is the same target word are both reported contested.

**Nothing here writes.** Accepting a suggestion is an ordinary
`alignment.crossVerse.link` call, and that remains the only writer.

### Asking a model as well (#146)

`alignment.crossVerse.aiPropose` puts the same question to the configured
OpenAI-compatible provider. It exists for the limit named above: a book with no
completed verses teaches the corpus table nothing, and that is exactly where a
reviewer most needs a suggestion.

- **The model picks from a closed menu.** It is handed opaque `S1`/`T1` handles
  for the gap tokens only — never Bridge's own positional ids, never the verse's
  full inventory — plus each verse's translation and a gloss for each source
  word. Every id in the reply is resolved back through that table and an unknown
  one is an `AIError`, so the model cannot invent a word; it can only mis-pick
  from a list Bridge wrote. Same discipline as `run_full_review`'s evidence ids.
- **`autoLinkable` is agreement, not confidence.** A proposal may be linked
  without a per-link click only where the model's pick and
  `cross_verse_proposals`' top candidate are the same uncontested pair. The
  model's own 0–100 number is recorded as evidence about the model and is
  deliberately not the gate — it is as uncalibrated as everything else here. On a
  cold-start book the corpus proposes nothing, so nothing is auto-linkable there:
  the model adds suggestions where it has no corroboration, not writes.
- **Still one writer, still click-only.** The proposer writes nothing; the caller
  applies an agreed proposal through the same `alignment.crossVerse.link`, with
  `origin: "ai-auto"` on the append-only `change_log` event so the audit trail can
  say what chose it. No request is ever sent because a page opened or a range
  changed.
- **Optional.** With no API key the method returns a structured `unavailable` and
  the offline path is untouched — the offline invariant holds.

Unmeasured, and worth knowing: per #131 no Bridge AI path has ever been sent a
real request, so prompt quality and the agreement-gate hit rate are unknown until
a real-key run is done and recorded.

## Aligned USFM

Aligned export writes USFM 3 and uses unfoldingWord's occurrence-aware `zaln`
and `w` user-extension convention. Nested source milestones represent
many-to-many relationships. The serializer preserves punctuation, inline
markers, footnotes, cross-references, headings, poetry, and custom markers by
using the retained imported USFM as its structural template.

USFM permits user-defined `z` markers and attributes; see the official
[USFM character attributes documentation](https://docs.usfm.bible/usfm/3.1.2/char/attributes.html).
`zaln` is an unfoldingWord interoperability convention layered on that extension
mechanism, not a native semantic alignment feature defined by the USFM standard.

## Bundled original-language source

Raw OT and NT imports receive blank source alignment groups from unfoldingWord
UHB v3.0.0 and UGNT v0.34 respectively. The packs preserve word, occurrence,
Strong's, lemma, and morphology fields using translationCore's pinned
`usfm-js`/`word-aligner` conversion path. Footnotes are excluded from the verse
body, so a Hebrew Ketiv in the body is retained while its Qere footnote is not
treated as a second alignable source token. Verse bridges combine canonical
verses and recalculate occurrence identities across the result.

This initialization is deliberately one-way and conservative:

- aligned USFM and native translationCore source groups remain authoritative;
- recovery fills only an exactly empty `alignments` array in a known Bridge raw
  import and leaves every non-empty verse unchanged;
- a project stamped with a different source-resource version/commit is not
  silently migrated;
- every pack is SHA-256 checked before use, and resource version, commit,
  attribution, license, and provenance are visible in Settings.

The exact upstream commits, file hashes, conversion dependencies, license, and
change statement are recorded beside each resource in
`engine/resources/{hbo,el-x-koine}/...`. Regenerate them only with
`npm run vendor:original-language -- --uhb <checkout> --ugnt <checkout>`;
the generator rejects checkouts that are not the pinned commits.

Optional AI gap-fill proposals remain human-triggered. Live original-language
downloads and live Paratext/Logos synchronization remain future work.
