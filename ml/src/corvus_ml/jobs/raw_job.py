"""``build_features`` with ``kind = raw_bytes`` (and the ``train_tokenizer`` step on its own).

Reads the snapshot's ``events`` and ``raw`` files, decrypts the payloads with the players' DEKs
(sessions of erased players have none and are skipped), trains the byte-level BPE tokenizer on a
sample of the payloads (streaming, bounded memory) and writes the tokenized packet table to
``features/<snapshot_id>/raw/``. Windows are cut from it later, by ``evaluate`` / ``train``.

Needs: read access to ``corvus.player_keys`` (``CORVUS_PG_DSN``, ``PGPASSWORD``) and ``CORVUS_KEK``;
see docs/reference/raw-byte-tokenizer.md.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from corvus_ml.features.reader import EventFiles, open_dataset
from corvus_ml.features.spec import SPEC_FILE, FeatureSpec
from corvus_ml.jobs import layout
from corvus_ml.jobs.artifacts import check_needs_rebuild, register_prefix
from corvus_ml.jobs.context import JobContext
from corvus_ml.jobs.lifecycle import JobEnv, JobError, JobOutcome
from corvus_ml.jobs.params import Params
from corvus_ml.raw.layout import DEFAULT_PROTOCOLS, TokenLayout
from corvus_ml.raw.pipeline import tokenize_sessions, train_tokenizer
from corvus_ml.raw.reader import RawReader, ReadStats, SessionInfo
from corvus_ml.raw.spec import TOKENIZER_FILE, PacketConfig, WindowConfig
from corvus_ml.raw.tokenizer import RawTokenizer, TokenizerConfig

log = logging.getLogger("corvus.job.raw")

RAW_DIR = "raw"
TOKENIZERS_ROOT = "tokenizers"
RAW_LAKE_ROOT = "lake/raw"
_TS = pa.timestamp("us", tz="UTC")
RAW_SESSIONS_SCHEMA = pa.schema(
    [
        ("session_id", pa.string()),
        ("pseudonym", pa.string()),
        ("started_at", _TS),
        ("client_protocol", pa.int32()),
    ]
)


def raw_prefix(snapshot_id: str) -> str:
    return f"{layout.features_prefix(snapshot_id)}/{RAW_DIR}"


def tokenizer_prefix(snapshot_id: str, job_id: str) -> str:
    return f"{TOKENIZERS_ROOT}/{snapshot_id}/{job_id}"


def _deks(env: JobEnv):
    if env.deks is not None:
        return env.deks
    from corvus_ml.raw.keys import PgDekProvider

    try:
        return PgDekProvider.from_env(os.environ)
    except ValueError as exc:
        raise JobError(f"raw-byte jobs cannot read the player keys: {exc}") from None


def _raw_files(
    env: JobEnv, snapshot_id: str, manifest: dict[str, Any], sessions: list[dict[str, Any]]
) -> list[str]:
    """Keys of the ``raw`` files: ``raw`` of ``GET /snapshots/{id}/files``.

    Only an API that does not send the field (older version) is served by the fallback: the
    manifest's list, else the lake's date partitions of the sessions' start dates. An empty
    ``raw`` list is an answer (no raw files), not a reason to scan.
    """
    files = env.api.get_snapshot_files(snapshot_id)
    if "raw" in files:
        return [f["key"] for f in files["raw"] or []]
    log.warning(
        "the API lists no raw files (older version): falling back to the manifest / a scan of %s",
        RAW_LAKE_ROOT,
    )
    listed = [f["key"] for f in (manifest.get("files") or {}).get("raw") or []]
    if listed:
        return listed
    dates = {s["started_at"].date().isoformat() for s in sessions if s.get("started_at")}
    keys = [k for k in env.store.list_keys(RAW_LAKE_ROOT) if k.endswith(".parquet")]
    if dates:
        keys = [k for k in keys if any(f"/date={d}/" in k for d in dates)]
    return keys


def _infos(sel) -> list[SessionInfo]:
    out = []
    for s in sel.sessions:
        pseudonym = s["pseudonym"]
        out.append(
            SessionInfo(
                bytes.fromhex(s["session_id"]),
                bytes.fromhex(pseudonym) if pseudonym else None,
                s.get("client_protocol"),
            )
        )
    return sorted(out, key=lambda i: i.session_id)


def _sample(infos: list[SessionInfo], limit: int) -> list[SessionInfo]:
    """Evenly spaced sessions (deterministic) for the tokenizer training."""
    if len(infos) <= limit:
        return infos
    step = len(infos) / limit
    return [infos[int(i * step)] for i in range(limit)]


def _read_params(ctx: JobContext, kind: str) -> dict[str, Any]:
    p = Params(ctx.params)
    p.string("kind")
    protocols = p.numbers("protocols", [float(v) for v in DEFAULT_PROTOCOLS]) or []
    macro = p.raw("macro_window_seconds")
    if macro is not None and (isinstance(macro, bool) or not isinstance(macro, (int, float))):
        raise JobError("macro_window_seconds must be a number")
    out: dict[str, Any] = {
        "strict": p.boolean("strict", False),
        "contexts": p.game_contexts(),
        "group_size": p.integer("group_size", 4, 1, 1000),
        "tokenizer_sessions": p.integer("tokenizer_sessions", 2000, 1, 1_000_000),
        "tokenizer_prefix": p.string("tokenizer_prefix"),
        "tokenizer": TokenizerConfig(
            vocab_size=p.integer("vocab_size", 4096, 1, 32767),
            min_frequency=p.integer("min_frequency", 2, 1, 1_000_000),
            train_bytes_per_payload=p.integer("train_bytes_per_payload", 256, 1, 4096),
            max_train_payloads=p.integer("max_train_payloads", 2_000_000, 1, 10**9),
            max_train_bytes=p.integer("max_train_bytes", 256 * 1024 * 1024, 1, 2**40),
            layout=TokenLayout(
                dt_buckets=p.integer("dt_buckets", 16, 2, 256),
                dt_min_ms=p.number("dt_min_ms", 0.5, 1e-6, 1e9),
                dt_max_ms=p.number("dt_max_ms", 5000.0, 1e-6, 1e9),
                protocols=tuple(int(v) for v in protocols),
                packet_type_max=p.integer("packet_type_max", 255, 1, 4095),
            ),
        ),
        "packet": PacketConfig(
            max_tokens_per_packet=p.integer("max_tokens_per_packet", 48, 8, 512),
            proto_mode=p.string("proto_mode", "first") or "first",
        ),
        "window": WindowConfig(
            window_seconds=p.number("window_seconds", 10.0, 0.01, 86_400.0),
            stride_seconds=p.number("stride_seconds", 5.0, 0.01, 86_400.0),
            max_packets=p.integer("max_packets", 1024, 1, 100_000),
            overflow=p.string("overflow", "recent") or "recent",
            min_packets=p.integer("min_packets", 8, 1, 100_000),
            macro_window_seconds=None if macro is None else float(macro),
        ),
    }
    p.done()
    return out


def _train_or_load_tokenizer(
    ctx: JobContext, env: JobEnv, opts: dict[str, Any], reader: RawReader, infos, out: Path
) -> RawTokenizer:
    prefix = opts["tokenizer_prefix"]
    if prefix:
        for name in (TOKENIZER_FILE, SPEC_FILE):
            (out / name).write_bytes(env.store.get_bytes(f"{prefix.rstrip('/')}/{name}"))
        spec = FeatureSpec.load(out / SPEC_FILE, require_tokenizer=True).raw_bytes
        assert spec is not None
        opts["packet"] = spec.packet  # the model input layout comes from the tokenizer's spec
        log.info("reusing the tokenizer of %s", prefix)
        return RawTokenizer.load(out, spec)
    sample = _sample(infos, opts["tokenizer_sessions"])
    log.info("training the tokenizer on %d of %d sessions", len(sample), len(infos))
    tok = train_tokenizer(reader.iter_sessions(sample, opts["group_size"]), opts["tokenizer"])
    tok.save(out)
    return tok


def _write_tables(sel, out: Path) -> None:
    sessions = [
        {**s, "client_protocol": s.get("client_protocol")} for s in sel.sessions
    ]  # started_at is a datetime already
    pq.write_table(pa.Table.from_pylist(sessions, RAW_SESSIONS_SCHEMA), out / layout.SESSIONS_FILE)
    if sel.labels:
        from corvus_ml.jobs.features_job import LABELS_SCHEMA

        pq.write_table(pa.Table.from_pylist(sel.labels, LABELS_SCHEMA), out / layout.LABELS_FILE)


def build_raw(ctx: JobContext, env: JobEnv, kind: str) -> JobOutcome:
    from corvus_ml.jobs.features_job import Selection

    opts = _read_params(ctx, kind)
    snapshot = env.api.get_snapshot(ctx.snapshot_id)
    check_needs_rebuild(snapshot, strict=opts["strict"])
    manifest = snapshot.get("manifest") or {}
    event_keys = [f["key"] for f in (manifest.get("files") or {}).get("events") or []]
    if not event_keys:
        raise JobError("the snapshot manifest lists no events files")
    allowed = {str(s) for s in manifest["sessions"]} if manifest.get("sessions") else None

    sel = Selection(opts["contexts"])
    for item in env.api.iter_snapshot_sessions(ctx.snapshot_id):
        sel.add(item, allowed)
    infos = _infos(sel)
    if not infos:
        raise JobError("no sessions selected: the snapshot has none in the requested game contexts")
    raw_keys = _raw_files(env, ctx.snapshot_id, manifest, sel.sessions)
    if not raw_keys:
        raise JobError("no raw files for the snapshot (short retention: are they still there?)")
    without_protocol = sum(1 for i in infos if i.protocol is None)
    if without_protocol:
        log.warning(
            "%d sessions have no client_protocol from the API: they get [PROTO_OTHER]",
            without_protocol,
        )

    deks = _deks(env)
    events = open_dataset(
        EventFiles(tuple(env.store.path(k) for k in event_keys), env.store.filesystem)
    )
    raw = open_dataset(EventFiles(tuple(env.store.path(k) for k in raw_keys), env.store.filesystem))
    out = env.scratch / "raw-features"
    out.mkdir(parents=True, exist_ok=True)

    train_reader = RawReader(events, raw, deks)
    tok = _train_or_load_tokenizer(ctx, env, opts, train_reader, infos, out)
    spec = tok.spec(out, opts["packet"], opts["window"])
    FeatureSpec.current(raw_bytes=spec).write(out)
    tok_stats = train_reader.stats

    if kind == "train_tokenizer":
        prefix = tokenizer_prefix(ctx.snapshot_id, ctx.job_id)
        for name in (TOKENIZER_FILE, SPEC_FILE):
            env.store.put_file(f"{prefix}/{name}", out / name)
        register_prefix(ctx, env, "tokenizer", prefix, contains_raw=False)
        result = {
            "tokenizer_prefix": prefix,
            "tokenizer_sha256": spec.tokenizer_sha256,
            "vocab_size": spec.vocab_size,
            "sessions_read": tok_stats.sessions_read,
            "sessions_crypto_shredded": tok_stats.sessions_erased,
        }
        return JobOutcome(f"trained a tokenizer with {spec.vocab_size} tokens", result)

    contexts: dict[bytes, dict[int, str]] = {}
    for sid, segment, game_context in sel.segments:
        contexts.setdefault(sid, {})[segment] = game_context
    reader = RawReader(events, raw, deks)
    packets = 0
    sink = out / "packets"
    sink.mkdir(parents=True, exist_ok=True)
    size = opts["group_size"]
    for index in range(0, len(infos), size):
        sessions = reader.read_group(infos[index : index + size])
        tables = list(tokenize_sessions(sessions, tok, opts["packet"], contexts))
        if not tables:
            continue
        table = pa.concat_tables(tables)
        packets += table.num_rows
        pq.write_table(
            table,
            sink / f"part-{index // size:06d}.parquet",
            compression="zstd",
            compression_level=3,
        )
    if packets == 0:
        stats = reader.stats
        raise JobError(
            "no packets could be tokenized: "
            f"{stats.sessions_erased} sessions without a key (erased), "
            f"{stats.sessions_no_pseudonym} without pseudonym, {stats.sessions_read} read"
        )
    _write_tables(sel, out)

    stats: ReadStats = reader.stats
    prefix = raw_prefix(ctx.snapshot_id)
    result = {
        "features_prefix": prefix,
        "tokenizer_sha256": spec.tokenizer_sha256,
        "vocab_size": spec.vocab_size,
        "sessions": stats.sessions_read,
        "packets": packets,
        "frames": stats.frames,
        "frames_with_payload": stats.frames_with_payload,
        "event_files": len(event_keys),
        "raw_files": len(raw_keys),
        "labels": len(sel.labels),
        "erased_sessions_skipped": sel.erased_skipped,
        "sessions_crypto_shredded": stats.sessions_erased,
        "sessions_without_pseudonym": stats.sessions_no_pseudonym,
        "sessions_without_protocol": without_protocol,
        "decrypt_failures": stats.decrypt_failures,
        "tokenizer_trained_on_sessions": None
        if opts["tokenizer_prefix"]
        else tok_stats.sessions_read,
    }
    env.store.delete_prefix(prefix)
    env.store.upload_dir(prefix, out)
    env.store.put_bytes(
        f"{prefix}/{layout.BUILD_FILE}",
        json.dumps({**result, "job_id": ctx.job_id, "built_at": env.clock().isoformat()}).encode(),
    )
    register_prefix(ctx, env, "raw_features", prefix, contains_raw=True)
    return JobOutcome(f"tokenized {packets} packets of {stats.sessions_read} sessions", result)
