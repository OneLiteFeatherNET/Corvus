"""Retention of raw payloads and of the data derived from them (NFR-018)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from .ports import ObjectInfo


def effective_raw_retention(pool_days: int | None, default_days: int, max_days: int) -> int:
    """Days raw payloads of a pool are kept: its own value or the default, at most the maximum."""
    return max(1, min(default_days if pool_days is None else pool_days, max_days))


def artifact_expiry(newest_source: datetime, retention_days: int) -> datetime:
    """When a raw-derived artefact has to be gone: ``retention_days`` after the newest session
    it was built from, i.e. when the last raw payload it could contain has expired as well."""
    return newest_source + timedelta(days=retention_days)


@dataclass(frozen=True)
class LiveArtifact:
    """A live registered artefact that contains raw-derived content."""

    id: UUID
    s3_prefix: str
    newest_source: (
        datetime  # start of the newest session of its snapshot (or the snapshot's creation)
    )
    pool_days: int | None  # raw_retention_days of the snapshot's pool; None: default (or pool gone)


class PrefixStore(Protocol):
    async def list_objects(self, prefix: str) -> list[ObjectInfo]: ...
    async def delete(self, key: str) -> None: ...


SAFE_PREFIX_ROOTS = ("features/", "tokenizers/")


async def delete_prefix(store: PrefixStore, prefix: str) -> int:
    """Delete every object below a registered artefact prefix; returns how many were deleted.

    Refuses anything outside ``features/`` and ``tokenizers/`` (the API only registers those), so a
    bad catalog row can never reach the lake. Raises on the first failed delete: the caller keeps
    the catalog row and retries the whole prefix on the next run.
    """
    if not prefix.startswith(SAFE_PREFIX_ROOTS) or not prefix.endswith("/") or ".." in prefix:
        raise ValueError(f"refusing to delete outside the derived prefixes: {prefix!r}")
    deleted = 0
    for obj in await store.list_objects(prefix):
        await store.delete(obj.key)
        deleted += 1
    return deleted
