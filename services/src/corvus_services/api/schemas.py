"""Response bodies (pydantic) and the mapping from records."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .pools import PoolFilter
from .ports import (
    BlockEntry,
    ErasureBrief,
    ErasureRequest,
    JobBrief,
    JobRecord,
    LabelRecord,
    ModelRecord,
    PlayerInfo,
    PoolRecord,
    ScoreHistoryRecord,
    ScoreLatestRecord,
    SnapshotRecord,
    Summary,
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
    result: dict[str, Any] | None = Field(
        default=None, description="Result document reported by the job when it finished"
    )

    @classmethod
    def of(cls, j: JobRecord) -> JobOut:
        return cls(
            id=j.id, type=j.type, snapshot_id=j.snapshot_id, params=j.params, status=j.status,
            runner=j.runner, message=j.message, created_by=j.created_by, created_at=j.created_at,
            updated_at=j.updated_at, finished_at=j.finished_at, result=j.result,
        )  # fmt: skip


class MeOut(BaseModel):
    subject: str
    display_name: str | None = Field(default=None, description="From the token, if it carries one")
    roles: list[str] = Field(description="Corvus roles after `CORVUS_API_ROLE_MAPPING`")


PSEUDONYM_PREFIX_LEN = 12


class JobBriefOut(BaseModel):
    id: UUID
    type: str
    status: str
    snapshot_id: UUID
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None

    @classmethod
    def of(cls, j: JobBrief) -> JobBriefOut:
        return cls(**j.__dict__)


class ErasureBriefOut(BaseModel):
    id: UUID
    pseudonym_prefix: str = Field(description="First 12 hex characters of the pseudonym")
    requested_at: datetime
    status: str
    compaction_pending: bool

    @classmethod
    def of(cls, e: ErasureBrief) -> ErasureBriefOut:
        return cls(
            id=e.id, pseudonym_prefix=e.pseudonym.hex()[:PSEUDONYM_PREFIX_LEN],
            requested_at=e.requested_at, status=e.status, compaction_pending=e.compaction_pending,
        )  # fmt: skip


class SummaryOut(BaseModel):
    pools: int
    snapshots: int
    jobs_by_status: dict[str, int] = Field(description="Every job status, zero when absent")
    models_by_stage: dict[str, int] = Field(description="Every model stage, zero when absent")
    labels_by_label: dict[str, int] = Field(description="Every label value, zero when absent")
    pending_erasures: int = Field(description="Erasure requests whose compaction is pending")
    recent_jobs: list[JobBriefOut]
    recent_erasures: list[ErasureBriefOut]

    @classmethod
    def of(cls, s: Summary) -> SummaryOut:
        def filled(counts: dict[str, int], keys: tuple[str, ...]) -> dict[str, int]:
            return {**dict.fromkeys(keys, 0), **counts}

        return cls(
            pools=s.pools,
            snapshots=s.snapshots,
            jobs_by_status=filled(s.jobs_by_status, ("queued", "running", "succeeded", "failed")),
            models_by_stage=filled(
                s.models_by_stage, ("registered", "shadow", "canary", "enforce", "archived")
            ),
            labels_by_label=filled(s.labels_by_label, ("legit", "cheat")),
            pending_erasures=s.pending_erasures,
            recent_jobs=[JobBriefOut.of(j) for j in s.recent_jobs],
            recent_erasures=[ErasureBriefOut.of(e) for e in s.recent_erasures],
        )


class ScoreLatestOut(BaseModel):
    game_context: str
    domain: str
    model_name: str
    model_version: str
    stage: Literal["shadow", "canary", "enforce"]
    score: float = Field(description="Window score in [0, 1]; higher = more likely cheating")
    smoothed: float = Field(description="Exponential moving average over the segment's windows")
    windows: int = Field(description="Number of windows behind `smoothed`")
    updated_at: datetime

    @classmethod
    def of(cls, r: ScoreLatestRecord) -> ScoreLatestOut:
        return cls(
            game_context=r.game_context, domain=r.domain, model_name=r.model_name,
            model_version=r.model_version, stage=r.stage,  # type: ignore[arg-type]
            score=r.score, smoothed=r.smoothed, windows=r.windows, updated_at=r.updated_at,
        )  # fmt: skip


class ScoreHistoryOut(BaseModel):
    session_id: str = Field(description="32 hex characters")
    segment: int
    game_context: str
    domain: str
    model_name: str
    model_version: str
    stage: Literal["shadow", "canary", "enforce"]
    score: float
    smoothed: float
    windows: int
    window_start_offset_nanos: int
    window_end_offset_nanos: int
    scored_at: datetime

    @classmethod
    def of(cls, r: ScoreHistoryRecord) -> ScoreHistoryOut:
        return cls(
            session_id=r.session_id.hex(), segment=r.segment, game_context=r.game_context,
            domain=r.domain, model_name=r.model_name, model_version=r.model_version,
            stage=r.stage,  # type: ignore[arg-type]
            score=r.score, smoothed=r.smoothed, windows=r.windows,
            window_start_offset_nanos=r.window_start_offset_nanos,
            window_end_offset_nanos=r.window_end_offset_nanos, scored_at=r.scored_at,
        )  # fmt: skip


class Items[T](BaseModel):
    items: list[T]
