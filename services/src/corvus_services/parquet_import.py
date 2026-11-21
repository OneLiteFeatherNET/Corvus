"""``corvus-import-parquet``: one-off copy of the Parquet lake into the Iceberg tables.

Run once, after the switch to ``CORVUS_LAKE_FORMAT=iceberg``, so that the captures that live only
in the Parquet lake are read from the Iceberg tables as well (docs/reference/data-lake.md,
section 2a, "One-off import"). The sources are the live ``corvus.lake_files`` entries and the
sessions, players and wrapped DEKs of PostgreSQL; the Parquet objects are read from S3.

Per UTC date, in this order:

* ``events``: every events file of the date, the player key taken from the session;
* ``raw``: every raw file of the date. The payloads are decrypted with the player's DEK (AAD
  ``session_id || seq``) and joined with the events of the same date on ``(session_id, seq)``, which
  supply offset, direction, state, packet type and wire packet id. ``protocol_version`` is the
  client protocol of the session;
* ``session_ends``: the sessions of the date that ended (counters and end time from PostgreSQL).

Not imported: ``sessions`` and ``segments``. Their non-nullable Iceberg columns
(text_packets_captured, names_kept, the CloudNet fields, stage) are not stored in PostgreSQL, and a
null would not fit.

Skipped, never imported, each counted in the report: rows of an erased player (``erased_at``), rows
of a player without a DEK, rows of orphan sessions (no player), rows of sessions unknown to the
catalog. A raw row without its event, a payload that does not decrypt and a repeated
``(session_id, seq)`` of the lake's at-least-once duplicates are skipped and counted too; a repeated
event is copied once, the first occurrence in file order.

Idempotency: each commit carries its source in the snapshot summary under ``IMPORT_PROPERTY``: the
S3 key of a Parquet file, or ``postgres:session_ends:<date>``. A run skips the commit of every
source found in the snapshot history of its table, so a rerun or a resume after a failure imports
the rest exactly once. Every file is still read on every run, because the join index and the
duplicate check need all events of a date. Snapshots that were expired before a rerun are no longer
seen: finish the import within the snapshot retention (``CORVUS_LAKE_SNAPSHOT_RETENTION_DAYS``),
or check the report.
"""

from __future__ import annotations

import argparse
import io
import logging
import sys
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Protocol

import boto3
import psycopg
import pyarrow as pa
import pyarrow.parquet as pq
from botocore.config import Config
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import Settings
from .crypto import Keyring, payload_aad
from .iceberg_sink import IcebergSink, lake_rows
from .schema import ICEBERG_LAYOUT

log = logging.getLogger("corvus.import")

EVENT_FIELDS = ("offset_nanos", "direction", "state", "packet_type", "wire_packet_id")
RAW_FIELDS = ("session_id", "segment", "seq", "payload_original_size", "nonce", "ciphertext")
SKIP_UNKNOWN = "skipped_unknown_session"
SKIP_NO_KEY = "skipped_no_key"
SKIP_ERASED = "skipped_erased"
SKIP_NO_EVENT = "skipped_no_event"
SKIP_DUPLICATE = "skipped_duplicate"
SKIP_DECRYPT = "skipped_decrypt_failed"
TABLES = ("events", "raw", "session_ends")


@dataclass(frozen=True)
class LiveFile:
    """A live ``lake_files`` entry (``deleted_at IS NULL``: replaced and purged files are gone)."""

    id: int
    s3_key: str
    table_name: str
    date: date


@dataclass(frozen=True)
class SessionRecord:
    session_id: bytes
    pseudonym: bytes | None  # None: orphan session (no SessionStart)
    client_protocol: int
    started_at: datetime
    ended_at: datetime | None
    dropped_queue_full: int
    dropped_privacy: int
    listener_errors: int
    dropped_transport: int


@dataclass(frozen=True)
class PlayerRecord:
    pseudonym: bytes
    erased: bool
    wrapped_dek: bytes | None  # None: no player_keys row


class ImportSource(Protocol):
    def live_files(self) -> list[LiveFile]: ...
    def session_records(self) -> list[SessionRecord]: ...
    def player_records(self) -> list[PlayerRecord]: ...


class ObjectReader(Protocol):
    def get(self, key: str) -> bytes: ...


class Report:
    """Counts per (table, UTC date). Keys are the names used by the importer (see the constants)."""

    def __init__(self) -> None:
        self._counts: dict[tuple[str, date], Counter[str]] = defaultdict(Counter)

    def add(self, table: str, day: date, key: str, n: int = 1) -> None:
        self._counts[(table, day)][key] += n

    def count(self, table: str, day: date, key: str) -> int:
        return self._counts.get((table, day), Counter())[key]

    def total(self, key: str) -> int:
        return sum(counter[key] for counter in self._counts.values())

    def render(self, dry_run: bool) -> str:
        head = "dry run, nothing written" if dry_run else "import done"
        lines = [f"corvus-import-parquet: {head}"]
        for (table, day), counter in sorted(self._counts.items()):
            detail = " ".join(f"{k}={v}" for k, v in sorted(counter.items()))
            lines.append(f"{table} {day.isoformat()} {detail}")
        return "\n".join(lines)


@dataclass(frozen=True)
class _Access:
    pseudonym: bytes | None
    dek: bytes | None
    session: SessionRecord | None
    reason: str | None  # None: importable, else the skip counter


class _Resolver:
    """Whether the rows of a session may be imported, and the DEK of its player."""

    def __init__(
        self,
        sessions: Sequence[SessionRecord],
        players: Sequence[PlayerRecord],
        keyring: Keyring,
    ) -> None:
        self._sessions = {s.session_id: s for s in sessions}
        self._players = {p.pseudonym: p for p in players}
        self._keyring = keyring
        self._cache: dict[bytes, _Access] = {}
        self._deks: dict[bytes, bytes] = {}

    def access(self, session_id: bytes) -> _Access:
        found = self._cache.get(session_id)
        if found is None:
            found = self._resolve(session_id)
            self._cache[session_id] = found
        return found

    def _resolve(self, session_id: bytes) -> _Access:
        session = self._sessions.get(session_id)
        if session is None:
            return _Access(None, None, None, SKIP_UNKNOWN)
        pseudonym = session.pseudonym
        if pseudonym is None:
            return _Access(None, None, session, SKIP_NO_KEY)
        player = self._players.get(pseudonym)
        if player is not None and player.erased:
            return _Access(pseudonym, None, session, SKIP_ERASED)
        if player is None or player.wrapped_dek is None:
            return _Access(pseudonym, None, session, SKIP_NO_KEY)
        dek = self._deks.get(pseudonym)
        if dek is None:
            # A wrong KEK raises UnwrapError and aborts the run before anything of it is written.
            dek = self._keyring.unwrap(pseudonym, player.wrapped_dek)
            self._deks[pseudonym] = dek
        return _Access(pseudonym, dek, session, None)


@dataclass(slots=True)
class _Event:
    offset_nanos: int
    direction: int
    state: int
    packet_type: int
    wire_packet_id: int
    emitted: bool = False


def _utc_date(ts: datetime) -> date:
    return ts.astimezone(UTC).date()


def _in_range(day: date, date_from: date | None, date_to: date | None) -> bool:
    return (date_from is None or day >= date_from) and (date_to is None or day <= date_to)


def _read(data: bytes) -> pa.Table:
    return pq.read_table(io.BytesIO(data))


def _events_rows(
    data: bytes,
    resolver: _Resolver,
    index: dict[tuple[bytes, int], _Event],
    report: Report,
    day: date,
) -> pa.Table:
    """The importable rows of one events file (Parquet layout plus ``player_key``). Fills the
    join index of the date with the rows it keeps; a repeated (session_id, seq) is dropped."""
    table = _read(data)
    sids = table.column("session_id").to_pylist()
    seqs = table.column("seq").to_pylist()
    fields = {name: table.column(name).to_pylist() for name in EVENT_FIELDS}
    keep: list[int] = []
    keys: list[bytes] = []
    for i, (sid, seq) in enumerate(zip(sids, seqs, strict=True)):
        report.add("events", day, "rows_read")
        access = resolver.access(sid)
        if access.reason is not None:
            report.add("events", day, access.reason)
            continue
        assert access.pseudonym is not None
        if (sid, seq) in index:
            report.add("events", day, SKIP_DUPLICATE)
            continue
        index[(sid, seq)] = _Event(*(fields[name][i] for name in EVENT_FIELDS))
        keep.append(i)
        keys.append(access.pseudonym)
    kept = table.take(pa.array(keep, pa.int64()))
    return kept.append_column("player_key", pa.array(keys, pa.binary(32)))


def _raw_rows(
    data: bytes,
    resolver: _Resolver,
    index: dict[tuple[bytes, int], _Event],
    report: Report,
    day: date,
) -> pa.Table:
    """The importable raw rows of one raw file in the Iceberg layout, payloads decrypted."""
    table = _read(data)
    cols = {name: table.column(name).to_pylist() for name in RAW_FIELDS}
    out: dict[str, list[Any]] = {name: [] for name in ICEBERG_LAYOUT.schemas["raw"].names}
    aeads: dict[bytes, AESGCM] = {}
    for i in range(table.num_rows):
        report.add("raw", day, "rows_read")
        sid = cols["session_id"][i]
        seq = cols["seq"][i]
        access = resolver.access(sid)
        if access.reason is not None:
            report.add("raw", day, access.reason)
            continue
        assert access.dek is not None
        assert access.session is not None
        event = index.get((sid, seq))
        if event is None:
            report.add("raw", day, SKIP_NO_EVENT)
            continue
        if event.emitted:
            report.add("raw", day, SKIP_DUPLICATE)
            continue
        aead = aeads.get(access.dek)
        if aead is None:
            aead = aeads[access.dek] = AESGCM(access.dek)
        try:
            payload = aead.decrypt(cols["nonce"][i], cols["ciphertext"][i], payload_aad(sid, seq))
        except InvalidTag:
            report.add("raw", day, SKIP_DECRYPT)
            continue
        event.emitted = True
        out["session_id"].append(sid)
        out["segment"].append(cols["segment"][i])
        out["seq"].append(seq)
        out["offset_nanos"].append(event.offset_nanos)
        out["direction"].append(event.direction)
        out["state"].append(event.state)
        out["packet_type"].append(event.packet_type)
        out["wire_packet_id"].append(event.wire_packet_id)
        out["protocol_version"].append(access.session.client_protocol)
        out["player_key"].append(access.pseudonym)
        out["payload_original_size"].append(cols["payload_original_size"][i])
        out["payload"].append(payload)
    return pa.Table.from_pydict(out, schema=ICEBERG_LAYOUT.schemas["raw"])


def _session_end_rows(
    resolver: _Resolver, sessions: Sequence[SessionRecord], report: Report, day: date
) -> pa.Table:
    out: dict[str, list[Any]] = {name: [] for name in ICEBERG_LAYOUT.schemas["session_ends"].names}
    for s in sessions:
        if s.ended_at is None or _utc_date(s.started_at) != day:
            continue
        report.add("session_ends", day, "rows_read")
        access = resolver.access(s.session_id)
        if access.reason is not None:
            report.add("session_ends", day, access.reason)
            continue
        out["session_id"].append(s.session_id)
        out["player_key"].append(access.pseudonym)
        out["ended_at"].append(s.ended_at)
        out["dropped_queue_full"].append(s.dropped_queue_full)
        out["dropped_privacy"].append(s.dropped_privacy)
        out["listener_errors"].append(s.listener_errors)
        out["dropped_transport"].append(s.dropped_transport)
    return pa.Table.from_pydict(out, schema=ICEBERG_LAYOUT.schemas["session_ends"])


class _Committer:
    """One commit per source, skipped when the source is already in the table's history."""

    def __init__(self, sink: IcebergSink, report: Report, dry_run: bool) -> None:
        self._sink = sink
        self._report = report
        self._dry_run = dry_run
        self._done: dict[str, set[str]] = {}

    def load(self) -> None:
        for name in TABLES:
            self._done[name] = self._sink.imported_sources(name)

    def commit(self, table: str, day: date, source: str, rows: pa.Table) -> None:
        self._report.add(table, day, "files")
        if source in self._done[table]:
            self._report.add(table, day, "files_already_imported")
            return
        if rows.num_rows == 0:
            return
        self._report.add(table, day, "rows_imported", rows.num_rows)
        if not self._dry_run:
            self._sink.append_import(table, lake_rows(table, rows, day), source)


def run_import(
    source: ImportSource,
    objects: ObjectReader,
    sink: IcebergSink,
    keyring: Keyring,
    *,
    dry_run: bool = False,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Report:
    """Copy the importable rows of the Parquet lake into the Iceberg tables; see the module doc.

    A dry run reads everything and counts what a run would write, but creates no table and commits
    nothing. A run creates the missing tables first (``bootstrap``)."""
    files = [
        f
        for f in source.live_files()
        if f.table_name in ("events", "raw") and _in_range(f.date, date_from, date_to)
    ]
    sessions = source.session_records()
    resolver = _Resolver(sessions, source.player_records(), keyring)
    if not dry_run:
        sink.bootstrap()
    report = Report()
    committer = _Committer(sink, report, dry_run)
    committer.load()
    days = sorted(
        {f.date for f in files}
        | {_utc_date(s.started_at) for s in sessions if s.ended_at is not None}
    )
    for day in (d for d in days if _in_range(d, date_from, date_to)):
        _import_day(day, files, sessions, objects, resolver, committer, report)
    return report


def _import_day(
    day: date,
    files: Sequence[LiveFile],
    sessions: Sequence[SessionRecord],
    objects: ObjectReader,
    resolver: _Resolver,
    committer: _Committer,
    report: Report,
) -> None:
    events_files = sorted(
        (f for f in files if f.date == day and f.table_name == "events"), key=lambda f: f.s3_key
    )
    raw_files = sorted(
        (f for f in files if f.date == day and f.table_name == "raw"), key=lambda f: f.s3_key
    )
    index: dict[tuple[bytes, int], _Event] = {}
    for f in events_files:
        report.add("events", day, "files_read")
        rows = _events_rows(objects.get(f.s3_key), resolver, index, report, day)
        committer.commit("events", day, f.s3_key, rows)
    for f in raw_files:
        report.add("raw", day, "files_read")
        rows = _raw_rows(objects.get(f.s3_key), resolver, index, report, day)
        committer.commit("raw", day, f.s3_key, rows)
    ends = _session_end_rows(resolver, sessions, report, day)
    committer.commit("session_ends", day, f"postgres:session_ends:{day.isoformat()}", ends)


# ----- adapters -------------------------------------------------------------------------------


class PgImportSource:
    """The catalog rows of the import, read with a session that is read-only by default."""

    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        self._conn = conn

    @classmethod
    def connect(cls, dsn: str) -> PgImportSource:
        conn: psycopg.Connection[Any] = psycopg.connect(dsn, autocommit=True)
        conn.execute("SET default_transaction_read_only = on")
        return cls(conn)

    def close(self) -> None:
        self._conn.close()

    def live_files(self) -> list[LiveFile]:
        rows = self._conn.execute(
            "SELECT id, s3_key, table_name, date FROM corvus.lake_files"
            " WHERE deleted_at IS NULL AND table_name IN ('events', 'raw')"
            " ORDER BY date, table_name, s3_key"
        ).fetchall()
        return [LiveFile(int(i), str(k), str(t), d) for i, k, t, d in rows]

    def session_records(self) -> list[SessionRecord]:
        rows = self._conn.execute(
            "SELECT session_id, pseudonym, client_protocol, started_at, ended_at,"
            " dropped_queue_full, dropped_privacy, listener_errors, dropped_transport"
            " FROM corvus.sessions"
        ).fetchall()
        return [
            SessionRecord(
                bytes(sid),
                bytes(p) if p is not None else None,
                int(proto),
                started,
                ended,
                int(q),
                int(pr),
                int(le),
                int(tr),
            )  # fmt: skip
            for sid, p, proto, started, ended, q, pr, le, tr in rows
        ]

    def player_records(self) -> list[PlayerRecord]:
        rows = self._conn.execute(
            "SELECT p.pseudonym, p.erased_at IS NOT NULL, k.dek_wrapped"
            " FROM corvus.players p LEFT JOIN corvus.player_keys k ON k.pseudonym = p.pseudonym"
        ).fetchall()
        return [
            PlayerRecord(bytes(p), bool(erased), bytes(w) if w is not None else None)
            for p, erased, w in rows
        ]


class S3Objects:
    """Reads the Parquet objects of the lake bucket (boto3, same client settings as S3Store)."""

    def __init__(self, client: Any, bucket: str) -> None:
        self._c = client
        self._bucket = bucket

    @classmethod
    def from_settings(cls, s: Settings) -> S3Objects:
        client = boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint_url,
            region_name=s.s3_region,
            aws_access_key_id=s.s3_access_key,
            aws_secret_access_key=s.s3_secret_key.get_secret_value() if s.s3_secret_key else None,
            config=Config(
                s3={"addressing_style": "path"},
                retries={"max_attempts": 5, "mode": "standard"},
            ),
        )
        return cls(client, s.s3_bucket)

    def get(self, key: str) -> bytes:
        body: bytes = self._c.get_object(Bucket=self._bucket, Key=key)["Body"].read()
        return body


# ----- command line ---------------------------------------------------------------------------


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="corvus-import-parquet",
        description="One-off copy of the Parquet lake into the Iceberg tables (run once).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="read and count what would be imported; write nothing",
    )
    parser.add_argument("--date-from", type=date.fromisoformat, help="first UTC date (YYYY-MM-DD)")
    parser.add_argument("--date-to", type=date.fromisoformat, help="last UTC date, inclusive")
    return parser.parse_args(argv)


def run_cli(argv: Sequence[str], settings: Settings) -> int:
    args = parse_args(argv)
    if settings.lake_format != "iceberg":
        print(
            "corvus-import-parquet needs CORVUS_LAKE_FORMAT=iceberg (the target)", file=sys.stderr
        )
        return 2
    if args.date_from and args.date_to and args.date_from > args.date_to:
        print("--date-from is after --date-to", file=sys.stderr)
        return 2
    keyring = Keyring(settings.kek_bytes(), settings.kek_id)
    source = PgImportSource.connect(settings.pg_dsn)
    try:
        report = run_import(
            source,
            S3Objects.from_settings(settings),
            IcebergSink.from_settings(settings),
            keyring,
            dry_run=args.dry_run,
            date_from=args.date_from,
            date_to=args.date_to,
        )
    finally:
        source.close()
    print(report.render(dry_run=args.dry_run))
    return 0


def main() -> None:
    raise SystemExit(run_cli(sys.argv[1:], Settings()))
