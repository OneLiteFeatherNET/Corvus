"""Model registry polling, download, validation and atomic hot-swap.

A :class:`ModelSet` is an immutable snapshot of the loaded models. :class:`ModelManager.sync`
builds the next snapshot from the registry and replaces ``current`` with a single assignment, so
the scoring path always sees either the old or the new set, never a mix. A model whose files fail
validation is not loaded (metric, error log); all other models keep running.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import posixpath
import shutil
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal, cast

import numpy as np
from corvus_ml.features.config import FeaturizerConfig
from corvus_ml.features.spec import SPEC_FILE, FeatureSpec, FeatureSpecError
from corvus_ml.raw.spec import TOKENIZER_FILE, RawBytesSpec
from corvus_ml.raw.tokenizer import RawTokenizer

from .metrics import ScoreMetrics
from .ports import ModelEntry, ModelFiles, ModelSession, ScoreCatalog, SessionFactory
from .raw import budget_warning, estimate_capacity_players, estimate_mean_ms, estimate_p99_ms

log = logging.getLogger("corvus.score.models")

InputKind = Literal["agg", "seq", "raw_bytes"]
FEATURE_KINDS = ("agg", "seq")
RAW_KIND = "raw_bytes"
ONNX_FILE = "model.onnx"


class ModelRejectedError(Exception):
    """The model's files are not usable with this runtime; retrying will not help."""


def plan_threads(cpus: int, models: int) -> tuple[int, int]:
    """Split the CPU budget across ``models``: ``(intra_op_threads_per_model, pool_workers)``.

    At most ``min(models, cpus)`` models run at the same time, each with ``cpus // that`` intra-op
    threads, so the compute threads never exceed ``cpus``. With one CPU everything runs
    sequentially on a single thread.
    """
    cpus = max(1, cpus)
    concurrent = max(1, min(models, cpus))
    return max(1, cpus // concurrent), concurrent


def input_kind_of(shape: tuple[int | str | None, ...], spec: FeatureSpec) -> InputKind:
    """Model input kind from the ONNX input rank (2: aggregates, 3: token sequences), checked
    against the feature spec; raises :class:`ModelRejectedError` on a mismatch."""
    if len(shape) == 2:
        width = shape[1]
        if isinstance(width, int) and width != spec.n_agg_inputs:
            raise ModelRejectedError(
                f"model takes {width} aggregates, spec has {spec.n_agg_inputs}"
            )
        return "agg"
    if len(shape) == 3:
        length, channels = shape[1], shape[2]
        if isinstance(length, int) and length != spec.seq_len:
            raise ModelRejectedError(f"model takes {length} frames, spec has {spec.seq_len}")
        if isinstance(channels, int) and channels != spec.n_seq_channels:
            raise ModelRejectedError(
                f"model takes {channels} channels, spec has {spec.n_seq_channels}"
            )
        return "seq"
    raise ModelRejectedError(f"unsupported model input shape {shape}")


def check_raw_shape(shape: tuple[int | str | None, ...], raw: RawBytesSpec) -> None:
    """The ONNX input of a raw-byte model: ``(batch, [macro,] packets, max_tokens + 1)``."""
    rank = 4 if raw.window.macro_windows > 1 else 3
    if len(shape) != rank:
        raise ModelRejectedError(
            f"raw-byte model input has rank {len(shape)}, the spec (macro windows "
            f"{raw.window.macro_windows}) needs rank {rank}"
        )
    channels = shape[-1]
    want = raw.packet.max_tokens_per_packet + 1
    if isinstance(channels, int) and channels != want:
        raise ModelRejectedError(f"model takes {channels} channels, the spec has {want}")
    if rank == 4 and isinstance(shape[1], int) and shape[1] != raw.window.macro_windows:
        raise ModelRejectedError(
            f"model takes {shape[1]} macro windows, the spec has {raw.window.macro_windows}"
        )


def declared_kind(card: dict[str, object] | None, spec: FeatureSpec) -> InputKind | None:
    """The input kind the model declares: ``model_card.json`` ``input_kind``, else ``raw_bytes``
    if the feature spec has a ``raw_bytes`` section, else None (legacy: decided by the ONNX rank).

    A raw-byte spec and a card that name different kinds are rejected: the ONNX rank alone cannot
    tell a token-sequence model from a raw-byte one (both feed a 3-D tensor).
    """
    declared = card.get("input_kind") if isinstance(card, dict) else None
    if declared is not None and declared not in (*FEATURE_KINDS, RAW_KIND):
        raise ModelRejectedError(f"model card declares unknown input_kind {declared!r}")
    if declared == RAW_KIND and spec.raw_bytes is None:
        raise ModelRejectedError("model card says raw_bytes but feature_spec.json has no raw_bytes")
    if declared in FEATURE_KINDS and spec.raw_bytes is not None:
        raise ModelRejectedError(
            f"model card says {declared} but feature_spec.json describes a raw-byte model"
        )
    if declared is not None:
        return cast(InputKind, declared)
    return "raw_bytes" if spec.raw_bytes is not None else None


CALIBRATED_SUFFIX = "_calibrated"
SCORE_OUTPUT = "score"
MODEL_CARD_FILE = "model_card.json"


@dataclass(frozen=True)
class OutputSemantics:
    """What the model declares about its output (``model_card.json`` / registered type)."""

    output_name: str | None = None
    calibrated: bool = False


def output_semantics(candidate_type: str, card: dict[str, object] | None) -> OutputSemantics:
    """Calibrated models are announced by the ``_calibrated`` candidate type suffix or by a
    ``model_card.json`` whose ``output_semantics.range`` is ``[0, 1]``; the card also names the
    ONNX output (``onnx_output``). Anything else is a legacy graph."""
    name: str | None = None
    calibrated = candidate_type.endswith(CALIBRATED_SUFFIX)
    sem = card.get("output_semantics") if isinstance(card, dict) else None
    if isinstance(sem, dict):
        out = sem.get("onnx_output")
        if isinstance(out, str) and out:
            name = out
        if sem.get("range") == [0, 1] or sem.get("range") == [0.0, 1.0]:
            calibrated = True
    if calibrated and name is None:
        name = SCORE_OUTPUT
    return OutputSemantics(name, calibrated)


def scores_from_outputs(
    candidate_type: str,
    outputs: list[np.ndarray],
    output_names: tuple[str, ...] | None = None,
    semantics: OutputSemantics | None = None,
) -> np.ndarray:
    """One score per window, higher = more likely cheating.

    A model that declares a calibrated output (see :func:`output_semantics`) is read from the
    output of that name (``score``) and used as it is. Legacy graphs are clipped to [0, 1] and
    follow ``Candidate.onnx_to_score`` of ``corvus_ml.eval`` (which needs torch and so cannot be
    imported here): the scikit-learn candidates have two outputs (label, scores), every other
    graph exports a single ``score`` output.
    """
    sem = semantics or OutputSemantics(None, candidate_type.endswith(CALIBRATED_SUFFIX))
    if sem.calibrated:
        index = 0
        if output_names and (sem.output_name or SCORE_OUTPUT) in output_names:
            index = output_names.index(sem.output_name or SCORE_OUTPUT)
        out = np.asarray(outputs[index], dtype=np.float64)
        return out.reshape(len(out), -1)[:, 0]
    if candidate_type == "isolation_forest":
        raw = -np.asarray(outputs[1], dtype=np.float64).reshape(-1)
    elif candidate_type == "gradient_boosting":
        raw = np.asarray(outputs[1], dtype=np.float64)[:, 1]
    else:
        first = np.asarray(outputs[0], dtype=np.float64)
        raw = first.reshape(len(first), -1)[:, 0]
    return np.clip(raw, 0.0, 1.0)


@dataclass(frozen=True)
class LoadedModel:
    entry: ModelEntry
    spec: FeatureSpec
    session: ModelSession
    input_kind: InputKind
    intra_op_threads: int
    semantics: OutputSemantics = field(default_factory=OutputSemantics)
    tokenizer: RawTokenizer | None = None  # raw-byte models only

    @property
    def id(self) -> str:
        return self.entry.id

    @property
    def raw_spec(self) -> RawBytesSpec | None:
        """The tokenization and windowing contract of a raw-byte model, else None."""
        return self.spec.raw_bytes if self.input_kind == RAW_KIND else None

    @property
    def window(self) -> FeaturizerConfig:
        return self.spec.window


@dataclass(frozen=True)
class ModelSet:
    """Immutable snapshot of the loaded models."""

    models: tuple[LoadedModel, ...] = ()
    _by_context: dict[str, tuple[LoadedModel, ...]] = field(default_factory=dict, compare=False)
    _raw_by_context: dict[str, dict[RawBytesSpec, RawTokenizer]] = field(
        default_factory=dict, compare=False
    )

    @classmethod
    def of(cls, models: list[LoadedModel]) -> ModelSet:
        by_context: dict[str, list[LoadedModel]] = {}
        for m in models:
            by_context.setdefault(m.entry.game_context, []).append(m)
        raw: dict[str, dict[RawBytesSpec, RawTokenizer]] = {}
        for m in models:
            if m.raw_spec is not None and m.tokenizer is not None:
                raw.setdefault(m.entry.game_context, {}).setdefault(m.raw_spec, m.tokenizer)
        return cls(tuple(models), {k: tuple(v) for k, v in by_context.items()}, raw)

    def __len__(self) -> int:
        return len(self.models)

    def for_context(self, game_context: str) -> tuple[LoadedModel, ...]:
        return self._by_context.get(game_context, ())

    def configs_for(self, game_context: str) -> frozenset[FeaturizerConfig]:
        """Featurizer windows of the aggregate / token-sequence models (raw-byte ones have their
        own windowing, see :meth:`raw_specs_for`)."""
        return frozenset(m.window for m in self.for_context(game_context) if m.raw_spec is None)

    def raw_specs_for(self, game_context: str) -> dict[RawBytesSpec, RawTokenizer]:
        """Distinct raw-byte windowings of the context with the tokenizer that serves them."""
        return self._raw_by_context.get(game_context, {})

    def matching(
        self, game_context: str, window: FeaturizerConfig | RawBytesSpec
    ) -> list[LoadedModel]:
        if isinstance(window, RawBytesSpec):
            return [m for m in self.for_context(game_context) if m.raw_spec == window]
        return [
            m for m in self.for_context(game_context) if m.raw_spec is None and m.window == window
        ]


def card_key(onnx_key: str) -> str:
    """The ``model_card.json`` next to the ONNX file."""
    return posixpath.join(posixpath.dirname(onnx_key), MODEL_CARD_FILE)


def tokenizer_key(onnx_key: str) -> str:
    """The ``tokenizer.json`` of a raw-byte model (uploaded next to the ONNX file)."""
    return posixpath.join(posixpath.dirname(onnx_key), TOKENIZER_FILE)


def spec_key(onnx_key: str) -> str:
    """The ``feature_spec.json`` that the eval export writes next to the ONNX files."""
    return posixpath.join(posixpath.dirname(onnx_key), SPEC_FILE)


@dataclass
class SyncResult:
    loaded: list[str] = field(default_factory=list)
    unloaded: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)


class ModelManager:
    def __init__(
        self,
        catalog: ScoreCatalog,
        files: ModelFiles,
        factory: SessionFactory,
        cache_dir: Path,
        cpus: int,
        metrics: ScoreMetrics,
        p99_budget_ms: float | None = None,
    ) -> None:
        self._catalog = catalog
        self._files = files
        self._factory = factory
        self._cache = cache_dir
        self._cpus = max(1, cpus)
        self._m = metrics
        self._budget_ms = p99_budget_ms
        self.current = ModelSet()
        self._rejected: set[tuple[str, str]] = set()  # (model id, onnx_key) that can never load
        self.last_sync_ok = False

    @property
    def pool_workers(self) -> int:
        """Size of the inference thread pool: one worker per CPU at most."""
        return self._cpus

    async def sync(self) -> SyncResult:
        """Poll the registry and swap in the new model set. Never raises."""
        result = SyncResult()
        try:
            entries = await self._catalog.active_models()
        except Exception:
            log.exception("model registry poll failed; keeping the loaded models")
            self._m.registry_polls.labels("error").inc()
            self.last_sync_ok = False
            return result
        self._m.registry_polls.labels("ok").inc()
        self.last_sync_ok = True

        intra, _ = plan_threads(self._cpus, len(entries))
        old = {m.id: m for m in self.current.models}
        keep: list[LoadedModel] = []
        for entry in entries:
            existing = old.get(entry.id)
            if (
                existing is not None
                and existing.entry.onnx_key == entry.onnx_key
                and existing.intra_op_threads == intra
            ):
                # same files; the stage (and thereby publishing) may have changed
                keep.append(
                    existing
                    if existing.entry == entry
                    else replace(existing, entry=entry, intra_op_threads=intra)
                )
                continue
            if (entry.id, entry.onnx_key) in self._rejected:
                continue
            try:
                keep.append(await self._load(entry, intra))
            except Exception as e:
                if isinstance(e, ModelRejectedError):
                    self._rejected.add((entry.id, entry.onnx_key))
                self._fail(
                    entry, "rejected" if isinstance(e, ModelRejectedError) else "load", e, result
                )
                if existing is not None:
                    keep.append(existing)  # keep serving the previous session of this model
            else:
                result.loaded.append(entry.id)

        new_ids = {m.id for m in keep}
        result.unloaded = [i for i in old if i not in new_ids]
        self.current = ModelSet.of(keep)  # atomic swap
        self._update_gauges()
        await asyncio.to_thread(self._prune_cache, {e.id for e in entries})
        return result

    # ----- loading --------------------------------------------------------------------------

    def _fail(self, entry: ModelEntry, reason: str, exc: Exception, result: SyncResult) -> None:
        result.failed[entry.id] = f"{type(exc).__name__}: {exc}"
        self._m.model_load_failures.labels(reason).inc()
        log.error(
            "model %s %s (%s) not loaded: %s: %s",
            entry.name,
            entry.version,
            entry.id,
            type(exc).__name__,
            exc,
        )

    async def _load(self, entry: ModelEntry, intra: int) -> LoadedModel:
        directory = await self._download(entry)
        return await asyncio.to_thread(self._open, entry, directory, intra)

    def _open(self, entry: ModelEntry, directory: Path, intra: int) -> LoadedModel:
        card = self._read_card(directory)
        try:
            spec = FeatureSpec.load(directory / SPEC_FILE, require_tokenizer=True)
            kind = declared_kind(card, spec)
        except FeatureSpecError as e:
            raise ModelRejectedError(str(e)) from e
        tokenizer = self._open_tokenizer(directory, spec, card) if spec.raw_bytes else None
        try:
            session = self._factory(directory / ONNX_FILE, intra)
        except Exception as e:
            raise ModelRejectedError(f"ONNX Runtime cannot load the model: {e}") from e
        if spec.raw_bytes is not None:
            check_raw_shape(session.input_shape, spec.raw_bytes)
            input_kind: InputKind = "raw_bytes"
            self._warn_budget(entry, spec.raw_bytes, intra)
        else:
            input_kind = input_kind_of(session.input_shape, spec)
            if kind is not None and kind != input_kind:
                raise ModelRejectedError(
                    f"model card says {kind}, the ONNX input {session.input_shape} is {input_kind}"
                )
        return LoadedModel(
            entry,
            spec,
            session,
            input_kind,
            intra,
            output_semantics(entry.candidate_type, card),
            tokenizer,
        )

    @staticmethod
    def _open_tokenizer(
        directory: Path, spec: FeatureSpec, card: dict[str, object] | None
    ) -> RawTokenizer:
        """``tokenizer.json`` validated against the spec (hash, vocabulary size, layout)."""
        raw = spec.raw_bytes
        assert raw is not None
        tok_card = card.get("tokenizer") if isinstance(card, dict) else None
        if isinstance(tok_card, dict) and tok_card.get("sha256") not in (
            None,
            raw.tokenizer_sha256,
        ):
            raise ModelRejectedError(
                f"model card names tokenizer {tok_card.get('sha256')}, "
                f"the spec {raw.tokenizer_sha256}"
            )
        try:
            return RawTokenizer.load(directory, raw)
        except FeatureSpecError as e:
            raise ModelRejectedError(str(e)) from e
        except Exception as e:
            raise ModelRejectedError(f"tokenizer.json cannot be loaded: {e}") from e

    def _warn_budget(self, entry: ModelEntry, raw: RawBytesSpec, intra: int) -> None:
        anomaly = entry.candidate_type.startswith("raw_hier_anomaly")
        name = f"{entry.name}:{entry.version}"
        self._m.model_estimated_p99_ms.labels(name).set(
            estimate_p99_ms(raw.window.max_packets, intra, anomaly=anomaly)
        )
        mean_ms = estimate_mean_ms(raw.window.max_packets, intra, anomaly=anomaly)
        players = estimate_capacity_players(self._cpus, intra, raw.window.stride_seconds, mean_ms)
        self._m.model_estimated_capacity_players.labels(name).set(players)
        log.info(
            "raw-byte model %s: estimated mean %.1f ms per full window, stride %.1f s, "
            "about %.0f concurrent players on %d CPU(s) (this model alone)",
            name,
            mean_ms,
            raw.window.stride_seconds,
            players,
            self._cpus,
        )
        message = budget_warning(
            name, raw.window.max_packets, intra, self._budget_ms, anomaly=anomaly
        )
        if message is not None:
            log.warning(message)

    @staticmethod
    def _read_card(directory: Path) -> dict[str, object] | None:
        try:
            doc = json.loads((directory / MODEL_CARD_FILE).read_text())
        except (OSError, ValueError):
            return None
        return doc if isinstance(doc, dict) else None

    async def _download(self, entry: ModelEntry) -> Path:
        final = self._cache / entry.id
        if (final / ONNX_FILE).is_file() and (final / SPEC_FILE).is_file():
            return final
        onnx = await self._files.get(entry.onnx_key)
        spec = await self._files.get(spec_key(entry.onnx_key))
        try:  # optional: legacy models have no model card
            card: bytes | None = await self._files.get(card_key(entry.onnx_key))
        except Exception:
            card = None
        tokenizer = None
        if self._needs_tokenizer(spec, card):
            tokenizer = await self._files.get(tokenizer_key(entry.onnx_key))
        await asyncio.to_thread(self._publish_dir, final, onnx, spec, card, tokenizer)
        return final

    @staticmethod
    def _needs_tokenizer(spec: bytes, card: bytes | None) -> bool:
        """Raw-byte models ship ``tokenizer.json``; anything unparsable is left to validation."""
        try:
            card_doc = json.loads(card) if card is not None else None
            spec_doc = json.loads(spec)
        except ValueError:
            return False
        if isinstance(card_doc, dict) and card_doc.get("input_kind") == RAW_KIND:
            return True
        inputs = spec_doc.get("model_inputs") if isinstance(spec_doc, dict) else None
        return isinstance(inputs, dict) and isinstance(inputs.get("raw_bytes"), dict)

    def _publish_dir(
        self, final: Path, onnx: bytes, spec: bytes, card: bytes | None, tokenizer: bytes | None
    ) -> None:
        self._cache.mkdir(parents=True, exist_ok=True)
        tmp = self._cache / f".{final.name}.{uuid.uuid4().hex}.tmp"
        tmp.mkdir()
        try:
            (tmp / ONNX_FILE).write_bytes(onnx)
            (tmp / SPEC_FILE).write_bytes(spec)
            if card is not None:
                (tmp / MODEL_CARD_FILE).write_bytes(card)
            if tokenizer is not None:
                (tmp / TOKENIZER_FILE).write_bytes(tokenizer)
            shutil.rmtree(final, ignore_errors=True)
            os.replace(tmp, final)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _prune_cache(self, wanted: set[str]) -> None:
        if not self._cache.is_dir():
            return
        for child in self._cache.iterdir():
            if child.is_dir() and not child.name.startswith(".") and child.name not in wanted:
                shutil.rmtree(child, ignore_errors=True)

    def _update_gauges(self) -> None:
        for stage in ("shadow", "canary", "enforce"):
            n = sum(1 for m in self.current.models if m.entry.stage == stage)
            self._m.models_loaded.labels(stage).set(n)
        self._m.model_window_max_packets.clear()
        self._m.model_window_seconds.clear()
        for m in self.current.models:
            if m.raw_spec is not None:
                name = f"{m.entry.name}:{m.entry.version}"
                self._m.model_window_max_packets.labels(name).set(m.raw_spec.window.max_packets)
                self._m.model_window_seconds.labels(name).set(m.raw_spec.window.window_seconds)
