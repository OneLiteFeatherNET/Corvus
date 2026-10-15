from __future__ import annotations

import pytest

from corvus_services.codec import event_row, raw_row
from corvus_services.proto import pb
from helpers import SESSION


def frame(**kw):
    f = pb.Frame(seq=3, offset_nanos=99, wire_packet_id=12, packet_type=4, **kw)
    f.direction = pb.DIRECTION_SERVERBOUND
    f.state = pb.CONNECTION_STATE_PLAY
    return f


def test_frame_without_event_has_kind_zero_and_only_base_columns():
    row = event_row(SESSION, 2, frame())
    assert row["event_kind"] == 0
    assert row["seq"] == 3
    assert row["segment"] == 2
    assert row["direction"] == pb.DIRECTION_SERVERBOUND
    assert row["state"] == pb.CONNECTION_STATE_PLAY
    assert row["packet_type"] == 4
    assert row["wire_packet_id"] == 12
    assert row["payload_original_size"] == 0
    assert "x" not in row


def test_movement_with_position_and_rotation():
    f = frame(
        movement=pb.MovementEvent(
            has_position=True,
            has_rotation=True,
            x=1.5,
            y=2.5,
            z=3.5,
            yaw=10.0,
            pitch=-5.0,
            on_ground=True,
        )
    )
    row = event_row(SESSION, 0, f)
    assert row["event_kind"] == 1
    assert (row["x"], row["y"], row["z"]) == (1.5, 2.5, 3.5)
    assert (row["yaw"], row["pitch"]) == (10.0, -5.0)
    assert row["has_position"] is True
    assert row["has_rotation"] is True
    assert row["on_ground"] is True


def test_movement_without_position_leaves_coordinates_null():
    f = frame(movement=pb.MovementEvent(has_rotation=True, yaw=1.0, pitch=2.0))
    row = event_row(SESSION, 0, f)
    assert row["has_position"] is False
    assert "x" not in row
    assert row["yaw"] == 1.0


def test_interact_entity():
    f = frame(
        interact_entity=pb.InteractEntityEvent(
            entity_id=42, action=pb.InteractEntityEvent.ACTION_ATTACK, sneaking=True, off_hand=False
        )
    )
    row = event_row(SESSION, 0, f)
    assert row["event_kind"] == 2
    assert row["entity_id"] == 42
    assert row["action"] == pb.InteractEntityEvent.ACTION_ATTACK
    assert row["sneaking"] is True
    assert row["off_hand"] is False


def test_swing():
    row = event_row(SESSION, 0, frame(swing=pb.SwingEvent(off_hand=True)))
    assert row["event_kind"] == 3
    assert row["off_hand"] is True


def test_dig():
    row = event_row(SESSION, 0, frame(dig=pb.DigEvent(status=2, x=-1, y=64, z=7, face=1)))
    assert row["event_kind"] == 4
    assert (row["status"], row["block_x"], row["block_y"], row["block_z"], row["face"]) == (
        2,
        -1,
        64,
        7,
        1,
    )


def test_place():
    row = event_row(SESSION, 0, frame(place=pb.PlaceEvent(x=1, y=2, z=3, face=4, off_hand=True)))
    assert row["event_kind"] == 5
    assert (row["block_x"], row["block_y"], row["block_z"], row["face"]) == (1, 2, 3, 4)
    assert row["off_hand"] is True


def test_held_item():
    row = event_row(SESSION, 0, frame(held_item=pb.HeldItemEvent(slot=8)))
    assert row["event_kind"] == 6
    assert row["slot"] == 8


def test_entity_motion():
    f = frame(
        entity_motion=pb.EntityMotionEvent(
            entity_id=7, kind=pb.EntityMotionEvent.KIND_VELOCITY, x=0.1, y=0.2, z=0.3
        )
    )
    row = event_row(SESSION, 0, f)
    assert row["event_kind"] == 7
    assert row["entity_id"] == 7
    assert row["action"] == pb.EntityMotionEvent.KIND_VELOCITY
    assert (row["x"], row["y"], row["z"]) == (0.1, 0.2, 0.3)


def test_event_row_records_payload_original_size_even_when_truncated():
    row = event_row(SESSION, 0, frame(payload=b"abc", payload_original_size=10))
    assert row["payload_original_size"] == 10


def test_raw_row_only_for_frames_with_payload():
    assert raw_row(SESSION, 0, frame(), b"k" * 32) is None


def test_raw_row_is_encrypted_with_fresh_nonce():
    f = frame(payload=b"secret", payload_original_size=6)
    a = raw_row(SESSION, 1, f, b"k" * 32)
    b = raw_row(SESSION, 1, f, b"k" * 32)
    assert a is not None
    assert b is not None
    assert a["ciphertext"] != b"secret"
    assert len(a["nonce"]) == 12
    assert a["nonce"] != b["nonce"]
    assert a["payload_original_size"] == 6


@pytest.mark.parametrize("kind", ["movement", "swing", "dig"])
def test_every_event_row_only_uses_schema_columns(kind):
    from corvus_services.schema import EVENTS_SCHEMA

    events = {
        "movement": pb.MovementEvent(has_position=True, has_rotation=True),
        "swing": pb.SwingEvent(),
        "dig": pb.DigEvent(),
    }
    row = event_row(SESSION, 0, frame(**{kind: events[kind]}))
    assert set(row) <= set(EVENTS_SCHEMA.names)
