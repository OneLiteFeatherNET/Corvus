"""Glue: sessions -> tokenizer -> packet table (used by the jobs, the CLI and the synthetic run)."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path

import pyarrow as pa

from corvus_ml.raw.packets import PACKETS_SCHEMA, session_packet_table
from corvus_ml.raw.reader import SessionPackets
from corvus_ml.raw.spec import PacketConfig, WindowConfig
from corvus_ml.raw.tokenizer import RawTokenizer, TokenizerConfig


def iter_payloads(sessions: Iterable[SessionPackets]) -> Iterator[bytes]:
    """Payloads of a (lazily read) session stream; nothing is kept."""
    for sp in sessions:
        for payload in sp.payloads:
            if payload:
                yield payload


def train_tokenizer(sessions: Iterable[SessionPackets], cfg: TokenizerConfig) -> RawTokenizer:
    return RawTokenizer.train(iter_payloads(sessions), cfg)


def tokenize_sessions(
    sessions: Iterable[SessionPackets],
    tok: RawTokenizer,
    packet: PacketConfig,
    contexts: Mapping[bytes, Mapping[int, str]],
) -> Iterator[pa.Table]:
    """One packet table per session (sessions without a known game context yield nothing)."""
    for sp in sessions:
        table = session_packet_table(sp, tok, packet, contexts.get(sp.session_id, {}))
        if table is not None:
            yield table


def synthetic_source(
    syn,
    workdir: Path,
    tokenizer_cfg: TokenizerConfig | None = None,
    packet: PacketConfig | None = None,
    window: WindowConfig | None = None,
):
    """Train a tokenizer on synthetic sessions and tokenize them: a ready ``RawSource``."""
    from corvus_ml.eval.raw_data import RawSource

    tokenizer_cfg = tokenizer_cfg or TokenizerConfig(vocab_size=1024)
    packet = packet or PacketConfig()
    window = window or WindowConfig()
    tok = train_tokenizer(syn.sessions, tokenizer_cfg)
    tok.save(workdir)
    tables = list(tokenize_sessions(syn.sessions, tok, packet, syn.contexts))
    packets = pa.concat_tables(tables) if tables else PACKETS_SCHEMA.empty_table()
    return RawSource(
        packets=packets,
        sessions=syn.sessions_table(),
        labels=syn.labels_table(),
        spec=tok.spec(workdir, packet, window),
        tokenizer_dir=workdir,
        synthetic=True,
    )
