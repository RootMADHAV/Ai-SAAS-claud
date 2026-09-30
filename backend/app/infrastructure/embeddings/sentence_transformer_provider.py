"""Local ``sentence-transformers`` embedding adapter -- the first
concrete ``EmbeddingPort`` implementation (see that port's own docstring
in ``app/application/interfaces/embedding_port.py`` for why embeddings
are a separate port from ``AIProviderPort``).

CPU-only by design -- a locked decision for this local/self-use
deployment (Phase 5 planning), not a limitation worked around: no
CUDA/GPU handling is attempted, and the model is constructed with
``device="cpu"`` explicitly rather than left to auto-detect, so behavior
does not silently change on a host that happens to have a GPU.

Wraps the official ``sentence-transformers`` library, which is
synchronous and CPU-bound. Every call is routed through
``asyncio.to_thread``, mirroring ``MinioStoragePort``'s own precedent
for wrapping a synchronous SDK without blocking the event loop (see that
module's docstring in ``app/infrastructure/storage/minio_storage.py``).

The model is loaded once, at construction time, not lazily on first
call -- loading ``all-MiniLM-L6-v2`` involves real disk I/O and CPU work
that belongs at process startup (where a slow load is expected and
monitored), not silently on whichever request happens to be first.
Mirrors ``AnthropicProvider``'s "which model is a constructor argument,
never a per-call one" precedent -- which model to load is deployment
wiring, not something a caller of ``embed()`` should decide per request.

This adapter belongs to the ``ingestion_worker`` process (Phase 5
planning decision) -- the same process ``AnthropicProvider``/
``AnalysisService`` already belong to (see ``app/config.py``'s
``check_role_boundaries``). Phase 5 Milestone 5 wires it into
``app/workers/tasks.py``'s ``_build_analysis_service``, imported
lazily there (not at that module's own top) so the API process's image
never needs ``sentence-transformers`` importable at all -- see that
function's own docstring for why.
"""

from __future__ import annotations

import asyncio
from typing import Any

from sentence_transformers import SentenceTransformer

from app.application.interfaces.embedding_port import (
    EmbeddingError,
    EmbeddingPort,
    EmbeddingResult,
)

DEFAULT_MODEL_NAME = "all-MiniLM-L6-v2"

# The fixed output dimensionality of DEFAULT_MODEL_NAME specifically --
# not a general property of every model this adapter could theoretically
# load, but "do NOT change the embedding model" (Phase 5 Milestone 5) is
# a locked decision, so this constant is exactly as valid as
# DEFAULT_MODEL_NAME itself for as long as that decision holds.
# Re-exported for the one caller that genuinely needs it:
# app/workers/tasks.py's composition root, which must construct a
# QdrantVectorStorePort dimensioned to match whichever embedding model
# actually produced the vectors already stored under Phase 5 Milestone
# 3's CORPUS_NAME collection -- this is that fact's single source of
# truth, not a magic number redeclared at the call site.
EMBEDDING_VECTOR_SIZE = 384


class SentenceTransformerEmbeddingPort(EmbeddingPort):
    """One instance per loaded model -- mirrors ``AnthropicProvider``'s
    own "which model" being a constructor argument, not a per-call
    one."""

    def __init__(self, *, model_name: str = DEFAULT_MODEL_NAME) -> None:
        self._model_name = model_name
        self._model = SentenceTransformer(model_name, device="cpu")

    @property
    def model_name(self) -> str:
        return self._model_name

    async def embed(self, text: str) -> EmbeddingResult:
        try:
            raw_vector = await asyncio.to_thread(self._encode_sync, text)
        except Exception as exc:
            # sentence-transformers/torch document no single shared
            # exception base the way anthropic.APIError or minio's
            # S3Error do -- a broad catch here is the honest boundary
            # translation this port's own docstring commits to
            # ("callers depend on this port-level type, never an
            # adapter's own underlying library exceptions"), not a
            # lapse.
            raise EmbeddingError(f"failed to embed text: {exc}") from exc
        return EmbeddingResult(vector=tuple(float(x) for x in raw_vector), model=self._model_name)

    def _encode_sync(self, text: str) -> Any:
        """The synchronous, CPU-bound call into the model -- wrapped by
        ``asyncio.to_thread`` in ``embed()`` above. Deliberately typed
        ``Any`` rather than threading ``SentenceTransformer.encode``'s
        own overloaded signature through ``asyncio.to_thread``'s
        generic ``Callable[..., T]`` parameter, which is more overload
        resolution than this adapter needs to expose. The actual
        runtime value (a 1-D ``numpy.ndarray`` for a single string with
        this call's defaults) is narrowed explicitly and safely by the
        ``float(x) for x in raw_vector`` conversion in ``embed()``, not
        assumed from this method's declared return type.
        """
        return self._model.encode(text)
