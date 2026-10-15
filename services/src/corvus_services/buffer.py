"""Row buffers per (table, date) with row/byte/age flush limits and ack tickets."""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from .ports import Message
from .schema import SCHEMAS


@dataclass
class Ticket:
    """Tracks one NATS message across the buffers its frames went into.

    The message is acked only when ``pending`` (buffers not yet durably in a file) reaches zero.
    """

    msg: Message
    pending: int = 0
    failed: bool = False


BufferKey = tuple[str, date]


class TableBuffer:
    def __init__(self, table: str, day: date, now: datetime) -> None:
        self.table = table
        self.day = day
        self.first_added = now
        self.columns: dict[str, list[Any]] = {f.name: [] for f in SCHEMAS[table]}
        self.rows = 0
        self.bytes = 0
        self.session_ids: set[bytes] = set()
        self.tickets: list[Ticket] = []
        self._ticket_ids: set[int] = set()

    def add_row(self, row: dict[str, Any], size: int) -> None:
        for name, col in self.columns.items():
            col.append(row.get(name))
        self.rows += 1
        self.bytes += size
        self.session_ids.add(row["session_id"])

    def attach(self, ticket: Ticket) -> None:
        """Register the message once per buffer."""
        if id(ticket) not in self._ticket_ids:
            self._ticket_ids.add(id(ticket))
            self.tickets.append(ticket)
            ticket.pending += 1


class BufferSet:
    def __init__(self, max_rows: int, max_bytes: int, max_age: timedelta) -> None:
        self._max_rows = max_rows
        self._max_bytes = max_bytes
        self._max_age = max_age
        self._buffers: dict[BufferKey, TableBuffer] = {}
        self.total_bytes = 0

    def get(self, table: str, day: date, now: datetime) -> TableBuffer:
        key = (table, day)
        buf = self._buffers.get(key)
        if buf is None:
            buf = self._buffers[key] = TableBuffer(table, day, now)
        return buf

    def note_bytes(self, n: int) -> None:
        self.total_bytes += n

    def due(self, now: datetime) -> list[BufferKey]:
        return [
            key
            for key, b in self._buffers.items()
            if b.rows >= self._max_rows
            or b.bytes >= self._max_bytes
            or now - b.first_added >= self._max_age
        ]

    def pending_keys(self) -> list[BufferKey]:
        return list(self._buffers)

    def take(self, key: BufferKey) -> TableBuffer | None:
        buf = self._buffers.pop(key, None)
        if buf is not None:
            self.total_bytes -= buf.bytes
        return buf

    def __len__(self) -> int:
        return len(self._buffers)


def encode_parquet(buf: TableBuffer) -> bytes:
    """Sort by (session_id, seq) and encode as zstd Parquet. Runs in the worker pool."""
    table = pa.Table.from_pydict(buf.columns, schema=SCHEMAS[buf.table])
    table = table.sort_by([("session_id", "ascending"), ("seq", "ascending")])
    sink = io.BytesIO()
    pq.write_table(table, sink, compression="zstd")
    return sink.getvalue()
