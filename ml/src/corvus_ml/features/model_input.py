"""Model-input adapter: feature outputs -> the numpy tensors an ONNX graph expects.

The single place that defines the aggregate vector order, the per-column token transforms and the
token tensor layout. The evaluation harness (which reads the offline Parquet tables) and the
scoring service (which holds :class:`CompletedWindow` objects) both go through these functions,
driven by a :class:`~corvus_ml.features.spec.FeatureSpec`, so training and serving cannot drift.

Aggregate input ``(n, len(spec.agg_inputs))`` float32: the plain aggregates, ``n_frames``, the
nullable aggregates with null as 0, then one ``has_<name>`` indicator per nullable aggregate.

Token input ``(n, spec.seq_len, F + 1)`` float32 with ``F = len(spec.seq_inputs)``: the transformed
token columns of the window's frames from position 0, zero padding after the last frame (frames
beyond ``seq_len`` are cut), and a trailing mask channel that is 1 for real frames.

Normalisation (mean / std) is inside the ONNX graph, so these tensors are model-independent.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal

import numpy as np

from corvus_ml.features.constants import AGGREGATE_NAMES, NULLABLE_AGGREGATES, TOKEN_INDEX
from corvus_ml.features.featurizer import CompletedWindow
from corvus_ml.features.spec import TRANSFORMS, FeatureSpec

InputKind = Literal["agg", "seq"]


def apply_transform(values: np.ndarray, kind: str) -> np.ndarray:
    v = values.astype(np.float32, copy=False)
    if kind == "slog":
        return np.sign(v) * np.log1p(np.abs(v))
    if kind == "scale8":
        return v / np.float32(8.0)
    if kind == "id":
        return v
    raise ValueError(f"unknown transform {kind!r}; known: {TRANSFORMS}")


def _default(spec: FeatureSpec | None) -> FeatureSpec:
    return spec if spec is not None else FeatureSpec.current()


# --- aggregates --------------------------------------------------------------------------------


def aggregate_inputs(
    columns: Mapping[str, np.ndarray], spec: FeatureSpec | None = None
) -> np.ndarray:
    """Aggregate matrix from one array per column (NaN = null, including ``n_frames``)."""
    spec = _default(spec)
    cols = []
    for name in spec.agg_inputs:
        if name.startswith("has_") and name[4:] in NULLABLE_AGGREGATES:
            cols.append((~np.isnan(columns[name[4:]])).astype(np.float64))
        else:
            cols.append(np.nan_to_num(np.asarray(columns[name], dtype=np.float64), nan=0.0))
    return np.stack(cols, axis=1).astype(np.float32)


def window_aggregate_columns(windows: Sequence[CompletedWindow]) -> dict[str, np.ndarray]:
    """Columns of the aggregate vectors of the windows, plus ``n_frames``."""
    if not windows:
        cols = {name: np.zeros(0) for name in AGGREGATE_NAMES}
        return {**cols, "n_frames": np.zeros(0)}
    stacked = np.stack([w.aggregates for w in windows])
    cols = {name: stacked[:, i] for i, name in enumerate(AGGREGATE_NAMES)}
    cols["n_frames"] = np.array([w.n_frames for w in windows], dtype=np.float64)
    return cols


# --- token sequences ---------------------------------------------------------------------------


def encode_tokens(columns: Mapping[str, np.ndarray], spec: FeatureSpec | None = None) -> np.ndarray:
    """Transformed token features ``(frames, F)`` from one array per token column."""
    spec = _default(spec)
    if not spec.seq_inputs:
        return np.zeros((len(next(iter(columns.values()))), 0), dtype=np.float32)
    return np.stack(
        [apply_transform(np.asarray(columns[name]), kind) for name, kind in spec.seq_inputs],
        axis=1,
    )


def empty_sequences(n: int, spec: FeatureSpec | None = None) -> np.ndarray:
    spec = _default(spec)
    return np.zeros((n, spec.seq_len, spec.n_seq_channels), dtype=np.float32)


def scatter_frames(out: np.ndarray, rows: np.ndarray, pos: np.ndarray, encoded: np.ndarray) -> None:
    """Write encoded frames into ``out`` at (window row, frame position); sets the mask channel.

    Frames at ``pos >= out.shape[1]`` are cut.
    """
    keep = pos < out.shape[1]
    r, p = rows[keep], pos[keep]
    out[r, p, : encoded.shape[1]] = encoded[keep]
    out[r, p, encoded.shape[1]] = 1.0


def window_sequence_inputs(
    windows: Sequence[CompletedWindow], spec: FeatureSpec | None = None
) -> np.ndarray:
    spec = _default(spec)
    out = empty_sequences(len(windows), spec)
    if not windows:
        return out
    matrix = np.concatenate([w.tokens for w in windows])
    columns = {name: matrix[:, i] for name, i in TOKEN_INDEX.items()}
    sizes = np.array([w.n_frames for w in windows], dtype=np.int64)
    rows = np.repeat(np.arange(len(windows)), sizes)
    pos = np.arange(int(sizes.sum())) - np.repeat(np.cumsum(sizes) - sizes, sizes)
    scatter_frames(out, rows, pos, encode_tokens(columns, spec))
    return out


# --- entry point -------------------------------------------------------------------------------


def model_inputs(
    windows: Sequence[CompletedWindow], kind: InputKind, spec: FeatureSpec | None = None
) -> np.ndarray:
    """The float32 array a candidate of input kind ``kind`` feeds to its ONNX graph (``x``)."""
    spec = _default(spec)
    if kind == "agg":
        arr = aggregate_inputs(window_aggregate_columns(windows), spec)
    elif kind == "seq":
        arr = window_sequence_inputs(windows, spec)
    else:
        raise ValueError(f"unknown input kind {kind!r}")
    return np.ascontiguousarray(arr, dtype=np.float32)
