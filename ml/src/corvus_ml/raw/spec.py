"""The ``raw_bytes`` part of the feature spec: tokenizer identity, packet layout and windowing.

Pure Python (no Arrow, torch or tokenizers), so ``features.spec`` can embed it and the scorer can
validate a model before it loads anything heavy.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from corvus_ml.raw.layout import TokenLayout

TOKENIZER_FILE = "tokenizer.json"
PROTO_MODES = ("first", "every", "none")
OVERFLOW_MODES = ("recent", "uniform")
TOKENIZER_MODES = ("bpe", "bytes")  # byte-level BPE, or one token per byte (no merges)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@dataclass(frozen=True)
class PacketConfig:
    max_tokens_per_packet: int = 48
    # Where the protocol-version token goes: the first packet of every window, every packet, never.
    proto_mode: str = "first"

    def __post_init__(self) -> None:
        if self.max_tokens_per_packet < 8:
            raise ValueError("max_tokens_per_packet must be >= 8")
        if self.proto_mode not in PROTO_MODES:
            raise ValueError(f"proto_mode must be one of {PROTO_MODES}")


@dataclass(frozen=True)
class WindowConfig:
    """Time windows over the packets of one segment (never across segments)."""

    window_seconds: float = 10.0
    stride_seconds: float = 5.0
    max_packets: int = 1024
    overflow: str = "recent"  # recent = keep the newest packets, uniform = evenly spaced subsample
    min_packets: int = 8  # windows with fewer packets are dropped
    # Second level: this many seconds of consecutive windows form one macro window (needs
    # stride == window). None disables the hierarchy over minutes.
    macro_window_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.window_seconds <= 0 or self.stride_seconds <= 0:
            raise ValueError("window_seconds and stride_seconds must be > 0")
        if self.max_packets < 1 or self.min_packets < 1:
            raise ValueError("max_packets and min_packets must be >= 1")
        if self.overflow not in OVERFLOW_MODES:
            raise ValueError(f"overflow must be one of {OVERFLOW_MODES}")
        if self.macro_window_seconds is not None:
            if self.macro_window_seconds < self.window_seconds:
                raise ValueError("macro_window_seconds must be >= window_seconds")
            if abs(self.stride_seconds - self.window_seconds) > 1e-9:
                raise ValueError("macro windows need stride_seconds == window_seconds")

    @property
    def macro_windows(self) -> int:
        """Windows per macro window (1 = no hierarchy)."""
        if self.macro_window_seconds is None:
            return 1
        return max(1, round(self.macro_window_seconds / self.window_seconds))


@dataclass(frozen=True)
class RawBytesSpec:
    """Everything a scorer needs to turn packets into the model's input tensor."""

    tokenizer_sha256: str
    vocab_size: int
    layout: TokenLayout
    packet: PacketConfig
    window: WindowConfig
    tokenizer_file: str = TOKENIZER_FILE
    tokenizer_mode: str = "bpe"

    def to_dict(self) -> dict[str, Any]:
        return {
            "tokenizer": {
                "file": self.tokenizer_file,
                "sha256": self.tokenizer_sha256,
                "vocab_size": self.vocab_size,
                "mode": self.tokenizer_mode,
                "byte_mapping": "chr(0x100 + byte)",
            },
            "layout": self.layout.to_dict(),
            "packet": asdict(self.packet),
            "window": asdict(self.window),
            "input": {
                "dtype": "float32",
                "shape": "(batch, [macro,] packets, max_tokens_per_packet + 1)",
                "channels": "token ids, then seconds since window start in the last channel",
            },
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> RawBytesSpec:
        tok = raw["tokenizer"]
        return cls(
            tokenizer_sha256=str(tok["sha256"]),
            vocab_size=int(tok["vocab_size"]),
            layout=TokenLayout.from_dict(raw["layout"]),
            packet=PacketConfig(**raw["packet"]),
            window=WindowConfig(**raw["window"]),
            tokenizer_file=str(tok.get("file", TOKENIZER_FILE)),
            tokenizer_mode=str(tok.get("mode", "bpe")),
        )

    def differences(self, other: RawBytesSpec, *, window: bool = True) -> list[str]:
        out: list[str] = []
        pairs = [
            ("tokenizer.mode", self.tokenizer_mode, other.tokenizer_mode),
            ("tokenizer.sha256", self.tokenizer_sha256, other.tokenizer_sha256),
            ("tokenizer.vocab_size", self.vocab_size, other.vocab_size),
            ("layout", self.layout, other.layout),
            ("packet", self.packet, other.packet),
        ]
        if window:
            pairs.append(("window", self.window, other.window))
        for label, mine, theirs in pairs:
            if mine != theirs:
                out.append(
                    f"model_inputs.raw_bytes.{label}: model has {mine!r}, "
                    f"runtime expects {theirs!r}"
                )
        return out
