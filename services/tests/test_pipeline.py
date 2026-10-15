from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pyarrow.parquet as pq
import pytest

from corvus_services.crypto import Keyring, decrypt_payload
from corvus_services.pipeline import IngestPipeline
from helpers import (
    KEK,
    PSEUDONYM,
    SESSION,
    FakeCatalog,
    FakeClock,
    FakeMessage,
    FakeStore,
    batch_env,
    end_env,
    make_settings,
    segment_env,
    session_start_env,
)


class Rig:
    def __init__(self, **settings):
        self.log: list[str] = []
        self.catalog = FakeCatalog(self.log)
        self.store = FakeStore(self.log)
        self.clock = FakeClock()
        self.executor = ThreadPoolExecutor(1)
        self.pipeline = IngestPipeline(
            make_settings(**settings),
            self.catalog,
            self.store,
            Keyring(KEK, "kek-1"),
            self.executor,
            self.clock,
        )
        self._n = 0

    def msg(self, env, msg_id: str | None = None, delivery: int = 1) -> FakeMessage:
        self._n += 1
        return FakeMessage(env.SerializeToString(), msg_id or f"m-{self._n}", self.log, delivery)

    async def send(self, env, msg_id: str | None = None, delivery: int = 1) -> FakeMessage:
        m = self.msg(env, msg_id, delivery)
        await self.pipeline.handle(m)
        return m

    def final_files(self, table: str) -> list[str]:
        return [k for k in self.store.objects if k.startswith(f"lake/{table}/")]


@pytest.fixture
def rig():
    r = Rig()
    yield r
    r.executor.shutdown()


async def test_message_is_acked_only_after_file_commit(rig):
    await rig.send(session_start_env())
    m = await rig.send(batch_env([0, 1]))
    assert m.acked == 0, "frames are buffered, not durable yet"

    await rig.pipeline.flush_all()

    assert m.acked == 1
    assert m.naked == 0
    assert rig.final_files("events")
    register = [i for i, e in enumerate(rig.log) if e.startswith("register lake/events")]
    ack = rig.log.index(f"ack {m.msg_id}")
    assert register
    assert register[0] < ack, "catalog row must exist before the ack"


async def test_staging_is_deleted_after_ack(rig):
    await rig.send(session_start_env())
    m = await rig.send(batch_env([0]))
    await rig.pipeline.flush_all()
    ack = rig.log.index(f"ack {m.msg_id}")
    deletes = [i for i, e in enumerate(rig.log) if e.startswith("delete _staging/")]
    assert deletes
    assert all(i > ack for i in deletes)
    assert not [k for k in rig.store.objects if k.startswith("_staging/")]


async def test_message_with_payload_acked_only_after_both_tables_are_durable(rig):
    await rig.send(session_start_env())
    m = await rig.send(batch_env([0, 1], payload=b"abc"))
    events_key = ("events", rig.catalog.sessions[SESSION].partition_date)
    raw_key = ("raw", rig.catalog.sessions[SESSION].partition_date)

    rig.pipeline._schedule(events_key)
    await rig.pipeline.drain()
    assert m.acked == 0, "raw rows of the same message are not durable yet"

    rig.pipeline._schedule(raw_key)
    await rig.pipeline.drain()
    assert m.acked == 1


async def test_failed_flush_naks_and_never_acks(rig):
    await rig.send(session_start_env())
    m = await rig.send(batch_env([0, 1], payload=b"abc"))
    rig.store.fail_put = True

    await rig.pipeline.flush_all()

    assert m.acked == 0
    assert m.naked == 1, "message is nak'ed once even though two buffers failed"
    assert rig.catalog.files == []


async def test_no_ack_when_second_buffer_fails_after_first_succeeded(rig):
    await rig.send(session_start_env())
    m = await rig.send(batch_env([0], payload=b"abc"))
    day = rig.catalog.sessions[SESSION].partition_date
    rig.pipeline._schedule(("events", day))
    await rig.pipeline.drain()
    rig.store.fail_put = True
    rig.pipeline._schedule(("raw", day))
    await rig.pipeline.drain()
    assert m.acked == 0
    assert m.naked == 1


async def test_redelivery_does_not_double_count_counters(rig):
    await rig.send(session_start_env())
    await rig.send(batch_env([0], dropped_transport=4), msg_id="s-1")
    await rig.send(batch_env([0], dropped_transport=4), msg_id="s-1")
    await rig.send(batch_env([1], dropped_transport=1), msg_id="s-2")
    assert rig.catalog.counters[SESSION][3] == 5


async def test_session_end_counters_are_idempotent_too(rig):
    await rig.send(session_start_env())
    await rig.send(end_env(dropped_privacy=3), msg_id="e-1")
    await rig.send(end_env(dropped_privacy=3), msg_id="e-1")
    assert rig.catalog.counters[SESSION][1] == 3
    assert SESSION in rig.catalog.ended


async def test_zero_counters_do_not_touch_the_catalog(rig):
    await rig.send(session_start_env())
    await rig.send(batch_env([0]))
    assert rig.catalog.applied == set()


async def test_erased_player_frames_are_dropped_and_message_acked(rig):
    rig.catalog.erased.add(PSEUDONYM)
    await rig.send(session_start_env())
    m = await rig.send(batch_env([0, 1], payload=b"abc"))
    assert m.acked == 1
    await rig.pipeline.flush_all()
    assert rig.store.objects == {}
    assert rig.catalog.files == []


async def test_player_erased_after_session_start_is_skipped_once_cache_expires(rig):
    await rig.send(session_start_env())
    rig.catalog.erased.add(PSEUDONYM)
    rig.clock.advance(seconds=61)
    m = await rig.send(batch_env([0], payload=b"abc"))
    assert m.acked == 1
    await rig.pipeline.flush_all()
    assert rig.catalog.files == []


async def test_batch_before_session_start_is_naked_with_delay_not_dropped(rig):
    delays: list[float] = []
    m = await rig.send(batch_env([0]))
    orig = m.nak

    async def spy(delay: float) -> None:
        delays.append(delay)
        await orig(delay)

    m.nak = spy
    await rig.pipeline.handle(m)
    assert m.naked == 2
    assert delays == [rig.pipeline._s.nak_delay_s]
    assert m.acked == 0
    assert SESSION not in rig.catalog.sessions, "no session invented on early deliveries"
    seg = await rig.send(segment_env())
    assert seg.naked == 1


async def test_batch_redelivered_after_session_start_is_processed(rig):
    m = await rig.send(batch_env([0, 1]), delivery=1)
    assert m.naked == 1
    await rig.send(session_start_env())
    m2 = await rig.send(batch_env([0, 1]), msg_id=m.msg_id, delivery=2)
    await rig.pipeline.flush_all()
    assert m2.acked == 1
    assert rig.final_files("events")
    assert not rig.catalog.sessions[SESSION].orphan


async def test_last_delivery_without_session_start_writes_events_only_and_marks_orphan(rig):
    max_deliver = rig.pipeline._s.unknown_session_max_deliver
    m = await rig.send(batch_env([0, 1], payload=b"abc"), delivery=max_deliver)
    assert m.naked == 0
    await rig.pipeline.flush_all()

    assert m.acked == 1
    session = rig.catalog.sessions[SESSION]
    assert session.orphan
    assert session.pseudonym is None
    assert rig.final_files("events")
    assert rig.final_files("raw") == [], "no pseudonym, no DEK, no raw rows"
    assert rig.catalog.deks == {}, "never create a DEK without a pseudonym"


async def test_orphan_session_keeps_producing_events_for_later_batches(rig):
    max_deliver = rig.pipeline._s.unknown_session_max_deliver
    await rig.send(batch_env([0]), delivery=max_deliver)
    m = await rig.send(batch_env([1], payload=b"x"), delivery=1)
    await rig.pipeline.flush_all()
    assert m.acked == 1
    assert rig.final_files("raw") == []


async def test_segment_start_is_recorded_and_acked(rig):
    await rig.send(session_start_env())
    m = await rig.send(segment_env(segment=2))
    assert m.acked == 1
    assert rig.catalog.segments[0].segment == 2
    assert rig.catalog.segments[0].game_context == "pvp"


async def test_batch_without_frames_is_acked_immediately(rig):
    await rig.send(session_start_env())
    m = await rig.send(batch_env([], dropped_transport=2))
    assert m.acked == 1


async def test_undecodable_message_is_terminated(rig):
    m = FakeMessage(b"\xff\xff\xff garbage", "bad", rig.log)
    await rig.pipeline.handle(m)
    assert m.termed == 1
    assert m.acked == 0


async def test_row_limit_triggers_flush_without_waiting(rig):
    r = Rig(flush_max_rows=2)
    try:
        await r.send(session_start_env())
        m = await r.send(batch_env([0, 1]))
        await r.pipeline.drain()
        assert m.acked == 1
        assert len(r.final_files("events")) == 1
    finally:
        r.executor.shutdown()


async def test_age_triggers_flush_on_tick():
    r = Rig(flush_max_age_s=30)
    try:
        await r.send(session_start_env())
        m = await r.send(batch_env([0]))
        r.pipeline.tick()
        await r.pipeline.drain()
        assert m.acked == 0
        r.clock.advance(seconds=30)
        r.pipeline.tick()
        await r.pipeline.drain()
        assert m.acked == 1
    finally:
        r.executor.shutdown()


async def test_buffer_cap_forces_flush():
    r = Rig(buffer_cap_bytes=100)
    try:
        await r.send(session_start_env())
        m = await r.send(batch_env([0, 1]))
        assert m.acked == 0
        await r.pipeline.apply_backpressure()
        assert m.acked == 1
        assert r.pipeline.buffered_bytes == 0
    finally:
        r.executor.shutdown()


async def test_file_is_registered_with_sessions_rows_and_date_partition(rig):
    await rig.send(session_start_env())
    await rig.send(batch_env([2, 0, 1]))
    await rig.pipeline.flush_all()
    record, sessions = rig.catalog.files[0]
    assert record.table_name == "events"
    assert record.row_count == 3
    assert record.s3_key.startswith("lake/events/date=2026-10-15/part-")
    assert record.size_bytes == len(rig.store.objects[record.s3_key])
    assert sessions == frozenset({SESSION})


async def test_written_files_are_sorted_and_raw_decrypts_with_the_players_dek(rig, tmp_path):
    await rig.send(session_start_env())
    await rig.send(batch_env([2, 0, 1], payload=b"abc"))
    await rig.pipeline.flush_all()

    events_key = rig.final_files("events")[0]
    raw_key = rig.final_files("raw")[0]
    (tmp_path / "e.parquet").write_bytes(rig.store.objects[events_key])
    (tmp_path / "r.parquet").write_bytes(rig.store.objects[raw_key])
    assert pq.read_table(tmp_path / "e.parquet").column("seq").to_pylist() == [0, 1, 2]
    raw = pq.read_table(tmp_path / "r.parquet").to_pylist()
    assert [r["seq"] for r in raw] == [0, 1, 2]

    stored = rig.catalog.deks[PSEUDONYM]
    dek = Keyring(KEK, "kek-1").unwrap(PSEUDONYM, stored.wrapped)
    first = raw[0]
    assert decrypt_payload(dek, SESSION, 0, first["nonce"], first["ciphertext"]) == b"abc"


async def test_second_session_of_known_player_reuses_the_stored_dek(rig):
    await rig.send(session_start_env())
    first = rig.catalog.deks[PSEUDONYM]
    await rig.send(session_start_env(session_id=b"\x07" * 16))
    assert rig.catalog.deks[PSEUDONYM] == first
    m = await rig.send(batch_env([0], session_id=b"\x07" * 16, payload=b"x"))
    await rig.pipeline.flush_all()
    assert m.acked == 1
    raw = rig.final_files("raw")
    dek = Keyring(KEK, "kek-1").unwrap(PSEUDONYM, first.wrapped)
    assert raw
    assert len(dek) == 32
