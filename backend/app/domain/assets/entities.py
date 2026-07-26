"""Domain entities for the Asset Intelligence bounded context.

See app/domain/identity/entities.py's module docstring for why these
exist now rather than being deferred further -- the same reasoning
applies here: repository ports need a domain type, not a SQLAlchemy
model, to be typed against.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.shared.enums import AssetStatus, AssetType


@dataclass(slots=True)
class Asset:
    """Current-state cache; identity is ``(organization_id, asset_type,
    value)``, per PROJECT_STATE.md section 3 -- callers that want to find
    an existing asset to update rather than create a duplicate look it up
    by that triple, not by ``id``."""

    id: UUID
    organization_id: UUID
    asset_type: AssetType
    value: str
    status: AssetStatus
    first_seen_at: datetime
    last_seen_at: datetime
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, object] | None = None
    confidence: float | None = None
    parent_asset_id: UUID | None = None
    deleted_at: datetime | None = None

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


@dataclass(slots=True)
class AssetObservation:
    """Append-only per-scan sighting. Immutable once created -- there is
    no ``updated_at``/``deleted_at`` to mirror, matching the persistence
    model's exclusion from both mixins (PROJECT_STATE.md section 3)."""

    id: UUID
    organization_id: UUID
    asset_id: UUID
    scan_id: UUID
    confidence: float
    observed_at: datetime
    created_at: datetime
    metadata: dict[str, object] | None = None


@dataclass(slots=True)
class AssetRelationship:
    """A typed edge between two assets -- the many-to-many half of
    relationship modeling described in PROJECT_STATE.md section 3."""

    id: UUID
    organization_id: UUID
    source_asset_id: UUID
    target_asset_id: UUID
    relationship_type: str
    created_at: datetime
    updated_at: datetime
    confidence: float | None = None
