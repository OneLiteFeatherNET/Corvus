"""Window defaults (20 s / 10 s / 2048 packets) and the guarantee that stored specs keep theirs."""

import json
from dataclasses import asdict

from corvus_ml.jobs.raw_job import _read_params
from corvus_ml.raw.spec import PacketConfig, RawBytesSpec, WindowConfig
from corvus_ml.raw.streaming import RawFrame, SegmentWindower
from job_fakes import make_context
from raw_builders import SMALL_LAYOUT, small_tokenizer, tiny_synthetic

NS = 1_000_000_000


def test_a_new_window_config_is_twenty_seconds_with_half_window_stride_and_2048_packets():
    w = WindowConfig()

    assert (w.window_seconds, w.stride_seconds, w.max_packets) == (20.0, 10.0, 2048)
    assert w.stride_seconds == w.window_seconds / 2, "the half-window overlap is kept"


def test_a_raw_build_without_window_params_uses_the_new_defaults():
    params = _read_params(make_context("build_features", kind="raw_bytes"), "raw_bytes")

    w = params["window"]
    assert (w.window_seconds, w.stride_seconds, w.max_packets) == (20.0, 10.0, 2048)


def test_a_raw_build_with_explicit_window_params_still_wins():
    ctx = make_context(
        "build_features", kind="raw_bytes", window_seconds=8.0, stride_seconds=4.0, max_packets=64
    )

    w = _read_params(ctx, "raw_bytes")["window"]

    assert (w.window_seconds, w.stride_seconds, w.max_packets) == (8.0, 4.0, 64)


def old_spec_dict() -> dict:
    tok = small_tokenizer(tiny_synthetic(n_legit=1, n_cheat=1))
    spec = RawBytesSpec("0" * 64, tok.vocab_size, SMALL_LAYOUT, PacketConfig(), WindowConfig())
    d = json.loads(json.dumps(spec.to_dict()))
    d["window"] = {
        "window_seconds": 10.0, "stride_seconds": 5.0, "max_packets": 1024,
        "overflow": "recent", "min_packets": 8, "macro_window_seconds": None,
    }  # fmt: skip
    return d


def test_a_spec_stored_with_ten_second_windows_is_loaded_unchanged():
    stored = old_spec_dict()

    loaded = RawBytesSpec.from_dict(stored)

    assert (loaded.window.window_seconds, loaded.window.stride_seconds) == (10.0, 5.0)
    assert loaded.window.max_packets == 1024


def test_a_loaded_old_spec_is_not_rewritten_when_it_is_saved_again():
    stored = old_spec_dict()

    again = RawBytesSpec.from_dict(stored).to_dict()

    assert again["window"] == stored["window"]
    assert asdict(RawBytesSpec.from_dict(again).window) == stored["window"]


def test_the_windower_cuts_the_grid_of_the_spec_it_is_given_not_a_fixed_one():
    syn = tiny_synthetic(n_legit=1, n_cheat=0, seconds_per_session=30.0)
    tok = small_tokenizer(syn)
    counts = {}
    for seconds in (10.0, 20.0):
        window = WindowConfig(
            window_seconds=seconds, stride_seconds=seconds, max_packets=512, min_packets=1
        )
        spec = RawBytesSpec("0" * 64, tok.vocab_size, tok.layout, PacketConfig(), window)
        w = SegmentWindower(spec, tok, b"s" * 16, 0, "pvp", 768)
        frames = [RawFrame(i, i * NS // 2, 0, 12, b"\x01\x02", 2) for i in range(60)]  # 30 s
        counts[seconds] = len(w.feed(frames) + w.flush())

    assert counts == {10.0: 3, 20.0: 2}
