<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import { bridge } from "../api/bridgeClient";
  import { decideLanguageQaFindingOptimistically, decideLocalFinding } from "../findingActions";
  import { languageQaChannel } from "../languageQaInline";
  import { forgetLearnedFix, isLearnedFinding, learnedPairOf } from "../learnedFixes";
  import type { LanguageQaFinding, LanguageQaSuggestion } from "../types/languageQa";
  import AlignmentModal from "./AlignmentModal.svelte";
  import TranslationHelpsReview from "./TranslationHelpsReview.svelte";
  import { aiJobAppliesToReference, isAIReviewJobActive } from "../utils/aiJobScope";
  import { categoryClass, findingNumbers } from "../utils/highlight";
  import {
    selectedVerse, selectedFindings, findingsByVerse, currentChapter,
    checkStatusByVerse, checkingProgress, verseKey,
    aiCheckReviewsByVerse, nativeChecksByVerse, project, reviewerMode,
  } from "../stores";
  import {
    editingChapter, editingVerse, editText, editSaving, editError, editErrorKey,
    recheckingKey, recheckedKey, startVerseEdit, cancelVerseEdit, setVerseEditSavedHook,
    setPendingAcceptFinding, applyLanguageQaSuggestedFix,
  } from "../verseEditor";
  import { alignmentOpen, alignmentKey, openAlignment } from "../alignmentUi";
  import { aiReviewRequest, aiJobActive } from "../aiReviewUi";
  import type { AiExplainResult, AIReviewJobSnapshot, FindingStatus } from "../types/finding";

  let greekRoomChecking = false;
  let lastCheckedKey = "";
  let liveCheckSequence = 0;
  let displayedLiveCheck = 0;
  const latestLiveCheckByVerse = new Map<string, number>();
  type DecisionSaveState = "saving" | "saved" | "error";
  let decisionSaveState: Record<string, DecisionSaveState> = {};
  let decisionSaveError: Record<string, string> = {};

  // Re-run Greek Room live whenever the selected verse changes — per the
  // approved design, Greek Room is the one engine that re-checks live on
  // focus; tN/tW/Alignment are already-computed background-pass results.
  $: if ($selectedVerse) {
    const key = verseKey($currentChapter, $selectedVerse);
    if (key !== lastCheckedKey) {
      lastCheckedKey = key;
      runLiveGreekRoomCheck($currentChapter, $selectedVerse);
    }
  }

  async function runLiveGreekRoomCheck(chapter: string, verse: string) {
    const key = verseKey(chapter, verse);
    const requestToken = ++liveCheckSequence;
    latestLiveCheckByVerse.set(key, requestToken);
    displayedLiveCheck = requestToken;
    greekRoomChecking = true;
    try {
      const findings = await bridge.runVerseChecks(chapter, verse, ["greekroom"]);
      if (latestLiveCheckByVerse.get(key) !== requestToken) return;
      findingsByVerse.update((map) => {
        const existing = (map[key] ?? []).filter((f) => f.engine !== "wildebeest");
        return { ...map, [key]: [...existing, ...findings] };
      });
    } catch (e) {
      console.error("Greek Room live check failed", e);
    } finally {
      if (displayedLiveCheck === requestToken) greekRoomChecking = false;
    }
  }

  // -- Language QA (layered-rules Phase 4.3) ---------------------------------
  // The selected verse's Language QA findings, inline or panel-only, with the
  // same actions as the verse's right-click menu. The LanguageQaPanel keeps
  // the book-level lists; this is where one verse's findings are acted on.
  // Fetched when the verse changes and when a new completed pass lands on the
  // status channel; actions change this list first and the engine after.
  let lqaFindings: LanguageQaFinding[] = [];
  let lqaHidden: Array<LanguageQaFinding & { decision: string }> = [];
  let lqaLoadedFor = "";
  let lqaSequence = 0;
  let lqaNotice = "";
  let lqaNoticeError = false;
  let lqaBusy = false;

  let lqaShownFor = "";
  $: lqaVerseKey = $selectedVerse && $project ? `${$project.path}::${verseKey($currentChapter, $selectedVerse)}` : "";
  // Another verse: drop the last verse's list at once, before its own arrives.
  $: if (lqaVerseKey !== lqaShownFor) {
    lqaShownFor = lqaVerseKey;
    lqaFindings = [];
    lqaHidden = [];
    lqaNotice = "";
  }
  $: lqaStatus = $languageQaChannel.status;
  $: lqaWanted = $selectedVerse && $project && lqaStatus?.state === "completed"
    ? `${$project.path}::${verseKey($currentChapter, $selectedVerse)}::${lqaStatus.generation}` : "";
  $: if (lqaWanted && lqaWanted !== lqaLoadedFor && $project && $selectedVerse) {
    lqaLoadedFor = lqaWanted;
    void loadLanguageQa($project.path, $currentChapter, $selectedVerse);
  }

  async function loadLanguageQa(projectPath: string, chapter: string, verse: string): Promise<void> {
    const sequence = ++lqaSequence;
    try {
      const result = await bridge.languageQaVerse(projectPath, chapter, verse);
      if (sequence !== lqaSequence) return;
      lqaFindings = result.findings;
      lqaHidden = result.hidden;
    } catch (error) {
      if (sequence !== lqaSequence) return;
      lqaFindings = [];
      lqaHidden = [];
      console.error("Language QA findings for the verse could not be loaded", error);
    }
  }

  function lqaSuggestions(finding: LanguageQaFinding): LanguageQaSuggestion[] {
    if (finding.suggestions?.length) return finding.suggestions.slice(0, 5);
    return finding.suggestedReplacement
      ? [{ text: finding.suggestedReplacement, rank: 1, source: "rule", rationale: "" }] : [];
  }

  function restoreLqa(finding: LanguageQaFinding): void {
    if (!lqaFindings.some((f) => f.id === finding.id)) {
      lqaFindings = [...lqaFindings, finding].sort((a, b) => a.start - b.start);
    }
  }

  function useLqa(finding: LanguageQaFinding, chosen: LanguageQaSuggestion): void {
    if (lqaBusy) return;
    lqaBusy = true;
    lqaNotice = "";
    lqaFindings = lqaFindings.filter((f) => f.id !== finding.id);
    void applyLanguageQaSuggestedFix(finding, chosen).then((result) => {
      lqaNotice = result.message;
      lqaNoticeError = !result.ok;
      if (!result.ok) restoreLqa(finding);
    }).finally(() => { lqaBusy = false; });
  }

  /** Forget a learned fix: every recurrence of it goes, not just this one. */
  function forgetLqa(finding: LanguageQaFinding): void {
    const pair = learnedPairOf(finding);
    if (!pair) return;
    const isSame = (f: LanguageQaFinding) => learnedPairOf(f)?.old === pair.old && learnedPairOf(f)?.newWord === pair.newWord;
    const removed = lqaFindings.filter(isSame);
    lqaFindings = lqaFindings.filter((f) => !isSame(f));
    lqaNotice = `No longer offering “${pair.old}” → “${pair.newWord}”. Restore it in Language QA › Dictionary.`;
    lqaNoticeError = false;
    void forgetLearnedFix(pair.old, pair.newWord).then((error) => {
      if (!error) return;
      lqaNotice = error;
      lqaNoticeError = true;
      removed.forEach(restoreLqa);
    });
  }

  function decideLqa(finding: LanguageQaFinding, status: "ignored" | "rejected"): void {
    lqaFindings = lqaFindings.filter((f) => f.id !== finding.id);
    lqaNotice = status === "ignored" ? "Occurrence ignored." : "Marked as a false positive.";
    lqaNoticeError = false;
    void decideLanguageQaFindingOptimistically(finding, status).then((error) => {
      if (!error) return;
      lqaNotice = error;
      lqaNoticeError = true;
      restoreLqa(finding);
    });
  }

  async function decide(findingId: string, status: FindingStatus) {
    if (!$selectedVerse || decisionSaveState[findingId] === "saving") return;
    const chapter = $currentChapter;
    const verse = $selectedVerse;
    decisionSaveState = { ...decisionSaveState, [findingId]: "saving" };
    decisionSaveError = { ...decisionSaveError, [findingId]: "" };
    try {
      await decideLocalFinding(chapter, verse, findingId, status);
      decisionSaveState = { ...decisionSaveState, [findingId]: "saved" };
      window.setTimeout(() => {
        if (decisionSaveState[findingId] !== "saved") return;
        const next = { ...decisionSaveState };
        delete next[findingId];
        decisionSaveState = next;
      }, 2500);
    } catch (error) {
      decisionSaveState = { ...decisionSaveState, [findingId]: "error" };
      decisionSaveError = {
        ...decisionSaveError,
        [findingId]: error instanceof Error ? error.message : String(error),
      };
    }
  }

  let aiExplainError = "";
  let aiExplainErrorJobId = "";
  let aiExplainErrorReference = "";
  let aiExplainResult: AiExplainResult | null = null;
  let aiExplainKey = "";
  let aiJob: AIReviewJobSnapshot | null = null;
  let visibleAIJob: AIReviewJobSnapshot | null = null;
  let aiJobBusy = false;
  let currentReviewReference = "";
  let visibleAIExplainError = "";
  let aiPollTimer: ReturnType<typeof setTimeout> | undefined;
  let aiPollSequence = 0;
  let processedAIResults = new Set<string>();
  let observedProjectPath = "";
  let aiFailedResults: Array<{ chapter: string; verse: string; error: string | null }> = [];
  let translationHelpsReview: TranslationHelpsReview;


  $: currentReviewReference = `${$project?.path ?? ""}::${$selectedVerse ? verseKey($currentChapter, $selectedVerse) : ""}`;
  $: aiJobBusy = isAIReviewJobActive(aiJob);
  $: aiJobActive.set(aiJobBusy);
  // Mirrors exactly what .panel-pinned renders. Without it the block shows
  // as an empty padded strip with a rule under it whenever the verse is
  // idle, which is most of the time now the AI status has moved to its tab.
  $: hasPinnedStatus = Boolean($selectedVerse) && (
    $recheckingKey === verseKey($currentChapter, $selectedVerse ?? "") ||
    $recheckedKey === verseKey($currentChapter, $selectedVerse ?? "") ||
    ($editError && $editErrorKey === verseKey($currentChapter, $selectedVerse ?? "") && !$editingChapter) ||
    ($editingChapter === $currentChapter && $editingVerse === $selectedVerse)
  );
  $: visibleAIJob = aiJob && $selectedVerse && aiJobAppliesToReference(
    aiJob, $project?.path ?? "", $currentChapter, $selectedVerse,
  ) ? aiJob : null;
  $: visibleAIExplainError = aiExplainError && (
    aiExplainErrorJobId
      ? visibleAIJob?.jobId === aiExplainErrorJobId
      : aiExplainErrorReference === currentReviewReference
  ) ? aiExplainError : "";
  $: aiFailedResults = visibleAIJob
    ? Object.values(visibleAIJob.results)
        .filter((result) => result.status === "failed")
        .map((result) => ({ chapter: result.chapter, verse: result.verse, error: result.error }))
    : [];
  // Roll-up across every verse in the run. Per-check reasons live on the check
  // itself in Translation helps; this is the "how much did it actually do"
  // answer for a chapter or book pass, where no single verse is on screen.
  $: aiSelectionTally = Object.values(visibleAIJob?.results ?? {}).reduce(
    (totals, result) => ({
      applied: totals.applied + (result.appliedCount ?? 0),
      pending: totals.pending + (result.skippedCount ?? 0),
    }),
    { applied: 0, pending: 0 },
  );

  function nativeCheckStateChanged(): void {
    aiExplainResult = null;
    aiExplainError = "";
    aiExplainErrorJobId = "";
    aiExplainErrorReference = "";
    aiExplainKey = "";
  }

  $: if (
    $editingChapter && !$editSaving && $selectedVerse &&
    verseKey($currentChapter, $selectedVerse) !== verseKey($editingChapter, $editingVerse)
  ) {
    cancelVerseEdit();
    editError.set("");
  }

  $: if (
    aiExplainResult && $selectedVerse &&
    verseKey($currentChapter, $selectedVerse) !== aiExplainKey
  ) {
    aiExplainResult = null;
    aiExplainError = "";
  }

  async function hydrateCompletedAIReview(
    chapter: string, verse: string, expectedProjectPath: string,
  ): Promise<void> {
    try {
      const result = await bridge.listChecksForVerse(chapter, verse);
      if (result.state !== "ready" || ($project?.path ?? "") !== expectedProjectPath) return;
      const key = verseKey(chapter, verse);
      aiCheckReviewsByVerse.update((values) => ({ ...values, [key]: result.aiReviews ?? [] }));
      nativeChecksByVerse.update((values) => ({ ...values, [key]: result.checks ?? [] }));
    } catch (error) {
      console.error(`Could not hydrate completed AI review ${chapter}:${verse}`, error);
    }
  }

  function syncAIJobResult(snapshot: AIReviewJobSnapshot): void {
    for (const [key, resultStatus] of Object.entries(snapshot.results)) {
      if (resultStatus.status !== "succeeded") continue;
      const resultIdentity = `${snapshot.jobId}:${key}`;
      if (!processedAIResults.has(resultIdentity)) {
        processedAIResults = new Set([...processedAIResults, resultIdentity]);
        void hydrateCompletedAIReview(
          resultStatus.chapter, resultStatus.verse, snapshot.projectPath,
        );
        if ($selectedVerse && key === verseKey($currentChapter, $selectedVerse)) {
          void translationHelpsReview?.refresh();
        }
      }
    }
    const latest = snapshot.latestResult;
    if (latest?.result.status === "succeeded") {
      const { key, result } = latest;
      aiCheckReviewsByVerse.update((values) => ({ ...values, [key]: result.checkReviews ?? [] }));
      if ($selectedVerse && key === verseKey($currentChapter, $selectedVerse)) {
        aiExplainKey = key;
        aiExplainError = "";
        aiExplainErrorJobId = "";
        aiExplainErrorReference = "";
        aiExplainResult = {
          summary: result.summary,
          checkReviews: result.checkReviews,
          qaIssues: result.qaIssues,
          alignmentProposal: result.alignmentProposal,
          alignmentWasAIProposed: result.alignmentWasAIProposed,
          usage: result.usage,
        };
      }
    }
  }

  async function pollAIJob(jobId: string, sequence: number): Promise<void> {
    try {
      const snapshot = await bridge.aiReviewStatus(jobId);
      if (sequence !== aiPollSequence) return;
      aiJob = snapshot;
      syncAIJobResult(snapshot);
      if (["queued", "running", "cancelling"].includes(snapshot.state)) {
        aiPollTimer = setTimeout(() => void pollAIJob(jobId, sequence), 650);
      } else {
        if ($selectedVerse && aiJobAppliesToReference(
          snapshot, $project?.path ?? "", $currentChapter, $selectedVerse,
        )) {
          void translationHelpsReview?.refresh();
        }
        if (snapshot.state === "failed") {
          aiExplainError = snapshot.error || "AI review failed.";
          aiExplainErrorJobId = snapshot.jobId;
          aiExplainErrorReference = "";
        }
      }
    } catch (error) {
      if (sequence === aiPollSequence) {
        aiExplainError = error instanceof Error ? error.message : String(error);
        aiExplainErrorJobId = jobId;
        aiExplainErrorReference = "";
      }
    }
  }

  async function startAIReview(
    scope: "verse" | "chapter" | "book",
    chapter: string = $currentChapter,
    verse: string = $selectedVerse ?? "",
  ) {
    if (!verse || aiJobBusy) return;
    const requestedReference = `${$project?.path ?? ""}::${verseKey(chapter, verse)}`;
    if (scope !== "verse" && !window.confirm(
      `Run AI review for this ${scope}? Each verse may use one or two model requests and incur API charges.`,
    )) return;
    aiExplainError = "";
    aiExplainErrorJobId = "";
    aiExplainErrorReference = "";
    aiExplainResult = null;
    try {
      const snapshot = await bridge.startAIReview(scope, chapter, verse, $reviewerMode);
      aiJob = snapshot;
      processedAIResults = new Set();
      const sequence = ++aiPollSequence;
      void pollAIJob(snapshot.jobId, sequence);
    } catch (e) {
      aiExplainError = e instanceof Error ? e.message : String(e);
      aiExplainErrorJobId = "";
      aiExplainErrorReference = requestedReference;
    }
  }

  // The verse-row context menu (issue #69) requests a scope this way instead
  // of calling startAIReview directly, since it lives outside this
  // component and has no reason to duplicate the job/polling state above.
  $: if ($aiReviewRequest) {
    const { chapter, verse, scope } = $aiReviewRequest;
    aiReviewRequest.set(null);
    void startAIReview(scope, chapter, verse);
  }

  async function cancelAIReview(): Promise<void> {
    if (!aiJob || !["queued", "running", "cancelling"].includes(aiJob.state)) return;
    try {
      aiJob = await bridge.cancelAIReview(aiJob.jobId);
    } catch (error) {
      aiExplainError = error instanceof Error ? error.message : String(error);
      aiExplainErrorJobId = aiJob?.jobId ?? "";
      aiExplainErrorReference = "";
    }
  }

  async function retryAIReview(): Promise<void> {
    if (!aiJob || !["failed", "cancelled"].includes(aiJob.state)) return;
    aiExplainError = "";
    aiExplainErrorJobId = "";
    aiExplainErrorReference = "";
    try {
      const snapshot = await bridge.retryAIReview(aiJob.jobId);
      aiJob = snapshot;
      processedAIResults = new Set();
      const sequence = ++aiPollSequence;
      void pollAIJob(snapshot.jobId, sequence);
    } catch (error) {
      aiExplainError = error instanceof Error ? error.message : String(error);
      aiExplainErrorJobId = aiJob?.jobId ?? "";
      aiExplainErrorReference = "";
    }
  }

  $: if (($project?.path ?? "") !== observedProjectPath) {
    if (observedProjectPath && aiJob && ["queued", "running", "cancelling"].includes(aiJob.state)) {
      void cancelAIReview();
    }
    observedProjectPath = $project?.path ?? "";
    aiJob = null;
    aiExplainResult = null;
    aiExplainError = "";
    aiExplainErrorJobId = "";
    aiExplainErrorReference = "";
    aiPollSequence += 1;
    if (aiPollTimer) clearTimeout(aiPollTimer);
  }

  onDestroy(() => {
    aiPollSequence += 1;
    if (aiPollTimer) clearTimeout(aiPollTimer);
  });

  $: if (
    $alignmentOpen && $selectedVerse &&
    verseKey($currentChapter, $selectedVerse) !== $alignmentKey
  ) {
    alignmentOpen.set(false);
  }

  function startEdit() {
    startVerseEdit($currentChapter, $selectedVerse ?? "");
  }

  // "Accept and edit" on a specific Greek Room finding: same edit session
  // as startEdit, but remembers which finding this was so a successful
  // save can record it as "accepted" (see setVerseEditSavedHook below)
  // rather than leaving it open for the next recheck to silently re-decide.
  function acceptAndEdit(findingId: string) {
    if (startVerseEdit($currentChapter, $selectedVerse ?? "")) {
      setPendingAcceptFinding(findingId);
    }
  }

  onMount(() => {
    setVerseEditSavedHook(({ chapter, verse, issueResolutionsNeedingRecheck, acceptFindingId }) => {
      const key = verseKey(chapter, verse);
      // Invalidate any live Greek-Room-only check still in flight from when
      // this verse was first selected, so it can't resolve after this point
      // and overwrite saveVerseEdit's own fresh (post-edit) findings with a
      // stale pre-edit wildebeest result.
      latestLiveCheckByVerse.set(key, ++liveCheckSequence);
      if ($selectedVerse && verseKey($currentChapter, $selectedVerse) === key) {
        void translationHelpsReview?.refresh();
        // Record the human decision unconditionally — the same "a decision
        // persists even if the finding somehow still recurs" behavior
        // Ignore already relies on. Usually the edit actually fixed the
        // underlying text, so this specific finding id won't even reappear
        // in the fresh recheck results at all; this just makes sure it's
        // filed as accepted for the case where it does.
        if (acceptFindingId) void decide(acceptFindingId, "accepted");
      }
      if (issueResolutionsNeedingRecheck > 0) {
        // A saved issue can only close against the edited text. Start the
        // evidence-grounded verse review automatically; failures remain visibly
        // stale/retryable and never restore the previous resolved state.
        void startAIReview("verse", chapter, verse);
      }
    });
  });

  const severityBadge: Record<string, string> = {
    high: "badge-wrong", medium: "badge-review", low: "badge-review", info: "badge-review",
  };

  type ReviewTab = "greekroom" | "tntw" | "lqa" | "ai";
  let activeTab: ReviewTab = "greekroom";
  // The three real Greek Room engines (see each adapter's own engine_name:
  // wildebeest_adapter.py, usfm_adapter.py, names_adapter.py) — everything
  // else on a finding's `engine` field (tN/tW/alignment's own QAIssue.source,
  // or "local" as its fallback) is native tC/Bridge QA, not Greek Room.
  const GREEK_ROOM_ENGINES = new Set(["wildebeest", "usfm", "names"]);
  function isGreekRoom(engine: string): boolean {
    return GREEK_ROOM_ENGINES.has(engine);
  }
  $: greekRoomOpenCount = $selectedFindings.filter((f) => isGreekRoom(f.engine) && f.status === "open").length;
  $: tntwOpenCount = $selectedFindings.filter((f) => !isGreekRoom(f.engine) && f.status === "open").length;
  // Same cross-reference-style numbering shown inline in the verse text
  // (VerseList.svelte) — both read $selectedFindings for the same verse
  // and both exclude ignored/accepted findings before numbering (VerseList
  // excludes them from buildSegments' highlighting too), so the numbers
  // line up without any shared state beyond that.
  $: findingNumberMap = findingNumbers(
    $selectedFindings.filter((f) => f.status !== "ignored" && f.status !== "accepted"),
  );
  function byFindingNumber(a: { id: string }, b: { id: string }): number {
    const na = findingNumberMap.get(a.id) ?? Infinity;
    const nb = findingNumberMap.get(b.id) ?? Infinity;
    return na - nb;
  }
</script>

<div class="panel">
  {#if $selectedVerse}
    <!-- Title and the two per-verse actions share one row where they fit;
         the row wraps the buttons onto their own line rather than squeezing
         them when the reference or the finding count runs long. The AI run
         is not here -- it lives in the AI review tab with its progress and
         errors, so a run can be started and watched in one place. -->
    <div class="panel-header">
      <div class="header-title">
        <div class="ref">Review {($project?.bookId ?? "").toUpperCase()} {$currentChapter}:{$selectedVerse}</div>
        <div class="sub">
          {$selectedFindings.filter((f) => f.status === "open").length} open finding(s)
        </div>
      </div>
      <div class="verse-actions">
        <button
          class="align-btn"
          on:click={() => openAlignment($currentChapter, $selectedVerse ?? "")}
          disabled={$checkingProgress.running || Boolean($editingChapter) || $editSaving || Boolean($recheckingKey)}
          title={$checkingProgress.running ? "Wait for background checking to finish before aligning" : "Review word alignment"}
        >⇄ Align words</button>
        <button
          class="edit-btn"
          on:click={startEdit}
          disabled={$checkingProgress.running || Boolean($editingChapter) || $editSaving || Boolean($recheckingKey)}
          title={$checkingProgress.running ? "Wait for background checking to finish before editing" : "Edit this verse"}
        >✎ Edit verse</button>
      </div>
    </div>

    {#if hasPinnedStatus}
    <div class="panel-pinned">
      {#if $recheckingKey === verseKey($currentChapter, $selectedVerse)}
        <div class="operation-status checking"><span class="spin" /> Verse saved. Re-checking local and Greek Room QA…</div>
      {:else if $recheckedKey === verseKey($currentChapter, $selectedVerse)}
        <div class="operation-status saved">✓ Verse saved and re-check completed.</div>
      {:else if $editError && $editErrorKey === verseKey($currentChapter, $selectedVerse) && !$editingChapter}
        <div class="operation-status failed">The verse was not fully rechecked: {$editError}</div>
      {/if}

      {#if $editingChapter === $currentChapter && $editingVerse === $selectedVerse}
        <div class="operation-status checking">✎ Editing this verse in the left panel — save or cancel there.</div>
      {/if}
    </div>
    {/if}

    <div class="panel-scroll">
      <div class="tabs" role="tablist" aria-label="Verse report">
        <button
          type="button" role="tab" aria-selected={activeTab === "greekroom"}
          class:active={activeTab === "greekroom"} on:click={() => (activeTab = "greekroom")}
        >
          Greek Room
          {#if greekRoomChecking}<span class="tab-live" />{/if}
          {#if greekRoomOpenCount > 0}<span class="tab-count">{greekRoomOpenCount}</span>{/if}
        </button>
        <button
          type="button" role="tab" aria-selected={activeTab === "tntw"}
          class:active={activeTab === "tntw"} on:click={() => (activeTab = "tntw")}
        >
          tN/tW/Alignment
          {#if tntwOpenCount > 0}<span class="tab-count">{tntwOpenCount}</span>{/if}
        </button>
        <button
          type="button" role="tab" aria-selected={activeTab === "lqa"}
          class:active={activeTab === "lqa"} on:click={() => (activeTab = "lqa")}
        >
          Language QA
          {#if lqaFindings.length > 0}<span class="tab-count">{lqaFindings.length}</span>{/if}
        </button>
        <button
          type="button" role="tab" aria-selected={activeTab === "ai"}
          class:active={activeTab === "ai"} on:click={() => (activeTab = "ai")}
        >AI review</button>
      </div>

      <div class="tab-content">
      <div class="tab-panel" role="tabpanel" hidden={activeTab !== "tntw"}>
        <TranslationHelpsReview
          bind:this={translationHelpsReview}
          chapter={$currentChapter}
          verse={$selectedVerse}
          onStateChanged={nativeCheckStateChanged}
          onRerunAIReview={() => void startAIReview("verse")}
          aiReviewBusy={$checkingProgress.running || aiJobBusy}
        />

        <div class="section">
          <div class="section-title">Already computed in background pass</div>
          {#each $selectedFindings.filter((f) => !isGreekRoom(f.engine)) as f}
            <div class="finding source-{categoryClass(f.category)}">
              <div class="verdict">
                {#if findingNumberMap.has(f.id)}<span class="finding-num-badge" title="Marked in the verse text">{findingNumberMap.get(f.id)}</span>{/if}
                <span class="badge {severityBadge[f.severity]}">{f.severity}</span>
                <span class="check-id">{f.category}</span>
                {#if f.status !== "open"}<span class="badge badge-decided">{f.status}</span>{/if}
                {#if decisionSaveState[f.id] === "saving"}
                  <span class="save-state">Saving…</span>
                {:else if decisionSaveState[f.id] === "saved"}
                  <span class="save-state saved">✓ Saved</span>
                {:else if decisionSaveState[f.id] === "error"}
                  <span class="save-state failed" title={decisionSaveError[f.id]}>Save failed</span>
                {/if}
              </div>
              <p class="explain">{f.explanation}</p>
            </div>
          {:else}
            <p class="none">No local QA findings.</p>
          {/each}
        </div>
      </div>

      {#if activeTab === "greekroom"}
        {@const grFindings = $selectedFindings.filter((f) => isGreekRoom(f.engine))}
        {@const grOpenFindings = grFindings.filter((f) => f.status !== "ignored" && f.status !== "accepted").sort(byFindingNumber)}
        {@const grIgnoredFindings = grFindings.filter((f) => f.status === "ignored")}
        {@const grAcceptedFindings = grFindings.filter((f) => f.status === "accepted")}
        <div class="tab-panel" role="tabpanel">
          <div class="section">
            <div class="section-title">
              Greek Room QA
              {#if greekRoomChecking}
                <span class="live"><span class="spin" /> live check</span>
              {/if}
            </div>
            {#each grOpenFindings as f}
              <div class="finding source-{categoryClass(f.category)}">
                <div class="verdict">
                  {#if findingNumberMap.has(f.id)}<span class="finding-num-badge" title="Marked in the verse text">{findingNumberMap.get(f.id)}</span>{/if}
                  <span class="badge {severityBadge[f.severity]}">{f.severity}</span>
                  <span class="engine-badge">{f.engine}</span>
                  <span class="check-id">{f.check_type}</span>
                  {#if f.status !== "open"}<span class="badge badge-decided">{f.status}</span>{/if}
                  {#if decisionSaveState[f.id] === "saving"}
                    <span class="save-state">Saving…</span>
                  {:else if decisionSaveState[f.id] === "saved"}
                    <span class="save-state saved">✓ Saved</span>
                  {:else if decisionSaveState[f.id] === "error"}
                    <span class="save-state failed" title={decisionSaveError[f.id]}>Save failed</span>
                  {/if}
                </div>
                <p class="explain">{f.explanation}</p>
                {#if f.evidence.length > 0}
                  <ul class="evidence">
                    {#each f.evidence as e}<li>{e.label}: {e.value}</li>{/each}
                  </ul>
                {/if}
                <div class="decision-row two-up">
                  <button
                    class="edit-inline"
                    on:click={() => acceptAndEdit(f.id)}
                    disabled={$checkingProgress.running || Boolean($editingChapter) || $editSaving || Boolean($recheckingKey)}
                    title={$checkingProgress.running ? "Wait for background checking to finish before editing" : "Edit this verse and mark this finding accepted"}
                  >✎ Accept and edit</button>
                  <button class="ignore" disabled={decisionSaveState[f.id] === "saving"} on:click={() => decide(f.id, "ignored")}>⊘ Ignore</button>
                </div>
              </div>
            {/each}
            {#if grFindings.length === 0 && !greekRoomChecking}<p class="none">No Greek Room findings.</p>{/if}
          </div>

          {#if grAcceptedFindings.length > 0}
            <details class="section accepted-section">
              <summary class="section-title ignored-summary">Accepted ({grAcceptedFindings.length})</summary>
              {#each grAcceptedFindings as f}
                <div class="finding source-{categoryClass(f.category)}">
                  <div class="verdict">
                    <span class="badge {severityBadge[f.severity]}">{f.severity}</span>
                    <span class="engine-badge">{f.engine}</span>
                    <span class="check-id">{f.check_type}</span>
                    <span class="badge badge-decided">{f.status}</span>
                    {#if decisionSaveState[f.id] === "saving"}
                      <span class="save-state">Saving…</span>
                    {:else if decisionSaveState[f.id] === "saved"}
                      <span class="save-state saved">✓ Saved</span>
                    {:else if decisionSaveState[f.id] === "error"}
                      <span class="save-state failed" title={decisionSaveError[f.id]}>Save failed</span>
                    {/if}
                  </div>
                  <p class="explain">{f.explanation}</p>
                  <div class="decision-row one-up">
                    <button class="undo-accept" disabled={decisionSaveState[f.id] === "saving"} on:click={() => decide(f.id, "open")}>↺ Undo accept</button>
                  </div>
                </div>
              {/each}
            </details>
          {/if}

          {#if grIgnoredFindings.length > 0}
            <details class="section ignored-section">
              <summary class="section-title ignored-summary">Ignored ({grIgnoredFindings.length})</summary>
              {#each grIgnoredFindings as f}
                <div class="finding source-{categoryClass(f.category)}">
                  <div class="verdict">
                    {#if findingNumberMap.has(f.id)}<span class="finding-num-badge" title="Marked in the verse text">{findingNumberMap.get(f.id)}</span>{/if}
                    <span class="badge {severityBadge[f.severity]}">{f.severity}</span>
                    <span class="engine-badge">{f.engine}</span>
                    <span class="check-id">{f.check_type}</span>
                    <span class="badge badge-decided">{f.status}</span>
                    {#if decisionSaveState[f.id] === "saving"}
                      <span class="save-state">Saving…</span>
                    {:else if decisionSaveState[f.id] === "saved"}
                      <span class="save-state saved">✓ Saved</span>
                    {:else if decisionSaveState[f.id] === "error"}
                      <span class="save-state failed" title={decisionSaveError[f.id]}>Save failed</span>
                    {/if}
                  </div>
                  <p class="explain">{f.explanation}</p>
                  <div class="decision-row two-up">
                    <button
                      class="edit-inline"
                      on:click={startEdit}
                      disabled={$checkingProgress.running || Boolean($editingChapter) || $editSaving || Boolean($recheckingKey)}
                      title={$checkingProgress.running ? "Wait for background checking to finish before editing" : "Edit this verse"}
                    >✎ Edit verse</button>
                    <button class="undo-ignore" disabled={decisionSaveState[f.id] === "saving"} on:click={() => decide(f.id, "open")}>↺ Undo ignore</button>
                  </div>
                </div>
              {/each}
            </details>
          {/if}
        </div>
      {:else if activeTab === "lqa"}
        <div class="tab-panel" role="tabpanel">
          <div class="section">
            <div class="section-title">Language QA</div>
            {#if lqaNotice}<p class="lqa-notice" class:failed={lqaNoticeError} role="status">{lqaNotice}</p>{/if}
            {#each lqaFindings as f (f.id)}
              <div class="finding lqa-finding" data-lqa-id={f.id}>
                <div class="verdict">
                  <span class="badge {severityBadge[f.severity] ?? 'badge-review'}">{f.severity}</span>
                  <span class="engine-badge">{isLearnedFinding(f) ? "learned fix" : f.category}</span>
                  <span class="check-id">{f.ruleId}</span>
                  {#if f.previouslyIgnored}<span class="badge badge-decided" title="Ignored under an older rule version; check it again">re-check</span>{/if}
                  {#if f.context}<span class="engine-badge" title={f.context === "heading" ? `Section heading: ${f.contextText ?? ""}` : "Footnote text"}>in {f.context}</span>{/if}
                </div>
                <p class="explain"><b>{f.originalText}</b> — {f.message}</p>
                {#if f.reference}<p class="explain lqa-reference"><b>{f.reference.label} {f.reference.ref}:</b> {f.reference.text}</p>{/if}
                <div class="decision-row lqa-actions">
                  {#each lqaSuggestions(f) as s (s.rank)}
                    <button class="edit-inline" disabled={lqaBusy || $checkingProgress.running || Boolean($editingChapter)}
                      title={s.rationale || "Replace the flagged text with this form and re-check the verse"}
                      on:click={() => useLqa(f, s)}>Use “{s.text}”</button>
                  {/each}
                  <button class="ignore" on:click={() => decideLqa(f, "ignored")}>⊘ Ignore</button>
                  <button class="ignore" on:click={() => decideLqa(f, "rejected")}>False positive</button>
                  {#if isLearnedFinding(f)}
                    <button class="ignore" title="Stop offering this replacement anywhere in the book; it can be restored"
                      on:click={() => forgetLqa(f)}>Forget this fix</button>
                  {/if}
                </div>
              </div>
            {:else}
              <p class="none">
                {#if !lqaStatus}Language QA has not reported yet.
                {:else if lqaStatus.state !== "completed"}Language QA is checking…
                {:else}No Language QA findings on this verse.{/if}
              </p>
            {/each}
          </div>
          {#if lqaHidden.length > 0}
            <details class="section ignored-section">
              <summary class="section-title ignored-summary">Decided ({lqaHidden.length})</summary>
              {#each lqaHidden as f (f.id)}
                <div class="finding lqa-finding">
                  <div class="verdict">
                    <span class="check-id">{f.ruleId}</span>
                    <span class="badge badge-decided">{f.decision === "rejected" ? "false positive" : f.decision}</span>
                  </div>
                  <p class="explain"><b>{f.originalText}</b> — {f.message}</p>
                </div>
              {/each}
            </details>
          {/if}
        </div>
      {:else if activeTab === "ai"}
        <div class="tab-panel" role="tabpanel">
          <!-- All three scopes together, so the choice is one row and the
               progress/errors for whichever ran sit directly under it.
               Narrowest scope first, so the cheapest and most common run is
               the leftmost. Verse waits on the verse-edit lifecycle as well
               as a background check and a running job, because it is the
               scope an open editor would conflict with; Chapter and Book
               wait only on the latter two. -->
          <div class="ai-run-row">
            <span class="ai-run-label">🤖 AI review:</span>
            <div class="ai-run-buttons">
              <button
                class="ai-scope-btn"
                on:click={() => startAIReview("verse")}
                disabled={$checkingProgress.running || Boolean($editingChapter) || $editSaving || Boolean($recheckingKey) || aiJobBusy}
                title="Run an evidence-grounded AI review for this verse in the background"
              >Verse</button>
              <button
                class="ai-scope-btn"
                on:click={() => startAIReview("chapter")}
                disabled={$checkingProgress.running || aiJobBusy}
                title="Run an evidence-grounded AI review across every verse in this chapter"
              >Chapter</button>
              <button
                class="ai-scope-btn"
                on:click={() => startAIReview("book")}
                disabled={$checkingProgress.running || aiJobBusy}
                title="Run an evidence-grounded AI review across every verse in this book"
              >Book</button>
            </div>
          </div>
        {#if visibleAIJob || aiJobBusy || visibleAIExplainError}
        <div class="section ai-review-controls">
          {#if visibleAIJob}
            <div class="ai-job-status" class:failed={visibleAIJob.state === "failed"}>
              <div><b>{visibleAIJob.state === "succeeded" ? "Complete" : visibleAIJob.currentStage}</b><span>{visibleAIJob.percent}%</span></div>
              <progress max="100" value={visibleAIJob.percent} />
              <small>{visibleAIJob.completedVerses}/{visibleAIJob.totalVerses} verses{visibleAIJob.failedVerses ? ` · ${visibleAIJob.failedVerses} failed` : ""}</small>
              {#if aiSelectionTally.applied + aiSelectionTally.pending > 0}
                <small class="ai-selection-tally">
                  <b>{aiSelectionTally.applied}</b> {aiSelectionTally.applied === 1 ? "check" : "checks"} selected automatically ·
                  <b>{aiSelectionTally.pending}</b> left for review
                </small>
              {/if}
              {#if visibleAIJob.skippedCurrentVerses > 0}
                <small>{visibleAIJob.skippedCurrentVerses} already-current verse(s) preserved and skipped.</small>
              {/if}
              {#if visibleAIJob.resumeOf}<small>Resumed from the previous unfinished job.</small>{/if}
              {#if aiFailedResults.length > 0}
                <div class="ai-failure-list">
                  {#each aiFailedResults.slice(0, 3) as failure}
                    <div><b>{failure.chapter}:{failure.verse}</b> — {failure.error || "Unknown AI review error"}</div>
                  {/each}
                  {#if aiFailedResults.length > 3}<div>+ {aiFailedResults.length - 3} more failed verse(s)</div>{/if}
                </div>
              {/if}
              <div class="ai-job-actions">
                {#if ["queued", "running", "cancelling"].includes(visibleAIJob.state)}
                  <button on:click={cancelAIReview} disabled={visibleAIJob.state === "cancelling"}>{visibleAIJob.state === "cancelling" ? "Cancelling…" : "Cancel"}</button>
                {:else if ["failed", "cancelled"].includes(visibleAIJob.state)}
                  <button on:click={retryAIReview}>Retry</button>
                {/if}
              </div>
            </div>
          {:else if aiJobBusy}
            <div class="ai-job-background" role="status">
              An AI review is continuing in the background for another reference. Return to its starting reference to view progress or cancel it.
            </div>
          {/if}
          {#if visibleAIExplainError}<p class="ai-control-error">{visibleAIExplainError}</p>{/if}
        </div>
        {/if}
          {#if visibleAIExplainError}
            <div class="section ai-explain-section">
              <div class="section-title">AI explanation</div>
              <p class="ai-error">{visibleAIExplainError}</p>
            </div>
          {:else if aiExplainResult}
            <div class="section ai-explain-section">
              <div class="section-title">
                AI explanation
                <span class="ai-cost">~${aiExplainResult.usage.estimatedCostUSD.toFixed(4)}</span>
              </div>
              <p class="ai-summary">{aiExplainResult.summary}</p>
              {#each aiExplainResult.checkReviews as review}
                <div class="finding ai-check-review">
                  <div class="verdict">
                    <span class="badge {severityBadge[review.severity] ?? 'badge-review'}">{review.verdict}</span>
                    <span class="check-id">{review.tool}{review.group_id ? ` · ${review.group_id}` : ""}</span>
                  </div>
                  <p class="explain">{review.rationale}</p>
                  {#if review.suggested_correction}<p class="ai-suggestion">Suggested: {review.suggested_correction}</p>{/if}
                </div>
              {/each}
              {#each aiExplainResult.qaIssues as issue}
                <div class="finding ai-qa-issue">
                  <div class="verdict">
                    <span class="badge {severityBadge[issue.severity] ?? 'badge-review'}">{issue.severity}</span>
                    <span class="check-id">{issue.title}</span>
                  </div>
                  <p class="explain">{issue.detail}</p>
                </div>
              {/each}
              {#if aiExplainResult.checkReviews.length === 0 && aiExplainResult.qaIssues.length === 0}
                <p class="none">AI found nothing to flag for this verse.</p>
              {/if}
            </div>
          {:else}
            <p class="none">No AI explanation yet for this verse — run "AI review" below.</p>
          {/if}
        </div>
      {/if}
      </div>

    </div>

  {:else}
    <div class="empty-panel">Select a verse to review its findings.</div>
  {/if}
</div>

{#if $alignmentOpen && $selectedVerse}
  <AlignmentModal chapter={$currentChapter} verse={$selectedVerse} onClose={() => alignmentOpen.set(false)} />
{/if}

<style>
  .panel { width: 400px; flex-shrink: 0; background: var(--surface); display: flex; flex-direction: column; overflow: hidden; border-left: 1px solid var(--border); }
  .panel-header { padding: 14px 16px; border-bottom: 1px solid var(--border); display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
  .header-title { flex: 1 1 auto; min-width: 0; }
  .panel-pinned { flex-shrink: 0; padding: 14px 16px; border-bottom: 1px solid var(--border); overflow-y: auto; max-height: 60vh; }
  .ref { font-size: var(--fs-md); font-weight: 700; color: var(--text); }
  .sub { font-size: var(--fs-xs); color: var(--text-2); margin-top: 2px; }
  .panel-scroll { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
  .operation-status { display: flex; align-items: center; gap: 7px; border-radius: 8px; padding: 8px 10px; margin-bottom: 12px; font-size: var(--fs-xs); line-height: 1.4; }
  .operation-status.checking { color: var(--accent); background: var(--accent-bg); }
  .operation-status.saved { color: var(--success); background: var(--success-bg); }
  .operation-status.failed { color: var(--danger); background: var(--danger-bg); }
  .tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--border); flex-shrink: 0; padding: 14px 16px 0; background: var(--surface); }
  .tab-content { flex: 1; overflow-y: auto; padding: 14px 16px; }
  .tabs button {
    flex: 1; display: flex; align-items: center; justify-content: center; gap: 5px;
    padding: 8px 6px; font-size: var(--fs-2xs); font-weight: 700; color: var(--text-2);
    background: none; border: none; border-bottom: 2px solid transparent; border-radius: 0;
    cursor: pointer;
  }
  .tabs button:hover:not(.active) { color: var(--text); }
  .tabs button.active { color: var(--accent); border-bottom-color: var(--accent); }
  .tab-count {
    font-size: var(--fs-3xs); font-weight: 700; padding: 1px 6px; border-radius: 999px;
    background: var(--accent-bg); color: var(--accent);
  }
  .tabs button.active .tab-count { background: var(--accent); color: white; }
  .tab-live { width: 6px; height: 6px; border-radius: 50%; background: var(--pass); flex-shrink: 0; }
  .tab-panel:empty { display: none; }
  .section { border: 1px solid var(--border); border-radius: 12px; padding: 12px 14px; margin-bottom: 12px; }
  .section-title { font-size: var(--fs-xs); font-weight: 700; color: var(--text); display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
  .ignored-summary { cursor: pointer; user-select: none; margin-bottom: 0; list-style: none; }
  .ignored-summary::-webkit-details-marker { display: none; }
  .ignored-summary::before { content: "▸"; font-size: var(--fs-3xs); color: var(--text-3); transition: transform 0.15s ease; }
  .ignored-section[open] .ignored-summary, .accepted-section[open] .ignored-summary { margin-bottom: 8px; }
  .ignored-section[open] .ignored-summary::before, .accepted-section[open] .ignored-summary::before { transform: rotate(90deg); }
  .live { display: flex; align-items: center; gap: 5px; font-size: var(--fs-2xs); font-weight: 700; color: var(--pass); }
  .spin { width: 10px; height: 10px; border-radius: 50%; border: 2px solid var(--pass-bg); border-top-color: var(--pass); animation: spin 0.8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .finding { border-top: 1px dashed var(--border); padding-top: 10px; margin-top: 10px; }
  .finding:first-child { border-top: none; padding-top: 0; margin-top: 0; }
  /* Same four-colour source legend as the verse-text underlines (index.css
     mark.m-*), so a green underline in the verse and its tN entry here read
     as one thing. Class names come from highlight.ts's categoryClass(). */
  .finding[class*="source-m-"] { border-left: 3px solid transparent; padding-left: 8px; }
  .finding.source-m-tn { border-left-color: var(--tn); }
  .finding.source-m-tw { border-left-color: var(--tw); }
  .finding.source-m-align { border-left-color: var(--align); }
  .finding.source-m-gr { border-left-color: var(--gr); }
  .verdict { display: flex; align-items: center; flex-wrap: wrap; gap: 6px 8px; margin-bottom: 6px; }
  .badge { font-size: var(--fs-2xs); font-weight: 700; padding: 3px 8px; border-radius: 5px; flex-shrink: 0; }
  .badge-wrong { background: var(--danger-bg); color: var(--danger); }
  .badge-review { background: var(--warning-bg); color: var(--warning); }
  .badge-decided { background: var(--success-bg); color: var(--success); text-transform: capitalize; }
  .check-id { font-size: var(--fs-xs); color: var(--text-3); min-width: 0; overflow-wrap: anywhere; }
  .engine-badge { font-size: var(--fs-3xs); font-weight: 700; text-transform: capitalize; color: var(--accent); background: var(--accent-bg); padding: 2px 7px; border-radius: 999px; flex-shrink: 0; }
  .finding-num-badge {
    display: inline-flex; align-items: center; justify-content: center; width: 16px; height: 16px;
    font-size: var(--fs-3xs); font-weight: 800; color: white; background: var(--accent); border-radius: 50%;
    flex-shrink: 0;
  }
  .save-state { margin-left: auto; font-size: var(--fs-2xs); color: var(--text-3); white-space: nowrap; }
  .save-state.saved { color: var(--success); }
  .save-state.failed { color: var(--danger); }
  .explain { font-size: var(--fs-sm); color: var(--text-2); line-height: 1.6; margin: 0 0 8px; }
  .evidence { font-size: var(--fs-xs); color: var(--text-2); padding-left: 16px; margin: 0 0 8px; }
  .decision-row { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 6px; }
  .decision-row.two-up { grid-template-columns: 1fr 1fr; }
  .decision-row.one-up { grid-template-columns: 1fr; }
  .decision-row.lqa-actions { display: flex; flex-wrap: wrap; }
  .decision-row.lqa-actions button { flex: 1 1 auto; }
  .lqa-notice { margin: 0 0 6px; font-size: var(--fs-xs); color: var(--text-3); }
  .lqa-notice.failed { color: var(--danger); }
  .decision-row button { padding: 7px; font-size: var(--fs-xs); font-weight: 700; border-radius: 6px; border: none; cursor: pointer; }
  .accept { background: var(--success); color: #fff; }
  .ignore { background: #F5EBFC; color: #9333EA; }
  .undo-ignore, .undo-accept { background: var(--surface-2); color: var(--text-2); border: 1px solid var(--border-strong); }
  .edit-inline { background: var(--accent-bg); color: var(--accent); }
  .none { font-size: var(--fs-xs); color: var(--text-3); }
  .decision-row button:disabled { opacity: .55; cursor: not-allowed; }
  .verse-actions { display: flex; gap: 8px; flex: 0 0 auto; margin-left: auto; }
  .edit-btn { padding: 8px 10px; font-size: var(--fs-xs); font-weight: 700; border-radius: 7px; border: none; background: var(--accent-bg); color: var(--accent); cursor: pointer; white-space: nowrap; }
  .align-btn { padding: 8px 10px; font-size: var(--fs-xs); font-weight: 700; border-radius: 7px; border: 1px solid var(--border-strong); background: var(--surface); color: var(--text); cursor: pointer; white-space: nowrap; }
  .ai-run-row { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
  .ai-run-label { font-size: var(--fs-sm); font-weight: 700; color: var(--text); white-space: nowrap; }
  .ai-run-buttons { display: flex; gap: 6px; margin-left: auto; }
  .ai-scope-btn { padding: 7px 12px; font-size: var(--fs-xs); font-weight: 700; border-radius: 7px; border: 1px solid var(--border-strong); background: var(--surface); color: var(--accent); cursor: pointer; white-space: nowrap; }
  .edit-btn:disabled, .align-btn:disabled, .ai-scope-btn:disabled { opacity: .55; cursor: not-allowed; }
  .empty-panel { padding: 24px 16px; font-size: var(--fs-sm); color: var(--text-3); }
  .ai-explain-section { border-color: var(--accent); }
  .ai-cost { margin-left: auto; font-size: var(--fs-2xs); font-weight: 400; color: var(--text-3); }
  .ai-summary { font-size: var(--fs-sm); color: var(--text); line-height: 1.5; margin: 0 0 10px; }
  .ai-error { font-size: var(--fs-sm); color: var(--danger); line-height: 1.5; margin: 0; }
  .ai-suggestion { font-size: var(--fs-xs); color: var(--accent); margin: -4px 0 8px; }
  .ai-review-controls { border-color: var(--accent); }
  .ai-job-actions button { padding: 6px; font-size: var(--fs-2xs); font-weight: 700; border-radius: 6px; border: 1px solid var(--border-strong); color: var(--accent); background: var(--surface); cursor: pointer; }
  .ai-job-actions button:disabled { opacity: .55; cursor: not-allowed; }
  .ai-job-status { margin-top: 9px; padding: 8px; border-radius: 7px; color: var(--accent); background: var(--accent-bg); }
  .ai-job-status.failed { color: var(--danger); background: var(--danger-bg); }
  .ai-job-status > div:first-child { display: flex; justify-content: space-between; gap: 8px; font-size: var(--fs-2xs); }
  .ai-job-status progress { width: 100%; height: 6px; margin: 5px 0; accent-color: var(--accent); }
  .ai-job-status small { display: block; font-size: var(--fs-3xs); color: var(--text-3); }
  .ai-selection-tally { margin-top: 3px; color: inherit; }
  .ai-selection-tally b { color: inherit; }
  .ai-failure-list { margin-top: 7px; padding-top: 6px; border-top: 1px solid color-mix(in srgb, var(--danger) 25%, transparent); font-size: var(--fs-3xs); line-height: 1.4; overflow-wrap: anywhere; }
  .ai-job-actions { margin-top: 6px; }
  .ai-job-background { margin-top: 9px; padding: 8px; border-radius: 7px; font-size: var(--fs-3xs); line-height: 1.4; color: var(--text-2); background: var(--surface-2); }
  .ai-control-error { margin: 8px 0 0; font-size: var(--fs-2xs); line-height: 1.4; color: var(--danger); overflow-wrap: anywhere; }
</style>
