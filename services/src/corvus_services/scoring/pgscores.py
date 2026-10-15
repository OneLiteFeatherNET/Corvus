"""PostgreSQL adapter of the scoring catalog port (psycopg 3 async pool)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from psycopg_pool import AsyncConnectionPool

from .ports import ModelEntry, ScoreRecord, SessionLookup

_UPSERT_LATEST = (
    "INSERT INTO corvus.scores_latest (pseudonym, game_context, domain, model_name, model_version,"
    " stage, score, smoothed, windows, session_id, segment, updated_at)"
    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
    " ON CONFLICT (pseudonym, game_context, domain, model_name) DO UPDATE SET"
    "  model_version = EXCLUDED.model_version, stage = EXCLUDED.stage, score = EXCLUDED.score,"
    "  smoothed = EXCLUDED.smoothed, windows = EXCLUDED.windows,"
    "  session_id = EXCLUDED.session_id, segment = EXCLUDED.segment,"
    "  updated_at = EXCLUDED.updated_at"
    " WHERE EXCLUDED.updated_at >= corvus.scores_latest.updated_at"
)
_INSERT_HISTORY = (
    "INSERT INTO corvus.score_history (pseudonym, session_id, segment, game_context, domain,"
    " model_name, model_version, stage, score, smoothed, windows, window_start_offset_nanos,"
    " window_end_offset_nanos, scored_at)"
    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)


def latest_params(r: ScoreRecord) -> tuple[Any, ...]:
    return (
        r.pseudonym, r.game_context, r.domain, r.model_name, r.model_version, r.stage, r.score,
        r.smoothed, r.windows, r.session_id, r.segment, r.scored_at,
    )  # fmt: skip


def history_params(r: ScoreRecord) -> tuple[Any, ...]:
    return (
        r.pseudonym, r.session_id, r.segment, r.game_context, r.domain, r.model_name,
        r.model_version, r.stage, r.score, r.smoothed, r.windows, r.window_start_offset_nanos,
        r.window_end_offset_nanos, r.scored_at,
    )  # fmt: skip


class PgScoreCatalog:
    def __init__(self, pool: AsyncConnectionPool[Any]) -> None:
        self._pool = pool

    @classmethod
    async def open(cls, dsn: str, max_size: int) -> PgScoreCatalog:
        pool: AsyncConnectionPool[Any] = AsyncConnectionPool(
            dsn, min_size=1, max_size=max_size, open=False
        )
        await pool.open(wait=True, timeout=30)
        return cls(pool)

    async def close(self) -> None:
        await self._pool.close()

    async def ping(self) -> None:
        async with self._pool.connection() as conn:
            await conn.execute("SELECT 1")

    async def lookup_session(self, session_id: bytes) -> SessionLookup | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT s.pseudonym, p.key_id, s.observed_protocol FROM corvus.sessions s"
                " JOIN corvus.players p USING (pseudonym)"
                " WHERE s.session_id = %s AND NOT s.orphan AND p.erased_at IS NULL",
                (session_id,),
            )
            row = await cur.fetchone()
            if row is None:
                return None
            cur = await conn.execute(
                "SELECT segment, game_context FROM corvus.segments WHERE session_id = %s",
                (session_id,),
            )
            segments = {int(r[0]): str(r[1]) for r in await cur.fetchall()}
        return SessionLookup(session_id, bytes(row[0]), row[1], int(row[2]), segments)

    async def active_models(self) -> list[ModelEntry]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT id::text, name, version, candidate_type, game_context, domain, stage,"
                " onnx_key FROM corvus.models WHERE stage IN ('shadow', 'canary', 'enforce')"
                " ORDER BY created_at, id"
            )
            return [ModelEntry(*r) for r in await cur.fetchall()]

    async def write_scores(
        self, latest: Sequence[ScoreRecord], history: Sequence[ScoreRecord]
    ) -> None:
        async with self._pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            if latest:
                await cur.executemany(_UPSERT_LATEST, [latest_params(r) for r in latest])
            if history:
                await cur.executemany(_INSERT_HISTORY, [history_params(r) for r in history])
