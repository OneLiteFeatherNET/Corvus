"""Streaming reader for the lake ``events`` table with de-duplication and ordering."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.fs as pafs

from corvus_ml.features.config import S3Config
from corvus_ml.features.schema import EVENT_COLUMNS, SESSION_ID_TYPE


@dataclass(frozen=True)
class EventFiles:
    """An explicit list of ``events`` Parquet files on a filesystem (a dataset snapshot).

    Picklable, so worker processes can open the same dataset.
    """

    paths: tuple[str, ...]
    filesystem: pafs.FileSystem


def open_dataset(path: str | Path | EventFiles, s3: S3Config | None = None) -> ds.Dataset:
    """Open ``events`` from a local directory/file, an ``s3://`` path (hive ``date=`` ok) or an
    explicit :class:`EventFiles` list."""
    if isinstance(path, EventFiles):
        return ds.dataset(list(path.paths), format="parquet", filesystem=path.filesystem)
    text = str(path)
    filesystem: pafs.FileSystem | None = None
    if text.startswith("s3://"):
        text = text.removeprefix("s3://")
        if s3 is not None:
            filesystem = pafs.S3FileSystem(
                endpoint_override=s3.endpoint_override, region=s3.region, scheme=s3.scheme
            )
        else:
            filesystem = pafs.S3FileSystem()
    return ds.dataset(text, format="parquet", filesystem=filesystem, partitioning="hive")


class EventReader:
    """Reads the frames of a bounded group of sessions; never the whole dataset."""

    def __init__(self, dataset: ds.Dataset, batch_rows: int = 65_536) -> None:
        self._dataset = dataset
        self._batch_rows = batch_rows

    def iter_batches(self, session_ids: Sequence[bytes]) -> Iterator[pa.RecordBatch]:
        """Projected, filtered record batches (at most ``batch_rows`` rows each)."""
        values = pa.array(list(session_ids), type=SESSION_ID_TYPE)
        scanner = self._dataset.scanner(
            columns=list(EVENT_COLUMNS),
            filter=pc.field("session_id").isin(values),
            batch_size=self._batch_rows,
        )
        yield from scanner.to_batches()

    def read_group(self, session_ids: Sequence[bytes]) -> dict[bytes, pa.Table]:
        """Per session: de-duplicated and ordered by ``seq``; sessions without rows are absent."""
        batches = list(self.iter_batches(session_ids))
        if not batches:
            return {}
        table = pa.Table.from_batches(batches)
        out: dict[bytes, pa.Table] = {}
        for sid in session_ids:
            part = table.filter(pc.equal(table["session_id"], pa.scalar(sid, SESSION_ID_TYPE)))
            if part.num_rows:
                out[sid] = dedup_sorted(part)
        return out


def dedup_sorted(table: pa.Table) -> pa.Table:
    """Order by ``seq`` (stable) and keep the first row of every duplicate ``seq``."""
    table = table.sort_by([("seq", "ascending")])
    seq = table["seq"].to_numpy()
    if len(seq) < 2:
        return table
    keep = np.concatenate(([True], seq[1:] != seq[:-1]))
    return table if keep.all() else table.filter(pa.array(keep))
