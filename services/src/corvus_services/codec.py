"""Frame to table-row conversion (docs/reference/data-lake.md section 2)."""

from __future__ import annotations

from typing import Any

from .crypto import encrypt_payload

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
