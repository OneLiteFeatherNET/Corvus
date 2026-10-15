"""The harness exports calibrated graphs: one ``score`` output in [0, 1] per candidate."""

import json

import numpy as np
import pytest

from corvus_ml.calibration import Calibration
from corvus_ml.eval import onnx_bench
from corvus_ml.eval.config import EvalConfig
from corvus_ml.eval.runner import run

CONFIG = """
name = "calibrated"

[run]
seed = 5
seq_len = 24

[synthetic]
n_legit = 14
n_cheat = 8
n_unlabelled = 0
sessions_per_player = 2
windows_per_session = 4
frames_per_window = 24
contexts = ["pvp"]

[candidates.isolation_forest]
n_estimators = 20

[candidates.gradient_boosting]
max_iter = 10

[candidates.gru_autoencoder]
epochs = 1
batch_size = 16
hidden = 8
latent = 4

[candidates.cnn1d]
epochs = 1
batch_size = 16

[benchmark]
enabled = false
"""

NAMES = ["isolation_forest", "gradient_boosting", "gru_autoencoder", "cnn1d"]


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    base = tmp_path_factory.mktemp("calibrated")
    (base / "eval.toml").write_text(CONFIG)
    report = run(EvalConfig.load(base / "eval.toml"), base / "out")
    return report["contexts"]["pvp"]["candidates"], base / "out" / "onnx" / "pvp"


def example_input(session, rows=16):
    shape = [rows if isinstance(d, str) or d is None else d for d in session.get_inputs()[0].shape]
    return np.random.default_rng(0).normal(size=shape).astype(np.float32)


@pytest.mark.parametrize("name", NAMES)
def test_exported_graph_outputs_one_score_in_the_unit_interval(result, name):
    _, onnx_dir = result
    session = onnx_bench.make_session(onnx_dir / f"{name}.onnx", 1)
    assert [o.name for o in session.get_outputs()] == ["score"]
    score = onnx_bench.run_session(session, example_input(session))[0]
    assert score.shape == (16,)
    assert score.min() >= 0.0
    assert score.max() <= 1.0


@pytest.mark.parametrize("name", NAMES)
def test_onnx_score_equals_the_python_calibrated_score(result, name):
    cands, _ = result
    assert cands[name]["onnx"]["calibrated"] is True
    assert cands[name]["onnx"]["parity_ok"], cands[name]["onnx"]


def test_unsupervised_candidates_use_the_legit_cdf_and_supervised_a_fitted_map(result):
    cands, _ = result
    assert cands["isolation_forest"]["calibration"]["method"] == "ecdf"
    assert cands["gru_autoencoder"]["calibration"]["method"] == "ecdf"
    assert cands["gradient_boosting"]["calibration"]["method"] in {"isotonic", "platt"}
    assert cands["cnn1d"]["calibration"]["method"] in {"isotonic", "platt"}


@pytest.mark.parametrize("name", NAMES)
def test_calibration_json_is_written_next_to_the_graph(result, name):
    _, onnx_dir = result
    cal = Calibration.from_dict(json.loads((onnx_dir / f"{name}.calibration.json").read_text()))
    grid = np.linspace(-5, 5, 50)
    out = cal.apply(grid)
    assert np.all(np.diff(out) >= 0)
    assert out.min() >= 0.0
    assert out.max() <= 1.0


def test_raw_graph_is_kept_for_the_quantisation_variant(result):
    cands, onnx_dir = result
    assert (onnx_dir / "cnn1d.raw.onnx").is_file()
    assert cands["cnn1d"]["onnx_raw"]["path"] == "cnn1d.raw.onnx"
