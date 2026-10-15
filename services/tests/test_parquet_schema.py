"""The Parquet files must match docs/reference/data-lake.md exactly."""

from __future__ import annotations

from datetime import date

import pyarrow as pa
import pyarrow.parquet as pq

from corvus_services.buffer import TableBuffer, encode_parquet
from corvus_services.codec import event_row, raw_row
from corvus_services.proto import pb
from helpers import SESSION, T0

EVENTS_CONTRACT = {
    "session_id": pa.binary(16),
    "segment": pa.uint32(),
    "seq": pa.uint64(),
    "offset_nanos": pa.uint64(),
    "direction": pa.uint8(),
    "state": pa.uint8(),
    "packet_type": pa.uint32(),
    "wire_packet_id": pa.int32(),
    "payload_original_size": pa.uint32(),
    "event_kind": pa.uint8(),
    "has_position": pa.bool_(),
    "has_rotation": pa.bool_(),
    "on_ground": pa.bool_(),
    "x": pa.float64(),
    "y": pa.float64(),
    "z": pa.float64(),
    "yaw": pa.float32(),
    "pitch": pa.float32(),
    "entity_id": pa.int32(),
    "action": pa.uint8(),
    "sneaking": pa.bool_(),
    "off_hand": pa.bool_(),
    "block_x": pa.int32(),
    "block_y": pa.int32(),
    "block_z": pa.int32(),
    "face": pa.int32(),
    "status": pa.int32(),
    "slot": pa.int32(),
}

RAW_CONTRACT = {
    "session_id": pa.binary(16),
    "segment": pa.uint32(),
    "seq": pa.uint64(),
    "payload_original_size": pa.uint32(),
    "nonce": pa.binary(12),
    "ciphertext": pa.binary(),
}


def _frames():
    movement = pb.Frame(
        seq=1, movement=pb.MovementEvent(has_position=True, x=1.0, y=2.0, z=3.0, on_ground=True)
    )
    plain = pb.Frame(seq=0, payload=b"zz", payload_original_size=2)
    return [movement, plain]


def test_events_file_matches_contract_schema(tmp_path):
    buf = TableBuffer("events", date(2026, 10, 15), T0)
    for f in _frames():
        buf.add_row(event_row(SESSION, 0, f), 1)
    path = tmp_path / "events.parquet"
    path.write_bytes(encode_parquet(buf))

    schema = pq.read_schema(path)
    assert {f.name: f.type for f in schema} == EVENTS_CONTRACT
    assert schema.names == list(EVENTS_CONTRACT), "column order follows the contract"
    assert pq.ParquetFile(path).metadata.row_group(0).column(0).compression == "ZSTD"


def test_raw_file_matches_contract_schema(tmp_path):
    buf = TableBuffer("raw", date(2026, 10, 15), T0)
    for f in _frames():
        row = raw_row(SESSION, 0, f, b"k" * 32)
        if row:
            buf.add_row(row, 1)
    path = tmp_path / "raw.parquet"
    path.write_bytes(encode_parquet(buf))

    schema = pq.read_schema(path)
    assert {f.name: f.type for f in schema} == RAW_CONTRACT
    assert schema.names == list(RAW_CONTRACT)
    assert pq.read_table(path).num_rows == 1, "only frames with a payload get a raw row"


def test_events_are_sorted_by_session_then_seq(tmp_path):
    buf = TableBuffer("events", date(2026, 10, 15), T0)
    other = b"\x00" * 16
    for sid, seq in [(SESSION, 5), (other, 9), (SESSION, 1), (other, 2)]:
        buf.add_row(event_row(sid, 0, pb.Frame(seq=seq)), 1)
    path = tmp_path / "e.parquet"
    path.write_bytes(encode_parquet(buf))
    rows = pq.read_table(path).to_pylist()
    assert [(r["session_id"], r["seq"]) for r in rows] == [
        (other, 2),
        (other, 9),
        (SESSION, 1),
        (SESSION, 5),
    ]


def test_frame_without_event_has_null_event_columns(tmp_path):
    buf = TableBuffer("events", date(2026, 10, 15), T0)
    buf.add_row(event_row(SESSION, 0, pb.Frame(seq=0)), 1)
    path = tmp_path / "e.parquet"
    path.write_bytes(encode_parquet(buf))
    row = pq.read_table(path).to_pylist()[0]
    assert row["event_kind"] == 0
    assert row["x"] is None
    assert row["slot"] is None
