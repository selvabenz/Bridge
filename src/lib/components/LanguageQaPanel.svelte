<script lang="ts">
  import { onDestroy } from "svelte";
  import { bridge } from "../api/bridgeClient";
  import { languageQaChannel, nudgeLanguageQa } from "../languageQaInline";
  import type { LanguageQaStatus, LanguageQaView } from "../types/languageQa";
  import LanguageQaHistoryList from "./LanguageQaHistoryList.svelte";

  export let projectPath: string;
  export let onNavigate: (book: string, chapter: string, verse: string) => void;

  let expanded = false;
  // Which list is paged: every open finding; those shown again because the
  // rule changed since they were ignored; or those marked as false positives.
  let view: LanguageQaView = "findings";
  // Finding ids whose decision history is expanded.
  let historyOpen = new Set<string>();
  let offset = 0;
  let sequence = 0;
  let disposed = false;
  let busy = false;
  let localError = "";
  // The page on show while expanded, fetched on demand (see below).
  let page: LanguageQaStatus | null = null;
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
  $: pageKey = expanded && live ? `${view}|${offset}|${live.generation}|${live.state}` : "";
  $: if (pageKey) void loadPage(pageKey);
  $: if (!expanded) page = null;

  async function loadPage(key: string): Promise<void> {
    const ticket = ++sequence;
    const path = projectPath;
    busy = true;
    try {
      const next = await bridge.languageQaStatus(path, offset, 50, view);
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

  function showView(next: LanguageQaView): void {
    if (view === next) return;
    view = next;
    offset = 0;
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

<aside class="language-qa" aria-label="Language QA">
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
        </p>
        {#if status.language}<p>{detectionLabels[status.language.basis] ?? ""}</p>{/if}
        {#if status.coverage?.inScope}
          <!-- Always shown, so a clean result is never read as a review (Phase 7). -->
          <div class="scope" aria-label="What Language QA checks">
            <div>
              <h3>Checks · <span lang="ta">சரிபார்ப்பவை</span></h3>
              <ul>
                {#each status.coverage.inScope as row (row.category)}
                  <li>{row.label}{#if row.labelTa} · <span lang="ta">{row.labelTa}</span>{/if}</li>
                {/each}
              </ul>
            </div>
            <div>
              <h3>Does not check · <span lang="ta">சரிபார்க்காதவை</span></h3>
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
        {#if view === "findings" && status.state === "completed" && !status.totalFindings}
          <p>No candidates found by the enabled checks. This is not publication approval.</p>
        {/if}
        <ol aria-label={view === "findings" ? "Language QA findings" : view === "recheck" ? "Findings to re-check" : "False positives"} start={status.offset + 1}>
          {#each status.findings as finding (finding.id)}
            <li>
              <button on:click={() => onNavigate(finding.book, finding.chapter, finding.verse)}>
                {finding.book.toUpperCase()} {finding.chapter}:{finding.verse}
              </button>
              <span class="severity">{finding.severity}</span>
              {#if finding.category}<span class="category">{finding.category}</span>{/if}
              {#if finding.previouslyIgnored}<span class="recheck">Re-check</span>{/if}
              <p class="evidence">{finding.originalText}</p>
              <p>{finding.message}</p>
              <button class="history-toggle" aria-expanded={historyOpen.has(finding.id)} on:click={() => toggleHistory(finding.id)}>
                History
              </button>
              {#if historyOpen.has(finding.id)}
                <LanguageQaHistoryList {projectPath} chapter={finding.chapter} verse={finding.verse} findingId={finding.id} />
              {/if}
            </li>
          {/each}
        </ol>
        {#if status.totalFindings > 50}
          <div class="paging">
            <button on:click={() => turnPage(-50)} disabled={busy || offset === 0}>Previous</button>
            <span>{status.offset + 1}–{Math.min(status.offset + 50, status.totalFindings)} of {status.totalFindings}</span>
            <button on:click={() => turnPage(50)} disabled={busy || offset + 50 >= status.totalFindings}>Next</button>
          </div>
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
  .views { display: flex; gap: 6px; margin: 8px 0; }
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
