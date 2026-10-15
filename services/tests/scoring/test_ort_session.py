"""The real ONNX Runtime adapter on a tiny hand-encoded graph (the ``onnx`` package is not
available without the training stack, so the protobuf is written out by hand)."""

from __future__ import annotations

import numpy as np
from corvus_ml.features.spec import FeatureSpec
from score_rig import SMALL, Rig, entry

from corvus_services.scoring.models import ModelManager, scores_from_outputs
from corvus_services.scoring.ort_session import create_session


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _field(number: int, wire: int, payload: bytes) -> bytes:
    return _varint(number << 3 | wire) + payload


def _bytes(number: int, data: bytes) -> bytes:
    return _field(number, 2, _varint(len(data)) + data)


def _int(number: int, value: int) -> bytes:
    return _field(number, 0, _varint(value))


def _value_info(name: str, dims: list[int | str]) -> bytes:
    shape = b"".join(
        _bytes(1, _int(1, d) if isinstance(d, int) else _bytes(2, d.encode())) for d in dims
    )
    tensor = _int(1, 1) + _bytes(2, shape)  # FLOAT, shape
    return _bytes(1, name.encode()) + _bytes(2, _bytes(1, tensor))


def mean_model(n_inputs: int) -> bytes:
    """``score = ReduceMean(x, axis=1)`` with ``x`` of shape (batch, n_inputs)."""
    axes = _bytes(1, b"axes") + _int(20, 7) + _field(8, 2, _varint(1) + _varint(1))
    node = _bytes(1, b"x") + _bytes(2, b"score") + _bytes(4, b"ReduceMean") + _bytes(5, axes)
    graph = (
        _bytes(1, node)
        + _bytes(2, b"mean")
        + _bytes(11, _value_info("x", ["batch", n_inputs]))
        + _bytes(12, _value_info("score", ["batch", 1]))
    )
    return _int(1, 8) + _bytes(8, _int(2, 13)) + _bytes(7, graph)


async def test_a_real_onnx_model_is_loaded_validated_and_run(tmp_path):
    spec = FeatureSpec.current(SMALL)
    rig = Rig(tmp_path)
    e = entry("m1", stage="enforce")
    rig.catalog.models.append(e)
    rig.files.put_model(e, spec)
    rig.files.objects[e.onnx_key] = mean_model(spec.n_agg_inputs)
    manager = ModelManager(rig.catalog, rig.files, create_session, tmp_path / "ort", 2, rig.metrics)

    result = await manager.sync()

    assert result.failed == {}
    (model,) = manager.current.models
    assert model.input_kind == "agg"
    assert model.session.input_shape[1] == spec.n_agg_inputs
    x = np.stack([np.full(spec.n_agg_inputs, 0.25), np.full(spec.n_agg_inputs, 2.0)])
    scores = scores_from_outputs("cnn1d", model.session.run(x.astype(np.float32)))
    assert scores.tolist() == [0.25, 1.0]


async def test_a_model_with_the_wrong_input_width_is_rejected_by_the_real_runtime(tmp_path):
    spec = FeatureSpec.current(SMALL)
    rig = Rig(tmp_path)
    e = entry("m1")
    rig.catalog.models.append(e)
    rig.files.put_model(e, spec)
    rig.files.objects[e.onnx_key] = mean_model(spec.n_agg_inputs + 3)
    manager = ModelManager(rig.catalog, rig.files, create_session, tmp_path / "ort", 2, rig.metrics)

    result = await manager.sync()

    assert "aggregates" in result.failed["m1"]
    assert len(manager.current) == 0
