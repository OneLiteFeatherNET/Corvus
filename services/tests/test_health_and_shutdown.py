"""Probe endpoints and clean shutdown of the NATS-fed services (ingest, score)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from nats.errors import ConnectionClosedError, DrainTimeoutError
from nats.errors import TimeoutError as NatsTimeout
from prometheus_client import CollectorRegistry

from corvus_services.health import HealthServer
from corvus_services.ingest import consume as ingest_consume
from corvus_services.nats_source import NatsSource
from corvus_services.scoring.app import consume as score_consume
from corvus_services.scoring.nats_io import NatsScoreIO
from helpers import make_settings


def make_health(checks: dict[str, Any], timeout: float = 0.01) -> HealthServer:
    return HealthServer(CollectorRegistry(), checks, 0, check_timeout_s=timeout)


async def test_livez_answers_200_even_when_a_check_hangs():
    async def hang() -> None:
        await asyncio.Event().wait()

    status, _, body = await asyncio.wait_for(make_health({"s3": hang}).respond("/livez"), 1)
    assert status == 200, "liveness must not depend on any dependency"
    assert body == b"ok\n"


async def test_healthz_is_503_when_a_check_fails():
    async def bad() -> None:
        raise ConnectionError("down")

    status, _, body = await make_health({"postgres": bad}).respond("/healthz")
    assert status == 503
    assert b"postgres: ConnectionError: down" in body


async def test_healthz_is_503_when_a_check_exceeds_the_check_timeout():
    async def hang() -> None:
        await asyncio.Event().wait()

    status, _, _ = await asyncio.wait_for(make_health({"s3": hang}).respond("/healthz"), 1)
    assert status == 503


async def test_healthz_is_200_when_all_checks_pass():
    async def ok() -> None:
        return None

    status, _, _ = await make_health({"nats": ok}).respond("/healthz")
    assert status == 200


class EndingSource:
    """Sets stop, then fails the in-flight fetch like nats-py does on a closed subscription."""

    def __init__(self, stop: asyncio.Event, error: BaseException) -> None:
        self.stop = stop
        self.error = error

    async def fetch(self) -> list[Any]:
        self.stop.set()
        raise self.error


class NoopPipeline:
    async def handle(self, msg: Any) -> None:  # pragma: no cover - never reached
        raise AssertionError

    async def apply_backpressure(self) -> None:
        return None


class NoopService(NoopPipeline):
    async def backpressure(self) -> None:
        return None


ENDINGS = [TimeoutError(), NatsTimeout(), ConnectionClosedError()]


@pytest.mark.parametrize("error", ENDINGS, ids=lambda e: type(e).__name__)
async def test_ingest_consume_exits_cleanly_when_fetch_fails_after_stop(error):
    stop = asyncio.Event()
    await ingest_consume(EndingSource(stop, error), NoopPipeline(), stop)  # type: ignore[arg-type]


@pytest.mark.parametrize("error", ENDINGS, ids=lambda e: type(e).__name__)
async def test_score_consume_exits_cleanly_when_fetch_fails_after_stop(error):
    stop = asyncio.Event()
    await score_consume(EndingSource(stop, error), NoopService(), stop)  # type: ignore[arg-type]


async def test_consume_still_raises_a_fetch_error_while_running():
    class Failing:
        async def fetch(self) -> list[Any]:
            raise ConnectionClosedError()

    with pytest.raises(ConnectionClosedError):
        await ingest_consume(Failing(), NoopPipeline(), asyncio.Event())  # type: ignore[arg-type]


class FakeNc:
    def __init__(self, drain_error: BaseException | None = None) -> None:
        self.drain_error = drain_error
        self.calls: list[str] = []

    async def drain(self) -> None:
        self.calls.append("drain")
        if self.drain_error:
            raise self.drain_error

    async def close(self) -> None:
        self.calls.append("close")


class TimingOutSub:
    async def fetch(self, *a: Any, **kw: Any) -> list[Any]:
        raise TimeoutError


@pytest.mark.parametrize(
    "make",
    [
        lambda nc: NatsSource(nc, None, make_settings()),
        lambda nc: NatsScoreIO(nc, None, None, make_settings()),  # type: ignore[arg-type]
    ],
    ids=["ingest", "score"],
)
async def test_close_swallows_a_drain_timeout_and_closes_the_connection(make, caplog):
    nc = FakeNc(DrainTimeoutError())
    await make(nc).close()
    assert nc.calls == ["drain", "close"]
    assert any(r.levelname == "WARNING" for r in caplog.records)


@pytest.mark.parametrize(
    "make",
    [
        lambda sub: NatsSource(FakeNc(), sub, make_settings()),
        lambda sub: NatsScoreIO(FakeNc(), None, sub, make_settings()),  # type: ignore[arg-type]
    ],
    ids=["ingest", "score"],
)
async def test_fetch_treats_a_builtin_timeout_as_an_empty_batch(make):
    assert await make(TimingOutSub()).fetch() == []
