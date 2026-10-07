<script lang="ts">
  import { onMount } from "svelte";
  import { deleteFlag, setFlagStatus } from "../flags";
  import type { LanguageQaFlag } from "../types/languageQa";

  // What a ⚑ in the verse text opens: the flag, and Resolve / Reopen / Delete.
  export let flag: LanguageQaFlag;
  export let projectPath: string;
  export let x: number;
  export let y: number;
  export let onClose: () => void;

  let box: HTMLDivElement | null = null;
  let left = x;
  let top = y;
  let confirmingDelete = false;
  let error = "";

  onMount(() => {
    // Inside the window, below the ⚑ when there is room, else above it.
    const rect = box?.getBoundingClientRect();
    const width = rect?.width ?? 320, height = rect?.height ?? 160;
    left = Math.max(8, Math.min(x, window.innerWidth - width - 8));
    top = y + height + 8 > window.innerHeight ? Math.max(8, y - height - 24) : y;
    box?.focus();
    const outside = (event: PointerEvent) => {
      if (box && !box.contains(event.target as Node)) onClose();
    };
    window.addEventListener("pointerdown", outside, true);
    return () => window.removeEventListener("pointerdown", outside, true);
  });

  async function status(next: "open" | "resolved"): Promise<void> {
    error = await setFlagStatus(projectPath, flag, next);
    if (!error) onClose();
  }

  async function remove(): Promise<void> {
    if (!confirmingDelete) {
      confirmingDelete = true;
      return;
    }
    error = await deleteFlag(projectPath, flag);
    if (!error) onClose();
  }
</script>

<svelte:window on:keydown={(e) => e.key === "Escape" && onClose()} />

<div class="popover" role="dialog" aria-label="Flag" tabindex="-1" bind:this={box} style:left="{left}px" style:top="{top}px">
  <div class="head"><b>⚑ {flag.type}</b><span class="status">{flag.status}</span></div>
  <div class="quote">{flag.text}</div>
  {#if flag.verseEnd}<p class="meta">Through verse {flag.verseEnd}</p>{/if}
  {#if flag.note}<p class="note">{flag.note}</p>{/if}
  {#if flag.suggested}<p class="meta">Suggested: <b class="target">{flag.suggested}</b></p>{/if}
  <p class="meta">{flag.reviewer} · {new Date(flag.createdAt).toLocaleString()}</p>
  {#if error}<p class="error" role="alert">{error}</p>{/if}
  <div class="actions">
    {#if flag.status === "open"}
      <button type="button" on:click={() => status("resolved")}>Resolve</button>
    {:else}
      <button type="button" on:click={() => status("open")}>Reopen</button>
    {/if}
    <button type="button" class="danger" on:click={remove}>{confirmingDelete ? "Really delete" : "Delete"}</button>
    <button type="button" on:click={onClose}>Close</button>
  </div>
</div>

<style>
  .popover { position: fixed; z-index: 10000; width: 320px; max-width: calc(100vw - 16px); box-sizing: border-box;
    padding: 10px 12px; border: 1px solid var(--border-strong); border-radius: 8px; background: var(--surface);
    color: var(--text); box-shadow: 0 10px 28px rgba(15, 23, 42, .22); font-size: var(--fs-xs); outline: none; }
  .head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; color: var(--flag); }
  .status { font-size: var(--fs-3xs); text-transform: uppercase; color: var(--text-3); }
  .quote, .target { font-family: var(--font-target); font-size: var(--fs-md); }
  .quote { background: var(--flag-bg); border-radius: 4px; padding: 2px 6px; }
  .note { margin: 6px 0; white-space: pre-wrap; }
  .meta { margin: 4px 0; color: var(--text-2); }
  .error { color: var(--danger); }
  .actions { display: flex; gap: 6px; margin-top: 8px; }
  .actions button { font: inherit; font-size: var(--fs-xs); padding: 4px 10px; border-radius: 6px;
    border: 1px solid var(--border-strong); background: var(--surface); color: var(--text); cursor: pointer; }
  .actions button.danger { color: var(--danger); }
</style>
