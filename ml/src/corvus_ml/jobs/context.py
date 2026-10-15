"""The job contract: environment and secrets as the control plane's runners provide them.

The runner passes the job type as the command argument and ``CORVUS_JOB_ID``,
``CORVUS_JOB_TYPE``, ``CORVUS_JOB_SNAPSHOT_ID`` and ``CORVUS_JOB_PARAMS`` (a JSON object) in the
environment. Credentials for the API and for S3 come from the environment as well; the values are
collected in :class:`Secrets` so that no message, log line or error text can carry them.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from uuid import UUID

JOB_TYPES = ("build_features", "evaluate", "train")
MAX_MESSAGE = 500


class ContractError(ValueError):
    """The environment does not satisfy the job contract."""


@dataclass(frozen=True)
class JobContext:
    job_id: str
    job_type: str
    snapshot_id: str
    params: dict

    @classmethod
    def from_env(cls, environ: Mapping[str, str], job_type: str | None = None) -> JobContext:
        """Read the contract; ``job_type`` is the command-line argument (checked against env)."""
        env_type = environ.get("CORVUS_JOB_TYPE")
        chosen = job_type or env_type
        if chosen not in JOB_TYPES:
            raise ContractError(f"job type must be one of {JOB_TYPES}")
        if env_type and env_type != chosen:
            raise ContractError("job type argument and CORVUS_JOB_TYPE disagree")
        raw_params = environ.get("CORVUS_JOB_PARAMS") or "{}"
        try:
            params = json.loads(raw_params)
        except json.JSONDecodeError as exc:
            raise ContractError(f"CORVUS_JOB_PARAMS is not valid JSON: {exc.msg}") from None
        if not isinstance(params, dict):
            raise ContractError("CORVUS_JOB_PARAMS must be a JSON object")
        return cls(
            job_id=_uuid(environ, "CORVUS_JOB_ID"),
            job_type=chosen,
            snapshot_id=_uuid(environ, "CORVUS_JOB_SNAPSHOT_ID"),
            params=params,
        )


def _uuid(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name, "")
    try:
        return str(UUID(value))
    except ValueError:
        raise ContractError(f"{name} must be a UUID") from None


@dataclass(frozen=True)
class ApiSettings:
    url: str
    token: str = field(repr=False)

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> ApiSettings:
        url, token = environ.get("CORVUS_API_URL", ""), environ.get("CORVUS_API_TOKEN", "")
        if not url or not token:
            raise ContractError("CORVUS_API_URL and CORVUS_API_TOKEN are required")
        return cls(url.rstrip("/"), token)


@dataclass(frozen=True)
class S3Settings:
    """Same variable names as the services (``corvus_services.config``)."""

    endpoint_url: str | None
    access_key: str | None = field(repr=False)
    secret_key: str | None = field(repr=False)
    bucket: str = "corvus-lake"
    region: str = "us-east-1"

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> S3Settings:
        return cls(
            endpoint_url=environ.get("CORVUS_S3_ENDPOINT_URL") or None,
            access_key=environ.get("CORVUS_S3_ACCESS_KEY") or None,
            secret_key=environ.get("CORVUS_S3_SECRET_KEY") or None,
            bucket=environ.get("CORVUS_S3_BUCKET") or "corvus-lake",
            region=environ.get("CORVUS_S3_REGION") or "us-east-1",
        )


class Secrets:
    """Scrubs known secret values and bearer tokens from text before it leaves the process."""

    _BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")

    def __init__(self, *values: str | None) -> None:
        # Longest first so that a secret containing another one is replaced as a whole.
        self._values = sorted({v for v in values if v and len(v) >= 4}, key=len, reverse=True)

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> Secrets:
        return cls(
            environ.get("CORVUS_API_TOKEN"),
            environ.get("CORVUS_S3_SECRET_KEY"),
            environ.get("CORVUS_S3_ACCESS_KEY"),
            environ.get("CORVUS_KEK"),
            environ.get("CORVUS_PG_DSN"),
            environ.get("PGPASSWORD"),
        )

    def scrub(self, text: str, limit: int = MAX_MESSAGE) -> str:
        for value in self._values:
            text = text.replace(value, "***")
        text = self._BEARER.sub("Bearer ***", text)
        return text if len(text) <= limit else text[: limit - 3] + "..."
