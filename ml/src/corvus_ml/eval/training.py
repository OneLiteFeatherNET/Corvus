"""Training helpers for the torch candidates: seeding, LR schedule, LR range test, fit loop."""

from __future__ import annotations

import contextlib
import copy
import math
import os
import random
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import torch

from corvus_ml.eval.lr import (
    LossFn,
    LrFindConfig,
    LrFindResult,
    LrSchedule,
    accumulate_backward,
    find_lr,
    plan_lr,
    warmup_cosine,
)


def _set_interop_threads(threads: int) -> None:
    """Inter-op pool size; torch allows it once, before parallel work, so a refusal is fine."""
    with contextlib.suppress(RuntimeError):
        torch.set_num_interop_threads(threads)


def seed_everything(seed: int, threads: int = 1) -> None:
    """Seed python, numpy and torch; set CPU threads; prefer deterministic kernels.

    Multithreaded CPU kernels can differ from a single-threaded run in the last bits of a float.
    """
    random.seed(seed)
    np.random.seed(seed)  # noqa: NPY002 - legacy global state, for libraries that use it
    torch.manual_seed(seed)
    torch.set_num_threads(max(1, threads))
    _set_interop_threads(max(1, threads))
    os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    torch.use_deterministic_algorithms(True, warn_only=True)


def lr_at(step: int, total_steps: int, warmup_steps: int, base_lr: float, min_lr: float = 0.0):
    """Linear warmup to ``base_lr`` over ``warmup_steps``, then cosine decay to ``min_lr``.

    ``lr_at(warmup_steps) == base_lr`` and ``lr_at(total_steps) == min_lr``.
    """
    return warmup_cosine(step, total_steps, warmup_steps, base_lr, min_lr)


def _batches(tensors: tuple[torch.Tensor, ...], size: int, gen: torch.Generator | None):
    n = tensors[0].shape[0]
    order = torch.randperm(n, generator=gen) if gen is not None else torch.arange(n)
    for i in range(0, n, size):
        sel = order[i : i + size]
        yield tuple(t[sel] for t in tensors)


LrRangeResult = LrFindResult  # kept for callers of the first range test


def lr_range_test(
    model: torch.nn.Module,
    loss_fn: LossFn,
    tensors: tuple[torch.Tensor, ...],
    batch_size: int = 64,
    lr_min: float = 1e-6,
    lr_max: float = 1.0,
    steps: int = 100,
    seed: int = 0,
    weight_decay: float = 0.0,
    smoothing: float = 0.9,
) -> LrFindResult:
    """Smith's LR range test on a copy of ``model`` (:func:`corvus_ml.eval.lr.find_lr`)."""
    cfg = LrFindConfig(
        lr_min=lr_min, lr_max=lr_max, steps=steps, batch_size=batch_size, seed=seed,
        weight_decay=weight_decay, beta=smoothing,
    )  # fmt: skip
    return find_lr(model, loss_fn, tensors, cfg)


@dataclass
class FitHistory:
    train_loss: list[float] = field(default_factory=list)
    val_loss: list[float] = field(default_factory=list)
    lr: list[float] = field(default_factory=list)
    best_epoch: int = -1
    stopped_early: bool = False
    lr_used: float | None = None  # peak lr of the run
    schedule: LrSchedule | None = None
    lr_find: LrFindResult | None = None  # set when lr was "auto"
    lr_plan: dict | None = None  # report form of the above (curve downsampled)


def fit(
    model: torch.nn.Module,
    loss_fn: LossFn,
    train: tuple[torch.Tensor, ...],
    val: tuple[torch.Tensor, ...] | None,
    *,
    epochs: int,
    batch_size: int,
    lr: float | str,
    weight_decay: float = 0.01,
    warmup_frac: float = 0.1,
    patience: int = 5,
    seed: int = 0,
    grad_clip: float = 1.0,
    on_epoch: Callable[[int, float], None] | None = None,
    lr_find: LrFindConfig | None = None,
    lr_mult: float = 1.0,
    schedule_kind: str = "cosine",
    lr_precomputed: LrFindResult | None = None,
    micro_batch: int | None = None,
    eval_batch: int | None = None,
) -> FitHistory:
    """AdamW + linear warmup / cosine decay + early stopping on validation loss.

    The best-validation weights are restored at the end. Without ``val`` the last epoch is kept.
    ``lr="auto"`` runs the range test first (:mod:`corvus_ml.eval.lr`), scales its suggestion by
    ``lr_mult`` and uses it as the peak of a 5 % warmup / cosine-to-10 % schedule; a number keeps
    the fixed ``warmup_frac`` / cosine-to-zero schedule.

    ``batch_size`` is the optimiser batch. ``micro_batch`` caps the rows per forward/backward pass
    (memory, :mod:`corvus_ml.membudget`): the gradients of the chunks are accumulated, so the
    update is that of the whole batch. ``eval_batch`` is the chunk size of the validation pass
    (default 1024).
    """
    plan = plan_lr(
        lr, model, loss_fn, train, epochs=epochs, batch_size=batch_size, find=lr_find,
        multiplier=lr_mult, schedule_kind=schedule_kind, legacy_warmup_frac=warmup_frac,
        precomputed=lr_precomputed,
    )  # fmt: skip
    schedule = plan.schedule
    opt = torch.optim.AdamW(model.parameters(), lr=plan.peak_lr, weight_decay=weight_decay)
    gen = torch.Generator().manual_seed(seed)
    hist = FitHistory(lr_used=plan.peak_lr, schedule=schedule, lr_find=plan.find)
    hist.lr_plan = plan.to_dict()
    best_val, best_state, bad, step = math.inf, None, 0, 0
    for epoch in range(epochs):
        model.train()
        running, count = 0.0, 0
        for batch in _batches(train, batch_size, gen):
            cur = schedule(step)
            for g in opt.param_groups:
                g["lr"] = cur
            opt.zero_grad(set_to_none=True)
            loss_value = accumulate_backward(model, loss_fn, batch, micro_batch)
            torch.nn.utils.clip_grad_norm_(model.parameters(), grad_clip)
            opt.step()
            running += loss_value * batch[0].shape[0]
            count += batch[0].shape[0]
            step += 1
        hist.train_loss.append(running / max(1, count))
        hist.lr.append(cur)
        if val is None:
            continue
        model.eval()
        with torch.no_grad():
            tot, n = 0.0, 0
            for batch in _batches(val, eval_batch or 1024, None):
                tot += float(loss_fn(model, batch)) * batch[0].shape[0]
                n += batch[0].shape[0]
        v = tot / max(1, n)
        hist.val_loss.append(v)
        if on_epoch is not None:
            on_epoch(epoch, v)
        if v < best_val - 1e-6:
            best_val, best_state, bad = v, copy.deepcopy(model.state_dict()), 0
            hist.best_epoch = epoch
        else:
            bad += 1
            if bad >= patience:
                hist.stopped_early = True
                break
    if best_state is not None:
        model.load_state_dict(best_state)
    elif val is None:
        hist.best_epoch = len(hist.train_loss) - 1
    model.eval()
    return hist
