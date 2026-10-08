"""The verse window the automatic aligner shows a model, and how its answers
are read back (#214 window payload, #219 two passes).

One window is 2-5 consecutive verses of one chapter: the whole source and the
whole target, in reading order, so the model can see a word that moved to the
next verse. Every token gets an opaque handle (``S1``, ``T1``); the model never
sees Bridge's positional ``H001``/``T001`` ids or its signatures, and an id it
returns that Bridge did not send is an error, never a dropped row -- the same
closed-menu discipline as `cross_verse_ai_proposals.py`. A token that already
has a home a reviewer gave it is still shown, for context, marked
``alreadyAligned``; a claim on it is discarded on the way back.

The two passes ask the same question from opposite ends, and **each must
account for every word on both sides**:

* *source-first*: every source word, in order -- which target words carry it,
  or is it grammatical / implicit, or can it not be found -- and then every
  target word left over;
* *target-first*: every target word, in order -- which source words it renders,
  or is it grammar / explicitation, or does it add something -- and then every
  source word left over.

If only one pass ever spoke about target-only words, a Tamil grammar word could
never be agreed on, and no verse containing one could end fully automatic.

Nothing here talks to a network or a project: `call_model` is injected, as in
`cross_verse_ai_proposals.propose_with_model`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from .alignment_agreement import PassResult, TokenKey, valid_null_reason

SOURCE_FIRST = "source-first"
TARGET_FIRST = "target-first"
MAX_WINDOW_VERSES = 5

_COMMON = (
    "You are aligning a Bible translation to its original-language source, by MEANING, so a "
    "reviewer can see what the translation left out or added.\n\n"
    "The passage is given verse by verse in reading order. Translators often move meaning "
    "into a neighbouring verse, so a source word of one verse may be carried by a target word "
    "of another verse in this passage; link across verses whenever that is what happened.\n\n"
    "Rules:\n"
    "- Use ONLY the ids given. Never invent an id, a word or a verse.\n"
    "- Skip every word marked alreadyAligned; it is shown for context only.\n"
    "- A link is one source id and one target id. Return each edge separately: one source word "
    "may need several target words, and several source words may share one target word.\n"
    "- Never link a word that does not carry the meaning just to cover it. A word you cannot "
    "honestly place goes in `unplaced` with a short note; that is a correct answer, not a failure.\n"
    "- `nulls` is for a word that has no counterpart and needs none. A SOURCE word: reason "
    "GRAMMATICAL (its meaning is carried by an ending, a case, word order or another word's "
    "grammar) or IMPLICIT (clear from the context with no word of its own). A TARGET word: reason "
    "GRAMMATICAL (the target language requires it) or EXPLICITATION (it states what the source "
    "implies). Give a short note naming what carries it.\n"
    "- `confidence` is 0-100 for that one edge. `reason` and every note is one short sentence a "
    "reviewer who reads neither original language can check.\n"
    "- Every word that is not alreadyAligned must appear in your answer exactly once as a link "
    "end, a null or unplaced -- on both sides."
)

INSTRUCTIONS = {
    SOURCE_FIRST: _COMMON + (
        "\n\nWork SOURCE-FIRST: go through the source words in order and decide each one -- "
        "link it, mark it null, or mark it unplaced. Then go through the target words you have "
        "not linked and mark each null or unplaced."
    ),
    TARGET_FIRST: _COMMON + (
        "\n\nWork TARGET-FIRST: go through the target words in order and decide each one -- "
        "link it to the source words whose meaning it renders, mark it null, or mark it "
        "unplaced if it adds meaning the source does not have. Then go through the source words "
        "you have not linked and mark each null or unplaced."
    ),
}

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["links", "nulls", "unplaced", "review_notes"],
    "properties": {
        "links": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["source_id", "target_id", "confidence", "reason"],
            "properties": {
                "source_id": {"type": "string"}, "target_id": {"type": "string"},
                "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
                "reason": {"type": "string"},
            },
        }},
        "nulls": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["id", "reason", "note"],
            "properties": {
                "id": {"type": "string"},
                "reason": {"type": "string", "enum": ["IMPLICIT", "GRAMMATICAL", "EXPLICITATION"]},
                "note": {"type": "string"},
            },
        }},
        "unplaced": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["id", "note"],
            "properties": {"id": {"type": "string"}, "note": {"type": "string"}},
        }},
        "review_notes": {"type": "array", "items": {"type": "string"}},
    },
}


@dataclass
class WindowToken:
    key: TokenKey
    word: str
    token_id: str                 # this load's positional id (H001 / T001)
    strong: str = ""
    lemma: str = ""
    morph: str = ""
    homed: bool = False           # a home a reviewer gave it


@dataclass
class WindowVerse:
    verse: str
    translation: str
    sources: list[WindowToken] = field(default_factory=list)
    targets: list[WindowToken] = field(default_factory=list)


@dataclass
class Window:
    chapter: str
    verses: list[WindowVerse]
    payload: dict[str, Any]
    handles: dict[str, WindowToken]

    def tokens(self) -> list[TokenKey]:
        return [t.key for v in self.verses for t in (*v.sources, *v.targets)]

    def homed(self) -> set[TokenKey]:
        return {t.key for v in self.verses for t in (*v.sources, *v.targets) if t.homed}

    def by_key(self) -> dict[TokenKey, WindowToken]:
        return {t.key: t for v in self.verses for t in (*v.sources, *v.targets)}


def build_window(
    chapter: str, verses: list[WindowVerse], *,
    reference: str = "", glosses: dict[str, str] | None = None,
    source_language: str = "", target_language: str = "",
) -> Window:
    """The payload both passes receive, and the handle table that decodes them."""
    glosses = glosses or {}
    handles: dict[str, WindowToken] = {}
    rows = []
    s_count = t_count = 0
    for verse in verses:
        source_rows, target_rows = [], []
        for token in verse.sources:
            s_count += 1
            handle = f"S{s_count}"
            handles[handle] = token
            row: dict[str, Any] = {"id": handle, "word": token.word}
            if token.lemma:
                row["lemma"] = token.lemma
            if token.strong:
                row["strong"] = token.strong
            meaning = glosses.get(token.strong, "")
            if meaning:
                row["meaning"] = meaning
            if token.homed:
                row["alreadyAligned"] = True
            source_rows.append(row)
        for token in verse.targets:
            t_count += 1
            handle = f"T{t_count}"
            handles[handle] = token
            row = {"id": handle, "word": token.word}
            if token.homed:
                row["alreadyAligned"] = True
            target_rows.append(row)
        rows.append({
            "verse": verse.verse, "translation": verse.translation,
            "source": source_rows, "target": target_rows,
        })
    payload = {
        "passage": reference or f"chapter {chapter}",
        "sourceLanguage": source_language, "targetLanguage": target_language,
        "verses": rows,
    }
    return Window(chapter=chapter, verses=verses, payload=payload, handles=handles)


def decode(
    raw: Any, window: Window, direction: str, *, error: Callable[[str], Exception] = RuntimeError,
) -> PassResult:
    """Resolve one pass's handles back to token keys.

    An id Bridge never sent raises. A link with both ends on one side, a null
    whose reason does not belong to the word's side, and any claim on an
    ``alreadyAligned`` token are dropped with a note -- they are the model
    misusing a field, not inventing a word. A token named twice keeps its first
    claim of each kind.
    """
    if not isinstance(raw, dict):
        raise error("The model's alignment reply was not a JSON object.")
    result = PassResult(direction=direction)
    handles = window.handles

    def resolve(handle: Any) -> WindowToken:
        key = str(handle or "")
        if key not in handles:
            raise error(f"The model returned id {key!r}, which was not in the passage it was given.")
        return handles[key]

    for item in _list(raw, "links", error):
        source, target = resolve(item.get("source_id")), resolve(item.get("target_id"))
        if source.key.side != "source" or target.key.side != "target":
            result.notes.append(f"Dropped a link between two {source.key.side} words.")
            continue
        if source.homed or target.homed:
            continue
        pair = (source.key, target.key)
        if pair in result.edges:
            continue
        try:
            confidence = max(0, min(100, int(item.get("confidence", 0))))
        except (TypeError, ValueError):
            confidence = 0
        result.edges[pair] = {"confidence": confidence, "reason": str(item.get("reason") or "").strip()}
    for item in _list(raw, "nulls", error):
        token = resolve(item.get("id"))
        reason = str(item.get("reason") or "").strip().upper()
        if token.homed or token.key in result.nulls:
            continue
        if not valid_null_reason(token.key.side, reason):
            result.notes.append(f"Dropped {reason or 'an empty reason'} on the {token.key.side} word {token.word}.")
            continue
        result.nulls[token.key] = {"reason": reason, "note": str(item.get("note") or "").strip()}
    for item in _list(raw, "unplaced", error):
        token = resolve(item.get("id"))
        if not token.homed:
            result.unplaced.setdefault(token.key, str(item.get("note") or "").strip())
    for note in raw.get("review_notes") or ():
        if isinstance(note, str) and note.strip():
            result.notes.append(note.strip())
    return result


def ask(
    window: Window, call_model: Callable[[str, str, str], Any], direction: str, *,
    guidance: str = "", error: Callable[[str], Exception] = RuntimeError,
) -> PassResult:
    """One pass: `call_model(instructions, input_json, direction)` then decode."""
    instructions = INSTRUCTIONS[direction]
    if guidance:
        instructions += "\n\nLanguage guidance: " + guidance
    raw = call_model(instructions, json.dumps(window.payload, ensure_ascii=False), direction)
    return decode(raw, window, direction, error=error)


def _list(raw: dict[str, Any], key: str, error: Callable[[str], Exception]) -> list[dict[str, Any]]:
    value = raw.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise error(f"The model's alignment reply had a '{key}' field that was not a list of objects.")
    return value
