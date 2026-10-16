"""Memory bound of one ONNX Runtime run for raw-byte models.

The packet hierarchy attends over all packets of a window, so the activation memory of one run
grows with the square of ``max_packets`` and linearly with the rows of the micro-batch. At 2048
packets one window needs about 0.3 GiB, so a micro-batch of 64 would ask for ~19 GiB and get the
pod OOM-killed. The scorer therefore splits a micro-batch into chunks whose estimate fits a budget
(a share of the container memory limit). Everything here is a pure function of numbers, so it is
unit tested without a model; the formula is the inference estimate of ``corvus_ml.membudget``
(see ``docs/reference/raw-byte-tokenizer.md``), evaluated for the default model architecture
because the feature spec of a model does not carry its layer sizes.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from corvus_ml.cgroup import memory_limit
from corvus_ml.membudget import (
    ATTENTION_COPIES_INFER,
    FLOAT_BYTES,
    LOCAL_ACTIVATIONS,
    memory_budget_bytes,
    micro_batch_size,
)

# Default architecture of ``corvus_ml.raw.model.ModelConfig``; a larger model needs more.
DEFAULT_HEADS = 4
DEFAULT_LOCAL_HEADS = 2
DEFAULT_LOCAL_DIM = 32
DEFAULT_FFN_MULT = 2
DEFAULT_FRACTION = 0.5


def window_inference_bytes(
    max_packets: int,
    max_tokens: int,
    macro_windows: int = 1,
    *,
    heads: int = DEFAULT_HEADS,
    local_heads: int = DEFAULT_LOCAL_HEADS,
    local_dim: int = DEFAULT_LOCAL_DIM,
    ffn_mult: int = DEFAULT_FFN_MULT,
) -> int:
    """Estimated peak ONNX Runtime activation bytes of one window (float32).

    ``3 * max(heads * (P + 1)^2 * 4, local_heads * T^2 * P * 4) + (6 + 2 * ffn_mult) * P * T *
    local_dim * 4`` for ``P`` packets and ``T`` tokens per packet, times the macro windows.
    """
    p, t, f = max_packets, max_tokens, FLOAT_BYTES
    global_att = heads * (p + 1) ** 2 * f
    local_att = local_heads * t * t * p * f
    tokens = p * t * local_dim * f
    infer = (
        ATTENTION_COPIES_INFER * max(global_att, local_att)
        + (LOCAL_ACTIVATIONS + 2 * ffn_mult) * tokens
    )
    return int(max(1, macro_windows) * infer)


def score_memory_budget(
    budget_mb: float | None = None,
    fraction: float = DEFAULT_FRACTION,
    *,
    limit: int | None = None,
) -> int:
    """Bytes one run may use: ``budget_mb`` if set, else ``fraction`` of the cgroup memory limit
    (``limit`` overrides the lookup), else of the installed RAM."""
    if limit is None:
        limit = memory_limit()
    return int(memory_budget_bytes(budget_mb, limit=limit, env={}, fraction=fraction))


def rows_per_run(per_window_bytes: int, budget_bytes: int) -> int:
    """Rows of one ONNX run that fit the budget; at least 1, since a window cannot be split."""
    return int(micro_batch_size(2**31, per_window_bytes, budget_bytes))


def chunked[T](items: Sequence[T], size: int) -> Iterator[Sequence[T]]:
    """``items`` in consecutive slices of at most ``size`` (at least 1)."""
    size = max(1, size)
    for start in range(0, len(items), size):
        yield items[start : start + size]
