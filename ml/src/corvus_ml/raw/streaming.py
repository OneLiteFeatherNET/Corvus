"""Incremental window builder for online scoring of raw-byte models.

The offline path tokenizes a whole session (``session_packet_table``) and cuts windows from the
packet table (``windows.stream_windows``). :class:`SegmentWindower` produces exactly the same
windows from frames that arrive in batches, so a scorer feeds the model what it was trained on:

* frames are tokenized once, on arrival, with :func:`corvus_ml.raw.packets.tokenize_payloads`; the
  payload bytes are not stored, only their token ids;
* the gap token (``[DT_*]``) comes from the previous frame of the segment, as in training;
* a window is emitted as soon as a frame at or after its end was seen (its content is final then);
  :meth:`SegmentWindower.flush` decides the windows that are only complete at the end of the
  segment with the same rule as :func:`corvus_ml.raw.windows.window_bounds`;
* frames older than the start of the next undecided window are dropped, so the buffer holds about
  ``window_seconds + stride_seconds`` of traffic (capped by ``max_buffered``).

With macro windows (``macro_window_seconds``) the windows are grouped like in training: group
``m`` holds windows ``m * macro .. m * macro + macro - 1`` and is emitted when the first window of
the next group is complete (or on flush); missing windows stay padding.

Pure numpy; the tokenizer is any object with ``layout`` and ``encode_batch`` (a
:class:`~corvus_ml.raw.tokenizer.RawTokenizer`), so this module imports neither ``tokenizers``
nor torch.

Frames must arrive in ``seq`` order with non-decreasing ``offset_nanos`` (the capture stream
does); a frame whose ``seq`` is not above the last one (redelivery) is ignored and an offset that
goes backwards is clamped to the previous one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from corvus_ml.raw.layout import HEADER_LEN, TokenLayout
from corvus_ml.raw.packets import dt_ids, payload_cap, tokenize_payloads
from corvus_ml.raw.spec import RawBytesSpec
from corvus_ml.raw.windows import (
    _NS,
    PacketStream,
    StreamWindow,
    base_rows,
    cut_window,
    to_input,
)


class FrameEncoder(Protocol):
    """What the windower needs from a tokenizer."""

    @property
    def layout(self) -> TokenLayout: ...

    def encode_batch(self, payloads: list[bytes]) -> list[list[int]]: ...


@dataclass(frozen=True)
class RawFrame:
    """One captured frame as the scorer sees it; ``payload`` is plaintext (never stored here)."""

    seq: int
    offset_nanos: int
    direction: int
    packet_type: int
    payload: bytes | None
    payload_original_size: int = 0


@dataclass(frozen=True)
class ModelWindow:
    """One model input: ``x`` is ``(packets, T + 1)``, or ``(macro, packets, T + 1)``."""

    segment: int
    window_index: int  # macro index when the model has macro windows
    start_offset_nanos: int
    end_offset_nanos: int
    x: np.ndarray
    n_packets: int  # packets before the max_packets cap (the largest member of a macro window)


@dataclass
class WindowerStats:
    frames: int = 0
    duplicates: int = 0  # seq not above the last one: ignored
    clamped_offsets: int = 0
    dropped_overflow: int = 0  # buffer cap hit: oldest frames dropped
    windows: int = 0


class SegmentWindower:
    """Windows of one (session, segment) for one :class:`RawBytesSpec`."""

    def __init__(
        self,
        spec: RawBytesSpec,
        tokenizer: FrameEncoder,
        session_id: bytes,
        segment: int,
        game_context: str,
        protocol: int | None,
        max_buffered: int | None = None,
    ) -> None:
        self._spec = spec
        self._tok = tokenizer
        self._session_id = session_id
        self._segment = segment
        self._context = game_context
        self._protocol = protocol
        self._max_buffered = max_buffered
        self._window_ns = int(round(spec.window.window_seconds * _NS))
        self._stride_ns = int(round(spec.window.stride_seconds * _NS))
        self._cap = payload_cap(spec.packet)
        self.stats = WindowerStats()
        # decision state of the window grid (see ``windows.window_bounds``)
        self._first: int | None = None
        self._k = 0
        self._covered: int | None = None
        self._done = False
        self._last_seq = -1
        self._last_offset: int | None = None
        # buffer: tokenized frames from the start of window ``_k`` on
        self._offset: list[int] = []
        self._direction: list[int] = []
        self._ptype: list[int] = []
        self._dt: list[int] = []
        self._trunc: list[bool] = []
        self._nopay: list[bool] = []
        self._tokens: list[list[int]] = []
        # macro grouping
        self._group: int | None = None
        self._members: dict[int, StreamWindow] = {}

    # --- public --------------------------------------------------------------------------------

    @property
    def buffered(self) -> int:
        """Tokenized frames held (never payload bytes)."""
        return len(self._offset)

    def feed(self, frames: Sequence[RawFrame]) -> list[ModelWindow]:
        """Add frames; returns the windows that became final."""
        if self._done:
            return []  # the segment was flushed
        fresh = self._accept(frames)
        if not fresh:
            return []
        self._append(fresh)
        return self._advance(final=False)

    def flush(self) -> list[ModelWindow]:
        """End of the segment: the remaining windows, then free the buffer."""
        out = self._advance(final=True)
        out.extend(self._emit_group())
        self._clear()
        return out

    # --- ingestion -----------------------------------------------------------------------------

    def _accept(self, frames: Sequence[RawFrame]) -> list[RawFrame]:
        fresh: list[RawFrame] = []
        for f in frames:
            if f.seq <= self._last_seq:
                self.stats.duplicates += 1
                continue
            self._last_seq = f.seq
            fresh.append(f)
        return fresh

    def _append(self, fresh: list[RawFrame]) -> None:
        offsets = np.fromiter((f.offset_nanos for f in fresh), dtype=np.int64, count=len(fresh))
        floor = self._last_offset if self._last_offset is not None else int(offsets[0])
        clamped = np.maximum.accumulate(np.maximum(offsets, floor))
        self.stats.clamped_offsets += int((clamped != offsets).sum())
        offsets = clamped
        # gap tokens: the previous frame of the segment is the first element, then dropped again
        if self._last_offset is None:
            dt = dt_ids(self._tok.layout, offsets, np.zeros(len(offsets), dtype=np.int64))
        else:
            joined = np.concatenate([[self._last_offset], offsets])
            dt = dt_ids(self._tok.layout, joined, np.zeros(len(joined), dtype=np.int64))[1:]
        tokens, trunc, nopay = tokenize_payloads(
            self._tok,
            self._spec.packet,
            [f.payload for f in fresh],
            [f.payload_original_size for f in fresh],
        )
        self._offset.extend(offsets.tolist())
        self._direction.extend(f.direction for f in fresh)
        self._ptype.extend(f.packet_type for f in fresh)
        self._dt.extend(dt.tolist())
        self._trunc.extend(trunc)
        self._nopay.extend(nopay)
        self._tokens.extend(tokens)
        self._last_offset = int(offsets[-1])
        if self._first is None:
            self._first = int(offsets[0])
        self.stats.frames += len(fresh)
        over = 0 if self._max_buffered is None else len(self._offset) - self._max_buffered
        if over > 0:
            self._drop(over)
            self.stats.dropped_overflow += over

    # --- windows -------------------------------------------------------------------------------

    def _advance(self, *, final: bool) -> list[ModelWindow]:
        """Decide the windows whose content is final; the grid rule is ``window_bounds``."""
        if self._first is None or self._done or self._last_offset is None:
            return []
        cfg = self._spec.window
        last = self._last_offset
        bounds: list[tuple[int, int, int, int]] = []
        offsets = np.asarray(self._offset, dtype=np.int64)
        while True:
            start = self._first + self._k * self._stride_ns
            if start > last:
                if final:
                    self._done = True
                break
            end = start + self._window_ns
            complete = end <= last  # a frame at or after the end exists: nothing more can join
            if not complete and not final:
                break
            full = end <= last + 1
            if not full and self._covered is not None and last < self._covered:
                self._done = True
                break
            lo = int(np.searchsorted(offsets, start, side="left"))
            hi = int(np.searchsorted(offsets, end, side="left"))
            if hi - lo >= cfg.min_packets:
                bounds.append((self._k, lo, hi, start))
            self._covered = end if self._covered is None else max(self._covered, end)
            if not full:
                self._done = True
                break
            self._k += 1
        if not bounds and not final:
            self._trim()
            return []
        out = self._cut(bounds)
        self._trim()
        return out

    def _cut(self, bounds: list[tuple[int, int, int, int]]) -> list[ModelWindow]:
        if not bounds:
            return []
        stream = self._stream()
        base = base_rows(stream, self._spec.layout, self._spec.packet)
        out: list[ModelWindow] = []
        for bound in bounds:
            w = cut_window(
                stream, self._spec.layout, self._spec.packet, self._spec.window, base, bound
            )
            self.stats.windows += 1
            out.extend(self._group_window(w))
        return out

    def _stream(self) -> PacketStream:
        n = len(self._offset)
        payload = np.zeros((n, self._cap), dtype=np.int16)
        lens = np.fromiter((len(t) for t in self._tokens), dtype=np.int64, count=n)
        for i, t in enumerate(self._tokens):
            if t:
                payload[i, : len(t)] = t
        return PacketStream(
            session_id=self._session_id,
            segment=self._segment,
            game_context=self._context,
            protocol=self._protocol,
            offset_ns=np.asarray(self._offset, dtype=np.int64),
            direction=np.asarray(self._direction, dtype=np.uint8),
            packet_type=np.asarray(self._ptype, dtype=np.uint32),
            dt_id=np.asarray(self._dt, dtype=np.int16),
            trunc=np.asarray(self._trunc, dtype=bool),
            nopayload=np.asarray(self._nopay, dtype=bool),
            payload=payload,
            payload_len=lens,
        )

    # --- macro grouping ------------------------------------------------------------------------

    def _group_window(self, w: StreamWindow) -> list[ModelWindow]:
        macro = self._spec.window.macro_windows
        if macro == 1:
            x = to_input(w.ids[None, None], w.time_s[None, None], 1)[0]
            return [
                ModelWindow(self._segment, w.window_index, w.start_ns, w.end_ns, x, w.n_packets)
            ]
        group = w.window_index // macro
        out: list[ModelWindow] = []
        if self._group is not None and group != self._group:
            out = self._emit_group()
        self._group = group
        self._members[w.window_index % macro] = w
        return out

    def _emit_group(self) -> list[ModelWindow]:
        if self._group is None or not self._members:
            self._members = {}
            self._group = None
            return []
        macro = self._spec.window.macro_windows
        T = self._spec.packet.max_tokens_per_packet
        p_max = max(len(w.time_s) for w in self._members.values())
        ids = np.zeros((1, macro, p_max, T), dtype=np.int16)
        time_s = np.zeros((1, macro, p_max), dtype=np.float32)
        for slot, w in self._members.items():
            k = len(w.time_s)
            ids[0, slot, :k] = w.ids
            time_s[0, slot, :k] = w.time_s
        x = to_input(ids, time_s, macro)[0]
        members = self._members.values()
        out = ModelWindow(
            self._segment,
            self._group,
            min(w.start_ns for w in members),
            max(w.end_ns for w in members),
            x,
            max(w.n_packets for w in members),
        )
        self._members = {}
        self._group = None
        return [out]

    # --- buffer --------------------------------------------------------------------------------

    def _trim(self) -> None:
        """Drop the frames before the start of the next undecided window."""
        if self._first is None:
            return
        if self._done:
            self._drop(len(self._offset))
            return
        cut = self._first + self._k * self._stride_ns
        n = int(np.searchsorted(np.asarray(self._offset, dtype=np.int64), cut, side="left"))
        if n:
            self._drop(n)

    def _drop(self, n: int) -> None:
        for col in (
            self._offset, self._direction, self._ptype, self._dt, self._trunc, self._nopay,
            self._tokens,
        ):  # fmt: skip
            del col[:n]

    def _clear(self) -> None:
        self._drop(len(self._offset))
        self._members = {}
        self._group = None


__all__ = ["HEADER_LEN", "FrameEncoder", "ModelWindow", "RawFrame", "SegmentWindower"]
