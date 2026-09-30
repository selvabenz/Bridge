"""Project house style as data (layered-rules Phase 6.2-6.4).

A house-style entry is a human decision of kind 'housestyle' (workbench v5):

    {"scope": "word-in-book" | "word-in-project" | "rule-in-book" | "rule-in-project",
     "ruleId": "<pack/rule>" or "", "word": "<NFC form>" or "", "list": "properNouns" or "",
     "provenance": "curated" | "explicit" | "learned", "state": "active" | "removed" | "undone",
     "evidence": [{"chapter", "verse", "decisionId"}], "imported": bool}

What an entry may do is only ever to narrow or to rank (DECISIONS.md, "House
style is learned from decisions, and may only narrow or rank"):

- a word-scoped entry with a rule hides that rule's findings on that word;
- a rule-scoped entry hides a rule for the book;
- a list entry (`list: properNouns`) adds a word to a list the rule pack's
  abstains read;
- a learned preference moves a suggestion to the top.

Nothing here adds or widens a match, changes a severity, or draws a rule
inline. Removing an entry writes a new state on the same row; change_log keeps
every earlier state, and nothing is deleted.

A pack may bundle a seed (`language_packs/<pack>/housestyle-seed.json`):
curated entries every project starts with, read-only, never written into a
project's workbench. A project's own entry with the same key always wins, so
Remove works on a seed entry as on any other (bundled_seed, with_seed).

The learner (HouseStyleLearner) is a visible, deterministic aggregation over
Language QA decisions, run incrementally: each decision recomputes only the
(rule, word) pair it touched. Its thresholds are constants below, cited in
docs/LANGUAGE_QA_HOUSESTYLE.md.
"""
from __future__ import annotations

import functools
import hashlib
import json
import threading
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

SCOPES = ("word-in-book", "word-in-project", "rule-in-book", "rule-in-project")
PROVENANCES = ("curated", "explicit", "learned")
STATES = ("active", "removed", "undone")
LISTS = ("properNouns",)

# The learner's thresholds, in one place (docs/LANGUAGE_QA_HOUSESTYLE.md).
LEARN_IGNORES = 3              # ignores of one (rule, word) in a book, none Used since -> learned word-in-book
PROPOSE_PROJECT_BOOKS = 2      # the same pair learned in this many books -> propose word-in-project
PROPOSE_RULE_DECISIONS = 20    # a rule with at least this many decisions in the project...
PROPOSE_RULE_IGNORE_RATE = 0.8  # ...and this share ignored -> propose disabling it project-wide
PREFER_USES = 3                # the same suggestion Used this many times for a word -> rank it first
SUPPRESSING = ("ignored", "rejected")  # a false positive counts as an ignore for learning


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text or "")


def entry_key(scope: str, rule_id: str, word: str, list_name: str = "") -> str:
    return "|".join((scope, rule_id or "", nfc(word), list_name or ""))


def validate_entry(entry: dict[str, Any]) -> dict[str, Any]:
    scope = str(entry.get("scope") or "")
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {SCOPES}")
    provenance = str(entry.get("provenance") or "explicit")
    if provenance not in PROVENANCES:
        raise ValueError(f"provenance must be one of {PROVENANCES}")
    state = str(entry.get("state") or "active")
    if state not in STATES:
        raise ValueError(f"state must be one of {STATES}")
    list_name = str(entry.get("list") or "")
    if list_name and list_name not in LISTS:
        raise ValueError(f"list must be one of {LISTS}")
    rule_id, word = str(entry.get("ruleId") or ""), nfc(str(entry.get("word") or ""))
    if scope.startswith("word") and not word:
        raise ValueError("a word-scoped entry needs a word")
    if scope.startswith("word") and not (rule_id or list_name):
        raise ValueError("a word-scoped entry needs a rule or a list")
    if scope.startswith("rule") and not rule_id:
        raise ValueError("a rule-scoped entry needs a rule")
    evidence = [e for e in entry.get("evidence") or [] if isinstance(e, dict)]
    return {"scope": scope, "ruleId": rule_id, "word": word, "list": list_name,
            "provenance": provenance, "state": state, "evidence": evidence[:50],
            "imported": bool(entry.get("imported", False)),
            "key": entry_key(scope, rule_id, word, list_name)}


@functools.lru_cache(maxsize=8)
def _seed_from(path: Path) -> tuple[dict[str, Any], ...]:
    if not path.is_file():
        return ()
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    return tuple({**validate_entry({**entry, "provenance": "curated", "state": "active"}), "seed": True}
                 for entry in data.get("entries") or [] if isinstance(entry, dict))


def bundled_seed(pack: str) -> list[dict[str, Any]]:
    """The pack's bundled house-style seed, as validated entries marked `seed`."""
    from .language_packs.registry import packs_dir
    return [dict(entry) for entry in _seed_from(packs_dir() / pack / "housestyle-seed.json")]


def with_seed(entries: list[dict[str, Any]], seed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A project's entries over the pack's seed: an entry with a seed entry's
    key replaces it, whatever its state (so a removed seed entry stays removed)."""
    own = {entry.get("key") for entry in entries}
    return [entry for entry in seed if entry.get("key") not in own] + list(entries)


@dataclass
class HouseStyle:
    """The active entries of one book, as the scan applies them."""
    hidden_words: dict[str, set[str]] = field(default_factory=dict)   # ruleId -> words
    disabled_rules: set[str] = field(default_factory=set)
    lists: dict[str, frozenset] = field(default_factory=dict)
    preferences: dict[tuple[str, str], str] = field(default_factory=dict)

    def list_fingerprint(self) -> str:
        """The lists feed the rule pack inside the (cached) verse scan, so they
        join its cache key. Word and rule entries are applied after the scan."""
        return hashlib.sha1(json.dumps({k: sorted(v) for k, v in sorted(self.lists.items())},
                                       ensure_ascii=False).encode("utf-8")).hexdigest()

    def suppresses(self, finding: dict[str, Any]) -> bool:
        rule_id = str(finding.get("ruleId") or "")
        if rule_id in self.disabled_rules:
            return True
        return nfc(str(finding.get("originalText") or "")) in self.hidden_words.get(rule_id, ())

    def rank(self, finding: dict[str, Any]) -> dict[str, Any]:
        """A learned preference ranks an existing suggestion first. It never
        adds one: a preference for a text not offered changes nothing."""
        preferred = self.preferences.get((str(finding.get("ruleId") or ""), nfc(str(finding.get("originalText") or ""))))
        suggestions = list(finding.get("suggestions") or [])
        if not preferred or not any(nfc(s.get("text", "")) == preferred for s in suggestions):
            return finding
        first = [dict(s, source="housestyle") for s in suggestions if nfc(s.get("text", "")) == preferred][:1]
        rest = [s for s in suggestions if nfc(s.get("text", "")) != preferred]
        ranked = [dict(s, rank=i) for i, s in enumerate(first + rest, start=1)]
        return {**finding, "suggestions": ranked, "suggestedReplacement": ranked[0]["text"]}


def house_style(entries: list[dict[str, Any]], preferences: dict[tuple[str, str], str] | None = None) -> HouseStyle:
    style = HouseStyle(preferences=dict(preferences or {}))
    lists: dict[str, set[str]] = {name: set() for name in LISTS}
    for entry in entries:
        if entry.get("state", "active") != "active":
            continue
        scope, rule_id, word = entry.get("scope"), entry.get("ruleId") or "", nfc(entry.get("word") or "")
        if entry.get("list"):
            lists.setdefault(entry["list"], set()).add(word)
        elif str(scope).startswith("word") and rule_id and word:
            style.hidden_words.setdefault(rule_id, set()).add(word)
        elif str(scope).startswith("rule") and rule_id:
            style.disabled_rules.add(rule_id)
    style.lists = {f"housestyle.{name}": frozenset(words) for name, words in lists.items()}
    return style


def preferences_from(decisions: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    """(rule, word) -> the suggestion Used at least PREFER_USES times in this
    book's Language QA decisions (a Use records `chosenSuggestion`). Pure: the
    scan computes it from the decisions it already reads each pass."""
    uses: dict[tuple[str, str], Counter] = {}
    for row in decisions:
        issue = row.get("issue") if isinstance(row, dict) and isinstance(row.get("issue"), dict) else {}
        pair = _pair_of(issue) if issue.get("source") == "languageQa" else None
        if pair and row.get("decision") == "accepted" and issue.get("chosenSuggestion"):
            uses.setdefault(pair, Counter())[nfc(str(issue["chosenSuggestion"]))] += 1
    out = {}
    for pair, counter in uses.items():
        text, count = counter.most_common(1)[0]
        if count >= PREFER_USES:
            out[pair] = text
    return out


@dataclass
class _Pair:
    """One (rule, word) in one book: each finding id's latest decision."""
    decisions: dict[str, tuple[str, str, dict[str, Any]]] = field(default_factory=dict)  # id -> (decision, ts, where)

    def ignores_since_use(self) -> list[tuple[str, dict[str, Any]]]:
        uses = [ts for decision, ts, _ in self.decisions.values() if decision == "accepted"]
        last_use = max(uses) if uses else ""
        return sorted(((ts, where) for decision, ts, where in self.decisions.values()
                       if decision in SUPPRESSING and ts > last_use), key=lambda item: item[0])


def _pair_of(issue: dict[str, Any]) -> tuple[str, str] | None:
    rule_id, word = str(issue.get("ruleId") or ""), nfc(str(issue.get("originalText") or ""))
    return (rule_id, word) if rule_id and word else None


class HouseStyleLearner:
    """Derives house style from what translators decide, per book, in memory.
    Built from the book's decisions once, then updated one decision at a time."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._books: dict[str, dict[tuple[str, str], _Pair]] = {}
        self._uses: dict[str, dict[tuple[str, str], Counter]] = {}

    def _book(self, key: str, decisions: Callable[[], list[dict[str, Any]]]) -> dict[tuple[str, str], _Pair]:
        if key not in self._books:
            pairs: dict[tuple[str, str], _Pair] = {}
            uses: dict[tuple[str, str], Counter] = {}
            for row in decisions():
                self._apply(pairs, uses, row)
            self._books[key], self._uses[key] = pairs, uses
        return self._books[key]

    @staticmethod
    def _apply(pairs, uses, row: dict[str, Any]) -> tuple[str, str] | None:
        issue = row.get("issue") if isinstance(row.get("issue"), dict) else {}
        if issue.get("source") != "languageQa":
            return None
        pair = _pair_of(issue)
        if pair is None:
            return None
        where = {"chapter": str(row.get("chapter", "")), "verse": str(row.get("verse", "")),
                 "decisionId": str(row.get("issueKey", ""))}
        decision = str(row.get("decision") or "")
        pairs.setdefault(pair, _Pair()).decisions[where["decisionId"]] = (
            decision, str(row.get("modifiedTimestamp") or ""), where)
        if decision == "accepted" and issue.get("chosenSuggestion"):
            uses.setdefault(pair, Counter())[nfc(str(issue["chosenSuggestion"]))] += 1
        return pair

    def forget(self, key: str) -> None:
        with self._lock:
            self._books.pop(key, None)
            self._uses.pop(key, None)

    def observe(self, key: str, decisions: Callable[[], list[dict[str, Any]]], row: dict[str, Any],
                entries: list[dict[str, Any]]) -> dict[str, Any] | None:
        """One decision was just written. Returns the word-in-book entry to
        create when its pair has just reached LEARN_IGNORES, else None. A pair
        with any entry already (active, removed or undone) is never re-learned:
        Undo and Remove are the translator's word."""
        with self._lock:
            pairs = self._book(key, decisions)
            pair = self._apply(pairs, self._uses[key], row)
            if pair is None:
                return None
            streak = pairs[pair].ignores_since_use()
            if len(streak) < LEARN_IGNORES:
                return None
            if any(e.get("scope") in ("word-in-book", "word-in-project") and e.get("ruleId") == pair[0]
                   and nfc(e.get("word") or "") == pair[1] for e in entries):
                return None
            return {"scope": "word-in-book", "ruleId": pair[0], "word": pair[1], "provenance": "learned",
                    "evidence": [where for _, where in streak[-LEARN_IGNORES:]]}

    def preferences(self, key: str, decisions: Callable[[], list[dict[str, Any]]]) -> dict[tuple[str, str], str]:
        """(rule, word) -> the suggestion Used at least PREFER_USES times."""
        with self._lock:
            self._book(key, decisions)
            out = {}
            for pair, counter in self._uses[key].items():
                text, count = counter.most_common(1)[0]
                if count >= PREFER_USES:
                    out[pair] = text
            return out


NAME_MAX_DISTANCE = 1.0   # confusion-set distance from an approved name
# A short name (at most this many grapheme clusters) sits one cluster from many
# ordinary words -- சேத்து from காத்து, பூத்து, தைத்து -- so only a single typist
# confusion (the lexicon's MAX_DISTANCE) counts as a variant of it (2026-09-28).
NAME_SHORT_CLUSTERS = 3
NAME_SHORT_MAX_DISTANCE = 0.5
NAME_RARE_BOOK_MAX = 2    # a spelling this rare in the book, near an approved name


def name_findings(book: str, counts: dict[str, int], first_seen: dict[str, tuple], names: frozenset,
                  *, rule_fields: Any, suggestion: Any, rule_version: str,
                  corpus_count: Callable[[str], int] | None = None,
                  distance: Callable[[str, str], float]) -> list[dict[str, Any]]:
    """`name.minority-spelling` (layered-rules 6.2): a word rare in the book
    that is within NAME_MAX_DISTANCE of a proper noun the project approved
    (house-style list properNouns), suggested as that name. The approved list
    is the curated source; the names check only proposes candidates for it.
    `distance` is the pack's confusion-set distance (indic/confusion.py).

    A word the corpus uses commonly (`corpus_count` >= COMMON_MIN) is never a
    misspelt name: a short name sits one letter from ordinary words (சேத்து
    against காத்து, செத்து, சேர்த்து, பத்து, சொத்து), and those are words, not
    variants. The same rule the lexicon keeps: a common word is never marked wrong."""
    from .language_packs.lexicon import COMMON_MIN, deletion_keys
    from .language_packs.indic.confusion import clusters
    if not names:
        return []
    buckets: dict[str, set[str]] = {}
    for name in names:
        for key in {name, *deletion_keys(name)}:
            buckets.setdefault(key, set()).add(name)
    findings = []
    for word in sorted(counts):
        if word in names or counts[word] > NAME_RARE_BOOK_MAX or word not in first_seen:
            continue
        if corpus_count is not None and corpus_count(word) >= COMMON_MIN:
            continue
        near = set().union(*(buckets.get(k, set()) for k in {word, *deletion_keys(word)}))
        ranked = sorted((distance(word, name), -counts.get(name, 0), name) for name in near)
        ranked = [r for r in ranked if r[0] <= (NAME_SHORT_MAX_DISTANCE if len(clusters(r[2])) <= NAME_SHORT_CLUSTERS
                                                else NAME_MAX_DISTANCE)]
        if not ranked:
            continue
        chapter, verse, start, end, original, text_hash = first_seen[word]
        identity = f"{book}:name.minority-spelling:{word}"
        findings.append({
            "id": hashlib.sha1(identity.encode("utf-8", errors="surrogatepass")).hexdigest()[:20],
            "book": book, "chapter": chapter, "verse": verse, "rule": "name.minority-spelling",
            "severity": "medium", "start": start, "end": end, "originalText": original,
            "message": (f'"{word}" is close to the approved name "{ranked[0][2]}" (house style). '
                        f'Verify whether this is the same name spelt differently.'),
            "textHash": text_hash, "ruleVersion": rule_version, "status": "review-needed",
            **rule_fields("name.minority-spelling", [
                suggestion(name, "housestyle", f"Approved name; {-same}× in this book (distance {distance:g})")
                for distance, same, name in ranked[:5]]),
        })
    return findings


def name_suggestions(names_findings: list[dict[str, Any]], approved: frozenset) -> list[dict[str, Any]]:
    """Candidates for the approved-name list from the names check's cached
    output: its majority spellings, most frequent first, minus those already
    approved. Offered for a person to approve, never added by themselves."""
    import re
    found: dict[str, dict[str, Any]] = {}
    for finding in names_findings:
        majority = nfc(str(finding.get("suggested_replacement") or ""))
        if not majority or majority in approved:
            continue
        count = 0
        for item in finding.get("evidence") or []:
            if isinstance(item, dict) and item.get("label") == "More common spelling":
                match = re.search(r"— (\d+)x", str(item.get("value") or ""))
                count = int(match.group(1)) if match else 0
        entry = found.setdefault(majority, {"word": majority, "count": count, "variants": []})
        entry["count"] = max(entry["count"], count)
        entry["variants"].append(nfc(str(finding.get("original_text") or "")))
    return sorted(found.values(), key=lambda e: (-e["count"], e["word"]))[:200]


def project_proposals(books: list[tuple[str, list[dict[str, Any]], list[dict[str, Any]]]]) -> list[dict[str, Any]]:
    """What the learner proposes across a collection; never applied by itself.
    `books` is (bookId, house-style entries, Language QA decisions) per book.

    - a (rule, word) learned in PROPOSE_PROJECT_BOOKS books -> word-in-project;
    - a rule with PROPOSE_RULE_DECISIONS decisions or more, of which at least
      PROPOSE_RULE_IGNORE_RATE are ignores -> rule-in-project (and inline off)."""
    learned: dict[tuple[str, str], list[str]] = {}
    project_scoped: set[tuple[str, str]] = set()
    dismissed: set[str] = set()
    per_rule: dict[str, Counter] = {}
    for book_id, entries, decisions in books:
        for entry in entries:
            pair = (entry.get("ruleId") or "", nfc(entry.get("word") or ""))
            if entry.get("scope") == "word-in-book" and entry.get("provenance") == "learned" and entry.get("state") == "active":
                learned.setdefault(pair, []).append(book_id)
            if entry.get("scope") in ("word-in-project", "rule-in-project"):
                project_scoped.add(pair)
                if entry.get("state") != "active":
                    dismissed.add(entry_key(entry["scope"], pair[0], pair[1]))
        for row in decisions:
            issue = row.get("issue") if isinstance(row.get("issue"), dict) else {}
            if issue.get("source") == "languageQa" and issue.get("ruleId"):
                per_rule.setdefault(str(issue["ruleId"]), Counter())[
                    "ignored" if row.get("decision") in SUPPRESSING else "other"] += 1
    proposals = []
    for (rule_id, word), book_ids in sorted(learned.items()):
        if len(set(book_ids)) >= PROPOSE_PROJECT_BOOKS and (rule_id, word) not in project_scoped:
            proposals.append({"scope": "word-in-project", "ruleId": rule_id, "word": word,
                              "books": sorted(set(book_ids)),
                              "reason": f"learned as house style in {len(set(book_ids))} books"})
    for rule_id, counts in sorted(per_rule.items()):
        total = counts["ignored"] + counts["other"]
        if (total >= PROPOSE_RULE_DECISIONS and counts["ignored"] / total >= PROPOSE_RULE_IGNORE_RATE
                and (rule_id, "") not in project_scoped and entry_key("rule-in-project", rule_id, "") not in dismissed):
            proposals.append({"scope": "rule-in-project", "ruleId": rule_id, "word": "",
                              "reason": f"{counts['ignored']} of {total} decisions ignored it"})
    return proposals
