"""SQL snapshots: the statements stay constant and only use bound parameters."""

from __future__ import annotations

import re

from corvus_services.api.pg import CURRENT_FILES_SQL, SESSIONS_PAGE_SQL
from corvus_services.compact_pg import lock_key


def placeholders(sql: str) -> set[str]:
    return set(re.findall(r"%\((\w+)\)s", sql))


def test_sessions_page_binds_every_value():
    assert placeholders(SESSIONS_PAGE_SQL) == {"snapshot_id", "after", "limit", "exclude_erased"}
    assert "%s" not in SESSIONS_PAGE_SQL


def test_sessions_page_reads_segments_labels_and_the_erased_flag():
    for fragment in (
        "FROM corvus.segments g",
        "FROM corvus.labels l",
        "LEFT JOIN corvus.sessions s ON s.session_id = sid.session_id",
        "l.valid_from IS NULL OR l.valid_from <= s.started_at",
    ):
        assert fragment in SESSIONS_PAGE_SQL


def test_sessions_page_orders_by_session_id_and_reads_the_erased_view():
    assert SESSIONS_PAGE_SQL.endswith("ORDER BY sid.session_id LIMIT %(limit)s")
    assert "corvus.erased_sessions" in SESSIONS_PAGE_SQL


def test_current_files_follows_replaced_by_to_live_files_only():
    assert placeholders(CURRENT_FILES_SQL) == {"snapshot_id"}
    assert "JOIN corvus.lake_files n ON n.id = c.replaced_by" in CURRENT_FILES_SQL
    assert "WHERE deleted_at IS NULL" in CURRENT_FILES_SQL


def test_lock_keys_are_distinct_per_file_and_fit_a_bigint():
    keys = {lock_key(i) for i in (1, 2, 10**9)}

    assert len(keys) == 3
    assert all(0 < k < 2**63 for k in keys)


def test_expired_raw_files_binds_every_value_and_reads_pool_retention():
    from corvus_services.pgcatalog import EXPIRED_RAW_FILES_SQL

    assert placeholders(EXPIRED_RAW_FILES_SQL) == {"today", "default_days", "max_days"}
    assert "%s" not in EXPIRED_RAW_FILES_SQL
    for fragment in (
        "p.raw_retention_days",
        "corvus.lake_file_sessions",
        "LEAST(",
        "max(days)",
        "f.table_name = 'raw' AND f.deleted_at IS NULL",
    ):
        assert fragment in EXPIRED_RAW_FILES_SQL


def test_derived_for_requests_skips_artifacts_of_jobs_started_after_the_request():
    from corvus_services.compact_pg import DERIVED_FOR_REQUESTS_SQL

    assert "a.contains_raw AND a.deleted_at IS NULL" in DERIVED_FOR_REQUESTS_SQL
    assert "j.created_at < r.requested_at" in DERIVED_FOR_REQUESTS_SQL


def test_current_raw_files_and_artifact_statements_bind_every_value():
    from corvus_services.api.pg import (
        CURRENT_RAW_FILES_SQL,
        LIST_ARTIFACTS_SQL,
        UPSERT_ARTIFACT_SQL,
    )

    assert placeholders(CURRENT_RAW_FILES_SQL) == {"snapshot_id"}
    assert placeholders(LIST_ARTIFACTS_SQL) == {"snapshot_id"}
    assert placeholders(UPSERT_ARTIFACT_SQL) == {
        "id", "snapshot_id", "job_id", "kind", "s3_prefix", "contains_raw", "created_at",
    }  # fmt: skip
    assert "ON CONFLICT (snapshot_id, s3_prefix)" in UPSERT_ARTIFACT_SQL
