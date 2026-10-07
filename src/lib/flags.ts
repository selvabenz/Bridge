import { get } from "svelte/store";
import { bridge } from "./api/bridgeClient";
import { currentChapter, flagsByVerse, verseKey } from "./stores";
import type { FlagInput, FlagStatus, LanguageQaFlag } from "./types/languageQa";

/**
 * Reviewer flags (indic-qa's ⚑): a question on a passage, kept in the
 * workbench. Loaded per chapter as chapters are shown; every change shows at
 * once and is put back if the engine refuses it.
 */

function keyOf(flag: Pick<LanguageQaFlag, "chapter" | "verse">): string {
  return verseKey(flag.chapter, flag.verse);
}

function message(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

/** Load the flags of the chapter on screen, and of every chapter shown after it. */
export function startFlagLoader(projectPath: string): () => void {
  let ticket = 0;
  const loaded = new Set<string>();
  const unsubscribe = currentChapter.subscribe((chapter) => {
    if (!chapter || loaded.has(chapter)) return;
    const mine = ++ticket;
    void bridge.languageQaFlagsList(projectPath, chapter).then(({ flags }) => {
      if (mine !== ticket && loaded.has(chapter)) return;
      loaded.add(chapter);
      flagsByVerse.update((all) => {
        const next = Object.fromEntries(Object.entries(all).filter(([key]) => !key.startsWith(`${chapter}:`)));
        for (const flag of flags) (next[keyOf(flag)] ??= []).push(flag);
        return next;
      });
    }).catch(() => { /* flags are an aid: a failed load leaves the chapter without them */ });
  });
  return unsubscribe;
}

export async function addFlag(projectPath: string, input: FlagInput): Promise<{ flag?: LanguageQaFlag; error: string; learned?: { old: string; new: string } }> {
  try {
    const { flag, learned } = await bridge.languageQaFlagAdd(projectPath, input);
    flagsByVerse.update((all) => ({ ...all, [keyOf(flag)]: [...(all[keyOf(flag)] ?? []), flag] }));
    return { flag, error: "", learned };
  } catch (cause) {
    return { error: message(cause) };
  }
}

function replace(flag: LanguageQaFlag, next: LanguageQaFlag | null): void {
  flagsByVerse.update((all) => {
    const key = keyOf(flag);
    const kept = (all[key] ?? []).filter((f) => f.flagId !== flag.flagId);
    return { ...all, [key]: next ? [...kept, next].sort((a, b) => a.start - b.start) : kept };
  });
}

/** Resolve or reopen. Shows at once; restored if the engine refuses. */
export async function setFlagStatus(projectPath: string, flag: LanguageQaFlag, status: Exclude<FlagStatus, "deleted">): Promise<string> {
  replace(flag, { ...flag, status });
  try {
    const { flag: saved } = await bridge.languageQaFlagUpdate(projectPath, flag.flagId, { status });
    replace(flag, saved);
    return "";
  } catch (cause) {
    replace(flag, flag);
    return message(cause);
  }
}

export async function deleteFlag(projectPath: string, flag: LanguageQaFlag): Promise<string> {
  replace(flag, null);
  try {
    await bridge.languageQaFlagDelete(projectPath, flag.flagId);
    return "";
  } catch (cause) {
    replace(flag, flag);
    return message(cause);
  }
}

/** The open and resolved flags of one verse, for the review panel. */
export function flagsOfVerse(chapter: string, verse: string): LanguageQaFlag[] {
  return get(flagsByVerse)[verseKey(chapter, verse)] ?? [];
}
