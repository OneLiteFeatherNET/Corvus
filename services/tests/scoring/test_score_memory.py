"""Memory bound of one ONNX run for raw-byte models: estimate, budget, chunking, load-time report."""

from __future__ import annotations

import dataclasses
import logging

import numpy as np
import pytest
from raw_support import PACKET, WINDOW, RawAssets
from score_rig import Rig
from test_raw_models import add_raw

from corvus_services.scoring.batching import PendingWindow
from corvus_services.scoring.memory import (
    chunked,
    rows_per_run,
    score_memory_budget,
    window_inference_bytes,
)
from corvus_services.scoring.raw import RawWindow
from corvus_services.scoring.scorer import infer
from corvus_services.scoring.settings import ScoreSettings

GIB = 2**30
T = 48
TOKENS = PACKET.max_tokens_per_packet


@pytest.fixture(scope="module")
def assets(tmp_path_factory) -> RawAssets:
    return RawAssets(tmp_path_factory.mktemp("tokenizer"))


# --- estimate ------------------------------------------------------------------------------------


def test_the_estimate_follows_the_inference_formula_of_the_default_model():
    p = 2048
    expected = 3 * max(4 * (p + 1) ** 2 * 4, 2 * T * T * p * 4) + (6 + 2 * 2) * p * T * 32 * 4

    assert window_inference_bytes(p, T) == expected


def test_a_2048_packet_window_needs_a_few_tenths_of_a_gib():
    assert 0.2 * GIB < window_inference_bytes(2048, T) < 0.4 * GIB


def test_the_estimate_grows_faster_than_linearly_with_the_packets():
    # the attention term is quadratic, the feed-forward term linear: between 2x and 4x per doubling
    ratio = window_inference_bytes(2048, T) / window_inference_bytes(1024, T)

    assert 2.5 < ratio < 4.0


def test_macro_windows_multiply_the_estimate():
    assert window_inference_bytes(512, T, macro_windows=3) == 3 * window_inference_bytes(512, T)


# --- budget and rows -----------------------------------------------------------------------------


def test_the_budget_is_half_of_the_memory_limit_by_default():
    assert score_memory_budget(limit=2 * GIB) == GIB


def test_the_budget_fraction_is_configurable():
    assert score_memory_budget(fraction=0.25, limit=2 * GIB) == GIB // 2


def test_an_explicit_budget_in_mib_wins_over_the_memory_limit():
    assert score_memory_budget(300, limit=2 * GIB) == 300 * 2**20


def test_the_environment_variable_sets_the_budget(monkeypatch):
    monkeypatch.setenv("CORVUS_SCORE_MEMORY_BUDGET_MB", "300")

    assert ScoreSettings().score_memory_budget_mb == 300


def test_rows_per_run_is_the_budget_over_the_window_estimate():
    assert rows_per_run(100, 450) == 4


def test_a_window_above_the_budget_still_gets_one_row_per_run():
    assert rows_per_run(10 * GIB, GIB) == 1


@pytest.mark.parametrize(
    ("packets", "limit_gib", "rows"),
    [(512, 1, 8), (1024, 1, 4), (2048, 1, 1), (512, 2, 17), (1024, 2, 8), (2048, 2, 3)],
)
def test_rows_per_run_for_the_default_model_under_the_pod_limits(packets, limit_gib, rows):
    budget = score_memory_budget(limit=limit_gib * GIB)

    assert rows_per_run(window_inference_bytes(packets, T), budget) == rows


def test_chunking_keeps_the_order_and_the_remainder():
    assert [list(c) for c in chunked(list(range(7)), 3)] == [[0, 1, 2], [3, 4, 5], [6]]


def test_chunking_by_zero_is_one_row_per_chunk():
    assert [list(c) for c in chunked([1, 2], 0)] == [[1], [2]]


# --- the scoring path ----------------------------------------------------------------------------


def pending(spec, n_packets: int = 6) -> PendingWindow:
    x = np.ones((n_packets, spec.packet.max_tokens_per_packet + 1), dtype=np.float32)
    return PendingWindow(RawWindow(b"s", 0, 0, 1, x), spec, b"p", "k", "pvp")


async def load_one(tmp_path, assets, **rig_args):
    rig = Rig(tmp_path, **rig_args)
    add_raw(rig, assets)
    await rig.manager.sync()
    (model,) = rig.manager.current.models
    return rig, model


async def test_a_micro_batch_is_split_into_runs_of_at_most_max_rows(tmp_path, assets):
    per_window = window_inference_bytes(WINDOW.max_packets, TOKENS)
    rig, model = await load_one(tmp_path, assets, memory_budget_bytes=per_window * 3)
    spec = model.raw_spec
    assert model.max_rows == 3

    scores, _ = infer(model, [pending(spec) for _ in range(7)])

    assert rig.factory.sessions["r1"].runs == [3, 3, 1]
    assert len(scores) == 7


async def test_chunked_scores_keep_the_window_order(tmp_path, assets):
    per_window = window_inference_bytes(WINDOW.max_packets, TOKENS)
    rig, model = await load_one(tmp_path, assets, memory_budget_bytes=per_window * 2)
    session = rig.factory.sessions["r1"]
    session.run = lambda x: [x[:, 0, 0:1].astype(np.float32)]  # type: ignore[method-assign]
    windows = []
    for i in range(5):
        w = pending(model.raw_spec)
        w.window.x[:] = i / 10  # type: ignore[union-attr]
        windows.append(w)

    scores, _ = infer(model, windows)

    assert scores.tolist() == pytest.approx([0.0, 0.1, 0.2, 0.3, 0.4])


async def test_without_a_budget_the_whole_batch_is_one_run(tmp_path, assets):
    rig, model = await load_one(tmp_path, assets)

    infer(model, [pending(model.raw_spec) for _ in range(5)])

    assert model.max_rows is None
    assert rig.factory.sessions["r1"].runs == [5]


# --- load-time report ----------------------------------------------------------------------------


async def test_the_load_logs_the_estimate_and_the_chunk_size_and_exports_gauges(
    tmp_path, assets, caplog
):
    per_window = window_inference_bytes(WINDOW.max_packets, TOKENS)
    rig = Rig(tmp_path, memory_budget_bytes=per_window * 2)
    add_raw(rig, assets)

    with caplog.at_level(logging.INFO, logger="corvus.score.models"):
        await rig.manager.sync()

    assert rig.metric("corvus_score_model_estimated_window_bytes", model="raw:1") == per_window
    assert rig.metric("corvus_score_model_max_rows", model="raw:1") == 2
    assert any("at most 2 row(s)" in r.getMessage() for r in caplog.records)
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


async def test_a_window_above_the_budget_logs_an_error_but_the_model_is_served(
    tmp_path, assets, caplog
):
    rig = Rig(tmp_path, memory_budget_bytes=1000)
    big = dataclasses.replace(WINDOW, max_packets=2048)
    add_raw(rig, assets, window=big)

    with caplog.at_level(logging.INFO, logger="corvus.score.models"):
        result = await rig.manager.sync()

    assert result.loaded == ["r1"]
    (message,) = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert "raw:1" in message
    assert "max_packets" in message
    assert "memory" in message
    assert rig.metric("corvus_score_model_max_rows", model="raw:1") == 1
    assert rig.metric("corvus_score_model_estimated_window_bytes", model="raw:1") > 1000
