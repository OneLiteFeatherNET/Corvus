"""Declarative pool definitions: the filter model, its translation to parameterised SQL, preview math.

The SQL builder only ever emits constant SQL fragments; every user-supplied value travels as a named
parameter (``%(name)s``) and identifiers never come from the filter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_LIST_ITEMS = 200


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProtocolRange(_Strict):
    min: int | None = Field(default=None, ge=0)
    max: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> ProtocolRange:
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("client_protocol.min must not exceed max")
        return self


class BrandFilter(_Strict):
    include: list[str] = Field(default_factory=list, max_length=MAX_LIST_ITEMS)
    exclude: list[str] = Field(default_factory=list, max_length=MAX_LIST_ITEMS)


class DateRange(_Strict):
    """Inclusive UTC calendar dates of the session start."""

    start: date | None = None
    end: date | None = None

    @model_validator(mode="after")
    def _ordered(self) -> DateRange:
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError("date_range.start must not be after end")
        return self


class LabelFilter(_Strict):
    value: Literal["legit", "cheat", "labeled", "unlabeled"]
    min_confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class PoolFilter(_Strict):
    game_contexts: list[str] = Field(default_factory=list, max_length=MAX_LIST_ITEMS)
    backend_servers: list[str] = Field(default_factory=list, max_length=MAX_LIST_ITEMS)
    client_protocol: ProtocolRange = Field(default_factory=ProtocolRange)
    client_brand: BrandFilter = Field(default_factory=BrandFilter)
    date_range: DateRange = Field(default_factory=DateRange)
    label: LabelFilter | None = None
    min_session_duration_s: float | None = Field(default=None, ge=0)
    max_drop_rate: float | None = Field(default=None, ge=0, le=1)
    exclude_orphans: bool = True
    # Informational for retention jobs; it does not influence which sessions are selected.
    retention_days: int | None = Field(default=None, ge=1, le=3650)


class PoolIn(_Strict):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    filter: PoolFilter = Field(default_factory=PoolFilter)
    # NFR-018: how long raw payloads of the pool's sessions are kept (the upper limit is a setting
    # and is checked by the endpoint). null: the deployment default.
    raw_retention_days: int | None = Field(default=None, ge=1)


@dataclass(frozen=True)
class PoolQuery:
    """Two selections sharing one parameter dict: matching sessions and matching segments."""

    sessions_sql: str
    segments_sql: str
    params: dict[str, Any] = field(default_factory=dict)


# Labels that apply to session ``s``: directly, or through its pseudonym, valid at the session start.
LABEL_MATCH = (
    "(l.session_id = s.session_id OR l.pseudonym = s.pseudonym)"
    " AND (l.valid_from IS NULL OR l.valid_from <= s.started_at)"
    " AND (l.valid_to IS NULL OR l.valid_to > s.started_at)"
)

_DROPS = "(s.dropped_queue_full + s.dropped_privacy + s.dropped_transport)"
_EST_FRAMES = (
    "(SELECT coalesce(sum(f.row_count::float8 / n.cnt), 0)"
    " FROM corvus.lake_file_sessions x"
    " JOIN corvus.lake_files f ON f.id = x.file_id AND f.table_name = 'events' AND f.deleted_at IS NULL"
    " JOIN LATERAL (SELECT count(*) AS cnt FROM corvus.lake_file_sessions y"
    " WHERE y.file_id = x.file_id) n ON true"
    " WHERE x.session_id = s.session_id)"
)


def _utc_start(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=UTC)


def build_pool_query(f: PoolFilter) -> PoolQuery:
    params: dict[str, Any] = {}

    seg: list[str] = []
    if f.game_contexts:
        seg.append("g.game_context = ANY(%(game_contexts)s)")
        params["game_contexts"] = list(f.game_contexts)
    if f.backend_servers:
        seg.append("g.backend_server = ANY(%(backend_servers)s)")
        params["backend_servers"] = list(f.backend_servers)
    if f.client_brand.include:
        seg.append("g.client_brand = ANY(%(brand_include)s)")
        params["brand_include"] = list(f.client_brand.include)
    if f.client_brand.exclude:
        seg.append("NOT (g.client_brand = ANY(%(brand_exclude)s))")
        params["brand_exclude"] = list(f.client_brand.exclude)

    # Never select data of erased players.
    where = [
        "(s.pseudonym IS NULL OR NOT EXISTS (SELECT 1 FROM corvus.players p"
        " WHERE p.pseudonym = s.pseudonym AND p.erased_at IS NOT NULL))"
    ]
    if f.exclude_orphans:
        where.append("s.orphan = false")
    if seg:
        where.append(
            "EXISTS (SELECT 1 FROM corvus.segments g WHERE g.session_id = s.session_id AND "
            + " AND ".join(seg)
            + ")"
        )
    if f.client_protocol.min is not None:
        where.append("s.client_protocol >= %(protocol_min)s")
        params["protocol_min"] = f.client_protocol.min
    if f.client_protocol.max is not None:
        where.append("s.client_protocol <= %(protocol_max)s")
        params["protocol_max"] = f.client_protocol.max
    if f.date_range.start is not None:
        where.append("s.started_at >= %(date_start)s")
        params["date_start"] = _utc_start(f.date_range.start)
    if f.date_range.end is not None:
        where.append("s.started_at < %(date_end)s")
        params["date_end"] = _utc_start(f.date_range.end + timedelta(days=1))
    if f.min_session_duration_s is not None:
        where.append(
            "s.ended_at IS NOT NULL AND s.ended_at - s.started_at >= make_interval(secs => %(min_duration_s)s)"
        )
        params["min_duration_s"] = f.min_session_duration_s
    if f.max_drop_rate is not None:
        where.append(
            f"({_DROPS} = 0 OR {_DROPS}::float8 / ({_DROPS} + {_EST_FRAMES}) <= %(max_drop_rate)s)"
        )
        params["max_drop_rate"] = f.max_drop_rate
    if f.label is not None:
        where.append(_label_condition(f.label, params))

    sessions_sql = "SELECT s.session_id FROM corvus.sessions s WHERE " + " AND ".join(where)
    seg_where = " AND ".join(["g.session_id IN (" + sessions_sql + ")", *seg])
    segments_sql = "SELECT g.session_id, g.segment FROM corvus.segments g WHERE " + seg_where
    return PoolQuery(sessions_sql, segments_sql, params)


def _label_condition(lf: LabelFilter, params: dict[str, Any]) -> str:
    base = f"SELECT 1 FROM corvus.labels l WHERE {LABEL_MATCH}"
    if lf.value == "unlabeled":
        return f"NOT EXISTS ({base})"
    if lf.value == "labeled":
        params["label_min_confidence"] = lf.min_confidence
        return f"EXISTS ({base} AND l.confidence >= %(label_min_confidence)s)"
    params["label_value"] = lf.value
    params["label_min_confidence"] = lf.min_confidence
    return f"EXISTS ({base} AND l.label = %(label_value)s AND l.confidence >= %(label_min_confidence)s)"


def preview_statements(q: PoolQuery) -> dict[str, str]:
    """Aggregate statements for ``POST /pools/{id}/preview``; all share ``q.params``."""
    return {
        "sessions": f"SELECT count(*) FROM ({q.sessions_sql}) q",
        "segments": f"SELECT count(*) FROM ({q.segments_sql}) q",
        "players": (
            "SELECT count(DISTINCT s.pseudonym) FROM corvus.sessions s"
            f" WHERE s.session_id IN ({q.sessions_sql})"
        ),
        "labels": (
            "SELECT coalesce(l.label, 'unlabeled') AS label, count(*)"
            " FROM corvus.sessions s LEFT JOIN LATERAL ("
            f"SELECT l.label FROM corvus.labels l WHERE {LABEL_MATCH}"
            " ORDER BY l.confidence DESC, l.created_at DESC, l.id DESC LIMIT 1) l ON true"
            f" WHERE s.session_id IN ({q.sessions_sql}) GROUP BY 1"
        ),
        "files": (
            "WITH sel AS (SELECT lfs.file_id, count(*) AS n FROM corvus.lake_file_sessions lfs"
            f" WHERE lfs.session_id IN ({q.sessions_sql}) GROUP BY lfs.file_id),"
            " tot AS (SELECT lfs.file_id, count(*) AS n FROM corvus.lake_file_sessions lfs"
            " WHERE lfs.file_id IN (SELECT file_id FROM sel) GROUP BY lfs.file_id)"
            " SELECT f.row_count, tot.n, sel.n FROM corvus.lake_files f"
            " JOIN sel ON sel.file_id = f.id JOIN tot ON tot.file_id = f.id"
            " WHERE f.table_name = 'events' AND f.deleted_at IS NULL"
        ),
    }


def snapshot_statements(q: PoolQuery) -> dict[str, str]:
    """Selections that materialise a snapshot (run in one repeatable-read transaction)."""
    return {
        "sessions": f"{q.sessions_sql} ORDER BY 1",
        "files": (
            "SELECT f.id, f.s3_key FROM corvus.lake_files f"
            " WHERE f.table_name = 'events' AND f.deleted_at IS NULL AND f.id IN ("
            "SELECT lfs.file_id FROM corvus.lake_file_sessions lfs"
            f" WHERE lfs.session_id IN ({q.sessions_sql})) ORDER BY f.id"
        ),
    }


@dataclass(frozen=True)
class FileShare:
    """One ``events`` file: its rows, the sessions it holds and how many of them are selected."""

    row_count: int
    total_sessions: int
    selected_sessions: int


@dataclass(frozen=True)
class RawPreview:
    sessions: int
    segments: int
    players: int
    labels: dict[str, int]
    files: list[FileShare]


class PreviewOut(BaseModel):
    sessions: int
    segments: int
    distinct_players: int
    events_files: int
    total_frames_estimate: int = Field(
        description="Approximation: each file's row count weighted by the share of its sessions that are selected."
    )
    label_distribution: dict[str, int]


def compute_preview(raw: RawPreview) -> PreviewOut:
    frames = 0.0
    for f in raw.files:
        if f.total_sessions > 0:
            frames += f.row_count * min(f.selected_sessions, f.total_sessions) / f.total_sessions
    dist = {"legit": 0, "cheat": 0, "unlabeled": 0}
    dist.update(raw.labels)
    return PreviewOut(
        sessions=raw.sessions,
        segments=raw.segments,
        distinct_players=raw.players,
        events_files=len(raw.files),
        total_frames_estimate=round(frames),
        label_distribution=dist,
    )
