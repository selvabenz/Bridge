"""Profile packs: Language QA findings from indic-qa's checker (pa, ml, hi, or).

A *profile pack* is a pack whose `pack.json` says `"engine": "indic-qa"`. Its
rules are not compiled from JSON: they are indic-qa's own catalogue (the
profile module's `RULES`), each given a Bridge category, layer, severity,
confidence, revision and enabled flag by the pack's `rule_versions.json`.
It is an ordinary `RulePack`, so overrides, version stamps, ignore expiry,
inline lists and house style treat it like any other pack. None of its rules
runs in the per-verse scan: they are `stage="book"` with
`match_type="indic-qa"`, which no pair, verse or book kind dispatches. The
findings come from `profile_findings`, which the pass calls once per book,
next to the lexicon audit.

How a book is checked:
- Bridge's chapter JSON is turned into indic-qa `Line`/`Book` objects
  (`build_book`): one line per physical line of each verse's visible text
  (`lift_inline_usfm`), so adjacency never crosses a poetry line. indic-qa's
  own USFM parser is never called (CLAUDE.md gotcha 14).
- The checker needs whole-IRV word counts, which indic-qa gets by indexing 66
  books at start-up. The pack ships that state instead (`irv_state.json.gz`,
  built by scripts/build_indic_qa_irv_state.py through this same path). On
  load it is restored, and each pass re-indexes the open book from its live
  text, which replaces that book's share exactly (`Checker.index_book`
  subtracts a book's previous counts).
- Every item the checker reports is mapped to a Bridge finding: a token with
  a rule, a lead (`sandhi` list: consistency, agreement, grammar), or a
  warning. Spans are offsets in the visible line, translated back to raw code
  points; one that would cross lifted markup is dropped and counted.

Findings are recomputed on every pass and never written to
`language_qa_cache`: they depend on the whole book, not one verse. The
dictionaries are read only. Nothing here writes Scripture.
"""
from __future__ import annotations

import gc
import gzip
import hashlib
import json
import threading
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import indic_qa_vendor
from .loader import PackError, RulePack, Rule

ENGINE = "indic-qa"
RULE_VERSIONS = "rule_versions.json"
IRV_STATE = "irv_state.json.gz"
IRV_STATE_FORMAT = 1
# Token statuses that are findings. Every other status (ok, irv_ok,
# inflected_ok, compound, sandhi_ok, ignored, learned) is a word the checker
# accepted.
FINDING_STATUSES = frozenset({"malformed", "suspect", "archaic", "rare_near_common", "unknown"})
CATEGORIES = ("typo", "word-joining", "punctuation", "unicode", "spacing", "consistency", "grammar")
LAYERS = ("pattern", "lexicon", "integrity")
SEVERITIES = ("high", "medium", "low")
CONFIDENCES = ("high", "medium", "low")
ENTRY_KEYS = {"revision", "category", "layer", "severity", "confidence", "enabled", "inline", "note"}


def is_profile_pack(meta: dict[str, Any]) -> bool:
    return meta.get("engine") == ENGINE


# -- the rule catalogue --------------------------------------------------------

def default_entry(rule_id: str, group: str, on_by_default: bool) -> dict[str, Any]:
    """The first rule_versions.json entry for an indic-qa rule, from its
    group. Written once by scripts/build_indic_qa_rule_versions.py; the file,
    not this function, is what the pack runs on, so a reviewed entry is never
    regenerated. Phase 1: nothing is inline (the human gate needs labels), and
    nothing is high severity with high confidence (that blocks export)."""
    tail = rule_id.split(".", 1)[1]
    category, layer, severity, confidence = {
        "Encoding": ("unicode", "integrity", "medium", "medium"),
        "Shape": ("unicode", "integrity", "medium", "medium"),
        "Punctuation": ("punctuation", "integrity", "medium", "medium"),
        "Lexicon": ("typo", "lexicon", "medium", "low"),
        "Consistency": ("consistency", "lexicon", "low", "low"),
        "Grammar": ("grammar", "pattern", "low", "low"),
        "Style": ("typo", "pattern", "low", "low"),
    }.get(group, ("typo", "pattern", "low", "low"))
    # First match wins.
    if any(k in tail for k in ("reduplication", "repeated-word", "postposition-joining")):
        category = "word-joining"
    elif "space" in tail:
        category = "spacing"
    elif group == "Shape" and tail.endswith(("single-letter", "long-token")):
        category, layer = "typo", "pattern"
    elif any(k in tail for k in ("hyphen", "dash", "quote", "ellipsis")):
        category = "punctuation"
    if tail in {"lex.known-misspelling", "style.divine-names", "style.rejected-names"}:
        confidence = "high"
    entry = {"revision": 1, "category": category, "layer": layer, "severity": severity,
             "confidence": confidence, "enabled": bool(on_by_default), "inline": False}
    if tail == "lex.unknown":
        # Not verifiable is not an error: indic-qa itself shows these grey (the
        # Hindi review found 22 of 25 correct). Off until counts say otherwise.
        entry["enabled"] = False
        entry["note"] = "off in Bridge: a word missing from the dictionary is not a finding"
    return entry


def _rules(profile: Any, entries: dict[str, Any], pack_name: str) -> list[Rule]:
    catalogue = profile.RULES
    missing = sorted(set(catalogue) - set(entries))
    extra = sorted(set(entries) - set(catalogue))
    if missing or extra:
        raise PackError(f"{pack_name}/{RULE_VERSIONS}: does not match the profile's RULES "
                        f"(missing {missing}, unknown {extra}); run scripts/build_indic_qa_rule_versions.py")
    rules = []
    for rule_id, (group, label, _inline_upstream, _on) in catalogue.items():
        entry = entries[rule_id]
        where = f"{pack_name}/{RULE_VERSIONS}: {rule_id}"
        if set(entry) - ENTRY_KEYS:
            raise PackError(f"{where}: unknown keys {sorted(set(entry) - ENTRY_KEYS)}")
        for key, allowed in (("category", CATEGORIES), ("layer", LAYERS), ("severity", SEVERITIES),
                             ("confidence", CONFIDENCES)):
            if entry.get(key) not in allowed:
                raise PackError(f"{where}: {key} must be one of {allowed}")
        if not isinstance(entry.get("revision"), int) or entry["revision"] < 1:
            raise PackError(f"{where}: revision must be a positive integer")
        if entry["severity"] == "high" and entry["confidence"] == "high":
            raise PackError(f"{where}: high severity with high confidence blocks export; not for an "
                            f"indic-qa rule without a DECISIONS entry")
        rules.append(Rule(
            id=rule_id, version=entry["revision"], legacy_id=None, enabled=bool(entry.get("enabled", True)),
            category=entry["category"], layer=entry["layer"], severity=entry["severity"],
            confidence=entry["confidence"], inline=bool(entry.get("inline", False)), message=label,
            rationale=f"indic-qa {group.lower()} rule", match_type=ENGINE, stage="book",
            params={"group": group},
            source={"provenance": f"indic-qa qa_app/langs/{profile.__name__.rsplit('.', 1)[1]}.py RULES"
                                  + (f"; {entry['note']}" if entry.get("note") else "")
                                  + ("" if entry.get("enabled", True) else "; DISABLED in Bridge")},
        ))
    return rules


def build_pack(meta: dict[str, Any], directory: Path) -> RulePack:
    """The RulePack for a profile pack directory (pack.json already read)."""
    name = str(meta.get("pack") or directory.name)
    code = str(meta.get("profile") or "")
    if code not in indic_qa_vendor.PROFILES:
        raise PackError(f"{name}/pack.json: profile must be one of {indic_qa_vendor.PROFILES}")
    if not (directory / str(meta.get("dictionary") or "dictionary") / "wordlist.txt").is_file():
        raise PackError(f"{name}: dictionary not found in {directory}")
    try:
        profile = indic_qa_vendor.profile(code)
    except ImportError as exc:
        raise PackError(f"{name}: the vendored indic-qa checker is unavailable: {exc}") from exc
    entries = json.loads((directory / RULE_VERSIONS).read_text(encoding="utf-8")).get("rules") or {}
    rules = _rules(profile, entries, name)
    return RulePack(name, str(meta.get("version") or "0"), str(meta.get("language") or code), rules,
                    str(meta.get("description") or ""), directory=directory, meta=meta)


# -- the IRV state snapshot ------------------------------------------------------

def encode_state(value: Any) -> Any:
    """JSON for the checker's index state, keeping the types its code relies
    on: Counter (it calls subtract/update on them), dict, set, tuple (keys of
    join_split are word pairs). Keys are sorted, so the file is deterministic."""
    if isinstance(value, Counter):
        return {"C": sorted(([encode_state(k), n] for k, n in value.items()), key=_sort_key)}
    if isinstance(value, dict):
        return {"D": sorted(([encode_state(k), encode_state(v)] for k, v in value.items()), key=_sort_key)}
    if isinstance(value, tuple):
        return {"T": [encode_state(v) for v in value]}
    if isinstance(value, (set, frozenset)):
        return {"S": sorted((encode_state(v) for v in value), key=_sort_key)}
    if isinstance(value, list):
        return [encode_state(v) for v in value]
    return value


def _sort_key(item: Any) -> str:
    return json.dumps(item, ensure_ascii=False, sort_keys=True)


def decode_state(value: Any) -> Any:
    if isinstance(value, list):
        return [decode_state(v) for v in value]
    if isinstance(value, dict):
        if "C" in value:
            return Counter({_key(k): n for k, n in value["C"]})
        if "D" in value:
            return {_key(k): decode_state(v) for k, v in value["D"]}
        if "T" in value:
            return tuple(decode_state(v) for v in value["T"])
        if "S" in value:
            return {_key(v) for v in value["S"]}
    return value


def _key(value: Any) -> Any:
    decoded = decode_state(value)
    return tuple(decoded) if isinstance(decoded, list) else decoded


def snapshot(checker: Any) -> dict[str, Any]:
    """What `index_book` wrote, for every book indexed so far."""
    return {"format": IRV_STATE_FORMAT, "bookCount": encode_state(checker.book_count),
            "data": encode_state(checker.data)}


def write_state(path: Path, checker: Any, provenance: dict[str, Any]) -> None:
    blob = json.dumps({**snapshot(checker), "provenance": provenance}, ensure_ascii=False,
                      separators=(",", ":"), sort_keys=True).encode("utf-8")
    path.write_bytes(gzip.compress(blob, 9, mtime=0))


def restore_state(checker: Any, path: Path) -> dict[str, Any]:
    """Load a snapshot into a fresh checker; returns its provenance."""
    raw = json.loads(gzip.decompress(path.read_bytes()))
    if raw.get("format") != IRV_STATE_FORMAT:
        raise PackError(f"{path}: snapshot format {raw.get('format')} is not {IRV_STATE_FORMAT}")
    checker.book_count = decode_state(raw["bookCount"])
    checker.irv_count = Counter()
    for counts in checker.book_count.values():
        checker.irv_count.update(counts)
    checker.data = decode_state(raw["data"])
    return raw.get("provenance") or {}


# -- one resident checker ------------------------------------------------------------

@dataclass
class LoadedChecker:
    key: tuple
    checker: Any
    lang: Any
    profile: Any
    provenance: dict[str, Any] = field(default_factory=dict)
    # The open book's token counts at the last build_index, per book code.
    built_for: dict[str, Counter] = field(default_factory=dict)
    # The one book whose live text replaced its snapshot share, if any.
    live_book: str | None = None


_LOCK = threading.Lock()
_RESIDENT: LoadedChecker | None = None


def _settings(pack: RulePack) -> dict[str, Any]:
    """indic-qa's own per-rule switches follow the pack (and its overrides),
    so a rule Bridge disabled does not run inside the checker either."""
    return {"rules": {r.id: r.enabled for r in pack.rules}}


def _load(pack: RulePack) -> LoadedChecker:
    mods = indic_qa_vendor.modules()
    code = str(pack.meta["profile"])
    lang = mods.langs.get(code)
    folder = pack.directory / str(pack.meta.get("dictionary") or "dictionary")
    checker = mods.checker.Checker(mods.checker.Lexicon.load(folder, lang), _settings(pack), lang)
    provenance: dict[str, Any] = {}
    state = pack.directory / IRV_STATE
    if state.is_file():
        provenance = restore_state(checker, state)
    # No build_index here: the first pass indexes its book and builds then.
    return LoadedChecker((pack.name, pack.version, json.dumps(_settings(pack), sort_keys=True)), checker,
                         lang, indic_qa_vendor.profile(code), provenance)


def release(keep: str | None = None) -> None:
    """Drop the resident checker unless it belongs to pack `keep`."""
    global _RESIDENT
    with _LOCK:
        if _RESIDENT is not None and _RESIDENT.key[0] != keep:
            _RESIDENT = None
            gc.collect()


def _resident(pack: RulePack, book: str) -> LoadedChecker:
    """The pack's checker, holding the IRV snapshot plus at most one live book.
    A pass over another book starts again from the snapshot: otherwise a book
    checked earlier in the session would keep its live counts, and a book's
    findings would depend on which books were opened before it."""
    global _RESIDENT
    key = (pack.name, pack.version, json.dumps(_settings(pack), sort_keys=True))
    if _RESIDENT is None or _RESIDENT.key != key or _RESIDENT.live_book not in (None, book):
        _RESIDENT = None
        gc.collect()
        _RESIDENT = _load(pack)
    return _RESIDENT


# -- Bridge chapters as an indic-qa book ---------------------------------------------

@dataclass(frozen=True)
class LineRef:
    chapter: str
    verse: str
    text: str            # the raw verse string, as stored
    lifted: Any          # language_qa.LiftedVerse
    base: int            # where this physical line starts in lifted.visible


def book_key(book: str) -> str:
    """The checker's index key for a book: Bridge's book id, lower case."""
    return book.strip().lower()


def _verse_numbers(verse: str) -> tuple[int, int | None]:
    """First and last number of a verse key ("3", "3-4", "3a"); the key itself
    is kept as given on every finding (CLAUDE.md gotcha 12)."""
    head, _, tail = verse.partition("-")
    first = int("".join(ch for ch in head if ch.isdigit()) or 0)
    last = "".join(ch for ch in tail if ch.isdigit())
    return first, int(last) if last else None


def _runs(lifted: Any, base: int, length: int) -> list[tuple[int, int]]:
    """The text runs of one physical line, as (start, end) in the line: a run
    ends where lifted markup was (the next visible character does not follow
    the previous one in the raw verse). indic-qa's runs end at every marker
    too, and some of its rules work per run (Malayalam's grouped encoding fix);
    for adjacency it joins the runs of a line into one stream, as here."""
    if length == 0:
        return [(0, 0)]
    index = lifted.raw_index
    out, start = [], 0
    for i in range(1, length):
        if index[base + i] != index[base + i - 1] + 1:
            out.append((start, i))
            start = i
    out.append((start, length))
    return out


def build_book(book: str, chapters: dict[str, dict[str, Any]], *, lift: Callable[[str], Any],
               max_verse_chars: int) -> tuple[Any, dict[int, LineRef], list[str]]:
    """(indic-qa Book, line index -> LineRef, notes). `lift(text)` is
    language_qa.lift_inline_usfm. A verse whose markup cannot be lifted is
    left out; the common rules already report why."""
    ud = indic_qa_vendor.modules().usfm_doc
    lines: list[Any] = []
    refs: dict[int, LineRef] = {}
    spans: list[tuple[int, int]] = [(0, -1)]  # chapter 0: front matter, none
    notes: list[str] = []
    line_no = 0
    for chapter in sorted(chapters, key=lambda c: int(c) if c.isdecimal() else 0):
        if not chapter.isdecimal():
            continue
        n = int(chapter)
        while len(spans) < n:
            spans.append((len(lines), len(lines) - 1))  # a missing chapter: empty
        first = len(lines)
        line_no += 1
        # The chapter line stops next_first() and the stream at a chapter break.
        lines.append(ud.Line(line_no, "", "c", "p", n, 0, None, f"{book} {n}:0", [], [], (n, 0, None, "p")))
        previous = 0
        for verse, text in chapters[chapter].items():
            if not verse[:1].isdigit() or not isinstance(text, str) or len(text) > max_verse_chars:
                continue
            lifted, _reason = lift(text)
            if lifted is None:
                continue
            number, last = _verse_numbers(verse)
            base = 0
            for k, piece in enumerate(lifted.visible.split("\n")):
                line_no += 1
                ref = f"{book} {n}:{verse}"
                style, para = ("v", "p") if k == 0 else ("q", "q")
                segs = [ud.Seg("t", piece[a:b], a, "", "verse", 0) for a, b in _runs(lifted, base, len(piece))]
                refs[len(lines)] = LineRef(chapter, verse, text, lifted, base)
                lines.append(ud.Line(line_no, piece, style, para, n, number, last, ref, segs, [],
                                     (n, previous, None, "p")))
                base += len(piece) + 1
            previous = number
        spans.append((first, len(lines) - 1))
    return ud.Book(book, lines, spans), refs, notes


# -- mapping ------------------------------------------------------------------------

def _suggestion(text: str, source: str, rationale: str) -> dict[str, Any]:
    return {"text": text, "rank": 1, "source": source, "rationale": rationale}


def _items(block: dict[str, Any], profile: Any) -> list[tuple[str, int, int, list[dict[str, Any]], str, str]]:
    """(rule, start, end, suggestions, message, detail rule) of every finding
    in one checked line, offsets within the line text (an item's own offsets
    are within its run, which starts at the run's `s`)."""
    out = []
    switch = getattr(profile, "SHAPE_RULE_SWITCH", {})
    status_rule = getattr(profile, "STATUS_RULE", {})
    generic = getattr(profile, "GENERIC_WARNING_RULE", {})
    for seg in block["segs"]:
        if seg.get("k") != "t" or not seg.get("checked"):
            continue
        at = seg["s"]
        for token in seg.get("tokens", ()):
            if token.get("ignored_once") or token["status"] not in FINDING_STATUSES:
                continue
            detail = token.get("rule") or status_rule.get(token["status"], "")
            suggestions = [_suggestion(s["w"], "lexicon", f"{s.get('cls') or s.get('op') or ''}"
                                       + (f" · {s['freq']}× in the IRV" if s.get("freq") else ""))
                           for s in token.get("sugg", ()) if s.get("w")]
            why = "; ".join(token.get("why") or ())
            out.append((switch.get(detail, detail), at + token["s"], at + token["e"], suggestions, why, detail))
        for lead in seg.get("sandhi", ()):
            if lead.get("ignored"):
                continue
            proposed = lead.get("proposed")
            source = "majority-form" if lead.get("kind") == "consistency" else "rule"
            why = "; ".join(lead.get("why") or ())
            suggestions = [_suggestion(proposed, source, why)] if proposed and proposed != lead.get("w1") else []
            out.append((lead.get("rule", ""), at + lead["s"], at + lead["e"], suggestions,
                        lead.get("title") or "", lead.get("rule", "")))
        for warning in seg.get("warnings", ()):
            if warning.get("ignored"):
                continue
            rule = warning.get("rule") or generic.get(warning["kind"], "")
            fix = warning.get("fix")
            suggestions = [_suggestion(fix, "rule", warning.get("fix_label") or "")] if fix is not None else []
            out.append((rule, at + warning["s"], at + warning["e"], suggestions, warning.get("label") or "", rule))
    return out


def profile_findings(pack: RulePack, book: str, chapters: dict[str, dict[str, Any]], *,
                     lift: Callable[[str], Any], max_verse_chars: int, finding_id: Callable[..., str],
                     rule_fields: Callable[..., dict[str, Any]], text_hash: Callable[[str], str],
                     crossing_note: str, proceed: Callable[[], bool] = lambda: True
                     ) -> tuple[list[dict[str, Any]], list[str]] | None:
    """Every finding of the pack's checker over one book, in reading order,
    before decisions; and coverage notes. None when `proceed()` says stop.
    The language_qa helpers are passed in, to keep the import one-way."""
    synthetic, refs, notes = build_book(book, chapters, lift=lift, max_verse_chars=max_verse_chars)
    findings: list[dict[str, Any]] = []
    crossing = unmapped = 0
    with _LOCK:
        # The snapshot keys books by Bridge's book id, lower case ("1ch"); the
        # same key here replaces that book's share instead of adding a 67th.
        key = book_key(book)
        loaded = _resident(pack, key)
        loaded.live_book = key
        checker = loaded.checker
        checker.index_book(key, synthetic)
        counts = checker.book_count.get(key, Counter())
        if loaded.built_for.get(key) != counts:
            # A new or changed word can start or join a consistency cluster, and
            # build_index is where clusters are made; it also clears classify()'s
            # cache, which is keyed by generation, not by counts. Unchanged text
            # (a decision, a status refresh) needs neither.
            checker.build_index()
            loaded.built_for = {key: Counter(counts)}
        occurrences: Counter = Counter()
        for n in range(1, len(synthetic.chapters)):
            if not proceed():
                return None
            lo, _hi = synthetic.chapters[n]
            for offset, block in enumerate(checker.check_chapter(synthetic, n)):
                ref = refs.get(lo + offset)
                if ref is None:
                    continue
                for rule_name, start, end, suggestions, why, detail in _items(block, loaded.profile):
                    rule = pack.by_id(rule_name)
                    if rule is None:
                        unmapped += 1
                        continue
                    if not rule.enabled:
                        continue
                    span = ref.lifted.raw_span(ref.base + start, ref.base + end)
                    if span is None:
                        crossing += 1
                        continue
                    original = ref.text[span[0]:span[1]]
                    occurrences[(ref.chapter, ref.verse, rule.name, original)] += 1
                    message = rule.message + (f" — {why}" if why else "")
                    finding = {
                        "id": finding_id(book, ref.chapter, ref.verse, rule.name, original,
                                         occurrences[(ref.chapter, ref.verse, rule.name, original)]),
                        "book": book, "chapter": ref.chapter, "verse": ref.verse, "rule": rule.name,
                        "severity": rule.severity, "start": span[0], "end": span[1], "originalText": original,
                        "message": message, "textHash": text_hash(ref.text),
                        "ruleVersion": pack.stamp(rule)[1], "status": "review-needed",
                        **rule_fields(rule.name, suggestions, pack=pack, pack_rule=rule),
                    }
                    if detail and detail != rule.name:
                        finding["detailRule"] = detail
                    findings.append(finding)
    if crossing:
        notes.append(f"{crossing} {pack.name} {crossing_note}")
    if unmapped:
        notes.append(f"{unmapped} {pack.name} finding(s) from a rule outside the pack's catalogue omitted.")
    return findings, notes


def state_digest(pack: RulePack) -> str:
    """For the panel and the benchmark: which IRV snapshot a pack runs on."""
    path = pack.directory / IRV_STATE if pack.directory else None
    if not path or not path.is_file():
        return "none"
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]
