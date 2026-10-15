"""Plain-SQL migration runner (``corvus-migrate``).

Numbered files ``NNNN_name.sql`` are applied in order, each in its own transaction, while holding a
PostgreSQL advisory lock. Applied files are recorded in ``corvus.schema_migrations`` with their
SHA-256; if an already applied file changed on disk the run is refused (migrations are never edited
after release).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import psycopg

log = logging.getLogger("corvus.migrate")

ADVISORY_LOCK_KEY = 0x434F52_56_5553  # "CORVUS"
_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


class MigrationError(Exception):
    """Migrations cannot be applied safely."""


@dataclass(frozen=True)
class Migration:
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode()).hexdigest()


class Executor(Protocol):
    """What the runner needs from the database; faked in unit tests."""

    def lock(self) -> None: ...
    def unlock(self) -> None: ...
    def bootstrap(self) -> None: ...
    def applied(self) -> dict[str, str]: ...
    def apply(self, name: str, sql: str, checksum: str) -> None:
        """Run ``sql`` and record the migration in one transaction."""


def load_migrations(directory: Path) -> list[Migration]:
    files = sorted(p for p in directory.iterdir() if p.suffix == ".sql")
    result: list[Migration] = []
    seen: set[str] = set()
    for path in files:
        m = _NAME.match(path.name)
        if not m:
            raise MigrationError(f"bad migration file name: {path.name}")
        if m.group(1) in seen:
            raise MigrationError(f"duplicate migration number {m.group(1)}")
        seen.add(m.group(1))
        result.append(Migration(path.name, path.read_text(encoding="utf-8")))
    return result


def run(migrations: Sequence[Migration], executor: Executor) -> list[str]:
    """Apply pending migrations in order; returns the names applied."""
    executor.lock()
    try:
        executor.bootstrap()
        applied = executor.applied()
        known = {m.name for m in migrations}
        missing = sorted(set(applied) - known)
        if missing:
            raise MigrationError(f"applied migrations missing on disk: {', '.join(missing)}")
        for m in migrations:
            recorded = applied.get(m.name)
            if recorded is not None and recorded != m.checksum:
                raise MigrationError(
                    f"checksum mismatch for applied migration {m.name}: "
                    f"recorded {recorded}, on disk {m.checksum}"
                )
        done: list[str] = []
        for m in migrations:
            if m.name in applied:
                continue
            log.info("applying %s", m.name)
            executor.apply(m.name, m.sql, m.checksum)
            done.append(m.name)
        return done
    finally:
        executor.unlock()


class PgExecutor:
    def __init__(self, conn: psycopg.Connection[tuple[object, ...]]) -> None:
        self._conn = conn

    def lock(self) -> None:
        self._conn.execute("SELECT pg_advisory_lock(%s)", (ADVISORY_LOCK_KEY,))

    def unlock(self) -> None:
        self._conn.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,))

    def bootstrap(self) -> None:
        with self._conn.transaction():
            self._conn.execute("CREATE SCHEMA IF NOT EXISTS corvus")
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS corvus.schema_migrations ("
                " name text PRIMARY KEY, checksum text NOT NULL,"
                " applied_at timestamptz NOT NULL DEFAULT now())"
            )

    def applied(self) -> dict[str, str]:
        rows = self._conn.execute("SELECT name, checksum FROM corvus.schema_migrations").fetchall()
        return {str(n): str(c) for n, c in rows}

    def apply(self, name: str, sql: str, checksum: str) -> None:
        with self._conn.transaction():
            self._conn.execute(sql.encode())
            self._conn.execute(
                "INSERT INTO corvus.schema_migrations (name, checksum) VALUES (%s, %s)",
                (name, checksum),
            )


def default_migrations_dir() -> Path:
    override = os.environ.get("CORVUS_MIGRATIONS_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "migrations"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    dsn = os.environ.get("CORVUS_PG_DSN")
    if not dsn:
        sys.exit("CORVUS_PG_DSN is required")
    try:
        migrations = load_migrations(default_migrations_dir())
        with psycopg.connect(dsn, autocommit=True) as conn:
            done = run(migrations, PgExecutor(conn))
    except MigrationError as e:
        log.error("%s", e)
        sys.exit(1)
    log.info("applied %d migration(s): %s", len(done), ", ".join(done) or "-")


if __name__ == "__main__":
    main()
