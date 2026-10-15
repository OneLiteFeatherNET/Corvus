import pytest

from builders import frame, simple_session
from corvus_ml.aggregate import aggregate_stream
from corvus_ml.privacy import Denylist
from corvus_ml.report import build_report, render_text


def payload_report(frames):
    result = aggregate_stream(simple_session(frames), "t.cvc", Denylist())
    return build_report(result)


def test_payload_cut_below_its_original_size_counts_as_truncated():
    report = payload_report([frame(1, payload=b"x" * 4, original_size=100)])

    assert report["overall"]["payloads"]["truncated"] == 1


def test_payload_with_matching_original_size_is_not_truncated():
    report = payload_report([frame(1, payload=b"x" * 4, original_size=4)])

    assert report["overall"]["payloads"]["truncated"] == 0


def test_frames_without_payload_are_left_out_of_the_share():
    report = payload_report(
        [
            frame(1, payload=b"x" * 4, original_size=100),
            frame(2, payload=b"", original_size=0),
        ]
    )

    payloads = report["overall"]["payloads"]
    assert payloads["frames_with_payload"] == 1
    assert payloads["truncated_share"] == pytest.approx(1.0)


def test_truncated_share_is_a_fraction_of_frames_with_payload():
    frames = [frame(i, payload=b"x" * 4, original_size=4) for i in range(1, 4)]
    frames.append(frame(4, payload=b"x" * 4, original_size=50))

    report = payload_report(frames)

    assert report["overall"]["payloads"]["truncated_share"] == pytest.approx(0.25)


def test_share_is_none_when_no_payload_was_recorded():
    report = payload_report([frame(1, payload=b"")])

    assert report["overall"]["payloads"]["truncated_share"] is None


def test_old_captures_without_original_size_count_as_untruncated_payloads():
    report = payload_report([frame(1, payload=b"x" * 4)])

    payloads = report["overall"]["payloads"]
    assert payloads["frames_with_payload"] == 1
    assert payloads["truncated"] == 0


def test_text_report_shows_the_truncated_share():
    report = payload_report([frame(1, payload=b"x" * 4, original_size=8)])

    assert "1 truncated (100.00%)" in render_text(report)
