"""Constant SQL fragments of the control-plane adapters that are worth testing without a database.

Everything here is pure: statements are constants, filters become named parameters, identifiers
never come from callers.
"""

from __future__ import annotations

from typing import Any

from .ports import ModelFilter


def list_sql(
    select_sql: str,
    conds: list[str],
    *,
    id_col: str,
    id_param_sql: str,
    time_col: str = "created_at",
    with_cursor: bool = False,
) -> str:
    """Keyset page, newest first. ``select_sql`` ends before WHERE; all parts are constants."""
    all_conds = list(conds)
    if with_cursor:
        all_conds.append(f"({time_col}, {id_col}) < (%(cursor_at)s, {id_param_sql})")
    sql = select_sql
    if all_conds:
        sql += " WHERE " + " AND ".join(all_conds)
    return sql + f" ORDER BY {time_col} DESC, {id_col} DESC LIMIT %(limit)s"


_MODEL_FILTER_ATTRS = ("name", "version", "game_context", "domain", "stage")


def model_conditions(flt: ModelFilter) -> tuple[list[str], dict[str, Any]]:
    where: list[str] = []
    params: dict[str, Any] = {}
    for attr in _MODEL_FILTER_ATTRS:
        value = getattr(flt, attr)
        if value is not None:
            where.append(f"{attr} = %(f_{attr})s")
            params[f"f_{attr}"] = value
    return where, params


# --- summary ------------------------------------------------------------------------------------

SUMMARY_COUNTS: dict[str, str] = {
    "pools": "SELECT count(*) FROM corvus.pools",
    "snapshots": "SELECT count(*) FROM corvus.dataset_snapshots",
    "pending_erasures": "SELECT count(*) FROM corvus.erasure_requests WHERE compaction_pending",
}

SUMMARY_GROUPS: dict[str, str] = {
    "jobs_by_status": "SELECT status, count(*) FROM corvus.jobs GROUP BY status",
    "models_by_stage": "SELECT stage, count(*) FROM corvus.models GROUP BY stage",
    "labels_by_label": "SELECT label, count(*) FROM corvus.labels GROUP BY label",
}

SUMMARY_RECENT_JOBS = (
    "SELECT id, type, status, snapshot_id, created_at, updated_at, finished_at"
    " FROM corvus.jobs ORDER BY created_at DESC, id DESC LIMIT %s"
)

SUMMARY_RECENT_ERASURES = (
    "SELECT id, pseudonym, requested_at, status, compaction_pending"
    " FROM corvus.erasure_requests ORDER BY requested_at DESC, id DESC LIMIT %s"
)

# --- erasure and jobs ---------------------------------------------------------------------------

DELETE_SCORES_LATEST = "DELETE FROM corvus.scores_latest WHERE pseudonym = %s"
DELETE_SCORE_HISTORY = "DELETE FROM corvus.score_history WHERE pseudonym = %s"

UPDATE_JOB = (
    "UPDATE corvus.jobs SET status = %s, runner_ref = COALESCE(%s, runner_ref),"
    " message = %s, updated_at = %s,"
    " finished_at = CASE WHEN %s THEN %s ELSE finished_at END,"
    " result = COALESCE(%s, result)"
)
