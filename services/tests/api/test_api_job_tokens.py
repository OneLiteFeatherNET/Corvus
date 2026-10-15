"""Machine tokens of ML jobs: configuration, authentication, endpoint restrictions, job reports."""

from __future__ import annotations

import hmac
from datetime import timedelta
from uuid import UUID

import pytest
from api_fakes import FIXED_NOW
from api_support import Env, make_verifier

from corvus_services.api.auth import JOB_ID_HEADER, Authenticator, AuthError, token_digest
from corvus_services.api.jobs import JobState, RunnerStatus, child_environment
from corvus_services.api.ports import JobRecord, SnapshotRecord
from corvus_services.api.settings import ApiSettings, parse_job_tokens

TOKEN = "job-secret-token-1"
OTHER_TOKEN = "job-secret-token-2"
JOB_ID = UUID(int=41)
OTHER_JOB_ID = UUID(int=42)
UNKNOWN_ID = UUID(int=43)
URL = f"/api/v1/jobs/{JOB_ID}"

# --- configuration --------------------------------------------------------------------------------


def test_job_tokens_parse_into_hash_and_name_pairs():
    digest = token_digest(TOKEN)

    assert parse_job_tokens(f"{digest}:train-1, {token_digest('x').upper()}:eval.2,") == [
        (digest, "train-1"),
        (token_digest("x"), "eval.2"),
    ]


def test_no_job_tokens_is_the_default():
    assert ApiSettings(pg_dsn="postgresql://unused").job_token_list() == []


@pytest.mark.parametrize(
    "bad",
    [
        "plain-token:name",
        f"{token_digest(TOKEN)}",
        f"{token_digest(TOKEN)}:",
        f"{token_digest(TOKEN)}:bad name",
        f"{token_digest(TOKEN)}:-leading",
        f"{token_digest(TOKEN)[:-1]}:short",
    ],
)
def test_malformed_job_tokens_are_refused_without_echoing_the_value(bad):
    with pytest.raises(ValueError, match="CORVUS_API_JOB_TOKENS entry 1") as caught:
        ApiSettings(pg_dsn="postgresql://unused", job_tokens=bad)

    assert "plain-token" not in str(caught.value)


# --- authenticator --------------------------------------------------------------------------------


async def test_a_job_token_authenticates_as_a_data_scientist_named_after_the_job():
    auth = Authenticator(None, job_tokens=[(token_digest(TOKEN), "train-1")])

    principal = await auth.authenticate(TOKEN)

    assert principal.subject == "job:train-1"
    assert {r.value for r in principal.roles} == {"data_scientist"}
    assert principal.is_job


async def test_an_unknown_token_is_rejected():
    auth = Authenticator(None, job_tokens=[(token_digest(TOKEN), "train-1")])

    with pytest.raises(AuthError):
        await auth.authenticate(OTHER_TOKEN)


async def test_the_configured_hash_is_not_itself_a_token():
    digest = token_digest(TOKEN)
    auth = Authenticator(None, job_tokens=[(digest, "train-1")])

    with pytest.raises(AuthError):
        await auth.authenticate(digest)


async def test_every_configured_hash_is_compared_without_early_exit(monkeypatch):
    compared: list[bytes] = []
    real = hmac.compare_digest

    def spy(a, b):
        compared.append(b)
        return real(a, b)

    monkeypatch.setattr("corvus_services.api.auth.hmac.compare_digest", spy)
    entries = [(token_digest(f"t{i}"), f"job{i}") for i in range(3)]
    auth = Authenticator(None, job_tokens=entries)

    principal = await auth.authenticate("t0")

    assert principal.subject == "job:job0"
    assert compared == [d.encode() for d, _ in entries]


def test_job_tokens_alone_count_as_configured_authentication():
    assert Authenticator(None, job_tokens=[(token_digest(TOKEN), "j")]).configured


# --- HTTP: restrictions ---------------------------------------------------------------------------


def bearer(token: str = TOKEN, **extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", **extra}


def scoped(job_id: UUID = JOB_ID, token: str = TOKEN) -> dict[str, str]:
    return bearer(token, **{JOB_ID_HEADER: str(job_id)})


@pytest.fixture
def job_env(env: Env, jwks_source, clock) -> Env:
    env.deps.authenticator = Authenticator(
        make_verifier(jwks_source, clock),
        job_tokens=[(token_digest(TOKEN), "train-1"), (token_digest(OTHER_TOKEN), "train-2")],
    )
    return env


@pytest.fixture
def snapshot_id(job_env: Env) -> UUID:
    snap = SnapshotRecord(
        UUID(int=9), UUID(int=8), "p", {"sessions": []}, "0" * 64, 0, 0, "dana", FIXED_NOW
    )
    job_env.parts["snapshots"].rows[snap.id] = snap
    return snap.id


@pytest.fixture
def jobs(job_env: Env, snapshot_id: UUID) -> Env:
    for jid in (JOB_ID, OTHER_JOB_ID):
        job_env.parts["jobs"].rows[jid] = JobRecord(
            jid,
            "train",
            snapshot_id,
            {},
            "running",
            "fake",
            "ref",
            "",
            "dana",
            FIXED_NOW,
            FIXED_NOW,
        )
    return job_env


MODEL = {
    "name": "gbdt",
    "version": "1",
    "candidate_type": "gbdt",
    "game_context": "bedwars",
    "domain": "movement",
    "metrics": {"p99_latency_ms": 3},
    "onnx_key": "models/gbdt/1.onnx",
}


def test_a_job_token_registers_a_model_as_the_job(job_env: Env, snapshot_id):
    resp = job_env.client.post(
        "/api/v1/models", json={**MODEL, "snapshot_id": str(snapshot_id)}, headers=bearer()
    )

    assert resp.status_code == 201
    assert resp.json()["created_by"] == "job:train-1"
    assert resp.json()["stage"] == "registered"


def test_a_job_token_reads_snapshots_and_models(job_env: Env, snapshot_id):
    for path in (
        "/api/v1/snapshots",
        f"/api/v1/snapshots/{snapshot_id}",
        "/api/v1/models",
        "/api/v1/models/gbdt/1",
    ):
        assert job_env.client.get(path, headers=bearer()).status_code in (200, 404), path
    assert (
        job_env.client.get(f"/api/v1/snapshots/{snapshot_id}", headers=bearer()).status_code == 200
    )


def test_a_job_token_cannot_promote_a_model(job_env: Env, snapshot_id):
    model = job_env.client.post(
        "/api/v1/models", json={**MODEL, "snapshot_id": str(snapshot_id)}, headers=bearer()
    ).json()

    resp = job_env.client.post(
        f"/api/v1/models/{model['id']}/stage", json={"stage": "shadow"}, headers=bearer()
    )

    assert resp.status_code == 403


NOT_FOR_JOBS = [
    ("POST", "/api/v1/jobs"),
    ("GET", "/api/v1/jobs"),
    ("GET", "/api/v1/pools"),
    ("POST", "/api/v1/pools"),
    ("POST", "/api/v1/pools/preview"),
    ("GET", f"/api/v1/pools/{UUID(int=1)}/snapshots"),
    ("POST", f"/api/v1/pools/{UUID(int=1)}/snapshots"),
    ("GET", "/api/v1/labels"),
    ("POST", "/api/v1/labels"),
    ("POST", "/api/v1/labels/import"),
    ("POST", "/api/v1/players/lookup"),
    ("POST", "/api/v1/players/erasure"),
    ("GET", "/api/v1/erasure-requests"),
    ("GET", "/api/v1/blocklist"),
    ("PUT", f"/api/v1/blocklist/{'ab' * 32}"),
    ("GET", f"/api/v1/scores/players/{'ab' * 32}"),
    ("GET", "/api/v1/summary"),
    ("GET", "/api/v1/me"),
]


@pytest.mark.parametrize(("method", "path"), NOT_FOR_JOBS)
def test_job_tokens_are_refused_outside_models_snapshots_and_jobs(job_env: Env, method, path):
    resp = job_env.client.request(method, path, headers=scoped(), json={})

    assert resp.status_code == 403, f"{method} {path}"
    assert resp.json()["status"] == 403


def test_the_blocklist_feed_does_not_accept_job_tokens(job_env: Env):
    resp = job_env.client.get("/api/v1/plugin/blocklist", headers=bearer())

    assert resp.status_code == 401


def test_a_wrong_job_token_is_unauthorized(job_env: Env):
    assert job_env.client.get("/api/v1/models", headers=bearer("nope")).status_code == 401


# --- HTTP: job reports ----------------------------------------------------------------------------


def _patch(env: Env, headers, body=None, job_id: UUID = JOB_ID):
    return env.client.patch(
        f"/api/v1/jobs/{job_id}",
        json={"status": "running"} if body is None else body,
        headers=headers,
    )


def test_a_job_reports_progress_and_result_for_its_own_id(jobs: Env):
    jobs.clock.advance(60)

    run = _patch(jobs, scoped(), {"status": "running", "message": "epoch 3"})
    done = _patch(jobs, scoped(), {"status": "succeeded", "result": {"model": "gbdt:1"}})

    assert run.status_code == 200
    assert run.json()["message"] == "epoch 3"
    assert done.json()["status"] == "succeeded"
    assert done.json()["result"] == {"model": "gbdt:1"}
    assert done.json()["finished_at"] is not None


def test_a_job_token_without_the_job_id_header_is_forbidden(jobs: Env):
    resp = _patch(jobs, bearer())

    assert resp.status_code == 403
    assert JOB_ID_HEADER in resp.json()["detail"]


def test_a_job_token_with_another_jobs_id_header_is_forbidden(jobs: Env):
    resp = _patch(jobs, scoped(OTHER_JOB_ID))

    assert resp.status_code == 403
    assert jobs.parts["jobs"].rows[JOB_ID].status == "running"


def test_the_header_must_match_the_path_not_just_exist(jobs: Env):
    resp = _patch(jobs, scoped(JOB_ID), job_id=OTHER_JOB_ID)

    assert resp.status_code == 403
    assert jobs.parts["jobs"].rows[OTHER_JOB_ID].status == "running"


def test_a_malformed_job_id_header_is_forbidden(jobs: Env):
    assert _patch(jobs, bearer(**{JOB_ID_HEADER: "not-a-uuid"})).status_code == 403


def test_a_job_may_read_only_its_own_job(jobs: Env):
    assert jobs.client.get(URL, headers=scoped()).status_code == 200
    assert jobs.client.get(URL, headers=bearer()).status_code == 403
    assert jobs.client.get(URL, headers=scoped(OTHER_JOB_ID)).status_code == 403


def test_reporting_on_a_missing_job_is_404(jobs: Env):
    resp = _patch(jobs, scoped(UNKNOWN_ID), job_id=UNKNOWN_ID)

    assert resp.status_code == 404


def test_a_finished_job_cannot_change_its_status(jobs: Env):
    _patch(jobs, scoped(), {"status": "failed", "message": "oom"})

    resp = _patch(jobs, scoped(), {"status": "succeeded"})

    assert resp.status_code == 409
    assert jobs.parts["jobs"].rows[JOB_ID].status == "failed"


def test_repeating_a_final_report_is_accepted(jobs: Env):
    first = _patch(jobs, scoped(), {"status": "succeeded", "result": {"a": 1}})

    again = _patch(jobs, scoped(), {"status": "succeeded", "result": {"a": 1}})

    assert again.status_code == 200
    assert again.json() == first.json()


@pytest.mark.parametrize(
    "body",
    [
        {"status": "queued"},
        {"status": "bogus"},
        {},
        {"status": "running", "message": "x" * 2001},
        {"status": "running", "result": {"blob": "x" * 17_000}},
        {"status": "running", "surprise": 1},
    ],
)
def test_invalid_reports_are_validation_errors(jobs: Env, body):
    assert _patch(jobs, scoped(), body).status_code == 422


def test_people_with_the_data_scientist_role_may_report_without_the_header(jobs: Env, token):
    resp = _patch(jobs, jobs.headers(token(["data_scientist"])), {"status": "failed"})

    assert resp.status_code == 200


def test_viewers_may_not_report(jobs: Env, token):
    assert _patch(jobs, jobs.headers(token(["viewer"]))).status_code == 403


def test_a_runner_refresh_keeps_the_message_the_job_reported(jobs: Env):
    _patch(jobs, scoped(), {"status": "running", "message": "epoch 3"})
    jobs.clock.advance(timedelta(minutes=1).total_seconds())
    jobs.runner.states["ref"] = RunnerStatus(JobState.RUNNING)

    got = jobs.client.get(URL, headers=scoped())

    assert got.json()["message"] == "epoch 3"


# --- the job's view of the environment -----------------------------------------------------------


def test_the_job_environment_keeps_its_api_coordinates_but_not_the_apis_secrets():
    parent = {
        "CORVUS_API_URL": "http://api:8080",
        "CORVUS_API_TOKEN": "job-token",
        "CORVUS_API_JOB_TOKENS": "hash:name",
        "CORVUS_API_DEV_TOKEN": "dev",
        "CORVUS_PSEUDONYM_KEY": "k",
        "PATH": "/bin",
    }

    env = child_environment(parent, {"CORVUS_JOB_ID": "1"})

    assert env == {
        "CORVUS_API_URL": "http://api:8080",
        "CORVUS_API_TOKEN": "job-token",
        "PATH": "/bin",
        "CORVUS_JOB_ID": "1",
    }
