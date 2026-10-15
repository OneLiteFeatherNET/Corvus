"""Worker count derived from the cgroup CPU limit.

Copied from ml/src/corvus_ml/cgroup.py (the two projects are separate uv projects); keep in sync.
"""

from __future__ import annotations

import math
import os
from pathlib import Path

CPU_MAX_PATH = Path("/sys/fs/cgroup/cpu.max")


def parse_cpu_max(text: str) -> float | None:
    """Parse cgroup v2 ``cpu.max`` ("<quota|max> <period>") into a CPU count.

    Returns None when there is no limit or the content is not understood.
    """
    parts = text.split()
    if len(parts) < 1 or parts[0] == "max":
        return None
    try:
        quota = int(parts[0])
        period = int(parts[1]) if len(parts) > 1 else 100_000
    except ValueError:
        return None
    if quota <= 0 or period <= 0:
        return None
    return quota / period


def cpu_limit(path: Path = CPU_MAX_PATH) -> float | None:
    """CPU limit from the cgroup file, None if absent, unreadable or unlimited."""
    try:
        return parse_cpu_max(path.read_text())
    except OSError:
        return None


def default_workers(path: Path = CPU_MAX_PATH, process_cpu_count: int | None = None) -> int:
    """Pool size: ceil(cgroup limit) if limited, else the usable CPU count; at least 1."""
    if process_cpu_count is None:
        process_cpu_count = os.process_cpu_count() or 1
    limit = cpu_limit(path)
    if limit is None:
        return max(1, process_cpu_count)
    return max(1, min(math.ceil(limit), process_cpu_count))
