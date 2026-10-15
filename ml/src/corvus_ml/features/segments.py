"""Segment -> game_context lookup supplied as a small table (Parquet, CSV or Arrow IPC)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.ipc as paipc
import pyarrow.parquet as pq

from corvus_ml.features.schema import SESSION_ID_TYPE


def _to_bytes(value: object) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return bytes.fromhex(value.removeprefix("\\x").replace("-", ""))
    raise TypeError(f"unsupported session_id value: {type(value).__name__}")


def read_table(path: Path) -> pa.Table:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        convert = pacsv.ConvertOptions(
            column_types={"session_id": pa.string(), "game_context": pa.string()}
        )
        return pacsv.read_csv(path, convert_options=convert)
    if suffix in (".arrow", ".feather", ".ipc"):
        with paipc.open_file(path) as reader:
            return reader.read_all()
    return pq.read_table(path)


@dataclass
class SegmentMap:
    """Maps session id -> {segment -> game_context}. Session ids are 16 raw bytes."""

    by_session: dict[bytes, dict[int, str]] = field(default_factory=dict)

    @classmethod
    def from_table(cls, table: pa.Table) -> SegmentMap:
        missing = {"session_id", "segment", "game_context"} - set(table.column_names)
        if missing:
            raise ValueError(f"segments table lacks columns: {sorted(missing)}")
        out: dict[bytes, dict[int, str]] = {}
        sids = table["session_id"].to_pylist()
        segs = table["segment"].to_pylist()
        ctxs = table["game_context"].to_pylist()
        for sid, seg, ctx in zip(sids, segs, ctxs, strict=True):
            if sid is None or seg is None or ctx is None:
                continue
            key = _to_bytes(sid)
            if len(key) != SESSION_ID_TYPE.byte_width:
                raise ValueError("session_id must be 16 bytes")
            out.setdefault(key, {})[int(seg)] = str(ctx)
        return cls(out)

    @classmethod
    def load(cls, path: Path) -> SegmentMap:
        return cls.from_table(read_table(path))

    def select(
        self, context: str | None = None, session_ids: list[bytes] | None = None
    ) -> SegmentMap:
        """Restrict to a game context and/or a list of sessions."""
        wanted = set(session_ids) if session_ids is not None else None
        out: dict[bytes, dict[int, str]] = {}
        for sid, segs in self.by_session.items():
            if wanted is not None and sid not in wanted:
                continue
            kept = {s: c for s, c in segs.items() if context is None or c == context}
            if kept:
                out[sid] = kept
        return SegmentMap(out)

    def sessions(self) -> list[bytes]:
        """Session ids in stable (sorted) order."""
        return sorted(self.by_session)
