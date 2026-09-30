"""Qdrant-backed adapters implementing
``app.application.interfaces.vector_store_port.VectorStorePort``.

- ``qdrant_vector_store.py`` -- ``QdrantVectorStorePort``, using the
  official ``qdrant-client`` library's native async client. Implemented
  in Phase 5 Milestone 2 (collection init, upsert, similarity search
  only -- no delete/update, no ingestion, no retrieval wiring yet).
"""
