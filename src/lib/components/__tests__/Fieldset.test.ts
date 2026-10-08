import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/svelte";

import Fieldset from "../Fieldset.svelte";

describe("Fieldset: a section that folds at its legend", () => {
  afterEach(() => cleanup());

  it("starts closed, opens from its legend button, and says which way it went", async () => {
    const onToggle = vi.fn();
    const { container } = render(Fieldset, { props: { legend: "Warnings", id: "f-warn", onToggle } });
    const button = screen.getByRole("button", { name: "Warnings" });
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(button).toHaveAttribute("aria-controls", "f-warn");
    expect(container.querySelector("fieldset")?.classList.contains("closed")).toBe(true);
    expect((container.querySelector("#f-warn") as HTMLElement).hidden).toBe(true);
    await fireEvent.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(container.querySelector("fieldset")?.classList.contains("closed")).toBe(false);
    expect(onToggle).toHaveBeenLastCalledWith(true);
    await fireEvent.click(button);
    expect(onToggle).toHaveBeenLastCalledWith(false);
  });

  it("opens as told", () => {
    render(Fieldset, { props: { legend: "Rules", id: "f-rules", open: true } });
    expect(screen.getByRole("button", { name: "Rules" })).toHaveAttribute("aria-expanded", "true");
  });
});
