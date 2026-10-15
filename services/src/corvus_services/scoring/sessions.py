"""Per-session featurizer state: a bounded LRU with idle eviction."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from corvus_ml.features.config import FeaturizerConfig
from corvus_ml.features.featurizer import CompletedWindow, SessionFeaturizer
from corvus_ml.features.frames import Frame

from ..clock import Clock

type ConfiguredWindow = tuple[FeaturizerConfig, CompletedWindow]


@dataclass
class SessionState:
    """Everything the scorer keeps for one session."""

    session_id: bytes
    pseudonym: bytes
    key_id: str
    observed_protocol: int
    segments: dict[int, str] = field(default_factory=dict)  # segment -> game_context
    last_seen: datetime = datetime.min.replace(tzinfo=UTC)
    # one incremental featurizer per distinct window configuration of the models in use
    _featurizers: dict[FeaturizerConfig, SessionFeaturizer] = field(default_factory=dict)
    _fz_segment: dict[FeaturizerConfig, int] = field(default_factory=dict)

    def begin_segment(self, segment: int, tick_end: bool | None) -> list[ConfiguredWindow]:
        """Start ``segment`` in every featurizer; returns the tail windows of the previous run."""
        out: list[ConfiguredWindow] = []
        for cfg, fz in self._featurizers.items():
            out.extend((cfg, w) for w in fz.begin_segment(segment, tick_end))
            self._fz_segment[cfg] = segment
        return out

    def feed(
        self,
        segment: int,
        frames: Iterable[Frame],
        configs: Iterable[FeaturizerConfig],
        tick_end: bool | None,
    ) -> list[ConfiguredWindow]:
        """Feed frames of ``segment`` to the featurizer of every config in ``configs``."""
        batch = list(frames)
        out: list[ConfiguredWindow] = []
        for cfg in configs:
            fz = self._featurizers.get(cfg)
            if fz is None:
                fz = self._featurizers[cfg] = SessionFeaturizer(self.session_id, cfg)
            if self._fz_segment.get(cfg) != segment:
                out.extend((cfg, w) for w in fz.begin_segment(segment, tick_end))
                self._fz_segment[cfg] = segment
            for frame in batch:
                out.extend((cfg, w) for w in fz.feed(frame))
        return out

    def flush(self) -> list[ConfiguredWindow]:
        """Tail windows of every featurizer (session end or eviction); frees the featurizers."""
        out: list[ConfiguredWindow] = []
        for cfg, fz in self._featurizers.items():
            out.extend((cfg, w) for w in fz.flush())
        self._featurizers.clear()
        self._fz_segment.clear()
        return out

    @property
    def featurizer_count(self) -> int:
        return len(self._featurizers)


class SessionTracker:
    """LRU of :class:`SessionState` bounded by count and idle time."""

    def __init__(self, max_sessions: int, idle: timedelta, clock: Clock) -> None:
        self._max = max_sessions
        self._idle = idle
        self._clock = clock
        self._states: OrderedDict[bytes, SessionState] = OrderedDict()

    def __len__(self) -> int:
        return len(self._states)

    def __contains__(self, session_id: bytes) -> bool:
        return session_id in self._states

    def get(self, session_id: bytes) -> SessionState | None:
        state = self._states.get(session_id)
        if state is not None:
            state.last_seen = self._clock()
            self._states.move_to_end(session_id)
        return state

    def put(self, state: SessionState) -> list[SessionState]:
        """Insert (or replace); returns the states evicted to stay within the bound."""
        state.last_seen = self._clock()
        self._states[state.session_id] = state
        self._states.move_to_end(state.session_id)
        evicted: list[SessionState] = []
        while len(self._states) > self._max:
            _, oldest = self._states.popitem(last=False)
            evicted.append(oldest)
        return evicted

    def pop(self, session_id: bytes) -> SessionState | None:
        return self._states.pop(session_id, None)

    def evict_idle(self) -> list[SessionState]:
        """Remove and return the states not touched for longer than the idle limit."""
        cutoff = self._clock() - self._idle
        stale = [sid for sid, s in self._states.items() if s.last_seen < cutoff]
        return [self._states.pop(sid) for sid in stale]
