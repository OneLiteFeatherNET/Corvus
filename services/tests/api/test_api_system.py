from __future__ import annotations

from api_support import Env

from corvus_services.api.settings import ApiSettings


def test_liveness_does_not_touch_the_database(env: Env):
    env.parts["db"].healthy = False

    assert env.client.get("/healthz").json() == {"status": "ok"}


def test_readiness_reflects_the_database(env: Env):
    assert env.client.get("/readyz").json() == {"status": "ok", "database": "ok"}

    env.parts["db"].healthy = False
    resp = env.client.get("/readyz")

    assert resp.status_code == 503
    assert resp.json()["status"] == "unavailable"


def test_metrics_expose_request_counters_by_route_template(env: Env, token):
    env.client.get("/api/v1/pools", headers=env.headers(token(["data_scientist"])))
    env.client.get(f"/api/v1/pools/{'0' * 8}-0000-0000-0000-{'0' * 12}", headers={})

    text = env.client.get("/metrics").text

    assert 'corvus_api_requests_total{method="GET",route="/api/v1/pools",status="200"} 1.0' in text
    assert 'route="/api/v1/pools/{pool_id}"' in text  # template, never the concrete id
    assert "corvus_api_request_seconds_bucket" in text


def test_unknown_paths_are_problem_details(env: Env):
    resp = env.client.get("/api/v1/nope")

    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resp.json()["status"] == 404


def test_problem_details_follow_rfc_9457(env: Env):
    resp = env.client.get("/api/v1/models")

    body = resp.json()
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert {"type", "title", "status", "detail", "instance"} <= set(body)
    assert body["instance"] == "/api/v1/models"
    assert body["status"] == resp.status_code == 401


def test_validation_problems_list_locations_but_never_inputs(env: Env, token):
    resp = env.client.post(
        "/api/v1/pools",
        json={"name": "", "filter": {"game_contexts": "PLAYER-DATA"}},
        headers=env.headers(token(["data_scientist"])),
    )

    assert resp.status_code == 422
    body = resp.json()
    assert body["type"].endswith("/validation")
    assert all("input" not in e for e in body["errors"])
    assert "PLAYER-DATA" not in resp.text


def test_malformed_json_is_a_problem_too(env: Env, token):
    resp = env.client.post(
        "/api/v1/pools",
        content=b"{not json",
        headers={**env.headers(token(["data_scientist"])), "Content-Type": "application/json"},
    )

    assert resp.status_code == 422
    assert resp.headers["content-type"].startswith("application/problem+json")


def test_wrong_method_is_a_problem(env: Env):
    resp = env.client.request("PATCH", "/api/v1/pools")

    assert resp.status_code == 405
    assert resp.json()["status"] == 405


def test_openapi_documents_the_versioned_api_and_bearer_security(env: Env):
    doc = env.client.get("/openapi.json").json()

    assert doc["info"]["title"] == "Corvus control plane"
    api_paths = [p for p in doc["paths"] if p.startswith("/api/")]
    assert api_paths
    assert all(p.startswith("/api/v1/") for p in api_paths)
    schemes = doc["components"]["securitySchemes"]
    assert schemes["HTTPBearer"]["scheme"] == "bearer"
    assert "ProxyToken" in schemes
    assert "/healthz" in doc["paths"]
    for path, ops in doc["paths"].items():
        for method, op in ops.items():
            assert op.get("summary"), f"{method.upper()} {path} has no summary"


def test_every_api_operation_declares_its_security(env: Env):
    doc = env.client.get("/openapi.json").json()

    for path, ops in doc["paths"].items():
        if path.startswith("/api/"):
            for method, op in ops.items():
                assert op.get("security"), f"{method.upper()} {path} is not protected"


def test_openapi_schema_names_are_unique_per_model(env: Env):
    doc = env.client.get("/openapi.json").json()

    names = list(doc["components"]["schemas"])
    clashes = [n for n in names if "__" in n]
    assert clashes == [], f"two models share a class name: {clashes}"


def test_defaults_are_safe():
    s = ApiSettings()

    assert s.dev_token is None
    assert s.plugin_token_list() == []
    assert s.job_runner == "local"
    assert s.oidc_algorithms == ["RS256", "ES256"]
    assert s.gate_max_p99_latency_ms == 10.0


def test_plugin_tokens_are_split_and_trimmed():
    s = ApiSettings(plugin_tokens=" a ,b,, c ")

    assert s.plugin_token_list() == ["a", "b", "c"]


def test_symmetric_algorithms_cannot_be_enabled():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ApiSettings(oidc_algorithms=["HS256"])


def test_startup_refuses_to_run_without_any_authentication():
    import pytest
    from fastapi.testclient import TestClient

    from corvus_services.api.app import create_app

    app = create_app(ApiSettings(pg_dsn="postgresql://unused"))

    with pytest.raises(RuntimeError, match="no authentication configured"), TestClient(app):
        pass


def test_startup_warns_when_the_pseudonym_key_is_missing(env: Env, caplog):
    import logging

    from fastapi.testclient import TestClient

    from corvus_services.api.pseudonym import Pseudonymizer

    env.deps.pseudonymizer = Pseudonymizer(None, "k1")

    with caplog.at_level(logging.WARNING, logger="corvus.api"), TestClient(env.client.app):
        pass

    assert "CORVUS_PSEUDONYM_KEY is not set" in caplog.text
