"""Raw-byte models in the scorer: window container, frame mapping, batching and the latency guard.

The windowing itself is ``corvus_ml.raw.streaming`` (the same code that cuts the training
windows); this module only adapts it to the capture stream and to the micro-batch. Payloads reach
the scorer as plaintext frames; they are tokenized on arrival and only token ids are kept, for at
most one window (see ``SegmentWindower``). Nothing here persists or logs payload bytes.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from corvus_ml.raw.streaming import RawFrame


@dataclass(frozen=True)
class RawWindow:
    """A completed raw-byte window: the model input of one window (or macro window).

    ``x`` is ``(packets, T + 1)`` or ``(macro, packets, T + 1)``; it holds token ids and packet
    times, never payload bytes. Quacks like ``CompletedWindow`` for what the scorer records.
    """

    session_id: bytes
    segment: int
    start_offset_nanos: int
    end_offset_nanos: int
    x: np.ndarray

    @property
    def n_packets(self) -> int:
        return int(self.x.shape[-2])


def raw_frame(frame: Any) -> RawFrame:
    """Capture proto ``Frame`` to the windower's frame; the payload is plaintext at this point."""
    payload = bytes(frame.payload) if frame.payload else None
    return RawFrame(
        seq=frame.seq,
        offset_nanos=frame.offset_nanos,
        direction=frame.direction,
        packet_type=frame.packet_type,
        payload=payload,
        payload_original_size=frame.payload_original_size,
    )


def stack_inputs(windows: Sequence[RawWindow]) -> np.ndarray:
    """Pad the windows to the longest packet count of the micro-batch and stack them.

    Padding packets are all zero (``[PAD]`` ids, time 0), which the model masks; the output of a
    window does not depend on how much padding follows it.
    """
    xs = [w.x for w in windows]
    p_max = max(x.shape[-2] for x in xs)
    first = xs[0]
    out = np.zeros((len(xs), *first.shape[:-2], p_max, first.shape[-1]), dtype=np.float32)
    for i, x in enumerate(xs):
        out[i, ..., : x.shape[-2], :] = x
    return out


# --- latency guard -----------------------------------------------------------------------------

# Measured with default-size models (d_model 64, 2 global layers), ONNX Runtime CPU, one window per
# call, one intra-op thread (docs/reference/raw-byte-tokenizer.md section 10): (packets, ms).
_CLASSIFIER_1T_MS = ((128, 3.8), (512, 17.0), (1024, 41.9))
_TWO_THREAD_FACTOR = 0.6  # 2.3/3.8, 10.0/17.0, 25.6/41.9; not extrapolated beyond two threads
_ANOMALY_FACTOR = 2.6  # the prediction heads of raw_hier_anomaly: 10.0/3.8, 44.7/17.0, 108.7/41.9
_P99_OVER_MEAN = 1.15  # 47.6 / 41.9


def estimate_p99_ms(max_packets: int, intra_op_threads: int, *, anomaly: bool = False) -> float:
    """Rough p99 latency of one window: the mean estimate times the measured tail factor."""
    return estimate_mean_ms(max_packets, intra_op_threads, anomaly=anomaly) * _P99_OVER_MEAN


def estimate_mean_ms(max_packets: int, intra_op_threads: int, *, anomaly: bool = False) -> float:
    """Rough mean latency of one window of ``max_packets`` packets for a default-size model.

    Piecewise linear through the measured points (proportional below the first, last slope above
    the last). A larger model needs more (``d_model`` 128 with 4 layers: about 1.6 times).
    """
    pts = _CLASSIFIER_1T_MS
    if max_packets <= pts[0][0]:
        ms = pts[0][1] * max_packets / pts[0][0]
    else:
        # the segment containing max_packets; above the last point the last segment continues
        (x0, y0), (x1, y1) = next(
            ((a, b) for a, b in zip(pts, pts[1:], strict=False) if max_packets <= b[0]),
            (pts[-2], pts[-1]),
        )
        ms = y0 + (y1 - y0) * (max_packets - x0) / (x1 - x0)
    if intra_op_threads > 1:
        ms *= _TWO_THREAD_FACTOR
    if anomaly:
        ms *= _ANOMALY_FACTOR
    return ms


def estimate_capacity_players(
    cpus: int, intra_op_threads: int, stride_seconds: float, mean_ms: float
) -> float:
    """Concurrent players one scorer replica can keep up with.

    Each player yields one window per ``stride_seconds``; a window occupies ``intra_op_threads``
    cores for ``mean_ms``. So ``max(1, cpus // intra_op_threads)`` windows run in parallel and
    capacity = parallel * stride_seconds / (mean_ms / 1000). With one thread per window this is
    ``cpus * stride_seconds / mean_latency``.
    """
    if mean_ms <= 0:
        return float("inf")
    parallel = max(1, cpus // max(1, intra_op_threads))
    return parallel * stride_seconds * 1000.0 / mean_ms


def budget_warning(
    name: str, max_packets: int, intra_op_threads: int, budget_ms: float | None, *, anomaly: bool
) -> str | None:
    """A message if the estimated p99 of a full window exceeds the budget, else None."""
    if budget_ms is None:
        return None
    est = estimate_p99_ms(max_packets, intra_op_threads, anomaly=anomaly)
    if est <= budget_ms:
        return None
    return (
        f"raw-byte model {name}: max_packets={max_packets} costs an estimated p99 of {est:.1f} ms "
        f"per full window on {intra_op_threads} thread(s), above the budget (sanity bound) of "
        f"{budget_ms:.1f} ms; "
        "retrain with a smaller max_packets or raise CORVUS_SCORE_P99_BUDGET_MS"
    )
