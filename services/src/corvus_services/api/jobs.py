"""Jobs: the runner port, its two adapters (local subprocess, Kubernetes batch/v1 Job) and the use cases.

Contract with the job process (same for both adapters): the arguments are
``<command...> <job type>``; the environment carries ``CORVUS_JOB_ID``, ``CORVUS_JOB_TYPE``,
``CORVUS_JOB_SNAPSHOT_ID`` and ``CORVUS_JOB_PARAMS`` (JSON). Params must not contain secrets.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID, uuid4

from .ports import JobRecord, JobRepo, SnapshotRepo
from .settings import ApiSettings

log = logging.getLogger("corvus.api.jobs")


class JobType(StrEnum):
    BUILD_FEATURES = "build_features"
    EVALUATE = "evaluate"
    TRAIN = "train"


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


TERMINAL = frozenset({JobState.SUCCEEDED, JobState.FAILED})


@dataclass(frozen=True)
class JobSpec:
    job_id: UUID
    type: JobType
    snapshot_id: UUID
    params: dict[str, Any]

    def env(self) -> dict[str, str]:
        return {
            "CORVUS_JOB_ID": str(self.job_id),
            "CORVUS_JOB_TYPE": self.type.value,
            "CORVUS_JOB_SNAPSHOT_ID": str(self.snapshot_id),
            "CORVUS_JOB_PARAMS": json.dumps(self.params, sort_keys=True, separators=(",", ":")),
        }


@dataclass(frozen=True)
class RunnerStatus:
    state: JobState
    message: str = ""


class JobRunner(Protocol):
    name: str

    async def submit(self, spec: JobSpec) -> str:
        """Start the job; returns the runner reference (process key, Kubernetes job name)."""
        ...

    async def status(self, ref: str) -> RunnerStatus: ...


# --- local (subprocess) -------------------------------------------------------------------------


class ProcessHandle(Protocol):
    def poll(self) -> int | None: ...


class ProcessLauncher(Protocol):
    def launch(self, argv: Sequence[str], env: Mapping[str, str]) -> ProcessHandle: ...


_SECRET_ENV = re.compile(r"^(CORVUS_API_.*|CORVUS_PSEUDONYM_KEY.*|CORVUS_KEK)$")
_JOB_API_ENV = frozenset({"CORVUS_API_URL", "CORVUS_API_TOKEN"})
"""How a job reaches the API; the token is the job's own machine token, not the API's secret."""


def child_environment(parent: Mapping[str, str], extra: Mapping[str, str]) -> dict[str, str]:
    """The environment of a job process: the parent's, minus the API's own secrets, plus ``extra``."""
    env = {k: v for k, v in parent.items() if k in _JOB_API_ENV or not _SECRET_ENV.match(k)}
    env.update(extra)
    return env


class SubprocessLauncher:
    """Starts a detached child without a shell. The API's own secrets are not passed on."""

    def launch(self, argv: Sequence[str], env: Mapping[str, str]) -> ProcessHandle:
        child_env = child_environment(os.environ, env)
        return subprocess.Popen(  # noqa: S603 - argv is a list, no shell
            list(argv),
            env=child_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )


class LocalRunner:
    """For standalone setups. Handles live in memory: after an API restart a running job is reported
    as failed because its exit status can no longer be observed."""

    name = "local"

    def __init__(self, command: Sequence[str], launcher: ProcessLauncher) -> None:
        if not command:
            raise ValueError("local job command must not be empty")
        self._command = list(command)
        self._launcher = launcher
        self._handles: dict[str, ProcessHandle] = {}

    async def submit(self, spec: JobSpec) -> str:
        ref = str(spec.job_id)
        argv = [*self._command, spec.type.value]
        self._handles[ref] = await asyncio.to_thread(self._launcher.launch, argv, spec.env())
        return ref

    async def status(self, ref: str) -> RunnerStatus:
        handle = self._handles.get(ref)
        if handle is None:
            return RunnerStatus(JobState.FAILED, "process handle lost (API restarted)")
        code = handle.poll()
        if code is None:
            return RunnerStatus(JobState.RUNNING)
        if code == 0:
            return RunnerStatus(JobState.SUCCEEDED)
        return RunnerStatus(JobState.FAILED, f"exit code {code}")


# --- kubernetes ---------------------------------------------------------------------------------


class BatchApi(Protocol):
    """The subset of ``kubernetes.client.BatchV1Api`` that is used."""

    def create_namespaced_job(self, namespace: str, body: Any) -> Any: ...
    def read_namespaced_job_status(self, name: str, namespace: str) -> Any: ...


@dataclass(frozen=True)
class K8sJobConfig:
    namespace: str
    image: str
    command: Sequence[str]
    cpu_request: str
    cpu_limit: str
    memory_request: str
    memory_limit: str
    ttl_after_finished_s: int
    active_deadline_s: int
    backoff_limit: int
    service_account: str | None = None
    env_secret: str | None = None
    scratch_size_limit: str = "20Gi"


SCRATCH_MOUNT = "/scratch"


class KubernetesRunner:
    name = "kubernetes"

    def __init__(self, api: BatchApi, config: K8sJobConfig) -> None:
        if not config.image:
            raise ValueError("CORVUS_API_JOB_K8S_IMAGE is required for the kubernetes runner")
        self._api = api
        self._cfg = config

    @staticmethod
    def job_name(spec: JobSpec) -> str:
        return f"corvus-{spec.type.value.replace('_', '-')}-{spec.job_id.hex[:8]}"

    def build_job(self, spec: JobSpec) -> dict[str, Any]:
        c = self._cfg
        name = self.job_name(spec)
        labels = {
            "app.kubernetes.io/name": "corvus-job",
            "app.kubernetes.io/part-of": "corvus",
            "app.kubernetes.io/managed-by": "corvus-api",
            "corvus.onelitefeather.net/job-type": spec.type.value.replace("_", "-"),
            "corvus.onelitefeather.net/job-id": str(spec.job_id),
        }
        container: dict[str, Any] = {
            "name": "job",
            "image": c.image,
            "command": list(c.command),
            "args": [spec.type.value],
            "env": [
                {"name": k, "value": v}
                for k, v in {
                    **spec.env(),
                    "CORVUS_JOB_SCRATCH_DIR": SCRATCH_MOUNT,
                    "TMPDIR": SCRATCH_MOUNT,
                }.items()
            ],
            "volumeMounts": [{"name": "scratch", "mountPath": SCRATCH_MOUNT}],
            "resources": {
                "requests": {"cpu": c.cpu_request, "memory": c.memory_request},
                "limits": {"cpu": c.cpu_limit, "memory": c.memory_limit},
            },
            "securityContext": {
                "allowPrivilegeEscalation": False,
                "capabilities": {"drop": ["ALL"]},
            },
        }
        if c.env_secret:
            container["envFrom"] = [{"secretRef": {"name": c.env_secret}}]
        pod_spec: dict[str, Any] = {
            "restartPolicy": "Never",
            "containers": [container],
            # Feature builds can be large: scratch space on a sized emptyDir, not the image layer.
            "volumes": [{"name": "scratch", "emptyDir": {"sizeLimit": c.scratch_size_limit}}],
            "securityContext": {"runAsNonRoot": True, "seccompProfile": {"type": "RuntimeDefault"}},
        }
        if c.service_account:
            pod_spec["serviceAccountName"] = c.service_account
        return {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {"name": name, "namespace": c.namespace, "labels": labels},
            "spec": {
                "ttlSecondsAfterFinished": c.ttl_after_finished_s,
                "activeDeadlineSeconds": c.active_deadline_s,
                "backoffLimit": c.backoff_limit,
                "template": {"metadata": {"labels": labels}, "spec": pod_spec},
            },
        }

    async def submit(self, spec: JobSpec) -> str:
        body = self.build_job(spec)
        await asyncio.to_thread(self._api.create_namespaced_job, self._cfg.namespace, body)
        return str(body["metadata"]["name"])

    async def status(self, ref: str) -> RunnerStatus:
        job = await asyncio.to_thread(
            self._api.read_namespaced_job_status, ref, self._cfg.namespace
        )
        st = getattr(job, "status", None)
        for cond in getattr(st, "conditions", None) or []:
            if getattr(cond, "status", None) != "True":
                continue
            if cond.type == "Complete":
                return RunnerStatus(JobState.SUCCEEDED)
            if cond.type == "Failed":
                return RunnerStatus(JobState.FAILED, str(getattr(cond, "reason", None) or "failed"))
        if getattr(st, "active", None):
            return RunnerStatus(JobState.RUNNING)
        if getattr(st, "succeeded", None):
            return RunnerStatus(JobState.SUCCEEDED)
        return RunnerStatus(JobState.QUEUED)


def build_runner(settings: ApiSettings) -> JobRunner:
    if settings.job_runner == "local":
        return LocalRunner(settings.job_local_command, SubprocessLauncher())
    from kubernetes import client, config  # imported lazily: only needed on this path

    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    cfg = K8sJobConfig(
        namespace=settings.job_k8s_namespace,
        image=settings.job_k8s_image,
        command=settings.job_k8s_command,
        cpu_request=settings.job_k8s_cpu_request,
        cpu_limit=settings.job_k8s_cpu_limit,
        memory_request=settings.job_k8s_memory_request,
        memory_limit=settings.job_k8s_memory_limit,
        ttl_after_finished_s=settings.job_k8s_ttl_after_finished_s,
        active_deadline_s=settings.job_k8s_active_deadline_s,
        backoff_limit=settings.job_k8s_backoff_limit,
        service_account=settings.job_k8s_service_account,
        env_secret=settings.job_k8s_env_secret,
        scratch_size_limit=settings.job_k8s_scratch_size_limit,
    )
    return KubernetesRunner(client.BatchV1Api(), cfg)


# --- use cases ----------------------------------------------------------------------------------


class SnapshotMissingError(Exception):
    pass


async def create_job(
    jobs: JobRepo,
    snapshots: SnapshotRepo,
    runner: JobRunner,
    *,
    job_type: JobType,
    snapshot_id: UUID,
    params: dict[str, Any],
    actor: str,
    now: datetime,
    new_id: Callable[[], UUID] = uuid4,
) -> JobRecord:
    if await snapshots.get(snapshot_id) is None:
        raise SnapshotMissingError(str(snapshot_id))
    job_id = new_id()
    record = JobRecord(
        job_id, job_type.value, snapshot_id, params, JobState.QUEUED.value, runner.name,
        None, "", actor, now, now,
    )  # fmt: skip
    await jobs.create(record)
    try:
        ref = await runner.submit(JobSpec(job_id, job_type, snapshot_id, params))
    except Exception as e:
        log.error("job %s submission failed (%s)", job_id, type(e).__name__)
        return await jobs.update(
            job_id,
            status=JobState.FAILED.value,
            runner_ref=None,
            message=f"submission failed: {type(e).__name__}",
            now=now,
            finished=True,
        )
    return await jobs.update(
        job_id, status=JobState.RUNNING.value, runner_ref=ref, message="", now=now, finished=False
    )


class JobFinishedError(Exception):
    """The job already reached a final status that differs from the reported one."""


REPORTABLE = (JobState.RUNNING, JobState.SUCCEEDED, JobState.FAILED)


async def report_job(
    jobs: JobRepo,
    job: JobRecord,
    *,
    status: JobState,
    message: str,
    result: dict[str, Any] | None,
    now: datetime,
) -> JobRecord:
    """Apply a status report from the job process. Final statuses are final; repeating the same
    report is accepted (retries after a lost response)."""
    if JobState(job.status) in TERMINAL:
        if status.value == job.status and message == job.message and result in (None, job.result):
            return job
        raise JobFinishedError(job.status)
    return await jobs.update(
        job.id,
        status=status.value,
        runner_ref=None,
        message=message,
        now=now,
        finished=status in TERMINAL,
        result=result,
    )


async def refresh_job(jobs: JobRepo, runner: JobRunner, job: JobRecord, now: datetime) -> JobRecord:
    """Pulls the runner status into the stored job. Runner errors leave the stored status unchanged."""
    if JobState(job.status) in TERMINAL or job.runner_ref is None or job.runner != runner.name:
        return job
    try:
        st = await runner.status(job.runner_ref)
    except Exception as e:
        log.warning("job %s status refresh failed (%s)", job.id, type(e).__name__)
        return job
    if st.state.value == job.status and (st.message == job.message or not st.message):
        return job  # a message the job itself reported is not overwritten by an empty one
    return await jobs.update(
        job.id,
        status=st.state.value,
        runner_ref=job.runner_ref,
        message=st.message,
        now=now,
        finished=st.state in TERMINAL,
    )
