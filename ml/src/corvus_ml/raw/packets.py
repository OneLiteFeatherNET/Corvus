"""Packet table: one row per frame with its header ids and payload BPE tokens.

This is the tokenized, window-agnostic form of the lake (``features/<snapshot>/raw/packets/``).
Windows are cut from it later (``corvus_ml.raw.windows``), so ``window_seconds``,
``stride_seconds`` and ``max_packets`` can change without re-reading the (encrypted) raw table.

The delta-t token is the gap to the previous frame of the same segment (``[DT_NONE]`` for the
first). The protocol token and the final row layout are added when a window is assembled.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np
import pyarrow as pa

from corvus_ml.features.schema import SESSION_ID_TYPE
from corvus_ml.raw.layout import HEADER_LEN, TokenLayout
from corvus_ml.raw.reader import SessionPackets
from corvus_ml.raw.spec import PacketConfig
from corvus_ml.raw.tokenizer import RawTokenizer

PACKETS_SCHEMA = pa.schema(
    [
        ("session_id", SESSION_ID_TYPE),
        ("segment", pa.uint32()),
        ("seq", pa.uint64()),
        ("offset_nanos", pa.uint64()),
        ("direction", pa.uint8()),
        ("packet_type", pa.uint32()),
        ("dt_id", pa.int16()),  # token id of the gap to the previous frame
        ("trunc", pa.bool_()),  # payload_original_size > recorded bytes, or cut by the token cap
        ("nopayload", pa.bool_()),  # no payload row (stripped / dropped / empty)
        ("tokens", pa.list_(pa.int16())),  # payload BPE token ids (at most payload_cap)
        ("game_context", pa.string()),
    ]
)


def payload_cap(packet: PacketConfig) -> int:
    """Payload tokens that fit next to the header, one flag token and ``[SEP]`` (no protocol)."""
    return packet.max_tokens_per_packet - HEADER_LEN - 2


def dt_ids(layout: TokenLayout, offsets_ns: np.ndarray, segment: np.ndarray) -> np.ndarray:
    """Delta-t token per frame; the first frame of each segment gets ``[DT_NONE]``."""
    n = len(offsets_ns)
    out = np.full(n, layout.dt_id(None), dtype=np.int16)
    if n < 2:
        return out
    edges = np.asarray(layout.dt_edges_ms(), dtype=np.float64)
    gap_ms = np.diff(offsets_ns.astype(np.int64)) / 1e6
    bucket = np.searchsorted(edges, gap_ms, side="right")
    same = segment[1:] == segment[:-1]
    out[1:] = np.where(same, layout.dt_base + bucket, layout.dt_id(None)).astype(np.int16)
    return out


def tokenize_payloads(
    tok: RawTokenizer,
    packet: PacketConfig,
    payloads: Sequence[bytes | None],
    original_sizes: Sequence[int],
) -> tuple[list[list[int]], list[bool], list[bool]]:
    """``(tokens, trunc, nopayload)`` per frame: the single tokenization path of the packet table.

    Offline (``session_packet_table``) and online (``corvus_ml.raw.streaming``) both go through
    this function so a frame gets the same ids in training and in serving. A frame without a
    payload (``None`` or empty) is ``nopayload``; ``trunc`` means the plugin recorded fewer bytes
    than the packet had, or the payload has more BPE tokens than fit into the row.
    """
    cap = payload_cap(packet)
    present = [i for i, p in enumerate(payloads) if p]
    encoded = dict(
        zip(present, tok.encode_batch([payloads[i] or b"" for i in present]), strict=True)
    )
    tokens: list[list[int]] = []
    trunc: list[bool] = []
    nopay: list[bool] = []
    for i, size in enumerate(original_sizes):
        ids = encoded.get(i)
        if ids is None:
            tokens.append([])
            trunc.append(False)
            nopay.append(True)
            continue
        recorded = len(payloads[i] or b"")
        tokens.append(ids[:cap])
        trunc.append(bool(size > recorded) or len(ids) > cap)
        nopay.append(False)
    return tokens, trunc, nopay


def session_packet_table(
    sp: SessionPackets,
    tok: RawTokenizer,
    packet: PacketConfig,
    contexts: Mapping[int, str],
) -> pa.Table | None:
    """Tokenize the frames of one session; frames in segments without a game context are skipped."""
    n = len(sp)
    if n == 0:
        return None
    keep = np.array([int(s) in contexts for s in sp.segment.tolist()], dtype=bool)
    if not keep.any():
        return None
    dt = dt_ids(tok.layout, sp.offset_nanos, sp.segment)
    idx = np.flatnonzero(keep)
    tokens, trunc, nopay = tokenize_payloads(
        tok,
        packet,
        [sp.payloads[i] for i in idx.tolist()],
        sp.payload_original_size[idx].tolist(),
    )
    ctx = [contexts[int(s)] for s in sp.segment[idx].tolist()]
    return pa.table(
        {
            "session_id": pa.array([sp.session_id] * len(idx), SESSION_ID_TYPE),
            "segment": pa.array(sp.segment[idx], pa.uint32()),
            "seq": pa.array(sp.seq[idx], pa.uint64()),
            "offset_nanos": pa.array(sp.offset_nanos[idx], pa.uint64()),
            "direction": pa.array(sp.direction[idx], pa.uint8()),
            "packet_type": pa.array(sp.packet_type[idx], pa.uint32()),
            "dt_id": pa.array(dt[idx], pa.int16()),
            "trunc": pa.array(trunc, pa.bool_()),
            "nopayload": pa.array(nopay, pa.bool_()),
            "tokens": pa.array(tokens, pa.list_(pa.int16())),
            "game_context": pa.array(ctx, pa.string()),
        },
        schema=PACKETS_SCHEMA,
    )
