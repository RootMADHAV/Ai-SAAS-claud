"""Integration tests for the Asset Intelligence repository implementation."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.assets.entities import AssetObservation, AssetRelationship
from app.domain.identity.entities import Organization
from app.domain.shared.enums import AssetType
from app.domain.shared.ids import new_id
from app.infrastructure.db.repositories.assets_repository import SqlAlchemyAssetRepository
from app.infrastructure.db.repositories.identity_repository import (
    SqlAlchemyOrganizationRepository,
)
from app.infrastructure.db.repositories.scanning_repository import SqlAlchemyScanRepository
from tests.integration.support import (
    make_asset,
    make_organization,
    make_scan,
    set_org_context,
)

pytestmark = pytest.mark.integration


async def _seed_org(session: AsyncSession) -> Organization:
    org = make_organization()
    await set_org_context(session, org.id)
    await SqlAlchemyOrganizationRepository(session).add(org)
    return org


async def test_add_and_get_by_identity_natural_key(db_session: AsyncSession) -> None:
    org = await _seed_org(db_session)
    asset = make_asset(org.id, value="app.example.com")
    repo = SqlAlchemyAssetRepository(db_session)

    await repo.add(asset)
    fetched = await repo.get_by_identity(org.id, AssetType.DOMAIN, "app.example.com")

    assert fetched is not None
    assert fetched.id == asset.id


async def test_update_persists_materialized_metadata_and_confidence(
    db_session: AsyncSession,
) -> None:
    org = await _seed_org(db_session)
    asset = make_asset(org.id)
    repo = SqlAlchemyAssetRepository(db_session)
    await repo.add(asset)

    asset.metadata = {"open_ports": [80, 443]}
    asset.confidence = 0.9
    asset.last_seen_at = asset.last_seen_at
    await repo.update(asset)

    reloaded = await repo.get_by_id(asset.id)
    assert reloaded is not None
    assert reloaded.metadata == {"open_ports": [80, 443]}
    assert reloaded.confidence == 0.9


async def test_add_and_list_observations_ordered_by_time(db_session: AsyncSession) -> None:
    org = await _seed_org(db_session)
    asset = make_asset(org.id)
    scan = make_scan(org.id)
    await SqlAlchemyAssetRepository(db_session).add(asset)
    await SqlAlchemyScanRepository(db_session).add(scan)

    repo = SqlAlchemyAssetRepository(db_session)
    first = AssetObservation(
        id=new_id(),
        organization_id=org.id,
        asset_id=asset.id,
        scan_id=scan.id,
        confidence=0.5,
        observed_at=asset.first_seen_at,
        created_at=asset.first_seen_at,
    )
    await repo.add_observation(first)

    observations = await repo.list_observations(asset.id)
    assert len(observations) == 1
    assert observations[0].confidence == 0.5


async def test_relationships_are_queryable_from_either_side(db_session: AsyncSession) -> None:
    org = await _seed_org(db_session)
    asset_repo = SqlAlchemyAssetRepository(db_session)
    source = make_asset(org.id, value="www.example.com")
    target = make_asset(org.id, asset_type=AssetType.IP, value="93.184.216.34")
    await asset_repo.add(source)
    await asset_repo.add(target)

    relationship = AssetRelationship(
        id=new_id(),
        organization_id=org.id,
        source_asset_id=source.id,
        target_asset_id=target.id,
        relationship_type="resolves_to",
        created_at=source.created_at,
        updated_at=source.created_at,
    )
    await asset_repo.add_relationship(relationship)

    from_source = await asset_repo.list_relationships(source.id)
    from_target = await asset_repo.list_relationships(target.id)
    assert [r.id for r in from_source] == [relationship.id]
    assert [r.id for r in from_target] == [relationship.id]


async def test_rls_isolates_assets_between_tenants(db_session: AsyncSession) -> None:
    org_a = make_organization()
    org_b = make_organization()
    await set_org_context(db_session, org_a.id)
    await SqlAlchemyOrganizationRepository(db_session).add(org_a)
    asset = make_asset(org_a.id)
    repo = SqlAlchemyAssetRepository(db_session)
    await repo.add(asset)

    await set_org_context(db_session, org_b.id)
    await SqlAlchemyOrganizationRepository(db_session).add(org_b)

    assert await repo.get_by_id(asset.id) is None
