"""Erasure and retention of player data in the Iceberg tables (docs/reference/data-lake.md 2a).

``IcebergCleanup.erase`` runs for one erasure request, from ``corvus-compact``:

1. delete the player's rows from ``events`` and ``raw``: first all rows with ``player_key`` equal
   to the pseudonym (one commit per table), then the rows with a null ``player_key`` of the
   player's sessions (orphan frames; chunked ``IN`` lists of session ids). Every delete commit is
   tagged with the request id in its snapshot summary, so the snapshots of this erasure can be
   found again;
2. per table: the cut is the newest tagged snapshot. Every snapshot older than the cut is expired
   right away, the cut and everything newer is kept. Expiry is refused (``held_by``) while a
   branch or tag points at one of the snapshots to expire: the request then stays blocked;
3. unless blocked, the files that only the expired snapshots named are deleted: every manifest
   list, manifest, data and delete file of an expired snapshot that no retained snapshot names
   (live or deleted entries). Nothing else is touched: a file that no snapshot names (an
   in-flight write of another job, an orphan of a failed commit) is never deleted here, so this
   step needs no age margin. The erased rows are then gone from the object store, except for the
   column statistics of the erased files, which the cut's manifest keeps until the next commit of
   the table (open point, see the doc). If this deletion fails, the request stays pending and the
   files are left to the opt-in maintenance (orphan removal with a 24 h margin).

Conflicts (another writer committed first) retry the whole step with a fresh table load.

``IcebergCleanup.maintain`` runs for ``corvus-housekeeping`` only when
``CORVUS_LAKE_MAINTENANCE_ENABLED`` is set (default off): the platform's Airflow DAG
``iceberg_maintenance`` owns the routine maintenance (rewrite, expiry, orphan removal). The
housekeeping variant expires snapshots older than the retention and removes orphan files older
than 24 h. It never deletes rows, and it never expires a snapshot newer than the oldest branch or
tag head, so an erasure that is held by a tag keeps its cut.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from functools import partial
from typing import Any, TypeVar

from pyiceberg.catalog import Catalog
from pyiceberg.expressions import And, EqualTo, In, IsNull
from pyiceberg.table import Table

from .clock import Clock
from .config import Settings
from .iceberg_maintenance import (
    MAX_CONFLICT_ATTEMPTS,
    S3TableFiles,
    TableFiles,
    expire_snapshot_ids,
    protected_snapshots,
    referenced_files,
    remove_orphan_files,
    retry_on_conflict,
    snapshot_files,
    snapshot_seq,
)
from .iceberg_sink import open_catalog
from .schema import SCHEMAS

log = logging.getLogger("corvus.iceberg.cleanup")

ERASURE_PROPERTY = "corvus.erasure-request"
"""Snapshot summary key that tags every delete commit of an erasure request with its id."""
DELETE_CHUNK = 1000
"""Session ids per delete commit (one ``IN`` list)."""

T = TypeVar("T")


@dataclass(frozen=True)
class TableOutcome:
    table: str
    expired: int = 0
    orphans: int = 0
    held_by: tuple[str, ...] = ()
    """Why expiry was refused: names of the branches or tags that point at a snapshot to expire."""


class IcebergCleanup:
    def __init__(
        self,
        catalog: Catalog,
        namespace: str,
        files: TableFiles,
        *,
        clock: Clock,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
        maintenance_orphan_margin: timedelta = timedelta(hours=24),
        delete_chunk: int = DELETE_CHUNK,
    ) -> None:
        self._catalog = catalog
        self._namespace = namespace
        self._files = files
        self._clock = clock
        self._sleep = sleep
        self._rng = rng or random.Random()
        self._maintenance_orphan_margin = maintenance_orphan_margin
        self._delete_chunk = delete_chunk

    @classmethod
    def from_settings(cls, s: Settings, clock: Clock) -> IcebergCleanup:
        return cls(
            open_catalog(s),
            s.lake_namespace,
            S3TableFiles.from_settings(s),
            clock=clock,
        )

    def ident(self, table: str) -> str:
        return f"{self._namespace}.{table}"

    # ----- erasure ----------------------------------------------------------------------------

    def erase(self, request_id: str, pseudonym: bytes, session_ids: list[bytes]) -> list[str]:
        """Delete, expire and clean up for one request. Blocking: run it in a worker thread.

        Returns the reasons the erasure is held back (empty: the rows are gone and the old
        snapshots are expired). Raises when a step fails for another reason than a commit
        conflict; the request then stays pending and the next run starts again (deletes are
        idempotent)."""
        # pyiceberg 0.12 types these expressions as pydantic models whose fields do not match
        # their documented constructors; the positional calls are correct at runtime.
        for name in SCHEMAS:
            self._retry(
                f"erasure delete {name}",
                partial(self._delete_op, name, EqualTo("player_key", pseudonym), request_id),  # type: ignore[call-arg,arg-type]
            )
            for start in range(0, len(session_ids), self._delete_chunk):
                chunk = session_ids[start : start + self._delete_chunk]
                self._retry(
                    f"erasure delete orphans {name}",
                    partial(
                        self._delete_op,
                        name,
                        And(IsNull("player_key"), In("session_id", chunk)),  # type: ignore[call-arg,arg-type]
                        request_id,
                    ),
                )
        outcomes = [self._erase_table(name, request_id) for name in SCHEMAS]
        return [
            f"{o.table}: snapshots to expire are held by {', '.join(o.held_by)}"
            for o in outcomes
            if o.held_by
        ]

    def _delete_op(self, name: str, predicate: Any, request_id: str) -> None:
        table = self._catalog.load_table(self.ident(name))
        # A predicate without a matching row commits nothing (pyiceberg only warns).
        table.delete(predicate, snapshot_properties={ERASURE_PROPERTY: request_id})

    def _erase_table(self, name: str, request_id: str) -> TableOutcome:
        cut = self._cut_sequence(name, request_id)
        if cut is None:  # no delete committed for this table: no erased rows in it
            return TableOutcome(name)
        expired, held, files = self._retry(
            f"erasure expiry {name}", partial(self._expire_before_cut, name, cut)
        )
        if held:
            log.warning("erasure %s: %s held by %s", request_id, name, ", ".join(held))
            return TableOutcome(name, expired=0, held_by=held)
        removed = self._remove_expired_files(name, files)
        return TableOutcome(name, expired=expired, orphans=len(removed))

    def _remove_expired_files(self, name: str, files: frozenset[str]) -> list[str]:
        """Delete the files of the expired snapshots that no retained snapshot names."""
        if not files:
            return []
        table = self._catalog.load_table(self.ident(name))
        live = referenced_files(table)
        candidates = sorted(f for f in files if f not in live)
        if candidates:
            self._files.delete_objects(candidates)
            log.info("erasure removed %d files of expired snapshots of %s", len(candidates), name)
        return candidates

    def _cut_sequence(self, name: str, request_id: str) -> int | None:
        table = self._catalog.load_table(self.ident(name))
        seqs = [
            snapshot_seq(s)
            for s in table.snapshots()
            if s.summary is not None
            and s.summary.additional_properties.get(ERASURE_PROPERTY) == request_id
        ]
        return max(seqs) if seqs else None

    def _expire_before_cut(
        self, name: str, cut: int
    ) -> tuple[int, tuple[str, ...], frozenset[str]]:
        table = self._catalog.load_table(self.ident(name))
        victim_snapshots = [s for s in table.snapshots() if snapshot_seq(s) < cut]
        if not victim_snapshots:
            return 0, (), frozenset()
        victim_set = {s.snapshot_id for s in victim_snapshots}
        held = tuple(
            sorted(ref for ref, sid in protected_snapshots(table).items() if sid in victim_set)
        )
        if held:
            return 0, held, frozenset()
        files = frozenset(snapshot_files(table, victim_snapshots))
        expire_snapshot_ids(table, list(victim_set))
        return len(victim_set), (), files

    # ----- housekeeping -----------------------------------------------------------------------

    def maintain(self, retention: timedelta) -> list[TableOutcome]:
        """Expire snapshots older than ``retention`` and remove orphan files, per table.

        A snapshot is expired only when it is older than the retention and older than every
        branch and tag head; a ref that points at an older snapshot keeps the whole chain from
        there on. Blocking: run it in a worker thread."""
        cutoff_ms = int((self._clock() - retention).timestamp() * 1000)
        return [self._maintain_table(name, cutoff_ms) for name in SCHEMAS]

    def _maintain_table(self, name: str, cutoff_ms: int) -> TableOutcome:
        expired = self._retry(f"expiry {name}", partial(self._expire_by_age, name, cutoff_ms))
        orphans = self._orphans(name)
        return TableOutcome(name, expired=expired, orphans=len(orphans))

    def _expire_by_age(self, name: str, cutoff_ms: int) -> int:
        table = self._catalog.load_table(self.ident(name))
        seq_of = {s.snapshot_id: snapshot_seq(s) for s in table.snapshots()}
        ref_seqs = [seq_of[sid] for sid in protected_snapshots(table).values() if sid in seq_of]
        floor = min(ref_seqs) if ref_seqs else None
        victims = [
            s.snapshot_id
            for s in table.snapshots()
            if s.timestamp_ms < cutoff_ms and (floor is None or snapshot_seq(s) < floor)
        ]
        if victims:
            expire_snapshot_ids(table, victims)
        return len(victims)

    # ----- shared -----------------------------------------------------------------------------

    def _orphans(self, name: str) -> list[str]:
        table: Table = self._catalog.load_table(self.ident(name))
        return remove_orphan_files(
            table, self._files, self._clock(), self._maintenance_orphan_margin
        )

    def _retry(self, what: str, op: Callable[[], T]) -> T:
        return retry_on_conflict(
            op,
            what,
            sleep=self._sleep,
            rng=self._rng,
            attempts=MAX_CONFLICT_ATTEMPTS,
        )
