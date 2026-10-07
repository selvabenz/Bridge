<script lang="ts">
  import { onMount, tick } from "svelte";
  import { FLAG_TYPES, type FlagInput, type FlagType } from "../types/languageQa";

  /** What is being flagged: raw code points into the verse as it is now. */
  export let draft: { chapter: string; verse: string; start: number; end: number; text: string;
    suggested?: string; findingId?: string; type?: FlagType };
  /** The chapter's verses, for "through verse". */
  export let verseNums: string[] = [];
  export let reference = "";
  export let onSave: (input: FlagInput) => Promise<string>;
  export let onCancel: () => void;

  const LABELS: Record<FlagType, string> = {
    spelling: "Spelling", grammar: "Grammar", meaning: "Meaning", style: "Style / wording",
    encoding: "Encoding", font: "Font / rendering", other: "Other",
  };

  let type: FlagType | null = draft.type ?? null;
  let note = "";
  let suggested = draft.suggested ?? "";
  let verseEnd = "";
  let saving = false;
  let error = "";
  let noteField: HTMLTextAreaElement | null = null;
  let previouslyFocused: Element | null = null;

  $: later = verseNums.slice(verseNums.indexOf(draft.verse) + 1);

  onMount(() => {
    previouslyFocused = document.activeElement;
    void tick().then(() => noteField?.focus());
    return () => {
      if (previouslyFocused instanceof HTMLElement) previouslyFocused.focus();
    };
  });

  async function save(): Promise<void> {
    if (!type || saving) return;
    saving = true;
    error = "";
    const input: FlagInput = {
      chapter: draft.chapter, verse: draft.verse, start: draft.start, end: draft.end, text: draft.text, type, note,
      ...(suggested.trim() ? { suggested: suggested.trim() } : {}),
      ...(verseEnd ? { verseEnd } : {}),
      ...(draft.findingId ? { findingId: draft.findingId } : {}),
    };
    error = await onSave(input);
    saving = false;
  }
</script>

<svelte:window on:keydown={(e) => e.key === "Escape" && !saving && onCancel()} />

<div class="overlay" role="presentation">
  <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="flag-title">
    <h2 id="flag-title">Flag for review</h2>
    <p class="desc">A question for the team about this text. Nothing in the verse changes.</p>

    <div class="label">Selected text · {reference}</div>
    <div class="quote">{draft.text}</div>
    {#if later.length}
      <label class="inline">Through verse
        <select bind:value={verseEnd}>
          <option value="">{draft.verse} only</option>
          {#each later as v}<option value={v}>{v}</option>{/each}
        </select>
      </label>
    {/if}

    <div class="label" id="flag-type">Type</div>
    <div class="types" role="radiogroup" aria-labelledby="flag-type">
      {#each FLAG_TYPES as option}
        <button type="button" role="radio" aria-checked={type === option} class:on={type === option}
          on:click={() => (type = option)}>{LABELS[option]}</button>
      {/each}
    </div>

    <label class="label" for="flag-note">Note</label>
    <textarea id="flag-note" rows="3" bind:this={noteField} bind:value={note} maxlength="4000"></textarea>

    <label class="label" for="flag-suggested">Suggested form <span class="optional">(optional)</span></label>
    <input id="flag-suggested" type="text" bind:value={suggested} placeholder="Leave blank if you are only asking" />
    <p class="hint">A one-word suggestion is also remembered as a learned fix, offered in every book of the project.</p>

    {#if error}<p class="error" role="alert">{error}</p>{/if}
    <div class="actions">
      <span class="who">{type ? "" : "Choose a type to save."}</span>
      <button type="button" on:click={onCancel} disabled={saving}>Cancel</button>
      <button type="button" class="primary" on:click={save} disabled={!type || saving}>{saving ? "Saving…" : "Save flag"}</button>
    </div>
  </div>
</div>

<style>
  .overlay { position: fixed; inset: 0; z-index: 60; display: grid; place-items: center; background: rgba(15, 20, 26, 0.45); }
  .dialog { width: min(560px, calc(100vw - 32px)); max-height: calc(100vh - 48px); overflow: auto; box-sizing: border-box;
    background: var(--surface); color: var(--text); border-radius: 14px; padding: 20px 24px; }
  h2 { font-size: var(--fs-lg); margin: 0 0 4px; }
  .desc, .hint { font-size: var(--fs-xs); color: var(--text-2); margin: 0 0 14px; }
  .hint { color: var(--text-3); margin-top: 4px; }
  .label { display: block; font-size: var(--fs-xs); font-weight: 700; color: var(--text-2); margin: 0 0 5px; }
  .optional { font-weight: 400; color: var(--text-3); }
  .quote { border: 1px solid var(--border); border-radius: 8px; padding: 8px 12px; background: var(--surface-2);
    font-family: var(--font-target); font-size: var(--fs-lg); line-height: 1.7; margin-bottom: 8px; }
  .inline { display: flex; gap: 8px; align-items: center; font-size: var(--fs-xs); color: var(--text-2); margin-bottom: 14px; }
  .types { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 14px; }
  .types button { font: inherit; font-size: var(--fs-xs); padding: 6px 10px; border-radius: 8px; border: 1px solid var(--border);
    background: var(--surface-2); color: var(--text); cursor: pointer; }
  .types button.on { border-color: var(--accent); background: var(--accent-bg); color: var(--accent); font-weight: 600; }
  textarea, input[type="text"], select { width: 100%; box-sizing: border-box; border: 1px solid var(--border); border-radius: 6px;
    padding: 6px 10px; font: inherit; font-size: var(--fs-sm); background: var(--surface-2); color: var(--text); margin-bottom: 4px; }
  select { width: auto; }
  .error { color: var(--danger); font-size: var(--fs-xs); }
  .actions { display: flex; align-items: center; gap: 10px; margin-top: 14px; }
  .who { flex: 1; font-size: var(--fs-2xs); color: var(--text-3); }
  .actions button { font: inherit; font-size: var(--fs-sm); font-weight: 600; padding: 8px 14px; border-radius: 6px;
    border: 1px solid var(--border-strong); background: var(--surface); color: var(--text); cursor: pointer; }
  .actions button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
  .actions button:disabled { opacity: .6; cursor: not-allowed; }
</style>
