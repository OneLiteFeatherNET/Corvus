from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from corvus_services.cgroup import default_workers, parse_cpu_max
from corvus_services.clock import new_ulid
from corvus_services.config import Settings
from corvus_services.health import run_checks
from helpers import b64


def test_cgroup_limit_rounds_up_and_is_capped_by_usable_cpus(tmp_path: Path):
    f = tmp_path / "cpu.max"
    f.write_text("150000 100000")
    assert default_workers(f, process_cpu_count=8) == 2
    assert default_workers(f, process_cpu_count=1) == 1


def test_cgroup_unlimited_uses_cpu_count(tmp_path: Path):
    f = tmp_path / "cpu.max"
    f.write_text("max 100000")
    assert parse_cpu_max("max 100000") is None
    assert default_workers(f, process_cpu_count=6) == 6


def test_ulid_is_26_chars_and_sorts_by_time():
    a = new_ulid(datetime(2026, 1, 1, tzinfo=UTC))
    b = new_ulid(datetime(2026, 1, 2, tzinfo=UTC))
    assert len(a) == 26
    assert a < b


def test_ulid_is_deterministic_for_given_entropy():
    t = datetime(2026, 1, 1, tzinfo=UTC)
    assert new_ulid(t, b"\x00" * 10) == new_ulid(t, b"\x00" * 10)


def test_settings_defaults_match_contract():
    s = Settings(kek=b64(b"k" * 32))
    assert s.nats_stream == "CORVUS_CAPTURE"
    assert s.nats_consumer == "corvus-ingest"
    assert s.consumer_ack_wait_s == 120
    assert s.consumer_max_ack_pending == 1000
    assert (s.flush_max_rows, s.flush_max_bytes, s.flush_max_age_s) == (200_000, 64 << 20, 60)
    assert s.buffer_cap_bytes == 256 << 20
    assert s.raw_retention_days == 21
    assert s.metrics_port == 9464
    assert s.kek_bytes() == b"k" * 32


def test_settings_reject_kek_of_wrong_length():
    with pytest.raises(ValueError, match="32 bytes"):
        Settings(kek=b64(b"short"))


def test_settings_read_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CORVUS_KEK_ID", "kek-9")
    monkeypatch.setenv("CORVUS_FLUSH_MAX_ROWS", "5")
    s = Settings()
    assert s.kek_id == "kek-9"
    assert s.flush_max_rows == 5


async def test_health_checks_report_failures():
    async def ok() -> None:
        return None

    async def bad() -> None:
        raise ConnectionError("down")

    result = await run_checks({"nats": ok, "s3": bad})
    assert result["nats"] == "ok"
    assert "down" in result["s3"]
