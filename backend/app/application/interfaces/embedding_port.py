"""Embedding port -- a separate port from ``AIProviderPort``, not an
extension of it (Phase 5 Milestone 1 planning decision).

``AIProviderPort``'s own docstring locks its scope deliberately: "this
port's job stops at 'send one prompt, return the provider's raw text
response.'" Turning text into an embedding vector is a different
capability shape entirely (different input semantics, a fixed-size
numeric output instead of free text, no provider in this codebase --
Anthropic -- even exposes it), not a variant of completion. Folding
``embed()`` onto ``AIProviderPort`` would force every future
``AIProviderPort`` implementer to support a capability not all of them
have -- the same reasoning that already split ``ScannerPort`` into
``ActiveScanner``/``ImportScanner`` for genuinely different capability
shapes rather than one interface with an optional method.

Deliberately minimal, mirroring ``AIProviderPort``'s own minimalism:
one method, one job, no batching, no interpretation of the vector it
returns. Retrieval (similarity search once a vector store exists) and
ingestion (what text gets embedded and stored) are both explicitly out
of scope for this port and for this milestone -- see PROJECT_STATE.md's
Phase 5 planning notes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    """One text's embedding vector, plus which model produced it --
    mirrors ``AICompletionResult``'s own shape (payload plus minimal
    typed provenance, no interpretation) from
    ``app/application/interfaces/ai_provider_port.py``."""

    vector: tuple[float, ...]
    model: str


class EmbeddingError(RuntimeError):
    """Raised by a concrete embedding adapter on any failure producing
    an embedding for the given text. Callers depend on this port-level
    type, never an adapter's own underlying library exceptions --
    mirrors ``AIProviderError``'s identical boundary rule."""


class EmbeddingPort(ABC):
    """One model's capability to turn text into a fixed-size embedding
    vector."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Stable identifier for the embedding model in use, e.g.
        ``"all-MiniLM-L6-v2"`` -- mirrors ``AIProviderPort.
        provider_name``, and is recorded alongside any future
        retrieved-context provenance the same way ``AnalysisService``
        already records ``{"provider", "model"}`` in
        ``FindingAnalysis.model_metadata``."""
        ...

    @abstractmethod
    async def embed(self, text: str) -> EmbeddingResult:
        """Return the embedding vector for ``text``.

        Raises ``EmbeddingError`` if the embedding cannot be produced.
        """
        ...
