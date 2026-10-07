/** Wire types for engine/tc_ai_bridge/housestyle.py (layered-rules 6.3/6.4). */

export type HouseStyleScope = "word-in-book" | "word-in-project" | "rule-in-book" | "rule-in-project";
export type HouseStyleProvenance = "curated" | "explicit" | "learned";
export type HouseStyleState = "active" | "removed" | "undone";

export interface HouseStyleEvidence {
  chapter: string;
  verse: string;
  decisionId: string;
}

export interface HouseStyleEntryInput {
  scope: HouseStyleScope;
  ruleId?: string;
  word?: string;
  /** properNouns: names the pack's rules abstain on; projectWords: words the
   * project says are spelt right (Language QA's word list). */
  list?: "" | "properNouns" | "projectWords";
  provenance?: HouseStyleProvenance;
  state?: HouseStyleState;
  evidence?: HouseStyleEvidence[];
}

export interface HouseStyleEntry extends Required<Omit<HouseStyleEntryInput, "list">> {
  list: string;
  key: string;
  imported: boolean;
  modifiedTimestamp: string;
}

export interface HouseStyleProposal {
  scope: "word-in-project" | "rule-in-project";
  ruleId: string;
  word: string;
  reason: string;
  books?: string[];
}

export interface HouseStyleListResponse {
  entries: HouseStyleEntry[];
  /** The rule pack's bundled seed (read-only, never in the workbench); an own
   * entry with the same key replaces a seed entry. */
  seed?: HouseStyleEntry[];
  proposals: HouseStyleProposal[];
  thresholds: Record<string, number>;
}
