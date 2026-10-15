"""Configuration of ``corvus-score`` (environment variables, prefix ``CORVUS_``)."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, model_validator

from ..config import Settings


class ScoreSettings(Settings):
    # Partition ownership (docs/reference/data-lake.md section 6). The replica index comes from
    # CORVUS_SCORE_REPLICA_INDEX or, when unset, from a StatefulSet hostname suffix ``-<i>``.
    capture_partitions: int = Field(default=16, ge=1)
    score_replicas: int = Field(default=1, ge=1)
    score_replica_index: int | None = Field(default=None, ge=0)
    score_consumer_prefix: str = "corvus-score"
    score_ack_wait_s: int = Field(default=60, ge=1)
    score_max_ack_pending: int = Field(default=2000, ge=1)

    # Output stream and subject
    score_stream: str = "CORVUS_SCORE"
    score_subject_prefix: str = "corvus.score.v1"
    score_stream_max_age_s: int = 7 * 24 * 3600

    # Sessions: bounded LRU of featurizer state with idle eviction
    score_sessions_max: int = Field(default=20_000, ge=1)
    score_session_idle_s: float = Field(default=900.0, gt=0)
    # A session or segment unknown to the scorer is looked up in the catalog; a failed lookup is
    # remembered for this long so a burst of batches does not hit the database for every message.
    score_lookup_retry_s: float = Field(default=5.0, gt=0)
    score_lookup_cache_max: int = Field(default=50_000, ge=1)
    # Clients at or above this protocol version may send CLIENT_TICK_END (1.21.2 = 768); below it
    # the packet does not exist, so tick_end_rate is known to be null for the run.
    score_tick_end_min_protocol: int = Field(default=768, ge=0)

    # Models
    score_models_dir: Path = Path(
        "/tmp/corvus-score/models"
    )  # writable under a read-only root (chart mounts /tmp)
    score_registry_poll_s: float = Field(default=30.0, gt=0)
    # CPUs available for inference; default is the cgroup limit.
    score_cpus: int | None = Field(default=None, ge=1)

    # Micro-batching and smoothing
    score_batch_max: int = Field(default=64, ge=1)
    score_batch_max_wait_ms: float = Field(default=50.0, ge=0)
    score_pending_windows_max: int = Field(default=10_000, ge=1)
    score_ema_alpha: float = Field(default=0.3, gt=0, le=1)
    score_ema_max: int = Field(default=100_000, ge=1)

    # Persistence
    score_write_queue: int = Field(default=20_000, ge=1)
    score_write_batch: int = Field(default=500, ge=1)
    score_write_interval_s: float = Field(default=1.0, gt=0)
    # score_history_retention_days (base Settings) is applied by corvus-housekeeping

    # Graceful shutdown: how long to wait for the final scoring and write drain
    score_drain_timeout_s: float = Field(default=20.0, gt=0)

    @model_validator(mode="after")
    def _replica_in_range(self) -> ScoreSettings:
        if self.score_replica_index is not None and self.score_replica_index >= self.score_replicas:
            raise ValueError("CORVUS_SCORE_REPLICA_INDEX must be below CORVUS_SCORE_REPLICAS")
        if self.score_replicas > self.capture_partitions:
            raise ValueError("CORVUS_SCORE_REPLICAS must not exceed CORVUS_CAPTURE_PARTITIONS")
        return self
