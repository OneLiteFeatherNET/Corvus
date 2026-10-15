"""NATS JetStream adapter: stream/consumer provisioning and message wrapper."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

import nats
from nats.errors import TimeoutError as NatsTimeout
from nats.js.api import (
    AckPolicy,
    ConsumerConfig,
    DiscardPolicy,
    RetentionPolicy,
    StorageType,
    StreamConfig,
)
from nats.js.errors import NotFoundError

from .config import Settings

log = logging.getLogger("corvus.nats")


class NatsMessage:
    """Adapts a nats-py message to the pipeline's ``Message`` port."""

    def __init__(self, msg: Any) -> None:
        self._msg = msg

    @property
    def msg_id(self) -> str:
        headers = self._msg.headers or {}
        mid = headers.get("Nats-Msg-Id")
        if mid:
            return str(mid)
        return f"seq-{self._msg.metadata.sequence.stream}"

    @property
    def data(self) -> bytes:
        return bytes(self._msg.data)

    @property
    def delivery_count(self) -> int:
        return int(self._msg.metadata.num_delivered)

    async def ack(self) -> None:
        await self._msg.ack()

    async def nak(self, delay: float) -> None:
        await self._msg.nak(delay=delay)

    async def term(self) -> None:
        await self._msg.term()


def connect_options(s: Settings, name: str) -> dict[str, Any]:
    """Keyword arguments of ``nats.connect`` (everything but the URL)."""
    opts: dict[str, Any] = {"max_reconnect_attempts": -1, "name": name}
    if s.nats_creds_file:
        opts["user_credentials"] = s.nats_creds_file
    return opts


class NatsSource:
    def __init__(self, nc: Any, sub: Any, settings: Settings) -> None:
        self._nc = nc
        self._sub = sub
        self._s = settings

    @classmethod
    async def connect(cls, s: Settings) -> NatsSource:
        nc = await nats.connect(s.nats_url, **connect_options(s, "corvus-ingest"))
        js = nc.jetstream()
        await ensure_stream(js, s)
        sub = await js.pull_subscribe(
            s.nats_subject,
            durable=s.nats_consumer,
            stream=s.nats_stream,
            config=consumer_config(s),
        )
        return cls(nc, sub, s)

    @property
    def connected(self) -> bool:
        return bool(self._nc.is_connected)

    async def fetch(self) -> list[NatsMessage]:
        try:
            msgs = await self._sub.fetch(self._s.fetch_batch, timeout=self._s.fetch_timeout_s)
        except NatsTimeout:
            return []
        return [NatsMessage(m) for m in msgs]

    async def close(self) -> None:
        await self._nc.drain()


def stream_config(s: Settings) -> StreamConfig:
    return StreamConfig(
        name=s.nats_stream,
        subjects=[s.nats_subject],
        storage=StorageType.FILE,
        retention=RetentionPolicy.LIMITS,
        discard=DiscardPolicy.OLD,
        max_age=float(s.stream_max_age_s),
        max_bytes=s.stream_max_bytes,
        max_msg_size=s.stream_max_msg_size,
        duplicate_window=float(s.stream_duplicate_window_s),
        num_replicas=s.nats_stream_replicas,
    )


def consumer_config(s: Settings) -> ConsumerConfig:
    return ConsumerConfig(
        durable_name=s.nats_consumer,
        ack_policy=AckPolicy.EXPLICIT,
        ack_wait=float(s.consumer_ack_wait_s),
        max_ack_pending=s.consumer_max_ack_pending,
        filter_subject=s.nats_subject,
    )


async def raise_replicas(js: Any, info: Any, wanted: int) -> None:
    """Grow an existing stream to ``wanted`` replicas; never shrinks it (operators own that)."""
    config = info.config
    if (config.num_replicas or 1) >= wanted:
        return
    log.info("raising stream %s from %s to %s replicas", config.name, config.num_replicas, wanted)
    await js.update_stream(replace(config, num_replicas=wanted))


async def ensure_stream(js: Any, s: Settings) -> None:
    """Create the stream if missing. An existing stream keeps its limits (operators own them);
    only its replica count is raised to ``nats_stream_replicas``."""
    try:
        info = await js.stream_info(s.nats_stream)
    except NotFoundError:
        log.info("creating stream %s", s.nats_stream)
        await js.add_stream(stream_config(s))
    else:
        await raise_replicas(js, info, s.nats_stream_replicas)
