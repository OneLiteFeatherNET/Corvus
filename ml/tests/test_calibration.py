"""Calibration maps raw scores to monotone, bounded scores, in Python and embedded in ONNX."""

import numpy as np
import onnx
import pytest
from onnx import TensorProto, helper

from corvus_ml import calibration as cal
from corvus_ml.calibration import Calibration
from corvus_ml.calibration_onnx import embed_calibration
from corvus_ml.eval import onnx_bench

GRID = np.linspace(-6.0, 6.0, 401)


def labelled_scores(n=600, seed=3):
    rng = np.random.default_rng(seed)
    y = (rng.random(n) < 0.3).astype(np.int64)
    raw = rng.normal(loc=y * 1.5, scale=1.0)
    return raw, y


def assert_monotone_and_bounded(c: Calibration):
    out = c.apply(np.concatenate([[-1e9], GRID, [1e9]]))
    assert out.min() >= 0.0, "calibrated scores leave [0, 1]"
    assert out.max() <= 1.0, "calibrated scores leave [0, 1]"
    assert np.all(np.diff(out) >= -1e-12), "calibration must not reorder scores"


def test_isotonic_is_monotone_bounded_and_close_to_the_label_rate():
    raw, y = labelled_scores()
    c = cal.fit_isotonic(raw, y)
    assert_monotone_and_bounded(c)
    assert c.method == "isotonic"
    assert abs(c.apply(raw).mean() - y.mean()) < 0.02


def test_isotonic_keeps_at_most_the_knot_limit():
    rng = np.random.default_rng(1)
    raw = rng.normal(size=5000)
    y = (rng.random(5000) < 1 / (1 + np.exp(-raw))).astype(np.int64)
    c = cal.fit_isotonic(raw, y)
    assert len(c.xs) <= cal.MAX_KNOTS
    assert_monotone_and_bounded(c)


def test_platt_is_monotone_bounded_and_recovers_the_slope():
    rng = np.random.default_rng(5)
    raw = rng.normal(size=4000) * 5 + 100  # offset and scale exercise the standardisation
    y = (rng.random(4000) < 1 / (1 + np.exp(-(raw - 100) / 2))).astype(np.int64)
    c = cal.fit_platt(raw, y)
    assert c is not None
    assert c.kind == "sigmoid"
    assert c.a == pytest.approx(0.5, rel=0.25)
    out = c.apply(np.linspace(80, 120, 100))
    assert out.min() >= 0.0
    assert out.max() <= 1.0
    assert np.all(np.diff(out) >= 0)


def test_platt_refuses_a_decreasing_fit():
    raw, y = labelled_scores()
    assert cal.fit_platt(-raw, y) is None


def test_ecdf_score_is_the_share_of_legit_windows_below():
    legit = np.random.default_rng(7).normal(size=20_000)
    c = cal.fit_ecdf(legit)
    assert_monotone_and_bounded(c)
    for q in (0.5, 0.9, 0.99):
        assert float(c.apply(np.quantile(legit, q))) == pytest.approx(q, abs=0.01)
    assert c.apply(np.array([legit.max() + 10.0]))[0] == 1.0
    assert c.apply(np.array([legit.min() - 10.0]))[0] <= 0.01


def test_ecdf_handles_a_constant_score():
    c = cal.fit_ecdf(np.full(50, 0.25))
    assert c.apply(np.array([0.0, 0.25, 1.0])).tolist() == [1.0, 1.0, 1.0]
    assert_monotone_and_bounded(c)


def test_auto_picks_ecdf_for_unsupervised_and_isotonic_for_enough_labels():
    raw, y = labelled_scores()
    assert cal.fit_calibration("auto", False, raw[y == 0], None).method == "ecdf"
    assert cal.fit_calibration("auto", True, raw, y).method == "isotonic"


def test_auto_falls_back_to_platt_on_a_small_validation_split():
    raw, y = labelled_scores(n=60)
    assert cal.fit_calibration("auto", True, raw, y).method == "platt"


def test_unsupervised_calibration_falls_back_to_the_training_windows():
    c = cal.fit_calibration("auto", False, np.zeros(0), None, np.arange(100.0))
    assert (c.method, c.fit_on) == ("ecdf", "train")


def test_supervised_with_one_class_passes_probabilities_through_only_if_bounded():
    raw = np.array([0.1, 0.9, 0.5])
    y = np.zeros(3, dtype=np.int64)
    assert cal.fit_calibration("auto", True, raw, y, bounded=True).method == "identity"
    with pytest.raises(ValueError, match="both classes"):
        cal.fit_calibration("auto", True, raw, y)


def test_method_mismatch_is_rejected():
    raw, y = labelled_scores()
    with pytest.raises(ValueError, match="ecdf"):
        cal.fit_calibration("isotonic", False, raw, None)
    with pytest.raises(ValueError, match="isotonic"):
        cal.fit_calibration("ecdf", True, raw, y)


def test_round_trip_through_json_keeps_the_function():
    raw, y = labelled_scores()
    for c in (cal.fit_isotonic(raw, y), cal.fit_platt(raw, y), cal.fit_ecdf(raw[y == 0])):
        again = Calibration.from_dict(c.to_dict())
        np.testing.assert_array_equal(again.apply(GRID), c.apply(GRID))


def test_invalid_calibrations_are_rejected():
    with pytest.raises(ValueError, match="strictly increasing"):
        Calibration("x", "piecewise_linear", (0.0, 0.0), (0.0, 1.0))
    with pytest.raises(ValueError, match="non-decreasing"):
        Calibration("x", "piecewise_linear", (0.0, 1.0), (1.0, 0.0))
    with pytest.raises(ValueError, match="decreasing"):
        Calibration("x", "sigmoid", a=-1.0)


# --- ONNX embedding ------------------------------------------------------------------------


def toy_model(kind: str) -> onnx.ModelProto:
    """Input ``x`` (batch, 3); the raw score is the row sum, laid out like each candidate type.

    ``neg_second`` mimics IsolationForest: output 1 is the decision function (minus the raw score).
    """
    nodes = [helper.make_node("ReduceSum", ["x", "axes"], ["total"], keepdims=1)]
    axes = onnx.numpy_helper.from_array(np.array([1], dtype=np.int64), "axes")
    inits = [axes]
    f = TensorProto.FLOAT
    if kind == "first":
        nodes.append(helper.make_node("Identity", ["total"], ["score"]))
        outputs = [helper.make_tensor_value_info("score", f, ["batch", 1])]
    elif kind == "neg_second":
        # IsolationForest: output 1 is decision_function, the raw score is its negative.
        nodes.append(helper.make_node("Neg", ["total"], ["scores"]))
        nodes.append(helper.make_node("Identity", ["total"], ["label"]))
        outputs = [
            helper.make_tensor_value_info("label", f, ["batch", 1]),
            helper.make_tensor_value_info("scores", f, ["batch", 1]),
        ]
    else:
        # classifier: output 1 holds [P(legit), P(cheat)]
        inits.append(onnx.numpy_helper.from_array(np.array(1.0, np.float32), "one"))
        nodes.append(helper.make_node("Sigmoid", ["total"], ["p"]))
        nodes.append(helper.make_node("Sub", ["one", "p"], ["q"]))
        nodes.append(helper.make_node("Concat", ["q", "p"], ["probs"], axis=1))
        nodes.append(helper.make_node("Identity", ["p"], ["label"]))
        outputs = [
            helper.make_tensor_value_info("label", f, ["batch", 1]),
            helper.make_tensor_value_info("probs", f, ["batch", 2]),
        ]
    graph = helper.make_graph(
        nodes, "toy", [helper.make_tensor_value_info("x", f, ["batch", 3])], outputs, inits
    )
    return helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)], ir_version=10)


def run(model: onnx.ModelProto, x: np.ndarray, tmp_path) -> np.ndarray:
    path = tmp_path / "m.onnx"
    onnx.save(model, str(path))
    return onnx_bench.run_session(onnx_bench.make_session(path, 1), x)[0]


def raw_of(kind: str, x: np.ndarray) -> np.ndarray:
    total = x.sum(axis=1).astype(np.float64)
    return {"first": total, "neg_second": total, "second_col1": 1 / (1 + np.exp(-total))}[kind]


def calibrations():
    rng = np.random.default_rng(11)
    raw = rng.normal(size=800)
    y = (rng.random(800) < 1 / (1 + np.exp(-1.5 * raw))).astype(np.int64)
    return {
        "isotonic": cal.fit_isotonic(raw, y),
        "platt": cal.fit_platt(raw, y),
        "ecdf": cal.fit_ecdf(raw),
        "identity": cal.identity(),
        "one_knot": Calibration("x", "piecewise_linear", (0.5,), (0.7,)),
        "two_knots": Calibration("x", "piecewise_linear", (-1.0, 2.0), (0.1, 0.9)),
    }


@pytest.mark.parametrize("kind", ["first", "neg_second", "second_col1"])
@pytest.mark.parametrize("name", list(calibrations()))
def test_onnx_embedded_calibration_equals_the_python_calibration(kind, name, tmp_path):
    c = calibrations()[name]
    # row sums span the knots and both ends (+-50 hits the clamps); the classifier kind's raw
    # score is a probability, so its knots below are only exercised near 0 and 1
    x = np.random.default_rng(2).normal(size=(300, 3)).astype(np.float32)
    x = np.concatenate([x, np.full((2, 3), 50.0, np.float32), np.full((2, 3), -50.0, np.float32)])
    got = run(embed_calibration(toy_model(kind), kind, c), x, tmp_path)
    want = c.apply(raw_of(kind, x))
    assert got.shape == (len(x),)
    assert got.dtype == np.float32
    np.testing.assert_allclose(got, want, atol=2e-5)
    assert got.min() >= 0.0
    assert got.max() <= 1.0


def test_embedded_graph_has_a_single_score_output():
    model = embed_calibration(toy_model("neg_second"), "neg_second", cal.identity())
    assert [o.name for o in model.graph.output] == ["score"]


def test_onnx_calibration_is_monotone_in_the_raw_score(tmp_path):
    c = cal.fit_ecdf(np.random.default_rng(4).normal(size=2000))
    x = np.linspace(-5, 5, 500, dtype=np.float32).reshape(-1, 1).repeat(3, axis=1) / 3
    got = run(embed_calibration(toy_model("first"), "first", c), x, tmp_path)
    assert np.all(np.diff(got) >= 0)


def test_old_opsets_are_rejected():
    model = toy_model("first")
    model.opset_import[0].version = 11
    with pytest.raises(ValueError, match="opset"):
        embed_calibration(model, "first", cal.identity())
