"""Throughput instead of a latency target: players per core from stride and mean latency."""

import pytest

from corvus_ml.eval.capacity import annotate_capacity, players_per_core
from corvus_ml.eval.config import BenchConfig, EvalConfig
from corvus_ml.eval.report import to_markdown


def test_players_per_core_is_stride_over_mean_latency():
    assert players_per_core(5.0, 50.0) == pytest.approx(100.0)


def test_a_five_second_window_latency_serves_one_player_per_core():
    assert players_per_core(5.0, 5000.0) == pytest.approx(1.0)


def test_threads_share_the_cores_of_one_window():
    assert players_per_core(5.0, 50.0, threads=2) == pytest.approx(50.0)


def test_a_zero_latency_is_unbounded():
    assert players_per_core(5.0, 0.0) == float("inf")


def test_the_default_latency_budget_is_a_5_second_sanity_bound():
    assert BenchConfig().latency_budget_ms == 5000.0
    assert EvalConfig().bench.latency_budget_ms == 5000.0


def test_annotate_adds_stride_and_players_to_the_bench_and_every_packet_size():
    res = {
        "bench": {"latency_mean_ms": 50.0},
        "bench_sizes": {"64": {"latency_mean_ms": 25.0}},
    }

    annotate_capacity(res, stride_seconds=5.0, threads=1)

    assert res["bench"]["stride_seconds"] == 5.0
    assert res["bench"]["players_per_core"] == pytest.approx(100.0)
    assert res["bench_sizes"]["64"]["players_per_core"] == pytest.approx(200.0)


def test_annotate_leaves_a_candidate_without_a_stride_alone():
    res = {"bench": {"latency_mean_ms": 50.0}}

    annotate_capacity(res, stride_seconds=None, threads=1)

    assert "players_per_core" not in res["bench"]


def _report(bench):
    return {
        "name": "t", "synthetic": True, "seed": 1, "threads": 1, "unlabelled": "exclude",
        "assumed_legit_windows": 0, "fpr_targets": [0.01], "split": [0.6, 0.2, 0.2],
        "latency_budget_ms": 5000.0,
        "contexts": {"pvp": {
            "windows": 1, "players": 1, "cheat_windows": 0, "split": {},
            "candidates": {"c": {
                "status": "ok", "n_params": 3, "bench": bench, "onnx": {},
                "confusion_at_val_threshold": {"1pct": {}},
                "test": {"roc_auc": 0.5, "pr_auc": 0.5, "ece": 0.1,
                         "recall@fpr1pct": 0.1, "resolved@fpr1pct": True,
                         "recall@fpr1pct_player_max": 0.1},
            }},
        }},
    }  # fmt: skip


def test_the_report_prints_players_per_core_next_to_p50_and_p99():
    bench = {
        "latency_p50_ms": 48.0,
        "latency_p99_ms": 60.0,
        "latency_mean_ms": 50.0,
        "players_per_core": 100.0,
        "batch_throughput_wps": 10.0,
        "peak_rss_mb": 1.0,
    }

    text = to_markdown(_report(bench))

    assert "players/core" in text
    assert "p99 <= 5000 ms" in text
    assert "100" in text
