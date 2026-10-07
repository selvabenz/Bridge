# Language QA packs

How Language QA is split between engine code and per-language data, and how a
language is added without writing code. The rule-file schema itself is in
`LANGUAGE_QA_RULE_PACK.md`; the Tamil rules are described in
`LANGUAGE_QA_TAMIL_SPECIFICATION.md`.

## Three tiers

| Tier | What it is | Where |
|---|---|---|
| core | Language-neutral engine: tokenizer, USFM lifting, raw offsets, findings, decisions, the common rules, the book pass, the benchmark runner and gate | `tc_ai_bridge/language_qa.py`, `language_qa_jobs.py`, `language_qa_benchmark.py`, `language_packs/loader.py`, `language_packs/tokens.py` |
| indic | **Rule kinds**, each written once and driven by a pack's data, each tested on synthetic input in two scripts | `tc_ai_bridge/language_packs/indic/` (`kinds.py`, `confusion.py`); tests in `engine/tests/language_packs/test_kinds.py` |
| packs | **Data only**: `pack.json`, `rules/*.json` with embedded examples, tables, a gzip lexicon, a house-style seed | `engine/language_packs/<pack>/` |

A pack never ships Python. A rule names its kind in `match.type`. If a
language needs something no kind covers, the answer is a new generic kind in
`indic/` with its own two-script tests, usable by every language. It is never
code inside a pack.

## Rule kinds

| `match.type` | Stage | Data it takes |
|---|---|---|
| `token-context` | word pair | `prev` / `next` conditions, `link` (see `LANGUAGE_QA_RULE_PACK.md`) |
| `regex` | verse | `pattern`, `on` (visible or raw) |
| `sign-sequence` | verse | `bases`, `signs`: a dependent sign must follow a base letter |
| `mixed-script` | verse | `scripts`: two or more Unicode script names inside one word |
| `reduplication-allowlist` | word pair | `allow`: repeats that are grammatical |
| `lexicon-lookup` | pair (`check: known-split`) or book (`known-misspelling`, `rare-near-common`) | the pack's lexicon; `rare-near-common` also uses the pack's confusion set and thresholds |
| `wordlist-variant` | book | thresholds; runs only when the pack has no lexicon |

Order inside a scan: the common rules first. Then, for each whitespace-separated
word pair, the pair kinds followed by the token-context rules. Then the verse
kinds in pack order, and after every verse the book kinds.

A **confusion set** (`confusion.json`) is the weighted edit distance that
`rare-near-common` and the house-style name check rank by. Its entry types are
`base`, `independent`, `sign`, `toggle`, `split` and `equivalent`, with a cost
on each (`indic/confusion.py`). An `equivalent` pair costs 0. It is folded
before a lookup and never proposed as a correction.

## Which pack a project gets

`engine/language_packs/index.json` is the registry:

```json
{"languages": {"ta": {"pack": "ta-irv", "name": "Tamil"}},
 "aliases": {"tam": "ta", "mal": "ml", "hin": "hi"}}
```

`detect_language` in `language_qa.py` decides the pack for a project:

1. **Declared metadata first.** The declared language (`manifest.target_language.id`)
   is folded through `aliases` and looked up. Its pack runs when the text is in
   that language's script, so `ta-Latn` never gets `ta-irv`.
2. **Script as fallback.** With nothing declared, the dominant script (at least
   20 letters, at least 80 %) suggests a pack only when exactly one language is
   written in that script (Tamil, Malayalam). Devanagari and Bengali are never
   guessed.
3. **Conflict.** When the declared language and the script disagree, or the
   script is mixed, only the common checks run, and the panel message names both.

On top of that, the project setting `language_qa.pack` is `auto` (the steps
above), `off` (common checks only) or a pack name (that pack, because a person
chose it). `language_qa_jobs.resolve_language` applies it. The resolved pack
name is part of every chapter's cache key.

There is one pack per project. A multi-book collection is one project per
book, so each book resolves its own pack.

## Where packs live at runtime

Packs are **outside** the frozen engine: a onefile archive re-extracts every
data file on every launch. `scripts/build-sidecars.ps1` copies
`engine/language_packs` to `src-tauri/language_packs`, `tauri.conf.json`
ships it through `bundle.resources`, and `sidecar.rs` passes
`--language-packs-dir`, which `main.py` puts in `BRIDGE_LANGUAGE_PACKS_DIR`. A
source checkout reads `engine/language_packs` (`registry.packs_dir()`).

A pack is loaded lazily, on the first Language QA pass that needs it, and its
examples run at load. Its lexicon (`lexicon.json.gz`) is loaded on that same
first pass, never at startup.

## Rules moved out of the engine's code

Some rules were moved from code into a pack. A moved rule keeps its identity:
`legacyId` keeps the finding-id name, and `legacyVersion` keeps the version
stamp (`packVersion`, `ruleVersion`) its findings carried in code. That way a
decision made before the move still applies, and the human benchmark's
population stays current. When such a rule's matching first changes, drop
`legacyVersion` and bump `version`.

The rules moved on 2026-09-30 are all in ta-irv: `tamil.dependent-sign`,
`tamil.mixed-word`, `tamil.repeated-word`, `tamil.wordlist-variant`,
`lexicon.known-split`, `lexicon.known-misspelling` and
`lexicon.rare-near-common`.

## Adding a language

1. Make a folder `engine/language_packs/<lang>-irv/` containing `pack.json`
   and `rules/*.json`. Add the optional tables (`confusion.json`, a lexicon,
   `housestyle-seed.json`) if the rules need them.
2. Add one line under `languages` in `index.json`.
3. Run `pytest tests/language_packs -m packs`. `test_pack[<pack>]` loads the
   pack, runs every embedded example, and checks that the registry and the
   shipped folders agree.

No engine code changes. New rules ship `inline: false`. A rule is drawn inline
only after the human-label gate (`LANGUAGE_QA_BENCHMARK.md`).

## Tests

- **Kinds:** generic tests in `tests/language_packs/test_kinds.py`, on
  synthetic Malayalam and Devanagari packs.
- **Packs:** one parametrized `test_pack[<pack>]`, with the `packs` marker. The
  inner loop is `pytest -m "not packs"`.
- **Registry:** `test_registry.py`.

Tamil behaviour is still pinned by the existing `tests/service` files and by
the human benchmark.

## Profile packs: indic-qa's checker (`"engine": "indic-qa"`)

Punjabi, Malayalam, Hindi and Odia do not use JSON rules. Their packs run
indic-qa's own checker (`selvabenz/indic-qa`). It is vendored in-process under
`engine/vendor/indic-qa/`; see its `NOTICE.md` and DECISIONS 2026-10-07. This
is the one exception to "a pack never ships Python". The code is upstream's,
byte-exact and pinned, and the pack folder still holds only data.

| File in `engine/language_packs/<code>-irv/` | What it is | Written by |
|---|---|---|
| `pack.json` | `"engine": "indic-qa"`, `"profile": "<code>"`, `"rules": []` | `scripts/build_indic_qa_packs.py --rule-versions`, once |
| `rule_versions.json` | One entry per rule of the profile's `RULES`: category, layer, severity, confidence, `enabled`, `inline`, `revision` | the same script adds new rules; reviewed values are edited by hand |
| `dictionary/` | The OV-built dictionary the profile reads, and `MANIFEST.json` with its hashes | `scripts/sync_indic_qa.py` |
| `irv_state.json.gz` | The checker's index of the whole IRV | `scripts/build_indic_qa_packs.py --irv-state <code> <IRV folder>` |

### How it fits the engine

- **It is an ordinary `RulePack`.** Overrides, version stamps, ignore expiry
  and the inline list all work unchanged. Every rule has
  `match_type="indic-qa"` and `stage="book"`, so no per-verse kind runs it.
- **Findings come from one book step.** It runs after the chapter loop, next
  to the lexicon audit (`indic_qa_adapter.profile_findings`). Findings are
  recomputed each pass and never written to `language_qa_cache`, because
  they depend on the whole book.
- **Bridge's chapter JSON becomes indic-qa's `Line`/`Book` objects.**
  - There is one line per physical line of a verse's visible text, so
    adjacency never crosses a poetry line.
  - There is one run per stretch the lifted markup left contiguous, matching
    indic-qa's runs. Malayalam's grouped encoding fix works per run.
  - indic-qa's own USFM parser is never called.
- **The IRV snapshot.** indic-qa indexes all 66 books at start-up; Bridge has
  one book open. The snapshot restores that index, keyed by Bridge's
  lower-case book id. Each pass re-indexes the open book from its live text,
  which replaces that book's share. The clusters are rebuilt only when the
  book's word counts changed.
- **One checker is resident at a time.** Opening another language releases
  it.

### Mapping

| indic-qa item | Bridge `rule` | Suggestions |
|---|---|---|
| A token with a status of `malformed`, `suspect`, `archaic`, `rare_near_common` or `unknown` | `SHAPE_RULE_SWITCH` maps a sub-rule to its catalogue rule. The sub-rule is kept as `detailRule`. | Its ranked `sugg` |
| A lead (consistency, agreement, grammar) | The lead's `rule` | `proposed` |
| A warning | Its `rule`, or `GENERIC_WARNING_RULE[kind]` | `fix` |

`ok`, `irv_ok`, `inflected_ok`, `compound` and `learned` tokens are not
findings. Two categories exist for these packs only: `consistency` and
`grammar`.

### Phase 1 policy (DECISIONS 2026-10-07)

- Every rule is panel-only (`inline: false`). The human gate needs labels.
- No rule has high severity together with high confidence, so none blocks
  export. The loader refuses such an entry.
- `<code>.lex.unknown` is off: a word that is not in the dictionary is not a
  finding.
- The coverage statement lists the pack's own categories. "Agreement" is
  replaced by "agreement across a whole clause", because corpus-attested pairs
  are flagged.

### Measured against indic-qa itself

`scripts/measure_indic_qa.py <code> <IRV folder> <BOOK>` runs indic-qa the
way its editor does, then a real `LanguageQaManager` pass. It compares the
verse-text findings as a multiset of (chapter, verse, rule, text). The
reference counts verse text only, like Bridge. Results for 2026-10-07 are in
BUILD_LOG.

### Labels and the inline gate

`scripts/import_indic_qa_labels.py` reads indic-qa reviewer workbooks into
`benchmark/human/<code>/<round>/`. These folders sit one level below the
Tamil rounds, because a book id alone cannot tell Hindi Genesis from Tamil
Genesis. The importer works in this order:

1. Each row is anchored in the verse as Bridge stores it.
2. It is moved onto the finding Bridge reports there for that rule.
3. A row inside a note, under a rule that is off in Bridge, or with no Bridge
   finding at that place is reported, never written.

```
python scripts/language_qa_benchmark.py --human-labels benchmark/human/hi --language hin --gate
```

This scores a language's rounds with its own pack. It gates only that pack's
rules, because the Tamil rounds gate the common rules, and CI runs it for
`hi` and `ml`. A rule becomes inline only through a reviewed `inline: true`
in `rule_versions.json` with a DECISIONS line, once it has ≥ 20 labels at
≥ 0.90.

### Updating

1. `sync_indic_qa.py --source <clone> --commit <sha>` to re-vendor.
2. Bump the `revision` of any rule whose matching changed. The script names
   the profiles that changed.
3. `build_indic_qa_packs.py --rule-versions` to add new rules.
4. Rebuild each `irv_state.json.gz`.
   `test_each_snapshot_is_for_the_vendored_dictionary` fails until you do.
5. Run `measure_indic_qa.py`.

## Pack layers: indic-qa's Tamil checker inside ta-irv (`indicQa`)

ta-irv keeps its JSON rules. Since DECISIONS 2026-10-07 ("indic-qa's Tamil
checker runs as a layer of ta-irv"), its `pack.json` also carries:

```json
"indicQa": {"profile": "ta", "dictionary": "dictionary", "rules": "indic_qa_rules.json"}
```

`load_pack` then appends the layer's 17 `indicqa.*` rules to the pack
(`indic_qa_adapter.layer_rules`). They are `match_type="indic-qa"` and
`stage="book"`, the same as a profile pack's. The catalogue is
`tc_ai_bridge/language_packs/indic_qa_tamil.py`, because indic-qa's Tamil
profile has no `RULES`. Bridge's view of each rule is
`ta-irv/indic_qa_rules.json`, which has the same shape as `rule_versions.json`.
If the dictionary or the vendored checker is missing, the pack loads without
the layer and says so in its problems.

| File in `engine/language_packs/ta-irv/` | What it is | Written by |
|---|---|---|
| `dictionary/` | The BSI 1957 OV dictionary: wordlists, `words.tsv`, `sandhi_pairs.tsv`, `final_consonant_words.tsv`, the reviewer's `corrections.tsv` and `sandhi_rules.tsv`, `verses.tsv`, and `MANIFEST.json` | `scripts/sync_indic_qa.py` |
| `indic_qa_rules.json` | One entry per layer rule | `scripts/build_indic_qa_packs.py --ta-layer` adds new rules; reviewed values are edited by hand |
| `irv_state.json.gz` | The whole-IRV index, including each book's ஒற்று pair and form counts and its headings and footnotes | `scripts/build_indic_qa_packs.py --irv-state ta <IRV folder>` |

What the layer does differently from a profile pack:

- **It also reads headings and footnote prose.**
  - **Headings.** `<chapter>.headings.json` headings become lines before the
    verse they introduce. `\r`, `\mr` and `\sr` are references and are not
    checked.
  - **Footnotes.** Each footnote part (`\ft`, `\fq`, `\fqa`, `\fk`, ...)
    becomes its own stream on its verse's first line. Its raw offset is found
    inside the note's range from `usfm_verse.lift_verse`.
  - **What a finding carries.** A heading finding carries
    `context: "heading"` and `contextText`, offers no suggestion, and its
    offsets index the heading. A footnote finding carries
    `context: "footnote"` and raw verse offsets.
- **It never repeats Bridge.** The pass drops a layer finding when a raw
  finding of the pack or the common rules on the same verse has the same
  category and an overlapping span (`language_qa_jobs._already_flagged`). The
  checks Bridge runs over verse text are reported by the layer only in
  headings and footnotes.
- **It shows the OV.** Each finding carries `reference`, the OV verse with
  the same number (`ov_reference.py`, reading `dictionary/verses.tsv`). Its
  message gives the OV and IRV counts behind it.
- **Unknown words go through a rarity gate.** The rule depends on how often
  the IRV uses the word:
  - If the IRV uses it more than twice, it is house practice.
  - A rare word whose best OV suggestion is a typing slip (ல/ள/ழ, ன/ண/ந,
    ர/ற, vowel length, புள்ளி, transposition) or a reviewed correction is
    `indicqa.lex.near-miss`.
  - A rare word that splits into two known words is `indicqa.lex.compound`.
  - Every other unknown word is `indicqa.lex.unknown`, which is off.

Re-vendoring: steps 1–4 of "Updating" above, plus
`build_indic_qa_packs.py --ta-layer` and `--irv-state ta`. If a change to
`langs/ta.py`, `tamil_grammar.py` or `checker.py` alters matching, bump the
affected `revision`s in `indic_qa_rules.json`.
