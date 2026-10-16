"""Run loop and graceful shutdown of the scoring service."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

from ..nats_shutdown import FETCH_ENDED
from .lag import LagWatch
from .metrics import ScoreMetrics
from .models import ModelManager
from .ports import ScoreSource
from .service import ScoringService
from .settings import ScoreSettings
from .writer import ScoreWriter

log = logging.getLogger("corvus.score")


async def consume(source: ScoreSource, service: ScoringService, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            msgs = await source.fetch()
        except FETCH_ENDED:
            if stop.is_set():  # the pull ended because we are shutting down
                return
            raise
        for msg in msgs:
            await service.handle(msg)
        await service.backpressure()


async def periodically(
    action: Callable[[], Awaitable[object] | None], interval: float, stop: asyncio.Event
) -> None:
    while not stop.is_set():
        result = action()
        if result is not None:
            await result
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), interval)


class ScoringApp:
    def __init__(
        self,
        settings: ScoreSettings,
        source: ScoreSource,
        service: ScoringService,
        manager: ModelManager,
        writer: ScoreWriter,
        backlog: Callable[[], Awaitable[int]] | None = None,
        metrics: ScoreMetrics | None = None,
    ) -> None:
        self._backlog = backlog
        self._metrics = metrics or ScoreMetrics()
        self._lag = LagWatch(settings.score_lag_growth_polls)
        self._s = settings
        self._source = source
        self._service = service
        self._manager = manager
        self._writer = writer

    async def poll_backlog(
        self,
        backlog: Callable[[], Awaitable[int]],
        watch: LagWatch,
        metrics: ScoreMetrics,
    ) -> None:
        """One lag poll: export the pending count, warn once when it keeps growing."""
        try:
            pending = await backlog()
        except Exception as e:  # a failed poll must never stop scoring
            log.warning("consumer lag poll failed: %s", e)
            return
        metrics.consumer_pending.set(pending)
        if watch.observe(pending):
            log.warning(
                "scoring falls behind: %d capture messages pending, growing for %d polls; "
                "add replicas, CPUs, or a smaller max_packets (see corvus_score_model_"
                "estimated_capacity_players)",
                pending,
                self._s.score_lag_growth_polls,
            )
        metrics.falling_behind.set(1 if watch.behind else 0)

    async def run(self, stop: asyncio.Event) -> None:
        """Consume until ``stop`` is set, then shut down in order: stop fetching, score the
        pending windows, flush the writes, close NATS (which drains outstanding publishes)."""
        await self._manager.sync()  # models first: frames of unmodelled contexts are not fed
        tick_every = max(0.005, self._s.score_batch_max_wait_ms / 2000)
        background = [
            asyncio.create_task(periodically(self._service.tick, tick_every, stop)),
            asyncio.create_task(
                periodically(self._manager.sync, self._s.score_registry_poll_s, stop)
            ),
        ]
        if self._backlog is not None and self._s.score_lag_growth_polls > 0:
            background.append(
                asyncio.create_task(
                    periodically(
                        lambda: self.poll_backlog(self._backlog, self._lag, self._metrics),  # type: ignore[arg-type]
                        self._s.score_lag_poll_s,
                        stop,
                    )
                )
            )
        writer_task = asyncio.create_task(self._writer.run())
        try:
            await consume(self._source, self._service, stop)
        finally:
            log.info("shutting down: stop fetching, score pending windows, flush writes")
            stop.set()
            await asyncio.gather(*background, return_exceptions=True)
            try:
                await asyncio.wait_for(self._service.drain(), self._s.score_drain_timeout_s)
            except TimeoutError:
                log.error("pending windows were not scored within the drain timeout")
            self._writer.stop()
            await writer_task
            await self._writer.flush()
            await self._source.close()
