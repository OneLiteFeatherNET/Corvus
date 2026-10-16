"""Raw-byte candidates in the evaluation harness: export parity, pretraining, macro, Optuna."""

import json

import numpy as np
import onnxruntime as ort
import pytest

from corvus_ml.eval import candidates
from corvus_ml.eval.cli import main as eval_main
from corvus_ml.eval.config import EvalConfig
from corvus_ml.eval.runner import pretrain_checkpoints, run
from corvus_ml.features.spec import FeatureSpec

FAST = {"epochs": 2, "batch_size": 16, "lr": 0.003, "d_model": 32, "heads": 2, "layers": 1}
SMALL = {"local_dim": 16, "local_heads": 2, "dropout": 0.0}


def config(candidates_table=None, *, tune=None, raw_extra=None, benchmark=None, seed=3):
    raw = {
        "window_seconds": 8.0,
        "stride_seconds": 8.0,
        "max_packets": 24,
        "min_packets": 6,
        "packet": {"max_tokens_per_packet": 16},
        "tokenizer": {"vocab_size": 600, "layout": {"packet_type_max": 64}},
        "synthetic": {
            "n_legit": 8,
            "n_cheat": 6,
            "sessions_per_player": 1,
            "seconds_per_session": 25.0,
        },
    } | (raw_extra or {})
    table = {
        "name": "raw-test",
        "run": {"seed": seed, "threads": 1, "fpr_targets": [0.05]},
        "raw": raw,
        "candidates": candidates_table
        or {
            "raw_hier_classifier": FAST | SMALL,
            "raw_hier_anomaly": FAST | SMALL,
        },
        "benchmark": benchmark
        or {"warmup": 1, "iters": 3, "batch": 2, "isolated": False, "quantize": False},
    }
    if tune is not None:
        table["tune"] = tune
    return EvalConfig.from_dict(table)


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    out = tmp_path_factory.mktemp("raw-eval")
    cfg = config(
        benchmark={"warmup": 1, "iters": 3, "batch": 2, "isolated": False, "raw_packets": [8, 40]}
    )
    return run(cfg, out), out


def candidate(report_and_out, name):
    return report_and_out[0]["contexts"]["pvp"]["candidates"][name]


@pytest.mark.parametrize("name", ["raw_hier_classifier", "raw_hier_anomaly"])
def test_exported_graph_matches_pytorch_including_the_calibration(report, name):
    res = candidate(report, name)

    assert res["status"] == "ok"
    assert res["onnx"]["calibrated"] is True
    assert res["onnx"]["parity_ok"] is True, res["onnx"]
    assert res["onnx"]["max_abs_diff"] < 2e-3
    assert res["onnx_raw"]["max_abs_diff"] < 1e-3


@pytest.mark.parametrize("name", ["raw_hier_classifier", "raw_hier_anomaly"])
def test_exported_graph_has_one_score_output_and_dynamic_batch_and_packets(report, name):
    _, out = report
    sess = ort.InferenceSession(str(out / "onnx/pvp" / f"{name}.onnx"))
    spec = FeatureSpec.load(out / "onnx/pvp").raw_bytes
    width = spec.packet.max_tokens_per_packet + 1

    assert [o.name for o in sess.get_outputs()] == ["score"]
    assert sess.get_inputs()[0].name == "x"
    small = np.zeros((1, 5, width), dtype=np.float32)
    big = np.zeros((3, 31, width), dtype=np.float32)
    small[:, :, 0] = 5  # [DIR_SB]
    big[:, :, 0] = 5
    assert sess.run(None, {"x": small})[0].shape == (1,)
    scores = sess.run(None, {"x": big})[0]
    assert scores.shape == (3,)
    assert ((scores >= 0) & (scores <= 1)).all()


def test_export_writes_the_spec_and_the_tokenizer_it_hashes(report):
    _, out = report

    spec = FeatureSpec.load(out / "onnx/pvp", require_tokenizer=True)

    assert spec.raw_bytes is not None
    assert spec.version == 2
    assert (out / "onnx/pvp/tokenizer.json").is_file()


def test_report_describes_the_raw_input_and_latency_by_packet_count(report):
    rep, _ = report
    res = candidate(report, "raw_hier_classifier")

    assert rep["raw_bytes"]["window"]["window_seconds"] == 8.0
    assert set(res["bench_sizes"]) == {"8", "40"}
    assert res["bench"]["latency_p50_ms"] > 0
    assert rep["synthetic"] is True


def test_classifier_fits_the_synthetic_cheats_it_is_trained_on(tmp_path):
    from sklearn.metrics import roc_auc_score

    from raw_builders import tiny_source

    data = tiny_source(tmp_path, n_legit=6, n_cheat=6).build()
    labelled = data.take(np.flatnonzero(data.label >= 0))
    cand = candidates.create(
        "raw_hier_classifier", seed=1, threads=1,
        params=FAST | SMALL | {"epochs": 25, "lr": 0.004, "patience": 50, "batch_size": 8},
    )  # fmt: skip

    cand.fit(labelled, None)

    auc = roc_auc_score(labelled.label, cand.score(labelled))
    assert auc > 0.85, f"training AUC {auc:.2f}: the model does not pick up the synthetic signal"


def test_pretraining_checkpoint_is_written_and_used_for_finetuning(tmp_path):
    ckpt = tmp_path / "pre.pt"
    cfg = config(
        {"raw_hier_classifier": FAST | SMALL | {"pretrain_epochs": 1, "checkpoint_out": str(ckpt)}},
        benchmark={"enabled": False},
    )

    run(cfg, tmp_path / "a")
    assert ckpt.is_file()

    again = config(
        {"raw_hier_classifier": FAST | SMALL | {"init_from": str(ckpt)}},
        benchmark={"enabled": False},
    )
    rep = run(again, tmp_path / "b")
    assert rep["contexts"]["pvp"]["candidates"]["raw_hier_classifier"]["status"] == "ok"


def test_pretrain_command_writes_one_checkpoint_per_context(tmp_path):
    cfg = config(
        {"raw_hier_classifier": FAST | SMALL | {"pretrain_epochs": 1}},
        benchmark={"enabled": False},
    )

    result = pretrain_checkpoints(cfg, tmp_path)

    assert set(result) == {"pvp"}
    assert (tmp_path / "pretrain" / "pvp.pt").is_file()
    assert result["pvp"]["windows"] > 0


def test_macro_windows_feed_the_second_level_model(tmp_path):
    cfg = config(
        {"raw_hier_classifier": FAST | SMALL | {"macro_layers": 1}},
        raw_extra={"macro_window_seconds": 16.0},
        benchmark={"enabled": False},
    )

    rep = run(cfg, tmp_path)

    res = rep["contexts"]["pvp"]["candidates"]["raw_hier_classifier"]
    assert res["status"] == "ok"
    assert res["onnx"]["parity_ok"] is True
    spec = FeatureSpec.load(tmp_path / "onnx/pvp").raw_bytes
    sess = ort.InferenceSession(str(tmp_path / "onnx/pvp/raw_hier_classifier.onnx"))
    assert len(sess.get_inputs()[0].shape) == 4
    assert spec.window.macro_windows == 2


def test_anomaly_model_refuses_macro_windows(tmp_path):
    cfg = config(
        {"raw_hier_anomaly": FAST | SMALL},
        raw_extra={"macro_window_seconds": 16.0},
        benchmark={"enabled": False},
    )

    with pytest.raises(ValueError, match="single windows"):
        run(cfg, tmp_path)


def test_unknown_candidate_parameter_is_an_error(tmp_path):
    cfg = config({"raw_hier_classifier": FAST | {"d_modell": 32}}, benchmark={"enabled": False})

    with pytest.raises(ValueError, match="d_modell"):
        run(cfg, tmp_path)


def test_raw_candidates_cannot_share_a_run_with_the_others():
    with pytest.raises(ValueError, match="own run"):
        config({"raw_hier_classifier": FAST, "isolation_forest": {}})


def test_raw_candidate_needs_a_raw_table():
    with pytest.raises(ValueError, match=r"\[raw\]"):
        EvalConfig.from_dict(
            {
                "synthetic": {"n_legit": 4},
                "candidates": {"raw_hier_classifier": {}},
            }
        )


def test_players_are_not_split_across_train_and_test(tmp_path):
    cfg = config(benchmark={"enabled": False})

    rep = run(cfg, tmp_path)

    split = rep["contexts"]["pvp"]["split"]
    assert sum(p["players"] for p in split.values()) == rep["contexts"]["pvp"]["players"]


# --- Optuna --------------------------------------------------------------------------------------


def tuned(tmp_path, **tune):
    cfg = config(
        {"raw_hier_classifier": FAST | SMALL},
        tune={"trials": 3, "storage": "memory"} | tune,
        benchmark={"enabled": False},
    )
    return run(cfg, tmp_path, tune=True)["contexts"]["pvp"]


def test_study_runs_the_requested_trials_and_reports_the_best_parameters(tmp_path):
    info = tuned(tmp_path)
    study = info["tune"]["raw_hier_classifier"]

    assert study["n_trials"] == 3
    assert study["n_complete"] + study["n_pruned"] == 3
    assert set(study["best_params"]) >= {"lr", "weight_decay", "d_model", "layers", "dropout"}
    assert set(study["best_params"]) >= {"window_seconds", "max_packets"}
    assert study["sampler"] == "TPE(seed=3)"
    assert "MedianPruner" in study["pruner"]
    assert info["candidates"]["raw_hier_classifier"]["status"] == "ok"


def test_study_is_reproducible_for_a_fixed_seed(tmp_path):
    first = tuned(tmp_path / "a")["tune"]["raw_hier_classifier"]
    second = tuned(tmp_path / "b")["tune"]["raw_hier_classifier"]

    assert first["best_params"] == second["best_params"]
    assert [t["params"] for t in first["trials"]] == [t["params"] for t in second["trials"]]
    assert [t["value"] for t in first["trials"]] == [t["value"] for t in second["trials"]]


def test_window_search_can_be_switched_off(tmp_path):
    study = tuned(tmp_path, tune_windows=False)["tune"]["raw_hier_classifier"]

    assert "window_seconds" not in study["best_params"]


def test_search_space_can_be_narrowed_in_the_config(tmp_path):
    study = tuned(tmp_path, tune_windows=False, space={"lr": [0.001, 0.002], "d_model": [32]})[
        "tune"
    ]["raw_hier_classifier"]

    assert 0.001 <= study["best_params"]["lr"] <= 0.002
    assert study["best_params"]["d_model"] == 32


def test_best_window_is_what_the_exported_model_uses(tmp_path):
    info = tuned(tmp_path, space={"window_seconds": [4.0]})
    study = info["tune"]["raw_hier_classifier"]

    spec = FeatureSpec.load(tmp_path / "onnx/pvp").raw_bytes
    assert study["best_params"]["window_seconds"] == 4.0
    assert spec.window.window_seconds == 4.0
    assert spec.window.max_packets == study["best_params"]["max_packets"]


def test_sqlite_storage_in_the_output_directory(tmp_path):
    cfg = config(
        {"raw_hier_classifier": FAST | SMALL | {"epochs": 1}},
        tune={"trials": 2, "tune_windows": False},
        benchmark={"enabled": False},
    )

    info = run(cfg, tmp_path, tune=True)["contexts"]["pvp"]

    assert (tmp_path / "tune" / "optuna.db").is_file()
    assert info["tune"]["raw_hier_classifier"]["storage"].startswith("sqlite:///")


def test_existing_torch_candidates_can_be_tuned_too(tmp_path):
    cfg = EvalConfig.from_dict(
        {
            "run": {"seed": 5, "seq_len": 24, "fpr_targets": [0.05]},
            "synthetic": {
                "n_legit": 12,
                "n_cheat": 6,
                "windows_per_session": 3,
                "frames_per_window": 24,
            },
            "candidates": {"cnn1d": {"epochs": 2, "batch_size": 32}},
            "tune": {"trials": 2, "storage": "memory"},
            "benchmark": {"enabled": False},
        }
    )

    info = run(cfg, tmp_path, tune=True)["contexts"]["pvp"]

    study = info["tune"]["cnn1d"]
    assert study["n_trials"] == 2
    assert set(study["best_params"]) == {"lr", "weight_decay", "channels"}


def test_cli_tune_command_writes_the_report_with_the_study(tmp_path, capsys):
    toml = tmp_path / "eval.toml"
    toml.write_text(
        """
name = "cli"
[run]
seed = 3
fpr_targets = [0.05]
[raw]
window_seconds = 8.0
stride_seconds = 8.0
max_packets = 24
min_packets = 6
[raw.packet]
max_tokens_per_packet = 16
[raw.tokenizer]
vocab_size = 600
[raw.tokenizer.layout]
packet_type_max = 64
[raw.synthetic]
n_legit = 8
n_cheat = 6
sessions_per_player = 1
seconds_per_session = 25.0
[candidates.raw_hier_classifier]
epochs = 1
d_model = 32
heads = 2
layers = 1
local_dim = 16
local_heads = 2
[tune]
trials = 2
storage = "memory"
tune_windows = false
[benchmark]
enabled = false
"""
    )

    code = eval_main(["tune", "--config", str(toml), "--out", str(tmp_path / "out")])

    assert code == 0
    report = json.loads((tmp_path / "out/report.json").read_text())
    assert "tune" in report["contexts"]["pvp"]
    assert "Hyper-parameter search" in (tmp_path / "out/report.md").read_text()
    capsys.readouterr()


def test_registry_has_the_raw_candidates():
    assert candidates.REGISTRY["raw_hier_classifier"].supervised is True
    assert candidates.REGISTRY["raw_hier_anomaly"].supervised is False
    assert candidates.REGISTRY["raw_hier_classifier"].input_kind == "raw_bytes"


def test_raw_candidates_cap_the_micro_batch_by_the_memory_budget_and_keep_the_batch(
    tmp_path, monkeypatch
):
    from corvus_ml.eval.candidates import raw_models
    from raw_builders import tiny_source

    seen = {}
    real_fit = raw_models.fit

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real_fit(*args, **kwargs)

    monkeypatch.setattr(raw_models, "fit", spy)
    data = tiny_source(tmp_path, n_legit=4, n_cheat=0).build()
    legit = data.take(np.flatnonzero(data.label == 0))
    params = FAST | SMALL | {"epochs": 1, "batch_size": 8, "lr": "auto", "lr_steps": 3,
                             "memory_budget_mb": 1}  # fmt: skip
    cand = candidates.create("raw_hier_anomaly", seed=1, threads=1, params=params)

    cand.fit(legit, None)

    assert seen["batch_size"] == 8, "the configured batch stays the optimiser batch"
    assert seen["micro_batch"] == 1, "a 1 MiB budget leaves room for one window"
    assert seen["lr_find"].batch_size == 8
    assert seen["lr_find"].micro_batch == 1
    assert cand.score(legit).shape == (len(legit),)
