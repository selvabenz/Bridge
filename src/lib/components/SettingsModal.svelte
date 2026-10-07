<script lang="ts">
  import { onMount } from "svelte";
  import { bridge, type EngineInfo } from "../api/bridgeClient";
  import { nudgeLanguageQa } from "../languageQaInline";
  import { manualOverrideMode, navigationStatus, project, reviewerMode } from "../stores";
  import type { InlineConfidence, NavigationSyncState, SettingsData, TerminologyRule } from "../types/finding";
  import type { HouseStyleEntry, HouseStyleListResponse, HouseStyleProposal } from "../types/houseStyle";

  type Pane = "ai" | "quality" | "connections" | "resources" | "terminology" | "languageQa" | "security";

  export let onClose: () => void;
  export let initialPane: Pane = "ai";

  let activePane: Pane = initialPane;
  let loading = true;
  let saving = false;
  let saveMessage = "";

  let provider = "openai";
  let apiBaseUrl = "";
  let model = "gpt-5.6";
  let apiKey = "";
  let hasApiKey = false;
  let allowOverride = false;
  let paratextNavigation = false;
  let logosNavigation = false;
  let reviewerName = "";
  let reviewerNameUpdatedAt = "";
  let engineInfo: EngineInfo | null = null;
  // Settings > Language QA: which indic-qa findings are drawn in the text.
  // App-level (a reviewer's tolerance), applied by the engine per request.
  let inlinePrecision = 0;
  let inlineConfidence: InlineConfidence = "low";
  let learnedFixes = true;
  let inlineSaving = false;
  let inlineMessage = "";

  let terminologyRules: TerminologyRule[] = [];
  let terminologyLoaded = false;
  let terminologyLoading = false;
  let terminologySaving = false;
  let terminologyMessage = "";
  let newConceptId = "";
  let newPreferred = "";
  let newRejected = "";
  // Termbase v3 (layered-rules 6.1).
  let newAllowed = "";
  let newInflected = "";      // one line per rejected rendering: "rendering: form, form"
  let newPrefix = false;      // match the rejected renderings with case/plural endings too
  let editingConcept: string | null = null;  // editing an existing rule: saving replaces it

  const splitList = (value: string): string[] => value.split(",").map((s) => s.trim()).filter(Boolean);

  function parseInflected(value: string): Record<string, string[]> {
    const forms: Record<string, string[]> = {};
    for (const line of value.split("\n")) {
      const [rendering, rest] = line.split(":");
      if (rendering?.trim() && rest !== undefined && splitList(rest).length) forms[rendering.trim()] = splitList(rest);
    }
    return forms;
  }

  function editTerminologyRule(rule: TerminologyRule): void {
    editingConcept = rule.conceptId;
    newConceptId = rule.conceptId;
    newPreferred = rule.approvedRenderings.join(", ");
    newRejected = rule.rejectedRenderings.join(", ");
    newAllowed = (rule.allowedAlternatives ?? []).join(", ");
    newInflected = Object.entries(rule.inflectedForms ?? {}).map(([r, f]) => `${r}: ${f.join(", ")}`).join("\n");
    newPrefix = rule.matchMode === "prefix";
    terminologyConflict = null;
    terminologyMessage = "";
  }

  function resetTerminologyForm(): void {
    editingConcept = null;
    newConceptId = newPreferred = newRejected = newAllowed = newInflected = "";
    newPrefix = false;
  }

  const providerPresets: Record<string, string> = {
    openai: "",
    azure: "https://YOUR-RESOURCE.openai.azure.com/openai/deployments/YOUR-DEPLOYMENT",
    local: "http://localhost:11434/v1",
    custom: "",
  };

  onMount(load);

  async function load() {
    loading = true;
    try {
      const s: SettingsData = await bridge.getSettings();
      provider = s.provider || "openai";
      apiBaseUrl = s.apiBaseUrl || "";
      model = s.model || "gpt-5.6";
      hasApiKey = s.hasApiKey;
      allowOverride = s.reviewerMode === "advanced";
      paratextNavigation = s.paratextNavigation;
      logosNavigation = s.logosNavigation;
      reviewerName = s.reviewerName || "";
      reviewerNameUpdatedAt = s.reviewerNameUpdatedAt || "";
      inlinePrecision = s.languageQaInlinePrecision ?? 0;
      inlineConfidence = s.languageQaInlineConfidence ?? "low";
      learnedFixes = s.languageQaLearnedFixes ?? true;
      reviewerMode.set(s.reviewerMode);
      navigationStatus.set(await bridge.navigationStatus());
    } catch (e) {
      console.error("failed to load settings", e);
    } finally {
      loading = false;
    }
    // Separate from the block above: a slow or failed version lookup is not
    // critical enough to hold the rest of Settings behind "Loading…".
    try {
      engineInfo = await bridge.engineInfo();
    } catch (e) {
      console.error("failed to load engine info", e);
    }
  }

  function applyPreset() {
    if (provider in providerPresets && !apiBaseUrl) {
      apiBaseUrl = providerPresets[provider];
    }
  }

  async function save() {
    saving = true;
    saveMessage = "";
    try {
      const params: Record<string, unknown> = {
        provider, apiBaseUrl, model, reviewerMode: manualOverrideMode(allowOverride),
        paratextNavigation, logosNavigation, reviewerName,
      };
      if (apiKey.trim()) params.apiKey = apiKey.trim();
      const result = await bridge.setSettings(params);
      hasApiKey = result.hasApiKey;
      reviewerName = result.reviewerName || "";
      reviewerNameUpdatedAt = result.reviewerNameUpdatedAt || "";
      reviewerMode.set(result.reviewerMode);
      apiKey = "";
      const status = await bridge.navigationStatus();
      navigationStatus.set(status);
      const pending = activePane === "connections" ? unresolvedConnection(status) : "";
      if (pending) {
        // "Save & connect" also connects: hold the panel open so that result stays readable.
        saveMessage = pending;
        return;
      }
      onClose();
    } catch (e) {
      saveMessage = e instanceof Error ? e.message : String(e);
    } finally {
      saving = false;
    }
  }

  /** Message describing a navigation target the reviewer enabled that has not connected yet. */
  function unresolvedConnection(status: NavigationSyncState): string {
    if (status.ownerConflict) return "Saved. Another Bridge window owns desktop navigation.";
    const targets: Array<[string, boolean, NavigationSyncState["paratext"]]> = [
      ["Paratext", paratextNavigation, status.paratext],
      ["Logos", logosNavigation, status.logos],
    ];
    for (const [name, enabled, target] of targets) {
      if (!enabled || target.connected) continue;
      return target.error ? `Saved. ${name}: ${target.error}` : `Saved. Waiting for ${name}…`;
    }
    return "";
  }

  /** Its own save: the threshold is not part of the AI/quality form, and the
   *  panel and marks should follow at once (the engine rescans nothing). */
  async function saveInlineThreshold(): Promise<void> {
    inlineSaving = true;
    inlineMessage = "";
    try {
      const result = await bridge.setSettings({
        languageQaInlinePrecision: inlinePrecision, languageQaInlineConfidence: inlineConfidence,
        languageQaLearnedFixes: learnedFixes,
      });
      inlinePrecision = result.languageQaInlinePrecision ?? inlinePrecision;
      inlineConfidence = result.languageQaInlineConfidence ?? inlineConfidence;
      learnedFixes = result.languageQaLearnedFixes ?? learnedFixes;
      inlineMessage = "Applied.";
      nudgeLanguageQa();
    } catch (e) {
      inlineMessage = e instanceof Error ? e.message : String(e);
    } finally {
      inlineSaving = false;
    }
  }

  async function refreshConnections(): Promise<void> {
    saveMessage = "";
    try {
      navigationStatus.set(await bridge.navigationStatus());
    } catch (e) {
      saveMessage = e instanceof Error ? e.message : String(e);
    }
  }

  // -- house style (layered-rules 6.2-6.4) --
  let houseStyle: HouseStyleListResponse | null = null;
  let houseStyleMessage = "";
  let houseStyleBusy = false;
  let newProperNoun = "";
  let nameSuggestions: Array<{ word: string; count: number; variants: string[] }> = [];

  async function approveName(word: string): Promise<void> {
    await houseStyleAct(() => bridge.housestyleRecord({ scope: "word-in-book", list: "properNouns", word,
                                                         provenance: "curated" }));
    nameSuggestions = nameSuggestions.filter((s) => s.word !== word);
  }

  async function houseStyleAct(fn: () => Promise<HouseStyleListResponse | null | void>, done = ""): Promise<void> {
    if (houseStyleBusy) return;
    houseStyleBusy = true;
    houseStyleMessage = "";
    try {
      const result = await fn();
      if (result) houseStyle = result;
      if (done) houseStyleMessage = done;
    } catch (e) {
      houseStyleMessage = e instanceof Error ? e.message : String(e);
    } finally {
      houseStyleBusy = false;
    }
  }

  const acceptProposal = (p: HouseStyleProposal) => houseStyleAct(() => bridge.housestyleRecord({
    scope: p.scope, ruleId: p.ruleId, word: p.word, provenance: "learned", state: "active" }), "Applied to every book.");
  // Dismissing records the proposal as removed, so it is not proposed again.
  const dismissProposal = (p: HouseStyleProposal) => houseStyleAct(() => bridge.housestyleRecord({
    scope: p.scope, ruleId: p.ruleId, word: p.word, provenance: "learned", state: "removed" }));

  async function addProperNoun(): Promise<void> {
    const word = newProperNoun.trim();
    if (!word) return;
    await houseStyleAct(() => bridge.housestyleRecord({ scope: "word-in-book", list: "properNouns", word,
                                                         provenance: "curated" }));
    if (!houseStyleMessage) newProperNoun = "";
  }

  async function exportHouseStyle(): Promise<void> {
    const path = await bridge.pickSavePath(`${$project?.bookId ?? "book"}-house-style.json`);
    if (path) await houseStyleAct(async () => {
      const result = await bridge.housestyleExport(path);
      houseStyleMessage = `Exported ${result.count} entr${result.count === 1 ? "y" : "ies"}.`;
    });
  }

  async function importHouseStyle(): Promise<void> {
    const path = await bridge.pickJsonFile();
    if (path) await houseStyleAct(async () => {
      const result = await bridge.housestyleImport(path);
      houseStyleMessage = `Imported ${result.imported}; confirm each to make it yours.`;
      return result;
    });
  }

  function describeEntry(e: HouseStyleEntry): string {
    if (e.list) return `proper noun “${e.word}”`;
    return e.scope.startsWith("word") ? `“${e.word}” for ${e.ruleId}` : `${e.ruleId} off`;
  }

  async function loadTerminology(): Promise<void> {
    if (terminologyLoading) return;
    terminologyLoading = true;
    try {
      const result = await bridge.terminologyList();
      terminologyRules = result.rules;
      terminologyLoaded = true;
    } catch (e) {
      terminologyMessage = e instanceof Error ? e.message : String(e);
    } finally {
      terminologyLoading = false;
    }
  }

  // House style lives in the Language QA pane; loaded when that pane opens.
  let houseStyleLoaded = false;

  async function loadHouseStyle(): Promise<void> {
    houseStyleLoaded = true;
    houseStyle = await bridge.housestyleList().catch(() => null);
    nameSuggestions = (await bridge.housestyleNameSuggestions().catch(() => null))?.suggestions ?? [];
  }

  // Language QA's pack for this project (manifest language_qa.pack). Loaded
  // on its own so a failure never blocks the terminology pane.
  let lqaSetting = "auto";
  let lqaPacks: { language: string; name: string; pack: string }[] = [];
  let lqaLoaded = false;
  let lqaBusy = false;
  let lqaMessage = "";

  async function loadLanguageQaSetting(path: string): Promise<void> {
    lqaLoaded = true;
    try {
      const status = await bridge.languageQaStatus(path, 0, 0);
      lqaSetting = status.setting ?? "auto";
      lqaPacks = status.packs ?? [];
    } catch {
      lqaPacks = [];
    }
  }

  async function setLanguageQaPack(path: string, pack: string): Promise<void> {
    lqaBusy = true;
    lqaMessage = "";
    try {
      const status = await bridge.languageQaSetPack(path, pack);
      lqaSetting = status.setting ?? pack;
      lqaMessage = pack === "off" ? "Language QA now runs the common checks only."
        : pack === "auto" ? "Language QA chooses the pack from the project language."
        : `Language QA now runs ${lqaPacks.find((p) => p.pack === pack)?.name ?? pack} rules.`;
      nudgeLanguageQa();
    } catch (e) {
      lqaMessage = e instanceof Error ? e.message : String(e);
    } finally {
      lqaBusy = false;
    }
  }

  $: if (activePane === "languageQa" && $project && !lqaLoaded) {
    void loadLanguageQaSetting($project.path);
  }

  $: if (activePane === "languageQa" && $project && !houseStyleLoaded) {
    void loadHouseStyle();
  }

  // Rules are book-scoped, so this only fires once a project is actually
  // open -- otherwise the pane shows the same "open a project" message the
  // Resources pane already uses, rather than calling an RPC that would
  // reject with project_error.
  $: if (activePane === "terminology" && $project && !terminologyLoaded && !terminologyLoading) {
    void loadTerminology();
  }

  // A rule already recorded for the concept being added: the engine wrote
  // nothing and the pane asks before replacing it.
  let terminologyConflict: TerminologyRule | null = null;

  async function addTerminologyRule(overwrite = false): Promise<void> {
    const conceptId = newConceptId.trim();
    if (!conceptId) {
      terminologyMessage = "Concept ID is required.";
      return;
    }
    const approved = splitList(newPreferred);
    const rejected = splitList(newRejected);
    if (!approved.length && !rejected.length) {
      terminologyMessage = "Enter at least one preferred or rejected rendering.";
      return;
    }
    terminologySaving = true;
    terminologyMessage = "";
    terminologyConflict = null;
    try {
      // Editing the rule it was opened from replaces it; a new concept id that
      // collides with an existing rule still asks first.
      const replace = overwrite || editingConcept === conceptId;
      const result = await bridge.terminologyRecord(conceptId, approved, rejected, replace, {
        allowedAlternatives: splitList(newAllowed), inflectedForms: parseInflected(newInflected),
        matchMode: newPrefix ? "prefix" : "exact",
      });
      terminologyRules = result.rules;
      if (result.conflict) {
        terminologyConflict = result.conflict;
        return;
      }
      resetTerminologyForm();
    } catch (e) {
      terminologyMessage = e instanceof Error ? e.message : String(e);
    } finally {
      terminologySaving = false;
    }
  }
</script>

<div class="modal-overlay">
  <div class="settings-modal">
    <button class="close-btn" on:click={onClose} aria-label="Close settings">✕</button>
    <div class="settings-nav">
      <div class="nav-title">Settings</div>
      <button class="nav-item" class:active={activePane === "ai"} on:click={() => (activePane = "ai")}>AI provider</button>
      <button class="nav-item" class:active={activePane === "quality"} on:click={() => (activePane = "quality")}>Quality engine</button>
      <button class="nav-item" class:active={activePane === "connections"} on:click={() => (activePane = "connections")}>Connections</button>
      <button class="nav-item" class:active={activePane === "resources"} on:click={() => (activePane = "resources")}>Resources & licenses</button>
      <button class="nav-item" class:active={activePane === "terminology"} on:click={() => (activePane = "terminology")}>Terminology</button>
      <button class="nav-item" class:active={activePane === "languageQa"} on:click={() => (activePane = "languageQa")}>Language QA</button>
      <button class="nav-item" class:active={activePane === "security"} on:click={() => (activePane = "security")}>Security</button>
      {#if engineInfo}
        <div class="about-version" title="What this window is actually running, not what Windows says was installed">
          Bridge {engineInfo.bridgeVersion} · Schema v{engineInfo.companionSchemaVersion}
        </div>
      {/if}
    </div>

    <div class="settings-body">
      {#if loading}
        <p class="muted">Loading…</p>
      {:else if activePane === "ai"}
        <h3>AI provider</h3>
        <p class="desc">Used only for optional "Explain with AI." Core QA (Greek Room, local checks) never requires this and works fully offline.</p>

        <div class="field">
          <label for="provider">Provider</label>
          <select id="provider" bind:value={provider} on:change={applyPreset}>
            <option value="openai">OpenAI</option>
            <option value="azure">Azure OpenAI</option>
            <option value="local">Local / self-hosted (Ollama, LM Studio, vLLM)</option>
            <option value="custom">Custom</option>
          </select>
        </div>

        <div class="field">
          <label for="baseUrl">API base URL</label>
          <input id="baseUrl" type="text" bind:value={apiBaseUrl} placeholder="Leave blank to use OpenAI's default endpoint" />
          <div class="hint">Works with any OpenAI-Responses-API-compatible endpoint — Azure OpenAI, a self-hosted server, OpenRouter, etc. Blank means the standard OpenAI endpoint.</div>
        </div>

        <div class="field">
          <label for="model">Model</label>
          <input id="model" type="text" bind:value={model} placeholder="e.g. gpt-5.6, claude-sonnet-5, llama3" />
          <div class="hint">For tN/tW review, use a model with strong multilingual reasoning and structured-output support. Small/mini models may miss inflected target-language renderings or omit checks; Bridge keeps uncertain results pending for human review.</div>
        </div>

        <div class="field">
          <label for="apiKey">API key</label>
          <input id="apiKey" type="password" bind:value={apiKey} placeholder={hasApiKey ? "•••••••••••••••• (already set — enter to replace)" : "Enter your API key"} />
          <div class="hint">Stored locally via your OS's secure storage — never sent anywhere except the endpoint above.</div>
        </div>

        <div class="save-row">
          <button class="btn primary" on:click={save} disabled={saving}>{saving ? "Saving…" : "Save"}</button>
          {#if saveMessage}<span class="save-msg">{saveMessage}</span>{/if}
        </div>
      {:else if activePane === "quality"}
        <h3>Quality engine</h3>
        <p class="desc">Greek Room checks run fully offline and never leave this machine.</p>
        <div class="field">
          <label for="reviewerName">Reviewer name</label>
          <input id="reviewerName" type="text" bind:value={reviewerName} placeholder="Your name" />
          <div class="hint">
            Recorded against every decision, proposal, application, and verification you make — shown in each finding's history.
            {#if reviewerNameUpdatedAt}Last changed {new Date(reviewerNameUpdatedAt).toLocaleString()}.
            {:else}Never explicitly changed — seeded from your OS account.{/if}
          </div>
        </div>
        <div class="field">
          <div class="field-label">tN / tW selections</div>
          <label class="mode-option" class:selected={allowOverride}>
            <input type="checkbox" bind:checked={allowOverride} />
            <span><b>Allow manual override</b><small>Adds an editor for correcting the target text a check selected. AI still applies its own high-confidence, evidence-backed selections either way.</small></span>
          </label>
        </div>
        <div class="kv"><span>Offline QA engine (Greek Room)</span><span class="on">On</span></div>
        <div class="kv"><span>Local checks (tN / tW / alignment)</span><span class="on">On</span></div>
        <div class="save-row">
          <button class="btn primary" on:click={save} disabled={saving}>{saving ? "Saving…" : "Save"}</button>
          {#if saveMessage}<span class="save-msg">{saveMessage}</span>{/if}
        </div>
      {:else if activePane === "connections"}
        <h3>Verse navigation</h3>
        <p class="desc">Keep Bridge, Paratext, and Logos on the same verse. Navigation stays on this computer and never changes Scripture text.</p>
        {#if $navigationStatus.ownerConflict}
          <div class="connection-warning">Another Bridge window currently owns desktop navigation. Close it or turn sync off there before enabling this window.</div>
        {/if}
        <label class="connection-option" class:selected={paratextNavigation}>
          <input type="checkbox" bind:checked={paratextNavigation} />
          <span>
            <b>Paratext navigation</b>
            <small>Requires the AI Bridge Connector plugin and an active Scripture window in sync group A–E.</small>
          </span>
          <i class:connected={$navigationStatus.paratext.connected} class:error={Boolean($navigationStatus.paratext.error)} />
        </label>
        <div class="connection-detail">
          {#if !paratextNavigation}Off
          {:else if $navigationStatus.paratext.connected}Connected{#if $navigationStatus.paratext.project_name} · {$navigationStatus.paratext.project_name}{/if}{#if $navigationStatus.paratext.reference} · {$navigationStatus.paratext.reference}{/if}
          {:else if $navigationStatus.paratext.error}{$navigationStatus.paratext.error}
          {:else if $navigationStatus.paratext.checking}Checking…
          {:else}Waiting for Paratext{/if}
        </div>
        <label class="connection-option" class:selected={logosNavigation}>
          <input type="checkbox" bind:checked={logosNavigation} />
          <span>
            <b>Logos navigation</b>
            <small>Requires Logos Desktop for Windows and a navigable Bible panel.</small>
          </span>
          <i class:connected={$navigationStatus.logos.connected} class:error={Boolean($navigationStatus.logos.error)} />
        </label>
        <div class="connection-detail">
          {#if !logosNavigation}Off
          {:else if $navigationStatus.logos.connected}Connected{#if $navigationStatus.logos.reference} · {$navigationStatus.logos.reference}{/if}
          {:else if $navigationStatus.logos.error}{$navigationStatus.logos.error}
          {:else if $navigationStatus.logos.checking}Checking…
          {:else}Waiting for Logos{/if}
        </div>
        <div class="save-row">
          <button class="btn primary" on:click={save} disabled={saving}>{saving ? "Saving…" : "Save & connect"}</button>
          <button class="btn" on:click={refreshConnections} disabled={saving}>Refresh</button>
          {#if saveMessage}<span class="save-msg">{saveMessage}</span>{/if}
        </div>
      {:else if activePane === "resources"}
        <h3>Original-language resources</h3>
        <p class="desc">Bridge bundles versioned Hebrew and Greek source-token indexes for offline word alignment.</p>
        {#if $project?.originalLanguageResource?.available}
          {#if $project.originalLanguageResource.versionMismatch}
            <div class="resource-warning">This project was initialized with a different original-language version. Bridge has not replaced its existing source tokens.</div>
          {/if}
          <div class="kv"><span>Current book source</span><span>{$project.originalLanguageResource.resourceId?.toUpperCase()} {$project.originalLanguageResource.version}</span></div>
          <div class="kv"><span>Language</span><span>{$project.originalLanguageResource.languageId}</span></div>
          <div class="kv"><span>Publisher</span><span>{$project.originalLanguageResource.owner}</span></div>
          <div class="kv"><span>License</span><span>{$project.originalLanguageResource.license}</span></div>
          <div class="resource-note">{$project.originalLanguageResource.attribution}</div>
          <div class="resource-note">Source commit: {$project.originalLanguageResource.commit}</div>
          <div class="resource-note">The installer includes the upstream LICENSE.md and manifest.yaml plus Bridge's NOTICE.md and PROVENANCE.json with file hashes and transformation details.</div>
        {:else if $project}
          <p class="muted">{$project.originalLanguageResource?.message ?? "No original-language resource is available for this book."}</p>
        {:else}
          <p class="muted">Open a project to see whether it uses bundled UHB 3.0.0 or UGNT 0.34.</p>
        {/if}
        <h3 class="sub">Fonts</h3>
        <p class="desc">Bridge bundles typefaces for the Indian gateway-language scripts and for Hebrew and Greek source text, so Scripture renders correctly with no network access and no font installation.</p>
        <div class="kv"><span>Indian scripts</span><span>Noto Serif (Nastaliq for Urdu)</span></div>
        <div class="kv"><span>Hebrew</span><span>Ezra SIL 2.51</span></div>
        <div class="kv"><span>Greek</span><span>Gentium Plus</span></div>
        <div class="resource-note">All bundled fonts are licensed under the SIL Open Font License 1.1, with the MIT/X11 licence additionally covering Ezra SIL's Hebrew layout intelligence. The full licence texts ship alongside the font files in the installed app under <code>fonts/</code>.</div>
        <div class="resource-note">On Windows, Tamil and the other Indian scripts prefer Vijaya and Nirmala UI where they are installed. Those are Microsoft fonts and are not redistributed with Bridge; the bundled faces above are the fallback.</div>
      {:else if activePane === "terminology"}
        <h3>Terminology</h3>
        <p class="desc">Preferred and deprecated target-language renderings for this book. Language QA flags a rejected rendering inline as it's typed; nothing here changes Scripture text automatically.</p>
        {#if !$project}
          <p class="muted">Open a project to manage its terminology.</p>
        {:else}
          {#if terminologyLoading && !terminologyLoaded}
            <p class="muted">Loading…</p>
          {:else if terminologyRules.length}
            {#each terminologyRules as rule (rule.conceptId)}
              <div class="kv term-rule">
                <span>{rule.conceptId}</span>
                <span>
                  preferred: {rule.approvedRenderings.join(", ") || "none"} · rejected: {rule.rejectedRenderings.join(", ") || "none"}
                  {#if rule.allowedAlternatives?.length} · allowed: {rule.allowedAlternatives.join(", ")}{/if}
                  {#if rule.matchMode === "prefix"} · <em>also with case endings</em>{/if}
                  {#each Object.entries(rule.inflectedForms ?? {}) as [rendering, forms]}
                    <br /><small>{rendering} → {forms.join(", ")}</small>
                  {/each}
                  <button class="btn link" on:click={() => editTerminologyRule(rule)}>Edit</button>
                </span>
              </div>
            {/each}
          {:else}
            <p class="muted">No terminology rules recorded for this book yet.</p>
          {/if}
          <h3 class="sub">{editingConcept ? `Edit ${editingConcept}` : "Add a rule"}</h3>
          <div class="field">
            <label for="termConcept">Concept ID</label>
            <input id="termConcept" type="text" bind:value={newConceptId} placeholder="e.g. god" />
          </div>
          <div class="field">
            <label for="termPreferred">Preferred rendering(s)</label>
            <input id="termPreferred" type="text" bind:value={newPreferred} placeholder="Comma-separated, e.g. இறைவன்" />
          </div>
          <div class="field">
            <label for="termRejected">Rejected rendering(s)</label>
            <input id="termRejected" type="text" bind:value={newRejected} placeholder="Comma-separated, e.g. கடவுள்" />
          </div>
          <div class="field">
            <label for="termAllowed">Allowed alternative(s)</label>
            <input id="termAllowed" type="text" bind:value={newAllowed} placeholder="Comma-separated; offered as further suggestions" />
          </div>
          <div class="field">
            <label for="termInflected">Inflected forms of a rejected rendering</label>
            <textarea id="termInflected" rows="2" bind:value={newInflected} placeholder="One per line, e.g. கடவுள்: கடவுளை, கடவுளுக்கு"></textarea>
          </div>
          <label class="check">
            <input type="checkbox" bind:checked={newPrefix} />
            Also match the rejected renderings with case and plural endings (ஐ, க்கு, இல், கள் …). Such matches are marked medium confidence, for you to confirm.
          </label>
          <div class="save-row">
            <button class="btn primary" on:click={() => addTerminologyRule()} disabled={terminologySaving}>{terminologySaving ? "Saving…" : editingConcept ? "Save changes" : "Add rule"}</button>
            {#if editingConcept}<button class="btn" on:click={resetTerminologyForm} disabled={terminologySaving}>Cancel</button>{/if}
            {#if terminologyMessage}<span class="save-msg">{terminologyMessage}</span>{/if}
          </div>
          {#if terminologyConflict}
            <div class="term-conflict" role="alertdialog" aria-label="Replace existing terminology rule">
              <p>
                A rule for <strong>{terminologyConflict.conceptId}</strong> already exists
                (preferred: {terminologyConflict.approvedRenderings.join(", ") || "none"} · rejected:
                {terminologyConflict.rejectedRenderings.join(", ") || "none"}). Replace it?
              </p>
              <div class="save-row">
                <button class="btn primary" on:click={() => addTerminologyRule(true)} disabled={terminologySaving}>Replace</button>
                <button class="btn" on:click={() => (terminologyConflict = null)} disabled={terminologySaving}>Keep existing</button>
              </div>
            </div>
          {/if}
        {/if}
      {:else if activePane === "languageQa"}
        <h3>Language QA</h3>
        <p class="desc">Offline spelling, punctuation and consistency checks of the target text. Nothing here changes Scripture text.</p>
        <h3 class="sub">Drawn in the text</h3>
        <p class="desc">indic-qa findings (Punjabi, Malayalam, Hindi, Odia, and the Tamil OV layer) are underlined in the verse text unless this threshold hides them. Hidden ones stay in the Language QA panel. A rule reviewers have not labelled has no measured precision and always passes the slider. Rules reviewed as inline are always drawn.</p>
        <div class="field">
          <label for="lqa-precision">Minimum measured precision: {inlinePrecision}%</label>
          <input id="lqa-precision" type="range" min="0" max="100" step="5" bind:value={inlinePrecision}
            aria-valuetext={inlinePrecision === 0 ? "0: draw every finding" : `${inlinePrecision} percent`} />
          <div class="hint">{inlinePrecision === 0 ? "Every indic-qa finding is drawn." : `Rules reviewers confirmed less than ${inlinePrecision}% of the time are listed, not drawn.`}</div>
        </div>
        <div class="field">
          <label for="lqa-confidence">Lowest rule confidence drawn</label>
          <select id="lqa-confidence" bind:value={inlineConfidence}>
            <option value="low">Low and above (every rule)</option>
            <option value="medium">Medium and above</option>
            <option value="high">High only</option>
          </select>
          <div class="hint">A rule must pass both the precision and the confidence threshold to be drawn.</div>
        </div>
        <h3 class="sub">Learned fixes</h3>
        <label class="mode-option" class:selected={learnedFixes}>
          <input type="checkbox" bind:checked={learnedFixes} />
          <span><b>Offer my earlier replacements</b><small>When you replace one word in a verse, the same word elsewhere in the book gets a blue dotted underline offering your change. Manage them in Language QA › Dictionary.</small></span>
        </label>
        <div class="save-row">
          <button class="btn primary" on:click={saveInlineThreshold} disabled={inlineSaving}>{inlineSaving ? "Saving…" : "Apply"}</button>
          {#if inlineMessage}<span class="save-msg" role="status">{inlineMessage}</span>{/if}
        </div>
        {#if !$project}
          <p class="muted">Open a project to choose its Language QA rules and manage its house style.</p>
        {:else}
          {#if lqaPacks.length && $project}
            {@const projectPath = $project.path}
            <h3 class="sub">Language QA</h3>
            <p class="desc">Which language's rules check this book. Automatic uses the project language; a pack chosen here is used whatever the metadata says.</p>
            <div class="kv">
              <label for="lqa-pack">Rules</label>
              <select id="lqa-pack" value={lqaSetting} disabled={lqaBusy}
                on:change={(e) => setLanguageQaPack(projectPath, e.currentTarget.value)}>
                <option value="auto">Automatic (from the project language)</option>
                {#each lqaPacks as p (p.pack)}<option value={p.pack}>{p.name} ({p.pack})</option>{/each}
                <option value="off">Common checks only</option>
              </select>
            </div>
            {#if lqaMessage}<p class="save-msg" role="status">{lqaMessage}</p>{/if}
          {/if}
          <h3 class="sub">House style</h3>
          <p class="desc">What this project has decided is not a problem. Learned entries come from your Ignores ({houseStyle?.thresholds.learnIgnores ?? 3} of the same word, none Used); every entry only hides or ranks, never adds a check.</p>
          {#if houseStyle}
            {#each houseStyle.proposals as p (p.scope + p.ruleId + p.word)}
              <div class="kv hs-proposal">
                <span>Suggested</span>
                <span>{p.scope === "rule-in-project" ? `${p.ruleId} off in every book` : `“${p.word}” for ${p.ruleId} in every book`} — {p.reason}
                  <button class="btn link" on:click={() => acceptProposal(p)} disabled={houseStyleBusy}>Accept</button>
                  <button class="btn link" on:click={() => dismissProposal(p)} disabled={houseStyleBusy}>Dismiss</button>
                </span>
              </div>
            {/each}
            {#each houseStyle.seed ?? [] as e (e.key)}
              <div class="kv hs-entry hs-seed">
                <span><span class="badge curated">bundled</span></span>
                <span>{describeEntry(e)} · from the language pack's reviewed house style
                  <!-- Removing a bundled entry records this project's own entry for it, state removed. -->
                  <button class="btn link" on:click={() => houseStyleAct(() => bridge.housestyleRecord({ scope: e.scope,
                    ruleId: e.ruleId, word: e.word, list: e.list === "properNouns" ? "properNouns" : "",
                    provenance: "curated", state: "removed" }))} disabled={houseStyleBusy}>Remove</button>
                </span>
              </div>
            {/each}
            {#each houseStyle.entries.filter((e) => e.state === "active") as e (e.key)}
              <div class="kv hs-entry">
                <span><span class="badge {e.provenance}">{e.imported ? "imported" : e.provenance}</span></span>
                <span>{describeEntry(e)} · {e.scope.replace("-in-", " in ")} · {e.evidence.length} evidence
                  {#if e.imported}<button class="btn link" on:click={() => houseStyleAct(() => bridge.housestyleSetState(e.key, "active"))} disabled={houseStyleBusy}>Confirm</button>{/if}
                  <button class="btn link" on:click={() => houseStyleAct(() => bridge.housestyleSetState(e.key, "removed"))} disabled={houseStyleBusy}>Remove</button>
                </span>
              </div>
            {:else}
              <p class="muted">No project house style recorded yet.</p>
            {/each}
          {/if}
          {#if nameSuggestions.length}
            <details class="name-suggestions">
              <summary>Suggested names from the names check ({nameSuggestions.length})</summary>
              {#each nameSuggestions.slice(0, 30) as s (s.word)}
                <div class="kv">
                  <span>{s.word}</span>
                  <span>{s.count}× · also spelt {s.variants.join(", ")}
                    <button class="btn link" on:click={() => approveName(s.word)} disabled={houseStyleBusy}>Approve</button></span>
                </div>
              {/each}
            </details>
          {/if}
          <div class="field">
            <label for="hsProperNoun">Add a proper noun (never a வல்லினம் target)</label>
            <input id="hsProperNoun" type="text" bind:value={newProperNoun} placeholder="e.g. மோவாப்" />
          </div>
          <div class="save-row">
            <button class="btn" on:click={addProperNoun} disabled={houseStyleBusy || !newProperNoun.trim()}>Add name</button>
            <button class="btn" on:click={exportHouseStyle} disabled={houseStyleBusy}>Export…</button>
            <button class="btn" on:click={importHouseStyle} disabled={houseStyleBusy}>Import…</button>
            {#if houseStyleMessage}<span class="save-msg">{houseStyleMessage}</span>{/if}
          </div>
        {/if}
      {:else if activePane === "security"}
        <h3>Security & privacy</h3>
        <p class="desc">Project data and Greek Room findings never leave this machine unless you explicitly use AI explain.</p>
        <div class="kv"><span>API key storage</span><span>OS secure storage (DPAPI on Windows)</span></div>
      {/if}
    </div>
  </div>
</div>

<style>
  .modal-overlay { position: absolute; inset: 0; background: rgba(15, 20, 26, 0.45); display: flex; align-items: center; justify-content: center; z-index: 30; }
  .settings-modal { width: 640px; height: 480px; background: var(--surface); border-radius: 14px; display: flex; overflow: hidden; position: relative; }
  .close-btn { position: absolute; top: 10px; right: 10px; z-index: 2; width: 28px; height: 28px; border-radius: 6px; border: none; background: transparent; color: var(--text-2); font-size: var(--fs-lg); cursor: pointer; }
  .close-btn:hover { background: var(--surface-2); }
  .settings-nav { width: 170px; background: var(--surface-2); border-right: 1px solid var(--border); padding: 14px 8px; flex-shrink: 0; display: flex; flex-direction: column; }
  .nav-title { font-size: var(--fs-sm); font-weight: 700; color: var(--text); padding: 6px 10px 12px; }
  .about-version { margin-top: auto; padding: 8px 10px 2px; font-size: var(--fs-2xs); color: var(--text-3); border-top: 1px dashed var(--border); }
  .nav-item { display: block; width: 100%; text-align: left; padding: 8px 10px; border-radius: 7px; font-size: var(--fs-sm); font-weight: 600; color: var(--text-2); background: transparent; border: none; cursor: pointer; margin-bottom: 2px; }
  .nav-item:hover { background: var(--surface); }
  .nav-item.active { background: var(--accent-bg); color: var(--accent); }
  .settings-body { flex: 1; padding: 20px 24px; overflow-y: auto; }
  h3 { font-size: var(--fs-lg); margin: 0 0 4px; color: var(--text); }
  /* Second heading inside a pane -- h3's own margin is bottom-only. */
  h3.sub { margin-top: 24px; }
  code { font-family: ui-monospace, "SF Mono", Consolas, monospace; }
  .desc { font-size: var(--fs-xs); color: var(--text-2); margin: 0 0 16px; }
  .muted { font-size: var(--fs-sm); color: var(--text-3); }
  .field { margin-bottom: 14px; }
  .field > label, .field-label { display: block; font-size: var(--fs-xs); font-weight: 700; color: var(--text-2); margin-bottom: 5px; }
  .field input, .field select { width: 100%; height: 34px; border: 1px solid var(--border); border-radius: 6px; padding: 0 10px; font-size: var(--fs-sm); color: var(--text); background: var(--surface-2); box-sizing: border-box; }
  .hint { font-size: var(--fs-2xs); color: var(--text-3); margin-top: 4px; }
  .mode-option { display: flex; align-items: flex-start; gap: 9px; border: 1px solid var(--border); border-radius: 8px; padding: 9px 10px; margin-bottom: 7px; cursor: pointer; background: var(--surface-2); }
  .mode-option.selected { border-color: var(--accent); background: var(--accent-bg); }
  .mode-option input { width: auto; height: auto; margin: 2px 0 0; }
  .mode-option span { display: flex; flex-direction: column; gap: 2px; font-size: var(--fs-xs); color: var(--text); }
  .mode-option small { color: var(--text-2); line-height: 1.35; }
  .save-row { display: flex; align-items: center; gap: 10px; margin-top: 6px; }
  .btn { font-size: var(--fs-sm); font-weight: 600; padding: 8px 14px; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--surface); color: var(--text); cursor: pointer; }
  .btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
  .btn:disabled { opacity: 0.6; cursor: not-allowed; }
  .save-msg { font-size: var(--fs-xs); color: var(--success); }
  .term-conflict { margin-top: 10px; padding: 10px 12px; border: 1px solid var(--warning); border-radius: 6px; background: var(--warning-bg); font-size: var(--fs-sm); }
  .term-conflict p { margin: 0 0 8px; }
  .field textarea { width: 100%; border: 1px solid var(--border); border-radius: 6px; padding: 6px 10px; font-size: var(--fs-sm); color: var(--text); background: var(--surface-2); box-sizing: border-box; font-family: inherit; }
  .check { display: flex; gap: 8px; align-items: flex-start; font-size: var(--fs-xs); color: var(--text-2); margin: -4px 0 12px; }
  .btn.link { border: none; background: none; padding: 0 0 0 6px; text-decoration: underline; cursor: pointer; font-size: var(--fs-xs); }
  .term-rule small { color: var(--text-3); }
  .badge { font-size: var(--fs-2xs); padding: 1px 6px; border-radius: 999px; border: 1px solid var(--border); }
  .badge.learned { border-color: var(--accent); color: var(--accent); }
  .badge.curated { border-color: var(--success); color: var(--success); }
  .kv { display: flex; justify-content: space-between; font-size: var(--fs-sm); padding: 6px 0; border-bottom: 1px dashed var(--border); }
  .kv .on { color: var(--success); font-weight: 700; }
  .resource-note { font-size: var(--fs-2xs); line-height: 1.45; color: var(--text-3); margin-top: 10px; overflow-wrap: anywhere; }
  .resource-warning { font-size: var(--fs-xs); line-height: 1.4; color: var(--danger); background: var(--danger-bg); border: 1px solid var(--danger); border-radius: 6px; padding: 8px; margin-bottom: 10px; }
  .connection-warning { font-size: var(--fs-xs); line-height: 1.4; color: var(--danger); background: var(--danger-bg); border: 1px solid var(--danger); border-radius: 6px; padding: 8px; margin-bottom: 10px; }
  .connection-option { display: grid; grid-template-columns: auto 1fr auto; align-items: start; gap: 9px; border: 1px solid var(--border); border-radius: 8px; padding: 10px; cursor: pointer; background: var(--surface-2); }
  .connection-option.selected { border-color: var(--accent); background: var(--accent-bg); }
  .connection-option input { width: auto; height: auto; margin: 2px 0 0; }
  .connection-option span { display: flex; flex-direction: column; gap: 2px; font-size: var(--fs-xs); }
  .connection-option small { color: var(--text-2); line-height: 1.35; }
  .connection-option i { width: 9px; height: 9px; border-radius: 50%; margin-top: 3px; background: var(--border-strong); }
  .connection-option i.connected { background: var(--success); }
  .connection-option i.error { background: var(--danger); }
  .connection-detail { min-height: 26px; padding: 5px 8px 8px 31px; color: var(--text-3); font-size: var(--fs-2xs); line-height: 1.35; overflow-wrap: anywhere; }
</style>
