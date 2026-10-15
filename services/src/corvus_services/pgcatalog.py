"""PostgreSQL catalog adapter (psycopg 3 async pool)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from psycopg_pool import AsyncConnectionPool

from .ports import (
    Counters,
    FileRecord,
    SegmentData,
    SessionData,
    SessionInfo,
    WrappedDek,
)


class PgCatalog:
    def __init__(self, pool: AsyncConnectionPool[Any]) -> None:
        self._pool = pool

    @classmethod
    async def open(cls, dsn: str, max_size: int) -> PgCatalog:
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

    async def start_session(
        self, data: SessionData, candidate_dek: WrappedDek
    ) -> tuple[SessionInfo, WrappedDek | None]:
        async with self._pool.connection() as conn, conn.transaction():
            cur = await conn.execute(
                "INSERT INTO corvus.players (pseudonym, key_id, first_seen, last_seen)"
                " VALUES (%(p)s, %(k)s, %(t)s, %(t)s)"
                " ON CONFLICT (pseudonym) DO UPDATE SET"
                "  first_seen = LEAST(corvus.players.first_seen, EXCLUDED.first_seen),"
                "  last_seen = GREATEST(corvus.players.last_seen, EXCLUDED.last_seen)"
                " RETURNING erased_at",
                {"p": data.pseudonym, "k": data.pseudonym_key_id, "t": data.started_at},
            )
            row = await cur.fetchone()
            assert row is not None
            erased = row[0] is not None
            await conn.execute(
                "INSERT INTO corvus.sessions (session_id, pseudonym, proxy_id, client_protocol,"
                " observed_protocol, plugin_version, started_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (session_id) DO UPDATE SET"
                "  pseudonym = EXCLUDED.pseudonym, orphan = false, proxy_id = EXCLUDED.proxy_id,"
                "  client_protocol = EXCLUDED.client_protocol,"
                "  observed_protocol = EXCLUDED.observed_protocol,"
                "  plugin_version = EXCLUDED.plugin_version, started_at = EXCLUDED.started_at"
                " WHERE corvus.sessions.orphan",
                (
                    data.session_id,
                    data.pseudonym,
                    data.proxy_id,
                    data.client_protocol,
                    data.observed_protocol,
                    data.plugin_version,
                    data.started_at,
                ),
            )
            stored: WrappedDek | None = None
            if not erased:
                await conn.execute(
                    "INSERT INTO corvus.player_keys (pseudonym, dek_wrapped, kek_id)"
                    " VALUES (%s, %s, %s) ON CONFLICT (pseudonym) DO NOTHING",
                    (data.pseudonym, candidate_dek.wrapped, candidate_dek.kek_id),
                )
                cur = await conn.execute(
                    "SELECT dek_wrapped, kek_id FROM corvus.player_keys WHERE pseudonym = %s",
                    (data.pseudonym,),
                )
                key_row = await cur.fetchone()
                if key_row is not None:
                    stored = WrappedDek(bytes(key_row[0]), key_row[1])
            cur = await conn.execute(
                "SELECT started_at FROM corvus.sessions WHERE session_id = %s", (data.session_id,)
            )
            srow = await cur.fetchone()
            assert srow is not None
        return SessionInfo(data.session_id, data.pseudonym, srow[0], erased), stored

    async def create_orphan_session(self, session_id: bytes, started_at: datetime) -> SessionInfo:
        async with self._pool.connection() as conn:
            await conn.execute(
                "INSERT INTO corvus.sessions (session_id, pseudonym, orphan, proxy_id,"
                " client_protocol, observed_protocol, plugin_version, started_at)"
                " VALUES (%s, NULL, true, '', 0, 0, '', %s) ON CONFLICT (session_id) DO NOTHING",
                (session_id, started_at),
            )
        info = await self.get_session(session_id)
        assert info is not None
        return info

    async def start_segment(self, data: SegmentData) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "INSERT INTO corvus.segments (session_id, segment, backend_server, game_context,"
                " client_brand, started_at) VALUES (%s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (session_id, segment) DO NOTHING",
                (
                    data.session_id,
                    data.segment,
                    data.backend_server,
                    data.game_context,
                    data.client_brand,
                    data.started_at,
                ),
            )

    async def get_session(self, session_id: bytes) -> SessionInfo | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT s.pseudonym, s.started_at, COALESCE(p.erased_at IS NOT NULL, false)"
                " FROM corvus.sessions s LEFT JOIN corvus.players p USING (pseudonym)"
                " WHERE s.session_id = %s",
                (session_id,),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return SessionInfo(
            session_id, None if row[0] is None else bytes(row[0]), row[1], bool(row[2])
        )

    async def get_dek(self, pseudonym: bytes) -> WrappedDek | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT k.dek_wrapped, k.kek_id FROM corvus.player_keys k"
                " JOIN corvus.players p USING (pseudonym)"
                " WHERE k.pseudonym = %s AND p.erased_at IS NULL",
                (pseudonym,),
            )
            row = await cur.fetchone()
        return None if row is None else WrappedDek(bytes(row[0]), row[1])

    async def apply_counters(
        self, session_id: bytes, msg_id: str, counters: Counters, ended_at: datetime | None
    ) -> bool:
        async with self._pool.connection() as conn, conn.transaction():
            cur = await conn.execute(
                "INSERT INTO corvus.session_batches (session_id, msg_id) VALUES (%s, %s)"
                " ON CONFLICT DO NOTHING",
                (session_id, msg_id),
            )
            if cur.rowcount == 0:
                return False
            await conn.execute(
                "UPDATE corvus.sessions SET"
                " dropped_queue_full = dropped_queue_full + %s,"
                " dropped_privacy = dropped_privacy + %s,"
                " listener_errors = listener_errors + %s,"
                " dropped_transport = dropped_transport + %s,"
                " ended_at = COALESCE(%s, ended_at)"
                " WHERE session_id = %s",
                (
                    counters.dropped_queue_full,
                    counters.dropped_privacy,
                    counters.listener_errors,
                    counters.dropped_transport,
                    ended_at,
                    session_id,
                ),
            )
        return True

    async def register_file(self, record: FileRecord, session_ids: frozenset[bytes]) -> int:
        async with self._pool.connection() as conn, conn.transaction():
            cur = await conn.execute(
                "INSERT INTO corvus.lake_files (s3_key, table_name, date, row_count, size_bytes,"
                " schema_version) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    record.s3_key,
                    record.table_name,
                    record.date,
                    record.row_count,
                    record.size_bytes,
                    record.schema_version,
                ),
            )
            row = await cur.fetchone()
            assert row is not None
            file_id = int(row[0])
            await conn.execute(
                "INSERT INTO corvus.lake_file_sessions (file_id, session_id)"
                " SELECT %s, unnest(%s::bytea[]) ON CONFLICT DO NOTHING",
                (file_id, sorted(session_ids)),
            )
        return file_id

    # ----- housekeeping queries ---------------------------------------------------------------

    async def known_keys(self, keys: list[str]) -> set[str]:
        """Subset of ``keys`` that has a ``lake_files`` row."""
        if not keys:
            return set()
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT s3_key FROM corvus.lake_files WHERE s3_key = ANY(%s)", (keys,)
            )
            return {r[0] for r in await cur.fetchall()}

    async def live_raw_files_before(self, day: date) -> list[tuple[int, str]]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT id, s3_key FROM corvus.lake_files"
                " WHERE table_name = 'raw' AND date < %s AND deleted_at IS NULL ORDER BY id",
                (day,),
            )
            return [(int(r[0]), r[1]) for r in await cur.fetchall()]

    async def mark_deleted(self, file_id: int, at: datetime) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "UPDATE corvus.lake_files SET deleted_at = %s WHERE id = %s", (at, file_id)
            )

    async def prune_batches(self, before: datetime) -> int:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "DELETE FROM corvus.session_batches WHERE received_at < %s", (before,)
            )
            return int(cur.rowcount)

    async def prune_score_history(self, before: datetime) -> int:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "DELETE FROM corvus.score_history WHERE scored_at < %s", (before,)
            )
            return int(cur.rowcount)
