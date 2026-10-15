"""ONNX Runtime session factory (CPU, sequential, inter-op 1)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import onnxruntime as ort


class OrtSession:
    """An ``InferenceSession`` whose first input is ``x`` (batch axis dynamic)."""

    def __init__(self, path: Path, intra_op_threads: int) -> None:
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = max(1, intra_op_threads)
        opts.inter_op_num_threads = 1
        opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        opts.log_severity_level = 3
        self._session = ort.InferenceSession(str(path), opts, providers=["CPUExecutionProvider"])
        first = self._session.get_inputs()[0]
        self._input = first.name
        self._shape: tuple[int | str | None, ...] = tuple(first.shape)
        self._outputs = tuple(o.name for o in self._session.get_outputs())

    @property
    def output_names(self) -> tuple[str, ...]:
        return self._outputs

    @property
    def input_shape(self) -> tuple[int | str | None, ...]:
        return self._shape

    def run(self, x: np.ndarray) -> list[np.ndarray]:
        return list(self._session.run(None, {self._input: x}))


def create_session(path: Path, intra_op_threads: int) -> OrtSession:
    return OrtSession(path, intra_op_threads)
