"""Player pseudonyms, bit-for-bit identical to the plugin.

Mirrors ``Pseudonymizer.java``: ``HMAC-SHA-256(key, uuid as 16 big-endian bytes)`` where the key is the
base64-decoded ``CORVUS_PSEUDONYM_KEY``. The test vector is in docs/reference/pseudonym-test-vector.md.
Clear UUIDs live only in request memory; they are never stored or logged.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid

PSEUDONYM_BYTES = 32


class PseudonymKeyMissingError(Exception):
    """No pseudonym key is configured, so UUIDs cannot be resolved."""


class Pseudonymizer:
    def __init__(self, key: bytes | None, key_id: str) -> None:
        self._key = key
        self.key_id = key_id

    @property
    def available(self) -> bool:
        return self._key is not None

    def pseudonym(self, player: uuid.UUID) -> bytes:
        if self._key is None:
            raise PseudonymKeyMissingError("CORVUS_PSEUDONYM_KEY is not configured")
        return hmac.new(self._key, player.bytes, hashlib.sha256).digest()


def parse_pseudonym(value: str) -> bytes:
    """Hex form (64 chars, any case) to bytes."""
    if len(value) != PSEUDONYM_BYTES * 2:
        raise ValueError("pseudonym must be 64 hex characters")
    try:
        return bytes.fromhex(value)
    except ValueError as e:
        raise ValueError("pseudonym must be hexadecimal") from e
