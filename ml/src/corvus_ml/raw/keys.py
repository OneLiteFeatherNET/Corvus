"""Where the player DEKs come from: the :class:`DekProvider` port and its adapters.

The jobs reach the catalog only through the control-plane API, which has no call for wrapped keys.
:class:`PgDekProvider` therefore reads ``corvus.player_keys`` directly (read-only) with the
connection settings of the services (``CORVUS_PG_DSN``, ``PGPASSWORD``) and unwraps the keys with
``CORVUS_KEK``. A job that reads raw bytes needs SELECT on ``corvus.player_keys``.

A pseudonym without a row (erased player, crypto-shredded) yields ``None``: the caller skips the
sessions of that player.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable, Mapping
from typing import Protocol

from corvus_ml.raw.crypto import UnwrapError, kek_from_env, unwrap_dek

log = logging.getLogger("corvus.raw.keys")


class DekProvider(Protocol):
    def prefetch(self, pseudonyms: Iterable[bytes]) -> None:
        """Optional bulk lookup so :meth:`dek_for` does not query per player."""

    def dek_for(self, pseudonym: bytes) -> bytes | None:
        """The player's DEK, or ``None`` if there is none (erased)."""


class MappingDekProvider:
    """DEKs from a dict (tests, or keys exported by other means)."""

    def __init__(self, deks: Mapping[bytes, bytes]) -> None:
        self._deks = dict(deks)

    def prefetch(self, pseudonyms: Iterable[bytes]) -> None:
        return None

    def dek_for(self, pseudonym: bytes) -> bytes | None:
        return self._deks.get(pseudonym)


class WrappedKeyDekProvider:
    """Unwraps DEKs fetched by ``fetch(pseudonyms) -> {pseudonym: wrapped}`` with the KEK."""

    def __init__(self, kek: bytes, fetch) -> None:
        self._kek = kek
        self._fetch = fetch
        self._cache: dict[bytes, bytes | None] = {}
        self.unwrap_failures = 0

    def prefetch(self, pseudonyms: Iterable[bytes]) -> None:
        wanted = [p for p in dict.fromkeys(pseudonyms) if p not in self._cache]
        if not wanted:
            return
        wrapped = self._fetch(wanted)
        for pseudonym in wanted:
            blob = wrapped.get(pseudonym)
            if blob is None:
                self._cache[pseudonym] = None
                continue
            try:
                self._cache[pseudonym] = unwrap_dek(self._kek, pseudonym, bytes(blob))
            except UnwrapError:
                self.unwrap_failures += 1
                log.warning("a wrapped DEK cannot be unwrapped with the configured KEK")
                self._cache[pseudonym] = None

    def dek_for(self, pseudonym: bytes) -> bytes | None:
        if pseudonym not in self._cache:
            self.prefetch([pseudonym])
        return self._cache[pseudonym]


class PgDekProvider(WrappedKeyDekProvider):
    """Reads ``corvus.player_keys`` through psycopg (read-only transactions)."""

    def __init__(self, dsn: str, kek: bytes) -> None:
        super().__init__(kek, self._query)
        self._dsn = dsn

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> PgDekProvider:
        env = os.environ if environ is None else environ
        dsn = env.get("CORVUS_PG_DSN")
        if not dsn:
            raise ValueError("CORVUS_PG_DSN is not set (raw-byte jobs read corvus.player_keys)")
        return cls(dsn, kek_from_env(env))

    def _query(self, pseudonyms: list[bytes]) -> dict[bytes, bytes]:
        import psycopg  # imported here: only raw-byte jobs need a database driver

        out: dict[bytes, bytes] = {}
        with psycopg.connect(self._dsn) as conn:
            conn.read_only = True
            with conn.cursor() as cur:
                for i in range(0, len(pseudonyms), 500):
                    chunk = pseudonyms[i : i + 500]
                    cur.execute(
                        "SELECT pseudonym, dek_wrapped FROM corvus.player_keys"
                        " WHERE pseudonym = ANY(%s)",
                        (chunk,),
                    )
                    out.update({bytes(p): bytes(w) for p, w in cur.fetchall()})
        return out
