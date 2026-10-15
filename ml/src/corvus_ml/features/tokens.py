"""Per-frame token features for one segment of one session.

All formulas are documented in ``docs/reference/features.md``.
"""

from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import dataclass

import numpy as np

from corvus_ml.features.config import FeatureConfig
from corvus_ml.features.schema import (
    ACTION_ATTACK,
    DIRECTION_CLIENTBOUND,
    DIRECTION_SERVERBOUND,
    DOMAIN_COMBAT,
    DOMAIN_INTERACTION,
    DOMAIN_MOVEMENT,
    DOMAIN_OTHER,
    EVENT_DIG,
    EVENT_ENTITY_MOTION,
    EVENT_HELD_ITEM,
    EVENT_INTERACT,
    EVENT_MOVEMENT,
    EVENT_PLACE,
    EVENT_SWING,
    MOTION_RELATIVE,
    MOTION_TELEPORT,
    TIME_SINCE_CAP_S,
    TOKEN_FEATURES,
)

EYE_HEIGHT = 1.62
ENTITY_AIM_HEIGHT = 0.9
_NS = 1e9

_DOMAIN_OF_EVENT = {
    EVENT_MOVEMENT: DOMAIN_MOVEMENT,
    EVENT_INTERACT: DOMAIN_COMBAT,
    EVENT_SWING: DOMAIN_COMBAT,
    EVENT_DIG: DOMAIN_INTERACTION,
    EVENT_PLACE: DOMAIN_INTERACTION,
    EVENT_HELD_ITEM: DOMAIN_INTERACTION,
}


def wrap_degrees(delta: float) -> float:
    """Wrap an angle difference to [-180, 180)."""
    return (delta + 180.0) % 360.0 - 180.0


def look_vector(yaw: float, pitch: float) -> tuple[float, float, float]:
    """Unit look vector in Minecraft convention (yaw 0 faces +z, pitch -90 faces up)."""
    yr, pr = math.radians(yaw), math.radians(pitch)
    return (-math.sin(yr) * math.cos(pr), -math.sin(pr), math.cos(yr) * math.cos(pr))


def target_geometry(
    pos: tuple[float, float, float],
    yaw: float,
    pitch: float,
    entity: tuple[float, float, float],
) -> tuple[float, float]:
    """(distance, angle in degrees) from the eye to the entity's aim point."""
    vx = entity[0] - pos[0]
    vy = entity[1] + ENTITY_AIM_HEIGHT - (pos[1] + EYE_HEIGHT)
    vz = entity[2] - pos[2]
    dist = math.sqrt(vx * vx + vy * vy + vz * vz)
    if dist < 1e-9:
        return 0.0, 0.0
    lx, ly, lz = look_vector(yaw, pitch)
    cos = max(-1.0, min(1.0, (lx * vx + ly * vy + lz * vz) / dist))
    return dist, math.degrees(math.acos(cos))


@dataclass
class TokenArrays:
    """Token features of the serverbound frames of one segment, plus frame identity."""

    seq: np.ndarray
    offset_nanos: np.ndarray
    features: dict[str, np.ndarray]

    def __len__(self) -> int:
        return len(self.seq)


def extract_tokens(cols: dict[str, np.ndarray], cfg: FeatureConfig) -> TokenArrays:
    """Compute tokens for one segment run. ``cols`` holds all directions, ordered by seq.

    Required keys: seq, offset_nanos, direction, packet_type, event_kind, has_position,
    has_rotation, on_ground (int8, -1 = null), x, y, z, yaw, pitch (NaN = null), entity_id, action.
    """
    direction = cols["direction"]
    n_out = int((direction == DIRECTION_SERVERBOUND).sum())
    feats = {name: np.zeros(n_out, dtype=typ.to_pandas_dtype()) for name, typ in TOKEN_FEATURES}
    seq_out = np.zeros(n_out, dtype=np.uint64)
    off_out = np.zeros(n_out, dtype=np.uint64)
    edges_ns = np.asarray(cfg.dt_edges_ms, dtype=np.float64) * 1e6
    no_previous = len(cfg.dt_edges_ms) + 1

    seq, off = cols["seq"].tolist(), cols["offset_nanos"].tolist()
    dirs, ptype = direction.tolist(), cols["packet_type"].tolist()
    ekind, act = cols["event_kind"].tolist(), cols["action"].tolist()
    has_pos, has_rot = cols["has_position"].tolist(), cols["has_rotation"].tolist()
    ground = cols["on_ground"].tolist()
    xs, ys, zs = cols["x"].tolist(), cols["y"].tolist(), cols["z"].tolist()
    yaws, pitches = cols["yaw"].tolist(), cols["pitch"].tolist()
    eids = cols["entity_id"].tolist()

    last_sb: int | None = None
    last_swing: int | None = None
    last_move: int | None = None
    pos: tuple[float, float, float] | None = None
    rot: tuple[float, float] | None = None
    last_rot_off: int | None = None
    last_rot_speed: float | None = None
    entities: OrderedDict[int, tuple[float, float, float]] = OrderedDict()

    j = 0
    for i in range(len(seq)):
        d = dirs[i]
        ek = ekind[i]
        if d == DIRECTION_CLIENTBOUND:
            if ek == EVENT_ENTITY_MOTION:
                _update_entity(entities, eids[i], act[i], xs[i], ys[i], zs[i], cfg.entity_cache)
            continue
        if d != DIRECTION_SERVERBOUND:
            continue

        seq_out[j], off_out[j] = seq[i], off[i]
        f = feats
        f["packet_type"][j] = ptype[i]
        f["direction"][j] = d
        f["event_kind"][j] = ek
        f["domain"][j] = _DOMAIN_OF_EVENT.get(ek, DOMAIN_OTHER)
        f["action"][j] = act[i]
        f["on_ground"][j] = -1

        if last_sb is None:
            f["dt_bucket"][j] = no_previous
        else:
            dt = off[i] - last_sb
            f["dt_bucket"][j] = int(np.searchsorted(edges_ns, dt, side="right"))
            f["log_dt"][j] = math.log1p(dt / 1e6)
        last_sb = off[i]

        if ek == EVENT_MOVEMENT:
            if has_pos[i] and not math.isnan(xs[i]):
                cur = (xs[i], ys[i], zs[i])
                if pos is not None:
                    f["has_pos_delta"][j] = 1
                    f["dx"][j] = cur[0] - pos[0]
                    f["dy"][j] = cur[1] - pos[1]
                    f["dz"][j] = cur[2] - pos[2]
                pos = cur
            if has_rot[i] and not math.isnan(yaws[i]):
                yaw, pitch = yaws[i], pitches[i]
                if rot is not None and last_rot_off is not None:
                    dyaw = wrap_degrees(yaw - rot[0])
                    dpitch = pitch - rot[1]
                    dt_s = (off[i] - last_rot_off) / _NS
                    speed = math.hypot(dyaw, dpitch) / dt_s if dt_s > 0 else 0.0
                    accel = 0.0
                    if last_rot_speed is not None and dt_s > 0:
                        accel = (speed - last_rot_speed) / dt_s
                    f["has_rot_delta"][j] = 1
                    f["dyaw"][j], f["dpitch"][j] = dyaw, dpitch
                    f["rot_speed"][j], f["rot_accel"][j] = speed, accel
                    last_rot_speed = speed
                rot, last_rot_off = (yaw, pitch), off[i]
            if ground[i] >= 0:
                f["on_ground"][j] = ground[i]
            last_move = off[i]
        elif ek == EVENT_SWING:
            last_swing = off[i]
        elif ek == EVENT_INTERACT and act[i] == ACTION_ATTACK:
            f["t_since_swing"][j] = _since(off[i], last_swing)
            f["t_since_move"][j] = _since(off[i], last_move)
            target = entities.get(eids[i])
            if target is not None and pos is not None and rot is not None:
                f["target_dist"][j], f["target_angle"][j] = target_geometry(
                    pos, rot[0], rot[1], target
                )
            else:
                f["target_dist"][j] = f["target_angle"][j] = -1.0
        j += 1
    return TokenArrays(seq_out, off_out, feats)


def _since(now: int, then: int | None) -> float:
    if then is None:
        return TIME_SINCE_CAP_S
    return min(TIME_SINCE_CAP_S, max(0.0, (now - then) / _NS))


def _update_entity(
    entities: OrderedDict[int, tuple[float, float, float]],
    entity_id: int,
    kind: int,
    x: float,
    y: float,
    z: float,
    capacity: int,
) -> None:
    if math.isnan(x):
        return
    if kind == MOTION_TELEPORT:
        entities[entity_id] = (x, y, z)
    elif kind == MOTION_RELATIVE:
        prev = entities.get(entity_id)
        if prev is None:
            return
        entities[entity_id] = (prev[0] + x, prev[1] + y, prev[2] + z)
    else:
        return
    entities.move_to_end(entity_id)
    while len(entities) > capacity:
        entities.popitem(last=False)
