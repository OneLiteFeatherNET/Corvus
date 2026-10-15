"""Raw-byte evaluation data: packet table + sessions + labels -> time windows -> :class:`EvalData`.

The packet table (``features/<snapshot>/raw/packets``) is window-agnostic, so the tuner can cut
different windows from the same source. Windows of sessions without a pseudonym are dropped, labels
are joined exactly like for the aggregate windows (``corvus_ml.eval.dataset``).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.dataset as pads

from corvus_ml.eval.data import EvalData
from corvus_ml.eval.dataset import (
    _NULL_NS,
    _to_bytes,
    _to_ns,
    join_labels,
    parse_labels,
    read_table,
)
from corvus_ml.features.spec import SPEC_FILE, FeatureSpec
from corvus_ml.raw.spec import RawBytesSpec, WindowConfig
from corvus_ml.raw.windows import build_windows, streams_from_table

log = logging.getLogger(__name__)

PACKETS_DIR = "packets"


@dataclass
class RawSource:
    """Tokenized packets of a snapshot plus the tables needed to label windows."""

    packets: pa.Table
    sessions: pa.Table
    labels: pa.Table | None
    spec: RawBytesSpec
    tokenizer_dir: Path | None = None
    synthetic: bool = False

    def with_window(self, window: WindowConfig) -> RawSource:
        return replace(self, spec=replace(self.spec, window=window))

    def feature_spec(self) -> FeatureSpec:
        return FeatureSpec.current(raw_bytes=self.spec)

    def build(
        self, min_confidence: float = 0.0, max_windows: int | None = None, seed: int = 0
    ) -> EvalData:
        spec = self.spec
        s_ids = [_to_bytes(v) for v in self.sessions["session_id"].to_pylist()]
        s_pseud = [
            _to_bytes(v) if v is not None else None for v in self.sessions["pseudonym"].to_pylist()
        ]
        pseud_of = {s: p for s, p in zip(s_ids, s_pseud, strict=True) if p is not None}
        start_of = (
            dict(zip(s_ids, _to_ns(self.sessions["started_at"]).tolist(), strict=True))
            if "started_at" in self.sessions.column_names
            else {}
        )
        protocols: dict[bytes, int | None] = {}
        if "client_protocol" in self.sessions.column_names:
            protocols = dict(zip(s_ids, self.sessions["client_protocol"].to_pylist(), strict=True))
        streams = [
            s
            for s in streams_from_table(self.packets, spec.packet, protocols)
            if s.session_id in pseud_of
        ]
        rw = build_windows(streams, spec.layout, spec.packet, spec.window)
        n = len(rw)
        if max_windows is not None and n > max_windows:
            keep = np.sort(np.random.default_rng(seed).choice(n, max_windows, replace=False))
        else:
            keep = np.arange(n)
        sid = rw.session_id[keep]
        pseud = np.empty(len(keep), dtype=object)
        pseud[:] = [pseud_of[s] for s in sid]
        base = np.array([start_of.get(s, _NULL_NS) for s in sid], dtype=np.int64)
        wtime = np.where(base == _NULL_NS, _NULL_NS, base + rw.start_offset_nanos[keep])
        if self.labels is not None:
            label, conf = join_labels(sid, pseud, wtime, parse_labels(self.labels), min_confidence)
        else:
            label = np.full(len(keep), -1, dtype=np.int8)
            conf = np.zeros(len(keep), dtype=np.float32)
        return EvalData(
            session_id=sid,
            segment=rw.segment[keep],
            window_index=rw.window_index[keep],
            game_context=rw.game_context[keep],
            pseudonym=pseud,
            label=label,
            confidence=conf,
            agg=np.zeros((len(keep), 0), dtype=np.float32),
            meta={"raw_spec": spec, "tokenizer_dir": self.tokenizer_dir},
            raw_ids=rw.ids[keep],
            raw_time=rw.time_s[keep],
        )


def load_raw_source(
    directory: Path, window: WindowConfig | None = None, labels: Path | None = None
) -> RawSource:
    """Load ``features/<snapshot>/raw`` (downloaded): packets, sessions, labels, spec, tokenizer."""
    directory = Path(directory)
    spec = FeatureSpec.load(directory / SPEC_FILE, require_tokenizer=True).raw_bytes
    if spec is None:
        raise ValueError(f"{directory / SPEC_FILE} is not a raw_bytes feature spec")
    if window is not None:
        spec = replace(spec, window=window)
    label_path = labels or directory / "labels.parquet"
    return RawSource(
        packets=pads.dataset(str(directory / PACKETS_DIR), format="parquet").to_table(),
        sessions=read_table(directory / "sessions.parquet"),
        labels=read_table(label_path) if Path(label_path).is_file() else None,
        spec=spec,
        tokenizer_dir=directory,
    )


def describe(data: EvalData) -> dict:
    """Small summary for reports."""
    assert data.raw_ids is not None
    real = (data.raw_ids[..., 0] != 0).sum(axis=-1)
    return json.loads(
        json.dumps(
            {
                "windows": len(data),
                "packets_per_window_mean": float(real.sum(-1).mean()) if len(data) else 0.0,
                "packets_per_window_max": int(real.sum(-1).max()) if len(data) else 0,
            }
        )
    )
