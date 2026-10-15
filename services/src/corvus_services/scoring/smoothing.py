"""Exponential moving average of window scores per (pseudonym, segment, model)."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

type EmaKey = tuple[bytes, int, str]  # pseudonym, segment, model id


@dataclass
class _Entry:
    value: float
    windows: int
    session_id: bytes


class EmaTracker:
    """``smoothed = alpha * score + (1 - alpha) * previous``; the first window is its own value.

    Bounded: the least recently updated entries are dropped beyond ``max_entries``. Entries of a
    finished session are released with :meth:`release_session`.
    """

    def __init__(self, alpha: float, max_entries: int = 100_000) -> None:
        if not 0.0 < alpha <= 1.0:
            raise ValueError("alpha must be in (0, 1]")
        self._alpha = alpha
        self._max = max_entries
        self._entries: OrderedDict[EmaKey, _Entry] = OrderedDict()
        self._by_session: dict[bytes, set[EmaKey]] = {}

    def __len__(self) -> int:
        return len(self._entries)

    def update(self, key: EmaKey, session_id: bytes, score: float) -> tuple[float, int]:
        """Fold one score in; returns ``(smoothed, windows)``."""
        entry = self._entries.get(key)
        if entry is None:
            entry = _Entry(score, 1, session_id)
            self._entries[key] = entry
            self._by_session.setdefault(session_id, set()).add(key)
            self._trim()
        else:
            entry.value = self._alpha * score + (1.0 - self._alpha) * entry.value
            entry.windows += 1
            self._entries.move_to_end(key)
        return entry.value, entry.windows

    def release_session(self, session_id: bytes) -> int:
        keys = self._by_session.pop(session_id, set())
        for key in keys:
            self._entries.pop(key, None)
        return len(keys)

    def _trim(self) -> None:
        while len(self._entries) > self._max:
            key, entry = self._entries.popitem(last=False)
            keys = self._by_session.get(entry.session_id)
            if keys is not None:
                keys.discard(key)
                if not keys:
                    del self._by_session[entry.session_id]
