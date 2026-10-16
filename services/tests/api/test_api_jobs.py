from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from api_fakes import FIXED_NOW, FakeJobRepo, FakeRunner, FakeSnapshotRepo
from api_support import Env

from corvus_services.api.jobs import (
    JobSpec,
    JobState,
    JobType,
    K8sJobConfig,
    KubernetesRunner,
    LocalRunner,
    RunnerStatus,
    SnapshotMissingError,
    child_environment,
    create_job,
    refresh_job,
)
from corvus_services.api.ports import SnapshotRecord

JOB_ID = UUID("1a2b3c4d-0000-0000-0000-000000000001")
SNAPSHOT = UUID(int=9)
SPEC = JobSpec(JOB_ID, JobType.TRAIN, SNAPSHOT, {"epochs": 3, "lr": 0.1})


# --- local runner ---------------------------------------------------------------------------------


class FakeProcess:
    def __init__(self, code: int | None = None) -> None:
        self.code = code

    def poll(self) -> int | None:
        return self.code


class FakeLauncher:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, str]]] = []
        self.process = FakeProcess()

    def launch(self, argv, env):
        self.calls.append((list(argv), dict(env)))
        return self.process


async def test_local_runner_starts_the_configured_command_with_the_job_contract():
    launcher = FakeLauncher()
    runner = LocalRunner(["corvus-job", "--quiet"], launcher)

    ref = await runner.submit(SPEC)

    argv, env = launcher.calls[0]
    assert argv == ["corvus-job", "--quiet", "train"]
    assert env == {
        "CORVUS_JOB_ID": str(JOB_ID),
        "CORVUS_JOB_TYPE": "train",
        "CORVUS_JOB_SNAPSHOT_ID": str(SNAPSHOT),
        "CORVUS_JOB_PARAMS": '{"epochs":3,"lr":0.1}',
    }
    assert ref == str(JOB_ID)


async def test_local_runner_reports_running_then_exit_status():
    launcher = FakeLauncher()
    runner = LocalRunner(["x"], launcher)
    ref = await runner.submit(SPEC)

    assert (await runner.status(ref)).state == JobState.RUNNING
    launcher.process.code = 0
    assert (await runner.status(ref)).state == JobState.SUCCEEDED
    launcher.process.code = 3
    failed = await runner.status(ref)
    assert (failed.state, failed.message) == (JobState.FAILED, "exit code 3")


async def test_local_runner_fails_jobs_whose_process_handle_was_lost():
    runner = LocalRunner(["x"], FakeLauncher())

    status = await runner.status("unknown-ref")

    assert status.state == JobState.FAILED
    assert "lost" in status.message


def test_local_runner_needs_a_command():
    with pytest.raises(ValueError, match="command"):
        LocalRunner([], FakeLauncher())


def test_job_environment_does_not_leak_the_apis_secrets():
    parent = {
        "CORVUS_API_DEV_TOKEN": "leak",
        "CORVUS_API_PLUGIN_TOKENS": "leak",
        "CORVUS_PSEUDONYM_KEY": "leak",
        "CORVUS_PSEUDONYM_KEY_ID": "leak",
        "CORVUS_KEK": "leak",
        "CORVUS_PG_DSN": "postgresql://keep",
        "PATH": "/usr/bin",
    }

    env = child_environment(parent, {"CORVUS_JOB_ID": "1"})

    assert env == {"CORVUS_PG_DSN": "postgresql://keep", "PATH": "/usr/bin", "CORVUS_JOB_ID": "1"}


# --- kubernetes runner ----------------------------------------------------------------------


class FakeBatchApi:
    def __init__(self) -> None:
        self.created: list[tuple[str, Any]] = []
        self.status_obj: Any = SimpleNamespace(
            status=SimpleNamespace(active=None, succeeded=None, failed=None, conditions=None)
        )
        self.read_calls: list[tuple[str, str]] = []
        self.fail_create = False

    def create_namespaced_job(self, namespace, body):
        if self.fail_create:
            raise RuntimeError("forbidden")
        self.created.append((namespace, body))
        return body

    def read_namespaced_job_status(self, name, namespace):
        self.read_calls.append((name, namespace))
        return self.status_obj


CONFIG = K8sJobConfig(
    namespace="corvus-ml",
    image="ghcr.io/onelitefeathernet/corvus-ml:1.2.3",
    command=["corvus-job"],
    cpu_request="500m",
    cpu_limit="2",
    memory_request="1Gi",
    memory_limit="4Gi",
    ttl_after_finished_s=3600,
    active_deadline_s=7200,
    backoff_limit=0,
    service_account="corvus-job",
    env_secret="corvus-job-env",
)


async def test_kubernetes_runner_creates_a_batch_job_with_limits_labels_and_ttl():
    api = FakeBatchApi()
    runner = KubernetesRunner(api, CONFIG)

    ref = await runner.submit(SPEC)

    assert ref == "corvus-train-1a2b3c4d"
    namespace, body = api.created[0]
    assert namespace == "corvus-ml"
    assert (body["apiVersion"], body["kind"]) == ("batch/v1", "Job")
    assert body["metadata"]["name"] == ref
    labels = body["metadata"]["labels"]
    assert labels["app.kubernetes.io/name"] == "corvus-job"
    assert labels["corvus.onelitefeather.net/job-type"] == "train"
    assert labels["corvus.onelitefeather.net/job-id"] == str(JOB_ID)
    spec = body["spec"]
    assert spec["ttlSecondsAfterFinished"] == 3600
    assert spec["activeDeadlineSeconds"] == 7200
    assert spec["backoffLimit"] == 0
    assert spec["template"]["metadata"]["labels"] == labels
    pod = spec["template"]["spec"]
    assert pod["restartPolicy"] == "Never"
    assert pod["serviceAccountName"] == "corvus-job"
    assert pod["securityContext"]["runAsNonRoot"] is True
    container = pod["containers"][0]
    assert container["image"] == "ghcr.io/onelitefeathernet/corvus-ml:1.2.3"
    assert container["command"] == ["corvus-job"]
    assert container["args"] == ["train"]
    assert container["resources"] == {
        "requests": {"cpu": "500m", "memory": "1Gi"},
        "limits": {"cpu": "2", "memory": "4Gi"},
    }
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert container["securityContext"]["capabilities"] == {"drop": ["ALL"]}
    assert container["envFrom"] == [{"secretRef": {"name": "corvus-job-env"}}]
    env = {e["name"]: e["value"] for e in container["env"]}
    assert env["CORVUS_JOB_SNAPSHOT_ID"] == str(SNAPSHOT)
    assert env["CORVUS_JOB_PARAMS"] == '{"epochs":3,"lr":0.1}'


async def test_kubernetes_job_without_secret_and_service_account_omits_them():
    api = FakeBatchApi()
    cfg = K8sJobConfig(**{**CONFIG.__dict__, "service_account": None, "env_secret": None})

    await KubernetesRunner(api, cfg).submit(SPEC)

    pod = api.created[0][1]["spec"]["template"]["spec"]
    assert "serviceAccountName" not in pod
    assert "envFrom" not in pod["containers"][0]


async def test_kubernetes_job_names_are_dns_safe_for_every_job_type():
    for t in JobType:
        name = KubernetesRunner.job_name(JobSpec(JOB_ID, t, SNAPSHOT, {}))
        assert name == name.lower()
        assert "_" not in name
        assert len(name) <= 63


def test_kubernetes_runner_requires_an_image():
    with pytest.raises(ValueError, match="IMAGE"):
        KubernetesRunner(FakeBatchApi(), K8sJobConfig(**{**CONFIG.__dict__, "image": ""}))


async def test_kubernetes_submit_errors_propagate():
    api = FakeBatchApi()
    api.fail_create = True

    with pytest.raises(RuntimeError):
        await KubernetesRunner(api, CONFIG).submit(SPEC)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (SimpleNamespace(), JobState.QUEUED),
        (SimpleNamespace(active=1), JobState.RUNNING),
        (SimpleNamespace(succeeded=1), JobState.SUCCEEDED),
        (SimpleNamespace(active=None, failed=1, conditions=None), JobState.QUEUED),
        (
            SimpleNamespace(
                conditions=[SimpleNamespace(type="Complete", status="True", reason=None)]
            ),
            JobState.SUCCEEDED,
        ),
        (
            SimpleNamespace(
                active=1,
                conditions=[
                    SimpleNamespace(type="Failed", status="True", reason="DeadlineExceeded")
                ],
            ),
            JobState.FAILED,
        ),
        (
            SimpleNamespace(
                active=1, conditions=[SimpleNamespace(type="Failed", status="False", reason="x")]
            ),
            JobState.RUNNING,
        ),
    ],
)
async def test_kubernetes_status_is_mapped(status, expected):
    api = FakeBatchApi()
    api.status_obj = SimpleNamespace(status=status)

    result = await KubernetesRunner(api, CONFIG).status("corvus-train-1a2b3c4d")

    assert result.state == expected
    assert api.read_calls == [("corvus-train-1a2b3c4d", "corvus-ml")]


async def test_kubernetes_failure_carries_the_reason():
    api = FakeBatchApi()
    cond = SimpleNamespace(type="Failed", status="True", reason="DeadlineExceeded")
    api.status_obj = SimpleNamespace(status=SimpleNamespace(conditions=[cond]))

    result = await KubernetesRunner(api, CONFIG).status("x")

    assert result == RunnerStatus(JobState.FAILED, "DeadlineExceeded")


# --- use cases ----------------------------------------------------------------------------------


@pytest.fixture
async def repos():
    snapshots = FakeSnapshotRepo()
    await snapshots.insert(
        SnapshotRecord(SNAPSHOT, UUID(int=1), "p", {}, "0" * 64, 0, 0, "u", FIXED_NOW)
    )
    return FakeJobRepo(), snapshots


async def _create(repos, runner, **over):
    jobs, snapshots = repos
    args: dict[str, Any] = {
        "job_type": JobType.EVALUATE,
        "snapshot_id": SNAPSHOT,
        "params": {},
        "actor": "dana",
        "now": FIXED_NOW,
        "new_id": lambda: JOB_ID,
    }
    args.update(over)
    return await create_job(jobs, snapshots, runner, **args)


async def test_create_job_persists_and_marks_it_running(repos):
    runner = FakeRunner()

    job = await _create(repos, runner)

    assert (job.status, job.runner, job.created_by) == ("running", "fake", "dana")
    assert job.runner_ref == "ref-" + JOB_ID.hex[:6]
    assert runner.submitted[0].type == JobType.EVALUATE
    assert repos[0].rows[JOB_ID] == job


async def test_create_job_for_an_unknown_snapshot_is_refused_before_anything_is_stored(repos):
    runner = FakeRunner()

    with pytest.raises(SnapshotMissingError):
        await _create(repos, runner, snapshot_id=UUID(int=1234))

    assert repos[0].rows == {}
    assert runner.submitted == []


async def test_failed_submission_is_recorded_as_a_failed_job(repos):
    runner = FakeRunner(fail_submit=True)

    job = await _create(repos, runner)

    assert job.status == "failed"
    assert job.message == "submission failed: RuntimeError"
    assert job.finished_at == FIXED_NOW


async def test_refresh_pulls_the_runner_state(repos):
    runner = FakeRunner()
    job = await _create(repos, runner)
    runner.states[job.runner_ref] = RunnerStatus(JobState.SUCCEEDED)
    later = FIXED_NOW.replace(minute=30)

    refreshed = await refresh_job(repos[0], runner, job, later)

    assert (refreshed.status, refreshed.finished_at, refreshed.updated_at) == (
        "succeeded",
        later,
        later,
    )


async def test_refresh_leaves_finished_jobs_alone(repos):
    runner = FakeRunner()
    job = await _create(repos, runner)
    runner.states[job.runner_ref] = RunnerStatus(JobState.SUCCEEDED)
    done = await refresh_job(repos[0], runner, job, FIXED_NOW)
    runner.states[job.runner_ref] = RunnerStatus(JobState.FAILED, "late")

    assert await refresh_job(repos[0], runner, done, FIXED_NOW) is done


async def test_refresh_keeps_the_stored_state_when_the_runner_is_unreachable(repos):
    runner = FakeRunner()
    job = await _create(repos, runner)
    runner.fail_status = True

    assert await refresh_job(repos[0], runner, job, FIXED_NOW) is job


async def test_refresh_ignores_jobs_of_another_runner(repos):
    job = await _create(repos, FakeRunner(name="local"))

    assert await refresh_job(repos[0], FakeRunner(name="kubernetes"), job, FIXED_NOW) is job


# --- HTTP ---------------------------------------------------------------------------------------


@pytest.fixture
def ds(env: Env, token):
    return env.headers(token(["data_scientist"], sub="dana"))


@pytest.fixture
def snapshot_id(env: Env, ds) -> str:
    pool = env.client.post("/api/v1/pools", json={"name": "p"}, headers=ds).json()["id"]
    return env.client.post(f"/api/v1/pools/{pool}/snapshots", headers=ds).json()["id"]


def test_submit_job_returns_202_with_the_persisted_job(env: Env, ds, snapshot_id):
    resp = env.client.post(
        "/api/v1/jobs",
        json={"type": "train", "snapshot_id": snapshot_id, "params": {"epochs": 2}},
        headers=ds,
    )

    assert resp.status_code == 202
    body = resp.json()
    assert (body["type"], body["status"], body["params"]) == ("train", "running", {"epochs": 2})
    assert env.runner.submitted[0].params == {"epochs": 2}


@pytest.mark.parametrize(
    "bad",
    [
        {"type": "deploy"},
        {"type": "train", "unknown": 1},
        {"type": "train", "params": {"blob": "x" * 20_000}},
    ],
)
def test_invalid_job_requests_are_rejected(env: Env, ds, snapshot_id, bad):
    resp = env.client.post("/api/v1/jobs", json={"snapshot_id": snapshot_id, **bad}, headers=ds)

    assert resp.status_code == 422
    assert env.runner.submitted == []


def test_job_for_an_unknown_snapshot_is_unprocessable(env: Env, ds):
    resp = env.client.post(
        "/api/v1/jobs", json={"type": "evaluate", "snapshot_id": str(UUID(int=77))}, headers=ds
    )

    assert resp.status_code == 422


def test_get_job_refreshes_a_running_job(env: Env, ds, snapshot_id):
    job = env.client.post(
        "/api/v1/jobs", json={"type": "evaluate", "snapshot_id": snapshot_id}, headers=ds
    ).json()
    ref = next(iter(env.runner.states))
    env.runner.states[ref] = RunnerStatus(JobState.FAILED, "OOMKilled")

    resp = env.client.get(f"/api/v1/jobs/{job['id']}", headers=ds).json()

    assert (resp["status"], resp["message"]) == ("failed", "OOMKilled")
    assert resp["finished_at"] is not None
    assert "runner_ref" not in resp


def test_jobs_can_be_listed_and_filtered_by_status(env: Env, ds, snapshot_id):
    for _ in range(3):
        env.clock.advance(1)
        env.client.post(
            "/api/v1/jobs", json={"type": "build_features", "snapshot_id": snapshot_id}, headers=ds
        )

    everything = env.client.get("/api/v1/jobs?limit=5", headers=ds).json()
    running = env.client.get("/api/v1/jobs?status=running&limit=5", headers=ds).json()
    done = env.client.get("/api/v1/jobs?status=succeeded", headers=ds).json()

    assert len(everything["items"]) == 3
    assert len(running["items"]) == 3
    assert done["items"] == []


def test_unknown_job_is_404(env: Env, ds):
    assert env.client.get(f"/api/v1/jobs/{UUID(int=3)}", headers=ds).status_code == 404


def test_runner_calls_do_not_block_the_event_loop():
    # to_thread keeps the blocking kubernetes client off the loop: the call runs in another thread.
    import threading

    seen: list[int] = []

    class Api(FakeBatchApi):
        def create_namespaced_job(self, namespace, body):
            seen.append(threading.get_ident())
            return super().create_namespaced_job(namespace, body)

    asyncio.run(KubernetesRunner(Api(), CONFIG).submit(SPEC))

    assert seen
    assert seen[0] != threading.get_ident()
