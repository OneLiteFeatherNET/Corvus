"""Registration of derived artefacts (raw-derived features) and the raw files of a snapshot."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import pytest
from api_fakes import FIXED_NOW
from api_support import Env, make_verifier

from corvus_services.api.auth import JOB_ID_HEADER, Authenticator, token_digest
from corvus_services.api.ports import JobRecord, SnapshotRecord
from corvus_services.api.routes.artifacts import check_prefix
from corvus_services.api.snapshots import LakeFileRef, build_manifest, manifest_sha256

SNAP = UUID(int=42)
OTHER_SNAP = UUID(int=43)
JOB = UUID(int=7)
TOKEN = "job-secret-token"
SESSIONS = [bytes([i]) * 16 for i in range(1, 4)]
PREFIX = f"features/{SNAP}/raw/"
BODY = {"kind": "raw_features", "s3_prefix": PREFIX, "contains_raw": True}


def _snapshot(snap_id: UUID) -> SnapshotRecord:
    manifest = build_manifest(
        pool_id=UUID(int=1),
        pool_name="p",
        pool_filter={},
        session_ids=SESSIONS,
        files=[LakeFileRef(1, "lake/events/a")],
        created_at=FIXED_NOW,
    )
    return SnapshotRecord(
        snap_id, UUID(int=1), "p", manifest, manifest_sha256(manifest), 3, 1, "dana", FIXED_NOW
    )


def _job(job_id: UUID, snap_id: UUID, started=FIXED_NOW) -> JobRecord:
    return JobRecord(
        job_id,
        "build_features",
        snap_id,
        {},
        "running",
        "fake",
        "ref",
        "",
        "dana",
        started,
        started,
    )


@pytest.fixture
def ds(env: Env, token):
    for s in (SNAP, OTHER_SNAP):
        env.parts["snapshots"].rows[s] = _snapshot(s)
    env.parts["jobs"].rows[JOB] = _job(JOB, SNAP)
    return env.headers(token(["data_scientist"]))


@pytest.fixture
def job_headers(env: Env, ds, jwks_source, clock) -> dict[str, str]:
    env.deps.authenticator = Authenticator(
        make_verifier(jwks_source, clock), job_tokens=[(token_digest(TOKEN), "build")]
    )
    return {"Authorization": f"Bearer {TOKEN}", JOB_ID_HEADER: str(JOB)}


def _post(env: Env, headers, body=None, snap=SNAP):
    return env.client.post(
        f"/api/v1/snapshots/{snap}/artifacts", json=BODY if body is None else body, headers=headers
    )


# --- prefix rules --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prefix",
    [
        f"features/{SNAP}/raw/",
        f"features/{SNAP}/",
        f"tokenizers/{SNAP}/{JOB}/",
    ],
)
def test_prefixes_below_the_snapshot_are_accepted(prefix: str):
    assert check_prefix(prefix, SNAP) is None


@pytest.mark.parametrize(
    "prefix",
    [
        f"features/{SNAP}/raw",  # no trailing slash
        f"features/{OTHER_SNAP}/raw/",  # another snapshot
        "lake/raw/",  # outside the derived roots
        "features/",  # whole root
        f"features/{SNAP}/../{OTHER_SNAP}/raw/",  # traversal
        f"features/{SNAP}//raw/",  # empty segment
        f"features/{SNAP}/raw file/",  # character outside the allowed set
    ],
)
def test_prefixes_that_could_reach_other_data_are_refused(prefix: str):
    assert check_prefix(prefix, SNAP) is not None


# --- registration --------------------------------------------------------------------------------


def test_a_data_scientist_registers_an_artifact(env: Env, ds):
    resp = _post(env, ds)

    assert resp.status_code == 201
    body = resp.json()
    assert (body["snapshot_id"], body["kind"], body["s3_prefix"], body["contains_raw"]) == (
        str(SNAP),
        "raw_features",
        PREFIX,
        True,
    )
    assert body["job_id"] is None


def test_registering_the_same_prefix_again_is_idempotent(env: Env, ds):
    first = _post(env, ds).json()

    again = _post(env, ds)

    assert again.status_code == 200
    assert again.json()["id"] == first["id"]
    assert len(env.parts["snapshots"].artifacts) == 1


def test_a_job_token_registers_with_its_job_header(env: Env, job_headers):
    resp = _post(env, job_headers)

    assert resp.status_code == 201
    assert resp.json()["job_id"] == str(JOB)


def test_a_job_token_without_the_job_header_is_refused(env: Env, job_headers):
    headers = {"Authorization": job_headers["Authorization"]}

    assert _post(env, headers).status_code == 403


def test_a_job_token_cannot_register_for_a_job_of_another_snapshot(env: Env, job_headers):
    assert (
        _post(
            env, job_headers, {**BODY, "s3_prefix": f"features/{OTHER_SNAP}/raw/"}, OTHER_SNAP
        ).status_code
        == 403
    )


def test_registration_by_job_id_uses_the_snapshot_of_the_job(env: Env, job_headers):
    resp = env.client.post(f"/api/v1/jobs/{JOB}/artifacts", json=BODY, headers=job_headers)

    assert resp.status_code == 201
    assert resp.json()["snapshot_id"] == str(SNAP)
    assert resp.json()["job_id"] == str(JOB)


def test_a_job_token_may_register_only_for_its_own_job_id(env: Env, job_headers):
    other = UUID(int=8)
    env.parts["jobs"].rows[other] = _job(other, SNAP)

    resp = env.client.post(f"/api/v1/jobs/{other}/artifacts", json=BODY, headers=job_headers)

    assert resp.status_code == 403


def test_a_body_job_id_from_another_snapshot_is_invalid(env: Env, ds):
    other = UUID(int=8)
    env.parts["jobs"].rows[other] = _job(other, OTHER_SNAP)

    assert _post(env, ds, {**BODY, "job_id": str(other)}).status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {**BODY, "kind": "Bad Kind"},
        {**BODY, "s3_prefix": "lake/raw/"},
        {**BODY, "s3_prefix": f"features/{SNAP}/raw"},
        {"kind": "raw_features", "s3_prefix": PREFIX},  # contains_raw is required
        {**BODY, "extra": 1},
    ],
)
def test_invalid_bodies_are_refused(env: Env, ds, body):
    assert _post(env, ds, body).status_code == 422
    assert env.parts["snapshots"].artifacts == {}


def test_unknown_snapshot_is_404(env: Env, ds):
    unknown = UUID(int=999)

    assert (
        _post(env, ds, {**BODY, "s3_prefix": f"features/{unknown}/raw/"}, unknown).status_code
        == 404
    )


# --- erasure marks the snapshot, a later build clears it -----------------------------------------


def test_a_build_started_before_the_erasure_is_refused_after_it(env: Env, ds):
    repo = env.parts["snapshots"]
    repo.rebuild[SNAP] = (True, FIXED_NOW + timedelta(minutes=5))

    resp = _post(env, {**ds}, {**BODY, "job_id": str(JOB)})

    assert resp.status_code == 409
    assert repo.artifacts == {}


def test_a_build_started_after_the_erasure_registers_and_clears_the_rebuild_flag(env: Env, ds):
    repo = env.parts["snapshots"]
    repo.rebuild[SNAP] = (True, FIXED_NOW - timedelta(minutes=5))

    resp = _post(env, ds, {**BODY, "job_id": str(JOB)})

    assert resp.status_code == 201
    assert repo.rebuild[SNAP][0] is False


def test_an_artifact_without_raw_content_registers_while_a_rebuild_is_pending(env: Env, ds):
    env.parts["snapshots"].rebuild[SNAP] = (True, FIXED_NOW)
    body = {
        "kind": "aggregate_features",
        "s3_prefix": f"features/{SNAP}/windows/",
        "contains_raw": False,
    }

    assert _post(env, ds, body).status_code == 201
    assert env.parts["snapshots"].rebuild[SNAP][0] is True


def test_the_snapshot_reports_needs_rebuild(env: Env, ds):
    env.parts["snapshots"].rebuild[SNAP] = (True, FIXED_NOW)

    resp = env.client.get(f"/api/v1/snapshots/{SNAP}", headers=ds)

    assert resp.json()["needs_rebuild"] is True


def test_artifacts_of_a_snapshot_are_listed(env: Env, ds):
    _post(env, ds)

    items = env.client.get(f"/api/v1/snapshots/{SNAP}/artifacts", headers=ds).json()["items"]

    assert [i["s3_prefix"] for i in items] == [PREFIX]


# --- files and sessions for the jobs -------------------------------------------------------------


def test_the_files_endpoint_lists_live_raw_files_of_the_snapshot_sessions(env: Env, ds):
    repo = env.parts["snapshots"]
    repo.lake_files = {1: ("lake/events/a", None, False)}
    repo.raw_files = {
        10: ("lake/raw/date=2026-10-14/a", {SESSIONS[0]}, False),
        11: ("lake/raw/date=2026-10-14/gone", {SESSIONS[1]}, True),
        12: ("lake/raw/date=2026-10-14/other", {b"\x09" * 16}, False),
    }

    body = env.client.get(f"/api/v1/snapshots/{SNAP}/files", headers=ds).json()

    assert body["raw"] == [{"manifest_file_id": 10, "id": 10, "key": "lake/raw/date=2026-10-14/a"}]
    assert [f["id"] for f in body["items"]] == [1]
