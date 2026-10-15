"""Synthetic raw packets: layouts, protocol differences, cheat variants, determinism."""

import struct

import numpy as np
import pytest

from corvus_ml.raw import synthetic as S
from raw_builders import tiny_synthetic


@pytest.fixture(scope="module")
def syn():
    return tiny_synthetic(n_legit=3, n_cheat=3, protocols=(767, 768, 770), seconds_per_session=30.0)


def frames(sp, ptype):
    return [p for p, t in zip(sp.payloads, sp.packet_type, strict=True) if t == ptype and p]


def test_generation_is_deterministic_for_a_seed():
    a, b = tiny_synthetic(seed=4), tiny_synthetic(seed=4)

    assert [sp.payloads for sp in a.sessions] == [sp.payloads for sp in b.sessions]
    assert tiny_synthetic(seed=5).sessions[0].payloads != a.sessions[0].payloads


def test_movement_payloads_have_the_wire_layout_of_the_vanilla_packets(syn):
    sp = syn.sessions[0]

    for body in frames(sp, S.C_PLAYER_POSITION_AND_ROTATION):
        x, y, z, yaw, pitch, flags = struct.unpack(">dddffB", body)
        assert -90.0 <= pitch <= 90.0
        assert flags & 1 in (0, 1)
    assert all(len(b) == 25 for b in frames(sp, S.C_PLAYER_POSITION))
    assert all(len(b) == 9 for b in frames(sp, S.C_PLAYER_ROTATION))
    assert all(len(b) == 1 for b in frames(sp, S.C_PLAYER_FLYING))


def test_tick_end_frames_exist_only_from_protocol_768_and_never_have_a_payload(syn):
    for sp in syn.sessions:
        ticks = sp.packet_type == S.C_CLIENT_TICK_END
        if sp.protocol >= 768:
            assert ticks.any()
            assert all(sp.payloads[i] is None for i in np.flatnonzero(ticks))
        else:
            assert not ticks.any()


def test_the_wall_bit_of_the_flag_byte_exists_only_from_protocol_768(syn):
    for sp in syn.sessions:
        flags = {
            body[-1] & 2
            for ptype in (
                S.C_PLAYER_POSITION_AND_ROTATION,
                S.C_PLAYER_ROTATION,
                S.C_PLAYER_POSITION,
            )
            for body in frames(sp, ptype)
        }
        if sp.protocol < 768:
            assert flags <= {0}


def test_privacy_stripped_and_unlisted_clientbound_frames_have_no_payload(syn):
    for sp in syn.sessions:
        for i, ptype in enumerate(sp.packet_type):
            if ptype in (S.C_PLUGIN_MESSAGE, S.S_CHUNK_DATA):
                assert sp.payloads[i] is None


def test_aimbot_snaps_its_view_more_often_than_a_legit_player():
    syn = tiny_synthetic(n_legit=2, n_cheat=3, seconds_per_session=60.0)

    def snaps(sp):
        yaws = [struct.unpack(">ffB", b)[0] for b in frames(sp, S.C_PLAYER_ROTATION)]
        deltas = np.abs(np.diff(yaws))
        return float((deltas > 30).mean()) if len(deltas) else 0.0

    by_kind = {"legit": [], "aimbot": []}
    for sp in syn.sessions:
        pseudonym = syn.pseudonym[sp.session_id]
        if syn.kind[pseudonym] == "legit":
            by_kind["legit"].append(snaps(sp))
        elif syn.variant[pseudonym] == "aimbot":
            by_kind["aimbot"].append(snaps(sp))
    assert by_kind["aimbot"], "the first cheater of the synthetic set is an aimbot"
    assert max(by_kind["aimbot"]) >= max(by_kind["legit"])


def test_every_cheat_variant_is_generated():
    syn = tiny_synthetic(n_legit=1, n_cheat=3)

    assert {v for v in syn.variant.values() if v} == set(S.VARIANTS)
