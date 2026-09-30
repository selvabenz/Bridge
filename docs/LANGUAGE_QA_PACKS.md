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
