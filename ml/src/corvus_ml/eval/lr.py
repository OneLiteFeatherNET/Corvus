"""Learning rate from the data: LR range test, suggestion rules and the schedule built on it.

The range test follows Smith, "Cyclical Learning Rates for Training Neural Networks"
(arXiv 1506.01186, section 3.3), in the exponential variant popularised by fastai's ``lr_find``:

* the learning rate of a throw-away copy of the model grows geometrically,
  ``lr_i = lr_min * (lr_max / lr_min) ** (i / (N - 1))`` for ``i = 0 .. N - 1`` (both ends are hit);
* the loss of every step is smoothed with a bias-corrected exponential moving average,
  ``a_t = beta * a_(t-1) + (1 - beta) * x_t`` and ``s_t = a_t / (1 - beta ** t)``;
* the sweep stops early once ``s_t > divergence * min(s)``;
* the suggestion is read off the smoothed curve by a documented rule (:func:`suggest_steepest`,
  :func:`suggest_valley`).

:class:`LrSchedule` turns the suggestion (the peak) and the dataset size into a warmup + cosine (or
warmup-stable-decay) schedule. Everything except :func:`find_lr` is a pure function of numbers.
Explanation and formulas: ``docs/explanation/learning-rate-finder.md``.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import numpy as np
import torch

LossFn = Callable[[torch.nn.Module, tuple[torch.Tensor, ...]], torch.Tensor]

AUTO = "auto"
RULES = ("steepest", "valley")
SCHEDULES = ("cosine", "wsd")

# Parameters of a candidate / job that steer the learning rate; ``pretrain_`` + name for the
# masked pretraining. ``lr`` itself is a number or "auto".
LR_PARAM_NAMES = ("lr", "lr_mult", "lr_steps", "lr_rule", "lr_schedule")
LR_PARAM_KEYS = LR_PARAM_NAMES
PRETRAIN_LR_PARAM_KEYS = tuple(f"pretrain_{k}" for k in LR_PARAM_NAMES)

# Defaults of the schedule built for "auto": 5 % warmup (at least 5, at most 2000 steps, never
# more than half of the run), cosine decay to 10 % of the peak.
AUTO_WARMUP_FRAC = 0.05
AUTO_WARMUP_MIN = 5
AUTO_WARMUP_MAX = 2000
AUTO_FLOOR_FRAC = 0.1
WSD_DECAY_FRAC = 0.2


# --- parameter parsing ---


def parse_lr(value: Any) -> float | Literal["auto"]:
    """``"auto"`` (any case) or a positive finite number; anything else is a ``ValueError``."""
    if isinstance(value, str):
        text = value.strip().lower()
        if text == AUTO:
            return AUTO
        try:
            value = float(text)
        except ValueError:
            raise ValueError(f"lr must be a positive number or 'auto', got {value!r}") from None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"lr must be a positive number or 'auto', got {value!r}")
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"lr must be a positive number or 'auto', got {value!r}")
    return float(value)


def is_auto(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() == AUTO


# --- exponential sweep, smoothing, suggestion (pure) ----------------------------------------------


def exponential_lrs(lr_min: float, lr_max: float, steps: int) -> np.ndarray:
    """``lr_i = lr_min * (lr_max / lr_min) ** (i / (steps - 1))``; first is ``lr_min``, last
    ``lr_max``."""
    if not 0 < lr_min < lr_max:
        raise ValueError("need 0 < lr_min < lr_max")
    if steps < 2:
        raise ValueError("the range test needs at least 2 steps")
    return lr_min * (lr_max / lr_min) ** (np.arange(steps) / (steps - 1))


def smooth_ema(values: np.ndarray | list[float], beta: float = 0.9) -> np.ndarray:
    """Bias-corrected exponential moving average ``a_t / (1 - beta ** t)`` (t starts at 1)."""
    if not 0 <= beta < 1:
        raise ValueError("beta must be in [0, 1)")
    out = np.empty(len(values), dtype=np.float64)
    avg = 0.0
    for t, x in enumerate(values, start=1):
        avg = beta * avg + (1.0 - beta) * float(x)
        out[t - 1] = avg / (1.0 - beta**t)
    return out


def suggest_steepest(lrs: np.ndarray, losses: np.ndarray) -> float | None:
    """Rule (a): the lr where the smoothed loss falls fastest per decade of lr.

    Only the part of the curve up to its minimum is searched (after it the loss climbs, that is not
    the descent we look for). The slope at point ``i`` is the secant over ``i - h .. i + h`` with
    ``h = max(1, round(0.05 * n))``: a one-step difference reads the noise of small batches as a
    steep descent. The suggestion is the lr with the most negative slope; ``None`` if the descent
    has fewer than 3 points or never falls.
    """
    lrs, losses = np.asarray(lrs, float), np.asarray(losses, float)
    k = int(np.argmin(losses))
    if k < 2:
        return None
    x, y = np.log10(lrs[: k + 1]), losses[: k + 1]
    h = max(1, round(0.05 * len(losses)))
    lo, hi = np.clip(np.arange(k + 1) - h, 0, k), np.clip(np.arange(k + 1) + h, 0, k)
    slope = (y[hi] - y[lo]) / np.maximum(x[hi] - x[lo], 1e-12)
    i = int(np.argmin(slope))
    return float(lrs[i]) if slope[i] < 0 else None


def suggest_valley(lrs: np.ndarray, losses: np.ndarray) -> float | None:
    """Rule (b): the lr of the smoothed minimum divided by 10 (Smith: the minimum itself is
    already on the edge of divergence). ``None`` for an empty curve."""
    if len(losses) == 0:
        return None
    return float(np.asarray(lrs, float)[int(np.argmin(losses))] / 10.0)


def suggest(lrs: np.ndarray, losses: np.ndarray) -> dict[str, float | None]:
    return {"steepest": suggest_steepest(lrs, losses), "valley": suggest_valley(lrs, losses)}


def downsample(lrs: np.ndarray | list[float], losses: np.ndarray | list[float], max_points: int):
    """At most ``max_points`` evenly spaced ``[lr, loss]`` pairs, first and last always kept."""
    n = len(lrs)
    if n == 0:
        return []
    idx = np.unique(np.linspace(0, n - 1, num=min(n, max(2, max_points))).round().astype(int))
    return [[float(lrs[i]), float(losses[i])] for i in idx]


# --- range test ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class LrFindConfig:
    lr_min: float = 1e-7
    lr_max: float = 1.0
    steps: int = 60  # CPU friendly; 100 gives a smoother curve
    batch_size: int = 64
    seed: int = 0
    weight_decay: float = 0.0
    beta: float = 0.9  # EMA smoothing of the loss
    divergence: float = 4.0  # stop when smoothed loss > divergence * best smoothed loss
    grad_clip: float = 1.0  # as in the training loop; None-like 0 disables
    rule: str = "valley"  # robust on small / noisy data; "steepest" is bolder
    fallback_lr: float = 2e-3  # used when the curve is too short / flat to read a suggestion

    def __post_init__(self) -> None:
        if self.rule not in RULES:
            raise ValueError(f"lr rule must be one of {RULES}, got {self.rule!r}")
        exponential_lrs(self.lr_min, self.lr_max, max(2, self.steps))


@dataclass
class LrFindResult:
    lrs: list[float]
    losses: list[float]  # smoothed
    suggested_lr: float
    rule: str = "valley"  # robust on small / noisy data; "steepest" is bolder
    suggestions: dict[str, float | None] = field(default_factory=dict)
    raw_losses: list[float] = field(default_factory=list)
    stopped_early: bool = False
    stop_reason: str | None = None
    config: dict[str, Any] = field(default_factory=dict)
    subset_size: int = 0

    def curve(self, max_points: int = 50) -> list[list[float]]:
        return downsample(self.lrs, self.losses, max_points)

    def to_dict(self, max_points: int = 50) -> dict[str, Any]:
        return {
            "suggested_lr": self.suggested_lr,
            "rule": self.rule,
            "suggestions": dict(self.suggestions),
            "steps_run": len(self.lrs),
            "steps_requested": self.config.get("steps"),
            "stopped_early": self.stopped_early,
            "stop_reason": self.stop_reason,
            "subset_size": self.subset_size,
            "config": dict(self.config),
            "curve": self.curve(max_points),
        }


def _pick(suggestions: Mapping[str, float | None], rule: str, fallback: float):
    value = suggestions.get(rule)
    if value is not None:
        return value, rule
    for other in RULES:
        if suggestions.get(other) is not None:
            return suggestions[other], other
    return fallback, "fallback"


def find_lr(
    model: torch.nn.Module,
    loss_fn: LossFn,
    tensors: tuple[torch.Tensor, ...],
    config: LrFindConfig | None = None,
) -> LrFindResult:
    """Run the range test on a copy of ``model`` and a seeded subset of ``tensors``.

    ``loss_fn(model, batch)`` returns the scalar loss of a batch (a tuple of tensors); it is the
    same closure for a supervised (BCE) and a self-supervised (masked) objective. ``model``, the
    optimizer a caller holds and the global RNG state are untouched: the sweep trains a deep copy
    with its own AdamW and runs inside ``torch.random.fork_rng``.
    """
    cfg = config or LrFindConfig()
    steps = max(2, cfg.steps)
    grid = exponential_lrs(cfg.lr_min, cfg.lr_max, steps)
    n = int(tensors[0].shape[0])
    if n == 0:
        raise ValueError("the range test needs at least one training sample")
    batch = max(1, min(cfg.batch_size, n))
    lrs: list[float] = []
    raw: list[float] = []
    stop_reason: str | None = None
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(cfg.seed)
        gen = torch.Generator().manual_seed(cfg.seed)
        size = min(n, steps * batch)
        pick = torch.randperm(n, generator=gen)[:size]
        subset = tuple(t[pick] for t in tensors)
        probe = copy.deepcopy(model)
        probe.train()
        opt = torch.optim.AdamW(probe.parameters(), lr=cfg.lr_min, weight_decay=cfg.weight_decay)
        best = math.inf
        step = 0
        while step < steps and stop_reason is None:
            order = torch.randperm(size, generator=gen)
            for i in range(0, size, batch):
                if step >= steps:
                    break
                sel = order[i : i + batch]
                for g in opt.param_groups:
                    g["lr"] = float(grid[step])
                opt.zero_grad(set_to_none=True)
                loss = loss_fn(probe, tuple(t[sel] for t in subset))
                value = float(loss.detach())
                if not math.isfinite(value):
                    stop_reason = "non-finite loss"
                    break
                loss.backward()
                if cfg.grad_clip > 0:
                    torch.nn.utils.clip_grad_norm_(probe.parameters(), cfg.grad_clip)
                opt.step()
                lrs.append(float(grid[step]))
                raw.append(value)
                step += 1
                smoothed = smooth_ema(raw, cfg.beta)[-1]  # O(N^2) over <= a few hundred steps
                best = min(best, smoothed)
                if smoothed > cfg.divergence * best:
                    stop_reason = f"loss exceeded {cfg.divergence:g}x the best smoothed loss"
                    break
    smoothed_all = smooth_ema(raw, cfg.beta) if raw else np.zeros(0)
    suggestions = suggest(np.asarray(lrs), smoothed_all)
    chosen, used_rule = _pick(suggestions, cfg.rule, cfg.fallback_lr)
    chosen = min(max(chosen, cfg.lr_min), cfg.lr_max)  # valley / 10 may leave the scanned range
    return LrFindResult(
        lrs=lrs,
        losses=[float(v) for v in smoothed_all],
        suggested_lr=float(chosen),
        rule=used_rule,
        suggestions=suggestions,
        raw_losses=raw,
        stopped_early=stop_reason is not None,
        stop_reason=stop_reason,
        config=asdict(cfg),
        subset_size=size,
    )


# --- schedule ----------------------------------------------------------------------------------


def total_steps(n_samples: int, batch_size: int, epochs: int) -> int:
    """``epochs * ceil(n_samples / batch_size)``."""
    return max(0, epochs) * math.ceil(max(0, n_samples) / max(1, batch_size))


def warmup_steps_for(
    total: int,
    frac: float = AUTO_WARMUP_FRAC,
    min_steps: int = AUTO_WARMUP_MIN,
    max_steps: int = AUTO_WARMUP_MAX,
) -> int:
    """``clamp(round(frac * total), min_steps, max_steps)``, but never more than ``total // 2``."""
    wanted = min(max(round(frac * total), min_steps), max_steps)
    return max(0, min(wanted, total // 2))


def scale_lr_for_batch(lr: float, reference_batch: int, batch: int) -> float:
    """Square-root scaling heuristic for Adam-type optimizers: ``lr * sqrt(batch / reference)``.

    Only a rule of thumb (Hoffer et al. 2017, Granziol et al. 2020); linear scaling is for SGD.
    """
    return lr * math.sqrt(max(1, batch) / max(1, reference_batch))


@dataclass(frozen=True)
class LrSchedule:
    """Warmup then decay, per optimizer step.

    ``cosine``: ``lr(s) = peak * s / W`` for ``s < W``, then
    ``floor + (peak - floor) * (1 + cos(pi * (s - W) / (T - W))) / 2``.
    ``wsd`` (warmup-stable-decay): warmup, ``peak`` until ``T * (1 - decay_frac)``, then linear
    decay to ``floor``. ``lr(T) == floor``; steps past ``T`` stay at ``floor``.
    """

    total_steps: int
    warmup_steps: int
    peak_lr: float
    floor_lr: float = 0.0
    kind: str = "cosine"
    decay_frac: float = WSD_DECAY_FRAC

    def __post_init__(self) -> None:
        if self.kind not in SCHEDULES:
            raise ValueError(f"lr schedule must be one of {SCHEDULES}, got {self.kind!r}")

    def __call__(self, step: int) -> float:
        peak, floor, warm, total = self.peak_lr, self.floor_lr, self.warmup_steps, self.total_steps
        if warm > 0 and step < warm:
            return peak * step / warm
        if step >= total:
            return floor
        if self.kind == "wsd":
            decay_start = max(warm, total - int(round(total * self.decay_frac)))
            if step < decay_start:
                return peak
            return peak + (floor - peak) * (step - decay_start) / max(1, total - decay_start)
        progress = (step - warm) / max(1, total - warm)
        return floor + 0.5 * (peak - floor) * (1.0 + math.cos(math.pi * progress))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def warmup_cosine(step: int, total: int, warmup: int, peak: float, floor: float = 0.0) -> float:
    return LrSchedule(total, warmup, peak, floor)(step)


# --- plan: lr + schedule for one training run ----------------------------------------------------


@dataclass
class LrPlan:
    peak_lr: float
    source: str  # "fixed" | "auto"
    schedule: LrSchedule
    find: LrFindResult | None = None
    multiplier: float = 1.0
    batch_scale: float = 1.0

    def to_dict(self, max_points: int = 50) -> dict[str, Any]:
        out: dict[str, Any] = {
            "lr": self.peak_lr,
            "source": self.source,
            "multiplier": self.multiplier,
            "batch_scale": self.batch_scale,
            "schedule": self.schedule.to_dict(),
        }
        if self.find is not None:
            out["lr_find"] = self.find.to_dict(max_points)
            out["rule"] = self.find.rule
        return out


def plan_lr(
    lr: float | str,
    model: torch.nn.Module,
    loss_fn: LossFn,
    tensors: tuple[torch.Tensor, ...],
    *,
    epochs: int,
    batch_size: int,
    find: LrFindConfig | None = None,
    multiplier: float = 1.0,
    schedule_kind: str = "cosine",
    legacy_warmup_frac: float = 0.1,
) -> LrPlan:
    """Peak lr and schedule of one run.

    A number keeps the long-standing behaviour: that lr, 10 % warmup (at least one step), cosine
    decay to 0. ``"auto"`` runs :func:`find_lr`, scales the suggestion by ``multiplier`` (the
    Optuna knob) and by ``sqrt(batch_size / find.batch_size)`` when the range test used another
    batch size, and decays to 10 % of the peak after a 5 % warmup (:func:`warmup_steps_for`).
    """
    total = total_steps(int(tensors[0].shape[0]), batch_size, epochs)
    value = parse_lr(lr)
    if value != AUTO:
        warmup = max(1, int(total * legacy_warmup_frac))
        return LrPlan(float(value), "fixed", LrSchedule(total, warmup, float(value), 0.0, "cosine"))
    cfg = find or LrFindConfig(batch_size=batch_size)
    result = find_lr(model, loss_fn, tensors, cfg)
    batch_scale = 1.0
    if cfg.batch_size != batch_size:
        batch_scale = scale_lr_for_batch(1.0, cfg.batch_size, batch_size)
    peak = result.suggested_lr * float(multiplier) * batch_scale
    schedule = LrSchedule(
        total, warmup_steps_for(total), peak, peak * AUTO_FLOOR_FRAC, schedule_kind
    )
    return LrPlan(peak, "auto", schedule, result, float(multiplier), batch_scale)


def lr_kwargs(
    params: Mapping[str, Any],
    *,
    prefix: str = "",
    default_lr: float,
    batch_size: int,
    seed: int,
    weight_decay: float,
) -> dict[str, Any]:
    """The learning-rate arguments of ``fit`` / ``pretrain`` from candidate or job parameters.

    ``prefix="pretrain_"`` reads ``pretrain_lr``, ``pretrain_lr_steps`` ... Without ``"auto"``
    only ``lr`` matters, so numeric configurations behave exactly as before.
    """
    lr = parse_lr(params.get(f"{prefix}lr", default_lr))
    find = LrFindConfig(
        steps=int(params.get(f"{prefix}lr_steps", LrFindConfig.steps)),
        rule=str(params.get(f"{prefix}lr_rule", LrFindConfig.rule)),
        batch_size=batch_size,
        seed=seed,
        weight_decay=weight_decay,
    )
    mult = float(params.get(f"{prefix}lr_mult", 1.0))
    if not mult > 0:
        raise ValueError(f"{prefix}lr_mult must be positive")
    return {
        "lr": lr,
        "lr_find": find,
        "lr_mult": mult,
        "schedule_kind": str(params.get(f"{prefix}lr_schedule", "cosine")),
    }
