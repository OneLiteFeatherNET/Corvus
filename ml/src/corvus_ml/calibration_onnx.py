"""Bake a :class:`~corvus_ml.calibration.Calibration` into an exported ONNX graph.

The result has one output named ``score`` (float32, shape ``[batch]``) in [0, 1], so a runtime
needs neither the candidate type nor a sidecar file to interpret it. Piecewise-linear maps are
evaluated with standard operators (a comparison against the inner knots finds the segment, then a
gathered slope and intercept give the value); a sigmoid map is ``Sigmoid(a * s + b)``.
"""

from __future__ import annotations

from typing import Literal

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper

from corvus_ml.calibration import Calibration

# How the raw score is read from the original graph outputs:
#   first       output 0, flattened (torch models)
#   neg_second  minus output 1, flattened (IsolationForest: skl2onnx returns decision_function)
#   second_col1 column 1 of output 1 (probabilities of a scikit-learn classifier)
RawOutput = Literal["first", "neg_second", "second_col1"]

SCORE_OUTPUT = "score"
_MIN_OPSET = 13  # tensor-valued Clip/ReduceSum axes and Unsqueeze axes


def _const(name: str, values: np.ndarray) -> TensorProto:
    return numpy_helper.from_array(values, name)


def _default_opset(model: onnx.ModelProto) -> int:
    for entry in model.opset_import:
        if entry.domain in ("", "ai.onnx"):
            return int(entry.version)
    return 1


def _rename_outputs(graph: onnx.GraphProto, prefix: str) -> list[str]:
    """Prefix the names of the graph outputs (nodes and consumers) and return the new names."""
    old = [o.name for o in graph.output]
    mapping = {n: f"{prefix}{n}" for n in old}
    for node in graph.node:
        node.input[:] = [mapping.get(n, n) for n in node.input]
        node.output[:] = [mapping.get(n, n) for n in node.output]
    return [mapping[n] for n in old]


def _raw_score(raw_names: list[str], how: RawOutput, nodes: list, inits: list) -> str:
    inits.append(_const("cal_flat", np.array([-1], dtype=np.int64)))
    if how == "first":
        nodes.append(helper.make_node("Reshape", [raw_names[0], "cal_flat"], ["cal_raw"]))
    elif how == "neg_second":
        nodes.append(helper.make_node("Neg", [raw_names[1]], ["cal_neg"]))
        nodes.append(helper.make_node("Reshape", ["cal_neg", "cal_flat"], ["cal_raw"]))
    elif how == "second_col1":
        inits.append(_const("cal_col", np.array(1, dtype=np.int64)))
        nodes.append(helper.make_node("Gather", [raw_names[1], "cal_col"], ["cal_raw"], axis=1))
    else:
        raise ValueError(f"unknown raw output kind {how!r}")
    return "cal_raw"


def _piecewise(cal: Calibration, raw: str, nodes: list, inits: list) -> str:
    xs = np.asarray(cal.xs, dtype=np.float64)
    ys = np.asarray(cal.ys, dtype=np.float64)
    f32 = np.float32
    if len(xs) == 1:
        inits += [_const("cal_zero", np.array(0.0, f32)), _const("cal_y0", np.array(ys[0], f32))]
        nodes.append(helper.make_node("Mul", [raw, "cal_zero"], ["cal_z"]))
        nodes.append(helper.make_node("Add", ["cal_z", "cal_y0"], ["cal_out"]))
        return "cal_out"
    inits += [
        _const("cal_lo", np.array(xs[0], f32)),
        _const("cal_hi", np.array(xs[-1], f32)),
    ]
    nodes.append(helper.make_node("Clip", [raw, "cal_lo", "cal_hi"], ["cal_sc"]))
    slopes = np.diff(ys) / np.diff(xs)
    if len(xs) == 2:
        inits += [
            _const("cal_x0", np.array(xs[0], f32)),
            _const("cal_y0", np.array(ys[0], f32)),
            _const("cal_slope", np.array(slopes[0], f32)),
        ]
        nodes.append(helper.make_node("Sub", ["cal_sc", "cal_x0"], ["cal_d"]))
        nodes.append(helper.make_node("Mul", ["cal_d", "cal_slope"], ["cal_s"]))
        nodes.append(helper.make_node("Add", ["cal_y0", "cal_s"], ["cal_out"]))
        return "cal_out"
    inits += [
        _const("cal_inner", xs[1:-1].astype(f32)),
        _const("cal_xs", xs[:-1].astype(f32)),
        _const("cal_ys", ys[:-1].astype(f32)),
        _const("cal_slopes", slopes.astype(f32)),
        _const("cal_axis1", np.array([1], dtype=np.int64)),
    ]
    nodes += [
        helper.make_node("Unsqueeze", ["cal_sc", "cal_axis1"], ["cal_col2"]),
        helper.make_node("GreaterOrEqual", ["cal_col2", "cal_inner"], ["cal_ge"]),
        helper.make_node("Cast", ["cal_ge"], ["cal_ge_i"], to=TensorProto.INT64),
        helper.make_node("ReduceSum", ["cal_ge_i", "cal_axis1"], ["cal_idx"], keepdims=0),
        helper.make_node("Gather", ["cal_xs", "cal_idx"], ["cal_x0"]),
        helper.make_node("Gather", ["cal_ys", "cal_idx"], ["cal_y0"]),
        helper.make_node("Gather", ["cal_slopes", "cal_idx"], ["cal_slope"]),
        helper.make_node("Sub", ["cal_sc", "cal_x0"], ["cal_d"]),
        helper.make_node("Mul", ["cal_d", "cal_slope"], ["cal_s"]),
        helper.make_node("Add", ["cal_y0", "cal_s"], ["cal_out"]),
    ]
    return "cal_out"


def _sigmoid(cal: Calibration, raw: str, nodes: list, inits: list) -> str:
    inits += [
        _const("cal_a", np.array(cal.a, np.float32)),
        _const("cal_b", np.array(cal.b, np.float32)),
    ]
    nodes.append(helper.make_node("Mul", [raw, "cal_a"], ["cal_az"]))
    nodes.append(helper.make_node("Add", ["cal_az", "cal_b"], ["cal_z"]))
    nodes.append(helper.make_node("Sigmoid", ["cal_z"], ["cal_out"]))
    return "cal_out"


def embed_calibration(
    model: onnx.ModelProto, raw_output: RawOutput, calibration: Calibration
) -> onnx.ModelProto:
    """A copy of ``model`` whose only output ``score`` is the calibrated score in [0, 1]."""
    if _default_opset(model) < _MIN_OPSET:
        raise ValueError(f"the model uses opset {_default_opset(model)}, need >= {_MIN_OPSET}")
    out = onnx.ModelProto()
    out.CopyFrom(model)
    graph = out.graph
    raw_names = _rename_outputs(graph, "raw_")
    nodes: list[onnx.NodeProto] = []
    inits: list[TensorProto] = []
    raw = _raw_score(raw_names, raw_output, nodes, inits)
    if calibration.kind == "sigmoid":
        value = _sigmoid(calibration, raw, nodes, inits)
    else:
        value = _piecewise(calibration, raw, nodes, inits)
        inits += [_const("cal_one", np.array(1.0, np.float32))]
        inits += [_const("cal_nil", np.array(0.0, np.float32))]
        nodes.append(helper.make_node("Clip", [value, "cal_nil", "cal_one"], ["cal_clipped"]))
        value = "cal_clipped"
    nodes.append(helper.make_node("Identity", [value], [SCORE_OUTPUT]))
    graph.node.extend(nodes)
    graph.initializer.extend(inits)
    del graph.output[:]
    graph.output.append(helper.make_tensor_value_info(SCORE_OUTPUT, TensorProto.FLOAT, ["batch"]))
    onnx.checker.check_model(out)
    return out


def embed_calibration_file(
    src: str, dst: str, raw_output: RawOutput, calibration: Calibration
) -> str:
    """Load ``src``, embed the calibration and save the result to ``dst``."""
    model = embed_calibration(onnx.load(src), raw_output, calibration)
    onnx.save(model, dst)
    return dst
