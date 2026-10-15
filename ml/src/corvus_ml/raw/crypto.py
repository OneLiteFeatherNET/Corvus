"""Payload decryption, identical to ``corvus_services.crypto`` (the ML side must not import it).

The algorithm is the contract in docs/reference/data-lake.md:

* payload: AES-256-GCM with the player's DEK, 12 byte nonce, AAD = ``session_id || seq`` (8 bytes
  big-endian);
* wrapped DEK: ``nonce(12) || AES-256-GCM(KEK, dek)`` with the player pseudonym as AAD.

``tests/test_raw_crypto.py`` cross-checks both with vectors computed by the services' algorithm.
"""

from __future__ import annotations

import base64
import binascii
import os
from collections.abc import Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_BYTES = 12
DEK_BYTES = 32


class UnwrapError(Exception):
    """A wrapped DEK could not be unwrapped (wrong KEK or tampered data)."""


def payload_aad(session_id: bytes, seq: int) -> bytes:
    return session_id + int(seq).to_bytes(8, "big")


def decrypt_payload(
    dek: bytes, session_id: bytes, seq: int, nonce: bytes, ciphertext: bytes
) -> bytes:
    """Raises ``cryptography.exceptions.InvalidTag`` for a wrong key or a tampered frame."""
    return AESGCM(dek).decrypt(nonce, ciphertext, payload_aad(session_id, seq))


def unwrap_dek(kek: bytes, pseudonym: bytes, wrapped: bytes) -> bytes:
    if len(kek) != 32:
        raise ValueError("KEK must be 32 bytes")
    try:
        return AESGCM(kek).decrypt(wrapped[:NONCE_BYTES], wrapped[NONCE_BYTES:], pseudonym)
    except InvalidTag as exc:
        raise UnwrapError("cannot unwrap DEK (wrong KEK or tampered)") from exc


def kek_from_env(environ: Mapping[str, str] | None = None) -> bytes:
    """``CORVUS_KEK`` (base64, 32 bytes)."""
    env = os.environ if environ is None else environ
    text = env.get("CORVUS_KEK")
    if not text:
        raise ValueError("CORVUS_KEK is not set")
    try:
        kek = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("CORVUS_KEK is not valid base64") from None
    if len(kek) != 32:
        raise ValueError("CORVUS_KEK must decode to 32 bytes")
    return kek
