"""``corvus-pilot`` command line: analyze pilot capture directories."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from corvus_ml.aggregate import analyze_file
from corvus_ml.cgroup import default_workers
from corvus_ml.model import FileResult
from corvus_ml.privacy import Denylist
from corvus_ml.report import build_report, render_text

EXIT_OK = 0
EXIT_PRIVACY = 1
EXIT_USAGE = 2


def find_captures(root: Path) -> list[Path]:
    """All ``*.cvc`` files below root (or root itself if it is a file), sorted."""
    if root.is_file():
        return [root]
    return sorted(root.rglob("*.cvc"))


def analyze_paths(paths: Sequence[Path], denylist: Denylist, workers: int) -> FileResult:
    """Aggregate each file (in a bounded process pool if useful) and merge the results.

    Results are merged as they arrive, so memory stays bounded by the pool size plus the
    merged aggregate, not by the number of files.
    """
    total = FileResult(denylist_active=denylist.active)
    pool_size = max(1, min(workers, len(paths)))
    if pool_size == 1:
        for path in paths:
            total.merge(analyze_file(path, denylist))
        return total
    with ProcessPoolExecutor(max_workers=pool_size) as pool:
        for partial in pool.map(analyze_file, paths, [denylist] * len(paths), chunksize=1):
            total.merge(partial)
    return total


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="corvus-pilot")
    sub = parser.add_subparsers(dest="command", required=True)
    analyze = sub.add_parser("analyze", help="analyze a directory of .cvc capture files")
    analyze.add_argument("captures_dir", type=Path)
    analyze.add_argument("--json", type=Path, dest="json_out", help="also write the report here")
    analyze.add_argument("--denylist", type=Path, help="JSON file with chat/command packet ids")
    analyze.add_argument(
        "--workers",
        type=int,
        help="process pool size (default: from cgroup cpu.max, else CPU count)",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    paths = find_captures(args.captures_dir)
    if not paths:
        print(f"no .cvc files found under {args.captures_dir}", file=sys.stderr)
        return EXIT_USAGE
    if args.workers is not None and args.workers < 1:
        print("--workers must be at least 1", file=sys.stderr)
        return EXIT_USAGE
    denylist = Denylist.from_file(args.denylist) if args.denylist else Denylist()
    workers = args.workers if args.workers is not None else default_workers()

    result = analyze_paths(paths, denylist, workers)
    report = build_report(result)
    if args.json_out:
        args.json_out.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(render_text(report))
    return EXIT_OK if report["privacy"]["passed"] else EXIT_PRIVACY


if __name__ == "__main__":
    sys.exit(main())
