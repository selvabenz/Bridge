"""Data-driven rule kinds (docs/LANGUAGE_QA_PACKS.md).

A rule's `match.type` names its kind; this module validates the kind's
`match` block and evaluates it. `token-context` and `regex` are compiled by
the loader itself (they predate the kinds); every other kind is here.

Each kind runs at one stage of a scan:

- pair: one pair of adjacent words separated only by whitespace
  (`reduplication-allowlist`, `lexicon-lookup` check `known-split`). Within
  a pair these run before the token-context rules;
- verse: the verse's visible text (`regex`, `sign-sequence`, `mixed-script`),
  in pack order;
- book: one book's word counts, after every verse (`lexicon-lookup` checks
  `known-misspelling` and `rare-near-common`, `wordlist-variant`).
"""
from __future__ import annotations

import unicodedata
from typing import Any

import regex

from ..candidate import Candidate
from ..tokens import GRAPHEME, WORD

LEXICON_CHECKS = ("known-split", "known-misspelling", "rare-near-common")
KINDS = ("token-context", "regex", "sign-sequence", "mixed-script", "reduplication-allowlist",
         "lexicon-lookup", "wordlist-variant")
PAIR_KINDS = frozenset({"reduplication-allowlist"})
VERSE_KINDS = frozenset({"regex", "sign-sequence", "mixed-script"})
BOOK_KINDS = frozenset({"wordlist-variant"})

# lexicon-lookup rare-near-common and wordlist-variant thresholds: what a rule
# may set in its `match`, with the values the Tamil rules had in code.
RARE_NEAR_COMMON = {"rareBookMax": 2, "rareCorpusMax": 2, "ratioMin": 5, "commonMin": 6,
                    "maxDistance": 0.5, "minClusters": 3, "maxSuggestions": 5, "maxFindings": 200}
WORDLIST_VARIANT = {"minLength": 4, "rareMax": 2, "commonMin": 6, "ratioMin": 5, "maxFindings": 200,
                    "maxTerms": 20_000}


class KindError(ValueError):
    pass


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def stage(match_type: str, params: dict[str, Any]) -> str:
    if match_type == "token-context" or match_type in PAIR_KINDS:
        return "pair"
    if match_type in VERSE_KINDS:
        return "verse"
    if match_type == "lexicon-lookup":
        return "pair" if params.get("check") == "known-split" else "book"
    return "book"


def _text(match: dict[str, Any], key: str, where: str) -> str:
    value = match.get(key)
    if not isinstance(value, str) or not value:
        raise KindError(f"{where}: match.{key} must be a non-empty string")
    return value


def _numbers(match: dict[str, Any], defaults: dict[str, Any], where: str) -> dict[str, Any]:
    out = dict(defaults)
    for key, default in defaults.items():
        if key in match:
            value = match[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise KindError(f"{where}: match.{key} must be a non-negative number")
            out[key] = type(default)(value) if isinstance(default, int) and float(value).is_integer() else value
    return out


def compile_match(kind: str, match: dict[str, Any], where: str) -> dict[str, Any]:
    """The compiled parameters of one kind's `match` block. Unknown keys are
    refused, as everywhere in a pack."""
    allowed = {"type"}
    params: dict[str, Any] = {}
    if kind == "sign-sequence":
        # A dependent sign (vowel sign, virama, ...) must follow a base letter.
        allowed |= {"bases", "signs"}
        params = {"bases": frozenset(nfc(_text(match, "bases", where))),
                  "signs": frozenset(nfc(_text(match, "signs", where)))}
    elif kind == "mixed-script":
        allowed |= {"scripts"}
        scripts = match.get("scripts")
        if not isinstance(scripts, list) or len(scripts) < 2 or not all(isinstance(s, str) and s for s in scripts):
            raise KindError(f"{where}: match.scripts must list at least two Unicode script names")
        try:
            params = {"scripts": tuple(regex.compile(rf"\p{{Script={s}}}") for s in scripts)}
        except regex.error as exc:
            raise KindError(f"{where}: match.scripts: {exc}") from exc
    elif kind == "reduplication-allowlist":
        allowed |= {"allow"}
        allow = match.get("allow", [])
        if not isinstance(allow, list) or not all(isinstance(w, str) and w for w in allow):
            raise KindError(f"{where}: match.allow must be a list of words")
        params = {"allow": frozenset(nfc(w) for w in allow)}
    elif kind == "lexicon-lookup":
        allowed |= {"check", "unconfirmed", "suggestionRationale", *RARE_NEAR_COMMON}
        check = match.get("check")
        if check not in LEXICON_CHECKS:
            raise KindError(f"{where}: match.check must be one of {LEXICON_CHECKS}")
        params = {"check": check}
        if check == "rare-near-common":
            params.update(_numbers(match, RARE_NEAR_COMMON, where))
            params["suggestionRationale"] = _text(match, "suggestionRationale", where)
        if check == "known-misspelling":
            unconfirmed = match.get("unconfirmed")
            if not isinstance(unconfirmed, dict) or not all(
                    isinstance(unconfirmed.get(k), str) and unconfirmed.get(k) for k in ("message", "rationale")):
                raise KindError(f"{where}: match.unconfirmed needs a message and a rationale "
                                "(for a pair no human has confirmed)")
            params["unconfirmed"] = {"message": unconfirmed["message"], "rationale": unconfirmed["rationale"]}
    elif kind == "wordlist-variant":
        allowed |= set(WORDLIST_VARIANT)
        params = _numbers(match, WORDLIST_VARIANT, where)
    else:
        raise KindError(f"{where}: match.type must be one of {KINDS}")
    unknown = set(match) - allowed
    if unknown:
        raise KindError(f"{where}: unknown match keys {sorted(unknown)}")
    return params


# ---- evaluation --------------------------------------------------------------

def pair_candidate(rule: Any, pack: Any, prev_match: Any, next_match: Any) -> Candidate | None:
    """A pair kind's finding on one whitespace-separated word pair, if any."""
    # Every kind's message may use {span} and {fix}; a pair kind adds {prev} and {next}.
    prev_norm, next_norm = nfc(prev_match.group()), nfc(next_match.group())
    if rule.match_type == "reduplication-allowlist":
        if prev_norm != next_norm or next_norm in rule.params["allow"]:
            return None
        values = {"prev": prev_norm, "next": next_norm, "span": next_norm, "fix": ""}
        return Candidate(rule, next_match.start(), next_match.end(), None,
                         rule.message.format(**values), rule.rationale.format(**values))
    if rule.params.get("check") == "known-split":
        lexicon = pack.lexicon()
        joined = (getattr(lexicon, "splits", None) or {}).get(f"{prev_norm} {next_norm}") if lexicon else None
        if not joined:
            return None
        values = {"prev": prev_norm, "next": next_norm, "span": f"{prev_norm} {next_norm}", "fix": joined}
        return Candidate(rule, prev_match.start(), next_match.end(), joined,
                         rule.message.format(**values), rule.rationale.format(**values), source="lexicon")
    return None


def verse_candidates(rule: Any, visible: str) -> list[Candidate]:
    """A verse kind's findings over the visible text (regex is the loader's)."""
    out: list[Candidate] = []
    if rule.match_type == "sign-sequence":
        bases, signs = rule.params["bases"], rule.params["signs"]
        for cluster in GRAPHEME.finditer(visible):
            normalized = nfc(cluster.group())
            if any(c in signs and (i == 0 or normalized[i - 1] not in bases) for i, c in enumerate(normalized)):
                values = {"span": cluster.group(), "fix": ""}
                out.append(Candidate(rule, *cluster.span(), None, rule.message.format(**values),
                                     rule.rationale.format(**values)))
    elif rule.match_type == "mixed-script":
        for word in WORD.finditer(visible):
            if all(script.search(word.group()) for script in rule.params["scripts"]):
                values = {"span": word.group(), "fix": ""}
                out.append(Candidate(rule, *word.span(), None, rule.message.format(**values),
                                     rule.rationale.format(**values)))
    return out
