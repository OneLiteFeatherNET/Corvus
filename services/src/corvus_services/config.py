"""Configuration from environment variables (prefix ``CORVUS_``)."""

from __future__ import annotations

import base64
import binascii

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIB = 1024 * 1024


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CORVUS_", extra="ignore")

    # NATS / JetStream (defaults from docs/reference/data-lake.md section 1)
    nats_url: str = "nats://localhost:4222"
    # Credentials file (.creds: JWT and NKey seed), usually mounted from a Secret; unset: none.
    nats_creds_file: str | None = None
    nats_stream: str = "CORVUS_CAPTURE"
    # Replicas of the capture and score streams; an existing stream with fewer is updated.
    nats_stream_replicas: int = Field(default=1, ge=1, le=5)
    nats_subject: str = "corvus.capture.v1.>"
    nats_consumer: str = "corvus-ingest"
    stream_max_age_s: int = 24 * 3600
    stream_max_bytes: int = 10 * 1024 * MIB
    stream_duplicate_window_s: int = 120
    stream_max_msg_size: int = MIB
    consumer_ack_wait_s: int = 120
    consumer_max_ack_pending: int = 1000
    fetch_batch: int = Field(default=100, ge=1)
    fetch_timeout_s: float = Field(default=1.0, gt=0)
    nak_delay_s: float = 5.0
    # Delivery attempt at which a message for an unknown session is processed as orphan.
    unknown_session_max_deliver: int = Field(default=5, ge=1)

    # S3 / MinIO
    s3_endpoint_url: str | None = None
    s3_bucket: str = "corvus-lake"
    s3_region: str = "us-east-1"
    s3_access_key: str | None = None
    s3_secret_key: SecretStr | None = None

    # PostgreSQL
    pg_dsn: str = "postgresql://corvus@localhost:5432/corvus"
    pg_pool_max: int = Field(default=4, ge=1)

    # Keys
    kek: SecretStr = SecretStr("")
    kek_id: str = "kek-1"
    dek_cache_size: int = Field(default=100_000, ge=1)
    session_cache_size: int = Field(default=50_000, ge=1)
    session_cache_ttl_s: float = 60.0

    # Buffering (defaults from the contract section 5)
    flush_max_rows: int = Field(default=200_000, ge=1)
    flush_max_bytes: int = Field(default=64 * MIB, ge=1)
    flush_max_age_s: float = Field(default=60.0, gt=0)
    buffer_cap_bytes: int = Field(default=256 * MIB, ge=1)
    workers: int | None = Field(default=None, ge=1)

    # Observability
    metrics_port: int = 9464

    # Housekeeping
    raw_retention_days: int = Field(default=21, ge=1)
    orphan_grace_s: int = 3600
    staging_max_age_s: int = 3600
    batch_log_retention_days: int = Field(default=3, ge=1)
    score_history_retention_days: int = Field(default=14, ge=1)

    # Compaction after erasure (corvus-compact)
    # Rows per Arrow batch while rewriting a file: bounds the memory of one rewrite.
    compact_batch_rows: int = Field(default=65_536, ge=1)
    # Erasure requests are compacted only after this age, so that frames still buffered or cached
    # by ingest at erasure time have landed in the lake (session cache TTL plus flush age).
    compact_grace_s: int = Field(default=600, ge=0)

    @field_validator("kek")
    @classmethod
    def _kek_is_256_bit(cls, v: SecretStr) -> SecretStr:
        raw = v.get_secret_value()
        if raw:
            try:
                key = base64.b64decode(raw, validate=True)
            except binascii.Error as e:
                raise ValueError("CORVUS_KEK must be base64") from e
            if len(key) != 32:
                raise ValueError("CORVUS_KEK must decode to 32 bytes")
        return v

    def kek_bytes(self) -> bytes:
        raw = self.kek.get_secret_value()
        if not raw:
            raise ValueError("CORVUS_KEK is required (base64, 32 bytes)")
        return base64.b64decode(raw)
