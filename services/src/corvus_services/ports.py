"""Ports (interfaces) between the ingest pipeline and its infrastructure.

The pipeline only talks to these protocols; production adapters live in ``pgcatalog``,
``s3store`` and ``nats_source``, unit tests use in-memory fakes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol


@dataclass(frozen=True)
class Counters:
    dropped_queue_full: int = 0
    dropped_privacy: int = 0
    listener_errors: int = 0
    dropped_transport: int = 0

    def is_zero(self) -> bool:
        return not (
            self.dropped_queue_full
            or self.dropped_privacy
            or self.listener_errors
            or self.dropped_transport
        )


@dataclass(frozen=True)
class SessionData:
    session_id: bytes
    pseudonym: bytes
    pseudonym_key_id: str
    client_protocol: int
    observed_protocol: int
    proxy_id: str
    plugin_version: str
    started_at: datetime


@dataclass(frozen=True)
class SegmentData:
    session_id: bytes
    segment: int
    backend_server: str
    game_context: str
    client_brand: str
    started_at: datetime


@dataclass(frozen=True)
class SessionInfo:
    session_id: bytes
    pseudonym: bytes | None
    started_at: datetime
    erased: bool

    @property
    def orphan(self) -> bool:
        return self.pseudonym is None

    @property
    def partition_date(self) -> date:
        """UTC date of the session start, the ``date=`` partition of its lake files."""
        return self.started_at.date()


@dataclass(frozen=True)
class WrappedDek:
    wrapped: bytes
    kek_id: str


@dataclass(frozen=True)
class FileRecord:
    s3_key: str
    table_name: str
    date: date
    row_count: int
    size_bytes: int
    schema_version: int


@dataclass(frozen=True)
class ObjectInfo:
    key: str
    last_modified: datetime
    size: int


class Message(Protocol):
    """A JetStream message as seen by the pipeline."""

    @property
    def msg_id(self) -> str:
        """Stable per-envelope id (``Nats-Msg-Id``), survives redelivery."""

    @property
    def data(self) -> bytes: ...

    @property
    def delivery_count(self) -> int:
        """How often JetStream delivered this message (1 on first delivery)."""

    async def ack(self) -> None: ...
    async def nak(self, delay: float) -> None: ...
    async def term(self) -> None: ...


class Catalog(Protocol):
    async def start_session(
        self, data: SessionData, candidate_dek: WrappedDek
    ) -> tuple[SessionInfo, WrappedDek | None]:
        """Upsert player and session; create the player's DEK from ``candidate_dek`` if none
        exists and the player is not erased. Returns the session and the DEK now stored
        (None for erased players). Idempotent."""

    async def create_orphan_session(self, session_id: bytes, started_at: datetime) -> SessionInfo:
        """Record a session whose SessionStart never arrived (no player row, no DEK). If the
        session exists already, return it unchanged. Idempotent."""

    async def start_segment(self, data: SegmentData) -> None:
        """Idempotent insert of a segment of a known session."""

    async def get_session(self, session_id: bytes) -> SessionInfo | None: ...

    async def get_dek(self, pseudonym: bytes) -> WrappedDek | None:
        """The player's wrapped DEK, None if it was shredded."""

    async def apply_counters(
        self, session_id: bytes, msg_id: str, counters: Counters, ended_at: datetime | None
    ) -> bool:
        """Add ``counters`` to the session unless ``msg_id`` was applied before (returns False
        then). Sets ``ended_at`` if given. One transaction."""

    async def register_file(self, record: FileRecord, session_ids: frozenset[bytes]) -> int:
        """Insert ``lake_files`` and ``lake_file_sessions`` in one transaction."""

    async def ping(self) -> None: ...


class ObjectStore(Protocol):
    async def put(self, key: str, data: bytes) -> None: ...
    async def copy(self, src: str, dst: str) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def list_objects(self, prefix: str) -> list[ObjectInfo]: ...
    async def ping(self) -> None: ...
