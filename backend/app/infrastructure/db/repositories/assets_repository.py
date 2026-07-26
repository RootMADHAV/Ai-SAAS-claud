"""SQLAlchemy implementation of the Asset Intelligence repository port."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.interfaces.assets_repository import AssetRepositoryPort
from app.domain.assets.entities import Asset, AssetObservation, AssetRelationship
from app.domain.shared.enums import AssetStatus, AssetType
from app.infrastructure.db.models.assets import (
    Asset as AssetRow,
)
from app.infrastructure.db.models.assets import (
    AssetObservation as AssetObservationRow,
)
from app.infrastructure.db.models.assets import (
    AssetRelationship as AssetRelationshipRow,
)


def _asset_to_domain(row: AssetRow) -> Asset:
    return Asset(
        id=row.id,
        organization_id=row.organization_id,
        asset_type=AssetType(row.asset_type),
        value=row.value,
        status=AssetStatus(row.status),
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        metadata=row.asset_metadata,
        confidence=row.confidence,
        parent_asset_id=row.parent_asset_id,
        deleted_at=row.deleted_at,
    )


def _observation_to_domain(row: AssetObservationRow) -> AssetObservation:
    return AssetObservation(
        id=row.id,
        organization_id=row.organization_id,
        asset_id=row.asset_id,
        scan_id=row.scan_id,
        confidence=row.confidence,
        observed_at=row.observed_at,
        created_at=row.created_at,
        metadata=row.observation_metadata,
    )


def _relationship_to_domain(row: AssetRelationshipRow) -> AssetRelationship:
    return AssetRelationship(
        id=row.id,
        organization_id=row.organization_id,
        source_asset_id=row.source_asset_id,
        target_asset_id=row.target_asset_id,
        relationship_type=row.relationship_type,
        created_at=row.created_at,
        updated_at=row.updated_at,
        confidence=row.confidence,
    )


class SqlAlchemyAssetRepository(AssetRepositoryPort):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, asset_id: UUID) -> Asset | None:
        # Explicit filter, not session.get(): RLS enforces tenant
        # isolation on this table but deliberately does not filter
        # deleted_at (see the DESIGN NOTE in the initial-schema migration
        # for why that combination is unimplementable in Postgres RLS).
        result = await self._session.execute(
            select(AssetRow).where(AssetRow.id == asset_id, AssetRow.deleted_at.is_(None))
        )
        row = result.scalar_one_or_none()
        return _asset_to_domain(row) if row is not None else None

    async def get_by_identity(
        self, organization_id: UUID, asset_type: AssetType, value: str
    ) -> Asset | None:
        result = await self._session.execute(
            select(AssetRow).where(
                AssetRow.organization_id == organization_id,
                AssetRow.asset_type == asset_type,
                AssetRow.value == value,
                AssetRow.deleted_at.is_(None),
            )
        )
        row = result.scalar_one_or_none()
        return _asset_to_domain(row) if row is not None else None

    async def add(self, asset: Asset) -> None:
        self._session.add(
            AssetRow(
                id=asset.id,
                organization_id=asset.organization_id,
                asset_type=asset.asset_type,
                value=asset.value,
                status=asset.status,
                asset_metadata=asset.metadata,
                confidence=asset.confidence,
                parent_asset_id=asset.parent_asset_id,
                first_seen_at=asset.first_seen_at,
                last_seen_at=asset.last_seen_at,
                created_at=asset.created_at,
                updated_at=asset.updated_at,
                deleted_at=asset.deleted_at,
            )
        )
        await self._session.flush()

    async def update(self, asset: Asset) -> None:
        row = await self._session.get(AssetRow, asset.id)
        if row is None:
            raise LookupError(f"Asset {asset.id} does not exist")
        row.status = asset.status
        row.asset_metadata = asset.metadata
        row.confidence = asset.confidence
        row.parent_asset_id = asset.parent_asset_id
        row.last_seen_at = asset.last_seen_at
        row.deleted_at = asset.deleted_at
        await self._session.flush()

    async def add_observation(self, observation: AssetObservation) -> None:
        self._session.add(
            AssetObservationRow(
                id=observation.id,
                organization_id=observation.organization_id,
                asset_id=observation.asset_id,
                scan_id=observation.scan_id,
                observation_metadata=observation.metadata,
                confidence=observation.confidence,
                observed_at=observation.observed_at,
                created_at=observation.created_at,
            )
        )
        await self._session.flush()

    async def list_observations(self, asset_id: UUID) -> list[AssetObservation]:
        result = await self._session.execute(
            select(AssetObservationRow)
            .where(AssetObservationRow.asset_id == asset_id)
            .order_by(AssetObservationRow.observed_at)
        )
        return [_observation_to_domain(row) for row in result.scalars().all()]

    async def add_relationship(self, relationship: AssetRelationship) -> None:
        self._session.add(
            AssetRelationshipRow(
                id=relationship.id,
                organization_id=relationship.organization_id,
                source_asset_id=relationship.source_asset_id,
                target_asset_id=relationship.target_asset_id,
                relationship_type=relationship.relationship_type,
                confidence=relationship.confidence,
                created_at=relationship.created_at,
                updated_at=relationship.updated_at,
            )
        )
        await self._session.flush()

    async def list_relationships(self, asset_id: UUID) -> list[AssetRelationship]:
        result = await self._session.execute(
            select(AssetRelationshipRow).where(
                or_(
                    AssetRelationshipRow.source_asset_id == asset_id,
                    AssetRelationshipRow.target_asset_id == asset_id,
                )
            )
        )
        return [_relationship_to_domain(row) for row in result.scalars().all()]
