<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import { bridge } from "./lib/api/bridgeClient";
  import ImportScreen from "./lib/components/ImportScreen.svelte";
  import TopBar from "./lib/components/TopBar.svelte";
  import VerseList from "./lib/components/VerseList.svelte";
  import ReviewPanel from "./lib/components/ReviewPanel.svelte";
  import LanguageQaPanel from "./lib/components/LanguageQaPanel.svelte";
  import ScopeConfirmDialog from "./lib/components/ScopeConfirmDialog.svelte";
  import ScopeNotice from "./lib/components/ScopeNotice.svelte";
  import { resetScopedApply, scopeDialog } from "./lib/scopedApply";
  import { startFlagLoader } from "./lib/flags";
  import ReferencePanel from "./lib/components/ReferencePanel.svelte";
  import FindingContextMenu from "./lib/components/FindingContextMenu.svelte";
  import { TEXT_SCALES, bumpTextScale, rawView, referencePanelOpen, textScale } from "./lib/editorPrefs";
  import {
    bookmarks, buildPlacesMenu, collectionOf, recentChapters, recordRecentChapter, setPlaces, toggleBookmark,
  } from "./lib/bookmarks";
  import AlignmentReview from "./lib/components/AlignmentReview.svelte";
  import CrossVerseAlignmentModal from "./lib/components/CrossVerseAlignmentModal.svelte";
  import {
    closeCrossVerse, crossVerseAnchor, crossVerseInitialVerses, crossVerseOpen, crossVerseRequest, openCrossVerse,
  } from "./lib/alignmentUi";
  import { resetReviewState } from "./lib/reviewStores";
  import SettingsModal from "./lib/components/SettingsModal.svelte";
  import ExportModal from "./lib/components/ExportModal.svelte";
  import ProjectDashboard from "./lib/components/ProjectDashboard.svelte";
  import DiagnosticsPanel from "./lib/components/DiagnosticsPanel.svelte";
  import ProjectReportScreen from "./lib/components/ProjectReportScreen.svelte";
  import type { AiCheckReview, AlignmentWorkStatus, BookProgressEntry, CheckJobSnapshot, ProjectReport, QaFinding, VerseDisplay, VerseHeading } from "./lib/types/finding";
  import type {
    QaReport, ReportJobSnapshot, TriageJobSnapshot, TriageRecord,
  } from "./lib/types/report";
  import { isTriageUnavailable } from "./lib/types/report";
  import type { TriageOverrideVerdict } from "./lib/types/finding";
  import {
    project, currentChapter, chapterVerseNums, verseTexts, verseDisplay, headingsByVerse, findingsByVerse, historyCountByVerse,
    checkStatusByVerse, alignmentStatusByVerse, loadedChapters, selectedVerse, selectedVerseSet, checkingProgress, approvedCount, verseNums,
    verseKey, settingsOpen, exportOpen, bookApprovedSummary, resetBookState, reviewerMode,
    aiCheckReviewsByVerse, diagnosticsOpen, engineLog, appendEngineLog, navigationStatus,
  } from "./lib/stores";
  import { editingChapter, editingVerse, editSaving } from "./lib/verseEditor";
  import { nudgeLanguageQa, startLanguageQaInline } from "./lib/languageQaInline";
  import { collectionQaRunning, stopCollectionQa } from "./lib/collectionQa";
  import CollectionQaPanel from "./lib/components/CollectionQaPanel.svelte";

  let opened = false;
  let engineStatus: "checking" | "ready" | "error" = "checking";
  let activeJobId = "";
  let monitorGeneration = 0;
  let openingBook = "";
  let bookOpenError = "";
  let droppedPath = "";
  let dropSequence = 0;
  let draggingOver = false;
  let dropError = "";
  let chapterLoadSequence = 0;
  let showingDashboard = false;
  // Alignment Review is a top-level surface alongside the editor, not a
  // replacement for ReviewPanel's per-verse alignment modal.
  let showingAlignmentReview = false;
  let bookProgress: BookProgressEntry[] = [];
  let dashboardLoading = false;
  let dashboardError = "";
  let report: ProjectReport | null = null;
  let reportLoading = false;
  let reportError = "";
  // Whole-collection QA report (TopBar "Generate report" on the project
  // screen). Built by a background sidecar job (report.generate) that is
  // polled here; the finished payload is fetched once and kept for the
  // rest of the session so returning to the screen is instant.
  let showingReport = false;
  let qaReport: QaReport | null = null;
  let qaReportJob: ReportJobSnapshot | null = null;
  let qaReportError = "";
  let qaReportPollTimer: ReturnType<typeof setTimeout> | undefined;
  let qaReportPollGeneration = 0;
  // AI triage overlay on that report. Optional and online-only: with no
  // verdicts and no API key the report screen renders exactly as it did
  // before triage existed.
  let triageEntries: Record<string, TriageRecord> = {};
  let triageJob: TriageJobSnapshot | null = null;
  let triageAvailable = false;
  let triageUnavailableReason = "";
  let triageError = "";
  let triageThreshold: number | null = 90;
  let triagePollTimer: ReturnType<typeof setTimeout> | undefined;
  let triagePollGeneration = 0;
  let engineNotice = "";
  let engineNoticeTimer: ReturnType<typeof setTimeout> | undefined;
  let settingsInitialPane: "ai" | "quality" | "connections" | "resources" | "terminology" | "languageQa" | "security" = "ai";
  let navigationPollInFlight = false;
  let handlingNavigationRequest = "";
  let lastNavigationReference = "";
  let navigationReference = "";
  let navigationRetryTimer: ReturnType<typeof setTimeout> | undefined;
  let navigationRetryReference = "";
  let navigationRetries = 0;

  function openSettings(pane: "ai" | "quality" | "connections" | "resources" | "terminology" | "languageQa" | "security" = "ai"): void {
    settingsInitialPane = pane;
    settingsOpen.set(true);
  }

  function currentBridgeReference(): string {
    if (!$project || !$selectedVerse) return "";
    return `${$project.bookId.toUpperCase()} ${$currentChapter}:${$selectedVerse}`;
  }

  function navigationContext(): string {
    return [
      $project?.path ?? "no-project",
      currentBridgeReference(),
      $editingChapter ? `editing:${$editingChapter}:${$editingVerse}` : "not-editing",
      $editSaving ? "saving" : "idle",
      screen,
    ].join("|");
  }

  // The ★ Bookmarks menu, anchored under its button.
  let placesMenu: { x: number; y: number } | null = null;
  $: currentPlace = $project && $selectedVerse
    ? { collection: collectionOf($project), book: $project.bookId.toLowerCase(), chapter: $currentChapter, verse: $selectedVerse }
    : null;
  $: placesActions = placesMenu ? buildPlacesMenu($bookmarks, $recentChapters, collectionOf($project), currentPlace) : [];

  function openPlacesMenu(event: MouseEvent): void {
    const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
    placesMenu = { x: rect.left, y: rect.bottom + 4 };
  }

  async function onPlacesAction(event: CustomEvent<{ id: string }>): Promise<void> {
    placesMenu = null;
    const [kind, index] = event.detail.id.split(":");
    if (kind === "toggle") {
      if (!currentPlace) return;
      const error = await toggleBookmark(currentPlace);
      if (error) showNavigationNotice(error);
      return;
    }
    const place = kind === "bm" ? $bookmarks[Number(index)] : kind === "recent" ? $recentChapters[Number(index)] : undefined;
    if (place) await navigateToReportRow(place.book, place.chapter, place.verse ?? "");
  }

  function showNavigationNotice(message: string): void {
    if (engineNoticeTimer) clearTimeout(engineNoticeTimer);
    engineNotice = message;
    engineNoticeTimer = setTimeout(() => { engineNotice = ""; }, 10000);
  }

  async function rejectNavigation(requestId: string, message: string): Promise<void> {
    navigationStatus.set(await bridge.navigationResolve(
      requestId, false, currentBridgeReference(), navigationContext(),
    ));
    showNavigationNotice(message);
  }

  async function handleNavigationCandidate(
    candidate: { reference: string; origin: "paratext" | "logos"; requestId: string },
  ): Promise<void> {
    if (handlingNavigationRequest === candidate.requestId) return;
    handlingNavigationRequest = candidate.requestId;
    const source = candidate.origin === "paratext" ? "Paratext" : "Logos";
    try {
      if (!opened || !$project) {
        await rejectNavigation(candidate.requestId, `${source} moved to ${candidate.reference}; open a Bridge project to follow it.`);
        return;
      }
      if ($editingChapter || $editSaving) {
        await rejectNavigation(candidate.requestId, `${source} moved to ${candidate.reference}. Save or cancel the verse edit before following external navigation.`);
        return;
      }
      const match = /^([1-4]?[A-Z]{2,4})\s+(\d+):(\d+[a-z]?)$/i.exec(candidate.reference);
      if (!match) {
        await rejectNavigation(candidate.requestId, `${source} reported an unsupported Scripture reference.`);
        return;
      }
      const [, bookId, chapter, verse] = match;
      const original = {
        path: $project.path, chapter: $currentChapter, verse: $selectedVerse,
        dashboard: showingDashboard, review: showingAlignmentReview,
      };
      const destination = $project.bookId.toUpperCase() === bookId.toUpperCase()
        ? { path: $project.path }
        : $project.importedProjects?.find((book) => book.bookId.toUpperCase() === bookId.toUpperCase());
      if (!destination) {
        await rejectNavigation(candidate.requestId, `${source} moved to ${candidate.reference}, but that book is not in the open Bridge collection.`);
        return;
      }
      if (destination.path !== $project.path && !(await switchBook(destination.path, false))) {
        await rejectNavigation(candidate.requestId, `Bridge could not open the book requested by ${source}: ${bookOpenError || candidate.reference}.`);
        return;
      }
      let valid = $project?.chapters.includes(chapter) ?? false;
      if (valid) {
        await ensureChapterData(chapter);
        valid = ($chapterVerseNums[chapter] ?? []).includes(verse);
      }
      if (!valid) {
        if ($project?.path !== original.path) {
          await switchBook(original.path, false);
          if ($project?.chapters.includes(original.chapter) && original.verse) {
            await activateChapter(original.chapter, original.verse);
          }
          showingDashboard = original.dashboard;
          showingAlignmentReview = original.review;
        }
        await rejectNavigation(candidate.requestId, `${source} moved to ${candidate.reference}, which is not present in this Bridge project.`);
        return;
      }
      showingDashboard = false;
      showingAlignmentReview = false;
      await activateChapter(chapter, verse);
      navigationStatus.set(await bridge.navigationResolve(
        candidate.requestId, true, currentBridgeReference(), navigationContext(),
      ));
    } catch (error) {
      try {
        await rejectNavigation(
          candidate.requestId,
          `Could not follow ${source} navigation: ${error instanceof Error ? error.message : String(error)}`,
        );
      } catch (resolveError) {
        console.error("Could not reject external navigation", resolveError);
      }
    } finally {
      handlingNavigationRequest = "";
    }
  }

  // Refresh the navigation store from the engine without polling the
  // connectors. Used once at engine-ready and after a respawn so the poll
  // gate below has a real `enabled` value; SettingsModal does the same after
  // a save, so turning sync on later starts the round-trips on the next tick.
  async function refreshNavigationStatus(): Promise<void> {
    try {
      navigationStatus.set(await bridge.navigationStatus());
    } catch (error) {
      console.error("Could not read desktop navigation status", error);
    }
  }

  async function pollNavigation(): Promise<void> {
    if (navigationPollInFlight || engineStatus !== "ready") return;
    // No connector enabled means the engine's coordinator returns at once
    // (navigation.py's poll guard), so the only thing this tick would do is
    // occupy the single-threaded dispatcher line ~75 times a minute (#110).
    // The timer keeps ticking; it just does no RPC until sync is enabled.
    if (!$navigationStatus.enabled) return;
    navigationPollInFlight = true;
    try {
      const state = await bridge.navigationPoll(navigationContext());
      navigationStatus.set(state);
      if (state.candidate) await handleNavigationCandidate(state.candidate);
    } catch (error) {
      console.error("Could not poll desktop navigation", error);
    } finally {
      navigationPollInFlight = false;
    }
  }

  // Sidecar respawns are silent by design (see sidecar.rs) — the new
  // process has no project open, so anything project-scoped will fail
  // with "No project open" until this reconnects it. This is the recovery
  // path for that, not just a status update.
  async function handleEngineRespawn(): Promise<void> {
    if (engineNoticeTimer) clearTimeout(engineNoticeTimer);
    engineNotice = "Engine restarted — reconnecting…";
    try {
      await bridge.ping();
      engineStatus = "ready";
      await refreshNavigationStatus();
    } catch {
      engineStatus = "error";
    }
    const reopenPath = $project?.path;
    if (reopenPath) {
      try {
        const info = await bridge.openProject(reopenPath, $project?.projectId);
        const siblings = $project?.importedProjects;
        if (!info.importedProjects && siblings) info.importedProjects = siblings;
        project.set(info);
        lastNavigationReference = "";
        engineNotice = "Engine restarted — project reconnected automatically.";
      } catch (error) {
        engineNotice = `Engine restarted, but the project could not be reopened automatically (${error instanceof Error ? error.message : String(error)}). Reopen it from Projects if things look stale.`;
      }
    } else {
      engineNotice = "Engine restarted.";
    }
    engineNoticeTimer = setTimeout(() => { engineNotice = ""; }, 10000);
  }

  // The verse marks' own poll (languageQaInline.ts), on the same lifecycle as
  // LanguageQaPanel below -- running while a book is open, restarted when the
  // book changes -- but independent of the panel, which only pages its list.
  let stopLanguageQaInline: (() => void) | null = null;
  let stopFlagLoader: (() => void) | null = null;
  let languageQaInlinePath = "";
  $: {
    const path = $project && opened ? $project.path : "";
    if (path !== languageQaInlinePath) {
      stopLanguageQaInline?.();
      stopLanguageQaInline = null;
      stopFlagLoader?.();
      stopFlagLoader = null;
      languageQaInlinePath = path;
      // A scoped correction's batch belongs to the book it was made in.
      resetScopedApply();
      if (path) stopLanguageQaInline = startLanguageQaInline(path);
      // Reviewer flags (⚑) of each chapter as it is shown.
      if (path) stopFlagLoader = startFlagLoader(path);
    }
  }
  onDestroy(() => {
    stopLanguageQaInline?.();
    stopFlagLoader?.();
  });

  // Collection QA (layered-rules 4.4): while a run checks every book, the app
  // is read-only. checkingProgress.running is what already holds editing,
  // alignment and AI review; the book switcher checks collectionQaRunning.
  let collectionHeldProgress = false;
  $: if ($collectionQaRunning && !collectionHeldProgress) {
    collectionHeldProgress = true;
    checkingProgress.update((p) => ({ ...p, running: true, label: "Collection QA running", state: "running" }));
  } else if (!$collectionQaRunning && collectionHeldProgress) {
    collectionHeldProgress = false;
    checkingProgress.update((p) => ({ ...p, running: false, label: "Collection QA finished", state: "succeeded" }));
  }
  $: if (!$project || !opened) stopCollectionQa();

  onMount(() => {
    let unlisten: (() => void) | null = null;
    let unlistenLog: (() => void) | null = null;
    let unlistenRespawn: (() => void) | null = null;
    let disposed = false;
    const navigationTimer = window.setInterval(() => void pollNavigation(), 800);
    void (async () => {
      try {
        const initialLog = await bridge.engineLogRecent(200);
        if (!disposed) engineLog.set(initialLog);
      } catch (error) {
        console.error("Could not load engine diagnostics", error);
      }
      try {
        const stopLog = await bridge.onEngineLog(appendEngineLog);
        if (disposed) stopLog();
        else unlistenLog = stopLog;
      } catch (error) {
        // Diagnostics are helpful but must never prevent the engine, settings,
        // file-drop handling, or project editor from finishing startup.
        console.error("Could not subscribe to engine diagnostics", error);
      }
      try {
        const stopRespawn = await bridge.onEngineRespawned(() => void handleEngineRespawn());
        if (disposed) stopRespawn();
        else unlistenRespawn = stopRespawn;
      } catch (error) {
        console.error("Could not subscribe to engine restart events", error);
      }

      try {
        await bridge.ping();
        engineStatus = "ready";
        await refreshNavigationStatus();
        try {
          const settings = await bridge.getSettings();
          reviewerMode.set(settings.reviewerMode);
          setPlaces(settings);
          triageThreshold = settings.triageHideThreshold > 0 ? settings.triageHideThreshold : null;
        } catch (error) {
          console.error("Could not load reviewer mode", error);
        }
        const stopListening = await bridge.onFileDrop(
          (paths) => void handleDroppedPaths(paths),
          (phase) => { draggingOver = phase === "over"; },
        );
        if (disposed) stopListening();
        else unlisten = stopListening;
      } catch {
        engineStatus = "error";
      }
    })();
    return () => {
      disposed = true;
      unlisten?.();
      unlistenLog?.();
      unlistenRespawn?.();
      if (engineNoticeTimer) clearTimeout(engineNoticeTimer);
      if (navigationRetryTimer) clearTimeout(navigationRetryTimer);
      window.clearInterval(navigationTimer);
    };
  });

  async function handleDroppedPaths(paths: string[]) {
    draggingOver = false;
    dropError = "";
    if (paths.length !== 1) {
      dropError = "Drop exactly one project file or folder at a time.";
      return;
    }
    await showProjectHome();
    droppedPath = paths[0];
    dropSequence += 1;
  }

  async function showProjectHome() {
    await stopActiveJob();
    resetBookState();
    resetReviewState();
    project.set(null);
    opened = false;
    showingAlignmentReview = false;
    showingReport = false;
    stopReportPolling();
    qaReport = null;
    qaReportJob = null;
    qaReportError = "";
    openingBook = "";
    bookOpenError = "";
  }

  async function handleOpened() {
    opened = true;
    showingDashboard = true;
    report = null;
    reportError = "";
    void loadDashboard();
    // The QA report is deliberately NOT built here. Measured on Genesis of a
    // real Hindi IRV import: build_book_report() takes ~130 s (project_scan
    // 78 s + exception_first_queue 44 s), and the sidecar's stdio loop is
    // single-threaded, so every other request queues behind it -- the 30 s
    // client timeout fired and the whole app was unresponsive until Python
    // finished. It is requested explicitly from the dashboard instead.
  }

  async function loadDashboard(): Promise<void> {
    dashboardLoading = true;
    dashboardError = "";
    try {
      bookProgress = (await bridge.listBookProgress()).books;
    } catch (error) {
      dashboardError = error instanceof Error ? error.message : String(error);
    } finally {
      dashboardLoading = false;
    }
  }

  async function loadReport(): Promise<void> {
    reportLoading = true;
    reportError = "";
    try {
      report = await bridge.projectReport();
    } catch (error) {
      reportError = error instanceof Error ? error.message : String(error);
    } finally {
      reportLoading = false;
    }
  }

  function openDashboard(): void {
    showingAlignmentReview = false;
    showingReport = false;
    showingDashboard = true;
    void loadDashboard();
    // Same reasoning as handleOpened: whichever book is open, the report is
    // minutes of synchronous work that blocks the sidecar, so it is a button.
  }

  // -- whole-collection QA report ---------------------------------------

  const REPORT_TERMINAL = new Set(["succeeded", "failed", "cancelled"]);

  function stopReportPolling(): void {
    qaReportPollGeneration += 1;
    if (qaReportPollTimer) clearTimeout(qaReportPollTimer);
    qaReportPollTimer = undefined;
  }

  function openReportScreen(): void {
    showingAlignmentReview = false;
    showingReport = true;
    if (!qaReport && !qaReportJob) void generateReport();
    // Verdicts live on disk, so a previous session's triage is already
    // available the moment the screen opens — no run required.
    void loadTriageResults();
  }

  async function generateReport(): Promise<void> {
    qaReportError = "";
    stopReportPolling();
    const generation = qaReportPollGeneration;
    try {
      let snapshot: ReportJobSnapshot;
      try {
        snapshot = await bridge.reportGenerate();
      } catch (error) {
        // A build is already running (a double click, or a second window
        // on the same sidecar): follow it rather than fail.
        const message = error instanceof Error ? error.message : String(error);
        if (!/already/i.test(message)) throw error;
        snapshot = await bridge.reportStatus("");
      }
      if (generation !== qaReportPollGeneration) return;
      qaReportJob = snapshot;
      await pollReport(snapshot.jobId, generation);
    } catch (error) {
      if (generation !== qaReportPollGeneration) return;
      qaReportError = error instanceof Error ? error.message : String(error);
      qaReportJob = null;
    }
  }

  async function pollReport(jobId: string, generation: number): Promise<void> {
    while (generation === qaReportPollGeneration) {
      const snapshot = await bridge.reportStatus(jobId);
      if (generation !== qaReportPollGeneration) return;
      qaReportJob = snapshot;
      if (REPORT_TERMINAL.has(snapshot.state)) {
        if (snapshot.ready) {
          const fetched = await bridge.reportGet(jobId);
          if (generation !== qaReportPollGeneration) return;
          qaReport = fetched.report;
          // Some books unreadable: the report still stands, say which part is missing.
          if (snapshot.error) qaReportError = snapshot.error;
        } else {
          qaReportError = snapshot.error
            || (snapshot.state === "cancelled" ? "Report generation was cancelled." : "Report generation failed.");
        }
        return;
      }
      await new Promise<void>((resolve) => { qaReportPollTimer = setTimeout(resolve, 500); });
    }
  }

  async function cancelReport(): Promise<void> {
    if (!qaReportJob) return;
    try {
      qaReportJob = await bridge.reportCancel(qaReportJob.jobId);
    } catch (error) {
      qaReportError = error instanceof Error ? error.message : String(error);
    }
  }

  // -- AI triage overlay -------------------------------------------------
  //
  // Verdicts are persisted per book by the engine, so they are always read
  // from disk (triage.results) rather than from the job — they survive a
  // restart, and a cancelled run keeps whatever it already paid for. The
  // report itself never depends on any of this having happened.

  /**
   * Reads verdicts off disk. With no `book` this replaces the whole map;
   * with one it merges just that book's verdicts in, which is what the
   * mid-run refresh wants — re-reading all 66 books every second to watch
   * one of them change would be pointless file I/O.
   */
  async function loadTriageResults(book = ""): Promise<void> {
    try {
      const results = await bridge.triageResults(book);
      triageEntries = book ? { ...triageEntries, ...results.entries } : results.entries;
      triageAvailable = results.available;
      triageUnavailableReason = results.unavailableReason;
    } catch (error) {
      // A failure here must never break the report: the page simply shows
      // no verdicts and the run button stays disabled.
      triageError = error instanceof Error ? error.message : String(error);
    }
  }

  async function runTriage(): Promise<void> {
    triageError = "";
    stopTriagePolling();
    const generation = triagePollGeneration;
    try {
      const started = await bridge.triageRun();
      if (generation !== triagePollGeneration) return;
      if (isTriageUnavailable(started)) {
        triageError = started.message;
        triageAvailable = false;
        triageUnavailableReason = started.message;
        return;
      }
      triageJob = started;
      await pollTriage(started.jobId, generation);
    } catch (error) {
      if (generation !== triagePollGeneration) return;
      triageError = error instanceof Error ? error.message : String(error);
      triageJob = null;
    }
  }

  async function pollTriage(jobId: string, generation: number): Promise<void> {
    while (generation === triagePollGeneration) {
      const snapshot = await bridge.triageStatus(jobId);
      if (generation !== triagePollGeneration) return;
      triageJob = snapshot;
      if (REPORT_TERMINAL.has(snapshot.state)) {
        // Even a cancelled or partly-failed run leaves verdicts on disk, so
        // this full read is always worth doing.
        await loadTriageResults();
        if (snapshot.error) triageError = snapshot.error;
        return;
      }
      // Refresh mid-run so verdicts appear as they are bought, not only at
      // the end — a whole-Bible run is long and silent otherwise. Scoped to
      // the book in flight: nothing else can have changed.
      if (snapshot.currentBook) await loadTriageResults(snapshot.currentBook);
      await new Promise<void>((resolve) => { triagePollTimer = setTimeout(resolve, 1000); });
    }
  }

  function stopTriagePolling(): void {
    triagePollGeneration += 1;
    if (triagePollTimer) clearTimeout(triagePollTimer);
    triagePollTimer = undefined;
  }

  async function cancelTriage(): Promise<void> {
    if (!triageJob) return;
    try {
      triageJob = await bridge.triageCancel(triageJob.jobId);
    } catch (error) {
      triageError = error instanceof Error ? error.message : String(error);
    }
  }

  async function setTriageThreshold(value: number | null): Promise<void> {
    triageThreshold = value;
    try {
      await bridge.setSettings({ triageHideThreshold: value ?? 0 });
    } catch {
      // A settings write failure must not fight the reviewer's slider: the
      // value stands for this session and simply is not remembered.
    }
  }

  async function overrideTriage(
    book: string, hash: string, verdict: TriageOverrideVerdict | "",
  ): Promise<void> {
    try {
      const result = await bridge.triageOverride(book, hash, verdict);
      triageEntries = { ...triageEntries, [hash]: result.record };
    } catch (error) {
      triageError = error instanceof Error ? error.message : String(error);
    }
  }

  // A row in the report table → that verse in the editor, switching to the
  // row's book first when it isn't the one currently open.
  async function navigateToReportRow(bookId: string, chapter: string, verse: string): Promise<void> {
    if (!$project) return;
    const wanted = bookId.toLowerCase();
    if ($project.bookId.toLowerCase() !== wanted) {
      const path = $project.importedProjects?.find((book) => book.bookId.toLowerCase() === wanted)?.path
        ?? bookProgress.find((book) => book.bookId.toLowerCase() === wanted)?.path;
      if (!path) {
        showNavigationNotice(`${bookId.toUpperCase()} is not in the open collection.`);
        return;
      }
      if (!(await switchBook(path, false))) return;
    }
    await navigateToFinding(chapter, verse);
  }

  async function enterBookFromDashboard(path: string): Promise<void> {
    // switchBook() no-ops when path === $project.path, which is exactly the
    // first-open interstitial case (enterCurrentProject was never called
    // yet) — so the already-open primary book's own row needs this instead.
    if ($project && path === $project.path) {
      showingDashboard = false;
      if (!$loadedChapters[$currentChapter]) await enterCurrentProject();
      return;
    }
    await switchBook(path);
    showingDashboard = false;
  }

  // Clicking a book row (not its Open button) previews that book's report in
  // the dashboard's right panel without leaving the dashboard — the
  // switchBook(path, false) seam activates a sibling book with no editor
  // navigation.
  async function previewBookOnDashboard(path: string): Promise<void> {
    if ($project && path === $project.path) return;
    if (await switchBook(path, false)) void loadReport();
  }

  // Shared by initial open and book switching: land on the first chapter
  // and its first verse once `project` points at the book to display.
  async function enterCurrentProject(): Promise<void> {
    const firstChapter = $project?.chapters[0] ?? "1";
    await activateChapter(firstChapter);
  }

  // Switch to a sibling book from a multi-book import. The sidecar's
  // project.open doesn't echo back importedProjects (only project.import
  // does), so the sibling list is carried forward on the frontend instead
  // of being re-fetched.
  async function switchBook(path: string, enterEditor = true): Promise<boolean> {
    if (!$project || openingBook) return false;
    if (path === $project.path) return true;
    if ($collectionQaRunning) {
      bookOpenError = "Collection QA is running. Pause or cancel it before switching books.";
      return false;
    }
    const siblings = $project.importedProjects;
    const destination = siblings?.find((book) => book.path === path);
    openingBook = destination?.bookName ?? "book";
    bookOpenError = "";
    try {
      await stopActiveJob();
      const info = await bridge.openProject(path);
      if (!info.importedProjects && siblings) info.importedProjects = siblings;
      resetBookState();
    resetReviewState();
      project.set(info);
      if (enterEditor) await enterCurrentProject();
      return true;
    } catch (error) {
      bookOpenError = error instanceof Error ? error.message : String(error);
      return false;
    } finally {
      openingBook = "";
    }
  }

  async function ensureChapterData(chapter: string): Promise<void> {
    const knownVerses = $chapterVerseNums[chapter] ?? [];
    if (
      knownVerses.length > 0 &&
      knownVerses.every((verse) => Object.prototype.hasOwnProperty.call($verseTexts, verseKey(chapter, verse)))
    ) return;
    const { verses, headings, editCounts } = await bridge.chapterVerseData(chapter);
    const verseIds = Object.keys(verses);
    chapterVerseNums.update((m) => ({ ...m, [chapter]: verseIds }));

    const texts: Record<string, string> = {};
    const displays: Record<string, VerseDisplay> = {};
    const alignmentStatuses: Record<string, AlignmentWorkStatus> = {};
    for (const [v, data] of Object.entries(verses)) {
      texts[verseKey(chapter, v)] = data.text;
      // The engine says what the verse shows (#91); the reader never parses USFM.
      if (data.display) displays[verseKey(chapter, v)] = data.display;
      alignmentStatuses[verseKey(chapter, v)] = data.alignmentStatus;
    }
    // Keyed by verseKey like everything else here: a bare verse number would
    // collide across chapters (gotcha 7).
    const headingRows: Record<string, VerseHeading[]> = {};
    for (const [v, rows] of Object.entries(headings ?? {})) {
      headingRows[verseKey(chapter, v)] = rows;
    }
    verseTexts.update((t) => ({ ...t, ...texts }));
    verseDisplay.update((d) => ({ ...d, ...displays }));
    headingsByVerse.update((h) => ({ ...h, ...headingRows }));
    alignmentStatusByVerse.update((existing) => ({ ...existing, ...alignmentStatuses }));
    const counts: Record<string, number> = {};
    for (const [v, n] of Object.entries(editCounts ?? {})) counts[verseKey(chapter, v)] = n;
    historyCountByVerse.update((existing) => ({ ...existing, ...counts }));
  }

  async function hydrateChapterAIReviews(
    chapter: string, expectedProjectPath: string, sequence: number,
  ): Promise<void> {
    try {
      const result = await bridge.listAIReviewsForChapter(chapter);
      if (
        sequence !== chapterLoadSequence || chapter !== $currentChapter
        || ($project?.path ?? "") !== expectedProjectPath
      ) return;
      const updates: Record<string, AiCheckReview[]> = {};
      for (const [verse, reviews] of Object.entries(result.reviewsByVerse)) {
        updates[verseKey(chapter, verse)] = reviews;
      }
      aiCheckReviewsByVerse.update((existing) => ({ ...existing, ...updates }));
    } catch (error) {
      console.error("Could not restore chapter AI reviews", error);
    }
  }

  function applyJobSnapshot(snapshot: CheckJobSnapshot): void {
    chapterVerseNums.update((existing) => ({ ...existing, ...snapshot.chapterVerses }));

    const findingUpdates: Record<string, QaFinding[]> = {};
    const statusUpdates: Record<string, "succeeded" | "failed"> = {};
    for (const [key, result] of Object.entries(snapshot.results)) {
      findingUpdates[key] = result.findings;
      statusUpdates[key] = result.status;
    }
    if (Object.keys(findingUpdates).length > 0) {
      findingsByVerse.update((existing) => ({ ...existing, ...findingUpdates }));
      checkStatusByVerse.update((existing) => ({ ...existing, ...statusUpdates }));
    }

    const terminal = ["succeeded", "failed", "cancelled"].includes(snapshot.state);
    // The job's Language QA pass published a new generation: redraw the marks
    // now rather than at the channel's next idle tick.
    if (terminal && snapshot.checks.includes("languageQa")) nudgeLanguageQa();
    if (terminal) {
      checkStatusByVerse.update((existing) => {
        const next = { ...existing };
        for (const [chapter, verses] of Object.entries(snapshot.chapterVerses)) {
          for (const verse of verses) {
            const key = verseKey(chapter, verse);
            if (!snapshot.results[key]) next[key] = snapshot.state === "cancelled" ? "cancelled" : "failed";
          }
        }
        return next;
      });
      loadedChapters.update((existing) => {
        const next = { ...existing };
        for (const [chapter, verses] of Object.entries(snapshot.chapterVerses)) {
          next[chapter] = verses.length > 0 && verses.every(
            (verse) => snapshot.results[verseKey(chapter, verse)]?.status === "succeeded",
          );
        }
        return next;
      });
    }

    const reference = snapshot.currentChapter
      ? ` · ${snapshot.currentChapter}${snapshot.currentVerse ? `:${snapshot.currentVerse}` : ""}`
      : "";
    checkingProgress.set({
      running: !terminal,
      percent: snapshot.percent,
      label: `${snapshot.currentStage}${reference}`,
      jobId: snapshot.jobId,
      state: snapshot.state,
      error: snapshot.error ?? "",
      scope: snapshot.scope,
    });
  }

  async function monitorJob(initial: CheckJobSnapshot, generation: number): Promise<void> {
    let snapshot = initial;
    try {
      while (!["succeeded", "failed", "cancelled"].includes(snapshot.state)) {
        await new Promise((resolve) => setTimeout(resolve, 750));
        if (generation !== monitorGeneration) return;
        snapshot = await bridge.checkStatus(snapshot.jobId);
        if (generation !== monitorGeneration) return;
        applyJobSnapshot(snapshot);
      }
    } catch (error) {
      if (generation !== monitorGeneration) return;
      checkingProgress.set({
        running: false,
        percent: snapshot.percent,
        label: "Checking failed",
        jobId: snapshot.jobId,
        state: "failed",
        error: error instanceof Error ? error.message : String(error),
        scope: snapshot.scope,
      });
    } finally {
      if (generation === monitorGeneration) activeJobId = "";
    }
  }

  function markJobPending(snapshot: CheckJobSnapshot): void {
    checkStatusByVerse.update((existing) => {
      const next = { ...existing };
      for (const [chapter, verses] of Object.entries(snapshot.chapterVerses)) {
        for (const verse of verses) next[verseKey(chapter, verse)] = "pending";
      }
      return next;
    });
  }

  async function beginChecks(scope: "chapter" | "book", chapters: string[]): Promise<void> {
    if (activeJobId) return;
    // Language QA is a stage of the same job (layered-rules Phase 4.1): its
    // pass is the authoritative one and shares the live-edit path's cache.
    const snapshot = await bridge.startChecks(scope, chapters, ["local", "greekroom", "languageQa"]);
    activeJobId = snapshot.jobId;
    const generation = ++monitorGeneration;
    markJobPending(snapshot);
    applyJobSnapshot(snapshot);
    void monitorJob(snapshot, generation);
  }

  async function activateChapter(chapter: string, targetVerse?: string): Promise<void> {
    const sequence = ++chapterLoadSequence;
    currentChapter.set(chapter);
    selectedVerseSet.set([]); // the multi-selection is chapter-scoped (#118)
    await ensureChapterData(chapter);
    if (sequence !== chapterLoadSequence || chapter !== $currentChapter) return;
    const verses = $chapterVerseNums[chapter] ?? [];
    selectedVerse.set(
      targetVerse && verses.includes(targetVerse) ? targetVerse : (verses.length > 0 ? verses[0] : null),
    );
    void hydrateChapterAIReviews(chapter, $project?.path ?? "", sequence);
    if ($project) recordRecentChapter({ collection: collectionOf($project), book: $project.bookId.toLowerCase(), chapter });
    if (!$loadedChapters[chapter] && !activeJobId) {
      await beginChecks("chapter", [chapter]);
    }
  }

  // Report/dashboard click-through (see ProjectDashboard's exceptionQueue
  // rows): project.report is scoped to whichever book is currently open,
  // so this never needs to switch books — just land on the right
  // chapter:verse and close the dashboard so the editor is visible.
  async function navigateToFinding(chapter: string, verse: string): Promise<void> {
    showingDashboard = false;
    showingAlignmentReview = false;
    showingReport = false;
    await activateChapter(chapter, verse);
  }

  async function cancelChecks(): Promise<void> {
    if (!activeJobId) return;
    applyJobSnapshot(await bridge.cancelChecks(activeJobId));
  }

  async function stopActiveJob(): Promise<void> {
    const jobId = activeJobId;
    if (!jobId) return;
    await bridge.cancelChecks(jobId);
    let snapshot = await bridge.checkStatus(jobId);
    while (!["succeeded", "failed", "cancelled"].includes(snapshot.state)) {
      await new Promise((resolve) => setTimeout(resolve, 400));
      snapshot = await bridge.checkStatus(jobId);
    }
    monitorGeneration++;
    activeJobId = "";
    applyJobSnapshot(snapshot);
  }

  async function retryChecks(): Promise<void> {
    const failedJobId = $checkingProgress.jobId;
    if (!failedJobId || activeJobId) return;
    const snapshot = await bridge.retryChecks(failedJobId);
    activeJobId = snapshot.jobId;
    const generation = ++monitorGeneration;
    markJobPending(snapshot);
    applyJobSnapshot(snapshot);
    void monitorJob(snapshot, generation);
  }

  function dismissCheckNotice(): void {
    checkingProgress.set({
      running: false, percent: 0, label: "", jobId: "", state: "idle", error: "", scope: "chapter",
    });
  }

  async function switchChapter(chapter: string) {
    await activateChapter(chapter);
  }

  async function runWholeBook() {
    const chapters = $project?.chapters ?? [];
    await beginChecks("book", chapters);
  }

  // #118: a finding may name verses of another chapter. Switch first (the
  // page is chapter-scoped and closes itself on a chapter change), then open.
  $: if ($crossVerseRequest && opened) void resolveCrossVerseRequest($crossVerseRequest);
  async function resolveCrossVerseRequest(request: { chapter: string; verses: string[] }) {
    crossVerseRequest.set(null);
    if (request.chapter !== $currentChapter) await activateChapter(request.chapter, request.verses[0]);
    if ($currentChapter !== request.chapter) return;
    openCrossVerse(request.verses[0], request.verses);
  }

  function selectVerse(v: string) {
    selectedVerse.set(v);
  }

  function gotoVerse(v: string) {
    if (($chapterVerseNums[$currentChapter] ?? []).includes(v)) selectedVerse.set(v);
  }

  $: navigationReference = currentBridgeReference();
  $: if (!navigationReference) {
    lastNavigationReference = "";
  } else if (engineStatus === "ready" && navigationReference !== lastNavigationReference) {
    const publishedReference = navigationReference;
    if (publishedReference !== navigationRetryReference) {
      navigationRetryReference = publishedReference;
      navigationRetries = 0;
    }
    lastNavigationReference = publishedReference;
    void bridge.navigationBridgeChanged(publishedReference)
      .then((state) => {
        navigationStatus.set(state);
        navigationRetries = 0;
      })
      .catch((error) => {
        console.error("Could not publish Bridge navigation", error);
        // Retrying by clearing lastNavigationReference re-enters this block, so
        // the attempts must be bounded: a persistently failing publish would
        // otherwise spin an RPC every 800ms for the rest of the session.
        if (navigationRetries >= 3) return;
        navigationRetries += 1;
        if (navigationRetryTimer) clearTimeout(navigationRetryTimer);
        navigationRetryTimer = setTimeout(() => {
          if (navigationReference === publishedReference && lastNavigationReference === publishedReference) {
            lastNavigationReference = "";
          }
        }, 800);
      });
  }

  // Export is enabled only once every chapter in the whole book has been
  // loaded AND fully approved — not just the currently visible one.
  $: bookSummary = bookApprovedSummary();
  $: allApproved =
    $project !== null &&
    bookSummary.totalChapters > 0 &&
    bookSummary.approvedChapters === bookSummary.totalChapters;

  // recompute bookSummary reactively when findings/loadedChapters change
  $: void $findingsByVerse, void $checkStatusByVerse, void $loadedChapters, (bookSummary = bookApprovedSummary());

  $: screen = (
    !opened ? "home"
      : showingAlignmentReview ? "review"
      : showingReport ? "report"
      : showingDashboard ? "dashboard"
      : "editor"
  ) as "home" | "dashboard" | "review" | "editor" | "report";
  $: projectName = $project?.projectName || $project?.bibleName || $project?.bookName || "";
  $: dashboardSubtitle = [
    $project?.targetLanguage,
    bookProgress.length > 0 ? `${bookProgress.length} ${bookProgress.length === 1 ? "book" : "books"}` : "",
  ].filter(Boolean).join(" · ");

  $: alignmentChapterSummary = ($chapterVerseNums[$currentChapter] ?? []).reduce(
    (counts, verse) => {
      const status = $alignmentStatusByVerse[verseKey($currentChapter, verse)] ?? "untouched";
      counts[status] += 1;
      return counts;
    },
    { complete: 0, partial: 0, untouched: 0, invalid: 0 },
  );
</script>

<div class="frame print-expand">
  <TopBar
    {screen}
    {projectName}
    onGoHome={showProjectHome}
    onGoToDashboard={openDashboard}
    onOpenSettings={() => openSettings("ai")}
    onOpenConnections={() => openSettings("connections")}
    onOpenExport={() => exportOpen.set(true)}
    onGenerateReport={openReportScreen}
    onGotoVerse={gotoVerse}
    onChapterChange={switchChapter}
    onBookChange={switchBook}
    exportEnabled={$project !== null}
    bookSwitching={Boolean(openingBook)}
  />

  {#if openingBook}
    <div class="progress-row checking-row">
      <div class="spin" />
      <span class="progress-label">Opening {openingBook}…</span>
      <div class="track"><div class="fill indeterminate" /></div>
      <span />
    </div>
  {:else if bookOpenError}
    <div class="progress-row check-notice">
      <span class="check-message">Could not open book: {bookOpenError}</span>
      <span class="grow" />
      <button class="progress-action" on:click={() => (bookOpenError = "")}>Dismiss</button>
    </div>
  {:else if $checkingProgress.running}
    <div class="progress-row checking-row">
      <div class="spin" />
      <span class="progress-label" title={$checkingProgress.label}>
        {$checkingProgress.label} — {$checkingProgress.percent}%
      </span>
      <div class="track"><div class="fill" style="width:{$checkingProgress.percent}%" /></div>
      <button class="progress-action cancel-action" on:click={cancelChecks} disabled={$checkingProgress.state === "cancelling"}>
        {$checkingProgress.state === "cancelling" ? "Cancelling…" : "Cancel"}
      </button>
    </div>
  {:else if $checkingProgress.state === "failed" || $checkingProgress.state === "cancelled"}
    <div class="progress-row check-notice">
      <span class="check-message" title={$checkingProgress.error}>{$checkingProgress.error || ($checkingProgress.state === "cancelled" ? "Checking was cancelled." : "Checking failed.")}</span>
      <span class="grow" />
      <button class="progress-action" on:click={retryChecks}>Retry</button>
      <button class="progress-action" on:click={dismissCheckNotice}>Dismiss</button>
    </div>
  {/if}

  {#if screen === "home"}
    <ImportScreen onOpened={handleOpened} {droppedPath} {dropSequence} />
  {:else if screen === "dashboard"}
    {#if bookProgress.length > 1}
      <div class="collection-qa-host">
        <CollectionQaPanel bookCount={bookProgress.length} onOpenBook={enterBookFromDashboard} />
      </div>
    {/if}
    <ProjectDashboard
      {projectName}
      subtitle={dashboardSubtitle}
      books={bookProgress}
      loading={dashboardLoading}
      error={dashboardError}
      onSelectBook={enterBookFromDashboard}
      onPreviewBook={previewBookOnDashboard}
      onRetry={loadDashboard}
      {report}
      reportLoading={reportLoading}
      reportError={reportError}
      openBookName={$project?.bookName || $project?.bookId?.toUpperCase() || ""}
      onLoadReport={loadReport}
      onNavigateToFinding={navigateToFinding}
    />
  {:else if screen === "report"}
    <ProjectReportScreen
      {projectName}
      report={qaReport}
      job={qaReportJob}
      error={qaReportError}
      onGenerate={generateReport}
      onCancel={cancelReport}
      onNavigate={navigateToReportRow}
      triage={triageEntries}
      {triageJob}
      {triageAvailable}
      {triageUnavailableReason}
      {triageError}
      {triageThreshold}
      onRunTriage={runTriage}
      onCancelTriage={cancelTriage}
      onTriageThreshold={setTriageThreshold}
      onTriageOverride={overrideTriage}
    />
  {:else if screen === "review"}
    {#key $project?.path}
      <AlignmentReview
        chapter={$currentChapter}
        verse={$selectedVerse}
        onClose={() => (showingAlignmentReview = false)}
      />
    {/key}
  {:else}
    <div class="body">
      <div class="editor-col" style:--verse-scale={$textScale}>
        <div class="editor-toolbar">
          <span>Chapter {$currentChapter} of {$project?.chapters.length ?? "?"}</span>
          <button class="whole-book-btn" on:click={runWholeBook} disabled={Boolean(activeJobId)}>
            {$checkingProgress.scope === "book" && $checkingProgress.running ? "Running…" : "Run whole book"}
          </button>
          <button class="whole-book-btn" on:click={() => (showingAlignmentReview = true)}>
            Alignment Review
          </button>
          <button
            class="whole-book-btn"
            on:click={() => openCrossVerse($selectedVerse ?? "", $selectedVerseSet)}
            disabled={!$selectedVerse}
            title={$selectedVerseSet.length > 1
              ? `Open the ${$selectedVerseSet.length} selected verses side by side`
              : "Align several verses of this chapter side by side (Ctrl-click or Shift-click verses to pick them first)"}
          >
            Cross-verse alignment{#if $selectedVerseSet.length > 1}&nbsp;({$selectedVerseSet.length}){/if}
          </button>
          <span class="toolbar-divider" aria-hidden="true" />
          <button class="whole-book-btn" class:on={$referencePanelOpen} aria-pressed={$referencePanelOpen}
            title="Show a reference Bible (an Old Version) beside the text"
            on:click={() => referencePanelOpen.update((open) => !open)}>Ref.</button>
          <button class="whole-book-btn" class:on={$rawView} aria-pressed={$rawView}
            title="Show each verse exactly as stored, USFM markers included"
            on:click={() => rawView.update((on) => !on)}>Raw</button>
          <button class="whole-book-btn" aria-haspopup="menu" disabled={!$project}
            title="Bookmark this verse, or go to a bookmark or a recent chapter"
            on:click={openPlacesMenu}>★ Bookmarks</button>
          <span class="text-size" role="group" aria-label="Text size">
            <button class="whole-book-btn" title="Smaller text" aria-label="Smaller text"
              disabled={$textScale === TEXT_SCALES[0]} on:click={() => bumpTextScale(-1)}>A−</button>
            <button class="whole-book-btn" title="Larger text" aria-label="Larger text"
              disabled={$textScale === TEXT_SCALES[TEXT_SCALES.length - 1]} on:click={() => bumpTextScale(1)}>A+</button>
          </span>
          <span class="grow" />
          <span title="Word-alignment status for this chapter">
            Alignment: {alignmentChapterSummary.complete} complete · {alignmentChapterSummary.partial} partial
            {#if alignmentChapterSummary.invalid} · {alignmentChapterSummary.invalid} invalid{/if}
          </span>
          <span>{bookSummary.approvedChapters}/{bookSummary.totalChapters} chapters approved</span>
        </div>
        <VerseList onSelect={selectVerse} onNavigate={navigateToReportRow} />
      </div>
      {#if $referencePanelOpen && $project}
        {#key $project.path}
          <ReferencePanel onOpenSettings={() => openSettings("languageQa")} onSelectVerse={selectVerse} />
        {/key}
      {/if}
      <ReviewPanel onNavigate={navigateToReportRow} />
    </div>

    <div class="statusbar">
      {#if $project}
        <span>Project: <b>{$project.bookName}</b></span>
        <span>Chapter: <b>{$currentChapter}</b></span>
        <span style="color:var(--success);">✓ Approved: {$approvedCount}/{$verseNums.length}</span>
      {/if}
      <span class="grow" />
      {#if engineNotice}<span class="engine-notice">{engineNotice}</span>{/if}
      <span>Engine: {engineStatus}</span>
      <button class="diagnostics-btn" on:click={() => diagnosticsOpen.set(true)}>
        Diagnostics
        {#if $engineLog.some((entry) => entry.level === "error")}<span class="error-dot" />{/if}
      </button>
    </div>
  {/if}

  {#if $project && opened}
    {#key $project.path}
      <LanguageQaPanel projectPath={$project.path} onNavigate={navigateToReportRow} />
    {/key}
    <ScopeNotice />
    {#if $scopeDialog}<ScopeConfirmDialog />{/if}
  {/if}

  {#if $project && $crossVerseOpen && $crossVerseAnchor}
    {#key `${$project.path}:${$currentChapter}:${$crossVerseAnchor}`}
      <CrossVerseAlignmentModal
        chapter={$currentChapter}
        verse={$crossVerseAnchor}
        initialVerses={$crossVerseInitialVerses}
        onClose={closeCrossVerse}
      />
    {/key}
  {/if}

  {#if placesMenu}
    <FindingContextMenu x={placesMenu.x} y={placesMenu.y} findingLabel="Bookmarks and recent chapters"
      actions={placesActions} on:action={onPlacesAction} on:close={() => (placesMenu = null)} />
  {/if}

  {#if $settingsOpen}
    <SettingsModal initialPane={settingsInitialPane} onClose={() => settingsOpen.set(false)} />
  {/if}

  {#if $diagnosticsOpen}
    <DiagnosticsPanel onClose={() => diagnosticsOpen.set(false)} />
  {/if}

  {#if $exportOpen}
    <ExportModal reviewComplete={allApproved} onClose={() => exportOpen.set(false)} />
  {/if}

  {#if draggingOver}
    <div class="global-drop" role="presentation">Drop to review this project source</div>
  {/if}
  {#if dropError}
    <div class="drop-error">{dropError}<button on:click={() => (dropError = "")}>Dismiss</button></div>
  {/if}
</div>

<style>
  .frame { width: 100vw; height: 100vh; background: var(--bg); display: flex; flex-direction: column; position: relative; overflow: hidden; }
  .progress-row { height: 32px; background: var(--surface-2); border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 10px; padding: 0 16px; font-size: var(--fs-xs); color: var(--text-2); flex-shrink: 0; }
  .checking-row { display: grid; grid-template-columns: 12px minmax(220px, 320px) minmax(120px, 1fr) 84px; }
  .progress-label { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .spin { width: 12px; height: 12px; border-radius: 50%; border: 2px solid var(--accent-bg); border-top-color: var(--accent); animation: spin 0.8s linear infinite; flex-shrink: 0; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .track { width: 100%; height: 6px; background: #EEF0F3; border-radius: 4px; overflow: hidden; }
  .fill { height: 100%; background: var(--accent); transition: width 0.3s; }
  .indeterminate { width: 35%; animation: slide 1.1s ease-in-out infinite; }
  @keyframes slide { from { transform: translateX(-100%); } to { transform: translateX(300%); } }
  .progress-action { border: 1px solid var(--border-strong); background: var(--surface); color: var(--text-2); border-radius: 5px; padding: 2px 8px; font-size: var(--fs-xs); cursor: pointer; }
  .cancel-action { width: 84px; }
  .progress-action:disabled { opacity: 0.55; cursor: wait; }
  .check-notice { color: var(--danger); height: auto; min-height: 32px; max-height: 96px; align-items: flex-start; padding-top: 7px; padding-bottom: 7px; }
  .check-message { flex: 1; min-width: 0; max-height: 78px; overflow: auto; white-space: normal; overflow-wrap: anywhere; line-height: 1.35; }
  .body { flex: 1; display: flex; overflow: hidden; }
  /* A 66-book table must not push the dashboard off screen: it scrolls. */
  .collection-qa-host { flex-shrink: 0; max-height: 40vh; overflow: auto; padding: 12px 16px 0; background: var(--bg); }
  .editor-col { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
  .text-size { display: inline-flex; gap: 2px; }
  /* min-height + wrap, not a fixed height: on a narrow window the items used to
     shrink, break their labels onto two lines and get clipped by the 34px box.
     Now each item keeps its label on one line and the row wraps instead. */
  .editor-toolbar { min-height: 34px; background: var(--surface-2); border-bottom: 1px solid var(--border); display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; padding: 4px 16px; font-size: var(--fs-xs); color: var(--text-2); flex-shrink: 0; }
  .editor-toolbar > * { white-space: nowrap; flex-shrink: 0; }
  .whole-book-btn { font-size: var(--fs-xs); font-weight: 600; padding: 4px 10px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--surface); color: var(--text); cursor: pointer; }
  .whole-book-btn:disabled { opacity: 0.6; cursor: not-allowed; }
  .whole-book-btn.on { background: var(--accent-bg); border-color: var(--accent); color: var(--accent); }
  .toolbar-divider { width: 1px; height: 20px; background: var(--border-strong); }
  .grow { flex: 1; }
  .statusbar { height: 28px; background: var(--surface); border-top: 1px solid var(--border); display: flex; align-items: center; padding: 0 16px; gap: 16px; font-size: var(--fs-xs); color: var(--text-2); flex-shrink: 0; }
  .engine-notice { color: var(--warning); font-weight: 600; }
  .diagnostics-btn { position: relative; font-size: var(--fs-2xs); font-weight: 650; padding: 3px 9px; border-radius: 999px; border: 1px solid var(--border-strong); background: var(--surface-2); color: var(--text-2); cursor: pointer; display: flex; align-items: center; gap: 5px; }
  .diagnostics-btn:hover { background: var(--surface); }
  .error-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--danger); }
  .global-drop { position: absolute; inset: 12px; z-index: 100; display: grid; place-items: center; border: 3px dashed var(--accent); border-radius: 14px; background: color-mix(in srgb, var(--accent-bg) 92%, transparent); color: var(--accent); font-size: var(--fs-2xl); font-weight: 750; pointer-events: none; }
  .drop-error { position: absolute; z-index: 101; left: 50%; bottom: 42px; transform: translateX(-50%); display: flex; align-items: center; gap: 14px; border: 1px solid var(--danger); border-radius: 8px; background: var(--surface); color: var(--danger); padding: 9px 12px; font-size: var(--fs-xs); box-shadow: 0 8px 24px rgba(0,0,0,.14); }
  .drop-error button { border: 0; background: transparent; color: var(--accent); cursor: pointer; font-size: var(--fs-xs); }
</style>
