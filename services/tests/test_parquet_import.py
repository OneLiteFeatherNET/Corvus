"""One-off import of the Parquet lake into the Iceberg tables, on a local SqlCatalog in tmp_path.

No network, no real S3 or PostgreSQL: the source of the catalog rows and the object store are
in-memory fakes, the Iceberg tables are a file-based pyiceberg SQL catalog.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.exceptions import NoSuchTableError

from corvus_services.crypto import Keyring, UnwrapError, encrypt_payload
from corvus_services.iceberg_sink import IcebergSink, lake_rows
from corvus_services.parquet_import import (
    LiveFile,
    PlayerRecord,
    Report,
    SessionRecord,
    run_import,
)
from corvus_services.schema import ICEBERG_LAYOUT, SCHEMAS

KEK = bytes(range(32))
KEK_ID = "kek-1"
DAY = date(2026, 10, 15)
OTHER_DAY = date(2026, 10, 16)

SESSION_A = bytes([1]) * 16  # player A, complete session
SESSION_B = bytes([2]) * 16  # player B, erased
SESSION_C = bytes([3]) * 16  # player C, no key row
SESSION_ORPHAN = bytes([4]) * 16  # no SessionStart
SESSION_LATER = bytes([5]) * 16  # player A, next day
SESSION_UNKNOWN = bytes([6]) * 16  # not in the catalog at all

PLAYER_A = bytes([0xA]) * 32
PLAYER_B = bytes([0xB]) * 32
PLAYER_C = bytes([0xC]) * 32
DEK_A = bytes([0x11]) * 32
DEK_B = bytes([0x22]) * 32

EVENTS_KEY = "lake/events/date=2026-10-15/part-a.parquet"
EVENTS_KEY_2 = "lake/events/date=2026-10-15/part-b.parquet"
RAW_KEY = "lake/raw/date=2026-10-15/part-a.parquet"
EVENTS_LATER_KEY = "lake/events/date=2026-10-16/part-a.parquet"
RAW_LATER_KEY = "lake/raw/date=2026-10-16/part-a.parquet"
SESSION_ENDS_SOURCE = "postgres:session_ends:2026-10-15"


def _ts(day: date, hour: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


def _event(session_id: bytes, seq: int, **overrides: Any) -> dict[str, Any]:
    """One events row in the Parquet layout (EVENTS_SCHEMA), defaults for every column."""
    row: dict[str, Any] = dict.fromkeys(SCHEMAS["events"].names)
    row.update(
        session_id=session_id,
        segment=1,
        seq=seq,
        offset_nanos=seq * 1000,
        direction=1,
        state=2,
        packet_type=0x0102,
        wire_packet_id=-5,
        payload_original_size=len(f"payload-{seq}"),
        event_kind=0,
    )
    row.update(overrides)
    return row


def _raw(session_id: bytes, seq: int, dek: bytes, payload: bytes) -> dict[str, Any]:
    """One raw row in the Parquet layout: the payload encrypted with the DEK, as ingest wrote it."""
    nonce, ciphertext = encrypt_payload(dek, session_id, seq, payload, nonce=bytes([seq]) * 12)
    return {
        "session_id": session_id,
        "segment": 1,
        "seq": seq,
        "payload_original_size": len(payload),
        "nonce": nonce,
        "ciphertext": ciphertext,
    }


def _parquet(table: str, rows: list[dict[str, Any]]) -> bytes:
    schema = SCHEMAS[table]
    columns = {name: [row[name] for row in rows] for name in schema.names}
    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pydict(columns, schema=schema), buf)
    return buf.getvalue()


@dataclass
class FakeSource:
    """The catalog rows the import reads: lake files, sessions and players."""

    files: list[LiveFile] = field(default_factory=list)
    sessions: list[SessionRecord] = field(default_factory=list)
    players: list[PlayerRecord] = field(default_factory=list)

    def live_files(self) -> list[LiveFile]:
        return list(self.files)

    def session_records(self) -> list[SessionRecord]:
        return list(self.sessions)

    def player_records(self) -> list[PlayerRecord]:
        return list(self.players)


class FakeObjects:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self._objects = objects

    def get(self, key: str) -> bytes:
        return self._objects[key]


@pytest.fixture
def keyring() -> Keyring:
    return Keyring(KEK, KEK_ID)


@pytest.fixture
def catalog(tmp_path: Path) -> SqlCatalog:
    cat = SqlCatalog(
        "test",
        uri=f"sqlite:///{tmp_path / 'catalog.db'}",
        warehouse=f"file://{tmp_path / 'warehouse'}",
    )
    cat.create_namespace("capture")
    return cat


@pytest.fixture
def sink(catalog: SqlCatalog) -> IcebergSink:
    return IcebergSink(catalog, "capture", sleep=lambda _s: None)


def _players(keyring: Keyring) -> list[PlayerRecord]:
    return [
        PlayerRecord(PLAYER_A, erased=False, wrapped_dek=keyring.wrap(PLAYER_A, DEK_A)),
        PlayerRecord(PLAYER_B, erased=True, wrapped_dek=None),
        PlayerRecord(PLAYER_C, erased=False, wrapped_dek=None),
    ]


def _sessions() -> list[SessionRecord]:
    return [
        SessionRecord(
            SESSION_A,
            PLAYER_A,
            client_protocol=767,
            started_at=_ts(DAY, 10),
            ended_at=_ts(DAY, 11),
            dropped_queue_full=1,
            dropped_privacy=2,
            listener_errors=3,
            dropped_transport=4,
        ),
        SessionRecord(SESSION_B, PLAYER_B, 767, _ts(DAY, 9), _ts(DAY, 9), 0, 0, 0, 0),
        SessionRecord(SESSION_C, PLAYER_C, 767, _ts(DAY, 9), None, 0, 0, 0, 0),
        SessionRecord(SESSION_ORPHAN, None, 767, _ts(DAY, 9), None, 0, 0, 0, 0),
        SessionRecord(SESSION_LATER, PLAYER_A, 768, _ts(OTHER_DAY, 8), None, 0, 0, 0, 0),
    ]


def _files() -> list[LiveFile]:
    return [
        LiveFile(1, EVENTS_KEY, "events", DAY),
        LiveFile(2, RAW_KEY, "raw", DAY),
        LiveFile(3, EVENTS_LATER_KEY, "events", OTHER_DAY),
        LiveFile(4, RAW_LATER_KEY, "raw", OTHER_DAY),
    ]


def _objects(keyring: Keyring) -> dict[str, bytes]:
    session_a_events = [_event(SESSION_A, seq) for seq in (1, 2, 3)]
    session_a_raw = [
        _raw(SESSION_A, 1, DEK_A, b"hello"),
        _raw(SESSION_A, 2, DEK_A, b"world!!"),
    ]
    # Erased player B, player C without a key, an orphan and an unknown session: all skipped.
    other_events = [
        _event(SESSION_B, 1),
        _event(SESSION_C, 1),
        _event(SESSION_ORPHAN, 1),
        _event(SESSION_UNKNOWN, 1),
    ]
    other_raw = [_raw(SESSION_B, 1, DEK_B, b"secret")]
    later_events = [_event(SESSION_LATER, 1)]
    later_raw = [_raw(SESSION_LATER, 1, DEK_A, b"later")]
    return {
        EVENTS_KEY: _parquet("events", session_a_events + other_events),
        RAW_KEY: _parquet("raw", session_a_raw + other_raw),
        EVENTS_LATER_KEY: _parquet("events", later_events),
        RAW_LATER_KEY: _parquet("raw", later_raw),
    }


@pytest.fixture
def source(keyring: Keyring) -> FakeSource:
    return FakeSource(_files(), _sessions(), _players(keyring))


@pytest.fixture
def objects(keyring: Keyring) -> FakeObjects:
    return FakeObjects(_objects(keyring))


def _run(
    source: FakeSource,
    objects: FakeObjects,
    sink: IcebergSink,
    keyring: Keyring,
    **kwargs: Any,
) -> Report:
    return run_import(source, objects, sink, keyring, **kwargs)


def _scan(catalog: SqlCatalog, table: str) -> pa.Table:
    return catalog.load_table(f"capture.{table}").scan().to_arrow()


def test_events_are_copied_with_the_player_key_of_their_session(
    source, objects, sink, keyring, catalog
):
    _run(source, objects, sink, keyring)

    events = _scan(catalog, "events").to_pylist()
    session_a_rows = [r for r in events if r["session_id"] == SESSION_A]
    assert len(session_a_rows) == 3
    assert {r["player_key"] for r in session_a_rows} == {PLAYER_A}
    first = next(r for r in session_a_rows if r["seq"] == 1)
    assert first["offset_nanos"] == 1000
    assert first["packet_type"] == 0x0102
    assert first["wire_packet_id"] == -5
    assert first["date"] == DAY


def test_raw_payloads_are_decrypted_and_joined_with_their_event_fields(
    source, objects, sink, keyring, catalog
):
    _run(source, objects, sink, keyring)

    raw = {r["seq"]: r for r in _scan(catalog, "raw").to_pylist() if r["session_id"] == SESSION_A}
    assert set(raw) == {1, 2}
    assert raw[1]["payload"] == b"hello"
    assert raw[2]["payload"] == b"world!!"
    assert raw[1]["player_key"] == PLAYER_A
    assert raw[1]["offset_nanos"] == 1000
    assert raw[1]["direction"] == 1
    assert raw[1]["state"] == 2
    assert raw[1]["packet_type"] == 0x0102
    assert raw[1]["wire_packet_id"] == -5
    assert raw[1]["payload_original_size"] == len(b"hello")


def test_raw_rows_take_the_protocol_version_of_their_session(
    source, objects, sink, keyring, catalog
):
    _run(source, objects, sink, keyring)

    later = [r for r in _scan(catalog, "raw").to_pylist() if r["session_id"] == SESSION_LATER]
    assert [r["protocol_version"] for r in later] == [768]


def test_raw_row_without_its_event_is_skipped_and_counted(keyring, objects, sink, catalog):
    source = FakeSource(
        files=[LiveFile(1, RAW_KEY, "raw", DAY)],
        sessions=_sessions(),
        players=_players(keyring),
    )
    report = _run(source, objects, sink, keyring)

    assert report.count("raw", DAY, "skipped_no_event") == 2
    assert _scan(catalog, "raw").num_rows == 0


def test_rows_of_an_erased_player_are_never_imported(source, objects, sink, keyring, catalog):
    report = _run(source, objects, sink, keyring)

    assert all(r["session_id"] != SESSION_B for r in _scan(catalog, "events").to_pylist())
    assert all(r["session_id"] != SESSION_B for r in _scan(catalog, "raw").to_pylist())
    assert report.count("events", DAY, "skipped_erased") == 1
    assert report.count("raw", DAY, "skipped_erased") == 1


def test_player_without_a_key_row_is_not_imported(source, objects, sink, keyring, catalog):
    _run(source, objects, sink, keyring)

    assert all(r["session_id"] != SESSION_C for r in _scan(catalog, "events").to_pylist())


def test_orphan_and_unknown_sessions_are_not_imported(source, objects, sink, keyring, catalog):
    report = _run(source, objects, sink, keyring)

    imported = {r["session_id"] for r in _scan(catalog, "events").to_pylist()}
    assert SESSION_ORPHAN not in imported
    assert SESSION_UNKNOWN not in imported
    assert report.count("events", DAY, "skipped_no_key") == 2
    assert report.count("events", DAY, "skipped_unknown_session") == 1


def test_session_ends_are_imported_with_the_player_key_and_counters(
    source, objects, sink, keyring, catalog
):
    _run(source, objects, sink, keyring)

    ends = _scan(catalog, "session_ends").to_pylist()
    assert len(ends) == 1
    assert ends[0]["session_id"] == SESSION_A
    assert ends[0]["player_key"] == PLAYER_A
    assert ends[0]["ended_at"] == _ts(DAY, 11)
    assert (
        ends[0]["dropped_queue_full"],
        ends[0]["dropped_privacy"],
        ends[0]["listener_errors"],
        ends[0]["dropped_transport"],
    ) == (1, 2, 3, 4)
    assert ends[0]["date"] == DAY


def test_session_ends_of_erased_players_are_not_imported(source, objects, sink, keyring, catalog):
    # SESSION_B (erased, with an end) is part of the fixture.
    _run(source, objects, sink, keyring)

    assert [r["session_id"] for r in _scan(catalog, "session_ends").to_pylist()] == [SESSION_A]


def test_a_second_run_imports_nothing_again(source, objects, sink, keyring, catalog):
    _run(source, objects, sink, keyring)
    before = {name: _scan(catalog, name).num_rows for name in ICEBERG_LAYOUT.schemas}

    second = _run(source, objects, sink, keyring)

    after = {name: _scan(catalog, name).num_rows for name in ICEBERG_LAYOUT.schemas}
    assert after == before
    assert second.count("events", DAY, "rows_imported") == 0
    assert second.count("events", DAY, "files_already_imported") == 1
    assert second.count("raw", DAY, "files_already_imported") == 1
    assert second.count("session_ends", DAY, "files_already_imported") == 1


def test_dry_run_writes_no_table_and_reports_what_a_run_would_write(
    source, objects, sink, keyring, catalog
):
    dry = _run(source, objects, sink, keyring, dry_run=True)

    with pytest.raises(NoSuchTableError):
        catalog.load_table("capture.events")
    assert dry.count("events", DAY, "rows_imported") == 3
    assert dry.count("raw", DAY, "rows_imported") == 2
    assert dry.count("session_ends", DAY, "rows_imported") == 1

    real = _run(source, objects, sink, keyring)
    assert real.count("events", DAY, "rows_imported") == dry.count("events", DAY, "rows_imported")
    assert real.count("raw", DAY, "rows_imported") == dry.count("raw", DAY, "rows_imported")


def test_a_failed_commit_is_resumed_by_the_next_run_without_duplicates(
    source, objects, keyring, catalog
):
    class FailRawOnce(IcebergSink):
        failed = False

        def append_import(self, name, rows, source):  # type: ignore[override]
            if name == "raw" and not FailRawOnce.failed:
                FailRawOnce.failed = True
                raise RuntimeError("catalog went away")
            return super().append_import(name, rows, source)

    failing = FailRawOnce(catalog, "capture", sleep=lambda _s: None)
    with pytest.raises(RuntimeError, match="catalog went away"):
        _run(source, objects, failing, keyring)
    assert _scan(catalog, "events").num_rows == 3

    resumed = IcebergSink(catalog, "capture", sleep=lambda _s: None)
    _run(source, objects, resumed, keyring)

    assert _scan(catalog, "raw").num_rows == 3
    assert _scan(catalog, "events").num_rows == 4
    assert _scan(catalog, "session_ends").num_rows == 1


def test_duplicate_events_of_the_lake_are_copied_once(keyring, sink, catalog):
    dup = _parquet("events", [_event(SESSION_A, 1), _event(SESSION_A, 1)])
    source = FakeSource(
        files=[LiveFile(1, EVENTS_KEY, "events", DAY)],
        sessions=_sessions(),
        players=_players(keyring),
    )
    report = _run(source, FakeObjects({EVENTS_KEY: dup}), sink, keyring)

    assert _scan(catalog, "events").num_rows == 1
    assert report.count("events", DAY, "skipped_duplicate") == 1


def test_files_and_sessions_outside_the_date_range_are_not_imported(
    source, objects, sink, keyring, catalog
):
    _run(source, objects, sink, keyring, date_from=OTHER_DAY, date_to=OTHER_DAY)

    days = {r["date"] for r in _scan(catalog, "events").to_pylist()}
    assert days == {OTHER_DAY}
    assert _scan(catalog, "session_ends").num_rows == 0


def test_a_key_that_does_not_unwrap_aborts_the_import(source, objects, sink, catalog):
    other = Keyring(bytes([9]) * 32, KEK_ID)
    with pytest.raises(UnwrapError):
        _run(source, objects, sink, other)
    assert _scan(catalog, "events").num_rows == 0


def test_cli_refuses_to_run_against_the_parquet_lake(capsys):
    from corvus_services.config import Settings
    from corvus_services.parquet_import import run_cli

    code = run_cli(["--dry-run"], Settings(lake_format="parquet"))

    assert code == 2
    assert "CORVUS_LAKE_FORMAT=iceberg" in capsys.readouterr().err


class UntaggedSink(IcebergSink):
    """The table as it is after its snapshots were expired: the rows are there, the tags are not."""

    def append_import(self, name, rows, source):  # type: ignore[override]
        self._append_with_retry(name, rows)


def test_a_rerun_without_snapshot_tags_imports_nothing(source, objects, sink, keyring, catalog):
    _run(source, objects, UntaggedSink(catalog, "capture", sleep=lambda _s: None), keyring)
    before = {name: _scan(catalog, name).num_rows for name in ICEBERG_LAYOUT.schemas}

    rerun = _run(source, objects, sink, keyring)

    after = {name: _scan(catalog, name).num_rows for name in ICEBERG_LAYOUT.schemas}
    assert after == before
    assert rerun.total("rows_imported") == 0


def test_a_rerun_without_snapshot_tags_does_not_duplicate_raw_rows(
    source, objects, sink, keyring, catalog
):
    _run(source, objects, UntaggedSink(catalog, "capture", sleep=lambda _s: None), keyring)

    _run(source, objects, sink, keyring)

    seqs = sorted(
        r["seq"] for r in _scan(catalog, "raw").to_pylist() if r["session_id"] == SESSION_A
    )
    assert seqs == [1, 2]


def test_session_ends_of_a_session_already_in_iceberg_are_not_imported(
    source, objects, sink, keyring, catalog
):
    sink.bootstrap()
    live_end = pa.Table.from_pydict(
        {
            "session_id": [SESSION_A],
            "player_key": [PLAYER_A],
            "ended_at": [_ts(DAY, 11)],
            "dropped_queue_full": [9],
            "dropped_privacy": [9],
            "listener_errors": [9],
            "dropped_transport": [9],
        },
        schema=ICEBERG_LAYOUT.schemas["session_ends"],
    )
    sink.append_import("session_ends", lake_rows("session_ends", live_end, DAY), "live-ingest")

    report = _run(source, objects, sink, keyring)

    ends = _scan(catalog, "session_ends").to_pylist()
    assert len(ends) == 1
    assert ends[0]["dropped_queue_full"] == 9
    assert report.count("session_ends", DAY, "rows_imported") == 0


def test_a_day_with_only_postgres_sessions_imports_nothing(keyring, sink, catalog):
    ended = SessionRecord(SESSION_A, PLAYER_A, 767, _ts(DAY, 10), _ts(DAY, 11), 0, 0, 0, 0)
    source = FakeSource(files=[], sessions=[ended], players=_players(keyring))

    report = _run(source, FakeObjects({}), sink, keyring)

    assert _scan(catalog, "session_ends").num_rows == 0
    assert report.total("rows_imported") == 0


def test_session_ends_are_only_imported_for_sessions_with_rows_in_the_day_files(
    source, objects, sink, keyring, catalog
):
    no_rows_in_files = SessionRecord(
        bytes([7]) * 16, PLAYER_A, 767, _ts(DAY, 9), _ts(DAY, 10), 0, 0, 0, 0
    )
    source.sessions.append(no_rows_in_files)

    _run(source, objects, sink, keyring)

    assert [r["session_id"] for r in _scan(catalog, "session_ends").to_pylist()] == [SESSION_A]
