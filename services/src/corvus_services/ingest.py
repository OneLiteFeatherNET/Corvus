"""``corvus-ingest``: consume the capture stream, write the Parquet lake and the catalog."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from concurrent.futures import ThreadPoolExecutor

import pyarrow as pa

from .cgroup import default_workers
from .clock import system_clock
from .config import Settings
from .crypto import Keyring
from .health import HealthServer
from .metrics import Metrics
from .nats_shutdown import FETCH_ENDED
from .nats_source import NatsSource
from .pgcatalog import PgCatalog
from .pipeline import IngestPipeline
from .s3store import S3Store

log = logging.getLogger("corvus.ingest")


async def consume(source: NatsSource, pipeline: IngestPipeline, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            msgs = await source.fetch()
        except FETCH_ENDED:
            if stop.is_set():  # the pull ended because we are shutting down
                return
            raise
        for msg in msgs:
            await pipeline.handle(msg)
        await pipeline.apply_backpressure()


async def ticker(pipeline: IngestPipeline, stop: asyncio.Event, interval: float = 1.0) -> None:
    while not stop.is_set():
        pipeline.tick()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), interval)


async def run(settings: Settings) -> None:
    workers = settings.workers or default_workers()
    pa.set_cpu_count(workers)
    pa.set_io_thread_count(workers)
    log.info("starting with %d worker thread(s)", workers)
    settings = settings.model_copy(update={"workers": workers})

    metrics = Metrics()
    catalog = await PgCatalog.open(settings.pg_dsn, settings.pg_pool_max)
    store = S3Store.from_settings(settings)
    source = await NatsSource.connect(settings)
    keyring = Keyring(settings.kek_bytes(), settings.kek_id)
    executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="parquet")
    pipeline = IngestPipeline(settings, catalog, store, keyring, executor, system_clock, metrics)

    async def nats_check() -> None:
        if not source.connected:
            raise ConnectionError("NATS disconnected")

    health = HealthServer(
        metrics.registry,
        {"nats": nats_check, "postgres": catalog.ping, "s3": store.ping},
        settings.metrics_port,
    )
    await health.start()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    tick_task = asyncio.create_task(ticker(pipeline, stop))
    try:
        await consume(source, pipeline, stop)
    finally:
        log.info("shutting down: flushing buffers")
        stop.set()
        await tick_task
        await pipeline.flush_all()
        await source.close()
        await catalog.close()
        await health.stop()
        executor.shutdown(wait=True)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(run(Settings()))


if __name__ == "__main__":
    main()
