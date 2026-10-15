"""Builders for the raw-byte scoring tests: a tiny real tokenizer, specs, model assets, frames."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

from corvus_ml.features.spec import FeatureSpec
from corvus_ml.raw.layout import TokenLayout
from corvus_ml.raw.spec import PacketConfig, RawBytesSpec, WindowConfig
from corvus_ml.raw.tokenizer import RawTokenizer, TokenizerConfig
from score_rig import SMALL

from corvus_services.proto import pb
from helpers import SESSION

LAYOUT = TokenLayout(packet_type_max=64)
PACKET = PacketConfig(max_tokens_per_packet=16)
# 10 ms between frames: 100 frames per one-second window, capped at 32 packets
WINDOW = WindowConfig(window_seconds=1.0, stride_seconds=1.0, max_packets=32, min_packets=4)
PAYLOADS = [bytes([1, 2, 3, 4, 5, i % 7]) * 3 for i in range(60)]


def train_tokenizer(directory: Path) -> RawTokenizer:
    tok = RawTokenizer.train(
        PAYLOADS, TokenizerConfig(vocab_size=LAYOUT.n_special + 256 + 20, layout=LAYOUT)
    )
    tok.save(directory)
    return tok


class RawAssets:
    """A trained tokenizer plus the spec and model card that describe it."""

    def __init__(self, directory: Path) -> None:
        self.tokenizer = train_tokenizer(directory)
        self.tokenizer_bytes = (directory / "tokenizer.json").read_bytes()
        self.raw_spec = self.tokenizer.spec(directory, PACKET, WINDOW)

    def spec(self, window: WindowConfig | None = None) -> RawBytesSpec:
        return dataclasses.replace(self.raw_spec, window=window or WINDOW)

    def feature_spec(self, window: WindowConfig | None = None) -> FeatureSpec:
        return FeatureSpec.current(SMALL, raw_bytes=self.spec(window))

    def card(self, window: WindowConfig | None = None, **overrides: Any) -> dict[str, object]:
        spec = self.spec(window)
        card: dict[str, object] = {
            "input_kind": "raw_bytes",
            "tokenizer": {"file": "tokenizer.json", "sha256": spec.tokenizer_sha256},
        }
        card.update(overrides)
        return card


def payload_frames_env(
    session_id: bytes = SESSION,
    segment: int = 0,
    first: int = 0,
    n: int = 100,
    payload: bytes = b"\x01\x02\x03\x04\x05\x06" * 3,
) -> Any:
    """``n`` serverbound frames with a payload, 10 ms apart, consecutive ``seq`` from ``first``."""
    env = pb.Envelope()
    b = env.batch
    b.session_id = session_id
    b.segment = segment
    for seq in range(first, first + n):
        f = b.frames.add()
        f.seq = seq
        f.offset_nanos = seq * 10_000_000
        f.direction = pb.DIRECTION_SERVERBOUND
        f.state = pb.CONNECTION_STATE_PLAY
        f.packet_type = 14
        f.payload = payload
        f.payload_original_size = len(payload)
    return env
