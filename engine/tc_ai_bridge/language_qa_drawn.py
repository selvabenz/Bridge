"""Which Language QA findings are drawn in the verse text.

A finding carries two separate answers, and they are kept apart on purpose
(DECISIONS 2026-10-07, "indic-qa findings are drawn inline by default"):

- `inline` is the **reviewed** flag. It comes from the pack (`rule_versions.json`,
  `indic_qa_rules.json`, the JSON rules) or from INLINE_RULES. The CI human
  gate (`language_qa_benchmark.human_inline_rules`) reads it and fails any
  inline rule with fewer than 20 labels or below 0.90 precision. It never
  changes here.
- `drawn` is what the reader sees. A reviewed-inline finding is always drawn.
  An indic-qa finding (a rule with `match_type == "indic-qa"`, from a profile
  pack or ta-irv's layer) is also drawn, unless the user's threshold in
  Settings > Language QA hides it. A rule is hidden when its measured reviewer
  precision is below the slider, or its confidence is below the floor. A rule
  with no labels has no measured precision and passes the slider.

The threshold is applied when a request is served, never during a pass. Moving
the slider therefore rescans nothing, touches no cache key, and changes no
finding id or decision.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .language_packs.indic_qa_adapter import ENGINE

CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}
CONFIDENCE_FLOORS = tuple(CONFIDENCE_RANK)


@dataclass(frozen=True)
class InlinePolicy:
    """The user's threshold. The defaults draw every indic-qa finding."""
    min_precision: int = 0        # percent, 0-100
    min_confidence: str = "low"   # "low" | "medium" | "high"

    @classmethod
    def of(cls, min_precision: Any = 0, min_confidence: Any = "low") -> "InlinePolicy":
        try:
            precision = int(min_precision)
        except (TypeError, ValueError):
            precision = 0
        floor = str(min_confidence or "low").lower()
        return cls(max(0, min(100, precision)), floor if floor in CONFIDENCE_RANK else "low")


def gated_rules(pack: Any) -> dict[str, dict[str, Any]]:
    """ruleId -> {"rule", "confidence", "precision", "labelled"} for every
    enabled indic-qa rule of the pack: the rules the threshold decides. Empty
    for the common checks and for a pack without an indic-qa layer."""
    if pack is None:
        return {}
    table = pack.precision()
    out: dict[str, dict[str, Any]] = {}
    for rule in pack.rules:
        if not rule.enabled or rule.match_type != ENGINE:
            continue
        rule_id = f"{pack.name}/{rule.id}"
        row = table.get(rule_id) or {}
        out[rule_id] = {"rule": rule.name, "confidence": rule.confidence,
                        "precision": row.get("precision"), "labelled": int(row.get("labelled") or 0)}
    return out


def rule_passes(meta: dict[str, Any], policy: InlinePolicy) -> bool:
    precision = meta.get("precision")
    if precision is not None and precision * 100 < policy.min_precision:
        return False
    return CONFIDENCE_RANK.get(str(meta.get("confidence")), 0) >= CONFIDENCE_RANK[policy.min_confidence]


def is_drawn(finding: dict[str, Any], gated: dict[str, dict[str, Any]], policy: InlinePolicy) -> bool:
    if finding.get("inline"):
        return True
    meta = gated.get(str(finding.get("ruleId") or ""))
    return meta is not None and rule_passes(meta, policy)


def with_drawn(findings: Iterable[dict[str, Any]], gated: dict[str, dict[str, Any]],
               policy: InlinePolicy) -> list[dict[str, Any]]:
    """Copies of the findings, each with its `drawn` answer."""
    return [{**f, "drawn": is_drawn(f, gated, policy)} for f in findings]


def drawn_rule_names(inline_rules: Iterable[str], gated: dict[str, dict[str, Any]],
                     policy: InlinePolicy) -> list[str]:
    """The `rule` names drawn under this threshold: the reviewed-inline ones
    plus every indic-qa rule that passes it."""
    return sorted(set(inline_rules) | {meta["rule"] for meta in gated.values() if rule_passes(meta, policy)})
