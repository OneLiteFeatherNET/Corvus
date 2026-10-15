"""Payload decryption and key handling of the raw-byte reader.

The ML side must not import ``corvus_services``; ``corvus_ml.raw.crypto`` copies its algorithm
(docs/reference/data-lake.md). The vectors below were computed with ``corvus_services.crypto``
(``encrypt_payload`` with a fixed nonce, ``Keyring.wrap`` with a fixed nonce) and are checked here
against ``cryptography`` directly, so a drift of either side shows up as a failing test.
"""

import base64

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from corvus_ml.raw.crypto import (
    UnwrapError,
    decrypt_payload,
    kek_from_env,
    payload_aad,
    unwrap_dek,
)
from corvus_ml.raw.keys import MappingDekProvider, PgDekProvider, WrappedKeyDekProvider

DEK = bytes(range(32))
SESSION = bytes(range(16, 32))
SEQ = 258
NONCE = bytes(range(12))
PLAINTEXT = b"\x40\x09\x00hello"
CIPHERTEXT = bytes.fromhex("070bd673a089ae74ebdc46545ba9e8f93a73e01bdd334195")
KEK = bytes(range(100, 132))
PSEUDONYM = bytes(range(32))
WRAPPED = bytes.fromhex(
    "32333435363738393a3b3c3df8923c45e871faa6d3a4acd65d63fad9b6064907ab3b66c0439d1a6ade1c371a"
    "7f168a161bfeeca4ef5443058b21865f"
)


def test_aad_is_session_id_then_big_endian_seq():
    assert payload_aad(SESSION, SEQ) == SESSION + (258).to_bytes(8, "big")
    assert payload_aad(SESSION, SEQ)[-2:] == b"\x01\x02"


def test_decrypts_the_vector_produced_with_the_services_algorithm():
    assert decrypt_payload(DEK, SESSION, SEQ, NONCE, CIPHERTEXT) == PLAINTEXT


def test_vector_matches_aes_gcm_with_the_documented_aad():
    assert AESGCM(DEK).encrypt(NONCE, PLAINTEXT, SESSION + SEQ.to_bytes(8, "big")) == CIPHERTEXT


def test_ciphertext_is_bound_to_session_and_sequence_number():
    from cryptography.exceptions import InvalidTag

    with pytest.raises(InvalidTag):
        decrypt_payload(DEK, SESSION, SEQ + 1, NONCE, CIPHERTEXT)
    with pytest.raises(InvalidTag):
        decrypt_payload(DEK, bytes(16), SEQ, NONCE, CIPHERTEXT)


def test_unwraps_the_dek_wrapped_by_the_services_keyring():
    assert unwrap_dek(KEK, PSEUDONYM, WRAPPED) == DEK


def test_wrapped_dek_is_bound_to_the_pseudonym():
    with pytest.raises(UnwrapError):
        unwrap_dek(KEK, bytes(32), WRAPPED)


def test_wrong_kek_is_an_unwrap_error():
    with pytest.raises(UnwrapError):
        unwrap_dek(bytes(32), PSEUDONYM, WRAPPED)


def test_kek_comes_from_base64_in_the_environment():
    env = {"CORVUS_KEK": base64.b64encode(KEK).decode()}
    assert kek_from_env(env) == KEK
    with pytest.raises(ValueError, match="32 bytes"):
        kek_from_env({"CORVUS_KEK": base64.b64encode(b"short").decode()})
    with pytest.raises(ValueError, match="not set"):
        kek_from_env({})


def test_wrapped_key_provider_unwraps_and_reports_erased_players_as_none():
    calls = []

    def fetch(pseudonyms):
        calls.append(list(pseudonyms))
        return {PSEUDONYM: WRAPPED}  # the other player has no row any more

    provider = WrappedKeyDekProvider(KEK, fetch)
    erased = bytes([9]) * 32
    provider.prefetch([PSEUDONYM, erased])

    assert provider.dek_for(PSEUDONYM) == DEK
    assert provider.dek_for(erased) is None
    assert len(calls) == 1, "prefetch is one lookup, later calls hit the cache"


def test_wrapped_key_provider_counts_keys_it_cannot_unwrap():
    provider = WrappedKeyDekProvider(bytes(32), lambda ps: {PSEUDONYM: WRAPPED})

    assert provider.dek_for(PSEUDONYM) is None
    assert provider.unwrap_failures == 1


def test_mapping_provider():
    provider = MappingDekProvider({PSEUDONYM: DEK})
    assert provider.dek_for(PSEUDONYM) == DEK
    assert provider.dek_for(bytes(32)) is None


class _FakeCursor:
    def __init__(self, rows, log):
        self.rows, self.log = rows, log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self.log.append((sql, params))

    def fetchall(self):
        return self.rows


class _FakeConnection:
    def __init__(self, rows, log):
        self.rows, self.log, self.read_only = rows, log, False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return _FakeCursor(self.rows, self.log)


def test_pg_provider_reads_player_keys_read_only_and_unwraps(monkeypatch):
    import types

    log, conns = [], []

    def connect(dsn):
        conn = _FakeConnection([(PSEUDONYM, WRAPPED)], log)
        conns.append((dsn, conn))
        return conn

    monkeypatch.setitem(
        __import__("sys").modules, "psycopg", types.SimpleNamespace(connect=connect)
    )
    provider = PgDekProvider("postgresql://job@db/corvus", KEK)

    assert provider.dek_for(PSEUDONYM) == DEK
    assert provider.dek_for(bytes([7]) * 32) is None, "no row: erased"
    (sql, params), *_ = log
    assert "FROM corvus.player_keys" in sql
    assert sql.lstrip().upper().startswith("SELECT")
    assert params == ([PSEUDONYM],)
    assert conns[0][0] == "postgresql://job@db/corvus"
    assert conns[0][1].read_only is True


def test_pg_provider_needs_dsn_and_kek_in_the_environment():
    kek = base64.b64encode(KEK).decode()

    with pytest.raises(ValueError, match="CORVUS_PG_DSN"):
        PgDekProvider.from_env({"CORVUS_KEK": kek})
    with pytest.raises(ValueError, match="CORVUS_KEK"):
        PgDekProvider.from_env({"CORVUS_PG_DSN": "postgresql://x"})
    assert PgDekProvider.from_env({"CORVUS_PG_DSN": "postgresql://x", "CORVUS_KEK": kek})
