from __future__ import annotations

from datetime import date, timedelta

from corvus_services.buffer import BufferSet
from helpers import SESSION, T0, FakeClock

DAY = date(2026, 10, 15)


def row(seq: int) -> dict:
    return {"session_id": SESSION, "seq": seq}


def make(max_rows=3, max_bytes=1000, max_age=60) -> BufferSet:
    return BufferSet(max_rows, max_bytes, timedelta(seconds=max_age))


def test_not_due_below_all_limits():
    bs = make()
    bs.get("events", DAY, T0).add_row(row(1), 10)
    assert bs.due(T0) == []


def test_due_when_row_limit_reached():
    bs = make(max_rows=2)
    b = bs.get("events", DAY, T0)
    b.add_row(row(1), 1)
    b.add_row(row(2), 1)
    assert bs.due(T0) == [("events", DAY)]


def test_due_when_byte_limit_reached():
    bs = make(max_bytes=100)
    bs.get("events", DAY, T0).add_row(row(1), 100)
    assert bs.due(T0) == [("events", DAY)]


def test_due_when_age_limit_reached_with_injected_clock():
    clock = FakeClock()
    bs = make(max_age=60)
    bs.get("events", DAY, clock()).add_row(row(1), 1)
    clock.advance(seconds=59)
    assert bs.due(clock()) == []
    clock.advance(seconds=1)
    assert bs.due(clock()) == [("events", DAY)]


def test_buffers_are_separate_per_table_and_date():
    bs = make()
    bs.get("events", DAY, T0)
    bs.get("raw", DAY, T0)
    bs.get("events", date(2026, 10, 16), T0)
    assert len(bs) == 3


def test_take_removes_buffer_and_its_bytes():
    bs = make()
    b = bs.get("events", DAY, T0)
    b.add_row(row(1), 10)
    bs.note_bytes(10)
    assert bs.take(("events", DAY)) is b
    assert bs.total_bytes == 0
    assert bs.take(("events", DAY)) is None
    assert len(bs) == 0
