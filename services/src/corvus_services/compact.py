"""``corvus-compact``: one-shot compaction of lake files after player erasure, for a CronJob.

For every erasure request with ``compaction_pending`` (older than the grace period) the live
``events`` and ``raw`` files that contain sessions of the erased player are rewritten without those
sessions. Per file, under a per-file advisory lock:

1. download the file to local disk and stream it batch by batch into a new Parquet file (bounded
   memory: one Arrow batch of ``CORVUS_COMPACT_BATCH_ROWS`` rows);
2. upload the result with the ingest staging protocol (``_staging/`` then copy to a new final key);
3. in ONE catalog transaction insert the new ``lake_files`` / ``lake_file_sessions`` rows and mark
   the old file ``deleted_at`` (``replaced_by`` the new one, ``purge_pending``);
4. delete the old object and clear ``purge_pending``.

Registered artefacts that contain tokenised raw payloads (``derived_artifacts.contains_raw``,
written by ``build_features`` below ``features/<snapshot>/raw/``) of a snapshot with sessions of
the erased player are not rewritten: their whole prefix is deleted and the snapshot is marked
``needs_rebuild`` (a rebuild skips erased players, whose DEK is gone). Artefacts of jobs that
started after the erasure request are kept: those jobs already excluded the erased player. This
step runs before the file rewrite and is repeated until it succeeds; the requests complete only
afterwards.

Crash safety: before step 3 the old file is still the live one (a leftover new object is an orphan
that housekeeping removes); after step 3 only ``purge_pending`` work remains, which every run
retries first. Once all files of the pending requests are done, the requests are marked
``compaction_pending = false`` with ``compacted_at``. Snapshots are immutable and keep listing the
erased sessions and the old file ids: readers use ``GET /api/v1/snapshots/{id}/sessions`` with
``exclude_erased=true`` and ``GET /api/v1/snapshots/{id}/files``.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Protocol
from uuid import UUID

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from .clock import Clock, new_ulid, system_clock
from .config import Settings
from .ports import FileRecord, ObjectInfo
from .retention import delete_prefix

log = logging.getLogger("corvus.compact")


@dataclass(frozen=True)
class LiveFile:
    id: int
    s3_key: str
    table_name: str
    date: date
    schema_version: int


@dataclass(frozen=True)
class PurgeItem:
    file_id: int
    s3_key: str


@dataclass(frozen=True)
class DerivedItem:
    """A live raw-derived artefact of a snapshot that contains sessions of an erased player."""

    id: UUID
    snapshot_id: UUID
    s3_prefix: str
    erased_at: datetime  # latest erasure request that affects it


class CompactCatalog(Protocol):
    async def pending_request_ids(self, requested_before: datetime) -> list[object]:
        """Requests with ``compaction_pending`` that were made before ``requested_before``."""
        ...

    async def affected_files(self, request_ids: list[object]) -> list[LiveFile]:
        """Live files containing sessions of the requests' pseudonyms, ordered by id."""
        ...

    async def derived_for_requests(self, request_ids: list[object]) -> list[DerivedItem]:
        """Live ``contains_raw`` artefacts of snapshots with sessions of the requests' players,
        except those of jobs that started after the request (they already skipped the player)."""
        ...

    async def invalidate_artifact(self, item: DerivedItem, now: datetime) -> None:
        """One transaction: mark the artefact deleted (reason ``erasure``) and the snapshot
        ``needs_rebuild`` since ``item.erased_at``."""
        ...

    def try_lock_file(self, file_id: int) -> AbstractAsyncContextManager[bool]:
        """Session-level advisory lock; yields False (and holds nothing) if another run has it."""
        ...

    async def locked_state(self, file_id: int) -> list[bytes] | None:
        """None if the file is no longer live, else the sessions of erased players it lists."""
        ...

    async def detach_sessions(self, file_id: int, sessions: list[bytes]) -> None:
        """Remove ``lake_file_sessions`` rows of a file that holds no rows of those sessions."""
        ...

    async def replace_file(
        self,
        old_id: int,
        new: FileRecord | None,
        erased: list[bytes],
        now: datetime,
    ) -> int | None:
        """One transaction: insert ``new`` (its sessions are the old file's minus ``erased``),
        mark the old file deleted, ``replaced_by`` the new id, ``purge_pending``. Raises if the old
        file is no longer live. Returns the new file id."""
        ...

    async def purge_pending(self) -> list[PurgeItem]: ...
    async def mark_purged(self, file_id: int) -> None: ...
    async def complete_requests(self, request_ids: list[object], now: datetime) -> None: ...


class FileStore(Protocol):
    async def download(self, key: str, path: Path) -> None: ...
    async def upload(self, path: Path, key: str) -> None: ...
    async def copy(self, src: str, dst: str) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def list_objects(self, prefix: str) -> list[ObjectInfo]: ...


@dataclass(frozen=True)
class RewriteResult:
    kept: int
    dropped: int


def rewrite_parquet(
    src: Path,
    dst: Path,
    erased: frozenset[bytes],
    batch_rows: int,
    on_batch: Callable[[int], None] | None = None,
) -> RewriteResult:
    """Stream ``src`` into ``dst`` without the rows whose ``session_id`` is in ``erased``.

    Only one batch of at most ``batch_rows`` rows is held in memory; ``on_batch`` is called with
    the size of every batch read (a test hook). Row order and compression are preserved.
    """
    value_set = pa.array(sorted(erased), type=pa.binary(16))
    reader = pq.ParquetFile(src)
    kept = dropped = 0
    writer = pq.ParquetWriter(dst, reader.schema_arrow, compression="zstd")
    try:
        for batch in reader.iter_batches(batch_size=batch_rows):
            if on_batch is not None:
                on_batch(batch.num_rows)
            mask = pc.invert(pc.is_in(batch.column("session_id"), value_set=value_set))
            out = batch.filter(mask)
            dropped += batch.num_rows - out.num_rows
            kept += out.num_rows
            if out.num_rows:
                writer.write_batch(out)
    finally:
        writer.close()
    return RewriteResult(kept, dropped)


@dataclass
class CompactReport:
    requests: int = 0
    files_rewritten: int = 0
    files_dropped: int = 0
    files_untouched: int = 0
    files_skipped_locked: int = 0
    rows_removed: int = 0
    purged: int = 0
    artifacts_deleted: int = 0
    errors: list[str] = field(default_factory=list)


async def _purge(catalog: CompactCatalog, store: FileStore, report: CompactReport) -> None:
    for item in await catalog.purge_pending():
        try:
            await store.delete(item.s3_key)
            await catalog.mark_purged(item.file_id)
        except Exception as e:
            report.errors.append(f"purge {item.s3_key}: {e}")
            log.warning("could not purge %s: %s", item.s3_key, e)
        else:
            report.purged += 1


async def _invalidate_derived(
    request_ids: list[object],
    catalog: CompactCatalog,
    store: FileStore,
    clock: Clock,
    report: CompactReport,
) -> None:
    for item in await catalog.derived_for_requests(request_ids):
        try:
            await delete_prefix(store, item.s3_prefix)
            await catalog.invalidate_artifact(item, clock())
        except Exception as e:
            report.errors.append(f"artefact {item.s3_prefix}: {e}")
            log.warning("could not delete artefact %s: %s", item.s3_prefix, e)
        else:
            report.artifacts_deleted += 1


async def _compact_file(
    f: LiveFile,
    catalog: CompactCatalog,
    store: FileStore,
    settings: Settings,
    clock: Clock,
    workdir: Path,
    report: CompactReport,
    on_batch: Callable[[int], None] | None,
) -> None:
    async with catalog.try_lock_file(f.id) as locked:
        if not locked:
            report.files_skipped_locked += 1
            return
        erased_list = await catalog.locked_state(f.id)
        if not erased_list:  # replaced by a concurrent run, or nothing erased in it any more
            report.files_untouched += 1
            return
        erased = frozenset(erased_list)
        src, dst = workdir / f"{f.id}.src.parquet", workdir / f"{f.id}.dst.parquet"
        try:
            await store.download(f.s3_key, src)
            result = await asyncio.to_thread(
                rewrite_parquet, src, dst, erased, settings.compact_batch_rows, on_batch
            )
            if result.dropped == 0:  # sessions are listed but no rows exist (e.g. no raw rows)
                await catalog.detach_sessions(f.id, erased_list)
                report.files_untouched += 1
                return
            record: FileRecord | None = None
            final_key: str | None = None
            if result.kept:
                ulid = new_ulid(clock())
                staging = f"_staging/{f.table_name}/{ulid}.parquet"
                final_key = f"lake/{f.table_name}/date={f.date.isoformat()}/part-{ulid}.parquet"
                await store.upload(dst, staging)
                await store.copy(staging, final_key)
                record = FileRecord(
                    final_key, f.table_name, f.date, result.kept, dst.stat().st_size,
                    f.schema_version,
                )  # fmt: skip
                try:
                    await store.delete(staging)
                except Exception as e:
                    log.warning("could not delete staging object %s (housekeeping will): %s",
                                staging, e)  # fmt: skip
            try:
                await catalog.replace_file(f.id, record, erased_list, clock())
            except Exception:
                if final_key is not None:  # best effort; otherwise housekeeping removes it
                    try:
                        await store.delete(final_key)
                    except Exception as e:
                        log.warning("could not delete unreferenced %s: %s", final_key, e)
                raise
            report.rows_removed += result.dropped
            if record is None:
                report.files_dropped += 1
            else:
                report.files_rewritten += 1
        finally:
            src.unlink(missing_ok=True)
            dst.unlink(missing_ok=True)
    await _purge(catalog, store, report)


async def run_compaction(
    settings: Settings,
    catalog: CompactCatalog,
    store: FileStore,
    clock: Clock = system_clock,
    *,
    workdir: Path | None = None,
    on_batch: Callable[[int], None] | None = None,
) -> CompactReport:
    report = CompactReport()
    await _purge(catalog, store, report)
    now = clock()
    request_ids = await catalog.pending_request_ids(
        now - timedelta(seconds=settings.compact_grace_s)
    )
    report.requests = len(request_ids)
    if not request_ids:
        return report
    await _invalidate_derived(request_ids, catalog, store, clock, report)
    files = await catalog.affected_files(request_ids)
    with tempfile.TemporaryDirectory(dir=workdir, prefix="corvus-compact-") as tmp:
        for f in files:
            try:
                await _compact_file(f, catalog, store, settings, clock, Path(tmp), report, on_batch)
            except Exception as e:
                report.errors.append(f"file {f.id} {f.s3_key}: {e}")
                log.warning("could not compact file %d: %s", f.id, e)
    if not report.errors and not report.files_skipped_locked:
        await catalog.complete_requests(request_ids, clock())
    log.info(
        "compact: %d requests, %d files rewritten, %d dropped, %d untouched, %d locked, "
        "%d rows removed, %d purged, %d artefacts deleted, %d errors",
        report.requests,
        report.files_rewritten,
        report.files_dropped,
        report.files_untouched,
        report.files_skipped_locked,
        report.rows_removed,
        report.purged,
        report.artifacts_deleted,
        len(report.errors),
    )
    return report


async def _main(settings: Settings) -> int:
    from .compact_pg import PgCompactCatalog
    from .s3store import S3Store

    catalog = await PgCompactCatalog.open(settings.pg_dsn, 4)
    try:
        report = await run_compaction(settings, catalog, S3Store.from_settings(settings))
    finally:
        await catalog.close()
    return 1 if report.errors else 0


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    raise SystemExit(asyncio.run(_main(Settings())))


if __name__ == "__main__":
    main()
