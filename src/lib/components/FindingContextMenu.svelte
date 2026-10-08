<script lang="ts">
  import { createEventDispatcher, onMount, tick } from "svelte";

  interface FindingMenuAction {
    id: string;
    label: string;
    disabled?: boolean;
    title?: string;
    separatorBefore?: boolean;
    // One level of flyout only (issue #69's "AI review ▸ Verse/Chapter/Book")
    // -- a leaf here dispatches "action" exactly like a top-level item, so a
    // consumer never has to know whether an id it handles came from the top
    // menu or a submenu.
    submenu?: FindingMenuAction[];
    // A line of text, not an item (the word and what was found): never
    // focusable, never dispatched. `tag` is its second, muted line.
    header?: boolean;
    // Shown on the item, right-aligned and muted ("vowel length · 412"), as
    // the indic-qa editor shows a suggestion's kind and frequency.
    tag?: string;
    // The label is Scripture: set in the target-script font.
    target?: boolean;
    // Small buttons on the item's row, as the indic-qa editor's Here /
    // Chapter / Book: each dispatches `${id}:${scope.id}`; the item itself
    // is "here". ArrowRight / ArrowLeft move across them.
    scopes?: { id: string; label: string; title?: string }[];
  }

  export let x = 0;
  export let y = 0;
  export let findingLabel = "Finding actions";
  export let actions: FindingMenuAction[] = [];

  const dispatch = createEventDispatcher<{
    action: { id: string };
    close: void;
  }>();

  const EDGE_GAP = 8;
  let menu: HTMLDivElement | null = null;
  let submenuEl: HTMLDivElement | null = null;
  let left = x;
  let top = y;
  let previousFocus: HTMLElement | null = null;

  // Id of the top-level action whose submenu is open, or "" for none.
  let openSubmenuId = "";
  let submenuAnchor: HTMLButtonElement | null = null;
  let submenuLeft = 0;
  let submenuTop = 0;

  function close(restoreFocus = false): void {
    if (restoreFocus && previousFocus?.isConnected) previousFocus.focus();
    dispatch("close");
  }

  function closeSubmenu(focusAnchor = false): void {
    const anchor = submenuAnchor;
    openSubmenuId = "";
    submenuAnchor = null;
    if (focusAnchor) anchor?.focus();
  }

  function clamp(preferred: number, size: number, viewportSize: number): number {
    return Math.max(EDGE_GAP, Math.min(preferred, viewportSize - size - EDGE_GAP));
  }

  function positionInsideViewport(): void {
    if (!menu) return;
    const rect = menu.getBoundingClientRect();
    left = clamp(x, rect.width, window.innerWidth);
    top = clamp(y, rect.height, window.innerHeight);
  }

  /** To the right of the top menu, aligned with the button that opened it;
   * flips to the left of the top menu when it wouldn't fit on the right, so
   * a menu opened near the right edge of the window doesn't run offscreen. */
  function positionSubmenu(button: HTMLButtonElement): void {
    if (!submenuEl || !menu) return;
    const buttonRect = button.getBoundingClientRect();
    const menuRect = menu.getBoundingClientRect();
    const subRect = submenuEl.getBoundingClientRect();
    const preferredLeft = menuRect.right + 2;
    const fitsRight = preferredLeft + subRect.width + EDGE_GAP <= window.innerWidth;
    submenuLeft = fitsRight ? preferredLeft : Math.max(EDGE_GAP, menuRect.left - subRect.width - 2);
    submenuTop = clamp(buttonRect.top, subRect.height, window.innerHeight);
  }

  function repositionAll(): void {
    positionInsideViewport();
    if (openSubmenuId && submenuAnchor) positionSubmenu(submenuAnchor);
  }

  async function openSubmenuFor(action: FindingMenuAction, button: HTMLButtonElement): Promise<void> {
    openSubmenuId = action.id;
    submenuAnchor = button;
    await tick();
    positionSubmenu(button);
    firstEnabled(submenuEl)?.focus();
  }

  function choose(action: FindingMenuAction, event: Event): void {
    if (action.disabled) return;
    if (action.submenu?.length) {
      void openSubmenuFor(action, event.currentTarget as HTMLButtonElement);
      return;
    }
    dispatch("action", { id: action.id });
  }

  function chooseSub(action: FindingMenuAction): void {
    if (action.disabled) return;
    dispatch("action", { id: action.id });
  }

  function chooseScope(action: FindingMenuAction, scope: string): void {
    if (action.disabled) return;
    dispatch("action", { id: `${action.id}:${scope}` });
  }

  // Up/Down walk the items; a row's scope buttons are reached with Right.
  const ITEM = '[role="menuitem"]:not(:disabled):not(.scope)';

  function firstEnabled(container: HTMLElement | null): HTMLButtonElement | null {
    return container?.querySelector<HTMLButtonElement>(ITEM) ?? null;
  }

  function enabledItems(container: HTMLElement | null): HTMLButtonElement[] {
    return container ? Array.from(container.querySelectorAll<HTMLButtonElement>(ITEM)) : [];
  }

  /** Right/Left inside a row with scope buttons: item -> its first scope,
   * scope -> the next or previous one, and back to the item. */
  function moveInRow(button: HTMLButtonElement, step: 1 | -1): boolean {
    const row = button.closest(".mi-scoped");
    if (!row) return false;
    const stops = Array.from(row.querySelectorAll<HTMLButtonElement>('button:not(:disabled)'));
    const next = stops[stops.indexOf(button) + step];
    if (!next) return step === -1 ? false : true;
    next.focus();
    return true;
  }

  function onKeydown(event: KeyboardEvent): void {
    const inSubmenu = Boolean(openSubmenuId) && Boolean(submenuEl?.contains(event.target as Node));

    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      if (inSubmenu) closeSubmenu(true);
      else close(true);
      return;
    }
    if (event.key === "Tab") {
      close();
      return;
    }
    if ((event.key === "ArrowRight" || event.key === "ArrowLeft") && !inSubmenu
        && moveInRow(event.target as HTMLButtonElement, event.key === "ArrowRight" ? 1 : -1)) {
      event.preventDefault();
      return;
    }
    if (event.key === "ArrowLeft" && inSubmenu) {
      event.preventDefault();
      closeSubmenu(true);
      return;
    }
    if (event.key === "ArrowRight" && !inSubmenu) {
      const button = event.target as HTMLButtonElement;
      const action = actions.find((a) => a.id === button.dataset.actionId);
      if (action?.submenu?.length && !action.disabled) {
        event.preventDefault();
        void openSubmenuFor(action, button);
      }
      return;
    }
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const items = enabledItems(inSubmenu ? submenuEl : menu);
    if (!items.length) return;
    // From a scope button, Up/Down continue from its row's item.
    const active = document.activeElement as HTMLButtonElement;
    const owner = active?.classList.contains("scope")
      ? active.closest(".mi-scoped")?.querySelector<HTMLButtonElement>(ITEM) ?? active : active;
    const current = items.indexOf(owner);
    const next = event.key === "Home"
      ? 0
      : event.key === "End"
        ? items.length - 1
        : (current + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
    items[next]?.focus();
  }

  function onOutsidePointer(event: PointerEvent): void {
    const target = event.target as Node;
    if (menu?.contains(target)) return;
    if (submenuEl?.contains(target)) return;
    close();
  }

  onMount(() => {
    previousFocus = document.activeElement as HTMLElement | null;
    const start = async (): Promise<void> => {
      await tick();
      positionInsideViewport();
      enabledItems(menu)[0]?.focus();
    };
    void start();
    window.addEventListener("pointerdown", onOutsidePointer, true);
    window.addEventListener("resize", repositionAll);
    return () => {
      window.removeEventListener("pointerdown", onOutsidePointer, true);
      window.removeEventListener("resize", repositionAll);
    };
  });
</script>

<div
  bind:this={menu}
  class="finding-menu"
  role="menu"
  tabindex="-1"
  aria-label={findingLabel}
  style:left="{left}px"
  style:top="{top}px"
  on:keydown={onKeydown}
  on:contextmenu|preventDefault
>
  {#each actions as action (action.id)}
    {#if action.separatorBefore}<div class="separator" role="separator"></div>{/if}
    {#if action.header}
      <div class="hdr" role="presentation" title={action.title}>
        <span class:target={action.target}>{action.label}</span>
        {#if action.tag}<small>{action.tag}</small>{/if}
      </div>
    {:else if action.scopes?.length}
      <div class="mi-scoped">
        <button
          type="button"
          role="menuitem"
          data-action-id={action.id}
          disabled={action.disabled}
          title={action.title}
          aria-label={action.tag ? `${action.label}, ${action.tag}` : undefined}
          on:click={(e) => choose(action, e)}
        ><span class:target={action.target}>{action.label}</span>{#if action.tag}<span class="tag" aria-hidden="true">{action.tag}</span>{/if}</button>
        <span class="scopes">
          {#each action.scopes as scope (scope.id)}
            <button
              type="button"
              role="menuitem"
              class="scope"
              tabindex="-1"
              disabled={action.disabled}
              title={scope.title ?? `${action.label}: ${scope.label}`}
              aria-label={`${action.label}: ${scope.label}`}
              on:click={() => chooseScope(action, scope.id)}
            >{scope.label}</button>
          {/each}
        </span>
      </div>
    {:else}
      <button
        type="button"
        role="menuitem"
        data-action-id={action.id}
        disabled={action.disabled}
        title={action.title}
        aria-haspopup={action.submenu?.length ? "menu" : undefined}
        aria-expanded={action.submenu?.length ? openSubmenuId === action.id : undefined}
        aria-label={action.tag ? `${action.label}, ${action.tag}` : undefined}
        on:click={(e) => choose(action, e)}
      ><span class:target={action.target}>{action.label}</span>{#if action.tag}<span class="tag" aria-hidden="true">{action.tag}</span>{/if}{#if action.submenu?.length}<span class="submenu-caret" aria-hidden="true">▸</span>{/if}</button>
    {/if}
  {/each}
</div>

{#if openSubmenuId}
  {@const parentAction = actions.find((a) => a.id === openSubmenuId)}
  {#if parentAction?.submenu?.length}
    <div
      bind:this={submenuEl}
      class="finding-menu submenu"
      role="menu"
      tabindex="-1"
      aria-label={parentAction.label}
      style:left="{submenuLeft}px"
      style:top="{submenuTop}px"
      on:keydown={onKeydown}
      on:contextmenu|preventDefault
    >
      {#each parentAction.submenu as sub (sub.id)}
        <button
          type="button"
          role="menuitem"
          disabled={sub.disabled}
          title={sub.title}
          on:click={() => chooseSub(sub)}
        ><span>{sub.label}</span></button>
      {/each}
    </div>
  {/if}
{/if}

<style>
  .finding-menu {
    position: fixed;
    z-index: 10000;
    box-sizing: border-box;
    min-width: 14rem;
    max-width: min(34rem, calc(100vw - 16px));
    max-height: calc(100vh - 16px);
    overflow-y: auto;
    padding: 0.3rem;
    border: 1px solid var(--border-strong, #cbd5e1);
    border-radius: 7px;
    background: var(--surface, #fff);
    color: var(--text, #111827);
    box-shadow: 0 10px 28px rgba(15, 23, 42, 0.22);
  }

  .finding-menu.submenu { min-width: 9rem; }

  button {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.6rem;
    width: 100%;
    padding: 0.45rem 0.6rem;
    border: 0;
    border-radius: 4px;
    background: transparent;
    color: inherit;
    font: inherit;
    font-size: var(--fs-md);
    text-align: left;
    cursor: pointer;
  }

  button:hover:not(:disabled), button:focus-visible:not(:disabled) {
    background: var(--accent-bg, #eff6ff);
    outline: none;
  }

  /* Focus moves into the submenu as soon as it opens, so without this the
     parent item (e.g. "AI review") would look unremarkable while its own
     flyout is the thing on screen -- there'd be nothing to look "expanded". */
  button[aria-expanded="true"] {
    background: var(--accent-bg, #eff6ff);
  }

  button:disabled {
    color: var(--text-3, #94a3b8);
    cursor: not-allowed;
  }

  .submenu-caret { color: var(--text-3, #94a3b8); flex-shrink: 0; }

  /* The word and what was found: a line of text, not an item. */
  .hdr { display: flex; flex-direction: column; gap: 0.1rem; padding: 0.35rem 0.6rem 0.4rem;
    border-bottom: 1px solid var(--border, #e5e7eb); margin-bottom: 0.2rem; }
  .hdr small { color: var(--text-2, #475569); font-size: var(--fs-xs); line-height: 1.35; }
  .target { font-family: var(--font-target); font-size: var(--fs-md); }
  .tag { margin-left: auto; padding-left: 0.8rem; color: var(--text-3, #94a3b8); font-size: var(--fs-xs);
    white-space: nowrap; flex-shrink: 0; }

  /* A row with Here (the item) / Chapter / Book buttons, as in the indic-qa editor. */
  .mi-scoped { display: flex; align-items: center; gap: 0.2rem; }
  .mi-scoped > button[role="menuitem"]:not(.scope) { flex: 1; min-width: 0; }
  .scopes { display: inline-flex; gap: 0.15rem; flex-shrink: 0; opacity: 0.55; }
  .mi-scoped:hover .scopes, .mi-scoped:focus-within .scopes { opacity: 1; }
  button.scope { width: auto; padding: 0.15rem 0.45rem; border: 1px solid var(--border, #e5e7eb);
    border-radius: 4px; font-size: var(--fs-xs); color: var(--text-2, #475569); }

  .separator {
    height: 1px;
    margin: 0.25rem 0.15rem;
    background: var(--border, #e5e7eb);
  }
</style>
