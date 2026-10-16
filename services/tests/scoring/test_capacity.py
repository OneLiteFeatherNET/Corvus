"""Throughput instead of a latency budget: capacity estimate, consumer lag watch, defaults."""

from __future__ import annotations

import asyncio
import logging

import pytest
from raw_support import WINDOW, RawAssets
from score_rig import Rig
from test_app import ScriptedSource
from test_raw_models import add_raw

from corvus_services.scoring.app import ScoringApp
from corvus_services.scoring.lag import LagWatch
from corvus_services.scoring.metrics import ScoreMetrics
from corvus_services.scoring.raw import estimate_capacity_players, estimate_mean_ms, estimate_p99_ms
from corvus_services.scoring.settings import ScoreSettings


@pytest.fixture(scope="module")
def assets(tmp_path_factory) -> RawAssets:
    return RawAssets(tmp_path_factory.mktemp("tokenizer"))


def test_the_default_p99_budget_is_a_5_second_sanity_bound():
    assert ScoreSettings().score_p99_budget_ms == 5000.0


def test_capacity_is_cores_times_stride_over_mean_latency():
    # stride 5 s, 50 ms: 100 players per core
    assert estimate_capacity_players(
        cpus=1, intra_op_threads=1, stride_seconds=5.0, mean_ms=50.0
    ) == (pytest.approx(100.0))
    assert estimate_capacity_players(
        cpus=4, intra_op_threads=1, stride_seconds=5.0, mean_ms=50.0
    ) == (pytest.approx(400.0))


def test_five_second_latency_serves_one_player_per_core():
    assert estimate_capacity_players(1, 1, 5.0, 5000.0) == pytest.approx(1.0)


def test_intra_op_threads_occupy_cores_so_fewer_windows_run_in_parallel():
    assert estimate_capacity_players(4, 2, 5.0, 50.0) == pytest.approx(200.0)


def test_capacity_needs_a_positive_latency():
    assert estimate_capacity_players(1, 1, 5.0, 0.0) == float("inf")


def test_the_mean_estimate_is_the_p99_estimate_without_the_tail_factor():
    assert estimate_p99_ms(512, 1) == pytest.approx(estimate_mean_ms(512, 1) * 1.15)


async def test_a_loaded_raw_model_exports_its_estimated_capacity(tmp_path, assets, caplog):
    rig = Rig(tmp_path)  # the rig gives the manager 2 CPUs
    add_raw(rig, assets)  # stride 1 s, at most 32 packets
    with caplog.at_level(logging.INFO, logger="corvus.score.models"):
        await rig.manager.sync()

    intra = rig.factory.created[-1][1]
    expected = estimate_capacity_players(
        2, intra, WINDOW.stride_seconds, estimate_mean_ms(32, intra)
    )
    assert rig.metric("corvus_score_model_estimated_capacity_players", model="raw:1") == (
        pytest.approx(expected)
    )
    assert any("players" in r.getMessage() for r in caplog.records)


# --- consumer lag -----------------------------------------------------------------------------


def test_pending_windows_growing_over_n_polls_is_reported_once():
    watch = LagWatch(polls=3)

    assert [watch.observe(p) for p in (10, 20, 30, 40)] == [False, False, False, True]
    assert watch.observe(50) is False, "reported once per episode"


def test_a_shrinking_backlog_ends_the_episode():
    watch = LagWatch(polls=3)
    for p in (10, 20, 30, 40):
        watch.observe(p)

    assert watch.observe(5) is False
    assert [watch.observe(p) for p in (6, 7, 8)] == [False, False, True]


def test_a_flat_or_empty_backlog_never_reports():
    watch = LagWatch(polls=2)

    assert [watch.observe(p) for p in (0, 0, 0, 7, 7, 7)] == [False] * 6


async def test_the_app_polls_the_backlog_and_warns_when_the_scorer_falls_behind(tmp_path, caplog):
    rig = Rig(tmp_path)
    await rig.start()
    stop = asyncio.Event()
    pendings = iter([5, 10, 20, 40])

    async def backlog() -> int:
        return next(pendings)

    metrics = ScoreMetrics()
    watch = LagWatch(polls=3)
    app = ScoringApp(
        rig.settings, ScriptedSource([], stop, []), rig.service, rig.manager, rig.writer
    )
    with caplog.at_level(logging.WARNING, logger="corvus.score"):
        for _ in range(4):
            await app.poll_backlog(backlog, watch, metrics)

    assert [r for r in caplog.records if "behind" in r.getMessage()] != []
    assert metrics.consumer_pending._value.get() == 40  # type: ignore[attr-defined]
    assert metrics.falling_behind._value.get() == 1  # type: ignore[attr-defined]
