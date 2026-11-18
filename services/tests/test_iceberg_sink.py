"""Iceberg lake sink on a local SqlCatalog (sqlite in tmp_path): no network, no real sleeps."""

from __future__ import annotations

import random
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.exceptions import CommitFailedException, NoSuchTableError
from pyiceberg.schema import Schema
from pyiceberg.types import LongType, NestedField

from corvus_services.buffer import TableBuffer
from corvus_services.crypto import Keyring
from corvus_services.iceberg_sink import ICEBERG_SCHEMAS, MAX_COMMIT_ATTEMPTS, IcebergSink
from corvus_services.pipeline import IngestPipeline
from corvus_services.schema import EVENT_ROW_BYTES, RAW_ROW_OVERHEAD
from helpers import (
    KEK,
    SESSION,
    T0,
    FakeCatalog,
    FakeClock,
    FakeMessage,
    FakeStore,
    batch_env,
    make_settings,
    session_start_env,
)

DAY = date(2026, 10, 15)
OTHER_DAY = date(2026, 10, 16)


@pytest.fixture
def catalog(tmp_path: Path) -> SqlCatalog:
    cat = SqlCatalog(
        "test",
        uri=f"sqlite:///{tmp_path / 'catalog.db'}",
        warehouse=f"file://{tmp_path / 'warehouse'}",
    )
    cat.create_namespace("capture")
    return cat


class Racing:
    """Catalog proxy for the failure modes: ``fail_load`` loads raise ``error`` (catalog down),
    ``conflicts`` appends raise a commit conflict first, ``error`` is raised by every append."""

    def __init__(
        self,
        real: Any,
        *,
        conflicts: int = 0,
        error: Exception | None = None,
        missing_once: bool = False,
    ) -> None:
        self.real = real
        self.conflicts = conflicts
        self.error = error
        self.missing_once = missing_once
        self.loads = 0
        self.appends = 0

    def load_table(self, ident: str) -> Any:
        self.loads += 1
        if self.missing_once:
            self.missing_once = False
            raise NoSuchTableError(ident)
        return _Table(self.real.load_table(ident), self)

    def create_table(self, *args: Any, **kwargs: Any) -> Any:
        return self.real.create_table(*args, **kwargs)

    def load_namespace_properties(self, namespace: str) -> Any:
        return self.real.load_namespace_properties(namespace)


class _Table:
    def __init__(self, table: Any, owner: Racing) -> None:
        self._table = table
        self._owner = owner

    def append(self, rows: Any) -> None:
        self._owner.appends += 1
        if self._owner.error is not None:
            raise self._owner.error
        if self._owner.conflicts > 0:
            self._owner.conflicts -= 1
            raise CommitFailedException("Table has been updated by another process")
        self._table.append(rows)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._table, name)


def events_buffer(seqs: list[int], day: date = DAY) -> TableBuffer:
    buf = TableBuffer("events", day, T0)
    for seq in seqs:
        buf.add_row(
            {
                "session_id": SESSION,
                "segment": 0,
                "seq": seq,
                "offset_nanos": seq * 1000,
                "direction": 1,
                "state": 4,
                "packet_type": 7,
                "wire_packet_id": 5,
                "payload_original_size": 0,
                "event_kind": 0,
            },
            EVENT_ROW_BYTES,
        )
    return buf


def raw_buffer(seqs: list[int], day: date = DAY) -> TableBuffer:
    buf = TableBuffer("raw", day, T0)
    for seq in seqs:
        buf.add_row(
            {
                "session_id": SESSION,
                "segment": 0,
                "seq": seq,
                "payload_original_size": 2,
                "nonce": b"\x00" * 12,
                "ciphertext": b"ct",
            },
            RAW_ROW_OVERHEAD,
        )
    return buf


def make_sink(catalog: Any, sleeps: list[float] | None = None) -> IcebergSink:
    return IcebergSink(
        catalog,
        "capture",
        sleep=(sleeps if sleeps is not None else []).append,
        rng=random.Random(7),
    )


# ----- bootstrap --------------------------------------------------------------------------------


def test_bootstrap_creates_both_tables_partitioned_by_date_and_sorted(catalog):
    make_sink(catalog).bootstrap()

    events = catalog.load_table("capture.events")
    raw = catalog.load_table("capture.raw")
    assert events.schema().column_names == ICEBERG_SCHEMAS["events"].column_names
    assert raw.schema().column_names[-1] == "date"
    assert [f.name for f in events.spec().fields] == ["date"]
    sort_names = [events.schema().find_column_name(f.source_id) for f in events.sort_order().fields]
    assert sort_names == ["session_id", "seq"]
    assert events.metadata.format_version == 2
    assert events.properties["write.parquet.compression-codec"] == "zstd"
    assert events.properties["corvus.lake-schema-version"] == "1"


def test_bootstrap_twice_changes_nothing(catalog):
    sink = make_sink(catalog)
    sink.bootstrap()
    before = catalog.load_table("capture.events").metadata_location
    sink.bootstrap()
    assert catalog.load_table("capture.events").metadata_location == before


def test_bootstrap_loads_a_table_another_replica_created_meanwhile(catalog):
    make_sink(catalog).bootstrap()  # the other replica won the race
    racing = Racing(catalog, missing_once=True)  # our load still saw no table

    make_sink(racing).bootstrap()

    assert racing.loads >= 3, "load, failed create, load again"
    assert catalog.load_table("capture.events").schema().column_names[-1] == "date"


def test_bootstrap_refuses_a_table_with_another_layout(catalog):
    other = Schema(NestedField(1, "something_else", LongType(), required=False))
    catalog.create_table("capture.events", other)

    with pytest.raises(RuntimeError, match="capture.events has columns"):
        make_sink(catalog).bootstrap()


# ----- appends ----------------------------------------------------------------------------------


async def test_commit_appends_rows_into_the_partition_of_their_date(catalog):
    sink = make_sink(catalog)
    sink.bootstrap()

    await sink.commit(events_buffer([2, 0, 1]))
    await sink.commit(events_buffer([5], day=OTHER_DAY))

    events = catalog.load_table("capture.events")
    rows = events.scan().to_arrow().to_pylist()
    assert sorted(r["seq"] for r in rows) == [0, 1, 2, 5]
    by_day = {r["seq"]: r["date"] for r in rows}
    assert by_day[0] == DAY
    assert by_day[5] == OTHER_DAY
    partitions = {tuple(f["partition"].values()) for f in events.inspect.files().to_pylist()}
    assert len(partitions) == 2, "one data file per date partition"


async def test_commit_writes_each_buffer_sorted_by_session_and_seq(catalog):
    sink = make_sink(catalog)
    sink.bootstrap()

    await sink.commit(events_buffer([3, 1, 2]))

    rows = catalog.load_table("capture.events").scan().to_arrow().to_pylist()
    assert [r["seq"] for r in rows] == [1, 2, 3]


async def test_commit_keeps_the_raw_rows_unchanged(catalog):
    sink = make_sink(catalog)
    sink.bootstrap()

    committed = await sink.commit(raw_buffer([0, 1]))

    assert committed.bytes_written is None, "no Parquet size is known for Iceberg commits"
    assert committed.cleanup is None
    rows = catalog.load_table("capture.raw").scan().to_arrow().to_pylist()
    assert [(r["seq"], r["ciphertext"], r["date"]) for r in rows] == [
        (0, b"ct", DAY),
        (1, b"ct", DAY),
    ]


# ----- commit conflicts -------------------------------------------------------------------------


async def test_a_commit_conflict_is_retried_once_after_a_fresh_table_load(catalog):
    make_sink(catalog).bootstrap()
    racing = Racing(catalog, conflicts=1)
    sleeps: list[float] = []
    sink = make_sink(racing, sleeps)

    await sink.commit(events_buffer([0, 1]))

    assert racing.appends == 2
    assert len(sleeps) == 1
    assert 0 <= sleeps[0] <= 0.2, "first retry waits at most the base backoff"
    rows = catalog.load_table("capture.events").scan().to_arrow().to_pylist()
    assert len(rows) == 2, "the conflicted attempt did not append anything"


async def test_a_persistent_conflict_gives_up_after_the_bounded_attempts(catalog):
    make_sink(catalog).bootstrap()
    racing = Racing(catalog, conflicts=10_000)
    sleeps: list[float] = []

    with pytest.raises(CommitFailedException):
        await make_sink(racing, sleeps).commit(events_buffer([0]))

    assert racing.appends == MAX_COMMIT_ATTEMPTS
    assert len(sleeps) == MAX_COMMIT_ATTEMPTS - 1
    assert all(0 <= s <= 5.0 for s in sleeps), "backoff is capped"


async def test_errors_other_than_conflicts_are_not_retried(catalog):
    make_sink(catalog).bootstrap()
    racing = Racing(catalog, error=OSError("s3 down"))
    sleeps: list[float] = []

    with pytest.raises(OSError, match="s3 down"):
        await make_sink(racing, sleeps).commit(events_buffer([0]))

    assert racing.appends == 1
    assert sleeps == []


# ----- pipeline: ack only after the commit, nak on failure --------------------------------------


class IcebergRig:
    def __init__(self, catalog: Any, log_sink: list[str] | None = None) -> None:
        self.log: list[str] = []
        self.pg = FakeCatalog(self.log)
        self.store = FakeStore(self.log)
        self.clock = FakeClock()
        self.executor = ThreadPoolExecutor(1)
        self.sink = make_sink(catalog)
        self.sink.bootstrap()
        self.pipeline = IngestPipeline(
            make_settings(workers=1),
            self.pg,
            self.store,
            Keyring(KEK, "kek-1"),
            self.executor,
            self.clock,
            sink=_Logging(self.sink, self.log),
        )
        self._n = 0

    async def send(self, env: Any) -> FakeMessage:
        self._n += 1
        m = FakeMessage(env.SerializeToString(), f"m-{self._n}", self.log)
        await self.pipeline.handle(m)
        return m

    def rows(self, table: str) -> list[dict[str, Any]]:
        return self.sink._catalog.load_table(f"capture.{table}").scan().to_arrow().to_pylist()


class _Logging:
    def __init__(self, inner: IcebergSink, log: list[str]) -> None:
        self.inner = inner
        self.log = log

    async def commit(self, buf: TableBuffer) -> Any:
        committed = await self.inner.commit(buf)
        self.log.append(f"committed {buf.table}")
        return committed

    async def ping(self) -> None:
        await self.inner.ping()


async def test_iceberg_message_is_acked_only_after_both_tables_are_committed(catalog):
    rig = IcebergRig(catalog)
    try:
        await rig.send(session_start_env())
        m = await rig.send(batch_env([0, 1], payload=b"abc"))
        assert m.acked == 0, "frames are buffered, nothing committed yet"

        await rig.pipeline.flush_all()

        assert m.acked == 1
        assert m.naked == 0
        assert len(rig.rows("events")) == 2
        assert len(rig.rows("raw")) == 2
        ack = rig.log.index(f"ack {m.msg_id}")
        commits = [i for i, e in enumerate(rig.log) if e.startswith("committed ")]
        assert len(commits) == 2
        assert all(i < ack for i in commits)
    finally:
        rig.executor.shutdown()


async def test_iceberg_mode_writes_no_lake_file_rows_and_no_s3_objects(catalog):
    rig = IcebergRig(catalog)
    try:
        await rig.send(session_start_env())
        await rig.send(batch_env([0, 1], payload=b"abc"))
        await rig.pipeline.flush_all()

        assert rig.pg.files == [], "session membership comes from Iceberg, not lake_files"
        assert rig.store.objects == {}
    finally:
        rig.executor.shutdown()


async def test_failed_iceberg_commit_naks_the_message_and_acks_nothing(catalog):
    rig = IcebergRig(catalog)
    try:
        await rig.send(session_start_env())
        m = await rig.send(batch_env([0, 1]))
        rig.sink._catalog = Racing(catalog, error=OSError("catalog down"))

        await rig.pipeline.flush_all()

        assert m.acked == 0
        assert m.naked == 1
        assert rig.rows("events") == []
    finally:
        rig.executor.shutdown()
