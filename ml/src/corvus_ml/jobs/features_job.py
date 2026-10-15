"""``build_features``: windows and tokens of a snapshot, written to ``features/<snapshot_id>/``.

Inputs: the snapshot's ``events`` files (from the manifest) and its sessions with segments
(``game_context``), pseudonyms and labels (from the API). Sessions of erased players are never
read. The build is bounded in memory (a few sessions per task, Arrow batches) and uses at most the
CPUs of the cgroup limit.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from corvus_ml.cgroup import default_workers
from corvus_ml.features.config import DEFAULT_DT_EDGES_MS, FeatureConfig
from corvus_ml.features.pipeline import build
from corvus_ml.features.reader import EventFiles
from corvus_ml.features.schema import SESSION_ID_TYPE
from corvus_ml.features.segments import SegmentMap
from corvus_ml.jobs import layout
from corvus_ml.jobs.context import JobContext
from corvus_ml.jobs.lifecycle import JobEnv, JobError, JobOutcome
from corvus_ml.jobs.params import Params

log = logging.getLogger("corvus.job.features")

_TS = pa.timestamp("us", tz="UTC")
LABELS_SCHEMA = pa.schema(
    [
        ("session_id", pa.string()),
        ("label", pa.string()),
        ("source", pa.string()),
        ("confidence", pa.float64()),
        ("valid_from", _TS),
        ("valid_to", _TS),
    ]
)
SESSIONS_SCHEMA = pa.schema(
    [("session_id", pa.string()), ("pseudonym", pa.string()), ("started_at", _TS)]
)


def _timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _protocol(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _hex_id(value: Any, length: int, what: str) -> str:
    try:
        raw = bytes.fromhex(str(value).removeprefix("\\x").replace("-", ""))
    except ValueError:
        raise JobError(f"the API returned a malformed {what}") from None
    if len(raw) != length:
        raise JobError(f"the API returned a {what} of {len(raw)} bytes, expected {length}")
    return raw.hex()


class Selection:
    """Sessions of the snapshot that are used: segments, sessions table and labels table."""

    def __init__(self, contexts: list[str] | None) -> None:
        self._contexts = set(contexts) if contexts else None
        self.segments: list[tuple[bytes, int, str]] = []
        self.sessions: list[dict[str, Any]] = []
        self.labels: list[dict[str, Any]] = []
        self.erased_skipped = 0
        self.outside_snapshot = 0

    def add(self, item: dict[str, Any], allowed: set[str] | None) -> None:
        if item.get("erased"):  # the API filters already; never rely on that alone
            self.erased_skipped += 1
            return
        sid = _hex_id(item.get("session_id"), 16, "session id")
        if allowed is not None and sid not in allowed:
            self.outside_snapshot += 1
            return
        kept = [
            (int(s["segment"]), str(s["game_context"]))
            for s in item.get("segments") or []
            if s.get("game_context")
            and (self._contexts is None or s["game_context"] in self._contexts)
        ]
        if not kept:
            return
        self.segments += [(bytes.fromhex(sid), seg, ctx) for seg, ctx in kept]
        pseudonym = item.get("pseudonym")
        self.sessions.append(
            {
                "session_id": sid,
                "pseudonym": _hex_id(pseudonym, 32, "pseudonym") if pseudonym else None,
                "started_at": _timestamp(item.get("started_at")),
                # Optional in the API response; only the raw-byte build uses it (protocol token).
                "client_protocol": _protocol(item.get("client_protocol")),
            }
        )
        for lab in item.get("labels") or []:
            self.labels.append(
                {
                    "session_id": sid,
                    "label": str(lab["label"]),
                    "source": str(lab.get("source") or ""),
                    "confidence": float(lab.get("confidence", 1.0)),
                    "valid_from": _timestamp(lab.get("valid_from")),
                    "valid_to": _timestamp(lab.get("valid_to")),
                }
            )

    def segment_map(self) -> SegmentMap:
        return SegmentMap.from_table(
            pa.table(
                {
                    "session_id": pa.array([s[0] for s in self.segments], SESSION_ID_TYPE),
                    "segment": pa.array([s[1] for s in self.segments], pa.uint32()),
                    "game_context": pa.array([s[2] for s in self.segments], pa.string()),
                }
            )
        )


def _write_tables(sel: Selection, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(sel.sessions, SESSIONS_SCHEMA), out / layout.SESSIONS_FILE)
    if sel.labels:
        pq.write_table(pa.Table.from_pylist(sel.labels, LABELS_SCHEMA), out / layout.LABELS_FILE)


KINDS = ("aggregate", "raw_bytes", "train_tokenizer")


def build_features(ctx: JobContext, env: JobEnv) -> JobOutcome:
    kind = Params(ctx.params).string("kind", "aggregate")
    if kind not in KINDS:
        raise JobError(f"kind must be one of {KINDS}")
    if kind != "aggregate":
        from corvus_ml.jobs import raw_job

        return raw_job.build_raw(ctx, env, kind)
    p = Params(ctx.params)
    p.string("kind")
    contexts = p.game_contexts()
    cfg = FeatureConfig(
        window=p.integer("window", 256, 1, 100_000),
        overlap=p.number("overlap", 0.5, 0.0, 0.99),
        max_duration_s=p.number("max_duration_s", 30.0, 0.001, 86_400.0),
        min_frames=p.integer("min_frames", 16, 1, 100_000),
        dt_edges_ms=tuple(p.numbers("dt_edges_ms", list(DEFAULT_DT_EDGES_MS)) or ()),
    )
    group_size = p.integer("group_size", 4, 1, 1000)
    cap = default_workers()
    workers = min(p.integer("workers", cap, 1, 256), cap)
    p.done()

    snapshot = env.api.get_snapshot(ctx.snapshot_id)
    manifest = snapshot.get("manifest") or {}
    keys = [f["key"] for f in (manifest.get("files") or {}).get("events") or []]
    if not keys:
        raise JobError("the snapshot manifest lists no events files")
    allowed = {str(s) for s in manifest["sessions"]} if manifest.get("sessions") else None

    sel = Selection(contexts)
    for item in env.api.iter_snapshot_sessions(ctx.snapshot_id):
        sel.add(item, allowed)
    segmap = sel.segment_map()
    if not segmap.by_session:
        raise JobError("no sessions selected: the snapshot has none in the requested game contexts")
    log.info(
        "snapshot %s: %d sessions, %d event files, %d erased skipped",
        ctx.snapshot_id, len(segmap.by_session), len(keys), sel.erased_skipped,
    )  # fmt: skip

    out = env.scratch / "features"
    events = EventFiles(tuple(env.store.path(k) for k in keys), env.store.filesystem)
    stats = build(events, segmap, out, cfg, workers, group_size)
    _write_tables(sel, out)

    prefix = layout.features_prefix(ctx.snapshot_id)
    result = {
        "features_prefix": prefix,
        "sessions": sum(s.sessions for s in stats),
        "frames": sum(s.frames for s in stats),
        "windows": sum(s.windows for s in stats),
        "event_files": len(keys),
        "labels": len(sel.labels),
        "game_contexts": sorted({s[2] for s in sel.segments}),
        "erased_sessions_skipped": sel.erased_skipped,
        "workers": workers,
    }
    # Replace an earlier build completely (fewer groups would leave stale parts), marker last.
    env.store.delete_prefix(prefix)
    env.store.upload_dir(prefix, out)
    env.store.put_bytes(
        f"{prefix}/{layout.BUILD_FILE}",
        json.dumps({**result, "job_id": ctx.job_id, "built_at": env.clock().isoformat()}).encode(),
    )
    return JobOutcome(
        f"built {result['windows']} windows from {result['sessions']} sessions", result
    )
