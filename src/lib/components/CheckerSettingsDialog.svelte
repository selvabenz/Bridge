<script lang="ts">
  import { onMount, tick } from "svelte";
  import { bridge } from "../api/bridgeClient";
  import { checkerSettingsOpen } from "../editorPrefs";
  import { nudgeLanguageQa } from "../languageQaInline";
  import type { CheckerSettings, CheckerSettingsPatch } from "../types/languageQa";
  import Fieldset from "./Fieldset.svelte";

  // The indic-qa checker's own settings, as the indic-qa web app's Settings
  // dialog offers them, at its size and with its folding sections. Saved per
  // collection (languageQa.checkerSettings.set); nothing here changes text.
  export let projectPath: string;
  export let onClose: (saved: boolean) => void;

  let settings: CheckerSettings | null = null;
  let unavailable = "";
  let loadError = "";
  let status = "";
  let saving = false;
  let projectWordCount: number | null = null;

  // The draft: a copy of what was loaded, edited in place, compared on Save.
  let checked: string[] = [];
  let warnings: Record<string, boolean> = {};
  let suggestMax = 9;
  let compound = false;
  let rules: Record<string, boolean> = {};
  let style: Record<string, string> = {};
  let numbers: Record<string, number> = {};
  let sandhi: Record<string, number | boolean> = {};

  let dialog: HTMLDivElement | null = null;
  let closeButton: HTMLButtonElement | null = null;
  let previouslyFocused: Element | null = null;

  const numberKey = (group: string, key: string): string => `${group}.${key}`;

  function adopt(s: CheckerSettings): void {
    settings = s;
    checked = [...s.contexts.checked];
    warnings = { ...s.warnings.values };
    suggestMax = s.suggest.max;
    compound = s.compound.enabled;
    rules = Object.fromEntries(s.rules.map((r) => [r.id, r.enabled]));
    style = Object.fromEntries((s.style ?? []).map((t) => [t.id, t.value]));
    numbers = Object.fromEntries((s.numbers ?? []).map((n) => [numberKey(n.group, n.key), n.value]));
    sandhi = { ...(s.sandhi?.values ?? {}) };
  }

  async function load(): Promise<void> {
    try {
      const got = await bridge.languageQaCheckerSettingsGet(projectPath);
      if ("available" in got && got.available === false) unavailable = got.reason;
      else adopt(got as CheckerSettings);
    } catch (e) {
      loadError = e instanceof Error ? e.message : String(e);
    }
    bridge.languageQaWordsList(projectPath)
      .then((words) => (projectWordCount = words.added.length))
      .catch(() => (projectWordCount = null));
  }

  /** Only what differs from what was loaded, section by section. */
  function patchFrom(s: CheckerSettings): CheckerSettingsPatch {
    const checker: Record<string, unknown> = {};
    const changed = <T>(now: Record<string, T>, was: Record<string, T>): Record<string, T> =>
      Object.fromEntries(Object.entries(now).filter(([k, v]) => was[k] !== v));
    const contexts = s.contexts.checkable.filter((c) => checked.includes(c));
    if (contexts.join("|") !== s.contexts.checked.join("|")) checker.checked_contexts = contexts;
    const w = changed(warnings, s.warnings.values);
    if (Object.keys(w).length) checker.warnings = w;
    if (suggestMax !== s.suggest.max) checker.suggest = { max: suggestMax };
    if (compound !== s.compound.enabled) checker.compound = { enabled: compound };
    const st = changed(style, Object.fromEntries((s.style ?? []).map((t) => [t.id, t.value])));
    if (Object.keys(st).length) checker.style = st;
    for (const n of s.numbers ?? []) {
      const value = numbers[numberKey(n.group, n.key)];
      if (value !== n.value) checker[n.group] = { ...((checker[n.group] as Record<string, number>) ?? {}), [n.key]: value };
    }
    const sd = changed(sandhi, s.sandhi?.values ?? {});
    if (s.sandhi && Object.keys(sd).length) checker.sandhi = sd;
    const r = Object.fromEntries(s.rules.filter((rule) => rules[rule.id] !== rule.enabled)
      .map((rule) => [rule.id, { enabled: rules[rule.id] }]));
    const patch: CheckerSettingsPatch = {};
    if (Object.keys(checker).length) patch.checker = checker;
    if (Object.keys(r).length) patch.rules = r;
    return patch;
  }

  async function save(): Promise<void> {
    if (!settings || saving) return;
    const patch = patchFrom(settings);
    if (!patch.checker && !patch.rules) {
      onClose(false);
      return;
    }
    saving = true;
    status = "Saving…";
    try {
      await bridge.languageQaCheckerSettingsSet(projectPath, patch);
      nudgeLanguageQa();
      onClose(true);
    } catch (e) {
      status = e instanceof Error ? e.message : String(e);
    } finally {
      saving = false;
    }
  }

  function isOpen(id: string, open: string[]): boolean {
    return open.includes(id);
  }

  function remember(id: string, open: boolean): void {
    checkerSettingsOpen.update((ids) => (open ? [...ids.filter((i) => i !== id), id] : ids.filter((i) => i !== id)));
  }

  function toggleContext(context: string, on: boolean): void {
    checked = on ? [...checked.filter((c) => c !== context), context] : checked.filter((c) => c !== context);
  }

  function focusables(): HTMLElement[] {
    return dialog ? Array.from(dialog.querySelectorAll<HTMLElement>(
      "button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex='-1'])")) : [];
  }

  function onKeydown(event: KeyboardEvent): void {
    if (event.key === "Escape" && !saving) {
      event.stopPropagation();
      onClose(false);
      return;
    }
    if (event.key !== "Tab") return;
    // Tab stays inside the dialog, as in any modal.
    const items = focusables();
    if (!items.length) return;
    const first = items[0], last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  onMount(() => {
    previouslyFocused = document.activeElement;
    void load();
    void tick().then(() => closeButton?.focus());
    return () => {
      if (previouslyFocused instanceof HTMLElement) previouslyFocused.focus();
    };
  });

  $: open = $checkerSettingsOpen;
  $: hasRulePack = Boolean(settings && (settings.rules.length || settings.style?.length || settings.numbers?.length));
</script>

<!-- svelte-ignore a11y-no-noninteractive-element-interactions -->
<div class="overlay" role="presentation">
  <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="checker-title" bind:this={dialog}
    on:keydown={onKeydown}>
    <div class="dlg-head">
      <h2 id="checker-title">Checker settings{settings ? ` — ${settings.language}` : ""}</h2>
      <button class="x" type="button" aria-label="Close" bind:this={closeButton} on:click={() => onClose(false)}>✕</button>
    </div>
    <div class="dlg-body">
      {#if unavailable}
        <p class="muted">{unavailable}</p>
      {:else if loadError}
        <p class="error" role="alert">{loadError}</p>
      {:else if !settings}
        <p class="muted">Loading…</p>
      {:else}
        <p class="hint">Saved with the project, for every book of the collection ({settings.books.length}
          book{settings.books.length === 1 ? "" : "s"}). A pass runs with them after Save.</p>
        {#if !settings.ready}
          <p class="notice" role="status">Still being prepared: the spelling-style choices appear once Language QA has
            checked this book. Open a chapter, wait for the pass, then reopen this dialog.</p>
        {/if}

        <Fieldset id="cs-contexts" legend="Check these text contexts" open={isOpen("contexts", open)}
          onToggle={(o) => remember("contexts", o)}>
          {#each settings.contexts.checkable as c (c)}
            <label class="row"><input type="checkbox" checked={checked.includes(c)}
              on:change={(e) => toggleContext(c, e.currentTarget.checked)} /> {settings.contexts.labels[c] ?? c}</label>
          {/each}
          {#if settings.contexts.checkable.length === 1}
            <p class="hint">This language's checker reads verse text only in Bridge.</p>
          {/if}
        </Fieldset>

        {#if settings.sandhi}
          <Fieldset id="cs-sandhi" legend="Sandhi leads" open={isOpen("sandhi", open)}
            onToggle={(o) => remember("sandhi", o)}>
            {#each Object.keys(settings.sandhi.labels) as key (key)}
              {#if typeof sandhi[key] === "boolean"}
                <label class="row"><input type="checkbox" bind:checked={sandhi[key]} /> {settings.sandhi.labels[key]}</label>
              {:else}
                <label class="row num">
                  <span>{settings.sandhi.labels[key]}</span>
                  <input type="number" min="0" step={key === "min_total" ? 1 : 0.05} bind:value={sandhi[key]} />
                </label>
              {/if}
            {/each}
          </Fieldset>
        {/if}

        {#if hasRulePack}
          <Fieldset id="cs-rules" legend={settings.legend} open={isOpen("rulePack", open)}
            onToggle={(o) => remember("rulePack", o)}>
            {#each settings.rules as r (r.id)}
              <label class="row" title={r.id}>
                <input type="checkbox" bind:checked={rules[r.id]} />
                <span class="grow">{r.label}</span>
                <span class="count" aria-label="{r.count} in this book">{r.count}</span>
              </label>
            {/each}
            {#if settings.style?.length}
              <h3>Spelling style</h3>
              {#each settings.style as t (t.id)}
                <label class="row num">
                  <span>{t.label}</span>
                  <select bind:value={style[t.id]}>
                    {#each t.options as o (o.value)}<option value={o.value}>{o.label}</option>{/each}
                  </select>
                </label>
              {/each}
            {/if}
            {#each settings.numbers ?? [] as n (numberKey(n.group, n.key))}
              <label class="row num">
                <span>{n.label}</span>
                <input type="number" min={n.min} step={n.group === "lex" ? 1 : 0.05}
                  bind:value={numbers[numberKey(n.group, n.key)]} />
              </label>
            {/each}
          </Fieldset>
        {/if}

        {#if Object.keys(settings.warnings.values).length}
          <Fieldset id="cs-warnings" legend="Warnings" open={isOpen("warnings", open)}
            onToggle={(o) => remember("warnings", o)}>
            {#each Object.keys(settings.warnings.values) as key (key)}
              <label class="row"><input type="checkbox" bind:checked={warnings[key]} /> {settings.warnings.labels[key] ?? key}</label>
            {/each}
          </Fieldset>
        {/if}

        <Fieldset id="cs-suggest" legend="Suggestions and compounds" open={isOpen("suggest", open)}
          onToggle={(o) => remember("suggest", o)}>
          <label class="row num">
            <span>Suggestions offered for a word</span>
            <input type="number" min="1" max={settings.suggest.limit} step="1" bind:value={suggestMax} />
          </label>
          <label class="row"><input type="checkbox" bind:checked={compound} /> Accept compounds of known words</label>
        </Fieldset>

        <Fieldset id="cs-words" legend="Project words" open={isOpen("words", open)}
          onToggle={(o) => remember("words", o)}>
          <p class="row">{projectWordCount === null ? "–" : projectWordCount} word{projectWordCount === 1 ? "" : "s"}
            added to this project's word list.</p>
          <p class="hint">Add a word from its right-click menu; review or remove them in Language QA › Dictionary.</p>
        </Fieldset>
      {/if}
    </div>
    <div class="dlg-foot">
      <span class="status" role="status">{status}</span>
      <button class="btn" type="button" on:click={() => onClose(false)} disabled={saving}>Cancel</button>
      <button class="btn primary" type="button" on:click={save} disabled={saving || !settings}>
        {saving ? "Saving…" : "Save"}</button>
    </div>
  </div>
</div>

<style>
  .overlay { position: fixed; inset: 0; z-index: 60; display: flex; background: rgba(15, 20, 26, 0.45); }
  .dialog { width: min(92vw, 40rem); max-height: calc(100vh - 3rem); margin: 1.5rem auto auto; display: flex;
    flex-direction: column; background: var(--surface); color: var(--text); border: 1px solid var(--border);
    border-radius: 10px; box-shadow: 0 12px 40px rgba(0, 0, 0, .2); box-sizing: border-box; overflow: hidden; }
  .dlg-head { display: flex; align-items: center; gap: 0.5rem; padding: 0.7rem 0.8rem 0.7rem 1.2rem;
    border-bottom: 1px solid var(--border); }
  .dlg-head h2 { flex: 1; margin: 0; font-size: var(--fs-lg); }
  .x { width: 2rem; height: 2rem; border: 0; border-radius: 6px; background: none; color: var(--text-2);
    font-size: var(--fs-lg); cursor: pointer; }
  .x:hover, .x:focus-visible { background: var(--surface-2); }
  .dlg-body { overflow: auto; padding: 0.6rem 1.2rem; font-size: var(--fs-sm); }
  .dlg-foot { display: flex; align-items: center; gap: 0.5rem; padding: 0.7rem 1.2rem; border-top: 1px solid var(--border);
    background: var(--surface-2); }
  .status { flex: 1; color: var(--text-2); font-size: var(--fs-sm); }
  .row { display: flex; align-items: center; gap: 0.5rem; margin: 0.25rem 0; }
  .row.num span { flex: 1; }
  .row.num input { width: 5.5rem; }
  .grow { flex: 1; }
  .count { color: var(--text-3); font-variant-numeric: tabular-nums; min-width: 2.5rem; text-align: right; }
  h3 { margin: 0.7rem 0 0.2rem; font-size: var(--fs-sm); color: var(--text-2); }
  .hint, .muted { color: var(--text-3); margin: 0.3rem 0; }
  .notice { background: var(--accent-bg); border-radius: 6px; padding: 0.4rem 0.6rem; }
  .error { color: var(--danger, #b42318); }
  .btn { padding: 0.35rem 0.9rem; border: 1px solid var(--border-strong); border-radius: 6px; background: var(--surface);
    color: var(--text); cursor: pointer; font: inherit; }
  .btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
  .btn:disabled { opacity: 0.55; cursor: default; }
</style>
