from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
from typing import Iterator

import pytest

_SEMANTIC_MAPPING_RESOURCES = Path(__file__).resolve().parents[1] / "resources" / "semantic_mapping"


@pytest.fixture(scope="session")
def stage3_db():
    return _SEMANTIC_MAPPING_RESOURCES / "bridge_semantic_source_v0.3.sqlite"


@pytest.fixture(scope="session")
def tamil_php_usfm():
    return _SEMANTIC_MAPPING_RESOURCES / "regression" / "51PHPIRVTam.SFM"


@pytest.fixture(scope="session")
def tamil_luk_usfm():
    return _SEMANTIC_MAPPING_RESOURCES / "regression" / "43LUKIRVTam.SFM"


# ---------------------------------------------------------------------------
# Directory- and file-level markers, applied automatically so an author cannot
# forget them. Registered (and explained) in engine/pyproject.toml; --strict-markers
# rejects anything not listed there. Per-test `slow` marks live on the tests.
# ---------------------------------------------------------------------------
_DIRECTORY_MARKERS = {
    "connectors": ("desktop", "subprocess"),
    "resources": ("resources",),
}
# `slow` is applied per file, to the files whose tests average more than ~5 s.
# Measured 2026-09-11 (serial baseline, docs/BUILD_LOG.md #74): these files
# plus the two subprocess files hold ~2,700 of the suite's ~3,700 test-seconds, almost
# all of it fixture *setup* that rebuilds the Stage 5-8 pipeline for every test (#82).
_SLOW_FILES = (
    "test_qa_review_service_stage9a.py",        # 660 s / 37 tests
    "test_qa_target_hash_contract_stage8_9b.py", # 465 s / 9
    "test_meaning_failure_eligibility_stage9b.py",  # 377 s / 72
    "test_source_semantic_inventory_stage5.py",  # 254 s / 16
    "test_ai_review_protocol.py",                # 203 s / 11 (as test_ai_explain.py; 9 tests since #105)
    "test_semantic_location_stage6b.py",         # 195 s / 14
    "test_qa_audit_stage8.py",                   # 169 s / 30
    "test_resource_materializer.py",             # 129 s / 10
    "test_ai_review_stale_after_apply.py",       #  96 s / 4
    "test_knowledge_base_ta.py",                 #  54 s / 4
    "test_correction_case_c_production.py",      #  49 s / 1
)
_FILE_MARKERS = {
    "test_stdio_e2e.py": ("subprocess", "slow"),
    "test_stdio_protocol_stream.py": ("subprocess",),
    "test_versification_concurrency.py": ("subprocess", "slow"),
    "test_semantic_mapping_stage3.py": ("stage3db",),
    "test_correction_acceptance_queue_visibility.py": ("external",),
    "test_correction_acceptance_scripts.py": ("external",),
    "test_correction_case_c_production.py": ("external",),
    "test_php_review_walkthrough_stage9a.py": ("external",),
    "test_passage_semantic_foundation.py": ("external",),
    "test_indic_qa_vendor.py": ("packs",),
    "test_indic_qa_packs.py": ("packs",),
    "test_indic_qa_tamil_layer.py": ("packs",),
    "test_indic_qa_general_corpus.py": ("packs",),
    "test_import_indic_qa_labels.py": ("external",),
}


def pytest_collection_modifyitems(config, items):
    for item in items:
        path = Path(str(item.fspath))
        names = set(_DIRECTORY_MARKERS.get(path.parent.name, ()))
        names.update(_FILE_MARKERS.get(path.name, ()))
        if path.name in _SLOW_FILES:
            names.add("slow")
        for name in names:
            item.add_marker(getattr(pytest.mark, name))


# ---------------------------------------------------------------------------
# SQLite durability opt-out. The companion repository opens a fresh connection
# for every method call with `PRAGMA synchronous = FULL` + WAL and closes it
# again; one Stage 5-8 pipeline build does that ~1,800 times and commits ~720
# times, each commit an fsync and each close a WAL checkpoint. Profiled
# 2026-09-11 (#82): ~10 s of an 11 s Tamil PHP build was that I/O, not stage
# logic. Tests do not need crash durability -- tmp_path is thrown away -- so
# they run with fsync off and the rollback journal in memory. Measured on the
# hash-contract file: 98.8 s -> 23.7 s for the same nine tests, nothing asserted
# changed. The product keeps FULL: that database is months of a team's work.
# Set BRIDGE_TEST_DURABLE_SQLITE=1 to run with the product pragmas (the weekly
# serial run in ci.yml is the intended place; #74 step 3).
#
# `WorkbenchRepository` (#75) copies FoundationRepository's connection
# discipline verbatim and every `TranslationCoreProject.__init__` opens one,
# so it gets the same opt-out for the same reason -- without it,
# test_alignment_statistics.py's 5-second corpus-stats ceiling started
# failing under real fsync+WAL cost added to project construction itself.
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True, scope="session")
def _sqlite_without_fsync():
    if os.environ.get("BRIDGE_TEST_DURABLE_SQLITE"):
        yield
        return
    from tc_ai_bridge import passage_semantic_repository as repo
    from tc_ai_bridge import workbench_repository as workbench

    def _make_connect(error_cls):
        @contextmanager
        def _connect(self) -> Iterator[sqlite3.Connection]:
            conn = sqlite3.connect(str(self.path), timeout=5.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA synchronous = OFF")
            conn.execute("PRAGMA journal_mode = MEMORY")
            if self.read_only:
                conn.execute("PRAGMA query_only = ON")
            if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                conn.close()
                raise error_cls("SQLite foreign-key enforcement could not be enabled")
            try:
                yield conn
            finally:
                conn.close()
        return _connect

    patched = {
        repo.FoundationRepository: (repo.FoundationRepository._connect, repo.FoundationError),
        workbench.WorkbenchRepository: (workbench.WorkbenchRepository._connect, workbench.WorkbenchError),
    }
    for cls, (_, error_cls) in patched.items():
        cls._connect = _make_connect(error_cls)
    try:
        yield
    finally:
        for cls, (original, _) in patched.items():
            cls._connect = original
