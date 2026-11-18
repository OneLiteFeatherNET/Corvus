"""The Iceberg step of corvus-compact: fake catalog and fake lake, no files, no network."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from corvus_services.compact import run_compaction
from helpers import make_settings

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
REQ_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
REQ_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
SESS_1 = b"\x01" * 16
SESS_2 = b"\x02" * 16
PSEUDONYM = b"\xa1" * 32


class FakeCompactCatalog:
    def __init__(self, requests: list[UUID], sessions: dict[UUID, list[bytes]]) -> None:
        self.requests = requests
        self.sessions = sessions
        self.completed: list[object] = []
        self.rebuild_marks: list[object] = []
        self.blocks: dict[object, str | None] = {}

    async def purge_pending(self) -> list[Any]:
        return []

    async def pending_request_ids(self, requested_before: datetime) -> list[object]:
        return list(self.requests)

    async def derived_for_requests(self, request_ids: list[object]) -> list[Any]:
        return []

    async def affected_files(self, request_ids: list[object]) -> list[Any]:
        return []

    async def complete_requests(self, request_ids: list[object], now: datetime) -> None:
        self.completed.extend(request_ids)

    async def request_pseudonym(self, request_id: object) -> bytes:
        return PSEUDONYM

    async def request_sessions(self, request_id: object) -> list[bytes]:
        assert isinstance(request_id, UUID)
        return self.sessions[request_id]

    async def mark_snapshots_needing_rebuild(self, request_id: object, now: datetime) -> None:
        self.rebuild_marks.append(request_id)

    async def set_lake_block(self, request_id: object, reason: str | None) -> None:
        self.blocks[request_id] = reason


class FakeLake:
    def __init__(self, held: dict[str, list[str]] | None = None, error: Exception | None = None):
        self.held = held or {}
        self.error = error
        self.calls: list[tuple[str, bytes, list[bytes]]] = []

    def erase(self, request_id: str, pseudonym: bytes, session_ids: list[bytes]) -> list[str]:
        self.calls.append((request_id, pseudonym, session_ids))
        if self.error is not None:
            raise self.error
        return self.held.get(request_id, [])


async def run(catalog: FakeCompactCatalog, lake: FakeLake | None):
    return await run_compaction(
        make_settings(compact_grace_s=0),
        catalog,
        object(),
        lambda: NOW,
        lake=lake,  # type: ignore[arg-type]
    )


async def test_without_a_lake_the_request_completes_as_before_and_nothing_is_marked():
    catalog = FakeCompactCatalog([REQ_A], {REQ_A: [SESS_1]})

    report = await run(catalog, None)

    assert catalog.completed == [REQ_A]
    assert catalog.rebuild_marks == []
    assert catalog.blocks == {}
    assert report.lake_blocked == 0


async def test_the_lake_erases_the_request_sessions_and_the_request_completes():
    catalog = FakeCompactCatalog([REQ_A], {REQ_A: [SESS_1, SESS_2]})
    lake = FakeLake()

    report = await run(catalog, lake)

    assert lake.calls == [(str(REQ_A), PSEUDONYM, [SESS_1, SESS_2])]
    assert catalog.rebuild_marks == [REQ_A]
    assert catalog.blocks == {REQ_A: None}
    assert catalog.completed == [REQ_A]
    assert report.errors == []


async def test_a_request_held_by_a_tag_stays_pending_with_its_reason():
    reason = "events: snapshots to expire are held by legal-hold"
    catalog = FakeCompactCatalog([REQ_A], {REQ_A: [SESS_1]})

    report = await run(catalog, FakeLake(held={str(REQ_A): [reason]}))

    assert catalog.completed == []
    assert catalog.blocks == {REQ_A: reason}
    assert report.lake_blocked == 1
    assert report.errors == []


async def test_a_blocked_request_does_not_hold_back_the_others():
    catalog = FakeCompactCatalog([REQ_A, REQ_B], {REQ_A: [SESS_1], REQ_B: [SESS_2]})

    await run(catalog, FakeLake(held={str(REQ_A): ["raw: held by audit"]}))

    assert catalog.completed == [REQ_B]


async def test_a_failing_lake_step_keeps_every_request_pending_and_reports_it():
    catalog = FakeCompactCatalog([REQ_A, REQ_B], {REQ_A: [SESS_1], REQ_B: [SESS_2]})

    report = await run(catalog, FakeLake(error=RuntimeError("catalog down")))

    assert catalog.completed == []
    assert report.errors
    assert "catalog down" in report.errors[0]
