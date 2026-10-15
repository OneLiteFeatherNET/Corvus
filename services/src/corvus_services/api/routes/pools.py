"""Pools, previews and dataset snapshots (role: data_scientist)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Response

from ..auth import Principal, Role, require
from ..deps import Deps, get_deps, get_page
from ..pagination import PageRequest
from ..pools import PoolFilter, PoolIn, PreviewOut, compute_preview
from ..ports import DuplicateError, PoolRecord, SnapshotRecord
from ..problems import ProblemError
from ..schemas import Paged, PoolOut, SnapshotOut
from ..snapshots import build_manifest, manifest_sha256

router = APIRouter(tags=["pools"])
_DS = Depends(require(Role.DATA_SCIENTIST))
_DS_OR_JOB = Depends(require(Role.DATA_SCIENTIST, allow_jobs=True))

_NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"description": "Pool not found"}}


async def _pool_or_404(deps: Deps, pool_id: UUID) -> PoolRecord:
    pool = await deps.repos.pools.get(pool_id)
    if pool is None:
        raise ProblemError(404, "pool not found", slug="not-found")
    return pool


@router.post("/pools", status_code=201, response_model=PoolOut, summary="Create a pool")
async def create_pool(
    body: PoolIn, who: Principal = _DS, deps: Deps = Depends(get_deps)
) -> PoolOut:
    now = deps.clock()
    pool = PoolRecord(
        deps.new_id(), body.name, body.description, body.filter, who.subject, now, now
    )
    try:
        await deps.repos.pools.create(pool)
    except DuplicateError as e:
        raise ProblemError(409, "a pool with this name already exists", slug="conflict") from e
    return PoolOut.of(pool)


@router.get("/pools", response_model=Paged[PoolOut], summary="List pools, newest first")
async def list_pools(
    _: Principal = _DS, page: PageRequest = Depends(get_page), deps: Deps = Depends(get_deps)
) -> Paged[PoolOut]:
    result = await deps.repos.pools.list_page(page)
    return Paged[PoolOut](
        items=[PoolOut.of(p) for p in result.items], next_cursor=result.next_cursor
    )


@router.get("/pools/{pool_id}", response_model=PoolOut, responses=_NOT_FOUND, summary="Get a pool")
async def get_pool(pool_id: UUID, _: Principal = _DS, deps: Deps = Depends(get_deps)) -> PoolOut:
    return PoolOut.of(await _pool_or_404(deps, pool_id))


@router.put(
    "/pools/{pool_id}", response_model=PoolOut, responses=_NOT_FOUND, summary="Replace a pool"
)
async def update_pool(
    pool_id: UUID, body: PoolIn, _: Principal = _DS, deps: Deps = Depends(get_deps)
) -> PoolOut:
    old = await _pool_or_404(deps, pool_id)
    new = PoolRecord(
        old.id,
        body.name,
        body.description,
        body.filter,
        old.created_by,
        old.created_at,
        deps.clock(),
    )
    try:
        if not await deps.repos.pools.update(new):
            raise ProblemError(404, "pool not found", slug="not-found")
    except DuplicateError as e:
        raise ProblemError(409, "a pool with this name already exists", slug="conflict") from e
    return PoolOut.of(new)


@router.delete(
    "/pools/{pool_id}",
    status_code=204,
    responses=_NOT_FOUND,
    summary="Delete a pool (its snapshots are kept)",
)
async def delete_pool(
    pool_id: UUID, _: Principal = _DS, deps: Deps = Depends(get_deps)
) -> Response:
    if not await deps.repos.pools.delete(pool_id):
        raise ProblemError(404, "pool not found", slug="not-found")
    return Response(status_code=204)


@router.post(
    "/pools/preview",
    response_model=PreviewOut,
    summary="Counts for a pool filter that is not saved (live preview)",
    description="The body is a pool `filter`, validated like on create. Nothing is stored.",
)
async def preview_filter(
    body: PoolFilter, _: Principal = _DS, deps: Deps = Depends(get_deps)
) -> PreviewOut:
    return compute_preview(await deps.repos.catalog.preview(body))


@router.post(
    "/pools/{pool_id}/preview",
    response_model=PreviewOut,
    responses=_NOT_FOUND,
    summary="Counts for the pool's current filter, computed with SQL over the catalog",
)
async def preview_pool(
    pool_id: UUID, _: Principal = _DS, deps: Deps = Depends(get_deps)
) -> PreviewOut:
    pool = await _pool_or_404(deps, pool_id)
    return compute_preview(await deps.repos.catalog.preview(pool.filter))


@router.post(
    "/pools/{pool_id}/snapshots",
    status_code=201,
    response_model=SnapshotOut,
    responses=_NOT_FOUND,
    summary="Materialise an immutable dataset snapshot of the pool",
)
async def create_snapshot(
    pool_id: UUID, who: Principal = _DS, deps: Deps = Depends(get_deps)
) -> SnapshotOut:
    pool = await _pool_or_404(deps, pool_id)
    material = await deps.repos.catalog.snapshot_material(pool.filter)
    now = deps.clock()
    manifest = build_manifest(
        pool_id=pool.id,
        pool_name=pool.name,
        pool_filter=pool.filter.model_dump(mode="json"),
        session_ids=material.session_ids,
        files=material.files,
        created_at=now,
    )
    snapshot = SnapshotRecord(
        id=deps.new_id(),
        pool_id=pool.id,
        pool_name=pool.name,
        manifest=manifest,
        sha256=manifest_sha256(manifest),
        session_count=len(manifest["sessions"]),
        file_count=len(manifest["files"]["events"]),
        created_by=who.subject,
        created_at=now,
    )
    await deps.repos.snapshots.insert(snapshot)
    return SnapshotOut.of(snapshot, with_manifest=False)


@router.get(
    "/pools/{pool_id}/snapshots",
    response_model=Paged[SnapshotOut],
    summary="List the snapshots of a pool, newest first",
)
async def list_pool_snapshots(
    pool_id: UUID,
    _: Principal = _DS,
    page: PageRequest = Depends(get_page),
    deps: Deps = Depends(get_deps),
) -> Paged[SnapshotOut]:
    result = await deps.repos.snapshots.list_page(page, pool_id)
    return Paged[SnapshotOut](
        items=[SnapshotOut.of(s) for s in result.items], next_cursor=result.next_cursor
    )


@router.get("/snapshots", response_model=Paged[SnapshotOut], summary="List all snapshots")
async def list_snapshots(
    _: Principal = _DS_OR_JOB, page: PageRequest = Depends(get_page), deps: Deps = Depends(get_deps)
) -> Paged[SnapshotOut]:
    result = await deps.repos.snapshots.list_page(page, None)
    return Paged[SnapshotOut](
        items=[SnapshotOut.of(s) for s in result.items], next_cursor=result.next_cursor
    )


@router.get(
    "/snapshots/{snapshot_id}",
    response_model=SnapshotOut,
    responses={404: {"description": "Snapshot not found"}},
    summary="Get a snapshot including its manifest",
)
async def get_snapshot(
    snapshot_id: UUID, _: Principal = _DS_OR_JOB, deps: Deps = Depends(get_deps)
) -> SnapshotOut:
    snapshot = await deps.repos.snapshots.get(snapshot_id)
    if snapshot is None:
        raise ProblemError(404, "snapshot not found", slug="not-found")
    return SnapshotOut.of(snapshot, with_manifest=True)
