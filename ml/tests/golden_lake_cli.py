"""Regenerate the golden fixture: `PYTHONPATH=tests uv run python tests/golden_lake_cli.py`.

Only run this deliberately: the fixture pins the output of the feature stage (docs/reference/
features.md), so a change to it is a change of the feature definition and needs a spec bump.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from corvus_ml.features.config import FeatureConfig
from corvus_ml.features.pipeline import build
from corvus_ml.features.segments import SegmentMap
from golden_lake import GOLDEN_DIR, SEGMENTS, write_golden_events

GOLDEN_CONFIG = FeatureConfig(window=32, overlap=0.5, max_duration_s=12.0, min_frames=4)


def golden_segmap() -> SegmentMap:
    return SegmentMap.from_table(
        pa.table(
            {
                "session_id": pa.array([r[0] for r in SEGMENTS], pa.binary(16)),
                "segment": pa.array([r[1] for r in SEGMENTS], pa.uint32()),
                "game_context": [r[2] for r in SEGMENTS],
            }
        )
    )


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        events = write_golden_events(tmp)
        build(events, golden_segmap(), tmp / "out", GOLDEN_CONFIG, group_size=1)
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        for name in ("windows", "window_tokens"):
            table = ds.dataset(tmp / "out" / name, format="parquet").to_table()
            print(name, table.num_rows, file=sys.stderr)
            pq.write_table(
                table, GOLDEN_DIR / f"{name}.parquet", compression="zstd", compression_level=9
            )


if __name__ == "__main__":
    main()
