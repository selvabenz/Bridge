"""Stage 6B location evidence from completed translationCore Word Alignment.

V11-000a. Projects same-verse tC alignment groups into the same
`{"sourceTokenInstanceIds": [...], "targetTokenInstanceIds": [...]}` shape
`human_approved_lexical_precedents()` already returns, so
`SemanticLocationEngine._score_candidate` can score a WORD_ALIGNMENT
component the same way it scores HUMAN_PRECEDENT -- see
`docs/archive/V11-000_STAGE6B_ALIGNMENT_SPIKE.md` for the full investigation this
implements.

Read-only, in one direction only: nothing here ever writes to
`.apps/translationCore/alignmentData/` or the `tools/wordAlignment/`
completion markers. A token that cannot be matched to a stored Bridge
identity unambiguously drops that one alignment group from the evidence
(costing a NOT_LOCATED, today's status quo without this evidence) rather
than being guessed at -- a wrong group would confidently mislocate a
finding, which is worse than the gap this closes.

Scope: same-verse tC groups, plus (#119) Bridge's own cross-verse links from
`cross_verse_links.py` -- the human record that a source token of one verse
is realized in another verse's target text, which translationCore alignment
cannot hold. Both are projected into the same precedent shape, so a
cross-verse link scores at the same 0.65 WORD_ALIGNMENT weight and the
resulting relationship acquires CROSS_VERSE in `semantic_location.py` on its
own; no weight or threshold changes. A tC alignment group stored under a
verse-bridge key ("3-4") is still out of scope -- deciding which individual
verse each of its tokens belongs to is itself an unresolved-or-guess
problem, so those verses simply contribute no alignment evidence rather
than being split heuristically -- and so is a link whose end sits on a
bridged verse.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from .lexicon_resources import normalize_strong
from .models import TokenRef, VerseAlignment
from .original_language_resources import OriginalLanguageResource, source_tokens_for_verse
from .source_semantic_inventory import source_token_identity

# V11-000a review fix (F1, R1's exact-NFC matching) changed what this
# evidence finds relative to the v1 that shipped in e3ff0de: a project
# analysed under v1 holds LOCATION_RUN fingerprints/policyVersions carrying
# that string, and without a bump the post-fix engine would serve those
# evidence-dropped runs as cache hits -- same rule
# correction_verification.py's fingerprint() docstring states for Stage 6B
# changes generally.
# v3 (#119): cross-verse links join the evidence. A run fingerprinted under
# v2 never saw them, so it must not be served as a cache hit once they exist.
# v4 (#218): a cross-verse link *group* is one precedent (N sources, M
# targets) instead of N x M unrelated pairs, and null decisions are read as
# Stage 8 evidence; both change what a run can conclude.
ALIGNMENT_EVIDENCE_VERSION = "tc-word-alignment-v4"

# Same "verse bridge" recognition rule as
# original_language_resources.source_tokens_for_verse (e.g. "2-3").
_VERSE_BRIDGE = re.compile(r"\d+-\d+")


def _norm(value: str) -> str:
    """Casefolded NFC -- for the lemma reinforcement score only.

    Never for a word/occurrence comparison: `tokenize_target_text` and tC's
    aligner both count `occurrence`/`occurrences` over the exact NFC string,
    case included, so a casefolded word comparison matches tokens that were
    counted separately upstream, produces a spurious tie, and drops the
    group (found in review; see docs/archive/V11-000a_REVIEW_FIX_PROMPT.md F1).
    Lemma is not part of that (word, occurrence) join -- it only adds to the
    reinforcement score among candidates that already passed it -- so
    casefolding it is safe.
    """
    return unicodedata.normalize("NFC", str(value or "")).casefold().strip()


def resolve_source_token_id(
    resource: OriginalLanguageResource, book: str, chapter: str, verse: str, ref: TokenRef,
) -> str | None:
    """Match a tC `topWord` onto the pinned UHB/UGNT pack's own token identity.

    Matching is conservative, following
    `semantic_alignment_guard.alignment_top_ids_for_canonical_tokens`: exact
    NFC word (case-sensitive -- see `_norm`'s docstring) + occurrence is
    required, lemma/Strong's/morph only reinforce a tie among candidates that
    already satisfy it. `raw` tokens come from the pack itself, never from
    `ref` -- an NFD-normalized tC entry must not be fed into the identity
    hash directly, or it mints a different id than the one Stage 5 already
    stored. No casefold fallback: a tC word whose case differs from the pack
    means the text changed under the alignment (ALIGN_TARGET_MISMATCH,
    `local_checks.py:36-40`), and that must stay unresolved, not be papered
    over.
    """
    raw_tokens = source_tokens_for_verse(book, chapter, verse)
    if not raw_tokens:
        return None
    target_word = unicodedata.normalize("NFC", ref.word)
    target_occurrence = int(ref.occurrence or 1)
    if not target_word:
        return None
    candidates: list[tuple[int, int]] = []
    for index, raw in enumerate(raw_tokens):
        if unicodedata.normalize("NFC", str(raw.get("word") or "")) != target_word:
            continue
        if int(raw.get("occurrence") or 1) != target_occurrence:
            continue
        score = 10
        if ref.lemma and _norm(str(raw.get("lemma") or "")) == _norm(ref.lemma):
            score += 3
        raw_strong = normalize_strong(str(raw.get("strong") or ""), resource.language_id)
        ref_strong = normalize_strong(ref.strong, resource.language_id) if ref.strong else None
        if ref_strong and raw_strong and ref_strong == raw_strong:
            score += 3
        if ref.morph and str(raw.get("morph") or "") == ref.morph:
            score += 1
        candidates.append((score, index))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    best_score = candidates[0][0]
    best = [index for score, index in candidates if score == best_score]
    if len(best) != 1:
        return None
    index = best[0]
    _, instance_id, _ = source_token_identity(resource, book, chapter, verse, index, raw_tokens[index])
    return instance_id


def resolve_target_token_id(
    *, project_id: str, book: str, displayed_reference: str, text_revision: str,
    current_text: str, profile: str, ref: TokenRef,
) -> str | None:
    """Match a tC `bottomWord` onto a fresh retokenization of the current verse.

    tC's own tokenization and Bridge's `bridge-unicode-word-v1` genuinely
    disagree (whitespace + edge-trimmed punctuation vs. Unicode-word regex
    with punctuation as its own token), so a `bottomWord`'s
    (normalizedForm, occurrence) pair is a *candidate* bound to this exact
    `text_revision`, never an identity. Anything but exactly one match --
    including an occurrences-total mismatch, which signals the two
    tokenizations disagree about this verse -- returns unresolved.

    Case-sensitive, deliberately: `token["normalized"]` is NFC without
    casefold (`tokenize_target_text`, `passage_semantic_runtime.py`), and
    that is exactly the space `occurrence`/`occurrences` were counted in, so
    matching case-insensitively would tie together tokens the counting
    already told apart and drop the group (F1,
    docs/archive/V11-000a_REVIEW_FIX_PROMPT.md). No casefold fallback: a tC word
    whose case differs from the current text is `ALIGN_TARGET_MISMATCH`
    (`local_checks.py:36-40`), not a case Bridge should resolve anyway.
    """
    from .passage_semantic_runtime import target_token_identity, tokenize_target_text

    target_word = unicodedata.normalize("NFC", ref.word)
    if not target_word:
        return None
    tokens = tokenize_target_text(current_text, profile)
    exact = [
        token for token in tokens
        if token["normalized"] == target_word
        and token["occurrence"] == int(ref.occurrence or 1)
        and token["occurrences"] == int(ref.occurrences or 1)
    ]
    if len(exact) != 1:
        return None
    _, instance_id, _ = target_token_identity(
        project_id, book, displayed_reference, text_revision, profile, exact[0],
    )
    return instance_id


def _reference_chapter_verse(displayed_reference: str) -> tuple[str, str] | None:
    _, separator, location = displayed_reference.rpartition(" ")
    if not separator or ":" not in location:
        return None
    chapter, _, verse = location.partition(":")
    return chapter, verse


def _duplicated_signatures(alignment: VerseAlignment) -> tuple[set[str], set[str]]:
    """Signatures appearing in more than one group -- DUPLICATE_ACTIVE_TOKEN_MEMBERSHIP.

    Same check `local_checks.alignment_integrity_checks` already runs (there,
    to raise ALIGN_DUP_TOP/ALIGN_DUP_BOTTOM); reused here rather than
    re-walked, so a verse already flagged as ambiguous by that QA check never
    quietly becomes location evidence.
    """
    from collections import Counter
    top_counts = Counter(t.signature for t in alignment.all_top())
    bottom_counts = Counter(t.signature for t in alignment.aligned_bottom())
    return (
        {sig for sig, n in top_counts.items() if n > 1},
        {sig for sig, n in bottom_counts.items() if n > 1},
    )


def alignment_evidence_digest(runtime: Any) -> str:
    """Everything that can change what alignment_precedents_for_range finds:
    alignment content AND completion/invalid markers (`alignment_state_digest`
    -- not the content-only digest the legacy compatibility scan memoizes
    against, since `complete_alignment()` can flip completion state with no
    content byte changing, and that alone changes this evidence).

    Stage 6B's run fingerprint (semantic_location.py) must be sensitive to
    the exact same thing `PassageSemanticRuntime.synchronize_alignment_state`
    stales on, or a completion-only change would stale an old run while a
    fresh one recomputes an unchanged fingerprint -- the same
    (project, book, range, fingerprint) row colliding on insert.
    Deferred import to avoid a module cycle (passage_semantic_runtime
    already imports semantic_location, which imports this module).
    """
    from .passage_semantic_runtime import alignment_state_digest
    return alignment_state_digest(runtime.project)


def alignment_precedents_for_range(
    runtime: Any, chapter: str, verse: str, end_chapter: str = "", end_verse: str = "",
) -> list[dict[str, Any]]:
    """Completed same-verse tC alignment groups, projected as location precedents.

    One entry per group that resolved cleanly on both sides, in the same
    shape `human_approved_lexical_precedents()` returns. Never raises for
    verse-scoped data problems (a malformed legacy alignment file, a missing
    current-text revision, ...) -- like HUMAN_PRECEDENT, this is optional
    evidence, so a problem with it costs that verse's contribution, not the
    whole search (see the module docstring's SEARCH_INCOMPLETE reasoning in
    the V11-000a spike doc).
    """
    from .original_language_resources import resource_for_book
    from .passage_semantic_runtime import DEFAULT_TOKENIZER

    project = runtime.project
    book = runtime.book
    resource = resource_for_book(book)
    if resource is None:
        return []
    try:
        passage = runtime.rebuild_current_passage(chapter, verse, end_chapter, end_verse)
    except Exception:
        return []
    precedents: list[dict[str, Any]] = []
    for reference, current_text in passage["targetTextByDisplayedReference"].items():
        parsed = _reference_chapter_verse(reference)
        if parsed is None:
            continue
        ref_chapter, ref_verse = parsed
        if _VERSE_BRIDGE.fullmatch(ref_verse):
            # V11-000a review fix (F3): out of scope per the module
            # docstring above -- a bridged tC alignment group covers
            # multiple verses, and deciding which of this bridge's tokens
            # belongs to which individual verse is itself an
            # unresolved-or-guess problem, so it contributes nothing rather
            # than being split heuristically. Confirmed empirically that a
            # bridge's displayed reference reaches here as e.g. "PHP 1:2-3"
            # (rebuild_current_passage, not assumed).
            continue
        try:
            if project.word_alignment_state(ref_chapter, ref_verse) != "completed":
                continue
            alignment = project.load_verse_alignment(ref_chapter, ref_verse)
        except Exception:
            continue
        if not alignment.alignments:
            continue
        revision_row = runtime.repository.current_target_revision(runtime.project_id, book, reference)
        if revision_row is None:
            continue
        text_revision = revision_row["textRevision"]
        duplicated_top, duplicated_bottom = _duplicated_signatures(alignment)
        for group in alignment.alignments:
            if not group.top_words or not group.bottom_words:
                continue  # unaligned-top or empty-bottomWords: no target evidence either way
            if any(t.signature in duplicated_top for t in group.top_words):
                continue
            if any(t.signature in duplicated_bottom for t in group.bottom_words):
                continue
            source_ids = [
                resolve_source_token_id(resource, book, ref_chapter, ref_verse, top)
                for top in group.top_words
            ]
            if any(item is None for item in source_ids):
                continue
            target_ids = [
                resolve_target_token_id(
                    project_id=runtime.project_id, book=book, displayed_reference=reference,
                    text_revision=text_revision, current_text=current_text,
                    profile=DEFAULT_TOKENIZER, ref=bottom,
                )
                for bottom in group.bottom_words
            ]
            if any(item is None for item in target_ids):
                continue
            precedents.append({
                "sourceTokenInstanceIds": source_ids, "targetTokenInstanceIds": target_ids,
            })
    precedents.extend(_cross_verse_precedents(runtime, resource, passage["targetTextByDisplayedReference"]))
    return precedents


def _cross_verse_precedents(
    runtime: Any, resource: OriginalLanguageResource, passage_texts: dict[str, str],
) -> list[dict[str, Any]]:
    """Active Bridge cross-verse links touching the range, as precedents (#119).

    A link counts when either of its verses is in the range: the source verse
    supplies the pack identity of its token and the target verse supplies the
    revision-bound target token id, each resolved exactly the way the
    same-verse path resolves them (exact NFC word plus occurrence, no
    guessing). A target verse outside the range is looked up through the same
    passage rebuild the range itself went through, so its text revision is the
    one Stage 6A would use. Whether the resolved pair can actually score is
    then Stage 6B's business: if the target verse is not in this range's
    search spans the precedent simply matches no candidate. Invalid links,
    links on bridged verses, and any verse-scoped read problem contribute
    nothing rather than raising -- optional evidence, like the rest.
    """
    from .passage_semantic_runtime import DEFAULT_TOKENIZER

    project = runtime.project
    book = runtime.book
    try:
        links = project.cross_verse_links.active_links()
    except Exception:
        return []
    if not links:
        return []
    in_range: dict[tuple[str, str], str] = {}
    for reference in passage_texts:
        parsed = _reference_chapter_verse(reference)
        if parsed is not None:
            in_range[parsed] = reference
    verse_cache: dict[tuple[str, str], tuple[str, str, str] | None] = {}

    def verse_context(chapter: str, verse: str) -> tuple[str, str, str] | None:
        key = (chapter, verse)
        if key in verse_cache:
            return verse_cache[key]
        result: tuple[str, str, str] | None = None
        try:
            if key in in_range:
                reference = in_range[key]
                text = passage_texts[reference]
            else:
                single = runtime.rebuild_current_passage(chapter, verse)
                items = list(single["targetTextByDisplayedReference"].items())
                if len(items) == 1:
                    reference, text = items[0]
                else:
                    reference, text = "", ""
            if reference:
                row = runtime.repository.current_target_revision(runtime.project_id, book, reference)
                if row is not None:
                    result = (reference, text, row["textRevision"])
        except Exception:
            result = None
        verse_cache[key] = result
        return result

    # #218: a composite group (#217) is one realization, so it is one
    # precedent with all its sources and all its targets -- the shape a
    # same-verse N:M tC group already has. A row written before groups is a
    # group of one. Any member that does not resolve drops the whole group,
    # the same all-or-nothing rule the same-verse path applies.
    from .cross_verse_links import group_of
    groups: dict[str, list[dict[str, Any]]] = {}
    for link in links:
        groups.setdefault(group_of(link), []).append(link)

    precedents: list[dict[str, Any]] = []
    for members in groups.values():
        first_source = members[0].get("source") or {}
        first_target = members[0].get("target") or {}
        s_key = (str(first_source.get("chapter") or ""), str(first_source.get("verse") or ""))
        t_key = (str(first_target.get("chapter") or ""), str(first_target.get("verse") or ""))
        if s_key not in in_range and t_key not in in_range:
            continue
        if _VERSE_BRIDGE.fullmatch(s_key[1]) or _VERSE_BRIDGE.fullmatch(t_key[1]):
            continue
        context = verse_context(*t_key)
        if context is None:
            continue
        reference, text, text_revision = context
        sources = {str((m.get("source") or {}).get("signature")): m.get("source") or {} for m in members}
        targets = {str((m.get("target") or {}).get("signature")): m.get("target") or {} for m in members}
        source_ids = [
            resolve_source_token_id(resource, book, s_key[0], s_key[1], TokenRef.from_dict(end))
            for end in sources.values()
        ]
        target_ids = [
            resolve_target_token_id(
                project_id=runtime.project_id, book=book, displayed_reference=reference,
                text_revision=text_revision, current_text=text, profile=DEFAULT_TOKENIZER,
                ref=TokenRef.from_dict(end),
            )
            for end in targets.values()
        ]
        if any(item is None for item in source_ids) or any(item is None for item in target_ids):
            continue
        precedents.append({
            "sourceTokenInstanceIds": source_ids, "targetTokenInstanceIds": target_ids,
        })
    return precedents


#: Null-decision reasons Stage 8 accepts as explaining an absence (#218).
SOURCE_NULL_REASONS = frozenset({"GRAMMATICAL", "IMPLICIT"})
TARGET_NULL_REASONS = frozenset({"GRAMMATICAL", "EXPLICITATION"})


def null_precedents_for_range(
    runtime: Any, chapter: str, verse: str, end_chapter: str = "", end_verse: str = "",
) -> dict[str, dict[str, dict[str, Any]]]:
    """Active null decisions (#216) in the range, keyed by token instance id.

    ``{"source": {instanceId: {reason, origin, note}}, "target": {...}}``.
    Resolved exactly the way an alignment group's tokens are -- exact NFC word
    plus occurrence on the pinned pack for a source token, the current text
    revision for a target word -- and a token that does not resolve is left
    out rather than guessed at. Stage 8 reads this; Stage 6B does not, because
    a null has no target span to locate.

    Never raises: like every other alignment evidence, a problem costs the
    evidence, not the audit.
    """
    from .original_language_resources import resource_for_book
    from .passage_semantic_runtime import DEFAULT_TOKENIZER

    found: dict[str, dict[str, dict[str, Any]]] = {"source": {}, "target": {}}
    try:
        decisions = runtime.project.null_decisions.active_decisions()
    except Exception:
        return found
    if not decisions:
        return found
    resource = resource_for_book(runtime.book)
    if resource is None:
        return found
    try:
        passage = runtime.rebuild_current_passage(chapter, verse, end_chapter, end_verse)
    except Exception:
        return found
    in_range: dict[tuple[str, str], tuple[str, str]] = {}
    for reference, text in passage["targetTextByDisplayedReference"].items():
        parsed = _reference_chapter_verse(reference)
        if parsed is not None and not _VERSE_BRIDGE.fullmatch(parsed[1]):
            in_range[parsed] = (reference, text)
    revisions: dict[str, str | None] = {}
    for decision in decisions:
        key = (str(decision.get("chapter") or ""), str(decision.get("verse") or ""))
        if key not in in_range:
            continue
        side = decision.get("side")
        reason = str(decision.get("reason") or "")
        token = decision.get("token") or {}
        try:
            ref = TokenRef.from_dict(token)
            if side == "source" and reason in SOURCE_NULL_REASONS:
                instance_id = resolve_source_token_id(resource, runtime.book, key[0], key[1], ref)
            elif side == "target" and reason in TARGET_NULL_REASONS:
                reference, text = in_range[key]
                if reference not in revisions:
                    row = runtime.repository.current_target_revision(runtime.project_id, runtime.book, reference)
                    revisions[reference] = row["textRevision"] if row is not None else None
                if revisions[reference] is None:
                    continue
                instance_id = resolve_target_token_id(
                    project_id=runtime.project_id, book=runtime.book, displayed_reference=reference,
                    text_revision=revisions[reference], current_text=text, profile=DEFAULT_TOKENIZER, ref=ref,
                )
            else:
                continue
        except Exception:
            continue
        if instance_id:
            found[str(side)][instance_id] = {
                "reason": reason, "origin": decision.get("origin", "human"), "note": decision.get("note", ""),
            }
    return found
