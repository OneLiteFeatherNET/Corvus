from __future__ import annotations

from datetime import date, datetime, timedelta
from uuid import UUID, uuid4

from corvus_services.housekeeping import (
    expired_artifacts,
    run_housekeeping,
    select_orphans,
    select_stale,
)
from corvus_services.ports import ObjectInfo
from corvus_services.retention import LiveArtifact
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


class FakeHkCatalog:
    def __init__(
        self,
        known: set[str],
        raw: list[tuple[int, str]],
        artifacts: list[LiveArtifact] | None = None,
    ) -> None:
        self.known = known
        self.raw = raw
        self.artifacts = artifacts or []
        self.expiry_args: tuple[date, int, int] | None = None
        self.marked: list[int] = []
        self.artifacts_marked: list[tuple[UUID, str]] = []
        self.pruned_before: datetime | None = None
        self.scores_pruned_before: datetime | None = None

    async def known_keys(self, keys: list[str]) -> set[str]:
        return self.known & set(keys)

    async def expired_raw_files(
        self, today: date, default_days: int, max_days: int
    ) -> list[tuple[int, str]]:
        self.expiry_args = (today, default_days, max_days)
        return self.raw

    async def live_raw_artifacts(self) -> list[LiveArtifact]:
        return self.artifacts

    async def mark_artifact_deleted(self, artifact_id: UUID, at: datetime, reason: str) -> None:
        self.artifacts_marked.append((artifact_id, reason))

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
    assert catalog.expiry_args == (T0.date(), 21, 365)
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


def test_artifact_expires_when_the_retention_of_its_pool_has_passed_since_the_newest_session():
    old = LiveArtifact(uuid4(), "features/a/raw/", T0 - timedelta(days=22), None)
    fresh = LiveArtifact(uuid4(), "features/b/raw/", T0 - timedelta(days=20), None)

    assert expired_artifacts([old, fresh], T0, 21, 365) == [old]


def test_a_pool_retention_extends_or_shortens_the_artifact_lifetime():
    longer = LiveArtifact(uuid4(), "features/a/raw/", T0 - timedelta(days=40), 60)
    shorter = LiveArtifact(uuid4(), "features/b/raw/", T0 - timedelta(days=8), 7)

    assert expired_artifacts([longer, shorter], T0, 21, 365) == [shorter]


def test_a_pool_retention_above_the_maximum_is_capped():
    capped = LiveArtifact(uuid4(), "features/a/raw/", T0 - timedelta(days=40), 3650)

    assert expired_artifacts([capped], T0, 21, 30) == [capped]


async def test_expired_artifact_prefix_is_deleted_and_marked_retention():
    art = LiveArtifact(uuid4(), "features/s/raw/", T0 - timedelta(days=30), None)
    store = Store(
        [obj("features/s/raw/packets/part-0.parquet", HOUR), obj("features/s/other.json", HOUR)]
    )
    catalog = FakeHkCatalog(known=set(), raw=[], artifacts=[art])

    report = await run_housekeeping(make_settings(), catalog, store, FakeClock())

    assert store.deleted == ["features/s/raw/packets/part-0.parquet"]
    assert catalog.artifacts_marked == [(art.id, "retention")]
    assert report.artifacts_expired == 1


async def test_artifact_stays_registered_when_deleting_its_objects_fails():
    class Failing(Store):
        async def delete(self, key: str) -> None:
            raise OSError("boom")

    art = LiveArtifact(uuid4(), "features/s/raw/", T0 - timedelta(days=30), None)
    catalog = FakeHkCatalog(known=set(), raw=[], artifacts=[art])

    report = await run_housekeeping(
        make_settings(), catalog, Failing([obj("features/s/raw/x", HOUR)]), FakeClock()
    )

    assert catalog.artifacts_marked == []
    assert len(report.errors) == 1


async def test_a_registered_prefix_outside_the_derived_roots_is_never_deleted():
    art = LiveArtifact(uuid4(), "lake/raw/", T0 - timedelta(days=30), None)
    store = Store([obj("lake/raw/date=2026-01-01/p.parquet", 100 * 24 * HOUR)])
    catalog = FakeHkCatalog(known={"lake/raw/date=2026-01-01/p.parquet"}, raw=[], artifacts=[art])

    report = await run_housekeeping(make_settings(), catalog, store, FakeClock())

    assert store.deleted == []
    assert catalog.artifacts_marked == []
    assert len(report.errors) == 1
