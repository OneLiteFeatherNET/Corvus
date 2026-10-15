"""NATS JetStream adapters of the scoring service: partition consumer, score stream, publisher."""

from __future__ import annotations

import logging
from typing import Any

import nats
from nats.errors import TimeoutError as NatsTimeout
from nats.js.api import (
    AckPolicy,
    ConsumerConfig,
    DeliverPolicy,
    DiscardPolicy,
    RetentionPolicy,
    StorageType,
    StreamConfig,
)
from nats.js.errors import NotFoundError

from ..nats_source import NatsMessage, connect_options, ensure_stream, raise_replicas
from .partitions import filter_subjects
from .settings import ScoreSettings

log = logging.getLogger("corvus.score.nats")


def consumer_name(s: ScoreSettings, replica: int) -> str:
    return f"{s.score_consumer_prefix}-{replica}"


def consumer_config(s: ScoreSettings, replica: int) -> ConsumerConfig:
    """Durable pull consumer of one replica: its partitions, new messages only, explicit ack."""
    return ConsumerConfig(
        durable_name=consumer_name(s, replica),
        ack_policy=AckPolicy.EXPLICIT,
        deliver_policy=DeliverPolicy.NEW,
        ack_wait=float(s.score_ack_wait_s),
        max_ack_pending=s.score_max_ack_pending,
        filter_subjects=filter_subjects(replica, s.score_replicas, s.capture_partitions),
    )


def configured_filters(config: Any) -> set[str]:
    """The filter subjects of a consumer config (``filter_subjects`` or the single filter)."""
    subjects = getattr(config, "filter_subjects", None)
    if subjects:
        return set(subjects)
    single = getattr(config, "filter_subject", None)
    return {single} if single else set()


def needs_update(existing: Any, wanted: ConsumerConfig) -> bool:
    return configured_filters(existing) != configured_filters(wanted)


async def ensure_consumer(js: Any, s: ScoreSettings, replica: int) -> None:
    """Create the replica's consumer, or update its filters if they changed (new partition count
    or replica count). An unchanged consumer is left alone so it keeps its position."""
    wanted = consumer_config(s, replica)
    try:
        info = await js.consumer_info(s.nats_stream, consumer_name(s, replica))
    except NotFoundError:
        log.info("creating consumer %s", consumer_name(s, replica))
        await js.add_consumer(s.nats_stream, wanted)
        return
    if needs_update(info.config, wanted):
        log.info(
            "updating consumer %s: filters %s -> %s",
            consumer_name(s, replica),
            sorted(configured_filters(info.config)),
            sorted(configured_filters(wanted)),
        )
        await js.add_consumer(s.nats_stream, wanted)


def score_stream_config(s: ScoreSettings) -> StreamConfig:
    return StreamConfig(
        name=s.score_stream,
        subjects=[f"{s.score_subject_prefix}.>"],
        storage=StorageType.FILE,
        retention=RetentionPolicy.LIMITS,
        discard=DiscardPolicy.OLD,
        max_age=float(s.score_stream_max_age_s),
        num_replicas=s.nats_stream_replicas,
    )


async def ensure_score_stream(js: Any, s: ScoreSettings) -> None:
    """Create ``CORVUS_SCORE`` if missing; an existing stream is left alone (operators own it)."""
    try:
        info = await js.stream_info(s.score_stream)
    except NotFoundError:
        log.info("creating stream %s", s.score_stream)
        await js.add_stream(score_stream_config(s))
    else:
        await raise_replicas(js, info, s.nats_stream_replicas)


class NatsScoreIO:
    """One NATS connection serving as score source (capture consumer) and publisher."""

    def __init__(self, nc: Any, js: Any, sub: Any, settings: ScoreSettings) -> None:
        self._nc = nc
        self._js = js
        self._sub = sub
        self._s = settings

    @classmethod
    async def connect(cls, s: ScoreSettings, replica: int) -> NatsScoreIO:
        nc = await nats.connect(s.nats_url, **connect_options(s, f"corvus-score-{replica}"))
        js = nc.jetstream()
        await ensure_stream(js, s)
        await ensure_score_stream(js, s)
        await ensure_consumer(js, s, replica)
        sub = await js.pull_subscribe_bind(consumer_name(s, replica), stream=s.nats_stream)
        return cls(nc, js, sub, s)

    @property
    def connected(self) -> bool:
        return bool(self._nc.is_connected)

    async def fetch(self) -> list[Any]:
        try:
            msgs = await self._sub.fetch(self._s.fetch_batch, timeout=self._s.fetch_timeout_s)
        except NatsTimeout:
            return []
        return [NatsMessage(m) for m in msgs]

    async def publish(self, subject: str, data: bytes) -> None:
        await self._js.publish(subject, data)

    async def close(self) -> None:
        await self._nc.drain()
