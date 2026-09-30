"""What a pack rule proposes before scan_text turns it into a finding."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Candidate:
    rule: Any           # loader.Rule
    start: int          # visible offsets, or raw offsets when raw is True
    end: int
    replacement: str | None
    message: str
    rationale: str
    raw: bool = False
    # Pair rules: where the first word ends, and the fix confined to it, for a
    # pair that straddles a poetry line or lifted markup (scan_text).
    first_word_end: int | None = None
    first_word_fix: str | None = None
    # A matched context: its confidence, and ranked alternatives after the fix.
    confidence: str | None = None
    alternatives: tuple = ()  # ((replacement, rationale), ...)
    # The suggestion's `source` ("rule", or "lexicon" for a lexicon kind).
    source: str = "rule"
