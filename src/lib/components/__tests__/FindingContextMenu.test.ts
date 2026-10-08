import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";

import FindingContextMenu from "../FindingContextMenu.svelte";

const actions = [
  { id: "apply", label: "Apply proposed fix", disabled: true },
  { id: "accept", label: "Accept finding", separatorBefore: true },
  { id: "defer", label: "Needs discussion" },
];

const actionsWithSubmenu = [
  {
    id: "ai-review",
    label: "AI review",
    submenu: [
      { id: "ai-review:verse", label: "Verse" },
      { id: "ai-review:chapter", label: "Chapter", disabled: true },
    ],
  },
  { id: "edit-verse", label: "Edit verse", separatorBefore: true },
];

describe("FindingContextMenu", () => {
  it("keeps unavailable fixes visible but disabled", () => {
    render(FindingContextMenu, { props: { x: 20, y: 30, actions } });
    expect(screen.getByRole("menuitem", { name: "Apply proposed fix" })).toBeDisabled();
  });

  it("dispatches actions and closes with Escape", async () => {
    const { component } = render(FindingContextMenu, { props: { x: 20, y: 30, actions } });
    const action = vi.fn();
    const close = vi.fn();
    component.$on("action", (event) => action(event.detail.id));
    component.$on("close", close);

    await fireEvent.click(screen.getByRole("menuitem", { name: "Accept finding" }));
    expect(action).toHaveBeenCalledWith("accept");
    await fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });
    expect(close).toHaveBeenCalledTimes(1);
  });

  it("dismisses on an outside pointer and clamps to the viewport", async () => {
    const { component } = render(FindingContextMenu, {
      props: { x: 99999, y: 99999, actions },
    });
    const close = vi.fn();
    component.$on("close", close);
    const menu = screen.getByRole("menu");
    await waitFor(() => expect(parseFloat(menu.style.left)).toBeLessThan(window.innerWidth));
    await fireEvent.pointerDown(document.body);
    expect(close).toHaveBeenCalledTimes(1);
  });

  it("opens a submenu on click without dispatching an action, and dispatches from a leaf", async () => {
    const { component } = render(FindingContextMenu, {
      props: { x: 20, y: 30, actions: actionsWithSubmenu },
    });
    const action = vi.fn();
    component.$on("action", (event) => action(event.detail.id));

    await fireEvent.click(screen.getByRole("menuitem", { name: "AI review" }));
    expect(action).not.toHaveBeenCalled();
    expect(screen.getByRole("menuitem", { name: "Verse" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Chapter" })).toBeDisabled();

    await fireEvent.click(screen.getByRole("menuitem", { name: "Verse" }));
    expect(action).toHaveBeenCalledWith("ai-review:verse");
  });

  it("navigates into a submenu with ArrowRight and back out with ArrowLeft", async () => {
    render(FindingContextMenu, { props: { x: 20, y: 30, actions: actionsWithSubmenu } });
    const parent = screen.getByRole("menuitem", { name: "AI review" });
    parent.focus();
    await fireEvent.keyDown(parent, { key: "ArrowRight" });
    const verseItem = screen.getByRole("menuitem", { name: "Verse" });
    expect(verseItem).toHaveFocus();

    await fireEvent.keyDown(verseItem, { key: "ArrowLeft" });
    expect(screen.queryByRole("menuitem", { name: "Verse" })).toBeNull();
    expect(parent).toHaveFocus();
  });

  it("closes only the submenu on Escape, leaving the top menu open", async () => {
    const { component } = render(FindingContextMenu, {
      props: { x: 20, y: 30, actions: actionsWithSubmenu },
    });
    const close = vi.fn();
    component.$on("close", close);
    await fireEvent.click(screen.getByRole("menuitem", { name: "AI review" }));
    const verseItem = screen.getByRole("menuitem", { name: "Verse" });
    await fireEvent.keyDown(verseItem, { key: "Escape" });
    expect(close).not.toHaveBeenCalled();
    expect(screen.queryByRole("menuitem", { name: "Verse" })).toBeNull();
    expect(screen.getByRole("menuitem", { name: "AI review" })).toHaveFocus();
  });

  it("does not dismiss as an outside click when the pointer is inside the submenu", async () => {
    const { component } = render(FindingContextMenu, {
      props: { x: 20, y: 30, actions: actionsWithSubmenu },
    });
    const close = vi.fn();
    component.$on("close", close);
    await fireEvent.click(screen.getByRole("menuitem", { name: "AI review" }));
    await fireEvent.pointerDown(screen.getByRole("menuitem", { name: "Verse" }));
    expect(close).not.toHaveBeenCalled();
  });
});

describe("FindingContextMenu, as the indic-qa editor's word menu", () => {
  const wordMenu = [
    { id: "header", header: true, target: true, label: "“மனூஷ்யன்”", tag: "Not in the OV dictionary" },
    { id: "use:1", label: 'Use "மனுஷ்யன்"', target: true, tag: "vowel length · 412",
      scopes: [{ id: "chapter", label: "Chapter" }, { id: "book", label: "Book" }] },
    { id: "ignore", label: "Ignore this occurrence", separatorBefore: true },
  ];

  it("shows the word as a line of text, not an item, and a suggestion's kind and frequency", () => {
    render(FindingContextMenu, { props: { x: 20, y: 30, actions: wordMenu } });
    expect(screen.getByRole("menu").textContent).toContain("“மனூஷ்யன்”");
    expect(screen.queryByRole("menuitem", { name: /மனூஷ்யன்/ })).toBeNull();
    const use = screen.getByRole("menuitem", { name: 'Use "மனுஷ்யன்", vowel length · 412' });
    expect(use.querySelector(".tag")?.textContent).toBe("vowel length · 412");
  });

  it("dispatches a row's scope as id:scope, and the item itself as here", async () => {
    const seen: string[] = [];
    const { component } = render(FindingContextMenu, { props: { x: 20, y: 30, actions: wordMenu } });
    component.$on("action", (event: CustomEvent<{ id: string }>) => seen.push(event.detail.id));
    await fireEvent.click(screen.getByRole("menuitem", { name: 'Use "மனுஷ்யன்": Book' }));
    await fireEvent.click(screen.getByRole("menuitem", { name: 'Use "மனுஷ்யன்", vowel length · 412' }));
    expect(seen).toEqual(["use:1:book", "use:1"]);
  });

  it("walks a row's scope buttons with Right and Left, and the items with Down", async () => {
    render(FindingContextMenu, { props: { x: 20, y: 30, actions: wordMenu } });
    const use = screen.getByRole("menuitem", { name: 'Use "மனுஷ்யன்", vowel length · 412' });
    await waitFor(() => expect(document.activeElement).toBe(use), { timeout: 500 });
    await fireEvent.keyDown(use, { key: "ArrowRight" });
    expect(document.activeElement).toBe(screen.getByRole("menuitem", { name: 'Use "மனுஷ்யன்": Chapter' }));
    await fireEvent.keyDown(document.activeElement as HTMLElement, { key: "ArrowRight" });
    expect(document.activeElement).toBe(screen.getByRole("menuitem", { name: 'Use "மனுஷ்யன்": Book' }));
    await fireEvent.keyDown(document.activeElement as HTMLElement, { key: "ArrowLeft" });
    await fireEvent.keyDown(document.activeElement as HTMLElement, { key: "ArrowLeft" });
    expect(document.activeElement).toBe(use);
    await fireEvent.keyDown(use, { key: "ArrowDown" });
    expect(document.activeElement).toBe(screen.getByRole("menuitem", { name: "Ignore this occurrence" }));
  });
});
