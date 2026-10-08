# Bridge — User Manual

For translators and checkers using Bridge day to day. If you're setting up
or building the app, see [`DEVELOPER_SETUP.md`](DEVELOPER_SETUP.md) instead.

> **This manual is written against `v0.9.6` (2026-09-10) and has not been
> rewritten since.** The app is at 0.11.0. Two shipped things are missing
> below: the workbench database cutover (0.10.0 — Bridge-private records moved
> from JSON files into a per-project SQLite database, which is invisible in use
> but means a project opened by 0.11.0 cannot be opened by 0.10.x), and the
> Cross-verse alignment page (0.11.0 — summarized in §6.5). Everything else
> still matches. The release notes in `RELEASE_*.md` cover what changed per
> version.

> **Screenshots:** not included yet — see [Adding screenshots](#adding-screenshots)
> at the bottom for exactly which screen/file to capture and where to drop
> images in.

---

## 1. What Bridge is for

Bridge is a **local-first Bible translation QA workbench**. It works
entirely offline, on your own computer, with a translation project in the
**translationCore** project format — a Bible book (or set of books) where a
target-language translation is being checked against, and word-aligned to,
the original Hebrew or Greek source text.

**What you use it for:**

- Import a Scripture project (your own translation draft, or an existing
  translationCore project).
- Run automatic quality checks: spelling/consistency, translation notes,
  translation words, word alignment, USFM structure, name/transliteration
  consistency.
- Review every flagged item verse by verse and decide what to do about it.
- Do manual word-for-word alignment between your translation and the
  Hebrew/Greek source.
- Export a clean, checked project.

**What Bridge will never do:** silently change your translation text or
alignment. In Basic mode, Bridge AI may automatically record only
high-confidence, evidence-grounded tN/tW review selections; uncertain items
remain open. Advanced mode leaves AI selections as proposals until you apply
or edit them. Neither mode rewrites Scripture or alignment groups.

---

## 2. Installing Bridge (end user)

*(As of 2026-08-25 there's no public downloadable installer yet — this is
written for once one exists, or for a machine a developer has built it on
for you.)*

1. Get the Windows installer (`.exe`, built via the Tauri/NSIS bundler — ask
   whoever built it for you, or check the repo's Releases page once
   available).
2. Run the installer and follow the prompts. Nothing else needs installing —
   the Python engine and all offline QA/source data are bundled inside.
3. Launch Bridge. It opens to **Project Home**, a list of recent projects
   (empty on first install).
4. **No internet connection needed** for normal use — original-language
   source text, translation notes, and translation words are bundled
   offline. You only need internet if you turn on the optional AI features.

---

## 3. How Bridge compares to translationCore

Bridge is **not** a replacement for translationCore's own checking tools in
the sense of doing something entirely different — it works on the *same*
project format and largely the *same* concepts (translationNotes,
translationWords, word alignment). The differences are in workflow and
scope:

| | **translationCore** | **Bridge** |
|---|---|---|
| Runs | Electron app, online-resource-dependent by default | Fully offline-first desktop app; all core checks and source data are bundled, no network needed |
| Checking engines | Its own built-in checking tools | translationCore's own alignment/project logic **plus** [Greek Room](https://github.com/BibleNLP/greek-room) (Wildebeest spelling/consistency, USFM structural checker, versification, name-transliteration consistency) in one unified review flow |
| Review model | Per-tool screens (translationNotes tool, translationWords tool, alignment tool, etc., navigated separately) | One inline verse-by-verse view with all finding types color-coded together, plus a focused review panel |
| AI assistance | Not built in | Optional: AI-generated explanations and alignment proposals, plus evidence-grounded automatic tN/tW review. Basic mode safely applies high-confidence review selections; Advanced mode proposes them for human approval. Scripture text is never silently changed. |
| Corpus statistics | Not built in | Alignment corpus statistics (co-occurrence, translation probability, PMI) computed from your own project's completed alignments (backend feature, no dedicated screen yet) |
| Live tool connectors | N/A | Identity-gated Paratext Project Note handoff; one-shot Logos navigation connector (see the Developer Guide for current verification status) |
| Project files | translationCore project format | **The same format.** A Bridge-exported project (aligned mode) is re-importable into translationCore or any other USFM 3 tool. |

**In short:** if you already know translationCore's tN/tW/alignment tools,
Bridge is the same underlying concepts, but with more offline QA checks
folded into one screen and a review workflow built around explicit
accept/reject decisions instead of separate tool screens.

---

## 4. Glossary — terms you'll see in Bridge

If a term below is unfamiliar, this is meant to be enough to work
productively without looking it up elsewhere. Links go to unfoldingWord's
own documentation, which Bridge's bundled data is sourced from.

- **USFM** — Unified Standard Format Markers. The plain-text markup format
  Scripture is stored in (`\v 1 In the beginning...`). Bridge reads and
  writes USFM 3.
- **translationCore project** — a folder structure containing your
  translation's Scripture text, alignment data, and check decisions, in the
  format used by the [translationCore](https://translationcore.com/) app.
- **translationNotes (tN)** — short notes explaining a specific translation
  challenge in a specific verse (idioms, cultural terms, difficult grammar).
  See [unfoldingWord's translationNotes](https://www.unfoldingword.org/for-translators/translation-notes).
- **translationWords (tW)** — a glossary of key biblical terms (names,
  theological terms, cultural terms) with definitions, meant to keep term
  usage consistent across a translation. See
  [unfoldingWord's translationWords](https://www.unfoldingword.org/for-translators/translation-words).
- **Alignment / word alignment** — linking each word of your translation to
  the specific Hebrew or Greek source word(s) it translates. Alignment can
  be 1:1 (one target word to one source word), 1:many, many:1, or many:many
  (a group of words on one side mapped to a group on the other).
- **Original-language tokens** — the individual Hebrew/Greek words from the
  source text (UHB = Hebrew Old Testament, UGNT = Greek New Testament) that
  your translation gets aligned to.
- **Versification** — how a Bible's chapter/verse numbering is organized;
  different Bible traditions sometimes number verses differently for the
  same passage, and versification tools detect/normalize/reconcile that.
- **Wildebeest** — an offline spelling/orthography/character-consistency
  checker (part of the [Greek Room](https://github.com/BibleNLP/greek-room)
  toolset Bridge uses).
- **Greek Room** — the umbrella name for the offline NLP/QA tools Bridge
  runs underneath (Wildebeest, the USFM structural checker, versification
  tools, Uroman + Smart Edit Distance for names). Not to be confused with
  Greek Room's own separate web app (`ephesus`) — Bridge only uses the
  underlying check modules, not that web app.
- **Uroman / Smart Edit Distance (SED)** — the tools behind Bridge's
  name-consistency check: Uroman converts names into a common Latin-script
  form regardless of source script, and SED measures how close two
  romanized spellings are, to catch inconsistent spellings of the same
  proper name (e.g. "Jerusalem" spelled two different ways in your target
  language).
- **QaFinding** — Bridge's internal term for any single flagged item,
  regardless of which check produced it. Every finding has a review status:
  open, accepted, rejected, ignored, fixed, or needs discussion.

---

## 5. Import and export formats

### What you can import

- A single `.usfm` or `.sfm` file.
- A marker-based `.txt` Scripture file.
- A folder containing one or many USFM/SFM books.
- A Paratext project folder (containing `Settings.xml`).
- An existing translationCore project folder.
- A `.tcore`, `.tstudio`, or ZIP project archive.

Import is always **read-only preview first** — Bridge shows detected books,
verse counts, whether alignment data already exists, and any warnings
*before* anything is written. Name collisions never silently overwrite —
they create a separate, clearly suffixed copy instead, or you can choose to
open the existing project.

If you import raw Scripture that has no existing alignment, Bridge
automatically prepares it for alignment work using its own bundled
Hebrew/Greek source tokens — you don't need a separate original-language
resource.

### What you can export

- **Aligned export** — full USFM 3 with word-alignment markup
  (`zaln`/`w` milestones) built in. This is re-importable into Bridge, into
  translationCore, or into any other tool that understands USFM 3
  alignment.
- **Non-aligned (simplified) export** — your current verse text in USFM,
  without alignment markup. This mode is simplified: footnotes, section
  headers, and poetry formatting are **not** preserved. Use the aligned
  export if you need those retained.

---

## 6. Using Bridge — screen by screen

### 6.1 Project Home

Your project list, most recent first. A project whose folder has gone
missing stays visible (never silently deleted) — you can **Locate** it or
**Forget** the entry. Actions here: **Import file**, **Import folder**,
**Open existing project**.

### 6.2 Importing a project

1. Pick a file/folder, or drag-and-drop anywhere in the window.
2. Review the read-only preview (books, verse counts, alignment status,
   warnings).
3. Fill in required metadata: **Language** (searchable offline catalog),
   **Project name**, **Bible/translation name**, **text direction**
   (LTR/RTL). Import stays disabled until these are complete.
4. If Bridge recognizes an overlap with something already in your project
   library, it explains why (exact match vs. partial book/language/Bible
   overlap) and lets you choose **Open existing project**, **Import as a
   separate copy**, or **Continue with separate import**.
5. Confirm. For a multi-book source, the first book opens right away; the
   rest normalize the first time you open them.

### 6.3 Main editor window

- **Top bar** — current project/book, a chapter dropdown, and **Run whole
  book** (runs background checks across every chapter with progress shown
  per chapter).
- **Verse list** — your chapter's text, inline, with findings shown as
  colored highlights:
  - **Purple** — translationNotes
  - **Blue** — translationWords
  - **Amber** — Alignment
  - **Teal/red** — Greek Room QA (spelling, structure, names)
  - Each verse also shows its alignment status: untouched / partial /
    complete / invalid.

### 6.4 Reviewing a finding

Click a verse or a highlighted finding to open the review panel:

- See the finding's detail and evidence (and, if you've configured an AI
  provider, an AI explanation grounded in the real translation notes/words
  data for that verse — not a guess).
- Decide: **✓ Accept**, **✗ Reject**, **⊘ Ignore**, or **✎ Edit verse**.
- Editing a verse re-runs checks immediately, and marks alignment invalid
  for that verse if it had alignment — Bridge never silently "fixes"
  alignment for you after a text edit.
- Your decisions are remembered — accepting something, re-running checks
  later, or reopening the project another day all keep the same decision.

#### Finding shortcuts — right-click, or Shift+F10

Everything below is a shortcut to the decisions you can already make in the
panel. Nothing is written to your project files without you choosing it, and
every action goes through the same code the panel uses, so a decision made
here survives a re-check exactly as one made there does.

**In the verse text.** Right-click a highlighted word to get a small menu for
the finding underneath it. It offers the same two things the review panel
does:

- **Accept finding** — you agree this is a real issue. If the check proposed a
  correction, Bridge replaces exactly the highlighted words with it, saves, and
  re-checks the verse, all in one step; the finding then moves to **Accepted**
  in the panel. If the check proposed nothing, your verse text is left
  untouched and the finding is simply filed as accepted. Hover the item to see
  which of the two it is about to do.
- **Ignore** — leave the verse as it is and move the finding to the
  **Ignored** section of the panel. Its highlight disappears from the verse.

If a proposed correction cannot be applied — usually because the verse has
been edited since the check ran — Bridge tells you why and leaves the menu
open. It does **not** quietly file the finding as accepted anyway.

**From the keyboard, no mouse needed.** Tab moves between *verses*, not
between highlighted words — a checked chapter would otherwise be hundreds of
tab stops. On a verse:

| Key | What it does |
| --- | --- |
| Tab / Shift+Tab | Move to the next / previous verse |
| Enter or Space | Select the verse (opens it in the review panel) |
| Right / Left arrow | Move between that verse's highlighted findings — the active one gets a ring around it |
| **Shift+F10**, or the **Menu** key | Open the menu above for the ringed finding |
| Up / Down arrow, Home / End | Move through the menu once it is open |
| Escape | Close the menu and put the cursor back on the verse |

Clicking anywhere outside the menu also closes it, and the menu keeps itself
inside the window when you open it near an edge.

**In the review queue** (Alignment QA review). Right-click a row, or press
**Shift+F10** / the **Menu** key with the list focused, for the same kind of
menu against the selected finding: **Apply proposed fix** plus the four
review decisions — *Confirm translation issue*, *Accept translation as
correct*, *False positive*, *Needs discussion*.

> **One word, two meanings — watch this.** In the verse text, *Accept
> finding* means "this finding is right, the translation has a problem" (and
> applies the correction, if there is one). In the review queue, *Accept
> translation as correct* means the opposite — "the translation is fine, there
> is no problem here". The two views work on different data with different
> decision models. Each menu item spells out what it means when you hover it;
> read the hint rather than the verb if you move between the two views.

### 6.5 Manual word alignment

Open the alignment editor for a verse to:

- See source (Hebrew/Greek) and target-language words side by side (each
  side's text direction handled independently).
- Build 1:1, 1:many, many:1, or many:many alignment groups by selecting
  words.
- Use the **word bank** for target words not yet aligned to anything.
- See live completion status and any blocking issues (e.g. missing
  original-language tokens for this verse).
- **Undo** just this verse, or restore an earlier version from its history.
- If you've configured an AI provider, request an **AI alignment
  proposal** as a starting point — you still have to review and accept it.

**Cross-verse alignment (added in 0.11.0).** Sometimes a word in the original
is translated in the *next* verse, not this one. Aligning one verse at a time
cannot show that: it looks like an omission here and an addition there. Open
**Cross-verse alignment** from the editor toolbar, or from the link inside
Align Words, to see several verses of the chapter at once — source words down
the left, each verse's unaligned words down the right, and a strip along the
top counting the gaps per verse.

- Pick the range with the from/to selectors, the per-verse chips, or by
  Ctrl-clicking and Shift-clicking verses in the verse list first. It opens on
  the selected verse and its neighbours by default.
- Dragging a word onto a source word **of the same verse** aligns it exactly as
  Align Words does.
- Dragging a word onto a source word **of another verse** records a *cross-verse
  link*: the source word shows "realized in v.N", the word stays in its own
  verse's word bank marked "↔ v.N", and the × on either removes the link.
- A verse whose only remaining gaps are linked stops being flagged as not fully
  aligned and says how many words are linked across verses. It is still not
  "complete" for translationCore — aligned USFM cannot express a cross-verse
  link, so completion stays with translationCore and nothing is written into
  its alignment data.
- Editing a verse so a linked word disappears marks that link invalid, with the
  reason, rather than deleting it.

**Align automatically.** With an AI provider set up in Settings, **Align
automatically** at the top of the page aligns the verses in range for you, up to
five at a time. Bridge asks your provider about the passage twice, once starting
from the original words and once starting from the translation, and writes only
what both readings agree on:

- **Same-verse groups**, including one word to several (1:2), several to one
  (2:1) and several to several.
- **Cross-verse links**, when the meaning sits in a neighbouring verse.
- **Words with no counterpart for a reason.** A Greek article is *grammatical*;
  a meaning clear from context is *implicit*; a Tamil word the language needs is
  *grammar*; a word that states what the original implies is *explicitation*.
  These show as grey dashed cards and are not counted as gaps.

Anything only one reading gave appears as a suggestion in the cell it would
fill, with ✓ to accept and × to dismiss. Hover to see which reading gave it.

A word **neither** reading could place is never forced. It is marked **?
possible omission** (an original word) or **? possible addition** (a translated
word), and the same finding appears in the verse editor. If it is not really
missing or added, choose **Not missing ▾** or **Not an addition ▾** and say why.
That records a decision, and the finding goes away.

Your own alignments, links and decisions are never changed by an automatic
pass. Each verse gets a card in the strip at the top:
- **✓ Aligned**: nothing is left to do;
- **? Needs review**: with what is left;
- **· Not aligned yet**.

**Undo a verse** puts a verse back as it was before the last pass, leaving alone
anything you changed since. **Align chapter with AI** in the editor toolbar
does the whole chapter three verses at a time, and shows the number of requests
and an approximate cost before anything is sent.

translationCore's own status is unchanged by any of this. A verse with a
cross-verse link or a grammatical word still reads as not complete there,
because aligned USFM cannot express either.

### 6.6 Translation helps review

A focused, occurrence-aware view for working through translationNotes and
translationWords. Distinct colors connect cards to inline verse highlights,
and each AI result includes its evidence and justification.

- **Basic mode** automatically records only safe, high-confidence selections
  grounded in the displayed tN/tW evidence. Uncertain checks remain open.
- **Advanced mode** prepares exact-word proposals but waits for you to apply,
  edit, or clear each selection.
- Run the review for **This verse**, **Chapter**, or **Whole book**. It runs in
  the background, can be cancelled, and Retry resumes only failed or unfinished
  verses while preserving completed current results.
- Results and selection provenance persist with the project. Imported or human
  selections are not overwritten, and editing a verse makes its prior AI review
  stale. Bridge shows an explicit warning; choose **Run AI review again** to
  replace it with a current evaluation.
- **Nothing to Select** is reserved for a genuinely absent or entirely implicit
  target rendering, or a check that does not apply. If AI cites an exact target
  phrase but omits its token IDs, Bridge accepts it only when that quoted phrase
  occurs exactly once; otherwise the check remains pending for manual selection
  or another AI run.
- **Resolve / Paratext** records the exact issue, proposed correction, evidence,
  and reviewer note before sending a Project Note. Offline handoffs remain
  queued and retryable; Bridge sends only to the explicitly confirmed active
  Paratext project.
- When you edit a verse that has a saved resolution, Bridge automatically starts
  a new evidence-grounded verse review. A pass closes it as **Resolved after
  recheck**; a remaining problem becomes **Issue remains after recheck**.
  Uncertain, cancelled, or failed reviews remain open and retryable. The result,
  evidence, confidence, and lifecycle audit persist after restart.

### 6.7 Settings

A modal (not a separate screen), opened via the **Settings** button:

- **AI provider** — Provider, API base URL (blank = OpenAI's default),
  Model, API key. Works with any endpoint that speaks the OpenAI Responses
  API shape — not necessarily every provider's own native format.
- **Quality engine** — **Basic** safely applies strong, evidence-grounded tN/tW
  selections; **Advanced** keeps AI selections as editable proposals.
- **Resources & licenses** — what's bundled offline and under what license.
- **Security** — how your API key is stored: session-only in memory,
  DPAPI-protected on Windows, never written to disk in plaintext.

### 6.8 Exporting

Choose **Aligned export** or **Non-aligned export** — see [§5](#5-import-and-export-formats)
above for what each includes.

### 6.9 AI triage — hiding likely false positives

Greek Room checks are cautious on purpose. They flag anything *objectively
suspicious*, which means a long report will contain findings that are
perfectly fine: punctuation that is correct for your language, a repeated
word that is real grammar, a name that inflects. AI triage is an optional
way to push those down out of your way.

**It needs internet and an API key.** Everything else in Bridge works
offline; this one feature does not. Without a key the **Run AI triage**
button is disabled and tells you so, and the report works exactly as it
otherwise would.

**How to use it.** Open the QA report and click **Run AI triage**. Bridge
sends each finding — with its check type, the verse and the verses either
side, and the aligned original-language words — to the model you configured
in Settings, and stores what comes back. You can keep working while it
runs, and cancel at any time; whatever it has already scored is kept.

Each scored finding then shows a label like *Likely false positive · 92%*
with a one-line reason, and the slider above the table appears:

> **Hide findings rated ≥ 90% likely false positive**

Drag it left to hide more, right to hide fewer, or all the way to **Off** to
see everything. Bridge always tells you how many it is hiding — *"3 findings
hidden by AI triage"* — with a **Show them** link, so nothing ever
disappears silently.

**What the percentage means.** It is the model's confidence in its own
judgement, and it is a **ranking signal, not a calibrated probability**. A
finding at 95% is not "95% certain to be wrong"; it means the model was more
sure about it than one at 70%. Treat the slider as "how much noise am I
willing to tolerate", not as a statistical guarantee.

**What it will never hide.** Only findings the model judged to be false
positives are ever hidden. Anything it marked *uncertain* or *likely real*
stays on screen no matter where you put the slider — including anything it
failed to score at all, because of a network problem or a response it could
not read. Hiding a genuine translation error is a far worse outcome than
leaving a false positive visible, so the default (90%) is deliberately
cautious.

**Correcting it.** Every scored finding has 👍 and 👎 buttons. 👍 means "this
is a real problem" and keeps it visible whatever the model said; 👎 means
"this one really is noise". Your judgement always wins over the model's, it
is remembered, and that finding is never sent to the model again — so
correcting it also stops it costing you anything. Click the same button
again to undo.

**When scores go away.** A verdict belongs to a finding *as it was when it
was scored*. If you edit the verse so the finding's evidence changes, the
old verdict is discarded rather than carried over, and the finding comes
back unscored until you run triage again. Re-running triage costs nothing
for findings that have not changed.

**Cost.** Every run sends findings to your AI provider and is billed by
them. Bridge batches them and never re-sends a finding it has already
scored, but a first run over a whole Bible is not free. The running total is
in Settings.

---

## Adding screenshots

Once the app is running:

1. Screenshot each screen listed in §6 (Win+Shift+S on Windows).
2. Save under `docs/screenshots/`, e.g.
   `docs/screenshots/import-screen.png`.
3. Reference with `![Import screen](screenshots/import-screen.png)`.

Send me the filenames (or upload the images) and I'll drop the image tags
into the right sections.
