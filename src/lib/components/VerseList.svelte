<script lang="ts">
  import { tick } from "svelte";
  import { verseNums, verseTexts, verseDisplay, headingsByVerse, findingsByVerse, checkStatusByVerse, alignmentStatusByVerse, selectedVerse, selectedVerseSet, currentChapter, verseKey, nativeChecksByVerse, aiCheckReviewsByVerse, checkingProgress, languageQaFindingsByVerse, project } from "../stores";
  import { rangeBetween } from "../crossVerseRange";
  import { unionInChapterOrder } from "../crossVerseSuggest";
  import { buildSegments } from "../utils/highlight";
  import { withNoteMarkers, type VerseNote, type VerseNoteKind } from "../utils/usfmNotes";
  import { identityDisplay, plainToUtf16, utf16Offset } from "../utils/verseDisplay";
  import type { VerseDisplay } from "../types/finding";
  import VerseNotesPopup from "./VerseNotesPopup.svelte";
  import FindingContextMenu from "./FindingContextMenu.svelte";
  import { decideLanguageQaFindingOptimistically, decideLocalFinding } from "../findingActions";
  import { codePointToUtf16 } from "../utils/codePoints";
  import LanguageQaHistoryPopup from "./LanguageQaHistoryPopup.svelte";
  import { IGNORE_SCOPES, houseStyleNotice, recordScopedIgnore, undoLearned } from "../houseStyleUi";
  import { forgetLearnedFix, isLearnedFinding, learnedPairOf } from "../learnedFixes";
  import type { HouseStyleScope } from "../types/houseStyle";
  import type { QaFinding } from "../types/finding";
  import type { LanguageQaFinding, LanguageQaSuggestion } from "../types/languageQa";
  import {
    applySuggestedFindingFix, applyLanguageQaSuggestedFix, editingChapter, editingVerse, editText, editSaving,
    editError, saveVerseEdit, cancelVerseEdit, startVerseEdit, recheckingKey,
  } from "../verseEditor";
  import { openAlignment } from "../alignmentUi";
  import { requestAIReview, aiJobActive, type AIReviewScope } from "../aiReviewUi";

  export let onSelect: (verse: string) => void;

  let openNotes: { kind: VerseNoteKind; notes: VerseNote[]; reference: string } | null = null;
  let contextMenu: { finding: QaFinding; verse: string; x: number; y: number } | null = null;
  let contextBusy = false;
  // Separate from contextMenu: Language QA findings are a different,
  // disposable data model (see language_qa_jobs.py's own docstring), never
  // cast into a fake QaFinding just to reuse the one menu instance above.
  // Covers every inline-decorated Language QA rule (terminology.deprecated-
  // form, tamil.vallinam-missing) -- not termbase-specific despite the
  // history, and "term" would now collide with the unrelated Settings >
  // Terminology pane.
  let langQaContextMenu: { finding: LanguageQaFinding; verse: string; x: number; y: number } | null = null;
  let langQaContextBusy = false;
  // A span carrying findings from more than one source (Greek Room/native and
  // Language QA): one menu with a section per finding (layered-rules 4.3).
  let mixedMenu: { verse: string; qa: QaFinding[]; lqa: LanguageQaFinding[]; x: number; y: number } | null = null;
  // The general verse right-click menu (issue #69) -- a separate menu from
  // contextMenu above, which only ever opens on a finding span. The two
  // never open at once: a right-click on a mark stops propagation before it
  // reaches the row.
  let verseMenu: { verse: string; x: number; y: number } | null = null;
  // The verse whose Language QA decision history is open (verse menu).
  let historyFor: { projectPath: string; chapter: string; verse: string } | null = null;
  // Which underlined finding Left/Right last landed on, scoped to one verse
  // key so switching verses starts at that verse's first finding again.
  let activeFindingVerseKey = "";
  let activeFindingIndex = 0;
  let contextNotice = "";
  let contextNoticeError = false;

  /**
   * The same two actions ReviewPanel offers on an open Greek Room finding
   * ("Accept and edit" / "Ignore"), so the menu and the panel cannot disagree
   * about what a reviewer can do to a finding.
   *
   * Accept folds the correction in rather than offering it separately: a fix
   * a reviewer agrees with and the decision that follows from it are one act,
   * and there is no apply item left stranded and greyed out on the many checks
   * that propose no replacement. Each hint says which of the two things Accept
   * is about to do.
   *
   * These write engine FindingStatus values. The QA review queue's
   * REVIEWER_ACTIONS are a different model over a different data source, and
   * its "Accept translation as correct" means the opposite of "Accept finding"
   * here — see the hints, and USER_MANUAL.md §6.4.
   */
  $: contextActions = contextMenu ? findingActionsFor(contextMenu.finding, contextBusy) : [];

  function findingActionsFor(finding: QaFinding, busy = contextBusy) {
    return [
      {
        id: "accept",
        label: "Accept finding",
        disabled: busy,
        title: hasProposedFix(finding)
          ? "Replace the highlighted words with the proposed correction, re-check the verse, and file this finding as accepted."
          : "File this finding as accepted. This check proposed no correction, so the verse text is left alone.",
      },
      {
        id: "ignore",
        label: "Ignore",
        disabled: busy,
        title: "Leave the verse as it is and move this finding to Ignored in the review panel.",
      },
    ];
  }

  /**
   * The inline Language QA context menu: one "Use" per ranked suggestion (at
   * most five, each with its rationale as the tooltip), Edit… (the verse
   * opens with the flagged text selected), Ignore this occurrence, and Mark as
   * false positive. A finding with no suggestion (a termbase entry with only
   * rejected forms) simply has no Use item. Every action closes the menu at
   * once: nothing here waits on the engine before the screen changes.
   * (Ignore scoped to a word or rule -- house style -- is layered-rules Phase 6.)
   */
  $: langQaContextActions = langQaContextMenu ? buildLangQaActions(langQaContextMenu.finding) : [];

  function buildLangQaActions(finding: LanguageQaFinding) {
    const suggestions = languageQaSuggestions(finding);
    return [
      ...suggestions.map((s) => ({
        id: `use:${s.rank}`,
        label: `Use "${s.text}"`,
        disabled: langQaContextBusy,
        title: s.rationale || "Replace the flagged text with this form, re-check the verse, and record it as accepted.",
      })),
      {
        id: "edit",
        label: "Edit…",
        separatorBefore: suggestions.length > 0,
        disabled: langQaContextBusy,
        title: "Open this verse for editing, with the flagged text selected.",
      },
      {
        id: "ignore",
        label: "Ignore this occurrence",
        separatorBefore: true,
        disabled: langQaContextBusy,
        title: "Leave the verse as it is and record this occurrence as ignored.",
      },
      {
        id: "ignore-scope",
        label: "Ignore more widely",
        disabled: langQaContextBusy,
        title: "Record this as the project's house style (Settings → Language QA → House style, where it can be removed).",
        submenu: IGNORE_SCOPES.filter((s) => s.scope !== "occurrence").map((s) => ({
          id: `ignore-scope:${s.scope}`, label: s.label, title: s.title, disabled: langQaContextBusy,
        })),
      },
      {
        id: "false-positive",
        label: "Mark as false positive",
        disabled: langQaContextBusy,
        title: "This is not a problem: hide it and list it under False positives in the Language QA panel.",
      },
      ...(isLearnedFinding(finding) ? [{
        id: "learned-forget",
        label: "Forget this learned fix",
        separatorBefore: true,
        disabled: langQaContextBusy,
        title: "Stop offering this replacement anywhere in the book. Restore it in Language QA › Dictionary.",
      }] : []),
    ];
  }

  /** Ranked, at most five. Falls back to the one-release alias for a finding
   * from an older engine. */
  function languageQaSuggestions(finding: LanguageQaFinding): LanguageQaSuggestion[] {
    if (finding.suggestions?.length) return finding.suggestions.slice(0, 5);
    return finding.suggestedReplacement
      ? [{ text: finding.suggestedReplacement, rank: 1, source: "rule", rationale: "" }]
      : [];
  }

  /**
   * The general verse right-click menu (issue #69): "AI review" opens a
   * submenu offering the same three scopes ReviewPanel's AI review tab
   * offers, and "Edit verse" is the same action the double-click/button
   * routes already trigger. Disabled reasons mirror ReviewPanel's own
   * buttons exactly (aiJobActive stands in for its local aiJobBusy) so the
   * menu and the panel never disagree about when an action is available.
   */
  $: verseMenuActions = verseMenu ? buildVerseMenuActions(verseMenu.verse) : [];

  function buildVerseMenuActions(verse: string) {
    const busyTitle = "Wait for background checking, editing or a running AI review to finish";
    const alreadyEditingThis = $editingChapter === $currentChapter && $editingVerse === verse;
    const editBlocked = $checkingProgress.running || Boolean($editingChapter) || $editSaving || Boolean($recheckingKey);
    const editDisabled = editBlocked || alreadyEditingThis;
    const verseAiDisabled = editBlocked || $aiJobActive;
    const scopeAiDisabled = $checkingProgress.running || $aiJobActive;
    return [
      {
        id: "ai-review",
        label: "AI review",
        title: "Run an evidence-grounded AI review for this verse, its chapter, or its book",
        submenu: [
          {
            id: "ai-review:verse",
            label: "Verse",
            disabled: verseAiDisabled,
            title: verseAiDisabled ? busyTitle : "Run an evidence-grounded AI review for this verse",
          },
          {
            id: "ai-review:chapter",
            label: "Chapter",
            disabled: scopeAiDisabled,
            title: scopeAiDisabled ? busyTitle : "Run an evidence-grounded AI review across every verse in this chapter",
          },
          {
            id: "ai-review:book",
            label: "Book",
            disabled: scopeAiDisabled,
            title: scopeAiDisabled ? busyTitle : "Run an evidence-grounded AI review across every verse in this book",
          },
        ],
      },
      {
        id: "edit-verse",
        label: "Edit verse",
        separatorBefore: true,
        disabled: editDisabled,
        title: alreadyEditingThis
          ? "This verse is already being edited."
          : editBlocked ? "Wait for background checking to finish before editing" : "Edit this verse",
      },
      {
        id: "lqa-history",
        label: "Language QA history…",
        disabled: !$project,
        title: "Every Language QA decision recorded on this verse (read-only)",
      },
    ];
  }

  function openVerseMenu(event: MouseEvent, verse: string): void {
    event.preventDefault();
    event.stopPropagation();
    selectFromList(verse);
    verseMenu = { verse, x: event.clientX, y: event.clientY };
  }

  function onVerseContextAction(event: CustomEvent<{ id: string }>): void {
    if (!verseMenu) return;
    const verse = verseMenu.verse;
    const id = event.detail.id;
    verseMenu = null;
    if (id === "edit-verse") {
      startVerseEdit($currentChapter, verse);
      return;
    }
    if (id === "lqa-history") {
      if ($project) historyFor = { projectPath: $project.path, chapter: $currentChapter, verse };
      return;
    }
    const scope: AIReviewScope | null =
      id === "ai-review:verse" ? "verse" : id === "ai-review:chapter" ? "chapter" : id === "ai-review:book" ? "book" : null;
    if (scope) requestAIReview($currentChapter, verse, scope);
  }

  const hasProposedFix = (finding: QaFinding): boolean =>
    finding.suggested_replacement !== null
      && finding.start_offset !== null && finding.end_offset !== null;

  const markerLabel = (kind: VerseNoteKind): string => (kind === "footnote" ? "f" : "x");
  const markerTitle = (kind: VerseNoteKind): string =>
    kind === "footnote" ? "Footnote" : "Cross reference";

  function openFindingMenu(
    event: MouseEvent,
    findingIds: string[],
    findings: QaFinding[],
    verse: string,
  ): void {
    const finding = findingIds
      .map((id) => findings.find((item) => item.id === id))
      .find((item): item is QaFinding => Boolean(item));
    if (!finding) return;
    event.preventDefault();
    event.stopPropagation();
    onSelect(verse);
    contextMenu = { finding, verse, x: event.clientX, y: event.clientY };
  }

  function openLangQaFindingMenu(
    event: MouseEvent,
    findingIds: string[],
    langFindings: LanguageQaFinding[],
    verse: string,
  ): void {
    const finding = findingIds
      .map((id) => langFindings.find((item) => item.id === id))
      .find((item): item is LanguageQaFinding => Boolean(item));
    if (!finding) return;
    event.preventDefault();
    event.stopPropagation();
    onSelect(verse);
    langQaContextMenu = { finding, verse, x: event.clientX, y: event.clientY };
  }

  /**
   * A mark's findingIds can come from QaFinding, native/AI check reviews, or
   * a Language QA finding (buildSegments merges all of them). Try the
   * existing QaFinding-owning menu first, exactly as before -- this
   * preserves every existing finding type's behaviour unchanged -- and fall
   * back to the Language QA menu only when nothing in `findings` claims this
   * span.
   */
  function onMarkContextMenu(
    event: MouseEvent,
    findingIds: string[],
    findings: QaFinding[],
    langFindings: LanguageQaFinding[],
    verse: string,
  ): void {
    const qa = findingIds.map((id) => findings.find((f) => f.id === id)).filter((f): f is QaFinding => Boolean(f));
    const lqa = findingIds.map((id) => langFindings.find((f) => f.id === id))
      .filter((f): f is LanguageQaFinding => Boolean(f));
    if (qa.length > 0 && lqa.length > 0) {
      // Several sources on one span (layered-rules Phase 4.3): one section per
      // finding, each with its own actions, rather than the first one winning.
      event.preventDefault();
      event.stopPropagation();
      onSelect(verse);
      mixedMenu = { verse, qa, lqa, x: event.clientX, y: event.clientY };
    } else if (qa.length > 0) {
      openFindingMenu(event, findingIds, findings, verse);
    } else {
      openLangQaFindingMenu(event, findingIds, langFindings, verse);
    }
  }

  const shortText = (text: string, limit = 48): string =>
    text.length > limit ? `${text.slice(0, limit - 1)}…` : text;

  /** One submenu per finding: the Greek Room/native actions or the Language QA
   * actions, exactly as their own menus offer them, with ids prefixed by the
   * finding so the action is routed back to the right handler. */
  $: mixedMenuActions = mixedMenu ? [
    ...mixedMenu.qa.map((finding) => ({
      id: `qa:${finding.id}`,
      label: `${finding.engine || "Check"}: ${shortText(finding.explanation || finding.check_type)}`,
      submenu: findingActionsFor(finding).map((a) => ({ ...a, id: `qa:${finding.id}:${a.id}` })),
    })),
    ...mixedMenu.lqa.map((finding) => ({
      id: `lqa:${finding.id}`,
      label: `Language QA: ${shortText(`${finding.originalText} — ${finding.message}`)}`,
      submenu: buildLangQaActions(finding).map((a) => ({ ...a, id: `lqa:${finding.id}:${a.id}` })),
    })),
  ] : [];

  function onMixedMenuAction(event: CustomEvent<{ id: string }>): void {
    if (!mixedMenu) return;
    const { verse, qa, lqa, x, y } = mixedMenu;
    const [source, findingId, ...rest] = event.detail.id.split(":");
    const action = rest.join(":");
    mixedMenu = null;
    if (source === "qa") {
      const finding = qa.find((f) => f.id === findingId);
      if (!finding) return;
      contextMenu = { finding, verse, x, y };
      void onContextAction(new CustomEvent("action", { detail: { id: action } }));
    } else if (source === "lqa") {
      const finding = lqa.find((f) => f.id === findingId);
      if (!finding) return;
      langQaContextMenu = { finding, verse, x, y };
      onLangQaContextAction(new CustomEvent("action", { detail: { id: action } }));
    }
  }

  /**
   * Every action closes the menu and changes the screen before any engine
   * call: Use shows the corrected verse at once (applyLanguageQaSuggestedFix
   * saves optimistically and rolls back if the save fails); Ignore and False
   * positive drop the mark at once and put it back only if recording fails.
   * The notice reports the outcome when it arrives.
   */
  function onLangQaContextAction(event: CustomEvent<{ id: string }>): void {
    if (!langQaContextMenu || langQaContextBusy) return;
    const { finding, verse } = langQaContextMenu;
    const id = event.detail.id;
    langQaContextMenu = null;
    contextNotice = "";
    if (id.startsWith("use:")) {
      const chosen = languageQaSuggestions(finding).find((s) => `use:${s.rank}` === id) ?? null;
      langQaContextBusy = true;
      void applyLanguageQaSuggestedFix(finding, chosen).then((result) => {
        contextNotice = result.message;
        contextNoticeError = !result.ok;
      }).finally(() => { langQaContextBusy = false; });
    } else if (id === "edit") {
      void editWithSelection(finding, verse);
    } else if (id === "learned-forget") {
      // Not a decision: the fix stops being offered everywhere, marks go at once.
      const pair = learnedPairOf(finding);
      if (!pair) return;
      contextNotice = `No longer offering “${pair.old}” → “${pair.newWord}”.`;
      contextNoticeError = false;
      void forgetLearnedFix(pair.old, pair.newWord).then((error) => {
        if (!error) return;
        contextNotice = error;
        contextNoticeError = true;
      });
    } else if (id.startsWith("ignore-scope:")) {
      // House style: the occurrence is ignored at once, and the scope is
      // recorded as an explicit house-style entry the next pass applies.
      const scope = id.slice("ignore-scope:".length) as HouseStyleScope;
      contextNotice = `Ignored: ${IGNORE_SCOPES.find((s) => s.scope === scope)?.label.toLowerCase() ?? scope}.`;
      contextNoticeError = false;
      void decideLanguageQaFindingOptimistically(finding, "ignored").then(async (error) => {
        if (error) {
          contextNotice = error;
          contextNoticeError = true;
          return;
        }
        try {
          await recordScopedIgnore(finding, scope);
        } catch (e) {
          contextNotice = e instanceof Error ? e.message : String(e);
          contextNoticeError = true;
        }
      });
    } else if (id === "ignore" || id === "false-positive") {
      contextNotice = id === "ignore" ? "Occurrence ignored." : "Marked as a false positive.";
      contextNoticeError = false;
      void decideLanguageQaFindingOptimistically(finding, id === "ignore" ? "ignored" : "rejected")
        .then((error) => {
          if (error) {
            contextNotice = error;
            contextNoticeError = true;
          }
        });
    }
  }

  /** Edit… opens the editor with the flagged span selected. Engine offsets
   * are code points; a textarea selection is UTF-16, so they are converted
   * over the raw text the editor holds. */
  async function editWithSelection(finding: LanguageQaFinding, verse: string): Promise<void> {
    if (!startVerseEdit($currentChapter, verse)) return;
    await tick();
    const area = scrollContainer?.querySelector<HTMLTextAreaElement>(
      `[data-verse-key="${verseKey($currentChapter, verse)}"] textarea`);
    if (!area) return;
    const text = area.value;
    area.focus();
    area.setSelectionRange(codePointToUtf16(text, finding.start), codePointToUtf16(text, finding.end));
  }

  /**
   * Finding ids that actually carry an underline in this verse, in reading
   * order — same filter and sort buildSegments/findingNumbers use, so the
   * keyboard walks the marks a reviewer can see, in the order their
   * superscript numbers run.
   */
  // The row's ✓ also counts drawn Language QA marks as open (layered-rules
  // 4.3): a verse with a mark on it is not shown as clean.
  function markedFindingIds(
    findings: QaFinding[], textLength: number, langDisplay: LanguageQaFinding[] = [],
  ): string[] {
    // Language QA marks are walked too (layered-rules 4.3), by their display
    // offsets, in one reading order with the other findings.
    const spans = [
      ...findings
        .filter((f) => f.start_offset !== null && f.end_offset !== null && f.end_offset <= textLength)
        .map((f) => ({ id: f.id, start: f.start_offset! })),
      ...langDisplay.filter((f) => f.end <= textLength).map((f) => ({ id: f.id, start: f.start })),
    ];
    return spans
      .sort((a, b) => (a.start - b.start) || a.id.localeCompare(b.id))
      .map((f) => f.id);
  }

  // Reactive rather than a plain function so the each-block {@const} that calls
  // it re-evaluates when the active finding moves: Svelte invalidates on the
  // reference to activeIndexFor, not on variables read inside a function body.
  $: activeIndexFor = (verseKeyValue: string, count: number): number =>
    verseKeyValue === activeFindingVerseKey && activeFindingIndex < count ? activeFindingIndex : 0;

  /**
   * Keyboard route to the same menus their right-click equivalents open —
   * without it, "Apply proposed fix" and the verse menu (issue #69) would be
   * reachable by pointer only, since the review panel has no apply-fix
   * control and (for a verse with no findings) no context-menu equivalent
   * of its own.
   *
   * The verse row stays the single tab stop, the way QaFindingList's listbox
   * does it: making every underlined span focusable would add one tab stop per
   * finding inside the verse text, so tabbing through a checked chapter would
   * stop on hundreds of words. Left/Right move the active finding within the
   * row instead, and Shift+F10 (or the Menu key) opens a menu: anchored under
   * the active finding's underline when the verse has one, or under the row
   * itself -- the general "AI review / Edit verse" menu -- when it doesn't.
   */
  function onVerseKeydown(
    event: KeyboardEvent,
    verse: string,
    key: string,
    findingIds: string[],
    findings: QaFinding[],
    langFindings: LanguageQaFinding[] = [],
  ): void {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelect(verse);
      return;
    }
    const isMenuKey = event.key === "ContextMenu" || (event.shiftKey && event.key === "F10");
    if (findingIds.length === 0) {
      if (!isMenuKey) return;
      event.preventDefault();
      onSelect(verse);
      const row = event.currentTarget as HTMLElement;
      const rect = row.getBoundingClientRect();
      verseMenu = { verse, x: rect.left, y: rect.bottom };
      return;
    }
    const index = activeIndexFor(key, findingIds.length);
    if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
      event.preventDefault();
      onSelect(verse);
      activeFindingVerseKey = key;
      activeFindingIndex =
        (index + (event.key === "ArrowRight" ? 1 : -1) + findingIds.length) % findingIds.length;
      return;
    }
    if (!isMenuKey) return;
    event.preventDefault();
    const finding = findings.find((item) => item.id === findingIds[index]);
    // The raw store copy, not the display one: the fix splices the raw verse.
    const langFinding = finding ? undefined : langFindings.find((item) => item.id === findingIds[index]);
    if (!finding && !langFinding) return;
    onSelect(verse);
    activeFindingVerseKey = key;
    activeFindingIndex = index;
    const row = event.currentTarget as HTMLElement;
    const anchor = row.querySelector<HTMLElement>(`[data-finding-ids~="${findingIds[index]}"]`) ?? row;
    const rect = anchor.getBoundingClientRect();
    if (finding) contextMenu = { finding, verse, x: rect.left, y: rect.bottom };
    else if (langFinding) langQaContextMenu = { finding: langFinding, verse, x: rect.left, y: rect.bottom };
  }

  async function onContextAction(event: CustomEvent<{ id: string }>): Promise<void> {
    if (!contextMenu || contextBusy) return;
    const { finding, verse } = contextMenu;
    contextBusy = true;
    contextNotice = "";
    try {
      if (event.detail.id === "accept" && hasProposedFix(finding)) {
        // applySuggestedFindingFix records the accept itself: it hands the
        // finding id to the save hook ReviewPanel registers, which files it as
        // accepted once the re-check lands. A fix that could not be applied is
        // NOT then quietly accepted — the reviewer sees why and the menu stays
        // open so they can choose again.
        const result = await applySuggestedFindingFix(finding);
        contextNotice = result.message;
        contextNoticeError = !result.ok;
        if (result.ok) contextMenu = null;
      } else if (event.detail.id === "accept" || event.detail.id === "ignore") {
        const accepted = event.detail.id === "accept";
        // $currentChapter and the verse the menu was opened on, not
        // finding.chapter/finding.verse: those are numeric anchors, so a verse
        // bridge ("3-4") would file the decision under "3" and miss the store
        // entry. ReviewPanel keys its own decisions the same way this does.
        await decideLocalFinding(
          $currentChapter, verse, finding.id, accepted ? "accepted" : "ignored",
        );
        contextNotice = accepted ? "Finding accepted." : "Finding ignored.";
        contextNoticeError = false;
        contextMenu = null;
      }
    } catch (error) {
      contextNotice = error instanceof Error ? error.message : String(error);
      contextNoticeError = true;
    } finally {
      contextBusy = false;
    }
  }

  /**
   * QaFinding offsets index the RAW verse string — bridge_service's
   * _first_token_span computes them that way deliberately so they line up with
   * what this component highlights. Now that the rendered text has notes and
   * markers removed, every span has to move with it or each underline slides
   * off its word. A finding that lived entirely inside a lifted footnote
   * collapses to zero length and simply covers no segment; it stays in the
   * list so the 1-based numbering still agrees with ReviewPanel.
   */
  function remapFindings(findings: QaFinding[], display: VerseDisplay): QaFinding[] {
    return findings.map((finding) =>
      finding.start_offset !== null && finding.end_offset !== null
        ? {
            ...finding,
            start_offset: utf16Offset(display, finding.start_offset),
            end_offset: utf16Offset(display, finding.end_offset),
          }
        : finding,
    );
  }

  /** The engine's note positions are code points into `plain`; segments are UTF-16. */
  function notesForLayout(display: VerseDisplay): VerseNote[] {
    return display.notes.map((note) => ({ ...note, position: plainToUtf16(display, note.position) }));
  }

  /** Display only. Language QA's start/end are raw-verse code-point offsets,
   * so to draw a mark over the rendered text (notes lifted out) they shift
   * the same way remapFindings' do. These copies go to buildSegments and
   * nowhere else: the right-click menu and applyLanguageQaSuggestedFix get the
   * store's raw findings, because the fix splices the raw verse. Handing them
   * these shifted offsets put the fix before a footnote in the wrong place. */
  function displayLanguageQaFindings(findings: LanguageQaFinding[], display: VerseDisplay): LanguageQaFinding[] {
    return findings.map((finding) => ({
      ...finding, start: utf16Offset(display, finding.start), end: utf16Offset(display, finding.end),
    }));
  }

  let scrollContainer: HTMLDivElement;
  let lastScrolledKey = "";

  async function scrollSelectedToTop(key: string): Promise<void> {
    await tick();
    const target = scrollContainer?.querySelector<HTMLElement>(`[data-verse-key="${key}"]`);
    if (!target) return;
    const containerRect = scrollContainer.getBoundingClientRect();
    const targetRect = target.getBoundingClientRect();
    scrollContainer.scrollTo({
      top: Math.max(0, scrollContainer.scrollTop + targetRect.top - containerRect.top - 8),
      behavior: "smooth",
    });
    lastScrolledKey = key;
  }

  $: {
    const key = $selectedVerse ? verseKey($currentChapter, $selectedVerse) : "";
    if (key && scrollContainer && key !== lastScrolledKey) void scrollSelectedToTop(key);
  }

  /**
   * Selecting by clicking a row must not scroll. The row is already on screen
   * -- the reader just pointed at it -- so pulling it to the top moves the
   * text out from under the cursor. Marking the key as already-scrolled is
   * what suppresses it, rather than dropping the reactive scroll: navigation
   * that does not come from a click (Go to, desktop verse sync, a click
   * through from the report) still has to bring the verse into view, and none
   * of those routes come through here.
   */
  function selectFromList(verse: string): void {
    lastScrolledKey = verseKey($currentChapter, verse);
    onSelect(verse);
  }

  /** Row click with modifiers (#118): Ctrl/Cmd toggles the verse in the
   *  multi-selection that feeds the Cross-verse alignment range picker,
   *  Shift selects the run from the active verse to this one (by index in
   *  the chapter's verse list, never by number), a plain click clears the
   *  set. `selectedVerse` stays the single active verse in every case. */
  function selectFromRow(verse: string, event: MouseEvent): void {
    const anchor = $selectedVerse;
    if (event.shiftKey && anchor) {
      const run = rangeBetween($verseNums, anchor, verse);
      selectedVerseSet.set(run.length > 1 ? run : []);
      onSelect(verse);
      return;
    }
    if (event.ctrlKey || event.metaKey) {
      const base = $selectedVerseSet.length > 0 ? $selectedVerseSet : (anchor ? [anchor] : []);
      const next = base.includes(verse)
        ? base.filter((v) => v !== verse)
        : unionInChapterOrder($verseNums, base, [verse]);
      selectedVerseSet.set(next.length > 1 ? next : []);
      if (!base.includes(verse)) onSelect(verse);
      return;
    }
    selectedVerseSet.set([]);
    selectFromList(verse);
  }

  // The set is chapter-scoped; a chapter switch starts over.
  $: $currentChapter, selectedVerseSet.set([]);

  /** Double-click is a second route to Edit verse, and so is the row's edit
   *  pencil (issue #73). startVerseEdit carries its own guards -- it returns
   *  false while a check, save or recheck is in flight -- and the verse must
   *  not get selected when it refuses: a disabled button can still receive a
   *  synthetic click, and a double-click is not gated at all, so selecting
   *  first moved the reader's selection for an edit that never opened. Same
   *  ordering as openAlignmentFromList below, for the same reason. */
  function beginEditFromList(verse: string): void {
    if ($editingChapter === $currentChapter && $editingVerse === verse) return;
    if (!startVerseEdit($currentChapter, verse)) return;
    selectFromList(verse);
  }

  /** The per-row alignment glyph (issue #70): open the same Align Words modal
   * ReviewPanel's "⇄ Align words" button opens, so the row itself is a
   * second entry point rather than a separate control. openAlignment's own
   * guard runs first -- while background checking/saving is in flight the
   * disabled attribute normally stops the click, but a disabled button can
   * still receive a synthetic click, so the verse must not get selected
   * either in that case. */
  function openAlignmentFromList(verse: string): void {
    if (!openAlignment($currentChapter, verse)) return;
    selectFromList(verse);
  }

  // Grows the edit textarea to fit its full content (1, 2, or more lines)
  // with no scrollbar, plus one blank line of buffer at the bottom — rather
  // than a fixed rows="2" that scrolls for longer verses and wastes space
  // for short ones.
  function autosize(node: HTMLTextAreaElement) {
    const resize = () => {
      node.style.height = "auto";
      const lineHeight = parseFloat(getComputedStyle(node).lineHeight) || 20;
      const borderHeight = node.offsetHeight - node.clientHeight;
      node.style.height = `${node.scrollHeight + lineHeight + borderHeight}px`;
    };
    resize();
    node.addEventListener("input", resize);
    return { destroy: () => node.removeEventListener("input", resize) };
  }
</script>

<div class="editor-scroll" bind:this={scrollContainer}>
  <div class="chapter-label">Chapter {$currentChapter}</div>

  {#each $verseNums as v}
    {@const key = verseKey($currentChapter, v)}
    {@const headings = $headingsByVerse[key] ?? []}
    {#each headings as heading}
      <!-- Rendered above the verse it introduces, not as part of the previous
           verse's text where it used to live (#180). Not selectable, not
           checkable and never alignable: it is not a translation of any source
           word. -->
      <h3 class="section-heading" class:major={heading.tag.startsWith("ms")}>{heading.text}</h3>
    {/each}
    {@const findings = $findingsByVerse[key] ?? []}
    {@const checkStatus = $checkStatusByVerse[key]}
    {@const alignmentStatus = $alignmentStatusByVerse[key] ?? "untouched"}
    {@const langFindings = $languageQaFindingsByVerse[key] ?? []}
    {@const openCount = findings.filter((f) => f.status === "open").length + langFindings.length}
    {@const highlightFindings = findings.filter((f) => f.status !== "ignored" && f.status !== "accepted")}
    {@const display = $verseDisplay[key] ?? identityDisplay($verseTexts[key] ?? "")}
    {@const remapped = remapFindings(highlightFindings, display)}
    {@const langDisplay = displayLanguageQaFindings(langFindings, display)}
    {@const segments = buildSegments(display.plain, remapped, $nativeChecksByVerse[key] ?? [], $aiCheckReviewsByVerse[key] ?? [], langDisplay)}
    {@const menuFindingIds = markedFindingIds(remapped, display.plain.length, langDisplay)}
    {@const activeFindingId = menuFindingIds[activeIndexFor(key, menuFindingIds.length)]}
    {@const isEditingThis = $editingChapter === $currentChapter && $editingVerse === v}
    <div
      class="verse"
      class:editing-row={isEditingThis}
      data-verse-key={key}
      class:active={$selectedVerse === v}
      class:multi={$selectedVerseSet.includes(v)}
      data-multi-selected={$selectedVerseSet.includes(v) ? "true" : undefined}
      class:approved={checkStatus === "succeeded" && openCount === 0}
      class:check-failed={checkStatus === "failed" || checkStatus === "cancelled"}
      role="button"
      tabindex="0"
      aria-haspopup="menu"
      aria-keyshortcuts="Shift+F10"
      on:click={(event) => selectFromRow(v, event)}
      on:dblclick={() => beginEditFromList(v)}
      on:keydown={(e) => onVerseKeydown(e, v, key, menuFindingIds, findings, langFindings)}
      on:contextmenu={(e) => openVerseMenu(e, v)}
    >
      <div class="vnum">
        {v}{#if checkStatus === "succeeded" && openCount === 0}&nbsp;✓{:else if checkStatus === "failed" || checkStatus === "cancelled"}&nbsp;⚠{/if}
      </div>
      {#if isEditingThis}
        <div
          class="vedit"
          on:click|stopPropagation
          on:dblclick|stopPropagation
          on:keydown|stopPropagation
          on:contextmenu|stopPropagation
          role="presentation"
        >
          <div class="vedit-row">
            <textarea use:autosize bind:value={$editText} disabled={$editSaving} />
            <div class="edit-actions">
              <button
                class="icon-btn save" on:click={() => void saveVerseEdit()}
                disabled={$editSaving || $editText.trim() === ""}
                title={$editSaving ? "Saving…" : "Save & re-check"}
              >{#if $editSaving}<span class="spin-sm" />{:else}✓{/if}</button>
              <button class="icon-btn cancel" on:click={cancelVerseEdit} disabled={$editSaving} title="Cancel">✕</button>
            </div>
          </div>
          {#if $editError}<p class="edit-error">{$editError}</p>{/if}
        </div>
      {:else}
        <div class="vtext">
          {#each withNoteMarkers(segments, notesForLayout(display)) as piece}
            {#if piece.kind === "note"}<button
                class="note-btn {piece.note.kind}"
                on:dblclick|stopPropagation
                on:click|stopPropagation={() =>
                  (openNotes = { kind: piece.note.kind, notes: [piece.note], reference: key })}
                title={`${markerTitle(piece.note.kind)}${piece.note.reference ? ` ${piece.note.reference}` : ""}`}
                aria-label={`Show ${markerTitle(piece.note.kind).toLowerCase()} at this point in verse ${key}`}
              >{markerLabel(piece.note.kind)}</button>{:else if piece.seg.className}<mark
                class={piece.seg.className}
                class:active-finding={$selectedVerse === v && activeFindingId !== undefined
                  && piece.seg.findingIds.includes(activeFindingId)}
                data-finding-ids={piece.seg.findingIds.join(" ")}
                title={piece.seg.title}
                on:contextmenu={(event) => onMarkContextMenu(event, piece.seg.findingIds, findings, langFindings, v)}
              >{piece.seg.text}</mark>{#if piece.seg.numbers.length}<sup class="finding-num">{piece.seg.numbers.join(",")}</sup>{/if}{:else}{piece.seg.text}{/if}
          {/each}
        </div>
        <div class="row-actions">
          <button
            type="button"
            class="edit-pencil"
            disabled={$checkingProgress.running || $editSaving || Boolean($recheckingKey)}
            title={$checkingProgress.running || $editSaving || $recheckingKey
              ? "Wait for background checking to finish before editing"
              : "Edit verse"}
            aria-label={`Edit verse ${v}`}
            on:click|stopPropagation={() => beginEditFromList(v)}
            on:dblclick|stopPropagation
          >✎︎</button>
          <button
            type="button"
            class="alignment-state {alignmentStatus}"
            disabled={$checkingProgress.running || $editSaving || Boolean($recheckingKey)}
            title={$checkingProgress.running || $editSaving || $recheckingKey
              ? "Wait for background checking to finish before aligning"
              : `Alignment: ${alignmentStatus} — click to open Align Words`}
            aria-label={`Alignment ${alignmentStatus} for verse ${v}. Open Align Words.`}
            on:click|stopPropagation={() => openAlignmentFromList(v)}
            on:dblclick|stopPropagation
          >⇄</button>
        </div>
      {/if}
    </div>
  {/each}

  {#if $verseNums.length === 0}
    <p class="empty">No verses loaded for this chapter yet.</p>
  {/if}
</div>

{#if contextNotice}
  <p class="context-notice" class:error={contextNoticeError} role="status">{contextNotice}</p>
{/if}

{#if $houseStyleNotice}
  <!-- The learner (layered-rules 6.4): shown for the session; Undo supersedes it. -->
  <p class="context-notice house-style" role="status">
    Learned: “{$houseStyleNotice.word}” is house style for {$houseStyleNotice.ruleId} in this book
    ({$houseStyleNotice.evidence.length} ignores).
    <button type="button" class="notice-undo" on:click={() => $houseStyleNotice && void undoLearned($houseStyleNotice)}>Undo</button>
    <button type="button" class="notice-undo" on:click={() => houseStyleNotice.set(null)} aria-label="Dismiss">✕</button>
  </p>
{/if}

{#if contextMenu}
  <FindingContextMenu
    x={contextMenu.x}
    y={contextMenu.y}
    findingLabel="Actions for {contextMenu.finding.explanation}"
    actions={contextActions}
    on:action={onContextAction}
    on:close={() => (contextMenu = null)}
  />
{/if}

{#if verseMenu}
  <FindingContextMenu
    x={verseMenu.x}
    y={verseMenu.y}
    findingLabel="Actions for verse {verseMenu.verse}"
    actions={verseMenuActions}
    on:action={onVerseContextAction}
    on:close={() => (verseMenu = null)}
  />
{/if}

{#if mixedMenu}
  <FindingContextMenu
    x={mixedMenu.x}
    y={mixedMenu.y}
    findingLabel="Findings on this text"
    actions={mixedMenuActions}
    on:action={onMixedMenuAction}
    on:close={() => (mixedMenu = null)}
  />
{/if}

{#if langQaContextMenu}
  <FindingContextMenu
    x={langQaContextMenu.x}
    y={langQaContextMenu.y}
    findingLabel="Actions for {langQaContextMenu.finding.originalText}"
    actions={langQaContextActions}
    on:action={onLangQaContextAction}
    on:close={() => (langQaContextMenu = null)}
  />
{/if}

{#if historyFor}
  <LanguageQaHistoryPopup
    projectPath={historyFor.projectPath}
    chapter={historyFor.chapter}
    verse={historyFor.verse}
    onClose={() => (historyFor = null)}
  />
{/if}

{#if openNotes}
  <VerseNotesPopup
    kind={openNotes.kind}
    notes={openNotes.notes}
    reference={openNotes.reference}
    onClose={() => (openNotes = null)}
  />
{/if}

<style>
  .editor-scroll { flex: 1; overflow-y: auto; padding: 22px 32px; background: var(--surface); }
  .chapter-label { font-size: var(--fs-xs); font-weight: 700; color: var(--text-3); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 14px; }
  /* A heading is the team's text, so it uses --font-target like the verses, but
     it is editorial rather than Scripture: no hover, no selection, no findings.
     The extra top margin is what separates one section from the last verse of
     the previous one. */
  .section-heading {
    font-family: var(--font-target);
    font-size: var(--fs-lg);
    font-weight: 700;
    color: var(--text-2, var(--text-3));
    margin: 20px 0 8px;
    padding: 0 10px;
    line-height: 1.6;
  }
  .section-heading:first-child { margin-top: 0; }
  .section-heading.major { font-size: var(--fs-xl); text-align: center; }
  .verse { display: flex; gap: 10px; padding: 9px 10px; border-radius: 7px; margin-bottom: 2px; cursor: pointer; border: 1px solid transparent; }
  .verse:hover { background: var(--surface-2); }
  .verse.active { background: var(--accent-bg); border-color: #C7D9FB; }
  .vnum { font-size: var(--fs-xs); font-weight: 700; color: var(--text-3); width: 26px; flex-shrink: 0; padding-top: 2px; }
  .verse.approved .vnum { color: var(--success); }
  .verse.check-failed .vnum { color: var(--danger, #ef4444); }
  /* --font-target rather than plain inheritance so the leading below can be
     tuned for Indic ascender/descender depth without moving the UI chrome. */
  .vtext { font-family: var(--font-target); font-size: var(--fs-xl); line-height: 1.85; color: var(--text); }
  .finding-num { font-size: var(--fs-2xs); font-weight: 700; color: var(--accent); margin-left: 1px; }
  /* Where Shift+F10 would open the menu. A visible ring, not colour alone:
     the underline classes already carry the finding's source colour. */
  mark.active-finding { outline: 2px solid var(--accent); outline-offset: 1px; border-radius: 2px; }
  /* Sits inline where the note was, like a printed Bible's callout. A plain
     letter, not an icon font: an offline PyInstaller build can't reach a CDN
     and icon-only controls render as empty boxes there. */
  .note-btn {
    font: inherit; font-size: var(--fs-2xs); font-weight: 800; font-style: italic; line-height: 1;
    vertical-align: super; margin: 0 1px; padding: 1px 3px; cursor: pointer;
    border: 1px solid var(--border-strong); border-radius: 3px;
    background: var(--surface-2); color: var(--text-2);
  }
  .note-btn:hover { background: var(--accent-bg); border-color: var(--accent); color: var(--accent); }
  .note-btn.xref { color: var(--accent); }
  /* Edit pencil stacked above the alignment arrow (issue #73), so a reviewer
     working from the alignment control has an edit entry point without
     leaving it. The column carries the margin-left:auto that used to sit on
     the glyph itself. */
  .row-actions {
    margin-left: auto; flex-shrink: 0; align-self: flex-start; margin-top: 3px;
    display: flex; flex-direction: column; align-items: center; gap: 1px;
  }
  /* U+270E is emoji-presentation-capable, so Windows resolves it through Segoe
     UI Emoji and renders a filled colour glyph next to the arrow's thin
     monochrome one -- they read as two different weights. The U+FE0E variation
     selector on the character asks for text presentation; `font-variant-emoji`
     says the same thing for renderers that honour it. Both are harmless where
     unsupported. */
  .alignment-state, .edit-pencil {
    font: inherit; font-size: var(--fs-md); line-height: 1; color: var(--text-3);
    font-variant-emoji: text;
    background: transparent; border: 1px solid transparent; border-radius: 5px;
    padding: 1px 4px; cursor: pointer;
  }
  .alignment-state:hover, .alignment-state:focus-visible,
  .edit-pencil:hover, .edit-pencil:focus-visible { background: var(--surface-2); border-color: var(--border-strong); outline: none; }
  .alignment-state:disabled, .edit-pencil:disabled { opacity: .55; cursor: not-allowed; }
  .edit-pencil:hover:not(:disabled), .edit-pencil:focus-visible { color: var(--accent); }
  .alignment-state.complete { color: var(--success); }
  .alignment-state.partial { color: var(--warning); }
  .alignment-state.invalid { color: var(--danger); font-weight: 800; }
  .verse.multi { box-shadow: inset 3px 0 0 var(--accent); background: var(--accent-bg); }
  .empty { color: var(--text-3); font-size: var(--fs-md); }
  .context-notice {
    position: fixed; left: 50%; bottom: 34px; z-index: 9000; transform: translateX(-50%);
    margin: 0; padding: 7px 11px; border-radius: 6px; background: var(--success-bg);
    color: var(--success); font-size: var(--fs-sm); box-shadow: 0 4px 14px rgba(15, 23, 42, .18);
  }
  .context-notice.error { background: var(--danger-bg, #fef2f2); color: var(--danger, #b91c1c); }
  /* Above the ordinary notice, so both can show at once. */
  .context-notice.house-style { bottom: 72px; background: var(--surface); color: var(--text); border: 1px solid var(--border); }
  .notice-undo { margin-left: 8px; border: none; background: none; color: var(--accent); cursor: pointer; font-size: var(--fs-sm); text-decoration: underline; }
  /* The row being edited is still .active, which paints its own border --
     nested inside the textarea's it read as a double outline. The
     textarea is the only box while editing. Equal specificity to
     .verse.active and declared after it, so this wins. */
  .verse.editing-row { cursor: default; background: var(--surface); border-color: transparent; }
  .vedit { flex: 1; min-width: 0; cursor: default; }
  .vedit-row { display: flex; align-items: flex-start; gap: 8px; }
  .vedit textarea {
    /* Same face, size and leading as .vtext above: entering edit mode must not
       reflow the verse. The line-height was 1.7 and is now matched to 1.85. */
    flex: 1; min-width: 0; box-sizing: border-box; font-size: var(--fs-xl); line-height: 1.85; color: var(--text);
    font-family: var(--font-target); padding: 10px 12px; border: 1px solid var(--border-strong); border-radius: 8px;
    resize: none; overflow-y: hidden;
  }
  /* Neutral, not accent: the blue box read as a validation state. The focus
     ring is replaced rather than simply removed -- a bare `outline: none`
     would leave keyboard users with no indication of where they are. */
  .vedit textarea:focus { outline: none; border-color: var(--text-3); }
  .vedit textarea:disabled { opacity: .6; }
  .edit-error { color: var(--danger); font-size: var(--fs-xs); margin: 6px 0 0; line-height: 1.4; }
  .edit-actions { display: flex; flex-direction: column; gap: 6px; flex-shrink: 0; }
  .icon-btn {
    width: 32px; height: 32px; padding: 0; font-size: var(--fs-xl); font-weight: 800; border-radius: 7px;
    border: none; cursor: pointer; display: flex; align-items: center; justify-content: center;
  }
  .icon-btn.save { background: var(--accent); color: white; }
  .icon-btn.cancel { background: var(--surface-2); color: var(--text-2); border: 1px solid var(--border-strong); }
  .icon-btn:disabled { opacity: .55; cursor: not-allowed; }
  .spin-sm { width: 12px; height: 12px; border-radius: 50%; border: 2px solid rgba(255,255,255,0.4); border-top-color: #fff; animation: spin-sm 0.8s linear infinite; }
  @keyframes spin-sm { to { transform: rotate(360deg); } }
</style>
