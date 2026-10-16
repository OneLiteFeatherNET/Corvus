"""Candidates on window aggregates: IsolationForest and gradient boosting."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import onnx.helper
from skl2onnx import to_onnx
from sklearn.ensemble import HistGradientBoostingClassifier, IsolationForest
from threadpoolctl import threadpool_limits

from corvus_ml.eval.candidates.base import Candidate, register
from corvus_ml.eval.data import LABEL_CHEAT, EvalData

_OPSET = {"": 17, "ai.onnx.ml": 3}


@contextlib.contextmanager
def _int_attributes() -> Iterator[None]:
    """skl2onnx emits numpy bools in integer-list attributes (HistGradientBoosting); the
    current ``onnx`` rejects them. Convert them to ints while converting."""
    original = onnx.helper.make_attribute

    def patched(key, value, *args, **kwargs):
        if isinstance(value, (list, tuple, np.ndarray)) and any(
            isinstance(v, (bool, np.bool_)) for v in value
        ):
            value = [int(v) for v in value]
        return original(key, value, *args, **kwargs)

    onnx.helper.make_attribute = patched
    try:
        yield
    finally:
        onnx.helper.make_attribute = original


@register
class IsolationForestCandidate(Candidate):
    name = "isolation_forest"
    supervised = False
    input_kind = "agg"
    onnx_raw = "neg_second"

    def fit(self, train: EvalData, val: EvalData | None = None) -> None:
        self.model = IsolationForest(
            n_estimators=int(self.params.get("n_estimators", 200)),
            max_samples=self.params.get("max_samples", "auto"),
            contamination="auto",
            random_state=self.seed,
            n_jobs=self.n_jobs,
        ).fit(self.inputs(train))

    def score(self, data: EvalData) -> np.ndarray:
        # -decision_function = offset-free anomaly score, the same quantity the ONNX graph yields.
        return -self.model.decision_function(self.inputs(data)).astype(np.float64)

    def export_onnx(self, path: Path, example: np.ndarray) -> Path:
        onx = to_onnx(self.model, example[:1], target_opset=_OPSET)
        path.write_bytes(onx.SerializeToString())
        return path

    def onnx_to_score(self, outputs: list[np.ndarray]) -> np.ndarray:
        # outputs: label, scores (= decision_function, higher = more normal)
        return -np.asarray(outputs[1], dtype=np.float64).reshape(-1)

    @property
    def n_params(self) -> int:
        return int(sum(t.tree_.node_count for t in self.model.estimators_))


@register
class GradientBoostingCandidate(Candidate):
    name = "gradient_boosting"
    supervised = True
    input_kind = "agg"
    onnx_raw = "second_col1"
    bounded = True

    def fit(self, train: EvalData, val: EvalData | None = None) -> None:
        y = (train.label == LABEL_CHEAT).astype(int)
        model = HistGradientBoostingClassifier(
            max_iter=int(self.params.get("max_iter", 200)),
            learning_rate=float(self.params.get("learning_rate", 0.1)),
            max_depth=self.params.get("max_depth"),
            class_weight="balanced",
            early_stopping=bool(self.params.get("early_stopping", False)),
            random_state=self.seed,
        )
        # HistGradientBoosting has no n_jobs: its OpenMP pool is sized through threadpoolctl.
        with threadpool_limits(limits=self.n_jobs):
            self.model = model.fit(self.inputs(train), y)

    def score(self, data: EvalData) -> np.ndarray:
        return self.model.predict_proba(self.inputs(data))[:, 1].astype(np.float64)

    def export_onnx(self, path: Path, example: np.ndarray) -> Path:
        with _int_attributes():
            onx = to_onnx(
                self.model,
                example[:1],
                target_opset=_OPSET,
                options={id(self.model): {"zipmap": False}},
            )
        path.write_bytes(onx.SerializeToString())
        return path

    def onnx_to_score(self, outputs: list[np.ndarray]) -> np.ndarray:
        return np.asarray(outputs[1], dtype=np.float64)[:, 1]

    @property
    def n_params(self) -> int:
        return int(sum(len(p.nodes) for stage in self.model._predictors for p in stage))
