"""Alignment gaps: which source tokens have no target, and which target words
have no source (#137).

`_alignment_context` has computed this per verse since the alignment editor
shipped, but only ever returned the two *counts*, so the identities were
recomputed a second time client-side in `alignmentGroups.ts`
(`unrealizedSources` / `unaccountedTargets`) -- two definitions of "gap", in two
languages, free to drift. #117 then added a third, link-aware computation a few
lines below the first inside that same function and left the original dead
(`matched_top_ids` / `matched_bottom_ids` and the `gaps` built from them were
never read again; the later assignment won by ordering alone). This module is
the one definition they all call now.

A gap is **net of active cross-verse links** (#117): a source token realized in
another verse, and a target word accounting for another verse's source token,
are both spoken for even though translationCore alignment cannot say so. That is
the same rule `fullyAccounted` uses, and it is why a verse's gap counts can
reach zero while `status` still reads `partial` and `canComplete` stays false --
see `docs/ALIGNMENT.md`.

Deliberately free of any project or service dependency: it takes an
already-built inventory and the group views, so a chapter-wide scan can reuse
one `load_alignment_chapter` read instead of paying `_alignment_context`'s
per-verse history, chapter-status and target-text work N times over.
"""
from __future__ import annotations

from typing import Any, Collection, Iterable


def group_views(alignment: Any, inventory: Any) -> list[dict[str, Any]]:
    """The `{id, topIds, bottomIds}` view of each alignment group.

    Positional ids come from `make_inventory`, which regenerates them on every
    load; a token whose signature is not in the inventory is dropped rather than
    given an invented id, matching what `_alignment_context` has always done.
    """
    views: list[dict[str, Any]] = []
    for index, group in enumerate(alignment.alignments):
        views.append({
            "id": f"G{index + 1:03d}",
            "topIds": [
                inventory.top_sig_to_id[token.signature]
                for token in group.top_words
                if token.signature in inventory.top_sig_to_id
            ],
            "bottomIds": [
                inventory.bottom_sig_to_id[token.signature]
                for token in group.bottom_words
                if token.signature in inventory.bottom_sig_to_id
            ],
        })
    return views


def gap_ids(
    inventory: Any,
    groups: Iterable[dict[str, Any]],
    *,
    realized_ids: Collection[str] = (),
    accounted_ids: Collection[str] = (),
    null_source_ids: Collection[str] = (),
    null_target_ids: Collection[str] = (),
) -> tuple[list[str], list[str]]:
    """(unmatched source ids, unaccounted target ids), in inventory order.

    A source token counts as matched only when some group holds it *and* that
    group has at least one target word -- an empty group is the editor's way of
    saying "this source word has no counterpart yet", which is precisely a gap,
    not an alignment. A target token counts as matched when any group holds it;
    word-bank tokens are in the inventory but in no group.

    `realized_ids` / `accounted_ids` are this verse's active cross-verse link
    ends, already resolved to positional ids by the caller (they are stored as
    signatures and never as ids, #117). `null_source_ids` / `null_target_ids`
    are its active null decisions (#216): a token with no counterpart *for a
    named reason* is accounted for, not a gap. An empty tC group is still a
    gap -- only a decision row says "and that is correct".
    """
    groups = list(groups)
    matched_top = {token_id for group in groups if group["bottomIds"] for token_id in group["topIds"]}
    grouped_bottom = {token_id for group in groups for token_id in group["bottomIds"]}
    realized = set(realized_ids) | set(null_source_ids)
    accounted = set(accounted_ids) | set(null_target_ids)
    remaining_sources = [
        token_id for token_id in inventory.top_ids
        if token_id not in matched_top and token_id not in realized
    ]
    remaining_targets = [
        token_id for token_id in inventory.bottom_ids
        if token_id not in grouped_bottom and token_id not in accounted
    ]
    return remaining_sources, remaining_targets


def describe_tokens(inventory: Any, token_ids: Iterable[str], *, bottom: bool) -> list[dict[str, Any]]:
    """Name the gap tokens: positional id, the tC fields, and the signature.

    The signature is what identifies a token to the cross-verse link store, so
    it is carried here rather than left for the caller to rebuild -- a caller
    that reconstructs `word U+241F occurrence U+241F occurrences` by hand is one
    typo away from writing a link that resolves to nothing.
    """
    source = inventory.bottom_ids if bottom else inventory.top_ids
    described: list[dict[str, Any]] = []
    for token_id in token_ids:
        token = source.get(token_id)
        if token is None:
            continue
        described.append({
            "id": token_id, **token.to_dict(bottom=bottom), "signature": token.signature,
        })
    return described
