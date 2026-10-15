"""Behaviour of the incremental featurizer core (no Arrow, no files)."""

import random
import subprocess
import sys

import numpy as np
import pytest

from corvus_ml.features.config import FeaturizerConfig
from corvus_ml.features.constants import AGGREGATE_NAMES, TOKEN_NAMES
from corvus_ml.features.featurizer import SessionFeaturizer
from corvus_ml.features.frames import Frame
from corvus_ml.features.tokens import TokenExtractor
from corvus_ml.features.windows import window_aggregates

SID = bytes([1]) * 16
MS = 1_000_000
TICK_END = 27


def frame(seq, t_ms, segment=0, **kw) -> Frame:
    defaults = {"direction": 1, "packet_type": 14, "event_kind": 0}
    return Frame(segment=segment, seq=seq, offset_nanos=int(t_ms * MS), **(defaults | kw))


def random_run(rng: random.Random, n: int, segment: int = 0, start_seq: int = 0) -> list[Frame]:
    frames, t, yaw = [], 0.0, 0.0
    for i in range(n):
        t += rng.choice((1.0, 5.0, 20.0, 50.0, 400.0, 3000.0))
        kind = rng.choice((1, 1, 2, 3, 4, 0))
        yaw += rng.uniform(-30, 30)
        frames.append(
            frame(
                start_seq + i,
                t,
                segment,
                packet_type=TICK_END if kind == 0 and rng.random() < 0.5 else 14,
                event_kind=kind,
                has_position=kind == 1,
                has_rotation=kind == 1,
                on_ground=rng.choice((True, False, None)) if kind == 1 else None,
                x=rng.uniform(0, 5),
                y=64.0,
                z=rng.uniform(0, 5),
                yaw=((yaw + 180) % 360) - 180,
                pitch=rng.uniform(-40, 40),
                action=2 if kind == 2 else 0,
                entity_id=-1,
            )
        )
    return frames


def batch_windows(frames: list[Frame], cfg: FeaturizerConfig, tick_end: bool):
    """Reference: tokens of the whole run first, then the window rule applied to all offsets."""
    ext = TokenExtractor(cfg)
    rows, offsets = [], []
    for fr in frames:
        row = np.zeros(len(TOKEN_NAMES), dtype=np.float32)
        if ext.step(fr, row):
            rows.append(row)
            offsets.append(fr.offset_nanos)
    matrix, offs = np.array(rows).reshape(-1, len(TOKEN_NAMES)), np.array(offsets, dtype=np.uint64)
    out, n, lo = [], len(offs), 0
    max_ns = int(cfg.max_duration_s * 1e9)
    while lo < n:
        limit = lo + cfg.window
        hi_dur = int(np.searchsorted(offs, int(offs[lo]) + max_ns, side="right"))
        hi = min(limit, hi_dur, n)
        cut = "duration" if hi_dur < min(limit, n) else "full" if limit <= n else "end"
        size = hi - lo
        if size >= cfg.min_frames:
            agg = window_aggregates(matrix[lo:hi], int(offs[lo]), int(offs[hi - 1]), tick_end)
            out.append((lo, hi, cut, matrix[lo:hi], agg))
        if hi >= n:
            break
        lo += max(1, size - int(size * cfg.overlap))
    return out


def stream(frames: list[Frame], cfg: FeaturizerConfig, tick_end: bool | None = None):
    fz = SessionFeaturizer(SID, cfg)
    fz.begin_segment(frames[0].segment, tick_end=tick_end)
    out = []
    for fr in frames:
        out.extend(fz.feed(fr))
    out.extend(fz.flush())
    return out


@pytest.mark.parametrize("seed", range(12))
def test_one_by_one_streaming_equals_batch_windowing(seed):
    rng = random.Random(seed)
    cfg = FeaturizerConfig(
        window=rng.choice((4, 16, 33)),
        overlap=rng.choice((0.0, 0.25, 0.5, 0.9)),
        max_duration_s=rng.choice((0.2, 2.0, 30.0)),
        min_frames=rng.choice((1, 3, 8)),
    )
    frames = random_run(rng, rng.choice((5, 90, 400)))
    expected = batch_windows(frames, cfg, tick_end=True)
    got = stream(frames, cfg, tick_end=True)
    assert len(got) == len(expected), f"window count differs for {cfg}"
    seqs = [fr.seq for fr in frames if fr.direction == 1]
    for w, (lo, hi, cut, matrix, agg) in zip(got, expected, strict=True):
        assert (w.first_seq, w.last_seq, w.cut) == (seqs[lo], seqs[hi - 1], cut)
        assert w.n_frames == hi - lo
        np.testing.assert_array_equal(w.tokens, matrix)
        np.testing.assert_array_equal(w.aggregates, agg)  # NaN positions included


def test_windows_are_emitted_as_soon_as_their_extent_is_known():
    cfg = FeaturizerConfig(window=4, overlap=0.5, min_frames=1)
    fz = SessionFeaturizer(SID, cfg)
    emitted = [len(fz.feed(frame(i, i))) for i in range(6)]
    assert emitted == [0, 0, 0, 1, 0, 1], "a full window is complete with its last frame"


def test_a_frame_beyond_max_duration_completes_the_window():
    cfg = FeaturizerConfig(window=100, max_duration_s=1.0, min_frames=1)
    fz = SessionFeaturizer(SID, cfg)
    assert fz.feed(frame(0, 0)) == []
    assert fz.feed(frame(1, 500)) == []
    (w,) = fz.feed(frame(2, 1500))
    assert (w.cut, w.n_frames) == ("duration", 2)


def test_flush_emits_the_tail_window_and_resets():
    cfg = FeaturizerConfig(window=8, overlap=0.0, min_frames=2)
    fz = SessionFeaturizer(SID, cfg)
    for i in range(10):
        fz.feed(frame(i, i))
    (tail,) = fz.flush()
    assert (tail.cut, tail.n_frames, tail.window_index) == ("end", 2, 1)
    assert fz.flush() == [], "a second flush has nothing left"


def test_flush_drops_a_tail_below_min_frames():
    cfg = FeaturizerConfig(window=8, overlap=0.0, min_frames=3)
    fz = SessionFeaturizer(SID, cfg)
    emitted = [w for i in range(10) for w in fz.feed(frame(i, i))]
    assert len(emitted) == 1
    assert fz.flush() == [], "2 tail frames < min_frames"


def test_flush_does_not_repeat_a_window_that_already_reached_the_end():
    cfg = FeaturizerConfig(window=4, overlap=0.5, min_frames=1)
    fz = SessionFeaturizer(SID, cfg)
    emitted = [w for i in range(8) for w in fz.feed(frame(i, i))]
    assert [(w.first_seq, w.last_seq) for w in emitted] == [(0, 3), (2, 5), (4, 7)]
    assert fz.flush() == []


def test_segment_boundary_resets_per_run_state_and_windows_do_not_cross_it():
    cfg = FeaturizerConfig(window=4, overlap=0.0, min_frames=1)
    fz = SessionFeaturizer(SID, cfg)
    out = []
    for i in range(6):
        out += fz.feed(
            frame(i, i * 10, 0, event_kind=1, has_position=True, x=float(i), y=0.0, z=0.0)
        )
    for i in range(6, 10):
        out += fz.feed(
            frame(i, i * 10, 1, event_kind=1, has_position=True, x=100.0 + i, y=0.0, z=0.0)
        )
    out += fz.flush()
    by_segment = {s: [w for w in out if w.segment == s] for s in (0, 1)}
    assert [w.n_frames for w in by_segment[0]] == [4, 2]
    assert [w.n_frames for w in by_segment[1]] == [4]
    first = by_segment[1][0].tokens
    col = {n: first[:, i] for i, n in enumerate(TOKEN_NAMES)}
    assert col["has_pos_delta"][0] == 0, "no position carried over from segment 0"
    assert col["dx"][0] == 0.0
    assert col["dt_bucket"][0] == len(cfg.dt_edges_ms) + 1, "first frame of a segment"
    assert col["log_dt"][0] == 0.0
    assert col["has_pos_delta"][1] == 1


def test_window_index_continues_when_a_segment_is_entered_again():
    cfg = FeaturizerConfig(window=2, overlap=0.0, min_frames=1)
    fz = SessionFeaturizer(SID, cfg)
    out = []
    for seq, segment in enumerate((0, 0, 1, 1, 0, 0)):
        out += fz.feed(frame(seq, seq, segment))
    out += fz.flush()
    assert [(w.segment, w.window_index) for w in out] == [(0, 0), (1, 0), (0, 1)]


def test_redelivered_frames_are_ignored():
    cfg = FeaturizerConfig(window=3, overlap=0.0, min_frames=1)
    fz = SessionFeaturizer(SID, cfg)
    frames = [frame(i, i) for i in range(3)]
    out = [w for fr in (frames[0], frames[1], frames[1], frames[0], frames[2]) for w in fz.feed(fr)]
    assert [w.n_frames for w in out] == [3]


def test_clientbound_frames_only_feed_entity_state():
    cfg = FeaturizerConfig(window=2, overlap=0.0, min_frames=1)
    fz = SessionFeaturizer(SID, cfg)
    out = []
    out += fz.feed(frame(0, 0, direction=2, event_kind=7, entity_id=5, action=4, x=1.0, y=2, z=3))
    out += fz.feed(frame(1, 1))
    out += fz.feed(frame(2, 2))
    (w,) = out
    assert (w.n_frames, w.first_seq, w.last_seq) == (2, 1, 2)


def test_tick_end_rate_follows_the_hint_or_what_was_seen():
    cfg = FeaturizerConfig(window=3, overlap=0.0, min_frames=1)
    plain = [frame(i, i * 100) for i in range(3)]
    ticking = [frame(i, i * 100, packet_type=TICK_END) for i in range(3)]
    name = AGGREGATE_NAMES.index("tick_end_rate")
    assert np.isnan(stream(plain, cfg)[0].aggregates[name])
    assert stream(ticking, cfg)[0].aggregates[name] == pytest.approx(3 / 0.2)
    assert stream(plain, cfg, tick_end=True)[0].aggregates[name] == 0.0
    assert np.isnan(stream(ticking, cfg, tick_end=False)[0].aggregates[name])


def test_buffer_stays_bounded_on_a_long_run():
    cfg = FeaturizerConfig(window=16, overlap=0.5, min_frames=1)
    fz = SessionFeaturizer(SID, cfg)
    emitted = 0
    for i in range(20_000):
        emitted += len(fz.feed(frame(i, i)))
    assert emitted == (20_000 - 16) // 8 + 1
    assert fz._run is not None
    assert len(fz._run._seq) <= 4096, "consumed frames are released"


def test_core_modules_import_without_the_heavy_stacks():
    code = (
        "import sys\n"
        "import corvus_ml.features\n"
        "import corvus_ml.features.featurizer, corvus_ml.features.config\n"
        "bad = {'torch', 'sklearn', 'onnx', 'skl2onnx', 'pyarrow'} & set(sys.modules)\n"
        "assert not bad, sorted(bad)\n"
    )
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
