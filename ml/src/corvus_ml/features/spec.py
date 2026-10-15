"""Versioned description of the features a model was trained on.

A :class:`FeatureSpec` is written as ``feature_spec.json`` next to every exported model and read
(and validated) by whoever runs the model: the evaluation harness and the scoring service. It pins

* the featurizer: window parameters, aggregate names and token columns it produces, and
* the model input layout: which aggregates / token columns, in which order, with which transform.

Normalisation statistics are *not* part of it: they live inside the ONNX graph.
Bump :data:`FEATURE_SPEC_VERSION` whenever the meaning of a feature or the input layout changes;
models trained on an older version are then refused instead of silently mis-scored.

Pure Python; importing this module pulls in neither Arrow nor any ML stack.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from corvus_ml.features.config import FeaturizerConfig
from corvus_ml.features.constants import AGGREGATE_NAMES, NULLABLE_AGGREGATES, TOKEN_NAMES

FEATURE_SPEC_NAME = "corvus-features"
FEATURE_SPEC_VERSION = 1
SPEC_FILE = "feature_spec.json"

# Model input "agg": window aggregates (+ n_frames) with nullable ones zero-filled and followed by
# has_<name> indicator columns. Order is the column order of the model input.
_AGG_NULLABLE = tuple(n for n in AGGREGATE_NAMES if n in NULLABLE_AGGREGATES)
DEFAULT_AGG_INPUTS: tuple[str, ...] = (
    "cps",
    "attacks_per_s",
    "packets_per_s",
    "movement_per_s",
    "rot_var",
    "rot_speed_mean",
    "rot_speed_max",
    "n_frames",
    *_AGG_NULLABLE,
    *(f"has_{n}" for n in _AGG_NULLABLE),
)

# Model input "seq": (token column, transform). "slog" = sign(x) * ln(1 + |x|) compresses heavy
# tails; "scale8" maps small enums to ~[0, 1]; "id" is the raw value. A trailing mask channel
# (1 = real frame, 0 = padding) is appended after these columns.
DEFAULT_SEQ_INPUTS: tuple[tuple[str, str], ...] = (
    ("event_kind", "scale8"),
    ("domain", "scale8"),
    ("action", "scale8"),
    ("dt_bucket", "scale8"),
    ("log_dt", "id"),
    ("has_pos_delta", "id"),
    ("dx", "slog"),
    ("dy", "slog"),
    ("dz", "slog"),
    ("has_rot_delta", "id"),
    ("dyaw", "slog"),
    ("dpitch", "slog"),
    ("rot_speed", "slog"),
    ("rot_accel", "slog"),
    ("on_ground", "id"),
    ("t_since_swing", "id"),
    ("t_since_move", "id"),
    ("target_dist", "slog"),
    ("target_angle", "slog"),
)
TRANSFORMS = ("id", "slog", "scale8")


class FeatureSpecError(ValueError):
    """The spec file is unreadable, malformed or of an unknown format."""


class FeatureSpecMismatch(FeatureSpecError):
    """The spec a model was exported with differs from what the running code produces."""


@dataclass(frozen=True)
class FeatureSpec:
    """See the module docstring. Construct the current one with :meth:`current`."""

    name: str
    version: int
    window: FeaturizerConfig
    aggregate_names: tuple[str, ...]
    token_columns: tuple[str, ...]
    agg_inputs: tuple[str, ...]
    seq_inputs: tuple[tuple[str, str], ...]
    seq_len: int

    @classmethod
    def current(cls, window: FeaturizerConfig | None = None, seq_len: int = 256) -> FeatureSpec:
        """The spec of the code in this checkout for the given windowing parameters."""
        return cls(
            name=FEATURE_SPEC_NAME,
            version=FEATURE_SPEC_VERSION,
            window=(window or FeaturizerConfig()).featurizer(),
            aggregate_names=AGGREGATE_NAMES,
            token_columns=TOKEN_NAMES,
            agg_inputs=DEFAULT_AGG_INPUTS,
            seq_inputs=DEFAULT_SEQ_INPUTS,
            seq_len=seq_len,
        )

    @property
    def n_agg_inputs(self) -> int:
        return len(self.agg_inputs)

    @property
    def n_seq_channels(self) -> int:
        """Token channels per frame including the trailing mask channel."""
        return len(self.seq_inputs) + 1

    # --- serialisation -----------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "window": {**asdict(self.window), "dt_edges_ms": list(self.window.dt_edges_ms)},
            "aggregate_names": list(self.aggregate_names),
            "token_columns": list(self.token_columns),
            "model_inputs": {
                "agg": {"columns": list(self.agg_inputs)},
                "seq": {
                    "length": self.seq_len,
                    "columns": [{"name": n, "transform": t} for n, t in self.seq_inputs],
                    "mask": "last",
                },
            },
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2) + "\n"

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> FeatureSpec:
        try:
            window_raw = dict(raw["window"])
            window_raw["dt_edges_ms"] = tuple(float(e) for e in window_raw["dt_edges_ms"])
            unknown = set(window_raw) - {f.name for f in fields(FeaturizerConfig)}
            if unknown:
                raise FeatureSpecError(f"unknown window keys: {sorted(unknown)}")
            inputs = raw["model_inputs"]
            return cls(
                name=str(raw["name"]),
                version=int(raw["version"]),
                window=FeaturizerConfig(**window_raw),
                aggregate_names=tuple(raw["aggregate_names"]),
                token_columns=tuple(raw["token_columns"]),
                agg_inputs=tuple(inputs["agg"]["columns"]),
                seq_inputs=tuple((c["name"], c["transform"]) for c in inputs["seq"]["columns"]),
                seq_len=int(inputs["seq"]["length"]),
            )
        except FeatureSpecError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise FeatureSpecError(f"malformed feature spec: {type(exc).__name__}: {exc}") from exc

    def write(self, directory: Path) -> Path:
        """Write ``feature_spec.json`` into ``directory`` (the model export directory)."""
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / SPEC_FILE
        path.write_text(self.to_json())
        return path

    @classmethod
    def read(cls, path: Path) -> FeatureSpec:
        """Parse a spec file (or the ``feature_spec.json`` inside a directory); no validation."""
        path = Path(path)
        if path.is_dir():
            path = path / SPEC_FILE
        try:
            raw = json.loads(path.read_text())
        except FileNotFoundError as exc:
            raise FeatureSpecError(f"no feature spec at {path}") from exc
        except json.JSONDecodeError as exc:
            raise FeatureSpecError(f"{path} is not valid JSON: {exc}") from exc
        if not isinstance(raw, dict):
            raise FeatureSpecError(f"{path}: expected a JSON object")
        return cls.from_dict(raw)

    # --- validation --------------------------------------------------------------------------

    def differences(self, other: FeatureSpec, *, window: bool = True) -> list[str]:
        """Human readable differences to ``other`` (empty = compatible)."""
        out: list[str] = []

        def cmp(label: str, mine: object, theirs: object) -> None:
            if mine != theirs:
                out.append(f"{label}: model has {mine!r}, runtime expects {theirs!r}")

        cmp("name", self.name, other.name)
        cmp("version", self.version, other.version)
        cmp("aggregate_names", self.aggregate_names, other.aggregate_names)
        cmp("token_columns", self.token_columns, other.token_columns)
        cmp("model_inputs.agg", self.agg_inputs, other.agg_inputs)
        cmp("model_inputs.seq", self.seq_inputs, other.seq_inputs)
        if window:
            cmp("seq_len", self.seq_len, other.seq_len)
            for f in fields(FeaturizerConfig):
                cmp(f"window.{f.name}", getattr(self.window, f.name), getattr(other.window, f.name))
        return out

    def require_compatible(self, expected: FeatureSpec, *, window: bool = True) -> None:
        """Raise :class:`FeatureSpecMismatch` listing every difference to ``expected``."""
        diffs = self.differences(expected, window=window)
        if diffs:
            raise FeatureSpecMismatch(
                "feature spec of the model does not match the runtime:\n  - " + "\n  - ".join(diffs)
            )

    @classmethod
    def load(cls, path: Path, expected: FeatureSpec | None = None) -> FeatureSpec:
        """Read a spec and validate it.

        With ``expected`` (e.g. the window parameters a serving process is configured with) the
        whole spec must match. Without it only the feature definitions and the input layout are
        checked against this checkout; the window parameters and ``seq_len`` of the file are then
        authoritative, so a service builds its featurizer from ``spec.window``.
        """
        spec = cls.read(path)
        if expected is None:
            spec.require_compatible(cls.current(spec.window, spec.seq_len))
        else:
            spec.require_compatible(expected)
        return spec
