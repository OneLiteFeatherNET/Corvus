"""Candidates on the easy synthetic set. SYNTHETIC: asserts that the code separates toy cheaters
from toy legit players, not anything about real performance."""

import numpy as np
import pytest

from corvus_ml.eval import candidates, onnx_bench
from corvus_ml.eval.data import LABEL_LEGIT
from corvus_ml.eval.dataset import build_eval_data
from corvus_ml.eval.metrics import roc_auc
from corvus_ml.eval.split import group_split
from corvus_ml.eval.synthetic import SyntheticConfig, generate

SEQ_LEN = 32
PARAMS = {
    "isolation_forest": {"n_estimators": 50},
    "gradient_boosting": {"max_iter": 30},
    "gru_autoencoder": {"epochs": 8, "batch_size": 16, "lr": 3e-3, "hidden": 32, "latent": 16},
    "cnn1d": {"epochs": 8, "batch_size": 16, "lr": 3e-3},
}


@pytest.fixture(scope="module")
def dataset():
    t = generate(SyntheticConfig(seed=11, frames_per_window=SEQ_LEN))
    data = build_eval_data(t.windows, t.tokens, t.sessions, t.labels, SEQ_LEN)
    split = group_split(data.label, data.group_codes(), seed=11)
    return data, split


def training_sets(cand, data, split):
    def pick(idx):
        keep = data.label[idx] == LABEL_LEGIT if not cand.supervised else slice(None)
        return data.take(idx[keep])

    return pick(split.train), pick(split.val)


@pytest.fixture(scope="module", params=sorted(PARAMS))
def fitted(request, dataset, tmp_path_factory):
    data, split = dataset
    cand = candidates.create(request.param, seed=11, threads=1, params=PARAMS[request.param])
    train, val = training_sets(cand, data, split)
    cand.fit(train, val)
    return cand, data.take(split.test), tmp_path_factory.mktemp(request.param)


def test_registry_holds_the_four_baseline_candidates():
    assert set(candidates.REGISTRY) == set(PARAMS)


def test_candidate_separates_synthetic_cheaters_better_than_chance(fitted):
    cand, test, _ = fitted
    auc = roc_auc((test.label == 1).astype(int), cand.score(test))
    assert auc > 0.8, f"{cand.name} AUC {auc:.3f} on the easy synthetic set"


def test_scores_are_one_finite_float_per_window(fitted):
    cand, test, _ = fitted
    s = cand.score(test)
    assert s.shape == (len(test),)
    assert np.isfinite(s).all()


def test_scoring_is_repeatable(fitted):
    cand, test, _ = fitted
    assert np.array_equal(cand.score(test), cand.score(test))


def test_param_count_is_positive_and_torch_models_stay_below_one_million(fitted):
    cand, _, _ = fitted
    assert cand.n_params > 0
    if cand.quantizable:
        assert cand.n_params < 1_000_000


def test_onnx_output_matches_the_native_model(fitted):
    cand, test, tmp = fitted
    x = cand.inputs(test)
    path = cand.export_onnx(tmp / f"{cand.name}.onnx", x)
    sess = onnx_bench.make_session(path, threads=1)
    got = cand.onnx_to_score(onnx_bench.run_session(sess, x))
    np.testing.assert_allclose(got, cand.score(test), rtol=1e-3, atol=1e-4)


def test_onnx_model_accepts_a_single_window_batch(fitted):
    cand, test, tmp = fitted
    x = cand.inputs(test)
    path = cand.export_onnx(tmp / f"{cand.name}-one.onnx", x)
    sess = onnx_bench.make_session(path, threads=1)
    one = cand.onnx_to_score(onnx_bench.run_session(sess, x[:1]))
    assert one.shape == (1,)


def test_training_is_deterministic_for_a_seed(dataset):
    data, split = dataset
    scores = []
    for _ in range(2):
        cand = candidates.create(
            "cnn1d", seed=5, threads=1, params={**PARAMS["cnn1d"], "epochs": 2}
        )
        train, val = training_sets(cand, data, split)
        cand.fit(train, val)
        scores.append(cand.score(data.take(split.test)))
    np.testing.assert_array_equal(scores[0], scores[1])


def test_quantized_torch_model_stays_close_to_fp32(fitted):
    cand, test, tmp = fitted
    if not cand.quantizable:
        pytest.skip("int8 variant exists for torch models only")
    x = cand.inputs(test)
    path = cand.export_onnx(tmp / f"{cand.name}-q.onnx", x)
    q = onnx_bench.quantize_int8(path, tmp / f"{cand.name}.int8.onnx")
    got = cand.onnx_to_score(onnx_bench.run_session(onnx_bench.make_session(q, 1), x))
    y = (test.label == 1).astype(int)
    assert abs(roc_auc(y, got) - roc_auc(y, cand.score(test))) < 0.1


def test_lr_auto_runs_the_range_test_and_records_the_choice(dataset):
    data, split = dataset
    cand = candidates.create(
        "cnn1d", seed=1, threads=1, params={"epochs": 1, "batch_size": 32, "lr": "auto"}
    )
    train, val = training_sets(cand, data, split)
    cand.fit(train, val)
    assert cand.lr_range is not None
    assert cand.lr_used == cand.lr_range.suggested_lr
