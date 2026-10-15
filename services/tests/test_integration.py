"""End-to-end against the dev compose stack (NATS, S3, PostgreSQL). Skipped by default.

cd deploy/dev && docker compose up -d --wait nats s3 postgres
cd services && CORVUS_PG_DSN=... CORVUS_S3_... uv run pytest -m integration
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg
import pytest

from corvus_services.clock import system_clock
from corvus_services.config import Settings
from corvus_services.crypto import Keyring
from corvus_services.migrate import PgExecutor, load_migrations, run
from corvus_services.nats_source import NatsSource
from corvus_services.pgcatalog import PgCatalog
from corvus_services.pipeline import IngestPipeline
from corvus_services.s3store import S3Store
from helpers import KEK, FakeMessage, b64, batch_env, session_start_env

pytestmark = pytest.mark.integration


def _settings() -> Settings:
    return Settings(
        kek=b64(KEK),
        workers=1,
        s3_endpoint_url=os.environ.get("CORVUS_S3_ENDPOINT_URL", "http://localhost:9000"),
    )


async def test_ingest_cycle_writes_file_catalog_row_and_acks():
    s = _settings()
    migrations = load_migrations(Path(__file__).resolve().parents[1] / "migrations")
    with psycopg.connect(s.pg_dsn, autocommit=True) as conn:
        run(migrations, PgExecutor(conn))

    catalog = await PgCatalog.open(s.pg_dsn, 2)
    store = S3Store.from_settings(s)
    log: list[str] = []
    executor = ThreadPoolExecutor(1)
    sid = os.urandom(16)
    pseudonym = os.urandom(32)
    try:
        pipeline = IngestPipeline(s, catalog, store, Keyring(KEK, "it"), executor, system_clock)
        await pipeline.handle(
            FakeMessage(session_start_env(sid, pseudonym).SerializeToString(), "a", log)
        )
        m = FakeMessage(
            batch_env([0, 1], sid, payload=b"x", dropped_transport=2).SerializeToString(), "b", log
        )
        await pipeline.handle(m)
        await pipeline.flush_all()
        assert m.acked == 1
        info = await catalog.get_session(sid)
        assert info is not None
        assert info.pseudonym == pseudonym
    finally:
        executor.shutdown()
        await catalog.close()


async def test_stream_and_consumer_are_created():
    s = _settings()
    source = await NatsSource.connect(s)
    try:
        assert source.connected
    finally:
        await source.close()
