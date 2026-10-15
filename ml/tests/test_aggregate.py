import pytest

from builders import (
    CLIENTBOUND,
    SERVERBOUND,
    SID,
    batch,
    frame,
    segment_start,
    session_end,
    session_start,
    simple_session,
)
from corvus_ml.aggregate import aggregate_stream
from corvus_ml.privacy import Denylist
from corvus_ml.proto import pb
from corvus_ml.report import build_report


def aggregate(envelopes):
    return aggregate_stream(envelopes, "t.cvc", Denylist())


def seqs(*numbers):
    return [frame(n, offset_nanos=n * 1_000_000) for n in numbers]


def test_frame_rate_is_frames_over_session_duration():
    frames = [frame(i, payload=b"x" * 5) for i in range(1, 101)]

    report = build_report(aggregate(simple_session(frames)))

    pvp = report["contexts"]["pvp"]
    assert pvp["player_seconds"] == pytest.approx(10.0)
    assert pvp["frames_per_s_per_player"] == pytest.approx(10.0)
    assert pvp["bytes_per_s_per_player"] == pytest.approx(50.0)


def test_rates_are_split_by_direction_and_state():
    frames = [
        frame(1, direction=SERVERBOUND),
        frame(2, direction=SERVERBOUND),
        frame(3, direction=CLIENTBOUND, state=pb.CONNECTION_STATE_CONFIGURATION),
    ]

    pvp = build_report(aggregate(simple_session(frames)))["contexts"]["pvp"]

    assert pvp["by_direction"]["serverbound"]["frames_per_s"] == pytest.approx(0.2)
    assert pvp["by_direction"]["clientbound"]["frames_per_s"] == pytest.approx(0.1)
    rows = {(r["direction"], r["state"]): r["frames"] for r in pvp["by_direction_state"]}
    assert rows[("clientbound", "configuration")] == 1


def test_top_packets_rank_by_frame_count():
    frames = [frame(1, packet_type=5), frame(2, packet_type=9), frame(3, packet_type=9)]

    top = build_report(aggregate(simple_session(frames)))["contexts"]["pvp"]["top_packets"]

    assert [(p["packet_type"], p["frames"]) for p in top] == [(9, 2), (5, 1)]


def test_unmapped_packets_are_identified_by_wire_id():
    frames = [frame(1, packet_type=0, wire_id=99)]

    pvp = build_report(aggregate(simple_session(frames)))["contexts"]["pvp"]

    assert pvp["unmapped_frames"] == 1
    assert pvp["top_packets"][0]["wire_packet_id"] == 99


def test_decoded_event_rates_are_counted_by_kind():
    frames = [
        frame(1, movement=pb.MovementEvent(has_position=True)),
        frame(2, movement=pb.MovementEvent(has_rotation=True)),
        frame(3, swing=pb.SwingEvent()),
    ]

    events = build_report(aggregate(simple_session(frames)))["contexts"]["pvp"]["events"]

    assert events["movement"]["frames_per_s"] == pytest.approx(0.2)
    assert events["swing"]["frames"] == 1


def test_gap_in_sequence_numbers_is_detected():
    gaps = build_report(aggregate(simple_session(seqs(1, 2, 5, 6))))["contexts"]["pvp"]["gaps"]

    assert gaps == {"events": 1, "missing_frames": 2, "out_of_order": 0}


def test_gap_across_batches_is_detected():
    envelopes = [
        session_start(),
        segment_start(),
        batch(seqs(1, 2)),
        batch(seqs(4)),
        session_end(),
    ]

    gaps = build_report(aggregate(envelopes))["contexts"]["pvp"]["gaps"]

    assert gaps["missing_frames"] == 1


def test_contiguous_sequence_has_no_gaps():
    gaps = build_report(aggregate(simple_session(seqs(1, 2, 3))))["contexts"]["pvp"]["gaps"]

    assert gaps == {"events": 0, "missing_frames": 0, "out_of_order": 0}


def test_repeated_sequence_number_counts_as_out_of_order():
    gaps = build_report(aggregate(simple_session(seqs(1, 2, 2, 3))))["contexts"]["pvp"]["gaps"]

    assert gaps["out_of_order"] == 1
    assert gaps["events"] == 0


def test_drop_rate_is_queue_drops_over_offered_frames():
    envelopes = simple_session(seqs(*range(1, 100)), queue_full=1)

    pvp = build_report(aggregate(envelopes))["contexts"]["pvp"]

    assert pvp["counters"]["queue_full"] == 1
    assert pvp["drop_rate"] == pytest.approx(1 / 100)


def test_session_end_counters_beyond_batches_are_attributed_to_last_segment():
    envelopes = [
        session_start(),
        segment_start(),
        batch(seqs(1), queue_full=1),
        session_end(dropped_queue_full=4, listener_errors=2),
    ]

    session = build_report(aggregate(envelopes))["sessions"][0]

    assert session["counters"]["queue_full"] == 4
    assert session["counters"]["listener_errors"] == 2


def test_segments_are_attributed_to_their_game_context():
    envelopes = [
        session_start(started_ms=0 + 1_000),
        segment_start(0, "lobby", started_ms=1_000),
        batch([frame(1)], segment=0),
        segment_start(1, "pvp", started_ms=5_000),
        batch([frame(2), frame(3)], segment=1),
        session_end(ended_ms=11_000),
    ]

    contexts = build_report(aggregate(envelopes))["contexts"]

    assert contexts["lobby"]["player_seconds"] == pytest.approx(4.0)
    assert contexts["lobby"]["frames"] == 1
    assert contexts["pvp"]["player_seconds"] == pytest.approx(6.0)
    assert contexts["pvp"]["frames"] == 2


def test_frames_without_segment_start_land_in_unassigned_context():
    envelopes = [session_start(), batch([frame(1, offset_nanos=2_000_000_000)]), session_end()]

    contexts = build_report(aggregate(envelopes))["contexts"]

    assert list(contexts) == ["unassigned"]


def test_translated_session_is_flagged_in_version_table():
    envelopes = simple_session([frame(1)])
    envelopes[0] = session_start(client=767, observed=770)

    versions = build_report(aggregate(envelopes))["protocol_versions"]

    assert versions == [
        {"client_protocol": 767, "observed_protocol": 770, "translated": True, "sessions": 1}
    ]


def test_native_session_is_not_flagged_translated():
    versions = build_report(aggregate(simple_session([frame(1)])))["protocol_versions"]

    assert versions[0]["translated"] is False


def test_session_without_session_end_is_reported_incomplete():
    envelopes = [session_start(), segment_start(), batch([frame(1, offset_nanos=3_000_000_000)])]

    session = build_report(aggregate(envelopes))["sessions"][0]

    assert session["complete"] is False
    assert session["duration_s"] == pytest.approx(3.0)
    assert session["session_id"] == SID.hex()
