"""Per-window aggregates over a token matrix (numpy only).

Window boundaries are decided incrementally by ``features.featurizer.SessionFeaturizer``.
"""

from __future__ import annotations

import numpy as np

from corvus_ml.features.constants import (
    ACTION_ATTACK,
    AGGREGATE_NAMES,
    EVENT_INTERACT,
    EVENT_MOVEMENT,
    EVENT_SWING,
    PACKET_CLIENT_TICK_END,
    TOKEN_INDEX,
)

_COL = TOKEN_INDEX


def window_aggregates(
    tokens: np.ndarray, first_offset_nanos: int, last_offset_nanos: int, segment_has_tick_end: bool
) -> np.ndarray:
    """Aggregates of one window as a float64 vector in ``AGGREGATE_NAMES`` order.

    ``tokens`` is the (n, len(TOKEN_NAMES)) float32 matrix of the window. Rates use the span
    first -> last frame as duration. Undefined nullable aggregates are NaN.
    """
    n = tokens.shape[0]
    span = (int(last_offset_nanos) - int(first_offset_nanos)) / 1e9
    ek = tokens[:, _COL["event_kind"]]

    def rate(count: int) -> float:
        return count / span if span > 0 else 0.0

    swings = int((ek == EVENT_SWING).sum())
    attacks = int(((ek == EVENT_INTERACT) & (tokens[:, _COL["action"]] == ACTION_ATTACK)).sum())
    moves = int((ek == EVENT_MOVEMENT).sum())

    rot = tokens[:, _COL["has_rot_delta"]] == 1
    dyaw = tokens[:, _COL["dyaw"]][rot].astype(np.float64)
    dpitch = tokens[:, _COL["dpitch"]][rot].astype(np.float64)
    speed = tokens[:, _COL["rot_speed"]][rot].astype(np.float64)
    rot_var = float(dyaw.var() + dpitch.var()) if len(dyaw) >= 2 else 0.0

    ground = tokens[:, _COL["on_ground"]]
    known = ground >= 0
    ground_frac = float((ground[known] == 1).mean()) if known.any() else float("nan")

    tick_rate = float("nan")
    if segment_has_tick_end:
        tick_rate = rate(int((tokens[:, _COL["packet_type"]] == PACKET_CLIENT_TICK_END).sum()))

    values = {
        "cps": rate(swings),
        "attacks_per_s": rate(attacks),
        "packets_per_s": rate(n),
        "movement_per_s": rate(moves),
        "rot_var": rot_var,
        "rot_speed_mean": float(speed.mean()) if len(speed) else 0.0,
        "rot_speed_max": float(speed.max()) if len(speed) else 0.0,
        "on_ground_frac": ground_frac,
        "tick_end_rate": tick_rate,
    }
    return np.array([values[name] for name in AGGREGATE_NAMES], dtype=np.float64)
