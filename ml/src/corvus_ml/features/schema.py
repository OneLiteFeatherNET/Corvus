"""Arrow schemas of the output tables and shared constants."""

from __future__ import annotations

import pyarrow as pa

from corvus_ml.features.constants import (  # noqa: F401 - re-exported
    ACTION_ATTACK,
    AGGREGATE_NAMES,
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
    EVENT_NONE,
    EVENT_PLACE,
    EVENT_SWING,
    MOTION_RELATIVE,
    MOTION_SPAWN,
    MOTION_TELEPORT,
    PACKET_CLIENT_TICK_END,
    TIME_SINCE_CAP_S,
    TOKEN_NAMES,
)

# Columns the reader must project from ``events``.
EVENT_COLUMNS = (
    "session_id",
    "segment",
    "seq",
    "offset_nanos",
    "direction",
    "packet_type",
    "event_kind",
    "has_position",
    "has_rotation",
    "on_ground",
    "x",
    "y",
    "z",
    "yaw",
    "pitch",
    "entity_id",
    "action",
)

SESSION_ID_TYPE = pa.binary(16)

# (name, arrow type) of the per-frame token features, in output order.
TOKEN_FEATURES: tuple[tuple[str, pa.DataType], ...] = (
    ("packet_type", pa.uint32()),
    ("direction", pa.uint8()),
    ("event_kind", pa.uint8()),
    ("domain", pa.uint8()),
    ("action", pa.uint8()),
    ("dt_bucket", pa.int16()),
    ("log_dt", pa.float32()),
    ("has_pos_delta", pa.uint8()),
    ("dx", pa.float32()),
    ("dy", pa.float32()),
    ("dz", pa.float32()),
    ("has_rot_delta", pa.uint8()),
    ("dyaw", pa.float32()),
    ("dpitch", pa.float32()),
    ("rot_speed", pa.float32()),
    ("rot_accel", pa.float32()),
    ("on_ground", pa.int8()),
    ("t_since_swing", pa.float32()),
    ("t_since_move", pa.float32()),
    ("target_dist", pa.float32()),
    ("target_angle", pa.float32()),
)

AGGREGATES: tuple[tuple[str, pa.DataType], ...] = (
    ("cps", pa.float64()),
    ("attacks_per_s", pa.float64()),
    ("packets_per_s", pa.float64()),
    ("movement_per_s", pa.float64()),
    ("rot_var", pa.float64()),
    ("rot_speed_mean", pa.float64()),
    ("rot_speed_max", pa.float64()),
    ("on_ground_frac", pa.float64()),
    ("tick_end_rate", pa.float64()),
)

WINDOWS_SCHEMA = pa.schema(
    [
        ("session_id", SESSION_ID_TYPE),
        ("segment", pa.uint32()),
        ("game_context", pa.string()),
        ("window_index", pa.uint32()),
        ("start_offset_nanos", pa.uint64()),
        ("end_offset_nanos", pa.uint64()),
        ("first_seq", pa.uint64()),
        ("last_seq", pa.uint64()),
        ("n_frames", pa.uint32()),
        ("cut", pa.string()),
        *AGGREGATES,
    ]
)

WINDOW_TOKENS_SCHEMA = pa.schema(
    [
        ("session_id", SESSION_ID_TYPE),
        ("segment", pa.uint32()),
        ("window_index", pa.uint32()),
        ("pos", pa.uint16()),
        ("seq", pa.uint64()),
        ("offset_nanos", pa.uint64()),
        *TOKEN_FEATURES,
    ]
)
