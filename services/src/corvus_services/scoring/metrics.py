"""Prometheus metrics of the scoring service."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram


class ScoreMetrics:
    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()
        r = self.registry
        self.messages = Counter(
            "corvus_score_messages_total",
            "Capture messages handled, by outcome",
            ["result"],
            registry=r,
        )
        self.frames_fed = Counter(
            "corvus_score_frames_fed_total", "Frames fed into featurizers", registry=r
        )
        self.windows_completed = Counter(
            "corvus_score_windows_completed_total", "Windows completed by featurizers", registry=r
        )
        self.windows_scored = Counter(
            "corvus_score_windows_scored_total",
            "Window scores produced",
            ["model", "stage"],
            registry=r,
        )
        self.windows_skipped = Counter(
            "corvus_score_windows_skipped_total",
            "Windows no model was run for",
            ["reason"],
            registry=r,
        )
        self.inference_seconds = Histogram(
            "corvus_score_inference_seconds",
            "Input building plus ONNX Runtime latency of one model over one micro-batch",
            ["model"],
            buckets=(0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5),
            registry=r,
        )
        self.inference_errors = Counter(
            "corvus_score_inference_errors_total",
            "Failed model runs over a micro-batch",
            ["model"],
            registry=r,
        )
        self.batch_size = Histogram(
            "corvus_score_batch_size",
            "Windows per micro-batch",
            buckets=(1, 2, 4, 8, 16, 32, 64, 128, 256, 512),
            registry=r,
        )
        self.sessions_tracked = Gauge(
            "corvus_score_sessions_tracked", "Sessions with featurizer state", registry=r
        )
        self.session_evictions = Counter(
            "corvus_score_session_evictions_total",
            "Featurizer states evicted",
            ["reason"],
            registry=r,
        )
        self.unknown_session_skips = Counter(
            "corvus_score_unknown_session_skips_total",
            "Batches skipped because neither the scorer nor the catalog knew the session/segment",
            registry=r,
        )
        self.catalog_fallbacks = Counter(
            "corvus_score_catalog_fallbacks_total",
            "Catalog lookups for unknown sessions",
            ["result"],
            registry=r,
        )
        self.model_load_failures = Counter(
            "corvus_score_model_load_failures_total",
            "Models that could not be loaded",
            ["reason"],
            registry=r,
        )
        self.models_loaded = Gauge(
            "corvus_score_models_loaded", "Loaded models by stage", ["stage"], registry=r
        )
        self.registry_polls = Counter(
            "corvus_score_registry_polls_total", "Model registry polls", ["result"], registry=r
        )
        self.publish_failures = Counter(
            "corvus_score_publish_failures_total",
            "Scores that could not be published",
            registry=r,
        )
        self.published = Counter(
            "corvus_score_published_total",
            "Scores published (canary and enforce)",
            ["stage"],
            registry=r,
        )
        self.history_dropped = Counter(
            "corvus_score_history_dropped_total",
            "History rows dropped because the write queue was full",
            registry=r,
        )
        self.write_failures = Counter(
            "corvus_score_write_failures_total", "Failed score write batches", registry=r
        )
        self.write_queue = Gauge(
            "corvus_score_write_queue", "Score rows waiting to be written", ["kind"], registry=r
        )
        self.model_window_max_packets = Gauge(
            "corvus_score_model_window_max_packets",
            "Packet cap per window of a loaded raw-byte model",
            ["model"],
            registry=r,
        )
        self.model_window_seconds = Gauge(
            "corvus_score_model_window_seconds",
            "Window length in seconds of a loaded raw-byte model",
            ["model"],
            registry=r,
        )
        self.model_estimated_p99_ms = Gauge(
            "corvus_score_model_estimated_p99_ms",
            "Estimated p99 latency of one full window of a raw-byte model (default-size model)",
            ["model"],
            registry=r,
        )
        self.raw_window_packets = Histogram(
            "corvus_score_raw_window_packets",
            "Packets per raw-byte window handed to the models",
            buckets=(8, 16, 32, 64, 128, 256, 512, 1024, 2048),
            registry=r,
        )
        self.raw_frames_dropped = Counter(
            "corvus_score_raw_frames_dropped_total",
            "Buffered raw-byte frames dropped because a window buffer was full",
            registry=r,
        )
        self.pending_windows = Gauge(
            "corvus_score_pending_windows", "Windows waiting for a micro-batch", registry=r
        )
