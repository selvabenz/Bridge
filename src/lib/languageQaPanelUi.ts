import { writable } from "svelte/store";

/**
 * Asks the mounted Language QA panel to open or close it: the status bar's
 * Language QA button, which stays when the floating launcher has hidden
 * itself. A counter, so every press is a change the panel sees.
 */
export const languageQaPanelToggle = writable(0);

export function toggleLanguageQaPanel(): void {
  languageQaPanelToggle.update((count) => count + 1);
}
