"""Shared helpers of the API tests (importable, unlike conftest)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import jwt
from api_fakes import FakeRunner, MutableClock
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi.testclient import TestClient

from corvus_services.api.auth import JwksCache, TokenVerifier
from corvus_services.api.deps import Deps

ISSUER = "https://idp.example.test/realms/corvus"
AUDIENCE = "corvus-api"
PLUGIN_TOKEN = "proxy-token-1"
PSEUDONYM_KEY = b"0123456789abcdef"


@dataclass
class Keys:
    rsa: rsa.RSAPrivateKey
    ec: ec.EllipticCurvePrivateKey

    def jwks(self) -> dict[str, Any]:
        rsa_jwk = jwt.algorithms.RSAAlgorithm.to_jwk(self.rsa.public_key(), as_dict=True)
        ec_jwk = jwt.algorithms.ECAlgorithm.to_jwk(self.ec.public_key(), as_dict=True)
        return {"keys": [{**rsa_jwk, "kid": "rsa-1"}, {**ec_jwk, "kid": "ec-1"}]}


class FakeJwksSource:
    def __init__(self, doc: dict[str, Any]) -> None:
        self.doc = doc
        self.fetches = 0
        self.fail = False

    async def fetch(self) -> dict[str, Any]:
        self.fetches += 1
        if self.fail:
            raise ConnectionError("idp down")
        return self.doc


def make_verifier(source: FakeJwksSource, clock: MutableClock, **overrides: Any) -> TokenVerifier:
    opts: dict[str, Any] = {
        "issuer": ISSUER,
        "audience": AUDIENCE,
        "algorithms": ["RS256", "ES256"],
        "roles_claim": "roles",
        "role_mapping": {},
        "leeway_s": 0,
    }
    opts.update(overrides)
    return TokenVerifier(JwksCache(source, clock, 600, 30), clock=clock, **opts)


TokenFactory = Callable[..., str]


@dataclass
class Env:
    client: TestClient
    deps: Deps
    parts: dict[str, Any]
    runner: FakeRunner
    clock: MutableClock

    def headers(self, tok: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {tok}"}
