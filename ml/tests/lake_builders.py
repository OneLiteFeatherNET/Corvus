"""Synthetic lake ``events`` data matching docs/reference/data-lake.md exactly."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

SERVERBOUND = 1
CLIENTBOUND = 2
MS = 1_000_000
TICK_END = 27
ANIMATION = 24
INTERACT = 9
POSITION_ROTATION = 12
ENTITY_VELOCITY = 105

EVENTS_SCHEMA = pa.schema(
    [
        ("session_id", pa.binary(16)),
        ("segment", pa.uint32()),
        ("seq", pa.uint64()),
        ("offset_nanos", pa.uint64()),
        ("direction", pa.uint8()),
        ("state", pa.uint8()),
        ("packet_type", pa.uint32()),
        ("wire_packet_id", pa.int32()),
        ("payload_original_size", pa.uint32()),
        ("event_kind", pa.uint8()),
        ("has_position", pa.bool_()),
        ("has_rotation", pa.bool_()),
        ("on_ground", pa.bool_()),
        ("x", pa.float64()),
        ("y", pa.float64()),
        ("z", pa.float64()),
        ("yaw", pa.float32()),
        ("pitch", pa.float32()),
        ("entity_id", pa.int32()),
        ("action", pa.uint8()),
        ("sneaking", pa.bool_()),
        ("off_hand", pa.bool_()),
        ("block_x", pa.int32()),
        ("block_y", pa.int32()),
        ("block_z", pa.int32()),
        ("face", pa.int32()),
        ("status", pa.int32()),
        ("slot", pa.int32()),
    ]
)


def sid(n: int) -> bytes:
    return bytes([n]) * 16


@dataclass
class Frame:
    seq: int
    t_ms: float
    segment: int = 0
    direction: int = SERVERBOUND
    packet_type: int = 14
    event_kind: int = 0
    has_position: bool | None = None
    has_rotation: bool | None = None
    on_ground: bool | None = None
    xyz: tuple[float, float, float] | None = None
    yaw: float | None = None
    pitch: float | None = None
    entity_id: int | None = None
    action: int | None = None


def move(seq, t_ms, xyz=None, yaw=None, pitch=None, ground=True, **kw) -> Frame:
    return Frame(
        seq,
        t_ms,
        packet_type=12,
        event_kind=1,
        has_position=xyz is not None,
        has_rotation=yaw is not None,
        on_ground=ground,
        xyz=xyz,
        yaw=yaw,
        pitch=pitch,
        **kw,
    )


def swing(seq, t_ms, **kw) -> Frame:
    return Frame(seq, t_ms, packet_type=ANIMATION, event_kind=3, **kw)


def attack(seq, t_ms, entity_id, **kw) -> Frame:
    return Frame(seq, t_ms, packet_type=INTERACT, event_kind=2, entity_id=entity_id, action=2, **kw)


def tick_end(seq, t_ms, **kw) -> Frame:
    return Frame(seq, t_ms, packet_type=TICK_END, **kw)


def entity_teleport(seq, t_ms, entity_id, xyz, action=2, **kw) -> Frame:
    return Frame(
        seq,
        t_ms,
        direction=CLIENTBOUND,
        packet_type=ENTITY_VELOCITY,
        event_kind=7,
        entity_id=entity_id,
        action=action,
        xyz=xyz,
        **kw,
    )


def events_table(session: bytes, frames: list[Frame]) -> pa.Table:
    n = len(frames)

    def col(fn, typ):
        return pa.array([fn(f) for f in frames], typ)

    xyz = lambda i: lambda f: None if f.xyz is None else f.xyz[i]  # noqa: E731
    cols = {
        "session_id": pa.array([session] * n, pa.binary(16)),
        "segment": col(lambda f: f.segment, pa.uint32()),
        "seq": col(lambda f: f.seq, pa.uint64()),
        "offset_nanos": col(lambda f: int(f.t_ms * MS), pa.uint64()),
        "direction": col(lambda f: f.direction, pa.uint8()),
        "state": pa.array([3] * n, pa.uint8()),
        "packet_type": col(lambda f: f.packet_type, pa.uint32()),
        "wire_packet_id": pa.array([0] * n, pa.int32()),
        "payload_original_size": pa.array([0] * n, pa.uint32()),
        "event_kind": col(lambda f: f.event_kind, pa.uint8()),
        "has_position": col(lambda f: f.has_position, pa.bool_()),
        "has_rotation": col(lambda f: f.has_rotation, pa.bool_()),
        "on_ground": col(lambda f: f.on_ground, pa.bool_()),
        "x": col(xyz(0), pa.float64()),
        "y": col(xyz(1), pa.float64()),
        "z": col(xyz(2), pa.float64()),
        "yaw": col(lambda f: f.yaw, pa.float32()),
        "pitch": col(lambda f: f.pitch, pa.float32()),
        "entity_id": col(lambda f: f.entity_id, pa.int32()),
        "action": col(lambda f: f.action, pa.uint8()),
    }
    for fld in EVENTS_SCHEMA:
        cols.setdefault(fld.name, pa.nulls(n, fld.type))
    return pa.table(cols, schema=EVENTS_SCHEMA)


def write_events(path: Path, *tables: pa.Table) -> Path:
    """One Parquet file; rows sorted like the ingest would (here: as given)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.concat_tables(tables), path, compression="zstd")
    return path
