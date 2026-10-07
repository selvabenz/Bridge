# Greek Room: module map, packaging, and verified behaviour

Point-in-time record. Verified 2026-09-15 against the `greekroom` 0.1.5 wheel on
Python 3.12.10, Windows 11, default `cp1252` locale. Written for anyone embedding
Greek Room in a Python app (Bridge or otherwise) who needs to know which parts are
a `pip install` and which must be pulled from source. Re-verify before relying on
it: upstream moved three times in the eight days before this was written.

**The headline.** Bridge's own notes (`CLAUDE.md`, `engine/pyproject.toml`, the
three `engine/vendor/*/NOTICE.md` files, `BUILD_LOG.md`) say only `owl` and
`gr_utilities` are on PyPI. That was true at the pinned commit
(`18ddcf0e6c03fa2774b73b21186115d712e4cba9`, August 2026) and is stale now. The
`greekroom` package went from 0.0.20 to 0.1.3 on 2026-09-04 and 0.1.5 on
2026-09-12, and the wheel now bundles Wildebeest, the USFM checker and
versification. Everything below was checked by downloading the wheel, listing it,
installing it and calling into it, not by reading the README.

## 1. What is in the upstream repository

`https://github.com/BibleNLP/greek-room`, repo-root `LICENSE` is BSD-3-Clause.

| Path | What it is | Packaged? |
|---|---|---|
| `greekroom/` | The pip package (`greekroom/greekroom/` is the import root). Built with `uv_build`; its `pyproject.toml` excludes `greekroom/ualign` and `greekroom/wildebeest/test2`. | Yes, `pip install greekroom` |
| `smart_edit_distance/` | `src/smart_edit_distance.py` (~430 lines, stdlib only) plus two cost-rule data files. | No |
| `utilities/` | `ualign.py` (200 KB), `parallel-corpus-prep.py`, `parallel-corpus-to-triple-pipe-format.py`, `filter-viz-snt-align.py`. | No |
| `ephesus/`, `web/`, `cli/`, `core/`, `instance/` | A Flask web application and its blueprints. | No, and not a library |
| `site/`, `html/` | Hugo documentation site and HTML output. | No |

Inside the wheel (0.1.5) the subpackages are:

| Subpackage | `__init__.py` | `.py` files | Notes |
|---|---|---|---|
| `wildebeest` | yes | 6 | `wb_check`, `wb_analysis`, `wb_normalize`, `wb_pprint_html` + 13 data files |
| `owl` | yes | 3 | `repeated_words` + `legitimate_duplicates.jsonl` |
| `gr_utilities` | yes | 8 | `wb_file_props`, `general_util`, `corpus`, `script_direction`, `html_util`, `color_diff` |
| `usfm` | **no** | 6 | `usfm_check`, `ualign_utilities`, `gruv-prep*`, `cleanup_filenames` + USFM tag data |
| `versification` | **no** | 6 | `versification`, `verse_inspection`, `*_diff_*`, `reversification_using_jc_tools` + `standard_mappings/*.json`, `vref.txt`, `psalm-descriptive-titles.txt` |
| `utoken` | no | **0** | Data files only (`tok-resource-*.txt` for ~70 languages). No code. |

Package metadata: Apache-2.0 classifier (conflicts with the repo-root BSD-3-Clause
`LICENSE`; upstream has not reconciled this), `requires-python >=3.11`,
dependencies `regex>=2025.7.34`, `tqdm>=4.70.0`, `unicodeblock>=0.3.1`,
`uroman>=1.3.1.1`, `wheel>=0.45.1`. Console scripts: `gr-wb-check`,
`gr-repeated-words`, `gr-wb-file-props`, `gr-test-wb-file-props`.

## 2. Module by module

### Wildebeest (`greekroom.wildebeest`)

Character-level checks: encoding damage, ligatures, non-canonical Unicode,
invisible characters, punctuation spacing, look-alike characters, plus a
normalizer (`wb_normalize.Wildebeest`). **Pip-installable and this is the version
to use.** The entry point is `wb_check.check(request: dict, text_corpus=None) ->
dict`, where the request is JSON-RPC shaped:

```python
import greekroom.wildebeest.wb_check as wb_c
req = {"jsonrpc": "2.0", "id": "t1", "method": "BibleTranslationCheck",
       "params": [{"checks": ["GreekRoom:Wildebeest"],
                   "corpus": {"langCode": "eng",
                              "body": [{"sntId": "GEN 1:1", "text": "In the beginning,God created the ﬁrst"}]}}]}
resp = wb_c.check(req)
```

Verified output shape: `resp["result"]` is a list of findings, each with `sntId`,
`span` (a list of `[start, end]` pairs), `orig`, `check` (a dotted id such as
`GreekRoom:Wildebeest:encoding:ligature`), `severity` (float) and an `actionMenu`
of `{substitute, confidence}` entries. `resp["version"]` reported
`GreekRoomWildebeest: 0.11.5`. A clean Tamil verse produced no findings. Note the
zero-width space in the test input was not flagged by this call; the older
`wb_analysis.process()` path, which Bridge's adapter uses, does report
`block.ZERO_WIDTH`, so the two entry points do not cover identical ground.

The standalone `wildebeest-nlp` 0.9.2 PyPI package (2022, the one Bridge's
`wildebeest` extra pins) is superseded. It cannot compile on Python 3.13 because a
docstring contains a literal `\uDC80-\uDCFF` escape. In the 0.1.5 wheel those two
docstrings (`wb_normalize.py` around lines 801 and 817) are raw strings, which is
exactly the fix. Only 3.12 was available here, so 3.13 was not run; the parse on
3.12 produced no warnings.

### OWL (`greekroom.owl.repeated_words`)

Repeated-word detection ("the the") with a per-language list of legitimate
duplicates. **Pip-installable.** API:

```python
from greekroom.owl import repeated_words as rw
mcp, misc, check_corpus = rw.check_mcp(task_json_str, rw.load_data_filename(), rw.new_corpus("id"))
```

`task_json_str` is the same JSON-RPC request as Wildebeest, serialised to a
string, with `"checks": ["GreekRoom:owl:repeated_words"]`. Verified output:
findings with `span`, `orig`, `repeatedWord`, `legitimate: false`, `severity 0.5`,
`check: GreekRoom:Owl:RepeatedWords`.

**Windows caveat, reproduced.** `read_legitimate_duplicate_data` (line 56) opens
`legitimate_duplicates.jsonl` with a bare `open()`, so under the default `cp1252`
codepage it fails with `UnicodeDecodeError: 'charmap' codec can't decode byte
0x81`. Running the interpreter in UTF-8 mode fixes it:

```
set PYTHONUTF8=1
```

This is the same bug class Bridge found in `usfm_check.py`, `versification.py`
and `smart_edit_distance.py`; see gotcha 5 in `CLAUDE.md`.

### gr_utilities (`greekroom.gr_utilities`)

Support code: `wb_file_props.script_props(filename=None, text=None, lang_code=None,
lang_name=None)` for script direction and quotation style, `general_util`
(the `Corpus` class and helpers that `usfm_check` and `versification` import),
`script_direction`, HTML helpers. **Pip-installable**, imports cleanly. Three bare
`open()` calls in `general_util.py` on file-loading paths; not hit when data is
passed in memory.

### USFM structural checker (`greekroom.usfm.usfm_check`)

Duplicate or missing verses, unclosed inline markers, tag misuse, with a report.
**In the wheel but not usable as a library import.** Line 21 is `from
ualign_utilities import ...` (flat, same-directory), while `versification.py`
does `from greekroom.usfm.ualign_utilities import ...` (packaged). Two different
import conventions from the same upstream; `import greekroom.usfm.usfm_check`
therefore fails with `ModuleNotFoundError: No module named 'ualign_utilities'`.
It imports if the `greekroom/usfm` directory is put on `sys.path` first, and runs
fine as a subprocess, which is how Bridge invokes its vendored copy.

Still true in 0.1.5: 3,997 lines, 22 top-level functions and classes, a `main()`
and no clean API; a `%-d`/`%-H` strftime at line 3795 that raises on Windows when
the report is written; 12 bare `open()` calls. Treat it as a CLI tool, not a
library. Bridge's vendored copy carries two annotated patches for the Windows
problems (`engine/vendor/greekroom-usfm/NOTICE.md`).

### Versification (`greekroom.versification.versification`)

Detects which verse-numbering scheme a text follows and maps references between
schemes (`BibleStructure`, `Versification`, `VersifiedCorpus`,
`VersificationMatch`, `BackVersification`). **In the wheel, imports cleanly.**
Two known traps carry over unchanged from the pinned commit:

- `Versification.versification_d` (line 176) is class-level state. A second
  `load_versifications()` call in the same process hits the duplicate-schema
  branch and returns a half-built object that crashes later. Load exactly once
  per process. Bridge guards this with a lock and flag in
  `engine/tc_ai_bridge/versification.py`.
- Eight bare `open()` calls on the file-loading paths (`load_corpus`,
  `write_corpus`, `main`, the mapping loaders). Build the corpus dicts in memory
  and never call those on Windows.

Also recorded in Bridge's NOTICE: `VersificationMatch.__init__` is a pure-Python
scan over tens of thousands of verse ids and suffers catastrophic GIL contention
if several run on threads at once (16 threads, ~47 s each instead of ~0.5 s).
Serialise it.

License: the code is BSD-3-Clause; the `standard_mappings/*.json` data is
CC BY-SA 4.0 from the Copenhagen Alliance Versification Working Group, re-hosted
by Greek Room. Attribution is required either way.

### utoken

Only the tokenizer's resource files are in the wheel; there is no code. The
separate `utoken` PyPI package is 0.1.8 from 2021 and pins `regex==2021.8.3`,
which is incompatible with `greekroom`'s `regex>=2025.7.34`. **Not usable
alongside `greekroom` without a fork.** Bridge does not use it.

### uroman

Universal romanizer by the same author. **A healthy PyPI package**
(`uroman>=1.3.1.1`), a hard dependency of `greekroom`, so it arrives
automatically. Bridge depends on it directly (`engine/pyproject.toml`). Its bundled
`LICENSE.txt` is a custom MIT-style license with a mandatory attribution clause,
despite the Apache classifier.

### Smart Edit Distance (`smart_edit_distance/` at repo root)

Phonetic-aware string distance used for name and transliteration consistency
(Bridge's `NamesAdapter`). **Not in any package; vendor it.** One ~430-line file
with no third-party imports plus `string-distance-cost-rules.txt` and a Devanagari
supplement. `load_smart_edit_distance_data()` does a bare `open()` when given a
string path; open the cost file yourself with `encoding="utf-8"` and pass the
file object. Bridge's copy: `engine/vendor/greekroom-smart-edit-distance/`.

### UAlign (`utilities/ualign.py`)

Word-alignment statistics (co-occurrence, fertility, PMI, SED-boosted translation
probability), a spell-checker, morphology-variant checking and an HTML
visualizer. **Explicitly excluded from the wheel** by upstream's own
`pyproject.toml`. It expects fast_align-style Pharaoh alignment files and
`e ||| f ||| ref` parallel text, and is 200 KB of script. Bridge reimplemented
the statistics it needed against its own data (`alignment_statistics.py`) rather
than vendor it; see `BUILD_LOG.md`, Phase 6. Make the same call unless you already
have that file pipeline.

## 3. Summary for an embedding app

| Module | How to get it | Status (Windows, Python 3.12, verified) |
|---|---|---|
| Wildebeest | `pip install greekroom` | Works |
| OWL | `pip install greekroom` | Works with `PYTHONUTF8=1` |
| gr_utilities | `pip install greekroom` | Works |
| Versification | `pip install greekroom` | Imports; load once per process, avoid its file loaders |
| USFM checker | `pip install greekroom`, run as subprocess | Not importable as `greekroom.usfm.usfm_check` |
| uroman | pulled in by `greekroom` | Works |
| Smart Edit Distance | vendor from GitHub | One file plus two data files |
| UAlign | vendor or reimplement | Heavy; excluded from the wheel by upstream |
| utoken | avoid | Wheel has data only; the PyPI package conflicts on `regex` |
| `wildebeest-nlp` | avoid | Superseded by `greekroom.wildebeest`; broken on 3.13 |

Two cross-cutting rules, regardless of module: run Python in UTF-8 mode on Windows
(or pass file objects you opened yourself), and pass data in memory wherever a
function accepts it, because every file-loading path in this codebase has at
least one bare `open()`.

## 4. What this means for Bridge

Bridge vendors `usfm/`, `versification/` and `smart_edit_distance/` from the pinned
August commit and installs Wildebeest from `wildebeest-nlp`. As of 0.1.5 three of
those four are now on PyPI, and upstream has moved since the pin:

| File | Vendored | 0.1.5 wheel | Diff |
|---|---|---|---|
| `usfm_check.py` | 4,005 lines | 3,997 | 25 removed / 17 added (includes Bridge's two patches) |
| `ualign_utilities.py` | 641 | 641 | identical |
| `versification.py` | 836 | 966 | 20 removed / 150 added |
| `general_util.py` | 230 | 255 | 4 removed / 29 added |

Replacing the vendor trees with the package is an architecture decision for an
issue, not something to do in passing: the Windows bugs are still in the package,
`usfm_check` still cannot be imported as a module, and the PyInstaller specs
(`bridge-engine.spec`, `bridge-usfm-checker.spec`) would need re-verification
against the frozen pair. Until that issue exists, the vendor NOTICE files remain
the operative record and their "not on PyPI" sentences should be read as "not on
PyPI at the pinned commit".

## 5. How this was verified

- `pip download greekroom==0.1.5 --no-deps` and `==0.0.20`; wheel contents listed
  with `zipfile`.
- `pip install --target` of 0.1.5 into a scratch directory; every subpackage
  imported under Python 3.12.10.
- `wb_check.check()` and `repeated_words.check_mcp()` called with real JSON-RPC
  requests (English with a ligature and a zero-width space, one Tamil verse);
  outputs quoted above.
- Grep of the wheel for `%-d` strftime, bare `open()`, the `ualign_utilities`
  import, class-level `versification_d`, and `\uDC80` docstring escapes.
- `difflib` of the wheel's files against Bridge's vendored copies.
- PyPI JSON API for `greekroom`, `wildebeest-nlp`, `utoken`; GitHub contents API
  for the repo tree, `greekroom/greekroom/`, `utilities/`, `smart_edit_distance/`.

Not verified: Python 3.13 (not installed on the machine used), macOS/Linux, the
frozen PyInstaller build with the new package, and `wb_analysis.process()` from the
0.1.5 wheel (Bridge's adapter was written against `wildebeest-nlp` 0.9.2's version
of it).
