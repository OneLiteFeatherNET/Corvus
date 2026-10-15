"""Arrow schemas of the lake tables, exactly as in docs/reference/data-lake.md section 2."""

from __future__ import annotations

import pyarrow as pa

SCHEMA_VERSION = 1

EVENTS_SCHEMA = pa.schema(
    [
        pa.field("session_id", pa.binary(16), nullable=False),
        pa.field("segment", pa.uint32(), nullable=False),
        pa.field("seq", pa.uint64(), nullable=False),
        pa.field("offset_nanos", pa.uint64(), nullable=False),
        pa.field("direction", pa.uint8(), nullable=False),
        pa.field("state", pa.uint8(), nullable=False),
        pa.field("packet_type", pa.uint32(), nullable=False),
        pa.field("wire_packet_id", pa.int32(), nullable=False),
        pa.field("payload_original_size", pa.uint32(), nullable=False),
        pa.field("event_kind", pa.uint8(), nullable=False),
        pa.field("has_position", pa.bool_()),
        pa.field("has_rotation", pa.bool_()),
        pa.field("on_ground", pa.bool_()),
        pa.field("x", pa.float64()),
        pa.field("y", pa.float64()),
        pa.field("z", pa.float64()),
        pa.field("yaw", pa.float32()),
        pa.field("pitch", pa.float32()),
        pa.field("entity_id", pa.int32()),
        pa.field("action", pa.uint8()),
        pa.field("sneaking", pa.bool_()),
        pa.field("off_hand", pa.bool_()),
        pa.field("block_x", pa.int32()),
        pa.field("block_y", pa.int32()),
        pa.field("block_z", pa.int32()),
        pa.field("face", pa.int32()),
        pa.field("status", pa.int32()),
        pa.field("slot", pa.int32()),
    ]
)

RAW_SCHEMA = pa.schema(
    [
        pa.field("session_id", pa.binary(16), nullable=False),
        pa.field("segment", pa.uint32(), nullable=False),
        pa.field("seq", pa.uint64(), nullable=False),
        pa.field("payload_original_size", pa.uint32(), nullable=False),
        pa.field("nonce", pa.binary(12), nullable=False),
        pa.field("ciphertext", pa.binary(), nullable=False),
    ]
)

SCHEMAS: dict[str, pa.Schema] = {"events": EVENTS_SCHEMA, "raw": RAW_SCHEMA}

# Rough in-memory cost per buffered row (fixed columns / overhead), used for the byte limits.
EVENT_ROW_BYTES = 96
RAW_ROW_OVERHEAD = 64
