<script lang="ts">
  import { onDestroy } from "svelte";
  import { bridge } from "../api/bridgeClient";
  import type { RelatedWordsResult } from "../types/languageQa";

  // indic-qa's related words for one word: what the OV used in the same slot
  // (verse-aligned, at least twice) and other forms sharing its stem. Corpus
  // evidence, not a thesaurus: every candidate shows its counts.
  export let projectPath: string;
  export let word: string;
  export let onShowOccurrences: (word: string, source: "irv" | "ov") => void;

  let result: RelatedWordsResult | null = null;
  let error = "";
  let ticket = 0;
  let timer: ReturnType<typeof setTimeout> | null = null;

  $: void load(projectPath, word);

  async function load(path: string, w: string, attempt = 0): Promise<void> {
    if (timer) clearTimeout(timer);
    const mine = ++ticket;
    if (!path || !w) return;
    try {
      const next = await bridge.languageQaRelated(path, w);
      if (mine !== ticket) return;
      result = next;
      error = "";
      // Built on a background thread the first time: ask again shortly.
      if (!next.ready && !next.off && next.configured !== false && !next.error && attempt < 120) {
        timer = setTimeout(() => void load(path, w, attempt + 1), 1000);
      }
    } catch (cause) {
      if (mine === ticket) error = cause instanceof Error ? cause.message : String(cause);
    }
  }

  onDestroy(() => {
    ticket += 1;
    if (timer) clearTimeout(timer);
  });
</script>

<div class="section related">
  <div class="section-title">Related words <span class="for">for <b class="target">{word}</b></span></div>
  {#if error}
    <p class="none" role="alert">{error}</p>
  {:else if !result}
    <p class="none">Loading…</p>
  {:else if result.off}
    <p class="none">Turned off in Settings › Language QA.</p>
  {:else if result.configured === false}
    <p class="none">No reference Bible is set for this book's language (Settings › Language QA).</p>
  {:else if result.error}
    <p class="none" role="alert">{result.error}</p>
  {:else if !result.ready}
    <p class="none">Comparing the reference with the text… (once; it is kept)</p>
  {:else}
    <p class="counts">This text uses it {result.irv ?? 0}×; the reference {result.ov ?? 0}×.</p>
    {#if result.equivalents.length}
      <div class="sub">In the same place in the other text</div>
      <ul>
        {#each result.equivalents as e (e.w)}
          <li><b class="target">{e.w}</b> <span class="meta">{e.n}× · {e.source} · e.g. {e.ref}</span></li>
        {/each}
      </ul>
    {/if}
    {#if result.family.length}
      <div class="sub">Same stem</div>
      <ul class="inline">
        {#each result.family as f (f.w)}
          <li><span class="target">{f.w}</span> <span class="meta">{f.irv}/{f.ov}</span></li>
        {/each}
      </ul>
    {/if}
    {#if !result.equivalents.length && !result.family.length}<p class="none">Nothing related found.</p>{/if}
  {/if}
  <div class="links">
    <button type="button" on:click={() => onShowOccurrences(word, "irv")}>Show IRV occurrences</button>
    <button type="button" on:click={() => onShowOccurrences(word, "ov")}>Show OV occurrences</button>
  </div>
</div>

<style>
  .section { border: 1px solid var(--border); border-radius: 12px; padding: 12px 14px; margin-bottom: 12px; }
  .section-title { font-size: var(--fs-xs); font-weight: 700; color: var(--text); margin-bottom: 6px; }
  .for { font-weight: 400; color: var(--text-3); }
  .target { font-family: var(--font-target); font-size: var(--fs-md); }
  .none, .counts { font-size: var(--fs-xs); color: var(--text-3); margin: 0 0 6px; }
  .counts { color: var(--text-2); }
  .sub { font-size: var(--fs-2xs); font-weight: 700; color: var(--text-2); margin: 6px 0 2px; }
  ul { list-style: none; margin: 0; padding: 0; font-size: var(--fs-xs); }
  ul.inline { display: flex; flex-wrap: wrap; gap: 4px 12px; }
  .meta { color: var(--text-3); font-size: var(--fs-2xs); }
  .links { display: flex; gap: 14px; margin-top: 8px; }
  .links button { font: inherit; font-size: var(--fs-2xs); font-weight: 600; color: var(--accent); background: none;
    border: 0; padding: 0; text-decoration: underline; cursor: pointer; }
</style>
