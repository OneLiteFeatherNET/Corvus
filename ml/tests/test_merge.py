import pytest

from builders import frame, simple_session
from corvus_ml.aggregate import aggregate_stream
from corvus_ml.model import FileResult
from corvus_ml.privacy import Denylist
from corvus_ml.report import build_report


def one_file(sid: bytes, count: int, context: str = "pvp", queue_full: int = 0):
    frames = [frame(i, payload=b"x" * 4) for i in range(1, count + 1)]
    envelopes = simple_session(frames, context=context, sid=sid, queue_full=queue_full)
    return aggregate_stream(envelopes, f"{sid.hex()}.cvc", Denylist())


def merged(*parts) -> FileResult:
    total = FileResult()
    for part in parts:
        total.merge(part)
    return total


def test_merge_sums_frames_and_player_seconds_of_same_context():
    report = build_report(merged(one_file(b"a" * 16, 10), one_file(b"b" * 16, 30)))

    pvp = report["contexts"]["pvp"]
    assert pvp["frames"] == 40
    assert pvp["player_seconds"] == pytest.approx(20.0)
    assert pvp["frames_per_s_per_player"] == pytest.approx(2.0)


def test_merge_keeps_different_contexts_separate():
    report = build_report(merged(one_file(b"a" * 16, 10, "lobby"), one_file(b"b" * 16, 20, "pvp")))

    assert report["contexts"]["lobby"]["frames"] == 10
    assert report["contexts"]["pvp"]["frames"] == 20
    assert report["overall"]["frames"] == 30


def test_merge_lists_every_session():
    report = build_report(merged(one_file(b"a" * 16, 1), one_file(b"b" * 16, 1)))

    assert len(report["sessions"]) == 2


def test_merge_adds_drop_counters_across_files():
    report = build_report(
        merged(one_file(b"a" * 16, 9, queue_full=1), one_file(b"b" * 16, 9, queue_full=2))
    )

    assert report["overall"]["counters"]["queue_full"] == 3
    assert report["overall"]["drop_rate"] == pytest.approx(3 / 21)


def test_merge_counts_protocol_versions_across_files():
    report = build_report(merged(one_file(b"a" * 16, 1), one_file(b"b" * 16, 1)))

    assert report["protocol_versions"][0]["sessions"] == 2


def test_merge_is_independent_of_order():
    a, b = one_file(b"a" * 16, 5), one_file(b"b" * 16, 7, "lobby")

    forward = build_report(merged(a, b))["overall"]
    backward = build_report(merged(one_file(b"b" * 16, 7, "lobby"), one_file(b"a" * 16, 5)))[
        "overall"
    ]

    assert forward == backward


def test_merge_combines_privacy_violations():
    bad = one_file(b"a" * 16, 1)
    bad.privacy.add("uuid_in_string_field", "x")
    other = one_file(b"b" * 16, 1)
    other.privacy.add("uuid_in_string_field", "y")

    assert merged(bad, other).privacy.counts == {"uuid_in_string_field": 2}
