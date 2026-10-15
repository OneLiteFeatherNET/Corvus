"""Jobs (role: data_scientist)."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..auth import Principal, Role, require
from ..deps import Deps, get_deps, get_page
from ..jobs import JobState, JobType, SnapshotMissingError, create_job, refresh_job
from ..pagination import PageRequest
from ..problems import ProblemError
from ..schemas import JobOut, Paged

router = APIRouter(tags=["jobs"])
_DS = Depends(require(Role.DATA_SCIENTIST))
MAX_PARAMS_BYTES = 16 * 1024


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
async def get_job(job_id: UUID, _: Principal = _DS, deps: Deps = Depends(get_deps)) -> JobOut:
    job = await deps.repos.jobs.get(job_id)
    if job is None:
        raise ProblemError(404, "job not found", slug="not-found")
    return JobOut.of(await refresh_job(deps.repos.jobs, deps.runner, job, deps.clock()))
