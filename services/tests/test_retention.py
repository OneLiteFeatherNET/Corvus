from __future__ import annotations

import pytest
from pydantic import ValidationError

from corvus_services.api.settings import ApiSettings
from corvus_services.config import Settings
from corvus_services.retention import effective_raw_retention


@pytest.mark.parametrize(
    ("pool", "expected"),
    [(None, 21), (7, 7), (365, 365), (1000, 365), (0, 1)],
)
def test_effective_raw_retention_is_the_pool_value_or_default_capped_at_the_maximum(pool, expected):
    assert effective_raw_retention(pool, 21, 365) == expected


def test_housekeeping_defaults_are_21_days_with_a_maximum_of_365():
    s = Settings()

    assert (s.raw_retention_days, s.raw_retention_max_days) == (21, 365)


def test_a_default_above_the_maximum_is_refused():
    with pytest.raises(ValidationError):
        Settings(raw_retention_days=400, raw_retention_max_days=365)


def test_api_reads_the_retention_names_shared_with_housekeeping(monkeypatch):
    monkeypatch.setenv("CORVUS_RAW_RETENTION_DAYS", "14")
    monkeypatch.setenv("CORVUS_RAW_RETENTION_MAX_DAYS", "90")

    s = ApiSettings(pg_dsn="postgresql://unused")

    assert (s.raw_retention_default_days, s.raw_retention_max_days) == (14, 90)


def test_api_default_above_maximum_is_refused():
    with pytest.raises(ValidationError):
        ApiSettings(
            pg_dsn="postgresql://unused", raw_retention_default_days=30, raw_retention_max_days=20
        )
