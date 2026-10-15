"""Group-wise, parallel build of ``windows`` and ``window_tokens`` from lake events."""

from __future__ import annotations

import multiprocessing
from collections.abc import Iterator, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from corvus_ml.features.config import FeatureConfig, S3Config
from corvus_ml.features.reader import EventReader, open_dataset
from corvus_ml.features.schema import (
    PACKET_CLIENT_TICK_END,
    SESSION_ID_TYPE,
    TOKEN_FEATURES,
    WINDOW_TOKENS_SCHEMA,
    WINDOWS_SCHEMA,
)
from corvus_ml.features.segments import SegmentMap
from corvus_ml.features.tokens import TokenArrays, extract_tokens
from corvus_ml.features.windows import aggregates, window_bounds

WINDOWS_DIR = "windows"
TOKENS_DIR = "window_tokens"


@dataclass(frozen=True)
class GroupStats:
    index: int
    sessions: int = 0
    frames: int = 0
    windows: int = 0


def make_groups(session_ids: Sequence[bytes], group_size: int) -> list[tuple[bytes, ...]]:
    """Sorted sessions in consecutive groups of at most ``group_size``."""
    if group_size < 1:
        raise ValueError("group_size must be >= 1")
    ordered = sorted(session_ids)
    return [tuple(ordered[i : i + group_size]) for i in range(0, len(ordered), group_size)]


def _frame_columns(table: pa.Table) -> dict[str, np.ndarray]:
    def num(name: str, dtype: str, null: float | int) -> np.ndarray:
        col = table[name].cast(dtype).fill_null(null)
        return col.to_numpy()

    ground = pc.if_else(table["on_ground"], 1, 0).fill_null(-1).cast("int8")
    return {
        "segment": num("segment", "uint32", 0),
        "seq": num("seq", "uint64", 0),
        "offset_nanos": num("offset_nanos", "uint64", 0),
        "direction": num("direction", "uint8", 0),
        "packet_type": num("packet_type", "uint32", 0),
        "event_kind": num("event_kind", "uint8", 0),
        "has_position": table["has_position"].fill_null(False).to_numpy(),
        "has_rotation": table["has_rotation"].fill_null(False).to_numpy(),
        "on_ground": ground.to_numpy(),
        "x": num("x", "float64", np.nan),
        "y": num("y", "float64", np.nan),
        "z": num("z", "float64", np.nan),
        "yaw": num("yaw", "float64", np.nan),
        "pitch": num("pitch", "float64", np.nan),
        "entity_id": num("entity_id", "int32", -1),
        "action": num("action", "uint8", 0),
    }


def _segment_runs(segment: np.ndarray) -> Iterator[tuple[int, int, int]]:
    """Consecutive runs of equal segment as (segment, lo, hi)."""
    if len(segment) == 0:
        return
    cuts = np.flatnonzero(segment[1:] != segment[:-1]) + 1
    starts = np.concatenate(([0], cuts))
    ends = np.concatenate((cuts, [len(segment)]))
    for lo, hi in zip(starts.tolist(), ends.tolist(), strict=True):
        yield int(segment[lo]), lo, hi


class _Sink:
    """Lazily opened Parquet file; identical settings for every run (deterministic bytes)."""

    def __init__(self, path: Path, schema: pa.Schema) -> None:
        self._path, self._schema = path, schema
        self._writer: pq.ParquetWriter | None = None

    def write(self, table: pa.Table) -> None:
        if self._writer is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._writer = pq.ParquetWriter(
                self._path, self._schema, compression="zstd", compression_level=3
            )
        self._writer.write_table(table)

    def close(self) -> None:
        if self._writer is not None:
            self._writer.close()


def _window_rows(
    sid: bytes,
    segment: int,
    context: str,
    first_index: int,
    tokens: TokenArrays,
    cfg: FeatureConfig,
) -> tuple[pa.Table, pa.Table] | None:
    """Windows + token rows of one segment run."""
    bounds = window_bounds(tokens.offset_nanos, cfg)
    if not bounds:
        return None
    has_tick = bool((tokens.features["packet_type"] == PACKET_CLIENT_TICK_END).any())
    aggs = [aggregates(tokens, w, has_tick) for w in bounds]
    n = len(bounds)
    idx = np.arange(first_index, first_index + n, dtype=np.uint32)
    lo = np.array([w.lo for w in bounds])
    hi = np.array([w.hi for w in bounds])
    sizes = hi - lo
    win_cols: dict[str, pa.Array] = {
        "session_id": pa.array([sid] * n, SESSION_ID_TYPE),
        "segment": pa.array(np.full(n, segment, dtype=np.uint32)),
        "game_context": pa.array([context] * n, pa.string()),
        "window_index": pa.array(idx),
        "start_offset_nanos": pa.array(tokens.offset_nanos[lo]),
        "end_offset_nanos": pa.array(tokens.offset_nanos[hi - 1]),
        "first_seq": pa.array(tokens.seq[lo]),
        "last_seq": pa.array(tokens.seq[hi - 1]),
        "n_frames": pa.array(sizes.astype(np.uint32)),
        "cut": pa.array([w.cut for w in bounds], pa.string()),
    }
    for name in aggs[0]:
        win_cols[name] = pa.array([a[name] for a in aggs], pa.float64())
    windows = pa.table(win_cols, schema=WINDOWS_SCHEMA)

    frame_idx = np.concatenate(
        [np.arange(a, b) for a, b in zip(lo.tolist(), hi.tolist(), strict=True)]
    )
    total = len(frame_idx)
    tok_cols: dict[str, pa.Array] = {
        "session_id": pa.array([sid] * total, SESSION_ID_TYPE),
        "segment": pa.array(np.full(total, segment, dtype=np.uint32)),
        "window_index": pa.array(np.repeat(idx, sizes)),
        "pos": pa.array((frame_idx - np.repeat(lo, sizes)).astype(np.uint16)),
        "seq": pa.array(tokens.seq[frame_idx]),
        "offset_nanos": pa.array(tokens.offset_nanos[frame_idx]),
    }
    for name, typ in TOKEN_FEATURES:
        tok_cols[name] = pa.array(tokens.features[name][frame_idx], typ)
    return windows, pa.table(tok_cols, schema=WINDOW_TOKENS_SCHEMA)


def build_group(
    reader: EventReader,
    segmap: SegmentMap,
    cfg: FeatureConfig,
    index: int,
    session_ids: Sequence[bytes],
    out_dir: Path,
) -> GroupStats:
    """Read one group of sessions, write ``part-<index>.parquet`` for both output tables."""
    tables = reader.read_group(session_ids)
    win_sink = _Sink(out_dir / WINDOWS_DIR / f"part-{index:06d}.parquet", WINDOWS_SCHEMA)
    tok_sink = _Sink(out_dir / TOKENS_DIR / f"part-{index:06d}.parquet", WINDOW_TOKENS_SCHEMA)
    pending_w: list[pa.Table] = []
    pending_t: list[pa.Table] = []
    n_windows = n_frames = n_sessions = 0

    def flush() -> None:
        if pending_w:
            win_sink.write(pa.concat_tables(pending_w))
            tok_sink.write(pa.concat_tables(pending_t))
            pending_w.clear()
            pending_t.clear()

    try:
        for sid in sorted(tables):
            contexts = segmap.by_session.get(sid, {})
            cols = _frame_columns(tables[sid])
            n_sessions += 1
            counters: dict[int, int] = {}
            for segment, lo, hi in _segment_runs(cols["segment"]):
                context = contexts.get(segment)
                if context is None:
                    continue
                run = {k: v[lo:hi] for k, v in cols.items()}
                tokens = extract_tokens(run, cfg)
                n_frames += len(tokens)
                rows = _window_rows(sid, segment, context, counters.get(segment, 0), tokens, cfg)
                if rows is None:
                    continue
                counters[segment] = counters.get(segment, 0) + rows[0].num_rows
                n_windows += rows[0].num_rows
                pending_w.append(rows[0])
                pending_t.append(rows[1])
                if sum(t.num_rows for t in pending_w) >= cfg.flush_windows:
                    flush()
        flush()
    finally:
        win_sink.close()
        tok_sink.close()
    return GroupStats(index, n_sessions, n_frames, n_windows)


# --- process pool plumbing -------------------------------------------------------------------

_STATE: dict[str, object] = {}


def _init_worker(
    events: str, s3: S3Config | None, segmap: SegmentMap, cfg: FeatureConfig, out_dir: str
) -> None:
    pa.set_cpu_count(1)
    pa.set_io_thread_count(1)
    _STATE["reader"] = EventReader(open_dataset(events, s3), cfg.batch_rows)
    _STATE["segmap"], _STATE["cfg"], _STATE["out"] = segmap, cfg, Path(out_dir)


def _run_task(task: tuple[int, tuple[bytes, ...]]) -> GroupStats:
    index, ids = task
    return build_group(
        _STATE["reader"],  # type: ignore[arg-type]
        _STATE["segmap"],  # type: ignore[arg-type]
        _STATE["cfg"],  # type: ignore[arg-type]
        index,
        ids,
        _STATE["out"],  # type: ignore[arg-type]
    )


def build(
    events: str | Path,
    segmap: SegmentMap,
    out_dir: Path,
    cfg: FeatureConfig,
    workers: int = 1,
    group_size: int = 4,
    s3: S3Config | None = None,
) -> list[GroupStats]:
    """Build both tables. Output is independent of ``workers`` (one file per group index)."""
    groups = make_groups(segmap.sessions(), group_size)
    tasks = list(enumerate(groups))
    out_dir.mkdir(parents=True, exist_ok=True)
    if workers <= 1 or len(tasks) <= 1:
        reader = EventReader(open_dataset(events, s3), cfg.batch_rows)
        return [build_group(reader, segmap, cfg, i, ids, out_dir) for i, ids in tasks]
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(
        max_workers=min(workers, len(tasks)),
        mp_context=ctx,
        initializer=_init_worker,
        initargs=(str(events), s3, segmap, cfg, str(out_dir)),
    ) as pool:
        return list(pool.map(_run_task, tasks))
