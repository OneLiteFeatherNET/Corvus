"""Control-plane SQL against a real PostgreSQL (dev compose stack). Skipped by default.

cd deploy/dev && docker compose up -d --wait postgres
cd services && CORVUS_PG_DSN=... PGPASSWORD=... uv run pytest -m integration tests/api

Every test seeds its own data under a random ``game_context`` and filters on it, so tests neither depend
on each other nor on what else is in the database.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from corvus_services.api.erasure import erase_player
from corvus_services.api.pagination import PageRequest
from corvus_services.api.pg import Pool, open_pool, open_repos
from corvus_services.api.pools import PoolFilter, compute_preview
from corvus_services.api.ports import (
    DuplicateError,
    LabelRecord,
    ModelRecord,
    PoolRecord,
    Repos,
    SnapshotRecord,
    StaleError,
    TransitionRecord,
)
from corvus_services.api.registry import Stage, transition_model
from corvus_services.api.snapshots import build_manifest, manifest_sha256
from corvus_services.config import Settings
from corvus_services.migrate import PgExecutor, load_migrations, run

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
DSN = Settings().pg_dsn


@pytest.fixture(scope="module", autouse=True)
def _migrated() -> None:
    migrations = load_migrations(Path(__file__).resolve().parents[2] / "migrations")
    with psycopg.connect(DSN, autocommit=True) as conn:
        run(migrations, PgExecutor(conn))


@pytest.fixture
async def repos():
    r = await open_repos(DSN, 2)
    try:
        yield r
    finally:
        for close in r.closers:
            await close()


@pytest.fixture
async def pool():
    p: Pool = await open_pool(DSN, 2)
    try:
        yield p
    finally:
        await p.close()


async def _seed(pool: Pool, context: str, *, sessions: int = 3) -> dict[str, list[bytes] | int]:
    """players + sessions + one segment each + one events file holding all of them."""
    pseudonyms = [os.urandom(32) for _ in range(sessions)]
    ids = [os.urandom(16) for _ in range(sessions)]
    async with pool.connection() as conn, conn.transaction():
        for i, (p, s) in enumerate(zip(pseudonyms, ids, strict=True)):
            await conn.execute(
                "INSERT INTO corvus.players (pseudonym, key_id, first_seen, last_seen)"
                " VALUES (%s, 'k1', %s, %s)",
                (p, NOW, NOW),
            )
            await conn.execute(
                "INSERT INTO corvus.player_keys (pseudonym, dek_wrapped, kek_id) VALUES (%s, %s, 'kek')",
                (p, b"wrapped"),
            )
            await conn.execute(
                "INSERT INTO corvus.sessions (session_id, pseudonym, proxy_id, client_protocol,"
                " observed_protocol, plugin_version, started_at, ended_at)"
                " VALUES (%s, %s, 'proxy', 770, 770, '1', %s, %s)",
                (s, p, NOW + timedelta(minutes=i), NOW + timedelta(minutes=i, seconds=120)),
            )
            await conn.execute(
                "INSERT INTO corvus.segments (session_id, segment, backend_server, game_context,"
                " client_brand, started_at) VALUES (%s, 0, 'bw-1', %s, 'vanilla', %s)",
                (s, context, NOW),
            )
        key = f"lake/events/it/{uuid4().hex}.parquet"
        cur = await conn.execute(
            "INSERT INTO corvus.lake_files (s3_key, table_name, date, row_count, size_bytes)"
            " VALUES (%s, 'events', %s, 3000, 1) RETURNING id",
            (key, NOW.date()),
        )
        row = await cur.fetchone()
        assert row is not None
        file_id = int(row[0])
        for s in ids:
            await conn.execute(
                "INSERT INTO corvus.lake_file_sessions (file_id, session_id) VALUES (%s, %s)",
                (file_id, s),
            )
    return {"pseudonyms": pseudonyms, "sessions": ids, "file_id": file_id}


def _filter(context: str, **extra) -> PoolFilter:
    return PoolFilter.model_validate({"game_contexts": [context], **extra})


async def test_preview_counts_sessions_players_frames_and_labels(repos: Repos, pool: Pool):
    context = f"it-{uuid4().hex}"
    data = await _seed(pool, context)
    first = data["pseudonyms"][0]
    label = LabelRecord(
        uuid4(), "pseudonym", first.hex(), "cheat", "staff_verdict", 0.9, None, None, "", "it", NOW
    )
    await repos.labels.add_many([label])

    out = compute_preview(await repos.catalog.preview(_filter(context)))

    assert (out.sessions, out.segments, out.distinct_players, out.events_files) == (3, 3, 3, 1)
    assert out.total_frames_estimate == 3000
    assert out.label_distribution == {"legit": 0, "cheat": 1, "unlabeled": 2}


async def test_pool_filters_select_the_expected_sessions(repos: Repos, pool: Pool):
    context = f"it-{uuid4().hex}"
    await _seed(pool, context)

    async def sessions(**extra) -> int:
        return (await repos.catalog.preview(_filter(context, **extra))).sessions

    assert await sessions(client_protocol={"min": 770, "max": 770}) == 3
    assert await sessions(client_protocol={"min": 771}) == 0
    assert await sessions(client_brand={"exclude": ["vanilla"]}) == 0
    assert await sessions(client_brand={"include": ["vanilla"]}) == 3
    assert await sessions(min_session_duration_s=60) == 3
    assert await sessions(min_session_duration_s=600) == 0
    assert await sessions(date_range={"start": "2026-10-15", "end": "2026-10-15"}) == 3
    assert await sessions(date_range={"end": "2026-10-14"}) == 0
    assert await sessions(max_drop_rate=0.0) == 3
    assert await sessions(label={"value": "unlabeled"}) == 3
    assert await sessions(label={"value": "cheat"}) == 0


async def test_erased_players_are_never_selected(repos: Repos, pool: Pool):
    context = f"it-{uuid4().hex}"
    data = await _seed(pool, context)
    await erase_player(repos.erasure, data["pseudonyms"][0], requested_by="it", now=NOW)

    out = await repos.catalog.preview(_filter(context))

    assert out.sessions == 2


async def test_snapshot_material_matches_the_filter_and_snapshots_are_immutable(
    repos: Repos, pool: Pool
):
    context = f"it-{uuid4().hex}"
    data = await _seed(pool, context)
    material = await repos.catalog.snapshot_material(_filter(context))
    assert sorted(material.session_ids) == sorted(data["sessions"])
    assert [f.id for f in material.files] == [data["file_id"]]

    pool_id = uuid4()
    manifest = build_manifest(
        pool_id=pool_id,
        pool_name="it",
        pool_filter=_filter(context).model_dump(mode="json"),
        session_ids=material.session_ids,
        files=material.files,
        created_at=NOW,
    )
    snap = SnapshotRecord(
        uuid4(), pool_id, "it", manifest, manifest_sha256(manifest), 3, 1, "it", NOW
    )
    await repos.snapshots.insert(snap)
    stored = await repos.snapshots.get(snap.id)
    assert stored is not None
    assert manifest_sha256(stored.manifest) == stored.sha256

    async with pool.connection() as conn:
        with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
            await conn.execute(
                "UPDATE corvus.dataset_snapshots SET sha256 = %s WHERE id = %s", ("0" * 64, snap.id)
            )
    async with pool.connection() as conn:
        with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
            await conn.execute("DELETE FROM corvus.dataset_snapshots WHERE id = %s", (snap.id,))


async def test_erasure_shreds_the_key_marks_the_player_and_blocklists(repos: Repos, pool: Pool):
    context = f"it-{uuid4().hex}"
    pseudonym = (await _seed(pool, context, sessions=1))["pseudonyms"][0]

    request = await erase_player(repos.erasure, pseudonym, requested_by="root", now=NOW)

    assert request.key_deleted and request.compaction_pending
    player = await repos.players.get(pseudonym)
    assert player is not None and player.erased_at == NOW
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM corvus.player_keys WHERE pseudonym = %s", (pseudonym,)
        )
        row = await cur.fetchone()
        assert row is not None and row[0] == 0
    entry = await repos.blocklist.get(pseudonym)
    assert entry is not None and (entry.kind, entry.source) == ("block", "erasure")
    assert await repos.erasure.get_request(request.id) == request


async def test_erasure_rolls_back_completely_when_a_step_fails(repos: Repos, pool: Pool):
    context = f"it-{uuid4().hex}"
    pseudonym = (await _seed(pool, context, sessions=1))["pseudonyms"][0]

    with pytest.raises(RuntimeError, match="boom"):
        async with repos.erasure.transaction() as tx:
            await tx.lock_player(pseudonym)
            await tx.delete_player_key(pseudonym)
            await tx.mark_erased(pseudonym, NOW)
            raise RuntimeError("boom")

    player = await repos.players.get(pseudonym)
    assert player is not None and player.erased_at is None
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM corvus.player_keys WHERE pseudonym = %s", (pseudonym,)
        )
        row = await cur.fetchone()
        assert row is not None and row[0] == 1, "the key must survive a rolled back erasure"


async def test_pools_are_unique_by_name_and_paginate_by_cursor(repos: Repos):
    tag = uuid4().hex
    created = []
    for i in range(3):
        rec = PoolRecord(
            uuid4(), f"{tag}-{i}", "", PoolFilter(), "it", NOW + timedelta(seconds=i), NOW
        )
        await repos.pools.create(rec)
        created.append(rec)
    with pytest.raises(DuplicateError):
        await repos.pools.create(PoolRecord(uuid4(), f"{tag}-0", "", PoolFilter(), "it", NOW, NOW))

    seen: list[PoolRecord] = []
    page = await repos.pools.list_page(PageRequest(2, None))
    while True:
        seen += [p for p in page.items if p.name.startswith(tag)]
        if page.next_cursor is None:
            break
        from corvus_services.api.pagination import Cursor

        page = await repos.pools.list_page(PageRequest(2, Cursor.decode(page.next_cursor)))

    assert [p.name for p in seen] == [f"{tag}-2", f"{tag}-1", f"{tag}-0"]
    for rec in created:
        assert await repos.pools.delete(rec.id)


async def test_model_transition_is_atomic_and_audited(repos: Repos, pool: Pool):
    context = f"it-{uuid4().hex}"
    await _seed(pool, context)
    pool_id = uuid4()
    manifest = build_manifest(
        pool_id=pool_id, pool_name="it", pool_filter={}, session_ids=[], files=[], created_at=NOW
    )
    snap = SnapshotRecord(
        uuid4(), pool_id, "it", manifest, manifest_sha256(manifest), 0, 0, "it", NOW
    )
    await repos.snapshots.insert(snap)

    def model(version: str, stage: str, recall: float) -> ModelRecord:
        return ModelRecord(
            uuid4(), f"it-{context}", version, "gbdt", context, "movement", snap.id,
            {"recall_at_fpr_0_1pct": recall, "p99_latency_ms": 3}, "k", stage, "it", NOW,
        )  # fmt: skip

    old, new = model("1", "enforce", 0.7), model("2", "canary", 0.8)
    await repos.models.create(old)
    await repos.models.create(new)
    with pytest.raises(DuplicateError):
        await repos.models.create(model("1", "registered", 0.1))

    promoted = await transition_model(
        repos.models, new.id, Stage.ENFORCE, actor="it", reason="r", now=NOW, max_p99_latency_ms=10
    )

    assert promoted.stage == "enforce"
    archived = await repos.models.get(old.id)
    assert archived is not None and archived.stage == "archived"
    assert [t.to_stage for t in await repos.models.transitions(new.id)] == ["enforce"]
    assert [t.to_stage for t in await repos.models.transitions(old.id)] == ["archived"]

    stale = TransitionRecord(uuid4(), new.id, "canary", "archived", "it", "", None, NOW)
    with pytest.raises(StaleError):
        await repos.models.apply_transition(new.id, "canary", [stale])


async def test_job_status_updates_are_persisted(repos: Repos, pool: Pool):
    from corvus_services.api.ports import JobRecord

    pool_id = uuid4()
    manifest = build_manifest(
        pool_id=pool_id, pool_name="it", pool_filter={}, session_ids=[], files=[], created_at=NOW
    )
    snap = SnapshotRecord(
        uuid4(), pool_id, "it", manifest, manifest_sha256(manifest), 0, 0, "it", NOW
    )
    await repos.snapshots.insert(snap)
    job = JobRecord(
        uuid4(), "train", snap.id, {"a": 1}, "queued", "local", None, "", "it", NOW, NOW
    )
    await repos.jobs.create(job)

    updated = await repos.jobs.update(
        job.id, status="succeeded", runner_ref="ref", message="", now=NOW, finished=True
    )

    assert (updated.status, updated.runner_ref, updated.finished_at) == ("succeeded", "ref", NOW)
    assert updated.params == {"a": 1}
    assert (await repos.jobs.get(job.id)) == updated


async def test_blocklist_feed_lists_pseudonyms_by_kind(repos: Repos):
    from corvus_services.api.ports import BlockEntry

    blocked, allowed = os.urandom(32), os.urandom(32)
    await repos.blocklist.put(BlockEntry(blocked, "block", "manual", "it", "it", NOW))
    await repos.blocklist.put(BlockEntry(allowed, "allow", "manual", "it", "it", NOW))

    b, a = await repos.blocklist.all_pseudonyms()

    assert blocked in b and blocked not in a
    assert allowed in a and allowed not in b
    assert await repos.blocklist.delete(blocked) and await repos.blocklist.delete(allowed)


async def test_erasure_deletes_the_players_scores_in_the_same_transaction(repos: Repos, pool: Pool):
    pseudonym, other = os.urandom(32), os.urandom(32)
    async with pool.connection() as conn:
        for p in (pseudonym, other):
            await conn.execute(
                "INSERT INTO corvus.scores_latest (pseudonym, game_context, domain, model_name,"
                " model_version, stage, score, smoothed, windows, session_id, segment, updated_at)"
                " VALUES (%s, 'it', 'move', 'm', '1', 'shadow', 0.5, 0.5, 1, %s, 0, %s)",
                (p, os.urandom(16), NOW),
            )
            await conn.execute(
                "INSERT INTO corvus.score_history (pseudonym, session_id, segment, game_context,"
                " domain, model_name, model_version, stage, score, smoothed, windows,"
                " window_start_offset_nanos, window_end_offset_nanos, scored_at)"
                " VALUES (%s, %s, 0, 'it', 'move', 'm', '1', 'shadow', 0.5, 0.5, 1, 0, 1, %s)",
                (p, os.urandom(16), NOW),
            )

    await erase_player(repos.erasure, pseudonym, requested_by="it", now=NOW)

    assert await repos.scores.latest(pseudonym, include_shadow=True) == []
    assert await repos.scores.history(pseudonym, None, 10, include_shadow=True) == []
    assert len(await repos.scores.latest(other, include_shadow=True)) == 1
    assert len(await repos.scores.history(other, None, 10, include_shadow=True)) == 1


async def test_shadow_scores_are_returned_only_on_request(repos: Repos, pool: Pool):
    p = os.urandom(32)
    async with pool.connection() as conn:
        for stage, model in (("shadow", "a"), ("enforce", "b")):
            await conn.execute(
                "INSERT INTO corvus.scores_latest (pseudonym, game_context, domain, model_name,"
                " model_version, stage, score, smoothed, windows, session_id, segment, updated_at)"
                " VALUES (%s, 'it', 'move', %s, '1', %s, 0.5, 0.5, 1, %s, 0, %s)",
                (p, model, stage, os.urandom(16), NOW),
            )
            await conn.execute(
                "INSERT INTO corvus.score_history (pseudonym, session_id, segment, game_context,"
                " domain, model_name, model_version, stage, score, smoothed, windows,"
                " window_start_offset_nanos, window_end_offset_nanos, scored_at)"
                " VALUES (%s, %s, 0, 'it', 'move', %s, '1', %s, 0.5, 0.5, 1, 0, 1, %s)",
                (p, os.urandom(16), model, stage, NOW),
            )

    assert [r.stage for r in await repos.scores.latest(p)] == ["enforce"]
    assert [r.stage for r in await repos.scores.history(p, None, 1)] == ["enforce"]
    assert len(await repos.scores.latest(p, include_shadow=True)) == 2
    assert len(await repos.scores.history(p, None, 10, include_shadow=True)) == 2


async def test_erasure_requests_are_listed_newest_first_and_filtered(repos: Repos):
    req = await erase_player(repos.erasure, os.urandom(32), requested_by="it", now=NOW)

    page = await repos.erasure.list_requests(PageRequest(500, None), "completed")

    assert req.id in [r.id for r in page.items]
    times = [r.requested_at for r in page.items]
    assert times == sorted(times, reverse=True)


async def test_summary_counts_pending_erasures_and_lists_recent_activity(repos: Repos):
    before = await repos.summary.summary(10)
    await erase_player(repos.erasure, os.urandom(32), requested_by="it", now=NOW)

    after = await repos.summary.summary(10)

    assert after.pending_erasures == before.pending_erasures + 1
    assert len(after.recent_erasures) <= 10
    assert all(isinstance(n, int) for n in after.jobs_by_status.values())


async def _snapshot(repos: Repos) -> SnapshotRecord:
    pool_id = uuid4()
    manifest = build_manifest(
        pool_id=pool_id, pool_name="it", pool_filter={}, session_ids=[], files=[], created_at=NOW
    )
    snap = SnapshotRecord(
        uuid4(), pool_id, "it", manifest, manifest_sha256(manifest), 0, 0, "it", NOW
    )
    await repos.snapshots.insert(snap)
    return snap


async def test_job_results_are_stored_and_kept_by_later_updates(repos: Repos):
    from corvus_services.api.ports import JobRecord

    snap = await _snapshot(repos)
    job = JobRecord(uuid4(), "train", snap.id, {}, "running", "local", None, "", "it", NOW, NOW)
    await repos.jobs.create(job)

    done = await repos.jobs.update(
        job.id, status="succeeded", runner_ref=None, message="", now=NOW, finished=True,
        result={"model": "m:1"},
    )  # fmt: skip
    again = await repos.jobs.update(
        job.id, status="succeeded", runner_ref=None, message="ok", now=NOW, finished=True
    )

    assert done.result == {"model": "m:1"}
    assert again.result == {"model": "m:1"}


async def test_list_active_returns_only_non_final_jobs(repos: Repos):
    from corvus_services.api.ports import JobRecord

    snap = await _snapshot(repos)
    ids = {}
    for status in ("queued", "running", "succeeded", "failed"):
        job = JobRecord(uuid4(), "train", snap.id, {}, status, "local", None, "", "it", NOW, NOW)
        await repos.jobs.create(job)
        ids[status] = job.id

    active = {j.id for j in await repos.jobs.list_active()}

    assert {ids["queued"], ids["running"]} <= active
    assert not ({ids["succeeded"], ids["failed"]} & active)


async def test_models_filter_by_name_and_version(repos: Repos):
    from corvus_services.api.ports import ModelFilter

    snap = await _snapshot(repos)
    name = f"it-{uuid4().hex[:8]}"
    model = ModelRecord(
        uuid4(), name, "1", "gbdt", "it", "move", snap.id, {}, "k.onnx", "registered", "it", NOW
    )
    await repos.models.create(model)

    found = await repos.models.get_by_name_version(name, "1")
    hit = await repos.models.list_page(PageRequest(10, None), ModelFilter(name=name, domain="move"))
    miss = await repos.models.list_page(PageRequest(10, None), ModelFilter(name=name, domain="aim"))

    assert found == model
    assert [m.id for m in hit.items] == [model.id]
    assert miss.items == []
