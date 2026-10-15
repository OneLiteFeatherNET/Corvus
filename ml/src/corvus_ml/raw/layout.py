"""Token layout of the raw-byte tokenizer: reserved ids in front, byte-level BPE tokens after.

Pure Python. The layout is derived from a handful of parameters (:class:`TokenLayout`) that are
stored in the feature spec, so the scorer rebuilds exactly the ids the model was trained with.

Packet layout (one packet = one row of at most ``max_tokens_per_packet`` ids)::

    [DIR_*] [PKT_<id>] [DT_<bucket>] [PROTO_<n>]? <payload BPE tokens...> [TRUNC]? [SEP]
    [DIR_*] [PKT_<id>] [DT_<bucket>] [PROTO_<n>]? [NOPAYLOAD] [SEP]

The reserved tokens come first, in this order::

    [PAD] [MASK] [CLS] [SEP] [UNK] [DIR_SB] [DIR_CB] [DIR_OTHER] [TRUNC] [NOPAYLOAD]
    [PROTO_OTHER] [DT_NONE] [PKT_OTHER] [DT_0..DT_<n-1>] [PROTO_<v>...] [PKT_1..PKT_<max>]
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass

DEFAULT_PROTOCOLS: tuple[int, ...] = (
    47, 340, 754, 758, 762, 763, 764, 765, 766, 767, 768, 769, 770, 771,
)  # fmt: skip

PAD, MASK, CLS, SEP, UNK = "[PAD]", "[MASK]", "[CLS]", "[SEP]", "[UNK]"
DIR_SB, DIR_CB, DIR_OTHER = "[DIR_SB]", "[DIR_CB]", "[DIR_OTHER]"
TRUNC, NOPAYLOAD = "[TRUNC]", "[NOPAYLOAD]"
PROTO_OTHER, DT_NONE, PKT_OTHER = "[PROTO_OTHER]", "[DT_NONE]", "[PKT_OTHER]"
_FIXED = (
    PAD, MASK, CLS, SEP, UNK, DIR_SB, DIR_CB, DIR_OTHER, TRUNC, NOPAYLOAD,
    PROTO_OTHER, DT_NONE, PKT_OTHER,
)  # fmt: skip

# Fixed ids (the first entries of every layout).
PAD_ID, MASK_ID, CLS_ID, SEP_ID, UNK_ID = 0, 1, 2, 3, 4
DIR_SB_ID, DIR_CB_ID, DIR_OTHER_ID = 5, 6, 7
TRUNC_ID, NOPAYLOAD_ID = 8, 9
PROTO_OTHER_ID, DT_NONE_ID, PKT_OTHER_ID = 10, 11, 12
HEADER_LEN = 3  # direction, packet type, delta-t; an optional protocol token follows


@dataclass(frozen=True)
class TokenLayout:
    """Parameters that fix the ids of all reserved tokens."""

    dt_buckets: int = 16  # log-spaced buckets of the gap to the previous packet
    dt_min_ms: float = 0.5  # upper edge of bucket 0
    dt_max_ms: float = 5000.0  # upper edge of the last but one bucket
    protocols: tuple[int, ...] = DEFAULT_PROTOCOLS
    packet_type_max: int = 255  # ids 1..max get a token; vocabulary.json is append-only

    def __post_init__(self) -> None:
        if self.dt_buckets < 2:
            raise ValueError("dt_buckets must be >= 2")
        if not 0 < self.dt_min_ms < self.dt_max_ms:
            raise ValueError("need 0 < dt_min_ms < dt_max_ms")
        if len(set(self.protocols)) != len(self.protocols):
            raise ValueError("protocols must be unique")
        if self.packet_type_max < 1:
            raise ValueError("packet_type_max must be >= 1")

    # --- ids -----------------------------------------------------------------------------------

    @property
    def dt_base(self) -> int:
        return len(_FIXED)

    @property
    def proto_base(self) -> int:
        return self.dt_base + self.dt_buckets

    @property
    def pkt_base(self) -> int:
        return self.proto_base + len(self.protocols)

    @property
    def n_special(self) -> int:
        """Reserved ids are ``0 .. n_special - 1``; byte tokens start at ``n_special``."""
        return self.pkt_base + self.packet_type_max

    def special_tokens(self) -> list[str]:
        """All reserved token strings in id order."""
        return [
            *_FIXED,
            *(f"[DT_{i}]" for i in range(self.dt_buckets)),
            *(f"[PROTO_{p}]" for p in self.protocols),
            *(f"[PKT_{i}]" for i in range(1, self.packet_type_max + 1)),
        ]

    def dt_edges_ms(self) -> list[float]:
        """Upper edges of buckets ``0 .. dt_buckets - 2``; the last bucket takes the rest."""
        n = self.dt_buckets - 1
        if n == 1:
            return [self.dt_min_ms]
        lo, hi = math.log(self.dt_min_ms), math.log(self.dt_max_ms)
        return [round(math.exp(lo + (hi - lo) * i / (n - 1)), 6) for i in range(n)]

    def dt_id(self, dt_ms: float | None) -> int:
        """Token of a gap in ms (``None``: no previous packet in the segment)."""
        if dt_ms is None:
            return DT_NONE_ID
        return self.dt_base + bisect.bisect_right(self.dt_edges_ms(), dt_ms)

    def proto_id(self, protocol: int | None) -> int:
        if protocol is None or protocol not in self.protocols:
            return PROTO_OTHER_ID
        return self.proto_base + self.protocols.index(protocol)

    def pkt_id(self, packet_type: int) -> int:
        if 1 <= packet_type <= self.packet_type_max:
            return self.pkt_base + packet_type - 1
        return PKT_OTHER_ID

    def direction_id(self, direction: int) -> int:
        return {1: DIR_SB_ID, 2: DIR_CB_ID}.get(int(direction), DIR_OTHER_ID)

    def is_proto(self, token_id: int) -> bool:
        in_list = self.proto_base <= token_id < self.proto_base + len(self.protocols)
        return in_list or token_id == PROTO_OTHER_ID

    def to_dict(self) -> dict:
        return {
            "dt_buckets": self.dt_buckets,
            "dt_min_ms": self.dt_min_ms,
            "dt_max_ms": self.dt_max_ms,
            "protocols": list(self.protocols),
            "packet_type_max": self.packet_type_max,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> TokenLayout:
        return cls(
            dt_buckets=int(raw["dt_buckets"]),
            dt_min_ms=float(raw["dt_min_ms"]),
            dt_max_ms=float(raw["dt_max_ms"]),
            protocols=tuple(int(p) for p in raw["protocols"]),
            packet_type_max=int(raw["packet_type_max"]),
        )
