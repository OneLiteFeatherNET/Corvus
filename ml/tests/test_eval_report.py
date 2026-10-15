"""End-to-end runs on tiny synthetic data: config -> runner -> JSON + Markdown report."""

import json

import pytest

from corvus_ml.eval import cli
from corvus_ml.eval.config import EvalConfig
from corvus_ml.eval.report import to_markdown, write_report
from corvus_ml.eval.runner import run

CONFIG = """
name = "tiny"

[run]
seed = 3
seq_len = 24
learning_curve = [0.5, 1.0]

[synthetic]
n_legit = 12
n_cheat = 8
n_unlabelled = 2
sessions_per_player = 2
windows_per_session = 4
frames_per_window = 24
contexts = ["pvp", "survival"]

[candidates.isolation_forest]
n_estimators = 20

[candidates.gradient_boosting]
max_iter = 10

[candidates.gru_autoencoder]
epochs = 2
batch_size = 16
hidden = 16
latent = 8

[candidates.cnn1d]
epochs = 2
batch_size = 16

[benchmark]
warmup = 1
iters = 5
isolated = false
"""


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "eval.toml"
    path.write_text(CONFIG)
    return path


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    """One shared run; the tests below only read it."""
    base = tmp_path_factory.mktemp("shared")
    (base / "eval.toml").write_text(CONFIG)
    return run(EvalConfig.load(base / "eval.toml"), base / "out")


def test_report_has_every_candidate_in_every_game_context(report):
    assert set(report["contexts"]) == {"pvp", "survival"}
    for info in report["contexts"].values():
        assert set(info["candidates"]) == {
            "isolation_forest",
            "gradient_boosting",
            "gru_autoencoder",
            "cnn1d",
        }
        assert all(c["status"] == "ok" for c in info["candidates"].values())


def test_report_is_marked_synthetic(report):
    assert report["synthetic"] is True
    assert "SYNTHETIC DATA" in to_markdown(report)


def test_split_section_shows_disjoint_player_sets_that_add_up(report):
    for info in report["contexts"].values():
        assert sum(p["players"] for p in info["split"].values()) == info["players"]


def test_every_candidate_reports_metrics_benchmark_and_onnx_parity(report):
    for info in report["contexts"].values():
        for name, c in info["candidates"].items():
            assert {"roc_auc", "pr_auc", "recall@fpr1pct", "recall@fpr0.1pct"} <= set(c["test"])
            assert c["onnx"]["parity_ok"], f"{name}: {c['onnx']}"
            assert c["bench"]["latency_p99_ms"] >= c["bench"]["latency_p50_ms"] > 0
            assert set(c["confusion_at_val_threshold"]) == {"1pct", "0.1pct"}


def test_only_supervised_candidates_report_calibration(report):
    cands = report["contexts"]["pvp"]["candidates"]
    assert "ece" in cands["cnn1d"]["test"]
    assert "ece" in cands["gradient_boosting"]["test"]
    assert "ece" not in cands["isolation_forest"]["test"]


def test_torch_models_get_an_int8_variant_with_deltas(report):
    c = report["contexts"]["pvp"]["candidates"]
    assert "delta" in c["cnn1d"]["int8"] or "error" in c["cnn1d"]["int8"]
    assert "int8" not in c["isolation_forest"]


def test_learning_curve_lists_each_requested_fraction(report):
    curve = report["contexts"]["pvp"]["candidates"]["cnn1d"]["learning_curve"]
    assert [p["fraction"] for p in curve] == [0.5, 1.0]
    assert curve[0]["n_train_players"] <= curve[1]["n_train_players"]


def test_report_files_are_written_and_json_parses(report, tmp_path):
    js, md = write_report(report, tmp_path / "reports" / "x")
    assert json.loads(js.read_text())["name"] == "tiny"
    text = md.read_text()
    assert "game_context `pvp`" in text
    assert "isolation_forest" in text
    assert "| candidate |" in text


def test_cli_run_writes_report_and_onnx_files(config, tmp_path, capsys):
    out = tmp_path / "reports" / "smoke"
    assert cli.main(["run", "--config", str(config), "--out", str(out)]) == 0
    assert (out / "report.json").is_file()
    assert (out / "report.md").is_file()
    assert (out / "onnx" / "pvp" / "cnn1d.onnx").is_file()
    assert "wrote" in capsys.readouterr().out


def test_supervised_candidates_are_skipped_without_labels(tmp_path):
    cfg = EvalConfig.from_dict(
        {
            "run": {"seq_len": 24},
            "synthetic": {"n_legit": 0, "n_cheat": 0, "n_unlabelled": 10, "frames_per_window": 24},
            "candidates": {"gradient_boosting": {"max_iter": 5}, "isolation_forest": {}},
            "benchmark": {"enabled": False},
        }
    )
    info = run(cfg, tmp_path)["contexts"]["pvp"]["candidates"]
    assert info["gradient_boosting"]["status"] == "skipped"
    assert info["isolation_forest"]["status"] == "skipped", "no legit windows by default"


def test_assume_legit_lets_unsupervised_candidates_train_on_unlabelled_windows(tmp_path):
    cfg = EvalConfig.from_dict(
        {
            "run": {"seq_len": 24, "unlabelled": "assume_legit"},
            "synthetic": {"n_legit": 6, "n_cheat": 4, "n_unlabelled": 10, "frames_per_window": 24},
            "candidates": {"isolation_forest": {"n_estimators": 10}},
            "benchmark": {"enabled": False},
        }
    )
    rep = run(cfg, tmp_path)
    assert rep["assumed_legit_windows"] > 0
    assert rep["contexts"]["pvp"]["candidates"]["isolation_forest"]["status"] == "ok"


def test_config_rejects_unknown_keys_and_missing_candidates():
    with pytest.raises(ValueError, match="unknown"):
        EvalConfig.from_dict({"run": {"bogus": 1}, "synthetic": {}, "candidates": {"x": {}}})
    with pytest.raises(ValueError, match="candidates"):
        EvalConfig.from_dict({"synthetic": {}})
    with pytest.raises(ValueError, match="unlabelled"):
        EvalConfig.from_dict(
            {"run": {"unlabelled": "yes"}, "synthetic": {}, "candidates": {"x": {}}}
        )
