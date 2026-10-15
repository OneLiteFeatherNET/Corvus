"""SQL snapshots: the statements the new endpoints run, checked without a database."""

from __future__ import annotations

import pytest

from corvus_services.api import queries
from corvus_services.api.ports import ModelFilter
from corvus_services.api.queries import list_sql, model_conditions

MODELS = "SELECT id FROM corvus.models"


def test_first_page_has_no_cursor_condition():
    assert list_sql(MODELS, [], id_col="id", id_param_sql="%(cursor_id)s::uuid") == (
        "SELECT id FROM corvus.models ORDER BY created_at DESC, id DESC LIMIT %(limit)s"
    )


def test_next_page_continues_after_the_cursor():
    sql = list_sql(
        MODELS, ["stage = %(f_stage)s"], id_col="id", id_param_sql="%(cursor_id)s::uuid",
        with_cursor=True,
    )  # fmt: skip

    assert sql == (
        "SELECT id FROM corvus.models WHERE stage = %(f_stage)s"
        " AND (created_at, id) < (%(cursor_at)s, %(cursor_id)s::uuid)"
        " ORDER BY created_at DESC, id DESC LIMIT %(limit)s"
    )


def test_erasure_requests_page_by_requested_at():
    sql = list_sql(
        "SELECT id FROM corvus.erasure_requests", [], id_col="id",
        id_param_sql="%(cursor_id)s::uuid", time_col="requested_at", with_cursor=True,
    )  # fmt: skip

    assert sql == (
        "SELECT id FROM corvus.erasure_requests"
        " WHERE (requested_at, id) < (%(cursor_at)s, %(cursor_id)s::uuid)"
        " ORDER BY requested_at DESC, id DESC LIMIT %(limit)s"
    )


def test_no_model_filter_adds_no_condition():
    assert model_conditions(ModelFilter()) == ([], {})


def test_every_model_filter_is_a_bound_parameter():
    flt = ModelFilter("gbdt", "2", "bedwars", "movement", "enforce")

    where, params = model_conditions(flt)

    assert where == [
        "name = %(f_name)s",
        "version = %(f_version)s",
        "game_context = %(f_game_context)s",
        "domain = %(f_domain)s",
        "stage = %(f_stage)s",
    ]
    assert params == {
        "f_name": "gbdt",
        "f_version": "2",
        "f_game_context": "bedwars",
        "f_domain": "movement",
        "f_stage": "enforce",
    }


def test_filter_values_never_reach_the_sql_text():
    hostile = "x'; DROP TABLE corvus.models; --"

    where, params = model_conditions(ModelFilter(name=hostile))

    assert hostile not in " ".join(where)
    assert params == {"f_name": hostile}


def test_summary_counts_snapshot():
    assert queries.SUMMARY_COUNTS == {
        "pools": "SELECT count(*) FROM corvus.pools",
        "snapshots": "SELECT count(*) FROM corvus.dataset_snapshots",
        "pending_erasures": "SELECT count(*) FROM corvus.erasure_requests WHERE compaction_pending",
    }


def test_summary_groups_snapshot():
    assert queries.SUMMARY_GROUPS == {
        "jobs_by_status": "SELECT status, count(*) FROM corvus.jobs GROUP BY status",
        "models_by_stage": "SELECT stage, count(*) FROM corvus.models GROUP BY stage",
        "labels_by_label": "SELECT label, count(*) FROM corvus.labels GROUP BY label",
    }


def test_recent_activity_is_bounded_and_newest_first():
    assert queries.SUMMARY_RECENT_JOBS == (
        "SELECT id, type, status, snapshot_id, created_at, updated_at, finished_at"
        " FROM corvus.jobs ORDER BY created_at DESC, id DESC LIMIT %s"
    )
    assert queries.SUMMARY_RECENT_ERASURES == (
        "SELECT id, pseudonym, requested_at, status, compaction_pending"
        " FROM corvus.erasure_requests ORDER BY requested_at DESC, id DESC LIMIT %s"
    )


def test_erasure_deletes_both_score_tables_by_pseudonym_only():
    assert queries.DELETE_SCORES_LATEST == "DELETE FROM corvus.scores_latest WHERE pseudonym = %s"
    assert queries.DELETE_SCORE_HISTORY == "DELETE FROM corvus.score_history WHERE pseudonym = %s"


def test_job_update_keeps_the_result_unless_a_new_one_is_given():
    assert queries.UPDATE_JOB.endswith("result = COALESCE(%s, result)")


@pytest.mark.parametrize(
    "sql", [*queries.SUMMARY_COUNTS.values(), *queries.SUMMARY_GROUPS.values()]
)
def test_summary_statements_only_read(sql):
    assert sql.startswith("SELECT ")


def test_the_job_result_migration_adds_a_nullable_column():
    from pathlib import Path

    text = (Path(__file__).parents[2] / "migrations" / "0004_job_result.sql").read_text()

    assert "ALTER TABLE corvus.jobs ADD COLUMN result jsonb;" in text
    assert "NOT NULL" not in text
