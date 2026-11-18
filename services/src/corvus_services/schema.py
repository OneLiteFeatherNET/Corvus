"""Arrow schemas of the lake tables, exactly as in docs/reference/data-lake.md section 2 (Parquet)
and section 2a (Iceberg). The two layouts differ in how raw payloads are stored."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

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

# Iceberg layout (section 2a). Raw payloads are plaintext and carry the pseudonymous player key and
# the client protocol version of their session (the payload bytes are only readable with it).
# ``player_key`` and ``protocol_version`` are null for orphan sessions (no SessionStart seen).
PLAYER_KEY_BYTES = 32
ICEBERG_EVENTS_SCHEMA = EVENTS_SCHEMA.append(pa.field("player_key", pa.binary(PLAYER_KEY_BYTES)))
ICEBERG_RAW_SCHEMA = pa.schema(
    [
        pa.field("session_id", pa.binary(16), nullable=False),
        pa.field("segment", pa.uint32(), nullable=False),
        pa.field("seq", pa.uint64(), nullable=False),
        pa.field("offset_nanos", pa.uint64(), nullable=False),
        pa.field("direction", pa.uint8(), nullable=False),
        pa.field("state", pa.uint8(), nullable=False),
        pa.field("packet_type", pa.uint32(), nullable=False),
        pa.field("wire_packet_id", pa.int32(), nullable=False),
        pa.field("protocol_version", pa.int32()),
        pa.field("player_key", pa.binary(PLAYER_KEY_BYTES)),
        pa.field("payload_original_size", pa.uint32(), nullable=False),
        pa.field("payload", pa.binary(), nullable=False),
    ]
)


# Session metadata (Iceberg only), written next to the packets so that dashboards can filter them
# by proxy, backend server or game context. Append-only: ``sessions`` gets one row per SessionStart,
# SessionEnd appends one row to ``session_ends`` and SegmentStart one row to ``segments``. Nothing
# is ever updated. ``date`` is the UTC date of the session start, as in raw, so the join raw ->
# segments on (session_id, segment) prunes the same partitions.
TIMESTAMP_TYPE = pa.timestamp("us", tz="UTC")
ICEBERG_SESSIONS_SCHEMA = pa.schema(
    [
        pa.field("session_id", pa.binary(16), nullable=False),
        pa.field("player_key", pa.binary(PLAYER_KEY_BYTES), nullable=False),
        pa.field("pseudonym_key_id", pa.string(), nullable=False),
        pa.field("client_protocol_version", pa.int32(), nullable=False),
        pa.field("observed_protocol_version", pa.int32(), nullable=False),
        pa.field("proxy_id", pa.string(), nullable=False),
        pa.field("plugin_version", pa.string(), nullable=False),
        pa.field("started_at", TIMESTAMP_TYPE, nullable=False),
        pa.field("text_packets_captured", pa.bool_(), nullable=False),
        pa.field("names_kept", pa.bool_(), nullable=False),
    ]
)
ICEBERG_SEGMENTS_SCHEMA = pa.schema(
    [
        pa.field("session_id", pa.binary(16), nullable=False),
        pa.field("segment", pa.uint32(), nullable=False),
        pa.field("backend_server", pa.string(), nullable=False),
        pa.field("game_context", pa.string(), nullable=False),
        pa.field("client_brand", pa.string(), nullable=False),
        pa.field("started_at", TIMESTAMP_TYPE, nullable=False),
    ]
)
# The counters are those of the SessionEnd message. ``player_key`` is null for orphan sessions.
ICEBERG_SESSION_ENDS_SCHEMA = pa.schema(
    [
        pa.field("session_id", pa.binary(16), nullable=False),
        pa.field("player_key", pa.binary(PLAYER_KEY_BYTES)),
        pa.field("ended_at", TIMESTAMP_TYPE, nullable=False),
        pa.field("dropped_queue_full", pa.uint64(), nullable=False),
        pa.field("dropped_privacy", pa.uint64(), nullable=False),
        pa.field("listener_errors", pa.uint64(), nullable=False),
        pa.field("dropped_transport", pa.uint64(), nullable=False),
    ]
)


@dataclass(frozen=True)
class Layout:
    """What a lake format stores: the table schemas, and whether raw payloads are encrypted."""

    name: str
    schemas: Mapping[str, pa.Schema]
    encrypts_raw: bool


PARQUET_LAYOUT = Layout("parquet", SCHEMAS, encrypts_raw=True)
ICEBERG_LAYOUT = Layout(
    "iceberg",
    {
        "events": ICEBERG_EVENTS_SCHEMA,
        "raw": ICEBERG_RAW_SCHEMA,
        "sessions": ICEBERG_SESSIONS_SCHEMA,
        "segments": ICEBERG_SEGMENTS_SCHEMA,
        "session_ends": ICEBERG_SESSION_ENDS_SCHEMA,
    },
    encrypts_raw=False,
)

# Rough in-memory cost per buffered row (fixed columns / overhead), used for the byte limits.
EVENT_ROW_BYTES = 96
RAW_ROW_OVERHEAD = 64
METADATA_ROW_BYTES = 160
