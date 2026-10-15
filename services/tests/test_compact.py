from __future__ import annotations

import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from corvus_services.compact import (
    LiveFile,
    PurgeItem,
    rewrite_parquet,
    run_compaction,
)
from corvus_services.ports import FileRecord
from helpers import T0, FakeClock, make_settings

SCHEMA = pa.schema([pa.field("session_id", pa.binary(16)), pa.field("seq", pa.uint64())])
KEEP_A, KEEP_B, GONE_1, GONE_2 = (bytes([i]) * 16 for i in (1, 2, 3, 4))
DAY = date(2026, 10, 14)


def write_parquet(path: Path, rows: list[tuple[bytes, int]], row_group_size: int = 1000) -> None:
    table = pa.Table.from_pydict(
        {"session_id": [r[0] for r in rows], "seq": [r[1] for r in rows]}, schema=SCHEMA
    )
    pq.write_table(table, path, compression="zstd", row_group_size=row_group_size)


def read_rows(path: Path) -> list[tuple[bytes, int]]:
    t = pq.read_table(path).to_pydict()
    return list(zip(t["session_id"], t["seq"], strict=True))


# --- rewrite --------------------------------------------------------------------------------


def test_rewrite_drops_exactly_the_erased_sessions_and_keeps_order(tmp_path: Path):
    rows = [(s, n) for n, s in enumerate([KEEP_A, GONE_1, KEEP_B, GONE_2, KEEP_A] * 7)]
    write_parquet(tmp_path / "in.parquet", rows)

    result = rewrite_parquet(
        tmp_path / "in.parquet", tmp_path / "out.parquet", frozenset({GONE_1, GONE_2}), 10
    )

    expected = [r for r in rows if r[0] not in (GONE_1, GONE_2)]
    assert read_rows(tmp_path / "out.parquet") == expected
    assert (result.kept, result.dropped) == (len(expected), len(rows) - len(expected))
    assert pq.ParquetFile(tmp_path / "out.parquet").schema_arrow == SCHEMA


def test_rewrite_of_a_file_without_erased_rows_drops_nothing(tmp_path: Path):
    write_parquet(tmp_path / "in.parquet", [(KEEP_A, 1), (KEEP_B, 2)])

    result = rewrite_parquet(
        tmp_path / "in.parquet", tmp_path / "out.parquet", frozenset({GONE_1}), 10
    )

    assert (result.kept, result.dropped) == (2, 0)


def test_rewrite_reads_in_bounded_batches(tmp_path: Path):
    n, batch_rows = 10_000, 1_000
    write_parquet(
        tmp_path / "in.parquet",
        [(GONE_1 if i % 3 == 0 else KEEP_A, i) for i in range(n)],
        row_group_size=n,  # one big row group, like an ingest file
    )
    seen: list[int] = []

    rewrite_parquet(
        tmp_path / "in.parquet", tmp_path / "out.parquet", frozenset({GONE_1}), batch_rows,
        on_batch=seen.append,
    )  # fmt: skip

    assert max(seen) <= batch_rows, "a batch exceeded the configured bound"
    assert len(seen) == math.ceil(n / batch_rows), "the file was not read batch by batch"
    assert sum(seen) == n


# --- orchestration with fakes ------------------------------------------------------------------


class Boom(Exception):
    pass


@dataclass
class Row:
    key: str
    table: str
    day: date
    schema_version: int = 1
    deleted: bool = False
    purge_pending: bool = False
    replaced_by: int | None = None


class FakeLake:
    """Object store backed by memory; parquet bytes are materialised on download/upload."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.fail_copy = False
        self.fail_delete: set[str] = set()
        self.staging_puts: list[str] = []

    async def download(self, key: str, path: Path) -> None:
        path.write_bytes(self.objects[key])

    async def upload(self, path: Path, key: str) -> None:
        self.staging_puts.append(key)
        self.objects[key] = path.read_bytes()

    async def copy(self, src: str, dst: str) -> None:
        if self.fail_copy:
            raise Boom("copy")
        self.objects[dst] = self.objects[src]

    async def delete(self, key: str) -> None:
        if key in self.fail_delete:
            raise Boom("delete")
        self.objects.pop(key, None)


@dataclass
class FakeCatalog:
    files: dict[int, Row] = field(default_factory=dict)
    file_sessions: dict[int, set[bytes]] = field(default_factory=dict)
    erased: set[bytes] = field(default_factory=set)
    sessions_of_request: dict[str, set[bytes]] = field(default_factory=dict)
    requests: dict[str, tuple[datetime, bool]] = field(default_factory=dict)  # at, pending
    locked_elsewhere: set[int] = field(default_factory=set)
    held: set[int] = field(default_factory=set)
    fail_replace: bool = False
    next_id: int = 100

    async def pending_request_ids(self, requested_before: datetime) -> list[object]:
        return sorted(
            r for r, (at, pending) in self.requests.items() if pending and at <= requested_before
        )

    async def affected_files(self, request_ids: list[object]) -> list[LiveFile]:
        sessions = set().union(*(self.sessions_of_request[str(r)] for r in request_ids))
        return [
            LiveFile(i, f.key, f.table, f.day, f.schema_version)
            for i, f in sorted(self.files.items())
            if not f.deleted and self.file_sessions[i] & sessions
        ]

    @asynccontextmanager
    async def try_lock_file(self, file_id: int) -> AsyncIterator[bool]:
        if file_id in self.locked_elsewhere:
            yield False
            return
        self.held.add(file_id)
        try:
            yield True
        finally:
            self.held.discard(file_id)

    async def locked_state(self, file_id: int) -> list[bytes] | None:
        if self.files[file_id].deleted:
            return None
        return sorted(self.file_sessions[file_id] & self.erased)

    async def detach_sessions(self, file_id: int, sessions: list[bytes]) -> None:
        self.file_sessions[file_id] -= set(sessions)

    async def replace_file(
        self, old_id: int, new: FileRecord | None, erased: list[bytes], now: datetime
    ) -> int | None:
        if self.fail_replace:
            raise Boom("replace")
        old = self.files[old_id]
        assert not old.deleted
        new_id = None
        if new is not None:  # all or nothing: nothing is touched before this point
            new_id = self.next_id
            self.next_id += 1
            self.files[new_id] = Row(new.s3_key, new.table_name, new.date, new.schema_version)
            self.file_sessions[new_id] = self.file_sessions[old_id] - set(erased)
        old.deleted, old.purge_pending, old.replaced_by = True, True, new_id
        return new_id

    async def purge_pending(self) -> list[PurgeItem]:
        return [PurgeItem(i, f.key) for i, f in sorted(self.files.items()) if f.purge_pending]

    async def mark_purged(self, file_id: int) -> None:
        self.files[file_id].purge_pending = False

    async def complete_requests(self, request_ids: list[object], now: datetime) -> None:
        for r in request_ids:
            self.requests[str(r)] = (self.requests[str(r)][0], False)


class World:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path / "work"
        self.tmp.mkdir()
        self.lake = FakeLake()
        self.cat = FakeCatalog()
        self.clock = FakeClock(T0)
        self.settings = make_settings(compact_batch_rows=4, compact_grace_s=60)
        # file 1: mixed; file 2: only erased sessions; file 3: nothing erased; file 4: a raw file
        self.add(1, "events", [(KEEP_A, 0), (GONE_1, 1), (KEEP_B, 2), (GONE_1, 3), (KEEP_A, 4)])
        self.add(2, "events", [(GONE_1, 0), (GONE_2, 1)])
        self.add(3, "events", [(KEEP_A, 5), (KEEP_B, 6)])
        self.add(4, "raw", [(KEEP_B, 7), (GONE_2, 8)])
        self.cat.erased = {GONE_1, GONE_2}
        self.cat.sessions_of_request["req-1"] = {GONE_1, GONE_2}
        self.cat.requests["req-1"] = (T0 - timedelta(hours=1), True)

    def add(self, file_id: int, table: str, rows: list[tuple[bytes, int]]) -> None:
        key = f"lake/{table}/date={DAY}/part-{file_id}.parquet"
        path = self.tmp / f"seed-{file_id}.parquet"
        write_parquet(path, rows)
        self.lake.objects[key] = path.read_bytes()
        self.cat.files[file_id] = Row(key, table, DAY)
        self.cat.file_sessions[file_id] = {r[0] for r in rows}

    async def run(self):
        return await run_compaction(
            self.settings, self.cat, self.lake, self.clock, workdir=self.tmp
        )

    def rows_of(self, key: str) -> list[tuple[bytes, int]]:
        path = self.tmp / "read.parquet"
        path.write_bytes(self.lake.objects[key])
        return read_rows(path)

    def live(self) -> dict[int, Row]:
        return {i: f for i, f in self.cat.files.items() if not f.deleted}

    def assert_consistent(self) -> None:
        """Every live file has its object and (once compacted) no erased session; no live row
        is purge_pending; nothing the catalog calls live is missing from the lake."""
        for i, f in self.live().items():
            assert f.key in self.lake.objects, f"live file {i} lost its object"
            assert not f.purge_pending


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


async def test_compaction_removes_exactly_the_erased_rows_everywhere(world: World):
    original_3 = world.lake.objects[world.cat.files[3].key]

    report = await world.run()

    assert not report.errors
    live = world.live()
    assert 1 not in live
    assert 2 not in live
    assert 4 not in live
    assert 3 in live
    assert world.lake.objects[live[3].key] == original_3
    new_events = next(f for i, f in live.items() if i >= 100 and f.table == "events")
    new_raw = next(f for i, f in live.items() if i >= 100 and f.table == "raw")
    assert world.rows_of(new_events.key) == [(KEEP_A, 0), (KEEP_B, 2), (KEEP_A, 4)]
    assert world.rows_of(new_raw.key) == [(KEEP_B, 7)]
    assert (report.files_rewritten, report.files_dropped, report.rows_removed) == (2, 1, 5)
    world.assert_consistent()


async def test_replacement_follows_the_ingest_layout_and_staging_protocol(world: World):
    await world.run()

    new = [f for i, f in world.cat.files.items() if i >= 100]
    assert all(
        f.key.startswith(f"lake/{f.table}/date={DAY}/part-") and f.key.endswith(".parquet")
        for f in new
    )
    assert all(k.startswith("_staging/") for k in world.lake.staging_puts)
    assert not [k for k in world.lake.objects if k.startswith("_staging/")]


async def test_old_objects_are_deleted_and_the_catalog_remembers_the_successor(world: World):
    old_keys = [world.cat.files[i].key for i in (1, 2, 4)]

    await world.run()

    assert all(k not in world.lake.objects for k in old_keys)
    assert world.cat.files[1].replaced_by is not None
    assert world.cat.files[2].replaced_by is None  # nothing remained of it
    assert not any(f.purge_pending for f in world.cat.files.values())


async def test_new_file_lists_the_remaining_sessions_only(world: World):
    await world.run()

    successor = world.cat.files[1].replaced_by
    assert successor is not None
    assert world.cat.file_sessions[successor] == {KEEP_A, KEEP_B}


async def test_request_is_completed_after_success(world: World):
    await world.run()

    assert world.cat.requests["req-1"][1] is False


async def test_second_run_changes_nothing(world: World):
    await world.run()
    snapshot = (dict(world.lake.objects), {i: vars(f).copy() for i, f in world.cat.files.items()})

    report = await world.run()

    assert report.requests == 0
    assert not report.errors
    assert (dict(world.lake.objects), {i: vars(f) for i, f in world.cat.files.items()}) == snapshot


async def test_rerunning_a_still_pending_request_is_idempotent(world: World):
    await world.run()
    world.cat.requests["req-1"] = (T0 - timedelta(hours=1), True)  # e.g. a repeated erasure
    before = dict(world.lake.objects)

    report = await world.run()

    assert report.files_rewritten == 0
    assert report.rows_removed == 0
    assert world.lake.objects == before
    assert world.cat.requests["req-1"][1] is False


async def test_a_file_that_lists_the_session_without_rows_is_only_detached(world: World):
    world.cat.file_sessions[3].add(GONE_1)  # listed in the catalog, no rows in the object
    original = world.lake.objects[world.cat.files[3].key]

    await world.run()

    assert world.lake.objects[world.cat.files[3].key] == original
    assert GONE_1 not in world.cat.file_sessions[3]
    assert not world.cat.files[3].deleted


async def test_requests_inside_the_grace_period_are_not_compacted(world: World):
    world.cat.requests["req-1"] = (T0 - timedelta(seconds=30), True)

    report = await world.run()

    assert report.requests == 0
    assert not world.cat.files[1].deleted


async def test_a_file_locked_by_another_run_is_skipped_and_the_request_stays_pending(world: World):
    world.cat.locked_elsewhere = {1}

    report = await world.run()

    assert report.files_skipped_locked == 1
    assert not world.cat.files[1].deleted
    assert world.cat.files[2].deleted  # the others were processed
    assert world.cat.requests["req-1"][1] is True


async def test_locks_are_released_after_the_run(world: World):
    await world.run()

    assert world.cat.held == set()


async def test_locks_are_released_after_a_failure(world: World):
    world.lake.fail_copy = True

    await world.run()

    assert world.cat.held == set()


async def test_crash_while_promoting_leaves_the_old_file_live_and_a_rerun_recovers(world: World):
    world.lake.fail_copy = True

    report = await world.run()

    assert report.errors
    assert not world.cat.files[1].deleted
    assert world.cat.files[1].key in world.lake.objects
    assert world.cat.requests["req-1"][1] is True
    world.assert_consistent()
    world.lake.fail_copy = False

    again = await world.run()

    assert not again.errors
    assert world.cat.requests["req-1"][1] is False
    assert 1 not in world.live()


async def test_crash_in_the_catalog_transaction_removes_the_unreferenced_new_object(world: World):
    world.cat.fail_replace = True
    before = set(world.lake.objects)

    report = await world.run()

    assert report.errors
    assert set(world.lake.objects) == before  # nothing new left behind
    assert not world.cat.files[1].deleted
    world.assert_consistent()
    world.cat.fail_replace = False

    again = await world.run()

    assert not again.errors
    assert world.cat.requests["req-1"][1] is False


async def test_crash_after_the_commit_is_finished_by_the_next_run(world: World):
    old_key = world.cat.files[1].key
    world.lake.fail_delete = {old_key}

    report = await world.run()

    assert report.errors
    assert world.cat.files[1].deleted
    assert world.cat.files[1].purge_pending
    assert old_key in world.lake.objects  # still to be purged
    assert world.cat.requests["req-1"][1] is True
    world.lake.fail_delete = set()

    again = await world.run()

    assert not again.errors
    assert old_key not in world.lake.objects
    assert not world.cat.files[1].purge_pending
    assert world.cat.requests["req-1"][1] is False
    world.assert_consistent()
