"""
Tests for Phase 6's UAlign-style corpus statistics
(tc_ai_bridge/alignment_statistics.py) — real TranslationCoreProject
fixtures and real Uroman + vendored Smart Edit Distance, not mocks, matching
this project's own standing practice (see docs/BUILD_LOG.md).
"""
import json
import time

import pytest

from bridge_service import BridgeEngine
from greek_room_engine.protocol import EngineRequest
from tc_ai_bridge import alignment_statistics as corpus_stats
from tc_ai_bridge.tc_project import TranslationCoreProject
from tests.support.alignment import _write_book


def call(engine, method, params=None):
    return engine.handle_request(EngineRequest(id="t", method=method, params=params or {})).to_dict()


def _group(top_word: str, bottom_word: str, strong: str = "H430") -> dict:
    return {
        "topWords": [{"word": top_word, "strong": strong, "occurrence": 1, "occurrences": 1}],
        "bottomWords": [{"word": bottom_word, "occurrence": 1, "occurrences": 1}],
    }


@pytest.fixture
def fixture_project(tmp_path):
    root = tmp_path / "rut"
    _write_book(root, "rut", {
        "1": {
            "1": {
                "text": "தேவன்",
                "alignment": {"alignments": [_group("אֱלֹהִ֑ים", "தேவன்")], "wordBank": []},
                "complete": True,
            },
            "2": {
                "text": "தேவன்",
                "alignment": {"alignments": [_group("אֱלֹהִ֑ים", "தேவன்")], "wordBank": []},
                "complete": False,  # NOT complete — must not be counted
            },
        },
    })
    return root


def test_build_corpus_stats_only_counts_completed_verses(fixture_project):
    project = TranslationCoreProject(fixture_project)
    table = corpus_stats.build_corpus_stats(project, include_collection=False)
    assert table.verses_scanned == 1
    assert table.pair_counts[("אֱלֹהִ֑ים", "தேவன்")] == 1
    assert table.source_counts["אֱלֹהִ֑ים"] == 1
    assert table.target_counts["தேவன்"] == 1


def test_the_scan_populates_the_strongs_index_beside_the_surface_one(fixture_project):
    """#138's parallel index is built by the same scan, not a second pass."""
    project = TranslationCoreProject(fixture_project)
    table = corpus_stats.build_corpus_stats(project, include_collection=False)
    # "H430" is what the fixture writes; the key folds padding and homonym
    # letters, so "H0430" and "H430a" would land here too.
    assert table.strong_pair_counts[("H430", "தேவன்")] == 1
    assert table.strong_counts["H430"] == 1
    assert table.strong_pair_stats("H0430", "தேவன்").joint_count == 1
    assert table.strong_pair_stats("H430a", "தேவன்").joint_count == 1


def test_an_inflected_source_form_reaches_its_rendering_through_strongs():
    """The reason the index exists: the surface pair misses, the Strong's pair
    does not."""
    table = corpus_stats.CorpusStatsTable()
    table.strong_pair_counts[("G2316", "கடவுள்")] = 8
    table.strong_counts["G2316"] = 8
    table.target_counts["கடவுள்"] = 8
    table.total_pairs = 8

    assert table.pair_stats("θεοῦ", "கடவுள்", with_sed_boost=False).joint_count == 0
    assert table.strong_pair_stats("G23160", "கடவுள்").translation_probability == 1.0


def test_strong_key_rejects_what_is_not_a_strongs_number():
    assert corpus_stats.strong_key("") == ""
    assert corpus_stats.strong_key("X430") == ""
    assert corpus_stats.strong_key("H") == ""
    assert corpus_stats.strong_key("Hxyz") == ""
    # H and G are kept apart, so an OT and an NT book in one collection cannot
    # collide on the same number.
    assert corpus_stats.strong_key("H430") != corpus_stats.strong_key("G430")
    # UGNT's trailing variant digit is folded, but only on a 5-digit number:
    # a classic 4-digit Greek number must not be truncated into another lemma.
    assert corpus_stats.strong_key("G23160") == corpus_stats.strong_key("G2316")
    assert corpus_stats.strong_key("G2316") == "G2316"
    assert corpus_stats.strong_key("G0040") == "G40"
    # Hebrew numbers are never shortened this way.
    assert corpus_stats.strong_key("H12345") == "H12345"


def test_pair_stats_probability_and_pmi_match_hand_computed_values():
    table = corpus_stats.CorpusStatsTable()
    # 3 instances of source "A": twice paired with target "x", once with "y".
    # 1 extra unrelated instance of target "x" paired with source "B".
    table.source_counts["A"] = 3
    table.source_counts["B"] = 1
    table.target_counts["x"] = 3
    table.target_counts["y"] = 1
    table.pair_counts[("A", "x")] = 2
    table.pair_counts[("A", "y")] = 1
    table.pair_counts[("B", "x")] = 1
    table.total_pairs = 4

    stats = table.pair_stats("A", "x", with_sed_boost=False)
    assert stats.joint_count == 2
    assert stats.source_count == 3
    assert stats.target_count == 3
    assert stats.translation_probability == pytest.approx(2 / 3)
    # PMI(A,x) = log((joint+1)/(expected+1)), expected = (3/4)*(3/4)*4 = 2.25
    assert stats.pmi == pytest.approx(__import__("math").log((2 + 1) / (2.25 + 1)))
    assert stats.sed_cost is None
    assert stats.sed_boosted_probability is None

    unseen = table.pair_stats("B", "y", with_sed_boost=False)
    assert unseen.joint_count == 0
    assert unseen.translation_probability == 0.0


def test_multi_book_collection_aggregates_normalized_siblings_and_skips_lazy(tmp_path):
    primary = tmp_path / "gen"
    _write_book(primary, "gen", {
        "1": {"1": {
            "text": "தேவன்", "alignment": {"alignments": [_group("אֱלֹהִ֑ים", "தேவன்")], "wordBank": []},
            "complete": True,
        }},
    })
    normalized_sibling = tmp_path / "exo"
    _write_book(normalized_sibling, "exo", {
        "1": {"1": {
            "text": "தேவன்", "alignment": {"alignments": [_group("אֱלֹהִ֑ים", "தேவன்")], "wordBank": []},
            "complete": True,
        }},
    })
    lazy_sibling = tmp_path / "lev"
    _write_book(lazy_sibling, "lev", {
        "1": {"1": {
            "text": "தேவன்", "alignment": {"alignments": [_group("אֱלֹהִ֑ים", "தேவன்")], "wordBank": []},
            "complete": True,
        }},
    })
    (lazy_sibling / ".bridge").mkdir(parents=True, exist_ok=True)
    (lazy_sibling / ".bridge" / "lazy-import.json").write_text(
        json.dumps({"sourceCopy": "source.usfm", "bookId": "lev", "metadata": {}}), encoding="utf-8",
    )

    collection = {
        "projects": [
            {"path": str(primary), "bookId": "gen"},
            {"path": str(normalized_sibling), "bookId": "exo"},
            {"path": str(lazy_sibling), "bookId": "lev"},
        ]
    }
    for project_dir in (primary, normalized_sibling, lazy_sibling):
        (project_dir / ".bridge").mkdir(parents=True, exist_ok=True)
        (project_dir / ".bridge" / "collection.json").write_text(json.dumps(collection), encoding="utf-8")

    project = TranslationCoreProject(primary)
    table = corpus_stats.build_corpus_stats(project, include_collection=True)

    assert sorted(table.books_scanned) == ["exo", "gen"]  # lev skipped: still lazy
    assert table.verses_scanned == 2
    assert table.pair_counts[("אֱלֹהִ֑ים", "தேவன்")] == 2


def test_corpus_stats_cache_invalidated_when_a_verse_is_newly_completed(tmp_path):
    root = tmp_path / "rut"
    _write_book(root, "rut", {
        "1": {
            "1": {
                "text": "தேவன்",
                "alignment": {"alignments": [_group("אֱלֹהִ֑ים", "தேவன்")], "wordBank": []},
                "complete": False,
            },
        },
    })
    engine = BridgeEngine()
    call(engine, "project.open", {"path": str(root)})

    # The corpus-stats table is read by the live inconsistent-rendering
    # finding (BridgeEngine._consistency_findings_for_book) and cached per
    # project path; the protocol readers that used to expose it went in #102,
    # so the cache is observed directly here.
    engine._consistency_findings_for_book()
    before = engine._corpus_stats_by_book[str(root)]
    assert before.verses_scanned == 0

    completed = call(engine, "alignment.complete", {"chapter": "1", "verse": "1"})
    assert completed["success"] is True
    assert str(root) not in engine._corpus_stats_by_book  # invalidated by the mutation

    engine._consistency_findings_for_book()
    after = engine._corpus_stats_by_book[str(root)]
    assert after.verses_scanned == 1
    assert after.total_pairs == 1


def test_sed_boost_present_for_real_uroman_and_smart_edit_distance():
    """No mocks: real Uroman romanization + the real vendored Smart Edit
    Distance cost function, same as Phase 5's NamesAdapter tests. A Greek
    proper name (Ἰωάννης, romanizes to "Ioannes") aligned to a target
    spelling romanizing to "Ioanes" (a plausible one-letter transliteration
    slip, the exact shape of thing SED-boost exists to catch) should score
    a real, finite SED cost (verified directly: 0.02, well under the max
    cost of 1) and produce a boosted probability at least as high as the
    plain co-occurrence probability. Not every cross-script "same name"
    pair scores this low — verified separately that a real but more
    divergent romanization gap (Ἰωάννης "Ioannes" vs Tamil யோவான்
    "yoovaan") exceeds SED's max_cost=1 ceiling entirely (cost is None,
    handled by the None/None fallback below), which is expected: SED is
    tuned for near-duplicate spellings (Phase 5's own docstring), not open
    transliteration variance."""
    if corpus_stats._ensure_uroman() is None or corpus_stats._load_sed_module() is None:
        pytest.skip("uroman / vendored smart_edit_distance not available in this environment")

    table = corpus_stats.CorpusStatsTable()
    table.source_counts["Ἰωάννης"] = 1
    table.target_counts["Ioanes"] = 1
    table.pair_counts[("Ἰωάννης", "Ioanes")] = 1
    table.total_pairs = 1

    stats = table.pair_stats("Ἰωάννης", "Ioanes", with_sed_boost=True)
    assert stats.sed_cost is not None
    assert 0.0 <= stats.sed_cost < 1.0
    assert stats.sed_boosted_probability is not None
    assert stats.sed_boosted_probability >= stats.translation_probability


def test_build_corpus_stats_performance_over_a_realistically_sized_completed_corpus(tmp_path):
    """Per this project's own standing rule (measure, don't guess — see the
    Phase 5 bigram-blocking and Phase 4 concurrency investigations), this
    actually times a synthetic but realistically-shaped corpus rather than
    asserting the linear-scan design is fast. 50 chapters x 40 completed
    verses x ~6 token pairs = 2,000 completed verses, comparable to a
    heavily-aligned single large book."""
    root = tmp_path / "big"
    chapters = {}
    for c in range(1, 51):
        verses = {}
        for v in range(1, 41):
            pairs = [
                _group(f"src{c}_{v}_{i}", f"tgt{c}_{v}_{i}")
                for i in range(6)
            ]
            groups = []
            for p in pairs:
                groups.append(p)
            verses[str(v)] = {
                "text": " ".join(g["bottomWords"][0]["word"] for g in groups),
                "alignment": {"alignments": groups, "wordBank": []},
                "complete": True,
            }
        chapters[str(c)] = verses
    _write_book(root, "big", chapters)

    project = TranslationCoreProject(root)
    wall_start, cpu_start = time.perf_counter(), time.process_time()
    table = corpus_stats.build_corpus_stats(project, include_collection=False)
    wall = time.perf_counter() - wall_start
    cpu = time.process_time() - cpu_start

    assert table.verses_scanned == 2000
    assert table.total_pairs == 2000 * 6
    # What this asserts is that the build stays a linear scan: 12,000 pairs
    # cost ~0.2s of CPU on the reference machine (2026-09-15, three runs,
    # 0.19-0.23s), and a comparison that went quadratic over those pairs
    # would be seconds to minutes of CPU, so 2.0s separates the two with room
    # for a slower machine. It is deliberately CPU time, not wall-clock: the
    # same runs took 1.1-1.3s of wall-clock, almost all of it reading 100
    # small files, and under `pytest -n auto` that I/O queues behind other
    # workers -- the old `elapsed < 5.0` wall budget failed there about half
    # the time while the algorithm had not changed (#90). The UI question
    # ("is the first consistency check of a book fast enough?" -- this runs
    # inline on the dispatcher from _consistency_findings_for_book) is a
    # wall-clock question about a real disk, answered in BUILD_LOG, not here.
    assert cpu < 2.0, (
        f"build_corpus_stats used {cpu:.2f}s CPU ({wall:.2f}s wall) for 2000 completed verses"
    )
