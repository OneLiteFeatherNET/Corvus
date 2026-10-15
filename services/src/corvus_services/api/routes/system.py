"""Operational endpoints: liveness, readiness (database) and Prometheus metrics. Unversioned, no auth."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from ..deps import Deps, get_deps

router = APIRouter(tags=["system"])


@router.get("/healthz", summary="Liveness")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get(
    "/readyz",
    summary="Readiness (database reachable)",
    responses={503: {"description": "Database not reachable"}},
)
async def readyz(response: Response, deps: Deps = Depends(get_deps)) -> dict[str, str]:
    try:
        await asyncio.wait_for(deps.repos.db.ping(), 3.0)
    except Exception as e:
        response.status_code = 503
        return {"status": "unavailable", "database": type(e).__name__}
    return {"status": "ok", "database": "ok"}


@router.get("/metrics", summary="Prometheus metrics", response_class=Response)
async def metrics(deps: Deps = Depends(get_deps)) -> Response:
    return Response(generate_latest(deps.metrics.registry), media_type=CONTENT_TYPE_LATEST)
