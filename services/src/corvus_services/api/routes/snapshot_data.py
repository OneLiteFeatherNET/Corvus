"""What jobs read from a snapshot after player erasure: its sessions and its live lake files.

Snapshots are immutable, so their manifest still lists erased sessions and the file ids that
``corvus-compact`` has replaced. These endpoints resolve both at read time.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from ..auth import Principal, Role, require
from ..deps import Deps, get_deps
from ..problems import ProblemError

router = APIRouter(tags=["snapshots"])
_DS = Depends(require(Role.DATA_SCIENTIST))
_NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"description": "Snapshot not found"}}


class SessionsOut(BaseModel):
    items: list[str]
    next_cursor: str | None = None


class CurrentFileOut(BaseModel):
    manifest_file_id: int
    id: int
    key: str


class FilesOut(BaseModel):
    items: list[CurrentFileOut]


def _after(cursor: str | None) -> bytes | None:
    if cursor is None:
        return None
    try:
        raw = bytes.fromhex(cursor)
    except ValueError:
        raw = b""
    if len(raw) != 16:
        raise ProblemError(400, "invalid cursor", slug="invalid-cursor")
    return raw


@router.get(
    "/snapshots/{snapshot_id}/sessions",
    response_model=SessionsOut,
    responses=_NOT_FOUND,
    summary="Session ids of a snapshot, ascending, optionally without erased players",
    description=(
        "Pages through the manifest's sessions (32 hex characters). With `exclude_erased=true` the "
        "sessions of erased players are left out; jobs must use this when they read a snapshot "
        "because the manifest itself is immutable. `cursor` is the `next_cursor` of the previous "
        "page (the last session id of that page)."
    ),
)
async def snapshot_sessions(
    snapshot_id: UUID,
    exclude_erased: bool = Query(default=False),
    limit: int | None = Query(default=None, ge=1),
    cursor: str | None = Query(default=None, description="`next_cursor` of the previous page"),
    _: Principal = _DS,
    deps: Deps = Depends(get_deps),
) -> SessionsOut:
    after = _after(cursor)
    if await deps.repos.snapshots.get(snapshot_id) is None:
        raise ProblemError(404, "snapshot not found", slug="not-found")
    s = deps.settings
    size = min(limit if limit is not None else s.default_page_size, s.max_page_size)
    rows = await deps.repos.snapshots.sessions_page(snapshot_id, after, size + 1, exclude_erased)
    items = [r.hex() for r in rows[:size]]
    return SessionsOut(items=items, next_cursor=items[-1] if len(rows) > size else None)


@router.get(
    "/snapshots/{snapshot_id}/files",
    response_model=FilesOut,
    responses=_NOT_FOUND,
    summary="Live events files of a snapshot (follows compaction replacements)",
)
async def snapshot_files(
    snapshot_id: UUID, _: Principal = _DS, deps: Deps = Depends(get_deps)
) -> FilesOut:
    if await deps.repos.snapshots.get(snapshot_id) is None:
        raise ProblemError(404, "snapshot not found", slug="not-found")
    files = await deps.repos.snapshots.current_files(snapshot_id)
    return FilesOut(
        items=[
            CurrentFileOut(manifest_file_id=f.manifest_file_id, id=f.id, key=f.key) for f in files
        ]
    )
