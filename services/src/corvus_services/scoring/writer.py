"""Batched, non-blocking persistence of scores.

The consumer loop only calls :meth:`ScoreWriter.submit`, which never waits. ``scores_latest`` rows
are coalesced per (pseudonym, game_context, domain, model) and therefore bounded by the number of
active keys; they are never dropped. ``score_history`` rows wait in a bounded queue: when it is
full the new history row is dropped and counted, the latest row is still upserted.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque

from .metrics import ScoreMetrics
from .ports import ScoreCatalog, ScoreRecord

log = logging.getLogger("corvus.score.writer")

type LatestKey = tuple[bytes, str, str, str]


def latest_key(r: ScoreRecord) -> LatestKey:
    return r.pseudonym, r.game_context, r.domain, r.model_name


class ScoreWriter:
    def __init__(
        self,
        catalog: ScoreCatalog,
        metrics: ScoreMetrics,
        queue_size: int,
        batch_size: int,
        interval_s: float,
    ) -> None:
        self._catalog = catalog
        self._m = metrics
        self._queue_size = queue_size
        self._batch = batch_size
        self._interval = interval_s
        self._latest: dict[LatestKey, ScoreRecord] = {}
        self._history: deque[ScoreRecord] = deque()
        self._wake = asyncio.Event()
        self._flush_lock = asyncio.Lock()
        self._stopping = False

    @property
    def queued(self) -> tuple[int, int]:
        """``(latest rows, history rows)`` waiting to be written."""
        return len(self._latest), len(self._history)

    def submit(self, record: ScoreRecord) -> None:
        """Queue one score. Never blocks and never raises."""
        key = latest_key(record)
        current = self._latest.get(key)
        if current is None or record.scored_at >= current.scored_at:
            self._latest[key] = record
        if len(self._history) < self._queue_size:
            self._history.append(record)
        else:
            self._m.history_dropped.inc()
        self._update_gauges()
        if len(self._latest) >= self._batch or len(self._history) >= self._batch:
            self._wake.set()

    async def flush(self) -> None:
        """Write everything queued, in batches. A failed batch is put back and ends the flush."""
        async with self._flush_lock:
            while self._latest or self._history:
                keys = list(self._latest)[: self._batch]
                latest = [self._latest.pop(k) for k in keys]
                history = [
                    self._history.popleft() for _ in range(min(self._batch, len(self._history)))
                ]
                self._update_gauges()
                try:
                    await self._catalog.write_scores(latest, history)
                except Exception:
                    log.exception(
                        "could not write %d latest / %d history scores", len(latest), len(history)
                    )
                    self._m.write_failures.inc()
                    self._restore(latest, history)
                    return

    def _restore(self, latest: list[ScoreRecord], history: list[ScoreRecord]) -> None:
        for r in latest:
            current = self._latest.get(latest_key(r))
            if current is None or r.scored_at > current.scored_at:
                self._latest[latest_key(r)] = r
        room = self._queue_size - len(self._history)
        keep = history[:room] if room > 0 else []
        self._history.extendleft(reversed(keep))
        if len(keep) < len(history):
            self._m.history_dropped.inc(len(history) - len(keep))
        self._update_gauges()

    async def run(self) -> None:
        """Flush whenever a batch is full or the interval elapsed, until :meth:`stop`."""
        while not self._stopping:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), self._interval)
            self._wake.clear()
            await self.flush()

    def stop(self) -> None:
        self._stopping = True
        self._wake.set()

    def _update_gauges(self) -> None:
        self._m.write_queue.labels("latest").set(len(self._latest))
        self._m.write_queue.labels("history").set(len(self._history))
