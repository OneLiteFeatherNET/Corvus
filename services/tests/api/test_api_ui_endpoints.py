"""Endpoints added for the management UI (me, summary, erasures, models, preview, CORS)."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import pytest
from api_fakes import FIXED_NOW, sequential_ids
from api_support import AUDIENCE, ISSUER, Env
from fastapi.testclient import TestClient

from corvus_services.api.app import create_app
from corvus_services.api.deps import Deps
from corvus_services.api.pools import FileShare, RawPreview
from corvus_services.api.ports import (
    ErasureRequest,
    JobRecord,
    LabelRecord,
    SnapshotMaterial,
)
from corvus_services.api.settings import ApiSettings

PSEUDONYM = bytes(range(32))

# --- /me ------------------------------------------------------------------------------------------


def test_me_returns_subject_display_name_and_mapped_roles(env: Env, token):
    headers = env.headers(token(["data_scientist", "ignored"], sub="u-1", name="Dana Scully"))

    body = env.client.get("/api/v1/me", headers=headers).json()

    assert body == {"subject": "u-1", "display_name": "Dana Scully", "roles": ["data_scientist"]}


def test_me_falls_back_to_preferred_username(env: Env, token):
    headers = env.headers(token(["viewer"], preferred_username="dscully"))

    assert env.client.get("/api/v1/me", headers=headers).json()["display_name"] == "dscully"


def test_me_has_no_display_name_when_the_token_carries_none(env: Env, token):
    body = env.client.get("/api/v1/me", headers=env.headers(token(["viewer"]))).json()

    assert body["display_name"] is None


def test_me_applies_the_role_mapping(jwks_source, clock, token):
    from api_fakes import make_repos
    from api_support import make_verifier

    from corvus_services.api.auth import Authenticator

    repos, _ = make_repos()
    verifier = make_verifier(jwks_source, clock, role_mapping={"corvus-ds": "data_scientist"})
    env_deps = _deps(repos, Authenticator(verifier), clock)

    resp = TestClient(create_app(deps=env_deps)).get(
        "/api/v1/me", headers={"Authorization": f"Bearer {token(['corvus-ds'])}"}
    )

    assert resp.json()["roles"] == ["data_scientist"]


def test_me_needs_a_token_but_no_role(env: Env, token):
    assert env.client.get("/api/v1/me").status_code == 401
    assert env.client.get("/api/v1/me", headers=env.headers(token([]))).status_code == 200


def _deps(repos, authenticator, clock) -> Deps:
    from api_fakes import FakeRunner
    from api_support import PSEUDONYM_KEY

    from corvus_services.api.pseudonym import Pseudonymizer

    return Deps(
        settings=ApiSettings(
            pg_dsn="postgresql://unused", oidc_issuer=ISSUER, oidc_audience=AUDIENCE
        ),
        repos=repos,
        authenticator=authenticator,
        runner=FakeRunner(),
        pseudonymizer=Pseudonymizer(PSEUDONYM_KEY, "k1"),
        clock=clock,
        new_id=sequential_ids(),
    )


# --- /summary -------------------------------------------------------------------------------------


def _job(i: int, status: str = "queued") -> JobRecord:
    return JobRecord(
        UUID(int=i), "train", UUID(int=99), {"secret-param": 1}, status, "fake", None, "", "dana",
        FIXED_NOW + timedelta(minutes=i), FIXED_NOW + timedelta(minutes=i),
    )  # fmt: skip


def _erasure(i: int, pending: bool = True) -> ErasureRequest:
    return ErasureRequest(
        UUID(int=i), bytes([i]) * 32, "root", FIXED_NOW + timedelta(minutes=i), "completed",
        True, True, pending,
    )  # fmt: skip


def test_summary_is_readable_by_viewers(env: Env, token):
    resp = env.client.get("/api/v1/summary", headers=env.headers(token(["viewer"])))

    assert resp.status_code == 200


def test_summary_of_an_empty_system_lists_every_bucket_with_zero(env: Env, token):
    body = env.client.get("/api/v1/summary", headers=env.headers(token(["viewer"]))).json()

    assert body["pools"] == body["snapshots"] == body["pending_erasures"] == 0
    assert body["jobs_by_status"] == {"queued": 0, "running": 0, "succeeded": 0, "failed": 0}
    assert body["models_by_stage"] == dict.fromkeys(
        ("registered", "shadow", "canary", "enforce", "archived"), 0
    )
    assert body["labels_by_label"] == {"legit": 0, "cheat": 0}
    assert (body["recent_jobs"], body["recent_erasures"]) == ([], [])


def test_summary_counts_what_exists(env: Env, token):
    ds = env.headers(token(["data_scientist"], sub="dana"))
    env.client.post("/api/v1/pools", json={"name": "a"}, headers=ds)
    env.client.post("/api/v1/pools", json={"name": "b"}, headers=ds)
    env.parts["jobs"].rows.update({j.id: j for j in (_job(1, "running"), _job(2), _job(3))})
    env.parts["labels"].rows.append(
        LabelRecord(
            UUID(int=7),
            "pseudonym",
            "ab" * 32,
            "cheat",
            "honeypot",
            1.0,
            None,
            None,
            "",
            "staff",
            FIXED_NOW,
        )  # fmt: skip
    )
    env.parts["erasure"].requests.update({r.id: r for r in (_erasure(1), _erasure(2, False))})

    body = env.client.get("/api/v1/summary", headers=env.headers(token(["viewer"]))).json()

    assert body["pools"] == 2
    assert body["jobs_by_status"] == {"queued": 2, "running": 1, "succeeded": 0, "failed": 0}
    assert body["labels_by_label"] == {"legit": 0, "cheat": 1}
    assert body["pending_erasures"] == 1


def test_summary_lists_only_the_ten_newest_jobs_and_erasures(env: Env, token):
    env.parts["jobs"].rows.update({j.id: j for j in map(_job, range(1, 16))})
    env.parts["erasure"].requests.update({r.id: r for r in map(_erasure, range(1, 16))})

    body = env.client.get("/api/v1/summary", headers=env.headers(token(["viewer"]))).json()

    assert [j["id"] for j in body["recent_jobs"]] == [str(UUID(int=i)) for i in range(15, 5, -1)]
    assert len(body["recent_erasures"]) == 10


def test_summary_shows_the_pseudonym_prefix_only_and_no_job_params(env: Env, token):
    env.parts["jobs"].rows[UUID(int=1)] = _job(1)
    env.parts["erasure"].requests[UUID(int=1)] = _erasure(1)

    resp = env.client.get("/api/v1/summary", headers=env.headers(token(["viewer"])))

    erasure = resp.json()["recent_erasures"][0]
    assert erasure["pseudonym_prefix"] == "01" * 6
    assert "pseudonym" not in erasure
    assert "01" * 32 not in resp.text
    assert "secret-param" not in resp.text


# --- GET /erasure-requests ------------------------------------------------------------------------


@pytest.fixture
def admin(env: Env, token):
    return env.headers(token(["admin"], sub="root"))


def test_erasure_requests_are_listed_newest_first_with_pagination(env: Env, admin):
    env.parts["erasure"].requests.update({r.id: r for r in map(_erasure, range(1, 4))})

    first = env.client.get("/api/v1/erasure-requests", headers=admin).json()
    second = env.client.get(
        "/api/v1/erasure-requests", params={"cursor": first["next_cursor"]}, headers=admin
    ).json()

    assert [i["id"] for i in first["items"]] == [str(UUID(int=3)), str(UUID(int=2))]
    assert [i["id"] for i in second["items"]] == [str(UUID(int=1))]
    assert second["next_cursor"] is None
    assert first["items"][0]["pseudonym"] == "03" * 32


def test_erasure_requests_filter_by_status(env: Env, admin):
    env.parts["erasure"].requests[UUID(int=1)] = _erasure(1)

    completed = env.client.get("/api/v1/erasure-requests?status=completed", headers=admin).json()

    assert len(completed["items"]) == 1
    assert env.client.get("/api/v1/erasure-requests?status=bogus", headers=admin).status_code == 422


@pytest.mark.parametrize("role", ["viewer", "staff", "data_scientist"])
def test_erasure_requests_are_admin_only(env: Env, token, role):
    resp = env.client.get("/api/v1/erasure-requests", headers=env.headers(token([role])))

    assert resp.status_code == 403


# --- models: filters and lookup by name and version ---------------------------------------------


@pytest.fixture
def ds(env: Env, token):
    return env.headers(token(["data_scientist"], sub="dana"))


@pytest.fixture
def snapshot_id(env: Env, ds) -> str:
    pool = env.client.post("/api/v1/pools", json={"name": "p"}, headers=ds).json()["id"]
    env.parts["catalog"].material = SnapshotMaterial([], [])
    return env.client.post(f"/api/v1/pools/{pool}/snapshots", headers=ds).json()["id"]


def _register(env: Env, ds, snapshot_id, *, name="gbdt", version="1", ctx="bedwars", domain="move"):
    return env.client.post(
        "/api/v1/models",
        json={
            "name": name, "version": version, "candidate_type": "gbdt", "game_context": ctx,
            "domain": domain, "snapshot_id": snapshot_id, "onnx_key": f"m/{name}/{version}.onnx",
        },
        headers=ds,
    )  # fmt: skip


@pytest.fixture
def models(env: Env, ds, snapshot_id):
    _register(env, ds, snapshot_id, name="gbdt", version="1", ctx="bedwars", domain="move")
    _register(env, ds, snapshot_id, name="gbdt", version="2", ctx="skywars", domain="move")
    _register(env, ds, snapshot_id, name="cnn", version="1", ctx="bedwars", domain="aim")


def _versions(env: Env, ds, **params) -> set[tuple[str, str]]:
    body = env.client.get("/api/v1/models", params={"limit": 5, **params}, headers=ds).json()
    return {(m["name"], m["version"]) for m in body["items"]}


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"name": "gbdt"}, {("gbdt", "1"), ("gbdt", "2")}),
        ({"version": "1"}, {("gbdt", "1"), ("cnn", "1")}),
        ({"game_context": "bedwars"}, {("gbdt", "1"), ("cnn", "1")}),
        ({"domain": "aim"}, {("cnn", "1")}),
        ({"stage": "registered"}, {("gbdt", "1"), ("gbdt", "2"), ("cnn", "1")}),
        ({"stage": "enforce"}, set()),
        ({"name": "gbdt", "game_context": "bedwars"}, {("gbdt", "1")}),
    ],
)
def test_model_list_filters(env: Env, ds, models, params, expected):
    assert _versions(env, ds, **params) == expected


def test_model_list_rejects_an_unknown_stage(env: Env, ds, models):
    assert env.client.get("/api/v1/models?stage=live", headers=ds).status_code == 422


def test_model_by_name_and_version(env: Env, ds, models):
    resp = env.client.get("/api/v1/models/gbdt/2", headers=ds)

    assert resp.status_code == 200
    assert (resp.json()["name"], resp.json()["game_context"]) == ("gbdt", "skywars")


def test_unknown_name_and_version_is_404(env: Env, ds, models):
    resp = env.client.get("/api/v1/models/gbdt/9", headers=ds)

    assert resp.status_code == 404
    assert resp.json()["status"] == 404


def test_model_by_id_and_transitions_still_resolve(env: Env, ds, snapshot_id):
    model_id = _register(env, ds, snapshot_id).json()["id"]

    assert env.client.get(f"/api/v1/models/{model_id}", headers=ds).status_code == 200
    assert env.client.get(f"/api/v1/models/{model_id}/transitions", headers=ds).status_code == 200


def test_model_by_name_is_readable_by_viewers(env: Env, token, ds, models):
    viewer = env.headers(token(["viewer"]))

    assert env.client.get("/api/v1/models/gbdt/1", headers=viewer).status_code == 200


# --- POST /pools/preview --------------------------------------------------------------------------


def test_preview_of_an_unsaved_filter_uses_the_posted_filter_and_stores_nothing(env: Env, ds):
    env.parts["catalog"].raw = RawPreview(4, 5, 2, {"cheat": 1, "legit": 1}, [FileShare(100, 4, 2)])

    resp = env.client.post(
        "/api/v1/pools/preview",
        json={"game_contexts": ["bedwars"], "max_drop_rate": 0.1},
        headers=ds,
    )

    assert resp.status_code == 200
    assert resp.json()["sessions"] == 4
    assert resp.json()["label_distribution"] == {"legit": 1, "cheat": 1, "unlabeled": 0}
    assert env.parts["catalog"].filters[-1].game_contexts == ["bedwars"]
    assert env.parts["pools"].rows == {}


def test_preview_of_an_empty_filter_is_allowed(env: Env, ds):
    assert env.client.post("/api/v1/pools/preview", json={}, headers=ds).status_code == 200


@pytest.mark.parametrize(
    "bad",
    [
        {"max_drop_rate": 2},
        {"client_protocol": {"min": 5, "max": 1}},
        {"date_range": {"start": "2026-02-01", "end": "2026-01-01"}},
        {"unknown": 1},
    ],
)
def test_preview_validates_like_create(env: Env, ds, bad):
    preview = env.client.post("/api/v1/pools/preview", json=bad, headers=ds)
    create = env.client.post("/api/v1/pools", json={"name": "x", "filter": bad}, headers=ds)

    assert preview.status_code == create.status_code == 422
    assert env.parts["catalog"].filters == []


def test_preview_is_for_data_scientists(env: Env, token):
    viewer = env.headers(token(["viewer"]))

    assert env.client.post("/api/v1/pools/preview", json={}, headers=viewer).status_code == 403


# --- CORS -----------------------------------------------------------------------------------------


def _cors_client(env: Env, origins: str) -> TestClient:
    settings = replace_settings(env.deps.settings, cors_origins=origins)
    deps = Deps(**{**env.deps.__dict__, "settings": settings})
    return TestClient(create_app(deps=deps))


def replace_settings(settings: ApiSettings, **changes) -> ApiSettings:
    return ApiSettings(**{**settings.model_dump(), **changes})


PREFLIGHT = {
    "Origin": "https://ui.example.test",
    "Access-Control-Request-Method": "GET",
    "Access-Control-Request-Headers": "authorization,content-type",
}


def test_cors_is_off_by_default(env: Env):
    resp = env.client.options("/api/v1/models", headers=PREFLIGHT)

    assert "access-control-allow-origin" not in resp.headers


def test_a_configured_origin_may_preflight_with_authorization(env: Env):
    client = _cors_client(env, "https://ui.example.test, https://other.example.test")

    resp = client.options("/api/v1/models", headers=PREFLIGHT)

    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "https://ui.example.test"
    allowed = resp.headers["access-control-allow-headers"].lower()
    assert "authorization" in allowed
    assert "content-type" in allowed


def test_cors_never_allows_credentials(env: Env):
    client = _cors_client(env, "https://ui.example.test")

    resp = client.options("/api/v1/models", headers=PREFLIGHT)

    assert "access-control-allow-credentials" not in resp.headers


def test_an_unlisted_origin_gets_no_cors_headers(env: Env):
    client = _cors_client(env, "https://ui.example.test")

    resp = client.options(
        "/api/v1/models", headers={**PREFLIGHT, "Origin": "https://evil.example.test"}
    )

    assert resp.status_code == 400
    assert "access-control-allow-origin" not in resp.headers


def test_simple_requests_from_a_listed_origin_are_answered_with_the_origin(env: Env, token):
    client = _cors_client(env, "https://ui.example.test")
    headers = {**env.headers(token(["viewer"])), "Origin": "https://ui.example.test"}

    resp = client.get("/api/v1/models", headers=headers)

    assert resp.headers["access-control-allow-origin"] == "https://ui.example.test"


def test_cors_origin_list_ignores_blanks():
    settings = ApiSettings(
        pg_dsn="postgresql://unused", cors_origins=" https://a.test ,, https://b.test,"
    )

    assert settings.cors_origin_list() == ["https://a.test", "https://b.test"]
