"""``corvus-features`` command line."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from corvus_ml.cgroup import default_workers
from corvus_ml.features.config import DEFAULT_DT_EDGES_MS, FeatureConfig, S3Config
from corvus_ml.features.pipeline import build
from corvus_ml.features.segments import SegmentMap


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="corvus-features")
    sub = p.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="build context windows from lake events")
    b.add_argument("--events", required=True, help="events dataset: directory or s3://bucket/path")
    b.add_argument(
        "--segments", required=True, type=Path, help="segments table (parquet/csv/arrow)"
    )
    b.add_argument("--out", required=True, type=Path)
    b.add_argument("--context", help="only this game_context")
    b.add_argument("--session", action="append", default=None, help="session id hex (repeatable)")
    b.add_argument("--window", type=int, default=256, help="serverbound frames per window")
    b.add_argument("--overlap", type=float, default=0.5)
    b.add_argument("--max-duration", type=float, default=30.0, help="seconds")
    b.add_argument("--min-frames", type=int, default=16)
    b.add_argument(
        "--dt-edges-ms",
        default=",".join(str(e) for e in DEFAULT_DT_EDGES_MS),
        help="comma separated bucket upper edges in ms",
    )
    b.add_argument("--workers", type=int, default=None, help="default: from the cgroup CPU limit")
    b.add_argument("--group-size", type=int, default=4, help="sessions read per task")
    b.add_argument("--s3-endpoint", help="S3 endpoint override (MinIO)")
    b.add_argument("--s3-region")
    b.add_argument("--s3-scheme", default="https", choices=["http", "https"])
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cfg = FeatureConfig(
        window=args.window,
        overlap=args.overlap,
        max_duration_s=args.max_duration,
        min_frames=args.min_frames,
        dt_edges_ms=tuple(float(e) for e in args.dt_edges_ms.split(",")),
    )
    sessions = [bytes.fromhex(s) for s in args.session] if args.session else None
    segmap = SegmentMap.load(args.segments).select(args.context, sessions)
    if not segmap.by_session:
        print("no sessions selected", file=sys.stderr)
        return 1
    workers = args.workers if args.workers is not None else default_workers()
    s3 = S3Config(args.s3_endpoint, args.s3_region, args.s3_scheme)
    stats = build(args.events, segmap, args.out, cfg, max(1, workers), args.group_size, s3)
    print(
        f"sessions={sum(s.sessions for s in stats)} frames={sum(s.frames for s in stats)} "
        f"windows={sum(s.windows for s in stats)} groups={len(stats)} workers={workers}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
