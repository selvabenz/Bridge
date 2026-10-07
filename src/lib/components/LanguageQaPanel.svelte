<script lang="ts">
  import { onDestroy, tick } from "svelte";
  import { bridge } from "../api/bridgeClient";
  import { languageQaChannel, nudgeLanguageQa } from "../languageQaInline";
  import type { BookWordRow, LanguageQaFlag, LanguageQaStatus, LanguageQaView, LearnedFix } from "../types/languageQa";
  import type { HouseStyleEntry } from "../types/houseStyle";
  import { addProjectWords } from "../projectWords";
  import { deleteFlag, setFlagStatus } from "../flags";
  import LanguageQaHistoryList from "./LanguageQaHistoryList.svelte";
  import type { VerseHistoryEntry } from "../types/finding";
  import { graphemeDiff } from "../utils/unicodeDiff";
  import { LANGUAGE_QA_CATEGORY_LABELS, LANGUAGE_QA_CATEGORY_MARKS } from "../utils/highlight";
  import {
    activeLanguageQaFindingId, chapterVerseNums, currentChapter, languageQaFindingsByVerse, project, selectedVerse, verseKey,
  } from "../stores";
  import { editingChapter } from "../verseEditor";
  import type { LanguageQaCategory, LanguageQaFinding } from "../types/languageQa";

  export let projectPath: string;
  export let onNavigate: (book: string, chapter: string, verse: string) => void;

  let expanded = false;
  // Which list is paged: every open finding; those shown again because the
  // rule changed since they were ignored; or those marked as false positives.
  let view: LanguageQaView = "findings";
  // Which part of the book the list covers, and which kinds it shows (the
  // legend; none ticked = every kind).
  type ListScope = "verse" | "chapter" | "book";
  let listScope: ListScope = "book";
  let shownCategories: string[] = [];
  let verseList: LanguageQaFinding[] = [];
  // F8 / Shift+F8: where the walk stands, said for screen readers.
  let walkStatus = "";
  let pendingStep: { chapter: string; step: 1 | -1 } | null = null;
  // The panel's own tabs. "findings" holds the three engine lists above; the
  // others are the reviewer's own data (learned fixes in Dictionary).
  type PanelTab = "findings" | "bookWords" | "flags" | "edits" | "dictionary";
  let panelTab: PanelTab = "findings";
  let learned: LearnedFix[] = [];
  let learnedOn = true;
  let learnedLoaded = false;
  let learnedBusy = false;
  let learnedError = "";
  // Book words: the book's words outside the dictionary (last pass).
  let bookWords: BookWordRow[] = [];
  let bookWordsReason = "";
  let bookWordsLoaded = false;
  let bookWordsBusy = false;
  let bookWordsNotice = "";
  let ticked = new Set<string>();
  let tickAtLeast = 5;
  let wordScope: "book" | "project" = "book";
  // Dictionary: the project word list.
  let projectWords: HouseStyleEntry[] = [];
  let flags: LanguageQaFlag[] = [];
  let flagsLoaded = false;
  let flagsError = "";
  let showResolved = false;
  $: listedFlags = showResolved ? flags : flags.filter((f) => f.status === "open");
  // Edits: the book's recorded Scripture edits, newest first, by day.
  let edits: VerseHistoryEntry[] = [];
  let editsTotal = 0;
  let editsLoaded = false;
  let editsError = "";
  $: editDays = groupByDay(edits);
  // Finding ids whose decision history is expanded.
  let historyOpen = new Set<string>();
  let offset = 0;
  let sequence = 0;
  let disposed = false;
  let busy = false;
  let localError = "";
  // The page on show while expanded, fetched on demand (see below).
  let page: LanguageQaStatus | null = null;
  // The coverage headings are bilingual only when the statement itself is
  // (Tamil); a profile pack's statement is English.
  $: tamilCoverage = Boolean(status?.coverage?.inScope?.some((row) => row.labelTa));
  const detectionLabels: Record<string, string> = {
    metadata: "Language supplied by the project.",
    "script-suggestion": "Language suggested from the script; the project has no declared language.",
    "metadata-conflict": "The project language and detected script disagree. Only common checks are enabled.",
    "mixed-script": "The sample contains mixed scripts. Only common checks are enabled.",
    undetermined: "The language could not be determined. Only common checks are enabled.",
  };

  // The panel does not poll. Its state and totals come from the one Language
  // QA status channel (languageQaInline.ts), which backs off when idle. It
  // fetches rows only while expanded, and only when there is something new to
  // show: it is opened, paged, switched to another list, or a new pass lands
  // (a changed generation or state). It never feeds the verse marks; those
  // come from languageQa.inline, unpaged.
  $: channel = $languageQaChannel.projectPath === projectPath ? $languageQaChannel : null;
  $: live = channel?.status ?? null;
  $: error = localError || channel?.error || "";
  $: status = expanded ? (page ?? live) : live;
  $: scopeChapter = listScope === "book" ? "" : $currentChapter;
  $: pageKey = expanded && live && listScope !== "verse"
    ? `${view}|${offset}|${live.generation}|${live.state}|${scopeChapter}|${shownCategories.join(",")}` : "";
  $: verseKeyNow = expanded && live && listScope === "verse" && $selectedVerse
    ? `${live.generation}|${$currentChapter}|${$selectedVerse}` : "";
  $: if (verseKeyNow) void loadVerseList(verseKeyNow, $currentChapter, $selectedVerse ?? "");
  // The verse scope reads languageQa.verse; its kinds are counted here.
  $: verseCounts = verseList.reduce<Record<string, number>>((counts, f) => {
    if (f.category) counts[f.category] = (counts[f.category] ?? 0) + 1;
    return counts;
  }, {});
  $: legendCounts = listScope === "verse" ? verseCounts : (status?.categoryCounts ?? {});
  $: listed = listScope === "verse"
    ? verseList.filter((f) => !shownCategories.length || shownCategories.includes(f.category ?? ""))
    : (status?.findings ?? []);
  $: if (pageKey) void loadPage(pageKey);
  $: if (!expanded) page = null;

  async function loadPage(key: string): Promise<void> {
    const ticket = ++sequence;
    const path = projectPath;
    busy = true;
    try {
      const filters: { chapter?: string; categories?: string[] } = {};
      if (scopeChapter) filters.chapter = scopeChapter;
      if (shownCategories.length) filters.categories = shownCategories;
      const next = Object.keys(filters).length
        ? await bridge.languageQaStatus(path, offset, 50, view, filters)
        : await bridge.languageQaStatus(path, offset, 50, view);
      if (disposed || ticket !== sequence || key !== pageKey || next.projectPath !== path) return;
      if (page && next.generation !== page.generation && offset !== 0) {
        offset = 0;  // a new pass: start again from page one
        return;
      }
      page = next;
      localError = "";
    } catch (cause) {
      if (!disposed && ticket === sequence) localError = cause instanceof Error ? cause.message : String(cause);
    } finally {
      if (ticket === sequence) busy = false;
    }
  }

  async function togglePause(): Promise<void> {
    if (busy || !live) return;
    busy = true;
    const path = projectPath;
    try {
      const next = await bridge.languageQaPause(path, live.state !== "paused");
      if (disposed || projectPath !== path || next.projectPath !== path) return;
      languageQaChannel.set({ projectPath: path, status: { ...next, findings: [] }, error: "" });
      offset = 0;
      localError = "";
    } catch (cause) {
      localError = cause instanceof Error ? cause.message : String(cause);
    } finally {
      busy = false;
      nudgeLanguageQa();
    }
  }

  function toggle(): void {
    expanded = !expanded;
    offset = 0;
  }

  function turnPage(delta: number): void {
    offset = Math.max(0, offset + delta);
  }

  async function loadVerseList(key: string, chapter: string, verse: string): Promise<void> {
    try {
      const result = await bridge.languageQaVerse(projectPath, chapter, verse);
      if (!disposed && key === verseKeyNow) verseList = result.findings;
    } catch (cause) {
      if (!disposed) localError = cause instanceof Error ? cause.message : String(cause);
    }
  }

  function showScope(next: ListScope): void {
    listScope = next;
    offset = 0;
  }

  const markClass = (category: string): string =>
    LANGUAGE_QA_CATEGORY_MARKS[category as LanguageQaCategory] ?? "m-lqa-typo";
  const categoryLabel = (category: string): string =>
    LANGUAGE_QA_CATEGORY_LABELS[category as LanguageQaCategory] ?? category;

  function toggleCategory(category: string): void {
    shownCategories = shownCategories.includes(category)
      ? shownCategories.filter((c) => c !== category) : [...shownCategories, category];
    offset = 0;
  }

  /** The marks of one chapter in reading order (the ones drawn in the text),
   * narrowed to the legend's kinds when any are ticked. */
  function chapterMarks(chapter: string): LanguageQaFinding[] {
    const marks: LanguageQaFinding[] = [];
    for (const verse of $chapterVerseNums[chapter] ?? []) {
      const found = ($languageQaFindingsByVerse[verseKey(chapter, verse)] ?? [])
        .filter((f) => !shownCategories.length || shownCategories.includes(f.category ?? ""));
      marks.push(...[...found].sort((a, b) => a.start - b.start || a.id.localeCompare(b.id)));
    }
    return marks;
  }

  async function goToMark(finding: LanguageQaFinding, index: number, total: number): Promise<void> {
    selectedVerse.set(finding.verse);
    activeLanguageQaFindingId.set(finding.id);
    walkStatus = `Finding ${index + 1} of ${total} in chapter ${finding.chapter}: ${finding.originalText}`;
    await tick();
    const mark = document.querySelector<HTMLElement>(`[data-finding-ids~="${CSS.escape(finding.id)}"]`);
    if (mark && typeof mark.scrollIntoView === "function") mark.scrollIntoView({ block: "nearest" });
  }

  /** F8: the next mark in reading order; Shift+F8: the previous. Past the end
   * of the chapter it opens the next (or previous) chapter, and lands on its
   * first (or last) mark when they arrive. */
  function step(direction: 1 | -1): void {
    const chapter = $currentChapter;
    const marks = chapterMarks(chapter);
    const at = marks.findIndex((f) => f.id === $activeLanguageQaFindingId);
    const next = at === -1 ? (direction === 1 ? 0 : marks.length - 1) : at + direction;
    if (next >= 0 && next < marks.length) {
      pendingStep = null;
      void goToMark(marks[next], next, marks.length);
      return;
    }
    const chapters = $project?.chapters ?? [];
    const target = chapters[chapters.indexOf(chapter) + direction];
    if (!target) {
      walkStatus = direction === 1 ? "No more findings: this is the end of the book." : "No earlier findings: this is the start of the book.";
      return;
    }
    activeLanguageQaFindingId.set(null);
    pendingStep = { chapter: target, step: direction };
    walkStatus = `Chapter ${target}…`;
    onNavigate(status?.book ?? $project?.bookId ?? "", target, "");
  }

  // A chapter F8 moved to: land on its first (or last) mark once they arrive.
  $: if (pendingStep && $currentChapter === pendingStep.chapter && $languageQaFindingsByVerse) {
    const marks = chapterMarks(pendingStep.chapter);
    if (marks.length) {
      const index = pendingStep.step === 1 ? 0 : marks.length - 1;
      pendingStep = null;
      void goToMark(marks[index], index, marks.length);
    }
  }

  function onWindowKeydown(event: KeyboardEvent): void {
    if (event.key !== "F8" || event.ctrlKey || event.altKey || event.metaKey || !$project) return;
    const target = event.target instanceof Element ? event.target : null;
    if (target?.closest("input, textarea, select, [contenteditable='true']")) return;
    if ($editingChapter || document.querySelector("[role='dialog']")) return;
    event.preventDefault();
    step(event.shiftKey ? -1 : 1);
  }

  function showView(next: LanguageQaView): void {
    if (view === next) return;
    view = next;
    offset = 0;
  }

  function showPanelTab(next: PanelTab): void {
    panelTab = next;
    if (next === "dictionary") {
      void loadLearned();
      void loadProjectWords();
    }
    if (next === "bookWords") void loadBookWords();
    if (next === "flags") void loadFlags();
    if (next === "edits") void loadEdits();
  }

  async function loadEdits(): Promise<void> {
    editsError = "";
    try {
      const result = await bridge.verseHistory();
      edits = result.entries;
      editsTotal = result.total;
    } catch (cause) {
      editsError = cause instanceof Error ? cause.message : String(cause);
    } finally {
      editsLoaded = true;
    }
  }

  /** Entries are newest first already; each day keeps that order. */
  function groupByDay(entries: VerseHistoryEntry[]): { day: string; entries: VerseHistoryEntry[] }[] {
    const days: { day: string; entries: VerseHistoryEntry[] }[] = [];
    for (const entry of entries) {
      const day = new Date(entry.timestamp).toLocaleDateString();
      if (days.length && days[days.length - 1].day === day) days[days.length - 1].entries.push(entry);
      else days.push({ day, entries: [entry] });
    }
    return days;
  }

  async function loadBookWords(): Promise<void> {
    try {
      const result = await bridge.languageQaBookWords(projectPath);
      bookWords = result.words;
      bookWordsReason = result.ready ? "" : result.reason ?? "";
    } catch (cause) {
      bookWordsReason = cause instanceof Error ? cause.message : String(cause);
    } finally {
      bookWordsLoaded = true;
    }
  }

  /** indic-qa's "tick words used at least N times in the IRV". */
  function tickFrequent(): void {
    ticked = new Set(bookWords.filter((w) => w.status !== "added" && w.countIrv >= tickAtLeast).map((w) => w.word));
  }

  function toggleTick(word: string): void {
    const next = new Set(ticked);
    if (!next.delete(word)) next.add(word);
    ticked = next;
  }

  async function addTicked(): Promise<void> {
    if (!ticked.size || bookWordsBusy) return;
    bookWordsBusy = true;
    const words = [...ticked];
    const error = await addProjectWords(words, wordScope, projectPath);
    bookWordsBusy = false;
    if (error) {
      bookWordsNotice = error;
      return;
    }
    bookWords = bookWords.map((w) => (ticked.has(w.word) ? { ...w, status: "added" } : w));
    bookWordsNotice = `${words.length} word${words.length === 1 ? "" : "s"} added to the project word list.`;
    ticked = new Set();
  }

  async function loadProjectWords(): Promise<void> {
    try {
      projectWords = (await bridge.languageQaWordsList(projectPath)).added;
    } catch {
      projectWords = [];
    }
  }

  async function removeProjectWord(entry: HouseStyleEntry): Promise<void> {
    try {
      await bridge.housestyleSetState(entry.key, "removed");
      projectWords = projectWords.filter((e) => e.key !== entry.key);
      nudgeLanguageQa();
    } catch (cause) {
      learnedError = cause instanceof Error ? cause.message : String(cause);
    }
  }

  /** The whole book's flags, fetched when the tab opens. */
  async function loadFlags(): Promise<void> {
    flagsError = "";
    try {
      flags = (await bridge.languageQaFlagsList(projectPath)).flags;
    } catch (cause) {
      flagsError = cause instanceof Error ? cause.message : String(cause);
    } finally {
      flagsLoaded = true;
    }
  }

  async function flagAction(flag: LanguageQaFlag, action: "resolved" | "open" | "delete"): Promise<void> {
    const error = action === "delete" ? await deleteFlag(projectPath, flag) : await setFlagStatus(projectPath, flag, action);
    if (error) {
      flagsError = error;
      return;
    }
    flags = action === "delete" ? flags.filter((f) => f.flagId !== flag.flagId)
      : flags.map((f) => (f.flagId === flag.flagId ? { ...f, status: action } : f));
  }

  /** Fetched when the Dictionary tab opens, never polled. */
  async function loadLearned(): Promise<void> {
    learnedError = "";
    try {
      const result = await bridge.languageQaLearnedList(projectPath);
      learned = result.fixes;
      learnedOn = result.enabled;
    } catch (cause) {
      learnedError = cause instanceof Error ? cause.message : String(cause);
    } finally {
      learnedLoaded = true;
    }
  }

  async function setLearned(fix: LearnedFix, enabled: boolean): Promise<void> {
    if (learnedBusy) return;
    learnedBusy = true;
    learnedError = "";
    try {
      const { fix: updated } = enabled
        ? await bridge.languageQaLearnedRestore(projectPath, fix.old, fix.new)
        : await bridge.languageQaLearnedForget(projectPath, fix.old, fix.new);
      learned = learned.map((f) => (f.old === fix.old && f.new === fix.new ? updated : f));
      nudgeLanguageQa();
    } catch (cause) {
      learnedError = cause instanceof Error ? cause.message : String(cause);
    } finally {
      learnedBusy = false;
    }
  }

  function toggleHistory(id: string): void {
    const next = new Set(historyOpen);
    if (!next.delete(id)) next.add(id);
    historyOpen = next;
  }

  onDestroy(() => {
    disposed = true;
    ++sequence;
  });
</script>

<svelte:window on:keydown={onWindowKeydown} />

<aside class="language-qa" aria-label="Language QA">
  <p class="walk-status" role="status" aria-live="polite">{walkStatus}</p>
  {#if expanded}
    <section id="language-qa-results" aria-label="Language QA results">
      <div class="heading">
        <h2>Language QA · Offline</h2>
        <button on:click={toggle} aria-label="Close Language QA">Close</button>
      </div>
      <p>Checks run automatically for the open book and after edits.</p>
      {#if error}<p role="alert">{error}</p>{/if}
      {#if status}
        <p>
          {status.language?.name || status.language?.language || "Detecting language"}
          {#if status.language} · {status.language.script.toLowerCase()} script{/if}
          {#if status.language && status.language.pack !== "common"} · {status.language.pack} rules{/if}
        </p>
        {#if status.language}<p>{detectionLabels[status.language.basis] ?? ""}</p>{/if}
        {#if status.coverage?.inScope}
          <!-- Always shown, so a clean result is never read as a review (Phase 7). -->
          <div class="scope" aria-label="What Language QA checks">
            <div>
              <h3>Checks{#if tamilCoverage} · <span lang="ta">சரிபார்ப்பவை</span>{/if}</h3>
              <ul>
                {#each status.coverage.inScope as row (row.category)}
                  <li>{row.label}{#if row.labelTa} · <span lang="ta">{row.labelTa}</span>{/if}</li>
                {/each}
              </ul>
            </div>
            <div>
              <h3>Does not check{#if tamilCoverage} · <span lang="ta">சரிபார்க்காதவை</span>{/if}</h3>
              <ul>
                {#each status.coverage.outOfScope as row (row.category)}
                  <li title={row.reason}>
                    {row.label}{#if row.labelTa} · <span lang="ta">{row.labelTa}</span>{/if}
                    <span class="reason">{row.reason}{#if row.reasonTa} <span lang="ta">{row.reasonTa}</span>{/if}</span>
                  </li>
                {/each}
              </ul>
            </div>
            <p class="muted">Who checks these before publication: {status.coverage.handOff}</p>
          </div>
        {/if}
        <p>{status.language?.message ?? (status.state === "paused" ? "Checks paused. Resume when ready." : "Preparing language checks…")}</p>
        <p>
          {status.state} · {status.completedChapters ?? 0}/{status.totalChapters ?? 0} chapters
          · {status.totalFindings} review candidates
        </p>
        {#if status.error}<p role="alert">{status.error}</p>{/if}
        <button on:click={togglePause} disabled={busy || status.state === "failed"}>
          {status.state === "paused" ? "Resume checks" : "Pause checks"}
        </button>
        {#if status.incomplete}
          <p class="notice">Coverage incomplete. Omitted text has not passed QA.</p>
        {/if}
        {#if status.limitations.length}
          <details>
            <summary>Coverage details ({status.limitations.length})</summary>
            <ul>{#each status.limitations as limitation}<li>{limitation}</li>{/each}</ul>
          </details>
        {/if}
        <div class="panel-tabs" role="tablist" aria-label="Language QA panel">
          <button role="tab" aria-selected={panelTab === "findings"} on:click={() => showPanelTab("findings")}>Issues</button>
          <button role="tab" aria-selected={panelTab === "bookWords"} on:click={() => showPanelTab("bookWords")}>Book words</button>
          <button role="tab" aria-selected={panelTab === "flags"} on:click={() => showPanelTab("flags")}>Flags</button>
          <button role="tab" aria-selected={panelTab === "edits"} on:click={() => showPanelTab("edits")}>Edits</button>
          <button role="tab" aria-selected={panelTab === "dictionary"} on:click={() => showPanelTab("dictionary")}>Dictionary</button>
        </div>
        {#if panelTab === "findings"}
        <div class="views" role="tablist" aria-label="Language QA lists">
          <button role="tab" aria-selected={view === "findings"} on:click={() => showView("findings")}>Findings</button>
          <button role="tab" aria-selected={view === "recheck"} on:click={() => showView("recheck")}>
            Re-check ({status.recheckCount ?? 0})
          </button>
          <button role="tab" aria-selected={view === "falsePositives"} on:click={() => showView("falsePositives")}>
            False positives ({status.falsePositiveCount ?? 0})
          </button>
        </div>
        {#if view === "recheck"}
          <p class="muted">Ignored before, under an older version of the rule. Check each one again.</p>
        {:else if view === "falsePositives"}
          <p class="muted">Marked as false positives and hidden from the verse text.</p>
        {/if}
        <div class="scope-row" role="radiogroup" aria-label="Show findings for">
          {#each [["verse", "Verse"], ["chapter", "Chapter"], ["book", "Book"]] as [value, label]}
            <button role="radio" aria-checked={listScope === value} disabled={value === "verse" && view !== "findings"}
              on:click={() => showScope(value === "verse" ? "verse" : value === "chapter" ? "chapter" : "book")}>{label}</button>
          {/each}
          <span class="keys" aria-keyshortcuts="F8 Shift+F8">F8 / Shift+F8: next / previous mark</span>
        </div>
        {#if Object.keys(legendCounts).length}
          <div class="legend" role="group" aria-label="Kinds of finding">
            {#each Object.entries(legendCounts) as [category, count] (category)}
              <button aria-pressed={shownCategories.includes(category)} on:click={() => toggleCategory(category)}
                title={shownCategories.includes(category) ? "Shown; click to stop narrowing to it" : "Show only this kind (and any others ticked)"}>
                <mark class="swatch {markClass(category)}">ab</mark>
                {categoryLabel(category)}
                <span class="n">{count}</span>
              </button>
            {/each}
          </div>
        {/if}
        {#if listScope === "verse" && !$selectedVerse}
          <p class="muted">Select a verse to list its findings.</p>
        {:else if listScope === "verse" && !listed.length}
          <p class="muted">No findings in this verse{shownCategories.length ? " of the kinds ticked" : ""}.</p>
        {/if}
        {#if listScope !== "verse" && view === "findings" && status.state === "completed" && !status.totalFindings}
          <p>No candidates found by the enabled checks. This is not publication approval.</p>
        {/if}
        <ol aria-label={view === "findings" ? "Language QA findings" : view === "recheck" ? "Findings to re-check" : "False positives"} start={listScope === "verse" ? 1 : status.offset + 1}>
          {#each listed as finding (finding.id)}
            <li>
              <button on:click={() => onNavigate(finding.book, finding.chapter, finding.verse)}>
                {finding.book.toUpperCase()} {finding.chapter}:{finding.verse}
              </button>
              <span class="severity">{finding.severity}</span>
              {#if finding.category}<span class="category">{finding.category}</span>{/if}
              {#if finding.previouslyIgnored}<span class="recheck">Re-check</span>{/if}
              {#if finding.context}<span class="where">in {finding.context}</span>{/if}
              <p class="evidence">{finding.originalText}</p>
              {#if finding.context === "heading" && finding.contextText}<p class="muted">Heading: {finding.contextText}</p>{/if}
              <p>{finding.message}</p>
              {#if finding.reference}
                <p class="reference"><b>{finding.reference.label} {finding.reference.ref}:</b> {finding.reference.text}</p>
              {/if}
              <button class="history-toggle" aria-expanded={historyOpen.has(finding.id)} on:click={() => toggleHistory(finding.id)}>
                History
              </button>
              {#if historyOpen.has(finding.id)}
                <LanguageQaHistoryList {projectPath} chapter={finding.chapter} verse={finding.verse} findingId={finding.id} />
              {/if}
            </li>
          {/each}
        </ol>
        {#if listScope !== "verse" && status.totalFindings > 50}
          <div class="paging">
            <button on:click={() => turnPage(-50)} disabled={busy || offset === 0}>Previous</button>
            <span>{status.offset + 1}–{Math.min(status.offset + 50, status.totalFindings)} of {status.totalFindings}</span>
            <button on:click={() => turnPage(50)} disabled={busy || offset + 50 >= status.totalFindings}>Next</button>
          </div>
        {/if}
        {:else if panelTab === "bookWords"}
          <section aria-label="Book words" class="book-words">
            <p class="muted">Words in this book that are not in the dictionary, most used first. Add the ones that are right
              to the project word list; they stop being reported as spelling problems. Nothing here changes the text.</p>
            {#if !bookWordsLoaded}
              <p class="muted">Loading…</p>
            {:else if bookWordsReason}
              <p class="muted">{bookWordsReason}</p>
            {:else if !bookWords.length}
              <p class="muted">Every word of this book is in the dictionary.</p>
            {:else}
              <div class="tick-row">
                <label>Tick words used at least
                  <input type="number" min="1" bind:value={tickAtLeast} aria-label="Minimum uses in the IRV" /> times in the IRV</label>
                <button on:click={tickFrequent}>Tick</button>
                <button on:click={() => (ticked = new Set())}>Untick all</button>
              </div>
              <div class="table-box">
                <table>
                  <thead><tr><th></th><th>Word</th><th>In book</th><th>IRV</th><th>OV</th><th>Status</th></tr></thead>
                  <tbody>
                    {#each bookWords as row (row.word)}
                      <tr class:added={row.status === "added"}>
                        <td><input type="checkbox" aria-label={`Tick ${row.word}`} checked={ticked.has(row.word)}
                          disabled={row.status === "added"} on:change={() => toggleTick(row.word)} /></td>
                        <td class="word">
                          <button class="link" on:click={() => onNavigate(status?.book ?? "", row.firstRef.chapter, row.firstRef.verse)}
                            title="Open its first verse">{row.word}</button>
                        </td>
                        <td>{row.countBook}</td><td>{row.countIrv}</td><td>{row.countOv}</td>
                        <td>{row.status === "added" ? "in word list" : row.status === "irv_ok" ? "IRV word" : row.status}</td>
                      </tr>
                    {/each}
                  </tbody>
                </table>
              </div>
              <div class="tick-row">
                <span class="muted">{ticked.size} ticked</span>
                <select bind:value={wordScope} aria-label="Where the words apply">
                  <option value="book">this book</option>
                  <option value="project">every book of the project</option>
                </select>
                <button on:click={addTicked} disabled={!ticked.size || bookWordsBusy}>Add to the project word list</button>
              </div>
              {#if bookWordsNotice}<p class="muted" role="status">{bookWordsNotice}</p>{/if}
            {/if}
          </section>
        {:else if panelTab === "flags"}
          <section aria-label="Flags" class="flags">
            <p class="muted">Questions reviewers raised on the text (⚑). A flag never changes the verse.</p>
            <label class="toggle"><input type="checkbox" bind:checked={showResolved} /> Show resolved</label>
            {#if flagsError}<p role="alert">{flagsError}</p>{/if}
            {#if !flagsLoaded}
              <p class="muted">Loading…</p>
            {:else if !listedFlags.length}
              <p class="muted">No {showResolved ? "" : "open "}flags in this book. Right-click a word or a verse to flag it.</p>
            {:else}
              <ol>
                {#each listedFlags as flag (flag.flagId)}
                  <li class:resolved={flag.status === "resolved"}>
                    <button on:click={() => onNavigate(status?.book ?? "", flag.chapter, flag.verse)}>
                      {(status?.book ?? "").toUpperCase()} {flag.chapter}:{flag.verse}{flag.verseEnd ? `–${flag.verseEnd}` : ""}
                    </button>
                    <span class="category">⚑ {flag.type}</span>
                    <span class="severity">{flag.status}</span>
                    <p class="evidence">{flag.text}</p>
                    {#if flag.note}<p>{flag.note}</p>{/if}
                    {#if flag.suggested}<p>Suggested: <b>{flag.suggested}</b></p>{/if}
                    <p class="muted">{flag.reviewer} · {new Date(flag.createdAt).toLocaleDateString()}</p>
                    {#if flag.status === "open"}
                      <button on:click={() => flagAction(flag, "resolved")}>Resolve</button>
                    {:else}
                      <button on:click={() => flagAction(flag, "open")}>Reopen</button>
                    {/if}
                    <button on:click={() => flagAction(flag, "delete")}>Delete</button>
                  </li>
                {/each}
              </ol>
            {/if}
          </section>
        {:else if panelTab === "edits"}
          <section aria-label="Edits" class="edits">
            <p class="muted">Every recorded change to this book's text, newest first. Open a verse's ↺ to restore an earlier wording.</p>
            {#if editsError}<p role="alert">{editsError}</p>{/if}
            {#if !editsLoaded}
              <p class="muted">Loading…</p>
            {:else if !edits.length}
              <p class="muted">No edits recorded in this book.</p>
            {:else}
              {#if editsTotal > edits.length}<p class="muted">The newest {edits.length} of {editsTotal}.</p>{/if}
              {#each editDays as day (day.day)}
                <h3>{day.day}</h3>
                <ol>
                  {#each day.entries as entry, index (entry.timestamp + entry.chapter + entry.verse + index)}
                    <li>
                      <button on:click={() => onNavigate(status?.book ?? "", entry.chapter, entry.verse)}>
                        {(status?.book ?? "").toUpperCase()} {entry.chapter}:{entry.verse}
                      </button>
                      <span class="category">{entry.username || "unknown"}</span>
                      <span class="severity">{new Date(entry.timestamp).toLocaleTimeString()}</span>
                      {#if entry.batchId}<span class="severity">{entry.undoes ? "undo of a change set" : "change set"}</span>{/if}
                      <p class="evidence">{#each graphemeDiff(entry.plainBefore, entry.plainAfter) as part}{#if part.kind === "removed"}<del>{part.text}</del>{:else if part.kind === "inserted"}<ins>{part.text}</ins>{:else}{part.text}{/if}{/each}</p>
                    </li>
                  {/each}
                </ol>
              {/each}
            {/if}
          </section>
        {:else if panelTab === "dictionary"}
          <section aria-label="Project words" class="dictionary">
            <h3>Project words</h3>
            {#if !projectWords.length}
              <p class="muted">None yet. Add words from Book words or a word's menu.</p>
            {:else}
              <ul class="words">
                {#each projectWords as entry (entry.key)}
                  <li><span class="word">{entry.word}</span> <span class="muted">{entry.scope === "word-in-project" ? "every book" : "this book"}</span>
                    <button on:click={() => removeProjectWord(entry)}>Remove</button></li>
                {/each}
              </ul>
            {/if}
          </section>
          <section aria-label="Learned fixes" class="dictionary">
            <h3>Learned fixes</h3>
            <p class="muted">
              A word you replaced is offered again where it recurs, in this book and the project's other books (blue dotted). Forget applies to every book.
              {#if !learnedOn}Learned fixes are off in Settings › Language QA.{/if}
            </p>
            {#if learnedError}<p role="alert">{learnedError}</p>{/if}
            {#if !learnedLoaded}
              <p class="muted">Loading…</p>
            {:else if !learned.length}
              <p class="muted">Nothing learned yet. Replace one word in a verse and it appears here.</p>
            {:else}
              <table>
                <thead><tr><th>Replaced</th><th>With</th><th>Times</th><th>Books</th><th>Last at</th><th></th></tr></thead>
                <tbody>
                  {#each learned as fix (`${fix.old} → ${fix.new}`)}
                    <tr class:forgotten={!fix.enabled || fix.count <= 0}>
                      <td class="word">{fix.old}</td>
                      <td class="word">{fix.new}</td>
                      <td>{Math.max(0, fix.count)}</td>
                      <td>{(fix.books ?? []).map((b) => b.toUpperCase()).join(", ")}</td>
                      <td>{fix.lastRef}</td>
                      <td>
                        {#if fix.enabled}
                          <button disabled={learnedBusy} on:click={() => setLearned(fix, false)}>Forget</button>
                        {:else}
                          <button disabled={learnedBusy} on:click={() => setLearned(fix, true)}>Restore</button>
                        {/if}
                      </td>
                    </tr>
                  {/each}
                </tbody>
              </table>
            {/if}
          </section>
        {/if}
        <p class="muted">{status.coverage?.summary ?? ""} {status.storage}</p>
      {/if}
    </section>
  {/if}
  <button class="launcher" on:click={toggle} aria-expanded={expanded} aria-controls="language-qa-results">
    Language QA · {error ? "unavailable" : live?.state ?? "starting"}
    {#if live?.totalFindings} · {live.totalFindings}{/if}
  </button>
</aside>

<style>
  .language-qa { position: fixed; bottom: 40px; right: 16px; z-index: 45; font-size: 12px; }
  section { width: min(560px, calc(100vw - 32px)); max-height: min(580px, calc(100vh - 150px)); overflow: auto; padding: 16px; background: var(--surface); color: var(--text); border: 1px solid var(--border); border-radius: 8px; box-shadow: 0 4px 24px #0003; margin-bottom: 8px; }
  .heading, .paging { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
  h2 { font-size: 15px; margin: 0; }
  button { cursor: pointer; border: 1px solid var(--border); border-radius: 4px; padding: 4px 8px; background: var(--surface); color: inherit; }
  button:disabled { opacity: .5; cursor: default; }
  .launcher { display: block; margin-left: auto; }
  p { margin: 8px 0; overflow-wrap: anywhere; }
  ol { padding-left: 22px; }
  li { padding: 8px 0; border-bottom: 1px solid var(--border, #ddd); }
  .evidence { font-size: 16px; white-space: pre-wrap; }
  .severity, .category { margin-left: 8px; }
  .recheck { margin-left: 8px; font-weight: 600; color: var(--warning); }
  .where { margin-left: 8px; font-style: italic; }
  .reference { font-size: 12px; opacity: .85; border-left: 2px solid var(--border); padding-left: 6px; }
  .views { display: flex; gap: 6px; margin: 8px 0; }
  .panel-tabs { display: flex; gap: 6px; margin: 10px 0 2px; padding-bottom: 6px; border-bottom: 1px solid var(--border); flex-wrap: wrap; }
  .panel-tabs button[aria-selected="true"] { border-color: var(--accent); background: var(--accent-bg); font-weight: 600; }
  .dictionary h3 { font-size: 13px; margin: 10px 0 4px; }
  .flags .toggle { display: flex; gap: 6px; align-items: center; }
  .book-words .tick-row { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin: 6px 0; }
  .book-words input[type="number"] { width: 48px; }
  .book-words .table-box { max-height: 260px; overflow: auto; border: 1px solid var(--border); border-radius: 6px; }
  .book-words table { width: 100%; border-collapse: collapse; }
  .book-words th { position: sticky; top: 0; background: var(--surface); text-align: left; font-size: 11px;
    color: var(--text-3); padding: 4px; border-bottom: 1px solid var(--border); }
  .book-words td { padding: 3px 4px; border-bottom: 1px solid var(--border); }
  .book-words td.word { font-family: var(--font-target); font-size: 15px; }
  .book-words tr.added td { opacity: .55; }
  .link { border: 0; background: none; padding: 0; font: inherit; color: inherit; text-decoration: underline dotted; }
  .dictionary .words { list-style: none; padding: 0; margin: 0; }
  .dictionary .words li { display: flex; gap: 8px; align-items: center; padding: 3px 0; border: 0; }
  .dictionary .words .word { font-family: var(--font-target); font-size: 15px; }
  .flags li.resolved { opacity: .7; }
  .scope-row { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; margin: 6px 0; }
  .scope-row button[aria-checked="true"] { border-color: var(--accent); background: var(--accent-bg); font-weight: 600; }
  .scope-row .keys { margin-left: auto; font-size: 11px; color: var(--text-3); }
  .legend { display: flex; flex-wrap: wrap; gap: 4px; margin: 4px 0 8px; }
  .legend button { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; padding: 2px 8px; }
  .legend button[aria-pressed="true"] { border-color: var(--accent); background: var(--accent-bg); }
  .legend .swatch { background: none; padding: 0 2px; font-size: 12px; }
  .legend .n { color: var(--text-3); font-variant-numeric: tabular-nums; }
  .walk-status { margin: 0; font-size: 11px; color: var(--text-2); }
  .walk-status:empty { display: none; }
  .edits h3 { font-size: 13px; margin: 10px 0 2px; }
  .edits del { background: var(--danger-bg); color: var(--danger); }
  .edits ins { background: var(--success-bg); color: var(--success); text-decoration: none; }
  .dictionary table { width: 100%; border-collapse: collapse; }
  .dictionary th { text-align: left; font-size: 11px; color: var(--text-3); padding: 4px; border-bottom: 1px solid var(--border); }
  .dictionary td { padding: 4px; border-bottom: 1px solid var(--border); }
  .dictionary td.word { font-family: var(--font-target); font-size: 15px; }
  .dictionary tr.forgotten td { opacity: .55; }
  .dictionary tr.forgotten td.word { text-decoration: line-through; }
  .views button[aria-selected="true"] { border-color: var(--accent); background: var(--accent-bg); }
  .history-toggle { font-size: 11px; padding: 2px 6px; }
  .notice { font-weight: 600; }
  .muted { opacity: .75; }
  .scope { display: grid; grid-template-columns: 1fr 1fr; gap: 4px 12px; margin: 8px 0; padding: 8px; border: 1px solid var(--border); border-radius: 6px; }
  .scope h3 { font-size: 12px; margin: 0 0 4px; }
  .scope ul { margin: 0; padding-left: 16px; }
  .scope li { padding: 2px 0; border: 0; }
  .scope .reason { display: block; font-size: 11px; opacity: .75; }
  .scope p { grid-column: 1 / -1; margin: 4px 0 0; }
  @media (max-width: 480px) { .scope { grid-template-columns: 1fr; } }
</style>
