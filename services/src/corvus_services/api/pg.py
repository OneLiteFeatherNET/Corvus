"""PostgreSQL adapters of the control-plane ports (psycopg 3 async pool, parameterised SQL only).

Every statement is a constant string; values are always bound parameters. The only f-strings assemble
constant fragments (column lists, WHERE conjunctions built from constants).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Literal, cast
from uuid import UUID

import psycopg
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from .pagination import Page, PageRequest, page_of
from .pools import (
    LABEL_MATCH,
    FileShare,
    PoolFilter,
    RawPreview,
    build_pool_query,
    preview_statements,
    snapshot_statements,
)
from .ports import (
    BlockEntry,
    CurrentFile,
    DuplicateError,
    ErasureBrief,
    ErasureRequest,
    ErasureTx,
    JobBrief,
    JobRecord,
    LabelRecord,
    ModelFilter,
    ModelRecord,
    PlayerInfo,
    PoolRecord,
    Repos,
    ScoreHistoryRecord,
    ScoreLatestRecord,
    SessionLabel,
    SessionSegment,
    SnapshotMaterial,
    SnapshotRecord,
    SnapshotSession,
    StaleError,
    Summary,
    TransitionRecord,
)
from .queries import (
    DELETE_SCORE_HISTORY,
    DELETE_SCORES_LATEST,
    SUMMARY_COUNTS,
    SUMMARY_GROUPS,
    SUMMARY_RECENT_ERASURES,
    SUMMARY_RECENT_JOBS,
    UPDATE_JOB,
    list_sql,
    model_conditions,
)
from .snapshots import LakeFileRef

type Pool = AsyncConnectionPool[Any]

_READ_ONLY_RR = "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"


async def open_pool(dsn: str, max_size: int) -> Pool:
    pool: Pool = AsyncConnectionPool(dsn, min_size=1, max_size=max_size, open=False)
    await pool.open(wait=True, timeout=30)
    return pool


async def _list_page[R](
    pool: Pool,
    select_sql: str,
    where: list[str],
    params: dict[str, Any],
    req: PageRequest,
    *,
    id_col: str,
    id_param_sql: str,
    to_record: Callable[[tuple[Any, ...]], R],
    key: Callable[[R], tuple[datetime, str]],
    time_col: str = "created_at",
) -> Page[R]:
    """Keyset pagination, newest first. ``select_sql`` ends before WHERE; all parts are constants."""
    args = dict(params, limit=req.limit + 1)
    if req.cursor is not None:
        args["cursor_at"] = req.cursor.created_at
        args["cursor_id"] = req.cursor.id
    sql = list_sql(
        select_sql,
        where,
        id_col=id_col,
        id_param_sql=id_param_sql,
        time_col=time_col,
        with_cursor=req.cursor is not None,
    )
    async with pool.connection() as conn:
        cur = await conn.execute(sql.encode(), args)
        rows = await cur.fetchall()
    return page_of([to_record(tuple(r)) for r in rows], req.limit, key)


# --- pools --------------------------------------------------------------------------------------

_POOL_COLS = "id, name, description, definition, created_by, created_at, updated_at"


def _pool(r: tuple[Any, ...]) -> PoolRecord:
    return PoolRecord(r[0], r[1], r[2], PoolFilter.model_validate(r[3]), r[4], r[5], r[6])


def _pool_key(p: PoolRecord) -> tuple[datetime, str]:
    return p.created_at, str(p.id)


class PgPoolRepo:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def create(self, pool: PoolRecord) -> None:
        try:
            async with self._pool.connection() as conn:
                await conn.execute(
                    "INSERT INTO corvus.pools (id, name, description, definition, created_by,"
                    " created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (
                        pool.id,
                        pool.name,
                        pool.description,
                        Jsonb(pool.filter.model_dump(mode="json")),
                        pool.created_by,
                        pool.created_at,
                        pool.updated_at,
                    ),
                )
        except UniqueViolation as e:
            raise DuplicateError("pool name already exists") from e

    async def get(self, pool_id: UUID) -> PoolRecord | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"SELECT {_POOL_COLS} FROM corvus.pools WHERE id = %s".encode(), (pool_id,)
            )
            row = await cur.fetchone()
        return _pool(tuple(row)) if row else None

    async def list_page(self, req: PageRequest) -> Page[PoolRecord]:
        return await _list_page(
            self._pool,
            f"SELECT {_POOL_COLS} FROM corvus.pools",
            [],
            {},
            req,
            id_col="id",
            id_param_sql="%(cursor_id)s::uuid",
            to_record=_pool,
            key=_pool_key,
        )

    async def update(self, pool: PoolRecord) -> bool:
        try:
            async with self._pool.connection() as conn:
                cur = await conn.execute(
                    "UPDATE corvus.pools SET name = %s, description = %s, definition = %s,"
                    " updated_at = %s WHERE id = %s",
                    (
                        pool.name,
                        pool.description,
                        Jsonb(pool.filter.model_dump(mode="json")),
                        pool.updated_at,
                        pool.id,
                    ),
                )
                return bool(cur.rowcount > 0)
        except UniqueViolation as e:
            raise DuplicateError("pool name already exists") from e

    async def delete(self, pool_id: UUID) -> bool:
        async with self._pool.connection() as conn:
            cur = await conn.execute("DELETE FROM corvus.pools WHERE id = %s", (pool_id,))
            return bool(cur.rowcount > 0)


class PgPoolCatalog:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def preview(self, pool_filter: PoolFilter) -> RawPreview:
        q = build_pool_query(pool_filter)
        stmts = preview_statements(q)
        async with self._pool.connection() as conn, conn.transaction():
            await conn.execute(_READ_ONLY_RR)
            counts: dict[str, int] = {}
            for name in ("sessions", "segments", "players"):
                cur = await conn.execute(stmts[name].encode(), q.params)
                row = await cur.fetchone()
                counts[name] = int(row[0]) if row else 0
            cur = await conn.execute(stmts["labels"].encode(), q.params)
            labels = {str(r[0]): int(r[1]) for r in await cur.fetchall()}
            cur = await conn.execute(stmts["files"].encode(), q.params)
            files = [FileShare(int(r[0]), int(r[1]), int(r[2])) for r in await cur.fetchall()]
        return RawPreview(counts["sessions"], counts["segments"], counts["players"], labels, files)

    async def snapshot_material(self, pool_filter: PoolFilter) -> SnapshotMaterial:
        q = build_pool_query(pool_filter)
        stmts = snapshot_statements(q)
        async with self._pool.connection() as conn, conn.transaction():
            await conn.execute(_READ_ONLY_RR)
            cur = await conn.execute(stmts["sessions"].encode(), q.params)
            sessions = [bytes(r[0]) for r in await cur.fetchall()]
            cur = await conn.execute(stmts["files"].encode(), q.params)
            files = [LakeFileRef(int(r[0]), str(r[1])) for r in await cur.fetchall()]
        return SnapshotMaterial(sessions, files)


# --- snapshots ----------------------------------------------------------------------------------

_SNAP_COLS = (
    "id, pool_id, pool_name, manifest, sha256, session_count, file_count, created_by, created_at"
)


def _snapshot(r: tuple[Any, ...]) -> SnapshotRecord:
    return SnapshotRecord(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8])


# Sessions of a manifest with pseudonym, start, segments, applicable labels and the erased flag,
# ascending by id (hex order equals byte order), keyset on the id.
SESSIONS_PAGE_SQL = (
    "SELECT sid.session_id, s.pseudonym, s.started_at,"
    " COALESCE((SELECT jsonb_agg(jsonb_build_object('segment', g.segment,"
    "  'game_context', g.game_context) ORDER BY g.segment)"
    "  FROM corvus.segments g WHERE g.session_id = sid.session_id), '[]'::jsonb),"
    " COALESCE((SELECT jsonb_agg(jsonb_build_object('label', l.label, 'source', l.source,"
    "  'confidence', l.confidence, 'valid_from', l.valid_from, 'valid_to', l.valid_to)"
    "  ORDER BY l.created_at, l.id)"
    f"  FROM corvus.labels l WHERE {LABEL_MATCH}), '[]'::jsonb),"
    " EXISTS (SELECT 1 FROM corvus.erased_sessions x WHERE x.session_id = sid.session_id)"
    " FROM corvus.dataset_snapshots d,"
    " LATERAL (SELECT decode(e.value, 'hex') AS session_id"
    "  FROM jsonb_array_elements_text(d.manifest -> 'sessions') AS e(value)) sid"
    " LEFT JOIN corvus.sessions s ON s.session_id = sid.session_id"
    " WHERE d.id = %(snapshot_id)s"
    " AND (%(after)s::bytea IS NULL OR sid.session_id > %(after)s::bytea)"
    " AND (NOT %(exclude_erased)s OR NOT EXISTS"
    "  (SELECT 1 FROM corvus.erased_sessions x WHERE x.session_id = sid.session_id))"
    " ORDER BY sid.session_id LIMIT %(limit)s"
)

# Follows lake_files.replaced_by from every events file of the manifest to the live successor.
CURRENT_FILES_SQL = (
    "WITH RECURSIVE manifest_files AS ("
    " SELECT (e.value ->> 'id')::bigint AS id"
    " FROM corvus.dataset_snapshots d,"
    " jsonb_array_elements(d.manifest -> 'files' -> 'events') AS e(value)"
    " WHERE d.id = %(snapshot_id)s),"
    " chain AS ("
    " SELECT f.id AS origin, f.id, f.s3_key, f.replaced_by, f.deleted_at"
    " FROM corvus.lake_files f JOIN manifest_files m ON m.id = f.id"
    " UNION ALL"
    " SELECT c.origin, n.id, n.s3_key, n.replaced_by, n.deleted_at"
    " FROM chain c JOIN corvus.lake_files n ON n.id = c.replaced_by)"
    " SELECT origin, id, s3_key FROM chain WHERE deleted_at IS NULL ORDER BY origin, id"
)


def _snapshot_session(r: tuple[Any, ...]) -> SnapshotSession:
    return SnapshotSession(
        session_id=bytes(r[0]),
        pseudonym=bytes(r[1]) if r[1] is not None else None,
        started_at=r[2],
        segments=[SessionSegment(int(g["segment"]), str(g["game_context"])) for g in r[3]],
        labels=[
            SessionLabel(
                str(lb["label"]),
                str(lb["source"]),
                float(lb["confidence"]),
                _opt_dt(lb["valid_from"]),
                _opt_dt(lb["valid_to"]),
            )
            for lb in r[4]
        ],
        erased=bool(r[5]),
    )


def _opt_dt(value: Any) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class PgSnapshotRepo:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def insert(self, snapshot: SnapshotRecord) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "INSERT INTO corvus.dataset_snapshots (id, pool_id, pool_name, manifest, sha256,"
                " session_count, file_count, created_by, created_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    snapshot.id,
                    snapshot.pool_id,
                    snapshot.pool_name,
                    Jsonb(snapshot.manifest),
                    snapshot.sha256,
                    snapshot.session_count,
                    snapshot.file_count,
                    snapshot.created_by,
                    snapshot.created_at,
                ),
            )

    async def get(self, snapshot_id: UUID) -> SnapshotRecord | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"SELECT {_SNAP_COLS} FROM corvus.dataset_snapshots WHERE id = %s".encode(),
                (snapshot_id,),
            )
            row = await cur.fetchone()
        return _snapshot(tuple(row)) if row else None

    async def list_page(self, req: PageRequest, pool_id: UUID | None) -> Page[SnapshotRecord]:
        # The list view omits the (large) manifest: it is replaced by an empty object.
        where, params = (
            ([], {}) if pool_id is None else (["pool_id = %(pool_id)s"], {"pool_id": pool_id})
        )
        return await _list_page(
            self._pool,
            "SELECT id, pool_id, pool_name, '{}'::jsonb, sha256, session_count, file_count,"
            " created_by, created_at FROM corvus.dataset_snapshots",
            where,
            params,
            req,
            id_col="id",
            id_param_sql="%(cursor_id)s::uuid",
            to_record=_snapshot,
            key=lambda s: (s.created_at, str(s.id)),
        )

    async def sessions_page(
        self, snapshot_id: UUID, after: bytes | None, limit: int, exclude_erased: bool
    ) -> list[SnapshotSession]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                SESSIONS_PAGE_SQL.encode(),
                {
                    "snapshot_id": snapshot_id,
                    "after": after,
                    "limit": limit,
                    "exclude_erased": exclude_erased,
                },
            )
            return [_snapshot_session(tuple(r)) for r in await cur.fetchall()]

    async def current_files(self, snapshot_id: UUID) -> list[CurrentFile]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(CURRENT_FILES_SQL.encode(), {"snapshot_id": snapshot_id})
            return [CurrentFile(int(r[0]), int(r[1]), r[2]) for r in await cur.fetchall()]


# --- labels -------------------------------------------------------------------------------------

_LABEL_COLS = (
    "id, subject_type, pseudonym, session_id, label, source, confidence, valid_from, valid_to,"
    " note, created_by, created_at"
)


def _label(r: tuple[Any, ...]) -> LabelRecord:
    subject = bytes(r[2] if r[1] == "pseudonym" else r[3]).hex()
    return LabelRecord(
        r[0], cast(Literal["pseudonym", "session"], r[1]), subject, r[4], r[5], r[6], r[7], r[8],
        r[9], r[10], r[11],
    )  # fmt: skip


class PgLabelRepo:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def add_many(self, labels: list[LabelRecord]) -> None:
        rows = [
            (
                lb.id,
                lb.subject_type,
                bytes.fromhex(lb.subject) if lb.subject_type == "pseudonym" else None,
                bytes.fromhex(lb.subject) if lb.subject_type == "session" else None,
                lb.label,
                lb.source,
                lb.confidence,
                lb.valid_from,
                lb.valid_to,
                lb.note,
                lb.created_by,
                lb.created_at,
            )
            for lb in labels
        ]
        async with self._pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            await cur.executemany(
                "INSERT INTO corvus.labels (id, subject_type, pseudonym, session_id, label, source,"
                " confidence, valid_from, valid_to, note, created_by, created_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                rows,
            )

    async def list_page(
        self, req: PageRequest, subject_type: str | None, subject: bytes | None
    ) -> Page[LabelRecord]:
        where: list[str] = []
        params: dict[str, Any] = {}
        if subject_type == "pseudonym":
            where.append("subject_type = 'pseudonym'")
            if subject is not None:
                where.append("pseudonym = %(subject)s")
        elif subject_type == "session":
            where.append("subject_type = 'session'")
            if subject is not None:
                where.append("session_id = %(subject)s")
        if subject is not None:
            params["subject"] = subject
        return await _list_page(
            self._pool,
            f"SELECT {_LABEL_COLS} FROM corvus.labels",
            where,
            params,
            req,
            id_col="id",
            id_param_sql="%(cursor_id)s::uuid",
            to_record=_label,
            key=lambda lb: (lb.created_at, str(lb.id)),
        )


# --- players, erasure, blocklist ----------------------------------------------------------------


def _player(r: tuple[Any, ...]) -> PlayerInfo:
    return PlayerInfo(bytes(r[0]), r[1], r[2], r[3], r[4])


class PgPlayerRepo:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def get(self, pseudonym: bytes) -> PlayerInfo | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT pseudonym, key_id, first_seen, last_seen, erased_at"
                " FROM corvus.players WHERE pseudonym = %s",
                (pseudonym,),
            )
            row = await cur.fetchone()
        return _player(tuple(row)) if row else None


class PgErasureTx:
    def __init__(self, conn: psycopg.AsyncConnection[Any]) -> None:
        self._conn = conn

    async def lock_player(self, pseudonym: bytes) -> PlayerInfo | None:
        cur = await self._conn.execute(
            "SELECT pseudonym, key_id, first_seen, last_seen, erased_at"
            " FROM corvus.players WHERE pseudonym = %s FOR UPDATE",
            (pseudonym,),
        )
        row = await cur.fetchone()
        return _player(tuple(row)) if row else None

    async def delete_player_key(self, pseudonym: bytes) -> bool:
        cur = await self._conn.execute(
            "DELETE FROM corvus.player_keys WHERE pseudonym = %s", (pseudonym,)
        )
        return cur.rowcount > 0

    async def delete_scores(self, pseudonym: bytes) -> int:
        latest = await self._conn.execute(DELETE_SCORES_LATEST, (pseudonym,))
        history = await self._conn.execute(DELETE_SCORE_HISTORY, (pseudonym,))
        return int(latest.rowcount) + int(history.rowcount)

    async def mark_erased(self, pseudonym: bytes, at: datetime) -> None:
        await self._conn.execute(
            "UPDATE corvus.players SET erased_at = COALESCE(erased_at, %s) WHERE pseudonym = %s",
            (at, pseudonym),
        )

    async def block(self, entry: BlockEntry) -> None:
        await _put_block(self._conn, entry)

    async def record_request(self, request: ErasureRequest) -> None:
        await self._conn.execute(
            "INSERT INTO corvus.erasure_requests (id, pseudonym, requested_by, requested_at, status,"
            " player_known, key_deleted, compaction_pending) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                request.id,
                request.pseudonym,
                request.requested_by,
                request.requested_at,
                request.status,
                request.player_known,
                request.key_deleted,
                request.compaction_pending,
            ),
        )


_ERASURE_COLS = (
    "id, pseudonym, requested_by, requested_at, status, player_known, key_deleted,"
    " compaction_pending, compacted_at"
)


def _erasure(r: tuple[Any, ...]) -> ErasureRequest:
    return ErasureRequest(r[0], bytes(r[1]), r[2], r[3], r[4], r[5], r[6], r[7], r[8])


class PgErasureStore:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[ErasureTx]:
        async with self._pool.connection() as conn, conn.transaction():
            yield PgErasureTx(conn)

    async def get_request(self, request_id: UUID) -> ErasureRequest | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"SELECT {_ERASURE_COLS} FROM corvus.erasure_requests WHERE id = %s".encode(),
                (request_id,),
            )
            row = await cur.fetchone()
        return _erasure(tuple(row)) if row else None

    async def list_requests(self, req: PageRequest, status: str | None) -> Page[ErasureRequest]:
        where, params = (
            ([], {}) if status is None else (["status = %(status)s"], {"status": status})
        )
        return await _list_page(
            self._pool,
            f"SELECT {_ERASURE_COLS} FROM corvus.erasure_requests",
            where,
            params,
            req,
            id_col="id",
            id_param_sql="%(cursor_id)s::uuid",
            to_record=_erasure,
            key=lambda e: (e.requested_at, str(e.id)),
            time_col="requested_at",
        )


async def _put_block(conn: psycopg.AsyncConnection[Any], e: BlockEntry) -> None:
    await conn.execute(
        "INSERT INTO corvus.blocklist (pseudonym, kind, source, reason, created_by, created_at)"
        " VALUES (%s, %s, %s, %s, %s, %s)"
        " ON CONFLICT (pseudonym) DO UPDATE SET kind = EXCLUDED.kind, source = EXCLUDED.source,"
        " reason = EXCLUDED.reason, created_by = EXCLUDED.created_by, created_at = EXCLUDED.created_at",
        (e.pseudonym, e.kind, e.source, e.reason, e.created_by, e.created_at),
    )


def _block(r: tuple[Any, ...]) -> BlockEntry:
    return BlockEntry(bytes(r[0]), r[1], r[2], r[3], r[4], r[5])


_BLOCK_COLS = "pseudonym, kind, source, reason, created_by, created_at"


class PgBlocklistRepo:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def get(self, pseudonym: bytes) -> BlockEntry | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"SELECT {_BLOCK_COLS} FROM corvus.blocklist WHERE pseudonym = %s".encode(),
                (pseudonym,),
            )
            row = await cur.fetchone()
        return _block(tuple(row)) if row else None

    async def list_page(self, req: PageRequest, kind: str | None) -> Page[BlockEntry]:
        where, params = ([], {}) if kind is None else (["kind = %(kind)s"], {"kind": kind})
        return await _list_page(
            self._pool,
            f"SELECT {_BLOCK_COLS} FROM corvus.blocklist",
            where,
            params,
            req,
            id_col="pseudonym",
            id_param_sql="decode(%(cursor_id)s, 'hex')",
            to_record=_block,
            key=lambda b: (b.created_at, b.pseudonym.hex()),
        )

    async def put(self, entry: BlockEntry) -> None:
        async with self._pool.connection() as conn:
            await _put_block(conn, entry)

    async def delete(self, pseudonym: bytes) -> bool:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "DELETE FROM corvus.blocklist WHERE pseudonym = %s", (pseudonym,)
            )
            return bool(cur.rowcount > 0)

    async def all_pseudonyms(self) -> tuple[list[bytes], list[bytes]]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT pseudonym, kind FROM corvus.blocklist ORDER BY pseudonym"
            )
            rows = await cur.fetchall()
        blocked = [bytes(r[0]) for r in rows if r[1] == "block"]
        allowed = [bytes(r[0]) for r in rows if r[1] == "allow"]
        return blocked, allowed


# --- models -------------------------------------------------------------------------------------

_MODEL_COLS = (
    "id, name, version, candidate_type, game_context, domain, snapshot_id, metrics, onnx_key,"
    " stage, created_by, created_at"
)


def _model(r: tuple[Any, ...]) -> ModelRecord:
    return ModelRecord(*r)


def _transition(r: tuple[Any, ...]) -> TransitionRecord:
    return TransitionRecord(*r)


class PgModelRepo:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def create(self, model: ModelRecord) -> None:
        try:
            async with self._pool.connection() as conn:
                await conn.execute(
                    f"INSERT INTO corvus.models ({_MODEL_COLS})"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)".encode(),
                    (
                        model.id,
                        model.name,
                        model.version,
                        model.candidate_type,
                        model.game_context,
                        model.domain,
                        model.snapshot_id,
                        Jsonb(model.metrics),
                        model.onnx_key,
                        model.stage,
                        model.created_by,
                        model.created_at,
                    ),
                )
        except UniqueViolation as e:
            raise DuplicateError("model name and version already exist") from e

    async def get(self, model_id: UUID) -> ModelRecord | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"SELECT {_MODEL_COLS} FROM corvus.models WHERE id = %s".encode(), (model_id,)
            )
            row = await cur.fetchone()
        return _model(tuple(row)) if row else None

    async def get_by_name_version(self, name: str, version: str) -> ModelRecord | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"SELECT {_MODEL_COLS} FROM corvus.models WHERE name = %s AND version = %s".encode(),
                (name, version),
            )
            row = await cur.fetchone()
        return _model(tuple(row)) if row else None

    async def list_page(self, req: PageRequest, flt: ModelFilter) -> Page[ModelRecord]:
        where, params = model_conditions(flt)
        return await _list_page(
            self._pool,
            f"SELECT {_MODEL_COLS} FROM corvus.models",
            where,
            params,
            req,
            id_col="id",
            id_param_sql="%(cursor_id)s::uuid",
            to_record=_model,
            key=lambda m: (m.created_at, str(m.id)),
        )

    async def enforced_in_scope(
        self, snapshot_id: UUID, game_context: str, exclude: UUID
    ) -> list[ModelRecord]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"SELECT {_MODEL_COLS} FROM corvus.models WHERE stage = 'enforce'"
                " AND snapshot_id = %s AND game_context = %s AND id <> %s".encode(),
                (snapshot_id, game_context, exclude),
            )
            return [_model(tuple(r)) for r in await cur.fetchall()]

    async def apply_transition(
        self, model_id: UUID, expected_stage: str, transitions: list[TransitionRecord]
    ) -> ModelRecord:
        async with self._pool.connection() as conn, conn.transaction():
            for t in transitions:
                cur = await conn.execute(
                    "UPDATE corvus.models SET stage = %s WHERE id = %s AND stage = %s",
                    (t.to_stage, t.model_id, t.from_stage),
                )
                if cur.rowcount != 1:
                    raise StaleError(str(t.model_id))
                await conn.execute(
                    "INSERT INTO corvus.model_transitions (id, model_id, from_stage, to_stage, actor,"
                    " reason, gate, at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        t.id,
                        t.model_id,
                        t.from_stage,
                        t.to_stage,
                        t.actor,
                        t.reason,
                        Jsonb(t.gate) if t.gate is not None else None,
                        t.at,
                    ),
                )
            cur = await conn.execute(
                f"SELECT {_MODEL_COLS} FROM corvus.models WHERE id = %s".encode(), (model_id,)
            )
            row = await cur.fetchone()
        assert row is not None
        return _model(tuple(row))

    async def transitions(self, model_id: UUID) -> list[TransitionRecord]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT id, model_id, from_stage, to_stage, actor, reason, gate, at"
                " FROM corvus.model_transitions WHERE model_id = %s ORDER BY at, id",
                (model_id,),
            )
            return [_transition(tuple(r)) for r in await cur.fetchall()]


# --- jobs ---------------------------------------------------------------------------------------

_JOB_COLS = (
    "id, type, snapshot_id, params, status, runner, runner_ref, message, created_by, created_at,"
    " updated_at, finished_at, result"
)


def _job(r: tuple[Any, ...]) -> JobRecord:
    return JobRecord(*r)


class PgJobRepo:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def create(self, job: JobRecord) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                f"INSERT INTO corvus.jobs ({_JOB_COLS})"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)".encode(),
                (
                    job.id,
                    job.type,
                    job.snapshot_id,
                    Jsonb(job.params),
                    job.status,
                    job.runner,
                    job.runner_ref,
                    job.message,
                    job.created_by,
                    job.created_at,
                    job.updated_at,
                    job.finished_at,
                    Jsonb(job.result) if job.result is not None else None,
                ),
            )

    async def get(self, job_id: UUID) -> JobRecord | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"SELECT {_JOB_COLS} FROM corvus.jobs WHERE id = %s".encode(), (job_id,)
            )
            row = await cur.fetchone()
        return _job(tuple(row)) if row else None

    async def list_page(self, req: PageRequest, status: str | None) -> Page[JobRecord]:
        where, params = (
            ([], {}) if status is None else (["status = %(status)s"], {"status": status})
        )
        return await _list_page(
            self._pool,
            f"SELECT {_JOB_COLS} FROM corvus.jobs",
            where,
            params,
            req,
            id_col="id",
            id_param_sql="%(cursor_id)s::uuid",
            to_record=_job,
            key=lambda j: (j.created_at, str(j.id)),
        )

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
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"{UPDATE_JOB} WHERE id = %s RETURNING {_JOB_COLS}".encode(),
                (
                    status,
                    runner_ref,
                    message,
                    now,
                    finished,
                    now,
                    Jsonb(result) if result is not None else None,
                    job_id,
                ),
            )
            row = await cur.fetchone()
        assert row is not None
        return _job(tuple(row))


class PgScoreRepo:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def latest(self, pseudonym: bytes) -> list[ScoreLatestRecord]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT game_context, domain, model_name, model_version, stage, score, smoothed,"
                " windows, updated_at FROM corvus.scores_latest WHERE pseudonym = %s"
                " ORDER BY game_context, domain, model_name",
                (pseudonym,),
            )
            return [ScoreLatestRecord(*r) for r in await cur.fetchall()]

    async def history(
        self, pseudonym: bytes, game_context: str | None, limit: int
    ) -> list[ScoreHistoryRecord]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT id, session_id, segment, game_context, domain, model_name, model_version,"
                " stage, score, smoothed, windows, window_start_offset_nanos,"
                " window_end_offset_nanos, scored_at FROM corvus.score_history"
                " WHERE pseudonym = %s AND (%s::text IS NULL OR game_context = %s)"
                " ORDER BY scored_at DESC, id DESC LIMIT %s",
                (pseudonym, game_context, game_context, limit),
            )
            return [ScoreHistoryRecord(r[0], bytes(r[1]), *r[2:]) for r in await cur.fetchall()]


class PgSummaryRepo:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def summary(self, recent: int) -> Summary:
        async with self._pool.connection() as conn, conn.transaction():
            await conn.execute(_READ_ONLY_RR)
            counts: dict[str, int] = {}
            for name, sql in SUMMARY_COUNTS.items():
                cur = await conn.execute(sql.encode())
                row = await cur.fetchone()
                counts[name] = int(row[0]) if row else 0
            groups: dict[str, dict[str, int]] = {}
            for name, sql in SUMMARY_GROUPS.items():
                cur = await conn.execute(sql.encode())
                groups[name] = {str(r[0]): int(r[1]) for r in await cur.fetchall()}
            cur = await conn.execute(SUMMARY_RECENT_JOBS.encode(), (recent,))
            jobs = [JobBrief(*r) for r in await cur.fetchall()]
            cur = await conn.execute(SUMMARY_RECENT_ERASURES.encode(), (recent,))
            erasures = [
                ErasureBrief(r[0], bytes(r[1]), r[2], r[3], r[4]) for r in await cur.fetchall()
            ]
        return Summary(
            pools=counts["pools"],
            snapshots=counts["snapshots"],
            jobs_by_status=groups["jobs_by_status"],
            models_by_stage=groups["models_by_stage"],
            labels_by_label=groups["labels_by_label"],
            pending_erasures=counts["pending_erasures"],
            recent_jobs=jobs,
            recent_erasures=erasures,
        )


class PgDatabase:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def ping(self) -> None:
        async with self._pool.connection() as conn:
            await conn.execute("SELECT 1")


async def open_repos(dsn: str, max_size: int) -> Repos:
    pool = await open_pool(dsn, max_size)
    return Repos(
        pools=PgPoolRepo(pool),
        catalog=PgPoolCatalog(pool),
        snapshots=PgSnapshotRepo(pool),
        labels=PgLabelRepo(pool),
        players=PgPlayerRepo(pool),
        erasure=PgErasureStore(pool),
        blocklist=PgBlocklistRepo(pool),
        models=PgModelRepo(pool),
        jobs=PgJobRepo(pool),
        scores=PgScoreRepo(pool),
        summary=PgSummaryRepo(pool),
        db=PgDatabase(pool),
        closers=[pool.close],
    )
