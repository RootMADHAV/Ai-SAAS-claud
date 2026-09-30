"""Qdrant-backed ``VectorStorePort`` implementation.

Uses the official ``qdrant-client`` library's native async client
(``AsyncQdrantClient``), not a synchronous client wrapped in
``asyncio.to_thread`` -- unlike ``MinioStoragePort``/
``SentenceTransformerEmbeddingPort``, the official SDK here already
ships a real async client, so there is no synchronous call to wrap.
Mirrors ``AnthropicProvider``'s own precedent: use the SDK's native
async client directly when one exists; wrap a synchronous one in
``asyncio.to_thread`` only when that's all the SDK offers.

Phase 5 Milestone 5 wires this into ``app/workers/tasks.py``'s
``_build_analysis_service``, imported lazily there (not at that
module's own top) so the API process's image never needs
``qdrant-client`` importable at all -- see that function's own
docstring for why (mirrors ``SentenceTransformerEmbeddingPort``'s
identical Milestone 5 note).
"""

from __future__ import annotations

from collections.abc import Sequence

from qdrant_client import AsyncQdrantClient, models

from app.application.interfaces.vector_store_port import (
    VectorMatch,
    VectorPoint,
    VectorStoreError,
    VectorStorePort,
)


class QdrantVectorStorePort(VectorStorePort):
    """One instance per collection -- mirrors ``MinioStoragePort``'s own
    "which bucket" being a constructor argument, not a per-call one.
    ``vector_size``/``distance`` are likewise bound here, not per-call,
    since a Qdrant collection has exactly one fixed vector shape for
    its whole lifetime."""

    def __init__(
        self,
        *,
        url: str,
        collection_name: str,
        vector_size: int,
        distance: models.Distance = models.Distance.COSINE,
    ) -> None:
        self._client = AsyncQdrantClient(url=url)
        self._collection_name = collection_name
        self._vector_size = vector_size
        self._distance = distance

    async def ensure_collection(self) -> None:
        try:
            exists = await self._client.collection_exists(self._collection_name)
            if not exists:
                await self._client.create_collection(
                    self._collection_name,
                    vectors_config=models.VectorParams(
                        size=self._vector_size, distance=self._distance
                    ),
                )
        except Exception as exc:
            # qdrant-client documents no single shared exception base
            # covering both its REST and gRPC transports -- the same
            # honest boundary-translation reasoning already applied in
            # SentenceTransformerEmbeddingPort.embed().
            raise VectorStoreError(
                f"failed to ensure collection {self._collection_name!r}: {exc}"
            ) from exc

    async def upsert(self, points: Sequence[VectorPoint]) -> None:
        await self.ensure_collection()
        try:
            await self._client.upsert(
                self._collection_name,
                points=[
                    models.PointStruct(
                        id=point.id, vector=list(point.vector), payload=dict(point.payload)
                    )
                    for point in points
                ],
            )
        except Exception as exc:
            raise VectorStoreError(
                f"failed to upsert {len(points)} point(s) into {self._collection_name!r}: {exc}"
            ) from exc

    async def search(self, query_vector: Sequence[float], *, limit: int = 5) -> list[VectorMatch]:
        try:
            response = await self._client.query_points(
                self._collection_name, query=list(query_vector), limit=limit
            )
        except Exception as exc:
            raise VectorStoreError(f"failed to search {self._collection_name!r}: {exc}") from exc
        return [
            VectorMatch(
                id=str(scored_point.id),
                score=scored_point.score,
                payload=scored_point.payload or {},
            )
            for scored_point in response.points
        ]
