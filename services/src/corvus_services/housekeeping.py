"""``corvus-housekeeping``: one-shot lake cleanup for a CronJob.

* final objects without a catalog row and older than the grace period are orphans (a flush that
  crashed between the copy and the catalog commit) and are deleted;
* ``_staging/`` objects older than the staging age are leftovers and are deleted;
* ``raw`` files whose date partition is older than the retention are deleted and marked
  ``deleted_at`` in the catalog;
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

from .clock import Clock, system_clock
from .config import Settings
from .pgcatalog import PgCatalog
from .ports import ObjectInfo, ObjectStore
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


def raw_cutoff(now: datetime, retention_days: int) -> date:
    """Raw files with a date partition strictly before this day are expired."""
    return (now - timedelta(days=retention_days)).date()


class HousekeepingCatalog(Protocol):
    async def known_keys(self, keys: list[str]) -> set[str]: ...
    async def live_raw_files_before(self, day: date) -> list[tuple[int, str]]: ...
    async def mark_deleted(self, file_id: int, at: datetime) -> None: ...
    async def prune_batches(self, before: datetime) -> int: ...
    async def prune_score_history(self, before: datetime) -> int: ...


@dataclass
class Report:
    orphans: int = 0
    staging: int = 0
    raw_expired: int = 0
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
    for file_id, key in await catalog.live_raw_files_before(
        raw_cutoff(now, settings.raw_retention_days)
    ):
        if await delete(key):
            await catalog.mark_deleted(file_id, now)
            report.raw_expired += 1

    report.batches_pruned = await catalog.prune_batches(
        now - timedelta(days=settings.batch_log_retention_days)
    )
    report.scores_pruned = await catalog.prune_score_history(
        now - timedelta(days=settings.score_history_retention_days)
    )
    log.info(
        "housekeeping: %d orphans, %d staging, %d raw expired, %d batches pruned, "
        "%d score rows pruned, %d errors",
        report.orphans,
        report.staging,
        report.raw_expired,
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
