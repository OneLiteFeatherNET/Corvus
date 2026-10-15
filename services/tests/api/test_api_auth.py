from __future__ import annotations

import logging
from typing import Any

import jwt
import pytest
from api_fakes import FIXED_NOW, MutableClock
from api_support import (
    AUDIENCE,
    ISSUER,
    Env,
    FakeJwksSource,
    Keys,
    TokenFactory,
    make_verifier,
)

from corvus_services.api.auth import Authenticator, AuthError, Role, TokenVerifier
from corvus_services.api.settings import ApiSettings

NOW = int(FIXED_NOW.timestamp())


@pytest.fixture
def verifier(jwks_source: FakeJwksSource, clock: MutableClock) -> TokenVerifier:
    return make_verifier(jwks_source, clock)


async def test_valid_rs256_token_yields_subject_and_roles(verifier, token: TokenFactory):
    principal = await verifier.verify(token(["staff", "viewer"], sub="alice"))

    assert principal.subject == "alice"
    assert principal.roles == {Role.STAFF, Role.VIEWER}


async def test_valid_es256_token_is_accepted(verifier, token: TokenFactory):
    principal = await verifier.verify(token(["admin"], alg="ES256"))

    assert principal.roles == {Role.ADMIN}


@pytest.mark.parametrize(
    ("claims", "reason"),
    [
        ({"exp": NOW - 1}, "expired"),
        ({"nbf": NOW + 60}, "not yet valid"),
        ({"aud": "someone-else"}, "wrong audience"),
        ({"iss": "https://evil.example.test"}, "wrong issuer"),
        ({"exp": None}, "invalid token"),
        ({"sub": None}, "invalid token"),
    ],
)
async def test_invalid_claims_are_rejected(verifier, token: TokenFactory, claims, reason):
    with pytest.raises(AuthError) as err:
        await verifier.verify(token(["admin"], **claims))

    assert reason in str(err.value)


async def test_token_just_before_expiry_is_still_valid(verifier, token: TokenFactory, clock):
    tok = token(["viewer"], exp=NOW + 10)
    clock.advance(9)

    assert (await verifier.verify(tok)).roles == {Role.VIEWER}


async def test_expiry_is_judged_by_the_injected_clock(verifier, token: TokenFactory, clock):
    tok = token(["viewer"], exp=NOW + 10)
    clock.advance(11)

    with pytest.raises(AuthError, match="expired"):
        await verifier.verify(tok)


async def test_leeway_tolerates_small_clock_skew(jwks_source, clock, token: TokenFactory):
    verifier = make_verifier(jwks_source, clock, leeway_s=30)

    assert await verifier.verify(token(["viewer"], exp=NOW - 10))


async def test_hs256_token_signed_with_the_public_key_is_rejected(verifier, keys: Keys):
    # Algorithm-confusion attempt: HMAC with a key derived from public material.
    forged = jwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "sub": "x", "exp": NOW + 60, "roles": ["admin"]},
        "any-shared-secret-of-sufficient-length-0123456789",
        algorithm="HS256",
        headers={"kid": "rsa-1"},
    )

    with pytest.raises(AuthError, match="algorithm is not allowed"):
        await verifier.verify(forged)


async def test_unsigned_token_is_rejected(verifier):
    unsigned = jwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "sub": "x", "exp": NOW + 60}, None, algorithm="none"
    )

    with pytest.raises(AuthError, match="algorithm is not allowed"):
        await verifier.verify(unsigned)


async def test_algorithm_allowlist_can_be_narrowed(jwks_source, clock, token: TokenFactory):
    verifier = make_verifier(jwks_source, clock, algorithms=["ES256"])

    with pytest.raises(AuthError, match="algorithm is not allowed"):
        await verifier.verify(token(["admin"], alg="RS256"))


async def test_key_type_must_match_the_token_algorithm(verifier, keys: Keys):
    # ES256 header pointing at the RSA key.
    tok = jwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "sub": "x", "exp": NOW + 60},
        keys.ec,
        algorithm="ES256",
        headers={"kid": "rsa-1"},
    )

    with pytest.raises(AuthError, match="does not match"):
        await verifier.verify(tok)


async def test_token_signed_by_an_unknown_key_is_rejected(verifier, token: TokenFactory):
    from cryptography.hazmat.primitives.asymmetric import rsa

    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    with pytest.raises(AuthError, match="invalid token"):
        await verifier.verify(token(["admin"], key=other))


async def test_unknown_kid_is_rejected(verifier, token: TokenFactory):
    with pytest.raises(AuthError, match="unknown signing key"):
        await verifier.verify(token(["admin"], kid="nope"))


async def test_malformed_token_is_rejected(verifier):
    with pytest.raises(AuthError, match="malformed"):
        await verifier.verify("not-a-jwt")


async def test_roles_come_from_a_nested_claim_and_are_mapped(jwks_source, clock, token):
    verifier = make_verifier(
        jwks_source,
        clock,
        roles_claim="realm_access.roles",
        role_mapping={"corvus-ds": "data_scientist", "corvus-ops": "admin"},
    )
    tok = token(None, realm_access={"roles": ["corvus-ds", "unrelated", "offline_access"]})

    assert (await verifier.verify(tok)).roles == {Role.DATA_SCIENTIST}


async def test_roles_claim_may_be_a_space_separated_string(jwks_source, clock, token):
    verifier = make_verifier(jwks_source, clock, roles_claim="scope")

    principal = await verifier.verify(token(None, scope="viewer staff openid"))

    assert principal.roles == {Role.VIEWER, Role.STAFF}


async def test_unknown_roles_are_ignored(verifier, token: TokenFactory):
    assert (await verifier.verify(token(["root", "superuser"]))).roles == frozenset()


async def test_jwks_is_cached_between_requests(verifier, jwks_source, token: TokenFactory):
    for _ in range(3):
        await verifier.verify(token(["viewer"]))

    assert jwks_source.fetches == 1


async def test_jwks_is_refetched_after_the_ttl(verifier, jwks_source, token, clock):
    tok = token(["viewer"], exp=NOW + 10_000)
    await verifier.verify(tok)
    clock.advance(601)
    await verifier.verify(tok)

    assert jwks_source.fetches == 2


async def test_unknown_kid_triggers_one_refetch_per_rate_limit_window(
    verifier, jwks_source, token: TokenFactory, clock
):
    await verifier.verify(token(["viewer"], exp=NOW + 10_000))
    clock.advance(31)

    for _ in range(3):
        with pytest.raises(AuthError):
            await verifier.verify(token(["viewer"], kid="rotated-away"))

    assert jwks_source.fetches == 2


async def test_rotated_key_is_picked_up_after_the_rate_limit(
    verifier, jwks_source, token, keys, clock
):
    await verifier.verify(token(["viewer"]))
    new_doc = keys.jwks()
    new_doc["keys"][0] = {**new_doc["keys"][0], "kid": "rsa-2"}
    jwks_source.doc = new_doc
    clock.advance(31)

    assert await verifier.verify(token(["viewer"], kid="rsa-2"))


async def test_cached_keys_survive_an_idp_outage(verifier, jwks_source, token, clock):
    tok = token(["viewer"], exp=NOW + 10_000)
    await verifier.verify(tok)
    jwks_source.fail = True
    clock.advance(601)

    assert await verifier.verify(tok)


# --- dev token --------------------------------------------------------------------------------


def test_dev_token_is_off_by_default():
    assert ApiSettings().dev_token is None


async def test_dev_token_grants_configured_roles_and_warns_loudly(caplog, verifier):
    with caplog.at_level(logging.WARNING, logger="corvus.api.auth"):
        auth = Authenticator(verifier, "dev-secret", [Role.DATA_SCIENTIST])

    assert "NEVER enable this outside local development" in caplog.text
    principal = await auth.authenticate("dev-secret")
    assert (principal.subject, principal.roles) == ("dev", {Role.DATA_SCIENTIST})


async def test_without_dev_token_the_static_value_is_just_an_invalid_jwt(verifier, caplog):
    with caplog.at_level(logging.WARNING, logger="corvus.api.auth"):
        auth = Authenticator(verifier)

    assert caplog.text == ""
    with pytest.raises(AuthError):
        await auth.authenticate("dev-secret")


async def test_dev_token_alone_works_without_oidc():
    auth = Authenticator(None, "dev-secret")

    assert auth.configured
    with pytest.raises(AuthError):
        await auth.authenticate("something-else")


def test_no_credentials_configured_is_detected():
    assert not Authenticator(None).configured


# --- HTTP-level authorization ------------------------------------------------------------------

UUID_ = "00000000-0000-0000-0000-000000000001"
PSEUDO = "ab" * 32

# (method, path, roles that may call it); admin may call everything.
ENDPOINTS: list[tuple[str, str, set[str]]] = [
    ("GET", "/api/v1/pools", {"data_scientist"}),
    ("POST", "/api/v1/pools", {"data_scientist"}),
    ("GET", f"/api/v1/pools/{UUID_}", {"data_scientist"}),
    ("PUT", f"/api/v1/pools/{UUID_}", {"data_scientist"}),
    ("DELETE", f"/api/v1/pools/{UUID_}", {"data_scientist"}),
    ("POST", f"/api/v1/pools/{UUID_}/preview", {"data_scientist"}),
    ("POST", f"/api/v1/pools/{UUID_}/snapshots", {"data_scientist"}),
    ("GET", f"/api/v1/pools/{UUID_}/snapshots", {"data_scientist"}),
    ("GET", "/api/v1/snapshots", {"data_scientist"}),
    ("GET", f"/api/v1/snapshots/{UUID_}", {"data_scientist"}),
    ("POST", "/api/v1/labels", {"staff"}),
    ("GET", "/api/v1/labels", {"viewer", "staff", "data_scientist"}),
    ("POST", "/api/v1/labels/import", {"data_scientist"}),
    ("POST", "/api/v1/players/lookup", set()),
    ("POST", "/api/v1/players/erasure", set()),
    ("GET", f"/api/v1/players/erasure/{UUID_}", set()),
    ("GET", "/api/v1/blocklist", set()),
    ("GET", f"/api/v1/blocklist/{PSEUDO}", set()),
    ("PUT", f"/api/v1/blocklist/{PSEUDO}", set()),
    ("DELETE", f"/api/v1/blocklist/{PSEUDO}", set()),
    ("POST", "/api/v1/models", {"data_scientist"}),
    ("GET", "/api/v1/models", {"viewer", "staff", "data_scientist"}),
    ("GET", f"/api/v1/models/{UUID_}", {"viewer", "staff", "data_scientist"}),
    ("GET", f"/api/v1/models/{UUID_}/transitions", {"viewer", "staff", "data_scientist"}),
    ("POST", f"/api/v1/models/{UUID_}/stage", {"data_scientist"}),
    ("POST", "/api/v1/jobs", {"data_scientist"}),
    ("GET", "/api/v1/jobs", {"data_scientist"}),
    ("GET", f"/api/v1/jobs/{UUID_}", {"data_scientist"}),
    ("PATCH", f"/api/v1/jobs/{UUID_}", {"data_scientist"}),
    ("POST", "/api/v1/pools/preview", {"data_scientist"}),
    ("GET", "/api/v1/models/some-model/1", {"viewer", "staff", "data_scientist"}),
    ("GET", "/api/v1/summary", {"viewer", "staff", "data_scientist"}),
    ("GET", "/api/v1/erasure-requests", set()),
]
ALL_ROLES = ["viewer", "staff", "data_scientist", "admin"]


def _call(env: Env, method: str, path: str, headers: dict[str, str]) -> Any:
    return env.client.request(method, path, headers=headers)


@pytest.mark.parametrize(("method", "path", "allowed"), ENDPOINTS)
def test_every_endpoint_requires_a_token(env: Env, method, path, allowed):
    resp = _call(env, method, path, {})

    assert resp.status_code == 401
    assert resp.headers["www-authenticate"].startswith("Bearer")
    assert resp.headers["content-type"].startswith("application/problem+json")


@pytest.mark.parametrize(("method", "path", "allowed"), ENDPOINTS)
@pytest.mark.parametrize("role", ALL_ROLES)
def test_roles_are_enforced_per_endpoint(env: Env, token, method, path, allowed, role):
    resp = _call(env, method, path, env.headers(token([role])))

    if role in allowed | {"admin"}:
        assert resp.status_code not in (401, 403), f"{role} must reach {method} {path}"
    else:
        assert resp.status_code == 403, f"{role} must not reach {method} {path}"
        assert resp.json()["status"] == 403


def test_token_without_known_roles_is_forbidden(env: Env, token):
    resp = env.client.get("/api/v1/models", headers=env.headers(token(["nonsense"])))

    assert resp.status_code == 403


def test_invalid_token_is_unauthorized_with_problem_details(env: Env, token):
    resp = env.client.get("/api/v1/models", headers=env.headers(token(["admin"], aud="other")))

    assert resp.status_code == 401
    body = resp.json()
    assert body["status"] == 401
    assert body["detail"] == "wrong audience"


def test_basic_auth_scheme_is_not_accepted(env: Env):
    resp = env.client.get("/api/v1/models", headers={"Authorization": "Basic Zm9vOmJhcg=="})

    assert resp.status_code == 401


def test_operational_endpoints_need_no_token(env: Env):
    for path in ("/healthz", "/readyz", "/metrics"):
        assert env.client.get(path).status_code == 200
