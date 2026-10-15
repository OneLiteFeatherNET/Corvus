"""Response bodies (pydantic) and the mapping from records."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .pools import PoolFilter
from .ports import (
    BlockEntry,
    ErasureRequest,
    JobRecord,
    LabelRecord,
    ModelRecord,
    PlayerInfo,
    PoolRecord,
    SnapshotRecord,
    TransitionRecord,
)


class Paged[T](BaseModel):
    items: list[T]
    next_cursor: str | None = Field(
        default=None, description="Pass as `cursor` to get the next page"
    )


class PoolOut(BaseModel):
    id: UUID
    name: str
    description: str
    filter: PoolFilter
    created_by: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, p: PoolRecord) -> PoolOut:
        return cls(
            id=p.id, name=p.name, description=p.description, filter=p.filter,
            created_by=p.created_by, created_at=p.created_at, updated_at=p.updated_at,
        )  # fmt: skip


class SnapshotOut(BaseModel):
    id: UUID
    pool_id: UUID
    pool_name: str
    sha256: str
    session_count: int
    file_count: int
    created_by: str
    created_at: datetime
    manifest: dict[str, Any] | None = Field(
        default=None, description="Present on the detail view only"
    )

    @classmethod
    def of(cls, s: SnapshotRecord, *, with_manifest: bool = False) -> SnapshotOut:
        return cls(
            id=s.id, pool_id=s.pool_id, pool_name=s.pool_name, sha256=s.sha256,
            session_count=s.session_count, file_count=s.file_count, created_by=s.created_by,
            created_at=s.created_at, manifest=s.manifest if with_manifest else None,
        )  # fmt: skip


class LabelOut(BaseModel):
    id: UUID
    subject_type: Literal["pseudonym", "session"]
    subject: str
    label: str
    source: str
    confidence: float
    valid_from: datetime | None
    valid_to: datetime | None
    note: str
    created_by: str
    created_at: datetime

    @classmethod
    def of(cls, r: LabelRecord) -> LabelOut:
        return cls(**r.__dict__)


class LabelImportOut(BaseModel):
    imported: int


class PlayerLookupOut(BaseModel):
    pseudonym: str
    key_id: str
    known: bool = Field(description="The pseudonym exists in the catalog")
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    erased_at: datetime | None = None

    @classmethod
    def of(cls, pseudonym: bytes, key_id: str, p: PlayerInfo | None) -> PlayerLookupOut:
        return cls(
            pseudonym=pseudonym.hex(),
            key_id=key_id,
            known=p is not None,
            first_seen=p.first_seen if p else None,
            last_seen=p.last_seen if p else None,
            erased_at=p.erased_at if p else None,
        )


class ErasureOut(BaseModel):
    id: UUID
    pseudonym: str
    requested_by: str
    requested_at: datetime
    status: str
    player_known: bool
    key_deleted: bool
    compaction_pending: bool

    @classmethod
    def of(cls, r: ErasureRequest) -> ErasureOut:
        return cls(
            id=r.id, pseudonym=r.pseudonym.hex(), requested_by=r.requested_by,
            requested_at=r.requested_at, status=r.status, player_known=r.player_known,
            key_deleted=r.key_deleted, compaction_pending=r.compaction_pending,
        )  # fmt: skip


class BlockEntryOut(BaseModel):
    pseudonym: str
    kind: Literal["block", "allow"]
    source: Literal["manual", "erasure"]
    reason: str
    created_by: str
    created_at: datetime

    @classmethod
    def of(cls, b: BlockEntry) -> BlockEntryOut:
        return cls(
            pseudonym=b.pseudonym.hex(), kind=b.kind, source=b.source, reason=b.reason,
            created_by=b.created_by, created_at=b.created_at,
        )  # fmt: skip


class PluginBlocklistOut(BaseModel):
    version: str = Field(
        description="SHA-256 over the two lists; changes whenever the lists change"
    )
    blocked: list[str]
    allowed: list[str]


class ModelOut(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    id: UUID
    name: str
    version: str
    candidate_type: str
    game_context: str
    domain: str
    snapshot_id: UUID
    metrics: dict[str, Any]
    onnx_key: str
    stage: str
    created_by: str
    created_at: datetime

    @classmethod
    def of(cls, m: ModelRecord) -> ModelOut:
        return cls(**m.__dict__)


class TransitionOut(BaseModel):
    id: UUID
    model_id: UUID
    from_stage: str
    to_stage: str
    actor: str
    reason: str
    gate: dict[str, Any] | None
    at: datetime

    @classmethod
    def of(cls, t: TransitionRecord) -> TransitionOut:
        return cls(**t.__dict__)


class JobOut(BaseModel):
    id: UUID
    type: str
    snapshot_id: UUID
    params: dict[str, Any]
    status: str
    runner: str
    message: str
    created_by: str
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None

    @classmethod
    def of(cls, j: JobRecord) -> JobOut:
        return cls(
            id=j.id, type=j.type, snapshot_id=j.snapshot_id, params=j.params, status=j.status,
            runner=j.runner, message=j.message, created_by=j.created_by, created_at=j.created_at,
            updated_at=j.updated_at, finished_at=j.finished_at,
        )  # fmt: skip
