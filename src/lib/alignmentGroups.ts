// Pure helpers over one verse's AlignmentContext (topTokens / bottomTokens /
// groups), shared by AlignmentModal.svelte and CrossVerseAlignmentModal.svelte
// so both surfaces agree on what "aligned", "unaligned" and "gap" mean.
import type { AlignmentContext, AlignmentGroupView, AlignmentToken } from "./types/finding";

type GroupsView = Pick<AlignmentContext, "groups" | "topTokens" | "bottomTokens">
  & Partial<Pick<AlignmentContext, "crossVerseAccountedIds" | "crossVerseRealizedIds" | "nullDecisions">>;

/** Ids of this verse's active null decisions on one side (#216): a word with
 *  no counterpart for a stated reason is accounted for, not a gap. */
export function nullDecidedIds(context: GroupsView, side: "source" | "target"): Set<string> {
  return new Set(
    (context.nullDecisions?.[side] ?? [])
      .filter((entry) => entry.state === "active" && entry.id)
      .map((entry) => entry.id as string),
  );
}

/** The group a target (bottom) token belongs to, if any. */
export function groupForTarget(context: GroupsView, bottomId: string): AlignmentGroupView | undefined {
  return context.groups.find((group) => group.bottomIds.includes(bottomId));
}

/** The group a source (top) token belongs to, if any. */
export function groupForSource(context: GroupsView, topId: string): AlignmentGroupView | undefined {
  return context.groups.find((group) => group.topIds.includes(topId));
}

/** Target tokens aligned to a source token, in group order. */
export function alignedTargetsFor(context: GroupsView, topId: string): AlignmentToken[] {
  const group = groupForSource(context, topId);
  if (!group) return [];
  return group.bottomIds
    .map((id) => context.bottomTokens.find((token) => token.id === id))
    .filter((token): token is AlignmentToken => Boolean(token));
}

/** Target tokens in no group -- the word bank as the editor shows it. */
export function unalignedTargets(context: GroupsView): AlignmentToken[] {
  return context.bottomTokens.filter((token) => !groupForTarget(context, token.id));
}

/** Source tokens with no target word: in no group, or in a group whose
 *  bottomIds is empty (tC keeps an empty group for an unaligned source). */
export function unmatchedSources(context: GroupsView): AlignmentToken[] {
  return context.topTokens.filter((token) => {
    const group = groupForSource(context, token.id);
    return !group || group.bottomIds.length === 0;
  });
}

/** Unaligned target tokens that no cross-verse link (#117) or null decision
 *  (#216) accounts for either: what the "not fully aligned" flag counts. */
export function unaccountedTargets(context: GroupsView): AlignmentToken[] {
  const accounted = new Set([...(context.crossVerseAccountedIds ?? []), ...nullDecidedIds(context, "target")]);
  return unalignedTargets(context).filter((token) => !accounted.has(token.id));
}

/** Unmatched source tokens that are not realized in another verse either. */
export function unrealizedSources(context: GroupsView): AlignmentToken[] {
  const realized = new Set([...(context.crossVerseRealizedIds ?? []), ...nullDecidedIds(context, "source")]);
  return unmatchedSources(context).filter((token) => !realized.has(token.id));
}

export interface GapCounts {
  sourceUnmatched: number;
  targetUnmatched: number;
}

/** Client-side mirror of the engine's per-verse `gaps` (alignment.getRange),
 *  net of cross-verse links like the engine's. */
export function gapCounts(context: GroupsView): GapCounts {
  return {
    sourceUnmatched: unrealizedSources(context).length,
    targetUnmatched: unaccountedTargets(context).length,
  };
}

/**
 * The full bottomIds to send with alignment.realign when `bottomId` is dropped
 * onto `topId`'s column. realign() replaces whatever group the top id belongs
 * to, so the words already there must be resent or they'd be bumped back to
 * the word bank. Returns null when the token is already in that column.
 */
export function bottomIdsAfterDrop(context: GroupsView, topId: string, bottomId: string): string[] | null {
  const destination = groupForSource(context, topId);
  if (destination?.bottomIds.includes(bottomId)) return null;
  return [...(destination?.bottomIds ?? []).filter((id) => id !== bottomId), bottomId];
}

/** "(1/2)" for a repeated word; callers check occurrences > 1 before rendering. */
export function occurrenceLabel(token: Pick<AlignmentToken, "occurrence" | "occurrences">): string {
  return `(${token.occurrence}/${token.occurrences})`;
}
