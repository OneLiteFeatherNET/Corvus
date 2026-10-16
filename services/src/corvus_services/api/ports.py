"""Records and repository ports of the control plane. Postgres adapters live in ``pg.py``; tests use fakes."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from .pagination import Page, PageRequest
from .pools import PoolFilter, RawPreview
from .snapshots import LakeFileRef


class DuplicateError(Exception):
    """A unique constraint is violated."""


class StaleError(Exception):
    """The row changed since it was read (optimistic concurrency)."""


@dataclass(frozen=True)
class PoolRecord:
    id: UUID
    name: str
    description: str
    filter: PoolFilter
    created_by: str
    created_at: datetime
    updated_at: datetime
    raw_retention_days: int | None = None  # None: the deployment default


@dataclass(frozen=True)
class SnapshotRecord:
    id: UUID
    pool_id: UUID
    pool_name: str
    manifest: dict[str, Any]
    sha256: str
    session_count: int
    file_count: int
    created_by: str
    created_at: datetime
    needs_rebuild: bool = False  # raw-derived artefacts were deleted by erasure


@dataclass(frozen=True)
class ArtifactRecord:
    """A prefix a job wrote below the lake bucket for a snapshot (``corvus.derived_artifacts``)."""

    id: UUID
    snapshot_id: UUID
    job_id: UUID | None
    kind: str
    s3_prefix: str
    contains_raw: bool
    created_at: datetime


class StaleBuildError(Exception):
    """The job started before an erasure invalidated the snapshot's raw-derived artefacts."""


@dataclass(frozen=True)
class LabelRecord:
    id: UUID
    subject_type: Literal["pseudonym", "session"]
    subject: str  # hex
    label: str
    source: str
    confidence: float
    valid_from: datetime | None
    valid_to: datetime | None
    note: str
    created_by: str
    created_at: datetime


@dataclass(frozen=True)
class PlayerInfo:
    pseudonym: bytes
    key_id: str
    first_seen: datetime
    last_seen: datetime
    erased_at: datetime | None


@dataclass(frozen=True)
class BlockEntry:
    pseudonym: bytes
    kind: Literal["block", "allow"]
    source: Literal["manual", "erasure"]
    reason: str
    created_by: str
    created_at: datetime


@dataclass(frozen=True)
class ErasureRequest:
    id: UUID
    pseudonym: bytes
    requested_by: str
    requested_at: datetime
    status: str
    player_known: bool
    key_deleted: bool
    compaction_pending: bool
    compacted_at: datetime | None = None


@dataclass(frozen=True)
class ModelRecord:
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


@dataclass(frozen=True)
class ModelFilter:
    """Exact-match filters of the model list; ``None`` means no restriction."""

    name: str | None = None
    version: str | None = None
    game_context: str | None = None
    domain: str | None = None
    stage: str | None = None


@dataclass(frozen=True)
class TransitionRecord:
    id: UUID
    model_id: UUID
    from_stage: str
    to_stage: str
    actor: str
    reason: str
    gate: dict[str, Any] | None
    at: datetime


@dataclass(frozen=True)
class JobRecord:
    id: UUID
    type: str
    snapshot_id: UUID
    params: dict[str, Any]
    status: str
    runner: str
    runner_ref: str | None
    message: str
    created_by: str
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None
    result: dict[str, Any] | None = None


@dataclass(frozen=True)
class SessionSegment:
    segment: int
    game_context: str


@dataclass(frozen=True)
class SessionLabel:
    label: str
    source: str
    confidence: float
    valid_from: datetime | None
    valid_to: datetime | None


@dataclass(frozen=True)
class SnapshotSession:
    """A manifest session as the jobs need it. ``pseudonym`` and ``started_at`` are ``None`` for
    a session that is no longer in the catalog."""

    session_id: bytes
    pseudonym: bytes | None
    started_at: datetime | None
    segments: list[SessionSegment] = field(default_factory=list)
    labels: list[SessionLabel] = field(default_factory=list)
    erased: bool = False
    client_protocol: int | None = None  # corvus.sessions.client_protocol


@dataclass(frozen=True)
class SnapshotMaterial:
    session_ids: list[bytes]
    files: list[LakeFileRef]


class PoolRepo(Protocol):
    async def create(self, pool: PoolRecord) -> None: ...
    async def get(self, pool_id: UUID) -> PoolRecord | None: ...
    async def list_page(self, req: PageRequest) -> Page[PoolRecord]: ...
    async def update(self, pool: PoolRecord) -> bool: ...
    async def delete(self, pool_id: UUID) -> bool: ...


class PoolCatalog(Protocol):
    """Read-only SQL over the ingest catalog for a pool filter."""

    async def preview(self, pool_filter: PoolFilter) -> RawPreview: ...
    async def snapshot_material(self, pool_filter: PoolFilter) -> SnapshotMaterial: ...


@dataclass(frozen=True)
class CurrentFile:
    """The live lake file that holds the data of a manifest file after compaction."""

    manifest_file_id: int
    id: int
    key: str


class SnapshotRepo(Protocol):
    async def insert(self, snapshot: SnapshotRecord) -> None: ...
    async def get(self, snapshot_id: UUID) -> SnapshotRecord | None: ...
    async def list_page(self, req: PageRequest, pool_id: UUID | None) -> Page[SnapshotRecord]: ...
    async def sessions_page(
        self, snapshot_id: UUID, after: bytes | None, limit: int, exclude_erased: bool
    ) -> list[SnapshotSession]:
        """Up to ``limit`` sessions of the manifest, ascending by id, greater than ``after``;
        sessions of erased players are left out if ``exclude_erased``."""
        ...

    async def current_files(self, snapshot_id: UUID) -> list[CurrentFile]:
        """Live files for the manifest's ``events`` files: a file replaced by compaction is
        followed to its successor; a file without live successor is omitted."""
        ...

    async def current_raw_files(self, snapshot_id: UUID) -> list[CurrentFile]:
        """Live ``raw`` files that hold payloads of the manifest's sessions (found through
        ``lake_file_sessions``, so a compaction replacement is already followed). The manifest
        lists no raw files; ``manifest_file_id`` equals ``id``."""
        ...

    async def register_artifact(
        self, artifact: ArtifactRecord, job_started_at: datetime | None
    ) -> tuple[ArtifactRecord, bool]:
        """Insert, or revive/update the row of ``(snapshot_id, s3_prefix)``; returns the stored
        row and whether it is new. Raises :class:`StaleBuildError` if ``job_started_at`` is before
        the snapshot's ``needs_rebuild_at``; a ``contains_raw`` artefact of a job that started
        after it clears ``needs_rebuild``."""
        ...

    async def list_artifacts(self, snapshot_id: UUID) -> list[ArtifactRecord]:
        """Live (not deleted) artefacts of a snapshot, ordered by prefix."""
        ...


class LabelRepo(Protocol):
    async def add_many(self, labels: list[LabelRecord]) -> None:
        """All or nothing."""
        ...

    async def list_page(
        self, req: PageRequest, subject_type: str | None, subject: bytes | None
    ) -> Page[LabelRecord]: ...


class PlayerRepo(Protocol):
    async def get(self, pseudonym: bytes) -> PlayerInfo | None: ...


class ErasureTx(Protocol):
    """The steps of an erasure; all run in one database transaction."""

    async def lock_player(self, pseudonym: bytes) -> PlayerInfo | None: ...
    async def delete_player_key(self, pseudonym: bytes) -> bool: ...
    async def delete_scores(self, pseudonym: bytes) -> int:
        """Delete the player's rows in ``scores_latest`` and ``score_history``; returns the count."""
        ...

    async def mark_erased(self, pseudonym: bytes, at: datetime) -> None: ...
    async def block(self, entry: BlockEntry) -> None: ...
    async def record_request(self, request: ErasureRequest) -> None: ...


class ErasureStore(Protocol):
    def transaction(self) -> AbstractAsyncContextManager[ErasureTx]:
        """Commits when the block ends normally, rolls back when it raises."""
        ...

    async def get_request(self, request_id: UUID) -> ErasureRequest | None: ...
    async def list_requests(self, req: PageRequest, status: str | None) -> Page[ErasureRequest]:
        """Newest ``requested_at`` first."""
        ...


class BlocklistRepo(Protocol):
    async def get(self, pseudonym: bytes) -> BlockEntry | None: ...
    async def list_page(self, req: PageRequest, kind: str | None) -> Page[BlockEntry]: ...
    async def put(self, entry: BlockEntry) -> None:
        """Insert or replace by pseudonym."""
        ...

    async def delete(self, pseudonym: bytes) -> bool: ...
    async def all_pseudonyms(self) -> tuple[list[bytes], list[bytes]]:
        """(blocked, allowed), each sorted."""
        ...


class ModelRepo(Protocol):
    async def create(self, model: ModelRecord) -> None: ...
    async def get(self, model_id: UUID) -> ModelRecord | None: ...
    async def get_by_name_version(self, name: str, version: str) -> ModelRecord | None: ...
    async def list_page(self, req: PageRequest, flt: ModelFilter) -> Page[ModelRecord]: ...
    async def enforced_in_scope(
        self, snapshot_id: UUID, game_context: str, exclude: UUID
    ) -> list[ModelRecord]: ...

    async def apply_transition(
        self, model_id: UUID, expected_stage: str, transitions: list[TransitionRecord]
    ) -> ModelRecord:
        """Atomically apply ``transitions`` (the first one is for ``model_id`` and must start at
        ``expected_stage``; others archive replaced models). Raises ``StaleError`` otherwise."""
        ...

    async def transitions(self, model_id: UUID) -> list[TransitionRecord]: ...


class JobRepo(Protocol):
    async def create(self, job: JobRecord) -> None: ...
    async def get(self, job_id: UUID) -> JobRecord | None: ...
    async def list_page(self, req: PageRequest, status: str | None) -> Page[JobRecord]: ...
    async def list_active(self) -> list[JobRecord]:
        """Jobs that are not final (``queued`` or ``running``), oldest first."""
        ...

    async def update(
        self,
        job_id: UUID,
        *,
        status: str,
        runner_ref: str | None,
        message: str,
        now: datetime,
        finished: bool,
        result: dict[str, Any] | None = None,
    ) -> JobRecord:
        """``result`` is stored when given and kept otherwise."""
        ...


@dataclass(frozen=True)
class ScoreLatestRecord:
    """Latest score of a player for one (game context, domain, model)."""

    game_context: str
    domain: str
    model_name: str
    model_version: str
    stage: str
    score: float
    smoothed: float
    windows: int
    updated_at: datetime


@dataclass(frozen=True)
class ScoreHistoryRecord:
    id: int
    session_id: bytes
    segment: int
    game_context: str
    domain: str
    model_name: str
    model_version: str
    stage: str
    score: float
    smoothed: float
    windows: int
    window_start_offset_nanos: int
    window_end_offset_nanos: int
    scored_at: datetime


class ScoreRepo(Protocol):
    async def latest(
        self, pseudonym: bytes, include_shadow: bool = False
    ) -> list[ScoreLatestRecord]:
        """Ordered by game context, domain, model name; ``shadow`` rows only on request."""
        ...

    async def history(
        self,
        pseudonym: bytes,
        game_context: str | None,
        limit: int,
        include_shadow: bool = False,
    ) -> list[ScoreHistoryRecord]:
        """Newest first, at most ``limit`` rows (``shadow`` rows are filtered before the limit)."""
        ...


@dataclass(frozen=True)
class JobBrief:
    id: UUID
    type: str
    status: str
    snapshot_id: UUID
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True)
class ErasureBrief:
    id: UUID
    pseudonym: bytes
    requested_at: datetime
    status: str
    compaction_pending: bool


@dataclass(frozen=True)
class Summary:
    pools: int
    snapshots: int
    jobs_by_status: dict[str, int]
    models_by_stage: dict[str, int]
    labels_by_label: dict[str, int]
    pending_erasures: int
    recent_jobs: list[JobBrief]
    recent_erasures: list[ErasureBrief]


class SummaryRepo(Protocol):
    async def summary(self, recent: int) -> Summary:
        """Counts over the whole catalog plus the ``recent`` newest jobs and erasure requests."""
        ...


class Database(Protocol):
    async def ping(self) -> None: ...


@dataclass
class Repos:
    pools: PoolRepo
    catalog: PoolCatalog
    snapshots: SnapshotRepo
    labels: LabelRepo
    players: PlayerRepo
    erasure: ErasureStore
    blocklist: BlocklistRepo
    models: ModelRepo
    jobs: JobRepo
    scores: ScoreRepo
    summary: SummaryRepo
    db: Database
    closers: list[Any] = field(default_factory=list)
