<script lang="ts">
  import { onMount } from "svelte";
  import { bridge } from "../api/bridgeClient";
  import { graphemeDiff } from "../utils/unicodeDiff";
  import { revertVerseTo } from "../verseEditor";
  import type { VerseHistoryEntry } from "../types/finding";

  // What a verse's ↺ opens: every recorded edit of the verse, newest first,
  // each with what changed. Restoring is an ordinary verse edit, so it is
  // journalled and appears here as the newest change.
  export let chapter: string;
  export let verse: string;
  export let reference: string;
  export let x: number;
  export let y: number;
  export let onClose: () => void;

  let box: HTMLDivElement | null = null;
  let left = x;
  let top = y;
  let entries: VerseHistoryEntry[] = [];
  let loaded = false;
  let error = "";
  let confirming = -1;
  let busy = false;

  onMount(() => {
    void load();
    const outside = (event: PointerEvent) => {
      if (box && !box.contains(event.target as Node)) onClose();
    };
    window.addEventListener("pointerdown", outside, true);
    return () => window.removeEventListener("pointerdown", outside, true);
  });

  async function load(): Promise<void> {
    try {
      entries = (await bridge.verseHistory({ chapter, verse })).entries;
    } catch (cause) {
      error = cause instanceof Error ? cause.message : String(cause);
    }
    loaded = true;
    // Inside the window, below the ↺ when there is room, else above it.
    await Promise.resolve();
    const rect = box?.getBoundingClientRect();
    const width = rect?.width ?? 420, height = rect?.height ?? 240;
    left = Math.max(8, Math.min(x, window.innerWidth - width - 8));
    top = y + height + 8 > window.innerHeight ? Math.max(8, y - height - 24) : y;
    box?.focus();
  }

  async function restore(index: number): Promise<void> {
    if (confirming !== index) {
      confirming = index;
      return;
    }
    busy = true;
    error = "";
    const ok = await revertVerseTo(chapter, verse, entries[index].verseBefore);
    busy = false;
    if (ok) onClose();
    else error = "The verse could not be restored now (another edit or a check is running).";
  }

  function kind(entry: VerseHistoryEntry): string {
    if (entry.undoes) return "undo of a change set";
    if (entry.batchId) return "change set";
    return entry.tags.join(", ");
  }
</script>

<svelte:window on:keydown={(e) => e.key === "Escape" && onClose()} />

<div class="popover" role="dialog" aria-label={`Change history of ${reference}`} tabindex="-1" bind:this={box}
  style:left="{left}px" style:top="{top}px">
  <div class="head"><b>↺ {reference}</b><span class="count">{entries.length} change{entries.length === 1 ? "" : "s"}</span></div>
  {#if error}<p class="error" role="alert">{error}</p>{/if}
  {#if !loaded}
    <p class="meta">Loading…</p>
  {:else if !entries.length}
    <p class="meta">No recorded changes.</p>
  {:else}
    <ol>
      {#each entries as entry, index (entry.timestamp + index)}
        <li>
          <p class="meta">
            {new Date(entry.timestamp).toLocaleString()} · {entry.username || "unknown"}
            {#if kind(entry)}<span class="tag">{kind(entry)}</span>{/if}
          </p>
          <p class="diff">
            {#each graphemeDiff(entry.plainBefore, entry.plainAfter) as part}
              {#if part.kind === "removed"}<del>{part.text}</del>{:else if part.kind === "inserted"}<ins>{part.text}</ins>{:else}{part.text}{/if}
            {/each}
          </p>
          <button type="button" disabled={busy} on:click={() => restore(index)}>
            {confirming === index ? "Restore it: the verse becomes the text before this change" : "Restore the text before this change"}
          </button>
        </li>
      {/each}
    </ol>
  {/if}
  <div class="actions"><button type="button" on:click={onClose}>Close</button></div>
</div>

<style>
  .popover { position: fixed; z-index: 10000; width: 420px; max-width: calc(100vw - 16px); max-height: min(70vh, 520px);
    overflow-y: auto; box-sizing: border-box; padding: 10px 12px; border: 1px solid var(--border-strong);
    border-radius: 8px; background: var(--surface); color: var(--text); box-shadow: 0 10px 28px rgba(15, 23, 42, .22);
    font-size: var(--fs-xs); outline: none; }
  .head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
  .count, .meta { color: var(--text-2); }
  .meta { margin: 2px 0; }
  .tag { margin-left: 6px; padding: 0 6px; border-radius: 8px; background: var(--surface-2); font-size: var(--fs-3xs); }
  ol { list-style: none; margin: 0; padding: 0; }
  li { padding: 6px 0; border-top: 1px solid var(--border); }
  .diff { font-family: var(--font-target); font-size: var(--fs-md); line-height: 1.7; margin: 4px 0; }
  del { background: var(--danger-bg); color: var(--danger); }
  ins { background: var(--success-bg); color: var(--success); text-decoration: none; }
  .error { color: var(--danger); }
  button { font: inherit; font-size: var(--fs-2xs); padding: 3px 8px; border-radius: 6px;
    border: 1px solid var(--border-strong); background: var(--surface); color: var(--text); cursor: pointer; }
  .actions { display: flex; justify-content: flex-end; margin-top: 8px; }
</style>
