"""The ablation variants as candidates of the evaluation harness: variant names, parity, report."""

import pytest

from corvus_ml.eval import candidates
from corvus_ml.eval.config import EvalConfig
from corvus_ml.eval.report import to_markdown
from corvus_ml.eval.runner import run
from corvus_ml.eval.tune import space_for
from test_raw_eval import FAST, SMALL, config

VARIANTS = {
    "raw_hier_anomaly__base": {},
    "raw_hier_anomaly__mae": {
        "p_packet": 0.5,
        "packet_mask": "full",
        "span_mean": 3.0,
        "entropy_weight": True,
    },  # fmt: skip
    "raw_hier_anomaly__jepa": {"objective": "jepa", "ema_momentum": 0.9, "type_pos": True},
    "raw_hier_anomaly__knn": {"combine": "zsum", "knn_bank": 16, "knn_k": 3},
    "raw_hier_classifier__typepos": {"type_pos": True},
}


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    out = tmp_path_factory.mktemp("variants-eval")
    cfg = config(
        {name: FAST | SMALL | params for name, params in VARIANTS.items()},
        raw_extra={
            "tokenizer": {"mode": "bytes", "layout": {"packet_type_max": 64}},
            "packet": {"max_tokens_per_packet": 24},
        },
        benchmark={"warmup": 1, "iters": 3, "batch": 2, "isolated": False, "raw_packets": [16]},
    )
    return run(cfg, out), out


def test_variant_names_resolve_to_their_candidate_under_their_own_name():
    cls = candidates.REGISTRY["raw_hier_anomaly__jepa"]

    assert "raw_hier_anomaly__jepa" in candidates.REGISTRY
    assert cls.name == "raw_hier_anomaly__jepa"
    assert issubclass(cls, candidates.REGISTRY["raw_hier_anomaly"])
    assert "raw_hier_anomaly__" not in candidates.REGISTRY
    assert "nothing__jepa" not in candidates.REGISTRY


def test_variants_use_the_tuning_space_of_their_candidate():
    cfg = EvalConfig.from_dict(
        {
            "raw": {"synthetic": {}},
            "candidates": {"raw_hier_anomaly__x": {}},
            "tune": {"space": {"p_packet": [0.3, 0.9]}},
        }  # fmt: skip
    )

    space = space_for("raw_hier_anomaly__x", cfg.tune, windows=False)

    assert space["p_packet"] == ("float", 0.3, 0.9)
    assert "d_model" in space


def test_every_variant_is_exported_with_matching_scores(report):
    rep, _ = report
    res = rep["contexts"]["pvp"]["candidates"]

    assert set(res) == set(VARIANTS)
    for name, c in res.items():
        assert c["status"] == "ok", (name, c)
        assert c["onnx"]["parity_ok"], (name, c["onnx"])


def test_every_variant_reports_parameters_and_latency(report):
    rep, _ = report
    res = rep["contexts"]["pvp"]["candidates"]
    text = to_markdown(rep)

    for name, c in res.items():
        assert c["n_params"] > 0
        assert c["bench"]["latency_p50_ms"] > 0
        assert c["bench"]["latency_p99_ms"] >= c["bench"]["latency_p50_ms"]
        assert c["bench_sizes"]["16"]["latency_p99_ms"] > 0
        assert name in text
    assert "p99 <= 10 ms" in text


def test_report_names_the_tokenizer_mode(report):
    rep, _ = report

    assert rep["raw_bytes"]["tokenizer_mode"] == "bytes"
    assert "bytes, vocab" in to_markdown(rep)
