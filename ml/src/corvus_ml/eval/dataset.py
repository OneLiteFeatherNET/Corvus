"""Dataset loader: windows + tokens + sessions + labels -> :class:`EvalData`.

Tables
------
``windows`` / ``window_tokens``: output of ``corvus-features build`` (docs/reference/features.md).
``sessions``: ``session_id``, ``pseudonym`` and optionally ``started_at`` (catalog export).
``labels``: ``pseudonym`` or ``session_id`` (one per row), ``label`` (legit|cheat), ``source``,
``confidence``, ``valid_from``, ``valid_to``. Parquet or CSV; ids in CSV are hex strings.

A label applies to a window when the window's absolute start time
(``sessions.started_at + windows.start_offset_nanos``) lies in ``[valid_from, valid_to)``. A null
bound is open. If a window has no known time, only labels without bounds apply. When several labels
match, the highest confidence wins; on a tie ``cheat`` wins.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv
import pyarrow.dataset as pads
import pyarrow.parquet as pq

from corvus_ml.eval.data import (
    AGG_COLUMNS,
    LABEL_CHEAT,
    LABEL_LEGIT,
    LABEL_UNLABELLED,
    N_SEQ_FEATURES,
    NULLABLE_AGG_COLUMNS,
    SEQ_FEATURES,
    EvalData,
    apply_transform,
)

log = logging.getLogger(__name__)

_ID_COLUMNS = {"session_id": pa.string(), "pseudonym": pa.string()}
_LABEL_NAMES = {"legit": LABEL_LEGIT, "cheat": LABEL_CHEAT}


def _to_bytes(value: object) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return bytes.fromhex(value.removeprefix("\\x").replace("-", ""))
    raise TypeError(f"unsupported id value: {type(value).__name__}")


def read_table(path: str | Path) -> pa.Table:
    """Read a Parquet or CSV table; ids in CSV stay strings (hex)."""
    path = Path(path)
    if path.suffix.lower() == ".csv":
        convert = pacsv.ConvertOptions(column_types=_ID_COLUMNS)
        return pacsv.read_csv(path, convert_options=convert)
    return pq.read_table(path)


def _to_ns(column: pa.ChunkedArray) -> np.ndarray:
    """Timestamp column -> int64 ns since epoch; null becomes ``iinfo.min`` (sentinel)."""
    if pa.types.is_null(column.type):  # an all-empty CSV column
        return np.full(len(column), _NULL_NS, dtype=np.int64)
    if pa.types.is_string(column.type):
        column = pc.cast(column, pa.timestamp("ns", tz="UTC"))
    elif pa.types.is_timestamp(column.type):
        column = pc.cast(column, pa.timestamp("ns", tz=column.type.tz))
    elif pa.types.is_integer(column.type):
        column = pc.cast(column, pa.int64()).cast(pa.timestamp("ns"))
    else:
        raise ValueError(f"unsupported time column type: {column.type}")
    ints = pc.cast(column, pa.int64())
    return ints.fill_null(np.iinfo(np.int64).min).to_numpy(zero_copy_only=False)


_NULL_NS = np.iinfo(np.int64).min


def parse_labels(table: pa.Table) -> list[dict]:
    """Normalise a labels table to dict rows (see module docstring)."""
    cols = set(table.column_names)
    if "label" not in cols:
        raise ValueError("labels table needs a 'label' column")
    if not cols & {"pseudonym", "session_id"}:
        raise ValueError("labels table needs a 'pseudonym' or 'session_id' column")
    n = table.num_rows
    pseud = table["pseudonym"].to_pylist() if "pseudonym" in cols else [None] * n
    sess = table["session_id"].to_pylist() if "session_id" in cols else [None] * n
    names = table["label"].to_pylist()
    conf = table["confidence"].to_pylist() if "confidence" in cols else [1.0] * n
    src = table["source"].to_pylist() if "source" in cols else [""] * n
    vfrom = _to_ns(table["valid_from"]) if "valid_from" in cols else np.full(n, _NULL_NS)
    vto = _to_ns(table["valid_to"]) if "valid_to" in cols else np.full(n, _NULL_NS)
    rows = []
    for i in range(n):
        if names[i] not in _LABEL_NAMES:
            raise ValueError(f"label must be 'legit' or 'cheat', got {names[i]!r}")
        if (pseud[i] is None) == (sess[i] is None):
            raise ValueError(f"labels row {i}: exactly one of pseudonym/session_id required")
        rows.append(
            {
                "pseudonym": None if pseud[i] is None else _to_bytes(pseud[i]),
                "session_id": None if sess[i] is None else _to_bytes(sess[i]),
                "label": _LABEL_NAMES[names[i]],
                "confidence": 1.0 if conf[i] is None else float(conf[i]),
                "source": src[i] or "",
                "valid_from": int(vfrom[i]),
                "valid_to": int(vto[i]),
            }
        )
    return rows


def join_labels(
    session_ids: np.ndarray,
    pseudonyms: np.ndarray,
    window_time_ns: np.ndarray,
    labels: list[dict],
    min_confidence: float = 0.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (label, confidence) per window; -1 / 0.0 where no label is valid."""
    n = len(session_ids)
    label = np.full(n, LABEL_UNLABELLED, dtype=np.int8)
    conf = np.zeros(n, dtype=np.float32)
    by_pseud: dict[bytes, list[int]] = {}
    by_sess: dict[bytes, list[int]] = {}
    for i in range(n):
        by_pseud.setdefault(pseudonyms[i], []).append(i)
        by_sess.setdefault(session_ids[i], []).append(i)
    # Ascending (confidence, label): later rows overwrite, so highest confidence / cheat wins.
    for row in sorted(labels, key=lambda r: (r["confidence"], r["label"])):
        if row["confidence"] < min_confidence:
            continue
        key = row["pseudonym"] if row["pseudonym"] is not None else row["session_id"]
        index = by_pseud if row["pseudonym"] is not None else by_sess
        idx = np.asarray(index.get(key, []), dtype=np.int64)
        if idx.size == 0:
            continue
        t = window_time_ns[idx]
        known = t != _NULL_NS
        ok = np.ones(idx.size, dtype=bool)
        bounded = row["valid_from"] != _NULL_NS or row["valid_to"] != _NULL_NS
        if bounded:
            ok &= known
            if row["valid_from"] != _NULL_NS:
                ok &= t >= row["valid_from"]
            if row["valid_to"] != _NULL_NS:
                ok &= t < row["valid_to"]
        sel = idx[ok]
        label[sel] = row["label"]
        conf[sel] = row["confidence"]
    return label, conf


def tokens_to_sequences(
    windows: pa.Table, tokens: pa.Table, seq_len: int
) -> tuple[np.ndarray, np.ndarray]:
    """Dense (n_windows, seq_len, N_SEQ_FEATURES) tensor in ``windows`` row order.

    Windows longer than ``seq_len`` keep their first ``seq_len`` frames; shorter ones are
    zero-padded and flagged by the trailing mask channel. Windows without tokens stay empty.
    Returns the tensor and a boolean "has tokens" vector.
    """
    n = windows.num_rows
    out = np.zeros((n, seq_len, N_SEQ_FEATURES), dtype=np.float32)
    has = np.zeros(n, dtype=bool)
    if tokens.num_rows == 0 or n == 0:
        return out, has
    key = {
        (bytes(s), int(g), int(w)): i
        for i, (s, g, w) in enumerate(
            zip(
                windows["session_id"].to_pylist(),
                windows["segment"].to_pylist(),
                windows["window_index"].to_pylist(),
                strict=True,
            )
        )
    }
    tokens = tokens.sort_by(
        [
            ("session_id", "ascending"),
            ("segment", "ascending"),
            ("window_index", "ascending"),
            ("pos", "ascending"),
        ]
    )
    sid = tokens["session_id"].combine_chunks()
    seg = tokens["segment"].to_numpy()
    wid = tokens["window_index"].to_numpy()
    pos = tokens["pos"].to_numpy()
    sid_codes = pc.dictionary_encode(sid).indices.to_numpy(zero_copy_only=False)
    change = np.ones(tokens.num_rows, dtype=bool)
    change[1:] = (sid_codes[1:] != sid_codes[:-1]) | (seg[1:] != seg[:-1]) | (wid[1:] != wid[:-1])
    starts = np.flatnonzero(change)
    ends = np.append(starts[1:], tokens.num_rows)
    start_sids = sid.take(pa.array(starts)).to_pylist()
    feats = np.stack(
        [
            apply_transform(tokens[name].to_numpy(zero_copy_only=False), kind)
            for name, kind in SEQ_FEATURES
        ],
        axis=1,
    )
    for s, e, sb in zip(starts, ends, start_sids, strict=True):
        row = key.get((bytes(sb), int(seg[s]), int(wid[s])))
        if row is None:
            continue
        # ``pos`` is contiguous from 0; keep it anyway so gaps cannot shift frames.
        p = pos[s:e].astype(np.int64)
        keep = p < seq_len
        out[row, p[keep], : N_SEQ_FEATURES - 1] = feats[s:e][keep]
        out[row, p[keep], N_SEQ_FEATURES - 1] = 1.0
        has[row] = True
    return out, has


def _aggregates(windows: pa.Table) -> np.ndarray:
    cols = []
    for name in AGG_COLUMNS:
        cols.append(windows[name].fill_null(0).to_numpy(zero_copy_only=False))
    flags = []
    for name in NULLABLE_AGG_COLUMNS:
        col = windows[name]
        cols.append(col.fill_null(0.0).to_numpy(zero_copy_only=False))
        flags.append(pc.is_valid(col).to_numpy(zero_copy_only=False).astype(np.float64))
    return np.stack([*cols, *flags], axis=1).astype(np.float32)


def build_eval_data(
    windows: pa.Table,
    tokens: pa.Table | None,
    sessions: pa.Table,
    labels: pa.Table | None,
    seq_len: int = 256,
    min_confidence: float = 0.0,
) -> EvalData:
    """Join the four tables. Windows whose session has no pseudonym are dropped (and logged)."""
    miss = {"session_id", "pseudonym"} - set(sessions.column_names)
    if miss:
        raise ValueError(f"sessions table lacks columns: {sorted(miss)}")
    s_ids = [_to_bytes(v) for v in sessions["session_id"].to_pylist()]
    s_pseud = [_to_bytes(v) if v is not None else None for v in sessions["pseudonym"].to_pylist()]
    pseud_of = {sid: p for sid, p in zip(s_ids, s_pseud, strict=True) if p is not None}
    if "started_at" in sessions.column_names:
        start_of = dict(zip(s_ids, _to_ns(sessions["started_at"]).tolist(), strict=True))
    else:
        start_of = {}

    w_sids = [bytes(v) for v in windows["session_id"].to_pylist()]
    known = np.array([s in pseud_of for s in w_sids], dtype=bool)
    if not known.all():
        log.warning("dropping %d windows of sessions without pseudonym", int((~known).sum()))
    keep_idx = np.flatnonzero(known)
    windows = windows.take(pa.array(keep_idx))
    w_sids = [w_sids[i] for i in keep_idx]

    seq = None
    if tokens is not None:
        seq, has = tokens_to_sequences(windows, tokens, seq_len)
        if not has.all():
            log.warning("%d windows have no tokens and are dropped", int((~has).sum()))
            windows = windows.take(pa.array(np.flatnonzero(has)))
            w_sids = [s for s, h in zip(w_sids, has, strict=True) if h]
            seq = seq[has]

    n = windows.num_rows
    sid_arr = np.empty(n, dtype=object)
    sid_arr[:] = w_sids
    pseud_arr = np.empty(n, dtype=object)
    pseud_arr[:] = [pseud_of[s] for s in w_sids]
    offsets = windows["start_offset_nanos"].to_numpy(zero_copy_only=False).astype(np.int64)
    base = np.array([start_of.get(s, _NULL_NS) for s in w_sids], dtype=np.int64)
    wtime = np.where(base == _NULL_NS, _NULL_NS, base + offsets)
    if labels is not None:
        label, conf = join_labels(sid_arr, pseud_arr, wtime, parse_labels(labels), min_confidence)
    else:
        label = np.full(n, LABEL_UNLABELLED, dtype=np.int8)
        conf = np.zeros(n, dtype=np.float32)
    ctx = np.empty(n, dtype=object)
    ctx[:] = windows["game_context"].to_pylist()
    return EvalData(
        session_id=sid_arr,
        segment=windows["segment"].to_numpy(zero_copy_only=False).astype(np.int64),
        window_index=windows["window_index"].to_numpy(zero_copy_only=False).astype(np.int64),
        game_context=ctx,
        pseudonym=pseud_arr,
        label=label,
        confidence=conf,
        agg=_aggregates(windows),
        seq=seq,
    )


def _read_parquet_dir(path: str | Path) -> pa.Table:
    return pads.dataset(str(path), format="parquet").to_table()


def load_dataset(
    windows: str | Path,
    tokens: str | Path | None,
    sessions: str | Path,
    labels: str | Path | None,
    seq_len: int = 256,
    min_confidence: float = 0.0,
) -> EvalData:
    """Load from files/directories (Parquet for windows/tokens; Parquet or CSV for the rest)."""
    return build_eval_data(
        _read_parquet_dir(windows),
        None if tokens is None else _read_parquet_dir(tokens),
        read_table(sessions),
        None if labels is None else read_table(labels),
        seq_len,
        min_confidence,
    )
