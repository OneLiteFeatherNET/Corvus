"""The input record of the featurizer: one row of the lake ``events`` table.

A plain :class:`~typing.NamedTuple` so the offline reader and the online consumer can build it
cheaply. Nullable event columns follow the lake convention: floats are NaN when null, integers use
the neutral value documented per field.
"""

from __future__ import annotations

from typing import NamedTuple

NAN = float("nan")


class Frame(NamedTuple):
    segment: int
    seq: int  # strictly increasing per session
    offset_nanos: int  # nanoseconds since the start of the session
    direction: int  # 1 serverbound, 2 clientbound
    packet_type: int
    event_kind: int
    has_position: bool = False
    has_rotation: bool = False
    on_ground: int | bool | None = None  # None / -1 = unknown
    x: float = NAN
    y: float = NAN
    z: float = NAN
    yaw: float = NAN
    pitch: float = NAN
    entity_id: int = -1
    action: int = 0
