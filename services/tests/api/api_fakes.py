"""In-memory fakes of the control-plane ports (no database, no network)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from itertools import count
from typing import Any
from uuid import UUID

from corvus_services.api.jobs import JobSpec, RunnerStatus
from corvus_services.api.jobs import JobState as JS
from corvus_services.api.pagination import Page, PageRequest, paginate
from corvus_services.api.pools import PoolFilter, RawPreview
from corvus_services.api.ports import (
    BlockEntry,
    DuplicateError,
    ErasureRequest,
    ErasureTx,
    JobRecord,
    LabelRecord,
    ModelRecord,
    PlayerInfo,
    PoolRecord,
    Repos,
    SnapshotMaterial,
    SnapshotRecord,
    StaleError,
    TransitionRecord,
)

FIXED_NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


class MutableClock:
    def __init__(self, now: datetime = FIXED_NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def sequential_ids() -> Any:
    counter = count(1)
    return lambda: UUID(int=next(counter))


def _created_key(r: Any) -> tuple[datetime, str]:
    return r.created_at, str(r.id)


class FakePoolRepo:
    def __init__(self) -> None:
        self.rows: dict[UUID, PoolRecord] = {}

    async def create(self, pool: PoolRecord) -> None:
        if any(p.name == pool.name for p in self.rows.values()):
            raise DuplicateError("name")
        self.rows[pool.id] = pool

    async def get(self, pool_id: UUID) -> PoolRecord | None:
        return self.rows.get(pool_id)

    async def list_page(self, req: PageRequest) -> Page[PoolRecord]:
        return paginate(list(self.rows.values()), req, _created_key)

    async def update(self, pool: PoolRecord) -> bool:
        if pool.id not in self.rows:
            return False
        if any(p.name == pool.name and p.id != pool.id for p in self.rows.values()):
            raise DuplicateError("name")
        self.rows[pool.id] = pool
        return True

    async def delete(self, pool_id: UUID) -> bool:
        return self.rows.pop(pool_id, None) is not None


class FakeCatalog:
    def __init__(self) -> None:
        self.raw = RawPreview(0, 0, 0, {}, [])
        self.material = SnapshotMaterial([], [])
        self.filters: list[PoolFilter] = []

    async def preview(self, pool_filter: PoolFilter) -> RawPreview:
        self.filters.append(pool_filter)
        return self.raw

    async def snapshot_material(self, pool_filter: PoolFilter) -> SnapshotMaterial:
        self.filters.append(pool_filter)
        return self.material


class FakeSnapshotRepo:
    """Append-only, like the database trigger: there is no update or delete."""

    def __init__(self) -> None:
        self.rows: dict[UUID, SnapshotRecord] = {}

    async def insert(self, snapshot: SnapshotRecord) -> None:
        if snapshot.id in self.rows:
            raise DuplicateError("id")
        self.rows[snapshot.id] = snapshot

    async def get(self, snapshot_id: UUID) -> SnapshotRecord | None:
        return self.rows.get(snapshot_id)

    async def list_page(self, req: PageRequest, pool_id: UUID | None) -> Page[SnapshotRecord]:
        rows = [s for s in self.rows.values() if pool_id is None or s.pool_id == pool_id]
        return paginate(rows, req, _created_key)


class FakeLabelRepo:
    def __init__(self) -> None:
        self.rows: list[LabelRecord] = []

    async def add_many(self, labels: list[LabelRecord]) -> None:
        self.rows.extend(labels)

    async def list_page(
        self, req: PageRequest, subject_type: str | None, subject: bytes | None
    ) -> Page[LabelRecord]:
        rows = [
            r
            for r in self.rows
            if (subject_type is None or r.subject_type == subject_type)
            and (subject is None or r.subject == subject.hex())
        ]
        return paginate(rows, req, _created_key)


class FakePlayerRepo:
    def __init__(self, store: FakeErasureStore) -> None:
        self._store = store

    async def get(self, pseudonym: bytes) -> PlayerInfo | None:
        return self._store.players.get(pseudonym)


@dataclass
class _ErasureState:
    players: dict[bytes, PlayerInfo]
    keys: set[bytes]
    blocklist: dict[bytes, BlockEntry]
    requests: dict[UUID, ErasureRequest]


class FakeErasureTx:
    def __init__(self, state: _ErasureState, fail_at: str | None) -> None:
        self._s = state
        self._fail_at = fail_at

    def _maybe_fail(self, step: str) -> None:
        if self._fail_at == step:
            raise RuntimeError(f"injected failure at {step}")

    async def lock_player(self, pseudonym: bytes) -> PlayerInfo | None:
        return self._s.players.get(pseudonym)

    async def delete_player_key(self, pseudonym: bytes) -> bool:
        self._maybe_fail("delete_player_key")
        existed = pseudonym in self._s.keys
        self._s.keys.discard(pseudonym)
        return existed

    async def mark_erased(self, pseudonym: bytes, at: datetime) -> None:
        self._maybe_fail("mark_erased")
        p = self._s.players[pseudonym]
        self._s.players[pseudonym] = replace(p, erased_at=p.erased_at or at)

    async def block(self, entry: BlockEntry) -> None:
        self._maybe_fail("block")
        self._s.blocklist[entry.pseudonym] = entry

    async def record_request(self, request: ErasureRequest) -> None:
        self._maybe_fail("record_request")
        self._s.requests[request.id] = request


class FakeErasureStore:
    """Transactional: work happens on a copy that replaces the committed state only on success."""

    def __init__(self) -> None:
        self.players: dict[bytes, PlayerInfo] = {}
        self.keys: set[bytes] = set()
        self.blocklist: dict[bytes, BlockEntry] = {}
        self.requests: dict[UUID, ErasureRequest] = {}
        self.fail_at: str | None = None

    def add_player(self, pseudonym: bytes, *, with_key: bool = True) -> None:
        self.players[pseudonym] = PlayerInfo(pseudonym, "k1", FIXED_NOW, FIXED_NOW, None)
        if with_key:
            self.keys.add(pseudonym)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[ErasureTx]:
        work = _ErasureState(
            dict(self.players), set(self.keys), dict(self.blocklist), dict(self.requests)
        )
        yield FakeErasureTx(work, self.fail_at)
        self.players, self.keys = work.players, work.keys
        self.blocklist, self.requests = work.blocklist, work.requests

    async def get_request(self, request_id: UUID) -> ErasureRequest | None:
        return self.requests.get(request_id)


class FakeBlocklistRepo:
    def __init__(self, store: FakeErasureStore) -> None:
        self._store = store

    async def get(self, pseudonym: bytes) -> BlockEntry | None:
        return self._store.blocklist.get(pseudonym)

    async def list_page(self, req: PageRequest, kind: str | None) -> Page[BlockEntry]:
        rows = [b for b in self._store.blocklist.values() if kind is None or b.kind == kind]
        return paginate(rows, req, lambda b: (b.created_at, b.pseudonym.hex()))

    async def put(self, entry: BlockEntry) -> None:
        self._store.blocklist[entry.pseudonym] = entry

    async def delete(self, pseudonym: bytes) -> bool:
        return self._store.blocklist.pop(pseudonym, None) is not None

    async def all_pseudonyms(self) -> tuple[list[bytes], list[bytes]]:
        items = sorted(self._store.blocklist.values(), key=lambda b: b.pseudonym)
        return (
            [b.pseudonym for b in items if b.kind == "block"],
            [b.pseudonym for b in items if b.kind == "allow"],
        )


class FakeModelRepo:
    def __init__(self) -> None:
        self.rows: dict[UUID, ModelRecord] = {}
        self.audit: list[TransitionRecord] = []

    async def create(self, model: ModelRecord) -> None:
        if any((m.name, m.version) == (model.name, model.version) for m in self.rows.values()):
            raise DuplicateError("name+version")
        self.rows[model.id] = model

    async def get(self, model_id: UUID) -> ModelRecord | None:
        return self.rows.get(model_id)

    async def list_page(self, req: PageRequest, stage: str | None) -> Page[ModelRecord]:
        rows = [m for m in self.rows.values() if stage is None or m.stage == stage]
        return paginate(rows, req, _created_key)

    async def enforced_in_scope(
        self, snapshot_id: UUID, game_context: str, exclude: UUID
    ) -> list[ModelRecord]:
        return [
            m
            for m in self.rows.values()
            if m.stage == "enforce"
            and m.snapshot_id == snapshot_id
            and m.game_context == game_context
            and m.id != exclude
        ]

    async def apply_transition(
        self, model_id: UUID, expected_stage: str, transitions: list[TransitionRecord]
    ) -> ModelRecord:
        for t in transitions:  # validate everything first: all or nothing
            if self.rows[t.model_id].stage != t.from_stage:
                raise StaleError(str(t.model_id))
        for t in transitions:
            self.rows[t.model_id] = replace(self.rows[t.model_id], stage=t.to_stage)
            self.audit.append(t)
        return self.rows[model_id]

    async def transitions(self, model_id: UUID) -> list[TransitionRecord]:
        return [t for t in self.audit if t.model_id == model_id]


class FakeJobRepo:
    def __init__(self) -> None:
        self.rows: dict[UUID, JobRecord] = {}

    async def create(self, job: JobRecord) -> None:
        self.rows[job.id] = job

    async def get(self, job_id: UUID) -> JobRecord | None:
        return self.rows.get(job_id)

    async def list_page(self, req: PageRequest, status: str | None) -> Page[JobRecord]:
        rows = [j for j in self.rows.values() if status is None or j.status == status]
        return paginate(rows, req, _created_key)

    async def update(
        self,
        job_id: UUID,
        *,
        status: str,
        runner_ref: str | None,
        message: str,
        now: datetime,
        finished: bool,
    ) -> JobRecord:
        old = self.rows[job_id]
        self.rows[job_id] = replace(
            old,
            status=status,
            runner_ref=runner_ref or old.runner_ref,
            message=message,
            updated_at=now,
            finished_at=now if finished else old.finished_at,
        )
        return self.rows[job_id]


class FakeDb:
    def __init__(self) -> None:
        self.healthy = True

    async def ping(self) -> None:
        if not self.healthy:
            raise ConnectionError("db down")


@dataclass
class FakeRunner:
    name: str = "fake"
    submitted: list[JobSpec] = field(default_factory=list)
    states: dict[str, RunnerStatus] = field(default_factory=dict)
    fail_submit: bool = False
    fail_status: bool = False

    async def submit(self, spec: JobSpec) -> str:
        if self.fail_submit:
            raise RuntimeError("cluster unreachable")
        self.submitted.append(spec)
        ref = f"ref-{spec.job_id.hex[:6]}"
        self.states[ref] = RunnerStatus(JS.RUNNING)
        return ref

    async def status(self, ref: str) -> RunnerStatus:
        if self.fail_status:
            raise RuntimeError("cluster unreachable")
        return self.states[ref]


def make_repos() -> tuple[Repos, dict[str, Any]]:
    erasure = FakeErasureStore()
    parts: dict[str, Any] = {
        "pools": FakePoolRepo(),
        "catalog": FakeCatalog(),
        "snapshots": FakeSnapshotRepo(),
        "labels": FakeLabelRepo(),
        "erasure": erasure,
        "players": FakePlayerRepo(erasure),
        "blocklist": FakeBlocklistRepo(erasure),
        "models": FakeModelRepo(),
        "jobs": FakeJobRepo(),
        "db": FakeDb(),
    }
    return Repos(**parts), parts
