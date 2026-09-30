"""Vector store port -- the abstraction Qdrant sits behind (Phase 5
Milestone 2; PROJECT_STATE.md sections 2/3: "Qdrant ... running from
day one ... unused until Phase 5 RAG").

Mirrors ``StoragePort``'s own binding rule: one ``VectorStorePort``
instance is bound to exactly one collection at construction time, the
same way one ``StoragePort`` instance is bound to exactly one bucket --
the port's methods never take a collection name, so a caller cannot
accidentally cross collections. Which collection a given
``VectorStorePort`` instance points at, and that collection's vector
size/distance metric, are wiring, not a per-call decision.

Deliberately minimal, scoped to exactly the three operations the
planned Phase 5 flow needs: initializing the collection (Milestone 3's
CWE ingestion needs this once, up front), writing vectors (ingestion),
and similarity search (Milestone 4's retrieval). No delete, no update,
no filtering, no pagination, no collection introspection beyond
existence -- none of that is required by any planned Milestone 3/4
flow, and this port does not anticipate needs beyond them.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class VectorPoint:
    """One vector to write, plus whatever metadata its later retrieval
    needs to show/use -- e.g. a CWE entry's id and source text.
    Milestone 3 decides exactly which keys ``payload`` holds; this port
    only requires it be JSON-compatible, mirroring Qdrant's own native
    payload concept, not a schema this port invents.

    ``id`` must be a UUID-formatted string or an unsigned integer given
    as a string -- a Qdrant server-side constraint, not one this port
    adds. A natural identifier that is not already UUID-shaped (e.g.
    "CWE-79") needs a deterministic UUID derived from it (``uuid.
    uuid5``, for example) before being passed here; this port does not
    perform that derivation itself.
    """

    id: str
    vector: tuple[float, ...]
    payload: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class VectorMatch:
    """One similarity-search result -- mirrors ``VectorPoint``'s own
    shape (id, payload) plus the similarity ``score`` the search itself
    produces. The raw vector is not included; no planned Milestone 4
    retrieval flow needs it back, only the payload text and score."""

    id: str
    score: float
    payload: Mapping[str, object]


class VectorStoreError(RuntimeError):
    """Raised by a concrete vector-store adapter on any failure to
    initialize the collection, write, or search. Callers depend on
    this port-level type, never an adapter's own underlying client
    exceptions -- mirrors ``StorageObjectNotFoundError``'s/
    ``EmbeddingError``'s identical boundary rule."""


class VectorStorePort(ABC):
    """One collection's capability to be initialized, written to, and
    searched over."""

    @abstractmethod
    async def ensure_collection(self) -> None:
        """Create the bound collection if it does not already exist.

        Idempotent -- safe to call every time, mirrors
        ``MinioStoragePort``'s own ``_ensure_bucket`` precedent. Does
        not validate or repair an existing collection's configuration;
        it only checks existence.

        Raises ``VectorStoreError`` if the collection cannot be
        created.
        """
        ...

    @abstractmethod
    async def upsert(self, points: Sequence[VectorPoint]) -> None:
        """Write ``points``, creating the bound collection first if it
        does not already exist (see ``ensure_collection``). Insert-or-
        replace by id, matching Qdrant's own native upsert semantics --
        not a refresh/versioning mechanism this port adds.

        Raises ``VectorStoreError`` if the write fails.
        """
        ...

    @abstractmethod
    async def search(self, query_vector: Sequence[float], *, limit: int = 5) -> list[VectorMatch]:
        """Return the ``limit`` closest points to ``query_vector``,
        ordered nearest-first.

        Does not create the collection if it does not exist -- creating
        one as a side effect of a read would be surprising; call
        ``ensure_collection``/``upsert`` first.

        Raises ``VectorStoreError`` if the search fails.
        """
        ...
