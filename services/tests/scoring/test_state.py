"""Session tracker, smoothing, micro-batcher and writer."""

from __future__ import annotations

from datetime import timedelta

import pytest
from score_rig import SMALL, FakeScoreCatalog, feature_frames

from corvus_services.scoring.batching import MicroBatcher, PendingWindow
from corvus_services.scoring.metrics import ScoreMetrics
from corvus_services.scoring.ports import ScoreRecord
from corvus_services.scoring.sessions import SessionState, SessionTracker
from corvus_services.scoring.smoothing import EmaTracker
from corvus_services.scoring.writer import ScoreWriter
from helpers import PSEUDONYM, FakeClock


def _state(n: int) -> SessionState:
    return SessionState(bytes([n]) * 16, PSEUDONYM, "k1", 769)


# --- session tracker -----------------------------------------------------------------------------


def test_tracker_evicts_the_least_recently_used_state_beyond_its_bound():
    tracker = SessionTracker(2, timedelta(minutes=10), FakeClock())
    tracker.put(_state(1))
    tracker.put(_state(2))
    tracker.get(_state(1).session_id)  # touch 1, so 2 is the oldest

    evicted = tracker.put(_state(3))

    assert [e.session_id for e in evicted] == [_state(2).session_id]
    assert len(tracker) == 2


def test_tracker_evicts_idle_sessions_only():
    clock = FakeClock()
    tracker = SessionTracker(10, timedelta(seconds=60), clock)
    tracker.put(_state(1))
    clock.advance(seconds=40)
    tracker.put(_state(2))
    clock.advance(seconds=30)  # 1 idle for 70 s, 2 for 30 s

    evicted = tracker.evict_idle()

    assert [e.session_id for e in evicted] == [_state(1).session_id]
    assert _state(2).session_id in tracker
    assert _state(1).session_id not in tracker


def test_reading_a_session_resets_its_idle_time():
    clock = FakeClock()
    tracker = SessionTracker(10, timedelta(seconds=60), clock)
    tracker.put(_state(1))
    clock.advance(seconds=50)
    tracker.get(_state(1).session_id)
    clock.advance(seconds=50)

    assert tracker.evict_idle() == []


def test_flushing_a_state_frees_its_featurizers():
    state = _state(1)
    state.feed(0, feature_frames(12), {SMALL}, None)
    assert state.featurizer_count == 1

    tail = state.flush()

    assert state.featurizer_count == 0
    assert len(tail) == 1  # the 4 frames after the first full window


# --- smoothing -----------------------------------------------------------------------------------


KEY = (PSEUDONYM, 0, "m1")
SID = b"\x01" * 16


def test_first_window_is_its_own_smoothed_value():
    assert EmaTracker(0.3).update(KEY, SID, 0.8) == (0.8, 1)


def test_smoothing_is_an_exponential_moving_average():
    ema = EmaTracker(0.25)
    ema.update(KEY, SID, 1.0)

    smoothed, windows = ema.update(KEY, SID, 0.0)

    assert smoothed == pytest.approx(0.75)
    assert windows == 2
    assert ema.update(KEY, SID, 0.0)[0] == pytest.approx(0.5625)


def test_smoothing_is_kept_per_segment_and_per_model():
    ema = EmaTracker(0.5)
    ema.update((PSEUDONYM, 0, "m1"), SID, 1.0)

    assert ema.update((PSEUDONYM, 1, "m1"), SID, 0.0) == (0.0, 1)
    assert ema.update((PSEUDONYM, 0, "m2"), SID, 0.0) == (0.0, 1)


def test_releasing_a_session_forgets_its_smoothing_state():
    ema = EmaTracker(0.5)
    ema.update(KEY, SID, 1.0)

    assert ema.release_session(SID) == 1
    assert ema.update(KEY, SID, 0.2) == (0.2, 1)


def test_ema_is_bounded():
    ema = EmaTracker(0.5, max_entries=2)
    for i in range(5):
        ema.update((PSEUDONYM, i, "m"), SID, 0.5)

    assert len(ema) == 2


def test_alpha_must_be_in_range():
    with pytest.raises(ValueError, match="alpha"):
        EmaTracker(0.0)


# --- micro-batcher -------------------------------------------------------------------------------


def _pending(n: int) -> list[PendingWindow]:
    return [PendingWindow(None, SMALL, PSEUDONYM, "k1", "pvp") for _ in range(n)]  # type: ignore[arg-type]


def test_batch_is_ready_at_the_maximum_size_without_waiting():
    batcher = MicroBatcher(4, timedelta(milliseconds=50), FakeClock())
    batcher.add(_pending(3))
    assert not batcher.ready()

    batcher.add(_pending(1))

    assert batcher.ready()
    assert len(batcher.take().windows) == 4


def test_batch_is_ready_once_the_oldest_window_waited_long_enough():
    clock = FakeClock()
    batcher = MicroBatcher(64, timedelta(milliseconds=50), clock)
    batcher.add(_pending(2))
    clock.advance(milliseconds=49)
    assert not batcher.ready()

    clock.advance(milliseconds=1)

    assert batcher.ready()
    assert len(batcher.take().windows) == 2


def test_take_never_exceeds_the_maximum_size():
    batcher = MicroBatcher(4, timedelta(seconds=1), FakeClock())
    batcher.add(_pending(10))

    assert len(batcher.take().windows) == 4
    assert len(batcher) == 6


def test_releases_wait_for_the_windows_queued_before_them():
    batcher = MicroBatcher(4, timedelta(seconds=1), FakeClock())
    batcher.add(_pending(6))
    batcher.release(SID)

    assert batcher.take().releases == []
    assert batcher.take().releases == [SID]


def test_a_release_without_pending_windows_is_ready_at_once():
    batcher = MicroBatcher(4, timedelta(seconds=1), FakeClock())
    batcher.release(SID)

    assert batcher.ready()
    assert batcher.take().releases == [SID]


# --- writer --------------------------------------------------------------------------------------


def _record(i: int = 0, who: bytes = PSEUDONYM, at: int = 0, model: str = "gb") -> ScoreRecord:
    from helpers import T0  # noqa: PLC0415

    return ScoreRecord(
        who, SID, 0, "pvp", "combat", model, "1", "shadow", 0.5, 0.5, i + 1, 0, 1,
        T0 + timedelta(seconds=at),
    )  # fmt: skip


def _writer(
    catalog: FakeScoreCatalog, queue: int = 100, batch: int = 10
) -> tuple[ScoreWriter, ScoreMetrics]:
    metrics = ScoreMetrics()
    return ScoreWriter(catalog, metrics, queue, batch, 1.0), metrics


async def test_latest_is_coalesced_per_key_and_history_keeps_every_row():
    catalog = FakeScoreCatalog()
    writer, _ = _writer(catalog)
    writer.submit(_record(0, at=0))
    writer.submit(_record(1, at=1))

    await writer.flush()

    assert len(catalog.latest) == 1
    assert next(iter(catalog.latest.values())).windows == 2
    assert len(catalog.history) == 2


async def test_an_older_score_does_not_replace_a_newer_latest_row():
    catalog = FakeScoreCatalog()
    writer, _ = _writer(catalog)
    writer.submit(_record(1, at=5))
    writer.submit(_record(0, at=1))

    await writer.flush()

    assert next(iter(catalog.latest.values())).windows == 2


async def test_full_history_queue_drops_history_but_still_upserts_latest():
    catalog = FakeScoreCatalog()
    writer, metrics = _writer(catalog, queue=2)
    for i in range(5):
        writer.submit(_record(i, at=i))

    await writer.flush()

    assert len(catalog.history) == 2
    assert metrics.registry.get_sample_value("corvus_score_history_dropped_total") == 3
    assert next(iter(catalog.latest.values())).windows == 5  # the newest score still landed


async def test_submit_never_writes_to_the_catalog():
    catalog = FakeScoreCatalog()
    writer, _ = _writer(catalog)

    for i in range(50):
        writer.submit(_record(i, who=bytes([i]) * 32))

    assert catalog.write_calls == 0
    assert writer.queued == (50, 50)


async def test_writes_are_batched():
    catalog = FakeScoreCatalog()
    writer, _ = _writer(catalog, batch=10)
    for i in range(25):
        writer.submit(_record(i, who=bytes([i]) * 32))

    await writer.flush()

    assert catalog.write_calls == 3
    assert len(catalog.latest) == 25
    assert len(catalog.history) == 25


async def test_failed_write_is_kept_and_retried():
    catalog = FakeScoreCatalog()
    writer, metrics = _writer(catalog)
    writer.submit(_record(0))
    catalog.fail_writes = True
    await writer.flush()
    assert catalog.history == []
    assert metrics.registry.get_sample_value("corvus_score_write_failures_total") == 1

    catalog.fail_writes = False
    await writer.flush()

    assert len(catalog.history) == 1
    assert len(catalog.latest) == 1
