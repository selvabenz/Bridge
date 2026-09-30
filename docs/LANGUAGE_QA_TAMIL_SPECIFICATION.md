# Tamil publication QA: the 56-item framework, owned

This is Bridge's working standard for **publication-worthy Tamil Scripture** (the
IRV project first). It lays out 56 proofreading items in six gates. For each item
it says whether Bridge checks it, and with which rule, and who owns the item when
Bridge does not.

The framework began as an outside methodology review of the IRV proofreading
workflow (the Joel Round 2 master workflow, Paratext's publication guidance,
USFM, Unicode and W3C Tamil material, and Tamil Virtual Academy grammar). That
review was pasted here verbatim in #169. This version replaces it:

- the six gates and 56 items are kept;
- each item gains a Status and an Owner;
- Bridge's pack ids are added;
- the prose is condensed;
- the tracking links are removed. The sources are listed once, at the end.

**Maintainer of this page:** @RevantCI. Change a Status only in the commit that
changes the behaviour, and cite the rule id.

Related:

- `docs/LANGUAGE_QA_RULE_PACK.md`: the rules and their schema.
- `docs/LANGUAGE_QA_BENCHMARK.md`: how well each rule does against the IRV
  reviews.
- `docs/LANGUAGE_QA_PLAN.md`: the phase table.

## Status and Owner

**Status**

| Value | Meaning |
|---|---|
| **Checked** | A Bridge rule finds this and reports it as a finding. The pack id is given. The rule may still miss cases; see the benchmark for recall. |
| **Partial** | A Bridge rule covers one named shape of this item and nothing else. The rest is the Owner's. |
| **Assisted** | Bridge shows the evidence (alignment, source coverage, terms) but does not judge the item. A person decides. |
| **Planned (Pn)** | Scheduled in phase *n* of the layered-rules plan. Not built yet. |
| **Not checked** | Outside what an offline text checker can decide. Owned by a person, and listed so that the boundary is explicit (Phase 7 shows it in the panel). |

**Owner** is who answers for the item at publication. That is Bridge only where
the Status is Checked, and even then a person acts on each finding. The owners
are: *Translator* (the translation team), *Proofreader* (Round 2 linguistic
proofreading), *Consultant* (exegetical and consultant check), *Typesetter*,
*Publisher* (release master and final acceptance), and *Bridge*.

Rule ids:

- `ta-irv/…` ids are pack rules (`engine/language_packs/ta-irv/`).
- `common/…` ids are language-neutral rules in `language_qa.py`.
- `wildebeest.*`, `usfm.*` and `names.*` are the Greek Room adapters.
- Stage 6B–8 are the semantic pipeline.

## Gate 1: Tamil language correctness

A verse does not move on while obvious Tamil errors remain.

| # | Item | What it covers | Status | Bridge rule / evidence | Owner |
|---|---|---|---|---|---|
| 1 | Spelling (எழுத்துப்பிழை) | Missing, extra or transposed letters. Wrong consonant or vowel sign, குறில்/நெடில், புள்ளி. Confusable pairs ர/ற, ல/ள/ழ, ந/ன/ண. Grantha use. | Partial | `ta-irv/lexicon.known-misspelling` (curated corrections; inline, 43 of 43 human-confirmed on 2026-09-28); `ta-irv/lexicon.rare-near-common` is **disabled** (0 of 21: every suggestion was another real word); `ta-irv/typo.divine-name.vowel-drop`, `ta-irv/typo.divine-name.dative-stem`, `ta-irv/typo.suffix.dropped-tha` (known IRV defect shapes). `ta-irv/tamil.repeated-word` is **disabled**: 0 of 20 in the 2026-09-28 review, all deliberate அடுக்குத்தொடர் (தங்கள் தங்கள், கொஞ்சம் கொஞ்சம்) | Proofreader |
| 2 | Word form and morphology | Noun and verb inflection, tense, finite/non-finite forms, participles, negative and honorific forms. | Not checked | The corpus lexicon flags a rare form near a common one. It does not judge grammar, and an unusual but correct inflection is a false positive to ignore. | Proofreader |
| 3 | சந்தி / புணர்ச்சி | Where வல்லினம் doubles (மிகும்) and where it does not (மிகா). Joining and splitting; suffix and case-marker joining. | Partial | `ta-irv/sandhi.vallinam.demonstrative`, `.manner-adverb`, `.accusative`, `.dative` (-க்கு and -ற்கு; missing link), `ta-irv/sandhi.vallinam.wrong-consonant`, `ta-irv/sandhi.compound.direction` (கிழக்குக் காற்று). Inline on human-labelled precision (2026-09-28 review): dative 100% and accusative 94.6%; the others are panel-only until a sample of 20 exists. No doubling before தேவ- forms (house style); a proper noun does not block doubling. Unnecessary வல்லினம் (மிகா இடம்) is **not checked**. | Proofreader |
| 4 | Word division | Wrongly split or wrongly joined units: compounds, postpositions, clitics, name + suffix. | Partial | `ta-irv/sandhi.clitic.fused` (தான் written apart; panel only). House style (2026-09-28 reviewer): தான் and ஆவது are written fused; கூட, மட்டும், போல, என்று, என, எனும் apart without doubling. A split word (சு வரை → சுவரை) is **not checked** yet: `word-joining.orphan-syllable` is scoped in LANGUAGE_QA_PLAN.md | Proofreader |
| 5 | வேற்றுமை / case suffixes | Missing, unnecessary or wrong case. Inconsistent case across coordinated nouns. | Not checked | The accusative and dative rules check only the sandhi after a case form, not the choice of case. | Proofreader |
| 6 | Number, person and gender agreement | Subject and verb, honorific singular, pronoun and antecedent, singular and plural against the source. | Not checked | | Proofreader, Consultant |
| 7 | Tamil syntax | Clause relationships, modifier attachment, subject and object reversal. | Not checked | | Proofreader |
| 8 | Sentence completeness | Incomplete sentences, unfinished quotations, clauses split across verses, duplicated clauses. | Assisted | Stage 8 source coverage shows omitted source content. It does not judge Tamil completeness. | Proofreader |
| 9 | Tamil punctuation | Duplicate punctuation, spacing around punctuation, unbalanced pairs, punctuation near notes and markers. | Partial | `common/punctuation.repeated`, `common/punctuation.space-before`, `ta-irv/integrity.space-before-note-end`. Unbalanced pairs and punctuation policy (comma versus full stop) are **not checked**. | Proofreader |
| 10 | Register and written Tamil | Deliberate, consistent register; accidental colloquial, regional or English-influenced forms. | Not checked | The house-style learner (`housestyle.py`) records what this project has decided is not a problem. It does not judge register. | Translator |

## Gate 2: Meaning and translation quality

| # | Item | What it covers | Status | Bridge rule / evidence | Owner |
|---|---|---|---|---|---|
| 11 | Verse-by-verse source fidelity | Omissions, additions, meaning shifts against the source. | Assisted | Stage 6B location, Stage 7 meaning preservation and Stage 8 bidirectional coverage, reviewed in the 9A queue. The confidence values are uncalibrated. | Consultant |
| 12 | Negation | A lost, added or reversed negative. | Assisted | Stage 7 POLARITY. **Known limitation on Tamil:** Tamil negatives are split by the comparator, and the check reports false contradictions. This is pinned by `test_tamil_negation_polarity_limit_is_pinned_not_worked_around` and is scheduled work. | Consultant |
| 13 | Pronoun and reference tracking | Who does what to whom; divine and human referents. | Not checked | | Consultant |
| 14 | Logical relationships | Cause, result, purpose, contrast, condition. | Not checked | | Consultant |
| 15 | Figures of speech | Idioms, metaphors, and whether a figure is kept or explained. | Not checked | tN notes are shown as resources. | Consultant |
| 16 | Implicit information | What is made explicit, and whether it is justified. | Not checked | | Consultant |
| 17 | Accuracy, clarity, naturalness, acceptability | The four translation-quality criteria. | Not checked | | Consultant, Translator |
| 18 | Comprehension testing | Community testing with target readers. | Not checked | | Translator |
| 19 | Read-aloud check | Reading aloud for flow, ambiguity and unintended sound. | Not checked | | Translator |

## Gate 3: Biblical consistency and consultant QA

| # | Item | What it covers | Status | Bridge rule / evidence | Owner |
|---|---|---|---|---|---|
| 20 | Biblical key terms | One rendering per key term, or deliberate variation. | Partial | `terminology.deprecated-form` (termbase v3: listed inflected forms, and case endings in prefix mode at medium confidence); tW resources shown | Consultant |
| 21 | Proper names | Consistent spelling of names across books; names with suffixes. | Partial | `names.spelling_similarity` (names adapter); `project/name.minority-spelling` against the approved `housestyle.properNouns` list. The vallinam rules no longer abstain on it: after a case form a name needed doubling in 17 of 25 reviewed cases | Translator |
| 22 | Parallel passages | Synoptic and quoted passages rendered consistently. | Not checked | | Consultant |
| 23 | Textual-basis review | Which source text and which variants the translation follows. | Not checked | | Consultant |
| 24 | Numbers, money, weights and measures | Consistent numerals and units, and the numeral policy. | Not checked | `ta-irv/integrity.digits-in-text` exists but is **disabled**: digits are an IRV house form (Pass 3 §5). | Translator |

## Gate 4: USFM and digital-text integrity

| # | Item | What it covers | Status | Bridge rule / evidence | Owner |
|---|---|---|---|---|---|
| 25 | Chapter and verse structure | Missing, duplicated or out-of-order verses; versification. | Checked | USFM structural checker (`usfm.*`); versification support | Bridge |
| 26 | USFM markers | Valid and closed markers, correct nesting. | Checked | USFM structural checker | Bridge |
| 27 | Footnotes | Note markup and the text inside notes. | Partial | USFM checker for structure; `ta-irv/integrity.space-before-note-end` (a space before `\f*`/`\x*`). Note content is **not checked**. | Proofreader |
| 28 | Cross-references | Reference markup and targets. | Partial | USFM checker for structure; target validity is **not checked**. | Proofreader |
| 29 | Words of Jesus | `\wj` coverage and boundaries. | Not checked | | Proofreader |
| 30 | OT/NT quotation markup | Quotation markers and their extent. | Not checked | | Proofreader |
| 31 | Headings and section titles | Presence, wording, consistency. | Not checked | Headings are imported and shown. | Translator |
| 32 | Paragraphing and discourse formatting | Paragraph breaks, discourse markers. | Not checked | | Typesetter |
| 33 | Poetry | Poetry markers and indentation levels. | Partial | USFM checker for markers. Line breaks inside poetry are treated as whitespace, not as control characters. | Typesetter |
| 34 | Special textual structures | Lists, genealogies, acrostics, letters. | Not checked | | Typesetter |
| 35 | File encoding | UTF-8 with no mixed or legacy encodings. | Checked | UTF-8 on every read and write; `common/unicode.corruption` (U+FFFD) | Bridge |
| 36 | Unicode normalization | NFC; canonical Tamil sequences. | Checked | `common/unicode.nfc`, `wildebeest.non_canonical` | Bridge |
| 37 | Grapheme integrity | Dependent signs with no base, malformed sequences, mixed scripts in a word. | Checked | `ta-irv/tamil.dependent-sign`, `ta-irv/tamil.mixed-word`, `wildebeest.script.mixed`, `common/unicode.private-use` | Bridge |
| 38 | Invisible characters and spaces | ZWJ/ZWNJ, NBSP, unusual or doubled spaces. | Checked | `common/unicode.invisible`, `common/spacing.unusual`, `common/spacing.extra`, `wildebeest.zero_width` | Bridge |

## Gate 5: Typesetting and visual publishing proof

Bridge has no typesetting engine. Every item in this gate is proofed on the PDF.

| # | Item | What it covers | Status | Owner |
|---|---|---|---|---|
| 39 | Tamil font rendering | Conjuncts, vowel signs and pulli render correctly in the chosen font. | Not checked | Typesetter |
| 40 | Line breaking | No breaks inside a grapheme cluster; acceptable break points. | Not checked | Typesetter |
| 41 | Hyphenation | Tamil hyphenation policy. | Not checked | Typesetter |
| 42 | Page-level composition | Widows, orphans, column balance, spacing. | Not checked | Typesetter |
| 43 | Running heads and page numbers | Correct book/chapter heads. | Not checked | Typesetter |
| 44 | Footnote composition | Note placement and callers. | Not checked | Typesetter |
| 45 | Cross-column reading order | Text flows in the right order across columns. | Not checked | Typesetter |
| 46 | Maps, captions and illustrations | Tamil labels, name spellings consistent with the text. | Not checked | Typesetter |
| 47 | Front and back matter | Title page, contents, glossary, maps index. | Not checked | Publisher |

## Gate 6: Final publication acceptance

| # | Item | What it covers | Status | Bridge rule / evidence | Owner |
|---|---|---|---|---|---|
| 48 | Run every automated check | All checks run and clean, or every finding decided. | Planned (P4) | Language QA as a check-framework stage, the collection runner and the publication gate | Publisher |
| 49 | Whole-Bible wordlist audit | Every word form reviewed across the Bible. | Not checked | `ta-irv/lexicon.rare-near-common`, which compared each book against the whole-corpus lexicon, is disabled (2026-09-28 review, 0 of 21). It needs a morphology filter first | Proofreader |
| 50 | Whole-Bible glossary audit | Glossary terms used consistently. | Partial | Termbase v3; the collection QA run's termbase coverage report (approved renderings never used, rejected ones still present, per book) | Consultant |
| 51 | Whole-Bible proper-name audit | One spelling per name across all books. | Partial | `names.spelling_similarity`; the collection run's cross-book names stage; `project/name.minority-spelling` | Translator |
| 52 | Final blind proofread | A fresh reader who has not seen earlier rounds. | Not checked | | Proofreader |
| 53 | Final consultant sign-off | The consultant's approval. | Not checked | | Consultant |
| 54 | Final community read-through | A community reading before release. | Not checked | | Translator |
| 55 | Regression check after every late correction | A late fix introduces no new error. | Partial | Language QA re-checks an edited verse at once. Stage 9B.3 re-runs affected analysis after a correction. The exception queue lists open high findings, and the export ledger lists every Language QA change. | Publisher |
| 56 | Source and version provenance | Which source files and which version were published. | Checked | `.bridge/import.json` SHA-256 provenance; `ruleVersion`/`packVersion` on every finding; the export ledger (`<book>.language-qa-changes.csv`) | Publisher |

## Finding fields

The framework asks for each finding to carry the following fields. This table
shows where Bridge keeps each one.

| Framework field | Bridge |
|---|---|
| Issue type | `category` (sandhi, word-joining, typo, punctuation, spacing, unicode, termbase, name) and `layer` (integrity, pattern, lexicon, housestyle) |
| Severity | `severity`: high, medium or low. The framework's **Critical** (missing verse, reversed meaning, wrong negation, wrong referent, corrupted Unicode, major USFM corruption) is not a Language QA severity. Most of those items are owned by the semantic pipeline, the USFM checker or a person. |
| Confidence | `confidence`: high, medium or low. The values are uncalibrated. |
| Action | A `suggestions` list: a suggestion is offered, never applied until "Use" is clicked. |
| Reviewer decision needed | Every finding needs a decision: Use, Edit, Ignore or False positive. |
| Scope | Verse. Book and collection scope arrive with the collection runner (P4). |
| Status | The decision history (`languageQa.history`). An ignore expires when its rule changes. |

## Publication sequence

The order the framework recommends for the IRV project:

1. Translation team revision (Round 1)
2. Strict Tamil linguistic proofreading (Round 2)
3. Source-fidelity check
4. Glossary, names and parallel passages
5. Consultant and exegetical check
6. Community and comprehension testing
7. Whole-book consistency
8. Whole-Bible terms, names and wordlist
9. USFM and Unicode technical QA
10. Typesetting
11. Tamil visual proof on the PDF
12. Final blind read-through
13. Regression checks
14. Consultant final approval
15. Release master

Proofreading uses the project's supplied Tamil and reference texts and its
approved resources as the textual basis. The outside sources below informed the
method only, not the text.

## Sources

- Paratext Manual, *24. Finalising for Publication*: manual.paratext.org/24.FFP/
- Paratext Manual, *12. Basic Checks 2* and *13. Formatting checks*: manual.paratext.org/12.BC2/, manual.paratext.org/13.FC/
- Paratext, *Translation checking tools* and the SIL, BI, PBT and TSC base plans: paratext.org
- USFM 3.0 documentation (identification, characters, cross references, syntax notes): ubsicap.github.io/usfm/
- Unicode, *Normalization FAQ*: unicode.org/faq/normalization.html
- W3C, *Tamil Script Resources*, *Tamil Layout Requirements*, *Tamil Gap Analysis*, *Approaches to line breaking*: w3.org
- Tamil Virtual Academy, grammar courses on எழுத்து, புணர்ச்சி and வல்லினம் மிகும்/மிகா இடங்கள்: tamilvu.org
