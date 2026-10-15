"""Per-frame token features for one segment run of one session (incremental, numpy only).

All formulas are documented in ``docs/reference/features.md``.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections import OrderedDict

import numpy as np

from corvus_ml.features.config import FeaturizerConfig
from corvus_ml.features.constants import (
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
    MOTION_SPAWN,
    MOTION_TELEPORT,
    PACKET_CLIENT_TICK_END,
    TIME_SINCE_CAP_S,
    TOKEN_INDEX,
    TOKEN_NAMES,
)
from corvus_ml.features.frames import Frame

EYE_HEIGHT = 1.62
ENTITY_AIM_HEIGHT = 0.9
_NS = 1e9

_PACKET_TYPE, _DIRECTION, _EVENT_KIND, _DOMAIN, _ACTION = (
    TOKEN_INDEX[n] for n in ("packet_type", "direction", "event_kind", "domain", "action")
)
_DT_BUCKET, _LOG_DT = TOKEN_INDEX["dt_bucket"], TOKEN_INDEX["log_dt"]
_HAS_POS_DELTA, _DX, _DY, _DZ = (TOKEN_INDEX[n] for n in ("has_pos_delta", "dx", "dy", "dz"))
_HAS_ROT_DELTA, _DYAW, _DPITCH = (TOKEN_INDEX[n] for n in ("has_rot_delta", "dyaw", "dpitch"))
_ROT_SPEED, _ROT_ACCEL, _ON_GROUND = (
    TOKEN_INDEX[n] for n in ("rot_speed", "rot_accel", "on_ground")
)
_T_SINCE_SWING, _T_SINCE_MOVE = TOKEN_INDEX["t_since_swing"], TOKEN_INDEX["t_since_move"]
_TARGET_DIST, _TARGET_ANGLE = TOKEN_INDEX["target_dist"], TOKEN_INDEX["target_angle"]

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


class TokenExtractor:
    """Incremental token computation for one segment run (all state lives here).

    Feed the frames of a run in ``seq`` order; :meth:`step` writes the token row of a serverbound
    frame into ``out`` (a float32 row in ``TOKEN_NAMES`` order) and returns True. Clientbound
    ``entity_motion`` frames only update the entity positions and return False.
    """

    def __init__(self, cfg: FeaturizerConfig) -> None:
        self._edges_ns = [e * 1e6 for e in cfg.dt_edges_ms]
        self._no_previous = len(cfg.dt_edges_ms) + 1
        self._cache = cfg.entity_cache
        self.saw_tick_end = False
        self._last_sb: int | None = None
        self._last_swing: int | None = None
        self._last_move: int | None = None
        self._pos: tuple[float, float, float] | None = None
        self._rot: tuple[float, float] | None = None
        self._last_rot_off: int | None = None
        self._last_rot_speed: float | None = None
        self._entities: OrderedDict[int, tuple[float, float, float]] = OrderedDict()

    def step(self, fr: Frame, out: np.ndarray) -> bool:
        d, ek = fr.direction, fr.event_kind
        if d == DIRECTION_CLIENTBOUND:
            if ek == EVENT_ENTITY_MOTION:
                _update_entity(
                    self._entities, fr.entity_id, fr.action, fr.x, fr.y, fr.z, self._cache
                )
            return False
        if d != DIRECTION_SERVERBOUND:
            return False

        now = fr.offset_nanos
        row = [0.0] * len(TOKEN_NAMES)
        row[_PACKET_TYPE] = fr.packet_type
        row[_DIRECTION] = d
        row[_EVENT_KIND] = ek
        row[_DOMAIN] = _DOMAIN_OF_EVENT.get(ek, DOMAIN_OTHER)
        row[_ACTION] = fr.action
        row[_ON_GROUND] = -1
        if fr.packet_type == PACKET_CLIENT_TICK_END:
            self.saw_tick_end = True

        if self._last_sb is None:
            row[_DT_BUCKET] = self._no_previous
        else:
            dt = now - self._last_sb
            row[_DT_BUCKET] = bisect_right(self._edges_ns, dt)
            row[_LOG_DT] = math.log1p(dt / 1e6)
        self._last_sb = now

        if ek == EVENT_MOVEMENT:
            self._movement(fr, now, row)
        elif ek == EVENT_SWING:
            self._last_swing = now
        elif ek == EVENT_INTERACT and fr.action == ACTION_ATTACK:
            row[_T_SINCE_SWING] = _since(now, self._last_swing)
            row[_T_SINCE_MOVE] = _since(now, self._last_move)
            target = self._entities.get(fr.entity_id)
            if target is not None and self._pos is not None and self._rot is not None:
                row[_TARGET_DIST], row[_TARGET_ANGLE] = target_geometry(
                    self._pos, self._rot[0], self._rot[1], target
                )
            else:
                row[_TARGET_DIST] = row[_TARGET_ANGLE] = -1.0
        out[:] = row
        return True

    def _movement(self, fr: Frame, now: int, row: list[float]) -> None:
        if fr.has_position and not math.isnan(fr.x):
            cur = (fr.x, fr.y, fr.z)
            pos = self._pos
            if pos is not None:
                row[_HAS_POS_DELTA] = 1
                row[_DX] = cur[0] - pos[0]
                row[_DY] = cur[1] - pos[1]
                row[_DZ] = cur[2] - pos[2]
            self._pos = cur
        if fr.has_rotation and not math.isnan(fr.yaw):
            yaw, pitch = fr.yaw, fr.pitch
            rot = self._rot
            if rot is not None and self._last_rot_off is not None:
                dyaw = wrap_degrees(yaw - rot[0])
                dpitch = pitch - rot[1]
                dt_s = (now - self._last_rot_off) / _NS
                speed = math.hypot(dyaw, dpitch) / dt_s if dt_s > 0 else 0.0
                accel = 0.0
                if self._last_rot_speed is not None and dt_s > 0:
                    accel = (speed - self._last_rot_speed) / dt_s
                row[_HAS_ROT_DELTA] = 1
                row[_DYAW], row[_DPITCH] = dyaw, dpitch
                row[_ROT_SPEED], row[_ROT_ACCEL] = speed, accel
                self._last_rot_speed = speed
            self._rot, self._last_rot_off = (yaw, pitch), now
        ground = -1 if fr.on_ground is None else int(fr.on_ground)
        if ground >= 0:
            row[_ON_GROUND] = ground
        self._last_move = now


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
    if kind in (MOTION_TELEPORT, MOTION_SPAWN):
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
