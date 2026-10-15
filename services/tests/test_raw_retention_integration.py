"""SQL of raw retention, derived artefacts and erasure on a real PostgreSQL. Skipped by default.

cd deploy/dev && docker compose up -d --wait postgres
cd services && CORVUS_PG_DSN=... PGPASSWORD=... \
    uv run pytest -m integration tests/test_raw_retention_integration.py

Every test seeds its own players, sessions, files and snapshots with random ids and looks only at
those ids, so the tests neither depend on each other nor on what else is in the database.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest

from corvus_services.api.pg import Pool, open_pool, open_repos
from corvus_services.api.pools import PoolFilter
from corvus_services.api.ports import ArtifactRecord, PoolRecord, SnapshotRecord, StaleBuildError
from corvus_services.api.snapshots import build_manifest, manifest_sha256
from corvus_services.compact import DerivedItem
from corvus_services.compact_pg import PgCompactCatalog
from corvus_services.config import Settings
from corvus_services.migrate import PgExecutor, load_migrations, run
from corvus_services.pgcatalog import PgCatalog
from corvus_services.retention import LiveArtifact

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
TODAY = NOW.date()
DSN = Settings().pg_dsn


@pytest.fixture(scope="module", autouse=True)
def _migrated() -> None:
    migrations = load_migrations(Path(__file__).resolve().parents[1] / "migrations")
    with psycopg.connect(DSN, autocommit=True) as conn:
        run(migrations, PgExecutor(conn))


@pytest.fixture
async def pool():
    p: Pool = await open_pool(DSN, 3)
    try:
        yield p
    finally:
        await p.close()


@pytest.fixture
async def repos():
    r = await open_repos(DSN, 2)
    try:
        yield r
    finally:
        for close in r.closers:
            await close()


@pytest.fixture
async def housekeeping():
    c = await PgCatalog.open(DSN, 2)
    try:
        yield c
    finally:
        await c.close()


@pytest.fixture
async def compaction():
    c = await PgCompactCatalog.open(DSN, 2)
    try:
        yield c
    finally:
        await c.close()


async def seed_session(
    pool: Pool, *, protocol: int = 770, started: datetime = NOW
) -> tuple[bytes, bytes]:
    pseudonym, session_id = os.urandom(32), os.urandom(16)
    async with pool.connection() as conn, conn.transaction():
        await conn.execute(
            "INSERT INTO corvus.players (pseudonym, key_id, first_seen, last_seen)"
            " VALUES (%s, 'k1', %s, %s)",
            (pseudonym, NOW, NOW),
        )
        await conn.execute(
            "INSERT INTO corvus.sessions (session_id, pseudonym, proxy_id, client_protocol,"
            " observed_protocol, plugin_version, started_at)"
            " VALUES (%s, %s, 'proxy', %s, %s, '1', %s)",
            (session_id, pseudonym, protocol, protocol, started),
        )
    return pseudonym, session_id


async def seed_raw_file(pool: Pool, day: date, sessions: list[bytes], table: str = "raw") -> int:
    async with pool.connection() as conn, conn.transaction():
        cur = await conn.execute(
            "INSERT INTO corvus.lake_files (s3_key, table_name, date, row_count, size_bytes)"
            " VALUES (%s, %s, %s, 10, 1) RETURNING id",
            (f"lake/{table}/it/{uuid4().hex}.parquet", table, day),
        )
        row = await cur.fetchone()
        assert row is not None
        for s in sessions:
            await conn.execute(
                "INSERT INTO corvus.lake_file_sessions (file_id, session_id) VALUES (%s, %s)",
                (int(row[0]), s),
            )
    return int(row[0])


async def seed_pool(repos: Any, days: int | None) -> PoolRecord:
    rec = PoolRecord(
        uuid4(), f"it-{uuid4().hex}", "", PoolFilter(), "it", NOW, NOW, raw_retention_days=days
    )
    await repos.pools.create(rec)
    return rec


async def seed_snapshot(repos: Any, pool_rec: PoolRecord, sessions: list[bytes]) -> SnapshotRecord:
    manifest = build_manifest(
        pool_id=pool_rec.id,
        pool_name=pool_rec.name,
        pool_filter={},
        session_ids=sessions,
        files=[],
        created_at=NOW,
    )
    snap = SnapshotRecord(
        uuid4(), pool_rec.id, pool_rec.name, manifest, manifest_sha256(manifest),
        len(sessions), 0, "it", NOW,
    )  # fmt: skip
    await repos.snapshots.insert(snap)
    return snap


async def seed_job(pool: Pool, snapshot_id: UUID, created: datetime) -> UUID:
    job_id = uuid4()
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO corvus.jobs (id, type, snapshot_id, status, runner, created_by,"
            " created_at, updated_at) VALUES (%s, 'build_features', %s, 'running', 'local', 'it',"
            " %s, %s)",
            (job_id, snapshot_id, created, created),
        )
    return job_id


def artifact(snapshot_id: UUID, job_id: UUID | None = None, *, raw: bool = True) -> ArtifactRecord:
    return ArtifactRecord(
        uuid4(), snapshot_id, job_id, "raw_features", f"features/{snapshot_id}/raw/", raw, NOW
    )


# --- migrations ----------------------------------------------------------------------------------


async def test_the_pool_retention_column_is_nullable_and_bounded(pool: Pool, repos: Any):
    default = await seed_pool(repos, None)
    ninety = await seed_pool(repos, 90)

    assert (await repos.pools.get(default.id)).raw_retention_days is None
    assert (await repos.pools.get(ninety.id)).raw_retention_days == 90
    with pytest.raises(psycopg.errors.CheckViolation):
        await seed_pool(repos, 0)


# --- snapshot data -------------------------------------------------------------------------------


async def test_sessions_carry_their_client_protocol(pool: Pool, repos: Any):
    _, a = await seed_session(pool, protocol=767)
    _, b = await seed_session(pool, protocol=769)
    snap = await seed_snapshot(repos, await seed_pool(repos, None), [a, b])

    sessions = await repos.snapshots.sessions_page(snap.id, None, 10, False)

    assert {s.session_id: s.client_protocol for s in sessions} == {a: 767, b: 769}


async def test_raw_files_of_the_snapshot_sessions_are_listed_and_follow_compaction(
    pool: Pool, repos: Any
):
    _, a = await seed_session(pool)
    _, b = await seed_session(pool)
    _, other = await seed_session(pool)
    snap = await seed_snapshot(repos, await seed_pool(repos, None), [a, b])
    both = await seed_raw_file(pool, TODAY, [a, b])
    unrelated = await seed_raw_file(pool, TODAY, [other])
    events = await seed_raw_file(pool, TODAY, [a], table="events")
    async with pool.connection() as conn:  # compaction replaced `both` by a successor
        cur = await conn.execute(
            "INSERT INTO corvus.lake_files (s3_key, table_name, date, row_count, size_bytes)"
            " VALUES (%s, 'raw', %s, 5, 1) RETURNING id",
            (f"lake/raw/it/{uuid4().hex}.parquet", TODAY),
        )
        row = await cur.fetchone()
        successor = int(row[0])
        await conn.execute(
            "INSERT INTO corvus.lake_file_sessions (file_id, session_id) VALUES (%s, %s)",
            (successor, a),
        )
        await conn.execute(
            "UPDATE corvus.lake_files SET deleted_at = %s, replaced_by = %s WHERE id = %s",
            (NOW, successor, both),
        )
        await conn.execute("DELETE FROM corvus.lake_file_sessions WHERE file_id = %s", (both,))

    raw = await repos.snapshots.current_raw_files(snap.id)

    ids = {f.id for f in raw}
    assert successor in ids
    assert both not in ids
    assert unrelated not in ids
    assert events not in ids


# --- registration of artefacts -------------------------------------------------------------------


async def test_registering_a_prefix_twice_keeps_one_row(pool: Pool, repos: Any):
    snap = await seed_snapshot(repos, await seed_pool(repos, None), [])

    first, created = await repos.snapshots.register_artifact(artifact(snap.id), None)
    again, created_again = await repos.snapshots.register_artifact(artifact(snap.id), None)

    assert (created, created_again) == (True, False)
    assert again.id == first.id
    assert len(await repos.snapshots.list_artifacts(snap.id)) == 1


async def test_a_prefix_outside_the_derived_roots_violates_the_check(pool: Pool, repos: Any):
    snap = await seed_snapshot(repos, await seed_pool(repos, None), [])
    bad = ArtifactRecord(uuid4(), snap.id, None, "raw_features", "lake/raw/", True, NOW)

    with pytest.raises(psycopg.errors.CheckViolation):
        await repos.snapshots.register_artifact(bad, None)


async def test_a_snapshot_marked_for_rebuild_refuses_older_builds_and_accepts_newer_ones(
    pool: Pool, repos: Any, compaction: PgCompactCatalog
):
    snap = await seed_snapshot(repos, await seed_pool(repos, None), [])
    old_job = await seed_job(pool, snap.id, NOW - timedelta(hours=2))
    new_job = await seed_job(pool, snap.id, NOW + timedelta(hours=1))
    stored, _ = await repos.snapshots.register_artifact(
        artifact(snap.id, old_job), NOW - timedelta(hours=2)
    )
    await compaction.invalidate_artifact(
        DerivedItem(stored.id, snap.id, stored.s3_prefix, NOW), NOW + timedelta(minutes=10)
    )
    assert (await repos.snapshots.get(snap.id)).needs_rebuild is True
    assert await repos.snapshots.list_artifacts(snap.id) == []  # the row is deleted

    with pytest.raises(StaleBuildError):
        await repos.snapshots.register_artifact(
            artifact(snap.id, old_job), NOW - timedelta(hours=2)
        )
    rebuilt, created = await repos.snapshots.register_artifact(
        artifact(snap.id, new_job), NOW + timedelta(hours=1)
    )

    assert created is False  # the deleted row of the same prefix is revived
    assert rebuilt.id == stored.id
    assert (await repos.snapshots.get(snap.id)).needs_rebuild is False
    assert len(await repos.snapshots.list_artifacts(snap.id)) == 1


# --- erasure: which artefacts are deleted --------------------------------------------------------


async def erase_request(pool: Pool, pseudonym: bytes, at: datetime) -> UUID:
    request_id = uuid4()
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO corvus.erasure_requests (id, pseudonym, requested_by, requested_at,"
            " status, player_known, key_deleted, compaction_pending)"
            " VALUES (%s, %s, 'it', %s, 'completed', true, true, true)",
            (request_id, pseudonym, at),
        )
    return request_id


async def test_erasure_selects_raw_artifacts_of_snapshots_with_the_players_sessions(
    pool: Pool, repos: Any, compaction: PgCompactCatalog
):
    victim, victim_session = await seed_session(pool)
    _, bystander_session = await seed_session(pool)
    with_victim = await seed_snapshot(repos, await seed_pool(repos, None), [victim_session])
    without_victim = await seed_snapshot(repos, await seed_pool(repos, None), [bystander_session])
    erasure_at = NOW
    before = await seed_job(pool, with_victim.id, NOW - timedelta(hours=1))
    after = await seed_job(pool, with_victim.id, NOW + timedelta(hours=1))
    raw_before, _ = await repos.snapshots.register_artifact(artifact(with_victim.id, before), None)
    clean_after = ArtifactRecord(
        uuid4(),
        with_victim.id,
        after,
        "raw_features",
        f"features/{with_victim.id}/rebuilt/",
        True,
        NOW,
    )
    await repos.snapshots.register_artifact(clean_after, None)
    no_raw = ArtifactRecord(
        uuid4(),
        with_victim.id,
        None,
        "aggregate_features",
        f"features/{with_victim.id}/windows/",
        False,
        NOW,
    )
    await repos.snapshots.register_artifact(no_raw, None)
    await repos.snapshots.register_artifact(artifact(without_victim.id), None)
    request = await erase_request(pool, victim, erasure_at)

    items = await compaction.derived_for_requests([request])

    assert [(i.id, i.snapshot_id) for i in items] == [(raw_before.id, with_victim.id)]
    assert items[0].erased_at == erasure_at


async def test_invalidating_an_artifact_marks_the_snapshot_and_hides_the_artifact(
    pool: Pool, repos: Any, compaction: PgCompactCatalog
):
    snap = await seed_snapshot(repos, await seed_pool(repos, None), [])
    stored, _ = await repos.snapshots.register_artifact(artifact(snap.id), None)
    item = DerivedItem(stored.id, snap.id, stored.s3_prefix, NOW)

    await compaction.invalidate_artifact(item, NOW)
    await compaction.invalidate_artifact(item, NOW)  # a retry changes nothing

    assert (await repos.snapshots.get(snap.id)).needs_rebuild is True
    assert await repos.snapshots.list_artifacts(snap.id) == []
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT deleted_reason FROM corvus.derived_artifacts WHERE id = %s", (stored.id,)
        )
        assert (await cur.fetchone()) == ("erasure",)


# --- retention: raw files ------------------------------------------------------------------------


def days_ago(n: int) -> date:
    return TODAY - timedelta(days=n)


async def expired(housekeeping: PgCatalog, *ids: int) -> set[int]:
    got = {fid for fid, _ in await housekeeping.expired_raw_files(TODAY, 21, 365)}
    return got & set(ids)


async def test_a_file_in_no_snapshot_expires_after_the_default_retention(
    pool: Pool, housekeeping: PgCatalog
):
    _, s = await seed_session(pool)
    old = await seed_raw_file(pool, days_ago(22), [s])
    young = await seed_raw_file(pool, days_ago(20), [s])
    empty = await seed_raw_file(pool, days_ago(30), [])

    assert await expired(housekeeping, old, young, empty) == {old, empty}


async def test_a_pool_with_a_longer_retention_keeps_the_file(
    pool: Pool, repos: Any, housekeeping: PgCatalog
):
    _, s = await seed_session(pool)
    await seed_snapshot(repos, await seed_pool(repos, 90), [s])
    file_id = await seed_raw_file(pool, days_ago(60), [s])
    older = await seed_raw_file(pool, days_ago(91), [s])

    assert await expired(housekeeping, file_id, older) == {older}


async def test_a_pool_with_a_shorter_retention_expires_the_file_earlier(
    pool: Pool, repos: Any, housekeeping: PgCatalog
):
    _, s = await seed_session(pool)
    await seed_snapshot(repos, await seed_pool(repos, 7), [s])
    file_id = await seed_raw_file(pool, days_ago(8), [s])
    young = await seed_raw_file(pool, days_ago(6), [s])

    assert await expired(housekeeping, file_id, young) == {file_id}


async def test_the_longest_retention_of_all_referencing_pools_wins(
    pool: Pool, repos: Any, housekeeping: PgCatalog
):
    _, s = await seed_session(pool)
    await seed_snapshot(repos, await seed_pool(repos, 7), [s])
    await seed_snapshot(repos, await seed_pool(repos, 60), [s])
    file_id = await seed_raw_file(pool, days_ago(30), [s])

    assert await expired(housekeeping, file_id) == set()


async def test_one_session_with_a_long_retention_keeps_the_whole_file(
    pool: Pool, repos: Any, housekeeping: PgCatalog
):
    _, short = await seed_session(pool)
    _, long = await seed_session(pool)
    await seed_snapshot(repos, await seed_pool(repos, 7), [short])
    await seed_snapshot(repos, await seed_pool(repos, 120), [long])
    file_id = await seed_raw_file(pool, days_ago(50), [short, long])

    assert await expired(housekeeping, file_id) == set()


async def test_a_session_outside_every_snapshot_counts_with_the_default(
    pool: Pool, repos: Any, housekeeping: PgCatalog
):
    _, in_short_pool = await seed_session(pool)
    _, in_no_snapshot = await seed_session(pool)
    await seed_snapshot(repos, await seed_pool(repos, 7), [in_short_pool])
    file_id = await seed_raw_file(pool, days_ago(15), [in_short_pool, in_no_snapshot])
    other = await seed_raw_file(pool, days_ago(15), [in_short_pool])

    assert await expired(housekeeping, file_id, other) == {other}


async def test_a_deleted_pool_falls_back_to_the_default(
    pool: Pool, repos: Any, housekeeping: PgCatalog
):
    _, s = await seed_session(pool)
    rec = await seed_pool(repos, 90)
    await seed_snapshot(repos, rec, [s])
    await repos.pools.delete(rec.id)
    file_id = await seed_raw_file(pool, days_ago(30), [s])

    assert await expired(housekeeping, file_id) == {file_id}


async def test_a_pool_value_above_the_maximum_is_capped(
    pool: Pool, repos: Any, housekeeping: PgCatalog
):
    _, s = await seed_session(pool)
    await seed_snapshot(repos, await seed_pool(repos, 3000), [s])
    file_id = await seed_raw_file(pool, days_ago(366), [s])

    got = {fid for fid, _ in await housekeeping.expired_raw_files(TODAY, 21, 365)}

    assert file_id in got


async def test_deleted_files_are_not_selected_again(pool: Pool, housekeeping: PgCatalog):
    _, s = await seed_session(pool)
    file_id = await seed_raw_file(pool, days_ago(40), [s])
    await housekeeping.mark_deleted(file_id, NOW)

    assert await expired(housekeeping, file_id) == set()


# --- retention: raw-derived artefacts ------------------------------------------------------------


async def test_live_raw_artifacts_report_the_newest_session_and_the_pool_retention(
    pool: Pool, repos: Any, housekeeping: PgCatalog
):
    _, early = await seed_session(pool, started=NOW - timedelta(days=5))
    _, late = await seed_session(pool, started=NOW - timedelta(days=2))
    snap = await seed_snapshot(repos, await seed_pool(repos, 45), [early, late])
    stored, _ = await repos.snapshots.register_artifact(artifact(snap.id), None)
    aggregates = ArtifactRecord(
        uuid4(), snap.id, None, "aggregate_features", f"features/{snap.id}/windows/", False, NOW
    )
    await repos.snapshots.register_artifact(aggregates, None)

    live = {a.id: a for a in await housekeeping.live_raw_artifacts()}

    assert aggregates.id not in live
    assert live[stored.id] == LiveArtifact(stored.id, stored.s3_prefix, NOW - timedelta(days=2), 45)


async def test_a_snapshot_without_catalog_sessions_uses_its_creation_time(
    pool: Pool, repos: Any, housekeeping: PgCatalog
):
    snap = await seed_snapshot(repos, await seed_pool(repos, None), [os.urandom(16)])
    stored, _ = await repos.snapshots.register_artifact(artifact(snap.id), None)

    live = {a.id: a for a in await housekeeping.live_raw_artifacts()}

    assert live[stored.id].newest_source == NOW
    assert live[stored.id].pool_days is None


async def test_an_expired_artifact_is_marked_and_no_longer_live(
    pool: Pool, repos: Any, housekeeping: PgCatalog
):
    snap = await seed_snapshot(repos, await seed_pool(repos, None), [])
    stored, _ = await repos.snapshots.register_artifact(artifact(snap.id), None)

    await housekeeping.mark_artifact_deleted(stored.id, NOW, "retention")

    assert stored.id not in {a.id for a in await housekeeping.live_raw_artifacts()}
    assert (await repos.snapshots.get(snap.id)).needs_rebuild is False  # retention is no rebuild
