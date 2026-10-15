"""Window boundaries and per-window aggregates over the tokens of one segment."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from corvus_ml.features.config import FeatureConfig
from corvus_ml.features.schema import (
    ACTION_ATTACK,
    EVENT_INTERACT,
    EVENT_MOVEMENT,
    EVENT_SWING,
    PACKET_CLIENT_TICK_END,
)
from corvus_ml.features.tokens import TokenArrays


@dataclass(frozen=True)
class Window:
    lo: int
    hi: int
    cut: str  # "full" (N frames), "duration" (max duration reached) or "end" (segment ended)


def window_bounds(offsets: np.ndarray, cfg: FeatureConfig) -> list[Window]:
    """Half-open frame ranges [lo, hi) of the windows of one segment.

    A window holds at most ``cfg.window`` frames within ``cfg.max_duration_s`` of its first frame.
    The next window starts ``n - floor(n * overlap)`` frames later (n = frames of this window).
    The window that reaches the last frame is the last one. Windows shorter than
    ``cfg.min_frames`` are dropped.
    """
    n = len(offsets)
    max_ns = int(cfg.max_duration_s * 1e9)
    out: list[Window] = []
    lo = 0
    while lo < n:
        limit = lo + cfg.window
        hi_dur = int(np.searchsorted(offsets, int(offsets[lo]) + max_ns, side="right"))
        hi = min(limit, hi_dur, n)
        if hi_dur < min(limit, n):
            cut = "duration"
        elif limit <= n:
            cut = "full"
        else:
            cut = "end"
        size = hi - lo
        if size >= cfg.min_frames:
            out.append(Window(lo, hi, cut))
        if hi >= n:
            break
        lo += max(1, size - int(size * cfg.overlap))
    return out


def aggregates(
    tokens: TokenArrays, w: Window, segment_has_tick_end: bool
) -> dict[str, float | None]:
    """Aggregates of one window; rates use the span first -> last frame as duration."""
    sl = slice(w.lo, w.hi)
    f = tokens.features
    n = w.hi - w.lo
    span = (int(tokens.offset_nanos[w.hi - 1]) - int(tokens.offset_nanos[w.lo])) / 1e9
    ek = f["event_kind"][sl]

    def rate(count: int) -> float:
        return count / span if span > 0 else 0.0

    swings = int((ek == EVENT_SWING).sum())
    attacks = int(((ek == EVENT_INTERACT) & (f["action"][sl] == ACTION_ATTACK)).sum())
    moves = int((ek == EVENT_MOVEMENT).sum())

    rot = f["has_rot_delta"][sl] == 1
    dyaw = f["dyaw"][sl][rot].astype(np.float64)
    dpitch = f["dpitch"][sl][rot].astype(np.float64)
    speed = f["rot_speed"][sl][rot].astype(np.float64)
    rot_var = float(dyaw.var() + dpitch.var()) if len(dyaw) >= 2 else 0.0

    ground = f["on_ground"][sl]
    known = ground >= 0
    ground_frac = float((ground[known] == 1).mean()) if known.any() else None

    tick_rate = None
    if segment_has_tick_end:
        tick_rate = rate(int((f["packet_type"][sl] == PACKET_CLIENT_TICK_END).sum()))

    return {
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
