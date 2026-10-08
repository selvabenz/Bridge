<script lang="ts">
  // "Align chapter with AI" in the chapter toolbar (#221/#222). Four states in
  // one place, so the toolbar never grows a second row:
  //   idle      -> the button;
  //   confirm   -> the offline estimate, with Start / Cancel (nothing sent yet);
  //   running   -> progress and Cancel (cancel takes effect between requests);
  //   finished  -> the result, with Open review on the first verse that needs one.
  // The job itself is the engine's (alignment.autoAlign.*); this only starts,
  // watches and reports it.
  import { onDestroy, onMount } from "svelte";
  import { bridge } from "../api/bridgeClient";
  import type { AutoAlignEstimate, AutoAlignJobStatus } from "../types/finding";

  export let chapter: string;
  /** Opens the Cross-Verse Alignment page on these verses. */
  export let onOpenReview: (verse: string, verses: string[]) => void;
  /** Called once a job ends, so the editor can reload what it wrote. */
  export let onFinished: () => void = () => {};
  export let disabled = false;

  type Phase = "idle" | "estimating" | "confirm" | "running" | "finished";
  let phase: Phase = "idle";
  let hasKey: boolean | undefined = undefined;
  let estimate: AutoAlignEstimate | null = null;
  let job: AutoAlignJobStatus | null = null;
  let error = "";
  let timer: ReturnType<typeof setInterval> | null = null;

  onMount(async () => {
    try {
      hasKey = Boolean((await bridge.getSettings())?.hasApiKey);
    } catch {
      hasKey = false;
    }
  });
  onDestroy(() => stopPolling());

  // The chapter changed underneath an unstarted confirm: its estimate is for
  // another chapter. A running job keeps going; only the panel resets.
  let shownFor = chapter;
  $: if (chapter !== shownFor) {
    shownFor = chapter;
    if (phase === "confirm" || phase === "finished") phase = "idle";
  }

  async function askEstimate() {
    error = "";
    phase = "estimating";
    try {
      estimate = await bridge.autoAlignEstimate("chapter", [chapter]);
      phase = "confirm";
    } catch (value) {
      error = value instanceof Error ? value.message : String(value);
      phase = "idle";
    }
  }

  async function start() {
    error = "";
    try {
      const snapshot = await bridge.autoAlignStart("chapter", [chapter]);
      if (snapshot.unavailable) {
        error = snapshot.unavailable.message;
        phase = "idle";
        return;
      }
      job = snapshot;
      phase = "running";
      startPolling();
    } catch (value) {
      error = value instanceof Error ? value.message : String(value);
      phase = "idle";
    }
  }

  function startPolling() {
    stopPolling();
    timer = setInterval(() => { void poll(); }, 1000);
  }

  function stopPolling() {
    if (timer) clearInterval(timer);
    timer = null;
  }

  async function poll() {
    if (!job) return;
    try {
      job = await bridge.autoAlignStatus(job.jobId);
    } catch (value) {
      error = value instanceof Error ? value.message : String(value);
      stopPolling();
      return;
    }
    if (["succeeded", "failed", "cancelled"].includes(job.state)) {
      stopPolling();
      phase = "finished";
      if (job.state === "failed" && job.error) error = job.error;
      onFinished();
    }
  }

  async function cancel() {
    if (!job) return;
    try {
      job = await bridge.autoAlignCancel(job.jobId);
    } catch (value) {
      error = value instanceof Error ? value.message : String(value);
    }
  }

  function firstNeedingReview(current: AutoAlignJobStatus): string[] {
    const verses = Object.entries(current.verdicts)
      .filter(([, verdict]) => verdict === "NEEDS_REVIEW")
      .map(([key]) => key.split(":").slice(1).join(":"));
    return verses.slice(0, 3);
  }

  function openReview() {
    if (!job) return;
    const verses = firstNeedingReview(job);
    if (verses.length) onOpenReview(verses[0], verses);
  }

  function money(value: number): string {
    return value < 0.01 ? "under $0.01" : `about $${value.toFixed(2)}`;
  }

  $: clean = job?.verdictCounts?.ALIGNED_CLEAN ?? 0;
  $: review = job?.verdictCounts?.NEEDS_REVIEW ?? 0;
</script>

<span class="auto-chapter">
  {#if phase === "idle" || phase === "estimating"}
    <button
      class="whole-book-btn"
      on:click={askEstimate}
      disabled={disabled || !hasKey || phase === "estimating"}
      title={hasKey === false
        ? "Add an API key in Settings to align automatically"
        : "Align this chapter automatically: three verses at a time, each asked twice. Shows the cost before anything is sent."}
    >{phase === "estimating" ? "Working out the cost…" : "Align chapter with AI"}</button>
  {:else if phase === "confirm" && estimate}
    <span class="confirm" role="group" aria-label="Confirm automatic alignment">
      Chapter {chapter}: {estimate.windows} windows · {estimate.calls} requests ·
      ~{Math.round(estimate.estimatedInputTokens / 1000)}k tokens · {money(estimate.estimatedCostUSD)}
      <button class="whole-book-btn primary" on:click={start}>Start</button>
      <button class="whole-book-btn" on:click={() => (phase = "idle")}>Cancel</button>
    </span>
  {:else if phase === "running" && job}
    <span class="progress" role="status">
      <span class="spin" aria-hidden="true" />
      {job.state === "cancelling" ? "Cancelling…" : job.currentWindow
        ? `v.${job.currentWindow.verses[0]}–${job.currentWindow.verses[job.currentWindow.verses.length - 1]}`
        : "Starting…"} · {job.windowsDone}/{job.windowsTotal} windows
      <button class="whole-book-btn" on:click={cancel} disabled={job.state === "cancelling"}>Cancel</button>
    </span>
  {:else if phase === "finished" && job}
    <span class="result" class:mixed={review > 0 || job.state !== "succeeded"} role="status">
      {#if job.state === "cancelled"}
        Cancelled after {job.windowsDone} of {job.windowsTotal} windows: {clean} verses clean, {review} need review.
      {:else}
        Chapter {chapter} aligned: {clean} verse{clean === 1 ? "" : "s"} clean, {review} need{review === 1 ? "s" : ""} review.
      {/if}
      {#if review > 0}
        <button class="whole-book-btn" on:click={openReview}>Open review</button>
      {/if}
      <button class="dismiss" on:click={() => (phase = "idle")} aria-label="Dismiss">×</button>
    </span>
  {/if}
  {#if error}<span class="error" role="alert">{error}</span>{/if}
</span>

<style>
  .auto-chapter { display: inline-flex; align-items: center; gap: 6px; flex-wrap: wrap; }
  .whole-book-btn { font-size: var(--fs-xs); font-weight: 600; padding: 4px 10px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--surface); color: var(--text); cursor: pointer; }
  .whole-book-btn:disabled { opacity: 0.6; cursor: not-allowed; }
  .whole-book-btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
  .confirm, .progress, .result { display: inline-flex; align-items: center; gap: 6px; font-size: var(--fs-xs); color: var(--text-2); font-variant-numeric: tabular-nums; }
  .result { color: var(--success); }
  .result.mixed { color: #8A4B05; }
  .dismiss { border: 0; background: none; color: var(--text-3); font-size: var(--fs-md); cursor: pointer; padding: 0 4px; }
  .error { color: var(--danger); font-size: var(--fs-xs); }
  .spin { width: 12px; height: 12px; border: 2px solid var(--accent-bg); border-top-color: var(--accent); border-radius: 50%; animation: spin .8s linear infinite; display: inline-block; }
  @keyframes spin { to { transform: rotate(360deg); } }
  @media (prefers-reduced-motion: reduce) { .spin { animation: none; } }
</style>
