"""``corvus-score``: online scoring of the capture stream."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from ..cgroup import default_workers
from ..clock import system_clock
from ..health import HealthServer
from ..s3store import S3Store
from .app import ScoringApp
from .batching import MicroBatcher
from .metrics import ScoreMetrics
from .models import ModelManager
from .nats_io import NatsScoreIO
from .ort_session import create_session
from .partitions import filter_subjects, replica_index
from .pgscores import PgScoreCatalog
from .scorer import Scorer
from .service import ScoringService
from .sessions import SessionTracker
from .settings import ScoreSettings
from .smoothing import EmaTracker
from .writer import ScoreWriter

log = logging.getLogger("corvus.score")


async def run(settings: ScoreSettings) -> None:
    # The tokenizer's rayon pool would add threads outside the inference CPU budget.
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    replica = replica_index(settings.score_replica_index, settings.score_replicas)
    cpus = settings.score_cpus or default_workers()
    log.info(
        "replica %d of %d, %d CPU(s), filters %s",
        replica,
        settings.score_replicas,
        cpus,
        filter_subjects(replica, settings.score_replicas, settings.capture_partitions),
    )

    metrics = ScoreMetrics()
    catalog = await PgScoreCatalog.open(settings.pg_dsn, settings.pg_pool_max)
    io = await NatsScoreIO.connect(settings, replica)
    manager = ModelManager(
        catalog,
        S3Store.from_settings(settings),
        create_session,
        settings.score_models_dir,
        cpus,
        metrics,
        settings.score_p99_budget_ms or None,
    )
    pool = ThreadPoolExecutor(max_workers=manager.pool_workers, thread_name_prefix="ort")
    writer = ScoreWriter(
        catalog,
        metrics,
        settings.score_write_queue,
        settings.score_write_batch,
        settings.score_write_interval_s,
    )
    scorer = Scorer(
        lambda: manager.current,
        pool,
        EmaTracker(settings.score_ema_alpha, settings.score_ema_max),
        writer,
        io,
        settings.score_subject_prefix,
        system_clock,
        metrics,
    )
    service = ScoringService(
        settings,
        catalog,
        manager,
        SessionTracker(
            settings.score_sessions_max,
            timedelta(seconds=settings.score_session_idle_s),
            system_clock,
        ),
        MicroBatcher(
            settings.score_batch_max,
            timedelta(milliseconds=settings.score_batch_max_wait_ms),
            system_clock,
        ),
        scorer,
        system_clock,
        metrics,
    )

    async def nats_check() -> None:
        if not io.connected:
            raise ConnectionError("NATS disconnected")

    async def registry_check() -> None:
        if not manager.last_sync_ok:
            raise ConnectionError("the model registry could not be read")

    health = HealthServer(
        metrics.registry,
        {"nats": nats_check, "postgres": catalog.ping, "registry": registry_check},
        settings.metrics_port,
    )
    await health.start()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    try:
        await ScoringApp(settings, io, service, manager, writer).run(stop)
    finally:
        await catalog.close()
        await health.stop()
        pool.shutdown(wait=True)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(run(ScoreSettings()))


if __name__ == "__main__":
    main()
