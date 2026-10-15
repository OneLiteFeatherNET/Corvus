"""Player erasure (GDPR): crypto-shredding plus blocklist, in one transaction."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import UUID, uuid4

from .ports import BlockEntry, ErasureRequest, ErasureStore


async def erase_player(
    store: ErasureStore,
    pseudonym: bytes,
    *,
    requested_by: str,
    now: datetime,
    new_id: Callable[[], UUID] = uuid4,
) -> ErasureRequest:
    """Steps, all or nothing:

    1. delete the ``player_keys`` row (the player's ``raw`` payloads become unreadable),
    2. set ``players.erased_at`` (ingest then stops storing data for the pseudonym),
    3. add the pseudonym to the blocklist (the plugin stops capturing),
    4. delete the player's rows in ``scores_latest`` and ``score_history``,
    5. record the audit row with ``compaction_pending`` (the ``events`` rewrite is a later job).

    A pseudonym that was never captured is still blocklisted and audited.
    """
    async with store.transaction() as tx:
        player = await tx.lock_player(pseudonym)
        key_deleted = await tx.delete_player_key(pseudonym) if player is not None else False
        if player is not None:
            await tx.mark_erased(pseudonym, now)
        await tx.block(
            BlockEntry(pseudonym, "block", "erasure", "erasure request", requested_by, now)
        )
        await tx.delete_scores(pseudonym)
        request = ErasureRequest(
            id=new_id(),
            pseudonym=pseudonym,
            requested_by=requested_by,
            requested_at=now,
            status="completed",
            player_known=player is not None,
            key_deleted=key_deleted,
            compaction_pending=True,
        )
        await tx.record_request(request)
    return request
