"""Iceberg lake sink: appends the frames of a buffer to the ``events`` and ``raw`` tables.

Target layout (docs/reference/data-lake.md section 2a):

* columns = the Iceberg layout of ``schema.py`` (plaintext ``raw`` payloads, ``player_key`` on both
  tables), unsigned integers widened to the signed Iceberg types (Iceberg has none), plus ``date``
  (UTC date of the session start);
* identity partition on ``date``; sort order (``session_id``, ``seq``); format version 2;
  ``write.parquet.compression-codec=zstd``;
* table property ``corvus.lake-schema-version`` = ``ICEBERG_LAKE_VERSION``. Bump it (and add a
  schema evolution) for any change of the columns; the writer refuses a table whose columns differ.

The catalog is an Apache Polaris REST catalog with OAuth2 client credentials; Polaris vends no
storage credentials here, so the writer uses its own static S3 keys (``X-Iceberg-Access-Delegation``
is sent empty).

Commits: one append per flush. Two writers racing on a table produce a commit conflict; the
append is retried with a fresh table load and jittered backoff (``MAX_COMMIT_ATTEMPTS``). A failed
attempt leaves its data files unreferenced (orphans for ``remove_orphan_files``).
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Callable

import pyarrow as pa
from pyiceberg.catalog import Catalog
from pyiceberg.catalog.rest import RestCatalog
from pyiceberg.exceptions import (
    CommitFailedException,
    NoSuchTableError,
    TableAlreadyExistsError,
)
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.table import Table
from pyiceberg.table.sorting import NullOrder, SortDirection, SortField, SortOrder
from pyiceberg.transforms import IdentityTransform
from pyiceberg.types import (
    BinaryType,
    BooleanType,
    DateType,
    DoubleType,
    FixedType,
    FloatType,
    IcebergType,
    IntegerType,
    LongType,
    NestedField,
)

from .buffer import TableBuffer
from .config import Settings
from .ports import Committed
from .schema import ICEBERG_LAYOUT, Layout

log = logging.getLogger("corvus.ingest.iceberg")

ICEBERG_LAKE_VERSION = 2
LAKE_VERSION_PROPERTY = "corvus.lake-schema-version"
DATE_COLUMN = "date"
PARTITION_FIELD_ID = 1000
MAX_COMMIT_ATTEMPTS = 5
TABLE_PROPERTIES = {
    "format-version": "2",
    "write.parquet.compression-codec": "zstd",
    LAKE_VERSION_PROPERTY: str(ICEBERG_LAKE_VERSION),
}

_SCALARS: dict[pa.DataType, IcebergType] = {
    pa.uint8(): IntegerType(),
    pa.int32(): IntegerType(),
    pa.uint32(): LongType(),
    pa.uint64(): LongType(),
    pa.float32(): FloatType(),
    pa.float64(): DoubleType(),
    pa.bool_(): BooleanType(),
    pa.binary(): BinaryType(),
}


def _iceberg_type(t: pa.DataType) -> IcebergType:
    if pa.types.is_fixed_size_binary(t):
        return FixedType(t.byte_width)
    try:
        return _SCALARS[t]
    except KeyError as e:
        raise TypeError(f"no Iceberg type for Arrow type {t}") from e


def iceberg_schema(arrow: pa.Schema) -> Schema:
    """Iceberg schema of a lake table: the Arrow columns in order, then ``date``."""
    fields: list[NestedField] = []
    for field_id, f in enumerate(arrow, start=1):
        fields.append(NestedField(field_id, f.name, _iceberg_type(f.type), required=not f.nullable))
    fields.append(NestedField(len(fields) + 1, DATE_COLUMN, DateType(), required=True))
    return Schema(*fields)


ICEBERG_SCHEMAS: dict[str, Schema] = {
    name: iceberg_schema(arrow) for name, arrow in ICEBERG_LAYOUT.schemas.items()
}


def partition_spec(schema: Schema) -> PartitionSpec:
    return PartitionSpec(
        PartitionField(
            source_id=schema.find_field(DATE_COLUMN).field_id,
            field_id=PARTITION_FIELD_ID,
            transform=IdentityTransform(),
            name=DATE_COLUMN,
        )
    )


def sort_order(schema: Schema) -> SortOrder:
    return SortOrder(
        *(
            SortField(
                source_id=schema.find_field(name).field_id,
                transform=IdentityTransform(),
                direction=SortDirection.ASC,
                null_order=NullOrder.NULLS_FIRST,
            )
            for name in ("session_id", "seq")
        )
    )


def _need(value: str | None, name: str) -> str:
    if not value:
        raise ValueError(f"{name} is required for the Iceberg lake")
    return value


def open_catalog(s: Settings) -> Catalog:
    """Polaris REST catalog: OAuth2 client credentials, no vended credentials, static S3 keys."""
    secret = s.lake_client_secret.get_secret_value() if s.lake_client_secret else ""
    s3_secret = s.lake_s3_secret_key.get_secret_value() if s.lake_s3_secret_key else ""
    return RestCatalog(
        "corvus",
        uri=_need(s.lake_catalog_uri, "CORVUS_LAKE_CATALOG_URI"),
        warehouse=s.lake_warehouse,
        credential=f"{_need(s.lake_client_id, 'CORVUS_LAKE_CLIENT_ID')}:{secret}",
        **{
            "oauth2-server-uri": _need(s.lake_oauth_token_url, "CORVUS_LAKE_OAUTH_TOKEN_URL"),
            "scope": _need(s.lake_oauth_scope, "CORVUS_LAKE_OAUTH_SCOPE"),
            "header.X-Iceberg-Access-Delegation": "",
            "s3.endpoint": _need(s.lake_s3_endpoint_url, "CORVUS_LAKE_S3_ENDPOINT_URL"),
            "s3.region": s.lake_s3_region,
            "s3.access-key-id": _need(s.lake_s3_access_key, "CORVUS_LAKE_S3_ACCESS_KEY"),
            "s3.secret-access-key": s3_secret,
            "s3.force-virtual-addressing": "false",
        },
    )


class IcebergSink:
    layout: Layout = ICEBERG_LAYOUT

    def __init__(
        self,
        catalog: Catalog,
        namespace: str,
        *,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
        base_backoff_s: float = 0.2,
        max_backoff_s: float = 5.0,
    ) -> None:
        self._catalog = catalog
        self._namespace = namespace
        self._sleep = sleep
        self._rng = rng or random.Random()
        self._base_backoff_s = base_backoff_s
        self._max_backoff_s = max_backoff_s

    @classmethod
    def from_settings(cls, s: Settings) -> IcebergSink:
        return cls(open_catalog(s), s.lake_namespace)

    def ident(self, table: str) -> str:
        return f"{self._namespace}.{table}"

    # ----- bootstrap (blocking, run once at start-up) ----------------------------------------

    def bootstrap(self) -> None:
        """Make sure both tables exist with the expected layout. Idempotent, race-safe: a table
        created by another replica in the meantime is loaded instead. The namespace must exist."""
        for name in ICEBERG_SCHEMAS:
            self._ensure_table(name)

    def _ensure_table(self, name: str) -> Table:
        ident = self.ident(name)
        schema = ICEBERG_SCHEMAS[name]
        try:
            table = self._catalog.load_table(ident)
        except NoSuchTableError:
            try:
                table = self._catalog.create_table(
                    ident,
                    schema,
                    partition_spec=partition_spec(schema),
                    sort_order=sort_order(schema),
                    properties=TABLE_PROPERTIES,
                )
                log.info("created lake table %s", ident)
            except TableAlreadyExistsError:
                table = self._catalog.load_table(ident)
        _check_layout(table, ident, name)
        return table

    # ----- commits ----------------------------------------------------------------------------

    async def commit(self, buf: TableBuffer) -> Committed:
        await asyncio.to_thread(self._write, buf)
        return Committed(bytes_written=None)

    async def ping(self) -> None:
        await asyncio.to_thread(self._catalog.load_namespace_properties, self._namespace)

    def _write(self, buf: TableBuffer) -> None:
        self._append_with_retry(buf.table, _arrow_rows(buf))

    def _append_with_retry(self, name: str, rows: pa.Table) -> None:
        for attempt in range(1, MAX_COMMIT_ATTEMPTS + 1):
            table = self._catalog.load_table(self.ident(name))
            try:
                table.append(rows)
                return
            except CommitFailedException:
                if attempt == MAX_COMMIT_ATTEMPTS:
                    raise
                ceiling = min(self._max_backoff_s, self._base_backoff_s * 2 ** (attempt - 1))
                delay = self._rng.uniform(0, ceiling)
                log.info(
                    "commit to %s conflicted (attempt %d of %d), retrying in %.2fs",
                    self.ident(name),
                    attempt,
                    MAX_COMMIT_ATTEMPTS,
                    delay,
                )
                self._sleep(delay)


def _check_layout(table: Table, ident: str, name: str) -> None:
    version = table.properties.get(LAKE_VERSION_PROPERTY)
    if version != str(ICEBERG_LAKE_VERSION):
        raise RuntimeError(
            f"{ident} has lake schema version {version or 'unset'}, this writer needs "
            f"{ICEBERG_LAKE_VERSION}; version 1 tables (encrypted raw payloads) are not migrated: "
            "drop the table and let ingest recreate it"
        )
    expected = ICEBERG_SCHEMAS[name]
    actual = [f.name for f in table.schema().fields]
    if actual != [f.name for f in expected.fields]:
        raise RuntimeError(
            f"{ident} has columns {actual}, this writer expects {expected.column_names}"
        )
    spec_fields = [f.name for f in table.spec().fields]
    if spec_fields != [DATE_COLUMN]:
        raise RuntimeError(f"{ident} is partitioned by {spec_fields}, expected [date]")


def _arrow_rows(buf: TableBuffer) -> pa.Table:
    columns = dict(buf.columns)
    columns[DATE_COLUMN] = [buf.day] * buf.rows
    rows = pa.Table.from_pydict(columns, schema=_ARROW_SCHEMAS[buf.table])
    return rows.sort_by([("session_id", "ascending"), ("seq", "ascending")])


_ARROW_SCHEMAS: dict[str, pa.Schema] = {
    name: schema.as_arrow() for name, schema in ICEBERG_SCHEMAS.items()
}
