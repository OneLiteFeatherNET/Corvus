"""Immutable dataset snapshots: manifest construction and its canonical SHA-256."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

MANIFEST_VERSION = 1


@dataclass(frozen=True)
class LakeFileRef:
    id: int
    s3_key: str


def build_manifest(
    *,
    pool_id: UUID,
    pool_name: str,
    pool_filter: dict[str, Any],
    session_ids: Iterable[bytes],
    files: Iterable[LakeFileRef],
    created_at: datetime,
) -> dict[str, Any]:
    """The manifest is a pure function of its inputs: sessions and files are sorted and de-duplicated."""
    return {
        "version": MANIFEST_VERSION,
        "pool": {"id": str(pool_id), "name": pool_name},
        "filter": pool_filter,
        "sessions": sorted({s.hex() for s in session_ids}),
        "files": {
            "events": [
                {"id": f.id, "key": f.s3_key}
                for f in sorted(set(files), key=lambda f: (f.id, f.s3_key))
            ]
        },
        "created_at": created_at.isoformat(),
    }


def canonical_json(manifest: dict[str, Any]) -> bytes:
    return json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def manifest_sha256(manifest: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(manifest)).hexdigest()
