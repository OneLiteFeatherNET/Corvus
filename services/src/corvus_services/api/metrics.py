"""Prometheus metrics of the control-plane API (one registry per app instance)."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Histogram


class ApiMetrics:
    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()
        r = self.registry
        self.requests = Counter(
            "corvus_api_requests_total",
            "HTTP requests by route template, method and status",
            ["route", "method", "status"],
            registry=r,
        )
        self.latency = Histogram(
            "corvus_api_request_seconds",
            "HTTP request latency by route template",
            ["route", "method"],
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
            registry=r,
        )
        self.erasures = Counter(
            "corvus_api_erasures_total", "Player erasure requests completed", registry=r
        )
        self.transitions = Counter(
            "corvus_api_model_transitions_total",
            "Model stage transitions by target stage and result",
            ["to_stage", "result"],
            registry=r,
        )
        self.jobs = Counter(
            "corvus_api_jobs_total",
            "Jobs submitted by type and runner",
            ["type", "runner"],
            registry=r,
        )
        self.jobs_reconciled = Counter(
            "corvus_api_jobs_reconciled_total",
            "Jobs whose status the reconciler changed, by new status",
            ["status"],
            registry=r,
        )
