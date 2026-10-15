"""Candidate interface and registry.

Adding a candidate is one class: subclass :class:`Candidate`, set the class attributes, implement
``fit`` / ``score`` / ``export_onnx`` / ``n_params`` and decorate it with :func:`register`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar, Literal

import numpy as np

from corvus_ml.eval.data import EvalData

REGISTRY: dict[str, type[Candidate]] = {}


def register(cls: type[Candidate]) -> type[Candidate]:
    REGISTRY[cls.name] = cls
    return cls


class Candidate(ABC):
    name: ClassVar[str]
    supervised: ClassVar[bool]
    input_kind: ClassVar[Literal["agg", "seq"]]
    # True for torch models (they get the int8 dynamic-quantization variant).
    quantizable: ClassVar[bool] = False

    def __init__(self, seed: int = 0, threads: int = 1, **params):
        self.seed = seed
        self.threads = threads
        self.params = params

    def inputs(self, data: EvalData) -> np.ndarray:
        """The float32 array fed to both the native model and the ONNX graph."""
        arr = data.agg if self.input_kind == "agg" else data.seq
        if arr is None:
            raise ValueError(f"{self.name} needs {self.input_kind} inputs")
        return np.ascontiguousarray(arr, dtype=np.float32)

    @abstractmethod
    def fit(self, train: EvalData, val: EvalData | None = None) -> None:
        """Fit on ``train``. Supervised candidates get only labelled windows; unsupervised ones
        get windows that are legit (or assumed legit). ``val`` is used for early stopping."""

    @abstractmethod
    def score(self, data: EvalData) -> np.ndarray:
        """Float64 score per window, higher = more likely cheat."""

    @abstractmethod
    def export_onnx(self, path: Path, example: np.ndarray) -> Path:
        """Write the ONNX graph. Input name ``x`` (batch axis dynamic), output index 0 or the
        output named ``score`` is turned into the score by :meth:`onnx_to_score`."""

    def onnx_to_score(self, outputs: list[np.ndarray]) -> np.ndarray:
        return np.asarray(outputs[0], dtype=np.float64).reshape(len(outputs[0]), -1)[:, 0]

    @property
    @abstractmethod
    def n_params(self) -> int:
        """Learnable parameters (trees: total node count)."""


def create(name: str, seed: int, threads: int, params: dict) -> Candidate:
    if name not in REGISTRY:
        raise KeyError(f"unknown candidate {name!r}; known: {sorted(REGISTRY)}")
    return REGISTRY[name](seed=seed, threads=threads, **params)
