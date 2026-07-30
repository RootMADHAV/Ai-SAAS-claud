"""Unit tests for app.infrastructure.storage.minio_storage.MinioStoragePort.

No real MinIO instance is exercised here -- ``Minio`` is patched at
construction time so these tests verify ``MinioStoragePort``'s own
logic (bucket-ensure-before-write, key-not-found -> port exception
translation, delete-checks-existence) against a controllable fake. This
is a weaker verification tier than Milestone 2's real-Postgres
integration suite: no real MinIO server was reachable to build an
equivalent integration test against in this environment (no apt package
for a MinIO server exists on the allowed package mirrors, and
dl.min.io -- where the official server binary is distributed -- is
outside this environment's network allowlist). This is flagged here
explicitly, per the verification-honesty rule, rather than implied to be
equivalent to a real-server integration test; a real integration suite
against a locally running MinIO container is future work once such an
environment is available (see docs/implementation_progress.md's
Technical debt list).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from minio.error import S3Error

from app.application.interfaces.storage_port import StorageObjectNotFoundError
from app.infrastructure.storage.minio_storage import MinioStoragePort


def _s3_error(code: str) -> S3Error:
    return S3Error(
        response=MagicMock(),
        code=code,
        message="boom",
        resource="/bucket/key",
        request_id="req-1",
        host_id="host-1",
    )


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    client = MagicMock()
    monkeypatch.setattr("app.infrastructure.storage.minio_storage.Minio", lambda *a, **kw: client)
    return client


def _make_port(**overrides: Any) -> MinioStoragePort:
    return MinioStoragePort(
        endpoint="localhost:9000",
        access_key="test",
        secret_key="test",
        bucket="raw-scan-output",
        **overrides,
    )


async def test_put_object_creates_bucket_when_missing(fake_client: MagicMock) -> None:
    fake_client.bucket_exists.return_value = False
    port = _make_port()

    await port.put_object("scan-1/output.jsonl", b"payload", content_type="application/x-ndjson")

    fake_client.make_bucket.assert_called_once_with("raw-scan-output")
    put_args = fake_client.put_object.call_args
    assert put_args.args[0] == "raw-scan-output"
    assert put_args.args[1] == "scan-1/output.jsonl"
    assert put_args.kwargs["length"] == len(b"payload")
    assert put_args.kwargs["content_type"] == "application/x-ndjson"


async def test_put_object_skips_bucket_creation_when_it_already_exists(
    fake_client: MagicMock,
) -> None:
    fake_client.bucket_exists.return_value = True
    port = _make_port()

    await port.put_object("scan-1/output.jsonl", b"payload")

    fake_client.make_bucket.assert_not_called()


async def test_get_object_returns_the_response_body(fake_client: MagicMock) -> None:
    response = MagicMock()
    response.read.return_value = b"payload"
    fake_client.get_object.return_value = response
    port = _make_port()

    data = await port.get_object("scan-1/output.jsonl")

    assert data == b"payload"
    response.close.assert_called_once()
    response.release_conn.assert_called_once()


async def test_get_object_raises_storage_not_found_for_missing_key(
    fake_client: MagicMock,
) -> None:
    fake_client.get_object.side_effect = _s3_error("NoSuchKey")
    port = _make_port()

    with pytest.raises(StorageObjectNotFoundError) as exc_info:
        await port.get_object("does-not-exist")

    assert exc_info.value.key == "does-not-exist"


async def test_get_object_reraises_unrelated_s3_errors(fake_client: MagicMock) -> None:
    fake_client.get_object.side_effect = _s3_error("AccessDenied")
    port = _make_port()

    with pytest.raises(S3Error):
        await port.get_object("some-key")


async def test_object_exists_true_and_false(fake_client: MagicMock) -> None:
    port = _make_port()

    fake_client.stat_object.return_value = MagicMock()
    assert await port.object_exists("present") is True

    fake_client.stat_object.side_effect = _s3_error("NoSuchKey")
    assert await port.object_exists("missing") is False


async def test_object_exists_reraises_unrelated_s3_errors(fake_client: MagicMock) -> None:
    fake_client.stat_object.side_effect = _s3_error("AccessDenied")
    port = _make_port()

    with pytest.raises(S3Error):
        await port.object_exists("some-key")


async def test_delete_object_removes_when_present(fake_client: MagicMock) -> None:
    fake_client.stat_object.return_value = MagicMock()
    port = _make_port()

    await port.delete_object("present")

    fake_client.remove_object.assert_called_once_with("raw-scan-output", "present")


async def test_delete_object_raises_storage_not_found_when_absent(
    fake_client: MagicMock,
) -> None:
    fake_client.stat_object.side_effect = _s3_error("NoSuchKey")
    port = _make_port()

    with pytest.raises(StorageObjectNotFoundError):
        await port.delete_object("missing")

    fake_client.remove_object.assert_not_called()
