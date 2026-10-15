"""``build_features``: snapshot events and API sessions to windows, tokens and tables."""

import json

import pyarrow.dataset as ds
import pyarrow.parquet as pq
import pytest

from corvus_ml.features.spec import SPEC_FILE, FeatureSpec
from corvus_ml.jobs.features_job import build_features
from corvus_ml.jobs.lifecycle import JobError, run_job
from job_fakes import JOB_ID, SNAPSHOT_ID, FakeApi, key_set, make_context, make_env
from lake_builders import Frame, events_table, sid, write_events

PREFIX = f"features/{SNAPSHOT_ID}"
EVENTS_KEY = "lake/events/date=2026-10-15/part-0.parquet"
SMALL = {"window": 8, "overlap": 0.0, "min_frames": 4, "workers": 1}


def frames(n=40, segment=0):
    return [Frame(i, i * 10.0, segment=segment) for i in range(n)]


def session(n, context="pvp", **extra):
    return {
        "session_id": sid(n).hex(),
        "pseudonym": f"{n:02x}" * 32,
        "started_at": "2026-10-15T10:00:00+00:00",
        "segments": [{"segment": 0, "game_context": context}],
        **extra,
    }


def lake(tmp_path, sessions, *, in_manifest=(1, 2, 3), honour=True):
    """Events of sessions 1-4 in one file; the manifest lists ``in_manifest``."""
    api = FakeApi(
        snapshot={
            "id": SNAPSHOT_ID,
            "manifest": {
                "files": {"events": [{"id": 1, "key": EVENTS_KEY}]},
                "sessions": [sid(n).hex() for n in in_manifest],
            },
        },
        sessions=sessions,
        honour_exclude_erased=honour,
    )
    env = make_env(tmp_path, api)
    write_events(
        tmp_path / "bucket" / EVENTS_KEY, *[events_table(sid(n), frames()) for n in (1, 2, 3, 4)]
    )
    return api, env


def window_sessions(env):
    table = ds.dataset(env.store.path(f"{PREFIX}/windows"), format="parquet").to_table()
    return {bytes(s) for s in table["session_id"].to_pylist()}


def test_the_artefact_layout_is_features_snapshot_id(tmp_path):
    api, env = lake(tmp_path, [session(1), session(3, "survival")])

    outcome = build_features(make_context(**SMALL), env)

    keys = key_set(env.store, PREFIX)
    assert f"{PREFIX}/{SPEC_FILE}" in keys
    assert f"{PREFIX}/sessions.parquet" in keys
    assert f"{PREFIX}/build.json" in keys
    assert any(k.startswith(f"{PREFIX}/windows/part-") for k in keys)
    assert any(k.startswith(f"{PREFIX}/window_tokens/part-") for k in keys)
    assert outcome.result["features_prefix"] == PREFIX
    assert outcome.result["windows"] > 0
    assert outcome.result["game_contexts"] == ["pvp", "survival"]


def test_the_feature_spec_records_the_window_parameters(tmp_path):
    _, env = lake(tmp_path, [session(1)])

    build_features(make_context(**SMALL), env)

    spec = FeatureSpec.load(env.scratch / "features" / SPEC_FILE)
    assert (spec.window.window, spec.window.overlap, spec.window.min_frames) == (8, 0.0, 4)


def test_sessions_the_api_flags_as_erased_are_never_read(tmp_path):
    # The fake ignores exclude_erased, so the job's own guard is what protects the player.
    _, env = lake(tmp_path, [session(1), session(2, erased=True)], honour=False)

    outcome = build_features(make_context(**SMALL), env)

    assert window_sessions(env) == {sid(1)}
    assert outcome.result["erased_sessions_skipped"] == 1
    stored = pq.read_table(env.store.path(f"{PREFIX}/sessions.parquet"))
    assert stored["session_id"].to_pylist() == [sid(1).hex()]


def test_erased_players_are_excluded_by_the_api_request(tmp_path):
    api, env = lake(tmp_path, [session(1), session(2, erased=True)])

    build_features(make_context(**SMALL), env)

    assert window_sessions(env) == {sid(1)}
    assert api.session_requests == 1


def test_sessions_outside_the_snapshot_manifest_are_ignored(tmp_path):
    _, env = lake(tmp_path, [session(1), session(4)], in_manifest=(1,))

    outcome = build_features(make_context(**SMALL), env)

    assert window_sessions(env) == {sid(1)}
    assert outcome.result["sessions"] == 1


def test_a_game_context_param_restricts_the_build(tmp_path):
    _, env = lake(tmp_path, [session(1), session(3, "survival")])

    outcome = build_features(make_context(game_context="survival", **SMALL), env)

    assert window_sessions(env) == {sid(3)}
    assert outcome.result["game_contexts"] == ["survival"]


def test_labels_are_stored_with_their_source_and_validity(tmp_path):
    label = {"label": "cheat", "source": "honeypot", "confidence": 0.9, "valid_from": None}
    _, env = lake(tmp_path, [session(1, labels=[label]), session(3, "survival")])

    build_features(make_context(**SMALL), env)

    stored = pq.read_table(env.store.path(f"{PREFIX}/labels.parquet")).to_pylist()
    assert stored == [
        {
            "session_id": sid(1).hex(),
            "label": "cheat",
            "source": "honeypot",
            "confidence": 0.9,
            "valid_from": None,
            "valid_to": None,
        }
    ]


def test_no_labels_file_is_written_without_labels(tmp_path):
    _, env = lake(tmp_path, [session(1)])

    build_features(make_context(**SMALL), env)

    assert f"{PREFIX}/labels.parquet" not in key_set(env.store, PREFIX)


def test_an_earlier_build_is_replaced_completely(tmp_path):
    _, env = lake(tmp_path, [session(1)])
    env.store.put_bytes(f"{PREFIX}/windows/part-000099.parquet", b"stale")

    build_features(make_context(**SMALL), env)

    assert f"{PREFIX}/windows/part-000099.parquet" not in key_set(env.store, PREFIX)


def test_the_build_marker_is_written_last_with_the_job_and_time(tmp_path):
    _, env = lake(tmp_path, [session(1)])

    build_features(make_context(**SMALL), env)

    marker = json.loads(env.store.get_bytes(f"{PREFIX}/build.json"))
    assert marker["job_id"] == make_context().job_id
    assert marker["built_at"].startswith("2026-10-15T12:00")


def test_two_workers_produce_the_same_windows_as_one(tmp_path):
    one_dir, two_dir = tmp_path / "one", tmp_path / "two"
    sessions = [session(1), session(2), session(3, "survival")]
    _, env1 = lake(one_dir, sessions)
    _, env2 = lake(two_dir, sessions)

    build_features(make_context(group_size=1, **{**SMALL, "workers": 1}), env1)
    build_features(make_context(group_size=1, **{**SMALL, "workers": 2}), env2)

    def read(env):
        path = env.store.path(f"{PREFIX}/windows")
        return ds.dataset(path, format="parquet").to_table().sort_by("session_id").to_pylist()

    assert read(env1) == read(env2)


def test_a_snapshot_without_events_files_fails_with_a_clear_message(tmp_path):
    api = FakeApi(snapshot={"manifest": {"files": {"events": []}}}, sessions=[session(1)])

    with pytest.raises(JobError, match="no events files"):
        build_features(make_context(**SMALL), make_env(tmp_path, api))


def test_no_matching_sessions_fail_the_job_and_leave_nothing_behind(tmp_path):
    api, env = lake(tmp_path, [session(1)])

    code = run_job(make_context(game_context="survival", **SMALL), env, build_features)

    assert code == 1
    assert api.statuses == ["running", "failed"]
    assert "no sessions selected" in api.updates[-1]["message"]
    assert key_set(env.store, PREFIX) == set()


def test_unknown_params_are_rejected(tmp_path):
    _, env = lake(tmp_path, [session(1)])

    with pytest.raises(ValueError, match="unknown params"):
        build_features(make_context(windw=8), env)


def test_a_malformed_session_id_from_the_api_fails_the_job(tmp_path):
    bad = {**session(1), "session_id": "xyz"}
    _, env = lake(tmp_path, [bad])

    with pytest.raises(JobError, match="malformed session id"):
        build_features(make_context(**SMALL), env)


def test_the_aggregate_build_registers_windows_and_tokens_without_raw(tmp_path):
    api, env = lake(tmp_path, [session(1)])

    build_features(make_context(**SMALL), env)

    assert [(a["kind"], a["s3_prefix"], a["contains_raw"]) for a in api.artifacts] == [
        ("window_features", f"{PREFIX}/windows/", False),
        ("window_tokens", f"{PREFIX}/window_tokens/", False),
    ]
    assert {a["job_id"] for a in api.artifacts} == {JOB_ID}


def test_a_conflict_on_registration_fails_the_aggregate_build_and_deletes_the_prefix(tmp_path):
    api, env = lake(tmp_path, [session(1)])
    api.artifact_conflict = True

    with pytest.raises(JobError, match="erasure invalidated"):
        build_features(make_context(**SMALL), env)

    assert not key_set(env.store, f"{PREFIX}/windows")


def test_a_snapshot_that_needs_a_rebuild_is_built_by_default(tmp_path):
    api, env = lake(tmp_path, [session(1)])
    api.snapshot["needs_rebuild"] = True

    outcome = build_features(make_context(**SMALL), env)

    assert outcome.result["windows"] > 0


def test_strict_refuses_a_snapshot_that_needs_a_rebuild(tmp_path):
    api, env = lake(tmp_path, [session(1)])
    api.snapshot["needs_rebuild"] = True

    with pytest.raises(JobError, match="needs a rebuild"):
        build_features(make_context(**SMALL, strict=True), env)

    assert not key_set(env.store, PREFIX)
