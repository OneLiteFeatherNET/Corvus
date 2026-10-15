import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pytest

from corvus_ml.features.config import FeatureConfig
from corvus_ml.features.reader import EventReader, dedup_sorted, open_dataset
from corvus_ml.features.schema import PACKET_CLIENT_TICK_END
from corvus_ml.features.segments import SegmentMap
from corvus_ml.features.tokens import extract_tokens, target_geometry, wrap_degrees
from corvus_ml.features.windows import window_bounds
from lake_builders import (  # noqa: I001
    Frame,
    attack,
    entity_teleport,
    events_table,
    move,
    sid,
    swing,
    write_events,
)

VOCAB = Path(__file__).parents[2] / "proto/corvus/capture/v1/vocabulary.json"


def frame_cols(frames: list[Frame]) -> dict[str, np.ndarray]:
    """Frame columns the way the pipeline hands them to extract_tokens."""
    nan = float("nan")
    return {
        "seq": np.array([f.seq for f in frames], dtype=np.uint64),
        "offset_nanos": np.array([int(f.t_ms * 1_000_000) for f in frames], dtype=np.uint64),
        "direction": np.array([f.direction for f in frames], dtype=np.uint8),
        "packet_type": np.array([f.packet_type for f in frames], dtype=np.uint32),
        "event_kind": np.array([f.event_kind for f in frames], dtype=np.uint8),
        "has_position": np.array([bool(f.has_position) for f in frames]),
        "has_rotation": np.array([bool(f.has_rotation) for f in frames]),
        "on_ground": np.array(
            [-1 if f.on_ground is None else int(f.on_ground) for f in frames], dtype=np.int8
        ),
        "x": np.array([nan if f.xyz is None else f.xyz[0] for f in frames]),
        "y": np.array([nan if f.xyz is None else f.xyz[1] for f in frames]),
        "z": np.array([nan if f.xyz is None else f.xyz[2] for f in frames]),
        "yaw": np.array([nan if f.yaw is None else f.yaw for f in frames]),
        "pitch": np.array([nan if f.pitch is None else f.pitch for f in frames]),
        "entity_id": np.array([-1 if f.entity_id is None else f.entity_id for f in frames]),
        "action": np.array([0 if f.action is None else f.action for f in frames], dtype=np.uint8),
    }


def tokens_of(frames: list[Frame], **cfg):
    return extract_tokens(frame_cols(frames), FeatureConfig(**cfg))


def test_vocabulary_id_of_client_tick_end_matches_contract():
    vocab = json.loads(VOCAB.read_text())
    ids = {e["name"]: e["id"] for e in vocab}
    assert ids["C_CLIENT_TICK_END"] == PACKET_CLIENT_TICK_END


def test_dedup_keeps_one_row_per_seq_in_order():
    s = sid(1)
    frames = [Frame(2, 20), Frame(0, 0), Frame(1, 10), Frame(1, 10), Frame(2, 20)]
    out = dedup_sorted(events_table(s, frames))
    assert out["seq"].to_pylist() == [0, 1, 2]


def test_reader_dedups_redelivered_rows_across_files(tmp_path):
    s = sid(1)
    write_events(tmp_path / "a.parquet", events_table(s, [Frame(0, 0), Frame(1, 10)]))
    write_events(tmp_path / "b.parquet", events_table(s, [Frame(1, 10), Frame(2, 20)]))
    reader = EventReader(open_dataset(tmp_path))
    assert reader.read_group([s])[s]["seq"].to_pylist() == [0, 1, 2]


def test_reader_selects_only_requested_sessions(tmp_path):
    write_events(
        tmp_path / "a.parquet",
        events_table(sid(1), [Frame(0, 0)]),
        events_table(sid(2), [Frame(0, 0)]),
    )
    got = EventReader(open_dataset(tmp_path)).read_group([sid(2)])
    assert list(got) == [sid(2)]


def test_reader_yields_bounded_batches(tmp_path):
    s = sid(1)
    write_events(tmp_path / "a.parquet", events_table(s, [Frame(i, i) for i in range(50)]))
    batches = list(EventReader(open_dataset(tmp_path), batch_rows=8).iter_batches([s]))
    assert len(batches) > 1
    assert max(b.num_rows for b in batches) <= 8


def test_wrap_degrees_crosses_the_seam():
    assert wrap_degrees(-179.0 - 179.0) == pytest.approx(2.0)
    assert wrap_degrees(179.0 - -179.0) == pytest.approx(-2.0)
    assert wrap_degrees(180.0) == -180.0


def test_dyaw_wraps_around_in_tokens():
    t = tokens_of([move(0, 0, yaw=179.0, pitch=0.0), move(1, 50, yaw=-179.0, pitch=0.0)])
    assert t.features["has_rot_delta"].tolist() == [0, 1]
    assert t.features["dyaw"][1] == pytest.approx(2.0)
    assert t.features["rot_speed"][1] == pytest.approx(2.0 / 0.05)


def test_position_delta_and_rotation_acceleration():
    t = tokens_of(
        [
            move(0, 0, xyz=(0, 64, 0), yaw=0.0, pitch=0.0),
            move(1, 100, xyz=(1, 64, 2), yaw=1.0, pitch=0.0),
            move(2, 200, xyz=(1, 63, 2), yaw=4.0, pitch=0.0),
        ]
    )
    f = t.features
    assert f["dx"][1] == pytest.approx(1.0)
    assert f["dz"][1] == pytest.approx(2.0)
    assert f["dy"][2] == pytest.approx(-1.0)
    assert f["rot_speed"][1:].tolist() == pytest.approx([10.0, 30.0])
    assert f["rot_accel"][2] == pytest.approx(200.0)
    assert f["has_pos_delta"].tolist() == [0, 1, 1]


def test_dt_bucket_uses_configured_edges():
    t = tokens_of([Frame(0, 0), Frame(1, 3), Frame(2, 53)], dt_edges_ms=(1.0, 10.0, 100.0))
    # first frame has no predecessor -> len(edges)+1, 3 ms -> bucket 1, 50 ms -> bucket 2
    assert t.features["dt_bucket"].tolist() == [4, 1, 2]


def test_attack_angle_and_distance_to_known_entity():
    frames = [
        entity_teleport(0, 0, 7, (0.0, 0.72, 3.0)),
        move(1, 10, xyz=(0.0, 0.0, 0.0), yaw=0.0, pitch=0.0),
        swing(2, 20),
        attack(3, 50, 7),
        move(4, 60, yaw=90.0, pitch=0.0),
        attack(5, 70, 7),
    ]
    f = tokens_of(frames).features
    # serverbound frames only: move, swing, attack, move, attack
    assert f["target_dist"][2] == pytest.approx(3.0)
    assert f["target_angle"][2] == pytest.approx(0.0, abs=1e-6)
    assert f["target_angle"][4] == pytest.approx(90.0, abs=1e-4)
    assert f["t_since_swing"][2] == pytest.approx(0.03)
    assert f["t_since_move"][2] == pytest.approx(0.04)


def test_attack_right_after_spawn_without_teleport_has_valid_geometry():
    frames = [
        entity_teleport(0, 0, 7, (0.0, 0.72, 3.0), action=4),  # KIND_SPAWN
        move(1, 10, xyz=(0.0, 0.0, 0.0), yaw=0.0, pitch=0.0),
        attack(2, 50, 7),
    ]
    f = tokens_of(frames).features
    assert f["target_dist"][1] == pytest.approx(3.0)
    assert f["target_angle"][1] == pytest.approx(0.0, abs=1e-6)


def test_attack_on_unknown_entity_has_sentinel_geometry():
    f = tokens_of([move(0, 0, xyz=(0, 0, 0), yaw=0.0, pitch=0.0), attack(1, 5, 99)]).features
    assert f["target_dist"][1] == -1.0
    assert f["target_angle"][1] == -1.0
    assert f["t_since_swing"][1] == 10.0  # never swung -> cap


def test_target_geometry_straight_ahead_has_zero_angle():
    dist, angle = target_geometry((0, 0, 0), 0.0, 0.0, (0.0, 0.72, 5.0))
    assert dist == pytest.approx(5.0)
    assert angle == pytest.approx(0.0, abs=1e-6)


def test_entity_cache_is_bounded_lru():
    frames = [entity_teleport(i, i, i, (float(i), 0.0, 0.0)) for i in range(5)]
    frames += [move(5, 10, xyz=(0, 0, 0), yaw=0.0, pitch=0.0), attack(6, 11, 0), attack(7, 12, 4)]
    f = tokens_of(frames, entity_cache=2).features
    assert f["target_dist"][1] == -1.0  # entity 0 was evicted
    assert f["target_dist"][2] > 0  # entity 4 is known


def ms_offsets(values_ms) -> np.ndarray:
    return (np.asarray(values_ms, dtype=np.float64) * 1e6).astype(np.uint64)


def test_stride_and_overlap_math():
    cfg = FeatureConfig(window=4, overlap=0.5, min_frames=1)
    bounds = window_bounds(ms_offsets(range(10)), cfg)
    assert [(w.lo, w.hi) for w in bounds] == [(0, 4), (2, 6), (4, 8), (6, 10)]


def test_zero_overlap_tiles_without_gaps():
    cfg = FeatureConfig(window=4, overlap=0.0, min_frames=1)
    bounds = window_bounds(ms_offsets(range(10)), cfg)
    assert [(w.lo, w.hi, w.cut) for w in bounds] == [(0, 4, "full"), (4, 8, "full"), (8, 10, "end")]


def test_short_tail_below_min_frames_is_dropped():
    cfg = FeatureConfig(window=4, overlap=0.0, min_frames=3)
    bounds = window_bounds(ms_offsets(range(10)), cfg)
    assert [(w.lo, w.hi) for w in bounds] == [(0, 4), (4, 8)]


def test_max_duration_cuts_window_before_n_frames():
    cfg = FeatureConfig(window=100, overlap=0.5, max_duration_s=30.0, min_frames=1)
    offsets = ms_offsets([i * 10_000 for i in range(8)])  # every 10 s
    bounds = window_bounds(offsets, cfg)
    assert (bounds[0].lo, bounds[0].hi, bounds[0].cut) == (0, 4, "duration")  # 0..30 s inclusive
    assert bounds[1].lo == 2


def test_config_rejects_bad_overlap():
    with pytest.raises(ValueError, match="overlap"):
        FeatureConfig(overlap=1.0)


def test_segment_map_selects_context_and_sessions():
    table = pa.table(
        {
            "session_id": pa.array([sid(1), sid(1), sid(2)], pa.binary(16)),
            "segment": pa.array([0, 1, 0], pa.uint32()),
            "game_context": ["lobby", "pvp", "lobby"],
        }
    )
    m = SegmentMap.from_table(table)
    assert m.select("pvp").by_session == {sid(1): {1: "pvp"}}
    assert m.select(session_ids=[sid(2)]).sessions() == [sid(2)]


def test_segment_map_reads_hex_ids_from_csv(tmp_path):
    csv = tmp_path / "segments.csv"
    csv.write_text(f"session_id,segment,game_context\n{sid(3).hex()},0,pvp\n")
    assert SegmentMap.load(csv).by_session == {sid(3): {0: "pvp"}}
