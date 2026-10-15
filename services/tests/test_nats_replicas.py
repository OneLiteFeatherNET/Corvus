from __future__ import annotations

from types import SimpleNamespace

import pytest
from nats.js.api import StreamConfig
from nats.js.errors import NotFoundError

from corvus_services.config import Settings
from corvus_services.nats_source import ensure_stream, stream_config
from corvus_services.scoring.nats_io import ensure_score_stream, score_stream_config
from corvus_services.scoring.settings import ScoreSettings


class FakeJs:
    def __init__(self, existing: StreamConfig | None) -> None:
        self.existing = existing
        self.added: list[StreamConfig] = []
        self.updated: list[StreamConfig] = []

    async def stream_info(self, name: str):
        if self.existing is None:
            raise NotFoundError
        return SimpleNamespace(config=self.existing)

    async def add_stream(self, config: StreamConfig) -> None:
        self.added.append(config)

    async def update_stream(self, config: StreamConfig) -> None:
        self.updated.append(config)


def test_replicas_default_to_one():
    assert Settings().nats_stream_replicas == 1
    assert stream_config(Settings()).num_replicas == 1


def test_replicas_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("CORVUS_NATS_STREAM_REPLICAS", "3")

    assert stream_config(Settings()).num_replicas == 3
    assert score_stream_config(ScoreSettings()).num_replicas == 3


@pytest.mark.parametrize("bad", [0, 6])
def test_replicas_are_bounded(bad):
    with pytest.raises(ValueError, match="nats_stream_replicas"):
        Settings(nats_stream_replicas=bad)


async def test_a_missing_stream_is_created_with_the_replicas():
    js = FakeJs(None)

    await ensure_stream(js, Settings(nats_stream_replicas=3))

    assert [c.num_replicas for c in js.added] == [3]


async def test_a_stream_with_fewer_replicas_is_updated_keeping_its_limits():
    js = FakeJs(StreamConfig(name="CORVUS_CAPTURE", num_replicas=1, max_bytes=123))

    await ensure_stream(js, Settings(nats_stream_replicas=3))

    assert [(c.num_replicas, c.max_bytes) for c in js.updated] == [(3, 123)]
    assert js.added == []


@pytest.mark.parametrize("have", [3, 5])
async def test_a_stream_with_enough_replicas_is_left_alone(have):
    js = FakeJs(StreamConfig(name="CORVUS_CAPTURE", num_replicas=have))

    await ensure_stream(js, Settings(nats_stream_replicas=3))

    assert js.updated == []


async def test_the_score_stream_follows_the_same_rule():
    js = FakeJs(StreamConfig(name="CORVUS_SCORE", num_replicas=1))

    await ensure_score_stream(js, ScoreSettings(nats_stream_replicas=3))

    assert [c.num_replicas for c in js.updated] == [3]
