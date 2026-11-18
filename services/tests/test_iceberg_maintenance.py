"""Iceberg maintenance helpers: conflict retry and the S3 table-file adapter (botocore Stubber,
no network)."""

from __future__ import annotations

import random
from datetime import UTC, datetime

import boto3
import pytest
from botocore.config import Config
from botocore.stub import Stubber
from pyiceberg.exceptions import CommitFailedException

from corvus_services.iceberg_maintenance import S3TableFiles, retry_on_conflict


def conflicts(times: int):
    state = {"left": times}

    def op() -> str:
        if state["left"] > 0:
            state["left"] -= 1
            raise CommitFailedException("conflict")
        return "done"

    return op


def test_retry_returns_the_first_success_without_waiting():
    sleeps: list[float] = []

    assert retry_on_conflict(lambda: "ok", "op", sleep=sleeps.append) == "ok"
    assert sleeps == []


def test_retry_waits_with_jitter_below_the_exponential_ceiling():
    sleeps: list[float] = []

    result = retry_on_conflict(
        conflicts(3),
        "op",
        sleep=sleeps.append,
        rng=random.Random(1),
        base_backoff_s=0.2,
        max_backoff_s=5.0,
    )

    assert result == "done"
    assert len(sleeps) == 3
    for attempt, delay in enumerate(sleeps, start=1):
        assert 0 <= delay <= min(5.0, 0.2 * 2 ** (attempt - 1))


def test_retry_gives_up_after_the_last_attempt():
    sleeps: list[float] = []

    with pytest.raises(CommitFailedException):
        retry_on_conflict(conflicts(10), "op", sleep=sleeps.append, attempts=3)

    assert len(sleeps) == 2


def test_other_errors_are_not_retried():
    sleeps: list[float] = []

    def op() -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        retry_on_conflict(op, "op", sleep=sleeps.append)

    assert sleeps == []


def _s3_stub() -> tuple[S3TableFiles, Stubber]:
    client = boto3.client(
        "s3",
        region_name="us-east-1",
        endpoint_url="http://127.0.0.1:9",
        aws_access_key_id="test",
        aws_secret_access_key="test",
        config=Config(s3={"addressing_style": "path"}),
    )
    return S3TableFiles(client), Stubber(client)


def test_s3_listing_returns_full_uris_across_pages():
    files, stub = _s3_stub()
    when = datetime(2026, 11, 18, tzinfo=UTC)
    with stub:
        stub.add_response(
            "list_objects_v2",
            {
                "Contents": [{"Key": "wh/t/data/a.parquet", "LastModified": when, "Size": 3}],
                "IsTruncated": True,
                "NextContinuationToken": "next",
            },
            {"Bucket": "polaris-corvus", "Prefix": "wh/t/data/"},
        )
        stub.add_response(
            "list_objects_v2",
            {"Contents": [{"Key": "wh/t/data/b.parquet", "LastModified": when, "Size": 4}]},
            {"Bucket": "polaris-corvus", "Prefix": "wh/t/data/", "ContinuationToken": "next"},
        )

        objects = files.list_objects("s3://polaris-corvus/wh/t/data/")

    assert [(o.key, o.size) for o in objects] == [
        ("s3://polaris-corvus/wh/t/data/a.parquet", 3),
        ("s3://polaris-corvus/wh/t/data/b.parquet", 4),
    ]
    assert objects[0].last_modified == when


def test_s3_delete_groups_keys_by_bucket_and_reports_failed_keys():
    files, stub = _s3_stub()
    with stub:
        stub.add_response(
            "delete_objects",
            {"Errors": [{"Key": "wh/t/data/x.parquet", "Code": "AccessDenied"}]},
            {
                "Bucket": "polaris-corvus",
                "Delete": {"Objects": [{"Key": "wh/t/data/x.parquet"}], "Quiet": True},
            },
        )

        with pytest.raises(RuntimeError, match="AccessDenied"):
            files.delete_objects(["s3://polaris-corvus/wh/t/data/x.parquet"])


def test_s3_keys_must_be_s3_uris():
    files, _ = _s3_stub()

    with pytest.raises(ValueError, match="not an s3 uri"):
        files.list_objects("file:///tmp/warehouse/")
