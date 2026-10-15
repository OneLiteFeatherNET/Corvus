"""Benchmark structure only: no wall-clock thresholds in assertions."""

import math

import numpy as np
import pytest
from skl2onnx import to_onnx
from sklearn.ensemble import IsolationForest

from corvus_ml.eval import onnx_bench


@pytest.fixture
def model_path(tmp_path):
    x = np.random.default_rng(0).normal(size=(64, 6)).astype(np.float32)
    model = IsolationForest(n_estimators=10, random_state=0).fit(x)
    path = tmp_path / "m.onnx"
    path.write_bytes(
        to_onnx(model, x[:1], target_opset={"": 17, "ai.onnx.ml": 3}).SerializeToString()
    )
    return path, x


def check(res, threads):
    assert res.threads == threads
    assert res.n_windows_timed == 10
    assert 0 < res.latency_p50_ms <= res.latency_p99_ms
    assert res.latency_mean_ms > 0
    assert res.batch_throughput_wps > 0
    assert math.isfinite(res.batch_throughput_wps)
    assert res.peak_rss_mb > 0
    assert res.model_rss_delta_mb >= 0
    assert res.file_size_kb > 0


def test_benchmark_reports_latency_throughput_and_memory(model_path):
    path, x = model_path
    check(onnx_bench.benchmark(path, x, threads=1, warmup=2, iters=10, batch_iters=2), 1)


def test_session_uses_the_configured_thread_count_and_sequential_execution(model_path):
    path, _ = model_path
    sess = onnx_bench.make_session(path, threads=2)
    opts = sess.get_session_options()
    assert opts.intra_op_num_threads == 2
    assert opts.inter_op_num_threads == 1
    assert opts.execution_mode == onnx_bench.ort.ExecutionMode.ORT_SEQUENTIAL


def test_isolated_benchmark_runs_in_a_separate_process(model_path):
    path, x = model_path
    res = onnx_bench.benchmark_isolated(path, x, threads=1, warmup=2, iters=10, batch_iters=2)
    check(res, 1)


def test_batch_larger_than_the_example_set_cycles_the_rows(model_path):
    path, x = model_path
    res = onnx_bench.benchmark(path, x[:3], threads=1, warmup=1, iters=4, batch=8, batch_iters=1)
    assert res.batch_size == 8
