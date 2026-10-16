"""Shared shutdown helpers of the NATS-fed services (ingest, score)."""

from __future__ import annotations

import logging
from typing import Any

from nats.errors import ConnectionClosedError, ConnectionDrainingError

log = logging.getLogger("corvus.nats")

# What an in-flight pull fetch raises when the connection is drained or closed under it. nats-py
# raises a plain ``asyncio.TimeoutError`` (the builtin) for a closed subscription, not its own.
FETCH_ENDED = (TimeoutError, ConnectionClosedError, ConnectionDrainingError)


async def close_connection(nc: Any) -> None:
    """Drain the connection (flush outstanding publishes); a failed drain is logged and the
    connection closed anyway, so shutdown never ends in a traceback."""
    try:
        await nc.drain()
    except Exception as e:
        log.warning("nats drain failed (%s: %s); closing the connection", type(e).__name__, e)
        try:
            await nc.close()
        except Exception:
            log.debug("nats close failed", exc_info=True)
