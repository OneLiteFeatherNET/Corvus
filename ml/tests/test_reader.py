import io
import logging

from builders import batch, delimited, frame, session_end, session_start
from corvus_ml.reader import ReadStats, iter_envelopes


def read(data: bytes):
    stats = ReadStats()
    return list(iter_envelopes(io.BytesIO(data), stats)), stats


def test_round_trip_returns_envelopes_in_order():
    sent = [session_start(), batch([frame(1)]), session_end()]

    got, stats = read(delimited(*sent))

    assert got == sent
    assert stats.envelopes == 3
    assert not stats.truncated
    assert not stats.corrupt


def test_empty_stream_yields_nothing():
    got, stats = read(b"")

    assert got == []
    assert not stats.truncated


def test_truncated_last_message_is_counted_and_earlier_messages_survive(caplog):
    data = delimited(session_start(), batch([frame(1)]))
    caplog.set_level(logging.WARNING)

    got, stats = read(data[:-3])

    assert len(got) == 1
    assert stats.truncated
    assert "truncated last message" in caplog.text


def test_truncated_length_prefix_is_counted_as_truncated():
    got, stats = read(delimited(session_start()) + b"\x80")

    assert len(got) == 1
    assert stats.truncated


def test_oversized_length_prefix_is_flagged_corrupt_without_allocating():
    huge = b"\xff\xff\xff\xff\x0f"  # varint for 4 GiB - 1

    got, stats = read(huge)

    assert got == []
    assert stats.corrupt


def test_undecodable_body_is_flagged_corrupt():
    got, stats = read(b"\x03\xff\xff\xff")

    assert got == []
    assert stats.corrupt
