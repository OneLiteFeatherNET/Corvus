"""Iceberg lake sink on a local SqlCatalog (sqlite in tmp_path): no network, no real sleeps."""

from __future__ import annotations

import random
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.exceptions import CommitFailedException, NoSuchTableError
from pyiceberg.schema import Schema
from pyiceberg.types import (
    BinaryType,
    DateType,
    FixedType,
    ListType,
    LongType,
    NestedField,
    StringType,
    TimestamptzType,
)

from corvus_services.buffer import TableBuffer
from corvus_services.crypto import Keyring
from corvus_services.iceberg_sink import (
    ICEBERG_SCHEMAS,
    MAX_COMMIT_ATTEMPTS,
    IcebergSink,
    iceberg_schema,
)
from corvus_services.pipeline import IngestPipeline
from corvus_services.schema import EVENT_ROW_BYTES, ICEBERG_LAYOUT, RAW_ROW_OVERHEAD
from helpers import (
    KEK,
    PSEUDONYM,
    SESSION,
    T0,
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

DAY = date(2026, 10, 15)
OTHER_DAY = date(2026, 10, 16)
CLOUDNET_FIELDS = {
    "cloudnet_task": "Lobby-Staging",
    "cloudnet_groups": ["Lobby", "Staging"],
    "cloudnet_node": "node-1",
    "cloudnet_environment": "MINECRAFT_SERVER",
    "stage": "staging",
}


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
    buf = TableBuffer("events", day, T0, ICEBERG_LAYOUT.schemas["events"])
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
                "player_key": PSEUDONYM,
            },
            EVENT_ROW_BYTES,
        )
    return buf


def raw_buffer(seqs: list[int], day: date = DAY) -> TableBuffer:
    buf = TableBuffer("raw", day, T0, ICEBERG_LAYOUT.schemas["raw"])
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
                "protocol_version": 769,
                "player_key": PSEUDONYM,
                "payload_original_size": 2,
                "payload": b"ab",
            },
            RAW_ROW_OVERHEAD,
        )
    return buf


RAW_COLUMNS = [
    "session_id",
    "segment",
    "seq",
    "offset_nanos",
    "direction",
    "state",
    "packet_type",
    "wire_packet_id",
    "protocol_version",
    "player_key",
    "payload_original_size",
    "payload",
    "date",
]


def v1_raw_schema() -> Schema:
    """The layout-version-1 raw table (encrypted payload), as created before this change."""
    return Schema(
        NestedField(1, "session_id", FixedType(16), required=True),
        NestedField(2, "segment", LongType(), required=True),
        NestedField(3, "seq", LongType(), required=True),
        NestedField(4, "payload_original_size", LongType(), required=True),
        NestedField(5, "nonce", FixedType(12), required=True),
        NestedField(6, "ciphertext", BinaryType(), required=True),
        NestedField(7, "date", DateType(), required=True),
    )


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
    assert events.properties["corvus.lake-schema-version"] == "2"


def test_bootstrap_creates_raw_in_plaintext_with_player_key_and_protocol_version(catalog):
    make_sink(catalog).bootstrap()

    raw = catalog.load_table("capture.raw")
    assert raw.schema().column_names == RAW_COLUMNS
    fields = {f.name: f for f in raw.schema().fields}
    assert isinstance(fields["payload"].field_type, BinaryType)
    assert fields["player_key"].field_type == FixedType(32)
    assert not fields["player_key"].required
    assert not fields["protocol_version"].required
    assert "ciphertext" not in raw.schema().column_names
    assert "nonce" not in raw.schema().column_names


@pytest.mark.parametrize(
    ("name", "sort_names"),
    [
        ("sessions", ["session_id"]),
        ("segments", ["session_id", "segment"]),
        ("session_ends", ["session_id"]),
    ],
)
def test_bootstrap_creates_each_session_metadata_table_partitioned_by_date(
    catalog, name, sort_names
):
    make_sink(catalog).bootstrap()

    table = catalog.load_table(f"capture.{name}")
    assert table.schema().column_names == ICEBERG_SCHEMAS[name].column_names
    assert [f.name for f in table.spec().fields] == ["date"]
    sorted_by = [table.schema().find_column_name(f.source_id) for f in table.sort_order().fields]
    assert sorted_by == sort_names
    assert table.properties["corvus.lake-schema-version"] == "2"


def test_bootstrap_stores_session_times_as_timestamptz_and_ids_as_fixed_binary(catalog):
    make_sink(catalog).bootstrap()

    fields = {f.name: f for f in catalog.load_table("capture.sessions").schema().fields}
    assert isinstance(fields["started_at"].field_type, TimestamptzType)
    assert fields["session_id"].field_type == FixedType(16)
    assert fields["player_key"].field_type == FixedType(32)
    assert fields["player_key"].required


def test_bootstrap_stores_cloudnet_groups_as_a_list_of_strings(catalog):
    make_sink(catalog).bootstrap()

    fields = {f.name: f for f in catalog.load_table("capture.segments").schema().fields}
    groups = fields["cloudnet_groups"].field_type
    assert isinstance(groups, ListType)
    assert groups.element_type == StringType()
    assert groups.element_required is False
    assert fields["cloudnet_groups"].required
    assert fields["stage"].field_type == StringType()


def test_list_of_strings_maps_to_a_list_of_required_strings_with_its_own_element_id():
    arrow = pa.schema(
        [
            pa.field("a", pa.string(), nullable=False),
            pa.field("groups", pa.list_(pa.string()), nullable=False),
            pa.field("b", pa.string(), nullable=False),
        ]
    )

    schema = iceberg_schema(arrow)

    assert [f.field_id for f in schema.fields] == [1, 2, 3, 4], "top-level fields, date last"
    groups = schema.find_field("groups").field_type
    assert isinstance(groups, ListType)
    assert groups.element_id == 5, "the list element id comes after all top-level ids"


def test_bootstrap_adds_player_key_to_events(catalog):
    make_sink(catalog).bootstrap()

    events = catalog.load_table("capture.events")
    assert "player_key" in events.schema().column_names


def test_bootstrap_refuses_a_v1_table_with_a_clear_message(catalog):
    catalog.create_table(
        "capture.raw",
        v1_raw_schema(),
        properties={"format-version": "2", "corvus.lake-schema-version": "1"},
    )

    with pytest.raises(RuntimeError, match="lake schema version 1, this writer needs 2"):
        make_sink(catalog).bootstrap()


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
    catalog.create_table(
        "capture.events",
        other,
        properties={"format-version": "2", "corvus.lake-schema-version": "2"},
    )

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


async def test_commit_appends_raw_rows_as_plaintext_payloads(catalog):
    sink = make_sink(catalog)
    sink.bootstrap()

    committed = await sink.commit(raw_buffer([0, 1]))

    assert committed.bytes_written is None, "no Parquet size is known for Iceberg commits"
    assert committed.cleanup is None
    rows = catalog.load_table("capture.raw").scan().to_arrow().to_pylist()
    assert [(r["seq"], r["payload"], r["player_key"], r["date"]) for r in rows] == [
        (0, b"ab", PSEUDONYM, DAY),
        (1, b"ab", PSEUDONYM, DAY),
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

    async def send(self, env: Any, delivery: int = 1) -> FakeMessage:
        self._n += 1
        m = FakeMessage(env.SerializeToString(), f"m-{self._n}", self.log, delivery)
        await self.pipeline.handle(m)
        return m

    def rows(self, table: str) -> list[dict[str, Any]]:
        return self.sink._catalog.load_table(f"capture.{table}").scan().to_arrow().to_pylist()


class _Logging:
    def __init__(self, inner: IcebergSink, log: list[str]) -> None:
        self.inner = inner
        self.log = log
        self.layout = inner.layout

    async def commit(self, buf: TableBuffer) -> Any:
        committed = await self.inner.commit(buf)
        self.log.append(f"committed {buf.table}")
        return committed

    async def ping(self) -> None:
        await self.inner.ping()


async def test_iceberg_message_is_acked_only_after_all_its_tables_are_committed(catalog):
    rig = IcebergRig(catalog)
    try:
        session = await rig.send(session_start_env())
        m = await rig.send(batch_env([0, 1], payload=b"abc"))
        assert m.acked == 0, "frames are buffered, nothing committed yet"

        await rig.pipeline.flush_all()

        assert m.acked == 1
        assert m.naked == 0
        assert session.acked == 1
        assert len(rig.rows("events")) == 2
        assert len(rig.rows("raw")) == 2
        ack = rig.log.index(f"ack {m.msg_id}")
        commits = [i for i, e in enumerate(rig.log) if e.startswith("committed ")]
        assert len(commits) == 3, "sessions, events and raw"
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


# ----- plaintext raw rows, player key and protocol version ----------------------------------------


async def test_raw_payloads_are_stored_in_plaintext_with_the_expected_columns(catalog):
    rig = IcebergRig(catalog)
    try:
        await rig.send(session_start_env())
        await rig.send(batch_env([0], payload=b"/msg hello"))
        await rig.pipeline.flush_all()

        raw = rig.rows("raw")
        assert [r["payload"] for r in raw] == [b"/msg hello"]
        assert set(raw[0]) == set(RAW_COLUMNS)
        assert raw[0]["payload_original_size"] == len(b"/msg hello")
    finally:
        rig.executor.shutdown()


async def test_events_and_raw_rows_carry_the_player_key_and_protocol_version(catalog):
    rig = IcebergRig(catalog)
    try:
        await rig.send(session_start_env())
        await rig.send(batch_env([0, 1], payload=b"abc"))
        await rig.pipeline.flush_all()

        events = rig.rows("events")
        raw = rig.rows("raw")
        assert {r["player_key"] for r in events} == {PSEUDONYM}
        assert {r["player_key"] for r in raw} == {PSEUDONYM}
        assert {r["protocol_version"] for r in raw} == {769}
        assert all(r["date"] == DAY for r in raw)
    finally:
        rig.executor.shutdown()


async def test_orphan_frames_are_written_with_null_player_key_and_protocol_version(catalog):
    rig = IcebergRig(catalog)
    try:
        await rig.send(batch_env([0], payload=b"abc"), delivery=5)
        await rig.pipeline.flush_all()

        assert [r["player_key"] for r in rig.rows("events")] == [None]
        raw = rig.rows("raw")
        assert [(r["payload"], r["player_key"], r["protocol_version"]) for r in raw] == [
            (b"abc", None, None)
        ]
    finally:
        rig.executor.shutdown()


async def test_iceberg_mode_neither_looks_up_nor_unwraps_a_dek(catalog):
    rig = IcebergRig(catalog)
    try:

        async def forbidden(pseudonym: bytes) -> None:
            raise AssertionError("iceberg mode must not read player DEKs")

        rig.pg.get_dek = forbidden  # type: ignore[method-assign]
        await rig.send(session_start_env())
        await rig.send(batch_env([0], payload=b"abc"))
        await rig.pipeline.flush_all()

        assert len(rig.rows("raw")) == 1
    finally:
        rig.executor.shutdown()


async def test_frames_of_an_erased_player_are_acked_and_not_written(catalog):
    rig = IcebergRig(catalog)
    try:
        rig.pg.erased.add(PSEUDONYM)
        await rig.send(session_start_env())
        m = await rig.send(batch_env([0, 1], payload=b"abc"))
        await rig.pipeline.flush_all()

        assert m.acked == 1
        assert rig.rows("events") == []
        assert rig.rows("raw") == []
    finally:
        rig.executor.shutdown()


# ----- session metadata: sessions, segments, session_ends ----------------------------------------


def _session_start(text_packets: bool = False, names: bool = False) -> Any:
    env = session_start_env()
    env.session_start.text_packets_captured = text_packets
    env.session_start.names_kept = names
    return env


async def test_session_start_is_acked_only_after_its_sessions_row_is_committed(catalog):
    rig = IcebergRig(catalog)
    try:
        m = await rig.send(session_start_env())
        assert m.acked == 0, "the row waits in the sessions buffer"

        await rig.pipeline.flush_all()

        assert m.acked == 1
        assert len(rig.rows("sessions")) == 1
        ack = rig.log.index(f"ack {m.msg_id}")
        assert rig.log.index("committed sessions") < ack
    finally:
        rig.executor.shutdown()


async def test_sessions_row_carries_the_fields_of_the_session_start(catalog):
    rig = IcebergRig(catalog)
    try:
        await rig.send(_session_start(text_packets=True, names=True))
        await rig.pipeline.flush_all()

        [row] = rig.rows("sessions")
        assert row["session_id"] == SESSION
        assert row["player_key"] == PSEUDONYM
        assert row["pseudonym_key_id"] == "k1"
        assert (row["client_protocol_version"], row["observed_protocol_version"]) == (769, 769)
        assert row["proxy_id"] == "proxy-1"
        assert row["plugin_version"] == "0.1.0"
        assert row["started_at"] == T0
        assert (row["text_packets_captured"], row["names_kept"]) == (True, True)
        assert row["date"] == DAY
    finally:
        rig.executor.shutdown()


async def test_segment_row_carries_the_fields_of_the_segment_start(catalog):
    rig = IcebergRig(catalog)
    try:
        await rig.send(session_start_env())
        await rig.send(segment_env(segment=0))
        await rig.pipeline.flush_all()

        [row] = rig.rows("segments")
        assert row["session_id"] == SESSION
        assert row["segment"] == 0
        assert (row["backend_server"], row["game_context"], row["client_brand"]) == (
            "pvp-1",
            "pvp",
            "vanilla",
        )
        assert row["started_at"] == T0
        assert row["date"] == DAY, "the date of the session start, as in raw"
    finally:
        rig.executor.shutdown()


async def test_segment_with_cloudnet_fields_lands_them_in_the_segments_table(catalog):
    rig = IcebergRig(catalog)
    try:
        await rig.send(session_start_env())
        await rig.send(segment_env(segment=0, cloudnet=CLOUDNET_FIELDS))
        await rig.pipeline.flush_all()

        [row] = rig.rows("segments")
        assert row["cloudnet_task"] == "Lobby-Staging"
        assert row["cloudnet_groups"] == ["Lobby", "Staging"]
        assert row["cloudnet_node"] == "node-1"
        assert row["cloudnet_environment"] == "MINECRAFT_SERVER"
        assert row["stage"] == "staging"
    finally:
        rig.executor.shutdown()


async def test_segment_without_cloudnet_fields_stores_empty_values(catalog):
    rig = IcebergRig(catalog)
    try:
        await rig.send(session_start_env())
        await rig.send(segment_env(segment=0))
        await rig.pipeline.flush_all()

        [row] = rig.rows("segments")
        assert row["cloudnet_task"] == ""
        assert row["cloudnet_groups"] == []
        assert row["cloudnet_node"] == ""
        assert row["cloudnet_environment"] == ""
        assert row["stage"] == ""
    finally:
        rig.executor.shutdown()


async def test_raw_rows_join_their_segment_on_session_and_segment(catalog):
    rig = IcebergRig(catalog)
    try:
        await rig.send(session_start_env())
        await rig.send(segment_env(segment=0))
        await rig.send(batch_env([0], payload=b"abc"))
        await rig.pipeline.flush_all()

        segments = {(r["session_id"], r["segment"]): r for r in rig.rows("segments")}
        joined = [
            (r["payload"], segments[(r["session_id"], r["segment"])]["backend_server"])
            for r in rig.rows("raw")
        ]
        assert joined == [(b"abc", "pvp-1")]
    finally:
        rig.executor.shutdown()


async def test_session_end_appends_a_session_ends_row_and_leaves_the_sessions_row_alone(catalog):
    rig = IcebergRig(catalog)
    try:
        await rig.send(session_start_env())
        m = await rig.send(end_env(dropped_privacy=3))
        await rig.pipeline.flush_all()

        assert m.acked == 1
        assert len(rig.rows("sessions")) == 1
        [end] = rig.rows("session_ends")
        assert end["session_id"] == SESSION
        assert end["player_key"] == PSEUDONYM
        assert end["ended_at"] == T0 + timedelta(minutes=5)
        assert end["dropped_privacy"] == 3
        assert end["date"] == DAY
    finally:
        rig.executor.shutdown()


async def test_session_metadata_of_an_erased_player_is_acked_and_not_written(catalog):
    rig = IcebergRig(catalog)
    try:
        rig.pg.erased.add(PSEUDONYM)
        m = await rig.send(session_start_env())
        await rig.pipeline.flush_all()

        assert m.acked == 1
        assert rig.rows("sessions") == []
    finally:
        rig.executor.shutdown()
