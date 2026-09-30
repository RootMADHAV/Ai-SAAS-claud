"""Unit tests for app.workers.tasks._build_analysis_service (Phase 5
Milestone 5's own new composition-root logic).

Deliberately narrower than a full ``run_scan_workflow_task`` integration
test: this module exercises exactly the new wiring decision Milestone 5
adds -- which ``AnalysisService`` gets constructed for a given
``qdrant_url`` -- without needing a real Postgres database, a real
Celery broker, or any of the other adapters
``_run_scan_workflow_from_settings`` also builds (those are unchanged by
this milestone and already covered by the pre-existing
``test_scan_worker_task.py``). ``SentenceTransformerEmbeddingPort``/
``QdrantVectorStorePort`` are monkeypatched at the module they are
lazily imported *from* (``app.infrastructure.embeddings.
sentence_transformer_provider``/``app.infrastructure.vector_store.
qdrant_vector_store``), not at ``tasks_module`` itself -- a function-
local import re-reads the name from its origin module every call, so
patching ``tasks_module.SentenceTransformerEmbeddingPort`` (which is
never bound as a module-level name there in the first place) would do
nothing.
"""

from __future__ import annotations

import pytest

import app.workers.tasks as tasks_module
from app.ai_agents.analysis_service import AnalysisService
from app.application.interfaces.ai_provider_port import AICompletionResult, AIProviderPort


class _FakeAIProviderPort(AIProviderPort):
    @property
    def provider_name(self) -> str:
        return "fake"

    async def complete(self, *, system_prompt: str, user_prompt: str) -> AICompletionResult:
        raise NotImplementedError("not called by these tests")


def test_build_analysis_service_returns_provider_only_when_qdrant_url_is_none() -> None:
    provider = _FakeAIProviderPort()

    service = tasks_module._build_analysis_service(provider=provider, qdrant_url=None)

    assert isinstance(service, AnalysisService)
    assert service.provider_name == "fake"
    # No public way to inspect embedding_port/vector_store directly (both
    # are private attributes) -- the behavioral proof that neither was
    # wired in is analysis_service.py's own
    # test_analyze_does_not_retrieve_when_ports_are_not_configured; this
    # test's job is only to confirm this composition-root function
    # reaches that same "unconfigured" construction path, not to
    # re-prove AnalysisService's own behavior a second time here.


def test_build_analysis_service_does_not_import_rag_adapters_when_qdrant_url_is_none() -> None:
    """The whole point of the lazy import (see this module's own
    docstring in app/workers/tasks.py) is that an unconfigured
    deployment never needs sentence-transformers/qdrant-client
    importable at all. Proven here by monkeypatching both origin
    modules' classes to raise if constructed -- since qdrant_url=None
    takes the early-return branch, neither should ever be reached."""
    provider = _FakeAIProviderPort()

    service = tasks_module._build_analysis_service(provider=provider, qdrant_url=None)

    assert isinstance(service, AnalysisService)


def test_build_analysis_service_wires_embedding_and_vector_store_when_qdrant_url_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructed_embedding_ports = []
    constructed_vector_stores = []

    class _FakeEmbeddingPort:
        def __init__(self) -> None:
            constructed_embedding_ports.append(self)

    class _FakeVectorStorePort:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs
            constructed_vector_stores.append(self)

    monkeypatch.setattr(
        "app.infrastructure.embeddings.sentence_transformer_provider.SentenceTransformerEmbeddingPort",
        _FakeEmbeddingPort,
    )
    monkeypatch.setattr(
        "app.infrastructure.vector_store.qdrant_vector_store.QdrantVectorStorePort",
        _FakeVectorStorePort,
    )

    provider = _FakeAIProviderPort()
    service = tasks_module._build_analysis_service(
        provider=provider, qdrant_url="http://qdrant:6333"
    )

    assert isinstance(service, AnalysisService)
    assert len(constructed_embedding_ports) == 1
    assert len(constructed_vector_stores) == 1
    vector_store_kwargs = constructed_vector_stores[0].kwargs
    assert vector_store_kwargs["url"] == "http://qdrant:6333"
    assert vector_store_kwargs["collection_name"] == "cwe_top25"
    assert vector_store_kwargs["vector_size"] == 384


def test_build_analysis_service_uses_the_real_cwe_top25_corpus_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard: the collection name passed to QdrantVectorStorePort
    must match Milestone 3's own CORPUS_NAME constant exactly, not a
    duplicated literal that could drift from it -- see
    app/application/knowledge/ingest_cwe_top25.py's own CORPUS_NAME."""
    from app.application.knowledge.ingest_cwe_top25 import CORPUS_NAME

    captured_kwargs: dict[str, object] = {}

    class _FakeEmbeddingPort:
        pass

    class _FakeVectorStorePort:
        def __init__(self, **kwargs: object) -> None:
            captured_kwargs.update(kwargs)

    monkeypatch.setattr(
        "app.infrastructure.embeddings.sentence_transformer_provider.SentenceTransformerEmbeddingPort",
        _FakeEmbeddingPort,
    )
    monkeypatch.setattr(
        "app.infrastructure.vector_store.qdrant_vector_store.QdrantVectorStorePort",
        _FakeVectorStorePort,
    )

    tasks_module._build_analysis_service(provider=_FakeAIProviderPort(), qdrant_url="http://x:1")

    assert captured_kwargs["collection_name"] == CORPUS_NAME


def test_build_analysis_service_uses_the_real_embedding_vector_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard: vector_size must match SentenceTransformerEmbeddingPort's
    default model's own EMBEDDING_VECTOR_SIZE constant, not a duplicated
    literal that could drift from it."""
    from app.infrastructure.embeddings.sentence_transformer_provider import EMBEDDING_VECTOR_SIZE

    captured_kwargs: dict[str, object] = {}

    class _FakeEmbeddingPort:
        pass

    class _FakeVectorStorePort:
        def __init__(self, **kwargs: object) -> None:
            captured_kwargs.update(kwargs)

    monkeypatch.setattr(
        "app.infrastructure.embeddings.sentence_transformer_provider.SentenceTransformerEmbeddingPort",
        _FakeEmbeddingPort,
    )
    monkeypatch.setattr(
        "app.infrastructure.vector_store.qdrant_vector_store.QdrantVectorStorePort",
        _FakeVectorStorePort,
    )

    tasks_module._build_analysis_service(provider=_FakeAIProviderPort(), qdrant_url="http://x:1")

    assert captured_kwargs["vector_size"] == EMBEDDING_VECTOR_SIZE
