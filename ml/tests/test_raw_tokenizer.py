"""Tokenizer: lossless bytes, reserved-token layout, packet rows, determinism, spec validation."""

import random

import numpy as np
import pytest

from corvus_ml.features.spec import (
    FEATURE_SPEC_RAW_VERSION,
    FeatureSpec,
    FeatureSpecError,
    FeatureSpecMismatch,
)
from corvus_ml.raw import layout as L
from corvus_ml.raw.packets import payload_cap, session_packet_table
from corvus_ml.raw.reader import SessionPackets
from corvus_ml.raw.spec import PacketConfig, WindowConfig
from corvus_ml.raw.tokenizer import (
    RawTokenizer,
    TokenizerConfig,
    bounded_payloads,
    bytes_to_text,
    text_to_bytes,
)
from corvus_ml.raw.windows import assemble_rows, streams_from_table
from raw_builders import SMALL_VOCAB, small_tokenizer, tiny_synthetic


@pytest.fixture(scope="module")
def syn():
    return tiny_synthetic()


@pytest.fixture(scope="module")
def tok(syn):
    return small_tokenizer(syn)


def test_every_byte_value_survives_the_text_mapping():
    data = bytes(range(256))
    assert text_to_bytes(bytes_to_text(data)) == data


def test_arbitrary_bytes_round_trip_through_encode_and_decode(tok):
    rng = random.Random(5)
    samples = [rng.randbytes(n) for n in (1, 2, 17, 255, 2048)]
    samples += [bytes(range(256)), b"\x00" * 64, b"", b"[PAD][SEP][MASK]\x00[PKT_1]"]

    for data in samples:
        assert tok.decode(tok.encode(data)) == data, f"{data[:16]!r}"


def test_payload_bytes_never_produce_reserved_tokens(tok):
    ids = tok.encode(b"[PAD][SEP][CLS][TRUNC][DT_3]")

    assert min(ids) >= tok.layout.n_special, "literal token names inside a payload stay bytes"


def test_reserved_tokens_have_the_ids_the_layout_promises(tok):
    layout = tok.layout
    names = layout.special_tokens()

    assert names[:5] == ["[PAD]", "[MASK]", "[CLS]", "[SEP]", "[UNK]"]
    assert (L.PAD_ID, L.MASK_ID, L.CLS_ID, L.SEP_ID, L.UNK_ID) == (0, 1, 2, 3, 4)
    assert tok._tok.token_to_id("[TRUNC]") == L.TRUNC_ID
    assert tok._tok.token_to_id("[NOPAYLOAD]") == L.NOPAYLOAD_ID
    assert tok._tok.token_to_id("[DIR_SB]") == L.DIR_SB_ID
    assert tok._tok.token_to_id("[DIR_CB]") == L.DIR_CB_ID
    assert tok._tok.token_to_id(f"[PKT_{27}]") == layout.pkt_id(27)
    assert tok._tok.token_to_id("[PROTO_768]") == layout.proto_id(768)
    assert tok._tok.token_to_id("[DT_0]") == layout.dt_base
    assert layout.pkt_id(0) == L.PKT_OTHER_ID
    assert layout.pkt_id(layout.packet_type_max + 1) == L.PKT_OTHER_ID
    assert layout.proto_id(999) == L.PROTO_OTHER_ID
    assert layout.proto_id(None) == L.PROTO_OTHER_ID
    assert tok.vocab_size == SMALL_VOCAB


def test_one_token_per_corvus_packet_type_up_to_the_maximum():
    layout = L.TokenLayout()
    ids = {layout.pkt_id(i) for i in range(1, 229)}  # vocabulary.json holds ids 1..228

    assert len(ids) == 228
    assert layout.n_special == layout.pkt_base + 255


def test_delta_t_buckets_are_log_spaced_and_cover_everything():
    layout = L.TokenLayout(dt_buckets=8, dt_min_ms=1.0, dt_max_ms=1000.0)
    edges = layout.dt_edges_ms()

    assert len(edges) == 7
    assert edges[0] == pytest.approx(1.0)
    assert edges[-1] == pytest.approx(1000.0)
    ratios = [b / a for a, b in zip(edges, edges[1:], strict=False)]
    assert ratios == pytest.approx([ratios[0]] * 6)
    assert layout.dt_id(0.0) == layout.dt_base
    assert layout.dt_id(1e9) == layout.dt_base + 7
    assert layout.dt_id(None) == L.DT_NONE_ID


def test_training_is_deterministic(syn, tmp_path):
    a, b = small_tokenizer(syn), small_tokenizer(syn)
    a.save(tmp_path / "a")
    b.save(tmp_path / "b")

    assert (tmp_path / "a/tokenizer.json").read_bytes() == (
        tmp_path / "b/tokenizer.json"
    ).read_bytes()


def test_training_learns_merges_for_repeated_structure(tok):
    flags_and_zeros = b"\x00" * 24

    assert len(tok.encode(flags_and_zeros)) < 24


def test_training_data_is_bounded_by_payload_count_bytes_and_prefix_length():
    payloads = [bytes([i]) * 100 for i in range(50)]
    cfg = TokenizerConfig(
        vocab_size=700, train_bytes_per_payload=10, max_train_payloads=7, max_train_bytes=10_000
    )
    texts = list(bounded_payloads(iter(payloads), cfg))
    assert len(texts) == 7
    assert all(len(t) == 10 for t in texts)

    by_bytes = list(
        bounded_payloads(
            iter(payloads),
            TokenizerConfig(vocab_size=700, train_bytes_per_payload=10, max_train_bytes=35),
        )
    )
    assert len(by_bytes) == 4


def test_vocabulary_must_leave_room_for_the_byte_alphabet():
    with pytest.raises(ValueError, match="vocab_size"):
        TokenizerConfig(vocab_size=100)


def _frames(payloads, original_sizes, *, offsets, segments=None, types=None, direction=1):
    n = len(payloads)
    return SessionPackets(
        session_id=bytes(16),
        protocol=768,
        segment=np.array(segments or [0] * n, dtype=np.uint32),
        seq=np.arange(n, dtype=np.uint64),
        offset_nanos=np.array(offsets, dtype=np.uint64),
        direction=np.full(n, direction, dtype=np.uint8),
        state=np.full(n, 5, dtype=np.uint8),
        packet_type=np.array(types or [12] * n, dtype=np.uint32),
        payload_original_size=np.array(original_sizes, dtype=np.uint32),
        payloads=list(payloads),
    )


def _rows(tok, sp, packet=None, proto=None):
    packet = packet or PacketConfig()
    table = session_packet_table(sp, tok, packet, {0: "pvp", 1: "pvp"})
    stream = next(streams_from_table(table, packet))
    return stream, stream.rows(tok.layout, packet.max_tokens_per_packet, proto, None)


def test_packet_row_layout_with_payload(tok):
    sp = _frames([b"\x01\x02\x03\x04"], [4], offsets=[0])
    stream, rows = _rows(tok, sp)
    layout = tok.layout
    row = rows[0].tolist()

    assert row[0] == L.DIR_SB_ID
    assert row[1] == layout.pkt_id(12)
    assert row[2] == L.DT_NONE_ID
    payload = row[3 : row[3:].index(L.SEP_ID) + 3]
    assert tok.decode(payload) == b"\x01\x02\x03\x04"
    assert L.TRUNC_ID not in row
    assert L.NOPAYLOAD_ID not in row
    assert row[row.index(L.SEP_ID) + 1 :] == [0] * (48 - row.index(L.SEP_ID) - 1)


def test_frames_without_payload_get_nopayload_and_keep_their_header(tok):
    sp = _frames([None, b""], [0, 0], offsets=[0, 5_000_000], types=[27, 27])
    _, rows = _rows(tok, sp)
    layout = tok.layout

    for row in rows.tolist():
        assert row[:3] == [L.DIR_SB_ID, layout.pkt_id(27), row[2]]
        assert row[3:5] == [L.NOPAYLOAD_ID, L.SEP_ID]
    assert rows[1, 2] == layout.dt_id(5.0)


def test_payload_cut_by_the_ring_buffer_is_marked_trunc(tok):
    # payload_original_size says 3000 bytes existed, only 40 were recorded
    sp = _frames([bytes(range(40))], [3000], offsets=[0])
    _, rows = _rows(tok, sp)
    row = rows[0].tolist()
    sep = row.index(L.SEP_ID)

    assert row[sep - 1] == L.TRUNC_ID


def test_payload_longer_than_the_token_cap_is_cut_and_marked_trunc(tok):
    packet = PacketConfig(max_tokens_per_packet=16)
    data = random.Random(3).randbytes(200)
    sp = _frames([data], [200], offsets=[0])
    _, rows = _rows(tok, sp, packet)
    row = rows[0].tolist()

    assert len(row) == 16
    assert row[row.index(L.SEP_ID) - 1] == L.TRUNC_ID
    assert payload_cap(packet) == 11


def test_protocol_token_goes_after_the_header_and_shifts_the_payload(tok):
    sp = _frames([b"\x01\x02\x03\x04"], [4], offsets=[0])
    _, plain = _rows(tok, sp)
    _, with_proto = _rows(tok, sp, proto=tok.layout.proto_id(768))

    assert with_proto[0, 3] == tok.layout.proto_id(768)
    assert plain[0, 3] != tok.layout.proto_id(768)
    assert tok.decode(with_proto[0, 4 : list(with_proto[0]).index(L.SEP_ID)].tolist()) == bytes(
        [1, 2, 3, 4]
    )


def test_delta_t_restarts_with_each_segment(tok):
    sp = _frames(
        [b"\x01", b"\x01", b"\x01"], [1, 1, 1],
        offsets=[0, 10_000_000, 20_000_000], segments=[0, 0, 1],
    )  # fmt: skip
    _, _ = _rows(tok, sp)
    table = session_packet_table(sp, tok, PacketConfig(), {0: "pvp", 1: "pvp"})

    dt = table["dt_id"].to_pylist()
    assert dt[0] == L.DT_NONE_ID
    assert dt[1] == tok.layout.dt_id(10.0)
    assert dt[2] == L.DT_NONE_ID


def test_frames_in_segments_without_a_game_context_are_dropped(tok):
    sp = _frames([b"\x01", b"\x01"], [1, 1], offsets=[0, 1_000_000], segments=[0, 3])

    table = session_packet_table(sp, tok, PacketConfig(), {0: "pvp"})

    assert table.num_rows == 1


def test_assemble_rows_marks_unknown_packet_types_and_directions():
    layout = L.TokenLayout(packet_type_max=10)
    rows = assemble_rows(
        layout, 16, np.array([2, 9]), np.array([3, 500]), np.array([20, 20]),
        np.zeros((2, 4), dtype=np.int16), np.zeros(2, dtype=np.int64),
        np.zeros(2, dtype=bool), np.ones(2, dtype=bool),
    )  # fmt: skip

    assert rows[0, 0] == L.DIR_CB_ID
    assert rows[1, 0] == L.DIR_OTHER_ID
    assert rows[1, 1] == L.PKT_OTHER_ID


# --- feature spec (kind raw_bytes) -------------------------------------------------------------


@pytest.fixture
def saved(tok, tmp_path):
    tok.save(tmp_path)
    spec = tok.spec(tmp_path, PacketConfig(), WindowConfig())
    FeatureSpec.current(raw_bytes=spec).write(tmp_path)
    return tmp_path, spec


def test_raw_bytes_spec_round_trips_and_is_version_2(saved):
    directory, spec = saved

    loaded = FeatureSpec.load(directory, require_tokenizer=True)

    assert loaded.raw_bytes == spec
    assert loaded.version == FEATURE_SPEC_RAW_VERSION == 2


def test_changed_tokenizer_file_is_refused_on_load(saved):
    directory, _ = saved
    (directory / "tokenizer.json").write_text((directory / "tokenizer.json").read_text() + " ")

    with pytest.raises(FeatureSpecMismatch, match="tokenizer hash"):
        FeatureSpec.load(directory)


def test_missing_tokenizer_file_is_an_error_when_required(saved):
    directory, _ = saved
    (directory / "tokenizer.json").unlink()

    FeatureSpec.load(directory)  # tolerated unless the caller needs the tokenizer
    with pytest.raises(FeatureSpecError, match="missing"):
        FeatureSpec.load(directory, require_tokenizer=True)


def test_tokenizer_loader_checks_hash_and_layout(saved, tok):
    directory, spec = saved

    assert RawTokenizer.load(directory, spec).vocab_size == spec.vocab_size

    other = tiny_synthetic(seed=9)
    small_tokenizer(other, vocab_size=SMALL_VOCAB + 10).save(directory)
    with pytest.raises(FeatureSpecMismatch, match="hash"):
        RawTokenizer.load(directory, spec)


def test_loader_rejects_a_tokenizer_that_does_not_follow_the_layout(saved):
    directory, spec = saved
    from dataclasses import replace

    wrong = replace(spec, layout=L.TokenLayout(packet_type_max=63))

    with pytest.raises(FeatureSpecMismatch):
        RawTokenizer.load(directory, replace(wrong))


def test_a_scorer_without_raw_support_refuses_the_spec(saved):
    directory, _ = saved
    raw = FeatureSpec.read(directory)
    aggregate_only = FeatureSpec.current(raw.window, raw.seq_len)

    with pytest.raises(FeatureSpecMismatch, match="version"):
        raw.require_compatible(aggregate_only)


def test_window_differences_are_ignored_only_when_asked(saved):
    directory, spec = saved
    from dataclasses import replace

    other = replace(spec, window=WindowConfig(window_seconds=20.0, stride_seconds=10.0))
    mine = FeatureSpec.current(raw_bytes=spec)
    theirs = FeatureSpec.current(raw_bytes=other)

    assert mine.differences(theirs, window=False) == []
    assert any("raw_bytes.window" in d for d in mine.differences(theirs))
