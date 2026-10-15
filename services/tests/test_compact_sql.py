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
