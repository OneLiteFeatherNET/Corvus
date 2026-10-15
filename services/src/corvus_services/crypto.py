"""Envelope encryption: per-player DEKs wrapped with the KEK, AES-256-GCM payload encryption."""

from __future__ import annotations

import os
from collections import OrderedDict

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_BYTES = 12
DEK_BYTES = 32


class UnwrapError(Exception):
    """A wrapped key could not be unwrapped (wrong KEK or tampered data)."""


def payload_aad(session_id: bytes, seq: int) -> bytes:
    """AAD binding a ciphertext to its frame: ``session_id || seq`` (8 bytes big-endian)."""
    return session_id + seq.to_bytes(8, "big")


def encrypt_payload(
    dek: bytes, session_id: bytes, seq: int, payload: bytes, nonce: bytes | None = None
) -> tuple[bytes, bytes]:
    """Return ``(nonce, ciphertext)``; the nonce is random 12 bytes unless given (tests)."""
    nonce = nonce if nonce is not None else os.urandom(NONCE_BYTES)
    return nonce, AESGCM(dek).encrypt(nonce, payload, payload_aad(session_id, seq))


def decrypt_payload(
    dek: bytes, session_id: bytes, seq: int, nonce: bytes, ciphertext: bytes
) -> bytes:
    return AESGCM(dek).decrypt(nonce, ciphertext, payload_aad(session_id, seq))


class Keyring:
    """Wraps and unwraps DEKs with the KEK. Wrapped form: ``nonce(12) || AES-GCM(dek)``.

    The player pseudonym is the AAD, so a wrapped key cannot be moved to another player's row.
    """

    def __init__(self, kek: bytes, kek_id: str) -> None:
        if len(kek) != 32:
            raise ValueError("KEK must be 32 bytes")
        self._aead = AESGCM(kek)
        self.kek_id = kek_id

    def new_dek(self, pseudonym: bytes) -> tuple[bytes, bytes]:
        """Fresh random DEK and its wrapped form."""
        dek = os.urandom(DEK_BYTES)
        return dek, self.wrap(pseudonym, dek)

    def wrap(self, pseudonym: bytes, dek: bytes) -> bytes:
        nonce = os.urandom(NONCE_BYTES)
        return nonce + self._aead.encrypt(nonce, dek, pseudonym)

    def unwrap(self, pseudonym: bytes, wrapped: bytes) -> bytes:
        try:
            return self._aead.decrypt(wrapped[:NONCE_BYTES], wrapped[NONCE_BYTES:], pseudonym)
        except InvalidTag as e:
            raise UnwrapError("cannot unwrap DEK (wrong KEK or tampered)") from e


class LruCache[K, V]:
    """Small bounded LRU map."""

    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._items: OrderedDict[K, V] = OrderedDict()

    def get(self, key: K) -> V | None:
        value = self._items.get(key)
        if value is not None:
            self._items.move_to_end(key)
        return value

    def put(self, key: K, value: V) -> None:
        self._items[key] = value
        self._items.move_to_end(key)
        while len(self._items) > self._capacity:
            self._items.popitem(last=False)

    def discard(self, key: K) -> None:
        self._items.pop(key, None)

    def __len__(self) -> int:
        return len(self._items)
