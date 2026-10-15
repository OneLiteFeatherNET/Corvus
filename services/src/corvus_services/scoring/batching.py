"""Micro-batching of completed windows across sessions."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from corvus_ml.features.config import FeaturizerConfig
from corvus_ml.features.featurizer import CompletedWindow

from ..clock import Clock


@dataclass(frozen=True)
class PendingWindow:
    """A completed window plus what is needed to score and attribute it."""

    window: CompletedWindow
    config: FeaturizerConfig
    pseudonym: bytes
    key_id: str
    game_context: str


@dataclass
class Batch:
    windows: list[PendingWindow] = field(default_factory=list)
    # sessions whose smoothing state can be freed once ``windows`` are scored
    releases: list[bytes] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.windows or self.releases)


class MicroBatcher:
    """Collects windows; a batch is ready at ``max_size`` windows or once the oldest waited
    ``max_wait``. Time comes from the injected clock, so tests need no sleeping."""

    def __init__(self, max_size: int, max_wait: timedelta, clock: Clock) -> None:
        self._max = max_size
        self._wait = max_wait
        self._clock = clock
        self._items: deque[tuple[datetime, PendingWindow]] = deque()
        self._releases: list[bytes] = []

    def __len__(self) -> int:
        return len(self._items)

    def add(self, items: list[PendingWindow]) -> None:
        now = self._clock()
        self._items.extend((now, item) for item in items)

    def release(self, session_id: bytes) -> None:
        """Free the smoothing state of ``session_id`` after the windows queued so far."""
        self._releases.append(session_id)

    def ready(self) -> bool:
        if not self._items:
            return bool(self._releases)
        if len(self._items) >= self._max:
            return True
        return self._clock() - self._items[0][0] >= self._wait

    def take(self) -> Batch:
        """Up to ``max_size`` windows; the releases go with the batch that empties the queue."""
        n = min(self._max, len(self._items))
        windows = [self._items.popleft()[1] for _ in range(n)]
        releases: list[bytes] = []
        if not self._items:
            releases, self._releases = self._releases, []
        return Batch(windows, releases)
