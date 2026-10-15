"""Ports of the scoring service; production adapters in ``pgscores``, ``nats_io``, ``ort_session``
and ``s3store``, unit tests use in-memory fakes."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

import numpy as np

from ..ports import Message

# Stages that are loaded and scored; only the last two are published.
SCORED_STAGES = ("shadow", "canary", "enforce")
PUBLISHED_STAGES = ("canary", "enforce")


@dataclass(frozen=True)
class SessionLookup:
    """What the catalog knows about a session: its player and the segments written by ingest."""

    session_id: bytes
    pseudonym: bytes
    key_id: str
    observed_protocol: int
    segments: dict[int, str]  # segment -> game_context
    client_protocol: int | None = None  # what the raw-byte models' protocol token is built from


@dataclass(frozen=True)
class ModelEntry:
    """One row of the model registry in a scored stage."""

    id: str
    name: str
    version: str
    candidate_type: str
    game_context: str
    domain: str
    stage: str
    onnx_key: str


@dataclass(frozen=True)
class ScoreRecord:
    """One persisted score (``scores_latest`` upsert and ``score_history`` row)."""

    pseudonym: bytes
    session_id: bytes
    segment: int
    game_context: str
    domain: str
    model_name: str
    model_version: str
    stage: str
    score: float
    smoothed: float
    windows: int
    window_start_offset_nanos: int
    window_end_offset_nanos: int
    scored_at: datetime


class ScoreCatalog(Protocol):
    async def lookup_session(self, session_id: bytes) -> SessionLookup | None:
        """Session with pseudonym and segments, None if unknown, orphaned or erased."""

    async def active_models(self) -> list[ModelEntry]:
        """Registry rows in the stages shadow, canary and enforce."""

    async def write_scores(
        self, latest: Sequence[ScoreRecord], history: Sequence[ScoreRecord]
    ) -> None:
        """Upsert ``latest`` (newer ``scored_at`` wins), append ``history``; one transaction."""

    async def ping(self) -> None: ...


class ModelFiles(Protocol):
    async def get(self, key: str) -> bytes:
        """Object body; raises if it does not exist."""


class ModelSession(Protocol):
    """An ONNX Runtime session as the scorer uses it."""

    @property
    def input_shape(self) -> tuple[int | str | None, ...]: ...
    def run(self, x: np.ndarray) -> list[np.ndarray]: ...


class SessionFactory(Protocol):
    def __call__(self, path: Path, intra_op_threads: int) -> ModelSession: ...


class ScoreSource(Protocol):
    async def fetch(self) -> list[Message]: ...
    async def close(self) -> None: ...
    @property
    def connected(self) -> bool: ...


class ScorePublisher(Protocol):
    async def publish(self, subject: str, data: bytes) -> None: ...
