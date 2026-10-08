<script lang="ts">
  import { onMount } from "svelte";
  import { bridge } from "../api/bridgeClient";
  import type { OccurrencesResult } from "../types/languageQa";

  // Where a word occurs: in this book's verse text (IRV) or in the reference
  // text (OV). An IRV hit opens its verse; an OV hit is for reading only.
  export let projectPath: string;
  export let word: string;
  export let source: "irv" | "ov" = "irv";
  /** "text": "Search in this book", any run of the text, not only the word. */
  export let match: "word" | "text" = "word";
  export let onNavigate: (book: string, chapter: string, verse: string) => void;
  export let onClose: () => void;

  let result: OccurrencesResult | null = null;
  let error = "";

  onMount(async () => {
    try {
      result = match === "text"
        ? await bridge.languageQaOccurrences(projectPath, word, source, 200, "text")
        : await bridge.languageQaOccurrences(projectPath, word, source, 200);
    } catch (cause) {
      error = cause instanceof Error ? cause.message : String(cause);
    }
  });
</script>

<svelte:window on:keydown={(e) => e.key === "Escape" && onClose()} />

<div class="overlay" role="presentation" on:click|self={onClose}>
  <div class="popup" role="dialog" aria-modal="true" aria-labelledby="occ-title">
    <div class="head">
      <h2 id="occ-title"><span class="target">{word}</span>{match === "text" ? " anywhere" : ""} in {source === "irv" ? "this book" : "the reference text"}</h2>
      <button class="close" aria-label="Close" on:click={onClose}>✕</button>
    </div>
    {#if error}
      <p class="error" role="alert">{error}</p>
    {:else if !result}
      <p class="muted">Searching…</p>
    {:else if !result.hits.length}
      <p class="muted">Not found.</p>
    {:else}
      <p class="muted">{result.total} place{result.total === 1 ? "" : "s"}{result.truncated ? `, the first ${result.hits.length} shown` : ""}.</p>
      <ol>
        {#each result.hits as hit (`${hit.chapter}:${hit.verse}:${hit.start}`)}
          <li>
            {#if source === "irv"}
              <button class="ref" on:click={() => { onNavigate(hit.book, hit.chapter, hit.verse); onClose(); }}>
                {hit.book.toUpperCase()} {hit.chapter}:{hit.verse}
              </button>
            {:else}
              <span class="ref">{hit.book.toUpperCase()} {hit.chapter}:{hit.verse}</span>
            {/if}
            <span class="snippet">{hit.snippet.slice(0, hit.snippetStart)}<b>{hit.snippet.slice(hit.snippetStart, hit.snippetEnd)}</b>{hit.snippet.slice(hit.snippetEnd)}</span>
          </li>
        {/each}
      </ol>
    {/if}
  </div>
</div>

<style>
  .overlay { position: fixed; inset: 0; z-index: 60; display: grid; place-items: center; background: rgba(15, 20, 26, 0.35); }
  .popup { width: min(560px, calc(100vw - 32px)); max-height: calc(100vh - 48px); overflow: auto; box-sizing: border-box;
    background: var(--surface); color: var(--text); border-radius: 12px; padding: 16px 20px; box-shadow: 0 20px 50px rgba(0,0,0,.25); }
  .head { display: flex; align-items: center; gap: 8px; }
  h2 { flex: 1; font-size: var(--fs-md); margin: 0; }
  .target { font-family: var(--font-target); }
  .close { border: 0; background: transparent; font-size: var(--fs-lg); color: var(--text-2); cursor: pointer; }
  .muted { color: var(--text-3); font-size: var(--fs-xs); }
  .error { color: var(--danger); font-size: var(--fs-xs); }
  ol { list-style: none; margin: 8px 0 0; padding: 0; }
  li { display: grid; grid-template-columns: 96px minmax(0, 1fr); gap: 8px; align-items: baseline;
    padding: 5px 0; border-bottom: 1px solid var(--border); font-size: var(--fs-xs); }
  .ref { font: inherit; font-weight: 700; color: var(--accent); background: none; border: 0; padding: 0; cursor: pointer; text-align: left; }
  span.ref { color: var(--text-2); cursor: default; }
  .snippet { font-family: var(--font-target); font-size: var(--fs-md); }
  .snippet b { background: var(--accent-bg); }
</style>
