"""Runs one micro-batch through the loaded models, smooths, publishes and persists the scores."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable
from concurrent.futures import Executor
from dataclasses import dataclass

import numpy as np
from corvus_ml.features.featurizer import CompletedWindow
from corvus_ml.features.model_input import model_inputs

from ..clock import Clock
from .batching import Batch, PendingWindow
from .metrics import ScoreMetrics
from .models import LoadedModel, ModelSet, scores_from_outputs
from .ports import PUBLISHED_STAGES, ScorePublisher, ScoreRecord
from .raw import RawWindow, stack_inputs
from .score_proto import score_pb
from .smoothing import EmaTracker
from .writer import ScoreWriter

log = logging.getLogger("corvus.score.scorer")

_SUBJECT_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")
_STAGE = {
    "shadow": score_pb.Score.STAGE_SHADOW,
    "canary": score_pb.Score.STAGE_CANARY,
    "enforce": score_pb.Score.STAGE_ENFORCE,
}


def score_subject(prefix: str, game_context: str) -> str:
    """``<prefix>.<game_context>`` with characters that are not valid in a subject token
    (``.``, ``*``, ``>``, whitespace, ...) replaced by ``_``."""
    return f"{prefix}.{_SUBJECT_UNSAFE.sub('_', game_context)}"


def encode_score(record: ScoreRecord, key_id: str) -> bytes:
    msg = score_pb.Score(
        player_pseudonym=record.pseudonym,
        pseudonym_key_id=key_id,
        session_id=record.session_id,
        segment=record.segment,
        game_context=record.game_context,
        domain=record.domain,
        model_name=record.model_name,
        model_version=record.model_version,
        stage=_STAGE[record.stage],
        score=record.score,
        score_smoothed=record.smoothed,
        windows=record.windows,
        window_start_offset_nanos=record.window_start_offset_nanos,
        window_end_offset_nanos=record.window_end_offset_nanos,
        scored_at_unix_millis=int(record.scored_at.timestamp() * 1000),
    )
    return bytes(msg.SerializeToString())


@dataclass(frozen=True)
class _Hit:
    index: int  # position of the window in the batch
    model: LoadedModel
    score: float


def build_input(model: LoadedModel, windows: list[PendingWindow]) -> np.ndarray:
    """The model's input tensor: stacked token windows for raw-byte models, else the feature
    adapter shared with the evaluation harness."""
    kind = model.input_kind
    if kind == "raw_bytes":
        raws = [w.window for w in windows if isinstance(w.window, RawWindow)]
        if len(raws) != len(windows):
            raise TypeError("a raw-byte model was handed a window that is not a raw window")
        return stack_inputs(raws)
    feats = [w.window for w in windows if isinstance(w.window, CompletedWindow)]
    if len(feats) != len(windows):
        raise TypeError(f"a {kind} model was handed a raw window")
    x: np.ndarray = model_inputs(feats, kind, model.spec)
    return x


def infer(model: LoadedModel, windows: list[PendingWindow]) -> tuple[np.ndarray, float]:
    """Blocking: model input via the shared adapter, then ONNX Runtime. Runs in the thread pool."""
    started = time.perf_counter()
    x = build_input(model, windows)
    outputs = model.session.run(x)
    scores = scores_from_outputs(
        model.entry.candidate_type,
        outputs,
        getattr(model.session, "output_names", None),
        model.semantics,
    )
    if len(scores) != len(windows):
        raise ValueError(f"model returned {len(scores)} scores for {len(windows)} windows")
    return scores, time.perf_counter() - started


class Scorer:
    def __init__(
        self,
        models: Callable[[], ModelSet],
        pool: Executor,
        ema: EmaTracker,
        writer: ScoreWriter,
        publisher: ScorePublisher,
        subject_prefix: str,
        clock: Clock,
        metrics: ScoreMetrics,
    ) -> None:
        self._models = models
        self._pool = pool
        self._ema = ema
        self._writer = writer
        self._publisher = publisher
        self._prefix = subject_prefix
        self._clock = clock
        self._m = metrics

    async def score(self, batch: Batch) -> None:
        """Score ``batch``; never raises (failures are logged and counted)."""
        try:
            if batch.windows:
                await self._score_windows(batch.windows)
        except Exception:
            log.exception("scoring a batch of %d windows failed", len(batch.windows))
        finally:
            for session_id in batch.releases:
                self._ema.release_session(session_id)

    async def _score_windows(self, windows: list[PendingWindow]) -> None:
        self._m.batch_size.observe(len(windows))
        models = self._models()  # one snapshot for the whole batch: a swap never splits it
        groups: dict[str, tuple[LoadedModel, list[int]]] = {}
        for i, pw in enumerate(windows):
            matching = models.matching(pw.game_context, pw.config)
            if not matching:
                self._m.windows_skipped.labels("no_model").inc()
                continue
            for model in matching:
                groups.setdefault(model.id, (model, []))[1].append(i)

        loop = asyncio.get_running_loop()
        jobs = [
            (model, idx, loop.run_in_executor(self._pool, infer, model, [windows[i] for i in idx]))
            for model, idx in groups.values()
        ]
        hits: list[_Hit] = []
        for model, idx, future in jobs:
            name = f"{model.entry.name}:{model.entry.version}"
            try:
                scores, seconds = await future
            except Exception:
                log.exception("model %s failed on a batch of %d windows", name, len(idx))
                self._m.inference_errors.labels(name).inc()
                continue
            self._m.inference_seconds.labels(name).observe(seconds)
            for i, s in zip(idx, scores.tolist(), strict=True):
                if s != s:  # NaN
                    self._m.inference_errors.labels(name).inc()
                    continue
                hits.append(_Hit(i, model, s))

        # Window order, so the smoothing of one (player, segment, model) sees its windows in order.
        hits.sort(key=lambda h: h.index)
        publishes: list[tuple[str, bytes, str]] = []
        for hit in hits:
            pw = windows[hit.index]
            record = self._record(pw, hit)
            self._writer.submit(record)
            self._m.windows_scored.labels(
                f"{hit.model.entry.name}:{hit.model.entry.version}", record.stage
            ).inc()
            if record.stage in PUBLISHED_STAGES:  # shadow is persisted, never published
                publishes.append(
                    (
                        score_subject(self._prefix, pw.game_context),
                        encode_score(record, pw.key_id),
                        record.stage,
                    )
                )
        await self._publish(publishes)

    def _record(self, pw: PendingWindow, hit: _Hit) -> ScoreRecord:
        w, entry = pw.window, hit.model.entry
        smoothed, count = self._ema.update(
            (pw.pseudonym, w.segment, entry.id), w.session_id, hit.score
        )
        return ScoreRecord(
            pseudonym=pw.pseudonym,
            session_id=w.session_id,
            segment=w.segment,
            game_context=pw.game_context,
            domain=entry.domain,
            model_name=entry.name,
            model_version=entry.version,
            stage=entry.stage,
            score=hit.score,
            smoothed=smoothed,
            windows=count,
            window_start_offset_nanos=w.start_offset_nanos,
            window_end_offset_nanos=w.end_offset_nanos,
            scored_at=self._clock(),
        )

    async def _publish(self, publishes: list[tuple[str, bytes, str]]) -> None:
        if not publishes:
            return
        results = await asyncio.gather(
            *(self._publisher.publish(subject, data) for subject, data, _ in publishes),
            return_exceptions=True,
        )
        for (subject, _, stage), result in zip(publishes, results, strict=True):
            if isinstance(result, BaseException):
                log.warning("publishing a score to %s failed: %s", subject, result)
                self._m.publish_failures.inc()
            else:
                self._m.published.labels(stage).inc()
