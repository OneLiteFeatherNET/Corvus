import json

import pytest

from builders import CLIENTBOUND, SERVERBOUND, frame, simple_session, write_capture
from corvus_ml.aggregate import aggregate_stream
from corvus_ml.pilot import EXIT_OK, EXIT_PRIVACY, EXIT_USAGE, main
from corvus_ml.privacy import (
    Denylist,
    PayloadCheck,
    Vocabulary,
    default_vocabulary_path,
)
from corvus_ml.report import build_report

CHAT = 50
SIGN = 43
MOVE = 11
ENTITY_VELOCITY = 105
SPAWN_ENTITY = 120


def row(type_id, name, direction, private, policy):
    return {
        "id": type_id,
        "name": name,
        "direction": direction,
        "state": "play",
        "private": private,
        "payload_policy": policy,
    }


ROWS = [
    row(CHAT, "C_CHAT_MESSAGE", "serverbound", True, "frame_dropped"),
    row(SIGN, "C_UPDATE_SIGN", "serverbound", False, "stripped"),
    row(MOVE, "C_PLAYER_POSITION", "serverbound", False, "default"),
    row(ENTITY_VELOCITY, "S_ENTITY_VELOCITY", "clientbound", False, "default"),
    row(SPAWN_ENTITY, "S_SPAWN_ENTITY", "clientbound", False, "all_only"),
]
VOCABULARY = Vocabulary.from_list(ROWS)


def violations(frames, mode="serverbound"):
    result = aggregate_stream(
        simple_session(frames),
        "t.cvc",
        VOCABULARY.private_denylist(),
        PayloadCheck(VOCABULARY, mode),
    )
    return build_report(result)["privacy"]["violations"]


def test_payload_in_stripped_type_fails():
    assert violations([frame(1, packet_type=SIGN, payload=b"text")]) == {
        "payload_in_stripped_type": 1
    }


def test_stripped_type_without_payload_passes():
    assert violations([frame(1, packet_type=SIGN, payload=b"")]) == {}


def test_stripped_type_payload_fails_even_in_mode_all():
    assert violations([frame(1, packet_type=SIGN, payload=b"text")], "all") == {
        "payload_in_stripped_type": 1
    }


def test_clientbound_payload_outside_allowlist_fails_under_default_policy():
    fr = frame(1, direction=CLIENTBOUND, packet_type=SPAWN_ENTITY, payload=b"abc")

    assert violations([fr]) == {"clientbound_payload_outside_allowlist": 1}


def test_clientbound_unmapped_payload_fails_under_default_policy():
    fr = frame(1, direction=CLIENTBOUND, packet_type=0, wire_id=9, payload=b"abc")

    assert violations([fr]) == {"clientbound_payload_outside_allowlist": 1}


def test_allowlisted_clientbound_motion_payload_passes():
    fr = frame(1, direction=CLIENTBOUND, packet_type=ENTITY_VELOCITY, payload=b"abc")

    assert violations([fr]) == {}


def test_clientbound_payload_outside_allowlist_passes_in_mode_all():
    fr = frame(1, direction=CLIENTBOUND, packet_type=SPAWN_ENTITY, payload=b"abc")

    assert violations([fr], "all") == {}


def test_serverbound_movement_payload_passes():
    assert violations([frame(1, direction=SERVERBOUND, packet_type=MOVE)]) == {}


def test_unmapped_serverbound_payload_fails_under_default_policy():
    assert violations([frame(1, packet_type=0, payload=b"abc")]) == {"payload_outside_policy": 1}


def test_any_payload_fails_in_mode_none():
    assert violations([frame(1, packet_type=MOVE)], "none") == {"payload_recorded_in_mode_none": 1}


def test_private_type_in_vocabulary_fails_the_denylist_check():
    assert violations([frame(1, packet_type=CHAT, payload=b"")]) == {"denylisted_packet_type": 1}


def test_private_denylist_contains_only_private_ids():
    assert VOCABULARY.private_denylist().packet_types == frozenset({CHAT})


def test_duplicate_vocabulary_ids_are_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        Vocabulary.from_list([ROWS[0], ROWS[0]])


def test_committed_vocabulary_marks_chat_messages_private():
    vocabulary = Vocabulary.from_file(default_vocabulary_path())

    assert vocabulary.entries[CHAT].private
    assert CHAT in vocabulary.private_denylist().packet_types


def test_committed_vocabulary_is_the_active_denylist_by_default(tmp_path):
    write_capture(tmp_path / "a.cvc", *simple_session([frame(1, packet_type=CHAT, payload=b"")]))

    assert main(["analyze", str(tmp_path), "--workers", "1"]) == EXIT_PRIVACY


def test_cli_fails_for_sign_payload(tmp_path):
    write_capture(tmp_path / "a.cvc", *simple_session([frame(1, packet_type=SIGN, payload=b"x")]))

    assert main(["analyze", str(tmp_path), "--workers", "1"]) == EXIT_PRIVACY


def test_cli_extra_denylist_file_is_added_to_vocabulary_ids(tmp_path):
    write_capture(tmp_path / "a.cvc", *simple_session([frame(1, packet_type=7)]))
    extra = tmp_path / "extra.json"
    extra.write_text(json.dumps({"packet_types": [7]}))

    code = main(["analyze", str(tmp_path), "--denylist", str(extra), "--workers", "1"])

    assert code == EXIT_PRIVACY


def test_cli_payload_policy_all_accepts_clientbound_payload(tmp_path):
    fr = frame(1, direction=CLIENTBOUND, packet_type=SPAWN_ENTITY, payload=b"abc")
    write_capture(tmp_path / "a.cvc", *simple_session([fr]))
    vocab = tmp_path / "vocab.json"
    vocab.write_text(json.dumps(ROWS))
    argv = ["analyze", str(tmp_path), "--vocabulary", str(vocab), "--workers", "1"]

    assert main([*argv, "--payload-policy", "all"]) == EXIT_OK


def test_cli_default_policy_rejects_clientbound_payload_outside_allowlist(tmp_path):
    fr = frame(1, direction=CLIENTBOUND, packet_type=SPAWN_ENTITY, payload=b"abc")
    write_capture(tmp_path / "a.cvc", *simple_session([fr]))
    vocab = tmp_path / "vocab.json"
    vocab.write_text(json.dumps(ROWS))

    code = main(["analyze", str(tmp_path), "--vocabulary", str(vocab), "--workers", "1"])

    assert code == EXIT_PRIVACY


def test_cli_missing_vocabulary_is_a_usage_error(tmp_path, capsys):
    write_capture(tmp_path / "a.cvc", *simple_session([frame(1)]))

    code = main(["analyze", str(tmp_path), "--vocabulary", str(tmp_path / "nope.json")])

    assert code == EXIT_USAGE
    assert "cannot load vocabulary" in capsys.readouterr().err


def test_denylist_union_combines_types():
    merged = Denylist(frozenset({1})).union(Denylist(frozenset({2})))

    assert merged.packet_types == frozenset({1, 2})
