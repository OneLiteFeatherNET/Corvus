from __future__ import annotations

import pytest
from score_rig import (
    Rig,
    end_session_env,
    entry,
    frames_env,
    lookup,
    run_until_idle,
    segment_start_env,
    start_env,
)

from corvus_services.scoring.score_proto import score_pb
from corvus_services.scoring.service import tick_end_hint
from helpers import PSEUDONYM, SESSION

OTHER = b"\x09" * 16


async def begin(rig: Rig, session_id: bytes = SESSION, ctx: str = "pvp") -> None:
    await rig.send(start_env(session_id))
    await rig.send(segment_start_env(session_id, 0, ctx))


async def flush_by_time(rig: Rig) -> None:
    rig.clock.advance(milliseconds=50)
    rig.service.tick()
    await rig.settle()


async def rig_with(tmp_path, *models, **settings) -> Rig:
    rig = Rig(tmp_path, **settings)
    for e, value in models:
        rig.add_model(e, value)
    await rig.start()
    return rig


# --- scoring, persistence and publishing ---------------------------------------------------------


async def test_a_full_window_is_scored_persisted_and_acked(tmp_path):
    rig = await rig_with(tmp_path, (entry("m1", stage="shadow"), 0.8))
    await begin(rig)

    msg = await rig.send(frames_env(n=8))
    await flush_by_time(rig)

    assert msg.acked == 1
    (latest,) = rig.catalog.latest.values()
    assert (latest.score, latest.smoothed, latest.windows) == (
        pytest.approx(0.8),
        pytest.approx(0.8),
        1,
    )
    assert (latest.stage, latest.model_name, latest.pseudonym) == ("shadow", "gb", PSEUDONYM)
    assert len(rig.catalog.history) == 1
    assert rig.metric("corvus_score_frames_fed_total") == 8
    assert rig.metric("corvus_score_windows_completed_total") == 1
    assert rig.metric("corvus_score_windows_scored_total", model="gb:1", stage="shadow") == 1


async def test_shadow_scores_are_persisted_but_never_published(tmp_path):
    rig = await rig_with(tmp_path, (entry("m1", stage="shadow"), 0.8))
    await begin(rig)
    await rig.send(frames_env(n=8))
    await flush_by_time(rig)

    assert rig.publisher.sent == []
    assert len(rig.catalog.latest) == 1


@pytest.mark.parametrize(("stage", "proto_stage"), [("canary", 2), ("enforce", 3)])
async def test_canary_and_enforce_scores_are_published_per_game_context(
    tmp_path, stage, proto_stage
):
    rig = await rig_with(tmp_path, (entry("m1", stage=stage), 0.8))
    await begin(rig)
    await rig.send(frames_env(n=8))
    await flush_by_time(rig)

    ((subject, data),) = rig.publisher.sent
    score = score_pb.Score.FromString(data)
    assert subject == "corvus.score.v1.pvp"
    assert score.stage == proto_stage
    assert score.player_pseudonym == PSEUDONYM
    assert score.pseudonym_key_id == "k1"
    assert score.session_id == SESSION
    assert (score.game_context, score.domain, score.model_name) == ("pvp", "combat", "gb")
    assert score.score == pytest.approx(0.8)
    assert score.windows == 1
    assert score.window_end_offset_nanos == 70_000_000
    assert len(rig.catalog.latest) == 1  # published scores are persisted as well


async def test_shadow_and_enforce_models_of_one_context_score_the_same_window(tmp_path):
    rig = await rig_with(
        tmp_path,
        (entry("s", name="shadow-model", stage="shadow"), 0.1),
        (entry("e", name="enforce-model", stage="enforce", domain="movement"), 0.9),
    )
    await begin(rig)
    await rig.send(frames_env(n=8))
    await flush_by_time(rig)

    assert len(rig.catalog.latest) == 2
    assert [score_pb.Score.FromString(d).model_name for _, d in rig.publisher.sent] == [
        "enforce-model"
    ]


async def test_a_failed_publish_is_counted_and_the_score_is_still_persisted(tmp_path):
    rig = await rig_with(tmp_path, (entry("m1", stage="enforce"), 0.8))
    rig.publisher.fail = True
    await begin(rig)
    await rig.send(frames_env(n=8))
    await flush_by_time(rig)

    assert rig.metric("corvus_score_publish_failures_total") == 1
    assert len(rig.catalog.history) == 1


async def test_scores_are_smoothed_over_the_windows_of_a_segment(tmp_path):
    rig = await rig_with(tmp_path, (entry("m1", stage="shadow"), 0.8))
    await begin(rig)
    await rig.send(frames_env(first=0, n=8))
    await flush_by_time(rig)
    rig.factory.sessions["m1"].value = 0.2

    await rig.send(frames_env(first=8, n=8))
    await flush_by_time(rig)

    (latest,) = rig.catalog.latest.values()
    assert latest.score == pytest.approx(0.2)
    assert latest.smoothed == pytest.approx(0.5)  # alpha 0.5: 0.5 * 0.2 + 0.5 * 0.8
    assert latest.windows == 2


async def test_a_model_that_fails_does_not_stop_the_others(tmp_path):
    rig = await rig_with(
        tmp_path,
        (entry("bad", name="bad"), 0.5),
        (entry("ok", name="ok", domain="movement"), 0.6),
    )

    def boom(_x):
        raise RuntimeError("ort failure")

    rig.factory.sessions["bad"].run = boom  # type: ignore[method-assign]
    await begin(rig)
    await rig.send(frames_env(n=8))
    await flush_by_time(rig)

    assert [r.model_name for r in rig.catalog.latest.values()] == ["ok"]
    assert rig.metric("corvus_score_inference_errors_total", model="bad:1") == 1


async def test_a_game_context_without_a_model_is_not_featurized(tmp_path):
    rig = await rig_with(tmp_path, (entry("m1", ctx="pvp"), 0.8))
    await begin(rig, ctx="lobby")

    msg = await rig.send(frames_env(n=16))
    await flush_by_time(rig)

    assert msg.acked == 1
    assert rig.metric("corvus_score_frames_fed_total") == 0
    assert rig.catalog.latest == {}


# --- micro-batching across sessions ----------------------------------------------------------------


async def test_windows_of_different_sessions_share_one_model_run_when_the_wait_elapses(tmp_path):
    rig = await rig_with(tmp_path, (entry("m1"), 0.8), score_batch_max=64)
    for sid in (SESSION, OTHER):
        await begin(rig, sid)
        await rig.send(frames_env(sid, n=8))
    await run_until_idle()
    assert rig.factory.sessions["m1"].runs == []  # neither full nor old enough yet

    await flush_by_time(rig)

    assert rig.factory.sessions["m1"].runs == [2]


async def test_a_full_batch_is_dispatched_without_waiting(tmp_path):
    rig = await rig_with(tmp_path, (entry("m1"), 0.8), score_batch_max=4)
    for i in range(4):
        sid = bytes([i + 1]) * 16
        await begin(rig, sid)
        await rig.send(frames_env(sid, n=8))
    await run_until_idle()

    assert rig.factory.sessions["m1"].runs == [4]


# --- catalog fallback ------------------------------------------------------------------------------


async def test_a_session_started_before_the_consumer_is_resolved_from_the_catalog(tmp_path):
    rig = await rig_with(tmp_path, (entry("m1"), 0.8))
    rig.catalog.sessions[SESSION] = lookup()

    msg = await rig.send(frames_env(n=8))
    await rig.send(frames_env(first=8, n=8))
    await flush_by_time(rig)

    assert msg.acked == 1
    assert rig.catalog.lookups == [SESSION]  # looked up once, then tracked
    assert rig.metric("corvus_score_catalog_fallbacks_total", result="hit") == 1
    assert rig.metric("corvus_score_unknown_session_skips_total") == 0
    assert rig.catalog.latest.popitem()[1].windows == 2


async def test_a_session_unknown_to_the_catalog_is_skipped_and_counted(tmp_path):
    rig = await rig_with(tmp_path, (entry("m1"), 0.8))

    msg = await rig.send(frames_env(n=8))
    await flush_by_time(rig)

    assert msg.acked == 1
    assert rig.metric("corvus_score_unknown_session_skips_total") == 1
    assert rig.metric("corvus_score_catalog_fallbacks_total", result="miss") == 1
    assert rig.catalog.latest == {}


async def test_a_failed_catalog_lookup_is_remembered_for_a_short_while(tmp_path):
    rig = await rig_with(tmp_path, (entry("m1"), 0.8), score_lookup_retry_s=5.0)
    await rig.send(frames_env(n=8))
    await rig.send(frames_env(first=8, n=8))
    assert rig.catalog.lookups == [SESSION]

    rig.clock.advance(seconds=6)
    rig.catalog.sessions[SESSION] = lookup()
    await rig.send(frames_env(first=16, n=8))

    assert rig.catalog.lookups == [SESSION, SESSION]
    assert rig.metric("corvus_score_unknown_session_skips_total") == 2


async def test_a_segment_written_by_ingest_after_the_session_start_is_found(tmp_path):
    rig = await rig_with(tmp_path, (entry("m1"), 0.8))
    await rig.send(start_env())  # the SegmentStart was not seen by this scorer
    rig.catalog.sessions[SESSION] = lookup(segments={0: "pvp"})

    await rig.send(frames_env(n=8))
    await flush_by_time(rig)

    assert len(rig.catalog.latest) == 1


async def test_a_catalog_outage_asks_for_redelivery_and_gives_up_after_the_last_attempt(tmp_path):
    rig = await rig_with(tmp_path, (entry("m1"), 0.8), unknown_session_max_deliver=3)
    rig.catalog.fail_lookup = True

    first = await rig.send(frames_env(n=8), delivery_count=1)
    last = await rig.send(frames_env(n=8), delivery_count=3)

    assert (first.naked, first.acked) == (1, 0)
    assert (last.naked, last.acked) == (0, 1)
    assert rig.metric("corvus_score_unknown_session_skips_total") == 1


async def test_an_undecodable_message_is_terminated(tmp_path):
    from helpers import FakeMessage  # noqa: PLC0415

    rig = await rig_with(tmp_path)
    msg = FakeMessage(b"\xff\xff\xff", "bad", rig.log)

    await rig.service.handle(msg)

    assert (msg.termed, msg.acked) == (1, 0)


# --- featurizer state lifetime ---------------------------------------------------------------------


async def test_session_end_scores_the_tail_and_frees_all_state(tmp_path):
    rig = await rig_with(tmp_path, (entry("m1"), 0.8))
    await begin(rig)
    await rig.send(frames_env(n=12))  # one full window and 4 frames of tail
    assert len(rig.tracker) == 1

    await rig.send(end_session_env())
    await rig.settle()

    assert len(rig.tracker) == 0
    assert len(rig.ema) == 0
    assert rig.catalog.latest.popitem()[1].windows == 2  # the tail window was scored too


async def test_lru_eviction_frees_the_oldest_session_and_scores_its_tail(tmp_path):
    rig = await rig_with(tmp_path, (entry("m1"), 0.8), score_sessions_max=1)
    await begin(rig, SESSION)
    await rig.send(frames_env(SESSION, n=12))

    await begin(rig, OTHER)
    await rig.settle()

    assert len(rig.tracker) == 1
    assert rig.metric("corvus_score_session_evictions_total", reason="lru") == 1
    assert rig.metric("corvus_score_sessions_tracked") == 1
    assert rig.catalog.latest.popitem()[1].windows == 2


async def test_idle_sessions_are_evicted_on_tick(tmp_path):
    rig = await rig_with(tmp_path, (entry("m1"), 0.8), score_session_idle_s=60.0)
    await begin(rig)
    await rig.send(frames_env(n=12))

    rig.clock.advance(seconds=61)
    rig.service.tick()
    await rig.settle()

    assert len(rig.tracker) == 0
    assert len(rig.ema) == 0
    assert rig.metric("corvus_score_session_evictions_total", reason="idle") == 1


async def test_a_redelivered_session_start_keeps_the_featurizer_state(tmp_path):
    rig = await rig_with(tmp_path, (entry("m1"), 0.8))
    await begin(rig)
    await rig.send(frames_env(n=4))

    await rig.send(start_env())
    await rig.send(frames_env(first=4, n=4))
    await flush_by_time(rig)

    assert len(rig.catalog.latest) == 1  # 4 + 4 frames still made one window


async def test_a_new_segment_closes_the_run_of_the_previous_one(tmp_path):
    rig = await rig_with(tmp_path, (entry("m1"), 0.8))
    await begin(rig)
    await rig.send(frames_env(n=12))
    await rig.send(segment_start_env(SESSION, 1, "pvp"))  # flushes the 4-frame tail of segment 0
    await flush_by_time(rig)

    assert rig.catalog.latest.popitem()[1].windows == 2


# --- tick-end hint ---------------------------------------------------------------------------------


def test_clients_that_cannot_send_tick_end_are_known_to_have_a_null_rate():
    assert tick_end_hint(767, 768) is False


def test_newer_clients_leave_the_tick_end_rate_to_inference():
    assert tick_end_hint(768, 768) is None
    assert tick_end_hint(773, 768) is None
