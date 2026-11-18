"""CORVUS_LAKE_* settings: Parquet by default, Iceberg needs its full set (checked at start-up)."""

from __future__ import annotations

import base64
import os

import pytest
from pydantic import ValidationError

from corvus_services.config import Settings
from helpers import KEK

ICEBERG_VALUES = {
    "lake_format": "iceberg",
    "lake_catalog_uri": "http://polaris.data.svc.cluster.local:8181/api/catalog",
    "lake_oauth_token_url": "https://login.example.invalid/token",
    "lake_oauth_scope": "api://example/.default",
    "lake_client_id": "ingest-client",
    "lake_client_secret": "not-a-real-secret",
    "lake_s3_endpoint_url": "http://rook-ceph-rgw.example.invalid",
    "lake_s3_access_key": "access",
    "lake_s3_secret_key": "hunter2-not-logged",
}


@pytest.fixture(autouse=True)
def _no_lake_env_from_the_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("CORVUS_LAKE_"):
            monkeypatch.delenv(name)


def settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"kek": base64.b64encode(KEK).decode()}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_parquet_is_the_default_and_needs_no_lake_settings():
    s = settings()
    assert s.lake_format == "parquet"
    assert s.lake_warehouse == "corvus"
    assert s.lake_namespace == "capture"
    assert s.lake_s3_region == "us-east-1"


def test_iceberg_with_its_full_set_is_accepted():
    s = settings(**ICEBERG_VALUES)
    assert s.lake_format == "iceberg"
    assert s.lake_client_secret is not None
    assert s.lake_client_secret.get_secret_value() == "not-a-real-secret"


def test_iceberg_names_every_missing_value_at_start_up():
    with pytest.raises(ValidationError) as err:
        settings(lake_format="iceberg")
    message = str(err.value)
    for name in (
        "CORVUS_LAKE_CATALOG_URI",
        "CORVUS_LAKE_OAUTH_TOKEN_URL",
        "CORVUS_LAKE_OAUTH_SCOPE",
        "CORVUS_LAKE_CLIENT_ID",
        "CORVUS_LAKE_CLIENT_SECRET",
        "CORVUS_LAKE_S3_ENDPOINT_URL",
        "CORVUS_LAKE_S3_ACCESS_KEY",
        "CORVUS_LAKE_S3_SECRET_KEY",
    ):
        assert name in message


def test_iceberg_rejects_one_missing_value_only():
    values = {k: v for k, v in ICEBERG_VALUES.items() if k != "lake_s3_secret_key"}
    with pytest.raises(ValidationError, match="CORVUS_LAKE_S3_SECRET_KEY"):
        settings(**values)


def test_unknown_lake_format_is_rejected():
    with pytest.raises(ValidationError):
        settings(lake_format="delta")


def test_lake_secrets_are_not_shown_in_the_representation():
    s = settings(**ICEBERG_VALUES)
    assert "hunter2-not-logged" not in repr(s)
    assert "not-a-real-secret" not in repr(s)


def test_iceberg_maintenance_by_housekeeping_is_off_by_default():
    assert Settings().lake_maintenance_enabled is False


def test_lake_snapshot_retention_defaults_to_seven_days_and_is_positive():
    assert Settings().lake_snapshot_retention_days == 7
    with pytest.raises(ValidationError, match="lake_snapshot_retention_days"):
        Settings(lake_snapshot_retention_days=0)
