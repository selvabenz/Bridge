import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/svelte";

const { getSettings, getNavigationStatus, setSettings, engineInfo, terminologyList, terminologyRecord, housestyleList, housestyleRecord, housestyleSetState, housestyleNameSuggestions, languageQaStatus, languageQaSetPack, pickFolder, checkerGet, checkerSet, wordsList } = vi.hoisted(() => ({
  checkerGet: vi.fn(),
  checkerSet: vi.fn(),
  wordsList: vi.fn(),
  pickFolder: vi.fn(),
  languageQaStatus: vi.fn(),
  languageQaSetPack: vi.fn(),
  housestyleNameSuggestions: vi.fn(),
  housestyleList: vi.fn(),
  housestyleRecord: vi.fn(),
  housestyleSetState: vi.fn(),
  getSettings: vi.fn(),
  getNavigationStatus: vi.fn(),
  setSettings: vi.fn(),
  engineInfo: vi.fn(),
  terminologyList: vi.fn(),
  terminologyRecord: vi.fn(),
}));

vi.mock("../../api/bridgeClient", () => ({
  bridge: {
    getSettings,
    navigationStatus: getNavigationStatus,
    setSettings,
    engineInfo,
    terminologyList,
    terminologyRecord,
    housestyleList,
    housestyleRecord,
    housestyleSetState,
    housestyleNameSuggestions,
    languageQaStatus,
    languageQaSetPack,
    languageQaCheckerSettingsGet: checkerGet,
    languageQaCheckerSettingsSet: checkerSet,
    languageQaWordsList: wordsList,
    pickFolder,
  },
}));

import SettingsModal from "../SettingsModal.svelte";
import { project } from "../../stores";
import type { ProjectInfo } from "../../types/finding";

const target = {
  enabled: true,
  checking: true,
  connected: false,
  reference: "",
  error: "",
  checkedAt: 1,
};

function navigationState(
  paratextOverrides: Record<string, unknown> = {},
  logosOverrides: Record<string, unknown> = {},
) {
  return {
    enabled: true,
    ownsNavigation: true,
    ownerConflict: false,
    currentReference: "PHP 1:5",
    currentOrigin: "bridge",
    candidate: null,
    paratext: { ...target, ...paratextOverrides },
    logos: { ...target, ...logosOverrides },
  };
}

describe("SettingsModal connections", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    engineInfo.mockResolvedValue({
      bridgeVersion: "0.9.6", companionSchemaVersion: 14, projectOpen: false, greekRoom: {},
    });
    getSettings.mockResolvedValue({
      provider: "openai",
      apiBaseUrl: "",
      model: "gpt-5.6",
      hasApiKey: false,
      reviewerMode: "basic",
      paratextNavigation: true,
      logosNavigation: true,
    });
  });

  it("keeps the last connected status visible during a background refresh", async () => {
    getNavigationStatus.mockResolvedValue(navigationState({
      connected: true,
      project_name: "IRVTam",
      reference: "PHP 1:5",
    }));

    const { container } = render(SettingsModal, { props: { initialPane: "connections", onClose: vi.fn() } });

    await waitFor(() => {
      expect(container.querySelector(".connection-detail")).toHaveTextContent(/Connected.*IRVTam.*PHP 1:5/);
    });
  });

  it("keeps an actionable connector error visible during a background refresh", async () => {
    getNavigationStatus.mockResolvedValue(navigationState({}, { error: "Logos COM registration needs repair." }));

    render(SettingsModal, { props: { initialPane: "connections", onClose: vi.fn() } });

    expect(await screen.findByText("Logos COM registration needs repair.")).toBeInTheDocument();
    expect(screen.getAllByText("Checking…")).toHaveLength(1);
  });
});

describe("SettingsModal manual override", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    engineInfo.mockResolvedValue({
      bridgeVersion: "0.9.6", companionSchemaVersion: 14, projectOpen: false, greekRoom: {},
    });
    getNavigationStatus.mockResolvedValue(navigationState());
    setSettings.mockImplementation(async (params: Record<string, unknown>) => ({
      ...params, hasApiKey: false,
    }));
    getSettings.mockResolvedValue({
      provider: "openai",
      apiBaseUrl: "",
      model: "gpt-5.6",
      hasApiKey: false,
      reviewerMode: "basic",
      paratextNavigation: false,
      logosNavigation: false,
    });
  });

  it("offers manual override as a single checkbox, not a mode choice", async () => {
    const { container } = render(SettingsModal, { props: { initialPane: "quality", onClose: vi.fn() } });

    const checkbox = await screen.findByRole("checkbox", { name: /Allow manual override/ });
    expect(checkbox).not.toBeChecked();
    expect(container.querySelectorAll('input[type="radio"]')).toHaveLength(0);
    expect(screen.queryByText("Basic")).not.toBeInTheDocument();
    expect(screen.queryByText("Advanced")).not.toBeInTheDocument();
  });

  it("reflects a stored advanced setting as override enabled", async () => {
    getSettings.mockResolvedValue({
      provider: "openai", apiBaseUrl: "", model: "gpt-5.6", hasApiKey: false,
      reviewerMode: "advanced", paratextNavigation: false, logosNavigation: false,
    });
    render(SettingsModal, { props: { initialPane: "quality", onClose: vi.fn() } });

    expect(await screen.findByRole("checkbox", { name: /Allow manual override/ })).toBeChecked();
  });

  it("saves the checkbox back as the stored basic/advanced value", async () => {
    render(SettingsModal, { props: { initialPane: "quality", onClose: vi.fn() } });
    const checkbox = await screen.findByRole("checkbox", { name: /Allow manual override/ });

    await fireEvent.click(checkbox);
    await fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(setSettings).toHaveBeenCalledWith(
      expect.objectContaining({ reviewerMode: "advanced" }),
    ));
  });
});

describe("SettingsModal reviewer name (V11-002/V11-005)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    engineInfo.mockResolvedValue({
      bridgeVersion: "0.9.6", companionSchemaVersion: 14, projectOpen: false, greekRoom: {},
    });
    getNavigationStatus.mockResolvedValue(navigationState());
    setSettings.mockImplementation(async (params: Record<string, unknown>) => ({
      ...params, hasApiKey: false,
      reviewerName: params.reviewerName, reviewerNameUpdatedAt: "2026-09-11T08:00:00+00:00",
    }));
  });

  it("shows the OS-seeded reviewer name as never explicitly changed", async () => {
    getSettings.mockResolvedValue({
      provider: "openai", apiBaseUrl: "", model: "gpt-5.6", hasApiKey: false,
      reviewerName: "benz", reviewerNameUpdatedAt: "",
      reviewerMode: "basic", paratextNavigation: false, logosNavigation: false,
    });
    render(SettingsModal, { props: { initialPane: "quality", onClose: vi.fn() } });

    expect(await screen.findByLabelText("Reviewer name")).toHaveValue("benz");
    expect(screen.getByText(/never explicitly changed/i)).toBeInTheDocument();
  });

  it("shows the last-changed timestamp once the reviewer has been renamed", async () => {
    getSettings.mockResolvedValue({
      provider: "openai", apiBaseUrl: "", model: "gpt-5.6", hasApiKey: false,
      reviewerName: "Alice", reviewerNameUpdatedAt: "2026-09-10T12:00:00+00:00",
      reviewerMode: "basic", paratextNavigation: false, logosNavigation: false,
    });
    render(SettingsModal, { props: { initialPane: "quality", onClose: vi.fn() } });

    expect(await screen.findByLabelText("Reviewer name")).toHaveValue("Alice");
    expect(screen.getByText(/Last changed/i)).toBeInTheDocument();
  });

  it("saves an edited reviewer name and reflects the fresh timestamp back", async () => {
    getSettings.mockResolvedValue({
      provider: "openai", apiBaseUrl: "", model: "gpt-5.6", hasApiKey: false,
      reviewerName: "benz", reviewerNameUpdatedAt: "",
      reviewerMode: "basic", paratextNavigation: false, logosNavigation: false,
    });
    render(SettingsModal, { props: { initialPane: "quality", onClose: vi.fn() } });
    const field = await screen.findByLabelText("Reviewer name");

    await fireEvent.input(field, { target: { value: "Alice" } });
    await fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(setSettings).toHaveBeenCalledWith(
      expect.objectContaining({ reviewerName: "Alice" }),
    ));
  });
});

describe("SettingsModal version display (V11-011)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getNavigationStatus.mockResolvedValue(navigationState());
    getSettings.mockResolvedValue({
      provider: "openai", apiBaseUrl: "", model: "gpt-5.6", hasApiKey: false,
      reviewerName: "benz", reviewerNameUpdatedAt: "",
      reviewerMode: "basic", paratextNavigation: false, logosNavigation: false,
    });
  });

  it("shows the version and schema exactly as the running process reports them, not a fixed constant", async () => {
    // A deliberately unusual, mismatched pair a hardcoded frontend copy of
    // "0.9.6"/"14" would never produce -- proves this reads the real
    // runtime value rather than a constant baked into the component.
    engineInfo.mockResolvedValue({
      bridgeVersion: "9.9.9-test", companionSchemaVersion: 999,
      projectOpen: false, greekRoom: {},
    });
    render(SettingsModal, { props: { initialPane: "ai", onClose: vi.fn() } });

    expect(await screen.findByText(/Bridge 9\.9\.9-test/)).toBeInTheDocument();
    expect(screen.getByText(/Schema v999/)).toBeInTheDocument();
  });

  it("shows nothing rather than a stale placeholder while engine info hasn't loaded", async () => {
    engineInfo.mockImplementation(() => new Promise(() => {})); // never resolves
    const { container } = render(SettingsModal, { props: { initialPane: "ai", onClose: vi.fn() } });

    await waitFor(() => expect(screen.queryByText(/Loading/)).not.toBeInTheDocument());
    expect(container.querySelector(".about-version")).toBeNull();
  });
});

describe("SettingsModal terminology (#171)", () => {
  function minimalProject(): ProjectInfo {
    return {
      path: "C:/projects/php", bookId: "php", bookName: "Philippians",
      targetLanguage: "Tamil", tcVersion: "9", chapters: ["1"], checkTypes: {},
    } as ProjectInfo;
  }

  beforeEach(() => {
    vi.clearAllMocks();
    project.set(null);
    housestyleList.mockResolvedValue({ entries: [], proposals: [], thresholds: { learnIgnores: 3 } });
    housestyleNameSuggestions.mockResolvedValue({ suggestions: [], checked: false });
    engineInfo.mockResolvedValue({
      bridgeVersion: "0.9.6", companionSchemaVersion: 14, projectOpen: true, greekRoom: {},
    });
    getNavigationStatus.mockResolvedValue(navigationState());
    getSettings.mockResolvedValue({
      provider: "openai", apiBaseUrl: "", model: "gpt-5.6", hasApiKey: false,
      reviewerMode: "basic", paratextNavigation: false, logosNavigation: false,
    });
  });

  it("chooses this project's Language QA pack and says what changed", async () => {
    project.set(minimalProject());
    terminologyList.mockResolvedValue({ rules: [] });
    const packs = [{ language: "hi", name: "Hindi", pack: "hi-irv" }, { language: "ta", name: "Tamil", pack: "ta-irv" }];
    languageQaStatus.mockResolvedValue({ setting: "auto", packs });
    languageQaSetPack.mockResolvedValue({ setting: "off", packs });
    render(SettingsModal, { props: { initialPane: "languageQa", onClose: vi.fn() } });

    const select = (await screen.findByLabelText("Rules")) as HTMLSelectElement;
    expect([...select.options].map((o) => o.value)).toEqual(["auto", "hi-irv", "ta-irv", "off"]);
    expect(select.value).toBe("auto");
    await fireEvent.change(select, { target: { value: "off" } });
    await waitFor(() => expect(languageQaSetPack).toHaveBeenCalledWith(minimalProject().path, "off"));
    expect(await screen.findByText(/common checks only/)).toBeInTheDocument();
  });

  it("summarises the checker's settings and opens the Checker settings dialog", async () => {
    project.set(minimalProject());
    terminologyList.mockResolvedValue({ rules: [] });
    const packs = [{ language: "hi", name: "Hindi", pack: "hi-irv" }];
    languageQaStatus.mockResolvedValue({ setting: "auto", packs });
    wordsList.mockResolvedValue({ added: [] });
    const settings = {
      pack: "hi-irv", language: "Hindi", layer: false, ready: true, books: ["php"], legend: "Hindi checks",
      contexts: { checkable: ["verse"], checked: ["verse"], labels: { verse: "verse text" } },
      warnings: { values: { a: true, b: false }, labels: { a: "A", b: "B" } },
      suggest: { max: 9, limit: 9 }, compound: { enabled: false },
      rules: [{ id: "r1", label: "R1", default: true, enabled: true, count: 0 },
        { id: "r2", label: "R2", default: false, enabled: false, count: 0 }],
    };
    checkerGet.mockResolvedValue(settings);
    render(SettingsModal, { props: { initialPane: "languageQa", onClose: vi.fn() } });

    expect(await screen.findByText(/1 of 2 rules on · checks verse text · 1\/2 warnings/)).toBeInTheDocument();
    await fireEvent.click(screen.getByRole("button", { name: "Checker settings…" }));
    expect(await screen.findByRole("dialog", { name: "Checker settings — Hindi" })).toBeInTheDocument();
    await fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog", { name: /Checker settings/ })).toBeNull();
  });

  it("offers no checker row for a language without an indic-qa checker", async () => {
    project.set(minimalProject());
    terminologyList.mockResolvedValue({ rules: [] });
    languageQaStatus.mockResolvedValue({ setting: "auto", packs: [{ language: "ta", name: "Tamil", pack: "ta-irv" }] });
    checkerGet.mockResolvedValue({ pack: null, available: false, reason: "none" });
    render(SettingsModal, { props: { initialPane: "languageQa", onClose: vi.fn() } });
    await screen.findByLabelText("Rules");
    await waitFor(() => expect(checkerGet).toHaveBeenCalled());
    expect(screen.queryByRole("button", { name: "Checker settings…" })).toBeNull();
  });

  it("leaves the Language QA choice out when its status cannot be read", async () => {
    project.set(minimalProject());
    terminologyList.mockResolvedValue({ rules: [] });
    languageQaStatus.mockRejectedValue(new Error("no pass yet"));
    render(SettingsModal, { props: { initialPane: "languageQa", onClose: vi.fn() } });

    expect(await screen.findByText(/No project house style recorded yet/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Rules")).toBeNull();
  });

  it("prompts to open a project instead of calling the RPC when none is open", async () => {
    render(SettingsModal, { props: { initialPane: "terminology", onClose: vi.fn() } });

    expect(await screen.findByText(/Open a project to manage its terminology/)).toBeInTheDocument();
    expect(terminologyList).not.toHaveBeenCalled();
  });

  it("renders existing rules for the open project", async () => {
    project.set(minimalProject());
    terminologyList.mockResolvedValue({
      rules: [{
        conceptId: "god", approvedRenderings: ["இறைவன்"], allowedAlternatives: [],
        rejectedRenderings: ["கடவுள்"], status: "approved", provenance: "human",
        modifiedTimestamp: "2026-09-23T05:24:44.736Z",
      }],
    });
    render(SettingsModal, { props: { initialPane: "terminology", onClose: vi.fn() } });

    expect(await screen.findByText("god")).toBeInTheDocument();
    expect(screen.getByText(/preferred: இறைவன்.*rejected: கடவுள்/)).toBeInTheDocument();
  });

  it("shows an empty-termbase message rather than nothing when there are no rules", async () => {
    project.set(minimalProject());
    terminologyList.mockResolvedValue({ rules: [] });
    render(SettingsModal, { props: { initialPane: "terminology", onClose: vi.fn() } });

    expect(await screen.findByText(/No terminology rules recorded/)).toBeInTheDocument();
  });

  it("adds a rule, splitting comma-separated renderings, and refreshes the list from the response", async () => {
    project.set(minimalProject());
    terminologyList.mockResolvedValue({ rules: [] });
    terminologyRecord.mockResolvedValue({
      rules: [{
        conceptId: "god", approvedRenderings: ["இறைவன்"], allowedAlternatives: [],
        rejectedRenderings: ["கடவுள்", "தேவன்"], status: "approved", provenance: "human",
        modifiedTimestamp: "2026-09-23T05:24:44.736Z",
      }],
    });
    render(SettingsModal, { props: { initialPane: "terminology", onClose: vi.fn() } });
    await screen.findByText(/No terminology rules recorded/);

    await fireEvent.input(screen.getByLabelText("Concept ID"), { target: { value: "god" } });
    await fireEvent.input(screen.getByLabelText("Preferred rendering(s)"), { target: { value: "இறைவன்" } });
    await fireEvent.input(screen.getByLabelText("Rejected rendering(s)"), { target: { value: "கடவுள், தேவன்" } });
    await fireEvent.click(screen.getByRole("button", { name: "Add rule" }));

    await waitFor(() => expect(terminologyRecord).toHaveBeenCalledWith(
      "god", ["இறைவன்"], ["கடவுள்", "தேவன்"], false, { allowedAlternatives: [], inflectedForms: {}, matchMode: "exact" },
    ));
    expect(await screen.findByText("god")).toBeInTheDocument();
    expect(screen.queryByText(/No terminology rules recorded/)).not.toBeInTheDocument();
    expect(screen.getByLabelText("Concept ID")).toHaveValue("");
  });

  it("asks before replacing an existing rule, and replaces it only when told to", async () => {
    const existing = {
      conceptId: "god", approvedRenderings: ["இறைவன்"], allowedAlternatives: [],
      rejectedRenderings: ["கடவுள்"], status: "approved", provenance: "human",
      modifiedTimestamp: "2026-09-23T05:24:44.736Z",
    };
    project.set(minimalProject());
    terminologyList.mockResolvedValue({ rules: [existing] });
    terminologyRecord.mockImplementation(async (_id, approved, _rejected, overwrite) => overwrite
      ? { rules: [{ ...existing, approvedRenderings: approved }] }
      : { rules: [existing], conflict: existing });
    render(SettingsModal, { props: { initialPane: "terminology", onClose: vi.fn() } });
    await screen.findByText(/preferred: இறைவன்/);

    await fireEvent.input(screen.getByLabelText("Concept ID"), { target: { value: "god" } });
    await fireEvent.input(screen.getByLabelText("Preferred rendering(s)"), { target: { value: "தேவன்" } });
    await fireEvent.click(screen.getByRole("button", { name: "Add rule" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Replace existing terminology rule" });
    expect(dialog.textContent).toContain("already exists");
    expect(terminologyRecord).toHaveBeenLastCalledWith("god", ["தேவன்"], [], false, { allowedAlternatives: [], inflectedForms: {}, matchMode: "exact" });
    // Nothing replaced yet, and the entry is kept for the reviewer to decide.
    expect(screen.getByLabelText("Concept ID")).toHaveValue("god");

    await fireEvent.click(screen.getByRole("button", { name: "Keep existing" }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(terminologyRecord).toHaveBeenCalledTimes(1);

    await fireEvent.click(screen.getByRole("button", { name: "Add rule" }));
    await fireEvent.click(await screen.findByRole("button", { name: "Replace" }));
    await waitFor(() => expect(terminologyRecord).toHaveBeenLastCalledWith("god", ["தேவன்"], [], true, { allowedAlternatives: [], inflectedForms: {}, matchMode: "exact" }));
    expect(await screen.findByText(/preferred: தேவன்/)).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });

  it("edits an existing rule with its v3 fields and saves it as a replacement (termbase v3)", async () => {
    const existing = {
      conceptId: "god", approvedRenderings: ["இறைவன்"], allowedAlternatives: ["கடவுள்"],
      rejectedRenderings: ["தேவன்"], status: "approved", provenance: "human",
      modifiedTimestamp: "2026-09-23T05:24:44.736Z",
      inflectedForms: { "தேவன்": ["தேவனே"] }, matchMode: "exact" as const,
    };
    project.set(minimalProject());
    terminologyList.mockResolvedValue({ rules: [existing] });
    terminologyRecord.mockResolvedValue({ rules: [{ ...existing, matchMode: "prefix" }] });
    render(SettingsModal, { props: { initialPane: "terminology", onClose: vi.fn() } });
    expect(await screen.findByText(/தேவன் → தேவனே/)).toBeInTheDocument();
    expect(screen.getByText(/allowed: கடவுள்/)).toBeInTheDocument();

    await fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    expect(screen.getByLabelText("Concept ID")).toHaveValue("god");
    expect(screen.getByLabelText("Inflected forms of a rejected rendering")).toHaveValue("தேவன்: தேவனே");
    await fireEvent.click(screen.getByRole("checkbox"));
    await fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(terminologyRecord).toHaveBeenLastCalledWith(
      "god", ["இறைவன்"], ["தேவன்"], true,
      { allowedAlternatives: ["கடவுள்"], inflectedForms: { "தேவன்": ["தேவனே"] }, matchMode: "prefix" },
    ));
    expect(await screen.findByText(/also with case endings/)).toBeInTheDocument();
  });

  it("lists house style with provenance and evidence, and removes, accepts and dismisses (layered-rules 6.3/6.4)", async () => {
    const learned = {
      scope: "word-in-book", ruleId: "ta-irv/sandhi.vallinam.demonstrative", word: "அந்த தேசம்", list: "",
      provenance: "learned", state: "active", imported: false, key: "k1", modifiedTimestamp: "t",
      evidence: [{ chapter: "1", verse: "1", decisionId: "a" }, { chapter: "1", verse: "2", decisionId: "b" },
                 { chapter: "1", verse: "3", decisionId: "c" }],
    };
    const proposal = { scope: "word-in-project", ruleId: learned.ruleId, word: learned.word, reason: "learned as house style in 2 books" };
    project.set(minimalProject());
    terminologyList.mockResolvedValue({ rules: [] });
    housestyleList.mockResolvedValue({ entries: [learned], proposals: [proposal], thresholds: { learnIgnores: 3 } });
    housestyleSetState.mockResolvedValue({ entries: [{ ...learned, state: "removed" }], proposals: [], thresholds: {}, entry: learned });
    housestyleRecord.mockResolvedValue({ entries: [learned], proposals: [], thresholds: {}, entry: learned });
    render(SettingsModal, { props: { initialPane: "languageQa", onClose: vi.fn() } });

    expect(await screen.findByText(/“அந்த தேசம்” for ta-irv\/sandhi.vallinam.demonstrative · word in book · 3 evidence/)).toBeInTheDocument();
    expect(screen.getByText("learned")).toBeInTheDocument();
    await fireEvent.click(screen.getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(housestyleRecord).toHaveBeenCalledWith(expect.objectContaining({
      scope: "word-in-project", word: "அந்த தேசம்", state: "active" })));
    await fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(housestyleSetState).toHaveBeenCalledWith("k1", "removed"));
    expect(await screen.findByText(/No project house style recorded yet/)).toBeInTheDocument();
  });

  it("shows the pack's bundled seed, and Remove records this project's own removed entry", async () => {
    const seed = { scope: "word-in-project", ruleId: "", word: "சேத்து", list: "properNouns", provenance: "curated",
      state: "active", imported: false, key: "word-in-project||சேத்து|properNouns", modifiedTimestamp: "",
      evidence: [{ chapter: "4", verse: "24", decisionId: "" }] };
    project.set(minimalProject());
    terminologyList.mockResolvedValue({ rules: [] });
    housestyleList.mockResolvedValue({ entries: [], seed: [seed], proposals: [], thresholds: { learnIgnores: 3 } });
    housestyleRecord.mockResolvedValue({ entries: [{ ...seed, state: "removed" }], seed: [], proposals: [], thresholds: {}, entry: seed });
    render(SettingsModal, { props: { initialPane: "languageQa", onClose: vi.fn() } });
    expect(await screen.findByText(/proper noun “சேத்து” · from the language pack's reviewed house style/)).toBeInTheDocument();
    expect(screen.getByText("bundled")).toBeInTheDocument();
    await fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(housestyleRecord).toHaveBeenCalledWith({ scope: "word-in-project", ruleId: "",
      word: "சேத்து", list: "properNouns", provenance: "curated", state: "removed" }));
  });

  it("adds a curated proper noun to the house-style list", async () => {
    project.set(minimalProject());
    terminologyList.mockResolvedValue({ rules: [] });
    housestyleRecord.mockResolvedValue({ entries: [], proposals: [], thresholds: {}, entry: {} });
    render(SettingsModal, { props: { initialPane: "languageQa", onClose: vi.fn() } });
    await screen.findByText(/No project house style recorded yet/);
    await fireEvent.input(screen.getByLabelText(/Add a proper noun/), { target: { value: "மோவாப்" } });
    await fireEvent.click(screen.getByRole("button", { name: "Add name" }));
    await waitFor(() => expect(housestyleRecord).toHaveBeenCalledWith(
      { scope: "word-in-book", list: "properNouns", word: "மோவாப்", provenance: "curated" }));
  });

  it("shows the RPC's validation error instead of silently doing nothing", async () => {
    project.set(minimalProject());
    terminologyList.mockResolvedValue({ rules: [] });
    terminologyRecord.mockRejectedValue(new Error("At least one approved, allowed, or rejected rendering is required."));
    render(SettingsModal, { props: { initialPane: "terminology", onClose: vi.fn() } });
    await screen.findByText(/No terminology rules recorded/);

    await fireEvent.input(screen.getByLabelText("Concept ID"), { target: { value: "god" } });
    await fireEvent.input(screen.getByLabelText("Preferred rendering(s)"), { target: { value: "இறைவன்" } });
    await fireEvent.click(screen.getByRole("button", { name: "Add rule" }));

    expect(await screen.findByText(/At least one approved, allowed, or rejected rendering is required/)).toBeInTheDocument();
  });

  it("keeps the Language QA rules and house style out of the Terminology pane", async () => {
    project.set(minimalProject());
    terminologyList.mockResolvedValue({ rules: [] });
    languageQaStatus.mockResolvedValue({ setting: "auto", packs: [{ language: "hi", name: "Hindi", pack: "hi-irv" }] });
    render(SettingsModal, { props: { initialPane: "terminology", onClose: vi.fn() } });
    await screen.findByText(/No terminology rules recorded/);
    expect(screen.queryByLabelText("Rules")).toBeNull();
    expect(screen.queryByRole("heading", { name: "House style" })).toBeNull();
    expect(housestyleList).not.toHaveBeenCalled();
  });

  it("loads the inline threshold, saves both values with Apply, and keeps the modal open", async () => {
    project.set(minimalProject());
    languageQaStatus.mockResolvedValue({ setting: "auto", packs: [] });
    getSettings.mockResolvedValue({
      provider: "openai", apiBaseUrl: "", model: "gpt-5.6", hasApiKey: false, reviewerMode: "basic",
      paratextNavigation: false, logosNavigation: false,
      languageQaInlinePrecision: 30, languageQaInlineConfidence: "medium",
    });
    setSettings.mockResolvedValue({ languageQaInlinePrecision: 60, languageQaInlineConfidence: "high" });
    const onClose = vi.fn();
    render(SettingsModal, { props: { initialPane: "languageQa", onClose } });

    const slider = (await screen.findByLabelText(/Minimum measured precision: 30%/)) as HTMLInputElement;
    expect(slider.value).toBe("30");
    const floor = screen.getByLabelText("Lowest rule confidence drawn") as HTMLSelectElement;
    expect(floor.value).toBe("medium");

    await fireEvent.input(slider, { target: { value: "60" } });
    await fireEvent.change(floor, { target: { value: "high" } });
    await fireEvent.click(screen.getByRole("button", { name: "Apply" }));

    await waitFor(() => expect(setSettings).toHaveBeenCalledWith(expect.objectContaining(
      { languageQaInlinePrecision: 60, languageQaInlineConfidence: "high", languageQaLearnedFixes: true })));
    expect(await screen.findByText("Applied.")).toBeInTheDocument();
    expect(screen.getByText(/confirmed less than 60% of the time are listed, not drawn/)).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("turns learned fixes off with the same Apply", async () => {
    getSettings.mockResolvedValue({
      provider: "openai", apiBaseUrl: "", model: "gpt-5.6", hasApiKey: false, reviewerMode: "basic",
      paratextNavigation: false, logosNavigation: false, languageQaLearnedFixes: true,
    });
    setSettings.mockResolvedValue({ languageQaLearnedFixes: false });
    render(SettingsModal, { props: { initialPane: "languageQa", onClose: vi.fn() } });
    const box = (await screen.findByRole("checkbox", { name: /Offer my earlier replacements/ })) as HTMLInputElement;
    expect(box.checked).toBe(true);
    await fireEvent.click(box);
    await fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(setSettings).toHaveBeenCalledWith(expect.objectContaining({ languageQaLearnedFixes: false })));
  });

  it("offers the threshold with no project open, and asks for one before the pack and house style", async () => {
    render(SettingsModal, { props: { initialPane: "languageQa", onClose: vi.fn() } });
    expect(await screen.findByLabelText(/Minimum measured precision: 0%/)).toBeInTheDocument();
    expect(screen.getByText("Every indic-qa finding is drawn.")).toBeInTheDocument();
    expect(screen.getByText(/Open a project to choose its Language QA rules/)).toBeInTheDocument();
    expect(languageQaStatus).not.toHaveBeenCalled();
    expect(housestyleList).not.toHaveBeenCalled();
  });

  it("sets a pack's reference Bible folder with Browse and saves it with Apply", async () => {
    project.set(minimalProject());
    languageQaStatus.mockResolvedValue({ setting: "auto", packs: [{ language: "hi", name: "Hindi", pack: "hi-irv" }] });
    pickFolder.mockResolvedValue("D:/OV Hindi");
    setSettings.mockResolvedValue({ languageQaReferenceDirs: { "hi-irv": "D:/OV Hindi" } });
    render(SettingsModal, { props: { initialPane: "languageQa", onClose: vi.fn() } });
    const field = (await screen.findByLabelText("Hindi (hi-irv)")) as HTMLInputElement;
    expect(field.value).toBe("");
    await fireEvent.click(screen.getByRole("button", { name: "Browse…" }));
    await waitFor(() => expect(field.value).toBe("D:/OV Hindi"));
    await fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(setSettings).toHaveBeenCalledWith(expect.objectContaining({
      languageQaReferenceDirs: { "hi-irv": "D:/OV Hindi" }, languageQaRelatedWords: true })));
  });
});
