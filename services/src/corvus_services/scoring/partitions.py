"""Partition ownership of a scorer replica and the JetStream filter subjects it implies.

The partition token format is the one of the plugin (``Partitioner.java``): decimal with at least
two digits; for more than 100 partitions the width grows to the digits of the highest partition.
"""

from __future__ import annotations

import os
import re

_ORDINAL = re.compile(r"-(\d+)$")


def partition_token(partition: int, partitions: int) -> str:
    """The last subject token of ``partition`` (``00``..``15`` for 16, ``000``..``127`` for 128)."""
    if not 0 <= partition < partitions:
        raise ValueError(f"partition {partition} outside 0..{partitions - 1}")
    width = max(2, len(str(partitions - 1)))
    return f"{partition:0{width}d}"


def partition_of(session_id: bytes, partitions: int) -> int:
    """First 4 bytes of the session id, big-endian unsigned, modulo ``partitions``."""
    head = int.from_bytes(session_id[:4].ljust(4, b"\x00"), "big")
    return head % partitions


def owned_partitions(replica: int, replicas: int, partitions: int) -> list[int]:
    """Partitions ``p`` with ``p mod replicas == replica``."""
    if replicas < 1 or not 0 <= replica < replicas:
        raise ValueError(f"replica {replica} outside 0..{replicas - 1}")
    return [p for p in range(partitions) if p % replicas == replica]


def filter_subjects(
    replica: int, replicas: int, partitions: int, prefix: str = "corvus.capture.v1"
) -> list[str]:
    """``<prefix>.*.<pp>`` for every partition the replica owns (``*`` = proxy id)."""
    return [
        f"{prefix}.*.{partition_token(p, partitions)}"
        for p in owned_partitions(replica, replicas, partitions)
    ]


def replica_index(configured: int | None, replicas: int, hostname: str | None = None) -> int:
    """Configured index, else the ordinal suffix of a StatefulSet hostname.

    A standalone scorer (one replica) falls back to 0; with several replicas an index that cannot
    be determined is an error, because two replicas silently sharing an index would double-score.
    """
    if configured is not None:
        index = configured
    else:
        host = hostname if hostname is not None else os.environ.get("HOSTNAME", "")
        m = _ORDINAL.search(host)
        if m is None:
            if replicas > 1:
                raise ValueError(
                    "cannot determine the replica index: set CORVUS_SCORE_REPLICA_INDEX or use a "
                    "StatefulSet hostname ending in -<ordinal>"
                )
            return 0
        index = int(m.group(1))
    if index >= replicas:
        raise ValueError(f"replica index {index} must be below the replica count {replicas}")
    return index
