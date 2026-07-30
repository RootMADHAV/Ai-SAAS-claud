"""Object storage port -- the abstraction MinIO sits behind
(PROJECT_STATE.md section 2: "MinIO behind a StoragePort abstraction").

One ``StoragePort`` instance is bound to exactly one bucket at
construction time, mirroring how a repository is bound to one aggregate:
the port's methods only ever take an object key, never a bucket name, so
a caller cannot accidentally cross buckets (e.g. raw scan output vs. a
future generated-report bucket) by passing the wrong string at a call
site. Which bucket a given ``StoragePort`` instance points at is wiring
(dependency-injection setup), not a per-call decision.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class StorageObjectNotFoundError(LookupError):
    """Raised by ``get_object``/``delete_object`` when ``key`` does not
    exist in the bound bucket. A ``LookupError`` subclass to match this
    codebase's existing convention of raising ``LookupError`` for
    "the thing you asked for by identity does not exist" (see the
    repository ``get_by_id`` methods from Milestone 2)."""

    def __init__(self, key: str) -> None:
        super().__init__(f"no object found for key: {key!r}")
        self.key = key


class StoragePort(ABC):
    """Persistence for opaque binary objects, keyed by string -- raw
    scanner output today (Milestone 3); generated report artifacts are
    the anticipated Reporting-context consumer later, per
    PROJECT_STATE.md section 1's Reporting bounded context."""

    @abstractmethod
    async def put_object(
        self, key: str, data: bytes, *, content_type: str = "application/octet-stream"
    ) -> None:
        """Write ``data`` under ``key``, overwriting any existing object
        at that key."""
        ...

    @abstractmethod
    async def get_object(self, key: str) -> bytes:
        """Return the bytes stored under ``key``.

        Raises ``StorageObjectNotFoundError`` if no object exists at
        ``key``.
        """
        ...

    @abstractmethod
    async def delete_object(self, key: str) -> None:
        """Delete the object at ``key``.

        Raises ``StorageObjectNotFoundError`` if no object exists at
        ``key`` -- deleting a key that was never written is a caller
        error, not a silent no-op, so it is surfaced rather than
        swallowed.
        """
        ...

    @abstractmethod
    async def object_exists(self, key: str) -> bool:
        """Return whether an object exists at ``key``, without raising."""
        ...
