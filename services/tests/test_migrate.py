from __future__ import annotations

from pathlib import Path

import pytest

from corvus_services.migrate import (
    Migration,
    MigrationError,
    default_migrations_dir,
    load_migrations,
    run,
)


class FakeExecutor:
    def __init__(self, applied: dict[str, str] | None = None) -> None:
        self._applied = dict(applied or {})
        self.calls: list[str] = []

    def lock(self) -> None:
        self.calls.append("lock")

    def unlock(self) -> None:
        self.calls.append("unlock")

    def bootstrap(self) -> None:
        self.calls.append("bootstrap")

    def applied(self) -> dict[str, str]:
        return dict(self._applied)

    def apply(self, name: str, sql: str, checksum: str) -> None:
        self.calls.append(f"apply {name}")
        self._applied[name] = checksum


M1 = Migration("0001_a.sql", "CREATE TABLE a();")
M2 = Migration("0002_b.sql", "CREATE TABLE b();")


def test_pending_migrations_are_applied_in_order_under_the_lock():
    ex = FakeExecutor()
    done = run([M1, M2], ex)
    assert done == ["0001_a.sql", "0002_b.sql"]
    assert ex.calls == ["lock", "bootstrap", "apply 0001_a.sql", "apply 0002_b.sql", "unlock"]


def test_already_applied_migrations_are_skipped():
    ex = FakeExecutor({M1.name: M1.checksum})
    assert run([M1, M2], ex) == ["0002_b.sql"]


def test_rerun_is_a_noop():
    ex = FakeExecutor()
    run([M1, M2], ex)
    assert run([M1, M2], ex) == []


def test_changed_checksum_of_applied_file_is_refused_and_nothing_is_applied():
    ex = FakeExecutor({M1.name: M1.checksum})
    edited = Migration(M1.name, "CREATE TABLE a(x int);")
    with pytest.raises(MigrationError, match="checksum mismatch"):
        run([edited, M2], ex)
    assert not any(c.startswith("apply") for c in ex.calls)
    assert ex.calls[-1] == "unlock", "lock is released on failure"


def test_applied_migration_missing_on_disk_is_refused():
    ex = FakeExecutor({"0009_gone.sql": "x"})
    with pytest.raises(MigrationError, match="missing on disk"):
        run([M1], ex)


def test_files_load_sorted_and_reject_bad_names(tmp_path: Path):
    (tmp_path / "0002_b.sql").write_text("b")
    (tmp_path / "0001_a.sql").write_text("a")
    assert [m.name for m in load_migrations(tmp_path)] == ["0001_a.sql", "0002_b.sql"]
    (tmp_path / "oops.sql").write_text("x")
    with pytest.raises(MigrationError, match="bad migration file name"):
        load_migrations(tmp_path)


def test_duplicate_numbers_are_rejected(tmp_path: Path):
    (tmp_path / "0001_a.sql").write_text("a")
    (tmp_path / "0001_b.sql").write_text("b")
    with pytest.raises(MigrationError, match="duplicate"):
        load_migrations(tmp_path)


def test_shipped_migrations_load_and_create_contract_tables():
    migrations = load_migrations(default_migrations_dir())
    assert migrations[0].name.startswith("0001_")
    sql = migrations[0].sql
    for table in (
        "players",
        "player_keys",
        "sessions",
        "segments",
        "lake_files",
        "lake_file_sessions",
    ):
        assert f"corvus.{table} " in sql
