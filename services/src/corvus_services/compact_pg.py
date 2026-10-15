"""PostgreSQL adapter of the compaction catalog (psycopg 3 async pool). Constant SQL."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from .compact import LiveFile, PurgeItem
from .ports import FileRecord

_LOCK_NAMESPACE = 0x434F << 40
"""Advisory-lock keys of compaction: namespace plus file id (migrations use another key)."""


def lock_key(file_id: int) -> int:
    return _LOCK_NAMESPACE | file_id


class PgCompactCatalog:
    def __init__(self, pool: AsyncConnectionPool[Any]) -> None:
        self._pool = pool

    @classmethod
    async def open(cls, dsn: str, max_size: int) -> PgCompactCatalog:
        # One connection is held per file lock; the rest of the work needs another one.
        pool: AsyncConnectionPool[Any] = AsyncConnectionPool(
            dsn, min_size=1, max_size=max(max_size, 2), open=False
        )
        await pool.open(wait=True, timeout=30)
        return cls(pool)

    async def close(self) -> None:
        await self._pool.close()

    async def pending_request_ids(self, requested_before: datetime) -> list[object]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT id FROM corvus.erasure_requests"
                " WHERE compaction_pending AND requested_at <= %s ORDER BY requested_at, id",
                (requested_before,),
            )
            return [r[0] for r in await cur.fetchall()]

    async def affected_files(self, request_ids: list[object]) -> list[LiveFile]:
        ids: list[UUID] = [i for i in request_ids if isinstance(i, UUID)]
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT DISTINCT f.id, f.s3_key, f.table_name, f.date, f.schema_version"
                " FROM corvus.erasure_requests r"
                " JOIN corvus.sessions s ON s.pseudonym = r.pseudonym"
                " JOIN corvus.lake_file_sessions fs ON fs.session_id = s.session_id"
                " JOIN corvus.lake_files f ON f.id = fs.file_id"
                " WHERE r.id = ANY(%s) AND f.deleted_at IS NULL ORDER BY f.id",
                (ids,),
            )
            return [LiveFile(int(r[0]), r[1], r[2], r[3], int(r[4])) for r in await cur.fetchall()]

    @asynccontextmanager
    async def try_lock_file(self, file_id: int) -> AsyncIterator[bool]:
        async with self._pool.connection() as conn:
            cur = await conn.execute("SELECT pg_try_advisory_lock(%s)", (lock_key(file_id),))
            row = await cur.fetchone()
            await conn.commit()  # do not sit idle in a transaction while the file is processed
            got = bool(row and row[0])
            try:
                yield got
            finally:
                if got:
                    await conn.execute("SELECT pg_advisory_unlock(%s)", (lock_key(file_id),))
                    await conn.commit()

    async def locked_state(self, file_id: int) -> list[bytes] | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT 1 FROM corvus.lake_files WHERE id = %s AND deleted_at IS NULL", (file_id,)
            )
            if await cur.fetchone() is None:
                return None
            cur = await conn.execute(
                "SELECT fs.session_id FROM corvus.lake_file_sessions fs"
                " JOIN corvus.erased_sessions e ON e.session_id = fs.session_id"
                " WHERE fs.file_id = %s ORDER BY fs.session_id",
                (file_id,),
            )
            return [bytes(r[0]) for r in await cur.fetchall()]

    async def detach_sessions(self, file_id: int, sessions: list[bytes]) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "DELETE FROM corvus.lake_file_sessions WHERE file_id = %s AND session_id = ANY(%s)",
                (file_id, sessions),
            )

    async def replace_file(
        self, old_id: int, new: FileRecord | None, erased: list[bytes], now: datetime
    ) -> int | None:
        async with self._pool.connection() as conn, conn.transaction():
            new_id: int | None = None
            if new is not None:
                cur = await conn.execute(
                    "INSERT INTO corvus.lake_files (s3_key, table_name, date, row_count,"
                    " size_bytes, schema_version) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                    (
                        new.s3_key,
                        new.table_name,
                        new.date,
                        new.row_count,
                        new.size_bytes,
                        new.schema_version,
                    ),
                )
                row = await cur.fetchone()
                assert row is not None
                new_id = int(row[0])
                await conn.execute(
                    "INSERT INTO corvus.lake_file_sessions (file_id, session_id)"
                    " SELECT %s, session_id FROM corvus.lake_file_sessions"
                    " WHERE file_id = %s AND session_id <> ALL(%s)",
                    (new_id, old_id, erased),
                )
            cur = await conn.execute(
                "UPDATE corvus.lake_files SET deleted_at = %s, replaced_by = %s,"
                " purge_pending = true WHERE id = %s AND deleted_at IS NULL",
                (now, new_id, old_id),
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"file {old_id} is no longer live")  # rolls back the insert
        return new_id

    async def purge_pending(self) -> list[PurgeItem]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT id, s3_key FROM corvus.lake_files WHERE purge_pending ORDER BY id"
            )
            return [PurgeItem(int(r[0]), r[1]) for r in await cur.fetchall()]

    async def mark_purged(self, file_id: int) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "UPDATE corvus.lake_files SET purge_pending = false WHERE id = %s", (file_id,)
            )

    async def complete_requests(self, request_ids: list[object], now: datetime) -> None:
        ids: list[UUID] = [i for i in request_ids if isinstance(i, UUID)]
        async with self._pool.connection() as conn:
            await conn.execute(
                "UPDATE corvus.erasure_requests SET compaction_pending = false, compacted_at = %s"
                " WHERE id = ANY(%s) AND compaction_pending",
                (now, ids),
            )
