"""Jobs (role: data_scientist)."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..auth import JOB_ID_HEADER, Principal, Role, enforce_job_scope, require
from ..deps import Deps, get_deps, get_page
from ..jobs import (
    REPORTABLE,
    JobFinishedError,
    JobState,
    JobType,
    SnapshotMissingError,
    create_job,
    refresh_job,
    report_job,
)
from ..pagination import PageRequest
from ..problems import ProblemError
from ..schemas import JobOut, Paged

router = APIRouter(tags=["jobs"])
_DS = Depends(require(Role.DATA_SCIENTIST))
_DS_OR_JOB = Depends(require(Role.DATA_SCIENTIST, allow_jobs=True))
MAX_PARAMS_BYTES = 16 * 1024
MAX_MESSAGE_CHARS = 2000


class JobIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: JobType
    snapshot_id: UUID
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Passed to the job as JSON (CORVUS_JOB_PARAMS); must not contain secrets",
    )

    @field_validator("params")
    @classmethod
    def _small(cls, v: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(v)) > MAX_PARAMS_BYTES:
            raise ValueError(f"params exceed {MAX_PARAMS_BYTES} bytes")
        return v


class JobReportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: JobState
    message: str = Field(default="", max_length=MAX_MESSAGE_CHARS)
    result: dict[str, Any] | None = Field(
        default=None,
        description="Result document (metrics, artefact keys); must not contain secrets",
    )

    @field_validator("status")
    @classmethod
    def _reportable(cls, v: JobState) -> JobState:
        if v not in REPORTABLE:
            raise ValueError("status must be one of running, succeeded, failed")
        return v

    @field_validator("result")
    @classmethod
    def _small(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is not None and len(json.dumps(v)) > MAX_PARAMS_BYTES:
            raise ValueError(f"result exceeds {MAX_PARAMS_BYTES} bytes")
        return v


@router.post(
    "/jobs",
    status_code=202,
    response_model=JobOut,
    responses={422: {"description": "Unknown snapshot or invalid parameters"}},
    summary="Submit a job",
)
async def submit_job(body: JobIn, who: Principal = _DS, deps: Deps = Depends(get_deps)) -> JobOut:
    try:
        job = await create_job(
            deps.repos.jobs,
            deps.repos.snapshots,
            deps.runner,
            job_type=body.type,
            snapshot_id=body.snapshot_id,
            params=body.params,
            actor=who.subject,
            now=deps.clock(),
            new_id=deps.new_id,
        )
    except SnapshotMissingError as e:
        raise ProblemError(422, "snapshot does not exist", slug="validation") from e
    deps.metrics.jobs.labels(body.type.value, deps.runner.name).inc()
    return JobOut.of(job)


@router.get("/jobs", response_model=Paged[JobOut], summary="List jobs, newest first")
async def list_jobs(
    status: JobState | None = Query(default=None),
    _: Principal = _DS,
    page: PageRequest = Depends(get_page),
    deps: Deps = Depends(get_deps),
) -> Paged[JobOut]:
    result = await deps.repos.jobs.list_page(page, status.value if status else None)
    return Paged[JobOut](items=[JobOut.of(j) for j in result.items], next_cursor=result.next_cursor)


@router.get(
    "/jobs/{job_id}",
    response_model=JobOut,
    responses={404: {"description": "Job not found"}},
    summary="Get a job; a running job's status is refreshed from the runner",
)
async def get_job(
    job_id: UUID,
    who: Principal = _DS_OR_JOB,
    job_header: str | None = Header(default=None, alias=JOB_ID_HEADER),
    deps: Deps = Depends(get_deps),
) -> JobOut:
    enforce_job_scope(who, job_id, job_header)
    job = await deps.repos.jobs.get(job_id)
    if job is None:
        raise ProblemError(404, "job not found", slug="not-found")
    return JobOut.of(await refresh_job(deps.repos.jobs, deps.runner, job, deps.clock()))


@router.patch(
    "/jobs/{job_id}",
    response_model=JobOut,
    responses={
        403: {"description": f"A job token needs `{JOB_ID_HEADER}` equal to the path id"},
        404: {"description": "Job not found"},
        409: {"description": "The job already finished with a different status"},
    },
    summary="Report status, message and result of a job",
    description=(
        "Called by the job process with its machine token (`CORVUS_API_URL`, `CORVUS_API_TOKEN`). "
        f"A job token may only update its own job: the header `{JOB_ID_HEADER}` (the job's "
        "`CORVUS_JOB_ID`) must equal the id in the path. `succeeded` and `failed` are final; "
        "repeating the identical report is accepted."
    ),
)
async def report_job_status(
    job_id: UUID,
    body: JobReportIn,
    who: Principal = _DS_OR_JOB,
    job_header: str | None = Header(default=None, alias=JOB_ID_HEADER),
    deps: Deps = Depends(get_deps),
) -> JobOut:
    enforce_job_scope(who, job_id, job_header)
    job = await deps.repos.jobs.get(job_id)
    if job is None:
        raise ProblemError(404, "job not found", slug="not-found")
    try:
        updated = await report_job(
            deps.repos.jobs,
            job,
            status=body.status,
            message=body.message,
            result=body.result,
            now=deps.clock(),
        )
    except JobFinishedError as e:
        raise ProblemError(409, f"the job already finished as {e}", slug="conflict") from e
    return JobOut.of(updated)
