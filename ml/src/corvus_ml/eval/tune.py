"""Hyper-parameter search with Optuna (``corvus-eval tune``, or ``params.tune`` of a job).

A study per (game context, candidate): TPE sampler and median pruner with a fixed seed, objective =
best validation loss of the candidate (the same loss early stopping uses; the pruner sees it per
epoch). Raw-byte candidates can also search ``window_seconds`` and ``max_packets``: the windows
are cut again from the packet table for every trial. The split is by player and is fixed before
the search: the trials of a window search see the same players in train / val, only their
windows change. The test split is never used.
"""

from __future__ import annotations

import logging
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np

from corvus_ml.eval import candidates as cand_pkg
from corvus_ml.eval.config import EvalConfig, TuneConfig
from corvus_ml.eval.data import LABEL_UNLABELLED, EvalData
from corvus_ml.eval.lr import is_auto
from corvus_ml.eval.split import Split
from corvus_ml.eval.training import seed_everything
from corvus_ml.raw.spec import WindowConfig

log = logging.getLogger(__name__)

Range = tuple  # ("log" | "float" | "int", low, high) or ("cat", [choices])

_COMMON: dict[str, Range] = {
    "lr": ("log", 3e-4, 1e-2),
    "weight_decay": ("log", 1e-4, 1e-1),
}
DEFAULT_SPACES: dict[str, dict[str, Range]] = {
    "raw_hier_classifier": {
        **_COMMON,
        "d_model": ("cat", [32, 64, 96, 128]),
        "layers": ("int", 1, 4),
        "dropout": ("float", 0.0, 0.3),
    },
    "gru_autoencoder": {
        **_COMMON,
        "hidden": ("cat", [32, 64, 128]),
        "latent": ("cat", [16, 32, 64]),
    },
    "cnn1d": {**_COMMON, "channels": ("cat", [16, 32, 64])},
}
DEFAULT_SPACES["raw_hier_anomaly"] = DEFAULT_SPACES["raw_hier_classifier"]
WINDOW_SPACE: dict[str, Range] = {
    "window_seconds": ("cat", [5.0, 10.0, 20.0]),
    "max_packets": ("cat", [256, 512, 1024]),
}


# With ``lr = "auto"`` the learning rate is not searched blindly: Optuna tunes a multiplier around
# the value the range test found (log-uniform, a factor of ~3 either way).
LR_MULT_RANGE: Range = ("log", 0.3, 3.0)


# Options without a default range: ``[tune.space] key = [low, high]`` searches them as floats.
OPTION_KINDS: dict[str, Range] = dict.fromkeys(
    ("p_token", "p_packet", "span_mean", "ema_momentum", "knn_weight"), ("float",)
)


def space_for(
    name: str, tune: TuneConfig, windows: bool, auto_lr: bool = False
) -> dict[str, Range]:
    """Default space of the candidate, with ``[tune.space]`` overrides applied.

    A variant name (``raw_hier_anomaly__jepa``) uses the space of its candidate.
    ``auto_lr``: the candidate's ``lr`` is ``"auto"``; ``lr`` is then replaced by ``lr_mult``.
    """
    space = dict(DEFAULT_SPACES.get(name.partition("__")[0], _COMMON))
    if auto_lr and "lr" in space:
        del space["lr"]
        space["lr_mult"] = LR_MULT_RANGE
    if windows:
        space.update(WINDOW_SPACE)
    for key, value in tune.space.items():
        default = space.get(key) or OPTION_KINDS.get(key)
        numeric = isinstance(value, list) and all(
            isinstance(v, (int, float)) and not isinstance(v, bool) for v in value
        )
        if numeric and len(value) == 2 and default is not None and default[0] != "cat":
            space[key] = (default[0], value[0], value[1])
        elif isinstance(value, list) and value:
            space[key] = ("cat", list(value))
        else:
            raise ValueError(f"tune.space.{key} must be [low, high] or a list of choices")
    return space


def _suggest(trial: Any, name: str, rng: Range) -> Any:
    kind = rng[0]
    if kind == "cat":
        return trial.suggest_categorical(name, rng[1])
    if kind == "int":
        return trial.suggest_int(name, int(rng[1]), int(rng[2]))
    return trial.suggest_float(name, float(rng[1]), float(rng[2]), log=kind == "log")


def player_parts(data: EvalData, split: Split) -> dict[bytes, str]:
    """Part (train / val / test) of every player of ``data`` under ``split``."""
    parts: dict[bytes, str] = {}
    for part, idx in split.parts().items():
        for p in set(data.pseudonym[idx].tolist()):
            parts[p] = part
    return parts


def split_like(data: EvalData, parts: dict[bytes, str]) -> Split:
    """Split of ``data`` that puts every player in the part it had before (new players: dropped)."""
    codes = np.array(
        [{"train": 0, "val": 1, "test": 2}.get(parts.get(p, ""), -1) for p in data.pseudonym]
    )
    return Split(*(np.flatnonzero(codes == c) for c in range(3)))


def window_for(base: WindowConfig, params: dict[str, Any]) -> WindowConfig:
    """Trial window: ``window_seconds`` rescales stride and macro window by the configured ratio."""
    w = float(params.get("window_seconds", base.window_seconds))
    scale = w / base.window_seconds
    macro = base.macro_window_seconds
    return replace(
        base,
        window_seconds=w,
        stride_seconds=base.stride_seconds * scale,
        max_packets=int(params.get("max_packets", base.max_packets)),
        macro_window_seconds=None if macro is None else macro * scale,
    )


def _storage(tune: TuneConfig, out_dir: Path) -> tuple[Any, str]:
    spec = tune.storage or os.environ.get("CORVUS_OPTUNA_STORAGE", "")
    if spec == "memory":
        return None, "memory"
    if not spec:
        out_dir.mkdir(parents=True, exist_ok=True)
        spec = f"sqlite:///{out_dir / 'optuna.db'}"
    shown = spec.split("://", 1)[0] + "://..." if "@" in spec else spec
    return spec, shown


def tune_candidate(
    name: str,
    cfg: EvalConfig,
    ctx: str,
    data: EvalData,
    split: Split,
    out_dir: Path,
) -> dict[str, Any]:
    """Run the study; returns the summary for the report (``best_params`` included)."""
    import optuna

    cls = cand_pkg.REGISTRY[name]
    tune = cfg.tune
    source = data.meta.get("raw_source")
    base_window: WindowConfig | None = data.meta["raw_spec"].window if source is not None else None
    windows = bool(tune.tune_windows and source is not None)
    space = space_for(name, tune, windows, auto_lr=is_auto(cfg.candidates[name].get("lr")))
    parts = player_parts(data, split)
    base_params = dict(cfg.candidates[name])
    cache: dict[WindowConfig, EvalData] = {}

    def data_for(window: WindowConfig | None) -> EvalData:
        if window is None or window == base_window:
            return data
        if window not in cache:
            built = source.with_window(window).build(  # type: ignore[union-attr]
                cfg.min_confidence, cfg.raw.max_windows if cfg.raw else None, cfg.seed
            )
            built = built.take(np.flatnonzero(built.game_context == ctx))
            built.meta["raw_source"] = source
            built.meta["tokenizer_json"] = data.meta.get("tokenizer_json")
            if cfg.unlabelled == "assume_legit":
                built.label[built.label == LABEL_UNLABELLED] = 0
            cache.clear()  # keep one rebuilt window set in memory
            cache[window] = built
        return cache[window]

    def sets_for(d: EvalData, s: Split) -> tuple[EvalData, EvalData]:
        lab = d.label
        if cls.supervised:
            keep_t = lab[s.train] != LABEL_UNLABELLED
            keep_v = lab[s.val] != LABEL_UNLABELLED
        else:
            keep_t, keep_v = lab[s.train] == 0, lab[s.val] == 0
        return d.take(s.train[keep_t]), d.take(s.val[keep_v])

    def find_lr_once() -> dict[str, Any] | None:
        """Range test with the base parameters on the base data: one run for the whole study."""
        train, val = sets_for(data, split)
        if len(train) == 0 or (cls.supervised and len(np.unique(train.label)) < 2):
            return None
        probe = {**base_params, "lr": "auto", "epochs": 0, "pretrain_epochs": 0}
        probe.pop("init_from", None)
        seed_everything(cfg.seed, cfg.threads)
        c = cand_pkg.create(name, cfg.seed, cfg.threads, probe)
        c.fit(train, val if len(val) else None)
        return {"result": c.lr_range, "plan": c.history.lr_plan}

    found: dict[str, Any] | None = None

    def objective(trial: optuna.Trial) -> float:
        suggested = {k: _suggest(trial, k, r) for k, r in space.items()}
        window = window_for(base_window, suggested) if base_window is not None else None
        d = data_for(window)
        s = split_like(d, parts)
        train_idx = s.train
        train, val = sets_for(d, s)
        if len(train) == 0:
            raise optuna.TrialPruned("no training windows with this configuration")
        model_params = {k: v for k, v in suggested.items() if k not in WINDOW_SPACE}
        trial_params = {**base_params, **model_params}
        if found is not None and found["result"] is not None:
            trial_params["lr_find_result"] = found["result"]
        cand = cand_pkg.create(name, cfg.seed, cfg.threads, trial_params)

        def on_epoch(epoch: int, value: float) -> None:
            trial.report(value, epoch)
            if trial.should_prune():
                raise optuna.TrialPruned()

        cand.on_epoch = on_epoch
        seed_everything(cfg.seed, cfg.threads)
        if cls.pretrains:
            cand.fit(train, val if len(val) else None, pool=d.take(train_idx))
        else:
            cand.fit(train, val if len(val) else None)
        hist = cand.history
        value = min(hist.val_loss) if hist.val_loss else hist.train_loss[-1]
        if not np.isfinite(value):
            raise optuna.TrialPruned("non-finite loss")
        return float(value)

    storage, shown = _storage(tune, out_dir)
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(
        study_name=f"{tune.study_name}-{ctx}-{name}",
        direction="minimize",
        sampler=optuna.samplers.TPESampler(seed=cfg.seed),
        pruner=optuna.pruners.MedianPruner(n_startup_trials=2, n_warmup_steps=1),
        storage=storage,
        load_if_exists=True,
    )
    finished = sum(t.state.is_finished() for t in study.trials)
    remaining = max(0, tune.trials - finished)
    if remaining and is_auto(base_params.get("lr")):
        found = find_lr_once()
    if remaining:
        study.optimize(objective, n_trials=remaining, timeout=tune.timeout_s, catch=())
    complete = [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]
    best = study.best_trial if complete else None
    best_params = dict(best.params) if best else {}
    return {
        "candidate": name,
        "direction": "minimize validation loss",
        "sampler": f"TPE(seed={cfg.seed})",
        "pruner": "MedianPruner(n_startup_trials=2, n_warmup_steps=1)",
        "storage": shown,
        "search_space": {k: list(v) for k, v in space.items()},
        "n_trials": len(study.trials),
        "n_complete": len(complete),
        "n_pruned": sum(t.state == optuna.trial.TrialState.PRUNED for t in study.trials),
        "best_value": best.value if best else None,
        "best_trial": best.number if best else None,
        "best_params": best_params,
        "lr_find": found["plan"] if found else None,
        "trials": [
            {"number": t.number, "state": t.state.name, "value": t.value, "params": dict(t.params)}
            for t in study.trials
        ],
    }
