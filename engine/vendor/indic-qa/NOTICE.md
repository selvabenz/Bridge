# Vendored: indic-qa checker core

This directory holds files copied byte-for-byte from the `selvabenz/indic-qa`
repository. Bridge uses them to run Language QA for **Punjabi, Malayalam, Hindi
and Odia**, and, as a second layer of the `ta-irv` pack, for **Tamil**. The
maintainer took the scope and shape decisions on 2026-10-07; see
`docs/DECISIONS.md` and `docs/BUILD_LOG.md` for that date. The Tamil layer is
the later decision that day.

Do not edit these files. Adaptations belong in Bridge's adapter,
`engine/tc_ai_bridge/language_packs/indic_qa_adapter.py`. Every change here comes
from `scripts/sync_indic_qa.py`.

## Provenance

- **Source:** https://github.com/selvabenz/indic-qa
- **Pinned commit:** recorded in `VENDORED.json` (`248eb81`, 2026-10-05, at the
  first sync).
- **Fetched:** the `fetched` date in `VENDORED.json`.
- **Not on PyPI.** indic-qa is a FastAPI application, not a package.
- **Byte-exact.** Files are read from the commit with `git show`, never from a
  working tree, so line endings are upstream's. `.gitattributes` keeps them
  that way in this repository. `python scripts/sync_indic_qa.py --check`
  compares every file against its sha256 in `VENDORED.json`.

## What is here, and why

| File | Why Bridge needs it |
|---|---|
| `qa_app/checker.py` | `Lexicon`, `Checker` and the block JSON every rule reports through. It is pure stdlib: "independent of FastAPI". |
| `qa_app/kinds.py` | Language-neutral rule kinds the profiles call: sign sequences, confusion distance, consistency clusters, suffix stripping, bigram rules. |
| `qa_app/usfm_doc.py` | The `Line`, `Seg` and `Book` data model the checker walks. Bridge builds these objects itself from chapter JSON. **It never calls this module's parser** (CLAUDE.md gotcha 14). |
| `qa_app/langs/{hi,ml,pa,odia}.py` and their `*_tables.py` | The four language profiles: rules, tables, hooks. Odia's module is `odia.py` because `or` is a Python keyword; `langs.MODULE` maps the code. |
| `qa_app/langs/__init__.py` | The `Language` dataclass and the profile registry. |
| `qa_app/langs/ta.py`, `qa_app/tamil_grammar.py` | Imported unconditionally by `checker.py`. They also run the Tamil layer of Bridge's own `ta-irv` pack (pack.json `indicQa`). Bridge's catalogue for that layer is `tc_ai_bridge/language_packs/indic_qa_tamil.py`, because the Tamil profile has no `RULES`. |
| `qa_app/paths.py` | Imported by the profiles for default folders. Bridge always passes the dictionary folder explicitly, so the defaults are never used. |
| `scripts/build_dictionary.py` | `usfm_doc.py` puts `<this directory>/scripts` first on `sys.path` and imports `build_dictionary` at import time, for the tokenizer, Tamil constants and `deletions`. Keeping it at that relative path satisfies the import with no edit. Its build code never runs in Bridge. |
| `LICENSE` | Upstream's licence: MIT, (c) 2019 The Free Bible Foundation. |

The dictionaries are not here. They are pack data in
`engine/language_packs/<code>-irv/dictionary/`, shipped outside the onefile exe
through Tauri `bundle.resources` (`docs/LANGUAGE_QA_PACKS.md`). Each pack's
`dictionary/MANIFEST.json` holds their hashes. Tamil's comes from upstream's
`dictionary/`, not `dictionary_ta/`.

## Deliberately not vendored

- **The application:** `server.py`, `store.py`, `corpus.py`, `scope.py`,
  `exports.py`, `static/` and the launchers. Bridge has its own UI, its own
  decisions and house style, and one Scripture writer. indic-qa's editor writes
  corrections straight into `.SFM` files, and none of that path is brought over.
- **`related.py` and every non-Tamil `verses.tsv`** (10–13 MB per language).
  Only the OV↔IRV related-word lookup reads them, and Bridge does not offer that
  lookup. Tamil's `verses.tsv` *is* taken. Bridge's own `ov_reference.py` reads
  it to show the OV verse beside a Tamil layer finding.
- **`usfm/`, `docs/` and `tests/`.** Also every other
  `scripts/*.py` (dictionary builds, workbooks, measurements), and the build
  reports nothing reads at runtime: `REPORT.md`, `malformed_words.tsv`,
  `suspect_words.tsv`, `archaic_map.tsv`, `sandhi.tsv`, `honorific.tsv` and
  `concordance.json`.

## Contracts Bridge relies on

1. **`sys.path`.** The adapter puts this directory on `sys.path`, under
   `sys._MEIPASS` in a frozen build, the same way `tc_ai_bridge/versification.py`
   does. Importing `qa_app.usfm_doc` then adds `scripts/` here as well.
   Bridge's pytest `pythonpath` also includes the repository's own `scripts/`,
   so **a Bridge `scripts/build_dictionary.py` must never exist**: it would
   shadow this one.
2. **Read-only dictionaries.** `Lexicon.add_words`,
   `append_extra_words_file` and `remove_from_extra_words_file` are never
   called. A translator's "this word is fine" is a Bridge house-style entry, not
   a dictionary write.
3. **Encoding.** Every file read in the vendored checker passes
   `encoding="utf-8"` (or `utf-8-sig`). This was checked with grep at the first
   sync; recheck it on every sync (CLAUDE.md gotcha 5).

## Open question

**Licence of the wordlists.** The code is MIT. The Punjabi, Malayalam, Hindi
and Odia dictionaries are derived from Bible Society Old Version texts. The IRV
counts in `words.tsv` and `clusters.tsv` are derived from the IRV. indic-qa does
not state the terms of either: its `build_info.json` records only local folder
names. Tamil's 1957 OV is described as public domain in India; nothing is
stated for the others. Answer this before any installer that ships these
dictionaries is published.

## Integration findings

Add each real problem found while integrating to this list, with the date.

- 2026-10-07: copying from a Windows working tree with `core.autocrlf=true`
  rewrote every file to CRLF. The sync now reads committed blobs.
- 2026-10-07: the Tamil profile keeps its IRV ஒற்று habits (`book_pairs`,
  `book_form_use`) outside `Checker.data`. A snapshot of `book_count` and `data`
  alone silently drops the IRV veto and the dangling check's name test. The
  snapshot now carries both for a sandhi profile, and restore rebuilds the
  IRV-wide sums the way `_index_pairs` does.
- 2026-10-07: `tamil_grammar.load_rules` *writes* a default `sandhi_rules.tsv`
  when the file is missing. Bridge ships the file, so the dictionary folder is
  never written (contract 2). Keep `sandhi_rules.tsv` on the sync allow-list.
