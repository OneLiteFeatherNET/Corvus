"""Streaming reader for length-delimited ``Envelope`` capture files."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from google.protobuf.message import DecodeError

from corvus_ml.proto import pb

log = logging.getLogger(__name__)

# A single envelope above this size is treated as corruption, never allocated.
MAX_ENVELOPE_BYTES = 64 * 1024 * 1024


@dataclass
class ReadStats:
    """Outcome of reading one file; filled in while iterating."""

    envelopes: int = 0
    truncated: bool = False
    corrupt: bool = False


def _read_varint(stream: BinaryIO) -> int | None:
    """Return the varint at the stream position, None at clean EOF.

    Raises EOFError for EOF in the middle of a varint, ValueError if it is too long.
    """
    result = 0
    shift = 0
    while True:
        byte = stream.read(1)
        if not byte:
            if shift == 0:
                return None
            raise EOFError("truncated varint")
        value = byte[0]
        result |= (value & 0x7F) << shift
        if not value & 0x80:
            return result
        shift += 7
        if shift >= 64:
            raise ValueError("varint too long")


def iter_envelopes(stream: BinaryIO, stats: ReadStats, name: str = "<stream>") -> Iterator:
    """Yield envelopes one at a time; memory use is bounded by the largest envelope.

    A truncated final message is counted (``stats.truncated``) and logged, not raised.
    """
    while True:
        try:
            size = _read_varint(stream)
        except EOFError:
            stats.truncated = True
            log.warning("%s: truncated length prefix at end of file", name)
            return
        except ValueError:
            stats.corrupt = True
            log.warning("%s: corrupt length prefix", name)
            return
        if size is None:
            return
        if size > MAX_ENVELOPE_BYTES:
            stats.corrupt = True
            log.warning("%s: envelope length %d exceeds limit", name, size)
            return
        body = stream.read(size)
        if len(body) < size:
            stats.truncated = True
            log.warning("%s: truncated last message (%d of %d bytes)", name, len(body), size)
            return
        envelope = pb.Envelope()
        try:
            envelope.ParseFromString(body)
        except DecodeError:
            stats.corrupt = True
            log.warning("%s: undecodable envelope after %d messages", name, stats.envelopes)
            return
        stats.envelopes += 1
        yield envelope


def read_file(path: Path, stats: ReadStats) -> Iterator:
    """Stream envelopes from a capture file on disk."""
    with path.open("rb") as stream:
        yield from iter_envelopes(stream, stats, path.name)
