# Language QA house style

House style is what a project has decided is **not** a problem, recorded as
data (layered-rules Phase 6). No algorithm can know it in advance. Examples:

- IRV writes `அந்த தேசம்` bare.
- `மோவாப்` is a name, so it is never a வல்லினம் target.
- A team may not want the clitic rule at all.

So Bridge learns house style from what translators decide, shows why each
entry exists, and lets anyone remove an entry.

The code is `engine/tc_ai_bridge/housestyle.py`. The decisions behind the
design are in `docs/DECISIONS.md`.

## An entry

An entry is a `human_decisions` row of kind `housestyle` (workbench schema
v5):

| Field | Values |
|---|---|
| `scope` | `word-in-book`, `word-in-project`, `rule-in-book`, `rule-in-project` |
| `ruleId` | the finding's pack-qualified rule id, e.g. `ta-irv/sandhi.vallinam.demonstrative` |
| `word` | the flagged text (NFC), for a word scope |
| `list` | `properNouns` for an approved name (Phase 6.2), or `projectWords` for a word the project says is spelt right (2026-10-07); neither carries a rule |
| `provenance` | `curated` (added in Settings), `explicit` (a scoped Ignore), `learned` (the learner) |
| `state` | `active`, `removed`, `undone` |
| `evidence` | the decisions it came from: `{chapter, verse, decisionId}` |
| `imported` | true until a local decision confirms an entry imported from another book |

**What an entry does:**
- A **word** entry with a rule hides that rule's findings whose text is that
  word.
- A **rule** entry hides the rule for the book.
- A **list** entry adds the word to `housestyle.properNouns`, which drives
  `name.minority-spelling`. The bundled வல்லினம் rules no longer abstain on
  it: the 2026-09-28 reviewer found a name after a case form needed doubling
  in 17 of 25 cases (அவனுக்குச் சேத் என்று பெயரிட்டான்).
- A **`projectWords`** entry (Language QA's "Add to the project word list",
  indic-qa's "Add to dictionary") hides every typo, name, consistency or
  learned-fix finding whose text is that word. Grammar and sandhi leads are
  not hidden: they are about a pair of words. The list is applied after the
  scan, like a word entry, so it is outside `list_fingerprint` and adding a
  word never rescans. It is never written into a dictionary file.
- A **learned preference** ranks a suggestion first (see below).

**What an entry may never do:** add or widen a match pattern, change a
severity, or draw a rule inline. That is the §31 line, "learning improves
ranking, never becomes an unconditional rule", applied to house style.

**A project scope** is written to every materialized book of the collection.
A book still lazy when the entry is made does not get it. Accept the
proposal again after opening that book.

**Remove and Undo** write a new `state` onto the same row. `change_log`
keeps every earlier state, and nothing is ever deleted. A pair that has
been undone or removed is never learned again. Recording it explicitly
still works.

**Where it is applied.** House style is applied when a pass assembles its
results, as decisions are, so changing it rescans nothing. The exception is
the proper-noun list: it feeds an abstain inside the verse scan, so it is
part of the per-chapter cache key, and changing it rescans what it can
affect.

A hidden finding still appears in the reports, as resolved and with
`houseStyleSuppressed`. The status reports `houseStyleSuppressed` per rule.

## Curated house style in the pack (2026-09-28 review)

Two rules the reviewer stated as the IRV project's house style are **curated
house style that lives in the `ta-irv` pack**, as abstains on every வல்லினம் rule.
They are not per-word entries, and a project does not need to record them:

| House style | Where it lives |
|---|---|
| **No doubling before தேவ- forms**: தேவன், தேவனுடைய, தேவனிடத்தில், தேவரீர், தேவசாயல் … (11 of 13 "case form, but no doubling here" verdicts). தேவை "need" is not a தேவ- form and is still checked. | abstain `{"next": {"prefix": ["தேவ"], "notPrefix": ["தேவை"]}}` |
| **Clitics:** கூட, மட்டும், போல, என்று, என, எனும் are written apart, without doubling. The clitic தான் and ஆவது are written fused (அதைத்தான், யாராவது). | the clitic abstain |
| **தான் (2026-09-29):** the reflexive *pronoun* தான் is written apart with doubling (தேவனுக்குத் தான்); the *clitic* is fused. | not abstained: a bare `X தான்` is flagged with both, the pronoun first (`contexts` in the வல்லினம் rules); `sandhi.clitic.fused` is disabled |

A project that disagrees narrows further with an override or an entry. It
cannot widen, so it cannot turn these off (DECISIONS.md, overrides only narrow).

**The bundled seed.** `language_packs/ta-irv/housestyle-seed.json` holds the
review's curated names. Today that is one entry, the proper noun சேத்து, from a
lexicon false alarm the reviewer marked "a name". Every Tamil project starts with
it:
- **Read-only.** It is merged into house style when read and never written into
  a project's workbench.
- **Visible.** Settings → House style shows it as **bundled**.
- **Removable.** Remove records the project's own entry with the same key, state
  `removed`, and an own entry always replaces a seed entry.

The seed never holds name abstains from the sandhi rows: a name does not block
doubling.

## The learner

The learner is a deterministic, visible aggregation over the book's
Language QA decisions. It is incremental: each decision recomputes only the
(rule, word) pair it touched.

| Constant | Value | What happens |
|---|---|---|
| `LEARN_IGNORES` | 3 | The same (rule, word) is ignored 3 times in a book with **no Use since**. A `word-in-book` entry is created **at once**, `provenance: learned`, with the three decisions as evidence. The UI shows "Learned: … — Undo". A false positive counts as an ignore. A Use resets the streak. |
| `PROPOSE_PROJECT_BOOKS` | 2 | The same pair is learned in 2 books. `word-in-project` is **proposed** in Settings (Accept or Dismiss). It is never applied automatically. |
| `PROPOSE_RULE_DECISIONS` | 20 | A rule has at least 20 decisions in the project… |
| `PROPOSE_RULE_IGNORE_RATE` | 0.8 | …and at least 80% of them are ignores. Disabling it project-wide (`rule-in-project`) is **proposed**, never applied automatically. |
| `PREFER_USES` | 3 | The same suggestion is Used 3 times for a word. That suggestion is ranked first for that word (`source: "housestyle"`). This is ranking only: it never adds a suggestion or changes a match. |

Proposals are computed when Settings opens, and again in the final stage of
a collection QA run. Dismissing a proposal records it as `removed`, so it is
not proposed again.

## The benchmark

`scripts/language_qa_benchmark.py --housestyle <project>` applies that
project's house style. What it hides is reported per rule as "suppressed by
house style (n)" and is **not counted** as a true or a false positive, so a
learned entry can never raise a rule's precision silently.

The option also exports the project's false-positive marks as
reviewer-labelled negatives
(`benchmark/results/<date>-reviewer-negatives.jsonl`). This fills the
benchmark's reserved "negative" class.

## Moving style between books

`housestyle.export` writes a book's active entries as JSON (Settings →
House style → Export…). `housestyle.import` reads such a file into another
book (Import…).

Imported entries keep their provenance and evidence, and show as
**imported** until someone confirms each one.

## The export ledger (6.5)

`export.aligned` and `export.nonAligned` also write
`<book>.language-qa-changes.csv` beside the export. It lists:
- every Scripture change a Language QA **Use** applied (chapter, verse,
  rule, original, replacement, time, user);
- every export made **over** the publication gate (`export.override`).

It is built from `change_log`, so a Use of a finding that was later decided
again is still listed.
