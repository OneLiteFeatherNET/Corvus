"""Authentication (OIDC bearer tokens, optional dev token, proxy API tokens) and role-based authorization."""

from __future__ import annotations

import hashlib
import hmac
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

import httpx
import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..clock import Clock
from .problems import ProblemError
from .settings import ApiSettings

log = logging.getLogger("corvus.api.auth")

_ALG_KTY = {"RS256": "RSA", "ES256": "EC"}


class Role(StrEnum):
    ADMIN = "admin"
    DATA_SCIENTIST = "data_scientist"
    STAFF = "staff"
    VIEWER = "viewer"


READERS: tuple[Role, ...] = (Role.VIEWER, Role.STAFF, Role.DATA_SCIENTIST)
"""Roles allowed on read-only endpoints (admin is always allowed)."""


@dataclass(frozen=True)
class Principal:
    subject: str
    roles: frozenset[Role]
    display_name: str | None = None
    job: str | None = None
    """Name of the machine token (``CORVUS_API_JOB_TOKENS``); ``None`` for people."""

    @property
    def is_job(self) -> bool:
        return self.job is not None


class AuthError(Exception):
    """The credentials are missing or invalid; the message is safe to show to the caller."""


class JwksSource(Protocol):
    async def fetch(self) -> dict[str, Any]:
        """Return the JWKS document (``{"keys": [...]}``)."""
        ...


class HttpJwksSource:
    def __init__(self, url: str, timeout_s: float = 5.0) -> None:
        self._url = url
        self._timeout = timeout_s

    async def fetch(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(self._url, headers={"Accept": "application/json"})
            resp.raise_for_status()
            doc = resp.json()
        if not isinstance(doc, dict) or not isinstance(doc.get("keys"), list):
            raise ValueError("JWKS document has no keys")
        return doc


class JwksCache:
    """Caches the JWKS for ``ttl_s``; an unknown ``kid`` triggers one refetch per ``min_refresh_s``."""

    def __init__(
        self, source: JwksSource, clock: Clock, ttl_s: float, min_refresh_s: float
    ) -> None:
        self._source = source
        self._clock = clock
        self._ttl = ttl_s
        self._min_refresh = min_refresh_s
        self._keys: dict[str, dict[str, Any]] = {}
        self._fetched_at: float | None = None
        self._last_attempt: float | None = None

    def _now(self) -> float:
        return self._clock().timestamp()

    def _can_attempt(self) -> bool:
        return self._last_attempt is None or self._now() - self._last_attempt >= self._min_refresh

    async def _refresh(self) -> None:
        self._last_attempt = self._now()
        try:
            doc = await self._source.fetch()
        except Exception as e:
            # Keep serving the old keys; the caller decides whether the kid is still unknown.
            log.warning("JWKS fetch failed (%s)", type(e).__name__)
            return
        self._keys = {
            str(k["kid"]): k for k in doc.get("keys", []) if isinstance(k, dict) and "kid" in k
        }
        self._fetched_at = self._now()

    async def get(self, kid: str | None) -> dict[str, Any] | None:
        stale = self._fetched_at is None or self._now() - self._fetched_at >= self._ttl
        if stale and self._can_attempt():
            await self._refresh()
        key = self._lookup(kid)
        if key is None and self._can_attempt():
            await self._refresh()
            key = self._lookup(kid)
        return key

    def _lookup(self, kid: str | None) -> dict[str, Any] | None:
        if kid is not None:
            return self._keys.get(kid)
        return next(iter(self._keys.values())) if len(self._keys) == 1 else None


class TokenVerifier:
    """Validates signature, ``iss``, ``aud``, ``exp`` and ``nbf`` of OIDC access tokens."""

    def __init__(
        self,
        jwks: JwksCache,
        *,
        issuer: str,
        audience: str,
        algorithms: Sequence[str],
        roles_claim: str,
        role_mapping: dict[str, str],
        clock: Clock,
        leeway_s: int = 30,
        display_name_claim: str = "name",
    ) -> None:
        self._jwks = jwks
        self._issuer = issuer
        self._audience = audience
        self._algorithms = list(algorithms)
        self._roles_claim = roles_claim
        self._role_mapping = role_mapping
        self._clock = clock
        self._leeway = leeway_s
        self._display_name_claim = display_name_claim

    async def verify(self, token: str) -> Principal:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as e:
            raise AuthError("malformed token") from e
        alg = header.get("alg")
        if alg not in self._algorithms:
            raise AuthError("token algorithm is not allowed")
        jwk = await self._jwks.get(header.get("kid"))
        if jwk is None:
            raise AuthError("unknown signing key")
        if jwk.get("kty") != _ALG_KTY.get(str(alg)):
            raise AuthError("signing key does not match the token algorithm")
        try:
            key = jwt.PyJWK.from_dict(jwk).key
            claims = jwt.decode(
                token,
                key,
                algorithms=[str(alg)],
                audience=self._audience,
                issuer=self._issuer,
                # exp/nbf are checked below against the injected clock.
                options={
                    "require": ["exp", "iss", "aud", "sub"],
                    "verify_exp": False,
                    "verify_nbf": False,
                    "verify_iat": False,
                },
            )
        except jwt.InvalidAudienceError as e:
            raise AuthError("wrong audience") from e
        except jwt.InvalidIssuerError as e:
            raise AuthError("wrong issuer") from e
        except jwt.PyJWTError as e:
            raise AuthError("invalid token") from e
        now = self._clock().timestamp()
        exp, nbf = claims.get("exp"), claims.get("nbf")
        if not isinstance(exp, int | float) or now - self._leeway >= exp:
            raise AuthError("token expired")
        if nbf is not None and (not isinstance(nbf, int | float) or now + self._leeway < nbf):
            raise AuthError("token not yet valid")
        return Principal(str(claims["sub"]), self._roles(claims), self._display_name(claims))

    def _display_name(self, claims: dict[str, Any]) -> str | None:
        for claim in (self._display_name_claim, "preferred_username"):
            value = claims.get(claim)
            if isinstance(value, str) and value.strip():
                return value.strip()[:200]
        return None

    def _roles(self, claims: dict[str, Any]) -> frozenset[Role]:
        value: Any = claims
        for part in self._roles_claim.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        raw = value.split() if isinstance(value, str) else value
        if not isinstance(raw, list):
            return frozenset()
        return frozenset(r for r in map(self._map_role, raw) if r is not None)

    def _map_role(self, external: Any) -> Role | None:
        name = (
            self._role_mapping.get(str(external), str(external))
            if self._role_mapping
            else str(external)
        )
        try:
            return Role(name)
        except ValueError:
            return None


def token_digest(token: str) -> str:
    """Hex SHA-256 of a machine token, the form configured in ``CORVUS_API_JOB_TOKENS``."""
    return hashlib.sha256(token.encode()).hexdigest()


class Authenticator:
    """Dev token (only if explicitly configured) first, then job tokens, then OIDC."""

    def __init__(
        self,
        verifier: TokenVerifier | None,
        dev_token: str | None = None,
        dev_roles: Sequence[Role] = (Role.ADMIN,),
        job_tokens: Sequence[tuple[str, str]] = (),
    ) -> None:
        self._verifier = verifier
        self._dev_token = dev_token
        self._dev_roles = frozenset(dev_roles)
        self._job_tokens = list(job_tokens)
        if dev_token:
            log.warning(
                "CORVUS_API_DEV_TOKEN is set: a static bearer token grants roles %s. "
                "NEVER enable this outside local development.",
                ", ".join(sorted(r.value for r in self._dev_roles)),
            )

    @property
    def configured(self) -> bool:
        return self._verifier is not None or bool(self._dev_token) or bool(self._job_tokens)

    def _job_principal(self, token: str) -> Principal | None:
        """Constant-time comparison of the presented token's hash against every configured hash."""
        presented = token_digest(token).encode()
        matched: str | None = None
        for digest, name in self._job_tokens:
            if hmac.compare_digest(presented, digest.encode()):
                matched = name
        if matched is None:
            return None
        return Principal(f"job:{matched}", frozenset({Role.DATA_SCIENTIST}), job=matched)

    async def authenticate(self, token: str) -> Principal:
        if self._dev_token and hmac.compare_digest(token.encode(), self._dev_token.encode()):
            return Principal("dev", self._dev_roles)
        job = self._job_principal(token) if self._job_tokens else None
        if job is not None:
            return job
        if self._verifier is None:
            raise AuthError("invalid token")
        return await self._verifier.verify(token)


def build_authenticator(
    settings: ApiSettings, clock: Clock, source: JwksSource | None = None
) -> Authenticator:
    verifier: TokenVerifier | None = None
    if settings.oidc_issuer and settings.oidc_audience and (settings.oidc_jwks_url or source):
        src = source or HttpJwksSource(str(settings.oidc_jwks_url))
        verifier = TokenVerifier(
            JwksCache(src, clock, settings.jwks_cache_ttl_s, settings.jwks_min_refresh_s),
            issuer=settings.oidc_issuer,
            audience=settings.oidc_audience,
            algorithms=settings.oidc_algorithms,
            roles_claim=settings.roles_claim,
            role_mapping=settings.role_mapping,
            clock=clock,
            leeway_s=settings.oidc_leeway_s,
            display_name_claim=settings.display_name_claim,
        )
    dev = settings.dev_token.get_secret_value() if settings.dev_token else None
    dev_roles = [Role(r) for r in settings.dev_token_roles]
    return Authenticator(verifier, dev or None, dev_roles, settings.job_token_list())


_bearer = HTTPBearer(auto_error=False, description="OIDC access token")
_plugin_bearer = HTTPBearer(
    auto_error=False, description="Proxy API token", scheme_name="ProxyToken"
)


def _unauthorized(reason: str) -> ProblemError:
    return ProblemError(
        401,
        reason,
        slug="unauthorized",
        headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
    )


JOB_ID_HEADER = "X-Corvus-Job-Id"


def require(*roles: Role, allow_jobs: bool = False) -> Callable[..., Awaitable[Principal]]:
    """Dependency factory: the caller needs one of ``roles`` (``admin`` always passes).

    Job tokens (machine principals with role ``data_scientist``) are refused unless ``allow_jobs``.
    """
    allowed = frozenset(roles) | {Role.ADMIN}

    async def dependency(
        request: Request,
        creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    ) -> Principal:
        if creds is None:
            raise _unauthorized("missing bearer token")
        authenticator: Authenticator = request.app.state.deps.authenticator
        try:
            principal = await authenticator.authenticate(creds.credentials)
        except AuthError as e:
            raise _unauthorized(str(e)) from e
        if principal.is_job and not allow_jobs:
            raise ProblemError(
                403, "job tokens are not accepted on this endpoint", slug="forbidden"
            )
        if not (principal.roles & allowed):
            raise ProblemError(
                403,
                "insufficient role",
                slug="forbidden",
                required=sorted(r.value for r in roles),
            )
        request.state.principal = principal
        return principal

    return dependency


def enforce_job_scope(principal: Principal, job_id: UUID, header: str | None) -> None:
    """A job principal may touch only the job named in ``X-Corvus-Job-Id`` and in the path."""
    if not principal.is_job:
        return
    try:
        declared = UUID(header) if header else None
    except ValueError:
        declared = None
    if declared != job_id:
        raise ProblemError(
            403,
            f"job tokens may only act on their own job: send {JOB_ID_HEADER} equal to the job id",
            slug="forbidden",
        )


async def authenticated(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    """Any valid token, whatever its roles (used by ``GET /me``)."""
    if creds is None:
        raise _unauthorized("missing bearer token")
    authenticator: Authenticator = request.app.state.deps.authenticator
    try:
        principal = await authenticator.authenticate(creds.credentials)
    except AuthError as e:
        raise _unauthorized(str(e)) from e
    if principal.is_job:
        raise ProblemError(403, "job tokens are not accepted on this endpoint", slug="forbidden")
    request.state.principal = principal
    return principal


async def require_proxy_token(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_plugin_bearer),
) -> None:
    """Machine auth for proxies. Constant-time comparison against every configured token."""
    if creds is None:
        raise _unauthorized("missing bearer token")
    presented = creds.credentials.encode()
    tokens: list[str] = request.app.state.deps.plugin_tokens
    matched = False
    for token in tokens:
        matched |= hmac.compare_digest(presented, token.encode())
    if not matched:
        raise _unauthorized("invalid token")
