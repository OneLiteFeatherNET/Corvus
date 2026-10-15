"""Training helpers for the torch candidates: seeding, LR schedule, LR range test, fit loop."""

from __future__ import annotations

import copy
import math
import os
import random
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import torch


def seed_everything(seed: int, threads: int = 1) -> None:
    """Seed python, numpy and torch; pin CPU threads; prefer deterministic kernels."""
    random.seed(seed)
    np.random.seed(seed)  # noqa: NPY002 - legacy global state, for libraries that use it
    torch.manual_seed(seed)
    torch.set_num_threads(max(1, threads))
    os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    torch.use_deterministic_algorithms(True, warn_only=True)


def lr_at(step: int, total_steps: int, warmup_steps: int, base_lr: float, min_lr: float = 0.0):
    """Linear warmup to ``base_lr`` over ``warmup_steps``, then cosine decay to ``min_lr``.

    ``lr_at(warmup_steps) == base_lr`` and ``lr_at(total_steps) == min_lr``.
    """
    if warmup_steps > 0 and step < warmup_steps:
        return base_lr * step / warmup_steps
    if step >= total_steps:
        return min_lr
    progress = (step - warmup_steps) / max(1, total_steps - warmup_steps)
    return min_lr + 0.5 * (base_lr - min_lr) * (1.0 + math.cos(math.pi * progress))


# loss_fn(model, batch) -> scalar tensor; ``batch`` is a tuple of tensors.
LossFn = Callable[[torch.nn.Module, tuple[torch.Tensor, ...]], torch.Tensor]


def _batches(tensors: tuple[torch.Tensor, ...], size: int, gen: torch.Generator | None):
    n = tensors[0].shape[0]
    order = torch.randperm(n, generator=gen) if gen is not None else torch.arange(n)
    for i in range(0, n, size):
        sel = order[i : i + size]
        yield tuple(t[sel] for t in tensors)


@dataclass
class LrRangeResult:
    lrs: list[float]
    losses: list[float]  # smoothed
    suggested_lr: float


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
) -> LrRangeResult:
    """Smith's LR range test on a throw-away copy of ``model`` (the model is left untouched).

    The LR grows exponentially from ``lr_min`` to ``lr_max`` while one AdamW step per batch is
    taken. The suggestion is the LR at the steepest descent of the smoothed loss; the
    test stops early once the loss exceeds four times its best value.
    """
    probe = copy.deepcopy(model)
    probe.train()
    opt = torch.optim.AdamW(probe.parameters(), lr=lr_min, weight_decay=weight_decay)
    gen = torch.Generator().manual_seed(seed)
    mult = (lr_max / lr_min) ** (1.0 / max(1, steps - 1))
    lrs: list[float] = []
    losses: list[float] = []
    avg, best = 0.0, math.inf
    step = 0
    while step < steps:
        for batch in _batches(tensors, batch_size, gen):
            lr = lr_min * mult**step
            for g in opt.param_groups:
                g["lr"] = lr
            opt.zero_grad(set_to_none=True)
            loss = loss_fn(probe, batch)
            loss.backward()
            opt.step()
            value = float(loss.detach())
            avg = smoothing * avg + (1 - smoothing) * value
            debiased = avg / (1 - smoothing ** (step + 1))
            lrs.append(lr)
            losses.append(debiased)
            best = min(best, debiased)
            step += 1
            if not math.isfinite(debiased) or debiased > 4 * best or step >= steps:
                step = steps
                break
    if len(losses) < 3:
        return LrRangeResult(lrs, losses, lrs[0] if lrs else lr_min)
    grads = np.gradient(np.asarray(losses), np.log(np.asarray(lrs)))
    return LrRangeResult(lrs, losses, float(lrs[int(np.argmin(grads))]))


@dataclass
class FitHistory:
    train_loss: list[float] = field(default_factory=list)
    val_loss: list[float] = field(default_factory=list)
    lr: list[float] = field(default_factory=list)
    best_epoch: int = -1
    stopped_early: bool = False


def fit(
    model: torch.nn.Module,
    loss_fn: LossFn,
    train: tuple[torch.Tensor, ...],
    val: tuple[torch.Tensor, ...] | None,
    *,
    epochs: int,
    batch_size: int,
    lr: float,
    weight_decay: float = 0.01,
    warmup_frac: float = 0.1,
    patience: int = 5,
    seed: int = 0,
    grad_clip: float = 1.0,
    on_epoch: Callable[[int, float], None] | None = None,
) -> FitHistory:
    """AdamW + linear warmup / cosine decay + early stopping on validation loss.

    The best-validation weights are restored at the end. Without ``val`` the last epoch is kept.
    """
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=weight_decay)
    gen = torch.Generator().manual_seed(seed)
    steps_per_epoch = math.ceil(train[0].shape[0] / batch_size)
    total = epochs * steps_per_epoch
    warmup = max(1, int(total * warmup_frac))
    hist = FitHistory()
    best_val, best_state, bad, step = math.inf, None, 0, 0
    for epoch in range(epochs):
        model.train()
        running, count = 0.0, 0
        for batch in _batches(train, batch_size, gen):
            cur = lr_at(step, total, warmup, lr)
            for g in opt.param_groups:
                g["lr"] = cur
            opt.zero_grad(set_to_none=True)
            loss = loss_fn(model, batch)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), grad_clip)
            opt.step()
            running += float(loss.detach()) * batch[0].shape[0]
            count += batch[0].shape[0]
            step += 1
        hist.train_loss.append(running / max(1, count))
        hist.lr.append(cur)
        if val is None:
            continue
        model.eval()
        with torch.no_grad():
            tot, n = 0.0, 0
            for batch in _batches(val, 1024, None):
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
