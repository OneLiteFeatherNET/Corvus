"""Builders for synthetic capture data (no I/O except explicit write_capture)."""

from __future__ import annotations

from pathlib import Path

from google.protobuf.internal.encoder import _VarintBytes

from corvus_ml.proto import pb

SID = bytes(range(16))
SERVERBOUND = pb.DIRECTION_SERVERBOUND
CLIENTBOUND = pb.DIRECTION_CLIENTBOUND
PLAY = pb.CONNECTION_STATE_PLAY


def delimited(*envelopes) -> bytes:
    out = b""
    for env in envelopes:
        body = env.SerializeToString()
        out += _VarintBytes(len(body)) + body
    return out


def write_capture(path: Path, *envelopes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(delimited(*envelopes))
    return path


def session_start(
    sid: bytes = SID,
    client: int = 770,
    observed: int = 770,
    started_ms: int = 1_000_000,
    **extra,
):
    return pb.Envelope(
        session_start=pb.SessionStart(
            session_id=sid,
            client_protocol_version=client,
            observed_protocol_version=observed,
            started_at_unix_millis=started_ms,
            plugin_version="0.1.0",
            **extra,
        )
    )


def segment_start(
    segment: int = 0,
    context: str = "pvp",
    started_ms: int = 1_000_000,
    sid: bytes = SID,
    brand: str = "vanilla",
):
    return pb.Envelope(
        segment_start=pb.SegmentStart(
            session_id=sid,
            segment=segment,
            backend_server=context,
            game_context=context,
            client_brand=brand,
            started_at_unix_millis=started_ms,
        )
    )


def frame(
    seq: int,
    offset_nanos: int = 0,
    direction: int = SERVERBOUND,
    state: int = PLAY,
    wire_id: int = 20,
    packet_type: int = 7,
    payload: bytes = b"\x00" * 10,
    original_size: int = 0,
    **event,
):
    return pb.Frame(
        seq=seq,
        offset_nanos=offset_nanos,
        direction=direction,
        state=state,
        wire_packet_id=wire_id,
        packet_type=packet_type,
        payload=payload,
        payload_original_size=original_size,
        **event,
    )


def batch(frames, segment: int = 0, sid: bytes = SID, queue_full: int = 0, privacy: int = 0):
    return pb.Envelope(
        batch=pb.CaptureBatch(
            session_id=sid,
            segment=segment,
            frames=frames,
            counters=pb.CaptureCounters(dropped_queue_full=queue_full, dropped_privacy=privacy),
        )
    )


def session_end(sid: bytes = SID, ended_ms: int = 1_010_000, **counters):
    return pb.Envelope(
        session_end=pb.SessionEnd(
            session_id=sid,
            ended_at_unix_millis=ended_ms,
            counters=pb.CaptureCounters(**counters),
        )
    )


def simple_session(frames, context: str = "pvp", sid: bytes = SID, **batch_kwargs):
    """Session of 10 s (start..end) with one segment holding the given frames."""
    return [
        session_start(sid=sid),
        segment_start(context=context, sid=sid),
        batch(frames, sid=sid, **batch_kwargs),
        session_end(sid=sid),
    ]
