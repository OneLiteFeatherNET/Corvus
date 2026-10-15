"""Capture proto ``Frame`` to the featurizer's frame record.

The mapping of a frame to column values is ``codec.event_row``, the one ingest writes to the lake
``events`` table. This module only applies the null conventions the offline reader uses
(``corvus_ml.features.pipeline._frame_columns``): floats NaN, ``entity_id`` -1, ``action`` 0,
``on_ground`` -1 when unknown, so online and offline frames are identical.
"""

from __future__ import annotations

from typing import Any

from corvus_ml.features.frames import NAN, Frame

from ..codec import event_row

ON_GROUND_UNKNOWN = -1


def feature_frame(session_id: bytes, segment: int, frame: Any) -> Frame:
    row = event_row(session_id, segment, frame)
    on_ground = row.get("on_ground")
    return Frame(
        segment=segment,
        seq=row["seq"],
        offset_nanos=row["offset_nanos"],
        direction=row["direction"],
        packet_type=row["packet_type"],
        event_kind=row["event_kind"],
        has_position=bool(row.get("has_position", False)),
        has_rotation=bool(row.get("has_rotation", False)),
        on_ground=ON_GROUND_UNKNOWN if on_ground is None else int(on_ground),
        x=row.get("x", NAN),
        y=row.get("y", NAN),
        z=row.get("z", NAN),
        yaw=row.get("yaw", NAN),
        pitch=row.get("pitch", NAN),
        entity_id=row.get("entity_id", -1),
        action=row.get("action", 0),
    )
