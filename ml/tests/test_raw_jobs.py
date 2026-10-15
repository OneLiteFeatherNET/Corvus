"""Raw-byte jobs: build_features (raw_bytes, train_tokenizer), evaluate and train."""

import json

import pyarrow.parquet as pq
import pytest

from corvus_ml.features.spec import SPEC_FILE, FeatureSpec
from corvus_ml.jobs.context import Secrets
from corvus_ml.jobs.features_job import build_features
from corvus_ml.jobs.lifecycle import JobError
from corvus_ml.jobs.model_jobs import evaluate, train
from corvus_ml.raw.keys import MappingDekProvider
from job_fakes import JOB_ID, SNAPSHOT_ID, FakeApi, key_set, make_context, make_env
from raw_builders import tiny_synthetic

RAW = f"features/{SNAPSHOT_ID}/raw"
EVENTS_KEY = "lake/events/date=2026-01-01/part-0.parquet"
RAW_KEY = "lake/raw/date=2026-01-01/part-0.parquet"
BUILD = {
    "kind": "raw_bytes",
    "vocab_size": 600,
    "packet_type_max": 64,
    "max_tokens_per_packet": 16,
    "window_seconds": 8.0,
    "stride_seconds": 8.0,
    "max_packets": 24,
    "min_packets": 6,
    "group_size": 2,
}
FAST_BENCH = {"warmup": 1, "iters": 3, "batch": 2, "isolated": False, "quantize": False}
MODEL = {
    "epochs": 2, "batch_size": 16, "lr": 0.003, "d_model": 32, "heads": 2, "layers": 1,
    "local_dim": 16, "local_heads": 2,
}  # fmt: skip


@pytest.fixture(scope="module")
def syn():
    return tiny_synthetic(n_legit=8, n_cheat=6, n_unlabelled=2)


def api_sessions(syn, *, protocol=True, erased=()):
    out = []
    for sp in syn.sessions:
        pseudonym = syn.pseudonym[sp.session_id]
        kind = syn.kind[pseudonym]
        item = {
            "session_id": sp.session_id.hex(),
            "pseudonym": pseudonym.hex(),
            "started_at": syn.started_at[sp.session_id].isoformat(),
            "segments": [{"segment": 0, "game_context": "pvp"}],
            "labels": [] if kind == "none" else [{"label": kind, "source": "synthetic"}],
        }
        if protocol:
            item["client_protocol"] = sp.protocol
        if pseudonym in erased:
            item["erased"] = True
        out.append(item)
    return out


def lake(tmp_path, syn, *, protocol=True, shredded=(), flagged=(), manifest_raw=False):
    """A bucket with the encrypted lake of ``syn``. ``shredded`` players lost their DEK,
    ``flagged`` ones are marked erased by the API."""
    staged = syn.write_lake(tmp_path / "stage")
    files = {"events": [{"id": 1, "key": EVENTS_KEY}]}
    if manifest_raw:
        files["raw"] = [{"id": 2, "key": RAW_KEY}]
    api = FakeApi(
        snapshot={
            "id": SNAPSHOT_ID,
            "manifest": {"files": files, "sessions": [sp.session_id.hex() for sp in syn.sessions]},
        },
        sessions=api_sessions(syn, protocol=protocol, erased=flagged),
    )
    env = make_env(tmp_path, api)
    env.store.put_file(EVENTS_KEY, staged["events"])
    env.store.put_file(RAW_KEY, staged["raw"])
    env.deks = MappingDekProvider({p: d for p, d in syn.deks.items() if p not in shredded})
    return api, env


@pytest.fixture(scope="module")
def built(syn, tmp_path_factory):
    api, env = lake(tmp_path_factory.mktemp("built"), syn)
    outcome = build_features(make_context(**BUILD), env)
    return env, outcome


def test_raw_build_writes_the_tokenizer_spec_packets_and_tables(built):
    env, outcome = built

    keys = key_set(env.store, RAW)
    assert {
        f"{RAW}/{n}"
        for n in ("tokenizer.json", SPEC_FILE, "sessions.parquet", "labels.parquet", "build.json")
    } <= keys
    assert any(k.startswith(f"{RAW}/packets/part-") for k in keys)
    assert outcome.result["features_prefix"] == RAW
    assert outcome.result["packets"] > 0
    assert outcome.result["sessions_crypto_shredded"] == 0


def test_raw_build_leaves_the_aggregate_feature_prefix_alone(built):
    env, _ = built

    assert not any(
        k.startswith(f"features/{SNAPSHOT_ID}/windows") for k in key_set(env.store, "features")
    )


def test_raw_build_spec_pins_the_tokenizer_hash(built, tmp_path):
    env, outcome = built
    for name in ("tokenizer.json", SPEC_FILE):
        (tmp_path / name).write_bytes(env.store.get_bytes(f"{RAW}/{name}"))

    spec = FeatureSpec.load(tmp_path, require_tokenizer=True)

    assert spec.raw_bytes.tokenizer_sha256 == outcome.result["tokenizer_sha256"]
    assert spec.raw_bytes.window.window_seconds == 8.0
    assert spec.raw_bytes.packet.max_tokens_per_packet == 16


def test_sessions_table_carries_the_client_protocol(built, tmp_path):
    env, _ = built
    (tmp_path / "s.parquet").write_bytes(env.store.get_bytes(f"{RAW}/sessions.parquet"))

    table = pq.read_table(tmp_path / "s.parquet")

    assert set(table["client_protocol"].to_pylist()) <= {767, 768, 770}
    assert None not in table["client_protocol"].to_pylist()


def test_sessions_without_a_key_are_skipped_silently_and_counted(syn, tmp_path):
    shredded = {syn.pseudonym[syn.sessions[0].session_id]}
    _, env = lake(tmp_path, syn, shredded=shredded)

    outcome = build_features(make_context(**BUILD), env)

    assert outcome.result["sessions_crypto_shredded"] == 1
    assert outcome.result["sessions"] == len(syn.sessions) - 1
    table = pq.read_table(env.store.path(f"{RAW}/packets"))
    assert syn.sessions[0].session_id not in {bytes(s) for s in table["session_id"].to_pylist()}


def test_sessions_the_api_flags_as_erased_are_never_read(syn, tmp_path):
    flagged = {syn.pseudonym[syn.sessions[1].session_id]}
    api, env = lake(tmp_path, syn, flagged=flagged)
    api.honour_exclude_erased = False

    outcome = build_features(make_context(**BUILD), env)

    assert outcome.result["erased_sessions_skipped"] == 1
    assert outcome.result["sessions"] == len(syn.sessions) - 1


def test_missing_client_protocol_is_counted_and_not_fatal(syn, tmp_path):
    _, env = lake(tmp_path, syn, protocol=False)

    outcome = build_features(make_context(**BUILD), env)

    assert outcome.result["sessions_without_protocol"] == len(syn.sessions)


def test_raw_files_listed_in_the_manifest_are_used_without_a_directory_scan(syn, tmp_path):
    _, env = lake(tmp_path, syn, manifest_raw=True)
    env.store.put_bytes("lake/raw/date=2026-01-01/decoy.parquet", b"not a parquet file")

    outcome = build_features(make_context(**BUILD), env)

    assert outcome.result["raw_files"] == 1


def test_a_snapshot_without_raw_files_fails_with_a_clear_message(syn, tmp_path):
    _, env = lake(tmp_path, syn)
    env.store.delete_prefix("lake/raw")

    with pytest.raises(JobError, match="no raw files"):
        build_features(make_context(**BUILD), env)


def test_when_every_player_is_erased_the_build_fails_instead_of_writing_nothing(syn, tmp_path):
    _, env = lake(tmp_path, syn, shredded=set(syn.deks))

    with pytest.raises(JobError, match="no packets"):
        build_features(make_context(**BUILD), env)


def test_without_a_key_source_the_job_names_what_is_missing(syn, tmp_path, monkeypatch):
    _, env = lake(tmp_path, syn)
    env.deks = None
    monkeypatch.delenv("CORVUS_PG_DSN", raising=False)

    with pytest.raises(JobError, match="CORVUS_PG_DSN"):
        build_features(make_context(**BUILD), env)


def test_unknown_kind_and_unknown_raw_params_fail(syn, tmp_path):
    _, env = lake(tmp_path, syn)

    with pytest.raises(JobError, match="kind"):
        build_features(make_context(kind="nope"), env)
    with pytest.raises(ValueError, match="unknown params"):
        build_features(make_context(**BUILD, windwo_seconds=3), env)


def test_train_tokenizer_step_can_be_reused_by_the_build(syn, tmp_path):
    _, env = lake(tmp_path, syn)
    params = {k: v for k, v in BUILD.items() if k != "kind"}

    first = build_features(make_context(kind="train_tokenizer", **params), env)
    prefix = first.result["tokenizer_prefix"]
    assert prefix == f"tokenizers/{SNAPSHOT_ID}/{JOB_ID}"
    assert {f"{prefix}/tokenizer.json", f"{prefix}/{SPEC_FILE}"} <= key_set(env.store, prefix)
    assert not key_set(env.store, RAW), "training the tokenizer alone writes no feature build"

    second = build_features(make_context(**BUILD, tokenizer_prefix=prefix), env)

    assert second.result["tokenizer_sha256"] == first.result["tokenizer_sha256"]
    assert second.result["tokenizer_trained_on_sessions"] is None


def test_the_aggregate_build_still_accepts_kind_aggregate(tmp_path):
    from job_fakes import SNAPSHOT_ID as SNAP
    from lake_builders import Frame, events_table, sid, write_events

    key = "lake/events/date=2026-10-15/part-0.parquet"
    api = FakeApi(
        snapshot={"id": SNAP, "manifest": {"files": {"events": [{"id": 1, "key": key}]}}},
        sessions=[
            {
                "session_id": sid(1).hex(),
                "pseudonym": "01" * 32,
                "started_at": "2026-10-15T10:00:00+00:00",
                "segments": [{"segment": 0, "game_context": "pvp"}],
            }
        ],
    )
    env = make_env(tmp_path, api)
    write_events(
        tmp_path / "bucket" / key, events_table(sid(1), [Frame(i, i * 10.0) for i in range(40)])
    )

    outcome = build_features(
        make_context(kind="aggregate", window=8, overlap=0.0, min_frames=4, workers=1), env
    )

    assert outcome.result["windows"] > 0


# --- evaluate / train ----------------------------------------------------------------------------


QUICK = {
    "candidates": {"raw_hier_classifier": MODEL, "raw_hier_anomaly": MODEL},
    "benchmark": FAST_BENCH,
    "fpr_targets": [0.05],
}


def test_evaluate_needs_the_raw_build(syn, tmp_path):
    _, env = lake(tmp_path, syn)

    with pytest.raises(JobError, match="kind"):
        evaluate(make_context("evaluate", **QUICK), env)


def test_evaluate_runs_raw_candidates_on_the_raw_build(built, tmp_path):
    env, _ = built

    outcome = evaluate(make_context("evaluate", **QUICK), env)

    report = json.loads(env.store.get_bytes(f"reports/{SNAPSHOT_ID}/{JOB_ID}/report.json"))
    assert report["raw_bytes"]["vocab_size"] == 600
    assert set(report["contexts"]["pvp"]["candidates"]) == set(QUICK["candidates"])
    assert outcome.result["contexts"]["pvp"]["chosen"] in QUICK["candidates"]
    assert env.api.models == []


def test_raw_and_other_candidates_do_not_mix_in_one_job(built):
    env, _ = built

    with pytest.raises(JobError, match="own job"):
        evaluate(make_context("evaluate", candidates=["raw_hier_classifier", "cnn1d"]), env)


def test_window_params_override_the_build_window(built):
    env, _ = built
    params = QUICK | {
        "window_seconds": 4.0,
        "stride_seconds": 4.0,
        "candidates": ["raw_hier_anomaly"],
    }

    evaluate(make_context("evaluate", **params | {"candidates": {"raw_hier_anomaly": MODEL}}), env)

    report = json.loads(env.store.get_bytes(f"reports/{SNAPSHOT_ID}/{JOB_ID}/report.json"))
    assert report["raw_bytes"]["window"]["window_seconds"] == 4.0


def test_unknown_evaluate_params_are_refused(built):
    env, _ = built

    with pytest.raises(ValueError, match="unknown params"):
        evaluate(make_context("evaluate", **QUICK | {"window_secondz": 4.0}), env)


def test_train_registers_the_model_with_tokenizer_and_card(built, tmp_path):
    env, build = built
    params = QUICK | {"candidates": {"raw_hier_classifier": MODEL}, "model_name": "raw"}

    outcome = train(make_context("train", **params), env)

    model = outcome.result["models"][0]
    prefix = f"models/raw-pvp/{model['version']}"
    assert key_set(env.store, prefix) >= {
        f"{prefix}/{n}"
        for n in ("model.onnx", SPEC_FILE, "tokenizer.json", "calibration.json", "model_card.json")
    }
    card = json.loads(env.store.get_bytes(f"{prefix}/model_card.json"))
    assert card["input_kind"] == "raw_bytes"
    assert card["tokenizer"]["sha256"] == build.result["tokenizer_sha256"]
    assert card["window"]["window_seconds"] == 8.0
    assert card["candidate_type"] == "raw_hier_classifier_calibrated"
    assert card["output_semantics"]["onnx_output"] == "score"
    registered = env.api.models[-1]
    assert registered["onnx_key"] == f"{prefix}/model.onnx"
    for name in (SPEC_FILE, "tokenizer.json"):
        (tmp_path / name).write_bytes(env.store.get_bytes(f"{prefix}/{name}"))
    FeatureSpec.load(tmp_path, require_tokenizer=True)


def test_tune_param_runs_a_study_and_summarises_it_in_the_result(built):
    env, _ = built
    params = QUICK | {
        "candidates": {"raw_hier_classifier": MODEL},
        "tune": {"trials": 2, "storage": "memory", "tune_windows": False},
    }

    outcome = evaluate(make_context("evaluate", **params), env)

    study = outcome.result["contexts"]["pvp"]["tune"]["raw_hier_classifier"]
    assert study["n_trials"] == 2
    assert "lr" in study["best_params"]
    report = json.loads(env.store.get_bytes(f"reports/{SNAPSHOT_ID}/{JOB_ID}/report.json"))
    assert report["contexts"]["pvp"]["tune"]["raw_hier_classifier"]["trials"]


def test_tune_must_be_a_flag_or_an_object(built):
    env, _ = built

    with pytest.raises(JobError, match="tune"):
        evaluate(make_context("evaluate", **QUICK | {"tune": "yes"}), env)


def test_unknown_tune_key_fails(built):
    env, _ = built

    with pytest.raises(JobError, match="tune keys"):
        evaluate(make_context("evaluate", **QUICK | {"tune": {"trails": 3}}), env)


def test_secrets_scrub_the_key_material_from_job_messages():
    secrets = Secrets.from_env(
        {
            "CORVUS_KEK": "a2V5LW1hdGVyaWFs",
            "CORVUS_PG_DSN": "postgresql://u:pw@db/x",
            "PGPASSWORD": "hunter22",
        }
    )

    text = secrets.scrub("failed: a2V5LW1hdGVyaWFs and postgresql://u:pw@db/x with hunter22")

    assert "a2V5LW1hdGVyaWFs" not in text
    assert "hunter22" not in text
    assert "postgresql" not in text
