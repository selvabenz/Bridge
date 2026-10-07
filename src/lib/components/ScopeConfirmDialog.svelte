<script lang="ts">
  import { onMount, tick } from "svelte";
  import { verseKey, verseTexts } from "../stores";
  import { cancelScopeDialog, confirmScopeDialog, scopeDialog } from "../scopedApply";
  import type { ScopeOccurrence } from "../types/languageQa";

  // The confirmation before a scoped Language QA correction: what will change
  // and where, then one button. Nothing is written until it is pressed.
  const LIST_CAP = 200;
  let cancelButton: HTMLButtonElement | null = null;
  let previouslyFocused: Element | null = null;

  $: state = $scopeDialog;
  $: writable = state?.found?.occurrences.filter((o) => o.new !== null) ?? [];
  $: verses = new Set(writable.map((o) => `${o.chapter}:${o.verse}`)).size;
  $: scopeWord = state?.scope === "book" ? "this book" : "this chapter";

  onMount(() => {
    previouslyFocused = document.activeElement;
    void tick().then(() => cancelButton?.focus());  // the safe default
    return () => {
      if (previouslyFocused instanceof HTMLElement) previouslyFocused.focus();
    };
  });

  /** Before and after, from the loaded verse text when there is one. */
  function snippet(o: ScopeOccurrence): { before: string; after: string } {
    const text = $verseTexts[verseKey(o.chapter, o.verse)];
    if (text === undefined) return { before: "", after: "" };
    const chars = Array.from(text);
    return { before: chars.slice(Math.max(0, o.start - 24), o.start).join(""), after: chars.slice(o.end, o.end + 24).join("") };
  }

  function onKeydown(event: KeyboardEvent): void {
    if (event.key === "Escape" && !state?.busy) cancelScopeDialog();
  }
</script>

<svelte:window on:keydown={onKeydown} />

{#if state}
  <div class="overlay" role="presentation">
    <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="scope-title" aria-describedby="scope-summary">
      <h2 id="scope-title">Change “{state.finding.originalText}” to “{state.suggestion}” in {scopeWord}?</h2>
      {#if !state.found && !state.error}
        <p id="scope-summary" class="muted">Finding every place…</p>
      {:else if state.found}
        <p id="scope-summary">
          <b>{writable.length}</b> place{writable.length === 1 ? "" : "s"} in <b>{verses}</b> verse{verses === 1 ? "" : "s"}.
          Each verse is saved as its own edit, and one Undo reverts them all.
        </p>
        <ol class="places">
          {#each writable.slice(0, LIST_CAP) as o (o.findingId)}
            {@const around = snippet(o)}
            <li>
              <span class="ref">{state.finding.book.toUpperCase()} {o.chapter}:{o.verse}</span>
              <span class="text">…{around.before}<del>{o.old}</del><ins>{o.new}</ins>{around.after}…</span>
            </li>
          {/each}
        </ol>
        {#if writable.length > LIST_CAP}<p class="muted">and {writable.length - LIST_CAP} more.</p>{/if}
      {/if}
      {#if state.error}<p class="error" role="alert">{state.error}</p>{/if}
      <div class="actions">
        <button bind:this={cancelButton} on:click={cancelScopeDialog} disabled={state.busy && Boolean(state.found)}>Cancel</button>
        <button class="primary" on:click={confirmScopeDialog}
          disabled={state.busy || !state.found || writable.length === 0}>
          {state.busy && state.found ? "Changing…" : `Change ${verses} verse${verses === 1 ? "" : "s"}`}
        </button>
      </div>
    </div>
  </div>
{/if}

<style>
  .overlay { position: fixed; inset: 0; z-index: 60; display: grid; place-items: center; background: rgba(15, 20, 26, 0.45); }
  .dialog { width: min(600px, calc(100vw - 32px)); max-height: calc(100vh - 48px); display: flex; flex-direction: column;
    background: var(--surface); color: var(--text); border-radius: 14px; padding: 20px 24px; box-sizing: border-box;
    box-shadow: 0 20px 50px rgba(0, 0, 0, .25); }
  h2 { font-size: var(--fs-lg); margin: 0 0 6px; }
  p { font-size: var(--fs-xs); color: var(--text-2); margin: 0 0 10px; }
  .muted { color: var(--text-3); }
  .error { color: var(--danger); }
  .places { margin: 0 0 10px; padding: 0; list-style: none; overflow: auto; border: 1px solid var(--border); border-radius: 8px; }
  .places li { display: grid; grid-template-columns: 96px minmax(0, 1fr); gap: 8px; align-items: center;
    padding: 6px 10px; border-bottom: 1px solid var(--border); font-size: var(--fs-xs); }
  .places li:last-child { border-bottom: 0; }
  .ref { font-weight: 700; color: var(--text-2); }
  .text { font-family: var(--font-target); font-size: var(--fs-md); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  del { color: var(--danger); background: var(--danger-bg); }
  ins { color: var(--success); background: var(--success-bg); text-decoration: none; font-weight: 700; }
  .actions { display: flex; justify-content: flex-end; gap: 10px; }
  button { font: inherit; font-size: var(--fs-sm); font-weight: 600; padding: 8px 14px; border-radius: 6px;
    border: 1px solid var(--border-strong); background: var(--surface); color: var(--text); cursor: pointer; }
  button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
  button:disabled { opacity: .6; cursor: not-allowed; }
</style>
