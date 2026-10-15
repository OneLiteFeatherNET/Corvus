"""Streaming windower: same inputs as the offline cut, bounded buffer, no payload bytes kept."""

import dataclasses

import numpy as np
import pytest

from corvus_ml.raw.packets import session_packet_table
from corvus_ml.raw.spec import PacketConfig, RawBytesSpec, WindowConfig
from corvus_ml.raw.streaming import RawFrame, SegmentWindower
from corvus_ml.raw.windows import build_windows, streams_from_table, to_input, window_bounds
from raw_builders import SMALL_LAYOUT, small_tokenizer, tiny_synthetic

NS = 1_000_000_000


@pytest.fixture(scope="module")
def syn():
    return tiny_synthetic(n_legit=2, n_cheat=2, seconds_per_session=30.0)


@pytest.fixture(scope="module")
def tok(syn):
    return small_tokenizer(syn)


def spec_of(tok, packet: PacketConfig, window: WindowConfig) -> RawBytesSpec:
    return RawBytesSpec("0" * 64, tok.vocab_size, tok.layout, packet, window)


def split_segments(sp, at: int):
    """Same frames, but the session switches to segment 1 after ``at`` frames."""
    seg = np.zeros(len(sp), dtype=np.uint32)
    seg[at:] = 1
    return dataclasses.replace(sp, segment=seg)


def offline(sp, tok, spec):
    """``{segment: [x of each window]}`` from the packet table (padded to the longest window)."""
    contexts = {int(s): "pvp" for s in set(sp.segment.tolist())}
    table = session_packet_table(sp, tok, spec.packet, contexts)
    out = {}
    for stream in streams_from_table(table, spec.packet, {sp.session_id: sp.protocol}):
        rw = build_windows([stream], spec.layout, spec.packet, spec.window)
        x = to_input(rw.ids, rw.time_s, spec.window.macro_windows)
        out[stream.segment] = [x[i] for i in range(len(x))]
    return out


def online(sp, tok, spec, chunks, **kw):
    """The windows of the streaming windower fed in batches of the given sizes."""
    result = {}
    for segment in sorted(set(sp.segment.tolist())):
        idx = np.flatnonzero(sp.segment == segment)
        w = SegmentWindower(spec, tok, sp.session_id, int(segment), "pvp", sp.protocol, **kw)
        frames = [
            RawFrame(
                int(sp.seq[i]),
                int(sp.offset_nanos[i]),
                int(sp.direction[i]),
                int(sp.packet_type[i]),
                sp.payloads[i],
                int(sp.payload_original_size[i]),
            )
            for i in idx
        ]
        wins, pos, c = [], 0, 0
        while pos < len(frames):
            n = chunks[c % len(chunks)]
            wins += w.feed(frames[pos : pos + n])
            pos, c = pos + n, c + 1
        wins += w.flush()
        result[int(segment)] = [m.x for m in wins]
    return result


def assert_same(off, on):
    assert off.keys() == on.keys()
    for seg in off:
        assert len(off[seg]) == len(on[seg]), f"window count differs in segment {seg}"
        for a, b in zip(off[seg], on[seg], strict=True):
            p = b.shape[-2]
            assert a.shape[-2] >= p
            assert np.array_equal(a[..., :p, :], b)
            assert not a[..., p:, :].any(), "offline padding must be zero"


PACKET = PacketConfig(max_tokens_per_packet=16)
WINDOWS = {
    "plain": WindowConfig(window_seconds=4.0, stride_seconds=2.0, max_packets=64, min_packets=4),
    "recent_cap": WindowConfig(
        window_seconds=4.0, stride_seconds=2.0, max_packets=20, min_packets=4
    ),
    "uniform_cap": WindowConfig(
        window_seconds=4.0, stride_seconds=3.0, max_packets=20, overflow="uniform", min_packets=4
    ),
    "macro": WindowConfig(
        window_seconds=4.0,
        stride_seconds=4.0,
        max_packets=64,
        min_packets=4,
        macro_window_seconds=12.0,
    ),
}


@pytest.mark.parametrize("window", WINDOWS.values(), ids=WINDOWS.keys())
@pytest.mark.parametrize("proto_mode", ["first", "every", "none"])
@pytest.mark.parametrize("chunks", [[1], [7, 3, 50], [100_000]], ids=["single", "mixed", "whole"])
def test_streamed_windows_equal_the_offline_cut(syn, tok, window, proto_mode, chunks):
    spec = spec_of(tok, dataclasses.replace(PACKET, proto_mode=proto_mode), window)
    for sp in syn.sessions[:2]:
        sp = split_segments(sp, len(sp) // 2)
        assert_same(offline(sp, tok, spec), online(sp, tok, spec, chunks))


def test_the_protocol_token_comes_from_the_session_protocol(syn, tok):
    sp = dataclasses.replace(syn.sessions[0], protocol=47)
    spec = spec_of(tok, PACKET, WINDOWS["plain"])

    first = online(sp, tok, spec, [10_000])[0][0]

    assert first[0, 3] == tok.layout.proto_id(47)


class Letters:
    """Stub encoder: one id per payload byte; no tokenizer training needed."""

    layout = SMALL_LAYOUT

    def encode_batch(self, payloads):
        return [[256 + b for b in p] for p in payloads]


@pytest.mark.parametrize("seed", range(150))
def test_window_grid_decisions_equal_window_bounds_on_random_streams(seed):
    rng = np.random.default_rng(seed)
    n = int(rng.integers(1, 60))
    gaps = rng.choice([0, 1, 1, 999_999_999, 1_000_000_000, 2_500_000_000], size=n)
    offsets = np.cumsum(gaps).astype(np.int64) + int(rng.integers(0, 5)) * NS
    cfg = WindowConfig(
        window_seconds=float(rng.choice([1.0, 2.0, 3.0])),
        stride_seconds=float(rng.choice([1.0, 2.0])),
        max_packets=int(rng.choice([3, 100])),
        min_packets=int(rng.choice([1, 2, 5])),
    )
    spec = RawBytesSpec("0" * 64, 1, SMALL_LAYOUT, PACKET, cfg)
    expected = [(b[3], b[2] - b[1]) for b in window_bounds(offsets, cfg)]

    w = SegmentWindower(spec, Letters(), b"s" * 16, 0, "pvp", 768)
    wins = []
    pos = 0
    while pos < n:
        step = int(rng.integers(1, 6))
        wins += w.feed(
            [RawFrame(i, int(offsets[i]), 1, 14, None, 0) for i in range(pos, min(n, pos + step))]
        )
        pos += step
    wins += w.flush()

    assert [(m.start_offset_nanos, m.n_packets) for m in wins] == expected


def frames_at(seconds, payload=b"\x01\x02\x03", first_seq=0):
    return [
        RawFrame(first_seq + i, int(t * NS), 1, 14, payload, len(payload))
        for i, t in enumerate(seconds)
    ]


def windower(window: WindowConfig, **kw) -> SegmentWindower:
    spec = RawBytesSpec("0" * 64, 1, SMALL_LAYOUT, PACKET, window)
    return SegmentWindower(spec, Letters(), b"s" * 16, 0, "pvp", 768, **kw)


def holds_bytes(obj, seen=None) -> bool:
    """Whether any bytes-like object is reachable from ``obj`` (containers, attributes)."""
    seen = set() if seen is None else seen
    if id(obj) in seen:
        return False
    seen.add(id(obj))
    if isinstance(obj, bytes | bytearray | memoryview):
        return True
    if isinstance(obj, dict):
        return any(holds_bytes(v, seen) or holds_bytes(k, seen) for k, v in obj.items())
    if isinstance(obj, list | tuple | set):
        return any(holds_bytes(v, seen) for v in obj)
    if hasattr(obj, "__dict__"):
        return holds_bytes(vars(obj), seen)
    return False


def test_payload_bytes_are_not_stored_after_they_were_tokenized():
    w = windower(WindowConfig(window_seconds=4.0, stride_seconds=2.0))

    w.feed(frames_at([0.0, 0.5, 1.0], payload=b"secret-payload"))

    assert w.buffered == 3
    own = {k: v for k, v in vars(w).items() if k not in ("_tok", "_spec", "_session_id")}
    assert not holds_bytes(own), "the windower must hold token ids only"


def test_frames_of_finished_windows_are_dropped_from_the_buffer():
    w = windower(WindowConfig(window_seconds=4.0, stride_seconds=2.0, min_packets=1))
    w.feed(frames_at(np.arange(0.0, 3.0, 0.1)))

    out = w.feed(frames_at(np.arange(3.0, 30.0, 0.1), first_seq=1000))

    assert out, "windows must have completed"
    # only frames from the start of the next undecided window (at most window + stride) are kept
    assert w.buffered <= int((4.0 + 2.0) / 0.1) + 1


def test_flush_frees_the_buffer_and_further_frames_are_ignored():
    w = windower(WindowConfig(window_seconds=4.0, stride_seconds=2.0))
    w.feed(frames_at(np.arange(0.0, 6.0, 0.1)))

    w.flush()

    assert w.buffered == 0
    assert w.feed(frames_at([9.0], first_seq=500)) == []
    assert w.buffered == 0


def test_the_buffer_cap_drops_the_oldest_frames_and_counts_them():
    w = windower(WindowConfig(window_seconds=60.0, stride_seconds=30.0), max_buffered=50)

    w.feed(frames_at(np.arange(0.0, 10.0, 0.05)))

    assert w.buffered == 50
    assert w.stats.dropped_overflow == 150


def test_redelivered_frames_are_ignored():
    w = windower(WindowConfig(window_seconds=4.0, stride_seconds=2.0))
    w.feed(frames_at([0.0, 0.1, 0.2]))

    w.feed(frames_at([0.0, 0.1, 0.2]))

    assert w.buffered == 3
    assert w.stats.duplicates == 3
