import datetime as dt

import numpy as np
import pyarrow as pa
import pyarrow.csv as pacsv
import pytest

from corvus_ml.eval.data import LABEL_CHEAT, LABEL_LEGIT, LABEL_UNLABELLED, N_SEQ_FEATURES
from corvus_ml.eval.dataset import build_eval_data, load_dataset, parse_labels
from corvus_ml.eval.synthetic import SyntheticConfig, SyntheticTables, generate
from corvus_ml.features.schema import WINDOW_TOKENS_SCHEMA, WINDOWS_SCHEMA

UTC = dt.UTC
T0 = dt.datetime(2026, 3, 1, tzinfo=UTC)


def small(**kw) -> SyntheticTables:
    base = {"n_legit": 4, "n_cheat": 2, "windows_per_session": 2, "frames_per_window": 20}
    return generate(SyntheticConfig(seed=1, **(base | kw)))


def labels_table(rows):
    cols = ("pseudonym", "session_id", "label", "source", "confidence", "valid_from", "valid_to")
    data = {c: [r.get(c) for r in rows] for c in cols}
    return pa.table(
        {
            "pseudonym": pa.array(data["pseudonym"], pa.binary(32)),
            "session_id": pa.array(data["session_id"], pa.binary(16)),
            "label": data["label"],
            "source": [r.get("source", "test") for r in rows],
            "confidence": pa.array([r.get("confidence", 1.0) for r in rows], pa.float32()),
            "valid_from": pa.array(data["valid_from"], pa.timestamp("us", tz="UTC")),
            "valid_to": pa.array(data["valid_to"], pa.timestamp("us", tz="UTC")),
        }
    )


def test_synthetic_tables_follow_the_real_schemas():
    t = small()
    assert t.windows.schema == WINDOWS_SCHEMA
    assert t.tokens.schema == WINDOW_TOKENS_SCHEMA
    assert t.tokens.num_rows == t.windows.num_rows * 20


def test_synthetic_generation_is_deterministic():
    assert small().tokens.equals(small().tokens)
    assert not small().tokens.equals(generate(SyntheticConfig(seed=2)).tokens)


def test_loader_builds_aligned_aggregate_and_sequence_arrays():
    t = small()
    d = build_eval_data(t.windows, t.tokens, t.sessions, t.labels, seq_len=16)
    assert len(d) == t.windows.num_rows
    assert d.seq.shape == (len(d), 16, N_SEQ_FEATURES)
    assert (d.seq[..., -1] == 1.0).all(), "all 20-frame windows fill 16 slots"
    assert set(d.label.tolist()) == {LABEL_LEGIT, LABEL_CHEAT}


def test_loader_pads_short_windows_and_marks_them_in_the_mask_channel():
    t = small()
    d = build_eval_data(t.windows, t.tokens, t.sessions, t.labels, seq_len=32)
    assert (d.seq[..., -1].sum(axis=1) == 20).all()
    assert (d.seq[:, 20:, :] == 0).all()


def test_windows_of_players_without_label_stay_unlabelled():
    t = small(n_unlabelled=2)
    d = build_eval_data(t.windows, None, t.sessions, t.labels)
    assert (d.label == LABEL_UNLABELLED).any()
    assert d.seq is None


def test_label_validity_window_limits_which_windows_get_the_label():
    t = small(n_legit=1, n_cheat=0, windows_per_session=3)
    pseud = t.sessions["pseudonym"][0].as_py()
    started = t.sessions["started_at"].to_pylist()
    # Window start = session start + offset; offsets are < 1 s apart for the first windows.
    first_session_start = min(started)
    rows = [
        {
            "pseudonym": pseud,
            "label": "cheat",
            "valid_from": first_session_start + dt.timedelta(hours=1),
        },
        {"pseudonym": pseud, "label": "legit", "confidence": 0.5},
    ]
    d = build_eval_data(t.windows, None, t.sessions, labels_table(rows))
    cheat = d.label == LABEL_CHEAT
    assert cheat.any()
    assert (d.label == LABEL_LEGIT).any()
    # The cheat label starts 1 h after the first session, i.e. only covers the later one.
    assert (
        d.label[d.session_id == t.sessions["session_id"][0].as_py()].tolist() == [LABEL_LEGIT] * 3
    )


def test_highest_confidence_wins_and_cheat_wins_ties():
    t = small(n_legit=1, n_cheat=0)
    pseud = t.sessions["pseudonym"][0].as_py()
    tie = labels_table(
        [{"pseudonym": pseud, "label": "legit"}, {"pseudonym": pseud, "label": "cheat"}]
    )
    assert (build_eval_data(t.windows, None, t.sessions, tie).label == LABEL_CHEAT).all()
    sure = labels_table(
        [
            {"pseudonym": pseud, "label": "legit", "confidence": 0.9},
            {"pseudonym": pseud, "label": "cheat", "confidence": 0.4},
        ]
    )
    assert (build_eval_data(t.windows, None, t.sessions, sure).label == LABEL_LEGIT).all()


def test_min_confidence_drops_weak_labels():
    t = small(n_legit=1, n_cheat=0)
    pseud = t.sessions["pseudonym"][0].as_py()
    weak = labels_table([{"pseudonym": pseud, "label": "cheat", "confidence": 0.3}])
    d = build_eval_data(t.windows, None, t.sessions, weak, min_confidence=0.5)
    assert (d.label == LABEL_UNLABELLED).all()


def test_session_scoped_label_only_hits_that_session():
    t = small(n_legit=1, n_cheat=0)
    sid = t.sessions["session_id"][0].as_py()
    d = build_eval_data(
        t.windows, None, t.sessions, labels_table([{"session_id": sid, "label": "cheat"}])
    )
    assert (d.label[d.session_id == sid] == LABEL_CHEAT).all()
    assert (d.label[d.session_id != sid] == LABEL_UNLABELLED).all()


def test_label_rows_need_exactly_one_key_and_a_known_label():
    both = labels_table([{"pseudonym": b"p" * 32, "session_id": b"s" * 16, "label": "cheat"}])
    with pytest.raises(ValueError, match="exactly one"):
        parse_labels(both)
    with pytest.raises(ValueError, match="legit"):
        parse_labels(labels_table([{"pseudonym": b"p" * 32, "label": "maybe"}]))


def test_windows_of_sessions_without_pseudonym_are_dropped():
    t = small()
    sessions = t.sessions.slice(1)  # first session unknown
    d = build_eval_data(t.windows, t.tokens, sessions, t.labels, seq_len=16)
    assert len(d) == t.windows.num_rows - 2


def test_csv_sessions_and_labels_with_hex_ids_and_iso_times_load(tmp_path):
    t = small(n_legit=2, n_cheat=1)
    paths = t.write(tmp_path / "pq")
    sessions = pa.table(
        {
            "session_id": [s.hex() for s in t.sessions["session_id"].to_pylist()],
            "pseudonym": [p.hex() for p in t.sessions["pseudonym"].to_pylist()],
            "started_at": [s.isoformat() for s in t.sessions["started_at"].to_pylist()],
        }
    )
    pacsv.write_csv(sessions, tmp_path / "sessions.csv")
    labels = pa.table(
        {
            "pseudonym": [p.hex() for p in t.labels["pseudonym"].to_pylist()],
            "label": t.labels["label"],
            "source": ["csv"] * t.labels.num_rows,
            "confidence": [1.0] * t.labels.num_rows,
            "valid_from": pa.array([None] * t.labels.num_rows, pa.string()),
            "valid_to": ["2099-01-01T00:00:00+00:00"] * t.labels.num_rows,
        }
    )
    pacsv.write_csv(labels, tmp_path / "labels.csv")
    d = load_dataset(
        paths["windows"], paths["tokens"], tmp_path / "sessions.csv", tmp_path / "labels.csv", 16
    )
    ref = build_eval_data(t.windows, t.tokens, t.sessions, t.labels, 16)
    assert len(d) == len(ref)
    assert sorted(d.label.tolist()) == sorted(ref.label.tolist())
    assert np.allclose(np.sort(d.agg[:, 0]), np.sort(ref.agg[:, 0]))
