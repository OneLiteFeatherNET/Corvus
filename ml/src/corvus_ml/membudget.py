"""Memory-aware micro-batching for the raw-byte models.

The packet hierarchy attends over all packets of a window, so activation memory grows with the
square of ``max_packets``. A batch that fits at 1024 packets can need many GiB at 2048 and gets
the pod OOM-killed. This module estimates the activation memory of one sample, derives a budget
from the container's memory limit and caps the number of rows per forward/backward pass
("micro-batch"). Training keeps the configured batch as the optimiser batch by accumulating
gradients over the micro-batches (:func:`corvus_ml.eval.lr.accumulate_backward`).

Everything here is a pure function of numbers (or an injectable environment), so it is unit
tested without a model. Explanation and the rule of thumb: ``docs/reference/raw-byte-tokenizer.md``.
"""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from corvus_ml.cgroup import MEMORY_MAX_PATH, memory_limit, physical_memory_bytes

if TYPE_CHECKING:
    from corvus_ml.raw.model import ModelConfig

log = logging.getLogger(__name__)

BUDGET_ENV = "CORVUS_ML_MEMORY_BUDGET_MB"
# Share of the memory limit given to activations. The rest holds the window tensors, weights,
# AdamW state, the allocator's slack and the Python/Arrow/ONNX runtimes.
DEFAULT_FRACTION = 0.5

FLOAT_BYTES = 4
# Attention matrices kept for the backward pass per layer: scores, scores + mask bias, softmax,
# and its dropout output.
ATTENTION_COPIES_TRAIN = 4
# Without autograd only the tensors of one layer are alive: scores, biased scores, softmax.
ATTENTION_COPIES_INFER = 3
# Other saved activations, in multiples of one (rows x width) float32 tensor per layer, before the
# ``2 * ffn_mult`` of the feed-forward block: qkv (3), attention output, residuals, norms ...
LOCAL_ACTIVATIONS = 6
GLOBAL_ACTIVATIONS = 8
# Logits, their log-softmax and the gradient of the masked-token head: ``max_masked * vocab`` each.
LOGIT_COPIES = 3


def activation_bytes_per_sample(cfg: ModelConfig, packets: int, *, training: bool) -> int:
    """Estimated peak activation bytes of one window (float32).

    With ``P`` packets, ``T = max_tokens``, ``d = d_model``, ``dl = local_dim`` and ``F = 4``::

        global_att = heads * (P + 1)^2 * F                  # packet transformer, per layer
        local_att  = local_heads * T^2 * P * F              # byte transformer (0 for cnn)
        tokens     = P * T * dl * F
        training   = 4 * (layers * global_att + local_layers * local_att)
                     + local_layers * (6 + 2 * ffn_mult) * tokens
                     + layers * (8 + 2 * ffn_mult) * (P + 1) * d * F
                     + 3 * max_masked * vocab * F           # masked-token logits
        inference  = 3 * max(global_att, local_att) + (6 + 2 * ffn_mult) * tokens

    The JEPA objectives add one inference pass (the EMA target); macro windows multiply the cost
    by ``macro_windows``. The constants were fitted against measured peak RSS (see
    ``scripts/measure_raw_memory.py``) and err on the high side: the estimate was 1.2 - 1.4 times
    the measurement at ``P = 2048``.
    """
    p, t, f = int(packets), cfg.max_tokens, FLOAT_BYTES
    global_att = cfg.heads * (p + 1) ** 2 * f
    local_att = cfg.local_heads * t * t * p * f if cfg.local_type == "transformer" else 0
    tokens = p * t * cfg.local_dim * f
    infer = (
        ATTENTION_COPIES_INFER * max(global_att, local_att)
        + (LOCAL_ACTIVATIONS + 2 * cfg.ffn_mult) * tokens
    )
    if not training:
        return cfg.macro_windows * infer
    total = (
        ATTENTION_COPIES_TRAIN * (cfg.layers * global_att + cfg.local_layers * local_att)
        + cfg.local_layers * (LOCAL_ACTIVATIONS + 2 * cfg.ffn_mult) * tokens
        + cfg.layers * (GLOBAL_ACTIVATIONS + 2 * cfg.ffn_mult) * (p + 1) * cfg.d_model * f
        + LOGIT_COPIES * cfg.max_masked * cfg.vocab_size * f
    )
    if cfg.objective != "reconstruct":
        total += infer
    return cfg.macro_windows * total


def memory_budget_bytes(
    budget_mb: float | None = None,
    *,
    limit: int | None = None,
    env: Mapping[str, str] | None = None,
    fraction: float = DEFAULT_FRACTION,
) -> int:
    """Activation budget in bytes.

    Order: the ``budget_mb`` argument (candidate parameter ``memory_budget_mb``), the
    ``CORVUS_ML_MEMORY_BUDGET_MB`` environment variable, then ``fraction`` of the cgroup memory
    limit (``limit`` overrides the lookup), then of the installed RAM.
    """
    env = os.environ if env is None else env
    if budget_mb is not None:
        if not budget_mb > 0:
            raise ValueError(f"memory_budget_mb must be positive, got {budget_mb!r}")
        return int(budget_mb * 1024 * 1024)
    if env.get(BUDGET_ENV):
        try:
            mb = float(env[BUDGET_ENV])
        except ValueError:
            raise ValueError(
                f"{BUDGET_ENV} must be a number of MiB, got {env[BUDGET_ENV]!r}"
            ) from None
        if not mb > 0:
            raise ValueError(f"{BUDGET_ENV} must be positive, got {env[BUDGET_ENV]!r}")
        return int(mb * 1024 * 1024)
    if limit is None:
        limit = memory_limit(MEMORY_MAX_PATH)
    if limit is None:
        limit = physical_memory_bytes()
    if limit is None:
        return 2**62  # nothing known: do not cap
    return int(limit * fraction)


def micro_batch_size(batch: int, per_sample_bytes: int, budget_bytes: int) -> int:
    """Largest number of rows (at least 1, at most ``batch``) whose estimate fits the budget."""
    if per_sample_bytes <= 0:
        return max(1, batch)
    return max(1, min(batch, budget_bytes // per_sample_bytes))


@dataclass(frozen=True)
class MicroBatchPlan:
    batch: int  # rows per optimiser step (what the user configured)
    micro: int  # rows per forward/backward pass
    per_sample_bytes: int
    budget_bytes: int

    @property
    def steps(self) -> int:
        """Forward/backward passes per optimiser step."""
        return math.ceil(self.batch / self.micro)

    @property
    def reduced(self) -> bool:
        return self.micro < self.batch


def plan_micro_batch(
    cfg: ModelConfig,
    packets: int,
    batch: int,
    *,
    training: bool,
    budget_mb: float | None = None,
    limit: int | None = None,
    env: Mapping[str, str] | None = None,
    what: str = "training",
) -> MicroBatchPlan:
    """Micro-batch for ``batch`` windows of ``packets`` packets; logs when it is below ``batch``."""
    per_sample = activation_bytes_per_sample(cfg, packets, training=training)
    budget = memory_budget_bytes(budget_mb, limit=limit, env=env)
    plan = MicroBatchPlan(batch, micro_batch_size(batch, per_sample, budget), per_sample, budget)
    if plan.reduced:
        log.warning(
            "%s: micro-batch %d instead of %d (%d passes per step): about %.0f MiB per window at "
            "%d packets, budget %.0f MiB (set memory_budget_mb or %s to change)",
            what, plan.micro, batch, plan.steps, per_sample / 2**20, packets, budget / 2**20,
            BUDGET_ENV,
        )  # fmt: skip
    return plan
