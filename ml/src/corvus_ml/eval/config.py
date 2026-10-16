"""Run configuration (``eval.toml``); documented in docs/how-to/evaluate-models.md."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from corvus_ml.calibration import METHODS as CALIBRATION_METHODS
from corvus_ml.eval.synthetic import SyntheticConfig
from corvus_ml.raw.layout import TokenLayout
from corvus_ml.raw.spec import PacketConfig, WindowConfig
from corvus_ml.raw.synthetic import SyntheticRawConfig
from corvus_ml.raw.tokenizer import TokenizerConfig

UNLABELLED_MODES = ("exclude", "assume_legit")


@dataclass
class BenchConfig:
    enabled: bool = True
    warmup: int = 20
    iters: int = 200
    batch: int = 64
    isolated: bool = True  # fresh process per model so peak RSS belongs to that model
    quantize: bool = True  # int8 dynamic quantisation variant of the torch models
    # Raw-byte models: extra single-window latencies at these packet counts (full-width rows).
    raw_packets: tuple[int, ...] = ()
    # Sanity bound for the p99 of one window on CPU; the report marks it. Scoring is asynchronous,
    # so this is no real-time target: the report's players/core column is the throughput check.
    latency_budget_ms: float = 5000.0


@dataclass
class RawEvalConfig:
    """Raw-byte input (``[raw]``): a packet table below ``dir`` or a synthetic source."""

    dir: Path | None = None  # features/<snapshot>/raw: packets/, sessions, labels, spec, tokenizer
    window_overrides: dict[str, Any] = field(default_factory=dict)
    synthetic: SyntheticRawConfig | None = None
    tokenizer: TokenizerConfig = field(default_factory=lambda: TokenizerConfig(vocab_size=1024))
    packet: PacketConfig = field(default_factory=PacketConfig)
    max_windows: int | None = None  # random subsample of the windows (memory bound)

    def window(self, base: WindowConfig | None = None) -> WindowConfig:
        merged = {**(base or WindowConfig()).__dict__, **self.window_overrides}
        return WindowConfig(**merged)


@dataclass
class TuneConfig:
    """Optuna study (``[tune]``); see docs/how-to/evaluate-models.md."""

    trials: int = 20
    timeout_s: float | None = None
    storage: str = ""  # "" = sqlite file in the output dir (or CORVUS_OPTUNA_STORAGE), "memory"
    study_name: str = "corvus"
    tune_windows: bool = True  # raw-byte models: window_seconds and max_packets are searched too
    space: dict[str, Any] = field(default_factory=dict)  # per parameter [low, high] or a list


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
    raw: RawEvalConfig | None = None
    tune: TuneConfig = field(default_factory=TuneConfig)

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
        elif "raw" in raw:
            cfg.raw = _raw_config(dict(raw["raw"]), base)
        elif not (cfg.windows and cfg.sessions):
            raise ValueError(
                "config needs [data] windows + sessions, a [synthetic] table or a [raw] table"
            )
        cfg.candidates = {k: dict(v) for k, v in raw.get("candidates", {}).items()}
        if not cfg.candidates:
            raise ValueError("config needs at least one [candidates.<name>] table")
        bench = dict(raw.get("benchmark", {}))
        if "raw_packets" in bench:
            bench["raw_packets"] = tuple(int(v) for v in bench["raw_packets"])
        cfg.bench = BenchConfig(**bench)
        if "tune" in raw:
            tune = dict(raw["tune"])
            extra = set(tune) - {f.name for f in fields(TuneConfig)}
            if extra:
                raise ValueError(f"unknown [tune] keys: {sorted(extra)}")
            cfg.tune = TuneConfig(**tune)
        _check_candidate_kinds(cfg)
        return cfg

    @classmethod
    def load(cls, path: Path) -> EvalConfig:
        path = Path(path)
        return cls.from_dict(tomllib.loads(path.read_text()), path.parent)


_WINDOW_KEYS = {f.name for f in fields(WindowConfig)}


def _raw_config(raw: dict, base: Path) -> RawEvalConfig:
    out = RawEvalConfig()
    if "dir" in raw:
        out.dir = (base / raw.pop("dir")).resolve()
    if "synthetic" in raw:
        syn = dict(raw.pop("synthetic"))
        for key in ("contexts", "protocols"):
            if key in syn:
                syn[key] = tuple(syn[key])
        out.synthetic = SyntheticRawConfig(**syn)
    if "tokenizer" in raw:
        tok = dict(raw.pop("tokenizer"))
        if "layout" in tok:
            lay = dict(tok.pop("layout"))
            if "protocols" in lay:
                lay["protocols"] = tuple(int(v) for v in lay["protocols"])
            tok["layout"] = TokenLayout(**lay)
        out.tokenizer = TokenizerConfig(**{"vocab_size": 1024, **tok})
    if "packet" in raw:
        out.packet = PacketConfig(**raw.pop("packet"))
    if "max_windows" in raw:
        out.max_windows = int(raw.pop("max_windows"))
    unknown = set(raw) - _WINDOW_KEYS
    if unknown:
        raise ValueError(f"unknown [raw] keys: {sorted(unknown)}")
    out.window_overrides = dict(raw)
    out.window()  # validates
    if (out.dir is None) == (out.synthetic is None):
        raise ValueError("[raw] needs exactly one of dir and [raw.synthetic]")
    return out


def _check_candidate_kinds(cfg: EvalConfig) -> None:
    """Raw-byte candidates run on their own windows: they cannot share a run with the others."""
    from corvus_ml.eval import candidates as cand_pkg

    kinds = {
        cand_pkg.REGISTRY[n].input_kind == "raw_bytes"
        for n in cfg.candidates
        if n in cand_pkg.REGISTRY
    }
    if len(kinds) > 1:
        raise ValueError("raw-byte candidates need their own run: do not mix them with others")
    if kinds == {True} and cfg.raw is None:
        raise ValueError("raw-byte candidates need a [raw] table")
    if kinds == {False} and cfg.raw is not None:
        raise ValueError("a [raw] table needs raw-byte candidates")
