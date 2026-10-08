"""Two-pass agreement: what two independent readings of one window both say (#219).

The automatic aligner asks a model the same window twice: once **source-first**
("for each source word, which target words realise it?") and once
**target-first** ("for each target word, which source words does it render?").
This module compares the two answers and decides, token by token, what is
written without a click, what becomes a suggestion, and what is reported as a
possible omission or addition. It is pure: no project, no transport, no ids
that live longer than one load. Every key is a ``TokenKey`` -- side, verse and
tC signature -- so the result can be applied to whatever positional ids the
next load assigns.

**The gate is agreement, not confidence** (CLAUDE.md: "two methods agreeing is
the gate; a confidence threshold on one number is still not, anywhere"):

* an edge both passes give is *agreed*;
* a null both passes give, with the **identical** reason, is *agreed*. A source
  word one pass calls implicit and the other grammatical is a disagreement,
  because the two reasons mean different things to Stage 8 (#218);
* everything else one pass gave is a *suggestion*, with the votes attached;
* a token **neither** pass placed -- no edge and no null in either answer -- is
  an *unplaced* token, which the caller reports as a possible omission (source)
  or addition (target). It is never forced anywhere.

Consistency rules, so nothing contradictory is written:

* an agreed null is written only when neither pass also gave that token an
  edge; otherwise the null is a suggestion;
* an agreed edge whose token is also nulled by either pass is a suggestion;
* when the project has completed alignments, the offline corpus scorer
  (`cross_verse_proposals`, #138) is consulted for **cross-verse** edges: an
  agreed edge is blocked when the corpus's top candidate for that source word
  is a different target word, or the target word is contested. A corpus with
  nothing to say about that word does not block it. On a cold project
  (`no-completed-alignments`) there is no corpus and two-pass agreement is the
  whole gate; the result says ``corpus.checked: false``.

Two samples of one model are correlated, not independent: agreement lowers the
error rate, it does not bound it. Every number here is uncalibrated
(``AGREEMENT_CALIBRATION_VERSION``); the per-edge confidences are recorded as
evidence about the model and decide nothing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

AGREEMENT_CALIBRATION_VERSION = "two-pass-agreement-v1"

SOURCE_REASONS = frozenset({"IMPLICIT", "GRAMMATICAL"})
TARGET_REASONS = frozenset({"GRAMMATICAL", "EXPLICITATION"})


@dataclass(frozen=True, order=True)
class TokenKey:
    side: str      # "source" | "target"
    verse: str
    signature: str


@dataclass
class PassResult:
    """One pass, decoded to token keys. `edges` maps (source, target) to the
    edge's confidence (0-100) and reason; `nulls` maps a token to its reason
    and note; `unplaced` maps a token the model explicitly declined to place to
    its note."""

    direction: str
    edges: dict[tuple[TokenKey, TokenKey], dict[str, Any]] = field(default_factory=dict)
    nulls: dict[TokenKey, dict[str, Any]] = field(default_factory=dict)
    unplaced: dict[TokenKey, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def placed(self) -> set[TokenKey]:
        found: set[TokenKey] = set(self.nulls)
        for source, target in self.edges:
            found.add(source)
            found.add(target)
        return found

    def edge_tokens(self) -> set[TokenKey]:
        found: set[TokenKey] = set()
        for source, target in self.edges:
            found.add(source)
            found.add(target)
        return found


@dataclass
class CorpusCheck:
    """The offline scorer's view of cross-verse edges, from
    `cross_verse_proposals.propose`. ``best`` maps a source token to the target
    token it ranks first; ``contested`` holds target tokens two sources compete
    for. ``checked`` is False on a cold project."""

    checked: bool
    reason: str = ""
    best: dict[TokenKey, TokenKey] = field(default_factory=dict)
    contested: set[TokenKey] = field(default_factory=set)

    def objection(self, source: TokenKey, target: TokenKey) -> str:
        if not self.checked:
            return ""
        if target in self.contested:
            return "the corpus scorer has another source word competing for this target word"
        top = self.best.get(source)
        if top is not None and top != target:
            return "the corpus scorer's top candidate for this source word is a different target word"
        return ""


@dataclass
class Suggestion:
    """A claim only one pass made (or that agreement could not settle), offered
    to the reviewer with ✓ / ×. ``kind`` is ``link`` or ``null``."""

    kind: str
    status: str                  # UNCERTAIN | CORPUS_DISAGREES | CONFLICTS_WITH_HUMAN
    votes: dict[str, bool]
    source: TokenKey | None = None
    target: TokenKey | None = None
    token: TokenKey | None = None
    reason: str = ""
    note: str = ""
    confidence: int = 0

    def tokens(self) -> tuple[TokenKey, ...]:
        return tuple(t for t in (self.source, self.target, self.token) if t is not None)


@dataclass
class Agreement:
    agreed_edges: dict[tuple[TokenKey, TokenKey], dict[str, Any]] = field(default_factory=dict)
    agreed_nulls: dict[TokenKey, dict[str, Any]] = field(default_factory=dict)
    suggestions: list[Suggestion] = field(default_factory=list)
    unplaced: dict[TokenKey, list[str]] = field(default_factory=dict)
    corpus: dict[str, Any] = field(default_factory=dict)
    calibration_version: str = AGREEMENT_CALIBRATION_VERSION


def agree(
    source_first: PassResult,
    target_first: PassResult,
    *,
    tokens: Iterable[TokenKey],
    corpus: CorpusCheck | None = None,
    homed: Iterable[TokenKey] = (),
) -> Agreement:
    """Compare two passes over the window's ``tokens``.

    ``homed`` are tokens that already have a home a reviewer gave them (a tC
    group, a link or a null not written by an earlier automatic pass). Any claim
    touching one becomes a ``CONFLICTS_WITH_HUMAN`` suggestion -- the automatic
    pass never writes over human work -- and such a token is never reported as
    unplaced: it is already accounted for.
    """
    corpus = corpus or CorpusCheck(checked=False, reason="not-consulted")
    homed = set(homed)
    a, b = source_first, target_first
    result = Agreement(corpus={"checked": corpus.checked, "reason": corpus.reason})

    def votes(in_a: bool, in_b: bool) -> dict[str, bool]:
        return {a.direction: in_a, b.direction: in_b}

    nulled_anywhere = set(a.nulls) | set(b.nulls)
    edged_anywhere = a.edge_tokens() | b.edge_tokens()

    # --- edges ----------------------------------------------------------------
    for key in sorted(set(a.edges) | set(b.edges)):
        source, target = key
        in_a, in_b = key in a.edges, key in b.edges
        info_a, info_b = a.edges.get(key, {}), b.edges.get(key, {})
        confidence = min(
            int(info_a.get("confidence", 0) or 0) if in_a else 100,
            int(info_b.get("confidence", 0) or 0) if in_b else 100,
        )
        reason = _join(info_a.get("reason"), info_b.get("reason"))
        status = ""
        if source in homed or target in homed:
            # Only both passes together are worth putting in front of a
            # reviewer as "the model disagrees with your alignment"; a single
            # pass's claim on human work is noise. (`alignment_window.decode`
            # already drops claims on tokens it marked alreadyAligned.)
            if not (in_a and in_b):
                continue
            status = "CONFLICTS_WITH_HUMAN"
        elif not (in_a and in_b):
            status = "UNCERTAIN"
        elif source in nulled_anywhere or target in nulled_anywhere:
            status = "UNCERTAIN"
        elif source.verse != target.verse:
            objection = corpus.objection(source, target)
            if objection:
                status = "CORPUS_DISAGREES"
                reason = _join(reason, objection)
        if status:
            result.suggestions.append(Suggestion(
                kind="link", status=status, votes=votes(in_a, in_b),
                source=source, target=target, reason=reason, confidence=confidence,
            ))
        else:
            result.agreed_edges[key] = {"confidence": confidence, "reason": reason}

    # --- nulls ----------------------------------------------------------------
    for token in sorted(nulled_anywhere):
        in_a, in_b = token in a.nulls, token in b.nulls
        reason_a = str(a.nulls.get(token, {}).get("reason", "")) if in_a else ""
        reason_b = str(b.nulls.get(token, {}).get("reason", "")) if in_b else ""
        note = _join(a.nulls.get(token, {}).get("note"), b.nulls.get(token, {}).get("note"))
        if token in homed:
            status = "CONFLICTS_WITH_HUMAN"
        elif in_a and in_b and reason_a == reason_b and token not in edged_anywhere:
            result.agreed_nulls[token] = {"reason": reason_a, "note": note}
            continue
        else:
            status = "UNCERTAIN"
        # One suggestion per distinct reason given, each with its own votes.
        for reason in dict.fromkeys(r for r, given in ((reason_a, in_a), (reason_b, in_b)) if given and r):
            result.suggestions.append(Suggestion(
                kind="null", status=status,
                votes=votes(in_a and reason_a == reason, in_b and reason_b == reason),
                token=token, reason=reason, note=note,
            ))

    # --- unplaced -------------------------------------------------------------
    placed = a.placed() | b.placed()
    for token in tokens:
        if token in placed or token in homed:
            continue
        notes = [n for n in (a.unplaced.get(token), b.unplaced.get(token)) if n]
        result.unplaced[token] = notes
    return result


def _join(*parts: Any) -> str:
    seen: list[str] = []
    for part in parts:
        text = str(part or "").strip()
        if text and text not in seen:
            seen.append(text)
    return " · ".join(seen)


def valid_null_reason(side: str, reason: str) -> bool:
    return reason in (SOURCE_REASONS if side == "source" else TARGET_REASONS)
