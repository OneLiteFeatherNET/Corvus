"""Synthetic windows / tokens / sessions / labels in the real table schemas.

SYNTHETIC DATA: this exists so tests and smoke runs can exercise the whole evaluation pipeline
end to end without real captures. The generator is a toy model of "human-like" versus "cheat-like"
input (rotation noise, aim snaps, constant click cadence). Numbers measured on it say that the
code works, never how well a model performs on real players. Do not quote them as such.

Legit players: Gaussian rotation noise with a per-player sensitivity, occasional larger flicks,
jittered inter-frame gaps and random click cadence. Cheat players: near-zero rotation noise
between rare large "snaps", constant inter-frame gaps, a constant click cadence, and attacks that
come with the swing at a near-perfect aim angle. ``cheat_strength`` (0..1) blends the cheat
behaviour towards the legit one, ``cheat_window_frac`` is the share of a cheater's windows that
actually show it (labels are per player, so < 1 injects label noise).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from corvus_ml.features.config import DEFAULT_DT_EDGES_MS
from corvus_ml.features.schema import (
    EVENT_INTERACT,
    EVENT_MOVEMENT,
    EVENT_SWING,
    TOKEN_NAMES,
    WINDOW_TOKENS_SCHEMA,
    WINDOWS_SCHEMA,
)

_BASE_TIME = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
_PKT = {EVENT_MOVEMENT: 10, EVENT_SWING: 20, EVENT_INTERACT: 30}
_DOMAIN = {EVENT_MOVEMENT: 1, EVENT_SWING: 2, EVENT_INTERACT: 2}


@dataclass(frozen=True)
class SyntheticConfig:
    seed: int = 0
    n_legit: int = 24
    n_cheat: int = 12
    n_unlabelled: int = 0  # extra players without any label row
    sessions_per_player: int = 2
    windows_per_session: int = 6
    frames_per_window: int = 64
    contexts: tuple[str, ...] = ("pvp",)
    cheat_strength: float = 1.0
    cheat_window_frac: float = 1.0


@dataclass
class SyntheticTables:
    windows: pa.Table
    tokens: pa.Table
    sessions: pa.Table
    labels: pa.Table

    def write(self, out: Path) -> dict[str, Path]:
        """Write the four tables as Parquet below ``out`` and return their paths."""
        out.mkdir(parents=True, exist_ok=True)
        paths = {
            "windows": out / "windows" / "part-0.parquet",
            "tokens": out / "window_tokens" / "part-0.parquet",
            "sessions": out / "sessions.parquet",
            "labels": out / "labels.parquet",
        }
        for name, table in (
            ("windows", self.windows),
            ("tokens", self.tokens),
            ("sessions", self.sessions),
            ("labels", self.labels),
        ):
            paths[name].parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(table, paths[name])
        return {"windows": paths["windows"].parent, "tokens": paths["tokens"].parent} | {
            "sessions": paths["sessions"],
            "labels": paths["labels"],
        }


def _window(rng: np.random.Generator, n: int, scale: float, cheat: bool, strength: float):
    """Token columns (dict of arrays) for one window."""
    kind = rng.choice(
        [EVENT_MOVEMENT, EVENT_SWING, EVENT_INTERACT], size=n, p=[0.78, 0.14, 0.08]
    ).astype(np.int64)
    gap_ms = 50.0 * np.exp(rng.normal(0.0, 0.35, n))
    if cheat:
        steady = np.full(n, 30.0) + rng.normal(0.0, 0.3, n)
        gap_ms = strength * steady + (1 - strength) * gap_ms
        period = max(2, round(6 - 3 * strength))
        swing = (np.arange(n) % period) == 0
        kind = np.where(swing, EVENT_SWING, np.where(kind == EVENT_SWING, EVENT_MOVEMENT, kind))
    gap_ms = np.maximum(gap_ms, 1.0)
    gap_ms[0] = 0.0
    has_rot = (kind == EVENT_MOVEMENT) & (rng.random(n) < 0.9)
    sigma = 2.0 * scale
    dyaw = rng.normal(0.0, sigma, n)
    flick = rng.random(n) < 0.04
    dyaw = np.where(flick, rng.normal(0.0, 6.0 * sigma, n), dyaw)
    dpitch = rng.normal(0.0, sigma / 2, n)
    if cheat:
        snap = rng.random(n) < 0.25 * strength
        smooth = rng.normal(0.0, sigma, n)
        sign = rng.choice([-1.0, 1.0], n)
        cheat_yaw = np.where(snap, sign * rng.uniform(60, 170, n), smooth)
        dyaw = strength * cheat_yaw + (1 - strength) * dyaw
        dpitch = (
            strength * np.where(snap, rng.normal(0, 15, n), 0.5 * smooth) + (1 - strength) * dpitch
        )
    dyaw = np.where(has_rot, dyaw, 0.0)
    dpitch = np.where(has_rot, dpitch, 0.0)
    dt_s = np.maximum(gap_ms / 1000.0, 1e-3)
    speed = np.hypot(dyaw, dpitch) / dt_s
    accel = np.zeros(n)
    accel[1:] = (speed[1:] - speed[:-1]) / dt_s[1:]
    is_move = kind == EVENT_MOVEMENT
    has_pos = is_move & (rng.random(n) < 0.7)
    attack = kind == EVENT_INTERACT
    swing_t = np.zeros(n)
    since = 0.0
    for i in range(n):  # time since the previous swing frame, only reported on attack frames
        since = 0.0 if kind[i] == EVENT_SWING else min(10.0, since + gap_ms[i] / 1000.0)
        swing_t[i] = since
    if cheat:
        swing_t = np.where(attack, strength * 0.0 + (1 - strength) * swing_t, 0.0)
    swing_t = np.where(attack, swing_t, 0.0)
    angle_scale = (1 - strength) * 8.0 + strength * 0.8 if cheat else 8.0
    angle = np.where(attack, np.abs(rng.normal(0.0, angle_scale, n)), 0.0)
    edges = np.asarray(DEFAULT_DT_EDGES_MS)
    bucket = np.searchsorted(edges, gap_ms, side="right")
    bucket[0] = len(edges) + 1
    cols = {
        "packet_type": np.vectorize(_PKT.get)(kind),
        "direction": np.ones(n),
        "event_kind": kind,
        "domain": np.vectorize(_DOMAIN.get)(kind),
        "action": np.where(attack, 2, 0),
        "dt_bucket": bucket,
        "log_dt": np.log1p(gap_ms),
        "has_pos_delta": has_pos,
        "dx": np.where(has_pos, rng.normal(0, 0.2, n), 0.0),
        "dy": np.where(has_pos, rng.normal(0, 0.05, n), 0.0),
        "dz": np.where(has_pos, rng.normal(0, 0.2, n), 0.0),
        "has_rot_delta": has_rot,
        "dyaw": dyaw,
        "dpitch": dpitch,
        "rot_speed": speed * has_rot,
        "rot_accel": accel * has_rot,
        "on_ground": np.where(is_move, rng.integers(0, 2, n), -1),
        "t_since_swing": swing_t,
        "t_since_move": np.where(attack, rng.uniform(0, 0.2, n), 0.0),
        "target_dist": np.where(attack, rng.uniform(1.0, 4.0, n), -1.0),
        "target_angle": angle,
    }
    return cols, gap_ms


def _aggregates(cols: dict[str, np.ndarray], gap_ms: np.ndarray) -> dict[str, float | None]:
    n = len(gap_ms)
    span = float(gap_ms.sum()) / 1000.0
    kind = cols["event_kind"]
    rot = cols["has_rot_delta"].astype(bool)

    def rate(count: float) -> float:
        return float(count) / span if span > 0 else 0.0

    og = cols["on_ground"]
    return {
        "cps": rate((kind == EVENT_SWING).sum()),
        "attacks_per_s": rate((cols["action"] == 2).sum()),
        "packets_per_s": rate(n),
        "movement_per_s": rate((kind == EVENT_MOVEMENT).sum()),
        "rot_var": float(cols["dyaw"][rot].var() + cols["dpitch"][rot].var())
        if rot.sum() > 1
        else 0.0,
        "rot_speed_mean": float(cols["rot_speed"][rot].mean()) if rot.any() else 0.0,
        "rot_speed_max": float(cols["rot_speed"][rot].max()) if rot.any() else 0.0,
        "on_ground_frac": float((og == 1).sum() / (og >= 0).sum()) if (og >= 0).any() else None,
        "tick_end_rate": None,
    }


def generate(cfg: SyntheticConfig | None = None) -> SyntheticTables:
    """Build the four tables. Deterministic for a given config."""
    cfg = cfg or SyntheticConfig()
    rng = np.random.default_rng(cfg.seed)
    players: list[tuple[bytes, str, float]] = []  # (pseudonym, kind, sensitivity scale)
    for kind, count in (("legit", cfg.n_legit), ("cheat", cfg.n_cheat), ("none", cfg.n_unlabelled)):
        for _ in range(count):
            players.append((rng.bytes(32), kind, float(np.exp(rng.normal(0.0, 0.3)))))

    w_rows: list[dict] = []
    t_parts: dict[str, list[np.ndarray]] = {
        k: [] for k in ("session_id", "segment", "window_index", "pos", "seq", "offset_nanos")
    }
    tok_cols: dict[str, list[np.ndarray]] = {k: [] for k in TOKEN_NAMES}
    s_rows: list[tuple[bytes, bytes, dt.datetime]] = []
    for p_i, (pseud, kind, scale) in enumerate(players):
        for s_i in range(cfg.sessions_per_player):
            sid = rng.bytes(16)
            started = _BASE_TIME + dt.timedelta(days=p_i % 20, hours=s_i * 3)
            s_rows.append((sid, pseud, started))
            seq_no = 0
            offset = 0
            for seg, ctx in enumerate(cfg.contexts):
                for w_i in range(cfg.windows_per_session):
                    cheating = kind == "cheat" and rng.random() < cfg.cheat_window_frac
                    n = cfg.frames_per_window
                    cols, gap_ms = _window(rng, n, scale, cheating, cfg.cheat_strength)
                    offs = offset + np.cumsum(np.rint(gap_ms * 1e6)).astype(np.int64)
                    agg = _aggregates(cols, gap_ms)
                    w_rows.append(
                        {
                            "session_id": sid,
                            "segment": seg,
                            "game_context": ctx,
                            "window_index": w_i,
                            "start_offset_nanos": int(offs[0]),
                            "end_offset_nanos": int(offs[-1]),
                            "first_seq": seq_no,
                            "last_seq": seq_no + n - 1,
                            "n_frames": n,
                            "cut": "full",
                            **agg,
                        }
                    )
                    t_parts["session_id"].append(np.full(n, len(s_rows) - 1))
                    t_parts["segment"].append(np.full(n, seg))
                    t_parts["window_index"].append(np.full(n, w_i))
                    t_parts["pos"].append(np.arange(n))
                    t_parts["seq"].append(seq_no + np.arange(n))
                    t_parts["offset_nanos"].append(offs)
                    for name in TOKEN_NAMES:
                        tok_cols[name].append(np.asarray(cols[name]))
                    seq_no += n
                    offset = int(offs[-1]) + 1_000_000_000

    sid_values = pa.array([r[0] for r in s_rows], pa.binary(16))
    windows = pa.Table.from_pylist(w_rows, schema=WINDOWS_SCHEMA)
    arrays = []
    for field in WINDOW_TOKENS_SCHEMA:
        if field.name == "session_id":
            arrays.append(sid_values.take(pa.array(np.concatenate(t_parts["session_id"]))))
        else:
            src = t_parts[field.name] if field.name in t_parts else tok_cols[field.name]
            arrays.append(
                pa.array(np.concatenate(src).astype(field.type.to_pandas_dtype()), field.type)
            )
    tokens = pa.Table.from_arrays(arrays, schema=WINDOW_TOKENS_SCHEMA)
    sessions = pa.table(
        {
            "session_id": pa.array([r[0] for r in s_rows], pa.binary(16)),
            "pseudonym": pa.array([r[1] for r in s_rows], pa.binary(32)),
            "started_at": pa.array([r[2] for r in s_rows], pa.timestamp("us", tz="UTC")),
        }
    )
    labelled = [(p, k) for p, k, _ in players if k != "none"]
    labels = pa.table(
        {
            "pseudonym": pa.array([p for p, _ in labelled], pa.binary(32)),
            "label": [k for _, k in labelled],
            "source": ["synthetic"] * len(labelled),
            "confidence": pa.array([1.0] * len(labelled), pa.float32()),
            "valid_from": pa.array([None] * len(labelled), pa.timestamp("us", tz="UTC")),
            "valid_to": pa.array([None] * len(labelled), pa.timestamp("us", tz="UTC")),
        }
    )
    return SyntheticTables(windows, tokens, sessions, labels)
