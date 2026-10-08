<script lang="ts">
  // A section that folds, as the indic-qa web app's Settings dialog folds its
  // fieldsets: the legend is the switch (▾ open, ▸ closed), every section
  // starts closed, and the caller remembers which are open.
  export let legend: string;
  export let id: string;
  export let open = false;
  export let onToggle: (open: boolean) => void = () => {};

  function toggle(): void {
    open = !open;
    onToggle(open);
  }
</script>

<fieldset class:closed={!open}>
  <legend>
    <button type="button" aria-expanded={open} aria-controls={id} on:click={toggle}>{legend}</button>
  </legend>
  <div {id} hidden={!open}>
    {#if open}<slot />{/if}
  </div>
</fieldset>

<style>
  fieldset { border: 1px solid var(--border); border-radius: 6px; margin: 0.4rem 0; padding: 0.3rem 0.8rem 0.6rem;
    min-width: 0; }
  fieldset.closed { border-color: transparent; border-top-color: var(--border); padding-bottom: 0; }
  legend { padding: 0; }
  legend button { display: inline-flex; align-items: center; padding: 0.1rem 0.35rem; border: 0; border-radius: 4px;
    background: none; color: var(--text); font: inherit; font-weight: 600; cursor: pointer; user-select: none; }
  legend button::before { content: "\25BE"; display: inline-block; width: 1em; color: var(--text-3); }
  fieldset.closed legend button::before { content: "\25B8"; }
  legend button:hover, legend button:focus-visible { color: var(--accent); outline: none; }
  legend button:focus-visible { box-shadow: 0 0 0 2px var(--accent-bg); }
</style>
