"""Graceful shutdown: stop fetching, score the pending windows, flush the writes, then close."""

from __future__ import annotations

import asyncio
from typing import Any

from score_rig import Rig, entry, frames_env, segment_start_env, start_env

from corvus_services.scoring.app import ScoringApp
from corvus_services.scoring.batching import Batch
from helpers import FakeMessage


class ScriptedSource:
    """Hands out the scripted messages one per fetch; the first empty fetch is the SIGTERM."""

    def __init__(self, log: list[str], stop: asyncio.Event, messages: list[FakeMessage]) -> None:
        self.log = log
        self.stop = stop
        self.messages = messages
        self.connected = True

    async def fetch(self) -> list[Any]:
        if self.messages:
            return [self.messages.pop(0)]
        self.log.append("sigterm")
        self.stop.set()
        return []

    async def close(self) -> None:
        self.log.append("close nats")


async def test_sigterm_scores_pending_windows_then_flushes_writes_then_closes(tmp_path):
    rig = Rig(tmp_path, score_batch_max=64)  # one window stays pending: neither full nor due
    rig.add_model(entry("m1", stage="enforce"), 0.9)
    await rig.start()
    log: list[str] = []
    stop = asyncio.Event()
    messages = [
        FakeMessage(e.SerializeToString(), f"m{i}", log)
        for i, e in enumerate([start_env(), segment_start_env(), frames_env(n=8)])
    ]
    source = ScriptedSource(log, stop, messages)

    original_score = rig.scorer.score
    original_write = rig.catalog.write_scores
    original_publish = rig.publisher.publish

    async def score(batch: Batch) -> None:
        log.append(f"score {len(batch.windows)}")
        await original_score(batch)

    async def write(latest: Any, history: Any) -> None:
        log.append(f"write {len(latest)}")
        await original_write(latest, history)

    async def publish(subject: str, data: bytes) -> None:
        log.append("publish")
        await original_publish(subject, data)

    rig.scorer.score = score  # type: ignore[method-assign]
    rig.catalog.write_scores = write  # type: ignore[method-assign]
    rig.publisher.publish = publish  # type: ignore[method-assign]

    app = ScoringApp(rig.settings, source, rig.service, rig.manager, rig.writer)
    await app.run(stop)

    assert [e for e in log if not e.startswith("ack")] == [
        "sigterm",  # fetching stops
        "score 1",  # the pending window is scored ...
        "publish",  # ... and its score published ...
        "write 1",  # ... then the writes are flushed ...
        "close nats",  # ... and only then the connection is drained and closed
    ]
    assert len(rig.catalog.history) == 1
    assert rig.writer.queued == (0, 0)


async def test_nothing_is_scored_or_written_when_nothing_is_pending(tmp_path):
    rig = Rig(tmp_path)
    await rig.start()
    log: list[str] = []
    stop = asyncio.Event()

    await ScoringApp(
        rig.settings, ScriptedSource(log, stop, []), rig.service, rig.manager, rig.writer
    ).run(stop)

    assert log == ["sigterm", "close nats"]
    assert rig.catalog.write_calls == 0
