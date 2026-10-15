from __future__ import annotations

from uuid import UUID

import pytest
from api_support import Env

from corvus_services.api.ports import SnapshotMaterial
from corvus_services.api.registry import (
    Stage,
    evaluate_gate,
    is_allowed_transition,
)

# --- stage machine --------------------------------------------------------------------------------

FORWARD = [
    (Stage.REGISTERED, Stage.SHADOW),
    (Stage.SHADOW, Stage.CANARY),
    (Stage.CANARY, Stage.ENFORCE),
]


@pytest.mark.parametrize(("a", "b"), FORWARD)
def test_the_forward_path_is_allowed(a, b):
    assert is_allowed_transition(a, b)


@pytest.mark.parametrize("stage", [s for s in Stage if s != Stage.ARCHIVED])
def test_any_live_stage_can_be_archived(stage):
    assert is_allowed_transition(stage, Stage.ARCHIVED)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        (Stage.REGISTERED, Stage.CANARY),  # no skipping
        (Stage.REGISTERED, Stage.ENFORCE),
        (Stage.SHADOW, Stage.ENFORCE),
        (Stage.SHADOW, Stage.REGISTERED),  # no going back
        (Stage.CANARY, Stage.SHADOW),
        (Stage.ENFORCE, Stage.CANARY),
        (Stage.ENFORCE, Stage.ENFORCE),
        (Stage.ARCHIVED, Stage.REGISTERED),  # archived is final
        (Stage.ARCHIVED, Stage.ARCHIVED),
    ],
)
def test_everything_else_is_refused(a, b):
    assert not is_allowed_transition(a, b)


# --- gate (pure) ----------------------------------------------------------------------------------


def _model(**metrics):
    from api_fakes import FIXED_NOW

    from corvus_services.api.ports import ModelRecord

    return ModelRecord(
        UUID(int=metrics.pop("n", 1)), "m", "v", "gbdt", "bedwars", "movement", UUID(int=100),
        dict(metrics), "k", "canary", "u", FIXED_NOW,
    )  # fmt: skip


def test_gate_passes_without_an_incumbent_when_latency_is_fine():
    assert evaluate_gate(_model(p99_latency_ms=9.9), [], 10).passed


def test_gate_requires_latency_strictly_below_the_limit():
    result = evaluate_gate(_model(p99_latency_ms=10.0), [], 10)

    assert not result.passed
    assert "p99_latency_ms" in result.reasons[0]


def test_gate_requires_the_latency_metric():
    result = evaluate_gate(_model(recall_at_fpr_0_1pct=0.9), [], 10)

    assert result.reasons == ["metric p99_latency_ms is missing"]


def test_gate_accepts_equal_recall():
    incumbent = _model(n=2, recall_at_fpr_0_1pct=0.8, p99_latency_ms=5)

    assert evaluate_gate(_model(recall_at_fpr_0_1pct=0.8, p99_latency_ms=5), [incumbent], 10).passed


def test_gate_rejects_lower_recall_and_reports_both_values():
    incumbent = _model(n=2, recall_at_fpr_0_1pct=0.8)
    result = evaluate_gate(_model(recall_at_fpr_0_1pct=0.79, p99_latency_ms=5), [incumbent], 10)

    assert not result.passed
    assert "0.79" in result.reasons[0]
    assert "0.8" in result.reasons[0]


def test_gate_collects_every_reason():
    incumbent = _model(n=2, recall_at_fpr_0_1pct=0.8)
    result = evaluate_gate(_model(recall_at_fpr_0_1pct=0.1, p99_latency_ms=50), [incumbent], 10)

    assert len(result.reasons) == 2


def test_gate_rejects_a_candidate_without_recall_when_there_is_an_incumbent():
    incumbent = _model(n=2, recall_at_fpr_0_1pct=0.8)

    assert not evaluate_gate(_model(p99_latency_ms=5), [incumbent], 10).passed


def test_gate_fails_closed_when_the_incumbent_has_no_recall_metric():
    result = evaluate_gate(
        _model(recall_at_fpr_0_1pct=0.9, p99_latency_ms=5), [_model(n=2, p99_latency_ms=5)], 10
    )

    assert not result.passed


def test_boolean_metrics_are_not_numbers():
    assert not evaluate_gate(_model(p99_latency_ms=True), [], 10).passed


# --- HTTP -----------------------------------------------------------------------------------------


@pytest.fixture
def ds(env: Env, token):
    return env.headers(token(["data_scientist"], sub="dana"))


@pytest.fixture
def snapshot_id(env: Env, ds) -> str:
    pool = env.client.post("/api/v1/pools", json={"name": "p"}, headers=ds).json()["id"]
    env.parts["catalog"].material = SnapshotMaterial([], [])
    return env.client.post(f"/api/v1/pools/{pool}/snapshots", headers=ds).json()["id"]


def _register(env: Env, ds, snapshot_id, version="1", game_context="bedwars", **metrics):
    return env.client.post(
        "/api/v1/models",
        json={
            "name": "movement-gbdt",
            "version": version,
            "candidate_type": "gbdt",
            "game_context": game_context,
            "domain": "movement",
            "snapshot_id": snapshot_id,
            "metrics": metrics,
            "onnx_key": f"models/movement/{version}.onnx",
        },
        headers=ds,
    )


def _stage(env: Env, ds, model_id, stage, reason=""):
    return env.client.post(
        f"/api/v1/models/{model_id}/stage", json={"stage": stage, "reason": reason}, headers=ds
    )


def _promote_to_canary(env, ds, model_id):
    assert _stage(env, ds, model_id, "shadow").status_code == 200
    assert _stage(env, ds, model_id, "canary").status_code == 200


def test_new_models_start_registered(env: Env, ds, snapshot_id):
    resp = _register(env, ds, snapshot_id, recall_at_fpr_0_1pct=0.9)

    assert resp.status_code == 201
    body = resp.json()
    assert (body["stage"], body["created_by"]) == ("registered", "dana")
    assert body["metrics"] == {"recall_at_fpr_0_1pct": 0.9}


def test_model_name_and_version_are_unique(env: Env, ds, snapshot_id):
    _register(env, ds, snapshot_id)

    assert _register(env, ds, snapshot_id).status_code == 409


def test_model_needs_an_existing_snapshot(env: Env, ds):
    resp = _register(env, ds, str(UUID(int=12345)))

    assert resp.status_code == 422


def test_viewer_can_read_models_but_not_register_or_promote(env: Env, ds, token, snapshot_id):
    mid = _register(env, ds, snapshot_id).json()["id"]
    viewer = env.headers(token(["viewer"]))

    assert env.client.get(f"/api/v1/models/{mid}", headers=viewer).status_code == 200
    assert env.client.get("/api/v1/models", headers=viewer).status_code == 200
    assert _register(env, viewer, snapshot_id, "2").status_code == 403
    assert _stage(env, viewer, mid, "shadow").status_code == 403


def test_forward_transitions_are_applied_and_audited(env: Env, ds, snapshot_id):
    mid = _register(env, ds, snapshot_id).json()["id"]

    env.clock.advance(1)
    assert _stage(env, ds, mid, "shadow", "start shadowing").json()["stage"] == "shadow"
    env.clock.advance(1)
    _stage(env, ds, mid, "canary")

    trail = env.client.get(f"/api/v1/models/{mid}/transitions", headers=ds).json()
    assert [(t["from_stage"], t["to_stage"]) for t in trail] == [
        ("registered", "shadow"),
        ("shadow", "canary"),
    ]
    assert trail[0]["actor"] == "dana"
    assert trail[0]["reason"] == "start shadowing"
    assert trail[0]["gate"] is None


def test_skipping_a_stage_is_a_conflict(env: Env, ds, snapshot_id):
    mid = _register(env, ds, snapshot_id).json()["id"]

    resp = _stage(env, ds, mid, "canary")

    assert resp.status_code == 409
    assert "not allowed" in resp.json()["detail"]
    assert env.client.get(f"/api/v1/models/{mid}", headers=ds).json()["stage"] == "registered"


def test_unknown_stage_is_a_validation_error(env: Env, ds, snapshot_id):
    mid = _register(env, ds, snapshot_id).json()["id"]

    assert _stage(env, ds, mid, "production").status_code == 422


def test_archiving_works_from_any_stage_and_is_final(env: Env, ds, snapshot_id):
    mid = _register(env, ds, snapshot_id).json()["id"]

    assert _stage(env, ds, mid, "archived").json()["stage"] == "archived"
    assert _stage(env, ds, mid, "shadow").status_code == 409


def test_unknown_model_is_404(env: Env, ds):
    unknown = UUID(int=404)

    assert _stage(env, ds, unknown, "shadow").status_code == 404
    assert env.client.get(f"/api/v1/models/{unknown}", headers=ds).status_code == 404
    assert env.client.get(f"/api/v1/models/{unknown}/transitions", headers=ds).status_code == 404


def test_first_model_reaches_enforce_with_good_latency(env: Env, ds, snapshot_id):
    mid = _register(env, ds, snapshot_id, p99_latency_ms=4.2).json()["id"]
    _promote_to_canary(env, ds, mid)

    resp = _stage(env, ds, mid, "enforce", "go live")

    assert resp.status_code == 200
    gate = env.client.get(f"/api/v1/models/{mid}/transitions", headers=ds).json()[-1]["gate"]
    assert gate["passed"] is True
    assert gate["p99_latency_ms"] == 4.2


def test_slow_model_is_refused_with_reasons(env: Env, ds, snapshot_id):
    mid = _register(env, ds, snapshot_id, p99_latency_ms=12.5).json()["id"]
    _promote_to_canary(env, ds, mid)

    resp = _stage(env, ds, mid, "enforce")

    assert resp.status_code == 409
    body = resp.json()
    assert body["type"].endswith("transition-rejected")
    assert len(body["reasons"]) == 1
    assert "p99_latency_ms" in body["reasons"][0]
    assert env.client.get(f"/api/v1/models/{mid}", headers=ds).json()["stage"] == "canary"
    trail = env.client.get(f"/api/v1/models/{mid}/transitions", headers=ds).json()
    assert [t["to_stage"] for t in trail] == ["shadow", "canary"], (
        "a refused promotion is not audited"
    )


def test_latency_limit_is_configurable(env: Env, ds, snapshot_id):
    env.deps.settings.gate_max_p99_latency_ms = 20
    mid = _register(env, ds, snapshot_id, p99_latency_ms=12.5).json()["id"]
    _promote_to_canary(env, ds, mid)

    assert _stage(env, ds, mid, "enforce").status_code == 200


def _enforced(env, ds, snapshot_id, version, recall, game_context="bedwars"):
    mid = _register(
        env, ds, snapshot_id, version, game_context,
        recall_at_fpr_0_1pct=recall, p99_latency_ms=3,
    ).json()["id"]  # fmt: skip
    _promote_to_canary(env, ds, mid)
    return mid, _stage(env, ds, mid, "enforce")


def test_better_model_replaces_the_incumbent_which_is_archived_with_audit(
    env: Env, ds, snapshot_id
):
    old, _ = _enforced(env, ds, snapshot_id, "1", 0.70)

    new, resp = _enforced(env, ds, snapshot_id, "2", 0.75)

    assert resp.status_code == 200
    assert env.client.get(f"/api/v1/models/{old}", headers=ds).json()["stage"] == "archived"
    assert env.client.get(f"/api/v1/models/{new}", headers=ds).json()["stage"] == "enforce"
    old_trail = env.client.get(f"/api/v1/models/{old}/transitions", headers=ds).json()
    assert old_trail[-1]["to_stage"] == "archived"
    assert old_trail[-1]["actor"] == "dana"
    assert new in old_trail[-1]["reason"]


def test_worse_model_is_refused_and_the_incumbent_stays(env: Env, ds, snapshot_id):
    old, _ = _enforced(env, ds, snapshot_id, "1", 0.80)

    new, resp = _enforced(env, ds, snapshot_id, "2", 0.79)

    assert resp.status_code == 409
    assert "recall_at_fpr_0_1pct" in resp.json()["reasons"][0]
    assert env.client.get(f"/api/v1/models/{old}", headers=ds).json()["stage"] == "enforce"
    assert env.client.get(f"/api/v1/models/{new}", headers=ds).json()["stage"] == "canary"


def test_equally_good_model_is_accepted(env: Env, ds, snapshot_id):
    _enforced(env, ds, snapshot_id, "1", 0.80)

    assert _enforced(env, ds, snapshot_id, "2", 0.80)[1].status_code == 200


def test_incumbents_of_other_game_contexts_do_not_block(env: Env, ds, snapshot_id):
    _enforced(env, ds, snapshot_id, "1", 0.99, game_context="skywars")

    resp = _enforced(env, ds, snapshot_id, "2", 0.10, game_context="bedwars")[1]

    assert resp.status_code == 200


def test_incumbents_trained_on_another_snapshot_do_not_block(env: Env, ds, snapshot_id):
    pool = env.client.post("/api/v1/pools", json={"name": "q"}, headers=ds).json()["id"]
    other_snapshot = env.client.post(f"/api/v1/pools/{pool}/snapshots", headers=ds).json()["id"]
    _enforced(env, ds, other_snapshot, "1", 0.99)

    resp = _enforced(env, ds, snapshot_id, "2", 0.10)[1]

    assert resp.status_code == 200


def test_models_can_be_filtered_by_stage(env: Env, ds, snapshot_id):
    a = _register(env, ds, snapshot_id, "1").json()["id"]
    _register(env, ds, snapshot_id, "2")
    _stage(env, ds, a, "shadow")

    resp = env.client.get("/api/v1/models?stage=shadow", headers=ds).json()

    assert [m["id"] for m in resp["items"]] == [a]
