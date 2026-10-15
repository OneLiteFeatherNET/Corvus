"""Against the dev compose stack (NATS, PostgreSQL). Skipped by default.

cd deploy/dev && docker compose up -d --wait nats postgres
cd services && uv run pytest -m integration tests/scoring
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import nats
import psycopg
import pytest

from corvus_services.migrate import PgExecutor, load_migrations, run
from corvus_services.nats_source import stream_config
from corvus_services.scoring.nats_io import (
    consumer_config,
    consumer_name,
    ensure_consumer,
    ensure_score_stream,
)
from corvus_services.scoring.pgscores import PgScoreCatalog
from corvus_services.scoring.ports import ScoreRecord
from corvus_services.scoring.settings import ScoreSettings

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


def _record(windows: int, at: datetime, stage: str = "shadow") -> ScoreRecord:
    return ScoreRecord(
        b"\x05" * 32, b"\x01" * 16, 0, "pvp", "combat", "gb",
        "1", stage, 0.5, 0.4, windows, 0, 1, at,
    )  # fmt: skip


async def test_scores_are_upserted_newest_wins_and_history_is_appended():
    s = ScoreSettings()
    migrations = load_migrations(Path(__file__).resolve().parents[2] / "migrations")
    with psycopg.connect(s.pg_dsn, autocommit=True) as conn:
        run(migrations, PgExecutor(conn))
        conn.execute("DELETE FROM corvus.scores_latest WHERE pseudonym = %s", (b"\x05" * 32,))
        conn.execute("DELETE FROM corvus.score_history WHERE pseudonym = %s", (b"\x05" * 32,))
    catalog = await PgScoreCatalog.open(s.pg_dsn, 2)
    try:
        newer = _record(2, NOW + timedelta(seconds=5), "canary")
        older = _record(1, NOW)
        await catalog.write_scores([newer], [newer])
        await catalog.write_scores([older], [older])

        with psycopg.connect(s.pg_dsn) as conn:
            row = conn.execute(
                "SELECT windows, stage FROM corvus.scores_latest WHERE pseudonym = %s",
                (b"\x05" * 32,),
            ).fetchone()
            n = conn.execute(
                "SELECT count(*) FROM corvus.score_history WHERE pseudonym = %s", (b"\x05" * 32,)
            ).fetchone()
        assert row == (2, "canary")
        assert n == (2,)
    finally:
        await catalog.close()


async def test_consumer_filters_are_updated_when_the_partitioning_changes():
    s = ScoreSettings(score_replicas=2, capture_partitions=16, score_consumer_prefix="it-score")
    nc = await nats.connect(s.nats_url)
    js = nc.jetstream()
    try:
        try:
            await js.stream_info(s.nats_stream)
        except Exception:
            await js.add_stream(stream_config(s))
        await ensure_score_stream(js, s)
        await ensure_consumer(js, s, 0)
        grown = s.model_copy(update={"score_replicas": 4})
        await ensure_consumer(js, grown, 0)

        info = await js.consumer_info(s.nats_stream, consumer_name(s, 0))
        assert info.config.filter_subjects == consumer_config(grown, 0).filter_subjects
    finally:
        await js.delete_consumer(s.nats_stream, consumer_name(s, 0))
        await nc.close()
