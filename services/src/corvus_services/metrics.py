"""Prometheus metrics of the ingest service."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram


class Metrics:
    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()
        r = self.registry
        self.messages = Counter(
            "corvus_ingest_messages_total", "Messages handled, by outcome", ["result"], registry=r
        )
        self.frames = Counter(
            "corvus_ingest_frames_total", "Frames converted to rows", ["table"], registry=r
        )
        self.bytes = Counter(
            "corvus_ingest_parquet_bytes_total", "Parquet bytes written", ["table"], registry=r
        )
        self.flushes = Counter(
            "corvus_ingest_flushes_total", "Buffer flushes", ["table", "result"], registry=r
        )
        self.flush_seconds = Histogram(
            "corvus_ingest_flush_seconds",
            "Flush latency (encode, upload, catalog commit, ack)",
            buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
            registry=r,
        )
        self.buffered_bytes = Gauge(
            "corvus_ingest_buffered_bytes", "Estimated bytes buffered or being flushed", registry=r
        )
        self.decode_errors = Counter(
            "corvus_ingest_decode_errors_total", "Undecodable messages", registry=r
        )
