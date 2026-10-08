import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { get } from "svelte/store";

const { checkerGet, checkerSet, wordsList, nudge } = vi.hoisted(() => ({
  checkerGet: vi.fn(),
  checkerSet: vi.fn(),
  wordsList: vi.fn(),
  nudge: vi.fn(),
}));

vi.mock("../../api/bridgeClient", () => ({
  bridge: {
    languageQaCheckerSettingsGet: checkerGet,
    languageQaCheckerSettingsSet: checkerSet,
    languageQaWordsList: wordsList,
  },
}));
vi.mock("../../languageQaInline", () => ({ nudgeLanguageQa: nudge }));

import CheckerSettingsDialog from "../CheckerSettingsDialog.svelte";
import { checkerSettingsOpen } from "../../editorPrefs";
import type { CheckerSettings } from "../../types/languageQa";

function hindi(overrides: Partial<CheckerSettings> = {}): CheckerSettings {
  return {
    pack: "hi-irv", language: "Hindi", layer: false, ready: true, books: ["gen", "exo"], legend: "Hindi checks",
    contexts: { checkable: ["verse"], checked: ["verse"], labels: { verse: "verse text" } },
    warnings: { values: { double_space: true, zwj: true }, labels: { double_space: "Double spaces", zwj: "Stray joiners" } },
    suggest: { max: 9, limit: 9 },
    compound: { enabled: false },
    rules: [
      { id: "hi.lex.unknown", label: "Unknown words", default: true, enabled: true, count: 4 },
      { id: "hi.lex.archaic-form", label: "Archaic forms", default: false, enabled: false, count: 0 },
    ],
    numbers: [{ group: "lex", key: "irv_accept_min", label: "Accept a word used at least this often in the IRV",
      min: 1, default: 5, value: 5 }],
    style: [{ id: "nukta", label: "Nukta", close: false, options: [{ value: "auto", label: "Either" },
      { value: "with", label: "With nukta" }], value: "auto" }],
    ...overrides,
  };
}

function tamil(): CheckerSettings {
  return {
    ...hindi(), pack: "ta-irv", language: "Tamil", layer: true, legend: "Tamil checks", numbers: undefined, style: undefined,
    contexts: { checkable: ["verse", "heading", "footnote_text"], checked: ["verse", "heading", "footnote_text"],
      labels: { verse: "verse text", heading: "section headings (\\s)", footnote_text: "footnote text (\\ft)" } },
    rules: [{ id: "indicqa.lex.unknown", label: "Unknown words", default: true, enabled: true, count: 129 }],
    sandhi: { values: { min_total: 3, include_weak: true }, labels: { min_total: "Minimum total occurrences",
      include_weak: "Include weak leads (80% rule)" } },
  };
}

async function openSection(name: string | RegExp): Promise<void> {
  await fireEvent.click(await screen.findByRole("button", { name }));
}

describe("CheckerSettingsDialog: the indic-qa web app's Settings dialog", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    checkerSettingsOpen.set([]);
    localStorage.clear();
    wordsList.mockResolvedValue({ added: [{}, {}] });
  });
  afterEach(() => cleanup());

  it("loads on open, every section closed, and offers the rule pack's section for a profile pack", async () => {
    checkerGet.mockResolvedValue(hindi());
    render(CheckerSettingsDialog, { props: { projectPath: "/p", onClose: vi.fn() } });
    expect(await screen.findByRole("dialog", { name: "Checker settings — Hindi" })).toBeInTheDocument();
    expect(checkerGet).toHaveBeenCalledWith("/p");
    for (const name of ["Check these text contexts", "Hindi checks", "Warnings", "Suggestions and compounds", "Project words"]) {
      expect(screen.getByRole("button", { name })).toHaveAttribute("aria-expanded", "false");
    }
    expect(screen.queryByRole("button", { name: "Sandhi leads" })).toBeNull();
    expect(screen.queryByLabelText(/Unknown words/)).toBeNull();
  });

  it("offers Sandhi leads and three contexts only for the Tamil layer", async () => {
    checkerGet.mockResolvedValue(tamil());
    render(CheckerSettingsDialog, { props: { projectPath: "/p", onClose: vi.fn() } });
    await openSection("Sandhi leads");
    expect(screen.getByLabelText("Include weak leads (80% rule)")).toBeChecked();
    await openSection("Check these text contexts");
    expect(screen.getByLabelText("footnote text (\\ft)")).toBeChecked();
  });

  it("remembers which sections are open, under its own key", async () => {
    checkerGet.mockResolvedValue(hindi());
    render(CheckerSettingsDialog, { props: { projectPath: "/p", onClose: vi.fn() } });
    await openSection("Warnings");
    expect(screen.getByRole("button", { name: "Warnings" })).toHaveAttribute("aria-expanded", "true");
    expect(get(checkerSettingsOpen)).toEqual(["warnings"]);
    expect(JSON.parse(localStorage.getItem("bridge.checkerSettings.open.v1") ?? "[]")).toEqual(["warnings"]);
    cleanup();
    render(CheckerSettingsDialog, { props: { projectPath: "/p", onClose: vi.fn() } });
    expect(await screen.findByLabelText("Double spaces")).toBeInTheDocument();
  });

  it("shows each rule's count in this book, and the rule id as its tooltip", async () => {
    checkerGet.mockResolvedValue(hindi());
    render(CheckerSettingsDialog, { props: { projectPath: "/p", onClose: vi.fn() } });
    await openSection("Hindi checks");
    const row = screen.getByLabelText(/Unknown words/).closest("label") as HTMLElement;
    expect(row.title).toBe("hi.lex.unknown");
    expect(row.querySelector(".count")?.textContent).toBe("4");
    expect(screen.getByLabelText("Nukta")).toHaveValue("auto");
  });

  it("Cancel sends nothing", async () => {
    checkerGet.mockResolvedValue(hindi());
    const onClose = vi.fn();
    render(CheckerSettingsDialog, { props: { projectPath: "/p", onClose } });
    await openSection("Warnings");
    await fireEvent.click(screen.getByLabelText("Double spaces"));
    await fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalledWith(false);
    expect(checkerSet).not.toHaveBeenCalled();
  });

  it("Save sends only what changed, then a pass runs", async () => {
    checkerGet.mockResolvedValue(hindi());
    checkerSet.mockResolvedValue(hindi());
    const onClose = vi.fn();
    render(CheckerSettingsDialog, { props: { projectPath: "/p", onClose } });
    await openSection("Warnings");
    await fireEvent.click(screen.getByLabelText("Double spaces"));
    await openSection("Hindi checks");
    await fireEvent.click(screen.getByLabelText(/Archaic forms/));
    await fireEvent.input(screen.getByLabelText("Accept a word used at least this often in the IRV"), { target: { value: "3" } });
    await fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledWith(true));
    expect(checkerSet).toHaveBeenCalledWith("/p", {
      checker: { warnings: { double_space: false }, lex: { irv_accept_min: 3 } },
      rules: { "hi.lex.archaic-form": { enabled: true } },
    });
    expect(nudge).toHaveBeenCalledTimes(1);
  });

  it("Save with nothing changed only closes", async () => {
    checkerGet.mockResolvedValue(hindi());
    const onClose = vi.fn();
    render(CheckerSettingsDialog, { props: { projectPath: "/p", onClose } });
    await screen.findByRole("button", { name: "Warnings" });
    await fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onClose).toHaveBeenCalledWith(false);
    expect(checkerSet).not.toHaveBeenCalled();
  });

  it("shows the engine's refusal and stays open", async () => {
    checkerGet.mockResolvedValue(hindi());
    checkerSet.mockRejectedValue(new Error("lex.irv_accept_min must be at least 1"));
    const onClose = vi.fn();
    render(CheckerSettingsDialog, { props: { projectPath: "/p", onClose } });
    await openSection("Suggestions and compounds");
    await fireEvent.click(screen.getByLabelText("Accept compounds of known words"));
    await fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("lex.irv_accept_min must be at least 1")).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("says it is still being prepared before a pass has loaded the checker", async () => {
    checkerGet.mockResolvedValue(hindi({ ready: false, style: [] }));
    render(CheckerSettingsDialog, { props: { projectPath: "/p", onClose: vi.fn() } });
    expect(await screen.findByText(/Still being prepared/)).toBeInTheDocument();
  });

  it("says why when the language has no checker, and Save is off", async () => {
    checkerGet.mockResolvedValue({ pack: null, available: false, reason: "This project's language has no indic-qa checker." });
    render(CheckerSettingsDialog, { props: { projectPath: "/p", onClose: vi.fn() } });
    expect(await screen.findByText(/has no indic-qa checker/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("closes on Escape, and Tab stays inside", async () => {
    checkerGet.mockResolvedValue(hindi());
    const onClose = vi.fn();
    render(CheckerSettingsDialog, { props: { projectPath: "/p", onClose } });
    const close = screen.getByRole("button", { name: "Close" });
    await waitFor(() => expect(document.activeElement).toBe(close));
    const save = await screen.findByRole("button", { name: "Save" });
    save.focus();
    await fireEvent.keyDown(save, { key: "Tab" });
    expect(document.activeElement).toBe(close);
    await fireEvent.keyDown(close, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(save);
    await fireEvent.keyDown(save, { key: "Escape" });
    expect(onClose).toHaveBeenCalledWith(false);
  });
});
