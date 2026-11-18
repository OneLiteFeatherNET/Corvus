"""Lake sinks behind the ``LakeSink`` port: Parquet files (default) or Iceberg tables.

``open_lake_sink`` picks the implementation from ``CORVUS_LAKE_FORMAT``. The Parquet sink is the
layout of docs/reference/data-lake.md section 2 and keeps its catalog rows (``lake_files``); the
Iceberg sink (``iceberg_sink``) keeps no such rows, session membership comes from Iceberg filters.
"""

from __future__ import annotations

import asyncio
import functools
from concurrent.futures import Executor

from .buffer import TableBuffer, encode_parquet
from .clock import Clock, new_ulid
from .config import Settings
from .iceberg_sink import IcebergSink
from .ports import Catalog, Committed, FileRecord, LakeSink, ObjectStore
from .schema import PARQUET_LAYOUT, SCHEMA_VERSION, Layout


class ParquetSink:
    """staging -> final copy -> ``lake_files`` / ``lake_file_sessions`` in one transaction."""

    layout: Layout = PARQUET_LAYOUT

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


async def open_lake_sink(
    settings: Settings,
    store: ObjectStore,
    catalog: Catalog,
    executor: Executor,
    clock: Clock,
) -> LakeSink:
    """The sink of ``CORVUS_LAKE_FORMAT``. Iceberg: connects and creates the tables if missing."""
    if settings.lake_format == "parquet":
        return ParquetSink(store, catalog, executor, clock)
    sink = IcebergSink.from_settings(settings)
    await asyncio.to_thread(sink.bootstrap)
    return sink
