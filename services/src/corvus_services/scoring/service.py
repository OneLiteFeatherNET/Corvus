"""The scoring pipeline: capture envelopes in, micro-batched scores out.

Per message: decode, keep the session/segment state, feed the frames into the incremental
featurizers, hand the completed windows to the micro-batcher and ack. Acks happen right after the
frames were fed (scoring never replays history, so there is nothing to redeliver for).

A session the scorer never saw start (the consumer delivers only new messages) is looked up in the
catalog, which ingest keeps up to date; if the catalog does not know it either the batch is skipped
and counted. Lookups that fail are remembered for a short time so a burst of batches does not hit
the database once per message.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Any

from corvus_ml.features.config import FeaturizerConfig
from google.protobuf.message import DecodeError

from ..clock import Clock
from ..ports import Message
from ..proto import pb
from .batching import Batch, MicroBatcher, PendingWindow
from .frames import feature_frame
from .metrics import ScoreMetrics
from .models import ModelSet
from .ports import ScoreCatalog, SessionLookup
from .raw import RawWindow, raw_frame
from .scorer import Scorer
from .sessions import ConfiguredWindow, SessionState, SessionTracker
from .settings import ScoreSettings

log = logging.getLogger("corvus.score")


class LookupFailedError(Exception):
    """The catalog could not be queried; the message is retried a few times, then skipped."""


def tick_end_hint(observed_protocol: int, min_protocol: int) -> bool | None:
    """Whether the run sends CLIENT_TICK_END, as far as it is known beforehand: clients below
    ``min_protocol`` cannot send it (False); newer ones may (None, inferred from the frames)."""
    return False if observed_protocol < min_protocol else None


class ScoringService:
    def __init__(
        self,
        settings: ScoreSettings,
        catalog: ScoreCatalog,
        models: Any,
        tracker: SessionTracker,
        batcher: MicroBatcher,
        scorer: Scorer,
        clock: Clock,
        metrics: ScoreMetrics,
    ) -> None:
        self._s = settings
        self._catalog = catalog
        self._models = models  # ModelManager or anything with ``current: ModelSet``
        self._tracker = tracker
        self._batcher = batcher
        self._scorer = scorer
        self._clock = clock
        self._m = metrics
        self._missing: OrderedDict[bytes, datetime] = OrderedDict()  # session -> retry not before
        self._inflight: asyncio.Task[None] | None = None

    @property
    def model_set(self) -> ModelSet:
        current: ModelSet = self._models.current
        return current

    # ----- messages ---------------------------------------------------------------------------

    async def handle(self, msg: Message) -> None:
        """Process and ack (or nak/term) one message. Never raises."""
        try:
            env = pb.Envelope.FromString(msg.data)
        except DecodeError:
            self._m.messages.labels("decode_error").inc()
            await self._settle(msg.term())
            return
        try:
            retry = await self._dispatch(env, msg)
        except LookupFailedError:
            retry = msg.delivery_count < self._s.unknown_session_max_deliver
            if not retry:
                self._m.unknown_session_skips.inc()
        except Exception:
            log.exception("failed to handle message %s", msg.msg_id)
            self._m.messages.labels("error").inc()
            retry = False
        if retry:
            self._m.messages.labels("retry").inc()
            await self._settle(msg.nak(self._s.nak_delay_s))
        else:
            await self._settle(msg.ack())
        self._dispatch_ready()

    @staticmethod
    async def _settle(op: Any) -> None:
        try:
            await op
        except Exception:
            log.warning("could not acknowledge a message", exc_info=True)

    async def _dispatch(self, env: Any, msg: Message) -> bool:
        """Returns True if the message should be redelivered later."""
        body = env.WhichOneof("body")
        if body == "session_start":
            self._on_session_start(env.session_start)
        elif body == "segment_start":
            return await self._on_segment_start(env.segment_start, msg)
        elif body == "batch":
            return await self._on_batch(env.batch, msg)
        elif body == "session_end":
            self._on_session_end(bytes(env.session_end.session_id))
        else:
            self._m.messages.labels("empty").inc()
            return False
        self._m.messages.labels(body).inc()
        return False

    def _on_session_start(self, s: Any) -> None:
        if bytes(s.session_id) in self._tracker:
            return  # redelivered SessionStart: keep the featurizer state
        state = SessionState(
            session_id=bytes(s.session_id),
            pseudonym=bytes(s.player_pseudonym),
            key_id=s.pseudonym_key_id,
            observed_protocol=s.observed_protocol_version,
            client_protocol=s.client_protocol_version,
        )
        self._track(state)

    async def _on_segment_start(self, s: Any, msg: Message) -> bool:
        sid = bytes(s.session_id)
        state = self._tracker.get(sid)
        if state is None:
            state = await self._resolve(sid)
            if state is None:
                return self._unknown()
        state.segments[s.segment] = s.game_context
        hint = tick_end_hint(state.observed_protocol, self._s.score_tick_end_min_protocol)
        self._queue(state, state.begin_segment(s.segment, hint))
        self._m.messages.labels("segment_start").inc()
        return False

    async def _on_batch(self, b: Any, msg: Message) -> bool:
        sid = bytes(b.session_id)
        state = self._tracker.get(sid)
        if state is None:
            state = await self._resolve(sid)
        if state is not None and b.segment not in state.segments:
            await self._refresh_segments(state)
        if state is None or b.segment not in state.segments:
            return self._unknown()
        game_context = state.segments[b.segment]
        models = self.model_set
        configs = models.configs_for(game_context)
        raw_specs = models.raw_specs_for(game_context)
        if not configs and not raw_specs:
            self._m.messages.labels("no_model").inc()
            return False
        completed: list[ConfiguredWindow] = []
        if configs:
            frames = [feature_frame(sid, b.segment, f) for f in b.frames]
            hint = tick_end_hint(state.observed_protocol, self._s.score_tick_end_min_protocol)
            completed += state.feed(b.segment, frames, configs, hint)
        if raw_specs:
            # plaintext payloads: tokenized on arrival, only token ids stay in the windower
            completed += state.feed_raw(
                b.segment,
                [raw_frame(f) for f in b.frames],
                raw_specs,
                game_context,
                self._s.score_raw_max_buffered_frames,
            )
            self._m.raw_frames_dropped.inc(state.raw_frames_dropped)
            state.raw_frames_dropped = 0
        self._m.frames_fed.inc(len(b.frames))
        self._queue(state, completed)
        self._m.messages.labels("batch").inc()
        return False

    def _on_session_end(self, session_id: bytes) -> None:
        state = self._tracker.pop(session_id)
        self._missing.pop(session_id, None)
        self._m.sessions_tracked.set(len(self._tracker))
        if state is not None:
            self._queue(state, state.flush())
        self._batcher.release(session_id)

    # ----- session state ----------------------------------------------------------------------

    def _track(self, state: SessionState) -> None:
        self._missing.pop(state.session_id, None)
        for evicted in self._tracker.put(state):
            self._evict(evicted, "lru")
        self._m.sessions_tracked.set(len(self._tracker))

    def _evict(self, state: SessionState, reason: str) -> None:
        self._m.session_evictions.labels(reason).inc()
        self._queue(state, state.flush())
        self._batcher.release(state.session_id)

    async def _resolve(self, session_id: bytes) -> SessionState | None:
        """Catalog fallback for a session the scorer did not see start."""
        retry_at = self._missing.get(session_id)
        if retry_at is not None and self._clock() < retry_at:
            return None
        lookup = await self._lookup(session_id)
        if lookup is None:
            self._m.catalog_fallbacks.labels("miss").inc()
            self._remember_missing(session_id)
            return None
        self._m.catalog_fallbacks.labels("hit").inc()
        state = SessionState(
            session_id,
            lookup.pseudonym,
            lookup.key_id,
            lookup.observed_protocol,
            dict(lookup.segments),
            lookup.client_protocol,
        )
        self._track(state)
        return state

    async def _lookup(self, session_id: bytes) -> SessionLookup | None:
        try:
            return await self._catalog.lookup_session(session_id)
        except Exception as e:
            log.warning("catalog lookup of session %s failed: %s", session_id.hex(), e)
            self._m.catalog_fallbacks.labels("error").inc()
            raise LookupFailedError from e

    async def _refresh_segments(self, state: SessionState) -> None:
        """A segment the scorer did not see start: ingest may have written it by now."""
        key = state.session_id
        retry_at = self._missing.get(key)
        if retry_at is not None and self._clock() < retry_at:
            return
        lookup = await self._lookup(key)
        if lookup is None:
            self._remember_missing(key)
            return
        for segment, ctx in lookup.segments.items():
            state.segments.setdefault(segment, ctx)
        self._missing.pop(key, None)

    def _remember_missing(self, key: bytes) -> None:
        self._missing[key] = self._clock() + timedelta(seconds=self._s.score_lookup_retry_s)
        self._missing.move_to_end(key)
        while len(self._missing) > self._s.score_lookup_cache_max:
            self._missing.popitem(last=False)

    def _unknown(self) -> bool:
        self._m.unknown_session_skips.inc()
        self._m.messages.labels("unknown_session").inc()
        return False

    # ----- windows and batches ----------------------------------------------------------------

    def _queue(self, state: SessionState, completed: list[ConfiguredWindow]) -> None:
        items: list[PendingWindow] = []
        for cfg, window in completed:
            ctx = state.segments.get(window.segment)
            if ctx is None:
                self._m.windows_skipped.labels("no_context").inc()
                continue
            if isinstance(window, RawWindow):
                self._m.raw_window_packets.observe(window.n_packets)
            items.append(PendingWindow(window, cfg, state.pseudonym, state.key_id, ctx))
        if completed:
            self._m.windows_completed.inc(len(completed))
        if items:
            self._batcher.add(items)
            self._m.pending_windows.set(len(self._batcher))

    def tick(self) -> None:
        """Periodic: evict idle sessions and dispatch a batch whose wait elapsed."""
        for state in self._tracker.evict_idle():
            self._evict(state, "idle")
        self._m.sessions_tracked.set(len(self._tracker))
        self._dispatch_ready()

    def _dispatch_ready(self) -> None:
        """Start scoring the next batch if one is ready and none is running (single in-flight
        batch keeps the per-key smoothing order)."""
        if self._inflight is not None and not self._inflight.done():
            return
        if not self._batcher.ready():
            return
        batch = self._batcher.take()
        self._m.pending_windows.set(len(self._batcher))
        self._inflight = asyncio.get_running_loop().create_task(self._run_batch(batch))

    async def _run_batch(self, batch: Batch) -> None:
        await self._scorer.score(batch)

    async def backpressure(self) -> None:
        """Wait for the running batch if too many windows are queued behind it."""
        if len(self._batcher) >= self._s.score_pending_windows_max and self._inflight is not None:
            await asyncio.shield(self._inflight)

    async def drain(self) -> None:
        """Score every pending window (also those not yet due)."""
        while True:
            if self._inflight is not None and not self._inflight.done():
                await asyncio.shield(self._inflight)
                continue
            batch = self._batcher.take()
            if not batch:
                return
            self._m.pending_windows.set(len(self._batcher))
            await self._scorer.score(batch)

    # ----- introspection for the model set ----------------------------------------------------

    def configs(self, game_context: str) -> frozenset[FeaturizerConfig]:
        return self.model_set.configs_for(game_context)
