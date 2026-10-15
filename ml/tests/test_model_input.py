"""The model-input adapter shared by the evaluation harness and the scoring service."""

import random
import subprocess
import sys

import numpy as np
import pyarrow as pa
import pytest

from corvus_ml.eval import candidates
from corvus_ml.eval.dataset import build_eval_data
from corvus_ml.features.config import FeaturizerConfig
from corvus_ml.features.constants import AGGREGATE_NAMES
from corvus_ml.features.featurizer import SessionFeaturizer
from corvus_ml.features.model_input import model_inputs
from corvus_ml.features.pipeline import window_tables
from corvus_ml.features.spec import FeatureSpec
from featurizer_builders import SID, random_run

CFG = FeaturizerConfig(window=24, overlap=0.5, max_duration_s=30.0, min_frames=4)


def featurize(frames, cfg=CFG, tick_end=None):
    fz = SessionFeaturizer(SID, cfg)
    fz.begin_segment(frames[0].segment, tick_end=tick_end)
    out = [w for fr in frames for w in fz.feed(fr)]
    return out + fz.flush()


@pytest.fixture(scope="module")
def windows():
    rng = random.Random(5)
    out = featurize(random_run(rng, 400, segment=0, start_seq=0), tick_end=True)
    return out + featurize(random_run(rng, 300, segment=1, start_seq=1000), tick_end=False)


def served_tables(windows):
    """What the offline pipeline would have written for these windows."""
    win, tok = window_tables(SID, "pvp", windows)
    sessions = pa.table(
        {"session_id": pa.array([SID], pa.binary(16)), "pseudonym": pa.array([b"p" * 32])}
    )
    return win, tok, sessions


@pytest.mark.parametrize(
    "name", ["isolation_forest", "gradient_boosting", "cnn1d", "gru_autoencoder"]
)
def test_training_and_serving_paths_build_identical_model_inputs(windows, name):
    """EvalData from the Parquet-shaped tables vs. freshly featurized CompletedWindows."""
    seq_len = 16  # shorter than the windows: exercises the cut as well
    win, tok, sessions = served_tables(windows)
    data = build_eval_data(win, tok, sessions, None, seq_len)
    cand = candidates.REGISTRY[name](seed=0, threads=1)
    spec = FeatureSpec.current(CFG, seq_len)

    trained_on = cand.inputs(data)
    served = cand.inputs_for_windows(windows, spec)

    assert served.dtype == trained_on.dtype == np.float32
    assert served.shape == trained_on.shape
    np.testing.assert_array_equal(served, trained_on)


def test_aggregate_input_order_and_null_handling(windows):
    spec = FeatureSpec.current(CFG)
    x = model_inputs(windows, "agg", spec)
    assert x.shape == (len(windows), len(spec.agg_inputs))
    col = {n: i for i, n in enumerate(spec.agg_inputs)}
    for i, w in enumerate(windows):
        assert x[i, col["cps"]] == np.float32(w.aggregate("cps"))
        assert x[i, col["n_frames"]] == w.n_frames
        for name in ("on_ground_frac", "tick_end_rate"):
            value = w.aggregate(name)
            assert x[i, col[name]] == np.float32(0.0 if value is None else value)
            assert x[i, col[f"has_{name}"]] == (0.0 if value is None else 1.0)
    assert x[:, col["has_tick_end_rate"]].min() == 0.0 < x[:, col["has_tick_end_rate"]].max()
    assert set(spec.agg_inputs) >= set(AGGREGATE_NAMES)


def test_token_tensor_pads_with_zeros_and_marks_real_frames(windows):
    spec = FeatureSpec.current(CFG, seq_len=32)
    x = model_inputs(windows, "seq", spec)
    assert x.shape == (len(windows), 32, spec.n_seq_channels)
    for i, w in enumerate(windows):
        real = min(w.n_frames, 32)
        assert x[i, :real, -1].tolist() == [1.0] * real
        assert not x[i, real:].any(), "padding is all zeros including the mask"


def test_token_tensor_cuts_frames_beyond_the_sequence_length(windows):
    full = model_inputs(windows, "seq", FeatureSpec.current(CFG, seq_len=64))
    cut = model_inputs(windows, "seq", FeatureSpec.current(CFG, seq_len=8))
    np.testing.assert_array_equal(cut, full[:, :8])


def test_token_transforms_follow_the_spec(windows):
    spec = FeatureSpec.current(CFG)
    w = windows[0]
    x = model_inputs([w], "seq", spec)[0]
    names = [n for n, _ in spec.seq_inputs]
    j = names.index("event_kind")
    assert x[0, j] == np.float32(w.tokens[0, w.token_names.index("event_kind")]) / np.float32(8)
    j = names.index("dx")
    raw = w.tokens[:, w.token_names.index("dx")]
    np.testing.assert_allclose(x[: w.n_frames, j], np.sign(raw) * np.log1p(np.abs(raw)))


def test_no_windows_give_empty_inputs():
    spec = FeatureSpec.current(seq_len=8)
    assert model_inputs([], "agg", spec).shape == (0, len(spec.agg_inputs))
    assert model_inputs([], "seq", spec).shape == (0, 8, spec.n_seq_channels)


def test_unknown_input_kind_is_rejected(windows):
    with pytest.raises(ValueError, match="input kind"):
        model_inputs(windows, "audio")  # type: ignore[arg-type]


def test_adapter_and_spec_import_without_arrow_or_ml_stacks():
    code = (
        "import sys\n"
        "import corvus_ml.features.model_input, corvus_ml.features.spec\n"
        "heavy = {'torch', 'sklearn', 'onnx', 'skl2onnx', 'onnxruntime', 'pyarrow'}\n"
        "bad = heavy & set(sys.modules)\n"
        "assert not bad, sorted(bad)\n"
    )
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
