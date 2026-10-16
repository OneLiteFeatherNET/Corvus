"""Raw-byte models in the scorer: dispatch, tokenizer validation, online windows, privacy, guard."""

from __future__ import annotations

import dataclasses
import logging
import math

import numpy as np
import pytest
from raw_support import LAYOUT, WINDOW, RawAssets, payload_frames_env
from score_rig import (
    SMALL,
    Rig,
    end_session_env,
    entry,
    frames_env,
    segment_start_env,
    start_env,
)

from corvus_services.scoring.models import ModelRejectedError, check_raw_shape
from corvus_services.scoring.raw import RawWindow, estimate_p99_ms, raw_frame, stack_inputs
from helpers import SESSION


@pytest.fixture(scope="module")
def assets(tmp_path_factory) -> RawAssets:
    return RawAssets(tmp_path_factory.mktemp("tokenizer"))


def raw_entry(id: str = "r1", name: str = "raw", stage: str = "shadow", ctx: str = "pvp"):
    return entry(
        id, name=name, stage=stage, ctx=ctx, candidate_type="raw_hier_classifier_calibrated"
    )


def add_raw(rig: Rig, assets: RawAssets, e=None, value=0.7, window=None, **kw) -> None:
    rig.add_model(
        e or raw_entry(),
        value,
        spec=kw.pop("spec", assets.feature_spec(window)),
        card=kw.pop("card", assets.card(window)),
        tokenizer=kw.pop("tokenizer", assets.tokenizer_bytes),
    )
    assert not kw


async def begin(rig: Rig, protocol: int = 769) -> None:
    await rig.send(start_env(SESSION, protocol=protocol))
    await rig.send(segment_start_env(SESSION, 0, "pvp"))


# --- dispatch by input_kind ----------------------------------------------------------------------


async def test_a_raw_byte_model_is_loaded_with_its_tokenizer_and_dispatched_by_input_kind(
    tmp_path, assets
):
    rig = Rig(tmp_path)
    add_raw(rig, assets)

    result = await rig.manager.sync()

    assert result.loaded == ["r1"]
    (model,) = rig.manager.current.models
    assert model.input_kind == "raw_bytes"
    assert model.tokenizer is not None
    assert model.raw_spec == assets.spec()
    assert (tmp_path / "models" / "r1" / "tokenizer.json").read_bytes() == assets.tokenizer_bytes


async def test_a_token_sequence_model_with_the_same_onnx_rank_is_not_taken_for_a_raw_one(
    tmp_path, assets
):
    rig = Rig(tmp_path)
    rig.add_model(entry("seq"), card={"input_kind": "seq"})  # fake graph is rank 2: not a seq model

    result = await rig.manager.sync()

    assert "seq" in result.failed
    assert rig.manager.current.models == ()


@pytest.mark.parametrize(
    ("card_kind", "raw_spec"),
    [("raw_bytes", False), ("agg", True), ("seq", True), ("mystery", False)],
)
async def test_a_card_that_contradicts_the_feature_spec_keeps_the_model_out(
    tmp_path, assets, card_kind, raw_spec
):
    rig = Rig(tmp_path)
    spec = assets.feature_spec() if raw_spec else None
    rig.add_model(
        raw_entry(), spec=spec, card={"input_kind": card_kind}, tokenizer=assets.tokenizer_bytes
    )

    result = await rig.manager.sync()

    assert "r1" in result.failed
    assert rig.metric("corvus_score_model_load_failures_total", reason="rejected") == 1


async def test_a_feature_model_whose_card_names_its_kind_still_loads(tmp_path):
    rig = Rig(tmp_path)
    rig.add_model(entry("m1"), card={"input_kind": "agg"})

    result = await rig.manager.sync()

    assert result.loaded == ["m1"]
    assert rig.manager.current.models[0].input_kind == "agg"


async def test_raw_and_feature_models_of_one_context_are_kept_apart(tmp_path, assets):
    rig = Rig(tmp_path)
    rig.add_model(entry("m1"))
    add_raw(rig, assets)
    await rig.manager.sync()
    current = rig.manager.current

    assert current.configs_for("pvp") == {SMALL}
    assert list(current.raw_specs_for("pvp")) == [assets.spec()]
    assert [m.id for m in current.matching("pvp", SMALL)] == ["m1"]
    assert [m.id for m in current.matching("pvp", assets.spec())] == ["r1"]


# --- tokenizer validation ------------------------------------------------------------------------


async def test_a_tokenizer_that_does_not_hash_to_the_spec_keeps_the_model_out(tmp_path, assets):
    rig = Rig(tmp_path)
    rig.add_model(raw_entry(), spec=assets.feature_spec(), card=assets.card(), tokenizer=b"{}")
    rig.add_model(entry("good", name="agg"))

    result = await rig.manager.sync()

    assert result.loaded == ["good"]
    assert "hash" in result.failed["r1"]
    assert [m.id for m in rig.manager.current.models] == ["good"]
    assert rig.metric("corvus_score_model_load_failures_total", reason="rejected") == 1


async def test_a_card_that_names_another_tokenizer_than_the_spec_keeps_the_model_out(
    tmp_path, assets
):
    rig = Rig(tmp_path)
    card = assets.card(tokenizer={"file": "tokenizer.json", "sha256": "f" * 64})
    add_raw(rig, assets, card=card)

    result = await rig.manager.sync()

    assert "r1" in result.failed


async def test_a_missing_tokenizer_file_is_a_failed_load_that_is_retried(tmp_path, assets):
    rig = Rig(tmp_path)
    add_raw(rig, assets, tokenizer=None)
    rig.files.objects.pop("models/r1/tokenizer.json", None)

    first = await rig.manager.sync()
    rig.files.objects["models/r1/tokenizer.json"] = assets.tokenizer_bytes
    second = await rig.manager.sync()

    assert "r1" in first.failed
    assert second.loaded == ["r1"]


def test_an_onnx_input_with_the_wrong_channel_count_or_rank_is_rejected(assets):
    spec = assets.spec()

    check_raw_shape((None, None, 17), spec)
    with pytest.raises(ModelRejectedError, match="channels"):
        check_raw_shape((None, None, 49), spec)
    with pytest.raises(ModelRejectedError, match="rank"):
        check_raw_shape((None, 17), spec)


# --- online scoring ------------------------------------------------------------------------------


async def test_payload_frames_become_windows_that_the_raw_model_scores(tmp_path, assets):
    rig = Rig(tmp_path)
    add_raw(rig, assets, value=0.7)
    await rig.start()
    await begin(rig)

    msg = await rig.send(payload_frames_env(n=250))
    await rig.settle()

    assert msg.acked == 1
    session = rig.factory.sessions["r1"]
    assert sum(session.runs) == 2, "two one-second windows are final, the third is still open"
    x = np.concatenate(session.inputs)
    assert x.shape == (2, 32, WINDOW_CHANNELS)
    assert x.dtype == np.float32
    (latest,) = rig.catalog.latest.values()
    assert (latest.model_name, latest.score, latest.windows) == ("raw", pytest.approx(0.7), 2)
    assert rig.metric("corvus_score_windows_scored_total", model="raw:1", stage="shadow") == 2
    assert rig.metric("corvus_score_raw_window_packets_count") == 2


WINDOW_CHANNELS = 17


async def test_the_open_window_is_scored_when_the_session_ends(tmp_path, assets):
    rig = Rig(tmp_path)
    add_raw(rig, assets)
    await rig.start()
    await begin(rig)
    await rig.send(payload_frames_env(n=250))

    await rig.send(end_session_env())
    await rig.settle()

    assert sum(rig.factory.sessions["r1"].runs) == 3


async def test_the_first_packet_of_a_window_carries_the_client_protocol_token(tmp_path, assets):
    rig = Rig(tmp_path)
    add_raw(rig, assets)
    await rig.start()
    await begin(rig, protocol=769)

    await rig.send(payload_frames_env(n=150))
    await rig.settle()

    x = rig.factory.sessions["r1"].inputs[0]
    assert int(x[0, 0, 3]) == LAYOUT.proto_id(769)
    assert int(x[0, 1, 3]) != LAYOUT.proto_id(769), "proto_mode first: the first packet only"
    assert int(x[0, 0, 0]) == 5, "[DIR_SB]"
    assert int(x[0, 0, 1]) == LAYOUT.pkt_id(14)


async def test_a_new_segment_closes_the_open_window_of_the_old_one(tmp_path, assets):
    rig = Rig(tmp_path)
    add_raw(rig, assets)
    await rig.start()
    await begin(rig)
    await rig.send(payload_frames_env(segment=0, n=50))

    await rig.send(segment_start_env(SESSION, 1, "pvp"))
    await rig.settle()

    assert [r.segment for r in rig.catalog.history] == [0]
    assert rig.service._tracker.get(SESSION).segments[1] == "pvp"


async def test_feature_and_raw_models_score_the_same_batches_in_one_scorer(tmp_path, assets):
    rig = Rig(tmp_path)
    rig.add_model(entry("m1", name="gb", stage="enforce"), 0.2)
    add_raw(rig, assets, raw_entry(stage="enforce"), value=0.9)
    await rig.start()
    await begin(rig)
    env = frames_env(n=250)
    for f in env.batch.frames:
        f.payload = b"\x01\x02\x03\x04\x05\x06" * 3
        f.payload_original_size = 18

    await rig.send(env)
    await rig.settle()

    by_model = {r.model_name: r.score for r in rig.catalog.history}
    assert by_model == {"gb": pytest.approx(0.2), "raw": pytest.approx(0.9)}
    agg = np.concatenate(rig.factory.sessions["m1"].inputs)
    raw = np.concatenate(rig.factory.sessions["r1"].inputs)
    assert agg.ndim == 2
    assert raw.ndim == 3
    assert len(rig.publisher.sent) == len(rig.catalog.history)


async def test_a_context_without_a_raw_model_does_not_tokenize_anything(tmp_path, assets):
    rig = Rig(tmp_path)
    rig.add_model(entry("m1"))
    await rig.start()
    await begin(rig)

    await rig.send(frames_env(n=16))
    await rig.settle()

    state = rig.service._tracker.get(SESSION)
    assert state._raw == {}


# --- privacy and memory --------------------------------------------------------------------------


async def test_no_payload_bytes_are_kept_once_a_window_is_complete(tmp_path, assets):
    rig = Rig(tmp_path)
    add_raw(rig, assets)
    await rig.start()
    await begin(rig)
    secret = b"TOP-SECRET-PAYLOAD"

    await rig.send(payload_frames_env(n=250, payload=secret))
    state = rig.service._tracker.get(SESSION)
    (windower,) = state._raw.values()

    # frames of the two finished windows are gone; the open one is token ids only
    assert windower.buffered <= 100
    assert secret not in b"".join(w.window.x.tobytes() for w in pending_raw(rig))
    await rig.send(end_session_env())
    assert windower.buffered == 0
    assert state._raw == {}


def pending_raw(rig: Rig) -> list:
    return [w for _, w in rig.batcher._items if isinstance(w.window, RawWindow)]


async def test_a_full_window_buffer_drops_the_oldest_frames_and_counts_them(tmp_path, assets):
    rig = Rig(tmp_path, score_raw_max_buffered_frames=64)
    add_raw(rig, assets)
    await rig.start()
    await begin(rig)

    await rig.send(payload_frames_env(n=100))

    assert rig.metric("corvus_score_raw_frames_dropped_total") == 36


def test_raw_frames_map_the_proto_frame_and_drop_an_empty_payload():
    from corvus_services.proto import pb

    f = pb.Frame()
    f.seq, f.offset_nanos, f.direction, f.packet_type = 7, 123, pb.DIRECTION_CLIENTBOUND, 9
    f.payload, f.payload_original_size = b"abc", 5

    mapped = raw_frame(f)

    assert (mapped.seq, mapped.offset_nanos, mapped.direction, mapped.packet_type) == (7, 123, 2, 9)
    assert (mapped.payload, mapped.payload_original_size) == (b"abc", 5)
    assert raw_frame(pb.Frame()).payload is None


def test_stacking_pads_windows_to_the_longest_one_with_zeros():
    a = RawWindow(b"s", 0, 0, 1, np.ones((3, 5), dtype=np.float32))
    b = RawWindow(b"s", 0, 0, 1, np.ones((5, 5), dtype=np.float32))

    x = stack_inputs([a, b])

    assert x.shape == (2, 5, 5)
    assert x[0, 3:].sum() == 0
    assert x[1].sum() == 25


# --- window size, latency and the budget guard ---------------------------------------------------


async def test_the_window_size_of_a_loaded_raw_model_is_exposed_as_metrics(tmp_path, assets):
    rig = Rig(tmp_path)
    add_raw(rig, assets)

    await rig.manager.sync()

    assert rig.metric("corvus_score_model_window_max_packets", model="raw:1") == 32
    assert rig.metric("corvus_score_model_window_seconds", model="raw:1") == 1.0
    assert rig.metric("corvus_score_model_estimated_p99_ms", model="raw:1") == pytest.approx(
        estimate_p99_ms(32, 2)
    )


async def test_a_max_packets_that_cannot_meet_the_budget_is_warned_about_at_load(
    tmp_path, assets, caplog
):
    rig = Rig(tmp_path, score_p99_budget_ms=10.0)
    big = dataclasses.replace(WINDOW, max_packets=1024)
    add_raw(rig, assets, window=big)

    with caplog.at_level(logging.WARNING, logger="corvus.score.models"):
        result = await rig.manager.sync()

    assert result.loaded == ["r1"], "the model is still served"
    (message,) = [r.getMessage() for r in caplog.records if "budget" in r.getMessage()]
    assert "max_packets=1024" in message
    assert "raw:1" in message


async def test_a_window_within_the_budget_logs_no_warning(tmp_path, assets, caplog):
    rig = Rig(tmp_path, score_p99_budget_ms=10.0)
    add_raw(rig, assets)

    with caplog.at_level(logging.WARNING, logger="corvus.score.models"):
        await rig.manager.sync()

    assert [r for r in caplog.records if "budget" in r.getMessage()] == []


async def test_a_zero_budget_disables_the_guard(tmp_path, assets, caplog):
    rig = Rig(tmp_path, score_p99_budget_ms=0.0)
    add_raw(rig, assets, window=dataclasses.replace(WINDOW, max_packets=1024))

    with caplog.at_level(logging.WARNING, logger="corvus.score.models"):
        await rig.manager.sync()

    assert [r for r in caplog.records if "budget" in r.getMessage()] == []


@pytest.mark.parametrize(("packets", "ms"), [(128, 3.8), (512, 17.0), (1024, 41.9)])
def test_the_estimate_matches_the_measured_points_on_one_thread(packets, ms):
    assert estimate_p99_ms(packets, 1) == pytest.approx(ms * 1.15)


def test_the_estimate_scales_with_threads_and_the_anomaly_heads_and_grows_with_packets():
    one = estimate_p99_ms(512, 1)

    assert estimate_p99_ms(512, 2) == pytest.approx(one * 0.6)
    assert estimate_p99_ms(512, 1, anomaly=True) == pytest.approx(one * 2.6)
    assert estimate_p99_ms(64, 1) < estimate_p99_ms(256, 1) < estimate_p99_ms(2048, 1)


def test_the_estimate_at_the_default_2048_packets_is_finite_and_continues_the_last_slope():
    at_1024, at_2048, at_3072 = (estimate_p99_ms(n, 1) for n in (1024, 2048, 3072))

    assert math.isfinite(at_2048)
    assert at_1024 < at_2048 < at_3072
    assert at_2048 - at_1024 == pytest.approx(at_3072 - at_2048, rel=1e-6)
