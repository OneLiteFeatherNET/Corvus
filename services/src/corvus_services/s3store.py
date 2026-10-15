"""S3 / MinIO adapter on boto3 (path-style addressing), calls run in worker threads.

boto3 was chosen over aioboto3: the flush path is few, large objects, and aioboto3 pins
aiobotocore to exact botocore versions, which makes dependency updates painful.
"""

from __future__ import annotations

import asyncio
from typing import Any

import boto3
from botocore.config import Config

from .config import Settings
from .ports import ObjectInfo


class S3Store:
    def __init__(self, client: Any, bucket: str) -> None:
        self._c = client
        self._bucket = bucket

    @classmethod
    def from_settings(cls, s: Settings) -> S3Store:
        client = boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint_url,
            region_name=s.s3_region,
            aws_access_key_id=s.s3_access_key,
            aws_secret_access_key=s.s3_secret_key.get_secret_value() if s.s3_secret_key else None,
            config=Config(
                s3={"addressing_style": "path"},
                max_pool_connections=16,
                retries={"max_attempts": 5, "mode": "standard"},
            ),
        )
        return cls(client, s.s3_bucket)

    async def get(self, key: str) -> bytes:
        return await asyncio.to_thread(self._get_sync, key)

    def _get_sync(self, key: str) -> bytes:
        body: bytes = self._c.get_object(Bucket=self._bucket, Key=key)["Body"].read()
        return body

    async def put(self, key: str, data: bytes) -> None:
        await asyncio.to_thread(self._c.put_object, Bucket=self._bucket, Key=key, Body=data)

    async def copy(self, src: str, dst: str) -> None:
        await asyncio.to_thread(
            self._c.copy_object,
            Bucket=self._bucket,
            Key=dst,
            CopySource={"Bucket": self._bucket, "Key": src},
        )

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._c.delete_object, Bucket=self._bucket, Key=key)

    async def list_objects(self, prefix: str) -> list[ObjectInfo]:
        return await asyncio.to_thread(self._list_sync, prefix)

    def _list_sync(self, prefix: str) -> list[ObjectInfo]:
        out: list[ObjectInfo] = []
        paginator = self._c.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for o in page.get("Contents", []):
                out.append(ObjectInfo(o["Key"], o["LastModified"], o["Size"]))
        return out

    async def ping(self) -> None:
        await asyncio.to_thread(self._c.head_bucket, Bucket=self._bucket)
