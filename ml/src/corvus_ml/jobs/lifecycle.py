"""Job lifecycle: ``running`` -> ``succeeded`` | ``failed``, reported to the control plane.

Whatever the handler does, the final status is reported exactly once and its message passes
through :class:`~corvus_ml.jobs.context.Secrets`, so credentials never reach the API or the logs.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from corvus_ml.jobs.api import ApiError, JobApi
from corvus_ml.jobs.context import JobContext, Secrets
from corvus_ml.jobs.storage import ObjectStore

log = logging.getLogger("corvus.job")


class JobError(RuntimeError):
    """A failure with a message that is safe and useful to show in the UI."""


class JobTerminated(RuntimeError):  # noqa: N818 - reads better than JobTerminatedError
    """The process was asked to stop (SIGTERM, e.g. the Kubernetes deadline)."""


def json_safe(value: Any) -> Any:
    """Recursively drop non-finite floats (JSON has no NaN) and convert numpy scalars."""
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items() if json_safe(v) is not None}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


@dataclass
class JobEnv:
    """Collaborators of a running job; everything the handlers touch is injected."""

    api: JobApi
    store: ObjectStore
    scratch: Path
    secrets: Secrets
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    # Source of the player DEKs for raw-byte jobs (``corvus_ml.raw.keys.DekProvider``); None =
    # read ``corvus.player_keys`` with CORVUS_PG_DSN and unwrap with CORVUS_KEK.
    deks: Any = None


@dataclass
class JobOutcome:
    message: str
    result: dict[str, Any]


Handler = Callable[[JobContext, JobEnv], JobOutcome]


def run_job(ctx: JobContext, env: JobEnv, handler: Handler) -> int:
    """Run ``handler`` and report the lifecycle. Exit code: 0 succeeded, 1 failed."""
    try:
        env.api.update_job(ctx.job_id, "running", message=f"{ctx.job_type} started")
    except ApiError as exc:
        log.error("cannot report the job as running: %s", env.secrets.scrub(str(exc)))
        return 2
    try:
        outcome = handler(ctx, env)
    except JobTerminated:
        return _fail(ctx, env, "terminated before it finished")
    except Exception as exc:  # report every failure; the process exit code carries it too
        return _fail(ctx, env, _describe(exc))
    try:
        env.api.update_job(
            ctx.job_id,
            "succeeded",
            message=env.secrets.scrub(outcome.message),
            result=json_safe(outcome.result),
        )
    except ApiError as exc:
        log.error("cannot report success: %s", env.secrets.scrub(str(exc)))
        return 2
    log.info("job %s succeeded: %s", ctx.job_id, env.secrets.scrub(outcome.message))
    return 0


def _describe(exc: Exception) -> str:
    # Messages of our own errors are written for the UI; anything else gets its type name.
    if isinstance(exc, (JobError, ValueError, ApiError)):
        return str(exc) or type(exc).__name__
    return f"{type(exc).__name__}: {exc}"


def _fail(ctx: JobContext, env: JobEnv, text: str) -> int:
    message = env.secrets.scrub(text)
    log.error("job %s failed: %s", ctx.job_id, message)
    try:
        env.api.update_job(ctx.job_id, "failed", message=message)
    except ApiError as exc:
        log.error("cannot report the failure: %s", env.secrets.scrub(str(exc)))
    return 1
