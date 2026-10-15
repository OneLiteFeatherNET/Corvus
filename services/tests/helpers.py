"""In-memory fakes and builders shared by the unit tests."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from typing import Any

from corvus_services.config import Settings
from corvus_services.ports import (
    Counters,
    FileRecord,
    ObjectInfo,
    SegmentData,
    SessionData,
    SessionInfo,
    WrappedDek,
)
from corvus_services.proto import pb

KEK = bytes(range(32))
SESSION = bytes(range(16))
PSEUDONYM = b"\x01" * 32
T0 = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw: float) -> None:
        self.now += timedelta(**kw)


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "kek": base64.b64encode(KEK).decode(),
        "workers": 1,
        "nak_delay_s": 0.0,
        "metrics_port": 0,
    }
    base.update(overrides)
    return Settings(**base)


class FakeMessage:
    def __init__(self, data: bytes, msg_id: str, log: list[str], delivery_count: int = 1) -> None:
        self.delivery_count = delivery_count
        self.data = data
        self.msg_id = msg_id
        self.log = log
        self.acked = 0
        self.naked = 0
        self.termed = 0

    async def ack(self) -> None:
        self.acked += 1
        self.log.append(f"ack {self.msg_id}")

    async def nak(self, delay: float) -> None:
        self.naked += 1
        self.log.append(f"nak {self.msg_id}")

    async def term(self) -> None:
        self.termed += 1
        self.log.append(f"term {self.msg_id}")


class FakeStore:
    def __init__(self, log: list[str]) -> None:
        self.objects: dict[str, bytes] = {}
        self.log = log
        self.fail_put = False
        self.deleted: list[str] = []

    async def put(self, key: str, data: bytes) -> None:
        if self.fail_put:
            raise OSError("s3 down")
        self.objects[key] = data
        self.log.append(f"put {key}")

    async def copy(self, src: str, dst: str) -> None:
        self.objects[dst] = self.objects[src]
        self.log.append(f"copy {dst}")

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)
        self.deleted.append(key)
        self.log.append(f"delete {key}")

    async def list_objects(self, prefix: str) -> list[ObjectInfo]:
        return [ObjectInfo(k, T0, len(v)) for k, v in self.objects.items() if k.startswith(prefix)]

    async def ping(self) -> None:
        return None


class FakeCatalog:
    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.sessions: dict[bytes, SessionInfo] = {}
        self.deks: dict[bytes, WrappedDek] = {}
        self.erased: set[bytes] = set()
        self.counters: dict[bytes, list[int]] = {}
        self.applied: set[tuple[bytes, str]] = set()
        self.segments: list[SegmentData] = []
        self.files: list[tuple[FileRecord, frozenset[bytes]]] = []
        self.ended: dict[bytes, datetime] = {}

    async def start_session(
        self, data: SessionData, candidate_dek: WrappedDek
    ) -> tuple[SessionInfo, WrappedDek | None]:
        erased = data.pseudonym in self.erased
        info = SessionInfo(data.session_id, data.pseudonym, data.started_at, erased)
        self.sessions[data.session_id] = info
        self.counters.setdefault(data.session_id, [0, 0, 0, 0])
        if erased:
            return info, None
        return info, self.deks.setdefault(data.pseudonym, candidate_dek)

    async def create_orphan_session(self, session_id: bytes, started_at: datetime) -> SessionInfo:
        info = self.sessions.setdefault(
            session_id, SessionInfo(session_id, None, started_at, False)
        )
        self.counters.setdefault(session_id, [0, 0, 0, 0])
        return info

    async def start_segment(self, data: SegmentData) -> None:
        self.segments.append(data)

    async def get_session(self, session_id: bytes) -> SessionInfo | None:
        info = self.sessions.get(session_id)
        if info is None:
            return None
        return SessionInfo(
            info.session_id,
            info.pseudonym,
            info.started_at,
            info.pseudonym is not None and info.pseudonym in self.erased,
        )

    async def get_dek(self, pseudonym: bytes) -> WrappedDek | None:
        return None if pseudonym in self.erased else self.deks.get(pseudonym)

    async def apply_counters(
        self, session_id: bytes, msg_id: str, counters: Counters, ended_at: datetime | None
    ) -> bool:
        if (session_id, msg_id) in self.applied:
            return False
        self.applied.add((session_id, msg_id))
        c = self.counters[session_id]
        c[0] += counters.dropped_queue_full
        c[1] += counters.dropped_privacy
        c[2] += counters.listener_errors
        c[3] += counters.dropped_transport
        if ended_at is not None:
            self.ended[session_id] = ended_at
        return True

    async def register_file(self, record: FileRecord, session_ids: frozenset[bytes]) -> int:
        self.files.append((record, session_ids))
        self.log.append(f"register {record.s3_key}")
        return len(self.files)

    async def ping(self) -> None:
        return None


def session_start_env(
    session_id: bytes = SESSION, pseudonym: bytes = PSEUDONYM, started: datetime = T0
) -> Any:
    env = pb.Envelope()
    s = env.session_start
    s.session_id = session_id
    s.player_pseudonym = pseudonym
    s.pseudonym_key_id = "k1"
    s.client_protocol_version = 769
    s.observed_protocol_version = 769
    s.proxy_id = "proxy-1"
    s.plugin_version = "0.1.0"
    s.started_at_unix_millis = int(started.timestamp() * 1000)
    return env


def segment_env(session_id: bytes = SESSION, segment: int = 0) -> Any:
    env = pb.Envelope()
    s = env.segment_start
    s.session_id = session_id
    s.segment = segment
    s.backend_server = "pvp-1"
    s.game_context = "pvp"
    s.client_brand = "vanilla"
    s.started_at_unix_millis = int(T0.timestamp() * 1000)
    return env


def batch_env(
    seqs: list[int],
    session_id: bytes = SESSION,
    payload: bytes = b"",
    dropped_transport: int = 0,
) -> Any:
    env = pb.Envelope()
    b = env.batch
    b.session_id = session_id
    b.segment = 0
    for seq in seqs:
        f = b.frames.add()
        f.seq = seq
        f.offset_nanos = seq * 1000
        f.direction = pb.DIRECTION_SERVERBOUND
        f.state = pb.CONNECTION_STATE_PLAY
        f.wire_packet_id = 5
        f.packet_type = 7
        if payload:
            f.payload = payload
            f.payload_original_size = len(payload)
    b.counters.dropped_transport = dropped_transport
    return env


def end_env(session_id: bytes = SESSION, dropped_privacy: int = 0) -> Any:
    env = pb.Envelope()
    e = env.session_end
    e.session_id = session_id
    e.ended_at_unix_millis = int((T0 + timedelta(minutes=5)).timestamp() * 1000)
    e.counters.dropped_privacy = dropped_privacy
    return env


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()
