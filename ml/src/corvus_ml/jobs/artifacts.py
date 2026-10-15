"""Registering what a job wrote, and the ``needs_rebuild`` guard.

A prefix is registered right after it was written, so that erasure and the raw retention can find
and delete it. ``409`` means an erasure invalidated raw-derived artefacts after the job started:
the prefix is deleted again and the job fails, so that nothing derived from erased data stays.
"""

from __future__ import annotations

import logging
from typing import Any

from corvus_ml.jobs.api import ArtifactInvalidated
from corvus_ml.jobs.context import JobContext
from corvus_ml.jobs.lifecycle import JobEnv, JobError

log = logging.getLogger("corvus.job.artifacts")


def register_prefix(
    ctx: JobContext, env: JobEnv, kind: str, prefix: str, *, contains_raw: bool
) -> None:
    """Register ``prefix`` (an object key prefix, with or without ``/``) of this job."""
    s3_prefix = prefix.rstrip("/") + "/"
    try:
        env.api.register_artifact(ctx.job_id, kind, s3_prefix, contains_raw)
    except ArtifactInvalidated:
        env.store.delete_prefix(s3_prefix.rstrip("/"))
        raise JobError(
            "an erasure invalidated the raw-derived artefacts of this snapshot while the job "
            f"ran; {s3_prefix} was deleted. Start a new job to rebuild"
        ) from None


def check_needs_rebuild(snapshot: dict[str, Any], *, strict: bool) -> None:
    """``needs_rebuild``: erasure deleted raw-derived artefacts of the snapshot.

    Building anew is usually what the caller wants, so this only logs; with ``strict`` it refuses.
    """
    if not snapshot.get("needs_rebuild"):
        return
    if strict:
        raise JobError("the snapshot needs a rebuild (erasure removed derived artefacts); refused")
    log.warning("the snapshot is flagged needs_rebuild; building anyway (set strict to refuse)")
