from __future__ import annotations

from datetime import date, datetime, timedelta

from corvus_services.housekeeping import raw_cutoff, run_housekeeping, select_orphans, select_stale
from corvus_services.ports import ObjectInfo
from helpers import T0, FakeClock, make_settings

HOUR = timedelta(hours=1)


def obj(key: str, age: timedelta) -> ObjectInfo:
    return ObjectInfo(key, T0 - age, 1)


def test_orphans_are_unknown_keys_older_than_grace():
    objs = [
        obj("lake/events/a", 2 * HOUR),  # known
        obj("lake/events/b", 2 * HOUR),  # orphan
        obj("lake/events/c", HOUR / 2),  # unknown but too young (flush in progress)
    ]
    assert select_orphans(objs, {"lake/events/a"}, T0, HOUR) == ["lake/events/b"]


def test_stale_staging_is_selected_by_age():
    objs = [obj("_staging/events/x", 2 * HOUR), obj("_staging/events/y", HOUR / 4)]
    assert select_stale(objs, T0, HOUR) == ["_staging/events/x"]


def test_raw_cutoff_is_retention_days_before_today():
    assert raw_cutoff(T0, 21) == date(2026, 9, 24)


class FakeHkCatalog:
    def __init__(self, known: set[str], raw: list[tuple[int, str]]) -> None:
        self.known = known
        self.raw = raw
        self.cutoff: date | None = None
        self.marked: list[int] = []
        self.pruned_before: datetime | None = None
        self.scores_pruned_before: datetime | None = None

    async def known_keys(self, keys: list[str]) -> set[str]:
        return self.known & set(keys)

    async def live_raw_files_before(self, day: date) -> list[tuple[int, str]]:
        self.cutoff = day
        return self.raw

    async def mark_deleted(self, file_id: int, at: datetime) -> None:
        self.marked.append(file_id)

    async def prune_batches(self, before: datetime) -> int:
        self.pruned_before = before
        return 7

    async def prune_score_history(self, before: datetime) -> int:
        self.scores_pruned_before = before
        return 5


class Store:
    def __init__(self, objects: list[ObjectInfo]) -> None:
        self.objects = {o.key: o for o in objects}
        self.deleted: list[str] = []

    async def list_objects(self, prefix: str) -> list[ObjectInfo]:
        return [o for k, o in self.objects.items() if k.startswith(prefix)]

    async def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.objects.pop(key, None)


async def test_run_deletes_orphans_staging_and_expired_raw():
    store = Store(
        [
            obj("lake/events/date=2026-10-15/part-known.parquet", 5 * HOUR),
            obj("lake/events/date=2026-10-15/part-orphan.parquet", 5 * HOUR),
            obj("lake/events/date=2026-10-15/part-fresh.parquet", HOUR / 4),
            obj("_staging/raw/old.parquet", 3 * HOUR),
            obj("_staging/raw/new.parquet", HOUR / 10),
            obj("lake/raw/date=2026-09-01/part-old.parquet", 40 * 24 * HOUR),
        ]
    )
    catalog = FakeHkCatalog(
        known={
            "lake/events/date=2026-10-15/part-known.parquet",
            "lake/raw/date=2026-09-01/part-old.parquet",
        },
        raw=[(11, "lake/raw/date=2026-09-01/part-old.parquet")],
    )
    report = await run_housekeeping(make_settings(), catalog, store, FakeClock())

    assert sorted(store.deleted) == sorted(
        [
            "lake/events/date=2026-10-15/part-orphan.parquet",
            "_staging/raw/old.parquet",
            "lake/raw/date=2026-09-01/part-old.parquet",
        ]
    )
    assert (report.orphans, report.staging, report.raw_expired) == (1, 1, 1)
    assert catalog.marked == [11]
    assert catalog.cutoff == date(2026, 9, 24)
    assert catalog.pruned_before == T0 - timedelta(days=3)
    assert report.batches_pruned == 7


async def test_run_prunes_score_history_after_its_retention():
    catalog = FakeHkCatalog(known=set(), raw=[])

    report = await run_housekeeping(
        make_settings(score_history_retention_days=10), catalog, Store([]), FakeClock()
    )

    assert catalog.scores_pruned_before == T0 - timedelta(days=10)
    assert report.scores_pruned == 5


async def test_catalog_row_is_not_marked_when_object_delete_fails():
    class Failing(Store):
        async def delete(self, key: str) -> None:
            raise OSError("boom")

    catalog = FakeHkCatalog(known=set(), raw=[(1, "lake/raw/date=2020-01-01/p.parquet")])
    report = await run_housekeeping(make_settings(), catalog, Failing([]), FakeClock())
    assert catalog.marked == []
    assert len(report.errors) == 1
