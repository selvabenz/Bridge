<script lang="ts">
  import { lastBatch, scopeNotice, undoLastBatch } from "../scopedApply";

  // What the last scoped correction did, with Undo all while its batch is the
  // last one. Stays until dismissed: undo should not vanish on a timer.
  let undoing = false;

  async function undo(): Promise<void> {
    if (undoing) return;
    undoing = true;
    try {
      await undoLastBatch();
    } finally {
      undoing = false;
    }
  }
</script>

{#if $scopeNotice}
  <p class="scope-notice" class:error={$scopeNotice.error} role="status">
    {$scopeNotice.text}
    {#if $lastBatch && !$scopeNotice.error}
      <button type="button" on:click={undo} disabled={undoing}>{undoing ? "Undoing…" : "Undo all"}</button>
    {/if}
    <button type="button" aria-label="Dismiss" on:click={() => scopeNotice.set(null)}>✕</button>
  </p>
{/if}

<style>
  .scope-notice {
    position: fixed; left: 50%; bottom: 110px; z-index: 9000; transform: translateX(-50%);
    margin: 0; padding: 7px 11px; border-radius: 6px; max-width: min(640px, calc(100vw - 32px));
    background: var(--surface); color: var(--text); border: 1px solid var(--border);
    font-size: var(--fs-sm); box-shadow: 0 4px 14px rgba(15, 23, 42, .18);
  }
  .scope-notice.error { background: var(--danger-bg); color: var(--danger); }
  button { margin-left: 8px; border: none; background: none; color: var(--accent); cursor: pointer;
    font: inherit; font-size: var(--fs-sm); text-decoration: underline; }
  button:disabled { opacity: .6; cursor: wait; }
</style>
