"""MinIO-backed ``StoragePort`` implementation.

Uses the official ``minio`` SDK, which is synchronous. Every call is
routed through ``asyncio.to_thread`` so this adapter honors the same
``async def`` contract as the rest of the codebase (SQLAlchemy async,
asyncpg) without blocking the event loop -- the alternative, an
async-native S3 client, would mean depending on ``aioboto3``/
``aiobotocore`` for one adapter while every other MinIO-facing tool in
this ecosystem (mc CLI, MinIO's own docs and examples) assumes the
official synchronous SDK; ``asyncio.to_thread`` gets the async contract
this codebase needs without that trade-off.
"""

from __future__ import annotations

import asyncio
import io

from minio import Minio
from minio.error import S3Error

from app.application.interfaces.storage_port import StorageObjectNotFoundError, StoragePort

_NOT_FOUND_CODES = frozenset({"NoSuchKey", "NoSuchObject"})


class MinioStoragePort(StoragePort):
    """One instance per bucket -- see ``StoragePort``'s module docstring
    for why bucket is a constructor argument, never a per-call one."""

    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        secure: bool = False,
    ) -> None:
        self._client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
        self._bucket = bucket

    async def _ensure_bucket(self) -> None:
        exists = await asyncio.to_thread(self._client.bucket_exists, self._bucket)
        if not exists:
            await asyncio.to_thread(self._client.make_bucket, self._bucket)

    async def put_object(
        self, key: str, data: bytes, *, content_type: str = "application/octet-stream"
    ) -> None:
        await self._ensure_bucket()
        await asyncio.to_thread(
            self._client.put_object,
            self._bucket,
            key,
            io.BytesIO(data),
            length=len(data),
            content_type=content_type,
        )

    async def get_object(self, key: str) -> bytes:
        try:
            response = await asyncio.to_thread(self._client.get_object, self._bucket, key)
        except S3Error as exc:
            if exc.code in _NOT_FOUND_CODES:
                raise StorageObjectNotFoundError(key) from exc
            raise
        try:
            return await asyncio.to_thread(response.read)
        finally:
            await asyncio.to_thread(response.close)
            await asyncio.to_thread(response.release_conn)

    async def delete_object(self, key: str) -> None:
        if not await self.object_exists(key):
            raise StorageObjectNotFoundError(key)
        await asyncio.to_thread(self._client.remove_object, self._bucket, key)

    async def object_exists(self, key: str) -> bool:
        try:
            await asyncio.to_thread(self._client.stat_object, self._bucket, key)
            return True
        except S3Error as exc:
            if exc.code in _NOT_FOUND_CODES:
                return False
            raise
