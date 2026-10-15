"""Join of ``raw`` (decrypted) and ``events``: payloads, missing payloads, erased players."""

import json
from pathlib import Path

import numpy as np
import pytest

from corvus_ml.raw import synthetic as syn_mod
from raw_builders import lake_reader, session_infos, tiny_synthetic


@pytest.fixture(scope="module")
def syn():
    return tiny_synthetic()


def test_reader_returns_the_plain_payloads_the_writer_encrypted(syn, tmp_path):
    reader = lake_reader(syn, tmp_path)

    got = {sp.session_id: sp for sp in reader.iter_sessions(session_infos(syn))}

    assert set(got) == {sp.session_id for sp in syn.sessions}
    for expected in syn.sessions:
        actual = got[expected.session_id]
        assert actual.payloads == expected.payloads
        assert np.array_equal(actual.seq, expected.seq)
        assert np.array_equal(actual.packet_type, expected.packet_type)
        assert np.array_equal(actual.direction, expected.direction)


def test_frames_without_a_raw_row_have_no_payload(syn, tmp_path):
    reader = lake_reader(syn, tmp_path)
    sp = next(iter(reader.iter_sessions(session_infos(syn)[:1])))

    tick_end = sp.packet_type == syn_mod.C_CLIENT_TICK_END
    chunk = sp.packet_type == syn_mod.S_CHUNK_DATA
    assert (tick_end | chunk).any(), "the synthetic session has payload-less frames"
    assert all(sp.payloads[i] is None for i in np.flatnonzero(tick_end | chunk))
    assert any(p is not None for p in sp.payloads)


def test_sessions_of_an_erased_player_are_skipped_and_counted(syn, tmp_path):
    erased_player = syn.pseudonym[syn.sessions[0].session_id]
    reader = lake_reader(syn, tmp_path, erased=frozenset({erased_player}))

    got = list(reader.iter_sessions(session_infos(syn)))

    ids = {sp.session_id for sp in got}
    assert syn.sessions[0].session_id not in ids
    assert len(ids) == len(syn.sessions) - 1
    assert reader.stats.sessions_erased == 1
    assert reader.stats.sessions_read == len(syn.sessions) - 1


def test_a_session_without_pseudonym_cannot_be_decrypted(syn, tmp_path):
    reader = lake_reader(syn, tmp_path)
    infos = session_infos(syn)
    from corvus_ml.raw.reader import SessionInfo

    infos[0] = SessionInfo(infos[0].session_id, None, infos[0].protocol)

    got = list(reader.iter_sessions(infos))

    assert len(got) == len(syn.sessions) - 1
    assert reader.stats.sessions_no_pseudonym == 1


def test_a_wrong_key_leaves_the_frames_without_payload_and_counts_failures(syn, tmp_path):
    reader = lake_reader(syn, tmp_path)
    victim = syn.sessions[0].session_id
    reader._deks._deks[syn.pseudonym[victim]] = bytes(32)  # replace the player's DEK

    sp = {s.session_id: s for s in reader.iter_sessions(session_infos(syn))}[victim]

    assert all(p is None for p in sp.payloads)
    assert reader.stats.decrypt_failures > 0


def test_duplicate_rows_from_a_redelivery_are_read_once(syn, tmp_path):
    import pyarrow as pa
    import pyarrow.dataset as pads
    import pyarrow.parquet as pq

    paths = syn.write_lake(tmp_path)
    events = pq.read_table(paths["events"])
    pq.write_table(pa.concat_tables([events, events]), paths["events"])
    from corvus_ml.raw.keys import MappingDekProvider
    from corvus_ml.raw.reader import RawReader

    reader = RawReader(
        pads.dataset(str(paths["events"]), format="parquet"),
        pads.dataset(str(paths["raw"]), format="parquet"),
        MappingDekProvider(syn.deks),
    )

    sp = next(iter(reader.iter_sessions(session_infos(syn)[:1])))

    assert len(sp) == len(syn.sessions[0])


def test_synthetic_packet_ids_exist_in_the_vocabulary():
    vocabulary = json.loads(
        (Path(__file__).parents[2] / "proto/corvus/capture/v1/vocabulary.json").read_text()
    )
    names = {v["id"]: v["name"] for v in vocabulary}
    assert names[syn_mod.C_PLAYER_POSITION_AND_ROTATION] == "C_PLAYER_POSITION_AND_ROTATION"
    assert names[syn_mod.C_PLAYER_POSITION] == "C_PLAYER_POSITION"
    assert names[syn_mod.C_PLAYER_ROTATION] == "C_PLAYER_ROTATION"
    assert names[syn_mod.C_PLAYER_FLYING] == "C_PLAYER_FLYING"
    assert names[syn_mod.C_INTERACT_ENTITY] == "C_INTERACT_ENTITY"
    assert names[syn_mod.C_ANIMATION] == "C_ANIMATION"
    assert names[syn_mod.C_CLIENT_TICK_END] == "C_CLIENT_TICK_END"
    assert names[syn_mod.C_KEEP_ALIVE] == "C_KEEP_ALIVE"
    assert names[syn_mod.C_PLUGIN_MESSAGE] == "C_PLUGIN_MESSAGE"
    assert names[syn_mod.S_ENTITY_RELATIVE_MOVE] == "S_ENTITY_RELATIVE_MOVE"
    assert max(names) <= 255, "packet_type_max (default 255) must cover the vocabulary"
