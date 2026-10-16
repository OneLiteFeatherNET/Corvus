"""Throughput view of the scoring latency: how many players one core keeps up with.

Scoring is asynchronous, so the single-window latency is no real-time budget. What matters is that
the scorer keeps up: each player yields one window per ``stride_seconds`` and a window costs
``mean_ms`` on ``threads`` cores, so one core serves ``stride_seconds / (mean_ms / 1000 * threads)``
players (``stride / mean latency`` for the default of one thread).
"""

from __future__ import annotations


def players_per_core(stride_seconds: float, mean_ms: float, threads: int = 1) -> float:
    if mean_ms <= 0:
        return float("inf")
    return stride_seconds * 1000.0 / (mean_ms * max(1, threads))


def annotate_capacity(res: dict, stride_seconds: float | None, threads: int) -> None:
    """Add ``stride_seconds`` and ``players_per_core`` to the bench dicts of a candidate result."""
    if stride_seconds is None:
        return
    benches = [res.get("bench"), *(res.get("bench_sizes") or {}).values()]
    for b in benches:
        if b:
            b["stride_seconds"] = stride_seconds
            b["players_per_core"] = players_per_core(stride_seconds, b["latency_mean_ms"], threads)
