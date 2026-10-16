"""``evaluate`` and ``train``: reports, model artefacts and the registration payload."""

import json

import numpy as np
import pytest

from corvus_ml.eval import onnx_bench
from corvus_ml.eval.synthetic import SyntheticConfig, generate
from corvus_ml.features.spec import SPEC_FILE, FeatureSpec
from corvus_ml.jobs.lifecycle import JobError, run_job
from corvus_ml.jobs.model_jobs import (
    choose_candidate,
    evaluate,
    metric_key,
    registry_metrics,
    train,
)
from job_fakes import JOB_ID, SNAPSHOT_ID, FakeApi, key_set, make_context, make_env

FEATURES = f"features/{SNAPSHOT_ID}"
SEQ_LEN = 24
FAST_BENCH = {"warmup": 1, "iters": 5, "batch": 4, "isolated": False, "quantize": False}
QUICK = {
    "candidates": {
        "isolation_forest": {"n_estimators": 20},
        "gradient_boosting": {"max_iter": 10},
    },
    "benchmark": FAST_BENCH,
    "seq_len": SEQ_LEN,
}


@pytest.fixture(scope="module")
def tables():
    return generate(
        SyntheticConfig(
            seed=3,
            n_legit=20,
            n_cheat=10,
            sessions_per_player=2,
            windows_per_session=4,
            frames_per_window=SEQ_LEN,
            contexts=("pvp", "survival"),
        )
    )


def with_features(tmp_path, tables, *, build=True):
    """A bucket that already holds the output of build_features for the snapshot."""
    env = make_env(tmp_path, FakeApi())
    staged = tmp_path / "staged"
    tables.write(staged)
    FeatureSpec.current(seq_len=SEQ_LEN).write(staged)
    env.store.upload_dir(FEATURES, staged)
    if build:
        env.store.put_bytes(f"{FEATURES}/build.json", b"{}")
    return env


@pytest.fixture(scope="module")
def trained(tables, tmp_path_factory):
    """One shared training run; the tests that use it only read what it produced."""
    env = with_features(tmp_path_factory.mktemp("trained"), tables)
    return env, train(make_context("train", **QUICK), env)


def test_evaluate_uploads_reports_but_registers_nothing(tmp_path, tables):
    env = with_features(tmp_path, tables)

    outcome = evaluate(make_context("evaluate", **QUICK), env)

    prefix = f"reports/{SNAPSHOT_ID}/{JOB_ID}"
    assert key_set(env.store, prefix) == {f"{prefix}/report.json", f"{prefix}/report.md"}
    report = json.loads(env.store.get_bytes(f"{prefix}/report.json"))
    assert set(report["contexts"]) == {"pvp", "survival"}
    assert env.api.models == []
    assert outcome.result["report_prefix"] == prefix
    assert outcome.result["contexts"]["pvp"]["chosen"] in QUICK["candidates"]


def test_train_registers_one_model_per_context_in_stage_registered_form(trained):
    env, outcome = trained

    payloads = {m["game_context"]: m for m in env.api.models}
    assert set(payloads) == {"pvp", "survival"}
    for context, payload in payloads.items():
        assert set(payload) == {
            "name",
            "version",
            "candidate_type",
            "game_context",
            "domain",
            "snapshot_id",
            "metrics",
            "onnx_key",
        }
        assert payload["snapshot_id"] == SNAPSHOT_ID
        assert payload["domain"] == "general"
        assert payload["name"].endswith(f"-{context}")
        assert payload["version"] == f"{SNAPSHOT_ID[:8]}-{JOB_ID[:8]}"
        assert payload["candidate_type"].endswith("_calibrated")
        assert payload["onnx_key"] == f"models/{payload['name']}/{payload['version']}/model.onnx"
    assert [m["id"] for m in outcome.result["models"]] == ["model-1", "model-2"]


def test_registered_metrics_use_the_names_of_the_quality_gate(trained):
    env, outcome = trained

    metrics = env.api.models[0]["metrics"]
    assert {"recall_at_fpr_0_1pct", "recall_at_fpr_1pct", "p99_latency_ms"} <= set(metrics)
    assert {"roc_auc", "pr_auc", "p50_latency_ms", "n_params", "test_windows"} <= set(metrics)
    assert all(isinstance(v, (int, float)) for v in metrics.values())
    assert all(np.isfinite(v) for v in metrics.values())
    assert metrics["p99_latency_ms"] > 0


def test_the_model_artefacts_sit_next_to_the_onnx_key(trained):
    env, outcome = trained

    payload = env.api.models[0]
    prefix = payload["onnx_key"].rsplit("/", 1)[0]
    assert key_set(env.store, prefix) == {
        f"{prefix}/model.onnx",
        f"{prefix}/{SPEC_FILE}",
        f"{prefix}/calibration.json",
        f"{prefix}/model_card.json",
        f"{prefix}/evaluation.json",
    }
    # the scorer finds the spec by the onnx key, so both must be loadable as published
    FeatureSpec.load(env.store.path(f"{prefix}/{SPEC_FILE}"))


def test_the_published_graph_outputs_a_calibrated_score(trained):
    env, outcome = trained

    for payload in env.api.models:
        session = onnx_bench.make_session(env.store.path(payload["onnx_key"]), 1)
        assert [o.name for o in session.get_outputs()] == ["score"]
        width = session.get_inputs()[0].shape[1]
        x = np.random.default_rng(0).normal(size=(32, width)).astype(np.float32)
        score = onnx_bench.run_session(session, x)[0]
        assert score.min() >= 0.0
        assert score.max() <= 1.0


def test_the_model_card_describes_calibration_snapshot_and_output(trained):
    env, outcome = trained

    payload = env.api.models[0]
    card = json.loads(
        env.store.get_bytes(payload["onnx_key"].replace("model.onnx", "model_card.json"))
    )
    assert card["snapshot_id"] == SNAPSHOT_ID
    assert card["job_id"] == JOB_ID
    assert card["created_at"] == "2026-10-15T12:00:00+00:00"
    assert card["candidate_type"] == payload["candidate_type"]
    assert card["calibration"]["method"] in {"isotonic", "platt", "ecdf"}
    assert card["output_semantics"]["range"] == [0.0, 1.0]
    assert card["output_semantics"]["onnx_output"] == "score"
    assert card["metrics"] == payload["metrics"]


def test_name_version_and_domain_can_be_set_by_the_caller(tmp_path, tables):
    env = with_features(tmp_path, tables)
    params = {**QUICK, "model_name": "Combat Model", "version": "2026.10", "domain": "combat"}

    train(make_context("train", game_context="pvp", **params), env)

    payload = env.api.models[0]
    assert (payload["name"], payload["version"], payload["domain"]) == (
        "Combat_Model-pvp",
        "2026.10",
        "combat",
    )
    assert payload["onnx_key"] == "models/Combat_Model-pvp/2026.10/model.onnx"


def test_label_sources_restrict_which_labels_train_the_model(tmp_path, tables):
    env = with_features(tmp_path, tables)
    labelled = tables.labels.to_pylist()
    assert labelled, "synthetic data should carry labels"
    only = {**QUICK, "candidates": {"isolation_forest": {"n_estimators": 20}}}

    outcome = evaluate(
        make_context(
            "evaluate", label_sources=["no-such-source"], unlabelled="assume_legit", **only
        ),
        env,
    )

    # without labels the unsupervised candidate still runs on windows assumed to be legit
    pvp = outcome.result["contexts"]["pvp"]["candidates"]["isolation_forest"]
    assert pvp["status"] == "ok"
    assert "recall_at_fpr_0_1pct" not in pvp or pvp["test_cheat_windows"] == 0


def test_a_sequence_candidate_downloads_the_tokens_and_trains(tmp_path, tables):
    env = with_features(tmp_path, tables)
    params = {
        **QUICK,
        "candidates": {"cnn1d": {"epochs": 1, "batch_size": 16}},
        "game_context": "pvp",
    }

    train(make_context("train", **params), env)

    assert (env.scratch / "features" / "window_tokens").is_dir()
    assert env.api.models[0]["candidate_type"] == "cnn1d_calibrated"


def test_tokens_are_not_downloaded_for_aggregate_candidates(tmp_path, tables):
    env = with_features(tmp_path, tables)

    evaluate(make_context("evaluate", **QUICK), env)

    assert not (env.scratch / "features" / "window_tokens").exists()


def test_without_a_feature_build_the_job_fails_and_says_what_to_do(tmp_path, tables):
    env = with_features(tmp_path, tables, build=False)

    code = run_job(make_context("train", **QUICK), env, train)

    assert code == 1
    assert env.api.statuses == ["running", "failed"]
    assert "run build_features first" in env.api.updates[-1]["message"]
    assert env.api.models == []


def test_unknown_candidates_and_params_fail_the_job(tmp_path, tables):
    env = with_features(tmp_path, tables)

    with pytest.raises(JobError, match="unknown candidates"):
        evaluate(make_context("evaluate", candidates=["nope"]), env)
    with pytest.raises(ValueError, match="unknown params"):
        evaluate(make_context("evaluate", **QUICK, learning_rate=1), env)


def test_a_rejected_registration_fails_the_job(tmp_path, tables):
    env = with_features(tmp_path, tables)
    env.api.fail_on["register"] = RuntimeError("409 this model name and version already exist")

    code = run_job(make_context("train", game_context="pvp", **QUICK), env, train)

    assert code == 1
    assert env.api.statuses[-1] == "failed"
    assert "already exist" in env.api.updates[-1]["message"]


def test_calibration_none_is_not_available_to_jobs(tmp_path, tables):
    env = with_features(tmp_path, tables)

    with pytest.raises(JobError, match="calibrate"):
        train(make_context("train", calibration="none", **QUICK), env)


# --- helpers ------------------------------------------------------------------------------


def test_metric_names_follow_the_gate():
    assert metric_key("recall@fpr0.1pct") == "recall_at_fpr_0_1pct"
    assert metric_key("recall@fpr1pct") == "recall_at_fpr_1pct"
    assert metric_key("recall@fpr0.1pct_player_max") == "recall_at_fpr_0_1pct_player_max"


def test_registry_metrics_drop_non_finite_values():
    metrics = registry_metrics(
        {"test": {"roc_auc": float("nan"), "pr_auc": 0.5}, "bench": {"latency_p99_ms": 1.5}}
    )

    assert metrics == {"pr_auc": 0.5, "p99_latency_ms": 1.5}


def test_the_best_candidate_by_the_selection_metric_wins_and_ties_prefer_low_latency():
    def cand(recall, p99, parity=True, status="ok"):
        return {
            "status": status,
            "test": {"recall@fpr0.1pct": recall},
            "bench": {"latency_p99_ms": p99},
            "onnx": {"parity_ok": parity},
        }

    candidates = {
        "slow": cand(0.8, 9.0),
        "fast": cand(0.8, 1.0),
        "better_but_broken": cand(0.99, 1.0, parity=False),
        "skipped": cand(1.0, 1.0, status="skipped"),
    }
    assert choose_candidate(candidates, "recall@fpr0.1pct") == "fast"
    assert choose_candidate({"x": cand(0.5, 1.0, parity=False)}, "recall@fpr0.1pct") is None


def test_a_bad_lr_fails_the_job_and_auto_is_accepted(tmp_path, tables):
    env = with_features(tmp_path, tables)

    with pytest.raises(JobError, match=r"candidates\.cnn1d\.lr"):
        evaluate(make_context("evaluate", candidates={"cnn1d": {"lr": "fast"}}), env)


def test_the_job_summary_carries_the_learning_rate_decision(tmp_path, tables):
    env = with_features(tmp_path, tables)
    params = {
        **QUICK,
        "candidates": {"cnn1d": {"epochs": 1, "batch_size": 16, "lr": "auto", "lr_steps": 10}},
        "game_context": "pvp",
    }

    outcome = evaluate(make_context("evaluate", **params), env)

    plan = outcome.result["contexts"]["pvp"]["candidates"]["cnn1d"]["training"]["lr_plan"]
    assert plan["source"] == "auto"
    assert plan["lr_find"]["curve"]


def test_an_inconclusive_range_test_is_a_warning_in_the_job_result():
    from corvus_ml.jobs.model_jobs import _training_info

    plan = {"lr_find": {"warning": "range test inconclusive: curve too flat, used fallback"}}

    info = _training_info({"training": {"lr": 0.002, "lr_plan": plan}})

    assert info["training"]["warnings"] == [plan["lr_find"]["warning"]]
