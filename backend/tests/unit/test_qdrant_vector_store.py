"""Unit tests for
app.infrastructure.vector_store.qdrant_vector_store.QdrantVectorStorePort.

Mirrors test_anthropic_provider.py's own pattern for an SDK that already
ships a native async client: ``AsyncQdrantClient`` is monkeypatched at
construction time with a fake exposing the same async method names,
rather than requiring a running Qdrant container -- no real Qdrant
instance is exercised anywhere in this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from qdrant_client import models

from app.application.interfaces.vector_store_port import VectorPoint, VectorStoreError
from app.infrastructure.vector_store.qdrant_vector_store import QdrantVectorStorePort


@dataclass
class _FakeQdrantClient:
    exists: bool = False
    query_points_result: object | None = None
    collection_exists_error: Exception | None = None
    create_collection_error: Exception | None = None
    upsert_error: Exception | None = None
    query_points_error: Exception | None = None
    collection_exists_calls: list[str] = field(default_factory=list)
    create_collection_calls: list[dict[str, object]] = field(default_factory=list)
    upsert_calls: list[dict[str, object]] = field(default_factory=list)
    query_points_calls: list[dict[str, object]] = field(default_factory=list)

    async def collection_exists(self, collection_name: str) -> bool:
        self.collection_exists_calls.append(collection_name)
        if self.collection_exists_error is not None:
            raise self.collection_exists_error
        return self.exists

    async def create_collection(self, collection_name: str, **kwargs: object) -> bool:
        self.create_collection_calls.append({"collection_name": collection_name, **kwargs})
        if self.create_collection_error is not None:
            raise self.create_collection_error
        return True

    async def upsert(self, collection_name: str, **kwargs: object) -> object:
        self.upsert_calls.append({"collection_name": collection_name, **kwargs})
        if self.upsert_error is not None:
            raise self.upsert_error
        return SimpleNamespace(status="completed")

    async def query_points(self, collection_name: str, **kwargs: object) -> object:
        self.query_points_calls.append({"collection_name": collection_name, **kwargs})
        if self.query_points_error is not None:
            raise self.query_points_error
        assert self.query_points_result is not None
        return self.query_points_result


def _install_fake_client(monkeypatch: pytest.MonkeyPatch, fake: _FakeQdrantClient) -> None:
    monkeypatch.setattr(
        "app.infrastructure.vector_store.qdrant_vector_store.AsyncQdrantClient",
        lambda **kwargs: fake,
    )


def _make_port(**overrides: object) -> QdrantVectorStorePort:
    defaults: dict[str, object] = {
        "url": "http://qdrant:6333",
        "collection_name": "cwe_top_25",
        "vector_size": 384,
    }
    defaults.update(overrides)
    return QdrantVectorStorePort(**defaults)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_ensure_collection_creates_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeQdrantClient(exists=False)
    _install_fake_client(monkeypatch, fake)
    port = _make_port()

    await port.ensure_collection()

    assert fake.collection_exists_calls == ["cwe_top_25"]
    assert len(fake.create_collection_calls) == 1
    call = fake.create_collection_calls[0]
    assert call["collection_name"] == "cwe_top_25"
    vectors_config = call["vectors_config"]
    assert isinstance(vectors_config, models.VectorParams)
    assert vectors_config.size == 384
    assert vectors_config.distance == models.Distance.COSINE


@pytest.mark.asyncio
async def test_ensure_collection_skips_creation_when_it_already_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeQdrantClient(exists=True)
    _install_fake_client(monkeypatch, fake)
    port = _make_port()

    await port.ensure_collection()

    assert fake.create_collection_calls == []


@pytest.mark.asyncio
async def test_ensure_collection_raises_vector_store_error_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeQdrantClient(collection_exists_error=RuntimeError("connection refused"))
    _install_fake_client(monkeypatch, fake)
    port = _make_port()

    with pytest.raises(VectorStoreError, match="connection refused"):
        await port.ensure_collection()


@pytest.mark.asyncio
async def test_upsert_ensures_collection_then_writes_points(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeQdrantClient(exists=True)
    _install_fake_client(monkeypatch, fake)
    port = _make_port()
    point = VectorPoint(
        id="123e4567-e89b-12d3-a456-426614174000",
        vector=(0.1, 0.2, 0.3),
        payload={"cwe_id": "CWE-79", "text": "Improper Neutralization of Input"},
    )

    await port.upsert([point])

    assert fake.collection_exists_calls == ["cwe_top_25"]
    assert len(fake.upsert_calls) == 1
    call = fake.upsert_calls[0]
    assert call["collection_name"] == "cwe_top_25"
    points = call["points"]
    assert len(points) == 1
    written = points[0]
    assert isinstance(written, models.PointStruct)
    assert written.id == "123e4567-e89b-12d3-a456-426614174000"
    assert written.vector == [0.1, 0.2, 0.3]
    assert written.payload == {"cwe_id": "CWE-79", "text": "Improper Neutralization of Input"}


@pytest.mark.asyncio
async def test_upsert_raises_vector_store_error_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeQdrantClient(exists=True, upsert_error=RuntimeError("write failed"))
    _install_fake_client(monkeypatch, fake)
    port = _make_port()

    with pytest.raises(VectorStoreError, match="write failed"):
        await port.upsert([VectorPoint(id="1", vector=(0.1,), payload={})])


@pytest.mark.asyncio
async def test_search_returns_matches_with_string_ids_and_default_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    point_id = uuid4()
    fake_response = SimpleNamespace(
        points=[
            SimpleNamespace(id=point_id, score=0.91, payload={"cwe_id": "CWE-79"}),
            SimpleNamespace(id=5, score=0.42, payload=None),
        ]
    )
    fake = _FakeQdrantClient(query_points_result=fake_response)
    _install_fake_client(monkeypatch, fake)
    port = _make_port()

    matches = await port.search([0.1, 0.2, 0.3])

    assert len(matches) == 2
    assert matches[0].id == str(point_id)
    assert isinstance(matches[0].id, str)
    assert UUID(matches[0].id) == point_id
    assert matches[0].score == 0.91
    assert matches[0].payload == {"cwe_id": "CWE-79"}
    assert matches[1].id == "5"
    assert matches[1].payload == {}


@pytest.mark.asyncio
async def test_search_passes_query_vector_and_limit_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeQdrantClient(query_points_result=SimpleNamespace(points=[]))
    _install_fake_client(monkeypatch, fake)
    port = _make_port()

    await port.search([0.1, 0.2, 0.3])
    await port.search([0.4, 0.5, 0.6], limit=10)

    assert len(fake.query_points_calls) == 2
    assert fake.query_points_calls[0]["query"] == [0.1, 0.2, 0.3]
    assert fake.query_points_calls[0]["limit"] == 5
    assert fake.query_points_calls[1]["query"] == [0.4, 0.5, 0.6]
    assert fake.query_points_calls[1]["limit"] == 10


@pytest.mark.asyncio
async def test_search_does_not_ensure_collection(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeQdrantClient(query_points_result=SimpleNamespace(points=[]))
    _install_fake_client(monkeypatch, fake)
    port = _make_port()

    await port.search([0.1, 0.2, 0.3])

    assert fake.collection_exists_calls == []
    assert fake.create_collection_calls == []


@pytest.mark.asyncio
async def test_search_raises_vector_store_error_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeQdrantClient(query_points_error=RuntimeError("search timed out"))
    _install_fake_client(monkeypatch, fake)
    port = _make_port()

    with pytest.raises(VectorStoreError, match="search timed out"):
        await port.search([0.1, 0.2, 0.3])
