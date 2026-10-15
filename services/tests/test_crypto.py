from __future__ import annotations

import pytest
from cryptography.exceptions import InvalidTag

from corvus_services.crypto import (
    Keyring,
    LruCache,
    UnwrapError,
    decrypt_payload,
    encrypt_payload,
    payload_aad,
)
from helpers import KEK, PSEUDONYM, SESSION

DEK = b"d" * 32


def test_payload_round_trip():
    nonce, ct = encrypt_payload(DEK, SESSION, 5, b"hello")
    assert decrypt_payload(DEK, SESSION, 5, nonce, ct) == b"hello"


def test_wrong_seq_fails_authentication():
    nonce, ct = encrypt_payload(DEK, SESSION, 5, b"hello")
    with pytest.raises(InvalidTag):
        decrypt_payload(DEK, SESSION, 6, nonce, ct)


def test_wrong_session_fails_authentication():
    nonce, ct = encrypt_payload(DEK, SESSION, 5, b"hello")
    with pytest.raises(InvalidTag):
        decrypt_payload(DEK, b"\x00" * 16, 5, nonce, ct)


def test_wrong_dek_fails_authentication():
    nonce, ct = encrypt_payload(DEK, SESSION, 5, b"hello")
    with pytest.raises(InvalidTag):
        decrypt_payload(b"e" * 32, SESSION, 5, nonce, ct)


def test_aad_is_session_id_then_big_endian_seq():
    assert payload_aad(SESSION, 1) == SESSION + b"\x00\x00\x00\x00\x00\x00\x00\x01"
    assert len(payload_aad(SESSION, 2**40)) == 24


def test_dek_wrap_unwrap_round_trip():
    ring = Keyring(KEK, "kek-1")
    dek, wrapped = ring.new_dek(PSEUDONYM)
    assert len(dek) == 32
    assert wrapped != dek
    assert ring.unwrap(PSEUDONYM, wrapped) == dek


def test_new_deks_are_random():
    ring = Keyring(KEK, "kek-1")
    assert ring.new_dek(PSEUDONYM)[0] != ring.new_dek(PSEUDONYM)[0]


def test_unwrap_with_other_kek_fails():
    _, wrapped = Keyring(KEK, "kek-1").new_dek(PSEUDONYM)
    with pytest.raises(UnwrapError):
        Keyring(b"\x09" * 32, "kek-2").unwrap(PSEUDONYM, wrapped)


def test_wrapped_dek_is_bound_to_its_player():
    ring = Keyring(KEK, "kek-1")
    _, wrapped = ring.new_dek(PSEUDONYM)
    with pytest.raises(UnwrapError):
        ring.unwrap(b"\x02" * 32, wrapped)


def test_kek_must_be_32_bytes():
    with pytest.raises(ValueError, match="32 bytes"):
        Keyring(b"short", "k")


def test_lru_evicts_least_recently_used():
    cache: LruCache[str, int] = LruCache(2)
    cache.put("a", 1)
    cache.put("b", 2)
    assert cache.get("a") == 1
    cache.put("c", 3)
    assert cache.get("b") is None
    assert cache.get("a") == 1
    assert len(cache) == 2
