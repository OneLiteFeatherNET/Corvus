"""Builders for the raw-byte tests: tiny synthetic sessions, an encrypted lake and a tokenizer."""

from __future__ import annotations

import pyarrow.dataset as pads

from corvus_ml.raw.keys import MappingDekProvider
from corvus_ml.raw.layout import TokenLayout
from corvus_ml.raw.reader import RawReader, SessionInfo
from corvus_ml.raw.synthetic import SyntheticRaw, SyntheticRawConfig, generate
from corvus_ml.raw.tokenizer import RawTokenizer, TokenizerConfig

SMALL_VOCAB = 600  # reserved tokens (about 300) + 256 bytes + a few merges
SMALL_LAYOUT = TokenLayout(packet_type_max=64)


def tiny_synthetic(**overrides) -> SyntheticRaw:
    cfg = {
        "seed": 1,
        "n_legit": 3,
        "n_cheat": 3,
        "sessions_per_player": 1,
        "seconds_per_session": 25.0,
    } | overrides
    return generate(SyntheticRawConfig(**cfg))


def small_tokenizer(syn: SyntheticRaw, vocab_size: int = SMALL_VOCAB) -> RawTokenizer:
    cfg = TokenizerConfig(vocab_size=vocab_size, layout=SMALL_LAYOUT, min_frequency=2)
    return RawTokenizer.train((p for sp in syn.sessions for p in sp.payloads if p), cfg)


def session_infos(syn: SyntheticRaw, skip_pseudonym: frozenset[bytes] = frozenset()):
    return [
        SessionInfo(sp.session_id, syn.pseudonym[sp.session_id], sp.protocol)
        for sp in syn.sessions
        if syn.pseudonym[sp.session_id] not in skip_pseudonym
    ]


def lake_reader(syn: SyntheticRaw, tmp_path, erased: frozenset[bytes] = frozenset()) -> RawReader:
    """A reader over the encrypted lake of ``syn``; ``erased`` players have lost their DEK."""
    paths = syn.write_lake(tmp_path)
    deks = {p: d for p, d in syn.deks.items() if p not in erased}
    return RawReader(
        pads.dataset(str(paths["events"]), format="parquet"),
        pads.dataset(str(paths["raw"]), format="parquet"),
        MappingDekProvider(deks),
    )


def tiny_source(workdir, *, window=None, packet=None, tokenizer=None, **synthetic):
    """A tokenized synthetic source with small windows (fast model tests)."""
    from corvus_ml.raw.pipeline import synthetic_source
    from corvus_ml.raw.spec import PacketConfig, WindowConfig

    syn = tiny_synthetic(**synthetic)
    return synthetic_source(
        syn,
        workdir,
        tokenizer or TokenizerConfig(vocab_size=SMALL_VOCAB, layout=SMALL_LAYOUT),
        packet or PacketConfig(max_tokens_per_packet=16),
        window
        or WindowConfig(window_seconds=8.0, stride_seconds=8.0, max_packets=24, min_packets=6),
    )
