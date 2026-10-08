"""V11-000a: Stage 6B word-alignment evidence -- source/target resolvers and
precedent projection. See docs/archive/V11-000_STAGE6B_ALIGNMENT_SPIKE.md.

The target-resolver tests below are the tokenization-disagreement cases the
implementation prompt asked to write first, explicitly: a bare verse
reference ("3:16"), a word in inline quotes, a hyphenated and an
apostrophised word, a repeated word with punctuation between shifting the
occurrence count, and a bottomWord no longer present in the current text.
Confirmed against the real tokenizers (tc-whitespace-v1 vs.
bridge-unicode-word-v1) rather than assumed -- hyphenated/apostrophised
words turn out to agree between the two, so those are "still resolves"
checks, not disagreement checks.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tc_ai_bridge import word_alignment_evidence
from tc_ai_bridge.models import TokenRef
from tc_ai_bridge.original_language_resources import resource_for_book
from tc_ai_bridge.passage_semantic_runtime import PassageSemanticRuntime
from tc_ai_bridge.tc_project import TranslationCoreProject
from tc_ai_bridge.word_alignment_evidence import (
    alignment_precedents_for_range, resolve_source_token_id, resolve_target_token_id,
)


# --- source resolver: real bundled UGNT tokens, PHP 1:3 --------------------

def test_resolves_a_real_ugnt_source_token_by_word_and_occurrence() -> None:
    resource = resource_for_book("PHP")
    assert resource is not None
    # Legacy classic-dictionary Strong's form (no trailing UGNT variant
    # digit) -- the pack itself stores "G23160" for this token.
    ref = TokenRef(word="Θεῷ", occurrence=1, occurrences=1, strong="G2316", lemma="θεός")
    instance_id = resolve_source_token_id(resource, "PHP", "1", "3", ref)
    assert instance_id is not None
    assert instance_id.startswith("source-token-")


def test_source_resolution_misses_when_occurrence_does_not_match() -> None:
    resource = resource_for_book("PHP")
    assert resource is not None
    ref = TokenRef(word="Θεῷ", occurrence=2, occurrences=1)
    assert resolve_source_token_id(resource, "PHP", "1", "3", ref) is None


def test_source_resolution_misses_an_unknown_word() -> None:
    resource = resource_for_book("PHP")
    assert resource is not None
    ref = TokenRef(word="not-a-real-greek-word", occurrence=1, occurrences=1)
    assert resolve_source_token_id(resource, "PHP", "1", "3", ref) is None


def test_source_resolution_ignores_strongs_five_digit_variant_padding() -> None:
    """G23160 (pack) and G2316 (classic dictionary) must resolve the same
    token -- word+occurrence already disambiguates, so this only confirms
    the reinforcement score never blocks a match on this difference."""
    resource = resource_for_book("PHP")
    assert resource is not None
    padded = TokenRef(word="Θεῷ", occurrence=1, occurrences=1, strong="G23160")
    classic = TokenRef(word="Θεῷ", occurrence=1, occurrences=1, strong="G2316")
    assert resolve_source_token_id(resource, "PHP", "1", "3", padded) \
        == resolve_source_token_id(resource, "PHP", "1", "3", classic)


# --- R1 (review fix): exact-NFC matching, no casefold ----------------------
#
# docs/archive/V11-000a_REVIEW_FIX_PROMPT.md F1: tokenize_target_text and tC's own
# aligner both count occurrence/occurrences over the exact NFC string, case
# included. Casefolding the word comparison ties together tokens the
# counting already told apart, produces a spurious len()!=1 ambiguity, and
# silently drops the group. Confirmed here against a monkeypatched source
# pack (deterministic) and real bundled UGNT data (1CO 2:11, found by
# scanning every NT book's pack for a verse where the same (lemma, strong)
# pair has two token forms differing only by case -- see the scan below).

def test_resolve_source_token_id_distinguishes_case_with_a_monkeypatched_pack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack_tokens = [
        {"word": "Χάρις", "occurrence": 1, "occurrences": 1,
         "strong": "G54850", "lemma": "χάρις", "morph": "Gr,N,,,,,NFS,"},
        {"word": "χάρις", "occurrence": 1, "occurrences": 1,
         "strong": "G54850", "lemma": "χάρις", "morph": "Gr,N,,,,,NFS,"},
    ]
    monkeypatch.setattr(
        "tc_ai_bridge.word_alignment_evidence.source_tokens_for_verse",
        lambda book, chapter, verse: pack_tokens,
    )
    resource = resource_for_book("PHP")
    assert resource is not None
    lower = TokenRef(word="χάρις", occurrence=1, occurrences=1, strong="G5485", lemma="χάρις")
    upper = TokenRef(word="Χάρις", occurrence=1, occurrences=1, strong="G5485", lemma="χάρις")
    lower_id = resolve_source_token_id(resource, "PHP", "1", "1", lower)
    upper_id = resolve_source_token_id(resource, "PHP", "1", "1", upper)
    assert lower_id is not None and upper_id is not None
    assert lower_id != upper_id


def test_resolve_source_token_id_distinguishes_case_in_real_ugnt_data() -> None:
    """1 Corinthians 2:11 genuinely carries πνεῦμα (occ 1/1) and Πνεῦμα
    (occ 1/1) as separate tokens, same lemma/strong/morph (πνεῦμα, G4151):
    found by scanning every bundled NT book's pack for a verse where one
    (lemma, strong) key maps to two distinct word forms whose only
    difference is case. Before the fix these two tied (same casefolded
    word, same occurrence, same lemma/strong/morph) and both dropped as
    ambiguous; after it, each resolves to its own real pack token."""
    resource = resource_for_book("1CO")
    assert resource is not None
    lower = TokenRef(word="πνεῦμα", occurrence=1, occurrences=1, strong="G4151", lemma="πνεῦμα")
    upper = TokenRef(word="Πνεῦμα", occurrence=1, occurrences=1, strong="G4151", lemma="πνεῦμα")
    lower_id = resolve_source_token_id(resource, "1CO", "2", "11", lower)
    upper_id = resolve_source_token_id(resource, "1CO", "2", "11", upper)
    assert lower_id is not None and upper_id is not None
    assert lower_id != upper_id


# --- target resolver: tokenization-disagreement cases ----------------------

_TARGET_FIXTURE_PROFILE = "bridge-unicode-word-v1"


def _resolve(text: str, ref: TokenRef) -> str | None:
    return resolve_target_token_id(
        project_id="p1", book="PHP", displayed_reference="PHP 1:1",
        text_revision="rev-1", current_text=text, profile=_TARGET_FIXTURE_PROFILE,
        ref=ref,
    )


def _tokens(text: str) -> list[dict]:
    from tc_ai_bridge.passage_semantic_runtime import tokenize_target_text
    return tokenize_target_text(text, _TARGET_FIXTURE_PROFILE)


def _identity_for(text: str, token: dict) -> str:
    from tc_ai_bridge.passage_semantic_runtime import target_token_identity
    _, instance_id, _ = target_token_identity(
        "p1", "PHP", "PHP 1:1", "rev-1", _TARGET_FIXTURE_PROFILE, token,
    )
    return instance_id


def test_target_resolution_matches_a_plain_word() -> None:
    ref = TokenRef(word="anchor", occurrence=1, occurrences=1)
    instance_id = _resolve("this is the anchor word", ref)
    assert instance_id is not None
    assert instance_id.startswith("target-token-")


def test_target_resolution_disagrees_on_a_bare_verse_reference() -> None:
    # tc-whitespace-v1 keeps "3:16" as one token; bridge-unicode-word-v1
    # splits it into "3", ":", "16" -- no single token equals "3:16".
    ref = TokenRef(word="3:16", occurrence=1, occurrences=1)
    assert _resolve("see John 3:16 today", ref) is None


def test_target_resolution_disagrees_on_a_word_in_inline_quotes() -> None:
    # tc-whitespace-v1 attaches the quote mark to the word ('"hello');
    # bridge-unicode-word-v1 emits the quote as its own punctuation token.
    ref = TokenRef(word='"hello', occurrence=1, occurrences=1)
    assert _resolve('he said "hello world" to me', ref) is None


def test_target_resolution_agrees_on_a_hyphenated_word() -> None:
    ref = TokenRef(word="well-known", occurrence=1, occurrences=1)
    assert _resolve("a well-known friend", ref) is not None


def test_target_resolution_agrees_on_an_apostrophised_word() -> None:
    ref = TokenRef(word="Peter’s", occurrence=1, occurrences=1)
    assert _resolve("it is Peter’s book", ref) is not None


def test_target_resolution_disagrees_when_interleaved_punctuation_shifts_occurrence_count() -> None:
    # Under tc-whitespace-v1, "love," and "love" are different normalized
    # forms, so tC's own bottomWord for the bare "love" would record
    # occurrences=2. bridge-unicode-word-v1 splits the comma off, so the
    # bare form "love" occurs 3 times in this text -- the counts disagree.
    ref = TokenRef(word="love", occurrence=1, occurrences=2)
    assert _resolve("love, love love", ref) is None


def test_target_resolution_returns_none_for_a_bottom_word_no_longer_in_the_text() -> None:
    ref = TokenRef(word="obsolete", occurrence=1, occurrences=1)
    assert _resolve("the verse was edited", ref) is None


def test_target_resolution_distinguishes_sentence_initial_capital_from_lowercase() -> None:
    """"Grace to you. He gave grace." -- before the fix, casefolding tied
    "Grace" and "grace" (both occurrence=1 of 1 under their own exact-NFC
    form) into one ambiguous len()==2 match and dropped both."""
    text = "Grace to you. He gave grace."
    lower_ref = TokenRef(word="grace", occurrence=1, occurrences=1)
    upper_ref = TokenRef(word="Grace", occurrence=1, occurrences=1)
    lower_id = _resolve(text, lower_ref)
    upper_id = _resolve(text, upper_ref)
    assert lower_id is not None and upper_id is not None
    assert lower_id != upper_id
    # Confirm each resolved to the token its own case actually names.
    expected_lower = next(
        t for t in _tokens(text) if t["raw"] == "grace" and t["occurrence"] == 1
    )
    expected_upper = next(
        t for t in _tokens(text) if t["raw"] == "Grace" and t["occurrence"] == 1
    )
    assert lower_id == _identity_for(text, expected_lower)
    assert upper_id == _identity_for(text, expected_upper)


def test_target_resolution_distinguishes_two_cased_repeats_of_the_same_word() -> None:
    """"The LORD is my shepherd; the Lord provides." -- Lord/LORD/The/the are
    each their own exact-NFC form, occurrence=1 of 1; casefolding ties
    "Lord"/"LORD" together (and separately "The"/"the")."""
    text = "The LORD is my shepherd; the Lord provides."
    lord_id = _resolve(text, TokenRef(word="Lord", occurrence=1, occurrences=1))
    upper_lord_id = _resolve(text, TokenRef(word="LORD", occurrence=1, occurrences=1))
    assert lord_id is not None and upper_lord_id is not None
    assert lord_id != upper_lord_id
    assert lord_id == _identity_for(text, next(
        t for t in _tokens(text) if t["raw"] == "Lord"
    ))
    assert upper_lord_id == _identity_for(text, next(
        t for t in _tokens(text) if t["raw"] == "LORD"
    ))


def test_target_resolution_distinguishes_capitalized_article_from_lowercase() -> None:
    text = "The lord is the shepherd"
    the_id = _resolve(text, TokenRef(word="The", occurrence=1, occurrences=1))
    lower_the_id = _resolve(text, TokenRef(word="the", occurrence=1, occurrences=1))
    assert the_id is not None and lower_the_id is not None
    assert the_id != lower_the_id
    assert the_id == _identity_for(text, next(
        t for t in _tokens(text) if t["raw"] == "The"
    ))
    assert lower_the_id == _identity_for(text, next(
        t for t in _tokens(text) if t["raw"] == "the"
    ))


# --- precedent projection: a real completed alignment, end to end ---------

# A single-word verse, like the pre-existing HUMAN_PRECEDENT test
# (test_human_approved_precedent_uses_revision_bound_token_identity) uses --
# a longer target text produces overlapping candidate spans that all touch
# the aligned token (e.g. a 2-word span containing "God" gets the same
# WORD_ALIGNMENT hit a 1-word "God" span does), which is a genuine, expected
# AMBIGUOUS outcome elsewhere, not what this fixture is testing.
PHP_1_3_EN = "God"


def _runtime_with_completed_alignment(tmp_path: Path) -> PassageSemanticRuntime:
    root = tmp_path / "php-en"
    (root / "php").mkdir(parents=True)
    alignment_dir = root / ".apps" / "translationCore" / "alignmentData" / "php"
    alignment_dir.mkdir(parents=True)
    (root / "manifest.json").write_text(json.dumps({
        "project": {"id": "php", "name": "Philippians"},
        "target_language": {"id": "en"}, "resource": {"id": "test"}, "tc_version": "8",
    }), encoding="utf-8")
    (root / "php" / "1.json").write_text(
        json.dumps({"3": PHP_1_3_EN}, ensure_ascii=False), encoding="utf-8",
    )
    # "Θεῷ" (occurrence 1 of 1, strong G2316, lemma θεός) -> "God".
    alignment_payload = {
        "3": {
            "alignments": [{
                "topWords": [{
                    "word": "Θεῷ", "occurrence": 1, "occurrences": 1,
                    "strong": "G2316", "lemma": "θεός", "morph": "Gr,N,,,,,DMS,",
                }],
                "bottomWords": [{"word": "God", "occurrence": 1, "occurrences": 1}],
            }],
            "wordBank": [],
        },
    }
    (alignment_dir / "1.json").write_text(json.dumps(alignment_payload), encoding="utf-8")
    completed_dir = root / ".apps" / "translationCore" / "tools" / "wordAlignment" / "completed" / "1"
    completed_dir.mkdir(parents=True)
    (completed_dir / "3.json").write_text(
        json.dumps({"username": "tester", "modifiedTimestamp": "2026-01-01T00:00:00.000Z"}),
        encoding="utf-8",
    )
    (root / "php.usfm").write_text("\\id PHP\n\\c 1\n\\p\n\\v 3 OLD IMPORTED\n", encoding="utf-8")
    return PassageSemanticRuntime(TranslationCoreProject(root), f"alignment-evidence-{tmp_path.name}")


def test_alignment_precedents_for_range_resolves_a_completed_group(tmp_path: Path) -> None:
    runtime = _runtime_with_completed_alignment(tmp_path)
    source = runtime.source_semantic.build_range("1", "3")
    target = runtime.target_semantic.build_range("1", "3")
    source_token_id = next(
        unit for unit in source["units"]
        if unit.get("semanticFeatures", {}).get("lemma") == "θεός"
    )["tokenInstanceIds"][0]
    target_token_id = next(token for token in target["tokens"] if token["rawForm"] == "God")["id"]

    precedents = alignment_precedents_for_range(runtime, "1", "3")

    assert precedents == [{
        "sourceTokenInstanceIds": [source_token_id],
        "targetTokenInstanceIds": [target_token_id],
    }]


def test_alignment_precedents_for_range_skips_a_verse_still_pending(tmp_path: Path) -> None:
    runtime = _runtime_with_completed_alignment(tmp_path)
    # Undo the "completed" marker written by the fixture: pending alignment
    # data must not become location evidence.
    completed = runtime.project.tc_dir / "tools" / "wordAlignment" / "completed" / "1" / "3.json"
    completed.unlink()
    assert alignment_precedents_for_range(runtime, "1", "3") == []


def test_alignment_precedents_for_range_skips_a_verse_marked_invalid(tmp_path: Path) -> None:
    runtime = _runtime_with_completed_alignment(tmp_path)
    completed = runtime.project.tc_dir / "tools" / "wordAlignment" / "completed" / "1" / "3.json"
    completed.unlink()
    invalid_dir = runtime.project.tc_dir / "tools" / "wordAlignment" / "invalid" / "1"
    invalid_dir.mkdir(parents=True)
    (invalid_dir / "3.json").write_text(
        json.dumps({"timestamp": "2026-01-01T00:00:00.000Z"}), encoding="utf-8",
    )
    assert alignment_precedents_for_range(runtime, "1", "3") == []


def test_completed_alignment_reaches_located_and_stage7_runs_against_it(tmp_path: Path) -> None:
    """Definition-of-done smoke test: a completed same-verse alignment is
    enough on its own (0.65 HUMAN_PRECEDENT-weight evidence, no embedding
    provider configured) to cross `located_minimum`, and Stage 7 can run
    against the resulting LOCATED relationship without erroring."""
    from tc_ai_bridge.meaning_analysis import MeaningAnalysisEngine
    from tc_ai_bridge.semantic_location import SemanticLocationEngine

    runtime = _runtime_with_completed_alignment(tmp_path)
    location = SemanticLocationEngine(runtime).run_range("1", "3")
    source_units = {
        unit["id"]: unit for unit in runtime.source_semantic.build_range("1", "3")["units"]
    }
    relationship = next(
        item for item in location["relationships"]
        if source_units[item["sourceSemanticUnitIds"][0]].get("semanticFeatures", {}).get("lemma")
        == "θεός"
    )
    assert relationship["locationOutcome"] == "LOCATED"
    candidate = next(
        item for item in location["candidates"] if item["id"] == relationship["selectedCandidateId"]
    )
    assert any(
        component["kind"] == "WORD_ALIGNMENT" and component["rawScore"] > 0
        for component in candidate["evidenceComponents"]
    )

    meaning = MeaningAnalysisEngine(runtime).run_range("1", "3", location_run_id=location["id"])
    assert meaning["assessments"]


def test_alignment_precedents_for_range_drops_a_group_with_an_unresolvable_target_word(
    tmp_path: Path,
) -> None:
    runtime = _runtime_with_completed_alignment(tmp_path)
    # Edit the alignment file in place (bypassing the normal save path, the
    # same way a hand-edited legacy project could) so the bottomWord no
    # longer occurs in the current verse text -- ALIGN_TARGET_MISMATCH shape.
    chapter_path = runtime.project.alignment_dir / "1.json"
    payload = json.loads(chapter_path.read_text(encoding="utf-8"))
    payload["3"]["alignments"][0]["bottomWords"][0]["word"] = "nonexistent"
    chapter_path.write_text(json.dumps(payload), encoding="utf-8")
    assert alignment_precedents_for_range(runtime, "1", "3") == []


# --- R3 (review fix): a verse-bridge alignment group is out of scope -------
#
# docs/archive/V11-000a_REVIEW_FIX_PROMPT.md F3: the module docstring already
# promised this ("a tC alignment group stored under a verse-bridge key is
# out of scope for this pass"), but nothing enforced it. Confirmed
# empirically (not assumed) that a bridge's displayed reference reaches
# alignment_precedents_for_range as e.g. "PHP 1:2-3" by running
# rebuild_current_passage directly against a bridge fixture.

def _runtime_with_completed_bridge_alignment(tmp_path: Path) -> PassageSemanticRuntime:
    root = tmp_path / "php-bridge"
    (root / "php").mkdir(parents=True)
    alignment_dir = root / ".apps" / "translationCore" / "alignmentData" / "php"
    alignment_dir.mkdir(parents=True)
    (root / "manifest.json").write_text(json.dumps({
        "project": {"id": "php", "name": "Philippians"},
        "target_language": {"id": "en"}, "resource": {"id": "test"}, "tc_version": "8",
    }), encoding="utf-8")
    (root / "php" / "1.json").write_text(
        json.dumps({"2-3": "a bridged verse of text"}, ensure_ascii=False), encoding="utf-8",
    )
    # A completed alignment stored under the bridge key itself -- exactly
    # the shape a real bridged verse's alignmentData takes.
    alignment_payload = {
        "2-3": {
            "alignments": [{
                "topWords": [{
                    "word": "Θεῷ", "occurrence": 1, "occurrences": 1,
                    "strong": "G2316", "lemma": "θεός", "morph": "Gr,N,,,,,DMS,",
                }],
                "bottomWords": [{"word": "text", "occurrence": 1, "occurrences": 1}],
            }],
            "wordBank": [],
        },
    }
    (alignment_dir / "1.json").write_text(json.dumps(alignment_payload), encoding="utf-8")
    completed_dir = root / ".apps" / "translationCore" / "tools" / "wordAlignment" / "completed" / "1"
    completed_dir.mkdir(parents=True)
    (completed_dir / "2-3.json").write_text(
        json.dumps({"username": "tester", "modifiedTimestamp": "2026-01-01T00:00:00.000Z"}),
        encoding="utf-8",
    )
    (root / "php.usfm").write_text("\\id PHP\n\\c 1\n\\p\n\\v 2-3 OLD BRIDGE WORDING\n", encoding="utf-8")
    return PassageSemanticRuntime(TranslationCoreProject(root), f"bridge-evidence-{tmp_path.name}")


def test_alignment_precedents_for_range_skips_a_verse_bridge_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime_with_completed_bridge_alignment(tmp_path)
    # Confirm the reference this project's bridge actually reaches the
    # function under as, before asserting behaviour against it.
    passage = runtime.rebuild_current_passage("1", "2", "1", "3")
    assert list(passage["targetTextByDisplayedReference"]) == ["PHP 1:2-3"]

    calls = 0
    real_resolve_source = word_alignment_evidence.resolve_source_token_id

    def counting_resolve_source(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_resolve_source(*args, **kwargs)

    monkeypatch.setattr(
        word_alignment_evidence, "resolve_source_token_id", counting_resolve_source,
    )

    precedents = alignment_precedents_for_range(runtime, "1", "2", "1", "3")

    assert precedents == []
    assert calls == 0


# --- version handling (required): ALIGNMENT_EVIDENCE_VERSION -> v2 ---------
#
# R1 changes what this evidence finds relative to the v1 that shipped in
# e3ff0de, so a project analysed under v1 must not have its (now
# evidence-dropped-differently) LOCATION_RUN fingerprints served as cache
# hits by the fixed engine -- correction_verification.py's fingerprint()
# docstring states the same rule for Stage 6B changes generally.

def test_alignment_evidence_version_is_v4() -> None:
    # v4 (#218): cross-verse groups and null decisions. v3 (#119): cross-verse links join the evidence; a v2-fingerprinted run
    # never saw them and must not be served as a cache hit.
    assert word_alignment_evidence.ALIGNMENT_EVIDENCE_VERSION == "tc-word-alignment-v4"


def test_alignment_evidence_version_is_in_policy_versions_output(tmp_path: Path) -> None:
    from tc_ai_bridge.analysis_jobs import AnalysisJobManager

    runtime = _runtime_with_completed_alignment(tmp_path)
    versions = AnalysisJobManager.policy_versions(runtime)
    assert versions["alignmentEvidenceVersion"] == "tc-word-alignment-v4"


# --- #119: Bridge-private cross-verse links as location evidence -------------

PHP_1_4_EN = "always"


def _runtime_with_cross_verse_link(tmp_path: Path) -> PassageSemanticRuntime:
    """PHP 1:3 "God" and 1:4 "always", no tC alignment at all, and one
    Bridge cross-verse link: 1:4's δεήσει is (for the test's purposes)
    realized by 1:3's "God". Single-word verses for the same reason
    PHP_1_3_EN is one word."""
    root = tmp_path / "php-en-xv"
    (root / "php").mkdir(parents=True)
    alignment_dir = root / ".apps" / "translationCore" / "alignmentData" / "php"
    alignment_dir.mkdir(parents=True)
    (root / "manifest.json").write_text(json.dumps({
        "project": {"id": "php", "name": "Philippians"},
        "target_language": {"id": "en"}, "resource": {"id": "test"}, "tc_version": "8",
    }), encoding="utf-8")
    (root / "php" / "1.json").write_text(
        json.dumps({"3": PHP_1_3_EN, "4": PHP_1_4_EN}, ensure_ascii=False), encoding="utf-8",
    )
    (alignment_dir / "1.json").write_text(json.dumps({
        "3": {"alignments": [], "wordBank": [{"word": "God", "occurrence": 1, "occurrences": 1}]},
        "4": {"alignments": [], "wordBank": [{"word": "always", "occurrence": 1, "occurrences": 1}]},
    }), encoding="utf-8")
    (root / "php.usfm").write_text("\\id PHP\n\\c 1\n\\p\n\\v 3 OLD\n\\v 4 OLD\n", encoding="utf-8")
    project = TranslationCoreProject(root)
    project.cross_verse_links.link(
        "1", "4", TokenRef("δεήσει", 1, 1, strong="G11620", lemma="δέησις", morph="Gr,N,,,,,DFS,"),
        "1", "3", TokenRef("God", 1, 1),
    )
    return PassageSemanticRuntime(project, f"cross-verse-evidence-{tmp_path.name}")


def _expected_pair(runtime: PassageSemanticRuntime) -> tuple[str, str]:
    source = runtime.source_semantic.build_range("1", "3", "1", "4")
    target = runtime.target_semantic.build_range("1", "3", "1", "4")
    source_token_id = next(
        unit for unit in source["units"]
        if unit.get("semanticFeatures", {}).get("lemma") == "δέησις"
    )["tokenInstanceIds"][0]
    target_token_id = next(token for token in target["tokens"] if token["rawForm"] == "God")["id"]
    return source_token_id, target_token_id


def test_cross_verse_link_projects_as_a_precedent_across_verses(tmp_path: Path) -> None:
    runtime = _runtime_with_cross_verse_link(tmp_path)
    source_token_id, target_token_id = _expected_pair(runtime)
    precedents = alignment_precedents_for_range(runtime, "1", "3", "1", "4")
    # No completed tC alignment exists, so the link is the only evidence.
    assert precedents == [{
        "sourceTokenInstanceIds": [source_token_id],
        "targetTokenInstanceIds": [target_token_id],
    }]
    # A range touching only one end of the link still resolves the pair; whether
    # it can score is then up to the search spans of that range.
    assert alignment_precedents_for_range(runtime, "1", "3") == precedents


def test_an_invalid_cross_verse_link_contributes_nothing(tmp_path: Path) -> None:
    runtime = _runtime_with_cross_verse_link(tmp_path)
    invalidated = runtime.project.cross_verse_links.invalidate_missing_targets("1", "3", {"God␟1␟1"})
    assert len(invalidated) == 1 and invalidated[0]["state"] == "invalid"
    assert alignment_precedents_for_range(runtime, "1", "3", "1", "4") == []


def test_cross_verse_link_lifts_the_source_from_not_located_to_a_credible_cross_verse_candidate(
    tmp_path: Path,
) -> None:
    """The #54 cross-verse half. Without the link, Stage 6B has nothing for
    δεήσει (1:4) and reports NOT_LOCATED (top candidate 0.16). With the link,
    at the existing 0.65 WORD_ALIGNMENT weight and no embedding provider, every
    top candidate contains 1:3's "God" and clears `located_minimum`.

    Measured, not assumed: on this two-word range the outcome is AMBIGUOUS,
    not LOCATED, because the split pseudo-span pairing "God" with its
    source-verse neighbour "always" also contains the linked token, also
    earns the WORD_ALIGNMENT component, and wins STRUCTURAL_PROXIMITY for
    touching the source verse -- 0.81 against 0.77 for "God" alone, inside
    the 0.07 ambiguity margin. That tie is candidate generation's, not this
    evidence's (filed as #123); no weight or threshold is tuned here. When
    the top candidate does win outright the relationship carries CROSS_VERSE,
    which this test asserts conditionally so a later fix to #123 tightens it
    rather than breaking it."""
    from tc_ai_bridge.semantic_location import LocationSearchPolicy, SemanticLocationEngine

    runtime = _runtime_with_cross_verse_link(tmp_path)
    source_token_id, target_token_id = _expected_pair(runtime)

    def relationship_for(location: dict) -> dict:
        source_units = {
            unit["id"]: unit for unit in runtime.source_semantic.build_range("1", "3", "1", "4")["units"]
        }
        return next(
            item for item in location["relationships"]
            if source_units[item["sourceSemanticUnitIds"][0]].get("semanticFeatures", {}).get("lemma")
            == "δέησις"
        )

    with_link = SemanticLocationEngine(runtime).run_range("1", "3", "1", "4")
    relationship = relationship_for(with_link)
    assert relationship["locationOutcome"] in {"LOCATED", "AMBIGUOUS"}
    top_ids = [relationship["selectedCandidateId"], *relationship["alternativeCandidateIds"]]
    top = sorted(
        (c for c in with_link["candidates"] if c["id"] in top_ids),
        key=lambda c: -c["rawScore"],
    )[:2]
    assert top and all(target_token_id in c["targetTokenInstanceIds"] for c in top)
    assert all(
        any(k["kind"] == "WORD_ALIGNMENT" and k["rawScore"] > 0 for k in c["evidenceComponents"])
        for c in top
    )
    assert top[0]["rawScore"] >= LocationSearchPolicy().located_minimum
    if relationship["locationOutcome"] == "LOCATED":
        assert "CROSS_VERSE" in relationship["properties"]
        assert target_token_id in relationship["targetTokenInstanceIds"]

    # The counterfactual: remove the link, stale the run, and the same source
    # unit is NOT_LOCATED again -- the link is the only thing that located it.
    link_id = runtime.project.cross_verse_links.active_links()[0]["id"]
    runtime.project.cross_verse_links.unlink(link_id)
    runtime.synchronize_alignment_state()
    without_link = SemanticLocationEngine(runtime).run_range("1", "3", "1", "4")
    assert without_link["fingerprint"] != with_link["fingerprint"]
    assert relationship_for(without_link)["locationOutcome"] == "NOT_LOCATED"


def test_alignment_evidence_version_is_a_real_input_to_the_verifier_fingerprint(
    tmp_path: Path,
) -> None:
    """Pinned the way test_analysis_jobs_stage9a4.py pins
    comparisonNormalizationVersion in policy_versions() output -- but
    CorrectionVerificationService.fingerprint() returns a hash, not the raw
    dict, so this proves ALIGNMENT_EVIDENCE_VERSION is a genuine
    differentiating input by reconstructing the exact dict fingerprint()
    hashes and confirming a v1 build of that same dict hashes differently."""
    from tc_ai_bridge.correction_verification import (
        COMPARISON_NORMALIZATION_VERSION,
        CorrectionVerificationPolicy,
        CorrectionVerificationService,
        MEANING_ENGINE_VERSION,
        MEANING_POLICY_VERSION,
        QA_ENGINE_VERSION,
        QA_POLICY_VERSION,
        VERIFICATION_ENGINE_VERSION,
        _json_hash,
    )
    from tc_ai_bridge.semantic_location import LOCATION_ENGINE_VERSION

    runtime = _runtime_with_completed_alignment(tmp_path)
    service = CorrectionVerificationService(runtime, jobs=None)
    real_fingerprint = service.fingerprint()

    def _fingerprint_with(alignment_evidence_version: str) -> str:
        return _json_hash({
            "engine": VERIFICATION_ENGINE_VERSION,
            "policy": CorrectionVerificationPolicy().version,
            "location": LOCATION_ENGINE_VERSION,
            "alignmentEvidence": alignment_evidence_version,
            "meaning": MEANING_ENGINE_VERSION,
            "meaningPolicy": MEANING_POLICY_VERSION,
            "comparisonNormalization": COMPARISON_NORMALIZATION_VERSION,
            "qa": QA_ENGINE_VERSION,
            "qaPolicy": QA_POLICY_VERSION,
        })

    assert real_fingerprint == _fingerprint_with("tc-word-alignment-v4")
    assert real_fingerprint != _fingerprint_with("tc-word-alignment-v2")
