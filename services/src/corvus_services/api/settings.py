"""Configuration of the control-plane API (environment variables, see docs/reference/control-plane-api.md)."""

from __future__ import annotations

import base64
import binascii
import re
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_PSEUDONYM_KEY_BYTES = 16
"""Same minimum as ``PseudonymKey.MIN_KEY_BYTES`` in the plugin."""


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CORVUS_API_",
        extra="ignore",
        populate_by_name=True,
        hide_input_in_errors=True,
    )

    host: str = "0.0.0.0"
    port: int = 8080

    # PostgreSQL: the DSN is shared with ingest and housekeeping.
    pg_dsn: str = Field(
        default="postgresql://corvus@localhost:5432/corvus",
        validation_alias=AliasChoices("CORVUS_PG_DSN", "CORVUS_API_PG_DSN"),
    )
    pg_pool_max: int = Field(default=8, ge=1)

    # OIDC bearer tokens
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    oidc_algorithms: list[Literal["RS256", "ES256"]] = ["RS256", "ES256"]
    oidc_leeway_s: int = Field(default=30, ge=0, le=300)
    jwks_cache_ttl_s: float = Field(default=600.0, gt=0)
    # Minimum seconds between JWKS refetches triggered by an unknown ``kid``.
    jwks_min_refresh_s: float = Field(default=30.0, ge=0)
    roles_claim: str = "roles"
    # {"<external role>": "<admin|data_scientist|staff|viewer>"}; empty means identity mapping.
    role_mapping: dict[str, str] = {}

    # Static development token. ONLY for local development; leave unset elsewhere.
    dev_token: SecretStr | None = None
    dev_token_roles: list[str] = ["admin"]

    # Machine tokens of the proxies for GET /api/v1/plugin/blocklist, comma separated.
    plugin_tokens: SecretStr = SecretStr("")

    # Claim holding the display name shown in the UI (falls back to ``preferred_username``).
    display_name_claim: str = "name"

    # Machine tokens of ML jobs, comma separated ``<sha256 hex of the token>:<job name>``.
    # Only the hash is configured; the plain token lives in the job's own secret.
    job_tokens: SecretStr = SecretStr("")

    # Browser origins allowed by CORS, comma separated (``*`` allows any). Empty disables CORS.
    cors_origins: str = ""

    # Pseudonym key, identical to the plugin's (base64, at least 16 bytes).
    pseudonym_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("CORVUS_PSEUDONYM_KEY", "CORVUS_API_PSEUDONYM_KEY"),
    )
    pseudonym_key_id: str = Field(
        default="k1",
        validation_alias=AliasChoices("CORVUS_PSEUDONYM_KEY_ID", "CORVUS_API_PSEUDONYM_KEY_ID"),
    )

    # Retention of raw payloads per pool (NFR-018). The names are shared with housekeeping, which
    # applies them; the API validates a pool's value against the maximum.
    raw_retention_default_days: int = Field(
        default=21,
        ge=1,
        validation_alias=AliasChoices(
            "CORVUS_RAW_RETENTION_DAYS", "CORVUS_API_RAW_RETENTION_DEFAULT_DAYS"
        ),
    )
    raw_retention_max_days: int = Field(
        default=365, ge=1, validation_alias=AliasChoices(
            "CORVUS_RAW_RETENTION_MAX_DAYS", "CORVUS_API_RAW_RETENTION_MAX_DAYS"
        ),
    )  # fmt: skip

    # Models: quality gate for promotion to ``enforce``. Scores are asynchronous decision aids, so
    # this is a sanity bound on one window, not a real-time requirement; throughput is what counts.
    gate_max_p99_latency_ms: float = Field(default=5000.0, gt=0)

    # Pagination and bulk limits
    default_page_size: int = Field(default=50, ge=1)
    max_page_size: int = Field(default=200, ge=1)
    label_import_max_rows: int = Field(default=50_000, ge=1)
    label_import_max_bytes: int = Field(default=8 * 1024 * 1024, ge=1)

    # Jobs
    job_runner: Literal["local", "kubernetes"] = "local"
    # Local runner: argv prefix; the job type and arguments are appended.
    job_local_command: list[str] = ["corvus-job"]
    job_k8s_namespace: str = "corvus"
    job_k8s_image: str = ""
    job_k8s_command: list[str] = ["corvus-job"]
    job_k8s_service_account: str | None = None
    job_k8s_cpu_request: str = "1"
    job_k8s_cpu_limit: str = "4"
    job_k8s_memory_request: str = "2Gi"
    job_k8s_memory_limit: str = "8Gi"
    job_k8s_ttl_after_finished_s: int = Field(default=86_400, ge=0)
    job_k8s_active_deadline_s: int = Field(default=14_400, ge=1)
    job_k8s_backoff_limit: int = Field(default=0, ge=0)
    # Size limit of the emptyDir mounted at /scratch (CORVUS_JOB_SCRATCH_DIR) in job pods.
    job_k8s_scratch_size_limit: str = "20Gi"
    # Name of a Secret that is exposed as environment (S3/DB credentials of the job); optional.
    job_k8s_env_secret: str | None = None

    @model_validator(mode="after")
    def _default_retention_within_max(self) -> ApiSettings:
        if self.raw_retention_default_days > self.raw_retention_max_days:
            raise ValueError(
                "CORVUS_RAW_RETENTION_DAYS must not exceed CORVUS_RAW_RETENTION_MAX_DAYS"
            )
        return self

    @field_validator("job_tokens")
    @classmethod
    def _job_tokens_are_well_formed(cls, v: SecretStr) -> SecretStr:
        parse_job_tokens(v.get_secret_value())
        return v

    @field_validator("pseudonym_key")
    @classmethod
    def _pseudonym_key_is_base64(cls, v: SecretStr) -> SecretStr:
        raw = v.get_secret_value()
        if raw:
            try:
                key = base64.b64decode(raw, validate=True)
            except binascii.Error as e:
                raise ValueError("CORVUS_PSEUDONYM_KEY must be base64") from e
            if len(key) < MIN_PSEUDONYM_KEY_BYTES:
                raise ValueError(
                    f"CORVUS_PSEUDONYM_KEY must be at least {MIN_PSEUDONYM_KEY_BYTES} bytes"
                )
        return v

    def pseudonym_key_bytes(self) -> bytes | None:
        raw = self.pseudonym_key.get_secret_value()
        return base64.b64decode(raw) if raw else None

    def plugin_token_list(self) -> list[str]:
        return [t.strip() for t in self.plugin_tokens.get_secret_value().split(",") if t.strip()]

    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def job_token_list(self) -> list[tuple[str, str]]:
        """``(sha256 hex, job name)`` pairs."""
        return parse_job_tokens(self.job_tokens.get_secret_value())


_JOB_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


def parse_job_tokens(raw: str) -> list[tuple[str, str]]:
    """Parse ``CORVUS_API_JOB_TOKENS``. Errors never echo the configured value."""
    entries: list[tuple[str, str]] = []
    for index, item in enumerate(i.strip() for i in raw.split(",")):
        if not item:
            continue
        digest, sep, name = item.partition(":")
        digest = digest.lower()
        if not sep or not _SHA256_HEX.match(digest) or not _JOB_NAME.match(name):
            raise ValueError(
                f"CORVUS_API_JOB_TOKENS entry {index + 1} must be <sha256 hex>:<name> "
                "(name: letters, digits, . _ -, at most 64 characters)"
            )
        entries.append((digest, name))
    return entries
