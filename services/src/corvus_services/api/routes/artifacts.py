"""Derived artefacts of a snapshot: prefixes a job wrote below the lake bucket.

Tokenised raw-byte features contain payload content and are personal data derived from ``raw``.
Jobs register every prefix they write here, so erasure (``corvus-compact``) and retention
(``corvus-housekeeping``) can find and delete it. Unregistered output is invisible to both.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel, ConfigDict, Field

from ..auth import JOB_ID_HEADER, Principal, Role, enforce_job_scope, require
from ..deps import Deps, get_deps
from ..ports import ArtifactRecord, JobRecord, StaleBuildError
from ..problems import ProblemError

router = APIRouter(tags=["artifacts"])
_DS_OR_JOB = Depends(require(Role.DATA_SCIENTIST, allow_jobs=True))

KIND = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_PREFIX_CHARS = re.compile(r"^[A-Za-z0-9._=/-]+$")
PREFIX_ROOTS = ("features", "tokenizers")


def check_prefix(prefix: str, snapshot_id: UUID) -> str | None:
    """None if ``prefix`` is acceptable, else the reason. A prefix must lie below
    ``features/<snapshot_id>/`` or ``tokenizers/<snapshot_id>/`` and end with ``/``: housekeeping
    deletes everything below a registered prefix, so it must never reach other data."""
    if not prefix.endswith("/") or not _PREFIX_CHARS.match(prefix):
        return "s3_prefix must end with / and use only letters, digits and . _ = / -"
    if "//" in prefix or any(part in ("", ".", "..") for part in prefix[:-1].split("/")):
        return "s3_prefix must not contain empty, . or .. segments"
    if not any(prefix.startswith(f"{root}/{snapshot_id}/") for root in PREFIX_ROOTS):
        return f"s3_prefix must start with features/{snapshot_id}/ or tokenizers/{snapshot_id}/"
    return None


class ArtifactIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(
        description="Lower-case identifier, e.g. `raw_features`, `aggregate_features`, `tokenizer`"
    )
    s3_prefix: str = Field(
        max_length=512,
        description="Below `features/<snapshot_id>/` or `tokenizers/<snapshot_id>/`, ends with `/`",
    )
    contains_raw: bool = Field(
        description="The prefix holds tokenised payload content (derived from `raw`)"
    )
    job_id: UUID | None = Field(
        default=None, description="The job that wrote it; a job token sends the header instead"
    )


class ArtifactOut(BaseModel):
    id: UUID
    snapshot_id: UUID
    job_id: UUID | None
    kind: str
    s3_prefix: str
    contains_raw: bool
    created_at: datetime

    @classmethod
    def of(cls, a: ArtifactRecord) -> ArtifactOut:
        return cls(
            id=a.id, snapshot_id=a.snapshot_id, job_id=a.job_id, kind=a.kind,
            s3_prefix=a.s3_prefix, contains_raw=a.contains_raw, created_at=a.created_at,
        )  # fmt: skip


class ArtifactsOut(BaseModel):
    items: list[ArtifactOut]


_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {"description": "Already registered: the row was updated (idempotent)"},
    403: {"description": f"A job token needs `{JOB_ID_HEADER}` of a job of this snapshot"},
    404: {"description": "Snapshot or job not found"},
    409: {
        "description": (
            "The job started before an erasure invalidated the snapshot's raw-derived artefacts: "
            "delete the prefix and rebuild with a new job"
        )
    },
}


async def _job_for(
    deps: Deps, who: Principal, snapshot_id: UUID, job_id: UUID | None, header: str | None
) -> JobRecord | None:
    if who.is_job:
        try:
            declared = UUID(header) if header else None
        except ValueError:
            declared = None
        if declared is None or (job_id is not None and job_id != declared):
            raise ProblemError(
                403, f"job tokens must send {JOB_ID_HEADER} with their own job id", slug="forbidden"
            )
        enforce_job_scope(who, declared, header)
        job_id = declared
    if job_id is None:
        return None
    job = await deps.repos.jobs.get(job_id)
    if job is None:
        raise ProblemError(404, "job not found", slug="not-found")
    if job.snapshot_id != snapshot_id:
        if who.is_job:
            raise ProblemError(403, "the job belongs to another snapshot", slug="forbidden")
        raise ProblemError(422, "the job belongs to another snapshot", slug="validation")
    return job


async def _register(
    deps: Deps, snapshot_id: UUID, body: ArtifactIn, job: JobRecord | None, response: Response
) -> ArtifactOut:
    if await deps.repos.snapshots.get(snapshot_id) is None:
        raise ProblemError(404, "snapshot not found", slug="not-found")
    if not KIND.match(body.kind):
        raise ProblemError(
            422, "kind must be lower case letters, digits and _ (at most 40)", slug="validation"
        )
    problem = check_prefix(body.s3_prefix, snapshot_id)
    if problem is not None:
        raise ProblemError(422, problem, slug="validation")
    record = ArtifactRecord(
        deps.new_id(), snapshot_id, job.id if job else None, body.kind, body.s3_prefix,
        body.contains_raw, deps.clock(),
    )  # fmt: skip
    try:
        stored, created = await deps.repos.snapshots.register_artifact(
            record, job.created_at if job else None
        )
    except StaleBuildError as e:
        raise ProblemError(
            409,
            "an erasure invalidated this snapshot's raw-derived artefacts after the job started; "
            "delete the prefix and rebuild with a new job",
            slug="conflict",
        ) from e
    response.status_code = 201 if created else 200
    return ArtifactOut.of(stored)


@router.post(
    "/snapshots/{snapshot_id}/artifacts",
    status_code=201,
    response_model=ArtifactOut,
    responses=_RESPONSES,
    summary="Register a prefix a job wrote for a snapshot (idempotent per snapshot and prefix)",
)
async def register_snapshot_artifact(
    snapshot_id: UUID,
    body: ArtifactIn,
    response: Response,
    who: Principal = _DS_OR_JOB,
    job_header: str | None = Header(default=None, alias=JOB_ID_HEADER),
    deps: Deps = Depends(get_deps),
) -> ArtifactOut:
    job = await _job_for(deps, who, snapshot_id, body.job_id, job_header)
    return await _register(deps, snapshot_id, body, job, response)


@router.post(
    "/jobs/{job_id}/artifacts",
    status_code=201,
    response_model=ArtifactOut,
    responses=_RESPONSES,
    summary="Register a prefix by job id (the snapshot is the job's snapshot)",
)
async def register_job_artifact(
    job_id: UUID,
    body: ArtifactIn,
    response: Response,
    who: Principal = _DS_OR_JOB,
    job_header: str | None = Header(default=None, alias=JOB_ID_HEADER),
    deps: Deps = Depends(get_deps),
) -> ArtifactOut:
    enforce_job_scope(who, job_id, job_header)
    job = await deps.repos.jobs.get(job_id)
    if job is None:
        raise ProblemError(404, "job not found", slug="not-found")
    if body.job_id is not None and body.job_id != job_id:
        raise ProblemError(422, "job_id in the body differs from the path", slug="validation")
    return await _register(deps, job.snapshot_id, body, job, response)


@router.get(
    "/snapshots/{snapshot_id}/artifacts",
    response_model=ArtifactsOut,
    responses={404: {"description": "Snapshot not found"}},
    summary="Live registered artefacts of a snapshot",
)
async def list_snapshot_artifacts(
    snapshot_id: UUID, _: Principal = _DS_OR_JOB, deps: Deps = Depends(get_deps)
) -> ArtifactsOut:
    if await deps.repos.snapshots.get(snapshot_id) is None:
        raise ProblemError(404, "snapshot not found", slug="not-found")
    items = await deps.repos.snapshots.list_artifacts(snapshot_id)
    return ArtifactsOut(items=[ArtifactOut.of(a) for a in items])
