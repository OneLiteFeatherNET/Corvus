"""Physical clean-up of the Iceberg lake tables: snapshot expiry, orphan files, commit retries.

Used by ``iceberg_erasure`` (erasure, immediately after the row deletes) and by
``corvus-housekeeping`` (expiry by age, orphans left by failed commits). See
docs/reference/data-lake.md section 2a.

Reachability: a file is live when a retained snapshot still reads it. Those are the manifest
lists and manifests of all retained snapshots, and the data and delete files their ``ADDED`` or
``EXISTING`` entries name. ``DELETED`` entries do not count: the file they name was removed by a
later snapshot, and keeping it would keep the erased rows. The current metadata file and the
metadata-log files and the statistics files of the table are kept as well (they hold no rows).
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Protocol

import boto3
from botocore.config import Config
from pyiceberg.exceptions import CommitFailedException
from pyiceberg.table import Table
from pyiceberg.table.snapshots import Snapshot

from .config import Settings
from .ports import ObjectInfo

log = logging.getLogger("corvus.iceberg.maintenance")

MAX_CONFLICT_ATTEMPTS = 5
DATA_DIRS = ("data", "metadata")
_DELETE_BATCH = 1000


class TableFiles(Protocol):
    """Object listing and deletion below the table locations (``s3://bucket/key`` or ``file://``)."""

    def list_objects(self, prefix: str) -> list[ObjectInfo]: ...
    def delete_objects(self, keys: list[str]) -> None: ...


def retry_on_conflict[T](
    op: Callable[[], T],
    what: str,
    *,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
    base_backoff_s: float = 0.2,
    max_backoff_s: float = 5.0,
    attempts: int = MAX_CONFLICT_ATTEMPTS,
) -> T:
    """Run ``op`` (which loads the table itself) again after a commit conflict, with jittered
    exponential backoff. Other errors are raised at once."""
    draw = rng or random.Random()
    for attempt in range(1, attempts + 1):
        try:
            return op()
        except CommitFailedException:
            if attempt == attempts:
                raise
            ceiling = min(max_backoff_s, base_backoff_s * 2 ** (attempt - 1))
            delay = draw.uniform(0, ceiling)
            log.info(
                "%s conflicted (attempt %d of %d), retrying in %.2fs",
                what,
                attempt,
                attempts,
                delay,
            )
            sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


def expire_snapshot_ids(table: Table, snapshot_ids: list[int]) -> None:
    """Expire the given snapshots in one commit (refs must be checked by the caller).

    pyiceberg 0.10 keeps the ids of ``ExpireSnapshots`` in a class attribute that all builders
    share (``_snapshot_ids_to_expire``), so the ids of an earlier commit stay in the next one and
    fail with "does not exist". Every call therefore gets a builder with its own set."""
    builder = table.maintenance.expire_snapshots()
    builder._snapshot_ids_to_expire = set()  # noqa: SLF001 - see above
    builder.by_ids(snapshot_ids).commit()


def snapshot_seq(snapshot: Snapshot) -> int:
    return snapshot.sequence_number or 0


def protected_snapshots(table: Table) -> dict[str, int]:
    """Branch and tag heads (ref name -> snapshot id). Expiry must not remove them."""
    return {name: ref.snapshot_id for name, ref in table.refs().items()}


def referenced_files(table: Table) -> set[str]:
    """Every file a retained snapshot still reads, plus the current and logged metadata files."""
    refs: set[str] = {table.metadata_location}
    refs.update(entry.metadata_file for entry in table.metadata.metadata_log)
    refs.update(stat.statistics_path for stat in table.metadata.statistics)
    refs.update(stat.statistics_path for stat in table.metadata.partition_statistics)
    seen_manifests: set[str] = set()
    for snapshot in table.snapshots():
        refs.add(snapshot.manifest_list)
        for manifest in snapshot.manifests(table.io):
            refs.add(manifest.manifest_path)
            if manifest.manifest_path in seen_manifests:
                continue
            seen_manifests.add(manifest.manifest_path)
            for entry in manifest.fetch_manifest_entry(table.io, discard_deleted=True):
                refs.add(entry.data_file.file_path)
    return refs


def snapshot_files(table: Table, snapshots: list[Snapshot]) -> set[str]:
    """Every file the given snapshots name, whatever the status of the entry: manifest lists,
    manifests, data and delete files. Read before the snapshots are expired."""
    out: set[str] = set()
    for snapshot in snapshots:
        out.add(snapshot.manifest_list)
        for manifest in snapshot.manifests(table.io):
            out.add(manifest.manifest_path)
            for entry in manifest.fetch_manifest_entry(table.io, discard_deleted=False):
                out.add(entry.data_file.file_path)
    return out


def remove_orphan_files(
    table: Table, files: TableFiles, now: datetime, margin: timedelta
) -> list[str]:
    """Delete the objects below the table's data and metadata directories that no retained
    snapshot reads and that are older than ``margin`` (an in-flight commit of another writer is
    younger than that). Returns the deleted keys."""
    live = referenced_files(table)
    location = table.location().rstrip("/")
    orphans = [
        obj.key
        for sub in DATA_DIRS
        for obj in files.list_objects(f"{location}/{sub}/")
        if obj.key not in live and now - obj.last_modified > margin
    ]
    if orphans:
        files.delete_objects(orphans)
        log.info("removed %d orphan files from %s", len(orphans), location)
    return orphans


class S3TableFiles:
    """``TableFiles`` on the warehouse object store of the Iceberg catalog (path-style addressing,
    static keys)."""

    def __init__(self, client: Any) -> None:
        self._client = client

    @classmethod
    def from_settings(cls, s: Settings) -> S3TableFiles:
        client = boto3.client(
            "s3",
            endpoint_url=s.lake_s3_endpoint_url,
            region_name=s.lake_s3_region,
            aws_access_key_id=s.lake_s3_access_key,
            aws_secret_access_key=(
                s.lake_s3_secret_key.get_secret_value() if s.lake_s3_secret_key else None
            ),
            config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 5}),
        )
        return cls(client)

    def list_objects(self, prefix: str) -> list[ObjectInfo]:
        bucket, key_prefix = _split_uri(prefix)
        out: list[ObjectInfo] = []
        for page in self._client.get_paginator("list_objects_v2").paginate(
            Bucket=bucket, Prefix=key_prefix
        ):
            for obj in page.get("Contents", []):
                out.append(
                    ObjectInfo(f"s3://{bucket}/{obj['Key']}", obj["LastModified"], obj["Size"])
                )
        return out

    def delete_objects(self, keys: list[str]) -> None:
        by_bucket: dict[str, list[str]] = {}
        for key in keys:
            bucket, object_key = _split_uri(key)
            by_bucket.setdefault(bucket, []).append(object_key)
        for bucket, object_keys in by_bucket.items():
            for i in range(0, len(object_keys), _DELETE_BATCH):
                batch = object_keys[i : i + _DELETE_BATCH]
                resp = self._client.delete_objects(
                    Bucket=bucket,
                    Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True},
                )
                errors = resp.get("Errors", [])
                if errors:
                    first = errors[0]
                    raise RuntimeError(
                        f"{len(errors)} deletes failed in {bucket}, first {first.get('Key')}: "
                        f"{first.get('Code')}"
                    )


def _split_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("s3://"):
        raise ValueError(f"not an s3 uri: {uri!r}")
    bucket, _, key = uri[len("s3://") :].partition("/")
    return bucket, key
