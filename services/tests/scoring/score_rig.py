"""Fakes and a wired-up scoring pipeline for the unit tests (no network, no threads, no sleeping)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from concurrent.futures import Executor, Future
from datetime import timedelta
from pathlib import Path
from typing import Any

import numpy as np
from corvus_ml.features.config import FeaturizerConfig
from corvus_ml.features.frames import Frame
from corvus_ml.features.spec import FeatureSpec

from corvus_services.proto import pb
from corvus_services.scoring.batching import MicroBatcher
from corvus_services.scoring.metrics import ScoreMetrics
from corvus_services.scoring.models import ModelManager, spec_key
from corvus_services.scoring.ports import ModelEntry, ScoreRecord, SessionLookup
from corvus_services.scoring.scorer import Scorer
from corvus_services.scoring.service import ScoringService
from corvus_services.scoring.sessions import SessionTracker
from corvus_services.scoring.settings import ScoreSettings
from corvus_services.scoring.smoothing import EmaTracker
from corvus_services.scoring.writer import ScoreWriter
from helpers import PSEUDONYM, SESSION, FakeClock, FakeMessage

# 8 frames per window, no overlap, so every 8 serverbound frames complete one window.
SMALL = FeaturizerConfig(window=8, overlap=0.0, min_frames=4)


class InlineExecutor(Executor):
    """Runs submitted work immediately on the calling thread: deterministic, no threads."""

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future[Any]:
        future: Future[Any] = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as e:
            future.set_exception(e)
        return future


class FakeSession:
    def __init__(self, shape: tuple[int | None, ...], value: float, intra: int) -> None:
        self.input_shape = shape
        self.value = value
        self.intra = intra
        self.runs: list[int] = []  # batch sizes

    def run(self, x: np.ndarray) -> list[np.ndarray]:
        self.runs.append(len(x))
        return [np.full((len(x), 1), self.value, dtype=np.float32)]


class FakeFactory:
    """Creates fake sessions; ``values`` maps a model directory name to its constant score."""

    def __init__(self) -> None:
        self.values: dict[str, float] = {}
        self.created: list[tuple[Path, int]] = []
        self.sessions: dict[str, FakeSession] = {}
        self.fail_for: set[str] = set()

    def __call__(self, path: Path, intra_op_threads: int) -> FakeSession:
        name = path.parent.name
        if name in self.fail_for:
            raise RuntimeError("invalid onnx")
        spec = FeatureSpec.read(path.parent)
        session = FakeSession(
            (None, spec.n_agg_inputs), self.values.get(name, 0.5), intra_op_threads
        )
        self.created.append((path, intra_op_threads))
        self.sessions[name] = session
        return session


class FakeFiles:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def get(self, key: str) -> bytes:
        if key not in self.objects:
            raise KeyError(key)
        return self.objects[key]

    def put_model(self, entry: ModelEntry, spec: FeatureSpec | None = None) -> None:
        self.objects[entry.onnx_key] = b"onnx"
        self.objects[spec_key(entry.onnx_key)] = (
            (spec or FeatureSpec.current(SMALL)).to_json().encode()
        )


def entry(
    id: str = "m1",
    name: str = "gb",
    stage: str = "shadow",
    ctx: str = "pvp",
    domain: str = "combat",
    version: str = "1",
    candidate_type: str = "cnn1d",
) -> ModelEntry:
    return ModelEntry(
        id, name, version, candidate_type, ctx, domain, stage, f"models/{id}/model.onnx"
    )


class FakeScoreCatalog:
    def __init__(self) -> None:
        self.models: list[ModelEntry] = []
        self.sessions: dict[bytes, SessionLookup] = {}
        self.lookups: list[bytes] = []
        self.fail_models = False
        self.fail_lookup = False
        self.fail_writes = False
        self.latest: dict[tuple[bytes, str, str, str], ScoreRecord] = {}
        self.history: list[ScoreRecord] = []
        self.write_calls = 0

    async def lookup_session(self, session_id: bytes) -> SessionLookup | None:
        self.lookups.append(session_id)
        if self.fail_lookup:
            raise ConnectionError("db down")
        return self.sessions.get(session_id)

    async def active_models(self) -> list[ModelEntry]:
        if self.fail_models:
            raise ConnectionError("db down")
        return list(self.models)

    async def write_scores(
        self, latest: Sequence[ScoreRecord], history: Sequence[ScoreRecord]
    ) -> None:
        self.write_calls += 1
        if self.fail_writes:
            raise ConnectionError("db down")
        for r in latest:
            self.latest[(r.pseudonym, r.game_context, r.domain, r.model_name)] = r
        self.history.extend(history)

    async def ping(self) -> None:
        return None


class FakePublisher:
    def __init__(self) -> None:
        self.sent: list[tuple[str, bytes]] = []
        self.fail = False

    async def publish(self, subject: str, data: bytes) -> None:
        if self.fail:
            raise TimeoutError("no ack")
        self.sent.append((subject, data))


class Rig:
    """The full pipeline on fakes. ``await rig.start()`` loads the models of ``catalog.models``."""

    def __init__(self, tmp_path: Path, **settings: Any) -> None:
        base: dict[str, Any] = {
            "score_batch_max": 4,
            "score_batch_max_wait_ms": 50.0,
            "score_ema_alpha": 0.5,
            "nak_delay_s": 0.0,
            "metrics_port": 0,
            "score_models_dir": tmp_path / "models",
        }
        base.update(settings)
        self.settings = ScoreSettings(**base)
        self.clock = FakeClock()
        self.metrics = ScoreMetrics()
        self.catalog = FakeScoreCatalog()
        self.files = FakeFiles()
        self.factory = FakeFactory()
        self.publisher = FakePublisher()
        self.manager = ModelManager(
            self.catalog, self.files, self.factory, self.settings.score_models_dir, 2, self.metrics
        )
        self.writer = ScoreWriter(
            self.catalog,
            self.metrics,
            self.settings.score_write_queue,
            self.settings.score_write_batch,
            self.settings.score_write_interval_s,
        )
        self.ema = EmaTracker(self.settings.score_ema_alpha)
        self.tracker = SessionTracker(
            self.settings.score_sessions_max,
            timedelta(seconds=self.settings.score_session_idle_s),
            self.clock,
        )
        self.batcher = MicroBatcher(
            self.settings.score_batch_max,
            timedelta(milliseconds=self.settings.score_batch_max_wait_ms),
            self.clock,
        )
        self.scorer = Scorer(
            lambda: self.manager.current,
            InlineExecutor(),
            self.ema,
            self.writer,
            self.publisher,
            self.settings.score_subject_prefix,
            self.clock,
            self.metrics,
        )
        self.service = ScoringService(
            self.settings,
            self.catalog,
            self.manager,
            self.tracker,
            self.batcher,
            self.scorer,
            self.clock,
            self.metrics,
        )
        self.log: list[str] = []
        self.counter = 0

    def add_model(self, e: ModelEntry, value: float = 0.5, spec: FeatureSpec | None = None) -> None:
        self.catalog.models.append(e)
        self.files.put_model(e, spec)
        self.factory.values[e.id] = value

    async def start(self) -> None:
        await self.manager.sync()

    async def send(self, env: Any, delivery_count: int = 1) -> FakeMessage:
        self.counter += 1
        msg = FakeMessage(env.SerializeToString(), f"m{self.counter}", self.log, delivery_count)
        await self.service.handle(msg)
        return msg

    async def settle(self) -> None:
        """Score everything pending and flush the writes."""
        await self.service.drain()
        await self.writer.flush()

    def metric(self, name: str, **labels: str) -> float:
        value = self.metrics.registry.get_sample_value(name, labels)
        return 0.0 if value is None else value


def start_env(
    session_id: bytes = SESSION, pseudonym: bytes = PSEUDONYM, protocol: int = 769
) -> Any:
    env = pb.Envelope()
    s = env.session_start
    s.session_id = session_id
    s.player_pseudonym = pseudonym
    s.pseudonym_key_id = "k1"
    s.client_protocol_version = protocol
    s.observed_protocol_version = protocol
    s.proxy_id = "proxy-1"
    s.plugin_version = "0.1.0"
    return env


def segment_start_env(session_id: bytes = SESSION, segment: int = 0, ctx: str = "pvp") -> Any:
    env = pb.Envelope()
    s = env.segment_start
    s.session_id = session_id
    s.segment = segment
    s.backend_server = "pvp-1"
    s.game_context = ctx
    return env


def frames_env(session_id: bytes = SESSION, segment: int = 0, first: int = 0, n: int = 8) -> Any:
    """``n`` serverbound movement frames, 10 ms apart, with consecutive ``seq`` from ``first``."""
    env = pb.Envelope()
    b = env.batch
    b.session_id = session_id
    b.segment = segment
    for seq in range(first, first + n):
        f = b.frames.add()
        f.seq = seq
        f.offset_nanos = seq * 10_000_000
        f.direction = pb.DIRECTION_SERVERBOUND
        f.state = pb.CONNECTION_STATE_PLAY
        f.packet_type = 14
        f.movement.has_position = True
        f.movement.has_rotation = True
        f.movement.x = float(seq)
        f.movement.y = 64.0
        f.movement.z = 0.5 * seq
        f.movement.yaw = float(seq % 360)
        f.movement.pitch = 5.0
        f.movement.on_ground = True
    return env


def feature_frames(n: int, segment: int = 0, first: int = 0) -> list[Frame]:
    return [
        Frame(
            segment,
            seq,
            seq * 10_000_000,
            1,
            14,
            1,
            True,
            True,
            1,
            float(seq),
            64.0,
            0.5 * seq,
            1.0,
            5.0,
        )
        for seq in range(first, first + n)
    ]


def end_session_env(session_id: bytes = SESSION) -> Any:
    env = pb.Envelope()
    env.session_end.session_id = session_id
    return env


def lookup(session_id: bytes = SESSION, segments: dict[int, str] | None = None) -> SessionLookup:
    return SessionLookup(
        session_id, PSEUDONYM, "k1", 769, {0: "pvp"} if segments is None else segments
    )


async def run_until_idle() -> None:
    """Let ready tasks (the in-flight batch) run."""
    for _ in range(5):
        await asyncio.sleep(0)
