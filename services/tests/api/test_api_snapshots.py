from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

import pytest
from api_support import Env

from corvus_services.api.pools import PoolFilter
from corvus_services.api.ports import SnapshotMaterial
from corvus_services.api.snapshots import (
    LakeFileRef,
    build_manifest,
    canonical_json,
    manifest_sha256,
)

POOL = UUID(int=7)
AT = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
S1, S2, S3 = (bytes([i]) * 16 for i in (1, 2, 3))
F1, F2 = (
    LakeFileRef(10, "lake/events/date=2026-10-14/a.parquet"),
    LakeFileRef(11, "lake/events/date=2026-10-15/b.parquet"),
)


def _manifest(sessions=(S1, S2), files=(F1, F2), **kw):
    args = {
        "pool_id": POOL,
        "pool_name": "p",
        "pool_filter": PoolFilter().model_dump(mode="json"),
        "created_at": AT,
    }
    args.update(kw)
    return build_manifest(session_ids=sessions, files=files, **args)


def test_manifest_hash_is_deterministic():
    assert manifest_sha256(_manifest()) == manifest_sha256(_manifest())


def test_manifest_hash_ignores_input_order_and_duplicates():
    a = _manifest(sessions=[S1, S2], files=[F1, F2])
    b = _manifest(sessions=[S2, S1, S2], files=[F2, F1, F1])

    assert a == b
    assert manifest_sha256(a) == manifest_sha256(b)


@pytest.mark.parametrize(
    "change",
    [
        {"sessions": (S1, S2, S3)},
        {"files": (F1,)},
        {"files": (F1, LakeFileRef(11, "lake/events/other.parquet"))},
        {"pool_name": "other"},
        {"created_at": datetime(2026, 10, 15, 12, 0, 1, tzinfo=UTC)},
        {"pool_filter": PoolFilter(retention_days=5).model_dump(mode="json")},
    ],
)
def test_any_change_of_the_content_changes_the_hash(change):
    assert manifest_sha256(_manifest(**change)) != manifest_sha256(_manifest())


def test_canonical_form_is_sorted_compact_ascii_json():
    m = _manifest(sessions=[S1], files=[F1], pool_name="Zoë")

    raw = canonical_json(m)

    assert raw == json.dumps(m, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    assert b" " not in raw.replace(b"events/date=", b"")  # no padding whitespace outside values
    assert raw.isascii()
    assert manifest_sha256(m) == hashlib.sha256(raw).hexdigest()


def test_manifest_hash_is_pinned():
    # Guards the canonical format: changing it would silently invalidate every stored hash.
    m = _manifest(sessions=[S1], files=[F1], pool_filter={"game_contexts": ["a"]})

    assert canonical_json(m) == (
        b'{"created_at":"2026-10-15T12:00:00+00:00","files":{"events":[{"id":10,'
        b'"key":"lake/events/date=2026-10-14/a.parquet"}]},"filter":{"game_contexts":["a"]},'
        b'"pool":{"id":"00000000-0000-0000-0000-000000000007","name":"p"},'
        b'"sessions":["01010101010101010101010101010101"],"version":1}'
    )
    assert manifest_sha256(m) == (
        "e29a0fda259df012b286f64cddad27af2cb0beeefe2b96ff186ed1364a7d1908"
    )


# --- HTTP ---------------------------------------------------------------------------------------


@pytest.fixture
def ds(env: Env, token):
    return env.headers(token(["data_scientist"], sub="dana"))


@pytest.fixture
def pool_id(env: Env, ds) -> str:
    resp = env.client.post("/api/v1/pools", json={"name": "p"}, headers=ds)
    return resp.json()["id"]


def test_snapshot_materialises_the_selected_sessions_and_files(env: Env, ds, pool_id):
    env.parts["catalog"].material = SnapshotMaterial([S2, S1], [F2, F1])

    resp = env.client.post(f"/api/v1/pools/{pool_id}/snapshots", headers=ds)

    assert resp.status_code == 201
    body = resp.json()
    assert (body["session_count"], body["file_count"], body["created_by"]) == (2, 2, "dana")
    assert body["manifest"] is None  # the list/create view omits the manifest
    detail = env.client.get(f"/api/v1/snapshots/{body['id']}", headers=ds).json()
    manifest = detail["manifest"]
    assert manifest["sessions"] == [S1.hex(), S2.hex()]
    assert manifest["files"]["events"] == [
        {"id": 10, "key": F1.s3_key},
        {"id": 11, "key": F2.s3_key},
    ]
    assert manifest["filter"]["exclude_orphans"] is True
    assert manifest["created_at"] == AT.isoformat()
    assert detail["sha256"] == manifest_sha256(manifest)


def test_stored_hash_verifies_against_the_stored_manifest(env: Env, ds, pool_id):
    env.parts["catalog"].material = SnapshotMaterial([S1], [F1])
    sid = env.client.post(f"/api/v1/pools/{pool_id}/snapshots", headers=ds).json()["id"]

    stored = env.parts["snapshots"].rows[UUID(sid)]

    assert manifest_sha256(stored.manifest) == stored.sha256


def test_snapshot_survives_changes_to_the_pool_and_the_catalog(env: Env, ds, pool_id):
    env.parts["catalog"].material = SnapshotMaterial([S1], [F1])
    sid = env.client.post(f"/api/v1/pools/{pool_id}/snapshots", headers=ds).json()["id"]
    before = env.client.get(f"/api/v1/snapshots/{sid}", headers=ds).json()

    env.client.put(
        f"/api/v1/pools/{pool_id}", json={"name": "p", "filter": {"retention_days": 1}}, headers=ds
    )
    env.parts["catalog"].material = SnapshotMaterial([S1, S2, S3], [F1, F2])
    env.client.post(f"/api/v1/pools/{pool_id}/snapshots", headers=ds)
    env.client.delete(f"/api/v1/pools/{pool_id}", headers=ds)

    assert env.client.get(f"/api/v1/snapshots/{sid}", headers=ds).json() == before


def test_snapshots_have_no_mutating_endpoints(env: Env, ds, pool_id):
    env.parts["catalog"].material = SnapshotMaterial([S1], [F1])
    sid = env.client.post(f"/api/v1/pools/{pool_id}/snapshots", headers=ds).json()["id"]

    for method in ("PUT", "PATCH", "DELETE"):
        resp = env.client.request(method, f"/api/v1/snapshots/{sid}", headers=ds)
        assert resp.status_code == 405, method


def test_repeated_snapshots_get_distinct_ids(env: Env, ds, pool_id):
    env.parts["catalog"].material = SnapshotMaterial([S1], [F1])

    a = env.client.post(f"/api/v1/pools/{pool_id}/snapshots", headers=ds).json()
    b = env.client.post(f"/api/v1/pools/{pool_id}/snapshots", headers=ds).json()

    assert a["id"] != b["id"]
    assert a["sha256"] == b["sha256"]  # same inputs and same clock


def test_snapshots_are_listed_per_pool_and_globally(env: Env, ds, pool_id):
    other = env.client.post("/api/v1/pools", json={"name": "q"}, headers=ds).json()["id"]
    for pid in (pool_id, pool_id, other):
        env.clock.advance(1)
        env.client.post(f"/api/v1/pools/{pid}/snapshots", headers=ds)

    per_pool = env.client.get(f"/api/v1/pools/{pool_id}/snapshots?limit=5", headers=ds).json()
    everything = env.client.get("/api/v1/snapshots?limit=5", headers=ds).json()

    assert len(per_pool["items"]) == 2
    assert len(everything["items"]) == 3
    assert all(s["pool_id"] == pool_id for s in per_pool["items"])


def test_unknown_snapshot_is_404(env: Env, ds):
    assert env.client.get(f"/api/v1/snapshots/{UUID(int=99)}", headers=ds).status_code == 404
