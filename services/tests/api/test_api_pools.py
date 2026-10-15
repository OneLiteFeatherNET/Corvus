from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from api_support import Env
from pydantic import ValidationError

from corvus_services.api.pools import (
    FileShare,
    PoolFilter,
    RawPreview,
    build_pool_query,
    compute_preview,
    preview_statements,
    snapshot_statements,
)

# --- filter -> SQL ------------------------------------------------------------------------------


def test_empty_filter_selects_non_orphan_sessions_of_players_that_are_not_erased():
    q = build_pool_query(PoolFilter())

    assert q.sessions_sql == (
        "SELECT s.session_id FROM corvus.sessions s WHERE"
        " (s.pseudonym IS NULL OR NOT EXISTS (SELECT 1 FROM corvus.players p"
        " WHERE p.pseudonym = s.pseudonym AND p.erased_at IS NOT NULL))"
        " AND s.orphan = false"
    )
    assert q.params == {}


def test_orphans_can_be_included():
    q = build_pool_query(PoolFilter(exclude_orphans=False))

    assert "s.orphan" not in q.sessions_sql


def test_full_filter_generates_the_expected_sql_and_parameters():
    f = PoolFilter.model_validate(
        {
            "game_contexts": ["bedwars", "skywars"],
            "backend_servers": ["bw-1"],
            "client_protocol": {"min": 760, "max": 800},
            "client_brand": {"include": ["vanilla"], "exclude": ["lunar"]},
            "date_range": {"start": "2026-09-01", "end": "2026-09-30"},
            "label": {"value": "cheat", "min_confidence": 0.8},
            "min_session_duration_s": 60,
            "max_drop_rate": 0.05,
            "exclude_orphans": True,
            "retention_days": 90,
        }
    )

    q = build_pool_query(f)

    assert q.sessions_sql == (
        "SELECT s.session_id FROM corvus.sessions s WHERE"
        " (s.pseudonym IS NULL OR NOT EXISTS (SELECT 1 FROM corvus.players p"
        " WHERE p.pseudonym = s.pseudonym AND p.erased_at IS NOT NULL))"
        " AND s.orphan = false"
        " AND EXISTS (SELECT 1 FROM corvus.segments g WHERE g.session_id = s.session_id"
        " AND g.game_context = ANY(%(game_contexts)s)"
        " AND g.backend_server = ANY(%(backend_servers)s)"
        " AND g.client_brand = ANY(%(brand_include)s)"
        " AND NOT (g.client_brand = ANY(%(brand_exclude)s)))"
        " AND s.client_protocol >= %(protocol_min)s AND s.client_protocol <= %(protocol_max)s"
        " AND s.started_at >= %(date_start)s AND s.started_at < %(date_end)s"
        " AND s.ended_at IS NOT NULL AND s.ended_at - s.started_at"
        " >= make_interval(secs => %(min_duration_s)s)"
        " AND ((s.dropped_queue_full + s.dropped_privacy + s.dropped_transport) = 0"
        " OR (s.dropped_queue_full + s.dropped_privacy + s.dropped_transport)::float8"
        " / ((s.dropped_queue_full + s.dropped_privacy + s.dropped_transport)"
        " + (SELECT coalesce(sum(f.row_count::float8 / n.cnt), 0)"
        " FROM corvus.lake_file_sessions x"
        " JOIN corvus.lake_files f ON f.id = x.file_id AND f.table_name = 'events'"
        " AND f.deleted_at IS NULL"
        " JOIN LATERAL (SELECT count(*) AS cnt FROM corvus.lake_file_sessions y"
        " WHERE y.file_id = x.file_id) n ON true WHERE x.session_id = s.session_id))"
        " <= %(max_drop_rate)s)"
        " AND EXISTS (SELECT 1 FROM corvus.labels l WHERE"
        " (l.session_id = s.session_id OR l.pseudonym = s.pseudonym)"
        " AND (l.valid_from IS NULL OR l.valid_from <= s.started_at)"
        " AND (l.valid_to IS NULL OR l.valid_to > s.started_at)"
        " AND l.label = %(label_value)s AND l.confidence >= %(label_min_confidence)s)"
    )
    assert q.params == {
        "game_contexts": ["bedwars", "skywars"],
        "backend_servers": ["bw-1"],
        "brand_include": ["vanilla"],
        "brand_exclude": ["lunar"],
        "protocol_min": 760,
        "protocol_max": 800,
        "date_start": datetime(2026, 9, 1, tzinfo=UTC),
        "date_end": datetime(2026, 10, 1, tzinfo=UTC),  # end date is inclusive
        "min_duration_s": 60,
        "max_drop_rate": 0.05,
        "label_value": "cheat",
        "label_min_confidence": 0.8,
    }


def test_segment_selection_only_counts_segments_matching_the_segment_filters():
    q = build_pool_query(PoolFilter(game_contexts=["bedwars"]))

    assert q.segments_sql.startswith(
        "SELECT g.session_id, g.segment FROM corvus.segments g WHERE g.session_id IN (SELECT"
    )
    assert q.segments_sql.endswith("AND g.game_context = ANY(%(game_contexts)s)")


def test_unlabeled_filter_negates_any_valid_label():
    q = build_pool_query(PoolFilter.model_validate({"label": {"value": "unlabeled"}}))

    assert "NOT EXISTS (SELECT 1 FROM corvus.labels l WHERE" in q.sessions_sql
    assert "label_value" not in q.params


def test_retention_does_not_change_the_selection():
    assert build_pool_query(PoolFilter(retention_days=30)) == build_pool_query(PoolFilter())


@pytest.mark.parametrize(
    "evil",
    ["x'; DROP TABLE corvus.sessions; --", "' OR '1'='1", "%(date_start)s", "a\x00b", "\\'"],
)
def test_user_values_never_appear_in_the_sql_text(evil: str):
    f = PoolFilter(
        game_contexts=[evil],
        backend_servers=[evil],
        client_brand={"include": [evil], "exclude": [evil]},
    )

    q = build_pool_query(f)
    statements = [q.sessions_sql, q.segments_sql, *preview_statements(q).values()]
    statements += snapshot_statements(q).values()

    for sql in statements:
        assert evil not in sql
        assert "DROP" not in sql
    assert q.params["game_contexts"] == [evil]
    assert q.params["brand_exclude"] == [evil]


def test_every_placeholder_is_bound_and_every_param_is_used():
    f = PoolFilter.model_validate(
        {
            "game_contexts": ["a"],
            "client_protocol": {"min": 1},
            "date_range": {"end": "2026-01-01"},
            "max_drop_rate": 0.5,
            "label": {"value": "legit"},
        }
    )
    q = build_pool_query(f)

    import re

    used = set(re.findall(r"%\((\w+)\)s", q.sessions_sql))
    assert used == set(q.params)


@pytest.mark.parametrize(
    "bad",
    [
        {"client_protocol": {"min": 5, "max": 1}},
        {"date_range": {"start": "2026-02-01", "end": "2026-01-01"}},
        {"max_drop_rate": 1.5},
        {"min_session_duration_s": -1},
        {"retention_days": 0},
        {"label": {"value": "maybe"}},
        {"unknown_field": 1},
        {"game_contexts": ["x"] * 201},
    ],
)
def test_invalid_filters_are_rejected(bad: dict):
    with pytest.raises(ValidationError):
        PoolFilter.model_validate(bad)


def test_date_range_uses_plain_dates():
    f = PoolFilter.model_validate({"date_range": {"start": "2026-09-01"}})

    assert f.date_range.start == date(2026, 9, 1)


# --- preview math -------------------------------------------------------------------------------


def test_frames_are_weighted_by_the_selected_share_of_each_file():
    raw = RawPreview(
        sessions=6,
        segments=9,
        players=4,
        labels={"cheat": 1},
        files=[
            FileShare(row_count=1000, total_sessions=4, selected_sessions=1),  # 250
            FileShare(row_count=500, total_sessions=2, selected_sessions=2),  # 500
            FileShare(row_count=999, total_sessions=3, selected_sessions=1),  # 333
        ],
    )

    out = compute_preview(raw)

    assert out.total_frames_estimate == 1083
    assert (out.sessions, out.segments, out.distinct_players, out.events_files) == (6, 9, 4, 3)


def test_label_distribution_always_lists_all_buckets():
    out = compute_preview(RawPreview(3, 3, 1, {"legit": 2, "unlabeled": 1}, []))

    assert out.label_distribution == {"legit": 2, "cheat": 0, "unlabeled": 1}


def test_empty_pool_previews_as_zero():
    out = compute_preview(RawPreview(0, 0, 0, {}, []))

    assert out.total_frames_estimate == 0
    assert out.events_files == 0


def test_a_file_without_sessions_cannot_divide_by_zero():
    out = compute_preview(RawPreview(0, 0, 0, {}, [FileShare(100, 0, 0)]))

    assert out.total_frames_estimate == 0


# --- HTTP ---------------------------------------------------------------------------------------


@pytest.fixture
def ds(env: Env, token):
    return env.headers(token(["data_scientist"], sub="dana"))


def _create(env: Env, headers, name="p1", **flt):
    return env.client.post("/api/v1/pools", json={"name": name, "filter": flt}, headers=headers)


def test_pool_crud_roundtrip(env: Env, ds):
    created = _create(env, ds, game_contexts=["bedwars"]).json()

    assert created["created_by"] == "dana"
    got = env.client.get(f"/api/v1/pools/{created['id']}", headers=ds).json()
    assert got["filter"]["game_contexts"] == ["bedwars"]
    assert got["filter"]["exclude_orphans"] is True

    put = env.client.put(
        f"/api/v1/pools/{created['id']}",
        json={"name": "renamed", "filter": {"retention_days": 30}},
        headers=ds,
    )
    assert put.status_code == 200
    assert put.json()["filter"]["retention_days"] == 30
    assert put.json()["created_at"] == created["created_at"]

    assert env.client.delete(f"/api/v1/pools/{created['id']}", headers=ds).status_code == 204
    assert env.client.get(f"/api/v1/pools/{created['id']}", headers=ds).status_code == 404


def test_duplicate_pool_name_is_a_conflict(env: Env, ds):
    assert _create(env, ds).status_code == 201

    resp = _create(env, ds)

    assert resp.status_code == 409
    assert resp.headers["content-type"].startswith("application/problem+json")


def test_invalid_filter_returns_validation_problem(env: Env, ds):
    resp = _create(env, ds, max_drop_rate=7)

    assert resp.status_code == 422
    assert resp.json()["errors"][0]["loc"][-1] == "max_drop_rate"


def test_unknown_pool_is_404(env: Env, ds):
    unknown = "00000000-0000-0000-0000-0000000000ff"
    for method, path in [("GET", ""), ("DELETE", ""), ("POST", "/preview"), ("POST", "/snapshots")]:
        resp = env.client.request(method, f"/api/v1/pools/{unknown}{path}", headers=ds)
        assert resp.status_code == 404, (method, path)


def test_pools_are_listed_newest_first_with_cursor_pagination(env: Env, ds):
    for i in range(5):
        env.clock.advance(1)
        _create(env, ds, name=f"pool-{i}")

    first = env.client.get("/api/v1/pools", headers=ds).json()
    assert [p["name"] for p in first["items"]] == ["pool-4", "pool-3"]  # default page size 2
    second = env.client.get(f"/api/v1/pools?cursor={first['next_cursor']}", headers=ds).json()
    assert [p["name"] for p in second["items"]] == ["pool-2", "pool-1"]
    third = env.client.get(f"/api/v1/pools?cursor={second['next_cursor']}", headers=ds).json()
    assert [p["name"] for p in third["items"]] == ["pool-0"]
    assert third["next_cursor"] is None


def test_page_size_is_capped_by_the_server_maximum(env: Env, ds):
    for i in range(7):
        env.clock.advance(1)
        _create(env, ds, name=f"pool-{i}")

    resp = env.client.get("/api/v1/pools?limit=100", headers=ds).json()

    assert len(resp["items"]) == 5


def test_garbage_cursor_is_a_bad_request(env: Env, ds):
    resp = env.client.get("/api/v1/pools?cursor=@@@", headers=ds)

    assert resp.status_code == 400


def test_preview_runs_the_pool_filter_and_returns_computed_counts(env: Env, ds):
    pool = _create(env, ds, game_contexts=["bedwars"]).json()
    env.parts["catalog"].raw = RawPreview(
        10, 14, 6, {"cheat": 2, "legit": 3, "unlabeled": 5}, [FileShare(1000, 10, 5)]
    )

    resp = env.client.post(f"/api/v1/pools/{pool['id']}/preview", headers=ds)

    assert resp.status_code == 200
    assert resp.json() == {
        "sessions": 10,
        "segments": 14,
        "distinct_players": 6,
        "events_files": 1,
        "total_frames_estimate": 500,
        "label_distribution": {"legit": 3, "cheat": 2, "unlabeled": 5},
    }
    assert env.parts["catalog"].filters[-1].game_contexts == ["bedwars"]
