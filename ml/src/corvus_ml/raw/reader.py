"""Join of the lake ``raw`` table (decrypted) with ``events`` on (session_id, seq).

``events`` supplies direction, state, packet type and time of every captured frame; ``raw``
supplies the payload bytes of the frames that have one. Sessions whose player has no DEK any more
(erased, crypto-shredded) are skipped and counted; their ciphertext is unreadable by design.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from corvus_ml.features.reader import dedup_sorted
from corvus_ml.features.schema import SESSION_ID_TYPE
from corvus_ml.raw.crypto import payload_aad
from corvus_ml.raw.keys import DekProvider

log = logging.getLogger("corvus.raw.reader")

EVENT_COLUMNS = (
    "session_id", "segment", "seq", "offset_nanos", "direction", "state", "packet_type",
    "payload_original_size",
)  # fmt: skip
RAW_COLUMNS = ("session_id", "segment", "seq", "payload_original_size", "nonce", "ciphertext")


@dataclass(frozen=True)
class SessionInfo:
    """What the reader needs to know about a session (from the snapshot sessions endpoint)."""

    session_id: bytes
    pseudonym: bytes | None
    protocol: int | None = None  # sessions.client_protocol


@dataclass
class ReadStats:
    sessions_read: int = 0
    sessions_erased: int = 0  # no DEK: crypto-shredded
    sessions_no_pseudonym: int = 0
    frames: int = 0
    frames_with_payload: int = 0
    decrypt_failures: int = 0  # wrong key or tampered ciphertext; the frame counts as no payload

    def to_dict(self) -> dict[str, int]:
        return dict(self.__dict__)


@dataclass
class SessionPackets:
    """All frames of one session in ``seq`` order; ``payloads[i]`` is None without payload."""

    session_id: bytes
    protocol: int | None
    segment: np.ndarray
    seq: np.ndarray
    offset_nanos: np.ndarray
    direction: np.ndarray
    state: np.ndarray
    packet_type: np.ndarray
    payload_original_size: np.ndarray
    payloads: list[bytes | None] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.seq)


def _isin(values: Sequence[bytes]) -> pc.Expression:
    return pc.field("session_id").isin(pa.array(list(values), type=SESSION_ID_TYPE))


class RawReader:
    """Reads and decrypts a bounded group of sessions at a time."""

    def __init__(
        self,
        events: ds.Dataset,
        raw: ds.Dataset,
        deks: DekProvider,
        batch_rows: int = 65_536,
    ) -> None:
        self._events, self._raw, self._deks = events, raw, deks
        self._batch_rows = batch_rows
        self.stats = ReadStats()

    def _table(self, dataset: ds.Dataset, columns: Sequence[str], ids: Sequence[bytes]):
        scanner = dataset.scanner(
            columns=list(columns), filter=_isin(ids), batch_size=self._batch_rows
        )
        batches = list(scanner.to_batches())
        return pa.Table.from_batches(batches) if batches else None

    def read_group(self, infos: Sequence[SessionInfo]) -> list[SessionPackets]:
        """Decrypted packets of the readable sessions of the group, sorted by session id."""
        readable: list[tuple[SessionInfo, bytes]] = []
        pseudonyms = [i.pseudonym for i in infos if i.pseudonym is not None]
        self._deks.prefetch(pseudonyms)
        for info in sorted(infos, key=lambda i: i.session_id):
            if info.pseudonym is None:
                self.stats.sessions_no_pseudonym += 1
                continue
            dek = self._deks.dek_for(info.pseudonym)
            if dek is None:
                self.stats.sessions_erased += 1
                continue
            readable.append((info, dek))
        if not readable:
            return []
        ids = [i.session_id for i, _ in readable]
        events = self._table(self._events, EVENT_COLUMNS, ids)
        if events is None:
            return []
        raw = self._table(self._raw, RAW_COLUMNS, ids)
        out = []
        for info, dek in readable:
            part = events.filter(
                pc.equal(events["session_id"], pa.scalar(info.session_id, SESSION_ID_TYPE))
            )
            if part.num_rows == 0:
                continue
            raw_part = None
            if raw is not None:
                sel = raw.filter(
                    pc.equal(raw["session_id"], pa.scalar(info.session_id, SESSION_ID_TYPE))
                )
                raw_part = sel if sel.num_rows else None
            out.append(self._session(info, dek, dedup_sorted(part), raw_part))
        return out

    def iter_sessions(
        self, infos: Sequence[SessionInfo], group_size: int = 4
    ) -> Iterator[SessionPackets]:
        ordered = sorted(infos, key=lambda i: i.session_id)
        for i in range(0, len(ordered), group_size):
            yield from self.read_group(ordered[i : i + group_size])

    def _session(
        self, info: SessionInfo, dek: bytes, events: pa.Table, raw: pa.Table | None
    ) -> SessionPackets:
        def num(name: str, dtype: str) -> np.ndarray:
            return events[name].cast(dtype).fill_null(0).to_numpy()

        seq = num("seq", "uint64")
        aead = AESGCM(dek)
        by_seq: dict[int, tuple[bytes, bytes]] = {}
        if raw is not None:
            by_seq = {
                int(s): (n, c)
                for s, n, c in zip(
                    raw["seq"].to_pylist(),
                    raw["nonce"].to_pylist(),
                    raw["ciphertext"].to_pylist(),
                    strict=True,
                )
            }
        payloads: list[bytes | None] = []
        for s in seq.tolist():
            entry = by_seq.get(s)
            if entry is None:
                payloads.append(None)
                continue
            try:
                payloads.append(aead.decrypt(entry[0], entry[1], payload_aad(info.session_id, s)))
            except InvalidTag:
                self.stats.decrypt_failures += 1
                payloads.append(None)
        self.stats.sessions_read += 1
        self.stats.frames += len(seq)
        self.stats.frames_with_payload += sum(p is not None for p in payloads)
        return SessionPackets(
            session_id=info.session_id,
            protocol=info.protocol,
            segment=num("segment", "uint32"),
            seq=seq,
            offset_nanos=num("offset_nanos", "uint64"),
            direction=num("direction", "uint8"),
            state=num("state", "uint8"),
            packet_type=num("packet_type", "uint32"),
            payload_original_size=num("payload_original_size", "uint32"),
            payloads=payloads,
        )
