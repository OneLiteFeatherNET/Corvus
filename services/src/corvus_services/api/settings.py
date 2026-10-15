"""Configuration of the control-plane API (environment variables, see docs/reference/control-plane-api.md)."""

from __future__ import annotations

import base64
import binascii
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_PSEUDONYM_KEY_BYTES = 16
"""Same minimum as ``PseudonymKey.MIN_KEY_BYTES`` in the plugin."""


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CORVUS_API_", extra="ignore", populate_by_name=True
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

    # Pseudonym key, identical to the plugin's (base64, at least 16 bytes).
    pseudonym_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("CORVUS_PSEUDONYM_KEY", "CORVUS_API_PSEUDONYM_KEY"),
    )
    pseudonym_key_id: str = Field(
        default="k1",
        validation_alias=AliasChoices("CORVUS_PSEUDONYM_KEY_ID", "CORVUS_API_PSEUDONYM_KEY_ID"),
    )

    # Models: quality gate for promotion to ``enforce``.
    gate_max_p99_latency_ms: float = Field(default=10.0, gt=0)

    # Pagination and bulk limits
    default_page_size: int = Field(default=50, ge=1)
    max_page_size: int = Field(default=200, ge=1)
    label_import_max_rows: int = Field(default=50_000, ge=1)
    label_import_max_bytes: int = Field(default=8 * 1024 * 1024, ge=1)

    # Jobs
    job_runner: Literal["local", "kubernetes"] = "local"
    # Local runner: argv prefix; the job type and arguments are appended.
    job_local_command: list[str] = ["corvus-ml"]
    job_k8s_namespace: str = "corvus"
    job_k8s_image: str = ""
    job_k8s_command: list[str] = ["corvus-ml"]
    job_k8s_service_account: str | None = None
    job_k8s_cpu_request: str = "1"
    job_k8s_cpu_limit: str = "4"
    job_k8s_memory_request: str = "2Gi"
    job_k8s_memory_limit: str = "8Gi"
    job_k8s_ttl_after_finished_s: int = Field(default=86_400, ge=0)
    job_k8s_active_deadline_s: int = Field(default=14_400, ge=1)
    job_k8s_backoff_limit: int = Field(default=0, ge=0)
    # Name of a Secret that is exposed as environment (S3/DB credentials of the job); optional.
    job_k8s_env_secret: str | None = None

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
