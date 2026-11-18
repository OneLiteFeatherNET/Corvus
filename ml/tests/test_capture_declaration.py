"""Captures declare their capture policy per session; text and name packets are then expected."""

import json

from builders import (
    batch,
    frame,
    segment_start,
    session_end,
    session_start,
    write_capture,
)
from corvus_ml.aggregate import aggregate_stream
from corvus_ml.pilot import EXIT_OK, EXIT_PRIVACY, main
from corvus_ml.privacy import PayloadCheck, Vocabulary, default_vocabulary_path

VOCABULARY = Vocabulary.from_file(default_vocabulary_path())
BY_NAME = {entry.name: entry.id for entry in VOCABULARY.entries.values()}
CHAT = BY_NAME["C_CHAT_MESSAGE"]
SIGN = BY_NAME["C_UPDATE_SIGN"]
COOKIE = BY_NAME["C_COOKIE_RESPONSE"]
MOVE = BY_NAME["C_PLAYER_POSITION"]


def envelopes(frames, **declaration):
    return [
        session_start(**declaration),
        segment_start(context="pvp"),
        batch(frames),
        session_end(),
    ]


def privacy_of(envs):
    result = aggregate_stream(
        iter(envs), "a.cvc", VOCABULARY.private_denylist(), PayloadCheck(VOCABULARY)
    )
    return result.privacy


DECLARED = {"text_packets_captured": True, "names_kept": True}


def test_legacy_session_without_declaration_still_fails_on_chat():
    privacy = privacy_of(envelopes([frame(1, packet_type=CHAT, payload=b"hi")]))

    assert not privacy.passed
    assert privacy.counts.get("denylisted_packet_type") == 1
    assert privacy.expected == {}


def test_declared_chat_is_expected_not_a_violation():
    privacy = privacy_of(envelopes([frame(1, packet_type=CHAT, payload=b"hi")], **DECLARED))

    assert privacy.passed
    assert privacy.counts == {}
    assert privacy.expected.get("text_packets_captured", 0) >= 1


def test_declared_sign_payload_is_expected_when_names_are_kept():
    privacy = privacy_of(envelopes([frame(1, packet_type=SIGN, payload=b"x")], **DECLARED))

    assert privacy.passed
    assert privacy.expected == {"names_kept": 1}


def test_cookie_payload_stays_a_violation_even_when_names_are_kept():
    privacy = privacy_of(envelopes([frame(1, packet_type=COOKIE, payload=b"x")], **DECLARED))

    assert privacy.counts == {"payload_in_stripped_type": 1}


def test_sign_payload_is_a_violation_when_names_are_redacted():
    privacy = privacy_of(
        envelopes([frame(1, packet_type=SIGN, payload=b"x")], text_packets_captured=True)
    )

    assert privacy.counts == {"payload_in_stripped_type": 1}


def test_chat_is_a_violation_when_text_was_dropped_by_the_plugin():
    privacy = privacy_of(envelopes([frame(1, packet_type=CHAT, payload=b"")], names_kept=True))

    assert privacy.counts == {"denylisted_packet_type": 1}


def test_declaration_does_not_excuse_non_text_packets():
    privacy = privacy_of(envelopes([frame(1, packet_type=MOVE, payload=b"x")], **DECLARED))

    assert privacy.passed
    assert "text_packets_captured" not in privacy.expected


def test_cli_passes_a_declared_capture_with_chat_and_names(tmp_path):
    frames = [frame(1, packet_type=CHAT, payload=b"hi"), frame(2, packet_type=SIGN, payload=b"x")]
    write_capture(tmp_path / "a.cvc", *envelopes(frames, **DECLARED))

    assert main(["analyze", str(tmp_path), "--workers", "1"]) == EXIT_OK


def test_cli_still_fails_an_undeclared_capture_with_chat(tmp_path):
    write_capture(tmp_path / "a.cvc", *envelopes([frame(1, packet_type=CHAT, payload=b"hi")]))

    assert main(["analyze", str(tmp_path), "--workers", "1"]) == EXIT_PRIVACY


def test_expected_counts_are_reported_in_the_json_report(tmp_path):
    frames = [frame(1, packet_type=SIGN, payload=b"x")]
    write_capture(tmp_path / "a.cvc", *envelopes(frames, **DECLARED))
    out = tmp_path / "report.json"

    main(["analyze", str(tmp_path), "--workers", "1", "--json", str(out)])

    assert json.loads(out.read_text())["privacy"]["expected"] == {"names_kept": 1}
