"""Time windows: grid, stride, segment boundaries, caps, macro windows, model input layout."""

import numpy as np
import pytest

from corvus_ml.raw import layout as L
from corvus_ml.raw.spec import PacketConfig, WindowConfig
from corvus_ml.raw.windows import (
    PacketStream,
    build_windows,
    from_input,
    pick_packets,
    stream_windows,
    to_input,
    window_bounds,
)

LAYOUT = L.TokenLayout(packet_type_max=64)
PACKET = PacketConfig(max_tokens_per_packet=16)
NS = 1_000_000_000


def stream(times_s, segment=0, packet_type=12, protocol=768, session=b"s" * 16):
    n = len(times_s)
    cap = PACKET.max_tokens_per_packet - L.HEADER_LEN - 2
    return PacketStream(
        session_id=session,
        segment=segment,
        game_context="pvp",
        protocol=protocol,
        offset_ns=(np.asarray(times_s) * NS).astype(np.int64),
        direction=np.ones(n, dtype=np.uint8),
        packet_type=np.full(n, packet_type, dtype=np.uint32),
        dt_id=np.full(n, LAYOUT.dt_base, dtype=np.int16),
        trunc=np.zeros(n, dtype=bool),
        nopayload=np.ones(n, dtype=bool),
        payload=np.zeros((n, cap), dtype=np.int16),
        payload_len=np.zeros(n, dtype=np.int64),
    )


def cfg(**kw):
    return WindowConfig(**({"window_seconds": 10.0, "stride_seconds": 5.0, "min_packets": 1} | kw))


def starts(bounds):
    return [b[3] / NS for b in bounds]


def test_windows_follow_the_stride_grid_anchored_at_the_first_packet():
    times = np.arange(100.0, 130.0, 0.1)

    bounds = window_bounds((times * NS).astype(np.int64), cfg())

    assert starts(bounds) == [100.0, 105.0, 110.0, 115.0, 120.0]
    assert [b[0] for b in bounds] == [0, 1, 2, 3, 4]


def test_consecutive_windows_overlap_by_window_minus_stride():
    times = np.arange(0.0, 30.0, 0.1)

    (_, lo0, hi0, _), (_, lo1, hi1, _) = window_bounds((times * NS).astype(np.int64), cfg())[:2]

    assert (hi0 - lo0, hi1 - lo1) == (100, 100)
    assert lo1 - lo0 == 50, "stride 5 s = 50 packets"


def test_stride_equal_to_the_window_gives_disjoint_windows():
    times = np.arange(0.0, 30.0, 0.1)

    bounds = window_bounds((times * NS).astype(np.int64), cfg(stride_seconds=10.0))

    assert [(lo, hi) for _, lo, hi, _ in bounds] == [(0, 100), (100, 200), (200, 300)]


def test_a_tail_window_is_kept_only_if_it_holds_packets_no_other_window_saw():
    # packets up to 12 s: windows [0,10) and [5,15) (partial, sees 10..12); [10,20) adds nothing
    times = np.arange(0.0, 12.0, 0.5)

    bounds = window_bounds((times * NS).astype(np.int64), cfg())

    assert starts(bounds) == [0.0, 5.0]


def test_a_segment_shorter_than_one_window_still_yields_one_window():
    bounds = window_bounds((np.arange(0.0, 4.0, 0.5) * NS).astype(np.int64), cfg())

    assert len(bounds) == 1


def test_sparse_windows_below_min_packets_are_dropped():
    times = np.concatenate([np.arange(0.0, 10.0, 0.5), [40.0, 41.0]])

    kept = window_bounds((times * NS).astype(np.int64), cfg(stride_seconds=10.0, min_packets=5))

    assert starts(kept) == [0.0]


def test_empty_input_has_no_windows():
    assert window_bounds(np.zeros(0, dtype=np.int64), cfg()) == []


def test_windows_never_cross_a_segment():
    a = stream(np.arange(0.0, 20.0, 0.2), segment=0, packet_type=11)
    b = stream(np.arange(20.0, 40.0, 0.2), segment=1, packet_type=13)

    rw = build_windows([a, b], LAYOUT, PACKET, cfg())

    pkt_a, pkt_b = LAYOUT.pkt_id(11), LAYOUT.pkt_id(13)
    for i in range(len(rw)):
        types = set(rw.ids[i, 0, :, 1][rw.ids[i, 0, :, 0] != 0].tolist())
        assert types in ({pkt_a}, {pkt_b}), "a window holds packets of one segment only"
        assert rw.segment[i] in (0, 1)
    assert set(rw.segment.tolist()) == {0, 1}


def test_time_channel_is_seconds_since_the_window_start():
    s = stream(np.arange(50.0, 62.0, 0.5))

    rw = build_windows([s], LAYOUT, PACKET, cfg())

    first = rw.time_s[0, 0]
    assert first[0] == 0.0
    assert first[1] == pytest.approx(0.5)
    assert rw.time_s[1, 0, 0] == pytest.approx(0.0), (
        "second window starts at 55 s, its packet at 55"
    )
    assert rw.start_offset_nanos.tolist()[:2] == [50 * NS, 55 * NS]


def test_packet_cap_keeps_the_most_recent_packets():
    s = stream(np.arange(0.0, 10.0, 0.05))  # 200 packets in one window

    rw = build_windows([s], LAYOUT, PACKET, cfg(max_packets=50, stride_seconds=10.0))

    assert rw.ids.shape[2] == 50
    assert rw.time_s[0, 0, 0] == pytest.approx(7.5), "the newest 50 of 200 start at 150 * 0.05"
    assert rw.time_s[0, 0, -1] == pytest.approx(9.95)


def test_packet_cap_uniform_subsamples_across_the_whole_window():
    picked = pick_packets(100, 300, 5, "uniform")

    assert len(picked) == 5
    assert picked[0] == 100
    assert picked[-1] == 299
    assert np.all(np.diff(picked) > 0)


def test_windows_below_the_cap_keep_all_packets():
    assert pick_packets(10, 20, 50, "recent").tolist() == list(range(10, 20))


def test_token_cap_bounds_every_packet_row():
    s = stream(np.arange(0.0, 10.0, 0.5))

    rw = build_windows([s], LAYOUT, PACKET, cfg())

    assert rw.ids.shape[-1] == PACKET.max_tokens_per_packet


def test_protocol_token_is_on_the_first_packet_of_each_window_by_default():
    s = stream(np.arange(0.0, 12.0, 0.5), protocol=768)

    rw = build_windows([s], LAYOUT, PACKET, cfg())

    for i in range(len(rw)):
        col = rw.ids[i, 0, :, 3]
        assert col[0] == LAYOUT.proto_id(768)
        assert LAYOUT.proto_id(768) not in col[1:][rw.ids[i, 0, 1:, 0] != 0]


def test_protocol_token_modes_every_and_none():
    s = stream(np.arange(0.0, 6.0, 0.5), protocol=770)

    every = build_windows(
        [s], LAYOUT, PacketConfig(max_tokens_per_packet=16, proto_mode="every"), cfg()
    )
    none = build_windows(
        [s], LAYOUT, PacketConfig(max_tokens_per_packet=16, proto_mode="none"), cfg()
    )

    real = every.ids[0, 0, :, 0] != 0
    assert (every.ids[0, 0, real, 3] == LAYOUT.proto_id(770)).all()
    assert (none.ids[0, 0, :, 3] != LAYOUT.proto_id(770)).all()


def test_unknown_protocol_gets_the_other_token():
    s = stream(np.arange(0.0, 6.0, 0.5), protocol=12345)

    rw = build_windows([s], LAYOUT, PACKET, cfg())

    assert rw.ids[0, 0, 0, 3] == L.PROTO_OTHER_ID


def test_macro_windows_group_consecutive_windows_and_pad_gaps():
    # 10 s windows without overlap; nothing between 10 and 20 s -> the middle window is missing
    times = np.concatenate([np.arange(0.0, 10.0, 0.5), np.arange(20.0, 40.0, 0.5)])
    macro = cfg(stride_seconds=10.0, macro_window_seconds=30.0)

    rw = build_windows([stream(times)], LAYOUT, PACKET, macro)

    assert macro.macro_windows == 3
    assert len(rw) == 2, "windows 0..2 and 3 form two macro windows"
    first_real = (rw.ids[0, :, :, 0] != 0).any(axis=1)
    assert first_real.tolist() == [True, False, True], "the gap stays padding"
    assert rw.ids.shape[1] == 3


def test_macro_windows_need_stride_equal_to_window():
    with pytest.raises(ValueError, match="stride"):
        WindowConfig(window_seconds=10.0, stride_seconds=5.0, macro_window_seconds=60.0)
    with pytest.raises(ValueError, match="macro_window_seconds"):
        WindowConfig(window_seconds=10.0, stride_seconds=10.0, macro_window_seconds=5.0)


def test_input_layout_round_trip_and_macro_axis():
    s = stream(np.arange(0.0, 40.0, 0.5))
    plain = build_windows([s], LAYOUT, PACKET, cfg(stride_seconds=10.0))
    grouped = build_windows(
        [s], LAYOUT, PACKET, cfg(stride_seconds=10.0, macro_window_seconds=20.0)
    )

    x = to_input(plain.ids, plain.time_s, 1)
    xm = to_input(grouped.ids, grouped.time_s, 2)

    assert x.dtype == np.float32
    assert x.ndim == 3
    assert x.shape[-1] == 17
    assert xm.ndim == 4
    assert xm.shape[1] == 2
    ids, t = from_input(x)
    assert np.array_equal(ids, plain.ids[:, 0])
    assert np.array_equal(t, plain.time_s[:, 0])


def test_stream_windows_record_how_many_packets_the_cap_dropped():
    s = stream(np.arange(0.0, 10.0, 0.05))

    (w,) = stream_windows(s, LAYOUT, PACKET, cfg(max_packets=20, stride_seconds=10.0))

    assert w.n_packets == 200
    assert len(w.time_s) == 20
