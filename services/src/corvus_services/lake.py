"""Lake sink behind the ``LakeSink`` port: Parquet files, catalogued in PostgreSQL.

Layout and catalog rows: docs/reference/data-lake.md sections 2 and 5.
"""

from __future__ import annotations

import asyncio
import functools
from concurrent.futures import Executor

from .buffer import TableBuffer, encode_parquet
from .clock import Clock, new_ulid
from .ports import Catalog, Committed, FileRecord, ObjectStore
from .schema import SCHEMA_VERSION


class ParquetSink:
    """staging -> final copy -> ``lake_files`` / ``lake_file_sessions`` in one transaction."""

    def __init__(
        self, store: ObjectStore, catalog: Catalog, executor: Executor, clock: Clock
    ) -> None:
        self._store = store
        self._catalog = catalog
        self._executor = executor
        self._clock = clock

    async def commit(self, buf: TableBuffer) -> Committed:
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(self._executor, encode_parquet, buf)
        ulid = new_ulid(self._clock())
        staging = f"_staging/{buf.table}/{ulid}.parquet"
        final = f"lake/{buf.table}/date={buf.day.isoformat()}/part-{ulid}.parquet"
        await self._store.put(staging, data)
        await self._store.copy(staging, final)
        await self._catalog.register_file(
            FileRecord(final, buf.table, buf.day, buf.rows, len(data), SCHEMA_VERSION),
            frozenset(buf.session_ids),
        )
        return Committed(len(data), functools.partial(self._store.delete, staging))

    async def ping(self) -> None:
        await self._store.ping()
