"""Erasure and retention of Iceberg rows on a local SqlCatalog in tmp_path: no network, no sleeps.

The clock is fixed (``NOW``); file ages are set with ``os.utime`` so the orphan margin is decided by
the test, not by the wall clock.
"""

from __future__ import annotations

import os
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.exceptions import CommitFailedException
from pyiceberg.expressions import In

from corvus_services.iceberg_erasure import ERASURE_PROPERTY, IcebergCleanup
from corvus_services.iceberg_maintenance import (
    expire_snapshot_ids,
    referenced_files,
    remove_orphan_files,
)
from corvus_services.iceberg_sink import ICEBERG_SCHEMAS, IcebergSink
from corvus_services.ports import ObjectInfo

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
DAY = NOW.date()
ALICE = b"\x01" * 16  # a session of the player that is erased
BOB = b"\x02" * 16  # a session of a player that stays
CAROL = b"\x03" * 16
CAROL_ORPHAN = b"\x06" * 16
PLAYER_A = b"\xa1" * 32  # pseudonym of ALICE, the erased player
PLAYER_B = b"\xb2" * 32
REQUEST = "11111111-1111-1111-1111-111111111111"
TABLES = ("events", "raw")
ALL_TABLES = ("events", "raw", "sessions", "segments", "session_ends")


class LocalFiles:
    """``TableFiles`` on the local file system (``file://`` keys, as pyiceberg writes them)."""

    def list_objects(self, prefix: str) -> list[ObjectInfo]:
        root = Path(prefix.removeprefix("file://"))
        if not root.is_dir():
            return []
        out: list[ObjectInfo] = []
        for path in sorted(root.rglob("*")):
            if path.is_file():
                st = path.stat()
                out.append(
                    ObjectInfo(
                        f"file://{path}", datetime.fromtimestamp(st.st_mtime, UTC), st.st_size
                    )
                )
        return out

    def delete_objects(self, keys: list[str]) -> None:
        for key in keys:
            Path(key.removeprefix("file://")).unlink(missing_ok=True)


class Recorder:
    def __init__(self) -> None:
        self.sleeps: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.sleeps.append(seconds)


class Lake:
    def __init__(self, catalog: SqlCatalog, warehouse: Path) -> None:
        self.catalog = catalog
        self.warehouse = warehouse

    def table(self, name: str) -> Any:
        return self.catalog.load_table(f"capture.{name}")

    def append(self, name: str, session: bytes, seqs: list[int], player_key: bytes | None) -> None:
        self.table(name).append(lake_rows(name, session, seqs, player_key))

    def sessions(self, name: str) -> set[bytes]:
        column = self.table(name).scan().to_arrow().column("session_id")
        return {bytes(s) for s in column.to_pylist()}

    def data_files_with(self, session: bytes) -> list[Path]:
        """Parquet files below the warehouse that contain rows of ``session`` (physical check)."""
        found = []
        for path in self.warehouse.rglob("*.parquet"):
            ids = pq.read_table(path, columns=["session_id"]).column("session_id").to_pylist()
            if session in ids:
                found.append(path)
        return found


def lake_rows(name: str, session: bytes, seqs: list[int], player_key: bytes | None) -> pa.Table:
    """Rows of a lake table with every required column set (values are irrelevant here)."""
    schema = ICEBERG_SCHEMAS[name].as_arrow()
    n = len(seqs)
    columns: dict[str, pa.Array] = {}
    for field in schema:
        if field.name == "session_id":
            values: list[Any] = [session] * n
        elif field.name == "player_key":
            values = [player_key] * n
        elif field.name == "seq":
            values = list(seqs)
        elif field.name == "date":
            values = [DAY] * n
        elif pa.types.is_fixed_size_binary(field.type):
            values = [bytes(field.type.byte_width)] * n
        elif pa.types.is_binary(field.type) or pa.types.is_large_binary(field.type):
            values = [b"x"] * n
        elif pa.types.is_string(field.type) or pa.types.is_large_string(field.type):
            values = [""] * n
        elif pa.types.is_timestamp(field.type):
            values = [NOW] * n
        elif pa.types.is_boolean(field.type):
            values = [False] * n
        elif pa.types.is_floating(field.type):
            values = [0.0] * n
        else:
            values = [0] * n
        columns[field.name] = pa.array(values, type=field.type)
    return pa.Table.from_pydict(columns, schema=schema)


def age_files(root: Path, mtime: datetime) -> None:
    for path in root.rglob("*"):
        if path.is_file():
            os.utime(path, (mtime.timestamp(), mtime.timestamp()))


@pytest.fixture
def lake(tmp_path: Path) -> Lake:
    warehouse = tmp_path / "warehouse"
    catalog = SqlCatalog(
        "test",
        uri=f"sqlite:///{tmp_path / 'catalog.db'}",
        warehouse=f"file://{warehouse}",
    )
    catalog.create_namespace("capture")
    IcebergSink(catalog, "capture").bootstrap()
    lk = Lake(catalog, warehouse)
    # Two players: ALICE (erased) and BOB; every table. Each append is one snapshot.
    for name in ALL_TABLES:
        lk.append(name, ALICE, [1, 2], PLAYER_A)
        lk.append(name, BOB, [1], PLAYER_B)
        lk.append(name, ALICE, [3], PLAYER_A)
    age_files(warehouse, NOW - timedelta(days=2))
    return lk


FAR_FUTURE = datetime(2100, 1, 1, tzinfo=UTC)
"""Retention tests: every snapshot written by the test is older than the cut-off."""


def cleanup(
    lake: Lake,
    *,
    recorder: Recorder | None = None,
    delete_chunk: int = 1000,
    catalog: Any = None,
    clock: datetime = NOW,
) -> IcebergCleanup:
    return IcebergCleanup(
        catalog or lake.catalog,
        "capture",
        LocalFiles(),
        clock=lambda: clock,
        sleep=recorder or Recorder(),
        rng=random.Random(7),
        maintenance_orphan_margin=timedelta(hours=24),
        delete_chunk=delete_chunk,
    )


# ----- erasure -------------------------------------------------------------------------------


def test_erasure_deletes_only_the_erased_sessions_from_events_and_raw(lake: Lake):
    assert cleanup(lake).erase(REQUEST, PLAYER_A, [ALICE]) == []

    for name in TABLES:
        assert lake.sessions(name) == {BOB}


def test_erasure_deletes_by_player_key_even_when_the_sessions_are_unknown(lake: Lake):
    assert cleanup(lake).erase(REQUEST, PLAYER_A, []) == []

    for name in TABLES:
        assert lake.sessions(name) == {BOB}


def test_erasure_deletes_null_key_rows_only_of_the_listed_sessions(lake: Lake):
    for name in TABLES:
        lake.append(name, CAROL, [7], None)  # orphan frames of a session of the player
        lake.append(name, CAROL_ORPHAN, [7], None)  # orphan frames of an unrelated session

    assert cleanup(lake).erase(REQUEST, PLAYER_A, [CAROL]) == []

    for name in TABLES:
        assert lake.sessions(name) == {BOB, CAROL_ORPHAN}


def test_erasure_removes_files_that_an_earlier_untagged_delete_had_dropped(lake: Lake):
    # ALICE leaves the table in a delete without an erasure tag (another job); the files stay.
    lake.table("events").delete(In("session_id", [ALICE]))
    lake.append("events", ALICE, [5], PLAYER_A)  # a row of the player written after that
    assert lake.data_files_with(ALICE)

    cleanup(lake).erase(REQUEST, PLAYER_A, [ALICE])

    assert lake.data_files_with(ALICE) == []


def test_erasure_never_removes_files_that_no_expired_snapshot_named(lake: Lake):
    data_dir = lake.warehouse / "capture" / "events" / "data"
    in_flight = data_dir / "in-flight.parquet"  # another writer's file, not committed yet
    old_orphan = data_dir / "old-orphan.parquet"  # left by a failed commit long ago
    in_flight.write_bytes(b"x")
    old_orphan.write_bytes(b"x")
    os.utime(in_flight, (NOW.timestamp() - 600, NOW.timestamp() - 600))
    os.utime(old_orphan, (NOW.timestamp() - 2 * 86400, NOW.timestamp() - 2 * 86400))

    cleanup(lake).erase(REQUEST, PLAYER_A, [ALICE])

    assert in_flight.exists()
    assert old_orphan.exists()


def test_erasure_removes_the_erased_rows_from_the_data_files_on_disk(lake: Lake):
    assert lake.data_files_with(ALICE)  # precondition: the rows are on disk

    cleanup(lake).erase(REQUEST, PLAYER_A, [ALICE])

    assert lake.data_files_with(ALICE) == []
    assert lake.data_files_with(BOB)  # the other player's rows are still there


def test_erasure_expires_every_snapshot_older_than_the_delete(lake: Lake):
    before = {name: {s.snapshot_id for s in lake.table(name).snapshots()} for name in TABLES}

    cleanup(lake).erase(REQUEST, PLAYER_A, [ALICE])

    for name in TABLES:
        table = lake.table(name)
        remaining = {s.snapshot_id for s in table.snapshots()}
        assert len(remaining) < len(before[name])
        assert table.current_snapshot() is not None
        assert table.current_snapshot().snapshot_id in remaining  # the delete snapshot is kept


def test_an_expired_snapshot_can_no_longer_be_scanned(lake: Lake):
    oldest = lake.table("events").snapshots()[0].snapshot_id

    cleanup(lake).erase(REQUEST, PLAYER_A, [ALICE])

    with pytest.raises(ValueError, match="Snapshot not found"):
        lake.table("events").scan(snapshot_id=oldest).to_arrow()


def test_erasure_of_a_player_without_rows_commits_nothing(lake: Lake):
    before = {name: len(lake.table(name).snapshots()) for name in TABLES}
    unknown = b"\x09" * 32

    assert cleanup(lake).erase(REQUEST, unknown, [CAROL]) == []

    assert {name: len(lake.table(name).snapshots()) for name in TABLES} == before


def test_erasure_deletes_in_chunks_of_the_given_size(lake: Lake):
    sessions = [CAROL, b"\x04" * 16, b"\x05" * 16, ALICE]
    for name in TABLES:
        for session in sessions[:3]:
            lake.append(name, session, [9], None)
    flaky = FlakyCatalog(lake.catalog)

    cleanup(lake, delete_chunk=2, catalog=flaky).erase(REQUEST, PLAYER_A, sessions)

    # one delete by player_key for events, raw, sessions and session_ends; the null-key deletes of
    # 4 sessions in chunks of 2 for events, raw and session_ends; the segment deletes (chunks of 2)
    assert flaky.deletes == 12
    for name in TABLES:
        assert lake.sessions(name) == {BOB}
        assert len(lake.table(name).snapshots()) == 1  # only the delete snapshot is left


def test_erasure_deletes_the_player_rows_of_sessions_and_session_ends(lake: Lake):
    assert cleanup(lake).erase(REQUEST, PLAYER_A, [ALICE]) == []

    assert lake.sessions("sessions") == {BOB}
    assert lake.sessions("session_ends") == {BOB}


def test_erasure_deletes_the_segments_of_the_erased_sessions_only(lake: Lake):
    assert cleanup(lake).erase(REQUEST, PLAYER_A, [ALICE]) == []

    assert lake.sessions("segments") == {BOB}


def _tag(snapshot: Any) -> str | None:
    if snapshot.summary is None:
        return None
    return snapshot.summary.additional_properties.get(ERASURE_PROPERTY)


@pytest.mark.parametrize("hold", ["tag", "branch"])
def test_erasure_is_held_by_a_tag_or_branch_on_an_older_snapshot(lake: Lake, hold: str):
    oldest = lake.table("events").snapshots()[0].snapshot_id
    ops = lake.table("events").manage_snapshots()
    if hold == "tag":
        ops.create_tag(oldest, "legal-hold").commit()
    else:
        ops.create_branch(oldest, "audit").commit()

    reasons = cleanup(lake).erase(REQUEST, PLAYER_A, [ALICE])

    name = "legal-hold" if hold == "tag" else "audit"
    assert reasons == [f"events: snapshots to expire are held by {name}"]
    assert lake.sessions("events") == {BOB}  # the rows are gone from the current view
    assert oldest in {s.snapshot_id for s in lake.table("events").snapshots()}  # held


def test_a_held_erasure_keeps_the_files_of_the_held_snapshots(lake: Lake):
    oldest = lake.table("events").snapshots()[0].snapshot_id
    lake.table("events").manage_snapshots().create_tag(oldest, "legal-hold").commit()

    cleanup(lake).erase(REQUEST, PLAYER_A, [ALICE])

    assert lake.data_files_with(ALICE)  # the held snapshot still reads them: nothing removed


def test_erasure_retries_a_delete_that_conflicts_with_an_append(lake: Lake):
    recorder = Recorder()
    flaky = FlakyCatalog(lake.catalog, fail_delete_times=1)

    assert cleanup(lake, recorder=recorder, catalog=flaky).erase(REQUEST, PLAYER_A, [ALICE]) == []

    assert len(recorder.sleeps) == 1
    for name in TABLES:
        assert lake.sessions(name) == {BOB}


def test_erasure_retries_the_expiry_on_a_commit_conflict(lake: Lake):
    recorder = Recorder()
    flaky = FlakyCatalog(lake.catalog, fail_expiry_times=1)

    assert cleanup(lake, recorder=recorder, catalog=flaky).erase(REQUEST, PLAYER_A, [ALICE]) == []

    assert len(recorder.sleeps) == 1
    for name in TABLES:
        assert lake.sessions(name) == {BOB}
        assert len(lake.table(name).snapshots()) == 1


def test_erasure_gives_up_after_the_last_conflict_and_raises(lake: Lake):
    recorder = Recorder()
    flaky = FlakyCatalog(lake.catalog, fail_delete_times=100)

    with pytest.raises(CommitFailedException):
        cleanup(lake, recorder=recorder, catalog=flaky).erase(REQUEST, PLAYER_A, [ALICE])

    assert len(recorder.sleeps) == 4  # five attempts, four waits


def test_other_failures_are_raised_at_once_without_a_retry(lake: Lake):
    recorder = Recorder()
    flaky = FlakyCatalog(lake.catalog, fail_delete_error=RuntimeError("catalog down"))

    with pytest.raises(RuntimeError, match="catalog down"):
        cleanup(lake, recorder=recorder, catalog=flaky).erase(REQUEST, PLAYER_A, [ALICE])

    assert recorder.sleeps == []


# ----- orphans, references and retention -----------------------------------------------------


def test_referenced_files_leave_out_the_files_of_deleted_entries(lake: Lake):
    erased_files = {str(p) for p in lake.data_files_with(ALICE)}
    assert erased_files  # ALICE's rows are in data files before the erasure

    cleanup(lake).erase(REQUEST, PLAYER_A, [ALICE])

    table = lake.table("raw")
    live = referenced_files(table)
    assert not any(f.removeprefix("file://") in erased_files for f in live)
    assert any(f.endswith(".parquet") for f in live)
    assert table.metadata_location in live


def test_orphan_files_older_than_the_margin_are_removed_and_younger_ones_kept(lake: Lake):
    data_dir = lake.warehouse / "capture" / "events" / "data"
    old = data_dir / "orphan-old.parquet"
    young = data_dir / "orphan-young.parquet"
    old.write_bytes(b"x")
    young.write_bytes(b"x")
    os.utime(old, (NOW.timestamp() - 2 * 86400, NOW.timestamp() - 2 * 86400))
    os.utime(young, (NOW.timestamp() - 600, NOW.timestamp() - 600))
    live_before = {p.name for p in data_dir.iterdir()}

    removed = remove_orphan_files(lake.table("events"), LocalFiles(), NOW, timedelta(hours=1))

    assert [Path(k.removeprefix("file://")).name for k in removed] == ["orphan-old.parquet"]
    assert not old.exists()
    assert young.exists()
    assert live_before - {"orphan-old.parquet"} == {p.name for p in data_dir.iterdir()}


def test_maintenance_removes_orphans_older_than_its_24h_margin_only(lake: Lake):
    data_dir = lake.warehouse / "capture" / "events" / "data"
    day_old = data_dir / "orphan-day.parquet"
    day_old.write_bytes(b"x")
    os.utime(day_old, (NOW.timestamp() - 25 * 3600, NOW.timestamp() - 25 * 3600))
    hour_old = data_dir / "orphan-hour.parquet"
    hour_old.write_bytes(b"x")
    os.utime(hour_old, (NOW.timestamp() - 3 * 3600, NOW.timestamp() - 3 * 3600))

    cleanup(lake).maintain(timedelta(days=7))

    assert not day_old.exists()
    assert hour_old.exists()


def test_maintenance_never_deletes_rows(lake: Lake):
    before = {name: lake.sessions(name) for name in TABLES}

    cleanup(lake, clock=FAR_FUTURE).maintain(timedelta(days=7))

    assert {name: lake.sessions(name) for name in TABLES} == before


def test_maintenance_expires_snapshots_older_than_the_retention_but_keeps_the_head(lake: Lake):
    head = {name: lake.table(name).current_snapshot().snapshot_id for name in TABLES}

    outcomes = cleanup(lake, clock=FAR_FUTURE).maintain(timedelta(days=7))

    assert sum(o.expired for o in outcomes) > 0
    for name in TABLES:
        assert [s.snapshot_id for s in lake.table(name).snapshots()] == [head[name]]


def test_maintenance_keeps_snapshots_within_the_retention(lake: Lake):
    before = {name: len(lake.table(name).snapshots()) for name in TABLES}

    outcomes = cleanup(lake, clock=NOW).maintain(timedelta(days=7))

    assert [o.expired for o in outcomes] == [0] * len(ALL_TABLES)
    assert {name: len(lake.table(name).snapshots()) for name in TABLES} == before


def test_maintenance_keeps_the_chain_from_an_older_tag(lake: Lake):
    oldest = lake.table("events").snapshots()[0].snapshot_id
    lake.table("events").manage_snapshots().create_tag(oldest, "legal-hold").commit()
    before = len(lake.table("events").snapshots())

    cleanup(lake, clock=FAR_FUTURE).maintain(timedelta(days=7))

    assert len(lake.table("events").snapshots()) == before


# ----- helpers -------------------------------------------------------------------------------


class _Flaky:
    """Proxy of a pyiceberg table: the configured operations fail first, then run for real."""

    def __init__(self, table: Any, owner: FlakyCatalog) -> None:
        self._table = table
        self._owner = owner

    def delete(self, delete_filter: Any, **kwargs: Any) -> None:
        self._owner.deletes += 1
        if self._owner.fail_delete_error is not None:
            raise self._owner.fail_delete_error
        if self._owner.fail_delete_times > 0:
            self._owner.fail_delete_times -= 1
            raise CommitFailedException("Table has been updated by another process")
        self._table.delete(delete_filter, **kwargs)

    @property
    def maintenance(self) -> _FlakyMaintenance:
        return _FlakyMaintenance(self._table, self._owner)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._table, name)


class _FlakyMaintenance:
    def __init__(self, table: Any, owner: FlakyCatalog) -> None:
        self._table = table
        self._owner = owner

    def expire_snapshots(self) -> _FlakyExpire:
        return _FlakyExpire(self._table, self._owner)


class _FlakyExpire:
    def __init__(self, table: Any, owner: FlakyCatalog) -> None:
        self._table = table
        self._owner = owner
        self._ids: list[int] = []

    def by_ids(self, ids: list[int]) -> _FlakyExpire:
        self._ids = ids
        return self

    def commit(self) -> None:
        if self._owner.fail_expiry_times > 0:
            self._owner.fail_expiry_times -= 1
            raise CommitFailedException("Table has been updated by another process")
        expire_snapshot_ids(self._table, self._ids)


class FlakyCatalog:
    def __init__(
        self,
        real: SqlCatalog,
        *,
        fail_delete_times: int = 0,
        fail_expiry_times: int = 0,
        fail_delete_error: Exception | None = None,
    ) -> None:
        self._real = real
        self.fail_delete_times = fail_delete_times
        self.fail_expiry_times = fail_expiry_times
        self.fail_delete_error = fail_delete_error
        self.deletes = 0

    def load_table(self, ident: str) -> _Flaky:
        return _Flaky(self._real.load_table(ident), self)
