import { get, writable } from "svelte/store";
import { bridge } from "./api/bridgeClient";
import { nudgeLanguageQa } from "./languageQaInline";
import { project } from "./stores";
import { applyEngineChanges } from "./verseEditor";
import type { LanguageQaFinding, LanguageQaScope, ScopeFindResult } from "./types/languageQa";

/**
 * Scoped Language QA corrections (indic-qa's "Here / Chapter / Book"). The
 * engine writes one ordinary journalled edit per verse and groups them in a
 * batch; this module only asks first, shows what changed, and offers Undo.
 * A verse-scope Use keeps the existing single-verse path
 * (applyLanguageQaSuggestedFix): nothing to confirm.
 */

export interface ScopeDialogState {
  finding: LanguageQaFinding;
  suggestion: string;
  scope: Exclude<LanguageQaScope, "verse">;
  /** What the engine found; null while it is being asked. */
  found: ScopeFindResult | null;
  busy: boolean;
  error: string;
}

export interface ScopeNotice { text: string; error: boolean }

/** The open confirmation, or null. One at a time, mounted once in App. */
export const scopeDialog = writable<ScopeDialogState | null>(null);
/** The last batch applied in this session, for "Undo all"; null after undo. */
export const lastBatch = writable<{ batchId: string; count: number; label: string } | null>(null);
/** What happened, for the fixed notice beside Undo all. */
export const scopeNotice = writable<ScopeNotice | null>(null);

function projectPath(): string {
  const path = get(project)?.path;
  if (!path) throw new Error("No project is open.");
  return path;
}

function message(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

/** Ask the engine where the same finding is, then show the confirmation. */
export async function openScopeDialog(
  finding: LanguageQaFinding, suggestion: string, scope: Exclude<LanguageQaScope, "verse">,
): Promise<void> {
  const state: ScopeDialogState = { finding, suggestion, scope, found: null, busy: true, error: "" };
  scopeDialog.set(state);
  try {
    const found = await bridge.languageQaScopeFind(projectPath(), finding.chapter, finding.verse, finding.id, scope,
      suggestion);
    if (get(scopeDialog) !== state) return;  // closed, or another one opened meanwhile
    scopeDialog.set({ ...state, found, busy: false });
  } catch (cause) {
    if (get(scopeDialog) === state) scopeDialog.set({ ...state, busy: false, error: message(cause) });
  }
}

export function cancelScopeDialog(): void {
  scopeDialog.set(null);
}

/** Write it: the confirmed scope, as one batch. */
export async function confirmScopeDialog(): Promise<void> {
  const state = get(scopeDialog);
  if (!state || state.busy || !state.found) return;
  scopeDialog.set({ ...state, busy: true, error: "" });
  try {
    const { finding, suggestion, scope } = state;
    const result = await bridge.languageQaScopeAccept(projectPath(), finding.chapter, finding.verse, finding.id,
      scope, suggestion);
    applyEngineChanges(result.changed);
    scopeDialog.set(null);
    const skipped = result.skipped.length
      ? ` ${result.skipped.length} verse${result.skipped.length === 1 ? " was" : "s were"} changed after the check and left as ${result.skipped.length === 1 ? "it is" : "they are"}.` : "";
    const label = `“${finding.originalText}” → “${suggestion}” in ${result.changed.length} verse${result.changed.length === 1 ? "" : "s"}`;
    if (result.batchId) lastBatch.set({ batchId: result.batchId, count: result.changed.length, label });
    scopeNotice.set({ text: `Changed ${label}.${skipped}`, error: false });
  } catch (cause) {
    // The engine may have written some verses before failing (or a timeout):
    // refresh rather than guess, and say so.
    const current = get(scopeDialog);
    if (current) scopeDialog.set({ ...current, busy: false, error: `${message(cause)} Reopen the chapter to see what was saved.` });
    nudgeLanguageQa();
  }
}

/** Ignore the same finding across a scope. Writes no Scripture. */
export async function ignoreInScope(finding: LanguageQaFinding, scope: LanguageQaScope): Promise<void> {
  try {
    const result = await bridge.languageQaScopeIgnore(projectPath(), finding.chapter, finding.verse, finding.id, scope);
    nudgeLanguageQa();
    scopeNotice.set({ text: `Ignored ${result.count} occurrence${result.count === 1 ? "" : "s"}.`, error: false });
  } catch (cause) {
    scopeNotice.set({ text: message(cause), error: true });
  }
}

/** Undo the last batch: every verse back unless it was changed since. */
export async function undoLastBatch(): Promise<void> {
  const batch = get(lastBatch);
  if (!batch) return;
  try {
    const result = await bridge.languageQaBatchUndo(projectPath(), batch.batchId);
    applyEngineChanges(result.reverted);
    lastBatch.set(null);
    const left = result.conflicts.length
      ? ` ${result.conflicts.length} verse${result.conflicts.length === 1 ? " was" : "s were"} edited since and left as they are.` : "";
    scopeNotice.set({ text: `Undone: ${result.reverted.length} verse${result.reverted.length === 1 ? "" : "s"} restored.${left}`,
      error: false });
  } catch (cause) {
    scopeNotice.set({ text: message(cause), error: true });
  }
}

/** A different project: its batches are not this one's. */
export function resetScopedApply(): void {
  scopeDialog.set(null);
  lastBatch.set(null);
  scopeNotice.set(null);
}
