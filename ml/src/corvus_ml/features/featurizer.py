"""The incremental featurizer shared by offline feature building and online scoring.

One :class:`SessionFeaturizer` per session. It is fed the frames of the session in ``seq`` order
(any direction, any segment) and emits :class:`CompletedWindow` objects as soon as a window's extent
is known. The offline pipeline and the scoring service run exactly this code, so they cannot drift.

Semantics (documented in docs/reference/features.md):

* All per-run state (previous frame, position, rotation, entity positions, buffered tokens) is reset
  when the segment changes; windows never cross a segment boundary. ``window_index`` continues per
  segment if a segment is entered again.
* A window holds at most ``window`` serverbound frames that start within ``max_duration_s`` of its
  first frame. It is emitted as soon as that is certain (enough frames arrived, or a frame beyond
  the duration limit arrived); :meth:`SessionFeaturizer.flush` emits the tail of the run.
* ``feed`` of a frame whose ``seq`` is not greater than the last one seen is ignored, which makes
  at-least-once redelivery harmless (offline input is already de-duplicated and ordered).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from corvus_ml.features.config import FeaturizerConfig
from corvus_ml.features.constants import AGGREGATE_NAMES, TOKEN_NAMES
from corvus_ml.features.frames import Frame
from corvus_ml.features.tokens import TokenExtractor
from corvus_ml.features.windows import window_aggregates

CUT_FULL = "full"
CUT_DURATION = "duration"
CUT_END = "end"


@dataclass(frozen=True, eq=False)
class CompletedWindow:
    """One finished window.

    ``aggregates`` is a float64 vector in ``AGGREGATE_NAMES`` order (NaN = null), ``tokens`` a
    float32 matrix of shape (n_frames, len(TOKEN_NAMES)) in ``TOKEN_NAMES`` column order, and
    ``token_seq`` / ``token_offset_nanos`` the uint64 identity of every token row.
    """

    session_id: bytes
    segment: int
    window_index: int
    start_offset_nanos: int
    end_offset_nanos: int
    first_seq: int
    last_seq: int
    n_frames: int
    cut: str
    aggregates: np.ndarray
    tokens: np.ndarray
    token_seq: np.ndarray
    token_offset_nanos: np.ndarray

    aggregate_names = AGGREGATE_NAMES
    token_names = TOKEN_NAMES

    def aggregate(self, name: str) -> float | None:
        """One aggregate by name; None where the table would hold null."""
        value = float(self.aggregates[AGGREGATE_NAMES.index(name)])
        return None if np.isnan(value) else value


class _Run:
    """Token buffer and window cutter of one segment run."""

    def __init__(
        self,
        session_id: bytes,
        segment: int,
        cfg: FeaturizerConfig,
        counters: dict[int, int],
        tick_end: bool | None,
    ) -> None:
        self._sid, self._segment, self._cfg, self._counters = session_id, segment, cfg, counters
        self._max_ns = int(cfg.max_duration_s * 1e9)
        self._tick_hint = tick_end
        self._ext = TokenExtractor(cfg)
        cap = max(4 * cfg.window, 1024)
        self._tokens = np.zeros((cap, len(TOKEN_NAMES)), dtype=np.float32)
        self._seq = np.zeros(cap, dtype=np.uint64)
        self._off = np.zeros(cap, dtype=np.uint64)
        self._n = 0  # rows stored
        self._base = 0  # absolute index of stored row 0
        self._lo = 0  # absolute index of the first frame of the next window
        self._prev_hi: int | None = None

    def push(self, fr: Frame) -> list[CompletedWindow]:
        if self._n == len(self._seq):
            self._grow()
        if not self._ext.step(fr, self._tokens[self._n]):
            return []
        self._seq[self._n] = fr.seq
        self._off[self._n] = fr.offset_nanos
        self._n += 1
        return self._cut(final=False)

    def finish(self) -> list[CompletedWindow]:
        return self._cut(final=True)

    def _grow(self) -> None:
        self._tokens = np.concatenate([self._tokens, np.zeros_like(self._tokens)])
        self._seq = np.concatenate([self._seq, np.zeros_like(self._seq)])
        self._off = np.concatenate([self._off, np.zeros_like(self._off)])

    def _compact(self) -> None:
        """Drop rows before the next window start once they make up a large share of the buffer."""
        drop = self._lo - self._base
        if drop < max(self._cfg.window, len(self._seq) // 2):
            return
        keep = self._n - drop
        self._tokens[:keep] = self._tokens[drop : self._n].copy()
        self._seq[:keep] = self._seq[drop : self._n].copy()
        self._off[:keep] = self._off[drop : self._n].copy()
        self._base, self._n = self._lo, keep

    def _cut(self, final: bool) -> list[CompletedWindow]:
        cfg = self._cfg
        avail = self._base + self._n  # absolute count of frames seen in this run
        if final and self._prev_hi is not None and self._prev_hi >= avail:
            return []  # the last window already reached the end of the run
        out: list[CompletedWindow] = []
        while self._lo < avail:
            lo = self._lo
            rel = lo - self._base
            limit = lo + cfg.window
            target = int(self._off[rel]) + self._max_ns
            hi_dur = lo + int(np.searchsorted(self._off[rel : self._n], target, side="right"))
            if not final and avail < limit and hi_dur >= avail:
                break  # extent still open: more frames can extend this window
            hi = min(limit, hi_dur, avail)
            if hi_dur < min(limit, avail):
                cut = CUT_DURATION
            elif limit <= avail:
                cut = CUT_FULL
            else:
                cut = CUT_END
            size = hi - lo
            if size >= cfg.min_frames:
                out.append(self._window(rel, rel + size, cut))
            self._prev_hi = hi
            self._lo = lo + max(1, size - int(size * cfg.overlap))
            if final and hi >= avail:
                break
        if not final:
            self._compact()
        return out

    def _window(self, a: int, b: int, cut: str) -> CompletedWindow:
        tokens = self._tokens[a:b].copy()
        seq, off = self._seq[a:b].copy(), self._off[a:b].copy()
        has_tick = self._ext.saw_tick_end if self._tick_hint is None else self._tick_hint
        index = self._counters.get(self._segment, 0)
        self._counters[self._segment] = index + 1
        first, last = int(off[0]), int(off[-1])
        return CompletedWindow(
            session_id=self._sid,
            segment=self._segment,
            window_index=index,
            start_offset_nanos=first,
            end_offset_nanos=last,
            first_seq=int(seq[0]),
            last_seq=int(seq[-1]),
            n_frames=b - a,
            cut=cut,
            aggregates=window_aggregates(tokens, first, last, has_tick),
            tokens=tokens,
            token_seq=seq,
            token_offset_nanos=off,
        )


class SessionFeaturizer:
    """Incremental featurizer of one session; see the module docstring for the semantics."""

    def __init__(self, session_id: bytes, config: FeaturizerConfig | None = None) -> None:
        self.session_id = session_id
        self.config = (config or FeaturizerConfig()).featurizer()
        self._run: _Run | None = None
        self._segment: int | None = None
        self._counters: dict[int, int] = {}
        self._last_seq = -1

    def begin_segment(self, segment: int, tick_end: bool | None = None) -> list[CompletedWindow]:
        """Close the current run (returning its tail windows) and start a new one.

        ``tick_end`` says whether the run sends ``CLIENT_TICK_END`` at all, which decides if
        ``tick_end_rate`` is defined (0.0 or a rate) or null. The offline build knows it from the
        whole run; leave it None online to infer it from the frames seen so far (a window that
        completes before the first tick end then reports null, see docs/reference/features.md).
        """
        out = self.flush()
        self._segment = segment
        self._run = _Run(self.session_id, segment, self.config, self._counters, tick_end)
        return out

    def feed(self, frame: Frame) -> list[CompletedWindow]:
        """Consume one frame; returns the windows that became complete (usually none)."""
        if frame.seq <= self._last_seq:
            return []
        self._last_seq = frame.seq
        out: list[CompletedWindow] = []
        if self._run is None or frame.segment != self._segment:
            out = self.begin_segment(frame.segment)
        assert self._run is not None
        out.extend(self._run.push(frame))
        return out

    def flush(self) -> list[CompletedWindow]:
        """Emit the tail windows of the current run and reset its state."""
        run, self._run, self._segment = self._run, None, None
        return [] if run is None else run.finish()
