"""Feature constants shared by the incremental core, the adapters and the Arrow schemas.

Deliberately free of pyarrow: the online scoring service imports this without the lake stack.
"""

from __future__ import annotations

DIRECTION_SERVERBOUND = 1
DIRECTION_CLIENTBOUND = 2

EVENT_NONE = 0
EVENT_MOVEMENT = 1
EVENT_INTERACT = 2
EVENT_SWING = 3
EVENT_DIG = 4
EVENT_PLACE = 5
EVENT_HELD_ITEM = 6
EVENT_ENTITY_MOTION = 7

ACTION_ATTACK = 2
MOTION_RELATIVE = 1
MOTION_TELEPORT = 2
MOTION_SPAWN = 4

# Vocabulary id of C_CLIENT_TICK_END (checked against vocabulary.json in the tests).
PACKET_CLIENT_TICK_END = 27

DOMAIN_OTHER = 0
DOMAIN_MOVEMENT = 1
DOMAIN_COMBAT = 2
DOMAIN_INTERACTION = 3

# Seconds; "time since" features saturate here, also used when the reference event never happened.
TIME_SINCE_CAP_S = 10.0

# Token features per serverbound frame, in the column order of ``CompletedWindow.tokens``.
# Values are stored as float32; every integer feature is far below 2**24, so this is lossless.
TOKEN_NAMES: tuple[str, ...] = (
    "packet_type",
    "direction",
    "event_kind",
    "domain",
    "action",
    "dt_bucket",
    "log_dt",
    "has_pos_delta",
    "dx",
    "dy",
    "dz",
    "has_rot_delta",
    "dyaw",
    "dpitch",
    "rot_speed",
    "rot_accel",
    "on_ground",
    "t_since_swing",
    "t_since_move",
    "target_dist",
    "target_angle",
)
TOKEN_INDEX: dict[str, int] = {name: i for i, name in enumerate(TOKEN_NAMES)}

# Window aggregates in the order of ``CompletedWindow.aggregates``.
AGGREGATE_NAMES: tuple[str, ...] = (
    "cps",
    "attacks_per_s",
    "packets_per_s",
    "movement_per_s",
    "rot_var",
    "rot_speed_mean",
    "rot_speed_max",
    "on_ground_frac",
    "tick_end_rate",
)
# Aggregates that are NaN in the vector (null in the ``windows`` table) when undefined.
NULLABLE_AGGREGATES: frozenset[str] = frozenset({"on_ground_frac", "tick_end_rate"})
