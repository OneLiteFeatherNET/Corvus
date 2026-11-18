"""Ingest pipeline: envelopes in, durable lake files (Parquet or Iceberg) and catalog rows out.

Delivery semantics (docs/reference/data-lake.md section 5) and the two idempotency mechanisms:

* Counters: every envelope that carries counters is recorded in ``corvus.session_batches`` keyed by
  ``(session_id, Nats-Msg-Id)`` in the same transaction as the counter update, so a redelivered
  message never adds its counters twice (``Catalog.apply_counters``).
* Acks: every message gets a ``Ticket``. Each (table, date) buffer that receives frames of the
  message increments ``ticket.pending``. When a buffer's file is durable (staging -> final copy ->
  catalog commit) the pending count of all its tickets is decremented, and a message is acked
  exactly when it reaches zero. A failed flush naks its tickets (redelivery after a delay) and
  poisons them so a sibling buffer cannot ack them. Duplicate rows from redelivery are tolerated
  by the contract (readers de-duplicate on ``session_id, seq``).

Iceberg only: the session metadata rows (``sessions`` at SessionStart, ``segments`` at SegmentStart,
``session_ends`` at SessionEnd) go through the same buffers and tickets, so their message is acked
only after the append. A redelivered message appends its row again; readers de-duplicate on
``session_id`` (and ``segment``). Postgres is written exactly as before.
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import Executor
from datetime import UTC, date, datetime, timedelta
from typing import Any

from google.protobuf.message import DecodeError

from .buffer import BufferKey, BufferSet, TableBuffer, Ticket
from .clock import Clock
from .codec import (
    Row,
    event_row,
    plain_raw_row,
    raw_row,
    segment_row,
    session_end_row,
    session_row,
)
from .config import Settings
from .crypto import Keyring, LruCache, UnwrapError
from .lake import ParquetSink
from .metrics import Metrics
from .ports import (
    Catalog,
    Counters,
    LakeSink,
    Message,
    ObjectStore,
    SegmentData,
    SessionData,
    SessionInfo,
    WrappedDek,
)
from .proto import pb
from .schema import EVENT_ROW_BYTES, METADATA_ROW_BYTES, RAW_ROW_OVERHEAD, Layout

log = logging.getLogger("corvus.ingest")


def _dt(millis: int) -> datetime:
    return datetime.fromtimestamp(millis / 1000, UTC)


def _counters(c: Any) -> Counters:
    return Counters(c.dropped_queue_full, c.dropped_privacy, c.listener_errors, c.dropped_transport)


class IngestPipeline:
    def __init__(
        self,
        settings: Settings,
        catalog: Catalog,
        store: ObjectStore,
        keyring: Keyring,
        executor: Executor,
        clock: Clock,
        metrics: Metrics | None = None,
        sink: LakeSink | None = None,
    ) -> None:
        self._s = settings
        self._catalog = catalog
        self._keyring = keyring
        self._clock = clock
        self._m = metrics or Metrics()
        # Where flushed buffers become durable; the default is the Parquet layout.
        self._sink: LakeSink = sink or ParquetSink(store, catalog, executor, clock)
        # Row layout of the sink: Parquet encrypts raw payloads, Iceberg stores them in plaintext.
        self._layout: Layout = self._sink.layout
        # Session metadata (sessions, segments, session_ends) goes to the lake only in Iceberg mode.
        self._writes_metadata = "sessions" in self._layout.schemas
        self._buffers = BufferSet(
            settings.flush_max_rows,
            settings.flush_max_bytes,
            timedelta(seconds=settings.flush_max_age_s),
            self._layout.schemas,
        )
        self._deks: LruCache[bytes, bytes] = LruCache(settings.dek_cache_size)
        self._sessions: LruCache[bytes, tuple[SessionInfo, datetime]] = LruCache(
            settings.session_cache_size
        )
        self._session_ttl = timedelta(seconds=settings.session_cache_ttl_s)
        self._tasks: set[asyncio.Task[None]] = set()
        self._flush_slots = asyncio.Semaphore(max(1, settings.workers or 1))
        self._inflight_bytes = 0

    # ----- message handling -------------------------------------------------------------------

    async def handle(self, msg: Message) -> None:
        """Process one message. Acks/naks it itself, either now or after the file commit."""
        try:
            env = pb.Envelope.FromString(msg.data)
        except DecodeError:
            log.warning("undecodable message %s", msg.msg_id)
            self._m.decode_errors.inc()
            await msg.term()
            return
        body = env.WhichOneof("body")
        if body is None:
            self._m.decode_errors.inc()
            await msg.term()
            return
        try:
            # The handlers return False when they deferred the message or buffered its row: the
            # message is then acked by the flush of the buffer (or redelivered), not here.
            if body == "session_start":
                if not await self._on_session_start(env.session_start, msg):
                    return
            elif body == "segment_start":
                if not await self._on_segment_start(env.segment_start, msg):
                    return
            elif body == "session_end":
                if not await self._on_session_end(env.session_end, msg):
                    return
            else:
                await self._on_batch(env.batch, msg)
                self._schedule_due()
                return
        except Exception:
            log.exception("failed to handle message %s", msg.msg_id)
            self._m.messages.labels("error").inc()
            await msg.nak(self._s.nak_delay_s)
            return
        self._m.messages.labels("ok").inc()
        await msg.ack()

    async def _defer(self, msg: Message) -> None:
        """The session is not known yet (message overtook SessionStart): retry later."""
        self._m.messages.labels("deferred").inc()
        await msg.nak(self._s.nak_delay_s)

    async def _session(self, session_id: bytes) -> SessionInfo | None:
        now = self._clock()
        cached = self._sessions.get(session_id)
        if cached is not None and cached[1] > now:
            return cached[0]
        info = await self._catalog.get_session(session_id)
        if info is None:
            self._sessions.discard(session_id)
            return None
        self._sessions.put(session_id, (info, now + self._session_ttl))
        return info

    async def _session_or_orphan(self, session_id: bytes, msg: Message) -> SessionInfo | None:
        """The session of a message. If it is unknown (the message overtook its SessionStart) and
        this is not yet the last allowed delivery, returns None so the caller defers (nak).
        On the last delivery the session is recorded as orphan (no pseudonym, no DEK)."""
        info = await self._session(session_id)
        if info is not None:
            return info
        if msg.delivery_count < self._s.unknown_session_max_deliver:
            return None
        log.warning(
            "session %s has no SessionStart after %d deliveries; orphan",
            session_id.hex(),
            msg.delivery_count,
        )
        info = await self._catalog.create_orphan_session(session_id, self._clock())
        self._m.messages.labels("orphan").inc()
        self._sessions.put(session_id, (info, self._clock() + self._session_ttl))
        return info

    async def _dek(self, pseudonym: bytes) -> bytes | None:
        dek = self._deks.get(pseudonym)
        if dek is not None:
            return dek
        stored = await self._catalog.get_dek(pseudonym)
        if stored is None:
            return None
        try:
            dek = self._keyring.unwrap(pseudonym, stored.wrapped)
        except UnwrapError:
            log.error(
                "cannot unwrap DEK (kek_id=%s, configured=%s)", stored.kek_id, self._keyring.kek_id
            )
            raise
        self._deks.put(pseudonym, dek)
        return dek

    async def _on_session_start(self, s: Any, msg: Message) -> bool:
        """Returns True when the message can be acked now."""
        data = SessionData(
            session_id=bytes(s.session_id),
            pseudonym=bytes(s.player_pseudonym),
            pseudonym_key_id=s.pseudonym_key_id,
            client_protocol=s.client_protocol_version,
            observed_protocol=s.observed_protocol_version,
            proxy_id=s.proxy_id,
            plugin_version=s.plugin_version,
            started_at=_dt(s.started_at_unix_millis),
        )
        candidate_dek, wrapped = self._keyring.new_dek(data.pseudonym)
        info, stored = await self._catalog.start_session(
            data, WrappedDek(wrapped, self._keyring.kek_id)
        )
        self._sessions.put(info.session_id, (info, self._clock() + self._session_ttl))
        if self._writes_metadata and not info.erased:
            self._buffer_row("sessions", info.partition_date, session_row(s, data.started_at), msg)
            return False
        # Iceberg stores raw payloads in plaintext: the stored DEK is never needed there.
        if not self._layout.encrypts_raw:
            return True
        if stored is not None and not info.erased and self._deks.get(data.pseudonym) is None:
            if stored.wrapped == wrapped:
                self._deks.put(data.pseudonym, candidate_dek)
            else:
                self._deks.put(data.pseudonym, self._keyring.unwrap(data.pseudonym, stored.wrapped))
        return True

    async def _on_segment_start(self, s: Any, msg: Message) -> bool:
        """Returns True when the message can be acked now (False: deferred or buffered)."""
        session_id = bytes(s.session_id)
        session = await self._session_or_orphan(session_id, msg)
        if session is None:
            await self._defer(msg)
            return False
        started_at = _dt(s.started_at_unix_millis)
        await self._catalog.start_segment(
            SegmentData(
                session_id=session_id,
                segment=s.segment,
                backend_server=s.backend_server,
                game_context=s.game_context,
                client_brand=s.client_brand,
                started_at=started_at,
            )
        )
        if self._writes_metadata and not session.erased:
            row = segment_row(s, started_at)
            self._buffer_row("segments", session.partition_date, row, msg)
            return False
        return True

    async def _on_session_end(self, e: Any, msg: Message) -> bool:
        """Returns True when the message can be acked now (False: deferred or buffered)."""
        session_id = bytes(e.session_id)
        session = await self._session_or_orphan(session_id, msg)
        if session is None:
            await self._defer(msg)
            return False
        ended_at = _dt(e.ended_at_unix_millis)
        counters = _counters(e.counters)
        await self._catalog.apply_counters(session_id, msg.msg_id, counters, ended_at)
        if self._writes_metadata and not session.erased:
            row = session_end_row(session_id, session.pseudonym, ended_at, counters)
            self._buffer_row("session_ends", session.partition_date, row, msg)
            return False
        return True

    def _buffer_row(self, table: str, day: date, row: Row, msg: Message) -> None:
        """Buffer one session-metadata row. The message is acked when its buffer is durable."""
        ticket = Ticket(msg)
        buf = self._buffers.get(table, day, self._clock())
        buf.attach(ticket)
        buf.add_row(row, METADATA_ROW_BYTES)
        self._buffers.note_bytes(METADATA_ROW_BYTES)
        self._m.messages.labels("ok").inc()
        self._update_gauge()
        self._schedule_due()

    async def _on_batch(self, b: Any, msg: Message) -> None:
        session_id = bytes(b.session_id)
        session = await self._session_or_orphan(session_id, msg)
        if session is None:
            await self._defer(msg)
            return
        if session.erased:
            self._m.messages.labels("erased").inc()
            await msg.ack()
            return
        try:
            counters = _counters(b.counters)
            if not counters.is_zero():
                await self._catalog.apply_counters(session_id, msg.msg_id, counters, None)
            dek = None
            if self._layout.encrypts_raw:
                if b.frames and session.pseudonym is not None:
                    dek = await self._dek(session.pseudonym)
                if b.frames and dek is None and not session.orphan:
                    # key shredded: the player was erased after our session cache entry was made
                    self._m.messages.labels("erased").inc()
                    await msg.ack()
                    return
        except Exception:
            log.exception("failed to handle batch %s", msg.msg_id)
            self._m.messages.labels("error").inc()
            await msg.nak(self._s.nak_delay_s)
            return

        ticket = Ticket(msg)
        now = self._clock()
        day = session.partition_date
        events_buf = raw_buf = None
        n_events = n_raw = 0
        for frame in b.frames:
            if events_buf is None:
                events_buf = self._buffers.get("events", day, now)
                events_buf.attach(ticket)
            event = event_row(session_id, b.segment, frame)
            if not self._layout.encrypts_raw:
                event["player_key"] = session.pseudonym
            events_buf.add_row(event, EVENT_ROW_BYTES)
            n_events += 1
            raw, size = self._raw_row(session, b.segment, frame, dek)
            if raw is not None:
                if raw_buf is None:
                    raw_buf = self._buffers.get("raw", day, now)
                    raw_buf.attach(ticket)
                raw_buf.add_row(raw, size)
                self._buffers.note_bytes(size)
                n_raw += 1
        self._buffers.note_bytes(n_events * EVENT_ROW_BYTES)
        self._m.frames.labels("events").inc(n_events)
        self._m.frames.labels("raw").inc(n_raw)
        self._m.messages.labels("ok").inc()
        self._update_gauge()
        if ticket.pending == 0:
            await msg.ack()

    def _raw_row(
        self, session: SessionInfo, segment: int, frame: Any, dek: bytes | None
    ) -> tuple[Row | None, int]:
        """The ``raw`` row of a frame and its size for the byte limits (None if no payload)."""
        if self._layout.encrypts_raw:
            if dek is None:
                return None, 0
            row = raw_row(session.session_id, segment, frame, dek)
            if row is None:
                return None, 0
            return row, len(row["ciphertext"]) + RAW_ROW_OVERHEAD
        row = plain_raw_row(
            session.session_id, segment, frame, session.pseudonym, session.client_protocol
        )
        if row is None:
            return None, 0
        return row, len(row["payload"]) + RAW_ROW_OVERHEAD

    # ----- flushing ---------------------------------------------------------------------------

    @property
    def buffered_bytes(self) -> int:
        return self._buffers.total_bytes + self._inflight_bytes

    def _update_gauge(self) -> None:
        self._m.buffered_bytes.set(self.buffered_bytes)

    def _schedule_due(self) -> None:
        for key in self._buffers.due(self._clock()):
            self._schedule(key)

    def tick(self) -> None:
        """Flush buffers whose age limit passed (called periodically)."""
        self._schedule_due()

    def _schedule(self, key: BufferKey) -> None:
        buf = self._buffers.take(key)
        if buf is None:
            return
        self._inflight_bytes += buf.bytes
        task = asyncio.get_running_loop().create_task(self._flush(buf))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def apply_backpressure(self) -> None:
        """If the buffered bytes exceed the cap, flush everything and wait for it."""
        if self.buffered_bytes >= self._s.buffer_cap_bytes:
            await self.flush_all()

    async def flush_all(self) -> None:
        """Flush every buffer and wait until all running flushes finished."""
        for key in self._buffers.pending_keys():
            self._schedule(key)
        await self.drain()

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def _flush(self, buf: TableBuffer) -> None:
        loop = asyncio.get_running_loop()
        started = loop.time()
        try:
            async with self._flush_slots:
                committed = await self._sink.commit(buf)
        except Exception:
            log.exception("flush of %s/%s failed (%d rows)", buf.table, buf.day, buf.rows)
            self._m.flushes.labels(buf.table, "error").inc()
            await self._fail(buf)
            self._finish(buf)
            return
        self._m.flushes.labels(buf.table, "ok").inc()
        if committed.bytes_written is not None:
            self._m.bytes.labels(buf.table).inc(committed.bytes_written)
        await self._ack(buf)
        self._m.flush_seconds.observe(loop.time() - started)
        self._finish(buf)
        if committed.cleanup is not None:
            try:
                await committed.cleanup()
            except Exception:
                log.warning(
                    "cleanup after flush of %s/%s failed (housekeeping will)", buf.table, buf.day
                )

    def _finish(self, buf: TableBuffer) -> None:
        self._inflight_bytes -= buf.bytes
        self._update_gauge()

    async def _ack(self, buf: TableBuffer) -> None:
        for ticket in buf.tickets:
            ticket.pending -= 1
            if ticket.pending == 0 and not ticket.failed:
                try:
                    await ticket.msg.ack()
                except Exception:
                    log.warning("ack failed for %s (will be redelivered)", ticket.msg.msg_id)

    async def _fail(self, buf: TableBuffer) -> None:
        for ticket in buf.tickets:
            ticket.pending -= 1
            if not ticket.failed:
                ticket.failed = True
                try:
                    await ticket.msg.nak(self._s.nak_delay_s)
                except Exception:
                    log.warning("nak failed for %s", ticket.msg.msg_id)
