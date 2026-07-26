"""Repository port for the Asset Intelligence bounded context."""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from app.domain.assets.entities import Asset, AssetObservation, AssetRelationship
from app.domain.shared.enums import AssetType


class AssetRepositoryPort(ABC):
    """Persistence for the ``Asset`` aggregate, including its
    ``AssetObservation`` history and ``AssetRelationship`` edges."""

    @abstractmethod
    async def get_by_id(self, asset_id: UUID) -> Asset | None: ...

    @abstractmethod
    async def get_by_identity(
        self, organization_id: UUID, asset_type: AssetType, value: str
    ) -> Asset | None:
        """Look up by the natural key (PROJECT_STATE.md section 3):
        ``(organization_id, asset_type, value)``. This is what a
        re-observation of the same asset matches against to decide
        "update the cache" vs. "this is a new asset," not ``id``."""
        ...

    @abstractmethod
    async def add(self, asset: Asset) -> None: ...

    @abstractmethod
    async def update(self, asset: Asset) -> None:
        """Persists changes to the materialized ``metadata``/``confidence``
        view and ``last_seen_at`` -- the write-time duplication described
        in PROJECT_STATE.md section 3. Keeping those fields in sync with
        the latest observation is application-layer responsibility; this
        method just persists whatever the caller already computed."""
        ...

    @abstractmethod
    async def add_observation(self, observation: AssetObservation) -> None: ...

    @abstractmethod
    async def list_observations(self, asset_id: UUID) -> list[AssetObservation]: ...

    @abstractmethod
    async def add_relationship(self, relationship: AssetRelationship) -> None: ...

    @abstractmethod
    async def list_relationships(self, asset_id: UUID) -> list[AssetRelationship]:
        """Returns edges where ``asset_id`` is either the source or the
        target -- relationships are conceptually undirected for querying
        purposes even though the schema stores a directed pair."""
        ...
