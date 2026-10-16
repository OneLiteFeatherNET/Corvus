from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from api_fakes import FIXED_NOW, FakeJobRepo, FakeRunner, FakeSnapshotRepo
from api_support import Env

from corvus_services.api.jobs import (
    JobGoneError,
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
    reconcile_jobs,
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
        self.read_error: Exception | None = None

    def create_namespaced_job(self, namespace, body):
        if self.fail_create:
            raise RuntimeError("forbidden")
        self.created.append((namespace, body))
        return body

    def read_namespaced_job_status(self, name, namespace):
        self.read_calls.append((name, namespace))
        if self.read_error is not None:
            raise self.read_error
        return self.status_obj


class FakeApiException(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status


class FakeCoreApi:
    def __init__(self, pods: list[Any] | None = None, error: Exception | None = None) -> None:
        self.pods = pods or []
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def list_namespaced_pod(self, namespace, label_selector):
        self.calls.append((namespace, label_selector))
        if self.error is not None:
            raise self.error
        return SimpleNamespace(items=self.pods)


def _pod(reason, exit_code, *, last=False):
    term = SimpleNamespace(reason=reason, exit_code=exit_code)
    state = SimpleNamespace(terminated=None if last else term)
    last_state = SimpleNamespace(terminated=term if last else None)
    cs = SimpleNamespace(state=state, last_state=last_state)
    return SimpleNamespace(status=SimpleNamespace(container_statuses=[cs], reason=None))


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
    cond = SimpleNamespace(type="Failed", status="True", reason="BackoffLimitExceeded")
    api.status_obj = SimpleNamespace(status=SimpleNamespace(conditions=[cond]))

    result = await KubernetesRunner(api, CONFIG).status("x")

    assert result == RunnerStatus(JobState.FAILED, "BackoffLimitExceeded")


def _failed(reason):
    cond = SimpleNamespace(type="Failed", status="True", reason=reason)
    return SimpleNamespace(status=SimpleNamespace(conditions=[cond]))


@pytest.mark.parametrize(
    ("pod", "expected"),
    [
        (_pod("OOMKilled", 137), "OOMKilled (exit 137)"),
        (_pod("Error", 1), "Error (exit 1)"),
        (_pod("OOMKilled", 137, last=True), "OOMKilled (exit 137)"),
        (_pod(None, 2), "Error (exit 2)"),
    ],
)
async def test_failed_job_reports_the_pod_termination_instead_of_backoff_limit(pod, expected):
    api, core = FakeBatchApi(), FakeCoreApi([pod])
    api.status_obj = _failed("BackoffLimitExceeded")

    result = await KubernetesRunner(api, CONFIG, core).status("corvus-train-1a2b3c4d")

    assert result == RunnerStatus(JobState.FAILED, expected)
    assert core.calls == [("corvus-ml", "job-name=corvus-train-1a2b3c4d")]


async def test_failed_job_without_pod_info_keeps_the_condition_reason():
    api = FakeBatchApi()
    api.status_obj = _failed("BackoffLimitExceeded")

    result = await KubernetesRunner(api, CONFIG, FakeCoreApi([])).status("x")

    assert result == RunnerStatus(JobState.FAILED, "BackoffLimitExceeded")


async def test_pod_lookup_errors_do_not_hide_the_job_failure():
    api = FakeBatchApi()
    api.status_obj = _failed("BackoffLimitExceeded")

    result = await KubernetesRunner(api, CONFIG, FakeCoreApi(error=RuntimeError("x"))).status("x")

    assert result == RunnerStatus(JobState.FAILED, "BackoffLimitExceeded")


async def test_deadline_exceeded_has_a_clear_message():
    api = FakeBatchApi()
    api.status_obj = _failed("DeadlineExceeded")

    result = await KubernetesRunner(api, CONFIG, FakeCoreApi([_pod("Error", 143)])).status("x")

    assert result.state == JobState.FAILED
    assert result.message == "DeadlineExceeded: job ran longer than the 7200 s deadline"


async def test_missing_kubernetes_job_raises_job_gone():
    api = FakeBatchApi()
    api.read_error = FakeApiException(404)

    with pytest.raises(JobGoneError):
        await KubernetesRunner(api, CONFIG).status("x")


async def test_other_kubernetes_api_errors_propagate_unchanged():
    api = FakeBatchApi()
    api.read_error = FakeApiException(503)

    with pytest.raises(FakeApiException):
        await KubernetesRunner(api, CONFIG).status("x")


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


def test_kubernetes_job_mounts_a_sized_scratch_volume_and_points_the_job_at_it():
    cfg = K8sJobConfig(**{**CONFIG.__dict__, "scratch_size_limit": "5Gi"})

    pod = KubernetesRunner(FakeBatchApi(), cfg).build_job(SPEC)["spec"]["template"]["spec"]

    assert pod["volumes"] == [{"name": "scratch", "emptyDir": {"sizeLimit": "5Gi"}}]
    container = pod["containers"][0]
    assert container["volumeMounts"] == [{"name": "scratch", "mountPath": "/scratch"}]
    env = {e["name"]: e["value"] for e in container["env"]}
    assert env["CORVUS_JOB_SCRATCH_DIR"] == "/scratch"
    assert env["TMPDIR"] == "/scratch"


def test_kubernetes_job_passes_the_job_type_as_first_argument_and_in_the_environment():
    container = KubernetesRunner(FakeBatchApi(), CONFIG).build_job(SPEC)["spec"]["template"][
        "spec"
    ]["containers"][0]

    env = {e["name"]: e["value"] for e in container["env"]}
    assert container["args"][0] == env["CORVUS_JOB_TYPE"] == "train"


# --- refresh and reconcile ----------------------------------------------------------------------


async def test_refresh_marks_a_job_gone_from_the_cluster_as_failed(repos):
    runner = FakeRunner()
    job = await _create(repos, runner)
    runner.gone = True

    refreshed = await refresh_job(repos[0], runner, job, FIXED_NOW)

    assert (refreshed.status, refreshed.message) == (
        "failed",
        "job disappeared from the cluster",
    )
    assert refreshed.finished_at == FIXED_NOW


async def test_refresh_keeps_the_status_on_transient_runner_errors(repos):
    runner = FakeRunner()
    job = await _create(repos, runner)
    runner.fail_status = True

    assert await refresh_job(repos[0], runner, job, FIXED_NOW) == job


async def test_refresh_does_not_overwrite_a_status_the_job_reported_meanwhile(repos):
    runner = FakeRunner()
    job = await _create(repos, runner)
    await repos[0].update(
        JOB_ID, status="succeeded", runner_ref=None, message="done", now=FIXED_NOW, finished=True
    )
    runner.states[job.runner_ref] = RunnerStatus(JobState.FAILED, "OOMKilled (exit 137)")

    refreshed = await refresh_job(repos[0], runner, job, FIXED_NOW)

    assert refreshed.status == "succeeded"
    assert repos[0].rows[JOB_ID].status == "succeeded"


async def test_reconcile_fails_killed_jobs_and_leaves_the_others(repos):
    runner = FakeRunner()
    killed = await _create(repos, runner, new_id=lambda: UUID(int=101 << 112))
    alive = await _create(repos, runner, new_id=lambda: UUID(int=102 << 112))
    done = await _create(repos, runner, new_id=lambda: UUID(int=103 << 112))
    runner.states[killed.runner_ref] = RunnerStatus(JobState.FAILED, "OOMKilled (exit 137)")
    await repos[0].update(
        done.id, status="succeeded", runner_ref=None, message="", now=FIXED_NOW, finished=True
    )

    changed = await reconcile_jobs(repos[0], runner, FIXED_NOW)

    rows = repos[0].rows
    assert changed == [killed.id]
    assert (rows[killed.id].status, rows[killed.id].message) == ("failed", "OOMKilled (exit 137)")
    assert rows[alive.id].status == "running"
    assert rows[done.id].status == "succeeded"


async def test_reconcile_continues_after_a_failing_refresh(repos):
    runner = FakeRunner(fail_status=True)
    await _create(repos, runner, new_id=lambda: UUID(int=101 << 112))
    await _create(repos, runner, new_id=lambda: UUID(int=102 << 112))

    assert await reconcile_jobs(repos[0], runner, FIXED_NOW) == []
    assert {j.status for j in repos[0].rows.values()} == {"running"}


async def test_reconcile_once_counts_changed_jobs_and_survives_errors(repos):
    from corvus_services.api.app import reconcile_once
    from corvus_services.api.deps import Deps

    runner = FakeRunner()
    job = await _create(repos, runner)
    runner.states[job.runner_ref] = RunnerStatus(JobState.FAILED, "OOMKilled (exit 137)")
    deps = SimpleNamespace(
        repos=SimpleNamespace(jobs=repos[0]),
        runner=runner,
        clock=lambda: FIXED_NOW,
        metrics=Deps.__dataclass_fields__["metrics"].default_factory(),
    )

    await reconcile_once(deps)  # type: ignore[arg-type]
    await reconcile_once(deps)  # type: ignore[arg-type]  # idempotent: nothing left to change

    assert repos[0].rows[job.id].status == "failed"
    assert deps.metrics.jobs_reconciled.labels("failed")._value.get() == 1
    deps.repos = None  # a broken repo must not raise out of the loop
    await reconcile_once(deps)  # type: ignore[arg-type]
