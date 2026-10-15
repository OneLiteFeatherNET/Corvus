import pytest

from builders import frame, segment_start, simple_session
from corvus_ml.aggregate import aggregate_stream
from corvus_ml.privacy import Denylist, identifier_kind
from corvus_ml.report import build_report

CHAT_TYPE = 42


def run(envelopes, denylist=None):
    denylist = denylist or Denylist()
    return build_report(aggregate_stream(envelopes, "t.cvc", denylist))["privacy"]


def test_chat_packet_type_on_denylist_fails_the_check():
    privacy = run(
        simple_session([frame(1, packet_type=CHAT_TYPE)]), Denylist(frozenset({CHAT_TYPE}))
    )

    assert privacy["passed"] is False
    assert privacy["violations"] == {"denylisted_packet_type": 1}


def test_denylisted_wire_id_fails_even_when_packet_type_is_unmapped():
    denylist = Denylist.from_dict({"wire_ids": [{"id": 6, "direction": "SERVERBOUND"}]})

    privacy = run(simple_session([frame(1, packet_type=0, wire_id=6)]), denylist)

    assert privacy["violations"] == {"denylisted_wire_id": 1}


def test_wire_rule_for_other_direction_does_not_match():
    denylist = Denylist.from_dict({"wire_ids": [{"id": 6, "direction": "clientbound"}]})

    assert run(simple_session([frame(1, wire_id=6)]), denylist)["passed"]


def test_clean_capture_passes_and_reports_denylist_active():
    privacy = run(simple_session([frame(1)]), Denylist(frozenset({CHAT_TYPE})))

    assert privacy["passed"] is True
    assert privacy["denylist_active"] is True


def test_empty_denylist_is_reported_inactive():
    assert run(simple_session([frame(1)]))["denylist_active"] is False


def test_uuid_in_string_field_fails_without_echoing_the_value():
    envelopes = simple_session([frame(1)])
    envelopes[1] = segment_start(brand="123e4567-e89b-12d3-a456-426614174000")

    privacy = run(envelopes)

    assert privacy["violations"] == {"uuid_in_string_field": 1}
    assert all("123e4567" not in s for s in privacy["samples"])


def test_ip_address_in_string_field_fails():
    envelopes = simple_session([frame(1)])
    envelopes[1] = segment_start(context="203.0.113.7")

    assert "ip_in_string_field" in run(envelopes)["violations"]


@pytest.mark.parametrize(
    ("value", "kind"),
    [
        ("123e4567-e89b-12d3-a456-426614174000", "uuid"),
        ("123e4567e89b12d3a456426614174000", "uuid"),
        ("10.0.0.1", "ip"),
        ("player at 192.168.1.20:25565", "ip"),
        ("2001:db8::1", "ip"),
        ("vanilla", None),
        ("0.1.0-SNAPSHOT", None),
        ("256.1.1.1", None),
        ("lobby-2", None),
    ],
)
def test_identifier_kind(value, kind):
    assert identifier_kind(value) == kind
