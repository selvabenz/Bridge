<script lang="ts">
  import { onDestroy, tick } from "svelte";
  import { bridge } from "../api/bridgeClient";
  import { currentChapter, project, selectedVerse } from "../stores";
  import type { ReferenceChapter } from "../types/languageQa";

  // The reference Bible (an Old Version) beside the text, on the same chapter
  // (indic-qa's reference panel). Reading only: nothing here changes the text.
  export let onOpenSettings: () => void = () => {};
  export let onSelectVerse: (verse: string) => void = () => {};

  let reference: ReferenceChapter | null = null;
  let error = "";
  let follow = true;
  let list: HTMLOListElement | null = null;
  let ticket = 0;
  let timer: ReturnType<typeof setTimeout> | null = null;

  $: path = $project?.path ?? "";
  $: void load(path, $currentChapter);
  $: if (follow && $selectedVerse && reference?.ready) void scrollTo($selectedVerse);

  async function load(projectPath: string, chapter: string, attempt = 0): Promise<void> {
    if (timer) clearTimeout(timer);
    const mine = ++ticket;
    if (!projectPath || !chapter) return;
    try {
      const result = await bridge.languageQaReference(projectPath, chapter);
      if (mine !== ticket) return;
      reference = result;
      error = "";
      // The folder is read on a background thread the first time: ask again.
      if (!result.ready && result.configured && !result.error && attempt < 120) {
        timer = setTimeout(() => void load(projectPath, chapter, attempt + 1), 1000);
      }
    } catch (cause) {
      if (mine === ticket) error = cause instanceof Error ? cause.message : String(cause);
    }
  }

  async function scrollTo(verse: string): Promise<void> {
    await tick();
    const row = list?.querySelector<HTMLElement>(`[data-ref-verse="${CSS.escape(verse)}"]`);
    if (row && typeof row.scrollIntoView === "function") row.scrollIntoView({ block: "nearest" });
  }

  onDestroy(() => {
    ticket += 1;
    if (timer) clearTimeout(timer);
  });
</script>

<aside class="reference-panel" aria-label="Reference Bible">
  <div class="head">
    <span class="label">{reference?.source?.label ?? "Reference"}</span>
    <label class="follow"><input type="checkbox" bind:checked={follow} /> follow</label>
  </div>
  {#if error}
    <p class="note error" role="alert">{error}</p>
  {:else if !reference}
    <p class="note">Loading…</p>
  {:else if !reference.configured}
    <p class="note">No reference Bible is set for this book's language.</p>
    <button class="settings" on:click={onOpenSettings}>Choose one in Settings › Language QA</button>
  {:else if reference.error}
    <p class="note error" role="alert">The reference could not be read: {reference.error}</p>
  {:else if !reference.ready}
    <p class="note">Reading the reference Bible… (once; it is kept)</p>
  {:else if !reference.verses.length}
    <p class="note">This chapter is not in the reference Bible.</p>
  {:else}
    <ol bind:this={list}>
      {#each reference.verses as row (row.verse)}
        <li data-ref-verse={row.verse} class:current={row.verse === $selectedVerse}>
          <button type="button" on:click={() => onSelectVerse(row.verse)}
            aria-label={`Verse ${row.verse} in the reference: show it in the text`}>
            <span class="num">{row.verse}</span><span class="text">{row.text}</span>
          </button>
        </li>
      {/each}
    </ol>
  {/if}
</aside>

<style>
  .reference-panel { width: 300px; flex-shrink: 0; display: flex; flex-direction: column; min-height: 0;
    border-left: 1px solid var(--border); background: var(--surface-2); }
  .head { display: flex; align-items: center; gap: 8px; padding: 8px 10px; border-bottom: 1px solid var(--border);
    font-size: var(--fs-2xs); color: var(--text-2); }
  .label { flex: 1; font-weight: 700; color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .follow { display: flex; align-items: center; gap: 4px; white-space: nowrap; }
  .note { margin: 10px; font-size: var(--fs-xs); color: var(--text-3); }
  .error { color: var(--danger); }
  .settings { margin: 0 10px; font: inherit; font-size: var(--fs-xs); color: var(--accent); background: none;
    border: 0; padding: 0; text-decoration: underline; cursor: pointer; text-align: left; }
  ol { list-style: none; margin: 0; padding: 6px; overflow-y: auto; flex: 1; }
  li button { display: flex; gap: 8px; width: 100%; text-align: left; padding: 6px 8px; border: 0; border-radius: 6px;
    background: transparent; color: var(--text); cursor: pointer; font: inherit; }
  li button:hover { background: var(--surface); }
  li.current button { background: var(--accent-bg); box-shadow: inset 3px 0 0 var(--accent); }
  .num { font-size: var(--fs-2xs); font-weight: 700; color: var(--text-3); min-width: 20px; padding-top: 2px; }
  .text { font-family: var(--font-target); font-size: calc(var(--fs-md) * var(--verse-scale, 1)); line-height: 1.7; }
  /* At a narrow window the text column needs the room more. */
  @media (max-width: 1199px) { .reference-panel { display: none; } }
</style>
