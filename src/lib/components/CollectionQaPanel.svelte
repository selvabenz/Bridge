<script lang="ts">
  // Collection QA (layered-rules Phase 4.4): run every check over every book
  // of the collection, one book after another, and watch it here. Starting a
  // run is always the user's choice (never automatic after an import): a
  // whole Bible is 66 sequential book jobs on one worker thread, an operation
  // meant to run unattended. While it runs the app is read-only.
  import { onMount } from "svelte";
  import {
    cancelCollectionQa, collectionQa, pauseCollectionQa, refreshCollectionQa, startCollectionQa,
  } from "../collectionQa";
  import type { CollectionQaBook } from "../types/collectionQa";
  import { collectionQaCollapsed } from "../editorPrefs";

  export let bookCount = 0;
  export let onOpenBook: (path: string) => void = () => {};

  let error = "";
  let busy = false;

  onMount(() => {
    void refreshCollectionQa().catch((e) => { error = e instanceof Error ? e.message : String(e); });
  });

  $: snapshot = $collectionQa;
  $: active = snapshot ? ["queued", "running", "cancelling"].includes(snapshot.state) : false;
  $: lastRun = snapshot?.books.some((b) => b.completedAt) ?? false;
  // Folded to its header row when the reviewer asks; a run in progress
  // always shows its table.
  $: open = !$collectionQaCollapsed || active;
  $: summary = snapshot ? collapsedSummary(snapshot.books) : "";

  /** One line for the folded panel: how many books, how they last ended,
   * and when the latest of them finished. */
  function collapsedSummary(books: CollectionQaBook[]): string {
    const count = (state: string) => books.filter((b) => b.state === state).length;
    const parts = [`${books.length} ${books.length === 1 ? "book" : "books"}`];
    for (const [state, label] of [["done", "done"], ["skipped", "unchanged"], ["failed", "failed"]] as const) {
      if (count(state)) parts.push(`${count(state)} ${label}`);
    }
    const latest = books.map((b) => b.completedAt).filter((at): at is string => Boolean(at)).sort().pop();
    parts.push(latest ? `last run ${new Date(latest).toLocaleString()}` : "never run");
    return parts.join(" · ");
  }

  async function act(fn: () => Promise<void>): Promise<void> {
    if (busy) return;
    busy = true;
    error = "";
    try {
      await fn();
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      busy = false;
    }
  }

  function duration(seconds: number | null | undefined): string {
    if (seconds === null || seconds === undefined) return "—";
    if (seconds < 60) return `${Math.round(seconds)} s`;
    const minutes = Math.floor(seconds / 60);
    return minutes < 60 ? `${minutes} min ${Math.round(seconds % 60)} s` : `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
  }

  function openFindings(book: CollectionQaBook): string {
    const entries = Object.entries(book.findingsByCategory).filter(([, n]) => n > 0);
    return entries.length ? entries.map(([k, n]) => `${k} ${n}`).join(" · ") : "none";
  }

  const STATE_LABEL: Record<string, string> = {
    pending: "Pending", running: "Running", done: "Done", skipped: "Unchanged", failed: "Failed",
  };
</script>

<section class="collection-qa" aria-label="Collection QA">
  <div class="head">
    <button type="button" class="fold" aria-expanded={open} aria-controls="collection-qa-body"
      aria-label={open ? "Collapse Collection QA" : "Expand Collection QA"} disabled={active}
      title={active ? "Stays open while a run is in progress" : open ? "Collapse" : "Expand"}
      on:click={() => collectionQaCollapsed.update((folded) => !folded)}>{open ? "▾" : "▸"}</button>
    <h3>Collection QA</h3>
    {#if !open && summary}<span class="progress">{summary}</span>{/if}
    {#if snapshot && active}
      <span class="progress">{snapshot.completedBooks}/{snapshot.totalBooks} books ·
        {duration(snapshot.elapsedSeconds)} elapsed{#if snapshot.estimatedRemainingSeconds !== null} ·
        about {duration(snapshot.estimatedRemainingSeconds)} left{/if}{#if snapshot.paused} · paused{/if}</span>
    {/if}
    <div class="actions">
      {#if active}
        <button on:click={() => act(() => pauseCollectionQa(!snapshot?.paused))} disabled={busy || snapshot?.state === "cancelling"}>
          {snapshot?.paused ? "Resume" : "Pause after this book"}</button>
        <button class="danger" on:click={() => act(cancelCollectionQa)} disabled={busy || snapshot?.state === "cancelling"}>Cancel</button>
      {:else}
        <button class="primary" on:click={() => act(() => startCollectionQa(false))} disabled={busy}
          title="Checks every book in turn; books unchanged since their last run are skipped">
          Run QA on all {bookCount} {bookCount === 1 ? "book" : "books"}</button>
        {#if lastRun}
          <button on:click={() => act(() => startCollectionQa(true))} disabled={busy}
            title="Check every book again, including unchanged ones">Run all again</button>
        {/if}
      {/if}
    </div>
  </div>
  {#if error}<p class="error" role="alert">{error}</p>{/if}
  {#if snapshot?.error}<p class="error">{snapshot.error}</p>{/if}
  {#if open}
  <div id="collection-qa-body">
  <p class="note">
    Runs tN/tW/alignment, Greek Room and Language QA over each book, one at a time. It is meant to run
    unattended: while it runs, editing and switching books are paused. It is never started automatically.
  </p>
  {#if snapshot && snapshot.books.length}
    <table>
      <thead><tr><th>Book</th><th>State</th><th>Verses checked</th><th>Open findings</th><th>Last run</th><th>Time</th><th /></tr></thead>
      <tbody>
        {#each snapshot.books as book (book.bookId)}
          <tr class={book.state} data-book={book.bookId}>
            <td>{book.bookName || book.bookId.toUpperCase()}</td>
            <td>{STATE_LABEL[book.state] ?? book.state}{#if book.error}<span class="book-error" title={book.error}> ⚠</span>{/if}</td>
            <td>{book.checkedVerses || "—"}</td>
            <td>{openFindings(book)}</td>
            <td>{book.completedAt ? new Date(book.completedAt).toLocaleString() : "—"}</td>
            <td>{duration(book.elapsedSeconds)}</td>
            <td><button class="link" disabled={active} on:click={() => onOpenBook(book.path)}>Open book</button></td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}
  {#if snapshot?.finalStage}
    <details class="final">
      <summary>Whole-collection checks{#if snapshot.finalStage.completedAt} · {new Date(snapshot.finalStage.completedAt).toLocaleString()}{/if}</summary>
      <ul>
        <li>Termbase coverage: {(snapshot.finalStage.termbaseCoverage ?? []).reduce((n, b) => n + b.issues.length, 0)} concept issue(s) across {(snapshot.finalStage.termbaseCoverage ?? []).length} book(s).</li>
        <li>Cross-book names: {#if snapshot.finalStage.crossBookNames?.available}{snapshot.finalStage.crossBookNames.findings?.length ?? 0} spelling pair(s){:else if snapshot.finalStage.crossBookNames?.error}unavailable ({snapshot.finalStage.crossBookNames.error}){:else}not run{/if}.</li>
        <li>House-style propagation: {snapshot.finalStage.houseStylePropagation?.reason ?? "not available"}</li>
      </ul>
    </details>
  {/if}
  </div>
  {/if}
</section>

<style>
  .collection-qa { border: 1px solid var(--border, #e5e7eb); border-radius: 8px; padding: 12px 14px; margin: 0 0 16px; }
  .head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  h3 { margin: 0; font-size: var(--fs-md, 15px); }
  .fold { border: 0; padding: 0 2px; width: 20px; font-size: var(--fs-xs, 12px); color: var(--text-2, #4b5563); }
  .fold:disabled { opacity: .4; }
  .progress { color: var(--text-3, #6b7280); font-size: var(--fs-xs, 12px); }
  .actions { margin-left: auto; display: flex; gap: 8px; flex-wrap: wrap; }
  button { padding: 6px 10px; border-radius: 6px; border: 1px solid var(--border, #d1d5db); background: none; cursor: pointer; font-size: var(--fs-xs, 12px); }
  button.primary { background: var(--accent, #2563eb); color: #fff; border-color: transparent; }
  button.danger { color: var(--danger, #b91c1c); }
  button.link { border: none; text-decoration: underline; padding: 0; }
  button:disabled { opacity: .55; cursor: not-allowed; }
  .note { margin: 6px 0 8px; color: var(--text-3, #6b7280); font-size: var(--fs-xs, 12px); }
  .error { color: var(--danger, #b91c1c); font-size: var(--fs-xs, 12px); }
  table { width: 100%; border-collapse: collapse; font-size: var(--fs-xs, 12px); }
  th, td { text-align: left; padding: 4px 6px; border-bottom: 1px solid var(--border, #eee); }
  tr.running td:first-child { font-weight: 700; }
  tr.failed td:nth-child(2) { color: var(--danger, #b91c1c); }
  .final { margin-top: 8px; font-size: var(--fs-xs, 12px); }
</style>
