"""Fixtures for the Stage 8 target-hash contract tests (Stage 8 / 9B): an
English Philippians project, cross-verse pairs, fixture embeddings, and the
Stage 6B-8 analysis run over it.

Moved verbatim out of test_qa_target_hash_contract_stage8_9b.py, which a
correction test imported them from (CLAUDE.md rule 2, #74)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unicodedata

from tc_ai_bridge.meaning_analysis import MeaningAnalysisEngine
from tc_ai_bridge.passage_semantic_runtime import PassageSemanticRuntime
from tc_ai_bridge.qa_audit import QaAuditEngine
from tc_ai_bridge.semantic_location import SemanticEmbeddingProvider, SemanticLocationEngine
from tc_ai_bridge.tc_project import TranslationCoreProject


# An English target, so the deterministic meaning rules -- whose specificity
# markers are English -- fire without an AI provider.  Verse 6 carries "only",
# an unlicensed specificity marker, and the embedding fixture locates the
# PHP 1:3 source unit there.  That is the canonical cross-verse shape: source
# semantics at PHP 1:3, target realization at PHP 1:6, analysis range 1:3-1:6.
ENGLISH_PHP = {
    "3": "I thank my God for all my remembrance of you,",
    "4": "always in every prayer of mine for you all making my prayer with joy,",
    "5": "because of your partnership in the gospel from the first day until now,",
    "6": "only remembrance of you remains with me and he who began a good work will carry it on.",
}


CROSS_VERSE_PAIRS = [("μνεία", "only"),
                     ("ἐνάρχομαι", "began"),
                     ("ἐπιτελέω", "carry")]


SOURCE_REFERENCE = "PHP 1:3"


TARGET_REFERENCE = "PHP 1:6"


def _norm(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


class _FixtureEmbeddings(SemanticEmbeddingProvider):
    """The shipped app has no embedding provider; Stage 6B tests inject one."""

    provider_id = "stage8-9b-hash-contract"
    provider_version = "v1"
    model_id = "stage8-9b-hash-contract"
    normalization = "L2"
    languages = ("el", "hbo", "en")
    offline = True
    available = True

    def __init__(self, vectors: dict[str, list[float]]):
        self.vectors = {_norm(key): value for key, value in vectors.items()}
        self.dimensions = len(next(iter(vectors.values())))
        self.model_hash = hashlib.sha256(
            json.dumps(self.vectors, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self.vectors.get(_norm(text), [0.0] * self.dimensions) for text in texts]


def _paired(pairs: list[tuple[str, str]]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for index, (source, target) in enumerate(pairs):
        vector = [0.0] * len(pairs)
        vector[index] = 1.0
        out[source] = vector
        out[target] = vector
    return out


def _project(tmp_path: Path, verses: dict[str, str]) -> PassageSemanticRuntime:
    root = tmp_path / "PHP-en"
    (root / "php").mkdir(parents=True)
    alignment = root / ".apps" / "translationCore" / "alignmentData" / "php"
    alignment.mkdir(parents=True)
    (root / "manifest.json").write_text(json.dumps({
        "project": {"id": "php", "name": "PHP"}, "target_language": {"id": "en"},
        "resource": {"id": "test"}, "tc_version": "8",
    }), encoding="utf-8")
    (root / "php" / "1.json").write_text(
        json.dumps(verses, ensure_ascii=False), encoding="utf-8")
    (alignment / "1.json").write_text(json.dumps(
        {ref: {"alignments": [], "wordBank": []} for ref in verses}), encoding="utf-8")
    lines = ["\\id PHP", "\\c 1", "\\p"]
    lines.extend(f"\\v {verse} OLD IMPORTED" for verse in verses)
    (root / "php.usfm").write_text("\n".join(lines) + "\n", encoding="utf-8")
    project = TranslationCoreProject(root)
    runtime = PassageSemanticRuntime(project, f"stage8-9b-{tmp_path.name}")
    project.attach_passage_semantic_runtime(runtime)
    return runtime


def _analyze(
    runtime: PassageSemanticRuntime, provider: SemanticEmbeddingProvider | None,
    chapter: str, verse: str, end_chapter: str = "", end_verse: str = "",
) -> tuple[dict, dict]:
    """Stage 5 and 6A (built by 6B) -> 6B -> 7 -> 8, as production runs them."""
    location = SemanticLocationEngine(runtime, provider).run_range(
        chapter, verse, end_chapter, end_verse)
    meaning = MeaningAnalysisEngine(runtime).run_range(
        chapter, verse, end_chapter, end_verse, location_run_id=location["id"])
    audit = QaAuditEngine(runtime).run_range(
        chapter, verse, end_chapter, end_verse, meaning_run_id=meaning["id"])
    target_inventory = runtime.repository.target_inventory(location["targetInventoryId"])
    return audit, target_inventory


def _of_kind(audit: dict, kind: str) -> dict:
    for finding in audit["findings"]:
        if finding["kind"] == kind:
            return finding
    raise AssertionError(f"the real pipeline emitted no {kind} finding")


def _confirm(runtime: PassageSemanticRuntime, finding: dict) -> dict:
    """The ordinary human decision, through the normal review service."""
    runtime.qa_review.decide_finding(
        finding["id"], "CONFIRMED_TRANSLATION_ERROR",
        expected_revision=finding["revision"],
        expected_target_content_hashes=tuple(finding["targetContentHashes"]),
        note="Confirmed by a reviewer.")
    stored = runtime.repository.qa_finding(finding["id"])
    assert stored["qaDisposition"] == "CONFIRMED_TRANSLATION_ERROR"
    assert stored["reviewStatus"] == "HUMAN_APPROVED"
    return stored


def _codes(eligibility) -> set[str]:
    return {reason.code.value for reason in eligibility.reasons}
