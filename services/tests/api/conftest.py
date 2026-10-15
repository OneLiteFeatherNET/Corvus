"""Fixtures of the API tests: signing keys, token factory, app with fakes."""

from __future__ import annotations

from typing import Any

import jwt
import pytest
from api_fakes import FIXED_NOW, FakeRunner, MutableClock, make_repos, sequential_ids
from api_support import (
    AUDIENCE,
    ISSUER,
    PLUGIN_TOKEN,
    PSEUDONYM_KEY,
    Env,
    FakeJwksSource,
    Keys,
    TokenFactory,
    make_verifier,
)
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi.testclient import TestClient

from corvus_services.api.app import create_app
from corvus_services.api.auth import Authenticator
from corvus_services.api.deps import Deps
from corvus_services.api.pseudonym import Pseudonymizer
from corvus_services.api.settings import ApiSettings


@pytest.fixture(scope="session")
def keys() -> Keys:
    return Keys(
        rsa.generate_private_key(public_exponent=65537, key_size=2048),
        ec.generate_private_key(ec.SECP256R1()),
    )


@pytest.fixture
def clock() -> MutableClock:
    return MutableClock()


@pytest.fixture
def jwks_source(keys: Keys) -> FakeJwksSource:
    return FakeJwksSource(keys.jwks())


@pytest.fixture
def token(keys: Keys) -> TokenFactory:
    def make(
        roles: list[str] | None = None,
        *,
        sub: str = "user-1",
        alg: str = "RS256",
        kid: str | None = None,
        key: Any = None,
        **claims: Any,
    ) -> str:
        now = int(FIXED_NOW.timestamp())
        payload: dict[str, Any] = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": sub,
            "iat": now,
            "exp": now + 300,
            "roles": roles if roles is not None else [],
        }
        payload.update(claims)
        payload = {k: v for k, v in payload.items() if v is not None}
        if key is None:
            key = keys.ec if alg == "ES256" else keys.rsa
        headers = {"kid": kid or ("ec-1" if alg == "ES256" else "rsa-1")}
        return jwt.encode(payload, key, algorithm=alg, headers=headers)

    return make


@pytest.fixture
def env(jwks_source: FakeJwksSource, clock: MutableClock) -> Env:
    repos, parts = make_repos()
    runner = FakeRunner()
    settings = ApiSettings(
        pg_dsn="postgresql://unused",
        oidc_issuer=ISSUER,
        oidc_audience=AUDIENCE,
        default_page_size=2,
        max_page_size=5,
        label_import_max_rows=5,
        label_import_max_bytes=4096,
    )
    deps = Deps(
        settings=settings,
        repos=repos,
        authenticator=Authenticator(make_verifier(jwks_source, clock)),
        runner=runner,
        pseudonymizer=Pseudonymizer(PSEUDONYM_KEY, "k1"),
        plugin_tokens=[PLUGIN_TOKEN, "proxy-token-2"],
        clock=clock,
        new_id=sequential_ids(),
    )
    return Env(TestClient(create_app(deps=deps)), deps, parts, runner, clock)
