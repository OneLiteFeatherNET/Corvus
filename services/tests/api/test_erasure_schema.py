from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from corvus_services.api.ports import ErasureRequest
from corvus_services.api.schemas import ErasureOut

REQUEST = ErasureRequest(
    id=UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
    pseudonym=b"\xaa\xbb",
    requested_by="ops",
    requested_at=datetime(2026, 11, 18, tzinfo=UTC),
    status="completed",
    player_known=True,
    key_deleted=True,
    compaction_pending=True,
)


def test_erasure_response_is_not_blocked_by_default():
    assert ErasureOut.of(REQUEST).lake_blocked_reason is None


def test_erasure_response_shows_why_the_lake_rows_cannot_be_removed_yet():
    reason = "events: snapshots to expire are held by legal-hold"
    blocked = ErasureRequest(**{**REQUEST.__dict__, "lake_blocked_reason": reason})

    assert ErasureOut.of(blocked).lake_blocked_reason == reason
