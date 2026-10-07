"""SQLite persistence for Bridge's passage-semantic companion foundation.

The canonical record schema remains v1. Database migration v2 adds Stage 4
runtime identity, revision, invalidation, reference, and migration metadata.
The database never owns or rewrites Scripture or native translationCore data.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Iterator
import uuid

from .passage_semantic_models import (
    ActorType,
    Cardinality,
    CoverageDimension,
    CorrectionApplicationIntent,
    CorrectionApplicationState,
    CorrectionCreationMode,
    CorrectionProposal,
    CorrectionProposalV2,
    VerificationStatus,
    DependencyRelation,
    EvidenceRecord,
    Exportability,
    LifecycleStatus,
    PassageRecord,
    PolicyBinding,
    QaDisposition,
    QaFinding,
    ReviewStatus,
    SCHEMA_ID,
    SCHEMA_VERSION,
    SemanticCoverageAccount,
    SemanticRelationship,
    SemanticUnit,
    SemanticRelation,
    TokenInstance,
    TokenLineage,
    TokenLayer,
    TokenSide,
    to_wire,
)


DATABASE_SCHEMA_VERSION = 16

# The one authoritative record_type -> table map for the dependency graph.
#
# This existed as three hand-maintained copies (stale propagation, recovery
# integrity checking, and the per-call table lookups). Stage 8 added QA_RUN
# edges and taught only one of them, so every project that had run a QA audit
# failed recovery on its next open and put itself into read-only mode. A type
# that is missing here is silently un-propagated rather than loudly broken,
# which is why RECORD_DEPENDENCY_TABLES is now a single constant and
# test_dependency_graph_invariants asserts every writable type appears in it.
RECORD_DEPENDENCY_TABLES: dict[str, str] = {
    "PASSAGE_RECORD": "passage_records",
    "EVIDENCE_RECORD": "evidence_records",
    "SEMANTIC_RELATIONSHIP": "semantic_relationships",
    "COVERAGE_ACCOUNT": "coverage_accounts",
    "QA_FINDING": "qa_findings",
    "LEXICAL_SOLUTION": "lexical_solutions",
    "CORRECTION_PROPOSAL": "correction_proposals",
    "EXPORTABILITY": "exportability_records",
    "SOURCE_INVENTORY": "source_inventory_runs",
    "TARGET_INVENTORY": "target_inventory_runs",
    "LOCATION_RUN": "semantic_location_runs",
    "LOCATION_RELATIONSHIP": "semantic_location_relationships",
    "MEANING_RUN": "meaning_analysis_runs",
    "MEANING_ASSESSMENT": "meaning_assessments",
    "QA_RUN": "qa_audit_runs",
    "SEMANTIC_UNIT": "semantic_units",
    "TOKEN_INSTANCE": "token_instances",
    "CORRECTION_VERIFICATION": "correction_verifications",
}

# Upstream-only dependency anchors. They are legitimate `depends_on_type`
# values with no record table of their own -- a target reference is a
# coordinate and a source resource is a content hash, not stored records -- so
# recovery must not report them as unknown, and stale propagation has nothing
# to walk past them. Their ids come from target_dependency_id(),
# source_dependency_id() and alignment_dependency_id(); anything else
# appearing upstream is real drift.
RECORD_DEPENDENCY_ANCHOR_TYPES = frozenset({"TARGET_REFERENCE", "SOURCE_RESOURCE", "WORD_ALIGNMENT"})


def _table_columns(conn: sqlite3.Connection, table: str) -> frozenset[str]:
    """Column names for one table.

    Deliberately uncached: it is asked only during stale propagation, and a
    process-wide cache would answer for the wrong schema while a database is
    part-way through a migration.
    """
    return frozenset(row[1] for row in conn.execute(f"PRAGMA table_info({table})"))

# Review priority, not alphabetical order — see _MIGRATION_V8.
_SEVERITY_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}

# The fields a human owns on a QA finding.  A Stage 8 re-run refreshes
# everything else and must leave these exactly as the reviewer left them.
_HUMAN_FINDING_FIELDS = ("qaDisposition", "reviewStatus", "revision")


def _machine_fields(payload: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in payload.items() if k not in _HUMAN_FINDING_FIELDS}


# Entities a human reviewer may decide on directly.  Stage 6B locations and
# Stage 7 meaning assessments are reviewable independently of the Stage 8
# finding synthesized from them, so a reviewer who disagrees with the
# underlying judgement is not forced to accept or reject the QA conclusion.
_REVIEWABLE_TABLES = {
    "QA_FINDING": "qa_findings",
    "LOCATION_RELATIONSHIP": "semantic_location_relationships",
    "MEANING_ASSESSMENT": "meaning_assessments",
    "COVERAGE_ACCOUNT": "coverage_accounts",
    "SEMANTIC_RELATIONSHIP": "semantic_relationships",
}


def _queue_sort_key(finding: QaFinding) -> tuple[int, int, str]:
    """Canonical-order sort key for the Stage 9A review queue.

    Findings that carry no reference sort first under a stable (0, 0, "") key
    rather than being dropped from the queue.  Verse bridges ("3-4") and
    lettered segments ("3a") anchor on their first numeric component, matching
    how finding conversion already treats them elsewhere in the codebase.
    """
    for reference in getattr(finding, "displayed_references", ()):
        _, _, location = str(reference).rpartition(" ")
        chapter, _, verse = location.partition(":")
        digits = re.match(r"\d+", verse.strip())
        if chapter.strip().isdigit() and digits:
            return int(chapter.strip()), int(digits.group()), str(reference)
    return 0, 0, ""


class FoundationError(RuntimeError):
    pass


class FoundationValidationError(FoundationError):
    pass


class FoundationConflict(FoundationError):
    pass


_MIGRATION_V1 = r"""
CREATE TABLE policy_bindings (
    id TEXT PRIMARY KEY,
    confidence_policy_version TEXT NOT NULL,
    calibration_version TEXT NOT NULL,
    audit_policy_version TEXT NOT NULL,
    UNIQUE(confidence_policy_version, calibration_version, audit_policy_version)
);

CREATE TABLE token_lineages (
    id TEXT PRIMARY KEY,
    side TEXT NOT NULL CHECK(side IN ('SOURCE','TARGET')),
    project_id TEXT,
    logical_resource_id TEXT NOT NULL,
    book TEXT NOT NULL,
    canonical_reference_scope_json TEXT NOT NULL,
    token_layer TEXT NOT NULL CHECK(token_layer IN ('ORTHOGRAPHIC','SUBTOKEN','MORPHEME')),
    upstream_identity TEXT,
    provenance TEXT NOT NULL,
    review_status TEXT NOT NULL CHECK(review_status IN ('UNREVIEWED','AI_PROPOSED','HUMAN_APPROVED','HUMAN_REJECTED','HUMAN_MODIFIED','NEEDS_DISCUSSION')),
    lifecycle_status TEXT NOT NULL CHECK(lifecycle_status IN ('ACTIVE','INACTIVE','STALE','SUPERSEDED','QUARANTINED')),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    payload_json TEXT NOT NULL
);

CREATE TABLE token_instances (
    id TEXT PRIMARY KEY,
    lineage_id TEXT NOT NULL REFERENCES token_lineages(id),
    side TEXT NOT NULL CHECK(side IN ('SOURCE','TARGET')),
    token_layer TEXT NOT NULL CHECK(token_layer IN ('ORTHOGRAPHIC','SUBTOKEN','MORPHEME')),
    parent_instance_id TEXT REFERENCES token_instances(id),
    text_revision TEXT,
    instance_fingerprint TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    UNIQUE(lineage_id, instance_fingerprint)
);

CREATE TABLE passage_records (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    book TEXT NOT NULL,
    target_revision TEXT NOT NULL,
    target_content_hash TEXT NOT NULL,
    policy_binding_id TEXT NOT NULL REFERENCES policy_bindings(id),
    lifecycle_status TEXT NOT NULL CHECK(lifecycle_status IN ('ACTIVE','INACTIVE','STALE','SUPERSEDED','QUARANTINED')),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    payload_json TEXT NOT NULL
);

CREATE TABLE evidence_records (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('SOURCE_TEXT','TARGET_TEXT','MORPHOLOGY','TRANSLATION_NOTE','TRANSLATION_WORD','TRANSLATION_WORD_LIST','VERSIFICATION','STRUCTURE','HUMAN_NOTE','AI_RATIONALE')),
    resource_id TEXT NOT NULL,
    resource_version TEXT,
    resource_hash TEXT NOT NULL,
    occurrence_id TEXT,
    validation_status TEXT NOT NULL CHECK(validation_status IN ('NOT_CHECKED','CONSISTENT','SUPPORTING','CONFLICTING','NOT_APPLICABLE')),
    policy_binding_id TEXT NOT NULL REFERENCES policy_bindings(id),
    review_status TEXT NOT NULL CHECK(review_status IN ('UNREVIEWED','AI_PROPOSED','HUMAN_APPROVED','HUMAN_REJECTED','HUMAN_MODIFIED','NEEDS_DISCUSSION')),
    lifecycle_status TEXT NOT NULL CHECK(lifecycle_status IN ('ACTIVE','INACTIVE','STALE','SUPERSEDED','QUARANTINED')),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    payload_json TEXT NOT NULL
);

CREATE TABLE semantic_units (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('SOURCE','TARGET')),
    kind TEXT NOT NULL CHECK(kind IN ('LEXICAL','MORPHOLOGICAL','NEGATION','QUANTIFIER','PARTICIPANT','REFERENT','PREDICATE','SEMANTIC_ROLE','CLAUSE','CLAUSE_RELATION','DISCOURSE_RELATION','IMPLICIT_GRAMMATICAL','IDIOM','CONSTRUCTION','TEMPORAL','SPATIAL')),
    audit_owner_unit_id TEXT NOT NULL,
    audit_eligibility TEXT NOT NULL CHECK(audit_eligibility IN ('ELIGIBLE','CONDITIONAL','AGGREGATE_ONLY','EXCLUDED','REVIEW_ONLY')),
    semantic_obligation TEXT NOT NULL CHECK(semantic_obligation IN ('REQUIRED','CONTEXT_DEPENDENT','GRAMMATICAL','DERIVED','NON_OBLIGATORY','UNCERTAIN')),
    accounting_role TEXT NOT NULL CHECK(accounting_role IN ('PRIMARY','COMPONENT','AGGREGATE','EVIDENCE_ONLY')),
    coverage_dimension TEXT NOT NULL CHECK(coverage_dimension IN ('LEXICAL_CONTENT','POLARITY','QUANTITY','PARTICIPANT','REFERENT','PREDICATION','TEMPORAL_ASPECTUAL','SPATIAL_RELATION','CLAUSE_RELATION','DISCOURSE_RELATION','OTHER')),
    semantic_fingerprint TEXT NOT NULL,
    review_status TEXT NOT NULL CHECK(review_status IN ('UNREVIEWED','AI_PROPOSED','HUMAN_APPROVED','HUMAN_REJECTED','HUMAN_MODIFIED','NEEDS_DISCUSSION')),
    lifecycle_status TEXT NOT NULL CHECK(lifecycle_status IN ('ACTIVE','INACTIVE','STALE','SUPERSEDED','QUARANTINED')),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    payload_json TEXT NOT NULL
);

CREATE TABLE semantic_unit_dependencies (
    parent_unit_id TEXT NOT NULL REFERENCES semantic_units(id),
    child_unit_id TEXT NOT NULL REFERENCES semantic_units(id),
    relation TEXT NOT NULL CHECK(relation IN ('CONTAINS','DEPENDS_ON','DERIVED_FROM','REFINES')),
    payload_json TEXT NOT NULL,
    PRIMARY KEY(parent_unit_id, child_unit_id, relation),
    CHECK(parent_unit_id <> child_unit_id)
);

CREATE TABLE semantic_unit_relations (
    left_unit_id TEXT NOT NULL REFERENCES semantic_units(id),
    right_unit_id TEXT NOT NULL REFERENCES semantic_units(id),
    relation TEXT NOT NULL CHECK(relation IN ('COREFERS_WITH','COEXTENSIVE_WITH','MODIFIES','NEGATES','QUANTIFIES','PARTICIPANT_OF','ARGUMENT_OF','SEMANTICALLY_RELATED')),
    payload_json TEXT NOT NULL,
    PRIMARY KEY(left_unit_id, right_unit_id, relation)
);

CREATE TABLE semantic_relationships (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    review_status TEXT NOT NULL CHECK(review_status IN ('UNREVIEWED','AI_PROPOSED','HUMAN_APPROVED','HUMAN_REJECTED','HUMAN_MODIFIED','NEEDS_DISCUSSION')),
    lifecycle_status TEXT NOT NULL CHECK(lifecycle_status IN ('ACTIVE','INACTIVE','STALE','SUPERSEDED','QUARANTINED')),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    payload_json TEXT NOT NULL
);

CREATE TABLE exportability_records (
    id TEXT PRIMARY KEY,
    relationship_id TEXT NOT NULL REFERENCES semantic_relationships(id),
    format TEXT NOT NULL CHECK(format IN ('BRIDGE','CLEAN_USFM','TRANSLATIONCORE_ALIGNED_USFM','SCRIPTURE_BURRITO')),
    level TEXT NOT NULL CHECK(level IN ('FULL','PARTIAL','NOT_REPRESENTABLE','TEXT_ONLY')),
    policy_binding_id TEXT NOT NULL REFERENCES policy_bindings(id),
    lifecycle_status TEXT NOT NULL CHECK(lifecycle_status IN ('ACTIVE','INACTIVE','STALE','SUPERSEDED','QUARANTINED')),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    payload_json TEXT NOT NULL
);
CREATE UNIQUE INDEX uq_active_exportability
ON exportability_records(relationship_id, format)
WHERE lifecycle_status = 'ACTIVE';

CREATE TABLE qa_findings (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    qa_disposition TEXT NOT NULL CHECK(qa_disposition IN ('UNRESOLVED','CONFIRMED_TRANSLATION_ERROR','ACCEPTABLE_TRANSLATION','FALSE_POSITIVE','NEEDS_DISCUSSION','CORRECTED')),
    policy_binding_id TEXT NOT NULL REFERENCES policy_bindings(id),
    review_status TEXT NOT NULL CHECK(review_status IN ('UNREVIEWED','AI_PROPOSED','HUMAN_APPROVED','HUMAN_REJECTED','HUMAN_MODIFIED','NEEDS_DISCUSSION')),
    lifecycle_status TEXT NOT NULL CHECK(lifecycle_status IN ('ACTIVE','INACTIVE','STALE','SUPERSEDED','QUARANTINED')),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    payload_json TEXT NOT NULL
);

CREATE TABLE coverage_accounts (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    passage_id TEXT NOT NULL,
    direction TEXT NOT NULL CHECK(direction IN ('SOURCE_COVERAGE','TARGET_SUPPORT')),
    audit_owner_unit_id TEXT NOT NULL REFERENCES semantic_units(id),
    coverage_dimension TEXT NOT NULL CHECK(coverage_dimension IN ('LEXICAL_CONTENT','POLARITY','QUANTITY','PARTICIPANT','REFERENT','PREDICATION','TEMPORAL_ASPECTUAL','SPATIAL_RELATION','CLAUSE_RELATION','DISCOURSE_RELATION','OTHER')),
    semantic_fingerprint TEXT NOT NULL,
    finding_id TEXT REFERENCES qa_findings(id),
    review_status TEXT NOT NULL CHECK(review_status IN ('UNREVIEWED','AI_PROPOSED','HUMAN_APPROVED','HUMAN_REJECTED','HUMAN_MODIFIED','NEEDS_DISCUSSION')),
    lifecycle_status TEXT NOT NULL CHECK(lifecycle_status IN ('ACTIVE','INACTIVE','STALE','SUPERSEDED','QUARANTINED')),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    payload_json TEXT NOT NULL
);
CREATE UNIQUE INDEX uq_active_coverage_account
ON coverage_accounts(project_id, passage_id, direction, audit_owner_unit_id, coverage_dimension, semantic_fingerprint)
WHERE lifecycle_status = 'ACTIVE';

CREATE TABLE lexical_solutions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    scope_key TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    source_layer TEXT CHECK(source_layer IN ('ORTHOGRAPHIC','SUBTOKEN','MORPHEME') OR source_layer IS NULL),
    target_layer TEXT CHECK(target_layer IN ('ORTHOGRAPHIC','SUBTOKEN','MORPHEME') OR target_layer IS NULL),
    authoritative INTEGER NOT NULL CHECK(authoritative IN (0,1)),
    policy_binding_id TEXT NOT NULL REFERENCES policy_bindings(id),
    review_status TEXT NOT NULL CHECK(review_status IN ('UNREVIEWED','AI_PROPOSED','HUMAN_APPROVED','HUMAN_REJECTED','HUMAN_MODIFIED','NEEDS_DISCUSSION')),
    lifecycle_status TEXT NOT NULL CHECK(lifecycle_status IN ('ACTIVE','INACTIVE','STALE','SUPERSEDED','QUARANTINED')),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    payload_json TEXT NOT NULL
);
CREATE UNIQUE INDEX uq_authoritative_active_lexical_solution
ON lexical_solutions(project_id, scope_key, profile_id, IFNULL(source_layer,'<NULL>'), IFNULL(target_layer,'<NULL>'))
WHERE authoritative = 1 AND lifecycle_status = 'ACTIVE';

CREATE TABLE lexical_groups (
    id TEXT PRIMARY KEY,
    solution_id TEXT NOT NULL REFERENCES lexical_solutions(id),
    cardinality TEXT NOT NULL CHECK(cardinality IN ('ONE_TO_ONE','ONE_TO_MANY','MANY_TO_ONE','MANY_TO_MANY','SOURCE_TO_NULL','NULL_TO_TARGET')),
    source_layer TEXT CHECK(source_layer IN ('ORTHOGRAPHIC','SUBTOKEN','MORPHEME') OR source_layer IS NULL),
    target_layer TEXT CHECK(target_layer IN ('ORTHOGRAPHIC','SUBTOKEN','MORPHEME') OR target_layer IS NULL),
    source_token_ids_json TEXT NOT NULL,
    target_token_ids_json TEXT NOT NULL,
    alignment_family_id TEXT NOT NULL,
    refines_group_id TEXT REFERENCES lexical_groups(id),
    review_status TEXT NOT NULL CHECK(review_status IN ('UNREVIEWED','AI_PROPOSED','HUMAN_APPROVED','HUMAN_REJECTED','HUMAN_MODIFIED','NEEDS_DISCUSSION')),
    lifecycle_status TEXT NOT NULL CHECK(lifecycle_status IN ('ACTIVE','INACTIVE','STALE','SUPERSEDED','QUARANTINED')),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    payload_json TEXT NOT NULL,
    CHECK(
      (cardinality = 'SOURCE_TO_NULL' AND source_layer IS NOT NULL AND target_layer IS NULL)
      OR (cardinality = 'NULL_TO_TARGET' AND source_layer IS NULL AND target_layer IS NOT NULL)
      OR (cardinality IN ('ONE_TO_ONE','ONE_TO_MANY','MANY_TO_ONE','MANY_TO_MANY') AND source_layer IS NOT NULL AND target_layer IS NOT NULL)
    )
);

CREATE TABLE active_lexical_membership (
    lexical_solution_id TEXT NOT NULL REFERENCES lexical_solutions(id),
    token_side TEXT NOT NULL CHECK(token_side IN ('SOURCE','TARGET')),
    token_layer TEXT NOT NULL CHECK(token_layer IN ('ORTHOGRAPHIC','SUBTOKEN','MORPHEME')),
    token_instance_id TEXT NOT NULL REFERENCES token_instances(id),
    lexical_group_id TEXT NOT NULL REFERENCES lexical_groups(id),
    PRIMARY KEY(lexical_solution_id, token_side, token_layer, token_instance_id)
);

CREATE TABLE correction_proposals (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    qa_finding_id TEXT NOT NULL REFERENCES qa_findings(id),
    current_target_revision TEXT NOT NULL,
    applied_target_revision TEXT,
    policy_binding_id TEXT NOT NULL REFERENCES policy_bindings(id),
    review_status TEXT NOT NULL CHECK(review_status IN ('UNREVIEWED','AI_PROPOSED','HUMAN_APPROVED','HUMAN_REJECTED','HUMAN_MODIFIED','NEEDS_DISCUSSION')),
    lifecycle_status TEXT NOT NULL CHECK(lifecycle_status IN ('ACTIVE','INACTIVE','STALE','SUPERSEDED','QUARANTINED')),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    payload_json TEXT NOT NULL
);

CREATE TABLE review_records (
    id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    previous_review_status TEXT,
    new_review_status TEXT NOT NULL CHECK(new_review_status IN ('UNREVIEWED','AI_PROPOSED','HUMAN_APPROVED','HUMAN_REJECTED','HUMAN_MODIFIED','NEEDS_DISCUSSION')),
    previous_lifecycle_status TEXT,
    new_lifecycle_status TEXT NOT NULL CHECK(new_lifecycle_status IN ('ACTIVE','INACTIVE','STALE','SUPERSEDED','QUARANTINED')),
    previous_qa_disposition TEXT,
    new_qa_disposition TEXT,
    actor_type TEXT NOT NULL CHECK(actor_type IN ('HUMAN','AI','SYSTEM','MIGRATION')),
    actor_id TEXT NOT NULL,
    note TEXT NOT NULL,
    base_revision INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE record_dependencies (
    record_type TEXT NOT NULL,
    record_id TEXT NOT NULL,
    depends_on_type TEXT NOT NULL,
    depends_on_id TEXT NOT NULL,
    PRIMARY KEY(record_type, record_id, depends_on_type, depends_on_id)
);

CREATE TABLE migration_quarantine (
    id TEXT PRIMARY KEY,
    source_kind TEXT NOT NULL,
    source_identity TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


_MIGRATION_V2 = r"""
CREATE TABLE project_metadata (
    project_id TEXT PRIMARY KEY,
    identity_fingerprint TEXT NOT NULL,
    book TEXT NOT NULL,
    target_language_id TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    path_history_json TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL CHECK(lifecycle_status IN ('ACTIVE','INACTIVE','STALE','SUPERSEDED','QUARANTINED')),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    payload_json TEXT NOT NULL
);

CREATE TABLE current_target_revisions (
    project_id TEXT NOT NULL REFERENCES project_metadata(project_id),
    book TEXT NOT NULL,
    displayed_reference TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    text_revision TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(project_id, book, displayed_reference)
);

CREATE TABLE pending_invalidations (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES project_metadata(project_id),
    book TEXT NOT NULL,
    displayed_reference TEXT NOT NULL,
    previous_text_hash TEXT NOT NULL,
    expected_text_hash TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('PREPARED','APPLIED','CANCELLED','FAILED')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX ix_pending_invalidations_state
ON pending_invalidations(project_id, state, created_at);

CREATE TABLE source_resource_locks (
    project_id TEXT NOT NULL REFERENCES project_metadata(project_id),
    book TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    resource_version TEXT NOT NULL,
    resource_hash TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL CHECK(lifecycle_status IN ('ACTIVE','INACTIVE','STALE','SUPERSEDED','QUARANTINED')),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    updated_at TEXT NOT NULL,
    PRIMARY KEY(project_id, book)
);

CREATE TABLE passage_reference_records (
    passage_id TEXT NOT NULL REFERENCES passage_records(id),
    displayed_reference TEXT NOT NULL,
    project_versification TEXT NOT NULL,
    canonical_references_json TEXT NOT NULL,
    mapping_kind TEXT NOT NULL CHECK(mapping_kind IN ('SAME','MAPPED','MERGE','SPLIT','PSALM_TITLE','VERSE_BRIDGE','CHAPTER_SHIFT','AMBIGUOUS_SEGMENT')),
    ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
    PRIMARY KEY(passage_id, displayed_reference)
);

CREATE TABLE token_lineage_candidates (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES project_metadata(project_id),
    old_instance_id TEXT NOT NULL REFERENCES token_instances(id),
    new_instance_id TEXT NOT NULL REFERENCES token_instances(id),
    relation TEXT NOT NULL CHECK(relation IN ('SAME_LINEAGE','POSSIBLE_SUCCESSOR','SPLIT_FROM','MERGED_FROM','NO_CORRESPONDENCE')),
    confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
    reason_code TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL CHECK(lifecycle_status IN ('ACTIVE','INACTIVE','STALE','SUPERSEDED','QUARANTINED')),
    payload_json TEXT NOT NULL,
    UNIQUE(old_instance_id, new_instance_id, relation)
);

CREATE TABLE migration_runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES project_metadata(project_id),
    source_path TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    source_schema TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('IMPORTED','QUARANTINED','SKIPPED','FAILED')),
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    report_json TEXT NOT NULL,
    UNIQUE(project_id, source_path, source_hash)
);

CREATE TABLE runtime_diagnostics (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    code TEXT NOT NULL,
    severity TEXT NOT NULL CHECK(severity IN ('INFO','WARNING','ERROR')),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
"""


_MIGRATION_V3 = r"""
CREATE TABLE source_inventory_runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES project_metadata(project_id),
    book TEXT NOT NULL,
    range_key TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    source_resource_id TEXT NOT NULL,
    source_resource_version TEXT NOT NULL,
    source_resource_hash TEXT NOT NULL,
    audit_policy_version TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL CHECK(lifecycle_status IN ('ACTIVE','INACTIVE','STALE','SUPERSEDED','QUARANTINED')),
    diagnostics_json TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(project_id, book, range_key, fingerprint)
);
CREATE INDEX ix_source_inventory_range
ON source_inventory_runs(project_id, book, range_key, lifecycle_status);

CREATE TABLE source_inventory_tokens (
    inventory_id TEXT NOT NULL REFERENCES source_inventory_runs(id) ON DELETE CASCADE,
    token_instance_id TEXT NOT NULL REFERENCES token_instances(id),
    language_id TEXT NOT NULL,
    upstream_identity TEXT NOT NULL,
    PRIMARY KEY(inventory_id, token_instance_id)
);

CREATE TABLE source_inventory_units (
    inventory_id TEXT NOT NULL REFERENCES source_inventory_runs(id) ON DELETE CASCADE,
    semantic_unit_id TEXT NOT NULL REFERENCES semantic_units(id),
    PRIMARY KEY(inventory_id, semantic_unit_id)
);

CREATE TABLE source_inventory_evidence (
    inventory_id TEXT NOT NULL REFERENCES source_inventory_runs(id) ON DELETE CASCADE,
    evidence_id TEXT NOT NULL REFERENCES evidence_records(id),
    PRIMARY KEY(inventory_id, evidence_id)
);
"""


_MIGRATION_V4 = r"""
CREATE TABLE target_inventory_runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES project_metadata(project_id),
    book TEXT NOT NULL,
    range_key TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    target_revision TEXT NOT NULL,
    target_content_hash TEXT NOT NULL,
    language_id TEXT NOT NULL,
    tokenizer_version TEXT NOT NULL,
    analyzer_registry_version TEXT NOT NULL,
    structure_hash TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL CHECK(lifecycle_status IN ('ACTIVE','INACTIVE','STALE','SUPERSEDED','QUARANTINED')),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    diagnostics_json TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(project_id, book, range_key, fingerprint)
);
CREATE INDEX ix_target_inventory_range
ON target_inventory_runs(project_id, book, range_key, lifecycle_status);

CREATE TABLE target_inventory_tokens (
    inventory_id TEXT NOT NULL REFERENCES target_inventory_runs(id) ON DELETE CASCADE,
    token_instance_id TEXT NOT NULL REFERENCES token_instances(id),
    PRIMARY KEY(inventory_id, token_instance_id)
);
CREATE TABLE target_inventory_units (
    inventory_id TEXT NOT NULL REFERENCES target_inventory_runs(id) ON DELETE CASCADE,
    semantic_unit_id TEXT NOT NULL REFERENCES semantic_units(id),
    PRIMARY KEY(inventory_id, semantic_unit_id)
);
CREATE TABLE target_search_spans (
    id TEXT NOT NULL,
    inventory_id TEXT NOT NULL REFERENCES target_inventory_runs(id) ON DELETE CASCADE,
    displayed_reference TEXT NOT NULL,
    span_kind TEXT NOT NULL CHECK(span_kind IN ('TOKEN','SUBTOKEN','PHRASE','STRUCTURAL_SEGMENT','CLAUSE','SENTENCE')),
    start_code_point INTEGER NOT NULL,
    end_code_point INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(inventory_id, id)
);
CREATE TABLE target_search_neighborhoods (
    id TEXT PRIMARY KEY,
    inventory_id TEXT NOT NULL REFERENCES target_inventory_runs(id) ON DELETE CASCADE,
    scope_kind TEXT NOT NULL CHECK(scope_kind IN ('NORMALIZED_VERSE','STRUCTURAL_SENTENCE','PARAGRAPH','ADJACENT_STRUCTURAL_SEGMENT','SELECTED_PASSAGE','CHAPTER_BOUNDARY_CONTINUATION')),
    payload_json TEXT NOT NULL
);
"""


_MIGRATION_V5 = r"""
CREATE TABLE semantic_location_runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES project_metadata(project_id),
    book TEXT NOT NULL,
    range_key TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    source_inventory_id TEXT NOT NULL REFERENCES source_inventory_runs(id),
    target_inventory_id TEXT NOT NULL REFERENCES target_inventory_runs(id),
    run_status TEXT NOT NULL CHECK(run_status IN ('RUNNING','COMPLETE','FAILED')),
    lifecycle_status TEXT NOT NULL CHECK(lifecycle_status IN ('ACTIVE','INACTIVE','STALE','SUPERSEDED','QUARANTINED')),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(project_id, book, range_key, fingerprint)
);
CREATE INDEX ix_semantic_location_runs_range
ON semantic_location_runs(project_id, book, range_key, lifecycle_status);

CREATE TABLE semantic_location_candidates (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES semantic_location_runs(id) ON DELETE CASCADE,
    source_owner_unit_id TEXT NOT NULL REFERENCES semantic_units(id),
    candidate_rank INTEGER NOT NULL CHECK(candidate_rank >= 1),
    payload_json TEXT NOT NULL
);
CREATE INDEX ix_semantic_location_candidates_owner
ON semantic_location_candidates(run_id, source_owner_unit_id, candidate_rank);

CREATE TABLE semantic_location_relationships (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES semantic_location_runs(id) ON DELETE CASCADE,
    source_owner_unit_id TEXT NOT NULL REFERENCES semantic_units(id),
    location_outcome TEXT NOT NULL CHECK(location_outcome IN ('LOCATED','AMBIGUOUS','NOT_LOCATED','SEARCH_INCOMPLETE','UNSUPPORTED_ANALYSIS')),
    selected_candidate_id TEXT REFERENCES semantic_location_candidates(id),
    review_status TEXT NOT NULL CHECK(review_status IN ('UNREVIEWED','AI_PROPOSED','HUMAN_APPROVED','HUMAN_REJECTED','HUMAN_MODIFIED','NEEDS_DISCUSSION')),
    lifecycle_status TEXT NOT NULL CHECK(lifecycle_status IN ('ACTIVE','INACTIVE','STALE','SUPERSEDED','QUARANTINED')),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    payload_json TEXT NOT NULL
);
CREATE INDEX ix_semantic_location_relationships_run
ON semantic_location_relationships(run_id, source_owner_unit_id);

CREATE TABLE semantic_embedding_cache (
    id TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    model_hash TEXT NOT NULL,
    dimensions INTEGER NOT NULL CHECK(dimensions > 0),
    normalization TEXT NOT NULL,
    vector_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(content_hash, model_hash)
);
"""

_MIGRATION_V6 = r"""
CREATE TABLE meaning_analysis_runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES project_metadata(project_id),
    book TEXT NOT NULL,
    range_key TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    location_run_id TEXT NOT NULL REFERENCES semantic_location_runs(id),
    run_status TEXT NOT NULL CHECK(run_status IN ('RUNNING','COMPLETE','FAILED')),
    lifecycle_status TEXT NOT NULL CHECK(lifecycle_status IN ('ACTIVE','INACTIVE','STALE','SUPERSEDED','QUARANTINED')),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(project_id,book,range_key,fingerprint)
);
CREATE INDEX ix_meaning_analysis_runs_range
ON meaning_analysis_runs(project_id,book,range_key,lifecycle_status);

CREATE TABLE meaning_assessments (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES meaning_analysis_runs(id) ON DELETE CASCADE,
    semantic_location_relationship_id TEXT NOT NULL REFERENCES semantic_location_relationships(id),
    meaning_status TEXT NOT NULL CHECK(meaning_status IN ('PRESERVED','PRESERVED_WITH_RESTRUCTURING','PARTIAL','OVERTRANSLATED','UNDERTRANSLATED','MEANING_SHIFT','CONTRADICTED','UNVERIFIABLE')),
    review_status TEXT NOT NULL CHECK(review_status IN ('UNREVIEWED','AI_PROPOSED','HUMAN_APPROVED','HUMAN_REJECTED','HUMAN_MODIFIED','NEEDS_DISCUSSION')),
    lifecycle_status TEXT NOT NULL CHECK(lifecycle_status IN ('ACTIVE','INACTIVE','STALE','SUPERSEDED','QUARANTINED')),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    payload_json TEXT NOT NULL
);
CREATE INDEX ix_meaning_assessments_run ON meaning_assessments(run_id,meaning_status);

CREATE TABLE meaning_component_assessments (
    id TEXT PRIMARY KEY,
    assessment_id TEXT NOT NULL REFERENCES meaning_assessments(id) ON DELETE CASCADE,
    coverage_dimension TEXT NOT NULL,
    component_status TEXT NOT NULL CHECK(component_status IN ('PRESERVED','PARTIALLY_PRESERVED','ALTERED','CONTRADICTED','TARGET_ADDS_SPECIFICITY','TARGET_WEAKENS_SPECIFICITY','NOT_EXPLICIT_BUT_RECOVERABLE','NOT_DETERMINABLE','NOT_APPLICABLE')),
    payload_json TEXT NOT NULL
);
CREATE INDEX ix_meaning_components_assessment
ON meaning_component_assessments(assessment_id,coverage_dimension);
"""


_MIGRATION_V7 = r"""
CREATE TABLE qa_audit_runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES project_metadata(project_id),
    book TEXT NOT NULL,
    range_key TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    meaning_run_id TEXT NOT NULL REFERENCES meaning_analysis_runs(id),
    run_status TEXT NOT NULL CHECK(run_status IN ('RUNNING','COMPLETE','FAILED')),
    lifecycle_status TEXT NOT NULL CHECK(lifecycle_status IN ('ACTIVE','INACTIVE','STALE','SUPERSEDED','QUARANTINED')),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(project_id,book,range_key,fingerprint)
);
CREATE INDEX ix_qa_audit_runs_range
ON qa_audit_runs(project_id,book,range_key,lifecycle_status);
"""


# Stage 9A: the human review queue orders and filters findings directly.  Every
# value below already exists inside qa_findings.payload_json; they are lifted
# into real columns purely so the queue can page deterministically without
# scanning and re-parsing every payload.  severity_rank exists because
# QaFindingSeverity does not sort lexicographically (CRITICAL < HIGH < MEDIUM
# < LOW < INFO is the review priority, not the alphabetical order).
_MIGRATION_V8 = r"""
ALTER TABLE qa_findings ADD COLUMN book TEXT NOT NULL DEFAULT '';
ALTER TABLE qa_findings ADD COLUMN kind TEXT NOT NULL DEFAULT '';
ALTER TABLE qa_findings ADD COLUMN direction TEXT NOT NULL DEFAULT '';
ALTER TABLE qa_findings ADD COLUMN severity TEXT NOT NULL DEFAULT '';
ALTER TABLE qa_findings ADD COLUMN severity_rank INTEGER NOT NULL DEFAULT 99;
ALTER TABLE qa_findings ADD COLUMN sort_chapter INTEGER NOT NULL DEFAULT 0;
ALTER TABLE qa_findings ADD COLUMN sort_verse INTEGER NOT NULL DEFAULT 0;
ALTER TABLE qa_findings ADD COLUMN displayed_reference TEXT NOT NULL DEFAULT '';

UPDATE qa_findings SET
    book = COALESCE(json_extract(payload_json,'$.book'),''),
    kind = COALESCE(json_extract(payload_json,'$.kind'),''),
    direction = COALESCE(json_extract(payload_json,'$.direction'),''),
    severity = COALESCE(json_extract(payload_json,'$.severity'),''),
    severity_rank = CASE COALESCE(json_extract(payload_json,'$.severity'),'')
        WHEN 'CRITICAL' THEN 0 WHEN 'HIGH' THEN 1 WHEN 'MEDIUM' THEN 2
        WHEN 'LOW' THEN 3 WHEN 'INFO' THEN 4 ELSE 99 END;

CREATE INDEX ix_qa_findings_queue
ON qa_findings(project_id,book,sort_chapter,sort_verse,id);
CREATE INDEX ix_qa_findings_severity
ON qa_findings(project_id,severity_rank,book,sort_chapter,sort_verse,id);
CREATE INDEX ix_qa_findings_filter
ON qa_findings(project_id,lifecycle_status,qa_disposition,kind);
"""


# Stage 9A.4: orchestration jobs are durable independently of the worker
# thread.  The JSON payload is the wire contract; the lifted columns support
# recovery, project scoping, and recent-job lookup without parsing every row.
_MIGRATION_V9 = r"""
CREATE TABLE analysis_jobs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES project_metadata(project_id),
    book TEXT NOT NULL,
    scope_kind TEXT NOT NULL CHECK(scope_kind IN ('CURRENT_PASSAGE','CURRENT_CHAPTER','CURRENT_BOOK','SELECTED_RANGE','AFFECTED')),
    range_key TEXT NOT NULL,
    overall_status TEXT NOT NULL CHECK(overall_status IN ('QUEUED','RUNNING','COMPLETED','COMPLETED_WITH_WARNINGS','FAILED','CANCELLED')),
    target_content_hash TEXT NOT NULL,
    source_resource_hash TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision >= 1),
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    payload_json TEXT NOT NULL
);
CREATE INDEX ix_analysis_jobs_project_recent
ON analysis_jobs(project_id,book,created_at DESC,id DESC);
CREATE INDEX ix_analysis_jobs_scope
ON analysis_jobs(project_id,book,range_key,overall_status,created_at DESC);
CREATE UNIQUE INDEX ux_analysis_jobs_one_active
ON analysis_jobs(project_id)
WHERE overall_status IN ('QUEUED','RUNNING');
"""


# Stage 9A.4: keep the review queue tied to the canonical semantic scope that
# produced each finding.  A separate association table is intentionally used
# instead of target/display verse columns: source coverage may be realized in
# another target verse, and both count and keyset pagination must filter the
# same indexed result set before rows are returned.
_MIGRATION_V10 = r"""
CREATE TABLE qa_finding_scope_references (
    finding_id TEXT NOT NULL REFERENCES qa_findings(id) ON DELETE CASCADE,
    side TEXT NOT NULL CHECK(side IN ('SOURCE','TARGET')),
    canonical_reference TEXT NOT NULL CHECK(length(canonical_reference) > 0),
    PRIMARY KEY(finding_id,side,canonical_reference)
);
CREATE INDEX ix_qa_finding_scope_reference
ON qa_finding_scope_references(canonical_reference,side,finding_id);

INSERT OR IGNORE INTO qa_finding_scope_references(finding_id,side,canonical_reference)
SELECT q.id,'SOURCE',refs.value
FROM qa_findings q
JOIN json_each(COALESCE(json_extract(q.payload_json,'$.sourceSemanticUnitIds'),'[]')) ids
JOIN semantic_units units ON units.id=ids.value
JOIN json_each(COALESCE(json_extract(units.payload_json,'$.canonicalReferences'),'[]')) refs
WHERE typeof(refs.value)='text' AND length(trim(refs.value)) > 0;

INSERT OR IGNORE INTO qa_finding_scope_references(finding_id,side,canonical_reference)
SELECT q.id,'TARGET',refs.value
FROM qa_findings q
JOIN json_each(COALESCE(json_extract(q.payload_json,'$.targetSemanticUnitIds'),'[]')) ids
JOIN semantic_units units ON units.id=ids.value
JOIN json_each(COALESCE(json_extract(units.payload_json,'$.canonicalReferences'),'[]')) refs
WHERE typeof(refs.value)='text' AND length(trim(refs.value)) > 0;
"""


_MIGRATION_V11 = r"""
ALTER TABLE correction_proposals ADD COLUMN proposal_schema_version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE correction_proposals ADD COLUMN verification_status TEXT NOT NULL DEFAULT 'NOT_RUN';
ALTER TABLE correction_proposals ADD COLUMN applicable INTEGER NOT NULL DEFAULT 0;

-- Every pre-v11 proposal predates the exact-span contract: it has no code-point
-- offsets, no target content hash, and no recorded original span text. Bridge
-- must not infer any of those retroactively -- a guessed span is exactly the
-- kind of silent Scripture damage this stage exists to prevent. So legacy rows
-- keep their history and stay readable, are stamped as schema v1, and remain
-- applicable=0 until a human explicitly recreates them against current text.
UPDATE correction_proposals
SET payload_json = json_set(
        json_set(
            json_set(payload_json, '$.proposalSchemaVersion', 1),
            '$.verificationStatus', 'NOT_RUN'
        ),
        '$.legacyApplicabilityBlocked', json('true')
    )
WHERE json_extract(payload_json, '$.proposalSchemaVersion') IS NULL;

CREATE INDEX ix_correction_proposal_finding
ON correction_proposals(qa_finding_id, lifecycle_status, review_status);

-- Stage 9B.0 defines this table; nothing writes Scripture through it yet.
-- Each row is one recoverable attempt to apply a proposal, kept separate from
-- the proposal so a crash between "Scripture written" and "bookkeeping done"
-- is a representable, resumable state rather than an ambiguous one.
CREATE TABLE correction_application_intents (
    application_id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL REFERENCES correction_proposals(id),
    project_id TEXT NOT NULL,
    expected_correction_revision INTEGER NOT NULL CHECK(expected_correction_revision >= 1),
    expected_finding_revision INTEGER NOT NULL CHECK(expected_finding_revision >= 1),
    expected_target_revision TEXT NOT NULL,
    expected_original_text TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN (
        'PREPARED','APPLYING','APPLIED_SCRIPTURE','INVALIDATED','COMPLETED','FAILED','RECOVERY_REQUIRED'
    )),
    created_at TEXT NOT NULL,
    completed_at TEXT,
    failure_code TEXT NOT NULL DEFAULT '',
    failure_detail TEXT NOT NULL DEFAULT '',
    recovery_required INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL
);
CREATE INDEX ix_correction_application_proposal
ON correction_application_intents(proposal_id, state);
"""


# Stage 9B.1 keeps proposal wording history independently of the mutable
# current proposal row.  Every edit/rejection/supersession is append-only and
# carries the full proposal snapshot that was current after that event.
_MIGRATION_V12 = r"""
CREATE TABLE correction_proposal_events (
    id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL REFERENCES correction_proposals(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL CHECK(event_type IN (
        'CREATED','SUGGESTED','EDITED','REJECTED','SUPERSEDED','STALE'
    )),
    actor_type TEXT NOT NULL CHECK(actor_type IN ('HUMAN','AI','SYSTEM','MIGRATION')),
    actor_id TEXT NOT NULL,
    base_revision INTEGER NOT NULL CHECK(base_revision >= 0),
    new_revision INTEGER NOT NULL CHECK(new_revision >= 1),
    reason TEXT NOT NULL DEFAULT '',
    provider_metadata_json TEXT NOT NULL DEFAULT '{}',
    proposal_snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX ix_correction_proposal_events
ON correction_proposal_events(proposal_id,created_at,id);

-- Existing Stage 9B.0 proposals are historical data, not blank slates.  Give
-- each one an auditable migration snapshot without inventing a human action.
INSERT INTO correction_proposal_events(
    id,proposal_id,event_type,actor_type,actor_id,base_revision,new_revision,
    reason,provider_metadata_json,proposal_snapshot_json,created_at
)
SELECT
    'migration-created:' || id,id,'CREATED','MIGRATION','schema-v12',
    CASE WHEN revision > 1 THEN revision - 1 ELSE 0 END,revision,
    'Proposal existed before append-only Stage 9B.1 history.',
    COALESCE(json_extract(payload_json,'$.providerMetadata'),'{}'),
    payload_json,strftime('%Y-%m-%dT%H:%M:%fZ','now')
FROM correction_proposals;
"""


_MIGRATION_V13 = r"""
-- Target token instances are immutable historical identities, but their
-- currentness is explicit. Source instances remain ACTIVE when target text
-- changes because their resource identity is independent.
ALTER TABLE token_instances ADD COLUMN lifecycle_status TEXT NOT NULL DEFAULT 'ACTIVE'
    CHECK(lifecycle_status IN ('ACTIVE','INACTIVE','STALE','SUPERSEDED','QUARANTINED'));
ALTER TABLE token_instances ADD COLUMN revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1);
UPDATE token_instances
SET payload_json=json_set(json_set(payload_json,'$.lifecycleStatus','ACTIVE'),'$.revision',1);

-- The v11 table was design-only. Rebuild it with the complete exact snapshot
-- and preserve any hand-created legacy rows as RECOVERY_REQUIRED: missing
-- coordinates/final hashes must never be guessed into a mutation-ready state.
DROP INDEX IF EXISTS ix_correction_application_proposal;
ALTER TABLE correction_application_intents RENAME TO correction_application_intents_v12;
CREATE TABLE correction_application_intents (
    application_id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL REFERENCES correction_proposals(id),
    finding_id TEXT NOT NULL REFERENCES qa_findings(id),
    project_id TEXT NOT NULL,
    expected_proposal_revision INTEGER NOT NULL CHECK(expected_proposal_revision >= 1),
    expected_finding_revision INTEGER NOT NULL CHECK(expected_finding_revision >= 1),
    target_displayed_reference TEXT NOT NULL,
    canonical_references_json TEXT NOT NULL,
    source_provenance_references_json TEXT NOT NULL,
    expected_target_revision TEXT NOT NULL,
    expected_target_content_hash TEXT NOT NULL,
    expected_start_code_point INTEGER NOT NULL CHECK(expected_start_code_point >= 0),
    expected_end_code_point INTEGER NOT NULL CHECK(expected_end_code_point >= expected_start_code_point),
    expected_original_text TEXT NOT NULL,
    replacement_text_snapshot TEXT NOT NULL,
    intended_final_verse_hash TEXT NOT NULL,
    pending_invalidation_id TEXT NOT NULL,
    translation_core_journal_transaction_id TEXT NOT NULL DEFAULT '',
    actor_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    application_state TEXT NOT NULL CHECK(application_state IN (
        'PREPARED','APPLYING','APPLIED_SCRIPTURE','INVALIDATED','COMPLETED','FAILED','RECOVERY_REQUIRED'
    )),
    state_revision INTEGER NOT NULL CHECK(state_revision >= 1),
    failure_code TEXT NOT NULL DEFAULT '',
    recovery_metadata_json TEXT NOT NULL DEFAULT '{}',
    result_metadata_json TEXT NOT NULL DEFAULT '{}',
    payload_json TEXT NOT NULL,
    UNIQUE(proposal_id, expected_proposal_revision)
);
INSERT INTO correction_application_intents(
    application_id,proposal_id,finding_id,project_id,expected_proposal_revision,
    expected_finding_revision,target_displayed_reference,canonical_references_json,
    source_provenance_references_json,expected_target_revision,expected_target_content_hash,
    expected_start_code_point,expected_end_code_point,expected_original_text,
    replacement_text_snapshot,intended_final_verse_hash,pending_invalidation_id,
    translation_core_journal_transaction_id,actor_json,created_at,updated_at,completed_at,
    application_state,state_revision,failure_code,recovery_metadata_json,result_metadata_json,payload_json
)
SELECT old.application_id,old.proposal_id,p.qa_finding_id,old.project_id,
       old.expected_correction_revision,old.expected_finding_revision,
       COALESCE(json_extract(p.payload_json,'$.intent.affectedTargetSpan.displayedReference'),''),
       COALESCE(json_extract(p.payload_json,'$.intent.affectedTargetSpan.canonicalReferences'),'[]'),
       COALESCE(json_extract(p.payload_json,'$.affectedReferences'),'[]'),
       old.expected_target_revision,
       COALESCE(json_extract(old.payload_json,'$.expectedTargetContentHashes[0]'),
                json_extract(p.payload_json,'$.intent.affectedTargetSpan.targetContentHash'),''),
       COALESCE(json_extract(p.payload_json,'$.intent.affectedTargetSpan.startCodePoint'),0),
       COALESCE(json_extract(p.payload_json,'$.intent.affectedTargetSpan.endCodePoint'),0),
       old.expected_original_text,COALESCE(json_extract(p.payload_json,'$.proposedText'),''),'','', '',
       json_object('actorType','MIGRATION','actorId','schema-v13'),old.created_at,old.created_at,
       old.completed_at,'RECOVERY_REQUIRED',1,'MIGRATED_INCOMPLETE_APPLICATION_SNAPSHOT',
       json_object('legacyPayload',json(old.payload_json)),'{}',
       json_object('applicationId',old.application_id,'proposalId',old.proposal_id,
                   'findingId',p.qa_finding_id,'projectId',old.project_id,
                   'expectedProposalRevision',old.expected_correction_revision,
                   'expectedFindingRevision',old.expected_finding_revision,
                   'targetDisplayedReference',COALESCE(json_extract(p.payload_json,'$.intent.affectedTargetSpan.displayedReference'),''),
                   'canonicalReferences',json(COALESCE(json_extract(p.payload_json,'$.intent.affectedTargetSpan.canonicalReferences'),'[]')),
                   'sourceProvenanceReferences',json(COALESCE(json_extract(p.payload_json,'$.affectedReferences'),'[]')),
                   'expectedTargetRevision',old.expected_target_revision,
                   'expectedTargetContentHash',COALESCE(json_extract(old.payload_json,'$.expectedTargetContentHashes[0]'),''),
                   'expectedStartCodePoint',COALESCE(json_extract(p.payload_json,'$.intent.affectedTargetSpan.startCodePoint'),0),
                   'expectedEndCodePoint',COALESCE(json_extract(p.payload_json,'$.intent.affectedTargetSpan.endCodePoint'),0),
                   'expectedOriginalText',old.expected_original_text,'replacementTextSnapshot',COALESCE(json_extract(p.payload_json,'$.proposedText'),''),
                   'intendedFinalVerseHash','','pendingInvalidationId','','translationCoreJournalTransactionId','',
                   'actor',json_object('actorType','MIGRATION','actorId','schema-v13'),
                   'createdAt',old.created_at,'updatedAt',old.created_at,'applicationState','RECOVERY_REQUIRED',
                   'stateRevision',1,'failureCode','MIGRATED_INCOMPLETE_APPLICATION_SNAPSHOT',
                   'recoveryMetadata',json_object('legacyPayload',json(old.payload_json)),'resultMetadata',json_object())
FROM correction_application_intents_v12 old
JOIN correction_proposals p ON p.id=old.proposal_id;
DROP TABLE correction_application_intents_v12;
CREATE INDEX ix_correction_application_proposal
ON correction_application_intents(proposal_id,application_state);
CREATE INDEX ix_correction_application_incomplete
ON correction_application_intents(project_id,application_state,created_at);
CREATE UNIQUE INDEX ux_correction_application_pending_invalidation
ON correction_application_intents(pending_invalidation_id)
WHERE pending_invalidation_id <> '';
CREATE UNIQUE INDEX ux_correction_application_tc_journal
ON correction_application_intents(translation_core_journal_transaction_id)
WHERE translation_core_journal_transaction_id <> '';

-- Existing target instances and units gain the same dependency edges new
-- writes receive. The U+241F delimiter is the repository's canonical anchor.
INSERT OR IGNORE INTO record_dependencies(record_type,record_id,depends_on_type,depends_on_id)
SELECT 'TOKEN_INSTANCE',id,'TARGET_REFERENCE',
       json_extract(payload_json,'$.projectId') || '␟' || upper(json_extract(payload_json,'$.book')) || '␟' || json_extract(payload_json,'$.displayedReference')
FROM token_instances
WHERE side='TARGET' AND json_extract(payload_json,'$.projectId') IS NOT NULL;
INSERT OR IGNORE INTO record_dependencies(record_type,record_id,depends_on_type,depends_on_id)
SELECT 'SEMANTIC_UNIT',semantic_unit_id,'TARGET_INVENTORY',inventory_id
FROM target_inventory_units;
"""


# Stage 9B.4 needs a *normalized* verification record, not another JSON blob on
# the application ledger, for one invariant that only the database can enforce:
#
#   At most one verification may exist for a given
#   (application, analysis job, target content hash, verifier fingerprint),
#   and a repeated request must return that same record.
#
# Verification is reached by a button a reviewer can double-click while a
# read-modify-write of `result_metadata_json` is in flight, so an append into
# that blob can duplicate under exactly the concurrency the UNIQUE index below
# rules out -- the same reasoning that gave applications
# UNIQUE(proposal_id, expected_proposal_revision) in v13. Acknowledgement also
# needs its own CAS column so "mark corrected" fails closed against a
# verification that went stale between render and click, and history has to
# stay queryable per application after the current record is superseded.
_MIGRATION_V14 = r"""
CREATE TABLE correction_verifications (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    application_id TEXT NOT NULL REFERENCES correction_application_intents(application_id),
    proposal_id TEXT NOT NULL REFERENCES correction_proposals(id),
    proposal_revision INTEGER NOT NULL CHECK(proposal_revision >= 0),
    finding_id TEXT NOT NULL REFERENCES qa_findings(id),
    analysis_job_id TEXT NOT NULL,
    target_revision TEXT NOT NULL,
    target_content_hash TEXT NOT NULL,
    verifier_fingerprint TEXT NOT NULL,
    result TEXT NOT NULL CHECK(result IN ('PASSED','FAILED','UNCERTAIN')),
    confidence REAL NOT NULL,
    reason_codes_json TEXT NOT NULL DEFAULT '[]',
    lifecycle_status TEXT NOT NULL DEFAULT 'ACTIVE'
        CHECK(lifecycle_status IN ('ACTIVE','INACTIVE','STALE','SUPERSEDED','QUARANTINED')),
    acknowledged_at TEXT,
    acknowledged_by TEXT,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    UNIQUE(application_id, analysis_job_id, target_content_hash, verifier_fingerprint)
);
CREATE INDEX ix_correction_verifications_application
ON correction_verifications(application_id, created_at, id);
CREATE INDEX ix_correction_verifications_finding
ON correction_verifications(finding_id, created_at);
"""


# V11-003 (#57): a human-authored correction proposal is now approved by
# default when created -- no AI wording, no separate Edit->Save round-trip
# needed just to earn "Review application" (see
# docs/archive/V11-003_ISSUE57_PROMPT.md). An existing UNREVIEWED + HUMAN_AUTHORED
# proposal is lazily reseeded to HUMAN_APPROVED the next time it is read
# (FoundationRepository.correction_proposal /
# correction_proposals_for_finding), and that reseed is audited with its own
# event type, REVIEW_STATUS_BACKFILLED, attributed to ActorType.MIGRATION.
# SQLite cannot widen a CHECK constraint in place, so the table is rebuilt
# the same way v13 rebuilt correction_application_intents: rename, recreate
# with the wider constraint, copy every row across unchanged, drop the old
# table. No column, index, or row changes -- purely admitting one more
# event_type literal.
_MIGRATION_V15 = r"""
ALTER TABLE correction_proposal_events RENAME TO correction_proposal_events_v14;
DROP INDEX IF EXISTS ix_correction_proposal_events;
CREATE TABLE correction_proposal_events (
    id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL REFERENCES correction_proposals(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL CHECK(event_type IN (
        'CREATED','SUGGESTED','EDITED','REJECTED','SUPERSEDED','STALE',
        'REVIEW_STATUS_BACKFILLED'
    )),
    actor_type TEXT NOT NULL CHECK(actor_type IN ('HUMAN','AI','SYSTEM','MIGRATION')),
    actor_id TEXT NOT NULL,
    base_revision INTEGER NOT NULL CHECK(base_revision >= 0),
    new_revision INTEGER NOT NULL CHECK(new_revision >= 1),
    reason TEXT NOT NULL DEFAULT '',
    provider_metadata_json TEXT NOT NULL DEFAULT '{}',
    proposal_snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
INSERT INTO correction_proposal_events
SELECT id,proposal_id,event_type,actor_type,actor_id,base_revision,new_revision,
       reason,provider_metadata_json,proposal_snapshot_json,created_at
FROM correction_proposal_events_v14;
DROP TABLE correction_proposal_events_v14;
CREATE INDEX ix_correction_proposal_events
ON correction_proposal_events(proposal_id,created_at,id);
"""

# v16 (#84): an explicit per-ledger sequence, replacing the `ORDER BY
# created_at, rowid` tie-break 7bf0a66 put in as a stopgap.
#
# The three append-only ledgers are read back in write order. They were ordered
# by `created_at` alone, and on Windows/CPython `datetime.now()` advances only
# about every 15 ms, so events written to one record inside a single tick came
# back in random order -- a proposal history that read CREATED, REJECTED,
# EDITED. The rowid tie-break fixed the symptom but leans on an implementation
# detail: rowids are reassigned by any table rebuild, and v13 and v15 both
# rebuild one of these tables. It held (verified empirically against v15's
# rebuild), but nothing would have caught it if it had not.
#
# `seq` is assigned inside the writing transaction as MAX(seq)+1, which is
# ordered, gap-free per table, and independent of both clock resolution and
# storage internals. Backfilled from rowid, which is correct because that is
# exactly the order the tie-break was already reading and preserving.
_MIGRATION_V16 = r"""
ALTER TABLE correction_proposal_events ADD COLUMN seq INTEGER;
ALTER TABLE correction_verifications ADD COLUMN seq INTEGER;
ALTER TABLE review_records ADD COLUMN seq INTEGER;

UPDATE correction_proposal_events SET seq = rowid;
UPDATE correction_verifications SET seq = rowid;
UPDATE review_records SET seq = rowid;

CREATE INDEX ix_correction_proposal_events_seq
    ON correction_proposal_events(proposal_id, seq);
CREATE INDEX ix_correction_verifications_seq
    ON correction_verifications(application_id, seq);
CREATE INDEX ix_review_records_seq
    ON review_records(entity_type, entity_id, seq);
"""


class FoundationRepository:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.read_only = False
        self._migrate()
        self.recovery_check()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA synchronous = FULL")
        conn.execute("PRAGMA journal_mode = WAL")
        if self.read_only:
            conn.execute("PRAGMA query_only = ON")
        if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            conn.close()
            raise FoundationError("SQLite foreign-key enforcement could not be enabled")
        try:
            yield conn
        finally:
            conn.close()

    def _migrate(self) -> None:
        with self._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, schema_id TEXT NOT NULL, applied_at TEXT NOT NULL)")
            current = conn.execute("SELECT COALESCE(MAX(version),0) FROM schema_migrations").fetchone()[0]
            if current > DATABASE_SCHEMA_VERSION:
                raise FoundationError(
                    f"Companion schema {current} is newer than supported v{DATABASE_SCHEMA_VERSION}"
                )
            if current < 1:
                self._apply_migration(conn, 1, _MIGRATION_V1)
                current = 1
            if current < 2:
                self._backup_before_migration(conn, current, 2)
                self._apply_migration(conn, 2, _MIGRATION_V2)
                current = 2
            if current < 3:
                self._backup_before_migration(conn, current, 3)
                self._apply_migration(conn, 3, _MIGRATION_V3)
                current = 3
            if current < 4:
                self._backup_before_migration(conn, current, 4)
                self._apply_migration(conn, 4, _MIGRATION_V4)
                current = 4
            if current < 5:
                self._backup_before_migration(conn, current, 5)
                self._apply_migration(conn, 5, _MIGRATION_V5)
                current = 5
            if current < 6:
                self._backup_before_migration(conn, current, 6)
                self._apply_migration(conn, 6, _MIGRATION_V6)
                current = 6
            if current < 7:
                self._backup_before_migration(conn, current, 7)
                self._apply_migration(conn, 7, _MIGRATION_V7)
                current = 7
            if current < 8:
                self._backup_before_migration(conn, current, 8)
                self._apply_migration(conn, 8, _MIGRATION_V8)
                current = 8
            if current < 9:
                self._backup_before_migration(conn, current, 9)
                self._apply_migration(conn, 9, _MIGRATION_V9)
                current = 9
            if current < 10:
                self._backup_before_migration(conn, current, 10)
                self._apply_migration(conn, 10, _MIGRATION_V10)
                current = 10
            if current < 11:
                self._backup_before_migration(conn, current, 11)
                self._apply_migration(conn, 11, _MIGRATION_V11)
                current = 11
            if current < 12:
                self._backup_before_migration(conn, current, 12)
                self._apply_migration(conn, 12, _MIGRATION_V12)
                current = 12
            if current < 13:
                self._backup_before_migration(conn, current, 13)
                self._apply_migration(conn, 13, _MIGRATION_V13)
                current = 13
            if current < 14:
                self._backup_before_migration(conn, current, 14)
                self._apply_migration(conn, 14, _MIGRATION_V14)
                current = 14
            if current < 15:
                self._backup_before_migration(conn, current, 15)
                self._apply_migration(conn, 15, _MIGRATION_V15)
                current = 15
            if current < 16:
                self._backup_before_migration(conn, current, 16)
                self._apply_migration(conn, 16, _MIGRATION_V16)
        self._ensure_policy(PolicyBinding.foundation_v1())

    def _backup_before_migration(
        self, conn: sqlite3.Connection, current: int, target: int,
    ) -> None:
        """Create a consistent, inspectable backup before upgrading an existing DB."""
        if current <= 0 or not self.path.is_file():
            return
        root = self.path.parent / "backups"
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        directory = root / f"pre-schema-v{target}-{stamp}"
        directory.mkdir(parents=True, exist_ok=False)
        backup_db = directory / "bridge-semantic-v1.sqlite3"
        destination = sqlite3.connect(str(backup_db))
        try:
            conn.backup(destination)
        finally:
            destination.close()
        digest = hashlib.sha256(backup_db.read_bytes()).hexdigest()
        manifest = {
            "reason": f"automatic backup before database migration v{current} to v{target}",
            "schemaId": SCHEMA_ID, "schemaVersion": current,
            "source": str(self.path), "sourceDatabase": str(self.path),
            "databaseSchemaVersion": current,
            "targetDatabaseSchemaVersion": target, "sha256": digest, "createdAt": self._now(),
        }
        (directory / "backup-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )

    def _apply_migration(self, conn: sqlite3.Connection, version: int, script: str) -> None:
        schema_id = SCHEMA_ID.replace("'", "''")
        applied_at = self._now().replace("'", "''")
        conn.executescript(
            "BEGIN IMMEDIATE;\n"
            + script
            + f"\nINSERT INTO schema_migrations(version,schema_id,applied_at) "
              f"VALUES({version},'{schema_id}','{applied_at}');\n"
            + f"PRAGMA user_version = {version};\nCOMMIT;"
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _policy_id(policy: PolicyBinding) -> str:
        raw = "\u241f".join((policy.confidence_policy_version, policy.calibration_version, policy.audit_policy_version))
        return "policy-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    def _ensure_policy(self, policy: PolicyBinding) -> str:
        policy_id = self._policy_id(policy)
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO policy_bindings VALUES(?,?,?,?)",
                (policy_id, policy.confidence_policy_version, policy.calibration_version, policy.audit_policy_version),
            )
            conn.commit()
        return policy_id

    def schema_version(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT COALESCE(MAX(version),0) FROM schema_migrations").fetchone()[0])

    def bind_project_metadata(
        self, *, project_id: str, identity_fingerprint: str, book: str,
        target_language_id: str, resource_id: str, path: str,
    ) -> dict[str, Any]:
        now = self._now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute("SELECT * FROM project_metadata").fetchall()
            if rows and all(row["project_id"] != project_id for row in rows):
                raise FoundationConflict(
                    "Companion database belongs to a different Bridge project; refusing to merge identities"
                )
            row = next((item for item in rows if item["project_id"] == project_id), None)
            if row is None:
                paths = [path]
                payload = {
                    "projectId": project_id, "identityFingerprint": identity_fingerprint,
                    "book": book, "targetLanguageId": target_language_id,
                    "resourceId": resource_id, "pathHistory": paths,
                    "createdAt": now, "updatedAt": now,
                    "lifecycleStatus": "ACTIVE", "revision": 1,
                }
                conn.execute(
                    "INSERT INTO project_metadata VALUES(?,?,?,?,?,?,?,?,?)",
                    (project_id, identity_fingerprint, book, target_language_id, resource_id,
                     json.dumps(paths, ensure_ascii=False), "ACTIVE", 1,
                     json.dumps(payload, ensure_ascii=False)),
                )
            else:
                if row["identity_fingerprint"] != identity_fingerprint:
                    raise FoundationConflict(
                        "Project identity metadata conflicts with this companion database"
                    )
                paths = json.loads(row["path_history_json"])
                path_added = path not in paths
                if path not in paths:
                    paths.append(path)
                metadata_changed = (
                    row["book"] != book
                    or row["target_language_id"] != target_language_id
                    or row["resource_id"] != resource_id
                )
                if path_added or metadata_changed:
                    payload = json.loads(row["payload_json"])
                    payload.update({
                        "book": book, "targetLanguageId": target_language_id,
                        "resourceId": resource_id, "pathHistory": paths,
                        "updatedAt": now, "revision": int(row["revision"]) + 1,
                    })
                    conn.execute(
                        "UPDATE project_metadata SET book=?,target_language_id=?,resource_id=?,"
                        "path_history_json=?,revision=revision+1,payload_json=? WHERE project_id=?",
                        (book, target_language_id, resource_id, json.dumps(paths, ensure_ascii=False),
                         json.dumps(payload, ensure_ascii=False), project_id),
                    )
            conn.commit()
        return self.project_metadata(project_id)

    def project_metadata(self, project_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM project_metadata WHERE project_id=?", (project_id,)
            ).fetchone()
        if row is None:
            raise FoundationValidationError(f"Unknown semantic project identity: {project_id}")
        return json.loads(row[0])

    def current_target_revision(
        self, project_id: str, book: str, displayed_reference: str,
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM current_target_revisions WHERE project_id=? AND book=? "
                "AND displayed_reference=?", (project_id, book, displayed_reference),
            ).fetchone()
        if row is None:
            return None
        return {
            "projectId": row["project_id"], "book": row["book"],
            "displayedReference": row["displayed_reference"], "textHash": row["text_hash"],
            "textRevision": row["text_revision"], "updatedAt": row["updated_at"],
        }

    def current_target_revisions(self, project_id: str, book: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM current_target_revisions WHERE project_id=? AND book=? "
                "ORDER BY displayed_reference", (project_id, book),
            ).fetchall()
        return [{
            "projectId": row["project_id"], "book": row["book"],
            "displayedReference": row["displayed_reference"], "textHash": row["text_hash"],
            "textRevision": row["text_revision"], "updatedAt": row["updated_at"],
        } for row in rows]

    def establish_target_revision(
        self, *, project_id: str, book: str, displayed_reference: str,
        text_hash: str, text_revision: str,
    ) -> None:
        self.establish_target_revisions_bulk(
            project_id=project_id, book=book,
            revisions=[(displayed_reference, text_hash, text_revision)],
        )

    def establish_target_revisions_bulk(
        self, *, project_id: str, book: str, revisions: list[tuple[str, str, str]],
    ) -> None:
        """Establish many ``(displayed_reference, text_hash, text_revision)`` rows
        in one transaction, one timestamp.

        The first open of a book establishes a revision for every verse it
        has -- 1,533 for Genesis -- and one commit (one fsync) each measured
        20.8 s of a 22 s open. The per-verse path above is now this with a
        one-element list. Same upsert, so re-establishing is still idempotent.
        """
        if not revisions:
            return
        now = self._now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.executemany(
                "INSERT INTO current_target_revisions VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(project_id,book,displayed_reference) DO UPDATE SET "
                "text_hash=excluded.text_hash,text_revision=excluded.text_revision,updated_at=excluded.updated_at",
                [(project_id, book, reference, text_hash, text_revision, now)
                 for reference, text_hash, text_revision in revisions],
            )
            conn.commit()

    def prepare_target_invalidation(
        self, *, project_id: str, book: str, displayed_reference: str,
        previous_text_hash: str, expected_text_hash: str,
    ) -> str:
        intent_id = str(uuid.uuid4())
        now = self._now()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO pending_invalidations VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (intent_id, project_id, book, displayed_reference, previous_text_hash,
                 expected_text_hash, "PREPARED", 0, "", now, now),
            )
            conn.commit()
        return intent_id

    def cancel_target_invalidation(self, intent_id: str, reason: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE pending_invalidations SET state='CANCELLED',last_error=?,updated_at=? "
                "WHERE id=? AND state='PREPARED'", (reason, self._now(), intent_id),
            )
            conn.commit()

    def apply_target_invalidation(
        self, intent_id: str, *, actual_text_hash: str, text_revision: str,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM pending_invalidations WHERE id=?", (intent_id,)).fetchone()
            if row is None:
                raise FoundationValidationError(f"Unknown pending invalidation: {intent_id}")
            if row["state"] == "APPLIED":
                return {"intentId": intent_id, "state": "APPLIED", "staled": 0}
            if row["state"] != "PREPARED":
                raise FoundationConflict(f"Pending invalidation is already {row['state']}")
            current = conn.execute(
                "SELECT text_hash FROM current_target_revisions WHERE project_id=? AND book=? "
                "AND displayed_reference=?",
                (row["project_id"], row["book"], row["displayed_reference"]),
            ).fetchone()
            if current is not None and current["text_hash"] != row["previous_text_hash"]:
                conn.execute(
                    "UPDATE pending_invalidations SET state='FAILED',attempt_count=attempt_count+1,"
                    "last_error=?,updated_at=? WHERE id=?",
                    ("Target revision changed after invalidation intent was prepared", self._now(), intent_id),
                )
                conn.commit()
                raise FoundationConflict(
                    "Target revision changed after invalidation intent was prepared"
                )
            if actual_text_hash != row["expected_text_hash"]:
                conn.execute(
                    "UPDATE pending_invalidations SET state='FAILED',attempt_count=attempt_count+1,"
                    "last_error=?,updated_at=? WHERE id=?",
                    ("Current target hash does not match the prepared edit", self._now(), intent_id),
                )
                conn.commit()
                raise FoundationConflict("Current target hash does not match the prepared edit")
            dependency_id = self.target_dependency_id(
                row["project_id"], row["book"], row["displayed_reference"]
            )
            staled = self._stale_generic_dependencies(
                conn, "TARGET_REFERENCE", dependency_id
            )
            conn.execute(
                "INSERT INTO current_target_revisions VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(project_id,book,displayed_reference) DO UPDATE SET "
                "text_hash=excluded.text_hash,text_revision=excluded.text_revision,updated_at=excluded.updated_at",
                (row["project_id"], row["book"], row["displayed_reference"],
                 actual_text_hash, text_revision, self._now()),
            )
            conn.execute(
                "UPDATE pending_invalidations SET state='APPLIED',attempt_count=attempt_count+1,"
                "last_error='',updated_at=? WHERE id=?", (self._now(), intent_id),
            )
            conn.commit()
        return {"intentId": intent_id, "state": "APPLIED", "staled": staled}

    def pending_invalidations(self, project_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM pending_invalidations WHERE project_id=? AND state='PREPARED' "
                "ORDER BY created_at,id", (project_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def target_invalidation(self, intent_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM pending_invalidations WHERE id=?", (intent_id,),
            ).fetchone()
        return None if row is None else dict(row)

    # --- Stage 9B.3a correction application ledger -----------------------

    @staticmethod
    def _application_payload(row: sqlite3.Row) -> dict[str, Any]:
        payload = json.loads(row["payload_json"])
        payload.update({
            "applicationId": row["application_id"],
            "proposalId": row["proposal_id"],
            "findingId": row["finding_id"],
            "projectId": row["project_id"],
            "expectedProposalRevision": int(row["expected_proposal_revision"]),
            "expectedFindingRevision": int(row["expected_finding_revision"]),
            "targetDisplayedReference": row["target_displayed_reference"],
            "canonicalReferences": json.loads(row["canonical_references_json"]),
            "sourceProvenanceReferences": json.loads(row["source_provenance_references_json"]),
            "expectedTargetRevision": row["expected_target_revision"],
            "expectedTargetContentHash": row["expected_target_content_hash"],
            "expectedStartCodePoint": int(row["expected_start_code_point"]),
            "expectedEndCodePoint": int(row["expected_end_code_point"]),
            "expectedOriginalText": row["expected_original_text"],
            "replacementTextSnapshot": row["replacement_text_snapshot"],
            "intendedFinalVerseHash": row["intended_final_verse_hash"],
            "pendingInvalidationId": row["pending_invalidation_id"],
            "translationCoreJournalTransactionId": row["translation_core_journal_transaction_id"],
            "actor": json.loads(row["actor_json"]),
            "createdAt": row["created_at"], "updatedAt": row["updated_at"],
            "completedAt": row["completed_at"],
            "applicationState": row["application_state"],
            "stateRevision": int(row["state_revision"]),
            "failureCode": row["failure_code"],
            "recoveryMetadata": json.loads(row["recovery_metadata_json"]),
            "resultMetadata": json.loads(row["result_metadata_json"]),
        })
        return payload

    def _insert_application_intent(
        self, conn: sqlite3.Connection, intent: CorrectionApplicationIntent,
    ) -> None:
        payload = to_wire(intent)
        conn.execute(
            "INSERT INTO correction_application_intents("
            "application_id,proposal_id,finding_id,project_id,expected_proposal_revision,"
            "expected_finding_revision,target_displayed_reference,canonical_references_json,"
            "source_provenance_references_json,expected_target_revision,expected_target_content_hash,"
            "expected_start_code_point,expected_end_code_point,expected_original_text,"
            "replacement_text_snapshot,intended_final_verse_hash,pending_invalidation_id,"
            "translation_core_journal_transaction_id,actor_json,created_at,updated_at,completed_at,"
            "application_state,state_revision,failure_code,recovery_metadata_json,result_metadata_json,payload_json"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                intent.application_id, intent.proposal_id, intent.finding_id,
                intent.project_id, intent.expected_proposal_revision,
                intent.expected_finding_revision, intent.target_displayed_reference,
                json.dumps(intent.canonical_references, ensure_ascii=False),
                json.dumps(intent.source_provenance_references, ensure_ascii=False),
                intent.expected_target_revision, intent.expected_target_content_hash,
                intent.expected_start_code_point, intent.expected_end_code_point,
                intent.expected_original_text, intent.replacement_text_snapshot,
                intent.intended_final_verse_hash, intent.pending_invalidation_id,
                intent.translation_core_journal_transaction_id,
                json.dumps(to_wire(intent.actor), ensure_ascii=False),
                intent.created_at, intent.updated_at, intent.completed_at,
                intent.application_state.value, intent.state_revision,
                intent.failure_code,
                json.dumps(intent.recovery_metadata, ensure_ascii=False),
                json.dumps(intent.result_metadata, ensure_ascii=False),
                json.dumps(payload, ensure_ascii=False),
            ),
        )

    def _validate_application_snapshot(
        self, conn: sqlite3.Connection, intent: CorrectionApplicationIntent,
    ) -> None:
        if intent.application_state != CorrectionApplicationState.PREPARED or intent.state_revision != 1:
            raise FoundationValidationError(
                "A new correction application must begin at PREPARED revision 1"
            )
        if intent.actor.actor_type != ActorType.HUMAN:
            raise FoundationValidationError(
                "Correction application requires explicit human action"
            )
        proposal = conn.execute(
            "SELECT qa_finding_id,project_id,revision FROM correction_proposals WHERE id=?",
            (intent.proposal_id,),
        ).fetchone()
        if proposal is None:
            raise FoundationValidationError(f"Unknown correction proposal: {intent.proposal_id}")
        if (
            proposal["qa_finding_id"] != intent.finding_id
            or proposal["project_id"] != intent.project_id
            or int(proposal["revision"]) != intent.expected_proposal_revision
        ):
            raise FoundationConflict("Correction proposal snapshot revision conflict")
        finding = conn.execute(
            "SELECT project_id,revision FROM qa_findings WHERE id=?", (intent.finding_id,),
        ).fetchone()
        if finding is None:
            raise FoundationValidationError(f"Unknown QA finding: {intent.finding_id}")
        if (
            finding["project_id"] != intent.project_id
            or int(finding["revision"]) != intent.expected_finding_revision
        ):
            raise FoundationConflict("QA finding snapshot revision conflict")
        book = intent.target_displayed_reference.split(" ", 1)[0].upper()
        target = conn.execute(
            "SELECT text_hash,text_revision FROM current_target_revisions "
            "WHERE project_id=? AND book=? AND displayed_reference=?",
            (intent.project_id, book, intent.target_displayed_reference),
        ).fetchone()
        if target is None or (
            target["text_hash"] != intent.expected_target_content_hash
            or target["text_revision"] != intent.expected_target_revision
        ):
            raise FoundationConflict("Current target snapshot does not match application target snapshot")

    def prepare_application_intent(
        self, intent: CorrectionApplicationIntent, *, previous_text_hash: str,
    ) -> dict[str, Any]:
        """Atomically prepare invalidation and persist one idempotent attempt.

        This method records only metadata. It never opens or writes Scripture.
        """
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM correction_application_intents "
                "WHERE proposal_id=? AND expected_proposal_revision=?",
                (intent.proposal_id, intent.expected_proposal_revision),
            ).fetchone()
            if existing is not None:
                conn.commit()
                return self._application_payload(existing)
            self._validate_application_snapshot(conn, intent)
            if previous_text_hash != intent.expected_target_content_hash:
                raise FoundationConflict("Current target snapshot does not match prepared target snapshot")
            now = self._now()
            conn.execute(
                "INSERT INTO pending_invalidations VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    intent.pending_invalidation_id, intent.project_id,
                    intent.target_displayed_reference.split(" ", 1)[0].upper(),
                    intent.target_displayed_reference, previous_text_hash,
                    intent.intended_final_verse_hash, "PREPARED", 0, "", now, now,
                ),
            )
            self._insert_application_intent(conn, intent)
            row = conn.execute(
                "SELECT * FROM correction_application_intents WHERE application_id=?",
                (intent.application_id,),
            ).fetchone()
            conn.commit()
        return self._application_payload(row)

    def create_application_intent(self, intent: CorrectionApplicationIntent) -> dict[str, Any]:
        """Persist against an already PREPARED invalidation, with CAS validation."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM correction_application_intents "
                "WHERE proposal_id=? AND expected_proposal_revision=?",
                (intent.proposal_id, intent.expected_proposal_revision),
            ).fetchone()
            if existing is not None:
                conn.commit()
                return self._application_payload(existing)
            self._validate_application_snapshot(conn, intent)
            pending = conn.execute(
                "SELECT * FROM pending_invalidations WHERE id=? AND state='PREPARED'",
                (intent.pending_invalidation_id,),
            ).fetchone()
            if pending is None:
                raise FoundationConflict("Application invalidation is not PREPARED")
            if (
                pending["project_id"] != intent.project_id
                or pending["displayed_reference"] != intent.target_displayed_reference
                or pending["previous_text_hash"] != intent.expected_target_content_hash
                or pending["expected_text_hash"] != intent.intended_final_verse_hash
            ):
                raise FoundationConflict("Prepared invalidation does not match application snapshot")
            self._insert_application_intent(conn, intent)
            row = conn.execute(
                "SELECT * FROM correction_application_intents WHERE application_id=?",
                (intent.application_id,),
            ).fetchone()
            conn.commit()
        return self._application_payload(row)

    def application_intent(self, application_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM correction_application_intents WHERE application_id=?",
                (application_id,),
            ).fetchone()
        if row is None:
            raise FoundationValidationError(f"Unknown correction application: {application_id}")
        return self._application_payload(row)

    get_application_intent = application_intent

    def find_application_by_proposal_revision(
        self, proposal_id: str, expected_proposal_revision: int,
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM correction_application_intents "
                "WHERE proposal_id=? AND expected_proposal_revision=?",
                (proposal_id, expected_proposal_revision),
            ).fetchone()
        return None if row is None else self._application_payload(row)

    def applications_for_finding(self, finding_id: str) -> list[dict[str, Any]]:
        """Return durable application history newest first for UI recovery."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM correction_application_intents WHERE finding_id=? "
                "ORDER BY created_at DESC,application_id DESC", (finding_id,),
            ).fetchall()
        return [self._application_payload(row) for row in rows]

    def record_affected_analysis_association(
        self, application_id: str, *, expected_state_revision: int,
        association: dict[str, Any],
    ) -> dict[str, Any]:
        """CAS-append an analysis association without changing application state.

        Stage 9B.3c deliberately keeps a completed application COMPLETED and
        semantic verification PENDING. The job itself owns mutable technical
        progress; this ledger stores the immutable link needed after restart.
        """
        job_id = str(association.get("analysisJobId") or "").strip()
        if not job_id:
            raise FoundationValidationError("Affected analysis association requires a job id")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM correction_application_intents WHERE application_id=?",
                (application_id,),
            ).fetchone()
            if row is None:
                raise FoundationValidationError(f"Unknown correction application: {application_id}")
            if row["application_state"] != CorrectionApplicationState.COMPLETED.value:
                raise FoundationConflict("Affected analysis requires a COMPLETED correction application")
            if int(row["state_revision"]) != expected_state_revision:
                raise FoundationConflict("Correction application state revision conflict")
            job = conn.execute(
                "SELECT project_id,payload_json FROM analysis_jobs WHERE id=?", (job_id,),
            ).fetchone()
            if job is None or job["project_id"] != row["project_id"]:
                raise FoundationValidationError("Affected analysis job belongs to another project")
            job_payload = json.loads(job["payload_json"])
            requested = job_payload.get("requestedScope") or {}
            if str(requested.get("correctionApplicationId") or "") != application_id:
                raise FoundationValidationError("Analysis job is not linked to this correction application")
            result = json.loads(row["result_metadata_json"])
            attempts = list(result.get("affectedAnalysisAttempts") or ())
            if not any(str(item.get("analysisJobId") or "") == job_id for item in attempts):
                attempts.append(dict(association))
            result.update({
                "affectedAnalysisStarted": True,
                "affectedAnalysisJobId": job_id,
                "affectedAnalysisAttempts": attempts,
            })
            now = self._now()
            payload = self._application_payload(row)
            payload.update({
                "stateRevision": expected_state_revision + 1,
                "updatedAt": now,
                "resultMetadata": result,
            })
            changed = conn.execute(
                "UPDATE correction_application_intents SET state_revision=state_revision+1,"
                "updated_at=?,result_metadata_json=?,payload_json=? "
                "WHERE application_id=? AND application_state='COMPLETED' AND state_revision=?",
                (now, json.dumps(result, ensure_ascii=False),
                 json.dumps(payload, ensure_ascii=False), application_id,
                 expected_state_revision),
            ).rowcount
            if changed != 1:
                raise FoundationConflict("Correction application state revision conflict")
            updated = conn.execute(
                "SELECT * FROM correction_application_intents WHERE application_id=?",
                (application_id,),
            ).fetchone()
            conn.commit()
        return self._application_payload(updated)

    find_by_proposal_revision = find_application_by_proposal_revision

    # -- Stage 9B.4 correction verification records ------------------------

    @staticmethod
    def _verification_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "verificationId": row["id"], "projectId": row["project_id"],
            "applicationId": row["application_id"], "proposalId": row["proposal_id"],
            "proposalRevision": int(row["proposal_revision"]),
            "findingId": row["finding_id"], "analysisJobId": row["analysis_job_id"],
            "targetRevision": row["target_revision"],
            "targetContentHash": row["target_content_hash"],
            "verifierFingerprint": row["verifier_fingerprint"],
            "result": row["result"], "confidence": float(row["confidence"]),
            "reasonCodes": json.loads(row["reason_codes_json"]),
            "lifecycleStatus": row["lifecycle_status"],
            "acknowledgedAt": row["acknowledged_at"], "acknowledgedBy": row["acknowledged_by"],
            "revision": int(row["revision"]), "createdAt": row["created_at"],
            "updatedAt": row["updated_at"], "payload": json.loads(row["payload_json"]),
        }

    def save_correction_verification(
        self, *, verification_id: str, project_id: str, application_id: str,
        proposal_id: str, proposal_revision: int, finding_id: str, analysis_job_id: str,
        target_revision: str, target_content_hash: str, verifier_fingerprint: str,
        result: str, confidence: float, reason_codes: list[str],
        payload: dict[str, Any], created_at: str,
    ) -> dict[str, Any]:
        """Insert a verification and supersede this application's older ones.

        The insert is idempotent on the UNIQUE verification identity: a
        concurrent duplicate click returns the record that already exists
        rather than creating a second one. Superseded records are retained --
        verification history is evidence, not a cache.
        """
        if result not in {"PASSED", "FAILED", "UNCERTAIN"}:
            raise FoundationValidationError(f"Unknown verification result: {result}")
        now = self._now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM correction_verifications WHERE application_id=? AND "
                "analysis_job_id=? AND target_content_hash=? AND verifier_fingerprint=?",
                (application_id, analysis_job_id, target_content_hash, verifier_fingerprint),
            ).fetchone()
            if existing is not None:
                conn.commit()
                return self._verification_payload(existing)
            application = conn.execute(
                "SELECT project_id,application_state FROM correction_application_intents "
                "WHERE application_id=?", (application_id,),
            ).fetchone()
            if application is None:
                raise FoundationValidationError(
                    f"Unknown correction application: {application_id}")
            if application["project_id"] != project_id:
                raise FoundationValidationError(
                    "Correction verification belongs to another project")
            if application["application_state"] != CorrectionApplicationState.COMPLETED.value:
                raise FoundationConflict(
                    "Verification requires a COMPLETED correction application")
            conn.execute(
                "UPDATE correction_verifications SET lifecycle_status='SUPERSEDED',"
                "revision=revision+1,updated_at=? WHERE application_id=? AND lifecycle_status='ACTIVE'",
                (now, application_id),
            )
            conn.execute(
                "INSERT INTO correction_verifications(id,project_id,application_id,"
                "proposal_id,proposal_revision,finding_id,analysis_job_id,target_revision,"
                "target_content_hash,verifier_fingerprint,result,confidence,reason_codes_json,"
                "lifecycle_status,acknowledged_at,acknowledged_by,revision,created_at,updated_at,"
                "payload_json,seq) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'ACTIVE',NULL,NULL,1,?,?,?,"
                "(SELECT IFNULL(MAX(seq),0)+1 FROM correction_verifications))",
                (verification_id, project_id, application_id, proposal_id, int(proposal_revision),
                 finding_id, analysis_job_id, target_revision, target_content_hash,
                 verifier_fingerprint, result, float(confidence),
                 json.dumps(list(reason_codes), ensure_ascii=False),
                 created_at or now, now, json.dumps(payload, ensure_ascii=False)),
            )
            # Verification depends on the proposal it verifies and on the exact
            # target coordinate whose text it was computed against, so a later
            # edit or proposal change stales it through the ordinary graph.
            target_reference = str((payload.get("targetReferences") or [""])[0])
            book, _, _location = target_reference.rpartition(" ")
            edges = [("CORRECTION_PROPOSAL", proposal_id)]
            if book and target_reference:
                edges.append(("TARGET_REFERENCE", self.target_dependency_id(
                    project_id, book, target_reference,
                )))
            for record_type, record_id in edges:
                conn.execute(
                    "INSERT OR IGNORE INTO record_dependencies VALUES(?,?,?,?)",
                    ("CORRECTION_VERIFICATION", verification_id, record_type, record_id),
                )
            # The proposal's independent verificationStatus mirrors the verdict.
            # It is a status field, never a QA disposition: CORRECTED still
            # requires a separate, explicit human acknowledgement.
            row = conn.execute(
                "SELECT payload_json,revision FROM correction_proposals WHERE id=?",
                (proposal_id,),
            ).fetchone()
            if row is not None:
                proposal_payload = json.loads(row["payload_json"])
                proposal_payload.update({
                    "verificationStatus": result,
                    "verificationJobIds": list(dict.fromkeys([
                        *(proposal_payload.get("verificationJobIds") or ()), analysis_job_id,
                    ])),
                })
                conn.execute(
                    "UPDATE correction_proposals SET verification_status=?,payload_json=? WHERE id=?",
                    (result, json.dumps(proposal_payload, ensure_ascii=False), proposal_id),
                )
            inserted = conn.execute(
                "SELECT * FROM correction_verifications WHERE id=?",
                (verification_id,),
            ).fetchone()
            conn.commit()
        return self._verification_payload(inserted)

    def correction_verification(self, verification_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM correction_verifications WHERE id=?",
                (verification_id,),
            ).fetchone()
        if row is None:
            raise FoundationValidationError(f"Unknown correction verification: {verification_id}")
        return self._verification_payload(row)

    def current_correction_verification(self, application_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM correction_verifications WHERE application_id=? AND "
                "lifecycle_status='ACTIVE' ORDER BY created_at DESC,id DESC LIMIT 1",
                (application_id,),
            ).fetchone()
        return None if row is None else self._verification_payload(row)

    def correction_verification_for_inputs(
        self, *, application_id: str, analysis_job_id: str,
        target_content_hash: str, verifier_fingerprint: str,
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM correction_verifications WHERE application_id=? AND "
                "analysis_job_id=? AND target_content_hash=? AND verifier_fingerprint=?",
                (application_id, analysis_job_id, target_content_hash, verifier_fingerprint),
            ).fetchone()
        return None if row is None else self._verification_payload(row)

    def correction_verification_history(self, application_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM correction_verifications WHERE application_id=? "
                "ORDER BY seq", (application_id,),
            ).fetchall()
        return [self._verification_payload(row) for row in rows]

    def acknowledge_correction_verification(
        self, verification_id: str, *, expected_revision: int, actor_id: str, note: str = "",
    ) -> dict[str, Any]:
        """Stamp the explicit human CORRECTED acknowledgement onto a verification.

        Only a current, ACTIVE, PASSED verification may be acknowledged, and the
        stamp is written under revision CAS so a verification superseded between
        render and click fails closed instead of silently closing a QA issue.
        """
        now = self._now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM correction_verifications WHERE id=?",
                (verification_id,),
            ).fetchone()
            if row is None:
                raise FoundationValidationError(
                    f"Unknown correction verification: {verification_id}")
            if int(row["revision"]) != int(expected_revision):
                raise FoundationConflict("Correction verification revision conflict")
            if row["lifecycle_status"] != "ACTIVE":
                raise FoundationConflict("Only a current verification may be acknowledged")
            if row["result"] != "PASSED":
                raise FoundationConflict("Only a PASSED verification may be acknowledged")
            if row["acknowledged_at"]:
                conn.commit()
                return self._verification_payload(row)
            payload = json.loads(row["payload_json"])
            payload.update({"acknowledgedBy": actor_id, "acknowledgementNote": note})
            changed = conn.execute(
                "UPDATE correction_verifications SET acknowledged_at=?,acknowledged_by=?,"
                "revision=revision+1,updated_at=?,payload_json=? "
                "WHERE id=? AND revision=? AND lifecycle_status='ACTIVE'",
                (now, actor_id, now, json.dumps(payload, ensure_ascii=False),
                 verification_id, int(expected_revision)),
            ).rowcount
            if changed != 1:
                raise FoundationConflict("Correction verification revision conflict")
            updated = conn.execute(
                "SELECT * FROM correction_verifications WHERE id=?",
                (verification_id,),
            ).fetchone()
            conn.commit()
        return self._verification_payload(updated)

    def list_incomplete_applications(self, project_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM correction_application_intents WHERE project_id=? "
                "AND application_state NOT IN ('COMPLETED','FAILED') ORDER BY created_at,application_id",
                (project_id,),
            ).fetchall()
        return [self._application_payload(row) for row in rows]

    @staticmethod
    def _state(value: str | CorrectionApplicationState) -> CorrectionApplicationState:
        return value if isinstance(value, CorrectionApplicationState) else CorrectionApplicationState(str(value))

    def transition_application_state(
        self, application_id: str, *,
        expected_state: str | CorrectionApplicationState,
        expected_state_revision: int,
        new_state: str | CorrectionApplicationState,
        failure_code: str = "",
        recovery_metadata: dict[str, Any] | None = None,
        result_metadata: dict[str, Any] | None = None,
        translation_core_journal_transaction_id: str | None = None,
    ) -> dict[str, Any]:
        expected = self._state(expected_state)
        target = self._state(new_state)
        if not CorrectionApplicationIntent.can_transition(expected, target):
            raise FoundationConflict(
                f"Invalid correction application transition: {expected.value} -> {target.value}"
            )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM correction_application_intents WHERE application_id=?",
                (application_id,),
            ).fetchone()
            if row is None:
                raise FoundationValidationError(f"Unknown correction application: {application_id}")
            if row["application_state"] != expected.value or int(row["state_revision"]) != expected_state_revision:
                raise FoundationConflict("Correction application state revision conflict")
            recovery = json.loads(row["recovery_metadata_json"])
            recovery.update(recovery_metadata or {})
            result = json.loads(row["result_metadata_json"])
            result.update(result_metadata or {})
            now = self._now()
            completed = now if target in {
                CorrectionApplicationState.COMPLETED, CorrectionApplicationState.FAILED,
            } else row["completed_at"]
            journal_id = (
                translation_core_journal_transaction_id
                if translation_core_journal_transaction_id is not None
                else row["translation_core_journal_transaction_id"]
            )
            payload = self._application_payload(row)
            payload.update({
                "applicationState": target.value,
                "stateRevision": expected_state_revision + 1,
                "failureCode": failure_code,
                "recoveryMetadata": recovery,
                "resultMetadata": result,
                "translationCoreJournalTransactionId": journal_id,
                "updatedAt": now, "completedAt": completed,
            })
            changed = conn.execute(
                "UPDATE correction_application_intents SET application_state=?,state_revision=state_revision+1,"
                "failure_code=?,recovery_metadata_json=?,result_metadata_json=?,"
                "translation_core_journal_transaction_id=?,updated_at=?,completed_at=?,payload_json=? "
                "WHERE application_id=? AND application_state=? AND state_revision=?",
                (
                    target.value, failure_code, json.dumps(recovery, ensure_ascii=False),
                    json.dumps(result, ensure_ascii=False), journal_id, now, completed,
                    json.dumps(payload, ensure_ascii=False), application_id, expected.value,
                    expected_state_revision,
                ),
            ).rowcount
            if changed != 1:
                raise FoundationConflict("Correction application state revision conflict")
            updated = conn.execute(
                "SELECT * FROM correction_application_intents WHERE application_id=?",
                (application_id,),
            ).fetchone()
            conn.commit()
        return self._application_payload(updated)

    transition_application_state_cas = transition_application_state

    def record_recovery_required(
        self, application_id: str, *, expected_state_revision: int,
        failure_code: str, recovery_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        current = self.application_intent(application_id)
        if current["applicationState"] == CorrectionApplicationState.RECOVERY_REQUIRED.value:
            return current
        return self.transition_application_state(
            application_id,
            expected_state=current["applicationState"],
            expected_state_revision=expected_state_revision,
            new_state=CorrectionApplicationState.RECOVERY_REQUIRED,
            failure_code=failure_code, recovery_metadata=recovery_metadata,
        )

    record_application_recovery_required = record_recovery_required

    def record_application_backup(
        self, application_id: str, *, backup_root: str | Path,
        expected_state_revision: int,
    ) -> dict[str, Any]:
        current = self.application_intent(application_id)
        if int(current["stateRevision"]) != expected_state_revision:
            raise FoundationConflict("Correction application state revision conflict")
        directory = self.backup(
            backup_root, reason=f"correction application {application_id} pre-mutation backup",
        )
        manifest_path = directory / "backup-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["applicationId"] = application_id
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        metadata = dict(current["recoveryMetadata"])
        metadata["semanticDatabaseBackup"] = {
            "path": str(directory), "sha256": manifest["sha256"],
            "createdAt": manifest["createdAt"], "applicationId": application_id,
        }
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM correction_application_intents WHERE application_id=?",
                (application_id,),
            ).fetchone()
            if row is None or int(row["state_revision"]) != expected_state_revision:
                raise FoundationConflict("Correction application state revision conflict")
            payload = self._application_payload(row)
            now = self._now()
            payload.update({
                "recoveryMetadata": metadata, "stateRevision": expected_state_revision + 1,
                "updatedAt": now,
            })
            conn.execute(
                "UPDATE correction_application_intents SET recovery_metadata_json=?,"
                "state_revision=state_revision+1,updated_at=?,payload_json=? "
                "WHERE application_id=? AND state_revision=?",
                (json.dumps(metadata, ensure_ascii=False), now,
                 json.dumps(payload, ensure_ascii=False), application_id,
                 expected_state_revision),
            )
            updated = conn.execute(
                "SELECT * FROM correction_application_intents WHERE application_id=?",
                (application_id,),
            ).fetchone()
            conn.commit()
        return self._application_payload(updated)

    @staticmethod
    def target_dependency_id(project_id: str, book: str, displayed_reference: str) -> str:
        return "\u241f".join((project_id, book.upper(), displayed_reference))

    @staticmethod
    def source_dependency_id(project_id: str, book: str, resource_hash: str) -> str:
        return "\u241f".join((project_id, book.upper(), resource_hash))

    @staticmethod
    def alignment_dependency_id(project_id: str, book: str) -> str:
        """Stable anchor for this book's tC Word Alignment as a whole.

        Deliberately stable (no content hash embedded, unlike
        source_dependency_id) so a LOCATION_RUN's dependency edge keeps
        pointing at the same coordinate across alignment changes -- staling
        is triggered explicitly by apply_alignment_invalidation walking
        forward from this id, the same shape target_dependency_id/
        apply_target_invalidation already use for Scripture edits.
        """
        return "\u241f".join((project_id, book.upper(), "WORD_ALIGNMENT"))

    def apply_alignment_invalidation(self, project_id: str, book: str) -> int:
        """Stale everything depending on this book's Word Alignment anchor.

        Coarser than target-text invalidation on purpose: one anchor per
        book, not per verse, matching the book-wide directory digest
        word_alignment_evidence.py's evidence and PassageSemanticRuntime's
        compatibility scan both key off. Over-invalidating a run whose range
        didn't touch the changed verse is safe; under-invalidating is not.
        """
        dependency_id = self.alignment_dependency_id(project_id, book)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            staled = self._stale_generic_dependencies(conn, "WORD_ALIGNMENT", dependency_id)
            conn.commit()
        return staled

    def save_passage_references(
        self, passage_id: str, references: list[dict[str, Any]],
    ) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM passage_reference_records WHERE passage_id=?", (passage_id,))
            for ordinal, reference in enumerate(references):
                conn.execute(
                    "INSERT INTO passage_reference_records VALUES(?,?,?,?,?,?)",
                    (passage_id, reference["displayedReference"], reference["projectVersification"],
                     json.dumps(reference["canonicalReferences"], ensure_ascii=False),
                     reference["mappingKind"], ordinal),
                )
            conn.commit()

    def passage_references(self, passage_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM passage_reference_records WHERE passage_id=? ORDER BY ordinal",
                (passage_id,),
            ).fetchall()
        return [{
            "displayedReference": row["displayed_reference"],
            "projectVersification": row["project_versification"],
            "canonicalReferences": json.loads(row["canonical_references_json"]),
            "mappingKind": row["mapping_kind"],
        } for row in rows]

    def save_token_lineage(self, lineage: TokenLineage) -> None:
        payload = to_wire(lineage)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO token_lineages VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (lineage.id, lineage.side.value, lineage.project_id, lineage.logical_resource_id, lineage.book,
                 json.dumps(list(lineage.canonical_reference_scope)), lineage.token_layer.value,
                 lineage.upstream_identity, lineage.provenance.value, lineage.review_status.value,
                 lineage.lifecycle_status.value, lineage.revision, json.dumps(payload, ensure_ascii=False)),
            )
            conn.commit()

    def save_token_instance(self, instance: TokenInstance) -> None:
        payload = to_wire(instance)
        with self._connect() as conn:
            try:
                lineage = conn.execute(
                    "SELECT side,token_layer FROM token_lineages WHERE id=?", (instance.lineage_id,)
                ).fetchone()
                if lineage is None:
                    raise FoundationValidationError(f"Unknown token lineage: {instance.lineage_id}")
                if lineage["side"] != instance.side.value or lineage["token_layer"] != instance.token_layer.value:
                    raise FoundationValidationError("Token instance side/layer must match its lineage")
                if instance.parent_instance_id is not None:
                    parent = conn.execute(
                        "SELECT side FROM token_instances WHERE id=?", (instance.parent_instance_id,)
                    ).fetchone()
                    if parent is None or parent["side"] != instance.side.value:
                        raise FoundationValidationError("Token parent must exist on the same side")
                conn.execute(
                    "INSERT INTO token_instances VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (instance.id, instance.lineage_id, instance.side.value, instance.token_layer.value,
                     instance.parent_instance_id, instance.text_revision, instance.instance_fingerprint,
                     json.dumps(payload, ensure_ascii=False), instance.lifecycle_status.value,
                     instance.revision),
                )
                if instance.side == TokenSide.TARGET and instance.project_id:
                    conn.execute(
                        "INSERT OR IGNORE INTO record_dependencies VALUES(?,?,?,?)",
                        ("TOKEN_INSTANCE", instance.id, "TARGET_REFERENCE",
                         self.target_dependency_id(
                             instance.project_id, instance.book,
                             instance.displayed_reference,
                         )),
                    )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                raise FoundationValidationError(f"Invalid token instance: {exc}") from exc

    def save_target_token_batch(
        self, lineages: list[TokenLineage], instances: list[TokenInstance],
    ) -> list[dict[str, Any]]:
        """Persist one target tokenization result in a single transaction."""
        if len(lineages) != len(instances):
            raise FoundationValidationError("Target token batch lineage/instance counts differ")
        lineage_ids = {lineage.id for lineage in lineages}
        instance_ids = {instance.id for instance in instances}
        payloads = [to_wire(instance) for instance in instances]
        with self._connect() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                for lineage in lineages:
                    conn.execute(
                        "INSERT INTO token_lineages VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (lineage.id, lineage.side.value, lineage.project_id,
                         lineage.logical_resource_id, lineage.book,
                         json.dumps(list(lineage.canonical_reference_scope)),
                         lineage.token_layer.value, lineage.upstream_identity,
                         lineage.provenance.value, lineage.review_status.value,
                         lineage.lifecycle_status.value, lineage.revision,
                         json.dumps(to_wire(lineage), ensure_ascii=False)),
                    )
                for instance, payload in zip(instances, payloads):
                    if instance.lineage_id not in lineage_ids:
                        lineage = conn.execute(
                            "SELECT side,token_layer FROM token_lineages WHERE id=?",
                            (instance.lineage_id,),
                        ).fetchone()
                        if lineage is None:
                            raise FoundationValidationError(
                                f"Unknown token lineage: {instance.lineage_id}"
                            )
                    if instance.parent_instance_id is not None and (
                        instance.parent_instance_id not in instance_ids
                        and conn.execute(
                            "SELECT 1 FROM token_instances WHERE id=?",
                            (instance.parent_instance_id,),
                        ).fetchone() is None
                    ):
                        raise FoundationValidationError("Unknown target token parent")
                    conn.execute(
                        "INSERT INTO token_instances VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (instance.id, instance.lineage_id, instance.side.value,
                         instance.token_layer.value, instance.parent_instance_id,
                         instance.text_revision, instance.instance_fingerprint,
                         json.dumps(payload, ensure_ascii=False),
                         instance.lifecycle_status.value, instance.revision),
                    )
                    if instance.side == TokenSide.TARGET and instance.project_id:
                        conn.execute(
                            "INSERT OR IGNORE INTO record_dependencies VALUES(?,?,?,?)",
                            ("TOKEN_INSTANCE", instance.id, "TARGET_REFERENCE",
                             self.target_dependency_id(
                                 instance.project_id, instance.book,
                                 instance.displayed_reference,
                             )),
                        )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                raise FoundationValidationError(f"Invalid target token batch: {exc}") from exc
        return payloads

    def token_instance(self, instance_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT payload_json FROM token_instances WHERE id=?", (instance_id,)).fetchone()
        if row is None:
            raise FoundationValidationError(f"Unknown token instance: {instance_id}")
        return json.loads(row[0])

    def token_instances_for_reference(
        self, *, project_id: str, book: str, displayed_reference: str,
        text_revision: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM token_instances WHERE side='TARGET' "
                "AND (? IS NULL OR text_revision=?) ORDER BY id",
                (text_revision, text_revision),
            ).fetchall()
        result = []
        for row in rows:
            payload = json.loads(row[0])
            if (
                payload.get("projectId") == project_id
                and str(payload.get("book") or "").upper() == book.upper()
                and payload.get("displayedReference") == displayed_reference
            ):
                result.append(payload)
        return sorted(result, key=lambda item: int(item.get("index") or 0))

    def save_token_lineage_candidate(
        self, *, candidate_id: str, project_id: str, old_instance_id: str,
        new_instance_id: str, relation: str, confidence: float, reason_code: str,
    ) -> None:
        payload = {
            "id": candidate_id, "projectId": project_id,
            "oldInstanceId": old_instance_id, "newInstanceId": new_instance_id,
            "relation": relation, "confidence": confidence, "reasonCode": reason_code,
            "lifecycleStatus": "INACTIVE",
        }
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO token_lineage_candidates VALUES(?,?,?,?,?,?,?,?,?)",
                (candidate_id, project_id, old_instance_id, new_instance_id, relation,
                 confidence, reason_code, "INACTIVE", json.dumps(payload, ensure_ascii=False)),
            )
            conn.commit()

    def token_lineage_candidates(self, project_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM token_lineage_candidates WHERE project_id=? ORDER BY id",
                (project_id,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    @staticmethod
    def target_content_hash(target_text_by_displayed_reference: dict[str, str]) -> str:
        canonical = json.dumps(
            target_text_by_displayed_reference,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def save_passage_record(self, passage: PassageRecord) -> None:
        expected_hash = self.target_content_hash(passage.target_text_by_displayed_reference)
        if passage.target_content_hash != expected_hash:
            raise FoundationValidationError("Passage target-content hash does not match current target text")
        policy_id = self._ensure_policy(passage.policy_binding)
        payload = to_wire(passage)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO passage_records VALUES(?,?,?,?,?,?,?,?,?)",
                (passage.id, passage.project_id, passage.book, passage.target_revision,
                 passage.target_content_hash, policy_id, passage.lifecycle_status.value, passage.revision,
                 json.dumps(payload, ensure_ascii=False)),
            )
            conn.commit()

    def passage_record(self, passage_id: str) -> dict[str, Any]:
        return self._load_payload("passage_records", passage_id, "passage record")

    def save_evidence_record(self, evidence: EvidenceRecord) -> None:
        if hashlib.sha256(evidence.content.encode("utf-8")).hexdigest() != evidence.content_hash:
            raise FoundationValidationError("Evidence content hash does not match its content")
        policy_id = self._ensure_policy(evidence.policy_binding)
        payload = to_wire(evidence)
        with self._connect() as conn:
            for unit_id in (*evidence.source_semantic_unit_ids, *evidence.target_semantic_unit_ids):
                if conn.execute("SELECT 1 FROM semantic_units WHERE id=?", (unit_id,)).fetchone() is None:
                    raise FoundationValidationError(f"Unknown evidence semantic unit: {unit_id}")
            conn.execute(
                "INSERT INTO evidence_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (evidence.id, evidence.project_id, evidence.kind.value, evidence.resource_id,
                 evidence.resource_version, evidence.resource_hash, evidence.occurrence_id,
                 evidence.validation_status.value, policy_id, evidence.review_status.value,
                 evidence.lifecycle_status.value, evidence.revision,
                 json.dumps(payload, ensure_ascii=False)),
            )
            conn.commit()

    def evidence_record(self, evidence_id: str) -> dict[str, Any]:
        return self._load_payload("evidence_records", evidence_id, "evidence record")

    def _load_payload(self, table: str, record_id: str, label: str) -> dict[str, Any]:
        allowed = {
            "passage_records", "evidence_records", "exportability_records",
        }
        if table not in allowed:
            raise FoundationValidationError(f"Unsupported payload table: {table}")
        with self._connect() as conn:
            row = conn.execute(f"SELECT payload_json FROM {table} WHERE id=?", (record_id,)).fetchone()
        if row is None:
            raise FoundationValidationError(f"Unknown {label}: {record_id}")
        return json.loads(row[0])

    def save_semantic_unit(self, unit: SemanticUnit) -> None:
        payload = to_wire(unit)
        with self._connect() as conn:
            for token_id in unit.token_instance_ids:
                token = conn.execute("SELECT side FROM token_instances WHERE id=?", (token_id,)).fetchone()
                if token is None:
                    raise FoundationValidationError(f"Unknown semantic-unit token instance: {token_id}")
                if token["side"] != unit.side.value:
                    raise FoundationValidationError(
                        f"Semantic-unit side does not match token instance: {token_id}"
                    )
            if unit.audit_owner_unit_id != unit.id and conn.execute(
                "SELECT 1 FROM semantic_units WHERE id=?", (unit.audit_owner_unit_id,)
            ).fetchone() is None:
                raise FoundationValidationError(
                    f"Unknown semantic-unit audit owner: {unit.audit_owner_unit_id}"
                )
            for evidence_id in unit.evidence_ids:
                if conn.execute(
                    "SELECT 1 FROM evidence_records WHERE id=?", (evidence_id,)
                ).fetchone() is None:
                    raise FoundationValidationError(
                        f"Unknown semantic-unit evidence record: {evidence_id}"
                    )
            conn.execute(
                "INSERT INTO semantic_units VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (unit.id, unit.project_id, unit.side.value, unit.kind.value, unit.audit_owner_unit_id,
                 unit.audit_eligibility.value, unit.semantic_obligation.value, unit.accounting_role.value,
                 unit.coverage_dimension.value, unit.semantic_fingerprint, unit.review_status.value,
                 unit.lifecycle_status.value, unit.revision, json.dumps(payload, ensure_ascii=False)),
            )
            conn.commit()

    def ensure_semantic_units(self, units: list[SemanticUnit]) -> list[dict[str, Any]]:
        """Load or insert deterministic semantic units with one commit."""
        unit_ids = {unit.id for unit in units}
        result: list[dict[str, Any]] = []
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for unit in units:
                existing = conn.execute(
                    "SELECT payload_json FROM semantic_units WHERE id=?", (unit.id,),
                ).fetchone()
                if existing is not None:
                    result.append(json.loads(existing[0]))
                    continue
                for token_id in unit.token_instance_ids:
                    token = conn.execute(
                        "SELECT side FROM token_instances WHERE id=?", (token_id,),
                    ).fetchone()
                    if token is None or token["side"] != unit.side.value:
                        raise FoundationValidationError(
                            f"Invalid semantic-unit token instance: {token_id}"
                        )
                if unit.audit_owner_unit_id not in unit_ids and conn.execute(
                    "SELECT 1 FROM semantic_units WHERE id=?", (unit.audit_owner_unit_id,),
                ).fetchone() is None:
                    raise FoundationValidationError(
                        f"Unknown semantic-unit audit owner: {unit.audit_owner_unit_id}"
                    )
                for evidence_id in unit.evidence_ids:
                    if conn.execute(
                        "SELECT 1 FROM evidence_records WHERE id=?", (evidence_id,),
                    ).fetchone() is None:
                        raise FoundationValidationError(
                            f"Unknown semantic-unit evidence record: {evidence_id}"
                        )
                payload = to_wire(unit)
                conn.execute(
                    "INSERT INTO semantic_units VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (unit.id, unit.project_id, unit.side.value, unit.kind.value,
                     unit.audit_owner_unit_id, unit.audit_eligibility.value,
                     unit.semantic_obligation.value, unit.accounting_role.value,
                     unit.coverage_dimension.value, unit.semantic_fingerprint,
                     unit.review_status.value, unit.lifecycle_status.value,
                     unit.revision, json.dumps(payload, ensure_ascii=False)),
                )
                result.append(payload)
            conn.commit()
        return result

    def semantic_unit(self, unit_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT payload_json FROM semantic_units WHERE id=?", (unit_id,)).fetchone()
        if row is None:
            raise FoundationValidationError(f"Unknown semantic unit: {unit_id}")
        return json.loads(row[0])

    # Everything on a coverage account that a later pass or a human legitimately
    # changes.  The rest is the account's content-addressed identity, and a
    # difference there is a genuine conflict rather than a re-seed.
    _COVERAGE_ACCOUNT_MUTABLE = frozenset({
        "coverageStatus", "coveredByRelationshipIds", "findingId",
        "reviewStatus", "lifecycleStatus", "revision",
    })

    @classmethod
    def _coverage_account_identity(cls, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value for key, value in payload.items()
            if key not in cls._COVERAGE_ACCOUNT_MUTABLE
        }

    def save_coverage_account(self, account: SemanticCoverageAccount) -> None:
        """Seed one content-addressed coverage account.

        The account id is a fingerprint of ``{owner, dimension, policy}``, so it
        is deliberately stable across audit runs -- Stage 8 is meant to seed it
        once and then update the same identity in place.  A re-run therefore
        re-derives an id that already exists, and a bare INSERT turned every
        post-correction affected re-analysis into a FoundationConflict: the
        unchanged verses in the affected range always collide.

        Re-seeding an identical account is a no-op, not an overwrite.  The
        stored row keeps its coverage status, its finding link and above all its
        review status, so a human promotion recorded against this obligation
        survives re-analysis untouched.  Only a real difference in the
        identity-bearing fields still conflicts.
        """
        payload = to_wire(account)
        with self._connect() as conn:
            try:
                # Serialize the identity check and insert.  Without one
                # transaction, two simultaneous re-analysis requests could
                # both observe an absent row and make the second, otherwise
                # identical seed fail at INSERT.
                conn.execute("BEGIN IMMEDIATE")
                existing = conn.execute(
                    "SELECT payload_json FROM coverage_accounts WHERE id=?", (account.id,),
                ).fetchone()
                if existing is not None:
                    stored = json.loads(existing["payload_json"])
                    if (self._coverage_account_identity(stored)
                            == self._coverage_account_identity(payload)):
                        conn.commit()
                        return
                    raise FoundationConflict(
                        "Coverage account conflicts with an active obligation: "
                        f"{account.id} already exists with different semantic identity"
                    )
                if conn.execute(
                    "SELECT 1 FROM semantic_units WHERE id=?", (account.audit_owner_unit_id,)
                ).fetchone() is None:
                    raise FoundationValidationError(
                        f"Unknown coverage-account audit owner: {account.audit_owner_unit_id}"
                    )
                for unit_id in account.member_unit_ids:
                    if conn.execute(
                        "SELECT 1 FROM semantic_units WHERE id=?", (unit_id,)
                    ).fetchone() is None:
                        raise FoundationValidationError(
                            f"Unknown coverage-account member: {unit_id}"
                        )
                conn.execute(
                    "INSERT INTO coverage_accounts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (account.id, account.project_id, account.passage_id, account.direction.value,
                     account.audit_owner_unit_id, account.coverage_dimension.value, account.semantic_fingerprint,
                     account.finding_id, account.review_status.value, account.lifecycle_status.value,
                     account.revision, json.dumps(payload, ensure_ascii=False)),
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                raise FoundationConflict(f"Coverage account conflicts with an active obligation: {exc}") from exc

    def save_source_inventory(
        self, *, inventory_id: str, project_id: str, book: str, range_key: str,
        fingerprint: str, source_resource_id: str, source_resource_version: str,
        source_resource_hash: str, audit_policy_version: str,
        diagnostics: dict[str, Any], payload: dict[str, Any], token_rows: list[tuple[str, str, str]],
        unit_ids: list[str], evidence_ids: list[str],
    ) -> None:
        """Atomically publish one immutable, content-addressed source inventory."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            lock = conn.execute(
                "SELECT resource_id,resource_version,resource_hash,lifecycle_status "
                "FROM source_resource_locks WHERE project_id=? AND book=?",
                (project_id, book),
            ).fetchone()
            if lock is None or lock["lifecycle_status"] != "ACTIVE" or (
                lock["resource_id"], lock["resource_version"], lock["resource_hash"]
            ) != (source_resource_id, source_resource_version, source_resource_hash):
                raise FoundationValidationError(
                    "Source inventory does not match the active project source resource lock"
                )
            conn.execute(
                "UPDATE source_inventory_runs SET lifecycle_status='SUPERSEDED' "
                "WHERE project_id=? AND book=? AND range_key=? AND lifecycle_status='ACTIVE' "
                "AND fingerprint<>?",
                (project_id, book, range_key, fingerprint),
            )
            conn.execute(
                "INSERT INTO source_inventory_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (inventory_id, project_id, book, range_key, fingerprint,
                 source_resource_id, source_resource_version, source_resource_hash,
                 audit_policy_version, "ACTIVE", json.dumps(diagnostics, ensure_ascii=False),
                 json.dumps(payload, ensure_ascii=False), self._now()),
            )
            conn.executemany(
                "INSERT INTO source_inventory_tokens VALUES(?,?,?,?)",
                [(inventory_id, token_id, language_id, upstream_identity)
                 for token_id, language_id, upstream_identity in token_rows],
            )
            conn.executemany(
                "INSERT INTO source_inventory_units VALUES(?,?)",
                [(inventory_id, unit_id) for unit_id in unit_ids],
            )
            conn.executemany(
                "INSERT INTO source_inventory_evidence VALUES(?,?)",
                [(inventory_id, evidence_id) for evidence_id in evidence_ids],
            )
            conn.commit()

    def source_inventory_for_fingerprint(
        self, project_id: str, book: str, range_key: str, fingerprint: str,
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM source_inventory_runs WHERE project_id=? AND book=? "
                "AND range_key=? AND fingerprint=? AND lifecycle_status='ACTIVE'",
                (project_id, book, range_key, fingerprint),
            ).fetchone()
        return None if row is None else json.loads(row[0])

    def source_inventory(self, inventory_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json,source_resource_id,source_resource_version,source_resource_hash,"
                "project_id,book,lifecycle_status FROM source_inventory_runs WHERE id=?",
                (inventory_id,),
            ).fetchone()
            if row is None:
                raise FoundationValidationError(f"Unknown source inventory: {inventory_id}")
            lock = conn.execute(
                "SELECT resource_id,resource_version,resource_hash,lifecycle_status "
                "FROM source_resource_locks WHERE project_id=? AND book=?",
                (row["project_id"], row["book"]),
            ).fetchone()
        if row["lifecycle_status"] != "ACTIVE" or lock is None or lock["lifecycle_status"] != "ACTIVE" or (
            row["source_resource_id"], row["source_resource_version"], row["source_resource_hash"]
        ) != (lock["resource_id"], lock["resource_version"], lock["resource_hash"]):
            raise FoundationValidationError(
                "Cached source inventory is stale or does not match the active source resource lock"
            )
        return json.loads(row["payload_json"])

    def quarantine_source_inventory(
        self, inventory_id: str, reason: str, payload: dict[str, Any],
    ) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE source_inventory_runs SET lifecycle_status='QUARANTINED' WHERE id=?",
                (inventory_id,),
            )
            conn.execute(
                "INSERT INTO migration_quarantine VALUES(?,?,?,?,?,?)",
                (str(uuid.uuid4()), "sourceSemantic.inventory",
                 inventory_id, "INVALID_SOURCE_INVENTORY",
                 json.dumps({"error": reason, "originalRecord": payload}, ensure_ascii=False),
                 self._now()),
            )
            conn.commit()

    def save_target_inventory(
        self, *, inventory_id: str, project_id: str, book: str, range_key: str,
        fingerprint: str, target_revision: str, target_content_hash: str,
        language_id: str, tokenizer_version: str, analyzer_registry_version: str,
        structure_hash: str, diagnostics: dict[str, Any], payload: dict[str, Any],
        token_ids: list[str], unit_ids: list[str], spans: list[dict[str, Any]],
        neighborhoods: list[dict[str, Any]], references: list[str],
    ) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE target_inventory_runs SET lifecycle_status='SUPERSEDED',revision=revision+1 "
                "WHERE project_id=? AND book=? AND range_key=? AND lifecycle_status='ACTIVE' AND fingerprint<>?",
                (project_id, book, range_key, fingerprint),
            )
            existing = conn.execute(
                "SELECT project_id,book,range_key,fingerprint FROM target_inventory_runs WHERE id=?",
                (inventory_id,),
            ).fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO target_inventory_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (inventory_id, project_id, book, range_key, fingerprint, target_revision,
                     target_content_hash, language_id, tokenizer_version, analyzer_registry_version,
                     structure_hash, "ACTIVE", 1, json.dumps(diagnostics, ensure_ascii=False),
                     json.dumps(payload, ensure_ascii=False), self._now()),
                )
            else:
                identity = (project_id, book, range_key, fingerprint)
                persisted_identity = tuple(existing[key] for key in (
                    "project_id", "book", "range_key", "fingerprint",
                ))
                if persisted_identity != identity:
                    raise FoundationConflict(
                        "Target inventory content identity conflicts with an existing record"
                    )
                # Machine-built inventories are content addressed. When an edit
                # is reverted exactly, reuse the identical immutable payload
                # rather than manufacturing a second identity. This never moves
                # or reinterprets a human-reviewed record.
                conn.execute(
                    "UPDATE target_inventory_runs SET lifecycle_status='ACTIVE',revision=revision+1 "
                    "WHERE id=?", (inventory_id,),
                )
            conn.executemany(
                "INSERT OR IGNORE INTO target_inventory_tokens VALUES(?,?)",
                [(inventory_id, item) for item in token_ids],
            )
            conn.executemany(
                "INSERT OR IGNORE INTO target_inventory_units VALUES(?,?)",
                [(inventory_id, item) for item in unit_ids],
            )
            # Content-addressed target records can become current again only
            # when the exact same inventory fingerprint is rebuilt (for
            # example, an edit is deliberately reverted). Preserve identity
            # and review data while making currentness explicit.
            for token_id in token_ids:
                token = conn.execute(
                    "SELECT side,lifecycle_status,revision,payload_json FROM token_instances WHERE id=?",
                    (token_id,),
                ).fetchone()
                if token is not None and token["side"] == "TARGET" and token["lifecycle_status"] != "ACTIVE":
                    token_payload = json.loads(token["payload_json"])
                    token_payload.update({
                        "lifecycleStatus": "ACTIVE", "revision": int(token["revision"]) + 1,
                    })
                    conn.execute(
                        "UPDATE token_instances SET lifecycle_status='ACTIVE',revision=revision+1,payload_json=? WHERE id=?",
                        (json.dumps(token_payload, ensure_ascii=False), token_id),
                    )
            for unit_id in unit_ids:
                unit = conn.execute(
                    "SELECT side,lifecycle_status,revision,payload_json FROM semantic_units WHERE id=?",
                    (unit_id,),
                ).fetchone()
                if unit is not None and unit["side"] == "TARGET" and unit["lifecycle_status"] != "ACTIVE":
                    unit_payload = json.loads(unit["payload_json"])
                    unit_payload.update({
                        "lifecycleStatus": "ACTIVE", "revision": int(unit["revision"]) + 1,
                    })
                    conn.execute(
                        "UPDATE semantic_units SET lifecycle_status='ACTIVE',revision=revision+1,payload_json=? WHERE id=?",
                        (json.dumps(unit_payload, ensure_ascii=False), unit_id),
                    )
                conn.execute(
                    "INSERT OR IGNORE INTO record_dependencies VALUES(?,?,?,?)",
                    ("SEMANTIC_UNIT", unit_id, "TARGET_INVENTORY", inventory_id),
                )
            conn.executemany(
                "INSERT OR IGNORE INTO target_search_spans VALUES(?,?,?,?,?,?,?)",
                [(item["id"], inventory_id, item["displayedReference"], item["kind"],
                  item["startCodePoint"], item["endCodePoint"],
                  json.dumps(item, ensure_ascii=False)) for item in spans],
            )
            conn.executemany(
                "INSERT OR IGNORE INTO target_search_neighborhoods VALUES(?,?,?,?)",
                [(item["id"], inventory_id, item["scopeKind"], json.dumps(item, ensure_ascii=False))
                 for item in neighborhoods],
            )
            for reference in references:
                conn.execute(
                    "INSERT OR IGNORE INTO record_dependencies VALUES(?,?,?,?)",
                    ("TARGET_INVENTORY", inventory_id, "TARGET_REFERENCE",
                     self.target_dependency_id(project_id, book, reference)),
                )
            conn.commit()

    def target_inventory_for_fingerprint(
        self, project_id: str, book: str, range_key: str, fingerprint: str,
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM target_inventory_runs WHERE project_id=? AND book=? "
                "AND range_key=? AND fingerprint=? AND lifecycle_status='ACTIVE'",
                (project_id, book, range_key, fingerprint),
            ).fetchone()
        return None if row is None else json.loads(row[0])

    def target_inventory(self, inventory_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json,lifecycle_status FROM target_inventory_runs WHERE id=?",
                (inventory_id,),
            ).fetchone()
        if row is None:
            raise FoundationValidationError(f"Unknown target inventory: {inventory_id}")
        if row["lifecycle_status"] != "ACTIVE":
            raise FoundationValidationError("Target inventory is stale or inactive")
        return json.loads(row["payload_json"])

    def save_semantic_location_run(
        self, *, run_id: str, project_id: str, book: str, range_key: str,
        fingerprint: str, source_inventory_id: str, target_inventory_id: str,
        run_status: str, payload: dict[str, Any], candidates: list[dict[str, Any]],
        relationships: list[dict[str, Any]], alignment_dependency_id: str,
    ) -> None:
        """Atomically publish an immutable source-to-target location run."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            source = conn.execute(
                "SELECT lifecycle_status FROM source_inventory_runs WHERE id=?",
                (source_inventory_id,),
            ).fetchone()
            target = conn.execute(
                "SELECT lifecycle_status FROM target_inventory_runs WHERE id=?",
                (target_inventory_id,),
            ).fetchone()
            if source is None or source[0] != "ACTIVE" or target is None or target[0] != "ACTIVE":
                raise FoundationValidationError(
                    "Semantic location requires active independent source and target inventories"
                )
            conn.execute(
                "UPDATE semantic_location_runs SET lifecycle_status='SUPERSEDED',revision=revision+1 "
                "WHERE project_id=? AND book=? AND range_key=? AND lifecycle_status='ACTIVE' "
                "AND fingerprint<>?",
                (project_id, book, range_key, fingerprint),
            )
            conn.execute(
                "INSERT INTO semantic_location_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, project_id, book, range_key, fingerprint, source_inventory_id,
                 target_inventory_id, run_status, "ACTIVE", 1,
                 json.dumps(payload, ensure_ascii=False), self._now()),
            )
            conn.executemany(
                "INSERT INTO semantic_location_candidates VALUES(?,?,?,?,?)",
                [(item["id"], run_id, item["sourceOwnerUnitId"], item["rank"],
                  json.dumps(item, ensure_ascii=False)) for item in candidates],
            )
            conn.executemany(
                "INSERT INTO semantic_location_relationships VALUES(?,?,?,?,?,?,?,?,?)",
                [(item["id"], run_id, item["sourceOwnerUnitId"], item["locationOutcome"],
                  item.get("selectedCandidateId"), item["reviewStatus"],
                  item["lifecycleStatus"], item["revision"],
                  json.dumps(item, ensure_ascii=False)) for item in relationships],
            )
            conn.executemany(
                "INSERT OR IGNORE INTO record_dependencies VALUES(?,?,?,?)",
                [
                    ("LOCATION_RUN", run_id, "SOURCE_INVENTORY", source_inventory_id),
                    ("LOCATION_RUN", run_id, "TARGET_INVENTORY", target_inventory_id),
                    # V11-000a review fix (F2): registered here, inside the same
                    # publish transaction as the run row itself, not via a
                    # separate add_record_dependency() call after commit -- a
                    # crash between the two would leave an ACTIVE LOCATION_RUN
                    # with no WORD_ALIGNMENT edge, permanently immune to
                    # apply_alignment_invalidation. Unconditional, even when no
                    # alignment evidence was found this run: a *future*
                    # completed alignment for a verse in this range could still
                    # change the outcome, so this run depends on the book's
                    # current alignment state either way.
                    ("LOCATION_RUN", run_id, "WORD_ALIGNMENT", alignment_dependency_id),
                    # Each relationship depends on the run that produced it.
                    # Without this edge a re-run staled the run but left every
                    # individual location relationship looking current, so
                    # downstream meaning assessments and QA findings anchored to
                    # them never learned their evidence had moved.
                    *[("LOCATION_RELATIONSHIP", item["id"], "LOCATION_RUN", run_id)
                      for item in relationships],
                ],
            )
            conn.commit()

    def semantic_location_for_fingerprint(
        self, project_id: str, book: str, range_key: str, fingerprint: str,
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM semantic_location_runs WHERE project_id=? AND book=? "
                "AND range_key=? AND fingerprint=? AND lifecycle_status='ACTIVE' "
                "AND run_status='COMPLETE'",
                (project_id, book, range_key, fingerprint),
            ).fetchone()
        return None if row is None else json.loads(row[0])

    def semantic_location_run(self, run_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json,lifecycle_status FROM semantic_location_runs WHERE id=?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise FoundationValidationError(f"Unknown semantic location run: {run_id}")
        if row["lifecycle_status"] != "ACTIVE":
            raise FoundationValidationError("Semantic location run is stale or inactive")
        return json.loads(row["payload_json"])

    def semantic_location_candidates(
        self, run_id: str, source_owner_unit_id: str = "",
    ) -> list[dict[str, Any]]:
        self.semantic_location_run(run_id)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM semantic_location_candidates WHERE run_id=? "
                "AND (?='' OR source_owner_unit_id=?) ORDER BY source_owner_unit_id,candidate_rank,id",
                (run_id, source_owner_unit_id, source_owner_unit_id),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def semantic_location_relationship(self, relationship_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT r.payload_json,r.run_id FROM semantic_location_relationships r WHERE r.id=?",
                (relationship_id,),
            ).fetchone()
        if row is None:
            raise FoundationValidationError(
                f"Unknown semantic location relationship: {relationship_id}"
            )
        self.semantic_location_run(row["run_id"])
        # The run id is a column, not part of the immutable payload, and every
        # reader that wants to reach the run's target inventory needs it: the
        # Stage 9B.2 correction review context skips any location without one,
        # so dropping it here left `candidateSpans` and the candidate
        # `alternatives` list permanently empty in the running app.
        return {**json.loads(row["payload_json"]), "runId": row["run_id"]}

    def save_meaning_analysis_run(
        self, *, run_id: str, project_id: str, book: str, range_key: str,
        fingerprint: str, location_run_id: str, run_status: str,
        payload: dict[str, Any], assessments: list[dict[str, Any]],
    ) -> None:
        """Atomically publish immutable Stage 7 assessments and dependencies."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            location = conn.execute(
                "SELECT lifecycle_status FROM semantic_location_runs WHERE id=?",
                (location_run_id,),
            ).fetchone()
            if location is None or location[0] != "ACTIVE":
                raise FoundationValidationError("Meaning analysis requires an active location run")
            conn.execute(
                "UPDATE meaning_analysis_runs SET lifecycle_status='SUPERSEDED',revision=revision+1 "
                "WHERE project_id=? AND book=? AND range_key=? AND lifecycle_status='ACTIVE' "
                "AND fingerprint<>?", (project_id, book, range_key, fingerprint),
            )
            conn.execute(
                "INSERT INTO meaning_analysis_runs VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, project_id, book, range_key, fingerprint, location_run_id,
                 run_status, "ACTIVE", 1, json.dumps(payload, ensure_ascii=False), self._now()),
            )
            conn.executemany(
                "INSERT INTO meaning_assessments VALUES(?,?,?,?,?,?,?,?)",
                [(item["id"], run_id, item["semanticLocationRelationshipId"],
                  item["meaningStatus"], item["reviewStatus"], item["lifecycleStatus"],
                  item["revision"], json.dumps(item, ensure_ascii=False),)
                 for item in assessments],
            )
            conn.executemany(
                "INSERT INTO meaning_component_assessments VALUES(?,?,?,?,?)",
                [(component["id"], item["id"], component["coverageDimension"],
                  component["status"], json.dumps(component, ensure_ascii=False))
                 for item in assessments for component in item["componentAssessments"]],
            )
            conn.executemany(
                "INSERT OR IGNORE INTO record_dependencies VALUES(?,?,?,?)",
                [
                    ("MEANING_RUN", run_id, "LOCATION_RUN", location_run_id),
                    *[("MEANING_ASSESSMENT", item["id"], "MEANING_RUN", run_id)
                      for item in assessments],
                    # A meaning assessment is *about* one located relationship.
                    # Registering that directly is what makes a human rejecting
                    # a mapping stale the meaning built on it -- going only via
                    # the run meant a per-relationship decision propagated nowhere.
                    *[("MEANING_ASSESSMENT", item["id"], "LOCATION_RELATIONSHIP",
                       str(item.get("semanticLocationRelationshipId") or ""))
                      for item in assessments
                      if str(item.get("semanticLocationRelationshipId") or "")],
                ],
            )
            conn.commit()

    def meaning_analysis_for_fingerprint(
        self, project_id: str, book: str, range_key: str, fingerprint: str,
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM meaning_analysis_runs WHERE project_id=? AND book=? "
                "AND range_key=? AND fingerprint=? AND lifecycle_status='ACTIVE' "
                "AND run_status='COMPLETE'", (project_id, book, range_key, fingerprint),
            ).fetchone()
        return None if row is None else json.loads(row[0])

    def meaning_analysis_run(self, run_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json,lifecycle_status FROM meaning_analysis_runs WHERE id=?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise FoundationValidationError(f"Unknown meaning analysis run: {run_id}")
        if row["lifecycle_status"] != "ACTIVE":
            raise FoundationValidationError("Meaning analysis run is stale or inactive")
        return json.loads(row["payload_json"])

    def meaning_assessment(self, assessment_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json,run_id FROM meaning_assessments WHERE id=?",
                (assessment_id,),
            ).fetchone()
        if row is None:
            raise FoundationValidationError(f"Unknown meaning assessment: {assessment_id}")
        self.meaning_analysis_run(row["run_id"])
        return json.loads(row["payload_json"])

    def meaning_components(self, assessment_id: str) -> list[dict[str, Any]]:
        self.meaning_assessment(assessment_id)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM meaning_component_assessments "
                "WHERE assessment_id=? ORDER BY coverage_dimension,id", (assessment_id,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def coverage_account(self, account_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM coverage_accounts WHERE id=?", (account_id,),
            ).fetchone()
        if row is None:
            raise FoundationValidationError(f"Unknown coverage account: {account_id}")
        return json.loads(row[0])

    def coverage_accounts_for_owners(
        self, project_id: str, direction: str, owner_unit_ids: list[str],
    ) -> list[dict[str, Any]]:
        if not owner_unit_ids:
            return []
        placeholders = ",".join("?" for _ in owner_unit_ids)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT payload_json FROM coverage_accounts WHERE project_id=? AND direction=? "
                f"AND audit_owner_unit_id IN ({placeholders}) AND lifecycle_status='ACTIVE'",
                (project_id, direction, *owner_unit_ids),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def update_coverage_account_status(
        self, account_id: str, *, coverage_status: str,
        covered_by_relationship_ids: tuple[str, ...], finding_id: str | None,
        expected_revision: int,
    ) -> None:
        """Finalize a content-addressed coverage account in place (item 3/7/14).

        Stage 5/6A seed SOURCE_COVERAGE/TARGET_SUPPORT accounts with a
        placeholder status; the QA audit updates the same immutable identity
        rather than inserting a duplicate row (the account's unique index is
        keyed by owner/dimension/fingerprint, not by audit run).
        """
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT payload_json FROM coverage_accounts WHERE id=?", (account_id,),
            ).fetchone()
            if row is None:
                raise FoundationValidationError(f"Unknown coverage account: {account_id}")
            payload = json.loads(row[0])
            payload.update({
                "coverageStatus": coverage_status,
                "coveredByRelationshipIds": list(covered_by_relationship_ids),
                "findingId": finding_id,
                "revision": expected_revision + 1,
            })
            changed = conn.execute(
                "UPDATE coverage_accounts SET finding_id=?,revision=revision+1,payload_json=? "
                "WHERE id=? AND revision=?",
                (finding_id, json.dumps(payload, ensure_ascii=False), account_id, expected_revision),
            ).rowcount
            if changed != 1:
                raise FoundationConflict("Coverage account revision conflict")
            conn.commit()

    def save_qa_audit_run(
        self, *, run_id: str, project_id: str, book: str, range_key: str,
        fingerprint: str, meaning_run_id: str, run_status: str, payload: dict[str, Any],
    ) -> None:
        """Atomically publish an immutable Stage 8 QA-audit run record.

        Coverage accounts and findings are saved individually (via
        save_coverage_account/save_qa_finding/update_coverage_account_status)
        before this call, so their ids are already embedded in payload.
        """
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            meaning = conn.execute(
                "SELECT lifecycle_status FROM meaning_analysis_runs WHERE id=?",
                (meaning_run_id,),
            ).fetchone()
            if meaning is None or meaning[0] != "ACTIVE":
                raise FoundationValidationError("QA audit requires an active meaning analysis run")
            conn.execute(
                "UPDATE qa_audit_runs SET lifecycle_status='SUPERSEDED',revision=revision+1 "
                "WHERE project_id=? AND book=? AND range_key=? AND lifecycle_status='ACTIVE' "
                "AND fingerprint<>?", (project_id, book, range_key, fingerprint),
            )
            conn.execute(
                "INSERT INTO qa_audit_runs VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, project_id, book, range_key, fingerprint, meaning_run_id,
                 run_status, "ACTIVE", 1, json.dumps(payload, ensure_ascii=False), self._now()),
            )
            dependency_edges = [("QA_RUN", run_id, "MEANING_RUN", meaning_run_id)]
            for account_id in payload.get("sourceCoverageAccountIds", ()):
                dependency_edges.append(("COVERAGE_ACCOUNT", account_id, "QA_RUN", run_id))
            for account_id in payload.get("targetSupportAccountIds", ()):
                dependency_edges.append(("COVERAGE_ACCOUNT", account_id, "QA_RUN", run_id))
            for finding in payload.get("findings", ()):
                dependency_edges.append(("QA_FINDING", finding["id"], "QA_RUN", run_id))
                for account_id in finding.get("coverageAccountIds", ()):
                    dependency_edges.append(
                        ("QA_FINDING", finding["id"], "COVERAGE_ACCOUNT", account_id)
                    )
                # A finding rests on the specific meaning assessments and
                # semantic units it cites, not merely on the run as a whole.
                # Registering only the run meant a human rejecting one mapping,
                # or overriding one meaning, left every finding built on it
                # looking current -- and therefore still correction-eligible.
                for assessment_id in finding.get("meaningAssessmentIds", ()):
                    dependency_edges.append(
                        ("QA_FINDING", finding["id"], "MEANING_ASSESSMENT", assessment_id)
                    )
                for unit_id in finding.get("sourceSemanticUnitIds", ()):
                    dependency_edges.append(
                        ("QA_FINDING", finding["id"], "SEMANTIC_UNIT", unit_id)
                    )
                for relationship_id in finding.get("semanticRelationshipIds", ()):
                    dependency_edges.append(
                        ("QA_FINDING", finding["id"], "SEMANTIC_RELATIONSHIP", relationship_id)
                    )
            conn.executemany(
                "INSERT OR IGNORE INTO record_dependencies VALUES(?,?,?,?)", dependency_edges,
            )
            conn.commit()

    def qa_audit_for_fingerprint(
        self, project_id: str, book: str, range_key: str, fingerprint: str,
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM qa_audit_runs WHERE project_id=? AND book=? "
                "AND range_key=? AND fingerprint=? AND lifecycle_status='ACTIVE' "
                "AND run_status='COMPLETE'", (project_id, book, range_key, fingerprint),
            ).fetchone()
        return None if row is None else json.loads(row[0])

    def qa_audit_run(self, run_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json,lifecycle_status FROM qa_audit_runs WHERE id=?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise FoundationValidationError(f"Unknown QA audit run: {run_id}")
        if row["lifecycle_status"] != "ACTIVE":
            raise FoundationValidationError("QA audit run is stale or inactive")
        return json.loads(row["payload_json"])

    # -- Stage 9A.4 analysis orchestration jobs ---------------------------

    @staticmethod
    def _validate_analysis_job(payload: dict[str, Any]) -> None:
        required = {
            "jobId", "projectId", "book", "requestedScope", "rangeKey",
            "canonicalReferences", "overallStatus", "stageStatuses",
            "stageProgress", "reusedRunIds", "createdRunIds", "warnings",
            "failures", "cancellationRequested", "targetContentHash",
            "sourceResourceHash", "createdAt",
        }
        missing = sorted(required - set(payload))
        if missing:
            raise FoundationValidationError(
                "Analysis job is missing required fields: " + ", ".join(missing)
            )
        scope = payload.get("requestedScope")
        if not isinstance(scope, dict) or scope.get("kind") not in {
            "CURRENT_PASSAGE", "CURRENT_CHAPTER", "CURRENT_BOOK",
            "SELECTED_RANGE", "AFFECTED",
        }:
            raise FoundationValidationError("Invalid analysis job requestedScope")
        if payload.get("overallStatus") not in {
            "QUEUED", "RUNNING", "COMPLETED", "COMPLETED_WITH_WARNINGS",
            "FAILED", "CANCELLED",
        }:
            raise FoundationValidationError("Invalid analysis job overallStatus")
        stage_names = {"SOURCE_INVENTORY", "TARGET_INVENTORY", "LOCATION", "MEANING", "QA"}
        stage_statuses = payload.get("stageStatuses")
        if not isinstance(stage_statuses, dict) or set(stage_statuses) != stage_names:
            raise FoundationValidationError("Invalid analysis job stageStatuses")
        allowed_stage_statuses = {
            "NOT_STARTED", "RUNNING", "COMPLETED", "REUSED", "FAILED", "CANCELLED",
        }
        if any(
            not isinstance(value, dict) or value.get("status") not in allowed_stage_statuses
            for value in stage_statuses.values()
        ):
            raise FoundationValidationError("Invalid analysis job stage status")
        progress = payload.get("stageProgress")
        if (
            not isinstance(progress, dict)
            or not isinstance(progress.get("completedStages"), int)
            or progress.get("totalStages") != len(stage_names)
            or not 0 <= progress["completedStages"] <= progress["totalStages"]
        ):
            raise FoundationValidationError("Invalid analysis job stageProgress")
        fingerprint = payload.get("analysisFingerprint")
        if fingerprint is not None and (
            not isinstance(fingerprint, str) or len(fingerprint) != 64
        ):
            raise FoundationValidationError("Invalid analysis job analysisFingerprint")
        policy_versions = payload.get("policyVersions")
        if policy_versions is not None and (
            not isinstance(policy_versions, dict)
            or any(not isinstance(key, str) or not isinstance(value, str)
                   for key, value in policy_versions.items())
        ):
            raise FoundationValidationError("Invalid analysis job policyVersions")

    def create_analysis_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._validate_analysis_job(payload)
        stored = dict(payload)
        stored["revision"] = 1
        try:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "INSERT INTO analysis_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        stored["jobId"], stored["projectId"], stored["book"],
                        stored["requestedScope"]["kind"], stored["rangeKey"],
                        stored["overallStatus"], stored["targetContentHash"],
                        stored["sourceResourceHash"], 1, stored["createdAt"],
                        stored.get("startedAt"), stored.get("completedAt"),
                        json.dumps(stored, ensure_ascii=False),
                    ),
                )
                conn.commit()
        except sqlite3.IntegrityError as exc:
            raise FoundationConflict(
                "Another analysis job is already active for this project"
            ) from exc
        return stored

    def update_analysis_job(
        self, job_id: str, payload: dict[str, Any], expected_revision: int,
    ) -> dict[str, Any]:
        self._validate_analysis_job(payload)
        stored = dict(payload)
        stored["revision"] = expected_revision + 1
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            changed = conn.execute(
                "UPDATE analysis_jobs SET overall_status=?,target_content_hash=?,"
                "source_resource_hash=?,revision=?,started_at=?,completed_at=?,payload_json=? "
                "WHERE id=? AND revision=?",
                (
                    stored["overallStatus"], stored["targetContentHash"],
                    stored["sourceResourceHash"], stored["revision"],
                    stored.get("startedAt"), stored.get("completedAt"),
                    json.dumps(stored, ensure_ascii=False), job_id, expected_revision,
                ),
            ).rowcount
            if changed != 1:
                conn.rollback()
                raise FoundationConflict("Analysis job revision conflict")
            conn.commit()
        return stored

    def analysis_job(self, job_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM analysis_jobs WHERE id=?", (job_id,),
            ).fetchone()
        if row is None:
            raise FoundationValidationError(f"Unknown analysis job: {job_id}")
        return json.loads(row[0])

    def recent_analysis_jobs(
        self, project_id: str, *, book: str = "", limit: int = 20,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(100, int(limit)))
        sql = "SELECT payload_json FROM analysis_jobs WHERE project_id=?"
        params: list[Any] = [project_id]
        if book:
            sql += " AND book=?"
            params.append(book)
        sql += " ORDER BY created_at DESC,id DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [json.loads(row[0]) for row in rows]

    def recover_analysis_jobs(
        self, project_id: str, *, active_job_ids: tuple[str, ...] = (),
    ) -> int:
        """Mark workers lost with a previous sidecar process as failed.

        In-memory active ids are excluded so reopening the same project while
        its worker is alive does not incorrectly terminate that job.
        """
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT id,revision,payload_json FROM analysis_jobs "
                "WHERE project_id=? AND overall_status IN ('QUEUED','RUNNING')",
                (project_id,),
            ).fetchall()
            recovered = 0
            for row in rows:
                if row["id"] in active_job_ids:
                    continue
                payload = json.loads(row["payload_json"])
                abandoned_stage = str(payload.get("currentStage") or "ORCHESTRATION")
                payload["overallStatus"] = "FAILED"
                payload["completedAt"] = self._now()
                payload["currentStage"] = ""
                payload.setdefault("failures", []).append({
                    "stage": abandoned_stage,
                    "code": "INTERRUPTED",
                    "message": "Analysis was interrupted when Bridge closed.",
                })
                payload["revision"] = int(row["revision"]) + 1
                conn.execute(
                    "UPDATE analysis_jobs SET overall_status='FAILED',revision=?,"
                    "completed_at=?,payload_json=? WHERE id=? AND revision=?",
                    (
                        payload["revision"], payload["completedAt"],
                        json.dumps(payload, ensure_ascii=False), row["id"], row["revision"],
                    ),
                )
                recovered += 1
            conn.commit()
        return recovered

    def human_approved_lexical_precedents(self, project_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT g.payload_json FROM lexical_groups g "
                "JOIN lexical_solutions s ON s.id=g.solution_id "
                "WHERE s.project_id=? AND s.lifecycle_status='ACTIVE' "
                "AND g.lifecycle_status='ACTIVE' AND g.review_status='HUMAN_APPROVED'",
                (project_id,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def embedding_vectors(
        self, content_hashes: list[str], model_hash: str,
    ) -> dict[str, list[float]]:
        if not content_hashes:
            return {}
        placeholders = ",".join("?" for _ in content_hashes)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT content_hash,vector_json FROM semantic_embedding_cache "
                f"WHERE model_hash=? AND content_hash IN ({placeholders})",
                (model_hash, *content_hashes),
            ).fetchall()
        return {row["content_hash"]: json.loads(row["vector_json"]) for row in rows}

    def save_embedding_vectors(
        self, *, model_hash: str, dimensions: int, normalization: str,
        vectors: dict[str, list[float]],
    ) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.executemany(
                "INSERT OR IGNORE INTO semantic_embedding_cache VALUES(?,?,?,?,?,?,?)",
                [("embedding-" + hashlib.sha256(
                    f"{model_hash}\u241f{content_hash}".encode("utf-8")
                ).hexdigest()[:32], content_hash, model_hash, dimensions, normalization,
                  json.dumps(vector), self._now()) for content_hash, vector in vectors.items()],
            )
            conn.commit()

    def save_semantic_relationship(self, relationship: SemanticRelationship) -> None:
        payload = to_wire(relationship)
        with self._connect() as conn:
            for unit_id in (*relationship.source_semantic_unit_ids, *relationship.target_semantic_unit_ids):
                if conn.execute("SELECT 1 FROM semantic_units WHERE id=?", (unit_id,)).fetchone() is None:
                    raise FoundationValidationError(f"Unknown relationship semantic unit: {unit_id}")
            conn.execute(
                "INSERT INTO semantic_relationships VALUES(?,?,?,?,?,?)",
                (relationship.id, relationship.project_id, relationship.review_status.value,
                 relationship.lifecycle_status.value, relationship.revision,
                 json.dumps(payload, ensure_ascii=False)),
            )
            conn.commit()

    def semantic_relationship(self, relationship_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT payload_json FROM semantic_relationships WHERE id=?", (relationship_id,)).fetchone()
        if row is None:
            raise FoundationValidationError(f"Unknown semantic relationship: {relationship_id}")
        return json.loads(row[0])

    def save_exportability(self, exportability: Exportability) -> None:
        policy_id = self._ensure_policy(exportability.policy_binding)
        payload = to_wire(exportability)
        with self._connect() as conn:
            try:
                conn.execute(
                    "INSERT INTO exportability_records VALUES(?,?,?,?,?,?,?,?)",
                    (exportability.id, exportability.relationship_id, exportability.format.value,
                     exportability.level.value, policy_id, exportability.lifecycle_status.value,
                     exportability.revision, json.dumps(payload, ensure_ascii=False)),
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                raise FoundationConflict(f"Invalid or competing exportability record: {exc}") from exc

    def exportability(self, exportability_id: str) -> dict[str, Any]:
        return self._load_payload("exportability_records", exportability_id, "exportability")

    def create_lexical_solution(
        self, *, solution_id: str, project_id: str, scope_key: str, profile_id: str,
        source_layer: TokenLayer | None, target_layer: TokenLayer | None,
        authoritative: bool = False, policy: PolicyBinding | None = None,
    ) -> None:
        policy = policy or PolicyBinding.foundation_v1()
        policy_id = self._ensure_policy(policy)
        lifecycle = LifecycleStatus.ACTIVE if authoritative else LifecycleStatus.INACTIVE
        payload = {
            "id": solution_id, "projectId": project_id, "scopeKey": scope_key, "profileId": profile_id,
            "sourceLayer": source_layer.value if source_layer else None,
            "targetLayer": target_layer.value if target_layer else None,
            "authoritative": authoritative, "policyBinding": to_wire(policy),
            "reviewStatus": ReviewStatus.UNREVIEWED.value, "lifecycleStatus": lifecycle.value, "revision": 1,
        }
        try:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "INSERT INTO lexical_solutions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (solution_id, project_id, scope_key, profile_id, payload["sourceLayer"], payload["targetLayer"],
                     int(authoritative), policy_id, ReviewStatus.UNREVIEWED.value, lifecycle.value, 1, json.dumps(payload)),
                )
                conn.commit()
        except sqlite3.IntegrityError as exc:
            if "uq_authoritative_active_lexical_solution" in str(exc) or "UNIQUE constraint failed" in str(exc):
                raise FoundationConflict("An authoritative active lexical solution already exists for this scope/profile/layers") from exc
            raise

    def lexical_solution(self, solution_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT payload_json FROM lexical_solutions WHERE id=?", (solution_id,)).fetchone()
        if row is None:
            raise FoundationValidationError(f"Unknown lexical solution: {solution_id}")
        return json.loads(row[0])

    def activate_lexical_solution(
        self, solution_id: str, *, expected_revision: int, authoritative: bool,
        supersede_solution_id: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM lexical_solutions WHERE id=?", (solution_id,)).fetchone()
            if row is None:
                raise FoundationValidationError(f"Unknown lexical solution: {solution_id}")
            if int(row["revision"]) != expected_revision:
                raise FoundationConflict("Lexical solution revision conflict")
            active = conn.execute(
                "SELECT id FROM lexical_solutions WHERE project_id=? AND scope_key=? AND profile_id=? "
                "AND IFNULL(source_layer,'<NULL>')=IFNULL(?, '<NULL>') AND IFNULL(target_layer,'<NULL>')=IFNULL(?, '<NULL>') "
                "AND authoritative=1 AND lifecycle_status='ACTIVE' AND id<>?",
                (row["project_id"], row["scope_key"], row["profile_id"], row["source_layer"], row["target_layer"], solution_id),
            ).fetchone()
            if active is not None:
                if supersede_solution_id != active["id"]:
                    raise FoundationConflict("An authoritative active lexical solution already exists; explicitly supersede it")
                self._update_solution_state(conn, active["id"], LifecycleStatus.SUPERSEDED, authoritative=False)
            self._activate_memberships(conn, solution_id)
            payload = json.loads(row["payload_json"])
            payload.update({"authoritative": bool(authoritative), "lifecycleStatus": "ACTIVE", "revision": expected_revision + 1})
            changed = conn.execute(
                "UPDATE lexical_solutions SET authoritative=?,lifecycle_status='ACTIVE',revision=revision+1,payload_json=? WHERE id=? AND revision=?",
                (int(authoritative), json.dumps(payload), solution_id, expected_revision),
            ).rowcount
            if changed != 1:
                raise FoundationConflict("Lexical solution revision conflict")
            conn.commit()

    def _update_solution_state(self, conn: sqlite3.Connection, solution_id: str, status: LifecycleStatus, *, authoritative: bool) -> None:
        row = conn.execute("SELECT payload_json FROM lexical_solutions WHERE id=?", (solution_id,)).fetchone()
        if row is None:
            raise FoundationValidationError(f"Unknown superseded solution: {solution_id}")
        payload = json.loads(row[0]); payload["lifecycleStatus"] = status.value
        payload["authoritative"] = bool(authoritative); payload["revision"] = int(payload["revision"]) + 1
        conn.execute("DELETE FROM active_lexical_membership WHERE lexical_solution_id=?", (solution_id,))
        conn.execute(
            "UPDATE lexical_solutions SET authoritative=?,lifecycle_status=?,revision=revision+1,payload_json=? WHERE id=?",
            (int(authoritative), status.value, json.dumps(payload), solution_id),
        )

    def add_lexical_group(
        self, *, group_id: str, solution_id: str, cardinality: Cardinality,
        source_layer: TokenLayer | None, target_layer: TokenLayer | None,
        source_token_ids: tuple[str, ...], target_token_ids: tuple[str, ...],
        alignment_family_id: str | None = None, refines_group_id: str | None = None,
    ) -> None:
        self._validate_group(cardinality, source_layer, target_layer, source_token_ids, target_token_ids)
        policy = PolicyBinding.foundation_v1()
        payload = {
            "id": group_id, "solutionId": solution_id, "cardinality": cardinality.value,
            "sourceLayer": source_layer.value if source_layer else None,
            "targetLayer": target_layer.value if target_layer else None,
            "sourceTokenInstanceIds": list(source_token_ids), "targetTokenInstanceIds": list(target_token_ids),
            "alignmentFamilyId": alignment_family_id or group_id, "refinesGroupId": refines_group_id,
            "policyBinding": to_wire(policy), "reviewStatus": "UNREVIEWED", "lifecycleStatus": "INACTIVE", "revision": 1,
        }
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO lexical_groups VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (group_id, solution_id, cardinality.value, payload["sourceLayer"], payload["targetLayer"],
                 json.dumps(list(source_token_ids)), json.dumps(list(target_token_ids)), payload["alignmentFamilyId"],
                 refines_group_id, "UNREVIEWED", "INACTIVE", 1, json.dumps(payload)),
            )
            conn.commit()

    @staticmethod
    def _validate_group(cardinality: Cardinality, source_layer: TokenLayer | None, target_layer: TokenLayer | None,
                        source_ids: tuple[str, ...], target_ids: tuple[str, ...]) -> None:
        if len(set(source_ids)) != len(source_ids) or len(set(target_ids)) != len(target_ids):
            raise FoundationValidationError("A lexical group cannot repeat a token instance")
        if cardinality == Cardinality.SOURCE_TO_NULL:
            if not source_ids or target_ids or source_layer is None or target_layer is not None:
                raise FoundationValidationError("SOURCE_TO_NULL requires source tokens/layer and an absent target layer")
            return
        if cardinality == Cardinality.NULL_TO_TARGET:
            if source_ids or not target_ids or source_layer is not None or target_layer is None:
                raise FoundationValidationError("NULL_TO_TARGET requires target tokens/layer and an absent source layer")
            return
        if not source_ids or not target_ids or source_layer is None or target_layer is None:
            raise FoundationValidationError("A non-null lexical group requires tokens and layers on both sides")
        expected = {
            Cardinality.ONE_TO_ONE: (1, 1), Cardinality.ONE_TO_MANY: (1, None),
            Cardinality.MANY_TO_ONE: (None, 1), Cardinality.MANY_TO_MANY: (None, None),
        }[cardinality]
        s, t = expected
        if (s == 1 and len(source_ids) != 1) or (s is None and len(source_ids) < 2):
            raise FoundationValidationError(f"{cardinality.value} has invalid source cardinality")
        if (t == 1 and len(target_ids) != 1) or (t is None and len(target_ids) < 2):
            raise FoundationValidationError(f"{cardinality.value} has invalid target cardinality")

    def _activate_memberships(self, conn: sqlite3.Connection, solution_id: str) -> None:
        conn.execute("DELETE FROM active_lexical_membership WHERE lexical_solution_id=?", (solution_id,))
        groups = conn.execute("SELECT * FROM lexical_groups WHERE solution_id=?", (solution_id,)).fetchall()
        for group in groups:
            for side, layer_col, ids_col in (("SOURCE", "source_layer", "source_token_ids_json"), ("TARGET", "target_layer", "target_token_ids_json")):
                layer = group[layer_col]
                ids = json.loads(group[ids_col])
                if not ids:
                    if layer is not None:
                        raise FoundationValidationError(f"Null side of group {group['id']} must have a null layer")
                    continue
                if layer is None:
                    raise FoundationValidationError(f"Populated side of group {group['id']} must declare its layer")
                for token_id in ids:
                    token = conn.execute("SELECT side,token_layer,parent_instance_id FROM token_instances WHERE id=?", (token_id,)).fetchone()
                    if token is None:
                        raise FoundationValidationError(f"Unknown token instance in lexical group: {token_id}")
                    if token["side"] != side or token["token_layer"] != layer:
                        raise FoundationValidationError(f"Token side/layer mismatch for {token_id}")
                    try:
                        conn.execute("INSERT INTO active_lexical_membership VALUES(?,?,?,?,?)", (solution_id, side, layer, token_id, group["id"]))
                    except sqlite3.IntegrityError as exc:
                        raise FoundationConflict(f"Token instance {token_id} belongs to more than one active {layer} lexical group") from exc
            payload = json.loads(group["payload_json"]); payload["lifecycleStatus"] = "ACTIVE"; payload["revision"] += 1
            conn.execute("UPDATE lexical_groups SET lifecycle_status='ACTIVE',revision=revision+1,payload_json=? WHERE id=?", (json.dumps(payload), group["id"]))
        conflicts = conn.execute(
            "SELECT child.lexical_group_id AS child_group,parent.lexical_group_id AS parent_group,"
            "cg.refines_group_id,cg.alignment_family_id AS child_family,pg.alignment_family_id AS parent_family "
            "FROM active_lexical_membership child "
            "JOIN token_instances ti ON ti.id=child.token_instance_id "
            "JOIN active_lexical_membership parent ON parent.lexical_solution_id=child.lexical_solution_id "
            " AND parent.token_side=child.token_side AND parent.token_instance_id=ti.parent_instance_id "
            "JOIN lexical_groups cg ON cg.id=child.lexical_group_id "
            "JOIN lexical_groups pg ON pg.id=parent.lexical_group_id "
            "WHERE child.lexical_solution_id=? AND child.lexical_group_id<>parent.lexical_group_id",
            (solution_id,),
        ).fetchall()
        for conflict in conflicts:
            if conflict["refines_group_id"] != conflict["parent_group"] or conflict["child_family"] != conflict["parent_family"]:
                raise FoundationConflict(
                    f"Cross-layer group {conflict['child_group']} must explicitly refine parent group {conflict['parent_group']} in the same alignment family"
                )

    def create_minimal_semantic_unit(self, unit_id: str, *, project_id: str) -> None:
        payload = {
            "id": unit_id, "side": "SOURCE", "projectId": project_id, "book": "PHP", "kind": "LEXICAL",
            "displayedReferences": ["PHP 1:1"], "canonicalReferences": ["PHP 1:1"], "tokenInstanceIds": [],
            "tokenLineageIds": [], "rawSurface": "", "normalizedSurface": "", "semanticFeatures": {},
            "unitConfidence": {"rawScore": None, "calibratedValue": 0.0, "confidencePolicyVersion": "confidence-v1", "calibrationVersion": "calibration-v1"},
            "provenance": "DETERMINISTIC_RULE", "evidenceIds": [], "resourceValidationIds": [],
            "auditEligibility": "ELIGIBLE", "semanticObligation": "REQUIRED", "accountingRole": "PRIMARY",
            "auditOwnerUnitId": unit_id, "coverageDimension": "LEXICAL_CONTENT", "semanticFingerprint": unit_id,
            "policyBinding": to_wire(PolicyBinding.foundation_v1()), "reviewStatus": "UNREVIEWED", "lifecycleStatus": "ACTIVE", "revision": 1,
        }
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO semantic_units VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (unit_id, project_id, "SOURCE", "LEXICAL", unit_id, "ELIGIBLE", "REQUIRED", "PRIMARY",
                 "LEXICAL_CONTENT", unit_id, "UNREVIEWED", "ACTIVE", 1, json.dumps(payload)),
            )
            conn.commit()

    def add_dependency(self, parent_unit_id: str, child_unit_id: str, relation: str | DependencyRelation) -> None:
        relation = DependencyRelation(str(relation))
        if parent_unit_id == child_unit_id:
            raise FoundationValidationError("Semantic dependency DAG cannot contain a cycle")
        with self._connect() as conn:
            exists = conn.execute(
                "WITH RECURSIVE descendants(id) AS ("
                "SELECT child_unit_id FROM semantic_unit_dependencies WHERE parent_unit_id=? "
                "UNION SELECT d.child_unit_id FROM semantic_unit_dependencies d JOIN descendants x ON d.parent_unit_id=x.id"
                ") SELECT 1 FROM descendants WHERE id=? LIMIT 1",
                (child_unit_id, parent_unit_id),
            ).fetchone()
            if exists is not None:
                raise FoundationValidationError("Semantic dependency would create a cycle")
            try:
                conn.execute("INSERT INTO semantic_unit_dependencies VALUES(?,?,?,?)", (parent_unit_id, child_unit_id, relation.value, "{}"))
                conn.commit()
            except sqlite3.IntegrityError as exc:
                raise FoundationValidationError(f"Invalid semantic dependency: {exc}") from exc

    def add_semantic_relation(self, left_unit_id: str, right_unit_id: str, relation: str | SemanticRelation) -> None:
        relation = SemanticRelation(str(relation))
        with self._connect() as conn:
            conn.execute("INSERT INTO semantic_unit_relations VALUES(?,?,?,?)", (left_unit_id, right_unit_id, relation.value, "{}"))
            conn.commit()

    def create_qa_finding(self, finding_id: str, project_id: str) -> None:
        policy = PolicyBinding.foundation_v1(); policy_id = self._ensure_policy(policy)
        payload = {
            "id": finding_id, "projectId": project_id, "book": "", "passageId": "",
            "kind": "NEEDS_PASSAGE_REVIEW", "direction": "SOURCE_COVERAGE",
            "sourceSemanticUnitIds": [], "targetSemanticUnitIds": [],
            "semanticRelationshipIds": [], "evidenceIds": [], "explanation": "",
            "confidence": {"rawScore": None, "calibratedValue": 0.0,
                           "confidencePolicyVersion": policy.confidence_policy_version,
                           "calibrationVersion": policy.calibration_version},
            "currentTargetRevision": "UNBOUND", "qaDisposition": "UNRESOLVED",
            "policyBinding": to_wire(policy), "reviewStatus": "UNREVIEWED",
            "lifecycleStatus": "ACTIVE", "revision": 1,
        }
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO qa_findings"
                "(id,project_id,qa_disposition,policy_binding_id,review_status,lifecycle_status,"
                "revision,payload_json,book,kind,direction,severity,severity_rank) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (finding_id, project_id, "UNRESOLVED", policy_id, "UNREVIEWED", "ACTIVE", 1,
                 json.dumps(payload), "", "NEEDS_PASSAGE_REVIEW", "SOURCE_COVERAGE", "", 99),
            )
            conn.commit()

    @staticmethod
    def _replace_qa_finding_scope_references(
        conn: sqlite3.Connection, finding_id: str,
        source_unit_ids: tuple[str, ...], target_unit_ids: tuple[str, ...],
    ) -> None:
        """Index a finding by the canonical scope of its semantic units.

        Source-coverage filtering deliberately follows source units, while
        target-support filtering follows target units.  This is what keeps a
        Greek PHP 1:3 unit visible when its Tamil realization is in 1:6,
        without making every target-side 1:6 finding appear in a 1:3 queue.
        """
        conn.execute(
            "DELETE FROM qa_finding_scope_references WHERE finding_id=?",
            (finding_id,),
        )
        rows: set[tuple[str, str, str]] = set()
        for side, unit_ids in (("SOURCE", source_unit_ids), ("TARGET", target_unit_ids)):
            for unit_id in unit_ids:
                row = conn.execute(
                    "SELECT payload_json FROM semantic_units WHERE id=?", (unit_id,),
                ).fetchone()
                if row is None:
                    continue
                payload = json.loads(row[0])
                for reference in payload.get("canonicalReferences") or ():
                    normalized = str(reference).strip()
                    if normalized:
                        rows.add((finding_id, side, normalized))
        if rows:
            conn.executemany(
                "INSERT INTO qa_finding_scope_references"
                "(finding_id,side,canonical_reference) VALUES(?,?,?)",
                sorted(rows),
            )

    def save_qa_finding(self, finding: QaFinding) -> None:
        policy_id = self._ensure_policy(finding.policy_binding)
        payload = to_wire(finding)
        with self._connect() as conn:
            for unit_id in (*finding.source_semantic_unit_ids, *finding.target_semantic_unit_ids):
                if conn.execute("SELECT 1 FROM semantic_units WHERE id=?", (unit_id,)).fetchone() is None:
                    raise FoundationValidationError(f"Unknown QA semantic unit: {unit_id}")
            for relationship_id in finding.semantic_relationship_ids:
                if conn.execute("SELECT 1 FROM semantic_relationships WHERE id=?", (relationship_id,)).fetchone() is None:
                    raise FoundationValidationError(f"Unknown QA semantic relationship: {relationship_id}")
            for evidence_id in finding.evidence_ids:
                if conn.execute("SELECT 1 FROM evidence_records WHERE id=?", (evidence_id,)).fetchone() is None:
                    raise FoundationValidationError(f"Unknown QA evidence record: {evidence_id}")
            chapter, verse, reference = _queue_sort_key(finding)
            rank = _SEVERITY_RANK.get(finding.severity.value, 99)
            existing = conn.execute(
                "SELECT * FROM qa_findings WHERE id=?", (finding.id,),
            ).fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO qa_findings"
                    "(id,project_id,qa_disposition,policy_binding_id,review_status,lifecycle_status,"
                    "revision,payload_json,book,kind,direction,severity,severity_rank,"
                    "sort_chapter,sort_verse,displayed_reference) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (finding.id, finding.project_id, finding.qa_disposition.value, policy_id,
                     finding.review_status.value, finding.lifecycle_status.value, finding.revision,
                     json.dumps(payload, ensure_ascii=False), finding.book, finding.kind.value,
                     finding.direction.value, finding.severity.value,
                     rank, chapter, verse, reference),
                )
                self._replace_qa_finding_scope_references(
                    conn, finding.id, finding.source_semantic_unit_ids,
                    finding.target_semantic_unit_ids,
                )
                conn.commit()
                return

            # Finding ids are stable across re-runs, so a re-run reaches an
            # existing row.  It refreshes the machine analysis and must never
            # overwrite the human decision recorded against it.
            stored = json.loads(existing["payload_json"])
            merged = dict(payload)
            for field in _HUMAN_FINDING_FIELDS:
                if field in stored:
                    merged[field] = stored[field]
            if _machine_fields(stored) == _machine_fields(merged):
                self._replace_qa_finding_scope_references(
                    conn, finding.id, finding.source_semantic_unit_ids,
                    finding.target_semantic_unit_ids,
                )
                conn.commit()
                return  # Nothing the machine produced actually changed.
            merged["revision"] = int(stored.get("revision", 1)) + 1
            conn.execute(
                "UPDATE qa_findings SET policy_binding_id=?,lifecycle_status=?,revision=?,"
                "payload_json=?,book=?,kind=?,direction=?,severity=?,severity_rank=?,"
                "sort_chapter=?,sort_verse=?,displayed_reference=? WHERE id=?",
                (policy_id, finding.lifecycle_status.value, merged["revision"],
                 json.dumps(merged, ensure_ascii=False), finding.book, finding.kind.value,
                 finding.direction.value, finding.severity.value, rank, chapter, verse,
                 reference, finding.id),
            )
            self._replace_qa_finding_scope_references(
                conn, finding.id, finding.source_semantic_unit_ids,
                finding.target_semantic_unit_ids,
            )
            if stored.get("qaDisposition") != QaDisposition.UNRESOLVED.value:
                # Leave an audit trail that a human-decided finding was
                # re-analysed, so the decision can be re-checked against it.
                self._append_review(
                    conn, "QA_FINDING", finding.id, stored.get("reviewStatus"),
                    str(merged.get("reviewStatus")), existing["lifecycle_status"],
                    finding.lifecycle_status.value, stored.get("qaDisposition"),
                    str(merged.get("qaDisposition")), ActorType.SYSTEM, "qa-audit",
                    int(stored.get("revision", 1)),
                    "Machine analysis refreshed; the existing human decision was preserved.",
                )
            conn.commit()

    def qa_finding(self, finding_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT payload_json FROM qa_findings WHERE id=?", (finding_id,)).fetchone()
        if row is None:
            raise FoundationValidationError(f"Unknown QA finding: {finding_id}")
        return json.loads(row[0])

    def record_human_review(
        self, entity_type: str, entity_id: str, *, review_status: ReviewStatus,
        expected_revision: int, actor_id: str = "human", note: str = "",
        lifecycle_status: LifecycleStatus | None = None,
        payload_updates: dict[str, Any] | None = None,
        invalidate_dependents: bool = False,
    ) -> dict[str, Any]:
        """Apply one human review decision to any reviewable entity.

        Optimistic concurrency only — a stale ``expected_revision`` is rejected
        rather than overwritten, because a human decision must never be
        last-write-wins.  ``invalidate_dependents`` marks everything downstream
        STALE (a rejected Stage 6B location invalidates the Stage 7 meaning and
        Stage 8 findings built on it) without rewriting their history.
        """
        table = _REVIEWABLE_TABLES.get(entity_type)
        if table is None:
            raise FoundationValidationError(f"Entity type is not reviewable: {entity_type}")
        review_status = ReviewStatus(review_status)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (entity_id,)).fetchone()
            if row is None:
                raise FoundationValidationError(f"Unknown {entity_type}: {entity_id}")
            if int(row["revision"]) != expected_revision:
                raise FoundationConflict(f"{entity_type} revision conflict")
            new_lifecycle = (
                lifecycle_status.value if lifecycle_status is not None
                else row["lifecycle_status"]
            )
            payload = json.loads(row["payload_json"])
            payload.update(payload_updates or {})
            payload.update({
                "reviewStatus": review_status.value, "lifecycleStatus": new_lifecycle,
                "revision": expected_revision + 1,
            })
            changed = conn.execute(
                f"UPDATE {table} SET review_status=?,lifecycle_status=?,revision=revision+1,"
                "payload_json=? WHERE id=? AND revision=?",
                (review_status.value, new_lifecycle,
                 json.dumps(payload, ensure_ascii=False), entity_id, expected_revision),
            ).rowcount
            if changed != 1:
                raise FoundationConflict(f"{entity_type} revision conflict")
            self._append_review(
                conn, entity_type, entity_id, row["review_status"], review_status.value,
                row["lifecycle_status"], new_lifecycle,
                (row["qa_disposition"] if "qa_disposition" in row.keys() else None),
                (row["qa_disposition"] if "qa_disposition" in row.keys() else None),
                ActorType.HUMAN, actor_id, expected_revision, note,
            )
            if invalidate_dependents:
                self._stale_generic_dependencies(conn, entity_type, entity_id)
            conn.commit()
        return payload

    def append_standalone_note(
        self, entity_type: str, entity_id: str, *, actor_id: str, note: str,
    ) -> None:
        """Record a reviewer note that changes no decision.

        The entity's own review/lifecycle state is deliberately unchanged and
        its revision is not bumped: commenting is not deciding.
        """
        table = _REVIEWABLE_TABLES.get(entity_type)
        if table is None:
            raise FoundationValidationError(f"Entity type is not reviewable: {entity_type}")
        with self._connect() as conn:
            row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (entity_id,)).fetchone()
            if row is None:
                raise FoundationValidationError(f"Unknown {entity_type}: {entity_id}")
            disposition = row["qa_disposition"] if "qa_disposition" in row.keys() else None
            self._append_review(
                conn, entity_type, entity_id, row["review_status"], row["review_status"],
                row["lifecycle_status"], row["lifecycle_status"], disposition, disposition,
                ActorType.HUMAN, actor_id, int(row["revision"]), note,
            )
            conn.commit()

    def query_qa_findings(
        self, project_id: str, *, book: str = "", chapter: int | None = None,
        canonical_references: tuple[str, ...] = (),
        kinds: tuple[str, ...] = (), severities: tuple[str, ...] = (),
        coverage_dimensions: tuple[str, ...] = (),
        dispositions: tuple[str, ...] = (), review_statuses: tuple[str, ...] = (),
        lifecycle_statuses: tuple[str, ...] = (), order: str = "CANONICAL",
        limit: int = 50, cursor: str = "",
    ) -> dict[str, Any]:
        """Page the Stage 9A review queue without parsing every stored payload.

        Ordering is fully deterministic: both orders end in the finding id, so
        equal-priority findings never swap places between pages.  Paging is
        keyset-based rather than OFFSET-based so that a human decision made
        mid-review cannot shift rows across a page boundary.
        """
        if order not in ("CANONICAL", "SEVERITY"):
            raise FoundationValidationError(f"Unknown review queue order: {order}")
        limit = max(1, min(int(limit), 500))
        key_columns = (
            ["book", "sort_chapter", "sort_verse", "id"] if order == "CANONICAL"
            else ["severity_rank", "book", "sort_chapter", "sort_verse", "id"]
        )

        where = ["project_id=?"]
        params: list[Any] = [project_id]
        for column, values in (
            ("kind", kinds), ("severity", severities), ("qa_disposition", dispositions),
            ("review_status", review_statuses), ("lifecycle_status", lifecycle_statuses),
        ):
            if values:
                where.append(f"{column} IN ({','.join('?' * len(values))})")
                params.extend(values)
        selected_dimensions = tuple(dict.fromkeys(
            CoverageDimension(str(dimension)).value for dimension in coverage_dimensions
        ))
        if selected_dimensions:
            placeholders = ",".join("?" * len(selected_dimensions))
            # A semantic category is intentionally independent from a QA
            # finding kind.  For example, an unlocated NEGATION unit is a
            # POSSIBLE_OMISSION whose coverage dimension is POLARITY; it is
            # not a NEGATION_PROBLEM unless a located realization contradicts
            # the source polarity.  Resolve dimensions through the persisted
            # semantic owners/components so existing v0.9.7 findings work
            # without a destructive migration or analysis rerun.
            where.append(
                "(EXISTS (SELECT 1 FROM json_each(COALESCE("
                "json_extract(qa_findings.payload_json,'$.sourceSemanticUnitIds'),'[]')) ids "
                "JOIN semantic_units units ON units.id=ids.value "
                f"WHERE units.coverage_dimension IN ({placeholders})) "
                "OR EXISTS (SELECT 1 FROM json_each(COALESCE("
                "json_extract(qa_findings.payload_json,'$.targetSemanticUnitIds'),'[]')) ids "
                "JOIN semantic_units units ON units.id=ids.value "
                f"WHERE units.coverage_dimension IN ({placeholders})) "
                "OR EXISTS (SELECT 1 FROM json_each(COALESCE("
                "json_extract(qa_findings.payload_json,'$.meaningAssessmentIds'),'[]')) ids "
                "JOIN meaning_component_assessments components "
                "ON components.assessment_id=ids.value "
                f"WHERE components.coverage_dimension IN ({placeholders})))"
            )
            params.extend(selected_dimensions)
            params.extend(selected_dimensions)
            params.extend(selected_dimensions)
        if book:
            where.append("book=?")
            params.append(book)
        if chapter is not None:
            where.append("sort_chapter=?")
            params.append(int(chapter))
        selected_references = tuple(dict.fromkeys(
            str(reference).strip() for reference in canonical_references
        ))
        if any(not reference for reference in selected_references):
            raise FoundationValidationError(
                "Canonical review-scope references must not be empty"
            )
        if selected_references:
            # One JSON parameter avoids SQLite's host-parameter ceiling for a
            # whole-book scope.  Direction chooses the semantic side whose
            # canonical ownership determines queue membership.
            where.append(
                "EXISTS (SELECT 1 FROM qa_finding_scope_references scope "
                "JOIN json_each(?) selected "
                "ON selected.value=scope.canonical_reference "
                "WHERE scope.finding_id=qa_findings.id AND scope.side="
                "CASE qa_findings.direction WHEN 'TARGET_SUPPORT' THEN 'TARGET' "
                "ELSE 'SOURCE' END)"
            )
            params.append(json.dumps(selected_references, ensure_ascii=False))

        filtered = " AND ".join(where)
        with self._connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM qa_findings WHERE {filtered}", params,
            ).fetchone()[0]
            paged = list(params)
            keyset = ""
            if cursor:
                key = self._decode_queue_cursor(cursor, len(key_columns))
                keyset = (
                    f" AND ({','.join(key_columns)}) > "
                    f"({','.join('?' * len(key_columns))})"
                )
                paged.extend(key)
            rows = conn.execute(
                f"SELECT {','.join(key_columns)},payload_json FROM qa_findings "
                f"WHERE {filtered}{keyset} ORDER BY {','.join(key_columns)} LIMIT ?",
                (*paged, limit + 1),
            ).fetchall()

        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = ""
        if has_more and rows:
            next_cursor = self._encode_queue_cursor(
                [rows[-1][column] for column in key_columns]
            )
        return {
            "findings": [json.loads(row["payload_json"]) for row in rows],
            "nextCursor": next_cursor, "totalCount": total, "order": order,
        }

    @staticmethod
    def _encode_queue_cursor(key: list[Any]) -> str:
        raw = json.dumps(key, ensure_ascii=False).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii")

    @staticmethod
    def _decode_queue_cursor(cursor: str, width: int) -> list[Any]:
        try:
            key = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
        except Exception as exc:
            raise FoundationValidationError("Malformed review queue cursor") from exc
        if not isinstance(key, list) or len(key) != width:
            raise FoundationValidationError("Review queue cursor does not match this ordering")
        return key

    def update_qa_disposition(self, finding_id: str, disposition: QaDisposition, expected_revision: int, reviewer: str, note: str = "", review_status: ReviewStatus | None = None) -> None:
        disposition = QaDisposition(disposition)
        # The caller may state the review status explicitly; Stage 9A does, so
        # that FALSE_POSITIVE records HUMAN_REJECTED rather than being folded
        # into HUMAN_APPROVED with every other decided disposition.
        review = ReviewStatus(review_status) if review_status is not None else (
            ReviewStatus.NEEDS_DISCUSSION if disposition == QaDisposition.NEEDS_DISCUSSION else (
                ReviewStatus.UNREVIEWED if disposition == QaDisposition.UNRESOLVED else ReviewStatus.HUMAN_APPROVED
            )
        )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM qa_findings WHERE id=?", (finding_id,)).fetchone()
            if row is None:
                raise FoundationValidationError(f"Unknown QA finding: {finding_id}")
            if int(row["revision"]) != expected_revision:
                raise FoundationConflict("QA finding revision conflict")
            payload = json.loads(row["payload_json"]); payload.update({"qaDisposition": disposition.value, "reviewStatus": review.value, "revision": expected_revision + 1})
            changed = conn.execute(
                "UPDATE qa_findings SET qa_disposition=?,review_status=?,revision=revision+1,payload_json=? WHERE id=? AND revision=?",
                (disposition.value, review.value, json.dumps(payload), finding_id, expected_revision),
            ).rowcount
            if changed != 1:
                raise FoundationConflict("QA finding revision conflict")
            self._append_review(conn, "QA_FINDING", finding_id, row["review_status"], review.value, row["lifecycle_status"], row["lifecycle_status"], row["qa_disposition"], disposition.value, ActorType.HUMAN, reviewer, expected_revision, note)
            conn.commit()

    # Columns are named explicitly: v11 widened this table, and a positional
    # INSERT would break on every schema addition after it.
    _CORRECTION_PROPOSAL_COLUMNS = (
        "id,project_id,qa_finding_id,current_target_revision,applied_target_revision,"
        "policy_binding_id,review_status,lifecycle_status,revision,payload_json,"
        "proposal_schema_version,verification_status,applicable"
    )

    def save_correction_proposal(self, proposal: CorrectionProposal) -> None:
        """Write a legacy v1 proposal. Never applicable -- it carries no exact span."""
        policy_id = self._ensure_policy(proposal.policy_binding); payload = to_wire(proposal)
        payload.setdefault("proposalSchemaVersion", 1)
        payload.setdefault("verificationStatus", VerificationStatus.NOT_RUN.value)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                f"INSERT INTO correction_proposals({self._CORRECTION_PROPOSAL_COLUMNS}) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (proposal.id, proposal.project_id, proposal.qa_finding_id, proposal.current_target_revision,
                 proposal.applied_target_revision, policy_id, proposal.review_status.value, proposal.lifecycle_status.value,
                 proposal.revision, json.dumps(payload, ensure_ascii=False),
                 1, VerificationStatus.NOT_RUN.value, 0),
            )
            self._append_correction_event(
                conn, proposal=payload, event_type="CREATED",
                actor_type=ActorType.MIGRATION, actor_id="legacy-proposal-writer",
                base_revision=0, reason="Legacy schema-v1 proposal retained as non-applicable history.",
            )
            conn.commit()

    def save_correction_proposal_v2(self, proposal: CorrectionProposalV2) -> None:
        """Write a canonical v2 proposal and register its dependency edges.

        The edges are the point: a v2 proposal depends on its finding, on the
        target references it would edit, and on the source semantic units and
        location/meaning evidence that justify it. Any of those going stale must
        take the proposal's eligibility with it.
        """
        policy_id = self._ensure_policy(proposal.policy_binding)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._insert_correction_proposal_v2(conn, proposal, policy_id)
            self._append_correction_event(
                conn, proposal=to_wire(proposal), event_type="CREATED",
                actor_type=ActorType.HUMAN, actor_id=proposal.created_by,
                base_revision=0, reason=proposal.explanation,
            )
            if proposal.provider_metadata is not None:
                self._append_correction_event(
                    conn, proposal=to_wire(proposal), event_type="SUGGESTED",
                    actor_type=ActorType.AI,
                    actor_id=(proposal.provider_metadata.model or proposal.provider_metadata.provider_name),
                    base_revision=0, reason=proposal.explanation,
                )
            conn.commit()

    def _insert_correction_proposal_v2(
        self, conn: sqlite3.Connection, proposal: CorrectionProposalV2, policy_id: str,
    ) -> None:
        payload = to_wire(proposal)
        span = proposal.intent.affected_target_span
        owner = conn.execute(
            "SELECT id FROM correction_proposals WHERE qa_finding_id=? "
            "AND lifecycle_status IN ('ACTIVE','INACTIVE') LIMIT 1",
            (proposal.qa_finding_id,),
        ).fetchone()
        if owner is not None:
            raise FoundationConflict(
                f"Correction proposal {owner['id']} already owns this finding"
            )
        conn.execute(
            f"INSERT INTO correction_proposals({self._CORRECTION_PROPOSAL_COLUMNS}) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (proposal.id, proposal.project_id, proposal.qa_finding_id,
             span.target_text_revision, proposal.applied_target_revision, policy_id,
             proposal.review_status.value, proposal.lifecycle_status.value,
             proposal.revision, json.dumps(payload, ensure_ascii=False),
             proposal.proposal_schema_version, proposal.verification_status.value,
             1 if proposal.is_applicable else 0),
        )
        edges: list[tuple[str, str, str, str]] = [
            ("CORRECTION_PROPOSAL", proposal.id, "QA_FINDING", proposal.qa_finding_id),
        ]
        for reference in dict.fromkeys(
            (*span.canonical_references, *proposal.affected_references)
        ):
            edges.append((
                "CORRECTION_PROPOSAL", proposal.id, "TARGET_REFERENCE",
                self.target_dependency_id(proposal.project_id, self._book_of(reference), reference),
            ))
        for unit_id in proposal.intent.affected_source_semantic_unit_ids:
            edges.append(("CORRECTION_PROPOSAL", proposal.id, "SEMANTIC_UNIT", unit_id))
        for assessment_id in proposal.meaning_assessment_ids:
            edges.append(("CORRECTION_PROPOSAL", proposal.id, "MEANING_ASSESSMENT", assessment_id))
        for relationship_id in (*proposal.semantic_relationship_ids, *proposal.location_relationship_ids):
            edge_type = (
                "LOCATION_RELATIONSHIP" if relationship_id in proposal.location_relationship_ids
                else "SEMANTIC_RELATIONSHIP"
            )
            edges.append(("CORRECTION_PROPOSAL", proposal.id, edge_type, relationship_id))
        for evidence_id in proposal.evidence_ids:
            # Stage 7 component evidence can be inline in a meaning assessment
            # rather than an evidence_records row. It remains cited in the
            # proposal payload, while the assessment dependency above owns its
            # invalidation. Register a generic edge only for durable records;
            # otherwise recovery would correctly flag a dangling dependency.
            if conn.execute(
                "SELECT 1 FROM evidence_records WHERE id=?", (evidence_id,),
            ).fetchone() is not None:
                edges.append(("CORRECTION_PROPOSAL", proposal.id, "EVIDENCE_RECORD", evidence_id))
        conn.executemany("INSERT OR IGNORE INTO record_dependencies VALUES(?,?,?,?)", edges)

    def _append_correction_event(
        self, conn: sqlite3.Connection, *, proposal: dict[str, Any], event_type: str,
        actor_type: ActorType, actor_id: str, base_revision: int, reason: str = "",
    ) -> None:
        conn.execute(
            "INSERT INTO correction_proposal_events("
            "id,proposal_id,event_type,actor_type,actor_id,base_revision,new_revision,"
            "reason,provider_metadata_json,proposal_snapshot_json,created_at,seq) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,"
            "(SELECT IFNULL(MAX(seq),0)+1 FROM correction_proposal_events))",
            (str(uuid.uuid4()), str(proposal["id"]), event_type, actor_type.value,
             actor_id, base_revision, int(proposal["revision"]), reason,
             json.dumps(proposal.get("providerMetadata") or {}, ensure_ascii=False),
             json.dumps(proposal, ensure_ascii=False), self._now()),
        )

    def update_correction_proposal_wording(
        self, proposal_id: str, *, proposed_text: str, explanation: str,
        creation_mode: str, review_status: ReviewStatus, expected_revision: int,
        actor_id: str,
    ) -> dict[str, Any]:
        """CAS-update proposal wording while retaining an append-only snapshot."""
        if expected_revision < 1:
            raise FoundationValidationError("expected_revision must be at least 1")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM correction_proposals WHERE id=?", (proposal_id,),
            ).fetchone()
            if row is None:
                raise FoundationValidationError(f"Unknown correction proposal: {proposal_id}")
            if int(row["revision"]) != expected_revision:
                raise FoundationConflict("Correction proposal revision conflict")
            payload = json.loads(row["payload_json"])
            payload.update({
                "proposedText": proposed_text,
                "explanation": explanation,
                "creationMode": creation_mode,
                "reviewStatus": review_status.value,
                "revision": expected_revision + 1,
            })
            changed = conn.execute(
                "UPDATE correction_proposals SET review_status=?,revision=revision+1,payload_json=? "
                "WHERE id=? AND revision=?",
                (review_status.value, json.dumps(payload, ensure_ascii=False),
                 proposal_id, expected_revision),
            ).rowcount
            if changed != 1:
                raise FoundationConflict("Correction proposal revision conflict")
            self._append_correction_event(
                conn, proposal=payload, event_type="EDITED", actor_type=ActorType.HUMAN,
                actor_id=actor_id, base_revision=expected_revision, reason=explanation,
            )
            conn.commit()
        return self.correction_proposal(proposal_id)

    def reject_correction_proposal(
        self, proposal_id: str, *, expected_revision: int, actor_id: str, reason: str,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM correction_proposals WHERE id=?", (proposal_id,),
            ).fetchone()
            if row is None:
                raise FoundationValidationError(f"Unknown correction proposal: {proposal_id}")
            if int(row["revision"]) != expected_revision:
                raise FoundationConflict("Correction proposal revision conflict")
            payload = json.loads(row["payload_json"])
            payload.update({
                "reviewStatus": ReviewStatus.HUMAN_REJECTED.value,
                "lifecycleStatus": LifecycleStatus.INACTIVE.value,
                "revision": expected_revision + 1,
            })
            changed = conn.execute(
                "UPDATE correction_proposals SET review_status=?,lifecycle_status=?,"
                "revision=revision+1,payload_json=? WHERE id=? AND revision=?",
                (ReviewStatus.HUMAN_REJECTED.value, LifecycleStatus.INACTIVE.value,
                 json.dumps(payload, ensure_ascii=False), proposal_id, expected_revision),
            ).rowcount
            if changed != 1:
                raise FoundationConflict("Correction proposal revision conflict")
            self._append_correction_event(
                conn, proposal=payload, event_type="REJECTED", actor_type=ActorType.HUMAN,
                actor_id=actor_id, base_revision=expected_revision, reason=reason,
            )
            conn.commit()
        return self.correction_proposal(proposal_id)

    def supersede_and_save_correction_proposal(
        self, old_proposal_id: str, *, expected_revision: int,
        replacement: CorrectionProposalV2, actor_id: str,
    ) -> dict[str, Any]:
        """Atomically preserve the old proposal and insert its replacement."""
        policy_id = self._ensure_policy(replacement.policy_binding)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM correction_proposals WHERE id=?", (old_proposal_id,),
            ).fetchone()
            if row is None:
                raise FoundationValidationError(f"Unknown correction proposal: {old_proposal_id}")
            if int(row["revision"]) != expected_revision:
                raise FoundationConflict("Correction proposal revision conflict")
            if replacement.supersedes_proposal_id != old_proposal_id:
                raise FoundationValidationError("Replacement must identify the proposal it supersedes")
            old = json.loads(row["payload_json"])
            old.update({
                "lifecycleStatus": LifecycleStatus.SUPERSEDED.value,
                "revision": expected_revision + 1,
            })
            changed = conn.execute(
                "UPDATE correction_proposals SET lifecycle_status=?,revision=revision+1,payload_json=? "
                "WHERE id=? AND revision=?",
                (LifecycleStatus.SUPERSEDED.value, json.dumps(old, ensure_ascii=False),
                 old_proposal_id, expected_revision),
            ).rowcount
            if changed != 1:
                raise FoundationConflict("Correction proposal revision conflict")
            self._append_correction_event(
                conn, proposal=old, event_type="SUPERSEDED", actor_type=ActorType.HUMAN,
                actor_id=actor_id, base_revision=expected_revision,
                reason=f"Superseded by {replacement.id}",
            )
            self._insert_correction_proposal_v2(conn, replacement, policy_id)
            self._append_correction_event(
                conn, proposal=to_wire(replacement), event_type="CREATED",
                actor_type=ActorType.HUMAN, actor_id=actor_id, base_revision=0,
                reason=replacement.explanation,
            )
            if replacement.provider_metadata is not None:
                self._append_correction_event(
                    conn, proposal=to_wire(replacement), event_type="SUGGESTED",
                    actor_type=ActorType.AI,
                    actor_id=(replacement.provider_metadata.model
                              or replacement.provider_metadata.provider_name),
                    base_revision=0, reason=replacement.explanation,
                )
            conn.commit()
        return self.correction_proposal(replacement.id)

    def correction_proposal_history(self, proposal_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM correction_proposal_events WHERE proposal_id=? "
                "ORDER BY seq", (proposal_id,),
            ).fetchall()
        return [{
            "id": row["id"], "proposalId": row["proposal_id"],
            "eventType": row["event_type"], "actorType": row["actor_type"],
            "actorId": row["actor_id"], "baseRevision": row["base_revision"],
            "newRevision": row["new_revision"], "reason": row["reason"],
            "providerMetadata": json.loads(row["provider_metadata_json"]),
            "proposalSnapshot": json.loads(row["proposal_snapshot_json"]),
            "createdAt": row["created_at"],
        } for row in rows]

    @staticmethod
    def _book_of(displayed_reference: str) -> str:
        return str(displayed_reference).split(" ", 1)[0].upper()

    def correction_proposal(self, proposal_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json,proposal_schema_version,verification_status,applicable "
                "FROM correction_proposals WHERE id=?", (proposal_id,),
            ).fetchone()
        if row is None:
            raise FoundationValidationError(f"Unknown correction proposal: {proposal_id}")
        payload = json.loads(row["payload_json"])
        payload["proposalSchemaVersion"] = int(row["proposal_schema_version"])
        payload["verificationStatus"] = row["verification_status"]
        payload["applicable"] = bool(row["applicable"])
        return self._reseed_review_status_if_needed(payload)

    def correction_proposals_for_finding(self, finding_id: str) -> list[dict[str, Any]]:
        """Every proposal against one finding, newest revision last.

        Includes legacy v1 rows: they are history a reviewer may need to see,
        and they still count as a conflicting owner of the finding even though
        they can never be applied.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload_json,proposal_schema_version,verification_status,applicable "
                "FROM correction_proposals WHERE qa_finding_id=? ORDER BY revision,id",
                (finding_id,),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            payload["proposalSchemaVersion"] = int(row["proposal_schema_version"])
            payload["verificationStatus"] = row["verification_status"]
            payload["applicable"] = bool(row["applicable"])
            out.append(self._reseed_review_status_if_needed(payload))
        return out

    @staticmethod
    def _looks_reseedable(payload: dict[str, Any]) -> bool:
        """Cheap in-memory pre-filter, checked before ever opening a write
        connection -- both proposal read paths call this on every row, so the
        common case (already reviewed, one way or another) must stay a plain
        dict-get away from a no-op.

        The `proposedText.strip()` check matters: a HUMAN_AUTHORED,
        UNREVIEWED proposal with no real wording (only whitespace) is not a
        pre-fix leftover -- it is W1's own new default correctly leaving it
        UNREVIEWED ("nobody has written anything to review"), and it must
        stay that way on every future read too, not just at creation.
        Without this, the very read that returns a freshly created
        whitespace-only proposal would immediately reseed it, since nothing
        else in its shape distinguishes "genuinely nothing to review" from
        "real wording written before this fix landed."
        """
        return (
            str(payload.get("reviewStatus") or "") == ReviewStatus.UNREVIEWED.value
            and str(payload.get("creationMode") or "") == CorrectionCreationMode.HUMAN_AUTHORED.value
            and str(payload.get("lifecycleStatus") or "") == LifecycleStatus.ACTIVE.value
            and not payload.get("providerMetadata")
            and not payload.get("originalSuggestedText")
            and str(payload.get("proposedText") or "").strip()
        )

    def _reseed_review_status_if_needed(self, payload: dict[str, Any]) -> dict[str, Any]:
        """V11-003 (#57): a manually authored proposal created before this fix
        landed is stuck UNREVIEWED, so "Review application" never appears even
        though this is already self-review by the proposal's own author --
        see docs/archive/V11-003_ISSUE57_PROMPT.md. Lazily reseed it to HUMAN_APPROVED
        the first time it is read through either `correction_proposal` or
        `correction_proposals_for_finding` -- both call this one choke point,
        so a proposal can never be reachable reseeded through one and
        unreseeded through the other.

        review_status / payload["reviewStatus"] are updated in place --
        deliberately NOT a CAS-guarded revision bump. This is a data-repair
        backfill, not a human edit, and it must stay invisible to the
        review panel's optimistic-concurrency revision check: the panel
        caches `revision` from whatever it last read and sends it back on
        edit/reject/apply, so a reseed that bumped it would turn a silent
        bug into a confusing REVISION_CONFLICT on the user's very next
        action (see Part A's "concurrency hazard" section).
        """
        if not self._looks_reseedable(payload):
            return payload
        # Never write on a read while the database is read-only/in recovery
        # (recovery_check() sets this) -- a reseed that raised here would
        # break project open, and the row simply stays unreseeded until the
        # database is healthy again.
        if self.read_only:
            return payload
        proposal_id = str(payload["id"])
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT revision,payload_json FROM correction_proposals WHERE id=?",
                (proposal_id,),
            ).fetchone()
            if row is None:
                conn.rollback()
                return payload
            current = json.loads(row["payload_json"])
            if not self._looks_reseedable(current):
                # Re-checked against a fresh read inside the write transaction:
                # something changed this proposal between the outer read and
                # here (a reject, an edit, ...). Idempotent by construction --
                # no write, no event, whatever is current wins.
                conn.rollback()
                return payload
            revision = int(row["revision"])
            current["reviewStatus"] = ReviewStatus.HUMAN_APPROVED.value
            conn.execute(
                "UPDATE correction_proposals SET review_status=?,payload_json=? WHERE id=?",
                (ReviewStatus.HUMAN_APPROVED.value, json.dumps(current, ensure_ascii=False), proposal_id),
            )
            self._append_correction_event(
                conn, proposal=current, event_type="REVIEW_STATUS_BACKFILLED",
                actor_type=ActorType.MIGRATION, actor_id="review-status-backfill-v11-003",
                base_revision=revision,
                reason="Human-authored proposal predates the V11-003 default; "
                       "reseeded to HUMAN_APPROVED on read (#57).",
            )
            conn.commit()
        payload = dict(payload)
        payload["reviewStatus"] = ReviewStatus.HUMAN_APPROVED.value
        return payload

    def record_correction_application_metadata(
        self, proposal_id: str, *, actor_type: str | ActorType, applied_target_revision: str,
        expected_revision: int, actor_id: str = "human",
    ) -> None:
        """Record that a proposal's text was written. Bookkeeping only.

        This used to be the whole "correction workflow": it flipped the finding
        straight to CORRECTED. That conflated two different events and made the
        strongest claim in the system -- "this translation error is fixed" -- a
        side effect of writing bytes, with no evidence that the edit achieved
        anything. Stage 9B's invariant is that **applied is not corrected**.

        So application now: stamps applied_* on the proposal, moves it to
        verification PENDING, and marks the finding STALE -- historical, pending
        re-analysis -- while deliberately leaving its disposition
        CONFIRMED_TRANSLATION_ERROR. Reaching CORRECTED additionally requires
        verificationStatus == PASSED *and* an explicit human acknowledgement,
        neither of which exists before Stage 9B.4.

        It also does not write Scripture and never did; the caller is
        responsible for having gone through the canonical edit path first.
        """
        actor = ActorType(str(actor_type))
        if actor != ActorType.HUMAN:
            raise FoundationValidationError("Correction application requires explicit human action")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM correction_proposals WHERE id=?", (proposal_id,)).fetchone()
            if row is None:
                raise FoundationValidationError(f"Unknown correction proposal: {proposal_id}")
            if int(row["revision"]) != expected_revision:
                raise FoundationConflict("Correction proposal revision conflict")
            payload = json.loads(row["payload_json"])
            payload.update({
                "appliedTargetRevision": applied_target_revision, "appliedBy": actor_id,
                "appliedAt": self._now(), "reviewStatus": "HUMAN_APPROVED",
                "lifecycleStatus": "ACTIVE", "revision": expected_revision + 1,
                "verificationStatus": VerificationStatus.PENDING.value,
            })
            conn.execute(
                "UPDATE correction_proposals SET applied_target_revision=?,review_status='HUMAN_APPROVED',"
                "lifecycle_status='ACTIVE',revision=revision+1,verification_status=?,payload_json=? "
                "WHERE id=? AND revision=?",
                (applied_target_revision, VerificationStatus.PENDING.value,
                 json.dumps(payload, ensure_ascii=False), proposal_id, expected_revision),
            )
            finding = conn.execute("SELECT * FROM qa_findings WHERE id=?", (row["qa_finding_id"],)).fetchone()
            if finding is not None:
                # STALE, not CORRECTED: the text under this finding changed, so
                # the finding no longer describes current Scripture and must be
                # re-analysed. Its disposition is left exactly as the human set it.
                q = json.loads(finding["payload_json"])
                q.update({"lifecycleStatus": "STALE", "revision": int(finding["revision"]) + 1})
                conn.execute(
                    "UPDATE qa_findings SET lifecycle_status='STALE',revision=revision+1,payload_json=? WHERE id=?",
                    (json.dumps(q, ensure_ascii=False), finding["id"]),
                )
            self._stale_generic_dependencies(conn, "CORRECTION_PROPOSAL", proposal_id)
            self._append_review(
                conn, "CORRECTION_PROPOSAL", proposal_id, row["review_status"], "HUMAN_APPROVED",
                row["lifecycle_status"], "ACTIVE", None, None, actor, actor_id, expected_revision,
                note="Correction text applied; verification pending. Not a CORRECTED disposition.",
            )
            conn.commit()

    def record_correction_applied(self, proposal_id: str, *, actor_type: str | ActorType, applied_target_revision: str, expected_revision: int, actor_id: str = "human") -> None:
        """Deprecated Stage 9A name. Retained so existing callers keep working.

        Kept as a thin alias rather than deleted: the old name reads like the
        complete correction workflow, which is exactly the misreading Stage 9B.0
        set out to remove, so it forwards to the metadata-only recorder above
        and can no longer produce a CORRECTED disposition.
        """
        self.record_correction_application_metadata(
            proposal_id, actor_type=actor_type,
            applied_target_revision=applied_target_revision,
            expected_revision=expected_revision, actor_id=actor_id,
        )

    def add_record_dependency(self, record_type: str, record_id: str, depends_on_type: str, depends_on_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO record_dependencies VALUES(?,?,?,?)",
                (record_type, record_id, depends_on_type, depends_on_id),
            )
            conn.commit()

    def quarantine_migration_record(
        self, *, source_kind: str, source_identity: str, reason_code: str,
        payload: dict[str, Any],
    ) -> str:
        return self.quarantine_migration_records_bulk([{
            "sourceKind": source_kind, "sourceIdentity": source_identity,
            "reasonCode": reason_code, "payload": payload,
        }])[0]

    def quarantine_migration_records_bulk(self, records: list[dict[str, Any]]) -> list[str]:
        """Quarantine many records in one transaction; returns their ids in order.

        Each record is ``{sourceKind, sourceIdentity, reasonCode, payload}``.
        One commit for the whole batch, and one timestamp: with
        ``synchronous = FULL`` every commit is an fsync, and the alignment
        compatibility scan (``synchronize_alignment_state``) quarantines one
        record per unaligned source word -- a raw import writes every word
        as its own empty group, and Genesis has 20,612 of them. Committed one
        at a time that measured 5m11s on a laptop SSD and blew the 300s
        ``project.import`` timeout; a first ``project.open`` has only 30s.
        """
        now = self._now()
        ids: list[str] = []
        rows: list[tuple[str, str, str, str, str, str]] = []
        for record in records:
            quarantine_id = str(uuid.uuid4())
            ids.append(quarantine_id)
            rows.append((
                quarantine_id, str(record["sourceKind"]), str(record["sourceIdentity"]),
                str(record["reasonCode"]), json.dumps(record["payload"], ensure_ascii=False), now,
            ))
        if not rows:
            return ids
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.executemany("INSERT INTO migration_quarantine VALUES(?,?,?,?,?,?)", rows)
            conn.commit()
        return ids

    def migration_quarantine_records(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM migration_quarantine ORDER BY created_at,id"
            ).fetchall()
        return [
            {
                "id": row["id"], "sourceKind": row["source_kind"],
                "sourceIdentity": row["source_identity"], "reasonCode": row["reason_code"],
                "payload": json.loads(row["payload_json"]), "createdAt": row["created_at"],
            }
            for row in rows
        ]

    def synchronize_source_lock(
        self, *, project_id: str, book: str, resource_id: str,
        resource_version: str, resource_hash: str,
    ) -> dict[str, Any]:
        now = self._now()
        changed = False
        staled = 0
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM source_resource_locks WHERE project_id=? AND book=?",
                (project_id, book),
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO source_resource_locks VALUES(?,?,?,?,?,?,?,?)",
                    (project_id, book, resource_id, resource_version, resource_hash,
                     "ACTIVE", 1, now),
                )
            elif (
                row["resource_id"] != resource_id
                or row["resource_version"] != resource_version
                or row["resource_hash"] != resource_hash
            ):
                changed = True
                dependency_id = self.source_dependency_id(project_id, book, row["resource_hash"])
                staled = self._stale_generic_dependencies(conn, "SOURCE_RESOURCE", dependency_id)
                active_inventories = conn.execute(
                    "SELECT id FROM source_inventory_runs WHERE project_id=? AND book=? "
                    "AND lifecycle_status='ACTIVE'",
                    (project_id, book),
                ).fetchall()
                for inventory in active_inventories:
                    staled += self._stale_generic_dependencies(
                        conn, "SOURCE_INVENTORY", inventory["id"],
                    )
                inventory_staled = conn.execute(
                    "UPDATE source_inventory_runs SET lifecycle_status='STALE' "
                    "WHERE project_id=? AND book=? AND lifecycle_status='ACTIVE'",
                    (project_id, book),
                ).rowcount
                staled += int(inventory_staled)
                conn.execute(
                    "UPDATE source_resource_locks SET resource_id=?,resource_version=?,resource_hash=?,"
                    "lifecycle_status='ACTIVE',revision=revision+1,updated_at=? "
                    "WHERE project_id=? AND book=?",
                    (resource_id, resource_version, resource_hash, now, project_id, book),
                )
            conn.commit()
        return {
            "changed": changed, "staled": staled, "resourceId": resource_id,
            "resourceVersion": resource_version, "resourceHash": resource_hash,
        }

    def source_lock(self, project_id: str, book: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM source_resource_locks WHERE project_id=? AND book=?",
                (project_id, book),
            ).fetchone()
        return dict(row) if row is not None else None

    def migration_run_for(
        self, project_id: str, source_path: str, source_hash: str,
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM migration_runs WHERE project_id=? AND source_path=? AND source_hash=?",
                (project_id, source_path, source_hash),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["report"] = json.loads(result.pop("report_json"))
        return result

    def save_migration_run(
        self, *, run_id: str, project_id: str, source_path: str, source_hash: str,
        source_schema: str, status: str, started_at: str, report: dict[str, Any],
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO migration_runs VALUES(?,?,?,?,?,?,?,?,?)",
                (run_id, project_id, source_path, source_hash, source_schema, status,
                 started_at, self._now(), json.dumps(report, ensure_ascii=False)),
            )
            conn.commit()

    def save_migration_runs(self, project_id: str, runs: list[dict[str, Any]]) -> None:
        """Several runs in one transaction (one fsync): the alignment
        compatibility scan's per-file runs and its folder run together."""
        now = self._now()
        rows = [(run["run_id"], project_id, run["source_path"], run["source_hash"], run["source_schema"],
                 run["status"], run["started_at"], now, json.dumps(run["report"], ensure_ascii=False))
                for run in runs]
        if not rows:
            return
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.executemany("INSERT INTO migration_runs VALUES(?,?,?,?,?,?,?,?,?)", rows)
            conn.commit()

    def migration_run_hashes(self, project_id: str, source_schema: str) -> dict[str, set[str]]:
        """source_path -> every source_hash recorded under one schema, in one
        query (the compatibility scan asks this for every chapter file)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT source_path,source_hash FROM migration_runs WHERE project_id=? AND source_schema=?",
                (project_id, source_schema),
            ).fetchall()
        out: dict[str, set[str]] = {}
        for row in rows:
            out.setdefault(row["source_path"], set()).add(row["source_hash"])
        return out

    def migration_report(self, project_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            runs = conn.execute(
                "SELECT * FROM migration_runs WHERE project_id=? ORDER BY started_at,id",
                (project_id,),
            ).fetchall()
            quarantine = conn.execute(
                "SELECT reason_code,COUNT(*) AS count FROM migration_quarantine "
                "GROUP BY reason_code ORDER BY reason_code"
            ).fetchall()
        return {
            "runs": [{
                "id": row["id"], "sourcePath": row["source_path"],
                "sourceHash": row["source_hash"], "sourceSchema": row["source_schema"],
                "status": row["status"], "startedAt": row["started_at"],
                "completedAt": row["completed_at"], "report": json.loads(row["report_json"]),
            } for row in runs],
            "quarantineByReason": {row["reason_code"]: row["count"] for row in quarantine},
        }

    def record_runtime_diagnostic(
        self, *, project_id: str, code: str, severity: str, payload: dict[str, Any],
    ) -> str:
        diagnostic_id = str(uuid.uuid4())
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO runtime_diagnostics VALUES(?,?,?,?,?,?,NULL)",
                (diagnostic_id, project_id, code, severity,
                 json.dumps(payload, ensure_ascii=False), self._now()),
            )
            conn.commit()
        return diagnostic_id

    def stale_summary(self, project_id: str) -> dict[str, Any]:
        tables = {
            "passages": "passage_records", "tokens": "token_lineages",
            "semanticUnits": "semantic_units", "semanticRelationships": "semantic_relationships",
            "coverageAccounts": "coverage_accounts", "qaFindings": "qa_findings",
            "lexicalSolutions": "lexical_solutions", "correctionProposals": "correction_proposals",
            "evidence": "evidence_records", "exportability": "exportability_records",
            "semanticLocationRuns": "semantic_location_runs",
            "meaningAnalysisRuns": "meaning_analysis_runs",
            "qaAuditRuns": "qa_audit_runs",
        }
        counts: dict[str, int] = {}
        with self._connect() as conn:
            for label, table in tables.items():
                if table == "token_lineages":
                    counts[label] = int(conn.execute(
                        "SELECT COUNT(*) FROM token_lineages WHERE project_id=? AND lifecycle_status='STALE'",
                        (project_id,),
                    ).fetchone()[0])
                else:
                    counts[label] = int(conn.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE project_id=? AND lifecycle_status='STALE'",
                        (project_id,),
                    ).fetchone()[0]) if table not in {"exportability_records"} else int(conn.execute(
                        "SELECT COUNT(*) FROM exportability_records e JOIN semantic_relationships r "
                        "ON r.id=e.relationship_id WHERE r.project_id=? AND e.lifecycle_status='STALE'",
                        (project_id,),
                    ).fetchone()[0])
            pending = int(conn.execute(
                "SELECT COUNT(*) FROM pending_invalidations WHERE project_id=? AND state='PREPARED'",
                (project_id,),
            ).fetchone()[0])
            quarantined = int(conn.execute(
                "SELECT COUNT(*) FROM migration_quarantine"
            ).fetchone()[0])
        return {"counts": counts, "pendingInvalidations": pending, "quarantined": quarantined}

    @staticmethod
    def _stale_generic_dependencies(
        conn: sqlite3.Connection, dependency_type: str, dependency_id: str,
    ) -> int:
        tables = RECORD_DEPENDENCY_TABLES
        queue: list[tuple[str, str]] = [(dependency_type, dependency_id)]
        visited: set[tuple[str, str]] = set()
        changed = 0
        while queue:
            current_type, current_id = queue.pop(0)
            if (current_type, current_id) in visited:
                continue
            visited.add((current_type, current_id))
            rows = conn.execute(
                "SELECT record_type,record_id FROM record_dependencies "
                "WHERE depends_on_type=? AND depends_on_id=?",
                (current_type, current_id),
            ).fetchall()
            for row in rows:
                record_type, record_id = row["record_type"], row["record_id"]
                table = tables.get(record_type)
                if not table:
                    continue
                record = conn.execute(
                    f"SELECT payload_json FROM {table} WHERE id=?", (record_id,),
                ).fetchone()
                if record is None:
                    continue
                payload = json.loads(record[0])
                payload["lifecycleStatus"] = "STALE"
                # Not every record table carries a revision column
                # (source_inventory_runs does not). Bumping it unconditionally
                # would raise mid-propagation and abort the whole invalidation,
                # leaving downstream records falsely current -- strictly worse
                # than a missing revision bump. Propagate STALE either way.
                if "revision" in _table_columns(conn, table):
                    payload["revision"] = int(payload.get("revision", 1)) + 1
                    statement = (
                        f"UPDATE {table} SET lifecycle_status='STALE',revision=revision+1,"
                        "payload_json=? WHERE id=? AND lifecycle_status<>'STALE'"
                    )
                else:
                    statement = (
                        f"UPDATE {table} SET lifecycle_status='STALE',payload_json=? "
                        "WHERE id=? AND lifecycle_status<>'STALE'"
                    )
                updated = conn.execute(
                    statement, (json.dumps(payload, ensure_ascii=False), record_id),
                ).rowcount
                changed += updated
                if updated and record_type == "CORRECTION_PROPOSAL":
                    conn.execute(
                        "INSERT INTO correction_proposal_events("
                        "id,proposal_id,event_type,actor_type,actor_id,base_revision,"
                        "new_revision,reason,provider_metadata_json,proposal_snapshot_json,"
                        "created_at,seq) VALUES(?,?,?,?,?,?,?,?,?,?,?,"
                        "(SELECT IFNULL(MAX(seq),0)+1 FROM correction_proposal_events))",
                        (str(uuid.uuid4()), record_id, "STALE", ActorType.SYSTEM.value,
                         "dependency-invalidation",
                         max(0, int(payload.get("revision", 1)) - 1),
                         int(payload.get("revision", 1)),
                         f"Dependency {current_type}:{current_id} became stale.", "{}",
                         json.dumps(payload, ensure_ascii=False),
                         datetime.now(timezone.utc).isoformat()),
                    )
                # Dependency propagation is independent of whether this record
                # was already stale. A downstream record must not remain current.
                queue.append((record_type, record_id))
        return changed

    def _append_review(self, conn: sqlite3.Connection, entity_type: str, entity_id: str,
                       previous_review: str | None, new_review: str, previous_lifecycle: str | None,
                       new_lifecycle: str, previous_disposition: str | None, new_disposition: str | None,
                       actor_type: ActorType, actor_id: str, base_revision: int,
                       note: str = "") -> None:
        conn.execute(
            "INSERT INTO review_records("
            "id,entity_type,entity_id,previous_review_status,new_review_status,"
            "previous_lifecycle_status,new_lifecycle_status,previous_qa_disposition,"
            "new_qa_disposition,actor_type,actor_id,note,base_revision,created_at,seq) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,"
            "(SELECT IFNULL(MAX(seq),0)+1 FROM review_records))",
            (str(uuid.uuid4()), entity_type, entity_id, previous_review, new_review, previous_lifecycle,
             new_lifecycle, previous_disposition, new_disposition, actor_type.value, actor_id, note, base_revision, self._now()),
        )

    def review_records(self, entity_type: str, entity_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM review_records WHERE entity_type=? AND entity_id=? ORDER BY seq",
                (entity_type, entity_id),
            ).fetchall()
        return [
            {
                "id": row["id"], "entityType": row["entity_type"], "entityId": row["entity_id"],
                "previousReviewStatus": row["previous_review_status"],
                "newReviewStatus": row["new_review_status"],
                "previousLifecycleStatus": row["previous_lifecycle_status"],
                "newLifecycleStatus": row["new_lifecycle_status"],
                "previousQaDisposition": row["previous_qa_disposition"],
                "newQaDisposition": row["new_qa_disposition"], "actorType": row["actor_type"],
                "actorId": row["actor_id"], "note": row["note"],
                "baseRevision": row["base_revision"], "createdAt": row["created_at"],
            }
            for row in rows
        ]

    def import_review_record(
        self, *, record_id: str, entity_type: str, entity_id: str,
        review_status: ReviewStatus, lifecycle_status: LifecycleStatus,
        actor_type: ActorType, actor_id: str, note: str, created_at: str,
        qa_disposition: QaDisposition | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO review_records("
                "id,entity_type,entity_id,previous_review_status,new_review_status,"
                "previous_lifecycle_status,new_lifecycle_status,previous_qa_disposition,"
                "new_qa_disposition,actor_type,actor_id,note,base_revision,created_at,seq) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,"
                "(SELECT IFNULL(MAX(seq),0)+1 FROM review_records))",
                (record_id, entity_type, entity_id, None, review_status.value, None,
                 lifecycle_status.value, None,
                 qa_disposition.value if qa_disposition is not None else None,
                 actor_type.value, actor_id, note, 1, created_at),
            )
            conn.commit()

    def recovery_check(self) -> dict[str, Any]:
        problems: list[str] = []
        with self._connect() as conn:
            integrity = [r[0] for r in conn.execute("PRAGMA integrity_check").fetchall()]
            if integrity != ["ok"]:
                problems.extend(integrity)
            foreign = conn.execute("PRAGMA foreign_key_check").fetchall()
            problems.extend(f"foreign-key:{tuple(row)}" for row in foreign)
            dangling = conn.execute(
                "SELECT m.token_instance_id FROM active_lexical_membership m "
                "JOIN lexical_solutions s ON s.id=m.lexical_solution_id "
                "JOIN lexical_groups g ON g.id=m.lexical_group_id "
                "WHERE s.lifecycle_status<>'ACTIVE' OR g.lifecycle_status<>'ACTIVE'"
            ).fetchall()
            problems.extend(f"inactive-membership:{row[0]}" for row in dangling)
            competing = conn.execute(
                "SELECT project_id,scope_key,profile_id,IFNULL(source_layer,'<NULL>'),"
                "IFNULL(target_layer,'<NULL>'),COUNT(*) FROM lexical_solutions "
                "WHERE authoritative=1 AND lifecycle_status='ACTIVE' "
                "GROUP BY project_id,scope_key,profile_id,IFNULL(source_layer,'<NULL>'),"
                "IFNULL(target_layer,'<NULL>') HAVING COUNT(*)>1"
            ).fetchall()
            problems.extend(f"competing-authoritative-solution:{tuple(row)}" for row in competing)
            known_record_tables = RECORD_DEPENDENCY_TABLES
            dependencies = conn.execute(
                "SELECT record_type,record_id,depends_on_type,depends_on_id FROM record_dependencies"
            ).fetchall()
            for dependency in dependencies:
                table = known_record_tables.get(dependency["record_type"])
                if table is None:
                    problems.append(
                        f"unknown-record-dependency-type:{dependency['record_type']}:{dependency['record_id']}"
                    )
                    continue
                if conn.execute(
                    f"SELECT 1 FROM {table} WHERE id=?", (dependency["record_id"],)
                ).fetchone() is None:
                    problems.append(
                        f"dangling-record-dependency:{dependency['record_type']}:{dependency['record_id']}"
                    )
                upstream_type = dependency["depends_on_type"]
                upstream_table = known_record_tables.get(upstream_type)
                if upstream_table is None:
                    # An unrecognized upstream type used to be skipped in
                    # silence, which is how a whole dependency edge could exist
                    # and propagate nothing. Anchor types are the one legitimate
                    # case: they are coordinates, not stored records.
                    if upstream_type not in RECORD_DEPENDENCY_ANCHOR_TYPES:
                        problems.append(
                            f"unknown-upstream-dependency-type:{upstream_type}:{dependency['depends_on_id']}"
                        )
                elif conn.execute(
                    f"SELECT 1 FROM {upstream_table} WHERE id=?", (dependency["depends_on_id"],)
                ).fetchone() is None:
                    problems.append(
                        f"dangling-upstream-dependency:{upstream_type}:{dependency['depends_on_id']}"
                    )
            for row in conn.execute(
                "SELECT id,target_content_hash,payload_json FROM passage_records"
            ).fetchall():
                try:
                    payload = json.loads(row["payload_json"])
                    actual = self.target_content_hash(payload["targetTextByDisplayedReference"])
                    if actual != row["target_content_hash"] or actual != payload["targetContentHash"]:
                        problems.append(f"passage-content-hash:{row['id']}")
                except (KeyError, TypeError, json.JSONDecodeError):
                    problems.append(f"invalid-passage-payload:{row['id']}")
            for row in conn.execute("SELECT id,payload_json FROM evidence_records").fetchall():
                try:
                    payload = json.loads(row["payload_json"])
                    actual = hashlib.sha256(payload["content"].encode("utf-8")).hexdigest()
                    if actual != payload["contentHash"]:
                        problems.append(f"evidence-content-hash:{row['id']}")
                except (KeyError, TypeError, json.JSONDecodeError):
                    problems.append(f"invalid-evidence-payload:{row['id']}")
        self.read_only = bool(problems)
        return {"ok": not problems, "readOnly": self.read_only, "problems": problems, "schemaVersion": self.schema_version()}

    def backup(self, backup_root: str | Path, *, reason: str) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        directory = Path(backup_root) / stamp
        directory.mkdir(parents=True, exist_ok=False)
        backup_db = directory / "bridge-semantic-v1.sqlite3"
        with self._connect() as source:
            destination = sqlite3.connect(str(backup_db))
            try:
                source.backup(destination)
                destination.commit()
            finally:
                destination.close()
        digest = hashlib.sha256(backup_db.read_bytes()).hexdigest()
        manifest = {"schemaId": SCHEMA_ID, "schemaVersion": self.schema_version(), "sha256": digest,
                    "source": str(self.path), "reason": reason, "createdAt": self._now()}
        (directory / "backup-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return directory

    def restore(self, backup_directory: str | Path) -> None:
        directory = Path(backup_directory)
        source_db = directory / "bridge-semantic-v1.sqlite3"
        manifest_path = directory / "backup-manifest.json"
        if not source_db.exists() or not manifest_path.exists():
            raise FoundationValidationError("Backup database or manifest is missing")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if hashlib.sha256(source_db.read_bytes()).hexdigest() != manifest.get("sha256"):
            raise FoundationValidationError("Backup checksum does not match its manifest")
        safety_root = self.path.parent / "restore-safety"
        self.backup(safety_root, reason="pre-restore safety backup")
        self.read_only = False
        temp = self.path.with_suffix(self.path.suffix + ".restore")
        if temp.exists():
            temp.unlink()
        source = sqlite3.connect(str(source_db))
        destination = sqlite3.connect(str(temp))
        try:
            source.backup(destination)
            destination.commit()
            if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise FoundationValidationError("Restored backup failed SQLite integrity check")
        finally:
            destination.close()
            source.close()
        for suffix in ("-wal", "-shm"):
            companion = Path(str(self.path) + suffix)
            if companion.exists():
                companion.unlink()
        last_replace_error: PermissionError | None = None
        for attempt in range(6):
            try:
                os.replace(temp, self.path)
                last_replace_error = None
                break
            except PermissionError as exc:
                last_replace_error = exc
                if attempt < 5:
                    time.sleep(0.05 * (attempt + 1))
        if last_replace_error is not None:
            # Windows can deny atomic replacement of a recently opened SQLite
            # file even after every Bridge connection is closed. SQLite's own
            # online-backup API is the transaction-safe fallback; the safety
            # backup above still makes the operation recoverable.
            source = sqlite3.connect(str(temp))
            destination = sqlite3.connect(str(self.path))
            try:
                source.backup(destination)
                destination.commit()
            except sqlite3.Error as exc:
                raise FoundationValidationError(
                    "Restored database could not replace the live companion store"
                ) from exc
            finally:
                destination.close()
                source.close()
            try:
                temp.unlink()
            except OSError:
                pass
        self.read_only = False
        # Backups from an older operational schema are restored losslessly and
        # then migrated through the same guarded path before becoming writable.
        self._migrate()
        check = self.recovery_check()
        if not check["ok"]:
            raise FoundationValidationError(f"Restored backup failed recovery checks: {check['problems']}")
