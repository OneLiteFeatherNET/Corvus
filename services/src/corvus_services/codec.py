"""Frame to table-row conversion (docs/reference/data-lake.md section 2)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .crypto import encrypt_payload
from .ports import Counters

EVENT_KIND_NONE = 0
EVENT_KINDS = {
    "movement": 1,
    "interact_entity": 2,
    "swing": 3,
    "dig": 4,
    "place": 5,
    "held_item": 6,
    "entity_motion": 7,
}

Row = dict[str, Any]


def event_row(session_id: bytes, segment: int, frame: Any) -> Row:
    """One ``events`` row for a frame. Columns not used by the event kind are absent (null)."""
    row: Row = {
        "session_id": session_id,
        "segment": segment,
        "seq": frame.seq,
        "offset_nanos": frame.offset_nanos,
        "direction": frame.direction,
        "state": frame.state,
        "packet_type": frame.packet_type,
        "wire_packet_id": frame.wire_packet_id,
        "payload_original_size": frame.payload_original_size,
        "event_kind": EVENT_KIND_NONE,
    }
    kind = frame.WhichOneof("event")
    if kind is None:
        return row
    row["event_kind"] = EVENT_KINDS[kind]
    e = getattr(frame, kind)
    match kind:
        case "movement":
            row["has_position"] = e.has_position
            row["has_rotation"] = e.has_rotation
            row["on_ground"] = e.on_ground
            if e.has_position:
                row["x"], row["y"], row["z"] = e.x, e.y, e.z
            if e.has_rotation:
                row["yaw"], row["pitch"] = e.yaw, e.pitch
        case "interact_entity":
            row["entity_id"] = e.entity_id
            row["action"] = e.action
            row["sneaking"] = e.sneaking
            row["off_hand"] = e.off_hand
        case "swing":
            row["off_hand"] = e.off_hand
        case "dig":
            row["status"] = e.status
            row["block_x"], row["block_y"], row["block_z"] = e.x, e.y, e.z
            row["face"] = e.face
        case "place":
            row["block_x"], row["block_y"], row["block_z"] = e.x, e.y, e.z
            row["face"] = e.face
            row["off_hand"] = e.off_hand
        case "held_item":
            row["slot"] = e.slot
        case "entity_motion":
            row["entity_id"] = e.entity_id
            row["action"] = e.kind
            row["x"], row["y"], row["z"] = e.x, e.y, e.z
    return row


def raw_row(session_id: bytes, segment: int, frame: Any, dek: bytes) -> Row | None:
    """One encrypted ``raw`` row, or None if the frame carries no payload."""
    if not frame.payload:
        return None
    nonce, ciphertext = encrypt_payload(dek, session_id, frame.seq, bytes(frame.payload))
    return {
        "session_id": session_id,
        "segment": segment,
        "seq": frame.seq,
        "payload_original_size": frame.payload_original_size,
        "nonce": nonce,
        "ciphertext": ciphertext,
    }


def session_row(s: Any, started_at: datetime) -> Row:
    """One ``sessions`` row (Iceberg, section 2a) from a SessionStart; end data: session_ends."""
    return {
        "session_id": bytes(s.session_id),
        "player_key": bytes(s.player_pseudonym),
        "pseudonym_key_id": s.pseudonym_key_id,
        "client_protocol_version": s.client_protocol_version,
        "observed_protocol_version": s.observed_protocol_version,
        "proxy_id": s.proxy_id,
        "plugin_version": s.plugin_version,
        "started_at": started_at,
        "text_packets_captured": s.text_packets_captured,
        "names_kept": s.names_kept,
    }


def segment_row(s: Any, started_at: datetime) -> Row:
    """One ``segments`` row (Iceberg, section 2a) from a SegmentStart."""
    return {
        "session_id": bytes(s.session_id),
        "segment": s.segment,
        "backend_server": s.backend_server,
        "game_context": s.game_context,
        "client_brand": s.client_brand,
        "started_at": started_at,
        "cloudnet_task": s.cloudnet_task,
        "cloudnet_groups": list(s.cloudnet_groups),
        "cloudnet_node": s.cloudnet_node,
        "cloudnet_environment": s.cloudnet_environment,
        "stage": s.stage,
    }


def session_end_row(
    session_id: bytes, player_key: bytes | None, ended_at: datetime, counters: Counters
) -> Row:
    """One ``session_ends`` row (Iceberg, section 2a) from a SessionEnd."""
    return {
        "session_id": session_id,
        "player_key": player_key,
        "ended_at": ended_at,
        "dropped_queue_full": counters.dropped_queue_full,
        "dropped_privacy": counters.dropped_privacy,
        "listener_errors": counters.listener_errors,
        "dropped_transport": counters.dropped_transport,
    }


def plain_raw_row(
    session_id: bytes,
    segment: int,
    frame: Any,
    player_key: bytes | None,
    protocol_version: int | None,
) -> Row | None:
    """One plaintext ``raw`` row of the Iceberg layout (section 2a), or None without a payload.

    The payload is stored as received. ``player_key`` and ``protocol_version`` come from the session
    and are None for orphan sessions.
    """
    if not frame.payload:
        return None
    return {
        "session_id": session_id,
        "segment": segment,
        "seq": frame.seq,
        "offset_nanos": frame.offset_nanos,
        "direction": frame.direction,
        "state": frame.state,
        "packet_type": frame.packet_type,
        "wire_packet_id": frame.wire_packet_id,
        "protocol_version": protocol_version,
        "player_key": player_key,
        "payload_original_size": frame.payload_original_size,
        "payload": bytes(frame.payload),
    }
