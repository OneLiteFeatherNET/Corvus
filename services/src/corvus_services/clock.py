"""Injectable time source and ULID generation."""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime

Clock = Callable[[], datetime]
"""Returns the current time as an aware UTC datetime."""


def system_clock() -> datetime:
    return datetime.now(UTC)


_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_ulid(now: datetime, entropy: bytes | None = None) -> str:
    """26-character Crockford base32 ULID: 48 bit millisecond timestamp + 80 bit randomness."""
    millis = int(now.timestamp() * 1000)
    rand = entropy if entropy is not None else os.urandom(10)
    value = (millis << 80) | int.from_bytes(rand, "big")
    out = []
    for _ in range(26):
        out.append(_CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(out))
