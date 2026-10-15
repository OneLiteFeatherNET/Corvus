"""Byte-level BPE over packet payloads (Hugging Face ``tokenizers``) plus the reserved tokens.

Lossless byte mapping: byte ``b`` becomes the character ``chr(0x100 + b)`` before it reaches the
BPE model (like GPT-2's byte-to-unicode table, but a plain offset). The 256 characters never
collide with the ASCII spelling of the reserved tokens (``[PAD]``, ...), so payload bytes cannot
forge a reserved token, and decoding is exact. There is no pre-tokenizer: merges may cross any
byte boundary, which is what binary layouts need.

The ids of the reserved tokens are fixed by :class:`~corvus_ml.raw.layout.TokenLayout`; the trained
merges follow after them. ``tokenizer.json`` is hashed into the feature spec.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from tokenizers import Tokenizer, models, trainers

from corvus_ml.features.spec import FeatureSpecError, FeatureSpecMismatch
from corvus_ml.raw.layout import TokenLayout
from corvus_ml.raw.spec import (
    TOKENIZER_FILE,
    PacketConfig,
    RawBytesSpec,
    WindowConfig,
    sha256_file,
)

log = logging.getLogger("corvus.raw.tokenizer")

BYTE_BASE = 0x100
_TO_TEXT = {b: chr(BYTE_BASE + b) for b in range(256)}
_ALPHABET = [_TO_TEXT[b] for b in range(256)]


def bytes_to_text(data: bytes) -> str:
    return data.decode("latin-1").translate(_TO_TEXT)


def text_to_bytes(text: str) -> bytes:
    return bytes(ord(c) - BYTE_BASE for c in text)


@dataclass(frozen=True)
class TokenizerConfig:
    vocab_size: int = 4096
    min_frequency: int = 2
    layout: TokenLayout = field(default_factory=TokenLayout)
    # Training reads only the first bytes of each payload (headers carry the structure) and stops
    # at the first of these limits, so memory stays bounded however large the snapshot is.
    train_bytes_per_payload: int = 256
    max_train_payloads: int = 2_000_000
    max_train_bytes: int = 256 * 1024 * 1024

    def __post_init__(self) -> None:
        if self.vocab_size <= self.layout.n_special + 256:
            raise ValueError(
                f"vocab_size must exceed the {self.layout.n_special} reserved tokens plus the 256 "
                "byte tokens"
            )
        if self.vocab_size > 32767:
            raise ValueError("vocab_size must be <= 32767 (token ids are stored as int16)")
        if self.min_frequency < 1:
            raise ValueError("min_frequency must be >= 1")


def bounded_payloads(payloads: Iterable[bytes], cfg: TokenizerConfig) -> Iterator[str]:
    """The training texts: non-empty payload prefixes up to the configured limits."""
    count = total = 0
    for payload in payloads:
        if not payload:
            continue
        prefix = payload[: cfg.train_bytes_per_payload]
        yield bytes_to_text(prefix)
        count += 1
        total += len(prefix)
        if count >= cfg.max_train_payloads or total >= cfg.max_train_bytes:
            return


class RawTokenizer:
    """A trained tokenizer with its layout; encodes payload bytes to ids."""

    def __init__(self, tokenizer: Tokenizer, layout: TokenLayout) -> None:
        self._tok = tokenizer
        self.layout = layout
        self._check_specials()

    # --- construction --------------------------------------------------------------------------

    @classmethod
    def train(cls, payloads: Iterable[bytes], cfg: TokenizerConfig | None = None) -> RawTokenizer:
        """Train on payload bytes. Deterministic for the same payloads in the same order."""
        cfg = cfg or TokenizerConfig()
        tok = Tokenizer(models.BPE(unk_token="[UNK]"))
        trainer = trainers.BpeTrainer(
            vocab_size=cfg.vocab_size,
            min_frequency=cfg.min_frequency,
            special_tokens=cfg.layout.special_tokens(),
            initial_alphabet=_ALPHABET,
            show_progress=False,
        )
        tok.train_from_iterator(bounded_payloads(payloads, cfg), trainer)
        return cls(tok, cfg.layout)

    @classmethod
    def load(
        cls, directory: Path, spec: RawBytesSpec | None = None, layout: TokenLayout | None = None
    ) -> RawTokenizer:
        """Load ``tokenizer.json``; with a spec the file hash and the layout must match."""
        directory = Path(directory)
        path = directory / (spec.tokenizer_file if spec else TOKENIZER_FILE)
        if not path.is_file():
            raise FeatureSpecError(f"tokenizer file {path} is missing")
        if spec is not None:
            actual = sha256_file(path)
            if actual != spec.tokenizer_sha256:
                raise FeatureSpecMismatch(
                    f"tokenizer hash mismatch: spec has {spec.tokenizer_sha256}, "
                    f"{path.name} is {actual}"
                )
            layout = spec.layout
        if layout is None:
            raise ValueError("pass the spec or the layout the tokenizer was trained with")
        tok = cls(Tokenizer.from_file(str(path)), layout)
        if spec is not None and tok.vocab_size != spec.vocab_size:
            raise FeatureSpecMismatch(
                f"tokenizer vocab size mismatch: spec has {spec.vocab_size}, file has "
                f"{tok.vocab_size}"
            )
        return tok

    def _check_specials(self) -> None:
        for i, name in enumerate(self.layout.special_tokens()):
            if self._tok.token_to_id(name) != i:
                raise FeatureSpecMismatch(
                    f"tokenizer does not follow the layout: {name} should have id {i}, "
                    f"has {self._tok.token_to_id(name)}"
                )
        first_byte_token = self._tok.id_to_token(self.layout.n_special)
        if first_byte_token is not None and first_byte_token.startswith("["):
            raise FeatureSpecMismatch(
                f"tokenizer has more reserved tokens than the layout ({first_byte_token} "
                f"at id {self.layout.n_special})"
            )

    # --- use -----------------------------------------------------------------------------------

    @property
    def vocab_size(self) -> int:
        return self._tok.get_vocab_size()

    def encode(self, payload: bytes) -> list[int]:
        return self._tok.encode(bytes_to_text(payload), add_special_tokens=False).ids

    def encode_batch(self, payloads: list[bytes]) -> list[list[int]]:
        if not payloads:
            return []
        encs = self._tok.encode_batch(
            [bytes_to_text(p) for p in payloads], add_special_tokens=False
        )
        return [e.ids for e in encs]

    def decode(self, ids: Iterable[int]) -> bytes:
        """Payload bytes of byte-token ids; reserved ids are skipped."""
        n = self.layout.n_special
        out = bytearray()
        for i in ids:
            if i >= n:
                tok = self._tok.id_to_token(int(i))
                if tok is not None:
                    out += text_to_bytes(tok)
        return bytes(out)

    def save(self, directory: Path) -> Path:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / TOKENIZER_FILE
        self._tok.save(str(path), pretty=False)
        return path

    def spec(self, directory: Path, packet: PacketConfig, window: WindowConfig) -> RawBytesSpec:
        """The spec for a tokenizer saved in ``directory`` (hash of the file on disk)."""
        return RawBytesSpec(
            tokenizer_sha256=sha256_file(Path(directory) / TOKENIZER_FILE),
            vocab_size=self.vocab_size,
            layout=self.layout,
            packet=packet,
            window=window,
        )
