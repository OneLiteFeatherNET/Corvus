"""Worker count and memory limit derived from the cgroup limits."""

from __future__ import annotations

import math
import os
from pathlib import Path

CPU_MAX_PATH = Path("/sys/fs/cgroup/cpu.max")
MEMORY_MAX_PATH = Path("/sys/fs/cgroup/memory.max")
# cgroup v1 and "no limit" report a huge number instead of "max"; anything this large is none.
_UNLIMITED_BYTES = 2**60


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


def parse_memory_max(text: str) -> int | None:
    """Parse cgroup v2 ``memory.max`` ("<bytes>|max") into bytes.

    Returns None when there is no limit or the content is not understood.
    """
    parts = text.split()
    if not parts or parts[0] == "max":
        return None
    try:
        limit = int(parts[0])
    except ValueError:
        return None
    if limit <= 0 or limit >= _UNLIMITED_BYTES:
        return None
    return limit


def memory_limit(path: Path = MEMORY_MAX_PATH) -> int | None:
    """Memory limit in bytes from the cgroup file, None if absent, unreadable or unlimited."""
    try:
        return parse_memory_max(path.read_text())
    except OSError:
        return None


def physical_memory_bytes() -> int | None:
    """Installed RAM from ``sysconf``; None where the platform does not report it."""
    try:
        return int(os.sysconf("SC_PAGE_SIZE")) * int(os.sysconf("SC_PHYS_PAGES"))
    except (ValueError, OSError, AttributeError):
        return None
