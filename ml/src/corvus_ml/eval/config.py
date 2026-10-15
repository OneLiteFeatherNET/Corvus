"""Run configuration (``eval.toml``); documented in docs/how-to/evaluate-models.md."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

from corvus_ml.calibration import METHODS as CALIBRATION_METHODS
from corvus_ml.eval.synthetic import SyntheticConfig

UNLABELLED_MODES = ("exclude", "assume_legit")


@dataclass
class BenchConfig:
    enabled: bool = True
    warmup: int = 20
    iters: int = 200
    batch: int = 64
    isolated: bool = True  # fresh process per model so peak RSS belongs to that model
    quantize: bool = True  # int8 dynamic quantisation variant of the torch models


@dataclass
class EvalConfig:
    name: str = "eval"
    seed: int = 1337
    threads: int = 1
    seq_len: int = 256
    unlabelled: str = "exclude"
    calibration: str = "auto"  # auto | isotonic | platt | ecdf | none (see corvus_ml.calibration)
    contexts: list[str] = field(default_factory=list)  # empty = every context in the data
    split: tuple[float, float, float] = (0.6, 0.2, 0.2)
    fpr_targets: tuple[float, ...] = (0.01, 0.001)
    min_confidence: float = 0.0
    learning_curve: tuple[float, ...] = ()
    # Real data (all paths relative to the config file). tokens is only needed by sequence models.
    windows: Path | None = None
    tokens: Path | None = None
    sessions: Path | None = None
    labels: Path | None = None
    synthetic: SyntheticConfig | None = None
    candidates: dict[str, dict] = field(default_factory=dict)
    bench: BenchConfig = field(default_factory=BenchConfig)

    @classmethod
    def from_dict(cls, raw: dict, base: Path = Path()) -> EvalConfig:
        run = dict(raw.get("run", {}))
        data = raw.get("data", {})
        cfg = cls(name=str(raw.get("name", "eval")))
        for key in (
            "seed",
            "threads",
            "seq_len",
            "unlabelled",
            "calibration",
            "contexts",
            "min_confidence",
        ):
            if key in run:
                setattr(cfg, key, run.pop(key))
        if "split" in run:
            cfg.split = tuple(float(v) for v in run.pop("split"))
        if "fpr_targets" in run:
            cfg.fpr_targets = tuple(float(v) for v in run.pop("fpr_targets"))
        if "learning_curve" in run:
            cfg.learning_curve = tuple(float(v) for v in run.pop("learning_curve"))
        if run:
            raise ValueError(f"unknown [run] keys: {sorted(run)}")
        if cfg.unlabelled not in UNLABELLED_MODES:
            raise ValueError(f"run.unlabelled must be one of {UNLABELLED_MODES}")
        if cfg.calibration not in CALIBRATION_METHODS:
            raise ValueError(f"run.calibration must be one of {CALIBRATION_METHODS}")
        for key in ("windows", "tokens", "sessions", "labels"):
            if key in data:
                setattr(cfg, key, (base / data[key]).resolve())
        if "synthetic" in raw:
            names = {f.name for f in fields(SyntheticConfig)}
            extra = set(raw["synthetic"]) - names
            if extra:
                raise ValueError(f"unknown [synthetic] keys: {sorted(extra)}")
            syn = dict(raw["synthetic"])
            if "contexts" in syn:
                syn["contexts"] = tuple(syn["contexts"])
            syn.setdefault("seed", cfg.seed)
            cfg.synthetic = SyntheticConfig(**syn)
        elif not (cfg.windows and cfg.sessions):
            raise ValueError("config needs [data] windows + sessions, or a [synthetic] table")
        cfg.candidates = {k: dict(v) for k, v in raw.get("candidates", {}).items()}
        if not cfg.candidates:
            raise ValueError("config needs at least one [candidates.<name>] table")
        bench = dict(raw.get("benchmark", {}))
        cfg.bench = BenchConfig(**bench)
        return cfg

    @classmethod
    def load(cls, path: Path) -> EvalConfig:
        path = Path(path)
        return cls.from_dict(tomllib.loads(path.read_text()), path.parent)
