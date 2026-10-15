from pathlib import Path

import pyarrow as pa
import pyarrow.dataset as ds
import pytest

from corvus_ml.cgroup import default_workers
from corvus_ml.features import cli
from corvus_ml.features.config import FeatureConfig
from corvus_ml.features.pipeline import (
    TOKENS_DIR,
    WINDOWS_DIR,
    build,
    build_group,
    make_groups,
)
from corvus_ml.features.reader import EventReader, open_dataset
from corvus_ml.features.schema import WINDOW_TOKENS_SCHEMA, WINDOWS_SCHEMA
from corvus_ml.features.segments import SegmentMap
from lake_builders import (
    Frame,
    events_table,
    move,
    sid,
    swing,
    tick_end,
    write_events,
)


def segmap(*rows: tuple[bytes, int, str]) -> SegmentMap:
    return SegmentMap.from_table(
        pa.table(
            {
                "session_id": pa.array([r[0] for r in rows], pa.binary(16)),
                "segment": pa.array([r[1] for r in rows], pa.uint32()),
                "game_context": [r[2] for r in rows],
            }
        )
    )


def read(out: Path, name: str) -> pa.Table:
    return (
        ds.dataset(out / name, format="parquet")
        .to_table()
        .sort_by(
            [("session_id", "ascending"), ("segment", "ascending"), ("window_index", "ascending")]
        )
    )


def steady(n: int, segment: int = 0, start_seq: int = 0, t0: float = 0.0, step=10.0) -> list[Frame]:
    return [Frame(start_seq + i, t0 + i * step, segment=segment) for i in range(n)]


def test_output_schemas_match_declared_schemas(tmp_path):
    write_events(tmp_path / "e.parquet", events_table(sid(1), steady(8)))
    out = tmp_path / "out"
    build(tmp_path / "e.parquet", segmap((sid(1), 0, "pvp")), out, FeatureConfig(min_frames=1))
    assert read(out, WINDOWS_DIR).schema == WINDOWS_SCHEMA
    assert read(out, TOKENS_DIR).schema == WINDOW_TOKENS_SCHEMA


def test_windows_never_cross_segments_and_carry_their_context(tmp_path):
    frames = steady(20, segment=0) + steady(20, segment=1, start_seq=20, t0=1000)
    write_events(tmp_path / "e.parquet", events_table(sid(1), frames))
    out = tmp_path / "out"
    cfg = FeatureConfig(window=8, overlap=0.0, min_frames=1)
    build(tmp_path / "e.parquet", segmap((sid(1), 0, "lobby"), (sid(1), 1, "pvp")), out, cfg)

    windows, tokens = read(out, WINDOWS_DIR), read(out, TOKENS_DIR)
    rows = windows.to_pylist()
    assert {(r["segment"], r["game_context"]) for r in rows} == {(0, "lobby"), (1, "pvp")}
    for r in rows:
        assert (r["segment"] == 0) == (r["last_seq"] < 20)
    assert [r["window_index"] for r in rows if r["segment"] == 1] == [0, 1, 2]
    # tokens of a window all belong to the window's segment, positions are 0..n-1
    by_window = {}
    for t in tokens.to_pylist():
        by_window.setdefault((t["segment"], t["window_index"]), []).append(t)
    for r in rows:
        toks = by_window[(r["segment"], r["window_index"])]
        assert [t["pos"] for t in toks] == list(range(r["n_frames"]))
        assert all((t["seq"] < 20) == (r["segment"] == 0) for t in toks)


def test_frames_of_unmapped_segments_are_ignored(tmp_path):
    write_events(tmp_path / "e.parquet", events_table(sid(1), steady(10, segment=5)))
    out = tmp_path / "out"
    stats = build(tmp_path / "e.parquet", segmap((sid(1), 0, "pvp")), out, FeatureConfig())
    assert sum(s.windows for s in stats) == 0


def test_aggregates_match_hand_computed_example(tmp_path):
    frames = [
        swing(0, 0),
        swing(1, 100),
        swing(2, 200),
        move(3, 300, xyz=(0, 0, 0), yaw=0.0, pitch=0.0, ground=True),
        move(4, 400, xyz=(0, 0, 0), yaw=1.0, pitch=0.0, ground=True),
        move(5, 500, xyz=(0, 0, 0), yaw=4.0, pitch=0.0, ground=False),
        tick_end(6, 600),
        tick_end(7, 700),
        tick_end(8, 800),
        tick_end(9, 900),
    ]
    write_events(tmp_path / "e.parquet", events_table(sid(1), frames))
    out = tmp_path / "out"
    build(tmp_path / "e.parquet", segmap((sid(1), 0, "pvp")), out, FeatureConfig(min_frames=1))
    (w,) = read(out, WINDOWS_DIR).to_pylist()  # 10 frames over 0.9 s
    assert w["n_frames"] == 10
    assert w["cut"] == "end"
    assert w["cps"] == pytest.approx(3 / 0.9)
    assert w["attacks_per_s"] == 0.0
    assert w["packets_per_s"] == pytest.approx(10 / 0.9)
    assert w["movement_per_s"] == pytest.approx(3 / 0.9)
    assert w["rot_var"] == pytest.approx(1.0)  # dyaw = [1, 3] -> population variance 1
    assert w["rot_speed_mean"] == pytest.approx(20.0)  # [10, 30] deg/s
    assert w["rot_speed_max"] == pytest.approx(30.0)
    assert w["on_ground_frac"] == pytest.approx(2 / 3)
    assert w["tick_end_rate"] == pytest.approx(4 / 0.9)


def test_tick_end_rate_is_null_when_segment_never_sends_it(tmp_path):
    write_events(tmp_path / "e.parquet", events_table(sid(1), steady(10)))
    out = tmp_path / "out"
    build(tmp_path / "e.parquet", segmap((sid(1), 0, "pvp")), out, FeatureConfig(min_frames=1))
    (w,) = read(out, WINDOWS_DIR).to_pylist()
    assert w["tick_end_rate"] is None
    assert w["on_ground_frac"] is None


def lake(tmp_path: Path, n_sessions: int = 3) -> tuple[Path, SegmentMap]:
    tables = [
        events_table(
            sid(i),
            [
                *[
                    move(2 * k, k * 25.0, xyz=(k * 0.1, 0, 0), yaw=k * 3.0, pitch=0.0)
                    for k in range(40)
                ],
                *[swing(2 * k + 1, k * 25.0 + 5) for k in range(40)],
            ],
        )
        for i in range(1, n_sessions + 1)
    ]
    path = write_events(tmp_path / "lake" / "events.parquet", *tables)
    return path, segmap(*[(sid(i), 0, "pvp") for i in range(1, n_sessions + 1)])


def test_two_runs_produce_identical_bytes(tmp_path):
    path, seg = lake(tmp_path)
    cfg = FeatureConfig(window=16, min_frames=1)
    build(path, seg, tmp_path / "a", cfg, group_size=2)
    build(path, seg, tmp_path / "b", cfg, group_size=2)
    files = sorted(p.relative_to(tmp_path / "a") for p in (tmp_path / "a").rglob("*.parquet"))
    assert len(files) == 4  # 2 groups x 2 tables
    for f in files:
        assert (tmp_path / "a" / f).read_bytes() == (tmp_path / "b" / f).read_bytes()


def test_output_does_not_depend_on_worker_count(tmp_path):
    path, seg = lake(tmp_path)
    cfg = FeatureConfig(window=16, min_frames=1)
    build(path, seg, tmp_path / "one", cfg, workers=1, group_size=2)
    build(path, seg, tmp_path / "two", cfg, workers=2, group_size=2)
    for f in (tmp_path / "one").rglob("*.parquet"):
        assert f.read_bytes() == (tmp_path / "two" / f.relative_to(tmp_path / "one")).read_bytes()


def test_sessions_are_read_in_bounded_groups_and_batches(tmp_path):
    path, seg = lake(tmp_path, n_sessions=5)
    calls: list[int] = []
    batch_sizes: list[int] = []

    class SpyReader(EventReader):
        def iter_batches(self, session_ids):
            calls.append(len(session_ids))
            for b in super().iter_batches(session_ids):
                batch_sizes.append(b.num_rows)
                yield b

    cfg = FeatureConfig(window=16, min_frames=1, batch_rows=32)
    reader = SpyReader(open_dataset(path), cfg.batch_rows)
    for i, ids in enumerate(make_groups(seg.sessions(), 2)):
        build_group(reader, seg, cfg, i, ids, tmp_path / "out")
    assert calls == [2, 2, 1]  # never the whole dataset at once
    assert max(batch_sizes) <= 32
    assert len(batch_sizes) > 3


def test_cli_end_to_end_with_context_filter(tmp_path, capsys):
    path, _ = lake(tmp_path, n_sessions=2)
    csv = tmp_path / "segments.csv"
    csv.write_text(
        f"session_id,segment,game_context\n{sid(1).hex()},0,pvp\n{sid(2).hex()},0,lobby\n"
    )
    out = tmp_path / "out"
    code = cli.main(
        [
            "build",
            "--events",
            str(path),
            "--segments",
            str(csv),
            "--out",
            str(out),
            "--context",
            "pvp",
            "--window",
            "16",
            "--min-frames",
            "1",
            "--workers",
            "1",
        ]
    )
    assert code == 0
    assert "sessions=1" in capsys.readouterr().out
    assert {r["game_context"] for r in read(out, WINDOWS_DIR).to_pylist()} == {"pvp"}


def test_cli_derives_workers_from_cgroup_limit(tmp_path, monkeypatch):
    cpu_max = tmp_path / "cpu.max"
    cpu_max.write_text("200000 100000")
    monkeypatch.setattr(cli, "default_workers", lambda: default_workers(cpu_max, 16))
    seen = {}
    monkeypatch.setattr(cli, "build", lambda *a, **k: seen.setdefault("workers", a[4]) and [])
    csv = tmp_path / "s.csv"
    csv.write_text(f"session_id,segment,game_context\n{sid(1).hex()},0,pvp\n")
    cli.main(["build", "--events", str(tmp_path), "--segments", str(csv), "--out", str(tmp_path)])
    assert seen["workers"] == 2


def test_cli_without_selected_sessions_fails(tmp_path):
    csv = tmp_path / "s.csv"
    csv.write_text(f"session_id,segment,game_context\n{sid(1).hex()},0,lobby\n")
    argv = ["build", "--events", str(tmp_path), "--segments", str(csv), "--out", str(tmp_path)]
    assert cli.main([*argv, "--context", "pvp"]) == 1
