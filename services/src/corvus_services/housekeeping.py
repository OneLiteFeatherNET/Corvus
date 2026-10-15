"""``corvus-housekeeping``: one-shot lake cleanup for a CronJob.

* final objects without a catalog row and older than the grace period are orphans (a flush that
  crashed between the copy and the catalog commit) and are deleted;
* ``_staging/`` objects older than the staging age are leftovers and are deleted;
* ``raw`` files whose date partition is older than the longest retention of any pool whose snapshot
  references a session in the file (the default for sessions in no snapshot) are deleted and marked
  ``deleted_at`` in the catalog; a pool sets its retention with ``raw_retention_days`` (NFR-018);
* registered artefacts that contain tokenised raw payloads (``derived_artifacts.contains_raw``) are
  deleted once the retention of their snapshot's pool has passed since the newest session they were
  built from, i.e. they never outlive the raw data they derive from;
* ``session_batches`` rows (redelivery bookkeeping) older than their retention are pruned;
* ``score_history`` rows (online scoring) older than their retention are pruned.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Protocol
from uuid import UUID

from .clock import Clock, system_clock
from .config import Settings
from .pgcatalog import PgCatalog
from .ports import ObjectInfo, ObjectStore
from .retention import LiveArtifact, artifact_expiry, delete_prefix, effective_raw_retention
from .s3store import S3Store

log = logging.getLogger("corvus.housekeeping")

LAKE_PREFIX = "lake/"
STAGING_PREFIX = "_staging/"
_LIST_CHUNK = 500


def select_orphans(
    objects: Iterable[ObjectInfo], known_keys: set[str], now: datetime, grace: timedelta
) -> list[str]:
    """Keys with no catalog row that are older than ``grace``."""
    return [o.key for o in objects if o.key not in known_keys and now - o.last_modified > grace]


def select_stale(objects: Iterable[ObjectInfo], now: datetime, max_age: timedelta) -> list[str]:
    return [o.key for o in objects if now - o.last_modified > max_age]


def expired_artifacts(
    artifacts: Iterable[LiveArtifact], now: datetime, default_days: int, max_days: int
) -> list[LiveArtifact]:
    return [
        a
        for a in artifacts
        if artifact_expiry(
            a.newest_source, effective_raw_retention(a.pool_days, default_days, max_days)
        )
        < now
    ]


class HousekeepingCatalog(Protocol):
    async def known_keys(self, keys: list[str]) -> set[str]: ...
    async def expired_raw_files(
        self, today: date, default_days: int, max_days: int
    ) -> list[tuple[int, str]]:
        """Live raw files older than the longest retention of any pool that references one of
        their sessions (``default_days`` for sessions in no snapshot, ``max_days`` caps a pool)."""
        ...

    async def mark_deleted(self, file_id: int, at: datetime) -> None: ...
    async def live_raw_artifacts(self) -> list[LiveArtifact]: ...
    async def mark_artifact_deleted(self, artifact_id: UUID, at: datetime, reason: str) -> None: ...
    async def prune_batches(self, before: datetime) -> int: ...
    async def prune_score_history(self, before: datetime) -> int: ...


@dataclass
class Report:
    orphans: int = 0
    staging: int = 0
    raw_expired: int = 0
    artifacts_expired: int = 0
    batches_pruned: int = 0
    scores_pruned: int = 0
    errors: list[str] = field(default_factory=list)


async def run_housekeeping(
    settings: Settings, catalog: HousekeepingCatalog, store: ObjectStore, clock: Clock
) -> Report:
    now = clock()
    report = Report()

    async def delete(key: str) -> bool:
        try:
            await store.delete(key)
        except Exception as e:
            report.errors.append(f"{key}: {e}")
            log.warning("could not delete %s: %s", key, e)
            return False
        return True

    # orphans among final objects
    final = await store.list_objects(LAKE_PREFIX)
    candidates = [
        o for o in final if now - o.last_modified > timedelta(seconds=settings.orphan_grace_s)
    ]
    for i in range(0, len(candidates), _LIST_CHUNK):
        chunk = candidates[i : i + _LIST_CHUNK]
        known = await catalog.known_keys([o.key for o in chunk])
        for key in select_orphans(chunk, known, now, timedelta(seconds=settings.orphan_grace_s)):
            if await delete(key):
                report.orphans += 1

    # stale staging objects
    staging = await store.list_objects(STAGING_PREFIX)
    for key in select_stale(staging, now, timedelta(seconds=settings.staging_max_age_s)):
        if await delete(key):
            report.staging += 1

    # raw retention
    for file_id, key in await catalog.expired_raw_files(
        now.date(), settings.raw_retention_days, settings.raw_retention_max_days
    ):
        if await delete(key):
            await catalog.mark_deleted(file_id, now)
            report.raw_expired += 1

    # raw-derived artefacts follow the retention of the raw data they come from
    for art in expired_artifacts(
        await catalog.live_raw_artifacts(),
        now,
        settings.raw_retention_days,
        settings.raw_retention_max_days,
    ):
        try:
            await delete_prefix(store, art.s3_prefix)
        except Exception as e:
            report.errors.append(f"{art.s3_prefix}: {e}")
            log.warning("could not delete artefact %s: %s", art.s3_prefix, e)
            continue
        await catalog.mark_artifact_deleted(art.id, now, "retention")
        report.artifacts_expired += 1

    report.batches_pruned = await catalog.prune_batches(
        now - timedelta(days=settings.batch_log_retention_days)
    )
    report.scores_pruned = await catalog.prune_score_history(
        now - timedelta(days=settings.score_history_retention_days)
    )
    log.info(
        "housekeeping: %d orphans, %d staging, %d raw expired, %d artefacts expired, "
        "%d batches pruned, %d score rows pruned, %d errors",
        report.orphans,
        report.staging,
        report.raw_expired,
        report.artifacts_expired,
        report.batches_pruned,
        report.scores_pruned,
        len(report.errors),
    )
    return report


async def _main(settings: Settings) -> int:
    catalog = await PgCatalog.open(settings.pg_dsn, 2)
    try:
        report = await run_housekeeping(
            settings, catalog, S3Store.from_settings(settings), system_clock
        )
    finally:
        await catalog.close()
    return 1 if report.errors else 0


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    raise SystemExit(asyncio.run(_main(Settings())))


if __name__ == "__main__":
    main()
