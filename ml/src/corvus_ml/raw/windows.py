"""Time windows over tokenized packets and the model input tensor.

* Windows are cut per (session, segment) and never cross a segment.
* ``window_seconds`` / ``stride_seconds`` define the grid (anchored at the first packet of the
  segment). A partial window at the end is kept only if it holds packets no earlier window saw.
* At most ``max_packets`` packets per window; on overflow the newest are kept (``recent``) or an
  evenly spaced subsample (``uniform``). Windows below ``min_packets`` are dropped.
* Optional macro windows group ``macro_windows`` consecutive windows (stride == window) for the
  second-level model; missing windows in a group stay padding.

The model input is one float32 array ``(batch, [macro,] packets, max_tokens + 1)``: token ids in
the first ``max_tokens`` channels (``0`` = ``[PAD]``) and the packet time in seconds since the
window start in the last one. Ids are exact in float32 (vocabulary below 2**24).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

from corvus_ml.raw.layout import (
    DIR_CB_ID,
    DIR_OTHER_ID,
    DIR_SB_ID,
    HEADER_LEN,
    NOPAYLOAD_ID,
    PKT_OTHER_ID,
    SEP_ID,
    TRUNC_ID,
    TokenLayout,
)
from corvus_ml.raw.spec import PacketConfig, RawBytesSpec, WindowConfig

_NS = 1_000_000_000


# --- row assembly ----------------------------------------------------------------------------


def assemble_rows(
    layout: TokenLayout,
    max_tokens: int,
    direction: np.ndarray,
    packet_type: np.ndarray,
    dt_id: np.ndarray,
    payload: np.ndarray,
    payload_len: np.ndarray,
    trunc: np.ndarray,
    nopayload: np.ndarray,
    proto_id: np.ndarray | None = None,
) -> np.ndarray:
    """Packet rows ``[DIR][PKT][DT][PROTO]? payload.. [TRUNC|NOPAYLOAD]? [SEP]`` as int16.

    ``proto_id`` holds a protocol token per row (``0`` = none). A payload that no longer fits next
    to the protocol token is cut and flagged ``[TRUNC]``.
    """
    n = len(direction)
    out = np.zeros((n, max_tokens), dtype=np.int16)
    if n == 0:
        return out
    out[:, 0] = np.where(
        direction == 1, DIR_SB_ID, np.where(direction == 2, DIR_CB_ID, DIR_OTHER_ID)
    )
    in_range = (packet_type >= 1) & (packet_type <= layout.packet_type_max)
    out[:, 1] = np.where(in_range, layout.pkt_base + packet_type.astype(np.int64) - 1, PKT_OTHER_ID)
    out[:, 2] = dt_id
    has_proto = np.zeros(n, dtype=bool) if proto_id is None else proto_id > 0
    if proto_id is not None:
        out[has_proto, HEADER_LEN] = proto_id[has_proto]
    start = HEADER_LEN + has_proto.astype(np.int64)
    avail = max_tokens - start - 2
    plen = np.minimum(payload_len, avail)
    trunc = trunc | (payload_len > avail)
    if payload.shape[1]:
        cols = np.arange(payload.shape[1])
        rows, cs = np.nonzero(cols[None, :] < plen[:, None])
        out[rows, start[rows] + cs] = payload[rows, cs]
    pos = start + plen
    flag = np.where(nopayload, NOPAYLOAD_ID, np.where(trunc, TRUNC_ID, 0))
    has_flag = flag > 0
    out[np.flatnonzero(has_flag), pos[has_flag]] = flag[has_flag]
    pos = pos + has_flag
    out[np.arange(n), pos] = SEP_ID
    return out


# --- one segment of one session -----------------------------------------------------------------


@dataclass
class PacketStream:
    """Tokenized packets of one (session, segment), ordered by time."""

    session_id: bytes
    segment: int
    game_context: str
    protocol: int | None
    offset_ns: np.ndarray  # int64
    direction: np.ndarray
    packet_type: np.ndarray
    dt_id: np.ndarray
    trunc: np.ndarray
    nopayload: np.ndarray
    payload: np.ndarray  # (n, payload_cap) int16
    payload_len: np.ndarray

    def __len__(self) -> int:
        return len(self.offset_ns)

    def rows(
        self, layout: TokenLayout, max_tokens: int, proto_id: int | None, idx: np.ndarray | None
    ) -> np.ndarray:
        sel = slice(None) if idx is None else idx
        n = len(self) if idx is None else len(idx)
        proto = None if proto_id is None else np.full(n, proto_id, dtype=np.int64)
        return assemble_rows(
            layout,
            max_tokens,
            self.direction[sel],
            self.packet_type[sel],
            self.dt_id[sel],
            self.payload[sel],
            self.payload_len[sel],
            self.trunc[sel],
            self.nopayload[sel],
            proto,
        )


def streams_from_table(
    table: pa.Table, packet: PacketConfig, protocols: dict[bytes, int | None] | None = None
) -> Iterator[PacketStream]:
    """Split a packet table into streams (sorted by session, segment, offset, seq)."""
    if table.num_rows == 0:
        return
    table = table.sort_by(
        [
            ("session_id", "ascending"),
            ("segment", "ascending"),
            ("offset_nanos", "ascending"),
            ("seq", "ascending"),
        ]
    )
    sid = table["session_id"].combine_chunks()
    sid_codes = pc.dictionary_encode(sid).indices.to_numpy(zero_copy_only=False)
    seg = table["segment"].to_numpy(zero_copy_only=False).astype(np.int64)
    change = np.ones(table.num_rows, dtype=bool)
    change[1:] = (sid_codes[1:] != sid_codes[:-1]) | (seg[1:] != seg[:-1])
    starts = np.flatnonzero(change)
    ends = np.append(starts[1:], table.num_rows)
    cap = packet.max_tokens_per_packet - HEADER_LEN - 2
    for lo, hi in zip(starts.tolist(), ends.tolist(), strict=True):
        part = table.slice(lo, hi - lo)
        lists = part["tokens"].combine_chunks()
        offs = lists.offsets.to_numpy()
        lens = np.diff(offs).astype(np.int64)
        values = lists.values.to_numpy(zero_copy_only=False)
        payload = np.zeros((hi - lo, cap), dtype=np.int16)
        if len(values):
            rows = np.repeat(np.arange(hi - lo), lens)
            cols = np.arange(len(values)) - np.repeat(offs[:-1] - offs[0], lens)
            payload[rows, cols] = values[offs[0] : offs[0] + lens.sum()]
        session = bytes(part["session_id"][0].as_py())
        yield PacketStream(
            session_id=session,
            segment=int(seg[lo]),
            game_context=str(part["game_context"][0].as_py()),
            protocol=None if protocols is None else protocols.get(session),
            offset_ns=part["offset_nanos"].to_numpy(zero_copy_only=False).astype(np.int64),
            direction=part["direction"].to_numpy(zero_copy_only=False),
            packet_type=part["packet_type"].to_numpy(zero_copy_only=False),
            dt_id=part["dt_id"].to_numpy(zero_copy_only=False),
            trunc=part["trunc"].to_numpy(zero_copy_only=False),
            nopayload=part["nopayload"].to_numpy(zero_copy_only=False),
            payload=payload,
            payload_len=lens,
        )


# --- time windows ------------------------------------------------------------------------------


def window_bounds(offset_ns: np.ndarray, cfg: WindowConfig) -> list[tuple[int, int, int, int]]:
    """``(window_index, lo, hi, start_ns)`` of the windows with at least ``min_packets`` packets."""
    n = len(offset_ns)
    if n == 0:
        return []
    window = int(round(cfg.window_seconds * _NS))
    stride = int(round(cfg.stride_seconds * _NS))
    first, last = int(offset_ns[0]), int(offset_ns[-1])
    out: list[tuple[int, int, int, int]] = []
    covered = None  # end of the latest emitted window
    k = 0
    while True:
        start = first + k * stride
        if start > last:
            break
        end = start + window
        full = end <= last + 1
        if not full and covered is not None and last < covered:
            break  # nothing the earlier windows have not seen
        lo = int(np.searchsorted(offset_ns, start, side="left"))
        hi = int(np.searchsorted(offset_ns, end, side="left"))
        if hi - lo >= cfg.min_packets:
            out.append((k, lo, hi, start))
        covered = end if covered is None else max(covered, end)
        if not full:
            break
        k += 1
    return out


def pick_packets(lo: int, hi: int, max_packets: int, overflow: str) -> np.ndarray:
    """Indices of the packets kept for a window ``[lo, hi)``."""
    n = hi - lo
    if n <= max_packets:
        return np.arange(lo, hi)
    if overflow == "recent":
        return np.arange(hi - max_packets, hi)
    pos = np.linspace(0, n - 1, max_packets).round().astype(np.int64)
    return lo + pos


@dataclass
class StreamWindow:
    window_index: int
    start_ns: int
    end_ns: int
    ids: np.ndarray  # (p, T) int16
    time_s: np.ndarray  # (p,) float32 seconds since window start
    n_packets: int  # before the max_packets cap


def stream_windows(
    stream: PacketStream, spec_layout: TokenLayout, packet: PacketConfig, cfg: WindowConfig
) -> list[StreamWindow]:
    T = packet.max_tokens_per_packet
    bounds = window_bounds(stream.offset_ns, cfg)
    if not bounds:
        return []
    proto = spec_layout.proto_id(stream.protocol)
    every = packet.proto_mode == "every"
    base = stream.rows(spec_layout, T, proto if every else None, None)
    out = []
    for k, lo, hi, start in bounds:
        idx = pick_packets(lo, hi, cfg.max_packets, cfg.overflow)
        ids = base[idx].copy()
        if packet.proto_mode == "first":
            ids[0:1] = stream.rows(spec_layout, T, proto, idx[:1])
        time_s = ((stream.offset_ns[idx] - start) / _NS).astype(np.float32)
        out.append(
            StreamWindow(
                k, start, start + int(round(cfg.window_seconds * _NS)), ids, time_s, hi - lo
            )
        )
    return out


# --- dataset of windows --------------------------------------------------------------------------


@dataclass
class RawWindows:
    """Windows (or macro windows) of many streams, padded to a common packet count."""

    session_id: np.ndarray  # object array of bytes
    segment: np.ndarray
    window_index: np.ndarray  # macro index when macro_windows > 1
    game_context: np.ndarray  # object array of str
    start_offset_nanos: np.ndarray
    ids: np.ndarray  # int16 (n, macro, packets, tokens)
    time_s: np.ndarray  # float32 (n, macro, packets)

    def __len__(self) -> int:
        return len(self.window_index)


def build_windows(
    streams: Sequence[PacketStream],
    layout: TokenLayout,
    packet: PacketConfig,
    cfg: WindowConfig,
) -> RawWindows:
    macro = cfg.macro_windows
    groups: list[tuple[PacketStream, int, dict[int, StreamWindow]]] = []
    for stream in streams:
        wins = stream_windows(stream, layout, packet, cfg)
        by_macro: dict[int, dict[int, StreamWindow]] = {}
        for w in wins:
            by_macro.setdefault(w.window_index // macro, {})[w.window_index % macro] = w
        groups += [(stream, m, members) for m, members in sorted(by_macro.items())]
    n = len(groups)
    p_max = max((len(w.time_s) for _, _, ms in groups for w in ms.values()), default=1)
    T = packet.max_tokens_per_packet
    ids = np.zeros((n, macro, p_max, T), dtype=np.int16)
    time_s = np.zeros((n, macro, p_max), dtype=np.float32)
    sid = np.empty(n, dtype=object)
    ctx = np.empty(n, dtype=object)
    segment = np.zeros(n, dtype=np.int64)
    index = np.zeros(n, dtype=np.int64)
    start = np.zeros(n, dtype=np.int64)
    for g, (stream, m, members) in enumerate(groups):
        sid[g], ctx[g], segment[g], index[g] = (
            stream.session_id, stream.game_context, stream.segment, m,
        )  # fmt: skip
        start[g] = min(w.start_ns for w in members.values())
        for slot, w in members.items():
            k = len(w.time_s)
            ids[g, slot, :k] = w.ids
            time_s[g, slot, :k] = w.time_s
    return RawWindows(sid, segment, index, ctx, start, ids, time_s)


def to_input(ids: np.ndarray, time_s: np.ndarray, macro_windows: int) -> np.ndarray:
    """The float32 model input; drops the macro axis when there is no hierarchy."""
    x = np.concatenate([ids.astype(np.float32), time_s[..., None]], axis=-1)
    return np.ascontiguousarray(x[:, 0] if macro_windows == 1 else x)


def from_input(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Inverse of :func:`to_input`: ``(ids int16, time_s float32)``."""
    return x[..., :-1].astype(np.int16), x[..., -1].astype(np.float32)


def stream_input(stream: PacketStream, spec: RawBytesSpec) -> np.ndarray:
    """Model input of all windows of one stream (serving helper); shape ``(n, [macro,] P, T+1)``."""
    rw = build_windows([stream], spec.layout, spec.packet, spec.window)
    return to_input(rw.ids, rw.time_s, spec.window.macro_windows)
