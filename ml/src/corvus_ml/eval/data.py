"""In-memory evaluation dataset: window aggregates, token sequences and joined labels."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

LABEL_UNLABELLED = -1
LABEL_LEGIT = 0
LABEL_CHEAT = 1

# Aggregate columns of the ``windows`` table used as model input (see docs/reference/features.md).
AGG_COLUMNS = (
    "cps",
    "attacks_per_s",
    "packets_per_s",
    "movement_per_s",
    "rot_var",
    "rot_speed_mean",
    "rot_speed_max",
    "n_frames",
)
# Nullable aggregates: null becomes 0 plus a ``has_<name>`` indicator column.
NULLABLE_AGG_COLUMNS = ("on_ground_frac", "tick_end_rate")
AGG_NAMES = (*AGG_COLUMNS, *NULLABLE_AGG_COLUMNS, *(f"has_{c}" for c in NULLABLE_AGG_COLUMNS))

# Token features fed to the sequence models, each with a transform applied before use.
# "slog" = sign(x) * ln(1 + |x|) compresses heavy tails; "scale8" maps small enums to ~[0, 1].
SEQ_FEATURES: tuple[tuple[str, str], ...] = (
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
N_SEQ_FEATURES = len(SEQ_FEATURES) + 1  # + trailing mask channel (1 = real frame, 0 = padding)


def apply_transform(values: np.ndarray, kind: str) -> np.ndarray:
    v = values.astype(np.float32, copy=False)
    if kind == "slog":
        return np.sign(v) * np.log1p(np.abs(v))
    if kind == "scale8":
        return v / np.float32(8.0)
    return v


@dataclass
class EvalData:
    """One row per window. ``label`` is -1 (unlabelled), 0 (legit) or 1 (cheat)."""

    session_id: np.ndarray  # object array of bytes
    segment: np.ndarray
    window_index: np.ndarray
    game_context: np.ndarray  # object array of str
    pseudonym: np.ndarray  # object array of bytes
    label: np.ndarray  # int8
    confidence: np.ndarray  # float32
    agg: np.ndarray  # float32 (n, len(AGG_NAMES))
    seq: np.ndarray | None = None  # float32 (n, L, N_SEQ_FEATURES)
    meta: dict = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.label)

    def take(self, idx: np.ndarray) -> EvalData:
        idx = np.asarray(idx)
        return EvalData(
            self.session_id[idx],
            self.segment[idx],
            self.window_index[idx],
            self.game_context[idx],
            self.pseudonym[idx],
            self.label[idx],
            self.confidence[idx],
            self.agg[idx],
            None if self.seq is None else self.seq[idx],
            self.meta,
        )

    def group_codes(self) -> np.ndarray:
        """Integer id per pseudonym, assigned in order of first appearance."""
        codes: dict[bytes, int] = {}
        return np.fromiter(
            (codes.setdefault(p, len(codes)) for p in self.pseudonym),
            dtype=np.int64,
            count=len(self),
        )

    def labelled_mask(self) -> np.ndarray:
        return self.label != LABEL_UNLABELLED
