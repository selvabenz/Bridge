from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unicodedata

import pytest

from tc_ai_bridge.meaning_analysis import MeaningAnalysisEngine
from tc_ai_bridge.qa_audit import QaAuditEngine, QaAuditPolicy
from tc_ai_bridge.semantic_location import SemanticEmbeddingProvider, SemanticLocationEngine
from tc_ai_bridge.passage_semantic_runtime import PassageSemanticRuntime
from tc_ai_bridge.tc_project import TranslationCoreProject
from tests.support.semantic import TAMIL
from tests.support.semantic import qa8_runtime as _runtime


PHP_PAIRS = [
    ("εὐχαριστέω", "ஸ்தோத்திரிக்கிறேன்"), ("θεός", "தேவனை"), ("μνεία", "நினைக்கும்"),
    ("πάντοτε", "எப்பொழுதும்"), ("δέησις", "விண்ணப்பத்திலும்"), ("χαρά", "மகிழ்ச்சியோடு"),
    ("ποιέω", "செய்து"), ("κοινωνία", "ஐக்கியப்பட்டிருப்பதால்"), ("εὐαγγέλιον", "நற்செய்தி"),
    ("πρῶτος", "முதல்"), ("ἡμέρα", "நாள்"), ("νῦν", "இதுவரைக்கும்"), ("πείθω", "நம்பி"),
    ("ἐνάρχομαι", "தொடங்கினவர்"), ("ἔργον", "செயலைத்"), ("ἀγαθός", "நல்ல"),
    ("ἐπιτελέω", "நடத்தி வருவார்"), ("χριστός", "கிறிஸ்துவின்"), ("Ἰησοῦς", "இயேசு"),
]


def _norm(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


class FixtureEmbeddingProvider(SemanticEmbeddingProvider):
    provider_id = "stage8-fixture"; provider_version = "v1"; model_id = "stage8-fixture"
    normalization = "L2"; languages = ("el", "hbo", "arc", "ta", "en"); offline = True; available = True
    def __init__(self, vectors: dict[str, list[float]]):
        self.vectors = {_norm(key): value for key, value in vectors.items()}
        self.dimensions = len(next(iter(vectors.values())))
        self.model_hash = hashlib.sha256(json.dumps(self.vectors, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self.vectors.get(_norm(text), [0.0] * self.dimensions) for text in texts]


def paired_vectors(pairs: list[tuple[str, str]]) -> dict[str, list[float]]:
    result = {}
    for index, (source, target) in enumerate(pairs):
        vector = [0.0] * len(pairs); vector[index] = 1.0
        result[source] = vector; result[target] = vector
    return result


def _run_qa(runtime: PassageSemanticRuntime, provider: SemanticEmbeddingProvider | None,
            chapter: str, verse: str, end_chapter: str = "", end_verse: str = "",
            **location_kwargs) -> dict:
    location = SemanticLocationEngine(runtime, provider).run_range(
        chapter, verse, end_chapter, end_verse, **location_kwargs,
    )
    meaning = MeaningAnalysisEngine(runtime).run_range(
        chapter, verse, end_chapter, end_verse, location_run_id=location["id"],
    )
    return QaAuditEngine(runtime).run_range(
        chapter, verse, end_chapter, end_verse, meaning_run_id=meaning["id"],
    )


def test_tamil_negative_preservation_does_not_emit_false_negation_problem(
    tmp_path: Path,
) -> None:
    raw_target = unicodedata.normalize("NFD", "இல்லை")
    runtime = _runtime(
        tmp_path,
        language="ta",
        chapters={"1": {"22": raw_target}},
    )
    scripture_path = runtime.project.path / "php" / "1.json"
    scripture_before = scripture_path.read_bytes()
    qa = _run_qa(
        runtime,
        FixtureEmbeddingProvider(paired_vectors([("οὐ", "இல்லை")])),
        "1", "22",
    )
    assert not any(finding["kind"] == "NEGATION_PROBLEM" for finding in qa["findings"])
    meaning = runtime.repository.meaning_analysis_run(qa["meaningRunId"])
    polarity = [
        component
        for assessment in meaning["assessments"]
        for component in assessment["componentAssessments"]
        if component["coverageDimension"] == "POLARITY"
    ]
    assert polarity
    assert all(component["status"] == "PRESERVED" for component in polarity)
    assert scripture_path.read_bytes() == scripture_before
    assert runtime.project.target_verse_text("1", "22") == raw_target


def test_fresh_meaning_fingerprint_refreshes_stable_target_account_status(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path, language="en", chapters={"1": {"3": "all"}})
    provider = FixtureEmbeddingProvider(paired_vectors([("πᾶς", "all")]))
    location = SemanticLocationEngine(runtime, provider).run_range("1", "3")
    first_meaning = MeaningAnalysisEngine(runtime, model_version="pre-v11-simulation").run_range(
        "1", "3", location_run_id=location["id"],
    )
    first_qa = QaAuditEngine(runtime).run_range(
        "1", "3", meaning_run_id=first_meaning["id"],
    )
    account_id = first_qa["targetSupportAccountIds"][0]
    account = runtime.repository.coverage_account(account_id)
    expected_status = account["coverageStatus"]
    wrong_status = "UNCERTAIN" if expected_status != "UNCERTAIN" else "SOURCE_SUPPORTED"
    runtime.repository.update_coverage_account_status(
        account_id,
        coverage_status=wrong_status,
        covered_by_relationship_ids=(),
        finding_id=None,
        expected_revision=account["revision"],
    )

    fresh_meaning = MeaningAnalysisEngine(runtime, model_version="v11-cache-regression").run_range(
        "1", "3", location_run_id=location["id"],
    )
    fresh_qa = QaAuditEngine(runtime).run_range(
        "1", "3", meaning_run_id=fresh_meaning["id"],
    )
    refreshed = runtime.repository.coverage_account(account_id)

    assert fresh_qa["cacheStatus"] == "MISS"
    assert fresh_qa["fingerprint"] != first_qa["fingerprint"]
    assert refreshed["coverageStatus"] == expected_status
    assert refreshed["reviewStatus"] == account["reviewStatus"]


# --- Precedence / severity policy (item 11, 24, 25) -------------------------

@pytest.mark.parametrize(("dimension", "status", "expected_kind"), [
    ("POLARITY", "CONTRADICTED", "NEGATION_PROBLEM"),
    ("QUANTITY", "CONTRADICTED", "QUANTITY_PROBLEM"),
    ("TEMPORAL_ASPECTUAL", "CONTRADICTED", "TEMPORAL_PROBLEM"),
    ("PARTICIPANT", "CONTRADICTED", "PARTICIPANT_PROBLEM"),
    ("REFERENT", "ALTERED", "REFERENT_PROBLEM"),
])
def test_component_dimension_precedence(dimension: str, status: str, expected_kind: str) -> None:
    assessment = {
        "meaningStatus": "CONTRADICTED" if status == "CONTRADICTED" else "MEANING_SHIFT",
        "componentAssessments": [{
            "coverageDimension": dimension, "status": status,
            "evidence": {"resourceStatus": "NOT_CHECKED"},
        }],
    }
    assert QaAuditPolicy.finding_kind_for(assessment) == expected_kind


@pytest.mark.parametrize(("status", "expected_kind"), [
    ("CONTRADICTED", "CONTRADICTION"),
    ("MEANING_SHIFT", "MEANING_SHIFT"),
    ("UNDERTRANSLATED", "POSSIBLE_UNDERTRANSLATION"),
    ("PARTIAL", "POSSIBLE_UNDERTRANSLATION"),
    ("OVERTRANSLATED", "POSSIBLE_OVERTRANSLATION"),
    ("PRESERVED", None),
    ("PRESERVED_WITH_RESTRUCTURING", None),
    ("UNVERIFIABLE", None),
])
def test_generic_status_precedence(status: str, expected_kind: str | None) -> None:
    assessment = {"meaningStatus": status, "componentAssessments": []}
    result = QaAuditPolicy.finding_kind_for(assessment)
    assert (result.value if result else None) == expected_kind


def test_resource_conflict_takes_precedence_over_meaning_status() -> None:
    assessment = {
        "meaningStatus": "PRESERVED",
        "componentAssessments": [{
            "coverageDimension": "LEXICAL_CONTENT", "status": "PRESERVED",
            "evidence": {"resourceStatus": "CONFLICTING"},
        }],
    }
    assert QaAuditPolicy.finding_kind_for(assessment) == "RESOURCE_CONFLICT"


# --- Source coverage gates (item 5-9) ----------------------------------------

def _unit_id_by_kind(runtime: PassageSemanticRuntime, chapter: str, verse: str, kind: str) -> str:
    source = runtime.source_semantic.build_range(chapter, verse)
    return next(unit["id"] for unit in source["units"] if unit["kind"] == kind)


def test_all_to_some_produces_quantity_problem_not_generic_finding(tmp_path: Path) -> None:
    # The real UGNT verse text has several Greek lexical items beyond "πᾶς"
    # that genuinely have no counterpart in the single-word target fixture
    # "some" -- those legitimately gate to POSSIBLE_OMISSION and are not the
    # object of this test. What matters is that the *quantifier* unit itself
    # (which IS located, just meaning-altered) is reported as a quantity
    # problem, not double-counted as an omission.
    runtime = _runtime(tmp_path, language="en", chapters={"1": {"3": "some"}})
    provider = FixtureEmbeddingProvider(paired_vectors([("πᾶς", "some")]))
    run = _run_qa(runtime, provider, "1", "3")
    quantifier_id = _unit_id_by_kind(runtime, "1", "3", "QUANTIFIER")
    quantity_findings = [item for item in run["findings"] if item["kind"] == "QUANTITY_PROBLEM"]
    assert any(quantifier_id in item["sourceSemanticUnitIds"] for item in quantity_findings)
    assert any(item["severity"] in {"HIGH", "CRITICAL"} for item in quantity_findings)
    omissions = [item for item in run["findings"] if item["kind"] == "POSSIBLE_OMISSION"]
    assert not any(quantifier_id in item["sourceSemanticUnitIds"] for item in omissions)


def test_genuine_absence_gates_to_possibly_missing_and_omission(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, language="en", chapters={"1": {"3": "unrelated"}})
    run = _run_qa(runtime, None, "1", "3")
    coverage = QaAuditEngine(runtime).get_source_coverage(run["id"])
    assert any(item["coverageStatus"] == "POSSIBLY_MISSING" for item in coverage)
    omissions = [item for item in run["findings"] if item["kind"] == "POSSIBLE_OMISSION"]
    assert omissions
    assert all(item["qaDisposition"] == "UNRESOLVED" for item in omissions)
    assert all(item["reviewStatus"] == "AI_PROPOSED" for item in omissions)
    assert "MISSING" not in {item["kind"] for item in run["findings"]}


def test_ambiguous_location_blocks_omission(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, language="en", chapters={"1": {"3": "some some"}})
    provider = FixtureEmbeddingProvider(paired_vectors([("πᾶς", "some")]))
    run = _run_qa(runtime, provider, "1", "3")
    quantifier_id = _unit_id_by_kind(runtime, "1", "3", "QUANTIFIER")
    coverage = QaAuditEngine(runtime).get_source_coverage(run["id"])
    quantifier_account = next(item for item in coverage if item["auditOwnerUnitId"] == quantifier_id)
    assert quantifier_account["coverageStatus"] == "UNCERTAIN"
    omissions = [item for item in run["findings"] if item["kind"] == "POSSIBLE_OMISSION"]
    assert not any(quantifier_id in item["sourceSemanticUnitIds"] for item in omissions)


def test_search_incomplete_blocks_omission(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, language="en", chapters={"1": {"3": "some"}})
    provider = FixtureEmbeddingProvider(paired_vectors([("πᾶς", "some")]))
    location = SemanticLocationEngine(runtime, provider).run_range(
        "1", "3", max_candidate_evaluations=1,
    )
    meaning = MeaningAnalysisEngine(runtime).run_range("1", "3", location_run_id=location["id"])
    run = QaAuditEngine(runtime).run_range("1", "3", meaning_run_id=meaning["id"])
    assert not any(item["kind"] == "POSSIBLE_OMISSION" for item in run["findings"])
    coverage = QaAuditEngine(runtime).get_source_coverage(run["id"])
    assert any(item["coverageStatus"] == "UNCERTAIN" for item in coverage)


def test_php_reordered_passage_produces_no_false_omissions(tmp_path: Path) -> None:
    # PHP_PAIRS covers the content-word vocabulary of Phil 1:3-6 but not every
    # function word/particle in the real UGNT text, so some genuinely-uncovered
    # lexical items may legitimately gate to POSSIBLY_MISSING. What matters is
    # that the well-covered lemmas (which the REORDERED Tamil passage really
    # does realize, just in a different verse) are never falsely flagged.
    runtime = _runtime(tmp_path)
    provider = FixtureEmbeddingProvider(paired_vectors(PHP_PAIRS))
    run = _run_qa(runtime, provider, "1", "3", "1", "6")
    source = runtime.source_semantic.build_range("1", "3", "1", "6")
    covered_lemmas = {source_lemma for source_lemma, _ in PHP_PAIRS}
    covered_unit_ids = {
        unit["id"] for unit in source["units"]
        if unit.get("semanticFeatures", {}).get("lemma") in covered_lemmas
    }
    omissions = [item for item in run["findings"] if item["kind"] == "POSSIBLE_OMISSION"]
    falsely_omitted = [
        item for item in omissions
        if set(item["sourceSemanticUnitIds"]) & covered_unit_ids
    ]
    assert not falsely_omitted
    coverage = QaAuditEngine(runtime).get_source_coverage(run["id"])
    statuses = {item["coverageStatus"] for item in coverage}
    assert statuses & {"COVERED", "COVERED_BY_RESTRUCTURING"}
    covered_accounts = {
        item["auditOwnerUnitId"] for item in coverage
        if item["coverageStatus"] in {"COVERED", "COVERED_BY_RESTRUCTURING"}
    }
    assert covered_unit_ids & covered_accounts


# --- Target support gates (item 12-17) ---------------------------------------

def test_grammatical_function_word_produces_no_addition(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, language="en", chapters={"1": {"3": "the man went"}})
    provider = FixtureEmbeddingProvider(paired_vectors([("ἄνθρωπος", "man")]))
    run = _run_qa(runtime, provider, "1", "3")
    target = runtime.target_semantic.build_range("1", "3")
    the_unit = next(unit for unit in target["units"] if unit["normalizedSurface"] == "the")
    support = QaAuditEngine(runtime).get_target_support(run["id"])
    the_support = next(item for item in support if item["auditOwnerUnitId"] == the_unit["id"])
    assert the_support["coverageStatus"] == "GRAMMATICALLY_REQUIRED"
    assert not any(item["kind"] == "POSSIBLE_ADDITION" for item in run["findings"])


def test_unsupported_specificity_produces_possible_addition(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, language="en", chapters={"1": {"3": "holy city"}})
    provider = FixtureEmbeddingProvider(paired_vectors([("πόλις", "city")]))
    run = _run_qa(runtime, provider, "1", "3")
    additions = [item for item in run["findings"] if item["kind"] == "POSSIBLE_ADDITION"]
    assert additions
    target = runtime.target_semantic.build_range("1", "3")
    holy_unit = next(unit for unit in target["units"] if unit["normalizedSurface"] == "holy")
    assert any(holy_unit["id"] in item["targetSemanticUnitIds"] for item in additions)


# --- Human-review boundary and staleness (item 22, 38, 39) -------------------

def test_human_confirmation_and_staleness_preserved(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, language="en", chapters={"1": {"3": "unrelated"}})
    run = _run_qa(runtime, None, "1", "3")
    finding = next(item for item in run["findings"] if item["kind"] == "POSSIBLE_OMISSION")
    runtime.repository.update_qa_disposition(
        finding["id"], "CONFIRMED_TRANSLATION_ERROR", expected_revision=1, reviewer="qa-test",
    )
    lock = runtime.repository.source_lock(runtime.project_id, "PHP")
    runtime.repository.synchronize_source_lock(
        project_id=runtime.project_id, book="PHP", resource_id=lock["resource_id"],
        resource_version="changed", resource_hash="f" * 64,
    )
    with runtime.repository._connect() as conn:
        row = conn.execute(
            "SELECT qa_disposition,review_status,lifecycle_status FROM qa_findings WHERE id=?",
            (finding["id"],),
        ).fetchone()
    assert row["qa_disposition"] == "CONFIRMED_TRANSLATION_ERROR"
    assert row["review_status"] == "HUMAN_APPROVED"
    assert row["lifecycle_status"] == "STALE"


def test_qa_cache_hit_on_repeat_run(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, language="en", chapters={"1": {"3": "unrelated"}})
    first = _run_qa(runtime, None, "1", "3")
    assert first["cacheStatus"] == "MISS"
    location = runtime.semantic_location.run_range("1", "3")
    meaning = runtime.meaning_analysis.run_range("1", "3", location_run_id=location["id"])
    second = QaAuditEngine(runtime).run_range("1", "3", meaning_run_id=meaning["id"])
    assert second["cacheStatus"] == "HIT"
    assert second["id"] == first["id"]


def test_qa_reanalysis_after_scripture_edit_reuses_stable_coverage_accounts(
    tmp_path: Path,
) -> None:
    """The ordinary post-correction Stage 8 path must be safely repeatable."""
    runtime = _runtime(tmp_path, language="en", chapters={"1": {
        "3": "i thank my god",
        "4": "always in every prayer",
        "5": "partnership from the first day until now",
        "6": "some remembrance of you remains with me",
    }})
    first = _run_qa(runtime, None, "1", "3", "1", "6")
    first_source_accounts = set(first["sourceCoverageAccountIds"])
    first_target_accounts = set(first["targetSupportAccountIds"])

    runtime.project.apply_scripture_edit(
        "1", "6", "all remembrance of you remains with me",
    )
    second = _run_qa(runtime, None, "1", "3", "1", "6")

    assert second["cacheStatus"] == "MISS"
    assert set(second["sourceCoverageAccountIds"]) == first_source_accounts
    # Unedited verses keep stable target units and therefore stable support
    # accounts; Stage 8 must update them rather than collide at INSERT.
    assert set(second["targetSupportAccountIds"]) & first_target_accounts
    with runtime.repository._connect() as conn:
        count, distinct_count = conn.execute(
            "SELECT COUNT(*),COUNT(DISTINCT id) FROM coverage_accounts",
        ).fetchone()
    assert count == distinct_count


# --- Protocol round-trip and no unintended side effects (item 42) -----------



# --- Hebrew / Aramaic reuse (item 45) ----------------------------------------

@pytest.mark.parametrize(("book", "chapter", "verse", "target", "pairs"), [
    ("GEN", "2", "5", "not all", [("לֹא", "not"), ("כֹּל", "all")]),
    ("DAN", "2", "4", "king", [("מֶלֶךְ", "king")]),
])
def test_hebrew_and_aramaic_qa_runs(
    tmp_path: Path, book: str, chapter: str, verse: str, target: str,
    pairs: list[tuple[str, str]],
) -> None:
    runtime = _runtime(tmp_path, book=book, language="en", chapters={chapter: {verse: target}})
    provider = FixtureEmbeddingProvider(paired_vectors(pairs))
    run = _run_qa(runtime, provider, chapter, verse)
    assert run["book"] == book
    assert run["meaningRunId"]
    assert isinstance(run["findings"], list)


def test_deduplication_one_finding_per_relationship(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, language="en", chapters={"1": {"3": "some"}})
    provider = FixtureEmbeddingProvider(paired_vectors([("πᾶς", "some")]))
    run = _run_qa(runtime, provider, "1", "3")
    relationship_ids = [
        relationship_id for item in run["findings"] for relationship_id in item["semanticRelationshipIds"]
    ]
    assert len(relationship_ids) == len(set(relationship_ids))
